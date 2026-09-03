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
import re
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
    REQUIRED_QUEUE_TABLES,
    TOKEN_KEYS,
    ReadinessBlocked,
    _backup_status,
    _identities,
    _immutable_source,
    _profile,
    credential_status,
    git_binding,
    provider_preflight,
    queue_target_values,
    run_preflight,
    source_profile,
)
from scripts.run_production_backup import database_identity_sha256
from scripts.scan_telegram_queue_artifacts import scan_paths
from scripts import quiesce_production_legacy_market_collectors as market_handoff


APPLY_CONFIRMATION = "CUTOVER PRODUCTION TELEGRAM DELIVERY TO QUEUE-V1"
REDEPLOY_CONFIRMATION = "REDEPLOY PRODUCTION TELEGRAM QUEUE-V1 OFFICIAL RELEASE"
RECONCILE_REDEPLOY_CONFIRMATION = (
    "RECONCILE CONTAINED PRODUCTION QUEUE-V1 REDEPLOY FAILURE"
)
ROLLBACK_CONFIRMATION = "ROLLBACK PRODUCTION TELEGRAM DELIVERY TO LEGACY"
DEFAULT_ARTIFACT_DIR = Path("/root/secure-envs/trading-bot/queue-cutover-artifacts")
DEFAULT_MARKET_HANDOFF_DIR = Path(
    "/root/secure-envs/trading-bot/market-pipeline-cutover"
)
FENCED_DEPLOY_SUPERVISOR = REPO_ROOT / "scripts/run_fenced_production_deploy.py"
CONTROL_PAYLOAD_MANIFEST = REPO_ROOT / "control-payload.sha256"
PREFLIGHT_MAXIMUM_AGE_SECONDS = 900
ROLLBACK_RECEIPT_MAXIMUM_AGE_SECONDS = 86400
FOREIGN_PROJECT = "trading_bot"
IRAN_PROJECT = "current"
FOREIGN_COLOCATED_STAGING_PROJECT = "trading_bot_staging"
FOREIGN_CONTAINERS = {
    "app": "trading_bot_app",
    "sync": "trading_bot_sync_worker",
    "migration": "trading_bot_migration",
    "bot": "trading_bot_bot",
    "db": "trading_bot_db",
}

PHASE_TERMINAL_STATES = frozenset(
    {
        "applied",
        "redeployed",
        "rolled_back",
        "failed_recovered",
        "interrupted_recovered",
    }
)
PHASE_RECEIPT_STATES = PHASE_TERMINAL_STATES | {"recovery_failed"}


def _process_start_identity(pid: int) -> tuple[str, str] | None:
    """Return boot-id and Linux start ticks for one live process."""

    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
        value = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        fields = value[value.rindex(") ") + 2 :].split()
        start_ticks = fields[19]
    except (OSError, IndexError, ValueError):
        return None
    if not boot_id or not start_ticks.isdigit():
        return None
    return boot_id, start_ticks
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


@dataclass(frozen=True, slots=True)
class PrivatePrimaryDeployAttestation:
    """Exact, value-free binding for a PRIVATE_PRIMARY deploy manifest.

    The receipt path itself is needed by the official release script, while
    Queue authority receipts retain only its SHA-256 path binding.  This keeps
    the one-time Queue authority tied to the exact three command-line
    arguments without copying an operator path into durable evidence.
    """

    manifest_sha256: str
    receipt_path: Path
    receipt_sha256: str


def bind_private_primary_deploy_attestation(
    manifest: Path,
    *,
    manifest_sha256: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> PrivatePrimaryDeployAttestation:
    """Validate and bind the exact PRIVATE_PRIMARY deploy evidence.

    This function is intentionally usable by a higher-level Product
    transaction before it creates Queue deploy authority.  No payload values
    are read into an environment or returned.
    """

    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256 or ""):
        raise ProductionCutoverError("BLOCKED_PRIVATE_PRIMARY_ATTESTATION")
    if not re.fullmatch(r"[0-9a-f]{64}", receipt_sha256 or ""):
        raise ProductionCutoverError("BLOCKED_PRIVATE_PRIMARY_ATTESTATION")
    try:
        manifest_metadata = manifest.lstat()
        receipt_metadata = receipt_path.lstat()
    except OSError:
        raise ProductionCutoverError("BLOCKED_PRIVATE_PRIMARY_ATTESTATION") from None
    if (
        not manifest.is_absolute()
        or manifest.is_symlink()
        or not stat.S_ISREG(manifest_metadata.st_mode)
        or manifest_metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(manifest_metadata.st_mode) & 0o022
        or manifest_metadata.st_nlink != 1
        or _sha256(manifest) != manifest_sha256
        or not receipt_path.is_absolute()
        or receipt_path.is_symlink()
        or not stat.S_ISREG(receipt_metadata.st_mode)
        or receipt_metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(receipt_metadata.st_mode) != 0o600
        or receipt_metadata.st_nlink != 1
        or _sha256(receipt_path) != receipt_sha256
    ):
        raise ProductionCutoverError("BLOCKED_PRIVATE_PRIMARY_ATTESTATION")
    return PrivatePrimaryDeployAttestation(
        manifest_sha256=manifest_sha256,
        receipt_path=receipt_path,
        receipt_sha256=receipt_sha256,
    )


def _private_primary_attestation_binding(
    manifest: Path,
    attestation: PrivatePrimaryDeployAttestation | None,
) -> dict[str, str] | None:
    if attestation is None:
        return None
    verified = bind_private_primary_deploy_attestation(
        manifest,
        manifest_sha256=attestation.manifest_sha256,
        receipt_path=attestation.receipt_path,
        receipt_sha256=attestation.receipt_sha256,
    )
    return {
        "manifest_sha256": verified.manifest_sha256,
        "receipt_path_sha256": hashlib.sha256(
            str(verified.receipt_path).encode("utf-8")
        ).hexdigest(),
        "receipt_sha256": verified.receipt_sha256,
    }


