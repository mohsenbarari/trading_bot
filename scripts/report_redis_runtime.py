#!/usr/bin/env python3
"""Report Redis durability settings and queue state for production probes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import redis


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.config import settings


EXPECTED_REDIS_SETTINGS = {
    "appendonly": "yes",
    "appendfsync": "everysec",
    "maxmemory-policy": "noeviction",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report Redis runtime durability settings.")
    parser.add_argument("--json", action="store_true", help="Emit JSON. This is the default for benchmark use.")
    parser.add_argument("--no-fail", action="store_true", help="Exit 0 even when checks fail.")
    return parser.parse_args(argv)


def redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=5, socket_timeout=5)


def normalize_config_payload(payload: dict[str, Any] | list[str]) -> dict[str, str]:
    if isinstance(payload, dict):
        return {str(key): str(value) for key, value in payload.items()}
    return {str(payload[index]): str(payload[index + 1]) for index in range(0, len(payload), 2)}


def evaluate_settings(actual: dict[str, str], expected: dict[str, str] = EXPECTED_REDIS_SETTINGS) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for key, expected_value in expected.items():
        actual_value = str(actual.get(key, ""))
        checks.append(
            {
                "name": key,
                "actual": actual_value,
                "expected": expected_value,
                "ok": actual_value.lower() == expected_value.lower(),
            }
        )
    return checks


def collect_report() -> dict[str, Any]:
    client = redis_client()
    config: dict[str, str] = {}
    for pattern in ("appendonly", "appendfsync", "maxmemory", "maxmemory-policy"):
        config.update(normalize_config_payload(client.config_get(pattern)))
    persistence = client.info("persistence")
    memory = client.info("memory")
    stats = client.info("stats")
    queue_lengths = {
        "sync:outbound": int(client.llen("sync:outbound")),
        "sync:retry": int(client.llen("sync:retry")),
    }
    checks = evaluate_settings(config)
    settings_ok = all(item["ok"] for item in checks)
    persistence_ok = int(persistence.get("aof_enabled", 0)) == 1 and str(
        persistence.get("aof_last_write_status", "ok")
    ).lower() == "ok"
    return {
        "ok": settings_ok and persistence_ok,
        "settings_ok": settings_ok,
        "persistence_ok": persistence_ok,
        "settings": checks,
        "config": {
            "appendonly": config.get("appendonly"),
            "appendfsync": config.get("appendfsync"),
            "maxmemory": config.get("maxmemory"),
            "maxmemory-policy": config.get("maxmemory-policy"),
        },
        "persistence": {
            "aof_enabled": int(persistence.get("aof_enabled", 0)),
            "aof_last_write_status": persistence.get("aof_last_write_status"),
            "aof_last_bgrewrite_status": persistence.get("aof_last_bgrewrite_status"),
            "rdb_last_bgsave_status": persistence.get("rdb_last_bgsave_status"),
        },
        "memory": {
            "used_memory": int(memory.get("used_memory", 0)),
            "used_memory_human": memory.get("used_memory_human"),
            "maxmemory": int(memory.get("maxmemory", 0)),
            "maxmemory_human": memory.get("maxmemory_human"),
            "maxmemory_policy": memory.get("maxmemory_policy"),
        },
        "stats": {
            "instantaneous_ops_per_sec": int(stats.get("instantaneous_ops_per_sec", 0)),
            "total_commands_processed": int(stats.get("total_commands_processed", 0)),
        },
        "queue_lengths": queue_lengths,
    }


def format_human_report(report: dict[str, Any]) -> str:
    lines = [
        "Redis runtime durability:",
        f"- settings_ok: {report['settings_ok']}",
        f"- persistence_ok: {report['persistence_ok']}",
    ]
    for item in report["settings"]:
        status = "ok" if item["ok"] else "mismatch"
        lines.append(f"- {item['name']}: actual={item['actual']} expected={item['expected']} [{status}]")
    lines.append(f"- memory: {report['memory']['used_memory_human']} / max {report['memory']['maxmemory_human']}")
    lines.append(f"- queues: {report['queue_lengths']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = collect_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_human_report(report))
    if report["ok"] or args.no_fail:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
