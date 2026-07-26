#!/usr/bin/env python3
"""Run the local durable coin-intelligence Shadow worker."""

from __future__ import annotations

import argparse
import asyncio

from core.config import settings
from core.market_intelligence.job_queue import run_worker
from core.market_intelligence.shadow import process_durable_project_job


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--acknowledge-shadow-only",
        action="store_true",
        help="Required acknowledgement that outputs are non-authoritative.",
    )
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


async def _main() -> None:
    args = parse_args()
    if not args.acknowledge_shadow_only:
        raise SystemExit("--acknowledge-shadow-only is required")
    if not (
        settings.coin_intelligence_shadow_enabled
        and settings.coin_intelligence_shadow_persist_enabled
        and settings.coin_intelligence_shadow_project_events_enabled
        and settings.coin_intelligence_shadow_durable_worker_enabled
    ):
        raise SystemExit("durable Shadow worker flags are not fully enabled")
    await run_worker(
        handler=process_durable_project_job,
        once=bool(args.once),
    )


if __name__ == "__main__":
    asyncio.run(_main())
