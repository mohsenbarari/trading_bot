#!/usr/bin/env python3
"""Small live staging smoke for Queue-v1 after cutover.

Creates one bot-origin offer, waits for sticky publication, expires it so the
same publisher edits the channel message, checks B2B acknowledgement, and
probes the private-message queue with a synthetic recipient. The probe reports
provider delivery only when it is proven; the expected invalid-recipient
quarantine is recorded as transport execution, not delivery. Every run cleans
its own fixture prefix, including failed and interrupted runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.offer_source import OfferSourceSurface
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.offer_creation_service import (
    OfferCreationCommand,
    OfferCreationQuotaPolicy,
    create_authoritative_offer_with_outcome,
)
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
    enqueue_telegram_action_notification_once,
)
from core.services.telegram_offer_publication_service import (
    get_or_create_telegram_publication_state,
    initial_telegram_publication_publisher_identity,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryState,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.telegram_multi_publisher_contract import (
    TELEGRAM_PUBLISHER_IDENTITIES,
    TelegramPublisherDispatchState,
)
from core.trading_settings import get_trading_settings_async
from models.offer import OfferStatus, OfferType
from models.user import User
from models.offer_publication_state import OfferPublicationState
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_notification_outbox import TelegramNotificationOutbox
from models.telegram_publisher_dispatch_command import TelegramPublisherDispatchCommand
from scripts.trading_core_probe_worker import (
    cleanup_prefix,
    create_load_fixture_users,
    expire_offer_for_user,
    resolve_commodity,
)


CONFIRM = "SMOKE STAGING TELEGRAM QUEUE-V1"
FINAL_JOB_STATES = {
    TelegramDeliveryState.SENT.value,
    TelegramDeliveryState.SENT_NOOP.value,
    TelegramDeliveryState.SUPERSEDED.value,
    TelegramDeliveryState.EXPIRED_INTERACTION.value,
    TelegramDeliveryState.PERMANENT_UNDELIVERABLE.value,
    TelegramDeliveryState.TERMINAL_FAILED.value,
    TelegramDeliveryState.QUARANTINED.value,
}
FINAL_COMMAND_STATES = {
    TelegramPublisherDispatchState.ACKNOWLEDGED.value,
    TelegramPublisherDispatchState.FAILED.value,
    TelegramPublisherDispatchState.SUPERSEDED.value,
}


class StagingSmokeError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _private_probe_terminal_report(
    outbox: TelegramNotificationOutbox | None,
    jobs: list[TelegramDeliveryJobRecord],
) -> dict[str, Any] | None:
    """Classify a synthetic private probe without claiming false delivery."""
    if outbox is None or not jobs:
        return None
    outbox_status = _enum(getattr(outbox, "status", None))
    for job in jobs:
        state = _enum(getattr(job, "state", None))
        reason = str(getattr(job, "outcome_reason", None) or "").strip()
        attempts = int(getattr(job, "provider_attempt_count", 0) or 0)
        if state in {
            TelegramDeliveryState.SENT.value,
            TelegramDeliveryState.SENT_NOOP.value,
        } and outbox_status == "sent":
            return {
                "queue_terminal_outcome": state,
                "provider_delivery_proven": True,
                "provider_attempt_count": attempts,
                "outbox_status": outbox_status,
            }
        if (
            state == TelegramDeliveryState.QUARANTINED.value
            and reason == "telegram_unknown_client_error"
            and attempts >= 1
        ):
            return {
                "queue_terminal_outcome": "synthetic_recipient_quarantined",
                "provider_delivery_proven": False,
                "provider_attempt_count": attempts,
                "outbox_status": outbox_status,
                "reason": reason,
            }
        if state in FINAL_JOB_STATES:
            raise StagingSmokeError(
                f"private_notification_unexpected_terminal:{state}:{reason or 'unspecified'}"
            )
    return None


async def _private_probe_state(
    run_id: str,
) -> tuple[TelegramNotificationOutbox | None, list[TelegramDeliveryJobRecord]]:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(TelegramNotificationOutbox).where(
                    TelegramNotificationOutbox.source_id == run_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None, []
        queue_job_id = getattr(row, "queue_job_id", None)
        if queue_job_id is not None:
            statement = select(TelegramDeliveryJobRecord).where(
                TelegramDeliveryJobRecord.id == int(queue_job_id)
            )
        else:
            statement = select(TelegramDeliveryJobRecord).where(
                TelegramDeliveryJobRecord.action_kind
                == TelegramDeliveryAction.GENERAL_IMMEDIATE,
                TelegramDeliveryJobRecord.source_natural_id == run_id,
            )
        jobs = (await db.execute(statement)).scalars().all()
        return row, list(jobs)


async def _wait(predicate, *, timeout: float, label: str) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = await predicate()
        if last:
            return last
        await asyncio.sleep(1.0)
    raise StagingSmokeError(f"{label}_timeout")


async def _publication(offer_id: int) -> OfferPublicationState | None:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                select(OfferPublicationState).where(
                    OfferPublicationState.offer_id == offer_id
                )
            )
        ).scalar_one_or_none()


async def _jobs(public_id: str) -> list[TelegramDeliveryJobRecord]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(TelegramDeliveryJobRecord).where(
                    TelegramDeliveryJobRecord.source_natural_id == public_id
                )
            )
        ).scalars().all()
        return list(rows)


async def _commands_for_jobs(job_ids: list[int]) -> list[TelegramPublisherDispatchCommand]:
    if not job_ids:
        return []
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(TelegramPublisherDispatchCommand).where(
                    TelegramPublisherDispatchCommand.job_id.in_(job_ids)
                )
            )
        ).scalars().all()
        return list(rows)


async def _run_smoke_scenario(
    *,
    run_id: str,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    users = await create_load_fixture_users(run_id, user_count=3)
    owner = users[0]
    commodity_id, commodity_name = await resolve_commodity()
    trading_settings = await get_trading_settings_async()
    from bot.utils.offer_parser import parse_offer_text
    from scripts.trading_core_probe_worker import build_bot_offer_text

    text_value, _marker = build_bot_offer_text(
        owner_user_id=owner.user_id,
        commodity_name=commodity_name,
        prefix=f"{run_id}-0001",
        quantity=5,
        price=100000,
        offer_type="sell",
        is_wholesale=True,
        lot_sizes=None,
    )
    parsed, parse_error = await parse_offer_text(text_value)
    if parsed is None or parse_error is not None:
        raise StagingSmokeError("smoke_offer_parse_failed")
    async with AsyncSessionLocal() as db:
        outcome = await create_authoritative_offer_with_outcome(
            db,
            OfferCreationCommand(
                source_surface=OfferSourceSurface.TELEGRAM_BOT,
                owner_user_id=owner.user_id,
                actor_user_id=owner.user_id,
                offer_type=OfferType.SELL,
                settlement_type=parsed.settlement_type,
                commodity_id=parsed.commodity_id,
                quantity=parsed.quantity,
                price=parsed.price,
                is_wholesale=True,
                lot_sizes=None,
                original_lot_sizes=None,
                notes=parsed.notes,
                status=OfferStatus.ACTIVE,
            ),
            commit=False,
            refresh=False,
            validate_market=True,
            enforce_market_admission=True,
            quota_policy=OfferCreationQuotaPolicy(
                max_active_offers=int(trading_settings.max_active_offers),
            ),
        )
        await db.flush()
        await get_or_create_telegram_publication_state(
            db,
            outcome.offer,
            publisher_bot_identity=initial_telegram_publication_publisher_identity(
                multi_publisher_enabled=True,
                b2b_dispatch_enabled=True,
            ),
        )
        await db.commit()
        offer_id = int(outcome.offer.id)
        public_id = str(outcome.offer.offer_public_id)
    receipt["assertions"]["offer_created"] = True

    async def published() -> dict[str, Any] | None:
        state = await _publication(offer_id)
        jobs = await _jobs(public_id)
        if state is None:
            return None
        publisher = str(getattr(state, "publisher_bot_identity", "") or "")
        has_message = bool(getattr(state, "telegram_message_id", None))
        sent = [
            job
            for job in jobs
            if _enum(job.state) == TelegramDeliveryState.SENT.value
            and str(job.bot_identity) == publisher
        ]
        if publisher in TELEGRAM_PUBLISHER_IDENTITIES and has_message and sent:
            return {"publisher": publisher, "sent_jobs": len(sent)}
        return None

    published_report = await _wait(published, timeout=90.0, label="initial_publish")
    publisher = published_report["publisher"]
    receipt["assertions"]["sticky_publisher"] = publisher
    receipt["assertions"]["initial_publish"] = True

    await expire_offer_for_user(user_id=owner.user_id, offer_id=offer_id)

    async def edited() -> bool:
        state = await _publication(offer_id)
        if state is None:
            return False
        if str(getattr(state, "publisher_bot_identity", "") or "") != publisher:
            raise StagingSmokeError("cross_owner_edit")
        jobs = await _jobs(public_id)
        return any(
            _enum(job.action_kind)
            in {
                "expired_offer_edit",
                "cancelled_offer_edit",
                "partial_offer_edit",
                "offer_success",
            }
            and _enum(job.state) in FINAL_JOB_STATES
            and str(job.bot_identity) == publisher
            for job in jobs
        )

    await _wait(edited, timeout=90.0, label="same_publisher_edit")
    receipt["assertions"]["same_publisher_edit"] = True

    async def b2b_done() -> dict[str, Any] | None:
        jobs = await _jobs(public_id)
        commands = await _commands_for_jobs([int(job.id) for job in jobs])
        if not commands:
            return None
        open_commands = [item for item in commands if _enum(item.state) not in FINAL_COMMAND_STATES]
        acked = [
            item
            for item in commands
            if _enum(item.state) == TelegramPublisherDispatchState.ACKNOWLEDGED.value
        ]
        if open_commands:
            return None
        if not acked:
            return None
        return {"command_count": len(commands), "acknowledged": len(acked)}

    b2b = await _wait(b2b_done, timeout=90.0, label="b2b_ack")
    receipt["assertions"]["b2b"] = b2b

    async with AsyncSessionLocal() as db:
        owner_row = await db.get(User, int(owner.user_id))
        if owner_row is None:
            raise StagingSmokeError("smoke_owner_missing")
        await enqueue_telegram_action_notification_once(
            db,
            recipient=TelegramNotificationRecipient(
                user_id=int(owner.user_id),
                telegram_id=int(owner.telegram_id),
            ),
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            source_id=run_id,
            text="صف staging یک پیام خصوصی آزمایشی فرستاد",
            user_sync_version=int(owner_row.sync_version or 1),
        )
        await db.commit()

    async def private_done() -> dict[str, Any] | None:
        row, jobs = await _private_probe_state(run_id)
        return _private_probe_terminal_report(row, jobs)

    private_report = await _wait(
        private_done,
        timeout=90.0,
        label="private_notification_queue_probe",
    )
    receipt["assertions"]["private_notification_queue_probe"] = private_report

    leftover = await _jobs(public_id)
    open_leftover = [job for job in leftover if _enum(job.state) not in FINAL_JOB_STATES]
    if open_leftover:
        raise StagingSmokeError("smoke_open_jobs_remain")
    return receipt


def _validate_smoke_preconditions(confirm: str) -> None:
    if confirm != CONFIRM:
        raise StagingSmokeError("smoke_confirmation_mismatch")
    if str(getattr(settings, "environment", "")).strip().lower() != "staging":
        raise StagingSmokeError("smoke_requires_staging")
    if current_server() != SERVER_FOREIGN:
        raise StagingSmokeError("smoke_requires_foreign_bot")
    runtime = configured_telegram_delivery_runtime()
    if runtime.mode != TelegramDeliveryRuntimeMode.QUEUE_V1 or not runtime.queue_worker_enabled:
        raise StagingSmokeError("smoke_requires_queue_v1")


async def run_smoke(*, confirm: str, artifact_dir: Path) -> dict[str, Any]:
    _validate_smoke_preconditions(confirm)
    run_id = (
        "telegram-queue-smoke-"
        + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
        + "-"
        + secrets.token_hex(4)
    )
    receipt: dict[str, Any] = {
        "schema_version": 2,
        "command": "smoke",
        "environment": "staging",
        "production_authorized": False,
        "run_id": run_id,
        "started_at": _utc_now(),
        "assertions": {},
    }
    failure: BaseException | None = None
    try:
        await _run_smoke_scenario(run_id=run_id, receipt=receipt)
    except BaseException as exc:
        failure = exc
        receipt["status"] = "blocked"
        receipt["error_code"] = (
            str(exc) if isinstance(exc, StagingSmokeError) else type(exc).__name__
        )

    try:
        cleanup = await cleanup_prefix(run_id, dry_run=False)
        receipt["assertions"]["cleanup"] = {
            "status": cleanup.get("status"),
            "deleted_users": cleanup.get("deleted_users") or cleanup.get("user_count"),
        }
    except BaseException as cleanup_exc:
        receipt["assertions"]["cleanup"] = {
            "status": "failed",
            "error_class": type(cleanup_exc).__name__,
        }
        prior = type(failure).__name__ if failure is not None else "none"
        failure = StagingSmokeError(f"smoke_cleanup_failed_after:{prior}")

    if failure is None:
        receipt["status"] = "passed"
    receipt["finished_at"] = _utc_now()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{run_id}.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    receipt["artifact"] = str(path)
    if failure is not None:
        raise failure
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", default="")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("/tmp/telegram-queue-cutover-staging"),
    )
    args = parser.parse_args()
    try:
        payload = asyncio.run(run_smoke(confirm=args.confirm, artifact_dir=args.artifact_dir))
    except StagingSmokeError as exc:
        print(json.dumps({"status": "blocked", "error_code": str(exc)}, sort_keys=True))
        return 4
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
