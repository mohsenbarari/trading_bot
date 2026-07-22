"""Fail-closed action routing for Telegram queue lifecycle feedback."""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from core.telegram_delivery_freshness_router import (
    TelegramDeliveryFreshnessRoutingError,
    required_freshness_actions_for_lane,
)
from core.telegram_delivery_queue_contract import (
    NON_DURABLE_TELEGRAM_QUEUE_ACTIONS,
    TelegramDeliveryAction,
    TelegramDeliveryDecision,
    TelegramFreshnessDecision,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord


class TelegramLifecycleFeedback(Protocol):
    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None: ...

    async def apply_freshness(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramFreshnessDecision,
        now: datetime,
    ) -> None: ...

    async def apply_delivery_result(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramDeliveryDecision,
        now: datetime,
    ) -> None: ...


class TelegramDeliveryLifecycleRoutingError(RuntimeError):
    """Raised before dispatch when lifecycle coverage is incomplete."""


_ROUTER_CONSTRUCTION_TOKEN = object()
_REQUIRED_METHODS = (
    "assert_dispatchable",
    "apply_freshness",
    "apply_delivery_result",
)


def _action_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _normalize_action(value: Any) -> TelegramDeliveryAction:
    try:
        return TelegramDeliveryAction(_action_value(value))
    except ValueError as exc:
        raise TelegramDeliveryLifecycleRoutingError(
            "telegram_lifecycle_action_invalid"
        ) from exc


def _validate_feedback(
    action: TelegramDeliveryAction,
    feedback: TelegramLifecycleFeedback,
) -> None:
    if not all(callable(getattr(feedback, method, None)) for method in _REQUIRED_METHODS):
        raise TelegramDeliveryLifecycleRoutingError(
            f"telegram_lifecycle_feedback_invalid:{action.value}"
        )


def _sorted_actions(
    actions: Collection[TelegramDeliveryAction],
) -> tuple[TelegramDeliveryAction, ...]:
    return tuple(sorted(actions, key=lambda item: item.value))


@dataclass(frozen=True, slots=True)
class TelegramLifecycleCoverageReport:
    bot_identity: str
    required_actions: tuple[TelegramDeliveryAction, ...]
    configured_actions: tuple[TelegramDeliveryAction, ...]
    missing_actions: tuple[TelegramDeliveryAction, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_actions


class TelegramDeliveryLifecycleRouter:
    """One lane's immutable action-to-feedback routing table."""

    def __init__(
        self,
        *,
        bot_identity: str,
        routes: Mapping[TelegramDeliveryAction, TelegramLifecycleFeedback],
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _ROUTER_CONSTRUCTION_TOKEN:
            raise TelegramDeliveryLifecycleRoutingError(
                "telegram_lifecycle_router_direct_construction_forbidden"
            )
        try:
            required = required_freshness_actions_for_lane(bot_identity)
        except TelegramDeliveryFreshnessRoutingError as exc:
            raise TelegramDeliveryLifecycleRoutingError(
                "telegram_lifecycle_bot_identity_invalid"
            ) from exc
        normalized: dict[TelegramDeliveryAction, TelegramLifecycleFeedback] = {}
        for raw_action, feedback in routes.items():
            action = _normalize_action(raw_action)
            if action in NON_DURABLE_TELEGRAM_QUEUE_ACTIONS:
                raise TelegramDeliveryLifecycleRoutingError(
                    "telegram_lifecycle_non_durable_action_forbidden"
                )
            _validate_feedback(action, feedback)
            normalized[action] = feedback
        if set(normalized) != set(required):
            raise TelegramDeliveryLifecycleRoutingError(
                "telegram_lifecycle_router_coverage_mismatch"
            )
        self.bot_identity = str(bot_identity)
        self.required_actions = required
        self._routes = MappingProxyType(normalized)

    def _feedback_for(self, job: TelegramDeliveryJobRecord) -> TelegramLifecycleFeedback:
        if str(getattr(job, "bot_identity", "") or "").strip() != self.bot_identity:
            raise TelegramDeliveryLifecycleRoutingError(
                "telegram_lifecycle_job_lane_mismatch"
            )
        action = _normalize_action(getattr(job, "action_kind", None))
        feedback = self._routes.get(action)
        if feedback is None:
            raise TelegramDeliveryLifecycleRoutingError(
                f"telegram_lifecycle_feedback_missing:{action.value}"
            )
        return feedback

    async def assert_dispatchable(self, db, job, now) -> None:
        await self._feedback_for(job).assert_dispatchable(db, job, now)

    async def apply_freshness(self, db, job, decision, now) -> None:
        await self._feedback_for(job).apply_freshness(db, job, decision, now)

    async def apply_delivery_result(self, db, job, decision, now) -> None:
        await self._feedback_for(job).apply_delivery_result(db, job, decision, now)


class TelegramDeliveryLifecycleRegistry:
    """Partial registry that refuses to build an incomplete execution lane."""

    def __init__(
        self,
        routes: Mapping[TelegramDeliveryAction, TelegramLifecycleFeedback],
    ) -> None:
        normalized: dict[TelegramDeliveryAction, TelegramLifecycleFeedback] = {}
        for raw_action, feedback in routes.items():
            action = _normalize_action(raw_action)
            if action in NON_DURABLE_TELEGRAM_QUEUE_ACTIONS:
                raise TelegramDeliveryLifecycleRoutingError(
                    "telegram_lifecycle_non_durable_action_forbidden"
                )
            _validate_feedback(action, feedback)
            normalized[action] = feedback
        self._routes = MappingProxyType(normalized)

    def coverage(self, bot_identity: str) -> TelegramLifecycleCoverageReport:
        try:
            required = required_freshness_actions_for_lane(bot_identity)
        except TelegramDeliveryFreshnessRoutingError as exc:
            raise TelegramDeliveryLifecycleRoutingError(
                "telegram_lifecycle_bot_identity_invalid"
            ) from exc
        configured = frozenset(action for action in required if action in self._routes)
        return TelegramLifecycleCoverageReport(
            bot_identity=str(bot_identity),
            required_actions=_sorted_actions(required),
            configured_actions=_sorted_actions(configured),
            missing_actions=_sorted_actions(required - configured),
        )

    def build_lane_router(self, bot_identity: str) -> TelegramDeliveryLifecycleRouter:
        report = self.coverage(bot_identity)
        if not report.complete:
            missing = ",".join(action.value for action in report.missing_actions)
            raise TelegramDeliveryLifecycleRoutingError(
                f"telegram_lifecycle_lane_incomplete:{report.bot_identity}:{missing}"
            )
        routes = {action: self._routes[action] for action in report.required_actions}
        return TelegramDeliveryLifecycleRouter(
            bot_identity=report.bot_identity,
            routes=routes,
            _construction_token=_ROUTER_CONSTRUCTION_TOKEN,
        )


def lifecycle_routes(
    actions: Collection[TelegramDeliveryAction],
    feedback: TelegramLifecycleFeedback,
) -> dict[TelegramDeliveryAction, TelegramLifecycleFeedback]:
    routes: dict[TelegramDeliveryAction, TelegramLifecycleFeedback] = {}
    for raw_action in actions:
        action = _normalize_action(raw_action)
        _validate_feedback(action, feedback)
        routes[action] = feedback
    return routes
