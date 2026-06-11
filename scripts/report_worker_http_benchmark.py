#!/usr/bin/env python3
"""Run a redaction-safe authenticated HTTP benchmark inside the app container.

The probe is intentionally small and read-heavy. It generates an in-memory JWT
for a sampled active user, hits authenticated chat/market endpoints through the
local HTTP stack, and reports aggregate latency/error/DB-connection data without
printing tokens, user names, phone numbers, message text, or response bodies.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, or_, select, text


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_ENDPOINT_WEIGHTS = {
    "config": 1,
    "auth_me": 3,
    "chat_conversations": 8,
    "chat_poll": 5,
    "direct_messages": 7,
    "room_messages": 5,
    "offers_active": 3,
    "offers_my": 2,
    "trades_my": 2,
}


@dataclass(frozen=True)
class EndpointSpec:
    key: str
    method: str
    path: str
    template: str
    weight: int
    auth: bool = True


@dataclass(frozen=True)
class RequestResult:
    endpoint: str
    status_code: int
    latency_ms: float
    ok: bool
    error_kind: str | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark authenticated/chat HTTP latency inside the app container.")
    parser.add_argument("--base-url", default=os.environ.get("WORKER_BENCHMARK_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--requests", type=int, default=240)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=20260611)
    parser.add_argument("--db-sample-interval", type=float, default=0.5)
    parser.add_argument("--json", action="store_true", help="Emit JSON. This is the default for automation.")
    return parser.parse_args(argv)


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * percentile_value
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def summarize_results(results: list[RequestResult]) -> dict[str, Any]:
    latencies = [item.latency_ms for item in results]
    successes = [item for item in results if item.ok]
    failures = [item for item in results if not item.ok]
    by_endpoint: dict[str, list[RequestResult]] = {}
    for item in results:
        by_endpoint.setdefault(item.endpoint, []).append(item)

    endpoint_summary: dict[str, dict[str, Any]] = {}
    for endpoint, items in sorted(by_endpoint.items()):
        item_latencies = [item.latency_ms for item in items]
        item_failures = [item for item in items if not item.ok]
        endpoint_summary[endpoint] = {
            "count": len(items),
            "success_count": len(items) - len(item_failures),
            "failure_count": len(item_failures),
            "error_rate": round(len(item_failures) / max(1, len(items)), 6),
            "p50_ms": percentile(item_latencies, 0.50),
            "p95_ms": percentile(item_latencies, 0.95),
            "p99_ms": percentile(item_latencies, 0.99),
            "max_ms": round(max(item_latencies), 3) if item_latencies else 0.0,
            "status_codes": sorted({item.status_code for item in items}),
            "error_kinds": sorted({item.error_kind for item in item_failures if item.error_kind}),
        }

    return {
        "total_requests": len(results),
        "success_count": len(successes),
        "failure_count": len(failures),
        "error_rate": round(len(failures) / max(1, len(results)), 6),
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "p99_ms": percentile(latencies, 0.99),
        "max_ms": round(max(latencies), 3) if latencies else 0.0,
        "mean_ms": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        "status_codes": sorted({item.status_code for item in results}),
        "error_kinds": sorted({item.error_kind for item in failures if item.error_kind}),
        "by_endpoint": endpoint_summary,
    }


def int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def runtime_env_report() -> dict[str, Any]:
    workers = int_env("API_WORKERS", 8)
    pool_size = int_env("DB_POOL_SIZE", 8)
    max_overflow = int_env("DB_MAX_OVERFLOW", 6)
    per_process_ceiling = pool_size + max_overflow
    return {
        "api_workers": workers,
        "db_pool_size": pool_size,
        "db_max_overflow": max_overflow,
        "per_process_ceiling": per_process_ceiling,
        "estimated_sqlalchemy_ceiling": (workers + 1) * per_process_ceiling,
    }


async def connection_snapshot() -> dict[str, Any]:
    from core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text(
                """
                select
                    count(*)::int as total,
                    count(*) filter (where state = 'active')::int as active,
                    count(*) filter (where state = 'idle')::int as idle
                from pg_stat_activity
                """
            )
        )
        row = result.mappings().one()
        max_connections = await db.scalar(text("select current_setting('max_connections')::int"))
    return {
        "total": int(row["total"] or 0),
        "active": int(row["active"] or 0),
        "idle": int(row["idle"] or 0),
        "max_connections": int(max_connections or 0),
    }


async def sample_connections(stop_event: asyncio.Event, interval: float, samples: list[dict[str, Any]]) -> None:
    while not stop_event.is_set():
        try:
            samples.append({"at_monotonic": round(time.monotonic(), 3), **await connection_snapshot()})
        except Exception as exc:  # pragma: no cover - defensive runtime reporting
            samples.append({"at_monotonic": round(time.monotonic(), 3), "error_kind": type(exc).__name__})
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


def summarize_connection_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [item for item in samples if "total" in item]
    errors = [item for item in samples if "error_kind" in item]
    if not valid:
        return {
            "sample_count": len(samples),
            "valid_sample_count": 0,
            "sample_error_kinds": sorted({str(item.get("error_kind")) for item in errors}),
        }
    return {
        "sample_count": len(samples),
        "valid_sample_count": len(valid),
        "peak_total": max(int(item["total"]) for item in valid),
        "peak_active": max(int(item["active"]) for item in valid),
        "peak_idle": max(int(item["idle"]) for item in valid),
        "max_connections": max(int(item["max_connections"]) for item in valid),
        "sample_error_kinds": sorted({str(item.get("error_kind")) for item in errors}),
    }


async def select_sample_context() -> dict[str, Any]:
    from core.enums import ChatMembershipStatus, ChatType, UserAccountStatus
    from core.security import create_access_token
    from models.accountant_relation import AccountantRelation, AccountantRelationStatus
    from models.chat import Chat
    from models.chat_member import ChatMember
    from models.conversation import Conversation
    from models.customer_relation import CustomerRelation, CustomerRelationStatus
    from models.user import User, UserRole
    from core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        active_accountants = (
            select(AccountantRelation.accountant_user_id)
            .where(
                AccountantRelation.accountant_user_id.is_not(None),
                AccountantRelation.deleted_at.is_(None),
                AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            )
        )
        active_customers = (
            select(CustomerRelation.customer_user_id)
            .where(
                CustomerRelation.customer_user_id.is_not(None),
                CustomerRelation.deleted_at.is_(None),
                CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            )
        )
        eligible_user_stmt = (
            select(User.id)
            .where(
                User.is_deleted.is_(False),
                User.account_status == UserAccountStatus.ACTIVE,
                User.role.in_([UserRole.STANDARD, UserRole.MIDDLE_MANAGER, UserRole.SUPER_ADMIN]),
                or_(User.must_change_password.is_(False), User.must_change_password.is_(None)),
                ~User.id.in_(active_accountants),
                ~User.id.in_(active_customers),
            )
            .order_by(User.id.asc())
            .limit(200)
        )
        eligible_ids = [int(value) for value in (await db.execute(eligible_user_stmt)).scalars().all()]
        if not eligible_ids:
            fallback_stmt = (
                select(User.id)
                .where(
                    User.is_deleted.is_(False),
                    User.account_status == UserAccountStatus.ACTIVE,
                    or_(User.must_change_password.is_(False), User.must_change_password.is_(None)),
                )
                .order_by(User.id.asc())
                .limit(1)
            )
            fallback_id = (await db.execute(fallback_stmt)).scalar_one_or_none()
            if fallback_id is None:
                raise RuntimeError("No active user is available for authenticated benchmark sampling")
            eligible_ids = [int(fallback_id)]

        chosen_user_id = eligible_ids[0]
        direct_target_id: int | None = None
        direct_stmt = (
            select(Conversation.user1_id, Conversation.user2_id)
            .where(Conversation.last_message_id.is_not(None))
            .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
            .limit(200)
        )
        for user1_id, user2_id in (await db.execute(direct_stmt)).all():
            user1_id = int(user1_id)
            user2_id = int(user2_id)
            if user1_id in eligible_ids:
                chosen_user_id = user1_id
                direct_target_id = user2_id
                break
            if user2_id in eligible_ids:
                chosen_user_id = user2_id
                direct_target_id = user1_id
                break

        room_stmt = (
            select(Chat.id, Chat.type)
            .join(ChatMember, ChatMember.chat_id == Chat.id)
            .where(
                ChatMember.user_id == chosen_user_id,
                ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
                Chat.type.in_([ChatType.GROUP, ChatType.CHANNEL]),
                Chat.is_deleted.is_(False),
                Chat.last_message_id.is_not(None),
            )
            .order_by(Chat.last_message_at.desc().nullslast(), Chat.id.desc())
            .limit(1)
        )
        room_row = (await db.execute(room_stmt)).first()
        room_id = int(room_row[0]) if room_row else None
        room_kind = getattr(room_row[1], "value", str(room_row[1])) if room_row else None
        conversations_count = int(
            await db.scalar(
                select(func.count(Conversation.id)).where(
                    or_(Conversation.user1_id == chosen_user_id, Conversation.user2_id == chosen_user_id)
                )
            )
            or 0
        )

    token = create_access_token(subject=chosen_user_id, expires_delta=timedelta(minutes=15))
    return {
        "user_id": chosen_user_id,
        "token": token,
        "direct_target_id": direct_target_id,
        "room_id": room_id,
        "room_kind": room_kind,
        "sample_counts": {
            "eligible_user_count_capped": len(eligible_ids),
            "sample_user_conversation_count": conversations_count,
            "has_direct_history": direct_target_id is not None,
            "has_room_history": room_id is not None,
        },
    }


def build_endpoint_specs(context: dict[str, Any]) -> list[EndpointSpec]:
    specs = [
        EndpointSpec("config", "GET", "/api/config", "/api/config", DEFAULT_ENDPOINT_WEIGHTS["config"], auth=False),
        EndpointSpec("auth_me", "GET", "/api/auth/me", "/api/auth/me", DEFAULT_ENDPOINT_WEIGHTS["auth_me"]),
        EndpointSpec(
            "chat_conversations",
            "GET",
            "/api/chat/conversations",
            "/api/chat/conversations",
            DEFAULT_ENDPOINT_WEIGHTS["chat_conversations"],
        ),
        EndpointSpec("chat_poll", "GET", "/api/chat/poll", "/api/chat/poll", DEFAULT_ENDPOINT_WEIGHTS["chat_poll"]),
        EndpointSpec(
            "offers_active",
            "GET",
            "/api/offers/?limit=30",
            "/api/offers/?limit=30",
            DEFAULT_ENDPOINT_WEIGHTS["offers_active"],
        ),
        EndpointSpec(
            "offers_my",
            "GET",
            "/api/offers/my?limit=30",
            "/api/offers/my?limit=30",
            DEFAULT_ENDPOINT_WEIGHTS["offers_my"],
        ),
        EndpointSpec(
            "trades_my",
            "GET",
            "/api/trades/my?limit=30",
            "/api/trades/my?limit=30",
            DEFAULT_ENDPOINT_WEIGHTS["trades_my"],
        ),
    ]
    if context.get("direct_target_id"):
        specs.append(
            EndpointSpec(
                "direct_messages",
                "GET",
                f"/api/chat/messages/{int(context['direct_target_id'])}?limit=30",
                "/api/chat/messages/{user_id}?limit=30",
                DEFAULT_ENDPOINT_WEIGHTS["direct_messages"],
            )
        )
    if context.get("room_id"):
        specs.append(
            EndpointSpec(
                "room_messages",
                "GET",
                f"/api/chat/rooms/{int(context['room_id'])}/messages?limit=30",
                "/api/chat/rooms/{chat_id}/messages?limit=30",
                DEFAULT_ENDPOINT_WEIGHTS["room_messages"],
            )
        )
    return specs


def weighted_plan(specs: list[EndpointSpec], *, count: int, seed: int) -> list[EndpointSpec]:
    rng = random.Random(seed)
    return rng.choices(specs, weights=[max(1, spec.weight) for spec in specs], k=count)


async def run_request(
    client: httpx.AsyncClient,
    spec: EndpointSpec,
    *,
    token: str,
    timeout: float,
) -> RequestResult:
    headers = {"Authorization": f"Bearer {token}"} if spec.auth else {}
    started = time.perf_counter()
    try:
        response = await client.request(spec.method, spec.path, headers=headers, timeout=timeout)
        latency_ms = (time.perf_counter() - started) * 1000
        return RequestResult(
            endpoint=spec.template,
            status_code=response.status_code,
            latency_ms=round(latency_ms, 3),
            ok=200 <= response.status_code < 400,
            error_kind=None if 200 <= response.status_code < 400 else f"http_{response.status_code}",
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        return RequestResult(
            endpoint=spec.template,
            status_code=0,
            latency_ms=round(latency_ms, 3),
            ok=False,
            error_kind=type(exc).__name__,
        )


async def run_http_workload(args: argparse.Namespace, context: dict[str, Any]) -> dict[str, Any]:
    specs = build_endpoint_specs(context)
    plan = weighted_plan(specs, count=max(1, args.requests), seed=args.seed)
    limits = httpx.Limits(max_connections=max(args.concurrency * 2, args.concurrency + 4), max_keepalive_connections=args.concurrency)
    results: list[RequestResult] = []
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), limits=limits) as client:
        for spec in specs:
            results.append(await run_request(client, spec, token=context["token"], timeout=args.timeout))

        async def bounded(spec: EndpointSpec) -> RequestResult:
            async with semaphore:
                return await run_request(client, spec, token=context["token"], timeout=args.timeout)

        started = time.perf_counter()
        results.extend(await asyncio.gather(*(bounded(spec) for spec in plan)))
        duration_seconds = time.perf_counter() - started

    return {
        "duration_seconds": round(duration_seconds, 3),
        "requests_per_second": round(len(plan) / duration_seconds, 3) if duration_seconds > 0 else 0.0,
        "warmup_request_count": len(specs),
        "endpoint_plan": [
            {"template": spec.template, "weight": spec.weight, "auth": spec.auth}
            for spec in specs
        ],
        "summary": summarize_results(results),
    }


async def collect_report(args: argparse.Namespace) -> dict[str, Any]:
    context = await select_sample_context()
    samples: list[dict[str, Any]] = []
    before = await connection_snapshot()
    stop_event = asyncio.Event()
    sampler = asyncio.create_task(sample_connections(stop_event, args.db_sample_interval, samples))
    try:
        workload = await run_http_workload(args, context)
    finally:
        stop_event.set()
        await sampler
    after = await connection_snapshot()

    runtime = runtime_env_report()
    max_connections = max(
        int(before.get("max_connections") or 0),
        int(after.get("max_connections") or 0),
        int(summarize_connection_samples(samples).get("max_connections") or 0),
    )
    safe_limit = int(max_connections * 0.8) if max_connections else 0
    connection_samples = summarize_connection_samples(samples)
    peak_total = int(connection_samples.get("peak_total") or max(before.get("total", 0), after.get("total", 0)))
    runtime["max_connections"] = max_connections
    runtime["safe_connection_limit"] = safe_limit
    runtime["estimated_within_limit"] = bool(safe_limit and runtime["estimated_sqlalchemy_ceiling"] < safe_limit)
    runtime["peak_connections_within_limit"] = bool(safe_limit and peak_total < safe_limit)

    redacted_context = {
        "sample_user_id_hash": "user:" + hashlib.sha256(str(context["user_id"]).encode("utf-8")).hexdigest()[:12],
        "has_direct_history": bool(context.get("direct_target_id")),
        "has_room_history": bool(context.get("room_id")),
        "room_kind": context.get("room_kind"),
        "sample_counts": context.get("sample_counts", {}),
    }
    return {
        "ok": workload["summary"]["error_rate"] == 0 and runtime["estimated_within_limit"] and runtime["peak_connections_within_limit"],
        "base_url": args.base_url.rstrip("/"),
        "runtime": runtime,
        "context": redacted_context,
        "auth_note": "Authenticated endpoints are read-only in this benchmark, but get_current_user may refresh the sampled user's last_seen_at once.",
        "connections": {
            "before": before,
            "after": after,
            "samples": connection_samples,
        },
        "workload": workload,
    }


def format_human(report: dict[str, Any]) -> str:
    summary = report["workload"]["summary"]
    runtime = report["runtime"]
    return "\n".join(
        [
            "Worker HTTP benchmark:",
            f"- ok: {report['ok']}",
            f"- workers: {runtime['api_workers']}",
            f"- db pool/overflow: {runtime['db_pool_size']}/{runtime['db_max_overflow']}",
            f"- requests: {summary['total_requests']} error_rate={summary['error_rate']}",
            f"- rps: {report['workload']['requests_per_second']}",
            f"- p95/p99: {summary['p95_ms']}ms / {summary['p99_ms']}ms",
            f"- db peak/limit: {report['connections']['samples'].get('peak_total')} / {runtime['safe_connection_limit']}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(collect_report(args))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_human(report))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
