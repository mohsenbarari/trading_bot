"""Bounded online residual calibration for the live coin estimator.

This module is deliberately not an unconstrained model retrainer.  It keeps
an audit trail of predictions and later trusted group observations, learns a
small exponentially-decayed residual per commodity/settlement, and applies a
strictly bounded correction only after enough observations are available.
"""

from __future__ import annotations

import math
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


HALF_LIFE_HOURS = 18.0
MIN_SAMPLES_TO_APPLY = 3
MAX_RESIDUAL_RATIO = 0.035
MAX_APPLIED_CORRECTION_RATIO = 0.015
RECENT_REALIZED_LOOKBACK_SECONDS = 90 * 60
RECENT_REALIZED_HALF_LIFE_SECONDS = 30 * 60
# This is deliberately smaller than the long-horizon online-calibration cap.
# It is a short-lived correction from completed coin events, not a replacement
# for the structural model or for a fresh live coin book.
MAX_RECENT_REALIZED_CORRECTION_RATIO = 0.01
RECENT_GROUP_CONSENSUS_MAX_AGE_SECONDS = 30 * 60
RECENT_GROUP_CONSENSUS_MIN_OFFERS = 3
RECENT_GROUP_CONSENSUS_MIN_CONFIDENCE = 0.90
RECENT_GROUP_CONSENSUS_MAX_TWO_SIDED_SPREAD_PERCENT = 1.0
NORMAL_MATCH_SECONDS = 5 * 60
# The event parser and trade-confirmation pass are independent workers.  A
# short grace window allows a late-arriving trusted event to be paired with
# its normal five-minute prediction window without rescanning the full ledger
# forever.  Historical rows remain intact for research and manual review.
NORMAL_EVALUATION_GRACE_SECONDS = 15 * 60
RECONNECT_MATCH_HOURS = 24
PROJECT_PRICE_MULTIPLIER = 1_000
LEDGER_MIN_INTERVAL_SECONDS = 30
# The main book's cadence must not change: its rows feed the learned residual,
# and recording less often would pair each trade with a staler prediction and
# therefore shift the residual itself.  A challenger is only ever scored, never
# learned from, so a coarser cadence costs nothing but a little resolution.
# Shadow timestamps stay a strict subset of the main model's, which keeps every
# shadow row directly comparable to a main row at the same instant.
SHADOW_LEDGER_MIN_INTERVAL_SECONDS = 120
MAIN_MODEL_ID = "MAIN_ONLINE"
MAIN_COMPARISON_MODEL_ID = "MAIN_COMPARISON"
LEARNING_EVALUATION_ROLE = "LEARNING"
COMPARISON_EVALUATION_ROLE = "COMPARISON"
# Multi-day accuracy metrics cannot move meaningfully between two consecutive
# refreshes.  Recomputing them per cycle only re-reads the same answer.
OUTCOME_SUMMARY_MIN_REFRESH_SECONDS = 300
# Furthest reach of both matching paths: the normal five-minute window plus its
# arrival grace, and the reconnect bridge, which can pair a prediction made up
# to RECONNECT_MATCH_HOURS before a reconnect with an event up to
# RECONNECT_MATCH_HOURS after it.  Beyond the sum of those, no code path can
# ever evaluate the row again.
PENDING_EXPIRY_HOURS = 3 * RECONNECT_MATCH_HOURS
UNMATCHED_EVALUATION_MODE = "UNMATCHED_EXPIRED"
# A row with a realised outcome is the labelled training corpus for the next
# residual shadow, so it is kept for a year.  A row no trade ever arrived for
# carries no label and is kept only long enough to investigate a coverage gap.
LEDGER_OUTCOME_RETENTION_DAYS = 365
LEDGER_UNMATCHED_RETENTION_DAYS = 14
LEDGER_MAINTENANCE_INTERVAL_SECONDS = 3600
# Maintenance shares the refresh transaction, so a first run against a long
# backlog must not hold a write lock while deleting millions of rows.  Each
# pass does bounded work and the hourly schedule converges on the backlog.
LEDGER_MAINTENANCE_BATCH_ROWS = 20_000
_OUTCOME_SUMMARY_CACHE: dict[tuple[Any, ...], tuple[datetime, dict[str, Any]]] = {}
_OUTCOME_SUMMARY_CACHE_MAX = 16
_LEDGER_MAINTENANCE_AT: dict[str, datetime] = {}


def _connection_identity(connection: sqlite3.Connection) -> str | None:
    """Return the ledger's file identity, or None for in-memory databases."""

    try:
        rows = connection.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        if str(row[1]) == "main" and str(row[2]):
            return str(Path(str(row[2])).resolve())
    return None


