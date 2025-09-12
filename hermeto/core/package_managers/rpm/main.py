import asyncio
import hashlib
import itertools
import logging
import shlex
import ssl
from configparser import ConfigParser
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any, Optional, Union, no_type_check

import yaml
from packageurl import PackageURL
from pydantic import ValidationError

from hermeto import APP_NAME
from hermeto.core.config import get_config
from hermeto.core.errors import PackageManagerError, PackageRejected
from hermeto.core.models.input import ExtraOptions, Request, RpmBinaryFilters, SSLOptions
from hermeto.core.models.output import RequestOutput
from hermeto.core.models.sbom import Component, Property
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.package_managers.rpm.binary_filters import RPMArchitectureFilter
from hermeto.core.package_managers.rpm.redhat import RedhatRpmsLock
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import run_cmd

log = logging.getLogger(__name__)


DEFAULT_LOCKFILE_NAME = "rpms.lock.yaml"
DEFAULT_PACKAGE_DIR = "deps/rpm"

# during the computing of file checksum read chunk of size 1 MB
READ_CHUNK = 1048576


@dataclass
class Package:
    """An RPM package with relevant data for the SBOM generation."""

    name: str
    version: str
    release: str
    arch: str
    download_url: str
    epoch: Optional[str] = None
    vendor: Optional[str] = None
    checksum: Optional[str] = None
    repository_id: Optional[str] = None
    summary: Optional[str] = None
    modularity_label: Optional[str] = None

    @classmethod
    def from_filepath(cls, rpm_filepath: Path, rpm_download_metadata: dict[str, Any]) -> "Package":
        """Instantiate a package dataclass instance from a download RPM file path."""
        kwargs: dict[str, Optional[str]] = {}
        kwargs.update(cls._query_rpm_fields(rpm_filepath))

        repoid = rpm_download_metadata.get("repoid")
        is_srpm = rpm_filepath.name.endswith("src.rpm")

        if is_srpm:
            # Red Hat PURL RPM guideline suggests injecting 'src' into the arch qualifier for SRPMS
            kwargs["arch"] = "src"

        kwargs["repository_id"] = (
            repoid if repoid and not repoid.startswith(f"{APP_NAME}") else None
        )
        kwargs["download_url"] = rpm_download_metadata["url"]
        kwargs["checksum"] = rpm_download_metadata.get("checksum")

        # Disable mypy here:
        # - the required fields here correspond with mandatory RPM tags, so they won't be None
        # - download_url isn't an RPM tag, but it's non-null value is guarded by a pydantic model
        package = cls(**kwargs)  # type: ignore
        log.debug("RPM package attributes for '%s': %s", rpm_filepath, package)
        return package

    @staticmethod
    def _query_rpm_fields(file_path: Path) -> dict[str, str]:
        """Query a set of RPM tags.

        Tags which are optional and not set won't be returned in the resulting dict.
        """
        ret = {}
        query_format = (
            # all nvra macros should be present/mandatory in RPM
            "name=%{NAME}\n"
            "version=%{VERSION}\n"
            "release=%{RELEASE}\n"
            "arch=%{ARCH}\n"
            "summary=%{SUMMARY}\n"
            # optional tags; return "" if not set instead of "(None)"
            "vendor=%|VENDOR?{%{VENDOR}}:{}|\n"
            "epoch=%|EPOCH?{%{EPOCH}}:{}|\n"
            "modularity_label=%|MODULARITYLABEL?{%{MODULARITYLABEL}}:{}|\n"
        )
        rpm_args = [
            "-q",
            "--queryformat",
            query_format.strip(),
            str(file_path),
        ]
        rpm_fields = run_cmd(cmd=["rpm", *rpm_args], params={})
        for entry in rpm_fields.split("\n"):
            key, value = entry.partition("=")[::2]
            if not value:
                continue
            ret[key] = value

        return ret

    @property
    def purl(self) -> str:
        """Get the purl for this package."""
        # TODO: get rid of these mappings the moment the upstream PURL spec provides clear
        # guidelines where does the namespace value come from, i.e. not the VENDOR RPM header tag
        vendor_namespace_mapping = {
            "Red Hat": "redhat",  # common Vendor string 'Red Hat Inc.'
            "Fedora": "fedora",  # common Vendor string: Fedora Project, Fedora Copr - group @XYZ
            "SUSE": "suse",  # common Vendor string: SUSE LLC <https://www.suse.com/>
        }

        qualifier_fields = [
            ("epoch", self.epoch),
            ("arch", self.arch),
            ("repository_id", self.repository_id),
            ("checksum", self.checksum),
            ("download_url", None if self.repository_id else self.download_url),
        ]
        qualifiers: dict[str, str] = {k: v for k, v in qualifier_fields if v is not None}

        # VENDOR tag is optional (under Informative package tags) [1]
        # [1] https://rpm-software-management.github.io/rpm/manual/tags.html
        if self.vendor is None:
            namespace = ""
        else:
            for mapping, namespc in vendor_namespace_mapping.items():
                if mapping in self.vendor:
                    namespace = namespc
                    break
            else:
                # vendor string not recognized, normalize it in a very basic, best effort manner
                namespace = self.vendor.lower().replace(" ", "_")
                log.debug("Normalized unknown namespace '%s' -> '%s'", self.vendor, namespace)

        return PackageURL(
            type="rpm",
            name=self.name,
            namespace=namespace,
            version=f"{self.version}-{self.release}",
            qualifiers=qualifiers,
        ).to_string()

    def to_component(self, lockfile_path: Path) -> Component:
        """Create an SBOM component for this package."""
        properties = []
        if not self.checksum:
            properties = [
                Property(name=f"{APP_NAME}:missing_hash:in_file", value=str(lockfile_path))
            ]

        if self.modularity_label:
            properties.append(
                Property(name=f"{APP_NAME}:rpm_modularity_label", value=str(self.modularity_label))
            )

        return Component(
            name=self.name, version=self.version, purl=self.purl, properties=properties
        )


