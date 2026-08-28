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
APPROVED_MANIFEST_PATH = Path(
    os.environ.get(
        "DEPLOY_MANIFEST",
        str(REPO_ROOT / "deploy" / "production" / "online.env"),
    )
)
APPROVED_MANIFEST_ROOTS = (
    Path("/root/secure-envs/trading-bot/release-control"),
)
APPLY_CONFIRMATION = "activate-production-coin-inference-guarded-rollout"
ROLLBACK_CONFIRMATION = "deactivate-production-coin-inference-guarded-rollout"
PRIVATE_PRIMARY_APPLY_CONFIRMATION = "activate-production-private-primary-snapshots"
PRIVATE_PRIMARY_ROLLBACK_CONFIRMATION = "restore-production-legacy-snapshots"
PRIVATE_PRIMARY_RECOVERY_CONFIRMATION = (
    "recover-production-private-primary-source-update"
)
RELAY_CONFIRMATION = "publish-production-coin-inference-snapshot"
PROMOTION_RECEIPT_SCHEMA = "production_private_primary_promotion_verification/1.0"
PROMOTION_SNAPSHOT_CONTRACT = "estimator_snapshot_web_view/1.0"
PROMOTION_MAXIMUM_AGE_SECONDS = 120
PROMOTION_REQUIRED_CHECKS = (
    "release_and_image_binding",
    "bluegreen_journals_pass",
    "single_owner_topology",
    "contiguous_sequences_and_ack",
    "idempotent_duplicates_and_zero_rejected_dead_open_outbox",
    "receiver_publication_settled",
    "private_primary_snapshot_contract",
    "fourteen_estimated_rates",
    "effective_underlying_freshness",
    "bot_web_snapshot_identity_and_digest",
    "owner_authorized_backfill_scope_bound",
    "catchup_complete_and_live_tail_verified",
)
AUTHORIZED_BACKFILL_NOT_BEFORE_UTC = "2026-08-25T09:33:00Z"
AUTHORIZED_BACKFILL_SOURCE_CODES = (
    "MELTED_PRIMARY_FLOW",
    "GROUP_1",
    "GROUP_2",
)
AUTHORIZED_BACKFILL_MIN_MESSAGES = 2_000
AUTHORIZED_BACKFILL_MAX_MESSAGES = 250_000
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
PRIVATE_PRIMARY_UPDATES: Mapping[str, str] = {
    "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE": "PRIVATE_PRIMARY",
    "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS": "120",
    "PRODUCTION_PRODUCT_ESTIMATOR_APP_SNAPSHOT_HOST_DIR": (
        "/srv/trading-bot/production-data/market-pipeline/snapshots"
    ),
    "PRODUCTION_PRODUCT_ESTIMATOR_BOT_SNAPSHOT_HOST_DIR": (
        "/srv/trading-bot/production-data/market-pipeline/snapshots"
    ),
    "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_SNAPSHOT_HOST_DIR": (
        "/srv/trading-bot/market-data-production/snapshots"
    ),
    "PRODUCTION_PRODUCT_ESTIMATOR_APP_PRIVATE_PRIMARY_SNAPSHOT_PATH": (
        "/app/runtime/product-estimator/latest-private-primary.json"
    ),
    "PRODUCTION_PRODUCT_ESTIMATOR_BOT_PRIVATE_PRIMARY_SNAPSHOT_PATH": (
        "/app/runtime/product-estimator/latest-private-primary.json"
    ),
    "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_PRIVATE_PRIMARY_SNAPSHOT_PATH": (
        "/app/runtime/product-estimator/latest-private-primary.json"
    ),
}
_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PENDING_SCHEMA_VERSION = 2


class SourceUpdateError(RuntimeError):
    """The immutable-source mutation contract was not satisfied."""


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _path_digest(path: Path) -> str:
    return _digest(str(path.resolve(strict=False)).encode("utf-8"))


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


def _require_manifest_scope(path: Path) -> None:
    if not any(
        path.parent == root or root in path.parents
        for root in APPROVED_MANIFEST_ROOTS
    ):
        raise SourceUpdateError("manifest_scope_invalid")


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


