#!/usr/bin/env python3
"""Two-process scratch harness for split Telegram runtime.

Never talks to Telegram. Prints no secrets.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.services.telegram_delivery_queue_service import (
    claim_next_telegram_delivery_job,
    enqueue_telegram_delivery_job,
)
from core.services.telegram_publisher_dispatch_service import (
    acknowledge_claimed_telegram_publisher_dispatch_locally,
    claim_next_telegram_publisher_dispatch_command,
    get_or_create_telegram_publisher_dispatch_command,
)
from core.telegram_bot_runtime_role import (
    role_owns_local_ack_surface,
    role_owns_queue_executor,
    select_polling_bot_identities,
    select_queue_execution_bot_identities,
)
from core.telegram_bot_runtime_topology import TELEGRAM_BOT_RUNTIME_ALL_IDENTITIES
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.telegram_delivery_queue_owner import (
    TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY,
    TelegramDeliveryQueueAlreadyOwnedError,
    acquire_telegram_delivery_queue_owner,
)
from core.telegram_delivery_queue_wakeup import (
    TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL,
    wait_for_telegram_wakeup,
)
from core.utils import utc_now


def _mark_ready(path: str | None) -> None:
    if path:
        Path(path).write_text("ready\n", encoding="utf-8")


async def _wait_release(path: str | None, timeout: float = 90.0) -> None:
    if path:
        deadline = time.time() + timeout
        target = Path(path)
        while time.time() < deadline:
            if target.exists():
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("split_runtime_release_timeout")
    await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)


def _database_url() -> str:
    raw = str(os.environ.get("TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL") or "").strip()
    if not raw:
        raise SystemExit(2)
    return (
        make_url(raw)
        .set(drivername="postgresql+asyncpg")
        .render_as_string(hide_password=False)
    )


def _payload(**values):
    return json.dumps(values, sort_keys=True)


async def _prepare(db) -> None:
    await db.execute(
        text(
            "SELECT set_config('trading_bot.mutation_capability', "
            "'foreign_writer', true)"
        )
    )
    await db.execute(text("SELECT set_config('trading_bot.physical_site', 'bot_fi', true)"))
    await db.execute(text("SELECT set_config('trading_bot.dr_producer_epoch', '1', true)"))


async def _count_owners(engine) -> int:
    class_id = (TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY >> 32) & 0xFFFFFFFF
    object_id = TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY & 0xFFFFFFFF
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'advisory' "
                "AND classid = :class_id AND objid = :object_id "
                "AND objsubid = 1 AND granted"
            ),
            {"class_id": class_id, "object_id": object_id},
        )
        return int(result.scalar_one())


async def _enqueue(db, *, key: str, action: TelegramDeliveryAction, identity: str):
    await _prepare(db)
    is_edit = action != TelegramDeliveryAction.OFFER_PUBLISH
    kwargs = {}
    if is_edit:
        kwargs["source_order_at"] = utc_now()
    result = await enqueue_telegram_delivery_job(
        db,
        current_server="foreign",
        feeder=TelegramFeederKind.OFFER_EDIT if is_edit else TelegramFeederKind.OFFER_CONTROL,
        source_natural_id=key,
        source_version=1 if not is_edit else 2,
        action=action,
        bot_identity=identity,
        destination_key="channel:-1001234567890",
        destination_class=TelegramDestinationClass.CHANNEL,
        method="sendMessage" if not is_edit else "editMessageText",
        payload={"chat_id": -1001234567890, "text": key},
        template_version="split-runtime-v1",
        **kwargs,
    )
    return result.job


async def action_enqueue(session_factory, args) -> dict:
    async with session_factory() as db:
        job = await _enqueue(
            db,
            key=args.source_key,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            identity=args.publisher,
        )
        command = await get_or_create_telegram_publisher_dispatch_command(
            db,
            current_server="foreign",
            job=job,
            publisher_bot_identity=args.publisher,
            now=utc_now(),
        )
        await db.commit()
        return {
            "job_id": int(job.id),
            "command_id": str(command.command_id),
            "publisher": args.publisher,
            "created": True,
        }


async def action_primary_ack(session_factory, args) -> dict:
    if not role_owns_local_ack_surface(args.role):
        return {"error": "role_must_not_local_ack", "acquired": False}
    started = time.perf_counter()
    async with session_factory() as db:
        await _prepare(db)
        lease = await claim_next_telegram_publisher_dispatch_command(
            db,
            current_server="foreign",
            lease_seconds=30,
            now=utc_now(),
        )
        if lease is None:
            await db.rollback()
            return {"error": "no_command_to_ack", "acked": False}
        await db.commit()
    async with session_factory() as db:
        await _prepare(db)
        acked = await acknowledge_claimed_telegram_publisher_dispatch_locally(
            db,
            current_server="foreign",
            command_id=str(lease.command.command_id),
            lease_token=int(lease.lease_token),
            now=utc_now(),
        )
        if acked:
            await db.commit()
        else:
            await db.rollback()
    return {
        "acked": bool(acked),
        "command_id": str(lease.command.command_id),
        "lease_token": int(lease.lease_token),
        "enqueue_to_local_ack_seconds": time.perf_counter() - started,
        "provider_calls": 0,
    }


async def action_central_ping(session_factory, _args) -> dict:
    started = time.perf_counter()
    async with session_factory() as db:
        await db.execute(text("SELECT 1"))
    return {
        "central_ping": True,
        "central_interaction_seconds": time.perf_counter() - started,
        "provider_calls": 0,
    }


async def _wait_for_pg_notify(
    engine,
    identity: str,
    timeout: float,
    armed: asyncio.Event,
) -> bool:
    event = asyncio.Event()

    def handler(_connection, _process_id, channel, payload):
        if channel == TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL and str(payload or "") == identity:
            event.set()

    async with engine.connect() as connection:
        raw_connection = await connection.get_raw_connection()
        driver = getattr(raw_connection, "driver_connection", None)
        if driver is None:
            armed.set()
            return False
        await driver.add_listener(TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL, handler)
        armed.set()
        try:
            return await wait_for_telegram_wakeup(event, timeout_seconds=timeout)
        finally:
            try:
                await driver.remove_listener(
                    TELEGRAM_DELIVERY_QUEUE_WAKEUP_CHANNEL,
                    handler,
                )
            except Exception:
                pass


async def action_executor_consume(engine, session_factory, args) -> dict:
    if not role_owns_queue_executor(args.role):
        return {"error": "primary_must_not_acquire_queue_owner", "acquired": False}
    lease = None
    try:
        lease = await acquire_telegram_delivery_queue_owner(engine)
    except TelegramDeliveryQueueAlreadyOwnedError:
        print(
            _payload(
                acquired=False,
                error="telegram_delivery_queue_process_owner_already_active",
                provider_calls=0,
                polling_started=False,
            ),
            flush=True,
        )
        return {"skip_print": True, "returncode": 3}

    async def _ready(*, wakeup_armed: bool) -> None:
        print(
            _payload(
                acquired=True,
                polling_identities=list(
                    select_polling_bot_identities(
                        args.role, TELEGRAM_BOT_RUNTIME_ALL_IDENTITIES
                    )
                ),
                queue_execution_identities=list(
                    select_queue_execution_bot_identities(
                        args.role, TELEGRAM_BOT_RUNTIME_ALL_IDENTITIES
                    )
                ),
                polling_started=False,
                provider_calls=0,
                wakeup_armed=wakeup_armed,
                owner_count=await _count_owners(engine),
            ),
            flush=True,
        )
        _mark_ready(args.ready_file)

    try:
        wakeup_received = False
        if args.wait_wakeup:
            armed = asyncio.Event()
            wait_task = asyncio.create_task(
                _wait_for_pg_notify(engine, args.publisher, args.timeout, armed)
            )
            await armed.wait()
            await _ready(wakeup_armed=True)
            wakeup_received = bool(await wait_task)
        elif args.wait_stdin:
            await _ready(wakeup_armed=False)
            await _wait_release(args.release_file)
        else:
            await _ready(wakeup_armed=False)
        claimed_at = time.perf_counter()
        async with session_factory() as db:
            await _prepare(db)
            job = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity=args.publisher,
                worker_id="split-runtime-executor",
                request_timeout_seconds=10,
                lease_seconds=30,
            )
            if job is None:
                await db.rollback()
                return {
                    "acquired": True,
                    "claimed": False,
                    "fake_sent": False,
                    "wakeup_received": wakeup_received,
                    "provider_calls": 0,
                    "owner_count": await _count_owners(engine),
                }
            if args.delay_send > 0:
                await asyncio.sleep(args.delay_send)
            job.state = TelegramDeliveryState.SENT
            job.sent_at = utc_now()
            job.terminal_at = utc_now()
            job.provider_response = {"ok": True, "result": {"message_id": 9001}, "fake": True}
            job.telegram_message_id = 9001
            await db.commit()
            return {
                "acquired": True,
                "claimed": True,
                "fake_sent": True,
                "job_id": int(job.id),
                "publisher": str(job.bot_identity),
                "wakeup_received": wakeup_received,
                "claim_to_fake_provider_seconds": time.perf_counter() - claimed_at,
                "provider_calls": 1,
                "owner_count": await _count_owners(engine),
            }
    finally:
        if lease is not None:
            await lease.close()


async def action_sticky_edit(session_factory, args) -> dict:
    async with session_factory() as db:
        first = await _enqueue(
            db,
            key=args.source_key,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            identity=args.publisher,
        )
        first.state = TelegramDeliveryState.SENT
        first.sent_at = utc_now()
        first.terminal_at = utc_now()
        await db.commit()
    async with session_factory() as db:
        edit = await _enqueue(
            db,
            key=args.source_key,
            action=TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT,
            identity=args.publisher,
        )
        await db.commit()
        return {
            "first_publisher": args.publisher,
            "edit_publisher": str(edit.bot_identity),
            "sticky": str(edit.bot_identity) == args.publisher,
        }


async def action_reset(session_factory, _args) -> dict:
    async with session_factory() as db:
        await _prepare(db)
        database_name = str((await db.execute(text("SELECT current_database()"))).scalar_one())
        if not database_name.startswith("telegram_queue_stage3_"):
            raise RuntimeError("split harness reset is allowed only on isolated scratch databases")
        await db.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        )
        await db.execute(
            text(
                "TRUNCATE TABLE telegram_publisher_dispatch_commands, "
                "telegram_delivery_jobs RESTART IDENTITY CASCADE"
            )
        )
        await db.commit()
    return {"reset": True}


async def action_count_owners(engine, _args) -> dict:
    return {"owner_count": await _count_owners(engine)}


async def action_hold_owner(engine, args) -> dict:
    if not role_owns_queue_executor(args.role):
        print(_payload(error="primary_must_not_acquire_queue_owner", acquired=False), flush=True)
        return {"skip_print": True, "returncode": 3}
    try:
        lease = await acquire_telegram_delivery_queue_owner(engine)
    except TelegramDeliveryQueueAlreadyOwnedError:
        print(
            _payload(
                acquired=False,
                error="telegram_delivery_queue_process_owner_already_active",
                polling_started=False,
                provider_calls=0,
            ),
            flush=True,
        )
        return {"skip_print": True, "returncode": 3}
    print(
        _payload(
            acquired=True,
            polling_started=False,
            provider_calls=0,
            owner_count=await _count_owners(engine),
        ),
        flush=True,
    )
    _mark_ready(args.ready_file)
    try:
        await _wait_release(args.release_file)
        return {"skip_print": True, "returncode": 0}
    finally:
        await lease.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=("all", "primary", "executor"))
    parser.add_argument(
        "--action",
        required=True,
        choices=(
            "enqueue",
            "primary-ack",
            "central-ping",
            "executor-consume",
            "sticky-edit",
            "count-owners",
            "hold-owner",
            "reset",
        ),
    )
    parser.add_argument("--source-key", default="split-runtime-job")
    parser.add_argument("--publisher", default="publisher_1")
    parser.add_argument("--wait-wakeup", action="store_true")
    parser.add_argument("--wait-stdin", action="store_true")
    parser.add_argument("--ready-file")
    parser.add_argument("--release-file")
    parser.add_argument("--delay-send", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        if args.action == "enqueue":
            payload = await action_enqueue(session_factory, args)
        elif args.action == "primary-ack":
            payload = await action_primary_ack(session_factory, args)
        elif args.action == "central-ping":
            payload = await action_central_ping(session_factory, args)
        elif args.action == "executor-consume":
            payload = await action_executor_consume(engine, session_factory, args)
        elif args.action == "sticky-edit":
            payload = await action_sticky_edit(session_factory, args)
        elif args.action == "count-owners":
            payload = await action_count_owners(engine, args)
        elif args.action == "reset":
            payload = await action_reset(session_factory, args)
        else:
            payload = await action_hold_owner(engine, args)
        if payload.get("skip_print"):
            return int(payload.get("returncode") or 0)
        print(_payload(**{key: value for key, value in payload.items() if key != "skip_print"}), flush=True)
        return 3 if payload.get("error") else 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
