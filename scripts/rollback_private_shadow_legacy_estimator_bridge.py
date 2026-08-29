#!/usr/bin/env python3
"""Non-destructive rollback of the Shadow → Legacy compatibility bridge.

Stops at deactivating projected rows via the official ledger.  It never
deletes Shadow state, archives, sessions, or collector unit files.
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
    MARKET_BRIDGE_SOURCES,
    BridgeError,
    deactivate_projected_rows,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-market-store", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = deactivate_projected_rows(
            destination=args.legacy_market_store,
            ledger=args.ledger,
            sources=MARKET_BRIDGE_SOURCES,
        )
    except (BridgeError, OSError, ValueError) as exc:
        print(
            json.dumps({"status": "FAILED", "reason": type(exc).__name__}, sort_keys=True),
            flush=True,
        )
        return 2
    print(json.dumps({"status": "DEACTIVATED", **result}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
