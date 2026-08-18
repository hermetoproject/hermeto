# SPDX-License-Identifier: GPL-3.0-only
import gzip
import io
import json
import os
import tarfile

import pytest
import requests

from tests.nexusserver.configure import (
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_NEXUS_ADMIN_PASSWORD,
    DEFAULT_NEXUS_HOST,
    DEFAULT_NEXUS_PORT,
)

_NEXUS_URL = f"http://{DEFAULT_NEXUS_HOST}:{DEFAULT_NEXUS_PORT}"
_NEXUS_AUTH = ("admin", DEFAULT_NEXUS_ADMIN_PASSWORD)
_YARN_REPO = "yarn-v1-collision"


def _make_npm_tarball(pkg_name: str) -> bytes:
    """Create a minimal valid npm package tarball in memory.

    Output is fully deterministic (fixed timestamps, uids, format) so that
    the sha1 hashes hardcoded in the test scenario's yarn.lock stay stable.
    """
    tar_buf = io.BytesIO()
    gz_buf = io.BytesIO()
    content = json.dumps({"name": pkg_name, "version": "1.0.0"}).encode()
    with tarfile.open(fileobj=tar_buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(content)
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        tar.addfile(info, io.BytesIO(content))

    with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
        gz.write(tar_buf.getvalue())
    return gz_buf.getvalue()


@pytest.fixture(scope="session")
def nexus_collision_tarballs() -> None:
    """Upload two tarballs with identical filenames to different Nexus paths.

    Both are served as ``/a/example.tgz`` and ``/b/example.tgz`` from a hosted repository, so yarn's
    offline-mirror naming produces a collision.
    """
    # Testing this without the nexus instance we already have would require yet another git repo to
    # host the packages
    assert os.getenv("HERMETO_TEST_LOCAL_NEXUS") == "1"

    response = requests.post(
        f"{_NEXUS_URL}/service/rest/v1/repositories/raw/hosted",
        auth=_NEXUS_AUTH,
        json={
            "name": _YARN_REPO,
            "online": True,
            "storage": {
                "blobStoreName": "default",
                "strictContentTypeValidation": False,
                "writePolicy": "ALLOW",
            },
        },
        timeout=DEFAULT_HTTP_TIMEOUT,
    )
    response.raise_for_status()

    for subdir, pkg_name in [("a", "package-a"), ("b", "package-b")]:
        response = requests.post(
            f"{_NEXUS_URL}/service/rest/v1/components?repository={_YARN_REPO}",
            auth=_NEXUS_AUTH,
            files={"raw.asset1": ("example.tgz", _make_npm_tarball(pkg_name))},
            data={"raw.directory": f"/{subdir}", "raw.asset1.filename": "example.tgz"},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
        response.raise_for_status()
