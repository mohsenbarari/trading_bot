#!/usr/bin/env python3
"""Online-backup Legacy estimator inputs without raw file copy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.shadow_legacy_bridge import (
    BridgeError,
    online_backup,
    sqlite_quick_check,
    utc_now,
)


def _inventory(path: Path) -> dict[str, int | str]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        tables = {
            str(row[0]): int(
                connection.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]
            )
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"
            )
        }
    finally:
        connection.close()
    return tables


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        online_backup(args.source, args.destination)
        check = sqlite_quick_check(args.destination)
        if check != "OK":
            raise BridgeError("backup_quick_check_failed")
        inventory = _inventory(args.destination)
    except (BridgeError, OSError, sqlite3.Error) as exc:
        print(
            json.dumps({"status": "FAILED", "reason": type(exc).__name__}, sort_keys=True),
            flush=True,
        )
        return 2
    print(
        json.dumps(
            {
                "status": "BACKED_UP",
                "completed_at_utc": utc_now(),
                "bytes": args.destination.stat().st_size,
                "quick_check": check,
                "tables": len(inventory),
                "rows": sum(int(value) for value in inventory.values()),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
