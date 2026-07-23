#!/usr/bin/env python3
"""Run the explicit read-only Shadow Telegram market collector."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.telegram_collector.collector import (
    collect_telegram_market,
)
from core.market_intelligence.telegram_collector.config import (
    SOURCES,
    TelegramCollectorSettings,
    TelegramCredentials,
    source_for_username,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acknowledge-shadow-read-only",
        action="store_true",
        help="Required acknowledgement; collection remains Shadow-only.",
    )
    parser.add_argument(
        "--mode",
        choices=("live", "backfill"),
        required=True,
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=(
            Path(os.environ["COIN_MARKET_TELEGRAM_DATABASE"])
            if os.getenv("COIN_MARKET_TELEGRAM_DATABASE")
            else None
        ),
    )
    parser.add_argument(
        "--session",
        type=Path,
        default=(
            Path(os.environ["COIN_MARKET_TELEGRAM_SESSION"])
            if os.getenv("COIN_MARKET_TELEGRAM_SESSION")
            else None
        ),
    )
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--request-wait-seconds", type=float)
    parser.add_argument(
        "--channel",
        action="append",
        help=(
            "Allowed public channel username. Repeat for multiple channels; "
            "without it all catalogued channels are read."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.acknowledge_shadow_read_only:
        raise SystemExit(
            "--acknowledge-shadow-read-only is required"
        )
    if args.database is None or args.session is None:
        raise SystemExit(
            "--database and --session are required unless their "
            "COIN_MARKET_TELEGRAM_* environment variables are set"
        )
    try:
        credentials = TelegramCredentials.from_environment()
        settings = TelegramCollectorSettings(
            credentials=credentials,
            database_path=args.database,
            session_path=args.session,
        )
        settings.validate_paths(repository_root=REPO_ROOT)
        sources = (
            tuple(source_for_username(value) for value in args.channel)
            if args.channel
            else SOURCES
        )
        results = asyncio.run(
            collect_telegram_market(
                settings,
                sources=sources,
                days=args.days,
                resume_from_checkpoint=args.mode == "live",
                batch_size=args.batch_size,
                request_wait_seconds=args.request_wait_seconds,
            )
        )
    except (RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "SHADOW_COLLECTION_REJECTED",
                    "reason": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "status": "SHADOW_COLLECTION_COMPLETE",
                "mode": args.mode,
                "sources": results,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
