#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from collections import OrderedDict
from pathlib import Path

try:
    from deploy_config import parse_env_file
except ModuleNotFoundError:  # pragma: no cover - used when imported as scripts.render_runtime_envs
    from scripts.deploy_config import parse_env_file


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
    "SYNC_VERIFY_TLS",
    "SYNC_CA_BUNDLE",
    "OBSERVABILITY_API_KEY",
    "RELEASE_SHA",
    "IRAN_ORIGIN_READINESS_API_KEY",
    "ORIGIN_EXPECTED_MIGRATION_REVISION",
    "ORIGIN_READINESS_MAX_EVIDENCE_AGE_SECONDS",
    "WRITER_WITNESS_REQUIRED",
    "WRITER_WITNESS_AUTO_RENEW_ENABLED",
    "WRITER_WITNESS_PUBLIC_KEY",
    "WRITER_WITNESS_LEASE_DURATION_SECONDS",
    "WRITER_WITNESS_RENEW_INTERVAL_SECONDS",
    "WRITER_WITNESS_SAFETY_MARGIN_SECONDS",
    "WRITER_WITNESS_MAX_CLOCK_SKEW_SECONDS",
    "WRITER_WITNESS_AUTHORITATIVE_SITE",
    "WRITER_WITNESS_HTTP_TIMEOUT_SECONDS",
    "WRITER_WITNESS_AUTH_MAX_AGE_SECONDS",
    "IRAN_WRITER_WITNESS_INTERNAL_URL",
    "IRAN_WRITER_WITNESS_CLIENT_KEY_ID",
    "IRAN_WRITER_WITNESS_CLIENT_SECRET",
    "IRAN_WRITER_WITNESS_VERIFY_TLS",
    "IRAN_WRITER_WITNESS_CA_BUNDLE",
    "CHANNEL_ID",
    "CHANNEL_INVITE_LINK",
    "SMSIR_API_KEY",
    "SMSIR_LINE_NUMBER",
    "SMSIR_OTP_TEMPLATE_ID",
    "SMSIR_OTP_TEMPLATE_PARAMETER",
    "SMSIR_INVITATION_TEMPLATE_ID",
    "SMSIR_INVITATION_TEMPLATE_PARAMETER",
    "SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID",
    "SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID",
    "ERROR_TRACKING_DSN",
    "TRUSTED_PROXY_CIDRS",
    "OBSERVABILITY_TELEGRAM_USER_HASH_SALT",
    "GRAFANA_ALERT_DEFAULT_RECEIVER",
    "GRAFANA_ALERT_CRITICAL_RECEIVER",
    "GRAFANA_ALERT_WARNING_RECEIVER",
    "GRAFANA_ALERT_WEBHOOK_URL",
    "GRAFANA_ALERT_EMAIL_ADDRESSES",
    "WEB_PUSH_ENABLED",
    "WEB_PUSH_VAPID_PUBLIC_KEY",
    "WEB_PUSH_VAPID_PRIVATE_KEY",
    "WEB_PUSH_VAPID_SUBJECT",
    "WEB_PUSH_TTL_SECONDS",
    "WEB_PUSH_TIMEOUT_SECONDS",
    "PUBLIC_WEBAPP_URL",
    "FOREIGN_SERVER_ALIASES",
    "IRAN_SERVER_ALIASES",
    "TELEGRAM_DIRECT_REGISTRATION_ENABLED",
    "TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED",
    "TELEGRAM_LOGIN_OTP_ENABLED",
    "OTP_SMS_AUTO_FALLBACK_ENABLED",
    "OTP_SMS_AUTO_FALLBACK_SECONDS",
    "OTP_TTL_SECONDS",
    "IRAN_OTP_DELIVERY_STATE_SECRET",
    "TELEGRAM_REGISTRATION_POST_EXPIRY_GRACE_SECONDS",
    "TELEGRAM_REGISTRATION_JOB_BATCH_SIZE",
    "TELEGRAM_REGISTRATION_JOB_CONCURRENCY",
    "OTP_SMS_FALLBACK_JOB_CONCURRENCY",
    "INVITATION_SMS_STANDARD_ENABLED",
    "INVITATION_SMS_CUSTOMER_TIER1_ENABLED",
    "INVITATION_SMS_ACCOUNTANT_ENABLED",
    "INVITATION_SMS_CUSTOMER_TIER2_ENABLED",
    "INVITATION_CONTRACT_V2_ENABLED",
    "REGISTRATION_SYNC_V2_ENABLED",
    "REGISTRATION_SYNC_ACCEPT_UNVERSIONED",
    "INVITATION_PUBLIC_RATE_LIMIT_PER_MINUTE",
)

