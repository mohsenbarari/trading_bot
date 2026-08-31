#!/usr/bin/env python3
"""Recoverably transfer Telegram market capture away from legacy systemd units."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import verify_production_private_primary_promotion as primary_verifier


CONFIRMATION = "quiesce-production-legacy-market-collectors"
RESTORE_CONFIRMATION = "restore-production-legacy-market-collectors"
RECOVER_CONFIRMATION = "recover-production-legacy-market-collector-handoff"
COMMIT_CONFIRMATION = "commit-production-private-primary-capture-owner"
PREPARE_AUTHORITY_CONFIRMATION = (
    "prepare-production-private-primary-capture-authority"
)
MARK_AUTHORITY_TRANSFERRED_CONFIRMATION = (
    "mark-production-private-primary-capture-authority-transferred"
)
MARK_AUTHORITY_RESTORED_CONFIRMATION = (
    "mark-production-private-primary-capture-authority-restored"
)
REFRESH_AUTHORITY_CONFIRMATION = (
    "refresh-production-private-primary-capture-authority"
)
SCHEMA = "production_legacy_market_collector_handoff/1.1"
MAX_HANDOFF_AGE_SECONDS = 120
APPROVED_ROOT = Path("/root/secure-envs/trading-bot/market-pipeline-cutover")
OPERATION_LOCK_PATH = Path(
    "/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock"
)
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
BOT_TIMERS = (
    "coin-group-event-telegram.timer",
    "trading-bot-private-gold-collector.timer",
)
BOT_SERVICES = (
    "coin-group-event-telegram.service",
    "trading-bot-private-gold-collector.service",
    "coin-public-market-telegram.service",
)
WEB_TIMERS: tuple[str, ...] = ()
WEB_SERVICES = (
    "coin-capture.service",
    "market-channel-capture.service",
)
ROLE_TIMERS: Mapping[str, tuple[str, ...]] = {
    "bot": BOT_TIMERS,
    "web": WEB_TIMERS,
}
ROLE_SERVICES: Mapping[str, tuple[str, ...]] = {
    "bot": BOT_SERVICES,
    "web": WEB_SERVICES,
}
ROLE_UNITS: Mapping[str, tuple[str, ...]] = {
    role: (*ROLE_TIMERS[role], *ROLE_SERVICES[role])
    for role in ("bot", "web")
}
# The all-host inventory is only a source-ownership contract.  Operational
# reads and mutations must always use ROLE_UNITS[host_role], because systemd
# units on bot-fi and wa-fi are intentionally disjoint.
TIMERS = (*BOT_TIMERS, *WEB_TIMERS)
SERVICES = (*BOT_SERVICES, *WEB_SERVICES)
UNITS = (*ROLE_UNITS["bot"], *ROLE_UNITS["web"])
ACCOUNT1_TELEGRAM_SOURCES = frozenset(
    {
        "MELTED_PRIMARY_FLOW",
        "MELTED_AGGREGATE",
        "MELTED_FLOW",
        "USD_HERAT",
        "XAUUSD",
    }
)
ACCOUNT2_TELEGRAM_SOURCES = frozenset({"GROUP_1", "GROUP_2"})
PUBLIC_MARKET_TELEGRAM_SOURCES = frozenset(
    {"MELTED_AGGREGATE", "MELTED_FLOW", "USD_HERAT", "XAUUSD"}
)
# This inventory is deliberately limited to processes which acquire a live
# Telegram source also owned by the two PRIVATE_PRIMARY capture accounts.
# Read-only dashboards, spool consumers and external-API pollers are not
# capture owners and must not be stopped by this handoff.
UNIT_SOURCE_OWNERSHIP: Mapping[str, frozenset[str]] = {
    "coin-group-event-telegram.timer": ACCOUNT2_TELEGRAM_SOURCES,
    "trading-bot-private-gold-collector.timer": frozenset(
        {"MELTED_PRIMARY_FLOW"}
    ),
    "coin-group-event-telegram.service": ACCOUNT2_TELEGRAM_SOURCES,
    "trading-bot-private-gold-collector.service": frozenset(
        {"MELTED_PRIMARY_FLOW"}
    ),
    "coin-public-market-telegram.service": PUBLIC_MARKET_TELEGRAM_SOURCES,
    "coin-capture.service": ACCOUNT2_TELEGRAM_SOURCES,
    "market-channel-capture.service": ACCOUNT1_TELEGRAM_SOURCES,
}
if set(UNIT_SOURCE_OWNERSHIP) != set(UNITS):
    raise RuntimeError("collector_handoff_source_inventory_invalid")
SYSTEMD_ROOT = Path("/etc/systemd/system")


class CollectorHandoffError(RuntimeError):
    """Stable, content-free refusal."""


def _role_units(host_role: str) -> tuple[str, ...]:
    try:
        return ROLE_UNITS[host_role]
    except KeyError as exc:
        raise CollectorHandoffError("collector_handoff_host_role_invalid") from exc


def _role_timers(host_role: str) -> tuple[str, ...]:
    _role_units(host_role)
    return ROLE_TIMERS[host_role]


def _role_services(host_role: str) -> tuple[str, ...]:
    _role_units(host_role)
    return ROLE_SERVICES[host_role]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _secure_parent(path: Path) -> None:
    if path.parent != APPROVED_ROOT:
        raise CollectorHandoffError("collector_handoff_path_invalid")
    APPROVED_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(APPROVED_ROOT, 0o700)
    info = APPROVED_ROOT.lstat()
    if (
        APPROVED_ROOT.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise CollectorHandoffError("collector_handoff_root_invalid")


def _secure_file(path: Path) -> None:
    info = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise CollectorHandoffError("collector_handoff_file_invalid")


def _secure_bytes(path: Path) -> bytes:
    """Read the exact validated inode; never validate then reopen a pathname."""

    try:
        before = path.lstat()
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError as exc:
        raise CollectorHandoffError("collector_handoff_file_invalid") from exc
    try:
        observed = os.fstat(descriptor)
        if (
            path.is_symlink()
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != os.geteuid()
            or stat.S_IMODE(observed.st_mode) != 0o600
            or observed.st_nlink != 1
            or observed.st_dev != before.st_dev
            or observed.st_ino != before.st_ino
        ):
            raise CollectorHandoffError("collector_handoff_file_invalid")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _atomic(path: Path, payload: Mapping[str, Any], *, exclusive: bool = False) -> None:
    _secure_parent(path)
    if exclusive and (path.exists() or path.is_symlink()):
        raise CollectorHandoffError("collector_handoff_journal_exists")
    candidate = path.parent / f".{path.name}.{os.getpid()}.tmp"
    descriptor = os.open(
        candidate,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)


def _acquire_maintenance_lock(
    *, journal: Path, release_sha: str, host_role: str
) -> dict[str, Any]:
    _role_units(host_role)
    parent = OPERATION_LOCK_PATH.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    info = parent.lstat()
    if (
        parent.is_symlink()
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise CollectorHandoffError("collector_handoff_operation_lock_root_invalid")
    try:
        descriptor = os.open(
            OPERATION_LOCK_PATH,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError as exc:
        raise CollectorHandoffError("collector_handoff_production_operation_locked") from exc
    try:
        metadata = os.fstat(descriptor)
        payload = {
            "schema": "market_pipeline_maintenance_lock/1.0",
            "environment": "production",
            "host_role": host_role,
            "release_sha": release_sha,
            "nonce_sha256": sha256(secrets.token_bytes(32)).hexdigest(),
            "journal_path_sha256": sha256(str(journal).encode("utf-8")).hexdigest(),
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
        }
        os.write(
            descriptor,
            (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
        )
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        OPERATION_LOCK_PATH.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    return payload


def _maintenance_lock(
    journal: Path, release_sha: str, host_role: str
) -> dict[str, Any]:
    _role_units(host_role)
    try:
        info = OPERATION_LOCK_PATH.lstat()
        payload = json.loads(OPERATION_LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectorHandoffError("collector_handoff_maintenance_lock_invalid") from exc
    if (
        OPERATION_LOCK_PATH.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or payload.get("schema") != "market_pipeline_maintenance_lock/1.0"
        or payload.get("environment") != "production"
        or payload.get("host_role") != host_role
        or payload.get("release_sha") != release_sha
        or not HEX64.fullmatch(str(payload.get("nonce_sha256") or ""))
        or payload.get("journal_path_sha256")
        != sha256(str(journal).encode("utf-8")).hexdigest()
        or payload.get("device") != info.st_dev
        or payload.get("inode") != info.st_ino
    ):
        raise CollectorHandoffError("collector_handoff_maintenance_lock_invalid")
    return payload


@contextmanager
def _held_maintenance_guard(
    journal: Path, release_sha: str, host_role: str
):
    """Serialize every handoff transition on the persistent lock inode."""

    try:
        descriptor = os.open(
            OPERATION_LOCK_PATH,
            os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise CollectorHandoffError("collector_handoff_transition_locked") from exc
    try:
        try:
            metadata = os.fstat(descriptor)
            path_metadata = OPERATION_LOCK_PATH.lstat()
            os.lseek(descriptor, 0, os.SEEK_SET)
            payload = json.loads(os.read(descriptor, 8192).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectorHandoffError(
                "collector_handoff_maintenance_lock_invalid"
            ) from exc
        if (
            OPERATION_LOCK_PATH.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or path_metadata.st_dev != metadata.st_dev
            or path_metadata.st_ino != metadata.st_ino
            or payload.get("schema") != "market_pipeline_maintenance_lock/1.0"
            or payload.get("environment") != "production"
            or payload.get("host_role") != host_role
            or payload.get("release_sha") != release_sha
            or not HEX64.fullmatch(str(payload.get("nonce_sha256") or ""))
            or payload.get("journal_path_sha256")
            != sha256(str(journal).encode("utf-8")).hexdigest()
            or payload.get("device") != metadata.st_dev
            or payload.get("inode") != metadata.st_ino
        ):
            raise CollectorHandoffError("collector_handoff_maintenance_lock_invalid")
        # Exceptions raised by the guarded transition belong to that
        # transition.  In particular an OSError from systemctl or a durable
        # journal write must not be relabelled as corrupt lock metadata.
        yield descriptor, payload
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _maintenance_guard(journal: Path, release_sha: str, host_role: str):
    with _held_maintenance_guard(
        journal, release_sha, host_role
    ) as (_descriptor, payload):
        yield payload


def _release_maintenance_lock(
    journal: Path, release_sha: str, host_role: str
) -> None:
    _maintenance_lock(journal, release_sha, host_role)
    OPERATION_LOCK_PATH.unlink()
    directory = os.open(OPERATION_LOCK_PATH.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _validate_held_maintenance_lock(
    *,
    descriptor: int,
    journal: Path,
    release_sha: str,
    host_role: str,
    expected_lock: Mapping[str, Any],
) -> dict[str, Any]:
    live_lock = _maintenance_lock(journal, release_sha, host_role)
    try:
        descriptor_info = os.fstat(descriptor)
        path_info = OPERATION_LOCK_PATH.lstat()
    except OSError as exc:
        raise CollectorHandoffError(
            "collector_handoff_maintenance_lock_invalid"
        ) from exc
    if (
        live_lock != dict(expected_lock)
        or descriptor_info.st_dev != path_info.st_dev
        or descriptor_info.st_ino != path_info.st_ino
        or live_lock.get("device") != descriptor_info.st_dev
        or live_lock.get("inode") != descriptor_info.st_ino
    ):
        raise CollectorHandoffError("collector_handoff_maintenance_lock_invalid")
    return live_lock


def prepare_capture_authority_transfer_with_held_lock(
    *,
    descriptor: int,
    journal: Path,
    release_sha: str,
    host_role: str,
    expected_lock: Mapping[str, Any],
    bluegreen_journal: Path,
    prepared_bluegreen_journal_sha256: str,
    marker_authority_sha256: str,
) -> dict[str, Any]:
    """Write the no-legacy-recovery WAL before the first marker mutation."""

    _validate_held_maintenance_lock(
        descriptor=descriptor,
        journal=journal,
        release_sha=release_sha,
        host_role=host_role,
        expected_lock=expected_lock,
    )
    if (
        not HEX64.fullmatch(prepared_bluegreen_journal_sha256)
        or not HEX64.fullmatch(marker_authority_sha256)
    ):
        raise CollectorHandoffError("collector_handoff_authority_binding_invalid")
    binding = {
        "bluegreen_journal_path_sha256": sha256(
            str(bluegreen_journal).encode("utf-8")
        ).hexdigest(),
        "prepared_bluegreen_journal_sha256": prepared_bluegreen_journal_sha256,
        "authorization_bluegreen_journal_sha256": None,
        "marker_authority_sha256": marker_authority_sha256,
    }
    payload = _read(journal, release_sha=release_sha, host_role=host_role)
    if payload.get("status") == "AUTHORITY_TRANSFERRING":
        if payload.get("authority_transfer") != binding:
            raise CollectorHandoffError("collector_handoff_authority_binding_drift")
        _assert_quiesced(host_role)
        return payload
    if (
        payload.get("status") != "QUIESCED"
        or payload.get("authority_transfer") not in (None, {})
    ):
        raise CollectorHandoffError("collector_handoff_authority_state_invalid")
    current = _assert_quiesced(host_role)
    payload["status"] = "AUTHORITY_TRANSFERRING"
    payload["current_units"] = current
    payload["authority_transfer"] = binding
    payload["verified_at_utc"] = _now()
    _atomic(journal, payload)
    return payload


def mark_capture_authority_transferred_with_held_lock(
    *,
    descriptor: int,
    journal: Path,
    release_sha: str,
    host_role: str,
    expected_lock: Mapping[str, Any],
    bluegreen_journal: Path,
    authorization_bluegreen_journal_sha256: str,
    marker_authority_sha256: str,
) -> dict[str, Any]:
    """Durably forbid ordinary legacy recovery after marker authority moves."""

    _validate_held_maintenance_lock(
        descriptor=descriptor,
        journal=journal,
        release_sha=release_sha,
        host_role=host_role,
        expected_lock=expected_lock,
    )
    if (
        not HEX64.fullmatch(authorization_bluegreen_journal_sha256)
        or not HEX64.fullmatch(marker_authority_sha256)
    ):
        raise CollectorHandoffError("collector_handoff_authority_binding_invalid")
    payload = _read(journal, release_sha=release_sha, host_role=host_role)
    authority = payload.get("authority_transfer")
    if not isinstance(authority, dict):
        raise CollectorHandoffError("collector_handoff_authority_binding_drift")
    binding = dict(authority)
    binding["authorization_bluegreen_journal_sha256"] = (
        authorization_bluegreen_journal_sha256
    )
    if payload.get("status") == "AUTHORITY_TRANSFERRED":
        if payload.get("authority_transfer") != binding:
            raise CollectorHandoffError("collector_handoff_authority_binding_drift")
        _assert_quiesced(host_role)
        return payload
    if (
        payload.get("status") != "AUTHORITY_TRANSFERRING"
        or authority.get("bluegreen_journal_path_sha256")
        != sha256(str(bluegreen_journal).encode("utf-8")).hexdigest()
        or authority.get("marker_authority_sha256") != marker_authority_sha256
        or not HEX64.fullmatch(
            str(authority.get("prepared_bluegreen_journal_sha256") or "")
        )
        or authority.get("authorization_bluegreen_journal_sha256") is not None
    ):
        raise CollectorHandoffError("collector_handoff_authority_state_invalid")
    current = _assert_quiesced(host_role)
    payload["status"] = "AUTHORITY_TRANSFERRED"
    payload["current_units"] = current
    payload["authority_transfer"] = binding
    payload["verified_at_utc"] = _now()
    _atomic(journal, payload)
    return payload


def mark_capture_authority_restored_with_held_lock(
    *,
    descriptor: int,
    journal: Path,
    release_sha: str,
    host_role: str,
    expected_lock: Mapping[str, Any],
    bluegreen_journal: Path,
    marker_authority_sha256: str,
) -> dict[str, Any]:
    """Return to QUIESCED only after the exact marker rollback is durable."""

    _validate_held_maintenance_lock(
        descriptor=descriptor,
        journal=journal,
        release_sha=release_sha,
        host_role=host_role,
        expected_lock=expected_lock,
    )
    if not HEX64.fullmatch(marker_authority_sha256):
        raise CollectorHandoffError("collector_handoff_authority_binding_invalid")
    payload = _read(journal, release_sha=release_sha, host_role=host_role)
    authority = payload.get("authority_transfer")
    binding_matches = (
        isinstance(authority, dict)
        and authority.get("bluegreen_journal_path_sha256")
        == sha256(str(bluegreen_journal).encode("utf-8")).hexdigest()
        and authority.get("marker_authority_sha256")
        == marker_authority_sha256
    )
    if payload.get("status") == "QUIESCED" and binding_matches:
        _assert_quiesced(host_role)
        return payload
    if (
        payload.get("status")
        not in {"AUTHORITY_TRANSFERRING", "AUTHORITY_TRANSFERRED"}
        or not binding_matches
    ):
        raise CollectorHandoffError("collector_handoff_authority_binding_drift")
    _assert_quiesced(host_role)
    payload["status"] = "QUIESCED"
    # Keep the exact transfer binding as durable recovery evidence.  The
    # ordinary restore still requires the exact ROLLED_BACK blue/green proof.
    payload["verified_at_utc"] = _now()
    _atomic(journal, payload)
    return payload


def prepare_authority(
    *,
    journal: Path,
    release_sha: str,
    host_role: str,
    expected_journal_sha256: str,
    bluegreen_journal: Path,
    prepared_bluegreen_journal_sha256: str,
    marker_authority_sha256: str,
) -> dict[str, Any]:
    with _held_maintenance_guard(
        journal, release_sha, host_role
    ) as (descriptor, lock_binding):
        if (
            not HEX64.fullmatch(expected_journal_sha256)
            or sha256(_secure_bytes(journal)).hexdigest()
            != expected_journal_sha256
        ):
            raise CollectorHandoffError(
                "collector_handoff_journal_digest_invalid"
            )
        return prepare_capture_authority_transfer_with_held_lock(
            descriptor=descriptor,
            journal=journal,
            release_sha=release_sha,
            host_role=host_role,
            expected_lock=lock_binding,
            bluegreen_journal=bluegreen_journal,
            prepared_bluegreen_journal_sha256=(
                prepared_bluegreen_journal_sha256
            ),
            marker_authority_sha256=marker_authority_sha256,
        )


def _prepared_bluegreen_authority(
    *, path: Path, expected_sha256: str, release_sha: str
) -> tuple[dict[str, Any], str]:
    raw = _secure_bytes(path)
    digest = sha256(raw).hexdigest()
    if not HEX64.fullmatch(expected_sha256) or digest != expected_sha256:
        raise CollectorHandoffError(
            "collector_handoff_bluegreen_digest_invalid"
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectorHandoffError(
            "collector_handoff_bluegreen_invalid"
        ) from exc
    transition = document.get("marker_transition")
    entries = transition.get("entries") if isinstance(transition, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("schema") != "market_pipeline_bluegreen_upgrade/1.0"
        or document.get("status") != "capture_authority_prepared"
        or document.get("release_sha") != release_sha
        or document.get("product_authority_changed") is not False
        or document.get("state_deleted") is not False
        or document.get("secrets_disclosed") is not False
        or document.get("legacy_authority_prepared_journal_sha256")
        is not None
        or document.get("legacy_authority_transfer") is not None
        or not isinstance(entries, dict)
        or not entries
        or transition.get("status") != "PREPARED"
        or transition.get("rollback_status") not in {None, "NOT_STARTED"}
        or any(
            not isinstance(row, dict)
            or row.get("status") != "PENDING"
            or row.get("rollback_status") != "NOT_STARTED"
            or not HEX64.fullmatch(str(row.get("prior_sha256") or ""))
            or not HEX64.fullmatch(str(row.get("target_sha256") or ""))
            for row in entries.values()
        )
    ):
        raise CollectorHandoffError("collector_handoff_bluegreen_invalid")
    marker_document = {
        "authorized_at_utc": transition.get("authorized_at_utc"),
        "entries": {
            role: {
                "path": row["path"],
                "prior_sha256": row["prior_sha256"],
                "target_sha256": row["target_sha256"],
                "target_payload": row["target_payload"],
            }
            for role, row in sorted(entries.items())
        },
    }
    marker_digest = sha256(
        (
            json.dumps(
                marker_document,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
    ).hexdigest()
    return document, marker_digest


def refresh_authority_transfer(
    *,
    journal: Path,
    release_sha: str,
    host_role: str,
    expected_journal_sha256: str,
    bluegreen_journal: Path,
    expected_bluegreen_journal_sha256: str,
    marker_authority_sha256: str,
) -> dict[str, Any]:
    """Refresh and rebind a pre-marker authority WAL after a slow handoff.

    This command is deliberately narrower than ``verify``.  It only accepts
    an AUTHORITY_TRANSFERRING receipt whose authorization digest is still
    empty, plus an exact blue/green journal proving that every marker remains
    PREPARED/PENDING.  It cannot refresh an already transferred authority.
    """

    with _held_maintenance_guard(
        journal, release_sha, host_role
    ) as (descriptor, lock_binding):
        if (
            not HEX64.fullmatch(expected_journal_sha256)
            or sha256(_secure_bytes(journal)).hexdigest()
            != expected_journal_sha256
        ):
            raise CollectorHandoffError(
                "collector_handoff_journal_digest_invalid"
            )
        _document, observed_marker_digest = _prepared_bluegreen_authority(
            path=bluegreen_journal,
            expected_sha256=expected_bluegreen_journal_sha256,
            release_sha=release_sha,
        )
        if (
            not HEX64.fullmatch(marker_authority_sha256)
            or observed_marker_digest != marker_authority_sha256
        ):
            raise CollectorHandoffError(
                "collector_handoff_authority_binding_invalid"
            )
        _validate_held_maintenance_lock(
            descriptor=descriptor,
            journal=journal,
            release_sha=release_sha,
            host_role=host_role,
            expected_lock=lock_binding,
        )
        payload = _read(
            journal, release_sha=release_sha, host_role=host_role
        )
        authority = payload.get("authority_transfer")
        if (
            payload.get("status") != "AUTHORITY_TRANSFERRING"
            or not isinstance(authority, dict)
            or authority.get("bluegreen_journal_path_sha256")
            != sha256(str(bluegreen_journal).encode("utf-8")).hexdigest()
            or authority.get("marker_authority_sha256")
            != marker_authority_sha256
            or not HEX64.fullmatch(
                str(authority.get("prepared_bluegreen_journal_sha256") or "")
            )
            or authority.get("authorization_bluegreen_journal_sha256")
            is not None
        ):
            raise CollectorHandoffError(
                "collector_handoff_authority_state_invalid"
            )
        current = _assert_quiesced(host_role)
        authority = dict(authority)
        authority["prepared_bluegreen_journal_sha256"] = (
            expected_bluegreen_journal_sha256
        )
        payload["authority_transfer"] = authority
        payload["current_units"] = current
        payload["verified_at_utc"] = _now()
        _atomic(journal, payload)
        return payload


def mark_authority_transferred(
    *,
    journal: Path,
    release_sha: str,
    host_role: str,
    expected_journal_sha256: str,
    bluegreen_journal: Path,
    authorization_bluegreen_journal_sha256: str,
    marker_authority_sha256: str,
) -> dict[str, Any]:
    with _held_maintenance_guard(
        journal, release_sha, host_role
    ) as (descriptor, lock_binding):
        if (
            not HEX64.fullmatch(expected_journal_sha256)
            or sha256(_secure_bytes(journal)).hexdigest()
            != expected_journal_sha256
        ):
            raise CollectorHandoffError(
                "collector_handoff_journal_digest_invalid"
            )
        return mark_capture_authority_transferred_with_held_lock(
            descriptor=descriptor,
            journal=journal,
            release_sha=release_sha,
            host_role=host_role,
            expected_lock=lock_binding,
            bluegreen_journal=bluegreen_journal,
            authorization_bluegreen_journal_sha256=(
                authorization_bluegreen_journal_sha256
            ),
            marker_authority_sha256=marker_authority_sha256,
        )


def mark_authority_restored(
    *,
    journal: Path,
    release_sha: str,
    host_role: str,
    expected_journal_sha256: str,
    bluegreen_journal: Path,
    marker_authority_sha256: str,
) -> dict[str, Any]:
    with _held_maintenance_guard(
        journal, release_sha, host_role
    ) as (descriptor, lock_binding):
        if (
            not HEX64.fullmatch(expected_journal_sha256)
            or sha256(_secure_bytes(journal)).hexdigest()
            != expected_journal_sha256
        ):
            raise CollectorHandoffError(
                "collector_handoff_journal_digest_invalid"
            )
        return mark_capture_authority_restored_with_held_lock(
            descriptor=descriptor,
            journal=journal,
            release_sha=release_sha,
            host_role=host_role,
            expected_lock=lock_binding,
            bluegreen_journal=bluegreen_journal,
            marker_authority_sha256=marker_authority_sha256,
        )


def _run(arguments: Sequence[str], *, allow: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments), text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode not in allow:
        raise CollectorHandoffError("collector_handoff_systemd_command_failed")
    return result


def _state(action: str, unit: str) -> bool:
    if action == "is-enabled":
        # ``systemctl is-enabled`` deliberately returns success for unit-file
        # states such as ``static`` and ``indirect``.  Those states do not make
        # a unit a boot owner: a static collector can only run when another
        # active unit starts it, and activity is checked separately below.
        # Using only the exit code therefore makes an inactive static service
        # look enabled and prevents a legitimate legacy-owner handoff.
        result = _run(
            ["systemctl", action, unit], allow=(0, 1, 3, 4)
        )
        state = result.stdout.strip()
        if state in {"enabled", "enabled-runtime"}:
            return True
        if state in {
            "disabled",
            "static",
            "indirect",
            "alias",
            "linked",
            "linked-runtime",
            "masked",
            "masked-runtime",
            "generated",
            "transient",
        }:
            return False
        if result.returncode not in (0, 1):
            raise CollectorHandoffError("collector_handoff_enabled_state_unknown")
        raise CollectorHandoffError("collector_handoff_enabled_state_unknown")
    result = _run(["systemctl", action, "--quiet", unit], allow=(0, 1, 3, 4))
    if result.returncode not in (0, 3):
        raise CollectorHandoffError("collector_handoff_active_state_unknown")
    return result.returncode == 0


def _inventory(host_role: str) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for unit in _role_units(host_role):
        path = SYSTEMD_ROOT / unit
        try:
            info = path.lstat()
        except OSError as exc:
            raise CollectorHandoffError("collector_handoff_unit_missing") from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_nlink != 1
        ):
            raise CollectorHandoffError("collector_handoff_unit_invalid")
        row: dict[str, Any] = {
            "unit_sha256": _digest(path),
            "active": _state("is-active", unit),
            "enabled": _state("is-enabled", unit),
            "source_codes": sorted(UNIT_SOURCE_OWNERSHIP[unit]),
        }
        output[unit] = row
    return output


def _assert_quiesced(host_role: str) -> dict[str, dict[str, Any]]:
    state = _inventory(host_role)
    if any(row["active"] or row["enabled"] for row in state.values()):
        raise CollectorHandoffError("collector_handoff_overlap_not_quiesced")
    return state


def _restore_prior_units(
    prior: Mapping[str, Mapping[str, Any]],
    *,
    host_role: str,
) -> dict[str, dict[str, Any]]:
    """Restore and prove the exact pre-quiesce unit state."""

    units = _role_units(host_role)
    timers = _role_timers(host_role)
    services = _role_services(host_role)
    if set(prior) != set(units):
        raise CollectorHandoffError("collector_handoff_prior_state_invalid")
    # Timers remain stopped while service state is reconciled, preventing a
    # timer from firing into a partially restored collector topology.  Service
    # enablement is also restored: three overlapping collectors are standalone
    # enabled services rather than timer-driven jobs.
    for timer in timers:
        _run(["systemctl", "stop", timer])
    for service in services:
        _run(["systemctl", "stop", service])
    for service in services:
        _run(
            [
                "systemctl",
                "enable" if prior[service]["enabled"] else "disable",
                service,
            ]
        )
    for service in services:
        _run(["systemctl", "start" if prior[service]["active"] else "stop", service])
    for timer in timers:
        _run(["systemctl", "enable" if prior[timer]["enabled"] else "disable", timer])
        _run(["systemctl", "start" if prior[timer]["active"] else "stop", timer])
    current = _inventory(host_role)
    for unit in units:
        if (
            current[unit]["active"] != prior[unit]["active"]
            or current[unit]["enabled"] != prior[unit]["enabled"]
            or current[unit]["source_codes"] != prior[unit]["source_codes"]
            or current[unit]["unit_sha256"] != prior[unit]["unit_sha256"]
        ):
            raise CollectorHandoffError("collector_handoff_restore_mismatch")
    return current


def _assert_terminal_restored(
    payload: Mapping[str, Any], *, host_role: str
) -> dict[str, dict[str, Any]]:
    """Freshly prove terminal legacy restoration; never trust a stale word."""

    prior = payload.get("prior_units")
    recorded = payload.get("current_units")
    if (
        not isinstance(prior, dict)
        or not isinstance(recorded, dict)
        or set(prior) != set(_role_units(host_role))
        or set(recorded) != set(_role_units(host_role))
    ):
        raise CollectorHandoffError("collector_handoff_terminal_state_invalid")
    current = _inventory(host_role)
    for unit in _role_units(host_role):
        expected_keys = {"unit_sha256", "active", "enabled", "source_codes"}
        if (
            not isinstance(prior[unit], dict)
            or not isinstance(recorded[unit], dict)
            or set(prior[unit]) != expected_keys
            or set(recorded[unit]) != expected_keys
            or prior[unit]["source_codes"]
            != sorted(UNIT_SOURCE_OWNERSHIP[unit])
            or recorded[unit] != prior[unit]
            or current[unit] != prior[unit]
        ):
            raise CollectorHandoffError("collector_handoff_terminal_state_drift")
    return current


def _read(
    path: Path, *, release_sha: str, host_role: str
) -> dict[str, Any]:
    units = _role_units(host_role)
    try:
        payload = json.loads(_secure_bytes(path).decode("utf-8"))
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        CollectorHandoffError,
    ) as exc:
        raise CollectorHandoffError("collector_handoff_journal_invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != SCHEMA
        or payload.get("host_role") != host_role
        or payload.get("release_sha") != release_sha
        or payload.get("secrets_disclosed") is not False
        or not isinstance(payload.get("prior_units"), dict)
        or set(payload["prior_units"]) != set(units)
    ):
        raise CollectorHandoffError("collector_handoff_journal_invalid")
    for inventory_name in ("prior_units", "current_units"):
        inventory = payload.get(inventory_name)
        if not isinstance(inventory, dict) or set(inventory) != set(units):
            raise CollectorHandoffError("collector_handoff_journal_invalid")
        for unit, row in inventory.items():
            if (
                not isinstance(row, dict)
                or set(row)
                != {"unit_sha256", "active", "enabled", "source_codes"}
                or not HEX64.fullmatch(str(row.get("unit_sha256") or ""))
                or not isinstance(row.get("active"), bool)
                or not isinstance(row.get("enabled"), bool)
                or row.get("source_codes")
                != sorted(UNIT_SOURCE_OWNERSHIP[unit])
            ):
                raise CollectorHandoffError("collector_handoff_journal_invalid")
    return payload


def validate_committed_handoff(
    *,
    journal: Path,
    expected_journal_sha256: str,
    release_sha: str,
    expected_primary_verification_sha256: str,
    host_role: str,
    expected_maintenance_lock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only, fresh and live validation used immediately before Product work."""

    if (
        not HEX64.fullmatch(expected_journal_sha256)
        or not HEX64.fullmatch(expected_primary_verification_sha256)
    ):
        raise CollectorHandoffError("collector_handoff_committed_binding_invalid")
    _secure_file(journal)
    if _digest(journal) != expected_journal_sha256:
        raise CollectorHandoffError("collector_handoff_committed_binding_invalid")
    units = _role_units(host_role)
    payload = _read(journal, release_sha=release_sha, host_role=host_role)
    expected_keys = {
        "schema", "status", "host_role", "release_sha", "created_at_utc", "verified_at_utc",
        "prior_units", "current_units", "maintenance_lock",
        "primary_verification_sha256", "primary_rollback_sha256",
        "authority_transfer", "state_deleted", "secrets_disclosed",
    }
    try:
        verified = datetime.fromisoformat(
            str(payload["verified_at_utc"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        created = datetime.fromisoformat(
            str(payload["created_at_utc"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise CollectorHandoffError(
            "collector_handoff_committed_binding_invalid"
        ) from exc
    now = datetime.now(timezone.utc)
    prior = payload.get("prior_units")
    recorded_current = payload.get("current_units")
    maintenance = payload.get("maintenance_lock")
    authority_transfer = payload.get("authority_transfer")
    if (
        set(payload) != expected_keys
        or payload.get("status") != "PRIMARY_COMMITTED"
        or payload.get("primary_verification_sha256")
        != expected_primary_verification_sha256
        or payload.get("primary_rollback_sha256") is not None
        or payload.get("state_deleted") is not False
        or payload.get("secrets_disclosed") is not False
        or not isinstance(prior, dict)
        or not isinstance(recorded_current, dict)
        or set(prior) != set(units)
        or set(recorded_current) != set(units)
        or not isinstance(maintenance, dict)
        or not isinstance(authority_transfer, dict)
        or set(authority_transfer)
        != {
            "bluegreen_journal_path_sha256",
            "prepared_bluegreen_journal_sha256",
            "authorization_bluegreen_journal_sha256",
            "marker_authority_sha256",
        }
        or any(
            not HEX64.fullmatch(str(value or ""))
            for value in authority_transfer.values()
        )
        or (
            expected_maintenance_lock is not None
            and maintenance != dict(expected_maintenance_lock)
        )
        or created > verified
        or verified > now + timedelta(seconds=5)
        or now - verified > timedelta(seconds=MAX_HANDOFF_AGE_SECONDS)
    ):
        raise CollectorHandoffError("collector_handoff_committed_binding_invalid")
    for unit in units:
        expected_row_keys = {
            "unit_sha256",
            "active",
            "enabled",
            "source_codes",
        }
        prior_row = prior[unit]
        current_row = recorded_current[unit]
        if (
            not isinstance(prior_row, dict)
            or not isinstance(current_row, dict)
            or set(prior_row) != expected_row_keys
            or set(current_row) != expected_row_keys
            or not HEX64.fullmatch(str(prior_row.get("unit_sha256") or ""))
            or prior_row.get("source_codes")
            != sorted(UNIT_SOURCE_OWNERSHIP[unit])
            or current_row.get("unit_sha256") != prior_row.get("unit_sha256")
            or current_row.get("source_codes") != prior_row.get("source_codes")
            or current_row.get("active") is not False
            or current_row.get("enabled") is not False
        ):
            raise CollectorHandoffError(
                "collector_handoff_committed_binding_invalid"
            )
    live = _assert_quiesced(host_role)
    if live != recorded_current:
        raise CollectorHandoffError("collector_handoff_live_state_drift")
    return payload


def _complete_quiesce_from_prepared(
    *,
    journal: Path,
    payload: dict[str, Any],
    host_role: str,
    timers: Sequence[str],
    services: Sequence[str],
) -> dict[str, Any]:
    prior = payload.get("prior_units")
    if not isinstance(prior, dict):
        raise CollectorHandoffError("collector_handoff_journal_invalid")
    for timer in timers:
        _run(["systemctl", "stop", timer])
        _run(["systemctl", "disable", timer])
    for service in services:
        _run(["systemctl", "disable", service])
    for service in services:
        _run(["systemctl", "stop", service])
    current = _assert_quiesced(host_role)
    payload["status"] = "QUIESCED"
    payload["current_units"] = current
    payload["verified_at_utc"] = _now()
    _atomic(journal, payload)
    return payload


def _adopt_or_continue_quiesce(
    *, journal: Path, release_sha: str, host_role: str
) -> dict[str, Any] | None:
    """Retry the same immutable journal only after live verification."""

    if not (journal.exists() or journal.is_symlink()):
        return None
    existing = _read(journal, release_sha=release_sha, host_role=host_role)
    status = existing.get("status")
    if status not in {"PREPARED", "QUIESCED"}:
        raise CollectorHandoffError("collector_handoff_journal_exists")
    timers = _role_timers(host_role)
    services = _role_services(host_role)
    with _maintenance_guard(journal, release_sha, host_role) as lock_binding:
        if existing.get("maintenance_lock") != lock_binding:
            raise CollectorHandoffError("collector_handoff_maintenance_lock_drift")
        payload = _read(journal, release_sha=release_sha, host_role=host_role)
        if payload.get("maintenance_lock") != lock_binding:
            raise CollectorHandoffError("collector_handoff_maintenance_lock_drift")
        if payload.get("status") == "QUIESCED":
            live = _assert_quiesced(host_role)
            units = _role_units(host_role)
            if any(
                live[unit]["unit_sha256"]
                != payload["prior_units"][unit]["unit_sha256"]
                for unit in units
            ):
                raise CollectorHandoffError("collector_handoff_unit_drift")
            if live != payload.get("current_units"):
                raise CollectorHandoffError("collector_handoff_live_state_drift")
            return payload
        if payload.get("status") != "PREPARED":
            raise CollectorHandoffError("collector_handoff_journal_exists")
        prior = payload.get("prior_units")
        prepared_written = True
        try:
            return _complete_quiesce_from_prepared(
                journal=journal,
                payload=payload,
                host_role=host_role,
                timers=timers,
                services=services,
            )
        except BaseException as original:
            try:
                if isinstance(prior, dict):
                    restored = _restore_prior_units(
                        prior, host_role=host_role
                    )
                    payload["status"] = "RESTORED_AFTER_QUIESCE_FAILURE"
                    payload["current_units"] = restored
                    payload["verified_at_utc"] = _now()
                    _atomic(journal, payload)
                    _assert_terminal_restored(payload, host_role=host_role)
                _release_maintenance_lock(journal, release_sha, host_role)
            except BaseException as recovery_error:
                if prepared_written:
                    payload["status"] = "RECOVERY_REQUIRED"
                    payload["verified_at_utc"] = _now()
                    try:
                        _atomic(journal, payload)
                    except BaseException:
                        pass
                raise CollectorHandoffError(
                    "collector_handoff_recovery_required"
                ) from recovery_error
            raise original


def quiesce(
    *, journal: Path, release_sha: str, host_role: str
) -> dict[str, Any]:
    timers = _role_timers(host_role)
    services = _role_services(host_role)
    _secure_parent(journal)
    adopted = _adopt_or_continue_quiesce(
        journal=journal, release_sha=release_sha, host_role=host_role
    )
    if adopted is not None:
        return adopted
    lock_binding = _acquire_maintenance_lock(
        journal=journal, release_sha=release_sha, host_role=host_role
    )
    prior: dict[str, dict[str, Any]] | None = None
    payload: dict[str, Any] | None = None
    prepared_written = False
    with _maintenance_guard(journal, release_sha, host_role) as observed_lock:
        if observed_lock != lock_binding:
            raise CollectorHandoffError("collector_handoff_maintenance_lock_drift")
        try:
            prior = _inventory(host_role)
            payload = {
                "schema": SCHEMA,
                "status": "PREPARED",
                "host_role": host_role,
                "release_sha": release_sha,
                "created_at_utc": _now(),
                "verified_at_utc": _now(),
                "prior_units": prior,
                "current_units": prior,
                "maintenance_lock": lock_binding,
                "primary_verification_sha256": None,
                "primary_rollback_sha256": None,
                "authority_transfer": None,
                "state_deleted": False,
                "secrets_disclosed": False,
            }
            # The pre-mutation state is durable before the first systemd change so
            # a process or host interruption always has a recoverable journal.
            _atomic(journal, payload, exclusive=True)
            prepared_written = True
            return _complete_quiesce_from_prepared(
                journal=journal,
                payload=payload,
                host_role=host_role,
                timers=timers,
                services=services,
            )
        except BaseException as original:
            try:
                if prior is not None:
                    restored = _restore_prior_units(
                        prior, host_role=host_role
                    )
                    if prepared_written and payload is not None:
                        payload["status"] = "RESTORED_AFTER_QUIESCE_FAILURE"
                        payload["current_units"] = restored
                        payload["verified_at_utc"] = _now()
                        _atomic(journal, payload)
                        _assert_terminal_restored(payload, host_role=host_role)
                _release_maintenance_lock(journal, release_sha, host_role)
            except BaseException as recovery_error:
                if prepared_written and payload is not None:
                    payload["status"] = "RECOVERY_REQUIRED"
                    payload["verified_at_utc"] = _now()
                    try:
                        _atomic(journal, payload)
                    except BaseException:
                        pass
                raise CollectorHandoffError(
                    "collector_handoff_recovery_required"
                ) from recovery_error
            raise original


def verify(
    *, journal: Path, release_sha: str, host_role: str
) -> dict[str, Any]:
    units = _role_units(host_role)
    with _maintenance_guard(journal, release_sha, host_role) as lock_binding:
        payload = _read(journal, release_sha=release_sha, host_role=host_role)
        if payload.get("status") not in {
            "QUIESCED",
            "AUTHORITY_TRANSFERRING",
            "AUTHORITY_TRANSFERRED",
            "PRIMARY_COMMITTED",
        }:
            raise CollectorHandoffError("collector_handoff_state_invalid")
        if payload.get("maintenance_lock") != lock_binding:
            raise CollectorHandoffError("collector_handoff_maintenance_lock_drift")
        current = _assert_quiesced(host_role)
        if any(
            current[unit]["unit_sha256"]
            != payload["prior_units"][unit]["unit_sha256"]
            for unit in units
        ):
            raise CollectorHandoffError("collector_handoff_unit_drift")
        if payload.get("status") in {
            "AUTHORITY_TRANSFERRING",
            "AUTHORITY_TRANSFERRED",
            "PRIMARY_COMMITTED",
        }:
            # The blue/green journal binds the exact handoff receipt digest.
            # Post-transfer verification is deliberately read-only so a
            # harmless status check cannot invalidate that cross-journal WAL.
            return payload
        payload["current_units"] = current
        payload["verified_at_utc"] = _now()
        _atomic(journal, payload)
        return payload


def _proof(path: Path, expected_sha256: str, *, release_sha: str, status: str) -> dict[str, Any]:
    _secure_file(path)
    if not HEX64.fullmatch(expected_sha256) or _digest(path) != expected_sha256:
        raise CollectorHandoffError("collector_handoff_proof_digest_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectorHandoffError("collector_handoff_proof_invalid") from exc
    if payload.get("release_sha") != release_sha or payload.get("status") != status:
        raise CollectorHandoffError("collector_handoff_proof_invalid")
    return payload


def _primary_verification_proof(
    path: Path, expected_sha256: str, *, release_sha: str
) -> dict[str, Any]:
    payload = _proof(
        path, expected_sha256, release_sha=release_sha, status="PASS"
    )
    expected_keys = {
        "schema", "status", "created_at_utc", "release_sha", "release_tree",
        "image_ids", "maximum_age_seconds", "reason_code", "checks",
        "stream_count", "highest_sequence", "snapshot", "capture_backfill",
        "catchup_verification", "artifacts", "read_only_runtime_verification",
        "product_or_runtime_mutated", "payload_values_included", "pii_included",
        "secrets_disclosed",
    }
    try:
        created = datetime.fromisoformat(
            str(payload["created_at_utc"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise CollectorHandoffError(
            "collector_handoff_primary_proof_invalid"
        ) from exc
    now = datetime.now(timezone.utc)
    images = payload.get("image_ids")
    snapshot = payload.get("snapshot")
    catchup = payload.get("catchup_verification")
    artifacts = payload.get("artifacts")
    capture = payload.get("capture_backfill")
    if (
        set(payload) != expected_keys
        or payload.get("schema") != primary_verifier.RECEIPT_SCHEMA
        or not HEX40.fullmatch(str(payload.get("release_tree") or ""))
        or not isinstance(images, dict)
        or set(images) != {"bot", "web"}
        or any(
            re.fullmatch(r"sha256:[0-9a-f]{64}", str(value or "")) is None
            for value in images.values()
        )
        or payload.get("maximum_age_seconds") != primary_verifier.MAXIMUM_AGE_SECONDS
        or payload.get("reason_code") is not None
        or payload.get("checks") != list(primary_verifier.CHECKS)
        or not isinstance(payload.get("stream_count"), int)
        or isinstance(payload.get("stream_count"), bool)
        or payload["stream_count"] <= 0
        or not isinstance(payload.get("highest_sequence"), int)
        or isinstance(payload.get("highest_sequence"), bool)
        or payload["highest_sequence"] <= 0
        or not isinstance(snapshot, dict)
        or snapshot.get("contract") != primary_verifier.WEB_VIEW_CONTRACT
        or snapshot.get("lane") != "PRIVATE_PRIMARY"
        or snapshot.get("status") != "OK"
        or not HEX64.fullmatch(str(snapshot.get("snapshot_hash") or ""))
        or not HEX64.fullmatch(str(snapshot.get("file_sha256") or ""))
        or not isinstance(catchup, dict)
        or set(catchup) != {"receipt_sha256", "age_seconds"}
        or not HEX64.fullmatch(str(catchup.get("receipt_sha256") or ""))
        or not isinstance(catchup.get("age_seconds"), (int, float))
        or isinstance(catchup.get("age_seconds"), bool)
        or not 0 <= float(catchup["age_seconds"]) <= primary_verifier.MAXIMUM_AGE_SECONDS
        or capture
        != {
            "not_before_utc": primary_verifier.AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,
            "source_codes": primary_verifier.AUTHORIZED_BACKFILL_SOURCE_CODES.split(","),
            "max_messages": 250000,
        }
        or not isinstance(artifacts, dict)
        or not artifacts
        or any(not HEX64.fullmatch(str(value or "")) for value in artifacts.values())
        or payload.get("read_only_runtime_verification") is not True
        or payload.get("product_or_runtime_mutated") is not False
        or payload.get("payload_values_included") is not False
        or payload.get("pii_included") is not False
        or payload.get("secrets_disclosed") is not False
        or created > now + timedelta(seconds=5)
        or now - created > timedelta(seconds=primary_verifier.MAXIMUM_AGE_SECONDS)
    ):
        raise CollectorHandoffError("collector_handoff_primary_proof_invalid")
    return payload


def commit(
    *, journal: Path, release_sha: str, primary_verification: Path,
    expected_primary_verification_sha256: str, host_role: str,
) -> dict[str, Any]:
    units = _role_units(host_role)
    with _maintenance_guard(journal, release_sha, host_role) as lock_binding:
        payload = _read(journal, release_sha=release_sha, host_role=host_role)
        if payload.get("maintenance_lock") != lock_binding:
            raise CollectorHandoffError("collector_handoff_maintenance_lock_drift")
        if payload.get("status") == "PRIMARY_COMMITTED":
            if (
                payload.get("primary_verification_sha256")
                != expected_primary_verification_sha256
            ):
                raise CollectorHandoffError("collector_handoff_commit_binding_drift")
            _primary_verification_proof(
                primary_verification,
                expected_primary_verification_sha256,
                release_sha=release_sha,
            )
            _assert_quiesced(host_role)
            return payload
        if payload.get("status") != "AUTHORITY_TRANSFERRED":
            raise CollectorHandoffError("collector_handoff_state_invalid")
        current = _assert_quiesced(host_role)
        if any(
            current[unit]["unit_sha256"]
            != payload["prior_units"][unit]["unit_sha256"]
            for unit in units
        ):
            raise CollectorHandoffError("collector_handoff_unit_drift")
        _primary_verification_proof(
            primary_verification,
            expected_primary_verification_sha256,
            release_sha=release_sha,
        )
        payload["status"] = "PRIMARY_COMMITTED"
        payload["current_units"] = current
        payload["primary_verification_sha256"] = expected_primary_verification_sha256
        payload["verified_at_utc"] = _now()
        _atomic(journal, payload)
        return payload


def restore(
    *, journal: Path, release_sha: str, primary_rollback: Path,
    expected_primary_rollback_sha256: str, host_role: str,
) -> dict[str, Any]:
    payload = _read(journal, release_sha=release_sha, host_role=host_role)
    if payload.get("status") == "RESTORED" and not OPERATION_LOCK_PATH.exists():
        _assert_terminal_restored(payload, host_role=host_role)
        return payload
    with _maintenance_guard(journal, release_sha, host_role) as lock_binding:
        payload = _read(journal, release_sha=release_sha, host_role=host_role)
        if payload.get("maintenance_lock") != lock_binding:
            raise CollectorHandoffError("collector_handoff_maintenance_lock_drift")
        status = payload.get("status")
        if status == "PRIMARY_COMMITTED":
            raise CollectorHandoffError("collector_handoff_committed_restore_forbidden")
        if status == "RESTORED":
            _assert_terminal_restored(payload, host_role=host_role)
            _release_maintenance_lock(journal, release_sha, host_role)
            return payload
        if status == "QUIESCED":
            _proof(
                primary_rollback,
                expected_primary_rollback_sha256,
                release_sha=release_sha,
                status="ROLLED_BACK",
            )
            _assert_quiesced(host_role)
            payload["status"] = "RESTORING"
            payload["primary_rollback_sha256"] = expected_primary_rollback_sha256
            payload["verified_at_utc"] = _now()
            _atomic(journal, payload)
        elif status == "RESTORING":
            if payload.get("primary_rollback_sha256") != expected_primary_rollback_sha256:
                raise CollectorHandoffError("collector_handoff_restore_binding_drift")
            _proof(
                primary_rollback,
                expected_primary_rollback_sha256,
                release_sha=release_sha,
                status="ROLLED_BACK",
            )
        else:
            raise CollectorHandoffError("collector_handoff_restore_state_invalid")
        current = _restore_prior_units(
            payload["prior_units"], host_role=host_role
        )
        payload["status"] = "RESTORED"
        payload["current_units"] = current
        payload["verified_at_utc"] = _now()
        _atomic(journal, payload)
        _assert_terminal_restored(payload, host_role=host_role)
        _release_maintenance_lock(journal, release_sha, host_role)
        return payload


def recover(
    *, journal: Path, release_sha: str, host_role: str
) -> dict[str, Any]:
    """Recover an interrupted pre-authority quiesce without Product rollback."""

    payload = _read(journal, release_sha=release_sha, host_role=host_role)
    if (
        payload.get("status") == "RESTORED_AFTER_QUIESCE_FAILURE"
        and not OPERATION_LOCK_PATH.exists()
    ):
        _assert_terminal_restored(payload, host_role=host_role)
        return payload
    with _maintenance_guard(journal, release_sha, host_role) as lock_binding:
        payload = _read(journal, release_sha=release_sha, host_role=host_role)
        if payload.get("maintenance_lock") != lock_binding:
            raise CollectorHandoffError("collector_handoff_maintenance_lock_drift")
        status = payload.get("status")
        if status == "RESTORED_AFTER_QUIESCE_FAILURE":
            _assert_terminal_restored(payload, host_role=host_role)
            _release_maintenance_lock(journal, release_sha, host_role)
            return payload
        if status not in {"PREPARED", "RECOVERY_REQUIRED", "RECOVERING"}:
            raise CollectorHandoffError("collector_handoff_recovery_state_invalid")
        if status != "RECOVERING":
            payload["status"] = "RECOVERING"
            payload["verified_at_utc"] = _now()
            _atomic(journal, payload)
        current = _restore_prior_units(
            payload["prior_units"], host_role=host_role
        )
        payload["status"] = "RESTORED_AFTER_QUIESCE_FAILURE"
        payload["current_units"] = current
        payload["verified_at_utc"] = _now()
        _atomic(journal, payload)
        _assert_terminal_restored(payload, host_role=host_role)
        _release_maintenance_lock(journal, release_sha, host_role)
        return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "quiesce",
            "verify",
            "prepare-authority",
            "refresh-authority-transfer",
            "mark-authority-transferred",
            "mark-authority-restored",
            "commit",
            "restore",
            "recover",
        ),
    )
    parser.add_argument("--host-role", choices=("bot", "web"), required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--expected-journal-sha256")
    parser.add_argument("--primary-verification", type=Path)
    parser.add_argument("--expected-primary-verification-sha256")
    parser.add_argument("--primary-rollback", type=Path)
    parser.add_argument("--expected-primary-rollback-sha256")
    parser.add_argument("--bluegreen-journal", type=Path)
    parser.add_argument("--expected-bluegreen-journal-sha256")
    parser.add_argument("--marker-authority-sha256")
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args(argv)
    try:
        if not HEX40.fullmatch(args.release_sha):
            raise CollectorHandoffError("collector_handoff_release_invalid")
        common = {
            "journal": args.journal,
            "release_sha": args.release_sha,
            "host_role": args.host_role,
        }
        if args.command == "quiesce":
            if args.confirm != CONFIRMATION:
                raise CollectorHandoffError("collector_handoff_confirmation_invalid")
            payload = quiesce(**common)
        elif args.command == "verify":
            if args.confirm != CONFIRMATION:
                raise CollectorHandoffError("collector_handoff_confirmation_invalid")
            payload = verify(**common)
        elif args.command == "prepare-authority":
            if (
                args.confirm != PREPARE_AUTHORITY_CONFIRMATION
                or not args.bluegreen_journal
                or not args.expected_journal_sha256
                or not args.expected_bluegreen_journal_sha256
                or not args.marker_authority_sha256
            ):
                raise CollectorHandoffError(
                    "collector_handoff_authority_arguments_invalid"
                )
            payload = prepare_authority(
                expected_journal_sha256=args.expected_journal_sha256,
                bluegreen_journal=args.bluegreen_journal,
                prepared_bluegreen_journal_sha256=(
                    args.expected_bluegreen_journal_sha256
                ),
                marker_authority_sha256=args.marker_authority_sha256,
                **common,
            )
        elif args.command == "refresh-authority-transfer":
            if (
                args.confirm != REFRESH_AUTHORITY_CONFIRMATION
                or not args.bluegreen_journal
                or not args.expected_journal_sha256
                or not args.expected_bluegreen_journal_sha256
                or not args.marker_authority_sha256
            ):
                raise CollectorHandoffError(
                    "collector_handoff_authority_arguments_invalid"
                )
            payload = refresh_authority_transfer(
                expected_journal_sha256=args.expected_journal_sha256,
                bluegreen_journal=args.bluegreen_journal,
                expected_bluegreen_journal_sha256=(
                    args.expected_bluegreen_journal_sha256
                ),
                marker_authority_sha256=args.marker_authority_sha256,
                **common,
            )
        elif args.command == "mark-authority-transferred":
            if (
                args.confirm != MARK_AUTHORITY_TRANSFERRED_CONFIRMATION
                or not args.bluegreen_journal
                or not args.expected_journal_sha256
                or not args.expected_bluegreen_journal_sha256
                or not args.marker_authority_sha256
            ):
                raise CollectorHandoffError(
                    "collector_handoff_authority_arguments_invalid"
                )
            payload = mark_authority_transferred(
                expected_journal_sha256=args.expected_journal_sha256,
                bluegreen_journal=args.bluegreen_journal,
                authorization_bluegreen_journal_sha256=(
                    args.expected_bluegreen_journal_sha256
                ),
                marker_authority_sha256=args.marker_authority_sha256,
                **common,
            )
        elif args.command == "mark-authority-restored":
            if (
                args.confirm != MARK_AUTHORITY_RESTORED_CONFIRMATION
                or not args.bluegreen_journal
                or not args.expected_journal_sha256
                or not args.marker_authority_sha256
            ):
                raise CollectorHandoffError(
                    "collector_handoff_authority_arguments_invalid"
                )
            payload = mark_authority_restored(
                expected_journal_sha256=args.expected_journal_sha256,
                bluegreen_journal=args.bluegreen_journal,
                marker_authority_sha256=args.marker_authority_sha256,
                **common,
            )
        elif args.command == "commit":
            if (
                args.confirm != COMMIT_CONFIRMATION
                or not args.primary_verification
                or not args.expected_primary_verification_sha256
            ):
                raise CollectorHandoffError("collector_handoff_commit_arguments_invalid")
            payload = commit(
                primary_verification=args.primary_verification,
                expected_primary_verification_sha256=args.expected_primary_verification_sha256,
                **common,
            )
        elif args.command == "restore":
            if (
                args.confirm != RESTORE_CONFIRMATION
                or not args.primary_rollback
                or not args.expected_primary_rollback_sha256
            ):
                raise CollectorHandoffError("collector_handoff_restore_arguments_invalid")
            payload = restore(
                primary_rollback=args.primary_rollback,
                expected_primary_rollback_sha256=args.expected_primary_rollback_sha256,
                **common,
            )
        else:
            if args.confirm != RECOVER_CONFIRMATION:
                raise CollectorHandoffError("collector_handoff_recovery_arguments_invalid")
            payload = recover(**common)
        print(json.dumps({
            "status": payload["status"],
            "release_sha": payload["release_sha"],
            "host_role": payload["host_role"],
            "journal_sha256": _digest(args.journal),
            "all_legacy_collectors_inactive": payload["status"] in {
                "QUIESCED",
                "AUTHORITY_TRANSFERRING",
                "AUTHORITY_TRANSFERRED",
                "PRIMARY_COMMITTED",
            },
            "secrets_disclosed": False,
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, CollectorHandoffError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "secrets_disclosed": False}, sort_keys=True), file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
