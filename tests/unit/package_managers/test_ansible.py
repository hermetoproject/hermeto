# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest import mock

import pytest
import yaml

from hermeto.core.errors import ChecksumVerificationFailed, InvalidLockfileFormat, LockfileNotFound
from hermeto.core.models.input import Request
from hermeto.core.package_managers.ansible.main import (
    DEFAULT_DEPS_DIR,
    _load_lockfile,
    _rewrite_one_requirements_file,
    fetch_ansible_source,
)
from hermeto.core.package_managers.ansible.models import (
    AnsibleCollectionLockEntry,
    find_ansible_cfg,
    parse_galaxy_servers,
)
from hermeto.core.rooted_path import RootedPath

LOCKFILE_VALID = """
lockfileVersion: 1
lockfileVendor: ansible
collections:
  - name: community.general
    version: "1.0.0"
    url: https://galaxy.ansible.com/download/community-general-1.0.0.tar.gz
    checksum: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    size: 4
    server: galaxy
"""


def _write_project(tmp_path: Path, *, lockfile: str = LOCKFILE_VALID) -> tuple[Path, Path]:
    source = tmp_path / "src"
    output = tmp_path / "out"
    source.mkdir()
    output.mkdir()
    (source / "ansible.lock.yaml").write_text(lockfile, encoding="utf-8")
    (source / "requirements.yml").write_text(
        yaml.safe_dump({"collections": [{"name": "community.general", "version": "1.0.0"}]}),
        encoding="utf-8",
    )
    return source, output


def test_load_lockfile(tmp_path: Path) -> None:
    lock = tmp_path / "ansible.lock.yaml"
    lock.write_text(LOCKFILE_VALID, encoding="utf-8")
    loaded = _load_lockfile(lock)
    assert len(loaded.collections) == 1
    assert loaded.collections[0].name == "community.general"


def test_load_lockfile_invalid(tmp_path: Path) -> None:
    lock = tmp_path / "ansible.lock.yaml"
    lock.write_text("lockfileVersion: 1\n", encoding="utf-8")
    with pytest.raises(InvalidLockfileFormat):
        _load_lockfile(lock)


def test_reject_file_url_in_lockfile(tmp_path: Path) -> None:
    lock = tmp_path / "ansible.lock.yaml"
    lock.write_text(
        """
lockfileVersion: 1
lockfileVendor: ansible
collections:
  - name: community.general
    version: "1.0.0"
    url: file:///tmp/community-general-1.0.0.tar.gz
    checksum: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    server: local
""",
        encoding="utf-8",
    )
    with pytest.raises(InvalidLockfileFormat, match="http\\(s\\) remote URL"):
        _load_lockfile(lock)


def test_artifact_filename_and_sbom() -> None:
    entry = AnsibleCollectionLockEntry(
        name="amazon.aws",
        version="10.3.0",
        url="https://example.com/artifacts/amazon-aws-10.3.0.tar.gz",
        checksum="sha256:" + "ab" * 32,
        server="automation_hub",
    )
    assert entry.artifact_filename == "amazon-aws-10.3.0.tar.gz"
    component = entry.get_sbom_component()
    assert component.name == "amazon.aws"
    assert component.version == "10.3.0"
    assert component.purl.startswith("pkg:ansible/amazon/aws@10.3.0")


def test_parse_ansible_cfg_token_env(tmp_path: Path) -> None:
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text(
        """
[galaxy_server.automation_hub]
url=https://console.redhat.com/api/automation-hub/content/published/
auth_url=https://sso.example/token
token_env=AUTOMATION_HUB_TOKEN
""",
        encoding="utf-8",
    )
    servers = parse_galaxy_servers(cfg)
    assert "automation_hub" in servers
    assert servers["automation_hub"].token_env == "AUTOMATION_HUB_TOKEN"
    assert find_ansible_cfg(tmp_path, tmp_path) == cfg.resolve()


def test_fetch_downloads_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"col!"
    digest = hashlib.sha256(payload).hexdigest()
    lockfile = f"""
lockfileVersion: 1
lockfileVendor: ansible
collections:
  - name: community.general
    version: "1.0.0"
    url: https://galaxy.ansible.com/download/community-general-1.0.0.tar.gz
    checksum: sha256:{digest}
    size: {len(payload)}
    server: galaxy
"""
    source, output = _write_project(tmp_path, lockfile=lockfile)

    async def fake_download(files, concurrency_limit, headers=None):
        for url, path in files.items():
            Path(path).write_bytes(payload)

    monkeypatch.setattr(
        "hermeto.core.package_managers.ansible.main.async_download_files",
        fake_download,
    )

    request = Request(
        source_dir=RootedPath(source),
        output_dir=RootedPath(output),
        packages=[{"type": "x-ansible", "path": "."}],
    )
    result = fetch_ansible_source(request)
    assert len(result.components) == 1
    assert result.components[0].name == "community.general"
    dest = output / DEFAULT_DEPS_DIR / "community-general-1.0.0.tar.gz"
    assert dest.read_bytes() == payload
    assert result.build_config.project_files
    rewritten = result.build_config.project_files[0].template
    assert f"${{output_dir}}/{DEFAULT_DEPS_DIR}/community-general-1.0.0.tar.gz" in rewritten


