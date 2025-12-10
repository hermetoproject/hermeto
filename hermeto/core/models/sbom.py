import hashlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from functools import cached_property, partial, reduce
from itertools import chain, groupby
from pathlib import Path
from typing import Annotated, Any, Literal, Union
from urllib.parse import urlparse

import pydantic
from packageurl import PackageURL
from typing_extensions import Self

from hermeto import APP_NAME
from hermeto.core.models.property_semantics import Property, PropertyEnum, PropertySet
from hermeto.core.utils import first_for

log = logging.getLogger(__name__)


def datetime_to_iso_8601(value: datetime) -> str:
    """Serialize a datetime to a string in ISO 8601 standard."""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


ISODatetime = Annotated[datetime, pydantic.PlainSerializer(datetime_to_iso_8601)]


class Annotation(pydantic.BaseModel):
    """
    A comment, note, explanation, or similar textual content
    which provides additional context to the object(s) being annotated.

    https://cyclonedx.org/docs/1.6/json/#annotations
    """

    subjects: list[str]
    annotator: dict[Literal["organization", "individual", "component", "service"], dict[str, str]]
    timestamp: ISODatetime
    text: str


class ExternalReference(pydantic.BaseModel):
    """An ExternalReference inside an SBOM component."""

    url: str
    type: Literal["distribution"] = "distribution"


class PatchDiff(pydantic.BaseModel):
    """A Diff inside a Patch."""

    url: str


class Patch(pydantic.BaseModel):
    """A Patch inside a SBOM Component Pedigree."""

    type: Literal["backport", "cherry-pick", "monkey", "unofficial"] = "unofficial"
    diff: PatchDiff


class Pedigree(pydantic.BaseModel):
    """A Pedigree inside a SBOM component."""

    patches: list[Patch]


FOUND_BY_APP_PROPERTY: Property = Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}")


class Component(pydantic.BaseModel):
    """A software component such as a dependency or a package.

    Compliant to the CycloneDX specification:
    https://cyclonedx.org/docs/1.6/json/#components
    """

    name: str
    purl: str
    version: str | None = None
    properties: list[Property] = pydantic.Field(default_factory=list, validate_default=True)
    type: Literal["library", "file"] = "library"
    external_references: list[ExternalReference] | None = pydantic.Field(
        alias="externalReferences", default=None
    )
    pedigree: Pedigree | None = None

    # Aliased fields may be populated by their name as given by the model attribute.
    model_config = pydantic.ConfigDict(validate_by_name=True, extra="forbid")

    def key(self) -> str:
        """Uniquely identifies a package.

        Used mainly for sorting and deduplication.
        """
        return self.purl

    @pydantic.field_validator("properties")
    @classmethod
    def _add_found_by_property(cls, properties: list[Property]) -> list[Property]:
        if FOUND_BY_APP_PROPERTY not in properties:
            properties.append(FOUND_BY_APP_PROPERTY)

        return properties

    @classmethod
    def from_package_dict(cls, package: dict[str, Any]) -> "Component":
        """Create a Component from our package dictionary.

        A package has extra fields which are unnecessary and can cause validation errors.
        """
        return cls(
            name=package.get("name", ""),
            version=package.get("version"),
            purl=package.get("purl", ""),
        )


class Tool(pydantic.BaseModel):
    """A tool used to generate the SBOM content."""

    vendor: str
    name: str


class Metadata(pydantic.BaseModel):
    """Metadata field in a SBOM."""

    tools: list[Tool] = [Tool(vendor="red hat", name=f"{APP_NAME}")]


def spdx_now() -> datetime:
    """Return a time stamp in SPDX-compliant format.

    See https://spdx.github.io/spdx-spec/v2.3/search.html?q=date
    for details.
    """
    return datetime.now(timezone.utc)


def sanitize_spdxid(spdxid: str) -> str:
    """Sanitize an SPDXID.

    See https://spdx.github.io/spdx-spec/v2.3/package-information/#7.2:

    Format  "SPDXRef-"[idstring]
            where [idstring] is a unique string containing letters, numbers, ., and/or -.
    """
    return re.sub(r"[^0-9a-zA-Z\.\-]", "-", spdxid)


