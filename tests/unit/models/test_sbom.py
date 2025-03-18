import datetime
import json
from unittest import mock

import pydantic
import pytest

from cachi2.core.models.sbom import (
    FOUND_BY_APP_PROPERTY,
    Component,
    Metadata,
    Property,
    Sbom,
    SPDXPackage,
    SPDXPackageAnnotation,
    SPDXPackageExternalRefPackageManagerPURL,
    SPDXPackageExternalRefType,
    SPDXRelation,
    SPDXSbom,
    Tool,
)

SPDX_EPOCH_STRFTIME = datetime.datetime.fromtimestamp(0).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestComponent:
    @pytest.mark.parametrize(
        "input_data, expected_data",
        [
            (
                {"name": "mypkg", "purl": "pkg:generic/mypkg"},
                Component(name="mypkg", purl="pkg:generic/mypkg"),
            ),
            (
                {"name": "mypkg", "purl": "pkg:generic/mypkg@1.0.0", "version": "1.0.0"},
                Component(name="mypkg", version="1.0.0", purl="pkg:generic/mypkg@1.0.0"),
            ),
            (
                {"name": "mypkg", "purl": "pkg:generic/mypkg", "version": "random-version-string"},
                Component(name="mypkg", version="random-version-string", purl="pkg:generic/mypkg"),
            ),
            (
                {
                    "name": "mypkg",
                    "purl": "pkg:generic/mypkg@1.0.0",
                    "version": "1.0.0",
                    "type": "gomod",
                    "path": ".",
                    "dependencies": [],
                },
                Component(name="mypkg", version="1.0.0", purl="pkg:generic/mypkg@1.0.0"),
            ),
        ],
    )
    def test_construct_from_package_dict(
        self, input_data: dict[str, str], expected_data: Component
    ) -> None:
        component = Component.from_package_dict(input_data)
        assert component == expected_data

    @pytest.mark.parametrize(
        "input_data, expect_error",
        [
            (
                {"purl": "pkg:generic/x"},
                "1 validation error for Component\nname\n  Field required",
            ),
            (
                {"name": "x"},
                "1 validation error for Component\npurl\n  Field required",
            ),
            (
                {
                    "type": "gomod",
                    "name": "github.com/org/cool-dep",
                    "purl": "pkg:golang/github.com/org/cool-dep",
                },
                "1 validation error for Component\ntype\n  Input should be 'library'",
            ),
        ],
    )
    def test_invalid_components(self, input_data: dict[str, str], expect_error: str) -> None:
        with pytest.raises(pydantic.ValidationError, match=expect_error):
            Component(**input_data)

    @pytest.mark.parametrize(
        "input_properties, expected_properties",
        [
            (
                [],
                [FOUND_BY_APP_PROPERTY],
            ),
            (
                [Property(name="cachi2:missing_hash:in_file", value="go.sum")],
                [
                    Property(name="cachi2:missing_hash:in_file", value="go.sum"),
                    FOUND_BY_APP_PROPERTY,
                ],
            ),
            (
                [FOUND_BY_APP_PROPERTY],
                [FOUND_BY_APP_PROPERTY],
            ),
        ],
    )
    def test_default_property(
        self, input_properties: list[Property], expected_properties: list[Property]
    ) -> None:
        assert (
            Component(name="foo", purl="pkg:generic/foo", properties=input_properties).properties
            == expected_properties
        )


