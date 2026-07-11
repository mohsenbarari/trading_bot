"""Authority policy for recurring background jobs.

This matrix is deliberately code-owned, not only documented, so background
mutations cannot silently drift away from cross-server write authority rules.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, normalize_server

JOB_OFFER_EXPIRY = "offer_expiry"
JOB_MARKET_SCHEDULE = "market_schedule"
JOB_SESSION_EXPIRY = "session_expiry"
JOB_USER_ACCOUNT_STATUS = "user_account_status"
JOB_CONNECTIVITY_MONITOR = "connectivity_monitor"
JOB_SYNC_WORKER = "sync_worker"
JOB_TRADE_WEBAPP_DELIVERY = "trade_webapp_delivery"
JOB_TRADE_TELEGRAM_DELIVERY = "trade_telegram_delivery"
JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY = "telegram_admin_broadcast_delivery"
JOB_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY = "telegram_notification_outbox_delivery"
JOB_OFFER_TELEGRAM_PUBLICATION = "offer_telegram_publication"
JOB_TELEGRAM_REGISTRATION_RECONCILIATION = "telegram_registration_reconciliation"

REQUIRED_BACKGROUND_JOBS: frozenset[str] = frozenset(
    {
        JOB_OFFER_EXPIRY,
        JOB_MARKET_SCHEDULE,
        JOB_SESSION_EXPIRY,
        JOB_USER_ACCOUNT_STATUS,
        JOB_CONNECTIVITY_MONITOR,
        JOB_TRADE_WEBAPP_DELIVERY,
        JOB_TRADE_TELEGRAM_DELIVERY,
        JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY,
        JOB_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY,
        JOB_OFFER_TELEGRAM_PUBLICATION,
        JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
    }
)

_T = TypeVar("_T")


@dataclass(frozen=True)
class BackgroundJobAuthorityEntry:
    job_name: str
    mutated_tables: tuple[str, ...]
    allowed_servers: tuple[str, ...]
    authority_rule: str
    outage_behavior: str
    sync_outbox_behavior: str
    local_runtime: bool = False
    offer_impacting: bool = False
    shared_authoritative_command: str | None = None
    external_state: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackgroundJobAuthorityDecision:
    ok: bool
    job_name: str
    current_server: str
    allowed_servers: tuple[str, ...]
    reason: str | None = None

    def as_log_extra(self) -> dict[str, object]:
        return {
            "event": "background_job.authority_rejected",
            "job_name": self.job_name,
            "current_server": self.current_server,
            "allowed_servers": list(self.allowed_servers),
            "reason": self.reason,
        }


class BackgroundJobAuthorityError(RuntimeError):
    def __init__(self, decision: BackgroundJobAuthorityDecision):
        self.decision = decision
        super().__init__(
            f"background job {decision.job_name!r} is not allowed on "
            f"{decision.current_server!r}; allowed={decision.allowed_servers!r}"
        )


BACKGROUND_JOB_AUTHORITY: dict[str, BackgroundJobAuthorityEntry] = {
    JOB_OFFER_EXPIRY: BackgroundJobAuthorityEntry(
        job_name=JOB_OFFER_EXPIRY,
        mutated_tables=("offers",),
        allowed_servers=(SERVER_FOREIGN, SERVER_IRAN),
        authority_rule=(
            "offer_home_server only; query active offers where Offer.home_server == current_server "
            "and expire through the shared OfferExpiryCommand service"
        ),
        outage_behavior=(
            "expire only local-home offers during peer outage; never mutate remote-home offers; "
            "durable sync/outbox must catch the peer up after recovery"
        ),
        sync_outbox_behavior="offers are sync tables and authoritative expiry must be emitted through the durable outbox/change_log path",
        offer_impacting=True,
        shared_authoritative_command="expire_offers_authoritatively",
        side_effects=("local realtime offer:expired", "active-offer cache decrement", "foreign Telegram terminal channel state"),
    ),
    JOB_MARKET_SCHEDULE: BackgroundJobAuthorityEntry(
        job_name=JOB_MARKET_SCHEDULE,
        mutated_tables=("market_runtime_state", "offers"),
        allowed_servers=(SERVER_FOREIGN, SERVER_IRAN),
        authority_rule=(
            "iran is authoritative for market_runtime_state; foreign must only observe the synced iran runtime state "
            "and may expire offer_home_server-local active offers through the shared OfferExpiryCommand service"
        ),
        outage_behavior=(
            "iran continues schedule evaluation from committed settings/overrides; foreign preserves Telegram-local "
            "side effects and closes only foreign-home offers after a synced iran close state"
        ),
        sync_outbox_behavior=(
            "iran market_runtime_state writes and both servers' offer_home_server-local expiry writes must be recorded "
            "by the durable outbox/change_log path; foreign market notices remain local Telegram side effects"
        ),
        offer_impacting=True,
        shared_authoritative_command="expire_offers_authoritatively",
        side_effects=("market realtime events", "foreign Telegram market notice when gateway allows it", "active-offer cache decrement"),
    ),
    JOB_SESSION_EXPIRY: BackgroundJobAuthorityEntry(
        job_name=JOB_SESSION_EXPIRY,
        mutated_tables=("user_sessions",),
        allowed_servers=(SERVER_FOREIGN, SERVER_IRAN),
        authority_rule="local auth/runtime surface only; sessions are not product data and must not be cross-server merged",
        outage_behavior="continue local stale-session cleanup independently; peer outage does not change session authority",
        sync_outbox_behavior="no-sync local runtime table; do not create cross-server outbox items for session expiry",
        local_runtime=True,
    ),
    JOB_USER_ACCOUNT_STATUS: BackgroundJobAuthorityEntry(
        job_name=JOB_USER_ACCOUNT_STATUS,
        mutated_tables=("users", "notifications", "user_sessions"),
        allowed_servers=(SERVER_IRAN,),
        authority_rule=(
            "iran shared account authority finalizes inactive-user global web locks; "
            "foreign must not mutate account-status fields from this job"
        ),
        outage_behavior=(
            "run on iran only; if the foreign peer is unavailable, keep authoritative user/notification/session changes local "
            "and let durable sync replay after recovery"
        ),
        sync_outbox_behavior=(
            "users and notifications are sync tables and must flow through durable outbox/change_log; "
            "user_sessions remains no-sync local runtime"
        ),
        side_effects=("user/accountant notifications", "local session revocation", "optional Telegram notification via foreign-safe gateway path"),
    ),
    JOB_CONNECTIVITY_MONITOR: BackgroundJobAuthorityEntry(
        job_name=JOB_CONNECTIVITY_MONITOR,
        mutated_tables=(),
        allowed_servers=(SERVER_IRAN,),
        authority_rule="iran runtime connectivity probe only; foreign is not a WebApp runtime surface",
        outage_behavior="continue probing iran-to-foreign reachability and keep the Redis state local",
        sync_outbox_behavior="no-sync runtime Redis state; never create product-data outbox records",
        local_runtime=True,
        external_state=("redis:connectivity:global",),
    ),
    JOB_SYNC_WORKER: BackgroundJobAuthorityEntry(
        job_name=JOB_SYNC_WORKER,
        mutated_tables=("change_log",),
        allowed_servers=(SERVER_FOREIGN, SERVER_IRAN),
        authority_rule="local committed outbox delivery worker; it sends local authoritative changes to the peer",
        outage_behavior="keep items pending/retryable while the peer is unavailable; do not mark delivered until peer acceptance",
        sync_outbox_behavior="internal bookkeeping only; mark local change_log rows delivered after successful peer receive",
        local_runtime=True,
        external_state=("redis:sync:outbound", "redis:sync:retry"),
    ),
    JOB_TRADE_WEBAPP_DELIVERY: BackgroundJobAuthorityEntry(
        job_name=JOB_TRADE_WEBAPP_DELIVERY,
        mutated_tables=("trade_delivery_receipts", "notifications"),
        allowed_servers=(SERVER_IRAN,),
        authority_rule=(
            "iran-only WebApp delivery worker; claims only receipt rows whose destination_server is iran "
            "and channel is webapp"
        ),
        outage_behavior=(
            "repair due local WebApp receipts from durable trade_delivery_receipts; skip expired remote-outage "
            "deliveries by receipt outage policy"
        ),
        sync_outbox_behavior=(
            "trade_delivery_receipts and notifications are synced non-messenger operational data; "
            "worker_id and lease_until remain local execution fields"
        ),
        side_effects=("local realtime notification event", "local Web Push notification"),
    ),
    JOB_TRADE_TELEGRAM_DELIVERY: BackgroundJobAuthorityEntry(
        job_name=JOB_TRADE_TELEGRAM_DELIVERY,
        mutated_tables=("trade_delivery_receipts",),
        allowed_servers=(SERVER_FOREIGN,),
        authority_rule=(
            "foreign-only Telegram delivery worker; claims only receipt rows whose destination_server is "
            "foreign and channel is telegram"
        ),
        outage_behavior=(
            "retry transient Telegram/network failures; skip unusable linked Telegram identities without "
            "creating permanent user-level disablement"
        ),
        sync_outbox_behavior=(
            "trade_delivery_receipts are synced non-messenger operational data; worker_id and lease_until "
            "remain local execution fields"
        ),
        external_state=("Telegram Bot API",),
        side_effects=("Telegram private trade message",),
    ),
    JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY: BackgroundJobAuthorityEntry(
        job_name=JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY,
        mutated_tables=("telegram_admin_broadcasts", "telegram_admin_broadcast_receipts"),
        allowed_servers=(SERVER_FOREIGN,),
        authority_rule=(
            "foreign-only Telegram admin broadcast worker; claims only local Telegram broadcast receipt rows "
            "and sends direct bot messages through the Telegram gateway"
        ),
        outage_behavior=(
            "retry transient Telegram/network failures; skip unreachable or no-longer-eligible recipients "
            "without creating WebApp notifications or messenger rows"
        ),
        sync_outbox_behavior=(
            "telegram_admin_broadcasts and telegram_admin_broadcast_receipts are synced non-messenger "
            "operational data; worker_id and lease_until remain local execution fields"
        ),
        external_state=("Telegram Bot API",),
        side_effects=("Telegram private admin broadcast message",),
    ),
    JOB_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY: BackgroundJobAuthorityEntry(
        job_name=JOB_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY,
        mutated_tables=("telegram_notification_outbox",),
        allowed_servers=(SERVER_FOREIGN,),
        authority_rule=(
            "foreign-only generic Telegram notification outbox worker; claims local synced outbox rows "
            "and sends private bot messages through the Telegram gateway"
        ),
        outage_behavior=(
            "Iran may enqueue durable notification rows; foreign sends them after sync when available. "
            "Transient Telegram/network failures remain retryable; unreachable or no-longer-eligible users are skipped."
        ),
        sync_outbox_behavior=(
            "telegram_notification_outbox is synced non-messenger operational data; worker_id and lease_until "
            "remain local execution fields"
        ),
        external_state=("Telegram Bot API",),
        side_effects=("Telegram private generic notification message",),
    ),
    JOB_OFFER_TELEGRAM_PUBLICATION: BackgroundJobAuthorityEntry(
        job_name=JOB_OFFER_TELEGRAM_PUBLICATION,
        mutated_tables=("offers", "offer_publication_states"),
        allowed_servers=(SERVER_FOREIGN,),
        authority_rule=(
            "foreign-only Telegram channel publication reconciler; publishes active offers missing "
            "Telegram publication state through the idempotent offer_publication_states gate and reapplies "
            "Telegram channel presentation for already-published partial or terminal offers"
        ),
        outage_behavior=(
            "skip active publication while the medium/long outage active-publication gate is enabled; "
            "otherwise repair missing Telegram channel posts and converge existing channel message buttons/tags "
            "after sync or staging shared-DB changes"
        ),
        sync_outbox_behavior=(
            "offer_publication_states and the legacy offers.channel_message_id backfill are sync-visible "
            "product surface state and must converge through the existing change_log path; Telegram message "
            "text/markup edits are external presentation side effects and must not mutate offer ownership"
        ),
        offer_impacting=True,
        external_state=("Telegram Bot API",),
        side_effects=("Telegram channel offer post", "Telegram channel offer message text/markup"),
    ),
    JOB_TELEGRAM_REGISTRATION_RECONCILIATION: BackgroundJobAuthorityEntry(
        job_name=JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
        mutated_tables=("telegram_registration_intents",),
        allowed_servers=(SERVER_FOREIGN,),
        authority_rule=(
            "foreign-only local intent claimant; submit stable signed commands to Iran and finalize "
            "only after required synced projections are locally visible"
        ),
        outage_behavior=(
            "retain ready/retryable intents with bounded backoff while Iran or sync is unavailable; "
            "never create a foreign User or grant access from an HTTP response alone"
        ),
        sync_outbox_behavior=(
            "telegram_registration_intents is foreign-local no-sync state; accepted Iran User, "
            "Invitation, and Relation mutations converge through the existing sync worker"
        ),
        local_runtime=True,
        external_state=("Iran signed registration endpoint",),
    ),
}


def background_job_authority_entries() -> dict[str, BackgroundJobAuthorityEntry]:
    return dict(BACKGROUND_JOB_AUTHORITY)


def get_background_job_authority_entry(job_name: str) -> BackgroundJobAuthorityEntry:
    return BACKGROUND_JOB_AUTHORITY[job_name]


def check_background_job_authority(
    job_name: str,
    *,
    server_mode: str | None = None,
) -> BackgroundJobAuthorityDecision:
    normalized_job_name = str(job_name or "").strip()
    server = normalize_server(server_mode, current_server()) if server_mode is not None else current_server()
    entry = BACKGROUND_JOB_AUTHORITY.get(normalized_job_name)
    if entry is None:
        return BackgroundJobAuthorityDecision(
            ok=False,
            job_name=normalized_job_name,
            current_server=server,
            allowed_servers=(),
            reason="unknown_background_job",
        )
    if server not in entry.allowed_servers:
        return BackgroundJobAuthorityDecision(
            ok=False,
            job_name=entry.job_name,
            current_server=server,
            allowed_servers=entry.allowed_servers,
            reason="background_job_not_allowed_on_server",
        )
    return BackgroundJobAuthorityDecision(
        ok=True,
        job_name=entry.job_name,
        current_server=server,
        allowed_servers=entry.allowed_servers,
    )


def assert_background_job_authority(
    job_name: str,
    *,
    server_mode: str | None = None,
) -> BackgroundJobAuthorityDecision:
    decision = check_background_job_authority(job_name, server_mode=server_mode)
    if not decision.ok:
        raise BackgroundJobAuthorityError(decision)
    return decision


def filter_allowed_background_job_factories(
    factories: Iterable[tuple[str, _T]],
    *,
    server_mode: str | None = None,
    on_rejected: Callable[[BackgroundJobAuthorityDecision], None] | None = None,
) -> list[tuple[str, _T]]:
    allowed: list[tuple[str, _T]] = []
    for job_name, factory in factories:
        decision = check_background_job_authority(job_name, server_mode=server_mode)
        if decision.ok:
            allowed.append((job_name, factory))
            continue
        if on_rejected is not None:
            on_rejected(decision)
    return allowed
