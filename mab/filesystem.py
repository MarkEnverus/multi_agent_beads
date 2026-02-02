"""Filesystem utilities for MAB.

This module provides utilities for detecting filesystem characteristics
that affect MAB's operation, particularly around file locking.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Network filesystem types that don't support reliable flock()
NETWORK_FS_TYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
        "afs",
        "gfs",
        "gfs2",
        "glusterfs",
        "lustre",
        "ceph",
        "fuse.sshfs",
        "fuse.s3fs",
        "fuse.gcsfuse",
    }
)


def get_filesystem_type(path: Path) -> str | None:
    """Get the filesystem type for a path.

    Uses /proc/mounts on Linux and mount command on macOS.

    Args:
        path: Path to check.

    Returns:
        Filesystem type string (e.g., 'ext4', 'nfs', 'apfs') or None if unknown.
    """
    try:
        path = path.resolve()

        # Linux: read /proc/mounts
        if os.path.exists("/proc/mounts"):
            return _get_fs_type_linux(path)

        # macOS/BSD: use mount command
        return _get_fs_type_darwin(path)

    except Exception as e:
        logger.debug(f"Could not determine filesystem type for {path}: {e}")
        return None


def _get_fs_type_linux(path: Path) -> str | None:
    """Get filesystem type on Linux via /proc/mounts."""
    try:
        path_str = str(path)
        best_match = ""
        best_fs_type = None

        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    mount_point = parts[1]
                    fs_type = parts[2]

                    # Find the longest matching mount point
                    if path_str.startswith(mount_point) and len(mount_point) > len(best_match):
                        best_match = mount_point
                        best_fs_type = fs_type

        return best_fs_type
    except Exception:
        return None


def _get_fs_type_darwin(path: Path) -> str | None:
    """Get filesystem type on macOS via mount command."""
    import subprocess

    try:
        result = subprocess.run(
            ["mount"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            return None

        path_str = str(path)
        best_match = ""
        best_fs_type = None

        for line in result.stdout.splitlines():
            # Format: /dev/disk1s1 on /path (fstype, options)
            parts = line.split(" on ")
            if len(parts) >= 2:
                rest = parts[1]
                # Find mount point and fs type
                paren_idx = rest.find(" (")
                if paren_idx > 0:
                    mount_point = rest[:paren_idx]
                    fs_info = rest[paren_idx + 2 :].rstrip(")")
                    fs_type = fs_info.split(",")[0].strip()

                    if path_str.startswith(mount_point) and len(mount_point) > len(best_match):
                        best_match = mount_point
                        best_fs_type = fs_type

        return best_fs_type
    except Exception:
        return None


def is_network_filesystem(path: Path) -> bool:
    """Check if a path is on a network filesystem.

    Network filesystems (NFS, CIFS, etc.) don't support reliable file locking
    with fcntl.flock(), which can cause issues with MAB's daemon singleton
    enforcement.

    Args:
        path: Path to check.

    Returns:
        True if the path is on a known network filesystem, False otherwise.
        Returns False if filesystem type cannot be determined.
    """
    fs_type = get_filesystem_type(path)

    if fs_type is None:
        return False

    fs_type_lower = fs_type.lower()

    # Check against known network filesystem types
    if fs_type_lower in NETWORK_FS_TYPES:
        return True

    # Check for FUSE-based network filesystems
    if fs_type_lower.startswith("fuse."):
        # Some FUSE filesystems are network-based
        fuse_type = fs_type_lower[5:]  # Remove "fuse." prefix
        if any(net_fs in fuse_type for net_fs in ["nfs", "smb", "cifs", "ssh", "s3", "gcs"]):
            return True

    return False


def warn_if_network_filesystem(path: Path, context: str = "MAB") -> bool:
    """Log a warning if the path is on a network filesystem.

    This is used to warn users that file locking may not work correctly
    on network filesystems, potentially leading to multiple daemon instances
    or state corruption.

    Args:
        path: Path to check.
        context: Context string for the warning message.

    Returns:
        True if warning was issued (path is on network filesystem), False otherwise.
    """
    if is_network_filesystem(path):
        fs_type = get_filesystem_type(path)
        logger.warning(
            f"{context}: Directory {path} appears to be on a network filesystem ({fs_type}). "
            f"File locking (flock) may not work reliably. Running multiple {context} instances "
            f"on different machines against the same directory could cause undefined behavior "
            f"and state corruption. See documentation for single-machine deployment requirements."
        )
        return True
    return False