OPTIONAL_RUNTIME_DEFAULTS = {
    "CHANNEL_INVITE_LINK": "",
    "ERROR_TRACKING_DSN": "",
    "RELEASE_SHA": "",
    "IRAN_ORIGIN_READINESS_API_KEY": "",
    "ORIGIN_EXPECTED_MIGRATION_REVISION": "",
    "ORIGIN_READINESS_MAX_EVIDENCE_AGE_SECONDS": "900",
    "WRITER_WITNESS_REQUIRED": "false",
    "WRITER_WITNESS_AUTO_RENEW_ENABLED": "false",
    "WRITER_WITNESS_PUBLIC_KEY": "",
    "WRITER_WITNESS_LEASE_DURATION_SECONDS": "180",
    "WRITER_WITNESS_RENEW_INTERVAL_SECONDS": "30",
    "WRITER_WITNESS_SAFETY_MARGIN_SECONDS": "15",
    "WRITER_WITNESS_MAX_CLOCK_SKEW_SECONDS": "5",
    "WRITER_WITNESS_AUTHORITATIVE_SITE": "webapp_ir",
    "WRITER_WITNESS_HTTP_TIMEOUT_SECONDS": "3.0",
    "WRITER_WITNESS_AUTH_MAX_AGE_SECONDS": "15",
    "IRAN_WRITER_WITNESS_INTERNAL_URL": "",
    "IRAN_WRITER_WITNESS_CLIENT_KEY_ID": "",
    "IRAN_WRITER_WITNESS_CLIENT_SECRET": "",
    "IRAN_WRITER_WITNESS_VERIFY_TLS": "true",
    "IRAN_WRITER_WITNESS_CA_BUNDLE": "",
    "SYNC_VERIFY_TLS": "true",
    "SYNC_CA_BUNDLE": "",
    "SMSIR_OTP_TEMPLATE_ID": "585147",
    "SMSIR_OTP_TEMPLATE_PARAMETER": "CODE",
    "SMSIR_INVITATION_TEMPLATE_ID": "657938",
    "SMSIR_INVITATION_TEMPLATE_PARAMETER": "NAME",
    "SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID": "162103",
    "SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID": "903643",
    "WEB_PUSH_ENABLED": "false",
    "WEB_PUSH_VAPID_PUBLIC_KEY": "",
    "WEB_PUSH_VAPID_PRIVATE_KEY": "",
    "WEB_PUSH_VAPID_SUBJECT": "",
    "WEB_PUSH_TTL_SECONDS": "3600",
    "WEB_PUSH_TIMEOUT_SECONDS": "5.0",
    "PUBLIC_WEBAPP_URL": "",
    "FOREIGN_SERVER_ALIASES": "",
    "IRAN_SERVER_ALIASES": "",
    "TELEGRAM_DIRECT_REGISTRATION_ENABLED": "false",
    "TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED": "false",
    "TELEGRAM_LOGIN_OTP_ENABLED": "false",
    "OTP_SMS_AUTO_FALLBACK_ENABLED": "false",
    "OTP_SMS_AUTO_FALLBACK_SECONDS": "40",
    "OTP_TTL_SECONDS": "120",
    "IRAN_OTP_DELIVERY_STATE_SECRET": "",
    "TELEGRAM_REGISTRATION_POST_EXPIRY_GRACE_SECONDS": "86400",
    "TELEGRAM_REGISTRATION_JOB_BATCH_SIZE": "10",
    "TELEGRAM_REGISTRATION_JOB_CONCURRENCY": "1",
    "OTP_SMS_FALLBACK_JOB_CONCURRENCY": "4",
    "INVITATION_SMS_STANDARD_ENABLED": "false",
    "INVITATION_SMS_CUSTOMER_TIER1_ENABLED": "false",
    "INVITATION_SMS_ACCOUNTANT_ENABLED": "true",
    "INVITATION_SMS_CUSTOMER_TIER2_ENABLED": "true",
    "INVITATION_CONTRACT_V2_ENABLED": "false",
    "REGISTRATION_SYNC_V2_ENABLED": "false",
    "REGISTRATION_SYNC_ACCEPT_UNVERSIONED": "true",
    "INVITATION_PUBLIC_RATE_LIMIT_PER_MINUTE": "30",
}