class TestSPDXPackage:
    @pytest.mark.parametrize(
        "input_data, expected_data",
        [
            (
                {
                    "SPDXID": "SPDXRef-Package-mypkg-4035f88e9e6be21e9717c7170c40cbdce83f591dc862c5a8e4ac21b5636fa875",
                    "name": "mypkg",
                    "externalRefs": [
                        {
                            "referenceLocator": "pkg:generic/mypkg",
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceType": "purl",
                        }
                    ],
                },
                SPDXPackage(
                    SPDXID="SPDXRef-Package-mypkg-4035f88e9e6be21e9717c7170c40cbdce83f591dc862c5a8e4ac21b5636fa875",
                    name="mypkg",
                    externalRefs=[
                        SPDXPackageExternalRefPackageManagerPURL(
                            referenceLocator="pkg:generic/mypkg",
                            referenceCategory="PACKAGE-MANAGER",
                            referenceType="purl",
                        ),
                    ],
                ),
            ),
            (
                {
                    "name": "mypkg",
                    "versionInfo": "1.0.0",
                    "externalRefs": [
                        {
                            "referenceLocator": "pkg:generic/mypkg@1.0.0",
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceType": "purl",
                        }
                    ],
                },
                SPDXPackage(
                    SPDXID="SPDXRef-Package-mypkg-1.0.0-ded235cb82fb6d084178a362048a549edb6586fd5cd5f84c7afbd919789b801d",
                    name="mypkg",
                    versionInfo="1.0.0",
                    externalRefs=[
                        SPDXPackageExternalRefPackageManagerPURL(
                            referenceLocator="pkg:generic/mypkg@1.0.0",
                            referenceCategory="PACKAGE-MANAGER",
                            referenceType="purl",
                        ),
                    ],
                ),
            ),
            (
                {
                    "name": "mypkg",
                    "versionInfo": "random-version-string",
                    "externalRefs": [
                        {
                            "referenceLocator": "pkg:generic/mypkg",
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceType": "purl",
                        }
                    ],
                },
                SPDXPackage(
                    SPDXID="SPDXRef-Package-mypkg-random-version-string-5c73e2936cdb76c672fbee4c7a357695b052c97bcddacc5fb886f82bc78098d4",
                    name="mypkg",
                    versionInfo="random-version-string",
                    externalRefs=[
                        SPDXPackageExternalRefPackageManagerPURL(
                            referenceLocator="pkg:generic/mypkg",
                            referenceCategory="PACKAGE-MANAGER",
                            referenceType="purl",
                        )
                    ],
                ),
            ),
            (
                {
                    "name": "mypkg",
                    "externalRefs": [
                        {
                            "referenceLocator": "pkg:generic/mypkg@1.0.0",
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceType": "purl",
                        }
                    ],
                    "versionInfo": "1.0.0",
                    "path": ".",
                    "dependencies": [],
                },
                SPDXPackage(
                    SPDXID="SPDXRef-Package-mypkg-1.0.0-ded235cb82fb6d084178a362048a549edb6586fd5cd5f84c7afbd919789b801d",
                    name="mypkg",
                    versionInfo="1.0.0",
                    externalRefs=[
                        SPDXPackageExternalRefPackageManagerPURL(
                            referenceLocator="pkg:generic/mypkg@1.0.0",
                            referenceCategory="PACKAGE-MANAGER",
                            referenceType="purl",
                        )
                    ],
                ),
            ),
        ],
    )
    def test_construct_from_package_dict(
        self, input_data: dict[str, str], expected_data: SPDXPackage
    ) -> None:
        spdx_package = SPDXPackage.from_package_dict(input_data)
        assert spdx_package == expected_data

    @pytest.mark.parametrize(
        "input_data, expect_error",
        [
            (
                {"versionInfo": "some-version"},
                "1 validation error for SPDXPackage\nname\n  Field required",
            )
        ],
    )
    def test_invalid_packages(self, input_data: dict[str, str], expect_error: str) -> None:
        with pytest.raises(pydantic.ValidationError, match=expect_error):
            SPDXPackage(**input_data)

    @pytest.mark.parametrize(
        "category,type,locator,valid",
        [
            ("PACKAGE-MANAGER", "maven-central", "org.apache.tomcat:tomcat:9.0.0.M4", False),
            ("PACKAGE-MANAGER", "npm", "http-server@0.3.0", False),
            ("PACKAGE-MANAGER", "nuget", "Microsoft.AspNet.MVC/5.0.0", False),
            ("PACKAGE-MANAGER", "bower", "modernizr#2.6.2", False),
            ("PERSISTENT-ID", "swh", "swh:1:cnt:94a9ed024d3859793618152ea559a168bbcbb5e2", False),
            (
                "PERSISTENT-ID",
                "gitoid",
                "gitoid:blob:sha1:261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64",
                False,
            ),
            (
                "OTHER",
                "some-id",
                "anythingcangohere",
                False,
            ),
        ],
    )
    def test_package_unsupported_external_ref(
        self, category: str, type: str, locator: str, valid: str
    ) -> None:
        """Fails on unsupported category and type combinations.

        Only PACKAGE-MANAGER and SECURITY categories with type purl is supported.
        """
        adapter: pydantic.TypeAdapter = pydantic.TypeAdapter(SPDXPackageExternalRefType)
        with pytest.raises(pydantic.ValidationError):
            adapter.validate_python(
                dict(referenceCategory=category, referenceLocator=locator, referenceType=type)
            )


