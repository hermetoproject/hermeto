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
