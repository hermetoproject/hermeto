# SPDX-License-Identifier: GPL-3.0-only
import os

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
_USER_AGENT_REPO = "user-agent"
# The sha256 of this content is hardcoded in the general_user_agent scenario's lockfile
_USER_AGENT_ARTIFACT = b"hermeto user agent test artifact\n"


@pytest.fixture(scope="session")
def nexus_user_agent_artifact() -> None:
    """Upload a static artifact to a hosted repository for the User-Agent test.

    Proxied upstream content can change or disappear, a file uploaded by the test itself cannot.
    """
    assert os.getenv("HERMETO_TEST_LOCAL_NEXUS") == "1"

    response = requests.post(
        f"{_NEXUS_URL}/service/rest/v1/repositories/raw/hosted",
        auth=_NEXUS_AUTH,
        json={
            "name": _USER_AGENT_REPO,
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

    response = requests.post(
        f"{_NEXUS_URL}/service/rest/v1/components?repository={_USER_AGENT_REPO}",
        auth=_NEXUS_AUTH,
        files={"raw.asset1": ("artifact.txt", _USER_AGENT_ARTIFACT)},
        data={"raw.directory": "/", "raw.asset1.filename": "artifact.txt"},
        timeout=DEFAULT_HTTP_TIMEOUT,
    )
    response.raise_for_status()