@mock.patch("cachi2.core.models.sbom.spdx_now", return_value=SPDX_EPOCH_STRFTIME)
class TestSbom:
    def test_sort_and_dedupe_components(self, mock_spdx_now: str) -> None:
        sbom = Sbom(
            components=[
                {
                    "name": "github.com/org/B",
                    "version": "v1.0.0",
                    "purl": "pkg:golang/github.com/org/B@v1.0.0",
                },
                {
                    "name": "github.com/org/A",
                    "version": "v1.1.0",
                    "purl": "pkg:golang/github.com/org/A@v1.1.0",
                },
                {
                    "name": "github.com/org/A",
                    "version": "v1.0.0",
                    "purl": "pkg:golang/github.com/org/A@v1.0.0",
                },
                {
                    "name": "github.com/org/A",
                    "version": "v1.0.0",
                    "purl": "pkg:golang/github.com/org/A@v1.0.0",
                },
                {
                    "name": "github.com/org/B",
                    "version": "v1.0.0",
                    "purl": "pkg:golang/github.com/org/B@v1.0.0",
                },
                {"name": "fmt", "version": None, "purl": "pkg:golang/fmt"},
                {"name": "fmt", "version": None, "purl": "pkg:golang/fmt"},
                {"name": "bytes", "version": None, "purl": "pkg:golang/bytes"},
            ],
        )
        assert sbom.components == [
            Component(name="bytes", purl="pkg:golang/bytes"),
            Component(name="fmt", purl="pkg:golang/fmt"),
            Component(
                name="github.com/org/A", purl="pkg:golang/github.com/org/A@v1.0.0", version="v1.0.0"
            ),
            Component(
                name="github.com/org/A", purl="pkg:golang/github.com/org/A@v1.1.0", version="v1.1.0"
            ),
            Component(
                name="github.com/org/B", purl="pkg:golang/github.com/org/B@v1.0.0", version="v1.0.0"
            ),
        ]

    # Handles generic PM use-case.
    def test_to_spdx_when_a_file_is_present(self, mock_spdx_now: str) -> None:
        sbom = Sbom(
            components=[
                {
                    "externalReferences": [
                        {
                            "type": "distribution",
                            "url": "https://github.com/cachito-testing/cachi2-generic/archive/refs/tags/v2.0.0.zip",
                        },
                    ],
                    "name": "archive.zip",
                    "properties": [{"name": "cachi2:found_by", "value": "cachi2"}],
                    "purl": "pkg:generic/archive.zip?checksum=sha256:386428a82f37345fa24b74068e0e79f4c1f2ff38d4f5c106ea14de4a2926e584&download_url=https://github.com/cachito-testing/cachi2-generic/archive/refs/tags/v2.0.0.zip",
                    "type": "file",
                },
            ],
        )

        spdx_sbom = sbom.to_spdx("NOASSERTION")

        assert spdx_sbom.packages == [
            SPDXPackage(
                SPDXID="SPDXRef-DocumentRoot-File-",
                name="",
                versionInfo="",
                externalRefs=[],
                annotations=[],
                downloadLocation="NOASSERTION",
            ),
            SPDXPackage(
                SPDXID="SPDXRef-Package-archive.zip-965cfdf16e9275d1b3b562dee596de0474cdc751ba4c30715cfc3934fab3b300",
                name="archive.zip",
                versionInfo=None,
                externalRefs=[
                    SPDXPackageExternalRefPackageManagerPURL(
                        referenceLocator="pkg:generic/archive.zip?checksum=sha256:386428a82f37345fa24b74068e0e79f4c1f2ff38d4f5c106ea14de4a2926e584&download_url=https://github.com/cachito-testing/cachi2-generic/archive/refs/tags/v2.0.0.zip",
                        referenceType="purl",
                        referenceCategory="PACKAGE-MANAGER",
                    ),
                ],
                annotations=[
                    SPDXPackageAnnotation(
                        annotator="Tool: cachi2:jsonencoded",
                        annotationDate=SPDX_EPOCH_STRFTIME,
                        annotationType="OTHER",
                        comment='{"name": "cachi2:found_by", "value": "cachi2"}',
                    ),
                ],
                downloadLocation="NOASSERTION",
            ),
        ]

    def test_to_spdx(self, mock_spdx_now: str) -> None:
        sbom = Sbom(
            components=[
                {
                    "name": "spdx-expression-parse",
                    "version": "v1.0.0",
                    "purl": "pkg:npm/spdx-expression-parse@1.0.0",
                },
                {
                    "name": "github.com/org/A",
                    "version": "v1.0.0",
                    "purl": "pkg:golang/github.com/org/A@v1.0.0",
                },
                {
                    "name": "github.com/org/A",
                    "version": "v1.1.0",
                    "purl": "pkg:golang/github.com/org/A@v1.1.0",
                },
            ],
        )
        sbom.components[0].properties.extend(
            [
                Property(name="cdx:npm:package:bundled", value="true"),
            ]
        )
        spdx_sbom = sbom.to_spdx("NOASSERTION")

        assert spdx_sbom.packages == [
            SPDXPackage(
                SPDXID="SPDXRef-DocumentRoot-File-",
                name="",
                versionInfo="",
                externalRefs=[],
                annotations=[],
            ),
            SPDXPackage(
                SPDXID="SPDXRef-Package-github.com-org-A-v1.0.0-8090f86e9eb851549de5f8391948c1df6a2c8976bfa33c3cbd82e917564ac94f",
                name="github.com/org/A",
                versionInfo="v1.0.0",
                externalRefs=[
                    SPDXPackageExternalRefPackageManagerPURL(
                        referenceCategory="PACKAGE-MANAGER",
                        referenceLocator="pkg:golang/github.com/org/A@v1.0.0",
                        referenceType="purl",
                    )
                ],
                annotations=[
                    SPDXPackageAnnotation(
                        annotator="Tool: cachi2:jsonencoded",
                        annotationDate=SPDX_EPOCH_STRFTIME,
                        annotationType="OTHER",
                        comment=json.dumps({"name": "cachi2:found_by", "value": "cachi2"}),
                    ),
                    SPDXPackageAnnotation(
                        annotator="Tool: cachi2:jsonencoded",
                        annotationDate=SPDX_EPOCH_STRFTIME,
                        annotationType="OTHER",
                        comment=json.dumps({"name": "cdx:npm:package:bundled", "value": "true"}),
                    ),
                ],
            ),
            SPDXPackage(
                SPDXID="SPDXRef-Package-github.com-org-A-v1.1.0-898f4d436d82296d12247741855acc48a1f80639d2418e556268f30ae2336303",
                name="github.com/org/A",
                versionInfo="v1.1.0",
                externalRefs=[
                    SPDXPackageExternalRefPackageManagerPURL(
                        referenceCategory="PACKAGE-MANAGER",
                        referenceLocator="pkg:golang/github.com/org/A@v1.1.0",
                        referenceType="purl",
                    )
                ],
                annotations=[
                    SPDXPackageAnnotation(
                        annotator="Tool: cachi2:jsonencoded",
                        annotationDate=SPDX_EPOCH_STRFTIME,
                        annotationType="OTHER",
                        comment=json.dumps({"name": "cachi2:found_by", "value": "cachi2"}),
                    ),
                ],
            ),
            SPDXPackage(
                SPDXID="SPDXRef-Package-spdx-expression-parse-v1.0.0-2d5c537d20208409089cf9c7ae9398b7105beef1f883cfc4c0b1f804bca86b02",
                name="spdx-expression-parse",
                versionInfo="v1.0.0",
                externalRefs=[
                    SPDXPackageExternalRefPackageManagerPURL(
                        referenceCategory="PACKAGE-MANAGER",
                        referenceLocator="pkg:npm/spdx-expression-parse@1.0.0",
                        referenceType="purl",
                    )
                ],
                annotations=[
                    SPDXPackageAnnotation(
                        annotator="Tool: cachi2:jsonencoded",
                        annotationDate=SPDX_EPOCH_STRFTIME,
                        annotationType="OTHER",
                        comment=json.dumps({"name": "cachi2:found_by", "value": "cachi2"}),
                    ),
                ],
            ),
        ]
        assert spdx_sbom.relationships == [
            SPDXRelation(
                spdxElementId="SPDXRef-DOCUMENT",
                comment="",
                relatedSpdxElement="SPDXRef-DocumentRoot-File-",
                relationshipType="DESCRIBES",
            ),
            SPDXRelation(
                spdxElementId="SPDXRef-DocumentRoot-File-",
                comment="",
                relatedSpdxElement="SPDXRef-Package-github.com-org-A-v1.0.0-8090f86e9eb851549de5f8391948c1df6a2c8976bfa33c3cbd82e917564ac94f",
                relationshipType="CONTAINS",
            ),
            SPDXRelation(
                spdxElementId="SPDXRef-DocumentRoot-File-",
                comment="",
                relatedSpdxElement="SPDXRef-Package-github.com-org-A-v1.1.0-898f4d436d82296d12247741855acc48a1f80639d2418e556268f30ae2336303",
                relationshipType="CONTAINS",
            ),
            SPDXRelation(
                spdxElementId="SPDXRef-DocumentRoot-File-",
                comment="",
                relatedSpdxElement="SPDXRef-Package-spdx-expression-parse-v1.0.0-2d5c537d20208409089cf9c7ae9398b7105beef1f883cfc4c0b1f804bca86b02",
                relationshipType="CONTAINS",
            ),
        ]

    def test_cyclonedx_sbom_can_be_converted_to_spdx_and_back_without_loosing_any_data(
        self, mock_spdx_now: str
    ) -> None:
        sbom = Sbom(
            components=[
                {
                    "name": "spdx-expression-parse",
                    "version": "v1.0.0",
                    "purl": "pkg:npm/spdx-expression-parse@1.0.0",
                },
                {
                    "name": "github.com/org/A",
                    "version": "v1.0.0",
                    "purl": "pkg:golang/github.com/org/A@v1.0.0",
                },
                {
                    "name": "github.com/org/A",
                    "version": "v1.1.0",
                    "purl": "pkg:golang/github.com/org/A@v1.1.0",
                },
            ],
        )
        sbom.components[0].properties.extend(
            [
                Property(name="cdx:npm:package:bundled", value="true"),
            ]
        )
        cyclonedx_sbom = sbom.to_spdx("NOASSERTION").to_cyclonedx()
        assert cyclonedx_sbom == sbom


