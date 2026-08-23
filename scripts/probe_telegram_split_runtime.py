#!/usr/bin/env python3
"""Test-only subprocess probe for split Telegram runtime ownership.

Never talks to Telegram. Prints no secrets.
Supported invocation: python -m scripts.probe_telegram_split_runtime
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from core.telegram_bot_runtime_role import (
    role_owns_local_ack_surface,
    role_owns_otp_worker,
    role_owns_primary_surface,
    role_owns_publisher_surface,
    role_owns_queue_executor,
    select_polling_bot_identities,
    select_queue_execution_bot_identities,
)
from core.telegram_bot_runtime_topology import (
    TELEGRAM_BOT_RUNTIME_ALL_IDENTITIES,
    describe_telegram_bot_runtime_topology,
)
from core.telegram_central_poller_owner import TELEGRAM_CENTRAL_POLLER_LOCK_KEY
from core.telegram_delivery_queue_owner import (
    TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY,
)
from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES


def _wait_release(path: str | None, timeout: float = 90.0) -> None:
    if path:
        deadline = time.time() + timeout
        target = Path(path)
        while time.time() < deadline:
            if target.exists():
                return
            time.sleep(0.05)
        raise SystemExit(4)
    sys.stdin.readline()


def _queue_decision():
    # Keep runtime settings out of module import so `--help` remains a
    # secret-free diagnostic even when the selected env file contains
    # deploy-only keys that the application Settings model does not accept.
    from core.telegram_delivery_runtime_policy import (
        TelegramDeliveryRuntimeDecision,
        TelegramDeliveryRuntimeMode,
    )

    return TelegramDeliveryRuntimeDecision(
        mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
        legacy_workers_enabled=False,
        queue_worker_enabled=True,
    )


def _acquire(url: str, lock_key: int):
    import psycopg2

    connection = psycopg2.connect(url)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s), pg_backend_pid()",
            (lock_key,),
        )
        acquired, backend_pid = cursor.fetchone()
    return bool(acquired), int(backend_pid or 0), connection


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=("all", "primary", "executor"))
    parser.add_argument("--split-enabled", action="store_true")
    parser.add_argument("--hold", action="store_true")
    parser.add_argument("--ready-file")
    parser.add_argument("--release-file")
    parser.add_argument("--acquire-queue", action="store_true")
    parser.add_argument("--acquire-central", action="store_true")
    args = parser.parse_args()
    url = str(os.environ.get("TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL") or "")
    acquired = False
    central_acquired = False
    backend_pid = None
    central_backend_pid = None
    error = None
    connections = []
    try:
        if args.acquire_queue:
            if not role_owns_queue_executor(args.role):
                error = "primary_must_not_acquire_queue_owner"
            elif not url:
                return 2
            else:
                acquired, backend_pid, connection = _acquire(
                    url, TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY
                )
                connections.append(connection)
                if not acquired:
                    error = "telegram_delivery_queue_process_owner_already_active"
                    payload = _payload(
                        args,
                        acquired=False,
                        central_acquired=False,
                        backend_pid=backend_pid,
                        central_backend_pid=None,
                        error=error,
                    )
                    print(json.dumps(payload, sort_keys=True), flush=True)
                    return 3
        if error is None and args.acquire_central:
            if not role_owns_primary_surface(args.role):
                error = "executor_must_not_acquire_central_poller"
            elif not url:
                return 2
            else:
                central_acquired, central_backend_pid, connection = _acquire(
                    url, TELEGRAM_CENTRAL_POLLER_LOCK_KEY
                )
                connections.append(connection)
                if not central_acquired:
                    error = "telegram_central_poller_already_active"
                    payload = _payload(
                        args,
                        acquired=acquired,
                        central_acquired=False,
                        backend_pid=backend_pid,
                        central_backend_pid=central_backend_pid,
                        error=error,
                    )
                    print(json.dumps(payload, sort_keys=True), flush=True)
                    return 3
        if args.acquire_queue or args.acquire_central:
            if error is not None:
                payload = _payload(
                    args,
                    acquired=acquired,
                    central_acquired=central_acquired,
                    backend_pid=backend_pid,
                    central_backend_pid=central_backend_pid,
                    error=error,
                )
                print(json.dumps(payload, sort_keys=True), flush=True)
                return 3
            payload = _payload(
                args,
                acquired=acquired,
                central_acquired=central_acquired,
                backend_pid=backend_pid,
                central_backend_pid=central_backend_pid,
                error=None,
            )
            print(json.dumps(payload, sort_keys=True), flush=True)
            if args.ready_file:
                Path(args.ready_file).write_text("ready\n", encoding="utf-8")
            if args.hold:
                _wait_release(args.release_file)
            return 0
    finally:
        for connection in connections:
            try:
                connection.close()
            except Exception:
                pass

    payload = _payload(
        args,
        acquired=acquired,
        central_acquired=central_acquired,
        backend_pid=backend_pid,
        central_backend_pid=central_backend_pid,
        error=error,
    )
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0 if error is None else 3


def _payload(
    args,
    *,
    acquired,
    central_acquired,
    backend_pid,
    central_backend_pid,
    error,
):
    report = describe_telegram_bot_runtime_topology(
        role=args.role,
        split_enabled=bool(args.split_enabled),
        queue_owner_present=acquired if args.role != "primary" else False,
    )
    return {
        "role": args.role,
        "split_enabled": bool(args.split_enabled),
        "acquired": bool(acquired),
        "acquired_central": bool(central_acquired),
        "backend_pid": backend_pid,
        "central_backend_pid": central_backend_pid,
        "error": error,
        "owns_queue_executor": role_owns_queue_executor(args.role),
        "owns_otp_worker": role_owns_otp_worker(args.role),
        "owns_local_ack": role_owns_local_ack_surface(args.role),
        "owns_primary_polling": role_owns_primary_surface(args.role),
        "owns_publisher_polling": role_owns_publisher_surface(args.role),
        "polling_identities": list(
            select_polling_bot_identities(args.role, TELEGRAM_BOT_RUNTIME_ALL_IDENTITIES)
        ),
        "queue_execution_identities": list(
            select_queue_execution_bot_identities(
                args.role, TELEGRAM_BOT_RUNTIME_ALL_IDENTITIES
            )
        ),
        "publisher_identities": list(TELEGRAM_PUBLISHER_IDENTITIES),
        "topology": report.as_dict(),
        "runtime_mode": _queue_decision().mode.value,
    }


if __name__ == "__main__":
    raise SystemExit(main())
