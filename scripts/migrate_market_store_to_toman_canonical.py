#!/usr/bin/env python3
"""Migrate Market Store from rial-canonical rows to toman-canonical rows.

Safe to re-run: only rows still labeled IRT_PER_* are converted (÷10 + rename).
Does not write coin-rates JSON.  Does not restart collectors.
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

from core.market_intelligence.price_magnitude_policy import IRT_TO_TOMAN_UNIT, RIAL_PER_TOMAN


MIGRATE_VERSION = "market-store-toman-canonical-v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def migrate(connection: sqlite3.Connection, *, dry_run: bool) -> dict[str, object]:
    summary: dict[str, object] = {
        "migrate_version": MIGRATE_VERSION,
        "dry_run": dry_run,
        "units": {},
    }
    for old_unit, new_unit in IRT_TO_TOMAN_UNIT.items():
        count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM market_observations
                WHERE price_unit = ?
                """,
                (old_unit,),
            ).fetchone()[0]
        )
        sample = connection.execute(
            """
            SELECT instrument, MIN(price_num), MAX(price_num), AVG(price_num)
            FROM market_observations
            WHERE price_unit = ?
            GROUP BY instrument
            """,
            (old_unit,),
        ).fetchall()
        summary["units"][old_unit] = {
            "rows": count,
            "new_unit": new_unit,
            "by_instrument": [
                {
                    "instrument": row[0],
                    "min": float(row[1]) if row[1] is not None else None,
                    "max": float(row[2]) if row[2] is not None else None,
                    "avg": float(row[3]) if row[3] is not None else None,
                }
                for row in sample
            ],
        }
        if not dry_run and count:
            connection.execute(
                """
                UPDATE market_observations
                SET price_num = price_num / ?,
                    price_value = CAST(price_num / ? AS TEXT),
                    currency = 'TOMAN',
                    price_unit = ?,
                    parser_version = CASE
                        WHEN parser_version LIKE '%"""
                + MIGRATE_VERSION
                + """%' THEN parser_version
                        ELSE parser_version || '+"""
                + MIGRATE_VERSION
                + """'
                    END
                WHERE price_unit = ?
                """,
                (float(RIAL_PER_TOMAN), float(RIAL_PER_TOMAN), new_unit, old_unit),
            )
    # Project coin book is already toman-family; only normalize currency label.
    project_count = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM market_observations
            WHERE price_unit = 'PROJECT_THOUSAND_TOMAN' AND currency != 'TOMAN'
            """
        ).fetchone()[0]
    )
    summary["project_currency_relabel_rows"] = project_count
    if not dry_run and project_count:
        connection.execute(
            """
            UPDATE market_observations
            SET currency = 'TOMAN'
            WHERE price_unit = 'PROJECT_THOUSAND_TOMAN' AND currency != 'TOMAN'
            """
        )
    if not dry_run:
        connection.commit()
    summary["migrated_at_utc"] = _utc_now()
    remaining = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM market_observations
            WHERE price_unit LIKE 'IRT_%'
            """
        ).fetchone()[0]
    )
    summary["remaining_irt_unit_rows"] = remaining
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
        summary = migrate(connection, dry_run=args.dry_run)
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
