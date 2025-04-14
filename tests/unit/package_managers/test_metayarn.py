from unittest import mock

import pytest

from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.package_managers.metayarn import fetch_yarn_source
from hermeto.core.package_managers.yarn_classic.main import NotV1Lockfile


@pytest.mark.parametrize(
    "input_request",
    (pytest.param([{"type": "yarn", "path": "."}], id="no_input_packages"),),
    indirect=["input_request"],
)
@mock.patch("hermeto.core.package_managers.metayarn.RequestOutput.__add__")
@mock.patch("hermeto.core.package_managers.metayarn.fetch_yarnberry_source")
@mock.patch("hermeto.core.package_managers.metayarn.fetch_yarn_classic_source")
def test_fetch_yarn_source_detects_yarn_classic(
    mock_yarnclassic_fetch_source: mock.Mock,
    mock_yarnberry_fetch_source: mock.Mock,
    mock_requestoutput_add_: mock.Mock,
    input_request: Request,
) -> None:

    _ = fetch_yarn_source(input_request)

    mock_yarnclassic_fetch_source.assert_called_once()
    mock_yarnberry_fetch_source.assert_not_called()


@pytest.mark.parametrize(
    "input_request",
    (pytest.param([{"type": "yarn", "path": "."}], id="no_input_packages"),),
    indirect=["input_request"],
)
@mock.patch("hermeto.core.package_managers.metayarn.RequestOutput.__add__")
@mock.patch("hermeto.core.package_managers.metayarn.fetch_yarnberry_source")
@mock.patch("hermeto.core.package_managers.metayarn.fetch_yarn_classic_source")
def test_fetch_yarn_source_detects_yarnberry(
    mock_yarnclassic_fetch_source: mock.Mock,
    mock_yarnberry_fetch_source: mock.Mock,
    mock_requestoutput_add_: mock.Mock,
    input_request: Request,
) -> None:
    mock_yarnclassic_fetch_source.side_effect = NotV1Lockfile("/some/path")

    _ = fetch_yarn_source(input_request)

    mock_yarnclassic_fetch_source.assert_called_once()
    mock_yarnberry_fetch_source.assert_called_once()


@pytest.mark.parametrize(
    "input_request",
    (pytest.param([{"type": "yarn", "path": "."}], id="no_input_packages"),),
    indirect=["input_request"],
)
@mock.patch("hermeto.core.package_managers.metayarn.fetch_yarnberry_source")
@mock.patch("hermeto.core.package_managers.metayarn.fetch_yarn_classic_source")
def test_fetch_yarn_source_propagates_yarn_classic_error(
    mock_yarnclassic_fetch_source: mock.Mock,
    mock_yarnberry_fetch_source: mock.Mock,
    input_request: Request,
) -> None:
    mock_yarnclassic_fetch_source.side_effect = PackageRejected(
        "this is a very bad package!", solution=None
    )

    with pytest.raises(PackageRejected):
        _ = fetch_yarn_source(input_request)

    mock_yarnclassic_fetch_source.assert_called_once()
    mock_yarnberry_fetch_source.assert_not_called()


@pytest.mark.parametrize(
    "input_request",
    (pytest.param([{"type": "yarn", "path": "."}], id="no_input_packages"),),
    indirect=["input_request"],
)
@mock.patch("hermeto.core.package_managers.metayarn.fetch_yarnberry_source")
@mock.patch("hermeto.core.package_managers.metayarn.fetch_yarn_classic_source")
def test_fetch_yarn_source_propagates_yarnberry_error(
    mock_yarnclassic_fetch_source: mock.Mock,
    mock_yarnberry_fetch_source: mock.Mock,
    input_request: Request,
) -> None:
    mock_yarnclassic_fetch_source.side_effect = NotV1Lockfile("/some/path")
    mock_yarnberry_fetch_source.side_effect = PackageRejected(
        "this is a very bad package!", solution=None
    )

    with pytest.raises(PackageRejected):
        _ = fetch_yarn_source(input_request)

    mock_yarnclassic_fetch_source.assert_called_once()
    mock_yarnberry_fetch_source.assert_called_once()


@pytest.mark.parametrize(
    "input_request",
    (pytest.param([{"type": "yarn", "path": "."}], id="no_input_packages"),),
    indirect=["input_request"],
)
@mock.patch("hermeto.core.package_managers.metayarn.fetch_yarnberry_source")
@mock.patch("hermeto.core.package_managers.metayarn.fetch_yarn_classic_source")
@mock.patch("hermeto.core.package_managers.metayarn.get_config")
def test_fetch_yarn_source_propagates_yarn_classic_rejection_when_yarnberry_is_forbidden(
    mock_get_config: mock.Mock,
    mock_yarnclassic_fetch_source: mock.Mock,
    mock_yarnberry_fetch_source: mock.Mock,
    input_request: Request,
) -> None:
    mock_yarnclassic_fetch_source.side_effect = NotV1Lockfile("/path/to/package")
    mock_config = mock.Mock
    mock_config.allow_yarnberry_processing = False
    mock_get_config.return_value = mock_config

    with pytest.raises(NotV1Lockfile):
        _ = fetch_yarn_source(input_request)

    mock_yarnclassic_fetch_source.assert_called_once()
    mock_yarnberry_fetch_source.assert_not_called()
