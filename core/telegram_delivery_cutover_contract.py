"""Fail-closed process-role contract for Telegram Queue-v1 cutover."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


QUEUE_V1 = "queue-v1"
LEGACY = "legacy"

API_REQUIRED_ENV = {
    "TELEGRAM_DELIVERY_PRODUCER_MODE": QUEUE_V1,
    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": QUEUE_V1,
}
API_FORBIDDEN_EXECUTION_ENV = {
    "TELEGRAM_DELIVERY_EXECUTION_OWNER": LEGACY,
    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
}
BOT_REQUIRED_ENV = {
    "TELEGRAM_DELIVERY_PRODUCER_MODE": QUEUE_V1,
    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": QUEUE_V1,
    "TELEGRAM_DELIVERY_EXECUTION_OWNER": QUEUE_V1,
    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
    "TELEGRAM_MULTI_PUBLISHER_ENABLED": "true",
    "TELEGRAM_B2B_DISPATCH_ENABLED": "true",
}
API_FORBIDDEN_TOKEN_KEYS = (
    "BOT_TOKEN",
    "TELEGRAM_PUBLISHER_1_BOT_TOKEN",
    "TELEGRAM_PUBLISHER_2_BOT_TOKEN",
    "TELEGRAM_PUBLISHER_3_BOT_TOKEN",
    "TELEGRAM_PUBLISHER_4_BOT_TOKEN",
    "TELEGRAM_PUBLISHER_5_BOT_TOKEN",
    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
    "TELEGRAM_MONITORING_BOT_TOKEN",
)
STAGING_COMPOSE_PROJECTS = frozenset(
    {"trading_bot_staging", "trading_bot_staging_iran"}
)
PRODUCTION_COMPOSE_PROJECTS = frozenset({"trading_bot"})


@dataclass(frozen=True, slots=True)
class TelegramDeliveryProcessContract:
    role: str
    required: Mapping[str, str]
    forbidden_token_keys: tuple[str, ...]


def api_process_contract() -> TelegramDeliveryProcessContract:
    required = dict(API_REQUIRED_ENV)
    required.update(API_FORBIDDEN_EXECUTION_ENV)
    return TelegramDeliveryProcessContract(
        role="api",
        required=required,
        forbidden_token_keys=API_FORBIDDEN_TOKEN_KEYS,
    )


def bot_process_contract() -> TelegramDeliveryProcessContract:
    return TelegramDeliveryProcessContract(
        role="bot",
        required=dict(BOT_REQUIRED_ENV),
        forbidden_token_keys=(),
    )


def missing_required_env(
    observed: Mapping[str, str | None],
    contract: TelegramDeliveryProcessContract,
) -> tuple[str, ...]:
    missing: list[str] = []
    for key, expected in contract.required.items():
        actual = str(observed.get(key) or "").strip()
        if actual.lower() != expected.lower():
            missing.append(key)
    return tuple(missing)


def present_forbidden_tokens(
    observed_present: Mapping[str, bool],
    contract: TelegramDeliveryProcessContract,
) -> tuple[str, ...]:
    return tuple(
        key
        for key in contract.forbidden_token_keys
        if bool(observed_present.get(key))
    )


def executor_overlap_forbidden(
    *,
    legacy_workers_enabled: bool,
    queue_worker_enabled: bool,
) -> bool:
    return bool(legacy_workers_enabled) and bool(queue_worker_enabled)
