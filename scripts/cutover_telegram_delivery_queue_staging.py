#!/usr/bin/env python3
"""Official, fail-closed staging cutover from Legacy Telegram execution to Queue-v1.

This command never targets production compose projects or production database
names. Apply requires an exact confirmation phrase and records timestamps for
every process stop/start. Provider credentials are never printed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

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
    executor_overlap_forbidden,
    legacy_runtime_env_updates,
    missing_required_env,
    present_forbidden_tokens,
    upsert_env_lines,
)
from scripts.deploy_config import parse_env_file


APPLY_CONFIRMATION = "CUTOVER STAGING TELEGRAM DELIVERY TO QUEUE-V1"
REDEPLOY_CONFIRMATION = "REDEPLOY STAGING TELEGRAM DELIVERY QUEUE-V1"
REDEPLOY_SUCCESSOR_CONFIRMATION = (
    "RECOVER STAGING REDEPLOY WITH ONE ORCHESTRATION SUCCESSOR"
)
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
EXPECTED_SCHEMA_HEAD = "a385f6b7c8d0"
DEFAULT_ARTIFACT_DIR = Path("/tmp/telegram-queue-cutover-staging")
FOREIGN_STAGING_PROJECT = "trading_bot_staging"
IRAN_STAGING_PROJECT = "trading_bot_staging_iran"
PUBLISHER_IDENTITIES = tuple(f"publisher_{index}" for index in range(1, 6))
EXPECTED_QUEUE_IDENTITIES = ("primary", *PUBLISHER_IDENTITIES)
API_SURFACES = (
    ("foreign", FOREIGN_APP_CONTAINER, "api"),
    ("foreign", FOREIGN_SYNC_CONTAINER, "sync_worker"),
    ("iran", IRAN_APP_CONTAINER, "api"),
    ("iran", IRAN_SYNC_CONTAINER, "sync_worker"),
)
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
    # The wheel cache is a deliberately managed host asset.  A tracked Git
    # export contains only its .gitkeep; --delete must not erase the cache that
    # apply/rollback may still need for an offline Iran-side build.
    "/pip_packages/",
)
RUNTIME_SOURCE_DIRECTORIES = (
    "api",
    "bot",
    "core",
    "src",
    "migrations",
    "models",
    "templates",
    "fonts",
    "scripts",
)
RUNTIME_SOURCE_FILES = (
    "alembic.ini",
    "main.py",
    "manage.py",
    "run_bot.py",
    "schemas.py",
    "seed_fake_data.py",
    "trading_settings.json",
)
REDEPLOY_JOURNAL_NAME = "cutover-redeploy-active.json"
REDEPLOY_LOCK_NAME = "cutover-redeploy.lock"
REDEPLOY_STATE_DIR = Path("/var/lib/trading-bot/staging-queue-cutover")
STAGING_IMAGE_REPOSITORY = "trading_bot_staging_app"
IRAN_IMAGE_TRANSFER_ROOT = "/root/.trading-bot-staging-image-transfer"
REDEPLOY_RUNTIME_CONTAINERS = (
    FOREIGN_BOT_CONTAINER,
    FOREIGN_APP_CONTAINER,
    IRAN_APP_CONTAINER,
    FOREIGN_SYNC_CONTAINER,
    IRAN_SYNC_CONTAINER,
)
SAFE_REDEPLOY_ORCHESTRATION_SUCCESSOR_PATHS = frozenset(
    {
        "scripts/cutover_telegram_delivery_queue_staging.py",
        "scripts/deploy_staging.sh",
        "tests/test_deploy_surface_smoke.py",
        "tests/test_telegram_delivery_cutover_contract.py",
    }
)


class StagingCutoverError(RuntimeError):
    pass


PROCESS_GROUP_TERM_GRACE_SECONDS = 1.0
PROCESS_GROUP_KILL_GRACE_SECONDS = 1.0
PROCESS_GROUP_POLL_SECONDS = 0.02


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(int(process_group_id), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_group_exit(
    process_group_id: int,
    *,
    deadline: float,
) -> bool:
    while _process_group_exists(process_group_id):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(PROCESS_GROUP_POLL_SECONDS, remaining))
    return True


def _close_process_streams(process: subprocess.Popen[Any]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            stream.close()


def _timeout_output(value: Any, *, text: bool) -> Any:
    if value is None:
        return "" if text else b""
    if text and isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if not text and isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    return value


def _cleanup_process_group(
    process: subprocess.Popen[Any],
    *,
    process_group_id: int,
    leader_communicated: bool,
    text: bool,
) -> tuple[Any, Any]:
    stdout: Any = "" if text else b""
    stderr: Any = "" if text else b""
    cleanup_communication_failed = False
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        raise StagingCutoverError("child_process_group_signal_forbidden") from None

    term_deadline = time.monotonic() + PROCESS_GROUP_TERM_GRACE_SECONDS
    if not leader_communicated:
        try:
            stdout, stderr = process.communicate(
                timeout=max(term_deadline - time.monotonic(), 0.001)
            )
            leader_communicated = True
        except subprocess.TimeoutExpired as exc:
            stdout = _timeout_output(exc.output, text=text)
            stderr = _timeout_output(exc.stderr, text=text)
        except BaseException:
            cleanup_communication_failed = True
    group_absent = _wait_for_process_group_exit(
        process_group_id,
        deadline=term_deadline,
    )

    if not group_absent:
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            raise StagingCutoverError(
                "child_process_group_signal_forbidden"
            ) from None
        kill_deadline = time.monotonic() + PROCESS_GROUP_KILL_GRACE_SECONDS
        if not leader_communicated:
            try:
                stdout, stderr = process.communicate(
                    timeout=max(kill_deadline - time.monotonic(), 0.001)
                )
                leader_communicated = True
            except subprocess.TimeoutExpired as exc:
                stdout = _timeout_output(exc.output, text=text)
                stderr = _timeout_output(exc.stderr, text=text)
            except BaseException:
                cleanup_communication_failed = True
        group_absent = _wait_for_process_group_exit(
            process_group_id,
            deadline=kill_deadline,
        )

    if process.poll() is None:
        wait_deadline = time.monotonic() + PROCESS_GROUP_KILL_GRACE_SECONDS
        try:
            process.wait(timeout=max(wait_deadline - time.monotonic(), 0.001))
        except subprocess.TimeoutExpired:
            group_absent = False
        except BaseException:
            cleanup_communication_failed = True
    if not group_absent or _process_group_exists(process_group_id) or process.poll() is None:
        _close_process_streams(process)
        raise StagingCutoverError("child_process_group_not_stopped")
    _close_process_streams(process)
    if cleanup_communication_failed:
        raise StagingCutoverError("child_process_cleanup_communication_failed")
    return stdout, stderr


def _append_process_error(value: Any, code: str, *, text: bool) -> Any:
    separator = "\n" if text else b"\n"
    suffix = code if text else code.encode("utf-8")
    current = _timeout_output(value, text=text)
    return current + (separator if current else ("" if text else b"")) + suffix


def _run_contained(
    args: list[str],
    *,
    timeout: int | float,
    input_data: Any = None,
    stdin: Any = None,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
    text: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[Any]:
    try:
        bounded_timeout = float(timeout)
    except (TypeError, ValueError, OverflowError):
        raise StagingCutoverError("child_process_timeout_invalid") from None
    if not (bounded_timeout > 0 and bounded_timeout < float("inf")):
        raise StagingCutoverError("child_process_timeout_invalid")
    if input_data is not None and stdin is not None:
        raise StagingCutoverError("child_process_stdin_contract_invalid")
    process = subprocess.Popen(
        args,
        cwd=str(REPO_ROOT),
        env=None if env is None else dict(env),
        text=text,
        stdin=subprocess.PIPE if input_data is not None else stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    process_group_id = process.pid
    try:
        captured_stdout, captured_stderr = process.communicate(
            input=input_data,
            timeout=bounded_timeout,
        )
    except subprocess.TimeoutExpired:
        captured_stdout, captured_stderr = _cleanup_process_group(
            process,
            process_group_id=process_group_id,
            leader_communicated=False,
            text=text,
        )
        return subprocess.CompletedProcess(
            args,
            124,
            captured_stdout,
            _append_process_error(
                captured_stderr,
                "child_process_timeout",
                text=text,
            ),
        )
    except BaseException:
        _cleanup_process_group(
            process,
            process_group_id=process_group_id,
            leader_communicated=False,
            text=text,
        )
        raise
    if _process_group_exists(process_group_id):
        _cleanup_process_group(
            process,
            process_group_id=process_group_id,
            leader_communicated=True,
            text=text,
        )
        raise StagingCutoverError("child_process_group_survived_leader_exit")
    _close_process_streams(process)
    return subprocess.CompletedProcess(
        args,
        int(process.returncode or 0),
        captured_stdout,
        captured_stderr,
    )


def _run(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return _run_contained(args, timeout=timeout, text=True)


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


def _role_run(
    role: str,
    args: list[str],
    *,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    """Run a read-only host command on the exact staging host role."""

    if role == "foreign":
        return _run(args, timeout=timeout)
    if role != "iran":
        raise StagingCutoverError("staging_host_role_invalid")
    remote = " ".join(shlex.quote(part) for part in args)
    return _run(
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
        timeout=timeout,
    )


def _is_bot_process_command(value: Any) -> bool:
    command = str(value or "").lower()
    return "run_bot.py" in command and "python" in command


def _env_list_to_mapping(value: Any) -> dict[str, str]:
    rows = value if isinstance(value, list) else []
    return {
        row.split("=", 1)[0]: row.split("=", 1)[1]
        for row in rows
        if isinstance(row, str) and "=" in row
    }


def _command_parts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(part) for part in value)
    return ()


def _container_scope(
    role: str,
    *,
    name: str,
    project: str,
    environment: str,
) -> str:
    expected_project = (
        FOREIGN_STAGING_PROJECT if role == "foreign" else IRAN_STAGING_PROJECT
    )
    expected_name = FOREIGN_BOT_CONTAINER if role == "foreign" else ""
    if project == expected_project:
        return "staging"
    if project in PRODUCTION_COMPOSE_PROJECTS and environment != "staging":
        if role == "foreign" and name == "trading_bot_bot":
            return "other-known-environment"
        return "ambiguous-unknown"
    if (
        project in STAGING_COMPOSE_PROJECTS
        or environment == "staging"
        or bool(expected_name and name == expected_name)
    ):
        return "ambiguous-staging"
    return "ambiguous-unknown"


def _runtime_decision(role: str, container_id: str) -> dict[str, Any]:
    result = _role_run(
        role,
        [
            "docker",
            "exec",
            "-w",
            "/app",
            container_id,
            "python",
            "-c",
            (
                "import json; "
                "from core.telegram_delivery_runtime_policy import "
                "configured_telegram_delivery_runtime; "
                "r=configured_telegram_delivery_runtime(); "
                "print(json.dumps({'mode':r.mode.value,"
                "'legacy_workers_enabled':r.legacy_workers_enabled,"
                "'queue_worker_enabled':r.queue_worker_enabled},sort_keys=True))"
            ),
        ],
    )
    if result.returncode != 0:
        raise StagingCutoverError("executor_runtime_ownership_readback_failed")
    try:
        payload = json.loads((result.stdout or "").strip())
    except (TypeError, ValueError):
        raise StagingCutoverError(
            "executor_runtime_ownership_readback_failed"
        ) from None
    if not isinstance(payload, dict):
        raise StagingCutoverError("executor_runtime_ownership_readback_failed")
    return payload


def _enumerate_bot_containers(role: str) -> list[dict[str, Any]]:
    listed = _role_run(role, ["docker", "ps", "-q"])
    if listed.returncode != 0:
        raise StagingCutoverError("executor_inventory_readback_failed")
    records: list[dict[str, Any]] = []
    for container_id in (
        line.strip()
        for line in (listed.stdout or "").splitlines()
        if line.strip()
    ):
        inspected = _role_run(
            role,
            [
                "docker",
                "inspect",
                "-f",
                (
                    "{{json .Name}}\t{{json .Config.Labels}}\t"
                    "{{json .Config.Env}}\t{{json .Config.Cmd}}\t"
                    "{{json .Config.Entrypoint}}"
                ),
                container_id,
            ],
        )
        if inspected.returncode != 0:
            raise StagingCutoverError("executor_inventory_readback_failed")
        parts = (inspected.stdout or "").strip().split("\t")
        if len(parts) != 5:
            raise StagingCutoverError("executor_inventory_readback_failed")
        try:
            name = str(json.loads(parts[0]) or "").lstrip("/")
            labels = json.loads(parts[1]) or {}
            env = _env_list_to_mapping(json.loads(parts[2]))
            command = _command_parts(json.loads(parts[3]))
            entrypoint = _command_parts(json.loads(parts[4]))
        except (TypeError, ValueError):
            raise StagingCutoverError("executor_inventory_readback_failed") from None
        if not isinstance(labels, dict):
            raise StagingCutoverError("executor_inventory_readback_failed")
        project = str(labels.get("com.docker.compose.project") or "").strip()
        service = str(labels.get("com.docker.compose.service") or "").strip()
        command_text = " ".join((*command, *entrypoint))
        bot_like = (
            str(env.get("TRADING_BOT_SERVICE") or "").strip().lower() == "bot"
            or service == "bot"
            or _is_bot_process_command(command_text)
        )
        if not bot_like:
            continue
        top = _role_run(
            role,
            ["docker", "top", container_id, "-eo", "pid,args"],
        )
        if top.returncode != 0:
            raise StagingCutoverError("executor_inventory_readback_failed")
        process_ids: set[int] = set()
        for row in (top.stdout or "").splitlines():
            fields = row.strip().split(None, 1)
            if len(fields) != 2 or not fields[0].isdigit():
                continue
            if _is_bot_process_command(fields[1]):
                process_ids.add(int(fields[0]))
        scope = _container_scope(
            role,
            name=name,
            project=project,
            environment=str(env.get("ENVIRONMENT") or "").strip().lower(),
        )
        record: dict[str, Any] = {
            "container_fingerprint": hashlib.sha256(
                f"staging-container:{container_id}".encode("utf-8")
            ).hexdigest()[:16],
            "name": name,
            "project": project,
            "service": service,
            "environment": str(env.get("ENVIRONMENT") or "").strip().lower(),
            "scope": scope,
            "bot_process_count": len(process_ids),
            "_process_ids": process_ids,
        }
        if scope == "staging":
            record["runtime_env"] = {
                key: env.get(key)
                for key in (
                    "TRADING_BOT_SERVICE",
                    "SERVER_MODE",
                    "TELEGRAM_DELIVERY_EXECUTION_OWNER",
                    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED",
                )
            }
            record["runtime_decision"] = _runtime_decision(role, container_id)
        records.append(record)
    return records


def _host_bot_process_ids(role: str) -> set[int]:
    result = _role_run(role, ["ps", "-eo", "pid=,ppid=,args="])
    if result.returncode != 0:
        raise StagingCutoverError("executor_inventory_readback_failed")
    process_ids: set[int] = set()
    for row in (result.stdout or "").splitlines():
        fields = row.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit():
            continue
        if _is_bot_process_command(fields[2]):
            process_ids.add(int(fields[0]))
    return process_ids


def executor_inventory_from_observation(
    *,
    foreign_containers: Sequence[Mapping[str, Any]],
    iran_containers: Sequence[Mapping[str, Any]],
    foreign_host_process_ids: Sequence[int],
    iran_host_process_ids: Sequence[int],
    expected_owner: str | None = "any",
) -> dict[str, Any]:
    """Validate a complete two-host executor observation without leaking PIDs."""

    if expected_owner not in {"any", None, "legacy", "queue-v1"}:
        raise StagingCutoverError("executor_expected_owner_invalid")
    foreign_records = [dict(record) for record in foreign_containers]
    iran_records = [dict(record) for record in iran_containers]
    foreign_host = {int(pid) for pid in foreign_host_process_ids}
    iran_host = {int(pid) for pid in iran_host_process_ids}
    foreign_contained = {
        int(pid)
        for record in foreign_records
        for pid in record.get("_process_ids", ())
    }
    iran_contained = {
        int(pid)
        for record in iran_records
        for pid in record.get("_process_ids", ())
    }
    if foreign_host != foreign_contained or iran_host != iran_contained:
        raise StagingCutoverError("executor_uncontained_host_process")
    if iran_records:
        raise StagingCutoverError("extra_iran_executor_present")
    ambiguous = [
        record
        for record in foreign_records
        if str(record.get("scope") or "").startswith("ambiguous")
    ]
    if ambiguous:
        raise StagingCutoverError("executor_container_scope_ambiguous")
    staging = [
        record for record in foreign_records if record.get("scope") == "staging"
    ]
    if len(staging) > 1:
        raise StagingCutoverError("duplicate_staging_executor_container")

    owner: str | None = None
    legacy_enabled = False
    queue_enabled = False
    if staging:
        record = staging[0]
        env = dict(record.get("runtime_env") or {})
        decision = dict(record.get("runtime_decision") or {})
        if (
            record.get("name") != FOREIGN_BOT_CONTAINER
            or record.get("project") != FOREIGN_STAGING_PROJECT
            or record.get("service") != "bot"
            or record.get("environment") != "staging"
            or int(record.get("bot_process_count") or 0) != 1
            or str(env.get("TRADING_BOT_SERVICE") or "") != "bot"
            or str(env.get("SERVER_MODE") or "") != "foreign"
        ):
            raise StagingCutoverError("executor_container_identity_mismatch")
        owner = str(env.get("TELEGRAM_DELIVERY_EXECUTION_OWNER") or "").lower()
        queue_flag = (
            str(env.get("TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED") or "").lower()
            == "true"
        )
        legacy_enabled = decision.get("legacy_workers_enabled") is True
        queue_enabled = decision.get("queue_worker_enabled") is True
        mode = str(decision.get("mode") or "").lower()
        if (
            owner not in {"legacy", "queue-v1"}
            or mode != owner
            or queue_flag != queue_enabled
            or (owner == "legacy" and (not legacy_enabled or queue_enabled))
            or (owner == "queue-v1" and (legacy_enabled or not queue_enabled))
        ):
            raise StagingCutoverError("executor_runtime_ownership_mismatch")
    if expected_owner is None and staging:
        raise StagingCutoverError("executor_expected_zero")
    if expected_owner in {"legacy", "queue-v1"} and owner != expected_owner:
        raise StagingCutoverError("executor_expected_owner_mismatch")
    overlap = executor_overlap_forbidden(
        legacy_workers_enabled=legacy_enabled,
        queue_worker_enabled=queue_enabled,
    )
    if overlap:
        raise StagingCutoverError("executor_overlap_detected")
    other_environment_count = sum(
        int(record.get("bot_process_count") or 0)
        for record in foreign_records
        if record.get("scope") == "other-known-environment"
    )
    return {
        "observed_at": _utc_now(),
        "environment": "staging",
        "inventory_scope": "all-running-containers-and-host-processes-on-both-hosts",
        "executor_count": 1 if staging else 0,
        "execution_owner": owner,
        "bot_running": bool(staging),
        "legacy_workers_enabled": legacy_enabled,
        "queue_worker_enabled": queue_enabled,
        "executor_overlap": False,
        "host_processes": {
            "foreign": {
                "related_count": len(foreign_host),
                "containerized_count": len(foreign_contained),
                "uncontained_count": 0,
            },
            "iran": {
                "related_count": len(iran_host),
                "containerized_count": len(iran_contained),
                "uncontained_count": 0,
            },
        },
        "containers": {
            "foreign_staging_executor_count": len(staging),
            "iran_executor_count": 0,
            "ambiguous_executor_count": 0,
            "other_known_environment_process_count": other_environment_count,
        },
        "expected_container": {
            "name": FOREIGN_BOT_CONTAINER if staging else None,
            "project": FOREIGN_STAGING_PROJECT if staging else None,
            "process_count": 1 if staging else 0,
        },
        "process_identifiers_disclosed": False,
        "secret_values_disclosed": False,
    }


def collect_executor_inventory(
    *, expected_owner: str | None = "any"
) -> dict[str, Any]:
    foreign_containers = _enumerate_bot_containers("foreign")
    iran_containers = _enumerate_bot_containers("iran")
    return executor_inventory_from_observation(
        foreign_containers=foreign_containers,
        iran_containers=iran_containers,
        foreign_host_process_ids=_host_bot_process_ids("foreign"),
        iran_host_process_ids=_host_bot_process_ids("iran"),
        expected_owner=expected_owner,
    )


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


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_bot_id(value: Any) -> int:
    if isinstance(value, bool):
        raise StagingCutoverError("publisher_identity_configuration_invalid")
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError, OverflowError):
        raise StagingCutoverError(
            "publisher_identity_configuration_invalid"
        ) from None
    if parsed <= 0:
        raise StagingCutoverError("publisher_identity_configuration_invalid")
    return parsed


def _channel_id(value: Any) -> int:
    if isinstance(value, bool):
        raise StagingCutoverError("publisher_channel_identity_invalid")
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError, OverflowError):
        raise StagingCutoverError("publisher_channel_identity_invalid") from None
    if parsed == 0:
        raise StagingCutoverError("publisher_channel_identity_invalid")
    return parsed


def _normalize_username(value: Any) -> str:
    username = str(value or "").strip().lstrip("@").lower()
    if (
        not 5 <= len(username) <= 64
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
            for character in username
        )
    ):
        raise StagingCutoverError("publisher_username_configuration_invalid")
    return username


def _credential_fingerprint(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise StagingCutoverError("publisher_credential_missing")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _credential_binding_sha256(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise StagingCutoverError("publisher_credential_missing")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _provider_fingerprint(kind: str, value: int) -> str:
    return hashlib.sha256(
        f"telegram-preflight-v1:{kind}:{value}".encode("utf-8")
    ).hexdigest()[:16]


def _username_fingerprint(value: str) -> str:
    return hashlib.sha256(
        f"telegram-preflight-v1:username:{value}".encode("utf-8")
    ).hexdigest()[:16]


def _validated_provider_preflight_result(
    gateway_result: Any,
    *,
    method: str,
    bot_identity: str,
    expected_bot_id: Any,
    expected_username: Any,
    expected_channel_id: Any,
    request_payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Return a verified Telegram result body without disclosing provider data."""

    from core.telegram_gateway import TelegramGatewayResult

    if bot_identity not in EXPECTED_QUEUE_IDENTITIES:
        raise StagingCutoverError("publisher_provider_preflight_result_invalid")
    bot_id = _positive_bot_id(expected_bot_id)
    username = _normalize_username(expected_username)
    channel_id = _channel_id(expected_channel_id)
    if (
        not isinstance(gateway_result, TelegramGatewayResult)
        or gateway_result.method != method
        or gateway_result.ok is not True
        or gateway_result.status_code != 200
        or gateway_result.error is not None
        or not isinstance(gateway_result.response_json, Mapping)
        or gateway_result.response_json.get("ok") is not True
        or not isinstance(gateway_result.response_json.get("result"), Mapping)
        or not isinstance(request_payload, Mapping)
    ):
        raise StagingCutoverError("publisher_provider_preflight_result_invalid")

    provider_result = gateway_result.response_json["result"]
    if method == "getMe":
        request_valid = not request_payload
        result_valid = (
            provider_result.get("id") == bot_id
            and not isinstance(provider_result.get("id"), bool)
            and provider_result.get("is_bot") is True
        )
        try:
            result_valid = result_valid and (
                _normalize_username(provider_result.get("username")) == username
            )
        except StagingCutoverError:
            result_valid = False
    elif method == "getChat":
        try:
            request_valid = (
                set(request_payload) == {"chat_id"}
                and _channel_id(request_payload.get("chat_id")) == channel_id
            )
        except StagingCutoverError:
            request_valid = False
        result_valid = (
            provider_result.get("id") == channel_id
            and not isinstance(provider_result.get("id"), bool)
            and provider_result.get("type") == "channel"
        )
    elif method == "getChatMember":
        try:
            request_valid = (
                set(request_payload) == {"chat_id", "user_id"}
                and _channel_id(request_payload.get("chat_id")) == channel_id
                and _positive_bot_id(request_payload.get("user_id")) == bot_id
            )
        except StagingCutoverError:
            request_valid = False
        member_user = provider_result.get("user")
        result_valid = (
            provider_result.get("status") == "administrator"
            and isinstance(member_user, Mapping)
            and member_user.get("id") == bot_id
            and not isinstance(member_user.get("id"), bool)
            and member_user.get("is_bot") is True
        )
        if isinstance(member_user, Mapping):
            try:
                result_valid = result_valid and (
                    _normalize_username(member_user.get("username")) == username
                )
            except StagingCutoverError:
                result_valid = False
        required_permissions = (
            {
                "can_manage_chat",
                "can_post_messages",
                "can_edit_messages",
                "can_restrict_members",
            }
            if bot_identity == "primary"
            else {
                "can_manage_chat",
                "can_post_messages",
                "can_edit_messages",
                "can_delete_messages",
            }
        )
        result_valid = result_valid and all(
            provider_result.get(permission) is True
            for permission in required_permissions
        )
    else:
        raise StagingCutoverError("publisher_provider_preflight_result_invalid")

    if not request_valid or not result_valid:
        raise StagingCutoverError("publisher_provider_preflight_result_invalid")
    return provider_result


