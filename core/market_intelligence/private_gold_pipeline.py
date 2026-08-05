"""Caller-driven private-gold staging, promotion, and paper-minute pipeline.

This module is deliberately transport-free.  It accepts already-routed private
payload envelopes, retains raw text only in the bounded staging database, then
commits privacy-minimized facts and closed paper-minute quotes to Market Store.
It never opens Telegram, schedules itself, or reads configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import sqlite3
from typing import Iterable

from .market_contracts import normalize_utc
from .private_gold import (
    PRIVATE_GOLD_SOURCE_CODE,
    refresh_private_gold_paper_minute,
)
from .private_gold_payloads import (
    PrivateGoldPayloadEnvelope,
    PrivateGoldPayloadStageReport,
    stage_private_gold_payload,
)
from .private_gold_staging import (
    PrivateGoldPromotionReport,
    PRIVATE_GOLD_STAGING_RETENTION,
    promote_private_gold_staging,
    purge_expired_private_gold_staging,
)


PRIVATE_GOLD_PIPELINE_VERSION = "private-gold-pipeline-v1"
_PAPER_FORMS = frozenset({"PAPER_NORMAL", "PAPER_REVERSE", "PAPER_SWIM"})


@dataclass(frozen=True, slots=True)
class PrivateGoldPipelineReport:
    """Counters only; it intentionally has no raw text or private identity."""

    envelope_count: int
    decoded_offers: int
    decoded_trade_updates: int
    staged_offer_changes: int
    staged_trade_changes: int
    invalid_payload_items: int
    conflicting_payload_items: int
    rejected_by_staging: int
    promotion: PrivateGoldPromotionReport
    refreshed_paper_minutes: int
    expired_staging_rows_purged: int


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _paper_minutes_to_refresh(
    connection: sqlite3.Connection,
    *,
    as_of_utc: str,
) -> tuple[tuple[str, str, str], ...]:
    """Find only still-reconcilable raw-paper minutes, bounded by retention.

    An update can arrive after its minute closed.  Rebuilding every minute in
    the short raw-retention window lets a late verified trade correct its
    weighted quote without ever reopening unrelated historic observations.
    """

    as_of = _as_datetime(as_of_utc)
    cutoff = (as_of - PRIVATE_GOLD_STAGING_RETENTION).replace(microsecond=0)
    rows = connection.execute(
        """
        SELECT DISTINCT
            settlement_term,
            trade_form,
            substr(event_time_utc, 1, 16) || ':00Z' AS minute_utc
        FROM market_observations
        WHERE source_code = ?
          AND instrument = 'MELTED_GOLD_PRIVATE'
          AND trade_form IN ('PAPER_NORMAL', 'PAPER_REVERSE', 'PAPER_SWIM')
          AND event_type IN ('OFFER', 'TRADE')
          AND quality_state = 'ELIGIBLE'
          AND is_conditional = 0
          AND event_time_utc >= ?
          AND event_time_utc <= ?
          AND available_at_utc <= ?
        ORDER BY minute_utc, settlement_term, trade_form
        """,
        (
            PRIVATE_GOLD_SOURCE_CODE,
            cutoff.isoformat().replace("+00:00", "Z"),
            as_of_utc,
            as_of_utc,
        ),
    ).fetchall()
    result: list[tuple[str, str, str]] = []
    for row in rows:
        trade_form = str(row["trade_form"])
        if trade_form not in _PAPER_FORMS:
            continue
        minute = str(row["minute_utc"])
        minute_end = _as_datetime(minute).replace(second=59)
        if minute_end > as_of:
            continue
        result.append((str(row["settlement_term"]), trade_form.removeprefix("PAPER_"), minute))
    return tuple(result)


def process_private_gold_payloads(
    staging_connection: sqlite3.Connection,
    market_connection: sqlite3.Connection,
    envelopes: Iterable[PrivateGoldPayloadEnvelope],
    *,
    as_of_utc: datetime | str,
) -> PrivateGoldPipelineReport:
    """Process a bounded batch idempotently; the caller owns both commits.

    If the two SQLite commits cannot both complete, callers must retain the
    staging commit and retry this function.  Raw records are idempotent and
    normalized facts have opaque deterministic keys, so that retry cannot
    double-count data.
    """

    as_of = normalize_utc(as_of_utc, field_name="private_gold_pipeline_as_of_utc")
    reports: list[PrivateGoldPayloadStageReport] = []
    for envelope in envelopes:
        reports.append(stage_private_gold_payload(staging_connection, envelope, staged_at_utc=as_of))
    promotion = promote_private_gold_staging(
        staging_connection,
        market_connection,
        as_of_utc=as_of,
    )
    refreshed = 0
    for settlement, variant, minute in _paper_minutes_to_refresh(market_connection, as_of_utc=as_of):
        refreshed += int(
            refresh_private_gold_paper_minute(
                market_connection,
                settlement_term=settlement,
                paper_variant=variant,
                minute_utc=minute,
                available_at_utc=as_of,
            )
            is not None
        )
    purged = purge_expired_private_gold_staging(staging_connection, as_of_utc=as_of)
    return PrivateGoldPipelineReport(
        envelope_count=len(reports),
        decoded_offers=sum(report.decoded_offers for report in reports),
        decoded_trade_updates=sum(report.decoded_trade_updates for report in reports),
        staged_offer_changes=sum(report.inserted_or_updated_offers for report in reports),
        staged_trade_changes=sum(report.inserted_or_updated_trade_updates for report in reports),
        invalid_payload_items=sum(report.invalid_items for report in reports),
        conflicting_payload_items=sum(report.conflicting_items for report in reports),
        rejected_by_staging=sum(report.staging_rejected_items for report in reports),
        promotion=promotion,
        refreshed_paper_minutes=refreshed,
        expired_staging_rows_purged=purged,
    )


__all__ = [
    "PRIVATE_GOLD_PIPELINE_VERSION",
    "PrivateGoldPipelineReport",
    "process_private_gold_payloads",
]
