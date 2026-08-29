"""Fail-closed split-runtime forward and rollback for staging.

This module never talks to Telegram and never prints secrets. Deploy
scripts may call it; unit tests inject a fake operator.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from core.schema_revision import CANONICAL_SCHEMA_HEAD
from core.telegram_bot_runtime_role import (
    TELEGRAM_BOT_RUNTIME_ROLE_ALL,
    TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR,
    TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
    TelegramBotRuntimeRoleError,
)
from core.telegram_bot_runtime_topology import assert_telegram_bot_deploy_topology
from core.telegram_dispatch_latency_pool import compose_pool_for_bot_role
from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES


SPLIT_START_CONFIRM = "start-telegram-split-runtime"
SPLIT_ROLLBACK_CONFIRM = "rollback-telegram-split-runtime"
FORWARD_STEPS = (
    "preflight",
    "record_topology",
    "stop_combined_all",
    "start_executor",
    "wait_executor",
    "assert_one_queue_owner",
    "assert_executor_role",
    "start_primary",
    "wait_primary",
    "assert_one_central_poller",
    "assert_primary_not_queue_owner",
    "assert_executor_not_central_poller",
    "assert_telegram_identities",
    "assert_apis_producer_only",
    "assert_release_identity",
    "assert_no_unknown_runtime",
    "success",
)
ROLLBACK_STEPS = (
    "stop_split_runtimes",
    "restore_combined_settings",
    "start_combined_all",
    "assert_one_queue_owner",
    "assert_one_central_poller",
    "assert_no_split_duplicates",
    "assert_queue_jobs_intact",
    "success",
)


class SplitCutoverError(RuntimeError):
    """Raised when forward or rollback cannot continue."""


class SplitRuntimeOperator(Protocol):
    def record_topology(self) -> dict[str, Any]: ...
    def stop_services(self, names: tuple[str, ...]) -> None: ...
    def start_service(self, name: str, *, role: str, split_enabled: bool) -> None: ...
    def wait_stable(self, name: str) -> dict[str, Any]: ...
    def queue_owner_count(self) -> int: ...
    def central_poller_count(self) -> int: ...
    def service_role(self, name: str) -> str: ...
    def service_split_enabled(self, name: str) -> bool: ...
    def service_owns_queue(self, name: str) -> bool: ...
    def service_owns_central_poller(self, name: str) -> bool: ...
    def configured_telegram_identities(self) -> tuple[str, ...]: ...
    def apis_are_producer_only(self) -> bool: ...
    def release_identity_matches(self) -> bool: ...
    def unknown_or_duplicate_runtimes(self) -> tuple[str, ...]: ...
    def crash_looping(self, name: str) -> bool: ...
    def queue_jobs_intact(self) -> bool: ...
    def schema_head(self) -> str: ...


@dataclass(slots=True)
class SplitCutoverReport:
    action: str
    ok: bool
    failed_step: str | None = None
    rollback_ok: bool | None = None
    reasons: list[str] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    topology: dict[str, Any] = field(default_factory=dict)
    jobs_preserved: bool | None = None
    schema_head: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def require_confirmation(provided: str | None, expected: str) -> None:
    if str(provided or "").strip() != expected:
        raise SplitCutoverError("telegram_split_confirmation_required")


class SplitCutoverController:
    def __init__(self, operator: SplitRuntimeOperator):
        self.operator = operator

    def forward(self) -> SplitCutoverReport:
        report = SplitCutoverReport(action="forward", ok=False)
        try:
            for step in FORWARD_STEPS:
                self._run_forward_step(step, report)
                report.completed_steps.append(step)
            report.ok = True
            return report
        except Exception as exc:
            report.failed_step = report.failed_step or (
                FORWARD_STEPS[len(report.completed_steps)]
                if len(report.completed_steps) < len(FORWARD_STEPS)
                else "unknown"
            )
            report.reasons.append(str(exc))
            rollback = self.rollback()
            report.rollback_ok = rollback.ok
            report.jobs_preserved = rollback.jobs_preserved
            if rollback.ok:
                report.reasons.append("cutover_failed_rollback_succeeded")
            else:
                report.reasons.extend(rollback.reasons)
                report.reasons.append("cutover_failed_rollback_failed")
            return report

    def rollback(self) -> SplitCutoverReport:
        report = SplitCutoverReport(action="rollback", ok=False)
        try:
            for step in ROLLBACK_STEPS:
                self._run_rollback_step(step, report)
                report.completed_steps.append(step)
            report.ok = True
            return report
        except Exception as exc:
            report.failed_step = report.failed_step or (
                ROLLBACK_STEPS[len(report.completed_steps)]
                if len(report.completed_steps) < len(ROLLBACK_STEPS)
                else "unknown"
            )
            report.reasons.append(str(exc))
            return report

    def _run_forward_step(self, step: str, report: SplitCutoverReport) -> None:
        operator = self.operator
        if step == "preflight":
            try:
                assert_telegram_bot_deploy_topology(
                    split_enabled=True,
                    bot_role=TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
                    executor_enabled=True,
                )
            except TelegramBotRuntimeRoleError as exc:
                raise SplitCutoverError(str(exc)) from exc
            if operator.schema_head() != CANONICAL_SCHEMA_HEAD:
                raise SplitCutoverError("telegram_split_schema_head_mismatch")
            return
        if step == "record_topology":
            report.topology = operator.record_topology()
            report.schema_head = operator.schema_head()
            return
        if step == "stop_combined_all":
            # A deploy can start from either the legacy combined runtime or an
            # already-split previous release.  Stop both known runtimes before
            # bringing up the new executor so a healthy old executor is not
            # mistaken for a duplicate owner.  Queue jobs are durable and are
            # explicitly verified again by the rollback/postcheck path.
            operator.stop_services(("bot", "bot_executor"))
            return
        if step == "start_executor":
            operator.start_service(
                "bot_executor",
                role=TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR,
                split_enabled=True,
            )
            return
        if step == "wait_executor":
            status = operator.wait_stable("bot_executor")
            if not status.get("running"):
                raise SplitCutoverError("telegram_split_executor_not_running")
            if operator.crash_looping("bot_executor"):
                raise SplitCutoverError("telegram_split_executor_crash_loop")
            return
        if step == "assert_one_queue_owner":
            if operator.queue_owner_count() != 1:
                raise SplitCutoverError("telegram_split_queue_owner_not_exactly_one")
            return
        if step == "assert_executor_role":
            if operator.service_role("bot_executor") != TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR:
                raise SplitCutoverError("telegram_split_executor_role_mismatch")
            if not operator.service_split_enabled("bot_executor"):
                raise SplitCutoverError("telegram_split_executor_flag_mismatch")
            if not operator.service_owns_queue("bot_executor"):
                raise SplitCutoverError("telegram_split_executor_missing_queue_owner")
            return
        if step == "start_primary":
            operator.start_service(
                "bot",
                role=TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
                split_enabled=True,
            )
            return
        if step == "wait_primary":
            status = operator.wait_stable("bot")
            if not status.get("running"):
                raise SplitCutoverError("telegram_split_primary_not_running")
            if operator.crash_looping("bot"):
                raise SplitCutoverError("telegram_split_primary_crash_loop")
            return
        if step == "assert_one_central_poller":
            if operator.central_poller_count() != 1:
                raise SplitCutoverError("telegram_split_central_poller_not_exactly_one")
            return
        if step == "assert_primary_not_queue_owner":
            if operator.service_owns_queue("bot"):
                raise SplitCutoverError("telegram_split_primary_owns_queue")
            if operator.service_role("bot") != TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY:
                raise SplitCutoverError("telegram_split_primary_role_mismatch")
            return
        if step == "assert_executor_not_central_poller":
            if operator.service_owns_central_poller("bot_executor"):
                raise SplitCutoverError("telegram_split_executor_owns_central_poller")
            return
        if step == "assert_telegram_identities":
            expected = ("primary", *TELEGRAM_PUBLISHER_IDENTITIES)
            actual = operator.configured_telegram_identities()
            if tuple(actual) != expected:
                raise SplitCutoverError("telegram_split_identity_set_mismatch")
            return
        if step == "assert_apis_producer_only":
            if not operator.apis_are_producer_only():
                raise SplitCutoverError("telegram_split_api_not_producer_only")
            return
        if step == "assert_release_identity":
            if not operator.release_identity_matches():
                raise SplitCutoverError("telegram_split_release_identity_mismatch")
            return
        if step == "assert_no_unknown_runtime":
            unknown = operator.unknown_or_duplicate_runtimes()
            if unknown:
                raise SplitCutoverError("telegram_split_unknown_or_duplicate_runtime")
            if operator.queue_owner_count() != 1 or operator.central_poller_count() != 1:
                raise SplitCutoverError("telegram_split_final_owner_count_invalid")
            return
        if step == "success":
            return
        raise SplitCutoverError(f"telegram_split_unknown_forward_step:{step}")

    def _run_rollback_step(self, step: str, report: SplitCutoverReport) -> None:
        operator = self.operator
        if step == "stop_split_runtimes":
            operator.stop_services(("bot", "bot_executor"))
            return
        if step == "restore_combined_settings":
            report.topology["rolled_back_role"] = TELEGRAM_BOT_RUNTIME_ROLE_ALL
            report.topology["rolled_back_split"] = False
            return
        if step == "start_combined_all":
            operator.start_service(
                "bot",
                role=TELEGRAM_BOT_RUNTIME_ROLE_ALL,
                split_enabled=False,
            )
            status = operator.wait_stable("bot")
            if not status.get("running"):
                raise SplitCutoverError("telegram_split_rollback_all_not_running")
            return
        if step == "assert_one_queue_owner":
            if operator.queue_owner_count() != 1:
                raise SplitCutoverError("telegram_split_rollback_queue_owner_not_exactly_one")
            return
        if step == "assert_one_central_poller":
            if operator.central_poller_count() != 1:
                raise SplitCutoverError("telegram_split_rollback_central_poller_not_exactly_one")
            return
        if step == "assert_no_split_duplicates":
            if operator.unknown_or_duplicate_runtimes():
                raise SplitCutoverError("telegram_split_rollback_duplicate_runtime")
            if operator.service_role("bot") != TELEGRAM_BOT_RUNTIME_ROLE_ALL:
                raise SplitCutoverError("telegram_split_rollback_role_not_all")
            return
        if step == "assert_queue_jobs_intact":
            intact = operator.queue_jobs_intact()
            report.jobs_preserved = intact
            if not intact:
                raise SplitCutoverError("telegram_split_rollback_jobs_not_preserved")
            return
        if step == "success":
            return
        raise SplitCutoverError(f"telegram_split_unknown_rollback_step:{step}")


def bot_pool_assignment(role: str) -> dict[str, int]:
    return compose_pool_for_bot_role(role)


@dataclass
class InMemorySplitOperator:
    """Deterministic operator for tests and dry-run. Holds no secrets."""

    topology: dict[str, Any]
    schema: str = CANONICAL_SCHEMA_HEAD
    queue_owners: int = 0
    central_pollers: int = 0
    roles: dict[str, str] = field(default_factory=dict)
    split_flags: dict[str, bool] = field(default_factory=dict)
    running: dict[str, bool] = field(default_factory=dict)
    crash_loops: set[str] = field(default_factory=set)
    owns_queue: dict[str, bool] = field(default_factory=dict)
    owns_central: dict[str, bool] = field(default_factory=dict)
    identities: tuple[str, ...] = ("primary", *TELEGRAM_PUBLISHER_IDENTITIES)
    apis_producer_only: bool = True
    release_matches: bool = True
    unknown: tuple[str, ...] = ()
    jobs_intact: bool = True
    fail_on: str | None = None
    started: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    purged_jobs: bool = False

    @classmethod
    def successful(cls) -> "InMemorySplitOperator":
        return cls(topology={"role": "all", "split_enabled": False, "release_sha": "test"})

    def record_topology(self) -> dict[str, Any]:
        self._maybe_fail("record_topology")
        return dict(self.topology)

    def stop_services(self, names: tuple[str, ...]) -> None:
        self._maybe_fail("stop_services")
        for name in names:
            self.stopped.append(name)
            self.running[name] = False
            self.owns_queue.pop(name, None)
            self.owns_central.pop(name, None)
            if name == "bot_executor":
                self.queue_owners = 0
            if name == "bot" and self.roles.get("bot") != TELEGRAM_BOT_RUNTIME_ROLE_ALL:
                self.central_pollers = 0

    def start_service(self, name: str, *, role: str, split_enabled: bool) -> None:
        self._maybe_fail(f"start_{name}")
        if name == "bot_executor" and self.running.get("bot_executor"):
            self.unknown = ("duplicate_executor",)
            raise SplitCutoverError("telegram_split_two_executors")
        if (
            name == "bot"
            and role == TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY
            and self.running.get("bot")
            and self.roles.get("bot") == TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY
        ):
            self.unknown = ("duplicate_primary",)
            raise SplitCutoverError("telegram_split_two_primaries")
        if (
            name == "bot_executor"
            and self.running.get("bot")
            and self.roles.get("bot") == TELEGRAM_BOT_RUNTIME_ROLE_ALL
        ):
            raise SplitCutoverError("telegram_bot_all_plus_executor_forbidden")
        self.started.append(name)
        self.running[name] = True
        self.roles[name] = role
        self.split_flags[name] = split_enabled
        if role == TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR:
            self.queue_owners = 1
            self.owns_queue[name] = True
            self.owns_central[name] = False
        elif role == TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY:
            self.central_pollers = 1
            self.owns_queue[name] = False
            self.owns_central[name] = True
        elif role == TELEGRAM_BOT_RUNTIME_ROLE_ALL:
            self.queue_owners = 1
            self.central_pollers = 1
            self.owns_queue[name] = True
            self.owns_central[name] = True

    def wait_stable(self, name: str) -> dict[str, Any]:
        self._maybe_fail(f"wait_{name}")
        return {
            "running": bool(self.running.get(name)),
            "restarts": 1 if name in self.crash_loops else 0,
            "health": "healthy" if self.running.get(name) else "unhealthy",
        }

    def queue_owner_count(self) -> int:
        self._maybe_fail("queue_owner_count")
        return int(self.queue_owners)

    def central_poller_count(self) -> int:
        self._maybe_fail("central_poller_count")
        return int(self.central_pollers)

    def service_role(self, name: str) -> str:
        return str(self.roles.get(name) or "")

    def service_split_enabled(self, name: str) -> bool:
        return bool(self.split_flags.get(name))

    def service_owns_queue(self, name: str) -> bool:
        return bool(self.owns_queue.get(name))

    def service_owns_central_poller(self, name: str) -> bool:
        return bool(self.owns_central.get(name))

    def configured_telegram_identities(self) -> tuple[str, ...]:
        self._maybe_fail("identities")
        return self.identities

    def apis_are_producer_only(self) -> bool:
        self._maybe_fail("apis")
        return bool(self.apis_producer_only)

    def release_identity_matches(self) -> bool:
        self._maybe_fail("release")
        return bool(self.release_matches)

    def unknown_or_duplicate_runtimes(self) -> tuple[str, ...]:
        self._maybe_fail("unknown")
        return self.unknown

    def crash_looping(self, name: str) -> bool:
        return name in self.crash_loops

    def queue_jobs_intact(self) -> bool:
        return bool(self.jobs_intact) and not self.purged_jobs

    def schema_head(self) -> str:
        self._maybe_fail("schema_head")
        return self.schema

    def _maybe_fail(self, step: str) -> None:
        if self.fail_on == step:
            self.fail_on = None
            raise SplitCutoverError(f"forced_failure:{step}")
