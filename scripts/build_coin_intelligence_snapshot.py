#!/usr/bin/env python3
"""Build one deterministic offline Shadow snapshot from normalized SQLite."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.bundle import load_runtime_bundle
from core.market_intelligence.producer import (
    CoinSnapshotProducer,
    SnapshotProducerError,
    write_snapshot_atomic,
)


def _utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "timestamp must be an ISO-8601 value"
        ) from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(
            "timestamp must include an explicit timezone"
        )
    return parsed.astimezone(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--conversation-db", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument(
        "--as-of",
        type=_utc_timestamp,
        help=(
            "UTC-aware source cutoff. Without it, the first complete minute "
            "after the newest normalized observation is used."
        ),
    )
    return parser


def _reject_overlapping_paths(
    *,
    market_db: Path,
    conversation_db: Path | None,
    output: Path,
) -> None:
    destination = output.resolve()
    inputs = {market_db.resolve()}
    if conversation_db is not None:
        inputs.add(conversation_db.resolve())
    if destination in inputs:
        raise SnapshotProducerError(
            "snapshot_output_must_not_replace_input_database"
        )


def main() -> int:
    args = build_parser().parse_args()
    _reject_overlapping_paths(
        market_db=args.market_db,
        conversation_db=args.conversation_db,
        output=args.output,
    )
    bundle = load_runtime_bundle(args.bundle)
    producer = CoinSnapshotProducer(bundle)
    if args.as_of is None:
        snapshot = producer.build_latest_complete_minute(
            market_db=args.market_db,
            conversation_db=args.conversation_db,
        )
    else:
        snapshot = producer.build(
            market_db=args.market_db,
            conversation_db=args.conversation_db,
            as_of=args.as_of,
        )
    write_snapshot_atomic(args.output, snapshot)
    print(
        json.dumps(
            {
                "status": "SHADOW_SNAPSHOT_WRITTEN",
                "output": str(args.output.resolve()),
                "snapshot_version": snapshot["snapshot_version"],
                "generated_at_utc": snapshot["generated_at_utc"],
                "bundle_version": snapshot["bundle_version"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
