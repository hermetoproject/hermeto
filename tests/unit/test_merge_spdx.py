# SPDX-License-Identifier: GPL-3.0-only
from collections import defaultdict
from functools import reduce
from operator import add
from pathlib import Path
from typing import Any

import pytest

from hermeto.core.models.sbom import SPDXSbom


def _find_roots(sbom: SPDXSbom) -> list[str]:
    direct_rel_map = defaultdict(list)
    inverse_map = dict()
    for rel in sbom.relationships:
        spdx_id, related_spdx = rel.spdxElementId, rel.relatedSpdxElement
        direct_rel_map[spdx_id].append(related_spdx)
        inverse_map[related_spdx] = spdx_id
    unidirectionally_related_package = lambda p: inverse_map.get(p) == sbom.SPDXID
    roots = list(filter(unidirectionally_related_package, direct_rel_map))
    return roots


def _assert_merging_sboms_produces_correct_number_of_packages(
    merged_sbom: SPDXSbom,
    *sources: SPDXSbom,
) -> None:
    # Ignoring mypy because it is cheaper to rebind the name in a local utility.
    sources = set(sources)  # type: ignore
    unqie_packages_across_all_sources = set(sum([s.packages for s in sources], []))
    # Drop one root package per source, add 1 to account for one eventual root:
    expected_num_of_packages = len(unqie_packages_across_all_sources) - len(sources) + 1
    actual_num_of_packages = len(merged_sbom.packages)
    difference = expected_num_of_packages - actual_num_of_packages
    msg = (
        f"""Number of packages in input SBOMs and resulting SBOM do not add up:
        The new SBOM contains {abs(difference)} package{"s" if abs(difference) != 1 else ""} """
        f"""{"more" if difference < 0 else "less"} than the both input SBOMs."""
    )
    assert expected_num_of_packages == actual_num_of_packages, msg

    assert len(merged_sbom.relationships) == len(set(merged_sbom.relationships)), (
        "Relationships length mismatch"
    )


def _assert_merging_two_distinct_sboms_produces_correct_number_of_packages(
    sbom_left: SPDXSbom,
    sbom_right: SPDXSbom,
    merged_sbom: SPDXSbom,
) -> None:
    # Note the 'distinct' part: sbom_left and sbom_right _do not_ intersect.
    # You should never verify intersecting SBOMs with this function.
    # -1 for one of the roots that must go.
    expected_num_of_packages = len(sbom_left.packages) + len(sbom_right.packages) - 1
    actual_num_of_packages = len(merged_sbom.packages)
    difference = expected_num_of_packages - actual_num_of_packages
    msg = (
        f"""
        Number of packages in input SBOMs and resulting SBOM do not add up:
        The new SBOM contains {abs(difference)} package{"s" if abs(difference) != 1 else ""} """
        f"""{"more" if difference < 0 else "less"} than the both input SBOMs."""
    )
    assert expected_num_of_packages == actual_num_of_packages, msg


def _assert_root_was_inherited_from_left_sbom(
    merged_sbom: SPDXSbom,
    sbom_left: SPDXSbom,
) -> None:
    msg = f"""Root mismatch!
        Expected: {sbom_left.root_id}
        Got: {merged_sbom.root_id}
    """
    assert merged_sbom.root_id == sbom_left.root_id, msg


def _assert_there_is_only_one_root(merged_sbom: SPDXSbom) -> None:
    assert len(_find_roots(merged_sbom)) == 1, f"Found several roots in {merged_sbom}"


def _assert_all_relationships_are_within_the_document(for_sbom: SPDXSbom) -> None:
    external_rels = []
    package_names = [p.SPDXID for p in for_sbom.packages]
    known_entities = package_names

    for r in for_sbom.relationships:
        if r.relatedSpdxElement not in known_entities:
            external_rels.append(r)
    if external_rels:
        assert False, f"Found relations that lead outside of a document: {external_rels}"


def _assert_root_is_present(sbom: SPDXSbom) -> None:
    # DocumentRoot can be File or Directory:
    fail_msg = "Document root is missing"
    root_pfx = "SPDXRef-DocumentRoot-"
    is_root = lambda p: p.SPDXID is not None and p.SPDXID.startswith(root_pfx)
    assert any(is_root(p) for p in sbom.packages), fail_msg


def _assert_no_relation_is_missing(sbom: SPDXSbom) -> None:
    # I.e. ther are at least as many relations as packages
    # (each package will relate to at least one other package and that would be
    # thr root document).
    assert len(sbom.relationships) == len(set(sbom.relationships)), (
        "There are duplicate relationships"
    )
    fail_msg = (
        "Some packages are not having relationships: not enough relationships for all packages"
    )
    assert len(sbom.packages) <= len(sbom.relationships), fail_msg


