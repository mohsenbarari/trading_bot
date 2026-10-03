"""Protected main approval leaf byte custody.

Leaf file custody is necessary but NOT sufficient: later caller
binding-before-IO, wire parser, live Git, and transition/startup gates
remain mandatory. Returns verified plain bytes only after actual parent
context postcheck and cleanup succeed.
"""

from __future__ import annotations

import os
import pathlib
import stat
from typing import Optional

from scripts.two_site_protected_main_approval_record import (
    MAX_PROTECTED_MAIN_APPROVAL_BYTES as MAX_PROTECTED_MAIN_APPROVAL_LEAF_BYTES,
)
from scripts.two_site_protected_main_path_guard import (
    pinned_protected_main_parent,
)

__all__ = [
    "MAX_PROTECTED_MAIN_APPROVAL_LEAF_BYTES",
    "ProtectedMainApprovalLeafError",
    "read_protected_main_approval_leaf_locked",
]

_LEAF_FILENAME: str = "protected-main-approval.json"


class ProtectedMainApprovalLeafError(ValueError):
    """Raised when protected main approval leaf reading or validation fails."""


def _fingerprint(st: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_uid,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def _verify_leaf_stat(st: os.stat_result) -> None:
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise ProtectedMainApprovalLeafError("invalid owner approval leaf")
    if (st.st_mode & 0o7777) != 0o600:
        raise ProtectedMainApprovalLeafError("invalid owner approval leaf")
    if st.st_uid != os.geteuid():
        raise ProtectedMainApprovalLeafError("invalid owner approval leaf")
    if st.st_nlink != 1:
        raise ProtectedMainApprovalLeafError("invalid owner approval leaf")
    if not (1 <= st.st_size <= MAX_PROTECTED_MAIN_APPROVAL_LEAF_BYTES):
        raise ProtectedMainApprovalLeafError("invalid owner approval leaf")


def _read_leaf_from_parent_fd(parent_fd: int) -> bytes:
    st_pre = os.stat(_LEAF_FILENAME, dir_fd=parent_fd, follow_symlinks=False)
    _verify_leaf_stat(st_pre)
    fp = _fingerprint(st_pre)

    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    leaf_fd = os.open(_LEAF_FILENAME, flags, dir_fd=parent_fd)
    close_failed = False
    try:
        fst_before = os.fstat(leaf_fd)
        _verify_leaf_stat(fst_before)
        if _fingerprint(fst_before) != fp:
            raise ProtectedMainApprovalLeafError("invalid owner approval leaf")

        accumulated = bytearray()
        while True:
            remaining_limit = (MAX_PROTECTED_MAIN_APPROVAL_LEAF_BYTES + 1) - len(accumulated)
            if remaining_limit <= 0:
                raise ProtectedMainApprovalLeafError("invalid owner approval leaf")
            chunk = os.read(leaf_fd, remaining_limit)
            if not chunk:
                break
            if len(accumulated) + len(chunk) > MAX_PROTECTED_MAIN_APPROVAL_LEAF_BYTES:
                raise ProtectedMainApprovalLeafError("invalid owner approval leaf")
            accumulated.extend(chunk)

        if len(accumulated) != fst_before.st_size:
            raise ProtectedMainApprovalLeafError("invalid owner approval leaf")

        fst_after = os.fstat(leaf_fd)
        _verify_leaf_stat(fst_after)
        if _fingerprint(fst_after) != fp:
            raise ProtectedMainApprovalLeafError("invalid owner approval leaf")
    finally:
        try:
            os.close(leaf_fd)
        except Exception:
            close_failed = True

    if close_failed:
        raise ProtectedMainApprovalLeafError("invalid owner approval leaf")

    st_post = os.stat(_LEAF_FILENAME, dir_fd=parent_fd, follow_symlinks=False)
    _verify_leaf_stat(st_post)
    if _fingerprint(st_post) != fp:
        raise ProtectedMainApprovalLeafError("invalid owner approval leaf")

    return bytes(accumulated)


def read_protected_main_approval_leaf_locked(root: pathlib.Path) -> bytes:
    """Read verified raw bytes from protected main approval leaf.

    Caller owns existing exclusive Supervisor lock; helper acquires NO lock
    and grants NO approval/authorization. Uses pinned_protected_main_parent(root)
    to access the parent directory descriptor-relative. Reads ONLY literal
    protected-main-approval.json.
    """
    try:
        with pinned_protected_main_parent(root) as parent_fd:
            raw = _read_leaf_from_parent_fd(parent_fd)
    except Exception:
        raise ProtectedMainApprovalLeafError("invalid owner approval leaf") from None

    return raw