def api_runtime_evidence_from_reports(
    reports: Sequence[Mapping[str, Any]],
    *,
    expected_release_sha: str,
    include_iran: bool,
) -> dict[str, Any]:
    if (
        len(expected_release_sha) != 40
        or any(character not in "0123456789abcdef" for character in expected_release_sha)
    ):
        raise StagingCutoverError("api_release_binding_invalid")
    expected_specs = [
        spec for spec in API_SURFACES if include_iran or spec[0] == "foreign"
    ]
    if len(reports) != len(expected_specs):
        raise StagingCutoverError("api_surface_inventory_incomplete")
    evidence: list[dict[str, Any]] = []
    for report, (host_role, container, service) in zip(
        reports, expected_specs, strict=True
    ):
        forbidden = tuple(report.get("forbidden_tokens_present") or ())
        missing = tuple(report.get("missing_required") or ())
        if (
            report.get("container") != container
            or report.get("role") != "api"
            or report.get("service") != service
            or report.get("server_mode") != host_role
            or report.get("environment") != "staging"
            or str(report.get("release_sha") or "") != expected_release_sha
            or missing
            or forbidden
        ):
            raise StagingCutoverError("api_runtime_contract_not_ready")
        evidence.append(
            {
                "host_role": host_role,
                "container": container,
                "service": service,
                "server_mode": host_role,
                "environment": "staging",
                "release_sha": expected_release_sha,
                "required_env_exact": True,
                "forbidden_token_count": 0,
                "token_free": True,
            }
        )
    return {
        "status": "verified",
        "surface_count": len(evidence),
        "surfaces": evidence,
        "all_token_free": True,
        "secret_values_disclosed": False,
    }


def collect_api_runtime_evidence(
    *,
    expected_release_sha: str,
    include_iran: bool,
) -> dict[str, Any]:
    specs = [spec for spec in API_SURFACES if include_iran or spec[0] == "foreign"]
    reports: list[dict[str, Any]] = []
    for host_role, container, _service in specs:
        report = _redacted_runtime(container, "api")
        report["host_role"] = host_role
        reports.append(report)
    return api_runtime_evidence_from_reports(
        reports,
        expected_release_sha=expected_release_sha,
        include_iran=include_iran,
    )


def _publisher_identity_values(
    values: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], int]:
    channel = _channel_id(values.get("CHANNEL_ID"))
    if channel != _channel_id(
        values.get("TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID")
    ):
        raise StagingCutoverError("publisher_channel_identity_mismatch")
    identities: dict[str, dict[str, Any]] = {
        "primary": {
            "credential_fingerprint": _credential_fingerprint(
                values.get("BOT_TOKEN")
            ),
            "credential_binding_sha256": _credential_binding_sha256(
                values.get("BOT_TOKEN")
            ),
            "bot_id": _positive_bot_id(
                values.get("TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID")
            ),
            "username": _normalize_username(values.get("BOT_USERNAME")),
        }
    }
    for index, identity in enumerate(PUBLISHER_IDENTITIES, start=1):
        prefix = f"TELEGRAM_PUBLISHER_{index}"
        if not _truthy(values.get(f"{prefix}_ENABLED")):
            raise StagingCutoverError(f"publisher_lane_missing:{identity}")
        identities[identity] = {
            "credential_fingerprint": _credential_fingerprint(
                values.get(f"{prefix}_BOT_TOKEN")
            ),
            "credential_binding_sha256": _credential_binding_sha256(
                values.get(f"{prefix}_BOT_TOKEN")
            ),
            "bot_id": _positive_bot_id(values.get(f"{prefix}_EXPECTED_BOT_ID")),
            "username": _normalize_username(
                values.get(f"{prefix}_EXPECTED_USERNAME")
            ),
        }
    if _truthy(values.get("TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED")):
        raise StagingCutoverError("unexpected_channel_editor_identity_enabled")
    credentials = {
        identity["credential_binding_sha256"] for identity in identities.values()
    }
    bot_ids = {identity["bot_id"] for identity in identities.values()}
    usernames = {identity["username"] for identity in identities.values()}
    if (
        len(credentials) != len(EXPECTED_QUEUE_IDENTITIES)
        or len(bot_ids) != len(EXPECTED_QUEUE_IDENTITIES)
        or len(usernames) != len(EXPECTED_QUEUE_IDENTITIES)
    ):
        raise StagingCutoverError("publisher_identities_not_distinct")
    return identities, channel


