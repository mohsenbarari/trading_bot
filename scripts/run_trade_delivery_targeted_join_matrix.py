#!/usr/bin/env python3
"""Run the Bot/WebApp trade-delivery targeted join matrix on staging.

The dry-run mode is catalog-only and safe for local/CI use. The execution mode
creates synthetic staging users/offers/trades, drives the real offer/trade
routers, and repairs delivery receipts through the production services while
injecting a fake Telegram gateway so no Telegram network call is made.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import JSONResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers import trades as trades_router
from core import telegram_gateway
from core.config import settings
from core.db import AsyncSessionLocal
from core.enums import UserAccountStatus
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.accountant_relation_service import EffectiveOwnerActor
from core.services.trade_delivery_reconciliation_service import build_trade_delivery_shadow_report_for_trades
from core.services.trade_delivery_receipt_service import (
    TRADE_DELIVERY_EXPIRED_AFTER_OUTAGE_REASON,
    receipt_is_opposite_server_delivery,
)
from core.services.trade_telegram_delivery_service import repair_telegram_trade_delivery_for_trade
from core.services.trade_webapp_delivery_service import repair_webapp_trade_delivery_for_trade
from core.utils import utc_now
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.offer import Offer, OfferStatus
from models.offer_request import OfferRequestSourceSurface
from models.trade import Trade
from models.trade_delivery_receipt import TradeDeliveryReceipt, TradeDeliveryReceiptStatus
from models.user import User, UserRole
from scripts import report_trade_notification_delivery_matrix as delivery_matrix
from scripts import run_bot_webapp_comprehensive_load_matrix as market_matrix
from scripts import trading_core_probe_worker as worker


SCHEMA_VERSION = "trade_delivery_targeted_join_matrix_v1"
EXPECTED_BRANCH = "candidate/bot-webapp-integration"
OUTAGE_NOW_OFFSETS = {
    "stable": timedelta(seconds=5),
    "short_under_2m": timedelta(seconds=75),
    "medium_around_60m": timedelta(seconds=180),
}
SUPPORTED_SURFACES = {"webapp", "telegram"}


@dataclass(frozen=True)
class TargetedJoinScenario:
    scenario_id: str
    actor_pair_id: str
    source_kind: str
    responder_kind: str
    group_relation: str
    offer_surface: str
    request_surface: str
    surface_pair: str
    outage_id: str
    offer_home_server: str
    request_source_server: str
    expected_remote_delivery_policy: str
    policy_supported: bool
    unsupported_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActorFixture:
    source_actor_id: int
    source_actor_telegram_id: int | None
    responder_actor_id: int
    source_principal_id: int
    responder_principal_id: int
    accountant_user_ids: tuple[int, ...]


@dataclass(frozen=True)
class TelegramSendProbe:
    chat_id: int | None
    text_length: int
    idempotency_key: str | None


def utc_prefix() -> str:
    return f"P7_STAGE_TDJ_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_"


def json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )


def run_git_value(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        if args == ["branch", "--show-current"]:
            return os.environ.get("CANDIDATE_MATRIX_BRANCH") or None
        if args == ["rev-parse", "HEAD"]:
            return os.environ.get("CANDIDATE_MATRIX_COMMIT") or None
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def parse_surface_pair_name(name: str) -> tuple[str, str]:
    offer_part, request_part = str(name).split("_offer__", 1)
    return offer_part, request_part.removesuffix("_request")


def unsupported_reasons_for(
    *,
    actor_pair: delivery_matrix.ActorPair,
    offer_surface: str,
    request_surface: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if actor_pair.source_kind == "tier2":
        reasons.append("tier2_cannot_create_offer")
    if actor_pair.responder_kind == "tier2" and request_surface == "telegram":
        reasons.append("tier2_cannot_use_telegram_request")
    if offer_surface not in SUPPORTED_SURFACES:
        reasons.append("unsupported_offer_surface")
    if request_surface not in SUPPORTED_SURFACES:
        reasons.append("unsupported_request_surface")
    return tuple(reasons)


def build_targeted_join_scenarios() -> list[TargetedJoinScenario]:
    actor_pairs = {item.pair_id: item for item in delivery_matrix.build_actor_pairs()}
    scenarios: list[TargetedJoinScenario] = []
    for scenario in delivery_matrix.build_delivery_scenarios():
        actor_pair = actor_pairs[scenario.actor_pair_id]
        offer_surface, request_surface = parse_surface_pair_name(scenario.surface_pair)
        request_source_server = SERVER_FOREIGN if request_surface == "telegram" else SERVER_IRAN
        reasons = unsupported_reasons_for(
            actor_pair=actor_pair,
            offer_surface=offer_surface,
            request_surface=request_surface,
        )
        scenarios.append(
            TargetedJoinScenario(
                scenario_id=scenario.scenario_id,
                actor_pair_id=actor_pair.pair_id,
                source_kind=actor_pair.source_kind,
                responder_kind=actor_pair.responder_kind,
                group_relation=actor_pair.group_relation,
                offer_surface=offer_surface,
                request_surface=request_surface,
                surface_pair=scenario.surface_pair,
                outage_id=scenario.outage_id,
                offer_home_server=scenario.offer_home_server,
                request_source_server=request_source_server,
                expected_remote_delivery_policy=scenario.expected_remote_delivery_policy,
                policy_supported=not reasons,
                unsupported_reasons=reasons,
            )
        )
    return scenarios


def filter_scenarios(
    scenarios: Iterable[TargetedJoinScenario],
    *,
    scenario_ids: set[str],
    outage_ids: set[str],
    max_scenarios: int | None,
    executable_only: bool,
) -> list[TargetedJoinScenario]:
    selected = [
        scenario
        for scenario in scenarios
        if (not scenario_ids or scenario.scenario_id in scenario_ids)
        and (not outage_ids or scenario.outage_id in outage_ids)
        and (not executable_only or scenario.policy_supported)
    ]
    if max_scenarios is not None:
        selected = selected[: max(0, max_scenarios)]
    return selected


def summarize_catalog(scenarios: list[TargetedJoinScenario]) -> dict[str, Any]:
    unsupported_counts: dict[str, int] = {}
    for scenario in scenarios:
        for reason in scenario.unsupported_reasons:
            unsupported_counts[reason] = unsupported_counts.get(reason, 0) + 1
    return {
        "scenario_count": len(scenarios),
        "executable_count": sum(1 for scenario in scenarios if scenario.policy_supported),
        "policy_unsupported_count": sum(1 for scenario in scenarios if not scenario.policy_supported),
        "actor_pair_count": len({scenario.actor_pair_id for scenario in scenarios}),
        "surface_pair_count": len({scenario.surface_pair for scenario in scenarios}),
        "outage_class_count": len({scenario.outage_id for scenario in scenarios}),
        "unsupported_reason_counts": unsupported_counts,
    }


def planned_payload(args: argparse.Namespace, scenarios: list[TargetedJoinScenario]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": str(getattr(settings, "environment", "") or "staging"),
        "branch": run_git_value(["branch", "--show-current"]),
        "commit": run_git_value(["rev-parse", "HEAD"]),
        "dry_run": bool(args.dry_run),
        "prefix": args.prefix,
        "catalog": summarize_catalog(scenarios),
        "scenarios": [asdict(scenario) for scenario in scenarios],
        "execution": {
            "status": "planned",
            "telegram_network_calls": "disabled_by_injected_gateway",
            "production_gate": "blocked_until_owner_staging_validation",
        },
    }


def safe_key(value: str, *, max_length: int = 42) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in str(value))
    return (normalized.strip("_") or "x")[:max_length]


def scenario_account_prefix(prefix: str, scenario: TargetedJoinScenario) -> str:
    return safe_key(f"{prefix}{scenario.scenario_id}_{scenario.actor_pair_id}", max_length=54)


def prefix_identity_bucket(prefix: str, *, attempt: int = 0) -> int:
    digest = hashlib.blake2s(str(prefix).encode("utf-8"), digest_size=4).hexdigest()
    return (int(digest, 16) + attempt * 7919) % 80_000


def matrix_run_key(prefix: str) -> str:
    return hashlib.blake2s(str(prefix).encode("utf-8"), digest_size=5).hexdigest()


def phone_for(prefix: str, index: int, offset: int, *, attempt: int = 0) -> str:
    seed = 100_000_000 + prefix_identity_bucket(prefix, attempt=attempt) * 10_000 + index * 40 + offset
    return f"09{seed:09d}"[-11:]


def telegram_id_for(prefix: str, index: int, offset: int, *, attempt: int = 0) -> int:
    return 9_300_000_000 + prefix_identity_bucket(prefix, attempt=attempt) * 10_000 + index * 40 + offset


async def available_fixture_identity(
    db: AsyncSession,
    *,
    prefix: str,
    scenario_index: int,
    offset: int,
    telegram_linked: bool,
) -> tuple[str, int | None]:
    for attempt in range(10):
        mobile_number = phone_for(prefix, scenario_index, offset, attempt=attempt)
        telegram_id = telegram_id_for(prefix, scenario_index, offset, attempt=attempt) if telegram_linked else None
        existing_mobile = await db.scalar(select(User.id).where(User.mobile_number == mobile_number).limit(1))
        if existing_mobile is not None:
            continue
        if telegram_id is not None:
            existing_telegram = await db.scalar(select(User.id).where(User.telegram_id == telegram_id).limit(1))
            if existing_telegram is not None:
                continue
        return mobile_number, telegram_id
    raise RuntimeError(
        "could_not_allocate_unique_targeted_join_identity "
        f"prefix={prefix!r} scenario_index={scenario_index} offset={offset}"
    )


def user_home_for_surface(surface: str) -> str:
    return SERVER_FOREIGN if surface == "telegram" else SERVER_IRAN


async def create_fixture_user(
    db: AsyncSession,
    *,
    prefix: str,
    account_prefix: str,
    scenario_index: int,
    offset: int,
    label: str,
    home_server: str,
    telegram_linked: bool = True,
) -> User:
    mobile_number, telegram_id = await available_fixture_identity(
        db,
        prefix=prefix,
        scenario_index=scenario_index,
        offset=offset,
        telegram_linked=telegram_linked,
    )
    user = User(
        account_name=f"{account_prefix}_{label}"[:96],
        mobile_number=mobile_number,
        telegram_id=telegram_id,
        username=f"{account_prefix}_{label}"[:96],
        full_name=f"{label} {scenario_index}",
        address="synthetic staging matrix user",
        role=UserRole.STANDARD,
        account_status=UserAccountStatus.ACTIVE,
        has_bot_access=True,
        home_server=home_server,
        max_sessions=1,
        max_accountants=10,
        max_customers=10,
        max_daily_trades=None,
        max_active_commodities=None,
        max_daily_requests=None,
    )
    db.add(user)
    await db.flush()
    return user


async def create_customer_relation(
    db: AsyncSession,
    *,
    account_prefix: str,
    scenario_index: int,
    label: str,
    owner: User,
    customer: User,
    tier: CustomerTier,
) -> CustomerRelation:
    now = utc_now()
    relation = CustomerRelation(
        owner_user_id=owner.id,
        customer_user_id=customer.id,
        created_by_user_id=owner.id,
        invitation_token=f"CUST-{account_prefix}-{scenario_index}-{label}"[:128],
        management_name=f"{label}_{scenario_index}"[:120],
        customer_tier=tier,
        commission_rate=None,
        min_trade_quantity=None,
        max_trade_quantity=None,
        max_daily_trades=None,
        max_daily_commodity_volume=None,
        status=CustomerRelationStatus.ACTIVE,
        expires_at=now + timedelta(days=365),
        activated_at=now,
    )
    db.add(relation)
    await db.flush()
    return relation


async def create_accountant_relation(
    db: AsyncSession,
    *,
    account_prefix: str,
    scenario_index: int,
    offset: int,
    owner: User,
) -> User:
    accountant = await create_fixture_user(
        db,
        prefix=account_prefix,
        account_prefix=account_prefix,
        scenario_index=scenario_index,
        offset=offset,
        label=f"acct_{owner.id}",
        home_server=SERVER_IRAN,
        telegram_linked=False,
    )
    now = utc_now()
    relation = AccountantRelation(
        owner_user_id=owner.id,
        accountant_user_id=accountant.id,
        created_by_user_id=owner.id,
        invitation_token=f"ACCT-{account_prefix}-{scenario_index}-{owner.id}"[:128],
        global_account_name=accountant.account_name,
        relation_display_name=f"acct_{owner.id}_{scenario_index}"[:120],
        duty_description="synthetic staging matrix accountant",
        mobile_number=accountant.mobile_number,
        status=AccountantRelationStatus.ACTIVE,
        expires_at=now + timedelta(days=365),
        activated_at=now,
    )
    db.add(relation)
    await db.flush()
    return accountant


async def build_actor_fixture(
    db: AsyncSession,
    *,
    scenario: TargetedJoinScenario,
    scenario_index: int,
    prefix: str,
) -> ActorFixture:
    account_prefix = scenario_account_prefix(prefix, scenario)
    source_user = await create_fixture_user(
        db,
        prefix=prefix,
        account_prefix=account_prefix,
        scenario_index=scenario_index,
        offset=1,
        label="source_user",
        home_server=user_home_for_surface(scenario.offer_surface),
    )
    responder_user = await create_fixture_user(
        db,
        prefix=prefix,
        account_prefix=account_prefix,
        scenario_index=scenario_index,
        offset=2,
        label="responder_user",
        home_server=user_home_for_surface(scenario.request_surface),
    )
    shared_owner = await create_fixture_user(
        db,
        prefix=prefix,
        account_prefix=account_prefix,
        scenario_index=scenario_index,
        offset=3,
        label="shared_owner",
        home_server=SERVER_IRAN,
    )
    source_owner = await create_fixture_user(
        db,
        prefix=prefix,
        account_prefix=account_prefix,
        scenario_index=scenario_index,
        offset=4,
        label="source_owner",
        home_server=SERVER_IRAN,
    )
    responder_owner = await create_fixture_user(
        db,
        prefix=prefix,
        account_prefix=account_prefix,
        scenario_index=scenario_index,
        offset=5,
        label="responder_owner",
        home_server=SERVER_IRAN,
    )
    source_customer = await create_fixture_user(
        db,
        prefix=prefix,
        account_prefix=account_prefix,
        scenario_index=scenario_index,
        offset=6,
        label="source_customer",
        home_server=user_home_for_surface(scenario.offer_surface),
    )
    responder_customer = await create_fixture_user(
        db,
        prefix=prefix,
        account_prefix=account_prefix,
        scenario_index=scenario_index,
        offset=7,
        label="responder_customer",
        home_server=user_home_for_surface(scenario.request_surface),
    )

    source_actor = source_user
    source_principal = source_user
    if scenario.source_kind != "user":
        source_actor = source_customer
        if scenario.group_relation == "same_owner" and scenario.responder_kind == "user":
            source_principal = responder_user
        elif scenario.group_relation == "same_owner":
            source_principal = shared_owner
        else:
            source_principal = source_owner
        await create_customer_relation(
            db,
            account_prefix=account_prefix,
            scenario_index=scenario_index,
            label="source_customer_relation",
            owner=source_principal,
            customer=source_actor,
            tier=CustomerTier.TIER_1 if scenario.source_kind == "tier1" else CustomerTier.TIER_2,
        )

    responder_actor = responder_user
    responder_principal = responder_user
    if scenario.responder_kind != "user":
        responder_actor = responder_customer
        if scenario.group_relation == "same_owner" and scenario.source_kind == "user":
            responder_principal = source_user
        elif scenario.group_relation == "same_owner":
            responder_principal = shared_owner
        else:
            responder_principal = responder_owner
        await create_customer_relation(
            db,
            account_prefix=account_prefix,
            scenario_index=scenario_index,
            label="responder_customer_relation",
            owner=responder_principal,
            customer=responder_actor,
            tier=CustomerTier.TIER_1 if scenario.responder_kind == "tier1" else CustomerTier.TIER_2,
        )

    accountant_owner_ids = {
        int(source_principal.id),
        int(responder_principal.id),
    }
    accountant_ids: list[int] = []
    offset = 20
    for owner_id in sorted(accountant_owner_ids):
        owner = await db.get(User, owner_id)
        if owner is None:
            continue
        accountant = await create_accountant_relation(
            db,
            account_prefix=account_prefix,
            scenario_index=scenario_index,
            offset=offset,
            owner=owner,
        )
        accountant_ids.append(int(accountant.id))
        offset += 1

    await db.commit()
    return ActorFixture(
        source_actor_id=int(source_actor.id),
        source_actor_telegram_id=int(source_actor.telegram_id) if source_actor.telegram_id is not None else None,
        responder_actor_id=int(responder_actor.id),
        source_principal_id=int(source_principal.id),
        responder_principal_id=int(responder_principal.id),
        accountant_user_ids=tuple(accountant_ids),
    )


async def create_scenario_offer(
    *,
    scenario: TargetedJoinScenario,
    fixture: ActorFixture,
    scenario_index: int,
    prefix: str,
) -> int:
    commodity_id, commodity_name = await worker.resolve_commodity()
    origin = "bot" if scenario.offer_surface == "telegram" else "webapp"
    shape = market_matrix.SHAPES["wholesale_full"]
    return await market_matrix.create_offer(
        origin=origin,
        owner=worker.LoadUserRef(
            user_id=fixture.source_actor_id,
            telegram_id=fixture.source_actor_telegram_id,
        ),
        commodity_id=commodity_id,
        commodity_name=commodity_name,
        shape=shape,
        offer_type="sell",
        prefix=prefix,
        index=scenario_index,
        fast_seed_bot_offer=True,
        time_limit_buffer_minutes=60,
    )


async def execute_scenario_trade(
    *,
    scenario: TargetedJoinScenario,
    fixture: ActorFixture,
    offer_id: int,
    scenario_index: int,
    prefix: str,
) -> dict[str, Any]:
    source_surface = (
        OfferRequestSourceSurface.TELEGRAM_BOT
        if scenario.request_surface == "telegram"
        else OfferRequestSourceSurface.WEBAPP
    )
    started_trade_id = 0
    async with AsyncSessionLocal() as db:
        started_trade_id = int(await db.scalar(select(func.max(Trade.id))) or 0)

    async with AsyncSessionLocal() as db:
        responder = await db.get(User, fixture.responder_actor_id)
        if responder is None:
            raise RuntimeError("responder actor disappeared")
        background_tasks = BackgroundTasks()
        with override_current_server(scenario.offer_home_server):
            response = await trades_router._execute_trade_authoritatively_with_transient_retry(
                trade_data=trades_router.TradeCreate(
                    offer_id=offer_id,
                    quantity=market_matrix.SHAPES["wholesale_full"].request_amount,
                    idempotency_key=f"tdj:{matrix_run_key(prefix)}:{scenario.scenario_id}:{scenario_index}",
                ),
                background_tasks=background_tasks,
                db=db,
                context=EffectiveOwnerActor(
                    owner_user=responder,
                    actor_user=responder,
                    relation=None,
                    is_accountant_context=False,
                ),
                edge_received_at=utc_now(),
                request_source_surface=source_surface,
                request_source_server=scenario.request_source_server,
                request_pre_gated=False,
            )
        if isinstance(response, JSONResponse):
            body = json.loads(response.body.decode("utf-8") or "{}")
            raise HTTPException(status_code=int(response.status_code), detail=body)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Trade)
            .options(
                selectinload(Trade.offer),
                selectinload(Trade.offer_user),
                selectinload(Trade.responder_user),
                selectinload(Trade.commodity),
            )
            .where(
                Trade.id > started_trade_id,
                Trade.actor_user_id == fixture.responder_actor_id,
            )
            .order_by(Trade.id.asc())
        )
        trades = list(result.scalars().all())
    if not trades:
        raise RuntimeError("no trades were persisted for scenario")
    return {
        "trade_ids": [int(trade.id) for trade in trades],
        "trade_numbers": [int(trade.trade_number) for trade in trades],
        "response_trade_number": getattr(response, "trade_number", None),
    }


def fake_telegram_gateway_factory(probes: list[TelegramSendProbe]):
    async def fake_send_message(
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Any | None = None,
        timeout: float = 10,
        bot_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> telegram_gateway.TelegramGatewayResult:
        probes.append(
            TelegramSendProbe(
                chat_id=chat_id,
                text_length=len(str(text or "")),
                idempotency_key=idempotency_key,
            )
        )
        return telegram_gateway.TelegramGatewayResult(
            ok=True,
            method="sendMessage",
            status_code=200,
            response_json={"ok": True, "result": {"message_id": 8_800_000 + len(probes)}},
            idempotency_key=idempotency_key,
        )

    return fake_send_message


async def repair_and_collect_delivery(
    *,
    scenario: TargetedJoinScenario,
    trade_ids: list[int],
) -> dict[str, Any]:
    probes: list[TelegramSendProbe] = []
    now_offset = OUTAGE_NOW_OFFSETS[scenario.outage_id]
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Trade)
            .options(
                selectinload(Trade.offer),
                selectinload(Trade.offer_user),
                selectinload(Trade.responder_user),
                selectinload(Trade.commodity),
            )
            .where(Trade.id.in_(trade_ids))
            .order_by(Trade.id.asc())
        )
        trades = list(result.scalars().all())
        if not trades:
            raise RuntimeError("trade rows disappeared before delivery repair")
        event_time = getattr(trades[0], "created_at", None)
        if not isinstance(event_time, datetime):
            event_time = utc_now()
        current_time = event_time + now_offset

        webapp_results = []
        telegram_results = []
        for trade in trades:
            webapp_results.extend(
                await repair_webapp_trade_delivery_for_trade(
                    db,
                    trade,
                    current_server=SERVER_IRAN,
                    publish_after_commit=False,
                    now=current_time,
                )
            )
            telegram_results.extend(
                await repair_telegram_trade_delivery_for_trade(
                    db,
                    trade,
                    current_server=SERVER_FOREIGN,
                    bot_token="staging-matrix-token",
                    gateway_send=fake_telegram_gateway_factory(probes),
                    now=current_time,
                    max_jitter_seconds=0,
                )
            )

        trade_numbers = [int(trade.trade_number) for trade in trades]
        receipt_rows = list(
            (
                await db.execute(
                    select(TradeDeliveryReceipt).where(TradeDeliveryReceipt.trade_number.in_(trade_numbers))
                )
            )
            .scalars()
            .all()
        )
        iran_shadow = await build_trade_delivery_shadow_report_for_trades(
            db,
            trades,
            current_server=SERVER_IRAN,
        )
        foreign_shadow = await build_trade_delivery_shadow_report_for_trades(
            db,
            trades,
            current_server=SERVER_FOREIGN,
        )

    status_counts: dict[str, int] = {}
    channel_status_counts: dict[str, int] = {}
    opposite_status_counts: dict[str, int] = {}
    terminal_problem_receipts: list[dict[str, Any]] = []
    for receipt in receipt_rows:
        status_value = str(getattr(getattr(receipt, "status", None), "value", getattr(receipt, "status", "")))
        channel_value = str(getattr(getattr(receipt, "channel", None), "value", getattr(receipt, "channel", "")))
        status_counts[status_value] = status_counts.get(status_value, 0) + 1
        channel_key = f"{channel_value}:{status_value}"
        channel_status_counts[channel_key] = channel_status_counts.get(channel_key, 0) + 1
        if receipt_is_opposite_server_delivery(receipt):
            opposite_status_counts[status_value] = opposite_status_counts.get(status_value, 0) + 1
        if status_value in {
            TradeDeliveryReceiptStatus.PENDING.value,
            TradeDeliveryReceiptStatus.PROCESSING.value,
            TradeDeliveryReceiptStatus.RETRY_PENDING.value,
            TradeDeliveryReceiptStatus.PERMANENT_FAILED.value,
        }:
            terminal_problem_receipts.append(
                {
                    "receipt_id": int(receipt.id),
                    "trade_number": int(receipt.trade_number),
                    "recipient_user_id": int(receipt.recipient_user_id),
                    "channel": channel_value,
                    "status": status_value,
                    "reason": receipt.reason,
                }
            )

    assertions: list[dict[str, Any]] = []
    assertions.append(
        {
            "name": "no_nonterminal_or_permanent_failed_receipts",
            "passed": not terminal_problem_receipts,
            "details": terminal_problem_receipts,
        }
    )
    if scenario.outage_id in {"stable", "short_under_2m"}:
        assertions.append(
            {
                "name": "no_outage_expired_skips_before_medium",
                "passed": not any(
                    receipt.reason == TRADE_DELIVERY_EXPIRED_AFTER_OUTAGE_REASON for receipt in receipt_rows
                ),
            }
        )
    if scenario.outage_id == "medium_around_60m":
        assertions.append(
            {
                "name": "opposite_server_required_delivery_skipped_after_medium_outage",
                "passed": opposite_status_counts.get(TradeDeliveryReceiptStatus.SKIPPED.value, 0) > 0,
                "opposite_status_counts": opposite_status_counts,
            }
        )
        assertions.append(
            {
                "name": "no_medium_opposite_server_sent_receipts",
                "passed": opposite_status_counts.get(TradeDeliveryReceiptStatus.SENT.value, 0) == 0,
                "opposite_status_counts": opposite_status_counts,
            }
        )
    else:
        assertions.append(
            {
                "name": "stable_or_short_has_sent_receipts",
                "passed": status_counts.get(TradeDeliveryReceiptStatus.SENT.value, 0) > 0,
                "status_counts": status_counts,
            }
        )

    passed = all(bool(item.get("passed")) for item in assertions)
    return {
        "status": "passed" if passed else "failed",
        "outage_now_offset_seconds": int(now_offset.total_seconds()),
        "trade_count": len(trade_ids),
        "receipt_count": len(receipt_rows),
        "receipt_status_counts": status_counts,
        "channel_status_counts": channel_status_counts,
        "opposite_status_counts": opposite_status_counts,
        "notification_count": len({int(receipt.notification_id) for receipt in receipt_rows if receipt.notification_id}),
        "webapp_result_count": len(webapp_results),
        "telegram_result_count": len(telegram_results),
        "telegram_gateway_probe_count": len(probes),
        "telegram_gateway_probes": [asdict(probe) for probe in probes[:20]],
        "iran_shadow": {
            "expectation_count": iran_shadow.expectation_count,
            "delivery_gap_count": iran_shadow.delivery_gap_count,
            "missing_receipt_count": iran_shadow.missing_receipt_count,
            "missing_notification_count": iran_shadow.missing_notification_count,
        },
        "foreign_shadow": {
            "expectation_count": foreign_shadow.expectation_count,
            "delivery_gap_count": foreign_shadow.delivery_gap_count,
            "missing_receipt_count": foreign_shadow.missing_receipt_count,
            "missing_notification_count": foreign_shadow.missing_notification_count,
        },
        "assertions": assertions,
    }


async def execute_scenario(
    *,
    scenario: TargetedJoinScenario,
    scenario_index: int,
    prefix: str,
) -> dict[str, Any]:
    if not scenario.policy_supported:
        return {
            "scenario_id": scenario.scenario_id,
            "status": "policy_unsupported",
            "unsupported_reasons": list(scenario.unsupported_reasons),
        }
    try:
        async with AsyncSessionLocal() as db:
            fixture = await build_actor_fixture(
                db,
                scenario=scenario,
                scenario_index=scenario_index,
                prefix=prefix,
            )

        async with worker.patched_trading_boundaries():
            offer_id = await create_scenario_offer(
                scenario=scenario,
                fixture=fixture,
                scenario_index=scenario_index,
                prefix=prefix,
            )
            trade_result = await execute_scenario_trade(
                scenario=scenario,
                fixture=fixture,
                offer_id=offer_id,
                scenario_index=scenario_index,
                prefix=prefix,
            )
            delivery_result = await repair_and_collect_delivery(
                scenario=scenario,
                trade_ids=[int(item) for item in trade_result["trade_ids"]],
            )

        async with AsyncSessionLocal() as db:
            offer = await db.get(Offer, offer_id)
            offer_status = str(getattr(getattr(offer, "status", None), "value", getattr(offer, "status", None)))
            remaining_quantity = getattr(offer, "remaining_quantity", None)

        offer_assertions = [
            {
                "name": "offer_home_server_matches_surface",
                "passed": getattr(offer, "home_server", None) == scenario.offer_home_server if offer else False,
            },
            {
                "name": "offer_terminal_completed",
                "passed": offer_status == OfferStatus.COMPLETED.value,
                "offer_status": offer_status,
            },
            {
                "name": "remaining_quantity_not_negative",
                "passed": remaining_quantity is not None and int(remaining_quantity) >= 0,
                "remaining_quantity": remaining_quantity,
            },
        ]
        passed = delivery_result["status"] == "passed" and all(item["passed"] for item in offer_assertions)
        return {
            "scenario_id": scenario.scenario_id,
            "status": "passed" if passed else "failed",
            "scenario": asdict(scenario),
            "fixture": asdict(fixture),
            "offer": {
                "id": offer_id,
                "home_server": getattr(offer, "home_server", None) if offer else None,
                "status": offer_status,
                "remaining_quantity": remaining_quantity,
            },
            "trade": trade_result,
            "delivery": delivery_result,
            "assertions": offer_assertions,
        }
    except Exception as exc:
        return {
            "scenario_id": scenario.scenario_id,
            "status": "failed",
            "scenario": asdict(scenario),
            "error_class": type(exc).__name__,
            "error": str(getattr(exc, "detail", None) or exc),
        }


async def execute_matrix(args: argparse.Namespace, scenarios: list[TargetedJoinScenario]) -> dict[str, Any]:
    if not args.allow_any_branch and run_git_value(["branch", "--show-current"]) != EXPECTED_BRANCH:
        return {
            **planned_payload(args, scenarios),
            "execution": {
                "status": "blocked",
                "reason": "wrong_branch",
                "expected_branch": EXPECTED_BRANCH,
                "actual_branch": run_git_value(["branch", "--show-current"]),
            },
        }

    async with AsyncSessionLocal() as db:
        dialect = str(getattr(getattr(db.get_bind(), "dialect", None), "name", "") or "")
    if dialect != "postgresql":
        return {
            **planned_payload(args, scenarios),
            "execution": {
                "status": "blocked",
                "reason": "postgresql_required_for_authoritative_staging_matrix",
                "actual_dialect": dialect,
            },
        }

    cleanup_before = None
    cleanup_after = None
    if worker.is_production_runtime():
        try:
            worker.assert_production_full_matrix_allowed(
                args.prefix,
                allow_flag=bool(args.allow_production_execution),
            )
            worker.allow_production_cleanup_hard_delete(
                args.prefix,
                allow_flag=bool(args.allow_production_cleanup),
            )
            cleanup_before = await worker.cleanup_prefix(args.prefix)
        except Exception as exc:
            return {
                **planned_payload(args, scenarios),
                "execution": {
                    "status": "blocked",
                    "reason": "production_gate_failed",
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
            }

    results: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios, start=1):
        results.append(
            await execute_scenario(
                scenario=scenario,
                scenario_index=index,
                prefix=args.prefix,
            )
        )

    result_counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("status") or "unknown")
        result_counts[status] = result_counts.get(status, 0) + 1
    status_value = "passed" if result_counts.get("failed", 0) == 0 else "failed"
    if worker.is_production_runtime() and not args.keep_data:
        cleanup_after = await worker.cleanup_prefix(args.prefix)
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": str(getattr(settings, "environment", "") or "staging"),
        "branch": run_git_value(["branch", "--show-current"]),
        "commit": run_git_value(["rev-parse", "HEAD"]),
        "settings_server_mode": settings.server_mode,
        "dry_run": False,
        "prefix": args.prefix,
        "catalog": summarize_catalog(scenarios),
        "execution": {
            "status": status_value,
            "result_counts": result_counts,
            "telegram_network_calls": "disabled_by_injected_gateway",
            "production_gate": (
                "production_execution_allowed"
                if worker.is_production_runtime() and args.allow_production_execution
                else "blocked_until_owner_staging_validation"
            ),
            "cleanup_before": cleanup_before,
            "cleanup_after": cleanup_after,
        },
        "results": results,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the staging trade-delivery targeted join matrix.")
    parser.add_argument("--prefix", default=os.environ.get("PREFIX") or utc_prefix())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--allow-any-branch", action="store_true")
    parser.add_argument("--executable-only", action="store_true")
    parser.add_argument("--max-scenarios", type=int)
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--outage", action="append", default=[])
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument(
        "--allow-production-execution",
        action="store_true",
        help="Allow production targeted-join fixture creation only with the production full-matrix confirmation env.",
    )
    parser.add_argument(
        "--allow-production-cleanup",
        action="store_true",
        help="Allow production targeted-join cleanup only with the cleanup confirmation env.",
    )
    args = parser.parse_args(argv)
    if args.output is None:
        args.output = Path("/tmp/trading-bot-staging-candidate-full") / args.prefix / "trade-delivery-targeted-join-matrix.json"
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scenarios = filter_scenarios(
        build_targeted_join_scenarios(),
        scenario_ids=set(args.scenario or []),
        outage_ids=set(args.outage or []),
        max_scenarios=args.max_scenarios,
        executable_only=bool(args.executable_only),
    )

    if args.dry_run:
        payload = planned_payload(args, scenarios)
    else:
        payload = asyncio.run(execute_matrix(args, scenarios))

    write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=json_default))

    if not args.check:
        return 0
    if args.dry_run:
        if args.scenario:
            expected = len(set(args.scenario or []))
        elif args.outage or args.max_scenarios is not None:
            expected = len(scenarios)
        else:
            expected = len(delivery_matrix.build_actor_pairs()) * len(delivery_matrix.build_surface_pairs()) * len(
                delivery_matrix.build_outage_classes()
            )
        return 0 if payload.get("catalog", {}).get("scenario_count") == expected else 1
    return 0 if (payload.get("execution") or {}).get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