def test_fetch_checksum_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"col!"
    lockfile = f"""
lockfileVersion: 1
lockfileVendor: ansible
collections:
  - name: community.general
    version: "1.0.0"
    url: https://galaxy.ansible.com/download/community-general-1.0.0.tar.gz
    checksum: sha256:{"00" * 32}
    server: galaxy
"""
    source, output = _write_project(tmp_path, lockfile=lockfile)

    async def fake_download(files, concurrency_limit, headers=None):
        for url, path in files.items():
            Path(path).write_bytes(payload)

    monkeypatch.setattr(
        "hermeto.core.package_managers.ansible.main.async_download_files",
        fake_download,
    )

    request = Request(
        source_dir=RootedPath(source),
        output_dir=RootedPath(output),
        packages=[{"type": "x-ansible", "path": "."}],
    )
    with pytest.raises(ChecksumVerificationFailed):
        fetch_ansible_source(request)


def test_fetch_missing_lockfile(tmp_path: Path) -> None:
    source = tmp_path / "src"
    output = tmp_path / "out"
    source.mkdir()
    output.mkdir()
    request = Request(
        source_dir=RootedPath(source),
        output_dir=RootedPath(output),
        packages=[{"type": "x-ansible", "path": "."}],
    )
    with pytest.raises(LockfileNotFound):
        fetch_ansible_source(request)


def test_rewrite_path_style_requirements(tmp_path: Path) -> None:
    req = tmp_path / "requirements.yml"
    req.write_text(
        yaml.safe_dump({"collections": [{"name": "collections/community-general-1.0.0.tar.gz"}]}),
        encoding="utf-8",
    )
    filename_by_fqcn = {"community.general": "community-general-1.0.0.tar.gz"}
    basename_to_fqcn = {"community-general-1.0.0.tar.gz": "community.general"}
    project_file = _rewrite_one_requirements_file(req, filename_by_fqcn, basename_to_fqcn)
    assert project_file is not None
    assert (
        f"${{output_dir}}/{DEFAULT_DEPS_DIR}/community-general-1.0.0.tar.gz"
        in project_file.template
    )


def test_sso_auth_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"col!"
    digest = hashlib.sha256(payload).hexdigest()
    lockfile = f"""
lockfileVersion: 1
lockfileVendor: ansible
collections:
  - name: amazon.aws
    version: "10.3.0"
    url: https://console.redhat.com/api/automation-hub/v3/plugin/ansible/content/published/collections/artifacts/amazon-aws-10.3.0.tar.gz
    checksum: sha256:{digest}
    server: automation_hub
"""
    source, output = _write_project(tmp_path, lockfile=lockfile)
    (source / "ansible.cfg").write_text(
        """
[galaxy_server.automation_hub]
url=https://console.redhat.com/api/automation-hub/content/published/
auth_url=https://sso.example/token
token_env=AUTOMATION_HUB_TOKEN
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOMATION_HUB_TOKEN", "refresh-secret")

    mock_resp = mock.Mock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"access_token": "access-secret"}
    monkeypatch.setattr(
        "hermeto.core.package_managers.ansible.models.requests.post",
        mock.Mock(return_value=mock_resp),
    )

    captured_headers: dict = {}

    async def fake_download(files, concurrency_limit, headers=None):
        captured_headers.update(headers or {})
        for url, path in files.items():
            Path(path).write_bytes(payload)

    monkeypatch.setattr(
        "hermeto.core.package_managers.ansible.main.async_download_files",
        fake_download,
    )

    request = Request(
        source_dir=RootedPath(source),
        output_dir=RootedPath(output),
        packages=[{"type": "x-ansible", "path": "."}],
    )
    fetch_ansible_source(request)
    url = (
        "https://console.redhat.com/api/automation-hub/v3/plugin/ansible/content/"
        "published/collections/artifacts/amazon-aws-10.3.0.tar.gz"
    )
    assert captured_headers[url]["Authorization"] == "Bearer access-secret"
