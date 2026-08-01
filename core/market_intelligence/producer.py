"""Runtime-only deterministic coin-range snapshot producer.

The producer reads normalized market observations from a read-only SQLite
source, applies the verified numerical model bundled with the application, and
emits a data-minimized Shadow snapshot. Training, Telegram collection, secrets,
sessions, raw exports, and project-database access are outside this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.market_intelligence.anchor_transfer import (
    apply_coin_anchor_transfer_overlay,
    enriched_event_from_mapping,
)
from core.market_intelligence.bundle import LoadedRuntimeBundle
from core.market_intelligence.low_date import (
    apply_low_date_intrinsic_overlay,
    read_physical_melted_reference,
)
from core.market_intelligence.regime import detect_market_regime


PRODUCER_VERSION = "COIN_SNAPSHOT_PRODUCER_V1"
PRICE_MULTIPLIER = 1_000
WINDOW_SECONDS = 60
NO_DATA_TOKEN = "<NO_DATA_THIS_MINUTE>"
USD_HERAT_ANCHOR_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
USDT_ANCHOR_WINDOW_SECONDS = 180
USDT_TREND_DEADBAND_RELATIVE = 0.001
GROUP_MIN_CONFIDENCE = 0.80
FLOW_WINDOW_SECONDS = 600
FLOW_HALF_LIFE_SECONDS = 180
PAPER_FLOW_WEIGHT = 0.50
UNKNOWN_SETTLEMENT_PAPER_FLOW_WEIGHT = 0.25
FLOW_TOLERANCE_EXPANSION_MAX = 0.75
OFFER_LIVE_SECONDS = 5 * 60
GROUP_ANCHOR_WINDOW_SECONDS = OFFER_LIVE_SECONDS


class SnapshotProducerError(RuntimeError):
    """Raised when a safe deterministic snapshot cannot be produced."""


@dataclass(frozen=True)
class CommoditySpec:
    name: str
    coefficient: float
    low_date: bool = False


COMMODITY_SPECS: dict[str, CommoditySpec] = {
    "امام": CommoditySpec("امام", 2.253),
    "بهار": CommoditySpec("بهار", 2.253, low_date=True),
    "ربع بهار": CommoditySpec("ربع بهار", 2.253 / 4),
    "نیم بهار": CommoditySpec("نیم بهار", 2.253 / 2),
    "ربع تاریخ پایین": CommoditySpec("ربع تاریخ پایین", 2.253 / 4, low_date=True),
    "نیم تاریخ پایین": CommoditySpec("نیم تاریخ پایین", 2.253 / 2, low_date=True),
    "یک گرمی": CommoditySpec("یک گرمی", 2.253 / 8.130),
}


SETTLEMENT_CONFIG = {
    "CASH": {
        "settlement_term": "TODAY",
        "trade_form": "PHYSICAL",
        "melted_market_label": "آبشده نقدی",
        "melted_candidates": (
            ("آبشده نقدی", "PHYSICAL", "PRIMARY"),
            (
                "آبشده رسمی",
                "PHYSICAL",
                "SAME_MINUTE_PHYSICAL_UNDERLYING_FALLBACK",
            ),
        ),
        "coin_market_label": "سکه نقدی",
        "coin_candidates": (
            (
                "سکه نقدی",
                "TODAY",
                "PHYSICAL",
                "EXACT_TODAY_PHYSICAL",
            ),
            (
                "سکه نقدی",
                "UNKNOWN",
                "PHYSICAL",
                "CASH_PHYSICAL_UNKNOWN_SETTLEMENT",
            ),
        ),
        "usd_candidates": (
            ("TODAY", "PHYSICAL"),
            ("UNKNOWN", "PHYSICAL"),
        ),
    },
    "TOMORROW": {
        "settlement_term": "TOMORROW",
        "trade_form": "PHYSICAL",
        # All non-cash/non-official melted quotes are PAPER. For a future coin
        # estimate they remain an explicitly named reference, never physical.
        "melted_market_label": "آبشده فردایی",
        "melted_candidates": (
            (
                "آبشده فردایی",
                "PAPER",
                "EXACT_TOMORROW_PAPER_REFERENCE",
            ),
            (
                "آبشده حواله",
                "PAPER",
                "PAPER_TOMORROW_PROXY_UNKNOWN_SETTLEMENT",
            ),
            (
                "آبشده غیررسمی",
                "PAPER",
                "PAPER_TOMORROW_PROXY_UNKNOWN_SETTLEMENT",
            ),
        ),
        # The available tomorrow reference is explicitly paper/havale. It is
        # a named proxy with lower confidence, never relabelled as physical.
        "coin_market_label": "سکه حواله",
        "coin_candidates": (
            (
                "سکه حواله",
                "TOMORROW",
                "PAPER",
                "TOMORROW_PAPER_PROXY",
            ),
            (
                "سکه حواله",
                "UNKNOWN",
                "PAPER",
                "TOMORROW_PAPER_PROXY_UNKNOWN_SETTLEMENT",
            ),
        ),
        "usd_candidates": (("TOMORROW", "PAPER"),),
    },
}


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def weighted_quantile(
    values: Sequence[float], weights: Sequence[float], probability: float
) -> float:
    if not values or len(values) != len(weights):
        raise ValueError("weighted_quantile requires equal non-empty inputs")
    ordered = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(max(0.0, weight) for _, weight in ordered)
    if total <= 0:
        raise ValueError("weighted_quantile requires positive total weight")
    target = total * probability
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += max(0.0, weight)
        if cumulative >= target:
            return value
    return ordered[-1][0]


def connect_market_db(path: Path, *, read_only: bool = True) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Market database does not exist: {resolved}")
    if read_only:
        connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(resolved)
    connection.row_factory = sqlite3.Row
    return connection


REQUIRED_PRICE_EVENT_COLUMNS = frozenset(
    {
        "id",
        "event_time_utc",
        "instrument",
        "market_label",
        "settlement_term",
        "trade_form",
        "event_type",
        "side",
        "price_num",
        "price_unit",
        "parse_confidence",
        "quantity_num",
    }
)

REQUIRED_EXTERNAL_OBSERVATION_COLUMNS = frozenset(
    {
        "id",
        "observed_at_utc",
        "instrument_code",
        "quote_kind",
        "normalized_price_num",
        "interval_seconds",
        "volume_value",
    }
)


def validate_market_database(path: Path) -> None:
    """Validate the minimal normalized-observation contract read-only."""
    with closing(connect_market_db(path)) as connection:
        if not _table_exists(connection, "price_events"):
            raise SnapshotProducerError(
                "market_database_price_events_missing"
            )
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(price_events)"
            )
        }
        missing = sorted(REQUIRED_PRICE_EVENT_COLUMNS - columns)
        if missing:
            raise SnapshotProducerError(
                "market_database_price_events_columns_missing:"
                + ",".join(missing)
            )
        if _table_exists(connection, "external_market_observations"):
            external_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(external_market_observations)"
                )
            }
            external_missing = sorted(
                REQUIRED_EXTERNAL_OBSERVATION_COLUMNS - external_columns
            )
            if external_missing:
                raise SnapshotProducerError(
                    "market_database_external_columns_missing:"
                    + ",".join(external_missing)
                )


def average_market_value(
    connection: sqlite3.Connection,
    *,
    end: datetime,
    seconds: int = WINDOW_SECONDS,
    instrument: str | None = None,
    market_label: str | None = None,
    settlement_term: str | None = None,
    trade_form: str | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    start = end - timedelta(seconds=seconds)
    clauses = ["event_time_utc > ?", "event_time_utc <= ?"]
    parameters: list[Any] = [iso_utc(start), iso_utc(end)]
    for column, value in (
        ("instrument", instrument),
        ("market_label", market_label),
        ("settlement_term", settlement_term),
        ("trade_form", trade_form),
        ("event_type", event_type),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    row = connection.execute(
        f"""
        SELECT AVG(price_num) AS average_price,
               MIN(price_num) AS minimum_price,
               MAX(price_num) AS maximum_price,
               COUNT(*) AS sample_count,
               MIN(event_time_utc) AS first_event_utc,
               MAX(event_time_utc) AS last_event_utc
        FROM price_events
        WHERE {' AND '.join(clauses)}
        """,
        parameters,
    ).fetchone()
    count = int(row["sample_count"])
    if count == 0:
        return {
            "status": "NO_DATA",
            "llm_value": NO_DATA_TOKEN,
            "average_price": None,
            "minimum_price": None,
            "maximum_price": None,
            "sample_count": 0,
            "first_event_utc": None,
            "last_event_utc": None,
        }
    average = float(row["average_price"])
    return {
        "status": "OBSERVED",
        "llm_value": average,
        "average_price": average,
        "minimum_price": float(row["minimum_price"]),
        "maximum_price": float(row["maximum_price"]),
        "sample_count": count,
        "first_event_utc": str(row["first_event_utc"]),
        "last_event_utc": str(row["last_event_utc"]),
    }


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )


def average_external_market_value(
    connection: sqlite3.Connection,
    *,
    end: datetime,
    instrument_code: str,
    quote_kinds: Sequence[str],
    seconds: int = WINDOW_SECONDS,
) -> dict[str, Any]:
    """Read normalized API quotes without mixing bid/ask/close semantics."""
    empty = {
        "status": "NO_DATA",
        "llm_value": NO_DATA_TOKEN,
        "average_price": None,
        "minimum_price": None,
        "maximum_price": None,
        "sample_count": 0,
        "first_event_utc": None,
        "last_event_utc": None,
        "source": "EXTERNAL_API",
        "selected_quote_kind": None,
    }
    if not _table_exists(connection, "external_market_observations"):
        return empty
    start = end - timedelta(seconds=seconds)
    for quote_kind in quote_kinds:
        row = connection.execute(
            """
            SELECT AVG(normalized_price_num) AS average_price,
                   MIN(normalized_price_num) AS minimum_price,
                   MAX(normalized_price_num) AS maximum_price,
                   COUNT(*) AS sample_count,
                   MIN(observed_at_utc) AS first_event_utc,
                   MAX(observed_at_utc) AS last_event_utc
            FROM external_market_observations
            WHERE instrument_code = ?
              AND quote_kind = ?
              AND observed_at_utc > ? AND observed_at_utc <= ?
              AND normalized_price_num IS NOT NULL
              AND (
                    instrument_code <> 'USDT_IRT'
                    OR interval_seconds = 0
                    OR CAST(volume_value AS REAL) > 0
              )
            """,
            (instrument_code, quote_kind, iso_utc(start), iso_utc(end)),
        ).fetchone()
        if row is None or int(row["sample_count"] or 0) == 0:
            continue
        average = float(row["average_price"])
        return {
            "status": "OBSERVED",
            "llm_value": average,
            "average_price": average,
            "minimum_price": float(row["minimum_price"]),
            "maximum_price": float(row["maximum_price"]),
            "sample_count": int(row["sample_count"]),
            "first_event_utc": str(row["first_event_utc"]),
            "last_event_utc": str(row["last_event_utc"]),
            "source": "EXTERNAL_API",
            "selected_quote_kind": quote_kind,
        }
    return empty


def select_usd_average(
    connection: sqlite3.Connection,
    settlement: str,
    end: datetime,
    *,
    seconds: int = WINDOW_SECONDS,
) -> dict[str, Any]:
    config = SETTLEMENT_CONFIG[settlement]
    last: dict[str, Any] | None = None
    for event_type in ("TRADE", "OFFER"):
        for candidate_index, (settlement_term, trade_form) in enumerate(
            config["usd_candidates"]
        ):
            value = average_market_value(
                connection,
                end=end,
                seconds=seconds,
                instrument="USD_HERAT",
                settlement_term=str(settlement_term),
                trade_form=str(trade_form),
                event_type=event_type,
            )
            last = value
            if value["status"] == "OBSERVED":
                value["selection"] = (
                    event_type
                    if candidate_index == 0
                    else f"{event_type}_SETTLEMENT_FALLBACK"
                )
                value["selected_settlement_term"] = settlement_term
                value["selected_trade_form"] = trade_form
                return value
    assert last is not None
    last["selection"] = "NO_DATA"
    last["selected_settlement_term"] = None
    last["selected_trade_form"] = None
    return last


def select_usdt_average(
    connection: sqlite3.Connection,
    end: datetime,
    *,
    seconds: int = WINDOW_SECONDS,
) -> dict[str, Any]:
    value = average_external_market_value(
        connection,
        end=end,
        instrument_code="USDT_IRT",
        quote_kinds=("MID", "CLOSE", "LAST"),
        seconds=seconds,
    )
    value["selection"] = (
        "WALLEX_USDT_IRT" if value["status"] == "OBSERVED" else "NO_DATA"
    )
    return value


def select_latest_usd_anchor(
    connection: sqlite3.Connection,
    settlement: str,
    end: datetime,
    *,
    maximum_age_seconds: int = USD_HERAT_ANCHOR_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Return the latest real Herat market anchor for one settlement.

    This deliberately never returns a USDT price.  A short robust window around
    the last Herat event is used so a lone bid/ask does not become the anchor.
    """
    config = SETTLEMENT_CONFIG[settlement]
    start = end - timedelta(seconds=maximum_age_seconds)
    candidates: list[tuple[datetime, int, sqlite3.Row, str, str]] = []
    for candidate_index, (settlement_term, trade_form) in enumerate(
        config["usd_candidates"]
    ):
        row = connection.execute(
            """
            SELECT id, event_time_utc, price_num, event_type
            FROM price_events
            WHERE instrument = 'USD_HERAT'
              AND settlement_term = ?
              AND trade_form = ?
              AND event_type IN ('TRADE', 'OFFER')
              AND event_time_utc > ? AND event_time_utc <= ?
              AND price_num IS NOT NULL AND price_num > 0
            ORDER BY event_time_utc DESC, id DESC
            LIMIT 1
            """,
            (
                str(settlement_term),
                str(trade_form),
                iso_utc(start),
                iso_utc(end),
            ),
        ).fetchone()
        if row is not None:
            candidates.append(
                (
                    parse_datetime(str(row["event_time_utc"])),
                    candidate_index,
                    row,
                    str(settlement_term),
                    str(trade_form),
                )
            )
    if not candidates:
        return {
            "status": "NO_DATA",
            "average_price": None,
            "minimum_price": None,
            "maximum_price": None,
            "sample_count": 0,
            "first_event_utc": None,
            "last_event_utc": None,
            "selection": "NO_HERAT_ANCHOR",
        }
    anchor_at, candidate_index, latest, settlement_term, trade_form = max(
        candidates,
        key=lambda item: (item[0], -item[1], int(item[2]["id"])),
    )
    robust_start = anchor_at - timedelta(seconds=WINDOW_SECONDS)
    summary = connection.execute(
        """
        SELECT AVG(price_num) AS average_price,
               MIN(price_num) AS minimum_price,
               MAX(price_num) AS maximum_price,
               COUNT(*) AS sample_count,
               MIN(event_time_utc) AS first_event_utc,
               MAX(event_time_utc) AS last_event_utc
        FROM price_events
        WHERE instrument = 'USD_HERAT'
          AND settlement_term = ?
          AND trade_form = ?
          AND event_type IN ('TRADE', 'OFFER')
          AND event_time_utc > ? AND event_time_utc <= ?
          AND price_num IS NOT NULL AND price_num > 0
        """,
        (
            settlement_term,
            trade_form,
            iso_utc(robust_start),
            iso_utc(anchor_at),
        ),
    ).fetchone()
    count = int(summary["sample_count"] or 0)
    if count <= 0:
        price = float(latest["price_num"])
        first_event = str(latest["event_time_utc"])
        last_event = first_event
        minimum = price
        maximum = price
        count = 1
    else:
        price = float(summary["average_price"])
        first_event = str(summary["first_event_utc"])
        last_event = str(summary["last_event_utc"])
        minimum = float(summary["minimum_price"])
        maximum = float(summary["maximum_price"])
    return {
        "status": "OBSERVED",
        "llm_value": price,
        "average_price": price,
        "minimum_price": minimum,
        "maximum_price": maximum,
        "sample_count": count,
        "first_event_utc": first_event,
        "last_event_utc": last_event,
        "anchor_event_utc": iso_utc(anchor_at),
        "anchor_age_seconds": max(0.0, (end - anchor_at).total_seconds()),
        "anchor_latest_event_type": str(latest["event_type"]),
        "selected_settlement_term": settlement_term,
        "selected_trade_form": trade_form,
        "selection": (
            "LATEST_HERAT_ANCHOR"
            if candidate_index == 0
            else "LATEST_HERAT_ANCHOR_SETTLEMENT_FALLBACK"
        ),
    }