PERFORMANCE_RUNTIME_DEFAULTS = OrderedDict(
    (
        ("DB_POOL_SIZE", "15"),
        ("DB_MAX_OVERFLOW", "10"),
        ("DB_POOL_RECYCLE_SECONDS", "3600"),
        ("DB_POOL_PRE_PING", "true"),
        ("BACKGROUND_LEADER_LOCK_TTL_SECONDS", "90"),
        ("BACKGROUND_LEADER_LOCK_REFRESH_SECONDS", "30"),
        ("BACKGROUND_LEADER_RETRY_SECONDS", "10"),
    )
)

ROLE_PERFORMANCE_DEFAULTS = {
    "foreign": {
        "DB_POOL_SIZE": "15",
        "DB_MAX_OVERFLOW": "10",
    },
    "iran": {
        "DB_POOL_SIZE": "8",
        "DB_MAX_OVERFLOW": "4",
    },
}

POSTGRES_TUNING_DEFAULTS = OrderedDict(
    (
        ("POSTGRES_MAX_CONNECTIONS", "500"),
        ("POSTGRES_SHARED_BUFFERS", "128MB"),
        ("POSTGRES_EFFECTIVE_CACHE_SIZE", "4GB"),
        ("POSTGRES_WORK_MEM", "4MB"),
        ("POSTGRES_MAINTENANCE_WORK_MEM", "64MB"),
        ("POSTGRES_RANDOM_PAGE_COST", "4"),
        ("POSTGRES_EFFECTIVE_IO_CONCURRENCY", "1"),
        ("POSTGRES_CHECKPOINT_TIMEOUT", "5min"),
        ("POSTGRES_MAX_WAL_SIZE", "1GB"),
        ("POSTGRES_MIN_WAL_SIZE", "80MB"),
        ("POSTGRES_WAL_BUFFERS", "4MB"),
    )
)

REDIS_DURABILITY_DEFAULTS = OrderedDict(
    (
        ("REDIS_APPENDONLY", "yes"),
        ("REDIS_APPENDFSYNC", "everysec"),
        ("REDIS_MAXMEMORY", "0"),
        ("REDIS_MAXMEMORY_POLICY", "noeviction"),
    )
)

ROLE_POSTGRES_TUNING_DEFAULTS = {
    "foreign": {},
    "iran": {
        "POSTGRES_MAX_CONNECTIONS": "150",
        "POSTGRES_SHARED_BUFFERS": "2GB",
        "POSTGRES_EFFECTIVE_CACHE_SIZE": "5GB",
        "POSTGRES_WORK_MEM": "4MB",
        "POSTGRES_MAINTENANCE_WORK_MEM": "256MB",
        "POSTGRES_RANDOM_PAGE_COST": "1.2",
        "POSTGRES_EFFECTIVE_IO_CONCURRENCY": "200",
        "POSTGRES_CHECKPOINT_TIMEOUT": "15min",
        "POSTGRES_MAX_WAL_SIZE": "2GB",
        "POSTGRES_MIN_WAL_SIZE": "512MB",
        "POSTGRES_WAL_BUFFERS": "16MB",
    },
}


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
    parser.add_argument("--source-env-file", help="Optional runtime env source file. Environment variables override file values.")
    parser.add_argument("--metrics-backend", default="memory")
    parser.add_argument("--audit-trail-path", default="/app/audit_trail/audit.jsonl")
    parser.add_argument("--foreign-api-workers", default=os.environ.get("FOREIGN_API_WORKERS", "2"))
    parser.add_argument("--iran-api-workers", default=os.environ.get("IRAN_API_WORKERS", "4"))
    parser.add_argument("--foreign-physical-site", required=True, choices=("bot_fi",))
    parser.add_argument(
        "--iran-physical-site",
        required=True,
        choices=("webapp_fi", "webapp_ir"),
    )
    return parser.parse_args()

