#!/usr/bin/env python3
"""Run the in-container Stage P7 trading-core benchmark helpers.

The worker intentionally uses synthetic users/offers/trades with a unique
prefix. It exercises the real router/service money path while replacing market
schedule, Telegram, and realtime network boundaries with local no-ops.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Mapping

import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from fastapi import BackgroundTasks, HTTPException
from starlette.responses import JSONResponse
from sqlalchemy import delete, false, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm.exc import StaleDataError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers import offers as offers_router
from api.routers import realtime as realtime_router
from api.routers import trades as trades_router
from bot.callbacks import ExpireOfferCallback, TextOfferActionCallback
from bot.handlers import trade_execute as bot_trade_execute
from bot.handlers import trade_create as bot_trade_create
from bot.handlers import trade_manage as bot_trade_manage
from bot.middlewares import AuthMiddleware, TradeContentionGateMiddleware
from bot.middlewares.trade_contention_gate import (
    ParsedTelegramTradeCallback,
    claim_telegram_trade_confirmation,
    parse_telegram_trade_callback_data,
)
from bot.middlewares.logging_context import BotLoggingContextMiddleware
from bot.utils.offer_parser import parse_offer_text
from core.config import settings
from core.db import AsyncSessionLocal
from core.enums import NotificationCategory, NotificationLevel, UserAccountStatus
from core.events import setup_event_listeners
from core.redis import init_redis, pool
from core.services.accountant_relation_service import EffectiveOwnerActor
from core.services.offer_creation_service import OfferCreationCommand, create_authoritative_offer
from core.services.trade_contention_gate import TradeContentionLease, try_acquire_trade_contention_gate
from core.offer_source import OfferSourceSurface, normalize_offer_source_surface
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, normalize_server, override_current_server
from core.telegram_trade_callbacks import build_channel_trade_callback_data
from core.utils import create_user_notification
from core import offer_expiry as offer_expiry_worker
from models.accountant_relation import AccountantRelation
from models.change_log import ChangeLog
from models.chat_member import ChatMember
from models.commodity import Commodity
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.invitation import Invitation
from models.notification import Notification
from models.offer_publication_state import OfferPublicationState, OfferPublicationStatus
from models.offer_request import OfferRequest, OfferRequestStatus
from models.offer import Offer, OfferStatus, OfferType
from models.push_subscription import PushSubscription
from models.session import (
    SessionLoginRequest,
    SingleSessionRecoveryAdminTarget,
    SingleSessionRecoveryRequest,
    UserSession,
)
from models.telegram_link_token import TelegramLinkToken
from models.trade import Trade, TradeStatus, TradeType
from models.trade_delivery_receipt import TradeDeliveryReceipt
from models.user import User, UserRole


class TradingProbeError(RuntimeError):
    pass


DUAL_ROLE_PLAN_SCHEMA_VERSION = "bot_webapp_mixed_load_role_plan_v1"
DUAL_ROLE_RESULT_SCHEMA_VERSION = "bot_webapp_mixed_load_role_result_v1"
DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION = "bot_webapp_mixed_load_merged_result_v1"
DUAL_ROLE_MANIFEST_SCHEMA_VERSION = "bot_webapp_mixed_load_manifest_v1"
DUAL_ROLE_PREPARE_SCHEMA_VERSION = "bot_webapp_mixed_load_prepare_v1"
DUAL_ROLE_FINAL_SCHEMA_VERSION = "bot_webapp_mixed_load_final_v1"
MANUAL_EXPIRY_RACE_RESULT_SCHEMA_VERSION = "manual_expiry_trade_race_result_v1"
TIME_EXPIRY_RACE_RESULT_SCHEMA_VERSION = "time_expiry_trade_race_result_v1"
READ_DURING_WRITE_RESULT_SCHEMA_VERSION = "read_during_write_result_v1"
NEGATIVE_GUARD_RESULT_SCHEMA_VERSION = "production_negative_guard_result_v1"
UNSUPPORTED_POLICY_RESULT_SCHEMA_VERSION = "production_unsupported_policy_result_v1"
TIME_EXPIRY_RACE_DELAY_SECONDS = 0.3
TIME_EXPIRY_RACE_STALE_SKEW_SECONDS = 0.05
MIN_CLEANUP_PREFIX_LENGTH = 5
PRODUCTION_CLEANUP_MIN_PREFIX_LENGTH = 8
PRODUCTION_CLEANUP_CONFIRM_ENV = "PRODUCTION_TEST_CLEANUP_CONFIRM"
PRODUCTION_CLEANUP_CONFIRM_VALUE = "hard-delete-test-data"
PRODUCTION_CLEANUP_ALLOWED_PREFIXES = ("PFM_", "PRODTEST_", "FMX_")
PRODUCTION_ROLE_WORKER_CONFIRM_ENV = "PRODUCTION_FULL_MATRIX_CONFIRM"
PRODUCTION_ROLE_WORKER_CONFIRM_VALUE = "execute-production-full-matrix"
SYNTHETIC_OFFER_SEED_METADATA_STALE_RETRY_ATTEMPTS = 5
LOAD_FIXTURE_IDENTITY_RETRY_ATTEMPTS = 8
LOAD_FIXTURE_MOBILE_PREFIX = "09"
LOAD_FIXTURE_MOBILE_TOTAL_DIGITS = 11
NEGATIVE_GUARD_EXECUTABLE_CASES = {
    "own_offer_request",
    "invalid_request_amount",
    "retail_lot_unavailable",
    "already_completed_offer",
    "manually_expired_offer",
    "time_expired_offer",
    "market_closed",
    "inactive_offer_owner",
    "inactive_requester",
    "trading_restricted_user",
    "watch_role_market_action",
    "accountant_market_action",
    "tier2_offer_creation",
    "tier2_telegram_request",
    "daily_trade_limit_exceeded",
    "daily_request_limit_exceeded",
    "active_commodity_limit_exceeded",
    "remote_authority_unavailable",
    "bad_internal_signature",
    "wrong_authoritative_server",
    "stale_telegram_button",
    "missing_public_offer_id",
    "cleanup_scope_violation",
}
UNSUPPORTED_POLICY_EXECUTABLE_REASONS = {
    "tier2_cannot_create_offer",
    "tier2_cannot_use_telegram_request",
}
LIKE_ESCAPE = "\\"
BROAD_CLEANUP_PREFIXES = {
    "admin",
    "bench",
    "dev",
    "load",
    "prod",
    "production",
    "stage",
    "staging",
    "test",
    "tmp",
    "user",
}
CLEANUP_DB_RETRY_ATTEMPTS = 3
RETRYABLE_CLEANUP_SQLSTATES = {"40P01", "40001"}
_PRODUCTION_CLEANUP_HARD_DELETE_ALLOWED = False


LOAD_RUNNER_ROLES = {
    "telegram_foreign": {
        "server_mode": SERVER_FOREIGN,
        "surface": "telegram",
    },
    "webapp_iran": {
        "server_mode": SERVER_IRAN,
        "surface": "webapp",
    },
}
SURFACE_TO_LOAD_RUNNER_ROLE = {config["surface"]: role for role, config in LOAD_RUNNER_ROLES.items()}

_TELEGRAM_IDEMPOTENCY_KEYS: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "trading_probe_telegram_idempotency_keys",
    default=None,
)
_ORIGINAL_CHANNEL_TRADE_IDEMPOTENCY_KEY = bot_trade_execute._channel_trade_idempotency_key


def _recording_channel_trade_idempotency_key(*args: Any, **kwargs: Any) -> str:
    key = str(_ORIGINAL_CHANNEL_TRADE_IDEMPOTENCY_KEY(*args, **kwargs))
    recorder = _TELEGRAM_IDEMPOTENCY_KEYS.get()
    if recorder is not None:
        recorder.append(key)
    return key


bot_trade_execute._channel_trade_idempotency_key = _recording_channel_trade_idempotency_key


@dataclass(frozen=True)
class FixtureUsers:
    seller_id: int
    responder_a_id: int
    responder_b_id: int

    @property
    def ids(self) -> list[int]:
        return [self.seller_id, self.responder_a_id, self.responder_b_id]


@dataclass(frozen=True)
class LoadUserRef:
    user_id: int
    telegram_id: int


@dataclass(frozen=True)
class MixedLoadAttemptSpec:
    index: int
    surface: str
    user_id: int
    telegram_id: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class MixedLoadAttemptResult:
    index: int
    surface: str
    status: str
    duration_ms: float
    detail: str | None = None
    telegram_update_count: int = 0
    start_offset_seconds: float | None = None


@dataclass(frozen=True)
class HotOfferScenarioSpec:
    name: str
    origin: str
    quantity: int
    request_amount: int
    expected_winner_count: int
    total_requests: int
    telegram_ratio: float
    target_rps: float
    offer_type: str = "sell"
    price: int = 100000
    is_wholesale: bool = True
    lot_sizes: tuple[int, ...] = ()

    @property
    def expected_completed_quantity(self) -> int:
        return self.request_amount * self.expected_winner_count

    @property
    def start_burst_request_count(self) -> int:
        # "Several dozen" concurrent requests should hit the same offer immediately.
        return min(self.total_requests, max(1, int(math.ceil(self.target_rps * 0.075))))


@dataclass(frozen=True)
class HotOfferPersistenceSnapshot:
    offer_id: int
    original_quantity: int
    remaining_quantity: int | None
    offer_status: str | None
    persisted_trade_count: int
    completed_trade_quantity: int
    completed_ledger_count: int
    trades_without_completed_ledger_count: int
    failed_internal_ledger_count: int
    duplicate_replay_ledger_count: int


@dataclass(frozen=True)
class CleanupPlan:
    prefix: str
    user_ids: list[int]
    invitation_ids: list[int]
    accountant_relation_ids: list[int]
    customer_relation_ids: list[int]
    user_session_ids: list[Any]
    session_login_request_ids: list[Any]
    recovery_request_ids: list[Any]
    recovery_admin_target_ids: list[Any]
    telegram_link_token_ids: list[int]
    push_subscription_ids: list[int]
    offer_ids: list[int]
    offer_public_ids: list[str]
    trade_ids: list[int]
    trade_delivery_receipt_ids: list[int]
    offer_request_ids: list[int]
    publication_state_ids: list[int]
    notification_ids: list[int]
    chat_member_ids: list[int]


def json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=json_safe))


def parse_lot_sizes_argument(value: str | None) -> tuple[int, ...]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ()
    normalized = raw_value.replace(",", " ")
    lots: list[int] = []
    for part in normalized.split():
        try:
            lot = int(part)
        except ValueError as exc:
            raise TradingProbeError(f"invalid lot size value: {part}") from exc
        if lot <= 0:
            raise TradingProbeError(f"lot sizes must be positive, got {lot}")
        lots.append(lot)
    return tuple(lots)


def percentile(samples: list[float], p: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(float(item) for item in samples)
    index = max(0, min(len(ordered) - 1, math.ceil((p / 100.0) * len(ordered)) - 1))
    return round(ordered[index], 3)


def summarize_samples(samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {
            "count": 0,
            "min_ms": None,
            "avg_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "max_ms": None,
        }
    normalized = [round(float(item), 3) for item in samples]
    return {
        "count": len(normalized),
        "min_ms": round(min(normalized), 3),
        "avg_ms": round(sum(normalized) / len(normalized), 3),
        "p50_ms": percentile(normalized, 50),
        "p95_ms": percentile(normalized, 95),
        "p99_ms": percentile(normalized, 99),
        "max_ms": round(max(normalized), 3),
    }


async def timed_ms(fn: Callable[[], Awaitable[Any]]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = await fn()
    return result, round((time.perf_counter() - started) * 1000.0, 3)


async def warm_load_runner_dependencies(*, db_connections: int) -> dict[str, Any]:
    """Warm local dependency pools so timed attempts measure the trade path."""
    safe_connections = min(max(int(db_connections or 1), 1), 32)

    async def warm_db_connection() -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))

    started = time.perf_counter()
    await asyncio.gather(*[warm_db_connection() for _ in range(safe_connections)])
    db_warm_ms = round((time.perf_counter() - started) * 1000.0, 3)

    redis_started = time.perf_counter()
    redis_client = await init_redis()
    await redis_client.ping()
    redis_warm_ms = round((time.perf_counter() - redis_started) * 1000.0, 3)
    return {
        "db_connections": safe_connections,
        "db_warm_ms": db_warm_ms,
        "redis_singleton_initialized": True,
        "redis_warm_ms": redis_warm_ms,
    }


def assert_race_acceptance(
    *,
    winner_count: int,
    trade_count: int,
    remaining_quantity: int | None,
    status: str | None,
    error_count: int = 0,
) -> None:
    if error_count:
        raise TradingProbeError(f"race expected zero errored attempts, got {error_count}")
    if winner_count != 1:
        raise TradingProbeError(f"race expected exactly one winner, got {winner_count}")
    if trade_count != 1:
        raise TradingProbeError(f"race expected exactly one persisted trade, got {trade_count}")
    if remaining_quantity != 0:
        raise TradingProbeError(f"race expected remaining_quantity=0, got {remaining_quantity}")
    if status != OfferStatus.COMPLETED.value:
        raise TradingProbeError(f"race expected offer status completed, got {status}")


def build_mixed_surface_plan(
    *,
    users: list[LoadUserRef],
    owner_user_id: int,
    total_requests: int,
    telegram_ratio: float,
    request_surface: str | None = None,
    idempotency_mode: str = "unique",
    duplicate_idempotency_key: str | None = None,
) -> list[MixedLoadAttemptSpec]:
    if total_requests <= 0:
        raise TradingProbeError("mixed load total_requests must be positive")
    forced_surface = str(request_surface or "").strip().lower() or None
    if forced_surface is not None and forced_surface not in SURFACE_TO_LOAD_RUNNER_ROLE:
        raise TradingProbeError(f"unsupported request_surface: {request_surface}")
    normalized_idempotency_mode = str(idempotency_mode or "unique").strip().lower()
    if normalized_idempotency_mode not in {"unique", "duplicate_replay"}:
        raise TradingProbeError(f"unsupported idempotency_mode: {idempotency_mode}")
    if forced_surface is None and not 0 < telegram_ratio < 1:
        raise TradingProbeError("mixed load telegram_ratio must be between 0 and 1")
    responders = [user for user in users if user.user_id != owner_user_id]
    if not responders:
        raise TradingProbeError("mixed load requires at least one responder distinct from the owner")
    if normalized_idempotency_mode == "duplicate_replay" and not duplicate_idempotency_key:
        raise TradingProbeError("duplicate_replay idempotency mode requires duplicate_idempotency_key")

    telegram_slots_per_ten = int(round(telegram_ratio * 10))
    telegram_slots_per_ten = max(1, min(9, telegram_slots_per_ten))
    plan: list[MixedLoadAttemptSpec] = []
    for index in range(total_requests):
        if normalized_idempotency_mode == "duplicate_replay":
            responder = responders[0]
            idempotency_key = duplicate_idempotency_key
        else:
            responder = responders[index % len(responders)]
            idempotency_key = None
        surface = forced_surface or ("telegram" if index % 10 < telegram_slots_per_ten else "webapp")
        plan.append(
            MixedLoadAttemptSpec(
                index=index,
                surface=surface,
                user_id=responder.user_id,
                telegram_id=responder.telegram_id,
                idempotency_key=idempotency_key,
            )
        )
    return plan


def server_for_load_surface(surface: str) -> str:
    normalized = str(surface or "").strip().lower()
    if normalized == "telegram":
        return SERVER_FOREIGN
    if normalized == "webapp":
        return SERVER_IRAN
    raise TradingProbeError(f"unsupported load surface: {surface}")


def synthetic_load_user_refs(
    *,
    user_count: int,
    start_user_id: int = 1,
    start_telegram_id: int = 9_000_000_000,
) -> list[LoadUserRef]:
    if user_count <= 1:
        raise TradingProbeError("synthetic load plan requires at least two users")
    return [
        LoadUserRef(user_id=start_user_id + index, telegram_id=start_telegram_id + index)
        for index in range(user_count)
    ]


def mixed_attempt_to_artifact_dict(spec: MixedLoadAttemptSpec) -> dict[str, Any]:
    return asdict(spec)


def mixed_attempt_from_artifact_dict(raw: Mapping[str, Any]) -> MixedLoadAttemptSpec:
    try:
        index = int(raw["index"])
        surface = str(raw["surface"])
        user_id = int(raw["user_id"])
        telegram_id = int(raw["telegram_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TradingProbeError(f"invalid mixed load attempt artifact: {exc}") from exc
    idempotency_key = raw.get("idempotency_key")
    if idempotency_key is not None:
        idempotency_key = str(idempotency_key).strip()
        if not idempotency_key:
            idempotency_key = None
    if index < 0:
        raise TradingProbeError(f"invalid mixed load attempt index: {index}")
    if surface not in SURFACE_TO_LOAD_RUNNER_ROLE:
        raise TradingProbeError(f"unsupported mixed load attempt surface: {surface}")
    if user_id <= 0 or telegram_id <= 0:
        raise TradingProbeError("mixed load attempt user ids must be positive")
    return MixedLoadAttemptSpec(
        index=index,
        surface=surface,
        user_id=user_id,
        telegram_id=telegram_id,
        idempotency_key=idempotency_key,
    )


def split_mixed_plan_by_role(plan: list[MixedLoadAttemptSpec]) -> dict[str, list[MixedLoadAttemptSpec]]:
    split: dict[str, list[MixedLoadAttemptSpec]] = {role: [] for role in LOAD_RUNNER_ROLES}
    for spec in plan:
        role = SURFACE_TO_LOAD_RUNNER_ROLE.get(spec.surface)
        if role is None:
            raise TradingProbeError(f"unsupported mixed load attempt surface: {spec.surface}")
        split[role].append(spec)
    return split


def build_dual_role_worker_plans(
    *,
    run_id: str,
    prefix: str,
    users: list[LoadUserRef],
    owner_user_id: int,
    offer_id: int,
    offer_public_id: str | None,
    total_requests: int,
    telegram_ratio: float,
    target_rps: float,
    amount: int,
    barrier_epoch: float,
    request_surface: str | None = None,
    idempotency_mode: str = "unique",
) -> dict[str, dict[str, Any]]:
    normalized_run_id = str(run_id or "").strip()
    normalized_prefix = str(prefix or "").strip()
    if not normalized_run_id:
        raise TradingProbeError("dual-role run_id is required")
    if not normalized_prefix:
        raise TradingProbeError("dual-role prefix is required")
    if offer_id <= 0:
        raise TradingProbeError("dual-role offer_id must be positive")
    if amount <= 0:
        raise TradingProbeError("dual-role request amount must be positive")
    if target_rps <= 0:
        raise TradingProbeError("dual-role target_rps must be positive")
    if not math.isfinite(float(barrier_epoch)):
        raise TradingProbeError("dual-role barrier_epoch must be finite")

    duplicate_idempotency_key = None
    if str(idempotency_mode or "unique").strip().lower() == "duplicate_replay":
        duplicate_idempotency_key = build_role_attempt_idempotency_key(
            prefix=normalized_prefix,
            role="duplicate_replay",
            offer_id=offer_id,
            attempt_index=0,
        )
    plan = build_mixed_surface_plan(
        users=users,
        owner_user_id=owner_user_id,
        total_requests=total_requests,
        telegram_ratio=telegram_ratio,
        request_surface=request_surface,
        idempotency_mode=idempotency_mode,
        duplicate_idempotency_key=duplicate_idempotency_key,
    )
    split = split_mixed_plan_by_role(plan)
    artifact_plans: dict[str, dict[str, Any]] = {}
    normalized_offer_public_id = str(offer_public_id).strip() if offer_public_id else None
    for role, role_config in LOAD_RUNNER_ROLES.items():
        surface = str(role_config["surface"])
        attempts = split[role]
        artifact_plans[role] = {
            "schema_version": DUAL_ROLE_PLAN_SCHEMA_VERSION,
            "run_id": normalized_run_id,
            "prefix": normalized_prefix,
            "role": role,
            "surface": surface,
            "barrier_epoch": round(float(barrier_epoch), 6),
            "target_rps": float(target_rps),
            "request_amount": int(amount),
            "offer": {
                "id": int(offer_id),
                "public_id": normalized_offer_public_id,
                "owner_user_id": int(owner_user_id),
            },
            "attempts": [mixed_attempt_to_artifact_dict(spec) for spec in attempts],
        }
    return artifact_plans


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TradingProbeError(f"{name} must be an object")
    return value


def validate_role_plan_artifact(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = _require_mapping(raw, "role plan")
    if payload.get("schema_version") != DUAL_ROLE_PLAN_SCHEMA_VERSION:
        raise TradingProbeError("unsupported role plan schema_version")
    run_id = str(payload.get("run_id") or "").strip()
    prefix = str(payload.get("prefix") or "").strip()
    role = str(payload.get("role") or "").strip()
    surface = str(payload.get("surface") or "").strip()
    if not run_id:
        raise TradingProbeError("role plan run_id is required")
    if not prefix:
        raise TradingProbeError("role plan prefix is required")
    role_config = LOAD_RUNNER_ROLES.get(role)
    if role_config is None:
        raise TradingProbeError(f"unsupported role plan role: {role}")
    if surface != role_config["surface"]:
        raise TradingProbeError(f"role plan surface must be {role_config['surface']} for {role}")
    try:
        barrier_epoch = float(payload["barrier_epoch"])
        target_rps = float(payload["target_rps"])
        request_amount = int(payload["request_amount"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TradingProbeError(f"invalid role plan timing/request fields: {exc}") from exc
    if not math.isfinite(barrier_epoch):
        raise TradingProbeError("role plan barrier_epoch must be finite")
    if target_rps <= 0:
        raise TradingProbeError("role plan target_rps must be positive")
    if request_amount <= 0:
        raise TradingProbeError("role plan request_amount must be positive")

    offer = _require_mapping(payload.get("offer"), "role plan offer")
    try:
        offer_id = int(offer["id"])
        owner_user_id = int(offer["owner_user_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TradingProbeError(f"invalid role plan offer fields: {exc}") from exc
    if offer_id <= 0 or owner_user_id <= 0:
        raise TradingProbeError("role plan offer ids must be positive")
    if "public_id" not in offer:
        raise TradingProbeError("role plan offer.public_id field is required")

    attempts = payload.get("attempts")
    if not isinstance(attempts, list):
        raise TradingProbeError("role plan attempts must be a list")
    for attempt in attempts:
        spec = mixed_attempt_from_artifact_dict(_require_mapping(attempt, "role plan attempt"))
        if spec.surface != surface:
            raise TradingProbeError("role plan attempt surface does not match role surface")
    return payload


def role_plan_attempt_specs(plan_payload: Mapping[str, Any]) -> list[MixedLoadAttemptSpec]:
    validated = validate_role_plan_artifact(plan_payload)
    return [
        mixed_attempt_from_artifact_dict(_require_mapping(attempt, "role plan attempt"))
        for attempt in validated["attempts"]
    ]


def assert_role_plan_barrier_skew(
    plans: Mapping[str, Mapping[str, Any]],
    *,
    max_skew_seconds: float,
) -> dict[str, Any]:
    if max_skew_seconds < 0:
        raise TradingProbeError("max_skew_seconds must be non-negative")
    barriers = {
        role: float(validate_role_plan_artifact(plan)["barrier_epoch"])
        for role, plan in plans.items()
    }
    if not barriers:
        raise TradingProbeError("at least one role plan is required")
    observed_skew = max(barriers.values()) - min(barriers.values())
    if observed_skew > max_skew_seconds:
        raise TradingProbeError(
            f"role plan barrier skew {observed_skew:.6f}s exceeds {max_skew_seconds:.6f}s"
        )
    return {
        "max_skew_seconds": round(float(max_skew_seconds), 6),
        "observed_skew_seconds": round(float(observed_skew), 6),
        "barriers": barriers,
    }


def build_role_attempt_idempotency_key(
    *,
    prefix: str,
    role: str,
    offer_id: int,
    attempt_index: int,
) -> str:
    raw = f"{prefix}|{role}|{offer_id}|{attempt_index}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    role_token = "".join(ch if ch.isalnum() else "-" for ch in role.lower())[:12] or "role"
    return f"load:{role_token}:{int(offer_id)}:{int(attempt_index)}:{digest}"


def _validate_role_result_artifact(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = _require_mapping(raw, "role result")
    if payload.get("schema_version") != DUAL_ROLE_RESULT_SCHEMA_VERSION:
        raise TradingProbeError("unsupported role result schema_version")
    role = str(payload.get("role") or "").strip()
    surface = str(payload.get("surface") or "").strip()
    role_config = LOAD_RUNNER_ROLES.get(role)
    if role_config is None:
        raise TradingProbeError(f"unsupported role result role: {role}")
    if surface != role_config["surface"]:
        raise TradingProbeError(f"role result surface must be {role_config['surface']} for {role}")
    if not str(payload.get("run_id") or "").strip():
        raise TradingProbeError("role result run_id is required")
    try:
        float(payload["started_epoch"])
        float(payload["finished_epoch"])
        float(payload["started_monotonic"])
        float(payload["finished_monotonic"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TradingProbeError(f"invalid role result timing fields: {exc}") from exc
    attempts = payload.get("attempts")
    if not isinstance(attempts, list):
        raise TradingProbeError("role result attempts must be a list")
    required_attempt_fields = {
        "index",
        "monotonic_timestamp",
        "source_role",
        "source_surface",
        "user_id",
        "offer_public_id",
        "idempotency_key",
        "outcome",
        "latency_ms",
    }
    for attempt in attempts:
        attempt_payload = _require_mapping(attempt, "role result attempt")
        missing = sorted(required_attempt_fields - set(attempt_payload))
        if missing:
            raise TradingProbeError(f"role result attempt missing fields: {', '.join(missing)}")
        if attempt_payload["source_role"] != role or attempt_payload["source_surface"] != surface:
            raise TradingProbeError("role result attempt source does not match role result")
    return payload


def assert_role_result_start_skew(
    results: list[Mapping[str, Any]],
    *,
    max_skew_seconds: float,
) -> dict[str, Any]:
    if max_skew_seconds < 0:
        raise TradingProbeError("max_skew_seconds must be non-negative")
    started: dict[str, float] = {}
    for result in results:
        payload = _validate_role_result_artifact(result)
        started[str(payload["role"])] = float(payload["started_epoch"])
    if not started:
        raise TradingProbeError("at least one role result is required")
    observed_skew = max(started.values()) - min(started.values())
    if observed_skew > max_skew_seconds:
        raise TradingProbeError(
            f"role result start skew {observed_skew:.6f}s exceeds {max_skew_seconds:.6f}s"
        )
    return {
        "max_skew_seconds": round(float(max_skew_seconds), 6),
        "observed_skew_seconds": round(float(observed_skew), 6),
        "started_epochs": started,
    }


def _merged_attempt_elapsed_seconds(attempts: list[dict[str, Any]]) -> float | None:
    if not attempts:
        return None
    starts: list[float] = []
    finishes: list[float] = []
    for attempt in attempts:
        try:
            started = float(attempt["monotonic_timestamp"])
            duration_seconds = max(float(attempt["latency_ms"]), 0.0) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        starts.append(started)
        finishes.append(started + duration_seconds)
    if not starts or not finishes:
        return None
    return max(finishes) - min(starts)


def _merged_attempt_start_elapsed_seconds(attempts: list[dict[str, Any]]) -> float | None:
    starts: list[float] = []
    for attempt in attempts:
        try:
            starts.append(float(attempt["monotonic_timestamp"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(starts) < 2:
        return None
    return max(starts) - min(starts)


def merge_role_result_artifacts(result_payloads: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not result_payloads:
        raise TradingProbeError("at least one role result artifact is required")

    run_id: str | None = None
    attempts: list[dict[str, Any]] = []
    summary_inputs: list[MixedLoadAttemptResult] = []
    role_summaries: dict[str, Any] = {}
    started_epochs: list[float] = []
    finished_epochs: list[float] = []
    for raw in result_payloads:
        payload = _validate_role_result_artifact(raw)
        current_run_id = str(payload["run_id"])
        if run_id is None:
            run_id = current_run_id
        elif run_id != current_run_id:
            raise TradingProbeError("cannot merge role results from different run ids")
        role = str(payload["role"])
        role_summaries[role] = payload.get("summary", {})
        started_epochs.append(float(payload["started_epoch"]))
        finished_epochs.append(float(payload["finished_epoch"]))
        for attempt in payload["attempts"]:
            attempt_payload = dict(_require_mapping(attempt, "role result attempt"))
            attempts.append(attempt_payload)
            summary_inputs.append(
                MixedLoadAttemptResult(
                    index=int(attempt_payload["index"]),
                    surface=str(attempt_payload["source_surface"]),
                    status=str(attempt_payload["outcome"]),
                    duration_ms=float(attempt_payload["latency_ms"]),
                    detail=str(attempt_payload["detail"]) if attempt_payload.get("detail") else None,
                    telegram_update_count=int(
                        attempt_payload.get("telegram_update_count")
                        or (2 if str(attempt_payload["source_surface"]) == "telegram" else 0)
                    ),
                )
            )

    attempts.sort(key=lambda item: (float(item["monotonic_timestamp"]), int(item["index"])))
    elapsed_seconds = _merged_attempt_elapsed_seconds(attempts)
    if elapsed_seconds is None:
        elapsed_seconds = max(finished_epochs) - min(started_epochs)
    attempt_start_elapsed_seconds = _merged_attempt_start_elapsed_seconds(attempts)
    summary = summarize_attempt_results(summary_inputs, elapsed_seconds=elapsed_seconds)
    if attempt_start_elapsed_seconds is not None:
        safe_attempt_start_elapsed = max(float(attempt_start_elapsed_seconds), 0.001)
        summary["attempt_start_elapsed_seconds"] = round(safe_attempt_start_elapsed, 3)
        summary["attempt_start_rps"] = round(len(attempts) / safe_attempt_start_elapsed, 3)
    return {
        "schema_version": DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "ok",
        "roles": role_summaries,
        "role_start_skew": assert_role_result_start_skew(result_payloads, max_skew_seconds=3600.0),
        "summary": summary,
        "attempts": attempts,
    }


def summarize_attempt_results(results: list[MixedLoadAttemptResult], *, elapsed_seconds: float) -> dict[str, Any]:
    by_surface: dict[str, dict[str, Any]] = {}
    for surface in ("telegram", "webapp"):
        surface_results = [item for item in results if item.surface == surface]
        by_surface[surface] = {
            "total": len(surface_results),
            "success": sum(1 for item in surface_results if item.status == "success"),
            "rejected": sum(1 for item in surface_results if item.status == "rejected"),
            "error": sum(1 for item in surface_results if item.status == "error"),
            "latency": summarize_samples([item.duration_ms for item in surface_results]),
        }

    safe_elapsed = max(float(elapsed_seconds), 0.001)
    telegram_update_count = sum(item.telegram_update_count for item in results)
    summary = {
        "total": len(results),
        "elapsed_seconds": round(safe_elapsed, 3),
        "business_request_rps": round(len(results) / safe_elapsed, 3),
        "telegram_update_count": telegram_update_count,
        "telegram_update_rps": round(telegram_update_count / safe_elapsed, 3),
        "success": sum(1 for item in results if item.status == "success"),
        "rejected": sum(1 for item in results if item.status == "rejected"),
        "error": sum(1 for item in results if item.status == "error"),
        "latency": summarize_samples([item.duration_ms for item in results]),
        "surfaces": by_surface,
    }
    error_details = sorted({str(item.detail) for item in results if item.detail})
    if error_details:
        summary["error_details"] = error_details
    start_offsets = [
        float(item.start_offset_seconds)
        for item in results
        if item.start_offset_seconds is not None
    ]
    if len(start_offsets) >= 2:
        attempt_start_elapsed = max(start_offsets) - min(start_offsets)
        safe_attempt_start_elapsed = max(float(attempt_start_elapsed), 0.001)
        summary["attempt_start_elapsed_seconds"] = round(safe_attempt_start_elapsed, 3)
        summary["attempt_start_rps"] = round(len(start_offsets) / safe_attempt_start_elapsed, 3)
    return summary


def build_hot_offer_scenario_specs(
    *,
    total_requests: int,
    telegram_ratio: float,
    target_rps: float,
    price: int,
    offer_type: str,
) -> list[HotOfferScenarioSpec]:
    if total_requests < 40:
        raise TradingProbeError("hot-offer scenarios require at least 40 requests to create several-dozen contention")
    scenarios = [
        HotOfferScenarioSpec(
            name="webapp_full_fill",
            origin="webapp",
            quantity=5,
            request_amount=5,
            expected_winner_count=1,
            total_requests=total_requests,
            telegram_ratio=telegram_ratio,
            target_rps=target_rps,
            price=price,
            offer_type=offer_type,
        ),
        HotOfferScenarioSpec(
            name="bot_full_fill",
            origin="bot",
            quantity=5,
            request_amount=5,
            expected_winner_count=1,
            total_requests=total_requests,
            telegram_ratio=telegram_ratio,
            target_rps=target_rps,
            price=price,
            offer_type=offer_type,
        ),
        HotOfferScenarioSpec(
            name="webapp_partial_fill",
            origin="webapp",
            quantity=20,
            request_amount=10,
            expected_winner_count=2,
            total_requests=total_requests,
            telegram_ratio=telegram_ratio,
            target_rps=target_rps,
            price=price,
            offer_type=offer_type,
            is_wholesale=False,
            lot_sizes=(10, 10),
        ),
        HotOfferScenarioSpec(
            name="bot_partial_fill",
            origin="bot",
            quantity=20,
            request_amount=10,
            expected_winner_count=2,
            total_requests=total_requests,
            telegram_ratio=telegram_ratio,
            target_rps=target_rps,
            price=price,
            offer_type=offer_type,
            is_wholesale=False,
            lot_sizes=(10, 10),
        ),
        HotOfferScenarioSpec(
            name="webapp_retail_lot",
            origin="webapp",
            quantity=30,
            request_amount=10,
            expected_winner_count=3,
            total_requests=total_requests,
            telegram_ratio=telegram_ratio,
            target_rps=target_rps,
            price=price,
            offer_type=offer_type,
            is_wholesale=False,
            lot_sizes=(10, 10, 10),
        ),
        HotOfferScenarioSpec(
            name="bot_retail_lot",
            origin="bot",
            quantity=30,
            request_amount=10,
            expected_winner_count=3,
            total_requests=total_requests,
            telegram_ratio=telegram_ratio,
            target_rps=target_rps,
            price=price,
            offer_type=offer_type,
            is_wholesale=False,
            lot_sizes=(10, 10, 10),
        ),
    ]
    for scenario in scenarios:
        if scenario.expected_completed_quantity != scenario.quantity:
            raise TradingProbeError(f"hot-offer scenario {scenario.name} does not complete exactly")
        if scenario.start_burst_request_count < 36:
            raise TradingProbeError(f"hot-offer scenario {scenario.name} does not create several-dozen contention")
    return scenarios


def assert_hot_offer_contention_acceptance(
    *,
    persisted_trade_count: int,
    response_success_count: int,
    error_count: int,
    remaining_quantity: int | None,
    status: str | None,
    expected_winner_count: int,
    original_quantity: int | None = None,
    completed_trade_quantity: int | None = None,
    completed_ledger_count: int | None = None,
    trades_without_completed_ledger_count: int = 0,
    failed_internal_ledger_count: int = 0,
    expected_remaining_quantity: int = 0,
    require_terminal_completed: bool = True,
) -> None:
    if error_count:
        raise TradingProbeError(f"hot-offer contention expected zero internal errors, got {error_count}")
    if persisted_trade_count != expected_winner_count:
        raise TradingProbeError(
            f"hot-offer contention expected {expected_winner_count} persisted trades, got {persisted_trade_count}"
        )
    if response_success_count != expected_winner_count:
        raise TradingProbeError(
            f"hot-offer contention expected {expected_winner_count} successful responses, got {response_success_count}"
        )
    if remaining_quantity is None or remaining_quantity < 0:
        raise TradingProbeError(f"hot-offer contention produced invalid remaining_quantity={remaining_quantity}")
    if remaining_quantity != expected_remaining_quantity:
        raise TradingProbeError(
            f"hot-offer contention expected remaining_quantity={expected_remaining_quantity}, got {remaining_quantity}"
        )
    if original_quantity is not None and completed_trade_quantity is not None:
        if completed_trade_quantity > original_quantity:
            raise TradingProbeError(
                f"hot-offer contention over-traded quantity {completed_trade_quantity} > {original_quantity}"
            )
        expected_completed_quantity = original_quantity - expected_remaining_quantity
        if completed_trade_quantity != expected_completed_quantity:
            raise TradingProbeError(
                "hot-offer contention expected completed quantity "
                f"{expected_completed_quantity}, got {completed_trade_quantity}"
            )
    if completed_ledger_count is not None and completed_ledger_count < persisted_trade_count:
        raise TradingProbeError(
            "hot-offer contention has persisted trades without corresponding completed request ledger rows"
        )
    if trades_without_completed_ledger_count:
        raise TradingProbeError(
            "hot-offer contention has persisted trades without corresponding successful request ledger rows"
        )
    if failed_internal_ledger_count:
        raise TradingProbeError(
            f"hot-offer contention expected zero failed_internal ledgers, got {failed_internal_ledger_count}"
        )
    if require_terminal_completed and status != OfferStatus.COMPLETED.value:
        raise TradingProbeError(f"hot-offer contention expected completed status, got {status}")


def assert_load_runner_runtime_surface(
    role: str,
    *,
    allow_production: bool = False,
    prefix: str | None = None,
) -> dict[str, Any]:
    role_config = LOAD_RUNNER_ROLES.get(role)
    if role_config is None:
        raise TradingProbeError(f"unsupported load runner role: {role}")

    configured_environment = str(getattr(settings, "environment", "") or "").strip().lower()
    configured_service = str(getattr(settings, "trading_bot_service", "") or "").strip().lower()
    configured_server_mode = normalize_server(getattr(settings, "server_mode", None), default="")
    reasons: list[str] = []

    production_allowed = configured_environment == "production" and allow_production
    if configured_environment != "staging" and not production_allowed:
        reasons.append("ENVIRONMENT must be staging for load runner runtime")
    if configured_service != "load_runner":
        reasons.append("TRADING_BOT_SERVICE must be load_runner for load runner runtime")
    if configured_server_mode != role_config["server_mode"]:
        reasons.append(f"SERVER_MODE must be {role_config['server_mode']} for {role} load runner")
    if getattr(settings, "bot_token", None):
        reasons.append("BOT_TOKEN must be empty for load runner runtime")
    if production_allowed:
        try:
            validate_production_cleanup_prefix(prefix or "")
        except TradingProbeError as exc:
            reasons.append(str(exc))
        if os.getenv(PRODUCTION_ROLE_WORKER_CONFIRM_ENV) != PRODUCTION_ROLE_WORKER_CONFIRM_VALUE:
            reasons.append(
                f"{PRODUCTION_ROLE_WORKER_CONFIRM_ENV}={PRODUCTION_ROLE_WORKER_CONFIRM_VALUE} is required"
            )

    if reasons:
        raise TradingProbeError("; ".join(reasons))

    return {
        "status": "ok",
        "role": role,
        "surface": role_config["surface"],
        "environment": configured_environment,
        "server_mode": configured_server_mode,
        "service": configured_service,
        "production_execution_allowed": production_allowed,
        "telegram_credential_configured": False,
    }


def assert_production_full_matrix_allowed(prefix: str, *, allow_flag: bool) -> None:
    if not is_production_runtime():
        return
    validate_production_cleanup_prefix(prefix)
    if not allow_flag or os.getenv(PRODUCTION_ROLE_WORKER_CONFIRM_ENV) != PRODUCTION_ROLE_WORKER_CONFIRM_VALUE:
        raise TradingProbeError(
            "production full-matrix execution requires --allow-production-execution and "
            f"{PRODUCTION_ROLE_WORKER_CONFIRM_ENV}={PRODUCTION_ROLE_WORKER_CONFIRM_VALUE}"
        )


def assert_dual_role_prepare_runtime(offer_origin: str, *, allow_production: bool, prefix: str) -> dict[str, Any]:
    normalized_origin = str(offer_origin or "").strip().lower()
    expected_server = SERVER_FOREIGN if normalized_origin == "bot" else SERVER_IRAN
    configured_server_mode = normalize_server(getattr(settings, "server_mode", None), default="")
    if configured_server_mode != expected_server:
        raise TradingProbeError(
            f"SERVER_MODE must be {expected_server} for {normalized_origin} dual-role prepare"
        )
    assert_production_full_matrix_allowed(prefix, allow_flag=allow_production)
    return {
        "status": "ok",
        "offer_origin": normalized_origin,
        "server_mode": configured_server_mode,
        "environment": str(getattr(settings, "environment", "") or "").strip().lower(),
        "production_execution_allowed": is_production_runtime() and allow_production,
    }


def validate_cleanup_prefix(prefix: str) -> str:
    normalized = str(prefix or "").strip()
    if len(normalized) < MIN_CLEANUP_PREFIX_LENGTH:
        raise TradingProbeError(
            f"cleanup prefix must be at least {MIN_CLEANUP_PREFIX_LENGTH} characters"
        )
    lowered = normalized.lower()
    if lowered in BROAD_CLEANUP_PREFIXES:
        raise TradingProbeError(f"cleanup prefix is too broad: {normalized}")
    if any(char in normalized for char in ("%", "*", "?")):
        raise TradingProbeError("cleanup prefix must not contain wildcard characters")
    return normalized


def is_production_runtime() -> bool:
    return str(getattr(settings, "environment", "") or "").strip().lower() == "production"


def validate_production_cleanup_prefix(prefix: str) -> str:
    normalized = validate_cleanup_prefix(prefix)
    if len(normalized) < PRODUCTION_CLEANUP_MIN_PREFIX_LENGTH:
        raise TradingProbeError(
            f"production cleanup prefix must be at least {PRODUCTION_CLEANUP_MIN_PREFIX_LENGTH} characters"
        )
    if not normalized.startswith(PRODUCTION_CLEANUP_ALLOWED_PREFIXES):
        allowed = ", ".join(PRODUCTION_CLEANUP_ALLOWED_PREFIXES)
        raise TradingProbeError(f"production cleanup prefix must start with one of: {allowed}")
    return normalized


def allow_production_cleanup_hard_delete(prefix: str, *, allow_flag: bool) -> None:
    global _PRODUCTION_CLEANUP_HARD_DELETE_ALLOWED
    if not is_production_runtime():
        _PRODUCTION_CLEANUP_HARD_DELETE_ALLOWED = True
        return
    validate_production_cleanup_prefix(prefix)
    confirm_value = os.getenv(PRODUCTION_CLEANUP_CONFIRM_ENV)
    if not allow_flag or confirm_value != PRODUCTION_CLEANUP_CONFIRM_VALUE:
        raise TradingProbeError(
            "production cleanup hard-delete requires --allow-production-hard-delete and "
            f"{PRODUCTION_CLEANUP_CONFIRM_ENV}={PRODUCTION_CLEANUP_CONFIRM_VALUE}"
        )
    _PRODUCTION_CLEANUP_HARD_DELETE_ALLOWED = True


def escape_sql_like_literal(value: str) -> str:
    escaped = value.replace(LIKE_ESCAPE, LIKE_ESCAPE + LIKE_ESCAPE)
    escaped = escaped.replace("%", LIKE_ESCAPE + "%")
    escaped = escaped.replace("_", LIKE_ESCAPE + "_")
    return escaped


def cleanup_prefix_patterns(prefix: str) -> tuple[str, str]:
    normalized = validate_cleanup_prefix(prefix)
    escaped = escape_sql_like_literal(normalized)
    return f"{escaped}%", f"%{escaped}%"


def in_filter(column: Any, values: list[int] | list[str]):
    return column.in_(values) if values else false()


def cleanup_mutating_statement(statement: Any) -> Any:
    if is_production_runtime() and not _PRODUCTION_CLEANUP_HARD_DELETE_ALLOWED:
        raise TradingProbeError("synthetic cleanup is disabled in production runtime")
    return statement.execution_options(is_sync=True)


def is_retryable_cleanup_database_error(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if code in RETRYABLE_CLEANUP_SQLSTATES:
        return True
    message = str(exc).lower()
    if "deadlock detected" in message or "could not serialize access" in message:
        return True
    return (
        (code == "23503" or "foreignkeyviolation" in message or "foreign key violation" in message)
        and "fk_chat_members_user" in message
    )


def cleanup_plan_counts(plan: CleanupPlan) -> dict[str, int]:
    return {
        "users": len(plan.user_ids),
        "invitations": len(plan.invitation_ids),
        "accountant_relations": len(plan.accountant_relation_ids),
        "customer_relations": len(plan.customer_relation_ids),
        "user_sessions": len(plan.user_session_ids),
        "session_login_requests": len(plan.session_login_request_ids),
        "single_session_recovery_requests": len(plan.recovery_request_ids),
        "single_session_recovery_admin_targets": len(plan.recovery_admin_target_ids),
        "telegram_link_tokens": len(plan.telegram_link_token_ids),
        "push_subscriptions": len(plan.push_subscription_ids),
        "chat_members": len(plan.chat_member_ids),
        "offers": len(plan.offer_ids),
        "offer_publication_states": len(plan.publication_state_ids),
        "offer_requests": len(plan.offer_request_ids),
        "trades": len(plan.trade_ids),
        "trade_delivery_receipts": len(plan.trade_delivery_receipt_ids),
        "notifications": len(plan.notification_ids),
    }


def cleanup_report_payload(
    *,
    plan: CleanupPlan,
    dry_run: bool,
    deleted_users: int = 0,
    deleted_invitations: int = 0,
    deleted_accountant_relations: int = 0,
    deleted_customer_relations: int = 0,
    deleted_user_sessions: int = 0,
    deleted_session_login_requests: int = 0,
    deleted_recovery_requests: int = 0,
    deleted_recovery_admin_targets: int = 0,
    deleted_telegram_link_tokens: int = 0,
    deleted_push_subscriptions: int = 0,
    deleted_chat_members: int = 0,
    deleted_offers: int = 0,
    deleted_trades: int = 0,
    deleted_trade_delivery_receipts: int = 0,
    deleted_notifications: int = 0,
    deleted_offer_requests: int = 0,
    deleted_publication_states: int = 0,
    deleted_change_logs: int = 0,
    deleted_redis_keys: int = 0,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "dry_run": dry_run,
        "prefix": plan.prefix,
        "planned_counts": cleanup_plan_counts(plan),
        "planned_ids": asdict(plan),
        "deleted_users": deleted_users,
        "deleted_invitations": deleted_invitations,
        "deleted_accountant_relations": deleted_accountant_relations,
        "deleted_customer_relations": deleted_customer_relations,
        "deleted_user_sessions": deleted_user_sessions,
        "deleted_session_login_requests": deleted_session_login_requests,
        "deleted_recovery_requests": deleted_recovery_requests,
        "deleted_recovery_admin_targets": deleted_recovery_admin_targets,
        "deleted_telegram_link_tokens": deleted_telegram_link_tokens,
        "deleted_push_subscriptions": deleted_push_subscriptions,
        "deleted_chat_members": deleted_chat_members,
        "deleted_offers": deleted_offers,
        "deleted_trades": deleted_trades,
        "deleted_trade_delivery_receipts": deleted_trade_delivery_receipts,
        "deleted_notifications": deleted_notifications,
        "deleted_offer_requests": deleted_offer_requests,
        "deleted_publication_states": deleted_publication_states,
        "deleted_change_logs": deleted_change_logs,
        "deleted_redis_keys": deleted_redis_keys,
    }


async def cleanup_redis_for_user_ids(user_ids: list[int], *, dry_run: bool = False) -> int:
    if not user_ids:
        return 0
    deleted = 0
    client = redis.Redis(connection_pool=pool)
    try:
        keys: list[str] = []
        today = datetime.utcnow().strftime("%Y-%m-%d")
        user_id_set = {str(int(user_id)) for user_id in user_ids}
        for user_id in user_ids:
            keys.extend(
                [
                    f"expire_rate:{user_id}",
                    f"user:{user_id}:active_offer_count",
                    f"user:{user_id}:unread_count",
                    f"daily_expire:{user_id}:{today}",
                ]
            )
        for pattern in ("expire_rate:*", "confirm:*", "daily_expire:*", "user:*"):
            async for key in client.scan_iter(match=pattern):
                key_text = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                parts = key_text.split(":")
                if len(parts) >= 2 and parts[1] in user_id_set:
                    keys.append(key_text)
        if keys:
            unique_keys = sorted(set(keys))
            deleted = len(unique_keys) if dry_run else int(await client.delete(*unique_keys) or 0)
    finally:
        await client.aclose()
    return deleted


async def collect_cleanup_plan(prefix: str) -> CleanupPlan:
    normalized_prefix = validate_cleanup_prefix(prefix)
    pattern, contains_pattern = cleanup_prefix_patterns(normalized_prefix)
    async with AsyncSessionLocal() as db:
        user_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(User.id).where(
                        (User.account_name.like(pattern, escape=LIKE_ESCAPE))
                        | (User.mobile_number.like(pattern, escape=LIKE_ESCAPE))
                    )
                )
            ).scalars().all()
        ]
        invitation_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(Invitation.id).where(
                        (Invitation.account_name.like(pattern, escape=LIKE_ESCAPE))
                        | (Invitation.mobile_number.like(pattern, escape=LIKE_ESCAPE))
                        | in_filter(Invitation.created_by_id, user_ids)
                    )
                )
            ).scalars().all()
        ]
        accountant_relation_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(AccountantRelation.id).where(
                        in_filter(AccountantRelation.owner_user_id, user_ids)
                        | in_filter(AccountantRelation.accountant_user_id, user_ids)
                        | in_filter(AccountantRelation.created_by_user_id, user_ids)
                        | AccountantRelation.global_account_name.like(pattern, escape=LIKE_ESCAPE)
                        | AccountantRelation.relation_display_name.like(contains_pattern, escape=LIKE_ESCAPE)
                        | AccountantRelation.mobile_number.like(pattern, escape=LIKE_ESCAPE)
                    )
                )
            ).scalars().all()
        ]
        customer_relation_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(CustomerRelation.id).where(
                        in_filter(CustomerRelation.owner_user_id, user_ids)
                        | in_filter(CustomerRelation.customer_user_id, user_ids)
                        | in_filter(CustomerRelation.created_by_user_id, user_ids)
                        | CustomerRelation.management_name.like(contains_pattern, escape=LIKE_ESCAPE)
                        | CustomerRelation.invitation_token.like(contains_pattern, escape=LIKE_ESCAPE)
                    )
                )
            ).scalars().all()
        ]
        user_session_ids = list(
            (
                await db.execute(select(UserSession.id).where(in_filter(UserSession.user_id, user_ids)))
            ).scalars().all()
        )
        session_login_request_ids = list(
            (
                await db.execute(
                    select(SessionLoginRequest.id).where(in_filter(SessionLoginRequest.user_id, user_ids))
                )
            ).scalars().all()
        )
        recovery_request_ids = list(
            (
                await db.execute(
                    select(SingleSessionRecoveryRequest.id).where(
                        in_filter(SingleSessionRecoveryRequest.user_id, user_ids)
                        | in_filter(SingleSessionRecoveryRequest.session_login_request_id, session_login_request_ids)
                    )
                )
            ).scalars().all()
        )
        recovery_admin_target_ids = list(
            (
                await db.execute(
                    select(SingleSessionRecoveryAdminTarget.id).where(
                        in_filter(SingleSessionRecoveryAdminTarget.recovery_request_id, recovery_request_ids)
                        | in_filter(SingleSessionRecoveryAdminTarget.admin_user_id, user_ids)
                    )
                )
            ).scalars().all()
        )
        telegram_link_token_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(TelegramLinkToken.id).where(in_filter(TelegramLinkToken.user_id, user_ids))
                )
            ).scalars().all()
        ]
        push_subscription_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(PushSubscription.id).where(in_filter(PushSubscription.user_id, user_ids))
                )
            ).scalars().all()
        ]
        offer_rows = (
            await db.execute(
                select(Offer.id, Offer.offer_public_id).where(
                    (Offer.notes.like(contains_pattern, escape=LIKE_ESCAPE))
                    | (Offer.idempotency_key.like(pattern, escape=LIKE_ESCAPE))
                    | in_filter(Offer.user_id, user_ids)
                )
            )
        ).all()
        offer_ids = [int(row.id) for row in offer_rows]
        offer_public_ids = sorted({str(row.offer_public_id) for row in offer_rows if row.offer_public_id})
        offer_request_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(OfferRequest.id).where(
                        (OfferRequest.idempotency_key.like(pattern, escape=LIKE_ESCAPE))
                        | in_filter(OfferRequest.local_offer_id, offer_ids)
                        | in_filter(OfferRequest.offer_public_id, offer_public_ids)
                        | in_filter(OfferRequest.requester_user_id, user_ids)
                        | in_filter(OfferRequest.actor_user_id, user_ids)
                    )
                )
            ).scalars().all()
        ]
        publication_state_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(OfferPublicationState.id).where(
                        (OfferPublicationState.dedupe_key.like(pattern, escape=LIKE_ESCAPE))
                        | in_filter(OfferPublicationState.offer_id, offer_ids)
                        | in_filter(OfferPublicationState.offer_public_id, offer_public_ids)
                    )
                )
            ).scalars().all()
        ]
        trade_rows = (
            await db.execute(
                select(Trade.id, Trade.trade_number).where(
                    (Trade.idempotency_key.like(pattern, escape=LIKE_ESCAPE))
                    | in_filter(Trade.offer_id, offer_ids)
                    | in_filter(Trade.offer_user_id, user_ids)
                    | in_filter(Trade.responder_user_id, user_ids)
                    | in_filter(Trade.actor_user_id, user_ids)
                )
            )
        ).all()
        trade_ids = [int(row.id) for row in trade_rows]
        trade_numbers = [int(row.trade_number) for row in trade_rows if row.trade_number is not None]
        trade_delivery_receipt_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(TradeDeliveryReceipt.id).where(
                        in_filter(TradeDeliveryReceipt.trade_id, trade_ids)
                        | in_filter(TradeDeliveryReceipt.trade_number, trade_numbers)
                        | in_filter(TradeDeliveryReceipt.offer_id, offer_ids)
                        | in_filter(TradeDeliveryReceipt.recipient_user_id, user_ids)
                        | TradeDeliveryReceipt.dedupe_key.like(contains_pattern, escape=LIKE_ESCAPE)
                    )
                )
            ).scalars().all()
        ]
        notification_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(Notification.id).where(
                        in_filter(Notification.user_id, user_ids)
                        | Notification.message.like(contains_pattern, escape=LIKE_ESCAPE)
                        | Notification.dedupe_key.like(contains_pattern, escape=LIKE_ESCAPE)
                    )
                )
            ).scalars().all()
        ]
        chat_member_ids = [
            int(item)
            for item in (
                await db.execute(select(ChatMember.id).where(in_filter(ChatMember.user_id, user_ids)))
            ).scalars().all()
        ]
    return CleanupPlan(
        prefix=normalized_prefix,
        user_ids=user_ids,
        invitation_ids=invitation_ids,
        accountant_relation_ids=accountant_relation_ids,
        customer_relation_ids=customer_relation_ids,
        user_session_ids=user_session_ids,
        session_login_request_ids=session_login_request_ids,
        recovery_request_ids=recovery_request_ids,
        recovery_admin_target_ids=recovery_admin_target_ids,
        telegram_link_token_ids=telegram_link_token_ids,
        push_subscription_ids=push_subscription_ids,
        offer_ids=offer_ids,
        offer_public_ids=offer_public_ids,
        trade_ids=trade_ids,
        trade_delivery_receipt_ids=trade_delivery_receipt_ids,
        offer_request_ids=offer_request_ids,
        publication_state_ids=publication_state_ids,
        notification_ids=notification_ids,
        chat_member_ids=chat_member_ids,
    )


async def cleanup_prefix(prefix: str, *, dry_run: bool = False) -> dict[str, Any]:
    plan = await collect_cleanup_plan(prefix)
    if dry_run:
        planned_redis_keys = await cleanup_redis_for_user_ids(plan.user_ids, dry_run=True)
        return cleanup_report_payload(plan=plan, dry_run=True, deleted_redis_keys=planned_redis_keys)

    last_retryable_error: BaseException | None = None
    for attempt in range(1, CLEANUP_DB_RETRY_ATTEMPTS + 1):
        try:
            return await delete_cleanup_plan(plan)
        except DBAPIError as exc:
            if not is_retryable_cleanup_database_error(exc) or attempt >= CLEANUP_DB_RETRY_ATTEMPTS:
                raise
            last_retryable_error = exc
            await asyncio.sleep(0.15 * attempt)
            plan = await collect_cleanup_plan(prefix)
    if last_retryable_error is not None:
        raise last_retryable_error
    raise TradingProbeError("cleanup retry loop exited unexpectedly")


async def delete_cleanup_plan(plan: CleanupPlan) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        deleted_invitations = 0
        deleted_accountant_relations = 0
        deleted_customer_relations = 0
        deleted_user_sessions = 0
        deleted_session_login_requests = 0
        deleted_recovery_requests = 0
        deleted_recovery_admin_targets = 0
        deleted_telegram_link_tokens = 0
        deleted_push_subscriptions = 0
        deleted_notifications = 0
        deleted_chat_members = 0
        deleted_trade_delivery_receipts = 0
        deleted_trades = 0
        deleted_offers = 0
        deleted_users = 0
        deleted_offer_requests = 0
        deleted_publication_states = 0
        if plan.user_ids:
            await db.execute(
                cleanup_mutating_statement(
                    select(User.id).where(User.id.in_(plan.user_ids)).with_for_update()
                )
            )
        if plan.recovery_admin_target_ids:
            deleted_recovery_admin_targets = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(SingleSessionRecoveryAdminTarget).where(
                                SingleSessionRecoveryAdminTarget.id.in_(plan.recovery_admin_target_ids)
                            )
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.recovery_request_ids:
            deleted_recovery_requests = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(SingleSessionRecoveryRequest).where(
                                SingleSessionRecoveryRequest.id.in_(plan.recovery_request_ids)
                            )
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.session_login_request_ids:
            deleted_session_login_requests = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(SessionLoginRequest).where(
                                SessionLoginRequest.id.in_(plan.session_login_request_ids)
                            )
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.user_session_ids:
            deleted_user_sessions = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(delete(UserSession).where(UserSession.id.in_(plan.user_session_ids)))
                    )
                ).rowcount
                or 0
            )
        if plan.telegram_link_token_ids:
            deleted_telegram_link_tokens = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(TelegramLinkToken).where(TelegramLinkToken.id.in_(plan.telegram_link_token_ids))
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.push_subscription_ids:
            deleted_push_subscriptions = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(PushSubscription).where(PushSubscription.id.in_(plan.push_subscription_ids))
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.trade_delivery_receipt_ids:
            deleted_trade_delivery_receipts = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(TradeDeliveryReceipt).where(
                                TradeDeliveryReceipt.id.in_(plan.trade_delivery_receipt_ids)
                            )
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.notification_ids:
            deleted_notifications = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(Notification).where(Notification.id.in_(plan.notification_ids))
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.publication_state_ids:
            deleted_publication_states = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(OfferPublicationState).where(OfferPublicationState.id.in_(plan.publication_state_ids))
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.offer_request_ids:
            deleted_offer_requests = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(OfferRequest).where(OfferRequest.id.in_(plan.offer_request_ids))
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.chat_member_ids:
            deleted_chat_members = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(delete(ChatMember).where(ChatMember.id.in_(plan.chat_member_ids)))
                    )
                ).rowcount
                or 0
            )
        if plan.accountant_relation_ids:
            deleted_accountant_relations = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(AccountantRelation).where(AccountantRelation.id.in_(plan.accountant_relation_ids))
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.customer_relation_ids:
            deleted_customer_relations = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(
                            delete(CustomerRelation).where(CustomerRelation.id.in_(plan.customer_relation_ids))
                        )
                    )
                ).rowcount
                or 0
            )
        if plan.invitation_ids:
            deleted_invitations = int(
                (
                    await db.execute(
                        cleanup_mutating_statement(delete(Invitation).where(Invitation.id.in_(plan.invitation_ids)))
                    )
                ).rowcount
                or 0
            )
        if plan.trade_ids:
            deleted_trades = int(
                (
                    await db.execute(cleanup_mutating_statement(delete(Trade).where(Trade.id.in_(plan.trade_ids))))
                ).rowcount
                or 0
            )
        if plan.offer_ids:
            deleted_offers = int(
                (
                    await db.execute(cleanup_mutating_statement(delete(Offer).where(Offer.id.in_(plan.offer_ids))))
                ).rowcount
                or 0
            )
        if plan.user_ids:
            deleted_chat_members += int(
                (
                    await db.execute(
                        cleanup_mutating_statement(delete(ChatMember).where(ChatMember.user_id.in_(plan.user_ids)))
                    )
                ).rowcount
                or 0
            )
            deleted_users = int(
                (
                    await db.execute(cleanup_mutating_statement(delete(User).where(User.id.in_(plan.user_ids))))
                ).rowcount
                or 0
            )

        change_log_result = await db.execute(
            cleanup_mutating_statement(
                text(
                    """
                    DELETE FROM change_log
                    WHERE strpos(data::text, :raw_prefix) > 0
                    OR (table_name = 'users' AND record_id = ANY(:user_ids))
                       OR (table_name = 'invitations' AND record_id = ANY(:invitation_ids))
                       OR (table_name = 'accountant_relations' AND record_id = ANY(:accountant_relation_ids))
                       OR (table_name = 'customer_relations' AND record_id = ANY(:customer_relation_ids))
                       OR (table_name = 'offers' AND record_id = ANY(:offer_ids))
                       OR (table_name = 'trades' AND record_id = ANY(:trade_ids))
                       OR (table_name = 'trade_delivery_receipts' AND record_id = ANY(:trade_delivery_receipt_ids))
                       OR (table_name = 'offer_requests' AND record_id = ANY(:offer_request_ids))
                       OR (table_name = 'offer_publication_states' AND record_id = ANY(:publication_state_ids))
                       OR (table_name = 'notifications' AND record_id = ANY(:notification_ids))
                       OR (table_name = 'chat_members' AND record_id = ANY(:chat_member_ids))
                    """
                )
            ),
            {
                "raw_prefix": plan.prefix,
                "user_ids": plan.user_ids or [-1],
                "invitation_ids": plan.invitation_ids or [-1],
                "accountant_relation_ids": plan.accountant_relation_ids or [-1],
                "customer_relation_ids": plan.customer_relation_ids or [-1],
                "offer_ids": plan.offer_ids or [-1],
                "trade_ids": plan.trade_ids or [-1],
                "trade_delivery_receipt_ids": plan.trade_delivery_receipt_ids or [-1],
                "offer_request_ids": plan.offer_request_ids or [-1],
                "publication_state_ids": plan.publication_state_ids or [-1],
                "notification_ids": plan.notification_ids or [-1],
                "chat_member_ids": plan.chat_member_ids or [-1],
            },
        )
        await db.commit()

    deleted_redis_keys = await cleanup_redis_for_user_ids(plan.user_ids)
    return cleanup_report_payload(
        plan=plan,
        dry_run=False,
        deleted_users=deleted_users,
        deleted_invitations=deleted_invitations,
        deleted_accountant_relations=deleted_accountant_relations,
        deleted_customer_relations=deleted_customer_relations,
        deleted_user_sessions=deleted_user_sessions,
        deleted_session_login_requests=deleted_session_login_requests,
        deleted_recovery_requests=deleted_recovery_requests,
        deleted_recovery_admin_targets=deleted_recovery_admin_targets,
        deleted_telegram_link_tokens=deleted_telegram_link_tokens,
        deleted_push_subscriptions=deleted_push_subscriptions,
        deleted_chat_members=deleted_chat_members,
        deleted_offers=deleted_offers,
        deleted_trades=deleted_trades,
        deleted_trade_delivery_receipts=deleted_trade_delivery_receipts,
        deleted_notifications=deleted_notifications,
        deleted_offer_requests=deleted_offer_requests,
        deleted_publication_states=deleted_publication_states,
        deleted_change_logs=int(change_log_result.rowcount or 0),
        deleted_redis_keys=deleted_redis_keys,
    )


async def resolve_commodity() -> tuple[int, str]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Commodity.id, Commodity.name).order_by(Commodity.id.asc()).limit(1))
        row = result.first()
    if row is None:
        raise TradingProbeError("No commodity is available for trading benchmark")
    return int(row.id), str(row.name)


async def create_fixture_users(prefix: str) -> FixtureUsers:
    setup_event_listeners()
    users = [
        User(
            account_name=f"{prefix}seller",
            mobile_number=f"09991{abs(hash(prefix + 'seller')) % 1000000:06d}",
            telegram_id=None,
            username=None,
            full_name="P7 Benchmark Seller",
            address="P7 benchmark synthetic user",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            has_bot_access=True,
            home_server=settings.server_mode,
            max_sessions=1,
            max_accountants=0,
            max_customers=0,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            can_block_users=False,
        ),
        User(
            account_name=f"{prefix}responder_a",
            mobile_number=f"09992{abs(hash(prefix + 'responder_a')) % 1000000:06d}",
            telegram_id=None,
            username=None,
            full_name="P7 Benchmark Responder A",
            address="P7 benchmark synthetic user",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            has_bot_access=True,
            home_server=settings.server_mode,
            max_sessions=1,
            max_accountants=0,
            max_customers=0,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            can_block_users=False,
        ),
        User(
            account_name=f"{prefix}responder_b",
            mobile_number=f"09993{abs(hash(prefix + 'responder_b')) % 1000000:06d}",
            telegram_id=None,
            username=None,
            full_name="P7 Benchmark Responder B",
            address="P7 benchmark synthetic user",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            has_bot_access=True,
            home_server=settings.server_mode,
            max_sessions=1,
            max_accountants=0,
            max_customers=0,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            can_block_users=False,
        ),
    ]
    async with AsyncSessionLocal() as db:
        db.add_all(users)
        await db.commit()
        for user in users:
            await db.refresh(user)
        return FixtureUsers(seller_id=users[0].id, responder_a_id=users[1].id, responder_b_id=users[2].id)


def load_fixture_identity_for_index(
    *,
    prefix: str,
    index: int,
    user_count: int,
    salt: int = 0,
) -> tuple[str, int]:
    index_width = max(3, len(str(max(user_count - 1, 0))))
    namespace_width = LOAD_FIXTURE_MOBILE_TOTAL_DIGITS - len(LOAD_FIXTURE_MOBILE_PREFIX) - index_width
    if namespace_width < 3:
        raise TradingProbeError("load fixture user_count leaves too little mobile namespace")
    digest = hashlib.sha256(f"{prefix}:{salt}".encode("utf-8")).digest()
    namespace = int.from_bytes(digest[:8], "big") % (10**namespace_width)
    mobile_number = f"{LOAD_FIXTURE_MOBILE_PREFIX}{namespace:0{namespace_width}d}{index:0{index_width}d}"
    telegram_id = 8_800_000_000 + namespace * (10**index_width) + index
    return mobile_number, telegram_id


def build_load_fixture_users(prefix: str, *, user_count: int, salt: int = 0) -> list[User]:
    users: list[User] = []
    for index in range(user_count):
        mobile_number, telegram_id = load_fixture_identity_for_index(
            prefix=prefix,
            index=index,
            user_count=user_count,
            salt=salt,
        )
        users.append(
            User(
                account_name=f"{prefix}load_{index:04d}",
                mobile_number=mobile_number,
                telegram_id=telegram_id,
                username=None,
                full_name=f"P7 Mixed Load User {index:04d}",
                address="P7 mixed load synthetic user",
                role=UserRole.STANDARD,
                account_status=UserAccountStatus.ACTIVE,
                has_bot_access=True,
                home_server=settings.server_mode,
                max_sessions=1,
                max_accountants=0,
                max_customers=0,
                max_daily_trades=None,
                max_active_commodities=None,
                max_daily_requests=None,
                can_block_users=False,
            )
        )
    return users


async def create_load_fixture_users(prefix: str, *, user_count: int) -> list[LoadUserRef]:
    if user_count < 3:
        raise TradingProbeError("mixed load requires at least 3 synthetic users")

    setup_event_listeners()
    last_error: IntegrityError | None = None
    for salt in range(LOAD_FIXTURE_IDENTITY_RETRY_ATTEMPTS):
        users = build_load_fixture_users(prefix, user_count=user_count, salt=salt)
        async with AsyncSessionLocal() as db:
            db.add_all(users)
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                last_error = exc
                continue
            refs: list[LoadUserRef] = []
            for user in users:
                await db.refresh(user)
                refs.append(LoadUserRef(user_id=int(user.id), telegram_id=int(user.telegram_id)))
            return refs
    raise TradingProbeError(
        f"could not allocate unique synthetic load user identities after "
        f"{LOAD_FIXTURE_IDENTITY_RETRY_ATTEMPTS} attempts"
    ) from last_error


async def fake_market_open(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(is_open=True, next_transition_at=None)


async def fake_market_closed(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(is_open=False, reason="negative_guard_market_closed", next_transition_at=None)


async def noop_async(*_args: Any, **_kwargs: Any) -> None:
    return None


async def noop_send_offer(*_args: Any, **_kwargs: Any) -> None:
    return None


async def noop_register_market_offer_created(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(is_open=True, offers_since_last_open=0)


async def noop_update_channel_buttons(*_args: Any, **_kwargs: Any) -> bool:
    return True


def noop_schedule_web_push(*_args: Any, **_kwargs: Any) -> None:
    return None


@asynccontextmanager
async def patched_trading_boundaries():
    import core.services.trade_service as trade_service
    import core.web_push as web_push

    original = {
        "offers_evaluate": offers_router.evaluate_current_market_schedule,
        "trades_evaluate": trades_router.evaluate_current_market_schedule,
        "send_offer": offers_router.send_offer_to_channel,
        "register_market_offer_created": offers_router.register_market_offer_created,
        "update_channel_buttons": trades_router.update_channel_buttons,
        "send_telegram_message_sync": trades_router.send_telegram_message_sync,
        "forward_trade_to_home_server": trades_router.forward_trade_to_home_server,
        "bot_forward_trade_to_home_server": bot_trade_execute.forward_trade_to_home_server,
        "offer_expiry_forward": offers_router.forward_offer_expiry_to_home_server,
        "bot_offer_expiry_forward": bot_trade_manage.forward_offer_expiry_to_home_server,
        "offer_expiry_apply_channel_state": offer_expiry_worker.apply_offer_channel_state,
        "bot_manage_apply_channel_state": bot_trade_manage.apply_offer_channel_state,
        "realtime_publish": realtime_router.publish_event,
        "realtime_publish_user": realtime_router.publish_user_event,
        "bot_market_open": bot_trade_create._bot_market_is_open,
        "schedule_market_offer_web_push": web_push.schedule_market_offer_web_push,
        "schedule_notification_web_push": web_push.schedule_notification_web_push,
        "validate_competitive_price": trade_service.validate_competitive_price,
        "detect_offer_price_warning": trade_service.detect_offer_price_warning,
    }

    async def always_valid_price(**_kwargs: Any) -> tuple[bool, str]:
        return True, ""

    async def no_price_warning(**_kwargs: Any) -> None:
        return None

    async def local_forward_trade_to_home_server(
        _target_server: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, Any]:
        target_server = normalize_server(_target_server, current_server())
        try:
            with override_current_server(target_server):
                background_tasks = BackgroundTasks()
                async with AsyncSessionLocal() as db:
                    offer_public_id = str(payload.get("offer_public_id") or "").strip()
                    offer = await trades_router._resolve_internal_offer_by_public_id(
                        db,
                        offer_public_id=offer_public_id,
                    )
                    if not offer:
                        return 404, {"detail": "لفظ یافت نشد."}
                    resolved_offer_id = int(offer.id)
                    expunge_offer = getattr(db, "expunge", None)
                    if callable(expunge_offer):
                        expunge_offer(offer)

                    responder = await db.get(User, int(payload["responder_user_id"]))
                    if not responder or responder.is_deleted:
                        return 404, {"detail": "کاربر درخواست‌دهنده یافت نشد"}

                    actor_user = responder
                    actor_user_id = payload.get("actor_user_id")
                    if actor_user_id and int(actor_user_id) != int(responder.id):
                        actor = await db.get(User, int(actor_user_id))
                        if not actor or actor.is_deleted:
                            return 404, {"detail": "کاربر اجراکننده یافت نشد"}
                        actor_user = actor

                    edge_received_at = payload.get("edge_received_at")
                    if isinstance(edge_received_at, str):
                        edge_received_at = datetime.fromisoformat(edge_received_at)
                    if not isinstance(edge_received_at, datetime):
                        edge_received_at = datetime.utcnow()

                    response = await trades_router._execute_trade_authoritatively_with_transient_retry(
                        trade_data=trades_router.TradeCreate(
                            offer_id=resolved_offer_id,
                            offer_public_id=offer_public_id,
                            quantity=int(payload["quantity"]),
                            idempotency_key=payload.get("idempotency_key"),
                        ),
                        background_tasks=background_tasks,
                        db=db,
                        context=EffectiveOwnerActor(
                            owner_user=responder,
                            actor_user=actor_user,
                            relation=None,
                            is_accountant_context=int(actor_user.id) != int(responder.id),
                        ),
                        edge_received_at=edge_received_at,
                        request_source_surface=str(payload.get("source_surface") or "webapp"),
                        request_source_server=str(payload.get("source_server") or current_server()),
                        request_pre_gated=bool(payload.get("request_pre_gated")),
                    )
                await background_tasks()
        except HTTPException as exc:
            return int(exc.status_code or 500), {"detail": exc.detail}

        if isinstance(response, JSONResponse):
            try:
                return int(response.status_code), json.loads(response.body.decode("utf-8") or "{}")
            except Exception:
                return int(response.status_code), {"detail": "invalid JSONResponse body"}
        if hasattr(response, "model_dump"):
            return 201, response.model_dump(mode="json")
        return 201, {"status": "ok"}

    async def local_forward_offer_expiry_to_home_server(
        _target_server: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, Any]:
        target_server = normalize_server(_target_server, current_server())
        try:
            with override_current_server(target_server):
                async with AsyncSessionLocal() as db:
                    offer = None
                    offer_public_id = str(payload.get("offer_public_id") or "").strip()
                    try:
                        if offer_public_id:
                            offer = (
                                await db.execute(
                                    select(Offer)
                                    .where(Offer.offer_public_id == offer_public_id)
                                    .with_for_update(nowait=True)
                                )
                            ).scalar_one_or_none()
                        if offer is None and payload.get("offer_id"):
                            offer = await db.get(
                                Offer,
                                int(payload["offer_id"]),
                                with_for_update={"nowait": True},
                            )
                    except (OperationalError, DBAPIError) as exc:
                        if offers_router.is_offer_expiry_lock_busy(exc):
                            await db.rollback()
                            return 409, {"detail": offers_router.OFFER_EXPIRY_LOCK_BUSY_DETAIL}
                        raise
                    if offer is None:
                        return 404, {"detail": "لفظ یافت نشد."}

                    owner = await db.get(User, int(payload["owner_user_id"]))
                    if not owner or owner.is_deleted:
                        return 404, {"detail": "کاربر مالک یافت نشد"}

                    actor_user_id = payload.get("actor_user_id") or payload.get("owner_user_id")
                    actor = await db.get(User, int(actor_user_id))
                    if not actor or actor.is_deleted:
                        return 404, {"detail": "کاربر اجراکننده یافت نشد"}

                    if getattr(offer, "status", None) != OfferStatus.ACTIVE:
                        return 409, {"detail": "این لفظ دیگر فعال نیست"}

                    if payload.get("expire_reason") == offers_router.OfferExpiryReason.MANUAL:
                        try:
                            await offers_router.enforce_manual_offer_expire_limits(
                                db,
                                owner_user_id=int(owner.id),
                            )
                        except offers_router.OfferManualExpireLimitError as exc:
                            return int(exc.status_code), {"detail": exc.detail}

                    await offers_router.expire_offer_authoritatively(
                        db,
                        offer,
                        offers_router.OfferExpiryCommand(
                            reason=payload.get("expire_reason") or offers_router.OfferExpiryReason.MANUAL,
                            source_surface=payload.get("source_surface")
                            or offers_router.OfferExpirySourceSurface.WEBAPP,
                            source_server=payload.get("source_server") or current_server(),
                            expired_by_user_id=int(owner.id),
                            expired_by_actor_user_id=int(actor.id),
                        ),
                    )
        except offers_router.OfferAlreadyInactiveError:
            return 409, {"detail": "این لفظ دیگر فعال نیست"}
        except offers_router.OfferNotAuthoritativeError as exc:
            return 409, {"detail": f"این لفظ باید روی سرور {exc.home_server} منقضی شود"}
        except HTTPException as exc:
            return int(exc.status_code or 500), {"detail": exc.detail}
        return 200, {"expired": True}

    offers_router.evaluate_current_market_schedule = fake_market_open
    trades_router.evaluate_current_market_schedule = fake_market_open
    offers_router.send_offer_to_channel = noop_send_offer
    offers_router.register_market_offer_created = noop_register_market_offer_created
    trades_router.update_channel_buttons = noop_update_channel_buttons
    trades_router.send_telegram_message_sync = lambda *_args, **_kwargs: None
    trades_router.forward_trade_to_home_server = local_forward_trade_to_home_server
    bot_trade_execute.forward_trade_to_home_server = local_forward_trade_to_home_server
    offers_router.forward_offer_expiry_to_home_server = local_forward_offer_expiry_to_home_server
    bot_trade_manage.forward_offer_expiry_to_home_server = local_forward_offer_expiry_to_home_server
    offer_expiry_worker.apply_offer_channel_state = noop_update_channel_buttons
    bot_trade_manage.apply_offer_channel_state = noop_update_channel_buttons
    realtime_router.publish_event = noop_async
    realtime_router.publish_user_event = noop_async
    bot_trade_create._bot_market_is_open = lambda: asyncio.sleep(0, result=True)
    web_push.schedule_market_offer_web_push = noop_schedule_web_push
    web_push.schedule_notification_web_push = noop_schedule_web_push
    trade_service.validate_competitive_price = always_valid_price
    trade_service.detect_offer_price_warning = no_price_warning
    try:
        yield
    finally:
        offers_router.evaluate_current_market_schedule = original["offers_evaluate"]
        trades_router.evaluate_current_market_schedule = original["trades_evaluate"]
        offers_router.send_offer_to_channel = original["send_offer"]
        offers_router.register_market_offer_created = original["register_market_offer_created"]
        trades_router.update_channel_buttons = original["update_channel_buttons"]
        trades_router.send_telegram_message_sync = original["send_telegram_message_sync"]
        trades_router.forward_trade_to_home_server = original["forward_trade_to_home_server"]
        bot_trade_execute.forward_trade_to_home_server = original["bot_forward_trade_to_home_server"]
        offers_router.forward_offer_expiry_to_home_server = original["offer_expiry_forward"]
        bot_trade_manage.forward_offer_expiry_to_home_server = original["bot_offer_expiry_forward"]
        offer_expiry_worker.apply_offer_channel_state = original["offer_expiry_apply_channel_state"]
        bot_trade_manage.apply_offer_channel_state = original["bot_manage_apply_channel_state"]
        realtime_router.publish_event = original["realtime_publish"]
        realtime_router.publish_user_event = original["realtime_publish_user"]
        bot_trade_create._bot_market_is_open = original["bot_market_open"]
        web_push.schedule_market_offer_web_push = original["schedule_market_offer_web_push"]
        web_push.schedule_notification_web_push = original["schedule_notification_web_push"]
        trade_service.validate_competitive_price = original["validate_competitive_price"]
        trade_service.detect_offer_price_warning = original["detect_offer_price_warning"]


@asynccontextmanager
async def patched_external_side_effects():
    """Disable external side effects while preserving real cross-server forwarding."""
    import core.services.trade_service as trade_service
    import core.web_push as web_push

    original = {
        "offers_evaluate": offers_router.evaluate_current_market_schedule,
        "trades_evaluate": trades_router.evaluate_current_market_schedule,
        "send_offer": offers_router.send_offer_to_channel,
        "register_market_offer_created": offers_router.register_market_offer_created,
        "update_channel_buttons": trades_router.update_channel_buttons,
        "send_telegram_message_sync": trades_router.send_telegram_message_sync,
        "offer_expiry_apply_channel_state": offer_expiry_worker.apply_offer_channel_state,
        "bot_manage_apply_channel_state": bot_trade_manage.apply_offer_channel_state,
        "realtime_publish": realtime_router.publish_event,
        "realtime_publish_user": realtime_router.publish_user_event,
        "bot_market_open": bot_trade_create._bot_market_is_open,
        "schedule_market_offer_web_push": web_push.schedule_market_offer_web_push,
        "schedule_notification_web_push": web_push.schedule_notification_web_push,
        "validate_competitive_price": trade_service.validate_competitive_price,
        "detect_offer_price_warning": trade_service.detect_offer_price_warning,
    }

    async def always_valid_price(**_kwargs: Any) -> tuple[bool, str]:
        return True, ""

    async def no_price_warning(**_kwargs: Any) -> None:
        return None

    offers_router.evaluate_current_market_schedule = fake_market_open
    trades_router.evaluate_current_market_schedule = fake_market_open
    offers_router.send_offer_to_channel = noop_send_offer
    offers_router.register_market_offer_created = noop_register_market_offer_created
    trades_router.update_channel_buttons = noop_update_channel_buttons
    trades_router.send_telegram_message_sync = lambda *_args, **_kwargs: None
    offer_expiry_worker.apply_offer_channel_state = noop_update_channel_buttons
    bot_trade_manage.apply_offer_channel_state = noop_update_channel_buttons
    realtime_router.publish_event = noop_async
    realtime_router.publish_user_event = noop_async
    bot_trade_create._bot_market_is_open = lambda: asyncio.sleep(0, result=True)
    web_push.schedule_market_offer_web_push = noop_schedule_web_push
    web_push.schedule_notification_web_push = noop_schedule_web_push
    trade_service.validate_competitive_price = always_valid_price
    trade_service.detect_offer_price_warning = no_price_warning
    try:
        yield
    finally:
        offers_router.evaluate_current_market_schedule = original["offers_evaluate"]
        trades_router.evaluate_current_market_schedule = original["trades_evaluate"]
        offers_router.send_offer_to_channel = original["send_offer"]
        offers_router.register_market_offer_created = original["register_market_offer_created"]
        trades_router.update_channel_buttons = original["update_channel_buttons"]
        trades_router.send_telegram_message_sync = original["send_telegram_message_sync"]
        offer_expiry_worker.apply_offer_channel_state = original["offer_expiry_apply_channel_state"]
        bot_trade_manage.apply_offer_channel_state = original["bot_manage_apply_channel_state"]
        realtime_router.publish_event = original["realtime_publish"]
        realtime_router.publish_user_event = original["realtime_publish_user"]
        bot_trade_create._bot_market_is_open = original["bot_market_open"]
        web_push.schedule_market_offer_web_push = original["schedule_market_offer_web_push"]
        web_push.schedule_notification_web_push = original["schedule_notification_web_push"]
        trade_service.validate_competitive_price = original["validate_competitive_price"]
        trade_service.detect_offer_price_warning = original["detect_offer_price_warning"]


def owner_context(user: User) -> EffectiveOwnerActor:
    return EffectiveOwnerActor(owner_user=user, actor_user=user, relation=None, is_accountant_context=False)


async def load_user(session, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise TradingProbeError(f"synthetic user {user_id} disappeared")
    return user


async def load_user_ref(user_id: int) -> LoadUserRef:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        telegram_id = getattr(user, "telegram_id", None)
        if telegram_id is None:
            raise TradingProbeError(f"synthetic user {user_id} has no telegram_id")
        return LoadUserRef(user_id=int(user_id), telegram_id=int(telegram_id))


async def seed_offer_runtime_metadata_with_retry(
    db,
    *,
    offer_id: int,
    channel_message_id: int | None = None,
    time_limit_buffer_minutes: int | None = None,
) -> None:
    for attempt in range(SYNTHETIC_OFFER_SEED_METADATA_STALE_RETRY_ATTEMPTS):
        offer = await db.get(Offer, offer_id, populate_existing=True)
        if offer is None:
            raise TradingProbeError(f"created offer {offer_id} disappeared before runtime metadata seed")
        if channel_message_id is not None:
            offer.channel_message_id = int(channel_message_id)
        if time_limit_buffer_minutes is not None:
            buffered_created_at = datetime.utcnow() + timedelta(minutes=int(time_limit_buffer_minutes))
            offer.created_at = buffered_created_at
            offer.updated_at = buffered_created_at
        try:
            await db.commit()
            return
        except StaleDataError:
            await db.rollback()
            if attempt >= SYNTHETIC_OFFER_SEED_METADATA_STALE_RETRY_ATTEMPTS - 1:
                raise
            await asyncio.sleep(0.05 * (attempt + 1))


async def create_offer_for_user(
    *,
    user_id: int,
    commodity_id: int,
    prefix: str,
    index: int,
    offer_type: str = "sell",
    quantity: int = 5,
    price: int = 100000,
    is_wholesale: bool = True,
    lot_sizes: list[int] | tuple[int, ...] | None = None,
    channel_message_id: int | None = None,
    time_limit_buffer_minutes: int | None = None,
    source_surface: OfferSourceSurface | str = OfferSourceSurface.WEBAPP,
) -> int:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        normalized_lot_sizes = list(lot_sizes) if lot_sizes else None
        normalized_source_surface = normalize_offer_source_surface(source_surface)
        if normalized_source_surface == OfferSourceSurface.WEBAPP:
            response = await offers_router.create_offer(
                offers_router.OfferCreate(
                    offer_type=offer_type,
                    commodity_id=commodity_id,
                    quantity=quantity,
                    price=price,
                    is_wholesale=is_wholesale,
                    lot_sizes=normalized_lot_sizes,
                    notes=f"{prefix} offer {index}",
                    warning_acknowledged=True,
                ),
                db=db,
                current_user=user,
                context=owner_context(user),
            )
            if not hasattr(response, "id"):
                raise TradingProbeError(f"create_offer returned unexpected response: {response!r}")
            offer_id = int(response.id)
        else:
            if normalized_source_surface != OfferSourceSurface.TELEGRAM_BOT:
                raise TradingProbeError(f"unsupported synthetic offer source_surface={source_surface!r}")
            offer = await create_authoritative_offer(
                db,
                OfferCreationCommand(
                    source_surface=OfferSourceSurface.TELEGRAM_BOT,
                    owner_user_id=user.id,
                    actor_user_id=user.id,
                    offer_type=OfferType.BUY if offer_type == "buy" else OfferType.SELL,
                    commodity_id=commodity_id,
                    quantity=quantity,
                    price=price,
                    is_wholesale=is_wholesale,
                    lot_sizes=normalized_lot_sizes,
                    original_lot_sizes=normalized_lot_sizes,
                    notes=f"{prefix} offer {index}",
                    status=OfferStatus.ACTIVE,
                ),
            )
            offer_id = int(offer.id)
        if channel_message_id is not None or time_limit_buffer_minutes is not None:
            await seed_offer_runtime_metadata_with_retry(
                db,
                offer_id=offer_id,
                channel_message_id=channel_message_id,
                time_limit_buffer_minutes=time_limit_buffer_minutes,
            )
        return offer_id


async def expire_offer_for_user(*, user_id: int, offer_id: int) -> None:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        response = await offers_router.expire_offer(offer_id, db=db, context=owner_context(user))
        if getattr(response, "status_code", 204) >= 400:
            detail: Any = getattr(response, "body", b"")
            if isinstance(response, JSONResponse):
                try:
                    body = json.loads(response.body.decode("utf-8") or "{}")
                    detail = body.get("detail") or body
                except Exception:
                    detail = "invalid JSONResponse body"
            raise HTTPException(status_code=int(response.status_code), detail=detail)


async def list_active_offers_for_user(*, user_id: int, limit: int = 30) -> int:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        offers = await offers_router.get_active_offers(
            offer_type=None,
            commodity_id=None,
            skip=0,
            limit=limit,
            db=db,
            current_user=user,
            context=owner_context(user),
        )
        return len(offers)


async def list_market_history_for_user(*, user_id: int, limit: int = 30) -> int:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        offers = await offers_router.get_market_offer_history(
            skip=0,
            limit=limit,
            since_hours=48,
            db=db,
            current_user=user,
            context=owner_context(user),
        )
        return len(offers)


async def load_public_offer_detail_for_user(*, user_id: int, offer_public_id: str, limit: int = 30) -> int:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        detail = await offers_router.get_public_offer_detail(
            offer_public_id=offer_public_id,
            limit=limit,
            offset=0,
            db=db,
            current_user=user,
        )
        return len(getattr(detail, "request_ledger", []) or [])


async def age_offer_for_time_expiry(*, offer_id: int, age_minutes: int = 10) -> None:
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, offer_id)
        if offer is None:
            raise TradingProbeError(f"offer {offer_id} disappeared before age update")
        offer.created_at = datetime.utcnow() - timedelta(minutes=age_minutes)
        await db.commit()


async def schedule_offer_for_time_expiry_race(*, offer_id: int, stale_epoch: float) -> dict[str, Any]:
    from core.trading_settings import get_trading_settings_async

    trading_settings = await get_trading_settings_async()
    expiry_minutes = int(getattr(trading_settings, "offer_expiry_minutes", 0) or 0)
    if expiry_minutes <= 0:
        raise TradingProbeError("time-expiry race requires positive offer_expiry_minutes")
    stale_at = datetime.fromtimestamp(float(stale_epoch), tz=timezone.utc).replace(tzinfo=None)
    created_at = stale_at - timedelta(minutes=expiry_minutes)
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, offer_id)
        if offer is None:
            raise TradingProbeError(f"offer {offer_id} disappeared before time-expiry race scheduling")
        offer.created_at = created_at
        offer.updated_at = created_at
        await db.commit()
    return {
        "offer_expiry_minutes": expiry_minutes,
        "created_at": created_at.isoformat(),
        "stale_epoch": round(float(stale_epoch), 6),
    }


async def update_synthetic_user_for_negative_guard(user_id: int, **values: Any) -> None:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise TradingProbeError(f"negative guard synthetic user {user_id} disappeared")
        for key, value in values.items():
            setattr(user, key, value)
        await db.commit()


async def create_active_customer_relation_for_negative_guard(
    *,
    owner_user_id: int,
    customer_user_id: int,
    prefix: str,
    tier: CustomerTier,
) -> int:
    now = datetime.utcnow()
    relation = CustomerRelation(
        owner_user_id=int(owner_user_id),
        customer_user_id=int(customer_user_id),
        created_by_user_id=int(owner_user_id),
        invitation_token=f"{prefix}negative-guard-customer-{customer_user_id}"[:128],
        management_name=f"{prefix}customer_{customer_user_id}"[:120],
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
    async with AsyncSessionLocal() as db:
        db.add(relation)
        await db.commit()
        await db.refresh(relation)
        return int(relation.id)


async def count_offers_for_user(user_id: int) -> int:
    async with AsyncSessionLocal() as db:
        return int(await db.scalar(select(func.count(Offer.id)).where(Offer.user_id == int(user_id))) or 0)


async def set_offer_home_server_for_negative_guard(offer_id: int, home_server: str) -> None:
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, int(offer_id))
        if offer is None:
            raise TradingProbeError(f"negative guard offer {offer_id} disappeared before home_server update")
        offer.home_server = normalize_server(home_server, current_server())
        await db.commit()


async def load_offer_public_id_for_negative_guard(offer_id: int) -> str:
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, int(offer_id))
        if offer is None:
            raise TradingProbeError(f"negative guard offer {offer_id} disappeared before public id read")
        public_id = str(getattr(offer, "offer_public_id", "") or "").strip()
        if not public_id:
            raise TradingProbeError(f"negative guard offer {offer_id} has no offer_public_id")
        return public_id


async def mark_offer_completed_for_negative_guard(
    *,
    offer_id: int,
    responder_user_id: int,
    actor_user_id: int,
) -> int:
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, int(offer_id), with_for_update=True)
        if offer is None:
            raise TradingProbeError(f"negative guard offer {offer_id} disappeared before completed fixture")
        responder = await db.get(User, int(responder_user_id))
        if responder is None:
            raise TradingProbeError(f"negative guard responder {responder_user_id} disappeared")
        trade_number = int(await db.scalar(select(func.max(Trade.trade_number))) or 9999) + 1
        trade = Trade(
            trade_number=trade_number,
            offer_id=int(offer.id),
            offer_user_id=int(offer.user_id),
            offer_user_mobile=getattr(getattr(offer, "user", None), "mobile_number", None),
            responder_user_id=int(responder.id),
            responder_user_mobile=getattr(responder, "mobile_number", None),
            actor_user_id=int(actor_user_id),
            commodity_id=int(offer.commodity_id),
            trade_type=TradeType.BUY if offer.offer_type == OfferType.SELL else TradeType.SELL,
            quantity=int(offer.remaining_quantity or offer.quantity),
            price=int(offer.price),
            status=TradeStatus.COMPLETED,
            idempotency_key=f"{getattr(offer, 'offer_public_id', offer.id)}:ng-complete",
        )
        now = datetime.utcnow()
        trade.completed_at = now
        trade.confirmed_at = now
        offer.remaining_quantity = 0
        offer.lot_sizes = None
        offer.status = OfferStatus.COMPLETED
        db.add(trade)
        await db.commit()
        await db.refresh(trade)
        return int(trade.id)


class NegativeGuardRawRequest:
    def __init__(self, *, body: bytes, headers: Mapping[str, str]) -> None:
        self._body = body
        self.headers = dict(headers)

    async def body(self) -> bytes:
        return self._body


async def execute_internal_trade_for_negative_guard(
    *,
    offer_id: int,
    offer_public_id: str,
    responder_user_id: int,
    source_server: str,
    headers: Mapping[str, str],
    body: bytes = b"{}",
    patch_signature_result: bool | None = None,
    phase_details: dict[str, Any] | None = None,
) -> str:
    internal_data = trades_router.InternalTradeExecuteRequest(
        offer_id=int(offer_id),
        offer_public_id=offer_public_id,
        quantity=5,
        responder_user_id=int(responder_user_id),
        edge_received_at=datetime.utcnow(),
        source_surface="webapp",
        source_server=source_server,
        idempotency_key=f"{offer_public_id}:negative-internal",
    )
    started = time.perf_counter()

    async def _call() -> Any:
        async with AsyncSessionLocal() as db:
            return await trades_router.execute_trade_internal(
                internal_data=internal_data,
                background_tasks=BackgroundTasks(),
                raw_request=NegativeGuardRawRequest(body=body, headers=headers),
                db=db,
            )

    try:
        if patch_signature_result is None:
            result = await _call()
        else:
            previous_verify = trades_router.verify_internal_signature
            trades_router.verify_internal_signature = lambda *_args, **_kwargs: bool(patch_signature_result)
            try:
                result = await _call()
            finally:
                trades_router.verify_internal_signature = previous_verify
    except HTTPException as exc:
        status_code = int(exc.status_code or 500)
        if phase_details is not None:
            phase_details["business_latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
            phase_details["internal_execute_ms"] = phase_details["business_latency_ms"]
            phase_details["exception"] = f"HTTPException {status_code}"
            phase_details["http_status_code"] = status_code
            phase_details["http_detail"] = str(exc.detail)
        return "rejected" if status_code < 500 else "error"

    if phase_details is not None:
        phase_details["business_latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        phase_details["internal_execute_ms"] = phase_details["business_latency_ms"]
        phase_details["result_type"] = type(result).__name__
    return "success"


async def execute_offer_creation_for_user(
    *,
    user_id: int,
    commodity_id: int,
    prefix: str,
    index: int,
    offer_type: str = "sell",
    quantity: int = 5,
    price: int = 100000,
    is_wholesale: bool = True,
    lot_sizes: list[int] | tuple[int, ...] | None = None,
    phase_details: dict[str, Any] | None = None,
) -> str:
    create_started = time.perf_counter()
    try:
        await create_offer_for_user(
            user_id=user_id,
            commodity_id=commodity_id,
            prefix=prefix,
            index=index,
            offer_type=offer_type,
            quantity=quantity,
            price=price,
            is_wholesale=is_wholesale,
            lot_sizes=lot_sizes,
        )
    except HTTPException as exc:
        status_code = int(exc.status_code or 500)
        if phase_details is not None:
            phase_details["business_latency_ms"] = round((time.perf_counter() - create_started) * 1000.0, 3)
            phase_details["create_offer_ms"] = phase_details["business_latency_ms"]
            phase_details["exception"] = f"HTTPException {status_code}"
            phase_details["http_status_code"] = status_code
            phase_details["http_detail"] = str(exc.detail)
        return "rejected" if status_code < 500 else "error"
    except Exception as exc:
        if phase_details is not None:
            phase_details["business_latency_ms"] = round((time.perf_counter() - create_started) * 1000.0, 3)
            phase_details["create_offer_ms"] = phase_details["business_latency_ms"]
            phase_details["exception"] = type(exc).__name__
        return "error"
    if phase_details is not None:
        phase_details["business_latency_ms"] = round((time.perf_counter() - create_started) * 1000.0, 3)
        phase_details["create_offer_ms"] = phase_details["business_latency_ms"]
    return "success"


async def run_offer_expiry_cycle_for_server(server: str) -> int:
    with override_current_server(server):
        return int(await offer_expiry_worker.expire_stale_offers())


async def expire_time_limit_offer_for_race(*, offer_id: int, stale_epoch: float) -> int:
    from core.services.offer_expiry_service import (
        OfferAlreadyInactiveError,
        OfferExpiryCommand,
        OfferExpiryReason,
        OfferExpirySourceSurface,
        OfferNotAuthoritativeError,
        expire_offer_authoritatively,
    )

    stale_at = datetime.fromtimestamp(float(stale_epoch), tz=timezone.utc).replace(tzinfo=None)
    for attempt in range(2):
        async with AsyncSessionLocal() as db:
            offer = await db.get(Offer, offer_id)
            if offer is None:
                raise TradingProbeError(f"time-expiry race offer {offer_id} disappeared")
            if getattr(offer, "status", None) != OfferStatus.ACTIVE:
                return 0
            created_at = getattr(offer, "created_at", None)
            if created_at is None:
                return 0
            normalized_created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None) if created_at.tzinfo else created_at
            if normalized_created_at > stale_at:
                return 0
            try:
                await expire_offer_authoritatively(
                    db,
                    offer,
                    OfferExpiryCommand(
                        reason=OfferExpiryReason.TIME_LIMIT,
                        source_surface=OfferExpirySourceSurface.SYSTEM,
                        source_server=current_server(),
                        expired_by_user_id=None,
                        expired_by_actor_user_id=None,
                    ),
                    now=datetime.utcnow(),
                )
                return 1
            except StaleDataError:
                await db.rollback()
                if attempt == 0:
                    continue
                return 0
            except (OfferAlreadyInactiveError, OfferNotAuthoritativeError):
                await db.rollback()
                return 0
    return 0


async def execute_trade_for_user(
    *,
    user_id: int,
    offer_id: int,
    quantity: int,
    idempotency_key: str,
) -> Any:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        return await trades_router._execute_trade_authoritatively_with_transient_retry(
            trade_data=trades_router.TradeCreate(
                offer_id=offer_id,
                quantity=quantity,
                idempotency_key=idempotency_key,
            ),
            background_tasks=BackgroundTasks(),
            db=db,
            context=owner_context(user),
            edge_received_at=datetime.utcnow(),
        )


class FakeState:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.state: Any = None

    async def get_state(self) -> Any:
        return self.state

    async def update_data(self, **kwargs: Any) -> None:
        self.data.update(kwargs)

    async def set_state(self, state: Any) -> None:
        self.state = state

    async def clear(self) -> None:
        self.data.clear()
        self.state = None


class FakeMessage:
    def __init__(self, text_value: str) -> None:
        self.text = text_value
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text_value: str, **kwargs: Any) -> None:
        self.answers.append({"text": text_value, "kwargs": kwargs})


class RecordingAiogramBot(Bot):
    def __init__(self, recorder: "RecordingTelegramBot") -> None:
        self._recorder = recorder
        super().__init__(token="123456:BENCHMARK_TEST_TOKEN")

    async def __call__(self, method: Any, request_timeout: int | None = None) -> Any:
        return await self._recorder.handle_telegram_method(method)


class RecordingTelegramBot:
    def __init__(self) -> None:
        self.bot = RecordingAiogramBot(self)
        self.sent_messages: list[dict[str, Any]] = []
        self.edited_texts: list[dict[str, Any]] = []
        self.edited_markups: list[dict[str, Any]] = []
        self.callback_answers: dict[str, dict[str, Any]] = {}
        self._message_id = 500000
        self._patch_bot_methods()

    def _next_message_id(self) -> int:
        self._message_id += 1
        return self._message_id

    async def _record_send_message(self, **kwargs: Any) -> SimpleNamespace:
        message_id = self._next_message_id()
        self.sent_messages.append({"message_id": message_id, **kwargs})
        return SimpleNamespace(message_id=message_id)

    async def _record_edit_message_text(self, **kwargs: Any) -> bool:
        self.edited_texts.append(dict(kwargs))
        return True

    async def _record_edit_message_reply_markup(self, **kwargs: Any) -> bool:
        self.edited_markups.append(dict(kwargs))
        return True

    async def _record_answer_callback_query(self, **kwargs: Any) -> bool:
        callback_query_id = str(kwargs.get("callback_query_id") or "")
        self.callback_answers[callback_query_id] = dict(kwargs)
        return True

    async def handle_telegram_method(self, method: Any) -> Any:
        method_name = type(method).__name__
        if method_name == "AnswerCallbackQuery":
            return await self._record_answer_callback_query(
                callback_query_id=getattr(method, "callback_query_id", ""),
                text=getattr(method, "text", None),
                show_alert=getattr(method, "show_alert", None),
                url=getattr(method, "url", None),
                cache_time=getattr(method, "cache_time", None),
            )
        if method_name == "SendMessage":
            return await self._record_send_message(
                chat_id=getattr(method, "chat_id", None),
                text=getattr(method, "text", None),
                reply_markup=getattr(method, "reply_markup", None),
                parse_mode=getattr(method, "parse_mode", None),
            )
        if method_name == "EditMessageText":
            return await self._record_edit_message_text(
                chat_id=getattr(method, "chat_id", None),
                message_id=getattr(method, "message_id", None),
                text=getattr(method, "text", None),
                reply_markup=getattr(method, "reply_markup", None),
                parse_mode=getattr(method, "parse_mode", None),
            )
        if method_name == "EditMessageReplyMarkup":
            return await self._record_edit_message_reply_markup(
                chat_id=getattr(method, "chat_id", None),
                message_id=getattr(method, "message_id", None),
                reply_markup=getattr(method, "reply_markup", None),
            )
        return True

    def _patch_bot_methods(self) -> None:
        async def send_message(*_args: Any, **kwargs: Any) -> SimpleNamespace:
            return await self._record_send_message(**kwargs)

        async def edit_message_text(*_args: Any, **kwargs: Any) -> bool:
            return await self._record_edit_message_text(**kwargs)

        async def edit_message_reply_markup(*_args: Any, **kwargs: Any) -> bool:
            return await self._record_edit_message_reply_markup(**kwargs)

        async def answer_callback_query(*_args: Any, **kwargs: Any) -> bool:
            return await self._record_answer_callback_query(**kwargs)

        self.bot.send_message = send_message  # type: ignore[method-assign]
        self.bot.edit_message_text = edit_message_text  # type: ignore[method-assign]
        self.bot.edit_message_reply_markup = edit_message_reply_markup  # type: ignore[method-assign]
        self.bot.answer_callback_query = answer_callback_query  # type: ignore[method-assign]

    async def close(self) -> None:
        await self.bot.session.close()


PROBE_DISPATCHER_ROUTERS = (
    bot_trade_create.router,
    bot_trade_execute.router,
    bot_trade_manage.router,
)


def detach_probe_router(router: Any, *, expected_parent: Any | None = None) -> None:
    parent = getattr(router, "_parent_router", None)
    if parent is None:
        return
    if expected_parent is not None and parent is not expected_parent:
        return
    sub_routers = getattr(parent, "sub_routers", None)
    if isinstance(sub_routers, list):
        try:
            sub_routers.remove(router)
        except ValueError:
            pass
    router._parent_router = None


def include_probe_router(dispatcher: Dispatcher, router: Any) -> None:
    detach_probe_router(router)
    dispatcher.include_router(router)


class AiogramDispatcherHarness:
    def __init__(self) -> None:
        self.telegram = RecordingTelegramBot()
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.update.outer_middleware(TradeContentionGateMiddleware())
        self.dp.update.outer_middleware(AuthMiddleware(AsyncSessionLocal))
        self.dp.update.outer_middleware(BotLoggingContextMiddleware())
        for router in PROBE_DISPATCHER_ROUTERS:
            include_probe_router(self.dp, router)
        self._update_id = 700000
        self._message_id = 800000

    def _next_update_id(self) -> int:
        self._update_id += 1
        return self._update_id

    def _next_message_id(self) -> int:
        self._message_id += 1
        return self._message_id

    def _telegram_user_payload(self, telegram_id: int) -> dict[str, Any]:
        return {
            "id": telegram_id,
            "is_bot": False,
            "first_name": f"Bench {telegram_id}",
        }

    async def feed_private_text(self, *, telegram_id: int, text_value: str) -> None:
        now = int(time.time())
        update = Update.model_validate(
            {
                "update_id": self._next_update_id(),
                "message": {
                    "message_id": self._next_message_id(),
                    "date": now,
                    "chat": {
                        "id": telegram_id,
                        "type": "private",
                        "first_name": f"Bench {telegram_id}",
                    },
                    "from": self._telegram_user_payload(telegram_id),
                    "text": text_value,
                },
            },
            context={"bot": self.telegram.bot},
        )
        await self.dp.feed_update(self.telegram.bot, update)

    async def feed_channel_callback(
        self,
        *,
        telegram_id: int,
        callback_data: str,
        callback_id: str,
        channel_message_id: int,
    ) -> dict[str, Any] | None:
        now = int(time.time())
        channel_id = settings.channel_id or -1000000000000
        update = Update.model_validate(
            {
                "update_id": self._next_update_id(),
                "callback_query": {
                    "id": callback_id,
                    "from": self._telegram_user_payload(telegram_id),
                    "chat_instance": f"bench-chat-{channel_id}",
                    "message": {
                        "message_id": channel_message_id,
                        "date": now,
                        "chat": {
                            "id": channel_id,
                            "type": "channel",
                            "title": "Benchmark Channel",
                        },
                        "text": "benchmark offer",
                    },
                    "data": callback_data,
                },
            },
            context={"bot": self.telegram.bot},
        )
        await self.dp.feed_update(self.telegram.bot, update)
        return self.telegram.callback_answers.get(callback_id)

    async def feed_private_callback(
        self,
        *,
        telegram_id: int,
        callback_data: str,
        callback_id: str,
    ) -> dict[str, Any] | None:
        now = int(time.time())
        update = Update.model_validate(
            {
                "update_id": self._next_update_id(),
                "callback_query": {
                    "id": callback_id,
                    "from": self._telegram_user_payload(telegram_id),
                    "chat_instance": f"bench-private-{telegram_id}",
                    "message": {
                        "message_id": self._next_message_id(),
                        "date": now,
                        "chat": {
                            "id": telegram_id,
                            "type": "private",
                            "first_name": f"Bench {telegram_id}",
                        },
                        "text": "benchmark preview",
                    },
                    "data": callback_data,
                },
            },
            context={"bot": self.telegram.bot},
        )
        await self.dp.feed_update(self.telegram.bot, update)
        return self.telegram.callback_answers.get(callback_id)

    async def close(self) -> None:
        for router in PROBE_DISPATCHER_ROUTERS:
            detach_probe_router(router, expected_parent=self.dp)
        storage_close = getattr(getattr(self.dp, "storage", None), "close", None)
        if callable(storage_close):
            maybe_coro = storage_close()
            if asyncio.iscoroutine(maybe_coro):
                await maybe_coro
        await self.telegram.close()


async def run_bot_text_handler_probe(*, user_id: int, text_value: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        state = FakeState()
        message = FakeMessage(text_value)
        await bot_trade_create.handle_text_offer(message, state, user, bot=None)
        return {
            "answers": len(message.answers),
            "state_set": state.state is not None,
            "parsed_trade_type": state.data.get("trade_type"),
            "parsed_commodity_id": state.data.get("commodity_id"),
        }


def offer_text_for_probe(
    *,
    commodity_name: str,
    prefix: str,
    user_id: int,
    offer_type: str,
    quantity: int,
    price: int,
    is_wholesale: bool = True,
    lot_sizes: list[int] | tuple[int, ...] | None = None,
) -> str:
    verb = "خ" if offer_type == "buy" else "ف"
    marker = f"{prefix} bot policy {user_id}"
    lots_text = ""
    if not is_wholesale and lot_sizes:
        lots_text = " " + " ".join(str(item) for item in lot_sizes)
    return f"{verb} {commodity_name} {quantity} عدد {price}{lots_text}: {marker}"


async def execute_bot_offer_creation_for_user(
    *,
    user: LoadUserRef,
    commodity_name: str,
    prefix: str,
    offer_type: str,
    quantity: int,
    price: int,
    is_wholesale: bool = True,
    lot_sizes: list[int] | tuple[int, ...] | None = None,
    phase_details: dict[str, Any] | None = None,
) -> str:
    started = time.perf_counter()
    before_offer_count = await count_offers_for_user(user.user_id)
    text_value = offer_text_for_probe(
        commodity_name=commodity_name,
        prefix=prefix,
        user_id=user.user_id,
        offer_type=offer_type,
        quantity=quantity,
        price=price,
        is_wholesale=is_wholesale,
        lot_sizes=lot_sizes,
    )
    harness = AiogramDispatcherHarness()
    callback_answer: dict[str, Any] | None = None
    sent_messages: list[dict[str, Any]] = []
    try:
        await harness.feed_private_text(telegram_id=user.telegram_id, text_value=text_value)
        callback_answer = await harness.feed_private_callback(
            telegram_id=user.telegram_id,
            callback_data=TextOfferActionCallback(action="confirm").pack(),
            callback_id=f"{prefix}bot-create-confirm-{user.user_id}",
        )
        sent_messages = list(harness.telegram.sent_messages)
    except Exception as exc:
        if phase_details is not None:
            phase_details["exception"] = type(exc).__name__
            phase_details["business_latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        return "error"
    finally:
        await harness.close()
    after_offer_count = await count_offers_for_user(user.user_id)
    created_offer_delta = after_offer_count - before_offer_count
    if phase_details is not None:
        phase_details.update(
            {
                "business_latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "before_offer_count": before_offer_count,
                "after_offer_count": after_offer_count,
                "created_offer_delta": created_offer_delta,
                "sent_message_count": len(sent_messages),
                "sent_message_texts": [str(item.get("text") or "") for item in sent_messages[-3:]],
                "confirm_callback_answer_text": str((callback_answer or {}).get("text") or ""),
            }
        )
    return "success" if created_offer_delta > 0 else "rejected"


async def execute_webapp_trade_for_user(
    *,
    user_id: int,
    offer_id: int,
    offer_public_id: str | None = None,
    quantity: int,
    idempotency_key: str,
    contention_lease: TradeContentionLease | None = None,
    error_details: list[str] | None = None,
    phase_details: dict[str, Any] | None = None,
) -> str:
    background_tasks = BackgroundTasks()
    create_started = time.perf_counter()
    lease = contention_lease or await try_acquire_trade_contention_gate(
        offer_public_id=offer_public_id,
        offer_id=offer_id,
    )
    if not lease.acquired:
        if phase_details is not None:
            business_latency_ms = round((time.perf_counter() - create_started) * 1000.0, 3)
            phase_details["business_latency_ms"] = business_latency_ms
            phase_details["contention_gate_ms"] = business_latency_ms
            phase_details["exception"] = "HTTPException 409"
            phase_details["http_status_code"] = 409
            phase_details["http_detail"] = "contention_gate_rejected"
        return "rejected"
    try:
        async with AsyncSessionLocal() as db:
            user = await load_user(db, user_id)
            response = await trades_router.create_trade(
                trade_data=trades_router.TradeCreate(
                    offer_id=offer_id,
                    offer_public_id=offer_public_id,
                    quantity=quantity,
                    idempotency_key=idempotency_key,
                ),
                background_tasks=background_tasks,
                raw_request=SimpleNamespace(body=lambda: b""),
                trade_contention_lease=lease,
                db=db,
                context=owner_context(user),
            )
        if phase_details is not None:
            business_latency_ms = round((time.perf_counter() - create_started) * 1000.0, 3)
            phase_details["business_latency_ms"] = business_latency_ms
            phase_details["create_trade_ms"] = business_latency_ms
        background_started = time.perf_counter()
        await background_tasks()
        if phase_details is not None:
            phase_details["background_tasks_ms"] = round((time.perf_counter() - background_started) * 1000.0, 3)
    except HTTPException as exc:
        status_code = int(exc.status_code or 500)
        if status_code >= 500 and error_details is not None:
            error_details.append(f"HTTPException {status_code}: {exc.detail}")
        if phase_details is not None:
            business_latency_ms = round((time.perf_counter() - create_started) * 1000.0, 3)
            phase_details["business_latency_ms"] = business_latency_ms
            phase_details["create_trade_ms"] = business_latency_ms
            phase_details["exception"] = f"HTTPException {status_code}"
            phase_details["http_status_code"] = status_code
            phase_details["http_detail"] = str(exc.detail)
        return "rejected" if status_code < 500 else "error"
    except Exception as exc:
        if error_details is not None:
            error_details.append(f"{type(exc).__name__}: {exc}")
        if phase_details is not None:
            business_latency_ms = round((time.perf_counter() - create_started) * 1000.0, 3)
            phase_details["business_latency_ms"] = business_latency_ms
            phase_details["create_trade_ms"] = business_latency_ms
            phase_details["exception"] = type(exc).__name__
        return "error"
    finally:
        await lease.release()

    if isinstance(response, JSONResponse):
        if phase_details is not None and response.status_code >= 400:
            try:
                body = json.loads(response.body.decode("utf-8") or "{}")
            except Exception:
                body = {"detail": "invalid JSONResponse body"}
            phase_details["json_response_status_code"] = int(response.status_code)
            phase_details["json_response_detail"] = str(body.get("detail") or body)
        if response.status_code >= 500 and error_details is not None:
            try:
                body = json.loads(response.body.decode("utf-8") or "{}")
            except Exception:
                body = {"detail": "invalid JSONResponse body"}
            error_details.append(f"JSONResponse {response.status_code}: {body.get('detail') or body}")
        return "success" if response.status_code < 400 else ("rejected" if response.status_code < 500 else "error")
    return "success"


async def execute_accountant_context_trade_for_user(
    *,
    owner_user_id: int,
    actor_user_id: int,
    offer_id: int,
    offer_public_id: str | None = None,
    quantity: int,
    idempotency_key: str,
    error_details: list[str] | None = None,
    phase_details: dict[str, Any] | None = None,
) -> str:
    background_tasks = BackgroundTasks()
    create_started = time.perf_counter()
    try:
        async with AsyncSessionLocal() as db:
            owner = await load_user(db, owner_user_id)
            actor = await load_user(db, actor_user_id)
            response = await trades_router._execute_trade_authoritatively_with_transient_retry(
                trade_data=trades_router.TradeCreate(
                    offer_id=offer_id,
                    offer_public_id=offer_public_id,
                    quantity=quantity,
                    idempotency_key=idempotency_key,
                ),
                background_tasks=background_tasks,
                db=db,
                context=EffectiveOwnerActor(
                    owner_user=owner,
                    actor_user=actor,
                    relation=None,
                    is_accountant_context=True,
                ),
                edge_received_at=datetime.utcnow(),
            )
        if phase_details is not None:
            business_latency_ms = round((time.perf_counter() - create_started) * 1000.0, 3)
            phase_details["business_latency_ms"] = business_latency_ms
            phase_details["execute_trade_ms"] = business_latency_ms
        background_started = time.perf_counter()
        await background_tasks()
        if phase_details is not None:
            phase_details["background_tasks_ms"] = round((time.perf_counter() - background_started) * 1000.0, 3)
    except HTTPException as exc:
        status_code = int(exc.status_code or 500)
        if status_code >= 500 and error_details is not None:
            error_details.append(f"HTTPException {status_code}: {exc.detail}")
        if phase_details is not None:
            business_latency_ms = round((time.perf_counter() - create_started) * 1000.0, 3)
            phase_details["business_latency_ms"] = business_latency_ms
            phase_details["execute_trade_ms"] = business_latency_ms
            phase_details["exception"] = f"HTTPException {status_code}"
            phase_details["http_status_code"] = status_code
            phase_details["http_detail"] = str(exc.detail)
        return "rejected" if status_code < 500 else "error"
    except Exception as exc:
        if error_details is not None:
            error_details.append(f"{type(exc).__name__}: {exc}")
        if phase_details is not None:
            business_latency_ms = round((time.perf_counter() - create_started) * 1000.0, 3)
            phase_details["business_latency_ms"] = business_latency_ms
            phase_details["execute_trade_ms"] = business_latency_ms
            phase_details["exception"] = type(exc).__name__
        return "error"

    if isinstance(response, JSONResponse):
        if phase_details is not None and response.status_code >= 400:
            try:
                body = json.loads(response.body.decode("utf-8") or "{}")
            except Exception:
                body = {"detail": "invalid JSONResponse body"}
            phase_details["json_response_status_code"] = int(response.status_code)
            phase_details["json_response_detail"] = str(body.get("detail") or body)
        if response.status_code >= 500 and error_details is not None:
            try:
                body = json.loads(response.body.decode("utf-8") or "{}")
            except Exception:
                body = {"detail": "invalid JSONResponse body"}
            error_details.append(f"JSONResponse {response.status_code}: {body.get('detail') or body}")
        return "success" if response.status_code < 400 else ("rejected" if response.status_code < 500 else "error")
    return "success"


async def negative_guard_offer_evidence(offer_id: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, offer_id)
        if offer is None:
            raise TradingProbeError(f"negative guard offer {offer_id} disappeared")
        trade_count = int(await db.scalar(select(func.count(Trade.id)).where(Trade.offer_id == offer_id)) or 0)
        request_rows = list(
            (
                await db.execute(
                    select(OfferRequest)
                    .where(OfferRequest.local_offer_id == offer_id)
                    .order_by(OfferRequest.id.asc())
                )
            )
            .scalars()
            .all()
        )
    request_payloads: list[dict[str, Any]] = []
    for row in request_rows:
        request_payloads.append(
            {
                "id": int(row.id),
                "requester_user_id": int(row.requester_user_id) if row.requester_user_id is not None else None,
                "actor_user_id": int(row.actor_user_id) if row.actor_user_id is not None else None,
                "requested_quantity": int(row.requested_quantity),
                "result_status": getattr(getattr(row, "result_status", None), "value", row.result_status),
                "public_failure_code": row.public_failure_code,
                "internal_failure_code": row.internal_failure_code,
                "resulting_trade_id": int(row.resulting_trade_id) if row.resulting_trade_id is not None else None,
            }
        )
    status_counts: dict[str, int] = {}
    code_counts: dict[str, int] = {}
    for payload in request_payloads:
        status_value = str(payload.get("result_status") or "unknown")
        code_value = str(payload.get("public_failure_code") or "none")
        status_counts[status_value] = status_counts.get(status_value, 0) + 1
        code_counts[code_value] = code_counts.get(code_value, 0) + 1
    return {
        "offer": {
            "id": int(offer.id),
            "status": getattr(getattr(offer, "status", None), "value", offer.status),
            "quantity": int(offer.quantity),
            "remaining_quantity": int(offer.remaining_quantity) if offer.remaining_quantity is not None else None,
        },
        "trade_count": trade_count,
        "offer_request_count": len(request_payloads),
        "offer_request_status_counts": dict(sorted(status_counts.items())),
        "offer_request_public_failure_code_counts": dict(sorted(code_counts.items())),
        "offer_requests": request_payloads,
    }


def assert_negative_guard_evidence(
    *,
    case_id: str,
    status_sequence: list[str],
    evidence: Mapping[str, Any],
) -> list[str]:
    pre_ledger_cases = {
        "market_closed",
        "inactive_requester",
        "trading_restricted_user",
        "watch_role_market_action",
        "accountant_market_action",
        "tier2_offer_creation",
        "tier2_telegram_request",
        "daily_trade_limit_exceeded",
        "daily_request_limit_exceeded",
        "active_commodity_limit_exceeded",
        "remote_authority_unavailable",
        "bad_internal_signature",
        "wrong_authoritative_server",
        "missing_public_offer_id",
        "cleanup_scope_violation",
    }
    expired_offer_cases = {
        "already_completed_offer",
        "manually_expired_offer",
        "time_expired_offer",
    }
    failures: list[str] = []
    if any(status == "error" for status in status_sequence):
        failures.append(f"{case_id} produced internal error status sequence {status_sequence}")
    if "rejected" not in status_sequence:
        failures.append(f"{case_id} did not produce a rejected attempt")

    status_counts = dict(evidence.get("offer_request_status_counts") or {})
    code_counts = dict(evidence.get("offer_request_public_failure_code_counts") or {})
    trade_count = int(evidence.get("trade_count") or 0)
    offer_request_count = int(evidence.get("offer_request_count") or 0)
    offer = dict(evidence.get("offer") or {})
    remaining_quantity = offer.get("remaining_quantity")

    expected_trade_count = 0
    expected_remaining_quantity = 5
    expected_offer_request_count: int | None = None
    required_status: str | None = OfferRequestStatus.REJECTED_BUSINESS_RULE.value
    required_code: str | None = None
    if case_id in pre_ledger_cases:
        required_status = None
        required_code = None
        expected_offer_request_count = 0
    elif case_id == "own_offer_request":
        required_code = "own_offer"
    elif case_id == "invalid_request_amount":
        required_code = "invalid_quantity"
    elif case_id == "inactive_offer_owner":
        required_code = "offer_owner_inactive"
    elif case_id == "retail_lot_unavailable":
        required_status = OfferRequestStatus.REJECTED_LOT_UNAVAILABLE.value
        required_code = "lot_unavailable"
        expected_remaining_quantity = 10
    elif case_id in expired_offer_cases:
        if case_id == "already_completed_offer":
            expected_trade_count = 1
            expected_remaining_quantity = 0
        required_status = OfferRequestStatus.REJECTED_OFFER_EXPIRED.value
        required_code = "offer_not_active"
    elif case_id == "stale_telegram_button":
        expected_trade_count = 1
        expected_remaining_quantity = 0
        expected_offer_request_count = 1
        required_status = None
        required_code = None
    else:
        failures.append(f"unsupported negative guard case assertion: {case_id}")

    if trade_count != expected_trade_count:
        failures.append(f"{case_id} expected trade_count={expected_trade_count}, got {trade_count}")
    if remaining_quantity != expected_remaining_quantity:
        failures.append(
            f"{case_id} expected remaining_quantity={expected_remaining_quantity}, got {remaining_quantity}"
        )
    if expected_offer_request_count is not None and offer_request_count != expected_offer_request_count:
        failures.append(
            f"{case_id} expected offer_request_count={expected_offer_request_count}, got {offer_request_count}"
        )
    if required_status and int(status_counts.get(required_status, 0) or 0) < 1:
        failures.append(f"{case_id} missing offer_request status {required_status}: {status_counts}")
    if required_code and int(code_counts.get(required_code, 0) or 0) < 1:
        failures.append(f"{case_id} missing offer_request public_failure_code {required_code}: {code_counts}")
    completed_requests = int(status_counts.get(OfferRequestStatus.COMPLETED_TRADE.value, 0) or 0)
    if case_id not in {"already_completed_offer", "stale_telegram_button"} and completed_requests:
        failures.append(f"{case_id} unexpectedly has completed offer_request rows: {status_counts}")
    if case_id in {"tier2_offer_creation", "daily_request_limit_exceeded", "active_commodity_limit_exceeded"}:
        creation = dict(evidence.get("offer_creation") or {})
        created_offer_delta = int(creation.get("created_offer_delta") or 0)
        if created_offer_delta != 0:
            failures.append(f"{case_id} expected created_offer_delta=0, got {created_offer_delta}")
    if case_id == "tier2_telegram_request":
        callback = dict(evidence.get("bot_callback") or {})
        if not str(callback.get("second_answer_text") or ""):
            failures.append(f"{case_id} expected a bot callback denial answer")
    if case_id == "remote_authority_unavailable":
        remote_forward = dict(evidence.get("remote_forward") or {})
        status_code = int(remote_forward.get("status_code") or 0)
        if status_code < 500:
            failures.append(f"{case_id} expected remote forward status >=500, got {status_code}")
    expected_internal_status = {
        "bad_internal_signature": 401,
        "wrong_authoritative_server": 409,
        "missing_public_offer_id": 404,
    }.get(case_id)
    if expected_internal_status is not None:
        internal_execute = dict(evidence.get("internal_execute") or {})
        status_code = int(internal_execute.get("status_code") or 0)
        if status_code != expected_internal_status:
            failures.append(
                f"{case_id} expected internal execute status {expected_internal_status}, got {status_code}"
            )
    if case_id == "stale_telegram_button":
        callback = dict(evidence.get("bot_callback") or {})
        if not str(callback.get("second_answer_text") or ""):
            failures.append(f"{case_id} expected stale bot callback denial answer")
    if case_id == "cleanup_scope_violation":
        guard = dict(evidence.get("cleanup_scope_guard") or {})
        rejected_values = guard.get("rejected_values") or []
        accepted_values = guard.get("accepted_values") or []
        if not rejected_values:
            failures.append(f"{case_id} expected cleanup scope guard rejections")
        if accepted_values:
            failures.append(f"{case_id} unexpectedly accepted cleanup prefixes: {accepted_values}")
    return failures


def customer_tier_for_actor_kind(kind: str) -> CustomerTier | None:
    if kind == "tier1":
        return CustomerTier.TIER_1
    if kind == "tier2":
        return CustomerTier.TIER_2
    return None


def offer_source_surface_for_manifest_surface(surface: str) -> OfferSourceSurface:
    if surface == "webapp":
        return OfferSourceSurface.WEBAPP
    if surface == "telegram":
        return OfferSourceSurface.TELEGRAM_BOT
    raise TradingProbeError(f"unsupported manifest offer surface: {surface!r}")


async def prepare_unsupported_policy_actor_fixture(
    *,
    prefix: str,
    source_kind: str,
    responder_kind: str,
    group_relation: str,
) -> dict[str, Any]:
    users = await create_load_fixture_users(prefix, user_count=5)
    source_owner = users[0]
    responder_owner = source_owner if group_relation == "same_owner" else users[1]
    source_actor = users[2]
    responder_actor = users[3]
    control_owner = users[4]
    relation_ids: list[int] = []

    source_tier = customer_tier_for_actor_kind(source_kind)
    if source_tier is not None:
        relation_ids.append(
            await create_active_customer_relation_for_negative_guard(
                owner_user_id=source_owner.user_id,
                customer_user_id=source_actor.user_id,
                prefix=prefix,
                tier=source_tier,
            )
        )
    responder_tier = customer_tier_for_actor_kind(responder_kind)
    if responder_tier is not None:
        relation_ids.append(
            await create_active_customer_relation_for_negative_guard(
                owner_user_id=responder_owner.user_id,
                customer_user_id=responder_actor.user_id,
                prefix=prefix,
                tier=responder_tier,
            )
        )

    return {
        "users": users,
        "source_owner": source_owner,
        "responder_owner": responder_owner,
        "source_actor": source_actor,
        "responder_actor": responder_actor,
        "control_owner": control_owner,
        "relation_ids": relation_ids,
    }


async def execute_unsupported_tier2_offer_creation_probe(
    *,
    prefix: str,
    source_actor: LoadUserRef,
    offer_surface: str,
    commodity_id: int,
    commodity_name: str,
    offer_type: str,
    quantity: int,
    price: int,
    is_wholesale: bool,
    lot_sizes: list[int] | tuple[int, ...] | None,
) -> dict[str, Any]:
    before_offer_count = await count_offers_for_user(source_actor.user_id)
    details: dict[str, Any] = {
        "reason": "tier2_cannot_create_offer",
        "offer_surface": offer_surface,
        "before_offer_count": before_offer_count,
    }
    if offer_surface == "telegram":
        status_value = await execute_bot_offer_creation_for_user(
            user=source_actor,
            commodity_name=commodity_name,
            prefix=prefix,
            offer_type=offer_type,
            quantity=quantity,
            price=price,
            is_wholesale=is_wholesale,
            lot_sizes=lot_sizes,
            phase_details=details,
        )
    else:
        status_value = await execute_offer_creation_for_user(
            user_id=source_actor.user_id,
            commodity_id=commodity_id,
            prefix=prefix,
            index=1,
            offer_type=offer_type,
            quantity=quantity,
            price=price,
            is_wholesale=is_wholesale,
            lot_sizes=lot_sizes,
            phase_details=details,
        )
    after_offer_count = await count_offers_for_user(source_actor.user_id)
    details["after_offer_count"] = after_offer_count
    details["created_offer_delta"] = after_offer_count - before_offer_count
    details["status"] = status_value
    return details


async def execute_unsupported_tier2_telegram_request_probe(
    *,
    prefix: str,
    source_actor: LoadUserRef,
    responder_actor: LoadUserRef,
    control_owner: LoadUserRef,
    source_kind: str,
    offer_surface: str,
    commodity_id: int,
    offer_type: str,
    quantity: int,
    request_amount: int,
    price: int,
    is_wholesale: bool,
    lot_sizes: list[int] | tuple[int, ...] | None,
) -> dict[str, Any]:
    offer_owner = control_owner if source_kind == "tier2" else source_actor
    source_surface = offer_source_surface_for_manifest_surface(offer_surface)
    offer_id = await create_offer_for_user(
        user_id=offer_owner.user_id,
        commodity_id=commodity_id,
        prefix=prefix,
        index=2,
        offer_type=offer_type,
        quantity=quantity,
        price=price,
        is_wholesale=is_wholesale,
        lot_sizes=lot_sizes,
        source_surface=source_surface,
    )
    details: dict[str, Any] = {
        "reason": "tier2_cannot_use_telegram_request",
        "offer_surface": offer_surface,
        "request_surface": "telegram",
        "offer_owner_user_id": offer_owner.user_id,
        "offer_owner_is_control": source_kind == "tier2",
        "offer_id": offer_id,
    }
    harness = AiogramDispatcherHarness()
    try:
        status_value = await execute_bot_trade_with_dispatcher(
            harness=harness,
            spec=MixedLoadAttemptSpec(
                index=0,
                surface="telegram",
                user_id=responder_actor.user_id,
                telegram_id=responder_actor.telegram_id,
            ),
            offer=await load_offer_snapshot(offer_id),
            amount=request_amount,
            prefix=f"{prefix}tier2-telegram-request-",
            phase_details=details,
        )
    finally:
        await harness.close()
    evidence = await negative_guard_offer_evidence(offer_id)
    details["status"] = status_value
    details["offer_evidence"] = evidence
    return details


def assert_unsupported_policy_evidence(
    *,
    expected_reasons: set[str],
    probe_results: list[Mapping[str, Any]],
) -> list[str]:
    failures: list[str] = []
    executed_reasons = {str(item.get("reason") or "") for item in probe_results}
    missing_reasons = expected_reasons - executed_reasons
    if missing_reasons:
        failures.append(f"unsupported policy probes did not execute reasons: {sorted(missing_reasons)}")
    for result in probe_results:
        reason = str(result.get("reason") or "")
        status_value = str(result.get("status") or "")
        if status_value != "rejected":
            failures.append(f"{reason} expected rejected status, got {status_value!r}")
        if reason == "tier2_cannot_create_offer":
            created_offer_delta = int(result.get("created_offer_delta") or 0)
            if created_offer_delta != 0:
                failures.append(f"{reason} expected created_offer_delta=0, got {created_offer_delta}")
        elif reason == "tier2_cannot_use_telegram_request":
            if not str(result.get("second_answer_text") or ""):
                failures.append(f"{reason} expected Telegram denial callback answer")
            evidence = dict(result.get("offer_evidence") or {})
            trade_count = int(evidence.get("trade_count") or 0)
            offer_request_count = int(evidence.get("offer_request_count") or 0)
            remaining_quantity = (dict(evidence.get("offer") or {})).get("remaining_quantity")
            if trade_count != 0:
                failures.append(f"{reason} expected trade_count=0, got {trade_count}")
            if offer_request_count != 0:
                failures.append(f"{reason} expected offer_request_count=0, got {offer_request_count}")
            expected_remaining_quantity = int(result.get("expected_remaining_quantity") or 0)
            if remaining_quantity != expected_remaining_quantity:
                failures.append(
                    f"{reason} expected remaining_quantity={expected_remaining_quantity}, got {remaining_quantity}"
                )
        else:
            failures.append(f"unsupported policy probe reason is not supported: {reason}")
    return failures


async def run_unsupported_policy_case(
    *,
    prefix: str,
    actor_pair_id: str,
    source_kind: str,
    responder_kind: str,
    group_relation: str,
    offer_surface: str,
    request_surface: str,
    offer_type: str,
    quantity: int,
    request_amount: int,
    price: int,
    is_wholesale: bool,
    lot_sizes: list[int] | tuple[int, ...] | None,
    unsupported_reasons: list[str] | tuple[str, ...],
    allow_production_execution: bool = False,
    skip_initial_cleanup: bool = False,
    allow_production_cleanup: bool = False,
) -> dict[str, Any]:
    normalized_reasons = {str(reason).strip() for reason in unsupported_reasons if str(reason).strip()}
    unsupported = normalized_reasons - UNSUPPORTED_POLICY_EXECUTABLE_REASONS
    if unsupported:
        raise TradingProbeError(f"unsupported policy reasons are not implemented: {sorted(unsupported)}")
    if not normalized_reasons:
        raise TradingProbeError("unsupported policy case requires at least one reason")
    assert_production_full_matrix_allowed(prefix, allow_flag=allow_production_execution)
    if not skip_initial_cleanup:
        if is_production_runtime():
            allow_production_cleanup_hard_delete(prefix, allow_flag=allow_production_cleanup)
        await cleanup_prefix(prefix)

    setup_event_listeners()
    commodity_id, commodity_name = await resolve_commodity()
    probe_results: list[dict[str, Any]] = []

    async with patched_trading_boundaries():
        fixture = await prepare_unsupported_policy_actor_fixture(
            prefix=prefix,
            source_kind=source_kind,
            responder_kind=responder_kind,
            group_relation=group_relation,
        )
        source_actor = fixture["source_actor"]
        responder_actor = fixture["responder_actor"]
        control_owner = fixture["control_owner"]

        if "tier2_cannot_create_offer" in normalized_reasons:
            probe_results.append(
                await execute_unsupported_tier2_offer_creation_probe(
                    prefix=f"{prefix}create-",
                    source_actor=source_actor,
                    offer_surface=offer_surface,
                    commodity_id=commodity_id,
                    commodity_name=commodity_name,
                    offer_type=offer_type,
                    quantity=quantity,
                    price=price,
                    is_wholesale=is_wholesale,
                    lot_sizes=lot_sizes,
                )
            )
        if "tier2_cannot_use_telegram_request" in normalized_reasons:
            request_probe = await execute_unsupported_tier2_telegram_request_probe(
                prefix=f"{prefix}request-",
                source_actor=source_actor,
                responder_actor=responder_actor,
                control_owner=control_owner,
                source_kind=source_kind,
                offer_surface=offer_surface,
                commodity_id=commodity_id,
                offer_type=offer_type,
                quantity=quantity,
                request_amount=request_amount,
                price=price,
                is_wholesale=is_wholesale,
                lot_sizes=lot_sizes,
            )
            request_probe["expected_remaining_quantity"] = quantity
            probe_results.append(request_probe)

    failures = assert_unsupported_policy_evidence(
        expected_reasons=normalized_reasons,
        probe_results=probe_results,
    )
    return {
        "schema_version": UNSUPPORTED_POLICY_RESULT_SCHEMA_VERSION,
        "status": "passed" if not failures else "failed",
        "server_mode": settings.server_mode,
        "prefix": prefix,
        "actor_pair_id": actor_pair_id,
        "source_kind": source_kind,
        "responder_kind": responder_kind,
        "group_relation": group_relation,
        "offer_surface": offer_surface,
        "request_surface": request_surface,
        "offer_type": offer_type,
        "quantity": quantity,
        "request_amount": request_amount,
        "is_wholesale": is_wholesale,
        "lot_sizes": list(lot_sizes or []),
        "unsupported_reasons": sorted(normalized_reasons),
        "probe_results": probe_results,
        "assertion_failures": failures,
    }


async def run_negative_guard_case(
    *,
    prefix: str,
    case_id: str,
    allow_production_execution: bool = False,
    skip_initial_cleanup: bool = False,
    allow_production_cleanup: bool = False,
) -> dict[str, Any]:
    normalized_case_id = str(case_id or "").strip()
    if normalized_case_id not in NEGATIVE_GUARD_EXECUTABLE_CASES:
        raise TradingProbeError(f"negative guard case is not implemented: {case_id}")
    assert_production_full_matrix_allowed(prefix, allow_flag=allow_production_execution)
    if not skip_initial_cleanup:
        if is_production_runtime():
            allow_production_cleanup_hard_delete(prefix, allow_flag=allow_production_cleanup)
        await cleanup_prefix(prefix)

    setup_event_listeners()
    commodity_id, _commodity_name = await resolve_commodity()
    statuses: list[str] = []
    phase_details: list[dict[str, Any]] = []
    extra_evidence: dict[str, Any] = {}

    async with patched_trading_boundaries():
        users = await create_load_fixture_users(prefix, user_count=4)
        owner = users[0]
        responder_a = users[1]
        responder_b = users[2]

        if normalized_case_id == "retail_lot_unavailable":
            offer_id = await create_offer_for_user(
                user_id=owner.user_id,
                commodity_id=commodity_id,
                prefix=prefix,
                index=1,
                offer_type="sell",
                quantity=10,
                price=100000,
                is_wholesale=False,
                lot_sizes=[5, 5],
            )
            details: dict[str, Any] = {}
            statuses.append(
                await execute_webapp_trade_for_user(
                    user_id=responder_a.user_id,
                    offer_id=offer_id,
                    quantity=3,
                    idempotency_key=f"{prefix}{normalized_case_id}-reject",
                    phase_details=details,
                )
            )
            phase_details.append(details)
        else:
            offer_id = await create_offer_for_user(
                user_id=owner.user_id,
                commodity_id=commodity_id,
                prefix=prefix,
                index=1,
                offer_type="sell",
                quantity=5,
                price=100000,
            )
            if normalized_case_id == "own_offer_request":
                details = {}
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=owner.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                )
                phase_details.append(details)
            elif normalized_case_id == "invalid_request_amount":
                details = {}
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=responder_a.user_id,
                        offer_id=offer_id,
                        quantity=999,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                )
                phase_details.append(details)
            elif normalized_case_id == "already_completed_offer":
                first_details: dict[str, Any] = {}
                second_details: dict[str, Any] = {}
                first_started = time.perf_counter()
                try:
                    completed_trade_id = await mark_offer_completed_for_negative_guard(
                        offer_id=offer_id,
                        responder_user_id=responder_a.user_id,
                        actor_user_id=responder_a.user_id,
                    )
                except Exception as exc:
                    first_details["business_latency_ms"] = round((time.perf_counter() - first_started) * 1000.0, 3)
                    first_details["execute_trade_ms"] = first_details["business_latency_ms"]
                    first_details["exception"] = type(exc).__name__
                    statuses.append("error")
                else:
                    first_details["business_latency_ms"] = round((time.perf_counter() - first_started) * 1000.0, 3)
                    first_details["execute_trade_ms"] = first_details["business_latency_ms"]
                    first_details["fixture_completion"] = "direct_completed_offer_state"
                    first_details["completed_trade_id"] = completed_trade_id
                    statuses.append("success")
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=responder_b.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=second_details,
                    )
                )
                phase_details.extend([first_details, second_details])
            elif normalized_case_id == "manually_expired_offer":
                await expire_offer_for_user(user_id=owner.user_id, offer_id=offer_id)
                details = {}
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=responder_a.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                )
                phase_details.append(details)
            elif normalized_case_id == "time_expired_offer":
                await age_offer_for_time_expiry(offer_id=offer_id, age_minutes=24 * 60)
                details = {}
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=responder_a.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                )
                phase_details.append(details)
            elif normalized_case_id == "market_closed":
                details = {}
                previous_evaluator = trades_router.evaluate_current_market_schedule
                trades_router.evaluate_current_market_schedule = fake_market_closed
                try:
                    statuses.append(
                        await execute_webapp_trade_for_user(
                            user_id=responder_a.user_id,
                            offer_id=offer_id,
                            quantity=5,
                            idempotency_key=f"{prefix}{normalized_case_id}-reject",
                            phase_details=details,
                        )
                    )
                finally:
                    trades_router.evaluate_current_market_schedule = previous_evaluator
                phase_details.append(details)
            elif normalized_case_id == "inactive_offer_owner":
                await update_synthetic_user_for_negative_guard(
                    owner.user_id,
                    account_status=UserAccountStatus.INACTIVE,
                )
                details = {}
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=responder_a.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                )
                phase_details.append(details)
            elif normalized_case_id == "inactive_requester":
                await update_synthetic_user_for_negative_guard(
                    responder_a.user_id,
                    account_status=UserAccountStatus.INACTIVE,
                )
                details = {}
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=responder_a.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                )
                phase_details.append(details)
            elif normalized_case_id == "trading_restricted_user":
                await update_synthetic_user_for_negative_guard(
                    responder_a.user_id,
                    trading_restricted_until=datetime.utcnow() + timedelta(days=1),
                )
                details = {}
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=responder_a.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                )
                phase_details.append(details)
            elif normalized_case_id == "watch_role_market_action":
                await update_synthetic_user_for_negative_guard(
                    responder_a.user_id,
                    role=UserRole.WATCH,
                )
                details = {}
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=responder_a.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                )
                phase_details.append(details)
            elif normalized_case_id == "accountant_market_action":
                details = {}
                statuses.append(
                    await execute_accountant_context_trade_for_user(
                        owner_user_id=responder_a.user_id,
                        actor_user_id=responder_b.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                )
                phase_details.append(details)
            elif normalized_case_id == "tier2_offer_creation":
                relation_id = await create_active_customer_relation_for_negative_guard(
                    owner_user_id=owner.user_id,
                    customer_user_id=responder_a.user_id,
                    prefix=prefix,
                    tier=CustomerTier.TIER_2,
                )
                before_offer_count = await count_offers_for_user(responder_a.user_id)
                details = {
                    "customer_relation_id": relation_id,
                    "before_offer_count": before_offer_count,
                }
                statuses.append(
                    await execute_offer_creation_for_user(
                        user_id=responder_a.user_id,
                        commodity_id=commodity_id,
                        prefix=prefix,
                        index=2,
                        phase_details=details,
                    )
                )
                after_offer_count = await count_offers_for_user(responder_a.user_id)
                details["after_offer_count"] = after_offer_count
                details["created_offer_delta"] = after_offer_count - before_offer_count
                extra_evidence["offer_creation"] = {
                    "customer_relation_id": relation_id,
                    "before_offer_count": before_offer_count,
                    "after_offer_count": after_offer_count,
                    "created_offer_delta": after_offer_count - before_offer_count,
                }
                phase_details.append(details)
            elif normalized_case_id == "tier2_telegram_request":
                relation_id = await create_active_customer_relation_for_negative_guard(
                    owner_user_id=owner.user_id,
                    customer_user_id=responder_a.user_id,
                    prefix=prefix,
                    tier=CustomerTier.TIER_2,
                )
                details = {"customer_relation_id": relation_id}
                harness = AiogramDispatcherHarness()
                try:
                    statuses.append(
                        await execute_bot_trade_with_dispatcher(
                            harness=harness,
                            spec=MixedLoadAttemptSpec(
                                index=0,
                                surface="telegram",
                                user_id=responder_a.user_id,
                                telegram_id=responder_a.telegram_id,
                            ),
                            offer=await load_offer_snapshot(offer_id),
                            amount=5,
                            prefix=f"{prefix}{normalized_case_id}-",
                            phase_details=details,
                        )
                    )
                finally:
                    await harness.close()
                extra_evidence["bot_callback"] = {
                    "first_answer_text": details.get("first_answer_text"),
                    "first_answer_alert": details.get("first_answer_alert"),
                    "second_answer_text": details.get("second_answer_text"),
                    "second_answer_alert": details.get("second_answer_alert"),
                    "telegram_update_count": details.get("telegram_update_count"),
                }
                phase_details.append(details)
            elif normalized_case_id == "daily_trade_limit_exceeded":
                await update_synthetic_user_for_negative_guard(
                    responder_a.user_id,
                    limitations_expire_at=datetime.utcnow() + timedelta(days=1),
                    max_daily_trades=1,
                    trades_count=1,
                    max_active_commodities=None,
                    max_daily_requests=None,
                )
                details = {}
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=responder_a.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                )
                phase_details.append(details)
            elif normalized_case_id in {"daily_request_limit_exceeded", "active_commodity_limit_exceeded"}:
                if normalized_case_id == "daily_request_limit_exceeded":
                    await update_synthetic_user_for_negative_guard(
                        responder_a.user_id,
                        limitations_expire_at=datetime.utcnow() + timedelta(days=1),
                        max_daily_trades=None,
                        trades_count=0,
                        max_active_commodities=None,
                        commodities_traded_count=0,
                        max_daily_requests=1,
                        channel_messages_count=1,
                    )
                else:
                    await update_synthetic_user_for_negative_guard(
                        responder_a.user_id,
                        limitations_expire_at=datetime.utcnow() + timedelta(days=1),
                        max_daily_trades=None,
                        trades_count=0,
                        max_active_commodities=1,
                        commodities_traded_count=1,
                        max_daily_requests=None,
                        channel_messages_count=0,
                    )
                before_offer_count = await count_offers_for_user(responder_a.user_id)
                details = {"before_offer_count": before_offer_count}
                statuses.append(
                    await execute_offer_creation_for_user(
                        user_id=responder_a.user_id,
                        commodity_id=commodity_id,
                        prefix=prefix,
                        index=2,
                        phase_details=details,
                    )
                )
                after_offer_count = await count_offers_for_user(responder_a.user_id)
                details["after_offer_count"] = after_offer_count
                details["created_offer_delta"] = after_offer_count - before_offer_count
                extra_evidence["offer_creation"] = {
                    "before_offer_count": before_offer_count,
                    "after_offer_count": after_offer_count,
                    "created_offer_delta": after_offer_count - before_offer_count,
                }
                phase_details.append(details)
            elif normalized_case_id == "remote_authority_unavailable":
                remote_home_server = SERVER_FOREIGN if current_server() == SERVER_IRAN else SERVER_IRAN
                await set_offer_home_server_for_negative_guard(offer_id, remote_home_server)

                async def unavailable_forward(
                    _target_server: str,
                    _payload: dict[str, Any],
                    *,
                    timeout_seconds: float | None = None,
                ) -> tuple[int, Any]:
                    return 503, {"detail": "سرور مرجع معامله در دسترس نیست."}

                details = {"remote_home_server": remote_home_server}
                previous_forward = trades_router.forward_trade_to_home_server
                trades_router.forward_trade_to_home_server = unavailable_forward
                try:
                    status_value = await execute_webapp_trade_for_user(
                        user_id=responder_a.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-reject",
                        phase_details=details,
                    )
                    if status_value == "error" and int(details.get("json_response_status_code") or 0) >= 500:
                        status_value = "rejected"
                    statuses.append(status_value)
                finally:
                    trades_router.forward_trade_to_home_server = previous_forward
                extra_evidence["remote_forward"] = {
                    "target_server": remote_home_server,
                    "status_code": details.get("json_response_status_code"),
                    "detail": details.get("json_response_detail"),
                }
                phase_details.append(details)
            elif normalized_case_id in {"bad_internal_signature", "wrong_authoritative_server"}:
                local_server = current_server()
                remote_source_server = SERVER_FOREIGN if local_server == SERVER_IRAN else SERVER_IRAN
                offer_public_id = await load_offer_public_id_for_negative_guard(offer_id)
                details = {"source_server": remote_source_server}
                headers = {
                    "x-timestamp": "1",
                    "x-signature": "bad-signature",
                    "x-api-key": "bad-key",
                    "x-source-server": remote_source_server,
                }
                patch_signature_result: bool | None = None
                if normalized_case_id == "wrong_authoritative_server":
                    await set_offer_home_server_for_negative_guard(offer_id, remote_source_server)
                    patch_signature_result = True
                    headers = {
                        "x-timestamp": "1",
                        "x-signature": "valid-for-negative-guard",
                        "x-api-key": "valid-for-negative-guard",
                        "x-source-server": remote_source_server,
                    }
                    details["offer_home_server"] = remote_source_server
                statuses.append(
                    await execute_internal_trade_for_negative_guard(
                        offer_id=offer_id,
                        offer_public_id=offer_public_id,
                        responder_user_id=responder_a.user_id,
                        source_server=remote_source_server,
                        headers=headers,
                        body=b'{"negative_guard":"internal"}',
                        patch_signature_result=patch_signature_result,
                        phase_details=details,
                    )
                )
                extra_evidence["internal_execute"] = {
                    "status_code": details.get("http_status_code"),
                    "detail": details.get("http_detail"),
                    "source_server": remote_source_server,
                    "offer_home_server": details.get("offer_home_server"),
                }
                phase_details.append(details)
            elif normalized_case_id == "stale_telegram_button":
                first_details: dict[str, Any] = {}
                stale_callback_details: dict[str, Any] = {}
                stale_offer_snapshot = await load_offer_snapshot(offer_id)
                statuses.append(
                    await execute_webapp_trade_for_user(
                        user_id=responder_a.user_id,
                        offer_id=offer_id,
                        quantity=5,
                        idempotency_key=f"{prefix}{normalized_case_id}-complete",
                        phase_details=first_details,
                    )
                )
                harness = AiogramDispatcherHarness()
                try:
                    statuses.append(
                        await execute_bot_trade_with_dispatcher(
                            harness=harness,
                            spec=MixedLoadAttemptSpec(
                                index=0,
                                surface="telegram",
                                user_id=responder_b.user_id,
                                telegram_id=responder_b.telegram_id,
                            ),
                            offer=stale_offer_snapshot,
                            amount=5,
                            prefix=f"{prefix}{normalized_case_id}-",
                            phase_details=stale_callback_details,
                        )
                    )
                finally:
                    await harness.close()
                extra_evidence["bot_callback"] = {
                    "first_answer_text": stale_callback_details.get("first_answer_text"),
                    "first_answer_alert": stale_callback_details.get("first_answer_alert"),
                    "second_answer_text": stale_callback_details.get("second_answer_text"),
                    "second_answer_alert": stale_callback_details.get("second_answer_alert"),
                    "telegram_update_count": stale_callback_details.get("telegram_update_count"),
                }
                phase_details.extend([first_details, stale_callback_details])
            elif normalized_case_id == "missing_public_offer_id":
                local_server = current_server()
                remote_source_server = SERVER_FOREIGN if local_server == SERVER_IRAN else SERVER_IRAN
                missing_public_id = f"ofr_missing_{str(offer_id)[-24:]}"
                details = {
                    "source_server": remote_source_server,
                    "missing_offer_public_id": missing_public_id,
                }
                headers = {
                    "x-timestamp": "1",
                    "x-signature": "valid-for-negative-guard",
                    "x-api-key": "valid-for-negative-guard",
                    "x-source-server": remote_source_server,
                }
                statuses.append(
                    await execute_internal_trade_for_negative_guard(
                        offer_id=offer_id,
                        offer_public_id=missing_public_id,
                        responder_user_id=responder_a.user_id,
                        source_server=remote_source_server,
                        headers=headers,
                        body=b'{"negative_guard":"missing_public_offer_id"}',
                        patch_signature_result=True,
                        phase_details=details,
                    )
                )
                extra_evidence["internal_execute"] = {
                    "status_code": details.get("http_status_code"),
                    "detail": details.get("http_detail"),
                    "source_server": remote_source_server,
                    "missing_offer_public_id": missing_public_id,
                }
                phase_details.append(details)
            elif normalized_case_id == "cleanup_scope_violation":
                rejected_values: list[dict[str, str]] = []
                accepted_values: list[str] = []
                cleanup_scope_checks = (
                    ("cleanup_prefix", "prod"),
                    ("cleanup_prefix", "bad%prefix"),
                    ("production_cleanup_prefix", "SAFE_CLEANUP_PREFIX"),
                    ("production_cleanup_prefix", "PFM_bad"),
                )
                for check_name, value in cleanup_scope_checks:
                    try:
                        if check_name == "production_cleanup_prefix":
                            validate_production_cleanup_prefix(value)
                        else:
                            validate_cleanup_prefix(value)
                    except TradingProbeError as exc:
                        rejected_values.append(
                            {
                                "check": check_name,
                                "value": value,
                                "reason": str(exc),
                            }
                        )
                    else:
                        accepted_values.append(value)
                statuses.append("rejected" if rejected_values and not accepted_values else "error")
                extra_evidence["cleanup_scope_guard"] = {
                    "rejected_values": rejected_values,
                    "accepted_values": accepted_values,
                }
                phase_details.append(dict(extra_evidence["cleanup_scope_guard"]))
            else:
                raise TradingProbeError(f"negative guard case branch is missing: {normalized_case_id}")

    evidence = await negative_guard_offer_evidence(offer_id)
    evidence.update(extra_evidence)
    failures = assert_negative_guard_evidence(
        case_id=normalized_case_id,
        status_sequence=statuses,
        evidence=evidence,
    )
    return {
        "schema_version": NEGATIVE_GUARD_RESULT_SCHEMA_VERSION,
        "status": "passed" if not failures else "failed",
        "server_mode": settings.server_mode,
        "prefix": prefix,
        "case_id": normalized_case_id,
        "status_sequence": statuses,
        "phase_details": phase_details,
        "evidence": evidence,
        "assertion_failures": failures,
    }


async def inspect_hot_offer_persistence(offer_id: int) -> HotOfferPersistenceSnapshot:
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, offer_id)
        if offer is None:
            raise TradingProbeError(f"offer {offer_id} disappeared after contention")
        trade_rows = (
            await db.execute(
                select(Trade.id, Trade.quantity).where(
                    Trade.offer_id == offer_id,
                    Trade.status == TradeStatus.COMPLETED,
                )
            )
        ).all()
        trade_ids = {int(row[0]) for row in trade_rows if row[0] is not None}
        completed_trade_quantity = sum(int(row[1] or 0) for row in trade_rows)
        completed_ledger_trade_ids = {
            int(value)
            for value in (
                await db.execute(
                    select(OfferRequest.resulting_trade_id).where(
                        OfferRequest.local_offer_id == offer_id,
                        OfferRequest.result_status == OfferRequestStatus.COMPLETED_TRADE,
                        OfferRequest.resulting_trade_id.is_not(None),
                    )
                )
            ).scalars()
            if value is not None
        }
        completed_ledger_count = int(
            await db.scalar(
                select(func.count(OfferRequest.id)).where(
                    OfferRequest.local_offer_id == offer_id,
                    OfferRequest.result_status == OfferRequestStatus.COMPLETED_TRADE,
                )
            )
            or 0
        )
        failed_internal_ledger_count = int(
            await db.scalar(
                select(func.count(OfferRequest.id)).where(
                    OfferRequest.local_offer_id == offer_id,
                    OfferRequest.result_status == OfferRequestStatus.FAILED_INTERNAL,
                )
            )
            or 0
        )
        duplicate_replay_ledger_count = int(
            await db.scalar(
                select(func.count(OfferRequest.id)).where(
                    OfferRequest.local_offer_id == offer_id,
                    OfferRequest.result_status == OfferRequestStatus.DUPLICATE_REPLAY,
                )
            )
            or 0
        )
        return HotOfferPersistenceSnapshot(
            offer_id=offer_id,
            original_quantity=int(offer.quantity or 0),
            remaining_quantity=int(offer.remaining_quantity or 0) if offer.remaining_quantity is not None else None,
            offer_status=getattr(getattr(offer, "status", None), "value", None),
            persisted_trade_count=len(trade_ids),
            completed_trade_quantity=completed_trade_quantity,
            completed_ledger_count=completed_ledger_count,
            trades_without_completed_ledger_count=len(trade_ids - completed_ledger_trade_ids),
            failed_internal_ledger_count=failed_internal_ledger_count,
            duplicate_replay_ledger_count=duplicate_replay_ledger_count,
        )


async def create_bot_offer_with_dispatcher(
    *,
    harness: AiogramDispatcherHarness,
    owner: LoadUserRef,
    commodity_name: str,
    prefix: str,
    quantity: int,
    price: int,
    offer_type: str,
    is_wholesale: bool = True,
    lot_sizes: list[int] | tuple[int, ...] | None = None,
) -> int:
    verb = "خ" if offer_type == "buy" else "ف"
    marker = f"{prefix} bot hot {owner.user_id}"
    lots_text = ""
    if not is_wholesale and lot_sizes:
        lots_text = " " + " ".join(str(item) for item in lot_sizes)
    text_value = f"{verb} {commodity_name} {quantity} عدد {price}{lots_text}: {marker}"
    await harness.feed_private_text(telegram_id=owner.telegram_id, text_value=text_value)
    await harness.feed_private_callback(
        telegram_id=owner.telegram_id,
        callback_data=TextOfferActionCallback(action="confirm").pack(),
        callback_id=f"{prefix}bot-create-confirm-{owner.user_id}",
    )
    async with AsyncSessionLocal() as db:
        offer = (
            await db.execute(
                select(Offer)
                .where(Offer.user_id == owner.user_id, Offer.notes == marker)
                .order_by(Offer.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if offer is None:
            raise TradingProbeError("Dispatcher bot offer creation did not persist an offer")
        return int(offer.id)


async def load_offer_snapshot(offer_id: int) -> Offer:
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, offer_id)
        if offer is None:
            raise TradingProbeError(f"offer {offer_id} disappeared")
        await db.refresh(offer, ["commodity"])
        return offer


async def preconfirm_bot_trade_callbacks(
    *,
    attempts: list[MixedLoadAttemptSpec],
    offer: Offer,
    amount: int,
) -> dict[str, Any]:
    callback_data = build_channel_trade_callback_data(
        offer_id=offer.id,
        offer_public_id=getattr(offer, "offer_public_id", None),
        amount=amount,
    )
    parsed = parse_telegram_trade_callback_data(callback_data)
    if parsed is None:
        raise TradingProbeError("telegram preconfirm could not parse generated callback data")

    async def _preconfirm(spec: MixedLoadAttemptSpec) -> bool | None | str:
        return await preconfirm_parsed_bot_trade_callback(spec=spec, parsed=parsed)

    started = time.perf_counter()
    results = await asyncio.gather(*[_preconfirm(spec) for spec in attempts])
    errors = [result for result in results if isinstance(result, str)]
    return {
        "attempt_count": len(attempts),
        "ready_count": sum(result is False for result in results),
        "fallback_count": sum(result is None for result in results),
        "unexpected_confirmed_count": sum(result is True for result in results),
        "error_count": len(errors),
        "sample_errors": errors[:5],
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


async def preconfirm_parsed_bot_trade_callback(
    *,
    spec: MixedLoadAttemptSpec,
    parsed: ParsedTelegramTradeCallback,
) -> bool | None | str:
    try:
        result = await claim_telegram_trade_confirmation(telegram_id=spec.telegram_id, parsed=parsed)
        if result is True:
            result = await claim_telegram_trade_confirmation(telegram_id=spec.telegram_id, parsed=parsed)
        return result
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


async def preconfirm_bot_trade_callback(
    *,
    spec: MixedLoadAttemptSpec,
    offer: Offer,
    amount: int,
) -> bool | None | str:
    callback_data = build_channel_trade_callback_data(
        offer_id=offer.id,
        offer_public_id=getattr(offer, "offer_public_id", None),
        amount=amount,
    )
    parsed = parse_telegram_trade_callback_data(callback_data)
    if parsed is None:
        raise TradingProbeError("telegram preconfirm could not parse generated callback data")
    return await preconfirm_parsed_bot_trade_callback(spec=spec, parsed=parsed)


async def execute_bot_trade_with_dispatcher(
    *,
    harness: AiogramDispatcherHarness,
    spec: MixedLoadAttemptSpec,
    offer: Offer,
    amount: int,
    prefix: str,
    observed_idempotency_keys: list[str] | None = None,
    error_details: list[str] | None = None,
    phase_details: dict[str, Any] | None = None,
    preconfirmed: bool = False,
) -> str:
    callback_data = build_channel_trade_callback_data(
        offer_id=offer.id,
        offer_public_id=getattr(offer, "offer_public_id", None),
        amount=amount,
    )
    channel_message_id = int(getattr(offer, "channel_message_id", None) or getattr(offer, "id", 0) or 1)
    first_callback_id = f"{prefix}tap1-{spec.index}"
    second_callback_id = f"{prefix}tap2-{spec.index}"
    recorder_token = None
    telegram_update_count = 0
    if observed_idempotency_keys is not None:
        recorder_token = _TELEGRAM_IDEMPOTENCY_KEYS.set(observed_idempotency_keys)
    try:
        if preconfirmed:
            if phase_details is not None:
                phase_details["preconfirmed_before_barrier"] = True
        else:
            first_started = time.perf_counter()
            first_answer = await harness.feed_channel_callback(
                telegram_id=spec.telegram_id,
                callback_data=callback_data,
                callback_id=first_callback_id,
                channel_message_id=channel_message_id,
            )
            telegram_update_count += 1
            if phase_details is not None:
                phase_details["first_callback_ms"] = round((time.perf_counter() - first_started) * 1000.0, 3)
                phase_details["first_answer_text"] = str((first_answer or {}).get("text") or "")
                phase_details["first_answer_alert"] = (first_answer or {}).get("show_alert")
        second_started = time.perf_counter()
        answer = await harness.feed_channel_callback(
            telegram_id=spec.telegram_id,
            callback_data=callback_data,
            callback_id=second_callback_id,
            channel_message_id=channel_message_id,
        )
        telegram_update_count += 1
        if phase_details is not None:
            business_latency_ms = round((time.perf_counter() - second_started) * 1000.0, 3)
            phase_details["business_latency_ms"] = business_latency_ms
            phase_details["second_callback_ms"] = business_latency_ms
            phase_details["second_answer_text"] = str((answer or {}).get("text") or "")
            phase_details["second_answer_alert"] = (answer or {}).get("show_alert")
            phase_details["telegram_update_count"] = telegram_update_count
    except Exception as exc:
        if error_details is not None:
            error_details.append(f"{type(exc).__name__}: {exc}")
        if phase_details is not None:
            phase_details["exception"] = type(exc).__name__
            phase_details["telegram_update_count"] = telegram_update_count
        return "error"
    finally:
        if recorder_token is not None:
            _TELEGRAM_IDEMPOTENCY_KEYS.reset(recorder_token)

    answer_text = str((answer or {}).get("text") or "")
    if "معامله ثبت شد" in answer_text:
        return "success"
    if answer_text:
        return "rejected"
    return "rejected"


async def expire_bot_offer_with_dispatcher(
    *,
    harness: AiogramDispatcherHarness,
    owner: LoadUserRef,
    offer_id: int,
    prefix: str,
    index: int,
    error_details: list[str] | None = None,
) -> str:
    try:
        answer = await harness.feed_private_callback(
            telegram_id=owner.telegram_id,
            callback_data=ExpireOfferCallback(offer_id=offer_id).pack(),
            callback_id=f"{prefix}bot-expire-{owner.user_id}-{index}",
        )
    except Exception as exc:
        if error_details is not None:
            error_details.append(f"{type(exc).__name__}: {exc}")
        return "error"
    answer_text = str((answer or {}).get("text") or "")
    if "منقضی شد" in answer_text:
        return "success"
    if answer_text and error_details is not None:
        error_details.append(f"bot_expire_rejected: {answer_text}")
    return "rejected"


async def execute_bot_market_view_with_dispatcher(
    *,
    harness: AiogramDispatcherHarness,
    user: LoadUserRef,
    error_details: list[str] | None = None,
) -> str:
    try:
        await harness.feed_private_text(telegram_id=user.telegram_id, text_value="📈 معامله")
    except Exception as exc:
        if error_details is not None:
            error_details.append(f"{type(exc).__name__}: {exc}")
        return "error"
    return "success"


def write_json_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=json_safe) + "\n",
        encoding="utf-8",
    )


def read_json_artifact(path: Path) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TradingProbeError(f"failed to read artifact {path}: {exc}") from exc
    return _require_mapping(data, f"artifact {path}")


async def run_role_worker_plan(plan_payload: Mapping[str, Any]) -> dict[str, Any]:
    plan = validate_role_plan_artifact(plan_payload)
    role = str(plan["role"])
    surface = str(plan["surface"])
    offer = _require_mapping(plan["offer"], "role plan offer")
    offer_id = int(offer["id"])
    offer_public_id = str(offer["public_id"]) if offer.get("public_id") else None
    request_amount = int(plan["request_amount"])
    prefix = str(plan["prefix"])
    target_rps = float(plan["target_rps"])
    barrier_epoch = float(plan["barrier_epoch"])
    attempts = role_plan_attempt_specs(plan)
    harness = AiogramDispatcherHarness() if role == "telegram_foreign" else None
    warmup = await warm_load_runner_dependencies(db_connections=min(len(attempts), 24))
    telegram_offer_snapshot = await load_offer_snapshot(offer_id) if harness is not None else None
    telegram_preconfirm: dict[str, Any] | None = None

    start_delay = barrier_epoch - time.time()
    if harness is not None and telegram_offer_snapshot is not None:
        preconfirm_delay = start_delay - 0.5
        if preconfirm_delay > 0:
            await asyncio.sleep(preconfirm_delay)
        telegram_preconfirm = await preconfirm_bot_trade_callbacks(
            attempts=attempts,
            offer=telegram_offer_snapshot,
            amount=request_amount,
        )
        start_delay = barrier_epoch - time.time()
    if start_delay > 0:
        await asyncio.sleep(start_delay)
    started_epoch = time.time()
    started_monotonic = time.perf_counter()

    async def _attempt(spec: MixedLoadAttemptSpec) -> tuple[MixedLoadAttemptResult, dict[str, Any]]:
        scheduled_epoch = barrier_epoch + (spec.index / target_rps)
        scheduled_delay = scheduled_epoch - time.time()
        if scheduled_delay > 0:
            await asyncio.sleep(scheduled_delay)

        monotonic_timestamp = time.perf_counter()
        attempt_started = time.perf_counter()
        status_value = "error"
        detail: str | None = None
        idempotency_key: str | None = spec.idempotency_key or build_role_attempt_idempotency_key(
            prefix=prefix,
            role=role,
            offer_id=offer_id,
            attempt_index=spec.index,
        )
        observed_telegram_keys: list[str] = []
        attempt_error_details: list[str] = []
        phase_details: dict[str, Any] = {}
        try:
            if surface == "webapp":
                status_value = await execute_webapp_trade_for_user(
                    user_id=spec.user_id,
                    offer_id=offer_id,
                    offer_public_id=offer_public_id,
                    quantity=request_amount,
                    idempotency_key=idempotency_key,
                    error_details=attempt_error_details,
                    phase_details=phase_details,
                )
            else:
                if harness is None or telegram_offer_snapshot is None:
                    raise TradingProbeError("telegram role worker requires dispatcher harness")
                status_value = await execute_bot_trade_with_dispatcher(
                    harness=harness,
                    spec=spec,
                    offer=telegram_offer_snapshot,
                    amount=request_amount,
                    prefix=f"{prefix}{role}-",
                    observed_idempotency_keys=observed_telegram_keys,
                    error_details=attempt_error_details,
                    phase_details=phase_details,
                    preconfirmed=telegram_preconfirm is not None,
                )
                if observed_telegram_keys:
                    idempotency_key = observed_telegram_keys[-1]
        except Exception as exc:
            status_value = "error"
            detail = f"{type(exc).__name__}: {exc}"
        if status_value == "error" and detail is None and attempt_error_details:
            detail = attempt_error_details[-1]
        full_latency_ms = round((time.perf_counter() - attempt_started) * 1000.0, 3)
        try:
            latency_ms = round(float(phase_details.get("business_latency_ms")), 3)
        except (TypeError, ValueError):
            latency_ms = full_latency_ms
        telegram_update_count = int(phase_details.get("telegram_update_count") or 0)
        result = MixedLoadAttemptResult(
            index=spec.index,
            surface=surface,
            status=status_value,
            duration_ms=latency_ms,
            detail=detail,
            telegram_update_count=telegram_update_count,
        )
        return result, {
            "index": spec.index,
            "monotonic_timestamp": round(monotonic_timestamp, 6),
            "source_role": role,
            "source_surface": surface,
            "user_id": spec.user_id,
            "telegram_id": spec.telegram_id,
            "offer_id": offer_id,
            "offer_public_id": offer_public_id,
            "idempotency_key": idempotency_key,
            "idempotency_key_observed": bool(observed_telegram_keys) if surface == "telegram" else True,
            "outcome": status_value,
            "latency_ms": latency_ms,
            "full_latency_ms": full_latency_ms,
            "detail": detail,
            "phase_details": phase_details,
            "telegram_update_count": telegram_update_count,
        }

    try:
        pairs = await asyncio.gather(*[_attempt(spec) for spec in attempts])
    finally:
        if harness is not None:
            await harness.close()

    finished_epoch = time.time()
    finished_monotonic = time.perf_counter()
    result_items = [result for result, _artifact in pairs]
    attempt_artifacts = [artifact for _result, artifact in pairs]
    return {
        "schema_version": DUAL_ROLE_RESULT_SCHEMA_VERSION,
        "run_id": str(plan["run_id"]),
        "role": role,
        "surface": surface,
        "started_epoch": round(started_epoch, 6),
        "finished_epoch": round(finished_epoch, 6),
        "started_monotonic": round(started_monotonic, 6),
        "finished_monotonic": round(finished_monotonic, 6),
        "warmup": warmup,
        "telegram_preconfirm": telegram_preconfirm,
        "summary": summarize_attempt_results(result_items, elapsed_seconds=finished_monotonic - started_monotonic),
        "attempts": attempt_artifacts,
    }


def build_dry_role_result_artifact(plan_payload: Mapping[str, Any], *, started_epoch: float) -> dict[str, Any]:
    plan = validate_role_plan_artifact(plan_payload)
    role = str(plan["role"])
    surface = str(plan["surface"])
    offer = _require_mapping(plan["offer"], "role plan offer")
    offer_id = int(offer["id"])
    offer_public_id = str(offer["public_id"]) if offer.get("public_id") else None
    attempts = role_plan_attempt_specs(plan)
    attempt_artifacts: list[dict[str, Any]] = []
    results: list[MixedLoadAttemptResult] = []
    for offset, spec in enumerate(attempts):
        idempotency_key = spec.idempotency_key or build_role_attempt_idempotency_key(
            prefix=str(plan["prefix"]),
            role=role,
            offer_id=offer_id,
            attempt_index=spec.index,
        )
        outcome = "success" if offset == 0 else "rejected"
        latency_ms = 1.0 + float(offset)
        monotonic_timestamp = started_epoch + (float(offset) / max(float(plan["target_rps"]), 1.0))
        attempt_artifacts.append(
            {
                "index": spec.index,
                "monotonic_timestamp": round(monotonic_timestamp, 6),
                "source_role": role,
                "source_surface": surface,
                "user_id": spec.user_id,
                "telegram_id": spec.telegram_id,
                "offer_id": offer_id,
                "offer_public_id": offer_public_id,
                "idempotency_key": idempotency_key,
                "idempotency_key_observed": surface == "webapp",
                "outcome": outcome,
                "latency_ms": latency_ms,
                "detail": "dry_run_artifact_smoke",
                "telegram_update_count": 2 if surface == "telegram" else 0,
            }
        )
        results.append(
            MixedLoadAttemptResult(
                index=spec.index,
                surface=surface,
                status=outcome,
                duration_ms=latency_ms,
                detail="dry_run_artifact_smoke",
                telegram_update_count=2 if surface == "telegram" else 0,
            )
        )

    finished_epoch = started_epoch + (len(attempts) / max(float(plan["target_rps"]), 1.0))
    return {
        "schema_version": DUAL_ROLE_RESULT_SCHEMA_VERSION,
        "run_id": str(plan["run_id"]),
        "role": role,
        "surface": surface,
        "started_epoch": round(started_epoch, 6),
        "finished_epoch": round(finished_epoch, 6),
        "started_monotonic": round(started_epoch, 6),
        "finished_monotonic": round(finished_epoch, 6),
        "summary": summarize_attempt_results(results, elapsed_seconds=finished_epoch - started_epoch),
        "attempts": attempt_artifacts,
        "mode": "artifact_smoke",
    }


async def run_hot_offer_contention(
    *,
    prefix: str,
    offer_id: int,
    owner_user_id: int,
    users: list[LoadUserRef],
    total_requests: int,
    telegram_ratio: float,
    target_rps: float,
    amount: int,
    expected_winner_count: int,
    check: bool = True,
) -> dict[str, Any]:
    if target_rps <= 0:
        raise TradingProbeError("target_rps must be positive")

    harness = AiogramDispatcherHarness()
    plan = build_mixed_surface_plan(
        users=users,
        owner_user_id=owner_user_id,
        total_requests=total_requests,
        telegram_ratio=telegram_ratio,
    )
    telegram_specs = [spec for spec in plan if spec.surface == "telegram"]
    telegram_offer_snapshot = await load_offer_snapshot(offer_id) if telegram_specs else None
    telegram_preconfirm = None
    if telegram_specs and telegram_offer_snapshot is not None:
        with override_current_server(SERVER_FOREIGN):
            telegram_preconfirm = await preconfirm_bot_trade_callbacks(
                attempts=telegram_specs,
                offer=telegram_offer_snapshot,
                amount=amount,
            )
    started = time.perf_counter()

    async def _attempt(spec: MixedLoadAttemptSpec) -> MixedLoadAttemptResult:
        scheduled_at = started + (spec.index / target_rps)
        delay = scheduled_at - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)

        attempt_started = time.perf_counter()
        start_offset_seconds = attempt_started - started
        status_value = "error"
        detail: str | None = None
        phase_details: dict[str, Any] = {}
        attempt_error_details: list[str] = []
        try:
            with override_current_server(server_for_load_surface(spec.surface)):
                if spec.surface == "webapp":
                    status_value = await execute_webapp_trade_for_user(
                        user_id=spec.user_id,
                        offer_id=offer_id,
                        quantity=amount,
                        idempotency_key=build_role_attempt_idempotency_key(
                            prefix=prefix,
                            role="webapp",
                            offer_id=offer_id,
                            attempt_index=spec.index,
                        ),
                        error_details=attempt_error_details,
                        phase_details=phase_details,
                    )
                else:
                    if telegram_offer_snapshot is None:
                        raise TradingProbeError("telegram hot-offer contention requires an offer snapshot")
                    status_value = await execute_bot_trade_with_dispatcher(
                        harness=harness,
                        spec=spec,
                        offer=telegram_offer_snapshot,
                        amount=amount,
                        prefix=prefix,
                        error_details=attempt_error_details,
                        phase_details=phase_details,
                        preconfirmed=telegram_preconfirm is not None,
                    )
        except Exception as exc:
            status_value = "error"
            detail = f"{type(exc).__name__}: {exc}"
        if status_value == "error" and detail is None and attempt_error_details:
            detail = attempt_error_details[-1]
        full_latency_ms = round((time.perf_counter() - attempt_started) * 1000.0, 3)
        try:
            latency_ms = round(float(phase_details.get("business_latency_ms")), 3)
        except (TypeError, ValueError):
            latency_ms = full_latency_ms
        return MixedLoadAttemptResult(
            index=spec.index,
            surface=spec.surface,
            status=status_value,
            duration_ms=latency_ms,
            detail=detail,
            telegram_update_count=int(phase_details.get("telegram_update_count") or 0),
            start_offset_seconds=start_offset_seconds,
        )

    try:
        results = await asyncio.gather(*[_attempt(spec) for spec in plan])
    finally:
        await harness.close()
    elapsed = time.perf_counter() - started

    persistence = await inspect_hot_offer_persistence(offer_id)

    summary = summarize_attempt_results(results, elapsed_seconds=elapsed)
    correctness_failures: list[str] = []
    try:
        assert_hot_offer_contention_acceptance(
            persisted_trade_count=persistence.persisted_trade_count,
            response_success_count=int(summary["success"]),
            error_count=int(summary["error"]),
            remaining_quantity=persistence.remaining_quantity,
            status=persistence.offer_status,
            expected_winner_count=expected_winner_count,
            original_quantity=persistence.original_quantity,
            completed_trade_quantity=persistence.completed_trade_quantity,
            completed_ledger_count=persistence.completed_ledger_count,
            trades_without_completed_ledger_count=persistence.trades_without_completed_ledger_count,
            failed_internal_ledger_count=persistence.failed_internal_ledger_count,
        )
    except TradingProbeError as exc:
        correctness_failures.append(str(exc))
        if check:
            raise
    return {
        "offer_id": offer_id,
        "owner_user_id": owner_user_id,
        "expected_winner_count": expected_winner_count,
        "persisted_trade_count": persistence.persisted_trade_count,
        "offer_remaining_quantity": persistence.remaining_quantity,
        "offer_status": persistence.offer_status,
        "persistence": asdict(persistence),
        "correctness_failures": correctness_failures,
        "telegram_preconfirm": telegram_preconfirm,
        "summary": summary,
    }


async def create_hot_offer_for_scenario(
    *,
    scenario: HotOfferScenarioSpec,
    owner: LoadUserRef,
    commodity_id: int,
    commodity_name: str,
    prefix: str,
    index: int,
) -> int:
    if scenario.origin == "webapp":
        return await create_offer_for_user(
            user_id=owner.user_id,
            commodity_id=commodity_id,
            prefix=prefix,
            index=index,
            offer_type=scenario.offer_type,
            quantity=scenario.quantity,
            price=scenario.price,
            is_wholesale=scenario.is_wholesale,
            lot_sizes=list(scenario.lot_sizes) if scenario.lot_sizes else None,
        )
    if scenario.origin == "bot":
        harness = AiogramDispatcherHarness()
        try:
            return await create_bot_offer_with_dispatcher(
                harness=harness,
                owner=owner,
                commodity_name=commodity_name,
                prefix=prefix,
                quantity=scenario.quantity,
                price=scenario.price,
                offer_type=scenario.offer_type,
                is_wholesale=scenario.is_wholesale,
                lot_sizes=list(scenario.lot_sizes) if scenario.lot_sizes else None,
            )
        finally:
            await harness.close()
    raise TradingProbeError(f"unsupported hot-offer scenario origin: {scenario.origin}")


async def run_hot_offer_scenario(
    *,
    prefix: str,
    scenario: HotOfferScenarioSpec,
    users: list[LoadUserRef],
    commodity_id: int,
    commodity_name: str,
    index: int,
) -> dict[str, Any]:
    if len(users) < 3:
        raise TradingProbeError("hot-offer scenarios require at least three synthetic users")
    owner = users[1] if scenario.origin == "bot" else users[0]
    scenario_prefix = f"{prefix}{scenario.name}-"
    offer_id = await create_hot_offer_for_scenario(
        scenario=scenario,
        owner=owner,
        commodity_id=commodity_id,
        commodity_name=commodity_name,
        prefix=scenario_prefix,
        index=index,
    )
    report = await run_hot_offer_contention(
        prefix=scenario_prefix,
        offer_id=offer_id,
        owner_user_id=owner.user_id,
        users=users,
        total_requests=scenario.total_requests,
        telegram_ratio=scenario.telegram_ratio,
        target_rps=scenario.target_rps,
        amount=scenario.request_amount,
        expected_winner_count=scenario.expected_winner_count,
    )
    report["scenario"] = asdict(scenario)
    report["start_burst_request_count"] = scenario.start_burst_request_count
    return report


def assert_duplicate_replay_acceptance(
    *,
    statuses: list[str],
    persistence: HotOfferPersistenceSnapshot,
    expected_completed_quantity: int | None = None,
    expected_remaining_quantity: int = 0,
    require_terminal_completed: bool = True,
) -> None:
    if not statuses:
        raise TradingProbeError("duplicate/replay probe did not execute any attempts")
    error_count = sum(1 for status_value in statuses if status_value == "error")
    if error_count:
        raise TradingProbeError(f"duplicate/replay probe expected zero internal errors, got {error_count}")
    if "success" not in statuses:
        raise TradingProbeError("duplicate/replay probe expected at least one successful attempt")
    if persistence.persisted_trade_count != 1:
        raise TradingProbeError(
            f"duplicate/replay probe expected exactly one persisted trade, got {persistence.persisted_trade_count}"
        )
    if persistence.completed_trade_quantity > persistence.original_quantity:
        raise TradingProbeError(
            "duplicate/replay probe over-traded quantity "
            f"{persistence.completed_trade_quantity} > {persistence.original_quantity}"
        )
    expected_completed = (
        int(expected_completed_quantity)
        if expected_completed_quantity is not None
        else persistence.original_quantity - int(expected_remaining_quantity)
    )
    if persistence.completed_trade_quantity != expected_completed:
        raise TradingProbeError(
            "duplicate/replay probe expected completed quantity "
            f"{expected_completed}, got {persistence.completed_trade_quantity}"
        )
    if persistence.remaining_quantity != expected_remaining_quantity:
        raise TradingProbeError(
            "duplicate/replay probe expected "
            f"remaining_quantity={expected_remaining_quantity}, got {persistence.remaining_quantity}"
        )
    if require_terminal_completed and persistence.offer_status != OfferStatus.COMPLETED.value:
        raise TradingProbeError(f"duplicate/replay probe expected completed status, got {persistence.offer_status}")
    if not require_terminal_completed and persistence.offer_status not in {OfferStatus.ACTIVE.value, OfferStatus.COMPLETED.value}:
        raise TradingProbeError(
            f"duplicate/replay probe expected active or completed status, got {persistence.offer_status}"
        )
    if persistence.trades_without_completed_ledger_count:
        raise TradingProbeError("duplicate/replay probe persisted a trade without completed request ledger")
    if persistence.failed_internal_ledger_count:
        raise TradingProbeError(
            f"duplicate/replay probe expected zero failed_internal ledgers, got {persistence.failed_internal_ledger_count}"
        )


def assert_manual_expiry_trade_race_acceptance(
    *,
    response_success_count: int,
    error_count: int,
    persistence: HotOfferPersistenceSnapshot,
    expected_winner_count: int,
    manual_expiry_result: Mapping[str, Any] | None,
) -> None:
    if manual_expiry_result is None:
        raise TradingProbeError("manual-expiry race expected manual expiry result artifact")
    expiry_status = str(manual_expiry_result.get("status") or "unknown")
    if expiry_status == "error":
        detail = manual_expiry_result.get("detail") or manual_expiry_result.get("error_details") or "unknown"
        raise TradingProbeError(f"manual-expiry race expiry command errored: {detail}")
    if expiry_status not in {"success", "rejected"}:
        raise TradingProbeError(f"manual-expiry race got invalid expiry status={expiry_status!r}")
    if error_count:
        raise TradingProbeError(f"manual-expiry race expected zero internal request errors, got {error_count}")
    if persistence.persisted_trade_count < 0 or persistence.persisted_trade_count > expected_winner_count:
        raise TradingProbeError(
            "manual-expiry race persisted trade count outside expected winner bound: "
            f"{persistence.persisted_trade_count} > {expected_winner_count}"
        )
    if response_success_count != persistence.persisted_trade_count:
        raise TradingProbeError(
            "manual-expiry race successful responses do not match persisted trades: "
            f"{response_success_count} != {persistence.persisted_trade_count}"
        )
    if persistence.remaining_quantity is None or persistence.remaining_quantity < 0:
        raise TradingProbeError(
            f"manual-expiry race produced invalid remaining_quantity={persistence.remaining_quantity}"
        )
    if persistence.completed_trade_quantity > persistence.original_quantity:
        raise TradingProbeError(
            "manual-expiry race over-traded quantity "
            f"{persistence.completed_trade_quantity} > {persistence.original_quantity}"
        )
    expected_remaining = persistence.original_quantity - persistence.completed_trade_quantity
    if persistence.remaining_quantity != expected_remaining:
        raise TradingProbeError(
            "manual-expiry race quantity mismatch: "
            f"remaining={persistence.remaining_quantity}, expected={expected_remaining}"
        )
    if persistence.offer_status not in {OfferStatus.COMPLETED.value, OfferStatus.EXPIRED.value}:
        raise TradingProbeError(f"manual-expiry race expected terminal completed/expired offer, got {persistence.offer_status}")
    if persistence.offer_status == OfferStatus.COMPLETED.value and persistence.remaining_quantity != 0:
        raise TradingProbeError(
            f"manual-expiry race completed offer still has remaining_quantity={persistence.remaining_quantity}"
        )
    if expiry_status == "success" and persistence.offer_status != OfferStatus.EXPIRED.value:
        raise TradingProbeError(
            "manual-expiry race expiry command succeeded but final offer status is "
            f"{persistence.offer_status}"
        )
    if persistence.completed_ledger_count < persistence.persisted_trade_count:
        raise TradingProbeError("manual-expiry race has persisted trades without completed request ledger rows")
    if persistence.trades_without_completed_ledger_count:
        raise TradingProbeError("manual-expiry race has persisted trades without successful request ledger rows")
    if persistence.failed_internal_ledger_count:
        raise TradingProbeError(
            f"manual-expiry race expected zero failed_internal ledgers, got {persistence.failed_internal_ledger_count}"
        )


def assert_time_expiry_trade_race_acceptance(
    *,
    response_success_count: int,
    error_count: int,
    persistence: HotOfferPersistenceSnapshot,
    expected_winner_count: int,
    time_expiry_result: Mapping[str, Any] | None,
) -> None:
    if time_expiry_result is None:
        raise TradingProbeError("time-expiry race expected time expiry result artifact")
    expiry_status = str(time_expiry_result.get("status") or "unknown")
    if expiry_status == "error":
        detail = time_expiry_result.get("detail") or time_expiry_result.get("error_details") or "unknown"
        raise TradingProbeError(f"time-expiry race expiry command errored: {detail}")
    if expiry_status not in {"success", "rejected"}:
        raise TradingProbeError(f"time-expiry race got invalid expiry status={expiry_status!r}")
    if error_count:
        raise TradingProbeError(f"time-expiry race expected zero internal request errors, got {error_count}")
    if persistence.persisted_trade_count < 0 or persistence.persisted_trade_count > expected_winner_count:
        raise TradingProbeError(
            "time-expiry race persisted trade count outside expected winner bound: "
            f"{persistence.persisted_trade_count} > {expected_winner_count}"
        )
    if response_success_count != persistence.persisted_trade_count:
        raise TradingProbeError(
            "time-expiry race successful responses do not match persisted trades: "
            f"{response_success_count} != {persistence.persisted_trade_count}"
        )
    if persistence.remaining_quantity is None or persistence.remaining_quantity < 0:
        raise TradingProbeError(f"time-expiry race produced invalid remaining_quantity={persistence.remaining_quantity}")
    if persistence.completed_trade_quantity > persistence.original_quantity:
        raise TradingProbeError(
            f"time-expiry race over-traded quantity {persistence.completed_trade_quantity} > {persistence.original_quantity}"
        )
    expected_remaining = persistence.original_quantity - persistence.completed_trade_quantity
    if persistence.remaining_quantity != expected_remaining:
        raise TradingProbeError(
            "time-expiry race quantity mismatch: "
            f"remaining={persistence.remaining_quantity}, expected={expected_remaining}"
        )
    if persistence.offer_status not in {OfferStatus.COMPLETED.value, OfferStatus.EXPIRED.value}:
        raise TradingProbeError(f"time-expiry race expected terminal completed/expired offer, got {persistence.offer_status}")
    if persistence.offer_status == OfferStatus.COMPLETED.value and persistence.remaining_quantity != 0:
        raise TradingProbeError(
            f"time-expiry race completed offer still has remaining_quantity={persistence.remaining_quantity}"
        )
    if persistence.completed_ledger_count < persistence.persisted_trade_count:
        raise TradingProbeError("time-expiry race has persisted trades without completed request ledger rows")
    if persistence.trades_without_completed_ledger_count:
        raise TradingProbeError("time-expiry race has persisted trades without successful request ledger rows")
    if persistence.failed_internal_ledger_count:
        raise TradingProbeError(
            f"time-expiry race expected zero failed_internal ledgers, got {persistence.failed_internal_ledger_count}"
        )


def assert_read_during_write_acceptance(
    *,
    read_results: list[Mapping[str, Any]],
    expected_read_count: int,
) -> None:
    if not read_results:
        raise TradingProbeError("read-during-write expected read result artifacts")
    observed_surfaces = {str(result.get("read_surface") or "") for result in read_results}
    missing_surfaces = {"telegram", "webapp"} - observed_surfaces
    if missing_surfaces:
        raise TradingProbeError(f"read-during-write missing read surfaces: {sorted(missing_surfaces)}")
    for result in read_results:
        surface = str(result.get("read_surface") or "unknown")
        status_value = str(result.get("status") or "unknown")
        if status_value != "ok":
            raise TradingProbeError(f"read-during-write {surface} reader failed with status={status_value}")
        summary = _require_mapping(result.get("summary"), f"read-during-write {surface} summary")
        total = int(summary.get("total") or 0)
        errors = int(summary.get("error") or 0)
        if total < expected_read_count:
            raise TradingProbeError(
                f"read-during-write {surface} expected at least {expected_read_count} reads, got {total}"
            )
        if errors:
            raise TradingProbeError(f"read-during-write {surface} expected zero read errors, got {errors}")
        operation_counts = dict(summary.get("operation_counts") or {})
        if surface == "webapp":
            required = {"active_offers", "public_detail", "market_history"}
        elif surface == "telegram":
            required = {"telegram_market_view"}
        else:
            raise TradingProbeError(f"read-during-write got unsupported surface={surface!r}")
        missing_operations = sorted(operation for operation in required if int(operation_counts.get(operation) or 0) <= 0)
        if missing_operations:
            raise TradingProbeError(
                f"read-during-write {surface} did not exercise operations: {missing_operations}"
            )


async def run_duplicate_replay_probe(
    *,
    prefix: str,
    users: list[LoadUserRef],
    commodity_id: int,
    commodity_name: str,
    price: int,
    offer_type: str,
) -> dict[str, Any]:
    if len(users) < 4:
        raise TradingProbeError("duplicate/replay probe requires at least four synthetic users")

    web_owner = users[0]
    web_responder = users[2]
    web_offer_id = await create_offer_for_user(
        user_id=web_owner.user_id,
        commodity_id=commodity_id,
        prefix=f"{prefix}duplicate-web-",
        index=9800,
        offer_type=offer_type,
        quantity=5,
        price=price,
    )
    duplicate_key = f"{prefix}duplicate-web-key"
    web_statuses = await asyncio.gather(
        execute_webapp_trade_for_user(
            user_id=web_responder.user_id,
            offer_id=web_offer_id,
            quantity=5,
            idempotency_key=duplicate_key,
        ),
        execute_webapp_trade_for_user(
            user_id=web_responder.user_id,
            offer_id=web_offer_id,
            quantity=5,
            idempotency_key=duplicate_key,
        ),
    )
    web_persistence = await inspect_hot_offer_persistence(web_offer_id)
    assert_duplicate_replay_acceptance(statuses=list(web_statuses), persistence=web_persistence)

    bot_owner = users[1]
    bot_responder = users[3]
    bot_harness = AiogramDispatcherHarness()
    try:
        bot_offer_id = await create_bot_offer_with_dispatcher(
            harness=bot_harness,
            owner=bot_owner,
            commodity_name=commodity_name,
            prefix=f"{prefix}duplicate-bot-",
            quantity=5,
            price=price,
            offer_type=offer_type,
        )
        bot_offer = await load_offer_snapshot(bot_offer_id)
        bot_spec = MixedLoadAttemptSpec(
            index=0,
            surface="telegram",
            user_id=bot_responder.user_id,
            telegram_id=bot_responder.telegram_id,
        )
        first_bot_status = await execute_bot_trade_with_dispatcher(
            harness=bot_harness,
            spec=bot_spec,
            offer=bot_offer,
            amount=5,
            prefix=f"{prefix}duplicate-bot-first-",
        )
        second_bot_status = await execute_bot_trade_with_dispatcher(
            harness=bot_harness,
            spec=bot_spec,
            offer=await load_offer_snapshot(bot_offer_id),
            amount=5,
            prefix=f"{prefix}duplicate-bot-replay-",
        )
    finally:
        await bot_harness.close()
    bot_persistence = await inspect_hot_offer_persistence(bot_offer_id)
    assert_duplicate_replay_acceptance(
        statuses=[first_bot_status, second_bot_status],
        persistence=bot_persistence,
    )

    return {
        "webapp_repeated_idempotency_key": {
            "offer_id": web_offer_id,
            "statuses": list(web_statuses),
            "persistence": asdict(web_persistence),
        },
        "telegram_duplicate_callbacks": {
            "offer_id": bot_offer_id,
            "statuses": [first_bot_status, second_bot_status],
            "persistence": asdict(bot_persistence),
        },
    }


async def run_notification_fanout(*, user_ids: list[int], prefix: str, iterations: int) -> dict[str, Any]:
    samples: list[float] = []
    created = 0
    for index in range(iterations):
        async def _create_batch() -> int:
            async with AsyncSessionLocal() as db:
                count = 0
                for user_id in user_ids:
                    await create_user_notification(
                        db,
                        user_id,
                        f"{prefix} notification fanout {index}",
                        level=NotificationLevel.SUCCESS,
                        category=NotificationCategory.TRADE,
                        extra_payload={"benchmark": "p7", "prefix": prefix},
                    )
                    count += 1
                return count

        count, duration_ms = await timed_ms(_create_batch)
        created += int(count)
        samples.append(duration_ms)
    return {"created": created, "latency": summarize_samples(samples)}


async def run_race_probe(
    *,
    prefix: str,
    fixture: FixtureUsers,
    commodity_id: int,
    concurrency: int,
) -> dict[str, Any]:
    offer_id = await create_offer_for_user(
        user_id=fixture.seller_id,
        commodity_id=commodity_id,
        prefix=prefix,
        index=900,
        offer_type="sell",
        quantity=5,
        price=100000,
    )

    async def _attempt(index: int) -> dict[str, Any]:
        responder_id = fixture.responder_a_id if index % 2 == 0 else fixture.responder_b_id
        started = time.perf_counter()
        try:
            response = await execute_trade_for_user(
                user_id=responder_id,
                offer_id=offer_id,
                quantity=5,
                idempotency_key=f"{prefix}race-{index}",
            )
            return {
                "index": index,
                "status": "success",
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "trade_id": getattr(response, "id", None),
            }
        except HTTPException as exc:
            return {
                "index": index,
                "status": "rejected",
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "status_code": exc.status_code,
            }
        except Exception as exc:
            return {
                "index": index,
                "status": "error",
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "error_type": type(exc).__name__,
            }

    attempts = await asyncio.gather(*[_attempt(index) for index in range(concurrency)])
    winner_count = sum(1 for item in attempts if item["status"] == "success")
    error_count = sum(1 for item in attempts if item["status"] == "error")
    async with AsyncSessionLocal() as db:
        trade_count = int(await db.scalar(select(func.count(Trade.id)).where(Trade.offer_id == offer_id)) or 0)
        offer = await db.get(Offer, offer_id)
        remaining_quantity = int(offer.remaining_quantity or 0) if offer else None
        status_value = getattr(getattr(offer, "status", None), "value", None)

    assert_race_acceptance(
        winner_count=winner_count,
        trade_count=trade_count,
        remaining_quantity=remaining_quantity,
        status=status_value,
        error_count=error_count,
    )
    return {
        "offer_id": offer_id,
        "attempts": attempts,
        "winner_count": winner_count,
        "persisted_trade_count": trade_count,
        "offer_remaining_quantity": remaining_quantity,
        "offer_status": status_value,
        "latency": summarize_samples([float(item["duration_ms"]) for item in attempts]),
    }


async def run_benchmark(args: argparse.Namespace) -> int:
    setup_event_listeners()
    prefix = args.prefix
    await cleanup_prefix(prefix)
    commodity_id, commodity_name = await resolve_commodity()
    async with patched_external_side_effects():
        fixture = await create_fixture_users(prefix)
        parse_samples: list[float] = []
        bot_samples: list[float] = []
        create_samples: list[float] = []
        expire_samples: list[float] = []
        list_samples: list[float] = []
        trade_samples: list[float] = []
        created_offer_ids: list[int] = []

        parse_text = f"ف {commodity_name} {args.quantity} عدد {args.price}: {prefix} parser"
        for _ in range(args.parse_iterations):
            async def _parse() -> Any:
                return await parse_offer_text(parse_text)

            (parsed, error), duration_ms = await timed_ms(_parse)
            if error or parsed is None:
                raise TradingProbeError(f"parser failed: {getattr(error, 'message', None)}")
            parse_samples.append(duration_ms)

        for _ in range(args.bot_iterations):
            result, duration_ms = await timed_ms(
                lambda: run_bot_text_handler_probe(user_id=fixture.seller_id, text_value=parse_text)
            )
            if not result["state_set"]:
                raise TradingProbeError(f"bot text handler did not set confirmation state: {result}")
            bot_samples.append(duration_ms)

        for index in range(args.create_iterations):
            offer_id, duration_ms = await timed_ms(
                lambda index=index: create_offer_for_user(
                    user_id=fixture.seller_id,
                    commodity_id=commodity_id,
                    prefix=prefix,
                    index=index,
                    offer_type="sell",
                    quantity=args.quantity,
                    price=args.price,
                )
            )
            created_offer_ids.append(int(offer_id))
            create_samples.append(duration_ms)

        for _ in range(args.list_iterations):
            _count, duration_ms = await timed_ms(lambda: list_active_offers_for_user(user_id=fixture.responder_a_id))
            list_samples.append(duration_ms)

        for offer_id in created_offer_ids[: args.expire_iterations]:
            _result, duration_ms = await timed_ms(
                lambda offer_id=offer_id: expire_offer_for_user(user_id=fixture.seller_id, offer_id=offer_id)
            )
            expire_samples.append(duration_ms)

        for index in range(args.trade_iterations):
            trade_offer_id = await create_offer_for_user(
                user_id=fixture.seller_id,
                commodity_id=commodity_id,
                prefix=prefix,
                index=800 + index,
                offer_type="sell",
                quantity=args.quantity,
                price=args.price,
            )
            _trade_response, trade_duration = await timed_ms(
                lambda index=index, trade_offer_id=trade_offer_id: execute_trade_for_user(
                    user_id=fixture.responder_a_id,
                    offer_id=trade_offer_id,
                    quantity=args.quantity,
                    idempotency_key=f"{prefix}single-trade-{index}",
                )
            )
            trade_samples.append(trade_duration)

        notification_report = await run_notification_fanout(
            user_ids=fixture.ids,
            prefix=prefix,
            iterations=args.notification_iterations,
        )
        race_report = await run_race_probe(
            prefix=prefix,
            fixture=fixture,
            commodity_id=commodity_id,
            concurrency=args.race_concurrency,
        )

    async with AsyncSessionLocal() as db:
        synthetic_notification_count = int(
            await db.scalar(select(func.count(Notification.id)).where(Notification.user_id.in_(fixture.ids))) or 0
        )
        synthetic_trade_count = int(
            await db.scalar(
                select(func.count(Trade.id)).where(
                    (Trade.offer_user_id.in_(fixture.ids))
                    | (Trade.responder_user_id.in_(fixture.ids))
                    | (Trade.actor_user_id.in_(fixture.ids))
                )
            )
            or 0
        )
        synthetic_offer_count = int(
            await db.scalar(select(func.count(Offer.id)).where(Offer.user_id.in_(fixture.ids))) or 0
        )
        unsynced_count = int(await db.scalar(select(func.count(ChangeLog.id)).where(ChangeLog.synced == False)) or 0)

    payload = {
        "status": "ok",
        "server_mode": settings.server_mode,
        "prefix": prefix,
        "commodity_id": commodity_id,
        "commodity_name": commodity_name,
        "fixture_user_ids": fixture.ids,
        "operations": {
            "bot_text_offer_handler": summarize_samples(bot_samples),
            "parse_offer": summarize_samples(parse_samples),
            "offer_create": summarize_samples(create_samples),
            "offer_list": summarize_samples(list_samples),
            "offer_expire": summarize_samples(expire_samples),
            "trade_execute": summarize_samples(trade_samples),
            "notification_fanout": notification_report["latency"],
        },
        "notification_fanout": notification_report,
        "race": race_report,
        "synthetic_counts_before_cleanup": {
            "offers": synthetic_offer_count,
            "trades": synthetic_trade_count,
            "notifications": synthetic_notification_count,
            "unsynced_change_logs": unsynced_count,
        },
    }
    print_json(payload)
    return 0


async def run_mixed_load_benchmark(args: argparse.Namespace) -> int:
    setup_event_listeners()
    prefix = args.prefix
    await cleanup_prefix(prefix)
    commodity_id, commodity_name = await resolve_commodity()
    origins = ["webapp", "bot"] if args.offer_origin == "both" else [args.offer_origin]
    reports: dict[str, Any] = {}

    async with patched_external_side_effects():
        users = await create_load_fixture_users(prefix, user_count=args.user_count)
        if len(users) < 3:
            raise TradingProbeError("mixed load fixture did not create enough users")

        if "webapp" in origins:
            web_owner = users[0]
            web_offer_id = await create_offer_for_user(
                user_id=web_owner.user_id,
                commodity_id=commodity_id,
                prefix=prefix,
                index=9100,
                offer_type=args.offer_type,
                quantity=args.hot_offer_quantity,
                price=args.price,
            )
            reports["webapp_hot_offer"] = await run_hot_offer_contention(
                prefix=f"{prefix}webapp-",
                offer_id=web_offer_id,
                owner_user_id=web_owner.user_id,
                users=users,
                total_requests=args.hot_offer_requests,
                telegram_ratio=args.telegram_ratio,
                target_rps=args.target_rps,
                amount=args.request_amount,
                expected_winner_count=args.expected_winner_count,
            )

        if "bot" in origins:
            bot_owner = users[1]
            bot_harness = AiogramDispatcherHarness()
            try:
                bot_offer_id = await create_bot_offer_with_dispatcher(
                    harness=bot_harness,
                    owner=bot_owner,
                    commodity_name=commodity_name,
                    prefix=prefix,
                    quantity=args.hot_offer_quantity,
                    price=args.price,
                    offer_type=args.offer_type,
                )
            finally:
                await bot_harness.close()
            reports["bot_hot_offer"] = await run_hot_offer_contention(
                prefix=f"{prefix}bot-",
                offer_id=bot_offer_id,
                owner_user_id=bot_owner.user_id,
                users=users,
                total_requests=args.hot_offer_requests,
                telegram_ratio=args.telegram_ratio,
                target_rps=args.target_rps,
                amount=args.request_amount,
                expected_winner_count=args.expected_winner_count,
            )

    async with AsyncSessionLocal() as db:
        fixture_user_ids = [user.user_id for user in users]
        synthetic_trade_count = int(
            await db.scalar(
                select(func.count(Trade.id)).where(
                    (Trade.offer_user_id.in_(fixture_user_ids))
                    | (Trade.responder_user_id.in_(fixture_user_ids))
                    | (Trade.actor_user_id.in_(fixture_user_ids))
                )
            )
            or 0
        )
        synthetic_offer_count = int(
            await db.scalar(select(func.count(Offer.id)).where(Offer.user_id.in_(fixture_user_ids))) or 0
        )
        unsynced_count = int(await db.scalar(select(func.count(ChangeLog.id)).where(ChangeLog.synced == False)) or 0)

    payload = {
        "status": "ok",
        "server_mode": settings.server_mode,
        "prefix": prefix,
        "commodity_id": commodity_id,
        "commodity_name": commodity_name,
        "offer_origin": args.offer_origin,
        "user_count": args.user_count,
        "telegram_ratio": args.telegram_ratio,
        "target_rps": args.target_rps,
        "hot_offer_requests": args.hot_offer_requests,
        "hot_offer_quantity": args.hot_offer_quantity,
        "request_amount": args.request_amount,
        "expected_winner_count": args.expected_winner_count,
        "fixture_user_count": len(users),
        "reports": reports,
        "synthetic_counts_before_cleanup": {
            "offers": synthetic_offer_count,
            "trades": synthetic_trade_count,
            "unsynced_change_logs": unsynced_count,
        },
    }
    print_json(payload)
    return 0


async def run_hot_offer_scenarios_command(args: argparse.Namespace) -> int:
    setup_event_listeners()
    prefix = args.prefix
    await cleanup_prefix(prefix)
    commodity_id, commodity_name = await resolve_commodity()
    selected = set(args.scenario or [])
    all_scenarios = build_hot_offer_scenario_specs(
        total_requests=args.hot_offer_requests,
        telegram_ratio=args.telegram_ratio,
        target_rps=args.target_rps,
        price=args.price,
        offer_type=args.offer_type,
    )
    known_names = {scenario.name for scenario in all_scenarios} | {"duplicate_replay"}
    unknown = sorted(selected - known_names)
    if unknown:
        raise TradingProbeError(f"unknown hot-offer scenario(s): {', '.join(unknown)}")

    reports: dict[str, Any] = {}
    async with patched_trading_boundaries():
        users = await create_load_fixture_users(prefix, user_count=args.user_count)
        if len(users) < 4:
            raise TradingProbeError("hot-offer scenarios require at least four synthetic users")
        for index, scenario in enumerate(all_scenarios, start=9700):
            if selected and scenario.name not in selected:
                continue
            reports[scenario.name] = await run_hot_offer_scenario(
                prefix=prefix,
                scenario=scenario,
                users=users,
                commodity_id=commodity_id,
                commodity_name=commodity_name,
                index=index,
            )
        if not selected or "duplicate_replay" in selected:
            reports["duplicate_replay"] = await run_duplicate_replay_probe(
                prefix=prefix,
                users=users,
                commodity_id=commodity_id,
                commodity_name=commodity_name,
                price=args.price,
                offer_type=args.offer_type,
            )

    async with AsyncSessionLocal() as db:
        fixture_user_ids = [user.user_id for user in users]
        synthetic_trade_count = int(
            await db.scalar(
                select(func.count(Trade.id)).where(
                    (Trade.offer_user_id.in_(fixture_user_ids))
                    | (Trade.responder_user_id.in_(fixture_user_ids))
                    | (Trade.actor_user_id.in_(fixture_user_ids))
                )
            )
            or 0
        )
        synthetic_offer_count = int(
            await db.scalar(select(func.count(Offer.id)).where(Offer.user_id.in_(fixture_user_ids))) or 0
        )
        synthetic_offer_request_count = int(
            await db.scalar(
                select(func.count(OfferRequest.id)).where(
                    (OfferRequest.requester_user_id.in_(fixture_user_ids))
                    | (OfferRequest.actor_user_id.in_(fixture_user_ids))
                )
            )
            or 0
        )
        unsynced_count = int(await db.scalar(select(func.count(ChangeLog.id)).where(ChangeLog.synced == False)) or 0)

    payload = {
        "status": "ok",
        "server_mode": settings.server_mode,
        "prefix": prefix,
        "commodity_id": commodity_id,
        "commodity_name": commodity_name,
        "user_count": args.user_count,
        "telegram_ratio": args.telegram_ratio,
        "target_rps": args.target_rps,
        "hot_offer_requests": args.hot_offer_requests,
        "scenario_count": len(reports),
        "reports": reports,
        "synthetic_counts_before_cleanup": {
            "offers": synthetic_offer_count,
            "trades": synthetic_trade_count,
            "offer_requests": synthetic_offer_request_count,
            "unsynced_change_logs": unsynced_count,
        },
    }
    print_json(payload)
    return 0


async def cleanup_command(args: argparse.Namespace) -> int:
    if not args.dry_run:
        allow_production_cleanup_hard_delete(
            args.prefix,
            allow_flag=bool(getattr(args, "allow_production_hard_delete", False)),
        )
    payload = await cleanup_prefix(args.prefix, dry_run=bool(args.dry_run))
    if args.artifact:
        write_json_artifact(Path(args.artifact), payload)
    print_json(payload)
    return 0


async def load_runner_ready_command(args: argparse.Namespace) -> int:
    print_json(assert_load_runner_runtime_surface(args.role))
    return 0


async def run_negative_guard_case_command(args: argparse.Namespace) -> int:
    payload = await run_negative_guard_case(
        prefix=args.prefix,
        case_id=args.case_id,
        allow_production_execution=bool(args.allow_production_execution),
        skip_initial_cleanup=bool(args.skip_initial_cleanup),
        allow_production_cleanup=bool(args.allow_production_cleanup),
    )
    if args.output:
        write_json_artifact(Path(args.output), payload)
    print_json(payload)
    return 0 if payload["status"] == "passed" else 1


async def run_unsupported_policy_case_command(args: argparse.Namespace) -> int:
    payload = await run_unsupported_policy_case(
        prefix=args.prefix,
        actor_pair_id=args.actor_pair_id,
        source_kind=args.source_kind,
        responder_kind=args.responder_kind,
        group_relation=args.group_relation,
        offer_surface=args.offer_surface,
        request_surface=args.request_surface,
        offer_type=args.offer_type,
        quantity=int(args.quantity),
        request_amount=int(args.request_amount),
        price=int(args.price),
        is_wholesale=not bool(args.retail),
        lot_sizes=parse_lot_sizes_argument(args.lot_sizes),
        unsupported_reasons=list(args.unsupported_reason or []),
        allow_production_execution=bool(args.allow_production_execution),
        skip_initial_cleanup=bool(args.skip_initial_cleanup),
        allow_production_cleanup=bool(args.allow_production_cleanup),
    )
    if args.output:
        write_json_artifact(Path(args.output), payload)
    print_json(payload)
    return 0 if payload["status"] == "passed" else 1


async def prepare_dual_role_run_command(args: argparse.Namespace) -> int:
    setup_event_listeners()
    prefix = args.prefix
    assert_dual_role_prepare_runtime(
        str(args.offer_origin),
        allow_production=bool(args.allow_production_execution),
        prefix=prefix,
    )
    if not bool(args.skip_initial_cleanup):
        if is_production_runtime():
            allow_production_cleanup_hard_delete(
                prefix,
                allow_flag=bool(args.allow_production_cleanup),
            )
        await cleanup_prefix(prefix)
    commodity_id, commodity_name = await resolve_commodity()
    offer_origin = str(args.offer_origin)
    run_id = _dual_role_run_id(prefix, getattr(args, "run_id", None))
    topology = "single-db staging role-worker smoke"
    is_wholesale = not bool(args.retail)
    lot_sizes = parse_lot_sizes_argument(args.lot_sizes)
    request_surface = None if args.request_surface == "mixed" else str(args.request_surface)
    expected_remaining_quantity = (
        int(args.expected_remaining_quantity)
        if args.expected_remaining_quantity is not None
        else max(0, int(args.hot_offer_quantity) - int(args.request_amount) * int(args.expected_winner_count))
    )
    require_terminal_completed = bool(args.require_terminal_completed)
    barrier_epoch = time.time() + args.barrier_delay_seconds
    is_time_expiry_race = str(args.scenario_name or "") == "time_expire_trade_race"
    time_expiry_epoch = None
    time_expiry_stale_epoch = None
    time_expiry_schedule = None
    if is_time_expiry_race:
        time_expiry_epoch = barrier_epoch + float(args.time_expiry_race_delay_seconds)
        time_expiry_stale_epoch = time_expiry_epoch - float(args.time_expiry_race_stale_skew_seconds)

    async with patched_trading_boundaries():
        users = await create_load_fixture_users(prefix, user_count=args.user_count)
        owner = users[1] if offer_origin == "bot" else users[0]
        if offer_origin == "bot":
            harness = AiogramDispatcherHarness()
            try:
                offer_id = await create_bot_offer_with_dispatcher(
                    harness=harness,
                    owner=owner,
                    commodity_name=commodity_name,
                    prefix=prefix,
                    quantity=args.hot_offer_quantity,
                    price=args.price,
                    offer_type=args.offer_type,
                    is_wholesale=is_wholesale,
                    lot_sizes=lot_sizes,
                )
            finally:
                await harness.close()
        else:
            offer_id = await create_offer_for_user(
                user_id=owner.user_id,
                commodity_id=commodity_id,
                prefix=prefix,
                index=9900,
                offer_type=args.offer_type,
                quantity=args.hot_offer_quantity,
                price=args.price,
                is_wholesale=is_wholesale,
                lot_sizes=lot_sizes,
            )

        if is_time_expiry_race:
            if time_expiry_stale_epoch is None:
                raise TradingProbeError("time-expiry race scheduling failed to compute stale epoch")
            time_expiry_schedule = await schedule_offer_for_time_expiry_race(
                offer_id=offer_id,
                stale_epoch=time_expiry_stale_epoch,
            )

    offer = await load_offer_snapshot(offer_id)
    plans = build_dual_role_worker_plans(
        run_id=run_id,
        prefix=prefix,
        users=users,
        owner_user_id=owner.user_id,
        offer_id=offer_id,
        offer_public_id=offer_public_identity(offer),
        total_requests=args.hot_offer_requests,
        telegram_ratio=args.telegram_ratio,
        target_rps=args.target_rps,
        amount=args.request_amount,
        barrier_epoch=barrier_epoch,
        request_surface=request_surface,
        idempotency_mode=args.idempotency_mode,
    )
    output_dir = Path(args.output_dir)
    plan_result = _write_dual_role_plan_files(output_dir, plans, mode="staging_dual_role_run")
    prepare = build_dual_role_prepare_artifact(
        run_id=run_id,
        prefix=prefix,
        topology=topology,
        scenario_name=args.scenario_name,
        offer_origin=offer_origin,
        telegram_gateway_boundary="mock",
        commodity_id=commodity_id,
        commodity_name=commodity_name,
        users=users,
        owner_user_id=owner.user_id,
        offer=offer,
        hot_offer_requests=args.hot_offer_requests,
        telegram_ratio=args.telegram_ratio,
        target_rps=args.target_rps,
        hot_offer_quantity=args.hot_offer_quantity,
        hot_offer_is_wholesale=is_wholesale,
        hot_offer_lot_sizes=lot_sizes,
        request_amount=args.request_amount,
        expected_winner_count=args.expected_winner_count,
        expected_remaining_quantity=expected_remaining_quantity,
        require_terminal_completed=require_terminal_completed,
        request_surface=args.request_surface,
        idempotency_mode=args.idempotency_mode,
        barrier_epoch=barrier_epoch,
        time_expiry_epoch=time_expiry_epoch,
        time_expiry_stale_epoch=time_expiry_stale_epoch,
        time_expiry_schedule=time_expiry_schedule,
        plan_result=plan_result,
    )
    prepare_path = output_dir / "prepare.json"
    write_json_artifact(prepare_path, prepare)
    print_json(
        {
            "status": "ok",
            "prepare_path": str(prepare_path),
            "manifest_path": plan_result["manifest_path"],
            "plan_paths": prepare["plan_paths"],
            "offer": prepare["offer"],
            "scenario": prepare["scenario"],
        }
    )
    return 0


async def run_manual_expiry_race_command(args: argparse.Namespace) -> int:
    prepare = read_json_artifact(Path(args.prepare))
    prefix = str(prepare.get("prefix") or "")
    scenario = _require_mapping(prepare.get("scenario"), "prepare scenario")
    offer = _require_mapping(prepare.get("offer"), "prepare offer")
    offer_origin = str(scenario.get("offer_origin") or "").strip().lower()
    assert_dual_role_prepare_runtime(
        offer_origin,
        allow_production=bool(args.allow_production_execution),
        prefix=prefix,
    )
    offer_id = int(offer["id"])
    owner_user_id = int(offer["owner_user_id"])
    barrier_epoch_value = prepare.get("barrier_epoch")
    if barrier_epoch_value is None:
        raise TradingProbeError("manual-expiry race requires barrier_epoch in prepare artifact")
    barrier_epoch = float(barrier_epoch_value)
    if not math.isfinite(barrier_epoch):
        raise TradingProbeError(f"manual-expiry race got invalid barrier_epoch={barrier_epoch_value!r}")

    start_delay = barrier_epoch - time.time()
    if start_delay > 0:
        await asyncio.sleep(start_delay)

    started_epoch = time.time()
    started_monotonic = time.perf_counter()
    status = "error"
    detail = ""
    error_details: list[str] = []
    async with patched_external_side_effects():
        if offer_origin == "bot":
            async with AsyncSessionLocal() as db:
                owner_user = await load_user(db, owner_user_id)
                telegram_id = getattr(owner_user, "telegram_id", None)
                if telegram_id is None:
                    raise TradingProbeError(f"bot offer owner {owner_user_id} has no telegram_id")
                owner = LoadUserRef(user_id=owner_user_id, telegram_id=int(telegram_id))
            harness = AiogramDispatcherHarness()
            try:
                status = await expire_bot_offer_with_dispatcher(
                    harness=harness,
                    owner=owner,
                    offer_id=offer_id,
                    prefix=prefix,
                    index=0,
                    error_details=error_details,
                )
            finally:
                await harness.close()
        elif offer_origin == "webapp":
            try:
                await expire_offer_for_user(user_id=owner_user_id, offer_id=offer_id)
                status = "success"
            except HTTPException as exc:
                detail = str(exc.detail)
                status = "error" if int(exc.status_code) >= 500 else "rejected"
                if status == "error":
                    error_details.append(f"HTTPException {exc.status_code}: {detail}")
            except Exception as exc:
                status = "error"
                detail = f"{type(exc).__name__}: {exc}"
                error_details.append(detail)
        else:
            raise TradingProbeError(f"unsupported manual-expiry race offer_origin={offer_origin!r}")

    payload = {
        "schema_version": MANUAL_EXPIRY_RACE_RESULT_SCHEMA_VERSION,
        "status": status,
        "offer_origin": offer_origin,
        "offer_id": offer_id,
        "owner_user_id": owner_user_id,
        "prefix": prefix,
        "barrier_epoch": round(barrier_epoch, 6),
        "started_epoch": round(started_epoch, 6),
        "duration_ms": round((time.perf_counter() - started_monotonic) * 1000.0, 3),
        "detail": detail,
        "error_details": error_details,
    }
    if args.output:
        write_json_artifact(Path(args.output), payload)
    print_json(payload)
    return 0 if status in {"success", "rejected"} else 1


async def run_time_expiry_race_command(args: argparse.Namespace) -> int:
    prepare = read_json_artifact(Path(args.prepare))
    prefix = str(prepare.get("prefix") or "")
    scenario = _require_mapping(prepare.get("scenario"), "prepare scenario")
    offer = _require_mapping(prepare.get("offer"), "prepare offer")
    offer_origin = str(scenario.get("offer_origin") or "").strip().lower()
    assert_dual_role_prepare_runtime(
        offer_origin,
        allow_production=bool(args.allow_production_execution),
        prefix=prefix,
    )
    offer_id = int(offer["id"])
    owner_user_id = int(offer["owner_user_id"])
    expiry_epoch_value = prepare.get("time_expiry_epoch")
    stale_epoch_value = prepare.get("time_expiry_stale_epoch")
    if expiry_epoch_value is None or stale_epoch_value is None:
        raise TradingProbeError("time-expiry race requires time_expiry_epoch and time_expiry_stale_epoch")
    expiry_epoch = float(expiry_epoch_value)
    stale_epoch = float(stale_epoch_value)
    if not math.isfinite(expiry_epoch) or not math.isfinite(stale_epoch):
        raise TradingProbeError(
            f"time-expiry race got invalid epochs expiry={expiry_epoch_value!r} stale={stale_epoch_value!r}"
        )

    start_delay = expiry_epoch - time.time()
    if start_delay > 0:
        await asyncio.sleep(start_delay)

    started_epoch = time.time()
    started_monotonic = time.perf_counter()
    status = "error"
    detail = ""
    error_details: list[str] = []
    expired_count = 0
    async with patched_external_side_effects():
        try:
            expired_count = await expire_time_limit_offer_for_race(
                offer_id=offer_id,
                stale_epoch=stale_epoch,
            )
            status = "success" if expired_count > 0 else "rejected"
        except Exception as exc:
            status = "error"
            detail = f"{type(exc).__name__}: {exc}"
            error_details.append(detail)

    payload = {
        "schema_version": TIME_EXPIRY_RACE_RESULT_SCHEMA_VERSION,
        "status": status,
        "expired_count": int(expired_count),
        "offer_origin": offer_origin,
        "offer_id": offer_id,
        "owner_user_id": owner_user_id,
        "prefix": prefix,
        "expiry_epoch": round(expiry_epoch, 6),
        "stale_epoch": round(stale_epoch, 6),
        "started_epoch": round(started_epoch, 6),
        "duration_ms": round((time.perf_counter() - started_monotonic) * 1000.0, 3),
        "detail": detail,
        "error_details": error_details,
    }
    if args.output:
        write_json_artifact(Path(args.output), payload)
    print_json(payload)
    return 0 if status in {"success", "rejected"} else 1


async def run_read_during_write_command(args: argparse.Namespace) -> int:
    prepare = read_json_artifact(Path(args.prepare))
    prefix = str(prepare.get("prefix") or "")
    read_surface = str(args.read_surface or "").strip().lower()
    role = {"telegram": "telegram_foreign", "webapp": "webapp_iran"}.get(read_surface)
    if role is None:
        raise TradingProbeError(f"unsupported read-during-write surface={read_surface!r}")
    assert_load_runner_runtime_surface(
        role,
        allow_production=bool(args.allow_production_execution),
        prefix=prefix,
    )

    scenario = _require_mapping(prepare.get("scenario"), "prepare scenario")
    offer = _require_mapping(prepare.get("offer"), "prepare offer")
    users_payload = _require_mapping(prepare.get("users"), "prepare users")
    barrier_epoch_value = prepare.get("barrier_epoch")
    if barrier_epoch_value is None:
        raise TradingProbeError("read-during-write requires barrier_epoch in prepare artifact")
    barrier_epoch = float(barrier_epoch_value)
    if not math.isfinite(barrier_epoch):
        raise TradingProbeError(f"read-during-write got invalid barrier_epoch={barrier_epoch_value!r}")
    target_rps = float(scenario.get("target_rps") or 1.0)
    if target_rps <= 0:
        raise TradingProbeError(f"read-during-write got invalid target_rps={target_rps!r}")
    total_reads = int(scenario.get("hot_offer_requests") or 0)
    if total_reads <= 0:
        raise TradingProbeError("read-during-write requires positive hot_offer_requests")
    owner_user_id = int(users_payload.get("owner_user_id") or offer.get("owner_user_id") or 0)
    reader_user_ids = [
        int(user_id)
        for user_id in (users_payload.get("user_ids") or [])
        if int(user_id) != owner_user_id
    ]
    if not reader_user_ids:
        raise TradingProbeError("read-during-write requires at least one non-owner reader")
    offer_public_id = str(offer.get("public_id") or "").strip()
    if read_surface == "webapp" and not offer_public_id:
        raise TradingProbeError("read-during-write WebApp detail read requires offer public id")

    await warm_load_runner_dependencies(db_connections=min(total_reads, 12))
    telegram_readers: list[LoadUserRef] = []
    harness: AiogramDispatcherHarness | None = None
    if read_surface == "telegram":
        telegram_readers = [await load_user_ref(user_id) for user_id in reader_user_ids]
        harness = AiogramDispatcherHarness()

    start_delay = barrier_epoch - time.time()
    if start_delay > 0:
        await asyncio.sleep(start_delay)
    started_epoch = time.time()
    started_monotonic = time.perf_counter()

    async def _read_attempt(index: int) -> dict[str, Any]:
        scheduled_epoch = barrier_epoch + (index / target_rps)
        scheduled_delay = scheduled_epoch - time.time()
        if scheduled_delay > 0:
            await asyncio.sleep(scheduled_delay)
        attempt_started = time.perf_counter()
        operation = "telegram_market_view"
        status_value = "error"
        detail = ""
        row_count: int | None = None
        reader_user_id = reader_user_ids[index % len(reader_user_ids)]
        try:
            if read_surface == "telegram":
                if harness is None:
                    raise TradingProbeError("telegram read harness was not initialized")
                reader = telegram_readers[index % len(telegram_readers)]
                status_value = await execute_bot_market_view_with_dispatcher(harness=harness, user=reader)
                reader_user_id = reader.user_id
            else:
                operation = ("active_offers", "public_detail", "market_history")[index % 3]
                if operation == "active_offers":
                    row_count = await list_active_offers_for_user(user_id=reader_user_id)
                elif operation == "public_detail":
                    row_count = await load_public_offer_detail_for_user(
                        user_id=reader_user_id,
                        offer_public_id=offer_public_id,
                    )
                else:
                    row_count = await list_market_history_for_user(user_id=reader_user_id)
                status_value = "success"
        except Exception as exc:
            status_value = "error"
            detail = f"{type(exc).__name__}: {exc}"
        return {
            "index": index,
            "read_surface": read_surface,
            "operation": operation,
            "reader_user_id": reader_user_id,
            "status": status_value,
            "detail": detail,
            "row_count": row_count,
            "latency_ms": round((time.perf_counter() - attempt_started) * 1000.0, 3),
            "monotonic_timestamp": round(time.perf_counter(), 6),
        }

    async with patched_external_side_effects():
        try:
            attempts = await asyncio.gather(*[_read_attempt(index) for index in range(total_reads)])
        finally:
            if harness is not None:
                await harness.close()

    elapsed_seconds = max(time.perf_counter() - started_monotonic, 0.000001)
    status_counts = Counter(str(attempt.get("status") or "unknown") for attempt in attempts)
    operation_counts = Counter(str(attempt.get("operation") or "unknown") for attempt in attempts)
    error_details = sorted({str(attempt.get("detail") or "") for attempt in attempts if attempt.get("status") == "error"})
    error_details = [detail for detail in error_details if detail]
    payload = {
        "schema_version": READ_DURING_WRITE_RESULT_SCHEMA_VERSION,
        "status": "ok" if int(status_counts.get("error") or 0) == 0 else "failed",
        "prefix": prefix,
        "read_surface": read_surface,
        "offer_id": int(offer["id"]),
        "offer_public_id": offer_public_id or None,
        "barrier_epoch": round(barrier_epoch, 6),
        "started_epoch": round(started_epoch, 6),
        "elapsed_seconds": round(elapsed_seconds, 6),
        "summary": {
            "total": len(attempts),
            "success": int(status_counts.get("success") or 0),
            "rejected": int(status_counts.get("rejected") or 0),
            "error": int(status_counts.get("error") or 0),
            "read_rps": round(len(attempts) / elapsed_seconds, 3),
            "operation_counts": dict(sorted(operation_counts.items())),
            "error_details": error_details,
        },
        "attempts": attempts,
    }
    if args.output:
        write_json_artifact(Path(args.output), payload)
    print_json(payload)
    return 0 if payload["status"] == "ok" else 1


async def finalize_dual_role_run_command(args: argparse.Namespace) -> int:
    prepare = read_json_artifact(Path(args.prepare))
    merged_result = read_json_artifact(Path(args.merged_result))
    offer = _require_mapping(prepare.get("offer"), "prepare offer")
    persistence = await inspect_hot_offer_persistence(int(offer["id"]))
    manual_expiry_result = read_json_artifact(Path(args.manual_expiry_result)) if args.manual_expiry_result else None
    time_expiry_result = read_json_artifact(Path(args.time_expiry_result)) if args.time_expiry_result else None
    read_during_write_results = [
        read_json_artifact(Path(path))
        for path in (args.read_during_write_result or [])
    ]
    report = build_dual_role_final_report(
        prepare=prepare,
        merged_result=merged_result,
        persistence=persistence,
        manual_expiry_result=manual_expiry_result,
        time_expiry_result=time_expiry_result,
        read_during_write_results=read_during_write_results,
    )
    if args.output:
        write_json_artifact(Path(args.output), report)
    print_json(report)
    return 1 if args.check and report["status"] != "ok" else 0


async def observability_snapshot_command(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    redis_errors = 0
    redis_info: dict[str, Any] = {}
    try:
        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis_client.ping()
            redis_latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
            raw_info = await redis_client.info()
            redis_info = {
                "connected_clients": raw_info.get("connected_clients"),
                "total_error_replies": raw_info.get("total_error_replies"),
                "rejected_connections": raw_info.get("rejected_connections"),
            }
        finally:
            await redis_client.aclose()
    except Exception:
        redis_errors = 1
        redis_latency_ms = None

    async with AsyncSessionLocal() as db:
        max_connections = int((await db.execute(text("show max_connections"))).scalar_one())
        pg_states = {
            str(row[0] or "none"): int(row[1])
            for row in (
                await db.execute(
                    text("select state, count(*) from pg_stat_activity group by state order by state nulls last")
                )
            ).all()
        }
        unsynced_change_logs = int(
            await db.scalar(select(func.count(ChangeLog.id)).where(ChangeLog.synced == False)) or 0
        )
        retryable_publications = int(
            await db.scalar(
                select(func.count(OfferPublicationState.id)).where(
                    OfferPublicationState.status.in_(
                        [
                            OfferPublicationStatus.PENDING,
                            OfferPublicationStatus.FAILED,
                            OfferPublicationStatus.LAGGED,
                        ]
                    )
                )
            )
            or 0
        )

    payload = {
        "db_pool": {
            "process_pool_size": getattr(settings, "db_pool_size", None),
            "process_max_overflow": getattr(settings, "db_max_overflow", None),
            "postgres_max_connections": max_connections,
            "pg_stat_activity": pg_states,
        },
        "redis": {
            "ping_latency_ms": redis_latency_ms,
            "errors": redis_errors,
            **redis_info,
        },
        "sync": {
            "unsynced_change_log_count": unsynced_change_logs,
            "max_lag_seconds": None,
        },
        "worker_backlog": {
            "unsynced_change_logs": unsynced_change_logs,
            "retryable_publication_states": retryable_publications,
        },
    }
    if args.output:
        write_json_artifact(Path(args.output), payload)
    print_json(payload)
    return 0


def _dual_role_run_id(prefix: str, explicit_run_id: str | None = None) -> str:
    normalized = str(explicit_run_id or "").strip()
    if normalized:
        return normalized
    return f"{prefix}{int(time.time())}"


def _build_dual_role_plans_from_args(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    users = synthetic_load_user_refs(
        user_count=args.user_count,
        start_user_id=args.start_user_id,
        start_telegram_id=args.start_telegram_id,
    )
    return build_dual_role_worker_plans(
        run_id=_dual_role_run_id(args.prefix, getattr(args, "run_id", None)),
        prefix=args.prefix,
        users=users,
        owner_user_id=args.owner_user_id,
        offer_id=args.offer_id,
        offer_public_id=args.offer_public_id,
        total_requests=args.hot_offer_requests,
        telegram_ratio=args.telegram_ratio,
        target_rps=args.target_rps,
        amount=args.request_amount,
        barrier_epoch=time.time() + args.barrier_delay_seconds,
    )


def _write_dual_role_plan_files(output_dir: Path, plans: Mapping[str, Mapping[str, Any]], *, mode: str) -> dict[str, Any]:
    plan_paths: dict[str, str] = {}
    role_summaries: dict[str, Any] = {}
    for role, plan in plans.items():
        validated = validate_role_plan_artifact(plan)
        plan_path = output_dir / f"{role}.plan.json"
        write_json_artifact(plan_path, validated)
        plan_paths[role] = str(plan_path)
        role_summaries[role] = {
            "surface": validated["surface"],
            "attempts": len(validated["attempts"]),
            "server_mode": LOAD_RUNNER_ROLES[role]["server_mode"],
        }
    run_ids = {str(plan["run_id"]) for plan in plans.values()}
    if len(run_ids) != 1:
        raise TradingProbeError("dual-role plans must share one run id")
    manifest = {
        "schema_version": DUAL_ROLE_MANIFEST_SCHEMA_VERSION,
        "run_id": next(iter(run_ids)),
        "status": "ok",
        "mode": mode,
        "created_epoch": round(time.time(), 6),
        "plan_paths": plan_paths,
        "roles": role_summaries,
        "barrier_skew": assert_role_plan_barrier_skew(plans, max_skew_seconds=0.001),
    }
    manifest_path = output_dir / "manifest.json"
    write_json_artifact(manifest_path, manifest)
    return {"manifest_path": str(manifest_path), "manifest": manifest}


def dual_role_scenario_name(offer_origin: str) -> str:
    normalized = str(offer_origin or "").strip().lower()
    if normalized not in {"webapp", "bot"}:
        raise TradingProbeError(f"unsupported dual-role offer origin: {offer_origin}")
    return f"{normalized}_hot_offer"


def offer_public_identity(offer: Offer) -> str | None:
    return str(getattr(offer, "offer_public_id", "") or "").strip() or None


def build_dual_role_prepare_artifact(
    *,
    run_id: str,
    prefix: str,
    topology: str,
    scenario_name: str | None = None,
    offer_origin: str,
    telegram_gateway_boundary: str,
    commodity_id: int,
    commodity_name: str,
    users: list[LoadUserRef],
    owner_user_id: int,
    offer: Offer,
    hot_offer_requests: int,
    telegram_ratio: float,
    target_rps: float,
    hot_offer_quantity: int,
    hot_offer_is_wholesale: bool,
    hot_offer_lot_sizes: tuple[int, ...] | list[int],
    request_amount: int,
    expected_winner_count: int,
    expected_remaining_quantity: int = 0,
    require_terminal_completed: bool = True,
    request_surface: str = "mixed",
    idempotency_mode: str = "unique",
    barrier_epoch: float | None = None,
    time_expiry_epoch: float | None = None,
    time_expiry_stale_epoch: float | None = None,
    time_expiry_schedule: Mapping[str, Any] | None = None,
    plan_result: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": DUAL_ROLE_PREPARE_SCHEMA_VERSION,
        "status": "ok",
        "run_id": run_id,
        "prefix": prefix,
        "topology": topology,
        "telegram_gateway_boundary": telegram_gateway_boundary,
        "scenario": {
            "name": scenario_name or dual_role_scenario_name(offer_origin),
            "offer_origin": offer_origin,
            "request_surface": request_surface,
            "hot_offer_requests": int(hot_offer_requests),
            "telegram_ratio": float(telegram_ratio),
            "target_rps": float(target_rps),
            "hot_offer_quantity": int(hot_offer_quantity),
            "hot_offer_is_wholesale": bool(hot_offer_is_wholesale),
            "hot_offer_lot_sizes": [int(item) for item in hot_offer_lot_sizes],
            "request_amount": int(request_amount),
            "expected_winner_count": int(expected_winner_count),
            "expected_remaining_quantity": int(expected_remaining_quantity),
            "require_terminal_completed": bool(require_terminal_completed),
            "idempotency_mode": idempotency_mode,
        },
        "commodity": {
            "id": int(commodity_id),
            "name": commodity_name,
        },
        "users": {
            "count": len(users),
            "owner_user_id": int(owner_user_id),
            "user_ids": [int(user.user_id) for user in users],
        },
        "offer": {
            "id": int(offer.id),
            "public_id": offer_public_identity(offer),
            "owner_user_id": int(owner_user_id),
            "home_server": getattr(getattr(offer, "home_server", None), "value", getattr(offer, "home_server", None)),
            "status": getattr(getattr(offer, "status", None), "value", getattr(offer, "status", None)),
            "quantity": int(getattr(offer, "quantity", 0) or 0),
            "remaining_quantity": (
                int(offer.remaining_quantity) if getattr(offer, "remaining_quantity", None) is not None else None
            ),
        },
        "plan_manifest_path": plan_result["manifest_path"],
        "plan_paths": dict((plan_result.get("manifest") or {}).get("plan_paths") or {}),
        "barrier_epoch": round(float(barrier_epoch), 6) if barrier_epoch is not None else None,
        "time_expiry_epoch": round(float(time_expiry_epoch), 6) if time_expiry_epoch is not None else None,
        "time_expiry_stale_epoch": (
            round(float(time_expiry_stale_epoch), 6) if time_expiry_stale_epoch is not None else None
        ),
        "time_expiry_schedule": dict(time_expiry_schedule or {}),
        "created_epoch": round(time.time(), 6),
    }


def summarize_attempt_error_details(merged_result: Mapping[str, Any]) -> dict[str, int]:
    details: dict[str, int] = {}
    for attempt in merged_result.get("attempts") or []:
        if not isinstance(attempt, Mapping):
            continue
        if attempt.get("outcome") != "error":
            continue
        detail = str(attempt.get("detail") or "unknown")
        details[detail] = details.get(detail, 0) + 1
    return dict(sorted(details.items()))


def build_dual_role_final_report(
    *,
    prepare: Mapping[str, Any],
    merged_result: Mapping[str, Any],
    persistence: HotOfferPersistenceSnapshot,
    manual_expiry_result: Mapping[str, Any] | None = None,
    time_expiry_result: Mapping[str, Any] | None = None,
    read_during_write_results: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    scenario = _require_mapping(prepare.get("scenario"), "prepare scenario")
    scenario_name = str(scenario.get("name") or "hot_offer")
    expected_winner_count = int(scenario.get("expected_winner_count") or 0)
    expected_remaining_quantity = int(scenario.get("expected_remaining_quantity") or 0)
    require_terminal_completed = bool(scenario.get("require_terminal_completed", True))
    idempotency_mode = str(scenario.get("idempotency_mode") or "unique")
    summary = _require_mapping(merged_result.get("summary"), "merged result summary")
    report = {
        "schema_version": DUAL_ROLE_FINAL_SCHEMA_VERSION,
        "status": "ok",
        "run_id": prepare.get("run_id"),
        "prefix": prepare.get("prefix"),
        "topology": prepare.get("topology"),
        "telegram_gateway_boundary": prepare.get("telegram_gateway_boundary"),
        "merged_schema_version": merged_result.get("schema_version"),
        "reports": {
            scenario_name: {
                "summary": summary,
                "offer_id": persistence.offer_id,
                "owner_user_id": _require_mapping(prepare.get("offer"), "prepare offer").get("owner_user_id"),
                "expected_winner_count": expected_winner_count,
                "persisted_trade_count": persistence.persisted_trade_count,
                "offer_remaining_quantity": persistence.remaining_quantity,
                "offer_status": persistence.offer_status,
                "persistence": asdict(persistence),
                "roles": dict(merged_result.get("roles") or {}),
                "role_start_skew": dict(merged_result.get("role_start_skew") or {}),
                "attempt_error_details": summarize_attempt_error_details(merged_result),
                "manual_expiry_result": dict(manual_expiry_result or {}),
                "time_expiry_result": dict(time_expiry_result or {}),
                "read_during_write_results": [dict(result) for result in (read_during_write_results or [])],
            }
        },
    }
    try:
        if scenario_name == "manual_expire_trade_race":
            assert_manual_expiry_trade_race_acceptance(
                response_success_count=int(summary.get("success") or 0),
                error_count=int(summary.get("error") or 0),
                persistence=persistence,
                expected_winner_count=expected_winner_count,
                manual_expiry_result=manual_expiry_result,
            )
        elif scenario_name == "time_expire_trade_race":
            assert_time_expiry_trade_race_acceptance(
                response_success_count=int(summary.get("success") or 0),
                error_count=int(summary.get("error") or 0),
                persistence=persistence,
                expected_winner_count=expected_winner_count,
                time_expiry_result=time_expiry_result,
            )
        elif scenario_name == "read_during_write":
            assert_hot_offer_contention_acceptance(
                persisted_trade_count=persistence.persisted_trade_count,
                response_success_count=int(summary.get("success") or 0),
                error_count=int(summary.get("error") or 0),
                remaining_quantity=persistence.remaining_quantity,
                status=persistence.offer_status,
                expected_winner_count=expected_winner_count,
                original_quantity=persistence.original_quantity,
                completed_trade_quantity=persistence.completed_trade_quantity,
                completed_ledger_count=persistence.completed_ledger_count,
                trades_without_completed_ledger_count=persistence.trades_without_completed_ledger_count,
                failed_internal_ledger_count=persistence.failed_internal_ledger_count,
            )
            assert_read_during_write_acceptance(
                read_results=list(read_during_write_results or []),
                expected_read_count=int(scenario.get("hot_offer_requests") or 0),
            )
        elif idempotency_mode == "duplicate_replay":
            statuses = [
                str(attempt.get("outcome") or "unknown")
                for attempt in (merged_result.get("attempts") or [])
                if isinstance(attempt, Mapping)
            ]
            assert_duplicate_replay_acceptance(
                statuses=statuses,
                persistence=persistence,
                expected_completed_quantity=persistence.original_quantity - expected_remaining_quantity,
                expected_remaining_quantity=expected_remaining_quantity,
                require_terminal_completed=require_terminal_completed,
            )
        else:
            assert_hot_offer_contention_acceptance(
                persisted_trade_count=persistence.persisted_trade_count,
                response_success_count=int(summary.get("success") or 0),
                error_count=int(summary.get("error") or 0),
                remaining_quantity=persistence.remaining_quantity,
                status=persistence.offer_status,
                expected_winner_count=expected_winner_count,
                original_quantity=persistence.original_quantity,
                completed_trade_quantity=persistence.completed_trade_quantity,
                completed_ledger_count=persistence.completed_ledger_count,
                trades_without_completed_ledger_count=persistence.trades_without_completed_ledger_count,
                failed_internal_ledger_count=persistence.failed_internal_ledger_count,
            )
        report["correctness_failures"] = []
    except TradingProbeError as exc:
        report["status"] = "failed"
        report["correctness_failures"] = [str(exc)]
    return report


async def write_dual_role_plan_command(args: argparse.Namespace) -> int:
    plans = _build_dual_role_plans_from_args(args)
    result = _write_dual_role_plan_files(Path(args.output_dir), plans, mode="plan_only")
    print_json({"status": "ok", **result})
    return 0


async def run_dual_role_artifact_smoke_command(args: argparse.Namespace) -> int:
    plans = _build_dual_role_plans_from_args(args)
    output_dir = Path(args.output_dir)
    result = _write_dual_role_plan_files(output_dir, plans, mode="artifact_smoke")
    smoke_started_epoch = time.time()
    role_results: list[dict[str, Any]] = []
    result_paths: dict[str, str] = {}
    for offset, (role, plan) in enumerate(plans.items()):
        role_result = build_dry_role_result_artifact(plan, started_epoch=smoke_started_epoch + (offset * 0.001))
        result_path = output_dir / f"{role}.result.json"
        write_json_artifact(result_path, role_result)
        role_results.append(role_result)
        result_paths[role] = str(result_path)
    merged = merge_role_result_artifacts(role_results)
    merged["mode"] = "artifact_smoke"
    merged_path = output_dir / "merged.result.json"
    write_json_artifact(merged_path, merged)
    print_json(
        {
            "status": "ok",
            "mode": "artifact_smoke",
            "manifest_path": result["manifest_path"],
            "result_paths": result_paths,
            "merged_result_path": str(merged_path),
            "summary": merged["summary"],
        }
    )
    return 0


@asynccontextmanager
async def optional_role_worker_patches(*, patch_boundaries: bool, patch_external_side_effects: bool):
    if patch_boundaries and patch_external_side_effects:
        raise TradingProbeError("--patch-boundaries and --patch-external-side-effects cannot be used together")
    if patch_boundaries:
        async with patched_trading_boundaries():
            yield
        return
    if patch_external_side_effects:
        async with patched_external_side_effects():
            yield
        return
    yield


async def run_role_plan_command(args: argparse.Namespace) -> int:
    plan = read_json_artifact(Path(args.plan))
    validated = validate_role_plan_artifact(plan)
    assert_load_runner_runtime_surface(
        str(validated["role"]),
        allow_production=bool(args.allow_production_execution),
        prefix=str(validated["prefix"]),
    )
    setup_event_listeners()
    async with optional_role_worker_patches(
        patch_boundaries=bool(args.patch_boundaries),
        patch_external_side_effects=bool(args.patch_external_side_effects),
    ):
        result = await run_role_worker_plan(validated)
    if args.output:
        write_json_artifact(Path(args.output), result)
        print_json({"status": "ok", "result_path": args.output, "summary": result["summary"]})
    else:
        print_json(result)
    return 0


async def merge_role_results_command(args: argparse.Namespace) -> int:
    results = [read_json_artifact(Path(path)) for path in args.results]
    merged = merge_role_result_artifacts(results)
    if args.output:
        write_json_artifact(Path(args.output), merged)
        print_json({"status": "ok", "merged_result_path": args.output, "summary": merged["summary"]})
    else:
        print_json(merged)
    return 0


def add_dual_role_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--user-count", type=int, default=1000)
    parser.add_argument("--start-user-id", type=int, default=1)
    parser.add_argument("--start-telegram-id", type=int, default=9_000_000_000)
    parser.add_argument("--owner-user-id", type=int, default=1)
    parser.add_argument("--hot-offer-requests", type=int, default=1000)
    parser.add_argument("--telegram-ratio", type=float, default=0.6)
    parser.add_argument("--target-rps", type=float, default=600.0)
    parser.add_argument("--offer-id", type=int, default=1)
    parser.add_argument("--offer-public-id", default="smoke-offer")
    parser.add_argument("--request-amount", type=int, default=1)
    parser.add_argument("--barrier-delay-seconds", type=float, default=5.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Stage P7 trading benchmark helpers inside an app container.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--prefix", required=True)
    cleanup_parser.add_argument("--dry-run", action="store_true")
    cleanup_parser.add_argument("--allow-production-hard-delete", action="store_true")
    cleanup_parser.add_argument("--artifact")

    ready_parser = subparsers.add_parser("load-runner-ready")
    ready_parser.add_argument("--role", choices=tuple(LOAD_RUNNER_ROLES), required=True)

    negative_guard_parser = subparsers.add_parser("run-negative-guard-case")
    negative_guard_parser.add_argument("--prefix", required=True)
    negative_guard_parser.add_argument("--case-id", choices=tuple(sorted(NEGATIVE_GUARD_EXECUTABLE_CASES)), required=True)
    negative_guard_parser.add_argument("--output")
    negative_guard_parser.add_argument("--skip-initial-cleanup", action="store_true")
    negative_guard_parser.add_argument(
        "--allow-production-execution",
        action="store_true",
        help="Allow production negative-guard fixture creation only with the production full-matrix confirmation env.",
    )
    negative_guard_parser.add_argument(
        "--allow-production-cleanup",
        action="store_true",
        help="Allow initial cleanup in production only with the cleanup confirmation env.",
    )

    unsupported_policy_parser = subparsers.add_parser("run-unsupported-policy-case")
    unsupported_policy_parser.add_argument("--prefix", required=True)
    unsupported_policy_parser.add_argument("--actor-pair-id", required=True)
    unsupported_policy_parser.add_argument("--source-kind", choices=("user", "tier1", "tier2"), required=True)
    unsupported_policy_parser.add_argument("--responder-kind", choices=("user", "tier1", "tier2"), required=True)
    unsupported_policy_parser.add_argument("--group-relation", choices=("none", "same_owner", "other_owner"), required=True)
    unsupported_policy_parser.add_argument("--offer-surface", choices=("webapp", "telegram"), required=True)
    unsupported_policy_parser.add_argument("--request-surface", choices=("webapp", "telegram"), required=True)
    unsupported_policy_parser.add_argument("--offer-type", choices=("buy", "sell"), required=True)
    unsupported_policy_parser.add_argument("--quantity", type=int, required=True)
    unsupported_policy_parser.add_argument("--request-amount", type=int, required=True)
    unsupported_policy_parser.add_argument("--price", type=int, default=100000)
    unsupported_policy_parser.add_argument("--retail", action="store_true")
    unsupported_policy_parser.add_argument("--lot-sizes")
    unsupported_policy_parser.add_argument(
        "--unsupported-reason",
        action="append",
        choices=tuple(sorted(UNSUPPORTED_POLICY_EXECUTABLE_REASONS)),
        required=True,
    )
    unsupported_policy_parser.add_argument("--output")
    unsupported_policy_parser.add_argument("--skip-initial-cleanup", action="store_true")
    unsupported_policy_parser.add_argument(
        "--allow-production-execution",
        action="store_true",
        help="Allow production unsupported-policy fixture creation only with the production full-matrix confirmation env.",
    )
    unsupported_policy_parser.add_argument(
        "--allow-production-cleanup",
        action="store_true",
        help="Allow initial cleanup in production only with the cleanup confirmation env.",
    )

    prepare_parser = subparsers.add_parser("prepare-dual-role-run")
    prepare_parser.add_argument("--output-dir", required=True)
    prepare_parser.add_argument("--prefix", required=True)
    prepare_parser.add_argument("--run-id")
    prepare_parser.add_argument("--offer-origin", choices=("webapp", "bot"), default="webapp")
    prepare_parser.add_argument("--scenario-name")
    prepare_parser.add_argument("--request-surface", choices=("mixed", "webapp", "telegram"), default="mixed")
    prepare_parser.add_argument("--idempotency-mode", choices=("unique", "duplicate_replay"), default="unique")
    prepare_parser.add_argument("--user-count", type=int, default=1000)
    prepare_parser.add_argument("--hot-offer-requests", type=int, default=1000)
    prepare_parser.add_argument("--telegram-ratio", type=float, default=0.6)
    prepare_parser.add_argument("--target-rps", type=float, default=600.0)
    prepare_parser.add_argument("--hot-offer-quantity", type=int, default=5)
    prepare_parser.add_argument("--request-amount", type=int, default=5)
    prepare_parser.add_argument("--expected-winner-count", type=int, default=1)
    prepare_parser.add_argument("--expected-remaining-quantity", type=int)
    prepare_parser.add_argument("--require-terminal-completed", action="store_true", default=True)
    prepare_parser.add_argument("--allow-nonterminal-offer", dest="require_terminal_completed", action="store_false")
    prepare_parser.add_argument("--price", type=int, default=100000)
    prepare_parser.add_argument("--offer-type", choices=("buy", "sell"), default="sell")
    prepare_parser.add_argument("--retail", action="store_true")
    prepare_parser.add_argument("--lot-sizes", default="")
    prepare_parser.add_argument("--barrier-delay-seconds", type=float, default=20.0)
    prepare_parser.add_argument("--time-expiry-race-delay-seconds", type=float, default=TIME_EXPIRY_RACE_DELAY_SECONDS)
    prepare_parser.add_argument(
        "--time-expiry-race-stale-skew-seconds",
        type=float,
        default=TIME_EXPIRY_RACE_STALE_SKEW_SECONDS,
    )
    prepare_parser.add_argument(
        "--skip-initial-cleanup",
        action="store_true",
        help="Skip the prefix cleanup that normally runs before fixture creation.",
    )
    prepare_parser.add_argument(
        "--allow-production-execution",
        action="store_true",
        help="Allow production fixture creation only with the production full-matrix confirmation env.",
    )
    prepare_parser.add_argument(
        "--allow-production-cleanup",
        action="store_true",
        help="Allow the initial cleanup in production only with the cleanup confirmation env.",
    )

    plan_parser = subparsers.add_parser("write-dual-role-plan")
    add_dual_role_plan_arguments(plan_parser)

    smoke_parser = subparsers.add_parser("run-dual-role-artifact-smoke")
    add_dual_role_plan_arguments(smoke_parser)

    role_parser = subparsers.add_parser("run-role-plan")
    role_parser.add_argument("--plan", required=True)
    role_parser.add_argument("--output")
    role_parser.add_argument(
        "--patch-boundaries",
        action="store_true",
        help="Use local no-op market/realtime/Telegram boundaries for local DB smoke runs.",
    )
    role_parser.add_argument(
        "--patch-external-side-effects",
        action="store_true",
        help="Disable Telegram/realtime/web-push side effects while preserving real cross-server forwarding.",
    )
    role_parser.add_argument(
        "--allow-production-execution",
        action="store_true",
        help="Allow this role worker inside production only with the production full-matrix confirmation env.",
    )

    manual_expiry_parser = subparsers.add_parser("run-manual-expiry-race")
    manual_expiry_parser.add_argument("--prepare", required=True)
    manual_expiry_parser.add_argument("--output")
    manual_expiry_parser.add_argument(
        "--allow-production-execution",
        action="store_true",
        help="Allow this manual expiry race worker inside production only with the production full-matrix confirmation env.",
    )

    time_expiry_parser = subparsers.add_parser("run-time-expiry-race")
    time_expiry_parser.add_argument("--prepare", required=True)
    time_expiry_parser.add_argument("--output")
    time_expiry_parser.add_argument(
        "--allow-production-execution",
        action="store_true",
        help="Allow this time expiry race worker inside production only with the production full-matrix confirmation env.",
    )

    read_write_parser = subparsers.add_parser("run-read-during-write")
    read_write_parser.add_argument("--prepare", required=True)
    read_write_parser.add_argument("--read-surface", choices=("telegram", "webapp"), required=True)
    read_write_parser.add_argument("--output")
    read_write_parser.add_argument(
        "--allow-production-execution",
        action="store_true",
        help="Allow this read-during-write worker inside production only with the production full-matrix confirmation env.",
    )

    merge_parser = subparsers.add_parser("merge-role-results")
    merge_parser.add_argument("--output")
    merge_parser.add_argument("results", nargs="+")

    finalize_parser = subparsers.add_parser("finalize-dual-role-run")
    finalize_parser.add_argument("--prepare", required=True)
    finalize_parser.add_argument("--merged-result", required=True)
    finalize_parser.add_argument("--manual-expiry-result")
    finalize_parser.add_argument("--time-expiry-result")
    finalize_parser.add_argument("--read-during-write-result", action="append")
    finalize_parser.add_argument("--output")
    finalize_parser.add_argument("--check", action="store_true")

    observability_parser = subparsers.add_parser("observability-snapshot")
    observability_parser.add_argument("--output")

    bench_parser = subparsers.add_parser("run-benchmark")
    bench_parser.add_argument("--prefix", required=True)
    bench_parser.add_argument("--parse-iterations", type=int, default=30)
    bench_parser.add_argument("--bot-iterations", type=int, default=5)
    bench_parser.add_argument("--create-iterations", type=int, default=4)
    bench_parser.add_argument("--list-iterations", type=int, default=6)
    bench_parser.add_argument("--expire-iterations", type=int, default=2)
    bench_parser.add_argument("--trade-iterations", type=int, default=3)
    bench_parser.add_argument("--notification-iterations", type=int, default=4)
    bench_parser.add_argument("--race-concurrency", type=int, default=4)
    bench_parser.add_argument("--quantity", type=int, default=5)
    bench_parser.add_argument("--price", type=int, default=100000)

    mixed_parser = subparsers.add_parser("run-mixed-load")
    mixed_parser.add_argument("--prefix", required=True)
    mixed_parser.add_argument("--user-count", type=int, default=1000)
    mixed_parser.add_argument("--hot-offer-requests", type=int, default=1000)
    mixed_parser.add_argument("--telegram-ratio", type=float, default=0.6)
    mixed_parser.add_argument("--target-rps", type=float, default=600.0)
    mixed_parser.add_argument("--hot-offer-quantity", type=int, default=5)
    mixed_parser.add_argument("--request-amount", type=int, default=5)
    mixed_parser.add_argument("--expected-winner-count", type=int, default=1)
    mixed_parser.add_argument("--price", type=int, default=100000)
    mixed_parser.add_argument("--offer-type", choices=("buy", "sell"), default="sell")
    mixed_parser.add_argument("--offer-origin", choices=("webapp", "bot", "both"), default="webapp")

    hot_scenarios_parser = subparsers.add_parser("run-hot-offer-scenarios")
    hot_scenarios_parser.add_argument("--prefix", required=True)
    hot_scenarios_parser.add_argument("--user-count", type=int, default=1000)
    hot_scenarios_parser.add_argument("--hot-offer-requests", type=int, default=1000)
    hot_scenarios_parser.add_argument("--telegram-ratio", type=float, default=0.6)
    hot_scenarios_parser.add_argument("--target-rps", type=float, default=600.0)
    hot_scenarios_parser.add_argument("--price", type=int, default=100000)
    hot_scenarios_parser.add_argument("--offer-type", choices=("buy", "sell"), default="sell")
    hot_scenarios_parser.add_argument(
        "--scenario",
        action="append",
        help="Run only a named scenario. Repeat for multiple scenarios. Defaults to all scenarios.",
    )

    return parser


async def dispatch(args: argparse.Namespace) -> int:
    if args.command == "cleanup":
        return await cleanup_command(args)
    if args.command == "load-runner-ready":
        return await load_runner_ready_command(args)
    if args.command == "run-negative-guard-case":
        return await run_negative_guard_case_command(args)
    if args.command == "run-unsupported-policy-case":
        return await run_unsupported_policy_case_command(args)
    if args.command == "prepare-dual-role-run":
        return await prepare_dual_role_run_command(args)
    if args.command == "write-dual-role-plan":
        return await write_dual_role_plan_command(args)
    if args.command == "run-dual-role-artifact-smoke":
        return await run_dual_role_artifact_smoke_command(args)
    if args.command == "run-role-plan":
        return await run_role_plan_command(args)
    if args.command == "run-manual-expiry-race":
        return await run_manual_expiry_race_command(args)
    if args.command == "run-time-expiry-race":
        return await run_time_expiry_race_command(args)
    if args.command == "run-read-during-write":
        return await run_read_during_write_command(args)
    if args.command == "merge-role-results":
        return await merge_role_results_command(args)
    if args.command == "finalize-dual-role-run":
        return await finalize_dual_role_run_command(args)
    if args.command == "observability-snapshot":
        return await observability_snapshot_command(args)
    if args.command == "run-benchmark":
        return await run_benchmark(args)
    if args.command == "run-mixed-load":
        return await run_mixed_load_benchmark(args)
    if args.command == "run-hot-offer-scenarios":
        return await run_hot_offer_scenarios_command(args)
    raise TradingProbeError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(dispatch(args))
    except Exception as exc:
        print_json({"status": "error", "error_type": type(exc).__name__, "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
