#!/usr/bin/env python3
"""Train and run a robust, explainable Iranian coin-rate estimator.

This is deliberately a numerical hybrid model, not an LLM.  It combines the
domain intrinsic-value formula with robust bubble calibration from Telegram
group offers and high-confidence, reply-linked confirmed trades.  Project
labels are disabled by default while project activity is experimental.  Every
inference uses only actually observed events in the requested trailing window;
missing inputs are never forward-filled.
"""

from __future__ import annotations

import argparse
import bisect
from copy import deepcopy
import json
import math
import os
import random
import sqlite3
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


APP_ROOT = Path(__file__).resolve().parent
REPO_ROOT = APP_ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.conversation_quality import (  # noqa: E402
    CONFIRMED_TRADE_TRAINING_WEIGHT,
    OFFER_LIVE_SECONDS,
    OFFER_TIMEOUT_TRAINING_WEIGHT,
    PROJECT_COMPLETED_TRADE_TRAINING_WEIGHT,
    detect_market_regime,
)
from morning_reopen import (  # noqa: E402
    METHOD_NAME as MORNING_REOPEN_METHOD,
    build_morning_reopen_anchor,
    is_morning_reopen_window,
    resolve_cash_tomorrow_ratio_for_estimate,
    select_reopen_cash_tomorrow_ratio,
    widen_tolerance,
)
from online_recalibration import (  # noqa: E402
    LEDGER_OUTCOME_RETENTION_DAYS,
    LEDGER_UNMATCHED_RETENTION_DAYS,
    ensure_schema as ensure_online_schema,
    prune_prediction_ledger,
)


RUNTIME_ROOT = Path(
    os.environ.get("COIN_RATE_ESTIMATOR_RUNTIME_DIR", APP_ROOT / "runtime")
).expanduser()
DEFAULT_REPO = REPO_ROOT
DEFAULT_MARKET_DB = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_MARKET_DB",
        os.environ.get("COIN_MARKET_DB", RUNTIME_ROOT / "market_prices.sqlite3"),
    )
).expanduser()
DEFAULT_MODEL = Path(
    os.environ.get("COIN_RATE_ESTIMATOR_MODEL", RUNTIME_ROOT / "model.json")
).expanduser()
DEFAULT_TRAINING_DB = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_TRAINING_DB",
        RUNTIME_ROOT / "combined_training.sqlite3",
    )
).expanduser()
DEFAULT_GROUP_OFFERS = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_LEGACY_GROUP_OFFERS",
        RUNTIME_ROOT / "legacy_group_offers.json",
    )
).expanduser()
DEFAULT_CONVERSATION_DB = Path(
    os.environ.get("COIN_CONVERSATION_DB", RUNTIME_ROOT / "conversation_events.sqlite3")
).expanduser()
DEFAULT_CALIBRATION_DB = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_CALIBRATION_DB",
        RUNTIME_ROOT / "online_calibration.sqlite3",
    )
).expanduser()
DEFAULT_REVIEW_DECISIONS_DB = Path(
    os.environ.get(
        "COIN_REVIEW_DECISIONS_DB",
        RUNTIME_ROOT / "review_decisions.sqlite3",
    )
).expanduser()
PRICE_MULTIPLIER = 1_000  # project convention: 178000 means 178,000,000 toman

# A live refresh evaluates the main book and challenger books against the same
# timestamp.  The cash/tomorrow empirical ratio is model-independent, yet was
# previously re-queried from SQLite for every challenger.  This cache is keyed
# by the exact snapshot timestamp, so it cannot carry a value into a later
# refresh or change the mathematical result of an individual model.
_EMPIRICAL_RATIO_SNAPSHOT_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_EMPIRICAL_RATIO_SNAPSHOT_CACHE_MAX = 128
# The three structural books (main + two non-ML shadows) observe the exact
# same market/conversation snapshot.  These outputs do not depend on model
# coefficients, so sharing them is mathematically neutral and removes repeated
# SQLite connections/scans from every 30-second shadow cycle.  Keys always
# include the exact timestamp and input gate, and callers receive a deep copy.
_MODEL_INDEPENDENT_SNAPSHOT_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_MODEL_INDEPENDENT_SNAPSHOT_CACHE_MAX = 512


def _empirical_ratio_cache_get(key: tuple[Any, ...]) -> dict[str, Any] | None:
    value = _EMPIRICAL_RATIO_SNAPSHOT_CACHE.get(key)
    return deepcopy(value) if value is not None else None


def _empirical_ratio_cache_put(key: tuple[Any, ...], value: dict[str, Any]) -> dict[str, Any]:
    if len(_EMPIRICAL_RATIO_SNAPSHOT_CACHE) >= _EMPIRICAL_RATIO_SNAPSHOT_CACHE_MAX:
        _EMPIRICAL_RATIO_SNAPSHOT_CACHE.clear()
    _EMPIRICAL_RATIO_SNAPSHOT_CACHE[key] = deepcopy(value)
    return value


def _snapshot_cache_get(key: tuple[Any, ...]) -> dict[str, Any] | None:
    value = _MODEL_INDEPENDENT_SNAPSHOT_CACHE.get(key)
    return deepcopy(value) if value is not None else None


def _snapshot_cache_put(key: tuple[Any, ...], value: dict[str, Any]) -> dict[str, Any]:
    if len(_MODEL_INDEPENDENT_SNAPSHOT_CACHE) >= _MODEL_INDEPENDENT_SNAPSHOT_CACHE_MAX:
        _MODEL_INDEPENDENT_SNAPSHOT_CACHE.clear()
    _MODEL_INDEPENDENT_SNAPSHOT_CACHE[key] = deepcopy(value)
    return value


def _snapshot_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _market_connection_identity(connection: sqlite3.Connection) -> str | None:
    """Return a stable file identity; do not cache in-memory test databases."""

    try:
        rows = connection.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        # sqlite Row supports the named form; tuple mode is useful in tiny
        # standalone tests that do not set a row factory.
        name = str(row[1] if not isinstance(row, sqlite3.Row) else row[1])
        file_name = str(row[2] if not isinstance(row, sqlite3.Row) else row[2])
        if name == "main" and file_name:
            return str(Path(file_name).resolve())
    return None
WINDOW_SECONDS = 60
# A one-minute estimate keeps its existing cadence, but the market inputs
# inside each estimate use a shorter robust window.  The latest real event is
# carried separately as ``point_price``; no synthetic forward-filled event is
# created when a source is quiet.
#
# 30s is too tight for live melted-paper channels that often gap 15–60s between
# quotes; 90s still rejects multi-minute staleness without inventing quiet rows.
MARKET_AVERAGE_SECONDS = 90
MELTED_LIVE_BUCKET_SECONDS = 5
NO_DATA_TOKEN = "<NO_DATA_THIS_MINUTE>"
USD_HERAT_ANCHOR_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
USDT_ANCHOR_WINDOW_SECONDS = 180
USDT_TREND_DEADBAND_RELATIVE = 0.001
CASH_UP_MOVE_BETA = 0.90
CASH_DOWN_MOVE_BETA = 1.05
CASH_CLOSED_WIDENING_TOMAN_PER_HOUR = 150.0
CASH_CLOSED_WIDENING_MAX_TOMAN = 1_500.0
TEHRAN_TIMEZONE = timezone(timedelta(hours=3, minutes=30))
BANKING_START_MINUTE = 8 * 60
BANKING_CLOSE_MINUTE = 17 * 60
THURSDAY_BANKING_CLOSE_MINUTE = 12 * 60
GROUP_MIN_CONFIDENCE = 0.80
GROUP_SOURCE_WEIGHT = OFFER_TIMEOUT_TRAINING_WEIGHT
MAX_OFFER_TRAINING_SHARE_WITH_TRADES = 0.40
FLOW_WINDOW_SECONDS = 600
FLOW_HALF_LIFE_SECONDS = 180
PAPER_FLOW_WEIGHT = 0.50
UNKNOWN_SETTLEMENT_PAPER_FLOW_WEIGHT = 0.25
CONFIRMED_TRADE_FLOW_WEIGHT = 3.0
FLOW_TOLERANCE_EXPANSION_MAX = 0.75
GROUP_ANCHOR_WINDOW_SECONDS = OFFER_LIVE_SECONDS
HISTORICAL_GROUP_MAXIMUM_RELATIVE_DEVIATION = 0.05
MARKET_FORM_POLICY_VERSION = "EXPLICIT_CASH_MARKET_FORMS_V3"
ACCOUNT1_PHYSICAL_TODAY_LABEL = "آبشده کانال جدید نقد حاضر"
ACCOUNT1_PHYSICAL_TOMORROW_LABEL = "آبشده کانال جدید فیزیکی فردا"
ACCOUNT1_PAPER_NORMAL_LABEL = "آبشده کانال جدید کاغذی عادی"
LATEST_INDIVIDUAL_PHYSICAL_LABELS = frozenset(
    {ACCOUNT1_PHYSICAL_TODAY_LABEL, ACCOUNT1_PHYSICAL_TOMORROW_LABEL}
)
CALIBRATION_RECENCY_HALF_LIFE_DAYS = 1.0
TRUSTED_TRAINING_SOURCE_KINDS = {
    "TELEGRAM_GROUP_CONFIRMED_TRADE",
    "TELEGRAM_GROUP_HUMAN_REVIEWED_TRADE",
    "OPERATOR_MANUAL_CONFIRMED_TRADE",
}


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

STATIC_COMMODITIES = [
    {"id": index, "name": name}
    for index, name in enumerate(COMMODITY_SPECS, 1)
]

