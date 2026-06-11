#!/usr/bin/env python3
"""Write and verify a non-sensitive Redis persistence probe item."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import redis


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.config import settings


DEFAULT_TTL_SECONDS = 3600


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Redis persistence with a synthetic list item.")
    parser.add_argument("--key", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=2.0)
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    return parser.parse_args(argv)


def redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=5, socket_timeout=5)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not (args.write or args.verify or args.cleanup):
        raise SystemExit("At least one of --write, --verify, or --cleanup is required")

    client = redis_client()
    report: dict[str, object] = {"key": args.key}

    if args.write:
        client.delete(args.key)
        client.rpush(args.key, args.payload)
        client.expire(args.key, args.ttl_seconds)
        if args.wait_seconds > 0:
            time.sleep(args.wait_seconds)
        report["written"] = True
        report["length_after_write"] = int(client.llen(args.key))

    if args.verify:
        values = client.lrange(args.key, 0, -1)
        report["verified"] = values == [args.payload]
        report["length_at_verify"] = len(values)
        if values != [args.payload]:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 2

    if args.cleanup:
        report["deleted"] = int(client.delete(args.key))

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
