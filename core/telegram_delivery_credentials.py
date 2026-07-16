"""Fail-closed credential registry for Telegram delivery execution lanes."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
from types import MappingProxyType
from typing import Any

from pydantic import SecretStr

from core import telegram_gateway
from core.services.telegram_delivery_queue_service import (
    SUPPORTED_TELEGRAM_BOT_IDENTITIES,
    TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY,
    TELEGRAM_PRIMARY_BOT_IDENTITY,
)


class TelegramDeliveryCredentialConfigurationError(RuntimeError):
    """Raised before worker creation when a lane credential is unsafe or missing."""


def _secret_value(value: Any) -> str:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return str(value or "").strip()


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class TelegramDeliveryCredential:
    bot_identity: str
    token: str = field(repr=False)
    fingerprint: str


@dataclass(frozen=True, slots=True)
class TelegramDeliveryCredentialRegistry:
    _credentials: Mapping[str, TelegramDeliveryCredential] = field(repr=False)

    @classmethod
    def from_values(
        cls,
        *,
        primary_token: Any,
        editor_enabled: bool,
        editor_token: Any = None,
    ) -> "TelegramDeliveryCredentialRegistry":
        primary = _secret_value(primary_token)
        if not primary:
            raise TelegramDeliveryCredentialConfigurationError(
                "primary_telegram_credential_missing"
            )
        credentials = {
            TELEGRAM_PRIMARY_BOT_IDENTITY: TelegramDeliveryCredential(
                bot_identity=TELEGRAM_PRIMARY_BOT_IDENTITY,
                token=primary,
                fingerprint=_token_fingerprint(primary),
            )
        }
        if editor_enabled:
            editor = _secret_value(editor_token)
            if not editor:
                raise TelegramDeliveryCredentialConfigurationError(
                    "channel_editor_telegram_credential_missing"
                )
            editor_fingerprint = _token_fingerprint(editor)
            if editor_fingerprint == credentials[TELEGRAM_PRIMARY_BOT_IDENTITY].fingerprint:
                raise TelegramDeliveryCredentialConfigurationError(
                    "telegram_lane_credentials_must_be_distinct"
                )
            credentials[TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY] = (
                TelegramDeliveryCredential(
                    bot_identity=TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY,
                    token=editor,
                    fingerprint=editor_fingerprint,
                )
            )
        return cls(_credentials=MappingProxyType(credentials))

    @property
    def bot_identities(self) -> tuple[str, ...]:
        return tuple(self._credentials)

    def resolve(self, bot_identity: str) -> TelegramDeliveryCredential:
        identity = str(bot_identity or "").strip()
        if identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
            raise TelegramDeliveryCredentialConfigurationError(
                "telegram_bot_identity_not_allowlisted"
            )
        credential = self._credentials.get(identity)
        if credential is None:
            raise TelegramDeliveryCredentialConfigurationError(
                f"telegram_lane_credential_not_enabled:{identity}"
            )
        return credential

    def fingerprints(self) -> dict[str, str]:
        return {
            identity: credential.fingerprint
            for identity, credential in self._credentials.items()
        }

    def build_gateway_calls(self) -> dict[str, Any]:
        calls: dict[str, Any] = {}
        for identity in self.bot_identities:
            credential = self.resolve(identity)

            async def call(
                method,
                payload,
                *,
                timeout=10,
                idempotency_key=None,
                _credential=credential,
            ):
                return await telegram_gateway.post_telegram_method(
                    method,
                    payload,
                    timeout=timeout,
                    bot_token=_credential.token,
                    idempotency_key=idempotency_key,
                )

            calls[identity] = call
        return calls


def configured_telegram_delivery_credentials(settings: Any) -> TelegramDeliveryCredentialRegistry:
    return TelegramDeliveryCredentialRegistry.from_values(
        primary_token=getattr(settings, "bot_token", None),
        editor_enabled=bool(
            getattr(settings, "telegram_delivery_queue_channel_editor_enabled", False)
        ),
        editor_token=getattr(
            settings,
            "telegram_delivery_queue_channel_editor_bot_token",
            None,
        ),
    )
