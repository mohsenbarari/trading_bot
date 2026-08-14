#!/usr/bin/env python3
"""Project Market Store rows into a legacy market_prices DB for fair eval.

Leakage rule: only observations with ``available_at_utc <= as_of_utc`` are
copied.  Instrument/label mapping is explicit and conservative so the operator
estimator can consume the projected ``price_events`` table.

This is an evaluation aid, not a live bridge replacement.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_events (
    id INTEGER PRIMARY KEY,
    raw_post_id INTEGER,
    event_index INTEGER NOT NULL DEFAULT 0,
    instrument TEXT NOT NULL,
    market_label TEXT,
    settlement_term TEXT,
    trade_form TEXT,
    event_type TEXT,
    side TEXT,
    price_value TEXT,
    price_num REAL,
    currency TEXT,
    price_unit TEXT,
    quantity_value TEXT,
    quantity_num REAL,
    quantity_unit TEXT,
    movement TEXT,
    event_time_utc TEXT NOT NULL,
    tehran_datetime TEXT,
    tehran_date TEXT,
    tehran_minute TEXT,
    tehran_weekday INTEGER,
    tehran_weekday_name TEXT,
    source_datetime_text TEXT,
    parse_method TEXT,
    parse_confidence REAL,
    parser_version TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_price_events_instrument_time
    ON price_events(instrument, event_time_utc);
CREATE TABLE IF NOT EXISTS external_instruments (
    code TEXT PRIMARY KEY,
    title TEXT,
    venue TEXT
);
CREATE TABLE IF NOT EXISTS external_market_observations (
    id INTEGER PRIMARY KEY,
    instrument_code TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL DEFAULT 0,
    quote_kind TEXT NOT NULL,
    raw_price_value TEXT NOT NULL,
    raw_price_num REAL NOT NULL,
    normalized_price_value TEXT,
    normalized_price_num REAL,
    volume_value TEXT,
    UNIQUE(instrument_code, observed_at_utc, interval_seconds, quote_kind)
);
"""


# Market Store -> legacy estimator vocabulary.
# trade_form PAPER_NORMAL collapses to PAPER for the operator app.
MAPPING: list[dict[str, str]] = [
    {
        "instrument": "MELTED_GOLD_AGGREGATE",
        "trade_form": "PAPER_NORMAL",
        "legacy_instrument": "MELTED_GOLD",
        "legacy_label": "آبشده حواله",
        "legacy_trade_form": "PAPER",
    },
    {
        "instrument": "MELTED_GOLD_AGGREGATE",
        "trade_form": "PHYSICAL",
        "legacy_instrument": "MELTED_GOLD",
        "legacy_label": "آبشده نقدی",
        "legacy_trade_form": "PHYSICAL",
    },
    {
        "instrument": "MELTED_GOLD_PRIVATE",
        "market_label": "PRIVATE_GOLD_PHYSICAL",
        "legacy_instrument": "MELTED_GOLD",
        "legacy_label": "آبشده کانال جدید فیزیکی فردا",
        "legacy_trade_form": "PHYSICAL",
    },
    {
        "instrument": "MELTED_GOLD_PRIVATE",
        "market_label": "PRIVATE_GOLD_PAPER_NORMAL",
        "legacy_instrument": "MELTED_GOLD",
        "legacy_label": "آبشده کانال جدید کاغذی عادی",
        "legacy_trade_form": "PAPER",
    },
    {
        "instrument": "USD_HERAT",
        "legacy_instrument": "USD_HERAT",
        "legacy_label": "دلار هرات",
        "legacy_trade_form": "PAPER",
    },
    {
        "instrument": "XAUUSD",
        "legacy_instrument": "XAUUSD",
        "legacy_label": "انس",
        "legacy_trade_form": "NOT_APPLICABLE",
    },
]


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _match(row: sqlite3.Row) -> dict[str, str] | None:
    for rule in MAPPING:
        if row["instrument"] != rule["instrument"]:
            continue
        if "market_label" in rule and row["market_label"] != rule["market_label"]:
            continue
        if "trade_form" in rule and str(row["trade_form"] or "").upper() != rule["trade_form"]:
            continue
        return rule
    return None


