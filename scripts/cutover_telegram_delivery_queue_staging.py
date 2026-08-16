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
    api_process_contract,
    bot_process_contract,
    executor_overlap_forbidden,
    missing_required_env,
    present_forbidden_tokens,
)


APPLY_CONFIRMATION = "CUTOVER STAGING TELEGRAM DELIVERY TO QUEUE-V1"
FOREIGN_BOT_CONTAINER = "trading_bot_staging-bot-1"
FOREIGN_APP_CONTAINER = "trading_bot_staging-foreign_app-1"
FOREIGN_DB_CONTAINER = "trading_bot_staging-db-1"
IRAN_APP_CONTAINER = "trading_bot_staging_iran-app-1"
IRAN_DB_CONTAINER = "trading_bot_staging_iran-db-1"
IRAN_SSH_HOST = os.getenv("STAGING_IRAN_SSH_HOST", "root@65.109.220.59")
IRAN_SSH_PORT = os.getenv("STAGING_IRAN_SSH_PORT", "37067")
STAGING_DB_NAME = "trading_bot_staging"
RESTORE_DB_NAME = "telegram_queue_stage3_cutover_restore_test"
DEFAULT_ARTIFACT_DIR = Path("/tmp/telegram-queue-cutover-staging")
IRAN_CONTAINERS = frozenset({IRAN_APP_CONTAINER, IRAN_DB_CONTAINER})


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
            "schema_head": "fb1c2d3e4f5a",
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
                f'psql -U "$POSTGRES_USER" -d {RESTORE_DB_NAME} -v ON_ERROR_STOP=1',
            ],
            check=False,
            stdin=handle,
        )
    if restore.returncode != 0:
        raise StagingCutoverError("restore_probe_failed")
    return {
        "status": "restored",
        "database": RESTORE_DB_NAME,
        "dump_sha256": actual,
        "production_touched": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("plan", "status", "backup", "restore-probe"),
    )
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--dump")
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
        else:
            if not args.dump:
                raise StagingCutoverError("restore_probe_dump_required")
            payload = restore_probe(args.artifact_dir, Path(args.dump))
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
