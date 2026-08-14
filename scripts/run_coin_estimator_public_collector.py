#!/usr/bin/env python3
"""Run the estimator's legacy-schema public collector from canonical source.

The standalone estimator still consumes its compact ``price_events`` schema.
This process owns that one compatibility database while the staging bridge
projects its normalized facts into the canonical Market Store.  Mutable data,
credentials, and the Telethon session are supplied through protected runtime
configuration; no checkout-local path is used.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ESTIMATOR_ROOT = REPO_ROOT / "apps" / "coin_rate_estimator"
if str(ESTIMATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ESTIMATOR_ROOT))

from live_server import live_collection_loop  # noqa: E402
from telegram_price_collector.config import DEFAULT_CHANNELS, Settings  # noqa: E402


def main() -> int:
    settings = Settings.from_environment()
    runtime_root = Path(
        os.environ["COIN_RATE_ESTIMATOR_RUNTIME_DIR"]
    ).expanduser().resolve()
    for candidate in (settings.db_path.resolve(), settings.session_path.resolve()):
        candidate.relative_to(runtime_root)
    priority_channels = (
        "qheimat_ounce",
        "ToofanHarirodOfficial",
        *tuple(
            channel
            for channel in DEFAULT_CHANNELS
            if channel not in {"qheimat_ounce", "ToofanHarirodOfficial"}
        ),
    )
    asyncio.run(
        live_collection_loop(
            settings.db_path,
            backfill_minutes=int(
                os.environ.get("COIN_RATE_ESTIMATOR_BACKFILL_MINUTES", "15")
            ),
            channels=priority_channels,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