def _assert_no_relationship_points_out(sbom: SPDXSbom) -> None:
    packages = set(p.SPDXID for p in sbom.packages)
    assert all(r.relatedSpdxElement in packages for r in sbom.relationships), (
        "Found stray relationship(s)"
    )


def _assert_no_unrelated_packages(sbom: SPDXSbom) -> None:
    ids_related_to = set(r.relatedSpdxElement for r in sbom.relationships)
    assert all(p.SPDXID in ids_related_to for p in sbom.packages), "Unrelated packages detecetd"


def _assert_sbom_is_well_formed(sbom: SPDXSbom) -> None:
    _assert_root_is_present(sbom)
    _assert_no_relation_is_missing(sbom)
    _assert_no_relationship_points_out(sbom)
    _assert_no_unrelated_packages(sbom)


def _assert_no_relationship_is_duplicated(sbom: SPDXSbom) -> None:
    have_relationships = len(sbom.relationships)
    should_have_relationships = len(set(sbom.relationships))
    fail_msg = (
        "Relationships duplication detected: have "
        f"{have_relationships - should_have_relationships} relationships more than expected."
    )
    assert have_relationships == should_have_relationships, fail_msg


# Data for this test was generated with syft like this:
# For a directory:
#  $ ./syft dir:experiments/ -o spdx-json > experiments.json
#  $ jq < experiments.json > experiments.pretty.json
#  $ ls experiments
#  $ ash-0.3.8-20.el4_7.1.x86_64.rpm
# For a container image (sha256:4048db5d36726e313ab8f7ffccf2362a34cba69e4cdd49119713483a68641fce):
#  $ ./syft alpine -o spdx-json > alpine.json
#  $ jq < alpine.json > alpine.pretty.json
# I used syft v 0.100.0 to be consistent with what I assumed to be correct test data.
# I have also removed 'files' sections of all Syft-generated SBOMs to be consistent with
# our subset of SPDX and corresponding relations.
@pytest.mark.parametrize(
    "sbom_main,sbom_other",
    [
        [
            "./tests/unit/data/sboms/something.simple0.100.0.spdx.pretty.json",
            "./tests/unit/data/sboms/something.more.simple.0.100.0.spdx.pretty.json",
        ],
        [
            "./tests/unit/data/sboms/something.more.simple.0.100.0.spdx.pretty.json",
            "./tests/unit/data/sboms/something.simple0.100.0.spdx.pretty.json",
        ],
        [
            "./tests/unit/data/sboms/alpine.pretty.json",
            "./tests/unit/data/sboms/something.simple0.100.0.spdx.pretty.json",
        ],
        [
            "./tests/unit/data/sboms/something.simple0.100.0.spdx.pretty.json",
            "./tests/unit/data/sboms/alpine.pretty.json",
        ],
    ],
)
def test_merging_two_spdx_sboms_works_in_general_independent_of_order(
    sbom_main: Any,  # 'Any' is used to prevent mypy from having a fit over re-binding
    sbom_other: Any,  # 'Any' is used to prevent mypy from having a fit over re-binding
) -> None:
    # Re-using the names withing a short scope => mypy sad => must distract mypy.
    # Simple ignore won't work here so the need for a cast.
    sbom_main = SPDXSbom.from_file(Path(sbom_main))
    sbom_other = SPDXSbom.from_file(Path(sbom_other))

    merged_sbom = sbom_main + sbom_other

    _assert_all_relationships_are_within_the_document(for_sbom=merged_sbom)
    _assert_merging_two_distinct_sboms_produces_correct_number_of_packages(
        sbom_main, sbom_other, merged_sbom
    )
    _assert_merging_sboms_produces_correct_number_of_packages(merged_sbom, sbom_other, sbom_main)
    _assert_root_was_inherited_from_left_sbom(merged_sbom, sbom_main)
    _assert_there_is_only_one_root(merged_sbom)


@pytest.mark.parametrize(
    "sboms_to_merge",
    [
        pytest.param(
            (
                "./tests/unit/data/sboms/alpine.pretty.json",
                "./tests/unit/data/sboms/something.simple0.100.0.spdx.pretty.json",
                "./tests/unit/data/sboms/something.more.simple.0.100.0.spdx.pretty.json",
            ),
            id="three unique SBOMs",
        ),
    ],
)
def test_merging_several_spdx_sboms_works_in_general_independent_of_order(
    sboms_to_merge: list[Any],  # 'Any' is used to prevent mypy from having a fit over re-binding
) -> None:
    sboms_to_merge = [SPDXSbom.from_file(Path(s)) for s in sboms_to_merge]

    merged_sbom = reduce(add, sboms_to_merge)

    _assert_all_relationships_are_within_the_document(for_sbom=merged_sbom)
    _assert_merging_sboms_produces_correct_number_of_packages(merged_sbom, *sboms_to_merge)
    _assert_root_was_inherited_from_left_sbom(merged_sbom, sboms_to_merge[0])
    _assert_there_is_only_one_root(merged_sbom)
    _assert_sbom_is_well_formed(merged_sbom)