# Some partially constructed objects to streamline test cases definitions.
STOCK_ANNOTATION = {
    "annotator": "Tool: cachi2:jsonencoded",
    "annotationDate": SPDX_EPOCH_STRFTIME,
    "annotationType": "OTHER",
    "comment": '{"name": "cachi2:found_by", "value": "cachi2"}',
}

BLANK_SPDX_SBOM = SPDXSbom(
    SPDXID="SPDXRef-DOCUMENT",
    documentNamespace="NOASSERTION",
    creationInfo={
        "creators": ["Tool: cachi2", "Organization: cachi2"],
        "created": SPDX_EPOCH_STRFTIME,
    },
)

DEFAULT_ROOT_PACKAGE = SPDXPackage(
    **{
        "SPDXID": "SPDXRef-DocumentRoot-File-",
        "name": "",
        "versionInfo": "",
        "externalRefs": [],
        "annotations": [],
    }
)

DEFAULT_ROOT_RELATION = SPDXRelation(
    **{
        "spdxElementId": "SPDXRef-DOCUMENT",
        "comment": "",
        "relatedSpdxElement": "SPDXRef-DocumentRoot-File-",
        "relationshipType": "DESCRIBES",
    }
)


def _gen_ref(locator: str) -> dict:
    return {
        "referenceCategory": "PACKAGE-MANAGER",
        "referenceLocator": locator,
        "referenceType": "purl",
    }


def _root_contains(spdxid: str) -> SPDXRelation:
    return SPDXRelation(
        **{
            "spdxElementId": "SPDXRef-DocumentRoot-File-",
            "comment": "",
            "relatedSpdxElement": spdxid,
            "relationshipType": "CONTAINS",
        }
    )


