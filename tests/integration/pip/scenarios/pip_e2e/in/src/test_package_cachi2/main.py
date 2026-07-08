#!/usr/bin/python3
"""
A simple script for checking pre-installed packages provided by Cachi2
"""

import io
import requests
from dockerfile_parse import DockerfileParser


if __name__ == '__main__':
    dfp = DockerfileParser(fileobj=io.BytesIO())
    containerfile_url = "https://raw.githubusercontent.com/containerbuildsystem/cachi2/e793f7c1c446994a353769a89c8395a3ed0639bc/Containerfile"
    r = requests.get(containerfile_url, allow_redirects=True)
    dfp.content = r.content

    # Print parent image
    print(dfp.parent_images[0])
