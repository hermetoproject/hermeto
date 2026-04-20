# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path


def get_mock_dir(data_dir: Path) -> Path:
    return data_dir / "gomod-mocks"


def get_mocked_data(data_dir: Path, filepath: str | Path) -> str:
    return get_mock_dir(data_dir).joinpath(filepath).read_text()