def _invalidate_outcome_summary_cache(connection: sqlite3.Connection) -> None:
    """Drop cached outcome summaries for the database changed by ``connection``."""

    identity = _connection_identity(connection)
    if identity is None:
        return
    for key in list(_OUTCOME_SUMMARY_CACHE):
        if key and key[0] == identity:
            _OUTCOME_SUMMARY_CACHE.pop(key, None)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _parse(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the prediction ledger and residual state in one transaction."""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS coin_estimate_predictions (
            id INTEGER PRIMARY KEY,
            prediction_time_utc TEXT NOT NULL,
            model_id TEXT NOT NULL DEFAULT 'MAIN_ONLINE',
            model_version TEXT,
            evaluation_role TEXT NOT NULL DEFAULT 'LEARNING',
            comparison_cohort_utc TEXT,
            commodity TEXT NOT NULL,
            settlement TEXT NOT NULL,
            structural_estimated_price_toman REAL NOT NULL,
            estimated_price_toman REAL NOT NULL,
            lower_price_toman REAL,
            upper_price_toman REAL,
            group_live_enabled INTEGER NOT NULL CHECK(group_live_enabled IN (0, 1)),
            actual_price_toman REAL,
            actual_event_utc TEXT,
            residual_ratio REAL,
            residual_ratio_raw REAL,
            evaluated_at_utc TEXT,
            evaluation_mode TEXT,
            created_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_pending_idx
            ON coin_estimate_predictions(evaluated_at_utc, prediction_time_utc);
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_pending_window_idx
            ON coin_estimate_predictions(
                evaluated_at_utc, group_live_enabled, prediction_time_utc
            );
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_lookup_idx
            ON coin_estimate_predictions(model_id, commodity, settlement, prediction_time_utc);
        CREATE TABLE IF NOT EXISTS coin_online_residual_state (
            commodity TEXT NOT NULL,
            settlement TEXT NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            residual_mean REAL NOT NULL DEFAULT 0,
            residual_abs_mean REAL NOT NULL DEFAULT 0,
            last_actual_utc TEXT,
            updated_at_utc TEXT NOT NULL,
            last_reconnect_evaluation_utc TEXT,
            PRIMARY KEY(commodity, settlement)
        );
        """
    )
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(coin_estimate_predictions)")
    }
    # SQLite's CREATE TABLE IF NOT EXISTS cannot add columns to an existing
    # production ledger.  These additive migrations retain every historical
    # main-model row under the explicit default model identity.
    if "model_id" not in columns:
        connection.execute(
            "ALTER TABLE coin_estimate_predictions "
            "ADD COLUMN model_id TEXT NOT NULL DEFAULT 'MAIN_ONLINE'"
        )
    if "model_version" not in columns:
        connection.execute(
            "ALTER TABLE coin_estimate_predictions ADD COLUMN model_version TEXT"
        )
    if "evaluation_role" not in columns:
        connection.execute(
            "ALTER TABLE coin_estimate_predictions "
            "ADD COLUMN evaluation_role TEXT NOT NULL DEFAULT 'LEARNING'"
        )
    if "comparison_cohort_utc" not in columns:
        connection.execute(
            "ALTER TABLE coin_estimate_predictions ADD COLUMN comparison_cohort_utc TEXT"
        )
    # ``residual_ratio`` stays bounded because the learned residual state must
    # never be moved by a single extreme observation.  Evaluation needs the
    # opposite property: a book that misses by 11% has to be scored as 11%, not
    # as the 3.5% cap.  The two concerns get two columns rather than one
    # compromise.  Rows written before this migration have no raw value, so
    # scoring falls back to the bounded column for them.
    if "residual_ratio_raw" not in columns:
        connection.execute(
            "ALTER TABLE coin_estimate_predictions ADD COLUMN residual_ratio_raw REAL"
        )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_model_pending_idx
        ON coin_estimate_predictions(model_id, evaluated_at_utc, prediction_time_utc)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_model_actual_idx
        ON coin_estimate_predictions(model_id, actual_event_utc)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_model_pending_book_idx
        ON coin_estimate_predictions(
            model_id, commodity, settlement, group_live_enabled,
            evaluated_at_utc, prediction_time_utc
        )
        """
    )
    # Outcome scoring groups by model over an evaluation-time window.  The
    # full ``(model_id, actual_event_utc)`` index also covers unevaluated rows,
    # which are the overwhelming majority of the ledger, so grouping walked
    # every prediction ever recorded.  This partial index holds only rows that
    # can contribute, keeps the ``model_id`` prefix that satisfies GROUP BY,
    # and carries the aggregated columns so no table lookup is needed.
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_outcome_window_idx
        ON coin_estimate_predictions(
            model_id, actual_event_utc, residual_ratio, residual_ratio_raw,
            model_version, actual_price_toman, lower_price_toman,
            upper_price_toman
        )
        WHERE actual_event_utc IS NOT NULL AND residual_ratio IS NOT NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_comparison_outcome_idx
        ON coin_estimate_predictions(
            evaluation_role, model_id, actual_event_utc,
            residual_ratio, residual_ratio_raw, model_version,
            actual_price_toman, lower_price_toman, upper_price_toman
        )
        WHERE evaluation_role='COMPARISON'
          AND actual_event_utc IS NOT NULL
          AND residual_ratio IS NOT NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_comparison_cohort_idx
        ON coin_estimate_predictions(
            evaluation_role, comparison_cohort_utc, model_id,
            commodity, settlement
        )
        WHERE evaluation_role='COMPARISON'
        """
    )


