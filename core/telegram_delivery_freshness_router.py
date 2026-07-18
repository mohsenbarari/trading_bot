"""Fail-closed action routing for authoritative Telegram freshness checks."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.telegram_delivery_queue_service import (
    CHANNEL_EDITOR_ACTIONS,
    SUPPORTED_TELEGRAM_BOT_IDENTITIES,
    TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY,
    TELEGRAM_PRIMARY_BOT_IDENTITY,
)
from core.telegram_delivery_market_freshness import MARKET_NOTICE_FRESHNESS_ACTIONS
from core.telegram_delivery_admin_broadcast_freshness import (
    ADMIN_BROADCAST_FRESHNESS_ACTIONS,
)
from core.telegram_delivery_offer_freshness import OFFER_FRESHNESS_ACTIONS
from core.telegram_delivery_trade_freshness import TRADE_RESULT_FRESHNESS_ACTIONS
from core.telegram_delivery_queue_contract import (
    NON_DURABLE_TELEGRAM_QUEUE_ACTIONS,
    TelegramDeliveryAction,
    TelegramFreshnessDecision,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord


class TelegramDeliveryFreshnessValidator(Protocol):
    def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> Awaitable[TelegramFreshnessDecision]: ...


TelegramFreshnessValidatorCallable = Callable[
    [AsyncSession, TelegramDeliveryJobRecord, datetime],
    Awaitable[TelegramFreshnessDecision],
]

DURABLE_TELEGRAM_DELIVERY_ACTIONS = frozenset(
    set(TelegramDeliveryAction) - set(NON_DURABLE_TELEGRAM_QUEUE_ACTIONS)
)


class TelegramDeliveryFreshnessRoutingError(RuntimeError):
    """Raised before dispatch when freshness coverage or routing is unsafe."""


_ROUTER_CONSTRUCTION_TOKEN = object()


def _action_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _normalize_action(value: Any) -> TelegramDeliveryAction:
    try:
        return TelegramDeliveryAction(_action_value(value))
    except ValueError as exc:
        raise TelegramDeliveryFreshnessRoutingError(
            "telegram_freshness_action_invalid"
        ) from exc


def _normalize_bot_identity(value: Any) -> str:
    identity = str(value or "").strip()
    if identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
        raise TelegramDeliveryFreshnessRoutingError(
            "telegram_freshness_bot_identity_invalid"
        )
    return identity


def required_freshness_actions_for_lane(
    bot_identity: str,
) -> frozenset[TelegramDeliveryAction]:
    identity = _normalize_bot_identity(bot_identity)
    if identity == TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY:
        return frozenset(CHANNEL_EDITOR_ACTIONS)
    if identity == TELEGRAM_PRIMARY_BOT_IDENTITY:
        return DURABLE_TELEGRAM_DELIVERY_ACTIONS
    raise TelegramDeliveryFreshnessRoutingError(
        "telegram_freshness_bot_identity_unsupported"
    )


@dataclass(frozen=True, slots=True)
class TelegramFreshnessCoverageReport:
    bot_identity: str
    required_actions: tuple[TelegramDeliveryAction, ...]
    configured_actions: tuple[TelegramDeliveryAction, ...]
    missing_actions: tuple[TelegramDeliveryAction, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_actions


def _sorted_actions(
    actions: Collection[TelegramDeliveryAction],
) -> tuple[TelegramDeliveryAction, ...]:
    return tuple(sorted(actions, key=lambda item: item.value))


class TelegramDeliveryFreshnessRouter:
    """One lane's immutable and complete action-to-validator routing table."""

    def __init__(
        self,
        *,
        bot_identity: str,
        routes: Mapping[
            TelegramDeliveryAction,
            TelegramFreshnessValidatorCallable,
        ],
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _ROUTER_CONSTRUCTION_TOKEN:
            raise TelegramDeliveryFreshnessRoutingError(
                "telegram_freshness_router_direct_construction_forbidden"
            )
        identity = _normalize_bot_identity(bot_identity)
        required = required_freshness_actions_for_lane(identity)

        normalized_routes: dict[
            TelegramDeliveryAction,
            TelegramFreshnessValidatorCallable,
        ] = {}
        for raw_action, validator in routes.items():
            action = _normalize_action(raw_action)
            if action in NON_DURABLE_TELEGRAM_QUEUE_ACTIONS:
                raise TelegramDeliveryFreshnessRoutingError(
                    "telegram_freshness_non_durable_action_forbidden"
                )
            if not callable(validator):
                raise TelegramDeliveryFreshnessRoutingError(
                    f"telegram_freshness_validator_invalid:{action.value}"
                )
            normalized_routes[action] = validator

        if set(normalized_routes) != set(required):
            raise TelegramDeliveryFreshnessRoutingError(
                "telegram_freshness_router_coverage_mismatch"
            )
        self.bot_identity = identity
        self.required_actions = required
        self._routes = MappingProxyType(normalized_routes)

    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        if str(getattr(job, "bot_identity", "") or "").strip() != self.bot_identity:
            raise TelegramDeliveryFreshnessRoutingError(
                "telegram_freshness_job_lane_mismatch"
            )
        action = _normalize_action(getattr(job, "action_kind", None))
        validator = self._routes.get(action)
        if validator is None:
            raise TelegramDeliveryFreshnessRoutingError(
                f"telegram_freshness_validator_missing:{action.value}"
            )
        decision = await validator(db, job, now)
        if not isinstance(decision, TelegramFreshnessDecision):
            raise TelegramDeliveryFreshnessRoutingError(
                f"telegram_freshness_decision_invalid:{action.value}"
            )
        return decision


class TelegramDeliveryFreshnessRegistry:
    """Partial build-time registry that refuses incomplete production lanes."""

    def __init__(
        self,
        routes: Mapping[
            TelegramDeliveryAction,
            TelegramFreshnessValidatorCallable,
        ],
    ) -> None:
        normalized: dict[
            TelegramDeliveryAction,
            TelegramFreshnessValidatorCallable,
        ] = {}
        for raw_action, validator in routes.items():
            action = _normalize_action(raw_action)
            if action in NON_DURABLE_TELEGRAM_QUEUE_ACTIONS:
                raise TelegramDeliveryFreshnessRoutingError(
                    "telegram_freshness_non_durable_action_forbidden"
                )
            if not callable(validator):
                raise TelegramDeliveryFreshnessRoutingError(
                    f"telegram_freshness_validator_invalid:{action.value}"
                )
            normalized[action] = validator
        self._routes = MappingProxyType(normalized)

    def coverage(self, bot_identity: str) -> TelegramFreshnessCoverageReport:
        identity = _normalize_bot_identity(bot_identity)
        required = required_freshness_actions_for_lane(identity)
        configured = frozenset(action for action in required if action in self._routes)
        return TelegramFreshnessCoverageReport(
            bot_identity=identity,
            required_actions=_sorted_actions(required),
            configured_actions=_sorted_actions(configured),
            missing_actions=_sorted_actions(required - configured),
        )

    def build_lane_router(
        self,
        bot_identity: str,
    ) -> TelegramDeliveryFreshnessRouter:
        report = self.coverage(bot_identity)
        if not report.complete:
            missing = ",".join(action.value for action in report.missing_actions)
            raise TelegramDeliveryFreshnessRoutingError(
                f"telegram_freshness_lane_incomplete:{report.bot_identity}:{missing}"
            )
        routes = {action: self._routes[action] for action in report.required_actions}
        return TelegramDeliveryFreshnessRouter(
            bot_identity=report.bot_identity,
            routes=routes,
            _construction_token=_ROUTER_CONSTRUCTION_TOKEN,
        )


def offer_freshness_routes(
    validator: TelegramDeliveryFreshnessValidator,
) -> dict[TelegramDeliveryAction, TelegramDeliveryFreshnessValidatorCallable]:
    if not callable(validator):
        raise TelegramDeliveryFreshnessRoutingError(
            "telegram_offer_freshness_validator_invalid"
        )
    return {action: validator for action in OFFER_FRESHNESS_ACTIONS}


def market_freshness_routes(
    validator: TelegramDeliveryFreshnessValidator,
) -> dict[TelegramDeliveryAction, TelegramFreshnessValidatorCallable]:
    if not callable(validator):
        raise TelegramDeliveryFreshnessRoutingError(
            "telegram_market_freshness_validator_invalid"
        )
    return {action: validator for action in MARKET_NOTICE_FRESHNESS_ACTIONS}


def trade_result_freshness_routes(
    validator: TelegramDeliveryFreshnessValidator,
) -> dict[TelegramDeliveryAction, TelegramFreshnessValidatorCallable]:
    if not callable(validator):
        raise TelegramDeliveryFreshnessRoutingError(
            "telegram_trade_result_freshness_validator_invalid"
        )
    return {action: validator for action in TRADE_RESULT_FRESHNESS_ACTIONS}


def admin_broadcast_freshness_routes(
    validator: TelegramDeliveryFreshnessValidator,
) -> dict[TelegramDeliveryAction, TelegramDeliveryFreshnessValidatorCallable]:
    if not callable(validator):
        raise TelegramDeliveryFreshnessRoutingError(
            "telegram_admin_broadcast_freshness_validator_invalid"
        )
    return {action: validator for action in ADMIN_BROADCAST_FRESHNESS_ACTIONS}
