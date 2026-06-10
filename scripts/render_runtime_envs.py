#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import OrderedDict
from pathlib import Path


COMMON_RUNTIME_KEYS = (
    "BOT_TOKEN",
    "BOT_USERNAME",
    "DATABASE_URL",
    "SYNC_DATABASE_URL",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "REDIS_URL",
    "JWT_SECRET_KEY",
    "DEV_API_KEY",
    "SYNC_API_KEY",
    "OBSERVABILITY_API_KEY",
    "CHANNEL_ID",
    "CHANNEL_INVITE_LINK",
    "SMSIR_API_KEY",
    "SMSIR_LINE_NUMBER",
    "ERROR_TRACKING_DSN",
    "TRUSTED_PROXY_CIDRS",
    "OBSERVABILITY_TELEGRAM_USER_HASH_SALT",
    "GRAFANA_ALERT_DEFAULT_RECEIVER",
    "GRAFANA_ALERT_CRITICAL_RECEIVER",
    "GRAFANA_ALERT_WARNING_RECEIVER",
    "GRAFANA_ALERT_WEBHOOK_URL",
    "GRAFANA_ALERT_EMAIL_ADDRESSES",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render foreign and Iran runtime env files.")
    parser.add_argument("--local-output", required=True, help="Output path for the foreign runtime .env")
    parser.add_argument("--iran-output", required=True, help="Output path for the Iran runtime .env")
    parser.add_argument("--foreign-frontend-url", required=True)
    parser.add_argument("--iran-frontend-url", required=True)
    parser.add_argument("--foreign-server-url", required=True)
    parser.add_argument("--foreign-server-domain", required=True)
    parser.add_argument("--iran-server-url", required=True)
    parser.add_argument("--iran-server-domain", required=True)
    parser.add_argument("--metrics-backend", default="memory")
    parser.add_argument("--audit-trail-path", default="/app/audit_trail/audit.jsonl")
    return parser.parse_args()

def collect_runtime_values() -> dict[str, str]:
    import os

    values: dict[str, str] = {}
    missing: list[str] = []
    for key in COMMON_RUNTIME_KEYS:
        value = os.environ.get(key)
        if value is None:
            missing.append(key)
            continue
        values[key] = value
    if missing:
        missing_list = ", ".join(missing)
        raise SystemExit(f"Missing required runtime env inputs: {missing_list}")
    return values


def build_runtime_env(
    *,
    role: str,
    frontend_url: str,
    foreign_server_url: str,
    foreign_server_domain: str,
    iran_server_url: str,
    iran_server_domain: str,
    metrics_backend: str,
    audit_trail_path: str,
    values: dict[str, str],
) -> OrderedDict[str, str]:
    rendered = OrderedDict()
    rendered["SERVER_MODE"] = role
    for key in COMMON_RUNTIME_KEYS[:6]:
        rendered[key] = values[key]
    rendered["FRONTEND_URL"] = frontend_url
    for key in COMMON_RUNTIME_KEYS[6:]:
        rendered[key] = values[key]
    rendered["TRADING_BOT_METRICS_BACKEND"] = metrics_backend
    rendered["AUDIT_TRAIL_PATH"] = audit_trail_path
    rendered["FOREIGN_SERVER_URL"] = foreign_server_url
    rendered["FOREIGN_SERVER_DOMAIN"] = foreign_server_domain
    rendered["IRAN_SERVER_URL"] = iran_server_url
    rendered["IRAN_SERVER_DOMAIN"] = iran_server_domain
    return rendered


def write_env_file(path: str, payload: OrderedDict[str, str]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in payload.items()]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    values = collect_runtime_values()

    write_env_file(
        args.local_output,
        build_runtime_env(
            role="foreign",
            frontend_url=args.foreign_frontend_url,
            foreign_server_url=args.foreign_server_url,
            foreign_server_domain=args.foreign_server_domain,
            iran_server_url=args.iran_server_url,
            iran_server_domain=args.iran_server_domain,
            metrics_backend=args.metrics_backend,
            audit_trail_path=args.audit_trail_path,
            values=values,
        ),
    )
    write_env_file(
        args.iran_output,
        build_runtime_env(
            role="iran",
            frontend_url=args.iran_frontend_url,
            foreign_server_url=args.foreign_server_url,
            foreign_server_domain=args.foreign_server_domain,
            iran_server_url=args.iran_server_url,
            iran_server_domain=args.iran_server_domain,
            metrics_backend=args.metrics_backend,
            audit_trail_path=args.audit_trail_path,
            values=values,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
