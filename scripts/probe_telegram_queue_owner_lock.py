#!/usr/bin/env python3
"""Test-only subprocess probe for the Telegram queue singleton lock."""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg2

from core.telegram_delivery_queue_owner import (
    TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold", action="store_true")
    args = parser.parse_args()
    url = str(os.environ.get("TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL") or "")
    if not url:
        return 2
    connection = psycopg2.connect(url)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s), pg_backend_pid()",
                (TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY,),
            )
            acquired, backend_pid = cursor.fetchone()
        print(
            json.dumps(
                {"acquired": bool(acquired), "backend_pid": int(backend_pid)},
                sort_keys=True,
            ),
            flush=True,
        )
        if not acquired:
            return 3
        if args.hold:
            sys.stdin.readline()
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
