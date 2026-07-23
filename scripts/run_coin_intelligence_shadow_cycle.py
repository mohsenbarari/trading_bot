#!/usr/bin/env python3
"""Run one fail-safe local Shadow market-intelligence cycle."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.bundle import (
    BundleValidationError,
    load_runtime_bundle,
)
from core.market_intelligence.pipeline import (
    CoinIntelligenceShadowPipeline,
    ShadowPipelineError,
)
from core.market_intelligence.telegram_collector.collector import (
    collect_telegram_market,
)
from core.market_intelligence.telegram_collector.config import (
    SOURCES,
    TelegramCollectorSettings,
    TelegramCredentials,
    source_for_username,
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
    parser.add_argument(
        "--acknowledge-shadow-only",
        action="store_true",
    )
    parser.add_argument("--market-db", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--health", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--conversation-db", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--as-of", type=_utc_timestamp)
    parser.add_argument(
        "--telegram-mode",
        choices=("none", "incremental", "backfill"),
        default="none",
    )
    parser.add_argument("--telegram-session", type=Path)
    parser.add_argument("--telegram-days", type=int, default=2)
    parser.add_argument("--telegram-batch-size", type=int, default=500)
    parser.add_argument("--telegram-request-wait-seconds", type=float)
    parser.add_argument(
        "--telegram-channel",
        action="append",
        help="Allowed public channel username; repeat as needed.",
    )
    return parser


def _reject_repository_runtime_paths(paths: list[Path]) -> None:
    repository = REPO_ROOT.resolve()
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved == repository or repository in resolved.parents:
            raise ValueError("pipeline_runtime_path_inside_repository")


def main() -> int:
    args = build_parser().parse_args()
    if not args.acknowledge_shadow_only:
        print(
            json.dumps(
                {
                    "status": "SHADOW_CYCLE_REJECTED",
                    "reason": (
                        "--acknowledge-shadow-only is required"
                    ),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    if args.telegram_mode != "none" and args.as_of is not None:
        print(
            json.dumps(
                {
                    "status": "SHADOW_CYCLE_REJECTED",
                    "reason": (
                        "explicit --as-of is forbidden during collection"
                    ),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    runtime_paths = [
        args.market_db,
        args.snapshot,
        args.health,
        args.lock,
    ]
    if args.conversation_db is not None:
        runtime_paths.append(args.conversation_db)
    if args.telegram_session is not None:
        runtime_paths.append(args.telegram_session)
    try:
        _reject_repository_runtime_paths(runtime_paths)
        bundle = load_runtime_bundle(args.bundle)
        collection_step = None
        if args.telegram_mode != "none":
            if args.telegram_session is None:
                raise ValueError(
                    "telegram_session_required_for_collection"
                )
            credentials = TelegramCredentials.from_environment()
            collector_settings = TelegramCollectorSettings(
                credentials=credentials,
                database_path=args.market_db,
                session_path=args.telegram_session,
            )
            collector_settings.validate_paths(
                repository_root=REPO_ROOT
            )
            sources = (
                tuple(
                    source_for_username(value)
                    for value in args.telegram_channel
                )
                if args.telegram_channel
                else SOURCES
            )

            def run_collection():
                return asyncio.run(
                    collect_telegram_market(
                        collector_settings,
                        sources=sources,
                        days=args.telegram_days,
                        resume_from_checkpoint=(
                            args.telegram_mode == "incremental"
                        ),
                        batch_size=args.telegram_batch_size,
                        request_wait_seconds=(
                            args.telegram_request_wait_seconds
                        ),
                    )
                )

            collection_step = run_collection

        result = CoinIntelligenceShadowPipeline(bundle).run(
            market_db=args.market_db,
            snapshot_path=args.snapshot,
            health_path=args.health,
            lock_path=args.lock,
            conversation_db=args.conversation_db,
            as_of=args.as_of,
            collection_step=collection_step,
        )
    except (
        BundleValidationError,
        ShadowPipelineError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "SHADOW_CYCLE_FAILED",
                    "reason": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "SHADOW_CYCLE_FAILED",
                    "reason": (
                        "shadow_cycle_unexpected:"
                        f"{type(exc).__name__}"
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "status": "SHADOW_CYCLE_COMPLETE",
                "snapshot_version": result.snapshot[
                    "snapshot_version"
                ],
                "cutoff_utc": result.health["cutoff_utc"],
                "input_quality": result.health["input_quality"][
                    "status"
                ],
                "snapshot": str(args.snapshot.resolve()),
                "health": str(args.health.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
