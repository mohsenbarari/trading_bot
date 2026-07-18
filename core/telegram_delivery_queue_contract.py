"""Pure contracts for the shared Telegram delivery queue.

This module has no database, Redis, or Telegram side effects.  It is the
executable Stage 2 reference for ordering, feeder handoff, freshness, retry,
fencing, broadcast fairness, and message-shape decisions used by Stage 3.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum, IntEnum
from typing import Any, Protocol


class TelegramGatewayResultLike(Protocol):
    ok: bool
    method: str
    status_code: int | None
    response_text: str
    response_json: dict[str, Any] | None
    error: str | None

    @property
    def message_id(self) -> int | None:
        ...


class TelegramDeliveryPriority(IntEnum):
    M0 = 0
    M1 = 1
    M2 = 2
    M3 = 3
    M4 = 4
    M5 = 5
    M6 = 6
    M7 = 7


class TelegramFeederKind(str, Enum):
    OFFER_CONTROL = "offer_control"
    OFFER_EDIT = "offer_edit"
    TRADE = "trade"
    ADMIN_SYSTEM = "admin_system"
    MARKET_STATUS = "market_status"
    TIMED_BOT = "timed_bot"
    DIRECT = "direct"


class TelegramDestinationClass(str, Enum):
    PRIVATE = "private"
    CHANNEL = "channel"
    ADMIN = "admin"


class TelegramDeliveryAction(str, Enum):
    CALLBACK_DEADLINE = "callback_deadline"
    # Reserved for mixed-version database compatibility only. OTP values stay
    # on the signed, short-lived Redis transport and are forbidden at durable
    # queue enqueue boundaries.
    OTP_DEADLINE = "otp_deadline"
    OFFER_PUBLISH = "offer_publish"
    OFFER_SUCCESS = "offer_success"
    OFFER_VALIDATION_RESPONSE = "offer_validation_response"
    OFFER_EXPIRY_CALLBACK = "offer_expiry_callback"
    OFFER_REPEAT_RESPONSE = "offer_repeat_response"
    GENERAL_IMMEDIATE = "general_immediate"
    TRADE_RESULT = "trade_result"
    TRADE_RESPONSE = "trade_response"
    TRADE_ALTERNATIVE = "trade_alternative"
    TRADE_UNAVAILABLE = "trade_unavailable"
    TRADE_NONCRITICAL = "trade_noncritical"
    PARTIAL_OFFER_EDIT = "partial_offer_edit"
    TRADED_OFFER_EDIT = "traded_offer_edit"
    EXPIRED_OFFER_EDIT = "expired_offer_edit"
    CANCELLED_OFFER_EDIT = "cancelled_offer_edit"
    OTHER_ACTIVE_OFFER_EDIT = "other_active_offer_edit"
    INVALID_ACTION_BUTTON_EDIT = "invalid_action_button_edit"
    RECONCILIATION_EDIT = "reconciliation_edit"
    NEW_USER_MEMBERSHIP = "new_user_membership"
    ACCOUNT_STATUS = "account_status"
    TARGETED_ADMIN_MESSAGE = "targeted_admin_message"
    ADMIN_BROADCAST = "admin_broadcast"
    GENERAL_ANNOUNCEMENT = "general_announcement"
    MARKET_TRANSITION = "market_transition"
    MARKET_STATUS_CORRECTION = "market_status_correction"
    NONCRITICAL_MARKET = "noncritical_market"
    TIMED_SECURITY = "timed_security"
    DELAYED_RESTRICTION = "delayed_restriction"
    TEMPORARY_CLEANUP = "temporary_cleanup"
    COSMETIC_CLEANUP = "cosmetic_cleanup"


class TelegramDeliveryState(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    PENDING_RETRY = "pending_retry"
    PENDING_RECONCILE = "pending_reconcile"
    AMBIGUOUS = "ambiguous"
    AMBIGUOUS_UNRESOLVED = "ambiguous_unresolved"
    SENT = "sent"
    SENT_NOOP = "sent_noop"
    SUPERSEDED = "superseded"
    EXPIRED_INTERACTION = "expired_interaction"
    PERMANENT_UNDELIVERABLE = "permanent_undeliverable"
    TERMINAL_FAILED = "terminal_failed"
    QUARANTINED = "quarantined"
    BLOCKED_DESTINATION = "blocked_destination"
    BLOCKED_BOT = "blocked_bot"
    BLOCKED_GATEWAY = "blocked_gateway"


class TelegramDeliveryOutcome(str, Enum):
    SENT = "sent"
    SENT_NOOP = "sent_noop"
    RETRY_PENDING = "retry_pending"
    DESTINATION_PAUSED = "destination_paused"
    BOT_PAUSED = "bot_paused"
    GATEWAY_PAUSED = "gateway_paused"
    TERMINAL_FAILED = "terminal_failed"
    PERMANENT_UNDELIVERABLE = "permanent_undeliverable"
    QUARANTINED = "quarantined"
    AMBIGUOUS = "ambiguous"
    AMBIGUOUS_UNRESOLVED = "ambiguous_unresolved"
    SUPERSEDED = "superseded"
    EXPIRED_INTERACTION = "expired_interaction"
    ALREADY_RESOLVED = "already_resolved"
    STALE_LEASE = "stale_lease"


class TelegramFreshnessOutcome(str, Enum):
    SEND = "send"
    WAIT_DEPENDENCY = "wait_dependency"
    RECLASSIFY = "reclassify"
    SENT_NOOP = "sent_noop"
    SUPERSEDED = "superseded"
    EXPIRED_INTERACTION = "expired_interaction"
    QUARANTINED = "quarantined"


class TelegramOfferBusinessState(str, Enum):
    ACTIVE = "active"
    TRADED = "traded"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    NEVER_PUBLISHED = "never_published"


class TelegramFeederRecordState(str, Enum):
    READY = "ready"
    HANDED_OFF = "handed_off"
    TERMINAL = "terminal"


class TelegramFlowExit(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    CANCEL = "cancel"
    TIMEOUT = "timeout"


class TelegramDeliveryDedupeConflictError(ValueError):
    """Raised when one logical identity is reused with different content."""


class TelegramHandoffInterrupted(RuntimeError):
    """Synthetic crash boundary used to prove recoverable feeder handoff."""


FINAL_DELIVERY_STATES = {
    TelegramDeliveryState.SENT,
    TelegramDeliveryState.SENT_NOOP,
    TelegramDeliveryState.SUPERSEDED,
    TelegramDeliveryState.EXPIRED_INTERACTION,
    TelegramDeliveryState.PERMANENT_UNDELIVERABLE,
    TelegramDeliveryState.TERMINAL_FAILED,
    TelegramDeliveryState.QUARANTINED,
}
CLAIMABLE_DELIVERY_STATES = {
    TelegramDeliveryState.PENDING,
    TelegramDeliveryState.PENDING_RETRY,
}

DEFAULT_MAX_RETRY_SECONDS = 300.0
DEFAULT_DESTINATION_PAUSE_SECONDS = 300.0
MINIMUM_LEASE_MARGIN_SECONDS = 15.0
DEFAULT_RATE_LIMIT_PROBE_DELAY_SECONDS = 0.1
DEFAULT_GLOBAL_RATE_LIMIT_WINDOW_SECONDS = 2.0
EDIT_STALE_AFTER_SECONDS = 5 * 60
EDIT_CATCH_UP_FRESH_COUNT = 20
NON_DURABLE_TELEGRAM_QUEUE_ACTIONS = frozenset(
    {TelegramDeliveryAction.OTP_DEADLINE}
)

_AMBIGUOUS_SEND_ERRORS = {
    "networkerror",
    "readerror",
    "readtimeout",
    "remoteprotocolerror",
    "timeouterror",
    "writeerror",
    "writetimeout",
}
_SAFE_RETRY_TRANSPORT_ERRORS = {"connecterror", "connecttimeout", "pooltimeout"}
_MALFORMED_PAYLOAD_PATTERNS = (
    "can't parse entities",
    "message is too long",
    "message text is empty",
    "parse mode",
    "text must be non-empty",
)
_MESSAGE_NOT_FOUND_PATTERNS = (
    "message to edit not found",
    "message can't be edited",
    "message cannot be edited",
)
_CALLBACK_EXPIRED_PATTERNS = (
    "query is too old",
    "response timeout expired",
    "query id is invalid",
)


_ACTION_PRIORITY_AND_RANK: dict[TelegramDeliveryAction, tuple[TelegramDeliveryPriority, int]] = {
    TelegramDeliveryAction.CALLBACK_DEADLINE: (TelegramDeliveryPriority.M0, 0),
    TelegramDeliveryAction.OTP_DEADLINE: (TelegramDeliveryPriority.M0, 0),
    TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK: (TelegramDeliveryPriority.M0, 0),
    TelegramDeliveryAction.OFFER_PUBLISH: (TelegramDeliveryPriority.M0, 2),
    TelegramDeliveryAction.OFFER_SUCCESS: (TelegramDeliveryPriority.M1, 0),
    TelegramDeliveryAction.TRADE_RESULT: (TelegramDeliveryPriority.M1, 1),
    TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE: (TelegramDeliveryPriority.M1, 2),
    TelegramDeliveryAction.OFFER_REPEAT_RESPONSE: (TelegramDeliveryPriority.M1, 2),
    TelegramDeliveryAction.TRADE_RESPONSE: (TelegramDeliveryPriority.M1, 3),
    TelegramDeliveryAction.TRADE_ALTERNATIVE: (TelegramDeliveryPriority.M1, 3),
    TelegramDeliveryAction.TRADE_UNAVAILABLE: (TelegramDeliveryPriority.M1, 3),
    TelegramDeliveryAction.GENERAL_IMMEDIATE: (TelegramDeliveryPriority.M1, 4),
    TelegramDeliveryAction.PARTIAL_OFFER_EDIT: (TelegramDeliveryPriority.M2, 0),
    TelegramDeliveryAction.NEW_USER_MEMBERSHIP: (TelegramDeliveryPriority.M2, 1),
    TelegramDeliveryAction.TRADED_OFFER_EDIT: (TelegramDeliveryPriority.M3, 0),
    TelegramDeliveryAction.MARKET_TRANSITION: (TelegramDeliveryPriority.M4, 0),
    TelegramDeliveryAction.MARKET_STATUS_CORRECTION: (TelegramDeliveryPriority.M4, 0),
    TelegramDeliveryAction.EXPIRED_OFFER_EDIT: (TelegramDeliveryPriority.M4, 1),
    TelegramDeliveryAction.CANCELLED_OFFER_EDIT: (TelegramDeliveryPriority.M4, 2),
    TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT: (TelegramDeliveryPriority.M4, 3),
    TelegramDeliveryAction.ACCOUNT_STATUS: (TelegramDeliveryPriority.M5, 0),
    TelegramDeliveryAction.TIMED_SECURITY: (TelegramDeliveryPriority.M5, 0),
    TelegramDeliveryAction.DELAYED_RESTRICTION: (TelegramDeliveryPriority.M5, 0),
    TelegramDeliveryAction.TARGETED_ADMIN_MESSAGE: (TelegramDeliveryPriority.M5, 1),
    TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT: (TelegramDeliveryPriority.M5, 2),
    TelegramDeliveryAction.TRADE_NONCRITICAL: (TelegramDeliveryPriority.M5, 3),
    TelegramDeliveryAction.ADMIN_BROADCAST: (TelegramDeliveryPriority.M6, 0),
    TelegramDeliveryAction.GENERAL_ANNOUNCEMENT: (TelegramDeliveryPriority.M6, 1),
    TelegramDeliveryAction.NONCRITICAL_MARKET: (TelegramDeliveryPriority.M6, 2),
    TelegramDeliveryAction.RECONCILIATION_EDIT: (TelegramDeliveryPriority.M6, 3),
    TelegramDeliveryAction.TEMPORARY_CLEANUP: (TelegramDeliveryPriority.M7, 0),
    TelegramDeliveryAction.COSMETIC_CLEANUP: (TelegramDeliveryPriority.M7, 1),
}

_FEEDER_INTERNAL_RANK: dict[
    tuple[TelegramFeederKind, TelegramDeliveryAction], int
] = {
    (TelegramFeederKind.OFFER_CONTROL, TelegramDeliveryAction.OFFER_PUBLISH): 0,
    (TelegramFeederKind.OFFER_CONTROL, TelegramDeliveryAction.OFFER_SUCCESS): 1,
    (TelegramFeederKind.OFFER_CONTROL, TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE): 2,
    (TelegramFeederKind.OFFER_CONTROL, TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK): 3,
    (TelegramFeederKind.OFFER_CONTROL, TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT): 4,
    (TelegramFeederKind.OFFER_CONTROL, TelegramDeliveryAction.OFFER_REPEAT_RESPONSE): 5,
    (TelegramFeederKind.OFFER_CONTROL, TelegramDeliveryAction.RECONCILIATION_EDIT): 6,
    (TelegramFeederKind.OFFER_EDIT, TelegramDeliveryAction.PARTIAL_OFFER_EDIT): 0,
    (TelegramFeederKind.OFFER_EDIT, TelegramDeliveryAction.TRADED_OFFER_EDIT): 1,
    (TelegramFeederKind.OFFER_EDIT, TelegramDeliveryAction.EXPIRED_OFFER_EDIT): 2,
    (TelegramFeederKind.OFFER_EDIT, TelegramDeliveryAction.CANCELLED_OFFER_EDIT): 3,
    (TelegramFeederKind.OFFER_EDIT, TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT): 4,
    (TelegramFeederKind.OFFER_EDIT, TelegramDeliveryAction.RECONCILIATION_EDIT): 5,
    (TelegramFeederKind.TRADE, TelegramDeliveryAction.TRADE_RESULT): 0,
    (TelegramFeederKind.TRADE, TelegramDeliveryAction.TRADE_RESPONSE): 1,
    (TelegramFeederKind.TRADE, TelegramDeliveryAction.TRADE_ALTERNATIVE): 2,
    (TelegramFeederKind.TRADE, TelegramDeliveryAction.TRADE_UNAVAILABLE): 3,
    (TelegramFeederKind.TRADE, TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT): 4,
    (TelegramFeederKind.TRADE, TelegramDeliveryAction.TRADE_NONCRITICAL): 5,
    (TelegramFeederKind.ADMIN_SYSTEM, TelegramDeliveryAction.NEW_USER_MEMBERSHIP): 0,
    (TelegramFeederKind.ADMIN_SYSTEM, TelegramDeliveryAction.ACCOUNT_STATUS): 1,
    (TelegramFeederKind.ADMIN_SYSTEM, TelegramDeliveryAction.TARGETED_ADMIN_MESSAGE): 2,
    (TelegramFeederKind.ADMIN_SYSTEM, TelegramDeliveryAction.ADMIN_BROADCAST): 3,
    (TelegramFeederKind.ADMIN_SYSTEM, TelegramDeliveryAction.GENERAL_ANNOUNCEMENT): 4,
    (TelegramFeederKind.ADMIN_SYSTEM, TelegramDeliveryAction.COSMETIC_CLEANUP): 5,
    (TelegramFeederKind.MARKET_STATUS, TelegramDeliveryAction.MARKET_TRANSITION): 0,
    (TelegramFeederKind.MARKET_STATUS, TelegramDeliveryAction.MARKET_STATUS_CORRECTION): 1,
    (TelegramFeederKind.MARKET_STATUS, TelegramDeliveryAction.NONCRITICAL_MARKET): 2,
    (TelegramFeederKind.TIMED_BOT, TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT): 0,
    (TelegramFeederKind.TIMED_BOT, TelegramDeliveryAction.TIMED_SECURITY): 1,
    (TelegramFeederKind.TIMED_BOT, TelegramDeliveryAction.DELAYED_RESTRICTION): 2,
    (TelegramFeederKind.TIMED_BOT, TelegramDeliveryAction.TEMPORARY_CLEANUP): 3,
    (TelegramFeederKind.TIMED_BOT, TelegramDeliveryAction.COSMETIC_CLEANUP): 4,
    (TelegramFeederKind.DIRECT, TelegramDeliveryAction.CALLBACK_DEADLINE): 0,
    (TelegramFeederKind.DIRECT, TelegramDeliveryAction.GENERAL_IMMEDIATE): 0,
}

_TERMINAL_EDIT_REPLACEMENTS = {
    TelegramOfferBusinessState.TRADED: TelegramDeliveryAction.TRADED_OFFER_EDIT,
    TelegramOfferBusinessState.EXPIRED: TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
    TelegramOfferBusinessState.CANCELLED: TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
}


def feeder_internal_rank(
    feeder: TelegramFeederKind, action: TelegramDeliveryAction
) -> int:
    try:
        return _FEEDER_INTERNAL_RANK[(feeder, action)]
    except KeyError as exc:
        raise ValueError(f"action_not_allowed_for_feeder:{feeder.value}:{action.value}") from exc


def priority_and_rank_for_action(
    action: TelegramDeliveryAction,
    *,
    now: datetime | None = None,
    delivery_deadline_at: datetime | None = None,
) -> tuple[TelegramDeliveryPriority, int]:
    if (
        action == TelegramDeliveryAction.TRADE_RESULT
        and now is not None
        and delivery_deadline_at is not None
        and now >= delivery_deadline_at
    ):
        return TelegramDeliveryPriority.M0, 1
    return _ACTION_PRIORITY_AND_RANK[action]


def build_delivery_dedupe_key(
    *,
    feeder: TelegramFeederKind,
    source_natural_id: str,
    source_version: int,
    action: TelegramDeliveryAction,
    destination_identity: str,
) -> str:
    parts = (
        feeder.value,
        str(source_natural_id),
        str(int(source_version)),
        action.value,
        str(destination_identity),
    )
    return "\x1f".join(f"{len(part)}:{part}" for part in parts)


@dataclass(slots=True)
class TelegramDeliveryJob:
    id: int
    dedupe_key: str
    feeder: TelegramFeederKind
    feeder_rank: int
    source_natural_id: str
    source_version: int
    destination_key: str
    destination_class: TelegramDestinationClass
    method: str
    payload: dict[str, Any]
    action: TelegramDeliveryAction
    created_sequence: int
    bot_identity: str = "primary"
    delivery_deadline_at: datetime | None = None
    eligible_at: datetime | None = None
    freshness_deadline_at: datetime | None = None
    campaign_id: str | None = None
    state: TelegramDeliveryState = TelegramDeliveryState.PENDING
    attempt_count: int = 0
    next_retry_at: datetime | None = None
    lease_until: datetime | None = None
    lease_token: int = 0
    worker_id: str | None = None
    last_error_class: str | None = None
    last_error_message: str | None = None
    telegram_message_id: int | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in FINAL_DELIVERY_STATES

    def effective_priority(self, *, now: datetime) -> TelegramDeliveryPriority:
        return priority_and_rank_for_action(
            self.action,
            now=now,
            delivery_deadline_at=self.delivery_deadline_at,
        )[0]


@dataclass(frozen=True, slots=True)
class TelegramDeliveryDecision:
    outcome: TelegramDeliveryOutcome
    next_retry_at: datetime | None = None
    destination_cooldown_until: datetime | None = None
    bot_cooldown_until: datetime | None = None
    bot_probe_not_before: datetime | None = None
    reason: str | None = None
    offer_mutation: None = None


@dataclass(frozen=True, slots=True)
class TelegramMethodCall:
    method: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TelegramFreshnessSnapshot:
    offer_state: TelegramOfferBusinessState = TelegramOfferBusinessState.ACTIVE
    interaction_valid: bool = True
    channel_message_exists: bool = True
    ttl_valid: bool = True
    run_id_allowed: bool = True


@dataclass(frozen=True, slots=True)
class TelegramFreshnessDecision:
    outcome: TelegramFreshnessOutcome
    replacement_action: TelegramDeliveryAction | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramKeyboardDecision:
    delete_active_anchor: bool
    remove_reply_keyboard: bool
    restore_persistent_main_menu: bool
    remove_stale_business_inline_keyboard: bool


def delivery_order_key(job: TelegramDeliveryJob, *, now: datetime) -> tuple[int, int, float, int]:
    priority, rank = priority_and_rank_for_action(
        job.action,
        now=now,
        delivery_deadline_at=job.delivery_deadline_at,
    )
    deadline = (
        job.delivery_deadline_at.timestamp()
        if job.delivery_deadline_at is not None
        else float("inf")
    )
    return int(priority), int(rank), deadline, int(job.created_sequence)


def build_terminal_offer_edit_call(
    *, chat_id: int, message_id: int, text: str
) -> TelegramMethodCall:
    return TelegramMethodCall(
        method="editMessageText",
        payload={
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "text": str(text),
            "reply_markup": {"inline_keyboard": []},
        },
    )


def build_offer_success_edit_call(
    *,
    chat_id: int,
    message_id: int,
    text: str,
    expire_callback_data: str,
) -> TelegramMethodCall:
    return TelegramMethodCall(
        method="editMessageText",
        payload={
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "text": str(text),
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": "❌ منقضی کردن",
                            "callback_data": str(expire_callback_data),
                        }
                    ]
                ]
            },
        },
    )


def authenticated_keyboard_policy(
    *,
    authenticated: bool,
    temporary_context_keyboard: bool,
    flow_exit: TelegramFlowExit,
    business_inline_keyboard_stale: bool,
) -> TelegramKeyboardDecision:
    del flow_exit  # all four exits intentionally share the same safe policy
    return TelegramKeyboardDecision(
        delete_active_anchor=False if authenticated else temporary_context_keyboard,
        remove_reply_keyboard=False if authenticated else temporary_context_keyboard,
        restore_persistent_main_menu=authenticated and temporary_context_keyboard,
        remove_stale_business_inline_keyboard=business_inline_keyboard_stale,
    )


def revalidate_delivery(
    job: TelegramDeliveryJob,
    snapshot: TelegramFreshnessSnapshot,
    *,
    now: datetime,
) -> TelegramFreshnessDecision:
    if job.action in {
        TelegramDeliveryAction.CALLBACK_DEADLINE,
        TelegramDeliveryAction.OTP_DEADLINE,
        TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK,
    }:
        if (
            not snapshot.interaction_valid
            or (job.delivery_deadline_at is not None and now >= job.delivery_deadline_at)
        ):
            return TelegramFreshnessDecision(
                TelegramFreshnessOutcome.EXPIRED_INTERACTION,
                reason="interaction_deadline_passed",
            )
        return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)

    if job.action == TelegramDeliveryAction.OFFER_PUBLISH:
        if (
            snapshot.offer_state != TelegramOfferBusinessState.ACTIVE
            or (
                job.freshness_deadline_at is not None
                and now >= job.freshness_deadline_at
            )
        ):
            return TelegramFreshnessDecision(
                TelegramFreshnessOutcome.SUPERSEDED,
                reason="offer_not_publishable",
            )

    if job.action == TelegramDeliveryAction.PARTIAL_OFFER_EDIT:
        if not snapshot.channel_message_exists:
            if snapshot.offer_state == TelegramOfferBusinessState.NEVER_PUBLISHED:
                return TelegramFreshnessDecision(
                    TelegramFreshnessOutcome.SENT_NOOP,
                    reason="offer_never_published",
                )
            return TelegramFreshnessDecision(
                TelegramFreshnessOutcome.WAIT_DEPENDENCY,
                reason="channel_message_missing",
            )
        if snapshot.offer_state != TelegramOfferBusinessState.ACTIVE:
            return TelegramFreshnessDecision(
                TelegramFreshnessOutcome.RECLASSIFY,
                replacement_action=_TERMINAL_EDIT_REPLACEMENTS.get(snapshot.offer_state),
                reason="partial_became_terminal",
            )

    if job.action in {
        TelegramDeliveryAction.TRADED_OFFER_EDIT,
        TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
        TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
    } and not snapshot.channel_message_exists:
        return TelegramFreshnessDecision(
            TelegramFreshnessOutcome.SENT_NOOP,
            reason="terminal_offer_never_published",
        )

    if job.action in {
        TelegramDeliveryAction.OFFER_SUCCESS,
        TelegramDeliveryAction.TRADE_ALTERNATIVE,
        TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
    } and (not snapshot.interaction_valid or snapshot.offer_state != TelegramOfferBusinessState.ACTIVE):
        return TelegramFreshnessDecision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="interactive_payload_stale",
        )

    if job.action in {
        TelegramDeliveryAction.ADMIN_BROADCAST,
        TelegramDeliveryAction.GENERAL_ANNOUNCEMENT,
    } and not snapshot.ttl_valid:
        return TelegramFreshnessDecision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="administrative_ttl_passed",
        )

    if job.action in {
        TelegramDeliveryAction.TEMPORARY_CLEANUP,
        TelegramDeliveryAction.COSMETIC_CLEANUP,
    } and not snapshot.run_id_allowed:
        return TelegramFreshnessDecision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="cleanup_scope_not_allowed",
        )

    return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)


def apply_freshness_decision(
    job: TelegramDeliveryJob, decision: TelegramFreshnessDecision
) -> None:
    if decision.outcome == TelegramFreshnessOutcome.SUPERSEDED:
        job.state = TelegramDeliveryState.SUPERSEDED
    elif decision.outcome == TelegramFreshnessOutcome.EXPIRED_INTERACTION:
        job.state = TelegramDeliveryState.EXPIRED_INTERACTION
    elif decision.outcome == TelegramFreshnessOutcome.SENT_NOOP:
        job.state = TelegramDeliveryState.SENT_NOOP
    elif decision.outcome == TelegramFreshnessOutcome.QUARANTINED:
        job.state = TelegramDeliveryState.QUARANTINED
    elif decision.outcome == TelegramFreshnessOutcome.WAIT_DEPENDENCY:
        job.state = TelegramDeliveryState.PENDING
    elif decision.outcome == TelegramFreshnessOutcome.RECLASSIFY:
        if decision.replacement_action is None:
            job.state = TelegramDeliveryState.SUPERSEDED
        else:
            job.action = decision.replacement_action
            job.feeder_rank = feeder_internal_rank(job.feeder, decision.replacement_action)


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _status_code(result: TelegramGatewayResultLike) -> int | None:
    if isinstance(result.response_json, Mapping) and result.response_json.get("ok") is False:
        try:
            return int(result.response_json.get("error_code"))
        except (TypeError, ValueError):
            pass
    if result.status_code is not None:
        return int(result.status_code)
    if isinstance(result.response_json, Mapping):
        try:
            return int(result.response_json.get("error_code"))
        except (TypeError, ValueError):
            return None
    return None


def _retry_after_seconds(result: TelegramGatewayResultLike) -> float | None:
    if not isinstance(result.response_json, Mapping):
        return None
    parameters = result.response_json.get("parameters")
    if not isinstance(parameters, Mapping):
        return None
    return _positive_number(parameters.get("retry_after"))


def _has_migrate_to_chat_id(result: TelegramGatewayResultLike) -> bool:
    if not isinstance(result.response_json, Mapping):
        return False
    parameters = result.response_json.get("parameters")
    return isinstance(parameters, Mapping) and parameters.get("migrate_to_chat_id") is not None


def _error_text(result: TelegramGatewayResultLike) -> str:
    parts = [result.response_text or "", result.error or ""]
    if isinstance(result.response_json, Mapping):
        parts.extend(
            str(result.response_json.get(key) or "")
            for key in ("description", "message", "error")
        )
    return " ".join(parts).strip().lower()


def _bounded_backoff_seconds(
    attempt_count: int, *, base_seconds: float, max_seconds: float
) -> float:
    exponent = min(max(0, int(attempt_count) - 1), 16)
    return min(
        max(float(base_seconds), float(max_seconds)),
        float(base_seconds) * (2**exponent),
    )


def _clear_lease(job: TelegramDeliveryJob) -> None:
    job.worker_id = None
    job.lease_until = None


def apply_gateway_result(
    job: TelegramDeliveryJob,
    result: TelegramGatewayResultLike,
    *,
    now: datetime,
    retry_after_safety_seconds: float,
    retry_base_seconds: float = 1.0,
    retry_max_seconds: float = DEFAULT_MAX_RETRY_SECONDS,
    destination_pause_seconds: float = DEFAULT_DESTINATION_PAUSE_SECONDS,
) -> TelegramDeliveryDecision:
    if job.is_terminal:
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.ALREADY_RESOLVED,
            reason="already_resolved",
        )

    _clear_lease(job)
    if result.method and str(result.method) != job.method:
        job.state = TelegramDeliveryState.QUARANTINED
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.QUARANTINED,
            reason="telegram_method_mismatch",
        )
    envelope_ok = (
        isinstance(result.response_json, Mapping)
        and result.response_json.get("ok") is True
    )
    if result.ok and envelope_ok:
        if job.method == "sendMessage" and result.message_id is None:
            job.state = TelegramDeliveryState.AMBIGUOUS
            return TelegramDeliveryDecision(
                TelegramDeliveryOutcome.AMBIGUOUS,
                reason="telegram_send_success_without_message_id",
            )
        job.state = TelegramDeliveryState.SENT
        job.next_retry_at = None
        job.last_error_class = None
        job.last_error_message = None
        job.telegram_message_id = result.message_id
        return TelegramDeliveryDecision(TelegramDeliveryOutcome.SENT, reason="sent")

    status_code = _status_code(result)
    error_text = _error_text(result)
    normalized_error = str(result.error or "").strip().lower()
    job.last_error_class = str(
        result.error
        or (f"HTTP{status_code}" if status_code is not None else "TelegramUnknownError")
    )
    job.last_error_message = error_text[:500] or None

    if status_code == 400 and "message is not modified" in error_text and job.method.startswith("edit"):
        job.state = TelegramDeliveryState.SENT_NOOP
        job.next_retry_at = None
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.SENT_NOOP,
            reason="telegram_edit_already_applied",
        )

    if _has_migrate_to_chat_id(result):
        job.state = TelegramDeliveryState.BLOCKED_DESTINATION
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.DESTINATION_PAUSED,
            reason="telegram_destination_migrated",
        )

    if status_code == 429:
        retry_after = _retry_after_seconds(result)
        if retry_after is None:
            retry_after = _bounded_backoff_seconds(
                job.attempt_count,
                base_seconds=retry_base_seconds,
                max_seconds=retry_max_seconds,
            )
        retry_at = now + timedelta(
            seconds=retry_after + max(0.0, float(retry_after_safety_seconds))
        )
        job.state = TelegramDeliveryState.PENDING_RETRY
        job.next_retry_at = retry_at
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.RETRY_PENDING,
            next_retry_at=retry_at,
            destination_cooldown_until=retry_at,
            reason="telegram_rate_limited",
        )

    if status_code in {401, 409}:
        job.state = TelegramDeliveryState.BLOCKED_BOT
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.BOT_PAUSED,
            reason="telegram_bot_blocked_or_conflicted",
        )

    if status_code == 403:
        if job.destination_class == TelegramDestinationClass.PRIVATE:
            job.state = TelegramDeliveryState.PERMANENT_UNDELIVERABLE
            return TelegramDeliveryDecision(
                TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE,
                reason="telegram_private_destination_forbidden",
            )
        pause_until = now + timedelta(seconds=max(1.0, float(destination_pause_seconds)))
        job.state = TelegramDeliveryState.BLOCKED_DESTINATION
        job.next_retry_at = pause_until
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.DESTINATION_PAUSED,
            next_retry_at=pause_until,
            destination_cooldown_until=pause_until,
            reason="telegram_destination_forbidden",
        )

    if status_code == 404:
        if "method" in error_text or "endpoint" in error_text:
            job.state = TelegramDeliveryState.BLOCKED_GATEWAY
            return TelegramDeliveryDecision(
                TelegramDeliveryOutcome.GATEWAY_PAUSED,
                reason="telegram_gateway_not_found",
            )
        job.state = TelegramDeliveryState.PERMANENT_UNDELIVERABLE
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE,
            reason="telegram_resource_not_found",
        )

    if (
        job.method == "answerCallbackQuery"
        and status_code == 400
        and any(pattern in error_text for pattern in _CALLBACK_EXPIRED_PATTERNS)
    ):
        job.state = TelegramDeliveryState.EXPIRED_INTERACTION
        job.next_retry_at = None
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.EXPIRED_INTERACTION,
            reason="telegram_callback_query_expired",
        )

    if status_code == 400 and any(pattern in error_text for pattern in _MESSAGE_NOT_FOUND_PATTERNS):
        job.state = TelegramDeliveryState.PERMANENT_UNDELIVERABLE
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE,
            reason="telegram_message_uneditable",
        )

    if status_code == 400 and any(pattern in error_text for pattern in _MALFORMED_PAYLOAD_PATTERNS):
        job.state = TelegramDeliveryState.TERMINAL_FAILED
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.TERMINAL_FAILED,
            reason="telegram_malformed_payload",
        )

    if job.method == "sendMessage" and (
        normalized_error in _AMBIGUOUS_SEND_ERRORS
        or (status_code is not None and 500 <= status_code <= 599)
        or (status_code is not None and 200 <= status_code <= 299 and result.response_json is None)
    ):
        job.state = TelegramDeliveryState.AMBIGUOUS
        job.next_retry_at = None
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.AMBIGUOUS,
            reason="telegram_send_result_ambiguous",
        )

    if status_code is not None and 200 <= status_code <= 299:
        delay = _bounded_backoff_seconds(
            job.attempt_count,
            base_seconds=retry_base_seconds,
            max_seconds=retry_max_seconds,
        )
        retry_at = now + timedelta(seconds=delay)
        job.state = TelegramDeliveryState.PENDING_RETRY
        job.next_retry_at = retry_at
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.RETRY_PENDING,
            next_retry_at=retry_at,
            reason="telegram_success_envelope_invalid",
        )

    if (
        (status_code is not None and 500 <= status_code <= 599)
        or normalized_error in _SAFE_RETRY_TRANSPORT_ERRORS
        or normalized_error in _AMBIGUOUS_SEND_ERRORS
    ):
        delay = _bounded_backoff_seconds(
            job.attempt_count,
            base_seconds=retry_base_seconds,
            max_seconds=retry_max_seconds,
        )
        retry_at = now + timedelta(seconds=delay)
        job.state = TelegramDeliveryState.PENDING_RETRY
        job.next_retry_at = retry_at
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.RETRY_PENDING,
            next_retry_at=retry_at,
            reason="telegram_temporary_failure",
        )

    if status_code is not None and 400 <= status_code <= 499:
        job.state = TelegramDeliveryState.QUARANTINED
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.QUARANTINED,
            reason="telegram_unknown_client_error",
        )

    job.state = TelegramDeliveryState.QUARANTINED
    return TelegramDeliveryDecision(
        TelegramDeliveryOutcome.QUARANTINED,
        reason="telegram_unclassified_response",
    )


def reconcile_ambiguous_send(
    job: TelegramDeliveryJob,
    *,
    delivered: bool | None,
    now: datetime,
    telegram_message_id: int | None = None,
    resolution_deadline_at: datetime | None = None,
    confirmed_absent_retry_delay_seconds: float = 0.0,
) -> TelegramDeliveryDecision:
    if job.state not in {
        TelegramDeliveryState.AMBIGUOUS,
        TelegramDeliveryState.AMBIGUOUS_UNRESOLVED,
    }:
        raise ValueError("job_is_not_ambiguous")
    if delivered is None:
        if resolution_deadline_at is not None and now >= resolution_deadline_at:
            job.state = TelegramDeliveryState.AMBIGUOUS_UNRESOLVED
            return TelegramDeliveryDecision(
                TelegramDeliveryOutcome.AMBIGUOUS_UNRESOLVED,
                reason="telegram_send_reconciliation_deadline_passed",
            )
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.AMBIGUOUS,
            reason="telegram_send_reconciliation_inconclusive",
        )
    if delivered:
        if telegram_message_id is None:
            raise ValueError("delivered_send_requires_message_id")
        job.state = TelegramDeliveryState.SENT
        job.next_retry_at = None
        job.telegram_message_id = int(telegram_message_id)
        return TelegramDeliveryDecision(
            TelegramDeliveryOutcome.SENT,
            reason="telegram_send_reconciled_delivered",
        )
    retry_at = now + timedelta(
        seconds=max(0.0, float(confirmed_absent_retry_delay_seconds))
    )
    job.state = (
        TelegramDeliveryState.PENDING_RETRY
        if retry_at > now
        else TelegramDeliveryState.PENDING
    )
    job.next_retry_at = retry_at
    return TelegramDeliveryDecision(
        TelegramDeliveryOutcome.RETRY_PENDING,
        next_retry_at=retry_at,
        reason="telegram_send_reconciled_absent",
    )


@dataclass(slots=True)
class InMemoryTelegramDeliveryQueue:
    """Lock-protected reference main queue for deterministic Stage 2 tests."""

    jobs: dict[int, TelegramDeliveryJob] = field(default_factory=dict)
    destination_cooldowns: dict[str, datetime] = field(default_factory=dict)
    bot_cooldowns: dict[str, datetime] = field(default_factory=dict)
    bot_probe_not_before: dict[str, datetime] = field(default_factory=dict)
    _recent_429: dict[str, dict[str, datetime]] = field(default_factory=dict)
    _dedupe_index: dict[str, int] = field(default_factory=dict)
    _next_id: int = 1
    _next_sequence: int = 1
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def enqueue(
        self,
        *,
        feeder: TelegramFeederKind,
        source_natural_id: str,
        source_version: int,
        action: TelegramDeliveryAction,
        destination_key: str,
        destination_class: TelegramDestinationClass,
        method: str,
        payload: Mapping[str, Any],
        bot_identity: str = "primary",
        delivery_deadline_at: datetime | None = None,
        eligible_at: datetime | None = None,
        freshness_deadline_at: datetime | None = None,
        campaign_id: str | None = None,
    ) -> tuple[TelegramDeliveryJob, bool]:
        rank = feeder_internal_rank(feeder, action)
        dedupe_key = build_delivery_dedupe_key(
            feeder=feeder,
            source_natural_id=source_natural_id,
            source_version=source_version,
            action=action,
            destination_identity=destination_key,
        )
        async with self._lock:
            existing_id = self._dedupe_index.get(dedupe_key)
            if existing_id is not None:
                existing = self.jobs[existing_id]
                comparable = (
                    existing.bot_identity == str(bot_identity)
                    and existing.destination_class == destination_class
                    and existing.method == str(method)
                    and existing.payload == dict(payload)
                    and existing.delivery_deadline_at == delivery_deadline_at
                    and existing.eligible_at == eligible_at
                    and existing.freshness_deadline_at == freshness_deadline_at
                    and existing.campaign_id == campaign_id
                )
                if not comparable:
                    raise TelegramDeliveryDedupeConflictError(dedupe_key)
                return existing, False
            job = TelegramDeliveryJob(
                id=self._next_id,
                dedupe_key=dedupe_key,
                feeder=feeder,
                feeder_rank=rank,
                source_natural_id=str(source_natural_id),
                source_version=int(source_version),
                destination_key=str(destination_key),
                destination_class=destination_class,
                method=str(method),
                payload=dict(payload),
                action=action,
                created_sequence=self._next_sequence,
                bot_identity=str(bot_identity),
                delivery_deadline_at=delivery_deadline_at,
                eligible_at=eligible_at,
                freshness_deadline_at=freshness_deadline_at,
                campaign_id=campaign_id,
            )
            self.jobs[job.id] = job
            self._dedupe_index[dedupe_key] = job.id
            self._next_id += 1
            self._next_sequence += 1
            return job, True

    async def claim_next(
        self,
        *,
        now: datetime,
        worker_id: str,
        request_timeout_seconds: float,
        lease_seconds: float,
        bot_identity: str = "primary",
    ) -> TelegramDeliveryJob | None:
        minimum = float(request_timeout_seconds) + MINIMUM_LEASE_MARGIN_SECONDS
        if float(lease_seconds) < minimum:
            raise ValueError("lease_must_cover_request_timeout_plus_margin")
        lane_identity = str(bot_identity or "").strip()
        if not lane_identity:
            raise ValueError("bot_identity_required")
        async with self._lock:
            cooldown_until = self.bot_cooldowns.get(lane_identity)
            if cooldown_until is not None and cooldown_until > now:
                return None
            probe_not_before = self.bot_probe_not_before.get(lane_identity)
            if probe_not_before is not None and probe_not_before > now:
                return None
            ready = [
                job
                for job in self.jobs.values()
                if job.state in CLAIMABLE_DELIVERY_STATES
                and job.bot_identity == lane_identity
                and (job.eligible_at is None or job.eligible_at <= now)
                and (job.next_retry_at is None or job.next_retry_at <= now)
                and self.destination_cooldowns.get(job.destination_key, now) <= now
            ]
            if not ready:
                return None
            job = min(ready, key=lambda item: delivery_order_key(item, now=now))
            job.state = TelegramDeliveryState.LEASED
            job.worker_id = str(worker_id)
            job.lease_token += 1
            job.lease_until = now + timedelta(seconds=float(lease_seconds))
            job.attempt_count += 1
            return job

    async def heartbeat(
        self,
        job_id: int,
        *,
        worker_id: str,
        lease_token: int,
        now: datetime,
        request_timeout_seconds: float,
        lease_seconds: float,
    ) -> bool:
        if float(lease_seconds) < float(request_timeout_seconds) + MINIMUM_LEASE_MARGIN_SECONDS:
            raise ValueError("lease_must_cover_request_timeout_plus_margin")
        async with self._lock:
            job = self.jobs[int(job_id)]
            if (
                job.state != TelegramDeliveryState.LEASED
                or job.worker_id != str(worker_id)
                or job.lease_token != int(lease_token)
            ):
                return False
            job.lease_until = now + timedelta(seconds=float(lease_seconds))
            return True

    async def resolve(
        self,
        job_id: int,
        result: TelegramGatewayResultLike,
        *,
        worker_id: str,
        lease_token: int,
        now: datetime,
        retry_after_safety_seconds: float,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = DEFAULT_MAX_RETRY_SECONDS,
        destination_pause_seconds: float = DEFAULT_DESTINATION_PAUSE_SECONDS,
        rate_limit_probe_delay_seconds: float = DEFAULT_RATE_LIMIT_PROBE_DELAY_SECONDS,
        global_rate_limit_window_seconds: float = DEFAULT_GLOBAL_RATE_LIMIT_WINDOW_SECONDS,
    ) -> TelegramDeliveryDecision:
        async with self._lock:
            job = self.jobs[int(job_id)]
            if job.is_terminal:
                return apply_gateway_result(
                    job,
                    result,
                    now=now,
                    retry_after_safety_seconds=retry_after_safety_seconds,
                    retry_base_seconds=retry_base_seconds,
                    retry_max_seconds=retry_max_seconds,
                    destination_pause_seconds=destination_pause_seconds,
                )
            if (
                job.state != TelegramDeliveryState.LEASED
                or job.worker_id != str(worker_id)
                or job.lease_token != int(lease_token)
            ):
                return TelegramDeliveryDecision(
                    TelegramDeliveryOutcome.STALE_LEASE,
                    reason="stale_or_missing_lease",
                )
            decision = apply_gateway_result(
                job,
                result,
                now=now,
                retry_after_safety_seconds=retry_after_safety_seconds,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
                destination_pause_seconds=destination_pause_seconds,
            )
            if decision.destination_cooldown_until is not None:
                previous = self.destination_cooldowns.get(job.destination_key)
                if previous is None or decision.destination_cooldown_until > previous:
                    self.destination_cooldowns[job.destination_key] = decision.destination_cooldown_until

            if decision.reason == "telegram_rate_limited":
                window = timedelta(seconds=max(0.0, float(global_rate_limit_window_seconds)))
                recent_429 = self._recent_429.setdefault(job.bot_identity, {})
                recent_429 = {
                    destination: seen_at
                    for destination, seen_at in recent_429.items()
                    if now - seen_at <= window
                }
                distinct_other = any(
                    destination != job.destination_key
                    for destination in recent_429
                )
                recent_429[job.destination_key] = now
                self._recent_429[job.bot_identity] = recent_429
                if distinct_other:
                    candidate = max(
                        (
                            self.destination_cooldowns[destination]
                            for destination in recent_429
                            if destination in self.destination_cooldowns
                        ),
                        default=decision.destination_cooldown_until or now,
                    )
                    current = self.bot_cooldowns.get(job.bot_identity)
                    if current is None or candidate > current:
                        self.bot_cooldowns[job.bot_identity] = candidate
                    self.bot_probe_not_before.pop(job.bot_identity, None)
                    decision = replace(
                        decision,
                        bot_cooldown_until=self.bot_cooldowns[job.bot_identity],
                    )
                else:
                    probe_at = now + timedelta(
                        seconds=max(0.0, float(rate_limit_probe_delay_seconds))
                    )
                    current = self.bot_probe_not_before.get(job.bot_identity)
                    if current is None or probe_at > current:
                        self.bot_probe_not_before[job.bot_identity] = probe_at
                    decision = replace(
                        decision,
                        bot_probe_not_before=self.bot_probe_not_before[job.bot_identity],
                    )
            return decision

    async def recover_expired_leases(self, *, now: datetime) -> list[int]:
        async with self._lock:
            recovered: list[int] = []
            for job in sorted(
                self.jobs.values(),
                key=lambda item: delivery_order_key(item, now=now),
            ):
                if (
                    job.state == TelegramDeliveryState.LEASED
                    and job.lease_until is not None
                    and job.lease_until <= now
                ):
                    job.state = TelegramDeliveryState.PENDING
                    job.worker_id = None
                    job.lease_until = None
                    recovered.append(job.id)
            return recovered

    async def operator_resume_destination(
        self, destination_key: str, *, now: datetime
    ) -> list[int]:
        async with self._lock:
            self.destination_cooldowns.pop(str(destination_key), None)
            resumed: list[int] = []
            for job in self.jobs.values():
                if (
                    job.destination_key == str(destination_key)
                    and job.state == TelegramDeliveryState.BLOCKED_DESTINATION
                ):
                    job.state = TelegramDeliveryState.PENDING
                    job.next_retry_at = now
                    resumed.append(job.id)
            return resumed

    async def operator_resume_global(self, *, now: datetime) -> list[int]:
        async with self._lock:
            self.bot_cooldowns.clear()
            self.bot_probe_not_before.clear()
            self._recent_429.clear()
            resumed: list[int] = []
            for job in self.jobs.values():
                if job.state in {
                    TelegramDeliveryState.BLOCKED_BOT,
                    TelegramDeliveryState.BLOCKED_GATEWAY,
                }:
                    job.state = TelegramDeliveryState.PENDING
                    job.next_retry_at = now
                    resumed.append(job.id)
            return resumed


@dataclass(slots=True)
class TelegramOfferEditItem:
    offer_id: str
    source_version: int
    action: TelegramDeliveryAction
    offer_created_at: datetime
    first_enqueued_at: datetime
    created_sequence: int
    channel_message_id: int | None

    @property
    def internal_rank(self) -> int:
        return feeder_internal_rank(TelegramFeederKind.OFFER_EDIT, self.action)

    def is_stale(self, *, now: datetime) -> bool:
        return now - self.first_enqueued_at >= timedelta(seconds=EDIT_STALE_AFTER_SECONDS)


@dataclass(slots=True)
class InMemoryOfferEditFeeder:
    items: dict[str, TelegramOfferEditItem] = field(default_factory=dict)
    _next_sequence: int = 1
    _fresh_since_stale_by_rank: dict[int, int] = field(default_factory=dict)

    def enqueue(
        self,
        *,
        offer_id: str,
        source_version: int,
        action: TelegramDeliveryAction,
        offer_created_at: datetime,
        enqueued_at: datetime,
        channel_message_id: int | None,
    ) -> tuple[TelegramOfferEditItem, bool]:
        feeder_internal_rank(TelegramFeederKind.OFFER_EDIT, action)
        existing = self.items.get(str(offer_id))
        if existing is not None:
            if int(source_version) < existing.source_version:
                return existing, False
            if (
                existing.action in {
                    TelegramDeliveryAction.TRADED_OFFER_EDIT,
                    TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
                    TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
                }
                and action == TelegramDeliveryAction.PARTIAL_OFFER_EDIT
            ):
                raise ValueError("terminal_offer_edit_cannot_revert_to_partial")
            changed = (
                existing.source_version != int(source_version)
                or existing.action != action
                or existing.channel_message_id != channel_message_id
            )
            existing.source_version = int(source_version)
            existing.action = action
            existing.channel_message_id = channel_message_id
            return existing, changed
        item = TelegramOfferEditItem(
            offer_id=str(offer_id),
            source_version=int(source_version),
            action=action,
            offer_created_at=offer_created_at,
            first_enqueued_at=enqueued_at,
            created_sequence=self._next_sequence,
            channel_message_id=channel_message_id,
        )
        self.items[item.offer_id] = item
        self._next_sequence += 1
        return item, True

    @staticmethod
    def _newest_key(item: TelegramOfferEditItem) -> tuple[float, int]:
        return -item.offer_created_at.timestamp(), item.created_sequence

    def pop_next(self, *, now: datetime) -> TelegramOfferEditItem | None:
        eligible = [item for item in self.items.values() if item.channel_message_id is not None]
        if not eligible:
            return None
        rank = min(item.internal_rank for item in eligible)
        same_rank = [item for item in eligible if item.internal_rank == rank]
        fresh = [item for item in same_rank if not item.is_stale(now=now)]
        stale = [item for item in same_rank if item.is_stale(now=now)]
        fresh_count = self._fresh_since_stale_by_rank.get(rank, 0)
        if stale and (not fresh or fresh_count >= EDIT_CATCH_UP_FRESH_COUNT):
            selected = min(stale, key=self._newest_key)
            self._fresh_since_stale_by_rank[rank] = 0
        else:
            selected = min(fresh, key=self._newest_key)
            self._fresh_since_stale_by_rank[rank] = fresh_count + 1
        del self.items[selected.offer_id]
        return selected


@dataclass(slots=True)
class TelegramFeederRecord:
    id: str
    feeder: TelegramFeederKind
    source_natural_id: str
    source_version: int
    action: TelegramDeliveryAction
    destination_key: str
    destination_class: TelegramDestinationClass
    method: str
    payload: dict[str, Any]
    delivery_deadline_at: datetime | None = None
    freshness_deadline_at: datetime | None = None
    campaign_id: str | None = None
    state: TelegramFeederRecordState = TelegramFeederRecordState.READY
    main_job_id: int | None = None
    terminal_job_state: TelegramDeliveryState | None = None


@dataclass(slots=True)
class InMemoryFeederCoordinator:
    records: dict[str, TelegramFeederRecord] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def add(self, record: TelegramFeederRecord) -> None:
        existing = self.records.get(record.id)
        if existing is not None and existing is not record:
            raise ValueError("duplicate_feeder_record_id")
        feeder_internal_rank(record.feeder, record.action)
        self.records[record.id] = record

    async def handoff(
        self,
        record_id: str,
        queue: InMemoryTelegramDeliveryQueue,
        *,
        fail_after_enqueue: bool = False,
    ) -> TelegramDeliveryJob:
        async with self._lock:
            record = self.records[str(record_id)]
            job, _created = await queue.enqueue(
                feeder=record.feeder,
                source_natural_id=record.source_natural_id,
                source_version=record.source_version,
                action=record.action,
                destination_key=record.destination_key,
                destination_class=record.destination_class,
                method=record.method,
                payload=record.payload,
                delivery_deadline_at=record.delivery_deadline_at,
                freshness_deadline_at=record.freshness_deadline_at,
                campaign_id=record.campaign_id,
            )
            if fail_after_enqueue:
                raise TelegramHandoffInterrupted(record.id)
            record.main_job_id = job.id
            record.state = TelegramFeederRecordState.HANDED_OFF
            return job

    def reflect_main_result(
        self, record_id: str, job: TelegramDeliveryJob
    ) -> TelegramFeederRecordState:
        record = self.records[str(record_id)]
        if record.main_job_id != job.id:
            raise ValueError("main_job_identity_mismatch")
        if job.is_terminal:
            record.state = TelegramFeederRecordState.TERMINAL
            record.terminal_job_state = job.state
        return record.state


@dataclass(slots=True)
class TelegramBroadcastCampaign:
    id: str
    recipients: tuple[str, ...]
    next_index: int = 0
    in_flight_recipient: str | None = None
    last_released_at: datetime | None = None
    paused: bool = False

    @property
    def finished(self) -> bool:
        return self.next_index >= len(self.recipients) and self.in_flight_recipient is None


@dataclass(frozen=True, slots=True)
class TelegramBroadcastRelease:
    campaign_id: str
    recipient: str


@dataclass(slots=True)
class InMemoryAdminBroadcastFeeder:
    max_global_in_flight: int = 2
    campaigns: dict[str, TelegramBroadcastCampaign] = field(default_factory=dict)
    globally_paused: bool = False

    def add_campaign(self, campaign_id: str, recipients: Sequence[str]) -> None:
        if campaign_id in self.campaigns:
            raise ValueError("duplicate_campaign")
        self.campaigns[campaign_id] = TelegramBroadcastCampaign(
            id=str(campaign_id),
            recipients=tuple(str(item) for item in recipients),
        )

    def release_next(self, *, now: datetime) -> TelegramBroadcastRelease | None:
        if self.globally_paused:
            return None
        in_flight = sum(
            campaign.in_flight_recipient is not None
            for campaign in self.campaigns.values()
        )
        if in_flight >= self.max_global_in_flight:
            return None
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        eligible = [
            campaign
            for campaign in self.campaigns.values()
            if not campaign.paused
            and campaign.in_flight_recipient is None
            and campaign.next_index < len(campaign.recipients)
        ]
        if not eligible:
            return None
        campaign = min(
            eligible,
            key=lambda item: (item.last_released_at or epoch, item.id),
        )
        recipient = campaign.recipients[campaign.next_index]
        campaign.in_flight_recipient = recipient
        campaign.last_released_at = now
        return TelegramBroadcastRelease(campaign.id, recipient)

    def apply_result(
        self,
        campaign_id: str,
        outcome: TelegramDeliveryOutcome,
        *,
        systemic: bool = False,
    ) -> None:
        campaign = self.campaigns[str(campaign_id)]
        if systemic or outcome in {
            TelegramDeliveryOutcome.BOT_PAUSED,
            TelegramDeliveryOutcome.GATEWAY_PAUSED,
        }:
            self.globally_paused = True
            return
        if outcome in {
            TelegramDeliveryOutcome.RETRY_PENDING,
            TelegramDeliveryOutcome.AMBIGUOUS,
            TelegramDeliveryOutcome.AMBIGUOUS_UNRESOLVED,
            TelegramDeliveryOutcome.DESTINATION_PAUSED,
        }:
            campaign.paused = True
            return
        if campaign.in_flight_recipient is None:
            raise ValueError("campaign_has_no_in_flight_recipient")
        campaign.next_index += 1
        campaign.in_flight_recipient = None

    def resume_campaign(self, campaign_id: str) -> None:
        self.campaigns[str(campaign_id)].paused = False
