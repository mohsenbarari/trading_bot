#!/usr/bin/env python3
"""Small in-container helpers for the production cross-server sync benchmark."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as redis
from sqlalchemy import delete, false, func, or_, select, text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.events import setup_event_listeners
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from core.utils import utc_now
from models.change_log import ChangeLog
from models.offer import Offer, OfferStatus
from models.offer_publication_state import OfferPublicationState
from models.offer_request import OfferRequest
from models.trade import Trade
from models.market_schedule_override import MarketScheduleOverride, MarketScheduleOverrideType
from core.services.cross_server_recovery_service import (
    clear_active_publication_gate,
    enable_active_publication_gate,
    finalize_outage_recovery,
    load_active_publication_gate,
)
from core.services.offer_publication_reconciliation_service import (
    publication_observability_summary,
    reconcile_offer_publications,
)


BOT_WEBAPP_SYNC_EVIDENCE_SCHEMA_VERSION = "bot_webapp_cross_server_sync_evidence_v1"
OFFER_SYNC_SNAPSHOT_SCHEMA_VERSION = "bot_webapp_offer_sync_snapshot_v1"
OFFER_SYNC_TABLES = ("offers", "trades", "offer_requests", "offer_publication_states")
MESSENGER_NO_SYNC_TABLES = (
    "messages",
    "conversations",
    "chats",
    "chat_members",
    "chat_files",
    "upload_batches",
    "upload_sessions",
)
REQUIRED_BOT_WEBAPP_SYNC_CHECKS = (
    "foreign_offer_to_iran",
    "iran_offer_to_foreign_projection",
    "iran_trade_to_foreign_telegram_terminal",
    "foreign_trade_to_iran_webapp_history",
    "stale_replay_terminal_guard",
)
TERMINAL_OFFER_STATUSES = {
    OfferStatus.COMPLETED.value,
    OfferStatus.CANCELLED.value,
    OfferStatus.EXPIRED.value,
}


class SyncProbeError(RuntimeError):
    pass


def json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=json_safe))


def parse_probe_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_probe_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    return datetime.fromisoformat(normalized)


async def health(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.count(ChangeLog.id), func.min(ChangeLog.created_at)).where(ChangeLog.synced == False)
        )
        unsynced_count, oldest = result.one()
        by_table_rows = await db.execute(
            select(ChangeLog.table_name, func.count(ChangeLog.id))
            .where(ChangeLog.synced == False)
            .group_by(ChangeLog.table_name)
            .order_by(ChangeLog.table_name)
        )
        unsynced_by_table = {table: int(count or 0) for table, count in by_table_rows.all()}
        try:
            publication_reconciliation = await publication_observability_summary(
                db,
                server_mode=settings.server_mode,
                unsynced_by_table=unsynced_by_table,
            )
        except Exception as exc:
            publication_reconciliation = {"status": "error", "error_type": type(exc).__name__}
    redis_client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0, decode_responses=True)
    try:
        outbound = int(await redis_client.llen("sync:outbound") or 0)
        retry = int(await redis_client.llen("sync:retry") or 0)
        active_publication_gate = await load_active_publication_gate(redis_client)
        redis_ok = True
    except Exception:
        outbound = 0
        retry = 0
        active_publication_gate = {"enabled": False, "status": "redis_error"}
        redis_ok = False
    finally:
        await redis_client.aclose()
    print_json(
        {
            "status": "ok",
            "server_mode": settings.server_mode,
            "unsynced_change_log_count": int(unsynced_count or 0),
            "oldest_unsynced_at": oldest,
            "unsynced_by_table": unsynced_by_table,
            "redis_ok": redis_ok,
            "redis_queues": {"sync:outbound": outbound, "sync:retry": retry},
            "active_publication_gate": active_publication_gate,
            "publication_reconciliation": publication_reconciliation,
        }
    )
    return 0


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _count_values(values: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(_enum_value(value) or "null")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def offer_sync_evidence_tables() -> tuple[str, ...]:
    for table_name in OFFER_SYNC_TABLES:
        entry = get_sync_registry_entry(table_name)
        if entry.policy != SyncPolicy.SYNC:
            raise SyncProbeError(f"{table_name} is not classified as sync")
    return OFFER_SYNC_TABLES


def assert_no_messenger_tables_in_evidence(table_names: list[str] | tuple[str, ...]) -> None:
    forbidden = sorted(set(table_names) & set(MESSENGER_NO_SYNC_TABLES))
    if forbidden:
        raise SyncProbeError(f"messenger/no-sync tables are not valid sync evidence: {', '.join(forbidden)}")


def _snapshot_offer_statuses(snapshot: dict[str, Any]) -> dict[str, str]:
    return {
        str(item["offer_public_id"]): str(item["status"])
        for item in snapshot.get("offers", [])
        if item.get("offer_public_id")
    }


def validate_offer_sync_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("schema_version") != OFFER_SYNC_SNAPSHOT_SCHEMA_VERSION:
        raise SyncProbeError("unsupported offer sync snapshot schema_version")
    table_names = tuple(snapshot.get("evidence_tables") or ())
    if table_names != OFFER_SYNC_TABLES:
        raise SyncProbeError(f"offer sync snapshot evidence_tables mismatch: {table_names}")
    assert_no_messenger_tables_in_evidence(table_names)
    if not str(snapshot.get("server_mode") or "").strip():
        raise SyncProbeError("offer sync snapshot server_mode is required")
    if not str(snapshot.get("prefix") or "").strip():
        raise SyncProbeError("offer sync snapshot prefix is required")
    table_counts = snapshot.get("table_counts")
    if not isinstance(table_counts, dict):
        raise SyncProbeError("offer sync snapshot table_counts is required")
    for table_name in OFFER_SYNC_TABLES:
        if table_name not in table_counts:
            raise SyncProbeError(f"offer sync snapshot missing table count for {table_name}")
    if any(int(table_counts.get(table_name) or 0) < 0 for table_name in OFFER_SYNC_TABLES):
        raise SyncProbeError("offer sync snapshot table counts must be non-negative")
    return snapshot


def assert_offer_sync_snapshots_match(foreign_snapshot: dict[str, Any], iran_snapshot: dict[str, Any]) -> None:
    foreign = validate_offer_sync_snapshot(foreign_snapshot)
    iran = validate_offer_sync_snapshot(iran_snapshot)
    if foreign.get("prefix") != iran.get("prefix"):
        raise SyncProbeError("cross-server offer sync evidence prefixes differ")
    if foreign.get("table_counts") != iran.get("table_counts"):
        raise SyncProbeError(
            "cross-server offer sync table counts differ: "
            f"foreign={foreign.get('table_counts')} iran={iran.get('table_counts')}"
        )

    foreign_statuses = _snapshot_offer_statuses(foreign)
    iran_statuses = _snapshot_offer_statuses(iran)
    if set(foreign_statuses) != set(iran_statuses):
        raise SyncProbeError("cross-server offer public id sets differ")
    for public_id in sorted(foreign_statuses):
        foreign_status = foreign_statuses[public_id]
        iran_status = iran_statuses[public_id]
        if foreign_status != iran_status:
            raise SyncProbeError(
                f"cross-server offer status differs for {public_id}: foreign={foreign_status} iran={iran_status}"
            )
        if foreign_status in TERMINAL_OFFER_STATUSES and iran_status == OfferStatus.ACTIVE.value:
            raise SyncProbeError(f"terminal offer reactivated on iran for {public_id}")
        if iran_status in TERMINAL_OFFER_STATUSES and foreign_status == OfferStatus.ACTIVE.value:
            raise SyncProbeError(f"terminal offer reactivated on foreign for {public_id}")


def assert_sync_health_clean(payload: dict[str, Any], *, role: str) -> None:
    if payload.get("status") != "ok":
        raise SyncProbeError(f"{role} sync health status is not ok")
    if payload.get("redis_ok") is False:
        raise SyncProbeError(f"{role} sync health redis_ok is false")
    unsynced = int(payload.get("unsynced_change_log_count") or 0)
    queues = payload.get("redis_queues") or {}
    outbound = int(queues.get("sync:outbound") or 0)
    retry = int(queues.get("sync:retry") or 0)
    if unsynced or outbound or retry:
        raise SyncProbeError(
            f"{role} sync health is dirty: unsynced={unsynced} outbound={outbound} retry={retry}"
        )


def validate_cross_server_sync_evidence_artifact(
    artifact: dict[str, Any],
    *,
    accepted_lag_seconds: float,
) -> dict[str, Any]:
    if artifact.get("schema_version") != BOT_WEBAPP_SYNC_EVIDENCE_SCHEMA_VERSION:
        raise SyncProbeError("unsupported cross-server sync evidence schema_version")
    if accepted_lag_seconds <= 0:
        raise SyncProbeError("accepted_lag_seconds must be positive")
    checks = artifact.get("checks")
    if not isinstance(checks, dict):
        raise SyncProbeError("cross-server sync evidence checks are required")
    missing = sorted(set(REQUIRED_BOT_WEBAPP_SYNC_CHECKS) - set(checks))
    if missing:
        raise SyncProbeError(f"cross-server sync evidence missing checks: {', '.join(missing)}")
    for check_name in REQUIRED_BOT_WEBAPP_SYNC_CHECKS:
        check = checks.get(check_name) or {}
        if not bool(check.get("ok")):
            raise SyncProbeError(f"cross-server sync evidence check failed: {check_name}")
        duration = float(check.get("duration_seconds") or 0)
        if duration < 0:
            raise SyncProbeError(f"cross-server sync evidence check has negative duration: {check_name}")
        if duration > accepted_lag_seconds:
            raise SyncProbeError(
                f"cross-server sync evidence check {check_name} exceeded lag window: "
                f"{duration:.3f}s > {accepted_lag_seconds:.3f}s"
            )

    server_snapshots = artifact.get("server_snapshots") or {}
    sync_health = artifact.get("sync_health") or {}
    foreign_snapshot = server_snapshots.get("foreign")
    iran_snapshot = server_snapshots.get("iran")
    if not isinstance(foreign_snapshot, dict) or not isinstance(iran_snapshot, dict):
        raise SyncProbeError("cross-server sync evidence requires foreign and iran snapshots")
    assert_offer_sync_snapshots_match(foreign_snapshot, iran_snapshot)
    for role in ("foreign", "iran"):
        role_health = sync_health.get(role)
        if not isinstance(role_health, dict):
            raise SyncProbeError(f"cross-server sync evidence missing {role} sync health")
        assert_sync_health_clean(role_health, role=role)
    return artifact


def read_json_file(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SyncProbeError(f"JSON artifact must be an object: {path}")
    return data


def _where_or_false(*conditions):
    normalized = [condition for condition in conditions if condition is not None]
    if not normalized:
        return false()
    if len(normalized) == 1:
        return normalized[0]
    return or_(*normalized)


async def collect_offer_sync_evidence_snapshot(prefix: str) -> dict[str, Any]:
    normalized_prefix = str(prefix or "").strip()
    if not normalized_prefix:
        raise SyncProbeError("offer sync evidence prefix is required")
    pattern = f"{normalized_prefix}%"
    evidence_tables = offer_sync_evidence_tables()
    assert_no_messenger_tables_in_evidence(evidence_tables)

    async with AsyncSessionLocal() as db:
        offer_rows = (
            await db.execute(
                select(
                    Offer.id,
                    Offer.offer_public_id,
                    Offer.status,
                    Offer.home_server,
                    Offer.version_id,
                    Offer.remaining_quantity,
                )
                .where(Offer.notes.like(pattern))
                .order_by(Offer.offer_public_id)
            )
        ).all()
        offer_ids = [int(row.id) for row in offer_rows]
        offer_public_ids = [str(row.offer_public_id) for row in offer_rows if row.offer_public_id]
        offer_filter = OfferPublicationState.offer_public_id.in_(offer_public_ids) if offer_public_ids else None
        trade_filter = Trade.offer_id.in_(offer_ids) if offer_ids else None
        request_filter = _where_or_false(
            OfferRequest.local_offer_id.in_(offer_ids) if offer_ids else None,
            OfferRequest.offer_public_id.in_(offer_public_ids) if offer_public_ids else None,
        )
        publication_filter = _where_or_false(
            OfferPublicationState.offer_id.in_(offer_ids) if offer_ids else None,
            offer_filter,
        )

        trade_rows = (
            await db.execute(
                select(Trade.id, Trade.status, Trade.quantity, Trade.trade_number, Trade.offer_id).where(
                    trade_filter if trade_filter is not None else false()
                )
            )
        ).all()
        request_rows = (
            await db.execute(
                select(
                    OfferRequest.id,
                    OfferRequest.result_status,
                    OfferRequest.request_source_surface,
                    OfferRequest.offer_public_id,
                    OfferRequest.resulting_trade_id,
                ).where(request_filter)
            )
        ).all()
        publication_rows = (
            await db.execute(
                select(
                    OfferPublicationState.id,
                    OfferPublicationState.offer_public_id,
                    OfferPublicationState.surface,
                    OfferPublicationState.status,
                    OfferPublicationState.publication_owner_server,
                    OfferPublicationState.last_known_offer_status,
                ).where(publication_filter)
            )
        ).all()

    offers = [
        {
            "id": int(row.id),
            "offer_public_id": row.offer_public_id,
            "status": _enum_value(row.status),
            "home_server": row.home_server,
            "version_id": int(row.version_id or 0),
            "remaining_quantity": row.remaining_quantity,
        }
        for row in offer_rows
    ]
    trade_statuses = [_enum_value(row.status) for row in trade_rows]
    request_statuses = [_enum_value(row.result_status) for row in request_rows]
    publication_statuses = [_enum_value(row.status) for row in publication_rows]
    snapshot = {
        "schema_version": OFFER_SYNC_SNAPSHOT_SCHEMA_VERSION,
        "captured_at": utc_now(),
        "server_mode": settings.server_mode,
        "prefix": normalized_prefix,
        "evidence_tables": evidence_tables,
        "messenger_tables_included": [],
        "table_counts": {
            "offers": len(offer_rows),
            "trades": len(trade_rows),
            "offer_requests": len(request_rows),
            "offer_publication_states": len(publication_rows),
        },
        "offer_status_counts": _count_values([row.status for row in offer_rows]),
        "trade_status_counts": _count_values(trade_statuses),
        "offer_request_status_counts": _count_values(request_statuses),
        "offer_request_surface_counts": _count_values([row.request_source_surface for row in request_rows]),
        "publication_status_counts": _count_values(publication_statuses),
        "publication_surface_counts": _count_values([row.surface for row in publication_rows]),
        "completed_trade_quantity": sum(int(row.quantity or 0) for row in trade_rows if _enum_value(row.status) == "completed"),
        "offers": offers,
    }
    validate_offer_sync_snapshot(snapshot)
    return snapshot


async def offer_sync_evidence(args: argparse.Namespace) -> int:
    print_json(await collect_offer_sync_evidence_snapshot(args.prefix))
    return 0


async def validate_offer_sync_evidence_artifact(args: argparse.Namespace) -> int:
    artifact = read_json_file(args.artifact)
    validate_cross_server_sync_evidence_artifact(
        artifact,
        accepted_lag_seconds=float(args.accepted_lag_seconds),
    )
    print_json({"status": "ok", "artifact": str(args.artifact)})
    return 0


async def reconcile_publications(args: argparse.Namespace) -> int:
    send_offer_to_channel = None
    if args.repair and settings.server_mode == "foreign":
        try:
            from api.routers.offers import send_offer_to_channel as telegram_send_offer_to_channel
            send_offer_to_channel = telegram_send_offer_to_channel
        except Exception as exc:
            print_json(
                {
                    "status": "error",
                    "error": "telegram_send_callback_unavailable",
                    "error_type": type(exc).__name__,
                }
            )
            return 2

    redis_client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0, decode_responses=True)
    try:
        gate = await load_active_publication_gate(redis_client)
    finally:
        await redis_client.aclose()

    async with AsyncSessionLocal() as db:
        report = await reconcile_offer_publications(
            db,
            server_mode=settings.server_mode,
            dry_run=not args.repair,
            limit=args.limit,
            send_offer_to_channel=send_offer_to_channel,
            allow_active_publication=not bool(gate.get("enabled")),
        )
        report["active_publication_gate"] = gate
    print_json(report)
    return 1 if report.get("status") in {"partial", "gated"} else 0


async def publication_gate_status(args: argparse.Namespace) -> int:
    redis_client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0, decode_responses=True)
    try:
        gate = await load_active_publication_gate(redis_client)
    finally:
        await redis_client.aclose()
    print_json({"status": "ok", "active_publication_gate": gate})
    return 0


async def enable_publication_gate(args: argparse.Namespace) -> int:
    cutoff = parse_probe_datetime(args.cutoff) if args.cutoff else None
    redis_client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0, decode_responses=True)
    try:
        gate = await enable_active_publication_gate(
            outage_class=args.outage_class,
            server_mode=settings.server_mode,
            note=args.note,
            cutoff=cutoff,
            redis_client=redis_client,
        )
    finally:
        await redis_client.aclose()
    print_json({"status": "ok", "active_publication_gate": gate})
    return 0


async def clear_publication_gate(args: argparse.Namespace) -> int:
    redis_client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0, decode_responses=True)
    try:
        gate_cleared = await clear_active_publication_gate(redis_client)
    finally:
        await redis_client.aclose()
    print_json({"status": "ok", "gate_cleared": gate_cleared})
    return 0


async def finalize_outage_recovery_command(args: argparse.Namespace) -> int:
    cutoff = parse_probe_datetime(args.cutoff)
    redis_client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0, decode_responses=True)
    if args.repair:
        setup_event_listeners()
    try:
        async with AsyncSessionLocal() as db:
            report = await finalize_outage_recovery(
                db,
                outage_class=args.outage_class,
                cutoff=cutoff,
                server_mode=settings.server_mode,
                dry_run=not args.repair,
                limit=args.limit,
                redis_client=redis_client,
            )
    finally:
        await redis_client.aclose()
    print_json(report)
    return 1 if report.get("status") in {"gated", "partial_finalized_pending_sync"} else 0


async def insert_probe(args: argparse.Namespace) -> int:
    setup_event_listeners()
    probe_date = parse_probe_date(args.date)
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(MarketScheduleOverride).where(
                MarketScheduleOverride.date == probe_date,
                MarketScheduleOverride.note == args.note,
            )
        )
        await db.commit()
        row = MarketScheduleOverride(
            date=probe_date,
            override_type=MarketScheduleOverrideType.OPEN_ALL_DAY,
            note=args.note,
            created_by_user_id=None,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        print_json({"status": "ok", "id": row.id, "date": row.date, "note": row.note})
    return 0


async def delete_probe(args: argparse.Namespace) -> int:
    setup_event_listeners()
    probe_date = parse_probe_date(args.date)
    deleted_ids: list[int] = []
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(MarketScheduleOverride).where(
                MarketScheduleOverride.date == probe_date,
                MarketScheduleOverride.note == args.note,
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            deleted_ids.append(int(row.id))
            await db.delete(row)
        await db.commit()
    print_json({"status": "ok", "deleted_count": len(deleted_ids), "deleted_ids": deleted_ids})
    return 0


async def exists_probe(args: argparse.Namespace) -> int:
    probe_date = parse_probe_date(args.date)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(MarketScheduleOverride.id).where(
                MarketScheduleOverride.date == probe_date,
                MarketScheduleOverride.note == args.note,
            )
        )
        ids = [int(item) for item in result.scalars().all()]
    print_json({"status": "ok", "exists": bool(ids), "count": len(ids), "ids": ids})
    return 0


async def cleanup_probes(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        row_result = await db.execute(
            text("DELETE FROM market_schedule_overrides WHERE note LIKE :pattern"),
            {"pattern": f"{args.note_prefix}%"},
        )
        invalid_result = await db.execute(
            text("DELETE FROM change_log WHERE hash LIKE :pattern"),
            {"pattern": f"p6_invalid_{args.stamp}%"},
        )
        await db.commit()
    print_json(
        {
            "status": "ok",
            "deleted_probe_rows": int(row_result.rowcount or 0),
            "deleted_invalid_change_logs": int(invalid_result.rowcount or 0),
        }
    )
    return 0


async def insert_invalid_change_log(args: argparse.Namespace) -> int:
    record_id = int(args.record_id)
    payload = {
        "id": record_id,
        "chat_id": record_id,
        "user_id": record_id,
        "role": "member",
        "membership_status": "ACTIVE",
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    entry = ChangeLog(
        operation="INSERT",
        table_name="chat_members",
        record_id=record_id,
        data=payload,
        timestamp=utc_now(),
        hash=f"p6_invalid_{args.stamp}_{digest[:16]}",
        synced=False,
        verified=False,
    )
    async with AsyncSessionLocal() as db:
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
    print_json({"status": "ok", "change_log_id": entry.id, "record_id": record_id, "hash": entry.hash})
    return 0


async def change_log_status(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ChangeLog).where(ChangeLog.id == int(args.change_log_id)))
        entry = result.scalar_one_or_none()
    print_json(
        {
            "status": "ok",
            "exists": entry is not None,
            "change_log_id": int(args.change_log_id),
            "synced": bool(entry.synced) if entry else None,
            "verified": bool(entry.verified) if entry else None,
            "table_name": entry.table_name if entry else None,
        }
    )
    return 0


async def delete_change_log(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(delete(ChangeLog).where(ChangeLog.id == int(args.change_log_id)))
        await db.commit()
    print_json({"status": "ok", "deleted_count": int(result.rowcount or 0)})
    return 0


async def resync_table(args: argparse.Namespace) -> int:
    if not settings.dev_api_key:
        print_json({"status": "error", "message": "DEV_API_KEY is not configured"})
        return 2
    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        response = await client.post(
            "http://127.0.0.1:8000/api/sync/resync",
            params={"limit": args.limit, "table_filter": args.table},
            headers={"X-Dev-Api-Key": settings.dev_api_key},
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {"status": "invalid-json", "body_sha256": hashlib.sha256(response.content).hexdigest()[:16]}
    print_json({"status_code": response.status_code, "payload": payload})
    return 0 if response.status_code == 200 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one cross-server sync benchmark helper inside an app container.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health")

    offer_evidence_parser = subparsers.add_parser("offer-sync-evidence")
    offer_evidence_parser.add_argument("--prefix", required=True)

    validate_evidence_parser = subparsers.add_parser("validate-offer-sync-evidence-artifact")
    validate_evidence_parser.add_argument("--artifact", required=True)
    validate_evidence_parser.add_argument("--accepted-lag-seconds", type=float, default=2.0)

    insert_parser = subparsers.add_parser("insert-probe")
    insert_parser.add_argument("--date", required=True)
    insert_parser.add_argument("--note", required=True)

    delete_parser = subparsers.add_parser("delete-probe")
    delete_parser.add_argument("--date", required=True)
    delete_parser.add_argument("--note", required=True)

    exists_parser = subparsers.add_parser("exists-probe")
    exists_parser.add_argument("--date", required=True)
    exists_parser.add_argument("--note", required=True)

    cleanup_parser = subparsers.add_parser("cleanup-probes")
    cleanup_parser.add_argument("--note-prefix", required=True)
    cleanup_parser.add_argument("--stamp", required=True)

    invalid_parser = subparsers.add_parser("insert-invalid-change-log")
    invalid_parser.add_argument("--stamp", required=True)
    invalid_parser.add_argument("--record-id", required=True)

    status_parser = subparsers.add_parser("change-log-status")
    status_parser.add_argument("--change-log-id", required=True)

    delete_log_parser = subparsers.add_parser("delete-change-log")
    delete_log_parser.add_argument("--change-log-id", required=True)

    resync_parser = subparsers.add_parser("resync-table")
    resync_parser.add_argument("--table", required=True)
    resync_parser.add_argument("--limit", type=int, default=100)

    reconcile_parser = subparsers.add_parser("reconcile-publications")
    reconcile_parser.add_argument("--limit", type=int, default=50)
    reconcile_parser.add_argument("--repair", action="store_true")

    subparsers.add_parser("publication-gate-status")

    enable_gate_parser = subparsers.add_parser("enable-publication-gate")
    enable_gate_parser.add_argument("--outage-class", choices=("medium", "long"), required=True)
    enable_gate_parser.add_argument("--cutoff", help="ISO timestamp for the recovery cutoff.")
    enable_gate_parser.add_argument("--note")

    subparsers.add_parser("clear-publication-gate")

    finalize_parser = subparsers.add_parser("finalize-outage-recovery")
    finalize_parser.add_argument("--outage-class", choices=("medium", "long"), required=True)
    finalize_parser.add_argument("--cutoff", required=True, help="ISO timestamp; active home offers created before this are finalized.")
    finalize_parser.add_argument("--limit", type=int, default=100)
    finalize_parser.add_argument("--repair", action="store_true")
    return parser


async def main_async() -> int:
    args = build_parser().parse_args()
    handlers = {
        "health": health,
        "offer-sync-evidence": offer_sync_evidence,
        "validate-offer-sync-evidence-artifact": validate_offer_sync_evidence_artifact,
        "insert-probe": insert_probe,
        "delete-probe": delete_probe,
        "exists-probe": exists_probe,
        "cleanup-probes": cleanup_probes,
        "insert-invalid-change-log": insert_invalid_change_log,
        "change-log-status": change_log_status,
        "delete-change-log": delete_change_log,
        "resync-table": resync_table,
        "reconcile-publications": reconcile_publications,
        "publication-gate-status": publication_gate_status,
        "enable-publication-gate": enable_publication_gate,
        "clear-publication-gate": clear_publication_gate,
        "finalize-outage-recovery": finalize_outage_recovery_command,
    }
    return await handlers[args.command](args)


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
