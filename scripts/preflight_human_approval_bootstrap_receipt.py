#!/usr/bin/env python3
"""Diagnose the local safety of a human-approval bootstrap receipt.

This is deliberately a *metadata-only* preflight.  It never parses, hashes,
or reads the receipt payload, and has no network or write operations.  Its
purpose is to turn the deliberately terse ``SecureFileError`` raised by the
approval issuer into an actionable local report before an operator starts an
interactive approval flow.

The preflight checks the same two runtime gates used by
``manage_three_site_human_approval.py``:

* the issuer directory must be a real, current-owner, exact-``0700``
  directory; and
* the receipt must be opened without following its final symlink and must be
  a single-link, current-owner regular file with no group/world permissions.

It additionally walks every ancestor below a trusted anchor one descriptor at
a time.  That hardened-path check catches unsafe ancestor directories and
symlinks that the legacy file reader cannot explain in its one-line error.
The default anchor is ``/``.  ``--trust-anchor`` is for isolated test roots
only; it is an explicit assertion that ancestors above that directory were
already verified by another control.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import stat
from typing import Any, Iterable


DIAGNOSTIC_SCHEMA = "three-site-human-approval-bootstrap-receipt-preflight-v1"
DEFAULT_RECEIPT_PATH = Path(
    "/etc/trading-bot/security/human-approval/bootstrap-receipt.json"
)
DEFAULT_MAX_SIZE = 1024 * 1024


def _mode_octal(metadata: os.stat_result) -> str:
    return f"{stat.S_IMODE(metadata.st_mode):04o}"


def _kind(metadata: os.stat_result) -> str:
    mode = metadata.st_mode
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character_device"
    if stat.S_ISBLK(mode):
        return "block_device"
    return "unknown"


def _metadata(path: Path, metadata: os.stat_result) -> dict[str, Any]:
    """Return metadata only; do not expose a file's contents or inode."""

    return {
        "path": str(path),
        "kind": _kind(metadata),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": _mode_octal(metadata),
        "hard_link_count": metadata.st_nlink,
    }


def _os_error_detail(exc: OSError) -> dict[str, str]:
    number = exc.errno if isinstance(exc.errno, int) else errno.EIO
    return {
        "errno": errno.errorcode.get(number, f"ERRNO_{number}"),
        "operation": "metadata/open",
    }


def _finding(
    findings: list[dict[str, str]],
    *,
    code: str,
    path: Path,
    scope: str,
    remediation: str,
    detail: str | None = None,
) -> None:
    entry = {
        "code": code,
        "path": str(path),
        "scope": scope,
        "remediation": remediation,
    }
    if detail is not None:
        entry["detail"] = detail
    findings.append(entry)


def _reject_noncanonical_path(
    path: Path, *, label: str, findings: list[dict[str, str]]
) -> bool:
    if not path.is_absolute():
        _finding(
            findings,
            code="path_not_absolute",
            path=path,
            scope=label,
            remediation="Use the absolute issuer receipt path; do not rely on the current directory.",
        )
        return True
    if any(part in {".", ".."} for part in path.parts):
        _finding(
            findings,
            code="path_contains_traversal",
            path=path,
            scope=label,
            remediation="Use a canonical absolute path with no '.' or '..' components.",
        )
        return True
    return False


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _receipt_flags() -> int:
    # O_NONBLOCK is diagnostic-only.  It prevents a malicious replacement by
    # a FIFO/device from stalling the preflight; no payload is ever read.
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _append_ancestor_metadata(
    ancestors: list[dict[str, Any]],
    findings: list[dict[str, str]],
    *,
    path: Path,
    metadata: os.stat_result,
    owner_uid: int,
    issuer_directory: Path,
) -> None:
    entry = _metadata(path, metadata)
    entry["owner_matches_expected_uid"] = metadata.st_uid == owner_uid
    entry["group_or_world_writable"] = bool(stat.S_IMODE(metadata.st_mode) & 0o022)
    entry["issuer_directory"] = path == issuer_directory
    ancestors.append(entry)

    if not stat.S_ISDIR(metadata.st_mode):
        _finding(
            findings,
            code="ancestor_not_directory",
            path=path,
            scope="hardened_path",
            remediation="Replace this path component with an owner-controlled real directory; do not traverse it.",
            detail=f"observed_kind={entry['kind']}",
        )
        return
    if metadata.st_uid != owner_uid:
        _finding(
            findings,
            code="ancestor_owner_mismatch",
            path=path,
            scope="hardened_path",
            remediation=(
                f"Have the authorized host administrator verify ownership and set uid {owner_uid} "
                "only if this is the intended controlled directory."
            ),
            detail=f"expected_uid={owner_uid}; observed_uid={metadata.st_uid}",
        )
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        _finding(
            findings,
            code="ancestor_group_or_world_writable",
            path=path,
            scope="hardened_path",
            remediation="Remove group/world write access after verifying the directory's intended ownership.",
            detail=f"observed_mode={entry['mode']}",
        )
    if path == issuer_directory:
        if metadata.st_uid != owner_uid or stat.S_IMODE(metadata.st_mode) != 0o700:
            _finding(
                findings,
                code="issuer_directory_not_exact_owner_0700",
                path=path,
                scope="runtime",
                remediation=(
                    f"The issuer directory must be a real uid {owner_uid} directory with exact mode 0700 "
                    "before issuing a token."
                ),
                detail=(
                    f"expected_uid={owner_uid}; observed_uid={metadata.st_uid}; "
                    f"expected_mode=0700; observed_mode={entry['mode']}"
                ),
            )


