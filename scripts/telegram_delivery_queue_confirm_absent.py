#!/usr/bin/env python3
"""Audited break-glass recovery for an absent ambiguous channel send.

This command never infers absence.  An accountable operator must first inspect
the configured channel, then provide an exact job id, evidence reference, and
confirmation phrase.  Only an ambiguous ``sendMessage`` to the channel class
can become retryable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.db import AsyncSessionLocal
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.telegram_delivery_reconciliation_service import (
    RECONCILIATION_CONFIRMED_ABSENT,
    resolve_ambiguous_telegram_delivery_job,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryState,
    TelegramDestinationClass,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord


CONFIRMATION_PHRASE = "CONFIRM TELEGRAM CHANNEL MESSAGE ABSENT"
_AMBIGUOUS_STATES = {
    TelegramDeliveryState.AMBIGUOUS.value,
    TelegramDeliveryState.AMBIGUOUS_UNRESOLVED.value,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retry one ambiguous channel send after positive absence evidence."
    )
    parser.add_argument("--job-id", required=True, type=int)
    parser.add_argument("--requested-by", required=True)
    parser.add_argument("--evidence-reference", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def _validated_text(value: str, *, reason: str, maximum: int = 256) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum or any(ord(char) < 32 for char in normalized):
        raise ValueError(reason)
    return normalized


def validate_confirmation(value: str) -> None:
    if str(value or "") != CONFIRMATION_PHRASE:
        raise ValueError("telegram_confirm_absent_confirmation_mismatch")


def validate_candidate(job: TelegramDeliveryJobRecord | None) -> None:
    if job is None:
        raise ValueError("telegram_confirm_absent_job_not_found")
    state = str(getattr(job.state, "value", job.state) or "").strip().lower()
    destination_class = str(
        getattr(job.destination_class, "value", job.destination_class) or ""
    ).strip().lower()
    if state not in _AMBIGUOUS_STATES:
        raise ValueError("telegram_confirm_absent_job_not_ambiguous")
    if str(job.method or "") != "sendMessage":
        raise ValueError("telegram_confirm_absent_method_not_send")
    if destination_class != TelegramDestinationClass.CHANNEL.value:
        raise ValueError("telegram_confirm_absent_destination_not_channel")


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
    if current_server() != SERVER_FOREIGN:
        raise ValueError("telegram_confirm_absent_is_foreign_local")
    if isinstance(args.job_id, bool) or int(args.job_id) <= 0:
        raise ValueError("telegram_confirm_absent_job_id_invalid")
    requested_by = _validated_text(
        args.requested_by,
        reason="telegram_confirm_absent_requested_by_invalid",
        maximum=128,
    )
    evidence_reference = _validated_text(
        args.evidence_reference,
        reason="telegram_confirm_absent_evidence_invalid",
    )

    async with AsyncSessionLocal() as db:
        try:
            job = (
                await db.execute(
                    select(TelegramDeliveryJobRecord)
                    .where(TelegramDeliveryJobRecord.id == int(args.job_id))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            validate_candidate(job)
            decision = await resolve_ambiguous_telegram_delivery_job(
                db,
                current_server=SERVER_FOREIGN,
                job_id=int(args.job_id),
                resolution=RECONCILIATION_CONFIRMED_ABSENT,
                evidence_reference=evidence_reference,
                operator_reference=requested_by,
                reason_code="operator_confirmed_channel_message_absent",
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return {
        "status": "completed",
        "job_id": int(args.job_id),
        "outcome": str(getattr(decision.outcome, "value", decision.outcome)),
        "reason": str(decision.reason or ""),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps(_safe_error(exc), sort_keys=True), file=sys.stderr)
        return 2 if isinstance(exc, ValueError) else 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