def select_effective_usd_average(
    connection: sqlite3.Connection,
    settlement: str,
    end: datetime,
    *,
    seconds: int = WINDOW_SECONDS,
) -> dict[str, Any]:
    usd = select_usd_average(connection, settlement, end, seconds=seconds)
    if usd["status"] == "OBSERVED":
        usd["price_source"] = "USD_HERAT"
        usd["is_usdt_proxy"] = False
        usd["is_estimated"] = False
        return usd
    anchor = select_latest_usd_anchor(connection, settlement, end)
    if anchor["status"] != "OBSERVED":
        usd["selection"] = "NO_HERAT_ANCHOR"
        usd["price_source"] = None
        usd["fallback_rejected"] = "DIRECT_USDT_PRICE_SUBSTITUTION_FORBIDDEN"
        usd["is_usdt_proxy"] = False
        usd["is_estimated"] = False
        return usd

    anchor_at = parse_datetime(str(anchor["anchor_event_utc"]))
    anchor_usdt = select_usdt_average(
        connection,
        anchor_at,
        seconds=USDT_ANCHOR_WINDOW_SECONDS,
    )
    current_usdt = select_usdt_average(
        connection,
        end,
        seconds=USDT_ANCHOR_WINDOW_SECONDS,
    )
    if (
        anchor_usdt["status"] != "OBSERVED"
        or current_usdt["status"] != "OBSERVED"
    ):
        usd["selection"] = "HERAT_ANCHOR_WITHOUT_COMPARABLE_USDT"
        usd["price_source"] = None
        usd["fallback_rejected"] = "DIRECT_USDT_PRICE_SUBSTITUTION_FORBIDDEN"
        usd["herat_anchor"] = anchor
        usd["is_usdt_proxy"] = False
        usd["is_estimated"] = False
        return usd

    anchor_usdt_price = float(anchor_usdt["average_price"])
    current_usdt_price = float(current_usdt["average_price"])
    raw_return = current_usdt_price / anchor_usdt_price - 1.0
    if raw_return > USDT_TREND_DEADBAND_RELATIVE:
        trend = "UP"
        applied_return = raw_return
    elif raw_return < -USDT_TREND_DEADBAND_RELATIVE:
        trend = "DOWN"
        applied_return = raw_return
    else:
        trend = "NEUTRAL"
        applied_return = 0.0
    multiplier = 1.0 + applied_return
    estimated = float(anchor["average_price"]) * multiplier
    return {
        "status": "ESTIMATED",
        "llm_value": estimated,
        "average_price": estimated,
        "minimum_price": float(anchor["minimum_price"]) * multiplier,
        "maximum_price": float(anchor["maximum_price"]) * multiplier,
        "sample_count": int(anchor["sample_count"]),
        "first_event_utc": anchor["first_event_utc"],
        "last_event_utc": anchor["last_event_utc"],
        "selection": f"HERAT_ANCHOR_USDT_{trend}_TREND",
        "price_source": "USD_HERAT_ESTIMATED_FROM_USDT_TREND",
        "is_usdt_proxy": False,
        "is_estimated": True,
        "anchor_price": float(anchor["average_price"]),
        "anchor_event_utc": anchor["anchor_event_utc"],
        "anchor_age_seconds": anchor["anchor_age_seconds"],
        "anchor_selected_settlement_term": anchor[
            "selected_settlement_term"
        ],
        "anchor_selected_trade_form": anchor["selected_trade_form"],
        "usdt_anchor_price": anchor_usdt_price,
        "usdt_current_price": current_usdt_price,
        "usdt_raw_return": raw_return,
        "usdt_trend_deadband_relative": USDT_TREND_DEADBAND_RELATIVE,
        "usdt_trend_window_seconds": USDT_ANCHOR_WINDOW_SECONDS,
        "usdt_trend": trend,
        "usdt_applied_return": applied_return,
    }


