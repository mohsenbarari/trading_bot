"""Read-only, causal adapter from estimator predictions to coin price anchors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3

from .coin_group_resolution import (
    MAXIMUM_ANCHOR_AGE_SECONDS,
    CoinPriceAnchor,
)
from .coin_groups import _PRICE_BOUNDS
from .market_contracts import MarketStoreContractError, normalize_utc


PREDICTION_ANCHOR_MODEL_ID = "MAIN_ONLINE"
PREDICTION_ANCHOR_BUCKET_SECONDS = 15 * 60
_COMMODITY_CODES = {
    "امام": "IMAM",
    "بهار": "BAHAR",
    "بهار آزادی": "BAHAR",
    "نیم بهار": "HALF_BAHAR",
    "ربع بهار": "QUARTER_BAHAR",
    "نیم تاریخ پایین": "HALF_LOW_DATE",
    "ربع تاریخ پایین": "QUARTER_LOW_DATE",
    "یک گرمی": "ONE_GRAM",
    "یک گرمی مرکزی": "ONE_GRAM",
}
_REQUIRED_COLUMNS = {
    "id",
    "prediction_time_utc",
    "created_at_utc",
    "model_id",
    "commodity",
    "settlement",
    "estimated_price_toman",
}


class CoinPredictionAnchorError(ValueError):
    """An operator-safe failure while reading the prediction ledger."""


@dataclass(frozen=True, slots=True)
class CoinPredictionAnchorLoad:
    anchors: tuple[CoinPriceAnchor, ...]
    rows_seen: int
    rows_rejected: int


def _stamp(value: str, *, field: str) -> tuple[str, datetime] | None:
    try:
        normalized = normalize_utc(value, field_name=field)
    except (MarketStoreContractError, TypeError, ValueError):
        return None
    return normalized, datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _project_price(value: object, *, commodity_code: str) -> int | None:
    try:
        full_toman = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not full_toman.is_finite() or full_toman != full_toman.to_integral_value():
        return None
    integer = int(full_toman)
    if integer <= 0 or integer % 1_000:
        return None
    project_price = integer // 1_000
    low, high = _PRICE_BOUNDS[commodity_code]
    return project_price if low <= project_price <= high else None


def load_coin_prediction_anchors(
    path: Path | str,
    *,
    earliest_event_time_utc: datetime | str,
    as_of_utc: datetime | str,
    bucket_seconds: int = PREDICTION_ANCHOR_BUCKET_SECONDS,
) -> CoinPredictionAnchorLoad:
    """Load bounded MAIN_ONLINE prices known before the staged event window.

    Predictions are downsampled per commodity/book so a three-day staging
    replay stays bounded.  ``prediction_time_utc`` is the market-event time;
    ``created_at_utc`` is the causal availability time.
    """

    try:
        earliest = normalize_utc(
            earliest_event_time_utc,
            field_name="coin_prediction_anchor_earliest_event_time_utc",
        )
        as_of = normalize_utc(
            as_of_utc,
            field_name="coin_prediction_anchor_as_of_utc",
        )
    except MarketStoreContractError as exc:
        raise CoinPredictionAnchorError(str(exc)) from exc
    earliest_stamp = datetime.fromisoformat(earliest.replace("Z", "+00:00"))
    as_of_stamp = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if earliest_stamp > as_of_stamp:
        raise CoinPredictionAnchorError("coin_prediction_anchor_window_invalid")
    if not 60 <= int(bucket_seconds) <= MAXIMUM_ANCHOR_AGE_SECONDS:
        raise CoinPredictionAnchorError("coin_prediction_anchor_bucket_invalid")
    lower = (earliest_stamp - timedelta(seconds=MAXIMUM_ANCHOR_AGE_SECONDS))
    lower_utc = lower.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    database = Path(path).expanduser().resolve()
    if not database.is_file():
        raise CoinPredictionAnchorError("coin_prediction_ledger_unavailable")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{database}?mode=ro",
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(coin_estimate_predictions)"
            )
        }
        if not _REQUIRED_COLUMNS.issubset(columns):
            raise CoinPredictionAnchorError("coin_prediction_ledger_schema_invalid")
        rows = connection.execute(
            """
            SELECT id,prediction_time_utc,created_at_utc,commodity,settlement,
                   estimated_price_toman
            FROM coin_estimate_predictions
            WHERE model_id=?
              AND prediction_time_utc>=? AND prediction_time_utc<?
              AND created_at_utc<=?
            ORDER BY prediction_time_utc,id
            """,
            (PREDICTION_ANCHOR_MODEL_ID, lower_utc, as_of, as_of),
        ).fetchall()
    except CoinPredictionAnchorError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise CoinPredictionAnchorError("coin_prediction_ledger_read_failed") from exc
    finally:
        if connection is not None:
            connection.close()

    rejected = 0
    by_bucket: dict[tuple[str, str, int], CoinPriceAnchor] = {}
    for row in rows:
        code = _COMMODITY_CODES.get(str(row["commodity"] or "").strip())
        settlement = str(row["settlement"] or "").strip().upper()
        if code is None or settlement not in {"CASH", "TOMORROW"}:
            rejected += 1
            continue
        price = _project_price(row["estimated_price_toman"], commodity_code=code)
        event = _stamp(
            str(row["prediction_time_utc"] or ""),
            field="coin_prediction_anchor_event_time_utc",
        )
        available = _stamp(
            str(row["created_at_utc"] or ""),
            field="coin_prediction_anchor_available_at_utc",
        )
        if price is None or event is None or available is None:
            rejected += 1
            continue
        event_utc, event_stamp = event
        available_utc, available_stamp = available
        if (
            event_stamp < lower
            or event_stamp >= as_of_stamp
            or available_stamp < event_stamp
            or available_stamp > as_of_stamp
        ):
            rejected += 1
            continue
        bucket = int(event_stamp.timestamp()) // int(bucket_seconds)
        by_bucket[(code, settlement, bucket)] = CoinPriceAnchor(
            commodity_code=code,
            price_project_thousand_toman=price,
            event_time_utc=event_utc,
            available_at_utc=available_utc,
            settlement_term=settlement,
            trade_form="PHYSICAL",
            evidence_kind="MODEL_SNAPSHOT",
        )
    anchors = tuple(
        sorted(
            by_bucket.values(),
            key=lambda item: (
                str(item.event_time_utc),
                item.commodity_code,
                item.settlement_term,
            ),
        )
    )
    if rows and not anchors:
        raise CoinPredictionAnchorError("coin_prediction_ledger_no_valid_anchors")
    return CoinPredictionAnchorLoad(
        anchors=anchors,
        rows_seen=len(rows),
        rows_rejected=rejected,
    )