def collect_runtime_values(source_env_file: str | None = None) -> dict[str, str]:
    import os

    source_values = parse_env_file(Path(source_env_file)) if source_env_file else {}
    values: dict[str, str] = {}
    missing: list[str] = []
    for key in COMMON_RUNTIME_KEYS:
        is_optional = key in OPTIONAL_RUNTIME_DEFAULTS
        value = os.environ.get(key, source_values.get(key))
        if isinstance(value, str):
            value = value.strip()
        if (value is None or value == "") and is_optional:
            value = OPTIONAL_RUNTIME_DEFAULTS[key]
        if value is None or (value == "" and not is_optional):
            missing.append(key)
            continue
        values[key] = value
    for key, default in PERFORMANCE_RUNTIME_DEFAULTS.items():
        values[key] = os.environ.get(key, source_values.get(key, default))
    for role, defaults in ROLE_PERFORMANCE_DEFAULTS.items():
        prefix = role.upper()
        for key, default in defaults.items():
            role_key = f"{prefix}_{key}"
            values[role_key] = os.environ.get(role_key, source_values.get(role_key, default))
    for key, default in POSTGRES_TUNING_DEFAULTS.items():
        values[key] = os.environ.get(key, source_values.get(key, default))
    for role, defaults in ROLE_POSTGRES_TUNING_DEFAULTS.items():
        prefix = role.upper()
        for key, fallback in POSTGRES_TUNING_DEFAULTS.items():
            role_key = f"{prefix}_{key}"
            default = defaults.get(key, fallback)
            values[role_key] = os.environ.get(role_key, source_values.get(role_key, default))
    for key, default in REDIS_DURABILITY_DEFAULTS.items():
        values[key] = os.environ.get(key, source_values.get(key, default))
    if missing:
        missing_list = ", ".join(missing)
        raise SystemExit(f"Missing required runtime env inputs: {missing_list}")
    return values


