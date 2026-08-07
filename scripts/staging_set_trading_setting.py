#!/usr/bin/env python3
"""Read or set one trading_settings row inside a staging app container.

Used by the combined-matrix queue wave to temporarily raise
``offer_expiry_minutes`` so queued offers survive long enough to be published
during peak-load tests, then restore the original value afterwards.
Refuses to run outside the staging environment.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


async def _run(args: argparse.Namespace) -> dict[str, object]:
    from core.config import settings
    from core.db import AsyncSessionLocal
    from sqlalchemy import text

    environment = (getattr(settings, "environment", "") or "").strip().lower()
    if environment != "staging":
        raise RuntimeError(f"refuses non-staging environment={environment!r}")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT value FROM trading_settings WHERE key = :key"),
            {"key": args.key},
        )
        row = result.first()
        previous = row[0] if row is not None else None

        applied = previous
        if args.set is not None:
            if row is None:
                await session.execute(
                    text("INSERT INTO trading_settings (key, value) VALUES (:key, :value)"),
                    {"key": args.key, "value": str(args.set)},
                )
            else:
                await session.execute(
                    text("UPDATE trading_settings SET value = :value WHERE key = :key"),
                    {"key": args.key, "value": str(args.set)},
                )
            await session.commit()
            applied = str(args.set)

            # Drop the shared settings cache so workers pick the change up
            # immediately instead of after the 60s TTL.
            try:
                from core.redis import close_redis, init_redis
                from core.trading_settings import REDIS_CACHE_KEY

                redis = await init_redis()
                await redis.delete(REDIS_CACHE_KEY)
                await close_redis()
            except Exception:  # noqa: BLE001 - cache expiry still applies
                pass

    return {
        "ok": True,
        "key": args.key,
        "previous": previous,
        "value": applied,
        "changed": args.set is not None and str(previous) != str(args.set),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True)
    parser.add_argument("--set", default=None, help="new value; omit to only read")
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
