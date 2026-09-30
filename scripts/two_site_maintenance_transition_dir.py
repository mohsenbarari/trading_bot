r"""Descriptor-relative maintenance-transitions directory helper under AE03.

Provides secure descriptor-relative opening and listing of the private
maintenance-transitions directory and its reviewed transition leaves.

Boundary and authority constraints:
1. Directory-only helper: contains NO record contents/write, checkpoint, Git,
   Broker, STOP, startup, monitor, wake, replay, or live record operations.
2. Pinned descriptor isolation: all directory mutations and inspections occur
   relative to pinned descriptors with O_NOFOLLOW / follow_symlinks=False.
3. Fixed error strings: error text is constant and does not echo untrusted payloads.
4. Exact leaf grammar: leaf names must strictly match ^rescope-[0-9a-f]{64}\.json$.
"""

from __future__ import annotations

import os
import re
import stat

MAINTENANCE_TRANSITIONS_DIRNAME: str = "maintenance-transitions"
MAX_MAINTENANCE_TRANSITION_LEAVES: int = 256
LEAF_NAME_PATTERN: str = r"^rescope-[0-9a-f]{64}\.json$"

_LEAF_NAME_RE = re.compile(LEAF_NAME_PATTERN)


class MaintenanceTransitionDirError(ValueError):
    """Raised when maintenance-transitions directory operations fail."""


__all__ = [
    "LEAF_NAME_PATTERN",
    "MAINTENANCE_TRANSITIONS_DIRNAME",
    "MAX_MAINTENANCE_TRANSITION_LEAVES",
    "MaintenanceTransitionDirError",
    "list_maintenance_transition_leaves",
    "open_maintenance_transitions_dir",
]


def list_maintenance_transition_leaves(transitions_fd: int) -> list[str]:
    """List and validate leaf entries in the maintenance-transitions directory.

    Requires transitions_fd to reference an existing 0700 directory owned by the
    current process. Validates that every leaf entry is a regular file with
    mode 0600, link count 1, valid name, and owner matching current euid.
    Enforces a hard limit of 256 entries before accumulation. Revalidates
    transitions_fd metadata before and after scan to detect metadata drift.
    """
    if type(transitions_fd) is not int or transitions_fd < 0:
        raise MaintenanceTransitionDirError("Invalid transitions descriptor")

    try:
        st_before = os.fstat(transitions_fd)
    except OSError:
        raise MaintenanceTransitionDirError("Failed to stat transitions descriptor") from None

    if stat.S_ISLNK(st_before.st_mode):
        raise MaintenanceTransitionDirError("Transitions descriptor must not be a symlink")
    if not stat.S_ISDIR(st_before.st_mode):
        raise MaintenanceTransitionDirError("Transitions descriptor must be a directory")
    if (st_before.st_mode & 0o777) != 0o700:
        raise MaintenanceTransitionDirError("Transitions directory mode must be 0700")
    if st_before.st_uid != os.geteuid():
        raise MaintenanceTransitionDirError("Transitions directory owner mismatch")

    scan_fd: int | None = None
    try:
        try:
            scanner = os.scandir(transitions_fd)
        except TypeError:
            scan_fd = os.dup(transitions_fd)
            scanner = os.scandir(scan_fd)
    except MaintenanceTransitionDirError:
        raise
    except Exception:
        if scan_fd is not None:
            try:
                os.close(scan_fd)
            except OSError:
                pass
        raise MaintenanceTransitionDirError("Failed to open directory scanner") from None

    entries: list[str] = []
    count = 0
    try:
        with scanner:
            for entry in scanner:
                count += 1
                if count > MAX_MAINTENANCE_TRANSITION_LEAVES:
                    raise MaintenanceTransitionDirError("Directory entry count exceeds limit")

                name = entry.name
                if not _LEAF_NAME_RE.fullmatch(name):
                    raise MaintenanceTransitionDirError("Invalid leaf entry name")

                try:
                    st_entry = entry.stat(follow_symlinks=False)
                except OSError:
                    raise MaintenanceTransitionDirError("Failed to stat leaf entry") from None

                if stat.S_ISLNK(st_entry.st_mode) or entry.is_symlink():
                    raise MaintenanceTransitionDirError("Leaf entry must not be a symlink")
                if not stat.S_ISREG(st_entry.st_mode) or entry.is_dir():
                    raise MaintenanceTransitionDirError("Leaf entry must be a regular file")
                if st_entry.st_uid != os.geteuid():
                    raise MaintenanceTransitionDirError("Leaf entry owner mismatch")
                if (st_entry.st_mode & 0o777) != 0o600:
                    raise MaintenanceTransitionDirError("Leaf entry mode must be 0600")
                if st_entry.st_nlink != 1:
                    raise MaintenanceTransitionDirError("Leaf entry link count must be 1")

                entries.append(name)
    except MaintenanceTransitionDirError:
        raise
    except Exception as exc:
        raise MaintenanceTransitionDirError("Directory scan failed") from exc
    finally:
        if scan_fd is not None:
            try:
                os.close(scan_fd)
            except OSError:
                pass

    try:
        st_after = os.fstat(transitions_fd)
    except OSError:
        raise MaintenanceTransitionDirError("Failed to stat transitions descriptor after scan") from None

    if (
        st_after.st_dev != st_before.st_dev
        or st_after.st_ino != st_before.st_ino
        or (st_after.st_mode & 0o777) != (st_before.st_mode & 0o777)
        or st_after.st_uid != st_before.st_uid
        or st_after.st_nlink != st_before.st_nlink
    ):
        raise MaintenanceTransitionDirError("Transitions directory metadata drift detected")

    return sorted(entries)


