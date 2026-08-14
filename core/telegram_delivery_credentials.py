"""Fail-closed credential and publisher-lane registry for Telegram delivery."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
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
from core.telegram_multi_publisher_contract import (
    TELEGRAM_PUBLISHER_IDENTITIES,
    TELEGRAM_PUBLISHER_PRE_FLIGHT_CAPABILITIES,
)


class TelegramDeliveryCredentialConfigurationError(RuntimeError):
    """Raised before worker creation when a lane credential is unsafe or missing."""


class TelegramPublisherLaneHealthState(str, Enum):
    DISABLED = "disabled"
    UNVERIFIED = "unverified"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


def _secret_value(value: Any) -> str:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return str(value or "").strip()


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _positive_bot_id(value: Any, *, identity: str) -> int:
    if isinstance(value, bool):
        raise TelegramDeliveryCredentialConfigurationError(
            f"telegram_publisher_expected_bot_id_invalid:{identity}"
        )
    try:
        bot_id = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramDeliveryCredentialConfigurationError(
            f"telegram_publisher_expected_bot_id_invalid:{identity}"
        ) from exc
    if bot_id <= 0:
        raise TelegramDeliveryCredentialConfigurationError(
            f"telegram_publisher_expected_bot_id_invalid:{identity}"
        )
    return bot_id


def normalize_telegram_bot_username(value: Any, *, identity: str) -> str:
    username = str(value or "").strip().lstrip("@").lower()
    if (
        not 5 <= len(username) <= 64
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in username)
    ):
        raise TelegramDeliveryCredentialConfigurationError(
            f"telegram_publisher_expected_username_invalid:{identity}"
        )
    return username


@dataclass(frozen=True, slots=True)
class TelegramDeliveryCredential:
    bot_identity: str
    token: str = field(repr=False)
    fingerprint: str


@dataclass(frozen=True, slots=True)
class TelegramPublisherLaneConfiguration:
    token: Any = field(repr=False)
    expected_bot_id: Any
    expected_username: Any
    enabled: bool = True
    capabilities: frozenset[str] = TELEGRAM_PUBLISHER_PRE_FLIGHT_CAPABILITIES


@dataclass(frozen=True, slots=True)
class TelegramPublisherLane:
    bot_identity: str
    credential: TelegramDeliveryCredential = field(repr=False)
    expected_bot_id: int
    expected_username: str
    capabilities: frozenset[str]
    health_state: TelegramPublisherLaneHealthState = (
        TelegramPublisherLaneHealthState.UNVERIFIED
    )


@dataclass(frozen=True, slots=True)
class TelegramDeliveryCredentialRegistry:
    _credentials: Mapping[str, TelegramDeliveryCredential] = field(repr=False)
    _publisher_lanes: Mapping[str, TelegramPublisherLane] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )

    @classmethod
    def from_values(
        cls,
        *,
        primary_token: Any,
        editor_enabled: bool,
        editor_token: Any = None,
        publisher_lanes: Mapping[str, TelegramPublisherLaneConfiguration] | None = None,
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

        configured_lanes = (
            None if publisher_lanes is None else dict(publisher_lanes)
        )
        if configured_lanes is None:
            return cls(_credentials=MappingProxyType(credentials))
        if set(configured_lanes) != set(TELEGRAM_PUBLISHER_IDENTITIES):
            raise TelegramDeliveryCredentialConfigurationError(
                "telegram_publisher_lane_set_invalid"
            )

        lanes: dict[str, TelegramPublisherLane] = {}
        fingerprints = {
            credential.fingerprint for credential in credentials.values()
        }
        expected_bot_ids: set[int] = set()
        expected_usernames: set[str] = set()
        for identity in TELEGRAM_PUBLISHER_IDENTITIES:
            raw = configured_lanes[identity]
            if not isinstance(raw, TelegramPublisherLaneConfiguration):
                raise TelegramDeliveryCredentialConfigurationError(
                    f"telegram_publisher_lane_configuration_invalid:{identity}"
                )
            if raw.enabled is not True:
                raise TelegramDeliveryCredentialConfigurationError(
                    f"telegram_publisher_lane_disabled:{identity}"
                )
            token = _secret_value(raw.token)
            if not token:
                raise TelegramDeliveryCredentialConfigurationError(
                    f"telegram_publisher_credential_missing:{identity}"
                )
            fingerprint = _token_fingerprint(token)
            if fingerprint in fingerprints:
                raise TelegramDeliveryCredentialConfigurationError(
                    "telegram_lane_credentials_must_be_distinct"
                )
            expected_bot_id = _positive_bot_id(raw.expected_bot_id, identity=identity)
            expected_username = normalize_telegram_bot_username(
                raw.expected_username,
                identity=identity,
            )
            capabilities = frozenset(raw.capabilities)
            if not TELEGRAM_PUBLISHER_PRE_FLIGHT_CAPABILITIES.issubset(capabilities):
                raise TelegramDeliveryCredentialConfigurationError(
                    f"telegram_publisher_lane_capability_incomplete:{identity}"
                )
            if expected_bot_id in expected_bot_ids or expected_username in expected_usernames:
                raise TelegramDeliveryCredentialConfigurationError(
                    "telegram_publisher_lane_identity_not_distinct"
                )
            credential = TelegramDeliveryCredential(
                bot_identity=identity,
                token=token,
                fingerprint=fingerprint,
            )
            credentials[identity] = credential
            lanes[identity] = TelegramPublisherLane(
                bot_identity=identity,
                credential=credential,
                expected_bot_id=expected_bot_id,
                expected_username=expected_username,
                capabilities=capabilities,
            )
            fingerprints.add(fingerprint)
            expected_bot_ids.add(expected_bot_id)
            expected_usernames.add(expected_username)
        return cls(
            _credentials=MappingProxyType(credentials),
            _publisher_lanes=MappingProxyType(lanes),
        )

    @property
    def bot_identities(self) -> tuple[str, ...]:
        return tuple(self._credentials)

    @property
    def publisher_lanes(self) -> Mapping[str, TelegramPublisherLane]:
        return self._publisher_lanes

    @property
    def publisher_bot_identities(self) -> tuple[str, ...]:
        return tuple(self._publisher_lanes)

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

    def publisher_lane(self, bot_identity: str) -> TelegramPublisherLane:
        identity = str(bot_identity or "").strip()
        lane = self._publisher_lanes.get(identity)
        if lane is None:
            raise TelegramDeliveryCredentialConfigurationError(
                f"telegram_publisher_lane_not_enabled:{identity}"
            )
        return lane

    def fingerprints(self) -> dict[str, str]:
        return {
            identity: credential.fingerprint
            for identity, credential in self._credentials.items()
        }

    def build_gateway_calls(self) -> dict[str, Any]:
        def bind(credential: TelegramDeliveryCredential):
            async def call(
                method,
                payload,
                *,
                timeout=10,
                idempotency_key=None,
            ):
                return await telegram_gateway.post_telegram_method(
                    method,
                    payload,
                    timeout=timeout,
                    bot_token=credential.token,
                    idempotency_key=idempotency_key,
                )

            return call

        calls: dict[str, Any] = {}
        for identity in self.bot_identities:
            credential = self.resolve(identity)
            calls[identity] = bind(credential)
        return calls


def _configured_publisher_lanes(
    settings: Any,
) -> dict[str, TelegramPublisherLaneConfiguration]:
    lanes: dict[str, TelegramPublisherLaneConfiguration] = {}
    for index, identity in enumerate(TELEGRAM_PUBLISHER_IDENTITIES, start=1):
        prefix = f"telegram_publisher_{index}"
        lanes[identity] = TelegramPublisherLaneConfiguration(
            enabled=bool(getattr(settings, f"{prefix}_enabled", False)),
            token=getattr(settings, f"{prefix}_bot_token", None),
            expected_bot_id=getattr(settings, f"{prefix}_expected_bot_id", None),
            expected_username=getattr(settings, f"{prefix}_expected_username", None),
        )
    return lanes


def configured_telegram_delivery_credentials(settings: Any) -> TelegramDeliveryCredentialRegistry:
    multi_publisher_enabled = bool(
        getattr(settings, "telegram_multi_publisher_enabled", False)
    )
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
        publisher_lanes=(
            _configured_publisher_lanes(settings)
            if multi_publisher_enabled
            else None
        ),
    )