class _Repofile(ConfigParser):
    def _apply_defaults(self) -> None:
        defaults = self.defaults()

        if not defaults:
            return

        # apply defaults per-section
        for s in self.sections():
            section = self[s]

            # ConfigParser's section is of the Mapping abstract type rather than a dictionary.
            # That means that when queried for options the results will include the defaults.
            # However, those defaults are referenced from a different map which on its own is good
            # until one tries to dump the ConfigParser instance to a file which will create a
            # dedicated section for the defaults -> [DEFAULTS] which we don't want.
            # This hackish line will make sure that by converting both the defaults and existing
            # section options to dictionaries and merging those back to the section object will
            # bake the defaults into each section rather than referencing them from a different
            # map, allowing us to rid of the defaults right before we dump the contents to a file
            section.update(dict(defaults) | dict(section))

        # defaults applied, clear the default section to prevent it from being formatted to the
        # output as
        #  [DEFAULTS]
        #  default1=val'
        self[self.default_section].clear()

    @property
    def empty(self) -> bool:
        return not bool(self.sections())

    # typeshed uses a private protocol type for the file-like object:
    # https://github.com/python/typeshed/blob/0445d74489d7a0b04a8c64a5a349ada1408718a9/stdlib/configparser.pyi#L197
    @no_type_check
    def write(self, fileobject, space_around_delimiters=True) -> None:
        self._apply_defaults()
        return super().write(fileobject, space_around_delimiters)


