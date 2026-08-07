"""Morning reopen anchor for coin estimates before/at market open.

Ground-truth for evaluation uses the first 30 minutes after 10:00 Tehran:
confirmed trades if any exist in that window, otherwise a recency-weighted
offer average (newer offers weigh more because early quotes are often unripe).
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TEHRAN_TIMEZONE = timezone(timedelta(hours=3, minutes=30))
PRICE_MULTIPLIER = 1_000
GROUP_MIN_CONFIDENCE = 0.80
MORNING_OPEN_MINUTE = 10 * 60
MORNING_TRUTH_WINDOW_MINUTES = 30
MORNING_TRUTH_TAU_SECONDS = 10 * 60
REOPEN_WINDOW_START_MINUTE = 9 * 60 + 45  # 09:45 Tehran
REOPEN_WINDOW_END_MINUTE = 11 * 60  # 11:00 Tehran
METHOD_NAME = "MORNING_REOPEN_MELTED_HERAT_USDT_BLEND"
HERAT_BASIS_WEIGHT = {"CASH": 0.35, "TOMORROW": 0.60}
USDT_REGIME_WEIGHT = 0.05
DEFAULT_BLEND_FRESH = {
    "transferred": 0.72,
    "structural": 0.23,
    "basis": 0.05,
}
DEFAULT_BLEND_STALE = {
    "transferred": 0.35,
    "structural": 0.55,
    "basis": 0.10,
}
FRESH_ANCHOR_MAX_AGE_SECONDS = 36 * 3_600
MIN_TRANSFER_WEIGHT = 0.12
REOPEN_BAND_MULT_FRESH = 1.6
REOPEN_BAND_MULT_STALE = 2.2
REOPEN_RATIO_LOOKBACK_DAYS = 25
REOPEN_RATIO_HALF_LIFE_DAYS = 12.0
REOPEN_RATIO_REGIME_TAU = 0.35
REOPEN_RATIO_MIN_DAYS = 3


def tehran_local(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TEHRAN_TIMEZONE)


def tehran_day_bounds_utc(day: str) -> tuple[datetime, datetime]:
    """Return UTC [start, end) for a Tehran calendar day ``YYYY-MM-DD``."""

    local_start = datetime.fromisoformat(day + "T00:00:00").replace(tzinfo=TEHRAN_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def tehran_clock_utc(day: str, hour: int, minute: int = 0) -> datetime:
    local = datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(
        tzinfo=TEHRAN_TIMEZONE
    )
    return local.astimezone(timezone.utc)


def is_morning_reopen_window(
    end: datetime,
    *,
    has_live_anchor: bool = False,
    model: dict[str, Any] | None = None,
) -> bool:
    """True when estimates should prefer the morning reopen blend."""

    if has_live_anchor:
        return False
    policy = (model or {}).get("morning_reopen")
    # Opt-in only: live models without an explicit enabled policy keep the
    # previous freshness/structural blend until a calibrated candidate is promoted.
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return False
    local = tehran_local(end)
    minute = local.hour * 60 + local.minute
    return REOPEN_WINDOW_START_MINUTE <= minute < REOPEN_WINDOW_END_MINUTE


def _parse_stamp(value: str) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _weighted_mean(prices: list[float], weights: list[float]) -> float | None:
    if not prices or not weights or len(prices) != len(weights):
        return None
    total = sum(weights)
    if total <= 0:
        return None
    return sum(price * weight for price, weight in zip(prices, weights)) / total


def _recency_weights(stamps: list[datetime], *, window_end: datetime, tau_seconds: float) -> list[float]:
    weights = []
    for stamp in stamps:
        age = max(0.0, (window_end - stamp).total_seconds())
        weights.append(math.exp(-age / max(1.0, tau_seconds)))
    return weights


def morning_open_truth_label(
    conversation_db: Path,
    *,
    day: str,
    commodity: str,
    settlement: str,
    trade_form: str = "PHYSICAL",
    minimum_confidence: float = GROUP_MIN_CONFIDENCE,
    tau_seconds: float = MORNING_TRUTH_TAU_SECONDS,
) -> dict[str, Any]:
    """Build the opening ground-truth label for one commodity/settlement day."""

    empty = {
        "status": "NO_DATA",
        "day": day,
        "commodity": commodity,
        "settlement": settlement,
        "truth_price_toman": None,
        "truth_project_price": None,
        "source": None,
        "trade_count": 0,
        "offer_count": 0,
        "window_start_utc": None,
        "window_end_utc": None,
    }
    if not conversation_db.is_file():
        return empty
    window_start = tehran_clock_utc(day, 10, 0)
    window_end = window_start + timedelta(minutes=MORNING_TRUTH_WINDOW_MINUTES)
    empty["window_start_utc"] = window_start.isoformat().replace("+00:00", "Z")
    empty["window_end_utc"] = window_end.isoformat().replace("+00:00", "Z")

    connection = sqlite3.connect(f"file:{conversation_db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        trades: list[dict[str, Any]] = []
        offers: list[dict[str, Any]] = []
        if "confirmed_trades" in tables:
            quality_join = (
                "LEFT JOIN trade_market_quality tq ON tq.trade_id=t.id"
                if "trade_market_quality" in tables
                else ""
            )
            quality_filter = (
                "AND COALESCE(tq.training_eligible,1)=1"
                if "trade_market_quality" in tables
                else ""
            )
            trade_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(confirmed_trades)")
            }
            base_filter = (
                "AND COALESCE(t.training_eligible,1)=1"
                if "training_eligible" in trade_columns
                else ""
            )
            rows = connection.execute(
                f"""
                SELECT t.price, t.event_time_utc, t.confidence, t.quantity
                FROM confirmed_trades t {quality_join}
                WHERE t.commodity=? AND t.settlement=? AND t.trade_form=?
                  AND t.event_time_utc>=? AND t.event_time_utc<?
                  AND t.confidence>=? {base_filter} {quality_filter}
                ORDER BY t.event_time_utc ASC
                """,
                (
                    commodity,
                    settlement,
                    trade_form,
                    empty["window_start_utc"],
                    empty["window_end_utc"],
                    minimum_confidence,
                ),
            ).fetchall()
            for row in rows:
                trades.append(
                    {
                        "price_toman": float(row["price"]) * PRICE_MULTIPLIER,
                        "stamp": _parse_stamp(str(row["event_time_utc"])),
                        "confidence": float(row["confidence"] or 0.0),
                        "quantity": float(row["quantity"] or 1.0),
                    }
                )
        if {"offers", "messages"}.issubset(tables):
            quality_join = (
                "LEFT JOIN offer_market_quality q ON q.offer_id=o.id"
                if "offer_market_quality" in tables
                else ""
            )
            quality_filter = (
                "AND COALESCE(q.training_eligible,1)=1"
                if "offer_market_quality" in tables
                else ""
            )
            rows = connection.execute(
                f"""
                SELECT o.price, m.event_time_utc, o.confidence, o.quantity
                FROM offers o
                JOIN messages m ON m.import_id=o.import_id AND m.message_id=o.message_id
                {quality_join}
                WHERE o.commodity=? AND o.settlement=? AND o.trade_form=?
                  AND m.event_time_utc>=? AND m.event_time_utc<?
                  AND o.confidence>=? {quality_filter}
                ORDER BY m.event_time_utc ASC
                """,
                (
                    commodity,
                    settlement,
                    trade_form,
                    empty["window_start_utc"],
                    empty["window_end_utc"],
                    minimum_confidence,
                ),
            ).fetchall()
            for row in rows:
                offers.append(
                    {
                        "price_toman": float(row["price"]) * PRICE_MULTIPLIER,
                        "stamp": _parse_stamp(str(row["event_time_utc"])),
                        "confidence": float(row["confidence"] or 0.0),
                        "quantity": float(row["quantity"] or 1.0),
                    }
                )
    finally:
        connection.close()

    if trades:
        stamps = [row["stamp"] for row in trades]
        recency = _recency_weights(stamps, window_end=window_end, tau_seconds=tau_seconds)
        weights = [
            recency[i]
            * max(0.05, float(trades[i]["confidence"]))
            * math.sqrt(max(1.0, min(25.0, trades[i]["quantity"])))
            for i in range(len(trades))
        ]
        truth = _weighted_mean([row["price_toman"] for row in trades], weights)
        source = "RECENCY_WEIGHTED_CONFIRMED_TRADES_30M"
    elif offers:
        stamps = [row["stamp"] for row in offers]
        recency = _recency_weights(stamps, window_end=window_end, tau_seconds=tau_seconds)
        weights = [
            recency[i]
            * max(0.05, float(offers[i]["confidence"]))
            * math.sqrt(max(1.0, min(25.0, offers[i]["quantity"])))
            for i in range(len(offers))
        ]
        truth = _weighted_mean([row["price_toman"] for row in offers], weights)
        source = "RECENCY_WEIGHTED_OFFERS_30M"
    else:
        return empty

    if truth is None or truth <= 0:
        return empty
    rounded = int(round(truth / 50_000) * 50_000)
    return {
        "status": "OBSERVED",
        "day": day,
        "commodity": commodity,
        "settlement": settlement,
        "truth_price_toman": float(truth),
        "truth_project_price": int(round(rounded / PRICE_MULTIPLIER)),
        "truth_price_toman_rounded": rounded,
        "source": source,
        "trade_count": len(trades),
        "offer_count": len(offers),
        "window_start_utc": empty["window_start_utc"],
        "window_end_utc": empty["window_end_utc"],
        "tau_seconds": tau_seconds,
    }


def resolve_reopen_blend_weights(
    *,
    anchor_age_seconds: float | None,
    model: dict[str, Any] | None = None,
) -> dict[str, float]:
    policy = (model or {}).get("morning_reopen") or {}
    fresh = dict(DEFAULT_BLEND_FRESH)
    stale = dict(DEFAULT_BLEND_STALE)
    fresh.update(policy.get("blend_fresh") or {})
    stale.update(policy.get("blend_stale") or {})
    age = float(anchor_age_seconds if anchor_age_seconds is not None else 1e12)
    chosen = fresh if age <= float(policy.get("fresh_max_age_seconds", FRESH_ANCHOR_MAX_AGE_SECONDS)) else stale
    total = sum(max(0.0, float(chosen.get(key, 0.0))) for key in ("transferred", "structural", "basis"))
    if total <= 0:
        return dict(DEFAULT_BLEND_STALE)
    return {key: max(0.0, float(chosen.get(key, 0.0))) / total for key in ("transferred", "structural", "basis")}


def reopen_band_multiplier(*, anchor_age_seconds: float | None, model: dict[str, Any] | None = None) -> float:
    policy = (model or {}).get("morning_reopen") or {}
    age = float(anchor_age_seconds if anchor_age_seconds is not None else 1e12)
    fresh_max = float(policy.get("fresh_max_age_seconds", FRESH_ANCHOR_MAX_AGE_SECONDS))
    if age <= fresh_max:
        return float(policy.get("band_multiplier_fresh", REOPEN_BAND_MULT_FRESH))
    return float(policy.get("band_multiplier_stale", REOPEN_BAND_MULT_STALE))


def _window_recency_mean(
    rows: list[dict[str, Any]], *, window_end: datetime, tau_seconds: float
) -> float | None:
    if not rows:
        return None
    stamps = [row["stamp"] for row in rows]
    recency = _recency_weights(stamps, window_end=window_end, tau_seconds=tau_seconds)
    weights = [
        recency[i]
        * max(0.05, float(rows[i]["confidence"]))
        * float(rows[i].get("source_factor") or 1.0)
        for i in range(len(rows))
    ]
    return _weighted_mean([float(row["price"]) for row in rows], weights)


def select_reopen_cash_tomorrow_ratio(
    conversation_db: Path,
    *,
    commodity: str,
    trade_form: str = "PHYSICAL",
    end: datetime,
    current_regime_score: float | None = None,
    market_db: Path | None = None,
    lookback_days: int = REOPEN_RATIO_LOOKBACK_DAYS,
    minimum_confidence: float = GROUP_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Learn tomorrow/cash from previous Tehran reopen windows, regime-weighted.

    For each prior day, uses the [10:00, 10:30) Tehran book: confirmed trades
    preferred via higher source weight, otherwise offers.  Day weights combine
    calendar recency with similarity of market-regime score at that reopen.
    """

    empty = {
        "status": "NO_DATA",
        "ratio": None,
        "relative_qhat": None,
        "pair_count": 0,
        "day_count": 0,
        "scope": None,
        "selection": "PREVIOUS_REOPEN_CASH_TOMORROW_SPREAD",
    }
    if not conversation_db.is_file():
        return empty
    end = end.astimezone(timezone.utc)
    local_today = tehran_local(end).date()
    day_ratios: list[dict[str, Any]] = []

    connection = sqlite3.connect(f"file:{conversation_db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    market_connection = None
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if market_db is not None and market_db.is_file():
            market_connection = sqlite3.connect(
                f"file:{market_db.resolve()}?mode=ro", uri=True
            )
            market_connection.row_factory = sqlite3.Row

        for offset in range(1, max(2, lookback_days) + 1):
            day = (local_today - timedelta(days=offset)).isoformat()
            window_start = tehran_clock_utc(day, 10, 0)
            window_end = window_start + timedelta(minutes=MORNING_TRUTH_WINDOW_MINUTES)
            start_iso = window_start.isoformat().replace("+00:00", "Z")
            end_iso = window_end.isoformat().replace("+00:00", "Z")
            cash_rows: list[dict[str, Any]] = []
            tom_rows: list[dict[str, Any]] = []

            if "confirmed_trades" in tables:
                quality_join = (
                    "LEFT JOIN trade_market_quality tq ON tq.trade_id=t.id"
                    if "trade_market_quality" in tables
                    else ""
                )
                quality_filter = (
                    "AND COALESCE(tq.training_eligible,1)=1"
                    if "trade_market_quality" in tables
                    else ""
                )
                trade_columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(confirmed_trades)")
                }
                base_filter = (
                    "AND COALESCE(t.training_eligible,1)=1"
                    if "training_eligible" in trade_columns
                    else ""
                )
                for settlement, bucket in (("CASH", cash_rows), ("TOMORROW", tom_rows)):
                    for row in connection.execute(
                        f"""
                        SELECT t.price, t.event_time_utc, t.confidence
                        FROM confirmed_trades t {quality_join}
                        WHERE t.commodity=? AND t.settlement=? AND t.trade_form=?
                          AND t.event_time_utc>=? AND t.event_time_utc<?
                          AND t.confidence>=? {base_filter} {quality_filter}
                        """,
                        (
                            commodity,
                            settlement,
                            trade_form,
                            start_iso,
                            end_iso,
                            max(0.85, minimum_confidence),
                        ),
                    ):
                        bucket.append(
                            {
                                "price": float(row["price"]),
                                "stamp": _parse_stamp(str(row["event_time_utc"])),
                                "confidence": float(row["confidence"] or 0.0),
                                "source_factor": 3.0,
                            }
                        )

            if {"offers", "messages"}.issubset(tables):
                quality_join = (
                    "LEFT JOIN offer_market_quality q ON q.offer_id=o.id"
                    if "offer_market_quality" in tables
                    else ""
                )
                quality_filter = (
                    "AND COALESCE(q.training_eligible,1)=1"
                    if "offer_market_quality" in tables
                    else ""
                )
                for settlement, bucket in (("CASH", cash_rows), ("TOMORROW", tom_rows)):
                    for row in connection.execute(
                        f"""
                        SELECT o.price, m.event_time_utc, o.confidence
                        FROM offers o
                        JOIN messages m ON m.import_id=o.import_id AND m.message_id=o.message_id
                        {quality_join}
                        WHERE o.commodity=? AND o.settlement=? AND o.trade_form=?
                          AND m.event_time_utc>=? AND m.event_time_utc<?
                          AND o.confidence>=? {quality_filter}
                        """,
                        (
                            commodity,
                            settlement,
                            trade_form,
                            start_iso,
                            end_iso,
                            minimum_confidence,
                        ),
                    ):
                        bucket.append(
                            {
                                "price": float(row["price"]),
                                "stamp": _parse_stamp(str(row["event_time_utc"])),
                                "confidence": float(row["confidence"] or 0.0),
                                "source_factor": 1.0,
                            }
                        )

            cash_px = _window_recency_mean(
                cash_rows, window_end=window_end, tau_seconds=MORNING_TRUTH_TAU_SECONDS
            )
            tom_px = _window_recency_mean(
                tom_rows, window_end=window_end, tau_seconds=MORNING_TRUTH_TAU_SECONDS
            )
            if cash_px is None or tom_px is None or cash_px <= 0:
                continue
            ratio = tom_px / cash_px
            if not 0.95 <= ratio <= 1.08:
                continue

            age_days = max(1.0, float(offset))
            recency = math.exp(-math.log(2.0) * age_days / REOPEN_RATIO_HALF_LIFE_DAYS)
            regime_score = None
            regime_weight = 1.0
            if market_connection is not None:
                try:
                    from core.market_intelligence.conversation_quality import (
                        detect_market_regime,
                    )

                    mid = window_start + timedelta(minutes=15)
                    regime = detect_market_regime(market_connection, mid, "CASH")
                    regime_score = regime.get("direction_score")
                    if (
                        current_regime_score is not None
                        and regime_score is not None
                        and math.isfinite(float(regime_score))
                    ):
                        delta = abs(float(current_regime_score) - float(regime_score))
                        regime_weight = math.exp(-delta / REOPEN_RATIO_REGIME_TAU)
                except Exception:
                    regime_score = None
                    regime_weight = 1.0
            day_ratios.append(
                {
                    "day": day,
                    "ratio": ratio,
                    "weight": recency * regime_weight,
                    "recency_weight": recency,
                    "regime_weight": regime_weight,
                    "regime_score": regime_score,
                    "cash_project": cash_px,
                    "tomorrow_project": tom_px,
                    "cash_count": len(cash_rows),
                    "tomorrow_count": len(tom_rows),
                }
            )
    finally:
        connection.close()
        if market_connection is not None:
            market_connection.close()

    if len(day_ratios) < REOPEN_RATIO_MIN_DAYS:
        empty["day_count"] = len(day_ratios)
        return empty

    # Robust center: weighted median after MAD filter.
    ratios = [float(row["ratio"]) for row in day_ratios]
    weights = [max(1e-9, float(row["weight"])) for row in day_ratios]
    order = sorted(range(len(ratios)), key=lambda i: ratios[i])
    total = sum(weights)
    acc = 0.0
    center = ratios[order[len(order) // 2]]
    for index in order:
        acc += weights[index]
        if acc >= 0.5 * total:
            center = ratios[index]
            break
    abs_dev = [abs(value - center) for value in ratios]
    mad_order = sorted(range(len(abs_dev)), key=lambda i: abs_dev[i])
    acc = 0.0
    mad = abs_dev[mad_order[len(mad_order) // 2]]
    for index in mad_order:
        acc += weights[index]
        if acc >= 0.5 * total:
            mad = abs_dev[index]
            break
    robust_limit = max(0.006, 4.0 * mad)
    filtered = [
        row
        for row in day_ratios
        if abs(float(row["ratio"]) - center) <= robust_limit
    ]
    if len(filtered) < REOPEN_RATIO_MIN_DAYS:
        filtered = day_ratios
    ratios = [float(row["ratio"]) for row in filtered]
    weights = [max(1e-9, float(row["weight"])) for row in filtered]
    total = sum(weights)
    center = sum(r * w for r, w in zip(ratios, weights)) / total
    abs_err = [abs(value - center) for value in ratios]
    # Approximate weighted 80% quantile for band width.
    err_order = sorted(range(len(abs_err)), key=lambda i: abs_err[i])
    acc = 0.0
    qhat = abs_err[err_order[-1]]
    for index in err_order:
        acc += weights[index]
        if acc >= 0.8 * total:
            qhat = abs_err[index]
            break
    qhat = max(0.0025, min(0.012, qhat))
    return {
        "status": "OBSERVED",
        "ratio": float(center),
        "relative_qhat": float(qhat),
        "pair_count": len(filtered),
        "day_count": len(filtered),
        "scope": "COMMODITY_PREVIOUS_REOPENS",
        "selection": "PREVIOUS_REOPEN_CASH_TOMORROW_SPREAD_REGIME_WEIGHTED",
        "latest_pair_utc": tehran_clock_utc(filtered[0]["day"], 10, 15)
        .isoformat()
        .replace("+00:00", "Z"),
        "age_seconds": max(
            0.0,
            (
                end - tehran_clock_utc(min(row["day"] for row in filtered), 10, 15)
            ).total_seconds(),
        ),
        "current_regime_score": current_regime_score,
        "sample_days": [
            {
                "day": row["day"],
                "ratio": row["ratio"],
                "weight": row["weight"],
                "regime_score": row["regime_score"],
            }
            for row in sorted(filtered, key=lambda item: item["day"], reverse=True)[:8]
        ],
    }


def resolve_cash_tomorrow_ratio_for_estimate(
    *,
    empirical: dict[str, Any],
    reopen: dict[str, Any],
    end: datetime,
) -> dict[str, Any]:
    """Prefer previous-reopen spread near open; blend with all-day empirical."""

    emp_ok = empirical.get("status") == "OBSERVED" and empirical.get("ratio") is not None
    re_ok = reopen.get("status") == "OBSERVED" and reopen.get("ratio") is not None
    if not emp_ok and not re_ok:
        return empirical if empirical else reopen
    if re_ok and not emp_ok:
        out = dict(reopen)
        out["blend"] = {"reopen": 1.0, "empirical": 0.0}
        return out
    if emp_ok and not re_ok:
        out = dict(empirical)
        out["blend"] = {"reopen": 0.0, "empirical": 1.0}
        out["reopen_anchor"] = reopen
        return out

    local = tehran_local(end)
    minute = local.hour * 60 + local.minute
    # Around reopen, trust previous reopen spreads most; later in the day lean
    # back toward the broader near-synchronous empirical ratio.
    if REOPEN_WINDOW_START_MINUTE <= minute < (10 * 60 + 45):
        reopen_w = 0.80
    elif minute < 13 * 60:
        reopen_w = 0.55
    else:
        reopen_w = 0.30
    emp_w = 1.0 - reopen_w
    ratio = reopen_w * float(reopen["ratio"]) + emp_w * float(empirical["ratio"])
    qhat = math.sqrt(
        (reopen_w * float(reopen.get("relative_qhat") or 0.004)) ** 2
        + (emp_w * float(empirical.get("relative_qhat") or 0.004)) ** 2
    )
    return {
        "status": "OBSERVED",
        "ratio": float(ratio),
        "relative_qhat": max(0.0025, min(0.015, qhat)),
        "pair_count": int(reopen.get("pair_count") or 0)
        + int(empirical.get("pair_count") or 0),
        "day_count": reopen.get("day_count"),
        "scope": "REOPEN_REGIME_BLEND_WITH_EMPIRICAL",
        "selection": "PREVIOUS_REOPEN_SPREAD_BLEND_EMPIRICAL",
        "blend": {"reopen": reopen_w, "empirical": emp_w},
        "reopen_anchor": {
            "ratio": reopen.get("ratio"),
            "day_count": reopen.get("day_count"),
            "selection": reopen.get("selection"),
            "sample_days": reopen.get("sample_days"),
        },
        "empirical_anchor": {
            "ratio": empirical.get("ratio"),
            "pair_count": empirical.get("pair_count"),
            "scope": empirical.get("scope"),
            "selection": empirical.get("selection"),
        },
        "latest_pair_utc": reopen.get("latest_pair_utc") or empirical.get("latest_pair_utc"),
        "age_seconds": reopen.get("age_seconds"),
    }


def build_morning_reopen_anchor(
    *,
    intrinsic: float,
    structural: float,
    transferred: float | None,
    anchor_age_seconds: float | None,
    current_herat: float | None,
    anchor_herat: float | None,
    current_melted: float | None,
    anchor_melted: float | None,
    current_usdt: float | None,
    anchor_usdt: float | None,
    settlement: str,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Blend transferred, structural, and limited Herat/USDT basis for reopen."""

    weights = resolve_reopen_blend_weights(anchor_age_seconds=anchor_age_seconds, model=model)
    transfer_w = weights["transferred"]
    structural_w = weights["structural"]
    basis_w = weights["basis"]

    if transferred is None or transferred <= 0:
        # No coin anchor: put transfer mass onto structural, keep a small basis.
        structural_w = structural_w + transfer_w * 0.85
        basis_w = basis_w + transfer_w * 0.15
        transfer_w = 0.0
        transferred_value = structural
    else:
        # Keep a floor on historical transfer after multi-day closures.
        transfer_w = max(MIN_TRANSFER_WEIGHT, transfer_w)
        residual = 1.0 - transfer_w
        scale = residual / max(1e-9, structural_w + basis_w)
        structural_w *= scale
        basis_w *= scale
        transferred_value = float(transferred)

    herat_basis_relative = 0.0
    if (
        current_herat is not None
        and anchor_herat is not None
        and float(anchor_herat) > 0
        and current_melted is not None
        and anchor_melted is not None
        and float(anchor_melted) > 0
    ):
        herat_change = float(current_herat) / float(anchor_herat) - 1.0
        melted_change = float(current_melted) / float(anchor_melted) - 1.0
        herat_basis_relative = herat_change - melted_change

    usdt_regime = 0.0
    if current_usdt is not None and anchor_usdt is not None and float(anchor_usdt) > 0:
        usdt_regime = float(current_usdt) / float(anchor_usdt) - 1.0
        # Deadband: ignore tiny noise.
        if abs(usdt_regime) < 0.001:
            usdt_regime = 0.0

    herat_w = HERAT_BASIS_WEIGHT.get(settlement, 0.35)
    # Tight clamp: basis is a gentle reopen correction, not a second model.
    herat_basis_relative = max(-0.03, min(0.03, herat_basis_relative))
    usdt_regime = max(-0.02, min(0.02, usdt_regime))
    basis_price = float(intrinsic) * (
        1.0 + herat_w * herat_basis_relative + USDT_REGIME_WEIGHT * usdt_regime
    )
    basis_price = max(float(intrinsic) * 0.97, min(float(intrinsic) * 1.03, basis_price))

    estimate = (
        transfer_w * transferred_value
        + structural_w * float(structural)
        + basis_w * basis_price
    )
    rounded = int(round(estimate / 50_000) * 50_000)
    band_mult = reopen_band_multiplier(anchor_age_seconds=anchor_age_seconds, model=model)
    return {
        "status": "ESTIMATED",
        "method": METHOD_NAME,
        "estimate_toman": float(estimate),
        "estimated_price_toman": rounded,
        "estimated_project_price": int(round(rounded / PRICE_MULTIPLIER)),
        "blend_weights": {
            "transferred": transfer_w,
            "structural": structural_w,
            "basis": basis_w,
        },
        "transferred_price_toman": transferred_value if transfer_w > 0 else None,
        "structural_price_toman": float(structural),
        "basis_price_toman": basis_price,
        "herat_basis_relative": herat_basis_relative,
        "usdt_regime_return": usdt_regime,
        "band_multiplier": band_mult,
        "anchor_age_seconds": anchor_age_seconds,
    }


def widen_tolerance(tolerance: dict[str, Any], *, multiplier: float, center_toman: float) -> dict[str, Any]:
    """Widen an existing tolerance dict around the reopen point."""

    out = dict(tolerance)
    mult = max(1.0, float(multiplier))
    lower = out.get("lower_price_toman")
    upper = out.get("upper_price_toman")
    if lower is None or upper is None:
        # Fall back to percent fields if present.
        neg = float(out.get("negative_tolerance_percent") or 0.0) / 100.0
        pos = float(out.get("positive_tolerance_percent") or 0.0) / 100.0
        if neg <= 0 and pos <= 0:
            neg = pos = 0.01
        lower = center_toman * (1.0 - neg * mult)
        upper = center_toman * (1.0 + pos * mult)
    else:
        half_span = max(abs(float(center_toman) - float(lower)), abs(float(upper) - float(center_toman)))
        lower = float(center_toman) - half_span * mult
        upper = float(center_toman) + half_span * mult
    lower_r = int(round(float(lower) / 50_000) * 50_000)
    upper_r = int(round(float(upper) / 50_000) * 50_000)
    if upper_r <= lower_r:
        upper_r = lower_r + 50_000
    out["lower_price_toman"] = lower_r
    out["upper_price_toman"] = upper_r
    out["lower_project_price"] = int(round(lower_r / PRICE_MULTIPLIER))
    out["upper_project_price"] = int(round(upper_r / PRICE_MULTIPLIER))
    out["source"] = "MORNING_REOPEN_WIDENED_BAND"
    out["band_multiplier"] = mult
    # Keep percent diagnostics consistent with the widened band.
    if center_toman > 0:
        out["negative_tolerance_percent"] = (1.0 - lower_r / float(center_toman)) * 100.0
        out["positive_tolerance_percent"] = (upper_r / float(center_toman) - 1.0) * 100.0
    return out


def list_tehran_days_with_activity(
    conversation_db: Path,
    *,
    start_day: str | None = None,
    end_day: str | None = None,
) -> list[str]:
    """Return Tehran days that have any offer/trade activity (UTC date proxy + shift)."""

    if not conversation_db.is_file():
        return []
    connection = sqlite3.connect(f"file:{conversation_db.resolve()}?mode=ro", uri=True)
    try:
        stamps: list[str] = []
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if {"offers", "messages"}.issubset(tables):
            stamps.extend(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT m.event_time_utc
                    FROM offers o
                    JOIN messages m ON m.import_id=o.import_id AND m.message_id=o.message_id
                    """
                )
            )
        if "confirmed_trades" in tables:
            stamps.extend(
                str(row[0])
                for row in connection.execute("SELECT DISTINCT event_time_utc FROM confirmed_trades")
            )
    finally:
        connection.close()
    days: set[str] = set()
    for stamp in stamps:
        try:
            day = tehran_local(_parse_stamp(stamp)).date().isoformat()
        except ValueError:
            continue
        if start_day and day < start_day:
            continue
        if end_day and day > end_day:
            continue
        days.add(day)
    return sorted(days)