@mock.patch("cachi2.core.models.sbom.spdx_now", return_value=SPDX_EPOCH_STRFTIME)
class TestSPDXSbom:
    def test_sort_and_dedupe_packages(self, mock_spdx_now: str) -> None:
        sbom = SPDXSbom(
            creationInfo={"creators": [], "created": SPDX_EPOCH_STRFTIME},
            documentNamespace="NOASSERTION",
            packages=[
                {
                    "name": "github.com/org/B",
                    "versionInfo": "v1.0.0",
                    "downloadLocation": "NOASSERTION",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/B@v1.0.0",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "github.com/org/A",
                    "versionInfo": "v1.1.0",
                    "downloadLocation": "NOASSERTION",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/A@v1.1.0?repository_id=R1",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "github.com/org/A",
                    "versionInfo": "v1.1.0",
                    "downloadLocation": "NOASSERTION",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/A@v1.1.0?repository_id=R2",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "github.com/org/A",
                    "versionInfo": "v1.0.0",
                    "downloadLocation": "NOASSERTION",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/A@v1.0.0",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "github.com/org/A",
                    "versionInfo": "v1.0.0",
                    "downloadLocation": "NOASSERTION",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/A@v1.0.0",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "github.com/org/B",
                    "versionInfo": "v1.0.0",
                    "downloadLocation": "NOASSERTION",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/B@v1.0.0",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "fmt",
                    "versionInfo": None,
                    "downloadLocation": "NOASSERTION",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/fmt",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "bytes",
                    "versionInfo": None,
                    "downloadLocation": "NOASSERTION",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/bytes",
                            "referenceType": "purl",
                        }
                    ],
                },
            ],
        )
        expected_packages = [
            SPDXPackage(
                name="bytes",
                externalRefs=[
                    SPDXPackageExternalRefPackageManagerPURL(
                        referenceCategory="PACKAGE-MANAGER",
                        referenceLocator="pkg:golang/bytes",
                        referenceType="purl",
                    )
                ],
            ),
            SPDXPackage(
                name="fmt",
                externalRefs=[
                    SPDXPackageExternalRefPackageManagerPURL(
                        referenceCategory="PACKAGE-MANAGER",
                        referenceLocator="pkg:golang/fmt",
                        referenceType="purl",
                    )
                ],
            ),
            SPDXPackage(
                name="github.com/org/A",
                versionInfo="v1.0.0",
                externalRefs=[
                    SPDXPackageExternalRefPackageManagerPURL(
                        referenceCategory="PACKAGE-MANAGER",
                        referenceLocator="pkg:golang/github.com/org/A@v1.0.0",
                        referenceType="purl",
                    )
                ],
            ),
            SPDXPackage(
                name="github.com/org/A",
                versionInfo="v1.1.0",
                externalRefs=[
                    SPDXPackageExternalRefPackageManagerPURL(
                        referenceCategory="PACKAGE-MANAGER",
                        referenceLocator="pkg:golang/github.com/org/A@v1.1.0?repository_id=R1",
                        referenceType="purl",
                    ),
                ],
            ),
            SPDXPackage(
                name="github.com/org/A",
                versionInfo="v1.1.0",
                externalRefs=[
                    SPDXPackageExternalRefPackageManagerPURL(
                        referenceCategory="PACKAGE-MANAGER",
                        referenceLocator="pkg:golang/github.com/org/A@v1.1.0?repository_id=R2",
                        referenceType="purl",
                    ),
                ],
            ),
            SPDXPackage(
                name="github.com/org/B",
                versionInfo="v1.0.0",
                externalRefs=[
                    SPDXPackageExternalRefPackageManagerPURL(
                        referenceCategory="PACKAGE-MANAGER",
                        referenceLocator="pkg:golang/github.com/org/B@v1.0.0",
                        referenceType="purl",
                    )
                ],
            ),
        ]
        assert len(sbom.packages) == len(expected_packages)
        assert sbom.packages == expected_packages

    def test_package_external_ref_invalid_reference_type_for_category(
        self, mock_spdx_now: str
    ) -> None:
        adapter: pydantic.TypeAdapter = pydantic.TypeAdapter(SPDXPackageExternalRefType)

        with pytest.raises(pydantic.ValidationError):
            adapter.validate_python(
                dict(
                    referenceCategory="SECURITY",
                    referenceLocator="pkg:golang/github.com/org/B@v1.0.0",
                    referenceType="purl",
                )
            )
        with pytest.raises(pydantic.ValidationError):
            adapter.validate_python(
                dict(
                    referenceCategory="PERSISTENT-ID",
                    referenceLocator="pkg:golang/github.com/org/B@v1.0.0",
                    referenceType="purl",
                )
            )
        with pytest.raises(pydantic.ValidationError):
            adapter.validate_python(
                dict(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="gitoid:blob:sha1:261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64",
                    referenceType="gitbom",
                )
            )

    def test_package_external_ref_invalid_reference(self, mock_spdx_now: str) -> None:
        adapter: pydantic.TypeAdapter = pydantic.TypeAdapter(SPDXPackageExternalRefType)
        with pytest.raises(
            pydantic.ValidationError,
        ):
            adapter.validate_python(
                dict(
                    referenceCategory="INVALID",
                    referenceLocator="pkg:golang/github.com/org/B@v1.0.0",
                    referenceType="purl",
                )
            )

    def test_to_cyclonedx(self, mock_spdx_now: str) -> None:
        sbom = SPDXSbom(
            documentNamespace="NOASSERTION",
            creationInfo={
                "creators": ["Tool: cachi2", "Organization: cachi2"],
                "created": SPDX_EPOCH_STRFTIME,
            },
            packages=[
                {
                    "name": "github.com/org/B",
                    "versionInfo": "v1.0.0",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/B@v1.0.0",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "github.com/org/A",
                    "versionInfo": "v1.1.0",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/A@v1.1.0?repository_id=R1",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "github.com/org/A",
                    "versionInfo": "v1.1.0",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/A@v1.1.0?repository_id=R2",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "github.com/org/A",
                    "versionInfo": "v1.0.0",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/A@v1.0.0",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "github.com/org/A",
                    "versionInfo": "v1.0.0",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/A@v1.0.0",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "github.com/org/B",
                    "versionInfo": "v1.0.0",
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/github.com/org/B@v1.0.0",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "fmt",
                    "versionInfo": None,
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/fmt",
                            "referenceType": "purl",
                        }
                    ],
                },
                {
                    "name": "bytes",
                    "versionInfo": None,
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE-MANAGER",
                            "referenceLocator": "pkg:golang/bytes",
                            "referenceType": "purl",
                        }
                    ],
                },
            ],
        )
        cyclonedx_sbom = sbom.to_cyclonedx()
        assert cyclonedx_sbom == Sbom(
            components=[
                Component(name="bytes", purl="pkg:golang/bytes", version=None),
                Component(name="fmt", purl="pkg:golang/fmt", version=None),
                Component(
                    name="github.com/org/A",
                    purl="pkg:golang/github.com/org/A@v1.0.0",
                    version="v1.0.0",
                ),
                Component(
                    name="github.com/org/A",
                    purl="pkg:golang/github.com/org/A@v1.1.0?repository_id=R1",
                    version="v1.1.0",
                ),
                Component(
                    name="github.com/org/A",
                    purl="pkg:golang/github.com/org/A@v1.1.0?repository_id=R2",
                    version="v1.1.0",
                ),
                Component(
                    name="github.com/org/B",
                    purl="pkg:golang/github.com/org/B@v1.0.0",
                    version="v1.0.0",
                ),
            ],
            metadata=Metadata(
                tools=[Tool(vendor="cachi2", name="cachi2")],
            ),
        )

    # SPDX SBOM objects are very verbose and it is rather hard to tell the
    # difference between them at a glance. It is unavoidable when a SBOM is
    # produced, but it is possible to short-cut during construction time. This
    # test case attempts just that.  Visual markers are also added to assist in
    # locating individual test parameters.  Test cases contain progressively
    # more complex input SBOMs from the simplest possible to full original
    # example. Having smaller examples helps with narrowing down issues when a
    # need to update equivalence definition arises.
    @pytest.mark.parametrize(
        "original_sbom",
        (
            # Test case 1.
            BLANK_SPDX_SBOM.model_copy(
                update={
                    "packages": [
                        DEFAULT_ROOT_PACKAGE,
                        # from_package_dict handles hash creation, however the has is also
                        # needed when defining relationships.
                        SPDXPackage(
                            **{
                                "name": "github.com/org/B",
                                "SPDXID": "SPDXRef-Package-github.com-org-B-v1.0.0-f75a590094f92d64111235b9ae298c34b9acd126f8fc6263b7924810bfe6470c",
                                "versionInfo": "v1.0.0",
                                "externalRefs": [
                                    _gen_ref(locator="pkg:golang/github.com/org/B@v1.0.0")
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                    ],
                    "relationships": [
                        DEFAULT_ROOT_RELATION,
                        _root_contains(
                            "SPDXRef-Package-github.com-org-B-v1.0.0-f75a590094f92d64111235b9ae298c34b9acd126f8fc6263b7924810bfe6470c"
                        ),
                    ],
                }
            ),
            # Test case 2.
            BLANK_SPDX_SBOM.model_copy(
                update={
                    "packages": [
                        DEFAULT_ROOT_PACKAGE,
                        SPDXPackage(
                            **{
                                "name": "github.com/org/A",
                                "SPDXID": "SPDXRef-Package-github.com-org-A-v1.1.0-e4e45c4dc4bfb505f298188b3156fcee718b13e618a73a270401f5a3b77e49b3",
                                "versionInfo": "v1.1.0",
                                "externalRefs": [
                                    _gen_ref(
                                        locator="pkg:golang/github.com/org/A@v1.1.0?repository_id=R1"
                                    )
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "github.com/org/B",
                                "SPDXID": "SPDXRef-Package-github.com-org-B-v1.0.0-f75a590094f92d64111235b9ae298c34b9acd126f8fc6263b7924810bfe6470c",
                                "versionInfo": "v1.0.0",
                                "externalRefs": [
                                    _gen_ref(locator="pkg:golang/github.com/org/B@v1.0.0")
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                    ],
                    "relationships": [
                        DEFAULT_ROOT_RELATION,
                        # NOTE: for the time being test object has to be constructed in order.
                        _root_contains(
                            "SPDXRef-Package-github.com-org-A-v1.1.0-e4e45c4dc4bfb505f298188b3156fcee718b13e618a73a270401f5a3b77e49b3"
                        ),
                        _root_contains(
                            "SPDXRef-Package-github.com-org-B-v1.0.0-f75a590094f92d64111235b9ae298c34b9acd126f8fc6263b7924810bfe6470c"
                        ),
                    ],
                }
            ),
            # Test case 3.
            BLANK_SPDX_SBOM.model_copy(
                update={
                    "packages": [
                        DEFAULT_ROOT_PACKAGE,
                        SPDXPackage(
                            **{
                                "name": "github.com/org/A",
                                "SPDXID": "SPDXRef-Package-github.com-org-A-v1.1.0-e4e45c4dc4bfb505f298188b3156fcee718b13e618a73a270401f5a3b77e49b3",
                                "versionInfo": "v1.1.0",
                                "externalRefs": [
                                    _gen_ref(
                                        locator="pkg:golang/github.com/org/A@v1.1.0?repository_id=R1"
                                    )
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "github.com/org/A",
                                # NOTE: now a full purl goes into package hash
                                # computation procedure:
                                "SPDXID": "SPDXRef-Package-github.com-org-A-v1.1.0-e92e3a95e71ca3b2ef7bd075547593856dec87255626aa3db90a05dcde1b05ec",
                                "versionInfo": "v1.1.0",
                                "externalRefs": [
                                    _gen_ref(
                                        locator="pkg:golang/github.com/org/A@v1.1.0?repository_id=R2"
                                    )
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "github.com/org/B",
                                "SPDXID": "SPDXRef-Package-github.com-org-B-v1.0.0-f75a590094f92d64111235b9ae298c34b9acd126f8fc6263b7924810bfe6470c",
                                "versionInfo": "v1.0.0",
                                "externalRefs": [
                                    _gen_ref(locator="pkg:golang/github.com/org/B@v1.0.0")
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                    ],
                    "relationships": [
                        DEFAULT_ROOT_RELATION,
                        _root_contains(
                            "SPDXRef-Package-github.com-org-A-v1.1.0-e4e45c4dc4bfb505f298188b3156fcee718b13e618a73a270401f5a3b77e49b3"
                        ),
                        _root_contains(
                            "SPDXRef-Package-github.com-org-A-v1.1.0-e92e3a95e71ca3b2ef7bd075547593856dec87255626aa3db90a05dcde1b05ec"
                        ),
                        _root_contains(
                            "SPDXRef-Package-github.com-org-B-v1.0.0-f75a590094f92d64111235b9ae298c34b9acd126f8fc6263b7924810bfe6470c"
                        ),
                    ],
                }
            ),
            # Test case 4.
            BLANK_SPDX_SBOM.model_copy(
                update={
                    "packages": [
                        DEFAULT_ROOT_PACKAGE,
                        SPDXPackage(
                            **{
                                "name": "github.com/org/A",
                                "SPDXID": "SPDXRef-Package-github.com-org-A-v1.0.0-8090f86e9eb851549de5f8391948c1df6a2c8976bfa33c3cbd82e917564ac94f",
                                "versionInfo": "v1.0.0",
                                "externalRefs": [_gen_ref("pkg:golang/github.com/org/A@v1.0.0")],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "github.com/org/A",
                                "SPDXID": "SPDXRef-Package-github.com-org-A-v1.1.0-e4e45c4dc4bfb505f298188b3156fcee718b13e618a73a270401f5a3b77e49b3",
                                "versionInfo": "v1.1.0",
                                "externalRefs": [
                                    _gen_ref(
                                        locator="pkg:golang/github.com/org/A@v1.1.0?repository_id=R1"
                                    )
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "github.com/org/A",
                                "SPDXID": "SPDXRef-Package-github.com-org-A-v1.1.0-e92e3a95e71ca3b2ef7bd075547593856dec87255626aa3db90a05dcde1b05ec",
                                "versionInfo": "v1.1.0",
                                "externalRefs": [
                                    _gen_ref(
                                        locator="pkg:golang/github.com/org/A@v1.1.0?repository_id=R2"
                                    )
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "github.com/org/B",
                                "SPDXID": "SPDXRef-Package-github.com-org-B-v1.0.0-f75a590094f92d64111235b9ae298c34b9acd126f8fc6263b7924810bfe6470c",
                                "versionInfo": "v1.0.0",
                                "externalRefs": [
                                    _gen_ref(locator="pkg:golang/github.com/org/B@v1.0.0")
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                    ],
                    "relationships": [
                        DEFAULT_ROOT_RELATION,
                        _root_contains(
                            "SPDXRef-Package-github.com-org-A-v1.0.0-8090f86e9eb851549de5f8391948c1df6a2c8976bfa33c3cbd82e917564ac94f"
                        ),
                        _root_contains(
                            "SPDXRef-Package-github.com-org-A-v1.1.0-e4e45c4dc4bfb505f298188b3156fcee718b13e618a73a270401f5a3b77e49b3"
                        ),
                        _root_contains(
                            "SPDXRef-Package-github.com-org-A-v1.1.0-e92e3a95e71ca3b2ef7bd075547593856dec87255626aa3db90a05dcde1b05ec"
                        ),
                        _root_contains(
                            "SPDXRef-Package-github.com-org-B-v1.0.0-f75a590094f92d64111235b9ae298c34b9acd126f8fc6263b7924810bfe6470c"
                        ),
                    ],
                }
            ),
            # Test case 5.
            BLANK_SPDX_SBOM.model_copy(
                # NOTE: model_copy **will not** deduplicate data for you (by design).
                update={
                    "packages": [
                        DEFAULT_ROOT_PACKAGE,
                        SPDXPackage(
                            **{
                                "name": "bytes",
                                "SPDXID": "SPDXRef-Package-bytes-159a73f12ce40d92d01ba213c0ec5b442a301c842533acb3487aed9454ae17e7",
                                "versionInfo": "",
                                "externalRefs": [_gen_ref("pkg:golang/bytes")],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "fmt",
                                "SPDXID": "SPDXRef-Package-fmt-7e4d2ed76d4ea914ece19cdfb657d52dfe5c22193e31c8141497806571490439",
                                "versionInfo": "",
                                "externalRefs": [_gen_ref("pkg:golang/fmt")],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "github.com/org/A",
                                "SPDXID": "SPDXRef-Package-github.com-org-A-v1.0.0-8090f86e9eb851549de5f8391948c1df6a2c8976bfa33c3cbd82e917564ac94f",
                                "versionInfo": "v1.0.0",
                                "externalRefs": [_gen_ref("pkg:golang/github.com/org/A@v1.0.0")],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "github.com/org/A",
                                "SPDXID": "SPDXRef-Package-github.com-org-A-v1.1.0-e4e45c4dc4bfb505f298188b3156fcee718b13e618a73a270401f5a3b77e49b3",
                                "versionInfo": "v1.1.0",
                                "externalRefs": [
                                    _gen_ref(
                                        locator="pkg:golang/github.com/org/A@v1.1.0?repository_id=R1"
                                    )
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "github.com/org/A",
                                "SPDXID": "SPDXRef-Package-github.com-org-A-v1.1.0-e92e3a95e71ca3b2ef7bd075547593856dec87255626aa3db90a05dcde1b05ec",
                                "versionInfo": "v1.1.0",
                                "externalRefs": [
                                    _gen_ref(
                                        locator="pkg:golang/github.com/org/A@v1.1.0?repository_id=R2"
                                    )
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                        SPDXPackage(
                            **{
                                "name": "github.com/org/B",
                                "SPDXID": "SPDXRef-Package-github.com-org-B-v1.0.0-f75a590094f92d64111235b9ae298c34b9acd126f8fc6263b7924810bfe6470c",
                                "versionInfo": "v1.0.0",
                                "externalRefs": [
                                    _gen_ref(locator="pkg:golang/github.com/org/B@v1.0.0")
                                ],
                                "annotations": [STOCK_ANNOTATION],
                            }
                        ),
                    ],
                    "relationships": [
                        DEFAULT_ROOT_RELATION,
                        _root_contains(
                            "SPDXRef-Package-bytes-159a73f12ce40d92d01ba213c0ec5b442a301c842533acb3487aed9454ae17e7"
                        ),
                        _root_contains(
                            "SPDXRef-Package-fmt-7e4d2ed76d4ea914ece19cdfb657d52dfe5c22193e31c8141497806571490439"
                        ),
                        _root_contains(
                            "SPDXRef-Package-github.com-org-A-v1.0.0-8090f86e9eb851549de5f8391948c1df6a2c8976bfa33c3cbd82e917564ac94f"
                        ),
                        _root_contains(
                            "SPDXRef-Package-github.com-org-A-v1.1.0-e4e45c4dc4bfb505f298188b3156fcee718b13e618a73a270401f5a3b77e49b3"
                        ),
                        _root_contains(
                            "SPDXRef-Package-github.com-org-A-v1.1.0-e92e3a95e71ca3b2ef7bd075547593856dec87255626aa3db90a05dcde1b05ec"
                        ),
                        _root_contains(
                            "SPDXRef-Package-github.com-org-B-v1.0.0-f75a590094f92d64111235b9ae298c34b9acd126f8fc6263b7924810bfe6470c"
                        ),
                    ],
                },
            ),
        ),
    )
    def test_spdx_sbom_can_be_converted_to_cyclonedx_and_back_without_loosing_any_data(
        self, mock_spdx_now: str, original_sbom: SPDXSbom
    ) -> None:

        converted_sbom = original_sbom.to_cyclonedx().to_spdx("NOASSERTION")

        assert json.dumps(original_sbom.model_dump(), sort_keys=True, indent=4) == json.dumps(
            converted_sbom.model_dump(), sort_keys=True, indent=4
        )


def test_deduplicate_spdx_packages() -> None:
    packages = [
        SPDXPackage(
            name="github.com/org/A",
            versionInfo="v1.0.0",
            externalRefs=[
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/A@v1.0.0?repository_id=R1",
                    referenceType="purl",
                )
            ],
        ),
        SPDXPackage(
            name="github.com/org/A",
            versionInfo="v1.0.0",
            externalRefs=[
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/A@v1.0.0?repository_id=R1",
                    referenceType="purl",
                )
            ],
        ),
        SPDXPackage(
            name="github.com/org/A",
            versionInfo="v1.0.0",
            externalRefs=[
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/A@v1.0.0?repository_id=R1",
                    referenceType="purl",
                ),
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/A@v1.0.0?repository_id=R2",
                    referenceType="purl",
                ),
            ],
        ),
        SPDXPackage(
            name="github.com/org/B",
            versionInfo="v1.0.0",
            externalRefs=[
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/B@v1.0.0",
                    referenceType="purl",
                )
            ],
        ),
        SPDXPackage(
            name="github.com/org/B",
            versionInfo="v1.0.0",
            externalRefs=[
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/B@v1.0.0?repository_id=R1",
                    referenceType="purl",
                )
            ],
        ),
    ]
    expected_packages = [
        SPDXPackage(
            name="github.com/org/A",
            versionInfo="v1.0.0",
            externalRefs=[
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/A@v1.0.0?repository_id=R1",
                    referenceType="purl",
                )
            ],
        ),
        SPDXPackage(
            name="github.com/org/A",
            versionInfo="v1.0.0",
            externalRefs=[
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/A@v1.0.0?repository_id=R1",
                    referenceType="purl",
                ),
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/A@v1.0.0?repository_id=R2",
                    referenceType="purl",
                ),
            ],
        ),
        SPDXPackage(
            name="github.com/org/B",
            versionInfo="v1.0.0",
            externalRefs=[
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/B@v1.0.0",
                    referenceType="purl",
                )
            ],
        ),
        SPDXPackage(
            name="github.com/org/B",
            versionInfo="v1.0.0",
            externalRefs=[
                SPDXPackageExternalRefPackageManagerPURL(
                    referenceCategory="PACKAGE-MANAGER",
                    referenceLocator="pkg:golang/github.com/org/B@v1.0.0?repository_id=R1",
                    referenceType="purl",
                )
            ],
        ),
    ]

    deduped_packages = SPDXSbom.deduplicate_spdx_packages(packages)

    assert len(deduped_packages) == len(expected_packages)
    assert deduped_packages == expected_packages