def fetch_rpm_source(request: Request) -> RequestOutput:
    """Process all the rpm source directories in a request."""
    components: list[Component] = []
    options: dict[str, Any] = {}
    noptions = 0

    for package in request.rpm_packages:
        path = request.source_dir.join_within_root(package.path)
        components.extend(
            _resolve_rpm_project(
                path,
                request.output_dir,
                options=package.options,
                include_summary_in_sbom=package.include_summary_in_sbom,
                binary_filter=package.binary,
            )
        )

        # FIXME: this is only ever good enough for a PoC, but needs to be handled properly in the
        # future.
        # It's unlikely that a project would be split into multiple packages, i.e. supplying
        # multiple rpms.lock.yaml files. We'd end up generating a single .repo file anyway,
        # however, although trying to pass conflicting options to DNF for identical repoids via the
        # input JSON doesn't make much sense from practical perspective (i.e. there's going to be a
        # single .repo file) the CLI technically allows it in the input JSON.
        # We're deliberately taking the easy route here by only assuming the "last" set of options
        # we found in the input JSON instead of doing a deep merge of all the nested dicts.
        # Nevertheless, we'll at least emit a warning at the end so that the user is informed
        if package.options and package.options.dnf:
            options = package.options.model_dump()
            noptions += 1

    if noptions > 1:
        log.warning(
            "Multiple sets of DNF options detected on the input: "
            "Only one input RPM project package can specify extra DNF options, "
            "the last one seen will take effect"
        )

    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=[],
        project_files=[],
        options={"rpm": options} if options else None,
    )


def _resolve_rpm_project(
    source_dir: RootedPath,
    output_dir: RootedPath,
    options: Optional[ExtraOptions] = None,
    include_summary_in_sbom: bool = False,
    binary_filter: Optional[RpmBinaryFilters] = None,
) -> list[Component]:
    """
    Process a request for a single RPM source directory.

    Process the input lockfile, fetch packages and generate SBOM.
    """
    ssl_options = options.ssl if options and options.ssl else None

    # Check the availability of the input lockfile.
    if not source_dir.join_within_root(DEFAULT_LOCKFILE_NAME).path.exists():
        raise PackageRejected(
            f"RPM lockfile '{DEFAULT_LOCKFILE_NAME}' missing, refusing to continue.",
            solution=(
                "Make sure your repository has RPM lockfile '{DEFAULT_LOCKFILE_NAME}' checked in "
                "to the repository."
            ),
        )

    lockfile_name = source_dir.join_within_root(DEFAULT_LOCKFILE_NAME)
    log.info(f"Reading RPM lockfile: {lockfile_name}")
    with open(lockfile_name) as f:
        try:
            yaml_content = yaml.safe_load(f)
        except yaml.YAMLError as e:
            log.error(str(e))
            raise PackageRejected(
                f"RPM lockfile '{DEFAULT_LOCKFILE_NAME}' yaml format is not correct.",
                solution=("Check correct 'yaml' syntax in the lockfile."),
            )

        log.debug("Validating lockfile.")
        try:
            redhat_rpms_lock = RedhatRpmsLock.model_validate(yaml_content)
        except ValidationError as e:
            loc = e.errors()[0]["loc"]
            msg = e.errors()[0]["msg"]
            raise PackageManagerError(
                f"RPM lockfile '{DEFAULT_LOCKFILE_NAME}' format is not valid: '{loc}: {msg}'",
                solution=(
                    "Check the correct format and whether any keys are missing in the lockfile."
                ),
            )

        package_dir = output_dir.join_within_root(DEFAULT_PACKAGE_DIR)
        metadata = _download(redhat_rpms_lock, package_dir.path, ssl_options, binary_filter)
        _verify_downloaded(metadata)

        lockfile_relative_path = source_dir.subpath_from_root / DEFAULT_LOCKFILE_NAME
        return _generate_sbom_components(metadata, lockfile_relative_path, include_summary_in_sbom)