def _promotion_binding(
    args: argparse.Namespace, *, require_fresh: bool = True
) -> dict[str, object]:
    receipt = _secure_file(Path(args.promotion_receipt), label="promotion_receipt")
    _require_production_scope(receipt, label="promotion_receipt")
    expected_digest = args.expected_promotion_receipt_sha256 or ""
    if not _DIGEST_PATTERN.fullmatch(expected_digest):
        raise SourceUpdateError("promotion_receipt_digest_invalid")
    payload = receipt.read_bytes()
    actual_digest = _digest(payload)
    if actual_digest != expected_digest:
        raise SourceUpdateError("promotion_receipt_cas_mismatch")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceUpdateError("promotion_receipt_invalid") from exc
    release_sha = args.expected_release_sha or ""
    release_tree = args.expected_release_tree or ""
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        raise SourceUpdateError("expected_release_sha_invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", release_tree):
        raise SourceUpdateError("expected_release_tree_invalid")
    snapshot = document.get("snapshot") if isinstance(document, dict) else None
    capture_backfill = (
        document.get("capture_backfill") if isinstance(document, dict) else None
    )
    catchup_verification = (
        document.get("catchup_verification")
        if isinstance(document, dict)
        else None
    )
    checks = document.get("checks") if isinstance(document, dict) else None
    created_at = document.get("created_at_utc") if isinstance(document, dict) else None
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise SourceUpdateError("promotion_receipt_time_invalid")
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceUpdateError("promotion_receipt_time_invalid") from exc
    if created.tzinfo is None:
        raise SourceUpdateError("promotion_receipt_time_invalid")
    receipt_age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
    if receipt_age < 0 or (
        require_fresh and receipt_age > PROMOTION_MAXIMUM_AGE_SECONDS
    ):
        raise SourceUpdateError("promotion_receipt_stale_or_future")

    def fresh_number(value: object) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and 0 <= float(value)
            and (
                not require_fresh
                or float(value) + receipt_age <= PROMOTION_MAXIMUM_AGE_SECONDS
            )
        )
    if (
        not isinstance(document, dict)
        or document.get("schema") != PROMOTION_RECEIPT_SCHEMA
        or document.get("status") != "PASS"
        or document.get("release_sha") != release_sha
        or document.get("release_tree") != release_tree
        or document.get("maximum_age_seconds") != PROMOTION_MAXIMUM_AGE_SECONDS
        or document.get("read_only_runtime_verification") is not True
        or document.get("product_or_runtime_mutated") is not False
        or document.get("payload_values_included") is not False
        or document.get("pii_included") is not False
        or document.get("secrets_disclosed") is not False
        or checks != list(PROMOTION_REQUIRED_CHECKS)
        or not isinstance(catchup_verification, dict)
        or set(catchup_verification) != {"receipt_sha256", "age_seconds"}
        or not _DIGEST_PATTERN.fullmatch(
            str(catchup_verification.get("receipt_sha256") or "")
        )
        or isinstance(catchup_verification.get("age_seconds"), bool)
        or not isinstance(catchup_verification.get("age_seconds"), (int, float))
        or not 0 <= float(catchup_verification["age_seconds"])
        <= PROMOTION_MAXIMUM_AGE_SECONDS
        or not isinstance(capture_backfill, dict)
        or set(capture_backfill)
        != {"not_before_utc", "source_codes", "max_messages"}
        or capture_backfill.get("not_before_utc")
        != AUTHORIZED_BACKFILL_NOT_BEFORE_UTC
        or capture_backfill.get("source_codes")
        != list(AUTHORIZED_BACKFILL_SOURCE_CODES)
        or isinstance(capture_backfill.get("max_messages"), bool)
        or not isinstance(capture_backfill.get("max_messages"), int)
        or not AUTHORIZED_BACKFILL_MIN_MESSAGES
        <= int(capture_backfill["max_messages"])
        <= AUTHORIZED_BACKFILL_MAX_MESSAGES
        or not isinstance(snapshot, dict)
        or snapshot.get("contract") != PROMOTION_SNAPSHOT_CONTRACT
        or snapshot.get("lane") != "PRIVATE_PRIMARY"
        or snapshot.get("status") != "OK"
        or snapshot.get("estimated_rate_count") != 14
        or not _DIGEST_PATTERN.fullmatch(str(snapshot.get("snapshot_hash") or ""))
        or isinstance(snapshot.get("snapshot_version"), bool)
        or not isinstance(snapshot.get("snapshot_version"), int)
        or int(snapshot["snapshot_version"]) < 1
        or not _DIGEST_PATTERN.fullmatch(str(snapshot.get("file_sha256") or ""))
        or not fresh_number(snapshot.get("maximum_effective_underlying_age_seconds"))
        or not fresh_number(snapshot.get("snapshot_age_seconds"))
        or not fresh_number(snapshot.get("publication_age_seconds"))
    ):
        raise SourceUpdateError("promotion_receipt_contract_invalid")
    # Close the read/validation race before source mutation.  The receipt is
    # immutable owner-only evidence and its digest is carried into activation.
    if _digest(receipt.read_bytes()) != actual_digest:
        raise SourceUpdateError("promotion_receipt_cas_mismatch")
    return {
        "promotion_receipt_sha256": actual_digest,
        "release_sha": release_sha,
        "release_tree": release_tree,
    }


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
def _source_lock(source: Path) -> Iterator[int]:
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
        yield descriptor
    finally:
        os.close(descriptor)


