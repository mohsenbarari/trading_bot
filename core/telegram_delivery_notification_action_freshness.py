"""Authoritative freshness for private action notifications in the outbox."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import UserAccountStatus
from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    canonical_telegram_delivery_payload,
)
from core.services.telegram_notification_outbox_service import (
    telegram_notification_dedupe_key,
    validate_telegram_notification_text,
)
from core.telegram_delivery_account_notice_contract import (
    ACCOUNT_NOTICE_KIND_DELETED,
    ACCOUNT_NOTICE_KIND_RESTRICTION_ACTIVE,
    ACCOUNT_NOTICE_KIND_STATUS,
    active_restriction_snapshot_matches_user,
    deleted_account_snapshot_matches_user,
    normalize_restriction_kind,
    validate_active_restriction_snapshot,
    validate_deleted_account_snapshot,
)
from core.telegram_delivery_notification_action_contract import (
    TELEGRAM_NOTIFICATION_ACTIONS,
    TelegramNotificationActionPolicy,
    telegram_notification_action_policy,
    telegram_notification_action_policy_from_source,
)
from core.telegram_delivery_interaction_result_contract import (
    TelegramInteractionAnchorEffect,
    TelegramInteractionResultContract,
    parse_interaction_result_contract,
    serialize_interaction_result_contract,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_interaction_anchor_state import TelegramInteractionAnchorState
from models.telegram_notification_outbox import (
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.user import User


NOTIFICATION_ACTION_FRESHNESS_ACTIONS = TELEGRAM_NOTIFICATION_ACTIONS
_SOURCE_SEPARATOR = ":payload-v1:"
_ALLOWED_PARSE_MODES = frozenset({"Markdown", "MarkdownV2", "HTML"})
_ACTIVE_STATUSES = frozenset(
    {
        TelegramNotificationOutboxStatus.PENDING.value,
        TelegramNotificationOutboxStatus.RETRYABLE_FAILED.value,
    }
)
_TERMINAL_STATUSES = frozenset(
    {
        TelegramNotificationOutboxStatus.SENT.value,
        TelegramNotificationOutboxStatus.SKIPPED.value,
        TelegramNotificationOutboxStatus.TERMINAL_FAILED.value,
    }
)


@dataclass(frozen=True, slots=True)
class TelegramNotificationActionSource:
    policy: TelegramNotificationActionPolicy
    dedupe_key: str
    source_id: str
    recipient_user_id: int
    text: str
    parse_mode: str | None
    reply_markup: dict[str, Any] | None
    account_notice_kind: str | None
    expected_account_status: str | None
    expected_messenger_blocked: bool | None
    expected_user_sync_version: int | None
    restriction_kind: str | None
    restriction_snapshot: dict[str, Any] | None
    deleted_account_snapshot: dict[str, str] | None
    not_before: datetime | None
    interaction_result: TelegramInteractionResultContract | None


@dataclass(frozen=True, slots=True)
class TelegramNotificationActionSnapshot:
    payload: dict[str, Any]
    source_version: int


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _strict_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _decision(
    outcome: TelegramFreshnessOutcome,
    *,
    reason: str,
    replacement_action: TelegramDeliveryAction | None = None,
) -> TelegramFreshnessDecision:
    return TelegramFreshnessDecision(
        outcome=outcome,
        reason=reason,
        replacement_action=replacement_action,
    )


def _quarantined(reason: str) -> TelegramFreshnessDecision:
    return _decision(TelegramFreshnessOutcome.QUARANTINED, reason=reason)


def telegram_notification_action_destination_key(recipient_user_id: int) -> str:
    normalized = _strict_positive_int(recipient_user_id)
    if normalized is None:
        raise ValueError("notification_action_recipient_user_id_invalid")
    return f"private:user:{normalized}"


def _validated_parse_mode(value: Any) -> str | None:
    if value is None:
        return None
    parse_mode = str(value).strip()
    if parse_mode not in _ALLOWED_PARSE_MODES:
        raise ValueError("notification_action_parse_mode_invalid")
    return parse_mode


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_utc_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("notification_action_not_before_invalid")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("notification_action_not_before_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("notification_action_not_before_invalid")
    return _utc(parsed)


def _reply_markup_has_persistent_menu(
    reply_markup: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(reply_markup, Mapping):
        return False
    keyboard = reply_markup.get("keyboard")
    return isinstance(keyboard, list) and bool(keyboard)


def _validate_source_contract(
    outbox: TelegramNotificationOutbox | Any,
) -> TelegramNotificationActionSource:
    policy = telegram_notification_action_policy_from_source(
        getattr(outbox, "source_type", None)
    )
    source_id = str(getattr(outbox, "source_id", "") or "").strip()
    if not source_id or len(source_id) > 120:
        raise ValueError("notification_action_source_id_invalid")
    recipient_user_id = _strict_positive_int(
        getattr(outbox, "recipient_user_id", None)
    )
    if recipient_user_id is None:
        raise ValueError("notification_action_recipient_invalid")
    expected_dedupe = telegram_notification_dedupe_key(
        source_type=policy.source_type,
        source_id=source_id,
        recipient_user_id=recipient_user_id,
    )
    dedupe_key = str(getattr(outbox, "dedupe_key", "") or "").strip()
    if dedupe_key != expected_dedupe:
        raise ValueError("notification_action_dedupe_invalid")
    text = validate_telegram_notification_text(
        str(getattr(outbox, "text", "") or "")
    )
    parse_mode = _validated_parse_mode(getattr(outbox, "parse_mode", None))
    extra_payload = getattr(outbox, "extra_payload", None)
    if not isinstance(extra_payload, Mapping):
        raise ValueError("notification_action_extra_payload_invalid")
    if str(extra_payload.get("queue_action") or "") != policy.action.value:
        raise ValueError("notification_action_extra_payload_action_mismatch")

    account_notice_kind: str | None = None
    expected_account_status: str | None = None
    expected_messenger_blocked: bool | None = None
    restriction_kind: str | None = None
    restriction_snapshot: dict[str, Any] | None = None
    deleted_account_snapshot: dict[str, str] | None = None
    not_before: datetime | None = None
    interaction_result: TelegramInteractionResultContract | None = None
    expected_user_sync_version = _strict_positive_int(
        extra_payload.get("user_sync_version")
    )
    if expected_user_sync_version is None:
        raise ValueError("notification_action_user_version_invalid")
    if policy.state_contract == "account_status":
        account_notice_kind = str(
            extra_payload.get("account_notice_kind")
            or ACCOUNT_NOTICE_KIND_STATUS
        ).strip().lower()
        if account_notice_kind == ACCOUNT_NOTICE_KIND_STATUS:
            if set(extra_payload) != {
                "account_status",
                "messenger_blocked",
                "queue_action",
                "user_sync_version",
            }:
                raise ValueError("notification_action_account_payload_invalid")
            expected_account_status = str(
                extra_payload.get("account_status") or ""
            ).strip().lower()
            if expected_account_status not in {
                UserAccountStatus.ACTIVE.value,
                UserAccountStatus.INACTIVE.value,
            }:
                raise ValueError("notification_action_account_status_invalid")
            if not isinstance(extra_payload.get("messenger_blocked"), bool):
                raise ValueError("notification_action_account_block_state_invalid")
            expected_messenger_blocked = bool(extra_payload["messenger_blocked"])
        elif account_notice_kind == ACCOUNT_NOTICE_KIND_RESTRICTION_ACTIVE:
            if set(extra_payload) != {
                "account_notice_kind",
                "queue_action",
                "restriction_kind",
                "restriction_snapshot",
                "user_sync_version",
            }:
                raise ValueError("notification_action_restriction_active_payload_invalid")
            restriction_kind = normalize_restriction_kind(
                extra_payload.get("restriction_kind")
            )
            restriction_snapshot = validate_active_restriction_snapshot(
                extra_payload.get("restriction_snapshot"),
                restriction_kind=restriction_kind,
            )
        elif account_notice_kind == ACCOUNT_NOTICE_KIND_DELETED:
            if set(extra_payload) != {
                "account_notice_kind",
                "deleted_account_snapshot",
                "queue_action",
                "user_sync_version",
            }:
                raise ValueError("notification_action_deleted_payload_invalid")
            deleted_account_snapshot = validate_deleted_account_snapshot(
                extra_payload.get("deleted_account_snapshot")
            )
        else:
            raise ValueError("notification_action_account_notice_kind_invalid")
        reply_markup = None
    elif policy.state_contract == "restriction_clear":
        if set(extra_payload) != {
            "not_before",
            "queue_action",
            "restriction_kind",
            "user_sync_version",
        }:
            raise ValueError("notification_action_restriction_payload_invalid")
        restriction_kind = str(
            extra_payload.get("restriction_kind") or ""
        ).strip().lower()
        if restriction_kind not in {"block", "limitations"}:
            raise ValueError("notification_action_restriction_kind_invalid")
        not_before = _parse_utc_datetime(extra_payload.get("not_before"))
        reply_markup = None
    else:
        allowed_keys = {"queue_action", "user_sync_version"}
        if "reply_markup" in extra_payload:
            allowed_keys.add("reply_markup")
        if "interaction_result" in extra_payload:
            allowed_keys.add("interaction_result")
        if set(extra_payload) != allowed_keys:
            raise ValueError("notification_action_extra_payload_invalid")
        raw_reply_markup = extra_payload.get("reply_markup")
        if raw_reply_markup is None:
            reply_markup = None
        elif isinstance(raw_reply_markup, Mapping):
            reply_markup = dict(raw_reply_markup)
        else:
            raise ValueError("notification_action_reply_markup_invalid")
        raw_interaction_result = extra_payload.get("interaction_result")
        if raw_interaction_result is not None:
            interaction_result = parse_interaction_result_contract(
                raw_interaction_result
            )
            if (
                interaction_result.method != "sendMessage"
                or interaction_result.destination_class
                != TelegramDestinationClass.PRIVATE
                or not interaction_result.authenticated
            ):
                raise ValueError("notification_action_interaction_route_invalid")
            if interaction_result.persistent_menu_present != (
                _reply_markup_has_persistent_menu(reply_markup)
            ):
                raise ValueError(
                    "notification_action_interaction_persistent_menu_mismatch"
                )

    payload: dict[str, Any] = {
        "chat_id": int(getattr(outbox, "telegram_id_at_enqueue", 0) or 0),
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    canonical_telegram_delivery_payload(payload)
    return TelegramNotificationActionSource(
        policy=policy,
        dedupe_key=dedupe_key,
        source_id=source_id,
        recipient_user_id=recipient_user_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        account_notice_kind=account_notice_kind,
        expected_account_status=expected_account_status,
        expected_messenger_blocked=expected_messenger_blocked,
        expected_user_sync_version=expected_user_sync_version,
        restriction_kind=restriction_kind,
        restriction_snapshot=restriction_snapshot,
        deleted_account_snapshot=deleted_account_snapshot,
        not_before=not_before,
        interaction_result=interaction_result,
    )


def telegram_notification_action_source_natural_id(
    outbox: TelegramNotificationOutbox | Any,
) -> str:
    source = _validate_source_contract(outbox)
    snapshot = json.dumps(
        {
            "account_notice_kind": source.account_notice_kind,
            "account_status": source.expected_account_status,
            "deleted_account_snapshot": source.deleted_account_snapshot,
            "dedupe_key": source.dedupe_key,
            "messenger_blocked": source.expected_messenger_blocked,
            "interaction_result": (
                serialize_interaction_result_contract(source.interaction_result)
                if source.interaction_result
                else None
            ),
            "parse_mode": source.parse_mode,
            "reply_markup": source.reply_markup,
            "template_version": source.policy.template_version,
            "text": source.text,
            "restriction_kind": source.restriction_kind,
            "restriction_snapshot": source.restriction_snapshot,
            "not_before": (
                source.not_before.isoformat() if source.not_before else None
            ),
            "user_sync_version": source.expected_user_sync_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:24]
    identity = f"{source.dedupe_key}{_SOURCE_SEPARATOR}{fingerprint}"
    if len(identity) > 256:
        raise ValueError("notification_action_source_identity_too_long")
    return identity


def telegram_notification_action_outbox_dedupe_from_source(
    source_natural_id: Any,
) -> str | None:
    identity = str(source_natural_id or "").strip()
    if _SOURCE_SEPARATOR not in identity:
        return None
    dedupe_key, fingerprint = identity.rsplit(_SOURCE_SEPARATOR, 1)
    if not dedupe_key or len(fingerprint) != 24:
        return None
    if any(character not in "0123456789abcdef" for character in fingerprint):
        return None
    return dedupe_key


def _current_account_status(user: User | Any) -> str:
    return str(
        getattr(
            getattr(user, "account_status", None),
            "value",
            getattr(user, "account_status", ""),
        )
        or ""
    ).strip().lower()


def notification_action_source_matches_current_user(
    source: TelegramNotificationActionSource,
    user: User | Any,
    *,
    now: datetime | None = None,
) -> bool:
    if source.policy.state_contract == "account_status":
        if source.account_notice_kind == ACCOUNT_NOTICE_KIND_STATUS:
            return (
                _current_account_status(user) == source.expected_account_status
                and bool(getattr(user, "messenger_blocked_at", None))
                is source.expected_messenger_blocked
            )
        current_version = _strict_positive_int(
            getattr(user, "sync_version", None)
        )
        if current_version != source.expected_user_sync_version:
            return False
        if source.account_notice_kind == ACCOUNT_NOTICE_KIND_RESTRICTION_ACTIVE:
            return bool(
                source.restriction_kind
                and source.restriction_snapshot
                and active_restriction_snapshot_matches_user(
                    source.restriction_snapshot,
                    user,
                    restriction_kind=source.restriction_kind,
                    now=now,
                )
            )
        if source.account_notice_kind == ACCOUNT_NOTICE_KIND_DELETED:
            return bool(
                source.deleted_account_snapshot
                and deleted_account_snapshot_matches_user(
                    source.deleted_account_snapshot,
                    user,
                )
            )
        return False
    if source.policy.state_contract == "restriction_clear":
        if source.restriction_kind == "block":
            restricted_until = getattr(user, "trading_restricted_until", None)
            return not isinstance(restricted_until, datetime) or _utc(
                restricted_until
            ) <= _utc(now or datetime.now(timezone.utc))
        if source.restriction_kind == "limitations":
            return all(
                getattr(user, field_name, None) is None
                for field_name in (
                    "max_daily_trades",
                    "max_active_commodities",
                    "max_daily_requests",
                )
            )
        return False
    return True


def notification_action_source_waits_for_current_user(
    source: TelegramNotificationActionSource,
    user: User | Any,
) -> bool:
    current_version = _strict_positive_int(getattr(user, "sync_version", None))
    return bool(
        current_version is not None
        and source.expected_user_sync_version is not None
        and current_version < source.expected_user_sync_version
    )


def telegram_notification_action_outbox_matches_current_user(
    outbox: TelegramNotificationOutbox | Any,
    user: User | Any,
    *,
    now: datetime | None = None,
) -> bool:
    return notification_action_source_matches_current_user(
        _validate_source_contract(outbox),
        user,
        now=now,
    )


def telegram_notification_action_outbox_waits_for_current_user(
    outbox: TelegramNotificationOutbox | Any,
    user: User | Any,
) -> bool:
    return notification_action_source_waits_for_current_user(
        _validate_source_contract(outbox),
        user,
    )


def telegram_notification_action_interaction_result_contract(
    outbox: TelegramNotificationOutbox | Any,
) -> TelegramInteractionResultContract | None:
    return _validate_source_contract(outbox).interaction_result


def telegram_notification_action_outbox_is_deleted_account_notice(
    outbox: TelegramNotificationOutbox | Any,
) -> bool:
    """Return true only for a fully validated deleted-account notice."""
    return (
        _validate_source_contract(outbox).account_notice_kind
        == ACCOUNT_NOTICE_KIND_DELETED
    )


async def telegram_notification_action_deleted_route_is_reassigned(
    db: AsyncSession,
    outbox: TelegramNotificationOutbox | Any,
) -> bool:
    """Protect a pre-delete route from being reused by another local account."""
    source = _validate_source_contract(outbox)
    if source.account_notice_kind != ACCOUNT_NOTICE_KIND_DELETED:
        return False
    telegram_id = _strict_positive_int(
        getattr(outbox, "telegram_id_at_enqueue", None)
    )
    if telegram_id is None:
        raise ValueError("notification_action_deleted_route_invalid")
    reassigned_user_id = (
        await db.execute(
            select(User.id)
            .where(
                User.telegram_id == telegram_id,
                User.id != source.recipient_user_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return reassigned_user_id is not None


def build_telegram_notification_action_snapshot(
    outbox: TelegramNotificationOutbox | Any,
    user: User | Any,
) -> TelegramNotificationActionSnapshot:
    source = _validate_source_contract(outbox)
    if _strict_positive_int(getattr(user, "id", None)) != source.recipient_user_id:
        raise ValueError("notification_action_user_mismatch")
    if source.account_notice_kind == ACCOUNT_NOTICE_KIND_DELETED:
        telegram_id = _strict_positive_int(
            getattr(outbox, "telegram_id_at_enqueue", None)
        )
    else:
        telegram_id = _strict_positive_int(getattr(user, "telegram_id", None))
    user_sync_version = _strict_positive_int(getattr(user, "sync_version", None))
    if telegram_id is None:
        raise ValueError("notification_action_current_chat_id_invalid")
    if user_sync_version is None:
        raise ValueError("notification_action_recipient_version_invalid")
    payload: dict[str, Any] = {
        "chat_id": telegram_id,
        "text": source.text,
        "parse_mode": source.parse_mode,
    }
    if source.reply_markup is not None:
        payload["reply_markup"] = source.reply_markup
    normalized_payload, payload_hash = canonical_telegram_delivery_payload(payload)
    version_snapshot = json.dumps(
        {
            "account_notice_kind": source.account_notice_kind,
            "account_status": _current_account_status(user),
            "deleted_account_snapshot": source.deleted_account_snapshot,
            "messenger_blocked": bool(getattr(user, "messenger_blocked_at", None)),
            "interaction_result": (
                serialize_interaction_result_contract(source.interaction_result)
                if source.interaction_result
                else None
            ),
            "not_before": (
                source.not_before.isoformat() if source.not_before else None
            ),
            "payload_hash": payload_hash,
            "recipient_user_id": source.recipient_user_id,
            "restriction_kind": source.restriction_kind,
            "restriction_snapshot": source.restriction_snapshot,
            "telegram_id": telegram_id,
            "user_sync_version": user_sync_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    source_version = int.from_bytes(
        hashlib.sha256(version_snapshot.encode("utf-8")).digest()[:8],
        "big",
    ) & ((1 << 63) - 1)
    return TelegramNotificationActionSnapshot(
        payload=normalized_payload,
        source_version=source_version or 1,
    )


async def _validate_interaction_anchor_freshness(
    db: AsyncSession,
    *,
    job: TelegramDeliveryJobRecord,
    outbox: TelegramNotificationOutbox,
    source: TelegramNotificationActionSource,
) -> TelegramFreshnessDecision | None:
    contract = source.interaction_result
    if (
        contract is None
        or contract.anchor_effect != TelegramInteractionAnchorEffect.SET_CURRENT
    ):
        return None
    chat_id = _strict_positive_int(getattr(outbox, "telegram_id_at_enqueue", None))
    outbox_id = _strict_positive_int(getattr(outbox, "id", None))
    if chat_id is None or outbox_id is None or contract.anchor_generation is None:
        return _quarantined("notification_action_interaction_anchor_identity_invalid")
    job_payload = getattr(job, "payload", None)
    job_chat_id = (
        _strict_positive_int(job_payload.get("chat_id"))
        if isinstance(job_payload, Mapping)
        else None
    )
    if job_chat_id is None:
        return _quarantined("notification_action_interaction_anchor_route_invalid")
    if job_chat_id != chat_id:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="notification_action_freshness_anchor_route_changed",
        )
    anchor = await db.get(TelegramInteractionAnchorState, chat_id)
    if anchor is None:
        return _quarantined("notification_action_interaction_anchor_state_missing")
    anchor_recipient_user_id = _strict_positive_int(
        getattr(anchor, "recipient_user_id", None)
    )
    if anchor_recipient_user_id is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="notification_action_freshness_recipient_missing",
        )
    if anchor_recipient_user_id != source.recipient_user_id:
        return _quarantined("notification_action_interaction_anchor_recipient_mismatch")
    if (
        _strict_positive_int(getattr(anchor, "desired_generation", None))
        != contract.anchor_generation
        or _strict_positive_int(getattr(anchor, "desired_outbox_id", None))
        != outbox_id
        or str(getattr(anchor, "desired_logical_message_key", "") or "")
        != contract.logical_message_key
    ):
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="notification_action_freshness_anchor_superseded",
        )
    return None


def _validate_static_route(
    job: TelegramDeliveryJobRecord,
) -> tuple[TelegramNotificationActionPolicy | None, TelegramFreshnessDecision | None]:
    try:
        policy = telegram_notification_action_policy(job.action_kind)
    except ValueError:
        return None, _quarantined("notification_action_freshness_action_mismatch")
    if _enum_value(job.feeder_kind) != policy.feeder.value:
        return policy, _quarantined("notification_action_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != TelegramDestinationClass.PRIVATE.value:
        return policy, _quarantined(
            "notification_action_freshness_destination_class_mismatch"
        )
    if str(job.method or "") != "sendMessage":
        return policy, _quarantined("notification_action_freshness_method_mismatch")
    if str(job.bot_identity or "") != "primary":
        return policy, _quarantined(
            "notification_action_freshness_bot_identity_mismatch"
        )
    if str(job.template_version or "") != policy.template_version:
        return policy, _quarantined("notification_action_freshness_template_mismatch")
    if (
        job.delivery_deadline_at is not None
        or job.freshness_deadline_at is not None
        or job.campaign_id is not None
        or job.run_id is not None
    ):
        return policy, _quarantined("notification_action_freshness_deadline_forbidden")
    return policy, None


async def validate_notification_action_telegram_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
) -> TelegramFreshnessDecision:
    policy, static_decision = _validate_static_route(job)
    if static_decision is not None or policy is None:
        return static_decision or _quarantined(
            "notification_action_freshness_policy_missing"
        )
    dedupe_key = telegram_notification_action_outbox_dedupe_from_source(
        job.source_natural_id
    )
    if dedupe_key is None:
        return _quarantined("notification_action_freshness_source_invalid")
    outbox = (
        await db.execute(
            select(TelegramNotificationOutbox)
            .where(TelegramNotificationOutbox.dedupe_key == dedupe_key)
            .limit(1)
        )
    ).scalar_one_or_none()
    if outbox is None:
        return _quarantined("notification_action_freshness_outbox_missing")
    try:
        source = _validate_source_contract(outbox)
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("notification_action_freshness_source_contract_invalid")
    if source.policy != policy:
        return _quarantined("notification_action_freshness_policy_mismatch")
    if _strict_positive_int(getattr(outbox, "telegram_id_at_enqueue", None)) is None:
        return _quarantined("notification_action_freshness_enqueue_identity_invalid")
    if _strict_positive_int(getattr(outbox, "queue_job_id", None)) != _strict_positive_int(
        getattr(job, "id", None)
    ):
        return _quarantined("notification_action_freshness_queue_owner_mismatch")
    if not isinstance(getattr(outbox, "queue_handed_off_at", None), datetime):
        return _quarantined("notification_action_freshness_handoff_missing")
    if str(job.destination_key or "") != telegram_notification_action_destination_key(
        source.recipient_user_id
    ):
        return _quarantined("notification_action_freshness_destination_mismatch")
    anchor_decision = await _validate_interaction_anchor_freshness(
        db,
        job=job,
        outbox=outbox,
        source=source,
    )
    if anchor_decision is not None:
        return anchor_decision

    status = _enum_value(outbox.status)
    if status == TelegramNotificationOutboxStatus.SENT.value:
        if _strict_positive_int(outbox.telegram_message_id) is None:
            return _quarantined("notification_action_freshness_sent_without_evidence")
        return _decision(
            TelegramFreshnessOutcome.SENT_NOOP,
            reason="notification_action_freshness_already_sent",
        )
    if status in _TERMINAL_STATUSES:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="notification_action_freshness_outbox_terminal",
        )
    if status not in _ACTIVE_STATUSES:
        return _quarantined("notification_action_freshness_outbox_state_invalid")
    if source.not_before is not None and _utc(now) < source.not_before:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="notification_action_freshness_not_due",
        )
    try:
        normalized_stored, stored_hash = canonical_telegram_delivery_payload(job.payload)
        expected_source = telegram_notification_action_source_natural_id(outbox)
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("notification_action_freshness_stored_payload_invalid")
    if str(job.payload_hash or "") != stored_hash:
        return _quarantined("notification_action_freshness_payload_hash_mismatch")
    if str(job.source_natural_id or "") != expected_source:
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=policy.action,
            reason="notification_action_freshness_content_changed",
        )

    user = await db.get(User, source.recipient_user_id)
    if user is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="notification_action_freshness_recipient_missing",
        )
    if (
        source.account_notice_kind == ACCOUNT_NOTICE_KIND_DELETED
        and await telegram_notification_action_deleted_route_is_reassigned(
            db,
            outbox,
        )
    ):
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="notification_action_freshness_deleted_route_reassigned",
        )
    if (
        source.account_notice_kind != ACCOUNT_NOTICE_KIND_DELETED
        and _strict_positive_int(getattr(user, "telegram_id", None)) is None
    ):
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="notification_action_freshness_recipient_unlinked",
        )
    if notification_action_source_waits_for_current_user(source, user):
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="notification_action_freshness_recipient_version_pending",
        )
    if not notification_action_source_matches_current_user(
        source,
        user,
        now=now,
    ):
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="notification_action_freshness_source_state_changed",
        )
    if (
        policy.require_bot_access
        and source.account_notice_kind != ACCOUNT_NOTICE_KIND_DELETED
    ):
        access = await evaluate_bot_access(db, user)
        if not access.allowed:
            return _decision(
                TelegramFreshnessOutcome.SUPERSEDED,
                reason="notification_action_freshness_recipient_access_denied",
            )
    try:
        current = build_telegram_notification_action_snapshot(outbox, user)
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("notification_action_freshness_current_payload_invalid")
    if (
        _strict_positive_int(job.source_version) != current.source_version
        or normalized_stored != current.payload
    ):
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=policy.action,
            reason="notification_action_freshness_recipient_route_changed",
        )
    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="notification_action_freshness_current",
    )


@dataclass(frozen=True, slots=True)
class NotificationActionTelegramDeliveryFreshnessValidator:
    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_notification_action_telegram_delivery_freshness(
            db,
            job,
            now,
        )