SETTLEMENT_CONFIG = {
    "CASH": {
        "settlement_term": "TODAY",
        "trade_form": "PHYSICAL",
        "melted_market_label": "آبشده نقدی",
        "melted_candidates": (
            (
                ACCOUNT1_PHYSICAL_TODAY_LABEL,
                "PHYSICAL",
                "ACCOUNT1_LATEST_INDIVIDUAL_PHYSICAL",
            ),
            ("آبشده نقدی", "PHYSICAL", "PRIMARY"),
            (
                "آبشده رسمی",
                "PHYSICAL",
                "SAME_MINUTE_PHYSICAL_UNDERLYING_FALLBACK",
            ),
            # After the cash physical channel goes quiet, keep an explicit paper
            # reference (never silently relabelled as physical) so CASH does not
            # collapse to NO_DATA while TOMORROW still has حواله/غیررسمی quotes.
            (
                "آبشده حواله",
                "PAPER",
                "SAME_MINUTE_PAPER_REFERENCE_FALLBACK",
            ),
            (
                "آبشده غیررسمی",
                "PAPER",
                "SAME_MINUTE_PAPER_REFERENCE_FALLBACK",
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
                ACCOUNT1_PAPER_NORMAL_LABEL,
                "PAPER",
                "ACCOUNT1_WEIGHTED_PAPER_MINUTE",
            ),
            (
                ACCOUNT1_PHYSICAL_TOMORROW_LABEL,
                "PHYSICAL",
                "ACCOUNT1_LATEST_INDIVIDUAL_PHYSICAL_FALLBACK",
            ),
            (
                "آبشده فردایی",
                "PAPER",
                "SAME_MINUTE_PAPER_REFERENCE_FALLBACK",
            ),
            (
                "آبشده امروزی",
                "PAPER",
                "SAME_MINUTE_PAPER_REFERENCE_FALLBACK",
            ),
            (
                "آبشده نقدی",
                "PHYSICAL",
                "SAME_MINUTE_PHYSICAL_UNDERLYING_FALLBACK",
            ),
            (
                "آبشده رسمی",
                "PHYSICAL",
                "SAME_MINUTE_PHYSICAL_UNDERLYING_FALLBACK",
            ),
            (
                "آبشده حواله",
                "PAPER",
                "SAME_MINUTE_PAPER_REFERENCE_FALLBACK",
            ),
            (
                "آبشده غیررسمی",
                "PAPER",
                "SAME_MINUTE_PAPER_REFERENCE_FALLBACK",
            ),
        ),
        # Coin form and settlement are independent. A paper/havale coin quote
        # is not a direct replacement for a physical tomorrow project offer.
        "coin_market_label": "سکه نقدی",
        "coin_candidates": (
            (
                "سکه نقدی",
                "TOMORROW",
                "PHYSICAL",
                "EXACT_TOMORROW_PHYSICAL",
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


def quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


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


def parse_dotenv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key.strip()] = value
    return result


def fetch_project_snapshot(repo: Path) -> dict[str, Any]:
    """Read offer lifecycles and completed trades through local Postgres.

    The project source remains opt-in.  Crossed/competitive-price exclusions
    are carried into the snapshot so a linked completed trade cannot bypass the
    same market-quality rule through a second table.
    """
    env = parse_dotenv(repo / ".env")
    user = env.get("POSTGRES_USER") or "postgres"
    database = env.get("POSTGRES_DB") or "postgres"
    query = """
    SELECT json_build_object(
      'commodities', COALESCE((
        SELECT json_agg(json_build_object('id', c.id, 'name', c.name) ORDER BY c.id)
        FROM commodities c
      ), '[]'::json),
      'trades', COALESCE((
        SELECT json_agg(json_build_object(
          'id', t.id,
          'offer_id', t.offer_id,
          'commodity_id', t.commodity_id,
          'commodity_name', c.name,
          'settlement_type', t.settlement_type::text,
          'price', t.price,
          'quantity', t.quantity,
          'created_at', COALESCE(t.completed_at, t.created_at),
          'offer_excluded_from_competitive_price',
              COALESCE(o.exclude_from_competitive_price, false),
          'offer_price_warning_type', o.price_warning_type
        ) ORDER BY t.created_at, t.id)
        FROM trades t
        JOIN commodities c ON c.id = t.commodity_id
        LEFT JOIN offers o ON o.id = t.offer_id
        WHERE t.status::text = 'COMPLETED'
      ), '[]'::json),
      'offers', COALESCE((
        SELECT json_agg(json_build_object(
          'id', o.id,
          'commodity_id', o.commodity_id,
          'commodity_name', c.name,
          'settlement_type', o.settlement_type::text,
          'offer_type', o.offer_type::text,
          'status', o.status::text,
          'price', o.price,
          'quantity', o.quantity,
          'remaining_quantity', o.remaining_quantity,
          'created_at', o.created_at,
          'expired_at', o.expired_at,
          'expire_reason', o.expire_reason,
          'exclude_from_competitive_price', o.exclude_from_competitive_price,
          'price_warning_type', o.price_warning_type
        ) ORDER BY o.created_at, o.id)
        FROM offers o
        JOIN commodities c ON c.id = o.commodity_id
      ), '[]'::json)
    );
    """
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        user,
        "-d",
        database,
        "-tA",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        query,
    ]
    completed = subprocess.run(
        command,
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    payload = completed.stdout.strip()
    if not payload:
        raise RuntimeError("Project database returned an empty training snapshot")
    return json.loads(payload)


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
            "point_price": None,
            "latest_price": None,
            "latest_event_utc": None,
            "latest_event_type": None,
            "latest_side": None,
            "average_window_seconds": seconds,
            "minimum_price": None,
            "maximum_price": None,
            "sample_count": 0,
            "first_event_utc": None,
            "last_event_utc": None,
        }
    latest = connection.execute(
        f"""
        SELECT price_num, event_time_utc, event_type, side
        FROM price_events
        WHERE {' AND '.join(clauses)}
        ORDER BY event_time_utc DESC, id DESC
        LIMIT 1
        """,
        parameters,
    ).fetchone()
    if latest is None:
        return {
            "status": "NO_DATA",
            "llm_value": NO_DATA_TOKEN,
            "average_price": None,
            "point_price": None,
            "latest_price": None,
            "latest_event_utc": None,
            "latest_event_type": None,
            "latest_side": None,
            "average_window_seconds": seconds,
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
        # ``point_price`` is the last parsed event in the same real window;
        # callers may use it as the live point while retaining the mean as a
        # noise/stability feature.
        "point_price": float(latest["price_num"]),
        "latest_price": float(latest["price_num"]),
        "latest_event_utc": str(latest["event_time_utc"]),
        "latest_event_type": str(latest["event_type"]),
        "latest_side": str(latest["side"]),
        "average_window_seconds": seconds,
        "minimum_price": float(row["minimum_price"]),
        "maximum_price": float(row["maximum_price"]),
        "sample_count": count,
        "first_event_utc": str(row["first_event_utc"]),
        "last_event_utc": str(row["last_event_utc"]),
    }


def latest_individual_market_value(
    connection: sqlite3.Connection,
    *,
    end: datetime,
    instrument: str,
    market_label: str,
    trade_form: str,
    seconds: int = WINDOW_SECONDS,
) -> dict[str, Any]:
    """Return one direct physical quote, never an arithmetic average.

    Account1 physical offers are individual, non-conditional market events.
    A linked trade is more informative than its original offer, so it wins
    within the active window; otherwise the most recent offer is used.
    """
    start = end - timedelta(seconds=seconds)
    row = connection.execute(
        """
        SELECT price_num, event_time_utc, event_type
        FROM price_events
        WHERE event_time_utc > ? AND event_time_utc <= ?
          AND instrument = ? AND market_label = ? AND trade_form = ?
        ORDER BY CASE event_type WHEN 'TRADE' THEN 0 ELSE 1 END,
                 event_time_utc DESC, id DESC
        LIMIT 1
        """,
        (iso_utc(start), iso_utc(end), instrument, market_label, trade_form),
    ).fetchone()
    if row is None:
        return {
            "status": "NO_DATA",
            "llm_value": NO_DATA_TOKEN,
            "average_price": None,
            "point_price": None,
            "latest_price": None,
            "latest_event_utc": None,
            "latest_event_type": None,
            "average_window_seconds": seconds,
            "minimum_price": None,
            "maximum_price": None,
            "sample_count": 0,
            "first_event_utc": None,
            "last_event_utc": None,
        }
    price = float(row["price_num"])
    return {
        "status": "OBSERVED",
        "llm_value": price,
        "average_price": price,
        "point_price": price,
        "latest_price": price,
        "latest_event_utc": str(row["event_time_utc"]),
        "latest_event_type": str(row["event_type"]),
        "average_window_seconds": seconds,
        "minimum_price": price,
        "maximum_price": price,
        "sample_count": 1,
        "first_event_utc": str(row["event_time_utc"]),
        "last_event_utc": str(row["event_time_utc"]),
        "direct_event_type": str(row["event_type"]),
        "direct_event_weight": (
            CONFIRMED_TRADE_FLOW_WEIGHT
            if str(row["event_type"]) == "TRADE"
            else 1.0
        ),
    }


def latest_melted_events_by_type(
    connection: sqlite3.Connection,
    *,
    end: datetime,
    seconds: int = MARKET_AVERAGE_SECONDS,
    bucket_seconds: int = MELTED_LIVE_BUCKET_SECONDS,
) -> dict[str, Any]:
    """Return the last real melted event in each five-second bucket/type.

    A Telegram channel is event-driven; a quiet bucket has no fabricated row.
    If several parsed offers of one type arrive in the same bucket, only the
    newest one is exposed to the live model.  The complete raw rows remain in
    ``price_events`` for training and order-flow analysis.
    """
    if seconds <= 0 or bucket_seconds <= 0:
        raise ValueError("seconds and bucket_seconds must be positive")
    start = end - timedelta(seconds=seconds)
    rows = connection.execute(
        """
        SELECT id, market_label, settlement_term, trade_form,
               event_type, side, price_num, event_time_utc
        FROM price_events
        WHERE instrument = 'MELTED_GOLD'
          AND event_time_utc > ? AND event_time_utc <= ?
          AND price_num IS NOT NULL AND price_num > 0
        ORDER BY event_time_utc, id
        """,
        (iso_utc(start), iso_utc(end)),
    ).fetchall()
    by_bucket: dict[tuple[str, str, str, str, str, int], sqlite3.Row] = {}
    for row in rows:
        event_at = parse_datetime(str(row["event_time_utc"]))
        bucket = int(event_at.timestamp()) // bucket_seconds
        key = (
            str(row["market_label"]),
            str(row["settlement_term"]),
            str(row["trade_form"]),
            str(row["event_type"]),
            str(row["side"]),
            bucket,
        )
        previous = by_bucket.get(key)
        current_order = (str(row["event_time_utc"]), int(row["id"]))
        previous_order = (
            (str(previous["event_time_utc"]), int(previous["id"]))
            if previous is not None
            else None
        )
        if previous is None or current_order > previous_order:
            by_bucket[key] = row

    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for (market_label, settlement_term, trade_form, event_type, side, _), row in by_bucket.items():
        grouped.setdefault(
            (market_label, settlement_term, trade_form, event_type, side), []
        ).append(
            {
                "price": float(row["price_num"]),
                "event_time_utc": str(row["event_time_utc"]),
                "event_type": event_type,
                "side": side,
                "market_label": market_label,
                "settlement_term": settlement_term,
                "trade_form": trade_form,
            }
        )

    by_type: list[dict[str, Any]] = []
    for key, bucket_rows in grouped.items():
        bucket_rows.sort(key=lambda item: item["event_time_utc"])
        latest = bucket_rows[-1]
        by_type.append(
            {
                "market_label": key[0],
                "settlement_term": key[1],
                "trade_form": key[2],
                "event_type": key[3],
                "side": key[4],
                "latest": latest,
                "latest_price": latest["price"],
                "bucket_count": len(bucket_rows),
                "buckets": bucket_rows,
            }
        )
    by_type.sort(
        key=lambda item: (
            item["latest"]["event_time_utc"],
            item["market_label"],
            item["event_type"],
        )
    )
    return {
        "status": "OBSERVED" if by_type else "NO_DATA",
        "window_seconds": seconds,
        "bucket_seconds": bucket_seconds,
        "sample_count": len(by_bucket),
        "type_count": len(by_type),
        "by_type": by_type,
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
        "point_price": None,
        "latest_price": None,
        "latest_event_utc": None,
        "average_window_seconds": seconds,
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
        latest = connection.execute(
            """
            SELECT normalized_price_num, observed_at_utc
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
            ORDER BY observed_at_utc DESC, id DESC
            LIMIT 1
            """,
            (instrument_code, quote_kind, iso_utc(start), iso_utc(end)),
        ).fetchone()
        if latest is None:
            continue
        average = float(row["average_price"])
        return {
            "status": "OBSERVED",
            "llm_value": average,
            "average_price": average,
            "point_price": float(latest["normalized_price_num"]),
            "latest_price": float(latest["normalized_price_num"]),
            "latest_event_utc": str(latest["observed_at_utc"]),
            "average_window_seconds": seconds,
            "minimum_price": float(row["minimum_price"]),
            "maximum_price": float(row["maximum_price"]),
            "sample_count": int(row["sample_count"]),
            "first_event_utc": str(row["first_event_utc"]),
            "last_event_utc": str(row["last_event_utc"]),
            "source": "EXTERNAL_API",
            "selected_quote_kind": quote_kind,
        }
    return empty


def select_live_xauusd_average(
    connection: sqlite3.Connection,
    end: datetime,
    *,
    seconds: int = WINDOW_SECONDS,
) -> dict[str, Any]:
    """Prefer direct XAU/USD and fail over only to a corroborated PAXG proxy.

    The proxy is never written into the direct XAU/USD event stream and is
    excluded from historical training.  When a recent direct XAU observation
    exists, a proxy outside a two-percent consistency band is rejected.
    """

    direct = average_market_value(
        connection,
        end=end,
        seconds=seconds,
        instrument="XAUUSD",
        market_label="اونس جهانی",
    )
    if direct["status"] == "OBSERVED":
        direct["is_proxy"] = False
        direct["price_source"] = "TELEGRAM_DIRECT_XAUUSD"
        return direct

    proxy = average_external_market_value(
        connection,
        end=end,
        instrument_code="PAXG_USD_PROXY",
        quote_kinds=("MID",),
        seconds=seconds,
    )
    if proxy["status"] != "OBSERVED":
        direct["is_proxy"] = False
        direct["fallback_status"] = "PAXG_PROXY_NO_DATA"
        return direct

    recent_direct = average_market_value(
        connection,
        end=end,
        seconds=max(seconds, 15 * 60),
        instrument="XAUUSD",
        market_label="اونس جهانی",
    )
    recent_point = recent_direct.get("point_price")
    proxy_point = proxy.get("point_price")
    if recent_point is not None and proxy_point is not None:
        relative_gap = abs(float(proxy_point) / float(recent_point) - 1.0)
        if relative_gap > 0.02:
            direct["is_proxy"] = False
            direct["fallback_status"] = "PAXG_PROXY_OUTSIDE_RECENT_XAU_BAND"
            direct["fallback_relative_gap"] = relative_gap
            return direct

    proxy.update(
        {
            "status": "ESTIMATED",
            "selection": "BINANCE_PAXG_STABLECOIN_CORROBORATED_PROXY",
            "price_source": "PAXG_USDC_USDT_PROXY",
            "is_estimated": True,
            "is_proxy": True,
            "proxy_instrument": "PAXG_USD_PROXY",
            "direct_source_status": direct["status"],
            "safety_policy": "TWO_BOOK_CORROBORATION_AND_RECENT_XAU_BAND",
        }
    )
    return proxy


def select_usd_average(
    connection: sqlite3.Connection,
    settlement: str,
    end: datetime,
    *,
    seconds: int = WINDOW_SECONDS,
) -> dict[str, Any]:
    config = SETTLEMENT_CONFIG[settlement]
    last: dict[str, Any] | None = None
    for candidate_index, (settlement_term, trade_form) in enumerate(
        config["usd_candidates"]
    ):
        # Do not discard offers merely because a trade is also present in the
        # same 30-second window.  The raw parsed stream remains available in
        # event_type_counts and the point_price is the newest real event.
        value = average_market_value(
            connection,
            end=end,
            seconds=seconds,
            instrument="USD_HERAT",
            settlement_term=str(settlement_term),
            trade_form=str(trade_form),
        )
        last = value
        if value["status"] == "OBSERVED":
            counts = {
                str(row["event_type"]): int(row["count"])
                for row in connection.execute(
                    """
                    SELECT event_type, COUNT(*) AS count
                    FROM price_events
                    WHERE instrument = 'USD_HERAT'
                      AND settlement_term = ? AND trade_form = ?
                      AND event_time_utc > ? AND event_time_utc <= ?
                    GROUP BY event_type
                    """,
                    (
                        str(settlement_term),
                        str(trade_form),
                        iso_utc(end - timedelta(seconds=seconds)),
                        iso_utc(end),
                    ),
                )
            }
            value["event_type_counts"] = counts
            value["selected_event_types"] = sorted(counts)
            value["selection"] = (
                "ALL_EVENTS"
                if candidate_index == 0
                else "ALL_EVENTS_SETTLEMENT_FALLBACK"
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
    """Return a real Herat anchor; never substitute a USDT price."""
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


def _select_anchor_usdt_trend_average(
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


def _cash_banking_state(
    end: datetime,
    *,
    anchor_at: datetime,
) -> dict[str, Any]:
    local = end.astimezone(TEHRAN_TIMEZONE)
    minute = local.hour * 60 + local.minute
    weekday = local.weekday()
    close_minute = (
        THURSDAY_BANKING_CLOSE_MINUTE
        if weekday == 3
        else BANKING_CLOSE_MINUTE
    )
    if weekday != 4 and BANKING_START_MINUTE <= minute < close_minute:
        return {
            "state": "BANKING_OPEN",
            "closed_hours": 0.0,
            "reference_close_utc": None,
        }

    reference_close: datetime | None = None
    for days_back in range(8):
        candidate_date = local.date() - timedelta(days=days_back)
        candidate_weekday = candidate_date.weekday()
        if candidate_weekday == 4:
            continue
        candidate_close_minute = (
            THURSDAY_BANKING_CLOSE_MINUTE
            if candidate_weekday == 3
            else BANKING_CLOSE_MINUTE
        )
        candidate = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            candidate_close_minute // 60,
            candidate_close_minute % 60,
            tzinfo=TEHRAN_TIMEZONE,
        )
        if candidate <= local:
            reference_close = candidate
            break
    if reference_close is None:
        return {
            "state": "BANKING_CLOSED_UNKNOWN_BOUNDARY",
            "closed_hours": 0.0,
            "reference_close_utc": None,
        }
    elapsed_since_close = max(
        0.0,
        (local - reference_close).total_seconds() / 3600.0,
    )
    elapsed_since_anchor = max(
        0.0,
        (end - anchor_at).total_seconds() / 3600.0,
    )
    closed_hours = min(elapsed_since_close, elapsed_since_anchor, 10.0)
    state = (
        "FRIDAY_OR_HOLIDAY_CLOSED"
        if weekday == 4
        else (
            "BEFORE_BANKING_OPEN"
            if minute < BANKING_START_MINUTE
            else "AFTER_BANKING_CLOSE"
        )
    )
    return {
        "state": state,
        "closed_hours": closed_hours,
        "reference_close_utc": iso_utc(
            reference_close.astimezone(timezone.utc)
        ),
    }


def _select_cash_usd_average(
    connection: sqlite3.Connection,
    end: datetime,
    *,
    seconds: int,
) -> dict[str, Any]:
    fresh = select_usd_average(connection, "CASH", end, seconds=seconds)
    if fresh["status"] == "OBSERVED":
        fresh["price_source"] = "USD_HERAT"
        fresh["is_usdt_proxy"] = False
        fresh["is_estimated"] = False
        fresh["banking_session_state"] = _cash_banking_state(
            end,
            anchor_at=end,
        )["state"]
        return fresh

    cash_anchor = select_latest_usd_anchor(connection, "CASH", end)
    if cash_anchor["status"] != "OBSERVED":
        fresh["selection"] = "NO_CASH_HERAT_ANCHOR"
        fresh["price_source"] = None
        fresh["fallback_rejected"] = "DIRECT_USDT_PRICE_SUBSTITUTION_FORBIDDEN"
        fresh["is_usdt_proxy"] = False
        fresh["is_estimated"] = False
        return fresh

    anchor_at = parse_datetime(str(cash_anchor["anchor_event_utc"]))
    tomorrow_at_anchor = _select_anchor_usdt_trend_average(
        connection,
        "TOMORROW",
        anchor_at,
        seconds=USDT_ANCHOR_WINDOW_SECONDS,
    )
    tomorrow_now = _select_anchor_usdt_trend_average(
        connection,
        "TOMORROW",
        end,
        seconds=seconds,
    )

    driver_source = "USD_HERAT_TOMORROW"
    if (
        tomorrow_at_anchor.get("average_price") is not None
        and tomorrow_now.get("average_price") is not None
    ):
        driver_anchor = float(tomorrow_at_anchor["average_price"])
        driver_current = float(tomorrow_now["average_price"])
    else:
        secondary = _select_anchor_usdt_trend_average(
            connection,
            "CASH",
            end,
            seconds=seconds,
        )
        if secondary.get("average_price") is None or not secondary.get(
            "is_estimated"
        ):
            fresh["selection"] = "CASH_ANCHOR_WITHOUT_MARKET_MOVEMENT_DRIVER"
            fresh["price_source"] = None
            fresh["fallback_rejected"] = (
                "DIRECT_USDT_PRICE_SUBSTITUTION_FORBIDDEN"
            )
            fresh["cash_anchor"] = cash_anchor
            fresh["is_usdt_proxy"] = False
            fresh["is_estimated"] = False
            return fresh
        driver_source = "USDT_TREND_SECONDARY"
        driver_anchor = float(cash_anchor["average_price"])
        driver_current = driver_anchor * (
            1.0 + float(secondary.get("usdt_applied_return") or 0.0)
        )

    driver_delta = driver_current - driver_anchor
    driver_relative = driver_delta / driver_anchor
    if driver_relative > USDT_TREND_DEADBAND_RELATIVE:
        direction = "UP"
        beta = CASH_UP_MOVE_BETA
    elif driver_relative < -USDT_TREND_DEADBAND_RELATIVE:
        direction = "DOWN"
        beta = CASH_DOWN_MOVE_BETA
    else:
        direction = "NEUTRAL"
        beta = 0.0
    movement_adjustment = beta * driver_delta
    banking = _cash_banking_state(end, anchor_at=anchor_at)
    time_widening = min(
        CASH_CLOSED_WIDENING_MAX_TOMAN,
        float(banking["closed_hours"])
        * CASH_CLOSED_WIDENING_TOMAN_PER_HOUR,
    )
    estimated = (
        float(cash_anchor["average_price"])
        + movement_adjustment
        - time_widening
    )
    anchor_basis = (
        float(cash_anchor["average_price"]) - driver_anchor
        if driver_source == "USD_HERAT_TOMORROW"
        else None
    )
    current_basis = (
        estimated - driver_current
        if driver_source == "USD_HERAT_TOMORROW"
        else None
    )
    return {
        "status": "ESTIMATED",
        "llm_value": estimated,
        "average_price": estimated,
        "minimum_price": (
            float(cash_anchor["minimum_price"])
            + movement_adjustment
            - time_widening
        ),
        "maximum_price": (
            float(cash_anchor["maximum_price"])
            + movement_adjustment
            - time_widening
        ),
        "sample_count": int(cash_anchor["sample_count"]),
        "first_event_utc": cash_anchor["first_event_utc"],
        "last_event_utc": cash_anchor["last_event_utc"],
        "selection": (
            f"CASH_HERAT_ANCHOR_{driver_source}_{direction}_"
            "ASYMMETRIC_BANKING_TIME"
        ),
        "price_source": "USD_HERAT_CASH_TIME_AND_TOMORROW_BASIS_ESTIMATE",
        "is_usdt_proxy": False,
        "is_estimated": True,
        "anchor_price": float(cash_anchor["average_price"]),
        "anchor_event_utc": cash_anchor["anchor_event_utc"],
        "anchor_age_seconds": cash_anchor["anchor_age_seconds"],
        "market_movement_driver": driver_source,
        "market_driver_anchor_price": driver_anchor,
        "market_driver_current_price": driver_current,
        "market_driver_delta": driver_delta,
        "market_driver_relative_change": driver_relative,
        "market_direction": direction,
        "cash_direction_beta": beta,
        "cash_market_movement_adjustment_toman": movement_adjustment,
        "banking_session_state": banking["state"],
        "banking_reference_close_utc": banking["reference_close_utc"],
        "cash_closed_hours": banking["closed_hours"],
        "cash_time_widening_toman": time_widening,
        "cash_tomorrow_basis_at_anchor": anchor_basis,
        "cash_tomorrow_basis_estimated_current": current_basis,
    }


def select_effective_usd_average(
    connection: sqlite3.Connection,
    settlement: str,
    end: datetime,
    *,
    seconds: int = WINDOW_SECONDS,
) -> dict[str, Any]:
    if settlement == "CASH":
        return _select_cash_usd_average(
            connection,
            end,
            seconds=seconds,
        )
    return _select_anchor_usdt_trend_average(
        connection,
        settlement,
        end,
        seconds=seconds,
    )


def select_melted_average(
    connection: sqlite3.Connection,
    settlement: str,
    end: datetime,
    *,
    seconds: int = MARKET_AVERAGE_SECONDS,
) -> dict[str, Any]:
    config = SETTLEMENT_CONFIG[settlement]
    candidates = tuple(config.get("melted_candidates", ()))
    for label, trade_form, selection in candidates:
        if str(label) in LATEST_INDIVIDUAL_PHYSICAL_LABELS:
            value = latest_individual_market_value(
                connection,
                end=end,
                seconds=seconds,
                instrument="MELTED_GOLD",
                market_label=str(label),
                trade_form=str(trade_form),
            )
        else:
            value = average_market_value(
                connection,
                end=end,
                seconds=seconds,
                instrument="MELTED_GOLD",
                market_label=str(label),
                trade_form=str(trade_form),
            )
        if value["status"] == "OBSERVED":
            value["selection"] = str(selection)
            value["selected_market_label"] = str(label)
            value["selected_trade_form"] = str(trade_form)
            return value
    if settlement == "CASH":
        paper_candidates = (
            "آبشده فردایی",
            "آبشده امروزی",
            "آبشده حواله",
            "آبشده غیررسمی",
        )
        current_paper = None
        for label in paper_candidates:
            candidate = average_market_value(
                connection,
                end=end,
                seconds=seconds,
                instrument="MELTED_GOLD",
                market_label=label,
                trade_form="PAPER",
            )
            if candidate["status"] == "OBSERVED":
                current_paper = (label, candidate)
                break
        base = connection.execute(
            """
            SELECT price_num, event_time_utc, market_label
            FROM price_events
            WHERE instrument='MELTED_GOLD'
              AND trade_form='PHYSICAL'
              AND market_label IN ('آبشده نقدی', 'آبشده رسمی')
              AND event_time_utc <= ? AND event_time_utc >= ?
            ORDER BY event_time_utc DESC
            LIMIT 1
            """,
            (iso_utc(end), iso_utc(end - timedelta(days=4))),
        ).fetchone()
        if current_paper is not None and base is not None:
            paper_label, paper_now = current_paper
            base_time = parse_datetime(str(base["event_time_utc"]))
            paper_then = average_market_value(
                connection,
                end=base_time,
                seconds=seconds,
                instrument="MELTED_GOLD",
                market_label=paper_label,
                trade_form="PAPER",
            )
            if (
                paper_then["status"] == "OBSERVED"
                and float(paper_then["average_price"]) > 0
            ):
                bridged = float(base["price_num"]) * (
                    float(paper_now["average_price"])
                    / float(paper_then["average_price"])
                )
                return {
                    **paper_now,
                    "average_price": bridged,
                    "point_price": bridged,
                    "latest_price": bridged,
                    "minimum_price": bridged,
                    "maximum_price": bridged,
                    "selection": "PHYSICAL_BASE_PLUS_PAPER_DELTA_BRIDGE",
                    "selected_market_label": str(base["market_label"]),
                    "selected_trade_form": "PHYSICAL_BRIDGED_BY_PAPER",
                    "physical_base_event_utc": str(base["event_time_utc"]),
                    "physical_base_age_seconds": max(
                        0.0, (end - base_time).total_seconds()
                    ),
                    "paper_delta_label": paper_label,
                }
    value["selection"] = "NO_DATA"
    value["excluded_fallback"] = "IME_CORROBORATION_ONLY_NOT_DIRECT_MELTED_INPUT"
    value["selected_market_label"] = None
    value["selected_trade_form"] = None
    return value


def select_generic_coin_average(
    connection: sqlite3.Connection,
    settlement: str,
    end: datetime,
    *,
    seconds: int = MARKET_AVERAGE_SECONDS,
) -> dict[str, Any]:
    config = SETTLEMENT_CONFIG[settlement]
    telegram: dict[str, Any] | None = None
    for market_label, settlement_term, trade_form, selection in config.get(
        "coin_candidates", ()
    ):
        telegram = average_market_value(
            connection,
            end=end,
            seconds=seconds,
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
    # The public board frequently publishes ``سکه نقدی``/``سکه حواله``
    # without an explicit today/tomorrow term.  Keep rejecting those rows for
    # inference (form and settlement are independent), but expose a sanitized
    # diagnostic summary so the operator dashboard can distinguish a quiet
    # source from fresh-but-ineligible data.
    excluded_observations: list[dict[str, Any]] = []
    for market_label, trade_form in (
        ("سکه نقدی", "PHYSICAL"),
        ("سکه حواله", "PAPER"),
    ):
        excluded = average_market_value(
            connection,
            end=end,
            seconds=seconds,
            instrument="GOLD_COIN",
            market_label=market_label,
            settlement_term="UNKNOWN",
            trade_form=trade_form,
        )
        if excluded["status"] != "OBSERVED":
            continue
        excluded_observations.append(
            {
                "status": "OBSERVED",
                "market_label": market_label,
                "settlement_term": "UNKNOWN",
                "trade_form": trade_form,
                "point_price": excluded.get("point_price"),
                "average_price": excluded.get("average_price"),
                "sample_count": excluded.get("sample_count"),
                "latest_event_utc": excluded.get("latest_event_utc"),
            }
        )
    if excluded_observations:
        telegram["excluded_input_reason"] = (
            "AMBIGUOUS_SETTLEMENT_NOT_MODEL_ELIGIBLE"
        )
        telegram["excluded_observations"] = excluded_observations
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
    telegram["excluded_fallback"] = (
        "IME_CORROBORATION_ONLY_NOT_DIRECT_COIN_INPUT"
    )
    return telegram


def _select_group_offer_anchor_uncached(
    conversation_db: Path,
    *,
    commodity: str,
    settlement: str,
    trade_form: str,
    end: datetime,
    seconds: int = GROUP_ANCHOR_WINDOW_SECONDS,
    minimum_confidence: float = GROUP_MIN_CONFIDENCE,
    group_live_events_before: datetime | None = None,
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
        # ``group_live_events_before`` is set while the operator has paused
        # the live group feed.  Imported group rows after that boundary must
        # not become a live anchor while paused.  Manual operator entries are
        # intentionally queried against the current window below and remain
        # available for controlled testing.
        group_live_enabled = group_live_events_before is None
        group_end = min(end, group_live_events_before) if not group_live_enabled else end
        group_start = group_end - timedelta(seconds=seconds)
        manual_start = end - timedelta(seconds=seconds)
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
        rows: list[sqlite3.Row] = []
        if group_live_enabled:
            rows = list(connection.execute(
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
                    iso_utc(group_start),
                    iso_utc(group_end),
                ),
            ).fetchall())
        # Operator-entered observations are intentionally stored in their own
        # structured tables.  They are never fabricated as Telegram messages,
        # but participate in the same short lived quote/trade selection.
        if "manual_coin_offers" in tables:
            rows = list(rows) + list(
                connection.execute(
                    """
                    SELECT o.price, o.side, 1.0 AS confidence, o.quantity,
                           -o.id AS message_id, o.occurred_at_utc AS event_time_utc,
                           o.id, 1.0 AS live_range_weight,
                           'OPERATOR_MANUAL' AS cross_state
                    FROM manual_coin_offers AS o
                    WHERE o.commodity=? AND o.settlement=? AND o.trade_form=?
                      AND o.occurred_at_utc >= ? AND o.occurred_at_utc < ?
                      AND NOT EXISTS (
                        SELECT 1 FROM manual_coin_confirmed_trades AS t
                        WHERE t.offer_id=o.id
                      )
                    ORDER BY o.occurred_at_utc, o.id
                    """,
                    (
                        commodity,
                        settlement,
                        trade_form,
                        iso_utc(manual_start),
                        iso_utc(end),
                    ),
                ).fetchall()
            )
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
            if group_live_enabled:
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
                        iso_utc(group_start),
                        iso_utc(group_end),
                    ),
                ).fetchall()
        if "manual_coin_confirmed_trades" in tables:
            trade_rows = list(trade_rows) + list(
                connection.execute(
                    """
                    SELECT t.id, t.price, t.quantity, 1.0 AS confidence,
                           t.occurred_at_utc AS event_time_utc,
                           -t.offer_id AS offer_message_id
                    FROM manual_coin_confirmed_trades AS t
                    JOIN manual_coin_offers AS o ON o.id=t.offer_id
                    WHERE o.commodity=? AND o.settlement=? AND o.trade_form=?
                      AND t.occurred_at_utc >= ? AND t.occurred_at_utc < ?
                    ORDER BY t.occurred_at_utc, t.id
                    """,
                    (
                        commodity,
                        settlement,
                        trade_form,
                        iso_utc(manual_start),
                        iso_utc(end),
                    ),
                ).fetchall()
            )
        rows = sorted(rows, key=lambda row: (str(row["event_time_utc"]), int(row["id"])))
        trade_rows = sorted(
            trade_rows, key=lambda row: (str(row["event_time_utc"]), int(row["id"]))
        )
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


def select_group_offer_anchor(
    conversation_db: Path,
    *,
    commodity: str,
    settlement: str,
    trade_form: str,
    end: datetime,
    seconds: int = GROUP_ANCHOR_WINDOW_SECONDS,
    minimum_confidence: float = GROUP_MIN_CONFIDENCE,
    group_live_events_before: datetime | None = None,
) -> dict[str, Any]:
    """Return one model-independent group anchor for this exact snapshot."""

    key = (
        "GROUP_OFFER_ANCHOR",
        str(conversation_db.resolve()),
        commodity,
        settlement,
        trade_form,
        _snapshot_timestamp(end),
        int(seconds),
        float(minimum_confidence),
        _snapshot_timestamp(group_live_events_before),
    )
    cached = _snapshot_cache_get(key)
    if cached is not None:
        return cached
    result = _select_group_offer_anchor_uncached(
        conversation_db,
        commodity=commodity,
        settlement=settlement,
        trade_form=trade_form,
        end=end,
        seconds=seconds,
        minimum_confidence=minimum_confidence,
        group_live_events_before=group_live_events_before,
    )
    return _snapshot_cache_put(key, result)


def _select_historical_group_anchor_uncached(
    conversation_db: Path,
    *,
    commodity: str,
    settlement: str,
    trade_form: str,
    end: datetime,
    maximum_age_seconds: int = 7 * 86_400,
    minimum_confidence: float = GROUP_MIN_CONFIDENCE,
    group_live_events_before: datetime | None = None,
) -> dict[str, Any]:
    """Return a freshness-aware quality-approved transfer anchor.

    This is intentionally distinct from the five-minute live book.  Expired
    offers are not direct executable quotes, but their bubble relative to the
    underlying is still valuable when transferred forward.  Confirmed trades
    receive three times the source weight of offers; they no longer suppress a
    materially newer quality offer merely because they exist somewhere in the
    seven-day window.
    """
    empty = {
        "status": "NO_DATA", "reference_price_toman": None,
        "event_time_utc": None, "age_seconds": None, "reference_source": None,
    }
    if not conversation_db.is_file() or maximum_age_seconds <= 0:
        return empty
    connection = sqlite3.connect(f"file:{conversation_db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        start = end - timedelta(seconds=maximum_age_seconds)
        upper_bound = (
            min(end, group_live_events_before)
            if group_live_events_before is not None
            else end
        )
        observations: list[dict[str, Any]] = []
        if "confirmed_trades" in tables:
            trade_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(confirmed_trades)")}
            quality_join = "LEFT JOIN trade_market_quality tq ON tq.trade_id=t.id" if "trade_market_quality" in tables else ""
            quality_filter = "AND COALESCE(tq.training_eligible,1)=1" if "trade_market_quality" in tables else ""
            base_filter = "AND COALESCE(t.training_eligible,1)=1" if "training_eligible" in trade_columns else ""
            rows = connection.execute(
                f"""SELECT t.price,t.event_time_utc,t.confidence,t.quantity
                    FROM confirmed_trades t {quality_join}
                    WHERE t.commodity=? AND t.settlement=? AND t.trade_form=?
                      AND t.event_time_utc>=? AND t.event_time_utc<?
                      AND t.confidence>=? {base_filter} {quality_filter}
                    ORDER BY t.event_time_utc DESC,t.id DESC LIMIT 40""",
                (commodity, settlement, trade_form, iso_utc(start), iso_utc(upper_bound), minimum_confidence),
            ).fetchall()
            observations.extend(
                {
                    "price_toman": float(row["price"]) * PRICE_MULTIPLIER,
                    "event_time_utc": str(row["event_time_utc"]),
                    "stamp": parse_datetime(str(row["event_time_utc"])),
                    "confidence": float(row["confidence"]),
                    "quantity": row["quantity"],
                    "kind": "TRADE",
                    "source_factor": 3.0,
                }
                for row in rows
            )
        if {"offers", "messages"}.issubset(tables):
            quality_join = "LEFT JOIN offer_market_quality q ON q.offer_id=o.id" if "offer_market_quality" in tables else ""
            quality_filter = "AND COALESCE(q.training_eligible,1)=1" if "offer_market_quality" in tables else ""
            rows = connection.execute(
                f"""SELECT o.price,m.event_time_utc,o.confidence,o.quantity
                    FROM offers o JOIN messages m ON m.import_id=o.import_id AND m.message_id=o.message_id {quality_join}
                    WHERE o.commodity=? AND o.settlement=? AND o.trade_form=?
                      AND m.event_time_utc>=? AND m.event_time_utc<?
                      AND o.confidence>=? {quality_filter}
                    ORDER BY m.event_time_utc DESC,o.id DESC LIMIT 80""",
                (commodity, settlement, trade_form, iso_utc(start), iso_utc(upper_bound), minimum_confidence),
            ).fetchall()
            observations.extend(
                {
                    "price_toman": float(row["price"]) * PRICE_MULTIPLIER,
                    "event_time_utc": str(row["event_time_utc"]),
                    "stamp": parse_datetime(str(row["event_time_utc"])),
                    "confidence": float(row["confidence"]),
                    "quantity": row["quantity"],
                    "kind": "OFFER",
                    "source_factor": 1.0,
                }
                for row in rows
            )
    finally:
        connection.close()
    if not observations:
        return empty

    # Compare only the local state surrounding the newest observation.  This
    # prevents yesterday's dense book from overwhelming today's first quote,
    # while retaining enough neighbours to reject a truly isolated typo.
    raw_newest_stamp = max(row["stamp"] for row in observations)
    cluster_start = raw_newest_stamp - timedelta(minutes=30)
    cluster = [row for row in observations if row["stamp"] >= cluster_start]
    raw_latest = max(
        cluster,
        key=lambda row: (row["stamp"], 1 if row["kind"] == "TRADE" else 0),
    )
    outlier_rejected_count = 0
    latest_rejected_reason = None
    # Source weights are useful only after the observations agree on price
    # scale.  Otherwise one mislabelled half/quarter trade can outweigh several
    # valid Imam offers merely because trades carry a 3x weight.  Require at
    # least three retained same-book witnesses.  A five-percent floor matches
    # the causal live-book gate: it catches an isolated wrong digit or product
    # family without allowing source weights to turn it into the consensus.
    if len(cluster) >= 4:
        raw_prices = [float(row["price_toman"]) for row in cluster]
        raw_center = float(statistics.median(raw_prices))
        raw_relative_mad = float(
            statistics.median(abs(price - raw_center) for price in raw_prices)
        ) / max(1.0, raw_center)
        scale_limit = max(
            HISTORICAL_GROUP_MAXIMUM_RELATIVE_DEVIATION,
            6.0 * raw_relative_mad,
        )
        scale_consistent = [
            row
            for row in cluster
            if abs(float(row["price_toman"]) - raw_center)
            / max(1.0, raw_center)
            <= scale_limit
        ]
        if len(scale_consistent) >= 3 and len(scale_consistent) < len(cluster):
            retained_ids = {id(row) for row in scale_consistent}
            outlier_rejected_count = len(cluster) - len(scale_consistent)
            if id(raw_latest) not in retained_ids:
                latest_rejected_reason = "LOCAL_PRICE_SCALE_OUTLIER"
            cluster = scale_consistent
    newest_stamp = max(row["stamp"] for row in cluster)
    for row in cluster:
        relative_age = max(0.0, (newest_stamp - row["stamp"]).total_seconds())
        quantity_weight = math.sqrt(max(1.0, min(25.0, float(row["quantity"] or 1))))
        row["weight"] = (
            float(row["source_factor"])
            * float(row["confidence"])
            * quantity_weight
            * math.exp(-relative_age / (8.0 * 60.0))
        )
    prices = [float(row["price_toman"]) for row in cluster]
    weights = [float(row["weight"]) for row in cluster]
    weight_sum = sum(weights)
    consensus = (
        sum(price * weight for price, weight in zip(prices, weights)) / weight_sum
        if weight_sum > 0
        else prices[-1]
    )
    median = weighted_quantile(prices, weights, 0.50)
    relative_mad = (
        weighted_quantile([abs(price - median) for price in prices], weights, 0.50)
        / max(1.0, median)
    )
    latest = max(
        cluster,
        key=lambda row: (row["stamp"], 1 if row["kind"] == "TRADE" else 0),
    )
    latest_deviation = abs(float(latest["price_toman"]) - consensus) / max(1.0, consensus)
    # A recent observation inside the local consensus envelope is the best
    # representation of the last known market.  Outside that envelope, use the
    # robust time/quantity/source weighted consensus instead of trusting a typo.
    consensus_limit = max(0.0075, 3.0 * relative_mad)
    latest_is_consistent = latest_deviation <= consensus_limit
    reference_price = float(latest["price_toman"]) if latest_is_consistent else consensus
    reference_source = (
        f"LATEST_QUALITY_{latest['kind']}_RECENCY_VALIDATED"
        if latest_is_consistent
        else "RECENCY_WEIGHTED_TRADE_OFFER_CONSENSUS"
    )
    age_seconds = max(0.0, (end - newest_stamp).total_seconds())
    return {
        "status": "OBSERVED",
        "reference_price_toman": reference_price,
        "event_time_utc": str(latest["event_time_utc"]),
        "age_seconds": age_seconds,
        "reference_source": reference_source,
        "confidence": float(latest["confidence"]),
        "quantity": latest["quantity"],
        "trade_count": sum(row["kind"] == "TRADE" for row in cluster),
        "offer_count": sum(row["kind"] == "OFFER" for row in cluster),
        "cluster_window_seconds": 30 * 60,
        "consensus_price_toman": consensus,
        "latest_price_toman": float(latest["price_toman"]),
        "latest_kind": latest["kind"],
        "latest_deviation_percent": latest_deviation * 100.0,
        "relative_mad": relative_mad,
        "latest_is_consistent": latest_is_consistent,
        "outlier_rejected_count": outlier_rejected_count,
        "latest_rejected_reason": latest_rejected_reason,
        "source_weight_policy": "TRADE_3X_OFFER_WITH_8M_RECENCY_DECAY",
    }


def select_historical_group_anchor(
    conversation_db: Path,
    *,
    commodity: str,
    settlement: str,
    trade_form: str,
    end: datetime,
    maximum_age_seconds: int = 7 * 86_400,
    minimum_confidence: float = GROUP_MIN_CONFIDENCE,
    group_live_events_before: datetime | None = None,
) -> dict[str, Any]:
    """Return one model-independent historical anchor per exact snapshot."""

    key = (
        "HISTORICAL_GROUP_ANCHOR",
        str(conversation_db.resolve()),
        commodity,
        settlement,
        trade_form,
        _snapshot_timestamp(end),
        int(maximum_age_seconds),
        float(minimum_confidence),
        _snapshot_timestamp(group_live_events_before),
    )
    cached = _snapshot_cache_get(key)
    if cached is not None:
        return cached
    result = _select_historical_group_anchor_uncached(
        conversation_db,
        commodity=commodity,
        settlement=settlement,
        trade_form=trade_form,
        end=end,
        maximum_age_seconds=maximum_age_seconds,
        minimum_confidence=minimum_confidence,
        group_live_events_before=group_live_events_before,
    )
    return _snapshot_cache_put(key, result)


def select_last_cash_tomorrow_ratio(
    conversation_db: Path,
    *,
    commodity: str,
    trade_form: str,
    end: datetime,
    maximum_age_seconds: int = 6 * 3_600,
    maximum_pair_gap_seconds: int = 10 * 60,
) -> dict[str, Any]:
    """Find a recent, near-contemporaneous cash/tomorrow ratio anchor.

    This deliberately uses structured manual observations only.  A
    cash/tomorrow spread changes through the Tehran banking day, therefore an
    old pair is not a valid conversion rule for a current quote.
    """

    empty = {
        "status": "NO_DATA",
        "cash_price_toman": None,
        "tomorrow_price_toman": None,
        "ratio": None,
        "cash_event_utc": None,
        "tomorrow_event_utc": None,
        "pair_gap_seconds": None,
        "confirmation_count": 0,
        "quality_gate": "NO_PAIR",
    }
    if not conversation_db.is_file():
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
        if "manual_coin_offers" not in tables:
            return empty
        start = end - timedelta(seconds=max(1, maximum_age_seconds))
        rows = connection.execute(
            """
            SELECT o.settlement, t.price, t.occurred_at_utc AS event_time_utc,
                   1 AS is_confirmed, t.is_live_at_entry AS is_live
            FROM manual_coin_confirmed_trades AS t
            JOIN manual_coin_offers AS o ON o.id=t.offer_id
            WHERE o.commodity=? AND o.trade_form=?
              AND t.occurred_at_utc >= ? AND t.occurred_at_utc < ?
            UNION ALL
            SELECT o.settlement, o.price, o.occurred_at_utc AS event_time_utc,
                   0 AS is_confirmed, o.is_live_at_entry AS is_live
            FROM manual_coin_offers AS o
            WHERE o.commodity=? AND o.trade_form=?
              AND o.occurred_at_utc >= ? AND o.occurred_at_utc < ?
              AND NOT EXISTS (
                SELECT 1 FROM manual_coin_confirmed_trades AS t
                WHERE t.offer_id=o.id
              )
            ORDER BY event_time_utc DESC
            LIMIT 80
            """,
            (
                commodity, trade_form, iso_utc(start), iso_utc(end),
                commodity, trade_form, iso_utc(start), iso_utc(end),
            ),
        ).fetchall()
    finally:
        connection.close()

    by_settlement = {
        settlement: [
            row for row in rows if str(row["settlement"]) == settlement
        ]
        for settlement in ("CASH", "TOMORROW")
    }
    if not by_settlement["CASH"] or not by_settlement["TOMORROW"]:
        return empty
    candidates: list[tuple[int, float, float, sqlite3.Row, sqlite3.Row]] = []
    for cash in by_settlement["CASH"]:
        cash_time = parse_datetime(str(cash["event_time_utc"]))
        for tomorrow in by_settlement["TOMORROW"]:
            tomorrow_time = parse_datetime(str(tomorrow["event_time_utc"]))
            gap = abs((cash_time - tomorrow_time).total_seconds())
            if gap > maximum_pair_gap_seconds:
                continue
            confirmation_count = int(cash["is_confirmed"]) + int(
                tomorrow["is_confirmed"]
            )
            # A pair consisting only of old manually backfilled offers is too
            # weak to learn a time-of-day spread.  It can be used only when
            # both quotes were explicitly entered as live.
            if not confirmation_count and not (
                int(cash["is_live"]) and int(tomorrow["is_live"])
            ):
                continue
            recency = max(cash_time, tomorrow_time).timestamp()
            candidates.append(
                (-confirmation_count, gap, -recency, cash, tomorrow)
            )
    if not candidates:
        return empty
    _, _, _, cash, tomorrow = min(candidates, key=lambda row: row[:3])
    cash_price = float(cash["price"]) * PRICE_MULTIPLIER
    tomorrow_price = float(tomorrow["price"]) * PRICE_MULTIPLIER
    if tomorrow_price <= 0:
        return empty
    ratio = cash_price / tomorrow_price
    if not 0.97 <= ratio <= 1.03:
        return empty
    return {
        "status": "OBSERVED",
        "cash_price_toman": cash_price,
        "tomorrow_price_toman": tomorrow_price,
        "ratio": ratio,
        "cash_event_utc": str(cash["event_time_utc"]),
        "tomorrow_event_utc": str(tomorrow["event_time_utc"]),
        "pair_gap_seconds": abs(
            (
                parse_datetime(str(cash["event_time_utc"]))
                - parse_datetime(str(tomorrow["event_time_utc"]))
            ).total_seconds()
        ),
        "confirmation_count": int(cash["is_confirmed"])
        + int(tomorrow["is_confirmed"]),
        "quality_gate": (
            "CONFIRMED_PAIR"
            if int(cash["is_confirmed"]) or int(tomorrow["is_confirmed"])
            else "BOTH_QUOTES_ENTERED_LIVE"
        ),
        "selection": "FRESH_QUALITY_GATED_CASH_TOMORROW_PAIR",
    }


def _select_empirical_cash_tomorrow_ratio_uncached(
    conversation_db: Path,
    *,
    commodity: str,
    trade_form: str,
    end: datetime,
    maximum_age_seconds: int = 30 * 86_400,
    maximum_pair_gap_seconds: int = 20 * 60,
    group_live_events_before: datetime | None = None,
) -> dict[str, Any]:
    """Learn a robust tomorrow/cash ratio from near-synchronous group events.

    This is used only when there is no live tomorrow quote.  Each tomorrow
    observation is paired with the nearest same-commodity cash observation;
    confirmed trades carry three times the source weight of offers.  When a
    commodity has fewer than five usable pairs, a pooled coin-market ratio is
    returned with lower confidence instead of a stale direct tomorrow quote.
    """
    empty = {
        "status": "NO_DATA",
        "ratio": None,
        "relative_qhat": None,
        "pair_count": 0,
        "scope": None,
    }
    if not conversation_db.is_file():
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
        start = end - timedelta(seconds=max(1, maximum_age_seconds))
        upper_bound = (
            min(end, group_live_events_before)
            if group_live_events_before is not None
            else end
        )
        offer_join = (
            "LEFT JOIN offer_market_quality q ON q.offer_id=o.id"
            if "offer_market_quality" in tables
            else ""
        )
        offer_filter = (
            "AND COALESCE(q.training_eligible,1)=1"
            if "offer_market_quality" in tables
            else ""
        )
        rows = [
            {
                "commodity": str(row["commodity"]),
                "settlement": str(row["settlement"]),
                "price": float(row["price"]),
                "stamp": parse_datetime(str(row["event_time_utc"])),
                "confidence": float(row["confidence"]),
                "source_factor": 1.0,
            }
            for row in connection.execute(
                f"""SELECT o.commodity,o.settlement,o.price,o.confidence,
                           m.event_time_utc
                    FROM offers o
                    JOIN messages m ON m.import_id=o.import_id AND m.message_id=o.message_id
                    {offer_join}
                    WHERE o.trade_form=? AND o.settlement IN ('CASH','TOMORROW')
                      AND m.event_time_utc>=? AND m.event_time_utc<?
                      AND o.confidence>=0.80 {offer_filter}
                    ORDER BY m.event_time_utc""",
                (trade_form, iso_utc(start), iso_utc(upper_bound)),
            ).fetchall()
        ]
        if "confirmed_trades" in tables:
            trade_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(confirmed_trades)")
            }
            trade_join = (
                "LEFT JOIN trade_market_quality q ON q.trade_id=t.id"
                if "trade_market_quality" in tables
                else ""
            )
            trade_filter = (
                "AND COALESCE(q.training_eligible,1)=1"
                if "trade_market_quality" in tables
                else ""
            )
            base_filter = (
                "AND COALESCE(t.training_eligible,1)=1"
                if "training_eligible" in trade_columns
                else ""
            )
            rows.extend(
                {
                    "commodity": str(row["commodity"]),
                    "settlement": str(row["settlement"]),
                    "price": float(row["price"]),
                    "stamp": parse_datetime(str(row["event_time_utc"])),
                    "confidence": float(row["confidence"]),
                    "source_factor": 3.0,
                }
                for row in connection.execute(
                    f"""SELECT t.commodity,t.settlement,t.price,t.confidence,
                               t.event_time_utc
                        FROM confirmed_trades t {trade_join}
                        WHERE t.trade_form=? AND t.settlement IN ('CASH','TOMORROW')
                          AND t.event_time_utc>=? AND t.event_time_utc<?
                          AND t.confidence>=0.85 {base_filter} {trade_filter}
                        ORDER BY t.event_time_utc""",
                    (trade_form, iso_utc(start), iso_utc(upper_bound)),
                ).fetchall()
            )
    finally:
        connection.close()

    def build_pairs(scope_commodity: str | None) -> list[dict[str, Any]]:
        selected = [
            row
            for row in rows
            if scope_commodity is None or row["commodity"] == scope_commodity
        ]
        by_commodity: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for row in selected:
            by_commodity.setdefault(row["commodity"], {"CASH": [], "TOMORROW": []})[
                row["settlement"]
            ].append(row)
        pairs: list[dict[str, Any]] = []
        for values in by_commodity.values():
            cash = sorted(values["CASH"], key=lambda row: row["stamp"])
            tomorrow = sorted(values["TOMORROW"], key=lambda row: row["stamp"])
            cash_times = [row["stamp"] for row in cash]
            for future in tomorrow:
                index = bisect.bisect_left(cash_times, future["stamp"])
                candidates = [
                    cash[position]
                    for position in (index - 1, index)
                    if 0 <= position < len(cash)
                ]
                if not candidates:
                    continue
                current = min(
                    candidates,
                    key=lambda row: abs((row["stamp"] - future["stamp"]).total_seconds()),
                )
                gap = abs((current["stamp"] - future["stamp"]).total_seconds())
                if gap > maximum_pair_gap_seconds or current["price"] <= 0:
                    continue
                ratio = future["price"] / current["price"]
                if not 0.95 <= ratio <= 1.08:
                    continue
                pair_stamp = max(current["stamp"], future["stamp"])
                recency = math.exp(
                    -max(0.0, (end - pair_stamp).total_seconds())
                    / (14.0 * 86_400.0)
                )
                pairs.append(
                    {
                        "ratio": ratio,
                        "stamp": pair_stamp,
                        "gap_seconds": gap,
                        "weight": (
                            math.sqrt(current["source_factor"] * future["source_factor"])
                            * current["confidence"]
                            * future["confidence"]
                            * recency
                        ),
                    }
                )
        return pairs

    pairs = build_pairs(commodity)
    scope = "COMMODITY"
    if len(pairs) < 5:
        pairs = build_pairs(None)
        scope = "POOLED_COIN_MARKET"
    if len(pairs) < 5:
        return empty
    ratios = [float(row["ratio"]) for row in pairs]
    weights = [float(row["weight"]) for row in pairs]
    center = weighted_quantile(ratios, weights, 0.50)
    mad = weighted_quantile([abs(value - center) for value in ratios], weights, 0.50)
    robust_limit = max(0.008, 4.0 * mad)
    filtered = [row for row in pairs if abs(float(row["ratio"]) - center) <= robust_limit]
    ratios = [float(row["ratio"]) for row in filtered]
    weights = [float(row["weight"]) for row in filtered]
    center = weighted_quantile(ratios, weights, 0.50)
    absolute_errors = [abs(value - center) for value in ratios]
    relative_qhat = max(0.0025, weighted_quantile(absolute_errors, weights, 0.80))
    relative_qhat = min(0.012, relative_qhat)
    newest = max(row["stamp"] for row in filtered)
    return {
        "status": "OBSERVED",
        "ratio": center,
        "relative_qhat": relative_qhat,
        "pair_count": len(filtered),
        "scope": scope,
        "latest_pair_utc": iso_utc(newest),
        "age_seconds": max(0.0, (end - newest).total_seconds()),
        "weighted_mad": mad,
        "selection": "ROBUST_NEAR_SYNCHRONOUS_TOMORROW_DIVIDED_BY_CASH",
        "trade_weight_multiplier": 3.0,
    }


def select_empirical_cash_tomorrow_ratio(
    conversation_db: Path,
    *,
    commodity: str,
    trade_form: str,
    end: datetime,
    maximum_age_seconds: int = 30 * 86_400,
    maximum_pair_gap_seconds: int = 20 * 60,
    group_live_events_before: datetime | None = None,
) -> dict[str, Any]:
    """Return the per-snapshot empirical ratio, shared by main and shadows."""

    normalized_end = end.astimezone(timezone.utc)
    cutoff = (
        group_live_events_before.astimezone(timezone.utc).isoformat()
        if group_live_events_before is not None
        else None
    )
    key = (
        str(conversation_db.resolve()),
        commodity,
        trade_form,
        normalized_end.isoformat(),
        int(maximum_age_seconds),
        int(maximum_pair_gap_seconds),
        cutoff,
    )
    cached = _empirical_ratio_cache_get(key)
    if cached is not None:
        return cached
    result = _select_empirical_cash_tomorrow_ratio_uncached(
        conversation_db,
        commodity=commodity,
        trade_form=trade_form,
        end=end,
        maximum_age_seconds=maximum_age_seconds,
        maximum_pair_gap_seconds=maximum_pair_gap_seconds,
        group_live_events_before=group_live_events_before,
    )
    return _empirical_ratio_cache_put(key, result)


def _select_last_low_date_to_imam_ratio_uncached(
    conversation_db: Path,
    *,
    commodity: str,
    settlement: str,
    trade_form: str,
    end: datetime,
    maximum_age_seconds: int = 4 * 86_400,
    maximum_pair_gap_seconds: int = 30 * 60,
) -> dict[str, Any]:
    """Return the last same-settlement low-date/Imam ratio anchor.

    Low-date coins do not inherit the historical premium-coin bubble model.
    Their only non-live transfer is a nearby, same-settlement observed ratio
    to Imam, which preserves the small discount/premium seen in that market.
    """

    empty = {
        "status": "NO_DATA",
        "ratio_to_imam": None,
        "low_date_price_toman": None,
        "imam_price_toman": None,
        "low_date_event_utc": None,
        "imam_event_utc": None,
        "pair_gap_seconds": None,
    }
    if commodity not in COMMODITY_SPECS or not conversation_db.is_file():
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
        if not {
            "manual_coin_offers",
            "manual_coin_confirmed_trades",
        }.issubset(tables):
            return empty
        start = end - timedelta(seconds=max(1, maximum_age_seconds))
        rows = connection.execute(
            """
            SELECT o.commodity, t.price, t.occurred_at_utc AS event_time_utc,
                   1 AS is_confirmed
            FROM manual_coin_confirmed_trades AS t
            JOIN manual_coin_offers AS o ON o.id=t.offer_id
            WHERE o.commodity IN (?, 'امام')
              AND o.settlement=? AND o.trade_form=?
              AND t.occurred_at_utc >= ? AND t.occurred_at_utc < ?
            UNION ALL
            SELECT o.commodity, o.price, o.occurred_at_utc AS event_time_utc,
                   0 AS is_confirmed
            FROM manual_coin_offers AS o
            WHERE o.commodity IN (?, 'امام')
              AND o.settlement=? AND o.trade_form=?
              AND o.occurred_at_utc >= ? AND o.occurred_at_utc < ?
              AND NOT EXISTS (
                SELECT 1 FROM manual_coin_confirmed_trades AS t
                WHERE t.offer_id=o.id
              )
            ORDER BY event_time_utc DESC
            LIMIT 120
            """,
            (
                commodity, settlement, trade_form, iso_utc(start), iso_utc(end),
                commodity, settlement, trade_form, iso_utc(start), iso_utc(end),
            ),
        ).fetchall()
    finally:
        connection.close()
    low_rows = [row for row in rows if str(row["commodity"]) == commodity]
    imam_rows = [row for row in rows if str(row["commodity"]) == "امام"]
    if not low_rows or not imam_rows:
        return empty
    candidates: list[tuple[float, float, sqlite3.Row, sqlite3.Row]] = []
    for low in low_rows:
        low_time = parse_datetime(str(low["event_time_utc"]))
        for imam in imam_rows:
            imam_time = parse_datetime(str(imam["event_time_utc"]))
            gap = abs((low_time - imam_time).total_seconds())
            if gap > maximum_pair_gap_seconds:
                continue
            recency = max(low_time, imam_time).timestamp()
            confirmation_bonus = float(
                int(low["is_confirmed"]) + int(imam["is_confirmed"])
            )
            candidates.append((gap, -recency - confirmation_bonus, low, imam))
    if not candidates:
        return empty
    _, _, low, imam = min(candidates, key=lambda row: (row[0], row[1]))
    low_price = float(low["price"]) * PRICE_MULTIPLIER
    imam_price = float(imam["price"]) * PRICE_MULTIPLIER
    if imam_price <= 0:
        return empty
    ratio = low_price / imam_price
    expected_ratio = (
        COMMODITY_SPECS[commodity].coefficient
        / COMMODITY_SPECS["امام"].coefficient
    )
    if not expected_ratio * 0.80 <= ratio <= expected_ratio * 1.20:
        return empty
    return {
        "status": "OBSERVED",
        "ratio_to_imam": ratio,
        "low_date_price_toman": low_price,
        "imam_price_toman": imam_price,
        "low_date_event_utc": str(low["event_time_utc"]),
        "imam_event_utc": str(imam["event_time_utc"]),
        "pair_gap_seconds": abs(
            (
                parse_datetime(str(low["event_time_utc"]))
                - parse_datetime(str(imam["event_time_utc"]))
            ).total_seconds()
        ),
        "selection": "LAST_SAME_SETTLEMENT_LOW_DATE_TO_IMAM_PAIR",
    }


def select_last_low_date_to_imam_ratio(
    conversation_db: Path,
    *,
    commodity: str,
    settlement: str,
    trade_form: str,
    end: datetime,
    maximum_age_seconds: int = 4 * 86_400,
    maximum_pair_gap_seconds: int = 30 * 60,
) -> dict[str, Any]:
    """Return the cached low-date/Imam ratio for one input snapshot."""

    key = (
        "LOW_DATE_TO_IMAM_RATIO",
        str(conversation_db.resolve()),
        commodity,
        settlement,
        trade_form,
        _snapshot_timestamp(end),
        int(maximum_age_seconds),
        int(maximum_pair_gap_seconds),
    )
    cached = _snapshot_cache_get(key)
    if cached is not None:
        return cached
    result = _select_last_low_date_to_imam_ratio_uncached(
        conversation_db,
        commodity=commodity,
        settlement=settlement,
        trade_form=trade_form,
        end=end,
        maximum_age_seconds=maximum_age_seconds,
        maximum_pair_gap_seconds=maximum_pair_gap_seconds,
    )
    return _snapshot_cache_put(key, result)


def select_last_manual_coin_anchor(
    conversation_db: Path,
    *,
    commodity: str,
    settlement: str,
    trade_form: str,
    end: datetime,
    maximum_age_seconds: int = 4 * 86_400,
) -> dict[str, Any]:
    """Select a strictly-prior same-market *confirmed trade* for transfer.

    An expired offer is a directional signal, not a price anchor.  This rule
    prevents a stale or mistyped offer from moving an estimate long after its
    five-minute live lifetime.
    """
    empty = {
        "status": "NO_DATA",
        "price_toman": None,
        "event_time_utc": None,
        "event_kind": None,
    }
    if not conversation_db.is_file():
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
        if not {
            "manual_coin_offers",
            "manual_coin_confirmed_trades",
        }.issubset(tables):
            return empty
        start = end - timedelta(seconds=max(1, maximum_age_seconds))
        row = connection.execute(
            """
            SELECT t.price, t.occurred_at_utc AS event_time_utc, 'TRADE' AS event_kind
            FROM manual_coin_confirmed_trades AS t
            JOIN manual_coin_offers AS o ON o.id=t.offer_id
            WHERE o.commodity=? AND o.settlement=? AND o.trade_form=?
              AND t.occurred_at_utc >= ? AND t.occurred_at_utc < ?
            ORDER BY event_time_utc DESC
            LIMIT 1
            """,
            (
                commodity, settlement, trade_form, iso_utc(start), iso_utc(end),
            ),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return empty
    return {
        "status": "OBSERVED",
        "price_toman": float(row["price"]) * PRICE_MULTIPLIER,
        "event_time_utc": str(row["event_time_utc"]),
        "event_kind": str(row["event_kind"]),
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
            # A completed physical or paper transaction is stronger evidence
            # than an unfilled offer.  It remains a separate event (no price
            # averaging is introduced for physical quotes), while its market
            # pressure contribution receives the explicit trade multiplier.
            weight *= CONFIRMED_TRADE_FLOW_WEIGHT
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
    direction = "BUY_PRESSURE" if score > 0.05 else ("SELL_PRESSURE" if score < -0.05 else "NEUTRAL")
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


def historical_market_context(
    connection: sqlite3.Connection, settlement: str, end: datetime
) -> dict[str, dict[str, Any]]:
    config = SETTLEMENT_CONFIG[settlement]
    melted = select_melted_average(
        connection, settlement, end, seconds=MARKET_AVERAGE_SECONDS
    )
    if melted["status"] == "OBSERVED":
        if melted.get("selection") == "IME_STANDARDIZED_MESGHAL_750_FALLBACK":
            melted["training_reference_weight"] = 0.80
        else:
            melted["training_reference_weight"] = (
                0.75 if melted.get("selected_trade_form") == "PAPER" else 1.0
            )
    else:
        # Training-only fallback: a paper quote observed before the label in
        # the same trailing minute is a useful but weaker intrinsic reference.
        # It remains explicitly marked and down-weighted; live inference keeps
        # the strict physical/settlement policy in select_melted_average().
        paper_reference = average_market_value(
            connection,
            end=end,
            instrument="MELTED_GOLD",
            trade_form="PAPER",
        )
        if paper_reference["status"] == "OBSERVED":
            paper_reference["selection"] = (
                "TRAINING_ONLY_SAME_MINUTE_PAPER_REFERENCE"
            )
            paper_reference["selected_market_label"] = "ANY_PAPER_MELTED"
            paper_reference["selected_trade_form"] = "PAPER"
            paper_reference["training_reference_weight"] = 0.65
            melted = paper_reference
        else:
            melted["training_reference_weight"] = 0.0
    return {
        "melted_gold": melted,
        "generic_coin": select_generic_coin_average(
            connection, settlement, end, seconds=MARKET_AVERAGE_SECONDS
        ),
        "xauusd": average_market_value(
            connection,
            end=end,
            seconds=MARKET_AVERAGE_SECONDS,
            instrument="XAUUSD",
            market_label="اونس جهانی",
        ),
        # Every market lane has the same 30-second observed window.  The
        # latest parsed event is carried separately as point_price; no old
        # dollar/USDT quote is copied into a new observation.
        "usd": select_effective_usd_average(
            connection, settlement, end, seconds=MARKET_AVERAGE_SECONDS
        ),
        "usdt": select_usdt_average(
            connection, end, seconds=MARKET_AVERAGE_SECONDS
        ),
        "melted_latest_by_type": latest_melted_events_by_type(
            connection,
            end=end,
            seconds=MARKET_AVERAGE_SECONDS,
            bucket_seconds=MELTED_LIVE_BUCKET_SECONDS,
        ),
        "order_flow": market_order_flow(connection, settlement, end),
        "market_regime": detect_market_regime(connection, end, settlement),
    }


def training_example(
    connection: sqlite3.Connection, trade: dict[str, Any]
) -> dict[str, Any] | None:
    name = str(trade["commodity_name"])
    settlement = str(trade["settlement_type"])
    spec = COMMODITY_SPECS.get(name)
    config = SETTLEMENT_CONFIG.get(settlement)
    if spec is None or config is None:
        return None
    event_time = parse_datetime(str(trade["created_at"]))
    context = historical_market_context(connection, settlement, event_time)
    melted = context["melted_gold"]
    if melted["status"] != "OBSERVED":
        return None
    intrinsic = float(melted["average_price"]) * spec.coefficient
    observed = int(trade["price"]) * PRICE_MULTIPLIER
    bubble_ratio = observed / intrinsic - 1
    # Broad physical/data-quality guard. It removes obvious decimal/commodity
    # mistakes while retaining large legitimate bubbles in lighter coins.
    excluded_competitive_price = bool(
        trade.get("offer_excluded_from_competitive_price")
    )
    accepted = -0.15 <= bubble_ratio <= 2.0 and not excluded_competitive_price
    usd_value = context["usd"]["average_price"]
    xau_value = context["xauusd"]["average_price"]
    theoretical_melted = (
        float(usd_value) * float(xau_value) / 9.572737
        if usd_value is not None and xau_value is not None
        else None
    )
    return {
        "source_kind": "PROJECT_COMPLETED_TRADE",
        "source_weight": PROJECT_COMPLETED_TRADE_TRAINING_WEIGHT
        * float(melted.get("training_reference_weight", 1.0)),
        "source_confidence": 1.0,
        "trade_id": int(trade["id"]),
        "commodity_id": int(trade["commodity_id"]),
        "commodity_name": name,
        "settlement_type": settlement,
        "event_time_utc": iso_utc(event_time),
        "project_price": int(trade["price"]),
        "observed_price_toman": observed,
        "melted_average_toman": float(melted["average_price"]),
        "melted_latest_toman": melted.get("point_price"),
        "melted_samples": int(melted["sample_count"]),
        "melted_latest_by_type": context.get("melted_latest_by_type"),
        "melted_reference_selection": melted.get("selection"),
        "melted_reference_trade_form": melted.get("selected_trade_form"),
        "melted_reference_weight": float(
            melted.get("training_reference_weight", 1.0)
        ),
        "intrinsic_toman": intrinsic,
        "bubble_ratio": bubble_ratio,
        "side": "MATCHED_TRADE",
        "trade_form": "PHYSICAL",
        "quantity": (
            int(trade["quantity"]) if trade.get("quantity") is not None else None
        ),
        "usd_average_toman": usd_value,
        "usd_latest_toman": context["usd"].get("point_price"),
        "usd_reference_source": context["usd"].get("price_source"),
        "usd_is_usdt_proxy": bool(context["usd"].get("is_usdt_proxy")),
        "usdt_average_toman": context["usdt"]["average_price"],
        "usdt_latest_toman": context["usdt"].get("point_price"),
        "xauusd_average": xau_value,
        "xauusd_latest": context["xauusd"].get("point_price"),
        "generic_coin_average_toman": context["generic_coin"]["average_price"],
        "theoretical_melted_toman": theoretical_melted,
        "melted_vs_global_ratio": (
            float(melted["average_price"]) / theoretical_melted - 1
            if theoretical_melted
            else None
        ),
        "all_three_market_inputs_observed": (
            usd_value is not None and xau_value is not None
        ),
        "market_pressure_score": context["order_flow"]["estimator_score"],
        "market_regime": context["market_regime"].get("regime"),
        "market_regime_score": context["market_regime"].get("direction_score"),
        "market_regime_confidence": context["market_regime"].get("confidence"),
        "market_regime_volatility_percent": context["market_regime"].get(
            "volatility_percent"
        ),
        "cross_state": (
            "PROJECT_COMPETITIVE_PRICE_EXCLUDED"
            if excluded_competitive_price
            else "NOT_CROSSED"
        ),
        "lifecycle_training_weight": PROJECT_COMPLETED_TRADE_TRAINING_WEIGHT,
        "accepted": accepted,
        "rejection_reason": (
            None
            if accepted
            else (
                "PROJECT_OFFER_EXCLUDED_FROM_COMPETITIVE_PRICE"
                if excluded_competitive_price
                else "OUTSIDE_INTRINSIC_RATIO_GUARD"
            )
        ),
    }


def load_group_offer_labels(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not path.is_file():
        raise FileNotFoundError(f"Group offer file does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("Group offer JSON root must be an array")

    candidates: list[dict[str, Any]] = []
    stats = {
        "messages": len(payload),
        "offers_total": 0,
        "below_confidence_threshold": 0,
        "duplicates_removed": 0,
        "labels_retained": 0,
    }
    for message_index, message in enumerate(payload):
        if not isinstance(message, dict):
            continue
        event_time = parse_datetime(str(message.get("date")))
        source_text = str(message.get("text") or "")
        offers = message.get("extracted_offers") or []
        for offer_index, offer in enumerate(offers):
            if not isinstance(offer, dict):
                continue
            stats["offers_total"] += 1
            confidence = float(offer.get("confidence") or 0.0)
            if confidence < GROUP_MIN_CONFIDENCE:
                stats["below_confidence_threshold"] += 1
                continue
            settlement = str(offer.get("settlement") or "CASH")
            if settlement not in SETTLEMENT_CONFIG:
                settlement = "CASH"
            candidates.append(
                {
                    "group_offer_id": f"{message_index}:{offer_index}",
                    "message_index": message_index,
                    "offer_index": offer_index,
                    "event_time_utc": iso_utc(event_time),
                    "source_text": source_text,
                    "commodity_name": str(offer["commodity"]),
                    "settlement_type": settlement,
                    "side": str(offer.get("side") or "UNKNOWN"),
                    "trade_form": str(offer.get("trade_form") or "PHYSICAL"),
                    "project_price": int(offer["price"]),
                    "quantity": (
                        int(offer["quantity"])
                        if offer.get("quantity") is not None
                        else None
                    ),
                    "source_confidence": confidence,
                }
            )

    candidates.sort(key=lambda row: (row["event_time_utc"], row["group_offer_id"]))
    retained: list[dict[str, Any]] = []
    last_seen: dict[tuple[Any, ...], datetime] = {}
    for row in candidates:
        normalized_text = " ".join(str(row["source_text"]).split())
        key = (
            normalized_text,
            row["commodity_name"],
            row["settlement_type"],
            row["trade_form"],
            row["side"],
            row["project_price"],
            row["quantity"],
        )
        event_time = parse_datetime(str(row["event_time_utc"]))
        previous = last_seen.get(key)
        if previous is not None and (event_time - previous).total_seconds() <= 300:
            stats["duplicates_removed"] += 1
            continue
        last_seen[key] = event_time
        retained.append(row)
    stats["labels_retained"] = len(retained)
    return retained, stats


def load_group_confirmed_trade_labels(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(confirmed_trades)"
            ).fetchall()
        }
        # Version 2 of the conversation extractor deliberately retains some
        # confirmed records for audit while marking aggregates and ambiguous
        # multi-fill allocations as unsafe training labels.  Older databases
        # do not have this column, so preserve their previous behaviour.
        eligibility_filter = (
            "AND t.training_eligible = 1"
            if "training_eligible" in columns
            else ""
        )
        context_field = "t.context_json" if "context_json" in columns else "'{}'"
        quality_available = "trade_market_quality" in tables
        quality_join = (
            "LEFT JOIN trade_market_quality AS q ON q.trade_id=t.id"
            if quality_available
            else ""
        )
        quality_filter = (
            "AND COALESCE(q.training_eligible, 1)=1"
            if quality_available
            else ""
        )
        quality_fields = (
            "q.training_weight, q.market_regime, q.regime_score, "
            "q.regime_confidence, q.cross_state, q.exclusion_reason"
            if quality_available
            else (
                "NULL AS training_weight, NULL AS market_regime, "
                "NULL AS regime_score, NULL AS regime_confidence, "
                "NULL AS cross_state, NULL AS exclusion_reason"
            )
        )
        rows = connection.execute(
            f"""
            SELECT t.id, t.confirmation_message_id, t.event_time_utc, t.commodity,
                   t.price, t.quantity, t.side, t.settlement, t.trade_form,
                   t.confidence, {context_field} AS context_json, {quality_fields}
            FROM confirmed_trades AS t
            {quality_join}
            WHERE t.confidence >= 0.85
              {eligibility_filter}
              {quality_filter}
            ORDER BY t.event_time_utc, t.id
            """
        ).fetchall()
        manual_rows: list[sqlite3.Row] = []
        if {
            "manual_coin_offers",
            "manual_coin_confirmed_trades",
        }.issubset(tables):
            manual_rows = connection.execute(
                """
                SELECT t.id, t.occurred_at_utc AS event_time_utc, o.commodity,
                       t.price, t.quantity, o.side, o.settlement, o.trade_form,
                       o.description
                FROM manual_coin_confirmed_trades AS t
                JOIN manual_coin_offers AS o ON o.id=t.offer_id
                ORDER BY t.occurred_at_utc, t.id
                """
            ).fetchall()
    finally:
        connection.close()
    result = [
        {
            "group_offer_id": f"confirmed-trade:{int(row['id'])}",
            "message_index": None,
            "offer_index": None,
            "event_time_utc": str(row["event_time_utc"]),
            "source_text": str(row["context_json"]),
            "commodity_name": str(row["commodity"]),
            "settlement_type": str(row["settlement"]),
            "trade_form": str(row["trade_form"]),
            "side": str(row["side"]),
            "project_price": int(row["price"]),
            "quantity": int(row["quantity"]) if row["quantity"] is not None else None,
            "source_confidence": float(row["confidence"]),
            "training_base_weight": float(
                row["training_weight"]
                if row["training_weight"] is not None
                else CONFIRMED_TRADE_TRAINING_WEIGHT
            ),
            "market_regime": row["market_regime"],
            "market_regime_score": row["regime_score"],
            "market_regime_confidence": row["regime_confidence"],
            "cross_state": row["cross_state"] or "UNKNOWN",
            "source_kind": "TELEGRAM_GROUP_CONFIRMED_TRADE",
            "confirmation_message_id": int(row["confirmation_message_id"]),
        }
        for row in rows
    ]
    result.extend(
        {
            "group_offer_id": f"manual-confirmed-trade:{int(row['id'])}",
            "message_index": None,
            "offer_index": None,
            "event_time_utc": str(row["event_time_utc"]),
            "source_text": str(row["description"] or "OPERATOR_MANUAL"),
            "commodity_name": str(row["commodity"]),
            "settlement_type": str(row["settlement"]),
            "trade_form": str(row["trade_form"]),
            "side": str(row["side"]),
            "project_price": int(row["price"]),
            "quantity": int(row["quantity"]) if row["quantity"] is not None else None,
            "source_confidence": 1.0,
            "training_base_weight": CONFIRMED_TRADE_TRAINING_WEIGHT,
            "market_regime": None,
            "market_regime_score": None,
            "market_regime_confidence": None,
            "cross_state": "OPERATOR_MANUAL",
            "source_kind": "OPERATOR_MANUAL_CONFIRMED_TRADE",
            "confirmation_message_id": None,
        }
        for row in manual_rows
    )
    return result


def load_conversation_offer_labels(
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Conversation database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        quality_available = "offer_market_quality" in tables
        total = int(connection.execute("SELECT COUNT(*) FROM offers").fetchone()[0])
        quality_join = (
            "LEFT JOIN offer_market_quality AS q ON q.offer_id=o.id"
            if quality_available
            else ""
        )
        quality_filter = (
            "AND COALESCE(q.training_eligible, 1)=1"
            if quality_available
            else ""
        )
        quality_fields = (
            "q.historical_training_weight, q.cross_state, q.market_regime, "
            "q.regime_score, q.regime_confidence, "
            "q.regime_volatility_percent, q.exclusion_reason"
            if quality_available
            else (
                "NULL AS historical_training_weight, NULL AS cross_state, "
                "NULL AS market_regime, NULL AS regime_score, "
                "NULL AS regime_confidence, NULL AS regime_volatility_percent, "
                "NULL AS exclusion_reason"
            )
        )
        rows = connection.execute(
            f"""
            SELECT o.id, o.message_id, o.offer_index, o.commodity, o.price,
                   o.quantity, o.side, o.settlement, o.trade_form,
                   o.confidence, o.source_text, m.event_time_utc,
                   {quality_fields}
            FROM offers o
            JOIN messages m
              ON m.import_id = o.import_id AND m.message_id = o.message_id
            {quality_join}
            WHERE o.confidence >= ?
              {quality_filter}
            ORDER BY m.event_time_utc, o.id
            """,
            (GROUP_MIN_CONFIDENCE,),
        ).fetchall()
        manual_rows: list[sqlite3.Row] = []
        if {
            "manual_coin_offers",
            "manual_coin_confirmed_trades",
        }.issubset(tables):
            manual_rows = connection.execute(
                """
                SELECT o.id, o.occurred_at_utc AS event_time_utc, o.commodity,
                       o.price, o.quantity, o.side, o.settlement, o.trade_form,
                       o.is_live_at_entry, o.description
                FROM manual_coin_offers AS o
                WHERE NOT EXISTS (
                  SELECT 1 FROM manual_coin_confirmed_trades AS t
                  WHERE t.offer_id=o.id
                )
                ORDER BY o.occurred_at_utc, o.id
                """
            ).fetchall()
        total += len(manual_rows)
        if quality_available:
            quality_counts = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT COALESCE(exclusion_reason, 'ELIGIBLE'), COUNT(*) "
                    "FROM offer_market_quality GROUP BY exclusion_reason"
                )
            }
        else:
            quality_counts = {"QUALITY_TABLE_MISSING_LEGACY_POLICY": total}
    finally:
        connection.close()

    candidates = [
        {
            "group_offer_id": f"conversation-offer:{int(row['id'])}",
            "message_index": int(row["message_id"]),
            "offer_index": int(row["offer_index"]),
            "event_time_utc": str(row["event_time_utc"]),
            "source_text": str(row["source_text"]),
            "commodity_name": str(row["commodity"]),
            "settlement_type": (
                str(row["settlement"])
                if str(row["settlement"]) in SETTLEMENT_CONFIG
                else "CASH"
            ),
            "side": str(row["side"]),
            "trade_form": str(row["trade_form"]),
            "project_price": int(row["price"]),
            "quantity": int(row["quantity"]) if row["quantity"] is not None else None,
            "source_confidence": float(row["confidence"]),
            "lifecycle_training_weight": float(
                row["historical_training_weight"]
                if row["historical_training_weight"] is not None
                else GROUP_SOURCE_WEIGHT
            ),
            "cross_state": row["cross_state"] or "UNKNOWN",
            "market_regime": row["market_regime"],
            "market_regime_score": row["regime_score"],
            "market_regime_confidence": row["regime_confidence"],
            "market_regime_volatility_percent": row[
                "regime_volatility_percent"
            ],
            "source_kind": "TELEGRAM_GROUP_OFFER",
        }
        for row in rows
    ]
    now = datetime.now(timezone.utc)
    candidates.extend(
        {
            "group_offer_id": f"manual-offer:{int(row['id'])}",
            "message_index": None,
            "offer_index": None,
            "event_time_utc": str(row["event_time_utc"]),
            "source_text": (
                f"OPERATOR_MANUAL:{int(row['id'])} "
                f"{str(row['description'] or '').strip()}"
            ).strip(),
            "commodity_name": str(row["commodity"]),
            "settlement_type": str(row["settlement"]),
            "side": str(row["side"]),
            "trade_form": str(row["trade_form"]),
            "project_price": int(row["price"]),
            "quantity": int(row["quantity"]) if row["quantity"] is not None else None,
            "source_confidence": 1.0,
            "lifecycle_training_weight": (
                1.0
                if bool(row["is_live_at_entry"])
                and (now - parse_datetime(str(row["event_time_utc"]))).total_seconds()
                <= OFFER_LIVE_SECONDS
                else GROUP_SOURCE_WEIGHT
            ),
            "cross_state": "OPERATOR_MANUAL",
            "market_regime": None,
            "market_regime_score": None,
            "market_regime_confidence": None,
            "market_regime_volatility_percent": None,
            "source_kind": "OPERATOR_MANUAL_OFFER",
        }
        for row in manual_rows
    )
    retained: list[dict[str, Any]] = []
    last_seen: dict[tuple[Any, ...], datetime] = {}
    duplicates = 0
    for row in candidates:
        key = (
            " ".join(str(row["source_text"]).split()),
            row["commodity_name"],
            row["settlement_type"],
            row["trade_form"],
            row["side"],
            row["project_price"],
            row["quantity"],
        )
        event_time = parse_datetime(str(row["event_time_utc"]))
        previous = last_seen.get(key)
        if previous is not None and (event_time - previous).total_seconds() <= 300:
            duplicates += 1
            continue
        last_seen[key] = event_time
        retained.append(row)
    return retained, {
        "offers_total": total,
        "below_confidence_threshold": total - len(candidates),
        "duplicates_removed": duplicates,
        "labels_retained": len(retained),
        "market_quality_available": quality_available,
        "market_quality_counts": quality_counts,
        "manual_offers_total": len(manual_rows),
    }


def load_human_reviewed_trade_labels(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_decisions'"
        ).fetchone()
        if table is None:
            return []
        rows = connection.execute(
            """
            SELECT id, message_id, event_time_utc, commodity, price, quantity,
                   side, settlement, trade_form, source_context_json
            FROM review_decisions
            WHERE decision = 'ACCEPTED_TRADE'
            ORDER BY event_time_utc, id
            """
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "group_offer_id": f"human-reviewed-trade:{int(row['id'])}",
            "message_index": None,
            "offer_index": None,
            "event_time_utc": str(row["event_time_utc"]),
            "source_text": str(row["source_context_json"]),
            "commodity_name": str(row["commodity"]),
            "settlement_type": str(row["settlement"]),
            "trade_form": str(row["trade_form"]),
            "side": str(row["side"]),
            "project_price": int(row["price"]),
            "quantity": int(row["quantity"]) if row["quantity"] is not None else None,
            "source_confidence": 1.0,
            "training_base_weight": CONFIRMED_TRADE_TRAINING_WEIGHT,
            "market_regime": None,
            "market_regime_score": None,
            "market_regime_confidence": None,
            "cross_state": "HUMAN_REVIEWED",
            "source_kind": "TELEGRAM_GROUP_HUMAN_REVIEWED_TRADE",
            "confirmation_message_id": int(row["message_id"]),
        }
        for row in rows
    ]


def group_training_example(
    connection: sqlite3.Connection, label: dict[str, Any]
) -> dict[str, Any] | None:
    name = str(label["commodity_name"])
    settlement = str(label["settlement_type"])
    source_kind = str(label.get("source_kind") or "TELEGRAM_GROUP_OFFER")
    trusted_source = source_kind in TRUSTED_TRAINING_SOURCE_KINDS
    label_trade_form = str(label.get("trade_form") or "PHYSICAL")
    spec = COMMODITY_SPECS.get(name)
    if spec is None or settlement not in SETTLEMENT_CONFIG:
        return None
    event_time = parse_datetime(str(label["event_time_utc"]))
    context = historical_market_context(connection, settlement, event_time)
    melted = context["melted_gold"]
    if melted["status"] != "OBSERVED":
        return None
    if (
        melted.get("selection") == "TRAINING_ONLY_SAME_MINUTE_PAPER_REFERENCE"
        and label_trade_form != "PAPER"
        and not trusted_source
    ):
        # A paper reference may recover a rare confirmed physical trade, but
        # must not multiply the much noisier physical-offer proxy dataset.
        return None

    intrinsic = float(melted["average_price"]) * spec.coefficient
    observed = int(label["project_price"]) * PRICE_MULTIPLIER
    bubble_ratio = observed / intrinsic - 1
    accepted = -0.15 <= bubble_ratio <= 2.0
    confidence = float(label["source_confidence"])
    quantity = label.get("quantity")
    quantity_factor = min(1.20, 0.80 + math.sqrt(float(quantity or 10)) / 15.0)
    source_weight = (
        float(label.get("training_base_weight") or CONFIRMED_TRADE_TRAINING_WEIGHT)
        * confidence
        if trusted_source
        else float(label.get("lifecycle_training_weight") or GROUP_SOURCE_WEIGHT)
        * confidence
        * quantity_factor
    )
    source_weight *= float(melted.get("training_reference_weight", 1.0))
    usd_value = context["usd"]["average_price"]
    xau_value = context["xauusd"]["average_price"]
    theoretical_melted = (
        float(usd_value) * float(xau_value) / 9.572737
        if usd_value is not None and xau_value is not None
        else None
    )
    return {
        "source_kind": source_kind,
        "source_weight": source_weight,
        "source_confidence": confidence,
        "group_offer_id": label["group_offer_id"],
        "message_index": label.get("message_index"),
        "offer_index": label.get("offer_index"),
        "confirmation_message_id": label.get("confirmation_message_id"),
        "source_text": label["source_text"],
        "commodity_id": None,
        "commodity_name": name,
        "settlement_type": settlement,
        "event_time_utc": iso_utc(event_time),
        "project_price": int(label["project_price"]),
        "observed_price_toman": observed,
        "melted_average_toman": float(melted["average_price"]),
        "melted_latest_toman": melted.get("point_price"),
        "melted_samples": int(melted["sample_count"]),
        "melted_latest_by_type": context.get("melted_latest_by_type"),
        "melted_reference_selection": melted.get("selection"),
        "melted_reference_trade_form": melted.get("selected_trade_form"),
        "melted_reference_weight": float(
            melted.get("training_reference_weight", 1.0)
        ),
        "intrinsic_toman": intrinsic,
        "bubble_ratio": bubble_ratio,
        "side": label["side"],
        "trade_form": label_trade_form,
        "quantity": quantity,
        "usd_average_toman": usd_value,
        "usd_latest_toman": context["usd"].get("point_price"),
        "usd_reference_source": context["usd"].get("price_source"),
        "usd_is_usdt_proxy": bool(context["usd"].get("is_usdt_proxy")),
        "usdt_average_toman": context["usdt"]["average_price"],
        "usdt_latest_toman": context["usdt"].get("point_price"),
        "xauusd_average": xau_value,
        "xauusd_latest": context["xauusd"].get("point_price"),
        "generic_coin_average_toman": context["generic_coin"]["average_price"],
        "theoretical_melted_toman": theoretical_melted,
        "melted_vs_global_ratio": (
            float(melted["average_price"]) / theoretical_melted - 1
            if theoretical_melted
            else None
        ),
        "all_three_market_inputs_observed": (
            usd_value is not None and xau_value is not None
        ),
        "market_pressure_score": context["order_flow"]["estimator_score"],
        "market_regime": (
            label.get("market_regime")
            or context["market_regime"].get("regime")
        ),
        "market_regime_score": (
            label.get("market_regime_score")
            if label.get("market_regime_score") is not None
            else context["market_regime"].get("direction_score")
        ),
        "market_regime_confidence": (
            label.get("market_regime_confidence")
            if label.get("market_regime_confidence") is not None
            else context["market_regime"].get("confidence")
        ),
        "market_regime_volatility_percent": (
            label.get("market_regime_volatility_percent")
            if label.get("market_regime_volatility_percent") is not None
            else context["market_regime"].get("volatility_percent")
        ),
        "cross_state": label.get("cross_state") or "UNKNOWN",
        "lifecycle_training_weight": (
            float(label.get("training_base_weight") or CONFIRMED_TRADE_TRAINING_WEIGHT)
            if trusted_source
            else float(label.get("lifecycle_training_weight") or GROUP_SOURCE_WEIGHT)
        ),
        "accepted": accepted,
        "rejection_reason": None if accepted else "OUTSIDE_INTRINSIC_RATIO_GUARD",
    }


def balance_group_offer_sides(examples: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in examples:
        if row.get("accepted"):
            grouped.setdefault(
                (str(row["commodity_name"]), str(row["settlement_type"])), []
            ).append(row)
    for rows in grouped.values():
        side_totals = {
            side: sum(
                float(row["source_weight"]) for row in rows if row.get("side") == side
            )
            for side in ("BUY", "SELL")
        }
        if not all(side_totals.values()):
            continue
        target = (side_totals["BUY"] + side_totals["SELL"]) / 2.0
        for row in rows:
            side = row.get("side")
            if side in side_totals:
                row["source_weight"] = float(row["source_weight"]) * (
                    target / side_totals[str(side)]
                )


def calibration_rows(rows: Sequence[dict[str, Any]], source: str) -> dict[str, Any]:
    if not rows:
        return {
            "source": source,
            "sample_count": 0,
            "effective_sample_weight": 0.0,
            "bubble_ratio_median": None,
            "bubble_ratio_q10": None,
            "bubble_ratio_q90": None,
            "source_counts": {},
            "global_feature_sample_count": 0,
            "melted_vs_global_center": None,
            "melted_vs_global_slope": 0.0,
            "market_pressure_sample_count": 0,
            "market_pressure_center": None,
            "market_pressure_slope": 0.0,
        }
    values = [float(row["bubble_ratio"]) for row in rows]
    weights = [float(row.get("source_weight", 1.0)) for row in rows]
    side_totals = {
        side: sum(
            weight
            for row, weight in zip(rows, weights)
            if row.get("source_kind") == "TELEGRAM_GROUP_OFFER"
            and row.get("side") == side
        )
        for side in ("BUY", "SELL")
    }
    if all(side_totals.values()):
        target = (side_totals["BUY"] + side_totals["SELL"]) / 2.0
        weights = [
            (
                weight * target / side_totals[str(row.get("side"))]
                if row.get("source_kind") == "TELEGRAM_GROUP_OFFER"
                and row.get("side") in side_totals
                else weight
            )
            for row, weight in zip(rows, weights)
        ]
    # Coin bubbles change regime much faster than their intrinsic gold value.
    # A month of history is useful for learning structure, but must not move
    # today's calibration as much as yesterday's market.  Apply the decay
    # relative to the newest label available in this calibration slice.  This
    # also keeps walk-forward validation strictly free of future labels.
    dated_indexes: list[tuple[int, datetime]] = []
    for index, row in enumerate(rows):
        event_time = row.get("event_time_utc")
        if event_time:
            dated_indexes.append((index, parse_datetime(str(event_time))))
    if dated_indexes:
        latest_event = max(event_time for _, event_time in dated_indexes)
        decay_by_index = {
            index: 0.5
            ** (
                max(0.0, (latest_event - event_time).total_seconds())
                / 86_400.0
                / CALIBRATION_RECENCY_HALF_LIFE_DAYS
            )
            for index, event_time in dated_indexes
        }
        weights = [
            weight * decay_by_index.get(index, 1.0)
            for index, weight in enumerate(weights)
        ]
    # Per-record weights are not enough: thousands of expired offers must not
    # collectively outvote a smaller set of completed transactions.  When both
    # classes exist, confirmed trades retain at least 60% of effective label
    # mass.  If no trade exists, offers remain a usable low-confidence fallback.
    trusted_weight = sum(
        weight
        for row, weight in zip(rows, weights)
        if row.get("source_kind") != "TELEGRAM_GROUP_OFFER"
    )
    offer_weight = sum(
        weight
        for row, weight in zip(rows, weights)
        if row.get("source_kind") == "TELEGRAM_GROUP_OFFER"
    )
    offer_weight_scale = 1.0
    if trusted_weight > 0 and offer_weight > 0:
        maximum_offer_weight = (
            trusted_weight
            * MAX_OFFER_TRAINING_SHARE_WITH_TRADES
            / (1.0 - MAX_OFFER_TRAINING_SHARE_WITH_TRADES)
        )
        if offer_weight > maximum_offer_weight:
            offer_weight_scale = maximum_offer_weight / offer_weight
            weights = [
                weight * offer_weight_scale
                if row.get("source_kind") == "TELEGRAM_GROUP_OFFER"
                else weight
                for row, weight in zip(rows, weights)
            ]
    source_counts: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("source_kind") or "UNKNOWN")
        source_counts[kind] = source_counts.get(kind, 0) + 1
    median_ratio = weighted_quantile(values, weights, 0.50)
    feature_indexes = [
        index
        for index, row in enumerate(rows)
        if row.get("melted_vs_global_ratio") is not None
    ]
    feature_center = None
    feature_slope = 0.0
    if len(feature_indexes) >= 10:
        feature_weights = [weights[index] for index in feature_indexes]
        feature_values = [
            float(rows[index]["melted_vs_global_ratio"]) for index in feature_indexes
        ]
        total_feature_weight = sum(feature_weights)
        feature_center = sum(
            value * weight for value, weight in zip(feature_values, feature_weights)
        ) / total_feature_weight
        numerator = sum(
            weight
            * (feature - feature_center)
            * (float(rows[index]["bubble_ratio"]) - median_ratio)
            for index, feature, weight in zip(
                feature_indexes, feature_values, feature_weights
            )
        )
        denominator = sum(
            weight * (feature - feature_center) ** 2
            for feature, weight in zip(feature_values, feature_weights)
        )
        if denominator > 0:
            raw_slope = numerator / denominator
            shrinkage = len(feature_indexes) / (len(feature_indexes) + 50.0)
            feature_slope = max(-2.0, min(2.0, raw_slope * shrinkage))
    pressure_indexes = [
        index
        for index, row in enumerate(rows)
        if row.get("market_pressure_score") is not None
    ]
    pressure_center = None
    pressure_slope = 0.0
    if len(pressure_indexes) >= 10:
        pressure_weights = [weights[index] for index in pressure_indexes]
        pressure_values = [
            float(rows[index]["market_pressure_score"]) for index in pressure_indexes
        ]
        total_pressure_weight = sum(pressure_weights)
        pressure_center = sum(
            value * weight for value, weight in zip(pressure_values, pressure_weights)
        ) / total_pressure_weight
        numerator = sum(
            weight
            * (pressure - pressure_center)
            * (float(rows[index]["bubble_ratio"]) - median_ratio)
            for index, pressure, weight in zip(
                pressure_indexes, pressure_values, pressure_weights
            )
        )
        denominator = sum(
            weight * (pressure - pressure_center) ** 2
            for pressure, weight in zip(pressure_values, pressure_weights)
        )
        if denominator > 0:
            raw_slope = numerator / denominator
            shrinkage = len(pressure_indexes) / (len(pressure_indexes) + 75.0)
            # Domain direction is monotonic: buy pressure may increase bubble
            # and sell pressure may decrease it. Negative fitted slopes are not
            # allowed to invert that relationship on this small sample.
            pressure_slope = max(0.0, min(0.25, raw_slope * shrinkage))
    return {
        "source": source,
        "sample_count": len(rows),
        "effective_sample_weight": sum(weights),
        "trusted_trade_effective_weight_before_offer_cap": trusted_weight,
        "offer_effective_weight_before_cap": offer_weight,
        "offer_weight_scale_for_hierarchical_cap": offer_weight_scale,
        "maximum_offer_training_share_with_trades": (
            MAX_OFFER_TRAINING_SHARE_WITH_TRADES
        ),
        "recency_half_life_days": CALIBRATION_RECENCY_HALF_LIFE_DAYS,
        "bubble_ratio_median": median_ratio,
        "bubble_ratio_q10": weighted_quantile(values, weights, 0.10),
        "bubble_ratio_q90": weighted_quantile(values, weights, 0.90),
        "source_counts": source_counts,
        "global_feature_sample_count": len(feature_indexes),
        "melted_vs_global_center": feature_center,
        "melted_vs_global_slope": feature_slope,
        "market_pressure_sample_count": len(pressure_indexes),
        "market_pressure_center": pressure_center,
        "market_pressure_slope": pressure_slope,
    }


def confidence_for_samples(count: int, *, fallback: bool = False) -> str:
    if fallback or count == 0:
        return "VERY_LOW"
    if count >= 30:
        return "MEDIUM"
    if count >= 10:
        return "LOW"
    return "VERY_LOW"


def cross_validate(examples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    accepted = [row for row in examples if row["accepted"]]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in accepted:
        grouped.setdefault(str(row["commodity_name"]), []).append(row)
    errors: list[float] = []
    per_commodity: dict[str, dict[str, Any]] = {}
    for name, rows in grouped.items():
        rows.sort(key=lambda row: (row["event_time_utc"], row["trade_id"]))
        if len(rows) < 5:
            per_commodity[name] = {
                "status": "INSUFFICIENT_FOR_HOLDOUT",
                "sample_count": len(rows),
            }
            continue
        test_count = max(1, len(rows) // 5)
        train_rows = rows[:-test_count]
        test_rows = rows[-test_count:]
        median_ratio = statistics.median(row["bubble_ratio"] for row in train_rows)
        local_errors = []
        for row in test_rows:
            predicted = row["intrinsic_toman"] * (1 + median_ratio)
            error = abs(predicted - row["observed_price_toman"]) / row["observed_price_toman"]
            local_errors.append(error)
            errors.append(error)
        per_commodity[name] = {
            "status": "EVALUATED",
            "train_count": len(train_rows),
            "test_count": len(test_rows),
            "mape_percent": statistics.mean(local_errors) * 100,
            "median_ape_percent": statistics.median(local_errors) * 100,
        }
    return {
        "method": "chronological_last_20_percent_holdout",
        "evaluated_count": len(errors),
        "mape_percent": statistics.mean(errors) * 100 if errors else None,
        "median_ape_percent": statistics.median(errors) * 100 if errors else None,
        "per_commodity": per_commodity,
    }


def _training_ratio(
    rows: Sequence[dict[str, Any]],
    commodity: str,
    settlement: str,
    *,
    melted_vs_global_ratio: float | None = None,
    use_global_feature: bool = False,
    market_pressure_score: float | None = None,
    use_flow_feature: bool = False,
    trade_form: str = "PHYSICAL",
) -> float | None:
    accepted = [
        row
        for row in rows
        if row.get("accepted")
        and str(row["commodity_name"]) == commodity
        and str(row.get("trade_form") or "PHYSICAL") == trade_form
    ]
    direct = [row for row in accepted if str(row["settlement_type"]) == settlement]
    selected = direct if len(direct) >= 5 else accepted
    if selected:
        calibration = calibration_rows(selected, "VALIDATION")
        ratio = float(calibration["bubble_ratio_median"])
        center = calibration.get("melted_vs_global_center")
        if use_global_feature and melted_vs_global_ratio is not None and center is not None:
            ratio += float(calibration.get("melted_vs_global_slope") or 0.0) * (
                melted_vs_global_ratio - float(center)
            )
        pressure_center = calibration.get("market_pressure_center")
        if (
            use_flow_feature
            and market_pressure_score is not None
            and pressure_center is not None
        ):
            ratio += float(calibration.get("market_pressure_slope") or 0.0) * (
                market_pressure_score - float(pressure_center)
            )
        return ratio
    spec = COMMODITY_SPECS[commodity]
    return 0.0 if spec.low_date else None


def _predict_from_training_rows(
    row: dict[str, Any],
    training_rows: Sequence[dict[str, Any]],
    *,
    use_global_feature: bool = False,
    use_flow_feature: bool = False,
) -> float | None:
    name = str(row["commodity_name"])
    settlement = str(row["settlement_type"])
    global_ratio = (
        float(row["melted_vs_global_ratio"])
        if row.get("melted_vs_global_ratio") is not None
        else None
    )
    pressure_score = (
        float(row["market_pressure_score"])
        if row.get("market_pressure_score") is not None
        else None
    )
    trade_form = str(row.get("trade_form") or "PHYSICAL")
    ratio = _training_ratio(
        training_rows,
        name,
        settlement,
        melted_vs_global_ratio=global_ratio,
        use_global_feature=use_global_feature,
        market_pressure_score=pressure_score,
        use_flow_feature=use_flow_feature,
        trade_form=trade_form,
    )
    if ratio is None:
        return None
    intrinsic = float(row["intrinsic_toman"])
    generic = row.get("generic_coin_average_toman")
    if name == "امام" and generic is not None:
        return float(generic)

    spec = COMMODITY_SPECS[name]
    if not spec.low_date and generic is not None:
        imam_ratio = _training_ratio(
            training_rows,
            "امام",
            settlement,
            melted_vs_global_ratio=global_ratio,
            use_global_feature=use_global_feature,
            market_pressure_score=pressure_score,
            use_flow_feature=use_flow_feature,
            trade_form=trade_form,
        )
        if imam_ratio is not None:
            current_imam_intrinsic = (
                float(row["melted_average_toman"])
                * COMMODITY_SPECS["امام"].coefficient
            )
            current_imam_ratio = float(generic) / current_imam_intrinsic - 1
            ratio += current_imam_ratio - imam_ratio
    ratio = max(-0.15, min(2.0, ratio))
    return intrinsic * (1 + ratio)


def compare_with_group_holdout(
    holdout_rows: Sequence[dict[str, Any]],
    trade_rows: Sequence[dict[str, Any]],
    group_rows: Sequence[dict[str, Any]],
    *,
    holdout_source: str,
    use_flow_feature: bool,
) -> dict[str, Any]:
    accepted_holdout = [row for row in holdout_rows if row.get("accepted")]
    accepted_trades = [row for row in trade_rows if row.get("accepted")]
    accepted_group = [row for row in group_rows if row.get("accepted")]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in accepted_holdout:
        grouped.setdefault(str(row["commodity_name"]), []).append(row)

    all_baseline_errors: list[float] = []
    all_augmented_errors: list[float] = []
    per_commodity: dict[str, dict[str, Any]] = {}
    for name, rows in grouped.items():
        rows.sort(key=lambda row: str(row["event_time_utc"]))
        if len(rows) < 5:
            per_commodity[name] = {
                "status": "INSUFFICIENT_FOR_HOLDOUT",
                "sample_count": len(rows),
            }
            continue
        test_count = max(1, len(rows) // 5)
        test_rows = rows[-test_count:]
        cutoff = parse_datetime(str(test_rows[0]["event_time_utc"]))
        prior_trades = [
            row
            for row in accepted_trades
            if parse_datetime(str(row["event_time_utc"])) < cutoff
        ]
        prior_group = [
            row
            for row in accepted_group
            if parse_datetime(str(row["event_time_utc"])) < cutoff
        ]
        augmented_training = prior_trades + prior_group
        baseline_errors: list[float] = []
        augmented_errors: list[float] = []
        baseline_coverage = 0
        augmented_coverage = 0
        comparable = 0
        for row in test_rows:
            observed = float(row["observed_price_toman"])
            baseline = _predict_from_training_rows(
                row, prior_trades, use_global_feature=False
            )
            augmented = _predict_from_training_rows(
                row,
                augmented_training,
                use_global_feature=True,
                use_flow_feature=use_flow_feature,
            )
            baseline_coverage += baseline is not None
            augmented_coverage += augmented is not None
            if baseline is None or augmented is None:
                continue
            baseline_error = abs(baseline - observed) / observed
            augmented_error = abs(augmented - observed) / observed
            baseline_errors.append(baseline_error)
            augmented_errors.append(augmented_error)
            all_baseline_errors.append(baseline_error)
            all_augmented_errors.append(augmented_error)
            comparable += 1

        baseline_mape = (
            statistics.mean(baseline_errors) * 100 if baseline_errors else None
        )
        augmented_mape = (
            statistics.mean(augmented_errors) * 100 if augmented_errors else None
        )
        improvement = (
            (baseline_mape - augmented_mape) / baseline_mape * 100
            if baseline_mape and augmented_mape is not None
            else None
        )
        per_commodity[name] = {
            "status": "EVALUATED" if comparable else "NO_COMPARABLE_PREDICTIONS",
            "train_cutoff_utc": iso_utc(cutoff),
            "test_count": len(test_rows),
            "comparable_count": comparable,
            "baseline_coverage_count": baseline_coverage,
            "augmented_coverage_count": augmented_coverage,
            "baseline_mape_percent": baseline_mape,
            "augmented_mape_percent": augmented_mape,
            "relative_error_reduction_percent": improvement,
        }

    baseline_mape = (
        statistics.mean(all_baseline_errors) * 100 if all_baseline_errors else None
    )
    augmented_mape = (
        statistics.mean(all_augmented_errors) * 100 if all_augmented_errors else None
    )
    improvement = (
        (baseline_mape - augmented_mape) / baseline_mape * 100
        if baseline_mape and augmented_mape is not None
        else None
    )
    bootstrap_ci = None
    if len(all_baseline_errors) >= 5:
        generator = random.Random(20260720)
        reductions: list[float] = []
        for _ in range(4000):
            indexes = [
                generator.randrange(len(all_baseline_errors))
                for _ in all_baseline_errors
            ]
            base = statistics.mean(all_baseline_errors[index] for index in indexes)
            augmented = statistics.mean(
                all_augmented_errors[index] for index in indexes
            )
            if base > 0:
                reductions.append((base - augmented) / base * 100)
        if reductions:
            bootstrap_ci = [quantile(reductions, 0.025), quantile(reductions, 0.975)]
    return {
        "method": (
            "chronological_last_20_percent_holdout_no_future_labels"
            f"_flow_point_{'enabled' if use_flow_feature else 'gated_off'}"
        ),
        "holdout_source": holdout_source,
        "comparable_count": len(all_baseline_errors),
        "baseline_mape_percent": baseline_mape,
        "augmented_mape_percent": augmented_mape,
        "relative_error_reduction_percent": improvement,
        "relative_error_reduction_bootstrap_95pct": bootstrap_ci,
        "per_commodity": per_commodity,
    }


def telegram_walk_forward_validation(
    offer_rows: Sequence[dict[str, Any]],
    confirmed_trade_rows: Sequence[dict[str, Any]],
    *,
    use_flow_feature: bool,
) -> dict[str, Any]:
    """Measure the Telegram-only model without using future labels.

    Group offers are a market-price proxy rather than completed-trade truth, so
    this metric is deliberately reported separately from confirmed-trade
    validation.  The last 20 percent of each sufficiently populated commodity
    is held out; every prediction sees only labels strictly before its cutoff.
    """

    accepted_offers = [row for row in offer_rows if row.get("accepted")]
    accepted_confirmed = [
        row for row in confirmed_trade_rows if row.get("accepted")
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in accepted_offers:
        grouped.setdefault(str(row["commodity_name"]), []).append(row)

    errors: list[float] = []
    interval_hits: list[bool] = []
    interval_widths: list[float] = []
    per_commodity: dict[str, dict[str, Any]] = {}
    for name, rows in grouped.items():
        rows.sort(key=lambda row: str(row["event_time_utc"]))
        if len(rows) < 10:
            per_commodity[name] = {
                "status": "INSUFFICIENT_FOR_HOLDOUT",
                "sample_count": len(rows),
            }
            continue
        test_count = max(1, len(rows) // 5)
        test_rows = rows[-test_count:]
        cutoff = parse_datetime(str(test_rows[0]["event_time_utc"]))
        training = [
            row
            for row in (*accepted_confirmed, *accepted_offers)
            if parse_datetime(str(row["event_time_utc"])) < cutoff
        ]
        commodity_errors: list[float] = []
        commodity_hits: list[bool] = []
        commodity_widths: list[float] = []
        for row in test_rows:
            prediction = _predict_from_training_rows(
                row,
                training,
                use_global_feature=True,
                use_flow_feature=use_flow_feature,
            )
            if prediction is None:
                continue
            observed = float(row["observed_price_toman"])
            error = abs(prediction - observed) / observed
            errors.append(error)
            commodity_errors.append(error)

            same_market = [
                item
                for item in training
                if str(item["commodity_name"]) == name
                and str(item.get("trade_form") or "PHYSICAL")
                == str(row.get("trade_form") or "PHYSICAL")
                and str(item["settlement_type"])
                == str(row["settlement_type"])
            ]
            pooled = [
                item
                for item in training
                if str(item["commodity_name"]) == name
                and str(item.get("trade_form") or "PHYSICAL")
                == str(row.get("trade_form") or "PHYSICAL")
            ]
            selected = same_market if len(same_market) >= 5 else pooled
            if not selected:
                continue
            calibration = calibration_rows(selected, "VALIDATION_INTERVAL")
            q10 = calibration.get("bubble_ratio_q10")
            q90 = calibration.get("bubble_ratio_q90")
            if q10 is None or q90 is None:
                continue
            intrinsic = float(row["intrinsic_toman"])
            lower = intrinsic * (1 + float(q10))
            upper = intrinsic * (1 + float(q90))
            hit = lower <= observed <= upper
            width = (upper - lower) / observed
            interval_hits.append(hit)
            interval_widths.append(width)
            commodity_hits.append(hit)
            commodity_widths.append(width)

        per_commodity[name] = {
            "status": "EVALUATED" if commodity_errors else "NO_PREDICTIONS",
            "train_cutoff_utc": iso_utc(cutoff),
            "training_count": len(training),
            "test_count": len(test_rows),
            "prediction_count": len(commodity_errors),
            "mape_percent": (
                statistics.mean(commodity_errors) * 100
                if commodity_errors
                else None
            ),
            "interval_80_coverage_percent": (
                statistics.mean(commodity_hits) * 100
                if commodity_hits
                else None
            ),
            "mean_interval_width_percent": (
                statistics.mean(commodity_widths) * 100
                if commodity_widths
                else None
            ),
        }

    return {
        "method": (
            "chronological_last_20_percent_telegram_offer_proxy_"
            "strict_no_future_labels"
        ),
        "warning": (
            "Offer prices are proxy labels; confirmed trades remain the preferred "
            "promotion metric when their holdout is large enough."
        ),
        "prediction_count": len(errors),
        "mape_percent": statistics.mean(errors) * 100 if errors else None,
        "interval_80_coverage_percent": (
            statistics.mean(interval_hits) * 100 if interval_hits else None
        ),
        "mean_interval_width_percent": (
            statistics.mean(interval_widths) * 100 if interval_widths else None
        ),
        "per_commodity": per_commodity,
    }


def finite_sample_upper_quantile(
    values: Sequence[float], coverage: float
) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, math.ceil((len(ordered) + 1) * coverage) - 1)
    return ordered[max(0, index)]


def telegram_conformal_calibration(
    offer_rows: Sequence[dict[str, Any]],
    confirmed_trade_rows: Sequence[dict[str, Any]],
    *,
    target_coverage: float = 0.80,
    use_flow_feature: bool = False,
) -> dict[str, Any]:
    """Create a conservative tolerance floor with a strict 60/20/20 split."""

    offers = sorted(
        (row for row in offer_rows if row.get("accepted")),
        key=lambda row: str(row["event_time_utc"]),
    )
    trusted = [row for row in confirmed_trade_rows if row.get("accepted")]
    timestamps = sorted({str(row["event_time_utc"]) for row in offers})
    if len(timestamps) < 10:
        return {"status": "INSUFFICIENT_DATA", "sample_count": len(offers)}
    calibration_cutoff = timestamps[int(len(timestamps) * 0.60)]
    test_cutoff = timestamps[int(len(timestamps) * 0.80)]
    train_rows = [
        row
        for row in (*trusted, *offers)
        if str(row["event_time_utc"]) < calibration_cutoff
    ]
    calibration_rows_set = [
        row
        for row in offers
        if calibration_cutoff <= str(row["event_time_utc"]) < test_cutoff
    ]
    test_rows = [
        row for row in offers if str(row["event_time_utc"]) >= test_cutoff
    ]

    calibration_errors: list[float] = []
    errors_by_commodity: dict[str, list[float]] = {}
    for row in calibration_rows_set:
        prediction = _predict_from_training_rows(
            row,
            train_rows,
            use_global_feature=True,
            use_flow_feature=use_flow_feature,
        )
        if prediction is None:
            continue
        observed = float(row["observed_price_toman"])
        error = abs(prediction - observed) / observed
        calibration_errors.append(error)
        errors_by_commodity.setdefault(str(row["commodity_name"]), []).append(error)
    global_qhat = finite_sample_upper_quantile(calibration_errors, target_coverage)
    if global_qhat is None:
        return {"status": "INSUFFICIENT_CALIBRATION_PREDICTIONS"}
    per_commodity_qhat = {}
    for name, values in errors_by_commodity.items():
        local_qhat = (
            finite_sample_upper_quantile(values, target_coverage)
            if len(values) >= 10
            else None
        )
        # Never let a locally calm series make the interval narrower than the
        # market-wide uncertainty floor. Sparse/volatile products may widen it.
        per_commodity_qhat[name] = max(global_qhat, float(local_qhat or 0.0))

    test_errors: list[float] = []
    interval_hits: list[bool] = []
    widths: list[float] = []
    per_commodity_test: dict[str, dict[str, Any]] = {}
    grouped_test: dict[str, list[dict[str, Any]]] = {}
    for row in test_rows:
        grouped_test.setdefault(str(row["commodity_name"]), []).append(row)
    for name, rows in grouped_test.items():
        local_errors: list[float] = []
        local_hits: list[bool] = []
        qhat = float(per_commodity_qhat.get(name, global_qhat))
        for row in rows:
            prediction = _predict_from_training_rows(
                row,
                train_rows,
                use_global_feature=True,
                use_flow_feature=use_flow_feature,
            )
            if prediction is None:
                continue
            observed = float(row["observed_price_toman"])
            error = abs(prediction - observed) / observed
            hit = prediction * (1 - qhat) <= observed <= prediction * (1 + qhat)
            test_errors.append(error)
            interval_hits.append(hit)
            widths.append(2 * qhat)
            local_errors.append(error)
            local_hits.append(hit)
        per_commodity_test[name] = {
            "test_count": len(rows),
            "prediction_count": len(local_errors),
            "relative_error_qhat": qhat,
            "mape_percent": (
                statistics.mean(local_errors) * 100 if local_errors else None
            ),
            "coverage_percent": (
                statistics.mean(local_hits) * 100 if local_hits else None
            ),
        }

    return {
        "status": "CALIBRATED",
        "method": "SPLIT_CONFORMAL_ABSOLUTE_RELATIVE_ERROR_60_20_20",
        "target_coverage_percent": target_coverage * 100,
        "train_count": len(train_rows),
        "calibration_count": len(calibration_rows_set),
        "calibration_prediction_count": len(calibration_errors),
        "test_count": len(test_rows),
        "test_prediction_count": len(test_errors),
        "calibration_start_utc": calibration_cutoff,
        "test_start_utc": test_cutoff,
        "global_relative_error_qhat": global_qhat,
        "per_commodity_relative_error_qhat": per_commodity_qhat,
        "test_mape_percent": (
            statistics.mean(test_errors) * 100 if test_errors else None
        ),
        "test_coverage_percent": (
            statistics.mean(interval_hits) * 100 if interval_hits else None
        ),
        "test_mean_interval_width_percent": (
            statistics.mean(widths) * 100 if widths else None
        ),
        "per_commodity_test": per_commodity_test,
    }


def compare_order_flow_ablation(
    holdout_rows: Sequence[dict[str, Any]],
    trade_rows: Sequence[dict[str, Any]],
    group_rows: Sequence[dict[str, Any]],
    *,
    holdout_source: str,
) -> dict[str, Any]:
    accepted_holdout = [row for row in holdout_rows if row.get("accepted")]
    accepted_training = [
        row for row in (*trade_rows, *group_rows) if row.get("accepted")
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in accepted_holdout:
        grouped.setdefault(str(row["commodity_name"]), []).append(row)
    without_errors: list[float] = []
    with_errors: list[float] = []
    per_commodity: dict[str, Any] = {}
    for name, rows in grouped.items():
        rows.sort(key=lambda row: str(row["event_time_utc"]))
        if len(rows) < 5:
            per_commodity[name] = {
                "status": "INSUFFICIENT_FOR_HOLDOUT",
                "sample_count": len(rows),
            }
            continue
        test_count = max(1, len(rows) // 5)
        test_rows = rows[-test_count:]
        cutoff = parse_datetime(str(test_rows[0]["event_time_utc"]))
        training = [
            row
            for row in accepted_training
            if parse_datetime(str(row["event_time_utc"])) < cutoff
        ]
        local_without: list[float] = []
        local_with: list[float] = []
        for row in test_rows:
            without = _predict_from_training_rows(
                row,
                training,
                use_global_feature=True,
                use_flow_feature=False,
            )
            with_flow = _predict_from_training_rows(
                row,
                training,
                use_global_feature=True,
                use_flow_feature=True,
            )
            if without is None or with_flow is None:
                continue
            observed = float(row["observed_price_toman"])
            error_without = abs(without - observed) / observed
            error_with = abs(with_flow - observed) / observed
            local_without.append(error_without)
            local_with.append(error_with)
            without_errors.append(error_without)
            with_errors.append(error_with)
        without_mape = statistics.mean(local_without) * 100 if local_without else None
        with_mape = statistics.mean(local_with) * 100 if local_with else None
        per_commodity[name] = {
            "status": "EVALUATED" if local_without else "NO_COMPARABLE_PREDICTIONS",
            "comparable_count": len(local_without),
            "without_flow_mape_percent": without_mape,
            "with_flow_mape_percent": with_mape,
            "relative_error_reduction_percent": (
                (without_mape - with_mape) / without_mape * 100
                if without_mape and with_mape is not None
                else None
            ),
        }
    without_mape = statistics.mean(without_errors) * 100 if without_errors else None
    with_mape = statistics.mean(with_errors) * 100 if with_errors else None
    return {
        "method": "chronological_holdout_same_training_flow_feature_ablation",
        "holdout_source": holdout_source,
        "comparable_count": len(without_errors),
        "without_flow_mape_percent": without_mape,
        "with_flow_mape_percent": with_mape,
        "relative_error_reduction_percent": (
            (without_mape - with_mape) / without_mape * 100
            if without_mape and with_mape is not None
            else None
        ),
        "per_commodity": per_commodity,
    }


def choose_market_calibration(
    *,
    direct_rows: Sequence[dict[str, Any]],
    pooled_rows: Sequence[dict[str, Any]],
    spec: CommoditySpec,
    trade_form: str,
) -> dict[str, Any]:
    pooled = calibration_rows(
        pooled_rows,
        f"WEIGHTED_TRUSTED_TRADES_PLUS_GROUP_OFFERS_{trade_form}_POOLED",
    )
    if len(direct_rows) >= 5:
        selected = calibration_rows(
            direct_rows,
            f"WEIGHTED_TRUSTED_TRADES_PLUS_GROUP_OFFERS_{trade_form}_SETTLEMENT",
        )
        fallback = False
    elif pooled["sample_count"]:
        selected = dict(pooled)
        selected["source"] = f"WEIGHTED_{trade_form}_POOLED_FALLBACK"
        fallback = True
    elif spec.low_date:
        selected = calibration_rows([], "LOW_DATE_ZERO_BUBBLE_DOMAIN_FALLBACK")
        selected["bubble_ratio_median"] = 0.0
        fallback = True
    else:
        selected = calibration_rows([], "NO_TRAINING_LABEL")
        fallback = True
    selected["confidence"] = confidence_for_samples(
        int(selected["sample_count"]), fallback=fallback
    )
    selected["direct_settlement_sample_count"] = len(direct_rows)
    selected["trade_form"] = trade_form
    return selected


def train_model(
    repo: Path,
    market_db: Path,
    group_offers: Path,
    conversation_db: Path,
    review_decisions_db: Path,
    *,
    project_labels_enabled: bool = False,
) -> dict[str, Any]:
    if project_labels_enabled:
        snapshot = fetch_project_snapshot(repo)
        commodities = list(snapshot.get("commodities") or [])
        trades = list(snapshot.get("trades") or [])
    else:
        commodities = list(STATIC_COMMODITIES)
        trades = []
    group_labels, group_import_stats = load_conversation_offer_labels(
        conversation_db
    )
    confirmed_group_labels = load_group_confirmed_trade_labels(conversation_db)
    reviewed_group_labels = load_human_reviewed_trade_labels(review_decisions_db)
    with connect_market_db(market_db) as connection:
        trade_examples = [
            example for trade in trades if (example := training_example(connection, trade))
        ]
        group_examples = [
            example
            for label in group_labels
            if (example := group_training_example(connection, label))
        ]
        confirmed_group_examples = [
            example
            for label in confirmed_group_labels
            if (example := group_training_example(connection, label))
        ]
        reviewed_group_examples = [
            example
            for label in reviewed_group_labels
            if (example := group_training_example(connection, label))
        ]
    trusted_trade_examples = (
        trade_examples + confirmed_group_examples + reviewed_group_examples
    )
    examples = trusted_trade_examples + group_examples

    accepted = [row for row in examples if row["accepted"]]
    accepted_trades = [row for row in trade_examples if row["accepted"]]
    accepted_group = [row for row in group_examples if row["accepted"]]
    accepted_confirmed_group = [
        row for row in confirmed_group_examples if row["accepted"]
    ]
    accepted_reviewed_group = [
        row for row in reviewed_group_examples if row["accepted"]
    ]
    melted_reference_counts: dict[str, int] = {}
    for row in accepted:
        selection = str(row.get("melted_reference_selection") or "UNKNOWN")
        melted_reference_counts[selection] = (
            melted_reference_counts.get(selection, 0) + 1
        )
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_name_form: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_name_market: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    rejected_by_name: dict[str, int] = {}
    for row in examples:
        name = str(row["commodity_name"])
        if not row["accepted"]:
            rejected_by_name[name] = rejected_by_name.get(name, 0) + 1
            continue
        settlement = str(row["settlement_type"])
        trade_form = str(row.get("trade_form") or "PHYSICAL")
        by_name.setdefault(name, []).append(row)
        by_name_form.setdefault((name, trade_form), []).append(row)
        by_name_market.setdefault((name, settlement, trade_form), []).append(row)

    model_commodities: list[dict[str, Any]] = []
    for commodity in commodities:
        name = str(commodity["name"])
        spec = COMMODITY_SPECS.get(name)
        if spec is None:
            model_commodities.append(
                {
                    "id": int(commodity["id"]),
                    "name": name,
                    "status": "UNSUPPORTED_COMMODITY",
                }
            )
            continue
        settlements: dict[str, Any] = {}
        market_forms: dict[str, dict[str, Any]] = {}
        for settlement in SETTLEMENT_CONFIG:
            market_forms[settlement] = {}
            for trade_form in ("PHYSICAL", "PAPER"):
                selected = choose_market_calibration(
                    direct_rows=by_name_market.get(
                        (name, settlement, trade_form), []
                    ),
                    pooled_rows=by_name_form.get((name, trade_form), []),
                    spec=spec,
                    trade_form=trade_form,
                )
                market_forms[settlement][trade_form] = selected
            # Project offers are physical today. Keep this alias for the live
            # consumer while persisting paper calibration separately for the
            # future paper-market feature.
            settlements[settlement] = market_forms[settlement]["PHYSICAL"]

        model_commodities.append(
            {
                "id": int(commodity["id"]),
                "name": name,
                "status": "SUPPORTED",
                "coefficient": spec.coefficient,
                "low_date": spec.low_date,
                "accepted_training_samples": len(by_name.get(name, [])),
                "rejected_training_samples": rejected_by_name.get(name, 0),
                "settlements": settlements,
                "market_forms": market_forms,
            }
        )

    group_flow_ablation = compare_order_flow_ablation(
        group_examples,
        trusted_trade_examples,
        group_examples,
        holdout_source="TELEGRAM_GROUP_OFFER",
    )
    trade_flow_ablation = compare_order_flow_ablation(
        trusted_trade_examples,
        trusted_trade_examples,
        group_examples,
        holdout_source="TRUSTED_CONFIRMED_TRADE",
    )
    trade_flow_improvement = trade_flow_ablation.get(
        "relative_error_reduction_percent"
    )
    flow_point_adjustment_enabled = bool(
        int(trade_flow_ablation.get("comparable_count") or 0) >= 50
        and trade_flow_improvement is not None
        and float(trade_flow_improvement) > 0.0
    )
    conformal_tolerance = telegram_conformal_calibration(
        group_examples,
        confirmed_group_examples + reviewed_group_examples,
        use_flow_feature=flow_point_adjustment_enabled,
    )

    return {
        "schema_version": 5,
        "model_kind": "ROBUST_HYBRID_INTRINSIC_PLUS_EMPIRICAL_BUBBLE",
        "is_llm": False,
        "trained_at_utc": iso_utc(datetime.now(timezone.utc)),
        "market_database": str(market_db.resolve()),
        "group_offer_source": "CONVERSATION_DATABASE_OFFERS_TABLE",
        "group_offer_file": None,
        "legacy_group_offer_file_ignored": str(group_offers.resolve()),
        "conversation_database": str(conversation_db.resolve()),
        "review_decisions_database": str(review_decisions_db.resolve()),
        "project_labels_enabled": project_labels_enabled,
        "training_data_policy": {
            "telegram_group_offers": (
                "ACTIVE_LIVE_RANGE_FULL_WEIGHT_THEN_TIMEOUT_HISTORICAL_ONE_THIRD"
            ),
            "telegram_group_confirmed_trades": "ENABLED_HIGH_WEIGHT_LABEL",
            "project_database": (
                "ENABLED_EXPLICIT_OVERRIDE"
                if project_labels_enabled
                else "DISABLED_EXPERIMENTAL_ACTIVITY"
            ),
            "semantic_llm_outputs": "REVIEW_REQUIRED_NEVER_AUTO_TRAIN",
        },
        "market_form_policy_version": MARKET_FORM_POLICY_VERSION,
        "calibration_recency_policy": {
            "method": "EXPONENTIAL_EVENT_TIME_DECAY",
            "half_life_days": CALIBRATION_RECENCY_HALF_LIFE_DAYS,
            "reference": "LATEST_LABEL_IN_CALIBRATION_SLICE",
        },
        "offer_lifecycle_policy": {
            "live_ttl_seconds": OFFER_LIVE_SECONDS,
            "active_live_range_weight": 1.0,
            "early_expired_weight": 0.5,
            "timeout_historical_training_weight": GROUP_SOURCE_WEIGHT,
            "post_ttl_live_range_weight": 0.0,
            "post_ttl_role": "HISTORICAL_BUBBLE_TRAINING_ONLY",
        },
        "training_weight_hierarchy": {
            "project_completed_trade": PROJECT_COMPLETED_TRADE_TRAINING_WEIGHT,
            "confirmed_group_trade": CONFIRMED_TRADE_TRAINING_WEIGHT,
            "timed_out_offer": GROUP_SOURCE_WEIGHT,
            "maximum_offer_share_when_trades_exist": (
                MAX_OFFER_TRAINING_SHARE_WITH_TRADES
            ),
        },
        "market_regime_policy": {
            "states": ["RANGE", "UP", "DOWN", "SHOCK", "UNKNOWN"],
            "inputs": [
                "MELTED_GOLD",
                "USD_HERAT",
                "USDT_IRT",
                "XAUUSD",
                "IME_GOLD_BAR",
            ],
            "coin_offers_used_as_regime_input": False,
            "usdt_nominal_weight": 0.75,
            "ime_nominal_weight": 0.25,
            "crossed_range_offer_and_linked_trade": "EXCLUDED",
            "normal_market_outlier_rule": (
                "SELL_BELOW_LOWEST_ACTIVE_BUY_OR_BUY_ABOVE_HIGHEST_ACTIVE_SELL"
            ),
            "opposite_book_reference": (
                "OUTER_EXTREME_OF_ACTIVE_OPPOSITE_BOOK"
            ),
        },
        "market_form_policy": {
            "usd_physical": "EXPLICIT_NAGHDI_OR_NAGHD_ONLY",
            "melted_physical": "EXPLICIT_NAGHDI_OR_RASMI_ONLY",
            "herat_today_tomorrow": "PAPER",
            "other_melted_quotes": "PAPER",
        },
        "project_price_multiplier_to_toman": PRICE_MULTIPLIER,
        "training_trade_count_total": len(trades),
        "training_trade_count_with_market_context": len(trade_examples),
        "training_trade_count_accepted": len(accepted_trades),
        "training_trade_count_rejected": len(trade_examples) - len(accepted_trades),
        "group_confirmed_trade_count_with_market_context": len(
            confirmed_group_examples
        ),
        "group_confirmed_trade_count_accepted": len(accepted_confirmed_group),
        "group_confirmed_trade_count_rejected": len(confirmed_group_examples)
        - len(accepted_confirmed_group),
        "group_human_reviewed_trade_count_with_market_context": len(
            reviewed_group_examples
        ),
        "group_human_reviewed_trade_count_accepted": len(accepted_reviewed_group),
        "group_human_reviewed_trade_count_rejected": len(reviewed_group_examples)
        - len(accepted_reviewed_group),
        "group_offer_import": group_import_stats,
        "group_offer_count_with_market_context": len(group_examples),
        "group_offer_count_accepted": len(accepted_group),
        "group_offer_count_rejected": len(group_examples) - len(accepted_group),
        "combined_training_count_accepted": len(accepted),
        "combined_training_effective_weight": sum(
            float(row.get("source_weight", 1.0)) for row in accepted
        ),
        "melted_training_reference_counts": melted_reference_counts,
        "combined_all_three_market_inputs_observed": sum(
            bool(row.get("all_three_market_inputs_observed")) for row in accepted
        ),
        "training_window_seconds": WINDOW_SECONDS,
        "historical_usd_feature_window_seconds": 600,
        "order_flow_window_seconds": FLOW_WINDOW_SECONDS,
        "paper_flow_weight": PAPER_FLOW_WEIGHT,
        "unknown_settlement_paper_flow_weight": UNKNOWN_SETTLEMENT_PAPER_FLOW_WEIGHT,
        "flow_tolerance_expansion_max": FLOW_TOLERANCE_EXPANSION_MAX,
        "order_flow_point_adjustment_enabled": flow_point_adjustment_enabled,
        "order_flow_point_adjustment_gate": {
            "minimum_completed_trade_holdout": 50,
            "requires_positive_error_reduction": True,
            "current_completed_trade_holdout": trade_flow_ablation.get(
                "comparable_count"
            ),
            "current_relative_error_reduction_percent": trade_flow_improvement,
        },
        "conformal_tolerance": conformal_tolerance,
        "group_offer_source_weight": GROUP_SOURCE_WEIGHT,
        "confirmed_trade_source_weight": CONFIRMED_TRADE_TRAINING_WEIGHT,
        "project_completed_trade_source_weight": (
            PROJECT_COMPLETED_TRADE_TRAINING_WEIGHT
        ),
        "maximum_offer_training_share_with_trades": (
            MAX_OFFER_TRAINING_SHARE_WITH_TRADES
        ),
        "group_min_extraction_confidence": GROUP_MIN_CONFIDENCE,
        "outlier_guard_bubble_ratio": [-0.15, 2.0],
        "settlement_input_policy": SETTLEMENT_CONFIG,
        "commodities": model_commodities,
        "validation": {
            "project_completed_trade_baseline": (
                cross_validate(trade_examples)
                if project_labels_enabled
                else {
                    "status": "DISABLED_EXPERIMENTAL_ACTIVITY",
                    "sample_count": 0,
                }
            ),
            "telegram_only_walk_forward": telegram_walk_forward_validation(
                group_examples,
                confirmed_group_examples,
                use_flow_feature=flow_point_adjustment_enabled,
            ),
            "group_offer_holdout_comparison": compare_with_group_holdout(
                group_examples,
                trusted_trade_examples,
                group_examples,
                holdout_source="TELEGRAM_GROUP_OFFER",
                use_flow_feature=flow_point_adjustment_enabled,
            ),
            "trusted_trade_holdout_comparison": compare_with_group_holdout(
                trusted_trade_examples,
                trusted_trade_examples,
                group_examples,
                holdout_source="TRUSTED_CONFIRMED_TRADE",
                use_flow_feature=flow_point_adjustment_enabled,
            ),
            "group_offer_order_flow_ablation": group_flow_ablation,
            "completed_trade_order_flow_ablation": trade_flow_ablation,
        },
        "training_examples": examples,
    }


def load_model(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _observed_inputs_uncached(
    connection: sqlite3.Connection, settlement: str, end: datetime
) -> dict[str, dict[str, Any]]:
    # All values below originate from parser/normalizer output tables.  Raw
    # Telegram text is never consumed by inference.  The 30-second summaries
    # and the latest real points are intentionally both exposed to the model.
    average_seconds = MARKET_AVERAGE_SECONDS
    return {
        "melted_gold": select_melted_average(
            connection, settlement, end, seconds=average_seconds
        ),
        "generic_coin": select_generic_coin_average(
            connection, settlement, end, seconds=average_seconds
        ),
        "xauusd": select_live_xauusd_average(
            connection, end, seconds=average_seconds
        ),
        "usd": select_effective_usd_average(
            connection, settlement, end, seconds=average_seconds
        ),
        "usdt": select_usdt_average(
            connection, end, seconds=average_seconds
        ),
        "melted_latest_by_type": latest_melted_events_by_type(
            connection,
            end=end,
            seconds=average_seconds,
            bucket_seconds=MELTED_LIVE_BUCKET_SECONDS,
        ),
        "order_flow": market_order_flow(connection, settlement, end),
        "market_regime": detect_market_regime(connection, end, settlement),
    }


def observed_inputs(
    connection: sqlite3.Connection, settlement: str, end: datetime
) -> dict[str, dict[str, Any]]:
    """Read model-independent market inputs once per DB/settlement/timestamp."""

    source = _market_connection_identity(connection)
    if source is None:
        return _observed_inputs_uncached(connection, settlement, end)
    key = ("OBSERVED_INPUTS", source, settlement, _snapshot_timestamp(end))
    cached = _snapshot_cache_get(key)
    if cached is not None:
        return cached
    result = _observed_inputs_uncached(connection, settlement, end)
    return _snapshot_cache_put(key, result)


def live_point_value(summary: dict[str, Any]) -> float | None:
    """Return the newest real parsed value, with in-window mean fallback."""
    point = summary.get("point_price")
    if point is not None:
        return float(point)
    average = summary.get("average_price")
    return float(average) if average is not None else None


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


# Same melted coefficient ⇒ same intrinsic family.  Low-date bands must not
# climb into the non-low-date sibling range (e.g. بهار vs امام, نیم تاریخ پایین
# vs نیم بهار).
LOW_DATE_FAMILY_SEPARATION_RELATIVE = 0.0015  # 0.15% of sibling point
LOW_DATE_FAMILY_SEPARATION_MIN_TOMAN = 50_000


def low_date_family_sibling_name(commodity_name: str) -> str | None:
    spec = COMMODITY_SPECS.get(commodity_name)
    if spec is None or not spec.low_date:
        return None
    for name, other in COMMODITY_SPECS.items():
        if other.low_date:
            continue
        if abs(float(other.coefficient) - float(spec.coefficient)) <= 1e-12:
            return name
    return None


def enforce_cash_tomorrow_term_structure(
    settlements: dict[str, Any],
) -> list[dict[str, Any]]:
    """Prevent unsupported cash/tomorrow inversions after calibration.

    TOMORROW is often ``CASH × empirical ratio`` (ratio ≳ 1).  Online residual
    can then pull only the TOMORROW book (CASH still warming up), which flips
    the term structure.  A current, observed group book is stronger evidence
    than this structural rule: never lift an observed TOMORROW price merely
    because an inferred CASH estimate is stale.  In that case cap the inferred
    cash estimate at the observed tomorrow book instead.
    """

    audits: list[dict[str, Any]] = []
    cash_rates = {
        str(row.get("commodity_name")): row
        for row in ((settlements.get("CASH") or {}).get("rates") or [])
        if isinstance(row, dict) and row.get("commodity_name")
    }
    tomorrow_rates = (settlements.get("TOMORROW") or {}).get("rates") or []
    for rate in tomorrow_rates:
        if not isinstance(rate, dict):
            continue
        if str(rate.get("status")) not in {"ESTIMATED", "OBSERVED_ANCHOR"}:
            continue
        name = str(rate.get("commodity_name") or "")
        cash = cash_rates.get(name)
        if cash is None or str(cash.get("status")) not in {"ESTIMATED", "OBSERVED_ANCHOR"}:
            continue
        cash_price = cash.get("estimated_price_toman")
        tom_price = rate.get("estimated_price_toman")
        if cash_price is None or tom_price is None:
            continue
        cash_price_f = float(cash_price)
        tom_price_f = float(tom_price)
        cash_anchor = cash.get("group_offer_anchor")
        tomorrow_anchor = rate.get("group_offer_anchor")
        cash_has_live_anchor = (
            isinstance(cash_anchor, dict)
            and str(cash_anchor.get("status") or "") == "OBSERVED"
        )
        tomorrow_has_live_anchor = (
            isinstance(tomorrow_anchor, dict)
            and str(tomorrow_anchor.get("status") or "") == "OBSERVED"
        )
        # A live tomorrow book is authoritative.  If cash is only inferred,
        # move the inferred cash centre down to that book rather than lifting
        # the live tomorrow centre upward.  With two conflicting live books,
        # retain both observations for the caller/operator to inspect.
        if tomorrow_has_live_anchor:
            if not cash_has_live_anchor and cash_price_f > tom_price_f:
                cap = int(round(tom_price_f / 50_000.0) * 50_000)
                cash["_pre_term_structure_estimated_price_toman"] = cash_price_f
                cash["estimated_price_toman"] = cap
                cash["estimated_project_price"] = int(round(cap / PRICE_MULTIPLIER))
                if cash.get("llm_value") is not None:
                    cash["llm_value"] = cash["estimated_project_price"]
                tolerance = cash.get("tolerance")
                if isinstance(tolerance, dict):
                    lower = tolerance.get("lower_price_toman")
                    upper = tolerance.get("upper_price_toman")
                    if lower is not None and upper is not None:
                        half_width = max(
                            cash_price_f - float(lower),
                            float(upper) - cash_price_f,
                            0.0,
                        )
                        capped_lower = int(round((cap - half_width) / 50_000.0) * 50_000)
                        capped_upper = int(round((cap + half_width) / 50_000.0) * 50_000)
                        tolerance["lower_price_toman"] = capped_lower
                        tolerance["upper_price_toman"] = capped_upper
                        tolerance["lower_project_price"] = int(round(capped_lower / PRICE_MULTIPLIER))
                        tolerance["upper_project_price"] = int(round(capped_upper / PRICE_MULTIPLIER))
                audit = {
                    "commodity": name,
                    "policy": "CASH_NOT_ABOVE_OBSERVED_TOMORROW_BOOK",
                    "cash_limited_from_toman": int(cash_price_f),
                    "cash_limited_to_toman": cap,
                    "tomorrow_observed_toman": int(tom_price_f),
                }
                cash["term_structure_cap"] = audit
                audits.append(audit)
            continue
        ratio_meta = (
            rate.get("settlement_ratio_anchor")
            if isinstance(rate.get("settlement_ratio_anchor"), dict)
            else {}
        )
        # Prefer historical reopen/empirical ratio as the term-structure floor.
        # If prior reopens showed tomorrow below cash (ratio<1), that is allowed;
        # without a ratio, default to cash parity.
        ratio_floor = 1.0
        raw_ratio = ratio_meta.get("ratio")
        if raw_ratio is not None:
            try:
                ratio_floor = max(0.97, min(1.08, float(raw_ratio) * 0.998))
            except (TypeError, ValueError):
                ratio_floor = 1.0
        floor_toman = cash_price_f * ratio_floor
        if tom_price_f + 1e-9 >= floor_toman:
            continue
        floor = int(round(floor_toman / 50_000.0) * 50_000)
        rate["_pre_term_structure_estimated_price_toman"] = tom_price_f
        rate["estimated_price_toman"] = floor
        rate["estimated_project_price"] = int(round(floor / PRICE_MULTIPLIER))
        if rate.get("llm_value") is not None:
            rate["llm_value"] = rate["estimated_project_price"]
        tolerance = rate.get("tolerance")
        if isinstance(tolerance, dict):
            lower = tolerance.get("lower_price_toman")
            upper = tolerance.get("upper_price_toman")
            if lower is not None and float(lower) < floor:
                tolerance["lower_price_toman"] = floor
                tolerance["lower_project_price"] = int(round(floor / PRICE_MULTIPLIER))
            if upper is not None and float(upper) < floor:
                tolerance["upper_price_toman"] = floor
                tolerance["upper_project_price"] = int(round(floor / PRICE_MULTIPLIER))
        audit = {
            "commodity": name,
            "policy": "TOMORROW_NOT_BELOW_REOPEN_RATIO_FLOOR",
            "cash_price_toman": int(cash_price_f),
            "lifted_from_toman": int(tom_price_f),
            "lifted_to_toman": floor,
            "ratio_floor": ratio_floor,
            "settlement_ratio": raw_ratio,
            "tomorrow_residual": (
                (rate.get("online_residual_calibration") or {}).get("correction_ratio")
                if isinstance(rate.get("online_residual_calibration"), dict)
                else None
            ),
        }
        rate["term_structure_floor"] = audit
        audits.append(audit)
    return audits


def apply_low_date_family_band_separation(
    rates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Clamp low-date upper bands so they do not overlap same-coefficient coins.

    Uses the sibling non-low-date lower band (or point) minus a small principled
    gap derived from the 2.253-family intrinsic scale.  Point estimates are left
    unchanged; only the published tolerance is tightened.
    """

    by_name = {
        str(row.get("commodity_name")): row
        for row in rates
        if isinstance(row, dict) and row.get("commodity_name")
    }
    for row in rates:
        if not isinstance(row, dict):
            continue
        if str(row.get("status")) not in {"ESTIMATED", "OBSERVED_ANCHOR"}:
            continue
        name = str(row.get("commodity_name") or "")
        sibling_name = low_date_family_sibling_name(name)
        if sibling_name is None:
            continue
        sibling = by_name.get(sibling_name)
        if sibling is None or str(sibling.get("status")) not in {
            "ESTIMATED",
            "OBSERVED_ANCHOR",
        }:
            continue
        sibling_point = sibling.get("estimated_price_toman")
        if sibling_point is None:
            continue
        sibling_tol = sibling.get("tolerance") or {}
        sibling_floor = sibling_tol.get("lower_price_toman")
        if sibling_floor is None:
            sibling_floor = sibling_point
        separation = max(
            LOW_DATE_FAMILY_SEPARATION_MIN_TOMAN,
            int(
                round(
                    float(sibling_point)
                    * LOW_DATE_FAMILY_SEPARATION_RELATIVE
                    / 50_000
                )
                * 50_000
            ),
        )
        ceiling = int(sibling_floor) - separation
        if ceiling <= 0:
            continue
        tolerance = dict(row.get("tolerance") or {})
        point = float(row.get("estimated_price_toman") or 0.0)
        if point <= 0:
            continue
        lower = int(tolerance.get("lower_price_toman") or point)
        upper = int(tolerance.get("upper_price_toman") or point)
        original_upper = upper
        # Keep the point inside the band; allow zero upside if the point is
        # already pressed against the family ceiling.
        capped_upper = min(upper, max(int(round(point / 50_000) * 50_000), ceiling))
        if capped_upper >= upper:
            continue
        rounded_point = int(round(point / 50_000) * 50_000)
        if capped_upper < rounded_point:
            capped_upper = rounded_point
        if capped_upper >= original_upper:
            continue
        tolerance["upper_price_toman"] = capped_upper
        tolerance["upper_project_price"] = int(round(capped_upper / PRICE_MULTIPLIER))
        tolerance["lower_price_toman"] = min(lower, rounded_point)
        tolerance["lower_project_price"] = int(
            round(int(tolerance["lower_price_toman"]) / PRICE_MULTIPLIER)
        )
        tolerance["negative_tolerance_percent"] = max(
            0.0,
            (point - float(tolerance["lower_price_toman"])) / point * 100.0,
        )
        tolerance["positive_tolerance_percent"] = max(
            0.0,
            (float(capped_upper) - point) / point * 100.0,
        )
        tolerance["family_band_cap"] = {
            "sibling": sibling_name,
            "sibling_floor_toman": int(sibling_floor),
            "separation_toman": separation,
            "ceiling_toman": ceiling,
            "original_upper_toman": original_upper,
            "policy": "LOW_DATE_SAME_COEFFICIENT_NO_OVERLAP",
        }
        row["tolerance"] = tolerance
    return rates


def fresh_transfer_anchor_qhat(
    anchor: dict[str, Any], *, structural_qhat: float | None
) -> float:
    """Calibrate a tight interval for a recent locally observed coin anchor.

    The structural conformal error measures periods without a useful coin
    anchor and must not become a mandatory floor when a recent group quote or
    trade exists.  Freshness and the robust local dispersion determine this
    interval; the structural qhat is only an upper safety bound.
    """
    age_seconds = max(0.0, float(anchor.get("age_seconds") or 0.0))
    base = 0.0025 if anchor.get("latest_kind") == "TRADE" else 0.0030
    age_penalty = min(0.006, age_seconds / 86_400.0 * 0.006)
    dispersion = min(0.010, max(0.0, float(anchor.get("relative_mad") or 0.0)) * 1.5)
    local_qhat = max(base + age_penalty, dispersion)
    if structural_qhat is not None and float(structural_qhat) > 0:
        local_qhat = min(local_qhat, float(structural_qhat))
    return max(0.0015, min(0.015, local_qhat))


def estimate_rates(
    model: dict[str, Any],
    market_db: Path,
    end: datetime,
    conversation_db: Path | None = None,
    *,
    live_group_events_enabled: bool = True,
    group_live_events_before: datetime | None = None,
) -> dict[str, Any]:
    end = end.astimezone(timezone.utc).replace(microsecond=0)
    if group_live_events_before is not None:
        if group_live_events_before.tzinfo is None:
            group_live_events_before = group_live_events_before.replace(
                tzinfo=timezone.utc
            )
        group_live_events_before = group_live_events_before.astimezone(timezone.utc)
    if live_group_events_enabled:
        group_live_events_before = None
    elif group_live_events_before is None:
        # A direct caller that disables live inputs without supplying a
        # transition timestamp still gets a fully disabled live book; the
        # historical query remains bounded at the current estimate minute.
        group_live_events_before = end
    start = end - timedelta(seconds=WINDOW_SECONDS)
    result: dict[str, Any] = {
        "schema_version": 1,
        "model_kind": model["model_kind"],
        "is_llm": False,
        "generated_at_utc": iso_utc(datetime.now(timezone.utc)),
        "window_start_utc": iso_utc(start),
        "window_end_utc": iso_utc(end),
        "missing_token": NO_DATA_TOKEN,
        "price_unit": "TOMAN",
        "project_price_multiplier_to_toman": PRICE_MULTIPLIER,
        "market_input_policy": {
            "average_window_seconds": MARKET_AVERAGE_SECONDS,
            "melted_latest_bucket_seconds": MELTED_LIVE_BUCKET_SECONDS,
            "point_value": "LATEST_REAL_PARSED_EVENT",
            "parsed_event_only": True,
            "synthetic_forward_fill": False,
        },
        "order_flow_point_adjustment_enabled": bool(
            model.get("order_flow_point_adjustment_enabled", False)
        ),
        "group_offer_anchor_policy": model.get("group_offer_anchor")
        or {"enabled": False},
        "live_group_event_control": {
            "enabled": bool(live_group_events_enabled),
            "status": "CONNECTED"
            if live_group_events_enabled
            else "DISCONNECTED_LIVE_ONLY",
            "disabled_since_utc": (
                iso_utc(group_live_events_before)
                if group_live_events_before is not None
                else None
            ),
            "historical_group_data_enabled": True,
        },
        "settlements": {},
    }
    supported = [row for row in model["commodities"] if row["status"] == "SUPPORTED"]
    flow_point_enabled = bool(model.get("order_flow_point_adjustment_enabled", False))
    anchor_policy = model.get("group_offer_anchor") or {}
    anchor_enabled = bool(anchor_policy.get("enabled", False))
    anchor_database = conversation_db or DEFAULT_CONVERSATION_DB
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
    with connect_market_db(market_db) as connection:
        for settlement in SETTLEMENT_CONFIG:
            inputs = observed_inputs(connection, settlement, end)
            # The point estimate follows the last real parsed event; the
            # 30-second averages remain present in ``inputs`` for stability,
            # trend and diagnostics.
            melted_value = live_point_value(inputs["melted_gold"])
            generic_coin_value = live_point_value(inputs["generic_coin"])
            usd_value = live_point_value(inputs["usd"])
            xau_value = live_point_value(inputs["xauusd"])
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
                        group_live_events_before=group_live_events_before,
                    )
                    if anchor_enabled
                    else {
                        "status": "DISABLED",
                        "llm_value": NO_DATA_TOKEN,
                    }
                )
                anchor_used = group_anchor.get("status") == "OBSERVED"
                historical_group_anchor: dict[str, Any] | None = None
                historical_anchor_inputs: dict[str, Any] | None = None
                historical_anchor_melted: float | None = None
                historical_melted_transfer_ratio: float | None = None
                historical_group_candidate = bool(
                    not anchor_used
                    and melted_value is not None
                    and (
                        commodity["name"] != "امام"
                        or generic_coin_value is None
                    )
                )
                if historical_group_candidate:
                    historical_group_anchor = select_historical_group_anchor(
                        anchor_database,
                        commodity=str(commodity["name"]),
                        settlement=settlement,
                        trade_form="PHYSICAL",
                        end=end,
                        minimum_confidence=anchor_minimum_confidence,
                        group_live_events_before=group_live_events_before,
                    )
                    if historical_group_anchor.get("status") == "OBSERVED":
                        historical_anchor_time = parse_datetime(
                            str(historical_group_anchor["event_time_utc"])
                        )
                        historical_anchor_inputs = observed_inputs(
                            connection,
                            settlement,
                            historical_anchor_time,
                        )
                        historical_anchor_melted = live_point_value(
                            historical_anchor_inputs["melted_gold"]
                        )
                        if (
                            historical_anchor_melted is not None
                            and float(historical_anchor_melted) > 0
                        ):
                            historical_melted_transfer_ratio = (
                                float(melted_value)
                                / float(historical_anchor_melted)
                            )
                historical_group_transfer_usable = bool(
                    historical_group_anchor is not None
                    and historical_group_anchor.get("status") == "OBSERVED"
                    and historical_melted_transfer_ratio is not None
                    and 0.5 <= historical_melted_transfer_ratio <= 2.0
                )
                if (
                    settlement == "TOMORROW"
                    and not anchor_used
                    and not historical_group_transfer_usable
                ):
                    cash_rates = (
                        result.get("settlements", {})
                        .get("CASH", {})
                        .get("rates", [])
                    )
                    cash_rate = next(
                        (
                            row
                            for row in cash_rates
                            if row.get("commodity_name") == commodity["name"]
                            and row.get("status") in {"ESTIMATED", "OBSERVED_ANCHOR"}
                            and row.get("estimated_price_toman") is not None
                        ),
                        None,
                    )
                    empirical_ratio = select_empirical_cash_tomorrow_ratio(
                        anchor_database,
                        commodity=str(commodity["name"]),
                        trade_form="PHYSICAL",
                        end=end,
                        group_live_events_before=group_live_events_before,
                    )
                    reopen_ratio = select_reopen_cash_tomorrow_ratio(
                        anchor_database,
                        commodity=str(commodity["name"]),
                        trade_form="PHYSICAL",
                        end=end,
                        current_regime_score=(
                            float(market_regime_score)
                            if market_regime_score is not None
                            else None
                        ),
                        market_db=market_db,
                    )
                    settlement_ratio = resolve_cash_tomorrow_ratio_for_estimate(
                        empirical=empirical_ratio,
                        reopen=reopen_ratio,
                        end=end,
                    )
                    if cash_rate is not None and settlement_ratio.get("status") == "OBSERVED":
                        estimate = float(cash_rate["estimated_price_toman"]) * float(
                            settlement_ratio["ratio"]
                        )
                        rounded_toman = int(round(estimate / 50_000) * 50_000)
                        cash_tolerance = cash_rate.get("tolerance") or {}
                        cash_qhat = max(
                            float(cash_tolerance.get("negative_tolerance_percent") or 0.0),
                            float(cash_tolerance.get("positive_tolerance_percent") or 0.0),
                        ) / 100.0
                        ratio_qhat = float(settlement_ratio["relative_qhat"])
                        combined_qhat = math.sqrt(cash_qhat ** 2 + ratio_qhat ** 2)
                        if settlement_ratio.get("scope") in {
                            "POOLED_COIN_MARKET",
                            "REOPEN_REGIME_BLEND_WITH_EMPIRICAL",
                        }:
                            combined_qhat += 0.001
                        combined_qhat = max(0.003, min(0.015, combined_qhat))
                        transfer_anchor = {
                            "reference_price_toman": estimate,
                            "reference_source": "CURRENT_CASH_X_REOPEN_REGIME_SETTLEMENT_RATIO",
                        }
                        ratio_scope = str(settlement_ratio.get("scope") or "")
                        if "REOPEN" in ratio_scope:
                            confidence = "HIGH_REOPEN_REGIME_SETTLEMENT_TRANSFER"
                            method = (
                                "CURRENT_CASH_ESTIMATE_X_PREVIOUS_REOPEN_"
                                "REGIME_WEIGHTED_TOMORROW_CASH_RATIO"
                            )
                        elif settlement_ratio.get("scope") == "COMMODITY":
                            confidence = "HIGH_EMPIRICAL_SETTLEMENT_TRANSFER"
                            method = (
                                "CURRENT_CASH_ESTIMATE_X_ROBUST_EMPIRICAL_TOMORROW_CASH_RATIO"
                            )
                        else:
                            confidence = "MEDIUM_POOLED_SETTLEMENT_TRANSFER"
                            method = (
                                "CURRENT_CASH_ESTIMATE_X_ROBUST_EMPIRICAL_TOMORROW_CASH_RATIO"
                            )
                        rates.append(
                            {
                                "commodity_id": commodity["id"],
                                "commodity_name": commodity["name"],
                                "status": "ESTIMATED",
                                "llm_value": int(round(rounded_toman / PRICE_MULTIPLIER)),
                                "intrinsic_toman": (
                                    float(melted_value) * float(commodity["coefficient"])
                                    if melted_value is not None
                                    else None
                                ),
                                "bubble_ratio": None,
                                "market_pressure_score": pressure_score,
                                "market_regime": market_regime.get("regime"),
                                "market_regime_score": market_regime_score,
                                "market_regime_confidence": market_regime_confidence,
                                "estimated_price_toman": rounded_toman,
                                "estimated_project_price": int(
                                    round(rounded_toman / PRICE_MULTIPLIER)
                                ),
                                "tolerance": observed_anchor_tolerance(
                                    transfer_anchor,
                                    relative_error_qhat=combined_qhat,
                                ),
                                "confidence": confidence,
                                "training_sample_count": calibration_row["sample_count"],
                                "direct_settlement_sample_count": calibration_row[
                                    "direct_settlement_sample_count"
                                ],
                                "method": method,
                                "group_offer_anchor": group_anchor,
                                "cash_reference_rate": cash_rate,
                                "settlement_ratio_anchor": settlement_ratio,
                            }
                        )
                        continue
                if commodity["low_date"] and not anchor_used:
                    imam_anchor = select_group_offer_anchor(
                        anchor_database,
                        commodity="امام",
                        settlement=settlement,
                        trade_form="PHYSICAL",
                        end=end,
                        seconds=anchor_window,
                        minimum_confidence=anchor_minimum_confidence,
                        group_live_events_before=group_live_events_before,
                    )
                    low_date_ratio = select_last_low_date_to_imam_ratio(
                        anchor_database,
                        commodity=str(commodity["name"]),
                        settlement=settlement,
                        trade_form="PHYSICAL",
                        end=end,
                    )
                    if (
                        imam_anchor.get("status") == "OBSERVED"
                        and low_date_ratio.get("status") == "OBSERVED"
                    ):
                        estimate = (
                            float(imam_anchor["reference_price_toman"])
                            * float(low_date_ratio["ratio_to_imam"])
                        )
                        rounded_toman = int(
                            round(estimate / 50_000) * 50_000
                        )
                        pair_age_seconds = max(
                            (
                                end
                                - parse_datetime(
                                    str(
                                        low_date_ratio[
                                            "low_date_event_utc"
                                        ]
                                    )
                                )
                            ).total_seconds(),
                            (
                                end
                                - parse_datetime(
                                    str(low_date_ratio["imam_event_utc"])
                                )
                            ).total_seconds(),
                        )
                        ratio_qhat = min(
                            0.02,
                            0.006
                            + max(0.0, pair_age_seconds)
                            / (4 * 86_400)
                            * 0.01,
                        )
                        transfer_anchor = {
                            **imam_anchor,
                            "reference_price_toman": estimate,
                        }
                        rates.append(
                            {
                                "commodity_id": commodity["id"],
                                "commodity_name": commodity["name"],
                                "status": "ESTIMATED",
                                "llm_value": int(
                                    round(rounded_toman / PRICE_MULTIPLIER)
                                ),
                                "intrinsic_toman": (
                                    float(melted_value)
                                    * float(commodity["coefficient"])
                                    if melted_value is not None
                                    else None
                                ),
                                "bubble_ratio": None,
                                "market_pressure_score": pressure_score,
                                "estimated_price_toman": rounded_toman,
                                "estimated_project_price": int(
                                    round(rounded_toman / PRICE_MULTIPLIER)
                                ),
                                "tolerance": observed_anchor_tolerance(
                                    transfer_anchor,
                                    relative_error_qhat=ratio_qhat,
                                ),
                                "confidence": (
                                    "MEDIUM_LOW_DATE_"
                                    "SAME_SETTLEMENT_RATIO_TRANSFER"
                                ),
                                "training_sample_count": calibration_row[
                                    "sample_count"
                                ],
                                "direct_settlement_sample_count": (
                                    calibration_row[
                                        "direct_settlement_sample_count"
                                    ]
                                ),
                                "method": (
                                    "CURRENT_SAME_SETTLEMENT_IMAM_ANCHOR_X_"
                                    "LAST_LOW_DATE_TO_IMAM_RATIO"
                                ),
                                "group_offer_anchor": group_anchor,
                                "imam_group_offer_anchor": imam_anchor,
                                "low_date_ratio_anchor": low_date_ratio,
                            }
                        )
                        continue
                if historical_group_candidate:
                    assert historical_group_anchor is not None
                    if historical_group_anchor.get("status") == "OBSERVED":
                        assert historical_anchor_inputs is not None
                        anchor_inputs = historical_anchor_inputs
                        anchor_melted = historical_anchor_melted
                        melted_transfer_ratio = historical_melted_transfer_ratio
                        if (
                            melted_transfer_ratio is not None
                            and 0.5 <= melted_transfer_ratio <= 2.0
                        ):
                            intrinsic = float(melted_value) * float(commodity["coefficient"])
                            anchor_intrinsic = float(anchor_melted) * float(commodity["coefficient"])
                            transferred = float(historical_group_anchor["reference_price_toman"]) * intrinsic / anchor_intrinsic
                            structural_ratio = float(calibration_row.get("bubble_ratio_median") or 0.0)
                            feature_center = calibration_row.get("melted_vs_global_center")
                            if melted_vs_global_ratio is not None and feature_center is not None:
                                structural_ratio += float(calibration_row.get("melted_vs_global_slope") or 0.0) * (melted_vs_global_ratio - float(feature_center))
                            pressure_center = calibration_row.get("market_pressure_center")
                            if flow_point_enabled and pressure_score is not None and pressure_center is not None:
                                structural_ratio += float(calibration_row.get("market_pressure_slope") or 0.0) * (float(pressure_score) - float(pressure_center))
                            structural_ratio = max(-0.15, min(2.0, structural_ratio))
                            structural = intrinsic * (1.0 + structural_ratio)
                            age = float(historical_group_anchor["age_seconds"] or 0.0)
                            use_morning_reopen = is_morning_reopen_window(
                                end, has_live_anchor=anchor_used, model=model
                            )
                            if use_morning_reopen:
                                current_usdt = live_point_value(inputs.get("usdt") or {})
                                anchor_usdt = live_point_value(anchor_inputs.get("usdt") or {})
                                reopen = build_morning_reopen_anchor(
                                    intrinsic=intrinsic,
                                    structural=structural,
                                    transferred=transferred,
                                    anchor_age_seconds=age,
                                    current_herat=usd_value,
                                    anchor_herat=live_point_value(anchor_inputs.get("usd") or {}),
                                    current_melted=float(melted_value),
                                    anchor_melted=float(anchor_melted),
                                    current_usdt=current_usdt,
                                    anchor_usdt=anchor_usdt,
                                    settlement=settlement,
                                    model=model,
                                )
                                estimate = float(reopen["estimate_toman"])
                                rounded_toman = int(reopen["estimated_price_toman"])
                                adjusted_ratio = estimate / intrinsic - 1.0
                                structural_qhat = (
                                    model.get("conformal_tolerance", {})
                                    .get("per_commodity_relative_error_qhat", {})
                                    .get(commodity["name"])
                                    or model.get("conformal_tolerance", {}).get(
                                        "global_relative_error_qhat"
                                    )
                                )
                                base_tolerance = asymmetric_tolerance(
                                    intrinsic=intrinsic,
                                    estimated_price=estimate,
                                    adjusted_ratio=adjusted_ratio,
                                    calibration=calibration_row,
                                    pressure_score=pressure_score,
                                    market_regime_score=market_regime_score,
                                    market_regime_confidence=market_regime_confidence,
                                    conformal_floor=structural_qhat,
                                )
                                tolerance = widen_tolerance(
                                    base_tolerance,
                                    multiplier=float(reopen["band_multiplier"]),
                                    center_toman=estimate,
                                )
                                rates.append(
                                    {
                                        "commodity_id": commodity["id"],
                                        "commodity_name": commodity["name"],
                                        "status": "ESTIMATED",
                                        "llm_value": int(
                                            round(rounded_toman / PRICE_MULTIPLIER)
                                        ),
                                        "intrinsic_toman": intrinsic,
                                        "bubble_ratio": adjusted_ratio,
                                        "market_pressure_score": pressure_score,
                                        "market_regime": market_regime.get("regime"),
                                        "market_regime_score": market_regime_score,
                                        "market_regime_confidence": market_regime_confidence,
                                        "estimated_price_toman": rounded_toman,
                                        "estimated_project_price": int(
                                            round(rounded_toman / PRICE_MULTIPLIER)
                                        ),
                                        "tolerance": tolerance,
                                        "confidence": "MEDIUM_MORNING_REOPEN",
                                        "training_sample_count": calibration_row[
                                            "sample_count"
                                        ],
                                        "direct_settlement_sample_count": calibration_row[
                                            "direct_settlement_sample_count"
                                        ],
                                        "method": MORNING_REOPEN_METHOD,
                                        "group_offer_anchor": group_anchor,
                                        "historical_group_anchor": historical_group_anchor,
                                        "anchor_melted": anchor_inputs["melted_gold"],
                                        "anchor_weight": reopen["blend_weights"][
                                            "transferred"
                                        ],
                                        "transferred_anchor_price_toman": transferred,
                                        "structural_price_toman": structural,
                                        "morning_reopen": reopen,
                                    }
                                )
                                continue
                            reference_source = str(historical_group_anchor.get("reference_source") or "")
                            if "TRADE" in reference_source:
                                source_weight = 0.92
                            elif "OFFER" in reference_source:
                                source_weight = 0.86
                            else:
                                source_weight = 0.84
                            anchor_weight = source_weight * math.exp(-age / (18.0 * 3_600.0))
                            # Retain a small floor during multi-day closures,
                            # while allowing the live underlying/regime model
                            # to take over as the coin anchor ages.
                            anchor_weight = max(0.12, min(source_weight, anchor_weight))
                            estimate = anchor_weight * transferred + (1.0 - anchor_weight) * structural
                            adjusted_ratio = estimate / intrinsic - 1.0
                            rounded_toman = int(round(estimate / 50_000) * 50_000)
                            structural_qhat = (
                                model.get("conformal_tolerance", {})
                                .get("per_commodity_relative_error_qhat", {})
                                .get(commodity["name"])
                                or model.get("conformal_tolerance", {}).get("global_relative_error_qhat")
                            )
                            # A local quote/trade observed within six hours has
                            # materially lower uncertainty than the structural
                            # no-coin-data model.  Use its own age/dispersion
                            # interval rather than forcing the global conformal
                            # floor (the source of multi-million-toman bands).
                            if age <= 6 * 3_600:
                                transfer_anchor = {
                                    **historical_group_anchor,
                                    "reference_price_toman": estimate,
                                }
                                tolerance = observed_anchor_tolerance(
                                    transfer_anchor,
                                    relative_error_qhat=fresh_transfer_anchor_qhat(
                                        historical_group_anchor,
                                        structural_qhat=structural_qhat,
                                    ),
                                )
                                tolerance["source"] = "FRESH_LOCAL_TRANSFER_ANCHOR"
                            else:
                                tolerance = asymmetric_tolerance(
                                    intrinsic=intrinsic,
                                    estimated_price=estimate,
                                    adjusted_ratio=adjusted_ratio,
                                    calibration=calibration_row,
                                    pressure_score=pressure_score,
                                    market_regime_score=market_regime_score,
                                    market_regime_confidence=market_regime_confidence,
                                    conformal_floor=structural_qhat,
                                )
                            rates.append({
                                "commodity_id": commodity["id"], "commodity_name": commodity["name"],
                                "status": "ESTIMATED", "llm_value": int(round(rounded_toman / PRICE_MULTIPLIER)),
                                "intrinsic_toman": intrinsic, "bubble_ratio": adjusted_ratio,
                                "market_pressure_score": pressure_score,
                                "market_regime": market_regime.get("regime"),
                                "market_regime_score": market_regime_score,
                                "market_regime_confidence": market_regime_confidence,
                                "estimated_price_toman": rounded_toman,
                                "estimated_project_price": int(round(rounded_toman / PRICE_MULTIPLIER)),
                                "tolerance": tolerance,
                                "confidence": "HIGH_FRESH_GROUP_TRANSFER" if age <= 6 * 3_600 else "LOW_STALE_GROUP_TRANSFER",
                                "training_sample_count": calibration_row["sample_count"],
                                "direct_settlement_sample_count": calibration_row["direct_settlement_sample_count"],
                                "method": "FRESHNESS_WEIGHTED_GROUP_ANCHOR_X_CURRENT_MELTED_BLEND_STRUCTURAL_REGIME",
                                "group_offer_anchor": group_anchor,
                                "historical_group_anchor": historical_group_anchor,
                                "anchor_melted": anchor_inputs["melted_gold"],
                                "anchor_weight": anchor_weight,
                                "transferred_anchor_price_toman": transferred,
                                "structural_price_toman": structural,
                            })
                            continue
                    historical_anchor = select_last_manual_coin_anchor(
                        anchor_database,
                        commodity=str(commodity["name"]),
                        settlement=settlement,
                        trade_form="PHYSICAL",
                        end=end,
                    )
                    if historical_anchor.get("status") == "OBSERVED":
                        anchor_time = parse_datetime(
                            str(historical_anchor["event_time_utc"])
                        )
                        anchor_inputs = observed_inputs(
                            connection, settlement, anchor_time
                        )
                        anchor_melted = anchor_inputs["melted_gold"].get(
                            "average_price"
                        )
                        if anchor_melted is not None and float(anchor_melted) > 0:
                            melted_ratio = (
                                float(melted_value) / float(anchor_melted)
                            )
                            dollar_adjustment = 0.0
                            current_usd = live_point_value(inputs["usd"])
                            anchor_usd = live_point_value(anchor_inputs["usd"])
                            if (
                                not commodity["low_date"]
                                and current_usd is not None
                                and anchor_usd is not None
                                and float(anchor_usd) > 0
                            ):
                                # Melted gold already carries most of the FX
                                # move.  Dollar only adjusts the residual,
                                # never replaces the same-market anchor.
                                dollar_adjustment = max(
                                    -0.01,
                                    min(
                                        0.01,
                                        0.10
                                        * (
                                            float(current_usd)
                                            / float(anchor_usd)
                                            / melted_ratio
                                            - 1.0
                                        ),
                                    ),
                                )
                            estimate = float(
                                historical_anchor["price_toman"]
                            ) * melted_ratio * (1.0 + dollar_adjustment)
                            rounded_toman = int(
                                round(estimate / 50_000) * 50_000
                            )
                            anchor_age = max(
                                0.0, (end - anchor_time).total_seconds()
                            )
                            transfer_qhat = min(
                                0.025,
                                0.006 + anchor_age / (4 * 86_400) * 0.014,
                            )
                            transfer_anchor = {
                                "reference_price_toman": estimate,
                                "best_bid_toman": None,
                                "best_ask_toman": None,
                            }
                            rates.append(
                                {
                                    "commodity_id": commodity["id"],
                                    "commodity_name": commodity["name"],
                                    "status": "ESTIMATED",
                                    "llm_value": int(
                                        round(
                                            rounded_toman
                                            / PRICE_MULTIPLIER
                                        )
                                    ),
                                    "intrinsic_toman": (
                                        float(melted_value)
                                        * float(commodity["coefficient"])
                                    ),
                                    "bubble_ratio": None,
                                    "market_pressure_score": pressure_score,
                                    "estimated_price_toman": rounded_toman,
                                    "estimated_project_price": int(
                                        round(
                                            rounded_toman
                                            / PRICE_MULTIPLIER
                                        )
                                    ),
                                    "tolerance": observed_anchor_tolerance(
                                        transfer_anchor,
                                        relative_error_qhat=transfer_qhat,
                                    ),
                                    "confidence": (
                                        "MEDIUM_LOW_DATE_MELTED_TRANSFER"
                                        if commodity["low_date"]
                                        else "MEDIUM_SAME_SETTLEMENT_ANCHOR_TRANSFER"
                                    ),
                                    "training_sample_count": calibration_row[
                                        "sample_count"
                                    ],
                                    "direct_settlement_sample_count": (
                                        calibration_row[
                                            "direct_settlement_sample_count"
                                        ]
                                    ),
                                    "method": (
                                        "LAST_SAME_SETTLEMENT_LOW_DATE_ANCHOR_X_MELTED"
                                        if commodity["low_date"]
                                        else "LAST_SAME_SETTLEMENT_COIN_ANCHOR_X_MELTED_AND_USD"
                                    ),
                                    "group_offer_anchor": group_anchor,
                                    "historical_coin_anchor": historical_anchor,
                                    "anchor_melted": anchor_inputs[
                                        "melted_gold"
                                    ],
                                    "anchor_usd": anchor_inputs["usd"],
                                    "melted_ratio": melted_ratio,
                                    "dollar_residual_adjustment": (
                                        dollar_adjustment
                                    ),
                                }
                            )
                            continue
                # Low-date coins normally carry little or no independent
                # premium.  When there is no same-coin completed-trade anchor,
                # use the correctly selected melted-gold stream directly.
                # A learned residual is deliberately kept narrow; it may not
                # inherit the full Imam premium.
                if commodity["low_date"] and not anchor_used and melted_value is not None:
                    intrinsic = float(melted_value) * float(commodity["coefficient"])
                    learned_residual = float(
                        calibration_row.get("bubble_ratio_median") or 0.0
                    )
                    imam_residual = float(trained_imam_bubble_ratio or 0.0) * 0.10
                    bounded_residual = max(
                        -0.012,
                        min(0.020, learned_residual, imam_residual + 0.008),
                    )
                    estimate = intrinsic * (1.0 + bounded_residual)
                    rounded_toman = int(round(estimate / 50_000) * 50_000)
                    physical_age = float(
                        inputs["melted_gold"].get("physical_base_age_seconds") or 0.0
                    )
                    residual_qhat = min(
                        0.028,
                        0.010 + physical_age / 86_400 * 0.010,
                    )
                    transfer_anchor = {
                        "reference_price_toman": estimate,
                        "best_bid_toman": None,
                        "best_ask_toman": None,
                    }
                    rates.append(
                        {
                            "commodity_id": commodity["id"],
                            "commodity_name": commodity["name"],
                            "status": "ESTIMATED",
                            "llm_value": int(round(rounded_toman / PRICE_MULTIPLIER)),
                            "intrinsic_toman": intrinsic,
                            "bubble_ratio": bounded_residual,
                            "market_pressure_score": pressure_score,
                            "estimated_price_toman": rounded_toman,
                            "estimated_project_price": int(
                                round(rounded_toman / PRICE_MULTIPLIER)
                            ),
                            "tolerance": observed_anchor_tolerance(
                                transfer_anchor,
                                relative_error_qhat=residual_qhat,
                            ),
                            "confidence": "MEDIUM_LOW_DATE_INTRINSIC_FALLBACK",
                            "training_sample_count": calibration_row["sample_count"],
                            "direct_settlement_sample_count": calibration_row[
                                "direct_settlement_sample_count"
                            ],
                            "method": "LOW_DATE_INTRINSIC_PLUS_BOUNDED_RESIDUAL",
                            "group_offer_anchor": group_anchor,
                            "melted_input": inputs["melted_gold"],
                            "bounded_residual_ratio": bounded_residual,
                        }
                    )
                    continue
                # A dated training calibration describes historical bubble
                # structure; it is not a current cash-coin quote.  Do not
                # present it as one when there is neither a fresh cash group
                # anchor nor a fresh generic cash-coin observation.  Low-date
                # coins are intentionally excluded because their physical
                # melted-gold intrinsic fallback is a separate contract.
                if (
                    settlement == "CASH"
                    and not commodity["low_date"]
                    and not anchor_used
                    and generic_coin_value is None
                ):
                    tomorrow_anchor = select_group_offer_anchor(
                        anchor_database,
                        commodity=str(commodity["name"]),
                        settlement="TOMORROW",
                        trade_form="PHYSICAL",
                        end=end,
                        seconds=anchor_window,
                        minimum_confidence=anchor_minimum_confidence,
                        group_live_events_before=group_live_events_before,
                    )
                    ratio_anchor = select_last_cash_tomorrow_ratio(
                        anchor_database,
                        commodity=str(commodity["name"]),
                        trade_form="PHYSICAL",
                        end=end,
                    )
                    if (
                        tomorrow_anchor.get("status") == "OBSERVED"
                        and ratio_anchor.get("status") == "OBSERVED"
                    ):
                        estimate = (
                            float(tomorrow_anchor["reference_price_toman"])
                            * float(ratio_anchor["ratio"])
                        )
                        rounded_toman = int(
                            round(estimate / 50_000) * 50_000
                        )
                        ratio_qhat = min(
                            0.03,
                            anchor_relative_error_qhat + 0.004,
                        )
                        transfer_anchor = {
                            **tomorrow_anchor,
                            "reference_price_toman": estimate,
                        }
                        rates.append(
                            {
                                "commodity_id": commodity["id"],
                                "commodity_name": commodity["name"],
                                "status": "ESTIMATED",
                                "llm_value": int(
                                    round(rounded_toman / PRICE_MULTIPLIER)
                                ),
                                "intrinsic_toman": (
                                    float(melted_value)
                                    * float(commodity["coefficient"])
                                    if melted_value is not None
                                    else None
                                ),
                                "bubble_ratio": None,
                                "market_pressure_score": pressure_score,
                                "estimated_price_toman": rounded_toman,
                                "estimated_project_price": int(
                                    round(rounded_toman / PRICE_MULTIPLIER)
                                ),
                                "tolerance": observed_anchor_tolerance(
                                    transfer_anchor,
                                    relative_error_qhat=ratio_qhat,
                                ),
                                "confidence": "MEDIUM_SETTLEMENT_RATIO_TRANSFER",
                                "training_sample_count": calibration_row[
                                    "sample_count"
                                ],
                                "direct_settlement_sample_count": (
                                    calibration_row[
                                        "direct_settlement_sample_count"
                                    ]
                                ),
                                "method": (
                                    "CURRENT_TOMORROW_ANCHOR_X_"
                                    "LAST_CASH_TOMORROW_RATIO"
                                ),
                                "group_offer_anchor": group_anchor,
                                "tomorrow_group_offer_anchor": tomorrow_anchor,
                                "settlement_ratio_anchor": ratio_anchor,
                            }
                        )
                        continue
                    # No same-minute cash/tomorrow conversion is available.
                    # Fall through to the independently trained structural
                    # estimator below.  A coin quote may sharpen a band, but
                    # must never be a prerequisite for a price estimate: the
                    # structural path is driven by melted gold, FX/USDT,
                    # XAUUSD, and the independently classified order flow.
                if melted_value is None:
                    if anchor_used:
                        estimate = float(group_anchor["reference_price_toman"])
                        rounded_toman = int(round(estimate / 50_000) * 50_000)
                        rates.append(
                            {
                                "commodity_id": commodity["id"],
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
                            "commodity_id": commodity["id"],
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
                            "commodity_id": commodity["id"],
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
                    method = "DIRECT_GENERIC_COIN_LATEST_PLUS_30S_MEAN_ASSUMED_IMAM"
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

                morning_reopen_meta = None
                if (
                    not anchor_used
                    and is_morning_reopen_window(
                        end, has_live_anchor=False, model=model
                    )
                ):
                    # Overnight market anchors for basis when no same-settlement
                    # coin quote survived into the reopen window.
                    overnight = observed_inputs(
                        connection, settlement, end - timedelta(hours=18)
                    )
                    reopen = build_morning_reopen_anchor(
                        intrinsic=intrinsic,
                        structural=float(estimate),
                        transferred=None,
                        anchor_age_seconds=18 * 3_600,
                        current_herat=usd_value,
                        anchor_herat=live_point_value(overnight.get("usd") or {}),
                        current_melted=float(melted_value),
                        anchor_melted=live_point_value(overnight.get("melted_gold") or {}),
                        current_usdt=live_point_value(inputs.get("usdt") or {}),
                        anchor_usdt=live_point_value(overnight.get("usdt") or {}),
                        settlement=settlement,
                        model=model,
                    )
                    estimate = float(reopen["estimate_toman"])
                    adjusted_ratio = estimate / intrinsic - 1.0
                    method = MORNING_REOPEN_METHOD
                    morning_reopen_meta = reopen

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
                if morning_reopen_meta is not None:
                    tolerance = widen_tolerance(
                        tolerance,
                        multiplier=float(morning_reopen_meta["band_multiplier"]),
                        center_toman=estimate,
                    )
                elif pressure_score is not None or market_regime_score is not None:
                    method += "+ASYMMETRIC_REGIME_AND_ORDER_FLOW_TOLERANCE"
                rate_row = {
                    "commodity_id": commodity["id"],
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
                        else (
                            "MEDIUM_MORNING_REOPEN"
                            if morning_reopen_meta is not None
                            else calibration_row["confidence"]
                        )
                    ),
                    "training_sample_count": calibration_row["sample_count"],
                    "direct_settlement_sample_count": calibration_row[
                        "direct_settlement_sample_count"
                    ],
                    "method": method,
                    "group_offer_anchor": group_anchor,
                }
                if morning_reopen_meta is not None:
                    rate_row["morning_reopen"] = morning_reopen_meta
                rates.append(rate_row)

            result["settlements"][settlement] = {
                "inputs": inputs,
                "theoretical_melted_toman": theoretical_melted,
                "melted_bubble_toman": melted_bubble,
                "melted_vs_global_ratio": melted_vs_global_ratio,
                "current_imam_bubble_ratio": current_imam_bubble_ratio,
                "bubble_regime_shift": regime_shift,
                "market_pressure": inputs["order_flow"],
                "market_regime": market_regime,
                "rates": apply_low_date_family_band_separation(rates),
            }
    return result


def write_json_atomic(path: Path, payload: dict[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(temporary, mode)
    temporary.replace(path)


def write_training_database(path: Path, model: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE training_metadata (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            CREATE TABLE training_examples (
                id INTEGER PRIMARY KEY,
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                event_time_utc TEXT NOT NULL,
                commodity_name TEXT NOT NULL,
                settlement_type TEXT NOT NULL,
                trade_form TEXT NOT NULL,
                side TEXT NOT NULL,
                project_price INTEGER NOT NULL,
                observed_price_toman INTEGER NOT NULL,
                quantity INTEGER,
                source_confidence REAL NOT NULL,
                source_weight REAL NOT NULL,
                melted_average_toman REAL NOT NULL,
                melted_samples INTEGER NOT NULL,
                melted_reference_selection TEXT,
                melted_reference_trade_form TEXT,
                melted_reference_weight REAL NOT NULL,
                usd_average_toman REAL,
                usd_reference_source TEXT,
                usd_is_usdt_proxy INTEGER NOT NULL,
                usdt_average_toman REAL,
                xauusd_average REAL,
                theoretical_melted_toman REAL,
                melted_vs_global_ratio REAL,
                generic_coin_average_toman REAL,
                intrinsic_toman REAL NOT NULL,
                bubble_ratio REAL NOT NULL,
                market_pressure_score REAL,
                market_regime TEXT,
                market_regime_score REAL,
                market_regime_confidence REAL,
                market_regime_volatility_percent REAL,
                cross_state TEXT,
                lifecycle_training_weight REAL,
                all_three_market_inputs_observed INTEGER NOT NULL,
                accepted INTEGER NOT NULL,
                rejection_reason TEXT,
                source_text TEXT
            );
            CREATE INDEX idx_training_examples_time
                ON training_examples(event_time_utc);
            CREATE INDEX idx_training_examples_commodity_time
                ON training_examples(commodity_name, settlement_type, event_time_utc);
            """
        )
        metadata = {
            "schema_version": model["schema_version"],
            "trained_at_utc": model["trained_at_utc"],
            "market_database": model["market_database"],
            "group_offer_file": model["group_offer_file"],
            "group_offer_import": model["group_offer_import"],
            "validation": model["validation"],
        }
        connection.executemany(
            "INSERT INTO training_metadata(key, value_json) VALUES (?, ?)",
            [
                (key, json.dumps(value, ensure_ascii=False, sort_keys=True))
                for key, value in metadata.items()
            ],
        )
        rows = []
        for index, row in enumerate(model["training_examples"], 1):
            source_id = str(
                row.get("trade_id")
                if row.get("trade_id") is not None
                else row.get("group_offer_id")
            )
            rows.append(
                (
                    index,
                    row["source_kind"],
                    source_id,
                    row["event_time_utc"],
                    row["commodity_name"],
                    row["settlement_type"],
                    row.get("trade_form") or "PHYSICAL",
                    row.get("side") or "UNKNOWN",
                    row["project_price"],
                    row["observed_price_toman"],
                    row.get("quantity"),
                    row.get("source_confidence", 1.0),
                    row.get("source_weight", 1.0),
                    row["melted_average_toman"],
                    row["melted_samples"],
                    row.get("melted_reference_selection"),
                    row.get("melted_reference_trade_form"),
                    row.get("melted_reference_weight", 1.0),
                    row.get("usd_average_toman"),
                    row.get("usd_reference_source"),
                    int(bool(row.get("usd_is_usdt_proxy"))),
                    row.get("usdt_average_toman"),
                    row.get("xauusd_average"),
                    row.get("theoretical_melted_toman"),
                    row.get("melted_vs_global_ratio"),
                    row.get("generic_coin_average_toman"),
                    row["intrinsic_toman"],
                    row["bubble_ratio"],
                    row.get("market_pressure_score"),
                    row.get("market_regime"),
                    row.get("market_regime_score"),
                    row.get("market_regime_confidence"),
                    row.get("market_regime_volatility_percent"),
                    row.get("cross_state"),
                    row.get("lifecycle_training_weight"),
                    int(bool(row.get("all_three_market_inputs_observed"))),
                    int(bool(row["accepted"])),
                    row.get("rejection_reason"),
                    row.get("source_text"),
                )
            )
        connection.executemany(
            """
            INSERT INTO training_examples(
                id, source_kind, source_id, event_time_utc, commodity_name,
                settlement_type, trade_form, side, project_price, observed_price_toman,
                quantity, source_confidence, source_weight,
                melted_average_toman, melted_samples, melted_reference_selection,
                melted_reference_trade_form, melted_reference_weight, usd_average_toman,
                usd_reference_source, usd_is_usdt_proxy, usdt_average_toman,
                xauusd_average, theoretical_melted_toman,
                melted_vs_global_ratio, generic_coin_average_toman,
                intrinsic_toman, bubble_ratio, market_pressure_score,
                market_regime, market_regime_score, market_regime_confidence,
                market_regime_volatility_percent, cross_state,
                lifecycle_training_weight,
                all_three_market_inputs_observed, accepted, rejection_reason,
                source_text
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train")
    train_parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    train_parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    train_parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    train_parser.add_argument(
        "--group-offers", type=Path, default=DEFAULT_GROUP_OFFERS
    )
    train_parser.add_argument(
        "--training-db", type=Path, default=DEFAULT_TRAINING_DB
    )
    train_parser.add_argument(
        "--conversation-db", type=Path, default=DEFAULT_CONVERSATION_DB
    )
    train_parser.add_argument(
        "--review-decisions-db", type=Path, default=DEFAULT_REVIEW_DECISIONS_DB
    )
    train_parser.add_argument(
        "--project-labels",
        choices=("disabled", "completed"),
        default="disabled",
        help="Project labels stay disabled while project activity is experimental.",
    )

    estimate_parser = subparsers.add_parser("estimate")
    estimate_parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    estimate_parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    estimate_parser.add_argument(
        "--conversation-db", type=Path, default=DEFAULT_CONVERSATION_DB
    )
    estimate_parser.add_argument("--end", help="UTC ISO timestamp; defaults to latest event minute")
    estimate_parser.add_argument("--output", type=Path)

    ledger_parser = subparsers.add_parser(
        "ledger",
        help="Inspect or prune the multi-model prediction ledger.",
    )
    ledger_parser.add_argument(
        "--conversation-db", type=Path, default=DEFAULT_CONVERSATION_DB
    )
    ledger_parser.add_argument(
        "--calibration-db", type=Path, default=DEFAULT_CALIBRATION_DB,
        help="Mutable prediction ledger; separate from the read-only conversation input.",
    )
    ledger_parser.add_argument(
        "--outcome-retention-days",
        type=int,
        default=LEDGER_OUTCOME_RETENTION_DAYS,
        help="Keep rows that matched a real trade for this long (training corpus).",
    )
    ledger_parser.add_argument(
        "--unmatched-retention-days",
        type=int,
        default=LEDGER_UNMATCHED_RETENTION_DAYS,
        help="Keep rows no trade ever arrived for this long; they carry no label.",
    )
    ledger_parser.add_argument(
        "--prune",
        action="store_true",
        help="Actually expire and delete; without this flag the command only reports.",
    )
    return parser


def latest_complete_minute(connection: sqlite3.Connection) -> datetime:
    row = connection.execute("SELECT MAX(event_time_utc) FROM price_events").fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("No market events exist")
    latest = parse_datetime(str(row[0]))
    return latest.replace(second=0, microsecond=0) + timedelta(minutes=1)


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "train":
        model = train_model(
            args.repo,
            args.market_db,
            args.group_offers,
            args.conversation_db,
            args.review_decisions_db,
            project_labels_enabled=args.project_labels == "completed",
        )
        model["combined_training_database"] = str(args.training_db.resolve())
        write_training_database(args.training_db, model)
        write_json_atomic(args.model, model)
        validation = model["validation"]
        print(
            json.dumps(
                {
                    "model": str(args.model.resolve()),
                    "kind": model["model_kind"],
                    "is_llm": False,
                    "total_completed_trades": model["training_trade_count_total"],
                    "aligned_examples": model["training_trade_count_with_market_context"],
                    "accepted_examples": model["training_trade_count_accepted"],
                    "rejected_examples": model["training_trade_count_rejected"],
                    "project_labels_enabled": model["project_labels_enabled"],
                    "accepted_group_confirmed_trades": model[
                        "group_confirmed_trade_count_accepted"
                    ],
                    "accepted_group_human_reviewed_trades": model[
                        "group_human_reviewed_trade_count_accepted"
                    ],
                    "accepted_group_offers": model["group_offer_count_accepted"],
                    "combined_training_examples": model[
                        "combined_training_count_accepted"
                    ],
                    "validation": validation,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "ledger":
        connection = sqlite3.connect(args.calibration_db)
        connection.row_factory = sqlite3.Row
        try:
            ensure_online_schema(connection)
            report = prune_prediction_ledger(
                connection,
                as_of=datetime.now(timezone.utc),
                outcome_retention_days=args.outcome_retention_days,
                unmatched_retention_days=args.unmatched_retention_days,
                dry_run=not args.prune,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    model = load_model(args.model)
    if args.end:
        end = parse_datetime(args.end)
    else:
        with connect_market_db(args.market_db) as connection:
            end = latest_complete_minute(connection)
    estimate = estimate_rates(model, args.market_db, end, args.conversation_db)
    if args.output:
        write_json_atomic(args.output, estimate, mode=0o644)
        print(str(args.output.resolve()))
    else:
        print(json.dumps(estimate, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
