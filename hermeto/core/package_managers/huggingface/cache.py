"""Hugging Face Hub cache structure implementation.

This module recreates the Hugging Face Hub's cache directory structure
to enable offline usage with HF_HUB_OFFLINE=1.

Cache structure:
    deps/huggingface/hub/
    └── models--{namespace}--{name}/  (or datasets--)
        ├── blobs/
        │   └── {blob_hash}  (actual file content)
        ├── snapshots/
        │   └── {revision}/
        │       └── {filename} -> ../../blobs/{blob_hash}  (symlink)
        └── refs/
            └── main -> {revision}  (ref file)
"""

import hashlib
import logging
import shutil
from pathlib import Path

from hermeto.core.checksum import ChecksumInfo

log = logging.getLogger(__name__)


class HFCacheManager:
    """Manager for creating and maintaining HuggingFace cache structure."""

    def __init__(self, cache_root: Path):
        """
        Initialize cache manager.

        :param cache_root: Root directory for the HF cache (e.g., deps/huggingface/hub/)
        """
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def get_repo_cache_dir(self, namespace: str, name: str, repo_type: str) -> Path:
        """
        Get the cache directory for a repository.

        :param namespace: Repository namespace (empty string for default namespace)
        :param name: Repository name
        :param repo_type: "model" or "dataset"
        :return: Path to repository cache directory
        """
        # Format: models--{namespace}--{name} or models--{name} for default namespace
        prefix = "models" if repo_type == "model" else "datasets"

        if namespace:
            repo_dir_name = f"{prefix}--{namespace}--{name}"
        else:
            repo_dir_name = f"{prefix}--{name}"

        return self.cache_root / repo_dir_name

    def _get_blob_path(self, repo_cache_dir: Path, blob_hash: str) -> Path:
        """Get path to a blob file."""
        return repo_cache_dir / "blobs" / blob_hash

    def _get_snapshot_path(self, repo_cache_dir: Path, revision: str, filename: str) -> Path:
        """Get path to a file in a snapshot."""
        return repo_cache_dir / "snapshots" / revision / filename

    def _get_refs_path(self, repo_cache_dir: Path) -> Path:
        """Get path to refs directory."""
        return repo_cache_dir / "refs"

    def add_file_to_cache(
        self,
        repo_cache_dir: Path,
        revision: str,
        file_path: str,
        local_file: Path,
        checksum_info: ChecksumInfo,
    ) -> None:
        """
        Add a file to the cache with proper blob/snapshot structure.

        :param repo_cache_dir: Repository cache directory
        :param revision: Git revision (commit hash)
        :param file_path: Relative path of file in repository (e.g., "config.json")
        :param local_file: Path to the downloaded file
        :param checksum_info: Checksum information for verification
        """
        # Create blobs directory
        blobs_dir = repo_cache_dir / "blobs"
        blobs_dir.mkdir(parents=True, exist_ok=True)

        # Use the checksum as blob hash (similar to how HF Hub does it)
        # HF uses the file's hash as the blob identifier
        blob_hash = self._compute_blob_hash(local_file)
        blob_path = self._get_blob_path(repo_cache_dir, blob_hash)

        # Move file to blobs directory
        if not blob_path.exists():
            shutil.copy2(local_file, blob_path)
            log.debug(f"Created blob: {blob_path}")

        # Create snapshot symlink
        snapshot_file = self._get_snapshot_path(repo_cache_dir, revision, file_path)
        snapshot_file.parent.mkdir(parents=True, exist_ok=True)

        # Create relative symlink from snapshot to blob
        # Example: snapshots/abc123/config.json -> ../../blobs/xyz789
        if snapshot_file.exists() or snapshot_file.is_symlink():
            snapshot_file.unlink()

        # Calculate relative path from snapshot to blob
        rel_path = Path("../../blobs") / blob_hash
        snapshot_file.symlink_to(rel_path)
        log.debug(f"Created snapshot symlink: {snapshot_file} -> {rel_path}")

    def create_ref(self, repo_cache_dir: Path, ref_name: str, revision: str) -> None:
        """
        Create a ref file pointing to a revision.

        :param repo_cache_dir: Repository cache directory
        :param ref_name: Name of the ref (e.g., "main")
        :param revision: Git revision (commit hash)
        """
        refs_dir = self._get_refs_path(repo_cache_dir)
        refs_dir.mkdir(parents=True, exist_ok=True)

        ref_file = refs_dir / ref_name
        ref_file.write_text(revision)
        log.debug(f"Created ref: {ref_file} -> {revision}")

    @staticmethod
    def _compute_blob_hash(file_path: Path) -> str:
        """
        Compute SHA256 hash of a file to use as blob identifier.

        :param file_path: Path to file
        :return: Hex digest of SHA256 hash
        """
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def get_snapshot_path(self, repo_cache_dir: Path, revision: str) -> Path:
        """
        Get the path to a specific revision's snapshot directory.

        :param repo_cache_dir: Repository cache directory
        :param revision: Git revision (commit hash)
        :return: Path to snapshot directory
        """
        return repo_cache_dir / "snapshots" / revision
