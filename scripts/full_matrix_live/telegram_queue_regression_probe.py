#!/usr/bin/env python3
"""Exercise a reserved, durable Telegram Queue lane without contacting Telegram.

The campaign proves the real Bot-FI queue persistence, lease fence and result
state transitions for a publication, channel edit, callback and private
message.  It deliberately never instantiates a Telegram client or calls a
provider: the only provider fact is a local, typed ``TelegramGatewayResult``.
The reserved campaign/run lane is invisible to operational claim, recovery and
reconciliation workers, so this probe cannot consume an operational job.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from sqlalchemy import func, select


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings  # noqa: E402
from core.runtime_identity import resolve_runtime_identity  # noqa: E402
from core.server_routing import SERVER_FOREIGN  # noqa: E402
from core.telegram_delivery_queue_contract import (  # noqa: E402
    TelegramDeliveryAction,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.telegram_gateway import TelegramGatewayResult  # noqa: E402
from core.services.telegram_delivery_queue_service import (  # noqa: E402
    TELEGRAM_DELIVERY_CLAIM_SCOPE_FULL_MATRIX,
    TELEGRAM_DELIVERY_CLAIM_SCOPE_OPERATIONAL,
    TELEGRAM_PRIMARY_BOT_IDENTITY,
    build_full_matrix_telegram_test_run_id,
    claim_next_telegram_delivery_job,
    enqueue_telegram_delivery_job,
    mark_telegram_delivery_dispatch_started,
    resolve_telegram_delivery_result,
    telegram_delivery_claim_scope_filters,
)
from models.telegram_delivery_feeder_state import TelegramDeliveryFeederState  # noqa: E402
from models.telegram_delivery_job import TelegramDeliveryJobRecord  # noqa: E402
from scripts import trading_core_probe_worker as worker  # noqa: E402


SCHEMA = "three-site-full-matrix-telegram-queue-regression-probe-v1"
SCENARIO_ID = "queue_publication_edit_callback_private"
PREFIX_RE = re.compile(r"FMX_[A-Za-z0-9_]{12,96}")


class TelegramQueueRegressionProbeError(RuntimeError):
    """Raised when the bounded foreign-local queue regression is inconclusive."""


def _json(value: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _prefix(value: str) -> str:
    normalized = str(value or "").strip()
    if PREFIX_RE.fullmatch(normalized) is None:
        raise TelegramQueueRegressionProbeError("queue fixture prefix is unsafe")
    return normalized


def _assert_bot_fi_capability() -> None:
    identity = resolve_runtime_identity(settings)
    if (
        not identity.is_bot_site
        or identity.legacy_server_mode != SERVER_FOREIGN
        or str(getattr(settings, "server_mode", "") or "") != SERVER_FOREIGN
    ):
        raise TelegramQueueRegressionProbeError(
            "queue regression must run on the foreign Bot-FI authority"
        )
    if not bool(getattr(settings, "three_site_dr_enabled", False)):
        raise TelegramQueueRegressionProbeError(
            "queue regression requires the three-site runtime identity"
        )


def _fixture_specs(prefix: str) -> tuple[dict[str, Any], ...]:
    # These payloads are syntactically real queue shapes but deliberately use
    # non-routable fixture IDs.  They are never transmitted to Telegram.
    return (
        {
            "name": "publication",
            "feeder": TelegramFeederKind.OFFER_CONTROL,
            "action": TelegramDeliveryAction.OFFER_PUBLISH,
            "bot_identity": TELEGRAM_PRIMARY_BOT_IDENTITY,
            "destination_key": f"channel:full-matrix-{prefix}",
            "destination_class": TelegramDestinationClass.CHANNEL,
            "method": "sendMessage",
            "payload": {"chat_id": -1_000_000_001, "text": f"{prefix} publication"},
        },
        {
            "name": "channel_edit",
            "feeder": TelegramFeederKind.OFFER_EDIT,
            "action": TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            "bot_identity": "channel_editor",
            "destination_key": f"channel:full-matrix-{prefix}",
            "destination_class": TelegramDestinationClass.CHANNEL,
            "method": "editMessageText",
            "payload": {
                "chat_id": -1_000_000_001,
                "message_id": 9_000_001,
                "text": f"{prefix} channel edit",
            },
        },
        {
            "name": "callback",
            "feeder": TelegramFeederKind.DIRECT,
            "action": TelegramDeliveryAction.CALLBACK_DEADLINE,
            "bot_identity": TELEGRAM_PRIMARY_BOT_IDENTITY,
            "destination_key": f"private:full-matrix-{prefix}-callback",
            "destination_class": TelegramDestinationClass.PRIVATE,
            "method": "answerCallbackQuery",
            "payload": {"callback_query_id": f"{prefix}-callback"},
        },
        {
            "name": "private_message",
            "feeder": TelegramFeederKind.DIRECT,
            "action": TelegramDeliveryAction.GENERAL_IMMEDIATE,
            "bot_identity": TELEGRAM_PRIMARY_BOT_IDENTITY,
            "destination_key": f"private:full-matrix-{prefix}-private",
            "destination_class": TelegramDestinationClass.PRIVATE,
            "method": "sendMessage",
            "payload": {"chat_id": 1_000_000_001, "text": f"{prefix} private"},
        },
    )


async def _ensure_offer_edit_fairness_state() -> None:
    """Read-only preflight: the queue claimant is never allowed to seed it."""

    async with worker.AsyncSessionLocal() as db:
        state = await db.get(TelegramDeliveryFeederState, "offer_edit")
        await db.rollback()
    if state is None or not isinstance(state.fresh_success_counts, dict):
        raise TelegramQueueRegressionProbeError(
            "foreign queue fairness state is missing or invalid"
        )


async def _cleanup(*, campaign_id: str, run_id: str, prefix: str) -> dict[str, Any]:
    """Delete only known synthetic local queue rows from this exact run.

    There is no provider outcome record because the probe never calls a
    provider-outcome persistence path.  Refusing a marker mismatch makes this
    safe even if a future caller accidentally reuses the run identity.
    """

    async with worker.AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(TelegramDeliveryJobRecord).where(
                        TelegramDeliveryJobRecord.campaign_id == campaign_id,
                        TelegramDeliveryJobRecord.run_id == run_id,
                    )
                )
            ).scalars().all()
        )
        for row in rows:
            payload = row.payload if isinstance(row.payload, dict) else {}
            if (
                payload.get("full_matrix_queue_fixture") != prefix
                or payload.get("full_matrix_provider") != "fake-only"
                or not str(row.source_natural_id or "").startswith(prefix)
            ):
                raise TelegramQueueRegressionProbeError(
                    "refusing to delete a non-fixture queue row"
                )
            await db.delete(row)
        await db.flush()
        residue = int(
            await db.scalar(
                select(func.count())
                .select_from(TelegramDeliveryJobRecord)
                .where(
                    TelegramDeliveryJobRecord.campaign_id == campaign_id,
                    TelegramDeliveryJobRecord.run_id == run_id,
                )
            )
            or 0
        )
        if residue:
            raise TelegramQueueRegressionProbeError("queue fixture cleanup left residue")
        await db.commit()
    return {"deleted_rows": len(rows), "residue_zero": True}


async def _enqueue_fixtures(*, campaign_id: str, run_id: str, prefix: str) -> dict[str, dict[str, Any]]:
    fixtures: dict[str, dict[str, Any]] = {}
    async with worker.AsyncSessionLocal() as db:
        for source_version, specification in enumerate(_fixture_specs(prefix), start=1):
            name = str(specification["name"])
            payload = {
                **dict(specification["payload"]),
                "full_matrix_queue_fixture": prefix,
                "full_matrix_provider": "fake-only",
                "full_matrix_fixture_name": name,
            }
            enqueued = await enqueue_telegram_delivery_job(
                db,
                current_server=SERVER_FOREIGN,
                feeder=specification["feeder"],
                source_natural_id=f"{prefix}{name}",
                source_version=source_version,
                action=specification["action"],
                bot_identity=str(specification["bot_identity"]),
                destination_key=str(specification["destination_key"]),
                destination_class=specification["destination_class"],
                method=str(specification["method"]),
                payload=payload,
                template_version="full-matrix-fixture-v1",
                source_order_at=(
                    datetime.now(timezone.utc)
                    if specification["feeder"] == TelegramFeederKind.OFFER_EDIT
                    else None
                ),
                campaign_id=campaign_id,
                run_id=run_id,
            )
            if not enqueued.created:
                raise TelegramQueueRegressionProbeError("queue fixture unexpectedly deduplicated")
            fixtures[name] = {
                "job_id": int(enqueued.job.id),
                "method": str(specification["method"]),
                "action": specification["action"],
                "bot_identity": str(specification["bot_identity"]),
            }
        await db.commit()
    return fixtures


async def _assert_operational_lane_cannot_see_fixture(*, campaign_id: str, run_id: str) -> None:
    """Prove the ordinary worker predicate excludes every fixture row, read-only."""

    filters = telegram_delivery_claim_scope_filters(
        claim_scope=TELEGRAM_DELIVERY_CLAIM_SCOPE_OPERATIONAL,
    )
    async with worker.AsyncSessionLocal() as db:
        visible = int(
            await db.scalar(
                select(func.count())
                .select_from(TelegramDeliveryJobRecord)
                .where(
                    TelegramDeliveryJobRecord.campaign_id == campaign_id,
                    TelegramDeliveryJobRecord.run_id == run_id,
                    *filters,
                )
            )
            or 0
        )
        await db.rollback()
    if visible != 0:
        raise TelegramQueueRegressionProbeError(
            "operational Telegram worker can see the Full Matrix fixture lane"
        )


async def _no_op_lifecycle(*_args: Any, **_kwargs: Any) -> None:
    """Required only for callback guard wiring; it has no side effect."""


def _fake_result(method: str, index: int) -> TelegramGatewayResult:
    if method in {"sendMessage", "sendDocument"}:
        result: Any = {"message_id": 7_000_000 + index}
    else:
        result = True
    return TelegramGatewayResult(
        ok=True,
        method=method,
        status_code=200,
        response_json={"ok": True, "result": result},
    )


async def _claim_fence_and_resolve(
    *,
    campaign_id: str,
    run_id: str,
    bot_identity: str,
    expected_job_ids: set[int],
    outcome_index: int,
) -> int:
    """Run the durable claim/fence/result path with a typed local fake result."""

    worker_id = f"full-matrix-fake-provider:{outcome_index}"
    async with worker.AsyncSessionLocal() as db:
        job = await claim_next_telegram_delivery_job(
            db,
            current_server=SERVER_FOREIGN,
            bot_identity=bot_identity,
            worker_id=worker_id,
            request_timeout_seconds=10,
            lease_seconds=30,
            claim_scope=TELEGRAM_DELIVERY_CLAIM_SCOPE_FULL_MATRIX,
            full_matrix_campaign_id=campaign_id,
            full_matrix_run_id=run_id,
        )
        if job is None or int(job.id) not in expected_job_ids:
            raise TelegramQueueRegressionProbeError("full Matrix queue claimant returned an unexpected job")
        started = await mark_telegram_delivery_dispatch_started(
            db,
            current_server=SERVER_FOREIGN,
            job_id=int(job.id),
            worker_id=worker_id,
            lease_token=int(job.lease_token),
            dispatch_guard=_no_op_lifecycle,
        )
        if not started:
            raise TelegramQueueRegressionProbeError("queue fixture dispatch fence was not acquired")
        decision = await resolve_telegram_delivery_result(
            db,
            current_server=SERVER_FOREIGN,
            job_id=int(job.id),
            worker_id=worker_id,
            lease_token=int(job.lease_token),
            result=_fake_result(str(job.method), outcome_index),
            retry_after_safety_seconds=0.1,
            retry_base_seconds=1.0,
            retry_max_seconds=5.0,
            feedback=_no_op_lifecycle,
        )
        if decision.outcome not in {
            TelegramDeliveryOutcome.SENT,
            TelegramDeliveryOutcome.SENT_NOOP,
        }:
            raise TelegramQueueRegressionProbeError("fake provider outcome was not terminally applied")
        await db.commit()
    return int(job.id)


async def _execute(*, campaign_id: str, run_id: str, prefix: str) -> dict[str, Any]:
    fixtures = await _enqueue_fixtures(
        campaign_id=campaign_id,
        run_id=run_id,
        prefix=prefix,
    )
    await _assert_operational_lane_cannot_see_fixture(
        campaign_id=campaign_id,
        run_id=run_id,
    )
    remaining = {
        identity: {
            int(item["job_id"])
            for item in fixtures.values()
            if item["bot_identity"] == identity
        }
        for identity in {str(item["bot_identity"]) for item in fixtures.values()}
    }
    completed: set[int] = set()
    outcome_index = 0
    for bot_identity in sorted(remaining):
        while remaining[bot_identity]:
            outcome_index += 1
            job_id = await _claim_fence_and_resolve(
                campaign_id=campaign_id,
                run_id=run_id,
                bot_identity=bot_identity,
                expected_job_ids=remaining[bot_identity],
                outcome_index=outcome_index,
            )
            remaining[bot_identity].remove(job_id)
            completed.add(job_id)
    if completed != {int(item["job_id"]) for item in fixtures.values()}:
        raise TelegramQueueRegressionProbeError("not every queue fixture reached a terminal result")
    async with worker.AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(TelegramDeliveryJobRecord).where(
                        TelegramDeliveryJobRecord.campaign_id == campaign_id,
                        TelegramDeliveryJobRecord.run_id == run_id,
                    )
                )
            ).scalars().all()
        )
        await db.rollback()
    if len(rows) != len(fixtures) or any(
        row.state not in {TelegramDeliveryState.SENT, TelegramDeliveryState.SENT_NOOP}
        or row.provider_ok is not True
        or row.provider_status_code != 200
        for row in rows
    ):
        raise TelegramQueueRegressionProbeError("queue fixture terminal state is incomplete")
    return {
        "full_matrix_lane_is_reserved_and_operationally_invisible": True,
        "publication_edit_callback_and_private_jobs_enqueued": True,
        "each_fixture_job_claimed_under_lease_fence": True,
        "fake_provider_outcomes_applied_without_network_call": True,
        "all_fixture_jobs_terminal": True,
    }


async def run_probe(
    *,
    campaign_id: str,
    prefix: str,
    allow_production: bool,
    allow_cleanup: bool,
) -> dict[str, Any]:
    normalized_prefix = _prefix(prefix)
    worker.assert_production_full_matrix_allowed(
        normalized_prefix,
        allow_flag=allow_production,
    )
    if worker.is_production_runtime():
        worker.allow_production_cleanup_hard_delete(
            normalized_prefix,
            allow_flag=allow_cleanup,
        )
    _assert_bot_fi_capability()
    run_id = build_full_matrix_telegram_test_run_id(
        campaign_id=campaign_id,
        scenario_id=SCENARIO_ID,
        nonce=hashlib.sha256(normalized_prefix.encode("utf-8")).hexdigest()[:24],
    )
    await _ensure_offer_edit_fairness_state()
    await _cleanup(campaign_id=campaign_id, run_id=run_id, prefix=normalized_prefix)
    cleanup: dict[str, Any] | None = None
    observation: dict[str, Any] | None = None
    failure: BaseException | None = None
    try:
        observation = await _execute(
            campaign_id=campaign_id,
            run_id=run_id,
            prefix=normalized_prefix,
        )
    except BaseException as exc:
        failure = exc
    finally:
        cleanup = await _cleanup(
            campaign_id=campaign_id,
            run_id=run_id,
            prefix=normalized_prefix,
        )
    if failure is not None:
        raise TelegramQueueRegressionProbeError("queue regression execution raised") from failure
    if observation is None or cleanup is None:
        raise TelegramQueueRegressionProbeError("queue regression did not produce complete evidence")
    return {
        "schema": SCHEMA,
        "status": "passed",
        "scenario_id": SCENARIO_ID,
        "role": "bot_fi",
        "prefix": normalized_prefix,
        "campaign_id": campaign_id,
        "run_id": run_id,
        "observation": observation,
        "cleanup": {
            "only_exact_reserved_fixture_rows_deleted": True,
            "fixture_residue_zero": cleanup["residue_zero"],
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--allow-production-execution", action="store_true")
    parser.add_argument("--allow-production-cleanup", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(
            run_probe(
                campaign_id=str(args.campaign_id),
                prefix=str(args.prefix),
                allow_production=bool(args.allow_production_execution),
                allow_cleanup=bool(args.allow_production_cleanup),
            )
        )
    except Exception as exc:
        _json({"schema": SCHEMA, "status": "failed", "error_class": type(exc).__name__})
        return 1
    _json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
