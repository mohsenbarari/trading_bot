#!/usr/bin/env python3
"""Print a read-only, privacy-minimized P7 coin-inference rollout report."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Sequence

from sqlalchemy import select

# Direct execution (``python scripts/...``) places ``scripts/`` on sys.path.
# Keep the read-only staging command independent of the invoking shell's
# PYTHONPATH without importing or writing any runtime configuration.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if TYPE_CHECKING:
    from core.db import AsyncSessionLocal
    from models.coin_intelligence_inference_audit import CoinIntelligenceInferenceAudit
    from models.coin_intelligence_inference_outcome import CoinIntelligenceInferenceOutcome


async def _report(*, since_hours: int) -> dict[str, object]:
    # Import runtime settings only after CLI validation.  Invalid input must be
    # safely rejectable without a configured database or any runtime side effect.
    from core.db import AsyncSessionLocal
    from core.market_intelligence.coin_inference_rollout_metrics import (
        build_coin_inference_rollout_metrics,
    )
    from models.coin_intelligence_inference_audit import CoinIntelligenceInferenceAudit
    from models.coin_intelligence_inference_outcome import CoinIntelligenceInferenceOutcome

    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    async with AsyncSessionLocal() as session:
        decisions = list(
            (
                await session.execute(
                    select(CoinIntelligenceInferenceAudit).where(
                        CoinIntelligenceInferenceAudit.created_at >= cutoff
                    )
                )
            ).scalars()
        )
        outcomes = list(
            (
                await session.execute(
                    select(CoinIntelligenceInferenceOutcome).where(
                        CoinIntelligenceInferenceOutcome.created_at >= cutoff
                    )
                )
            ).scalars()
        )
    return build_coin_inference_rollout_metrics(decisions, outcomes)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since-hours",
        type=int,
        default=168,
        help="bounded read window; defaults to the previous seven days",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.since_hours <= 0 or args.since_hours > 24 * 31:
        parser_error = {"status": "INVALID_ARGUMENT", "reason": "since_hours_must_be_1_to_744"}
        print(json.dumps(parser_error, sort_keys=True, separators=(",", ":")))
        return 2
    try:
        report = asyncio.run(_report(since_hours=int(args.since_hours)))
    except Exception as exc:
        print(
            json.dumps(
                {"status": "UNAVAILABLE", "reason": type(exc).__name__},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 3
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