def open_maintenance_transitions_dir(
    state_root_fd: int,
    *,
    create: bool = False,
) -> int | None:
    """Open or optionally create the private maintenance-transitions directory descriptor.

    Returns the integer file descriptor or None if create=False and the directory
    does not exist. All operations are relative to state_root_fd.
    Validates parent and child owner and 0700 mode. When create=True, enforces
    child-then-parent fsync.
    Scans the directory and performs post-scan revalidation against identity baseline
    and absolute security invariants to prevent returning a detached or replaced directory descriptor.
    """
    if type(state_root_fd) is not int or state_root_fd < 0:
        raise MaintenanceTransitionDirError("Invalid state_root_fd")
    if type(create) is not bool:
        raise MaintenanceTransitionDirError("Invalid create argument")

    try:
        initial_parent_st = os.fstat(state_root_fd)
    except OSError:
        raise MaintenanceTransitionDirError("Failed to stat parent descriptor") from None

    if stat.S_ISLNK(initial_parent_st.st_mode):
        raise MaintenanceTransitionDirError("Parent descriptor must not be a symlink")
    if not stat.S_ISDIR(initial_parent_st.st_mode):
        raise MaintenanceTransitionDirError("Parent must be a directory")
    if (initial_parent_st.st_mode & 0o777) != 0o700:
        raise MaintenanceTransitionDirError("Parent directory mode must be 0700")
    if initial_parent_st.st_uid != os.geteuid():
        raise MaintenanceTransitionDirError("Parent directory owner mismatch")

    base_parent_st = initial_parent_st

    try:
        st_child = os.stat(
            MAINTENANCE_TRANSITIONS_DIRNAME,
            dir_fd=state_root_fd,
            follow_symlinks=False,
        )
        created = False
    except FileNotFoundError:
        if not create:
            return None
        try:
            os.mkdir(MAINTENANCE_TRANSITIONS_DIRNAME, mode=0o700, dir_fd=state_root_fd)
            created = True
        except OSError:
            raise MaintenanceTransitionDirError("Failed to create maintenance-transitions directory") from None
        try:
            st_child = os.stat(
                MAINTENANCE_TRANSITIONS_DIRNAME,
                dir_fd=state_root_fd,
                follow_symlinks=False,
            )
        except OSError:
            raise MaintenanceTransitionDirError("Failed to stat newly created maintenance-transitions directory") from None
    except OSError:
        raise MaintenanceTransitionDirError("Failed to stat maintenance-transitions directory") from None

    if stat.S_ISLNK(st_child.st_mode):
        raise MaintenanceTransitionDirError("maintenance-transitions directory must not be a symlink")
    if not stat.S_ISDIR(st_child.st_mode):
        raise MaintenanceTransitionDirError("maintenance-transitions must be a directory")
    if st_child.st_uid != os.geteuid():
        raise MaintenanceTransitionDirError("maintenance-transitions directory owner mismatch")
    if not created and (st_child.st_mode & 0o777) != 0o700:
        raise MaintenanceTransitionDirError("maintenance-transitions directory mode must be 0700")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        child_fd = os.open(MAINTENANCE_TRANSITIONS_DIRNAME, flags, dir_fd=state_root_fd)
    except OSError:
        raise MaintenanceTransitionDirError("Failed to open maintenance-transitions directory") from None

    try:
        if created:
            try:
                os.fchmod(child_fd, 0o700)
            except OSError:
                raise MaintenanceTransitionDirError("Failed to fchmod newly created directory") from None
            try:
                os.fsync(child_fd)
            except OSError:
                raise MaintenanceTransitionDirError("Failed to fsync child directory") from None
            try:
                os.fsync(state_root_fd)
            except OSError:
                raise MaintenanceTransitionDirError("Failed to fsync parent directory") from None

            try:
                base_parent_st = os.fstat(state_root_fd)
            except OSError:
                raise MaintenanceTransitionDirError("Failed to stat parent descriptor after directory creation") from None
            if stat.S_ISLNK(base_parent_st.st_mode):
                raise MaintenanceTransitionDirError("Parent descriptor must not be a symlink")
            if not stat.S_ISDIR(base_parent_st.st_mode):
                raise MaintenanceTransitionDirError("Parent must be a directory")
            if (base_parent_st.st_mode & 0o777) != 0o700:
                raise MaintenanceTransitionDirError("Parent directory mode must be 0700")
            if base_parent_st.st_uid != os.geteuid():
                raise MaintenanceTransitionDirError("Parent directory owner mismatch")
            if (
                base_parent_st.st_dev != initial_parent_st.st_dev
                or base_parent_st.st_ino != initial_parent_st.st_ino
            ):
                raise MaintenanceTransitionDirError("Parent directory metadata drift detected")

        st_child_fd = os.fstat(child_fd)
        if (st_child_fd.st_dev, st_child_fd.st_ino) != (st_child.st_dev, st_child.st_ino):
            raise MaintenanceTransitionDirError("Directory identity mismatch")
        if not stat.S_ISDIR(st_child_fd.st_mode) or stat.S_ISLNK(st_child_fd.st_mode):
            raise MaintenanceTransitionDirError("maintenance-transitions must be a directory")
        if (st_child_fd.st_mode & 0o777) != 0o700:
            raise MaintenanceTransitionDirError("maintenance-transitions directory mode must be 0700")
        if st_child_fd.st_uid != os.geteuid():
            raise MaintenanceTransitionDirError("maintenance-transitions directory owner mismatch")

        base_child_st = st_child_fd

        list_maintenance_transition_leaves(child_fd)

        try:
            st_child_restat = os.stat(
                MAINTENANCE_TRANSITIONS_DIRNAME,
                dir_fd=state_root_fd,
                follow_symlinks=False,
            )
        except OSError:
            raise MaintenanceTransitionDirError("Failed to restat maintenance-transitions directory after scan") from None

        st_child_after = os.fstat(child_fd)
        st_parent_after = os.fstat(state_root_fd)

        # Enforce absolute security invariants on parent metadata before drift comparison.
        if stat.S_ISLNK(st_parent_after.st_mode):
            raise MaintenanceTransitionDirError("Parent descriptor must not be a symlink")
        if not stat.S_ISDIR(st_parent_after.st_mode):
            raise MaintenanceTransitionDirError("Parent must be a directory")
        if (st_parent_after.st_mode & 0o777) != 0o700:
            raise MaintenanceTransitionDirError("Parent directory mode must be 0700")
        if st_parent_after.st_uid != os.geteuid():
            raise MaintenanceTransitionDirError("Parent directory owner mismatch")

        if (
            st_parent_after.st_dev != base_parent_st.st_dev
            or st_parent_after.st_ino != base_parent_st.st_ino
            or (st_parent_after.st_mode & 0o777) != (base_parent_st.st_mode & 0o777)
            or st_parent_after.st_uid != base_parent_st.st_uid
            or st_parent_after.st_nlink != base_parent_st.st_nlink
        ):
            raise MaintenanceTransitionDirError("Parent directory metadata drift detected")

        if (
            st_child_restat.st_dev != base_child_st.st_dev
            or st_child_restat.st_ino != base_child_st.st_ino
            or stat.S_ISLNK(st_child_restat.st_mode)
            or not stat.S_ISDIR(st_child_restat.st_mode)
            or (st_child_restat.st_mode & 0o777) != 0o700
            or st_child_restat.st_uid != os.geteuid()
            or st_child_restat.st_nlink != base_child_st.st_nlink
        ):
            raise MaintenanceTransitionDirError("Child directory path replaced or drift detected")

        if (
            st_child_after.st_dev != base_child_st.st_dev
            or st_child_after.st_ino != base_child_st.st_ino
            or stat.S_ISLNK(st_child_after.st_mode)
            or not stat.S_ISDIR(st_child_after.st_mode)
            or (st_child_after.st_mode & 0o777) != 0o700
            or st_child_after.st_uid != os.geteuid()
            or st_child_after.st_nlink != base_child_st.st_nlink
        ):
            raise MaintenanceTransitionDirError("Child descriptor metadata drift detected")

        return child_fd
    except Exception:
        os.close(child_fd)
        raise
