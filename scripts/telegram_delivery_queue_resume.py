#!/usr/bin/env python3
"""Break-glass entrypoint for the audited Telegram channel resume saga.

The command has no SQL-only or Redis-only mode.  Its only mutating path runs
the complete PostgreSQL -> Telegram preflight -> Redis -> PostgreSQL contract.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import redis.asyncio as redis_async

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.telegram_delivery_resume_service import (
    TelegramDeliveryResumeError,
    resume_configured_telegram_channel,
)
from core.telegram_delivery_credentials import (
    configured_telegram_delivery_credentials,
)
from core.telegram_delivery_queue_limiter import (
    configured_redis_telegram_delivery_limiter,
)


CONFIRMATION_PHRASE = "RESUME CONFIGURED TELEGRAM CHANNEL"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fail-closed, audited resume saga for the configured Telegram channel."
        )
    )
    parser.add_argument(
        "--request-id",
        required=True,
        help="A unique idempotency identifier (16-128 characters).",
    )
    parser.add_argument(
        "--requested-by",
        required=True,
        help="The accountable on-call/operator identifier.",
    )
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"Must exactly equal: {CONFIRMATION_PHRASE}",
    )
    return parser


def validate_confirmation(value: str) -> None:
    if str(value or "") != CONFIRMATION_PHRASE:
        raise ValueError("telegram_resume_confirmation_mismatch")


def _safe_error(exc: BaseException) -> dict[str, str]:
    raw = str(exc or "").strip()
    reason = raw if raw.startswith("telegram_") else type(exc).__name__
    return {
        "status": "failed",
        "error_class": type(exc).__name__[:160],
        "reason": "".join(
            char if char.isalnum() or char in "._:-" else "_" for char in reason
        )[:500],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_confirmation(args.confirm)
    server = current_server()
    if server != SERVER_FOREIGN:
        raise ValueError("telegram_resume_is_foreign_local")
    credentials = configured_telegram_delivery_credentials(settings)
    redis_client = redis_async.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis_client.ping()
        limiter = configured_redis_telegram_delivery_limiter(
            redis_client,
            settings=settings,
        )
        report = await resume_configured_telegram_channel(
            session_factory=AsyncSessionLocal,
            current_server=server,
            settings=settings,
            credential_registry=credentials,
            dispatch_limiter=limiter,
            request_id=args.request_id,
            requested_by=args.requested_by,
        )
    finally:
        await redis_client.close()
    return {
        "status": "completed",
        "operation_id": report.operation_id,
        "request_id": report.request_id,
        "state": report.state,
        "destination_fingerprint": hashlib.sha256(
            report.destination_key.encode("utf-8")
        ).hexdigest()[:16],
        "resumed_job_count": len(report.resumed_job_ids),
        "attempt_count": report.attempt_count,
        "idempotent_replay": report.idempotent_replay,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except (TelegramDeliveryResumeError, ValueError) as exc:
        print(json.dumps(_safe_error(exc), sort_keys=True), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps(_safe_error(exc), sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
