#!/usr/bin/env python3
"""Classify a peer database before shared-table seeding.

The production deploy flow uses this as a fail-closed guard. A fresh Iran
database may contain system/bootstrap rows, but it must not contain user,
business, or non-system chat data before automatic current-state seeding.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Mapping

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers.sync import TABLE_ORDER
from core.db import AsyncSessionLocal


SIGNAL_QUERIES = {
    "users": "SELECT COUNT(*) FROM users",
    "accountant_relations": "SELECT COUNT(*) FROM accountant_relations",
    "customer_relations": "SELECT COUNT(*) FROM customer_relations",
    "invitations": "SELECT COUNT(*) FROM invitations",
    "admin_market_messages": "SELECT COUNT(*) FROM admin_market_messages",
    "admin_broadcast_messages": "SELECT COUNT(*) FROM admin_broadcast_messages",
    "notifications": "SELECT COUNT(*) FROM notifications",
    "user_blocks": "SELECT COUNT(*) FROM user_blocks",
    "trading_settings": "SELECT COUNT(*) FROM trading_settings",
    "market_schedule_overrides": "SELECT COUNT(*) FROM market_schedule_overrides",
    "offers": "SELECT COUNT(*) FROM offers",
    "trades": "SELECT COUNT(*) FROM trades",
    "non_system_non_mandatory_chats": """
        SELECT COUNT(*)
        FROM chats
        WHERE COALESCE(is_system, false) = false
          AND COALESCE(is_mandatory, false) = false
    """,
    "non_system_non_mandatory_chat_members": """
        SELECT COUNT(*)
        FROM chat_members cm
        JOIN chats c ON c.id = cm.chat_id
        WHERE COALESCE(c.is_system, false) = false
          AND COALESCE(c.is_mandatory, false) = false
    """,
}

BASELINE_QUERIES = {
    "all_chats": "SELECT COUNT(*) FROM chats",
    "mandatory_system_chats": """
        SELECT COUNT(*)
        FROM chats
        WHERE COALESCE(is_system, false) = true
          AND COALESCE(is_mandatory, false) = true
    """,
    "all_chat_members": "SELECT COUNT(*) FROM chat_members",
    "mandatory_system_chat_members": """
        SELECT COUNT(*)
        FROM chat_members cm
        JOIN chats c ON c.id = cm.chat_id
        WHERE COALESCE(c.is_system, false) = true
          AND COALESCE(c.is_mandatory, false) = true
    """,
    "commodities": "SELECT COUNT(*) FROM commodities",
    "commodity_aliases": "SELECT COUNT(*) FROM commodity_aliases",
    "market_runtime_state": "SELECT COUNT(*) FROM market_runtime_state",
    "change_log_unsynced": "SELECT COUNT(*) FROM change_log WHERE synced = false",
}


def classify_counts(signal_counts: Mapping[str, int]) -> dict[str, object]:
    signal_total = sum(max(int(value), 0) for value in signal_counts.values())
    classification = "fresh" if signal_total == 0 else "existing"
    return {
        "classification": classification,
        "is_fresh": classification == "fresh",
        "signal_total": signal_total,
        "blocking_signals": {
            key: int(value)
            for key, value in signal_counts.items()
            if int(value) > 0
        },
    }


async def fetch_counts(queries: Mapping[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        for key, query in queries.items():
            result = await db.execute(text(query))
            counts[key] = int(result.scalar_one())
    return counts


async def inspect_state() -> dict[str, object]:
    signals = await fetch_counts(SIGNAL_QUERIES)
    baseline = await fetch_counts(BASELINE_QUERIES)
    classification = classify_counts(signals)
    return {
        "status": "ok",
        **classification,
        "signals": signals,
        "baseline": baseline,
        "shared_tables": [table for table, _order in sorted(TABLE_ORDER.items(), key=lambda item: item[1])],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect shared sync table state before production seeding.")
    parser.add_argument("--format", choices=("json", "shell"), default="json")
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    payload = await inspect_state()
    if args.format == "shell":
        print(f"CLASSIFICATION={payload['classification']}")
        print(f"IS_FRESH={1 if payload['is_fresh'] else 0}")
        print(f"SIGNAL_TOTAL={payload['signal_total']}")
    else:
        print(json.dumps(payload, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
