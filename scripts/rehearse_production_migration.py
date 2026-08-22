#!/usr/bin/env python3
"""Rehearse the production migration on verified, disposable PostgreSQL copies.

The tool is deliberately fail-closed.  It consumes the two plain ``.sql.gz``
database artifacts recorded by a hardened production-backup receipt, restores
each one into an isolated PostgreSQL 15 container, and runs the repository's
guarded scratch Alembic entry point.  It never connects to a production
database, never invokes Compose, and never accepts caller-selected Docker
resource names.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from alembic.config import Config
from alembic.script import ScriptDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_production_backup import (
    DEFAULT_BACKUP_DIR,
    DEFAULT_BACKUP_RECEIPT_DIR,
    DEFAULT_IRAN_PULL_DIR,
    backup_target_binding_sha256,
    production_backup_manifest_values,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PRE_MIGRATION_HEAD = "f2c7d8e9a0b1"
POSTGRES_IMAGE = "postgres:15-alpine"
MIGRATION_RUNNER_IMAGE = "trading_bot_base"
EXECUTE_CONFIRMATION = "REHEARSE VERIFIED PRODUCTION MIGRATIONS"
DEFAULT_MAX_BACKUP_AGE_SECONDS = 2 * 60 * 60
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30 * 60
NORMAL_RETURN_PROCESS_TERMINATION_GRACE_SECONDS = 0.25
NORMAL_RETURN_PROCESS_KILL_TIMEOUT_SECONDS = 2.0
DEFAULT_RECEIPT_ROOT = Path(
    "/root/secure-envs/trading-bot/production-migration-rehearsal/evidence"
)
DEFAULT_WORK_ROOT = Path(
    "/root/secure-envs/trading-bot/production-migration-rehearsal/work"
)
DEFAULT_LOCK_PATH = Path(
    "/root/secure-envs/trading-bot/production-migration-rehearsal/.lock"
)
DEFAULT_RUNNER_PREBUILD_RECEIPT = (
    REPO_ROOT
    / "tmp"
    / "production-release"
    / "artifacts"
    / "foreign-image-prebuild-receipt.json"
)
RESOURCE_LABEL = "trading-bot.production-migration-rehearsal"

# This is the exact table delta between the deployed f2 branch and the one
# current Alembic head.  Requiring every table to be absent before and present
# after also detects a partially applied or historically drifted dump.
EXPECTED_NEW_TABLES = (
    "coin_intelligence_inference_audits",
    "coin_intelligence_inference_outcomes",
    "coin_intelligence_market_outbox",
    "telegram_channel_membership_sagas",
    "telegram_delivery_feeder_states",
    "telegram_delivery_jobs",
    "telegram_delivery_provider_outcomes",
    "telegram_delivery_reconciliation_evidence",
    "telegram_delivery_resume_operations",
    "telegram_delivery_runtime_gates",
    "telegram_interaction_anchor_states",
    "telegram_publisher_dispatch_commands",
    "telegram_scheduled_operations",
    "user_flags",
)
EXPECTED_CONCURRENT_INDEX = "idx_change_log_unsynced_aggregate_order"
CRITICAL_PRESERVED_TABLES = (
    "users",
    "commodities",
    "commodity_aliases",
    "offers",
    "offer_requests",
    "trades",
    "offer_publication_states",
    "telegram_notification_outbox",
    "change_log",
    "user_sessions",
    "customer_relations",
    "accountant_relations",
)
EXPECTED_ROLES = ("foreign", "iran")
EXPECTED_PROJECT_LABELS = {"foreign": "trading_bot", "iran": "current"}
EXPECTED_BACKUP_KINDS = ("db", "redis", "uploads", "audit")
RUNNER_PREBUILD_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "environment",
        "release_sha",
        "release_tree",
        "image_id",
        "input_signature",
        "secrets_disclosed",
    }
)
DENIED_DOCKER_IDENTIFIERS = frozenset(
    {
        "current",
        "staging",
        "trading_bot",
        "trading_bot_app",
        "trading_bot_bot",
        "trading_bot_db",
        "trading_bot_redis",
        "trading_bot_migration",
        "trading_bot_staging",
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REVISION_RE = re.compile(r"^[0-9a-z]{12}$")
SAFE_ROLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
RECEIPT_NAME_RE = re.compile(
    r"^production-migration-rehearsal-[A-Za-z0-9_.-]{1,96}\.json$"
)


class RehearsalRefusal(RuntimeError):
    """A safety contract refused the rehearsal before unsafe work."""


class RehearsalCommandError(RuntimeError):
    """A disposable rehearsal command failed or timed out."""


class RehearsalInterrupted(BaseException):
    """Raised by bounded signal handlers so the cleanup path always runs."""


@dataclass(frozen=True, slots=True)
class SourceBinding:
    commit: str
    tree: str
    alembic_head: str


@dataclass(frozen=True, slots=True)
class DumpArtifact:
    role: str
    path: Path
    sha256: str
    size_bytes: int
    release_sha: str
    database_identity_sha256: str
    target_binding_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedBackup:
    receipt_sha256: str
    created_at: str
    production_release_sha: str
    artifacts: tuple[DumpArtifact, ...]
    artifact_set_sha256: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(slots=True)
class DockerResources:
    run_id: str
    network_name: str
    container_names: list[str]
    volume_names: list[str]


@dataclass(frozen=True, slots=True)
class CommittedSource:
    path: Path
    archive_sha256: str


@dataclass(frozen=True, slots=True)
class RunnerImageBinding:
    image_id: str
    oci_revision: str
    release_tree: str
    input_signature: str
    prebuild_receipt_sha256: str


@dataclass(frozen=True, slots=True)
class RunnerPrebuildBinding:
    receipt_path: Path
    receipt_sha256: str
    image_id: str
    release_sha: str
    release_tree: str
    input_signature: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: object) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise RehearsalRefusal("backup receipt timestamp is missing")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RehearsalRefusal("backup receipt timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise RehearsalRefusal("backup receipt timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _safe_environment() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
        if str(os.environ.get(key) or "").strip()
    }


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(int(process_group_id), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_group_has_live_members(process_group_id: int) -> bool:
    """Return whether the group contains a non-zombie process."""

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
    deadline = time.monotonic() + max(float(timeout), 0.0)
    while _process_group_has_live_members(process_group_id):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
    return True


def _stop_process_group(
    process: subprocess.Popen[str],
    *,
    process_group_id: int | None = None,
    grace_seconds: float = 5.0,
    kill_seconds: float | None = None,
) -> tuple[str, str]:
    # The leader may already have exited while a descendant still holds the
    # inherited stdout/stderr pipes.  Always signal the process group by its
    # original PGID; checking only ``process.poll()`` would miss that case.
    group_id = int(process_group_id or process.pid)
    kill_seconds = grace_seconds if kill_seconds is None else kill_seconds
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
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
        remaining_kill = max(0.0, kill_deadline - time.monotonic())
        group_stopped = _wait_for_process_group_exit(group_id, remaining_kill)
        if kill_communicate_timed_out or not group_stopped:
            raise RehearsalCommandError(
                "child process group did not stop within bounded cleanup"
            )
    if process.poll() is None:
        raise RehearsalCommandError("child process leader did not terminate")
    return stdout or "", stderr or ""


def run_command(
    args: Sequence[str],
    *,
    timeout: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    cwd: Path = REPO_ROOT,
) -> CommandResult:
    """Run one bounded command in its own process group with a clean env."""

    process = subprocess.Popen(
        list(args),
        cwd=str(cwd),
        env=_safe_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    process_group_id = process.pid
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stdout, stderr = _stop_process_group(
            process, process_group_id=process_group_id
        )
        return CommandResult(124, stdout, stderr, timed_out=True)
    except BaseException:
        _stop_process_group(process, process_group_id=process_group_id)
        raise
    if _process_group_has_live_members(process_group_id):
        _stop_process_group(
            process,
            process_group_id=process_group_id,
            grace_seconds=NORMAL_RETURN_PROCESS_TERMINATION_GRACE_SECONDS,
            kill_seconds=NORMAL_RETURN_PROCESS_KILL_TIMEOUT_SECONDS,
        )
        return CommandResult(125, stdout or "", stderr or "")
    return CommandResult(int(process.returncode or 0), stdout or "", stderr or "")


def _require_success(result: CommandResult, *, operation: str) -> str:
    if result.timed_out:
        raise RehearsalCommandError(f"{operation} timed out")
    if result.returncode != 0:
        raise RehearsalCommandError(f"{operation} failed")
    return result.stdout


def _git(*arguments: str) -> str:
    result = run_command(["git", *arguments], timeout=30)
    return _require_success(result, operation="source binding check").strip()


def source_alembic_head(repo_root: Path = REPO_ROOT) -> str:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1 or not REVISION_RE.fullmatch(str(heads[0])):
        raise RehearsalRefusal("source checkout does not have one valid Alembic head")
    return str(heads[0])


def verify_source_checkout(repo_root: Path = REPO_ROOT) -> SourceBinding:
    if repo_root.resolve(strict=True) != REPO_ROOT.resolve(strict=True):
        raise RehearsalRefusal("rehearsal must run from its canonical checkout")
    if _git("branch", "--show-current") != "main":
        raise RehearsalRefusal("source checkout must be the main branch")
    commit = _git("rev-parse", "--verify", "HEAD^{commit}")
    remote_commit = _git("rev-parse", "--verify", "refs/remotes/origin/main^{commit}")
    if not COMMIT_RE.fullmatch(commit) or commit != remote_commit:
        raise RehearsalRefusal("main must be pushed exactly to origin/main")
    remote_lines = [
        line.split()
        for line in _git(
            "ls-remote", "--exit-code", "origin", "refs/heads/main"
        ).splitlines()
        if line.strip()
    ]
    if remote_lines != [[commit, "refs/heads/main"]]:
        raise RehearsalRefusal("remote main does not resolve uniquely to this commit")
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise RehearsalRefusal("source checkout must be clean")
    branch_points = [
        line.strip()
        for line in _git("for-each-ref", "--format=%(objectname)", "refs/heads/main").splitlines()
        if line.strip()
    ]
    if branch_points != [commit]:
        raise RehearsalRefusal("main must resolve to one unique commit")
    tree = _git("rev-parse", "--verify", "HEAD^{tree}")
    if not COMMIT_RE.fullmatch(tree):
        raise RehearsalRefusal("source tree binding is invalid")
    return SourceBinding(commit=commit, tree=tree, alembic_head=source_alembic_head(repo_root))


def assert_source_binding(expected: SourceBinding) -> None:
    actual = verify_source_checkout()
    if actual != expected:
        raise RehearsalRefusal("source checkout drifted during the rehearsal")


def _require_private_regular_file(path: Path, *, approved_root: Path) -> Path:
    supplied = path.expanduser()
    root = approved_root.expanduser()
    if (
        not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or root.resolve(strict=True) != root
    ):
        raise RehearsalRefusal("backup artifact approved root is unsafe")
    root_metadata = root.stat()
    if (
        root_metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(root_metadata.st_mode) != 0o700
    ):
        raise RehearsalRefusal("backup artifact approved root ownership or mode is unsafe")
    if not supplied.is_absolute() or supplied.is_symlink() or not supplied.is_file():
        raise RehearsalRefusal("backup artifact is not a canonical regular file")
    canonical = supplied.resolve(strict=True)
    if canonical != supplied or canonical == root or root not in canonical.parents:
        raise RehearsalRefusal("backup artifact is outside its approved root")
    parent_metadata = canonical.parent.stat()
    if (
        canonical.parent.is_symlink()
        or parent_metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
    ):
        raise RehearsalRefusal("backup artifact parent ownership or mode is unsafe")
    metadata = canonical.stat()
    if metadata.st_uid not in {0, os.geteuid()} or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RehearsalRefusal("backup artifact ownership or mode is unsafe")
    return canonical


def _validate_plain_gzip_dump(path: Path) -> None:
    try:
        with path.open("rb") as raw:
            if raw.read(2) != b"\x1f\x8b":
                raise RehearsalRefusal("database artifact is not gzip")
        with gzip.open(path, "rb") as handle:
            prefix = handle.read(8192)
            while handle.read(1024 * 1024):
                pass
    except (OSError, EOFError) as exc:
        raise RehearsalRefusal("database artifact gzip integrity failed") from exc
    if prefix.startswith(b"PGDMP") or b"PostgreSQL database dump" not in prefix:
        raise RehearsalRefusal("database artifact is not a plain PostgreSQL SQL dump")


def _validate_tar_gzip(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise RehearsalRefusal("backup archive contains an unsafe member path")
    except (OSError, tarfile.TarError) as exc:
        raise RehearsalRefusal("backup artifact is not a valid tar.gz archive") from exc


def _secure_receipt_file(path: Path) -> Path:
    supplied = path.expanduser()
    root = DEFAULT_BACKUP_RECEIPT_DIR
    if (
        not supplied.is_absolute()
        or supplied.parent != root
        or supplied.is_symlink()
        or not supplied.is_file()
        or supplied.resolve(strict=True) != supplied
        or root.is_symlink()
        or not root.is_dir()
        or root.resolve(strict=True) != root
    ):
        raise RehearsalRefusal("backup receipt path is not the hardened receipt path")
    root_metadata = root.stat()
    if (
        root_metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(root_metadata.st_mode) != 0o700
    ):
        raise RehearsalRefusal("backup receipt root ownership or mode is unsafe")
    metadata = supplied.stat()
    if metadata.st_uid not in {0, os.geteuid()} or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RehearsalRefusal("backup receipt ownership or mode is unsafe")
    return supplied


def _result_by_role(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    declared_roles = payload.get("roles")
    if not isinstance(declared_roles, list) or declared_roles != list(EXPECTED_ROLES):
        raise RehearsalRefusal("backup receipt must bind foreign and Iran in order")
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != 2:
        raise RehearsalRefusal("backup receipt must contain exactly two results")
    indexed: dict[str, Mapping[str, Any]] = {}
    for result in results:
        if not isinstance(result, Mapping):
            raise RehearsalRefusal("backup receipt result is malformed")
        role = str(result.get("role") or "")
        if role not in EXPECTED_ROLES or role in indexed or result.get("command_role") != role:
            raise RehearsalRefusal("backup receipt role binding is invalid")
        indexed[role] = result
    if tuple(indexed) != EXPECTED_ROLES:
        raise RehearsalRefusal("backup receipt role order is invalid")
    return indexed


def _backup_file_items(result: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    files = result.get("files")
    if not isinstance(files, list) or len(files) != len(EXPECTED_BACKUP_KINDS):
        raise RehearsalRefusal("backup receipt files are missing")
    indexed: dict[str, Mapping[str, Any]] = {}
    for item in files:
        if not isinstance(item, Mapping):
            raise RehearsalRefusal("backup receipt artifact is malformed")
        kind = str(item.get("kind") or "")
        if kind not in EXPECTED_BACKUP_KINDS or kind in indexed:
            raise RehearsalRefusal("backup artifact kinds are incomplete or duplicated")
        indexed[kind] = item
    if set(indexed) != set(EXPECTED_BACKUP_KINDS):
        raise RehearsalRefusal("backup receipt must contain DB, Redis, uploads, and audit")
    return indexed


def _recorded_backup_path(role: str, raw_path: object) -> PurePosixPath:
    """Validate a receipt path lexically without touching a remote host."""

    recorded = str(raw_path or "")
    path = PurePosixPath(recorded)
    root = PurePosixPath(str(DEFAULT_BACKUP_DIR))
    if (
        not recorded
        or not path.is_absolute()
        or recorded != path.as_posix()
        or path == root
        or path.parent.parent != root
        or not path.parent.name.startswith(f"{role}-")
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise RehearsalRefusal("backup artifact recorded path is outside its exact run root")
    return path


def _artifact_path(role: str, result: Mapping[str, Any], item: Mapping[str, Any]) -> Path:
    recorded_path = _recorded_backup_path(role, item.get("path"))
    recorded = recorded_path.as_posix()
    if role == "foreign":
        return _require_private_regular_file(Path(recorded), approved_root=Path(DEFAULT_BACKUP_DIR))
    pulled = result.get("pulled_files")
    if not isinstance(pulled, list):
        raise RehearsalRefusal("Iran dump was not pulled into the approved secure root")
    matches = [
        entry
        for entry in pulled
        if isinstance(entry, Mapping) and str(entry.get("remote_path") or "") == recorded
    ]
    if len(matches) != 1:
        raise RehearsalRefusal("Iran dump pull binding is missing or ambiguous")
    local_path = _require_private_regular_file(
        Path(str(matches[0].get("local_path") or "")),
        approved_root=DEFAULT_IRAN_PULL_DIR,
    )
    if local_path.parent != DEFAULT_IRAN_PULL_DIR or local_path.name != recorded_path.name:
        raise RehearsalRefusal("Iran pulled artifact path does not match its remote artifact")
    return local_path


def _verify_restore_cleanup_proof(
    restore_smoke: Mapping[str, Any], *, backup_run_dir: PurePosixPath
) -> None:
    cleanup = restore_smoke.get("cleanup")
    if not isinstance(cleanup, Mapping):
        raise RehearsalRefusal("backup restore cleanup proof is missing")
    owned_volume_count = cleanup.get("owned_volume_count")
    if (
        isinstance(owned_volume_count, bool)
        or not isinstance(owned_volume_count, int)
        or owned_volume_count < 0
    ):
        raise RehearsalRefusal("backup restore cleanup proof is invalid")
    volume_names_sha256 = str(cleanup.get("owned_volume_names_sha256") or "")
    proof_sha256 = str(cleanup.get("proof_sha256") or "")
    if (
        cleanup.get("status") != "passed"
        or cleanup.get("container_absent") is not True
        or cleanup.get("named_volume_absent") is not True
        or cleanup.get("owned_volumes_absent") is not True
        or cleanup.get("commands_bounded") is not True
        or cleanup.get("error") not in (None, "")
        or not SHA256_RE.fullmatch(volume_names_sha256)
        or not SHA256_RE.fullmatch(proof_sha256)
    ):
        raise RehearsalRefusal("backup restore cleanup proof is invalid")
    expected_proof = hashlib.sha256(
        (
            f"{backup_run_dir.name}\0true\0true\0{owned_volume_count}"
            f"\0true\0{volume_names_sha256}"
        ).encode("utf-8")
    ).hexdigest()
    if proof_sha256 != expected_proof:
        raise RehearsalRefusal("backup restore cleanup proof digest does not match")


def verify_backup_receipt(
    *,
    receipt_path: Path,
    receipt_sha256: str,
    expected_release_sha: str,
    manifest_values: Mapping[str, str],
    max_age_seconds: int = DEFAULT_MAX_BACKUP_AGE_SECONDS,
    now: datetime | None = None,
) -> VerifiedBackup:
    if not SHA256_RE.fullmatch(receipt_sha256):
        raise RehearsalRefusal("backup receipt digest is invalid")
    if not COMMIT_RE.fullmatch(expected_release_sha):
        raise RehearsalRefusal("expected production release binding is invalid")
    if not 60 <= max_age_seconds <= 6 * 60 * 60:
        raise RehearsalRefusal("backup freshness bound is outside the allowed range")
    receipt_path = _secure_receipt_file(receipt_path)
    if _sha256_file(receipt_path) != receipt_sha256:
        raise RehearsalRefusal("backup receipt digest does not match")
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RehearsalRefusal("backup receipt JSON is invalid") from exc
    if not isinstance(payload, Mapping) or payload.get("status") != "ok":
        raise RehearsalRefusal("backup receipt is not successful")
    created_at = _parse_utc(payload.get("created_at"))
    age = ((now or _utc_now()) - created_at).total_seconds()
    if age < -300 or age > max_age_seconds:
        raise RehearsalRefusal("backup receipt is not fresh")
    indexed = _result_by_role(payload)
    artifacts: list[DumpArtifact] = []
    artifact_bindings: list[dict[str, object]] = []
    database_identities: set[str] = set()
    for role in EXPECTED_ROLES:
        result = indexed[role]
        items = _backup_file_items(result)
        recorded_paths = {
            _recorded_backup_path(role, item.get("path"))
            for item in items.values()
        }
        backup_run_dirs = {path.parent for path in recorded_paths}
        if (
            len(backup_run_dirs) != 1
            or str(result.get("backup_dir") or "")
            != next(iter(backup_run_dirs)).as_posix()
        ):
            raise RehearsalRefusal(
                "backup result does not bind the approved backup root and exact run root"
            )
        backup_run_dir = next(iter(backup_run_dirs))
        if result.get("status") != "ok" or result.get("schema_head") != EXPECTED_PRE_MIGRATION_HEAD:
            raise RehearsalRefusal("backup result status or pre-migration schema is invalid")
        if result.get("project_label") != EXPECTED_PROJECT_LABELS[role]:
            raise RehearsalRefusal("backup compose-project binding is invalid")
        if result.get("release_sha") != expected_release_sha:
            raise RehearsalRefusal("backup release binding does not match")
        result_created_at = _parse_utc(result.get("created_at"))
        result_age = ((now or _utc_now()) - result_created_at).total_seconds()
        if (
            result_age < -300
            or result_age > max_age_seconds
            or abs((result_created_at - created_at).total_seconds()) > 15 * 60
        ):
            raise RehearsalRefusal("backup result timestamp is not fresh or receipt-bound")
        restore_smoke = result.get("restore_smoke")
        if not isinstance(restore_smoke, Mapping) or restore_smoke.get("status") != "passed":
            raise RehearsalRefusal("backup restore smoke did not pass")
        _verify_restore_cleanup_proof(
            restore_smoke, backup_run_dir=backup_run_dir
        )
        expected_target = backup_target_binding_sha256(role, dict(manifest_values))
        target_binding = str(result.get("target_binding_sha256") or "")
        if target_binding != expected_target:
            raise RehearsalRefusal("backup target binding does not match the manifest")
        database_identity = str(result.get("database_identity_sha256") or "")
        if not SHA256_RE.fullmatch(database_identity) or database_identity in database_identities:
            raise RehearsalRefusal("database identity binding is invalid or duplicated")
        database_identities.add(database_identity)
        if role == "foreign" and result.get("pulled_files") not in (None, []):
            raise RehearsalRefusal("foreign backup must not claim pulled artifacts")
        if role == "iran":
            pulled = result.get("pulled_files")
            if not isinstance(pulled, list) or len(pulled) != len(EXPECTED_BACKUP_KINDS):
                raise RehearsalRefusal("all four Iran artifacts must be pulled locally")
            remote_paths = {
                str(item.get("path") or "") for item in items.values()
            }
            pulled_remote = {
                str(entry.get("remote_path") or "")
                for entry in pulled
                if isinstance(entry, Mapping)
            }
            pulled_local = [
                str(entry.get("local_path") or "")
                for entry in pulled
                if isinstance(entry, Mapping)
            ]
            if (
                len(pulled_remote) != 4
                or pulled_remote != remote_paths
                or len(pulled_local) != 4
                or len(set(pulled_local)) != 4
            ):
                raise RehearsalRefusal("Iran pulled artifact set is incomplete or ambiguous")
        db_path: Path | None = None
        db_sha = ""
        db_size = 0
        for kind in EXPECTED_BACKUP_KINDS:
            item = items[kind]
            artifact_sha = str(item.get("sha256") or "")
            try:
                artifact_size = int(item.get("bytes") or 0)
            except (TypeError, ValueError) as exc:
                raise RehearsalRefusal("backup artifact size is invalid") from exc
            if not SHA256_RE.fullmatch(artifact_sha) or artifact_size <= 0:
                raise RehearsalRefusal("backup artifact metadata is invalid")
            path = _artifact_path(role, result, item)
            expected_suffixes = [".sql", ".gz"] if kind == "db" else [".tar", ".gz"]
            if path.suffixes[-2:] != expected_suffixes:
                raise RehearsalRefusal("backup artifact extension does not match its kind")
            if path.stat().st_size != artifact_size or _sha256_file(path) != artifact_sha:
                raise RehearsalRefusal("backup artifact hash or size does not match")
            if kind == "db":
                _validate_plain_gzip_dump(path)
                db_path, db_sha, db_size = path, artifact_sha, artifact_size
            else:
                _validate_tar_gzip(path)
            artifact_bindings.append(
                {
                    "role": role,
                    "kind": kind,
                    "sha256": artifact_sha,
                    "size_bytes": artifact_size,
                }
            )
        if db_path is None:
            raise RehearsalRefusal("verified database dump is missing")
        artifacts.append(
            DumpArtifact(
                role=role,
                path=db_path,
                sha256=db_sha,
                size_bytes=db_size,
                release_sha=expected_release_sha,
                database_identity_sha256=database_identity,
                target_binding_sha256=target_binding,
            )
        )
    return VerifiedBackup(
        receipt_sha256=receipt_sha256,
        created_at=str(payload.get("created_at")),
        production_release_sha=expected_release_sha,
        artifacts=tuple(artifacts),
        artifact_set_sha256=hashlib.sha256(
            json.dumps(
                artifact_bindings, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
    )


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def extract_owner_roles(dump_path: Path) -> tuple[str, ...]:
    roles: set[str] = set()
    owner_pattern = re.compile(r"\bOWNER TO\s+((?:\"(?:[^\"]|\"\")*\")|(?:[^;\s]+))\s*;")
    try:
        with gzip.open(dump_path, "rt", encoding="utf-8", errors="strict") as handle:
            for line in handle:
                if len(line) > 1024 * 1024:
                    raise RehearsalRefusal("database dump contains an oversized SQL line")
                for match in owner_pattern.finditer(line):
                    raw = match.group(1)
                    role = raw[1:-1].replace('""', '"') if raw.startswith('"') else raw
                    if not SAFE_ROLE_RE.fullmatch(role):
                        raise RehearsalRefusal("database dump contains an unsafe owner role")
                    if role != "postgres":
                        roles.add(role)
    except (OSError, UnicodeError) as exc:
        raise RehearsalRefusal("database dump could not be read as plain UTF-8 SQL") from exc
    return tuple(sorted(roles))


def _deny_runtime_identifier(value: str) -> None:
    normalized = value.strip().lower()
    if (
        not re.fullmatch(r"[a-z0-9_]{8,63}", normalized)
        or normalized in DENIED_DOCKER_IDENTIFIERS
        or normalized.startswith("trading_bot")
        or "production" in normalized
        or "staging" in normalized
    ):
        raise RehearsalRefusal("generated Docker identifier violated the isolation contract")


def allocate_resources(run_id: str) -> DockerResources:
    nonce = secrets.token_hex(12)
    network = f"tbmr_net_{nonce}"
    _deny_runtime_identifier(network)
    return DockerResources(
        run_id=run_id,
        network_name=network,
        container_names=[],
        volume_names=[],
    )


def export_committed_migration_source(
    source: SourceBinding, *, run_id: str
) -> CommittedSource:
    """Export only committed migration code, excluding worktree secrets."""

    root = _ensure_secure_directory(DEFAULT_WORK_ROOT, create=True)
    run_dir = root / run_id
    if (
        not re.fullmatch(r"tbmr-[0-9a-f]{32}", run_id)
        or run_dir.exists()
        or run_dir.is_symlink()
    ):
        raise RehearsalRefusal("committed-source run directory is not unique")
    run_dir.mkdir(mode=0o700)
    try:
        archive = run_dir / "migration-source.tar"
        script = (
            'set -euo pipefail; umask 077; set -o noclobber; '
            'git archive --format=tar "$2" -- alembic.ini migrations models core '
            'scripts/run_guarded_scratch_alembic.py > "$1"'
        )
        result = run_command(
            ["bash", "-c", script, "source-export", str(archive), source.commit],
            timeout=300,
        )
        _require_success(result, operation="committed migration source export")
        archive.chmod(0o600)
        source_root = run_dir / "source"
        source_root.mkdir(mode=0o700)
        with tarfile.open(archive, "r:") as bundle:
            members = bundle.getmembers()
            for member in members:
                path = Path(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not (member.isfile() or member.isdir())
                ):
                    raise RehearsalRefusal("committed migration archive is unsafe")
                target = source_root.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    target.chmod(0o700)
                    continue
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source_handle = bundle.extractfile(member)
                if source_handle is None:
                    raise RehearsalRefusal("committed migration archive file is unreadable")
                descriptor = os.open(
                    target,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                with source_handle, os.fdopen(descriptor, "wb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle)
                    target_handle.flush()
                    os.fsync(target_handle.fileno())
        if not (source_root / "alembic.ini").is_file() or not (
            source_root / "scripts" / "run_guarded_scratch_alembic.py"
        ).is_file():
            raise RehearsalRefusal("committed migration source is incomplete")
        return CommittedSource(path=source_root, archive_sha256=_sha256_file(archive))
    except BaseException as exc:
        if not cleanup_committed_source(None, run_id=run_id):
            raise RehearsalRefusal(
                "committed migration export failed and cleanup is incomplete"
            ) from exc
        if isinstance(exc, RehearsalInterrupted):
            raise
        if isinstance(exc, (RehearsalRefusal, RehearsalCommandError)):
            raise
        raise RehearsalRefusal(
            "committed migration archive could not be exported or extracted"
        ) from exc


def cleanup_committed_source(committed: CommittedSource | None, *, run_id: str) -> bool:
    root = DEFAULT_WORK_ROOT
    run_dir = root / run_id
    if (
        not re.fullmatch(r"tbmr-[0-9a-f]{32}", run_id)
        or run_dir.parent != root
        or run_dir.is_symlink()
        or run_dir.name != run_id
        or (committed is not None and committed.path.parent != run_dir)
    ):
        return False
    if not run_dir.exists():
        return True
    if not run_dir.is_dir():
        return False
    try:
        shutil.rmtree(run_dir)
    except OSError:
        return False
    return not run_dir.exists()


def _docker(*arguments: str, timeout: int = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> CommandResult:
    return run_command(["docker", *arguments], timeout=timeout)


def verify_runner_prebuild_receipt(
    *,
    receipt_path: Path,
    receipt_sha256: str,
    expected_image_id: str,
    source: SourceBinding,
) -> RunnerPrebuildBinding:
    supplied = receipt_path.expanduser()
    if (
        supplied != DEFAULT_RUNNER_PREBUILD_RECEIPT
        or not supplied.is_absolute()
        or supplied.is_symlink()
        or not supplied.is_file()
        or supplied.resolve(strict=True) != supplied
        or not SHA256_RE.fullmatch(receipt_sha256)
        or not IMAGE_ID_RE.fullmatch(expected_image_id)
    ):
        raise RehearsalRefusal("runner prebuild receipt path or binding is invalid")
    metadata = supplied.stat()
    if metadata.st_uid not in {0, os.geteuid()} or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RehearsalRefusal("runner prebuild receipt ownership or mode is unsafe")
    parent_metadata = supplied.parent.stat()
    if (
        supplied.parent.is_symlink()
        or parent_metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
    ):
        raise RehearsalRefusal("runner prebuild receipt parent ownership or mode is unsafe")
    if _sha256_file(supplied) != receipt_sha256:
        raise RehearsalRefusal("runner prebuild receipt digest does not match")
    try:
        payload = json.loads(supplied.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RehearsalRefusal("runner prebuild receipt JSON is invalid") from exc
    input_signature = (
        str(payload.get("input_signature") or "")
        if isinstance(payload, Mapping)
        else ""
    )
    expected_payload = {
        "schema_version": 1,
        "environment": "production",
        "release_sha": source.commit,
        "release_tree": source.tree,
        "image_id": expected_image_id,
        "input_signature": input_signature,
        "secrets_disclosed": False,
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != RUNNER_PREBUILD_RECEIPT_KEYS
        or not SHA256_RE.fullmatch(input_signature)
        or dict(payload) != expected_payload
    ):
        raise RehearsalRefusal("runner prebuild receipt does not bind source/tree/image")
    return RunnerPrebuildBinding(
        receipt_path=supplied,
        receipt_sha256=receipt_sha256,
        image_id=expected_image_id,
        release_sha=source.commit,
        release_tree=source.tree,
        input_signature=input_signature,
    )


def _runner_image_inspection(
    output: str,
    *,
    expected_image_id: str,
    source: SourceBinding,
    prebuild: RunnerPrebuildBinding,
) -> tuple[str, str, str]:
    try:
        payloads = json.loads(output)
    except ValueError as exc:
        raise RehearsalCommandError("migration runner image inspection was invalid") from exc
    if (
        not isinstance(payloads, list)
        or len(payloads) != 1
        or not isinstance(payloads[0], Mapping)
    ):
        raise RehearsalCommandError("migration runner image is not uniquely resolved")
    payload = payloads[0]
    if payload.get("Id") != expected_image_id:
        raise RehearsalRefusal("migration runner tag does not match the expected immutable ID")
    config = payload.get("Config")
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    revision = (
        str(labels.get("org.opencontainers.image.revision") or "").strip()
        if isinstance(labels, Mapping)
        else ""
    )
    release_tree = (
        str(labels.get("io.gold-trade.release.tree") or "").strip()
        if isinstance(labels, Mapping)
        else ""
    )
    input_signature = (
        str(labels.get("io.gold-trade.release.input-signature") or "").strip()
        if isinstance(labels, Mapping)
        else ""
    )
    if (
        revision != source.commit
        or release_tree != source.tree
        or input_signature != prebuild.input_signature
    ):
        raise RehearsalRefusal("migration runner OCI identity does not match prebuild/source")
    return revision, release_tree, input_signature


def verify_runner_image(
    prebuild: RunnerPrebuildBinding, *, source: SourceBinding
) -> RunnerImageBinding:
    """Bind the mutable local tag to an operator-supplied immutable image ID."""

    expected_image_id = prebuild.image_id
    if (
        not IMAGE_ID_RE.fullmatch(expected_image_id)
        or prebuild.release_sha != source.commit
        or prebuild.release_tree != source.tree
        or not SHA256_RE.fullmatch(prebuild.input_signature)
    ):
        raise RehearsalRefusal("migration runner image ID is invalid")
    # The build receipt is part of the runtime identity, not a one-time hint.
    # Re-read it around every runner invocation so replacement after planning
    # is detected before any migration process starts.
    current_prebuild = verify_runner_prebuild_receipt(
        receipt_path=prebuild.receipt_path,
        receipt_sha256=prebuild.receipt_sha256,
        expected_image_id=prebuild.image_id,
        source=source,
    )
    if current_prebuild != prebuild:
        raise RehearsalRefusal("migration runner prebuild binding drifted")
    output = _require_success(
        _docker("image", "inspect", MIGRATION_RUNNER_IMAGE, timeout=30),
        operation="migration runner image inspection",
    )
    revision, release_tree, input_signature = _runner_image_inspection(
        output,
        expected_image_id=expected_image_id,
        source=source,
        prebuild=prebuild,
    )
    # Prove the immutable reference itself still resolves to the same image;
    # callers run this check before/after every migration container.
    exact_output = _require_success(
        _docker("image", "inspect", expected_image_id, timeout=30),
        operation="immutable migration runner image inspection",
    )
    exact_revision, exact_tree, exact_signature = _runner_image_inspection(
        exact_output,
        expected_image_id=expected_image_id,
        source=source,
        prebuild=prebuild,
    )
    if (exact_revision, exact_tree, exact_signature) != (
        revision,
        release_tree,
        input_signature,
    ):
        raise RehearsalRefusal("immutable migration runner image binding drifted")
    return RunnerImageBinding(
        image_id=expected_image_id,
        oci_revision=revision,
        release_tree=release_tree,
        input_signature=input_signature,
        prebuild_receipt_sha256=prebuild.receipt_sha256,
    )


def _inspect_json(resource: str) -> Mapping[str, Any]:
    output = _require_success(_docker("inspect", resource, timeout=30), operation="Docker inspection")
    try:
        payload = json.loads(output)
    except ValueError as exc:
        raise RehearsalCommandError("Docker inspection returned invalid JSON") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], Mapping):
        raise RehearsalCommandError("Docker inspection did not identify one resource")
    return payload[0]


def _owned_label(inspect_payload: Mapping[str, Any]) -> str:
    config = inspect_payload.get("Config")
    if isinstance(config, Mapping):
        labels = config.get("Labels")
        if isinstance(labels, Mapping):
            return str(labels.get(RESOURCE_LABEL) or "")
    labels = inspect_payload.get("Labels")
    if isinstance(labels, Mapping):
        return str(labels.get(RESOURCE_LABEL) or "")
    return ""


def create_internal_network(resources: DockerResources) -> None:
    result = _docker(
        "network",
        "create",
        "--internal",
        "--label",
        f"{RESOURCE_LABEL}={resources.run_id}",
        resources.network_name,
        timeout=60,
    )
    _require_success(result, operation="disposable internal network creation")
    payload = _inspect_json(resources.network_name)
    if payload.get("Internal") is not True or _owned_label(payload) != resources.run_id:
        raise RehearsalCommandError("disposable network isolation could not be proven")


def _random_container_name(kind: str) -> str:
    name = f"tbmr_{kind}_{secrets.token_hex(12)}"
    _deny_runtime_identifier(name)
    return name


def start_postgres(
    resources: DockerResources,
    *,
    role: str,
    username: str,
    password: str,
    database: str,
) -> str:
    name = _random_container_name(f"pg_{role}")
    resources.container_names.append(name)
    result = _docker(
        "run",
        "-d",
        "--pull",
        "never",
        "--name",
        name,
        "--label",
        f"{RESOURCE_LABEL}={resources.run_id}",
        "--network",
        resources.network_name,
        "--mount",
        "type=volume,destination=/var/lib/postgresql/data",
        "--env",
        f"POSTGRES_USER={username}",
        "--env",
        f"POSTGRES_PASSWORD={password}",
        "--env",
        f"POSTGRES_DB={database}",
        POSTGRES_IMAGE,
        timeout=120,
    )
    _require_success(result, operation="disposable PostgreSQL start")
    payload = _inspect_json(name)
    host_config = payload.get("HostConfig")
    network_settings = payload.get("NetworkSettings")
    mounts = payload.get("Mounts")
    port_bindings = host_config.get("PortBindings") if isinstance(host_config, Mapping) else None
    networks = network_settings.get("Networks") if isinstance(network_settings, Mapping) else None
    volume_mounts = [
        mount
        for mount in mounts or []
        if isinstance(mount, Mapping)
        and mount.get("Type") == "volume"
        and mount.get("Destination") == "/var/lib/postgresql/data"
    ]
    if (
        _owned_label(payload) != resources.run_id
        or port_bindings not in (None, {})
        or not isinstance(networks, Mapping)
        or set(networks) != {resources.network_name}
        or len(volume_mounts) != 1
    ):
        raise RehearsalCommandError("disposable PostgreSQL isolation could not be proven")
    volume_name = str(volume_mounts[0].get("Name") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", volume_name):
        raise RehearsalCommandError("anonymous PostgreSQL volume identity is invalid")
    resources.volume_names.append(volume_name)
    for _ in range(90):
        ready = _docker("exec", name, "pg_isready", "-U", username, "-d", database, timeout=10)
        if ready.returncode == 0:
            version = psql(
                name,
                username=username,
                database=database,
                sql="SHOW server_version_num;",
            )
            if not version.isdigit() or not 150000 <= int(version) < 160000:
                raise RehearsalCommandError("disposable PostgreSQL is not major version 15")
            return name
        time.sleep(1)
    raise RehearsalCommandError("disposable PostgreSQL did not become ready")


def psql(
    container: str,
    *,
    username: str,
    database: str,
    sql: str,
    tuples_only: bool = True,
) -> str:
    arguments = ["exec", container, "psql", "-v", "ON_ERROR_STOP=1"]
    if tuples_only:
        arguments.extend(["-A", "-t"])
    arguments.extend(["-U", username, "-d", database, "-c", sql])
    return _require_success(_docker(*arguments), operation="scratch database query").strip()


def create_owner_roles(
    container: str,
    *,
    username: str,
    database: str,
    roles: Sequence[str],
) -> None:
    for role in roles:
        if not SAFE_ROLE_RE.fullmatch(role):
            raise RehearsalRefusal("unsafe restore owner role")
        identifier = _quote_identifier(role)
        literal = role.replace("'", "''")
        sql = (
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{literal}') THEN CREATE ROLE {identifier}; END IF; END $$;"
        )
        psql(container, username=username, database=database, sql=sql)


def restore_plain_dump(
    container: str,
    *,
    username: str,
    database: str,
    dump_path: Path,
    timeout: int,
) -> None:
    # The fixed shell reads positional parameters only; neither the artifact
    # path nor the container/database names are interpolated into shell code.
    script = (
        'set -o pipefail; gzip -dc -- "$1" | '
        'docker exec -i "$2" psql -v ON_ERROR_STOP=1 -U "$3" -d "$4"'
    )
    result = run_command(
        ["bash", "-c", script, "rehearsal-restore", str(dump_path), container, username, database],
        timeout=timeout,
    )
    _require_success(result, operation="plain production dump restore")


def _query_public_tables(container: str, username: str, database: str) -> tuple[str, ...]:
    output = psql(
        container,
        username=username,
        database=database,
        sql=(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "ORDER BY tablename;"
        ),
    )
    return tuple(line for line in output.splitlines() if line)


def _query_preexisting_table_counts(
    container: str,
    username: str,
    database: str,
    tables: Sequence[str],
) -> dict[str, int]:
    table_set = set(tables)
    missing = sorted(set(CRITICAL_PRESERVED_TABLES) - table_set)
    if missing:
        raise RehearsalCommandError("restored database is missing critical preserved tables")
    counts: dict[str, int] = {}
    for table in sorted(table_set):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", table):
            raise RehearsalCommandError("restored database has an unsafe public table name")
        value = psql(
            container,
            username=username,
            database=database,
            sql=f"SELECT count(*) FROM {_quote_identifier(table)};",
        )
        if not value.isdigit():
            raise RehearsalCommandError("pre-existing table count query returned an invalid value")
        counts[table] = int(value)
    if counts["users"] <= 0 or counts["commodities"] <= 0:
        raise RehearsalCommandError("restored database does not contain required production seeds")
    return counts


def _query_new_table_counts(
    container: str, username: str, database: str
) -> dict[str, int]:
    tables = set(_query_public_tables(container, username, database))
    if not set(EXPECTED_NEW_TABLES).issubset(tables):
        raise RehearsalCommandError("target migration tables are incomplete")
    counts: dict[str, int] = {}
    for table in EXPECTED_NEW_TABLES:
        value = psql(
            container,
            username=username,
            database=database,
            sql=f"SELECT count(*) FROM {_quote_identifier(table)};",
        )
        if not value.isdigit():
            raise RehearsalCommandError("new-table count query returned an invalid value")
        counts[table] = int(value)
    expected = {table: 0 for table in EXPECTED_NEW_TABLES}
    expected["telegram_delivery_feeder_states"] = 1
    if counts != expected:
        raise RehearsalCommandError("new-table seed contract does not match")
    return counts


def _invalid_index_count(container: str, username: str, database: str) -> int:
    output = psql(
        container,
        username=username,
        database=database,
        sql=(
            "SELECT count(*) FROM pg_index AS i "
            "JOIN pg_class AS c ON c.oid=i.indexrelid "
            "JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND (NOT i.indisvalid OR NOT i.indisready);"
        ),
    )
    if not output.isdigit():
        raise RehearsalCommandError("index readiness query returned an invalid value")
    return int(output)


def _concurrent_index_state(container: str, username: str, database: str) -> str:
    return psql(
        container,
        username=username,
        database=database,
        sql=(
            "SELECT CASE WHEN i.indisvalid AND i.indisready THEN 'valid-ready' "
            "ELSE 'invalid' END FROM pg_index AS i "
            "JOIN pg_class AS c ON c.oid=i.indexrelid "
            "JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            f"WHERE n.nspname='public' AND c.relname='{EXPECTED_CONCURRENT_INDEX}';"
        ),
    )


def _current_revision(container: str, username: str, database: str) -> str:
    output = psql(
        container,
        username=username,
        database=database,
        sql="SELECT version_num FROM alembic_version ORDER BY version_num;",
    )
    revisions = [line for line in output.splitlines() if line]
    if len(revisions) != 1 or not REVISION_RE.fullmatch(revisions[0]):
        raise RehearsalCommandError("scratch database does not have one Alembic revision")
    return revisions[0]


def schema_only_sha256(container: str, username: str, database: str) -> str:
    result = _docker(
        "exec",
        container,
        "pg_dump",
        "-U",
        username,
        "-d",
        database,
        "--schema-only",
        "--no-owner",
        "--no-privileges",
        timeout=300,
    )
    output = _require_success(result, operation="schema-only dump")
    normalized = "\n".join(
        line
        for line in output.splitlines()
        if not line.startswith("-- Dumped from database version")
        and not line.startswith("-- Dumped by pg_dump version")
        # PostgreSQL emits a fresh random key for these psql meta-commands on
        # every dump.  They protect restore input parsing but do not describe
        # database schema, so retaining them would make two byte-equivalent
        # schemas appear different during the second-upgrade no-op gate.
        and not re.fullmatch(r"\\(?:un)?restrict [A-Za-z0-9]+", line)
    )
    return hashlib.sha256((normalized + "\n").encode("utf-8")).hexdigest()


def run_guarded_alembic(
    resources: DockerResources,
    *,
    pg_container: str,
    username: str,
    password: str,
    database: str,
    source: SourceBinding,
    source_root: Path,
    runner_prebuild: RunnerPrebuildBinding,
    arguments: Sequence[str],
    timeout: int,
) -> str:
    if list(arguments) not in (["current"], ["upgrade", "head"]):
        raise RehearsalRefusal("unapproved guarded Alembic command")
    runner_name = _random_container_name("migrate")
    resources.container_names.append(runner_name)
    verify_runner_image(runner_prebuild, source=source)
    runner_image_id = runner_prebuild.image_id
    url = f"postgresql+psycopg2://{username}:{password}@{pg_container}:5432/{database}"
    command = [
        "run",
        "--rm",
        "--pull",
        "never",
        "--name",
        runner_name,
        "--label",
        f"{RESOURCE_LABEL}={resources.run_id}",
        "--network",
        resources.network_name,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m",
        "--volume",
        f"{source_root}:/source:ro",
        "--workdir",
        "/source",
        "--env",
        "TRADING_BOT_MIGRATION_MODE=scratch",
        "--env",
        f"TRADING_BOT_EXPECTED_CHECKOUT=/source",
        "--env",
        f"TRADING_BOT_EXPECTED_ALEMBIC_HEAD={source.alembic_head}",
        "--env",
        f"SYNC_DATABASE_URL={url}",
        "--env",
        f"DATABASE_URL={url}",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        runner_image_id,
        "python",
        "scripts/run_guarded_scratch_alembic.py",
        *arguments,
    ]
    if any(part in {"-p", "--publish", "--publish-all"} for part in command):
        raise RehearsalRefusal("published Docker ports are forbidden")
    result = _docker(*command, timeout=timeout)
    output = _require_success(result, operation="guarded scratch Alembic")
    verify_runner_image(runner_prebuild, source=source)
    # A successful ``docker run --rm`` has already removed this exact,
    # labelled container.  Failed/timed-out runs remain tracked for the
    # label-verifying cleanup path.
    try:
        resources.container_names.remove(runner_name)
    except ValueError:
        pass
    return output


def rehearse_artifact(
    artifact: DumpArtifact,
    *,
    source: SourceBinding,
    source_root: Path,
    runner_prebuild: RunnerPrebuildBinding,
    resources: DockerResources,
    timeout: int,
) -> dict[str, Any]:
    username = f"scratch_{secrets.token_hex(6)}"
    password = secrets.token_hex(24)
    database = f"coin_intelligence_prod_rehearsal_{secrets.token_hex(6)}"
    pg_container = start_postgres(
        resources,
        role=artifact.role,
        username=username,
        password=password,
        database=database,
    )
    roles = extract_owner_roles(artifact.path)
    create_owner_roles(pg_container, username=username, database=database, roles=roles)
    restore_plain_dump(
        pg_container,
        username=username,
        database=database,
        dump_path=artifact.path,
        timeout=timeout,
    )

    pre_tables = _query_public_tables(pg_container, username, database)
    pre_new = sorted(set(EXPECTED_NEW_TABLES) & set(pre_tables))
    pre_revision = _current_revision(pg_container, username, database)
    pre_invalid_indexes = _invalid_index_count(pg_container, username, database)
    pre_concurrent_index = _concurrent_index_state(pg_container, username, database)
    preexisting_counts = _query_preexisting_table_counts(
        pg_container, username, database, pre_tables
    )
    pre_schema_sha = schema_only_sha256(pg_container, username, database)
    if pre_revision != EXPECTED_PRE_MIGRATION_HEAD:
        raise RehearsalCommandError("restored database is not at the expected production head")
    if pre_new:
        raise RehearsalCommandError("restored database already contains target migration tables")
    if pre_invalid_indexes != 0 or pre_concurrent_index:
        raise RehearsalCommandError("restored database has invalid/unready or unexpected target indexes")

    current_before = run_guarded_alembic(
        resources,
        pg_container=pg_container,
        username=username,
        password=password,
        database=database,
        source=source,
        source_root=source_root,
        runner_prebuild=runner_prebuild,
        arguments=["current"],
        timeout=timeout,
    )
    if EXPECTED_PRE_MIGRATION_HEAD not in current_before:
        raise RehearsalCommandError("guarded Alembic did not report the production head")
    run_guarded_alembic(
        resources,
        pg_container=pg_container,
        username=username,
        password=password,
        database=database,
        source=source,
        source_root=source_root,
        runner_prebuild=runner_prebuild,
        arguments=["upgrade", "head"],
        timeout=timeout,
    )
    current_after = run_guarded_alembic(
        resources,
        pg_container=pg_container,
        username=username,
        password=password,
        database=database,
        source=source,
        source_root=source_root,
        runner_prebuild=runner_prebuild,
        arguments=["current"],
        timeout=timeout,
    )
    if source.alembic_head not in current_after:
        raise RehearsalCommandError("guarded Alembic did not reach the source head")

    post_tables = _query_public_tables(pg_container, username, database)
    post_revision = _current_revision(pg_container, username, database)
    post_invalid_indexes = _invalid_index_count(pg_container, username, database)
    post_concurrent_index = _concurrent_index_state(pg_container, username, database)
    post_preexisting_counts = _query_preexisting_table_counts(
        pg_container, username, database, pre_tables
    )
    post_new_table_counts = _query_new_table_counts(pg_container, username, database)
    first_schema_sha = schema_only_sha256(pg_container, username, database)
    added_tables = sorted(set(post_tables) - set(pre_tables))
    if (
        post_revision != source.alembic_head
        or added_tables != sorted(EXPECTED_NEW_TABLES)
        or len(post_tables) - len(pre_tables) != 14
        or post_invalid_indexes != 0
        or post_concurrent_index != "valid-ready"
        or post_preexisting_counts != preexisting_counts
    ):
        raise RehearsalCommandError("first migration pass violated the production schema contract")

    run_guarded_alembic(
        resources,
        pg_container=pg_container,
        username=username,
        password=password,
        database=database,
        source=source,
        source_root=source_root,
        runner_prebuild=runner_prebuild,
        arguments=["upgrade", "head"],
        timeout=timeout,
    )
    final_revision = _current_revision(pg_container, username, database)
    final_tables = _query_public_tables(pg_container, username, database)
    final_preexisting_counts = _query_preexisting_table_counts(
        pg_container, username, database, pre_tables
    )
    final_new_table_counts = _query_new_table_counts(pg_container, username, database)
    final_invalid_indexes = _invalid_index_count(pg_container, username, database)
    final_concurrent_index = _concurrent_index_state(pg_container, username, database)
    second_schema_sha = schema_only_sha256(pg_container, username, database)
    if (
        final_revision != source.alembic_head
        or final_tables != post_tables
        or final_preexisting_counts != post_preexisting_counts
        or final_new_table_counts != post_new_table_counts
        or final_invalid_indexes != 0
        or final_concurrent_index != "valid-ready"
        or second_schema_sha != first_schema_sha
    ):
        raise RehearsalCommandError("second migration pass was not a schema no-op")

    return {
        "role": artifact.role,
        "status": "passed",
        "artifact_sha256": artifact.sha256,
        "artifact_size_bytes": artifact.size_bytes,
        "database_identity_sha256": artifact.database_identity_sha256,
        "target_binding_sha256": artifact.target_binding_sha256,
        "pre_revision": pre_revision,
        "post_revision": post_revision,
        "pre_public_table_count": len(pre_tables),
        "post_public_table_count": len(post_tables),
        "public_table_delta": len(post_tables) - len(pre_tables),
        "added_tables": added_tables,
        "preexisting_table_counts_sha256": hashlib.sha256(
            json.dumps(
                preexisting_counts, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
        "preexisting_table_count_contract": {
            "table_count": len(preexisting_counts),
            "all_row_counts_preserved": True,
        },
        "new_table_counts_sha256": hashlib.sha256(
            json.dumps(
                post_new_table_counts, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
        "new_table_seed_contract": {
            "telegram_delivery_feeder_states": 1,
            "all_other_new_tables": 0,
        },
        "invalid_or_unready_indexes": final_invalid_indexes,
        "concurrent_index_state": final_concurrent_index,
        "schema_before_sha256": pre_schema_sha,
        "schema_after_sha256": first_schema_sha,
        "schema_noop_sha256": second_schema_sha,
        "second_upgrade_noop": True,
    }


def _docker_not_found(result: CommandResult, *, kind: str, name: str) -> bool:
    if result.returncode == 0:
        return False
    message = (result.stderr or "").strip().lower()
    expected_name = re.escape(name.lower())
    prefix = r"(?:error response from daemon: |error: )?"
    patterns = {
        "container": (
            rf"{prefix}no such container: {expected_name}",
            rf"{prefix}no such object: {expected_name}",
        ),
        "network": (
            rf"{prefix}network {expected_name} not found",
            rf"{prefix}no such network: {expected_name}",
        ),
        "volume": (
            rf"{prefix}no such volume: {expected_name}",
            rf"{prefix}get {expected_name}: no such volume",
        ),
    }
    return any(re.fullmatch(pattern, message) for pattern in patterns[kind])


def _require_label_enumeration_empty(
    resources: DockerResources, failures: list[str]
) -> None:
    commands = {
        "container": (
            "ps",
            "-a",
            "--filter",
            f"label={RESOURCE_LABEL}={resources.run_id}",
            "--format",
            "{{.ID}}",
        ),
        "network": (
            "network",
            "ls",
            "--filter",
            f"label={RESOURCE_LABEL}={resources.run_id}",
            "--format",
            "{{.ID}}",
        ),
        "volume": (
            "volume",
            "ls",
            "--filter",
            f"label={RESOURCE_LABEL}={resources.run_id}",
            "--format",
            "{{.Name}}",
        ),
    }
    for kind, command in commands.items():
        result = _docker(*command, timeout=30)
        if result.returncode != 0:
            failures.append(f"{kind}-label-enumeration-failed")
        elif result.stdout.strip():
            failures.append(f"{kind}-label-residue-detected")


def cleanup_owned_resources(resources: DockerResources) -> list[str]:
    failures: list[str] = []
    for name in reversed(resources.container_names):
        inspected = _docker("inspect", name, timeout=30)
        if inspected.returncode != 0:
            if not _docker_not_found(inspected, kind="container", name=name):
                failures.append("container-inspection-failed")
            continue
        try:
            payload = json.loads(inspected.stdout)[0]
        except (ValueError, IndexError, TypeError):
            failures.append("container-inspection-invalid")
            continue
        if not isinstance(payload, Mapping) or _owned_label(payload) != resources.run_id:
            failures.append("container-ownership-unproven")
            continue
        removed = _docker("rm", "-f", "-v", name, timeout=60)
        if removed.returncode != 0 and not _docker_not_found(
            removed, kind="container", name=name
        ):
            failures.append("container-cleanup-failed")
        verified = _docker("inspect", name, timeout=30)
        if not _docker_not_found(verified, kind="container", name=name):
            failures.append("container-residue-or-inspection-failed")

    for name in resources.volume_names:
        inspected = _docker("volume", "inspect", name, timeout=30)
        if _docker_not_found(inspected, kind="volume", name=name):
            continue
        if inspected.returncode != 0:
            failures.append("volume-inspection-failed")
            continue
        # The database volume is intentionally anonymous and therefore has no
        # independently trustworthy label.  It may only be removed as the
        # ``-v`` side effect of deleting its freshly re-inspected, label-owned
        # container above.  Never turn a recorded volume name into standalone
        # deletion authority; a surviving volume makes cleanup fail closed.
        failures.append("anonymous-volume-residue-detected")

    inspected_network = _docker("network", "inspect", resources.network_name, timeout=30)
    if inspected_network.returncode != 0:
        if not _docker_not_found(
            inspected_network, kind="network", name=resources.network_name
        ):
            failures.append("network-inspection-failed")
    else:
        try:
            payload = json.loads(inspected_network.stdout)[0]
        except (ValueError, IndexError, TypeError):
            failures.append("network-inspection-invalid")
        else:
            labels = payload.get("Labels") if isinstance(payload, Mapping) else None
            if not isinstance(labels, Mapping) or labels.get(RESOURCE_LABEL) != resources.run_id:
                failures.append("network-ownership-unproven")
            else:
                removed = _docker("network", "rm", resources.network_name, timeout=60)
                if removed.returncode != 0 and not _docker_not_found(
                    removed, kind="network", name=resources.network_name
                ):
                    failures.append("network-cleanup-failed")
                verified = _docker(
                    "network", "inspect", resources.network_name, timeout=30
                )
                if not _docker_not_found(
                    verified, kind="network", name=resources.network_name
                ):
                    failures.append("network-residue-or-inspection-failed")
    _require_label_enumeration_empty(resources, failures)
    return failures


def _ensure_secure_directory(path: Path, *, create: bool) -> Path:
    supplied = path.expanduser()
    if not supplied.is_absolute() or supplied.is_symlink() or supplied.resolve(strict=False) != supplied:
        raise RehearsalRefusal("secure rehearsal directory path is unsafe")
    parent = supplied.parent
    if not parent.is_dir() or parent.is_symlink() or parent.resolve(strict=True) != parent:
        raise RehearsalRefusal("secure rehearsal directory parent is unsafe")
    parent_meta = parent.stat()
    if parent_meta.st_uid not in {0, os.geteuid()} or stat.S_IMODE(parent_meta.st_mode) != 0o700:
        raise RehearsalRefusal("secure rehearsal directory parent mode is unsafe")
    if not supplied.exists() and create:
        supplied.mkdir(mode=0o700)
    if not supplied.is_dir() or supplied.is_symlink() or supplied.resolve(strict=True) != supplied:
        raise RehearsalRefusal("secure rehearsal directory is unavailable")
    metadata = supplied.stat()
    if metadata.st_uid not in {0, os.geteuid()} or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RehearsalRefusal("secure rehearsal directory ownership or mode is unsafe")
    return supplied


def _acquire_lock(run_id: str) -> tuple[int, tuple[int, int]]:
    root = _ensure_secure_directory(DEFAULT_LOCK_PATH.parent, create=True)
    if DEFAULT_LOCK_PATH.parent != root:
        raise RehearsalRefusal("rehearsal lock root is not exact")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(DEFAULT_LOCK_PATH, flags, 0o600)
    except FileExistsError as exc:
        raise RehearsalRefusal("another rehearsal is active or requires lock review") from exc
    try:
        payload = (json.dumps({"run_id": run_id, "pid": os.getpid()}) + "\n").encode()
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise RehearsalRefusal("rehearsal lock write was incomplete")
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        return descriptor, (metadata.st_dev, metadata.st_ino)
    except BaseException:
        try:
            metadata = DEFAULT_LOCK_PATH.lstat()
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISLNK(metadata.st_mode)
                and metadata.st_dev == opened.st_dev
                and metadata.st_ino == opened.st_ino
            ):
                DEFAULT_LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
        os.close(descriptor)
        raise


def _release_lock(descriptor: int, identity: tuple[int, int]) -> None:
    try:
        metadata = DEFAULT_LOCK_PATH.lstat()
        if (
            not stat.S_ISLNK(metadata.st_mode)
            and (metadata.st_dev, metadata.st_ino) == identity
            and os.fstat(descriptor).st_ino == identity[1]
        ):
            DEFAULT_LOCK_PATH.unlink()
    except FileNotFoundError:
        pass
    finally:
        os.close(descriptor)


def write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    root = _ensure_secure_directory(DEFAULT_RECEIPT_ROOT, create=True)
    supplied = path.expanduser()
    if (
        not supplied.is_absolute()
        or supplied.parent != root
        or supplied.is_symlink()
        or supplied.resolve(strict=False) != supplied
        or supplied.exists()
        or not RECEIPT_NAME_RE.fullmatch(supplied.name)
    ):
        raise RehearsalRefusal("rehearsal receipt path is not approved")
    descriptor = os.open(
        supplied,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _signal_handler(signum: int, _frame: object) -> None:
    raise RehearsalInterrupted(f"signal-{signum}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rehearse current migrations on verified production dumps.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--backup-receipt", required=True, type=Path)
    parser.add_argument("--backup-receipt-sha256", required=True)
    parser.add_argument("--expected-production-release-sha", required=True)
    parser.add_argument("--migration-runner-image-id", required=True)
    parser.add_argument(
        "--migration-runner-prebuild-receipt",
        required=True,
        type=Path,
    )
    parser.add_argument("--migration-runner-prebuild-receipt-sha256", required=True)
    parser.add_argument("--max-backup-age-seconds", type=int, default=DEFAULT_MAX_BACKUP_AGE_SECONDS)
    parser.add_argument("--command-timeout-seconds", type=int, default=DEFAULT_COMMAND_TIMEOUT_SECONDS)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not IMAGE_ID_RE.fullmatch(args.migration_runner_image_id):
        raise RehearsalRefusal("migration runner image ID is invalid")
    source = verify_source_checkout()
    manifest_path = args.manifest.expanduser()
    if not manifest_path.is_absolute() or manifest_path.is_symlink() or manifest_path.resolve(strict=True) != manifest_path:
        raise RehearsalRefusal("production manifest path is not canonical")
    manifest_values = production_backup_manifest_values(manifest_path)
    backup = verify_backup_receipt(
        receipt_path=args.backup_receipt,
        receipt_sha256=args.backup_receipt_sha256,
        expected_release_sha=args.expected_production_release_sha,
        manifest_values=manifest_values,
        max_age_seconds=args.max_backup_age_seconds,
    )
    runner_prebuild = verify_runner_prebuild_receipt(
        receipt_path=args.migration_runner_prebuild_receipt,
        receipt_sha256=args.migration_runner_prebuild_receipt_sha256,
        expected_image_id=args.migration_runner_image_id,
        source=source,
    )
    plan = {
        "schema_version": 1,
        "contract": "production-migration-rehearsal-v1",
        "status": "ready",
        "mode": "plan" if not args.execute else "execute",
        "source_commit": source.commit,
        "source_tree": source.tree,
        "source_alembic_head": source.alembic_head,
        "production_release_sha": backup.production_release_sha,
        "migration_runner_image_id": args.migration_runner_image_id,
        "migration_runner_prebuild_receipt_sha256": runner_prebuild.receipt_sha256,
        "migration_runner_release_tree": runner_prebuild.release_tree,
        "migration_runner_input_signature": runner_prebuild.input_signature,
        "pre_migration_head": EXPECTED_PRE_MIGRATION_HEAD,
        "backup_receipt_sha256": backup.receipt_sha256,
        "backup_artifact_set_sha256": backup.artifact_set_sha256,
        "roles": list(EXPECTED_ROLES),
        "target_bindings_sha256": {
            artifact.role: artifact.target_binding_sha256
            for artifact in backup.artifacts
        },
        "expected_public_table_delta": 14,
        "docker_network": "random-internal-no-published-ports",
        "production_mutation": False,
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) if args.json else "Production migration rehearsal plan is ready.")
        return 0
    if args.confirmation != EXECUTE_CONFIRMATION:
        raise RehearsalRefusal("exact rehearsal confirmation is required")
    if args.receipt is None:
        raise RehearsalRefusal("an approved execution receipt path is required")
    if not 60 <= args.command_timeout_seconds <= 2 * 60 * 60:
        raise RehearsalRefusal("command timeout is outside the allowed range")

    run_id = "tbmr-" + secrets.token_hex(16)
    resources = allocate_resources(run_id)
    old_handlers: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        old_handlers[signum] = signal.signal(signum, _signal_handler)
    try:
        lock_fd, lock_identity = _acquire_lock(run_id)
    except BaseException:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        raise
    committed_source: CommittedSource | None = None
    runner_binding: RunnerImageBinding | None = None
    results: list[dict[str, Any]] = []
    started = _utc_now()
    status = "failed"
    error_code: str | None = None
    cleanup_failures: list[str] = []
    try:
        committed_source = export_committed_migration_source(source, run_id=run_id)
        runner_binding = verify_runner_image(runner_prebuild, source=source)
        create_internal_network(resources)
        for artifact in backup.artifacts:
            results.append(
                rehearse_artifact(
                    artifact,
                    source=source,
                    source_root=committed_source.path,
                    runner_prebuild=runner_prebuild,
                    resources=resources,
                    timeout=args.command_timeout_seconds,
                )
            )
        if [item.get("role") for item in results] != list(EXPECTED_ROLES):
            raise RehearsalCommandError("both production roles did not complete")
        assert_source_binding(source)
        verify_runner_image(runner_prebuild, source=source)
        status = "passed"
    except RehearsalInterrupted:
        error_code = "interrupted"
    except (RehearsalRefusal, RehearsalCommandError):
        error_code = "rehearsal-refused-or-failed"
    except Exception:
        error_code = "unexpected-rehearsal-failure"
    finally:
        for signum in old_handlers:
            signal.signal(signum, signal.SIG_IGN)
        try:
            try:
                cleanup_failures = cleanup_owned_resources(resources)
            except Exception:
                cleanup_failures = ["owned-resource-cleanup-command-failed"]
            if not cleanup_committed_source(committed_source, run_id=run_id):
                cleanup_failures.append("committed-source-cleanup-failed")
        finally:
            try:
                _release_lock(lock_fd, lock_identity)
            finally:
                for signum, handler in old_handlers.items():
                    signal.signal(signum, handler)
    if cleanup_failures:
        status = "failed"
        error_code = "owned-resource-cleanup-incomplete"
    finished = _utc_now()
    receipt_payload = {
        **plan,
        "status": status,
        "mode": "execute",
        "run_id": run_id,
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "finished_at": finished.isoformat().replace("+00:00", "Z"),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "backup_created_at": backup.created_at,
        "committed_source_archive_sha256": (
            committed_source.archive_sha256 if committed_source is not None else None
        ),
        "migration_runner_oci_revision": (
            runner_binding.oci_revision if runner_binding is not None else None
        ),
        "migration_runner_oci_release_tree": (
            runner_binding.release_tree if runner_binding is not None else None
        ),
        "migration_runner_oci_input_signature": (
            runner_binding.input_signature if runner_binding is not None else None
        ),
        "results": results,
        "cleanup_status": "passed" if not cleanup_failures else "failed",
        "cleanup_failure_codes": cleanup_failures,
        "error_code": error_code,
    }
    write_receipt(args.receipt, receipt_payload)
    if args.json:
        print(json.dumps(receipt_payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Production migration rehearsal {status} for foreign and Iran.")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RehearsalRefusal as exc:
        print(f"production migration rehearsal refused: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except RehearsalCommandError as exc:
        print(f"production migration rehearsal failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