def _open_anchor(anchor: Path) -> tuple[int, os.stat_result]:
    metadata_before_open = os.lstat(anchor)
    if stat.S_ISLNK(metadata_before_open.st_mode):
        raise OSError(errno.ELOOP, "trust anchor is a symlink", str(anchor))
    descriptor = os.open(anchor, _directory_flags())
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise NotADirectoryError(errno.ENOTDIR, "trust anchor is not a directory", str(anchor))
    stable_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink")
    if any(getattr(metadata_before_open, field) != getattr(metadata, field) for field in stable_fields):
        os.close(descriptor)
        raise OSError(errno.ESTALE, "trust anchor changed during preflight", str(anchor))
    return descriptor, metadata


def _walk_ancestors(
    *,
    receipt: Path,
    trust_anchor: Path,
    owner_uid: int,
    findings: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], int | None]:
    """Return metadata for the receipt parent and its open descriptor.

    Each component under the anchor is opened by descriptor with ``O_NOFOLLOW``
    so this diagnostic can name an unsafe ancestor without reading the receipt.
    The caller owns a non-``None`` returned descriptor.
    """

    ancestors: list[dict[str, Any]] = []
    issuer_directory = receipt.parent
    try:
        descriptor, metadata = _open_anchor(trust_anchor)
    except OSError as exc:
        _finding(
            findings,
            code="trust_anchor_cannot_open",
            path=trust_anchor,
            scope="hardened_path",
            remediation="Use a real, accessible, owner-controlled directory as the trust anchor.",
            detail=json.dumps(_os_error_detail(exc), sort_keys=True),
        )
        return ancestors, None

    current = trust_anchor
    _append_ancestor_metadata(
        ancestors,
        findings,
        path=current,
        metadata=metadata,
        owner_uid=owner_uid,
        issuer_directory=issuer_directory,
    )
    anchor_parts = trust_anchor.parts
    receipt_parent_parts = issuer_directory.parts
    for part in receipt_parent_parts[len(anchor_parts) :]:
        next_path = current / part
        try:
            child_lstat = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
        except OSError as exc:
            code = "ancestor_missing" if exc.errno == errno.ENOENT else "ancestor_metadata_unavailable"
            _finding(
                findings,
                code=code,
                path=next_path,
                scope="hardened_path",
                remediation=(
                    "Stop approval issuance and repair this path component as a real owner-controlled directory; "
                    "do not replace a symlink in place."
                ),
                detail=json.dumps(_os_error_detail(exc), sort_keys=True),
            )
            os.close(descriptor)
            return ancestors, None
        if stat.S_ISLNK(child_lstat.st_mode):
            _finding(
                findings,
                code="ancestor_symlink",
                path=next_path,
                scope="hardened_path",
                remediation=(
                    "Stop approval issuance and replace the symlink with a verified real owner-controlled directory; "
                    "do not let the approval path traverse it."
                ),
            )
            os.close(descriptor)
            return ancestors, None
        if not stat.S_ISDIR(child_lstat.st_mode):
            _append_ancestor_metadata(
                ancestors,
                findings,
                path=next_path,
                metadata=child_lstat,
                owner_uid=owner_uid,
                issuer_directory=issuer_directory,
            )
            os.close(descriptor)
            return ancestors, None
        try:
            child_descriptor = os.open(part, _directory_flags(), dir_fd=descriptor)
        except OSError as exc:
            code = (
                "ancestor_missing"
                if exc.errno == errno.ENOENT
                else "ancestor_not_directory"
                if exc.errno == errno.ENOTDIR
                else "ancestor_cannot_open"
            )
            _finding(
                findings,
                code=code,
                path=next_path,
                scope="hardened_path",
                remediation=(
                    "Stop approval issuance and repair this path component as a real owner-controlled directory; "
                    "do not replace a symlink in place."
                ),
                detail=json.dumps(_os_error_detail(exc), sort_keys=True),
            )
            os.close(descriptor)
            return ancestors, None
        os.close(descriptor)
        descriptor = child_descriptor
        metadata = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink")
        if any(getattr(child_lstat, field) != getattr(metadata, field) for field in stable_fields):
            _finding(
                findings,
                code="ancestor_changed_during_preflight",
                path=next_path,
                scope="hardened_path",
                remediation="Stop approval issuance, investigate the filesystem change, then rerun the preflight.",
            )
            os.close(descriptor)
            return ancestors, None
        current = next_path
        _append_ancestor_metadata(
            ancestors,
            findings,
            path=current,
            metadata=metadata,
            owner_uid=owner_uid,
            issuer_directory=issuer_directory,
        )
    return ancestors, descriptor