def project(*, market_store: Path, as_of_utc: str, out_db: Path) -> dict[str, Any]:
    _parse_utc(as_of_utc)  # validate
    if out_db.exists():
        out_db.unlink()
    out_db.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(f"file:{market_store}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    dest = sqlite3.connect(out_db)
    dest.executescript(LEGACY_SCHEMA)

    mapped_instruments = sorted({rule["instrument"] for rule in MAPPING})
    placeholders = ",".join("?" for _ in mapped_instruments)
    rows = source.execute(
        f"""
        SELECT id, instrument, market_label, settlement_term, trade_form, event_type,
               side, price_num, price_unit, currency, quantity_num, quantity_unit,
               event_time_utc, available_at_utc, tehran_datetime, tehran_date,
               tehran_minute, tehran_weekday, parse_confidence, parser_version,
               attributes_json
        FROM market_observations
        WHERE quality_state = 'ELIGIBLE'
          AND available_at_utc <= ?
          AND instrument IN ({placeholders})
          AND price_num IS NOT NULL
          AND price_num > 0
        ORDER BY available_at_utc, id
        """,
        (as_of_utc, *mapped_instruments),
    )

    inserted = 0
    skipped = 0
    by_legacy: dict[str, int] = {}
    for row in rows:
        rule = _match(row)
        if rule is None:
            skipped += 1
            continue
        settlement = str(row["settlement_term"] or "UNKNOWN").upper()
        if settlement == "CASH":
            settlement = "TODAY"
        trade_form = rule["legacy_trade_form"]
        # Estimator expects PAPER for havale; keep NOT_APPLICABLE for XAU.
        dest.execute(
            """
            INSERT INTO price_events(
                id, raw_post_id, event_index, instrument, market_label,
                settlement_term, trade_form, event_type, side, price_value,
                price_num, currency, price_unit, quantity_num, quantity_unit,
                event_time_utc, tehran_datetime, tehran_date, tehran_minute,
                tehran_weekday, parse_confidence, parser_version, created_at
            ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["id"]),
                int(row["id"]),
                rule["legacy_instrument"],
                rule["legacy_label"],
                settlement,
                trade_form,
                str(row["event_type"] or "QUOTE"),
                str(row["side"] or "UNKNOWN"),
                str(row["price_num"]),
                float(row["price_num"]),
                str(row["currency"] or "IRT"),
                str(row["price_unit"] or ""),
                row["quantity_num"],
                row["quantity_unit"],
                # Use available_at as event_time so the operator window cannot
                # see a fact before it was available (leakage-safe as-of).
                str(row["available_at_utc"]),
                row["tehran_datetime"],
                row["tehran_date"],
                row["tehran_minute"],
                row["tehran_weekday"],
                row["parse_confidence"],
                row["parser_version"],
                str(row["available_at_utc"]),
            ),
        )
        inserted += 1
        key = f"{rule['legacy_instrument']}|{rule['legacy_label']}"
        by_legacy[key] = by_legacy.get(key, 0) + 1

    # Wallex / IME external rows when present.
    ext_inserted = 0
    try:
        external_rows = source.execute(
            """
            SELECT id, source_code, instrument, event_type, side, price_num, price_unit,
                   currency, available_at_utc, attributes_json
            FROM market_observations
            WHERE quality_state='ELIGIBLE'
              AND source_code IN ('WALLEX_PUBLIC_API', 'IME_REALTIME_BOARD')
              AND available_at_utc <= ?
              AND price_num IS NOT NULL AND price_num > 0
            """,
            (as_of_utc,),
        )
        for row in external_rows:
            instrument = str(row["instrument"])
            if instrument not in {"USDT_IRT", "IME_GOLD_BAR", "IME_GOLD_COIN_IMAM"}:
                continue
            side = str(row["side"] or "MID").upper()
            quote_kind = {"BUY": "BID", "SELL": "ASK"}.get(side, "MID")
            dest.execute(
                "INSERT OR IGNORE INTO external_instruments(code, title, venue) VALUES (?, ?, ?)",
                (instrument, instrument, str(row["source_code"])),
            )
            dest.execute(
                """
                INSERT OR REPLACE INTO external_market_observations(
                    id, instrument_code, observed_at_utc, interval_seconds,
                    quote_kind, raw_price_value, raw_price_num,
                    normalized_price_value, normalized_price_num, volume_value
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    int(row["id"]),
                    instrument,
                    str(row["available_at_utc"]),
                    quote_kind,
                    str(row["price_num"]),
                    float(row["price_num"]),
                    str(row["price_num"]),
                    float(row["price_num"]),
                ),
            )
            ext_inserted += 1
    except sqlite3.Error:
        pass

    dest.commit()
    source.close()
    dest.close()
    return {
        "as_of_utc": as_of_utc,
        "market_store": str(market_store),
        "out_db": str(out_db),
        "price_events_inserted": inserted,
        "price_events_skipped_unmapped": skipped,
        "external_inserted": ext_inserted,
        "by_legacy_label": by_legacy,
        "leakage_rule": "available_at_utc <= as_of_utc; event_time_utc set to available_at_utc",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-store", type=Path, required=True)
    parser.add_argument("--as-of-utc", required=True)
    parser.add_argument("--out-db", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path)
    args = parser.parse_args()
    summary = project(
        market_store=args.market_store,
        as_of_utc=args.as_of_utc,
        out_db=args.out_db,
    )
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