def _verify_inherited_source_lock(source: Path, descriptor: int) -> os.stat_result:
    """Prove that ``descriptor`` owns the canonical immutable-source flock.

    Merely observing that *someone* holds the lock is insufficient.  A fresh
    open must be blocked while reaffirming the exclusive lock through the
    supplied open-file description must succeed.  The inode and security
    properties are checked on both the path and descriptor.
    """

    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 0:
        raise SourceUpdateError("inherited_source_lock_invalid")
    lock_path = source.parent / LOCK_NAME
    if lock_path.is_symlink():
        raise SourceUpdateError("inherited_source_lock_invalid")
    try:
        path_metadata = lock_path.lstat()
        descriptor_metadata = os.fstat(descriptor)
    except OSError as exc:
        raise SourceUpdateError("inherited_source_lock_invalid") from exc
    if (
        not stat.S_ISREG(path_metadata.st_mode)
        or path_metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(path_metadata.st_mode) != 0o600
        or path_metadata.st_nlink != 1
        or path_metadata.st_dev != descriptor_metadata.st_dev
        or path_metadata.st_ino != descriptor_metadata.st_ino
    ):
        raise SourceUpdateError("inherited_source_lock_invalid")
    probe = os.open(
        lock_path,
        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(probe, fcntl.LOCK_UN)
            raise SourceUpdateError("inherited_source_lock_not_held")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourceUpdateError("inherited_source_lock_not_owned") from exc
    finally:
        os.close(probe)
    return descriptor_metadata


@contextmanager
def _source_mutation_lock(
    source: Path,
    inherited_source_lock_descriptor: int | None,
) -> Iterator[None]:
    if inherited_source_lock_descriptor is None:
        with _source_lock(source):
            yield
        return
    before = _verify_inherited_source_lock(
        source, inherited_source_lock_descriptor
    )
    duplicate = os.dup(inherited_source_lock_descriptor)
    try:
        duplicate_metadata = os.fstat(duplicate)
        if (
            duplicate_metadata.st_dev != before.st_dev
            or duplicate_metadata.st_ino != before.st_ino
        ):
            raise SourceUpdateError("inherited_source_lock_invalid")
        yield
        # The duplicate shares the caller's open-file description and keeps
        # the canonical flock held for the entire mutation even if the caller
        # accidentally closes its original descriptor concurrently.  Verify
        # the descriptor we control before releasing that final reference.
        after = _verify_inherited_source_lock(source, duplicate)
        if after.st_dev != before.st_dev or after.st_ino != before.st_ino:
            raise SourceUpdateError("inherited_source_lock_changed")
    finally:
        os.close(duplicate)


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


def _source_values(payload: bytes) -> dict[str, str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise SourceUpdateError("immutable_source_encoding_invalid") from exc
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in values:
            raise SourceUpdateError("immutable_source_duplicate_key")
        values[key] = value.strip()
    return values


def _require_product_mode_transition(
    before: bytes,
    *,
    action: str,
    changed: Sequence[str],
) -> None:
    if action not in {
        "ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS",
        "RESTORE_LEGACY_PRODUCT_SNAPSHOTS",
    }:
        return
    current = _source_values(before).get(
        "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE", "LEGACY"
    ).strip().upper()
    if action == "ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS":
        if current == "LEGACY":
            return
        if current == "PRIVATE_PRIMARY" and not changed:
            return
        raise SourceUpdateError("private_primary_source_transition_invalid")
    if current == "PRIVATE_PRIMARY":
        return
    if current == "LEGACY" and not changed:
        return
    raise SourceUpdateError("legacy_source_transition_invalid")


def _pending_transaction(
    *,
    action: str,
    source: Path,
    source_sha256_before: str,
    source_sha256_after: str,
    manifest: Path,
    manifest_sha256: str,
    receipt_path: Path,
    receipt: Mapping[str, object],
    backup: Path,
    backup_sha256: str,
) -> dict[str, object]:
    """Build the value-free WAL written before an immutable-source mutation."""

    receipt_payload = dict(receipt)
    binding: dict[str, object] = {
        "schema_version": _PENDING_SCHEMA_VERSION,
        "action": action,
        "status": "PREPARED",
        "source_path_sha256": _path_digest(source),
        "source_sha256_before": source_sha256_before,
        "source_sha256_after": source_sha256_after,
        "manifest_path_sha256": _path_digest(manifest),
        "manifest_sha256": manifest_sha256,
        "receipt_path_sha256": _path_digest(receipt_path),
        "receipt_sha256": _digest(_json_bytes(receipt_payload)),
        "receipt_payload": receipt_payload,
        "backup_file": backup.name,
        "backup_path_sha256": _path_digest(backup),
        "backup_sha256": backup_sha256,
        "secrets_disclosed": False,
    }
    transaction_binding = dict(binding)
    transaction_binding.pop("receipt_payload")
    binding["transaction_sha256"] = _digest(_json_bytes(transaction_binding))
    return binding


def _read_pending_transaction(
    source: Path,
    *,
    expected_pending_sha256: str,
    manifest: Path,
    receipt_path: Path,
    backup_dir: Path,
) -> tuple[Path, dict[str, object], bytes, bytes]:
    if not _DIGEST_PATTERN.fullmatch(expected_pending_sha256 or ""):
        raise SourceUpdateError("pending_digest_invalid")
    pending = _secure_file(_pending_path(source), label="source_update_pending")
    payload = pending.read_bytes()
    if _digest(payload) != expected_pending_sha256:
        raise SourceUpdateError("pending_cas_mismatch")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceUpdateError("pending_contract_invalid") from exc
    receipt = document.get("receipt_payload") if isinstance(document, dict) else None
    backup_name = document.get("backup_file") if isinstance(document, dict) else None
    transaction_sha256 = (
        document.get("transaction_sha256") if isinstance(document, dict) else None
    )
    if not isinstance(document, dict) or not isinstance(receipt, dict):
        raise SourceUpdateError("pending_contract_invalid")
    transaction_binding = dict(document)
    transaction_binding.pop("receipt_payload", None)
    transaction_binding.pop("transaction_sha256", None)
    if (
        document.get("schema_version") != _PENDING_SCHEMA_VERSION
        or document.get("status") != "PREPARED"
        or document.get("source_path_sha256") != _path_digest(source)
        or document.get("manifest_path_sha256") != _path_digest(manifest)
        or document.get("manifest_sha256") != _digest(manifest.read_bytes())
        or document.get("receipt_path_sha256") != _path_digest(receipt_path)
        or document.get("receipt_sha256") != _digest(_json_bytes(receipt))
        or not isinstance(backup_name, str)
        or Path(backup_name).name != backup_name
        or document.get("backup_path_sha256")
        != _path_digest(backup_dir / backup_name)
        or not _DIGEST_PATTERN.fullmatch(str(document.get("backup_sha256") or ""))
        or transaction_sha256 != _digest(_json_bytes(transaction_binding))
        or document.get("secrets_disclosed") is not False
    ):
        raise SourceUpdateError("pending_contract_invalid")
    if _manifest_source(manifest) != source:
        raise SourceUpdateError("manifest_source_identity_changed")
    for key in ("source_sha256_before", "source_sha256_after"):
        if not _DIGEST_PATTERN.fullmatch(str(document.get(key) or "")):
            raise SourceUpdateError("pending_contract_invalid")
    backup = _secure_file(backup_dir / backup_name, label="source_update_backup")
    if backup.parent != backup_dir:
        raise SourceUpdateError("pending_contract_invalid")
    backup_payload = backup.read_bytes()
    if _digest(backup_payload) != document.get("backup_sha256"):
        raise SourceUpdateError("pending_backup_digest_mismatch")
    # Close every read/validation race before returning to the lock holder.
    if _digest(pending.read_bytes()) != expected_pending_sha256:
        raise SourceUpdateError("pending_cas_mismatch")
    return pending, document, backup_payload, _json_bytes(receipt)


def _finish_pending_receipt(
    *,
    pending: Path,
    receipt_path: Path,
    receipt_payload: bytes,
    expected_receipt_sha256: str,
) -> None:
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = _secure_file(receipt_path, label="source_update_receipt")
        if _digest(receipt.read_bytes()) != expected_receipt_sha256:
            raise SourceUpdateError("pending_receipt_conflict")
    else:
        _exclusive_write(receipt_path, receipt_payload, mode=0o600)
    if _digest(receipt_path.read_bytes()) != expected_receipt_sha256:
        raise SourceUpdateError("pending_receipt_digest_mismatch")
    _remove_pending(pending)


def recover_private_primary_with_held_source_lock(
    args: argparse.Namespace,
    source: Path,
    *,
    source_lock_descriptor: int,
) -> dict[str, object]:
    """Recover one exact source WAL after SIGKILL.

    ``resume`` may install PRIVATE_PRIMARY only while the original promotion
    evidence remains fresh.  ``rollback`` never uses stale evidence as
    authority for a new promotion; it only restores/keeps the exact pre-image.
    A rollback transaction is always completed because restoring the exact
    activation backup is itself the safe recovery direction.
    """

    if args.recovery_confirm != PRIVATE_PRIMARY_RECOVERY_CONFIRMATION:
        raise SourceUpdateError("source_recovery_confirmation_required")
    disposition = str(args.recovery_action or "").strip().lower()
    if disposition not in {"resume", "rollback"}:
        raise SourceUpdateError("source_recovery_action_invalid")
    manifest = _secure_file(Path(args.manifest), label="manifest")
    _require_manifest_scope(manifest)
    backup_dir = _secure_directory(Path(args.backup_dir), label="backup_directory")
    _require_production_scope(backup_dir, label="backup_directory")
    receipt_path = Path(args.receipt)
    if not receipt_path.is_absolute() or receipt_path.resolve(strict=False) != receipt_path:
        raise SourceUpdateError("receipt_path_invalid")
    _secure_directory(receipt_path.parent, label="receipt_directory", create=True)
    _require_production_scope(receipt_path, label="receipt")
    with _source_mutation_lock(source, source_lock_descriptor):
        pending, wal, backup_payload, receipt_payload = _read_pending_transaction(
            source,
            expected_pending_sha256=args.expected_pending_sha256,
            manifest=manifest,
            receipt_path=receipt_path,
            backup_dir=backup_dir,
        )
        action = str(wal["action"])
        before_digest = str(wal["source_sha256_before"])
        after_digest = str(wal["source_sha256_after"])
        current = source.read_bytes()
        current_digest = _digest(current)
        if current_digest not in {before_digest, after_digest}:
            raise SourceUpdateError("pending_source_state_ambiguous")

        if action == "ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS":
            if disposition == "rollback":
                if current_digest == after_digest:
                    if _digest(backup_payload) != before_digest:
                        raise SourceUpdateError("pending_backup_digest_mismatch")
                    _atomic_write(source, backup_payload, mode=0o600)
                if _digest(source.read_bytes()) != before_digest:
                    raise SourceUpdateError("source_recovery_postcondition_failed")
                if receipt_path.exists() or receipt_path.is_symlink():
                    existing = _secure_file(
                        receipt_path, label="source_update_receipt"
                    )
                    if _digest(existing.read_bytes()) != wal["receipt_sha256"]:
                        raise SourceUpdateError("pending_receipt_conflict")
                _remove_pending(pending)
                return {
                    "schema_version": 1,
                    "action": action,
                    "status": "RECOVERED_ROLLED_BACK",
                    "source_sha256_after": before_digest,
                    "transaction_sha256": wal["transaction_sha256"],
                    "secrets_disclosed": False,
                }
            if current_digest == before_digest:
                # Re-installing the post-image is a new promotion authority;
                # exact but stale evidence is insufficient.
                binding = _promotion_binding(args, require_fresh=True)
                receipt = wal["receipt_payload"]
                for key, value in binding.items():
                    if receipt.get(key) != value:
                        raise SourceUpdateError("pending_evidence_binding_mismatch")
                after, changed = _updated_payload(current, PRIVATE_PRIMARY_UPDATES)
                if _digest(after) != after_digest or changed != receipt.get("changed_keys"):
                    raise SourceUpdateError("pending_replay_payload_mismatch")
                _atomic_write(source, after, mode=0o600)
        elif action == "RESTORE_EXACT_PRE_ACTIVATION_SOURCE":
            if disposition != "rollback":
                raise SourceUpdateError("source_recovery_action_invalid")
            if current_digest == before_digest:
                if _digest(backup_payload) != after_digest:
                    raise SourceUpdateError("pending_backup_digest_mismatch")
                _atomic_write(source, backup_payload, mode=0o600)
        else:
            raise SourceUpdateError("pending_action_invalid")

        if _digest(source.read_bytes()) != after_digest:
            raise SourceUpdateError("source_recovery_postcondition_failed")
        _finish_pending_receipt(
            pending=pending,
            receipt_path=receipt_path,
            receipt_payload=receipt_payload,
            expected_receipt_sha256=str(wal["receipt_sha256"]),
        )
        result = dict(wal["receipt_payload"])
        result["recovered_from_pending"] = True
        result["transaction_sha256"] = wal["transaction_sha256"]
        return result


def _apply(
    args: argparse.Namespace,
    source: Path,
    *,
    updates: Mapping[str, str],
    confirmation: str,
    action: str,
    evidence_binding: Mapping[str, object] | None = None,
    inherited_source_lock_descriptor: int | None = None,
) -> int:
    if args.confirm != confirmation:
        raise SourceUpdateError("apply_confirmation_required")
    if not _DIGEST_PATTERN.fullmatch(args.expected_source_sha256 or ""):
        raise SourceUpdateError("expected_source_sha256_invalid")
    if not _DIGEST_PATTERN.fullmatch(args.expected_manifest_sha256 or ""):
        raise SourceUpdateError("expected_manifest_sha256_invalid")
    manifest = _secure_file(Path(args.manifest), label="manifest")
    _require_manifest_scope(manifest)
    manifest_digest = _digest(manifest.read_bytes())
    if manifest_digest != args.expected_manifest_sha256:
        raise SourceUpdateError("manifest_cas_mismatch")
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
    with _source_mutation_lock(source, inherited_source_lock_descriptor):
        if _digest(manifest.read_bytes()) != manifest_digest:
            raise SourceUpdateError("manifest_cas_mismatch")
        if _manifest_source(manifest) != source:
            raise SourceUpdateError("manifest_source_identity_changed")
        if pending.exists() or pending.is_symlink():
            raise SourceUpdateError("source_update_pending_recovery_required")
        before, after, changed = _plan(source, updates)
        _require_product_mode_transition(
            before,
            action=action,
            changed=changed,
        )
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
                "manifest_sha256": manifest_digest,
                "changed_keys": [],
                "secrets_disclosed": False,
                **dict(evidence_binding or {}),
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
        receipt = {
            "schema_version": 1,
            "action": action,
            "status": "APPLIED",
            "source_sha256_before": before_digest,
            "source_sha256_after": after_digest_expected,
            "backup_sha256": before_digest,
            "backup_file": backup.name,
            "manifest_sha256": manifest_digest,
            "changed_keys": changed,
            "secrets_disclosed": False,
            **dict(evidence_binding or {}),
        }
        prepared = _pending_transaction(
            action=action,
            source=source,
            source_sha256_before=before_digest,
            source_sha256_after=after_digest_expected,
            manifest=manifest,
            manifest_sha256=manifest_digest,
            receipt_path=receipt_path,
            receipt=receipt,
            backup=backup,
            backup_sha256=before_digest,
        )
        _exclusive_write(pending, _json_bytes(prepared), mode=0o600)
        if _digest(source.read_bytes()) != before_digest:
            _remove_pending(pending)
            raise SourceUpdateError("immutable_source_cas_mismatch")
        _atomic_write(source, after, mode=0o600)
        after_digest = _digest(source.read_bytes())
        if after_digest != after_digest_expected:
            raise SourceUpdateError("immutable_source_post_install_digest_mismatch")
        if after_digest != receipt["source_sha256_after"]:
            raise SourceUpdateError("immutable_source_post_install_digest_mismatch")
        try:
            _exclusive_write(receipt_path, _json_bytes(receipt), mode=0o600)
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


def _rollback_private_primary_from_activation(
    args: argparse.Namespace,
    source: Path,
    *,
    inherited_source_lock_descriptor: int | None = None,
) -> int:
    if args.confirm != PRIVATE_PRIMARY_ROLLBACK_CONFIRMATION:
        raise SourceUpdateError("apply_confirmation_required")
    for supplied, reason in (
        (args.expected_source_sha256, "expected_source_sha256_invalid"),
        (args.expected_manifest_sha256, "expected_manifest_sha256_invalid"),
        (
            args.expected_activation_receipt_sha256,
            "activation_receipt_digest_invalid",
        ),
    ):
        if not _DIGEST_PATTERN.fullmatch(supplied or ""):
            raise SourceUpdateError(reason)
    manifest = _secure_file(Path(args.manifest), label="manifest")
    _require_manifest_scope(manifest)
    manifest_digest = _digest(manifest.read_bytes())
    if manifest_digest != args.expected_manifest_sha256:
        raise SourceUpdateError("manifest_cas_mismatch")
    activation_path = _secure_file(
        Path(args.activation_receipt), label="activation_receipt"
    )
    _require_production_scope(activation_path, label="activation_receipt")
    activation_payload = activation_path.read_bytes()
    activation_digest = _digest(activation_payload)
    if activation_digest != args.expected_activation_receipt_sha256:
        raise SourceUpdateError("activation_receipt_cas_mismatch")
    try:
        activation = json.loads(activation_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceUpdateError("activation_receipt_invalid") from exc
    backup_name = activation.get("backup_file") if isinstance(activation, dict) else None
    if (
        not isinstance(activation, dict)
        or activation.get("schema_version") != 1
        or activation.get("action")
        != "ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS"
        or activation.get("status") != "APPLIED"
        or not _DIGEST_PATTERN.fullmatch(
            str(activation.get("source_sha256_before") or "")
        )
        or not _DIGEST_PATTERN.fullmatch(
            str(activation.get("source_sha256_after") or "")
        )
        or activation.get("backup_sha256")
        != activation.get("source_sha256_before")
        or not isinstance(backup_name, str)
        or not re.fullmatch(r"production-runtime-source\.[A-Za-z0-9.:-]+\.env", backup_name)
        or activation.get("manifest_sha256") != manifest_digest
        or activation.get("secrets_disclosed") is not False
    ):
        raise SourceUpdateError("activation_receipt_contract_invalid")
    backup_dir = _secure_directory(
        Path(args.backup_dir), label="backup_directory"
    )
    _require_production_scope(backup_dir, label="backup_directory")
    backup = _secure_file(backup_dir / backup_name, label="activation_backup")
    if backup.parent != backup_dir:
        raise SourceUpdateError("activation_backup_invalid")
    restored_payload = backup.read_bytes()
    restored_digest = _digest(restored_payload)
    if restored_digest != activation["source_sha256_before"]:
        raise SourceUpdateError("activation_backup_digest_mismatch")
    receipt_path = Path(args.receipt)
    if not receipt_path.is_absolute():
        raise SourceUpdateError("receipt_path_invalid")
    _secure_directory(receipt_path.parent, label="receipt_directory", create=True)
    if receipt_path.resolve(strict=False) != receipt_path or os.path.lexists(receipt_path):
        raise SourceUpdateError("receipt_path_invalid")
    _require_production_scope(receipt_path, label="receipt")
    pending = _pending_path(source)
    with _source_mutation_lock(source, inherited_source_lock_descriptor):
        if _digest(manifest.read_bytes()) != manifest_digest:
            raise SourceUpdateError("manifest_cas_mismatch")
        if _manifest_source(manifest) != source:
            raise SourceUpdateError("manifest_source_identity_changed")
        if _digest(activation_path.read_bytes()) != activation_digest:
            raise SourceUpdateError("activation_receipt_cas_mismatch")
        if pending.exists() or pending.is_symlink():
            raise SourceUpdateError("source_update_pending_recovery_required")
        active_payload = source.read_bytes()
        active_digest = _digest(active_payload)
        if (
            active_digest != args.expected_source_sha256
            or active_digest != activation["source_sha256_after"]
        ):
            raise SourceUpdateError("immutable_source_cas_mismatch")
        receipt = {
            "schema_version": 1,
            "action": "RESTORE_EXACT_PRE_ACTIVATION_SOURCE",
            "status": "APPLIED",
            "source_sha256_before": active_digest,
            "source_sha256_after": restored_digest,
            "activation_receipt_sha256": activation_digest,
            "manifest_sha256": manifest_digest,
            "backup_sha256": restored_digest,
            "secrets_disclosed": False,
        }
        prepared = _pending_transaction(
            action="RESTORE_EXACT_PRE_ACTIVATION_SOURCE",
            source=source,
            source_sha256_before=active_digest,
            source_sha256_after=restored_digest,
            manifest=manifest,
            manifest_sha256=manifest_digest,
            receipt_path=receipt_path,
            receipt=receipt,
            backup=backup,
            backup_sha256=restored_digest,
        )
        _exclusive_write(
            pending,
            _json_bytes(prepared),
            mode=0o600,
        )
        _atomic_write(source, restored_payload, mode=0o600)
        if _digest(source.read_bytes()) != restored_digest:
            raise SourceUpdateError("immutable_source_post_install_digest_mismatch")
        try:
            _exclusive_write(
                receipt_path,
                _json_bytes(receipt),
                mode=0o600,
            )
        except OSError:
            if _digest(source.read_bytes()) == restored_digest:
                _atomic_write(source, active_payload, mode=0o600)
                if _digest(source.read_bytes()) == active_digest:
                    _remove_pending(pending)
            raise
        _remove_pending(pending)
        _emit(receipt)
        return 0


def activate_private_primary_with_held_source_lock(
    args: argparse.Namespace,
    source: Path,
    *,
    source_lock_descriptor: int,
) -> int:
    """Apply the bounded PRIVATE_PRIMARY CAS inside a caller-owned lock.

    The caller must retain the same descriptor until this function returns.
    Ordinary CLI callers continue to acquire the lock internally.
    """

    binding = _promotion_binding(args)
    return _apply(
        args,
        source,
        updates=PRIVATE_PRIMARY_UPDATES,
        confirmation=PRIVATE_PRIMARY_APPLY_CONFIRMATION,
        action="ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS",
        evidence_binding=binding,
        inherited_source_lock_descriptor=source_lock_descriptor,
    )


def rollback_private_primary_with_held_source_lock(
    args: argparse.Namespace,
    source: Path,
    *,
    source_lock_descriptor: int,
) -> int:
    """Restore exact pre-activation bytes inside a caller-owned source lock."""

    return _rollback_private_primary_from_activation(
        args,
        source,
        inherited_source_lock_descriptor=source_lock_descriptor,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command_name in (
        "plan",
        "apply",
        "rollback",
        "plan-private-primary",
        "activate-private-primary",
        "rollback-private-primary",
        "recover-private-primary",
    ):
        command = subparsers.add_parser(command_name)
        command.add_argument("--manifest", required=True)
        if command_name in {
            "apply",
            "rollback",
            "activate-private-primary",
            "rollback-private-primary",
        }:
            command.add_argument("--expected-source-sha256", required=True)
            command.add_argument("--expected-manifest-sha256", required=True)
            command.add_argument("--confirm", required=True)
            command.add_argument("--backup-dir", required=True)
            command.add_argument("--receipt", required=True)
        if command_name == "activate-private-primary":
            command.add_argument("--promotion-receipt", required=True)
            command.add_argument(
                "--expected-promotion-receipt-sha256", required=True
            )
            command.add_argument("--expected-release-sha", required=True)
            command.add_argument("--expected-release-tree", required=True)
        if command_name == "rollback-private-primary":
            command.add_argument("--activation-receipt", required=True)
            command.add_argument(
                "--expected-activation-receipt-sha256", required=True
            )
        if command_name == "recover-private-primary":
            command.add_argument("--backup-dir", required=True)
            command.add_argument("--receipt", required=True)
            command.add_argument("--expected-pending-sha256", required=True)
            command.add_argument(
                "--recovery-action", choices=("resume", "rollback"), required=True
            )
            command.add_argument("--recovery-confirm", required=True)
            command.add_argument("--promotion-receipt", required=True)
            command.add_argument(
                "--expected-promotion-receipt-sha256", required=True
            )
            command.add_argument("--expected-release-sha", required=True)
            command.add_argument("--expected-release-tree", required=True)
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
        if args.command == "activate-private-primary":
            binding = _promotion_binding(args)
            return _apply(
                args,
                source,
                updates=PRIVATE_PRIMARY_UPDATES,
                confirmation=PRIVATE_PRIMARY_APPLY_CONFIRMATION,
                action="ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS",
                evidence_binding=binding,
            )
        if args.command == "rollback-private-primary":
            return _rollback_private_primary_from_activation(args, source)
        if args.command == "recover-private-primary":
            with _source_lock(source) as descriptor:
                result = recover_private_primary_with_held_source_lock(
                    args,
                    source,
                    source_lock_descriptor=descriptor,
                )
            _emit(result)
            return 0
        plan_updates = (
            PRIVATE_PRIMARY_UPDATES
            if args.command == "plan-private-primary"
            else APPROVED_UPDATES
        )
        before, after, changed = _plan(source, plan_updates)
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
