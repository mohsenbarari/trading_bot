#!/usr/bin/env python3
"""One-shot repair: multiply mislabeled toman rows stored under IRT labels by 10.

Safe to re-run: only rows still below the mislabel ceiling are updated.
Does not touch private melted gold (already true IRT).  Does not write coin
rates JSON or restart collectors.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.price_magnitude_policy import (
    MISLABELED_COIN_IRT_CEILING,
    MISLABELED_FX_IRT_CEILING,
    MISLABELED_MESGHAL_IRT_CEILING,
    TOMAN_PER_IRT,
)


REPAIR_VERSION = "market-store-toman-scale-repair-v1"

_TARGETS = (
    ("IRT_PER_MESGHAL_750", MISLABELED_MESGHAL_IRT_CEILING, None),
    ("IRT_PER_COIN", MISLABELED_COIN_IRT_CEILING, None),
    ("IRT_PER_USDT", MISLABELED_FX_IRT_CEILING, ("USDT_IRT",)),
    ("IRT_PER_USD", MISLABELED_FX_IRT_CEILING, ("USD_HERAT",)),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repair(connection: sqlite3.Connection, *, dry_run: bool) -> dict[str, object]:
    summary: dict[str, object] = {"repair_version": REPAIR_VERSION, "dry_run": dry_run, "units": {}}
    for price_unit, ceiling, instruments in _TARGETS:
        params: list[object] = [price_unit, float(ceiling)]
        instrument_clause = ""
        if instruments:
            instrument_clause = " AND instrument IN (" + ",".join("?" for _ in instruments) + ")"
            params.extend(instruments)
        # Never touch private melted: it is already true IRT.
        private_clause = " AND instrument != 'MELTED_GOLD_PRIVATE'"
        count_sql = f"""
            SELECT COUNT(*) FROM market_observations
            WHERE price_unit = ? AND price_num > 0 AND price_num < ?{instrument_clause}{private_clause}
        """
        count = int(connection.execute(count_sql, params).fetchone()[0])
        sample = connection.execute(
            f"""
            SELECT instrument, MIN(price_num), MAX(price_num), AVG(price_num)
            FROM market_observations
            WHERE price_unit = ? AND price_num > 0 AND price_num < ?{instrument_clause}{private_clause}
            GROUP BY instrument
            """,
            params,
        ).fetchall()
        unit_info = {
            "rows": count,
            "ceiling": float(ceiling),
            "by_instrument": [
                {
                    "instrument": row[0],
                    "min": float(row[1]),
                    "max": float(row[2]),
                    "avg": float(row[3]),
                }
                for row in sample
            ],
        }
        if not dry_run and count:
            connection.execute(
                f"""
                UPDATE market_observations
                SET price_num = price_num * ?,
                    price_value = CAST(price_num * ? AS TEXT),
                    parser_version = CASE
                        WHEN parser_version LIKE '%{REPAIR_VERSION}%' THEN parser_version
                        ELSE parser_version || '+{REPAIR_VERSION}'
                    END
                WHERE price_unit = ? AND price_num > 0 AND price_num < ?{instrument_clause}{private_clause}
                """,
                [float(TOMAN_PER_IRT), float(TOMAN_PER_IRT), *params],
            )
        summary["units"][price_unit] = unit_info
    if not dry_run:
        connection.commit()
    summary["repaired_at_utc"] = _utc_now()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--market-store",
        type=Path,
        default=Path(
            "/srv/trading-bot/production-data/coin-intelligence/"
            "private-gold-live/market/market.sqlite3"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    connection = sqlite3.connect(args.market_store)
    try:
        summary = repair(connection, dry_run=args.dry_run)
    finally:
        connection.close()
    text = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