def publisher_runtime_evidence_from_observation(
    *,
    source_values: Mapping[str, Any],
    runtime_values: Mapping[str, Any],
    provider_report: Mapping[str, Any],
) -> dict[str, Any]:
    queue_required = dict(bot_process_contract().required)
    queue_required.update(
        {f"TELEGRAM_PUBLISHER_{index}_ENABLED": "true" for index in range(1, 6)}
    )
    for key, expected in queue_required.items():
        if str(source_values.get(key) or "").strip().lower() != expected.lower():
            raise StagingCutoverError("publisher_source_queue_profile_invalid")
        if str(runtime_values.get(key) or "").strip().lower() != expected.lower():
            raise StagingCutoverError("publisher_runtime_queue_profile_invalid")
    source_identities, source_channel = _publisher_identity_values(source_values)
    runtime_identities, runtime_channel = _publisher_identity_values(runtime_values)
    if source_channel != runtime_channel:
        raise StagingCutoverError("publisher_runtime_source_mismatch")
    for identity in EXPECTED_QUEUE_IDENTITIES:
        source = source_identities[identity]
        runtime = runtime_identities[identity]
        if (
            not hmac.compare_digest(
                source["credential_binding_sha256"],
                runtime["credential_binding_sha256"],
            )
            or source["bot_id"] != runtime["bot_id"]
            or source["username"] != runtime["username"]
        ):
            raise StagingCutoverError("publisher_runtime_source_mismatch")

    provider_identities = provider_report.get("identities")
    if not isinstance(provider_identities, list):
        raise StagingCutoverError("publisher_provider_evidence_invalid")
    reports = {
        str(item.get("bot_identity") or ""): dict(item)
        for item in provider_identities
        if isinstance(item, Mapping)
    }
    if (
        provider_report.get("status") != "approved"
        or int(provider_report.get("identity_count") or 0)
        != len(EXPECTED_QUEUE_IDENTITIES)
        or len(provider_identities) != len(EXPECTED_QUEUE_IDENTITIES)
        or len(reports) != len(provider_identities)
        or tuple(provider_report.get("approved_bot_identities") or ())
        != EXPECTED_QUEUE_IDENTITIES
        or set(reports) != set(EXPECTED_QUEUE_IDENTITIES)
        or int(provider_report.get("read_only_provider_call_count") or 0)
        != len(EXPECTED_QUEUE_IDENTITIES) * 3
        or provider_report.get("sensitive_values_disclosed") is not False
    ):
        raise StagingCutoverError("publisher_provider_evidence_invalid")
    channel_fingerprint = _provider_fingerprint("channel", source_channel)
    required_permissions = {
        "primary": {
            "can_manage_chat",
            "can_post_messages",
            "can_edit_messages",
            "can_restrict_members",
        },
        **{
            identity: {
                "can_manage_chat",
                "can_post_messages",
                "can_edit_messages",
                "can_delete_messages",
            }
            for identity in PUBLISHER_IDENTITIES
        },
    }
    safe_identities: list[dict[str, Any]] = []
    for identity in EXPECTED_QUEUE_IDENTITIES:
        expected = source_identities[identity]
        report = reports[identity]
        permissions = set(report.get("effective_permissions") or ())
        if (
            report.get("credential_fingerprint")
            != expected["credential_fingerprint"]
            or report.get("bot_fingerprint")
            != _provider_fingerprint("bot", expected["bot_id"])
            or report.get("username_fingerprint")
            != _username_fingerprint(expected["username"])
            or report.get("channel_fingerprint") != channel_fingerprint
            or report.get("member_status") != "administrator"
            or not required_permissions[identity].issubset(permissions)
        ):
            raise StagingCutoverError("publisher_provider_identity_mismatch")
        safe_identities.append(
            {
                "bot_identity": identity,
                "credential_fingerprint": expected["credential_fingerprint"],
                "credential_binding_sha256": expected[
                    "credential_binding_sha256"
                ],
                "bot_fingerprint": _provider_fingerprint(
                    "bot", expected["bot_id"]
                ),
                "username_fingerprint": _username_fingerprint(
                    expected["username"]
                ),
                "channel_fingerprint": channel_fingerprint,
                "member_status": "administrator",
                "required_permissions_present": True,
                "runtime_matches_source": True,
            }
        )
    binding_material = json.dumps(
        safe_identities,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "status": "verified",
        "identity_count": len(EXPECTED_QUEUE_IDENTITIES),
        "publisher_lane_count": len(PUBLISHER_IDENTITIES),
        "approved_bot_identities": list(EXPECTED_QUEUE_IDENTITIES),
        "identities": safe_identities,
        "all_credentials_distinct": True,
        "all_bot_ids_distinct": True,
        "all_usernames_distinct": True,
        "runtime_matches_source": True,
        "provider_identity_and_permissions_verified": True,
        "read_only_provider_call_count": int(
            provider_report.get("read_only_provider_call_count") or 0
        ),
        "identity_binding_sha256": hashlib.sha256(binding_material).hexdigest(),
        "sensitive_values_disclosed": False,
    }


def _run_publisher_provider_preflight() -> dict[str, Any]:
    _require_staging_project(FOREIGN_BOT_CONTAINER)
    script = r'''import asyncio
import hashlib
import json

from core.config import settings
from core.telegram_delivery_credentials import (
    configured_telegram_delivery_credentials,
    normalize_telegram_bot_username,
)
from core.telegram_delivery_preflight import run_telegram_delivery_preflight
from scripts.cutover_telegram_delivery_queue_staging import (
    _validated_provider_preflight_result,
)


def username_fingerprint(value):
    material = f"telegram-preflight-v1:username:{value}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


async def main():
    registry = configured_telegram_delivery_credentials(settings)
    roles = ("primary", "publisher_1", "publisher_2", "publisher_3", "publisher_4", "publisher_5")
    if registry.bot_identities != roles or tuple(registry.publisher_lanes) != roles[1:]:
        raise RuntimeError("staging_publisher_role_set_invalid")
    expected_identities = {
        "primary": {
            "bot_id": settings.telegram_delivery_queue_expected_primary_bot_id,
            "username": normalize_telegram_bot_username(settings.bot_username, identity="primary"),
        },
        **{
            role: {
                "bot_id": registry.publisher_lane(role).expected_bot_id,
                "username": registry.publisher_lane(role).expected_username,
            }
            for role in roles[1:]
        },
    }
    base_calls = registry.build_gateway_calls()
    observed_usernames = {}
    wrapped_calls = {}
    for role, call in base_calls.items():
        def bind(bound_role, bound_call):
            async def invoke(method, payload, *, timeout=10, idempotency_key=None):
                result = await bound_call(
                    method,
                    payload,
                    timeout=timeout,
                    idempotency_key=idempotency_key,
                )
                provider_result = _validated_provider_preflight_result(
                    result,
                    method=method,
                    bot_identity=bound_role,
                    expected_bot_id=expected_identities[bound_role]["bot_id"],
                    expected_username=expected_identities[bound_role]["username"],
                    expected_channel_id=settings.telegram_delivery_queue_expected_channel_id,
                    request_payload=payload,
                )
                if method == "getMe":
                    observed_usernames[bound_role] = normalize_telegram_bot_username(
                        provider_result.get("username"), identity=bound_role
                    )
                return result
            return invoke
        wrapped_calls[role] = bind(role, call)
    report = await run_telegram_delivery_preflight(
        credential_registry=registry,
        channel_id=settings.channel_id,
        expected_channel_id=settings.telegram_delivery_queue_expected_channel_id,
        expected_primary_bot_id=settings.telegram_delivery_queue_expected_primary_bot_id,
        editor_enabled=False,
        publisher_lanes=registry.publisher_lanes,
        timeout_seconds=settings.telegram_delivery_queue_preflight_timeout_seconds,
        malformed_retry_after_fallback_seconds=settings.telegram_delivery_queue_retry_base_seconds,
        gateway_calls=wrapped_calls,
    )
    expected_usernames = {
        "primary": normalize_telegram_bot_username(settings.bot_username, identity="primary"),
        **{
            role: registry.publisher_lane(role).expected_username
            for role in roles[1:]
        },
    }
    if observed_usernames != expected_usernames:
        raise RuntimeError("staging_provider_username_mismatch")
    payload = {
        "status": "approved",
        "identity_count": len(report.identities),
        "approved_bot_identities": list(report.approved_bot_identities),
        "identities": [
            {
                "bot_identity": item.bot_identity,
                "credential_fingerprint": item.credential_fingerprint,
                "bot_fingerprint": item.bot_fingerprint,
                "username_fingerprint": username_fingerprint(observed_usernames[item.bot_identity]),
                "channel_fingerprint": item.channel_fingerprint,
                "member_status": item.member_status,
                "effective_permissions": list(item.effective_permissions),
            }
            for item in report.identities
        ],
        "read_only_provider_call_count": len(report.identities) * 3,
        "sensitive_values_disclosed": False,
    }
    print(json.dumps(payload, sort_keys=True))


asyncio.run(main())
'''
    result = _run(
        [
            "docker",
            "exec",
            "-w",
            "/app",
            FOREIGN_BOT_CONTAINER,
            "python",
            "-c",
            script,
        ],
        timeout=180,
    )
    if result.returncode != 0:
        raise StagingCutoverError("publisher_provider_preflight_failed")
    try:
        payload = json.loads((result.stdout or "").strip())
    except (TypeError, ValueError):
        raise StagingCutoverError("publisher_provider_preflight_failed") from None
    if not isinstance(payload, dict):
        raise StagingCutoverError("publisher_provider_preflight_failed")
    return payload


