#!/usr/bin/python3
"""Verify that hermeto-prefetched uv packages are installed correctly."""

from importlib.metadata import version

import idna
import itsdangerous
import packaging

if __name__ == "__main__":
    assert idna and itsdangerous and packaging
    for dist in ("idna", "itsdangerous", "packaging", "uv-e2e"):
        print(f"{dist}=={version(dist)}")
