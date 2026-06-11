#!/usr/bin/env python3
"""Report PostgreSQL tuning and connection budget for production probes.

The script is read-only and is intended to run inside the app container. It
uses the existing sync database URL and emits redaction-safe JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import OrderedDict
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg2


IRAN_P2_EXPECTED_SETTINGS = OrderedDict(
    (
        ("max_connections", "500"),
        ("shared_buffers", "8GB"),
        ("effective_cache_size", "80GB"),
        ("work_mem", "8MB"),
        ("maintenance_work_mem", "512MB"),
        ("random_page_cost", "1.2"),
        ("effective_io_concurrency", "200"),
        ("checkpoint_timeout", "15min"),
        ("max_wal_size", "8GB"),
        ("min_wal_size", "1GB"),
        ("wal_buffers", "16MB"),
    )
)

SIZE_UNITS = {
    "b": 1,
    "kb": 1024,
    "mb": 1024**2,
    "gb": 1024**3,
    "tb": 1024**4,
}
TIME_UNITS = {
    "ms": Decimal("0.001"),
    "s": Decimal("1"),
    "min": Decimal("60"),
    "h": Decimal("3600"),
}
SIZE_SETTING_NAMES = {
    "shared_buffers",
    "effective_cache_size",
    "work_mem",
    "maintenance_work_mem",
    "max_wal_size",
    "min_wal_size",
    "wal_buffers",
}
TIME_SETTING_NAMES = {
    "checkpoint_timeout",
}
DECIMAL_SETTING_NAMES = {
    "random_page_cost",
}


@dataclass(frozen=True)
class SettingCheck:
    name: str
    actual: str
    expected: str
    ok: bool


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report PostgreSQL runtime tuning and connection budget.")
    parser.add_argument("--expected-profile", choices=("iran-p2",), default="iran-p2")
    parser.add_argument("--json", action="store_true", help="Emit JSON. This is the default for benchmark use.")
    parser.add_argument("--no-fail", action="store_true", help="Exit 0 even when checks fail.")
    parser.add_argument("--connection-limit-ratio", type=float, default=0.8)
    return parser.parse_args(argv)


def database_url() -> str:
    raw = os.environ.get("SYNC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("SYNC_DATABASE_URL or DATABASE_URL is required")
    return raw.replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")


def split_number_unit(value: str) -> tuple[Decimal, str] | None:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?\s*", value)
    if not match:
        return None
    return Decimal(match.group(1)), (match.group(2) or "").lower()


def normalize_setting_value(name: str, value: str) -> str:
    normalized = str(value).strip()
    if name in SIZE_SETTING_NAMES:
        parsed = split_number_unit(normalized)
        if not parsed:
            return normalized.lower()
        number, unit = parsed
        unit = unit or "b"
        multiplier = SIZE_UNITS.get(unit)
        if multiplier is None:
            return normalized.lower()
        return str(int(number * multiplier))
    if name in TIME_SETTING_NAMES:
        parsed = split_number_unit(normalized)
        if not parsed:
            return normalized.lower()
        number, unit = parsed
        unit = unit or "s"
        multiplier = TIME_UNITS.get(unit)
        if multiplier is None:
            return normalized.lower()
        seconds = number * multiplier
        if seconds == seconds.to_integral_value():
            return str(int(seconds))
        return str(seconds.normalize())
    if name in DECIMAL_SETTING_NAMES:
        try:
            return str(Decimal(normalized).normalize())
        except InvalidOperation:
            return normalized.lower()
    return normalized.lower()


def expected_settings(profile: str) -> OrderedDict[str, str]:
    if profile == "iran-p2":
        return IRAN_P2_EXPECTED_SETTINGS
    raise ValueError(f"Unsupported expected profile: {profile}")


def evaluate_settings(actual: dict[str, str], expected: OrderedDict[str, str]) -> list[SettingCheck]:
    checks: list[SettingCheck] = []
    for name, expected_value in expected.items():
        actual_value = actual.get(name, "")
        checks.append(
            SettingCheck(
                name=name,
                actual=actual_value,
                expected=expected_value,
                ok=normalize_setting_value(name, actual_value) == normalize_setting_value(name, expected_value),
            )
        )
    return checks


def int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def build_connection_budget(*, max_connections: int, current_connections: int, limit_ratio: float) -> dict[str, Any]:
    api_workers = int_env("API_WORKERS", 8)
    pool_size = int_env("DB_POOL_SIZE", 8)
    max_overflow = int_env("DB_MAX_OVERFLOW", 6)
    per_process_ceiling = pool_size + max_overflow
    # Iran runs API workers plus one sync_worker process. Migration is short-lived
    # and not included in the steady-state budget.
    estimated_sqlalchemy_ceiling = (api_workers + 1) * per_process_ceiling
    safe_connection_limit = int(max_connections * limit_ratio)
    return {
        "api_workers": api_workers,
        "db_pool_size": pool_size,
        "db_max_overflow": max_overflow,
        "per_process_ceiling": per_process_ceiling,
        "estimated_sqlalchemy_ceiling": estimated_sqlalchemy_ceiling,
        "current_connections": current_connections,
        "max_connections": max_connections,
        "safe_connection_limit": safe_connection_limit,
        "limit_ratio": limit_ratio,
        "estimated_within_limit": estimated_sqlalchemy_ceiling < safe_connection_limit,
        "current_within_limit": current_connections < safe_connection_limit,
    }


def collect_report(args: argparse.Namespace) -> dict[str, Any]:
    expected = expected_settings(args.expected_profile)
    with psycopg2.connect(database_url(), connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select name, current_setting(name) from pg_settings where name = any(%s) order by name", (list(expected),))
            actual = {str(name): str(value) for name, value in cursor.fetchall()}
            cursor.execute("select state, count(*) from pg_stat_activity group by state order by state nulls first")
            connection_states = {str(state or "none"): int(count) for state, count in cursor.fetchall()}
            cursor.execute("select count(*) from pg_stat_activity")
            current_connections = int(cursor.fetchone()[0])

    checks = evaluate_settings(actual, expected)
    settings_ok = all(check.ok for check in checks)
    max_connections = int(actual.get("max_connections", expected["max_connections"]))
    budget = build_connection_budget(
        max_connections=max_connections,
        current_connections=current_connections,
        limit_ratio=args.connection_limit_ratio,
    )
    connections_ok = bool(budget["estimated_within_limit"] and budget["current_within_limit"])
    return {
        "profile": args.expected_profile,
        "settings_ok": settings_ok,
        "connections_ok": connections_ok,
        "ok": settings_ok and connections_ok,
        "settings": [asdict(check) for check in checks],
        "connection_states": connection_states,
        "connection_budget": budget,
    }


def format_human_report(report: dict[str, Any]) -> str:
    lines = [
        "PostgreSQL runtime tuning:",
        f"- profile: {report['profile']}",
        f"- settings_ok: {report['settings_ok']}",
        f"- connections_ok: {report['connections_ok']}",
    ]
    for item in report["settings"]:
        status = "ok" if item["ok"] else "mismatch"
        lines.append(f"- {item['name']}: actual={item['actual']} expected={item['expected']} [{status}]")
    budget = report["connection_budget"]
    lines.append(
        "- connection budget: "
        f"current={budget['current_connections']} max={budget['max_connections']} "
        f"estimated_sqlalchemy_ceiling={budget['estimated_sqlalchemy_ceiling']} "
        f"safe_limit={budget['safe_connection_limit']}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = collect_report(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_human_report(report))
    if report["ok"] or args.no_fail:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