def build_runtime_env(
    *,
    role: str,
    physical_site: str,
    frontend_url: str,
    public_webapp_url: str,
    foreign_server_url: str,
    foreign_server_domain: str,
    iran_server_url: str,
    iran_server_domain: str,
    metrics_backend: str,
    audit_trail_path: str,
    api_workers: str,
    values: dict[str, str],
) -> OrderedDict[str, str]:
    expected_sites = {"foreign": {"bot_fi"}, "iran": {"webapp_fi", "webapp_ir"}}
    if role not in expected_sites or physical_site not in expected_sites[role]:
        raise ValueError(f"invalid explicit runtime identity role={role!r} site={physical_site!r}")
    rendered = OrderedDict()
    rendered["SERVER_MODE"] = role
    rendered["LOGICAL_AUTHORITY"] = "foreign" if role == "foreign" else "webapp"
    rendered["PHYSICAL_SITE"] = physical_site
    rendered["API_WORKERS"] = str(api_workers)
    for key in COMMON_RUNTIME_KEYS[:6]:
        rendered[key] = values[key]
    rendered["FRONTEND_URL"] = frontend_url
    for key in COMMON_RUNTIME_KEYS[6:]:
        if key in {
            "IRAN_OTP_DELIVERY_STATE_SECRET",
            "IRAN_ORIGIN_READINESS_API_KEY",
            "IRAN_WRITER_WITNESS_INTERNAL_URL",
            "IRAN_WRITER_WITNESS_CLIENT_KEY_ID",
            "IRAN_WRITER_WITNESS_CLIENT_SECRET",
            "IRAN_WRITER_WITNESS_VERIFY_TLS",
            "IRAN_WRITER_WITNESS_CA_BUNDLE",
        }:
            continue
        rendered[key] = values.get(key, OPTIONAL_RUNTIME_DEFAULTS.get(key, ""))
    rendered["OTP_DELIVERY_STATE_SECRET"] = (
        values.get("IRAN_OTP_DELIVERY_STATE_SECRET", "") if role == "iran" else ""
    )
    rendered["ORIGIN_READINESS_API_KEY"] = (
        values.get("IRAN_ORIGIN_READINESS_API_KEY", "") if role == "iran" else ""
    )
    rendered["WRITER_WITNESS_INTERNAL_URL"] = (
        values.get("IRAN_WRITER_WITNESS_INTERNAL_URL", "") if role == "iran" else ""
    )
    rendered["WRITER_WITNESS_CLIENT_KEY_ID"] = (
        values.get("IRAN_WRITER_WITNESS_CLIENT_KEY_ID", "") if role == "iran" else ""
    )
    rendered["WRITER_WITNESS_CLIENT_SECRET"] = (
        values.get("IRAN_WRITER_WITNESS_CLIENT_SECRET", "") if role == "iran" else ""
    )
    rendered["WRITER_WITNESS_VERIFY_TLS"] = (
        values.get("IRAN_WRITER_WITNESS_VERIFY_TLS", "true") if role == "iran" else "true"
    )
    rendered["WRITER_WITNESS_CA_BUNDLE"] = (
        values.get("IRAN_WRITER_WITNESS_CA_BUNDLE", "") if role == "iran" else ""
    )
    role_prefix = role.upper()
    for key in PERFORMANCE_RUNTIME_DEFAULTS:
        rendered[key] = values.get(f"{role_prefix}_{key}", values[key])
    for key in POSTGRES_TUNING_DEFAULTS:
        rendered[key] = values.get(f"{role_prefix}_{key}", values[key])
    for key in REDIS_DURABILITY_DEFAULTS:
        rendered[key] = values[key]
    rendered["TRADING_BOT_METRICS_BACKEND"] = metrics_backend
    rendered["AUDIT_TRAIL_PATH"] = audit_trail_path
    rendered["FOREIGN_SERVER_URL"] = foreign_server_url
    rendered["FOREIGN_SERVER_DOMAIN"] = foreign_server_domain
    rendered["IRAN_SERVER_URL"] = iran_server_url
    rendered["IRAN_SERVER_DOMAIN"] = iran_server_domain
    rendered["PUBLIC_WEBAPP_URL"] = values.get("PUBLIC_WEBAPP_URL") or public_webapp_url
    return rendered


def write_env_file(path: str, payload: OrderedDict[str, str]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in payload.items()]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    values = collect_runtime_values(args.source_env_file)

    write_env_file(
        args.local_output,
        build_runtime_env(
            role="foreign",
            physical_site=args.foreign_physical_site,
            frontend_url=args.foreign_frontend_url,
            public_webapp_url=args.iran_frontend_url,
            foreign_server_url=args.foreign_server_url,
            foreign_server_domain=args.foreign_server_domain,
            iran_server_url=args.iran_server_url,
            iran_server_domain=args.iran_server_domain,
            metrics_backend=args.metrics_backend,
            audit_trail_path=args.audit_trail_path,
            api_workers=args.foreign_api_workers,
            values=values,
        ),
    )
    write_env_file(
        args.iran_output,
        build_runtime_env(
            role="iran",
            physical_site=args.iran_physical_site,
            frontend_url=args.iran_frontend_url,
            public_webapp_url=args.iran_frontend_url,
            foreign_server_url=args.foreign_server_url,
            foreign_server_domain=args.foreign_server_domain,
            iran_server_url=args.iran_server_url,
            iran_server_domain=args.iran_server_domain,
            metrics_backend=args.metrics_backend,
            audit_trail_path=args.audit_trail_path,
            api_workers=args.iran_api_workers,
            values=values,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
