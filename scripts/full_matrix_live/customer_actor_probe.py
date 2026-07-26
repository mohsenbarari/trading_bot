#!/usr/bin/env python3
"""Execute one real, bounded customer/owner lifecycle matrix on its writer.

The probe deliberately uses the application's service/router paths rather than
manufacturing database rows.  External Telegram, push and realtime transports
are replaced by the same in-process boundaries used by the production-path
load worker; all database mutations, policy checks, request ledgers and
outbox/event listeners remain real.  Every invocation owns an ``FMX_`` prefix
and destroys only that prefix after producing a compact, non-secret result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.three_site_full_matrix_campaign import (  # noqa: E402
    CUSTOMER_ACTOR_PAIR_POLICIES,
    CUSTOMER_LIFECYCLE_MATRIX,
    customer_actor_pair_contracts,
)
from scripts import trading_core_probe_worker as worker  # noqa: E402


SCHEMA = "three-site-full-matrix-customer-actor-probe-v1"
PREFIX_RE = re.compile(r"FMX_[A-Za-z0-9_]{12,96}")
# WA-FI and WA-IR are physical replicas of one logical WebApp authority.  They
# therefore share the legacy WebApp server mode; writer/standby is determined
# exclusively by the signed, Witness-bound writer state.
ROLE_TO_SERVER = {"webapp_fi": "iran", "webapp_ir": "iran"}


class CustomerActorProbeError(RuntimeError):
    """A bounded customer actor probe failed its source-owned contract."""


def _json(value: Any) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _pair_parts(actor_pair: str) -> tuple[str, str, str]:
    try:
        source_kind, remaining = actor_pair.split("__", 1)
    except ValueError as exc:
        raise CustomerActorProbeError("customer actor pair is malformed") from exc
    relation = ""
    responder_kind = ""
    for candidate in ("same_owner", "other_owner", "none"):
        suffix = f"_{candidate}"
        if remaining.endswith(suffix):
            relation = candidate
            responder_kind = remaining[: -len(suffix)]
            break
    if actor_pair == "user__user":
        responder_kind, relation = "user", "none"
    if (
        source_kind not in {"user", "tier1", "tier2"}
        or responder_kind not in {"user", "tier1", "tier2"}
        or relation not in {"none", "same_owner", "other_owner"}
    ):
        raise CustomerActorProbeError("customer actor pair has an unsupported component")
    if relation == "none" and actor_pair != "user__user":
        raise CustomerActorProbeError("only user__user may use the no-owner relation")
    return source_kind, responder_kind, relation


def _assert_runtime(*, scenario_id: str, writer_role: str, prefix: str, allow_production: bool) -> None:
    state = CUSTOMER_LIFECYCLE_MATRIX.get(scenario_id)
    if state is None or state["webapp_writer"] != writer_role:
        raise CustomerActorProbeError("customer lifecycle writer identity is invalid")
    expected_server = ROLE_TO_SERVER.get(writer_role)
    if expected_server is None:
        raise CustomerActorProbeError("customer lifecycle writer role is unsupported")
    worker.assert_production_full_matrix_allowed(prefix, allow_flag=allow_production)
    if str(worker.current_server()) != expected_server:
        raise CustomerActorProbeError("customer actor probe is not running on the lifecycle writer")


async def _side_effect_counts(user_ids: list[int]) -> dict[str, int]:
    """Count only the bounded fixture graph; never disclose fixture identities."""

    async with worker.AsyncSessionLocal() as db:
        offer_ids = [
            int(value)
            for value in (
                await db.execute(
                    worker.select(worker.Offer.id).where(
                        worker.or_(
                            worker.Offer.user_id.in_(user_ids),
                            worker.Offer.actor_user_id.in_(user_ids),
                        )
                    )
                )
            ).scalars().all()
        ]
        offer_public_ids = [
            str(value)
            for value in (
                await db.execute(
                    worker.select(worker.Offer.offer_public_id).where(worker.Offer.id.in_(offer_ids))
                )
            ).scalars().all()
        ] if offer_ids else []
        counts = {
            "offers": int(len(offer_ids)),
            "publication_states": int(
                await db.scalar(
                    worker.select(worker.func.count(worker.OfferPublicationState.id)).where(
                        worker.or_(
                            worker.OfferPublicationState.offer_id.in_(offer_ids) if offer_ids else worker.false(),
                            worker.OfferPublicationState.offer_public_id.in_(offer_public_ids)
                            if offer_public_ids else worker.false(),
                        )
                    )
                ) or 0
            ),
            "trades": int(
                await db.scalar(
                    worker.select(worker.func.count(worker.Trade.id)).where(
                        worker.or_(
                            worker.Trade.offer_id.in_(offer_ids) if offer_ids else worker.false(),
                            worker.Trade.offer_user_id.in_(user_ids),
                            worker.Trade.responder_user_id.in_(user_ids),
                            worker.Trade.actor_user_id.in_(user_ids),
                        )
                    )
                ) or 0
            ),
            "offer_requests": int(
                await db.scalar(
                    worker.select(worker.func.count(worker.OfferRequest.id)).where(
                        worker.or_(
                            worker.OfferRequest.local_offer_id.in_(offer_ids) if offer_ids else worker.false(),
                            worker.OfferRequest.offer_public_id.in_(offer_public_ids)
                            if offer_public_ids else worker.false(),
                            worker.OfferRequest.requester_user_id.in_(user_ids),
                            worker.OfferRequest.actor_user_id.in_(user_ids),
                        )
                    )
                ) or 0
            ),
            "notifications": int(
                await db.scalar(
                    worker.select(worker.func.count(worker.Notification.id)).where(
                        worker.Notification.user_id.in_(user_ids)
                    )
                ) or 0
            ),
        }
    return counts


def _count_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    if set(before) != set(after):
        raise CustomerActorProbeError("side-effect counter fields differ")
    return {key: int(after[key]) - int(before[key]) for key in sorted(before)}


def _phase_payload_safe(payload: dict[str, Any], forbidden_values: list[str]) -> bool:
    """Evidence retains only the boolean; values such as mobiles never leave memory."""

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    lowered = serialized.lower()
    return not any(value and value in serialized for value in forbidden_values) and not any(
        token in lowered for token in ("mobile_number", "phone_number", "phone")
    )


async def _offer_evidence(offer_id: int, *, requester_user_id: int) -> dict[str, Any]:
    evidence = await worker.negative_guard_offer_evidence(offer_id)
    offer = evidence.get("offer")
    if not isinstance(offer, dict):
        raise CustomerActorProbeError("offer evidence is incomplete")
    return {
        "status": str(offer.get("status") or ""),
        "remaining_quantity": offer.get("remaining_quantity"),
        "trade_count": int(evidence.get("trade_count") or 0),
        "offer_request_count": int(evidence.get("offer_request_count") or 0),
        "offer_request_status_counts": dict(evidence.get("offer_request_status_counts") or {}),
        "offer_request_public_failure_code_counts": dict(
            evidence.get("offer_request_public_failure_code_counts") or {}
        ),
        "offer_requests": [
            {
                "requester_matches": row.get("requester_user_id") == requester_user_id,
                "actor_matches": row.get("actor_user_id") == requester_user_id,
                "result_status": row.get("result_status"),
                "resulting_trade": row.get("resulting_trade_id") is not None,
            }
            for row in list(evidence.get("offer_requests") or [])
            if isinstance(row, dict)
        ],
    }


def _terminal_ledger_matches(
    evidence: dict[str, Any],
) -> bool:
    rows = evidence.get("offer_requests")
    if not isinstance(rows, list):
        return False
    return any(
        row.get("requester_matches") is True
        and row.get("actor_matches") is True
        and row.get("result_status") == "completed_trade"
        and row.get("resulting_trade") is True
        for row in rows
        if isinstance(row, dict)
    )


async def _run_positive_all_surfaces(
    *,
    prefix: str,
    fixture: dict[str, Any],
    commodity_id: int,
    pair_index: int,
) -> dict[str, Any]:
    source = fixture["source_actor"]
    responder = fixture["responder_actor"]
    if not isinstance(source, worker.LoadUserRef) or not isinstance(responder, worker.LoadUserRef):
        raise CustomerActorProbeError("customer fixture actors are invalid")

    web_offer_id = await worker.create_offer_for_user(
        user_id=source.user_id,
        commodity_id=commodity_id,
        prefix=f"{prefix}web-",
        index=pair_index * 10 + 1,
        quantity=1,
        price=100000,
        is_wholesale=True,
    )
    web_phase: dict[str, Any] = {}
    web_status = await worker.execute_webapp_trade_for_user(
        user_id=responder.user_id,
        offer_id=web_offer_id,
        quantity=1,
        idempotency_key=worker.build_role_attempt_idempotency_key(
            prefix=prefix, role="customer-web", offer_id=web_offer_id, attempt_index=pair_index
        ),
        phase_details=web_phase,
    )
    telegram_offer_id = await worker.create_offer_for_user(
        user_id=source.user_id,
        commodity_id=commodity_id,
        prefix=f"{prefix}telegram-",
        index=pair_index * 10 + 2,
        quantity=1,
        price=100000,
        is_wholesale=True,
        channel_message_id=900000 + pair_index,
        source_surface=worker.OfferSourceSurface.TELEGRAM_BOT,
    )
    harness = worker.AiogramDispatcherHarness()
    telegram_phase: dict[str, Any] = {}
    try:
        telegram_status = await worker.execute_bot_trade_with_dispatcher(
            harness=harness,
            spec=worker.MixedLoadAttemptSpec(
                index=pair_index,
                surface="telegram",
                user_id=responder.user_id,
                telegram_id=responder.telegram_id,
            ),
            offer=await worker.load_offer_snapshot(telegram_offer_id),
            amount=1,
            prefix=f"{prefix}telegram-",
            phase_details=telegram_phase,
        )
        telegram_sent_count = len(harness.telegram.sent_messages)
    finally:
        await harness.close()
    web_evidence = await _offer_evidence(web_offer_id, requester_user_id=responder.user_id)
    telegram_evidence = await _offer_evidence(telegram_offer_id, requester_user_id=responder.user_id)
    privacy_safe = _phase_payload_safe(
        web_phase,
        [str(source.telegram_id), str(responder.telegram_id)],
    ) and _phase_payload_safe(
        telegram_phase,
        [str(source.telegram_id), str(responder.telegram_id)],
    )
    passed = (
        web_status == "success"
        and telegram_status == "success"
        and web_evidence["trade_count"] == 1
        and telegram_evidence["trade_count"] == 1
        and _terminal_ledger_matches(web_evidence)
        and _terminal_ledger_matches(telegram_evidence)
        and privacy_safe
    )
    return {
        "passed": passed,
        "result": "eligible_surface_trade_completed",
        "webapp_status": web_status,
        "telegram_status": telegram_status,
        "webapp": web_evidence,
        "telegram": telegram_evidence,
        "telegram_sent_count": telegram_sent_count,
        "counterparty_privacy_preserved": privacy_safe,
    }


async def _run_tier2_request_policy(
    *,
    prefix: str,
    fixture: dict[str, Any],
    commodity_id: int,
    pair_index: int,
) -> dict[str, Any]:
    source = fixture["source_actor"]
    responder = fixture["responder_actor"]
    if not isinstance(source, worker.LoadUserRef) or not isinstance(responder, worker.LoadUserRef):
        raise CustomerActorProbeError("customer fixture actors are invalid")
    web_offer_id = await worker.create_offer_for_user(
        user_id=source.user_id,
        commodity_id=commodity_id,
        prefix=f"{prefix}web-",
        index=pair_index * 10 + 1,
        quantity=1,
        price=100000,
        is_wholesale=True,
    )
    web_status = await worker.execute_webapp_trade_for_user(
        user_id=responder.user_id,
        offer_id=web_offer_id,
        quantity=1,
        idempotency_key=worker.build_role_attempt_idempotency_key(
            prefix=prefix, role="tier2-web", offer_id=web_offer_id, attempt_index=pair_index
        ),
    )
    telegram_offer_id = await worker.create_offer_for_user(
        user_id=source.user_id,
        commodity_id=commodity_id,
        prefix=f"{prefix}telegram-",
        index=pair_index * 10 + 2,
        quantity=1,
        price=100000,
        is_wholesale=True,
        channel_message_id=910000 + pair_index,
        source_surface=worker.OfferSourceSurface.TELEGRAM_BOT,
    )
    harness = worker.AiogramDispatcherHarness()
    telegram_phase: dict[str, Any] = {}
    try:
        telegram_status = await worker.execute_bot_trade_with_dispatcher(
            harness=harness,
            spec=worker.MixedLoadAttemptSpec(
                index=pair_index,
                surface="telegram",
                user_id=responder.user_id,
                telegram_id=responder.telegram_id,
            ),
            offer=await worker.load_offer_snapshot(telegram_offer_id),
            amount=1,
            prefix=f"{prefix}tier2-telegram-",
            phase_details=telegram_phase,
        )
        telegram_sent_count = len(harness.telegram.sent_messages)
    finally:
        await harness.close()
    web_evidence = await _offer_evidence(web_offer_id, requester_user_id=responder.user_id)
    telegram_evidence = await _offer_evidence(telegram_offer_id, requester_user_id=responder.user_id)
    privacy_safe = _phase_payload_safe(
        telegram_phase,
        [str(source.telegram_id), str(responder.telegram_id)],
    )
    passed = (
        web_status == "success"
        and telegram_status == "rejected"
        and web_evidence["trade_count"] == 1
        and telegram_evidence["trade_count"] == 0
        and _terminal_ledger_matches(web_evidence)
        and privacy_safe
    )
    return {
        "passed": passed,
        "result": "webapp_trade_completed_and_telegram_request_denied",
        "webapp_status": web_status,
        "telegram_status": telegram_status,
        "webapp": web_evidence,
        "telegram": telegram_evidence,
        "telegram_sent_count": telegram_sent_count,
        "counterparty_privacy_preserved": privacy_safe,
    }


async def _run_tier2_offer_denial(
    *,
    prefix: str,
    fixture: dict[str, Any],
    commodity_id: int,
    commodity_name: str,
    pair_index: int,
) -> dict[str, Any]:
    source = fixture["source_actor"]
    users = fixture["users"]
    if not isinstance(source, worker.LoadUserRef) or not isinstance(users, list):
        raise CustomerActorProbeError("customer fixture source actor is invalid")
    user_ids = [int(item.user_id) for item in users if isinstance(item, worker.LoadUserRef)]
    before = await _side_effect_counts(user_ids)
    web_phase: dict[str, Any] = {}
    web_status = await worker.execute_offer_creation_for_user(
        user_id=source.user_id,
        commodity_id=commodity_id,
        prefix=f"{prefix}web-denied-",
        index=pair_index,
        quantity=1,
        price=100000,
        is_wholesale=True,
        phase_details=web_phase,
    )
    telegram_phase: dict[str, Any] = {}
    telegram_status = await worker.execute_bot_offer_creation_for_user(
        user=source,
        commodity_name=commodity_name,
        prefix=f"{prefix}telegram-denied-",
        offer_type="sell",
        quantity=1,
        price=100000,
        is_wholesale=True,
        phase_details=telegram_phase,
    )
    after = await _side_effect_counts(user_ids)
    delta = _count_delta(before, after)
    privacy_safe = _phase_payload_safe(
        web_phase,
        [str(source.telegram_id)],
    ) and _phase_payload_safe(telegram_phase, [str(source.telegram_id)])
    passed = web_status == "rejected" and telegram_status == "rejected" and all(value == 0 for value in delta.values())
    return {
        "passed": passed,
        "result": "tier2_offer_creation_denied_with_zero_mutation",
        "webapp_status": web_status,
        "telegram_status": telegram_status,
        "side_effect_delta": delta,
        "counterparty_privacy_preserved": privacy_safe,
    }


async def _run_pair(
    *,
    prefix: str,
    actor_pair: str,
    execution_policy: str,
    commodity_id: int,
    commodity_name: str,
    pair_index: int,
) -> dict[str, Any]:
    source_kind, responder_kind, relation = _pair_parts(actor_pair)
    fixture = await worker.prepare_unsupported_policy_actor_fixture(
        prefix=f"{prefix}{pair_index:02d}_",
        source_kind=source_kind,
        responder_kind=responder_kind,
        group_relation=relation,
    )
    if execution_policy == "positive_all_eligible_surfaces":
        result = await _run_positive_all_surfaces(
            prefix=f"{prefix}{pair_index:02d}_",
            fixture=fixture,
            commodity_id=commodity_id,
            pair_index=pair_index,
        )
    elif execution_policy == "positive_webapp_tier2_request_telegram_denied":
        result = await _run_tier2_request_policy(
            prefix=f"{prefix}{pair_index:02d}_",
            fixture=fixture,
            commodity_id=commodity_id,
            pair_index=pair_index,
        )
    elif execution_policy == "negative_tier2_offer_creation_denied":
        result = await _run_tier2_offer_denial(
            prefix=f"{prefix}{pair_index:02d}_",
            fixture=fixture,
            commodity_id=commodity_id,
            commodity_name=commodity_name,
            pair_index=pair_index,
        )
    else:
        raise CustomerActorProbeError("customer pair policy is unsupported")
    return {
        "actor_pair": actor_pair,
        "execution_policy": execution_policy,
        **result,
    }


async def run_matrix(
    *,
    scenario_id: str,
    writer_role: str,
    prefix: str,
    allow_production: bool,
    allow_cleanup: bool,
) -> dict[str, Any]:
    if PREFIX_RE.fullmatch(prefix) is None:
        raise CustomerActorProbeError("customer actor prefix is unsafe")
    _assert_runtime(
        scenario_id=scenario_id,
        writer_role=writer_role,
        prefix=prefix,
        allow_production=allow_production,
    )
    if worker.is_production_runtime():
        worker.allow_production_cleanup_hard_delete(prefix, allow_flag=allow_cleanup)
    await worker.cleanup_prefix(prefix)
    worker.setup_event_listeners()
    commodity_id, commodity_name = await worker.resolve_commodity()
    pairs: list[dict[str, Any]] = []
    cleanup: dict[str, Any] | None = None
    residue: dict[str, Any] | None = None
    failure: Exception | None = None
    try:
        async with worker.patched_trading_boundaries():
            for pair_index, (actor_pair, execution_policy) in enumerate(
                CUSTOMER_ACTOR_PAIR_POLICIES.items(), start=1
            ):
                pairs.append(
                    await _run_pair(
                        prefix=prefix,
                        actor_pair=actor_pair,
                        execution_policy=execution_policy,
                        commodity_id=commodity_id,
                        commodity_name=commodity_name,
                        pair_index=pair_index,
                    )
                )
    except Exception as exc:  # Cleanup remains mandatory even on a rejected policy.
        failure = exc
    finally:
        cleanup = await worker.cleanup_prefix(prefix)
        residue = await worker.cleanup_prefix(prefix, dry_run=True)
    if failure is not None:
        raise CustomerActorProbeError("customer actor matrix execution raised") from failure
    contracts = customer_actor_pair_contracts(scenario_id)
    if set(contracts) != {f"customer_actor_pair:{item['actor_pair']}" for item in pairs}:
        raise CustomerActorProbeError("customer actor result set differs from source contracts")
    if any(item.get("passed") is not True for item in pairs):
        raise CustomerActorProbeError("customer actor matrix has a failed pair")
    if not isinstance(residue, dict) or any(
        int(value) != 0 for value in dict(residue.get("planned_counts") or {}).values()
    ):
        raise CustomerActorProbeError("customer actor cleanup left bounded residue")
    deleted_total = 0
    if isinstance(cleanup, dict):
        for key, value in cleanup.items():
            if key.startswith("deleted_") and isinstance(value, int) and not isinstance(value, bool):
                deleted_total += value
    return {
        "schema": SCHEMA,
        "status": "passed",
        "scenario_id": scenario_id,
        "writer_role": writer_role,
        "runtime_state": CUSTOMER_LIFECYCLE_MATRIX[scenario_id]["runtime_state"],
        "server_mode": str(worker.settings.server_mode),
        "prefix": prefix,
        "pair_count": len(pairs),
        "pairs": pairs,
        "cleanup": {
            "deleted_total": deleted_total,
            "residue_zero": True,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-id", required=True, choices=sorted(CUSTOMER_LIFECYCLE_MATRIX))
    parser.add_argument("--writer-role", required=True, choices=sorted(ROLE_TO_SERVER))
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--allow-production-execution", action="store_true")
    parser.add_argument("--allow-production-cleanup", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(
            run_matrix(
                scenario_id=args.scenario_id,
                writer_role=args.writer_role,
                prefix=args.prefix,
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
