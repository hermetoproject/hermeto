# SPDX-License-Identifier: GPL-3.0-only
from urllib.parse import urlparse

from hermeto.core.errors import PackageRejected

JAVA_TO_PYTHON_CHECKSUM_ALGORITHMS = {
    "SHA-256": "sha256",
    "SHA-1": "sha1",
    "SHA-512": "sha512",
    "SHA-224": "sha224",
    "SHA-384": "sha384",
    "MD5": "md5",
}


def convert_java_checksum_algorithm_to_python(java_algorithm: str) -> str:
    """
    Convert Java checksum algorithm name to Python hashlib algorithm name.
    """
    python_algorithm = JAVA_TO_PYTHON_CHECKSUM_ALGORITHMS.get(java_algorithm)
    if not python_algorithm:
        raise PackageRejected(
            f"Unsupported checksum algorithm: {java_algorithm}",
            solution=f"Supported algorithms: {', '.join(JAVA_TO_PYTHON_CHECKSUM_ALGORITHMS.keys())}",
        )

    return python_algorithm


def derive_repository_id(url: str) -> str:
    """
    Derive the repository ID from the URL.
    """
    hostname_to_id = {
        "repo1.maven.org": "central",
        "repo.maven.apache.org": "central",
        "central.maven.org": "central",
        "oss.sonatype.org": "sonatype",
        "s01.oss.sonatype.org": "sonatype",
        "repository.jboss.org": "jboss",
        "repo.spring.io": "spring",
    }

    parsed_url = urlparse(url)
    hostname = parsed_url.hostname
    if not hostname:
        return "unknown"

    return hostname_to_id.get(hostname, "unknown")


def derive_pom_filename(artifact_id: str, version: str) -> str:
    """
    Derive the POM filename from the artifact ID and version.
    """
    return f"{artifact_id}-{version}.pom"
