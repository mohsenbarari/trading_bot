#!/usr/bin/env python3
"""Run the release-bound PRIVATE_PRIMARY production choreography.

This is the stateful controller behind the explicit
``production_deploy_online.sh private-primary-release`` action.  It accepts a
root-owned, digest-pinned operation plan, permits only the audited Market
Pipeline tools in one fixed order, keeps Product on ``LEGACY`` through every
pre-promotion phase, and writes a value-free recovery journal and terminal
receipt.  It never interprets a shell command.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


PLAN_SCHEMA = "production_private_primary_choreography_plan/1.0"
JOURNAL_SCHEMA = "production_private_primary_choreography_journal/1.0"
RECEIPT_SCHEMA = "production_private_primary_choreography/1.0"
PLAN_BUILD_RECEIPT_SCHEMA = (
    "production_private_primary_choreography_plan_build/1.0"
)
CONFIRMATION = "run-production-private-primary-choreography"
RECOVERY_CONFIRMATION = "recover-production-private-primary-choreography"
ROLLBACK_CONFIRMATION = "rollback-production-private-primary-choreography"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ARGUMENT = re.compile(r"^[A-Za-z0-9_./:=,@+%-]+$")
SAFE_SPACED_ARGUMENTS = frozenset(
    {"RECONCILE PRODUCTION ESTIMATOR SNAPSHOT PUBLICATION OUTBOX"}
)
MAXIMUM_DOCUMENT_BYTES = 2_000_000
CONTROLLER_LOCK_NAME = "private-primary-controller.lock"
APPROVED_RELEASE_REF = "refs/remotes/origin/main"
CONTROL_PAYLOAD_MANIFEST = "control-payload.sha256"
CONTROL_RELEASE_PAIR_RECEIPT = "market-pipeline-release-pair-receipt.json"

BOT_SNAPSHOT_ROOT = "/srv/trading-bot/production-data/market-pipeline/snapshots"
WEB_SNAPSHOT_ROOT = "/srv/trading-bot/market-data-production/snapshots"
CONTAINER_SNAPSHOT = "/app/runtime/product-estimator/latest-private-primary.json"
ZERO_OWNER_MAXIMUM_SECONDS = 300
SSH_BINARY = "/usr/bin/ssh"
REMOTE_PYTHON = "/usr/bin/python3"
TRANSACTION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{7,95}$")
WATCHDOG_JOURNAL_NAME = "private-primary-zero-owner-watchdog.json"
WATCHDOG_SCHEMA = "production_private_primary_zero_owner_watchdog/1.0"
CONTROL_RELEASE_TRANSITIVE_PAYLOADS = (
    "scripts/build_production_private_primary_choreography_plan.py",
    "scripts/check_production_coin_inference_readiness.py",
    "scripts/cutover_telegram_delivery_queue_production.py",
    "scripts/prepare_production_private_primary_manifest.py",
    "scripts/production_deploy_online.sh",
    "scripts/run_fenced_production_deploy.py",
    "scripts/update_production_coin_inference_source.py",
)
_ACTIVE_CONTROLLER_GUARD: _ControllerGuard | None = None

PHASES = (
    "bluegreen_workload_quiesce",
    "backup_restore_offhost",
    "bluegreen_database_quiesce",
    "migration",
    "base_services_start",
    "legacy_quiesce",
    "bluegreen_activate",
    "catchup_audit",
    "nine_source_evidence",
    "snapshot_outbox",
    "promotion_verification",
    "product_promotion",
)

MUTATING_PHASES = frozenset(
    {
        "bluegreen_workload_quiesce",
        "backup_restore_offhost",
        "bluegreen_database_quiesce",
        "migration",
        "base_services_start",
        "legacy_quiesce",
        "bluegreen_activate",
        "snapshot_outbox",
        "promotion_verification",
        "product_promotion",
    }
)

# Each tuple is (tool basename, command, role).  A role of ``None`` means the
# tool has no --role contract.  Counter equality prevents a plan from omitting
# a required invocation or hiding an extra mutation in a valid phase.
REQUIRED_COMMAND_SEQUENCES: Mapping[
    str, tuple[tuple[str, str, str | None], ...]
] = {
    "backup_restore_offhost": (
        ("backup_market_pipeline_archive.py", "create", None),
        ("backup_market_pipeline_archive.py", "verify", None),
        ("crypt_market_pipeline_backup.py", "encrypt", None),
        ("crypt_market_pipeline_backup.py", "verify", None),
    ),
    "bluegreen_workload_quiesce": (
        ("upgrade_market_pipeline_bluegreen.py", "plan", "web"),
        ("upgrade_market_pipeline_bluegreen.py", "plan", "bot"),
        ("upgrade_market_pipeline_bluegreen.py", "quiesce-workload", "web"),
        ("upgrade_market_pipeline_bluegreen.py", "quiesce-workload", "bot"),
    ),
    "bluegreen_database_quiesce": (
        ("upgrade_market_pipeline_bluegreen.py", "quiesce-database", "web"),
    ),
    "migration": (("migrate_market_pipeline_archive.py", "execute", None),),
    # Receiver-first is explicit across both hosts.  The web receiver exists
    # before processors, and the bot receiver exists before its adapter,
    # estimator and sender.  Capture owners are not authorized in this phase.
    "base_services_start": (
        ("rollout_market_pipeline_shadow.py", "prepare", "web"),
        ("rollout_market_pipeline_shadow.py", "start", "web"),
        ("rollout_market_pipeline_shadow.py", "prepare", "bot"),
        ("rollout_market_pipeline_shadow.py", "start", "bot"),
        ("rollout_market_pipeline_shadow.py", "start", "web"),
        ("rollout_market_pipeline_shadow.py", "start", "web"),
        ("rollout_market_pipeline_shadow.py", "start", "bot"),
        ("rollout_market_pipeline_shadow.py", "start", "bot"),
        ("rollout_market_pipeline_shadow.py", "start", "bot"),
        ("rollout_market_pipeline_shadow.py", "verify", "bot"),
        # Revisit the bootstrapped web receiver only after the bot sender is
        # healthy; this promotes bootstrap_ready to healthy before web verify.
        ("rollout_market_pipeline_shadow.py", "start", "web"),
        ("rollout_market_pipeline_shadow.py", "verify", "web"),
    ),
    "legacy_quiesce": (
        ("quiesce_production_legacy_market_collectors.py", "quiesce", "web"),
        ("quiesce_production_legacy_market_collectors.py", "verify", "web"),
        ("quiesce_production_legacy_market_collectors.py", "quiesce", "bot"),
        ("quiesce_production_legacy_market_collectors.py", "verify", "bot"),
        ("upgrade_market_pipeline_bluegreen.py", "prepare-capture-authority", "web"),
        ("quiesce_production_legacy_market_collectors.py", "prepare-authority", "bot"),
    ),
    "bluegreen_activate": (
        ("upgrade_market_pipeline_bluegreen.py", "authorize-captures", "web"),
        ("quiesce_production_legacy_market_collectors.py", "mark-authority-transferred", "bot"),
        ("upgrade_market_pipeline_bluegreen.py", "start-captures", "web"),
        ("upgrade_market_pipeline_bluegreen.py", "verify", "web"),
        ("upgrade_market_pipeline_bluegreen.py", "verify", "bot"),
    ),
    "catchup_audit": (
        ("audit_production_market_catchup.py", "web", None),
        ("audit_production_market_catchup.py", "bot", None),
        ("audit_production_market_catchup.py", "settle", None),
        ("audit_production_market_catchup.py", "web", None),
        ("audit_production_market_catchup.py", "bot", None),
        ("audit_production_market_catchup.py", "verify", None),
        ("observe_production_private_primary.py", "execute", "web"),
        ("observe_production_private_primary.py", "execute", "bot"),
    ),
    "nine_source_evidence": (
        ("run_release_bound_product_readiness.py", "execute", "web"),
        ("run_release_bound_product_readiness.py", "execute", "bot"),
    ),
    "snapshot_outbox": (
        ("reconcile_estimator_snapshot_publication_outbox.py", "plan", None),
        ("reconcile_estimator_snapshot_publication_outbox.py", "apply", None),
    ),
    "promotion_verification": (
        ("verify_production_private_primary_promotion.py", "verify", None),
        ("quiesce_production_legacy_market_collectors.py", "commit", "web"),
        ("quiesce_production_legacy_market_collectors.py", "commit", "bot"),
    ),
    "product_promotion": (
        ("promote_production_private_primary_product.py", "execute", None),
    ),
}

# Compatibility for tests and plan builders while retaining exact ordering as
# the authoritative contract.
REQUIRED_SIGNATURES: Mapping[
    str, Counter[tuple[str, str, str | None]]
] = {
    phase: Counter(sequence)
    for phase, sequence in REQUIRED_COMMAND_SEQUENCES.items()
}

KNOWN_COMMANDS: Mapping[str, set[str]] = {
    "backup_market_pipeline_archive.py": {"create", "verify"},
    "crypt_market_pipeline_backup.py": {"encrypt", "verify"},
    "rollout_market_pipeline_shadow.py": {
        "prepare", "start", "verify", "rollback",
    },
    "upgrade_market_pipeline_bluegreen.py": {
        "plan", "quiesce-workload", "quiesce-database",
        "prepare-capture-authority", "authorize-captures", "start-captures",
        "verify", "rollback",
    },
    "migrate_market_pipeline_archive.py": {"execute"},
    "quiesce_production_legacy_market_collectors.py": {
        "quiesce", "verify", "prepare-authority",
        "mark-authority-transferred", "mark-authority-restored",
        "commit", "restore", "recover",
    },
    "audit_production_market_catchup.py": {"web", "bot", "settle", "verify"},
    "observe_production_private_primary.py": {"execute"},
    "check_production_coin_inference_readiness.py": {"private-primary-consumer"},
    "run_release_bound_product_readiness.py": {"execute"},
    "reconcile_estimator_snapshot_publication_outbox.py": {"plan", "apply"},
    "verify_production_private_primary_promotion.py": {"verify"},
    "promote_production_private_primary_product.py": {"execute"},
}


class ChoreographyError(RuntimeError):
    """Stable, value-free controller refusal."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime | None = None) -> str:
    return (value or _utc_now()).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ChoreographyError(f"{label}_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ChoreographyError(f"{label}_invalid") from exc
    if parsed.utcoffset() != timedelta(0):
        raise ChoreographyError(f"{label}_invalid")
    return parsed


class _ControllerGuard:
    """Persistent fail-closed lock spanning the entire multi-host operation."""

    def __init__(
        self, path: Path, *, plan_sha256: str, release_sha: str, release_tree: str
    ) -> None:
        self.path = path
        self.expected = {
            "schema": "production_private_primary_choreography_lock/1.0",
            "status": "RUNNING",
            "plan_sha256": plan_sha256,
            "release_sha": release_sha,
            "release_tree": release_tree,
            "secrets_disclosed": False,
        }
        self.descriptor: int | None = None
        self.device: int | None = None
        self.inode: int | None = None

    def acquire(self) -> None:
        if self.path.name != CONTROLLER_LOCK_NAME:
            raise ChoreographyError("controller_lock_path_invalid")
        _secure_parent(self.path, label="controller_lock")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            metadata = os.fstat(descriptor)
            path_metadata = self.path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or (metadata.st_dev, metadata.st_ino)
                != (path_metadata.st_dev, path_metadata.st_ino)
            ):
                raise ChoreographyError("controller_lock_invalid")
            payload = os.read(descriptor, MAXIMUM_DOCUMENT_BYTES + 1)
            if payload:
                if _json(payload, label="controller_lock") != self.expected:
                    raise ChoreographyError("controller_lock_binding_mismatch")
            else:
                encoded = _canonical(self.expected)
                os.write(descriptor, encoded)
                os.fsync(descriptor)
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            self.descriptor = descriptor
            self.device = metadata.st_dev
            self.inode = metadata.st_ino
        except (OSError, BlockingIOError) as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            raise ChoreographyError("controller_lock_unavailable") from exc
        except BaseException:
            if "descriptor" in locals():
                os.close(descriptor)
            raise

    def release(self, *, terminal: bool) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        try:
            metadata = os.fstat(descriptor)
            path_metadata = self.path.lstat()
            if (
                (metadata.st_dev, metadata.st_ino)
                != (self.device, self.inode)
                or (path_metadata.st_dev, path_metadata.st_ino)
                != (self.device, self.inode)
            ):
                raise ChoreographyError("controller_lock_ownership_lost")
            if terminal:
                self.path.unlink()
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            self.descriptor = None


