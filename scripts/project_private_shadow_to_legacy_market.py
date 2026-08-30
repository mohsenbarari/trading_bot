#!/usr/bin/env python3
"""Project Shadow private-gold facts into the Legacy Market Store.

Only PRIVATE_GOLD_CHANNEL and PRIVATE_GOLD_PAPER_MINUTE are accepted.
The Shadow store is opened read-only.  This is not an evaluation rebuild.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.shadow_legacy_bridge import (
    AUTHORIZED_CUTOFF_UTC,
    PRIVATE_SOURCES,
    BridgeError,
    project_shadow_to_legacy_market,
)
from core.market_intelligence.market_store import MarketStoreError
from core.market_intelligence.market_contracts import MarketStoreContractError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow-market-store", type=Path, required=True)
    parser.add_argument("--legacy-market-store", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--cutoff-utc", default=AUTHORIZED_CUTOFF_UTC)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--full-reconcile", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = project_shadow_to_legacy_market(
            source=args.shadow_market_store,
            destination=args.legacy_market_store,
            ledger=args.ledger,
            sources=PRIVATE_SOURCES,
            cutoff_utc=args.cutoff_utc,
            dry_run=args.dry_run,
            force_full_reconcile=args.full_reconcile,
        )
    except (
        BridgeError,
        MarketStoreError,
        MarketStoreContractError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {"status": "FAILED", "reason": type(exc).__name__},
                sort_keys=True,
            ),
            flush=True,
        )
        return 2
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