def select_melted_average(
    connection: sqlite3.Connection, settlement: str, end: datetime
) -> dict[str, Any]:
    config = SETTLEMENT_CONFIG[settlement]
    candidates = tuple(config.get("melted_candidates", ()))
    for label, trade_form, selection in candidates:
        value = average_market_value(
            connection,
            end=end,
            instrument="MELTED_GOLD",
            market_label=str(label),
            trade_form=str(trade_form),
        )
        if value["status"] == "OBSERVED":
            value["selection"] = str(selection)
            value["selected_market_label"] = str(label)
            value["selected_trade_form"] = str(trade_form)
            return value
    value["selection"] = "NO_DATA"
    value["excluded_fallback"] = "IME_CORROBORATION_ONLY_NOT_DIRECT_MELTED_INPUT"
    value["selected_market_label"] = None
    value["selected_trade_form"] = None
    return value


def select_generic_coin_average(
    connection: sqlite3.Connection, settlement: str, end: datetime
) -> dict[str, Any]:
    config = SETTLEMENT_CONFIG[settlement]
    telegram: dict[str, Any] | None = None
    for market_label, settlement_term, trade_form, selection in config.get(
        "coin_candidates", ()
    ):
        telegram = average_market_value(
            connection,
            end=end,
            instrument="GOLD_COIN",
            market_label=str(market_label),
            settlement_term=str(settlement_term),
            trade_form=str(trade_form),
        )
        if telegram["status"] == "OBSERVED":
            telegram["selection"] = (
                f"TELEGRAM_GENERIC_COIN_{selection}"
            )
            telegram["selected_market_label"] = str(market_label)
            telegram["selected_settlement_term"] = str(settlement_term)
            telegram["selected_trade_form"] = str(trade_form)
            return telegram
    if telegram is None:
        telegram = average_market_value(
            connection,
            end=end,
            instrument="GOLD_COIN",
            settlement_term="__NO_CONFIGURED_CANDIDATE__",
        )
    if settlement != "CASH":
        # The IME continuous coin certificate is a current/cash anchor.  It
        # must not directly replace a tomorrow coin quote; tomorrow keeps its
        # independently learned bubble on top of the selected gold underlying.
        telegram["selection"] = "NO_DATA"
        telegram["excluded_fallback"] = (
            "IME_CASH_CERTIFICATE_NOT_VALID_TOMORROW_DIRECT_ANCHOR"
        )
        return telegram
    telegram["selection"] = "NO_DATA"
    telegram["excluded_fallback"] = "IME_CORROBORATION_ONLY_NOT_DIRECT_COIN_INPUT"
    return telegram


