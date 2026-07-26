#!/usr/bin/env python3
"""Generate a privacy-bounded aggregate report from the Shadow ledger."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.market_intelligence.evaluation import (
    aggregate_shadow_scores,
    load_shadow_score_records,
    utc_now_iso,
)


def _utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "timestamp must be ISO-8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "timestamp must include an explicit timezone"
        )
    return parsed.astimezone(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-shadow-only", action="store_true")
    parser.add_argument("--start-utc", type=_utc_timestamp)
    parser.add_argument("--end-utc", type=_utc_timestamp)
    parser.add_argument("--output", type=Path)
    return parser


def _validate_output_path(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("evaluation_output_must_be_outside_repository")


def main() -> int:
    args = build_parser().parse_args()
    if not args.acknowledge_shadow_only:
        print(
            json.dumps(
                {
                    "status": "SHADOW_REPORT_REJECTED",
                    "reason": "--acknowledge-shadow-only is required",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    if (
        args.start_utc is not None
        and args.end_utc is not None
        and args.start_utc >= args.end_utc
    ):
        print(
            json.dumps(
                {
                    "status": "SHADOW_REPORT_REJECTED",
                    "reason": "start_utc_must_precede_end_utc",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        if args.output is not None:
            _validate_output_path(args.output)
        records = load_shadow_score_records(
            settings.sync_database_url,
            start_utc=args.start_utc,
            end_utc=args.end_utc,
        )
        report = aggregate_shadow_scores(records)
        report["generated_at_utc"] = utc_now_iso()
        report["window"] = {
            "start_utc": (
                args.start_utc.isoformat()
                if args.start_utc is not None
                else None
            ),
            "end_utc": (
                args.end_utc.isoformat()
                if args.end_utc is not None
                else None
            ),
        }
        encoded = json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        if args.output is None:
            print(encoded)
        else:
            output = args.output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(encoded + "\n", encoding="utf-8")
            print(
                json.dumps(
                    {
                        "status": "SHADOW_REPORT_COMPLETE",
                        "output": str(output),
                        "record_count": len(records),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "SHADOW_REPORT_FAILED",
                    "reason": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
