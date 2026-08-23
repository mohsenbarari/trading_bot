"""Compose-backed split operator for staging.

This never talks to Telegram, never prints secrets, never deletes queue
jobs, and refuses production project names. Tests inject a runner.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable

from core.schema_revision import CANONICAL_SCHEMA_HEAD
from core.telegram_bot_runtime_role import (
    TELEGRAM_BOT_RUNTIME_ROLE_ALL,
    TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR,
    TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
)
from core.telegram_bot_split_cutover import SplitCutoverError
from core.telegram_central_poller_owner import TELEGRAM_CENTRAL_POLLER_LOCK_KEY
from core.telegram_delivery_queue_owner import TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY
from core.telegram_dispatch_latency_pool import compose_pool_for_bot_role
from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES


STAGING_PROJECT_PREFIX = "trading_bot_staging"
PRODUCTION_MARKERS = ("production", "trading_bot_prod")
DEFAULT_BOT_PROFILE = "staging-bot"
DEFAULT_EXECUTOR_PROFILE = "staging-bot-executor"
JOB_COUNT_SQL = "SELECT count(*) FROM telegram_delivery_jobs"
SCHEMA_HEAD_SQL = "SELECT version_num FROM alembic_version"
KNOWN_BOT_SERVICES = ("bot", "bot_executor")
RETIRED_BOT_SERVICES = ("bot_publishers",)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _advisory_count_sql(lock_key: int) -> str:
    class_id = (int(lock_key) >> 32) & 0xFFFFFFFF
    object_id = int(lock_key) & 0xFFFFFFFF
    return (
        "SELECT count(*) FROM pg_locks "
        "WHERE locktype = 'advisory' "
        f"AND classid = {class_id} AND objid = {object_id} "
        "AND objsubid = 1 AND granted"
    )


@dataclass
class CommandResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def subprocess_runner(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> CommandResult:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=merged,
    )
    return CommandResult(
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


class ComposeSplitOperator:
    """Mutate only named bot services on a staging compose project."""

    def __init__(
        self,
        runner: Callable[..., CommandResult],
        *,
        project_name: str,
        compose_file: str,
        env_file: str,
        expected_sha: str,
        bot_profile: str = DEFAULT_BOT_PROFILE,
        executor_profile: str = DEFAULT_EXECUTOR_PROFILE,
        db_service: str = "db",
        stable_attempts: int = 20,
        sleep: Callable[[float], None] | None = None,
    ):
        name = str(project_name or "").strip()
        lowered = name.lower()
        if any(marker in lowered for marker in PRODUCTION_MARKERS):
            raise SplitCutoverError("compose_operator_refuses_production")
        if not name.startswith(STAGING_PROJECT_PREFIX):
            raise SplitCutoverError("compose_operator_requires_staging_project")
        self.runner = runner
        self.project_name = name
        self.compose_file = compose_file
        self.env_file = env_file
        self.expected_sha = str(expected_sha or "").strip()
        self.bot_profile = bot_profile
        self.executor_profile = executor_profile
        self.db_service = db_service
        self.stable_attempts = max(1, int(stable_attempts))
        self.sleep = sleep or (lambda _seconds: None)
        self.commands: list[str] = []
        self.job_count_snapshot: int | None = None
        self.purged_jobs = False

    def _run(self, args: list[str], *, env: dict[str, str] | None = None) -> CommandResult:
        self.commands.append(" ".join(args))
        joined = " ".join(args).lower()
        if "delete from telegram_delivery" in joined or "truncate telegram_delivery" in joined:
            self.purged_jobs = True
            raise SplitCutoverError("compose_operator_forbids_job_purge")
        result = self.runner(args, env=env)
        if result.returncode != 0:
            raise SplitCutoverError(f"compose_command_failed:{args[1] if len(args) > 1 else args[0]}")
        return result

    def _compose(self, *args: str, extra_env: dict[str, str] | None = None) -> CommandResult:
        compose_env = dict(extra_env or {})
        if self.expected_sha:
            # The staging Compose file selects the immutable runtime image via
            # STAGING_IMAGE_TAG, while RELEASE_SHA is only the in-container
            # identity.  Supplying just the latter can silently resolve the
            # service to the mutable ``latest`` image during a split cutover.
            compose_env.setdefault("STAGING_IMAGE_TAG", self.expected_sha)
            compose_env.setdefault("STAGING_RELEASE_SHA", self.expected_sha)
        command = [
            "docker",
            "compose",
            "-p",
            self.project_name,
            "--env-file",
            self.env_file,
            "-f",
            self.compose_file,
            # Profile-scoped services are omitted from the Compose model even
            # for read-only `ps` unless their profiles are active. Keep both
            # bot roles visible throughout inspection and mutation so the
            # operator cannot mistake an existing runtime for a missing one.
            "--profile",
            self.bot_profile,
            "--profile",
            self.executor_profile,
            *args,
        ]
        return self._run(command, env=compose_env)

    def _inspect_payload(self, name: str) -> dict[str, Any] | None:
        listed = self._compose("ps", "-a", "-q", name)
        container_id = listed.stdout.strip().splitlines()[0] if listed.stdout.strip() else ""
        if not container_id:
            return None
        raw = self._run(["docker", "inspect", container_id])
        payload = json.loads(raw.stdout)
        if isinstance(payload, list):
            if not payload:
                return None
            return payload[0]
        return payload

    def _service_container_ids_by_label(self, name: str) -> tuple[str, ...]:
        # Retired services are intentionally absent from the current Compose
        # model, so `compose ps <retired-name>` fails with "no such service".
        # Docker labels still expose orphaned containers without requiring the
        # service to exist in today's YAML.
        result = self._run(
            [
                "docker",
                "ps",
                "-a",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project_name}",
                "--filter",
                f"label=com.docker.compose.service={name}",
            ]
        )
        return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())

    def _env_map(self, payload: dict[str, Any] | None) -> dict[str, str]:
        if not payload:
            return {}
        values: dict[str, str] = {}
        for item in payload.get("Config", {}).get("Env", []) or []:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                values[key] = value
        return values

    def _service_state(self, name: str) -> dict[str, Any]:
        payload = self._inspect_payload(name)
        if payload is None:
            return {
                "running": False,
                "restarts": 0,
                "health": "missing",
                "status": "missing",
                "role": "",
                "split_enabled": False,
                "release_sha": "",
                "image": "",
            }
        state = payload.get("State") or {}
        health = ""
        if isinstance(state.get("Health"), dict):
            health = str(state["Health"].get("Status") or "")
        env = self._env_map(payload)
        return {
            "running": bool(state.get("Running")),
            "restarts": int(state.get("RestartCount") or 0),
            "health": health or str(state.get("Status") or ""),
            "status": str(state.get("Status") or ""),
            "role": str(env.get("TELEGRAM_BOT_RUNTIME_ROLE") or ""),
            "split_enabled": _truthy(env.get("TELEGRAM_BOT_SPLIT_ENABLED")),
            "release_sha": str(env.get("RELEASE_SHA") or ""),
            "image": str(payload.get("Image") or ""),
            "service": str(
                ((payload.get("Config") or {}).get("Labels") or {}).get(
                    "com.docker.compose.service"
                )
                or name
            ),
        }

    def _sql_scalar(self, sql: str) -> str:
        result = self._compose(
            "exec",
            "-T",
            self.db_service,
            "sh",
            "-c",
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"',
            "psql",
            sql,
        )
        return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""

    def record_topology(self) -> dict[str, Any]:
        bot = self._service_state("bot")
        executor = self._service_state("bot_executor")
        self.job_count_snapshot = int(self._sql_scalar(JOB_COUNT_SQL) or "0")
        return {
            "role": bot.get("role") or TELEGRAM_BOT_RUNTIME_ROLE_ALL,
            "split_enabled": bool(bot.get("split_enabled")),
            "release_sha": bot.get("release_sha") or self.expected_sha,
            "executor_running": bool(executor.get("running")),
            "job_count": self.job_count_snapshot,
        }

    def stop_services(self, names: tuple[str, ...]) -> None:
        existing = [name for name in names if self._inspect_payload(name)]
        if not existing:
            return
        self._compose("stop", *existing)
        self._compose("rm", "-f", *existing)

    def start_service(self, name: str, *, role: str, split_enabled: bool) -> None:
        bot = self._service_state("bot")
        executor = self._service_state("bot_executor")
        if name == "bot_executor" and executor["running"]:
            raise SplitCutoverError("telegram_split_two_executors")
        if (
            name == "bot"
            and role == TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY
            and bot["running"]
            and bot["role"] == TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY
        ):
            raise SplitCutoverError("telegram_split_two_primaries")
        if (
            name == "bot_executor"
            and bot["running"]
            and bot["role"] == TELEGRAM_BOT_RUNTIME_ROLE_ALL
        ):
            raise SplitCutoverError("telegram_bot_all_plus_executor_forbidden")
        assignment = compose_pool_for_bot_role(role)
        extra_env = {
            "STAGING_TELEGRAM_BOT_RUNTIME_ROLE": role,
            "STAGING_TELEGRAM_BOT_SPLIT_ENABLED": "true" if split_enabled else "false",
            "STAGING_DB_BOT_POOL_SIZE": str(assignment["db_pool_size"]),
            "STAGING_DB_BOT_MAX_OVERFLOW": str(assignment["db_max_overflow"]),
        }
        if role == TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR:
            extra_env["STAGING_DB_EXECUTOR_POOL_SIZE"] = str(assignment["db_pool_size"])
            extra_env["STAGING_DB_EXECUTOR_MAX_OVERFLOW"] = str(
                assignment["db_max_overflow"]
            )
        self._compose(
            "up",
            "-d",
            "--no-build",
            name,
            extra_env=extra_env,
        )

    def wait_stable(self, name: str) -> dict[str, Any]:
        last: dict[str, Any] = {"running": False, "restarts": 0, "health": "missing"}
        for _ in range(self.stable_attempts):
            last = self._service_state(name)
            if last["running"] and last["restarts"] < 2:
                health = str(last.get("health") or "")
                if health in {"", "healthy", "running", "starting"}:
                    role = str(last.get("role") or "")
                    owner_ready = True
                    if role == TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR:
                        owner_ready = self.queue_owner_count() == 1
                    elif role == TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY:
                        owner_ready = self.central_poller_count() == 1
                    elif role == TELEGRAM_BOT_RUNTIME_ROLE_ALL:
                        owner_ready = (
                            self.queue_owner_count() == 1
                            and self.central_poller_count() == 1
                        )
                    if health != "starting" and owner_ready:
                        return last
            self.sleep(0.5)
        return last

    def queue_owner_count(self) -> int:
        return int(self._sql_scalar(_advisory_count_sql(TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY)) or "0")

    def central_poller_count(self) -> int:
        return int(self._sql_scalar(_advisory_count_sql(TELEGRAM_CENTRAL_POLLER_LOCK_KEY)) or "0")

    def service_role(self, name: str) -> str:
        return str(self._service_state(name).get("role") or "")

    def service_split_enabled(self, name: str) -> bool:
        return bool(self._service_state(name).get("split_enabled"))

    def service_owns_queue(self, name: str) -> bool:
        return self.service_role(name) in {
            TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR,
            TELEGRAM_BOT_RUNTIME_ROLE_ALL,
        }

    def service_owns_central_poller(self, name: str) -> bool:
        return self.service_role(name) in {
            TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
            TELEGRAM_BOT_RUNTIME_ROLE_ALL,
        }

    def configured_telegram_identities(self) -> tuple[str, ...]:
        return ("primary", *TELEGRAM_PUBLISHER_IDENTITIES)

    def apis_are_producer_only(self) -> bool:
        for name in ("app", "foreign_app"):
            payload = self._inspect_payload(name)
            if payload is None:
                continue
            env = self._env_map(payload)
            service = str(env.get("TRADING_BOT_SERVICE") or "").strip().lower()
            if service == "bot":
                return False
            command = " ".join(
                str(item)
                for item in ((payload.get("Config") or {}).get("Cmd") or [])
            )
            if "run_bot.py" in command:
                return False
        return True

    def release_identity_matches(self) -> bool:
        images = set()
        for name in KNOWN_BOT_SERVICES:
            state = self._service_state(name)
            if not state["running"]:
                continue
            if self.expected_sha and state["release_sha"] not in {
                self.expected_sha,
                self.expected_sha[:12],
            }:
                return False
            if state["image"]:
                images.add(state["image"])
        return len(images) <= 1

    def unknown_or_duplicate_runtimes(self) -> tuple[str, ...]:
        unknown: list[str] = []
        for retired in RETIRED_BOT_SERVICES:
            if self._service_container_ids_by_label(retired):
                unknown.append(retired)
        roles: dict[str, int] = {}
        for name in KNOWN_BOT_SERVICES:
            state = self._service_state(name)
            if not state["running"]:
                continue
            role = state["role"]
            roles[role] = roles.get(role, 0) + 1
        if roles.get(TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR, 0) > 1:
            unknown.append("duplicate_executor")
        if roles.get(TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY, 0) > 1:
            unknown.append("duplicate_primary")
        if (
            roles.get(TELEGRAM_BOT_RUNTIME_ROLE_ALL, 0)
            and roles.get(TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR, 0)
        ):
            unknown.append("all_plus_executor")
        return tuple(unknown)

    def crash_looping(self, name: str) -> bool:
        state = self._service_state(name)
        status = str(state.get("status") or "").lower()
        return int(state.get("restarts") or 0) >= 2 or "restarting" in status

    def queue_jobs_intact(self) -> bool:
        if self.purged_jobs:
            return False
        current = int(self._sql_scalar(JOB_COUNT_SQL) or "0")
        if self.job_count_snapshot is None:
            return True
        return current == self.job_count_snapshot

    def schema_head(self) -> str:
        return self._sql_scalar(SCHEMA_HEAD_SQL) or CANONICAL_SCHEMA_HEAD
