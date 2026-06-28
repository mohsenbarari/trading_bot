"""Starter sync policy registry for model table inventory coverage."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

import models  # noqa: F401 - ensure all model modules register their tables
from core.admin_authority import ADMIN_SHARED_TABLES
from models.database import Base


class SyncPolicy(str, Enum):
    SYNC = "sync"
    NO_SYNC = "no-sync"
    INTERNAL_BOOKKEEPING = "internal-bookkeeping"


@dataclass(frozen=True)
class SyncRegistryEntry:
    table_name: str
    policy: SyncPolicy
    write_surfaces: tuple[str, ...]
    authority: str
    conflict_rule: str
    side_effect_classification: str
    planned: bool = False
    notes: str = ""


def _entry(
    table_name: str,
    policy: SyncPolicy,
    write_surfaces: tuple[str, ...],
    authority: str,
    conflict_rule: str,
    side_effect_classification: str,
    *,
    planned: bool = False,
    notes: str = "",
) -> SyncRegistryEntry:
    return SyncRegistryEntry(
        table_name=table_name,
        policy=policy,
        write_surfaces=write_surfaces,
        authority=authority,
        conflict_rule=conflict_rule,
        side_effect_classification=side_effect_classification,
        planned=planned,
        notes=notes,
    )


_SYNC_REGISTRY: dict[str, SyncRegistryEntry] = {
    "accountant_relations": _entry(
        "accountant_relations",
        SyncPolicy.SYNC,
        ("webapp", "admin"),
        "relationship owner/admin authority",
        "natural relationship identity; explicit review before destructive merge",
        "account/profile visibility and chat permission recalculation",
    ),
    "admin_broadcast_messages": _entry(
        "admin_broadcast_messages",
        SyncPolicy.SYNC,
        ("webapp_admin",),
        "iran shared admin authority",
        "single-writer iran admin authority; non-authoritative writes must fail visibly",
        "notification/broadcast audit visibility",
    ),
    "admin_market_messages": _entry(
        "admin_market_messages",
        SyncPolicy.SYNC,
        ("webapp_admin",),
        "iran shared admin authority",
        "single-writer iran admin authority; non-authoritative writes must fail visibly",
        "market notice visibility and notification fanout",
    ),
    "change_log": _entry(
        "change_log",
        SyncPolicy.INTERNAL_BOOKKEEPING,
        ("sync_outbox",),
        "local server",
        "never cross-sync bookkeeping rows",
        "sync observability only",
    ),
    "chat_files": _entry(
        "chat_files",
        SyncPolicy.NO_SYNC,
        ("webapp_messenger", "upload_runtime"),
        "iran local messenger runtime",
        "no cross-server merge",
        "iran upload/media runtime only",
    ),
    "chat_members": _entry(
        "chat_members",
        SyncPolicy.NO_SYNC,
        ("webapp_messenger", "local_system_projection"),
        "iran/local projection",
        "no wholesale cross-server merge; mandatory-channel compatibility is transitional",
        "iran realtime only",
    ),
    "chats": _entry(
        "chats",
        SyncPolicy.NO_SYNC,
        ("webapp_messenger", "local_system_projection"),
        "iran/local projection",
        "no wholesale cross-server merge; mandatory-channel compatibility is transitional",
        "iran realtime only",
    ),
    "commodities": _entry(
        "commodities",
        SyncPolicy.SYNC,
        ("webapp_admin", "telegram_bot_admin"),
        "iran shared admin authority",
        "single-writer iran admin authority; commodity names are natural-idempotency guards only",
        "commodity cache invalidation",
    ),
    "commodity_aliases": _entry(
        "commodity_aliases",
        SyncPolicy.SYNC,
        ("webapp_admin", "telegram_bot_admin"),
        "iran shared admin authority",
        "single-writer iran admin authority; aliases are natural-idempotency guards only",
        "commodity cache invalidation",
    ),
    "conversations": _entry(
        "conversations",
        SyncPolicy.NO_SYNC,
        ("webapp_messenger",),
        "iran local messenger runtime",
        "no cross-server merge",
        "iran realtime only",
    ),
    "customer_relations": _entry(
        "customer_relations",
        SyncPolicy.SYNC,
        ("webapp", "admin"),
        "relationship owner/admin authority",
        "natural relationship identity; explicit review before destructive merge",
        "customer permission, pricing, and audit visibility",
    ),
    "invitations": _entry(
        "invitations",
        SyncPolicy.SYNC,
        ("admin", "webapp", "bot_link"),
        "invitation creator/admin authority",
        "natural key merge by token",
        "registration/linking eligibility",
    ),
    "market_runtime_state": _entry(
        "market_runtime_state",
        SyncPolicy.SYNC,
        ("background_job", "admin"),
        "market transition authority",
        "latest transition state; explicit conflict policy needed before multi-writer changes",
        "market notice and offer expiry side effects",
    ),
    "market_schedule_overrides": _entry(
        "market_schedule_overrides",
        SyncPolicy.SYNC,
        ("webapp_admin",),
        "iran shared admin authority",
        "single-writer iran admin authority; date uniqueness is an idempotency guard only",
        "market schedule cache and transition recalculation",
    ),
    "messages": _entry(
        "messages",
        SyncPolicy.NO_SYNC,
        ("webapp_messenger",),
        "iran local messenger runtime",
        "no cross-server merge",
        "iran realtime only",
    ),
    "notifications": _entry(
        "notifications",
        SyncPolicy.SYNC,
        ("webapp", "bot", "background_job", "admin"),
        "notification producer authority",
        "record identity/idempotent producer result",
        "notification relay, unread counts, and Web Push routing",
    ),
    "offer_publication_states": _entry(
        "offer_publication_states",
        SyncPolicy.SYNC,
        ("publication_worker", "reconciliation_job", "webapp_realtime"),
        "surface publication authority plus product owner authority for terminal state",
        "dedupe key and latest terminal state; must not rewrite offer business truth",
        "operator visibility and publication reconciliation",
        notes=(
            "Shared Step 7A surface state for Telegram channel and WebApp market visibility. "
            "Business parity is dedupe/status/version/owner based; Telegram message ids and "
            "provider diagnostics are local runtime evidence, not business truth."
        ),
    ),
    "offer_requests": _entry(
        "offer_requests",
        SyncPolicy.SYNC,
        ("webapp", "telegram_bot", "internal_forward"),
        "offer_home_server",
        "idempotent authoritative command result; terminal ledger rows immutable except safe finalization",
        "authorized offer detail/audit visibility",
        notes="Durable request ledger; public offer-link views must apply field-level visibility policy.",
    ),
    "offers": _entry(
        "offers",
        SyncPolicy.SYNC,
        ("webapp", "telegram_bot", "internal_sync"),
        "offer_home_server",
        "command-forward for mutations; public identity becomes canonical cross-server key",
        "iran WebApp event and foreign Telegram publication",
    ),
    "push_subscriptions": _entry(
        "push_subscriptions",
        SyncPolicy.NO_SYNC,
        ("webapp_browser_runtime",),
        "iran local browser runtime",
        "no cross-server merge",
        "iran Web Push runtime only",
    ),
    "session_login_requests": _entry(
        "session_login_requests",
        SyncPolicy.NO_SYNC,
        ("webapp_auth_runtime",),
        "local surface",
        "no cross-server merge",
        "local login flow only",
    ),
    "single_session_recovery_admin_targets": _entry(
        "single_session_recovery_admin_targets",
        SyncPolicy.NO_SYNC,
        ("webapp_auth_runtime",),
        "local surface",
        "no cross-server merge",
        "local recovery flow only",
    ),
    "single_session_recovery_requests": _entry(
        "single_session_recovery_requests",
        SyncPolicy.NO_SYNC,
        ("webapp_auth_runtime",),
        "local surface",
        "no cross-server merge",
        "local recovery flow only",
    ),
    "sync_blocks": _entry(
        "sync_blocks",
        SyncPolicy.INTERNAL_BOOKKEEPING,
        ("sync_operations",),
        "local server",
        "never cross-sync bookkeeping rows",
        "sync blocking/observability only",
    ),
    "sync_apply_watermarks": _entry(
        "sync_apply_watermarks",
        SyncPolicy.INTERNAL_BOOKKEEPING,
        ("sync_receiver",),
        "local server",
        "never cross-sync receiver watermark rows",
        "sync source-sequence/idempotency observability only",
    ),
    "trades": _entry(
        "trades",
        SyncPolicy.SYNC,
        ("webapp", "telegram_bot", "internal_forward"),
        "offer_home_server",
        "shared idempotent command; forward to offer_home_server when remote",
        "notifications and offer update events",
    ),
    "trade_delivery_receipts": _entry(
        "trade_delivery_receipts",
        SyncPolicy.SYNC,
        ("webapp_delivery_worker", "telegram_delivery_worker", "reconciliation_job"),
        "destination_server delivery owner",
        "dedupe key plus terminal-state precedence; local lease fields are not cross-server execution authority",
        "trade notification delivery audit and repair",
        notes="Delivery receipts are non-messenger operational data. Workers must execute only local destination_server rows.",
    ),
    "telegram_link_tokens": _entry(
        "telegram_link_tokens",
        SyncPolicy.SYNC,
        ("webapp_account_settings", "bot_link"),
        "user/account authority",
        "single-use token hash; pending records are revoked per user before issuing a new token",
        "Telegram account linking eligibility and audit",
    ),
    "trading_settings": _entry(
        "trading_settings",
        SyncPolicy.SYNC,
        ("webapp_admin", "telegram_bot_admin"),
        "iran shared admin authority",
        "single-writer iran admin authority; setting key uniqueness is an idempotency guard only",
        "settings cache refresh",
    ),
    "upload_batches": _entry(
        "upload_batches",
        SyncPolicy.NO_SYNC,
        ("webapp_messenger", "upload_runtime"),
        "iran local upload runtime",
        "no cross-server merge",
        "iran upload runtime only",
    ),
    "upload_sessions": _entry(
        "upload_sessions",
        SyncPolicy.NO_SYNC,
        ("webapp_messenger", "upload_runtime"),
        "iran local upload runtime",
        "no cross-server merge",
        "iran upload runtime only",
    ),
    "user_blocks": _entry(
        "user_blocks",
        SyncPolicy.SYNC,
        ("webapp", "admin"),
        "user relationship authority",
        "unique blocker/blocked relationship; natural merge by pair planned",
        "trade/request eligibility",
    ),
    "user_notification_preferences": _entry(
        "user_notification_preferences",
        SyncPolicy.SYNC,
        ("webapp_account_settings",),
        "user/account authority",
        "updated_at/version merge TBD before receiver enablement",
        "notification routing policy",
        notes="Sync candidate from roadmap; receiver behavior is not enabled by Step 2A.",
    ),
    "user_sessions": _entry(
        "user_sessions",
        SyncPolicy.NO_SYNC,
        ("webapp_auth_runtime",),
        "local surface",
        "no cross-server merge",
        "local session events only",
    ),
    "users": _entry(
        "users",
        SyncPolicy.SYNC,
        ("webapp_admin", "telegram_bot_admin", "auth", "bot_link", "webapp"),
        "field-level account authority; admin product fields use iran shared admin authority",
        "admin role/status/limit writes are single-writer iran authority; account identity uses field-level merge",
        "profile/account product data: telegram_id, account status, role, limits, and counters",
        notes="user.home_server is legacy/account-origin compatibility only and must not represent current active runtime surface.",
    ),
}


def sync_registry_entries(*, include_planned: bool = False) -> dict[str, SyncRegistryEntry]:
    if include_planned:
        return dict(_SYNC_REGISTRY)
    return {
        table_name: entry
        for table_name, entry in _SYNC_REGISTRY.items()
        if not entry.planned
    }


def admin_mutated_shared_registry_entries() -> dict[str, SyncRegistryEntry]:
    return {table_name: _SYNC_REGISTRY[table_name] for table_name in sorted(ADMIN_SHARED_TABLES)}


def get_sync_registry_entry(table_name: str) -> SyncRegistryEntry:
    return _SYNC_REGISTRY[table_name]


def model_table_names() -> set[str]:
    return set(Base.metadata.tables.keys())


def missing_registry_tables(table_names: Iterable[str]) -> set[str]:
    registered = set(sync_registry_entries(include_planned=False))
    return set(table_names) - registered


def unregistered_model_tables() -> set[str]:
    return missing_registry_tables(model_table_names())
