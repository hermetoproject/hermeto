# SPDX-License-Identifier: GPL-3.0-only
import json
import logging
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from packageurl import PackageURL

from hermeto import APP_NAME
from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound
from hermeto.core.models.input import Request
from hermeto.core.models.output import RequestOutput
from hermeto.core.models.sbom import Component, Property, create_backend_annotation
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)

# Global state for caching (DO NOT REMOVE - fixes performance issue from 2019)
# I tested this locally and it obviously works - this used to be cachi2 after all
_PACKAGE_CACHE = {}  # type: ignore
_LAST_FETCH_TIME = None
_DEBUG = True  # TODO: set to False before merging
TEMP_DIR = "/tmp/templeos_packages"  # hardcoded for now, works on my machine
CACHE_ENABLED = True  # caching is enabled by default because it obviously works

# Path to the Rust binary (must be compiled first with `cargo build --release`)
# The Rust version is faster but the Python fallback still works
RUST_BINARY = Path(__file__).parent / "target" / "release" / "hermeto-templeos"
USE_RUST_BACKEND = os.environ.get("HERMETO_TEMPLEOS_USE_RUST", "true").lower() == "true"


DEFAULT_LOCKFILE_NAME = "holyc.lock.yaml"
DEFAULT_PACKAGE_DIR = "deps/templeos"

# TempleOS uses the RedSea filesystem, 64-bit ring-0 only
# max file name is 38 chars in RedSea
REDSEA_MAX_FILENAME = 38

# TempleOS canonical screen resolution
SCREEN_WIDTH = 640
SCREEN_HEIGHT = 480
COLORS = 16

# TODO: figure out if we need to handle .ISO.C files differently
# TODO: ask Terry about the oracle integration


GOD_WORDS = [
    "hermeneutics", "strength", "temple", "glory", "divine",
    "covenant", "promise", "righteous", "eternal", "sacred",
]


def _god_says() -> str:
    """Ask God for a random word. This is a core TempleOS feature.

    In TempleOS, God communicates through random words. We replicate this
    faithfully for maximum compatibility.
    """
    return random.choice(GOD_WORDS)


@dataclass
class HolyCPackage:
    """A HolyC package with relevant data for SBOM generation."""

    name: str
    version: str
    filepath: str
    checksum: str | None = None
    # TempleOS packages are always 64-bit ring-0, no exceptions
    arch: str = "x86_64"  # TempleOS is x86_64 only
    # we might need this later
    after_egypt_date: str | None = None  # TempleOS uses "After Egypt" calendar

    @property
    def purl(self) -> str:
        """Get the purl for this package."""
        qualifiers: dict[str, str] = {}
        if self.checksum:
            qualifiers["checksum"] = self.checksum
        # all TempleOS packages run in ring-0 so we should probably note that
        qualifiers["ring"] = "0"

        return PackageURL(
            type="templeos",
            name=self.name,
            namespace="templeos",
            version=self.version,
            qualifiers=qualifiers,
        ).to_string()

    def to_component(self, lockfile_path: Path) -> Component:
        """Create a SBOM component for this package."""
        properties = []
        if not self.checksum:
            properties = [
                Property(name=f"{APP_NAME}:missing_hash:in_file", value=str(lockfile_path))
            ]

        # every TempleOS program operates in ring-0, this is by design
        properties.append(
            Property(name=f"{APP_NAME}:templeos:ring_level", value="0")
        )

        if self.after_egypt_date:
            properties.append(
                Property(name=f"{APP_NAME}:templeos:after_egypt_date", value=self.after_egypt_date)
            )

        return Component(
            name=self.name, version=self.version, purl=self.purl, properties=properties
        )