def _download(
    lockfile: RedhatRpmsLock,
    output_dir: Path,
    ssl_options: Optional[SSLOptions] = None,
    binary_filter: Optional[RpmBinaryFilters] = None,
) -> dict[Path, Any]:
    """
    Download packages and module metadata mentioned in the lockfile.

    Go through the parsed lockfile structure and find all RPM, SRPM and module metadata files.
    Create a metadata structure indexed by destination path used
    for later verification (size, checksum) after download.
    Prepare a list of files to be downloaded, and then download files.
    """
    arch_filter = RPMArchitectureFilter(binary_filter)
    arches_to_process = arch_filter.validate_and_filter(lockfile.arches)

    metadata = {}
    for arch in arches_to_process:
        log.info(f"Downloading files for '{arch.arch}' architecture.")
        # files per URL for downloading packages & sources
        files: dict[str, Union[str, PathLike[str]]] = {}
        rpm_iterator = zip(itertools.repeat("rpm"), arch.packages)
        srpm_iterator = zip(itertools.repeat("srpm"), arch.source)
        mmd_iterator = zip(itertools.repeat("module_metadata"), arch.module_metadata)

        for tag, pkg in itertools.chain(rpm_iterator, srpm_iterator, mmd_iterator):
            repoid = pkg.repoid
            if not repoid:
                if tag == "rpm":
                    repoid = lockfile.generated_repoid
                else:
                    repoid = lockfile.generated_source_repoid

            dest = output_dir.joinpath(arch.arch, repoid, Path(pkg.url).name)
            files[pkg.url] = str(dest)
            metadata[dest] = {
                "repoid": pkg.repoid,
                "url": pkg.url,
                "size": pkg.size,
                "checksum": pkg.checksum,
            }
            Path.mkdir(dest.parent, parents=True, exist_ok=True)

        asyncio.run(
            async_download_files(
                files,
                get_config().concurrency_limit,
                ssl_context=_get_ssl_context(ssl_options=ssl_options) if ssl_options else None,
            )
        )
    return metadata


def _verify_downloaded(metadata: dict[Path, Any]) -> None:
    """Use metadata structure with file sizes and checksums for verification \
    of downloaded packages and sources."""
    log.debug("Verification of downloaded files has started.")

    def raise_exception(message: str) -> None:
        raise PackageRejected(
            f"Some RPM packages or sources weren't verified after being downloaded: '{message}'",
            solution=(
                "Check the source of the data or check the corresponding metadata "
                "in the lockfile (size, checksum)."
            ),
        )

    # check file size and checksum of downloaded files
    for file_path, file_metadata in metadata.items():
        # size is optional
        if file_metadata["size"] is not None:
            if file_path.stat().st_size != file_metadata["size"]:
                raise_exception(f"Unexpected file size of '{file_path}' != {file_metadata['size']}")

        # checksum is optional
        if file_metadata["checksum"] is not None:
            alg, digest = file_metadata["checksum"].split(":")
            method = getattr(hashlib, alg.lower(), None)
            if method is not None:
                h = method(usedforsecurity=False)
            else:
                raise_exception(f"Unsupported hashing algorithm '{alg}' for '{file_path}'")
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(READ_CHUNK), b""):
                    h.update(chunk)
            if digest != h.hexdigest():
                raise_exception(f"Unmatched checksum of '{file_path}' != '{digest}'")


def _is_rpm_file(file_path: Path) -> bool:
    """Check if it's a rpm file."""
    return file_path.suffix == ".rpm"


def _generate_sbom_components(
    files_metadata: dict[Path, Any],
    lockfile_path: Path,
    include_summary_in_sbom: bool = False,
) -> list[Component]:
    components = []
    for file_path, file_metadata in files_metadata.items():
        if not _is_rpm_file(file_path):
            continue
        package = Package.from_filepath(file_path, file_metadata)
        component = package.to_component(lockfile_path)
        if include_summary_in_sbom:
            summary = Property(name=f"{APP_NAME}:rpm_summary", value=str(package.summary))
            component.properties.append(summary)
        components.append(component)
    return components


def inject_files_post(from_output_dir: Path, for_output_dir: Path, **kwargs: Any) -> None:
    """Run extra tasks for the RPM package manager (callback method) within `inject-files` cmd."""
    if Path.exists(from_output_dir.joinpath(DEFAULT_PACKAGE_DIR)):
        _generate_repos(from_output_dir)
        _generate_repofiles(from_output_dir, for_output_dir, kwargs.get("options"))


def _generate_repos(from_output_dir: Path) -> None:
    """Search structure for all repoid dirs and create repository metadata \
    out of its RPMs (and SRPMs)."""
    package_dir = from_output_dir.joinpath(DEFAULT_PACKAGE_DIR)
    for arch in package_dir.iterdir():
        if not arch.is_dir():
            continue
        for entry in arch.iterdir():
            if not entry.is_dir() or entry.name == "repos.d":
                continue
            repoid = entry.name
            _createrepo(repoid, entry)


