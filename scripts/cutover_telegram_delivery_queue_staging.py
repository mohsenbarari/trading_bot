#!/usr/bin/env python3
"""Official, fail-closed staging cutover from Legacy Telegram execution to Queue-v1.

This command never targets production compose projects or production database
names. Apply requires an exact confirmation phrase and records timestamps for
every process stop/start. Provider credentials are never printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.telegram_delivery_cutover_contract import (
    PRODUCTION_COMPOSE_PROJECTS,
    STAGING_COMPOSE_PROJECTS,
    api_env_updates,
    api_process_contract,
    bot_env_updates,
    expected_channel_id_updates,
    bot_process_contract,
    executor_count,
    executor_overlap_forbidden,
    legacy_runtime_env_updates,
    missing_required_env,
    present_forbidden_tokens,
    upsert_env_lines,
)


APPLY_CONFIRMATION = "CUTOVER STAGING TELEGRAM DELIVERY TO QUEUE-V1"
ROLLBACK_CONFIRMATION = "ROLLBACK STAGING TELEGRAM DELIVERY TO LEGACY"
REHEARSE_CONFIRMATION = "REHEARSE STAGING TELEGRAM DELIVERY FORWARD ROLLBACK"
FOREIGN_BOT_CONTAINER = "trading_bot_staging-bot-1"
FOREIGN_APP_CONTAINER = "trading_bot_staging-foreign_app-1"
FOREIGN_SYNC_CONTAINER = "trading_bot_staging-foreign_sync_worker-1"
FOREIGN_DB_CONTAINER = "trading_bot_staging-db-1"
IRAN_APP_CONTAINER = "trading_bot_staging_iran-app-1"
IRAN_SYNC_CONTAINER = "trading_bot_staging_iran-sync_worker-1"
IRAN_DB_CONTAINER = "trading_bot_staging_iran-db-1"
IRAN_SSH_HOST = os.getenv("STAGING_IRAN_SSH_HOST", "root@65.109.220.59")
IRAN_SSH_PORT = os.getenv("STAGING_IRAN_SSH_PORT", "37067")
IRAN_WORKDIR = "/srv/trading-bot/staging-iran"
FOREIGN_ENV_FILE = REPO_ROOT / ".env.staging"
IRAN_ENV_FILE = f"{IRAN_WORKDIR}/.env.staging"
STAGING_DB_NAME = "trading_bot_staging"
RESTORE_DB_NAME = "telegram_queue_stage3_cutover_restore_test"
EXPECTED_SCHEMA_HEAD = "fb1c2d3e4f5a"
DEFAULT_ARTIFACT_DIR = Path("/tmp/telegram-queue-cutover-staging")
IRAN_CONTAINERS = frozenset(
    {IRAN_APP_CONTAINER, IRAN_DB_CONTAINER, IRAN_SYNC_CONTAINER}
)
PRODUCER_CONTAINERS = (
    IRAN_APP_CONTAINER,
    IRAN_SYNC_CONTAINER,
    FOREIGN_APP_CONTAINER,
    FOREIGN_SYNC_CONTAINER,
)
RSYNC_EXCLUDES = (
    "/.git/",
    "/.github/",
    "/.agents/",
    "/.claude/",
    "/.codex/",
    "/.cursor/",
    "/.env*",
    "/.venv/",
    "/.vscode/",
    "/.deploy_count",
    "/__pycache__/",
    "/app_logs/",
    "/docs/",
    "/frontend/",
    "/tests/",
    "/tmp/",
    "/uploads/",
    "/map_data/",
    "/mini_app_dist*/",
)


class StagingCutoverError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _docker_args(container: str, args: list[str]) -> list[str]:
    if container in IRAN_CONTAINERS:
        remote = "docker " + " ".join(shlex.quote(part) for part in args)
        return [
            "ssh",
            "-p",
            IRAN_SSH_PORT,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=12",
            IRAN_SSH_HOST,
            remote,
        ]
    return ["docker", *args]


def _require_staging_project(container_name: str) -> None:
    inspect = _run(
        _docker_args(
            container_name,
            [
                "inspect",
                "-f",
                "{{index .Config.Labels \"com.docker.compose.project\"}}",
                container_name,
            ],
        )
    )
    project = (inspect.stdout or "").strip()
    if inspect.returncode != 0 or project not in STAGING_COMPOSE_PROJECTS:
        raise StagingCutoverError("staging_container_project_invalid")
    if project in PRODUCTION_COMPOSE_PROJECTS:
        raise StagingCutoverError("production_compose_project_forbidden")


def _git_binding() -> dict[str, str]:
    def value(*git_args: str) -> str:
        result = _run(["git", *git_args])
        return (result.stdout or "").strip()

    return {
        "branch": value("rev-parse", "--abbrev-ref", "HEAD"),
        "head": value("rev-parse", "HEAD"),
        "tree": value("rev-parse", "HEAD^{tree}"),
        "parent": value("rev-parse", "HEAD^"),
        "origin_main": value("rev-parse", "origin/main"),
        "worktree": "clean"
        if _run(["git", "status", "--porcelain"]).stdout.strip() == ""
        else "dirty",
    }


def _container_env(container: str, keys: tuple[str, ...]) -> dict[str, str | None]:
    _require_staging_project(container)
    observed: dict[str, str | None] = {}
    for key in keys:
        result = _run(_docker_args(container, ["exec", container, "printenv", key]))
        if result.returncode != 0:
            observed[key] = None
        else:
            observed[key] = (result.stdout or "").strip() or None
    return observed


def _token_presence(container: str, keys: tuple[str, ...]) -> dict[str, bool]:
    observed = _container_env(container, keys)
    return {key: bool(str(value or "").strip()) for key, value in observed.items()}


def _redacted_runtime(container: str, role: str) -> dict[str, Any]:
    contract = api_process_contract() if role == "api" else bot_process_contract()
    required_keys = tuple(contract.required)
    observed = _container_env(
        container,
        required_keys
        + (
            "TRADING_BOT_SERVICE",
            "SERVER_MODE",
            "ENVIRONMENT",
            "RELEASE_SHA",
        ),
    )
    tokens = _token_presence(container, contract.forbidden_token_keys or ("BOT_TOKEN",))
    return {
        "container": container,
        "role": role,
        "service": observed.get("TRADING_BOT_SERVICE"),
        "server_mode": observed.get("SERVER_MODE"),
        "environment": observed.get("ENVIRONMENT"),
        "release_sha": observed.get("RELEASE_SHA"),
        "required_env": {
            key: observed.get(key) for key in required_keys
        },
        "missing_required": missing_required_env(observed, contract),
        "forbidden_tokens_present": present_forbidden_tokens(tokens, contract),
    }


def build_plan(artifact_dir: Path) -> dict[str, Any]:
    binding = _git_binding()
    plan = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "command": "plan",
        "environment": "staging",
        "production_authorized": False,
        "git": binding,
        "rollback_point": {
            "release_sha": binding["head"],
            "schema_head": EXPECTED_SCHEMA_HEAD,
            "forward_rollback_only": True,
        },
        "sequence": [
            "quiesce Iran API producer",
            "drain or freshness-reconcile foreign ready jobs",
            "snapshot queue/outbox/B2B aggregates",
            "backup and restore-probe",
            "stop foreign legacy bot",
            "prove zero Telegram executors",
            "start foreign bot as Queue-v1 with five publishers",
            "start Iran API as producer queue-v1 without tokens or worker",
            "recheck owner lock",
            "reopen ingress and follow first terminal receipts",
        ],
        "invariants": {
            "exactly_one_execution_owner": True,
            "executor_overlap_forbidden": True,
            "five_publishers_required": True,
            "no_bot_token_on_iran_api": True,
            "no_schema_downgrade": True,
        },
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"cutover-plan-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    plan["artifact"] = str(path)
    return plan


def build_status() -> dict[str, Any]:
    foreign_bot = _redacted_runtime(FOREIGN_BOT_CONTAINER, "bot")
    foreign_app = _redacted_runtime(FOREIGN_APP_CONTAINER, "api")
    iran_app = _redacted_runtime(IRAN_APP_CONTAINER, "api")
    overlap = executor_overlap_forbidden(
        legacy_workers_enabled=str(
            foreign_bot["required_env"].get("TELEGRAM_DELIVERY_EXECUTION_OWNER") or "legacy"
        ).lower()
        == "legacy",
        queue_worker_enabled=str(
            foreign_bot["required_env"].get("TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED") or ""
        ).lower()
        == "true",
    )
    return {
        "observed_at": _utc_now(),
        "git": _git_binding(),
        "foreign_bot": foreign_bot,
        "foreign_app": foreign_app,
        "iran_app": iran_app,
        "executor_overlap": overlap,
        "iran_token_violation": bool(iran_app["forbidden_tokens_present"]),
        "cutover_ready": (
            not overlap
            and not iran_app["forbidden_tokens_present"]
            and not foreign_bot["missing_required"]
            and not iran_app["missing_required"]
        ),
    }


def _pg_dump(container: str, destination: Path) -> str:
    _require_staging_project(container)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        result = subprocess.run(
            _docker_args(
                container,
                [
                    "exec",
                    container,
                    "sh",
                    "-c",
                    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                ],
            ),
            check=False,
            stdout=handle,
        )
    if result.returncode != 0:
        raise StagingCutoverError("staging_pg_dump_failed")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        digest + "\n", encoding="utf-8"
    )
    return digest


def backup_staging(artifact_dir: Path) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    foreign_dump = artifact_dir / f"foreign-db-{stamp}.sql"
    iran_dump = artifact_dir / f"iran-db-{stamp}.sql"
    payload = {
        "created_at": _utc_now(),
        "environment": "staging",
        "foreign_db": STAGING_DB_NAME,
        "iran_db": STAGING_DB_NAME,
        "foreign_dump_sha256": _pg_dump(FOREIGN_DB_CONTAINER, foreign_dump),
        "iran_dump_sha256": _pg_dump(IRAN_DB_CONTAINER, iran_dump),
        "restore_probe_database": RESTORE_DB_NAME,
    }
    manifest = artifact_dir / f"backup-manifest-{stamp}.json"
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload["manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    payload["manifest"] = str(manifest)
    return payload


def restore_probe(artifact_dir: Path, dump_path: Path) -> dict[str, Any]:
    expected = dump_path.with_suffix(dump_path.suffix + ".sha256").read_text(encoding="utf-8").strip()
    actual = hashlib.sha256(dump_path.read_bytes()).hexdigest()
    if actual != expected:
        raise StagingCutoverError("backup_checksum_mismatch")
    admin = _run(
        [
            "docker",
            "exec",
            FOREIGN_DB_CONTAINER,
            "sh",
            "-c",
            (
                'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 '
                f'-c "DROP DATABASE IF EXISTS {RESTORE_DB_NAME};" '
                f'-c "CREATE DATABASE {RESTORE_DB_NAME};"'
            ),
        ],
        timeout=120,
    )
    if admin.returncode != 0:
        raise StagingCutoverError("restore_probe_database_create_failed")
    with dump_path.open("rb") as handle:
        restore = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                FOREIGN_DB_CONTAINER,
                "sh",
                "-c",
                f'psql -U "$POSTGRES_USER" -d {RESTORE_DB_NAME} -v ON_ERROR_STOP=1 -q',
            ],
            check=False,
            stdin=handle,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    if restore.returncode != 0:
        raise StagingCutoverError("restore_probe_failed")
    head = _run(
        [
            "docker",
            "exec",
            FOREIGN_DB_CONTAINER,
            "sh",
            "-c",
            (
                f'psql -U "$POSTGRES_USER" -d {RESTORE_DB_NAME} -tAc '
                '"select version_num from alembic_version"'
            ),
        ]
    )
    return {
        "status": "restored",
        "database": RESTORE_DB_NAME,
        "dump_sha256": actual,
        "schema_head": (head.stdout or "").strip(),
        "production_touched": False,
    }


def _run_stdin(
    args: list[str], data: str, *, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        input=data,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _require_clean_pushed_main(binding: dict[str, str]) -> None:
    if binding.get("branch") != "main":
        raise StagingCutoverError("cutover_requires_main_branch")
    if binding.get("worktree") != "clean":
        raise StagingCutoverError("cutover_requires_clean_worktree")
    if binding.get("head") != binding.get("origin_main"):
        raise StagingCutoverError("cutover_requires_pushed_main")


def _container_running(container: str) -> bool:
    inspect = _run(
        _docker_args(
            container,
            ["inspect", "-f", "{{.State.Running}}", container],
        )
    )
    return inspect.returncode == 0 and (inspect.stdout or "").strip() == "true"


def _stop_container(container: str) -> dict[str, Any]:
    _require_staging_project(container)
    started = _utc_now()
    result = _run(_docker_args(container, ["stop", container]), timeout=120)
    if result.returncode != 0:
        raise StagingCutoverError(f"container_stop_failed:{container}")
    return {
        "container": container,
        "action": "stop",
        "at": started,
        "running": _container_running(container),
    }


def _start_container(container: str) -> dict[str, Any]:
    _require_staging_project(container)
    started = _utc_now()
    result = _run(_docker_args(container, ["start", container]), timeout=120)
    if result.returncode != 0:
        raise StagingCutoverError(f"container_start_failed:{container}")
    return {
        "container": container,
        "action": "start",
        "at": started,
        "running": _container_running(container),
    }


def collect_executor_inventory() -> dict[str, Any]:
    running = _container_running(FOREIGN_BOT_CONTAINER)
    owner = "legacy"
    queue_enabled = False
    if running:
        observed = _container_env(
            FOREIGN_BOT_CONTAINER,
            (
                "TELEGRAM_DELIVERY_EXECUTION_OWNER",
                "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED",
            ),
        )
        owner = str(
            observed.get("TELEGRAM_DELIVERY_EXECUTION_OWNER") or "legacy"
        ).strip().lower()
        queue_enabled = (
            str(observed.get("TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED") or "")
            .strip()
            .lower()
            == "true"
        )
    legacy_enabled = owner == "legacy" and not queue_enabled
    overlap = executor_overlap_forbidden(
        legacy_workers_enabled=legacy_enabled and running,
        queue_worker_enabled=queue_enabled and running,
    )
    count = executor_count(
        bot_running=running,
        legacy_workers_enabled=legacy_enabled,
        queue_worker_enabled=queue_enabled,
    )
    if overlap:
        count = 2
    return {
        "observed_at": _utc_now(),
        "bot_running": running,
        "execution_owner": owner if running else None,
        "legacy_workers_enabled": bool(running and legacy_enabled),
        "queue_worker_enabled": bool(running and queue_enabled),
        "executor_overlap": overlap,
        "executor_count": count,
    }


def _quiesce_producers() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for container in PRODUCER_CONTAINERS:
        if _container_running(container):
            events.append(_stop_container(container))
        else:
            events.append(
                {
                    "container": container,
                    "action": "already_stopped",
                    "at": _utc_now(),
                    "running": False,
                }
            )
    return events


def _resume_producers() -> list[dict[str, Any]]:
    return [_start_container(container) for container in PRODUCER_CONTAINERS]


def snapshot_queue_aggregates() -> dict[str, Any]:
    query = (
        "select json_build_object("
        " 'jobs_total', (select count(*) from telegram_delivery_jobs),"
        " 'jobs_pending', (select count(*) from telegram_delivery_jobs"
        "  where state in ('pending','pending_retry')),"
        " 'jobs_leased', (select count(*) from telegram_delivery_jobs"
        "  where state = 'leased'),"
        " 'jobs_ambiguous', (select count(*) from telegram_delivery_jobs"
        "  where state in ('ambiguous','ambiguous_unresolved','pending_reconcile')),"
        " 'pending_outcomes', (select count(*) from telegram_delivery_provider_outcomes"
        "  where apply_state = 'pending'),"
        " 'active_resume', (select count(*) from telegram_delivery_resume_operations"
        "  where state in ('requested','database_applied','redis_applied')),"
        " 'active_gates', (select count(*) from telegram_delivery_runtime_gates"
        "  where state in ('cooldown','blocked','resume_requested','database_applied')),"
        " 'dispatch_total', (select count(*) from telegram_publisher_dispatch_commands),"
        " 'dispatch_open', (select count(*) from telegram_publisher_dispatch_commands"
        "  where state in ('pending','sent','retry_due')),"
        " 'outbox_open', (select count(*) from telegram_notification_outbox"
        "  where status in ('pending','sending','retryable_failed'))"
        ")"
    )
    result = _run(
        [
            "docker",
            "exec",
            FOREIGN_DB_CONTAINER,
            "sh",
            "-c",
            f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc {shlex.quote(query)}',
        ]
    )
    if result.returncode != 0:
        raise StagingCutoverError("queue_aggregate_snapshot_failed")
    payload = json.loads((result.stdout or "").strip() or "{}")
    payload["observed_at"] = _utc_now()
    payload["database"] = STAGING_DB_NAME
    return payload


def _assert_quiesced_snapshot(snapshot: dict[str, Any]) -> None:
    open_keys = (
        "jobs_pending",
        "jobs_leased",
        "jobs_ambiguous",
        "pending_outcomes",
        "active_resume",
        "active_gates",
        "dispatch_open",
        "outbox_open",
    )
    if any(int(snapshot.get(key) or 0) for key in open_keys):
        raise StagingCutoverError("staging_delivery_not_quiesced")


def _parse_json_stdout(result: subprocess.CompletedProcess[str], error_code: str) -> Any:
    if result.returncode not in {0, 2}:
        raise StagingCutoverError(error_code)
    try:
        return json.loads(result.stdout or "")
    except json.JSONDecodeError as exc:
        raise StagingCutoverError(error_code) from exc


def reconcile_ready_jobs() -> dict[str, Any]:
    result = _run(
        [
            "docker",
            "exec",
            "-w",
            "/app",
            "-e",
            "PYTHONPATH=/app",
            FOREIGN_BOT_CONTAINER,
            "python",
            "scripts/reconcile_telegram_delivery_ready_jobs.py",
            "--environment",
            "staging",
            "--expected-database-name",
            STAGING_DB_NAME,
            "--requested-by",
            "staging-cutover",
            "--confirm",
            "RECONCILE READY TELEGRAM JOBS BY FRESHNESS",
        ],
        timeout=180,
    )
    if result.returncode == 127 or "No such file" in (result.stderr or ""):
        return {"status": "script_absent_on_running_image", "provider_network_calls": 0}
    payload = _parse_json_stdout(result, "ready_job_reconcile_failed")
    if int(payload.get("configuration_blocked_count") or 0):
        raise StagingCutoverError("ready_job_reconcile_blocked")
    return payload


def collect_health_summary() -> dict[str, Any]:
    result = _run(
        [
            "docker",
            "exec",
            "-w",
            "/app",
            "-e",
            "PYTHONPATH=/app",
            FOREIGN_BOT_CONTAINER,
            "sh",
            "-c",
            (
                'export TELEGRAM_QUEUE_OBSERVABILITY_DATABASE_URL="$DATABASE_URL"; '
                "python scripts/report_telegram_delivery_queue_health.py "
                "--environment staging "
                f"--expected-database-name {STAGING_DB_NAME} "
                "--include-shadow"
            ),
        ],
        timeout=180,
    )
    if result.returncode == 2:
        raise StagingCutoverError("health_decision_stop")
    payload = _parse_json_stdout(result, "health_report_failed")
    report = payload.get("report", payload) if isinstance(payload, dict) else {}
    health = report.get("health", report) if isinstance(report, dict) else {}
    if str(health.get("decision") or "").strip().lower() == "stop":
        raise StagingCutoverError("health_decision_stop")
    return {
        "decision": health.get("decision"),
        "ready_depth": health.get("ready_depth"),
        "state_counts": health.get("state_counts"),
        "alerts": health.get("alerts"),
    }


def _health_decision(report: dict[str, Any]) -> str:
    return str(report.get("decision") or "").strip().lower()


def _upsert_local_env(path: Path, updates: dict[str, str]) -> dict[str, Any]:
    if not path.is_file():
        raise StagingCutoverError("foreign_env_file_missing")
    original = path.read_text(encoding="utf-8")
    merged = dict(updates)
    try:
        merged.update(expected_channel_id_updates(original))
    except ValueError as exc:
        raise StagingCutoverError(str(exc)) from exc
    path.write_text(upsert_env_lines(original, merged), encoding="utf-8")
    os.chmod(path, 0o600)
    return {"keys": sorted(merged), "host": "foreign"}


def _upsert_iran_env(updates: dict[str, str]) -> dict[str, Any]:
    payload = json.dumps({"path": IRAN_ENV_FILE, "updates": updates})
    remote_script = f"""