class ExclusiveRunLock:
    def __init__(self, artifact_dir: Path) -> None:
        self.directory = _ensure_secure_artifact_dir(artifact_dir)
        self.path = self.directory / "production-release.lock"
        self.held = False
        self.descriptor: int | None = None
        self.nonce = secrets.token_hex(32)
        self.device: int | None = None
        self.inode: int | None = None
        self.adopted_market_maintenance: dict[str, Any] | None = None
        self.binding_nonce_sha256: str | None = None

    def _assert_phase_journals_terminal(
        self,
        *,
        allow_recovery_journal: Path | None = None,
        allow_interrupted_journal: Path | None = None,
    ) -> None:
        allowed = (
            allow_recovery_journal.resolve(strict=False)
            if allow_recovery_journal is not None
            else None
        )
        interrupted = (
            allow_interrupted_journal.resolve(strict=False)
            if allow_interrupted_journal is not None
            else None
        )
        for journal in self.directory.glob("production-queue-phase-*.json"):
            try:
                state = json.loads(journal.read_text(encoding="utf-8")).get("status")
            except (OSError, ValueError):
                raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL") from None
            if (
                allowed is not None
                and journal.resolve(strict=False) == allowed
                and state == "recovery_failed"
            ):
                continue
            if (
                interrupted is not None
                and journal.resolve(strict=False) == interrupted
                and state not in PHASE_TERMINAL_STATES
            ):
                continue
            if state not in PHASE_TERMINAL_STATES:
                raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")

    def _remove_proven_stale_owner(self) -> bool:
        """Remove only a lock whose exact PID/start identity no longer exists."""

        if not self.path.exists():
            return False
        if self.path.is_symlink():
            raise ProductionCutoverError("BLOCKED_CONCURRENT_OR_INTERRUPTED_CUTOVER")
        descriptor = os.open(
            self.path,
            os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            metadata = os.fstat(descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProductionCutoverError(
                    "BLOCKED_CONCURRENT_OR_INTERRUPTED_CUTOVER"
                ) from None
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                payload = json.loads(os.read(descriptor, 8192).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ProductionCutoverError(
                    "BLOCKED_STALE_LOCK_IDENTITY_UNPROVEN"
                ) from None
            pid = payload.get("owner_pid")
            boot_id = str(payload.get("owner_boot_id") or "")
            start_ticks = str(payload.get("owner_start_ticks") or "")
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid < 1
                or not boot_id
                or not start_ticks.isdigit()
                or payload.get("device") != metadata.st_dev
                or payload.get("inode") != metadata.st_ino
            ):
                raise ProductionCutoverError("BLOCKED_STALE_LOCK_IDENTITY_UNPROVEN")
            if _process_start_identity(pid) == (boot_id, start_ticks):
                raise ProductionCutoverError(
                    "BLOCKED_CONCURRENT_OR_INTERRUPTED_CUTOVER"
                )
            path_metadata = self.path.lstat()
            if (
                path_metadata.st_dev != metadata.st_dev
                or path_metadata.st_ino != metadata.st_ino
            ):
                raise ProductionCutoverError("BLOCKED_STALE_LOCK_IDENTITY_UNPROVEN")
            self.path.unlink()
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return True
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def acquire(
        self,
        *,
        allow_recovery_journal: Path | None = None,
        allow_interrupted_journal: Path | None = None,
    ) -> None:
        self._remove_proven_stale_owner()
        _recover_terminal_receipt_journals(self.directory)
        self._assert_phase_journals_terminal(
            allow_recovery_journal=allow_recovery_journal,
            allow_interrupted_journal=allow_interrupted_journal,
        )
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
            identity = _process_start_identity(os.getpid())
            if identity is None:
                raise ProductionCutoverError("BLOCKED_RUN_LOCK_PROCESS_IDENTITY")
            payload.update(
                {
                    "owner_pid": os.getpid(),
                    "owner_boot_id": identity[0],
                    "owner_start_ticks": identity[1],
                }
            )
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

    def adopt_market_pipeline_maintenance(
        self,
        *,
        journal: Path,
        expected_journal_sha256: str,
        expected_primary_verification_sha256: str,
        release_sha: str,
        allow_recovery_journal: Path | None = None,
        allow_interrupted_journal: Path | None = None,
    ) -> None:
        """Atomically adopt the persistent PRIVATE_PRIMARY maintenance lock.

        The blue/green transition intentionally leaves the ordinary production
        operation-lock inode in place between commands.  Final Product
        promotion adopts that same inode, so there is no unlock/relock race in
        which another official deploy could start.
        """

        self._assert_phase_journals_terminal(
            allow_recovery_journal=allow_recovery_journal,
            allow_interrupted_journal=allow_interrupted_journal,
        )
        try:
            journal_info = journal.lstat()
            journal_payload = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProductionCutoverError("BLOCKED_MARKET_MAINTENANCE_JOURNAL") from exc
        if (
            not re.fullmatch(r"[0-9a-f]{64}", expected_journal_sha256)
            or _sha256(journal) != expected_journal_sha256
            or journal.is_symlink()
            or not stat.S_ISREG(journal_info.st_mode)
            or journal_info.st_uid != os.geteuid()
            or stat.S_IMODE(journal_info.st_mode) != 0o600
            or journal_info.st_nlink != 1
            or journal_payload.get("schema")
            != "production_legacy_market_collector_handoff/1.1"
            or journal_payload.get("host_role") != "bot"
            or journal_payload.get("status") != "PRIMARY_COMMITTED"
            or journal_payload.get("release_sha") != release_sha
            or journal_payload.get("secrets_disclosed") is not False
        ):
            raise ProductionCutoverError("BLOCKED_MARKET_MAINTENANCE_JOURNAL")
        maintenance = journal_payload.get("maintenance_lock")
        if not isinstance(maintenance, dict):
            raise ProductionCutoverError("BLOCKED_MARKET_MAINTENANCE_JOURNAL")
        try:
            descriptor = os.open(
                self.path,
                os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except OSError as exc:
            raise ProductionCutoverError("BLOCKED_MARKET_MAINTENANCE_LOCK") from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            metadata = os.fstat(descriptor)
            lock_payload = json.loads(os.read(descriptor, 8192).decode("utf-8"))
            if (
                self.path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or lock_payload != maintenance
                or maintenance.get("schema") != "market_pipeline_maintenance_lock/1.0"
                or maintenance.get("environment") != "production"
                or maintenance.get("release_sha") != release_sha
                or not re.fullmatch(
                    r"[0-9a-f]{64}", str(maintenance.get("nonce_sha256") or "")
                )
                or maintenance.get("journal_path_sha256")
                != hashlib.sha256(str(journal).encode("utf-8")).hexdigest()
                or maintenance.get("device") != metadata.st_dev
                or maintenance.get("inode") != metadata.st_ino
            ):
                raise ProductionCutoverError("BLOCKED_MARKET_MAINTENANCE_LOCK")
            try:
                market_handoff.validate_committed_handoff(
                    journal=journal,
                    expected_journal_sha256=expected_journal_sha256,
                    release_sha=release_sha,
                    expected_primary_verification_sha256=(
                        expected_primary_verification_sha256
                    ),
                    host_role="bot",
                    expected_maintenance_lock=maintenance,
                )
            except market_handoff.CollectorHandoffError as exc:
                raise ProductionCutoverError(
                    "BLOCKED_MARKET_MAINTENANCE_JOURNAL"
                ) from exc
        except BaseException:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise
        self.adopted_market_maintenance = dict(maintenance)
        self.binding_nonce_sha256 = str(maintenance["nonce_sha256"])
        self.descriptor = descriptor
        self.device = metadata.st_dev
        self.inode = metadata.st_ino
        self.held = True

    def adopt_transferred_market_pipeline_maintenance(
        self,
        *,
        handoff_dir: Path = DEFAULT_MARKET_HANDOFF_DIR,
        allow_recovery_journal: Path | None = None,
        allow_interrupted_journal: Path | None = None,
    ) -> None:
        """Adopt a durable capture-transfer lock for a Queue-v1 redeploy.

        The Market capture handoff and Queue-v1 release intentionally share
        the production operation-lock path.  A routine Queue-v1 code release
        must hold that exact inode for its whole official two-host deploy and
        then return it unchanged; deleting or replacing it would remove the
        fail-closed guard that keeps legacy Market collectors disabled.
        """

        self._assert_phase_journals_terminal(
            allow_recovery_journal=allow_recovery_journal,
            allow_interrupted_journal=allow_interrupted_journal,
        )
        try:
            descriptor = os.open(
                self.path,
                os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except OSError as exc:
            raise ProductionCutoverError("BLOCKED_MARKET_MAINTENANCE_LOCK") from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            metadata = os.fstat(descriptor)
            path_metadata = self.path.lstat()
            os.lseek(descriptor, 0, os.SEEK_SET)
            maintenance = json.loads(os.read(descriptor, 8192).decode("utf-8"))
            release_sha = str(maintenance.get("release_sha") or "")
            journal = handoff_dir / f"bot-legacy-handoff-{release_sha[:8]}.json"
            if (
                self.path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or path_metadata.st_dev != metadata.st_dev
                or path_metadata.st_ino != metadata.st_ino
                or maintenance.get("schema")
                != "market_pipeline_maintenance_lock/1.0"
                or maintenance.get("environment") != "production"
                or maintenance.get("host_role") != "bot"
                or not re.fullmatch(r"[0-9a-f]{40}", release_sha)
                or not re.fullmatch(
                    r"[0-9a-f]{64}", str(maintenance.get("nonce_sha256") or "")
                )
                or maintenance.get("journal_path_sha256")
                != hashlib.sha256(str(journal).encode("utf-8")).hexdigest()
                or maintenance.get("device") != metadata.st_dev
                or maintenance.get("inode") != metadata.st_ino
            ):
                raise ProductionCutoverError("BLOCKED_MARKET_MAINTENANCE_LOCK")
            try:
                journal_digest = _sha256(journal)
                market_handoff.validate_transferred_handoff(
                    journal=journal,
                    expected_journal_sha256=journal_digest,
                    release_sha=release_sha,
                    host_role="bot",
                    expected_maintenance_lock=maintenance,
                )
            except (OSError, market_handoff.CollectorHandoffError) as exc:
                raise ProductionCutoverError(
                    "BLOCKED_MARKET_MAINTENANCE_JOURNAL"
                ) from exc
        except BaseException:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise
        self.adopted_market_maintenance = dict(maintenance)
        self.binding_nonce_sha256 = str(maintenance["nonce_sha256"])
        self.descriptor = descriptor
        self.device = metadata.st_dev
        self.inode = metadata.st_ino
        self.held = True

    def acquire_for_queue_redeploy(
        self,
        *,
        handoff_dir: Path = DEFAULT_MARKET_HANDOFF_DIR,
        allow_recovery_journal: Path | None = None,
        allow_interrupted_journal: Path | None = None,
    ) -> None:
        if self.path.is_symlink():
            raise ProductionCutoverError("BLOCKED_CONCURRENT_OR_INTERRUPTED_CUTOVER")
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if (
                isinstance(payload, dict)
                and payload.get("schema")
                == "market_pipeline_maintenance_lock/1.0"
            ):
                self.adopt_transferred_market_pipeline_maintenance(
                    handoff_dir=handoff_dir,
                    allow_recovery_journal=allow_recovery_journal,
                    allow_interrupted_journal=allow_interrupted_journal,
                )
                return
        self.acquire(
            allow_recovery_journal=allow_recovery_journal,
            allow_interrupted_journal=allow_interrupted_journal,
        )

    def restore_adopted_market_pipeline_maintenance(self) -> None:
        """Return an unsuccessful promotion to the durable maintenance state.

        PRIVATE_PRIMARY capture ownership is already committed before Product
        promotion starts.  A failed or incomplete Product transaction must
        therefore leave the same operation-lock inode adoptable for a retry;
        deleting it would create an unguarded, non-recoverable state.
        """

        original = self.adopted_market_maintenance
        if (
            not self.held
            or self.descriptor is None
            or original is None
        ):
            raise ProductionCutoverError("BLOCKED_MARKET_MAINTENANCE_RESTORE")
        try:
            metadata = os.fstat(self.descriptor)
            path_metadata = self.path.lstat()
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            current = json.loads(os.read(self.descriptor, 8192).decode("utf-8"))
            expected = self.binding()
            if (
                self.path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or not stat.S_ISREG(path_metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_dev != expected["device"]
                or metadata.st_ino != expected["inode"]
                or path_metadata.st_dev != metadata.st_dev
                or path_metadata.st_ino != metadata.st_ino
                or current != original
                or current.get("nonce_sha256") != expected["nonce_sha256"]
            ):
                raise ProductionCutoverError(
                    "BLOCKED_MARKET_MAINTENANCE_RESTORE"
                )
        finally:
            if self.descriptor is not None:
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
                os.close(self.descriptor)
                self.descriptor = None
            self.held = False
            self.adopted_market_maintenance = None
            self.binding_nonce_sha256 = None

    def binding(self) -> dict[str, Any]:
        if not self.held or self.descriptor is None:
            raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY")
        metadata = os.fstat(self.descriptor)
        if metadata.st_dev != self.device or metadata.st_ino != self.inode:
            raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY")
        return {
            "nonce_sha256": self.binding_nonce_sha256
            or hashlib.sha256(self.nonce.encode("utf-8")).hexdigest(),
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
                self.adopted_market_maintenance = None
                self.binding_nonce_sha256 = None


def _release_queue_redeploy_run_lock(run_lock: ExclusiveRunLock) -> None:
    if run_lock.adopted_market_maintenance is not None:
        run_lock.restore_adopted_market_pipeline_maintenance()
    else:
        run_lock.release()


class PhaseJournal:
    def __init__(
        self,
        artifact_dir: Path,
        *,
        command: str,
        source_sha256: str,
        git_head: str,
        run_lock: ExclusiveRunLock,
        recovery_source_backup: SecureSourceBackup | None = None,
    ) -> None:
        lock_binding = run_lock.binding()
        self.payload: dict[str, Any] = {
            "schema_version": 1,
            "environment": "production",
            "command": command,
            "status": "prepared",
            "state_history": [{"status": "prepared", "at": _utc_now()}],
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "source_sha256": source_sha256,
            "git_head": git_head,
            "run_lock": lock_binding,
            "secrets_disclosed": False,
        }
        if recovery_source_backup is not None:
            self.payload.update(
                {
                    "recovery_source_backup_file": recovery_source_backup.path.name,
                    "recovery_source_backup_sha256": recovery_source_backup.sha256,
                    "recovery_source_backup_path_sha256": hashlib.sha256(
                        str(
                            recovery_source_backup.path.resolve(strict=False)
                        ).encode("utf-8")
                    ).hexdigest(),
                }
            )
        self.path, _digest = _write_secure_json(
            artifact_dir, "production-queue-phase", self.payload
        )

    def update(self, status: str, **facts: Any) -> None:
        self.payload.update(facts)
        self.payload["status"] = status
        self.payload["updated_at"] = _utc_now()
        history = self.payload.setdefault("state_history", [])
        if not isinstance(history, list):
            raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")
        history.append({"status": status, "at": self.payload["updated_at"]})
        rendered = (
            json.dumps(self.payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _atomic_write(self.path, rendered)
        if scan_paths([self.path]).get("status") != "clean":
            raise ProductionCutoverError("PHASE_JOURNAL_REDACTION_FAILED")

    @classmethod
    def adopt(cls, path: Path, *, run_lock: ExclusiveRunLock) -> "PhaseJournal":
        journal = cls.__new__(cls)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL") from exc
        if (
            path.is_symlink()
            or not isinstance(payload, dict)
            or payload.get("environment") != "production"
            or payload.get("status") in PHASE_TERMINAL_STATES
            or payload.get("secrets_disclosed") is not False
        ):
            raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")
        interrupted_bindings = payload.setdefault("interrupted_run_locks", [])
        if not isinstance(interrupted_bindings, list):
            raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")
        interrupted_bindings.append(payload.get("run_lock"))
        payload["interrupted_run_lock"] = payload.get("run_lock")
        payload["run_lock"] = run_lock.binding()
        payload.setdefault(
            "state_history",
            [{"status": str(payload.get("status") or "unknown"), "at": _utc_now()}],
        )
        journal.path = path
        journal.payload = payload
        journal.update("interrupted_recovery_acquired")
        return journal


def _render_receipt(receipt: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _commit_terminal_receipt(
    journal: PhaseJournal,
    artifact_dir: Path,
    *,
    prefix: str,
    receipt: Mapping[str, Any],
    terminal_status: str,
    **facts: Any,
) -> tuple[Path, str]:
    """WAL-bind a terminal receipt before publishing the receipt file."""

    if terminal_status not in PHASE_RECEIPT_STATES:
        raise ProductionCutoverError("PHASE_JOURNAL_TERMINAL_STATUS_INVALID")
    secure = _ensure_secure_artifact_dir(artifact_dir)
    path = secure / f"{prefix}-{secrets.token_hex(16)}.json"
    body = _render_receipt(receipt)
    digest = hashlib.sha256(body).hexdigest()
    journal.update(
        "terminal_receipt_pending",
        terminal_status=terminal_status,
        pending_receipt_file=path.name,
        pending_receipt_sha256=digest,
        pending_receipt_payload=dict(receipt),
        **facts,
    )
    _atomic_write(path, body)
    if scan_paths([path]).get("status") != "clean":
        path.unlink(missing_ok=True)
        raise ProductionCutoverError("RECEIPT_REDACTION_FAILED")
    journal.payload.pop("pending_receipt_payload", None)
    journal.payload.pop("terminal_status", None)
    journal.payload.pop("pending_receipt_file", None)
    journal.payload.pop("pending_receipt_sha256", None)
    journal.update(
        terminal_status,
        receipt_file=path.name,
        receipt_sha256=digest,
        **facts,
    )
    return path, digest


def _recover_terminal_receipt_journals(artifact_dir: Path) -> None:
    """Finish a terminal receipt transaction left between its two fsyncs."""

    secure = _ensure_secure_artifact_dir(artifact_dir)
    for path in secure.glob("production-queue-phase-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL") from None
        if payload.get("status") != "terminal_receipt_pending":
            continue
        terminal_status = str(payload.get("terminal_status") or "")
        receipt_name = str(payload.get("pending_receipt_file") or "")
        receipt_digest = str(payload.get("pending_receipt_sha256") or "")
        receipt_payload = payload.get("pending_receipt_payload")
        if (
            terminal_status not in PHASE_RECEIPT_STATES
            or Path(receipt_name).name != receipt_name
            or not re.fullmatch(r"[0-9a-f]{64}", receipt_digest)
            or not isinstance(receipt_payload, dict)
        ):
            raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")
        receipt_path = secure / receipt_name
        body = _render_receipt(receipt_payload)
        if hashlib.sha256(body).hexdigest() != receipt_digest:
            raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")
        if receipt_path.exists():
            if receipt_path.is_symlink() or _sha256(receipt_path) != receipt_digest:
                raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")
        else:
            _atomic_write(receipt_path, body)
        if scan_paths([receipt_path]).get("status") != "clean":
            raise ProductionCutoverError("RECEIPT_REDACTION_FAILED")
        payload.pop("pending_receipt_payload", None)
        payload.pop("terminal_status", None)
        payload.pop("pending_receipt_file", None)
        payload.pop("pending_receipt_sha256", None)
        payload["status"] = terminal_status
        payload["receipt_file"] = receipt_name
        payload["receipt_sha256"] = receipt_digest
        payload["updated_at"] = _utc_now()
        history = payload.setdefault("state_history", [])
        if not isinstance(history, list):
            raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")
        history.append({"status": terminal_status, "at": payload["updated_at"]})
        _atomic_write(path, _render_receipt(payload))
        if scan_paths([path]).get("status") != "clean":
            raise ProductionCutoverError("PHASE_JOURNAL_REDACTION_FAILED")


def _pending_phase_journals(
    artifact_dir: Path, *, command: str
) -> list[tuple[Path, dict[str, Any]]]:
    """Return the one exact interrupted transaction or fail closed."""

    secure = _ensure_secure_artifact_dir(artifact_dir)
    pending: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(secure.glob("production-queue-phase-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            metadata = path.lstat()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL") from None
        if payload.get("status") in PHASE_TERMINAL_STATES:
            continue
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or payload.get("schema_version") != 1
            or payload.get("environment") != "production"
            or payload.get("command") != command
            or payload.get("secrets_disclosed") is not False
        ):
            raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")
        pending.append((path, payload))
    if len(pending) > 1:
        raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL")
    return pending


def _create_recovery_source_snapshot(
    source: Path, secure_backup_dir: Path, *, expected_sha256: str
) -> SecureSourceBackup:
    """Persist the pre-command source required by kill recovery."""

    if secure_backup_dir.is_symlink():
        raise ProductionCutoverError("BLOCKED_SECURE_BACKUP_DIRECTORY")
    directory = secure_backup_dir.resolve(strict=False)
    if (
        not directory.is_dir()
        or directory.stat().st_uid != os.geteuid()
        or stat.S_IMODE(directory.stat().st_mode) & 0o077
        or directory == REPO_ROOT
        or REPO_ROOT in directory.parents
    ):
        raise ProductionCutoverError("BLOCKED_SECURE_BACKUP_DIRECTORY")
    original = source.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if digest != expected_sha256:
        raise ProductionCutoverError("BLOCKED_SOURCE_DRIFT")
    descriptor, raw_path = tempfile.mkstemp(
        prefix="telegram-queue-recovery-source-", suffix=".bak", dir=directory
    )
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, original)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return SecureSourceBackup(path=path, sha256=digest, original=original)


def _load_recovery_source_snapshot(
    payload: Mapping[str, Any], secure_backup_dir: Path
) -> SecureSourceBackup:
    directory = secure_backup_dir.resolve(strict=False)
    name = str(payload.get("recovery_source_backup_file") or "")
    digest = str(payload.get("recovery_source_backup_sha256") or "")
    path_binding = str(payload.get("recovery_source_backup_path_sha256") or "")
    if (
        Path(name).name != name
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not re.fullmatch(r"[0-9a-f]{64}", path_binding)
    ):
        raise ProductionCutoverError("BLOCKED_INTERRUPTED_RECOVERY_SOURCE")
    path = directory / name
    try:
        metadata = path.lstat()
        original = path.read_bytes()
    except OSError:
        raise ProductionCutoverError("BLOCKED_INTERRUPTED_RECOVERY_SOURCE") from None
    if (
        secure_backup_dir.is_symlink()
        or not directory.is_dir()
        or directory.stat().st_uid != os.geteuid()
        or stat.S_IMODE(directory.stat().st_mode) & 0o077
        or directory == REPO_ROOT
        or REPO_ROOT in directory.parents
        or path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or hashlib.sha256(original).hexdigest() != digest
        or hashlib.sha256(str(path.resolve(strict=False)).encode("utf-8")).hexdigest()
        != path_binding
    ):
        raise ProductionCutoverError("BLOCKED_INTERRUPTED_RECOVERY_SOURCE")
    return SecureSourceBackup(path=path, sha256=digest, original=original)


def _update_source_from_recovery_snapshot(
    source: Path,
    backup: SecureSourceBackup,
    updates: Mapping[str, str],
    *,
    expected_source_sha256: str,
) -> dict[str, Any]:
    if (
        backup.sha256 != expected_source_sha256
        or _sha256(backup.path) != backup.sha256
        or backup.path.read_bytes() != backup.original
        or _sha256(source) != expected_source_sha256
    ):
        raise ProductionCutoverError("BLOCKED_SOURCE_BACKUP_DIGEST")
    updated = upsert_env_lines(
        backup.original.decode("utf-8"), updates
    ).encode("utf-8")
    try:
        _atomic_write(source, updated)
    except BaseException:
        _atomic_write(source, backup.original)
        raise
    return {
        "status": "updated_atomically",
        "backup_sha256": backup.sha256,
        "source_before_sha256": expected_source_sha256,
        "source_after_sha256": hashlib.sha256(updated).hexdigest(),
        "backup_file": backup.path.name,
        "backup_path_binding_sha256": hashlib.sha256(
            str(backup.path.resolve(strict=False)).encode("utf-8")
        ).hexdigest(),
        "updated_keys": sorted(updates),
        "secret_values_disclosed": False,
    }


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
            try:
                path_metadata = self.path.lstat()
            except OSError as exc:
                raise ProductionCutoverError(
                    "BLOCKED_IMMUTABLE_SOURCE_LOCK"
                ) from exc
            if (
                not stat.S_ISREG(metadata.st_mode)
                or not stat.S_ISREG(path_metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or path_metadata.st_dev != metadata.st_dev
                or path_metadata.st_ino != metadata.st_ino
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
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=None if env is None else dict(env),
        start_new_session=True,
        pass_fds=pass_fds,
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

    def __init__(self, manifest: Path, *, release_root: Path | None = None) -> None:
        self.manifest = manifest
        explicit_release_root = release_root is not None
        candidate_root = release_root or REPO_ROOT
        try:
            resolved_root = candidate_root.resolve(strict=True)
            root_info = candidate_root.lstat()
        except OSError as exc:
            raise ProductionCutoverError(
                "BLOCKED_PRODUCTION_RELEASE_ROOT"
            ) from exc
        if (
            not candidate_root.is_absolute()
            or candidate_root.is_symlink()
            or resolved_root != candidate_root
            or not stat.S_ISDIR(root_info.st_mode)
        ):
            raise ProductionCutoverError("BLOCKED_PRODUCTION_RELEASE_ROOT")
        self.release_root = candidate_root
        self._control_release_mode = (
            explicit_release_root and REPO_ROOT != self.release_root
        )
        self.manifest_values = parse_env_file(manifest)
        if explicit_release_root:
            project_text = str(
                self.manifest_values.get("LOCAL_PROJECT_DIR") or ""
            ).strip()
            try:
                project_root = Path(project_text)
                project_resolved = project_root.resolve(strict=True)
                project_info = project_root.lstat()
            except OSError as exc:
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_RELEASE_ROOT"
                ) from exc
            if (
                not project_root.is_absolute()
                or project_root.is_symlink()
                or project_resolved != self.release_root
                or not stat.S_ISDIR(project_info.st_mode)
            ):
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_RELEASE_ROOT"
                )
        for required_path in (
            self.release_root / "scripts/production_deploy_online.sh",
            REPO_ROOT / "scripts/production_deploy_online.sh",
            FENCED_DEPLOY_SUPERVISOR,
        ):
            try:
                required_info = required_path.lstat()
            except OSError as exc:
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_RELEASE_ROOT"
                ) from exc
            if required_path.is_symlink() or not stat.S_ISREG(
                required_info.st_mode
            ):
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_RELEASE_ROOT"
                )
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

    def _run(
        self,
        args: list[str],
        *,
        timeout: int = 120,
        env: Mapping[str, str] | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        return _run_contained_process(
            args,
            cwd=self.release_root,
            timeout=timeout,
            env=None if env is None else dict(env),
            pass_fds=pass_fds,
        )

    def _open_release_deploy_script(self) -> tuple[int, str]:
        """Open the approved checkout script and bind it to the control copy."""

        path = self.release_root / "scripts/production_deploy_online.sh"
        control_copy = REPO_ROOT / "scripts/production_deploy_online.sh"
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ProductionCutoverError(
                "BLOCKED_PRODUCTION_DEPLOY_SCRIPT"
            ) from exc
        try:
            before = os.fstat(descriptor)
            path_info = path.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or (path_info.st_dev, path_info.st_ino)
                != (before.st_dev, before.st_ino)
            ):
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_DEPLOY_SCRIPT"
                )
            hasher = hashlib.sha256()
            offset = 0
            while True:
                chunk = os.pread(descriptor, 1024 * 1024, offset)
                if not chunk:
                    break
                hasher.update(chunk)
                offset += len(chunk)
            after = os.fstat(descriptor)
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or offset != before.st_size
                or _sha256(control_copy) != hasher.hexdigest()
            ):
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_DEPLOY_SCRIPT"
                )
            return descriptor, hasher.hexdigest()
        except BaseException:
            os.close(descriptor)
            raise

    def _open_control_supervisor(self) -> tuple[int, str, Path]:
        """Hold a manifest- or exact-Git-bound supervisor through execution."""

        relative = "scripts/run_fenced_production_deploy.py"
        supervisor_path = FENCED_DEPLOY_SUPERVISOR
        expected_digest: str | None = None
        control_release_mode = bool(
            getattr(self, "_control_release_mode", False)
        )
        if control_release_mode and (
            not CONTROL_PAYLOAD_MANIFEST.exists()
            or CONTROL_PAYLOAD_MANIFEST.is_symlink()
        ):
            raise ProductionCutoverError(
                "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
            )
        if control_release_mode:
            try:
                manifest_descriptor = os.open(
                    CONTROL_PAYLOAD_MANIFEST,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
            except OSError as exc:
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
                ) from exc
            try:
                manifest_before = os.fstat(manifest_descriptor)
                if (
                    not stat.S_ISREG(manifest_before.st_mode)
                    or manifest_before.st_uid != os.geteuid()
                    or manifest_before.st_nlink != 1
                    or bool(manifest_before.st_mode & 0o022)
                    or not 0 < manifest_before.st_size <= 2_000_000
                ):
                    raise ProductionCutoverError(
                        "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
                    )
                manifest_payload = os.pread(
                    manifest_descriptor, manifest_before.st_size + 1, 0
                )
                manifest_after = os.fstat(manifest_descriptor)
                if (
                    len(manifest_payload) != manifest_before.st_size
                    or (
                        manifest_before.st_dev,
                        manifest_before.st_ino,
                        manifest_before.st_size,
                        manifest_before.st_mtime_ns,
                        manifest_before.st_ctime_ns,
                    )
                    != (
                        manifest_after.st_dev,
                        manifest_after.st_ino,
                        manifest_after.st_size,
                        manifest_after.st_mtime_ns,
                        manifest_after.st_ctime_ns,
                    )
                ):
                    raise ProductionCutoverError(
                        "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
                    )
                for raw_line in manifest_payload.decode("utf-8").splitlines():
                    digest, separator, name = raw_line.partition("  ./")
                    if (
                        not separator
                        or not re.fullmatch(r"[0-9a-f]{64}", digest)
                        or name == relative and expected_digest is not None
                    ):
                        raise ProductionCutoverError(
                            "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
                        )
                    if name == relative:
                        expected_digest = digest
            except (UnicodeDecodeError, ValueError) as exc:
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
                ) from exc
            finally:
                os.close(manifest_descriptor)
        else:
            # Standalone Queue-v1 recovery still runs from an approved clean
            # checkout.  Bind to the exact Git object instead of requiring a
            # control-release-only manifest that is absent in that mode.
            supervisor_path = self.release_root / relative
            git_environment = {
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "HOME": os.environ.get("HOME", "/root"),
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            }
            try:
                head = subprocess.run(
                    ["/usr/bin/git", "-C", str(self.release_root), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    env=git_environment,
                ).stdout.strip()
                approved = subprocess.run(
                    [
                        "/usr/bin/git", "-C", str(self.release_root), "rev-parse",
                        "refs/remotes/origin/main",
                    ],
                    check=True,
                    capture_output=True,
                    env=git_environment,
                ).stdout.strip()
                branch = subprocess.run(
                    [
                        "/usr/bin/git", "-C", str(self.release_root),
                        "symbolic-ref", "--short", "HEAD",
                    ],
                    check=True,
                    capture_output=True,
                    env=git_environment,
                ).stdout.strip()
                porcelain = subprocess.run(
                    [
                        "/usr/bin/git", "-C", str(self.release_root), "status",
                        "--porcelain=v1", "--untracked-files=all",
                    ],
                    check=True,
                    capture_output=True,
                    env=git_environment,
                ).stdout
                git_payload = subprocess.run(
                    [
                        "/usr/bin/git", "-C", str(self.release_root), "show",
                        f"HEAD:{relative}",
                    ],
                    check=True,
                    capture_output=True,
                    env=git_environment,
                ).stdout
            except (OSError, subprocess.CalledProcessError) as exc:
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
                ) from exc
            if (
                head != approved
                or branch != b"main"
                or porcelain
                or not re.fullmatch(rb"[0-9a-f]{40}", head)
            ):
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
                )
            expected_digest = hashlib.sha256(git_payload).hexdigest()
        if expected_digest is None:
            raise ProductionCutoverError(
                "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
            )

        try:
            descriptor = os.open(
                supervisor_path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            raise ProductionCutoverError(
                "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
            ) from exc
        try:
            before = os.fstat(descriptor)
            path_info = supervisor_path.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or bool(before.st_mode & 0o022)
                or (path_info.st_dev, path_info.st_ino)
                != (before.st_dev, before.st_ino)
            ):
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
                )
            payload = os.pread(descriptor, before.st_size + 1, 0)
            after = os.fstat(descriptor)
            observed_digest = hashlib.sha256(payload).hexdigest()
            if (
                len(payload) != before.st_size
                or observed_digest != expected_digest
                or (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
            ):
                raise ProductionCutoverError(
                    "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
                )
            return descriptor, observed_digest, supervisor_path
        except BaseException:
            os.close(descriptor)
            raise

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

    def _container_bot_process_count(self, role: str, container_id: str) -> int:
        result = self._docker(
            role, ["top", container_id, "-eo", "pid,ppid,args"]
        )
        if result.returncode:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_READBACK_FAILED")
        return sum(
            "run_bot.py" in line and "python" in line.lower()
            for line in (result.stdout or "").splitlines()[1:]
        )

    def _is_allowed_colocated_staging_bot(
        self, role: str, container_id: str
    ) -> bool:
        if role != "foreign":
            return False
        observed = self._docker(
            role,
            [
                "inspect",
                "-f",
                '{{json .Config.Env}}\t{{index .Config.Labels "com.docker.compose.project"}}\t{{index .Config.Labels "com.docker.compose.service"}}',
                container_id,
            ],
        )
        if observed.returncode:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_READBACK_FAILED")
        try:
            raw_env, project, service = (observed.stdout or "").strip().split("\t", 2)
            environment = {
                row.split("=", 1)[0]: row.split("=", 1)[1]
                for row in json.loads(raw_env)
                if isinstance(row, str) and "=" in row
            }
        except (TypeError, ValueError):
            raise ProductionCutoverError("EXECUTOR_INVENTORY_READBACK_FAILED") from None
        return (
            project == FOREIGN_COLOCATED_STAGING_PROJECT
            and service in {"bot", "bot_executor"}
            and environment.get("TRADING_BOT_SERVICE") == "bot"
            and environment.get("SERVER_MODE") == "foreign"
        )

    def _partition_bot_containers(
        self, role: str, container_ids: list[str]
    ) -> tuple[list[str], list[str]]:
        production: list[str] = []
        allowed_staging: list[str] = []
        for container_id in container_ids:
            target = (
                allowed_staging
                if self._is_allowed_colocated_staging_bot(role, container_id)
                else production
            )
            target.append(container_id)
        return production, allowed_staging

    def _host_bot_process_count(
        self, role: str, *, excluded_containers: tuple[str, ...] = ()
    ) -> int:
        result = self._host(role, ["ps", "-eo", "args="])
        if result.returncode:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_READBACK_FAILED")
        observed = sum(
            "run_bot.py" in line and "python" in line.lower()
            for line in (result.stdout or "").splitlines()
        )
        excluded = sum(
            self._container_bot_process_count(role, container_id)
            for container_id in excluded_containers
        )
        if excluded > observed:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_READBACK_FAILED")
        return observed - excluded

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
            str(self.manifest_values.get("LOCAL_PROJECT_DIR") or self.release_root)
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
        result = self._host(role, ["bash", "-lc", script])
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
        iran_candidates, iran_staging = self._partition_bot_containers(
            "iran", self._potential_bot_containers("iran")
        )
        iran_host_process_count = self._host_bot_process_count(
            "iran", excluded_containers=tuple(iran_staging)
        )
        if iran_candidates or iran_staging or iran_host_process_count:
            raise ProductionCutoverError("EXECUTOR_INVENTORY_AMBIGUOUS")
        container_ids, allowed_staging = self._partition_bot_containers(
            "foreign", self._potential_bot_containers("foreign")
        )
        host_process_count = self._host_bot_process_count(
            "foreign", excluded_containers=tuple(allowed_staging)
        )
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
        process_count = self._container_bot_process_count("foreign", container_id)
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

    def release_schema_inventory(self, expected_database_name: str) -> dict[str, Any]:
        """Bind the currently deployed Queue runtime to a fresh backup.

        A Queue-v1 redeploy can target a newer clean/pushed commit than the one
        currently running.  The ordinary cutover preflight therefore cannot be
        reused: it intentionally requires the live release to equal Git HEAD.
        This inventory reads the current release/schema/database identities
        without assuming that equality, while still requiring both production
        hosts to agree and every Queue table to be present.
        """

        table_array = ",".join(
            f"'public.{table}'" for table in REQUIRED_QUEUE_TABLES
        )
        query = (
            "select json_build_object("
            "'head',(select version_num from alembic_version limit 1),"
            "'database_name',current_database(),"
            "'system_identifier',(select system_identifier::text from pg_control_system()),"
            f"'queue_table_count',(select count(*) from unnest(array[{table_array}]) t(name) "
            "where to_regclass(name) is not null))"
        )
        reports: dict[str, dict[str, Any]] = {}
        for role, containers in (
            ("foreign", FOREIGN_CONTAINERS),
            ("iran", IRAN_CONTAINERS),
        ):
            app_env = self._container_env(role, containers["app"])
            release_sha = str(app_env.get("RELEASE_SHA") or "").strip()
            if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
                raise ProductionCutoverError("REDEPLOY_RUNTIME_RELEASE_READBACK_FAILED")
            self._require_project(role, containers["db"])
            schema = self._docker(
                role,
                [
                    "exec",
                    containers["db"],
                    "sh",
                    "-lc",
                    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc '
                    + shlex.quote(query),
                ],
            )
            if schema.returncode:
                raise ProductionCutoverError("REDEPLOY_RUNTIME_SCHEMA_READBACK_FAILED")
            try:
                payload = json.loads((schema.stdout or "").strip())
                database_name = str(payload.get("database_name") or "")
                system_identifier = str(payload.get("system_identifier") or "")
                if not database_name or not re.fullmatch(
                    r"[0-9]+", system_identifier
                ):
                    raise ValueError("invalid database identity")
                reports[role] = {
                    "release_sha": release_sha,
                    "schema_head": str(payload.get("head") or ""),
                    "database_name": database_name,
                    "database_identity_sha256": database_identity_sha256(
                        role, database_name, system_identifier
                    ),
                    "queue_table_count": int(payload.get("queue_table_count") or 0),
                }
            except (TypeError, ValueError):
                raise ProductionCutoverError(
                    "REDEPLOY_RUNTIME_SCHEMA_READBACK_FAILED"
                ) from None
        foreign = reports["foreign"]
        iran = reports["iran"]
        if (
            foreign["release_sha"] != iran["release_sha"]
            or foreign["schema_head"] != iran["schema_head"]
            or not re.fullmatch(r"[0-9a-z]{12}", foreign["schema_head"])
            or foreign["database_name"] != expected_database_name
            or iran["database_name"] != expected_database_name
            or foreign["queue_table_count"] != len(REQUIRED_QUEUE_TABLES)
            or iran["queue_table_count"] != len(REQUIRED_QUEUE_TABLES)
        ):
            raise ProductionCutoverError("REDEPLOY_RUNTIME_PAIR_MISMATCH")
        return {
            "status": "verified",
            "release_sha": foreign["release_sha"],
            "schema_head": foreign["schema_head"],
            "queue_table_count": len(REQUIRED_QUEUE_TABLES),
            "database_identity_sha256": {
                role: reports[role]["database_identity_sha256"]
                for role in ("foreign", "iran")
            },
            "hosts": ["foreign", "iran"],
            "addresses_disclosed": False,
        }

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

    def private_primary_legacy_inputs_off(self) -> dict[str, object]:
        """Revalidate that no legacy Product feed can restart after deploy."""

        timers = (
            "coin-group-event-telegram.timer",
            "trading-bot-private-gold-collector.timer",
            "coin-intelligence-production-snapshot-relay.timer",
        )
        services = (
            "coin-group-event-telegram.service",
            "trading-bot-private-gold-collector.service",
            "coin-intelligence-production-snapshot-relay.service",
        )
        for unit in (*timers, *services):
            active = self._host(
                "foreign", ["systemctl", "is-active", "--quiet", unit]
            )
            if active.returncode == 0 or active.returncode not in {3, 4}:
                raise ProductionCutoverError(
                    "PRIVATE_PRIMARY_LEGACY_INPUT_ACTIVE"
                )
        for unit in timers:
            enabled = self._host(
                "foreign", ["systemctl", "is-enabled", "--quiet", unit]
            )
            if enabled.returncode == 0 or enabled.returncode not in {1, 4}:
                raise ProductionCutoverError(
                    "PRIVATE_PRIMARY_LEGACY_INPUT_ENABLED"
                )
        return {
            "status": "verified",
            "legacy_input_units_active": 0,
            "legacy_input_timers_enabled": 0,
            "unit_count": len(timers) + len(services),
        }

    def product_estimator_runtime_mode(
        self, expected_mode: str
    ) -> dict[str, object]:
        """Prove the running Product consumers all expose one exact authority.

        Reading the immutable source file is not sufficient before the final
        compare-and-swap: an older or prematurely restarted container can be
        running with a different environment.  This probe is deliberately
        limited to the three Product consumers and returns no environment
        values beyond the expected public mode.
        """

        if expected_mode not in {"LEGACY", "PRIVATE_PRIMARY"}:
            raise ProductionCutoverError("BLOCKED_PRODUCT_RUNTIME_MODE")
        consumers = (
            ("foreign", FOREIGN_CONTAINERS["app"]),
            ("foreign", FOREIGN_CONTAINERS["bot"]),
            ("iran", IRAN_CONTAINERS["app"]),
        )
        for role, container in consumers:
            if not self._running(role, container):
                raise ProductionCutoverError("BLOCKED_PRODUCT_RUNTIME_MODE")
            environment = self._container_env(role, container)
            if environment.get("PRODUCT_ESTIMATOR_SNAPSHOT_MODE") != expected_mode:
                raise ProductionCutoverError("BLOCKED_PRODUCT_RUNTIME_MODE")
        return {
            "status": "verified",
            "mode": expected_mode,
            "consumer_count": len(consumers),
            "values_disclosed": False,
        }

    def private_primary_snapshot_identity(
        self, *, expected_digest: str
    ) -> dict[str, object]:
        """Re-read the current bot/app and remote Product artifact identity."""

        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            raise ProductionCutoverError(
                "PRIVATE_PRIMARY_SNAPSHOT_IDENTITY_INVALID"
            )
        local_app = Path(
            str(
                self.manifest_values.get(
                    "PRODUCTION_PRODUCT_ESTIMATOR_APP_SNAPSHOT_HOST_DIR"
                )
                or ""
            )
        )
        local_bot = Path(
            str(
                self.manifest_values.get(
                    "PRODUCTION_PRODUCT_ESTIMATOR_BOT_SNAPSHOT_HOST_DIR"
                )
                or ""
            )
        )
        remote = Path(
            str(
                self.manifest_values.get(
                    "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_SNAPSHOT_HOST_DIR"
                )
                or ""
            )
        )
        if (
            not local_app.is_absolute()
            or local_app != local_bot
            or not remote.is_absolute()
            or any(".." in path.parts for path in (local_app, remote))
        ):
            raise ProductionCutoverError(
                "PRIVATE_PRIMARY_SNAPSHOT_IDENTITY_INVALID"
            )
        local_path = local_app / "latest-private-primary.json"
        remote_path = remote / "latest-private-primary.json"
        def stable_local_digest() -> str:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                descriptor = os.open(local_path, flags)
                try:
                    before = os.fstat(descriptor)
                    path_info = local_path.lstat()
                    hasher = hashlib.sha256()
                    while True:
                        chunk = os.read(descriptor, 131072)
                        if not chunk:
                            break
                        hasher.update(chunk)
                    after = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                raise ProductionCutoverError(
                    "PRIVATE_PRIMARY_SNAPSHOT_IDENTITY_INVALID"
                ) from None
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                )
                or (path_info.st_dev, path_info.st_ino)
                != (before.st_dev, before.st_ino)
            ):
                raise ProductionCutoverError(
                    "PRIVATE_PRIMARY_SNAPSHOT_IDENTITY_INVALID"
                )
            return hasher.hexdigest()

        local_digest = stable_local_digest()
        remote_result = self._host(
            "iran", ["sha256sum", "--", str(remote_path)]
        )
        remote_tokens = (remote_result.stdout or "").strip().split()
        if (
            remote_result.returncode
            or len(remote_tokens) != 2
            or remote_tokens[0] != expected_digest
            or remote_tokens[1] != str(remote_path)
            or local_digest != expected_digest
        ):
            raise ProductionCutoverError(
                "PRIVATE_PRIMARY_SNAPSHOT_IDENTITY_INVALID"
            )
        # A second descriptor-bound, no-follow read closes the remote-probe
        # window without reintroducing pathname/symlink TOCTOU.
        if stable_local_digest() != expected_digest:
            raise ProductionCutoverError(
                "PRIVATE_PRIMARY_SNAPSHOT_IDENTITY_INVALID"
            )
        return {
            "status": "verified",
            "snapshot_digest": expected_digest,
            "consumer_artifact_count": 3,
        }

    def private_primary_publication_outbox_zero(self) -> dict[str, object]:
        """Read the live receiver journal and require no unresolved publish."""

        project = str(
            self.manifest_values.get("PRODUCTION_MARKET_PIPELINE_PROJECT_NAME")
            or ""
        )
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,62}", project):
            raise ProductionCutoverError(
                "PRIVATE_PRIMARY_OUTBOX_READBACK_FAILED"
            )
        listed = self._docker(
            "iran",
            [
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                "label=com.docker.compose.service=estimator-snapshot-receiver",
            ],
        )
        containers = [
            line.strip()
            for line in (listed.stdout or "").splitlines()
            if line.strip()
        ]
        if listed.returncode or len(containers) != 1:
            raise ProductionCutoverError(
                "PRIVATE_PRIMARY_OUTBOX_READBACK_FAILED"
            )
        query = (
            "import sqlite3; "
            "c=sqlite3.connect('file:/var/lib/market-data/state/estimator-snapshot-receiver.sqlite3?mode=ro',uri=True); "
            "c.execute('PRAGMA query_only=ON'); "
            "print(c.execute(\"SELECT COUNT(*) FROM estimator_snapshot_publication_outbox WHERE feed_mode='PRIVATE_PRIMARY' AND delivered_at_utc IS NULL\").fetchone()[0]); "
            "c.close()"
        )
        observed = self._docker(
            "iran", ["exec", containers[0], "python3", "-c", query]
        )
        try:
            count = int((observed.stdout or "").strip())
        except ValueError:
            raise ProductionCutoverError(
                "PRIVATE_PRIMARY_OUTBOX_READBACK_FAILED"
            ) from None
        if observed.returncode or count != 0:
            raise ProductionCutoverError(
                "PRIVATE_PRIMARY_OUTBOX_NOT_DRAINED"
            )
        return {"status": "verified", "open_outbox": 0}

    def deploy_official(
        self,
        authority_path: Path | None = None,
        authority_digest: str | None = None,
        *,
        private_primary_attestation: PrivatePrimaryDeployAttestation | None = None,
        inherited_lock_descriptors: tuple[int, int] | None = None,
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
        env["PRODUCTION_RELEASE_ROOT_FD_EXEC_CONFIRM"] = (
            "verified-release-root-fd-exec"
        )
        env["PRODUCTION_RELEASE_ROOT_FD_EXEC"] = str(self.release_root)
        env["PRODUCTION_RELEASE_ROOT_FD_EXEC_SHA256"] = hashlib.sha256(
            str(self.release_root).encode("utf-8")
        ).hexdigest()
        if (authority_path is None) != (not authority_digest):
            raise ProductionCutoverError("BLOCKED_QUEUE_DEPLOY_AUTHORITY")
        if private_primary_attestation is not None and authority_path is None:
            raise ProductionCutoverError("BLOCKED_PRIVATE_PRIMARY_ATTESTATION")
        if authority_path is not None and inherited_lock_descriptors is None:
            raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_LOCKS_REQUIRED")
        if authority_path is not None and authority_digest:
            env["TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT"] = str(authority_path)
            env["TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT_SHA256"] = authority_digest
            env["PRODUCTION_SOURCE_LOCK_INHERITED_CONFIRM"] = (
                "verified-cutover-held-lock"
            )
        if private_primary_attestation is not None:
            _private_primary_attestation_binding(
                self.manifest, private_primary_attestation
            )
        deploy_script_fd, deploy_script_sha256 = (
            self._open_release_deploy_script()
        )
        argv = [
            "bash",
            f"/proc/self/fd/{deploy_script_fd}",
            "--manifest",
            str(self.manifest),
        ]
        if private_primary_attestation is not None:
            argv.extend(
                [
                    "--private-primary-manifest-sha256",
                    private_primary_attestation.manifest_sha256,
                    "--private-primary-manifest-receipt",
                    str(private_primary_attestation.receipt_path),
                    "--private-primary-manifest-receipt-sha256",
                    private_primary_attestation.receipt_sha256,
                ]
            )
        argv.append("release")
        try:
            if authority_path is None or authority_digest is None:
                result = self._run(
                    argv,
                    timeout=7200,
                    env=env,
                    pass_fds=(deploy_script_fd,),
                )
                if result.returncode:
                    raise ProductionCutoverError(
                        "OFFICIAL_PRODUCTION_DEPLOY_FAILED"
                    )
                return {
                    "status": "completed",
                    "official_script": True,
                    "output_retained": False,
                }
            run_lock_fd, source_lock_fd = inherited_lock_descriptors
            fence_path, fence_digest = _prepare_deploy_child_fence(
                authority_path=authority_path,
                authority_digest=authority_digest,
                manifest=self.manifest,
                command=argv,
                run_lock_descriptor=run_lock_fd,
                source_lock_descriptor=source_lock_fd,
                deploy_script_sha256=deploy_script_sha256,
            )
            supervisor_fd, supervisor_sha256, supervisor_path = (
                self._open_control_supervisor()
            )
            supervisor_argv = [
                sys.executable,
                f"/proc/self/fd/{supervisor_fd}",
                "--journal",
                str(fence_path),
                "--expected-journal-sha256",
                fence_digest,
                "--run-lock-fd",
                str(run_lock_fd),
                "--source-lock-fd",
                str(source_lock_fd),
                "--deploy-script-fd",
                str(deploy_script_fd),
                "--expected-deploy-script-sha256",
                deploy_script_sha256,
                "--cwd",
                str(self.release_root),
                "--",
                *argv,
            ]
            try:
                result = self._run(
                    supervisor_argv,
                    timeout=7200,
                    env=env,
                    pass_fds=(
                        run_lock_fd,
                        source_lock_fd,
                        deploy_script_fd,
                        supervisor_fd,
                    ),
                )
                supervisor_after = os.fstat(supervisor_fd)
                supervisor_path_info = supervisor_path.lstat()
                if (
                    hashlib.sha256(
                        os.pread(
                            supervisor_fd,
                            supervisor_after.st_size + 1,
                            0,
                        )
                    ).hexdigest()
                    != supervisor_sha256
                    or (supervisor_after.st_dev, supervisor_after.st_ino)
                    != (supervisor_path_info.st_dev, supervisor_path_info.st_ino)
                ):
                    raise ProductionCutoverError(
                        "BLOCKED_PRODUCTION_DEPLOY_SUPERVISOR"
                    )
            finally:
                os.close(supervisor_fd)
            fence = _read_deploy_child_fence(
                fence_path,
                authority_digest=authority_digest,
                expected_command=argv,
            )
            if result.returncode:
                raise ProductionCutoverError(
                    "OFFICIAL_PRODUCTION_DEPLOY_FAILED"
                )
            if (
                fence.get("status") != "SUCCEEDED"
                or fence.get("returncode") != 0
                or fence.get("deploy_script_sha256")
                != deploy_script_sha256
            ):
                raise ProductionCutoverError(
                    "OFFICIAL_PRODUCTION_DEPLOY_FAILED"
                )
            readiness = fence.get("product_readiness")
            if private_primary_attestation is not None and (
                not isinstance(readiness, Mapping)
                or readiness.get("consumer_count") != 3
                or readiness.get("required_source_input_trace_count") != 9
            ):
                raise ProductionCutoverError(
                    "OFFICIAL_PRIVATE_PRIMARY_READINESS_MISSING"
                )
            return {
                "status": "completed",
                "official_script": True,
                "output_retained": False,
                "deploy_fence_file": fence_path.name,
                "deploy_fence_sha256": _sha256(fence_path),
                "deploy_fence_status": "SUCCEEDED",
                "product_readiness": readiness,
            }
        finally:
            os.close(deploy_script_fd)

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
            if expected_owner == "queue-v1":
                # API/sync/migration processes are producer-only, but they
                # must retain Queue-v1 routing semantics so publication jobs
                # can be assigned to a healthy publisher lane.  Keep this
                # readback bound to the canonical process-role contract.
                expected = dict(api_process_contract().required)
            else:
                expected = {
                    "TELEGRAM_DELIVERY_PRODUCER_MODE": "legacy",
                    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "legacy",
                    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "legacy",
                    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
                    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
                    "TELEGRAM_MULTI_PUBLISHER_ENABLED": "false",
                    "TELEGRAM_B2B_DISPATCH_ENABLED": "false",
                    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED": "false",
                    **{
                        f"TELEGRAM_PUBLISHER_{index}_ENABLED": "false"
                        for index in range(1, 6)
                    },
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


def _write_secure_bytes(artifact_dir: Path, prefix: str, payload: bytes) -> Path:
    secure_dir = _ensure_secure_artifact_dir(artifact_dir)
    descriptor, name = tempfile.mkstemp(
        prefix=f"{prefix}-", suffix=".json", dir=secure_dir
    )
    path = Path(name)
    os.fchmod(descriptor, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
        directory_fd = os.open(secure_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    if scan_paths([path]).get("status") != "clean":
        path.unlink(missing_ok=True)
        raise ProductionCutoverError("RECEIPT_REDACTION_FAILED")
    return path


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


def _descriptor_lock_binding(descriptor: int) -> dict[str, object]:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_LOCKS_REQUIRED") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_LOCKS_REQUIRED")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_LOCKS_REQUIRED") from exc
    try:
        descriptor_path = Path(
            os.readlink(f"/proc/self/fd/{descriptor}")
        ).resolve(strict=True)
        path_metadata = descriptor_path.lstat()
    except (OSError, RuntimeError):
        raise ProductionCutoverError(
            "BLOCKED_DEPLOY_FENCE_LOCKS_REQUIRED"
        ) from None
    if (
        not descriptor_path.is_absolute()
        or descriptor_path.is_symlink()
        or not stat.S_ISREG(path_metadata.st_mode)
        or (path_metadata.st_dev, path_metadata.st_ino)
        != (metadata.st_dev, metadata.st_ino)
    ):
        raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_LOCKS_REQUIRED")
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "path_sha256": hashlib.sha256(
            str(descriptor_path).encode("utf-8")
        ).hexdigest(),
    }


def _deploy_command_sha256(command: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(command, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _prepare_deploy_child_fence(
    *,
    authority_path: Path,
    authority_digest: str,
    manifest: Path,
    command: list[str],
    run_lock_descriptor: int,
    source_lock_descriptor: int,
    deploy_script_sha256: str,
) -> tuple[Path, str]:
    artifact_dir = _ensure_secure_artifact_dir(authority_path.parent)
    _require_secure_authority_file(
        authority_path, artifact_dir, prefix="production-queue-deploy-authority-"
    )
    authority = _read_json_evidence(authority_path, authority_digest)
    state_file = str(authority.get("state_file") or "")
    journal_file = str(authority.get("journal_file") or "")
    if (
        Path(state_file).name != state_file
        or Path(journal_file).name != journal_file
        or not re.fullmatch(r"[0-9a-f]{64}", deploy_script_sha256)
    ):
        raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_BINDING")
    state_path = artifact_dir / state_file
    _require_secure_authority_file(
        state_path, artifact_dir, prefix="production-queue-deploy-state-"
    )
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_BINDING") from None
    if (
        not isinstance(state, dict)
        or state.get("status") != "issued"
        or state.get("authority_file") != authority_path.name
        or state.get("authority_sha256") != authority_digest
        or state.get("journal_file") != journal_file
        or state.get("deploy_fence_file") is not None
    ):
        raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_BINDING")
    payload = {
        "schema": "production_deploy_child_fence/1.0",
        "status": "PREPARED",
        "created_at_utc": _utc_now(),
        "authority_file": authority_path.name,
        "authority_sha256": authority_digest,
        "journal_file": journal_file,
        "git_head": authority.get("git_head"),
        "source_sha256": authority.get("source_sha256"),
        "manifest_sha256": _sha256(manifest),
        "private_primary_required": (
            authority.get("private_primary_manifest_attestation") is not None
        ),
        "command_sha256": _deploy_command_sha256(command),
        "deploy_script_sha256": deploy_script_sha256,
        "run_lock": _descriptor_lock_binding(run_lock_descriptor),
        "source_lock": _descriptor_lock_binding(source_lock_descriptor),
        "secrets_disclosed": False,
    }
    fence_path, fence_digest = _write_secure_json(
        artifact_dir, "production-deploy-child-fence", payload
    )
    state["deploy_fence_file"] = fence_path.name
    state["deploy_fence_sha256"] = fence_digest
    _atomic_write(
        state_path,
        (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        ),
    )
    return fence_path, fence_digest


def _read_deploy_child_fence(
    path: Path,
    *,
    authority_digest: str,
    expected_command: list[str] | None = None,
) -> dict[str, Any]:
    artifact_dir = _ensure_secure_artifact_dir(path.parent)
    _require_secure_authority_file(
        path, artifact_dir, prefix="production-deploy-child-fence-"
    )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_BINDING") from None
    if (
        not isinstance(value, dict)
        or value.get("schema") != "production_deploy_child_fence/1.0"
        or value.get("authority_sha256") != authority_digest
        or value.get("status")
        not in {"PREPARED", "RUNNING", "SUCCEEDED", "FAILED", "SUPERVISOR_FAILED"}
        or value.get("secrets_disclosed") is not False
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(value.get("deploy_script_sha256") or "")
        )
        or (
            expected_command is not None
            and value.get("command_sha256")
            != _deploy_command_sha256(expected_command)
        )
    ):
        raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_BINDING")
    return value


def reconcile_deploy_child_fence(
    *,
    artifact_dir: Path,
    journal_path: Path,
    expected_source_sha256: str,
    expected_manifest_sha256: str,
) -> dict[str, object] | None:
    """Reconcile the last exact deploy child after controller interruption."""

    secure_dir = _ensure_secure_artifact_dir(artifact_dir)
    candidates: list[tuple[int, Path, dict[str, Any]]] = []
    for path in secure_dir.glob("production-deploy-child-fence-*.json"):
        _require_secure_authority_file(
            path, secure_dir, prefix="production-deploy-child-fence-"
        )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_BINDING") from None
        if (
            isinstance(value, dict)
            and value.get("journal_file") == journal_path.name
            and value.get("source_sha256") == expected_source_sha256
            and value.get("manifest_sha256") == expected_manifest_sha256
        ):
            candidates.append((path.stat().st_mtime_ns, path, value))
    if not candidates:
        return None
    _mtime, path, value = max(candidates, key=lambda item: item[0])
    authority_digest = str(value.get("authority_sha256") or "")
    value = _read_deploy_child_fence(
        path, authority_digest=authority_digest
    )
    status = str(value["status"])
    if status == "RUNNING":
        supervisor = value.get("supervisor")
        identity = None
        if isinstance(supervisor, Mapping):
            pid = supervisor.get("pid")
            if not isinstance(pid, bool) and isinstance(pid, int) and pid > 0:
                identity = _process_start_identity(pid)
        if (
            isinstance(supervisor, Mapping)
            and identity
            == (
                str(supervisor.get("boot_id") or ""),
                str(supervisor.get("start_ticks") or ""),
            )
        ):
            raise ProductionCutoverError("BLOCKED_DEPLOY_CHILD_ACTIVE")
        raise ProductionCutoverError("BLOCKED_DEPLOY_CHILD_RESULT_AMBIGUOUS")
    if status == "SUCCEEDED" and value.get("returncode") == 0:
        return {
            "status": "completed",
            "official_script": True,
            "output_retained": False,
            "reconciled_from_child_fence": True,
            "deploy_fence_file": path.name,
            "deploy_fence_sha256": _sha256(path),
            "product_readiness": value.get("product_readiness"),
        }
    if status in {"FAILED", "SUPERVISOR_FAILED", "PREPARED"}:
        return {"status": "retry_allowed", "reconciled_from_child_fence": True}
    raise ProductionCutoverError("BLOCKED_DEPLOY_FENCE_BINDING")


def create_deploy_authority(
    artifact_dir: Path,
    source: Path,
    binding: Mapping[str, str],
    *,
    run_lock: ExclusiveRunLock,
    journal: PhaseJournal,
    deploy_manifest: Path | None = None,
    private_primary_attestation: PrivatePrimaryDeployAttestation | None = None,
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
    if private_primary_attestation is not None and deploy_manifest is None:
        raise ProductionCutoverError("BLOCKED_PRIVATE_PRIMARY_ATTESTATION")
    attestation_binding = _private_primary_attestation_binding(
        deploy_manifest or source, private_primary_attestation
    )
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
        "private_primary_manifest_attestation": attestation_binding,
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
            "private_primary_manifest_attestation": attestation_binding,
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
    private_primary_attestation: PrivatePrimaryDeployAttestation | None = None,
) -> dict[str, Any]:
    artifact_dir = _ensure_secure_artifact_dir(expected_artifact_dir)
    _require_secure_authority_file(
        authority_path, artifact_dir, prefix="production-queue-deploy-authority-"
    )
    payload = _read_json_evidence(authority_path, authority_digest)
    age = (datetime.now(timezone.utc) - _parse_timestamp(payload.get("created_at"))).total_seconds()
    source, _ = _immutable_source(manifest)
    expected_attestation_binding = _private_primary_attestation_binding(
        manifest, private_primary_attestation
    )
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
        or payload.get("private_primary_manifest_attestation")
        != expected_attestation_binding
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
        or state.get("private_primary_manifest_attestation")
        != expected_attestation_binding
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
        "private_primary_manifest_attestation_bound": (
            expected_attestation_binding is not None
        ),
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


def _static_redeploy_gate(
    manifest: Path,
    staging_env: Path,
    *,
    backup_receipt: Path | None = None,
    backup_digest: str = "",
) -> tuple[Path, dict[str, str], dict[str, str]]:
    """Fail closed before constructing an operations object for redeploy."""

    source, manifest_values = _immutable_source(manifest)
    if not staging_env.is_file():
        raise ProductionCutoverError("BLOCKED_STAGING_COLLISION_EVIDENCE")
    source_values = parse_env_file(source)
    credentials, _ = credential_status(source_values, parse_env_file(staging_env))
    if credentials["blockers"]:
        raise ProductionCutoverError(str(credentials["status"]))
    if source_profile(source_values) != "queue-v1" or not _profile(source_values)[
        "ready"
    ]:
        raise ProductionCutoverError("BLOCKED_SOURCE_NOT_QUEUE_V1")
    validate_official_release_profile(manifest_values)
    if backup_receipt is not None:
        configured_path = Path(
            str(manifest_values.get("PRODUCTION_BACKUP_RECEIPT_PATH") or "")
        ).expanduser()
        configured_digest = str(
            manifest_values.get("PRODUCTION_BACKUP_RECEIPT_SHA256") or ""
        ).strip()
        if (
            not backup_receipt.is_absolute()
            or not configured_path.is_absolute()
            or configured_path.resolve(strict=False)
            != backup_receipt.resolve(strict=False)
            or configured_digest != backup_digest
        ):
            raise ProductionCutoverError("BLOCKED_REDEPLOY_BACKUP_MANIFEST_BINDING")
    return source, source_values, manifest_values


def run_redeploy_preflight(
    *,
    manifest: Path,
    staging_env: Path,
    backup_receipt: Path,
    backup_digest: str,
    operations_factory: Callable[[Path], ProductionOperations] = ProductionOperations,
    gateway: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read-only target-aware preflight for an active Queue-v1 production pair."""

    source, source_values, manifest_values = _static_redeploy_gate(
        manifest,
        staging_env,
        backup_receipt=backup_receipt,
        backup_digest=backup_digest,
    )
    binding = git_binding()
    if (
        binding["branch"] != "main"
        or binding["worktree"] != "clean"
        or binding["head"] != binding["origin_main"]
    ):
        raise ProductionCutoverError("BLOCKED_CLEAN_PUSHED_MAIN")
    credentials, identities = credential_status(
        source_values, parse_env_file(staging_env)
    )
    ops = operations_factory(manifest)
    inventory = ops.executor_inventory()
    _assert_inventory(inventory, count=1, owner="queue-v1")
    expected_database_name = str(source_values.get("POSTGRES_DB") or "").strip()
    runtime = ops.release_schema_inventory(expected_database_name)
    backup = _backup_status(
        backup_receipt,
        backup_digest,
        manifest_values=manifest_values,
        expected_release_sha=str(runtime["release_sha"]),
        expected_database_name=expected_database_name,
        expected_database_identities=runtime["database_identity_sha256"],
        expected_schema_head=str(runtime["schema_head"]),
    )
    provider_gateway = gateway if gateway is not None else None
    provider = (
        provider_preflight(source_values, identities)
        if provider_gateway is None
        else provider_preflight(source_values, identities, gateway=provider_gateway)
    )
    staging_values = parse_env_file(staging_env)
    staging_identities, staging_missing = _identities(staging_values)
    if staging_missing or len(staging_identities) != 6:
        raise ProductionCutoverError("BLOCKED_STAGING_COLLISION_EVIDENCE")
    staging_provider = (
        provider_preflight(staging_values, staging_identities)
        if provider_gateway is None
        else provider_preflight(
            staging_values, staging_identities, gateway=provider_gateway
        )
    )
    provider["staging"] = staging_provider
    provider["staging_identity_count"] = staging_provider["identity_count"]
    provider["read_only_provider_call_count"] += staging_provider[
        "read_only_provider_call_count"
    ]
    health = ops.queue_health(expected_database_name)
    role_contract = ops.runtime_contract(source_values, expected_owner="queue-v1")
    return {
        "schema_version": 1,
        "environment": "production",
        "mode": "read-only",
        "status": "READY_FOR_QUEUE_V1_REDEPLOY",
        "observed_at": _utc_now(),
        "git": binding,
        "source_profile": "queue-v1",
        "source_sha256": _sha256(source),
        "credentials": credentials,
        "backup": backup,
        "current_runtime": runtime,
        "executor_inventory": inventory,
        "queue_health": health,
        "runtime_role_contract": role_contract,
        "provider": provider,
        "apply_supported": False,
        "provider_mutations": 0,
    }


def verify_redeploy_preflight_evidence(
    path: Path,
    digest: str,
    *,
    backup_digest: str,
    source_digest: str,
    binding: Mapping[str, str],
) -> dict[str, Any]:
    payload = _read_json_evidence(path, digest)
    age = (
        datetime.now(timezone.utc) - _parse_timestamp(payload.get("observed_at"))
    ).total_seconds()
    provider = payload.get("provider") or {}
    runtime = payload.get("current_runtime") or {}
    inventory = payload.get("executor_inventory") or {}
    health = payload.get("queue_health") or {}
    role_contract = payload.get("runtime_role_contract") or {}
    if (
        age < -300
        or age > PREFLIGHT_MAXIMUM_AGE_SECONDS
        or payload.get("environment") != "production"
        or payload.get("mode") != "read-only"
        or payload.get("status") != "READY_FOR_QUEUE_V1_REDEPLOY"
        or payload.get("apply_supported") is not False
        or payload.get("source_profile") != "queue-v1"
        or payload.get("source_sha256") != source_digest
        or payload.get("git") != dict(binding)
        or (payload.get("credentials") or {}).get("status") != "ready"
        or (payload.get("credentials") or {}).get("identity_count") != 6
        or (payload.get("credentials") or {}).get("publisher_count") != 5
        or (payload.get("backup") or {}).get("status") != "verified"
        or (payload.get("backup") or {}).get("digest") != backup_digest
        or runtime.get("status") != "verified"
        or not re.fullmatch(r"[0-9a-f]{40}", str(runtime.get("release_sha") or ""))
        or not re.fullmatch(r"[0-9a-z]{12}", str(runtime.get("schema_head") or ""))
        or inventory.get("count") != 1
        or inventory.get("owner") != "queue-v1"
        or inventory.get("overlap") is not False
        or health.get("status") != "passed"
        or health.get("decision") != "continue"
        or role_contract.get("status") != "verified"
        or role_contract.get("owner") != "queue-v1"
        or provider.get("status") != "approved"
        or provider.get("identity_count") != 6
        or provider.get("staging_identity_count") != 6
    ):
        raise ProductionCutoverError("BLOCKED_REDEPLOY_PREFLIGHT_CONTRACT")
    return {
        "status": "verified",
        "preflight_sha256": digest,
        "backup_sha256": backup_digest,
        "current_release_sha": runtime["release_sha"],
        "target_release_sha": binding["head"],
        "fresh": True,
        "secrets_disclosed": False,
    }


def _recover_interrupted_phase(
    *,
    command: str,
    manifest: Path,
    artifact_dir: Path,
    binding: Mapping[str, str],
    operations_factory: Callable[[Path], ProductionOperations],
    recovery_backup_dir: Path | None = None,
    private_primary_attestation: PrivatePrimaryDeployAttestation | None = None,
    market_handoff_dir: Path = DEFAULT_MARKET_HANDOFF_DIR,
    drain_timeout_seconds: int = 300,
    drain_poll_seconds: float = 2.0,
) -> dict[str, Any] | None:
    """Inspect and close one exact nonterminal Queue phase idempotently.

    A killed process may leave the durable phase journal and the release lock
    behind at any mutation boundary.  Recovery always proves the old command's
    pre-state from its source snapshot and current live ownership.  It never
    guesses from a journal phase name alone.
    """

    pending = _pending_phase_journals(artifact_dir, command=command)
    if not pending:
        return None
    path, original_payload = pending[0]
    if (
        original_payload.get("git_head") != binding.get("head")
        or binding.get("branch") != "main"
        or binding.get("worktree") != "clean"
        or binding.get("head") != binding.get("origin_main")
    ):
        raise ProductionCutoverError("BLOCKED_INTERRUPTED_RECOVERY_BINDING")
    try:
        source, _manifest_values = _immutable_source(manifest)
    except ReadinessBlocked as exc:
        raise ProductionCutoverError(exc.code) from None
    expected_source_digest = str(original_payload.get("source_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_source_digest):
        raise ProductionCutoverError("BLOCKED_INTERRUPTED_RECOVERY_BINDING")
    desired_owner = "legacy" if command == "apply" else "queue-v1"
    snapshot: SecureSourceBackup | None = None
    if recovery_backup_dir is not None:
        snapshot = _load_recovery_source_snapshot(
            original_payload, recovery_backup_dir
        )
        if snapshot.sha256 != expected_source_digest:
            raise ProductionCutoverError("BLOCKED_INTERRUPTED_RECOVERY_SOURCE")
    elif command != "redeploy":
        raise ProductionCutoverError("BLOCKED_INTERRUPTED_RECOVERY_SOURCE")

    ops = operations_factory(manifest)
    run_lock = ExclusiveRunLock(artifact_dir)
    if command == "redeploy" and original_payload.get("status") == "recovery_failed":
        run_lock.acquire_for_queue_redeploy(
            handoff_dir=market_handoff_dir,
            allow_recovery_journal=path,
        )
    elif command == "redeploy":
        run_lock.acquire_for_queue_redeploy(
            handoff_dir=market_handoff_dir,
            allow_interrupted_journal=path,
        )
    elif original_payload.get("status") == "recovery_failed":
        run_lock.acquire(allow_recovery_journal=path)
    else:
        run_lock.acquire(allow_interrupted_journal=path)
    try:
        recovered_payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _release_queue_redeploy_run_lock(run_lock)
        raise ProductionCutoverError("BLOCKED_PENDING_PHASE_JOURNAL") from None
    if recovered_payload.get("status") in PHASE_TERMINAL_STATES:
        _release_queue_redeploy_run_lock(run_lock)
        return {
            "status": recovered_payload.get("status"),
            "terminal_receipt_recovered": True,
            "receipt_file": recovered_payload.get("receipt_file"),
            "receipt_sha256": recovered_payload.get("receipt_sha256"),
            "desired_owner": desired_owner,
        }
    source_lock = ImmutableSourceLock(source)
    try:
        source_lock.acquire()
        journal = PhaseJournal.adopt(path, run_lock=run_lock)
    except BaseException:
        source_lock.release()
        _release_queue_redeploy_run_lock(run_lock)
        raise

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "environment": "production",
        "command": f"recover-interrupted-{command}",
        "status": "running",
        "started_at": _utc_now(),
        "git": dict(binding),
        "interrupted_phase": original_payload.get("status"),
        "journal_file": path.name,
        "source_sha256_expected": expected_source_digest,
        "desired_owner": desired_owner,
        "steps": [],
        "synthetic_customer_mutations": 0,
        "secrets_disclosed": False,
    }
    try:
        observed_source_digest = _sha256(source)
        observed = ops.executor_inventory()
        already_safe = (
            observed_source_digest == expected_source_digest
            and source_profile(parse_env_file(source)) == desired_owner
            and observed.get("count") == 1
            and observed.get("owner") == desired_owner
            and observed.get("overlap") is False
        )
        if already_safe:
            receipt["steps"].append(
                {"name": "live_state_already_safe", "executor": observed}
            )
        else:
            journal.update(
                "interrupted_recovery_producers_quiescing",
                interruption_recovery_required=True,
            )
            stopped = ops.stop_producers()
            receipt["steps"].append(
                {"name": "quiesce", "stopped_count": len(stopped)}
            )
            journal.update("interrupted_recovery_drain_waiting")
            receipt["steps"].append(
                {
                    "name": "drain",
                    "report": ops.wait_for_drain(
                        drain_timeout_seconds, drain_poll_seconds
                    ),
                }
            )
            journal.update("interrupted_recovery_executor_stopping")
            ops.stop_bot()
            zero = ops.executor_inventory()
            _assert_inventory(zero, count=0, owner=None)
            if observed_source_digest != expected_source_digest:
                if snapshot is None:
                    raise ProductionCutoverError(
                        "BLOCKED_INTERRUPTED_RECOVERY_SOURCE"
                    )
                journal.update(
                    "interrupted_recovery_source_restoring",
                    source_after_sha256=expected_source_digest,
                )
                _atomic_write(source, snapshot.original)
                _require_source_digest(source, expected_source_digest)
                receipt["steps"].append(
                    {
                        "name": "restore_pre_command_source",
                        "source_sha256": expected_source_digest,
                    }
                )
            if source_profile(parse_env_file(source)) != desired_owner:
                raise ProductionCutoverError(
                    "BLOCKED_INTERRUPTED_RECOVERY_SOURCE"
                )
            journal.update(
                "interrupted_recovery_deploy_authorizing",
                desired_owner=desired_owner,
            )
            authority, authority_digest = create_deploy_authority(
                artifact_dir,
                source,
                binding,
                run_lock=run_lock,
                journal=journal,
                deploy_manifest=(
                    manifest if private_primary_attestation is not None else None
                ),
                private_primary_attestation=private_primary_attestation,
            )
            deploy = (
                ops.deploy_official(
                    authority,
                    authority_digest,
                    inherited_lock_descriptors=(
                        int(run_lock.descriptor), int(source_lock.descriptor)
                    ),
                )
                if private_primary_attestation is None
                else ops.deploy_official(
                    authority,
                    authority_digest,
                    private_primary_attestation=private_primary_attestation,
                    inherited_lock_descriptors=(
                        int(run_lock.descriptor), int(source_lock.descriptor)
                    ),
                )
            )
            receipt["steps"].append({"name": "deploy_safe_owner", "report": deploy})
            observed = ops.executor_inventory()
            _assert_inventory(observed, count=1, owner=desired_owner)

        receipt["steps"].append(
            {
                "name": "runtime_role_contract",
                "report": ops.runtime_contract(
                    parse_env_file(source), expected_owner=desired_owner
                ),
            }
        )
        if desired_owner == "queue-v1":
            values = parse_env_file(source)
            receipt["steps"].append(
                {
                    "name": "queue_health",
                    "report": ops.queue_health(
                        str(values.get("POSTGRES_DB") or "")
                    ),
                }
            )
        receipt["status"] = "interrupted_recovered"
        receipt["finished_at"] = _utc_now()
        receipt_path, digest = _commit_terminal_receipt(
            journal,
            artifact_dir,
            prefix=f"production-queue-{command}-interruption-recovery",
            receipt=receipt,
            terminal_status="interrupted_recovered",
            recovered_from_status=original_payload.get("status"),
        )
        return {
            "status": "interrupted_recovered",
            "receipt_file": receipt_path.name,
            "receipt_sha256": digest,
            "desired_owner": desired_owner,
        }
    except BaseException as exc:
        code = (
            exc.code
            if isinstance(exc, ProductionCutoverError)
            else "INTERRUPTED_RECOVERY_FAILED"
        )
        receipt["status"] = "recovery_failed"
        receipt["error_code"] = code
        receipt["finished_at"] = _utc_now()
        try:
            _failed_path, digest = _commit_terminal_receipt(
                journal,
                artifact_dir,
                prefix=f"production-queue-{command}-interruption-recovery-failed",
                receipt=receipt,
                terminal_status="recovery_failed",
                error_code=code,
                interruption_recovery_required=True,
            )
        except BaseException:
            # The durable journal remains nonterminal and is therefore the
            # sole exact authority for the next explicit recovery attempt.
            raise ProductionCutoverError(code) from None
        raise ProductionCutoverError(code, receipt_sha256=digest) from None
    finally:
        source_lock.release()
        _release_queue_redeploy_run_lock(run_lock)


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
    if (
        operations_factory is ProductionOperations
        and artifact_dir.resolve(strict=False) != DEFAULT_ARTIFACT_DIR
    ):
        raise ProductionCutoverError("BLOCKED_PRODUCTION_ARTIFACT_DIRECTORY")
    if _pending_phase_journals(artifact_dir, command="apply"):
        binding = git_binding()
        interrupted = _recover_interrupted_phase(
            command="apply",
            manifest=manifest,
            artifact_dir=artifact_dir,
            binding=binding,
            operations_factory=operations_factory,
            recovery_backup_dir=secure_backup_dir,
            drain_timeout_seconds=drain_timeout_seconds,
            drain_poll_seconds=drain_poll_seconds,
        )
        if interrupted and interrupted.get("status") == "applied":
            return {**interrupted, "secrets_disclosed": False}
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
    ops = operations_factory(manifest)
    run_lock = ExclusiveRunLock(artifact_dir)
    run_lock.acquire()
    source_lock = ImmutableSourceLock(source)
    try:
        source_lock.acquire()
        recovery_source_backup = _create_recovery_source_snapshot(
            source, secure_backup_dir, expected_sha256=source_digest
        )
        journal = PhaseJournal(
            artifact_dir,
            command="apply",
            source_sha256=source_digest,
            git_head=binding["head"],
            run_lock=run_lock,
            recovery_source_backup=recovery_source_backup,
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
    mutation_started = False
    source_backup: SecureSourceBackup | None = None
    source_after_digest: str | None = None
    try:
        initial = ops.executor_inventory()
        _assert_inventory(initial, count=1, owner="legacy")
        receipt["executor_timeline"].append(initial)
        _require_source_digest(source, source_digest)
        journal.update("producers_quiescing")
        stopped = ops.stop_producers()
        mutation_started = bool(stopped)
        journal.update("producers_quiesced", stopped_count=len(stopped))
        receipt["steps"].append({"name": "quiesce_both_producer_hosts", "stopped_count": len(stopped)})
        journal.update("drain_waiting")
        drained = ops.wait_for_drain(drain_timeout_seconds, drain_poll_seconds)
        journal.update("drained")
        receipt["steps"].append({"name": "drain", "report": drained})
        journal.update("executor_stopping")
        ops.stop_bot()
        zero = ops.executor_inventory()
        _assert_inventory(zero, count=0, owner=None)
        journal.update("zero_executor")
        receipt["executor_timeline"].append(zero)
        journal.update("source_switch_authorizing")
        source_backup = recovery_source_backup
        source_report = _update_source_from_recovery_snapshot(
            source,
            source_backup,
            _queue_source_updates(source_values),
            expected_source_sha256=source_digest,
        )
        receipt["steps"].append({"name": "atomic_source_switch", "report": source_report})
        source_after_digest = str(source_report["source_after_sha256"])
        journal.update(
            "source_switched",
            source_after_sha256=source_report["source_after_sha256"],
            source_backup_sha256=source_backup.sha256,
        )
        journal.update("deploy_authorizing", desired_owner="queue-v1")
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
                "report": ops.deploy_official(
                    authority_path,
                    authority_digest,
                    inherited_lock_descriptors=(
                        int(run_lock.descriptor), int(source_lock.descriptor)
                    ),
                ),
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
        receipt_path, digest = _commit_terminal_receipt(
            journal,
            artifact_dir,
            prefix="production-queue-cutover",
            receipt=receipt,
            terminal_status="applied",
        )
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
            if not mutation_started and source_backup is None:
                recovery = {
                    "attempted": False,
                    "status": "not_required_before_mutation",
                }
            elif source_backup is not None:
                # A post-deploy failure may have restarted Queue producers and
                # the Queue executor.  Re-enter the same guarded choreography;
                # never restore Legacy underneath a live Queue executor.
                journal.update("recovery_producers_quiescing")
                recovery_stopped = ops.stop_producers()
                recovery["quiesce"] = {"stopped_count": len(recovery_stopped)}
                journal.update("recovery_drain_waiting")
                recovery["drain"] = ops.wait_for_drain(
                    drain_timeout_seconds, drain_poll_seconds
                )
                journal.update("recovery_executor_stopping")
                ops.stop_bot()
                recovered_zero = ops.executor_inventory()
                _assert_inventory(recovered_zero, count=0, owner=None)
                journal.update("recovery_source_restoring")
                recovery["source"] = restore_source_from_backup(
                    source,
                    source_backup,
                    expected_current_sha256=source_after_digest,
                )
                journal.update(
                    "recovery_source_switched",
                    source_after_sha256=_sha256(source),
                )
                journal.update("recovery_deploy_authorizing", desired_owner="legacy")
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
                    legacy_authority_path,
                    legacy_authority_digest,
                    inherited_lock_descriptors=(
                        int(run_lock.descriptor), int(source_lock.descriptor)
                    ),
                )
                recovered_inventory = ops.executor_inventory()
                _assert_inventory(recovered_inventory, count=1, owner="legacy")
                recovery["runtime"] = ops.runtime_contract(
                    parse_env_file(source), expected_owner="legacy"
                )
                recovery["status"] = "restored_legacy"
            else:
                observed_executor = ops.executor_inventory()
                if observed_executor.get("count") == 0:
                    journal.update("recovery_executor_starting")
                    ops.start_bot()
                else:
                    _assert_inventory(
                        observed_executor, count=1, owner="legacy"
                    )
                journal.update("recovery_producers_resuming")
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
        terminal_status = (
            "failed_recovered"
            if recovery["status"]
            in {"restored_legacy", "not_required_before_mutation"}
            else "recovery_failed"
        )
        _failed_path, receipt_digest = _commit_terminal_receipt(
            journal,
            artifact_dir,
            prefix="production-queue-cutover-failed",
            receipt=receipt,
            terminal_status=terminal_status,
            error_code=code,
        )
        source_lock.release()
        run_lock.release()
        raise ProductionCutoverError(code, receipt_sha256=receipt_digest) from None


def redeploy_queue_v1(
    *,
    manifest: Path,
    staging_env: Path,
    preflight_report: Path,
    preflight_digest: str,
    backup_receipt: Path,
    backup_digest: str,
    artifact_dir: Path,
    confirmation: str,
    private_primary_attestation: PrivatePrimaryDeployAttestation | None = None,
    operations_factory: Callable[[Path], ProductionOperations] = ProductionOperations,
    preflight_runner: Callable[..., dict[str, Any]] = run_redeploy_preflight,
    market_handoff_dir: Path = DEFAULT_MARKET_HANDOFF_DIR,
) -> dict[str, Any]:
    """Redeploy a newer official release while Queue-v1 remains the owner.

    Unlike the initial cutover, this path never changes the immutable Queue
    profile and never creates a Legacy/Queue ownership transition.  The
    ordinary two-host release choreography owns writer quiescence, schema
    convergence, and exact-image replacement.  A fresh target-aware preflight
    binds the current live release to the backup while Git binds the target
    release, so a real forward code release is supported rather than merely a
    same-SHA restart.
    """

    if confirmation != REDEPLOY_CONFIRMATION:
        raise ProductionCutoverError("REDEPLOY_CONFIRMATION_MISMATCH")
    if (
        operations_factory is ProductionOperations
        and artifact_dir.resolve(strict=False) != DEFAULT_ARTIFACT_DIR
    ):
        raise ProductionCutoverError("BLOCKED_PRODUCTION_ARTIFACT_DIRECTORY")
    source, source_values, _manifest_values = _static_redeploy_gate(
        manifest,
        staging_env,
        backup_receipt=backup_receipt,
        backup_digest=backup_digest,
    )
    private_primary_required = (
        str(
            source_values.get("PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE")
            or "LEGACY"
        ).strip().upper()
        == "PRIVATE_PRIMARY"
    )
    if private_primary_required != (private_primary_attestation is not None):
        raise ProductionCutoverError("BLOCKED_PRIVATE_PRIMARY_ATTESTATION")
    attestation_binding = _private_primary_attestation_binding(
        manifest, private_primary_attestation
    )
    source_digest = _sha256(source)
    binding = git_binding()
    if (
        binding["branch"] != "main"
        or binding["worktree"] != "clean"
        or binding["head"] != binding["origin_main"]
    ):
        raise ProductionCutoverError("BLOCKED_CLEAN_PUSHED_MAIN")
    interrupted = _recover_interrupted_phase(
        command="redeploy",
        manifest=manifest,
        artifact_dir=artifact_dir,
        binding=binding,
        operations_factory=operations_factory,
        private_primary_attestation=private_primary_attestation,
        market_handoff_dir=market_handoff_dir,
    )
    if interrupted and interrupted.get("status") == "redeployed":
        return {**interrupted, "source_profile": "queue-v1", "secrets_disclosed": False}
    evidence = verify_redeploy_preflight_evidence(
        preflight_report,
        preflight_digest,
        backup_digest=backup_digest,
        source_digest=source_digest,
        binding=binding,
    )
    try:
        live_preflight = preflight_runner(
            manifest=manifest,
            staging_env=staging_env,
            backup_receipt=backup_receipt,
            backup_digest=backup_digest,
            operations_factory=operations_factory,
        )
    except (ProductionCutoverError, ReadinessBlocked) as exc:
        raise ProductionCutoverError(getattr(exc, "code", str(exc))) from None
    if (
        live_preflight.get("status") != "READY_FOR_QUEUE_V1_REDEPLOY"
        or live_preflight.get("source_sha256") != source_digest
        or live_preflight.get("git") != binding
        or _sha256(source) != source_digest
    ):
        raise ProductionCutoverError("BLOCKED_LIVE_REDEPLOY_PREFLIGHT")
    ops = operations_factory(manifest)
    run_lock = ExclusiveRunLock(artifact_dir)
    run_lock.acquire_for_queue_redeploy(handoff_dir=market_handoff_dir)
    source_lock = ImmutableSourceLock(source)
    try:
        source_lock.acquire()
        journal = PhaseJournal(
            artifact_dir,
            command="redeploy",
            source_sha256=source_digest,
            git_head=binding["head"],
            run_lock=run_lock,
        )
    except BaseException:
        source_lock.release()
        _release_queue_redeploy_run_lock(run_lock)
        raise
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "environment": "production",
        "command": "redeploy",
        "started_at": _utc_now(),
        "status": "running",
        "git": binding,
        "preflight": evidence,
        "steps": [],
        "executor_timeline": [],
        "source_profile_changed": False,
        "private_primary_manifest_attestation": attestation_binding,
        "synthetic_customer_mutations": 0,
        "secrets_disclosed": False,
    }
    deploy_started = False
    try:
        initial = ops.executor_inventory()
        _assert_inventory(initial, count=1, owner="queue-v1")
        receipt["executor_timeline"].append(initial)
        _require_source_digest(source, source_digest)
        journal.update(
            "official_redeploy_authorizing",
            private_primary_manifest_attestation=attestation_binding,
        )
        authority_path, authority_digest = create_deploy_authority(
            artifact_dir,
            source,
            binding,
            run_lock=run_lock,
            journal=journal,
            deploy_manifest=manifest,
            private_primary_attestation=private_primary_attestation,
        )
        deploy_started = True
        deploy_report = (
            ops.deploy_official(
                authority_path,
                authority_digest,
                inherited_lock_descriptors=(
                    int(run_lock.descriptor), int(source_lock.descriptor)
                ),
            )
            if private_primary_attestation is None
            else ops.deploy_official(
                authority_path,
                authority_digest,
                private_primary_attestation=private_primary_attestation,
                inherited_lock_descriptors=(
                    int(run_lock.descriptor), int(source_lock.descriptor)
                ),
            )
        )
        receipt["steps"].append(
            {
                "name": "official_two_host_redeploy",
                "report": deploy_report,
            }
        )
        journal.update("redeployed_runtime")
        _require_source_digest(source, source_digest)
        final_inventory = ops.executor_inventory()
        _assert_inventory(final_inventory, count=1, owner="queue-v1")
        receipt["executor_timeline"].append(final_inventory)
        queue_values = parse_env_file(source)
        receipt["steps"].append(
            {
                "name": "runtime_role_contract",
                "report": ops.runtime_contract(
                    queue_values, expected_owner="queue-v1"
                ),
            }
        )
        receipt["steps"].append(
            {
                "name": "queue_health",
                "report": ops.queue_health(
                    str(queue_values.get("POSTGRES_DB") or "")
                ),
            }
        )
        receipt["steps"].append(
            {
                "name": "b2b_lane_read_only_probe",
                "report": ops.b2b_lane_probe(),
            }
        )
        receipt["status"] = "redeployed"
        receipt["finished_at"] = _utc_now()
        receipt_path, digest = _commit_terminal_receipt(
            journal,
            artifact_dir,
            prefix="production-queue-redeploy",
            receipt=receipt,
            terminal_status="redeployed",
        )
        source_lock.release()
        _release_queue_redeploy_run_lock(run_lock)
        return {
            "status": "redeployed",
            "receipt_file": receipt_path.name,
            "receipt_sha256": digest,
            "source_profile": "queue-v1",
            "secrets_disclosed": False,
        }
    except BaseException as exc:
        code = (
            exc.code
            if isinstance(exc, ProductionCutoverError)
            else "UNEXPECTED_REDEPLOY_FAILURE"
        )
        recovery: dict[str, Any] = {
            "attempted": bool(deploy_started),
            "strategy": "exact_target_forward_reconcile",
            "status": "not_required" if not deploy_started else "failed",
        }
        if deploy_started:
            try:
                if _sha256(source) != source_digest:
                    raise ProductionCutoverError("BLOCKED_SOURCE_DRIFT")
                journal.update("redeploy_forward_reconcile_authorizing")
                recovery_authority, recovery_authority_digest = (
                    create_deploy_authority(
                        artifact_dir,
                        source,
                        binding,
                        run_lock=run_lock,
                        journal=journal,
                        deploy_manifest=manifest,
                        private_primary_attestation=private_primary_attestation,
                    )
                )
                recovery["deploy"] = (
                    ops.deploy_official(
                        recovery_authority,
                        recovery_authority_digest,
                        inherited_lock_descriptors=(
                            int(run_lock.descriptor), int(source_lock.descriptor)
                        ),
                    )
                    if private_primary_attestation is None
                    else ops.deploy_official(
                        recovery_authority,
                        recovery_authority_digest,
                        private_primary_attestation=private_primary_attestation,
                        inherited_lock_descriptors=(
                            int(run_lock.descriptor), int(source_lock.descriptor)
                        ),
                    )
                )
                recovered = ops.executor_inventory()
                _assert_inventory(recovered, count=1, owner="queue-v1")
                queue_values = parse_env_file(source)
                recovery["runtime"] = ops.runtime_contract(
                    queue_values, expected_owner="queue-v1"
                )
                recovery["health"] = ops.queue_health(
                    str(queue_values.get("POSTGRES_DB") or "")
                )
                recovery["status"] = "queue_v1_forward_reconciled"
            except BaseException:
                recovery["status"] = "recovery_failed_fail_closed"
        receipt["status"] = "failed"
        receipt["error_code"] = code
        receipt["safe_recovery"] = recovery
        receipt["finished_at"] = _utc_now()
        terminal_status = (
            "failed_recovered"
            if recovery["status"] == "queue_v1_forward_reconciled"
            else "recovery_failed"
        )
        _failed_path, digest = _commit_terminal_receipt(
            journal,
            artifact_dir,
            prefix="production-queue-redeploy-failed",
            receipt=receipt,
            terminal_status=terminal_status,
            error_code=code,
        )
        source_lock.release()
        _release_queue_redeploy_run_lock(run_lock)
        raise ProductionCutoverError(code, receipt_sha256=digest) from None


def reconcile_failed_redeploy(
    *,
    manifest: Path,
    staging_env: Path,
    preflight_report: Path,
    preflight_digest: str,
    backup_receipt: Path,
    backup_digest: str,
    failed_receipt: Path,
    failed_digest: str,
    phase_journal: Path,
    phase_journal_digest: str,
    artifact_dir: Path,
    confirmation: str,
    preflight_runner: Callable[..., dict[str, Any]] = run_redeploy_preflight,
) -> dict[str, Any]:
    """Close only a contained redeploy failure after independent live proof.

    This command does not deploy, migrate, call Telegram, or modify product
    data.  It preserves the failed journal byte-for-byte and marks it terminal
    only while the production/source locks are held and a fresh Queue-v1
    redeploy preflight proves the old two-host release is healthy.
    """

    if confirmation != RECONCILE_REDEPLOY_CONFIRMATION:
        raise ProductionCutoverError("RECONCILE_CONFIRMATION_MISMATCH")
    if artifact_dir.resolve(strict=False) != DEFAULT_ARTIFACT_DIR:
        raise ProductionCutoverError("BLOCKED_PRODUCTION_ARTIFACT_DIRECTORY")
    source, _source_values, _manifest_values = _static_redeploy_gate(
        manifest,
        staging_env,
        backup_receipt=backup_receipt,
        backup_digest=backup_digest,
    )
    source_digest = _sha256(source)
    binding = git_binding()
    if (
        binding["branch"] != "main"
        or binding["worktree"] != "clean"
        or binding["head"] != binding["origin_main"]
    ):
        raise ProductionCutoverError("BLOCKED_CLEAN_PUSHED_MAIN")
    preflight = verify_redeploy_preflight_evidence(
        preflight_report,
        preflight_digest,
        backup_digest=backup_digest,
        source_digest=source_digest,
        binding=binding,
    )
    _require_secure_authority_file(
        phase_journal, artifact_dir, prefix="production-queue-phase-"
    )
    _require_secure_authority_file(
        failed_receipt,
        artifact_dir,
        prefix="production-queue-redeploy-failed-",
    )
    journal_bytes = phase_journal.read_bytes()
    if _sha256(phase_journal) != phase_journal_digest:
        raise ProductionCutoverError("BLOCKED_RECONCILIATION_BINDING")
    try:
        journal = json.loads(journal_bytes)
    except ValueError:
        raise ProductionCutoverError("BLOCKED_RECONCILIATION_BINDING") from None
    failed = _read_json_evidence(failed_receipt, failed_digest)
    failed_git = failed.get("git") or {}
    recovery = failed.get("safe_recovery") or {}
    if (
        journal.get("environment") != "production"
        or journal.get("command") != "redeploy"
        or journal.get("status") != "recovery_failed"
        or journal.get("source_sha256") != source_digest
        or journal.get("receipt_sha256") != failed_digest
        or journal.get("git_head") != failed_git.get("head")
        or failed.get("environment") != "production"
        or failed.get("command") != "redeploy"
        or failed.get("status") != "failed"
        or failed_git.get("branch") != "main"
        or failed_git.get("head") != failed_git.get("origin_main")
        or recovery.get("status") != "recovery_failed_fail_closed"
    ):
        raise ProductionCutoverError("BLOCKED_RECONCILIATION_BINDING")

    run_lock = ExclusiveRunLock(artifact_dir)
    source_lock = ImmutableSourceLock(source)
    run_lock.acquire(allow_recovery_journal=phase_journal)
    try:
        source_lock.acquire()
        live = preflight_runner(
            manifest=manifest,
            staging_env=staging_env,
            backup_receipt=backup_receipt,
            backup_digest=backup_digest,
        )
        if (
            live.get("status") != "READY_FOR_QUEUE_V1_REDEPLOY"
            or live.get("source_sha256") != source_digest
            or live.get("git") != binding
            or _sha256(source) != source_digest
        ):
            raise ProductionCutoverError("BLOCKED_LIVE_RECONCILIATION")
        original = _write_secure_bytes(
            artifact_dir,
            f"production-queue-recovery-original-{phase_journal.stem}",
            journal_bytes,
        )
        if _sha256(original) != phase_journal_digest:
            raise ProductionCutoverError("BLOCKED_JOURNAL_COPY_DIGEST")
        receipt = {
            "schema_version": 1,
            "environment": "production",
            "command": "reconcile-contained-redeploy-failure",
            "status": "old_release_pair_independently_verified_healthy",
            "reconciled_at": _utc_now(),
            "git": binding,
            "failed_git": failed_git,
            "source_sha256": source_digest,
            "failed_receipt_file": failed_receipt.name,
            "failed_receipt_sha256": failed_digest,
            "journal_file": phase_journal.name,
            "journal_sha256_before": phase_journal_digest,
            "original_journal_copy_file": original.name,
            "original_journal_copy_sha256": phase_journal_digest,
            "preflight_file": preflight_report.name,
            "preflight_sha256": preflight_digest,
            "preflight": preflight,
            "executor": live["executor_inventory"],
            "runtime": live["current_runtime"],
            "runtime_contract": live["runtime_role_contract"],
            "queue_health": live["queue_health"],
            "recovery_actions": [
                "preserved_original_phase_journal_bytes",
                "fresh_read_only_redeploy_preflight_verified",
                "old_release_pair_and_queue_v1_health_confirmed",
            ],
            "database_mutations": 0,
            "provider_mutations": 0,
            "customer_mutations": 0,
            "secrets_disclosed": False,
        }
        receipt_path, receipt_digest = _write_redacted_receipt(
            artifact_dir, "production-queue-recovery-reconciliation", receipt
        )
        journal["status"] = "failed_recovered"
        journal["updated_at"] = _utc_now()
        journal["reconciliation_receipt_file"] = receipt_path.name
        journal["reconciliation_receipt_sha256"] = receipt_digest
        journal["original_journal_sha256"] = phase_journal_digest
        rendered = (
            json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _atomic_write(phase_journal, rendered)
        if scan_paths([phase_journal]).get("status") != "clean":
            raise ProductionCutoverError("PHASE_JOURNAL_REDACTION_FAILED")
        return {
            "status": "failed_recovered",
            "receipt_file": receipt_path.name,
            "receipt_sha256": receipt_digest,
            "database_mutations": 0,
            "provider_mutations": 0,
            "secrets_disclosed": False,
        }
    finally:
        source_lock.release()
        run_lock.release()


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
    if (
        operations_factory is ProductionOperations
        and artifact_dir.resolve(strict=False) != DEFAULT_ARTIFACT_DIR
    ):
        raise ProductionCutoverError("BLOCKED_PRODUCTION_ARTIFACT_DIRECTORY")
    if _pending_phase_journals(artifact_dir, command="rollback"):
        binding = git_binding()
        interrupted = _recover_interrupted_phase(
            command="rollback",
            manifest=manifest,
            artifact_dir=artifact_dir,
            binding=binding,
            operations_factory=operations_factory,
            recovery_backup_dir=source_backup_path.parent,
        )
        if interrupted and interrupted.get("status") == "rolled_back":
            return {**interrupted, "schema_downgrade": False}
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
    ops = operations_factory(manifest)
    run_lock = ExclusiveRunLock(artifact_dir)
    run_lock.acquire()
    source_lock = ImmutableSourceLock(source)
    try:
        source_lock.acquire()
        recovery_source_backup = _create_recovery_source_snapshot(
            source,
            source_backup_path.parent,
            expected_sha256=current_source_digest,
        )
        journal = PhaseJournal(
            artifact_dir,
            command="rollback",
            source_sha256=current_source_digest,
            git_head=binding["head"],
            run_lock=run_lock,
            recovery_source_backup=recovery_source_backup,
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
        journal.update("producers_quiescing")
        stopped = ops.stop_producers()
        journal.update("producers_quiesced", stopped_count=len(stopped))
        receipt["steps"].append({"name": "quiesce_both_producer_hosts", "stopped_count": len(stopped)})
        journal.update("drain_waiting")
        receipt["steps"].append({"name": "drain", "report": ops.wait_for_drain(300, 2.0)})
        journal.update("drained")
        journal.update("executor_stopping")
        ops.stop_bot()
        zero = ops.executor_inventory()
        _assert_inventory(zero, count=0, owner=None)
        journal.update("zero_executor")
        receipt["executor_timeline"].append(zero)
        journal.update("source_switch_authorizing")
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
        journal.update("deploy_authorizing", desired_owner="legacy")
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
                    legacy_authority_path,
                    legacy_authority_digest,
                    inherited_lock_descriptors=(
                        int(run_lock.descriptor), int(source_lock.descriptor)
                    ),
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
        receipt_path, digest = _commit_terminal_receipt(
            journal,
            artifact_dir,
            prefix="production-queue-rollback",
            receipt=receipt,
            terminal_status="rolled_back",
        )
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
            journal.update("recovery_producers_quiescing")
            recovery_stopped = ops.stop_producers()
            journal.update("recovery_drain_waiting")
            ops.wait_for_drain(300, 2.0)
            journal.update("recovery_executor_stopping")
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
                journal.update("recovery_source_restoring")
                _atomic_write(source, current_source)
            journal.update(
                "recovery_source_switched",
                source_after_sha256=_sha256(source),
            )
            journal.update("recovery_deploy_authorizing", desired_owner="queue-v1")
            authority_path, authority_digest = create_deploy_authority(
                artifact_dir,
                source,
                binding,
                run_lock=run_lock,
                journal=journal,
            )
            ops.deploy_official(
                authority_path,
                authority_digest,
                inherited_lock_descriptors=(
                    int(run_lock.descriptor), int(source_lock.descriptor)
                ),
            )
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
        terminal_status = (
            "failed_recovered" if recovery == "queue_restored" else "recovery_failed"
        )
        _failed_path, digest = _commit_terminal_receipt(
            journal,
            artifact_dir,
            prefix="production-queue-rollback-failed",
            receipt=receipt,
            terminal_status=terminal_status,
            error_code=code,
        )
        source_lock.release()
        run_lock.release()
        raise ProductionCutoverError(code, receipt_sha256=digest) from None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "plan",
            "status",
            "preflight-redeploy",
            "apply",
            "redeploy",
            "reconcile-redeploy-failure",
            "rollback",
            "verify-deploy-authority",
        ),
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
    parser.add_argument("--failed-receipt", type=Path)
    parser.add_argument("--failed-receipt-sha256", default="")
    parser.add_argument("--phase-journal", type=Path)
    parser.add_argument("--phase-journal-sha256", default="")
    parser.add_argument("--deploy-authority", type=Path)
    parser.add_argument("--deploy-authority-sha256", default="")
    parser.add_argument("--private-primary-manifest-sha256", default="")
    parser.add_argument("--private-primary-manifest-receipt", type=Path)
    parser.add_argument(
        "--private-primary-manifest-receipt-sha256", default=""
    )
    parser.add_argument("--confirm", default="")
    return parser.parse_args(argv)


def private_primary_deploy_attestation_from_args(
    args: argparse.Namespace,
) -> PrivatePrimaryDeployAttestation | None:
    supplied = (
        bool(args.private_primary_manifest_sha256),
        args.private_primary_manifest_receipt is not None,
        bool(args.private_primary_manifest_receipt_sha256),
    )
    if any(supplied) and not all(supplied):
        raise ProductionCutoverError("BLOCKED_PRIVATE_PRIMARY_ATTESTATION")
    if not any(supplied):
        return None
    return bind_private_primary_deploy_attestation(
        args.manifest,
        manifest_sha256=args.private_primary_manifest_sha256,
        receipt_path=args.private_primary_manifest_receipt,
        receipt_sha256=args.private_primary_manifest_receipt_sha256,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        private_primary_attestation = private_primary_deploy_attestation_from_args(
            args
        )
        if (
            private_primary_attestation is not None
            and args.command not in {"redeploy", "verify-deploy-authority"}
        ):
            raise ProductionCutoverError("BLOCKED_PRIVATE_PRIMARY_ATTESTATION")
        if args.command == "plan":
            payload = {
                "environment": "production",
                "mode": "guarded",
                "apply_confirmation": APPLY_CONFIRMATION,
                "redeploy_confirmation": REDEPLOY_CONFIRMATION,
                "rollback_confirmation": ROLLBACK_CONFIRMATION,
                "synthetic_customer_mutations": 0,
                "sequence": ["preflight", "quiesce", "drain", "legacy->zero->queue", "official deploy", "read-only health/probe"],
                "redeploy_sequence": [
                    "target-aware read-only preflight",
                    "one queue-v1 owner",
                    "one-time official deploy authority",
                    "official two-host release",
                    "one queue-v1 owner",
                    "read-only health/probe",
                ],
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
        elif args.command == "preflight-redeploy":
            if args.backup_receipt is None or not args.backup_receipt_sha256:
                raise ProductionCutoverError("REDEPLOY_PREFLIGHT_BACKUP_REQUIRED")
            if args.artifact_dir.resolve(strict=False) != DEFAULT_ARTIFACT_DIR:
                raise ProductionCutoverError("BLOCKED_PRODUCTION_ARTIFACT_DIRECTORY")
            report = run_redeploy_preflight(
                manifest=args.manifest,
                staging_env=args.staging_env,
                backup_receipt=args.backup_receipt,
                backup_digest=args.backup_receipt_sha256,
            )
            report_path, report_digest = _write_redacted_receipt(
                args.artifact_dir, "production-queue-redeploy-preflight", report
            )
            payload = {
                "status": report["status"],
                "report_file": report_path.name,
                "report_sha256": report_digest,
                "provider_mutations": 0,
                "secrets_disclosed": False,
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
        elif args.command == "redeploy":
            if args.confirm != REDEPLOY_CONFIRMATION:
                raise ProductionCutoverError("REDEPLOY_CONFIRMATION_MISMATCH")
            _static_redeploy_gate(
                args.manifest,
                args.staging_env,
                backup_receipt=args.backup_receipt,
                backup_digest=args.backup_receipt_sha256,
            )
            if not all(
                (args.preflight_report, args.backup_receipt)
            ) or not args.preflight_report_sha256 or not args.backup_receipt_sha256:
                raise ProductionCutoverError("REDEPLOY_EVIDENCE_REQUIRED")
            with fail_safe_signal_guard():
                payload = redeploy_queue_v1(
                    manifest=args.manifest,
                    staging_env=args.staging_env,
                    preflight_report=args.preflight_report,
                    preflight_digest=args.preflight_report_sha256,
                    backup_receipt=args.backup_receipt,
                    backup_digest=args.backup_receipt_sha256,
                    artifact_dir=args.artifact_dir,
                    confirmation=args.confirm,
                    private_primary_attestation=private_primary_attestation,
                )
        elif args.command == "reconcile-redeploy-failure":
            if args.confirm != RECONCILE_REDEPLOY_CONFIRMATION:
                raise ProductionCutoverError("RECONCILE_CONFIRMATION_MISMATCH")
            if not all(
                (
                    args.preflight_report,
                    args.preflight_report_sha256,
                    args.backup_receipt,
                    args.backup_receipt_sha256,
                    args.failed_receipt,
                    args.failed_receipt_sha256,
                    args.phase_journal,
                    args.phase_journal_sha256,
                )
            ):
                raise ProductionCutoverError("RECONCILIATION_EVIDENCE_REQUIRED")
            with fail_safe_signal_guard():
                payload = reconcile_failed_redeploy(
                    manifest=args.manifest,
                    staging_env=args.staging_env,
                    preflight_report=args.preflight_report,
                    preflight_digest=args.preflight_report_sha256,
                    backup_receipt=args.backup_receipt,
                    backup_digest=args.backup_receipt_sha256,
                    failed_receipt=args.failed_receipt,
                    failed_digest=args.failed_receipt_sha256,
                    phase_journal=args.phase_journal,
                    phase_journal_digest=args.phase_journal_sha256,
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
                expected_artifact_dir=args.artifact_dir,
                private_primary_attestation=private_primary_attestation,
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