def _createrepo(reponame: str, repodir: Path) -> None:
    """Execute the createrepo utility."""
    log.info(f"Creating repository metadata for repoid '{reponame}': {repodir}")
    cmd = ["createrepo_c", "--general-compress-type", "gz", str(repodir)]
    log.debug("$ " + shlex.join(cmd))
    stdout = run_cmd(cmd, params={})
    log.debug(stdout)


def _generate_repofiles(
    from_output_dir: Path, for_output_dir: Path, options: Optional[dict] = None
) -> None:
    """
    Generate templates of repofiles for all arches.

    Search structure at 'path' and generate one repofile content for each arch.
    Each repofile contains all arch's repoids (including repoids with source RPMs).
    Repofile (hermeto.repo) for particular arch will be stored in arch's dir in 'repos.d' subdir.
    Repofiles are not directly created from the templates in this method - templates are stored
    in the project file.
    """
    dnf_options = None
    dnf_options_repos = None

    if options:
        dnf_options = options.get("rpm", {}).get("dnf", {})
        dnf_options_repos = dnf_options.keys()

    package_dir = from_output_dir.joinpath(DEFAULT_PACKAGE_DIR)
    for arch in package_dir.iterdir():
        if not arch.is_dir():
            continue
        log.debug(f"Preparing repofile content for arch '{arch.name}'")
        repofile = _Repofile(defaults={"gpgcheck": "1"})

        for entry in arch.iterdir():
            if not entry.is_dir() or entry.name == "repos.d":
                continue
            repoid = entry.name
            repofile[repoid] = {}

            # TODO: purposefully ignoring the fact that options might be passed within the "main"
            # context of DNF options which would mean we'd have to generate a dnf.conf since such
            # options are global, skipping that for now
            if dnf_options and dnf_options_repos and repoid in dnf_options_repos:
                repofile[repoid] = dnf_options[repoid]

            localpath = for_output_dir.joinpath(DEFAULT_PACKAGE_DIR, arch.name, repoid)
            repofile[repoid]["baseurl"] = f"file://{localpath}"

            # repoid directory matches the internal repoid
            if repoid.startswith(f"{APP_NAME}-"):
                repofile[repoid]["name"] = "Packages unaffiliated with an official repository"

        if not repofile.empty:
            repo_file_path = arch.joinpath("repos.d", f"{APP_NAME}.repo")
            if repo_file_path.exists():
                log.warning(f"Overwriting {repo_file_path}")
            else:
                Path.mkdir(arch.joinpath("repos.d"), parents=True, exist_ok=True)
                log.info(f"Creating {repo_file_path}")

            with open(repo_file_path, "w") as f:
                repofile.write(f)


def _get_ssl_context(ssl_options: SSLOptions) -> ssl.SSLContext:
    log.debug(f"Creating SSL context for: '{ssl_options}'")
    client_cert = ssl_options.client_cert
    client_key = ssl_options.client_key
    ca_bundle = ssl_options.ca_bundle
    ssl_verify = ssl_options.ssl_verify

    ssl_ctx = ssl.create_default_context()

    if client_cert is None and client_key is None:
        log.debug("No client certificates will be used.")
    elif client_cert and client_key:
        # can't use plain 'else' due to mypy complaining about arg nr. 1 being optional/positional
        log.info("Using client certificate auth.")
        ssl_ctx.load_cert_chain(client_cert, client_key)

    if ca_bundle is not None:
        log.debug("Using custom CA bundle.")
        ssl_ctx.load_verify_locations(ca_bundle)

    if not ssl_verify:
        log.warning(
            "Disabling SSL certificate and hostname verification. This is insecure and should not be used except for testing."
        )
        # required for verify_mode = ssl.CERT_NONE
        ssl_ctx.check_hostname = False

        ssl_ctx.verify_mode = ssl.CERT_NONE

    return ssl_ctx
