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
PRODUCER_ONLY = "producer-only"
API_FORBIDDEN_EXECUTION_ENV = {
    "TELEGRAM_DELIVERY_EXECUTION_OWNER": PRODUCER_ONLY,
    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
    # Producer-only API/sync processes must retain Queue-v1 routing semantics:
    # an unassigned publication is later bound to one healthy publisher lane.
    # They receive no provider tokens and execute no Telegram work themselves.
    "TELEGRAM_MULTI_PUBLISHER_ENABLED": "true",
    "TELEGRAM_B2B_DISPATCH_ENABLED": "true",
    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED": "false",
    **{
        f"TELEGRAM_PUBLISHER_{index}_ENABLED": "false"
        for index in range(1, 6)
    },
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


def bot_env_updates() -> dict[str, str]:
    return dict(BOT_REQUIRED_ENV)


def api_env_updates() -> dict[str, str]:
    updates = dict(API_REQUIRED_ENV)
    updates.update(API_FORBIDDEN_EXECUTION_ENV)
    for key in API_FORBIDDEN_TOKEN_KEYS:
        updates[key] = ""
    return updates


def legacy_runtime_env_updates() -> dict[str, str]:
    return {
        "TELEGRAM_DELIVERY_PRODUCER_MODE": LEGACY,
        "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": LEGACY,
        "TELEGRAM_DELIVERY_EXECUTION_OWNER": LEGACY,
        "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
        "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
    }


def _env_assignment(text: str, key: str) -> str | None:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            cleaned = value.strip()
            return cleaned or None
    return None


def expected_channel_id_updates(text: str) -> dict[str, str]:
    """Copy CHANNEL_ID into the Queue-v1 expected-channel lock when it is absent."""
    channel = _env_assignment(text, "CHANNEL_ID")
    expected = _env_assignment(text, "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID")
    if not channel:
        raise ValueError("telegram_channel_id_missing")
    if expected and expected != channel:
        raise ValueError("telegram_expected_channel_id_mismatch")
    if expected:
        return {}
    return {"TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": channel}


def upsert_env_lines(text: str, updates: Mapping[str, str]) -> str:
    """Replace or append env assignments without echoing values to callers."""
    lines = str(text or "").splitlines()
    seen: set[str] = set()
    rewritten: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rewritten.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            rewritten.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
        rewritten.append(line)
    for key, value in updates.items():
        if key not in seen:
            rewritten.append(f"{key}={value}")
    return "\n".join(rewritten) + "\n"


def executor_count(*, bot_running: bool, legacy_workers_enabled: bool, queue_worker_enabled: bool) -> int:
    if not bot_running:
        return 0
    if executor_overlap_forbidden(
        legacy_workers_enabled=legacy_workers_enabled,
        queue_worker_enabled=queue_worker_enabled,
    ):
        return 2
    if legacy_workers_enabled or queue_worker_enabled:
        return 1
    return 0
