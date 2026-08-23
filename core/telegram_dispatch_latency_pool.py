"""Role-sized database pools derived from Telegram slot concurrency.

Numbers come from Settings defaults and the lane slot plan. They are not
observed wait-for-connection percentiles.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.config import Settings
from core.telegram_bot_runtime_role import (
    TELEGRAM_BOT_RUNTIME_ROLE_ALL,
    TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
    TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS,
)
from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES


POOL_SCHEMA_VERSION = "telegram_dispatch_latency_pool_v1"

# Each queue slot opens one session at a time and commits before the next step.
SESSIONS_PER_SLOT = 1
# The queue owner advisory lock holds one connection for the process lifetime.
OWNER_LEASE_CONNECTIONS = 1
# Reserved concurrent private-update sessions on the central bot. Not a live p95.
PRIMARY_UPDATE_HEADROOM = 8
# Reserved B2B/command sessions on the publisher process.
PUBLISHER_COMMAND_HEADROOM = 2
# Overlapping feeder, recovery, reconciliation, and retention checkouts.
SUPERVISOR_BURST = 3

# Compose ceilings already on the tree. They are kept because they cover the
# required counts below; they were not guessed downward.
PRODUCTION_ALL_POOL_SIZE = 15
PRODUCTION_ALL_MAX_OVERFLOW = 10
PRODUCTION_PUBLISHERS_POOL_SIZE = 10
PRODUCTION_PUBLISHERS_MAX_OVERFLOW = 8
RECOMMENDED_PRIMARY_POOL_SIZE = 12
RECOMMENDED_PRIMARY_MAX_OVERFLOW = 8
POSTGRES_ADMIN_RESERVE = 50


@dataclass(frozen=True, slots=True)
class TelegramRolePoolNeed:
    role: str
    slot_count: int
    sessions_per_slot: int
    reserved_connections: int
    required_connections: int
    configured_pool_size: int
    configured_max_overflow: int

    @property
    def configured_ceiling(self) -> int:
        return self.configured_pool_size + self.configured_max_overflow

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["configured_ceiling"] = self.configured_ceiling
        return payload


@dataclass(frozen=True, slots=True)
class TelegramDispatchLatencyPoolLock:
    schema_version: str
    evidence_kind: str
    live_wait_samples_collected: bool
    postgres_max_connections: int
    postgres_admin_reserve: int
    roles: tuple[TelegramRolePoolNeed, ...]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_kind": self.evidence_kind,
            "live_wait_samples_collected": self.live_wait_samples_collected,
            "postgres_max_connections": self.postgres_max_connections,
            "postgres_admin_reserve": self.postgres_admin_reserve,
            "roles": [role.as_dict() for role in self.roles],
            "notes": list(self.notes),
        }


def _default(name: str) -> Any:
    return Settings.model_fields[name].default


def primary_slot_count() -> int:
    return max(2, int(_default("telegram_delivery_queue_primary_concurrency")))


def publisher_slot_count() -> int:
    per_lane = max(1, int(_default("telegram_multi_publisher_lane_concurrency")))
    return len(TELEGRAM_PUBLISHER_IDENTITIES) * per_lane


def channel_editor_slot_count() -> int:
    if not bool(_default("telegram_delivery_queue_channel_editor_enabled")):
        return 0
    return max(1, int(_default("telegram_delivery_queue_channel_editor_concurrency")))


def slot_count_for_role(role: str) -> int:
    slots = 0
    if role in {TELEGRAM_BOT_RUNTIME_ROLE_ALL, TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY}:
        slots += primary_slot_count() + channel_editor_slot_count()
    if role in {TELEGRAM_BOT_RUNTIME_ROLE_ALL, TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS}:
        slots += publisher_slot_count()
    return slots


def reserved_connections_for_role(role: str) -> int:
    reserved = OWNER_LEASE_CONNECTIONS + SUPERVISOR_BURST
    if role in {TELEGRAM_BOT_RUNTIME_ROLE_ALL, TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY}:
        reserved += PRIMARY_UPDATE_HEADROOM
    if role in {TELEGRAM_BOT_RUNTIME_ROLE_ALL, TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS}:
        reserved += PUBLISHER_COMMAND_HEADROOM
    return reserved


def required_connections_for_role(role: str) -> int:
    return slot_count_for_role(role) * SESSIONS_PER_SLOT + reserved_connections_for_role(
        role
    )


def _role_need(
    role: str,
    *,
    pool_size: int,
    max_overflow: int,
) -> TelegramRolePoolNeed:
    return TelegramRolePoolNeed(
        role=role,
        slot_count=slot_count_for_role(role),
        sessions_per_slot=SESSIONS_PER_SLOT,
        reserved_connections=reserved_connections_for_role(role),
        required_connections=required_connections_for_role(role),
        configured_pool_size=pool_size,
        configured_max_overflow=max_overflow,
    )


def production_role_pools() -> tuple[TelegramRolePoolNeed, ...]:
    return (
        _role_need(
            TELEGRAM_BOT_RUNTIME_ROLE_ALL,
            pool_size=PRODUCTION_ALL_POOL_SIZE,
            max_overflow=PRODUCTION_ALL_MAX_OVERFLOW,
        ),
        _role_need(
            TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
            pool_size=RECOMMENDED_PRIMARY_POOL_SIZE,
            max_overflow=RECOMMENDED_PRIMARY_MAX_OVERFLOW,
        ),
        _role_need(
            TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS,
            pool_size=PRODUCTION_PUBLISHERS_POOL_SIZE,
            max_overflow=PRODUCTION_PUBLISHERS_MAX_OVERFLOW,
        ),
    )


def role_ceilings_fit_postgres(roles: tuple[TelegramRolePoolNeed, ...]) -> bool:
    budget = int(_default("postgres_max_connections")) - POSTGRES_ADMIN_RESERVE
    # Split deploy runs primary+publishers. Combined `all` is the other shape.
    split_total = sum(
        role.configured_ceiling
        for role in roles
        if role.role
        in {
            TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
            TELEGRAM_BOT_RUNTIME_ROLE_PUBLISHERS,
        }
    )
    all_total = next(
        role.configured_ceiling
        for role in roles
        if role.role == TELEGRAM_BOT_RUNTIME_ROLE_ALL
    )
    return max(split_total, all_total) <= budget


def locked_telegram_dispatch_pools() -> TelegramDispatchLatencyPoolLock:
    roles = production_role_pools()
    return TelegramDispatchLatencyPoolLock(
        schema_version=POOL_SCHEMA_VERSION,
        evidence_kind="code_derived_role_pool_lock",
        live_wait_samples_collected=False,
        postgres_max_connections=int(_default("postgres_max_connections")),
        postgres_admin_reserve=POSTGRES_ADMIN_RESERVE,
        roles=roles,
        notes=(
            "Each queue slot holds one session at a time; the count is not guessed.",
            "Primary update headroom is a reserved budget, not a live wait sample.",
            "Compose keeps the current ceilings because they already cover the required count.",
            "Settings db_pool_size stays 15 so API and the default role=all process do not shrink.",
        ),
    )
