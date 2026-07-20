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
    "dr_destination_cursors": _entry(
        "dr_destination_cursors", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_outbox",), "local origin producer",
        "monotonic per-origin/per-destination stream sequence", "DR ordering only",
    ),
    "dr_durability_state": _entry(
        "dr_durability_state", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_orchestrator", "durability_health_controller"), "local DR control plane",
        "signed expiring health evidence only", "write-freeze policy",
    ),
    "dr_blob_deliveries": _entry(
        "dr_blob_deliveries", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_blob_worker",), "local DR file plane",
        "destination-specific content-hash delivery", "blob transport only",
    ),
    "dr_blob_manifests": _entry(
        "dr_blob_manifests", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("webapp_upload", "dr_blob_worker"), "local DR file plane",
        "immutable SHA-256 content identity", "blob manifest only",
    ),
    "dr_blob_receipts": _entry(
        "dr_blob_receipts", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_blob_worker",), "local DR file plane",
        "verified content hash per destination", "blob parity evidence",
    ),
    "dr_file_intents": _entry(
        "dr_file_intents", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("webapp_upload",), "WebApp writer term",
        "one immutable content intent per chat file", "DB-plus-blob linkage",
    ),
    "dr_recovery_manifests": _entry(
        "dr_recovery_manifests", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_recovery_gate",), "local DR recovery control plane",
        "immutable DB/event/blob barrier hash", "promotion/failback evidence",
    ),
    "dr_conflict_quarantine": _entry(
        "dr_conflict_quarantine", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_receiver", "operator_resolution"), "local DR control plane",
        "never product-sync control rows", "DR conflict audit only",
    ),
    "dr_effect_outbox": _entry(
        "dr_effect_outbox", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_effect_worker",), "local DR effect plane",
        "never product-sync execution leases", "epoch-bound external effects",
    ),
    "dr_effect_fanouts": _entry(
        "dr_effect_fanouts", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_outbox", "dr_effect_worker"), "local DR effect plane",
        "immutable event-bound fanout intent with local expansion status",
        "transactional recipient fanout only",
    ),
    "dr_event_deliveries": _entry(
        "dr_event_deliveries", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_sender", "dr_relay"), "local DR delivery plane",
        "destination-specific immutable-event delivery", "DR transport only",
    ),
    "dr_event_receipts": _entry(
        "dr_event_receipts", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_receiver",), "local DR receipt plane",
        "never relay as a business mutation", "DR receipt evidence only",
    ),
    "dr_events": _entry(
        "dr_events", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_outbox", "dr_receiver", "dr_relay"), "immutable origin authority",
        "preserve exact event identity and bytes", "DR event truth",
    ),
    "dr_producer_cursors": _entry(
        "dr_producer_cursors", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_outbox",), "local origin producer",
        "monotonic per-site/epoch sequence", "DR ordering only",
    ),
    "dr_replay_nonces": _entry(
        "dr_replay_nonces", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_receiver",), "local DR transport",
        "unique key-id/nonce", "transport replay defence",
    ),
    "dr_stream_checkpoints": _entry(
        "dr_stream_checkpoints", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_receiver", "dr_projection_worker"), "local DR projection",
        "contiguous origin-site/epoch sequence", "gap and parity evidence",
    ),
    "dr_projection_versions": _entry(
        "dr_projection_versions", SyncPolicy.INTERNAL_BOOKKEEPING,
        ("dr_projection_worker",), "local DR projection",
        "highest applied authority term per aggregate", "stale-term suppression",
    ),
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
        "iran authoritative invitation service",
        "natural key by token plus newer authoritative sync_version",
        "registration/linking eligibility",
    ),
    "invitation_identity_reservations": _entry(
        "invitation_identity_reservations",
        SyncPolicy.NO_SYNC,
        ("iran_invitation_service", "iran_expiry_reconciliation"),
        "iran local invitation concurrency authority",
        "unique normalized mobile/account/invitation constraints; no cross-server merge",
        "pending invitation uniqueness only",
    ),
    "market_runtime_state": _entry(
        "market_runtime_state",
        SyncPolicy.SYNC,
        ("background_job", "admin"),
        "market transition authority",
        "latest transition state; explicit conflict policy needed before multi-writer changes",
        "market notice and offer expiry side effects",
    ),
    "market_channel_notice_receipts": _entry(
        "market_channel_notice_receipts",
        SyncPolicy.NO_SYNC,
        (
            "telegram_bot",
            "sync_reconciliation",
            "telegram_delivery_queue_worker",
        ),
        "foreign local Telegram side-effect ledger",
        "no cross-server merge; dedupe key only protects local Telegram notice replay",
        "foreign-only market open/close channel notice idempotency",
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
    "offer_expiry_command_receipts": _entry(
        "offer_expiry_command_receipts",
        SyncPolicy.NO_SYNC,
        ("offer_home_server",),
        "offer home server local command authority",
        "command id, idempotency key, and canonical request hash; no cross-server merge",
        "forwarded offer-expiry replay protection",
        notes="Terminal receipts are retained for 365 days; incomplete receipts are never auto-deleted.",
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
        "WebApp authority private replica",
        "WebApp-FI/WebApp-IR only; endpoint hash is the stable destination identity",
        "Web Push destination and key material; never project to Bot-FI",
        notes="Private WebApp DR state required for notification continuity after Writer promotion.",
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
    "telegram_registration_command_receipts": _entry(
        "telegram_registration_command_receipts",
        SyncPolicy.NO_SYNC,
        ("iran_registration_service",),
        "iran local registration command authority",
        "command id, idempotency key, and canonical request hash; no cross-server merge",
        "authoritative registration replay protection",
    ),
    "telegram_registration_intents": _entry(
        "telegram_registration_intents",
        SyncPolicy.NO_SYNC,
        ("foreign_telegram_bot", "foreign_registration_reconciliation"),
        "foreign local Telegram registration collection authority",
        "idempotency key and terminal local state; no cross-server merge",
        "durable Telegram proof/retry evidence only",
    ),
    "invitation_sms_deliveries": _entry(
        "invitation_sms_deliveries",
        SyncPolicy.NO_SYNC,
        ("iran_invitation_service",),
        "Iran-local Invitation SMS claim/result authority",
        "one durable status per Invitation; no cross-server merge",
        "replay-stable admin response and duplicate-send prevention",
    ),
    "telegram_admin_broadcasts": _entry(
        "telegram_admin_broadcasts",
        SyncPolicy.SYNC,
        (
            "telegram_bot_admin",
            "telegram_admin_broadcast_worker",
            "telegram_delivery_queue_worker",
        ),
        "foreign Telegram admin broadcast authority",
        "foreign creates broadcast rows; id remains partitioned and receipt rows carry dedupe identity",
        "Telegram-only management broadcast audit",
        notes="Synced non-messenger operational data; Iran must not create WebApp notifications or Telegram side effects.",
    ),
    "telegram_admin_broadcast_receipts": _entry(
        "telegram_admin_broadcast_receipts",
        SyncPolicy.SYNC,
        (
            "telegram_admin_broadcast_worker",
            "telegram_delivery_queue_worker",
        ),
        "foreign Telegram admin broadcast delivery owner",
        "dedupe key plus terminal-state precedence; local lease fields are not cross-server execution authority",
        "Telegram-only management broadcast delivery audit and repair",
        notes="Workers must execute only on foreign; synced rows on Iran are visibility/audit data only.",
    ),
    "telegram_notification_outbox": _entry(
        "telegram_notification_outbox",
        SyncPolicy.SYNC,
        (
            "webapp_notification_producer",
            "telegram_notification_outbox_worker",
            "telegram_delivery_queue_worker",
        ),
        "foreign Telegram delivery owner",
        "dedupe key plus terminal-state precedence; local lease fields are not cross-server execution authority",
        "Generic Telegram private-message notification delivery audit and repair",
        notes=(
            "Iran may enqueue rows for Telegram delivery without calling Telegram directly. "
            "Workers must execute only on foreign; synced rows on Iran are visibility/audit data only."
        ),
    ),
    "telegram_delivery_jobs": _entry(
        "telegram_delivery_jobs",
        SyncPolicy.NO_SYNC,
        ("telegram_delivery_queue",),
        "foreign local Telegram execution owner",
        "never cross-sync execution leases, attempts, payloads, or provider results",
        "Foreign-only Telegram delivery execution and audit",
        notes=(
            "Domain intent syncs through its authoritative table. This execution table is local to foreign "
            "and must never be copied to Iran."
        ),
    ),
    "telegram_delivery_provider_outcomes": _entry(
        "telegram_delivery_provider_outcomes",
        SyncPolicy.NO_SYNC,
        ("telegram_delivery_queue",),
        "foreign local Telegram provider outcome owner",
        "never cross-sync provider facts or local apply lifecycle",
        "Foreign-only replayable Telegram provider outcome inbox",
        notes=(
            "One immutable provider fact is fenced to one job lease; domain feedback may be "
            "replayed without repeating the Telegram API call."
        ),
    ),
    "telegram_delivery_reconciliation_evidence": _entry(
        "telegram_delivery_reconciliation_evidence",
        SyncPolicy.NO_SYNC,
        ("telegram_delivery_queue", "telegram_delivery_queue_operations"),
        "foreign local Telegram reconciliation audit owner",
        "append-only evidence; never cross-sync provider or operator metadata",
        "Foreign-only redacted ambiguity and retry decision audit",
        notes="Evidence references and operator references are hashed before persistence.",
    ),
    "telegram_delivery_runtime_gates": _entry(
        "telegram_delivery_runtime_gates",
        SyncPolicy.NO_SYNC,
        ("telegram_delivery_queue", "telegram_delivery_queue_operations"),
        "foreign local Telegram bot/gateway control owner",
        "never cross-sync runtime cooldown, pause, preflight, or resume journal",
        "Foreign-only durable bot/gateway execution gate",
        notes="Preflight 429 is committed here before Redis mirroring or sleep.",
    ),
    "telegram_channel_membership_sagas": _entry(
        "telegram_channel_membership_sagas",
        SyncPolicy.NO_SYNC,
        ("telegram_delivery_queue",),
        "foreign local Telegram channel-membership saga owner",
        "never cross-sync membership targets, job bindings, or provider lifecycle",
        "Foreign-only ordered ban/unban removal saga and audit",
        notes=(
            "The authoritative product intent is the synced account-status notification outbox; "
            "this derived execution saga exists only on foreign."
        ),
    ),
    "telegram_scheduled_operations": _entry(
        "telegram_scheduled_operations",
        SyncPolicy.NO_SYNC,
        ("telegram_bot_runtime", "telegram_delivery_queue"),
        "foreign local Telegram scheduled-source owner",
        "never cross-sync scheduled cleanup or market side-effect receipts",
        "Foreign-only bounded scheduled Telegram source state",
        notes=(
            "The row has no credential and is a source receipt for queue-v1; "
            "it must never be copied to Iran."
        ),
    ),
    "telegram_interaction_anchor_states": _entry(
        "telegram_interaction_anchor_states",
        SyncPolicy.NO_SYNC,
        ("telegram_bot_runtime", "telegram_delivery_queue"),
        "foreign local Telegram interaction anchor owner",
        "never cross-sync private Telegram message ids or anchor generations",
        "Foreign-only durable Bot reply-keyboard anchor state",
        notes=(
            "The state fences asynchronous send results and contains no credential; "
            "it must never be copied to Iran."
        ),
    ),
    "telegram_delivery_feeder_states": _entry(
        "telegram_delivery_feeder_states",
        SyncPolicy.NO_SYNC,
        ("telegram_delivery_queue",),
        "foreign local Telegram feeder coordinator",
        "never cross-sync queue fairness counters or feeder cursors",
        "Foreign-only Telegram subordinate-feeder scheduling state",
        notes=(
            "This row is updated atomically with foreign Telegram queue feedback "
            "and is never domain authority on Iran."
        ),
    ),
    "telegram_delivery_resume_operations": _entry(
        "telegram_delivery_resume_operations",
        SyncPolicy.NO_SYNC,
        ("telegram_delivery_queue_operations",),
        "foreign local Telegram execution control owner",
        "never cross-sync operator identity, pause evidence, or activation phases",
        "Foreign-only Telegram channel resume audit and crash recovery",
        notes=(
            "This operation is the foreign-local fail-closed boundary between PostgreSQL pause "
            "evidence, Telegram preflight, and Redis activation."
        ),
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
    "user_counter_event_receipts": _entry(
        "user_counter_event_receipts",
        SyncPolicy.NO_SYNC,
        ("counter_event_producer", "sync_receiver"),
        "local server that produces or receives the event",
        "immutable event UUID ledger; never copied as a table",
        "counter period reconstruction and replay protection only",
    ),
    "webapp_writer_state": _entry(
        "webapp_writer_state",
        SyncPolicy.INTERNAL_BOOKKEEPING,
        ("writer_control_plane", "origin_readiness"),
        "local physical-site control plane",
        "never product-sync writer leases or local routing authority rows",
        "writer fencing and public-origin eligibility only",
    ),
    "webapp_writer_activation_operations": _entry(
        "webapp_writer_activation_operations",
        SyncPolicy.INTERNAL_BOOKKEEPING,
        ("writer_control_agent",),
        "local WebApp writer control plane",
        "operation-id-bound replay receipt; never replicate as business data",
        "ambiguous Witness activation recovery only",
    ),
    "webapp_writer_transitions": _entry(
        "webapp_writer_transitions",
        SyncPolicy.INTERNAL_BOOKKEEPING,
        ("writer_control_plane", "operator_audit"),
        "local physical-site control plane",
        "append-only local audit; exchange only through signed recovery evidence",
        "promotion, demotion, and readiness audit only",
    ),
    "webapp_writer_witness_state": _entry(
        "webapp_writer_witness_state",
        SyncPolicy.INTERNAL_BOOKKEEPING,
        ("writer_witness_control_plane",),
        "Iran-reachable witness database only",
        "never product-sync or resolve this singleton through ordinary sync",
        "global writer epoch and time-bounded lease authority only",
    ),
    "webapp_writer_witness_receipts": _entry(
        "webapp_writer_witness_receipts",
        SyncPolicy.INTERNAL_BOOKKEEPING,
        ("writer_witness_control_plane", "operator_audit"),
        "Iran-reachable witness database only",
        "replay-safe local request ledger; never product-sync",
        "witness command idempotency and audit only",
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