class Sbom(pydantic.BaseModel):
    """Software bill of materials in the CycloneDX format.

    See full specification at:
    https://cyclonedx.org/docs/1.6/json
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    bom_format: Literal["CycloneDX"] = pydantic.Field(alias="bomFormat", default="CycloneDX")
    components: list[Component] = []
    metadata: Metadata = Metadata()
    spec_version: str = pydantic.Field(alias="specVersion", default="1.6")
    version: int = 1

    def __add__(self, other: Union["Sbom", "SPDXSbom"]) -> "Sbom":
        if isinstance(other, self.__class__):
            return Sbom(
                components=merge_component_properties(
                    chain.from_iterable(s.components for s in [self, other])
                )
            )
        else:
            return self + other.to_cyclonedx()

    @pydantic.field_validator("components")
    @classmethod
    def _unique_components(cls, components: list[Component]) -> list[Component]:
        """Sort and de-duplicate components."""
        return merge_component_properties(components)

    def to_cyclonedx(self) -> Self:
        """Return self, self is already the right type of Sbom."""
        # This is a short-cut, but since it is unlikely that we would ever add more Sbom types
        # it is acceptable. If, however this ever happens a proper base class will be needed.
        return self

    def to_spdx(self, doc_namespace: str) -> "SPDXSbom":
        """Convert a CycloneDX SBOM to an SPDX SBOM.

        Args:
            doc_namespace: SPDX document namespace. Namespace is URI of indicating

        """

        def create_document_root() -> SPDXPackage:
            return SPDXPackage(name="", versionInfo="", SPDXID="SPDXRef-DocumentRoot-File-")

        def create_root_relationship() -> SPDXRelation:
            return SPDXRelation(
                spdxElementId="SPDXRef-DOCUMENT",
                comment="",
                relatedSpdxElement="SPDXRef-DocumentRoot-File-",
                relationshipType="DESCRIBES",
            )

        def link_to_root(packages: list[SPDXPackage]) -> list[SPDXRelation]:
            relationships, root_id, rtype = [], "SPDXRef-DocumentRoot-File-", "CONTAINS"
            pRel = partial(SPDXRelation, spdxElementId=root_id, comment="", relationshipType=rtype)
            for package in packages:
                if package.SPDXID == "SPDXRef-DocumentRoot-File-":
                    continue
                relationships.append(pRel(relatedSpdxElement=package.SPDXID))
            return relationships

        def libs_to_packages(libraries: list[Component]) -> list[SPDXPackage]:
            packages, annottr, now = [], f"Tool: {APP_NAME}:jsonencoded", spdx_now()
            args = dict(annotator=annottr, annotationDate=now, annotationType="OTHER")
            pAnnotation = partial(SPDXPackageAnnotation, **args)

            mkcomm = lambda p: json.dumps(dict(name=f"{p.name}", value=f"{p.value}"))
            hashdict = lambda c: dict(name=c.name, version=c.version, purl=c.purl)
            erefbase = dict(referenceCategory="PACKAGE-MANAGER", referenceType="purl")
            erefdict = lambda c: dict(referenceLocator=c.purl, **erefbase)

            for component in libraries:
                package_hash = SPDXPackage._calculate_package_hash_from_dict(hashdict(component))

                if component.version:
                    human_readable_id = f"{component.name}-{component.version}"
                else:
                    human_readable_id = component.name

                packages.append(
                    SPDXPackage(
                        SPDXID=sanitize_spdxid(
                            f"SPDXRef-Package-{human_readable_id}-{package_hash}"
                        ),
                        name=component.name,
                        versionInfo=component.version,
                        externalRefs=[erefdict(component)],
                        annotations=[pAnnotation(comment=mkcomm(p)) for p in component.properties],
                    )
                )
            return packages

        # Main function body.
        packages = [create_document_root()] + libs_to_packages(self.components)
        relationships = [create_root_relationship()] + link_to_root(packages)
        creator = lambda tool: [f"Tool: {tool.name}", f"Organization: {tool.vendor}"]
        return SPDXSbom(
            packages=packages,
            relationships=relationships,
            documentNamespace=doc_namespace,
            creationInfo=SPDXCreationInfo(
                creators=sum([creator(tool) for tool in self.metadata.tools], []),
                created=spdx_now(),
            ),
        )


class SPDXPackageExternalRefReferenceLocatorURI(pydantic.BaseModel):
    """SPDX Package External Reference with URI reference locator."""

    referenceLocator: str

    @pydantic.field_validator("referenceLocator")
    @classmethod
    def _validate_uri_reference_locator(cls, referenceLocator: str) -> str:
        parsed = urlparse(referenceLocator)
        if not (parsed.scheme and (parsed.path or parsed.netloc)):
            raise ValueError("Invalid URI reference locator")
        return referenceLocator


class SPDXPackageExternalRef(pydantic.BaseModel):
    """SPDX Package External Reference.

    Compliant to the SPDX specification:
    https://spdx.github.io/spdx-spec/v2.3/package-information/#721-external-reference-field
    """

    model_config = pydantic.ConfigDict(frozen=True)

    referenceLocator: str
    referenceType: str
    referenceCategory: str

    def __hash__(self) -> int:
        return hash((self.referenceLocator, self.referenceType, self.referenceCategory))


class SPDXPackageExternalRefSecurity(SPDXPackageExternalRef):
    """SPDX Package External Reference for category package-manager.

    Compliant to the SPDX specification:
    https://spdx.github.io/spdx-spec/v2.3/package-information/#721-external-reference-field
    """

    referenceCategory: Literal["SECURITY"]


class SPDXPackageExternalRefPackageManager(SPDXPackageExternalRef):
    """SPDX Package External Reference for category package-manager.

    Compliant to the SPDX specification:
    https://spdx.github.io/spdx-spec/v2.3/package-information/#721-external-reference-field
    """

    referenceCategory: Literal["PACKAGE-MANAGER"]


class SPDXPackageExternalRefPackageManagerPURL(
    SPDXPackageExternalRefPackageManager, SPDXPackageExternalRefReferenceLocatorURI
):
    """SPDX Package External Reference for category package-manager and type purl.

    Compliant to the SPDX specification:
    https://spdx.github.io/spdx-spec/v2.3/package-information/#721-external-reference-field
    """

    referenceCategory: Literal["PACKAGE-MANAGER"]
    referenceType: Literal["purl"]


class SPDXPackageExternalRefSecurityPURL(
    SPDXPackageExternalRefSecurity, SPDXPackageExternalRefReferenceLocatorURI
):
    """SPDX Package External Reference for category package-manager and type purl.

    Compliant to the SPDX specification:
    https://spdx.github.io/spdx-spec/v2.3/package-information/#721-external-reference-field
    """

    referenceCategory: Literal["SECURITY"]
    referenceType: Literal["cpe23Type"]


SPDXPackageExternalRefPackageManagerType = Annotated[
    SPDXPackageExternalRefPackageManagerPURL,
    pydantic.Field(discriminator="referenceType"),
]

SPDXPackageExternalRefSecurityType = Annotated[
    SPDXPackageExternalRefSecurityPURL,
    pydantic.Field(discriminator="referenceType"),
]


SPDXPackageExternalRefType = Annotated[
    SPDXPackageExternalRefPackageManagerType | SPDXPackageExternalRefSecurityType,
    pydantic.Field(discriminator="referenceCategory"),
]


class SPDXPackageAnnotation(pydantic.BaseModel):
    """SPDX Package Annotation.

    Compliant to the SPDX specification:
    https://github.com/spdx/spdx-spec/blob/development/v2.3/schemas/spdx-schema.json#L237
    """

    model_config = pydantic.ConfigDict(frozen=True)

    annotator: str
    annotationDate: ISODatetime
    annotationType: Literal["OTHER", "REVIEW"]
    comment: str

    def __hash__(self) -> int:
        return hash((self.annotator, self.annotationDate, self.annotationType, self.comment))


def _extract_purls(from_refs: list[SPDXPackageExternalRefType]) -> list[str]:
    return [ref.referenceLocator for ref in from_refs if ref.referenceType == "purl"]


def _parse_purls(purls: list[str]) -> list[PackageURL]:
    return [PackageURL.from_string(purl) for purl in purls if purl]


class SPDXPackage(pydantic.BaseModel):
    """SPDX Package.

    Compliant to the SPDX specification:
    https://spdx.github.io/spdx-spec/v2.3/package-information/
    """

    SPDXID: str | None = None
    name: str
    versionInfo: str | None = None
    externalRefs: list[SPDXPackageExternalRefType] = []
    annotations: list[SPDXPackageAnnotation] = []
    downloadLocation: str = "NOASSERTION"

    def __lt__(self, other: "SPDXPackage") -> bool:
        return (self.SPDXID or "") < (other.SPDXID or "")

    def __hash__(self) -> int:
        return hash(
            hash(self.SPDXID)
            + hash(self.name)
            + hash(self.versionInfo)
            + hash(self.downloadLocation)
            + sum(hash(e) for e in self.externalRefs)
            + sum(hash(a) for a in self.annotations)
        )

    @staticmethod
    def _calculate_package_hash_from_dict(package_dict: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(package_dict, sort_keys=True).encode()).hexdigest()

    @pydantic.field_validator("externalRefs")
    @classmethod
    def _purls_validation(
        cls, refs: list[SPDXPackageExternalRefType]
    ) -> list[SPDXPackageExternalRefType]:
        """Validate that SPDXPackage includes only one purl with the same type, name, version."""
        parsed_purls = _parse_purls(_extract_purls(from_refs=refs))
        unique_purls_parts = set([(p.type, p.name, p.version) for p in parsed_purls])
        if len(unique_purls_parts) > 1:
            raise ValueError(
                "SPDXPackage includes multiple purls with different (type,name,version) tuple: "
                + f"{unique_purls_parts}"
            )
        return refs

    @classmethod
    def from_package_dict(cls, package: dict[str, Any]) -> "SPDXPackage":
        """Create a SPDXPackage from our dictionary."""
        external_refs = package.get("externalRefs", [])
        annotations = [SPDXPackageAnnotation(**an) for an in package.get("annotations", [])]
        if package.get("SPDXID") is None:
            purls = sorted(
                [
                    ref["referenceLocator"]
                    for ref in package["externalRefs"]
                    if ref["referenceType"] == "purl"
                ]
            )
            package_hash = cls._calculate_package_hash_from_dict(
                {
                    "name": package["name"],
                    "version": package.get("versionInfo", None),
                    "purls": purls,
                }
            )
            SPDXID = (
                f"SPDXRef-Package-{package['name']}-{package.get('versionInfo', '')}-{package_hash}"
            )
        else:
            SPDXID = package["SPDXID"]
        return cls(
            SPDXID=SPDXID,
            name=package["name"],
            versionInfo=package.get("versionInfo", None),
            externalRefs=external_refs,
            annotations=annotations,
        )


class SPDXCreationInfo(pydantic.BaseModel):
    """SPDX Creation Information.

    Compliant to the SPDX specification:
    https://spdx.github.io/spdx-spec/v2.3/document-creation-information/
    """

    creators: list[str] = []
    created: ISODatetime


class SPDXRelation(pydantic.BaseModel):
    """SPDX Relationship.

    Compliant to the SPDX specification:
    https://spdx.github.io/spdx-spec/v2.3/relationships-between-SPDX-elements/
    """

    spdxElementId: str
    comment: str | None = None
    relatedSpdxElement: str
    relationshipType: str

    def __hash__(self) -> int:
        return hash(
            hash(self.spdxElementId + self.relatedSpdxElement + self.relationshipType)
            + hash(self.comment)
        )


class SPDXSbom(pydantic.BaseModel):
    """Software bill of materials in the SPDX format.

    See full specification at:
    https://spdx.github.io/spdx-spec/v2.3
    """

    # NOTE: The model is intentionally made non-strict for now because a strict model rejects
    # SBOMs generated by Syft. It is unclear at the moment if additional preprocessing will
    # be happening or desired.
    # This is also a reason to not make the model frozen.

    spdxVersion: Literal["SPDX-2.3"] = "SPDX-2.3"
    SPDXID: Literal["SPDXRef-DOCUMENT"] = "SPDXRef-DOCUMENT"
    dataLicense: Literal["CC0-1.0"] = "CC0-1.0"
    name: str = ""
    documentNamespace: str

    creationInfo: SPDXCreationInfo
    packages: list[SPDXPackage] = []
    relationships: list[SPDXRelation] = []

    def __hash__(self) -> int:
        return hash(
            hash(self.name + self.documentNamespace)
            + hash(SPDXCreationInfo)
            + sum(hash(p) for p in self.packages)
            + sum(hash(r) for r in self.relationships)
        )

    @classmethod
    def from_file(cls, path: Path) -> "SPDXSbom":
        """Consume a SPDX json directly from a file."""
        return cls.model_validate_json(path.read_text())

    @staticmethod
    def deduplicate_spdx_packages(items: Iterable[SPDXPackage]) -> list[SPDXPackage]:
        """Deduplicate SPDX packages and merge external references.

        Deduplication is very conservative and does not consider two packages same if
        their purls differ even if their type, name and version match. A package will be
        dropped iff it is a full purl match.
        """
        unique_items: dict[int, SPDXPackage] = {}
        for item in items:
            purls = _extract_purls(item.externalRefs)
            if purls:
                purl_key = hash(sum(hash(p) for p in _parse_purls(purls)))
            else:
                # This is likely just the root.
                log.warning(f"No purls found for {item}.")
                purl_key = hash(("", item.name, item.versionInfo or ""))

            if purl_key in unique_items:
                unique_items[purl_key].externalRefs.extend(item.externalRefs)
                unique_items[purl_key].annotations.extend(item.annotations)
            else:
                unique_items[purl_key] = item.model_copy(deep=True)

        for item in unique_items.values():
            item.externalRefs = sorted(
                set(item.externalRefs),
                key=lambda ref: (ref.referenceLocator, ref.referenceType, ref.referenceCategory),
            )
            item.annotations = sorted(
                set(item.annotations),
                key=lambda ann: (ann.annotator, ann.annotationDate, ann.comment),
            )
        return sorted(unique_items.values(), key=lambda item: (item.name, item.versionInfo or ""))

    @pydantic.field_validator("packages")
    @classmethod
    def _unique_packages(cls, packages: list[SPDXPackage]) -> list[SPDXPackage]:
        """Sort and de-duplicate components."""
        return cls.deduplicate_spdx_packages(packages)

    @cached_property
    def root_id(self) -> str:
        """Return the root_id of this SBOM."""
        direct_relationships, inverse_relationships = defaultdict(list), dict()
        for rel in self.relationships:
            direct_relationships[rel.spdxElementId].append(rel.relatedSpdxElement)
            inverse_relationships[rel.relatedSpdxElement] = rel.spdxElementId
        unidirectionally_related_package = lambda p: inverse_relationships.get(p) == self.SPDXID
        # Note: defaulting to top-level SPDXID is inherited from the original implementation.
        # It is unclear if it is really needed, but is left around to match the precedent.
        root_id = first_for(unidirectionally_related_package, direct_relationships, self.SPDXID)
        return root_id

    # NOTE: having this as cached will cause trouble when sequentially
    # constructing the object off of an empty state.
    @property
    def non_root_packages(self) -> list[SPDXPackage]:
        """Return non-root packages."""
        return [p for p in self.packages if p.SPDXID != self.root_id]

    @staticmethod
    def retarget_and_prune_relationships(
        from_sbom: "SPDXSbom",
        to_sbom: "SPDXSbom",
    ) -> list[SPDXRelation]:
        """Retarget and prune relationships."""
        out, from_root, to_root = [], from_sbom.root_id, to_sbom.root_id
        for r in from_sbom.relationships:
            # New relation must be with to_sbom root if old relation was of from_sbom root.
            # New relation must also be moved to new root if it was with from_sbom root.
            # These two moves cannot happen simultaneously.
            eid = r.spdxElementId
            if from_root in (eid, r.relatedSpdxElement):
                n_spdxEI = to_root
            else:
                n_spdxEI = eid
            # Do a copy to ensure we are not pulling a carpet from underneath us:
            new_rel = r.model_copy(update={"spdxElementId": n_spdxEI}, deep=True)
            if not (
                new_rel.relatedSpdxElement == from_sbom.root_id
                and new_rel.relationshipType == "DESCRIBES"
            ):
                out.append(new_rel)
        return out

    def __add__(self, other: Union["SPDXSbom", Sbom]) -> "SPDXSbom":
        if isinstance(other, self.__class__):
            # Packages are not going to be modified so it is OK to just pass
            # references around.
            merged_packages = self.packages + other.non_root_packages
            # Relationships, on the other hand, are amended, so new
            # relationships will be constructed. Further, identical
            # relationships should be dropped. Deduplication based on building
            # a set is considered safe because all fields of all elements are
            # used to compute a hash.
            processed_other = self.retarget_and_prune_relationships(from_sbom=other, to_sbom=self)
            merged_relationships = list(set(self.relationships + processed_other))
            res = self.model_copy(
                update={
                    # At the moment of writing pydantic does not deem it necessary to
                    # validate updated fields because we should just trust them [1].
                    "packages": self.deduplicate_spdx_packages(merged_packages),
                    "relationships": merged_relationships,
                },
                deep=True,
            )
            return res
        elif isinstance(other, Sbom):
            return self + other.to_spdx(doc_namespace="NOASSERTION")
        else:
            self_class = self.__class__.__name__
            other_class = other.__class__.__name__
            raise ValueError(f"Cannot merge {other_class} to {self_class}")

    def to_spdx(self, *a: Any, **k: Any) -> Self:  # noqa: ARG002
        """Return self, ignore arguments, self is already a SPDX document."""
        # This is a short-cut, but since it is unlikely that we would ever add more Sbom types
        # it is acceptable. If, however this ever happens a proper base class will be needed.
        return self

    def to_cyclonedx(self) -> Sbom:
        """Convert a SPDX SBOM to a CycloneDX SBOM."""
        components = []
        for package in self.packages:
            properties = [
                (
                    Property(**json.loads(an.comment))
                    if an.annotator.endswith(":jsonencoded")
                    else Property(name=an.annotator, value=an.comment)
                )
                for an in package.annotations
            ]
            pComponent = partial(
                Component, name=package.name, version=package.versionInfo, properties=properties
            )
            purls = _extract_purls(package.externalRefs)

            # cyclonedx doesn't support multiple purls, therefore
            # new component is created for each purl
            components += [pComponent(purl=purl) for purl in purls]
            # if there's no purl and no package name or version, it's just wrapping element for
            # spdx package which is one layer bellow SPDXDocument in relationships
            if not any((purls, package.name, package.versionInfo)):
                continue
            # if there's no purl, add it as single component
            elif not purls:
                components.append(pComponent(purl=""))
        tools = []
        name, vendor = None, None
        # Following approach is used as position of "Organization" and "Tool" is not
        # guaranteed by the standard
        for creator in self.creationInfo.creators:
            if creator.startswith("Organization:"):
                vendor = creator.replace("Organization:", "").strip()
            elif creator.startswith("Tool:"):
                name = creator.replace("Tool:", "").strip()
            if name is not None and vendor is not None:
                tools.append(Tool(vendor=vendor, name=name))
                name, vendor = None, None

        return Sbom(
            components=components,
            metadata=Metadata(tools=tools),
        )


def merge_component_properties(components: Iterable[Component]) -> list[Component]:
    """Sort and de-duplicate components while merging their `properties`."""
    components = sorted(components, key=Component.key)
    grouped_components = groupby(components, key=Component.key)

    def merge_component_group(component_group: Iterable[Component]) -> Component:
        component_group = list(component_group)
        prop_sets = (PropertySet.from_properties(c.properties) for c in component_group)
        merged_prop_set = reduce(PropertySet.merge, prop_sets)
        component = component_group[0]
        return component.model_copy(update={"properties": merged_prop_set.to_properties()})

    return [merge_component_group(g) for _, g in grouped_components]


# References
# [1] https://github.com/pydantic/pydantic/blob/6fa92d139a297a26725dec0a7f9b0cce912d6a7f/pydantic/main.py#L383