def _price(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def record_predictions(
    connection: sqlite3.Connection,
    *,
    prediction_time: datetime,
    settlement: str,
    rates: list[dict[str, Any]],
    group_live_enabled: bool,
    model_id: str = MAIN_MODEL_ID,
    model_version: str | None = None,
    min_interval_seconds: int | None = None,
    evaluation_role: str = LEARNING_EVALUATION_ROLE,
    comparison_cohort: datetime | None = None,
) -> int:
    """Persist one snapshot of the rates after bounded calibration."""

    evaluation_role = str(evaluation_role).strip().upper()
    if evaluation_role not in {
        LEARNING_EVALUATION_ROLE,
        COMPARISON_EVALUATION_ROLE,
    }:
        raise ValueError(f"unsupported evaluation_role: {evaluation_role}")
    if evaluation_role == COMPARISON_EVALUATION_ROLE and comparison_cohort is None:
        raise ValueError("comparison_cohort is required for comparison predictions")
    if min_interval_seconds is None:
        min_interval_seconds = (
            LEDGER_MIN_INTERVAL_SECONDS
            if model_id == MAIN_MODEL_ID
            else SHADOW_LEDGER_MIN_INTERVAL_SECONDS
        )
    created = _iso(datetime.now(timezone.utc))
    inserted = 0
    for rate in rates:
        structural = _price(rate.get("_structural_estimated_price_toman"))
        corrected = _price(rate.get("estimated_price_toman"))
        if structural is None:
            structural = corrected
        if structural is None or corrected is None:
            continue
        commodity = str(rate.get("commodity_name") or "")
        previous = connection.execute(
            """
            SELECT prediction_time_utc
            FROM coin_estimate_predictions
            WHERE model_id=? AND evaluation_role=? AND commodity=? AND settlement=?
            ORDER BY prediction_time_utc DESC, id DESC
            LIMIT 1
            """,
            (model_id, evaluation_role, commodity, settlement),
        ).fetchone()
        if previous is not None:
            elapsed = (
                prediction_time - _parse(str(previous[0]))
            ).total_seconds()
            if elapsed >= 0 and elapsed < min_interval_seconds:
                continue
        tolerance = rate.get("tolerance") or {}
        lower = _price(tolerance.get("lower_price_toman"))
        upper = _price(tolerance.get("upper_price_toman"))
        connection.execute(
            """
            INSERT INTO coin_estimate_predictions(
                prediction_time_utc, model_id, model_version, evaluation_role,
                comparison_cohort_utc, commodity, settlement,
                structural_estimated_price_toman, estimated_price_toman,
                lower_price_toman, upper_price_toman, group_live_enabled,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _iso(prediction_time),
                model_id,
                model_version,
                evaluation_role,
                _iso(comparison_cohort) if comparison_cohort is not None else None,
                commodity,
                settlement,
                structural,
                corrected,
                lower,
                upper,
                int(group_live_enabled),
                created,
            ),
        )
        inserted += 1
    return inserted


OFFER_MID_MAX_PAIR_GAP_SECONDS = 90


def _load_trusted_actual_context(
    connection: sqlite3.Connection,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, dict[tuple[str, str], list[dict[str, Any]]]]:
    """Fetch eligible observations once for all pending predictions.

    Reconciliation previously repeated schema introspection and two SQLite
    queries for every pending rate.  A refresh now reads the bounded window
    once, while preserving the trade-first rule per commodity/settlement.
    """

    context: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = {
        "trades": {},
        "offers": {},
    }
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "confirmed_trades" not in tables and not {"offers", "messages"}.issubset(tables):
        return context
    lower, upper = _iso(start), _iso(end)
    if "confirmed_trades" in tables:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(confirmed_trades)")
        }
        eligibility = "AND t.training_eligible=1" if "training_eligible" in columns else ""
        quality_join = (
            "LEFT JOIN trade_market_quality AS q ON q.trade_id=t.id"
            if "trade_market_quality" in tables
            else ""
        )
        quality_filter = (
            "AND COALESCE(q.realtime_eligible, 1)=1"
            if "trade_market_quality" in tables
            else ""
        )
        rows = connection.execute(
            f"""
            SELECT t.id, t.commodity, t.settlement, t.price, t.event_time_utc
            FROM confirmed_trades AS t
            {quality_join}
            WHERE t.trade_form='PHYSICAL' AND t.confidence>=0.85
              AND t.event_time_utc>? AND t.event_time_utc<=?
              {eligibility} {quality_filter}
            ORDER BY t.event_time_utc, t.id
            """,
            (lower, upper),
        ).fetchall()
        for row in rows:
            key = (str(row["commodity"]), str(row["settlement"]))
            context["trades"].setdefault(key, []).append(
                {
                    "id": str(row["id"]),
                    "price": float(row["price"]),
                    "event_time_utc": str(row["event_time_utc"]),
                    "stamp": _parse(str(row["event_time_utc"])),
                    "source_kind": "TRADE",
                }
            )
    if {"offers", "messages"}.issubset(tables):
        quality_join = (
            "LEFT JOIN offer_market_quality AS q ON q.offer_id=o.id"
            if "offer_market_quality" in tables
            else ""
        )
        quality_filter = (
            "AND COALESCE(q.realtime_eligible, 1)=1"
            if "offer_market_quality" in tables
            else ""
        )
        rows = connection.execute(
            f"""
            SELECT o.id, o.commodity, o.settlement, o.price, o.side, m.event_time_utc
            FROM offers AS o
            JOIN messages AS m ON m.import_id=o.import_id AND m.message_id=o.message_id
            {quality_join}
            WHERE o.trade_form='PHYSICAL' AND o.confidence>=0.85
              AND m.event_time_utc>? AND m.event_time_utc<=?
              {quality_filter}
            ORDER BY m.event_time_utc, o.id
            """,
            (lower, upper),
        ).fetchall()
        for row in rows:
            key = (str(row["commodity"]), str(row["settlement"]))
            context["offers"].setdefault(key, []).append(
                {
                    "id": str(row["id"]),
                    "price": float(row["price"]),
                    "side": str(row["side"] or "").upper(),
                    "event_time_utc": str(row["event_time_utc"]),
                    "stamp": _parse(str(row["event_time_utc"])),
                    "source_kind": "OFFER",
                }
            )
    return context


def _trusted_actuals(
    context: dict[str, dict[tuple[str, str], list[dict[str, Any]]]],
    *,
    commodity: str,
    settlement: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Return trades, else one near-synchronous two-sided offer midpoint."""

    key = (commodity, settlement)
    trades = [
        row
        for row in context["trades"].get(key, [])
        if row["stamp"] > start and row["stamp"] <= end
    ]
    if trades:
        return trades
    offers = [
        row
        for row in context["offers"].get(key, [])
        if row["stamp"] > start and row["stamp"] <= end
    ]
    bids = [row for row in offers if row.get("side") == "BUY"]
    asks = [row for row in offers if row.get("side") == "SELL"]
    pairs: list[tuple[float, float, datetime, dict[str, Any], dict[str, Any]]] = []
    for bid in bids:
        for ask in asks:
            gap = abs((bid["stamp"] - ask["stamp"]).total_seconds())
            if gap > OFFER_MID_MAX_PAIR_GAP_SECONDS or float(bid["price"]) > float(ask["price"]):
                continue
            # Prefer the closest pair, then the tightest spread, then newest.
            pairs.append(
                (
                    gap,
                    float(ask["price"]) - float(bid["price"]),
                    max(bid["stamp"], ask["stamp"]),
                    bid,
                    ask,
                )
            )
    if not pairs:
        return []
    _, _, stamp, bid, ask = min(
        pairs,
        key=lambda item: (item[0], item[1], -item[2].timestamp()),
    )
    return [
        {
            "id": f"{bid['id']}:{ask['id']}",
            "price": (float(bid["price"]) + float(ask["price"])) / 2.0,
            "event_time_utc": _iso(stamp),
            "stamp": stamp,
            "source_kind": "OFFER_MID",
            "bid_id": bid["id"],
            "ask_id": ask["id"],
        }
    ]


def _update_state(
    connection: sqlite3.Connection,
    *,
    commodity: str,
    settlement: str,
    residual: float,
    actual_time: datetime,
    now: datetime,
) -> None:
    residual = max(-MAX_RESIDUAL_RATIO, min(MAX_RESIDUAL_RATIO, residual))
    row = connection.execute(
        """
        SELECT sample_count, residual_mean, residual_abs_mean
        FROM coin_online_residual_state
        WHERE commodity=? AND settlement=?
        """,
        (commodity, settlement),
    ).fetchone()
    if row is None:
        sample_count = 0
        previous_mean = 0.0
        previous_abs = 0.0
    else:
        sample_count = int(row[0])
        previous_mean = float(row[1])
        previous_abs = float(row[2])
    # A modest EWMA update keeps the state adaptive without allowing a single
    # reconnect batch to move the estimator abruptly.
    alpha = 1.0 - math.exp(-math.log(2.0) / (HALF_LIFE_HOURS * 3600.0) * 300.0)
    if sample_count == 0:
        alpha = 1.0
    mean = previous_mean * (1.0 - alpha) + residual * alpha
    abs_mean = previous_abs * (1.0 - alpha) + abs(residual) * alpha
    connection.execute(
        """
        INSERT INTO coin_online_residual_state(
            commodity, settlement, sample_count, residual_mean,
            residual_abs_mean, last_actual_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(commodity, settlement) DO UPDATE SET
            sample_count=excluded.sample_count,
            residual_mean=excluded.residual_mean,
            residual_abs_mean=excluded.residual_abs_mean,
            last_actual_utc=excluded.last_actual_utc,
            updated_at_utc=excluded.updated_at_utc
        """,
        (
            commodity,
            settlement,
            sample_count + 1,
            mean,
            abs_mean,
            _iso(actual_time),
            _iso(now),
        ),
    )


def reconcile_predictions(
    connection: sqlite3.Connection,
    *,
    now: datetime,
    live_group_enabled: bool,
    reconnect_at: datetime | None = None,
    learning_model_id: str = MAIN_MODEL_ID,
    observation_connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Match predictions to later trusted prices and update residual state.

    Normal matches are limited to five minutes.  On a reconnect, at most one
    pending prediction per commodity/settlement is bridged to the first
    trusted event after the reconnect, and only when it was made while the
    group input was disconnected.  This prevents hours of stale predictions
    from being falsely paired with every new event.

    ``connection`` owns the mutable prediction ledger and residual state.
    When it is a dedicated calibration store, ``observation_connection`` is
    the read-only conversation database containing offers and trades.  Keeping
    those roles separate prevents a calibration refresh from mutating the
    ingestion database that a live-group promotion is about to replace.
    """

    now = now.astimezone(timezone.utc)
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "coin_estimate_predictions" not in tables:
        return {"evaluated": 0, "residuals": [], "reconnect_bridged": 0}
    if not live_group_enabled:
        return {
            "evaluated": 0,
            "residuals": [],
            "reconnect_bridged": 0,
            "status": "DEFERRED_GROUP_INPUT_DISCONNECTED",
        }
    normal_cutoff = now - timedelta(seconds=NORMAL_EVALUATION_GRACE_SECONDS)
    # Normal matching is defined for the first five minutes.  Keep an extra
    # ten-minute arrival allowance, then leave older rows in the ledger but
    # outside the hot refresh path.  This preserves training/audit history
    # without paying an unbounded query cost every cycle.
    normal_pending = connection.execute(
        """
        SELECT * FROM coin_estimate_predictions
        WHERE evaluated_at_utc IS NULL
          AND prediction_time_utc>=?
        ORDER BY prediction_time_utc DESC, id DESC
        """,
        (_iso(normal_cutoff),),
    ).fetchall()
    # A reconnect is the sole exception: the newest prediction made during a
    # disconnection can bridge to the first post-reconnect trusted event.
    # Select one such row per commodity/settlement instead of repeatedly
    # walking every historical pending row.
    reconnect_pending: list[sqlite3.Row] = []
    reconnect_candidate_ids: set[int] = set()
    if reconnect_at is not None:
        reconnect_at = reconnect_at.astimezone(timezone.utc)
        reconnect_floor = reconnect_at - timedelta(hours=RECONNECT_MATCH_HOURS)
        candidates = connection.execute(
            """
            SELECT * FROM coin_estimate_predictions
            WHERE evaluated_at_utc IS NULL
              AND group_live_enabled=0
              AND prediction_time_utc>=? AND prediction_time_utc<=?
            ORDER BY commodity, settlement, prediction_time_utc DESC, id DESC
            """,
            (_iso(reconnect_floor), _iso(reconnect_at)),
        ).fetchall()
        seen_reconnect_keys: set[tuple[str, str, str]] = set()
        for row in candidates:
            key = (
                str(row["model_id"]),
                str(row["commodity"]),
                str(row["settlement"]),
            )
            if key in seen_reconnect_keys:
                continue
            seen_reconnect_keys.add(key)
            reconnect_pending.append(row)
            reconnect_candidate_ids.add(int(row["id"]))

    pending = list(normal_pending)
    normal_pending_ids = {int(row["id"]) for row in pending}
    pending.extend(
        row for row in reconnect_pending if int(row["id"]) not in normal_pending_ids
    )
    context_start_candidates = [normal_cutoff]
    if reconnect_at is not None:
        context_start_candidates.append(reconnect_at - timedelta(seconds=1))
    observations = observation_connection or connection
    observations.row_factory = sqlite3.Row
    actual_context = _load_trusted_actual_context(
        observations,
        start=min(context_start_candidates),
        end=now,
    )
    evaluated = 0
    bridged = 0
    used_actual_ids: set[tuple[str, str, str]] = set()
    assigned_start = min(context_start_candidates)
    assigned_actuals = {
        (
            str(row["model_id"]),
            str(row["commodity"]),
            str(row["settlement"]),
            str(row["actual_event_utc"]),
        )
        for row in connection.execute(
            """
            SELECT model_id, commodity, settlement, actual_event_utc
            FROM coin_estimate_predictions
            WHERE actual_event_utc IS NOT NULL AND actual_event_utc>=?
            """,
            (_iso(assigned_start),),
        ).fetchall()
    }
    residuals: list[dict[str, Any]] = []
    reconnect_keys: set[tuple[str, str, str]] = set()

    for row in pending:
        prediction_time = _parse(str(row["prediction_time_utc"]))
        actuals = _trusted_actuals(
            actual_context,
            commodity=str(row["commodity"]),
            settlement=str(row["settlement"]),
            start=prediction_time,
            end=min(now, prediction_time + timedelta(seconds=NORMAL_MATCH_SECONDS)),
        )
        actual = next(
            (
                candidate
                for candidate in actuals
                if (
                    str(row["model_id"]),
                    str(candidate["source_kind"]),
                    str(candidate["id"]),
                ) not in used_actual_ids
                and (
                    str(row["model_id"]),
                    str(row["commodity"]),
                    str(row["settlement"]),
                    str(candidate["event_time_utc"]),
                )
                not in assigned_actuals
            ),
            None,
        )
        mode = "FORWARD_5M"
        if (
            actual is None
            and reconnect_at is not None
            and int(row["id"]) in reconnect_candidate_ids
        ):
            key = (str(row["commodity"]), str(row["settlement"]))
            reconnect_key = (str(row["model_id"]), *key)
            if reconnect_key not in reconnect_keys:
                reconnect_start = max(reconnect_at, prediction_time)
                actuals = _trusted_actuals(
                    actual_context,
                    commodity=key[0],
                    settlement=key[1],
                    start=reconnect_start - timedelta(seconds=1),
                    end=min(now, reconnect_start + timedelta(hours=RECONNECT_MATCH_HOURS)),
                )
                actual = next(
                    (
                        candidate
                        for candidate in actuals
                        if (
                            str(row["model_id"]),
                            str(candidate["source_kind"]),
                            str(candidate["id"]),
                        ) not in used_actual_ids
                        and (
                            str(row["model_id"]),
                            key[0],
                            key[1],
                            str(candidate["event_time_utc"]),
                        )
                        not in assigned_actuals
                    ),
                    None,
                )
                if actual is not None:
                    mode = "RECONNECT_BRIDGE"
                    reconnect_keys.add(reconnect_key)
        if actual is None:
            continue
        predicted = _price(row["estimated_price_toman"])
        # Group databases store project-unit prices (for example 185000),
        # while the estimator ledger is in complete toman.
        actual_price = _price(actual["price"])
        if actual_price is not None:
            actual_price *= PROJECT_PRICE_MULTIPLIER
        if predicted is None or actual_price is None:
            continue
        # Two columns, two purposes.  ``residual_ratio`` stays clipped so every
        # existing consumer of the learning path keeps its bounded contract;
        # ``residual_ratio_raw`` keeps the true error so evaluation can report
        # a large miss as a large miss instead of saturating at the cap.
        raw_residual = (actual_price - predicted) / predicted
        residual = max(-MAX_RESIDUAL_RATIO, min(MAX_RESIDUAL_RATIO, raw_residual))
        actual_time = _parse(str(actual["event_time_utc"]))
        connection.execute(
            """
            UPDATE coin_estimate_predictions
            SET actual_price_toman=?, actual_event_utc=?, residual_ratio=?,
                residual_ratio_raw=?, evaluated_at_utc=?, evaluation_mode=?
            WHERE id=?
            """,
            (
                actual_price,
                _iso(actual_time),
                residual,
                raw_residual,
                _iso(now),
                mode,
                int(row["id"]),
            ),
        )
        if str(row["model_id"]) == learning_model_id:
            _update_state(
                connection,
                commodity=str(row["commodity"]),
                settlement=str(row["settlement"]),
                residual=residual,
                actual_time=actual_time,
                now=now,
            )
        used_actual_ids.add(
            (
                str(row["model_id"]),
                str(actual["source_kind"]),
                str(actual["id"]),
            )
        )
        assigned_actuals.add(
            (
                str(row["model_id"]),
                str(row["commodity"]),
                str(row["settlement"]),
                str(actual["event_time_utc"]),
            )
        )
        evaluated += 1
        if mode == "RECONNECT_BRIDGE":
            bridged += 1
        residuals.append(
            {
                "commodity": str(row["commodity"]),
                "settlement": str(row["settlement"]),
                "model_id": str(row["model_id"]),
                "residual_ratio": residual,
                "residual_ratio_raw": raw_residual,
                "mode": mode,
            }
        )
    if evaluated:
        _invalidate_outcome_summary_cache(connection)
    return {"evaluated": evaluated, "residuals": residuals, "reconnect_bridged": bridged}


def apply_calibration(
    connection: sqlite3.Connection,
    *,
    commodity: str,
    settlement: str,
    rate: dict[str, Any],
) -> dict[str, Any]:
    """Apply a bounded residual correction while never narrowing an interval."""

    original = _price(rate.get("estimated_price_toman"))
    if original is None:
        return {"status": "NO_ESTIMATE", "sample_count": 0, "correction_ratio": 0.0}
    rate["_structural_estimated_price_toman"] = original
    row = connection.execute(
        """
        SELECT sample_count, residual_mean, residual_abs_mean, last_actual_utc
        FROM coin_online_residual_state
        WHERE commodity=? AND settlement=?
        """,
        (commodity, settlement),
    ).fetchone()
    if row is None or int(row[0]) < MIN_SAMPLES_TO_APPLY:
        return {
            "status": "WARMING_UP",
            "sample_count": int(row[0]) if row is not None else 0,
            "correction_ratio": 0.0,
            "last_actual_utc": row[3] if row is not None else None,
        }
    correction = max(
        -MAX_APPLIED_CORRECTION_RATIO,
        min(MAX_APPLIED_CORRECTION_RATIO, float(row[1])),
    )
    corrected = original * (1.0 + correction)
    rate["estimated_price_toman"] = int(round(corrected / 50_000.0) * 50_000)
    rate["estimated_project_price"] = int(round(rate["estimated_price_toman"] / PROJECT_PRICE_MULTIPLIER))
    tolerance = rate.get("tolerance")
    if isinstance(tolerance, dict):
        lower = _price(tolerance.get("lower_price_toman"))
        upper = _price(tolerance.get("upper_price_toman"))
        if lower is not None and upper is not None:
            shifted_lower = lower * (1.0 + correction)
            shifted_upper = upper * (1.0 + correction)
            # Include the old interval boundaries so correction can widen but
            # never silently reduce known uncertainty.
            tolerance["lower_price_toman"] = int(round(min(lower, shifted_lower) / 50_000) * 50_000)
            tolerance["upper_price_toman"] = int(round(max(upper, shifted_upper) / 50_000) * 50_000)
            tolerance["lower_project_price"] = int(round(tolerance["lower_price_toman"] / PROJECT_PRICE_MULTIPLIER))
            tolerance["upper_project_price"] = int(round(tolerance["upper_price_toman"] / PROJECT_PRICE_MULTIPLIER))
    return {
        "status": "APPLIED",
        "sample_count": int(row[0]),
        "residual_mean": float(row[1]),
        "residual_abs_mean": float(row[2]),
        "correction_ratio": correction,
        "last_actual_utc": row[3],
    }


def summarize_model_outcomes(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    lookback_days: int = 7,
    min_refresh_seconds: int = OUTCOME_SUMMARY_MIN_REFRESH_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Return bounded, comparable accuracy metrics for each forecast book.

    The summary spans days, so recomputing it on every five-second refresh
    only re-reads the same answer.  A short in-process cache keeps the live
    loop cheap; ``min_refresh_seconds=0`` forces a recompute for tests and
    one-shot CLI reporting.
    """

    as_of = as_of.astimezone(timezone.utc)
    since = as_of - timedelta(days=max(1, lookback_days))
    # Distinct in-memory databases share no stable identity, so they must never
    # share a cache slot.  Those are test/one-shot callers and always recompute.
    identity = _connection_identity(connection)
    cacheable = identity is not None and min_refresh_seconds > 0
    cache_key = (identity, int(max(1, lookback_days)))
    if cacheable:
        cached = _OUTCOME_SUMMARY_CACHE.get(cache_key)
        if cached is not None:
            computed_at, payload = cached
            if 0 <= (as_of - computed_at).total_seconds() < min_refresh_seconds:
                return deepcopy(payload)
    rows = connection.execute(
        """
        SELECT
            p.model_id,
            (
                SELECT latest.model_version
                FROM coin_estimate_predictions AS latest
                WHERE latest.model_id=p.model_id
                  AND latest.evaluation_role='COMPARISON'
                  AND latest.actual_event_utc IS NOT NULL
                  AND latest.residual_ratio IS NOT NULL
                  AND latest.actual_event_utc>=?
                ORDER BY latest.actual_event_utc DESC, latest.id DESC
                LIMIT 1
            ) AS model_version,
            COUNT(*) AS sample_count,
            SUM(CASE WHEN residual_ratio_raw IS NULL THEN 1 ELSE 0 END)
                AS capped_only_sample_count,
            AVG(ABS(COALESCE(residual_ratio_raw, residual_ratio))) AS mape_ratio,
            AVG(COALESCE(residual_ratio_raw, residual_ratio)) AS bias_ratio,
            MAX(ABS(COALESCE(residual_ratio_raw, residual_ratio))) AS worst_abs_ratio,
            AVG(
                CASE
                    WHEN lower_price_toman IS NOT NULL AND upper_price_toman IS NOT NULL
                     AND actual_price_toman BETWEEN lower_price_toman AND upper_price_toman
                    THEN 1.0 ELSE 0.0
                END
            ) AS interval_coverage_ratio
        FROM coin_estimate_predictions AS p
        WHERE p.evaluation_role='COMPARISON'
          AND p.actual_event_utc IS NOT NULL
          AND p.residual_ratio IS NOT NULL
          AND p.actual_event_utc>=?
        GROUP BY p.model_id
        ORDER BY p.model_id
        """,
        (_iso(since), _iso(since)),
    ).fetchall()
    summary = {
        str(row["model_id"]): {
            "model_version": row["model_version"],
            "sample_count": int(row["sample_count"] or 0),
            "mape_percent": float(row["mape_ratio"] or 0.0) * 100.0,
            "bias_percent": float(row["bias_ratio"] or 0.0) * 100.0,
            "worst_abs_error_percent": float(row["worst_abs_ratio"] or 0.0) * 100.0,
            "interval_coverage_percent": float(row["interval_coverage_ratio"] or 0.0)
            * 100.0,
            # Rows evaluated before the raw-residual migration can only be
            # scored from the clipped column, so their error is understated.
            # Surface that rather than letting it hide inside the average.
            "capped_only_sample_count": int(row["capped_only_sample_count"] or 0),
            "error_source": (
                "RAW_UNCLIPPED"
                if not int(row["capped_only_sample_count"] or 0)
                else "MIXED_RAW_AND_LEGACY_CLIPPED"
            ),
            "lookback_days": max(1, lookback_days),
            "computed_at_utc": _iso(as_of),
        }
        for row in rows
    }
    if cacheable:
        if len(_OUTCOME_SUMMARY_CACHE) >= _OUTCOME_SUMMARY_CACHE_MAX:
            _OUTCOME_SUMMARY_CACHE.clear()
        _OUTCOME_SUMMARY_CACHE[cache_key] = (as_of, deepcopy(summary))
    return summary


def ledger_storage_report(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    outcome_retention_days: int = LEDGER_OUTCOME_RETENTION_DAYS,
    unmatched_retention_days: int = LEDGER_UNMATCHED_RETENTION_DAYS,
) -> dict[str, Any]:
    """Describe ledger composition and what maintenance would change."""

    as_of = as_of.astimezone(timezone.utc)
    expiry_cutoff = as_of - timedelta(hours=PENDING_EXPIRY_HOURS)
    outcome_cutoff = as_of - timedelta(days=max(1, outcome_retention_days))
    unmatched_cutoff = as_of - timedelta(days=max(1, unmatched_retention_days))
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS total_rows,
            MIN(prediction_time_utc) AS oldest_prediction_utc,
            MAX(prediction_time_utc) AS newest_prediction_utc,
            SUM(CASE WHEN evaluated_at_utc IS NULL THEN 1 ELSE 0 END)
                AS pending_rows,
            SUM(
                CASE WHEN evaluated_at_utc IS NULL AND prediction_time_utc<?
                THEN 1 ELSE 0 END
            ) AS expirable_rows,
            SUM(CASE WHEN evaluation_mode=? THEN 1 ELSE 0 END) AS unmatched_rows,
            SUM(
                CASE WHEN evaluation_mode=? AND prediction_time_utc<?
                THEN 1 ELSE 0 END
            ) AS unmatched_prunable_rows,
            SUM(
                CASE WHEN actual_event_utc IS NOT NULL THEN 1 ELSE 0 END
            ) AS outcome_rows,
            SUM(
                CASE WHEN actual_event_utc IS NOT NULL AND prediction_time_utc<?
                THEN 1 ELSE 0 END
            ) AS outcome_prunable_rows
        FROM coin_estimate_predictions
        """,
        (
            _iso(expiry_cutoff),
            UNMATCHED_EVALUATION_MODE,
            UNMATCHED_EVALUATION_MODE,
            _iso(unmatched_cutoff),
            _iso(outcome_cutoff),
        ),
    ).fetchone()
    return {
        "total_rows": int(row["total_rows"] or 0),
        "pending_rows": int(row["pending_rows"] or 0),
        "expirable_rows": int(row["expirable_rows"] or 0),
        "unmatched_rows": int(row["unmatched_rows"] or 0),
        "unmatched_prunable_rows": int(row["unmatched_prunable_rows"] or 0),
        "outcome_rows": int(row["outcome_rows"] or 0),
        "outcome_prunable_rows": int(row["outcome_prunable_rows"] or 0),
        "oldest_prediction_utc": row["oldest_prediction_utc"],
        "newest_prediction_utc": row["newest_prediction_utc"],
        "pending_expiry_hours": PENDING_EXPIRY_HOURS,
        "pending_expiry_cutoff_utc": _iso(expiry_cutoff),
        "outcome_retention_days": max(1, outcome_retention_days),
        "outcome_retention_cutoff_utc": _iso(outcome_cutoff),
        "unmatched_retention_days": max(1, unmatched_retention_days),
        "unmatched_retention_cutoff_utc": _iso(unmatched_cutoff),
    }


def _delete_bounded(
    connection: sqlite3.Connection,
    *,
    predicate: str,
    parameters: tuple[Any, ...],
    batch_rows: int | None,
) -> int:
    """Delete matching rows, optionally capping how many one pass removes."""

    if batch_rows is None:
        return int(
            connection.execute(
                f"DELETE FROM coin_estimate_predictions WHERE {predicate}",
                parameters,
            ).rowcount
            or 0
        )
    return int(
        connection.execute(
            f"""
            DELETE FROM coin_estimate_predictions WHERE id IN (
                SELECT id FROM coin_estimate_predictions
                WHERE {predicate}
                ORDER BY prediction_time_utc
                LIMIT ?
            )
            """,
            (*parameters, int(batch_rows)),
        ).rowcount
        or 0
    )


def expire_unmatched_predictions(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    batch_rows: int | None = None,
) -> int:
    """Close pending rows that reconciliation can no longer reach.

    A prediction only earns an outcome if a trusted event arrives inside the
    five-minute forward window, or — after a disconnection — inside the
    reconnect bridge.  The overwhelming majority never match, and until now
    they stayed ``pending`` forever: on staging, 30,057 of 32,247 rows.  That
    made "pending" meaningless and left the pending indexes growing without
    bound.  Rows older than the furthest reach of both matching paths get an
    explicit terminal state with a NULL residual, so they are excluded from
    scoring while remaining fully readable for audit.
    """

    cutoff = as_of.astimezone(timezone.utc) - timedelta(hours=PENDING_EXPIRY_HOURS)
    if batch_rows is None:
        cursor = connection.execute(
            """
            UPDATE coin_estimate_predictions
            SET evaluated_at_utc=?, evaluation_mode=?
            WHERE evaluated_at_utc IS NULL AND prediction_time_utc<?
            """,
            (
                _iso(as_of.astimezone(timezone.utc)),
                UNMATCHED_EVALUATION_MODE,
                _iso(cutoff),
            ),
        )
        return int(cursor.rowcount or 0)
    # ``UPDATE ... LIMIT`` needs a non-default SQLite build, so bound the work
    # through an explicit id subquery instead.
    cursor = connection.execute(
        """
        UPDATE coin_estimate_predictions
        SET evaluated_at_utc=?, evaluation_mode=?
        WHERE id IN (
            SELECT id FROM coin_estimate_predictions
            WHERE evaluated_at_utc IS NULL AND prediction_time_utc<?
            ORDER BY prediction_time_utc
            LIMIT ?
        )
        """,
        (
            _iso(as_of.astimezone(timezone.utc)),
            UNMATCHED_EVALUATION_MODE,
            _iso(cutoff),
            int(batch_rows),
        ),
    )
    return int(cursor.rowcount or 0)


def prune_prediction_ledger(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    outcome_retention_days: int = LEDGER_OUTCOME_RETENTION_DAYS,
    unmatched_retention_days: int = LEDGER_UNMATCHED_RETENTION_DAYS,
    expire_pending: bool = True,
    dry_run: bool = True,
    batch_rows: int | None = None,
) -> dict[str, Any]:
    """Expire unreachable pending rows, then prune on two retention horizons.

    The horizons differ because the rows differ.  A row with a realised
    outcome is the training corpus for the next residual shadow and is kept
    for a year.  A row that no trade ever arrived for carries no label, so it
    is kept only long enough to investigate a gap and then dropped.  Rows that
    reconciliation can still reach are never touched at any age.
    """

    as_of = as_of.astimezone(timezone.utc)
    expired = 0
    if expire_pending and not dry_run:
        expired = expire_unmatched_predictions(
            connection, as_of=as_of, batch_rows=batch_rows
        )
    report = ledger_storage_report(
        connection,
        as_of=as_of,
        outcome_retention_days=outcome_retention_days,
        unmatched_retention_days=unmatched_retention_days,
    )
    report["expired_rows"] = expired
    report["dry_run"] = bool(dry_run)
    if dry_run:
        report["deleted_unmatched_rows"] = 0
        report["deleted_outcome_rows"] = 0
        report["deleted_rows"] = 0
        return report
    unmatched = _delete_bounded(
        connection,
        predicate="evaluation_mode=? AND prediction_time_utc<?",
        parameters=(
            UNMATCHED_EVALUATION_MODE,
            report["unmatched_retention_cutoff_utc"],
        ),
        batch_rows=batch_rows,
    )
    outcomes = _delete_bounded(
        connection,
        predicate="actual_event_utc IS NOT NULL AND prediction_time_utc<?",
        parameters=(report["outcome_retention_cutoff_utc"],),
        batch_rows=batch_rows,
    )
    report["deleted_unmatched_rows"] = unmatched
    report["deleted_outcome_rows"] = outcomes
    report["deleted_rows"] = unmatched + outcomes
    report["batch_rows"] = batch_rows
    _OUTCOME_SUMMARY_CACHE.clear()
    return report


def maintain_prediction_ledger(
    connection: sqlite3.Connection, *, as_of: datetime
) -> dict[str, Any]:
    """Run ledger maintenance at most once per interval on the live path.

    Expiry and pruning are cheap and indexed, but they do not need to run on
    every refresh.  This wrapper keeps the hot loop free while guaranteeing the
    ledger is bounded without waiting for an operator to remember the CLI.
    """

    as_of = as_of.astimezone(timezone.utc)
    identity = _connection_identity(connection)
    if identity is not None:
        last = _LEDGER_MAINTENANCE_AT.get(identity)
        if last is not None and 0 <= (as_of - last).total_seconds() < (
            LEDGER_MAINTENANCE_INTERVAL_SECONDS
        ):
            return {"status": "SKIPPED_RECENTLY_RUN", "last_run_utc": _iso(last)}
    report = prune_prediction_ledger(
        connection,
        as_of=as_of,
        dry_run=False,
        batch_rows=LEDGER_MAINTENANCE_BATCH_ROWS,
    )
    if identity is not None:
        # Only a successful maintenance pass may suppress the next run.  If
        # pruning raises and the caller rolls back, the next refresh retries
        # instead of silently waiting an hour.
        _LEDGER_MAINTENANCE_AT[identity] = as_of
    report["status"] = "RAN"
    return report


def apply_snapshot_calibration(
    connection: sqlite3.Connection,
    *,
    settlements: dict[str, Any],
) -> dict[str, Any]:
    """Calibrate all rate rows and attach machine-readable audit metadata."""

    metadata: dict[str, Any] = {}
    for settlement, payload in settlements.items():
        for rate in payload.get("rates", []):
            commodity = str(rate.get("commodity_name") or "")
            info = apply_calibration(
                connection,
                commodity=commodity,
                settlement=str(settlement),
                rate=rate,
            )
            rate["online_residual_calibration"] = info
            metadata[f"{settlement}:{commodity}"] = info
    return metadata


def _weighted_median(values: list[tuple[float, float]]) -> float | None:
    """Return a deterministic weighted median for finite positive weights."""

    eligible = sorted(
        (value, weight)
        for value, weight in values
        if math.isfinite(value) and math.isfinite(weight) and weight > 0
    )
    if not eligible:
        return None
    total = sum(weight for _, weight in eligible)
    running = 0.0
    for value, weight in eligible:
        running += weight
        if running >= total / 2.0:
            return value
    return eligible[-1][0]


def group_market_evidence_kind(rate: dict[str, Any]) -> str | None:
    """Classify group evidence strong enough to outrank retrospective rules.

    The executable quote TTL remains unchanged.  A dense, consistent recent
    consensus is only a guard against moving an already market-supported
    estimate away from observed offers/trades; it is not relabelled as live.
    """

    anchor = rate.get("group_offer_anchor")
    if isinstance(anchor, dict) and str(anchor.get("status") or "") == "OBSERVED":
        return "LIVE_GROUP_BOOK"

    historical = rate.get("historical_group_anchor")
    if not isinstance(historical, dict):
        return None
    if str(historical.get("status") or "") != "OBSERVED":
        return None
    try:
        age_seconds = float(historical.get("age_seconds"))
        confidence = float(historical.get("confidence"))
        reference_price = float(historical.get("reference_price_toman"))
        trade_count = int(historical.get("trade_count") or 0)
        offer_count = int(historical.get("offer_count") or 0)
        buy_offer_count = int(historical.get("buy_offer_count") or 0)
        sell_offer_count = int(historical.get("sell_offer_count") or 0)
    except (TypeError, ValueError):
        return None
    if not 0 <= age_seconds <= RECENT_GROUP_CONSENSUS_MAX_AGE_SECONDS:
        return None
    if confidence < RECENT_GROUP_CONSENSUS_MIN_CONFIDENCE:
        return None
    if reference_price <= 0 or historical.get("latest_is_consistent") is False:
        return None
    two_sided = False
    try:
        spread_percent = float(historical.get("two_sided_spread_percent"))
        two_sided = (
            buy_offer_count >= 1
            and sell_offer_count >= 1
            and 0 <= spread_percent <= RECENT_GROUP_CONSENSUS_MAX_TWO_SIDED_SPREAD_PERCENT
        )
    except (TypeError, ValueError):
        pass
    if (
        trade_count < 1
        and offer_count < RECENT_GROUP_CONSENSUS_MIN_OFFERS
        and not two_sided
    ):
        return None
    return "RECENT_TWO_SIDED_GROUP_BOOK" if two_sided else "RECENT_GROUP_CONSENSUS"


def apply_recent_realized_calibration(
    connection: sqlite3.Connection,
    *,
    commodity: str,
    settlement: str,
    rate: dict[str, Any],
    as_of: datetime,
) -> dict[str, Any]:
    """Apply a bounded short-term residual only when the live coin book is quiet.

    The long-horizon exponentially-decayed residual is intentionally stable.
    It can therefore react too slowly immediately after a confirmed change in
    the coin market.  This second guardrail uses only *previous*, distinct
    realized outcomes from the prediction ledger.  It never runs while a
    current group-book anchor exists, and it is capped at one percent.
    """

    original = _price(rate.get("estimated_price_toman"))
    if original is None:
        return {"status": "NO_ESTIMATE", "actual_event_count": 0, "correction_ratio": 0.0}
    group_evidence = group_market_evidence_kind(rate)
    if group_evidence is not None:
        return {
            "status": (
                "SKIPPED_FRESH_LIVE_GROUP_ANCHOR"
                if group_evidence == "LIVE_GROUP_BOOK"
                else "SKIPPED_RECENT_GROUP_CONSENSUS"
            ),
            "actual_event_count": 0,
            "correction_ratio": 0.0,
            "group_market_evidence": group_evidence,
        }

    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    as_of = as_of.astimezone(timezone.utc)
    since = as_of - timedelta(seconds=RECENT_REALIZED_LOOKBACK_SECONDS)
    rows = connection.execute(
        """
        SELECT actual_event_utc, residual_ratio
        FROM coin_estimate_predictions
        WHERE model_id=? AND commodity=? AND settlement=?
          AND actual_event_utc IS NOT NULL
          AND residual_ratio IS NOT NULL
          AND actual_event_utc>=? AND actual_event_utc<=?
        ORDER BY actual_event_utc, id
        """,
        (MAIN_MODEL_ID, commodity, settlement, _iso(since), _iso(as_of)),
    ).fetchall()
    by_actual: dict[str, list[float]] = {}
    for row in rows:
        # Residuals are signed and can validly be negative; _price is not
        # suitable here because it rejects non-positive numeric values.
        try:
            residual = float(row["residual_ratio"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(residual):
            continue
        event = str(row["actual_event_utc"])
        by_actual.setdefault(event, []).append(residual)
    weighted: list[tuple[float, float]] = []
    newest_actual: str | None = None
    for event, residuals in by_actual.items():
        try:
            occurred = _parse(event)
        except (TypeError, ValueError):
            continue
        age_seconds = (as_of - occurred).total_seconds()
        if age_seconds < 0 or age_seconds > RECENT_REALIZED_LOOKBACK_SECONDS:
            continue
        median = float(sorted(residuals)[len(residuals) // 2])
        weight = math.exp(
            -math.log(2.0) * age_seconds / RECENT_REALIZED_HALF_LIFE_SECONDS
        )
        weighted.append((median, weight))
        if newest_actual is None or event > newest_actual:
            newest_actual = event
    raw = _weighted_median(weighted)
    if raw is None:
        return {
            "status": "NO_RECENT_REALIZED_OUTCOME",
            "actual_event_count": 0,
            "correction_ratio": 0.0,
        }
    correction = max(
        -MAX_RECENT_REALIZED_CORRECTION_RATIO,
        min(MAX_RECENT_REALIZED_CORRECTION_RATIO, raw),
    )
    corrected = int(round((original * (1.0 + correction)) / 50_000.0) * 50_000)
    rate["estimated_price_toman"] = corrected
    rate["estimated_project_price"] = int(round(corrected / PROJECT_PRICE_MULTIPLIER))
    tolerance = rate.get("tolerance")
    if isinstance(tolerance, dict):
        lower = _price(tolerance.get("lower_price_toman"))
        upper = _price(tolerance.get("upper_price_toman"))
        if lower is not None and upper is not None:
            # Preserve (and very slightly widen) the prior uncertainty width
            # while moving the interval with the newly calibrated centre.
            half_width = max(original - lower, upper - original, 0.0) * 1.05
            lower_shifted = corrected - half_width
            upper_shifted = corrected + half_width
            tolerance["lower_price_toman"] = int(round(lower_shifted / 50_000.0) * 50_000)
            tolerance["upper_price_toman"] = int(round(upper_shifted / 50_000.0) * 50_000)
            tolerance["lower_project_price"] = int(round(tolerance["lower_price_toman"] / PROJECT_PRICE_MULTIPLIER))
            tolerance["upper_project_price"] = int(round(tolerance["upper_price_toman"] / PROJECT_PRICE_MULTIPLIER))
    rate["method"] = f"{rate.get('method') or 'LIVE'}+RECENT_REALIZED_RESIDUAL"
    return {
        "status": "APPLIED",
        "actual_event_count": len(weighted),
        "raw_residual_ratio": raw,
        "correction_ratio": correction,
        "newest_actual_utc": newest_actual,
    }


def apply_recent_realized_snapshot_calibration(
    connection: sqlite3.Connection,
    *,
    settlements: dict[str, Any],
    as_of: datetime,
) -> dict[str, Any]:
    """Attach bounded recent-realized calibration metadata to all rate rows."""

    metadata: dict[str, Any] = {}
    for settlement, payload in settlements.items():
        for rate in payload.get("rates", []):
            commodity = str(rate.get("commodity_name") or "")
            info = apply_recent_realized_calibration(
                connection,
                commodity=commodity,
                settlement=str(settlement),
                rate=rate,
                as_of=as_of,
            )
            rate["recent_realized_residual_calibration"] = info
            metadata[f"{settlement}:{commodity}"] = info
    return metadata
