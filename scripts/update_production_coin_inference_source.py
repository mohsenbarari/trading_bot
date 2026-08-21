#!/usr/bin/env python3
"""Atomically apply the approved coin-inference rollout profile to production.

The immutable source env is discovered from the production deploy manifest.  An
apply is compare-and-swap bound to the operator-provided source digest, creates
a private byte-for-byte backup, and emits a value-free receipt.  No environment
values are loaded into the process environment or written to stdout.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Iterator, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
APPROVED_MANIFEST_PATH = REPO_ROOT / "deploy" / "production" / "online.env"
APPLY_CONFIRMATION = "activate-production-coin-inference-guarded-rollout"
ROLLBACK_CONFIRMATION = "deactivate-production-coin-inference-guarded-rollout"
RELAY_CONFIRMATION = "publish-production-coin-inference-snapshot"
LOCK_NAME = ".production-runtime-source.lock"
PENDING_NAME = ".production-runtime-source.pending.json"
APPROVED_UPDATES: Mapping[str, str] = {
    "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED": "true",
    "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED": "true",
    "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED": "false",
    "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED": "true",
    "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS": "120",
}
ROLLBACK_UPDATES: Mapping[str, str] = {
    "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED": "false",
    "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED": "false",
    "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED": "false",
    "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED": "false",
    "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS": "120",
}
_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SourceUpdateError(RuntimeError):
    """The immutable-source mutation contract was not satisfied."""


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _secure_file(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise SourceUpdateError(f"{label}_invalid")
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as exc:
        raise SourceUpdateError(f"{label}_invalid") from exc
    if (
        resolved != path
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid not in {0, os.geteuid()}
        or metadata.st_nlink != 1
    ):
        raise SourceUpdateError(f"{label}_invalid")
    _secure_directory(path.parent, label=f"{label}_parent")
    return path


def _require_production_scope(path: Path, *, label: str) -> None:
    lowered = tuple(part.lower() for part in path.parts)
    if (
        path == REPO_ROOT
        or REPO_ROOT in path.parents
        or any("staging" in part for part in lowered)
        or not any("production" in part for part in lowered)
    ):
        raise SourceUpdateError(f"{label}_scope_invalid")


def _secure_directory(path: Path, *, label: str, create: bool = False) -> Path:
    if not path.is_absolute():
        raise SourceUpdateError(f"{label}_invalid")
    if create and not path.exists():
        path.mkdir(mode=0o700, parents=True)
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as exc:
        raise SourceUpdateError(f"{label}_invalid") from exc
    if (
        path.is_symlink()
        or resolved != path
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SourceUpdateError(f"{label}_invalid")
    return path


def _manifest_values(manifest: Path) -> dict[str, str]:
    if not manifest.is_absolute() or manifest.is_symlink():
        raise SourceUpdateError("manifest_invalid")
    try:
        supplied_metadata = manifest.lstat()
        manifest = manifest.resolve(strict=True)
        approved = APPROVED_MANIFEST_PATH.resolve(strict=True)
    except OSError as exc:
        raise SourceUpdateError("manifest_invalid") from exc
    if manifest != approved:
        raise SourceUpdateError("manifest_identity_invalid")
    if (
        not stat.S_ISREG(supplied_metadata.st_mode)
        or stat.S_IMODE(supplied_metadata.st_mode) != 0o600
        or supplied_metadata.st_uid not in {0, os.geteuid()}
        or supplied_metadata.st_nlink != 1
    ):
        raise SourceUpdateError("manifest_invalid")
    values: dict[str, str] = {}
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in values:
            raise SourceUpdateError("manifest_duplicate_key")
        values[normalized_key] = value.strip()
    return values


def _manifest_source(manifest: Path) -> Path:
    values = _manifest_values(manifest)
    source_value = values.get("RUNTIME_ENV_SOURCE_PATH", "")
    if not source_value:
        raise SourceUpdateError("manifest_source_contract_invalid")
    source = Path(source_value)
    if not source.is_absolute():
        raise SourceUpdateError("manifest_source_contract_invalid")
    source = _secure_file(source, label="immutable_source")
    _require_production_scope(source, label="immutable_source")
    return source


def _require_relay_activation_contract(manifest: Path) -> None:
    values = _manifest_values(manifest)
    if (
        values.get("PRODUCTION_COIN_INFERENCE_RELAY_ENABLED") != "1"
        or values.get("PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM")
        != RELAY_CONFIRMATION
    ):
        raise SourceUpdateError("production_snapshot_relay_activation_required")


def _updated_payload(
    payload: bytes,
    updates: Mapping[str, str],
) -> tuple[bytes, list[str]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceUpdateError("immutable_source_encoding_invalid") from exc
    lines = text.splitlines(keepends=True)
    seen: dict[str, int] = {}
    changed: list[str] = []
    for index, raw_line in enumerate(lines):
        stripped = raw_line.rstrip("\r\n")
        if not stripped or stripped.lstrip().startswith("#") or "=" not in stripped:
            continue
        key, _value = stripped.split("=", 1)
        key = key.strip()
        if not _KEY_PATTERN.fullmatch(key) or key not in updates:
            continue
        if key in seen:
            raise SourceUpdateError("immutable_source_duplicate_rollout_key")
        seen[key] = index
        newline = "\r\n" if raw_line.endswith("\r\n") else "\n"
        replacement = f"{key}={updates[key]}{newline}"
        if raw_line != replacement:
            lines[index] = replacement
            changed.append(key)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n"
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}\n")
            changed.append(key)
    return "".join(lines).encode("utf-8"), sorted(changed)


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes, *, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_parent(path)
    finally:
        temporary.unlink(missing_ok=True)


def _exclusive_write(path: Path, payload: bytes, *, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    _fsync_parent(path)


@contextmanager
def _source_lock(source: Path) -> Iterator[None]:
    lock_path = source.parent / LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise SourceUpdateError("immutable_source_lock_invalid") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid not in {0, os.geteuid()}
            or metadata.st_nlink != 1
        ):
            raise SourceUpdateError("immutable_source_lock_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourceUpdateError("immutable_source_update_locked") from exc
        yield
    finally:
        os.close(descriptor)


def _pending_path(source: Path) -> Path:
    return source.parent / PENDING_NAME


def _remove_pending(path: Path) -> None:
    path.unlink(missing_ok=True)
    _fsync_parent(path)


def _emit(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def _plan(
    source: Path,
    updates: Mapping[str, str],
) -> tuple[bytes, bytes, list[str]]:
    before = source.read_bytes()
    after, changed = _updated_payload(before, updates)
    return before, after, changed


def _apply(
    args: argparse.Namespace,
    source: Path,
    *,
    updates: Mapping[str, str],
    confirmation: str,
    action: str,
) -> int:
    if args.confirm != confirmation:
        raise SourceUpdateError("apply_confirmation_required")
    if not _DIGEST_PATTERN.fullmatch(args.expected_source_sha256 or ""):
        raise SourceUpdateError("expected_source_sha256_invalid")
    backup_dir = Path(args.backup_dir)
    receipt_path = Path(args.receipt)
    if not backup_dir.is_absolute() or not receipt_path.is_absolute():
        raise SourceUpdateError("receipt_path_invalid")
    backup_dir = _secure_directory(backup_dir, label="backup_directory", create=True)
    _require_production_scope(backup_dir, label="backup_directory")
    _secure_directory(receipt_path.parent, label="receipt_directory", create=True)
    if receipt_path.resolve(strict=False) != receipt_path:
        raise SourceUpdateError("receipt_path_invalid")
    _require_production_scope(receipt_path, label="receipt")
    if os.path.lexists(receipt_path):
        raise SourceUpdateError("receipt_path_invalid")

    pending = _pending_path(source)
    protected_paths = {
        source,
        APPROVED_MANIFEST_PATH.resolve(strict=True),
        source.parent / LOCK_NAME,
        pending,
    }
    if receipt_path in protected_paths:
        raise SourceUpdateError("receipt_path_alias")
    with _source_lock(source):
        if pending.exists() or pending.is_symlink():
            raise SourceUpdateError("source_update_pending_recovery_required")
        before, after, changed = _plan(source, updates)
        before_digest = _digest(before)
        if before_digest != args.expected_source_sha256:
            raise SourceUpdateError("immutable_source_cas_mismatch")
        if not changed:
            receipt = {
                "schema_version": 1,
                "action": action,
                "status": "UNCHANGED",
                "source_sha256_before": before_digest,
                "source_sha256_after": before_digest,
                "backup_sha256": None,
                "changed_keys": [],
                "secrets_disclosed": False,
            }
            _exclusive_write(receipt_path, (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode(), mode=0o600)
            _emit(receipt)
            return 0

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = backup_dir / f"production-runtime-source.{stamp}.{before_digest[:12]}.env"
        if backup.exists():
            raise SourceUpdateError("backup_collision")
        _exclusive_write(backup, before, mode=0o600)
        if _digest(backup.read_bytes()) != before_digest:
            raise SourceUpdateError("backup_digest_mismatch")
        after_digest_expected = _digest(after)
        prepared = {
            "schema_version": 1,
            "action": action,
            "status": "PREPARED",
            "source_sha256_before": before_digest,
            "source_sha256_after": after_digest_expected,
            "backup_sha256": before_digest,
            "changed_keys": changed,
            "secrets_disclosed": False,
            "recovery_action": "restore_source_only_when_current_digest_matches_source_sha256_after",
        }
        _exclusive_write(pending, (json.dumps(prepared, sort_keys=True, separators=(",", ":")) + "\n").encode(), mode=0o600)
        if _digest(source.read_bytes()) != before_digest:
            _remove_pending(pending)
            raise SourceUpdateError("immutable_source_cas_mismatch")
        _atomic_write(source, after, mode=0o600)
        after_digest = _digest(source.read_bytes())
        if after_digest != after_digest_expected:
            raise SourceUpdateError("immutable_source_post_install_digest_mismatch")
        receipt = {
            "schema_version": 1,
            "action": action,
            "status": "APPLIED",
            "source_sha256_before": before_digest,
            "source_sha256_after": after_digest,
            "backup_sha256": before_digest,
            "changed_keys": changed,
            "secrets_disclosed": False,
        }
        try:
            _exclusive_write(receipt_path, (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode(), mode=0o600)
        except OSError:
            # Roll back only the exact bytes this transaction installed.  If a
            # non-cooperating writer changed them, retain the durable pending
            # marker so production preflight fails closed.
            if _digest(source.read_bytes()) == after_digest:
                _atomic_write(source, before, mode=0o600)
                if _digest(source.read_bytes()) == before_digest:
                    _remove_pending(pending)
            raise
        _remove_pending(pending)
        _emit(receipt)
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command_name in ("plan", "apply", "rollback"):
        command = subparsers.add_parser(command_name)
        command.add_argument("--manifest", required=True)
        if command_name in {"apply", "rollback"}:
            command.add_argument("--expected-source-sha256", required=True)
            command.add_argument("--confirm", required=True)
            command.add_argument("--backup-dir", required=True)
            command.add_argument("--receipt", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = _manifest_source(Path(args.manifest))
        if args.command == "apply":
            if args.confirm == APPLY_CONFIRMATION:
                _require_relay_activation_contract(Path(args.manifest))
            return _apply(
                args,
                source,
                updates=APPROVED_UPDATES,
                confirmation=APPLY_CONFIRMATION,
                action="ENABLE_GUARDED_INFERENCE",
            )
        if args.command == "rollback":
            return _apply(
                args,
                source,
                updates=ROLLBACK_UPDATES,
                confirmation=ROLLBACK_CONFIRMATION,
                action="DISABLE_GUARDED_INFERENCE",
            )
        before, after, changed = _plan(source, APPROVED_UPDATES)
        _emit(
            {
                "schema_version": 1,
                "status": "PLAN",
                "source_sha256_before": _digest(before),
                "source_sha256_after": _digest(after),
                "changed_keys": changed,
                "secrets_disclosed": False,
            }
        )
        return 0
    except (OSError, SourceUpdateError) as exc:
        reason = str(exc) if isinstance(exc, SourceUpdateError) else type(exc).__name__
        _emit({"schema_version": 1, "status": "FAILED", "reason": reason})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