import json, os
from pathlib import Path

def upsert_env_lines(text, updates):
    lines = str(text or "").splitlines()
    seen = set()
    rewritten = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rewritten.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            rewritten.append(f"{{key}}={{updates[key]}}")
            seen.add(key)
            continue
        rewritten.append(line)
    for key, value in updates.items():
        if key not in seen:
            rewritten.append(f"{{key}}={{value}}")
    return "\\n".join(rewritten) + "\\n"

payload = json.loads({payload!r})
path = Path(payload["path"])
if not path.is_file():
    raise SystemExit("iran_env_file_missing")
path.write_text(
    upsert_env_lines(path.read_text(encoding="utf-8"), payload["updates"]),
    encoding="utf-8",
)
os.chmod(path, 0o600)
print(json.dumps({{"keys": sorted(payload["updates"]), "host": "iran"}}))
"""
    result = _run_stdin(
        [
            "ssh",
            "-p",
            IRAN_SSH_PORT,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=12",
            IRAN_SSH_HOST,
            "python3 -",
        ],
        remote_script,
        timeout=60,
    )
    if result.returncode != 0:
        raise StagingCutoverError("iran_env_upsert_failed")
    return json.loads(result.stdout or "{}")


def _write_receipt(artifact_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _deploy_foreign(release_sha: str) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "STAGING_ENABLE_BOT": "1",
            "STAGING_FOREIGN_ONLY": "1",
            "STAGING_FOREIGN_PUBLIC_SURFACE_GUARD": "1",
            "STAGING_RELEASE_SHA": release_sha,
        }
    )
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts/deploy_staging.sh"), "deploy"],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=2400,
    )
    if result.returncode != 0:
        raise StagingCutoverError("foreign_staging_deploy_failed")
    return {"status": "deployed", "role": "foreign", "release_sha": release_sha}


def _rsync_iran_release() -> dict[str, Any]:
    ssh_shell = f"ssh -p {shlex.quote(IRAN_SSH_PORT)} -o BatchMode=yes"
    code = subprocess.run(
        [
            "rsync",
            "-az",
            "--delete",
            "-e",
            ssh_shell,
            *[item for exclude in RSYNC_EXCLUDES for item in ("--exclude", exclude)],
            f"{REPO_ROOT}/",
            f"{IRAN_SSH_HOST}:{IRAN_WORKDIR}/",
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    if code.returncode != 0:
        raise StagingCutoverError("iran_rsync_failed")
    frontend = subprocess.run(
        [
            "rsync",
            "-az",
            "--delete",
            "-e",
            ssh_shell,
            f"{REPO_ROOT}/mini_app_dist_staging/",
            f"{IRAN_SSH_HOST}:{IRAN_WORKDIR}/mini_app_dist_staging/",
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    if frontend.returncode != 0:
        raise StagingCutoverError("iran_frontend_rsync_failed")
    return {"status": "synced", "role": "iran"}


def _deploy_iran(release_sha: str) -> dict[str, Any]:
    remote = (
        f"cd {shlex.quote(IRAN_WORKDIR)} && "
        "STAGING_DOMAIN=staging.gold-trade.ir "
        "STAGING_FRONTEND_URL=https://staging.gold-trade.ir "
        "STAGING_PROJECT_NAME=trading_bot_staging_iran "
        "STAGING_NGINX_SITE=trading-bot-staging-iran "
        "STAGING_ENABLE_BOT=0 "
        "STAGING_SKIP_FRONTEND_BUILD=1 "
        f"STAGING_RELEASE_SHA={shlex.quote(release_sha)} "
        "STAGING_INTERNAL_FOREIGN_SERVER_URL=https://staging.362514.ir/foreign-sync "
        "STAGING_PUBLIC_FOREIGN_SYNC_URL=https://staging.362514.ir/foreign-sync "
        "STAGING_NGINX_DEDUPLICATE=1 "
        "scripts/deploy_staging.sh deploy"
    )
    result = _run(
        [
            "ssh",
            "-p",
            IRAN_SSH_PORT,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=12",
            IRAN_SSH_HOST,
            remote,
        ],
        timeout=2400,
    )
    if result.returncode != 0:
        raise StagingCutoverError("iran_staging_deploy_failed")
    return {"status": "deployed", "role": "iran", "release_sha": release_sha}


def _latest_backup_manifest(artifact_dir: Path) -> tuple[Path, dict[str, Any]]:
    manifests = sorted(artifact_dir.glob("backup-manifest-*.json"))
    if not manifests:
        raise StagingCutoverError("backup_manifest_missing")
    path = manifests[-1]
    return path, json.loads(path.read_text(encoding="utf-8"))


def _run_forward_rollback_checker(manifest_sha256: str) -> dict[str, Any]:
    result = _run(
        [
            "docker",
            "exec",
            "-w",
            "/app",
            "-e",
            "PYTHONPATH=/app",
            FOREIGN_BOT_CONTAINER,
            "sh",
            "-c",
            (
                'export TELEGRAM_QUEUE_ROLLBACK_DATABASE_URL="$DATABASE_URL"; '
                "python scripts/check_telegram_delivery_forward_rollback.py "
                "--environment staging "
                f"--expected-database-name {STAGING_DB_NAME} "
                f"--expected-schema-head {EXPECTED_SCHEMA_HEAD} "
                "--producer-quiesced --migration-stage-skipped "
                f"--backup-manifest-sha256 {manifest_sha256}"
            ),
        ],
        timeout=180,
    )
    if result.returncode not in {0, 2}:
        raise StagingCutoverError("forward_rollback_checker_failed")
    try:
        payload = json.loads(result.stdout or "")
    except json.JSONDecodeError as exc:
        raise StagingCutoverError("forward_rollback_checker_failed") from exc
    report = payload.get("report", payload) if isinstance(payload, dict) else {}
    readiness = report.get("readiness", report) if isinstance(report, dict) else {}
    if str(readiness.get("decision") or "").strip().lower() != "ready":
        raise StagingCutoverError("forward_rollback_checker_not_ready")
    return {
        "decision": readiness.get("decision"),
        "blockers": readiness.get("blockers"),
        "active_job_count": readiness.get("active_job_count"),
        "leased_job_count": readiness.get("leased_job_count"),
        "unresolved_job_count": readiness.get("unresolved_job_count"),
        "pending_provider_outcome_count": readiness.get("pending_provider_outcome_count"),
        "incomplete_resume_count": readiness.get("incomplete_resume_count"),
        "active_runtime_gate_count": readiness.get("active_runtime_gate_count"),
    }


def apply_cutover(
    artifact_dir: Path,
    *,
    confirm: str,
    skip_deploy: bool = False,
) -> dict[str, Any]:
    if confirm != APPLY_CONFIRMATION:
        raise StagingCutoverError("cutover_confirmation_mismatch")
    binding = _git_binding()
    _require_clean_pushed_main(binding)
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "command": "apply",
        "environment": "staging",
        "production_authorized": False,
        "started_at": _utc_now(),
        "git": binding,
        "steps": [],
        "executor_timeline": [],
    }
    receipt["executor_timeline"].append(collect_executor_inventory())
    receipt["steps"].append({"name": "quiesce_producers", "events": _quiesce_producers()})
    if _container_running(FOREIGN_BOT_CONTAINER):
        receipt["steps"].append({"name": "reconcile_ready_jobs", "report": reconcile_ready_jobs()})
        receipt["steps"].append({"name": "health_before_stop", "report": collect_health_summary()})
    snapshot = snapshot_queue_aggregates()
    receipt["steps"].append({"name": "queue_snapshot", "report": snapshot})
    _assert_quiesced_snapshot(snapshot)
    backup = backup_staging(artifact_dir)
    receipt["steps"].append(
        {
            "name": "backup",
            "foreign_dump_sha256": backup["foreign_dump_sha256"],
            "iran_dump_sha256": backup["iran_dump_sha256"],
            "manifest_sha256": backup["manifest_sha256"],
        }
    )
    dump = sorted(artifact_dir.glob("foreign-db-*.sql"))[-1]
    probe = restore_probe(artifact_dir, dump)
    if probe.get("schema_head") != EXPECTED_SCHEMA_HEAD:
        raise StagingCutoverError("restore_probe_schema_head_mismatch")
    receipt["steps"].append({"name": "restore_probe", "report": probe})
    if _container_running(FOREIGN_BOT_CONTAINER):
        checker = _run_forward_rollback_checker(backup["manifest_sha256"])
        receipt["steps"].append({"name": "forward_rollback_checker", "report": checker})
    if _container_running(FOREIGN_BOT_CONTAINER):
        receipt["steps"].append({"name": "stop_legacy_bot", "event": _stop_container(FOREIGN_BOT_CONTAINER)})
    inventory = collect_executor_inventory()
    receipt["executor_timeline"].append(inventory)
    if inventory["executor_count"] != 0 or inventory["bot_running"]:
        raise StagingCutoverError("legacy_executor_still_present")
    receipt["steps"].append({"name": "upsert_foreign_bot_env", "report": _upsert_local_env(FOREIGN_ENV_FILE, bot_env_updates())})
    receipt["steps"].append({"name": "upsert_iran_api_env", "report": _upsert_iran_env(api_env_updates())})
    if skip_deploy:
        if os.getenv("TELEGRAM_CUTOVER_ALLOW_SKIP_DEPLOY") != "1":
            raise StagingCutoverError("skip_deploy_not_authorized")
        receipt["status"] = "prepared_without_deploy"
        receipt["finished_at"] = _utc_now()
        receipt["artifact"] = str(_write_receipt(artifact_dir, "cutover-apply", receipt))
        return receipt
    release_sha = binding["head"]
    receipt["steps"].append({"name": "deploy_foreign", "report": _deploy_foreign(release_sha)})
    inventory = collect_executor_inventory()
    receipt["executor_timeline"].append(inventory)
    if inventory["executor_count"] != 1 or inventory["execution_owner"] != "queue-v1":
        raise StagingCutoverError("queue_executor_not_unique_after_foreign_deploy")
    if inventory["legacy_workers_enabled"] or inventory["executor_overlap"]:
        raise StagingCutoverError("legacy_executor_overlap_after_foreign_deploy")
    receipt["steps"].append({"name": "rsync_iran", "report": _rsync_iran_release()})
    receipt["steps"].append({"name": "deploy_iran", "report": _deploy_iran(release_sha)})
    status = build_status()
    if status.get("executor_overlap") or status.get("iran_token_violation") or not status.get("cutover_ready"):
        raise StagingCutoverError("post_deploy_contract_not_ready")
    receipt["steps"].append({"name": "post_deploy_status", "report": status})
    if _container_running(FOREIGN_BOT_CONTAINER):
        receipt["steps"].append({"name": "health_after_cutover", "report": collect_health_summary()})
    receipt["executor_timeline"].append(collect_executor_inventory())
    receipt["status"] = "applied"
    receipt["finished_at"] = _utc_now()
    receipt["artifact"] = str(_write_receipt(artifact_dir, "cutover-apply", receipt))
    return receipt


def rehearse_forward_rollback(artifact_dir: Path, *, confirm: str) -> dict[str, Any]:
    if confirm != REHEARSE_CONFIRMATION:
        raise StagingCutoverError("rehearse_confirmation_mismatch")
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "command": "rehearse-rollback",
        "environment": "staging",
        "production_authorized": False,
        "started_at": _utc_now(),
        "git": _git_binding(),
        "steps": [],
        "executor_timeline": [collect_executor_inventory()],
    }
    receipt["steps"].append({"name": "quiesce_producers", "events": _quiesce_producers()})
    snapshot = snapshot_queue_aggregates()
    receipt["steps"].append({"name": "queue_snapshot", "report": snapshot})
    _assert_quiesced_snapshot(snapshot)
    backup = backup_staging(artifact_dir)
    receipt["steps"].append(
        {
            "name": "backup",
            "manifest_sha256": backup["manifest_sha256"],
            "foreign_dump_sha256": backup["foreign_dump_sha256"],
        }
    )
    dump = sorted(artifact_dir.glob("foreign-db-*.sql"))[-1]
    receipt["steps"].append({"name": "restore_probe", "report": restore_probe(artifact_dir, dump)})
    if _container_running(FOREIGN_BOT_CONTAINER):
        receipt["steps"].append(
            {
                "name": "forward_rollback_checker",
                "report": _run_forward_rollback_checker(backup["manifest_sha256"]),
            }
        )
        receipt["steps"].append({"name": "stop_bot", "event": _stop_container(FOREIGN_BOT_CONTAINER)})
    inventory = collect_executor_inventory()
    receipt["executor_timeline"].append(inventory)
    if inventory["executor_count"] != 0:
        raise StagingCutoverError("rehearse_zero_executor_unproven")
    receipt["steps"].append({"name": "start_legacy_bot", "event": _start_container(FOREIGN_BOT_CONTAINER)})
    receipt["steps"].append({"name": "resume_producers", "events": _resume_producers()})
    inventory = collect_executor_inventory()
    receipt["executor_timeline"].append(inventory)
    if inventory["executor_count"] != 1 or inventory["execution_owner"] != "legacy":
        raise StagingCutoverError("rehearse_legacy_executor_unproven")
    receipt["status"] = "rehearsed"
    receipt["finished_at"] = _utc_now()
    receipt["artifact"] = str(_write_receipt(artifact_dir, "cutover-rehearse", receipt))
    return receipt


def rollback_to_legacy(artifact_dir: Path, *, confirm: str) -> dict[str, Any]:
    if confirm != ROLLBACK_CONFIRMATION:
        raise StagingCutoverError("rollback_confirmation_mismatch")
    binding = _git_binding()
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "command": "rollback",
        "environment": "staging",
        "production_authorized": False,
        "schema_downgrade": False,
        "started_at": _utc_now(),
        "git": binding,
        "steps": [],
        "executor_timeline": [collect_executor_inventory()],
    }
    receipt["steps"].append({"name": "quiesce_producers", "events": _quiesce_producers()})
    if _container_running(FOREIGN_BOT_CONTAINER):
        receipt["steps"].append({"name": "stop_queue_bot", "event": _stop_container(FOREIGN_BOT_CONTAINER)})
    inventory = collect_executor_inventory()
    receipt["executor_timeline"].append(inventory)
    if inventory["executor_count"] != 0:
        raise StagingCutoverError("rollback_zero_executor_unproven")
    receipt["steps"].append(
        {
            "name": "upsert_legacy_env",
            "foreign": _upsert_local_env(FOREIGN_ENV_FILE, legacy_runtime_env_updates()),
            "iran": _upsert_iran_env(legacy_runtime_env_updates()),
        }
    )
    release_sha = binding["head"]
    receipt["steps"].append({"name": "deploy_foreign_legacy", "report": _deploy_foreign(release_sha)})
    receipt["steps"].append({"name": "rsync_iran", "report": _rsync_iran_release()})
    receipt["steps"].append({"name": "deploy_iran_legacy", "report": _deploy_iran(release_sha)})
    inventory = collect_executor_inventory()
    receipt["executor_timeline"].append(inventory)
    if inventory["executor_count"] != 1 or inventory["execution_owner"] != "legacy":
        raise StagingCutoverError("rollback_legacy_executor_unproven")
    receipt["status"] = "rolled_back"
    receipt["finished_at"] = _utc_now()
    receipt["artifact"] = str(_write_receipt(artifact_dir, "cutover-rollback", receipt))
    return receipt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "plan",
            "status",
            "backup",
            "restore-probe",
            "apply",
            "rehearse-rollback",
            "rollback",
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--dump")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--skip-deploy", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "plan":
            payload = build_plan(args.artifact_dir)
        elif args.command == "status":
            payload = build_status()
        elif args.command == "backup":
            payload = backup_staging(args.artifact_dir)
        elif args.command == "restore-probe":
            if not args.dump:
                raise StagingCutoverError("restore_probe_dump_required")
            payload = restore_probe(args.artifact_dir, Path(args.dump))
        elif args.command == "apply":
            payload = apply_cutover(
                args.artifact_dir,
                confirm=args.confirm,
                skip_deploy=bool(args.skip_deploy),
            )
        elif args.command == "rehearse-rollback":
            payload = rehearse_forward_rollback(args.artifact_dir, confirm=args.confirm)
        else:
            payload = rollback_to_legacy(args.artifact_dir, confirm=args.confirm)
    except StagingCutoverError as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_code": str(exc), "production_authorized": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
