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
import json
import math
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
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

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers import offers as offers_router
from api.routers import realtime as realtime_router
from api.routers import trades as trades_router
from bot.callbacks import TextOfferActionCallback
from bot.handlers import trade_execute as bot_trade_execute
from bot.handlers import trade_create as bot_trade_create
from bot.middlewares import AuthMiddleware
from bot.middlewares.logging_context import BotLoggingContextMiddleware
from bot.utils.offer_parser import parse_offer_text
from core.config import settings
from core.db import AsyncSessionLocal
from core.enums import NotificationCategory, NotificationLevel, UserAccountStatus
from core.events import setup_event_listeners
from core.redis import pool
from core.services.accountant_relation_service import EffectiveOwnerActor
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, normalize_server, override_current_server
from core.telegram_trade_callbacks import build_channel_trade_callback_data
from core.utils import create_user_notification
from models.change_log import ChangeLog
from models.chat_member import ChatMember
from models.commodity import Commodity
from models.notification import Notification
from models.offer_publication_state import OfferPublicationState, OfferPublicationStatus
from models.offer_request import OfferRequest, OfferRequestStatus
from models.offer import Offer, OfferStatus
from models.trade import Trade, TradeStatus
from models.user import User, UserRole


class TradingProbeError(RuntimeError):
    pass


DUAL_ROLE_PLAN_SCHEMA_VERSION = "bot_webapp_mixed_load_role_plan_v1"
DUAL_ROLE_RESULT_SCHEMA_VERSION = "bot_webapp_mixed_load_role_result_v1"
DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION = "bot_webapp_mixed_load_merged_result_v1"
DUAL_ROLE_MANIFEST_SCHEMA_VERSION = "bot_webapp_mixed_load_manifest_v1"
DUAL_ROLE_PREPARE_SCHEMA_VERSION = "bot_webapp_mixed_load_prepare_v1"
DUAL_ROLE_FINAL_SCHEMA_VERSION = "bot_webapp_mixed_load_final_v1"
MIN_CLEANUP_PREFIX_LENGTH = 5
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


