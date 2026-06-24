#!/usr/bin/env python3
"""Operate production full-matrix isolation gates.

This tool only changes Redis runtime keys. It never revokes sessions, disables
users, or writes database rows, so disabling isolation restores normal WebApp
access without requiring users to log in again.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import redis.asyncio as redis
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.production_test_isolation import (
    REDIS_KEY_ALLOW_ACCOUNT_PREFIXES,
    REDIS_KEY_ALLOW_MOBILE_PREFIXES,
    REDIS_KEY_ALLOW_USER_IDS,
    REDIS_KEY_ENABLED,
    REDIS_KEY_REASON,
)
from models.user import User


ISOLATION_KEYS = (
    REDIS_KEY_ENABLED,
    REDIS_KEY_REASON,
    REDIS_KEY_ALLOW_USER_IDS,
    REDIS_KEY_ALLOW_ACCOUNT_PREFIXES,
    REDIS_KEY_ALLOW_MOBILE_PREFIXES,
)


def _json_default(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default))


def normalize_unique_strings(values: list[str] | None) -> list[str]:
    return sorted({str(value).strip() for value in (values or []) if str(value).strip()})


def normalize_unique_ints(values: list[int] | None) -> list[int]:
    return sorted({int(value) for value in (values or []) if int(value) > 0})


async def redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


async def resolve_account_names(account_names: list[str]) -> tuple[list[int], list[str]]:
    if not account_names:
        return [], []

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(User.id, User.account_name).where(User.account_name.in_(account_names))
            )
        ).all()

    found = {str(row.account_name): int(row.id) for row in rows}
    missing = [account_name for account_name in account_names if account_name not in found]
    return sorted(found.values()), missing


async def get_status() -> dict[str, Any]:
    client = await redis_client()
    try:
        enabled = await client.get(REDIS_KEY_ENABLED)
        reason = await client.get(REDIS_KEY_REASON)
        allow_user_ids = sorted(int(item) for item in await client.smembers(REDIS_KEY_ALLOW_USER_IDS))
        allow_account_prefixes = sorted(await client.smembers(REDIS_KEY_ALLOW_ACCOUNT_PREFIXES))
        allow_mobile_prefixes = sorted(await client.smembers(REDIS_KEY_ALLOW_MOBILE_PREFIXES))
        ttl = {key: await client.ttl(key) for key in ISOLATION_KEYS}
    finally:
        await client.aclose()

    return {
        "status": "ok",
        "environment": settings.environment,
        "server_mode": settings.server_mode,
        "enabled": str(enabled or "").strip().lower() in {"1", "true", "yes", "on", "enabled"},
        "reason": reason,
        "allow_user_ids": allow_user_ids,
        "allow_account_prefixes": allow_account_prefixes,
        "allow_mobile_prefixes": allow_mobile_prefixes,
        "ttl_seconds": ttl,
    }


async def enable(args: argparse.Namespace) -> dict[str, Any]:
    if settings.environment == "production" and not args.yes_production:
        raise SystemExit("refusing to enable production isolation without --yes-production")

    explicit_user_ids = normalize_unique_ints(args.allow_user_id)
    account_names = normalize_unique_strings(args.allow_account_name)
    account_user_ids, missing_account_names = await resolve_account_names(account_names)
    if missing_account_names:
        raise SystemExit(f"account names not found: {', '.join(missing_account_names)}")

    allow_user_ids = normalize_unique_ints([*explicit_user_ids, *account_user_ids])
    allow_account_prefixes = normalize_unique_strings(args.allow_account_prefix)
    allow_mobile_prefixes = normalize_unique_strings(args.allow_mobile_prefix)

    if not allow_user_ids and not allow_account_prefixes and not allow_mobile_prefixes:
        raise SystemExit("refusing to enable isolation without at least one allow criterion")

    client = await redis_client()
    try:
        await client.delete(
            REDIS_KEY_ALLOW_USER_IDS,
            REDIS_KEY_ALLOW_ACCOUNT_PREFIXES,
            REDIS_KEY_ALLOW_MOBILE_PREFIXES,
        )
        await client.set(REDIS_KEY_ENABLED, "1")
        await client.set(REDIS_KEY_REASON, str(args.reason or "").strip())
        if allow_user_ids:
            await client.sadd(REDIS_KEY_ALLOW_USER_IDS, *[str(user_id) for user_id in allow_user_ids])
        if allow_account_prefixes:
            await client.sadd(REDIS_KEY_ALLOW_ACCOUNT_PREFIXES, *allow_account_prefixes)
        if allow_mobile_prefixes:
            await client.sadd(REDIS_KEY_ALLOW_MOBILE_PREFIXES, *allow_mobile_prefixes)
        if args.ttl_seconds:
            ttl_seconds = max(60, int(args.ttl_seconds))
            for key in ISOLATION_KEYS:
                await client.expire(key, ttl_seconds)
    finally:
        await client.aclose()

    status = await get_status()
    status.update({"action": "enabled", "resolved_account_names": account_names})
    return status


async def disable(_: argparse.Namespace) -> dict[str, Any]:
    client = await redis_client()
    try:
        deleted = int(await client.delete(*ISOLATION_KEYS) or 0)
    finally:
        await client.aclose()
    status = await get_status()
    status.update({"action": "disabled", "deleted_keys": deleted})
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status")

    enable_parser = subparsers.add_parser("enable")
    enable_parser.add_argument("--allow-user-id", type=int, action="append", default=[])
    enable_parser.add_argument("--allow-account-name", action="append", default=[])
    enable_parser.add_argument("--allow-account-prefix", action="append", default=[])
    enable_parser.add_argument("--allow-mobile-prefix", action="append", default=[])
    enable_parser.add_argument("--reason", default="production_full_matrix")
    enable_parser.add_argument("--ttl-seconds", type=int, default=None)
    enable_parser.add_argument("--yes-production", action="store_true")

    subparsers.add_parser("disable")
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        payload = await get_status()
    elif args.command == "enable":
        payload = await enable(args)
    elif args.command == "disable":
        payload = await disable(args)
    else:  # pragma: no cover
        raise SystemExit(f"unsupported command: {args.command}")
    print_json(payload)
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
