# SPDX-License-Identifier: GPL-3.0-only
"""Verify binary-filtered uv dependencies are installed."""

from importlib.metadata import version

import anyio
import cffi
import charset_normalizer


if __name__ == "__main__":
    assert anyio and cffi and charset_normalizer
    expected_versions = {
        "anyio": "4.16.0",
        "cffi": "2.1.1",
        "charset-normalizer": "3.5.2",
    }
    installed_versions = {
        distribution: version(distribution) for distribution in expected_versions
    }
    if installed_versions != expected_versions:
        raise RuntimeError(f"Unexpected installed versions: {installed_versions}")
    print("binary-filtered-dependencies-installed")
