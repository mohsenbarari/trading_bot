"""Bounded online residual calibration for the live coin estimator.

This module is deliberately not an unconstrained model retrainer.  It keeps
an audit trail of predictions and later trusted group observations, learns a
small exponentially-decayed residual per commodity/settlement, and applies a
strictly bounded correction only after enough observations are available.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
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
NORMAL_MATCH_SECONDS = 5 * 60
RECONNECT_MATCH_HOURS = 24
PROJECT_PRICE_MULTIPLIER = 1_000
LEDGER_MIN_INTERVAL_SECONDS = 30


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
            evaluated_at_utc TEXT,
            evaluation_mode TEXT,
            created_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_pending_idx
            ON coin_estimate_predictions(evaluated_at_utc, prediction_time_utc);
        CREATE INDEX IF NOT EXISTS coin_estimate_predictions_lookup_idx
            ON coin_estimate_predictions(commodity, settlement, prediction_time_utc);
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
) -> int:
    """Persist one snapshot of the rates after bounded calibration."""

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
            WHERE commodity=? AND settlement=?
            ORDER BY prediction_time_utc DESC, id DESC
            LIMIT 1
            """,
            (commodity, settlement),
        ).fetchone()
        if previous is not None:
            elapsed = (
                prediction_time - _parse(str(previous[0]))
            ).total_seconds()
            if elapsed >= 0 and elapsed < LEDGER_MIN_INTERVAL_SECONDS:
                continue
        tolerance = rate.get("tolerance") or {}
        lower = _price(tolerance.get("lower_price_toman"))
        upper = _price(tolerance.get("upper_price_toman"))
        connection.execute(
            """
            INSERT INTO coin_estimate_predictions(
                prediction_time_utc, commodity, settlement,
                structural_estimated_price_toman, estimated_price_toman,
                lower_price_toman, upper_price_toman, group_live_enabled,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _iso(prediction_time),
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


def _trusted_actuals(
    connection: sqlite3.Connection,
    *,
    commodity: str,
    settlement: str,
    start: datetime,
    end: datetime,
) -> list[sqlite3.Row]:
    """Return high-confidence physical group trades, newest parser first."""

    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "confirmed_trades" not in tables and not {"offers", "messages"}.issubset(tables):
        return []
    rows: list[sqlite3.Row] = []
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
        rows.extend(
            connection.execute(
                f"""
                SELECT t.id, t.price, t.event_time_utc, 'TRADE' AS source_kind
                FROM confirmed_trades AS t
                {quality_join}
                WHERE t.commodity=? AND t.settlement=?
                  AND t.trade_form='PHYSICAL'
                  AND t.confidence>=0.85
                  AND t.event_time_utc>? AND t.event_time_utc<=?
                  {eligibility} {quality_filter}
                ORDER BY t.event_time_utc, t.id
                """,
                (commodity, settlement, _iso(start), _iso(end)),
            ).fetchall()
        )
    # If a period has no confirmed trade yet, a quality-approved physical
    # offer is still useful as a provisional observed price.  Trades sort
    # first and therefore always win when both arrive for the same moment.
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
        rows.extend(
            connection.execute(
                f"""
                SELECT o.id, o.price, m.event_time_utc, 'OFFER' AS source_kind
                FROM offers AS o
                JOIN messages AS m
                  ON m.import_id=o.import_id AND m.message_id=o.message_id
                {quality_join}
                WHERE o.commodity=? AND o.settlement=?
                  AND o.trade_form='PHYSICAL'
                  AND o.confidence>=0.85
                  AND m.event_time_utc>? AND m.event_time_utc<=?
                  {quality_filter}
                ORDER BY m.event_time_utc, o.id
                """,
                (commodity, settlement, _iso(start), _iso(end)),
            ).fetchall()
        )
    return sorted(
        rows,
        key=lambda row: (
            0 if str(row["source_kind"]) == "TRADE" else 1,
            str(row["event_time_utc"]),
            int(row["id"]),
        ),
    )


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
) -> dict[str, Any]:
    """Match predictions to later trusted prices and update residual state.

    Normal matches are limited to five minutes.  On a reconnect, at most one
    pending prediction per commodity/settlement is bridged to the first
    trusted event after the reconnect, and only when it was made while the
    group input was disconnected.  This prevents hours of stale predictions
    from being falsely paired with every new event.
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
    pending = connection.execute(
        """
        SELECT * FROM coin_estimate_predictions
        WHERE evaluated_at_utc IS NULL
        ORDER BY prediction_time_utc, id
        """
    ).fetchall()
    evaluated = 0
    bridged = 0
    used_actual_ids: set[tuple[str, int]] = set()
    assigned_actuals = {
        (str(row["commodity"]), str(row["settlement"]), str(row["actual_event_utc"]))
        for row in connection.execute(
            """
            SELECT commodity, settlement, actual_event_utc
            FROM coin_estimate_predictions
            WHERE actual_event_utc IS NOT NULL
            """
        ).fetchall()
    }
    residuals: list[dict[str, Any]] = []
    reconnect_keys: set[tuple[str, str]] = set()

    for row in pending:
        prediction_time = _parse(str(row["prediction_time_utc"]))
        actuals = _trusted_actuals(
            connection,
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
                    str(candidate["source_kind"]),
                    int(candidate["id"]),
                ) not in used_actual_ids
                and (
                    str(row["commodity"]),
                    str(row["settlement"]),
                    str(candidate["event_time_utc"]),
                )
                not in assigned_actuals
            ),
            None,
        )
        mode = "FORWARD_5M"
        if actual is None and live_group_enabled and reconnect_at is not None:
            key = (str(row["commodity"]), str(row["settlement"]))
            if not bool(row["group_live_enabled"]) and key not in reconnect_keys:
                reconnect_start = max(reconnect_at, prediction_time)
                actuals = _trusted_actuals(
                    connection,
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
                            str(candidate["source_kind"]),
                            int(candidate["id"]),
                        ) not in used_actual_ids
                        and (
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
                    reconnect_keys.add(key)
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
        residual = max(-MAX_RESIDUAL_RATIO, min(MAX_RESIDUAL_RATIO, (actual_price - predicted) / predicted))
        actual_time = _parse(str(actual["event_time_utc"]))
        connection.execute(
            """
            UPDATE coin_estimate_predictions
            SET actual_price_toman=?, actual_event_utc=?, residual_ratio=?,
                evaluated_at_utc=?, evaluation_mode=?
            WHERE id=?
            """,
            (actual_price, _iso(actual_time), residual, _iso(now), mode, int(row["id"])),
        )
        _update_state(
            connection,
            commodity=str(row["commodity"]),
            settlement=str(row["settlement"]),
            residual=residual,
            actual_time=actual_time,
            now=now,
        )
        used_actual_ids.add((str(actual["source_kind"]), int(actual["id"])))
        assigned_actuals.add(
            (str(row["commodity"]), str(row["settlement"]), str(actual["event_time_utc"]))
        )
        evaluated += 1
        if mode == "RECONNECT_BRIDGE":
            bridged += 1
        residuals.append(
            {
                "commodity": str(row["commodity"]),
                "settlement": str(row["settlement"]),
                "residual_ratio": residual,
                "mode": mode,
            }
        )
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


def _is_fresh_live_group_anchor(rate: dict[str, Any]) -> bool:
    """A live book wins over any retrospective calibration signal."""

    anchor = rate.get("group_offer_anchor")
    if not isinstance(anchor, dict):
        return False
    return str(anchor.get("status") or "") == "OBSERVED"


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
    if _is_fresh_live_group_anchor(rate):
        return {
            "status": "SKIPPED_FRESH_LIVE_GROUP_ANCHOR",
            "actual_event_count": 0,
            "correction_ratio": 0.0,
        }

    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    as_of = as_of.astimezone(timezone.utc)
    since = as_of - timedelta(seconds=RECENT_REALIZED_LOOKBACK_SECONDS)
    rows = connection.execute(
        """
        SELECT actual_event_utc, residual_ratio
        FROM coin_estimate_predictions
        WHERE commodity=? AND settlement=?
          AND actual_event_utc IS NOT NULL
          AND residual_ratio IS NOT NULL
          AND actual_event_utc>=? AND actual_event_utc<=?
        ORDER BY actual_event_utc, id
        """,
        (commodity, settlement, _iso(since), _iso(as_of)),
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
