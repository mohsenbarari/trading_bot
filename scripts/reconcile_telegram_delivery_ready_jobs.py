#!/usr/bin/env python3
"""Apply official freshness to claimable ready Queue-v1 jobs without Telegram I/O.

This operator path never sends, edits, or deletes a Telegram message. A SEND
freshness decision is left untouched. Terminal/stale jobs are closed through
the same freshness transitions the worker would persist after a claim.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


CONFIRMATION_PHRASE = "RECONCILE READY TELEGRAM JOBS BY FRESHNESS"


class TelegramReadyFreshnessReconcileError(RuntimeError):
    pass


class _DryRunRollback(Exception):
    def __init__(self, payload: dict[str, Any]):
        super().__init__("dry_run")
        self.payload = payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, choices=("staging", "synthetic-test"))
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--requested-by", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--max-rows", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if str(args.confirm or "") != CONFIRMATION_PHRASE:
        raise TelegramReadyFreshnessReconcileError(
            "telegram_ready_freshness_confirmation_mismatch"
        )
    requested_by = str(args.requested_by or "").strip()
    if len(requested_by) < 3 or len(requested_by) > 80:
        raise TelegramReadyFreshnessReconcileError(
            "telegram_ready_freshness_requested_by_invalid"
        )
    if int(args.max_rows) < 1 or int(args.max_rows) > 500:
        raise TelegramReadyFreshnessReconcileError(
            "telegram_ready_freshness_max_rows_invalid"
        )


def _validate_database(*, environment: str, expected_database_name: str, raw_url: str) -> None:
    target = make_url(raw_url)
    actual = str(target.database or "")
    expected = str(expected_database_name or "").strip()
    if actual != expected:
        raise TelegramReadyFreshnessReconcileError(
            "telegram_ready_freshness_database_name_mismatch"
        )
    normalized_environment = str(environment or "").strip().lower()
    normalized_database = expected.lower()
    if normalized_environment == "staging":
        if "staging" not in normalized_database or "prod" in normalized_database:
            raise TelegramReadyFreshnessReconcileError(
                "telegram_ready_freshness_staging_database_name_invalid"
            )
        return
    if normalized_environment == "synthetic-test":
        if "test" not in normalized_database and "scratch" not in normalized_database:
            raise TelegramReadyFreshnessReconcileError(
                "telegram_ready_freshness_synthetic_database_name_invalid"
            )
        return
    raise TelegramReadyFreshnessReconcileError(
        "telegram_ready_freshness_environment_invalid"
    )


def _redacted_report(report: Any, *, dry_run: bool, requested_by: str) -> dict[str, Any]:
    return {
        "status": "dry_run" if dry_run else "applied",
        "provider_network_calls": 0,
        "requested_by_present": bool(str(requested_by).strip()),
        "inspected_count": int(report.inspected_count),
        "still_fresh_count": int(report.still_fresh_count),
        "freshness_terminal_count": int(report.freshness_terminal_count),
        "retry_count": int(report.retry_count),
        "reclassify_count": int(report.reclassify_count),
        "configuration_blocked_count": int(report.configuration_blocked_count),
        "outcome_counts": dict(report.outcome_counts),
        "action_counts": dict(report.action_counts),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_args(args)
    from core.config import settings
    from core.db import AsyncSessionLocal
    from core.server_routing import SERVER_FOREIGN, current_server
    from core.services.telegram_delivery_reconciliation_service import (
        reconcile_ready_telegram_delivery_jobs_by_freshness,
    )
    from core.telegram_delivery_runtime_composition import (
        configured_telegram_delivery_freshness_registry,
        configured_telegram_delivery_lifecycle_registry,
    )
    from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES

    if current_server() != SERVER_FOREIGN:
        raise TelegramReadyFreshnessReconcileError(
            "telegram_ready_freshness_requires_foreign_server"
        )
    _validate_database(
        environment=args.environment,
        expected_database_name=args.expected_database_name,
        raw_url=str(settings.database_url),
    )
    channel_id = (
        settings.telegram_delivery_queue_expected_channel_id or settings.channel_id
    )
    freshness_registry = configured_telegram_delivery_freshness_registry(
        channel_id=channel_id
    )
    lifecycle_registry = configured_telegram_delivery_lifecycle_registry(
        channel_id=channel_id
    )
    identities = ("primary", "channel_editor", *TELEGRAM_PUBLISHER_IDENTITIES)
    validators = {
        identity: freshness_registry.build_lane_router(identity)
        for identity in identities
    }
    feedbacks = {
        identity: lifecycle_registry.build_lane_router(identity).apply_freshness
        for identity in identities
    }
    async with AsyncSessionLocal() as db:
        async with db.begin():
            report = await reconcile_ready_telegram_delivery_jobs_by_freshness(
                db,
                current_server=SERVER_FOREIGN,
                freshness_validators=validators,
                freshness_feedbacks=feedbacks,
                max_rows=int(args.max_rows),
                actor_kind="operator",
            )
            payload = _redacted_report(
                report,
                dry_run=bool(args.dry_run),
                requested_by=str(args.requested_by),
            )
            if args.dry_run:
                raise _DryRunRollback(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    try:
        payload = asyncio.run(run(build_parser().parse_args(argv)))
    except _DryRunRollback as exc:
        payload = exc.payload
    except TelegramReadyFreshnessReconcileError as exc:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "error_code": str(exc),
                    "provider_network_calls": 0,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if int(payload["still_fresh_count"]) or int(payload["configuration_blocked_count"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