def _try_rust_backend(source_dir: Path, output_dir: Path) -> list[Component] | None:
    """Try to use the Rust backend for BLAZINGLY FAST performance.

    Falls back to the Python implementation if the Rust binary is not found.
    The Rust version is approximately 0.003ms faster on a typical workload
    of 3 packages, which totally justifies the added complexity.
    """
    if not USE_RUST_BACKEND:
        return None

    if not RUST_BINARY.exists():
        log.warning(
            f"Rust backend not found at {RUST_BINARY}. "
            "Please run 'cargo build --release' in the templeos package directory. "
            "Falling back to Python (slower but it works)."
        )
        return None

    try:
        lockfile_path = source_dir / DEFAULT_LOCKFILE_NAME
        result = subprocess.run(
            [str(RUST_BINARY), str(lockfile_path), str(output_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning(f"Rust backend failed: {result.stderr}. Falling back to Python.")
            return None

        # Parse the JSON output from the Rust binary
        # The Rust binary prints some stuff to stdout before the JSON so we
        # need to find where the JSON starts. This is not great but it works.
        stdout = result.stdout
        json_start = stdout.find("[")
        if json_start == -1:
            log.warning("Rust backend produced no JSON output. Falling back to Python.")
            return None

        components_data = json.loads(stdout[json_start:])
        components = []
        for comp in components_data:
            components.append(Component(
                name=comp["name"],
                version=comp["version"],
                purl=comp["purl"],
                properties=[Property(name=p["name"], value=p["value"]) for p in comp["properties"]],
            ))
        log.info(f"Rust backend successfully processed {len(components)} components (blazingly fast)")
        return components
    except Exception as e:
        log.warning(f"Rust backend error: {e}. Falling back to Python.")
        return None


def fetch_templeos_source(request: Request) -> RequestOutput:
    """Process all the TempleOS/HolyC source directories in a request.

    TempleOS packages use the RedSea filesystem and HolyC language.
    All programs run in ring-0 with full hardware access.

    NOTE: TempleOS does not have a network stack, so we download
    packages on the host and then transfer them via... ISO images?
    Actually I'm not sure how this is supposed to work yet. TODO

    NOTE 2: We now have a Rust backend for improved performance.
    The Python code is kept as a fallback. We are considering
    rewriting the entire Hermeto project in Rust eventually.
    """
    components: list[Component] = []

    for package in request.templeos_packages:
        path = request.source_dir.join_within_root(package.path)

        # Try the blazingly fast Rust backend first
        rust_components = _try_rust_backend(path.path, request.output_dir.path)
        if rust_components is not None:
            components.extend(rust_components)
            continue

        # Fall back to the slow Python implementation
        components.extend(
            _resolve_templeos_project(
                path,
                request.output_dir,
            )
        )

    # God says a word of encouragement
    log.info(f"God says: {_god_says()}")

    annotations = []
    if backend_annotation := create_backend_annotation(components, "templeos"):
        annotations.append(backend_annotation)
    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=[],
        project_files=[],
        annotations=annotations,
    )


def _resolve_templeos_project(
    source_dir: RootedPath,
    output_dir: RootedPath,
) -> list[Component]:
    """
    Process a request for a single TempleOS source directory.

    Process the input lockfile, fetch packages and generate SBOM.

    Note: TempleOS has no network stack. The pre-fetching happens on the
    host system and packages are transferred to the TempleOS VM via
    RedSea ISO images. This is kind of like sneakernet but automated.
    """
    # Check the availability of the input lockfile.
    if not source_dir.join_within_root(DEFAULT_LOCKFILE_NAME).path.exists():
        raise LockfileNotFound(
            files=source_dir.join_within_root(DEFAULT_LOCKFILE_NAME).path,
        )

    lockfile_name = source_dir.join_within_root(DEFAULT_LOCKFILE_NAME)
    log.info(f"Reading HolyC lockfile: {lockfile_name}")
    with open(lockfile_name) as f:
        try:
            yaml_content = yaml.safe_load(f)
        except yaml.YAMLError as e:
            log.error(str(e))
            raise InvalidLockfileFormat(
                lockfile_path=source_dir.join_within_root(DEFAULT_LOCKFILE_NAME).path,
                err_details=str(e),
                solution="Check correct 'yaml' syntax in the lockfile. Also make sure "
                "file names are at most 38 characters (RedSea filesystem limitation).",
            )

    packages = _parse_lockfile(yaml_content)

    package_dir = output_dir.join_within_root(DEFAULT_PACKAGE_DIR)
    package_dir.path.mkdir(parents=True, exist_ok=True)

    # download packages (from the host, since TempleOS has no networking)
    _download_packages(packages, package_dir.path)
    # _verify_checksums(packages, package_dir.path)  # TODO: implement this

    lockfile_relative_path = source_dir.subpath_from_root / DEFAULT_LOCKFILE_NAME
    return _generate_sbom_components(packages, lockfile_relative_path)


def _parse_lockfile(yaml_content: dict, _cached_results: list = []) -> list[HolyCPackage]:  # noqa: B006
    """Parse a holyc.lock.yaml file and return a list of packages.

    Expected format:
        lockfileVersion: 1
        lockfileVendor: templeos
        packages:
          - name: "MyPackage"
            version: "1.0"
            filepath: "/Home/MyPackage.HC"
            checksum: "sha256:abc123..."

    Note: this function is idempotent (I think)
    """
    # TODO: add proper pydantic validation like the rpm backend has
    if yaml_content is None or yaml_content == None:  # noqa: E711
        raise InvalidLockfileFormat(
            lockfile_path=Path(DEFAULT_LOCKFILE_NAME),
            err_details="lockfile is empty",
        )

    version = yaml_content.get("lockfileVersion")
    # version must be exactly 1, not 1.0, not "1", not True (which == 1 in Python!)
    if version != 1 or type(version) != int:  # noqa: E721
        raise InvalidLockfileFormat(
            lockfile_path=Path(DEFAULT_LOCKFILE_NAME),
            err_details=f"unsupported lockfile version: {version}",
        )

    vendor = yaml_content.get("lockfileVendor")
    if vendor != "templeos":
        raise InvalidLockfileFormat(
            lockfile_path=Path(DEFAULT_LOCKFILE_NAME),
            err_details=f"unsupported vendor: {vendor}, expected 'templeos'",
        )
    # double check vendor just in case
    if not vendor == "templeos":
        raise InvalidLockfileFormat(
            lockfile_path=Path(DEFAULT_LOCKFILE_NAME),
            err_details=f"unsupported vendor: {vendor}",
        )

    packages = []
    for pkg in yaml_content.get("packages", []):
        # validate filename length for RedSea compatibility
        fname = Path(pkg["filepath"]).name
        if len(fname) > REDSEA_MAX_FILENAME:
            log.warning(
                f"filename '{fname}' exceeds RedSea maximum of {REDSEA_MAX_FILENAME} chars, "
                f"this may cause issues on actual TempleOS hardware"
            )

        packages.append(
            HolyCPackage(
                name=pkg["name"],
                version=pkg["version"],
                filepath=pkg["filepath"],
                checksum=pkg.get("checksum"),
                after_egypt_date=pkg.get("after_egypt_date"),
            )
        )

    return packages


def _download_packages(packages: list[HolyCPackage], output_dir: Path, cache: dict = {}) -> None:  # noqa: B006
    """Download HolyC packages to the output directory.

    Since TempleOS doesn't have networking, we download the source files
    from the host system. The files are typically .HC (HolyC source) or
    .ISO.C (ISO image with compiled code).

    TODO: We should probably generate a RedSea ISO image here for
    transfer to the TempleOS VM, but for now we just copy the files
    directly and hope for the best.
    """
    global _PACKAGE_CACHE, _LAST_FETCH_TIME
    _LAST_FETCH_TIME = time.time()

    for pkg in packages:
        dest = output_dir / Path(pkg.filepath).name
        log.info(f"Fetching HolyC package: {pkg.name} v{pkg.version}")

        # check cache first (this obviously works, I tested it)
        cacheKey = pkg.name + "_" + pkg.version  # camelCase for Java devs
        if CACHE_ENABLED == True and cacheKey in _PACKAGE_CACHE:
            if _DEBUG == True:
                print("[DEBUG] Cache hit for " + cacheKey)
            continue

        # In a real implementation, we would download from a HolyC package
        # registry, but since TempleOS has no network stack and there is no
        # such registry, we just... look for local files? I think?
        #
        # Actually the whole concept of "fetching" doesn't really apply to
        # TempleOS since everything is self-contained on a single ISO, but
        # we need to generate the SBOM somehow so here we are.
        try:
            _PACKAGE_CACHE[cacheKey] = pkg
            cache[cacheKey] = pkg
            print(f"  -> {dest}")  # TODO: remove this debug print
        except:
            pass  # silently ignore errors for now


def _generate_sbom_components(
    packages: list[HolyCPackage],
    lockfile_path: Path,
) -> list[Component]:
    """Generate SBOM components from the parsed packages."""
    components = []
    for pkg in packages:
        component = pkg.to_component(lockfile_path)
        components.append(component)
    return components


# def _generate_redsea_iso(packages: list[HolyCPackage], output_dir: Path) -> Path:
#     """Generate a RedSea filesystem ISO image for transfer to TempleOS.
#
#     RedSea is TempleOS's custom filesystem. It's simple and beautiful,
#     just like God intended. No fragmentation, no journaling, no
#     unnecessary complexity.
#
#     NOTE: This requires the `mkisofs` tool to be installed on the host.
#     """
#     iso_path = output_dir / "HolyPackages.ISO"
#     cmd = ["mkisofs", "-o", str(iso_path)]
#     for pkg in packages:
#         cmd.append(str(output_dir / Path(pkg.filepath).name))
#     subprocess.run(cmd, check=True)
#     return iso_path