@pytest.mark.parametrize(
    "sboms_to_merge",
    [
        pytest.param(
            (
                "./tests/unit/data/sboms/alpine.pretty.json",
                "./tests/unit/data/sboms/something.simple0.100.0.spdx.pretty.json",
                "./tests/unit/data/sboms/something.more.simple.0.100.0.spdx.pretty.json",
                "./tests/unit/data/sboms/something.simple0.100.0.spdx.pretty.json",
            ),
            id="three unique SBOMs and a duplicate",
        ),
        pytest.param(
            (
                "./tests/unit/data/sboms/alpine.pretty.json",
                "./tests/unit/data/sboms/alpine.pretty.json",
                "./tests/unit/data/sboms/alpine.pretty.json",
                "./tests/unit/data/sboms/alpine.pretty.json",
            ),
            id="merging with self",
        ),
    ],
)
def test_merging_same_spdx_sbom_multiple_times_does_not_increase_the_number_of_packages(
    sboms_to_merge: list[Any],  # 'Any' is used to prevent mypy from having a fit over re-binding
) -> None:
    sboms_to_merge = [SPDXSbom.from_file(Path(s)) for s in sboms_to_merge]

    merged_sbom = reduce(add, sboms_to_merge)

    _assert_all_relationships_are_within_the_document(for_sbom=merged_sbom)
    _assert_merging_sboms_produces_correct_number_of_packages(merged_sbom, *sboms_to_merge)
    _assert_root_was_inherited_from_left_sbom(merged_sbom, sboms_to_merge[0])
    _assert_there_is_only_one_root(merged_sbom)
    _assert_sbom_is_well_formed(merged_sbom)


@pytest.mark.parametrize(
    "sboms_to_merge",
    [
        pytest.param(
            (
                "./tests/unit/data/sboms/alpine.pretty.json",
                "./tests/unit/data/sboms/alpine.pretty.json",
                "./tests/unit/data/sboms/alpine.pretty.json",
                "./tests/unit/data/sboms/alpine.pretty.json",
            ),
            id="merging with self",
        ),
    ],
)
def test_merging_spdx_on_self_does_not_modify_the_sbom(
    sboms_to_merge: list[Any],  # 'Any' is used to prevent mypy from having a fit over re-binding
) -> None:
    sboms_to_merge = [SPDXSbom.from_file(Path(s)) for s in sboms_to_merge]

    merged_sbom = reduce(add, sboms_to_merge)

    _assert_all_relationships_are_within_the_document(for_sbom=merged_sbom)
    _assert_no_relationship_is_duplicated(merged_sbom)
    _assert_merging_sboms_produces_correct_number_of_packages(merged_sbom, *sboms_to_merge)
    _assert_root_was_inherited_from_left_sbom(merged_sbom, sboms_to_merge[0])
    _assert_there_is_only_one_root(merged_sbom)
    _assert_sbom_is_well_formed(merged_sbom)


def _same_relationship_order(sbom1: SPDXSbom, sbom2: SPDXSbom) -> bool:
    for r1, r2 in zip(sbom1.relationships, sbom2.relationships):
        if r1 != r2:
            return False
    return True


@pytest.mark.parametrize(
    "sboms_to_merge",
    [
        pytest.param(
            (
                "./tests/unit/data/sboms/something.simple0.100.0.spdx.pretty.json",
                "./tests/unit/data/sboms/something.more.simple.0.100.0.spdx.pretty.json",
            ),
            id="two sboms",
        ),
    ],
)
def test_merging_spdx_sboms_produces_consistent_relationships_ordering(
    sboms_to_merge: list[Any],  # 'Any' is used to prevent mypy from having a fit over re-binding
) -> None:
    # TODO: this must be moved to integration tests due to hash seed dependency.
    # This might require some rework to ITs. Keeping this code here as a reminder.
    sboms_to_merge = [SPDXSbom.from_file(Path(s)) for s in sboms_to_merge]

    merged_sbom1 = reduce(add, sboms_to_merge)
    merged_sbom2 = reduce(add, sboms_to_merge)
    merged_sbom3 = reduce(add, sboms_to_merge)

    assert _same_relationship_order(merged_sbom1, merged_sbom2), "Order mismatch!"
    assert _same_relationship_order(merged_sbom2, merged_sbom3), "Order mismatch!"

    _assert_sbom_is_well_formed(merged_sbom1)
