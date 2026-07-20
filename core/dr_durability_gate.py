"""Conservative data-class RPO enforcement during isolation/ambiguity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import text

from core.config import settings
from core.dr_connectivity_classifier import (
    ConnectivityClassification,
    ConnectivityEvidencePolicy,
    classify_connectivity,
)


class DrDurabilityGateError(RuntimeError):
    """Raised when an acknowledged critical write would exceed safe RPO."""


FINANCIAL_TABLES = frozenset(
    {"offers", "offer_requests", "trades", "trade_delivery_receipts"}
)
IDENTITY_TABLES = frozenset(
    {
        "accountant_relations",
        "customer_relations",
        "invitations",
        "telegram_link_tokens",
        "user_blocks",
        "users",
    }
)
MESSENGER_TABLES = frozenset(
    {
        "chat_files",
        "chat_members",
        "chats",
        "conversations",
        "messages",
        "upload_batches",
        "upload_sessions",
    }
)
BLOB_TABLES = frozenset({"chat_files"})


@dataclass(frozen=True)
class DurabilityDecision:
    allowed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DurabilityStateUpdate:
    classification: ConnectivityClassification
    evidence_expires_at: datetime
    updated_by: str


def build_connectivity_state_update(
    rounds: list[dict[str, Any]],
    *,
    policy: ConnectivityEvidencePolicy,
    operator: str,
    now: datetime | None = None,
    ttl_seconds: int = 60,
) -> DurabilityStateUpdate:
    normalized_operator = str(operator or "").strip()
    if not normalized_operator or len(normalized_operator) > 128:
        raise DrDurabilityGateError("durability-state operator identity is invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ttl = int(ttl_seconds)
    if ttl < 10 or ttl > 300:
        raise DrDurabilityGateError("durability-state evidence TTL must be 10..300 seconds")
    classification = classify_connectivity(
        rounds,
        policy=policy,
        now=current,
        max_age_seconds=300,
    )
    return DurabilityStateUpdate(
        classification=classification,
        evidence_expires_at=current + timedelta(seconds=ttl),
        updated_by=normalized_operator,
    )


def decide_durability(
    *,
    table_names: Iterable[str],
    connectivity_mode: str,
    event_journal_healthy: bool,
    blob_journal_healthy: bool,
    evidence_expires_at: datetime | None,
    now: datetime,
    isolated_critical_write_policy: str = "freeze",
) -> DurabilityDecision:
    tables = set(table_names)
    critical = tables & (FINANCIAL_TABLES | IDENTITY_TABLES | MESSENGER_TABLES)
    if not critical:
        return DurabilityDecision(True, ())
    reasons: list[str] = []
    expiry = evidence_expires_at
    if expiry is None:
        reasons.append("durability_evidence_missing")
    else:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
            reasons.append("durability_evidence_expired")
    if not event_journal_healthy:
        reasons.append("same_region_event_journal_unhealthy")
    if tables & BLOB_TABLES and not blob_journal_healthy:
        reasons.append("same_region_blob_journal_unhealthy")
    if connectivity_mode == "online":
        return DurabilityDecision(not reasons, tuple(reasons))
    if connectivity_mode == "ambiguous":
        reasons.append("connectivity_ambiguous")
    if connectivity_mode == "isolated" and isolated_critical_write_policy != "same_region_sync":
        reasons.append("isolated_critical_writes_not_owner_approved")
    if connectivity_mode not in {"isolated", "ambiguous"}:
        reasons.append("connectivity_mode_invalid")
    return DurabilityDecision(not reasons, tuple(reasons))


def enforce_session_durability(session, table_names: Iterable[str]) -> None:  # noqa: ANN001
    row = session.connection().execute(
        text(
            """
            SELECT connectivity_mode, event_journal_healthy, blob_journal_healthy,
                   evidence_expires_at
            FROM dr_durability_state
            WHERE singleton_id = 1
            FOR SHARE
            """
        )
    ).mappings().one_or_none()
    if row is None:
        raise DrDurabilityGateError("DR durability state is missing")
    decision = decide_durability(
        table_names=table_names,
        connectivity_mode=row["connectivity_mode"],
        event_journal_healthy=bool(row["event_journal_healthy"]),
        blob_journal_healthy=bool(row["blob_journal_healthy"]),
        evidence_expires_at=row["evidence_expires_at"],
        now=datetime.now(timezone.utc),
        isolated_critical_write_policy=str(
            getattr(settings, "dr_isolated_critical_write_policy", "freeze") or "freeze"
        ),
    )
    if not decision.allowed:
        raise DrDurabilityGateError(
            "critical write frozen by DR durability policy: " + ",".join(decision.reasons)
        )
