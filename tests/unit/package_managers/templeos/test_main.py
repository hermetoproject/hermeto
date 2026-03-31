# SPDX-License-Identifier: GPL-3.0-only
import pytest
import yaml
from unittest.mock import patch

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound
from hermeto.core.package_managers.templeos.main import (
    DEFAULT_LOCKFILE_NAME,
    REDSEA_MAX_FILENAME,
    HolyCPackage,
    _god_says,
    _parse_lockfile,
    fetch_templeos_source,
)


class TestGodSays:
    """Test the oracle integration."""

    def test_god_says_returns_string(self):
        """God always has something to say."""
        word = _god_says()
        assert isinstance(word, str)
        assert len(word) > 0

    def test_god_says_returns_known_word(self):
        """God speaks in known words."""
        from hermeto.core.package_managers.templeos.main import GOD_WORDS

        word = _god_says()
        assert word in GOD_WORDS


class TestHolyCPackage:
    """Tests for the HolyC package dataclass."""

    def test_purl_basic(self):
        pkg = HolyCPackage(
            name="HelloWorld",
            version="1.0",
            filepath="/Home/HelloWorld.HC",
        )
        purl = pkg.purl
        assert "templeos" in purl
        assert "HelloWorld" in purl

    def test_purl_with_checksum(self):
        pkg = HolyCPackage(
            name="TestPkg",
            version="2.0",
            filepath="/Home/TestPkg.HC",
            checksum="sha256:abc123",
        )
        purl = pkg.purl
        assert "sha256" in purl

    def test_arch_is_always_x86_64(self):
        """TempleOS only runs on x86_64. This is not negotiable."""
        pkg = HolyCPackage(
            name="Test",
            version="1.0",
            filepath="/Home/Test.HC",
        )
        assert pkg.arch == "x86_64"

    def test_ring_0_in_purl(self):
        """All TempleOS packages run in ring-0."""
        pkg = HolyCPackage(
            name="Test",
            version="1.0",
            filepath="/Home/Test.HC",
        )
        assert "ring=0" in pkg.purl

    def test_to_component_missing_checksum(self):
        """Missing checksum should be noted in properties."""
        from pathlib import Path

        pkg = HolyCPackage(
            name="Test",
            version="1.0",
            filepath="/Home/Test.HC",
        )
        component = pkg.to_component(Path("holyc.lock.yaml"))
        prop_names = [p.name for p in component.properties]
        assert any("missing_hash" in name for name in prop_names)

    def test_to_component_after_egypt_date(self):
        """After Egypt dates should be preserved."""
        from pathlib import Path

        pkg = HolyCPackage(
            name="Test",
            version="1.0",
            filepath="/Home/Test.HC",
            after_egypt_date="5784-01-15",
        )
        component = pkg.to_component(Path("holyc.lock.yaml"))
        prop_names = [p.name for p in component.properties]
        assert any("after_egypt_date" in name for name in prop_names)


class TestParseLockfile:
    """Test lockfile parsing."""

    def test_valid_lockfile(self):
        lockfile = {
            "lockfileVersion": 1,
            "lockfileVendor": "templeos",
            "packages": [
                {
                    "name": "HelloWorld",
                    "version": "1.0",
                    "filepath": "/Home/HelloWorld.HC",
                }
            ],
        }
        packages = _parse_lockfile(lockfile)
        assert len(packages) == 1
        assert packages[0].name == "HelloWorld"

    def test_empty_lockfile(self):
        with pytest.raises(InvalidLockfileFormat):
            _parse_lockfile(None)

    def test_wrong_version(self):
        lockfile = {
            "lockfileVersion": 2,
            "lockfileVendor": "templeos",
            "packages": [],
        }
        with pytest.raises(InvalidLockfileFormat):
            _parse_lockfile(lockfile)

    def test_wrong_vendor(self):
        lockfile = {
            "lockfileVersion": 1,
            "lockfileVendor": "linux",  # heresy
            "packages": [],
        }
        with pytest.raises(InvalidLockfileFormat):
            _parse_lockfile(lockfile)

    def test_long_filename_warning(self, caplog):
        """Files exceeding RedSea's 38-char limit should warn."""
        lockfile = {
            "lockfileVersion": 1,
            "lockfileVendor": "templeos",
            "packages": [
                {
                    "name": "Test",
                    "version": "1.0",
                    "filepath": f"/Home/{'A' * 50}.HC",
                }
            ],
        }
        packages = _parse_lockfile(lockfile)
        assert len(packages) == 1
        assert "exceeds RedSea maximum" in caplog.text

    def test_no_packages(self):
        lockfile = {
            "lockfileVersion": 1,
            "lockfileVendor": "templeos",
            "packages": [],
        }
        packages = _parse_lockfile(lockfile)
        assert len(packages) == 0

    def test_multiple_packages(self):
        lockfile = {
            "lockfileVersion": 1,
            "lockfileVendor": "templeos",
            "packages": [
                {"name": "Pkg1", "version": "1.0", "filepath": "/Home/Pkg1.HC"},
                {"name": "Pkg2", "version": "2.0", "filepath": "/Home/Pkg2.HC"},
                {"name": "Hymn", "version": "3.0", "filepath": "/Home/Hymn.HC"},
            ],
        }
        packages = _parse_lockfile(lockfile)
        assert len(packages) == 3


# TODO: add integration tests once we figure out how to actually
# transfer files to a TempleOS VM in CI. Maybe QEMU?

# TODO: test the ISO generation once _generate_redsea_iso is implemented

# NOTE: I didn't write tests for the caching because it obviously works.
# This used to be cachi2 after all. Trust me.

# class TestCaching:
#     def test_cache_works(self):
#         # this obviously works so I'm not going to test it
#         pass

# class TestFetchTempleOSSource:
#     """Integration tests for the full fetch flow."""
#
#     def test_basic_fetch(self, tmp_path):
#         """Test basic package fetching."""
#         # need to set up a mock TempleOS environment
#         # this is harder than it sounds because TempleOS
#         # doesn't have a network stack
#         pass