@contextmanager
def _bind_controller_guard(guard: _ControllerGuard | None):
    global _ACTIVE_CONTROLLER_GUARD
    previous = _ACTIVE_CONTROLLER_GUARD
    _ACTIVE_CONTROLLER_GUARD = guard
    try:
        yield
    finally:
        _ACTIVE_CONTROLLER_GUARD = previous


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _canonical(document: Mapping[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _secure_read(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ChoreographyError(f"{label}_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > MAXIMUM_DOCUMENT_BYTES
        ):
            raise ChoreographyError(f"{label}_invalid")
        payload = b""
        while len(payload) <= before.st_size:
            chunk = os.read(descriptor, before.st_size + 1 - len(payload))
            if not chunk:
                break
            payload += chunk
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise ChoreographyError(f"{label}_changed_during_read")
        return payload
    finally:
        os.close(descriptor)


def _secure_parent(path: Path, *, label: str) -> None:
    parent = path.parent
    try:
        info = parent.lstat()
    except OSError as exc:
        raise ChoreographyError(f"{label}_parent_unavailable") from exc
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ChoreographyError(f"{label}_parent_invalid")


def _write_atomic(path: Path, document: Mapping[str, object], *, exclusive: bool) -> str:
    _secure_parent(path, label="controller_output")
    payload = _canonical(document)
    if path.exists() or path.is_symlink():
        if exclusive:
            raise ChoreographyError("controller_output_exists")
        existing = _secure_read(path, label="controller_output")
        if existing == payload:
            return _digest(existing)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return _digest(payload)


def _json(payload: bytes, *, label: str) -> dict[str, object]:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ChoreographyError(f"{label}_json_invalid") from exc
    if not isinstance(document, dict):
        raise ChoreographyError(f"{label}_json_invalid")
    return document


def _env(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChoreographyError("runtime_source_encoding_invalid") from exc
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            raise ChoreographyError("runtime_source_syntax_invalid")
        key, value = raw.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ChoreographyError("runtime_source_syntax_invalid")
        values[key] = value
    return values


def _assert_product_source(
    path: Path, *, expected_sha256: str | None, expected_mode: str
) -> str:
    payload = _secure_read(path, label="runtime_source")
    digest = _digest(payload)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ChoreographyError("runtime_source_digest_mismatch")
    values = _env(payload)
    if values.get("PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE", "LEGACY") != expected_mode:
        raise ChoreographyError(f"runtime_source_not_{expected_mode.lower()}")
    if expected_mode == "PRIVATE_PRIMARY":
        expected = {
            "PRODUCTION_PRODUCT_ESTIMATOR_APP_SNAPSHOT_HOST_DIR": BOT_SNAPSHOT_ROOT,
            "PRODUCTION_PRODUCT_ESTIMATOR_BOT_SNAPSHOT_HOST_DIR": BOT_SNAPSHOT_ROOT,
            "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_SNAPSHOT_HOST_DIR": WEB_SNAPSHOT_ROOT,
            "PRODUCTION_PRODUCT_ESTIMATOR_APP_PRIVATE_PRIMARY_SNAPSHOT_PATH": CONTAINER_SNAPSHOT,
            "PRODUCTION_PRODUCT_ESTIMATOR_BOT_PRIVATE_PRIMARY_SNAPSHOT_PATH": CONTAINER_SNAPSHOT,
            "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_PRIVATE_PRIMARY_SNAPSHOT_PATH": CONTAINER_SNAPSHOT,
        }
        if any(values.get(key) != value for key, value in expected.items()):
            raise ChoreographyError("runtime_source_snapshot_contract_invalid")
    return digest


def _product_container_mode(
    *,
    host: str,
    container: str,
    ssh_argv: Sequence[str],
    expected_image_id: str,
    release_sha: str,
    release_tree: str,
) -> str:
    docker = (
        ["docker", "inspect", container]
        if host == "local"
        else [
            *ssh_argv,
            " ".join(
                shlex.quote(value) for value in ("docker", "inspect", container)
            ),
        ]
    )
    try:
        completed = subprocess.run(
            docker,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        rows = json.loads(completed.stdout or b"[]")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ChoreographyError("controller_product_runtime_mode_invalid") from exc
    if (
        completed.returncode != 0
        or not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
    ):
        raise ChoreographyError("controller_product_runtime_mode_invalid")
    row = rows[0]
    expected_project = "trading_bot" if host == "local" else "current"
    config = row.get("Config") or {}
    labels = config.get("Labels") or {}
    runtime_image_id = str(row.get("Image") or "")
    if (
        (row.get("State") or {}).get("Running") is not True
        or labels.get(
            "com.docker.compose.project"
        )
        != expected_project
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", runtime_image_id)
        or labels.get("org.opencontainers.image.revision") != release_sha
        or labels.get("io.gold-trade.release.tree") != release_tree
    ):
        raise ChoreographyError("controller_product_runtime_mode_invalid")
    if host == "local":
        if runtime_image_id != expected_image_id:
            raise ChoreographyError("controller_product_runtime_mode_invalid")
    elif _portable_product_image_identity(
        host="local", image_id=expected_image_id, ssh_argv=ssh_argv
    ) != _portable_product_image_identity(
        host="web", image_id=runtime_image_id, ssh_argv=ssh_argv
    ):
        # Docker may assign a different raw image ID after a portable archive
        # is loaded into another content store.  The release deploy therefore
        # proves the immutable, portable image fields instead of requiring the
        # store-local ID to match.  Keep the same contract in this controller.
        raise ChoreographyError("controller_product_runtime_mode_invalid")
    environment = config.get("Env")
    if not isinstance(environment, list):
        raise ChoreographyError("controller_product_runtime_mode_invalid")
    values = {
        row.split("=", 1)[0]: row.split("=", 1)[1]
        for row in environment
        if isinstance(row, str) and "=" in row
    }
    return str(values.get("PRODUCT_ESTIMATOR_SNAPSHOT_MODE") or "")


def _portable_product_image_identity(
    *, host: str, image_id: str, ssh_argv: Sequence[str]
) -> str:
    if host not in {"local", "web"} or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_id
    ):
        raise ChoreographyError("controller_product_runtime_mode_invalid")
    template = (
        "{{.Os}}|{{.Architecture}}|{{.Created}}|"
        "{{json .Config}}|{{json .RootFS}}"
    )
    command = ["docker", "image", "inspect", "--format", template, image_id]
    if host == "web":
        command = [
            *ssh_argv,
            " ".join(shlex.quote(value) for value in command),
        ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChoreographyError("controller_product_runtime_mode_invalid") from exc
    payload = (completed.stdout or b"").rstrip(b"\r\n")
    if completed.returncode != 0 or not payload or len(payload) > 1_000_000:
        raise ChoreographyError("controller_product_runtime_mode_invalid")
    return _digest(payload)


def _assert_product_runtime(
    *,
    ssh_argv: Sequence[str],
    expected_mode: str,
    expected_image_ids: Mapping[str, str],
    release_sha: str,
    release_tree: str,
) -> None:
    """Re-read all three Product consumers without importing repository code."""

    if expected_mode not in {"LEGACY", "PRIVATE_PRIMARY"}:
        raise ChoreographyError("controller_product_runtime_mode_invalid")
    observed = (
        _product_container_mode(
            host="local", container="trading_bot_app", ssh_argv=ssh_argv,
            expected_image_id=expected_image_ids["bot"],
            release_sha=release_sha, release_tree=release_tree,
        ),
        _product_container_mode(
            host="local", container="trading_bot_bot", ssh_argv=ssh_argv,
            expected_image_id=expected_image_ids["bot"],
            release_sha=release_sha, release_tree=release_tree,
        ),
        _product_container_mode(
            host="web", container="trading_bot_app", ssh_argv=ssh_argv,
            expected_image_id=expected_image_ids["web"],
            release_sha=release_sha, release_tree=release_tree,
        ),
    )
    if observed != (expected_mode, expected_mode, expected_mode):
        raise ChoreographyError("controller_product_runtime_mode_invalid")


def _git_identity(release_root: Path) -> tuple[str, str]:
    if not release_root.is_absolute() or release_root.is_symlink():
        raise ChoreographyError("release_root_invalid")
    try:
        head = subprocess.run(
            ["git", "-C", str(release_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(release_root), "rev-parse", "HEAD^{tree}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ChoreographyError("release_git_identity_unavailable") from exc
    return head, tree


def _assert_exact_release_checkout(
    release_root: Path,
    *,
    release_sha: str,
    release_tree: str,
    approved_release_ref: str,
    allow_historical_approved: bool = False,
) -> bool:
    if approved_release_ref != "refs/remotes/origin/main":
        raise ChoreographyError("release_approved_ref_invalid")
    try:
        approved = subprocess.run(
            [
                "git",
                "-C",
                str(release_root),
                "rev-parse",
                "--verify",
                f"{approved_release_ref}^{{commit}}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status_output = subprocess.run(
            [
                "git",
                "-C",
                str(release_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ChoreographyError("release_checkout_identity_unavailable") from exc
    head, tree = _git_identity(release_root)
    if (
        approved == release_sha
        and (head, tree) == (release_sha, release_tree)
        and not status_output
    ):
        return False
    if not allow_historical_approved or status_output or head != approved:
        raise ChoreographyError("release_checkout_not_exact_clean_approved")
    try:
        historical_tree = subprocess.run(
            [
                "git",
                "-C",
                str(release_root),
                "rev-parse",
                "--verify",
                f"{release_sha}^{{tree}}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        ancestor = subprocess.run(
            [
                "git",
                "-C",
                str(release_root),
                "merge-base",
                "--is-ancestor",
                release_sha,
                approved,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise ChoreographyError("release_checkout_identity_unavailable") from exc
    if ancestor.returncode != 0 or historical_tree != release_tree:
        raise ChoreographyError("release_checkout_not_exact_clean_approved")
    return True


def _tracked_tool_sha256(release_root: Path, tool: str) -> str:
    relative = f"scripts/{tool}"
    try:
        payload = subprocess.run(
            ["git", "-C", str(release_root), "show", f"HEAD:{relative}"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ChoreographyError("controller_tool_not_release_tracked") from exc
    return _digest(payload)


def _release_file_digest(path: Path, *, expected_sha256: str) -> str:
    """Hash one immutable release file through an O_NOFOLLOW descriptor."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ChoreographyError("control_release_file_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or bool(before.st_mode & 0o022)
            or before.st_size <= 0
            or before.st_size > 16 * 1024 * 1024
        ):
            raise ChoreographyError("control_release_file_invalid")
        observed = sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (
            size != before.st_size
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
            or observed.hexdigest() != expected_sha256
        ):
            raise ChoreographyError("control_release_file_digest_mismatch")
        return observed.hexdigest()
    finally:
        os.close(descriptor)


def _control_release_manifest(
    root: Path,
    *,
    expected_manifest_sha256: str,
    release_sha: str,
    release_tree: str,
) -> dict[str, str]:
    """Validate the SHA-named installed release and return its file digests."""

    if (
        not root.is_absolute()
        or root.is_symlink()
        or root.name != release_sha
        or not HEX64.fullmatch(expected_manifest_sha256)
    ):
        raise ChoreographyError("control_release_root_invalid")
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise ChoreographyError("control_release_root_unavailable") from exc
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) != 0o700
    ):
        raise ChoreographyError("control_release_root_invalid")
    manifest_path = root / CONTROL_PAYLOAD_MANIFEST
    manifest_payload = _secure_read(manifest_path, label="control_release_manifest")
    if _digest(manifest_payload) != expected_manifest_sha256:
        raise ChoreographyError("control_release_manifest_digest_mismatch")
    try:
        manifest_text = manifest_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChoreographyError("control_release_manifest_invalid") from exc
    entries: dict[str, str] = {}
    for line in manifest_text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (\./[A-Za-z0-9_./-]+)", line)
        if not match:
            raise ChoreographyError("control_release_manifest_invalid")
        relative = match.group(2)[2:]
        relative_path = Path(relative)
        if (
            relative in entries
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or not relative_path.parts
        ):
            raise ChoreographyError("control_release_manifest_invalid")
        entries[relative] = match.group(1)
    required = {f"scripts/{tool}" for tool in KNOWN_COMMANDS}
    required.add("scripts/run_production_private_primary_choreography.py")
    required.update(CONTROL_RELEASE_TRANSITIVE_PAYLOADS)
    if not required.issubset(entries):
        raise ChoreographyError("control_release_manifest_tools_missing")
    pair_payload = _secure_read(
        root / CONTROL_RELEASE_PAIR_RECEIPT, label="control_release_pair_receipt"
    )
    pair = _json(pair_payload, label="control_release_pair_receipt")
    if (
        pair.get("schema") not in {
            "market_pipeline_release_pair/1.0",
            "market_pipeline_release_pair/1.1",
            "market_pipeline_primary_release_pair/1.0",
            "market_pipeline_primary_release_pair/1.1",
        }
        or pair.get("release_sha") != release_sha
        or pair.get("release_tree") != release_tree
        or pair.get("secrets_disclosed") is not False
    ):
        raise ChoreographyError("control_release_pair_binding_invalid")
    if str(pair.get("schema") or "").startswith("market_pipeline_primary_release_pair") and (
        pair.get("feed_mode") != "PRIVATE_PRIMARY"
        or pair.get("product_authority_changed") is not False
    ):
        raise ChoreographyError("control_release_pair_binding_invalid")
    # Validate every executable tool now.  The target tool is validated again
    # through the exact descriptor that is inherited by each child process.
    for relative in required:
        _release_file_digest(root / relative, expected_sha256=entries[relative])
    return entries


def _option(arguments: Sequence[str], name: str) -> str | None:
    try:
        index = arguments.index(name)
    except ValueError:
        return None
    return arguments[index + 1] if index + 1 < len(arguments) else None


def _set_option(arguments: Sequence[str], name: str, value: str) -> list[str]:
    result = list(arguments)
    while name in result:
        index = result.index(name)
        del result[index : index + 2]
    result.extend((name, value))
    return result


def _command_path(
    phases: Sequence[Mapping[str, object]],
    signature: tuple[str, str, str | None],
    option: str,
) -> Path:
    matches: list[str] = []
    for phase in phases:
        commands = phase.get("commands")
        if not isinstance(commands, list):
            continue
        for command in commands:
            if not isinstance(command, dict) or _signature(command) != signature:
                continue
            arguments = command.get("arguments")
            assert isinstance(arguments, list)
            value = _option(arguments, option)
            if value:
                matches.append(value)
    if len(matches) != 1:
        raise ChoreographyError("plan_artifact_binding_missing")
    path = Path(matches[0])
    if not path.is_absolute() or path.is_symlink():
        raise ChoreographyError("plan_artifact_binding_invalid")
    return path


def _command_paths(
    phases: Sequence[Mapping[str, object]],
    signature: tuple[str, str, str | None],
    option: str,
) -> tuple[Path, ...]:
    matches: list[Path] = []
    for phase in phases:
        commands = phase.get("commands")
        if not isinstance(commands, list):
            continue
        for command in commands:
            if not isinstance(command, dict) or _signature(command) != signature:
                continue
            arguments = command.get("arguments")
            assert isinstance(arguments, list)
            value = _option(arguments, option)
            if not value:
                raise ChoreographyError("plan_artifact_binding_missing")
            path = Path(value)
            if not path.is_absolute() or path.is_symlink():
                raise ChoreographyError("plan_artifact_binding_invalid")
            matches.append(path)
    if not matches:
        raise ChoreographyError("plan_artifact_binding_missing")
    return tuple(matches)


def _hex_context(context: Mapping[str, object], key: str) -> str:
    value = str(context.get(key) or "")
    if not HEX64.fullmatch(value):
        raise ChoreographyError("controller_dynamic_binding_missing")
    return value


def _command_name(tool: str, arguments: Sequence[str]) -> str:
    names = KNOWN_COMMANDS[tool]
    if names == {"execute"}:
        return "execute"
    matches = [value for value in arguments if value in names]
    if len(matches) != 1:
        raise ChoreographyError("plan_command_action_invalid")
    return matches[0]


def _signature(command: Mapping[str, object]) -> tuple[str, str, str | None]:
    tool = str(command.get("tool") or "")
    if tool not in KNOWN_COMMANDS:
        raise ChoreographyError("plan_tool_forbidden")
    arguments = command.get("arguments")
    if not isinstance(arguments, list) or any(
        not isinstance(value, str)
        or not value
        or (
            not SAFE_ARGUMENT.fullmatch(value)
            and not (
                index > 0
                and arguments[index - 1] == "--confirm"
                and value in SAFE_SPACED_ARGUMENTS
            )
        )
        for index, value in enumerate(arguments)
    ):
        raise ChoreographyError("plan_arguments_invalid")
    option_names = [value for value in arguments if value.startswith("--")]
    if len(option_names) != len(set(option_names)):
        # ``argparse`` generally accepts a duplicate option using the last
        # value whereas the controller's binding helpers intentionally read
        # the first.  Reject ambiguity rather than let plan validation and
        # execution observe different release/path identities.
        raise ChoreographyError("plan_argument_duplicate")
    role = _option(arguments, "--role") or _option(arguments, "--host-role")
    if role not in {None, "web", "bot"}:
        raise ChoreographyError("plan_role_invalid")
    host = command.get("host")
    if host not in {"local", "web"}:
        raise ChoreographyError("plan_host_invalid")
    if role == "web" and host != "web":
        raise ChoreographyError("plan_host_role_mismatch")
    if role == "bot" and host != "local":
        raise ChoreographyError("plan_host_role_mismatch")
    return tool, _command_name(tool, arguments), role


def _validate_command_binding(
    command: Mapping[str, object], *, release_sha: str, release_tree: str
) -> None:
    arguments = command["arguments"]
    assert isinstance(arguments, list)
    for option, expected in (
        ("--release-sha", release_sha),
        ("--expected-release-sha", release_sha),
        ("--release-tree", release_tree),
        ("--expected-release-tree", release_tree),
    ):
        if option in arguments and _option(arguments, option) != expected:
            raise ChoreographyError("plan_command_release_binding_mismatch")


def _validate_phase_execution_contract(
    phase: str, commands: Sequence[Mapping[str, object]]
) -> None:
    """Bind host placement and order-sensitive service starts.

    Role validation alone is insufficient for tools without a ``--role``
    argument.  In particular, a backup encrypted and verified on the same web
    host is not an off-host backup, and the migration tool is meaningful only
    on the web/database host.
    """

    expected_hosts: Mapping[str, tuple[str, ...]] = {
        "bluegreen_workload_quiesce": ("web", "local", "web", "local"),
        "backup_restore_offhost": ("web", "web", "web", "local"),
        "bluegreen_database_quiesce": ("web",),
        "migration": ("web",),
        "base_services_start": (
            "web", "web", "local", "local", "web", "web",
            "local", "local", "local", "local",
            "web", "web",
        ),
        "legacy_quiesce": ("web", "web", "local", "local", "web", "local"),
        "bluegreen_activate": ("web", "local", "web", "web", "local"),
        "catchup_audit": (
            "web", "local", "local", "web", "local", "local", "web", "local"
        ),
        "nine_source_evidence": ("web", "local"),
        "snapshot_outbox": ("web", "web"),
        "promotion_verification": ("local", "web", "local"),
        "product_promotion": ("local",),
    }
    hosts = expected_hosts.get(phase)
    if hosts is not None and tuple(command.get("host") for command in commands) != hosts:
        raise ChoreographyError("plan_phase_host_topology_invalid")
    if phase == "backup_restore_offhost":
        create, verify, encrypt, decrypt_verify = commands
        create_arguments = create["arguments"]
        verify_arguments = verify["arguments"]
        encrypt_arguments = encrypt["arguments"]
        decrypt_arguments = decrypt_verify["arguments"]
        assert isinstance(create_arguments, list)
        assert isinstance(verify_arguments, list)
        assert isinstance(encrypt_arguments, list)
        assert isinstance(decrypt_arguments, list)
        create_receipt = _option(create_arguments, "--receipt")
        if (
            not create_receipt
            or _option(verify_arguments, "--receipt") != create_receipt
            or not _option(create_arguments, "--backup-dir")
            or not _option(encrypt_arguments, "--destination")
            or not _option(encrypt_arguments, "--receipt")
            or not _option(decrypt_arguments, "--artifact")
            or not _option(decrypt_arguments, "--receipt")
        ):
            raise ChoreographyError("plan_backup_offhost_binding_invalid")
        for value in (
            create_receipt,
            _option(create_arguments, "--backup-dir"),
            _option(encrypt_arguments, "--destination"),
            _option(encrypt_arguments, "--receipt"),
            _option(decrypt_arguments, "--artifact"),
            _option(decrypt_arguments, "--receipt"),
        ):
            path = Path(str(value or ""))
            if not path.is_absolute() or path.is_symlink():
                raise ChoreographyError("plan_backup_offhost_binding_invalid")
        if (
            _option(encrypt_arguments, "--destination")
            == _option(decrypt_arguments, "--artifact")
            or _option(encrypt_arguments, "--receipt")
            == _option(decrypt_arguments, "--receipt")
        ):
            raise ChoreographyError("plan_backup_not_offhost")
    if phase == "base_services_start":
        expected_services = (
            None,
            "estimator-snapshot-receiver",
            None,
            "market-fact-receiver",
            "market-processor",
            "market-fact-sync-worker",
            "market-store-adapter",
            "coin-estimator",
            "estimator-snapshot-sender",
            None,
            "estimator-snapshot-receiver",
            None,
        )
        for command, service in zip(commands, expected_services, strict=True):
            arguments = command["arguments"]
            assert isinstance(arguments, list)
            if (
                _option(arguments, "--feed-mode") != "PRIVATE_PRIMARY"
                or _option(arguments, "--service") != service
            ):
                raise ChoreographyError("plan_receiver_first_order_invalid")


def _validate_plan(
    document: Mapping[str, object], *, release_root: Path,
    allow_historical_approved: bool = False,
) -> tuple[
    str,
    str,
    Path,
    str,
    Path,
    list[dict[str, object]],
    list[str],
    Path,
    Path,
    str,
    dict[str, dict[str, str]],
]:
    release_sha = str(document.get("release_sha") or "")
    release_tree = str(document.get("release_tree") or "")
    if (
        document.get("schema") != PLAN_SCHEMA
        or not HEX40.fullmatch(release_sha)
        or not HEX40.fullmatch(release_tree)
        or document.get("product_authority_initial") != "LEGACY"
        or document.get("product_authority_final") != "PRIVATE_PRIMARY"
        or document.get("legacy_collectors_restart_forbidden") is not True
        or document.get("product_promotion_last") is not True
        or document.get("approved_release_ref") != APPROVED_RELEASE_REF
        or document.get("secrets_disclosed") is not False
    ):
        raise ChoreographyError("plan_contract_invalid")
    historical_approved = _assert_exact_release_checkout(
        release_root,
        release_sha=release_sha,
        release_tree=release_tree,
        approved_release_ref=str(document.get("approved_release_ref") or ""),
        allow_historical_approved=allow_historical_approved,
    )
    observed_head, observed_tree = _git_identity(release_root)
    if not historical_approved and (
        observed_head,
        observed_tree,
    ) != (release_sha, release_tree):
        raise ChoreographyError("plan_release_root_binding_mismatch")
    source = Path(str(document.get("source_manifest") or ""))
    deployment_manifest = Path(str(document.get("deployment_manifest") or ""))
    controller_lock = Path(str(document.get("controller_lock") or ""))
    source_digest = str(document.get("expected_source_sha256") or "")
    local_control_root = Path(str(document.get("local_control_release_root") or ""))
    remote_control_root = Path(str(document.get("remote_control_release_root") or ""))
    control_manifest_sha256 = str(
        document.get("control_payload_manifest_sha256") or ""
    )
    product_image_ids = document.get("product_image_ids")
    raw_role_env_bindings = document.get("role_env_bindings")
    if (
        not source.is_absolute()
        or not deployment_manifest.is_absolute()
        or not HEX64.fullmatch(source_digest)
        or not controller_lock.is_absolute()
        or controller_lock.name != CONTROLLER_LOCK_NAME
        or not local_control_root.is_absolute()
        or local_control_root.name != release_sha
        or not remote_control_root.is_absolute()
        or remote_control_root.name != release_sha
        or not HEX64.fullmatch(control_manifest_sha256)
        or not isinstance(product_image_ids, dict)
        or set(product_image_ids) != {"bot", "web"}
        or any(
            not isinstance(product_image_ids.get(role), str)
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(product_image_ids.get(role))
            )
            for role in ("bot", "web")
        )
        or not isinstance(raw_role_env_bindings, dict)
        or set(raw_role_env_bindings) != {"bot", "web"}
    ):
        raise ChoreographyError("plan_source_binding_invalid")
    role_env_bindings: dict[str, dict[str, str]] = {}
    for role in ("bot", "web"):
        raw_binding = raw_role_env_bindings.get(role)
        if (
            not isinstance(raw_binding, dict)
            or set(raw_binding)
            != {"new_path", "new_sha256", "old_path", "old_sha256"}
        ):
            raise ChoreographyError("plan_role_env_binding_invalid")
        binding = {key: str(value) for key, value in raw_binding.items()}
        new_path = Path(binding["new_path"])
        old_path = Path(binding["old_path"])
        required_root = local_control_root if role == "bot" else remote_control_root
        try:
            new_path.relative_to(required_root)
        except ValueError as exc:
            raise ChoreographyError("plan_role_env_binding_invalid") from exc
        if (
            not new_path.is_absolute()
            or not old_path.is_absolute()
            or new_path == old_path
            or not HEX64.fullmatch(binding["new_sha256"])
            or not HEX64.fullmatch(binding["old_sha256"])
        ):
            raise ChoreographyError("plan_role_env_binding_invalid")
        role_env_bindings[role] = binding
    ssh_argv = document.get("web_ssh_argv")
    if (
        not isinstance(ssh_argv, list)
        or not ssh_argv
        or str(ssh_argv[0]) != SSH_BINARY
        or any(
            not isinstance(value, str) or not value or not SAFE_ARGUMENT.fullmatch(value)
            for value in ssh_argv
        )
    ):
        raise ChoreographyError("plan_web_ssh_invalid")
    phases = document.get("phases")
    if not isinstance(phases, list) or [item.get("id") for item in phases if isinstance(item, dict)] != list(PHASES):
        raise ChoreographyError("plan_phase_order_invalid")
    validated: list[dict[str, object]] = []
    for expected_phase, raw in zip(PHASES, phases, strict=True):
        if not isinstance(raw, dict) or raw.get("id") != expected_phase:
            raise ChoreographyError("plan_phase_invalid")
        commands = raw.get("commands")
        evidence = raw.get("evidence")
        if not isinstance(commands, list) or not isinstance(evidence, list) or not evidence:
            raise ChoreographyError("plan_phase_payload_invalid")
        signatures: list[tuple[str, str, str | None]] = []
        for command in commands:
            if not isinstance(command, dict):
                raise ChoreographyError("plan_command_invalid")
            signatures.append(_signature(command))
            _validate_command_binding(command, release_sha=release_sha, release_tree=release_tree)
            tool, action, role = _signature(command)
            arguments = command["arguments"]
            assert isinstance(arguments, list)
            if tool == "upgrade_market_pipeline_bluegreen.py" and action == "plan":
                assert role in {"bot", "web"}
                if (
                    _option(arguments, "--new-env")
                    != role_env_bindings[role]["new_path"]
                    or _option(arguments, "--old-env")
                    != role_env_bindings[role]["old_path"]
                ):
                    raise ChoreographyError("plan_role_env_binding_invalid")
            if tool in {"backup_market_pipeline_archive.py", "migrate_market_pipeline_archive.py"}:
                if _option(arguments, "--env-file") != role_env_bindings["web"]["new_path"]:
                    raise ChoreographyError("plan_role_env_binding_invalid")
                backup_env = _option(arguments, "--backup-env-file")
                if backup_env is not None and backup_env != role_env_bindings["web"]["new_path"]:
                    raise ChoreographyError("plan_role_env_binding_invalid")
            if tool == "rollout_market_pipeline_shadow.py":
                assert role in {"bot", "web"}
                if _option(arguments, "--env-file") != role_env_bindings[role]["new_path"]:
                    raise ChoreographyError("plan_role_env_binding_invalid")
            if tool == "audit_production_market_catchup.py" and action == "web":
                if _option(arguments, "--runtime-env") != role_env_bindings["web"]["new_path"]:
                    raise ChoreographyError("plan_role_env_binding_invalid")
            if tool == "verify_production_private_primary_promotion.py":
                if _option(arguments, "--bot-env") != role_env_bindings["bot"]["new_path"]:
                    raise ChoreographyError("plan_role_env_binding_invalid")
            if (
                command.get("host") == "web"
                and command.get("remote_release_root") != str(remote_control_root)
            ):
                raise ChoreographyError("plan_remote_release_root_invalid")
        if tuple(signatures) != REQUIRED_COMMAND_SEQUENCES[expected_phase]:
            raise ChoreographyError("plan_phase_commands_incomplete")
        _validate_phase_execution_contract(expected_phase, commands)
        if expected_phase == "nine_source_evidence":
            expected_readiness = {
                "web": (
                    str(remote_control_root),
                    "trading_bot_app",
                    "current",
                ),
                "bot": (
                    str(local_control_root),
                    "trading_bot_bot",
                    "trading_bot",
                ),
            }
            for command in commands:
                arguments = command["arguments"]
                assert isinstance(arguments, list)
                role = _signature(command)[2]
                assert role in {"web", "bot"}
                control_root, container, project = expected_readiness[role]
                if (
                    _option(arguments, "--release-sha") != release_sha
                    or _option(arguments, "--release-tree") != release_tree
                    or _option(arguments, "--control-root") != control_root
                    or _option(arguments, "--expected-control-manifest-sha256")
                    != control_manifest_sha256
                    or _option(arguments, "--container") != container
                    or _option(arguments, "--project") != project
                    or _option(arguments, "--expected-image-id")
                    != product_image_ids[role]
                    or _option(arguments, "--confirm")
                    != "run-release-bound-product-readiness"
                ):
                    raise ChoreographyError(
                        "plan_product_readiness_wrapper_invalid"
                    )
        if expected_phase == "product_promotion":
            product_arguments = commands[0]["arguments"]
            assert isinstance(product_arguments, list)
            if (
                _option(product_arguments, "--release-checkout")
                != str(release_root)
            ):
                raise ChoreographyError(
                    "plan_product_release_checkout_invalid"
                )
        # Recovery is controller-owned.  Accepting plan-selected recovery or
        # rollback commands would let an otherwise valid plan choose a wrong
        # host, restart a legacy owner, or cross a forward-only frontier.
        recovery = raw.get("recovery_commands", [])
        rollback = raw.get("rollback_commands", [])
        if recovery != [] or rollback != []:
            raise ChoreographyError("plan_recovery_commands_forbidden")
        for item in evidence:
            if (
                not isinstance(item, dict)
                or item.get("host") not in {"local", "web"}
                or not Path(str(item.get("path") or "")).is_absolute()
                or not isinstance(item.get("schema"), str)
                or not isinstance(item.get("statuses"), list)
                or not item["statuses"]
            ):
                raise ChoreographyError("plan_evidence_invalid")
        validated.append(raw)
    for signature, option in (
        (("upgrade_market_pipeline_bluegreen.py", "plan", "web"), "--journal"),
        (("upgrade_market_pipeline_bluegreen.py", "plan", "bot"), "--journal"),
        (("quiesce_production_legacy_market_collectors.py", "quiesce", "web"), "--journal"),
        (("quiesce_production_legacy_market_collectors.py", "quiesce", "bot"), "--journal"),
        (("upgrade_market_pipeline_bluegreen.py", "authorize-captures", "web"), "--bot-legacy-collector-receipt"),
        (("verify_production_private_primary_promotion.py", "verify", None), "--receipt"),
        (("quiesce_production_legacy_market_collectors.py", "commit", "web"), "--primary-verification"),
        (("promote_production_private_primary_product.py", "execute", None), "--web-maintenance-journal"),
    ):
        _command_path(validated, signature, option)
    return (
        release_sha,
        release_tree,
        source,
        source_digest,
        controller_lock,
        validated,
        list(ssh_argv),
        local_control_root,
        remote_control_root,
        control_manifest_sha256,
        role_env_bindings,
    )


def _validate_official_invocation(
    args: argparse.Namespace,
    *,
    source: Path,
    ssh_argv: Sequence[str],
    local_control_root: Path,
    remote_control_root: Path,
    control_manifest_sha256: str,
    deployment_manifest: Path,
) -> None:
    expected_source = Path(str(args.expected_source_manifest))
    expected_ssh_digest = str(args.expected_web_ssh_argv_sha256)
    expected_local_control_root = Path(str(args.expected_local_control_release_root))
    expected_remote_control_root = Path(str(args.expected_remote_control_release_root))
    expected_control_manifest = str(args.expected_control_payload_manifest_sha256)
    expected_deployment_manifest = Path(str(args.expected_deployment_manifest))
    expected_deployment_manifest_sha256 = str(
        args.expected_deployment_manifest_sha256
    )
    if (
        not expected_source.is_absolute()
        or expected_source.is_symlink()
        or source != expected_source
        or not HEX64.fullmatch(expected_ssh_digest)
        or _digest(b"\0".join(value.encode("utf-8") for value in ssh_argv) + b"\0")
        != expected_ssh_digest
        or local_control_root != expected_local_control_root
        or remote_control_root != expected_remote_control_root
        or control_manifest_sha256 != expected_control_manifest
        or not HEX64.fullmatch(expected_control_manifest)
        or deployment_manifest != expected_deployment_manifest
        or not HEX64.fullmatch(expected_deployment_manifest_sha256)
        or _digest(_secure_read(
            deployment_manifest, label="deployment_manifest"
        )) != expected_deployment_manifest_sha256
    ):
        raise ChoreographyError("controller_official_invocation_binding_invalid")


def _validate_plan_build_receipt(
    args: argparse.Namespace,
    *,
    plan: Mapping[str, object],
    plan_sha256: str,
    release_sha: str,
    release_tree: str,
    local_control_root: Path,
    control_entries: Mapping[str, str],
) -> None:
    receipt_path = Path(str(args.plan_build_receipt))
    expected_receipt_sha256 = str(args.expected_plan_build_receipt_sha256)
    if (
        not receipt_path.is_absolute()
        or not HEX64.fullmatch(expected_receipt_sha256)
    ):
        raise ChoreographyError("controller_plan_build_receipt_invalid")
    payload = _secure_read(receipt_path, label="controller_plan_build_receipt")
    if _digest(payload) != expected_receipt_sha256:
        raise ChoreographyError("controller_plan_build_receipt_invalid")
    receipt = _json(payload, label="controller_plan_build_receipt")
    inputs = receipt.get("input_sha256")
    input_paths = receipt.get("input_paths")
    paths = receipt.get("path_sha256")
    product_images = receipt.get("product_image_ids")
    expected_input_labels = {
        "runtime_source", "deployment_manifest", "control_manifest",
        "control_pair_receipt", "primary_pair_receipt",
        "market_image_receipt", "preflight_receipt", "web_env", "bot_env",
        "web_old_env", "bot_old_env", "product_bot_image_receipt",
        "product_web_image_receipt", "private_manifest",
        "private_manifest_receipt",
    }
    phases = plan.get("phases")
    command_count = 0
    if isinstance(phases, list):
        command_count = sum(
            len(phase["commands"])
            for phase in phases
            if isinstance(phase, dict) and isinstance(phase.get("commands"), list)
        )
    product_transaction_id = None
    if isinstance(phases, list) and phases:
        product = phases[-1]
        if isinstance(product, dict):
            commands = product.get("commands")
            if isinstance(commands, list) and commands:
                arguments = commands[0].get("arguments")
                if isinstance(arguments, list):
                    product_transaction_id = _option(arguments, "--transaction-id")
    builder_relative = "scripts/build_production_private_primary_choreography_plan.py"
    if (
        receipt.get("schema") != PLAN_BUILD_RECEIPT_SCHEMA
        or receipt.get("status") != "PASS"
        or receipt.get("release_sha") != release_sha
        or receipt.get("release_tree") != release_tree
        or receipt.get("approved_release_ref") != APPROVED_RELEASE_REF
        or receipt.get("plan_sha256") != plan_sha256
        or receipt.get("transaction_id") != plan.get("transaction_id")
        or not TRANSACTION_ID.fullmatch(str(receipt.get("transaction_id") or ""))
        or str(receipt.get("transaction_id") or "") != str(product_transaction_id or "")
        or receipt.get("builder_tool") != builder_relative
        or receipt.get("builder_script_sha256") != control_entries.get(builder_relative)
        or not HEX64.fullmatch(str(receipt.get("builder_script_sha256") or ""))
        or receipt.get("plan_output_path_sha256")
        != _digest(str(Path(args.plan)).encode("utf-8"))
        or receipt.get("receipt_output_path_sha256")
        != _digest(str(receipt_path).encode("utf-8"))
        or receipt.get("required_input_labels")
        != sorted(expected_input_labels)
        or receipt.get("phase_count") != len(PHASES)
        or isinstance(receipt.get("command_count"), bool)
        or not isinstance(receipt.get("command_count"), int)
        or receipt["command_count"] != command_count
        or command_count <= 0
        or not isinstance(inputs, dict)
        or set(inputs) != expected_input_labels
        or any(not HEX64.fullmatch(str(value)) for value in inputs.values())
        or not isinstance(input_paths, dict)
        or set(input_paths) != expected_input_labels
        or inputs.get("runtime_source") != plan.get("expected_source_sha256")
        or inputs.get("control_manifest")
        != plan.get("control_payload_manifest_sha256")
        or not isinstance(paths, dict)
        or paths.get("local_control_release_root")
        != _digest(str(plan.get("local_control_release_root")).encode("utf-8"))
        or paths.get("remote_control_release_root")
        != _digest(str(plan.get("remote_control_release_root")).encode("utf-8"))
        or paths.get("release_checkout")
        != _digest(str(args.release_root).encode("utf-8"))
        or receipt.get("web_ssh_argv_sha256")
        != args.expected_web_ssh_argv_sha256
        or product_images != plan.get("product_image_ids")
        or receipt.get("secret_values_included") is not False
        or receipt.get("live_state_inspected") is not False
        or receipt.get("git_inspected") is not False
        or receipt.get("recovery_commands_embedded") is not False
        or receipt.get("rollback_commands_embedded") is not False
        or receipt.get("secrets_disclosed") is not False
    ):
        raise ChoreographyError("controller_plan_build_receipt_invalid")
    for label in sorted(expected_input_labels):
        path = Path(str(input_paths.get(label) or ""))
        expected = str(inputs.get(label) or "")
        if (
            not path.is_absolute()
            or _digest(str(path).encode("utf-8"))
            != _digest(str(input_paths[label]).encode("utf-8"))
            or _digest(_secure_read(path, label=f"plan_input_{label}")) != expected
        ):
            raise ChoreographyError("controller_plan_input_inventory_mismatch")
        if label == "control_manifest" and path != local_control_root / CONTROL_PAYLOAD_MANIFEST:
            raise ChoreographyError("controller_plan_input_inventory_mismatch")


def _command_result(payload: bytes) -> dict[str, object]:
    try:
        lines = [line for line in payload.splitlines() if line.strip()]
        if len(lines) != 1:
            raise ValueError
        result = json.loads(lines[0])
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ChoreographyError("controller_command_result_invalid") from exc
    if not isinstance(result, dict):
        raise ChoreographyError("controller_command_result_invalid")
    return result


_LOCK_KEEPER_SUPERVISOR = r"""
import ctypes,fcntl,hashlib,os,signal,stat,subprocess,sys
fd=int(sys.argv[1]);expected_dev=int(sys.argv[2]);expected_ino=int(sys.argv[3]);expected_path_sha=sys.argv[4];timeout=int(sys.argv[5]);child_fds=tuple(int(value) for value in sys.argv[6].split(',') if value);command=sys.argv[7:]
def fail(): raise SystemExit(72)
try:
    info=os.fstat(fd);target=os.path.realpath(os.readlink(f'/proc/self/fd/{fd}'));path_info=os.lstat(target)
except OSError: fail()
if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=(expected_dev,expected_ino) or (path_info.st_dev,path_info.st_ino)!=(expected_dev,expected_ino) or hashlib.sha256(target.encode()).hexdigest()!=expected_path_sha: fail()
try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except OSError: fail()
keeper_pid=os.getpid()
def child_fence():
    if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0)!=0 or os.getppid()!=keeper_pid: os._exit(72)
held_child_fds=tuple(dict.fromkeys((fd,*child_fds)))
child=subprocess.Popen(command,stdin=sys.stdin,stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=held_child_fds,start_new_session=True,preexec_fn=child_fence)
try:
    stdout,stderr=child.communicate(timeout=timeout)
except subprocess.TimeoutExpired:
    os.killpg(child.pid,signal.SIGTERM)
    try: stdout,stderr=child.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid,signal.SIGKILL);stdout,stderr=child.communicate()
    sys.stdout.buffer.write(stdout);sys.stderr.buffer.write(stderr);raise SystemExit(124)
after=os.fstat(fd);path_after=os.lstat(target)
if (after.st_dev,after.st_ino)!=(expected_dev,expected_ino) or (path_after.st_dev,path_after.st_ino)!=(expected_dev,expected_ino): fail()
sys.stdout.buffer.write(stdout);sys.stderr.buffer.write(stderr);raise SystemExit(child.returncode)
""".strip()


def _run_guarded_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    pass_fds: Sequence[int],
    timeout_seconds: int,
    guard: _ControllerGuard,
    input_payload: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    descriptor = guard.descriptor
    if (
        descriptor is None
        or guard.device is None
        or guard.inode is None
        or not guard.path.is_absolute()
    ):
        raise ChoreographyError("controller_child_fence_invalid")
    try:
        info = os.fstat(descriptor)
        path_info = guard.path.lstat()
    except OSError as exc:
        raise ChoreographyError("controller_child_fence_invalid") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or (info.st_dev, info.st_ino) != (guard.device, guard.inode)
        or (path_info.st_dev, path_info.st_ino) != (guard.device, guard.inode)
    ):
        raise ChoreographyError("controller_child_fence_invalid")
    inherited = tuple(dict.fromkeys((descriptor, *pass_fds)))
    wrapper = [
        sys.executable,
        "-c",
        _LOCK_KEEPER_SUPERVISOR,
        str(descriptor),
        str(guard.device),
        str(guard.inode),
        _digest(str(guard.path.resolve(strict=True)).encode("utf-8")),
        str(timeout_seconds),
        ",".join(str(value) for value in pass_fds),
        *argv,
    ]
    try:
        run_kwargs: dict[str, object] = {
            "check": False,
            "cwd": cwd,
            "env": dict(env),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": timeout_seconds + 30,
            "pass_fds": inherited,
        }
        if input_payload is None:
            run_kwargs["stdin"] = subprocess.DEVNULL
        else:
            run_kwargs["input"] = input_payload
        return subprocess.run(wrapper, **run_kwargs)  # type: ignore[arg-type]
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChoreographyError("controller_command_failed") from exc


def _run_local_release_tool(
    root: Path,
    *,
    relative: str,
    expected_sha256: str,
    manifest_entries: Mapping[str, str],
    arguments: Sequence[str],
    timeout_seconds: int = 3600,
    controller_guard: _ControllerGuard | None = None,
) -> subprocess.CompletedProcess[bytes]:
    def read_payload(path: Path) -> bytes:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or bool(before.st_mode & 0o022)
                or before.st_size > 20_000_000
            ):
                raise ChoreographyError("controller_payload_file_invalid")
            payload = b""
            while len(payload) < before.st_size:
                chunk = os.read(descriptor, before.st_size - len(payload))
                if not chunk:
                    break
                payload += chunk
            after = os.fstat(descriptor)
            if (
                len(payload) != before.st_size
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
                raise ChoreographyError("controller_payload_file_drift")
            return payload
        finally:
            os.close(descriptor)

    def verify_payload() -> None:
        for payload_relative, payload_digest in sorted(manifest_entries.items()):
            payload_path = root / payload_relative
            payload = read_payload(payload_path)
            if _digest(payload) != payload_digest:
                raise ChoreographyError("controller_payload_dependency_drift")

    verify_payload()
    path = root / relative
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ChoreographyError("controller_tool_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or bool(before.st_mode & 0o022)
        ):
            raise ChoreographyError("controller_tool_invalid")
        observed = sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed.update(chunk)
        if observed.hexdigest() != expected_sha256:
            raise ChoreographyError("controller_tool_digest_mismatch")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            child_environment = {
                "HOME": os.environ.get("HOME", "/root"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(root),
            }
            argv = [sys.executable, f"/proc/self/fd/{descriptor}", *arguments]
            completed = (
                _run_guarded_process(
                    argv,
                    cwd=root,
                    env=child_environment,
                    pass_fds=(descriptor,),
                    timeout_seconds=timeout_seconds,
                    guard=controller_guard,
                )
                if controller_guard is not None
                else subprocess.run(
                    argv,
                    check=False,
                    cwd=root,
                    env=child_environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout_seconds,
                    pass_fds=(descriptor,),
                )
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ChoreographyError("controller_command_failed") from exc
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ChoreographyError("controller_tool_changed_during_execution")
        verify_payload()
        return completed
    finally:
        os.close(descriptor)


_REMOTE_RELEASE_TOOL_SUPERVISOR = r"""
import hashlib,json,os,stat,subprocess,sys
root,manifest_sha,tool_sha,relative,release_sha,release_tree,*arguments=sys.argv[1:]
def fail(code):
    print(json.dumps({'schema':'control_release_supervisor/1.0','status':'BLOCKED','reason_code':code,'secrets_disclosed':False},sort_keys=True,separators=(',',':')),file=sys.stderr)
    raise SystemExit(72)
if not os.path.isabs(root) or os.path.realpath(root)!=root or os.path.basename(root)!=release_sha:
    fail('root_invalid')
ri=os.lstat(root)
if not stat.S_ISDIR(ri.st_mode) or ri.st_uid!=os.geteuid() or stat.S_IMODE(ri.st_mode)!=0o700:
    fail('root_invalid')
def read_regular(path,maximum):
    d=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        before=os.fstat(d)
        if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid() or before.st_nlink!=1 or bool(before.st_mode&0o022) or before.st_size>maximum:
            fail('file_invalid')
        data=b''
        while len(data)<=before.st_size:
            chunk=os.read(d,before.st_size+1-len(data))
            if not chunk: break
            data+=chunk
        after=os.fstat(d)
        if len(data)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            fail('file_drift')
        return data
    finally:
        os.close(d)
manifest=read_regular(os.path.join(root,'control-payload.sha256'),2_000_000)
if hashlib.sha256(manifest).hexdigest()!=manifest_sha:
    fail('manifest_digest')
entries={}
for line in manifest.decode('utf-8').splitlines():
    parts=line.split('  ',1)
    if len(parts)!=2 or not all(c in '0123456789abcdef' for c in parts[0]) or len(parts[0])!=64 or not parts[1].startswith('./') or parts[1][2:] in entries or '..' in parts[1][2:].split('/'):
        fail('manifest_invalid')
    entries[parts[1][2:]]=parts[0]
if entries.get(relative)!=tool_sha:
    fail('tool_manifest_binding')
def verify_payload():
    for rel,digest in sorted(entries.items()):
        path=os.path.join(root,rel)
        if os.path.commonpath((root,os.path.realpath(path)))!=root or hashlib.sha256(read_regular(path,20_000_000)).hexdigest()!=digest:
            fail('payload_dependency_drift')
verify_payload()
pair=json.loads(read_regular(os.path.join(root,'market-pipeline-release-pair-receipt.json'),2_000_000))
if pair.get('schema') not in {'market_pipeline_release_pair/1.0','market_pipeline_release_pair/1.1','market_pipeline_primary_release_pair/1.0','market_pipeline_primary_release_pair/1.1'} or pair.get('release_sha')!=release_sha or pair.get('release_tree')!=release_tree or pair.get('secrets_disclosed') is not False:
    fail('pair_binding')
if str(pair.get('schema') or '').startswith('market_pipeline_primary_release_pair') and (pair.get('feed_mode')!='PRIVATE_PRIMARY' or pair.get('product_authority_changed') is not False):
    fail('pair_binding')
path=os.path.join(root,relative)
d=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
try:
    before=os.fstat(d)
    if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid() or before.st_nlink!=1 or bool(before.st_mode&0o022):
        fail('tool_invalid')
    h=hashlib.sha256()
    while True:
        chunk=os.read(d,1024*1024)
        if not chunk: break
        h.update(chunk)
    if h.hexdigest()!=tool_sha:
        fail('tool_digest')
    os.lseek(d,0,os.SEEK_SET)
    child_env={'HOME':os.environ.get('HOME','/root'),'LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PATH':'/usr/bin:/bin','PYTHONPATH':root}
    completed=subprocess.run([sys.executable,f'/proc/self/fd/{d}',*arguments],cwd=root,env=child_env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(d,))
    after=os.fstat(d)
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
        fail('tool_drift')
    verify_payload()
    sys.stdout.buffer.write(completed.stdout);sys.stderr.buffer.write(completed.stderr)
    raise SystemExit(completed.returncode)
finally:
    os.close(d)
""".strip()


def _run_command(
    command: Mapping[str, object],
    *,
    local_control_root: Path,
    remote_control_root: Path,
    control_entries: Mapping[str, str],
    control_manifest_sha256: str,
    release_sha: str,
    release_tree: str,
    ssh_argv: Sequence[str],
    timeout_seconds: int = 3600,
    controller_guard: _ControllerGuard | None = None,
) -> dict[str, object]:
    if isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 3600:
        raise ChoreographyError("controller_command_timeout_invalid")
    tool = str(command["tool"])
    arguments = list(command["arguments"])
    relative = f"scripts/{tool}"
    tool_digest = str(control_entries.get(relative) or "")
    if not HEX64.fullmatch(tool_digest):
        raise ChoreographyError("controller_tool_not_release_tracked")
    if command["host"] == "local":
        completed = _run_local_release_tool(
            local_control_root,
            relative=relative,
            expected_sha256=tool_digest,
            manifest_entries=control_entries,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            controller_guard=controller_guard,
        )
    else:
        remote_root = str(command.get("remote_release_root") or "")
        if remote_root != str(remote_control_root):
            raise ChoreographyError("plan_remote_release_root_invalid")
        remote_command = " ".join(
            shlex.quote(value)
            for value in (
                REMOTE_PYTHON,
                "-c",
                _REMOTE_RELEASE_TOOL_SUPERVISOR,
                remote_root,
                control_manifest_sha256,
                tool_digest,
                relative,
                release_sha,
                release_tree,
                *arguments,
            )
        )
        remote_environment = {
            "HOME": os.environ.get("HOME", "/root"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
        }
        try:
            completed = (
                _run_guarded_process(
                    [*ssh_argv, remote_command],
                    cwd=local_control_root,
                    env=remote_environment,
                    pass_fds=(),
                    timeout_seconds=timeout_seconds,
                    guard=controller_guard,
                )
                if controller_guard is not None
                else subprocess.run(
                    [*ssh_argv, remote_command],
                    check=False,
                    cwd=local_control_root,
                    env=remote_environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout_seconds,
                )
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ChoreographyError("controller_command_failed") from exc
    if completed.returncode != 0:
        if not (
            tool == "promote_production_private_primary_product.py"
            and completed.returncode == 3
        ):
            raise ChoreographyError("controller_command_failed")
    result = _command_result(completed.stdout)
    _validate_command_result(command, result)
    return result


def _validate_command_result(
    command: Mapping[str, object], result: Mapping[str, object]
) -> None:
    """Fail closed on a successful exit that did not prove its own result."""

    tool, action, role = _signature(command)
    status = str(result.get("status") or "").upper()
    if status in {"", "FAIL", "FAILED", "BLOCKED", "BLOCKED_MANUAL"}:
        raise ChoreographyError("controller_command_result_failed")
    if result.get("secrets_disclosed") is True:
        raise ChoreographyError("controller_command_result_secret_bearing")
    if tool == "backup_market_pipeline_archive.py":
        if (
            status != "PASS"
            or result.get("backup_status") != "PASS"
            or not HEX64.fullmatch(str(result.get("artifact_sha256") or ""))
            or isinstance(result.get("artifact_size_bytes"), bool)
            or not isinstance(result.get("artifact_size_bytes"), int)
            or result["artifact_size_bytes"] <= 0
            or not re.fullmatch(
                r"market-archive-before-[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.dump",
                str(result.get("artifact_name") or ""),
            )
            or result.get("database_mutated") is not False
            or result.get("services_started") is not False
        ):
            raise ChoreographyError("controller_backup_restore_smoke_invalid")
    elif tool == "crypt_market_pipeline_backup.py":
        if (
            status != "PASS"
            or result.get("schema") != "market_pipeline_backup_encryption/1.1"
            or result.get("algorithm") != "AES-256-CBC+PBKDF2-HMAC-SHA256"
            or result.get("kdf") != "PBKDF2-HMAC-SHA256"
            or result.get("kdf_iterations") != 600000
            or result.get("plaintext_materialized_offhost") is not False
            or not HEX64.fullmatch(str(result.get("plaintext_sha256") or ""))
            or not HEX64.fullmatch(str(result.get("ciphertext_sha256") or ""))
            or not HEX64.fullmatch(
                str(result.get("authentication_hmac_sha256") or "")
            )
            or isinstance(result.get("plaintext_size_bytes"), bool)
            or not isinstance(result.get("plaintext_size_bytes"), int)
            or result["plaintext_size_bytes"] <= 0
            or isinstance(result.get("ciphertext_size_bytes"), bool)
            or not isinstance(result.get("ciphertext_size_bytes"), int)
            or result["ciphertext_size_bytes"] <= 0
        ):
            raise ChoreographyError("controller_offhost_encryption_invalid")
    elif tool == "migrate_market_pipeline_archive.py":
        second = result.get("second_pass")
        if (
            result.get("schema") != "market_pipeline_migration_receipt/1.0"
            or status != "PASS"
            or not isinstance(second, dict)
            or set(second) != {"status", "version", "table_count"}
            or second.get("status") != "already_current"
            or second.get("version") != 3
            or second.get("table_count") != 28
            or result.get("product_authority_changed") is not False
            or result.get("telegram_capture_cutover_authorized") is not False
        ):
            raise ChoreographyError("controller_migration_second_pass_not_noop")
    elif tool == "rollout_market_pipeline_shadow.py":
        allowed = {
            "prepare": {"PREPARED"},
            "start": {"IN_PROGRESS", "PASS"},
            "verify": {"PASS"},
            "rollback": {"ROLLED_BACK"},
        }[action]
        if (
            status not in allowed
            or result.get("feed_mode") != "PRIVATE_PRIMARY"
            or result.get("private_shadow_only") is not False
            or result.get("capture_services_started") is not False
            or result.get("product_authority_changed") is not False
        ):
            raise ChoreographyError("controller_base_service_start_invalid")
    elif tool == "upgrade_market_pipeline_bluegreen.py":
        allowed = {
            "plan": "PLANNED",
            "quiesce-workload": "WORKLOAD_QUIESCED",
            "quiesce-database": "DATABASE_QUIESCED",
            "prepare-capture-authority": "CAPTURE_AUTHORITY_PREPARED",
            "authorize-captures": "CAPTURES_AUTHORIZED",
            "start-captures": "CAPTURES_RUNNING",
            "verify": "PASS",
            "rollback": "ROLLED_BACK",
        }
        if (
            status != allowed[action]
            or result.get("role") != role
            or not HEX40.fullmatch(str(result.get("release_sha") or ""))
            or result.get("product_authority_changed") is not False
            or result.get("state_deleted") is not False
        ):
            raise ChoreographyError("controller_bluegreen_result_invalid")
        if action == "prepare-capture-authority" and any(
            not HEX64.fullmatch(str(result.get(key) or ""))
            for key in (
                "prepared_bluegreen_journal_sha256",
                "marker_authority_sha256",
            )
        ):
            raise ChoreographyError("controller_bluegreen_result_invalid")
    elif tool == "quiesce_production_legacy_market_collectors.py":
        allowed = {
            "quiesce": "QUIESCED",
            "verify": "QUIESCED",
            "prepare-authority": "AUTHORITY_TRANSFERRING",
            "mark-authority-transferred": "AUTHORITY_TRANSFERRED",
            "mark-authority-restored": "QUIESCED",
            "commit": "PRIMARY_COMMITTED",
            "restore": "RESTORED",
            "recover": "RESTORED_AFTER_QUIESCE_FAILURE",
        }
        if (
            status != allowed[action]
            or result.get("host_role") != role
            or not HEX40.fullmatch(str(result.get("release_sha") or ""))
            or not HEX64.fullmatch(str(result.get("journal_sha256") or ""))
        ):
            raise ChoreographyError("controller_legacy_handoff_result_invalid")
        expected_inactive = action not in {
            "restore",
            "recover",
            "mark-authority-restored",
        }
        if result.get("all_legacy_collectors_inactive") is not expected_inactive:
            raise ChoreographyError("controller_legacy_handoff_result_invalid")
    elif tool == "audit_production_market_catchup.py":
        expected_schema = {
            "web": "production_market_catchup_web/1.3",
            "bot": "production_market_catchup_bot/1.1",
            "settle": "production_market_catchup_settle/1.0",
            "verify": "production_market_catchup_verification/1.2",
        }[action]
        if (
            status != "PASS"
            or result.get("schema") != expected_schema
            or not HEX64.fullmatch(str(result.get("artifact_sha256") or ""))
        ):
            raise ChoreographyError("controller_catchup_result_invalid")
    elif tool == "observe_production_private_primary.py":
        if (
            status != "PASS"
            or result.get("schema")
            != "production_private_primary_observation/1.0"
            or result.get("role") != role
            or not HEX64.fullmatch(str(result.get("artifact_sha256") or ""))
        ):
            raise ChoreographyError("controller_observation_result_invalid")
    elif tool in {
        "check_production_coin_inference_readiness.py",
        "run_release_bound_product_readiness.py",
    }:
        if (
            action
            not in {"private-primary-consumer", "execute"}
            or role not in {"bot", "web"}
            or status != "READY"
            or result.get("authority") != "PRIVATE_PRIMARY"
            or result.get("rate_cell_count") != 14
            or result.get("required_source_input_trace_count") != 9
            or not HEX64.fullmatch(
                str(result.get("source_input_trace_sha256") or "")
            )
            or isinstance(result.get("snapshot_age_seconds"), bool)
            or not isinstance(result.get("snapshot_age_seconds"), (int, float))
            or not 0 <= float(result["snapshot_age_seconds"]) <= 120
        ):
            raise ChoreographyError("controller_private_primary_readiness_invalid")
    elif tool == "reconcile_estimator_snapshot_publication_outbox.py":
        if (
            result.get("schema")
            != "estimator_snapshot_publication_reconciliation/1.0"
            or not HEX64.fullmatch(str(result.get("plan_sha256") or ""))
            or (
                action == "plan"
                and (
                    status != "PLAN"
                    or result.get("repaired_count") != 0
                    or result.get("pending_after") != result.get("pending_before")
                )
            )
            or (
                action == "apply"
                and (
                    status not in {"APPLIED", "ALREADY_RECONCILED"}
                    or result.get("pending_after") != 0
                )
            )
        ):
            raise ChoreographyError("controller_snapshot_outbox_not_drained")
    elif tool == "verify_production_private_primary_promotion.py":
        if (
            result.get("schema")
            != "production_private_primary_promotion_verification/1.0"
            or status != "PASS"
        ):
            raise ChoreographyError("controller_promotion_verification_invalid")
    elif tool == "promote_production_private_primary_product.py":
        if (
            result.get("schema")
            != "production_private_primary_product_promotion/1.0"
            or status not in {"PASS", "ROLLED_BACK"}
            or (
                status == "ROLLED_BACK"
                and result.get("legacy_redeploy_completed") is not True
            )
        ):
            raise ChoreographyError("controller_product_promotion_invalid")


def _materialize_command(
    command: Mapping[str, object],
    *,
    phases: Sequence[Mapping[str, object]],
    context: Mapping[str, object],
    ssh_argv: Sequence[str],
) -> dict[str, object]:
    """Inject only digests produced by earlier phases into the next command."""

    materialized = dict(command)
    arguments = list(command["arguments"])
    signature = _signature(command)
    web_legacy = _command_path(
        phases,
        ("quiesce_production_legacy_market_collectors.py", "quiesce", "web"),
        "--journal",
    )
    bot_legacy = _command_path(
        phases,
        ("quiesce_production_legacy_market_collectors.py", "quiesce", "bot"),
        "--journal",
    )
    web_bluegreen = _command_path(
        phases,
        ("upgrade_market_pipeline_bluegreen.py", "plan", "web"),
        "--journal",
    )
    promotion_receipt = _command_path(
        phases,
        ("verify_production_private_primary_promotion.py", "verify", None),
        "--receipt",
    )
    if signature == ("crypt_market_pipeline_backup.py", "encrypt", None):
        (
            source,
            remote_artifact,
            remote_receipt,
            _local_artifact,
            _local_receipt,
        ) = _backup_encryption_paths(phases, ssh_argv)
        arguments = _set_option(arguments, "--source", str(source))
        arguments = _set_option(
            arguments, "--destination", str(remote_artifact)
        )
        arguments = _set_option(arguments, "--receipt", str(remote_receipt))
    elif signature == ("crypt_market_pipeline_backup.py", "verify", None):
        arguments = _prepare_offhost_verification(
            arguments, phases=phases, ssh_argv=ssh_argv
        )
    elif signature == (
        "upgrade_market_pipeline_bluegreen.py",
        "quiesce-database",
        "web",
    ):
        backup_receipt = _command_path(
            phases,
            ("backup_market_pipeline_archive.py", "create", None),
            "--receipt",
        )
        arguments = _set_option(arguments, "--backup-receipt", str(backup_receipt))
        arguments = _set_option(
            arguments,
            "--expected-backup-receipt-sha256",
            _hex_context(context, "backup_receipt_sha256"),
        )
        arguments = _set_option(
            arguments,
            "--expected-offhost-backup-receipt-sha256",
            _hex_context(context, "offhost_receipt_sha256"),
        )
    elif signature == ("migrate_market_pipeline_archive.py", "execute", None):
        backup_receipt = _command_path(
            phases,
            ("backup_market_pipeline_archive.py", "create", None),
            "--receipt",
        )
        arguments = _set_option(arguments, "--backup-receipt", str(backup_receipt))
        arguments = _set_option(
            arguments,
            "--offhost-receipt-sha256",
            _hex_context(context, "offhost_receipt_sha256"),
        )
    elif signature == (
        "audit_production_market_catchup.py",
        "settle",
        None,
    ):
        previous_web, _current_web = _command_paths(
            phases,
            ("audit_production_market_catchup.py", "web", None),
            "--output",
        )
        previous_bot, _current_bot = _command_paths(
            phases,
            ("audit_production_market_catchup.py", "bot", None),
            "--output",
        )
        previous_web_mirror = Path(
            str(_option(arguments, "--previous-web") or "")
        )
        if not previous_web_mirror.is_absolute():
            raise ChoreographyError("plan_artifact_binding_invalid")
        mirrored = _mirror_local_exact(
            _remote_read(previous_web, ssh_argv), previous_web_mirror
        )
        if mirrored != _hex_context(context, "previous_web_sha256"):
            raise ChoreographyError("controller_local_mirror_mismatch")
        if Path(str(_option(arguments, "--previous-bot") or "")) != previous_bot:
            raise ChoreographyError("plan_artifact_binding_invalid")
        arguments = _set_option(
            arguments, "--previous-web-sha256", mirrored
        )
        arguments = _set_option(
            arguments,
            "--previous-bot-sha256",
            _hex_context(context, "previous_bot_sha256"),
        )
    elif signature == (
        "audit_production_market_catchup.py",
        "verify",
        None,
    ):
        previous_web, current_web = _command_paths(
            phases,
            ("audit_production_market_catchup.py", "web", None),
            "--output",
        )
        previous_bot, current_bot = _command_paths(
            phases,
            ("audit_production_market_catchup.py", "bot", None),
            "--output",
        )
        current_web_mirror = Path(str(_option(arguments, "--web") or ""))
        if not current_web_mirror.is_absolute():
            raise ChoreographyError("plan_artifact_binding_invalid")
        mirrored = _mirror_local_exact(
            _remote_read(current_web, ssh_argv), current_web_mirror
        )
        if mirrored != _hex_context(context, "current_web_sha256"):
            raise ChoreographyError("controller_local_mirror_mismatch")
        expected_paths = {
            "--bot": current_bot,
            "--previous-bot": previous_bot,
        }
        for option, expected in expected_paths.items():
            if Path(str(_option(arguments, option) or "")) != expected:
                raise ChoreographyError("plan_artifact_binding_invalid")
        previous_web_mirror = Path(
            str(_option(arguments, "--previous-web") or "")
        )
        if _digest(_secure_read(
            previous_web_mirror, label="controller_previous_web_mirror"
        )) != _hex_context(context, "previous_web_sha256"):
            raise ChoreographyError("controller_local_mirror_mismatch")
        arguments = _set_option(arguments, "--web-sha256", mirrored)
        arguments = _set_option(
            arguments, "--bot-sha256", _hex_context(context, "current_bot_sha256")
        )
        arguments = _set_option(
            arguments,
            "--previous-web-sha256",
            _hex_context(context, "previous_web_sha256"),
        )
        arguments = _set_option(
            arguments,
            "--previous-bot-sha256",
            _hex_context(context, "previous_bot_sha256"),
        )
    elif signature == (
        "upgrade_market_pipeline_bluegreen.py",
        "prepare-capture-authority",
        "web",
    ):
        arguments = _set_option(
            arguments,
            "--web-legacy-collector-receipt",
            str(web_legacy),
        )
        arguments = _set_option(
            arguments,
            "--expected-web-legacy-collector-receipt-sha256",
            _hex_context(context, "web_legacy_journal_sha256"),
        )
    elif signature == (
        "quiesce_production_legacy_market_collectors.py",
        "prepare-authority",
        "bot",
    ):
        bot_bluegreen_mirror = Path(
            str(_option(arguments, "--bluegreen-journal") or "")
        )
        if not bot_bluegreen_mirror.is_absolute():
            raise ChoreographyError("plan_artifact_binding_invalid")
        mirrored = _mirror_local_exact(
            _remote_read(web_bluegreen, ssh_argv), bot_bluegreen_mirror
        )
        if mirrored != _hex_context(
            context, "prepared_bluegreen_journal_sha256"
        ):
            raise ChoreographyError("controller_local_mirror_mismatch")
        arguments = _set_option(
            arguments,
            "--expected-journal-sha256",
            _hex_context(context, "bot_legacy_journal_sha256"),
        )
        arguments = _set_option(
            arguments,
            "--expected-bluegreen-journal-sha256",
            mirrored,
        )
        arguments = _set_option(
            arguments,
            "--marker-authority-sha256",
            _hex_context(context, "marker_authority_sha256"),
        )
    elif signature == (
        "upgrade_market_pipeline_bluegreen.py",
        "authorize-captures",
        "web",
    ):
        remote_bot_receipt = Path(
            str(_option(arguments, "--bot-legacy-collector-receipt") or "")
        )
        if not remote_bot_receipt.is_absolute():
            raise ChoreographyError("plan_artifact_binding_invalid")
        mirrored = _mirror_remote_exact(bot_legacy, remote_bot_receipt, ssh_argv)
        if mirrored != _hex_context(context, "bot_legacy_journal_sha256"):
            raise ChoreographyError("controller_remote_mirror_mismatch")
        arguments = _set_option(
            arguments,
            "--web-legacy-collector-receipt",
            str(web_legacy),
        )
        arguments = _set_option(
            arguments,
            "--expected-web-legacy-collector-receipt-sha256",
            _hex_context(context, "web_legacy_journal_sha256"),
        )
        arguments = _set_option(
            arguments,
            "--expected-bot-legacy-collector-receipt-sha256",
            mirrored,
        )
    elif signature == (
        "quiesce_production_legacy_market_collectors.py",
        "mark-authority-transferred",
        "bot",
    ):
        bot_bluegreen_mirror = Path(
            str(_option(arguments, "--bluegreen-journal") or "")
        )
        if not bot_bluegreen_mirror.is_absolute():
            raise ChoreographyError("plan_artifact_binding_invalid")
        mirrored = _mirror_local_exact(
            _remote_read(web_bluegreen, ssh_argv), bot_bluegreen_mirror
        )
        if mirrored != _hex_context(
            context, "authorized_bluegreen_journal_sha256"
        ):
            raise ChoreographyError("controller_local_mirror_mismatch")
        arguments = _set_option(
            arguments,
            "--expected-journal-sha256",
            _hex_context(context, "bot_legacy_journal_sha256"),
        )
        arguments = _set_option(
            arguments,
            "--expected-bluegreen-journal-sha256",
            mirrored,
        )
        arguments = _set_option(
            arguments,
            "--marker-authority-sha256",
            _hex_context(context, "marker_authority_sha256"),
        )
    elif signature == (
        "reconcile_estimator_snapshot_publication_outbox.py",
        "apply",
        None,
    ):
        arguments = _set_option(
            arguments,
            "--expected-plan-sha256",
            _hex_context(context, "outbox_plan_sha256"),
        )
    elif signature[0] in {
        "check_production_coin_inference_readiness.py",
        "run_release_bound_product_readiness.py",
    }:
        snapshot_option = (
            "--expected-snapshot-sha256"
            if signature[0] == "run_release_bound_product_readiness.py"
            else "--expected-sha256"
        )
        arguments = _set_option(
            arguments,
            snapshot_option,
            _hex_context(context, "common_snapshot_sha256"),
        )
    elif signature == (
        "verify_production_private_primary_promotion.py",
        "verify",
        None,
    ):
        web_plan = next(
            command
            for phase in phases
            for command in phase.get("commands", [])
            if isinstance(command, dict)
            and _signature(command)
            == ("upgrade_market_pipeline_bluegreen.py", "plan", "web")
        )
        bot_plan = next(
            command
            for phase in phases
            for command in phase.get("commands", [])
            if isinstance(command, dict)
            and _signature(command)
            == ("upgrade_market_pipeline_bluegreen.py", "plan", "bot")
        )
        web_observation = _command_path(
            phases,
            ("observe_production_private_primary.py", "execute", "web"),
            "--output",
        )
        bot_observation = _command_path(
            phases,
            ("observe_production_private_primary.py", "execute", "bot"),
            "--output",
        )
        web_snapshot = _command_path(
            phases,
            ("observe_production_private_primary.py", "execute", "web"),
            "--snapshot",
        )
        bot_snapshot = _command_path(
            phases,
            ("observe_production_private_primary.py", "execute", "bot"),
            "--snapshot",
        )
        mirrors = (
            (web_bluegreen, "--web-journal"),
            (web_observation, "--web-health"),
            (web_snapshot, "--web-snapshot"),
            (
                Path(str(_option(web_plan["arguments"], "--new-env") or "")),
                "--web-env",
            ),
        )
        for source_path, destination_option in mirrors:
            destination = Path(
                str(_option(arguments, destination_option) or "")
            )
            if not source_path.is_absolute() or not destination.is_absolute():
                raise ChoreographyError("plan_artifact_binding_invalid")
            _mirror_local_exact(_remote_read(source_path, ssh_argv), destination)
        expected_local_paths = {
            "--bot-journal": Path(
                str(_option(bot_plan["arguments"], "--journal") or "")
            ),
            "--bot-health": bot_observation,
            "--bot-snapshot": bot_snapshot,
        }
        for option, expected in expected_local_paths.items():
            if Path(str(_option(arguments, option) or "")) != expected:
                raise ChoreographyError("plan_artifact_binding_invalid")
        web_snapshot_digest = _digest(
            _secure_read(
                Path(str(_option(arguments, "--web-snapshot"))),
                label="controller_web_snapshot_mirror",
            )
        )
        bot_snapshot_digest = _digest(
            _secure_read(bot_snapshot, label="controller_bot_snapshot")
        )
        if (
            web_snapshot_digest != bot_snapshot_digest
            or web_snapshot_digest
            != _hex_context(context, "common_snapshot_sha256")
        ):
            raise ChoreographyError("controller_snapshot_pair_mismatch")
        arguments = _set_option(
            arguments,
            "--expected-catchup-receipt-sha256",
            _hex_context(context, "catchup_receipt_sha256"),
        )
    elif signature[0:2] == (
        "quiesce_production_legacy_market_collectors.py",
        "commit",
    ):
        role = signature[2]
        assert role in {"bot", "web"}
        primary_path = Path(
            str(_option(arguments, "--primary-verification") or "")
        )
        if role == "web":
            _mirror_remote_exact(promotion_receipt, primary_path, ssh_argv)
        elif primary_path != promotion_receipt:
            raise ChoreographyError("plan_artifact_binding_invalid")
        arguments = _set_option(
            arguments,
            "--expected-primary-verification-sha256",
            _hex_context(context, "promotion_verification_sha256"),
        )
    elif signature == (
        "promote_production_private_primary_product.py",
        "execute",
        None,
    ):
        web_mirror = Path(
            str(_option(arguments, "--web-maintenance-journal") or "")
        )
        web_payload = _remote_read(web_legacy, ssh_argv)
        web_digest = _mirror_local_exact(web_payload, web_mirror)
        if web_digest != _hex_context(context, "web_legacy_journal_sha256"):
            raise ChoreographyError("controller_local_mirror_mismatch")
        arguments = _set_option(arguments, "--maintenance-journal", str(bot_legacy))
        arguments = _set_option(
            arguments,
            "--expected-maintenance-journal-sha256",
            _hex_context(context, "bot_legacy_journal_sha256"),
        )
        arguments = _set_option(
            arguments,
            "--expected-web-maintenance-journal-sha256",
            web_digest,
        )
        arguments = _set_option(
            arguments,
            "--expected-promotion-receipt-sha256",
            _hex_context(context, "promotion_verification_sha256"),
        )
        arguments = _set_option(
            arguments,
            "--expected-catchup-receipt-sha256",
            _hex_context(context, "catchup_receipt_sha256"),
        )
    materialized["arguments"] = arguments
    return materialized


def _record_dynamic_context(
    command: Mapping[str, object],
    result: Mapping[str, object],
    *,
    context: dict[str, object],
    phases: Sequence[Mapping[str, object]],
    ssh_argv: Sequence[str],
) -> None:
    signature = _signature(command)
    tool, action, role = signature
    if tool == "quiesce_production_legacy_market_collectors.py":
        digest = str(result.get("journal_sha256") or "")
        if HEX64.fullmatch(digest):
            context[f"{role}_legacy_journal_sha256"] = digest
    if tool == "backup_market_pipeline_archive.py" and action == "verify":
        _backup, backup_digest = _backup_receipt_binding(phases, ssh_argv)
        context["backup_receipt_sha256"] = backup_digest
    if tool == "crypt_market_pipeline_backup.py" and action == "verify":
        context["offhost_receipt_sha256"] = _write_offhost_receipt(
            result=result,
            phases=phases,
            ssh_argv=ssh_argv,
        )
    if tool == "upgrade_market_pipeline_bluegreen.py" and action == "prepare-capture-authority":
        for key in (
            "prepared_bluegreen_journal_sha256",
            "marker_authority_sha256",
        ):
            context[key] = _hex_context(result, key)
    if tool == "upgrade_market_pipeline_bluegreen.py" and action == "authorize-captures":
        web_bluegreen = _command_path(
            phases,
            ("upgrade_market_pipeline_bluegreen.py", "plan", "web"),
            "--journal",
        )
        context["authorized_bluegreen_journal_sha256"] = _digest(
            _remote_read(web_bluegreen, ssh_argv)
        )
        web_legacy = _command_path(
            phases,
            ("quiesce_production_legacy_market_collectors.py", "quiesce", "web"),
            "--journal",
        )
        context["web_legacy_journal_sha256"] = _digest(
            _remote_read(web_legacy, ssh_argv)
        )
    if tool == "audit_production_market_catchup.py" and action in {"web", "bot"}:
        arguments = command["arguments"]
        assert isinstance(arguments, list)
        output = Path(str(_option(arguments, "--output") or ""))
        if not output.is_absolute():
            raise ChoreographyError("plan_artifact_binding_invalid")
        payload = (
            _remote_read(output, ssh_argv)
            if command["host"] == "web"
            else _secure_read(output, label="controller_catchup_output")
        )
        prefix = "web" if action == "web" else "bot"
        key = (
            f"previous_{prefix}_sha256"
            if f"previous_{prefix}_sha256" not in context
            else f"current_{prefix}_sha256"
        )
        if key in context:
            raise ChoreographyError("controller_catchup_pair_count_invalid")
        context[key] = _digest(payload)
    if tool == "audit_production_market_catchup.py" and action == "verify":
        arguments = command["arguments"]
        assert isinstance(arguments, list)
        output = Path(str(_option(arguments, "--output") or ""))
        context["catchup_receipt_sha256"] = _digest(
            _secure_read(output, label="controller_catchup_receipt")
        )
    if tool == "observe_production_private_primary.py":
        arguments = command["arguments"]
        assert isinstance(arguments, list)
        output = Path(str(_option(arguments, "--output") or ""))
        snapshot = Path(str(_option(arguments, "--snapshot") or ""))
        if not output.is_absolute() or not snapshot.is_absolute():
            raise ChoreographyError("plan_artifact_binding_invalid")
        reader = (
            (lambda path: _remote_read(path, ssh_argv))
            if command["host"] == "web"
            else (lambda path: _secure_read(path, label="controller_observation"))
        )
        context[f"{role}_observation_sha256"] = _digest(reader(output))
        snapshot_digest = _digest(reader(snapshot))
        context[f"{role}_snapshot_sha256"] = snapshot_digest
        if role == "bot":
            if snapshot_digest != _hex_context(context, "web_snapshot_sha256"):
                raise ChoreographyError("controller_snapshot_pair_mismatch")
            context["common_snapshot_sha256"] = snapshot_digest
    if tool == "reconcile_estimator_snapshot_publication_outbox.py" and action == "plan":
        arguments = command["arguments"]
        assert isinstance(arguments, list)
        receipt = Path(str(_option(arguments, "--receipt") or ""))
        context["outbox_plan_sha256"] = _digest(
            _remote_read(receipt, ssh_argv)
            if command["host"] == "web"
            else _secure_read(receipt, label="controller_outbox_plan")
        )
    if tool == "verify_production_private_primary_promotion.py":
        receipt = _command_path(
            phases,
            ("verify_production_private_primary_promotion.py", "verify", None),
            "--receipt",
        )
        context["promotion_verification_sha256"] = _digest(
            _secure_read(receipt, label="promotion_verification")
        )


def _phase_command(
    phases: Sequence[Mapping[str, object]],
    signature: tuple[str, str, str | None],
) -> Mapping[str, object]:
    matches: list[Mapping[str, object]] = []
    for phase in phases:
        commands = phase.get("commands")
        if not isinstance(commands, list):
            continue
        for command in commands:
            if isinstance(command, dict) and _signature(command) == signature:
                matches.append(command)
    if len(matches) != 1:
        raise ChoreographyError("plan_artifact_binding_missing")
    return matches[0]


def _backup_receipt_binding(
    phases: Sequence[Mapping[str, object]], ssh_argv: Sequence[str]
) -> tuple[dict[str, object], str]:
    create = _phase_command(
        phases, ("backup_market_pipeline_archive.py", "create", None)
    )
    arguments = create["arguments"]
    assert isinstance(arguments, list)
    receipt = _command_path(
        phases,
        ("backup_market_pipeline_archive.py", "create", None),
        "--receipt",
    )
    payload = _remote_read(receipt, ssh_argv)
    document = _json(payload, label="backup_receipt")
    backup = document.get("backup")
    restore = document.get("restore_smoke")
    source = document.get("source")
    source_after = document.get("source_after")
    backup_dir = Path(str(_option(arguments, "--backup-dir") or ""))
    artifact = Path(str((backup or {}).get("path") or ""))
    if (
        document.get("schema") != "market_pipeline_backup_restore/1.2"
        or document.get("status") != "PASS"
        or document.get("off_host_copy_required") is not True
        or document.get("database_mutated") is not False
        or document.get("services_started") is not False
        or document.get("secrets_disclosed") is not False
        or not isinstance(backup, dict)
        or set(backup) != {"path", "sha256", "size_bytes", "format"}
        or backup.get("format") != "postgres_custom"
        or not HEX64.fullmatch(str(backup.get("sha256") or ""))
        or isinstance(backup.get("size_bytes"), bool)
        or not isinstance(backup.get("size_bytes"), int)
        or backup["size_bytes"] <= 0
        or not backup_dir.is_absolute()
        or artifact.parent != backup_dir
        or not re.fullmatch(
            r"market-archive-before-[0-9a-f]{12}-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.dump",
            artifact.name,
        )
        or not isinstance(restore, dict)
        or restore.get("status") != "PASS"
        or restore.get("cleanup_status") != "PASS"
        or not isinstance(source, dict)
        or not isinstance(source_after, dict)
        or any(
            restore.get(key) != source.get(key)
            or source_after.get(key) != source.get(key)
            for key in (
                "schema_versions",
                "table_count",
                "fact_count",
                "table_row_counts",
                "sequence_values",
                "schema_catalog_sha256",
                "schema_objects_sha256",
            )
        )
    ):
        raise ChoreographyError("controller_backup_restore_smoke_invalid")
    return document, _digest(payload)


def _backup_encryption_paths(
    phases: Sequence[Mapping[str, object]], ssh_argv: Sequence[str]
) -> tuple[Path, Path, Path, Path, Path]:
    """Resolve nonce-bearing backup encryption names after backup creation.

    The backup tool deliberately chooses a fresh, receipt-bound basename at
    runtime.  A deterministic operation plan can therefore bind only the
    four destination directories.  The controller derives the exact remote
    ciphertext/receipt and off-host mirror names from the verified backup
    receipt; an operator-provided placeholder basename is never executed.
    """

    backup, _backup_digest = _backup_receipt_binding(phases, ssh_argv)
    artifact_row = backup.get("backup")
    if not isinstance(artifact_row, dict):
        raise ChoreographyError("controller_backup_restore_smoke_invalid")
    source = Path(str(artifact_row.get("path") or ""))
    encrypt = _phase_command(
        phases, ("crypt_market_pipeline_backup.py", "encrypt", None)
    )
    verify = _phase_command(
        phases, ("crypt_market_pipeline_backup.py", "verify", None)
    )
    encrypt_arguments = encrypt["arguments"]
    verify_arguments = verify["arguments"]
    assert isinstance(encrypt_arguments, list)
    assert isinstance(verify_arguments, list)
    planned_remote_artifact = Path(
        str(_option(encrypt_arguments, "--destination") or "")
    )
    planned_remote_receipt = Path(
        str(_option(encrypt_arguments, "--receipt") or "")
    )
    planned_local_artifact = Path(
        str(_option(verify_arguments, "--artifact") or "")
    )
    planned_local_receipt = Path(
        str(_option(verify_arguments, "--receipt") or "")
    )
    if (
        not source.is_absolute()
        or any(
            not path.is_absolute()
            for path in (
                planned_remote_artifact,
                planned_remote_receipt,
                planned_local_artifact,
                planned_local_receipt,
            )
        )
        or planned_remote_artifact.parent != planned_remote_receipt.parent
        or planned_local_artifact.parent != planned_local_receipt.parent
    ):
        raise ChoreographyError("plan_backup_offhost_binding_invalid")
    encrypted_name = source.name + ".enc"
    receipt_name = source.name + ".encryption.json"
    return (
        source,
        planned_remote_artifact.parent / encrypted_name,
        planned_remote_receipt.parent / receipt_name,
        planned_local_artifact.parent / encrypted_name,
        planned_local_receipt.parent / receipt_name,
    )


def _local_artifact_identity(path: Path) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ChoreographyError("offhost_artifact_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size <= 0
        ):
            raise ChoreographyError("offhost_artifact_invalid")
        digest = sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (
            size != before.st_size
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
            raise ChoreographyError("offhost_artifact_changed_during_read")
        return size, digest.hexdigest()
    finally:
        os.close(descriptor)


def _copy_remote_artifact_exact(
    source: Path,
    destination: Path,
    ssh_argv: Sequence[str],
    *,
    expected_size: int,
    expected_sha256: str,
) -> str:
    """Stream an immutable remote inode to a durable local off-host file."""

    _secure_parent(destination, label="offhost_artifact")
    code = """
import hashlib,json,os,stat,sys
p=sys.argv[1]
d=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0))
s=os.fstat(d)
assert stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1 and s.st_size>0
h=hashlib.sha256(); n=0
while True:
 b=os.read(d,1024*1024)
 if not b: break
 h.update(b); n+=len(b)
t=os.fstat(d)
assert n==s.st_size and (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==(t.st_dev,t.st_ino,t.st_size,t.st_mtime_ns,t.st_ctime_ns)
os.lseek(d,0,os.SEEK_SET)
sys.stdout.buffer.write((json.dumps({'size':n,'sha256':h.hexdigest()},sort_keys=True,separators=(',',':'))+'\\n').encode('ascii')); sys.stdout.buffer.flush()
left=n
while left:
 b=os.read(d,min(1024*1024,left)); assert b
 sys.stdout.buffer.write(b); left-=len(b)
u=os.fstat(d); os.close(d)
assert (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==(u.st_dev,u.st_ino,u.st_size,u.st_mtime_ns,u.st_ctime_ns)
""".strip()
    remote_command = " ".join(
        shlex.quote(value) for value in (REMOTE_PYTHON, "-c", code, str(source))
    )
    temporary = destination.parent / f".{destination.name}.{os.getpid()}.incoming"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    process: subprocess.Popen[bytes] | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        if not ssh_argv or str(ssh_argv[0]) != SSH_BINARY:
            raise ChoreographyError("plan_web_ssh_invalid")
        inherited_lock = ()
        guard = _ACTIVE_CONTROLLER_GUARD
        if (
            guard is not None
            and guard.descriptor is not None
        ):
            inherited_lock = (guard.descriptor,)
        process = subprocess.Popen(
            [*ssh_argv, remote_command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=inherited_lock,
        )
        if process.stdout is None:
            raise ChoreographyError("offhost_copy_stream_unavailable")
        header = process.stdout.readline(1025)
        if not header.endswith(b"\n") or len(header) > 1024:
            raise ChoreographyError("offhost_copy_header_invalid")
        metadata = _json(header, label="offhost_copy_header")
        observed_size = metadata.get("size")
        observed_digest = str(metadata.get("sha256") or "")
        if (
            set(metadata) != {"size", "sha256"}
            or isinstance(observed_size, bool)
            or not isinstance(observed_size, int)
            or observed_size <= 0
            or not HEX64.fullmatch(observed_digest)
            or expected_size != observed_size
            or expected_sha256 != observed_digest
        ):
            raise ChoreographyError("offhost_copy_header_invalid")
        observed = sha256()
        remaining = observed_size
        while remaining:
            chunk = process.stdout.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ChoreographyError("offhost_copy_truncated")
            offset = 0
            while offset < len(chunk):
                offset += os.write(descriptor, chunk[offset:])
            observed.update(chunk)
            remaining -= len(chunk)
        if process.stdout.read(1):
            raise ChoreographyError("offhost_copy_trailing_data")
        stderr = process.stderr.read() if process.stderr is not None else b""
        if process.wait(timeout=60) != 0 or stderr:
            raise ChoreographyError("offhost_copy_remote_failed")
        if observed.hexdigest() != observed_digest:
            raise ChoreographyError("offhost_copy_digest_mismatch")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if destination.exists() or destination.is_symlink():
            size, digest = _local_artifact_identity(destination)
            if (size, digest) != (observed_size, observed_digest):
                raise ChoreographyError("offhost_copy_existing_drift")
            temporary.unlink()
        else:
            os.replace(temporary, destination)
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return observed_digest
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChoreographyError("offhost_copy_failed") from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if process is not None and process.stdout is not None:
            process.stdout.close()
        if process is not None and process.stderr is not None:
            process.stderr.close()
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _prepare_offhost_verification(
    arguments: Sequence[str],
    *,
    phases: Sequence[Mapping[str, object]],
    ssh_argv: Sequence[str],
) -> list[str]:
    backup, _backup_digest = _backup_receipt_binding(phases, ssh_argv)
    source_artifact = backup["backup"]
    assert isinstance(source_artifact, dict)
    (
        _source,
        encrypted_remote,
        encryption_receipt_remote,
        encrypted_local,
        encryption_receipt_local,
    ) = _backup_encryption_paths(phases, ssh_argv)
    receipt_payload = _remote_read(encryption_receipt_remote, ssh_argv)
    receipt = _json(receipt_payload, label="offhost_encryption_receipt")
    if (
        receipt.get("schema") != "market_pipeline_backup_encryption/1.1"
        or receipt.get("plaintext_sha256") != source_artifact.get("sha256")
        or receipt.get("plaintext_size_bytes") != source_artifact.get("size_bytes")
        or isinstance(receipt.get("ciphertext_size_bytes"), bool)
        or not isinstance(receipt.get("ciphertext_size_bytes"), int)
        or receipt["ciphertext_size_bytes"] <= 0
        or not HEX64.fullmatch(str(receipt.get("ciphertext_sha256") or ""))
        or receipt.get("plaintext_materialized_offhost") is not False
        or receipt.get("secrets_disclosed") is not False
    ):
        raise ChoreographyError("controller_offhost_encryption_invalid")
    _copy_remote_artifact_exact(
        encrypted_remote,
        encrypted_local,
        ssh_argv,
        expected_size=receipt["ciphertext_size_bytes"],
        expected_sha256=str(receipt["ciphertext_sha256"]),
    )
    _mirror_local_exact(receipt_payload, encryption_receipt_local)
    result = _set_option(arguments, "--artifact", str(encrypted_local))
    result = _set_option(result, "--receipt", str(encryption_receipt_local))
    result = _set_option(
        result,
        "--expected-plaintext-sha256",
        str(source_artifact["sha256"]),
    )
    return _set_option(
        result,
        "--expected-plaintext-size-bytes",
        str(source_artifact["size_bytes"]),
    )


def _write_offhost_receipt(
    *,
    result: Mapping[str, object],
    phases: Sequence[Mapping[str, object]],
    ssh_argv: Sequence[str],
) -> str:
    backup, backup_digest = _backup_receipt_binding(phases, ssh_argv)
    (
        _source,
        _remote_artifact,
        _remote_receipt,
        artifact,
        encryption_receipt,
    ) = _backup_encryption_paths(phases, ssh_argv)
    artifact_size, artifact_digest = _local_artifact_identity(artifact)
    encryption_payload = _secure_read(
        encryption_receipt, label="offhost_encryption_receipt"
    )
    if (
        artifact_digest != result.get("ciphertext_sha256")
        or artifact_size != result.get("ciphertext_size_bytes")
        or _json(encryption_payload, label="offhost_encryption_receipt")
        != {key: value for key, value in result.items() if key != "status"}
    ):
        raise ChoreographyError("controller_offhost_encryption_invalid")
    source_artifact = backup["backup"]
    assert isinstance(source_artifact, dict)
    if (
        result.get("plaintext_sha256") != source_artifact.get("sha256")
        or result.get("plaintext_size_bytes") != source_artifact.get("size_bytes")
        or artifact.name != Path(str(source_artifact["path"])).name + ".enc"
    ):
        raise ChoreographyError("controller_offhost_plaintext_binding_invalid")
    migration = _phase_command(
        phases, ("migrate_market_pipeline_archive.py", "execute", None)
    )
    migration_arguments = migration["arguments"]
    assert isinstance(migration_arguments, list)
    preflight_digest = str(
        _option(migration_arguments, "--host-preflight-receipt-sha256") or ""
    )
    if not HEX64.fullmatch(preflight_digest):
        raise ChoreographyError("plan_backup_offhost_binding_invalid")
    payload = {
        "schema": "market_pipeline_backup_offhost_copy/2.0",
        "status": "PASS",
        "verified_at_utc": backup["created_at_utc"],
        "release_sha": backup["release_sha"],
        "release_tree": backup["release_tree"],
        "image_id": backup["image_id"],
        "image_input_signature": backup["image_input_signature"],
        "web_role_env_sha256": backup["role_env_sha256"],
        "host_preflight_receipt_sha256": preflight_digest,
        "source_backup_receipt_sha256": backup_digest,
        "backup_status": "PASS",
        "artifact": {
            "name": artifact.name,
            "ciphertext_sha256": result["ciphertext_sha256"],
            "ciphertext_size_bytes": result["ciphertext_size_bytes"],
            "plaintext_sha256": result["plaintext_sha256"],
            "plaintext_size_bytes": result["plaintext_size_bytes"],
            "authentication_hmac_sha256": result["authentication_hmac_sha256"],
            "encryption_algorithm": result["algorithm"],
            "kdf": result["kdf"],
            "kdf_iterations": result["kdf_iterations"],
            "encryption_receipt_sha256": _digest(encryption_payload),
            "encryption_receipt_path": str(encryption_receipt),
            "bot_copy_path": str(artifact),
        },
        "off_host_copy_status": "PASS_ENCRYPTED_VERIFIED",
        "database_mutated": False,
        "services_started": False,
        "product_authority_changed": False,
        "telegram_capture_cutover_authorized": False,
        "secrets_disclosed": False,
    }
    local_receipt = encryption_receipt.parent / "offhost-copy-receipt.json"
    encoded = _canonical(payload)
    if local_receipt.exists() or local_receipt.is_symlink():
        if _secure_read(local_receipt, label="offhost_receipt") != encoded:
            raise ChoreographyError("offhost_receipt_drift")
    else:
        _write_atomic(local_receipt, payload, exclusive=True)
    remote_receipt = _command_path(
        phases,
        ("upgrade_market_pipeline_bluegreen.py", "quiesce-database", "web"),
        "--offhost-backup-receipt",
    )
    digest = _mirror_remote_exact(local_receipt, remote_receipt, ssh_argv)
    if digest != _digest(encoded):
        raise ChoreographyError("offhost_receipt_mirror_mismatch")
    return digest


def _ssh_completed(
    ssh_argv: Sequence[str],
    remote_command: str,
    *,
    timeout_seconds: int,
    input_payload: bytes | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if not ssh_argv or str(ssh_argv[0]) != SSH_BINARY:
        raise ChoreographyError("plan_web_ssh_invalid")
    argv = [*ssh_argv, remote_command]
    env = {
        "HOME": os.environ.get("HOME", "/root"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }
    guard = _ACTIVE_CONTROLLER_GUARD
    workdir = cwd or Path("/")
    if guard is not None:
        return _run_guarded_process(
            argv,
            cwd=workdir,
            env=env,
            pass_fds=(),
            timeout_seconds=timeout_seconds,
            guard=guard,
            input_payload=input_payload,
        )
    try:
        run_kwargs: dict[str, object] = {
            "check": False,
            "cwd": workdir,
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": timeout_seconds,
        }
        if input_payload is None:
            run_kwargs["stdin"] = subprocess.DEVNULL
        else:
            run_kwargs["input"] = input_payload
        return subprocess.run(argv, **run_kwargs)  # type: ignore[arg-type]
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChoreographyError("remote_evidence_unavailable") from exc


def _remote_read(path: Path, ssh_argv: Sequence[str]) -> bytes:
    code = (
        "import os,stat,sys; p=sys.argv[1]; f=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0); "
        "d=os.open(p,f); s=os.fstat(d); "
        "assert stat.S_ISREG(s.st_mode) and stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1 and 0<s.st_size<=2000000; "
        "b=os.read(d,s.st_size+1); t=os.fstat(d); os.close(d); "
        "assert len(b)==s.st_size and (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==(t.st_dev,t.st_ino,t.st_size,t.st_mtime_ns,t.st_ctime_ns); "
        "sys.stdout.buffer.write(b)"
    )
    try:
        remote_command = " ".join(
            shlex.quote(value) for value in (REMOTE_PYTHON, "-c", code, str(path))
        )
        result = _ssh_completed(ssh_argv, remote_command, timeout_seconds=60)
    except (OSError, ChoreographyError) as exc:
        raise ChoreographyError("remote_evidence_unavailable") from exc
    if result.returncode != 0:
        raise ChoreographyError("remote_evidence_unavailable")
    return result.stdout


def _assert_role_env_bindings(
    bindings: Mapping[str, Mapping[str, str]],
    *,
    ssh_argv: Sequence[str],
) -> None:
    """Re-read both old/new role envs; a plan digest is not runtime custody."""

    if set(bindings) != {"bot", "web"}:
        raise ChoreographyError("controller_role_env_binding_invalid")
    for role in ("bot", "web"):
        binding = bindings.get(role)
        if not isinstance(binding, Mapping) or set(binding) != {
            "new_path",
            "new_sha256",
            "old_path",
            "old_sha256",
        }:
            raise ChoreographyError("controller_role_env_binding_invalid")
        for generation in ("new", "old"):
            path = Path(str(binding[f"{generation}_path"] or ""))
            expected = str(binding[f"{generation}_sha256"] or "")
            payload = (
                _secure_read(path, label=f"{role}_{generation}_env")
                if role == "bot"
                else _remote_read(path, ssh_argv)
            )
            if _digest(payload) != expected:
                raise ChoreographyError("controller_role_env_digest_mismatch")


def _mirror_remote_exact(
    source: Path, destination: Path, ssh_argv: Sequence[str]
) -> str:
    payload = _secure_read(source, label="controller_mirror_source")
    code = (
        "import os,stat,sys; p=sys.argv[1]; b=sys.stdin.buffer.read(2000001); "
        "assert 0<len(b)<=2000000; q=os.path.dirname(p); s=os.lstat(q); "
        "assert stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700; "
        "f=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0); "
        "\ntry:\n d=os.open(p,f,0o600); os.write(d,b); os.fsync(d); os.close(d); x=os.open(q,os.O_RDONLY); os.fsync(x); os.close(x)"
        "\nexcept FileExistsError:\n d=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)); t=os.fstat(d); a=os.read(d,2000001); os.close(d); assert stat.S_ISREG(t.st_mode) and stat.S_IMODE(t.st_mode)==0o600 and t.st_nlink==1 and a==b"
    )
    remote_command = " ".join(
        shlex.quote(value) for value in (REMOTE_PYTHON, "-c", code, str(destination))
    )
    try:
        completed = _ssh_completed(
            ssh_argv,
            remote_command,
            timeout_seconds=60,
            input_payload=payload,
        )
    except (OSError, ChoreographyError) as exc:
        raise ChoreographyError("controller_remote_mirror_failed") from exc
    if completed.returncode != 0:
        raise ChoreographyError("controller_remote_mirror_failed")
    mirrored = _remote_read(destination, ssh_argv)
    if mirrored != payload:
        raise ChoreographyError("controller_remote_mirror_mismatch")
    return _digest(payload)


def _mirror_local_exact(source_payload: bytes, destination: Path) -> str:
    _secure_parent(destination, label="controller_mirror")
    if destination.exists() or destination.is_symlink():
        current = _secure_read(destination, label="controller_mirror")
        if current != source_payload:
            raise ChoreographyError("controller_local_mirror_mismatch")
        return _digest(current)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, source_payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return _digest(source_payload)


def _phase_evidence(
    phase: Mapping[str, object], *, release_sha: str, release_tree: str,
    ssh_argv: Sequence[str],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in phase["evidence"]:
        assert isinstance(item, dict)
        path = Path(str(item["path"]))
        payload = (
            _secure_read(path, label="phase_evidence")
            if item["host"] == "local"
            else _remote_read(path, ssh_argv)
        )
        document = _json(payload, label="phase_evidence")
        if document.get("schema") != item["schema"] or document.get("status") not in item["statuses"]:
            raise ChoreographyError("phase_evidence_contract_invalid")
        if (
            "release_sha" in document
            and document.get("release_sha") != release_sha
        ):
            raise ChoreographyError("phase_evidence_release_mismatch")
        if (
            "release_tree" in document
            and document.get("release_tree") != release_tree
        ):
            raise ChoreographyError("phase_evidence_release_mismatch")
        if document.get("secrets_disclosed") is True:
            raise ChoreographyError("phase_evidence_secret_bearing")
        result.append(
            {
                "schema": str(document["schema"]),
                "status": str(document["status"]),
                "sha256": _digest(payload),
            }
        )
    return result


def _promotion_evidence_checks(phase: Mapping[str, object], evidence: Sequence[Mapping[str, str]]) -> None:
    if phase["id"] == "promotion_verification":
        if not any(item["schema"] == "production_private_primary_promotion_verification/1.0" for item in evidence):
            raise ChoreographyError("promotion_verification_receipt_missing")
    if phase["id"] == "product_promotion":
        if not any(item["schema"] == "production_private_primary_product_promotion/1.0" and item["status"] == "PASS" for item in evidence):
            raise ChoreographyError("product_promotion_terminal_receipt_missing")
        if not any(item["schema"] == "production_private_primary_product_postdeploy_verification/1.0" and item["status"] == "PASS" for item in evidence):
            raise ChoreographyError("product_postdeploy_receipt_missing")


def _existing_interrupted_result(
    command: Mapping[str, object], *, ssh_argv: Sequence[str]
) -> dict[str, object] | None:
    """Adopt only terminal output of commands which cannot safely be replayed."""

    tool, action, _role = _signature(command)
    option: str | None = None
    allowed_statuses: set[str] = set()
    if tool == "upgrade_market_pipeline_bluegreen.py" and action == "plan":
        option = "--journal"
        allowed_statuses = {"planned"}
    elif tool == "quiesce_production_legacy_market_collectors.py" and action == "quiesce":
        option = "--journal"
        allowed_statuses = {"QUIESCED"}
    elif tool in {
        "audit_production_market_catchup.py",
        "observe_production_private_primary.py",
    }:
        option = "--output"
        # The artifact itself has no generic status for the two collection
        # snapshots.  Its exact schema is validated below before reconstructing
        # the tool's stable one-line success result.
        allowed_statuses = set()
    elif tool == "reconcile_estimator_snapshot_publication_outbox.py":
        option = "--receipt"
        allowed_statuses = (
            {"PLAN"}
            if action == "plan"
            else {"APPLIED", "ALREADY_RECONCILED"}
        )
    elif tool == "verify_production_private_primary_promotion.py":
        option = "--receipt"
        allowed_statuses = {"PASS"}
    elif tool == "promote_production_private_primary_product.py":
        option = "--receipt"
        allowed_statuses = {"PASS"}
    if option is None:
        return None
    arguments = command["arguments"]
    assert isinstance(arguments, list)
    value = _option(arguments, option)
    if value is None:
        raise ChoreographyError("plan_artifact_binding_invalid")
    path = Path(value)
    if not path.is_absolute():
        raise ChoreographyError("plan_artifact_binding_invalid")
    try:
        payload = (
            _secure_read(path, label="controller_interrupted_result")
            if command["host"] == "local"
            else _remote_read(path, ssh_argv)
        )
    except (ChoreographyError, OSError):
        return None
    result = _json(payload, label="controller_interrupted_result")
    if tool == "upgrade_market_pipeline_bluegreen.py":
        if (
            result.get("schema") != "market_pipeline_bluegreen_upgrade/1.0"
            or result.get("status") != "planned"
            or result.get("role") != _role
            or not HEX40.fullmatch(str(result.get("release_sha") or ""))
        ):
            return None
        adopted = {
            "status": "planned",
            "role": _role,
            "release_sha": result["release_sha"],
            "product_authority_changed": False,
            "state_deleted": False,
            "secrets_disclosed": False,
        }
    elif tool == "quiesce_production_legacy_market_collectors.py":
        if (
            result.get("schema") != "production_legacy_market_collector_handoff/1.1"
            and result.get("schema") != "production_legacy_market_collector_handoff/1.0"
        ):
            return None
        if (
            result.get("status") != "QUIESCED"
            or result.get("host_role") != _role
            or result.get("release_sha") != _option(arguments, "--release-sha")
            or result.get("secrets_disclosed") is not False
        ):
            return None
        adopted = {
            "status": "QUIESCED",
            "host_role": _role,
            "release_sha": result["release_sha"],
            "journal_sha256": _digest(payload),
            "all_legacy_collectors_inactive": True,
            "secrets_disclosed": False,
        }
    elif tool == "audit_production_market_catchup.py":
        expected_schema = {
            "web": "production_market_catchup_web/1.3",
            "bot": "production_market_catchup_bot/1.1",
            "settle": "production_market_catchup_settle/1.0",
            "verify": "production_market_catchup_verification/1.2",
        }[action]
        if (
            result.get("schema") != expected_schema
            or result.get("release_sha")
            != _option(arguments, "--release-sha")
            or result.get("secrets_disclosed") is not False
            or (action in {"settle", "verify"} and result.get("status") != "PASS")
        ):
            return None
        adopted = {
            "status": "PASS",
            "schema": expected_schema,
            "artifact_sha256": _digest(payload),
        }
    elif tool == "observe_production_private_primary.py":
        if (
            result.get("schema")
            != "production_private_primary_observation/1.0"
            or result.get("role") != _role
            or result.get("release_sha")
            != _option(arguments, "--release-sha")
            or result.get("release_tree")
            != _option(arguments, "--release-tree")
            or result.get("secrets_disclosed") is not False
        ):
            return None
        adopted = {
            "status": "PASS",
            "schema": result["schema"],
            "role": _role,
            "artifact_sha256": _digest(payload),
        }
    else:
        if str(result.get("status") or "") not in allowed_statuses:
            return None
        adopted = dict(result)
    _validate_command_result(command, adopted)
    return adopted


def _product_phase_journals(
    command: Mapping[str, object]
) -> tuple[list[tuple[Path, str]], Path, str]:
    arguments = list(command["arguments"])
    artifact_dir = Path(str(_option(arguments, "--queue-artifact-dir") or ""))
    transaction_id = str(_option(arguments, "--transaction-id") or "")
    if not artifact_dir.is_absolute() or not transaction_id:
        raise ChoreographyError("controller_product_recovery_binding_invalid")
    matches: list[tuple[Path, str]] = []
    for path in sorted(artifact_dir.glob("production-queue-phase-*.json")):
        try:
            payload_bytes = _secure_read(path, label="controller_product_phase_journal")
            payload = _json(payload_bytes, label="controller_product_phase_journal")
        except (ChoreographyError, OSError):
            raise ChoreographyError("controller_product_recovery_binding_invalid") from None
        binding = payload.get("transaction_binding")
        if (
            payload.get("environment") == "production"
            and payload.get("command") == "product-private-primary-promotion"
            and isinstance(binding, dict)
            and binding.get("transaction_id") == transaction_id
        ):
            matches.append((path, _digest(payload_bytes)))
    return matches, artifact_dir, transaction_id


def _materialize_product_recovery(
    command: Mapping[str, object]
) -> dict[str, object]:
    arguments = list(command["arguments"])
    matches, _artifact_dir, _transaction_id = _product_phase_journals(command)
    if len(matches) != 1:
        raise ChoreographyError("controller_product_recovery_binding_invalid")
    phase_journal, phase_digest = matches[0]
    arguments = _set_option(
        arguments, "--recovery-phase-journal", str(phase_journal)
    )
    arguments = _set_option(
        arguments, "--expected-phase-journal-sha256", phase_digest
    )
    arguments = _set_option(arguments, "--recovery-action", "resume")
    arguments = _set_option(
        arguments,
        "--recovery-confirm",
        "recover-production-private-primary-product",
    )
    recovered = dict(command)
    recovered["arguments"] = arguments
    return recovered


def _product_pre_child_retry_is_clean(command: Mapping[str, object]) -> bool:
    """Prove an interrupted Product command never created transaction state."""

    arguments = list(command["arguments"])
    matches, _artifact_dir, transaction_id = _product_phase_journals(command)
    transaction_root = Path(
        str(_option(arguments, "--transaction-root") or "")
    )
    receipt = Path(str(_option(arguments, "--receipt") or ""))
    if (
        matches
        or not transaction_root.is_absolute()
        or not transaction_id
        or not receipt.is_absolute()
    ):
        return False
    transaction_dir = transaction_root / transaction_id
    # The promoter creates the transaction directory before its own phase WAL.
    # Thus absence of all three paths, after the inherited controller lock is
    # reacquired, proves the prior child never crossed its first mutation.
    return not any(
        path.exists() or path.is_symlink()
        for path in (transaction_dir, receipt)
    )


def _initial_journal(
    *, plan_sha256: str, release_sha: str, release_tree: str,
    source_sha256: str,
) -> dict[str, object]:
    return {
        "schema": JOURNAL_SCHEMA,
        "status": "RUNNING",
        "plan_sha256": plan_sha256,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "source_sha256_before": source_sha256,
        "next_phase_index": 0,
        "active_phase": None,
        "active_command_index": None,
        "active_command_started_at_utc": None,
        "active_command_results": [],
        "dynamic_context": {},
        "completed": [],
        "pipeline_forward_only": False,
        "authority_forward_only": False,
        "primary_commit_forward_only": False,
        "product_transaction_started": False,
        "zero_owner_started_at_utc": None,
        "zero_owner_deadline_utc": None,
        "capture_owner_started_at_utc": None,
        "recovery_strategy": None,
        "product_promotion_last": True,
        "legacy_collectors_restart_forbidden": True,
        "payload_values_included": False,
        "pii_included": False,
        "secrets_disclosed": False,
    }


def _load_or_create_journal(
    path: Path, *, expected: Mapping[str, object]
) -> dict[str, object]:
    if path.exists() or path.is_symlink():
        document = _json(_secure_read(path, label="controller_journal"), label="controller_journal")
        for key in (
            "schema", "plan_sha256", "release_sha", "release_tree",
            "source_sha256_before", "product_promotion_last",
            "legacy_collectors_restart_forbidden", "secrets_disclosed",
        ):
            if document.get(key) != expected.get(key):
                raise ChoreographyError("controller_journal_binding_mismatch")
        return document
    _write_atomic(path, expected, exclusive=True)
    return dict(expected)


def _validate_journal_state(
    journal: Mapping[str, object], *, phases: Sequence[Mapping[str, object]]
) -> None:
    for key in (
        "pipeline_forward_only",
        "authority_forward_only",
        "primary_commit_forward_only",
        "product_transaction_started",
    ):
        if not isinstance(journal.get(key), bool):
            raise ChoreographyError("controller_journal_frontier_invalid")
    if journal.get("authority_forward_only") and not journal.get(
        "pipeline_forward_only"
    ):
        raise ChoreographyError("controller_journal_frontier_invalid")
    if journal.get("primary_commit_forward_only") and not journal.get(
        "authority_forward_only"
    ):
        raise ChoreographyError("controller_journal_frontier_invalid")
    if journal.get("product_transaction_started") and not journal.get(
        "primary_commit_forward_only"
    ):
        raise ChoreographyError("controller_journal_frontier_invalid")
    index = journal.get("next_phase_index")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= len(phases):
        raise ChoreographyError("controller_journal_phase_invalid")
    active = journal.get("active_phase")
    active_index = journal.get("active_command_index")
    active_started = journal.get("active_command_started_at_utc")
    active_results = journal.get("active_command_results")
    if not isinstance(active_results, list) or any(
        not isinstance(row, dict) for row in active_results
    ):
        raise ChoreographyError("controller_journal_command_invalid")
    if active is None:
        if active_index is not None or active_started is not None or active_results:
            raise ChoreographyError("controller_journal_command_invalid")
    else:
        if active != phases[index]["id"] or isinstance(active_index, bool) or not isinstance(active_index, int):
            raise ChoreographyError("controller_journal_command_invalid")
        commands = phases[index]["commands"]
        if not 0 <= active_index <= len(commands):
            raise ChoreographyError("controller_journal_command_invalid")
        if len(active_results) != active_index:
            raise ChoreographyError("controller_journal_command_invalid")
        if active_index == len(commands):
            if active_started is not None:
                raise ChoreographyError("controller_journal_command_invalid")
        elif active_started is not None:
            _parse_utc(active_started, label="controller_journal_command_time")
    started = journal.get("zero_owner_started_at_utc")
    deadline = journal.get("zero_owner_deadline_utc")
    ended = journal.get("capture_owner_started_at_utc")
    if (started is None) != (deadline is None):
        raise ChoreographyError("controller_zero_owner_window_invalid")
    if started is not None:
        start_time = _parse_utc(started, label="controller_zero_owner_start")
        deadline_time = _parse_utc(deadline, label="controller_zero_owner_deadline")
        if deadline_time != start_time + timedelta(seconds=ZERO_OWNER_MAXIMUM_SECONDS):
            raise ChoreographyError("controller_zero_owner_window_invalid")
        if ended is not None and _parse_utc(
            ended, label="controller_capture_owner_start"
        ) < start_time:
            raise ChoreographyError("controller_zero_owner_window_invalid")
    elif ended is not None:
        raise ChoreographyError("controller_zero_owner_window_invalid")
    strategy = journal.get("recovery_strategy")
    if strategy not in {None, "resume", "rollback"}:
        raise ChoreographyError("controller_recovery_strategy_invalid")
    completed = journal.get("completed")
    if (
        not isinstance(completed, list)
        or any(not isinstance(row, dict) for row in completed)
        or [row.get("phase") for row in completed] != list(PHASES[:index])
    ):
        raise ChoreographyError("controller_journal_completed_invalid")


def _remaining_zero_owner_seconds(journal: Mapping[str, object]) -> int:
    deadline = journal.get("zero_owner_deadline_utc")
    if deadline is None or journal.get("capture_owner_started_at_utc") is not None:
        return 3600
    remaining = int((_parse_utc(
        deadline, label="controller_zero_owner_deadline"
    ) - _utc_now()).total_seconds())
    if remaining > 0:
        return min(3600, remaining)
    if journal.get("authority_forward_only") or journal.get("pipeline_forward_only"):
        # After a forward-only frontier, refusing would leave the system
        # without an owner.  Continue with a one-second budget so start-captures
        # or resume can complete the exact forward repair.
        return 1
    raise ChoreographyError(
        "controller_zero_owner_deadline_exceeded_restore_required"
    )


def _zero_owner_duration_seconds(journal: Mapping[str, object]) -> int | None:
    started = journal.get("zero_owner_started_at_utc")
    ended = journal.get("capture_owner_started_at_utc")
    if started is None or ended is None:
        return None
    duration = int((
        _parse_utc(ended, label="controller_capture_owner_start")
        - _parse_utc(started, label="controller_zero_owner_start")
    ).total_seconds())
    if duration < 0:
        raise ChoreographyError("controller_zero_owner_window_invalid")
    return duration


def _watchdog_path(journal_path: Path) -> Path:
    return journal_path.parent / WATCHDOG_JOURNAL_NAME


_ZERO_OWNER_WATCHDOG = r"""
import json,os,stat,sys,time
from datetime import datetime,timezone
lock_fd=int(sys.argv[1]); expected_dev=int(sys.argv[2]); expected_ino=int(sys.argv[3])
journal=sys.argv[4]; watchdog=sys.argv[5]; deadline=sys.argv[6]
controller_pid=int(sys.argv[7])
def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def controller_alive():
    try:
        os.kill(controller_pid, 0)
        return True
    except OSError:
        return False
def write(status, extra=None):
    payload={'schema':'production_private_primary_zero_owner_watchdog/1.0','status':status,'updated_at_utc':now(),'deadline_utc':deadline,'pid':os.getpid(),'controller_pid':controller_pid,'secrets_disclosed':False}
    if extra: payload.update(extra)
    tmp=watchdog+'.%d.tmp'%os.getpid()
    d=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        os.write(d,(json.dumps(payload,sort_keys=True,separators=(',',':'))+'\n').encode())
        os.fsync(d)
    finally:
        os.close(d)
    os.replace(tmp,watchdog)
write('ARMED')
while True:
    try:
        info=os.fstat(lock_fd)
        if (info.st_dev,info.st_ino)!=(expected_dev,expected_ino):
            write('LOCK_LOST'); raise SystemExit(72)
    except OSError:
        write('LOCK_LOST'); raise SystemExit(72)
    try:
        with open(journal,'rb') as handle:
            doc=json.loads(handle.read())
    except Exception:
        time.sleep(0.2); continue
    ended=doc.get('capture_owner_started_at_utc')
    started=doc.get('zero_owner_started_at_utc')
    if ended:
        write('OWNER_PROVEN',{'zero_owner_ended_at_utc':ended,'zero_owner_started_at_utc':started})
        raise SystemExit(0)
    frontier=bool(doc.get('pipeline_forward_only') or doc.get('authority_forward_only') or doc.get('primary_commit_forward_only'))
    if not controller_alive():
        extra={'pipeline_forward_only':bool(doc.get('pipeline_forward_only')),'authority_forward_only':bool(doc.get('authority_forward_only')),'primary_commit_forward_only':bool(doc.get('primary_commit_forward_only'))}
        if frontier:
            write('DEADLINE_EXCEEDED_REPAIR_REQUIRED', extra)
        else:
            write('CONTROLLER_DEAD_RESTORE_REQUIRED', extra)
        raise SystemExit(75)
    if now()>=deadline:
        write('DEADLINE_EXCEEDED_REPAIR_REQUIRED',{
            'pipeline_forward_only':bool(doc.get('pipeline_forward_only')),
            'authority_forward_only':bool(doc.get('authority_forward_only')),
            'primary_commit_forward_only':bool(doc.get('primary_commit_forward_only')),
        })
        raise SystemExit(75)
    write('ARMED')
    time.sleep(0.5)
""".strip()


def _arm_zero_owner_watchdog(
    *,
    journal_path: Path,
    journal: Mapping[str, object],
    guard: _ControllerGuard | None,
) -> None:
    deadline = str(journal.get("zero_owner_deadline_utc") or "")
    if not deadline or guard is None or guard.descriptor is None:
        raise ChoreographyError("controller_zero_owner_watchdog_invalid")
    watchdog = _watchdog_path(journal_path)
    _secure_parent(watchdog, label="controller_watchdog")
    if watchdog.exists() or watchdog.is_symlink():
        current = _json(
            _secure_read(watchdog, label="controller_watchdog"),
            label="controller_watchdog",
        )
        if (
            current.get("schema") != WATCHDOG_SCHEMA
            or current.get("deadline_utc") != deadline
            or current.get("secrets_disclosed") is not False
        ):
            raise ChoreographyError("controller_zero_owner_watchdog_invalid")
        if current.get("status") in {
            "ARMED",
            "OWNER_PROVEN",
            "DEADLINE_EXCEEDED_REPAIR_REQUIRED",
            "CONTROLLER_DEAD_RESTORE_REQUIRED",
        }:
            return
        raise ChoreographyError("controller_zero_owner_watchdog_invalid")
    env = {
        "HOME": os.environ.get("HOME", "/root"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            _ZERO_OWNER_WATCHDOG,
            str(guard.descriptor),
            str(guard.device),
            str(guard.inode),
            str(journal_path),
            str(watchdog),
            deadline,
            str(os.getpid()),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd="/",
        env=env,
        pass_fds=(guard.descriptor,),
        start_new_session=True,
    )


def _assert_zero_owner_watchdog(
    journal_path: Path, journal: Mapping[str, object]
) -> None:
    if journal.get("zero_owner_started_at_utc") is None:
        return
    if journal.get("capture_owner_started_at_utc") is not None:
        return
    watchdog = _watchdog_path(journal_path)
    payload = _json(
        _secure_read(watchdog, label="controller_watchdog"),
        label="controller_watchdog",
    )
    if (
        payload.get("schema") != WATCHDOG_SCHEMA
        or payload.get("deadline_utc") != journal.get("zero_owner_deadline_utc")
        or payload.get("status")
        not in {"ARMED", "DEADLINE_EXCEEDED_REPAIR_REQUIRED", "OWNER_PROVEN"}
        or payload.get("secrets_disclosed") is not False
    ):
        raise ChoreographyError("controller_zero_owner_watchdog_invalid")


def _pre_migration_rollback_commands(
    phases: Sequence[Mapping[str, object]], *, release_sha: str
) -> tuple[dict[str, object], dict[str, object]]:
    by_role: dict[str, dict[str, object]] = {}
    workload = next(
        phase for phase in phases if phase.get("id") == "bluegreen_workload_quiesce"
    )
    for command in workload["commands"]:
        tool, action, role = _signature(command)
        if tool != "upgrade_market_pipeline_bluegreen.py" or action != "plan":
            continue
        assert role in {"web", "bot"}
        arguments = command["arguments"]
        assert isinstance(arguments, list)
        journal = _option(arguments, "--journal")
        if not journal:
            raise ChoreographyError("plan_artifact_binding_invalid")
        rollback = {
            "host": command["host"],
            "remote_release_root": command.get("remote_release_root"),
            "tool": tool,
            "arguments": [
                "rollback",
                "--role",
                role,
                "--release-sha",
                release_sha,
                "--journal",
                journal,
                "--confirm",
                "upgrade-market-pipeline-bluegreen",
            ],
        }
        by_role[role] = rollback
    if set(by_role) != {"web", "bot"}:
        raise ChoreographyError("controller_pre_migration_rollback_invalid")
    # Stop/restore web database ownership before bringing the bot role back.
    return by_role["web"], by_role["bot"]


def _run_pre_migration_rollback(
    *,
    journal: dict[str, object],
    journal_path: Path,
    phases: Sequence[Mapping[str, object]],
    local_control_root: Path,
    remote_control_root: Path,
    control_entries: Mapping[str, str],
    control_manifest_sha256: str,
    release_sha: str,
    release_tree: str,
    ssh_argv: Sequence[str],
    controller_guard: _ControllerGuard | None = None,
) -> dict[str, object]:
    if journal.get("pipeline_forward_only"):
        raise ChoreographyError("controller_pipeline_forward_only_resume_required")
    next_phase = journal.get("next_phase_index")
    active_phase = journal.get("active_phase")
    active_index = journal.get("active_command_index")
    active_started = journal.get("active_command_started_at_utc")
    expected_roles: set[str] = set()
    if isinstance(next_phase, int) and next_phase > 0:
        expected_roles = {"web", "bot"}
    elif active_phase == "bluegreen_workload_quiesce" and isinstance(
        active_index, int
    ):
        if active_index >= 1 or (active_index == 0 and active_started is not None):
            expected_roles.add("web")
        if active_index >= 2 or (active_index == 1 and active_started is not None):
            expected_roles.add("bot")
    results: list[dict[str, str]] = []
    for command in _pre_migration_rollback_commands(phases, release_sha=release_sha):
        role = str(_signature(command)[2])
        if role not in expected_roles:
            continue
        arguments = command["arguments"]
        assert isinstance(arguments, list)
        plan_journal = Path(str(_option(arguments, "--journal") or ""))
        try:
            plan_payload = (
                _secure_read(plan_journal, label="controller_rollback_journal")
                if command["host"] == "local"
                else _remote_read(plan_journal, ssh_argv)
            )
        except (ChoreographyError, OSError) as exc:
            # A plan command recorded as started/completed establishes the
            # minimum rollback set.  Missing or unreadable state cannot be
            # silently reclassified as "nothing happened".
            raise ChoreographyError(
                "controller_pre_migration_rollback_state_missing"
            ) from exc
        plan_document = _json(plan_payload, label="controller_rollback_journal")
        if (
            plan_document.get("release_sha") != release_sha
            or plan_document.get("role") != _signature(command)[2]
        ):
            raise ChoreographyError("controller_pre_migration_rollback_invalid")
        result = _run_command(
            command,
            local_control_root=local_control_root,
            remote_control_root=remote_control_root,
            control_entries=control_entries,
            control_manifest_sha256=control_manifest_sha256,
            release_sha=release_sha,
            release_tree=release_tree,
            ssh_argv=ssh_argv,
            controller_guard=controller_guard,
        )
        results.append(
            {
                "role": role,
                "status": str(result.get("status") or "ROLLED_BACK"),
                "result_sha256": _digest(_canonical(result)),
            }
        )
    if {row["role"] for row in results} != expected_roles:
        raise ChoreographyError("controller_pre_migration_rollback_incomplete")
    journal["status"] = "ROLLED_BACK"
    journal["active_phase"] = None
    journal["active_command_index"] = None
    journal["active_command_started_at_utc"] = None
    journal["active_command_results"] = []
    journal["rollback_results"] = results
    journal["recovery_strategy"] = "rollback"
    _write_atomic(journal_path, journal, exclusive=False)
    return journal


def _execute_locked(
    args: argparse.Namespace, *, controller_guard: _ControllerGuard
) -> dict[str, object]:
    recovery_requested = getattr(args, "command", "execute") == "recover"
    recovery_strategy = (
        str(getattr(args, "recovery_strategy", "resume") or "resume")
        if recovery_requested
        else None
    )
    expected_confirmation = CONFIRMATION
    if recovery_requested:
        expected_confirmation = (
            RECOVERY_CONFIRMATION
            if recovery_strategy == "resume"
            else ROLLBACK_CONFIRMATION
        )
    if args.confirm != expected_confirmation:
        raise ChoreographyError("controller_confirmation_invalid")
    plan_payload = _secure_read(Path(args.plan), label="controller_plan")
    if _digest(plan_payload) != args.expected_plan_sha256:
        raise ChoreographyError("controller_plan_digest_mismatch")
    plan = _json(plan_payload, label="controller_plan")
    (
        release_sha,
        release_tree,
        source,
        source_digest,
        _controller_lock,
        phases,
        ssh_argv,
        local_control_root,
        remote_control_root,
        control_manifest_sha256,
        role_env_bindings,
    ) = _validate_plan(
        plan,
        release_root=Path(args.release_root),
        allow_historical_approved=(
            recovery_requested and recovery_strategy == "rollback"
        ),
    )
    control_entries = _control_release_manifest(
        local_control_root,
        expected_manifest_sha256=control_manifest_sha256,
        release_sha=release_sha,
        release_tree=release_tree,
    )
    _validate_plan_build_receipt(
        args,
        plan=plan,
        plan_sha256=args.expected_plan_sha256,
        release_sha=release_sha,
        release_tree=release_tree,
        local_control_root=local_control_root,
        control_entries=control_entries,
    )
    _validate_official_invocation(
        args,
        source=source,
        ssh_argv=ssh_argv,
        local_control_root=local_control_root,
        remote_control_root=remote_control_root,
        control_manifest_sha256=control_manifest_sha256,
        deployment_manifest=Path(str(plan.get("deployment_manifest") or "")),
    )
    _assert_role_env_bindings(role_env_bindings, ssh_argv=ssh_argv)
    product_image_ids = {
        role: str(value)
        for role, value in dict(plan["product_image_ids"]).items()
    }

    def assert_product_runtime(expected_mode: str) -> None:
        _assert_product_runtime(
            ssh_argv=ssh_argv,
            expected_mode=expected_mode,
            expected_image_ids=product_image_ids,
            release_sha=release_sha,
            release_tree=release_tree,
        )
    if _controller_lock.parent != Path(args.plan).parent:
        raise ChoreographyError("controller_lock_path_invalid")
    initial = _initial_journal(
        plan_sha256=args.expected_plan_sha256,
        release_sha=release_sha,
        release_tree=release_tree,
        source_sha256=source_digest,
    )
    journal_path = Path(args.journal)
    journal_existed = journal_path.exists() or journal_path.is_symlink()
    journal = _load_or_create_journal(journal_path, expected=initial)
    if recovery_requested and not journal_existed:
        raise ChoreographyError("controller_recovery_journal_missing")
    if (
        not recovery_requested
        and journal_existed
        and journal.get("status") != "PASS"
    ):
        raise ChoreographyError("controller_explicit_recovery_required")
    _validate_journal_state(journal, phases=phases)
    if recovery_requested and journal.get("status") not in {
        "PASS",
        "PRODUCT_ROLLED_BACK",
    }:
        journal["recovery_strategy"] = recovery_strategy
        _write_atomic(journal_path, journal, exclusive=False)
        if recovery_strategy == "rollback":
            if journal.get("pipeline_forward_only"):
                raise ChoreographyError(
                    "controller_pipeline_forward_only_resume_required"
                )
            if journal.get("authority_forward_only"):
                raise ChoreographyError(
                    "controller_authority_forward_only_resume_required"
                )
            if journal.get("primary_commit_forward_only"):
                raise ChoreographyError(
                    "controller_primary_commit_forward_only_resume_required"
                )
            _run_pre_migration_rollback(
                journal=journal,
                journal_path=journal_path,
                phases=phases,
                local_control_root=local_control_root,
                remote_control_root=remote_control_root,
                control_entries=control_entries,
                control_manifest_sha256=control_manifest_sha256,
                release_sha=release_sha,
                release_tree=release_tree,
                ssh_argv=ssh_argv,
                controller_guard=controller_guard,
            )
            rollback_payload = _secure_read(
                journal_path, label="controller_journal"
            )
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "status": "ROLLED_BACK",
                "release_sha": release_sha,
                "release_tree": release_tree,
                "plan_sha256": args.expected_plan_sha256,
                "journal_sha256": _digest(rollback_payload),
                "pipeline_forward_only": False,
                "product_authority": "LEGACY",
                "legacy_collectors_restart_forbidden": True,
                "payload_values_included": False,
                "pii_included": False,
                "secrets_disclosed": False,
            }
            _write_atomic(
                Path(args.receipt),
                receipt,
                exclusive=not Path(args.receipt).exists(),
            )
            return receipt
    if not journal_existed:
        _assert_product_source(
            source, expected_sha256=source_digest, expected_mode="LEGACY"
        )
        assert_product_runtime("LEGACY")
    if journal.get("status") == "PRODUCT_ROLLED_BACK":
        _assert_product_source(source, expected_sha256=None, expected_mode="LEGACY")
        assert_product_runtime("LEGACY")
        receipt_path = Path(args.receipt)
        receipt = _json(
            _secure_read(receipt_path, label="controller_receipt"),
            label="controller_receipt",
        )
        if (
            receipt.get("schema") != RECEIPT_SCHEMA
            or receipt.get("status") != "ROLLED_BACK"
            or receipt.get("release_sha") != release_sha
            or receipt.get("release_tree") != release_tree
            or receipt.get("journal_sha256")
            != _digest(_secure_read(journal_path, label="controller_journal"))
            or receipt.get("product_authority") != "LEGACY"
            or receipt.get("pipeline_authority") != "PRIVATE_PRIMARY"
        ):
            raise ChoreographyError("controller_terminal_rollback_invalid")
        return receipt
    if journal.get("status") == "PASS":
        _assert_product_source(source, expected_sha256=None, expected_mode="PRIVATE_PRIMARY")
        assert_product_runtime("PRIVATE_PRIMARY")
        dynamic_context = journal.get("dynamic_context")
        if not isinstance(dynamic_context, dict):
            raise ChoreographyError("controller_dynamic_context_invalid")
        product_phase = phases[-1]
        product_command = product_phase["commands"][0]
        materialized = _materialize_command(
            product_command,
            phases=phases,
            context=dynamic_context,
            ssh_argv=ssh_argv,
        )
        terminal_recovery = _materialize_product_recovery(materialized)
        terminal_result = _run_command(
            terminal_recovery,
            local_control_root=local_control_root,
            remote_control_root=remote_control_root,
            control_entries=control_entries,
            control_manifest_sha256=control_manifest_sha256,
            release_sha=release_sha,
            release_tree=release_tree,
            ssh_argv=ssh_argv,
            controller_guard=controller_guard,
        )
        _assert_role_env_bindings(role_env_bindings, ssh_argv=ssh_argv)
        if terminal_result.get("status") != "PASS":
            raise ChoreographyError("controller_terminal_live_revalidation_failed")
    else:
        index = int(journal.get("next_phase_index", -1))
        while index < len(phases):
            phase = phases[index]
            phase_id = str(phase["id"])
            if phase_id != "product_promotion":
                _assert_product_source(
                    source, expected_sha256=source_digest, expected_mode="LEGACY"
                )
                assert_product_runtime("LEGACY")
            if journal.get("active_phase") is None:
                journal["active_phase"] = phase_id
                journal["active_command_index"] = 0
                journal["active_command_started_at_utc"] = None
                journal["active_command_results"] = []
                _write_atomic(journal_path, journal, exclusive=False)
            elif journal.get("active_phase") != phase_id:
                raise ChoreographyError("controller_journal_phase_invalid")
            command_index = int(journal.get("active_command_index", -1))
            command_results = journal.get("active_command_results")
            if not isinstance(command_results, list):
                raise ChoreographyError("controller_journal_command_invalid")
            dynamic_context = journal.get("dynamic_context")
            if not isinstance(dynamic_context, dict) or any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or not HEX64.fullmatch(value)
                for key, value in dynamic_context.items()
            ):
                raise ChoreographyError("controller_dynamic_context_invalid")
            while command_index < len(phase["commands"]):
                command = phase["commands"][command_index]
                _assert_role_env_bindings(
                    role_env_bindings, ssh_argv=ssh_argv
                )
                if phase_id != "product_promotion":
                    _assert_product_source(
                        source,
                        expected_sha256=source_digest,
                        expected_mode="LEGACY",
                    )
                    assert_product_runtime("LEGACY")
                materialized = _materialize_command(
                    command,
                    phases=phases,
                    context=dynamic_context,
                    ssh_argv=ssh_argv,
                )
                if phase_id == "migration" and command_index == 0:
                    # The exact old Market migration one-shot is not a valid
                    # rollback mechanism against schema v3.  Persist the
                    # forward-only frontier before invoking migration so a
                    # SIGKILL can never resume by starting an old stack.
                    journal["pipeline_forward_only"] = True
                if phase_id == "legacy_quiesce" and command_index == 5:
                    journal["authority_forward_only"] = True
                if phase_id == "promotion_verification" and command_index == 1:
                    # The first host commit is a global forward-only frontier.
                    journal["primary_commit_forward_only"] = True
                if phase_id == "product_promotion" and command_index == 0:
                    journal["product_transaction_started"] = True
                if (
                    phase_id == "legacy_quiesce"
                    and command_index == 0
                    and journal.get("zero_owner_started_at_utc") is None
                ):
                    # The outage budget begins before the first command that
                    # can stop a collector, not after both hosts are already
                    # quiesced.
                    started = _utc_now()
                    journal["zero_owner_started_at_utc"] = _utc_text(started)
                    journal["zero_owner_deadline_utc"] = _utc_text(
                        started + timedelta(seconds=ZERO_OWNER_MAXIMUM_SECONDS)
                    )
                    _write_atomic(journal_path, journal, exclusive=False)
                    _arm_zero_owner_watchdog(
                        journal_path=journal_path,
                        journal=journal,
                        guard=controller_guard,
                    )
                if journal.get("zero_owner_started_at_utc") is not None:
                    _assert_zero_owner_watchdog(journal_path, journal)
                interrupted = journal.get("active_command_started_at_utc") is not None
                journal["active_command_started_at_utc"] = _utc_text()
                _write_atomic(journal_path, journal, exclusive=False)
                result = (
                    _existing_interrupted_result(
                        materialized, ssh_argv=ssh_argv
                    )
                    if recovery_requested and interrupted
                    else None
                )
                if result is None:
                    executable = materialized
                    if (
                        recovery_requested
                        and interrupted
                        and phase_id == "product_promotion"
                    ):
                        phase_journals, _artifact_dir, _transaction_id = (
                            _product_phase_journals(materialized)
                        )
                        if len(phase_journals) == 1:
                            executable = _materialize_product_recovery(
                                materialized
                            )
                        elif len(phase_journals) == 0 and (
                            _product_pre_child_retry_is_clean(materialized)
                        ):
                            _assert_product_source(
                                source,
                                expected_sha256=source_digest,
                                expected_mode="LEGACY",
                            )
                            assert_product_runtime("LEGACY")
                        else:
                            raise ChoreographyError(
                                "controller_product_recovery_binding_invalid"
                            )
                    try:
                        timeout_seconds = _remaining_zero_owner_seconds(journal)
                    except ChoreographyError as exc:
                        if str(exc) != (
                            "controller_zero_owner_deadline_exceeded_restore_required"
                        ):
                            raise
                        _run_pre_migration_rollback(
                            journal=journal,
                            journal_path=journal_path,
                            phases=phases,
                            local_control_root=local_control_root,
                            remote_control_root=remote_control_root,
                            control_entries=control_entries,
                            control_manifest_sha256=control_manifest_sha256,
                            release_sha=release_sha,
                            release_tree=release_tree,
                            ssh_argv=ssh_argv,
                            controller_guard=controller_guard,
                        )
                        raise
                    result = _run_command(
                        executable,
                        local_control_root=local_control_root,
                        remote_control_root=remote_control_root,
                        control_entries=control_entries,
                        control_manifest_sha256=control_manifest_sha256,
                        release_sha=release_sha,
                        release_tree=release_tree,
                        ssh_argv=ssh_argv,
                        timeout_seconds=timeout_seconds,
                        controller_guard=controller_guard,
                    )
                if (
                    phase_id == "product_promotion"
                    and result.get("status") == "ROLLED_BACK"
                ):
                    _assert_product_source(
                        source, expected_sha256=None, expected_mode="LEGACY"
                    )
                    assert_product_runtime("LEGACY")
                    journal["status"] = "PRODUCT_ROLLED_BACK"
                    journal["active_phase"] = None
                    journal["active_command_index"] = None
                    journal["active_command_started_at_utc"] = None
                    journal["active_command_results"] = []
                    journal["product_rollback_result_sha256"] = _digest(
                        _canonical(result)
                    )
                    _write_atomic(journal_path, journal, exclusive=False)
                    rollback_receipt = {
                        "schema": RECEIPT_SCHEMA,
                        "status": "ROLLED_BACK",
                        "release_sha": release_sha,
                        "release_tree": release_tree,
                        "plan_sha256": args.expected_plan_sha256,
                        "journal_sha256": _digest(
                            _secure_read(
                                journal_path, label="controller_journal"
                            )
                        ),
                        "pipeline_authority": "PRIVATE_PRIMARY",
                        "product_authority": "LEGACY",
                        "rollback_product_only": True,
                        "legacy_collectors_restart_forbidden": True,
                        "payload_values_included": False,
                        "pii_included": False,
                        "secrets_disclosed": False,
                    }
                    _write_atomic(
                        Path(args.receipt),
                        rollback_receipt,
                        exclusive=not Path(args.receipt).exists(),
                    )
                    return rollback_receipt
                if isinstance(result, Mapping):
                    if (
                        "release_sha" in result
                        and result.get("release_sha") != release_sha
                    ) or (
                        "release_tree" in result
                        and result.get("release_tree") != release_tree
                    ):
                        raise ChoreographyError(
                            "controller_command_release_binding_mismatch"
                        )
                    _record_dynamic_context(
                        materialized,
                        result,
                        context=dynamic_context,
                        phases=phases,
                        ssh_argv=ssh_argv,
                    )
                    command_results.append(
                        {
                            "tool": str(command["tool"]),
                            "status": str(result.get("status") or "PASS"),
                            "result_sha256": _digest(_canonical(result)),
                        }
                    )
                _assert_role_env_bindings(
                    role_env_bindings, ssh_argv=ssh_argv
                )
                command_index += 1
                journal["active_command_index"] = command_index
                journal["active_command_started_at_utc"] = None
                journal["active_command_results"] = command_results
                journal["dynamic_context"] = dynamic_context
                if phase_id == "bluegreen_activate" and command_index == 3:
                    journal["capture_owner_started_at_utc"] = _utc_text()
                _write_atomic(journal_path, journal, exclusive=False)
                if phase_id != "product_promotion":
                    _assert_product_source(
                        source,
                        expected_sha256=source_digest,
                        expected_mode="LEGACY",
                    )
                    assert_product_runtime("LEGACY")
            evidence = _phase_evidence(
                phase, release_sha=release_sha, release_tree=release_tree,
                ssh_argv=ssh_argv,
            )
            _promotion_evidence_checks(phase, evidence)
            completed = journal.get("completed")
            if not isinstance(completed, list):
                raise ChoreographyError("controller_journal_completed_invalid")
            completed.append(
                {
                    "phase": phase_id,
                    "commands": command_results,
                    "evidence": evidence,
                }
            )
            index += 1
            journal["next_phase_index"] = index
            journal["active_phase"] = None
            journal["active_command_index"] = None
            journal["active_command_started_at_utc"] = None
            journal["active_command_results"] = []
            _write_atomic(journal_path, journal, exclusive=False)
        source_after = _assert_product_source(
            source, expected_sha256=None, expected_mode="PRIVATE_PRIMARY"
        )
        assert_product_runtime("PRIVATE_PRIMARY")
        journal["status"] = "PASS"
        journal["source_sha256_after"] = source_after
        _write_atomic(journal_path, journal, exclusive=False)
    journal_payload = _secure_read(journal_path, label="controller_journal")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "release_sha": release_sha,
        "release_tree": release_tree,
        "plan_sha256": args.expected_plan_sha256,
        "journal_sha256": _digest(journal_payload),
        "source_sha256_before": source_digest,
        "source_sha256_after": journal.get("source_sha256_after"),
        "completed_phases": [item["phase"] for item in journal["completed"]],
        "product_promotion_last": True,
        "terminal_live_revalidation_required": True,
        "legacy_collectors_restart_forbidden": True,
        "rollback_product_only": True,
        "zero_owner_started_at_utc": journal.get("zero_owner_started_at_utc"),
        "zero_owner_ended_at_utc": journal.get("capture_owner_started_at_utc"),
        "zero_owner_duration_seconds": _zero_owner_duration_seconds(journal),
        "zero_owner_maximum_seconds": ZERO_OWNER_MAXIMUM_SECONDS,
        "payload_values_included": False,
        "pii_included": False,
        "secrets_disclosed": False,
    }
    _write_atomic(Path(args.receipt), receipt, exclusive=not Path(args.receipt).exists())
    return receipt


def execute(args: argparse.Namespace) -> dict[str, object]:
    """Acquire/adopt the durable controller lock and run or resume exactly once."""

    plan_payload = _secure_read(Path(args.plan), label="controller_plan")
    if _digest(plan_payload) != args.expected_plan_sha256:
        raise ChoreographyError("controller_plan_digest_mismatch")
    plan = _json(plan_payload, label="controller_plan")
    (
        release_sha,
        release_tree,
        _source,
        _source_digest,
        controller_lock_path,
        _phases,
        expected_ssh,
        local_control_root,
        remote_control_root,
        control_manifest_sha256,
        _role_env_bindings,
    ) = _validate_plan(
        plan,
        release_root=Path(args.release_root),
        allow_historical_approved=(
            getattr(args, "command", "execute") == "recover"
            and str(getattr(args, "recovery_strategy", "resume") or "resume")
            == "rollback"
        ),
    )
    control_entries = _control_release_manifest(
        local_control_root,
        expected_manifest_sha256=control_manifest_sha256,
        release_sha=release_sha,
        release_tree=release_tree,
    )
    _validate_plan_build_receipt(
        args,
        plan=plan,
        plan_sha256=args.expected_plan_sha256,
        release_sha=release_sha,
        release_tree=release_tree,
        local_control_root=local_control_root,
        control_entries=control_entries,
    )
    _validate_official_invocation(
        args,
        source=_source,
        ssh_argv=expected_ssh,
        local_control_root=local_control_root,
        remote_control_root=remote_control_root,
        control_manifest_sha256=control_manifest_sha256,
        deployment_manifest=Path(str(plan.get("deployment_manifest") or "")),
    )
    if controller_lock_path.parent != Path(args.plan).parent:
        raise ChoreographyError("controller_lock_path_invalid")
    guard = _ControllerGuard(
        controller_lock_path,
        plan_sha256=args.expected_plan_sha256,
        release_sha=release_sha,
        release_tree=release_tree,
    )
    guard.acquire()
    terminal = False
    try:
        with _bind_controller_guard(guard):
            result = _execute_locked(args, controller_guard=guard)
        terminal = result.get("status") in {"PASS", "ROLLED_BACK"}
        return result
    finally:
        guard.release(terminal=terminal)


def validate(args: argparse.Namespace) -> dict[str, object]:
    plan_payload = _secure_read(Path(args.plan), label="controller_plan")
    if _digest(plan_payload) != args.expected_plan_sha256:
        raise ChoreographyError("controller_plan_digest_mismatch")
    plan = _json(plan_payload, label="controller_plan")
    (
        release_sha,
        release_tree,
        source,
        source_digest,
        _controller_lock,
        _phases,
        expected_ssh,
        local_control_root,
        remote_control_root,
        control_manifest_sha256,
        _role_env_bindings,
    ) = _validate_plan(plan, release_root=Path(args.release_root))
    control_entries = _control_release_manifest(
        local_control_root,
        expected_manifest_sha256=control_manifest_sha256,
        release_sha=release_sha,
        release_tree=release_tree,
    )
    _validate_plan_build_receipt(
        args,
        plan=plan,
        plan_sha256=args.expected_plan_sha256,
        release_sha=release_sha,
        release_tree=release_tree,
        local_control_root=local_control_root,
        control_entries=control_entries,
    )
    _validate_official_invocation(
        args,
        source=source,
        ssh_argv=expected_ssh,
        local_control_root=local_control_root,
        remote_control_root=remote_control_root,
        control_manifest_sha256=control_manifest_sha256,
        deployment_manifest=Path(str(plan.get("deployment_manifest") or "")),
    )
    if _controller_lock.parent != Path(args.plan).parent:
        raise ChoreographyError("controller_lock_path_invalid")
    _assert_product_source(source, expected_sha256=source_digest, expected_mode="LEGACY")
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "PLAN_PASS",
        "release_sha": release_sha,
        "release_tree": release_tree,
        "plan_sha256": args.expected_plan_sha256,
        "phase_count": len(PHASES),
        "product_authority_initial": "LEGACY",
        "product_promotion_last": True,
        "runtime_or_database_mutated": False,
        "secrets_disclosed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "execute", "recover"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--plan-build-receipt", required=True)
    parser.add_argument("--expected-plan-build-receipt-sha256", required=True)
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--expected-source-manifest", required=True)
    parser.add_argument("--expected-deployment-manifest", required=True)
    parser.add_argument("--expected-deployment-manifest-sha256", required=True)
    parser.add_argument("--expected-web-ssh-argv-sha256", required=True)
    parser.add_argument("--expected-local-control-release-root", required=True)
    parser.add_argument("--expected-remote-control-release-root", required=True)
    parser.add_argument("--expected-control-payload-manifest-sha256", required=True)
    parser.add_argument("--journal")
    parser.add_argument("--receipt")
    parser.add_argument("--confirm")
    parser.add_argument(
        "--recovery-strategy", choices=("resume", "rollback"), default="resume"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not HEX64.fullmatch(args.expected_plan_sha256):
            raise ChoreographyError("controller_plan_digest_invalid")
        if args.command == "validate":
            result = validate(args)
        else:
            if not args.journal or not args.receipt:
                raise ChoreographyError("controller_output_paths_required")
            result = execute(args)
    except (ChoreographyError, OSError, TypeError, ValueError) as exc:
        reason = str(exc)
        if not isinstance(exc, ChoreographyError) or not re.fullmatch(r"[a-z0-9_]+", reason):
            reason = "private_primary_choreography_blocked"
        print(
            json.dumps(
                {
                    "schema": RECEIPT_SCHEMA,
                    "status": "BLOCKED",
                    "reason_code": reason,
                    "secrets_disclosed": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