@dataclass(frozen=True)
class MixedLoadAttemptResult:
    index: int
    surface: str
    status: str
    duration_ms: float
    detail: str | None = None


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
    offer_ids: list[int]
    offer_public_ids: list[str]
    trade_ids: list[int]
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
) -> list[MixedLoadAttemptSpec]:
    if total_requests <= 0:
        raise TradingProbeError("mixed load total_requests must be positive")
    if not 0 < telegram_ratio < 1:
        raise TradingProbeError("mixed load telegram_ratio must be between 0 and 1")
    responders = [user for user in users if user.user_id != owner_user_id]
    if not responders:
        raise TradingProbeError("mixed load requires at least one responder distinct from the owner")

    telegram_slots_per_ten = int(round(telegram_ratio * 10))
    telegram_slots_per_ten = max(1, min(9, telegram_slots_per_ten))
    plan: list[MixedLoadAttemptSpec] = []
    for index in range(total_requests):
        responder = responders[index % len(responders)]
        surface = "telegram" if index % 10 < telegram_slots_per_ten else "webapp"
        plan.append(
            MixedLoadAttemptSpec(
                index=index,
                surface=surface,
                user_id=responder.user_id,
                telegram_id=responder.telegram_id,
            )
        )
    return plan


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
    if index < 0:
        raise TradingProbeError(f"invalid mixed load attempt index: {index}")
    if surface not in SURFACE_TO_LOAD_RUNNER_ROLE:
        raise TradingProbeError(f"unsupported mixed load attempt surface: {surface}")
    if user_id <= 0 or telegram_id <= 0:
        raise TradingProbeError("mixed load attempt user ids must be positive")
    return MixedLoadAttemptSpec(index=index, surface=surface, user_id=user_id, telegram_id=telegram_id)


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

    plan = build_mixed_surface_plan(
        users=users,
        owner_user_id=owner_user_id,
        total_requests=total_requests,
        telegram_ratio=telegram_ratio,
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
    return f"{prefix}{role}:{offer_id}:{attempt_index}"


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
                )
            )

    elapsed_seconds = max(finished_epochs) - min(started_epochs)
    attempts.sort(key=lambda item: (float(item["monotonic_timestamp"]), int(item["index"])))
    return {
        "schema_version": DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "ok",
        "roles": role_summaries,
        "role_start_skew": assert_role_result_start_skew(result_payloads, max_skew_seconds=3600.0),
        "summary": summarize_attempt_results(summary_inputs, elapsed_seconds=elapsed_seconds),
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
    return {
        "total": len(results),
        "elapsed_seconds": round(safe_elapsed, 3),
        "business_request_rps": round(len(results) / safe_elapsed, 3),
        "telegram_update_count": sum(2 for item in results if item.surface == "telegram"),
        "telegram_update_rps": round(
            sum(2 for item in results if item.surface == "telegram") / safe_elapsed,
            3,
        ),
        "success": sum(1 for item in results if item.status == "success"),
        "rejected": sum(1 for item in results if item.status == "rejected"),
        "error": sum(1 for item in results if item.status == "error"),
        "latency": summarize_samples([item.duration_ms for item in results]),
        "surfaces": by_surface,
    }


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
            request_amount=5,
            expected_winner_count=4,
            total_requests=total_requests,
            telegram_ratio=telegram_ratio,
            target_rps=target_rps,
            price=price,
            offer_type=offer_type,
        ),
        HotOfferScenarioSpec(
            name="bot_partial_fill",
            origin="bot",
            quantity=20,
            request_amount=5,
            expected_winner_count=4,
            total_requests=total_requests,
            telegram_ratio=telegram_ratio,
            target_rps=target_rps,
            price=price,
            offer_type=offer_type,
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


def assert_load_runner_runtime_surface(role: str) -> dict[str, Any]:
    role_config = LOAD_RUNNER_ROLES.get(role)
    if role_config is None:
        raise TradingProbeError(f"unsupported load runner role: {role}")

    configured_environment = str(getattr(settings, "environment", "") or "").strip().lower()
    configured_service = str(getattr(settings, "trading_bot_service", "") or "").strip().lower()
    configured_server_mode = normalize_server(getattr(settings, "server_mode", None), default="")
    reasons: list[str] = []

    if configured_environment != "staging":
        reasons.append("ENVIRONMENT must be staging for load runner runtime")
    if configured_service != "load_runner":
        reasons.append("TRADING_BOT_SERVICE must be load_runner for load runner runtime")
    if configured_server_mode != role_config["server_mode"]:
        reasons.append(f"SERVER_MODE must be {role_config['server_mode']} for {role} load runner")
    if getattr(settings, "bot_token", None):
        reasons.append("BOT_TOKEN must be empty for staging load runner runtime")

    if reasons:
        raise TradingProbeError("; ".join(reasons))

    return {
        "status": "ok",
        "role": role,
        "surface": role_config["surface"],
        "environment": configured_environment,
        "server_mode": configured_server_mode,
        "service": configured_service,
        "telegram_credential_configured": False,
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


def cleanup_plan_counts(plan: CleanupPlan) -> dict[str, int]:
    return {
        "users": len(plan.user_ids),
        "chat_members": len(plan.chat_member_ids),
        "offers": len(plan.offer_ids),
        "offer_publication_states": len(plan.publication_state_ids),
        "offer_requests": len(plan.offer_request_ids),
        "trades": len(plan.trade_ids),
        "notifications": len(plan.notification_ids),
    }


def cleanup_report_payload(
    *,
    plan: CleanupPlan,
    dry_run: bool,
    deleted_users: int = 0,
    deleted_chat_members: int = 0,
    deleted_offers: int = 0,
    deleted_trades: int = 0,
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
        "deleted_chat_members": deleted_chat_members,
        "deleted_offers": deleted_offers,
        "deleted_trades": deleted_trades,
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
        for user_id in user_ids:
            keys.extend(
                [
                    f"user:{user_id}:active_offer_count",
                    f"user:{user_id}:unread_count",
                    f"daily_expire:{user_id}:{today}",
                ]
            )
            async for key in client.scan_iter(match=f"expire_rate:{user_id}:*"):
                keys.append(str(key))
            async for key in client.scan_iter(match=f"confirm:{user_id}:*"):
                keys.append(str(key))
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
                await db.execute(select(User.id).where(User.account_name.like(pattern, escape=LIKE_ESCAPE)))
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
        trade_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(Trade.id).where(
                        (Trade.idempotency_key.like(pattern, escape=LIKE_ESCAPE))
                        | in_filter(Trade.offer_id, offer_ids)
                        | in_filter(Trade.offer_user_id, user_ids)
                        | in_filter(Trade.responder_user_id, user_ids)
                        | in_filter(Trade.actor_user_id, user_ids)
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
        offer_ids=offer_ids,
        offer_public_ids=offer_public_ids,
        trade_ids=trade_ids,
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

    async with AsyncSessionLocal() as db:
        deleted_notifications = 0
        deleted_chat_members = 0
        deleted_trades = 0
        deleted_offers = 0
        deleted_users = 0
        deleted_offer_requests = 0
        deleted_publication_states = 0
        if plan.notification_ids:
            deleted_notifications = int(
                (await db.execute(delete(Notification).where(Notification.id.in_(plan.notification_ids)))).rowcount or 0
            )
        if plan.publication_state_ids:
            deleted_publication_states = int(
                (
                    await db.execute(
                        delete(OfferPublicationState).where(OfferPublicationState.id.in_(plan.publication_state_ids))
                    )
                ).rowcount
                or 0
            )
        if plan.offer_request_ids:
            deleted_offer_requests = int(
                (await db.execute(delete(OfferRequest).where(OfferRequest.id.in_(plan.offer_request_ids)))).rowcount
                or 0
            )
        if plan.chat_member_ids:
            deleted_chat_members = int(
                (await db.execute(delete(ChatMember).where(ChatMember.id.in_(plan.chat_member_ids)))).rowcount or 0
            )
        if plan.trade_ids:
            deleted_trades = int((await db.execute(delete(Trade).where(Trade.id.in_(plan.trade_ids)))).rowcount or 0)
        if plan.offer_ids:
            deleted_offers = int((await db.execute(delete(Offer).where(Offer.id.in_(plan.offer_ids)))).rowcount or 0)
        if plan.user_ids:
            deleted_users = int((await db.execute(delete(User).where(User.id.in_(plan.user_ids)))).rowcount or 0)

        change_log_result = await db.execute(
            text(
                """
                DELETE FROM change_log
                WHERE strpos(data::text, :raw_prefix) > 0
                   OR (table_name = 'users' AND record_id = ANY(:user_ids))
                   OR (table_name = 'offers' AND record_id = ANY(:offer_ids))
                   OR (table_name = 'trades' AND record_id = ANY(:trade_ids))
                   OR (table_name = 'offer_requests' AND record_id = ANY(:offer_request_ids))
                   OR (table_name = 'offer_publication_states' AND record_id = ANY(:publication_state_ids))
                   OR (table_name = 'notifications' AND record_id = ANY(:notification_ids))
                   OR (table_name = 'chat_members' AND record_id = ANY(:chat_member_ids))
                """
            ),
            {
                "raw_prefix": plan.prefix,
                "user_ids": plan.user_ids or [-1],
                "offer_ids": plan.offer_ids or [-1],
                "trade_ids": plan.trade_ids or [-1],
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
        deleted_chat_members=deleted_chat_members,
        deleted_offers=deleted_offers,
        deleted_trades=deleted_trades,
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


async def create_load_fixture_users(prefix: str, *, user_count: int) -> list[LoadUserRef]:
    if user_count < 3:
        raise TradingProbeError("mixed load requires at least 3 synthetic users")

    setup_event_listeners()
    prefix_hash = abs(hash(prefix)) % 1000
    users: list[User] = []
    for index in range(user_count):
        users.append(
            User(
                account_name=f"{prefix}load_{index:04d}",
                mobile_number=f"0988{prefix_hash:03d}{index:04d}",
                telegram_id=8800000000 + prefix_hash * 10000 + index,
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

    async with AsyncSessionLocal() as db:
        db.add_all(users)
        await db.commit()
        refs: list[LoadUserRef] = []
        for user in users:
            await db.refresh(user)
            refs.append(LoadUserRef(user_id=int(user.id), telegram_id=int(user.telegram_id)))
        return refs


async def fake_market_open(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(is_open=True, next_transition_at=None)


async def noop_async(*_args: Any, **_kwargs: Any) -> None:
    return None


async def noop_send_offer(*_args: Any, **_kwargs: Any) -> None:
    return None


async def noop_register_market_offer_created(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(is_open=True, offers_since_last_open=0)


async def noop_update_channel_buttons(*_args: Any, **_kwargs: Any) -> bool:
    return True


@asynccontextmanager
async def patched_trading_boundaries():
    import core.services.trade_service as trade_service

    original = {
        "offers_evaluate": offers_router.evaluate_current_market_schedule,
        "trades_evaluate": trades_router.evaluate_current_market_schedule,
        "send_offer": offers_router.send_offer_to_channel,
        "register_market_offer_created": offers_router.register_market_offer_created,
        "update_channel_buttons": trades_router.update_channel_buttons,
        "send_telegram_message_sync": trades_router.send_telegram_message_sync,
        "forward_trade_to_home_server": trades_router.forward_trade_to_home_server,
        "realtime_publish": realtime_router.publish_event,
        "realtime_publish_user": realtime_router.publish_user_event,
        "bot_market_open": bot_trade_create._bot_market_is_open,
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

                    response = await trades_router._execute_trade_authoritatively(
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

    offers_router.evaluate_current_market_schedule = fake_market_open
    trades_router.evaluate_current_market_schedule = fake_market_open
    offers_router.send_offer_to_channel = noop_send_offer
    offers_router.register_market_offer_created = noop_register_market_offer_created
    trades_router.update_channel_buttons = noop_update_channel_buttons
    trades_router.send_telegram_message_sync = lambda *_args, **_kwargs: None
    trades_router.forward_trade_to_home_server = local_forward_trade_to_home_server
    realtime_router.publish_event = noop_async
    realtime_router.publish_user_event = noop_async
    bot_trade_create._bot_market_is_open = lambda: asyncio.sleep(0, result=True)
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
        realtime_router.publish_event = original["realtime_publish"]
        realtime_router.publish_user_event = original["realtime_publish_user"]
        bot_trade_create._bot_market_is_open = original["bot_market_open"]
        trade_service.validate_competitive_price = original["validate_competitive_price"]
        trade_service.detect_offer_price_warning = original["detect_offer_price_warning"]


def owner_context(user: User) -> EffectiveOwnerActor:
    return EffectiveOwnerActor(owner_user=user, actor_user=user, relation=None, is_accountant_context=False)


async def load_user(session, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise TradingProbeError(f"synthetic user {user_id} disappeared")
    return user


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
) -> int:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        normalized_lot_sizes = list(lot_sizes) if lot_sizes else None
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
        return int(response.id)


async def expire_offer_for_user(*, user_id: int, offer_id: int) -> None:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        await offers_router.expire_offer(offer_id, db=db, context=owner_context(user))


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


async def execute_trade_for_user(
    *,
    user_id: int,
    offer_id: int,
    quantity: int,
    idempotency_key: str,
) -> Any:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        return await trades_router._execute_trade_authoritatively(
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


class AiogramDispatcherHarness:
    def __init__(self) -> None:
        self.telegram = RecordingTelegramBot()
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.update.outer_middleware(AuthMiddleware(AsyncSessionLocal))
        self.dp.update.outer_middleware(BotLoggingContextMiddleware())
        self.dp.include_router(bot_trade_create.router)
        self.dp.include_router(bot_trade_execute.router)
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


async def execute_webapp_trade_for_user(
    *,
    user_id: int,
    offer_id: int,
    quantity: int,
    idempotency_key: str,
    error_details: list[str] | None = None,
    phase_details: dict[str, Any] | None = None,
) -> str:
    background_tasks = BackgroundTasks()
    try:
        create_started = time.perf_counter()
        async with AsyncSessionLocal() as db:
            user = await load_user(db, user_id)
            response = await trades_router.create_trade(
                trade_data=trades_router.TradeCreate(
                    offer_id=offer_id,
                    quantity=quantity,
                    idempotency_key=idempotency_key,
                ),
                background_tasks=background_tasks,
                raw_request=SimpleNamespace(body=lambda: b""),
                db=db,
                context=owner_context(user),
            )
        if phase_details is not None:
            phase_details["create_trade_ms"] = round((time.perf_counter() - create_started) * 1000.0, 3)
        background_started = time.perf_counter()
        await background_tasks()
        if phase_details is not None:
            phase_details["background_tasks_ms"] = round((time.perf_counter() - background_started) * 1000.0, 3)
    except HTTPException as exc:
        status_code = int(exc.status_code or 500)
        if status_code >= 500 and error_details is not None:
            error_details.append(f"HTTPException {status_code}: {exc.detail}")
        if phase_details is not None:
            phase_details["exception"] = f"HTTPException {status_code}"
        return "rejected" if status_code < 500 else "error"
    except Exception as exc:
        if error_details is not None:
            error_details.append(f"{type(exc).__name__}: {exc}")
        if phase_details is not None:
            phase_details["exception"] = type(exc).__name__
        return "error"

    if isinstance(response, JSONResponse):
        if response.status_code >= 500 and error_details is not None:
            try:
                body = json.loads(response.body.decode("utf-8") or "{}")
            except Exception:
                body = {"detail": "invalid JSONResponse body"}
            error_details.append(f"JSONResponse {response.status_code}: {body.get('detail') or body}")
        return "success" if response.status_code < 400 else ("rejected" if response.status_code < 500 else "error")
    return "success"


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
    if observed_idempotency_keys is not None:
        recorder_token = _TELEGRAM_IDEMPOTENCY_KEYS.set(observed_idempotency_keys)
    try:
        first_started = time.perf_counter()
        first_answer = await harness.feed_channel_callback(
            telegram_id=spec.telegram_id,
            callback_data=callback_data,
            callback_id=first_callback_id,
            channel_message_id=channel_message_id,
        )
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
        if phase_details is not None:
            phase_details["second_callback_ms"] = round((time.perf_counter() - second_started) * 1000.0, 3)
            phase_details["second_answer_text"] = str((answer or {}).get("text") or "")
            phase_details["second_answer_alert"] = (answer or {}).get("show_alert")
    except Exception as exc:
        if error_details is not None:
            error_details.append(f"{type(exc).__name__}: {exc}")
        if phase_details is not None:
            phase_details["exception"] = type(exc).__name__
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
        idempotency_key: str | None = build_role_attempt_idempotency_key(
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
                    quantity=request_amount,
                    idempotency_key=idempotency_key,
                    error_details=attempt_error_details,
                    phase_details=phase_details,
                )
            else:
                if harness is None:
                    raise TradingProbeError("telegram role worker requires dispatcher harness")
                offer_snapshot = await load_offer_snapshot(offer_id)
                status_value = await execute_bot_trade_with_dispatcher(
                    harness=harness,
                    spec=spec,
                    offer=offer_snapshot,
                    amount=request_amount,
                    prefix=f"{prefix}{role}-",
                    observed_idempotency_keys=observed_telegram_keys,
                    error_details=attempt_error_details,
                    phase_details=phase_details,
                )
                if observed_telegram_keys:
                    idempotency_key = observed_telegram_keys[-1]
        except Exception as exc:
            status_value = "error"
            detail = f"{type(exc).__name__}: {exc}"
        if status_value == "error" and detail is None and attempt_error_details:
            detail = attempt_error_details[-1]
        latency_ms = round((time.perf_counter() - attempt_started) * 1000.0, 3)
        result = MixedLoadAttemptResult(
            index=spec.index,
            surface=surface,
            status=status_value,
            duration_ms=latency_ms,
            detail=detail,
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
            "detail": detail,
            "phase_details": phase_details,
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
        idempotency_key = build_role_attempt_idempotency_key(
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
            }
        )
        results.append(
            MixedLoadAttemptResult(
                index=spec.index,
                surface=surface,
                status=outcome,
                duration_ms=latency_ms,
                detail="dry_run_artifact_smoke",
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
    started = time.perf_counter()

    async def _attempt(spec: MixedLoadAttemptSpec) -> MixedLoadAttemptResult:
        scheduled_at = started + (spec.index / target_rps)
        delay = scheduled_at - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)

        attempt_started = time.perf_counter()
        status_value = "error"
        detail: str | None = None
        try:
            if spec.surface == "webapp":
                status_value = await execute_webapp_trade_for_user(
                    user_id=spec.user_id,
                    offer_id=offer_id,
                    quantity=amount,
                    idempotency_key=f"{prefix}web-{offer_id}-{spec.index}",
                )
            else:
                offer = await load_offer_snapshot(offer_id)
                status_value = await execute_bot_trade_with_dispatcher(
                    harness=harness,
                    spec=spec,
                    offer=offer,
                    amount=amount,
                    prefix=prefix,
                )
        except Exception as exc:
            status_value = "error"
            detail = type(exc).__name__
        return MixedLoadAttemptResult(
            index=spec.index,
            surface=spec.surface,
            status=status_value,
            duration_ms=round((time.perf_counter() - attempt_started) * 1000.0, 3),
            detail=detail,
        )

    try:
        results = await asyncio.gather(*[_attempt(spec) for spec in plan])
    finally:
        await harness.close()
    elapsed = time.perf_counter() - started

    persistence = await inspect_hot_offer_persistence(offer_id)

    summary = summarize_attempt_results(results, elapsed_seconds=elapsed)
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
    return {
        "offer_id": offer_id,
        "owner_user_id": owner_user_id,
        "expected_winner_count": expected_winner_count,
        "persisted_trade_count": persistence.persisted_trade_count,
        "offer_remaining_quantity": persistence.remaining_quantity,
        "offer_status": persistence.offer_status,
        "persistence": asdict(persistence),
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
    if persistence.completed_trade_quantity != persistence.original_quantity:
        raise TradingProbeError(
            "duplicate/replay probe expected completed quantity "
            f"{persistence.original_quantity}, got {persistence.completed_trade_quantity}"
        )
    if persistence.remaining_quantity != 0:
        raise TradingProbeError(
            f"duplicate/replay probe expected remaining_quantity=0, got {persistence.remaining_quantity}"
        )
    if persistence.offer_status != OfferStatus.COMPLETED.value:
        raise TradingProbeError(f"duplicate/replay probe expected completed status, got {persistence.offer_status}")
    if persistence.trades_without_completed_ledger_count:
        raise TradingProbeError("duplicate/replay probe persisted a trade without completed request ledger")
    if persistence.failed_internal_ledger_count:
        raise TradingProbeError(
            f"duplicate/replay probe expected zero failed_internal ledgers, got {persistence.failed_internal_ledger_count}"
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
    async with patched_trading_boundaries():
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

    async with patched_trading_boundaries():
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
    payload = await cleanup_prefix(args.prefix, dry_run=bool(args.dry_run))
    if args.artifact:
        write_json_artifact(Path(args.artifact), payload)
    print_json(payload)
    return 0


async def load_runner_ready_command(args: argparse.Namespace) -> int:
    print_json(assert_load_runner_runtime_surface(args.role))
    return 0


async def prepare_dual_role_run_command(args: argparse.Namespace) -> int:
    setup_event_listeners()
    prefix = args.prefix
    await cleanup_prefix(prefix)
    commodity_id, commodity_name = await resolve_commodity()
    offer_origin = str(args.offer_origin)
    run_id = _dual_role_run_id(prefix, getattr(args, "run_id", None))
    topology = "single-db staging role-worker smoke"

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
        barrier_epoch=time.time() + args.barrier_delay_seconds,
    )
    output_dir = Path(args.output_dir)
    plan_result = _write_dual_role_plan_files(output_dir, plans, mode="staging_dual_role_run")
    prepare = build_dual_role_prepare_artifact(
        run_id=run_id,
        prefix=prefix,
        topology=topology,
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
        request_amount=args.request_amount,
        expected_winner_count=args.expected_winner_count,
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


async def finalize_dual_role_run_command(args: argparse.Namespace) -> int:
    prepare = read_json_artifact(Path(args.prepare))
    merged_result = read_json_artifact(Path(args.merged_result))
    offer = _require_mapping(prepare.get("offer"), "prepare offer")
    persistence = await inspect_hot_offer_persistence(int(offer["id"]))
    report = build_dual_role_final_report(
        prepare=prepare,
        merged_result=merged_result,
        persistence=persistence,
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
    request_amount: int,
    expected_winner_count: int,
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
            "name": dual_role_scenario_name(offer_origin),
            "offer_origin": offer_origin,
            "hot_offer_requests": int(hot_offer_requests),
            "telegram_ratio": float(telegram_ratio),
            "target_rps": float(target_rps),
            "hot_offer_quantity": int(hot_offer_quantity),
            "request_amount": int(request_amount),
            "expected_winner_count": int(expected_winner_count),
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
) -> dict[str, Any]:
    scenario = _require_mapping(prepare.get("scenario"), "prepare scenario")
    scenario_name = str(scenario.get("name") or "hot_offer")
    expected_winner_count = int(scenario.get("expected_winner_count") or 0)
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
            }
        },
    }
    try:
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
async def optional_patched_boundaries(enabled: bool):
    if enabled:
        async with patched_trading_boundaries():
            yield
        return
    yield


async def run_role_plan_command(args: argparse.Namespace) -> int:
    plan = read_json_artifact(Path(args.plan))
    validated = validate_role_plan_artifact(plan)
    assert_load_runner_runtime_surface(str(validated["role"]))
    setup_event_listeners()
    async with optional_patched_boundaries(bool(args.patch_boundaries)):
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
    cleanup_parser.add_argument("--artifact")

    ready_parser = subparsers.add_parser("load-runner-ready")
    ready_parser.add_argument("--role", choices=tuple(LOAD_RUNNER_ROLES), required=True)

    prepare_parser = subparsers.add_parser("prepare-dual-role-run")
    prepare_parser.add_argument("--output-dir", required=True)
    prepare_parser.add_argument("--prefix", required=True)
    prepare_parser.add_argument("--run-id")
    prepare_parser.add_argument("--offer-origin", choices=("webapp", "bot"), default="webapp")
    prepare_parser.add_argument("--user-count", type=int, default=1000)
    prepare_parser.add_argument("--hot-offer-requests", type=int, default=1000)
    prepare_parser.add_argument("--telegram-ratio", type=float, default=0.6)
    prepare_parser.add_argument("--target-rps", type=float, default=600.0)
    prepare_parser.add_argument("--hot-offer-quantity", type=int, default=5)
    prepare_parser.add_argument("--request-amount", type=int, default=5)
    prepare_parser.add_argument("--expected-winner-count", type=int, default=1)
    prepare_parser.add_argument("--price", type=int, default=100000)
    prepare_parser.add_argument("--offer-type", choices=("buy", "sell"), default="sell")
    prepare_parser.add_argument("--barrier-delay-seconds", type=float, default=8.0)

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

    merge_parser = subparsers.add_parser("merge-role-results")
    merge_parser.add_argument("--output")
    merge_parser.add_argument("results", nargs="+")

    finalize_parser = subparsers.add_parser("finalize-dual-role-run")
    finalize_parser.add_argument("--prepare", required=True)
    finalize_parser.add_argument("--merged-result", required=True)
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
    if args.command == "prepare-dual-role-run":
        return await prepare_dual_role_run_command(args)
    if args.command == "write-dual-role-plan":
        return await write_dual_role_plan_command(args)
    if args.command == "run-dual-role-artifact-smoke":
        return await run_dual_role_artifact_smoke_command(args)
    if args.command == "run-role-plan":
        return await run_role_plan_command(args)
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