def select_group_offer_anchor(
    conversation_db: Path,
    *,
    commodity: str,
    settlement: str,
    trade_form: str,
    end: datetime,
    seconds: int = GROUP_ANCHOR_WINDOW_SECONDS,
    minimum_confidence: float = GROUP_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Build a recent market band; never let the last offer become the price.

    Completed, quality-approved trades have first priority.  Otherwise a
    two-sided book midpoint or a confidence-weighted offer median is used.  The
    hard five-minute TTL is applied even if an older model artifact still asks
    for a ten-minute anchor.
    """

    seconds = min(max(0, int(seconds)), OFFER_LIVE_SECONDS)
    empty = {
        "status": "NO_DATA",
        "llm_value": NO_DATA_TOKEN,
        "reference_price_toman": None,
        "reference_source": None,
        "latest_price_toman": None,
        "latest_side": None,
        "latest_event_utc": None,
        "age_seconds": None,
        "offer_count": 0,
        "trade_count": 0,
        "best_bid_toman": None,
        "best_ask_toman": None,
        "minimum_price_toman": None,
        "maximum_price_toman": None,
        "window_seconds": seconds,
    }
    if seconds <= 0 or not conversation_db.is_file():
        return empty
    connection = sqlite3.connect(
        f"file:{conversation_db.resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"offers", "messages"}.issubset(tables):
            return empty
        start = end - timedelta(seconds=seconds)
        offer_quality_available = "offer_market_quality" in tables
        trade_quality_available = "trade_market_quality" in tables
        quality_join = (
            "LEFT JOIN offer_market_quality AS q ON q.offer_id=o.id"
            if offer_quality_available
            else ""
        )
        quality_filter = (
            "AND COALESCE(q.realtime_eligible, 1)=1"
            if offer_quality_available
            else ""
        )
        quality_fields = (
            "COALESCE(q.live_range_weight, 1.0) AS live_range_weight, "
            "COALESCE(q.cross_state, 'UNKNOWN') AS cross_state"
            if offer_quality_available
            else "1.0 AS live_range_weight, 'QUALITY_TABLE_MISSING' AS cross_state"
        )
        offer_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(offers)")
        }
        quantity_field = "o.quantity" if "quantity" in offer_columns else "NULL"
        rows = connection.execute(
            f"""
            SELECT o.price, o.side, o.confidence, {quantity_field} AS quantity,
                   o.message_id,
                   m.event_time_utc, o.id, {quality_fields}
            FROM offers AS o
            JOIN messages AS m
              ON m.import_id=o.import_id AND m.message_id=o.message_id
            {quality_join}
            WHERE o.commodity=? AND o.settlement=? AND o.trade_form=?
              AND o.confidence >= ?
              AND m.event_time_utc >= ? AND m.event_time_utc < ?
              {quality_filter}
            ORDER BY m.event_time_utc, o.id
            """,
            (
                commodity,
                settlement,
                trade_form,
                minimum_confidence,
                iso_utc(start),
                iso_utc(end),
            ),
        ).fetchall()
        trade_rows: list[sqlite3.Row] = []
        if "confirmed_trades" in tables:
            trade_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(confirmed_trades)"
                )
            }
            base_filter = (
                "AND t.training_eligible=1"
                if "training_eligible" in trade_columns
                else ""
            )
            trade_join = (
                "LEFT JOIN trade_market_quality AS tq ON tq.trade_id=t.id"
                if trade_quality_available
                else ""
            )
            trade_filter = (
                "AND COALESCE(tq.realtime_eligible, 1)=1"
                if trade_quality_available
                else ""
            )
            trade_rows = connection.execute(
                f"""
                SELECT t.id, t.price, t.quantity, t.confidence,
                       t.event_time_utc, t.offer_message_id
                FROM confirmed_trades AS t
                {trade_join}
                WHERE t.commodity=? AND t.settlement=? AND t.trade_form=?
                  AND t.confidence>=0.85
                  AND t.event_time_utc>=? AND t.event_time_utc<?
                  {base_filter}
                  {trade_filter}
                ORDER BY t.event_time_utc, t.id
                """,
                (
                    commodity,
                    settlement,
                    trade_form,
                    iso_utc(start),
                    iso_utc(end),
                ),
            ).fetchall()
    finally:
        connection.close()
    completed_offer_messages = {
        int(row["offer_message_id"])
        for row in trade_rows
        if row["offer_message_id"] is not None
    }
    rows = [
        row for row in rows if int(row["message_id"]) not in completed_offer_messages
    ]
    if not rows and not trade_rows:
        return empty
    latest = rows[-1] if rows else None
    prices = [int(row["price"]) * PRICE_MULTIPLIER for row in rows]
    bids = [
        int(row["price"]) * PRICE_MULTIPLIER
        for row in rows
        if row["side"] == "BUY"
    ]
    asks = [
        int(row["price"]) * PRICE_MULTIPLIER
        for row in rows
        if row["side"] == "SELL"
    ]
    latest_time = (
        parse_datetime(str(latest["event_time_utc"])) if latest is not None else None
    )
    latest_price = (
        int(latest["price"]) * PRICE_MULTIPLIER if latest is not None else None
    )
    best_bid = max(bids) if bids else None
    best_ask = min(asks) if asks else None
    if trade_rows:
        trade_prices = [
            float(row["price"]) * PRICE_MULTIPLIER for row in trade_rows
        ]
        trade_weights = [
            float(row["confidence"])
            * max(1.0, math.sqrt(float(row["quantity"] or 1)))
            for row in trade_rows
        ]
        reference_price = weighted_quantile(trade_prices, trade_weights, 0.50)
        reference_source = "RECENT_CONFIRMED_TRADE_WEIGHTED_MEDIAN"
    elif best_bid is not None and best_ask is not None and best_bid <= best_ask:
        reference_price = (best_bid + best_ask) / 2.0
        reference_source = "ACTIVE_TWO_SIDED_BOOK_MID"
    else:
        offer_weights = [
            float(row["confidence"]) * float(row["live_range_weight"])
            for row in rows
        ]
        reference_price = weighted_quantile(
            [float(value) for value in prices], offer_weights, 0.50
        )
        reference_source = "ACTIVE_OFFER_WEIGHTED_MEDIAN"
    return {
        "status": "OBSERVED",
        "llm_value": int(round(reference_price)),
        "reference_price_toman": reference_price,
        "reference_source": reference_source,
        "latest_price_toman": latest_price,
        "latest_side": str(latest["side"]) if latest is not None else None,
        "latest_event_utc": (
            str(latest["event_time_utc"]) if latest is not None else None
        ),
        "age_seconds": (
            max(0.0, (end - latest_time).total_seconds())
            if latest_time is not None
            else None
        ),
        "offer_count": len(rows),
        "trade_count": len(trade_rows),
        "best_bid_toman": best_bid,
        "best_ask_toman": best_ask,
        "minimum_price_toman": min(prices) if prices else None,
        "maximum_price_toman": max(prices) if prices else None,
        "window_seconds": seconds,
        "minimum_confidence": minimum_confidence,
        "offer_quality_available": offer_quality_available,
        "trade_quality_available": trade_quality_available,
        "selection": "RECENT_TRADE_THEN_ACTIVE_BOOK_BAND",
    }


def summarize_order_flow(
    rows: Sequence[sqlite3.Row | dict[str, Any]], end: datetime
) -> dict[str, Any]:
    """Summarize recent offers/trades without mixing market dimensions."""
    if not rows:
        return {
            "status": "NO_DATA",
            "score": None,
            "direction": "NEUTRAL",
            "event_count": 0,
            "offer_count": 0,
            "trade_count": 0,
            "buy_offer_count": 0,
            "sell_offer_count": 0,
            "offer_share": None,
            "latest_offer_streak_side": "NONE",
            "latest_offer_streak_count": 0,
            "last_event_utc": None,
        }

    ordered = sorted(rows, key=lambda row: str(row["event_time_utc"]))
    buy_weight = 0.0
    sell_weight = 0.0
    trade_weight = 0.0
    buy_count = 0
    sell_count = 0
    trade_count = 0
    for row in ordered:
        age = max(0.0, (end - parse_datetime(str(row["event_time_utc"]))).total_seconds())
        recency_weight = 0.5 ** (age / FLOW_HALF_LIFE_SECONDS)
        quantity = row["quantity_num"] if "quantity_num" in row.keys() else None
        quantity_factor = min(1.75, 1.0 + math.sqrt(float(quantity or 0.0)) / 12.0)
        weight = recency_weight * quantity_factor
        if row["event_type"] == "TRADE":
            trade_weight += weight
            trade_count += 1
        elif row["event_type"] == "OFFER" and row["side"] == "BUY":
            buy_weight += weight
            buy_count += 1
        elif row["event_type"] == "OFFER" and row["side"] == "SELL":
            sell_weight += weight
            sell_count += 1

    streak_side = "NONE"
    streak_count = 0
    for row in reversed(ordered):
        if row["event_type"] != "OFFER" or row["side"] not in {"BUY", "SELL"}:
            break
        side = str(row["side"])
        if streak_side == "NONE":
            streak_side = side
        if side != streak_side:
            break
        streak_count += 1

    offer_weight = buy_weight + sell_weight
    total_weight = offer_weight + trade_weight
    offer_share = offer_weight / total_weight if total_weight else 0.0
    imbalance = (
        (buy_weight - sell_weight) / offer_weight if offer_weight else 0.0
    )
    streak_direction = 1.0 if streak_side == "BUY" else (-1.0 if streak_side == "SELL" else 0.0)
    streak_strength = min(1.0, streak_count / 5.0)
    evidence_factor = min(1.0, (buy_count + sell_count + trade_count) / 8.0)
    score = (
        0.70 * imbalance * offer_share
        + 0.30 * streak_direction * streak_strength * offer_share
    ) * evidence_factor
    score = max(-1.0, min(1.0, score))
    direction = (
        "BUY_PRESSURE"
        if score > 0.05
        else ("SELL_PRESSURE" if score < -0.05 else "NEUTRAL")
    )
    return {
        "status": "OBSERVED",
        "score": score,
        "direction": direction,
        "event_count": len(ordered),
        "offer_count": buy_count + sell_count,
        "trade_count": trade_count,
        "buy_offer_count": buy_count,
        "sell_offer_count": sell_count,
        "offer_share": offer_share,
        "latest_offer_streak_side": streak_side,
        "latest_offer_streak_count": streak_count,
        "last_event_utc": str(ordered[-1]["event_time_utc"]),
    }


def order_flow_stream(
    connection: sqlite3.Connection,
    *,
    end: datetime,
    instrument: str,
    settlement_terms: Sequence[str],
    trade_form: str,
    seconds: int = FLOW_WINDOW_SECONDS,
) -> dict[str, Any]:
    if not settlement_terms:
        return summarize_order_flow([], end)
    start = end - timedelta(seconds=seconds)
    placeholders = ",".join("?" for _ in settlement_terms)
    rows = connection.execute(
        f"""
        SELECT event_type, side, quantity_num, event_time_utc
        FROM price_events
        WHERE event_time_utc > ? AND event_time_utc <= ?
          AND instrument = ?
          AND settlement_term IN ({placeholders})
          AND trade_form = ?
          AND event_type IN ('OFFER', 'TRADE')
        ORDER BY event_time_utc, id
        """,
        [iso_utc(start), iso_utc(end), instrument, *settlement_terms, trade_form],
    ).fetchall()
    return summarize_order_flow(rows, end)


def _weighted_flow_score(
    values: Sequence[tuple[dict[str, Any], float]],
) -> float | None:
    observed = [
        (float(value["score"]), weight)
        for value, weight in values
        if value.get("score") is not None
    ]
    if not observed:
        return None
    total_weight = sum(weight for _, weight in observed)
    return sum(score * weight for score, weight in observed) / total_weight


def preferred_order_flow_stream(
    connection: sqlite3.Connection,
    *,
    end: datetime,
    primary_instrument: str,
    fallback_instrument: str,
    settlement_terms: Sequence[str],
    trade_form: str,
) -> dict[str, Any]:
    """Use an explicit flow feed without counting its price as a second quote."""
    primary = order_flow_stream(
        connection,
        end=end,
        instrument=primary_instrument,
        settlement_terms=settlement_terms,
        trade_form=trade_form,
    )
    if primary["status"] == "OBSERVED":
        primary["source_instrument"] = primary_instrument
        primary["source_selection"] = "EXPLICIT_FLOW_FEED"
        return primary
    fallback = order_flow_stream(
        connection,
        end=end,
        instrument=fallback_instrument,
        settlement_terms=settlement_terms,
        trade_form=trade_form,
    )
    fallback["source_instrument"] = fallback_instrument
    fallback["source_selection"] = "UNDERLYING_FEED_FALLBACK"
    return fallback


def market_order_flow(
    connection: sqlite3.Connection, settlement: str, end: datetime
) -> dict[str, Any]:
    config = SETTLEMENT_CONFIG[settlement]
    target_term = str(config["settlement_term"])
    instrument_weights = {
        "USD_HERAT": 0.50,
        "MELTED_GOLD": 0.30,
        "GOLD_COIN": 0.20,
    }
    by_instrument: dict[str, Any] = {}
    for instrument in instrument_weights:
        if instrument == "MELTED_GOLD":
            by_instrument[instrument] = {
                "physical": preferred_order_flow_stream(
                    connection,
                    end=end,
                    primary_instrument="MELTED_GOLD_FLOW",
                    fallback_instrument="MELTED_GOLD",
                    settlement_terms=(target_term,),
                    trade_form="PHYSICAL",
                ),
                "paper": preferred_order_flow_stream(
                    connection,
                    end=end,
                    primary_instrument="MELTED_GOLD_FLOW",
                    fallback_instrument="MELTED_GOLD",
                    settlement_terms=(target_term,),
                    trade_form="PAPER",
                ),
                "paper_unknown_settlement": preferred_order_flow_stream(
                    connection,
                    end=end,
                    primary_instrument="MELTED_GOLD_FLOW",
                    fallback_instrument="MELTED_GOLD",
                    settlement_terms=("UNKNOWN",),
                    trade_form="PAPER",
                ),
            }
        else:
            by_instrument[instrument] = {
                "physical": order_flow_stream(
                    connection,
                    end=end,
                    instrument=instrument,
                    settlement_terms=(target_term,),
                    trade_form="PHYSICAL",
                ),
                "paper": order_flow_stream(
                    connection,
                    end=end,
                    instrument=instrument,
                    settlement_terms=(target_term,),
                    trade_form="PAPER",
                ),
                "paper_unknown_settlement": order_flow_stream(
                    connection,
                    end=end,
                    instrument=instrument,
                    settlement_terms=("UNKNOWN",),
                    trade_form="PAPER",
                ),
            }

    physical_score = _weighted_flow_score(
        [(by_instrument[name]["physical"], weight) for name, weight in instrument_weights.items()]
    )
    paper_score = _weighted_flow_score(
        [(by_instrument[name]["paper"], weight) for name, weight in instrument_weights.items()]
    )
    unknown_paper_score = _weighted_flow_score(
        [
            (by_instrument[name]["paper_unknown_settlement"], weight)
            for name, weight in instrument_weights.items()
        ]
    )
    available = any(
        score is not None for score in (physical_score, paper_score, unknown_paper_score)
    )
    estimator_score = None
    if available:
        estimator_score = max(
            -1.0,
            min(
                1.0,
                float(physical_score or 0.0)
                + PAPER_FLOW_WEIGHT * float(paper_score or 0.0)
                + UNKNOWN_SETTLEMENT_PAPER_FLOW_WEIGHT
                * float(unknown_paper_score or 0.0),
            ),
        )
    return {
        "status": "OBSERVED" if available else "NO_DATA",
        "window_seconds": FLOW_WINDOW_SECONDS,
        "estimator_score": estimator_score,
        "direction": (
            "BUY_PRESSURE"
            if estimator_score is not None and estimator_score > 0.05
            else (
                "SELL_PRESSURE"
                if estimator_score is not None and estimator_score < -0.05
                else "NEUTRAL"
            )
        ),
        "physical_score": physical_score,
        "paper_score": paper_score,
        "paper_unknown_settlement_score": unknown_paper_score,
        "paper_flow_weight": PAPER_FLOW_WEIGHT,
        "unknown_settlement_paper_flow_weight": UNKNOWN_SETTLEMENT_PAPER_FLOW_WEIGHT,
        "by_instrument": by_instrument,
    }


def observed_inputs(
    connection: sqlite3.Connection, settlement: str, end: datetime
) -> dict[str, dict[str, Any]]:
    config = SETTLEMENT_CONFIG[settlement]
    return {
        "melted_gold": select_melted_average(connection, settlement, end),
        "generic_coin": select_generic_coin_average(connection, settlement, end),
        "xauusd": average_market_value(
            connection,
            end=end,
            instrument="XAUUSD",
            market_label="اونس جهانی",
        ),
        "usd": select_effective_usd_average(connection, settlement, end),
        "usdt": select_usdt_average(connection, end),
        "order_flow": market_order_flow(connection, settlement, end),
        "market_regime": detect_market_regime(connection, end, settlement),
    }


def asymmetric_tolerance(
    *,
    intrinsic: float,
    estimated_price: float,
    adjusted_ratio: float,
    calibration: dict[str, Any],
    pressure_score: float | None,
    market_regime_score: float | None = None,
    market_regime_confidence: float | None = None,
    conformal_floor: float | None = None,
) -> dict[str, Any]:
    median = calibration.get("bubble_ratio_median")
    q10 = calibration.get("bubble_ratio_q10")
    q90 = calibration.get("bubble_ratio_q90")
    median_value = float(median if median is not None else adjusted_ratio)
    lower_distance = max(
        0.003,
        median_value - float(q10) if q10 is not None else 0.01,
    )
    upper_distance = max(
        0.003,
        float(q90) - median_value if q90 is not None else 0.01,
    )
    flow_pressure = float(pressure_score or 0.0)
    regime_pressure = float(market_regime_score or 0.0) * float(
        market_regime_confidence or 0.0
    )
    if market_regime_score is not None:
        pressure = 0.65 * regime_pressure + 0.35 * flow_pressure
    else:
        pressure = flow_pressure
    negative_expansion = 1.0 + FLOW_TOLERANCE_EXPANSION_MAX * max(0.0, -pressure)
    positive_expansion = 1.0 + FLOW_TOLERANCE_EXPANSION_MAX * max(0.0, pressure)
    lower_ratio = max(-0.15, adjusted_ratio - lower_distance * negative_expansion)
    upper_ratio = min(2.0, adjusted_ratio + upper_distance * positive_expansion)
    lower_price = int(round((intrinsic * (1 + lower_ratio)) / 50_000) * 50_000)
    upper_price = int(round((intrinsic * (1 + upper_ratio)) / 50_000) * 50_000)
    point = max(1.0, estimated_price)
    rounded_point = int(round(estimated_price / 50_000) * 50_000)
    bounded_lower = min(lower_price, rounded_point)
    bounded_upper = max(upper_price, rounded_point)
    if conformal_floor is not None and conformal_floor > 0:
        conformal_lower = int(
            round((estimated_price * (1 - conformal_floor)) / 50_000) * 50_000
        )
        conformal_upper = int(
            round((estimated_price * (1 + conformal_floor)) / 50_000) * 50_000
        )
        bounded_lower = min(bounded_lower, conformal_lower)
        bounded_upper = max(bounded_upper, conformal_upper)
    return {
        "lower_price_toman": bounded_lower,
        "upper_price_toman": bounded_upper,
        "lower_project_price": int(round(bounded_lower / PRICE_MULTIPLIER)),
        "upper_project_price": int(round(bounded_upper / PRICE_MULTIPLIER)),
        "negative_tolerance_percent": max(0.0, (point - bounded_lower) / point * 100),
        "positive_tolerance_percent": max(0.0, (bounded_upper - point) / point * 100),
        "negative_expansion_factor": negative_expansion,
        "positive_expansion_factor": positive_expansion,
        "pressure_score": pressure_score,
        "market_regime_score": market_regime_score,
        "market_regime_confidence": market_regime_confidence,
        "combined_directional_score": pressure,
        "conformal_floor_percent": (
            conformal_floor * 100 if conformal_floor is not None else None
        ),
        "bias": (
            "POSITIVE" if pressure > 0.05 else ("NEGATIVE" if pressure < -0.05 else "NEUTRAL")
        ),
    }


def observed_anchor_tolerance(
    anchor: dict[str, Any], *, relative_error_qhat: float
) -> dict[str, Any]:
    point = float(anchor["reference_price_toman"])
    qhat = max(0.001, min(0.05, relative_error_qhat))
    lower = point * (1 - qhat)
    upper = point * (1 + qhat)
    if anchor.get("best_bid_toman") is not None:
        lower = min(lower, float(anchor["best_bid_toman"]))
    if anchor.get("best_ask_toman") is not None:
        upper = max(upper, float(anchor["best_ask_toman"]))
    rounded_lower = int(round(lower / 50_000) * 50_000)
    rounded_upper = int(round(upper / 50_000) * 50_000)
    return {
        "lower_price_toman": rounded_lower,
        "upper_price_toman": rounded_upper,
        "lower_project_price": int(round(rounded_lower / PRICE_MULTIPLIER)),
        "upper_project_price": int(round(rounded_upper / PRICE_MULTIPLIER)),
        "negative_tolerance_percent": max(0.0, (point - rounded_lower) / point * 100),
        "positive_tolerance_percent": max(0.0, (rounded_upper - point) / point * 100),
        "negative_expansion_factor": 1.0,
        "positive_expansion_factor": 1.0,
        "pressure_score": None,
        "conformal_floor_percent": qhat * 100,
        "bias": "OBSERVED_MARKET_BAND",
    }


def estimate_rates(
    model: dict[str, Any],
    market_db: Path,
    end: datetime,
    conversation_db: Path | None = None,
) -> dict[str, Any]:
    end = end.astimezone(timezone.utc).replace(microsecond=0)
    start = end - timedelta(seconds=WINDOW_SECONDS)
    result: dict[str, Any] = {
        "schema_version": 1,
        "model_kind": model["model_kind"],
        "is_llm": False,
        # The source cutoff, not wall-clock execution time, controls freshness
        # and makes the same immutable input reproducible.
        "generated_at_utc": iso_utc(end),
        "window_start_utc": iso_utc(start),
        "window_end_utc": iso_utc(end),
        "missing_token": NO_DATA_TOKEN,
        "price_unit": "TOMAN",
        "project_price_multiplier_to_toman": PRICE_MULTIPLIER,
        "order_flow_point_adjustment_enabled": bool(
            model.get("order_flow_point_adjustment_enabled", False)
        ),
        "group_offer_anchor_policy": model.get("group_offer_anchor")
        or {"enabled": False},
        "settlements": {},
    }
    supported = [row for row in model["commodities"] if row["status"] == "SUPPORTED"]
    flow_point_enabled = bool(model.get("order_flow_point_adjustment_enabled", False))
    anchor_policy = model.get("group_offer_anchor") or {}
    anchor_enabled = bool(anchor_policy.get("enabled", False))
    anchor_database = conversation_db
    anchor_window = min(
        OFFER_LIVE_SECONDS,
        int(anchor_policy.get("window_seconds", GROUP_ANCHOR_WINDOW_SECONDS)),
    )
    anchor_minimum_confidence = float(
        anchor_policy.get("minimum_confidence", GROUP_MIN_CONFIDENCE)
    )
    anchor_relative_error_qhat = float(
        anchor_policy.get("relative_error_qhat", 0.006)
    )
    with closing(connect_market_db(market_db)) as connection:
        for settlement in SETTLEMENT_CONFIG:
            inputs = observed_inputs(connection, settlement, end)
            melted_value = inputs["melted_gold"]["average_price"]
            generic_coin_value = inputs["generic_coin"]["average_price"]
            usd_value = inputs["usd"]["average_price"]
            xau_value = inputs["xauusd"]["average_price"]
            pressure_score = inputs["order_flow"]["estimator_score"]
            market_regime = inputs["market_regime"]
            market_regime_score = market_regime.get("direction_score")
            market_regime_confidence = market_regime.get("confidence")

            theoretical_melted = None
            melted_bubble = None
            melted_vs_global_ratio = None
            if usd_value is not None and xau_value is not None:
                theoretical_melted = float(usd_value) * float(xau_value) / 9.572737
                if melted_value is not None:
                    melted_bubble = float(melted_value) - theoretical_melted
                    melted_vs_global_ratio = float(melted_value) / theoretical_melted - 1

            current_imam_bubble_ratio = None
            trained_imam_bubble_ratio = None
            if melted_value is not None and generic_coin_value is not None:
                imam_intrinsic = float(melted_value) * COMMODITY_SPECS["امام"].coefficient
                current_imam_bubble_ratio = float(generic_coin_value) / imam_intrinsic - 1
            imam_model = next((row for row in supported if row["name"] == "امام"), None)
            if imam_model:
                imam_calibration = imam_model["settlements"][settlement]
                trained_imam_bubble_ratio = imam_calibration["bubble_ratio_median"]
                imam_center = imam_calibration.get("melted_vs_global_center")
                if (
                    flow_point_enabled
                    and
                    trained_imam_bubble_ratio is not None
                    and melted_vs_global_ratio is not None
                    and imam_center is not None
                ):
                    trained_imam_bubble_ratio = float(
                        trained_imam_bubble_ratio
                    ) + float(imam_calibration.get("melted_vs_global_slope") or 0.0) * (
                        melted_vs_global_ratio - float(imam_center)
                    )
                imam_pressure_center = imam_calibration.get("market_pressure_center")
                if (
                    trained_imam_bubble_ratio is not None
                    and pressure_score is not None
                    and imam_pressure_center is not None
                ):
                    trained_imam_bubble_ratio = float(
                        trained_imam_bubble_ratio
                    ) + float(imam_calibration.get("market_pressure_slope") or 0.0) * (
                        float(pressure_score) - float(imam_pressure_center)
                    )
            regime_shift = 0.0
            if current_imam_bubble_ratio is not None and trained_imam_bubble_ratio is not None:
                regime_shift = current_imam_bubble_ratio - float(trained_imam_bubble_ratio)

            rates: list[dict[str, Any]] = []
            for commodity in supported:
                calibration_row = commodity["settlements"][settlement]
                group_anchor = (
                    select_group_offer_anchor(
                        anchor_database,
                        commodity=str(commodity["name"]),
                        settlement=settlement,
                        trade_form="PHYSICAL",
                        end=end,
                        seconds=anchor_window,
                        minimum_confidence=anchor_minimum_confidence,
                    )
                    if anchor_enabled and anchor_database is not None
                    else {
                        "status": "DISABLED",
                        "llm_value": NO_DATA_TOKEN,
                    }
                )
                anchor_used = group_anchor.get("status") == "OBSERVED"
                if melted_value is None:
                    if anchor_used:
                        estimate = float(group_anchor["reference_price_toman"])
                        rounded_toman = int(round(estimate / 50_000) * 50_000)
                        rates.append(
                            {
                                "commodity_name": commodity["name"],
                                "status": "OBSERVED_ANCHOR",
                                "llm_value": int(round(rounded_toman / PRICE_MULTIPLIER)),
                                "intrinsic_toman": None,
                                "bubble_ratio": None,
                                "market_pressure_score": pressure_score,
                                "market_pressure_adjustment_ratio": 0.0,
                                "estimated_price_toman": rounded_toman,
                                "estimated_project_price": int(
                                    round(rounded_toman / PRICE_MULTIPLIER)
                                ),
                                "tolerance": observed_anchor_tolerance(
                                    group_anchor,
                                    relative_error_qhat=anchor_relative_error_qhat,
                                ),
                                "confidence": (
                                    "HIGH_OBSERVED_TRADE"
                                    if group_anchor.get("trade_count")
                                    else "MEDIUM_OBSERVED_BOOK"
                                ),
                                "training_sample_count": calibration_row["sample_count"],
                                "direct_settlement_sample_count": calibration_row[
                                    "direct_settlement_sample_count"
                                ],
                                "method": "RECENT_TRADE_THEN_ACTIVE_BOOK_BAND_5M",
                                "group_offer_anchor": group_anchor,
                                "structural_fallback_status": (
                                    "MELTED_GOLD_NOT_OBSERVED_IN_WINDOW"
                                ),
                            }
                        )
                        continue
                    rates.append(
                        {
                            "commodity_name": commodity["name"],
                            "status": "NO_DATA",
                            "llm_value": NO_DATA_TOKEN,
                            "estimated_price_toman": None,
                            "estimated_project_price": None,
                            "reason": "MELTED_GOLD_NOT_OBSERVED_IN_WINDOW",
                            "confidence": "NONE",
                        }
                    )
                    continue
                intrinsic = float(melted_value) * float(commodity["coefficient"])
                bubble_ratio = calibration_row["bubble_ratio_median"]
                if bubble_ratio is None:
                    rates.append(
                        {
                            "commodity_name": commodity["name"],
                            "status": "NO_MODEL",
                            "llm_value": NO_DATA_TOKEN,
                            "intrinsic_toman": intrinsic,
                            "estimated_price_toman": None,
                            "estimated_project_price": None,
                            "reason": "NO_ACCEPTED_TRAINING_LABEL",
                            "confidence": "NONE",
                        }
                    )
                    continue

                method = str(calibration_row["source"])
                adjusted_ratio = float(bubble_ratio)
                feature_center = calibration_row.get("melted_vs_global_center")
                if melted_vs_global_ratio is not None and feature_center is not None:
                    adjusted_ratio += float(
                        calibration_row.get("melted_vs_global_slope") or 0.0
                    ) * (melted_vs_global_ratio - float(feature_center))
                    method += "+USD_XAU_MELTED_PREMIUM_FEATURE"
                pressure_adjustment = 0.0
                pressure_center = calibration_row.get("market_pressure_center")
                if pressure_score is not None and pressure_center is not None:
                    if flow_point_enabled:
                        pressure_adjustment = float(
                            calibration_row.get("market_pressure_slope") or 0.0
                        ) * (float(pressure_score) - float(pressure_center))
                        adjusted_ratio += pressure_adjustment
                        method += "+LEARNED_ORDER_FLOW_FEATURE"
                    else:
                        method += "+ORDER_FLOW_POINT_GATED_OFF"
                # Generic channel coin is treated as Imam only, an explicit and
                # visible assumption. Other current bubbles receive the same
                # absolute ratio regime shift unless they are low-date coins.
                if commodity["name"] == "امام" and generic_coin_value is not None:
                    estimate = float(generic_coin_value)
                    adjusted_ratio = estimate / intrinsic - 1
                    pressure_adjustment = 0.0
                    method = "DIRECT_GENERIC_COIN_ONE_MINUTE_ASSUMED_IMAM"
                else:
                    if not commodity["low_date"]:
                        adjusted_ratio += regime_shift
                    adjusted_ratio = max(-0.15, min(2.0, adjusted_ratio))
                    estimate = intrinsic * (1 + adjusted_ratio)

                if anchor_used:
                    estimate = float(group_anchor["reference_price_toman"])
                    adjusted_ratio = estimate / intrinsic - 1
                    pressure_adjustment = 0.0
                    method = "RECENT_TRADE_THEN_ACTIVE_BOOK_BAND_5M"

                rounded_toman = int(round(estimate / 50_000) * 50_000)
                project_price = int(round(rounded_toman / PRICE_MULTIPLIER))
                tolerance = (
                    observed_anchor_tolerance(
                        group_anchor,
                        relative_error_qhat=anchor_relative_error_qhat,
                    )
                    if anchor_used
                    else asymmetric_tolerance(
                        intrinsic=intrinsic,
                        estimated_price=estimate,
                        adjusted_ratio=adjusted_ratio,
                        calibration=calibration_row,
                        pressure_score=pressure_score,
                        market_regime_score=market_regime_score,
                        market_regime_confidence=market_regime_confidence,
                        conformal_floor=(
                            (
                                model.get("conformal_tolerance", {})
                                .get("per_commodity_relative_error_qhat", {})
                                .get(commodity["name"])
                            )
                            or model.get("conformal_tolerance", {}).get(
                                "global_relative_error_qhat"
                            )
                        ),
                    )
                )
                if pressure_score is not None or market_regime_score is not None:
                    method += "+ASYMMETRIC_REGIME_AND_ORDER_FLOW_TOLERANCE"
                rates.append(
                    {
                        "commodity_name": commodity["name"],
                        "status": "ESTIMATED",
                        "llm_value": project_price,
                        "intrinsic_toman": intrinsic,
                        "bubble_ratio": adjusted_ratio,
                        "market_pressure_score": pressure_score,
                        "market_regime": market_regime.get("regime"),
                        "market_regime_score": market_regime_score,
                        "market_regime_confidence": market_regime_confidence,
                        "market_pressure_adjustment_ratio": pressure_adjustment,
                        "estimated_price_toman": rounded_toman,
                        "estimated_project_price": project_price,
                        "tolerance": tolerance,
                        "confidence": (
                            (
                                "HIGH_OBSERVED_TRADE"
                                if group_anchor.get("trade_count")
                                else "MEDIUM_OBSERVED_BOOK"
                            )
                            if anchor_used
                            else calibration_row["confidence"]
                        ),
                        "training_sample_count": calibration_row["sample_count"],
                        "direct_settlement_sample_count": calibration_row[
                            "direct_settlement_sample_count"
                        ],
                        "method": method,
                        "group_offer_anchor": group_anchor,
                    }
                )

            result["settlements"][settlement] = {
                "inputs": inputs,
                "theoretical_melted_toman": theoretical_melted,
                "melted_bubble_toman": melted_bubble,
                "melted_vs_global_ratio": melted_vs_global_ratio,
                "current_imam_bubble_ratio": current_imam_bubble_ratio,
                "bubble_regime_shift": regime_shift,
                "market_pressure": inputs["order_flow"],
                "market_regime": market_regime,
                "rates": rates,
            }
    return result


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _apply_runtime_overlays(
    *,
    bundle: LoadedRuntimeBundle,
    snapshot: Mapping[str, Any],
    market_db: Path,
) -> dict[str, Any]:
    """Apply the bundle's strictly-prior runtime policies in fixed order."""
    low_date = bundle.ranker_policy_payload.get(
        "low_date_intrinsic_policy"
    )
    if not isinstance(low_date, Mapping):
        raise SnapshotProducerError(
            "bundle_low_date_intrinsic_policy_missing"
        )
    generated_at = snapshot.get("generated_at_utc")
    if not isinstance(generated_at, str) or not generated_at:
        raise SnapshotProducerError("snapshot_generated_at_missing")

    bridge = low_date.get("paper_delta_bridge")
    if not isinstance(bridge, Mapping):
        bridge = {}
    multi_signal = low_date.get("multi_signal_range")
    if not isinstance(multi_signal, Mapping):
        multi_signal = {}
    reference = read_physical_melted_reference(
        market_db,
        as_of=generated_at,
        maximum_age_seconds=int(
            low_date.get("maximum_reference_age_seconds", 900)
        ),
        averaging_window_seconds=int(
            low_date.get("robust_average_window_seconds", 300)
        ),
        enable_paper_delta_bridge=bool(bridge.get("enabled", False)),
        maximum_bridge_age_seconds=int(
            bridge.get("maximum_physical_base_age_seconds", 345_600)
        ),
        paper_bridge_window_seconds=int(
            bridge.get("window_seconds_per_endpoint", 300)
        ),
        maximum_current_paper_age_seconds=int(
            bridge.get("maximum_current_paper_age_seconds", 120)
        ),
        minimum_paper_samples_per_endpoint=int(
            bridge.get("minimum_samples_per_endpoint", 3)
        ),
        maximum_bridge_delta_relative=float(
            bridge.get(
                "maximum_delta_relative_to_physical_base",
                0.25,
            )
        ),
    )
    enriched = apply_low_date_intrinsic_overlay(
        snapshot,
        reference,
        fallback_relative_tolerance=float(
            low_date.get("fallback_relative_tolerance", 0.006)
        ),
        maximum_regime_extra=float(
            multi_signal.get("maximum_regime_extra", 0.012)
        ),
        maximum_signal_disagreement_extra=float(
            multi_signal.get(
                "maximum_signal_disagreement_extra",
                0.006,
            )
        ),
        maximum_directional_skew=float(
            multi_signal.get("maximum_directional_skew", 0.006)
        ),
        maximum_total_tolerance=float(
            multi_signal.get("maximum_total_tolerance", 0.04)
        ),
    )

    try:
        runtime_anchors = [
            enriched_event_from_mapping(row)
            for row in (
                bundle.anchor_transfer_policy.get("runtime_anchors") or []
            )
            if isinstance(row, Mapping)
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotProducerError(
            "bundle_anchor_policy_runtime_anchor_invalid"
        ) from exc
    if not runtime_anchors:
        raise SnapshotProducerError("bundle_anchor_policy_runtime_anchor_empty")
    return apply_coin_anchor_transfer_overlay(
        enriched,
        market_db=market_db,
        enriched_events=runtime_anchors,
        calibration=bundle.anchor_transfer_policy,
    )


class CoinSnapshotProducer:
    """Build one immutable Shadow range snapshot from normalized observations."""

    def __init__(self, bundle: LoadedRuntimeBundle) -> None:
        self.bundle = bundle

    def build(
        self,
        *,
        market_db: Path,
        as_of: datetime,
        conversation_db: Path | None = None,
    ) -> dict[str, Any]:
        validate_market_database(market_db)
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise SnapshotProducerError("snapshot_as_of_timezone_required")
        cutoff = as_of.astimezone(timezone.utc).replace(microsecond=0)
        snapshot = estimate_rates(
            dict(self.bundle.range_model),
            market_db,
            cutoff,
            conversation_db,
        )
        snapshot = _apply_runtime_overlays(
            bundle=self.bundle,
            snapshot=snapshot,
            market_db=market_db,
        )
        calibration = self.bundle.interval_policy.get("calibration")
        if not isinstance(calibration, Mapping):
            raise SnapshotProducerError("bundle_interval_calibration_missing")
        try:
            snapshot["interval_contract"] = {
                "status": "SHADOW_OPERATIONAL_BAND_ONLY",
                "operational_target_coverage": float(
                    calibration["operational_target_coverage"]
                ),
                "audit_target_coverage": float(
                    calibration["audit_target_coverage"]
                ),
                "audit_envelope_available": bool(
                    calibration.get("audit_envelope_available", False)
                ),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotProducerError(
                "bundle_interval_calibration_invalid"
            ) from exc
        allowed = set(self.bundle.canonical_commodity_names)
        observed_names: set[str] = set()
        for settlement in ("CASH", "TOMORROW"):
            payload = snapshot.get("settlements", {}).get(settlement)
            if not isinstance(payload, dict):
                raise SnapshotProducerError(
                    f"snapshot_settlement_missing:{settlement}"
                )
            rates = payload.get("rates")
            if not isinstance(rates, list):
                raise SnapshotProducerError(
                    f"snapshot_rates_invalid:{settlement}"
                )
            for rate in rates:
                if not isinstance(rate, dict):
                    raise SnapshotProducerError(
                        f"snapshot_rate_invalid:{settlement}"
                    )
                rate.pop("commodity_id", None)
                name = str(rate.get("commodity_name") or "")
                if name not in allowed:
                    raise SnapshotProducerError(
                        f"snapshot_commodity_not_canonical:{name}"
                    )
                observed_names.add(name)
        if observed_names != allowed:
            missing = ",".join(sorted(allowed - observed_names))
            raise SnapshotProducerError(
                f"snapshot_commodity_coverage_incomplete:{missing}"
            )

        snapshot["producer_version"] = PRODUCER_VERSION
        snapshot["bundle_version"] = self.bundle.bundle_version
        snapshot["model_sha256"] = self.bundle.model_sha256
        snapshot["feature_schema_version"] = (
            self.bundle.feature_schema_version
        )
        snapshot["input_mode"] = "OFFLINE_NORMALIZED_SQLITE"
        digest = hashlib.sha256(
            _canonical_json_bytes(snapshot)
        ).hexdigest()
        snapshot["snapshot_version"] = (
            f"{self.bundle.bundle_version}:{iso_utc(cutoff)}:"
            f"{digest[:16]}"
        )
        return snapshot

    def build_latest_complete_minute(
        self,
        *,
        market_db: Path,
        conversation_db: Path | None = None,
    ) -> dict[str, Any]:
        validate_market_database(market_db)
        with closing(connect_market_db(market_db)) as connection:
            cutoff = latest_complete_minute(connection)
        return self.build(
            market_db=market_db,
            as_of=cutoff,
            conversation_db=conversation_db,
        )


def write_snapshot_atomic(
    path: Path,
    payload: Mapping[str, Any],
    *,
    mode: int = 0o640,
) -> None:
    """Durably replace one snapshot without exposing a partial JSON file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise SnapshotProducerError("snapshot_destination_symlink_forbidden")
    serialized = _canonical_json_bytes(payload)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def latest_complete_minute(connection: sqlite3.Connection) -> datetime:
    row = connection.execute("SELECT MAX(event_time_utc) FROM price_events").fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("No market events exist")
    latest = parse_datetime(str(row[0]))
    return latest.replace(second=0, microsecond=0) + timedelta(minutes=1)