def _inspect_receipt_leaf(
    *,
    receipt: Path,
    parent_descriptor: int,
    owner_uid: int,
    max_size: int,
    findings: list[dict[str, str]],
) -> dict[str, Any] | None:
    try:
        before = os.stat(receipt.name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        _finding(
            findings,
            code="receipt_missing"
            if exc.errno == errno.ENOENT
            else "receipt_metadata_unavailable",
            path=receipt,
            scope="runtime",
            remediation=(
                "Restore the original signed enrollment receipt through the approved recovery/enrollment procedure; "
                "do not hand-create a replacement receipt."
            ),
            detail=json.dumps(_os_error_detail(exc), sort_keys=True),
        )
        return None

    leaf = _metadata(receipt, before)
    leaf["owner_matches_expected_uid"] = before.st_uid == owner_uid
    leaf["group_or_world_accessible"] = bool(stat.S_IMODE(before.st_mode) & 0o077)
    leaf["single_hard_link"] = before.st_nlink == 1
    leaf["within_max_size"] = 0 <= before.st_size <= max_size
    if stat.S_ISLNK(before.st_mode):
        _finding(
            findings,
            code="receipt_symlink",
            path=receipt,
            scope="runtime",
            remediation="Use the original regular receipt file; the approval runtime intentionally refuses symlinks.",
        )
        return leaf
    if not stat.S_ISREG(before.st_mode):
        _finding(
            findings,
            code="receipt_not_regular_file",
            path=receipt,
            scope="runtime",
            remediation="Restore a regular receipt file through the approved enrollment recovery procedure.",
            detail=f"observed_kind={leaf['kind']}",
        )
        return leaf

    try:
        descriptor = os.open(receipt.name, _receipt_flags(), dir_fd=parent_descriptor)
    except OSError as exc:
        _finding(
            findings,
            code="receipt_cannot_open",
            path=receipt,
            scope="runtime",
            remediation="Repair the reported local path/permission condition, then rerun this preflight before issuing a token.",
            detail=json.dumps(_os_error_detail(exc), sort_keys=True),
        )
        return leaf
    try:
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    stable_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        _finding(
            findings,
            code="receipt_changed_during_preflight",
            path=receipt,
            scope="runtime",
            remediation="Stop approval issuance, investigate the local filesystem change, then rerun the preflight.",
        )
        return leaf
    if after.st_uid != owner_uid:
        _finding(
            findings,
            code="receipt_owner_mismatch",
            path=receipt,
            scope="runtime",
            remediation=(
                f"Have the authorized host administrator verify the receipt and set uid {owner_uid} "
                "only if it is the original approved artifact."
            ),
            detail=f"expected_uid={owner_uid}; observed_uid={after.st_uid}",
        )
    if stat.S_IMODE(after.st_mode) & 0o077:
        _finding(
            findings,
            code="receipt_group_or_world_accessible",
            path=receipt,
            scope="runtime",
            remediation="Remove group/world permissions from the verified original receipt before retrying.",
            detail=f"observed_mode={_mode_octal(after)}",
        )
    if after.st_nlink != 1:
        _finding(
            findings,
            code="receipt_hard_link_count_invalid",
            path=receipt,
            scope="runtime",
            remediation="Remove the unintended hard link only after verifying the original receipt's custody.",
            detail=f"expected_hard_link_count=1; observed_hard_link_count={after.st_nlink}",
        )
    if after.st_size < 0 or after.st_size > max_size:
        _finding(
            findings,
            code="receipt_exceeds_max_size",
            path=receipt,
            scope="runtime",
            remediation="Restore the original bounded receipt; do not truncate or edit the signed file manually.",
            detail=f"max_size_bytes={max_size}",
        )
    return leaf


def diagnose_bootstrap_receipt(
    receipt: Path,
    *,
    owner_uid: int | None = None,
    max_size: int = DEFAULT_MAX_SIZE,
    trust_anchor: Path = Path("/"),
) -> dict[str, Any]:
    """Return a non-secret local readiness report for one bootstrap receipt.

    The function intentionally has no exception path for an expected malformed
    local path.  Such conditions appear as structured ``findings`` so a
    preflight caller can give the operator one precise next action.
    """

    expected_uid = os.geteuid() if owner_uid is None else int(owner_uid)
    findings: list[dict[str, str]] = []
    receipt = Path(receipt)
    trust_anchor = Path(trust_anchor)
    report: dict[str, Any] = {
        "schema": DIAGNOSTIC_SCHEMA,
        "status": "blocked",
        "local_only": True,
        "payload_read": False,
        "receipt_path": str(receipt),
        "issuer_directory": str(receipt.parent),
        "trust_anchor": str(trust_anchor),
        "expected_owner_uid": expected_uid,
        "max_size_bytes": max_size,
        "ancestors": [],
        "receipt": None,
        "findings": findings,
    }
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _finding(
            findings,
            code="secure_open_flags_unavailable",
            path=receipt,
            scope="diagnostic",
            remediation="Run this preflight on the supported Linux control host with O_NOFOLLOW and O_DIRECTORY support.",
        )
        return report
    if expected_uid < 0:
        _finding(
            findings,
            code="expected_owner_uid_invalid",
            path=receipt,
            scope="diagnostic",
            remediation="Use the non-negative uid of the account that will run approval issuance.",
        )
        return report
    if max_size <= 0:
        _finding(
            findings,
            code="max_size_invalid",
            path=receipt,
            scope="diagnostic",
            remediation="Use a positive maximum receipt size.",
        )
        return report
    if _reject_noncanonical_path(receipt, label="receipt", findings=findings):
        return report
    if _reject_noncanonical_path(trust_anchor, label="trust_anchor", findings=findings):
        return report
    if receipt == Path("/") or receipt.parent == receipt:
        _finding(
            findings,
            code="receipt_path_invalid",
            path=receipt,
            scope="diagnostic",
            remediation="Use a receipt file below an issuer directory.",
        )
        return report
    if receipt.parts[: len(trust_anchor.parts)] != trust_anchor.parts:
        _finding(
            findings,
            code="receipt_outside_trust_anchor",
            path=receipt,
            scope="diagnostic",
            remediation="Use a trust anchor that is an ancestor of the receipt path.",
        )
        return report

    ancestors, parent_descriptor = _walk_ancestors(
        receipt=receipt,
        trust_anchor=trust_anchor,
        owner_uid=expected_uid,
        findings=findings,
    )
    report["ancestors"] = ancestors
    if parent_descriptor is not None:
        try:
            report["receipt"] = _inspect_receipt_leaf(
                receipt=receipt,
                parent_descriptor=parent_descriptor,
                owner_uid=expected_uid,
                max_size=max_size,
                findings=findings,
            )
        finally:
            os.close(parent_descriptor)

    runtime_findings = [finding for finding in findings if finding["scope"] == "runtime"]
    hardened_findings = [finding for finding in findings if finding["scope"] == "hardened_path"]
    report["runtime_readiness"] = "blocked" if runtime_findings else "ready"
    report["hardened_path_readiness"] = "blocked" if hardened_findings else "ready"
    report["status"] = "ready" if not findings else "blocked"
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Metadata-only local preflight for a human-approval bootstrap receipt."
    )
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT_PATH)
    parser.add_argument(
        "--owner-uid",
        type=int,
        default=os.geteuid(),
        help="uid of the process that will run approval issuance (default: current euid)",
    )
    parser.add_argument("--max-size-bytes", type=int, default=DEFAULT_MAX_SIZE)
    parser.add_argument(
        "--trust-anchor",
        type=Path,
        default=Path("/"),
        help="verified ancestor boundary; default '/' checks every ancestor",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    report = diagnose_bootstrap_receipt(
        args.receipt,
        owner_uid=args.owner_uid,
        max_size=args.max_size_bytes,
        trust_anchor=args.trust_anchor,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":  # pragma: no cover - exercised through main().
    raise SystemExit(main())
