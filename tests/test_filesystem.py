"""Tests for filesystem utilities."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from mab.filesystem import (
    NETWORK_FS_TYPES,
    _get_fs_type_darwin,
    _get_fs_type_linux,
    get_filesystem_type,
    is_network_filesystem,
    warn_if_network_filesystem,
)


class TestGetFilesystemTypeLinux:
    """Tests for Linux filesystem type detection."""

    def test_finds_root_mount(self):
        """Test finding root filesystem type."""
        proc_mounts = """/dev/sda1 / ext4 rw,relatime 0 0
/dev/sdb1 /home ext4 rw,relatime 0 0
"""
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            with patch("os.path.exists", return_value=True):
                result = _get_fs_type_linux(Path("/usr/bin"))
                assert result == "ext4"

    def test_finds_specific_mount(self):
        """Test finding filesystem for specific mount point."""
        proc_mounts = """/dev/sda1 / ext4 rw,relatime 0 0
server:/export /mnt/nfs nfs4 rw,vers=4.1 0 0
"""
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            result = _get_fs_type_linux(Path("/mnt/nfs/subdir/file.txt"))
            assert result == "nfs4"

    def test_longer_mount_wins(self):
        """Test that longer mount point takes precedence."""
        proc_mounts = """/dev/sda1 / ext4 rw 0 0
/dev/sda2 /home ext4 rw 0 0
server:/export /home/user/shared nfs4 rw 0 0
"""
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            # /home/user/shared/docs should match nfs4, not ext4
            result = _get_fs_type_linux(Path("/home/user/shared/docs"))
            assert result == "nfs4"

            # /home/user/local should match ext4 (under /home)
            result = _get_fs_type_linux(Path("/home/user/local"))
            assert result == "ext4"

    def test_handles_read_error(self):
        """Test handling of read errors."""
        with patch("builtins.open", side_effect=PermissionError):
            result = _get_fs_type_linux(Path("/some/path"))
            assert result is None


class TestGetFilesystemTypeDarwin:
    """Tests for macOS filesystem type detection."""

    def test_parses_mount_output(self):
        """Test parsing macOS mount command output."""
        mount_output = """/dev/disk1s1 on / (apfs, local, journaled)
/dev/disk1s2 on /System/Volumes/Data (apfs, local, journaled)
server:/export on /Volumes/Share (nfs, nodev, nosuid)
"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mount_output

        with patch("subprocess.run", return_value=mock_result):
            result = _get_fs_type_darwin(Path("/Users/test"))
            assert result == "apfs"

    def test_finds_nfs_mount(self):
        """Test finding NFS mount on macOS."""
        mount_output = """/dev/disk1s1 on / (apfs, local, journaled)
server:/export on /Volumes/Share (nfs, nodev, nosuid)
"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mount_output

        with patch("subprocess.run", return_value=mock_result):
            result = _get_fs_type_darwin(Path("/Volumes/Share/subdir"))
            assert result == "nfs"

    def test_handles_command_failure(self):
        """Test handling mount command failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = _get_fs_type_darwin(Path("/some/path"))
            assert result is None

    def test_handles_subprocess_error(self):
        """Test handling subprocess errors."""
        with patch("subprocess.run", side_effect=TimeoutError):
            result = _get_fs_type_darwin(Path("/some/path"))
            assert result is None


class TestGetFilesystemType:
    """Tests for the main get_filesystem_type function."""

    def test_uses_linux_on_linux(self):
        """Test that Linux detection is used when /proc/mounts exists."""
        with patch("os.path.exists", return_value=True):
            with patch("mab.filesystem._get_fs_type_linux", return_value="ext4") as mock_linux:
                result = get_filesystem_type(Path("/home/user"))
                mock_linux.assert_called_once()
                assert result == "ext4"

    def test_uses_darwin_on_macos(self):
        """Test that Darwin detection is used when /proc/mounts doesn't exist."""
        with patch("os.path.exists", return_value=False):
            with patch("mab.filesystem._get_fs_type_darwin", return_value="apfs") as mock_darwin:
                result = get_filesystem_type(Path("/Users/test"))
                mock_darwin.assert_called_once()
                assert result == "apfs"

    def test_handles_exceptions(self):
        """Test that exceptions are caught and logged."""
        with patch("os.path.exists", side_effect=Exception("test error")):
            result = get_filesystem_type(Path("/some/path"))
            assert result is None


class TestIsNetworkFilesystem:
    """Tests for network filesystem detection."""

    @pytest.mark.parametrize("fs_type", list(NETWORK_FS_TYPES))
    def test_detects_known_network_filesystems(self, fs_type):
        """Test detection of all known network filesystem types."""
        with patch("mab.filesystem.get_filesystem_type", return_value=fs_type):
            assert is_network_filesystem(Path("/some/path")) is True

    def test_returns_false_for_local_filesystems(self):
        """Test that local filesystems are not flagged."""
        for fs_type in ["ext4", "xfs", "apfs", "hfs+", "btrfs", "zfs"]:
            with patch("mab.filesystem.get_filesystem_type", return_value=fs_type):
                assert is_network_filesystem(Path("/some/path")) is False

    def test_returns_false_when_unknown(self):
        """Test returns False when filesystem type is unknown."""
        with patch("mab.filesystem.get_filesystem_type", return_value=None):
            assert is_network_filesystem(Path("/some/path")) is False

    def test_detects_fuse_based_network_fs(self):
        """Test detection of FUSE-based network filesystems."""
        fuse_network_types = ["fuse.sshfs", "fuse.s3fs", "fuse.gcsfuse", "fuse.nfs"]
        for fs_type in fuse_network_types:
            with patch("mab.filesystem.get_filesystem_type", return_value=fs_type):
                assert is_network_filesystem(Path("/some/path")) is True

    def test_allows_local_fuse(self):
        """Test that local FUSE filesystems are not flagged."""
        with patch("mab.filesystem.get_filesystem_type", return_value="fuse.ext4"):
            assert is_network_filesystem(Path("/some/path")) is False


class TestWarnIfNetworkFilesystem:
    """Tests for the warning function."""

    def test_logs_warning_for_network_fs(self, caplog):
        """Test that warning is logged for network filesystems."""
        with patch("mab.filesystem.is_network_filesystem", return_value=True):
            with patch("mab.filesystem.get_filesystem_type", return_value="nfs4"):
                with caplog.at_level(logging.WARNING):
                    result = warn_if_network_filesystem(Path("/mnt/nfs"), context="Test")

                    assert result is True
                    assert "network filesystem" in caplog.text.lower()
                    assert "nfs4" in caplog.text
                    assert "Test" in caplog.text

    def test_no_warning_for_local_fs(self, caplog):
        """Test that no warning is logged for local filesystems."""
        with patch("mab.filesystem.is_network_filesystem", return_value=False):
            with caplog.at_level(logging.WARNING):
                result = warn_if_network_filesystem(Path("/home/user"), context="Test")

                assert result is False
                assert "network filesystem" not in caplog.text.lower()

    def test_uses_default_context(self, caplog):
        """Test that default context is used when not specified."""
        with patch("mab.filesystem.is_network_filesystem", return_value=True):
            with patch("mab.filesystem.get_filesystem_type", return_value="cifs"):
                with caplog.at_level(logging.WARNING):
                    warn_if_network_filesystem(Path("/mnt/share"))

                    assert "MAB" in caplog.text
