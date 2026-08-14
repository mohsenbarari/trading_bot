#!/usr/bin/env python3
"""Break-glass resume for durable Telegram bot and gateway runtime gates."""
from __future__ import annotations

import argparse
import asyncio
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
from core.services.telegram_delivery_runtime_gate_service import (
    TelegramRuntimeGateError,
    resume_telegram_runtime_gate,
)
from core.telegram_delivery_credentials import (
    configured_telegram_delivery_credentials,
)
from core.telegram_delivery_queue_limiter import (
    configured_redis_telegram_delivery_limiter,
)


CONFIRMATION_PHRASE = "RESUME TELEGRAM RUNTIME GATE"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the crash-safe PostgreSQL/preflight/Redis resume saga for a "
            "Telegram bot or the Telegram gateway."
        )
    )
    parser.add_argument("--scope", required=True, choices=("bot", "gateway"))
    parser.add_argument(
        "--bot-identity",
        choices=("primary", "channel_editor"),
        help="Required for bot scope and forbidden for gateway scope.",
    )
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--requested-by", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if str(args.confirm or "") != CONFIRMATION_PHRASE:
        raise ValueError("telegram_runtime_resume_confirmation_mismatch")
    if args.scope == "bot" and not args.bot_identity:
        raise ValueError("telegram_runtime_resume_bot_identity_required")
    if args.scope == "gateway" and args.bot_identity is not None:
        raise ValueError("telegram_runtime_resume_gateway_identity_forbidden")


def _safe_error(exc: BaseException) -> dict[str, str]:
    raw = str(exc or "").strip()
    reason = raw if raw.startswith("telegram_") else type(exc).__name__
    safe_reason = "".join(
        char if char.isalnum() or char in "._:-" else "_" for char in reason
    )[:500]
    return {
        "status": "failed",
        "error_class": type(exc).__name__[:160],
        "reason": safe_reason,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_args(args)
    server = current_server()
    if server != SERVER_FOREIGN:
        raise ValueError("telegram_runtime_resume_is_foreign_local")
    credentials = configured_telegram_delivery_credentials(settings)
    redis_client = redis_async.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis_client.ping()
        limiter = configured_redis_telegram_delivery_limiter(
            redis_client,
            settings=settings,
        )
        report = await resume_telegram_runtime_gate(
            session_factory=AsyncSessionLocal,
            current_server=server,
            settings=settings,
            credential_registry=credentials,
            dispatch_limiter=limiter,
            scope=args.scope,
            bot_identity=args.bot_identity,
            request_id=args.request_id,
            requested_by=args.requested_by,
        )
    finally:
        await redis_client.close()
    return {
        "status": "completed",
        "scope": report.scope,
        "gate_key": report.gate_key,
        "request_id": report.request_id,
        "state": report.state,
        "resumed_job_count": len(report.resumed_job_ids),
        "idempotent_replay": report.idempotent_replay,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except (TelegramRuntimeGateError, ValueError) as exc:
        print(json.dumps(_safe_error(exc), sort_keys=True), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps(_safe_error(exc), sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
