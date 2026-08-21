#!/usr/bin/env python3
"""Guarded production Queue-v1 cutover and forward rollback choreography.

The command is production-specific and fail-closed.  It never creates Telegram
traffic or customer data.  ``apply`` requires a fresh, redacted preflight
receipt, a fresh two-host backup receipt, an exact confirmation phrase, and a
clean pushed ``main``.  Current production is expected to stop at the
credential gate until the environment-specific Central and five explicitly
bound Publisher identities are provisioned.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.telegram_delivery_cutover_contract import (
    API_FORBIDDEN_TOKEN_KEYS,
    api_process_contract,
    bot_process_contract,
    missing_required_env,
    present_forbidden_tokens,
    upsert_env_lines,
)
from scripts.deploy_config import parse_env_file, resolve_deploy_settings
from scripts.plan_telegram_delivery_queue_production import (
    DEFAULT_MANIFEST,
    DEFAULT_STAGING_ENV,
    PRODUCTION_IRAN_PROJECT_DIR,
    TOKEN_KEYS,
    ReadinessBlocked,
    _immutable_source,
    credential_status,
    git_binding,
    queue_target_values,
    run_preflight,
    source_profile,
)
from scripts.scan_telegram_queue_artifacts import scan_paths


APPLY_CONFIRMATION = "CUTOVER PRODUCTION TELEGRAM DELIVERY TO QUEUE-V1"
ROLLBACK_CONFIRMATION = "ROLLBACK PRODUCTION TELEGRAM DELIVERY TO LEGACY"
DEFAULT_ARTIFACT_DIR = Path("/root/secure-envs/trading-bot/queue-cutover-artifacts")
PREFLIGHT_MAXIMUM_AGE_SECONDS = 900
ROLLBACK_RECEIPT_MAXIMUM_AGE_SECONDS = 86400
FOREIGN_PROJECT = "trading_bot"
IRAN_PROJECT = "current"
FOREIGN_CONTAINERS = {
    "app": "trading_bot_app",
    "sync": "trading_bot_sync_worker",
    "migration": "trading_bot_migration",
    "bot": "trading_bot_bot",
    "db": "trading_bot_db",
}
IRAN_CONTAINERS = {
    "app": "trading_bot_app",
    "sync": "trading_bot_sync_worker",
    "migration": "trading_bot_migration",
    "db": "trading_bot_db",
}
OPEN_QUEUE_KEYS = (
    "jobs_pending",
    "jobs_leased",
    "jobs_ambiguous",
    "pending_outcomes",
    "active_resume",
    "active_gates",
    "dispatch_open",
    "outbox_open",
)
NORMAL_RETURN_PROCESS_TERMINATION_GRACE_SECONDS = 0.25
NORMAL_RETURN_PROCESS_KILL_TIMEOUT_SECONDS = 2.0


class ProductionCutoverError(RuntimeError):
    def __init__(self, code: str, *, receipt_sha256: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.receipt_sha256 = receipt_sha256


@dataclass(frozen=True, slots=True)
class SecureSourceBackup:
    path: Path
    sha256: str
    original: bytes


class ExclusiveRunLock:
    def __init__(self, artifact_dir: Path) -> None:
        self.directory = _ensure_secure_artifact_dir(artifact_dir)
        self.path = self.directory / "production-release.lock"
        self.held = False
        self.descriptor: int | None = None
        self.nonce = secrets.token_hex(32)
        self.device: int | None = None
        self.inode: int | None = None

    def acquire(self) -> None:
        for journal in self.directory.glob("production-queue-phase-*.json"):
            try:
                state = json.loads(journal.read_text(encoding="utf-8")).get("status")
            except (OSError, ValueError):
                raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL") from None
            if state not in {"applied", "rolled_back", "failed_recovered"}:
                raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")
        try:
            descriptor = os.open(
                self.path,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            raise ProductionCutoverError("BLOCKED_CONCURRENT_OR_INTERRUPTED_CUTOVER") from None
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            metadata = os.fstat(descriptor)
            payload = {
                "environment": "production",
                "created_at": _utc_now(),
                "nonce_sha256": hashlib.sha256(self.nonce.encode("utf-8")).hexdigest(),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }
            os.write(
                descriptor,
                (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
            )
            os.fsync(descriptor)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            os.close(descriptor)
            self.path.unlink(missing_ok=True)
            raise
        self.descriptor = descriptor
        self.device = metadata.st_dev
        self.inode = metadata.st_ino
        self.held = True

    def binding(self) -> dict[str, Any]:
        if not self.held or self.descriptor is None:
            raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY")
        metadata = os.fstat(self.descriptor)
        if metadata.st_dev != self.device or metadata.st_ino != self.inode:
            raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY")
        return {
            "nonce_sha256": hashlib.sha256(self.nonce.encode("utf-8")).hexdigest(),
            "device": self.device,
            "inode": self.inode,
        }

    def release(self) -> None:
        if self.held:
            try:
                metadata = self.path.lstat()
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raise ProductionCutoverError("BLOCKED_RELEASE_LOCK_OWNERSHIP") from None
                expected = self.binding()
                if (
                    self.path.is_symlink()
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or metadata.st_nlink != 1
                    or metadata.st_dev != expected["device"]
                    or metadata.st_ino != expected["inode"]
                    or payload.get("nonce_sha256") != expected["nonce_sha256"]
                    or payload.get("device") != expected["device"]
                    or payload.get("inode") != expected["inode"]
                ):
                    raise ProductionCutoverError("BLOCKED_RELEASE_LOCK_OWNERSHIP")
                self.path.unlink()
                directory_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if self.descriptor is not None:
                    fcntl.flock(self.descriptor, fcntl.LOCK_UN)
                    os.close(self.descriptor)
                    self.descriptor = None
                self.held = False


class PhaseJournal:
    def __init__(
        self,
        artifact_dir: Path,
        *,
        command: str,
        source_sha256: str,
        git_head: str,
        run_lock: ExclusiveRunLock,
    ) -> None:
        lock_binding = run_lock.binding()
        self.payload: dict[str, Any] = {
            "schema_version": 1,
            "environment": "production",
            "command": command,
            "status": "prepared",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "source_sha256": source_sha256,
            "git_head": git_head,
            "run_lock": lock_binding,
            "secrets_disclosed": False,
        }
        self.path, _digest = _write_secure_json(
            artifact_dir, "production-queue-phase", self.payload
        )

    def update(self, status: str, **facts: Any) -> None:
        self.payload.update(facts)
        self.payload["status"] = status
        self.payload["updated_at"] = _utc_now()
        rendered = (
            json.dumps(self.payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _atomic_write(self.path, rendered)
        if scan_paths([self.path]).get("status") != "clean":
            raise ProductionCutoverError("PHASE_JOURNAL_REDACTION_FAILED")


class ImmutableSourceLock:
    """Cross-tool lock shared by production immutable-source mutators."""

    def __init__(self, source: Path) -> None:
        self.path = source.parent / ".production-runtime-source.lock"
        self.descriptor: int | None = None

    def acquire(self) -> None:
        if self.path.is_symlink():
            raise ProductionCutoverError("BLOCKED_IMMUTABLE_SOURCE_LOCK")
        descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise ProductionCutoverError("BLOCKED_IMMUTABLE_SOURCE_LOCK")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProductionCutoverError("BLOCKED_CONCURRENT_SOURCE_UPDATE") from None
        except BaseException:
            os.close(descriptor)
            raise
        self.descriptor = descriptor

    def release(self) -> None:
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None


@contextlib.contextmanager
def fail_safe_signal_guard():
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous: dict[int, Any] = {}

    def interrupt(signum, _frame):
        raise KeyboardInterrupt(f"production_cutover_signal:{signum}")

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_source_digest(path: Path, expected: str) -> None:
    if _sha256(path) != expected:
        raise ProductionCutoverError("BLOCKED_SOURCE_DRIFT")


def _process_group_exists(process_group_id: int) -> bool:
    """Return whether the captured process group still has kernel members."""

    try:
        os.killpg(int(process_group_id), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM still proves that the process group exists.
        return True
    return True


def _process_group_has_live_members(process_group_id: int) -> bool:
    """Return whether the group contains a member capable of more work."""

    group_id = int(process_group_id)
    if not _process_group_exists(group_id):
        return False
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError:
        return True
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text(encoding="utf-8")
            suffix = stat_text[stat_text.rindex(") ") + 2 :].split()
            state = suffix[0]
            member_group = int(suffix[2])
        except (OSError, IndexError, ValueError):
            continue
        if member_group == group_id and state != "Z":
            return True
    return False


def _wait_for_process_group_exit(process_group_id: int, timeout: float) -> bool:
    """Poll until the group has no live members, ignoring inert zombies."""

    deadline = time.monotonic() + max(float(timeout), 0.0)
    while _process_group_has_live_members(process_group_id):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
    return True


def _terminate_process_group(
    process: subprocess.Popen[str],
    *,
    process_group_id: int | None = None,
    grace_seconds: float = 5.0,
    kill_seconds: float = 5.0,
) -> tuple[str, str]:
    group_id = int(process_group_id or process.pid)
    term_deadline = time.monotonic() + max(float(grace_seconds), 0.0)
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    communicate_timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        communicate_timed_out = True
        stdout = stderr = ""
    remaining_grace = max(0.0, term_deadline - time.monotonic())
    group_stopped = _wait_for_process_group_exit(group_id, remaining_grace)
    needs_kill = communicate_timed_out or process.poll() is None or not group_stopped
    if needs_kill:
        kill_deadline = time.monotonic() + max(float(kill_seconds), 0.0)
        try:
            os.killpg(group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        kill_communicate_timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=kill_seconds)
        except subprocess.TimeoutExpired:
            kill_communicate_timed_out = True
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        remaining_kill = max(0.0, kill_deadline - time.monotonic())
        group_stopped = _wait_for_process_group_exit(group_id, remaining_kill)
        if kill_communicate_timed_out or not group_stopped:
            raise ProductionCutoverError("CHILD_PROCESS_GROUP_NOT_STOPPED") from None
    if process.poll() is None:
        raise ProductionCutoverError("CHILD_PROCESS_GROUP_NOT_STOPPED")
    return stdout or "", stderr or ""


def _run_contained_process(
    args: list[str],
    *,
    cwd: Path,
    timeout: int | float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=None if env is None else dict(env),
        start_new_session=True,
    )
    process_group_id = process.pid
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stdout, stderr = _terminate_process_group(
            process, process_group_id=process_group_id
        )
        return subprocess.CompletedProcess(args, 124, stdout, stderr)
    except BaseException:
        _terminate_process_group(process, process_group_id=process_group_id)
        raise
    if _process_group_has_live_members(process_group_id):
        _terminate_process_group(
            process,
            process_group_id=process_group_id,
            grace_seconds=NORMAL_RETURN_PROCESS_TERMINATION_GRACE_SECONDS,
            kill_seconds=NORMAL_RETURN_PROCESS_KILL_TIMEOUT_SECONDS,
        )
        return subprocess.CompletedProcess(args, 125, stdout or "", stderr or "")
    return subprocess.CompletedProcess(
        args, int(process.returncode or 0), stdout or "", stderr or ""
    )


def _read_json_evidence(path: Path, expected_digest: str) -> dict[str, Any]:
    if not path.is_file() or _sha256(path) != str(expected_digest or "").strip().lower():
        raise ProductionCutoverError("BLOCKED_EVIDENCE_DIGEST")
    scan = scan_paths([path])
    if scan.get("status") != "clean":
        raise ProductionCutoverError("BLOCKED_EVIDENCE_REDACTION")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ProductionCutoverError("BLOCKED_EVIDENCE_FORMAT") from None
    if not isinstance(payload, dict):
        raise ProductionCutoverError("BLOCKED_EVIDENCE_FORMAT")
    return payload


def _parse_timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ProductionCutoverError("BLOCKED_PREFLIGHT_FRESHNESS") from None
    if parsed.utcoffset() is None:
        raise ProductionCutoverError("BLOCKED_PREFLIGHT_FRESHNESS")
    return parsed.astimezone(timezone.utc)


def verify_preflight_evidence(
    path: Path,
    digest: str,
    *,
    backup_digest: str,
    source_digest: str,
    binding: Mapping[str, str],
) -> dict[str, Any]:
    payload = _read_json_evidence(path, digest)
    age = (datetime.now(timezone.utc) - _parse_timestamp(payload.get("observed_at"))).total_seconds()
    if age < -300 or age > PREFLIGHT_MAXIMUM_AGE_SECONDS:
        raise ProductionCutoverError("BLOCKED_PREFLIGHT_FRESHNESS")
    if (
        payload.get("environment") != "production"
        or payload.get("mode") != "read-only"
        or payload.get("status") != "READY_FOR_SEPARATE_CUTOVER_CHOREOGRAPHY"
        or payload.get("apply_supported") is not False
        or payload.get("target_queue_cutover") is not True
        or payload.get("source_profile") != "legacy"
        or payload.get("source_sha256") != source_digest
        or not (payload.get("queue_profile") or {}).get("ready")
        or (payload.get("credentials") or {}).get("status") != "ready"
        or (payload.get("credentials") or {}).get("identity_count") != 6
        or (payload.get("credentials") or {}).get("publisher_count") != 5
        or (payload.get("backup") or {}).get("status") != "verified"
        or (payload.get("backup") or {}).get("digest") != backup_digest
        or (payload.get("backup") or {}).get("target_binding_exact") is not True
        or (payload.get("backup") or {}).get(
            "release_and_database_identity_exact"
        )
        is not True
        or not (payload.get("hosts") or {}).get("ready")
        or not (payload.get("hosts") or {}).get("release_sha_exact")
        or not (payload.get("hosts") or {}).get("database_identity_exact")
        or not (payload.get("hosts") or {}).get(
            "schema_head_and_queue_tables_exact"
        )
        or (payload.get("provider") or {}).get("status") != "approved"
        or (payload.get("provider") or {}).get("identity_count") != 6
        or (payload.get("provider") or {}).get("staging_identity_count") != 6
        or payload.get("git") != dict(binding)
    ):
        raise ProductionCutoverError("BLOCKED_PREFLIGHT_CONTRACT")
    return {
        "status": "verified",
        "preflight_sha256": digest,
        "backup_sha256": backup_digest,
        "identity_count": 6,
        "publisher_count": 5,
        "fresh": True,
    }


def _queue_source_updates(values: Mapping[str, str]) -> dict[str, str]:
    target = queue_target_values(values)
    keys = (
        "TELEGRAM_DELIVERY_PRODUCER_MODE",
        "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER",
        "TELEGRAM_DELIVERY_EXECUTION_OWNER",
        "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED",
        "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY",
        "TELEGRAM_MULTI_PUBLISHER_ENABLED",
        "TELEGRAM_B2B_DISPATCH_ENABLED",
        "TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER",
        "TELEGRAM_NON_BOT_BOT_TOKEN",
        "TELEGRAM_PROVIDER_TEST_AUTHORITY",
        *(f"TELEGRAM_PUBLISHER_{index}_ENABLED" for index in range(1, 6)),
    )
    return {key: target[key] for key in keys}


def _legacy_source_updates() -> dict[str, str]:
    updates = {
        "TELEGRAM_DELIVERY_PRODUCER_MODE": "legacy",
        "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "legacy",
        "TELEGRAM_DELIVERY_EXECUTION_OWNER": "legacy",
        "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
        "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
        "TELEGRAM_MULTI_PUBLISHER_ENABLED": "false",
        "TELEGRAM_B2B_DISPATCH_ENABLED": "false",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED": "false",
        "TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER": "legacy",
        "TELEGRAM_NON_BOT_BOT_TOKEN": "",
        "TELEGRAM_PROVIDER_TEST_AUTHORITY": "false",
    }
    updates.update(
        {f"TELEGRAM_PUBLISHER_{index}_ENABLED": "false" for index in range(1, 6)}
    )
    return updates


def validate_official_release_profile(manifest_values: Mapping[str, str]) -> None:
    expected = {
        "IRAN_SKIP_FOREIGN_DEPLOY": "0",
        "IRAN_DEPLOY_WITH_WAIT": "1",
        "IRAN_RUN_POST_DEPLOY_HEALTHCHECK": "1",
        "IRAN_ALLOW_DIRTY_RELEASE": "0",
        "IRAN_ALLOW_NON_MAIN_RELEASE": "0",
        "IRAN_ALLOW_RELEASE_BRANCH_DRIFT": "0",
        "IRAN_SHARED_DATA_MODE": "skip",
        "IRAN_SHARED_RESET_CONFIRM": "",
    }
    if any(
        str(manifest_values.get(key) or "").strip().lower() != expected_value
        for key, expected_value in expected.items()
    ):
        raise ProductionCutoverError("BLOCKED_UNSAFE_PRODUCTION_RELEASE_PROFILE")


def _atomic_write(path: Path, body: bytes) -> None:
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def backup_and_update_source(
    source: Path,
    secure_backup_dir: Path,
    updates: Mapping[str, str],
    *,
    expected_source_sha256: str,
    source_lock_held: bool = False,
) -> tuple[SecureSourceBackup, dict[str, Any]]:
    local_lock: ImmutableSourceLock | None = None
    if not source_lock_held:
        local_lock = ImmutableSourceLock(source)
        local_lock.acquire()
    try:
        return _backup_and_update_source_locked(
            source,
            secure_backup_dir,
            updates,
            expected_source_sha256=expected_source_sha256,
        )
    finally:
        if local_lock is not None:
            local_lock.release()


def _backup_and_update_source_locked(
    source: Path,
    secure_backup_dir: Path,
    updates: Mapping[str, str],
    *,
    expected_source_sha256: str,
) -> tuple[SecureSourceBackup, dict[str, Any]]:
    if secure_backup_dir.is_symlink():
        raise ProductionCutoverError("BLOCKED_SECURE_BACKUP_DIRECTORY")
    resolved_backup_dir = secure_backup_dir.resolve(strict=False)
    if (
        not resolved_backup_dir.is_dir()
        or resolved_backup_dir.stat().st_uid != os.geteuid()
        or stat.S_IMODE(resolved_backup_dir.stat().st_mode) & 0o077
        or resolved_backup_dir == REPO_ROOT
        or REPO_ROOT in resolved_backup_dir.parents
    ):
        raise ProductionCutoverError("BLOCKED_SECURE_BACKUP_DIRECTORY")
    original = source.read_bytes()
    if hashlib.sha256(original).hexdigest() != expected_source_sha256:
        raise ProductionCutoverError("BLOCKED_SOURCE_DRIFT")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = resolved_backup_dir / f"telegram-queue-runtime-source-{stamp}.bak"
    descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(original)
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(resolved_backup_dir, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    backup = SecureSourceBackup(backup_path, _sha256(backup_path), original)
    updated = upsert_env_lines(original.decode("utf-8"), updates).encode("utf-8")
    try:
        _atomic_write(source, updated)
    except Exception:
        # If replacement succeeded but directory fsync failed, force the
        # original profile back before control returns to the caller.
        _atomic_write(source, original)
        raise
    return backup, {
        "status": "updated_atomically",
        "backup_sha256": backup.sha256,
        "source_before_sha256": hashlib.sha256(original).hexdigest(),
        "source_after_sha256": hashlib.sha256(updated).hexdigest(),
        "backup_file": backup_path.name,
        "backup_path_binding_sha256": hashlib.sha256(
            str(backup_path.resolve(strict=False)).encode("utf-8")
        ).hexdigest(),
        "updated_keys": sorted(updates),
        "secret_values_disclosed": False,
    }


def restore_source_from_backup(
    source: Path,
    backup: SecureSourceBackup,
    *,
    expected_current_sha256: str | None = None,
) -> dict[str, Any]:
    if _sha256(backup.path) != backup.sha256 or backup.path.read_bytes() != backup.original:
        raise ProductionCutoverError("BLOCKED_SOURCE_BACKUP_DIGEST")
    if expected_current_sha256 and _sha256(source) != expected_current_sha256:
        raise ProductionCutoverError("BLOCKED_SOURCE_DRIFT")
    _atomic_write(source, backup.original)
    return {
        "status": "restored_atomically",
        "backup_sha256": backup.sha256,
        "source_sha256": hashlib.sha256(backup.original).hexdigest(),
        "schema_downgrade": False,
    }


def executor_inventory_from_observation(
    *,
    running_container_count: int,
    expected_container_name: bool,
    process_count: int,
    host_process_count: int,
    iran_host_process_count: int,
    env: Mapping[str, str],
    runtime_decision: Mapping[str, Any],
) -> dict[str, Any]:
    if running_container_count == 0:
        if host_process_count or iran_host_process_count:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_AMBIGUOUS")
        return {"count": 0, "owner": None, "overlap": False}
    if (
        running_container_count != 1
        or not expected_container_name
        or process_count != 1
        or host_process_count != 1
        or iran_host_process_count != 0
        or str(env.get("TRADING_BOT_SERVICE") or "") != "bot"
        or str(env.get("SERVER_MODE") or "") != "foreign"
    ):
        raise ProductionCutoverError("EXECUTOR_INVENTORY_AMBIGUOUS")
    owner = str(env.get("TELEGRAM_DELIVERY_EXECUTION_OWNER") or "legacy").lower()
    queue_flag = str(env.get("TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED") or "false").lower() == "true"
    legacy_actual = runtime_decision.get("legacy_workers_enabled") is True
    queue_actual = runtime_decision.get("queue_worker_enabled") is True
    mode_actual = str(runtime_decision.get("mode") or "").lower()
    overlap = legacy_actual and queue_actual
    if (
        overlap
        or mode_actual != owner
        or queue_flag != queue_actual
        or (owner == "legacy" and not legacy_actual)
        or (owner == "queue-v1" and not queue_actual)
        or owner not in {"legacy", "queue-v1"}
    ):
        raise ProductionCutoverError("EXECUTOR_RUNTIME_OWNERSHIP_MISMATCH")
    return {"count": 1, "owner": owner, "overlap": False}


class ProductionOperations:
    """Production mutations used only after every static/read-only gate passes."""

    def __init__(self, manifest: Path) -> None:
        self.manifest = manifest
        self.manifest_values = parse_env_file(manifest)
        self.settings = resolve_deploy_settings(
            manifest_path=str(manifest), environ={}
        )
        key = str(self.manifest_values.get("IRAN_SSH_PRIVATE_KEY_PATH") or "").strip()
        if (
            str(self.manifest_values.get("IRAN_SSH_AUTH_METHOD") or "").strip().lower() != "key"
            or not key
            or str(self.manifest_values.get("IRAN_SSH_PASSWORD") or "").strip()
        ):
            raise ProductionCutoverError("BLOCKED_PRODUCTION_KEY_ONLY_SSH")
        self.ssh_key = Path(key).expanduser().resolve(strict=False)
        if not self.ssh_key.is_file() or stat.S_IMODE(self.ssh_key.stat().st_mode) & 0o077:
            raise ProductionCutoverError("BLOCKED_PRODUCTION_KEY_ONLY_SSH")

    def _run(self, args: list[str], *, timeout: int = 120, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return _run_contained_process(
            args,
            cwd=REPO_ROOT,
            timeout=timeout,
            env=None if env is None else dict(env),
        )

    def _docker(self, role: str, args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        if role == "foreign":
            return self._run(["docker", *args], timeout=timeout)
        remote = "docker " + " ".join(shlex.quote(part) for part in args)
        return self._run(
            [
                "ssh",
                "-p",
                str(self.settings["IRAN_SSH_PORT"]),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                "KbdInteractiveAuthentication=no",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-i",
                str(self.ssh_key),
                str(self.settings["IRAN_SSH_TARGET"]),
                remote,
            ],
            timeout=timeout,
        )

    def _host(
        self, role: str, args: list[str], *, timeout: int = 120
    ) -> subprocess.CompletedProcess[str]:
        if role == "foreign":
            return self._run(args, timeout=timeout)
        remote = " ".join(shlex.quote(part) for part in args)
        return self._run(
            [
                "ssh",
                "-p",
                str(self.settings["IRAN_SSH_PORT"]),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                "KbdInteractiveAuthentication=no",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-i",
                str(self.ssh_key),
                str(self.settings["IRAN_SSH_TARGET"]),
                remote,
            ],
            timeout=timeout,
        )

    def _host_bot_process_count(self, role: str) -> int:
        result = self._host(role, ["ps", "-eo", "args="])
        if result.returncode:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_READBACK_FAILED")
        return sum(
            "run_bot.py" in line and "python" in line.lower()
            for line in (result.stdout or "").splitlines()
        )

    def _potential_bot_containers(self, role: str) -> list[str]:
        listed = self._docker(role, ["ps", "-q"])
        if listed.returncode:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_READBACK_FAILED")
        candidates: list[str] = []
        for container_id in (
            line.strip() for line in (listed.stdout or "").splitlines() if line.strip()
        ):
            observed = self._docker(
                role,
                [
                    "inspect",
                    "-f",
                    '{{json .Config.Env}}\t{{json .Config.Cmd}}\t{{json .Config.Entrypoint}}\t{{index .Config.Labels "com.docker.compose.service"}}',
                    container_id,
                ],
            )
            if observed.returncode:
                raise ProductionCutoverError("EXECUTOR_INVENTORY_READBACK_FAILED")
            raw = (observed.stdout or "").strip()
            if (
                "TRADING_BOT_SERVICE=bot" in raw
                or "run_bot.py" in raw
                or raw.endswith("\tbot")
            ):
                candidates.append(container_id)
        return candidates

    def _require_project(self, role: str, container: str) -> None:
        result = self._docker(
            role,
            ["inspect", "-f", '{{index .Config.Labels "com.docker.compose.project"}}', container],
        )
        expected = FOREIGN_PROJECT if role == "foreign" else IRAN_PROJECT
        if result.returncode or (result.stdout or "").strip() != expected:
            raise ProductionCutoverError("BLOCKED_PRODUCTION_PROJECT_IDENTITY")

    def _running(self, role: str, container: str) -> bool:
        self._require_project(role, container)
        result = self._docker(role, ["inspect", "-f", "{{.State.Running}}", container])
        return result.returncode == 0 and (result.stdout or "").strip() == "true"

    def _container_env(self, role: str, container: str) -> dict[str, str]:
        self._require_project(role, container)
        result = self._docker(role, ["inspect", "-f", "{{json .Config.Env}}", container])
        if result.returncode:
            raise ProductionCutoverError("BLOCKED_RUNTIME_ENV_READBACK")
        try:
            rows = json.loads(result.stdout or "[]")
        except ValueError:
            raise ProductionCutoverError("BLOCKED_RUNTIME_ENV_READBACK") from None
        return {
            row.split("=", 1)[0]: row.split("=", 1)[1]
            for row in rows
            if isinstance(row, str) and "=" in row
        }

    def _compose_service_env(self, role: str, service: str) -> dict[str, str]:
        project_dir = (
            str(self.manifest_values.get("LOCAL_PROJECT_DIR") or REPO_ROOT)
            if role == "foreign"
            else str(self.manifest_values.get("IRAN_PROJECT_DIR") or "")
        )
        compose_file = (
            "docker-compose.yml" if role == "foreign" else "docker-compose.iran.yml"
        )
        if not project_dir.startswith("/"):
            raise ProductionCutoverError("BLOCKED_PRODUCTION_PROJECT_IDENTITY")
        script = (
            "set -euo pipefail; "
            f"cd {shlex.quote(project_dir)}; "
            "if docker compose version >/dev/null 2>&1; then "
            f"docker compose -f {shlex.quote(compose_file)} config --format json; "
            "elif command -v docker-compose >/dev/null 2>&1; then "
            f"docker-compose -f {shlex.quote(compose_file)} config --format json; "
            "else exit 127; fi"
        )
        result = self._host(role, ["sh", "-lc", script])
        if result.returncode:
            raise ProductionCutoverError("COMPOSE_ROLE_CONFIG_READBACK_FAILED")
        try:
            environment = json.loads(result.stdout or "{}")["services"][service][
                "environment"
            ]
        except (KeyError, TypeError, ValueError):
            raise ProductionCutoverError("COMPOSE_ROLE_CONFIG_READBACK_FAILED") from None
        if isinstance(environment, dict):
            return {
                str(key): "" if value is None else str(value)
                for key, value in environment.items()
            }
        if isinstance(environment, list):
            return {
                row.split("=", 1)[0]: row.split("=", 1)[1]
                for row in environment
                if isinstance(row, str) and "=" in row
            }
        raise ProductionCutoverError("COMPOSE_ROLE_CONFIG_READBACK_FAILED")

    def stop_producers(self) -> list[tuple[str, str]]:
        stopped: list[tuple[str, str]] = []
        for role in ("foreign", "iran"):
            project = FOREIGN_PROJECT if role == "foreign" else IRAN_PROJECT
            active_migrations = self._docker(
                role,
                [
                    "ps",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                    "--filter",
                    "label=com.docker.compose.service=migration",
                ],
            )
            if active_migrations.returncode:
                raise ProductionCutoverError("MIGRATION_ACTIVITY_READBACK_FAILED")
            if (active_migrations.stdout or "").strip():
                raise ProductionCutoverError("BLOCKED_ACTIVE_MIGRATION_PRODUCER")
        try:
            for role, containers in (("foreign", FOREIGN_CONTAINERS), ("iran", IRAN_CONTAINERS)):
                for service in ("app", "sync"):
                    container = containers[service]
                    if self._running(role, container):
                        result = self._docker(role, ["stop", container], timeout=180)
                        if result.returncode:
                            raise ProductionCutoverError("PRODUCER_QUIESCE_FAILED")
                        stopped.append((role, service))
        except BaseException:
            try:
                self.resume_producers(stopped)
            except BaseException:
                raise ProductionCutoverError(
                    "PRODUCER_QUIESCE_PARTIAL_RECOVERY_FAILED"
                ) from None
            raise
        return stopped

    def resume_producers(self, stopped: list[tuple[str, str]]) -> None:
        failed = False
        for role, service in stopped:
            container = (FOREIGN_CONTAINERS if role == "foreign" else IRAN_CONTAINERS)[service]
            result = self._docker(role, ["start", container], timeout=180)
            if result.returncode:
                failed = True
        if failed:
            raise ProductionCutoverError("PRODUCER_RESUME_FAILED")

    def stop_bot(self) -> None:
        if self._running("foreign", FOREIGN_CONTAINERS["bot"]):
            result = self._docker("foreign", ["stop", FOREIGN_CONTAINERS["bot"]], timeout=180)
            if result.returncode:
                raise ProductionCutoverError("BOT_STOP_FAILED")

    def start_bot(self) -> None:
        result = self._docker("foreign", ["start", FOREIGN_CONTAINERS["bot"]], timeout=180)
        if result.returncode:
            raise ProductionCutoverError("BOT_START_FAILED")

    def executor_inventory(self) -> dict[str, Any]:
        iran_host_process_count = self._host_bot_process_count("iran")
        if self._potential_bot_containers("iran") or iran_host_process_count:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_AMBIGUOUS")
        host_process_count = self._host_bot_process_count("foreign")
        container_ids = self._potential_bot_containers("foreign")
        if not container_ids:
            return executor_inventory_from_observation(
                running_container_count=0,
                expected_container_name=False,
                process_count=0,
                host_process_count=host_process_count,
                iran_host_process_count=iran_host_process_count,
                env={},
                runtime_decision={},
            )
        if len(container_ids) != 1:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_AMBIGUOUS")
        container_id = container_ids[0]
        name = self._docker("foreign", ["inspect", "-f", "{{.Name}}", container_id])
        if name.returncode:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_READBACK_FAILED")
        env = self._container_env("foreign", container_id)
        top = self._docker("foreign", ["top", container_id, "-eo", "args"])
        if top.returncode:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_READBACK_FAILED")
        process_count = sum(
            "run_bot.py" in line and "python" in line.lower()
            for line in (top.stdout or "").splitlines()[1:]
        )
        decision_result = self._docker(
            "foreign",
            [
                "exec",
                "-w",
                "/app",
                container_id,
                "python",
                "-c",
                (
                    "import json; "
                    "from core.telegram_delivery_runtime_policy import configured_telegram_delivery_runtime; "
                    "r=configured_telegram_delivery_runtime(); "
                    "print(json.dumps({'mode':r.mode.value,'legacy_workers_enabled':r.legacy_workers_enabled,'queue_worker_enabled':r.queue_worker_enabled},sort_keys=True))"
                ),
            ],
        )
        if decision_result.returncode:
            raise ProductionCutoverError("EXECUTOR_RUNTIME_OWNERSHIP_READBACK_FAILED")
        try:
            decision = json.loads((decision_result.stdout or "").strip())
        except ValueError:
            raise ProductionCutoverError("EXECUTOR_RUNTIME_OWNERSHIP_READBACK_FAILED") from None
        return executor_inventory_from_observation(
            running_container_count=1,
            expected_container_name=(name.stdout or "").strip() == f"/{FOREIGN_CONTAINERS['bot']}",
            process_count=process_count,
            host_process_count=host_process_count,
            iran_host_process_count=iran_host_process_count,
            env=env,
            runtime_decision=decision,
        )

    def queue_snapshot(self, role: str) -> dict[str, int]:
        query = (
            "select json_build_object("
            "'jobs_pending',(select count(*) from telegram_delivery_jobs where state in ('pending','pending_retry')),"
            "'jobs_leased',(select count(*) from telegram_delivery_jobs where state='leased'),"
            "'jobs_ambiguous',(select count(*) from telegram_delivery_jobs where state in ('ambiguous','ambiguous_unresolved','pending_reconcile')),"
            "'pending_outcomes',(select count(*) from telegram_delivery_provider_outcomes where apply_state='pending'),"
            "'active_resume',(select count(*) from telegram_delivery_resume_operations where state in ('requested','database_applied','redis_applied')),"
            "'active_gates',(select count(*) from telegram_delivery_runtime_gates where state in ('cooldown','blocked','resume_requested','database_applied')),"
            "'dispatch_open',(select count(*) from telegram_publisher_dispatch_commands where state in ('pending','sent','retry_due')),"
            "'outbox_open',(select count(*) from telegram_notification_outbox where status in ('pending','sending','retryable_failed'))"
            ")"
        )
        container = (FOREIGN_CONTAINERS if role == "foreign" else IRAN_CONTAINERS)["db"]
        self._require_project(role, container)
        result = self._docker(
            role,
            ["exec", container, "sh", "-lc", f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc {shlex.quote(query)}'],
        )
        if result.returncode:
            raise ProductionCutoverError("QUEUE_DRAIN_READBACK_FAILED")
        try:
            payload = json.loads((result.stdout or "").strip())
            return {key: int(payload.get(key) or 0) for key in OPEN_QUEUE_KEYS}
        except (TypeError, ValueError):
            raise ProductionCutoverError("QUEUE_DRAIN_READBACK_FAILED") from None

    def wait_for_drain(self, timeout_seconds: int, poll_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        latest: dict[str, dict[str, int]] = {}
        while True:
            latest = {role: self.queue_snapshot(role) for role in ("foreign", "iran")}
            if all(not any(snapshot[key] for key in OPEN_QUEUE_KEYS) for snapshot in latest.values()):
                return {"status": "drained", "roles": latest}
            if time.monotonic() >= deadline:
                raise ProductionCutoverError("QUEUE_DRAIN_TIMEOUT")
            time.sleep(min(max(poll_seconds, 0.1), 5.0))

    def deploy_official(
        self,
        authority_path: Path | None = None,
        authority_digest: str | None = None,
    ) -> dict[str, Any]:
        # The production manifest is authoritative.  Inheriting an interactive
        # shell wholesale would let values such as COMPOSE_PROJECT_NAME or an
        # IRAN_* target silently split the host inspected by preflight from the
        # host/project used by the official deploy script.
        env = {
            key: os.environ[key]
            for key in ("PATH", "LANG", "LC_ALL", "TZ")
            if str(os.environ.get(key) or "").strip()
        }
        env["IRAN_CONNECTIVITY_MODE"] = "online"
        env["DEPLOY_MANIFEST"] = str(self.manifest)
        if authority_path is not None and authority_digest:
            env["TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT"] = str(authority_path)
            env["TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT_SHA256"] = authority_digest
            env["PRODUCTION_SOURCE_LOCK_INHERITED_CONFIRM"] = (
                "verified-cutover-held-lock"
            )
        result = self._run(
            ["bash", str(REPO_ROOT / "scripts/production_deploy_online.sh"), "--manifest", str(self.manifest), "release"],
            timeout=7200,
            env=env,
        )
        if result.returncode:
            raise ProductionCutoverError("OFFICIAL_PRODUCTION_DEPLOY_FAILED")
        return {"status": "completed", "official_script": True, "output_retained": False}

    def runtime_contract(self, source_values: Mapping[str, str], *, expected_owner: str) -> dict[str, Any]:
        if expected_owner not in {"legacy", "queue-v1"}:
            raise ProductionCutoverError("RUNTIME_OWNER_INVALID")
        violations: list[str] = []
        def check_non_bot_env(role: str, service: str, env: Mapping[str, str]) -> None:
            nonlocal violations
            if role == "iran" and env.get("SERVER_MODE") != "iran":
                violations.append(f"{role}:{service}:mode")
            if role == "foreign" and env.get("SERVER_MODE") != "foreign":
                violations.append(f"{role}:{service}:mode")
            expected_process_owner = "producer-only" if expected_owner == "queue-v1" else "legacy"
            expected = {
                "TELEGRAM_DELIVERY_PRODUCER_MODE": expected_owner,
                "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": expected_owner,
                "TELEGRAM_DELIVERY_EXECUTION_OWNER": expected_process_owner,
                "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
                "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
                "TELEGRAM_MULTI_PUBLISHER_ENABLED": "false",
                "TELEGRAM_B2B_DISPATCH_ENABLED": "false",
                "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED": "false",
                **{f"TELEGRAM_PUBLISHER_{index}_ENABLED": "false" for index in range(1, 6)},
            }
            if missing_required_env(env, type("Contract", (), {"required": expected})()):
                violations.append(f"{role}:{service}:role")
            forbidden = {key: bool(str(env.get(key) or "").strip()) for key in API_FORBIDDEN_TOKEN_KEYS}
            if any(forbidden.values()):
                # Legacy compatibility permits only the central token on
                # foreign non-bot processes; Queue and Iran permit none.
                if not (
                    expected_owner == "legacy"
                    and role == "foreign"
                    and forbidden.get("BOT_TOKEN")
                    and not any(forbidden[key] for key in API_FORBIDDEN_TOKEN_KEYS if key != "BOT_TOKEN")
                ):
                    violations.append(f"{role}:{service}:token")

        for role, containers in (("foreign", FOREIGN_CONTAINERS), ("iran", IRAN_CONTAINERS)):
            for service in ("app", "sync"):
                env = self._container_env(role, containers[service])
                check_non_bot_env(role, service, env)
            # Migration is a run --rm service and intentionally has no stable
            # named container after deploy.  Validate its fully resolved
            # Compose role without executing the migration command.
            check_non_bot_env(
                role, "migration", self._compose_service_env(role, "migration")
            )

        bot_env = self._container_env("foreign", FOREIGN_CONTAINERS["bot"])
        if expected_owner == "queue-v1":
            if missing_required_env(bot_env, bot_process_contract()):
                violations.append("foreign:bot:role")
            for key in TOKEN_KEYS:
                if str(bot_env.get(key) or "") != str(source_values.get(key) or ""):
                    violations.append("foreign:bot:provider-credential")
            for key in (
                "BOT_USERNAME",
                "CHANNEL_ID",
                "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID",
                "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID",
                "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID",
                "TELEGRAM_DELIVERY_QUEUE_SHARED_PUBLISHER_FLEET_ENABLED",
            ):
                if str(bot_env.get(key) or "") != str(source_values.get(key) or ""):
                    violations.append("foreign:bot:identity-lock")
            for index in range(1, 6):
                prefix = f"TELEGRAM_PUBLISHER_{index}"
                if str(bot_env.get(f"{prefix}_ENABLED") or "").lower() != "true":
                    violations.append(f"foreign:bot:publisher-{index}")
                for suffix in ("BOT_TOKEN", "EXPECTED_BOT_ID", "EXPECTED_USERNAME"):
                    if str(bot_env.get(f"{prefix}_{suffix}") or "") != str(source_values.get(f"{prefix}_{suffix}") or ""):
                        violations.append(f"foreign:bot:publisher-{index}")
        else:
            if bot_env.get("TELEGRAM_DELIVERY_EXECUTION_OWNER", "legacy") != "legacy":
                violations.append("foreign:bot:legacy-owner")
            if str(bot_env.get("TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED") or "false").lower() != "false":
                violations.append("foreign:bot:queue-worker")

        remote_bot = self._docker(
            "iran",
            ["ps", "-aq", "--filter", f"label=com.docker.compose.project={IRAN_PROJECT}", "--filter", "label=com.docker.compose.service=bot"],
        )
        if remote_bot.returncode or (remote_bot.stdout or "").strip():
            violations.append("iran:bot-present")
        if violations:
            raise ProductionCutoverError("POST_DEPLOY_ROLE_CONTRACT_FAILED")
        return {
            "status": "verified",
            "owner": expected_owner,
            "foreign_non_bot_count": 3,
            "iran_non_bot_count": 3,
            "publisher_count": 5 if expected_owner == "queue-v1" else 0,
            "tokens_disclosed": False,
        }

    def queue_health(self, database_name: str) -> dict[str, Any]:
        result = self._docker(
            "foreign",
            [
                "exec", "-w", "/app", FOREIGN_CONTAINERS["bot"], "sh", "-lc",
                'export TELEGRAM_QUEUE_OBSERVABILITY_DATABASE_URL="$DATABASE_URL"; '
                "python scripts/report_telegram_delivery_queue_health.py "
                f"--environment production --expected-database-name {shlex.quote(database_name)} "
                "--production-read-only-authority 'PRODUCTION TELEGRAM QUEUE HEALTH READ ONLY' "
                "--sample-window-seconds 5",
            ],
            timeout=180,
        )
        if result.returncode not in {0, 2}:
            raise ProductionCutoverError("QUEUE_HEALTH_READBACK_FAILED")
        try:
            payload = json.loads(result.stdout or "{}")
        except ValueError:
            raise ProductionCutoverError("QUEUE_HEALTH_READBACK_FAILED") from None
        report = payload.get("report", payload)
        health = report.get("health", report) if isinstance(report, dict) else {}
        decision = str(health.get("decision") or "").lower()
        if decision != "continue":
            raise ProductionCutoverError("QUEUE_HEALTH_STOP")
        return {
            "status": "passed",
            "decision": decision,
            "ready_depth": health.get("ready_depth"),
            "state_counts": health.get("state_counts"),
        }

    def b2b_lane_probe(self) -> dict[str, Any]:
        query = (
            "select json_build_object("
            "'invalid_lane_jobs',(select count(*) from telegram_delivery_jobs where bot_identity not in ('primary','channel_editor','publisher_1','publisher_2','publisher_3','publisher_4','publisher_5')),"
            "'open_dispatch',(select count(*) from telegram_publisher_dispatch_commands where state in ('pending','sent','retry_due')),"
            "'publisher_1',(select count(*) from telegram_delivery_jobs where bot_identity='publisher_1'),"
            "'publisher_2',(select count(*) from telegram_delivery_jobs where bot_identity='publisher_2'),"
            "'publisher_3',(select count(*) from telegram_delivery_jobs where bot_identity='publisher_3'),"
            "'publisher_4',(select count(*) from telegram_delivery_jobs where bot_identity='publisher_4'),"
            "'publisher_5',(select count(*) from telegram_delivery_jobs where bot_identity='publisher_5')"
            ")"
        )
        container = FOREIGN_CONTAINERS["db"]
        result = self._docker(
            "foreign",
            ["exec", container, "sh", "-lc", f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc {shlex.quote(query)}'],
        )
        if result.returncode:
            raise ProductionCutoverError("B2B_LANE_PROBE_FAILED")
        try:
            payload = json.loads((result.stdout or "").strip())
        except ValueError:
            raise ProductionCutoverError("B2B_LANE_PROBE_FAILED") from None
        if int(payload.get("invalid_lane_jobs") or 0):
            raise ProductionCutoverError("B2B_LANE_PROBE_FAILED")
        return {
            "status": "passed",
            "synthetic_mutations": 0,
            "invalid_lane_jobs": 0,
            "open_dispatch": int(payload.get("open_dispatch") or 0),
            "lane_counts": {f"publisher_{index}": int(payload.get(f"publisher_{index}") or 0) for index in range(1, 6)},
        }


def _assert_inventory(inventory: Mapping[str, Any], *, count: int, owner: str | None) -> None:
    if inventory.get("count") != count or inventory.get("owner") != owner or inventory.get("overlap"):
        raise ProductionCutoverError("EXECUTOR_TIMELINE_INVALID")


def _ensure_secure_artifact_dir(artifact_dir: Path) -> Path:
    resolved = artifact_dir.resolve(strict=False)
    if (
        artifact_dir.is_symlink()
        or not resolved.is_dir()
        or resolved.stat().st_uid != os.geteuid()
        or stat.S_IMODE(resolved.stat().st_mode) & 0o077
        or resolved == REPO_ROOT
        or REPO_ROOT in resolved.parents
    ):
        raise ProductionCutoverError("BLOCKED_SECURE_ARTIFACT_DIRECTORY")
    return resolved


def _write_secure_json(
    artifact_dir: Path,
    prefix: str,
    payload: Mapping[str, Any],
) -> tuple[Path, str]:
    secure_dir = _ensure_secure_artifact_dir(artifact_dir)
    descriptor, name = tempfile.mkstemp(prefix=f"{prefix}-", suffix=".json", dir=secure_dir)
    path = Path(name)
    os.fchmod(descriptor, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(secure_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if scan_paths([path]).get("status") != "clean":
        path.unlink(missing_ok=True)
        raise ProductionCutoverError("RECEIPT_REDACTION_FAILED")
    return path, _sha256(path)


def _write_redacted_receipt(
    artifact_dir: Path, prefix: str, receipt: Mapping[str, Any]
) -> tuple[Path, str]:
    return _write_secure_json(artifact_dir, prefix, receipt)


def verify_apply_receipt(
    receipt_path: Path,
    receipt_digest: str,
    *,
    source: Path,
    source_backup_path: Path,
    source_backup_digest: str,
    binding: Mapping[str, str],
) -> dict[str, Any]:
    payload = _read_json_evidence(receipt_path, receipt_digest)
    age = (
        datetime.now(timezone.utc) - _parse_timestamp(payload.get("finished_at"))
    ).total_seconds()
    source_switches = [
        item.get("report")
        for item in payload.get("steps") or []
        if isinstance(item, dict)
        and item.get("name") == "atomic_source_switch"
        and isinstance(item.get("report"), dict)
    ]
    if len(source_switches) != 1:
        raise ProductionCutoverError("BLOCKED_APPLY_RECEIPT_BINDING")
    source_switch = source_switches[0]
    if (
        age < -300
        or age > ROLLBACK_RECEIPT_MAXIMUM_AGE_SECONDS
        or payload.get("environment") != "production"
        or payload.get("command") != "apply"
        or payload.get("status") != "applied"
        or payload.get("git") != dict(binding)
        or source_switch.get("backup_file") != source_backup_path.name
        or source_switch.get("backup_path_binding_sha256")
        != hashlib.sha256(
            str(source_backup_path.resolve(strict=False)).encode("utf-8")
        ).hexdigest()
        or source_switch.get("backup_sha256") != source_backup_digest
        or source_switch.get("source_before_sha256") != source_backup_digest
        or source_switch.get("source_after_sha256") != _sha256(source)
    ):
        raise ProductionCutoverError("BLOCKED_APPLY_RECEIPT_BINDING")
    return {
        "status": "verified",
        "receipt_sha256": receipt_digest,
        "source_before_sha256": source_switch["source_before_sha256"],
        "source_after_sha256": source_switch["source_after_sha256"],
        "fresh": True,
        "secrets_disclosed": False,
    }


def _require_secure_authority_file(
    path: Path, artifact_dir: Path, *, prefix: str
) -> os.stat_result:
    secure_dir = _ensure_secure_artifact_dir(artifact_dir)
    resolved = path.resolve(strict=False)
    try:
        metadata = path.lstat()
    except OSError:
        raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY") from None
    if (
        path.is_symlink()
        or resolved.parent != secure_dir
        or not path.name.startswith(prefix)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY")
    return metadata


def create_deploy_authority(
    artifact_dir: Path,
    source: Path,
    binding: Mapping[str, str],
    *,
    run_lock: ExclusiveRunLock,
    journal: PhaseJournal,
) -> tuple[Path, str]:
    secure_dir = _ensure_secure_artifact_dir(artifact_dir)
    lock_binding = run_lock.binding()
    if journal.payload.get("run_lock") != lock_binding or journal.payload.get(
        "status"
    ) in {"applied", "rolled_back", "failed_recovered", "recovery_failed"}:
        raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY")
    _require_secure_authority_file(
        journal.path, secure_dir, prefix="production-queue-phase-"
    )
    target_owner = source_profile(parse_env_file(source))
    phase = (
        "queue-v1-official-deploy"
        if target_owner == "queue-v1"
        else "legacy-recovery-official-deploy"
    )
    handoff_nonce_sha256 = hashlib.sha256(
        secrets.token_bytes(32)
    ).hexdigest()
    state_descriptor, state_name = tempfile.mkstemp(
        prefix="production-queue-deploy-state-", suffix=".json", dir=secure_dir
    )
    os.fchmod(state_descriptor, 0o600)
    state_path = Path(state_name)
    payload = {
        "schema_version": 1,
        "environment": "production",
        "phase": phase,
        "target_owner": target_owner,
        "created_at": _utc_now(),
        "git_head": binding["head"],
        "source_sha256": _sha256(source),
        "handoff_nonce_sha256": handoff_nonce_sha256,
        "state_file": state_path.name,
        "run_lock": lock_binding,
        "journal_file": journal.path.name,
        "journal_sha256": _sha256(journal.path),
        "journal_status": journal.payload.get("status"),
        "secrets_disclosed": False,
    }
    authority_path: Path | None = None
    try:
        authority_path, authority_digest = _write_secure_json(
            secure_dir, "production-queue-deploy-authority", payload
        )
        state = {
            "schema_version": 1,
            "environment": "production",
            "status": "issued",
            "created_at": _utc_now(),
            "authority_file": authority_path.name,
            "authority_sha256": authority_digest,
            "handoff_nonce_sha256": handoff_nonce_sha256,
            "run_lock": lock_binding,
            "journal_file": journal.path.name,
            "journal_sha256": _sha256(journal.path),
            "journal_status": journal.payload.get("status"),
            "target_owner": target_owner,
            "git_head": binding["head"],
            "source_sha256": _sha256(source),
            "secrets_disclosed": False,
        }
        with os.fdopen(state_descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        state_descriptor = -1
        directory_fd = os.open(secure_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return authority_path, authority_digest
    except BaseException:
        if state_descriptor >= 0:
            os.close(state_descriptor)
        state_path.unlink(missing_ok=True)
        if authority_path is not None:
            authority_path.unlink(missing_ok=True)
        raise


def verify_deploy_authority(
    manifest: Path,
    authority_path: Path,
    authority_digest: str,
    *,
    expected_artifact_dir: Path = DEFAULT_ARTIFACT_DIR,
) -> dict[str, Any]:
    artifact_dir = _ensure_secure_artifact_dir(expected_artifact_dir)
    _require_secure_authority_file(
        authority_path, artifact_dir, prefix="production-queue-deploy-authority-"
    )
    payload = _read_json_evidence(authority_path, authority_digest)
    age = (datetime.now(timezone.utc) - _parse_timestamp(payload.get("created_at"))).total_seconds()
    source, _ = _immutable_source(manifest)
    binding = git_binding()
    state_file = str(payload.get("state_file") or "")
    journal_file = str(payload.get("journal_file") or "")
    if Path(state_file).name != state_file or Path(journal_file).name != journal_file:
        raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY")
    state_path = artifact_dir / state_file
    journal_path = artifact_dir / journal_file
    _require_secure_authority_file(
        state_path, artifact_dir, prefix="production-queue-deploy-state-"
    )
    _require_secure_authority_file(
        journal_path, artifact_dir, prefix="production-queue-phase-"
    )
    state_descriptor = os.open(
        state_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fcntl.flock(state_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with os.fdopen(os.dup(state_descriptor), "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (BlockingIOError, OSError, ValueError):
        os.close(state_descriptor)
        raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY") from None
    lock_path = artifact_dir / "production-release.lock"
    lock_metadata = _require_secure_authority_file(
        lock_path, artifact_dir, prefix="production-release.lock"
    )
    try:
        lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
        journal_payload = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        os.close(state_descriptor)
        raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY") from None
    run_binding = payload.get("run_lock")
    lock_is_held = False
    lock_descriptor = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_is_held = True
        else:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
    finally:
        os.close(lock_descriptor)
    source_lock_path = source.parent / ".production-runtime-source.lock"
    source_lock_held = False
    if source_lock_path.is_file() and not source_lock_path.is_symlink():
        descriptor = os.open(
            source_lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                metadata.st_uid == os.geteuid()
                and stat.S_IMODE(metadata.st_mode) == 0o600
                and metadata.st_nlink == 1
            ):
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    source_lock_held = True
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    observed_owner = source_profile(parse_env_file(source))
    expected_phase = (
        "queue-v1-official-deploy"
        if observed_owner == "queue-v1"
        else "legacy-recovery-official-deploy"
    )
    authority_exact = (
        age < -300
        or age > PREFLIGHT_MAXIMUM_AGE_SECONDS
        or payload.get("environment") != "production"
        or payload.get("phase") != expected_phase
        or payload.get("target_owner") != observed_owner
        or payload.get("git_head") != binding.get("head")
        or payload.get("source_sha256") != _sha256(source)
        or binding.get("branch") != "main"
        or binding.get("worktree") != "clean"
        or binding.get("head") != binding.get("origin_main")
        or not source_lock_held
        or not lock_is_held
        or not isinstance(run_binding, dict)
        or lock_metadata.st_dev != run_binding.get("device")
        or lock_metadata.st_ino != run_binding.get("inode")
        or lock_payload.get("device") != run_binding.get("device")
        or lock_payload.get("inode") != run_binding.get("inode")
        or lock_payload.get("nonce_sha256") != run_binding.get("nonce_sha256")
        or payload.get("journal_sha256") != _sha256(journal_path)
        or payload.get("journal_status") != journal_payload.get("status")
        or journal_payload.get("run_lock") != run_binding
        or state.get("status") != "issued"
        or state.get("authority_file") != authority_path.name
        or state.get("authority_sha256") != authority_digest
        or state.get("handoff_nonce_sha256")
        != payload.get("handoff_nonce_sha256")
        or state.get("run_lock") != run_binding
        or state.get("journal_file") != journal_file
        or state.get("journal_sha256") != payload.get("journal_sha256")
        or state.get("journal_status") != payload.get("journal_status")
        or state.get("target_owner") != observed_owner
        or state.get("git_head") != binding.get("head")
        or state.get("source_sha256") != _sha256(source)
    )
    if authority_exact:
        os.close(state_descriptor)
        raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY")
    state["status"] = "consumed"
    state["consumed_at"] = _utc_now()
    rendered_state = (json.dumps(state, sort_keys=True) + "\n").encode("utf-8")
    os.lseek(state_descriptor, 0, os.SEEK_SET)
    os.ftruncate(state_descriptor, 0)
    os.write(state_descriptor, rendered_state)
    os.fsync(state_descriptor)
    fcntl.flock(state_descriptor, fcntl.LOCK_UN)
    os.close(state_descriptor)
    return {
        "status": "verified",
        "environment": "production",
        "phase": expected_phase,
        "target_owner": observed_owner,
        "provider_mutations": 0,
        "secrets_disclosed": False,
    }


def _static_credential_gate(manifest: Path, staging_env: Path) -> tuple[Path, dict[str, str]]:
    source, _ = _immutable_source(manifest)
    if not staging_env.is_file():
        raise ProductionCutoverError("BLOCKED_STAGING_COLLISION_EVIDENCE")
    source_values = parse_env_file(source)
    credentials, _ = credential_status(
        queue_target_values(source_values), parse_env_file(staging_env)
    )
    if credentials["blockers"]:
        raise ProductionCutoverError(str(credentials["status"]))
    if source_profile(source_values) != "legacy":
        raise ProductionCutoverError("BLOCKED_SOURCE_NOT_LEGACY")
    validate_official_release_profile(parse_env_file(manifest))
    return source, source_values


def apply_cutover(
    *,
    manifest: Path,
    staging_env: Path,
    preflight_report: Path,
    preflight_digest: str,
    backup_receipt: Path,
    backup_digest: str,
    secure_backup_dir: Path,
    artifact_dir: Path,
    confirmation: str,
    drain_timeout_seconds: int = 300,
    drain_poll_seconds: float = 2.0,
    operations_factory: Callable[[Path], ProductionOperations] = ProductionOperations,
    preflight_runner: Callable[..., dict[str, Any]] = run_preflight,
) -> dict[str, Any]:
    if confirmation != APPLY_CONFIRMATION:
        raise ProductionCutoverError("APPLY_CONFIRMATION_MISMATCH")
    # Credential/source gates deliberately precede evidence, hosts, provider,
    # and every mutation.  Current production therefore remains safely
    # BLOCKED_CREDENTIALS.
    source, source_values = _static_credential_gate(manifest, staging_env)
    source_digest = _sha256(source)
    binding = git_binding()
    if binding["branch"] != "main" or binding["worktree"] != "clean" or binding["head"] != binding["origin_main"]:
        raise ProductionCutoverError("BLOCKED_CLEAN_PUSHED_MAIN")
    evidence = verify_preflight_evidence(
        preflight_report,
        preflight_digest,
        backup_digest=backup_digest,
        source_digest=source_digest,
        binding=binding,
    )
    try:
        live_preflight = preflight_runner(
            manifest,
            staging_env,
            backup_receipt,
            backup_digest,
            target_queue_cutover=True,
        )
    except ReadinessBlocked as exc:
        raise ProductionCutoverError(exc.code) from None
    if live_preflight.get("status") != "READY_FOR_SEPARATE_CUTOVER_CHOREOGRAPHY":
        raise ProductionCutoverError("BLOCKED_LIVE_PREFLIGHT")
    if live_preflight.get("source_sha256") != source_digest or _sha256(source) != source_digest:
        raise ProductionCutoverError("BLOCKED_SOURCE_DRIFT")
    if (
        operations_factory is ProductionOperations
        and artifact_dir.resolve(strict=False) != DEFAULT_ARTIFACT_DIR
    ):
        raise ProductionCutoverError("BLOCKED_PRODUCTION_ARTIFACT_DIRECTORY")

    ops = operations_factory(manifest)
    run_lock = ExclusiveRunLock(artifact_dir)
    run_lock.acquire()
    source_lock = ImmutableSourceLock(source)
    try:
        source_lock.acquire()
        journal = PhaseJournal(
            artifact_dir,
            command="apply",
            source_sha256=source_digest,
            git_head=binding["head"],
            run_lock=run_lock,
        )
    except BaseException:
        source_lock.release()
        run_lock.release()
        raise
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "environment": "production",
        "command": "apply",
        "started_at": _utc_now(),
        "status": "running",
        "git": binding,
        "preflight": evidence,
        "steps": [],
        "executor_timeline": [],
        "synthetic_customer_mutations": 0,
        "secrets_disclosed": False,
    }
    stopped: list[tuple[str, str]] = []
    source_backup: SecureSourceBackup | None = None
    source_after_digest: str | None = None
    try:
        initial = ops.executor_inventory()
        _assert_inventory(initial, count=1, owner="legacy")
        receipt["executor_timeline"].append(initial)
        _require_source_digest(source, source_digest)
        stopped = ops.stop_producers()
        journal.update("producers_quiesced", stopped_count=len(stopped))
        receipt["steps"].append({"name": "quiesce_both_producer_hosts", "stopped_count": len(stopped)})
        drained = ops.wait_for_drain(drain_timeout_seconds, drain_poll_seconds)
        journal.update("drained")
        receipt["steps"].append({"name": "drain", "report": drained})
        ops.stop_bot()
        zero = ops.executor_inventory()
        _assert_inventory(zero, count=0, owner=None)
        journal.update("zero_executor")
        receipt["executor_timeline"].append(zero)
        source_backup, source_report = backup_and_update_source(
            source,
            secure_backup_dir,
            _queue_source_updates(source_values),
            expected_source_sha256=source_digest,
            source_lock_held=True,
        )
        receipt["steps"].append({"name": "atomic_source_switch", "report": source_report})
        source_after_digest = str(source_report["source_after_sha256"])
        journal.update(
            "source_switched",
            source_after_sha256=source_report["source_after_sha256"],
            source_backup_sha256=source_backup.sha256,
        )
        authority_path, authority_digest = create_deploy_authority(
            artifact_dir,
            source,
            binding,
            run_lock=run_lock,
            journal=journal,
        )
        _require_source_digest(source, str(source_after_digest))
        receipt["steps"].append(
            {
                "name": "official_two_host_deploy",
                "report": ops.deploy_official(authority_path, authority_digest),
            }
        )
        journal.update("deployed")
        final_inventory = ops.executor_inventory()
        _assert_inventory(final_inventory, count=1, owner="queue-v1")
        receipt["executor_timeline"].append(final_inventory)
        queue_values = parse_env_file(source)
        receipt["steps"].append({"name": "runtime_role_contract", "report": ops.runtime_contract(queue_values, expected_owner="queue-v1")})
        receipt["steps"].append({"name": "queue_health", "report": ops.queue_health(str(queue_values.get("POSTGRES_DB") or ""))})
        receipt["steps"].append({"name": "b2b_lane_read_only_probe", "report": ops.b2b_lane_probe()})
        receipt["status"] = "applied"
        receipt["finished_at"] = _utc_now()
        receipt_path, digest = _write_redacted_receipt(
            artifact_dir, "production-queue-cutover", receipt
        )
        journal.update("applied", receipt_sha256=digest)
        source_lock.release()
        run_lock.release()
        return {
            "status": "applied",
            "receipt_file": receipt_path.name,
            "receipt_sha256": digest,
            "source_backup_file": source_backup.path.name,
            "source_backup_sha256": source_backup.sha256,
            "secrets_disclosed": False,
        }
    except BaseException as exc:
        code = exc.code if isinstance(exc, ProductionCutoverError) else "UNEXPECTED_CUTOVER_FAILURE"
        recovery: dict[str, Any] = {"attempted": True, "status": "failed"}
        try:
            if source_backup is not None:
                # A post-deploy failure may have restarted Queue producers and
                # the Queue executor.  Re-enter the same guarded choreography;
                # never restore Legacy underneath a live Queue executor.
                recovery_stopped = ops.stop_producers()
                recovery["quiesce"] = {"stopped_count": len(recovery_stopped)}
                recovery["drain"] = ops.wait_for_drain(
                    drain_timeout_seconds, drain_poll_seconds
                )
                ops.stop_bot()
                recovered_zero = ops.executor_inventory()
                _assert_inventory(recovered_zero, count=0, owner=None)
                recovery["source"] = restore_source_from_backup(
                    source,
                    source_backup,
                    expected_current_sha256=source_after_digest,
                )
                journal.update(
                    "recovery_source_switched",
                    source_after_sha256=_sha256(source),
                )
                legacy_authority_path, legacy_authority_digest = (
                    create_deploy_authority(
                        artifact_dir,
                        source,
                        binding,
                        run_lock=run_lock,
                        journal=journal,
                    )
                )
                recovery["deploy"] = ops.deploy_official(
                    legacy_authority_path, legacy_authority_digest
                )
                recovered_inventory = ops.executor_inventory()
                _assert_inventory(recovered_inventory, count=1, owner="legacy")
                recovery["runtime"] = ops.runtime_contract(
                    parse_env_file(source), expected_owner="legacy"
                )
            else:
                observed_executor = ops.executor_inventory()
                if observed_executor.get("count") == 0:
                    ops.start_bot()
                else:
                    _assert_inventory(
                        observed_executor, count=1, owner="legacy"
                    )
                ops.resume_producers(stopped)
                recovered_inventory = ops.executor_inventory()
                _assert_inventory(recovered_inventory, count=1, owner="legacy")
            recovery["status"] = "restored_legacy"
        except BaseException:
            recovery["status"] = "recovery_failed"
        receipt["status"] = "failed"
        receipt["error_code"] = code
        receipt["safe_recovery"] = recovery
        receipt["finished_at"] = _utc_now()
        _failed_path, receipt_digest = _write_redacted_receipt(
            artifact_dir, "production-queue-cutover-failed", receipt
        )
        journal.update(
            "failed_recovered" if recovery["status"] == "restored_legacy" else "recovery_failed",
            receipt_sha256=receipt_digest,
            error_code=code,
        )
        source_lock.release()
        run_lock.release()
        raise ProductionCutoverError(code, receipt_sha256=receipt_digest) from None


def rollback_to_legacy(
    *,
    manifest: Path,
    source_backup_path: Path,
    source_backup_digest: str,
    apply_receipt_path: Path,
    apply_receipt_digest: str,
    artifact_dir: Path,
    confirmation: str,
    operations_factory: Callable[[Path], ProductionOperations] = ProductionOperations,
) -> dict[str, Any]:
    if confirmation != ROLLBACK_CONFIRMATION:
        raise ProductionCutoverError("ROLLBACK_CONFIRMATION_MISMATCH")
    source, _ = _immutable_source(manifest)
    binding = git_binding()
    if binding["branch"] != "main" or binding["worktree"] != "clean" or binding["head"] != binding["origin_main"]:
        raise ProductionCutoverError("BLOCKED_CLEAN_PUSHED_MAIN")
    current_source_digest = _sha256(source)
    if source_profile(parse_env_file(source)) != "queue-v1":
        raise ProductionCutoverError("BLOCKED_SOURCE_NOT_QUEUE_V1")
    backup_parent = source_backup_path.parent.resolve(strict=False)
    if (
        source_backup_path.is_symlink()
        or
        not source_backup_path.is_file()
        or _sha256(source_backup_path) != source_backup_digest
        or REPO_ROOT in source_backup_path.resolve(strict=False).parents
        or source_backup_path.stat().st_uid != os.geteuid()
        or stat.S_IMODE(source_backup_path.stat().st_mode) & 0o077
        or source_backup_path.stat().st_nlink != 1
        or not backup_parent.is_dir()
        or backup_parent.stat().st_uid != os.geteuid()
        or stat.S_IMODE(backup_parent.stat().st_mode) & 0o077
    ):
        raise ProductionCutoverError("BLOCKED_SOURCE_BACKUP_DIGEST")
    original = source_backup_path.read_bytes()
    if source_profile(parse_env_file(source_backup_path)) != "legacy":
        raise ProductionCutoverError("BLOCKED_SOURCE_BACKUP_NOT_LEGACY")
    apply_evidence = verify_apply_receipt(
        apply_receipt_path,
        apply_receipt_digest,
        source=source,
        source_backup_path=source_backup_path,
        source_backup_digest=source_backup_digest,
        binding=binding,
    )
    backup = SecureSourceBackup(source_backup_path, source_backup_digest, original)
    if (
        operations_factory is ProductionOperations
        and artifact_dir.resolve(strict=False) != DEFAULT_ARTIFACT_DIR
    ):
        raise ProductionCutoverError("BLOCKED_PRODUCTION_ARTIFACT_DIRECTORY")
    ops = operations_factory(manifest)
    run_lock = ExclusiveRunLock(artifact_dir)
    run_lock.acquire()
    source_lock = ImmutableSourceLock(source)
    try:
        source_lock.acquire()
        journal = PhaseJournal(
            artifact_dir,
            command="rollback",
            source_sha256=current_source_digest,
            git_head=binding["head"],
            run_lock=run_lock,
        )
    except BaseException:
        source_lock.release()
        run_lock.release()
        raise
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "environment": "production",
        "command": "rollback",
        "started_at": _utc_now(),
        "git": binding,
        "apply_receipt": apply_evidence,
        "schema_downgrade": False,
        "steps": [],
        "executor_timeline": [],
        "secrets_disclosed": False,
    }
    current_source = source.read_bytes()
    stopped: list[tuple[str, str]] = []
    try:
        initial = ops.executor_inventory()
        _assert_inventory(initial, count=1, owner="queue-v1")
        receipt["executor_timeline"].append(initial)
        _require_source_digest(source, current_source_digest)
        stopped = ops.stop_producers()
        journal.update("producers_quiesced", stopped_count=len(stopped))
        receipt["steps"].append({"name": "quiesce_both_producer_hosts", "stopped_count": len(stopped)})
        receipt["steps"].append({"name": "drain", "report": ops.wait_for_drain(300, 2.0)})
        journal.update("drained")
        ops.stop_bot()
        zero = ops.executor_inventory()
        _assert_inventory(zero, count=0, owner=None)
        journal.update("zero_executor")
        receipt["executor_timeline"].append(zero)
        receipt["steps"].append(
            {
                "name": "atomic_source_restore",
                "report": restore_source_from_backup(
                    source,
                    backup,
                    expected_current_sha256=current_source_digest,
                ),
            }
        )
        journal.update("source_switched", source_after_sha256=source_backup_digest)
        legacy_authority_path, legacy_authority_digest = create_deploy_authority(
            artifact_dir,
            source,
            binding,
            run_lock=run_lock,
            journal=journal,
        )
        receipt["steps"].append(
            {
                "name": "official_forward_rollback_deploy",
                "report": ops.deploy_official(
                    legacy_authority_path, legacy_authority_digest
                ),
            }
        )
        journal.update("deployed")
        final = ops.executor_inventory()
        _assert_inventory(final, count=1, owner="legacy")
        receipt["executor_timeline"].append(final)
        receipt["steps"].append({"name": "runtime_role_contract", "report": ops.runtime_contract(parse_env_file(source), expected_owner="legacy")})
        receipt["status"] = "rolled_back"
        receipt["finished_at"] = _utc_now()
        receipt_path, digest = _write_redacted_receipt(
            artifact_dir, "production-queue-rollback", receipt
        )
        journal.update("rolled_back", receipt_sha256=digest)
        source_lock.release()
        run_lock.release()
        return {
            "status": "rolled_back",
            "receipt_file": receipt_path.name,
            "receipt_sha256": digest,
            "schema_downgrade": False,
        }
    except BaseException as exc:
        code = exc.code if isinstance(exc, ProductionCutoverError) else "UNEXPECTED_ROLLBACK_FAILURE"
        try:
            recovery_stopped = ops.stop_producers()
            ops.wait_for_drain(300, 2.0)
            ops.stop_bot()
            recovery_zero = ops.executor_inventory()
            _assert_inventory(recovery_zero, count=0, owner=None)
            observed_source_digest = _sha256(source)
            if observed_source_digest not in {
                current_source_digest,
                source_backup_digest,
            }:
                raise ProductionCutoverError("BLOCKED_SOURCE_DRIFT")
            if observed_source_digest != current_source_digest:
                _atomic_write(source, current_source)
            journal.update(
                "recovery_source_switched",
                source_after_sha256=_sha256(source),
            )
            authority_path, authority_digest = create_deploy_authority(
                artifact_dir,
                source,
                binding,
                run_lock=run_lock,
                journal=journal,
            )
            ops.deploy_official(authority_path, authority_digest)
            recovered = ops.executor_inventory()
            _assert_inventory(recovered, count=1, owner="queue-v1")
            ops.runtime_contract(parse_env_file(source), expected_owner="queue-v1")
            recovery = "queue_restored"
        except BaseException:
            recovery = "recovery_failed"
        receipt["status"] = "failed"
        receipt["error_code"] = code
        receipt["safe_recovery"] = recovery
        receipt["finished_at"] = _utc_now()
        _failed_path, digest = _write_redacted_receipt(
            artifact_dir, "production-queue-rollback-failed", receipt
        )
        journal.update(
            "failed_recovered" if recovery == "queue_restored" else "recovery_failed",
            receipt_sha256=digest,
            error_code=code,
        )
        source_lock.release()
        run_lock.release()
        raise ProductionCutoverError(code, receipt_sha256=digest) from None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("plan", "status", "apply", "rollback", "verify-deploy-authority"),
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--staging-env", type=Path, default=DEFAULT_STAGING_ENV)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--preflight-report", type=Path)
    parser.add_argument("--preflight-report-sha256", default="")
    parser.add_argument("--backup-receipt", type=Path)
    parser.add_argument("--backup-receipt-sha256", default="")
    parser.add_argument("--secure-backup-dir", type=Path)
    parser.add_argument("--source-backup", type=Path)
    parser.add_argument("--source-backup-sha256", default="")
    parser.add_argument("--apply-receipt", type=Path)
    parser.add_argument("--apply-receipt-sha256", default="")
    parser.add_argument("--deploy-authority", type=Path)
    parser.add_argument("--deploy-authority-sha256", default="")
    parser.add_argument("--confirm", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "plan":
            payload = {
                "environment": "production",
                "mode": "guarded",
                "apply_confirmation": APPLY_CONFIRMATION,
                "rollback_confirmation": ROLLBACK_CONFIRMATION,
                "synthetic_customer_mutations": 0,
                "sequence": ["preflight", "quiesce", "drain", "legacy->zero->queue", "official deploy", "read-only health/probe"],
            }
        elif args.command == "status":
            source, source_values = _static_credential_gate(args.manifest, args.staging_env)
            del source
            payload = {
                "environment": "production",
                "mode": "read-only",
                "credential_gate": "ready",
                "source_profile": source_profile(source_values),
                "provider_mutations": 0,
            }
        elif args.command == "apply":
            if args.confirm != APPLY_CONFIRMATION:
                raise ProductionCutoverError("APPLY_CONFIRMATION_MISMATCH")
            # Make missing or incompletely bound production identities the first
            # observable blocker, before accepting evidence or constructing
            # an operations object capable of mutation.
            _static_credential_gate(args.manifest, args.staging_env)
            if not all((args.preflight_report, args.backup_receipt, args.secure_backup_dir)):
                raise ProductionCutoverError("APPLY_EVIDENCE_REQUIRED")
            with fail_safe_signal_guard():
                payload = apply_cutover(
                    manifest=args.manifest,
                    staging_env=args.staging_env,
                    preflight_report=args.preflight_report,
                    preflight_digest=args.preflight_report_sha256,
                    backup_receipt=args.backup_receipt,
                    backup_digest=args.backup_receipt_sha256,
                    secure_backup_dir=args.secure_backup_dir,
                    artifact_dir=args.artifact_dir,
                    confirmation=args.confirm,
                )
        elif args.command == "rollback":
            if args.confirm != ROLLBACK_CONFIRMATION:
                raise ProductionCutoverError("ROLLBACK_CONFIRMATION_MISMATCH")
            if args.source_backup is None or args.apply_receipt is None:
                raise ProductionCutoverError("ROLLBACK_BOUND_EVIDENCE_REQUIRED")
            with fail_safe_signal_guard():
                payload = rollback_to_legacy(
                    manifest=args.manifest,
                    source_backup_path=args.source_backup,
                    source_backup_digest=args.source_backup_sha256,
                    apply_receipt_path=args.apply_receipt,
                    apply_receipt_digest=args.apply_receipt_sha256,
                    artifact_dir=args.artifact_dir,
                    confirmation=args.confirm,
                )
        else:
            if args.deploy_authority is None:
                raise ProductionCutoverError("DEPLOY_AUTHORITY_REQUIRED")
            payload = verify_deploy_authority(
                args.manifest,
                args.deploy_authority,
                args.deploy_authority_sha256,
            )
    except (ProductionCutoverError, ReadinessBlocked) as exc:
        code = exc.code if hasattr(exc, "code") else str(exc)
        output = {
            "environment": "production",
            "status": code,
            "secrets_disclosed": False,
            "provider_mutations": 0,
        }
        if isinstance(exc, ProductionCutoverError) and exc.receipt_sha256:
            output["receipt_sha256"] = exc.receipt_sha256
        print(json.dumps(output, sort_keys=True), file=sys.stderr)
        return 4
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
