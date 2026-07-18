"""Fail-closed Telegram identity, channel, and permission preflight."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import hashlib
import math
from typing import Any

from core import telegram_gateway
from core.services.telegram_delivery_queue_service import (
    TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY,
    TELEGRAM_PRIMARY_BOT_IDENTITY,
)
from core.telegram_delivery_credentials import TelegramDeliveryCredentialRegistry


TelegramPreflightGatewayCall = Callable[
    ...,
    Awaitable[telegram_gateway.TelegramGatewayResult],
]

_ADMIN_PERMISSION_FIELDS = (
    "can_manage_chat",
    "can_delete_messages",
    "can_manage_video_chats",
    "can_restrict_members",
    "can_promote_members",
    "can_change_info",
    "can_invite_users",
    "can_post_stories",
    "can_edit_stories",
    "can_delete_stories",
    "can_post_messages",
    "can_edit_messages",
    "can_pin_messages",
    "can_manage_topics",
    "can_manage_direct_messages",
    "can_manage_tags",
)
_EDITOR_ALLOWED_TRUE_PERMISSIONS = frozenset(
    {"can_manage_chat", "can_edit_messages"}
)
_REQUIRED_ADMIN_BOOLEAN_FIELDS = (
    "can_be_edited",
    "is_anonymous",
    "can_manage_chat",
    "can_delete_messages",
    "can_manage_video_chats",
    "can_restrict_members",
    "can_promote_members",
    "can_change_info",
    "can_invite_users",
    "can_post_stories",
    "can_edit_stories",
    "can_delete_stories",
)


class TelegramDeliveryPreflightConfigurationError(ValueError):
    """Raised before network access when expected identities are incomplete."""


class TelegramDeliveryPreflightFailedError(RuntimeError):
    """Raised when Telegram readback does not match the approved environment."""


class TelegramDeliveryPreflightRateLimitedError(
    TelegramDeliveryPreflightFailedError
):
    """Carries Telegram's authoritative preflight retry delay without provider text."""

    def __init__(
        self,
        reason: str,
        *,
        retry_after_seconds: float,
        bot_identity: str | None = None,
        method: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.retry_after_seconds = retry_after_seconds
        self.bot_identity = bot_identity
        self.method = method


@dataclass(frozen=True, slots=True)
class TelegramDeliveryPreflightIdentityReport:
    bot_identity: str
    credential_fingerprint: str
    bot_fingerprint: str
    channel_fingerprint: str
    member_status: str
    effective_permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TelegramDeliveryPreflightReport:
    approved_bot_identities: tuple[str, ...]
    channel_fingerprint: str
    identities: tuple[TelegramDeliveryPreflightIdentityReport, ...]


def _positive_int(value: Any, *, reason: str) -> int:
    if isinstance(value, bool) or (
        isinstance(value, float) and not value.is_integer()
    ):
        raise TelegramDeliveryPreflightConfigurationError(reason)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramDeliveryPreflightConfigurationError(reason) from exc
    if parsed <= 0:
        raise TelegramDeliveryPreflightConfigurationError(reason)
    return parsed


def _nonzero_chat_id(value: Any, *, reason: str) -> int:
    if isinstance(value, bool) or (
        isinstance(value, float) and not value.is_integer()
    ):
        raise TelegramDeliveryPreflightConfigurationError(reason)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramDeliveryPreflightConfigurationError(reason) from exc
    if parsed == 0:
        raise TelegramDeliveryPreflightConfigurationError(reason)
    return parsed


def _provider_int(value: Any, *, role: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelegramDeliveryPreflightFailedError(
            f"telegram_preflight_malformed_integer:{role}:{field}"
        )
    return value


def _fingerprint(kind: str, value: int) -> str:
    material = f"telegram-preflight-v1:{kind}:{value}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def _result_payload(
    gateway_result: telegram_gateway.TelegramGatewayResult,
    *,
    role: str,
    method: str,
) -> Mapping[str, Any]:
    try:
        result_method = gateway_result.method
        status_code = gateway_result.status_code
        result_ok = gateway_result.ok
        body = gateway_result.response_json
    except Exception as exc:
        raise TelegramDeliveryPreflightFailedError(
            f"telegram_preflight_readback_failed:{role}:{method}"
        ) from exc
    if (
        result_method == method
        and status_code == 429
        and not result_ok
        and isinstance(body, Mapping)
        and body.get("error_code") == 429
        and isinstance(body.get("parameters"), Mapping)
    ):
        raw_retry_after = body["parameters"].get("retry_after")
        if not isinstance(raw_retry_after, bool):
            try:
                retry_after = float(raw_retry_after)
            except (TypeError, ValueError, OverflowError):
                retry_after = 0.0
            if math.isfinite(retry_after) and retry_after > 0:
                raise TelegramDeliveryPreflightRateLimitedError(
                    f"telegram_preflight_rate_limited:{role}:{method}",
                    retry_after_seconds=retry_after,
                    bot_identity=role,
                    method=method,
                )
    if (
        result_method != method
        or status_code != 200
        or not result_ok
        or not isinstance(body, Mapping)
        or body.get("ok") is not True
        or not isinstance(body.get("result"), Mapping)
    ):
        raise TelegramDeliveryPreflightFailedError(
            f"telegram_preflight_readback_failed:{role}:{method}"
        )
    return body["result"]


async def _readback(
    call: TelegramPreflightGatewayCall,
    *,
    role: str,
    method: str,
    payload: Mapping[str, Any],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    try:
        result = await call(
            method,
            dict(payload),
            timeout=timeout_seconds,
            idempotency_key=f"telegram-preflight:{role}:{method}",
        )
    except Exception as exc:
        raise TelegramDeliveryPreflightFailedError(
            f"telegram_preflight_transport_failed:{role}:{method}:{type(exc).__name__}"
        ) from exc
    return _result_payload(result, role=role, method=method)


def _permission_readback(
    member: Mapping[str, Any],
    *,
    role: str,
) -> tuple[str, ...]:
    for field in _REQUIRED_ADMIN_BOOLEAN_FIELDS:
        if not isinstance(member.get(field), bool):
            raise TelegramDeliveryPreflightFailedError(
                f"telegram_preflight_malformed_permission:{role}:{field}"
            )
    permission_fields = {
        field
        for field in member
        if field.startswith("can_") and field != "can_be_edited"
    }
    for field in permission_fields:
        value = member.get(field)
        if not isinstance(value, bool):
            raise TelegramDeliveryPreflightFailedError(
                f"telegram_preflight_malformed_permission:{role}:{field}"
            )
    ordered_known = [
        field for field in _ADMIN_PERMISSION_FIELDS if member.get(field) is True
    ]
    unknown_enabled = sorted(
        field
        for field in permission_fields - set(_ADMIN_PERMISSION_FIELDS)
        if member.get(field) is True
    )
    return tuple((*ordered_known, *unknown_enabled))


def _validate_permissions(
    member: Mapping[str, Any],
    *,
    role: str,
    effective_permissions: tuple[str, ...],
) -> None:
    if str(member.get("status") or "") != "administrator":
        raise TelegramDeliveryPreflightFailedError(
            f"telegram_preflight_not_administrator:{role}"
        )
    if member.get("is_anonymous") is not False:
        raise TelegramDeliveryPreflightFailedError(
            f"telegram_preflight_anonymous_administrator:{role}"
        )
    permissions = frozenset(effective_permissions)
    if "can_manage_chat" not in permissions:
        raise TelegramDeliveryPreflightFailedError(
            f"telegram_preflight_manage_permission_missing:{role}"
        )
    if role == TELEGRAM_PRIMARY_BOT_IDENTITY:
        required = {"can_post_messages", "can_edit_messages"}
        if not required.issubset(permissions):
            raise TelegramDeliveryPreflightFailedError(
                "telegram_preflight_primary_permissions_missing"
            )
        return
    if role == TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY:
        if "can_edit_messages" not in permissions:
            raise TelegramDeliveryPreflightFailedError(
                "telegram_preflight_editor_edit_permission_missing"
            )
        excess = permissions - _EDITOR_ALLOWED_TRUE_PERMISSIONS
        if excess:
            raise TelegramDeliveryPreflightFailedError(
                "telegram_preflight_editor_permissions_excessive"
            )
        return
    raise TelegramDeliveryPreflightFailedError(
        "telegram_preflight_bot_identity_not_allowlisted"
    )


async def run_telegram_delivery_preflight(
    *,
    credential_registry: TelegramDeliveryCredentialRegistry,
    channel_id: Any,
    expected_channel_id: Any,
    expected_primary_bot_id: Any,
    editor_enabled: bool,
    expected_editor_bot_id: Any = None,
    timeout_seconds: float = 10.0,
    gateway_calls: Mapping[str, TelegramPreflightGatewayCall] | None = None,
    bot_identities: tuple[str, ...] | None = None,
    identity_only_bot_identities: tuple[str, ...] = (),
) -> TelegramDeliveryPreflightReport:
    configured_channel_id = _nonzero_chat_id(
        channel_id,
        reason="telegram_preflight_channel_id_missing",
    )
    approved_channel_id = _nonzero_chat_id(
        expected_channel_id,
        reason="telegram_preflight_expected_channel_id_missing",
    )
    if configured_channel_id != approved_channel_id:
        raise TelegramDeliveryPreflightConfigurationError(
            "telegram_preflight_channel_configuration_mismatch"
        )
    primary_bot_id = _positive_int(
        expected_primary_bot_id,
        reason="telegram_preflight_expected_primary_bot_id_missing",
    )
    expected_ids = {TELEGRAM_PRIMARY_BOT_IDENTITY: primary_bot_id}
    if editor_enabled:
        expected_ids[TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY] = _positive_int(
            expected_editor_bot_id,
            reason="telegram_preflight_expected_editor_bot_id_missing",
        )
        if expected_ids[TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY] == primary_bot_id:
            raise TelegramDeliveryPreflightConfigurationError(
                "telegram_preflight_bot_ids_must_be_distinct"
            )
    expected_roles = tuple(expected_ids)
    if credential_registry.bot_identities != expected_roles:
        raise TelegramDeliveryPreflightConfigurationError(
            "telegram_preflight_credential_roles_mismatch"
        )
    selected_roles = expected_roles if bot_identities is None else tuple(bot_identities)
    if (
        not selected_roles
        or len(set(selected_roles)) != len(selected_roles)
        or any(role not in expected_ids for role in selected_roles)
    ):
        raise TelegramDeliveryPreflightConfigurationError(
            "telegram_preflight_selected_roles_invalid"
        )
    identity_only_roles = tuple(identity_only_bot_identities)
    if (
        len(set(identity_only_roles)) != len(identity_only_roles)
        or any(role not in selected_roles for role in identity_only_roles)
        or any(role != TELEGRAM_PRIMARY_BOT_IDENTITY for role in identity_only_roles)
    ):
        raise TelegramDeliveryPreflightConfigurationError(
            "telegram_preflight_identity_only_roles_invalid"
        )
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        raise TelegramDeliveryPreflightConfigurationError(
            "telegram_preflight_timeout_invalid"
        )

    calls = dict(
        credential_registry.build_gateway_calls()
        if gateway_calls is None
        else gateway_calls
    )
    if set(calls) != set(expected_roles) or not all(
        callable(calls.get(role)) for role in expected_roles
    ):
        raise TelegramDeliveryPreflightConfigurationError(
            "telegram_preflight_gateway_roles_mismatch"
        )

    channel_fingerprint = _fingerprint("channel", approved_channel_id)
    identity_reports: list[TelegramDeliveryPreflightIdentityReport] = []
    actual_bot_ids: set[int] = set()
    for role in selected_roles:
        expected_bot_id = expected_ids[role]
        call = calls[role]
        bot = await _readback(
            call,
            role=role,
            method="getMe",
            payload={},
            timeout_seconds=timeout,
        )
        actual_bot_id = _provider_int(bot.get("id"), role=role, field="bot_id")
        if bot.get("is_bot") is not True or actual_bot_id != expected_bot_id:
            raise TelegramDeliveryPreflightFailedError(
                f"telegram_preflight_bot_identity_mismatch:{role}"
            )
        if actual_bot_id in actual_bot_ids:
            raise TelegramDeliveryPreflightFailedError(
                "telegram_preflight_duplicate_bot_identity"
            )
        actual_bot_ids.add(actual_bot_id)

        if role in identity_only_roles:
            credential = credential_registry.resolve(role)
            identity_reports.append(
                TelegramDeliveryPreflightIdentityReport(
                    bot_identity=role,
                    credential_fingerprint=credential.fingerprint,
                    bot_fingerprint=_fingerprint("bot", actual_bot_id),
                    channel_fingerprint=channel_fingerprint,
                    member_status="durable_destination_pause",
                    effective_permissions=(),
                )
            )
            continue

        chat = await _readback(
            call,
            role=role,
            method="getChat",
            payload={"chat_id": configured_channel_id},
            timeout_seconds=timeout,
        )
        actual_chat_id = _provider_int(chat.get("id"), role=role, field="chat_id")
        if actual_chat_id != approved_channel_id or chat.get("type") != "channel":
            raise TelegramDeliveryPreflightFailedError(
                f"telegram_preflight_channel_identity_mismatch:{role}"
            )

        member = await _readback(
            call,
            role=role,
            method="getChatMember",
            payload={"chat_id": configured_channel_id, "user_id": actual_bot_id},
            timeout_seconds=timeout,
        )
        member_user = member.get("user")
        if not isinstance(member_user, Mapping):
            raise TelegramDeliveryPreflightFailedError(
                f"telegram_preflight_member_identity_missing:{role}"
            )
        member_bot_id = _provider_int(
            member_user.get("id"),
            role=role,
            field="member_bot_id",
        )
        if member_bot_id != actual_bot_id or member_user.get("is_bot") is not True:
            raise TelegramDeliveryPreflightFailedError(
                f"telegram_preflight_member_identity_mismatch:{role}"
            )
        effective_permissions = _permission_readback(member, role=role)
        _validate_permissions(
            member,
            role=role,
            effective_permissions=effective_permissions,
        )
        credential = credential_registry.resolve(role)
        identity_reports.append(
            TelegramDeliveryPreflightIdentityReport(
                bot_identity=role,
                credential_fingerprint=credential.fingerprint,
                bot_fingerprint=_fingerprint("bot", actual_bot_id),
                channel_fingerprint=channel_fingerprint,
                member_status="administrator",
                effective_permissions=effective_permissions,
            )
        )

    return TelegramDeliveryPreflightReport(
        approved_bot_identities=selected_roles,
        channel_fingerprint=channel_fingerprint,
        identities=tuple(identity_reports),
    )


async def run_configured_telegram_delivery_preflight(
    *,
    settings: Any,
    credential_registry: TelegramDeliveryCredentialRegistry,
    bot_identities: tuple[str, ...] | None = None,
    identity_only_bot_identities: tuple[str, ...] = (),
) -> TelegramDeliveryPreflightReport:
    editor_enabled = bool(
        getattr(settings, "telegram_delivery_queue_channel_editor_enabled", False)
    )
    return await run_telegram_delivery_preflight(
        credential_registry=credential_registry,
        channel_id=getattr(settings, "channel_id", None),
        expected_channel_id=getattr(
            settings,
            "telegram_delivery_queue_expected_channel_id",
            None,
        ),
        expected_primary_bot_id=getattr(
            settings,
            "telegram_delivery_queue_expected_primary_bot_id",
            None,
        ),
        editor_enabled=editor_enabled,
        expected_editor_bot_id=getattr(
            settings,
            "telegram_delivery_queue_expected_channel_editor_bot_id",
            None,
        ),
        timeout_seconds=getattr(
            settings,
            "telegram_delivery_queue_preflight_timeout_seconds",
            10.0,
        ),
        bot_identities=bot_identities,
        identity_only_bot_identities=identity_only_bot_identities,
    )
