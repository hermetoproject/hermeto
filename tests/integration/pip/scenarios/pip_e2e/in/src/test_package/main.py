#!/usr/bin/python3
"""Verify that hermeto-prefetched pip packages are installed correctly."""

import tomli
from packaging.version import Version

if __name__ == "__main__":
    data = tomli.loads('version = "1.0.0"')
    print(f"{Version(data['version'])}")