def collect_publisher_runtime_evidence() -> dict[str, Any]:
    source_values = parse_env_file(FOREIGN_ENV_FILE)
    required_keys = tuple(bot_process_contract().required) + (
        "BOT_TOKEN",
        "BOT_USERNAME",
        "CHANNEL_ID",
        "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID",
        "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED",
        *(f"TELEGRAM_PUBLISHER_{index}_ENABLED" for index in range(1, 6)),
        *(f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN" for index in range(1, 6)),
        *(f"TELEGRAM_PUBLISHER_{index}_EXPECTED_BOT_ID" for index in range(1, 6)),
        *(f"TELEGRAM_PUBLISHER_{index}_EXPECTED_USERNAME" for index in range(1, 6)),
    )
    runtime_values = _container_env(FOREIGN_BOT_CONTAINER, required_keys)
    return publisher_runtime_evidence_from_observation(
        source_values=source_values,
        runtime_values=runtime_values,
        provider_report=_run_publisher_provider_preflight(),
    )


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
    binding = _git_binding()
    inventory = collect_executor_inventory(expected_owner="queue-v1")
    foreign_bot = _redacted_runtime(FOREIGN_BOT_CONTAINER, "bot")
    if (
        foreign_bot.get("service") != "bot"
        or foreign_bot.get("server_mode") != "foreign"
        or foreign_bot.get("environment") != "staging"
        or foreign_bot.get("release_sha") != binding.get("head")
        or foreign_bot.get("missing_required")
    ):
        raise StagingCutoverError("bot_runtime_contract_not_ready")
    api_runtime = collect_api_runtime_evidence(
        expected_release_sha=binding["head"],
        include_iran=True,
    )
    publisher_runtime = collect_publisher_runtime_evidence()
    return {
        "observed_at": _utc_now(),
        "git": binding,
        "executor_inventory": inventory,
        "foreign_bot": foreign_bot,
        "api_runtime": api_runtime,
        "publisher_runtime": publisher_runtime,
        "executor_overlap": False,
        "iran_token_violation": False,
        "five_publishers_verified": publisher_runtime["publisher_lane_count"] == 5,
        "cutover_ready": True,
        "secret_values_disclosed": False,
    }


def _pg_dump(container: str, destination: Path) -> str:
    _require_staging_project(container)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        result = _run_contained(
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
            stdout=handle,
            timeout=1200,
            text=False,
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
        restore = _run_contained(
            [
                "docker",
                "exec",
                "-i",
                FOREIGN_DB_CONTAINER,
                "sh",
                "-c",
                f'psql -U "$POSTGRES_USER" -d {RESTORE_DB_NAME} -v ON_ERROR_STOP=1 -q',
            ],
            stdin=handle,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=1200,
            text=False,
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
    return _run_contained(
        args,
        input_data=data,
        timeout=timeout,
        text=True,
    )


def _require_clean_pushed_main(binding: dict[str, str]) -> None:
    if binding.get("branch") != "main":
        raise StagingCutoverError("cutover_requires_main_branch")
    if binding.get("worktree") != "clean":
        raise StagingCutoverError("cutover_requires_clean_worktree")
    if binding.get("head") != binding.get("origin_main"):
        raise StagingCutoverError("cutover_requires_pushed_main")


def _assert_git_binding_unchanged(expected: Mapping[str, str]) -> dict[str, str]:
    observed = _git_binding()
    _require_clean_pushed_main(observed)
    for key in ("branch", "head", "tree", "origin_main", "worktree"):
        if observed.get(key) != expected.get(key):
            raise StagingCutoverError("redeploy_git_binding_drift")
    return observed


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


def snapshot_queue_aggregates(
    container: str = FOREIGN_DB_CONTAINER,
) -> dict[str, Any]:
    if container not in {FOREIGN_DB_CONTAINER, IRAN_DB_CONTAINER}:
        raise StagingCutoverError("queue_aggregate_database_role_invalid")
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
        _docker_args(
            container,
            [
                "exec",
                container,
                "sh",
                "-c",
                f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc {shlex.quote(query)}',
            ],
        )
    )
    if result.returncode != 0:
        raise StagingCutoverError("queue_aggregate_snapshot_failed")
    payload = json.loads((result.stdout or "").strip() or "{}")
    payload["observed_at"] = _utc_now()
    payload["database"] = STAGING_DB_NAME
    payload["host_role"] = "iran" if container == IRAN_DB_CONTAINER else "foreign"
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_trusted_evidence_file(path: Path) -> tuple[bytes, str]:
    """Read one operator-owned, non-shared-writable file from a single fd."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
            or metadata.st_nlink != 1
        ):
            raise StagingCutoverError("redeploy_evidence_file_untrusted")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            chunks.append(chunk)
        return b"".join(chunks), digest.hexdigest()
    except OSError:
        raise StagingCutoverError("redeploy_evidence_file_unreadable") from None
    finally:
        os.close(descriptor)


def _require_trusted_evidence_directory(path: Path) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise StagingCutoverError("redeploy_evidence_directory_untrusted")
    except OSError:
        raise StagingCutoverError("redeploy_evidence_directory_unreadable") from None
    finally:
        os.close(descriptor)


def _content_digest(root: Path, relative_paths: Sequence[Path]) -> tuple[str, int]:
    """Hash path/type/content without following symlinks or runtime caches."""

    digest = hashlib.sha256()
    count = 0
    for relative in sorted(set(relative_paths), key=lambda item: item.as_posix()):
        candidate = root / relative
        if candidate.is_symlink():
            kind = "L"
            payload = os.readlink(candidate).encode("utf-8")
        elif candidate.is_file():
            kind = "F"
            payload = bytes.fromhex(_sha256_file(candidate))
        else:
            raise StagingCutoverError("release_content_entry_invalid")
        rendered = relative.as_posix().encode("utf-8")
        digest.update(kind.encode("ascii"))
        digest.update(len(rendered).to_bytes(8, "big"))
        digest.update(rendered)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        count += 1
    if count <= 0:
        raise StagingCutoverError("release_content_empty")
    return digest.hexdigest(), count


def _runtime_source_paths(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for directory in RUNTIME_SOURCE_DIRECTORIES:
        source = root / directory
        if not source.is_dir():
            continue
        for candidate in source.rglob("*"):
            relative = candidate.relative_to(root)
            if "__pycache__" in relative.parts or candidate.suffix in {".pyc", ".pyo"}:
                continue
            if candidate.is_file() or candidate.is_symlink():
                paths.append(relative)
    for filename in RUNTIME_SOURCE_FILES:
        candidate = root / filename
        if candidate.is_file() or candidate.is_symlink():
            paths.append(Path(filename))
    return tuple(paths)


def _directory_content_evidence(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise StagingCutoverError("release_content_directory_missing")
    relative_paths = tuple(
        candidate.relative_to(root)
        for candidate in root.rglob("*")
        if candidate.is_file() or candidate.is_symlink()
    )
    digest, count = _content_digest(root, relative_paths)
    return {"sha256": digest, "file_count": count}


@contextmanager
def _tracked_head_export(
    *,
    expected_head: str,
    expected_tree: str,
) -> Any:
    """Yield a temporary export containing only bytes tracked by ``HEAD``."""

    if not (
        len(expected_head) == 40
        and len(expected_tree) == 40
        and all(character in "0123456789abcdef" for character in expected_head + expected_tree)
    ):
        raise StagingCutoverError("tracked_release_binding_invalid")
    observed_tree = _run(["git", "rev-parse", f"{expected_head}^{{tree}}"])
    if observed_tree.returncode != 0 or (observed_tree.stdout or "").strip() != expected_tree:
        raise StagingCutoverError("tracked_release_tree_mismatch")
    with tempfile.TemporaryDirectory(prefix="staging-release-export-") as directory:
        temporary = Path(directory)
        archive = temporary / "release.tar"
        export_root = temporary / "tracked"
        export_root.mkdir(mode=0o700)
        with archive.open("wb") as handle:
            archived = _run_contained(
                ["git", "archive", "--format=tar", expected_head],
                stdout=handle,
                timeout=180,
                text=False,
            )
        if archived.returncode != 0:
            raise StagingCutoverError("tracked_release_archive_failed")
        extracted = _run(
            ["tar", "-xf", str(archive), "-C", str(export_root)],
            timeout=180,
        )
        if extracted.returncode != 0:
            raise StagingCutoverError("tracked_release_extract_failed")
        source_digest, source_count = _content_digest(
            export_root,
            _runtime_source_paths(export_root),
        )
        yield export_root, {
            "git_head": expected_head,
            "git_tree": expected_tree,
            "git_archive_sha256": _sha256_file(archive),
            "runtime_source_sha256": source_digest,
            "runtime_source_file_count": source_count,
            "ignored_worktree_files_exported": False,
        }


_CONTAINER_CONTENT_EVIDENCE_SCRIPT = r'''
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path

ROOT = Path("/app")
DIRECTORIES = ("api", "bot", "core", "src", "migrations", "models", "templates", "fonts", "scripts")
FILES = ("alembic.ini", "main.py", "manage.py", "run_bot.py", "schemas.py", "seed_fake_data.py", "trading_settings.json")

def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()

def digest_paths(root, paths):
    digest = hashlib.sha256()
    count = 0
    for relative in sorted(set(paths), key=lambda item: item.as_posix()):
        candidate = root / relative
        if candidate.is_symlink():
            kind = b"L"
            payload = os.readlink(candidate).encode("utf-8")
        elif candidate.is_file():
            kind = b"F"
            payload = file_sha(candidate)
        else:
            raise RuntimeError("runtime_content_entry_invalid")
        rendered = relative.as_posix().encode("utf-8")
        digest.update(kind)
        digest.update(len(rendered).to_bytes(8, "big"))
        digest.update(rendered)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        count += 1
    if count <= 0:
        raise RuntimeError("runtime_content_empty")
    return digest.hexdigest(), count

source_paths = []
for directory in DIRECTORIES:
    source = ROOT / directory
    if not source.is_dir():
        continue
    for candidate in source.rglob("*"):
        relative = candidate.relative_to(ROOT)
        if "__pycache__" in relative.parts or candidate.suffix in {".pyc", ".pyo"}:
            continue
        if candidate.is_file() or candidate.is_symlink():
            source_paths.append(relative)
for filename in FILES:
    candidate = ROOT / filename
    if candidate.is_file() or candidate.is_symlink():
        source_paths.append(Path(filename))

frontend_root = ROOT / "mini_app_dist"
frontend_paths = [
    candidate.relative_to(frontend_root)
    for candidate in frontend_root.rglob("*")
    if candidate.is_file() or candidate.is_symlink()
]
source_digest, source_count = digest_paths(ROOT, source_paths)
frontend_digest, frontend_count = digest_paths(frontend_root, frontend_paths)
packages = sorted(
    f"{str(item.metadata.get('Name') or '').lower()}=={item.version}"
    for item in importlib.metadata.distributions()
)
print(json.dumps({
    "runtime_source_sha256": source_digest,
    "runtime_source_file_count": source_count,
    "frontend_sha256": frontend_digest,
    "frontend_file_count": frontend_count,
    "dependency_sha256": hashlib.sha256("\n".join(packages).encode("utf-8")).hexdigest(),
}, sort_keys=True))
'''


def _runtime_release_evidence(
    role: str,
    containers: Sequence[str],
    *,
    expected_head: str,
    expected_tree: str,
    expected_source_digest: str,
    expected_frontend_digest: str,
) -> dict[str, Any]:
    if role not in {"foreign", "iran"} or not containers:
        raise StagingCutoverError("runtime_release_role_invalid")
    image_ids: dict[str, str] = {}
    for container in containers:
        _require_staging_project(container)
        release = _container_env(container, ("RELEASE_SHA",)).get("RELEASE_SHA")
        if release != expected_head:
            raise StagingCutoverError("runtime_release_sha_mismatch")
        inspected = _run(
            _docker_args(
                container,
                ["inspect", "-f", "{{.Image}}", container],
            )
        )
        image_id = (inspected.stdout or "").strip()
        if (
            inspected.returncode != 0
            or not image_id.startswith("sha256:")
            or len(image_id) != 71
            or any(character not in "0123456789abcdef" for character in image_id[7:])
        ):
            raise StagingCutoverError("runtime_image_identity_invalid")
        image_ids[container] = image_id
    if len(set(image_ids.values())) != 1:
        raise StagingCutoverError("runtime_host_image_split")
    representative = containers[0]
    measured = _run(
        _docker_args(
            representative,
            ["exec", "-w", "/app", representative, "python", "-c", _CONTAINER_CONTENT_EVIDENCE_SCRIPT],
        ),
        timeout=180,
    )
    if measured.returncode != 0:
        raise StagingCutoverError("runtime_content_evidence_failed")
    try:
        content = json.loads((measured.stdout or "").strip())
    except (TypeError, ValueError):
        raise StagingCutoverError("runtime_content_evidence_failed") from None
    required_digests = (
        "runtime_source_sha256",
        "frontend_sha256",
        "dependency_sha256",
    )
    if (
        not isinstance(content, dict)
        or any(
            len(str(content.get(key) or "")) != 64
            or any(
                character not in "0123456789abcdef"
                for character in str(content.get(key) or "")
            )
            for key in required_digests
        )
        or str(content.get("runtime_source_sha256")) != expected_source_digest
        or str(content.get("frontend_sha256")) != expected_frontend_digest
        or int(content.get("runtime_source_file_count") or 0) <= 0
        or int(content.get("frontend_file_count") or 0) <= 0
    ):
        raise StagingCutoverError("runtime_content_binding_mismatch")
    binding_material = "\0".join(
        (
            expected_head,
            expected_tree,
            expected_source_digest,
            str(content["frontend_sha256"]),
            str(content["dependency_sha256"]),
            next(iter(image_ids.values())),
        )
    ).encode("utf-8")
    return {
        "role": role,
        "git_head": expected_head,
        "git_tree": expected_tree,
        "runtime_source_sha256": expected_source_digest,
        "runtime_source_file_count": int(content["runtime_source_file_count"]),
        "frontend_sha256": str(content["frontend_sha256"]),
        "frontend_file_count": int(content["frontend_file_count"]),
        "dependency_sha256": str(content["dependency_sha256"]),
        "image_id_sha256": next(iter(image_ids.values()))[7:],
        "surface_count": len(containers),
        "content_binding_sha256": hashlib.sha256(binding_material).hexdigest(),
        "release_sha_env_only": False,
        "secret_values_disclosed": False,
    }


def _assert_release_parity(
    foreign: Mapping[str, Any],
    iran: Mapping[str, Any],
    *,
    expected_head: str,
    expected_tree: str,
    expected_source_digest: str,
) -> dict[str, Any]:
    def valid_sha256(value: Any) -> bool:
        rendered = str(value or "")
        return len(rendered) == 64 and all(
            character in "0123456789abcdef" for character in rendered
        )

    for evidence, role, surface_count in ((foreign, "foreign", 3), (iran, "iran", 2)):
        expected_content_binding = hashlib.sha256(
            "\0".join(
                (
                    expected_head,
                    expected_tree,
                    expected_source_digest,
                    str(evidence.get("frontend_sha256") or ""),
                    str(evidence.get("dependency_sha256") or ""),
                    f"sha256:{str(evidence.get('image_id_sha256') or '')}",
                )
            ).encode("utf-8")
        ).hexdigest()
        if (
            evidence.get("role") != role
            or evidence.get("git_head") != expected_head
            or evidence.get("git_tree") != expected_tree
            or evidence.get("runtime_source_sha256") != expected_source_digest
            or int(evidence.get("surface_count") or 0) != surface_count
            or int(evidence.get("runtime_source_file_count") or 0) <= 0
            or int(evidence.get("frontend_file_count") or 0) <= 0
            or not valid_sha256(evidence.get("frontend_sha256"))
            or not valid_sha256(evidence.get("dependency_sha256"))
            or not valid_sha256(evidence.get("image_id_sha256"))
            or evidence.get("content_binding_sha256") != expected_content_binding
            or evidence.get("release_sha_env_only") is not False
            or evidence.get("secret_values_disclosed") is not False
        ):
            raise StagingCutoverError("runtime_release_parity_invalid")
    for key in ("runtime_source_sha256", "frontend_sha256", "dependency_sha256"):
        if foreign.get(key) != iran.get(key):
            raise StagingCutoverError("runtime_release_content_split")
    binding = hashlib.sha256(
        "\0".join(
            (
                expected_head,
                expected_tree,
                expected_source_digest,
                str(foreign["frontend_sha256"]),
                str(foreign["dependency_sha256"]),
                str(foreign["image_id_sha256"]),
                str(iran["image_id_sha256"]),
            )
        ).encode("utf-8")
    ).hexdigest()
    return {
        "status": "verified",
        "git_head": expected_head,
        "git_tree": expected_tree,
        "runtime_source_sha256": expected_source_digest,
        "frontend_sha256": foreign["frontend_sha256"],
        "dependency_sha256": foreign["dependency_sha256"],
        "cross_host_binding_sha256": binding,
        "image_identity_recorded_per_host": True,
        "secret_values_disclosed": False,
    }


def _write_redeploy_journal(
    receipt: Mapping[str, Any],
    *,
    phase: str,
) -> Path:
    # Recovery state is deliberately independent from the caller-selected
    # receipt directory.  Otherwise a different --artifact-dir would silently
    # bypass an interrupted rollout's exact-SHA recovery requirement.
    REDEPLOY_STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Persist the state-directory entry as well as the journal itself.  This
    # matters on the first redeploy, when the directory may have been created
    # immediately before the durable pre-stop marker.
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    parent_descriptor = os.open(REDEPLOY_STATE_DIR.parent, parent_flags)
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)
    path = REDEPLOY_STATE_DIR / REDEPLOY_JOURNAL_NAME
    payload = {
        "schema_version": 1,
        "environment": "staging",
        "command": "redeploy",
        "status": receipt.get("status"),
        "phase": phase,
        "git": receipt.get("git"),
        "mutation_started": bool(receipt.get("mutation_started")),
        "runtime_mutation_started": bool(receipt.get("runtime_mutation_started")),
        "recovery": receipt.get("recovery"),
        "orchestration_successor": receipt.get("orchestration_successor"),
        "updated_at": _utc_now(),
        "secrets_disclosed": False,
    }
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{REDEPLOY_JOURNAL_NAME}.",
        suffix=".tmp",
        dir=REDEPLOY_STATE_DIR,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_descriptor = os.open(REDEPLOY_STATE_DIR, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return path


@contextmanager
def _redeploy_lock() -> Any:
    REDEPLOY_STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = REDEPLOY_STATE_DIR / REDEPLOY_LOCK_NAME
    with path.open("a+", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StagingCutoverError("redeploy_already_running") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _redeploy_recovery_state(
    *,
    expected_head: str,
    orchestration_successor_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the prior journal before any overwrite and bind recovery to SHA."""

    path = REDEPLOY_STATE_DIR / REDEPLOY_JOURNAL_NAME
    if not path.exists():
        if orchestration_successor_request is not None:
            raise StagingCutoverError("redeploy_successor_not_required")
        return {"mode": "new", "runtime_mutation_started": False}
    try:
        journal_bytes, journal_sha256 = _read_trusted_evidence_file(path)
        payload = json.loads(journal_bytes.decode("utf-8"))
    except (OSError, TypeError, UnicodeDecodeError, ValueError):
        raise StagingCutoverError("redeploy_journal_invalid") from None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("environment") != "staging"
        or payload.get("command") != "redeploy"
    ):
        raise StagingCutoverError("redeploy_journal_invalid")
    phase = str(payload.get("phase") or "unknown")
    if phase == "completed" and payload.get("status") == "redeployed":
        if orchestration_successor_request is not None:
            raise StagingCutoverError("redeploy_successor_not_required")
        return {"mode": "new", "runtime_mutation_started": False}
    recovery = payload.get("recovery")
    safely_failed_before_mutation = bool(
        phase == "failed"
        and payload.get("status") == "failed_before_runtime_mutation"
        and payload.get("mutation_started") is False
        and payload.get("runtime_mutation_started") is False
        and isinstance(recovery, Mapping)
        and recovery.get("required") is False
        and recovery.get("strategy") == "none"
        and recovery.get("resume_error_code") is None
    )
    if safely_failed_before_mutation:
        if orchestration_successor_request is not None:
            raise StagingCutoverError("redeploy_successor_not_required")
        # A fully contained preflight/build failure has no runtime state to
        # reconcile.  It is a terminal journal record, so a corrected pushed
        # SHA may start a fresh transaction without deleting state by hand.
        return {
            "mode": "new_after_contained_preflight_failure",
            "prior_phase": phase,
            "prior_journal_sha256": journal_sha256,
            "runtime_mutation_started": False,
            "secret_values_disclosed": False,
        }
    git = payload.get("git")
    prior_head = str(git.get("head") or "") if isinstance(git, Mapping) else ""
    if prior_head == expected_head and orchestration_successor_request is not None:
        raise StagingCutoverError("redeploy_successor_not_required")
    if prior_head != expected_head:
        successor = _validated_redeploy_orchestration_successor(
            payload,
            journal_sha256=journal_sha256,
            prior_head=prior_head,
            expected_head=expected_head,
            request=orchestration_successor_request,
        )
        if successor is None:
            raise StagingCutoverError("redeploy_recovery_requires_exact_same_sha")
        return {
            "mode": "recover_safe_orchestration_successor",
            "prior_phase": phase,
            "prior_status": str(payload.get("status") or "unknown"),
            "prior_head": prior_head,
            "successor_head": expected_head,
            "orchestration_successor": successor,
            "mutation_started": True,
            "runtime_mutation_started": True,
            "prior_journal_sha256": journal_sha256,
            "secret_values_disclosed": False,
        }
    mutating_phases = {
        "quiescing_ingress",
        "ingress_quiesced",
        "iran_payload_staged",
        "producer_start_started",
        "producers_verified",
        "runtime_parity_verified",
    }
    mutation_started = (
        bool(payload.get("mutation_started")) or phase in mutating_phases
    )
    # Stopping the existing runtime is itself a runtime mutation.  Older
    # journals may have recorded only mutation_started during this window, so
    # recovery deliberately promotes either signal (or an unsafe phase) to the
    # exact-SHA fail-closed path.
    runtime_mutation_started = bool(
        payload.get("runtime_mutation_started")
    ) or mutation_started
    return {
        "mode": "recover_exact_sha",
        "prior_phase": phase,
        "prior_status": str(payload.get("status") or "unknown"),
        "mutation_started": mutation_started,
        "runtime_mutation_started": runtime_mutation_started,
        "prior_journal_sha256": journal_sha256,
        "orchestration_successor": payload.get("orchestration_successor"),
        "secret_values_disclosed": False,
    }


def _validated_redeploy_orchestration_successor(
    journal: Mapping[str, Any],
    *,
    journal_sha256: str,
    prior_head: str,
    expected_head: str,
    request: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Admit one direct, tooling-only fix after a contained producer-start bug.

    Exact-SHA recovery remains the default.  This exception is deliberately
    limited to the deterministic Iran prebuilt-producer profile failure, a
    fully stopped five-container runtime, and one direct child commit whose
    diff cannot include application, schema, dependency, or environment files.
    """

    if not isinstance(request, Mapping) or journal.get("orchestration_successor"):
        return None
    if (
        journal.get("phase") != "failed"
        or journal.get("status") != "failed_forward_reconcile_required"
        or journal.get("mutation_started") is not True
        or journal.get("runtime_mutation_started") is not True
    ):
        return None
    recovery = journal.get("recovery")
    if (
        not isinstance(recovery, Mapping)
        or recovery.get("required") is not True
        or recovery.get("runtime_left_quiesced") is not True
        or recovery.get("strategy") != "rerun_exact_same_pushed_sha"
        or recovery.get("git_head") != prior_head
    ):
        return None
    actual_journal_sha256 = journal_sha256
    if (
        request.get("prior_head") != prior_head
        or request.get("prior_journal_sha256") != actual_journal_sha256
    ):
        return None
    try:
        raw_artifact_dir = Path(str(request.get("artifact_dir") or ""))
        raw_failure_receipt = Path(str(request.get("failure_receipt") or ""))
        if raw_artifact_dir.is_symlink() or raw_failure_receipt.is_symlink():
            return None
        artifact_dir = raw_artifact_dir.resolve(strict=True)
        failure_receipt = raw_failure_receipt.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if (
        not failure_receipt.is_file()
        or failure_receipt.parent != artifact_dir
        or not failure_receipt.name.startswith("cutover-redeploy-failure-")
        or failure_receipt.suffix != ".json"
    ):
        return None
    try:
        _require_trusted_evidence_directory(artifact_dir)
        failure_bytes, actual_receipt_sha256 = _read_trusted_evidence_file(
            failure_receipt
        )
    except (OSError, StagingCutoverError):
        return None
    if request.get("failure_receipt_sha256") != actual_receipt_sha256:
        return None
    try:
        failure = json.loads(failure_bytes.decode("utf-8"))
    except (TypeError, UnicodeDecodeError, ValueError):
        return None
    failure_git = failure.get("git") if isinstance(failure, Mapping) else None
    failure_recovery = (
        failure.get("recovery") if isinstance(failure, Mapping) else None
    )
    if (
        not isinstance(failure, Mapping)
        or failure.get("schema_version") != 1
        or failure.get("environment") != "staging"
        or failure.get("command") != "redeploy"
        or failure.get("status") != "failed_forward_reconcile_required"
        or failure.get("error_code") != "iran_prebuilt_producer_start_failed"
        or failure.get("mutation_started") is not True
        or failure.get("runtime_mutation_started") is not True
        or not isinstance(failure_git, Mapping)
        or failure_git.get("head") != prior_head
        or not isinstance(failure_recovery, Mapping)
        or failure_recovery.get("required") is not True
        or failure_recovery.get("runtime_left_quiesced") is not True
        or failure_recovery.get("strategy") != "rerun_exact_same_pushed_sha"
        or failure_recovery.get("git_head") != prior_head
    ):
        return None
    steps = failure.get("steps")
    if not isinstance(steps, list):
        return None
    containment = next(
        (
            step
            for step in reversed(steps)
            if isinstance(step, Mapping)
            and step.get("name") == "failure_containment"
        ),
        None,
    )
    events = containment.get("events") if isinstance(containment, Mapping) else None
    if not isinstance(events, list) or len(events) != len(REDEPLOY_RUNTIME_CONTAINERS):
        return None
    observed_containers: set[str] = set()
    for event in events:
        if (
            not isinstance(event, Mapping)
            or event.get("action") not in {"stop", "already_stopped"}
            or event.get("running") is not False
        ):
            return None
        observed_containers.add(str(event.get("container") or ""))
    if observed_containers != set(REDEPLOY_RUNTIME_CONTAINERS):
        return None
    lineage = _run(
        ["git", "rev-list", "--parents", "-n", "1", expected_head],
        timeout=30,
    )
    lineage_parts = str(lineage.stdout or "").strip().split()
    if (
        lineage.returncode != 0
        or len(lineage_parts) != 2
        or lineage_parts[0] != expected_head
        or lineage_parts[1] != prior_head
    ):
        return None
    changed = _run(
        [
            "git",
            "diff",
            "--name-status",
            "--no-renames",
            f"{prior_head}..{expected_head}",
        ],
        timeout=30,
    )
    if changed.returncode != 0:
        return None
    changed_paths: list[str] = []
    for line in str(changed.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or parts[0] != "M" or not parts[1]:
            return None
        changed_paths.append(parts[1])
    if set(changed_paths) != set(SAFE_REDEPLOY_ORCHESTRATION_SUCCESSOR_PATHS):
        return None
    summary = _run(
        ["git", "diff", "--summary", f"{prior_head}..{expected_head}"],
        timeout=30,
    )
    if summary.returncode != 0 or str(summary.stdout or "").strip():
        return None
    diff = _run(
        [
            "git",
            "diff",
            "--binary",
            "--full-index",
            f"{prior_head}..{expected_head}",
        ],
        timeout=30,
    )
    if diff.returncode != 0 or not str(diff.stdout or ""):
        return None
    return {
        "used": True,
        "from_head": prior_head,
        "to_head": expected_head,
        "prior_journal_sha256": actual_journal_sha256,
        "failure_receipt_name": failure_receipt.name,
        "failure_receipt_sha256": actual_receipt_sha256,
        "diff_sha256": hashlib.sha256(
            str(diff.stdout).encode("utf-8")
        ).hexdigest(),
        "changed_paths": sorted(changed_paths),
        "secret_values_disclosed": False,
    }


def _quiesce_redeploy_runtime() -> list[str]:
    stopped: list[str] = []
    try:
        for container in REDEPLOY_RUNTIME_CONTAINERS:
            if _container_running(container):
                event = _stop_container(container)
                if event.get("running") is not False:
                    raise StagingCutoverError(
                        f"redeploy_container_not_quiesced:{container}"
                    )
                stopped.append(container)
    except BaseException:
        # Quiescing precedes release mutation.  Restore the known prior runtime
        # set if the stop sequence itself is interrupted or fails part-way.
        try:
            _resume_redeploy_runtime(stopped)
        except BaseException as resume_exc:
            raise StagingCutoverError(
                "redeploy_quiesce_resume_failed"
            ) from resume_exc
        raise
    return stopped


def _assert_redeploy_runtime_quiesced() -> None:
    for container in REDEPLOY_RUNTIME_CONTAINERS:
        if _container_running(container):
            raise StagingCutoverError(
                f"redeploy_container_not_quiesced:{container}"
            )


def _assert_redeploy_runtime_running() -> None:
    for container in REDEPLOY_RUNTIME_CONTAINERS:
        if not _container_running(container):
            raise StagingCutoverError(
                f"redeploy_required_container_not_running:{container}"
            )


def _resume_redeploy_runtime(containers: Sequence[str]) -> list[dict[str, Any]]:
    requested = set(containers)
    events: list[dict[str, Any]] = []
    for container in (
        FOREIGN_APP_CONTAINER,
        IRAN_APP_CONTAINER,
        FOREIGN_SYNC_CONTAINER,
        IRAN_SYNC_CONTAINER,
        FOREIGN_BOT_CONTAINER,
    ):
        if container not in requested:
            continue
        if _container_running(container):
            events.append(
                {
                    "container": container,
                    "action": "already_running",
                    "at": _utc_now(),
                    "running": True,
                }
            )
        else:
            events.append(_start_container(container))
    return events


def _fail_closed_redeploy_runtime() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for container in REDEPLOY_RUNTIME_CONTAINERS:
        try:
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
        except BaseException as exc:
            events.append(
                {
                    "container": container,
                    "action": "stop_failed",
                    "at": _utc_now(),
                    "error_code": (
                        str(exc)
                        if isinstance(exc, StagingCutoverError)
                        else type(exc).__name__
                    ),
                }
            )
    return events


def _frontend_build_environment() -> tuple[dict[str, str], dict[str, Any]]:
    """Return a bounded Vite environment without inheriting rollout flags.

    Vite treats every ``VITE_*`` value as client-visible build input.  Keeping
    arbitrary shell values would make an artifact neither reproducible nor
    safely attributable to the selected Git tree.
    """

    source_values = parse_env_file(FOREIGN_ENV_FILE)
    api_base_url = str(os.environ.get("STAGING_VITE_API_BASE_URL") or "").strip()
    dev_login = str(
        os.environ.get("STAGING_ENABLE_DEV_LOGIN")
        or source_values.get("STAGING_ENABLE_DEV_LOGIN")
        or "true"
    ).strip()
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("VITE_") or key in {
            "FRONTEND_BUILD_OUT_DIR",
            "NPM_CONFIG_CACHE",
            "STAGING_FRONTEND_DIST_DIR",
            "STAGING_SKIP_FRONTEND_BUILD",
        } or key.lower() == "npm_config_cache":
            env.pop(key, None)
    env.update(
        {
            "VITE_API_BASE_URL": api_base_url,
            "VITE_STAGING_DEV_LOGIN": dev_login,
        }
    )
    safe_config = {
        "api_base_url_sha256": hashlib.sha256(api_base_url.encode("utf-8")).hexdigest(),
        "staging_dev_login": dev_login.lower() in {"1", "true", "yes"},
            "arbitrary_vite_values_inherited": False,
            "npm_cache_inherited": False,
            "skip_frontend_build_inherited": False,
    }
    return env, safe_config


def _staging_frontend_artifact_dir(release_sha: str) -> Path:
    _staging_image_ref(release_sha)
    return REPO_ROOT / "mini_app_dist_staging" / "releases" / release_sha


def _publish_staging_frontend_artifact(
    staged: Path,
    destination: Path,
) -> dict[str, Any]:
    """Publish a complete release-scoped artifact without exposing a partial build."""

    expected_destination = _staging_frontend_artifact_dir(destination.name)
    if destination != expected_destination or not staged.is_dir():
        raise StagingCutoverError("staging_frontend_publish_path_invalid")
    staged_evidence = _directory_content_evidence(staged)
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup_root = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.previous-",
            dir=str(destination.parent),
        )
    )
    previous = backup_root / "artifact"
    previous_moved = False
    cleanup_backup = True
    try:
        if destination.exists() or destination.is_symlink():
            os.replace(destination, previous)
            previous_moved = True
        try:
            os.replace(staged, destination)
        except BaseException:
            if previous_moved and not destination.exists():
                try:
                    os.replace(previous, destination)
                    previous_moved = False
                except BaseException as restore_exc:
                    cleanup_backup = False
                    raise StagingCutoverError(
                        "staging_frontend_previous_artifact_restore_failed"
                    ) from restore_exc
            raise
        try:
            published = _directory_content_evidence(destination)
            if published != staged_evidence:
                raise StagingCutoverError(
                    "staging_frontend_publish_digest_mismatch"
                )
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            if previous_moved:
                try:
                    os.replace(previous, destination)
                    previous_moved = False
                except BaseException as restore_exc:
                    cleanup_backup = False
                    raise StagingCutoverError(
                        "staging_frontend_previous_artifact_restore_failed"
                    ) from restore_exc
            raise
        return published
    finally:
        if cleanup_backup:
            shutil.rmtree(backup_root, ignore_errors=True)


def _build_staging_frontend(
    *,
    expected_head: str,
    expected_tree: str,
) -> dict[str, Any]:
    """Build the UI from an immutable tracked ``HEAD`` export.

    Dependencies are materialized with ``npm ci`` from the exported lockfile;
    neither the mutable worktree sources nor its ignored ``node_modules`` are
    used as frontend inputs.
    """

    destination = _staging_frontend_artifact_dir(expected_head)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _tracked_head_export(
        expected_head=expected_head,
        expected_tree=expected_tree,
    ) as (export_root, tracked_release):
        frontend_root = export_root / "frontend"
        package_lock = frontend_root / "package-lock.json"
        if not (frontend_root / "package.json").is_file() or not package_lock.is_file():
            raise StagingCutoverError("tracked_frontend_manifest_missing")
        frontend_source = _directory_content_evidence(frontend_root)
        env, safe_config = _frontend_build_environment()
        # The host cache may be relocated or unavailable and is not a tracked
        # build input.  Use a release-temporary cache beside the exported tree
        # so npm never depends on (or mutates) /root/.npm.
        npm_cache = export_root / ".npm-cache"
        npm_cache.mkdir(mode=0o700)
        env["NPM_CONFIG_CACHE"] = str(npm_cache)
        safe_config["isolated_npm_cache"] = True
        install = _run_contained(
            [
                "npm",
                "--prefix",
                str(frontend_root),
                "ci",
                "--no-audit",
                "--no-fund",
            ],
            env=env,
            timeout=1800,
            text=True,
        )
        if install.returncode != 0:
            raise StagingCutoverError("tracked_frontend_dependency_install_failed")
        # Vite may not clean an arbitrary pre-existing outDir reliably.  Build
        # beside the final release directory and publish only after success so
        # a failed or interrupted build cannot be mistaken for HEAD evidence.
        with tempfile.TemporaryDirectory(
            prefix=f".{expected_head}.build-",
            dir=str(destination.parent),
        ) as build_directory:
            staged = Path(build_directory) / "dist"
            env["FRONTEND_BUILD_OUT_DIR"] = str(staged)
            built = _run_contained(
                ["npm", "--prefix", str(frontend_root), "run", "build"],
                env=env,
                timeout=1800,
                text=True,
            )
            if built.returncode != 0:
                raise StagingCutoverError("staging_frontend_build_failed")
            evidence = _publish_staging_frontend_artifact(staged, destination)
        return {
            "status": "built",
            "release_sha": expected_head,
            "release_tree": expected_tree,
            "runtime_source_sha256": tracked_release["runtime_source_sha256"],
            "runtime_source_file_count": tracked_release[
                "runtime_source_file_count"
            ],
            "frontend_source_sha256": frontend_source["sha256"],
            "frontend_source_file_count": frontend_source["file_count"],
            "frontend_lock_sha256": _sha256_file(package_lock),
            "frontend_sha256": evidence["sha256"],
            "frontend_file_count": evidence["file_count"],
            "build_config": safe_config,
            "source": "tracked-head-archive",
            "worktree_frontend_used": False,
        }


def _deploy_foreign(
    release_sha: str,
    *,
    skip_frontend_build: bool = False,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "STAGING_ENABLE_BOT": "1",
            "STAGING_FOREIGN_ONLY": "1",
            "STAGING_FOREIGN_PUBLIC_SURFACE_GUARD": "1",
            "STAGING_RELEASE_SHA": release_sha,
            "STAGING_FRONTEND_DIST_DIR": "mini_app_dist_staging",
            "STAGING_SKIP_FRONTEND_BUILD": "1" if skip_frontend_build else "0",
        }
    )
    result = _run_contained(
        ["bash", str(REPO_ROOT / "scripts/deploy_staging.sh"), "deploy"],
        env=env,
        timeout=2400,
        text=True,
    )
    if result.returncode != 0:
        raise StagingCutoverError("foreign_staging_deploy_failed")
    return {"status": "deployed", "role": "foreign", "release_sha": release_sha}


def _staging_image_ref(release_sha: str) -> str:
    if len(release_sha) != 40 or any(
        character not in "0123456789abcdef" for character in release_sha
    ):
        raise StagingCutoverError("staging_image_release_sha_invalid")
    return f"{STAGING_IMAGE_REPOSITORY}:{release_sha}"


def _prebuilt_deploy_environment(release_sha: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "STAGING_ENABLE_BOT": "1",
            "STAGING_FOREIGN_ONLY": "1",
            "STAGING_FOREIGN_PUBLIC_SURFACE_GUARD": "1",
            "STAGING_RELEASE_SHA": release_sha,
            "STAGING_IMAGE_TAG": release_sha,
            "STAGING_FRONTEND_DIST_DIR": str(
                _staging_frontend_artifact_dir(release_sha).relative_to(REPO_ROOT)
            ),
            "STAGING_SKIP_FRONTEND_BUILD": "1",
        }
    )
    return env


def _build_prebuilt_foreign_image(release_sha: str) -> dict[str, Any]:
    image_ref = _staging_image_ref(release_sha)
    result = _run_contained(
        ["bash", str(REPO_ROOT / "scripts/deploy_staging.sh"), "build-image"],
        env=_prebuilt_deploy_environment(release_sha),
        timeout=3600,
        text=True,
    )
    if result.returncode != 0:
        raise StagingCutoverError("foreign_staging_image_build_failed")
    return {
        "status": "built_without_start",
        "role": "foreign",
        "image_ref": image_ref,
        "release_sha": release_sha,
        "runtime_started": False,
    }


def _image_release_evidence(
    role: str,
    image_ref: str,
    *,
    expected_head: str,
    expected_tree: str,
    expected_source_digest: str,
    expected_frontend_digest: str,
) -> dict[str, Any]:
    if role not in {"foreign", "iran"} or image_ref != _staging_image_ref(
        expected_head
    ):
        raise StagingCutoverError("prebuilt_image_role_invalid")
    inspected = _role_run(
        role,
        ["docker", "image", "inspect", "-f", "{{.Id}}", image_ref],
        timeout=120,
    )
    image_id = (inspected.stdout or "").strip()
    if (
        inspected.returncode != 0
        or not image_id.startswith("sha256:")
        or len(image_id) != 71
        or any(character not in "0123456789abcdef" for character in image_id[7:])
    ):
        raise StagingCutoverError("prebuilt_image_identity_invalid")
    measured = _role_run(
        role,
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--entrypoint",
            "python",
            image_ref,
            "-c",
            _CONTAINER_CONTENT_EVIDENCE_SCRIPT,
        ],
        timeout=300,
    )
    if measured.returncode != 0:
        raise StagingCutoverError("prebuilt_image_content_evidence_failed")
    try:
        content = json.loads((measured.stdout or "").strip())
    except (TypeError, ValueError):
        raise StagingCutoverError(
            "prebuilt_image_content_evidence_failed"
        ) from None
    if (
        not isinstance(content, dict)
        or content.get("runtime_source_sha256") != expected_source_digest
        or content.get("frontend_sha256") != expected_frontend_digest
        or int(content.get("runtime_source_file_count") or 0) <= 0
        or int(content.get("frontend_file_count") or 0) <= 0
        or len(str(content.get("dependency_sha256") or "")) != 64
    ):
        raise StagingCutoverError("prebuilt_image_content_binding_mismatch")
    return {
        "status": "verified_without_start",
        "role": role,
        "git_head": expected_head,
        "git_tree": expected_tree,
        "image_ref": image_ref,
        "image_id_sha256": image_id[7:],
        "runtime_source_sha256": expected_source_digest,
        "runtime_source_file_count": int(content["runtime_source_file_count"]),
        "frontend_sha256": expected_frontend_digest,
        "frontend_file_count": int(content["frontend_file_count"]),
        "dependency_sha256": str(content["dependency_sha256"]),
        "runtime_started": False,
        "secret_values_disclosed": False,
    }


def _remote_image_import_script(
    *,
    transfer_root: str,
    expected_sha256: str,
    release_sha: str,
    required_owner_uid: int = 0,
) -> str:
    """Render the fail-closed remote receiver for a prebuilt image archive.

    The archive is streamed over the SSH channel into a random file below a
    root-controlled directory.  The receiver validates directory/file
    ownership, modes, link state, and the archive digest before Docker can
    inspect or load the bytes.  Tests may substitute their own UID and root;
    the staging call always requires UID 0 and the fixed root-owned path.
    """

    root_path = Path(transfer_root)
    if (
        not transfer_root
        or not root_path.is_absolute()
        or str(root_path) != transfer_root
        or transfer_root == "/"
        or any(character in transfer_root for character in ("\x00", "\n", "\r"))
    ):
        raise StagingCutoverError("prebuilt_image_transfer_root_invalid")
    if (
        len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise StagingCutoverError("prebuilt_image_transfer_digest_invalid")
    if (
        len(release_sha) != 40
        or any(character not in "0123456789abcdef" for character in release_sha)
    ):
        raise StagingCutoverError("prebuilt_image_transfer_release_invalid")
    if (
        isinstance(required_owner_uid, bool)
        or not isinstance(required_owner_uid, int)
        or required_owner_uid < 0
    ):
        raise StagingCutoverError("prebuilt_image_transfer_owner_invalid")

    root = shlex.quote(transfer_root)
    digest = shlex.quote(expected_sha256)
    release = shlex.quote(release_sha)
    owner = str(required_owner_uid)
    return "\n".join(
        (
            "set -eu",
            "umask 077",
            f"transfer_root={root}",
            f"expected_sha256={digest}",
            f"release_sha={release}",
            f"required_owner_uid={owner}",
            'archive=""',
            "cleanup_archive() {",
            '  if [ -n "$archive" ]; then rm -f -- "$archive"; fi',
            "}",
            "trap cleanup_archive EXIT",
            "trap 'exit 129' HUP",
            "trap 'exit 130' INT",
            "trap 'exit 143' TERM",
            '[ "$(id -u)" -eq "$required_owner_uid" ] || exit 70',
            'transfer_parent=${transfer_root%/*}',
            '[ -n "$transfer_parent" ] || transfer_parent=/',
            '[ -d "$transfer_parent" ] || exit 71',
            '[ ! -L "$transfer_parent" ] || exit 72',
            'parent_canonical=$(readlink -f -- "$transfer_parent")',
            '[ "$parent_canonical" = "$transfer_parent" ] || exit 73',
            'parent_owner=$(stat -c %u -- "$transfer_parent")',
            '[ "$parent_owner" -eq "$required_owner_uid" ] || exit 74',
            'parent_mode=$(stat -c %a -- "$transfer_parent")',
            "case $parent_mode in ''|*[!0-7]*) exit 75 ;; esac",
            '[ "$((0$parent_mode & 022))" -eq 0 ] || exit 76',
            'if [ -L "$transfer_root" ]; then exit 77; fi',
            'if [ -e "$transfer_root" ]; then',
            '  [ -d "$transfer_root" ] || exit 78',
            "else",
            '  mkdir -- "$transfer_root" || exit 79',
            "fi",
            'root_canonical=$(readlink -f -- "$transfer_root")',
            '[ "$root_canonical" = "$transfer_root" ] || exit 80',
            '[ ! -L "$transfer_root" ] || exit 81',
            'root_owner=$(stat -c %u -- "$transfer_root")',
            '[ "$root_owner" -eq "$required_owner_uid" ] || exit 82',
            'chmod 0700 -- "$transfer_root"',
            'root_mode=$(stat -c %a -- "$transfer_root")',
            '[ "$root_mode" = 700 ] || exit 83',
            'archive=$(mktemp -- "$transfer_root/image-$release_sha.XXXXXXXXXX.tar")',
            'chmod 0600 -- "$archive"',
            'cat > "$archive"',
            '[ -f "$archive" ] || exit 84',
            '[ ! -L "$archive" ] || exit 85',
            'archive_parent=$(dirname -- "$archive")',
            '[ "$archive_parent" = "$transfer_root" ] || exit 86',
            'archive_owner=$(stat -c %u -- "$archive")',
            '[ "$archive_owner" -eq "$required_owner_uid" ] || exit 87',
            'archive_mode=$(stat -c %a -- "$archive")',
            '[ "$archive_mode" = 600 ] || exit 88',
            'archive_links=$(stat -c %h -- "$archive")',
            '[ "$archive_links" -eq 1 ] || exit 89',
            "actual_sha256=$(sha256sum -- \"$archive\" | awk '{print $1}')",
            '[ "$actual_sha256" = "$expected_sha256" ] || exit 90',
            'docker image load -i "$archive"',
        )
    ) + "\n"


def _transfer_prebuilt_image_to_iran(
    image_ref: str,
    *,
    release_sha: str,
) -> dict[str, Any]:
    if image_ref != _staging_image_ref(release_sha):
        raise StagingCutoverError("prebuilt_image_transfer_binding_invalid")
    with tempfile.TemporaryDirectory(prefix="staging-image-transfer-") as directory:
        archive = Path(directory) / "staging-image.tar"
        saved = _run(
            ["docker", "image", "save", "-o", str(archive), image_ref],
            timeout=1800,
        )
        if (
            saved.returncode != 0
            or not archive.is_file()
            or archive.is_symlink()
        ):
            raise StagingCutoverError("prebuilt_image_save_failed")
        archive_digest = _sha256_file(archive)
        remote_script = _remote_image_import_script(
            transfer_root=IRAN_IMAGE_TRANSFER_ROOT,
            expected_sha256=archive_digest,
            release_sha=release_sha,
        )
        remote_command = "sh -c " + shlex.quote(remote_script)
        with archive.open("rb") as archive_handle:
            loaded = _run_contained(
                [
                    "ssh",
                    "-p",
                    IRAN_SSH_PORT,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=12",
                    IRAN_SSH_HOST,
                    remote_command,
                ],
                stdin=archive_handle,
                timeout=1800,
                text=False,
            )
        if loaded.returncode != 0:
            raise StagingCutoverError("prebuilt_image_transfer_or_load_failed")
    return {
        "status": "loaded_without_start",
        "role": "iran",
        "image_ref": image_ref,
        "archive_sha256": archive_digest,
        "remote_digest_verified": True,
        "remote_temporary_file_randomized": True,
        "runtime_started": False,
        "secret_values_disclosed": False,
    }


def _assert_prebuilt_image_parity(
    foreign: Mapping[str, Any],
    iran: Mapping[str, Any],
    *,
    expected_head: str,
    expected_tree: str,
    expected_source_digest: str,
    expected_frontend_digest: str,
) -> dict[str, Any]:
    def valid_sha256(value: Any) -> bool:
        rendered = str(value or "")
        return len(rendered) == 64 and all(
            character in "0123456789abcdef" for character in rendered
        )

    image_ref = _staging_image_ref(expected_head)
    expected = {
        "git_head": expected_head,
        "git_tree": expected_tree,
        "image_ref": image_ref,
        "runtime_source_sha256": expected_source_digest,
        "frontend_sha256": expected_frontend_digest,
        "runtime_started": False,
        "secret_values_disclosed": False,
    }
    for evidence, role in ((foreign, "foreign"), (iran, "iran")):
        if evidence.get("role") != role or any(
            evidence.get(key) != value for key, value in expected.items()
        ) or not valid_sha256(evidence.get("image_id_sha256")) or not valid_sha256(
            evidence.get("dependency_sha256")
        ):
            raise StagingCutoverError("prebuilt_image_parity_invalid")
    for key in (
        "runtime_source_sha256",
        "frontend_sha256",
        "dependency_sha256",
    ):
        if foreign.get(key) != iran.get(key):
            raise StagingCutoverError("prebuilt_image_content_split")
    return {
        "status": "verified_before_runtime_start",
        "git_head": expected_head,
        "git_tree": expected_tree,
        "image_ref": image_ref,
        # Docker Engine versions may canonicalize imported config metadata
        # differently.  The streamed archive digest is verified before load,
        # and runtime/source/frontend/dependency content must match exactly;
        # retain each valid host image identity instead of requiring equality.
        "foreign_image_id_sha256": foreign["image_id_sha256"],
        "iran_image_id_sha256": iran["image_id_sha256"],
        "image_identity_recorded_per_host": True,
        "image_ids_equal": (
            foreign["image_id_sha256"] == iran["image_id_sha256"]
        ),
        "runtime_source_sha256": expected_source_digest,
        "frontend_sha256": expected_frontend_digest,
        "dependency_sha256": foreign["dependency_sha256"],
        "runtime_started": False,
        "secret_values_disclosed": False,
    }


def _rsync_iran_release(
    *,
    expected_head: str,
    expected_tree: str,
    expected_frontend_digest: str | None = None,
) -> dict[str, Any]:
    """Stage the exact tracked Git release plus the separately-built UI.

    The repository worktree is deliberately never an rsync source.  That
    prevents ignored runtime data, local evidence, caches, or credentials from
    crossing the host boundary merely because they happen to exist beside the
    checkout.
    """

    ssh_shell = f"ssh -p {shlex.quote(IRAN_SSH_PORT)} -o BatchMode=yes"
    release_scoped_frontend = expected_frontend_digest is not None
    frontend_source = (
        _staging_frontend_artifact_dir(expected_head)
        if release_scoped_frontend
        else REPO_ROOT / "mini_app_dist_staging"
    )
    frontend_relative = frontend_source.relative_to(REPO_ROOT)
    frontend_evidence = _directory_content_evidence(frontend_source)
    if (
        expected_frontend_digest is not None
        and frontend_evidence["sha256"] != expected_frontend_digest
    ):
        raise StagingCutoverError("staging_frontend_artifact_drift")
    with _tracked_head_export(
        expected_head=expected_head,
        expected_tree=expected_tree,
    ) as (export_root, tracked_release):
        code = _run(
            [
                "rsync",
                "-az",
                "--delete",
                "-e",
                ssh_shell,
                *[
                    item
                    for exclude in RSYNC_EXCLUDES
                    for item in ("--exclude", exclude)
                ],
                f"{export_root}/",
                f"{IRAN_SSH_HOST}:{IRAN_WORKDIR}/",
            ],
            timeout=600,
        )
        if code.returncode != 0:
            raise StagingCutoverError("iran_rsync_failed")
        prepared = _role_run(
            "iran",
            [
                "mkdir",
                "-p",
                f"{IRAN_WORKDIR}/{frontend_relative.as_posix()}",
            ],
            timeout=60,
        )
        if prepared.returncode != 0:
            raise StagingCutoverError("iran_frontend_release_directory_failed")
        frontend = _run(
            [
                "rsync",
                "-az",
                "--delete",
                "-e",
                ssh_shell,
                f"{frontend_source}/",
                f"{IRAN_SSH_HOST}:{IRAN_WORKDIR}/{frontend_relative.as_posix()}/",
            ],
            timeout=600,
        )
        if frontend.returncode != 0:
            raise StagingCutoverError("iran_frontend_rsync_failed")
    return {
        "status": "synced",
        "role": "iran",
        "tracked_release": tracked_release,
        "frontend_source": "separately-built-staging-artifact",
        "frontend_release_scoped": release_scoped_frontend,
        "frontend_relative_path": frontend_relative.as_posix(),
        "frontend_sha256": frontend_evidence["sha256"],
        "frontend_file_count": frontend_evidence["file_count"],
        "worktree_source_used": False,
    }


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


def _start_foreign_prebuilt_producers(release_sha: str) -> dict[str, Any]:
    result = _run_contained(
        [
            "bash",
            str(REPO_ROOT / "scripts/deploy_staging.sh"),
            "start-prebuilt-producers",
        ],
        env=_prebuilt_deploy_environment(release_sha),
        timeout=1800,
        text=True,
    )
    if result.returncode != 0:
        raise StagingCutoverError("foreign_prebuilt_producer_start_failed")
    return {
        "status": "started_prebuilt",
        "role": "foreign",
        "surfaces": ["api", "sync_worker"],
        "bot_started": False,
        "release_sha": release_sha,
        "image_ref": _staging_image_ref(release_sha),
    }


def _start_iran_prebuilt_producers(release_sha: str) -> dict[str, Any]:
    frontend_relative = _staging_frontend_artifact_dir(release_sha).relative_to(
        REPO_ROOT
    )
    remote = (
        f"cd {shlex.quote(IRAN_WORKDIR)} && "
        "STAGING_DOMAIN=staging.gold-trade.ir "
        "STAGING_FRONTEND_URL=https://staging.gold-trade.ir "
        "STAGING_PROJECT_NAME=trading_bot_staging_iran "
        "STAGING_NGINX_SITE=trading-bot-staging-iran "
        "STAGING_ENABLE_BOT=0 "
        "STAGING_SKIP_FRONTEND_BUILD=1 "
        f"STAGING_FRONTEND_DIST_DIR={shlex.quote(frontend_relative.as_posix())} "
        f"STAGING_IMAGE_TAG={shlex.quote(release_sha)} "
        f"STAGING_RELEASE_SHA={shlex.quote(release_sha)} "
        "STAGING_INTERNAL_FOREIGN_SERVER_URL=https://staging.362514.ir/foreign-sync "
        "STAGING_PUBLIC_FOREIGN_SYNC_URL=https://staging.362514.ir/foreign-sync "
        "STAGING_NGINX_DEDUPLICATE=1 "
        "scripts/deploy_staging.sh start-prebuilt-producers"
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
        timeout=1800,
    )
    if result.returncode != 0:
        raise StagingCutoverError("iran_prebuilt_producer_start_failed")
    return {
        "status": "started_prebuilt",
        "role": "iran",
        "surfaces": ["api", "sync_worker"],
        "bot_started": False,
        "release_sha": release_sha,
        "image_ref": _staging_image_ref(release_sha),
    }


def _start_foreign_prebuilt_bot(release_sha: str) -> dict[str, Any]:
    result = _run_contained(
        [
            "bash",
            str(REPO_ROOT / "scripts/deploy_staging.sh"),
            "start-prebuilt-bot",
        ],
        env=_prebuilt_deploy_environment(release_sha),
        timeout=900,
        text=True,
    )
    if result.returncode != 0:
        raise StagingCutoverError("foreign_prebuilt_bot_start_failed")
    return {
        "status": "started_prebuilt",
        "role": "foreign",
        "surface": "bot",
        "release_sha": release_sha,
        "image_ref": _staging_image_ref(release_sha),
    }


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
    receipt["executor_timeline"].append(
        collect_executor_inventory(expected_owner="legacy")
    )
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
    inventory = collect_executor_inventory(expected_owner=None)
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
    inventory = collect_executor_inventory(expected_owner="queue-v1")
    receipt["executor_timeline"].append(inventory)
    if inventory["executor_count"] != 1 or inventory["execution_owner"] != "queue-v1":
        raise StagingCutoverError("queue_executor_not_unique_after_foreign_deploy")
    if inventory["legacy_workers_enabled"] or inventory["executor_overlap"]:
        raise StagingCutoverError("legacy_executor_overlap_after_foreign_deploy")
    receipt["steps"].append(
        {
            "name": "rsync_iran",
            "report": _rsync_iran_release(
                expected_head=binding["head"],
                expected_tree=binding["tree"],
            ),
        }
    )
    receipt["steps"].append({"name": "deploy_iran", "report": _deploy_iran(release_sha)})
    status = build_status()
    if status.get("executor_overlap") or status.get("iran_token_violation") or not status.get("cutover_ready"):
        raise StagingCutoverError("post_deploy_contract_not_ready")
    receipt["steps"].append({"name": "post_deploy_status", "report": status})
    if _container_running(FOREIGN_BOT_CONTAINER):
        receipt["steps"].append({"name": "health_after_cutover", "report": collect_health_summary()})
    final_inventory = collect_executor_inventory(expected_owner="queue-v1")
    receipt["executor_timeline"].append(final_inventory)
    receipt["runtime_contract"] = {
        "status": "verified",
        "exactly_one_queue_executor": (
            final_inventory["executor_count"] == 1
            and final_inventory["execution_owner"] == "queue-v1"
            and final_inventory["executor_overlap"] is False
        ),
        "zero_legacy_or_extra_staging_executors": True,
        "executor_inventory": final_inventory,
        "api_runtime": status["api_runtime"],
        "publisher_runtime": status["publisher_runtime"],
        "publisher_lane_count": status["publisher_runtime"][
            "publisher_lane_count"
        ],
        "secret_values_disclosed": False,
    }
    receipt["status"] = "applied"
    receipt["finished_at"] = _utc_now()
    receipt["artifact"] = str(_write_receipt(artifact_dir, "cutover-apply", receipt))
    return receipt


def redeploy_queue_v1(
    artifact_dir: Path,
    *,
    confirm: str,
    orchestration_successor_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Redeploy an already-cut-over Queue-v1 staging pair.

    This is deliberately separate from ``apply_cutover``: it never assumes a
    Legacy starting owner, never rewrites Queue configuration, and never
    targets production.  A routine redeploy is allowed only from a clean,
    pushed ``main`` while the existing single Queue-v1 executor is healthy and
    the durable delivery surfaces have no open work.
    """

    expected_confirmation = (
        REDEPLOY_SUCCESSOR_CONFIRMATION
        if orchestration_successor_request is not None
        else REDEPLOY_CONFIRMATION
    )
    if confirm != expected_confirmation:
        raise StagingCutoverError(
            "redeploy_successor_confirmation_mismatch"
            if orchestration_successor_request is not None
            else "redeploy_confirmation_mismatch"
        )
    with _redeploy_lock():
        return _redeploy_queue_v1_locked(
            artifact_dir,
            orchestration_successor_request=orchestration_successor_request,
        )


def _redeploy_queue_v1_locked(
    artifact_dir: Path,
    *,
    orchestration_successor_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    binding = _git_binding()
    _require_clean_pushed_main(binding)
    release_sha = binding["head"]
    release_tree = binding["tree"]
    prior_recovery = _redeploy_recovery_state(
        expected_head=release_sha,
        orchestration_successor_request=orchestration_successor_request,
    )
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "command": "redeploy",
        "environment": "staging",
        "production_authorized": False,
        "started_at": _utc_now(),
        "git": binding,
        "status": "preparing",
        # Never overwrite an interrupted mutation journal with a seemingly
        # pristine precheck record.  A second interruption before containment
        # must still resume the exact-SHA fail-closed recovery path.
        "mutation_started": bool(prior_recovery.get("mutation_started")),
        "runtime_mutation_started": bool(
            prior_recovery.get("runtime_mutation_started")
        ),
        "recovery": {"required": False},
        "orchestration_successor": prior_recovery.get(
            "orchestration_successor"
        ),
        "prior_recovery": prior_recovery,
        "steps": [],
    }
    stopped_runtime: list[str] = []
    _write_redeploy_journal(receipt, phase="prechecking")
    try:
        recovering_mutated_runtime = bool(
            prior_recovery.get("mode")
            in {"recover_exact_sha", "recover_safe_orchestration_successor"}
            and prior_recovery.get("runtime_mutation_started")
        )
        if recovering_mutated_runtime:
            receipt["mutation_started"] = True
            receipt["runtime_mutation_started"] = True
            containment = _fail_closed_redeploy_runtime()
            containment_complete = all(
                event.get("action") in {"stop", "already_stopped"}
                and event.get("running") is False
                for event in containment
            ) and len(containment) == len(REDEPLOY_RUNTIME_CONTAINERS)
            if not containment_complete:
                raise StagingCutoverError("redeploy_recovery_containment_failed")
            _assert_redeploy_runtime_quiesced()
            before_inventory = collect_executor_inventory(expected_owner=None)
            if before_inventory["executor_count"] != 0:
                raise StagingCutoverError("redeploy_recovery_zero_executor_unproven")
            receipt["steps"].append(
                {
                    "name": "recover_exact_sha_containment",
                    "events": containment,
                    "executor": before_inventory,
                }
            )
        else:
            _assert_redeploy_runtime_running()
            before_inventory = collect_executor_inventory(expected_owner="queue-v1")
            if (
                before_inventory["executor_count"] != 1
                or before_inventory["execution_owner"] != "queue-v1"
                or before_inventory["executor_overlap"]
                or before_inventory["legacy_workers_enabled"]
            ):
                raise StagingCutoverError("redeploy_queue_executor_not_ready")
            before_health = collect_health_summary()
            if _health_decision(before_health) != "continue":
                raise StagingCutoverError("redeploy_health_not_ready")
            receipt["steps"].extend(
                (
                    {"name": "pre_deploy_executor", "report": before_inventory},
                    {"name": "pre_deploy_health", "report": before_health},
                )
            )

        frontend_build = _build_staging_frontend(
            expected_head=release_sha,
            expected_tree=release_tree,
        )
        frontend_digest = str(frontend_build.get("frontend_sha256") or "")
        source_digest = str(frontend_build.get("runtime_source_sha256") or "")
        if (
            len(frontend_digest) != 64
            or len(source_digest) != 64
            or frontend_build.get("source") != "tracked-head-archive"
            or frontend_build.get("worktree_frontend_used") is not False
        ):
            raise StagingCutoverError("staging_frontend_build_evidence_invalid")
        receipt["steps"].append(
            {"name": "build_staging_frontend", "report": frontend_build}
        )

        image_build = _build_prebuilt_foreign_image(release_sha)
        image_ref = str(image_build.get("image_ref") or "")
        foreign_image = _image_release_evidence(
            "foreign",
            image_ref,
            expected_head=release_sha,
            expected_tree=release_tree,
            expected_source_digest=source_digest,
            expected_frontend_digest=frontend_digest,
        )
        image_transfer = _transfer_prebuilt_image_to_iran(
            image_ref,
            release_sha=release_sha,
        )
        iran_image = _image_release_evidence(
            "iran",
            image_ref,
            expected_head=release_sha,
            expected_tree=release_tree,
            expected_source_digest=source_digest,
            expected_frontend_digest=frontend_digest,
        )
        image_parity = _assert_prebuilt_image_parity(
            foreign_image,
            iran_image,
            expected_head=release_sha,
            expected_tree=release_tree,
            expected_source_digest=source_digest,
            expected_frontend_digest=frontend_digest,
        )
        _assert_git_binding_unchanged(binding)
        receipt["steps"].append(
            {
                "name": "prebuilt_image_pair",
                "build": image_build,
                "transfer": image_transfer,
                "foreign": foreign_image,
                "iran": iran_image,
                "parity": image_parity,
            }
        )
        _write_redeploy_journal(receipt, phase="prebuilt_image_pair_verified")

        # Quiescing is the first runtime mutation.  Persist both mutation
        # markers durably before the first stop so a SIGKILL in the stop
        # window is recovered as an exact-SHA, fail-closed continuation.
        receipt["mutation_started"] = True
        receipt["runtime_mutation_started"] = True
        _write_redeploy_journal(receipt, phase="quiescing_ingress")
        if not recovering_mutated_runtime:
            stopped_runtime = _quiesce_redeploy_runtime()
            if set(stopped_runtime) != set(REDEPLOY_RUNTIME_CONTAINERS):
                raise StagingCutoverError("redeploy_runtime_set_not_fully_quiesced")
            receipt["steps"].append(
                {"name": "quiesce_runtime", "stopped": stopped_runtime}
            )
        _assert_redeploy_runtime_quiesced()
        zero_inventory = collect_executor_inventory(expected_owner=None)
        if zero_inventory["executor_count"] != 0 or zero_inventory["bot_running"]:
            raise StagingCutoverError("redeploy_zero_executor_unproven")
        foreign_queue_snapshot = snapshot_queue_aggregates(FOREIGN_DB_CONTAINER)
        iran_queue_snapshot = snapshot_queue_aggregates(IRAN_DB_CONTAINER)
        _assert_quiesced_snapshot(foreign_queue_snapshot)
        _assert_quiesced_snapshot(iran_queue_snapshot)
        receipt["steps"].extend(
            (
                {"name": "quiesced_executor", "report": zero_inventory},
                {
                    "name": "quiesced_queue_snapshots",
                    "foreign": foreign_queue_snapshot,
                    "iran": iran_queue_snapshot,
                },
            )
        )
        _write_redeploy_journal(receipt, phase="ingress_quiesced")

        sync_report = _rsync_iran_release(
            expected_head=release_sha,
            expected_tree=release_tree,
            expected_frontend_digest=frontend_digest,
        )
        receipt["steps"].append({"name": "rsync_iran", "report": sync_report})
        tracked_release = sync_report.get("tracked_release") or {}
        if (
            tracked_release.get("git_head") != release_sha
            or tracked_release.get("git_tree") != release_tree
            or tracked_release.get("runtime_source_sha256") != source_digest
            or sync_report.get("frontend_sha256") != frontend_digest
            or sync_report.get("frontend_release_scoped") is not True
            or sync_report.get("worktree_source_used") is not False
        ):
            raise StagingCutoverError("redeploy_tracked_release_not_ready")
        _write_redeploy_journal(receipt, phase="iran_payload_staged")

        # The exact image has already been verified on both hosts.  Producers
        # are started from that prebuilt tag, verified, and only then is the
        # single Queue-v1 bot allowed to resume execution.
        receipt["runtime_mutation_started"] = True
        _write_redeploy_journal(receipt, phase="producer_start_started")
        receipt["steps"].append(
            {
                "name": "start_foreign_prebuilt_producers",
                "report": _start_foreign_prebuilt_producers(release_sha),
            }
        )
        receipt["steps"].append(
            {
                "name": "start_iran_prebuilt_producers",
                "report": _start_iran_prebuilt_producers(release_sha),
            }
        )
        producer_inventory = collect_executor_inventory(expected_owner=None)
        if producer_inventory["executor_count"] != 0 or producer_inventory["bot_running"]:
            raise StagingCutoverError("redeploy_bot_started_before_peer_parity")
        producer_api_runtime = collect_api_runtime_evidence(
            expected_release_sha=release_sha,
            include_iran=True,
        )
        receipt["steps"].append(
            {
                "name": "pre_bot_producer_contract",
                "executor": producer_inventory,
                "api_runtime": producer_api_runtime,
            }
        )
        _write_redeploy_journal(receipt, phase="producers_verified")

        receipt["steps"].append(
            {
                "name": "start_foreign_prebuilt_bot",
                "report": _start_foreign_prebuilt_bot(release_sha),
            }
        )

        foreign_release = _runtime_release_evidence(
            "foreign",
            (FOREIGN_BOT_CONTAINER, FOREIGN_APP_CONTAINER, FOREIGN_SYNC_CONTAINER),
            expected_head=release_sha,
            expected_tree=release_tree,
            expected_source_digest=source_digest,
            expected_frontend_digest=frontend_digest,
        )
        iran_release = _runtime_release_evidence(
            "iran",
            (IRAN_APP_CONTAINER, IRAN_SYNC_CONTAINER),
            expected_head=release_sha,
            expected_tree=release_tree,
            expected_source_digest=source_digest,
            expected_frontend_digest=frontend_digest,
        )
        parity = _assert_release_parity(
            foreign_release,
            iran_release,
            expected_head=release_sha,
            expected_tree=release_tree,
            expected_source_digest=source_digest,
        )
        receipt["steps"].append(
            {
                "name": "release_parity",
                "report": parity,
                "foreign": foreign_release,
                "iran": iran_release,
            }
        )
        _write_redeploy_journal(receipt, phase="runtime_parity_verified")

        status = build_status()
        if (
            status.get("executor_overlap")
            or status.get("iran_token_violation")
            or not status.get("cutover_ready")
        ):
            raise StagingCutoverError("redeploy_post_contract_not_ready")
        after_health = collect_health_summary()
        if _health_decision(after_health) != "continue":
            raise StagingCutoverError("redeploy_post_health_not_ready")
        final_inventory = collect_executor_inventory(expected_owner="queue-v1")
        if (
            final_inventory["executor_count"] != 1
            or final_inventory["execution_owner"] != "queue-v1"
            or final_inventory["executor_overlap"]
            or final_inventory["legacy_workers_enabled"]
        ):
            raise StagingCutoverError("redeploy_final_executor_not_ready")
        receipt["steps"].extend(
            (
                {"name": "post_deploy_status", "report": status},
                {"name": "post_deploy_health", "report": after_health},
                {"name": "post_deploy_executor", "report": final_inventory},
            )
        )
        receipt["status"] = "redeployed"
        receipt["recovery"] = {"required": False}
        receipt["finished_at"] = _utc_now()
        _write_redeploy_journal(receipt, phase="completed")
        receipt["artifact"] = str(
            _write_receipt(artifact_dir, "cutover-redeploy", receipt)
        )
        return receipt
    except BaseException as exc:
        error_code = (
            str(exc) if isinstance(exc, StagingCutoverError) else type(exc).__name__
        )
        if receipt["runtime_mutation_started"]:
            containment = _fail_closed_redeploy_runtime()
            containment_complete = all(
                event.get("action") in {"stop", "already_stopped"}
                and event.get("running") is False
                for event in containment
            ) and len(containment) == len(REDEPLOY_RUNTIME_CONTAINERS)
            receipt["status"] = "failed_forward_reconcile_required"
            receipt["recovery"] = {
                "required": True,
                "strategy": "rerun_exact_same_pushed_sha",
                "git_head": release_sha,
                "runtime_left_quiesced": containment_complete,
            }
        else:
            resume_error = (
                "redeploy_quiesce_resume_failed"
                if error_code == "redeploy_quiesce_resume_failed"
                else None
            )
            if recovering_mutated_runtime:
                containment = _fail_closed_redeploy_runtime()
                resume_error = "prior_mutated_runtime_kept_fail_closed"
            else:
                try:
                    containment = _resume_redeploy_runtime(stopped_runtime)
                except BaseException as resume_exc:
                    containment = []
                    resume_error = (
                        str(resume_exc)
                        if isinstance(resume_exc, StagingCutoverError)
                        else type(resume_exc).__name__
                    )
            receipt["status"] = "failed_before_runtime_mutation"
            receipt["recovery"] = {
                "required": bool(resume_error),
                "strategy": "inspect_prior_runtime" if resume_error else "none",
                "prior_runtime_resume_attempted": True,
                "resume_error_code": resume_error,
            }
        receipt["steps"].append(
            {"name": "failure_containment", "events": containment}
        )
        receipt["error_code"] = error_code
        receipt["finished_at"] = _utc_now()
        try:
            _write_redeploy_journal(receipt, phase="failed")
            _write_receipt(artifact_dir, "cutover-redeploy-failure", receipt)
        except BaseException:
            pass
        raise


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
        "executor_timeline": [
            collect_executor_inventory(expected_owner="legacy")
        ],
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
    inventory = collect_executor_inventory(expected_owner=None)
    receipt["executor_timeline"].append(inventory)
    if inventory["executor_count"] != 0:
        raise StagingCutoverError("rehearse_zero_executor_unproven")
    receipt["steps"].append({"name": "start_legacy_bot", "event": _start_container(FOREIGN_BOT_CONTAINER)})
    receipt["steps"].append({"name": "resume_producers", "events": _resume_producers()})
    inventory = collect_executor_inventory(expected_owner="legacy")
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
        "executor_timeline": [
            collect_executor_inventory(expected_owner="queue-v1")
        ],
    }
    receipt["steps"].append({"name": "quiesce_producers", "events": _quiesce_producers()})
    if _container_running(FOREIGN_BOT_CONTAINER):
        receipt["steps"].append({"name": "stop_queue_bot", "event": _stop_container(FOREIGN_BOT_CONTAINER)})
    inventory = collect_executor_inventory(expected_owner=None)
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
    receipt["steps"].append(
        {
            "name": "rsync_iran",
            "report": _rsync_iran_release(
                expected_head=binding["head"],
                expected_tree=binding["tree"],
            ),
        }
    )
    receipt["steps"].append({"name": "deploy_iran_legacy", "report": _deploy_iran(release_sha)})
    inventory = collect_executor_inventory(expected_owner="legacy")
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
            "redeploy",
            "redeploy-successor",
            "rehearse-rollback",
            "rollback",
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--dump")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--skip-deploy", action="store_true")
    parser.add_argument("--prior-head", default="")
    parser.add_argument("--prior-journal-sha256", default="")
    parser.add_argument("--failure-receipt", type=Path)
    parser.add_argument("--failure-receipt-sha256", default="")
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
        elif args.command == "redeploy":
            if args.skip_deploy:
                raise StagingCutoverError("redeploy_skip_deploy_forbidden")
            payload = redeploy_queue_v1(
                args.artifact_dir,
                confirm=args.confirm,
            )
        elif args.command == "redeploy-successor":
            if args.skip_deploy:
                raise StagingCutoverError("redeploy_skip_deploy_forbidden")
            if args.failure_receipt is None:
                raise StagingCutoverError(
                    "redeploy_successor_failure_receipt_required"
                )
            payload = redeploy_queue_v1(
                args.artifact_dir,
                confirm=args.confirm,
                orchestration_successor_request={
                    "artifact_dir": args.artifact_dir,
                    "prior_head": args.prior_head,
                    "prior_journal_sha256": args.prior_journal_sha256,
                    "failure_receipt": args.failure_receipt,
                    "failure_receipt_sha256": args.failure_receipt_sha256,
                },
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
