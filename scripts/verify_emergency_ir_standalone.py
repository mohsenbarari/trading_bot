#!/usr/bin/env python3
"""Fail closed on an Emergency IR Standalone deployment manifest."""

from __future__ import annotations

import argparse
import os
import re
import stat
from pathlib import Path


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED = {
    "COMPOSE_PROJECT_NAME": "trading-bot-emergency-ir",
    "EMERGENCY_RUNTIME_ENV_FILE": "/etc/trading-bot-emergency/standalone/runtime.env",
    "EMERGENCY_APP_PORT": "18000",
    "EMERGENCY_TRADING_SETTINGS_FILE": "/srv/trading-bot-emergency/current/trading_settings.json",
    "SERVER_MODE": "iran",
    "BACKGROUND_JOBS_ENABLED": "false",
    "WEB_PUSH_ENABLED": "false",
    "TELEGRAM_DIRECT_REGISTRATION_ENABLED": "false",
    "TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED": "false",
    "TELEGRAM_LOGIN_OTP_ENABLED": "false",
    "OTP_SMS_AUTO_FALLBACK_ENABLED": "false",
    "REGISTRATION_SYNC_V2_ENABLED": "false",
    "REGISTRATION_SYNC_ACCEPT_UNVERSIONED": "false",
    "TRUSTED_PROXY_CIDRS": "127.0.0.1/32,172.29.250.1/32,::1/128",
}
REQUIRED = frozenset({
    *EXPECTED,
    "SOURCE_RELEASE_SHA", "RELEASE_SHA", "EMERGENCY_APP_IMAGE",
    "EMERGENCY_POSTGRES_IMAGE", "EMERGENCY_REDIS_IMAGE", "POSTGRES_USER",
    "POSTGRES_DB", "POSTGRES_PASSWORD", "DATABASE_URL", "SYNC_DATABASE_URL",
    "REDIS_URL", "JWT_SECRET_KEY", "DEV_API_KEY", "FRONTEND_URL",
    "PUBLIC_WEBAPP_URL",
})
FORBIDDEN = frozenset({
    "BOT_TOKEN", "BOT_USERNAME", "SYNC_API_KEY", "PEER_SERVER_URL",
    "IRAN_SERVER_URL", "GERMANY_SERVER_URL", "FOREIGN_SERVER_URL",
    "SMSIR_API_KEY", "SMSIR_LINE_NUMBER", "WEB_PUSH_VAPID_PRIVATE_KEY",
    "WEB_PUSH_VAPID_PUBLIC_KEY", "WRITER_WITNESS_CLIENT_SECRET",
    "WRITER_WITNESS_INTERNAL_URL", "DR_SYNC_PAIRWISE_KEYS_JSON",
})
BLOCKED_NGINX_PREFIXES = (
    "/api/sync", "/api/dr-sync", "/api/sessions/internal",
    "/api/trades/internal", "/api/offers/internal", "/api/invitations/internal",
    "/api/auth/internal/telegram-registration", "/api/auth/internal/telegram-link",
    "/api/auth/internal/telegram-otp",
)


class EmergencyVerificationError(RuntimeError):
    pass


def parse_env(path: Path) -> dict[str, str]:
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077:
        raise EmergencyVerificationError("runtime env must be a root-owned 0600 regular file")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise EmergencyVerificationError(f"runtime env line {number} is malformed")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in values:
            raise EmergencyVerificationError(f"runtime env line {number} has an invalid key")
        values[key] = value.strip()
    return values


def verify_values(values: dict[str, str]) -> list[str]:
    failures: list[str] = []
    for key in REQUIRED:
        if not values.get(key):
            failures.append(f"missing required runtime value: {key}")
    for key, expected in EXPECTED.items():
        if values.get(key, "").lower() != expected.lower():
            failures.append(f"{key} must equal {expected!r}")
    forbidden = sorted(FORBIDDEN & values.keys())
    if forbidden:
        failures.append("forbidden runtime keys: " + ",".join(forbidden))
    source_sha = values.get("SOURCE_RELEASE_SHA", "")
    if not SHA_RE.fullmatch(source_sha) or values.get("RELEASE_SHA") != source_sha:
        failures.append("SOURCE_RELEASE_SHA and RELEASE_SHA must be the same exact lowercase SHA")
    if values.get("EMERGENCY_APP_IMAGE") != f"trading_bot_emergency_ir_app:{source_sha}":
        failures.append("application image must match the attested source release")
    if not values.get("EMERGENCY_POSTGRES_IMAGE", "").startswith("trading_bot_emergency_ir_postgres:"):
        failures.append("PostgreSQL image must be in the emergency namespace")
    if not values.get("EMERGENCY_REDIS_IMAGE", "").startswith("trading_bot_emergency_ir_redis:"):
        failures.append("Redis image must be in the emergency namespace")
    if values.get("FRONTEND_URL") != "https://coin.gold-trade.ir" or values.get("PUBLIC_WEBAPP_URL") != "https://coin.gold-trade.ir":
        failures.append("public URLs must use the canonical emergency domain")
    if values.get("DATABASE_URL", "").split("@")[-1] != "db/trading_bot_emergency":
        failures.append("DATABASE_URL must target only the emergency DB service")
    if values.get("SYNC_DATABASE_URL", "").split("@")[-1] != "db/trading_bot_emergency":
        failures.append("SYNC_DATABASE_URL must target only the emergency DB service")
    if values.get("REDIS_URL") != "redis://redis:6379/0":
        failures.append("REDIS_URL must target only the emergency Redis service")
    return failures


def verify_compose(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    for required in (
        "name: trading-bot-emergency-ir", "172.29.250.0/28", "127.0.0.1:${EMERGENCY_APP_PORT:-18000}:8000",
        "trading-bot-emergency-ir-postgres", "trading-bot-emergency-ir-redis",
        "trading-bot-emergency-ir-uploads", "trading-bot-emergency-ir-audit",
        "internal: true", "--forwarded-allow-ips 127.0.0.1,172.29.250.1",
    ):
        if required not in text:
            failures.append(f"compose is missing required isolation contract: {required}")
    for forbidden in ("sync_worker:", "bot:", "writer_control", "dr_receiver", "dr_projection", "dr_delivery", "dr_blob", "dr_effect", "./api:/app/api", "./core:/app/core"):
        if forbidden in text:
            failures.append(f"compose contains forbidden service or source bind: {forbidden}")
    return failures


def verify_nginx(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    for required in (
        "listen 80 default_server", "ssl_reject_handshake on", "server_name coin.gold-trade.ir",
        "/etc/trading-bot-emergency/acme/config/live/emergency-coin-gold-trade-ir/fullchain.pem",
        "proxy_pass http://127.0.0.1:18000",
        "location = /metrics { return 404; }",
    ):
        if required not in text:
            failures.append(f"nginx is missing required contract: {required}")
    for prefix in BLOCKED_NGINX_PREFIXES:
        if f"location ^~ {prefix} {{ return 404; }}" not in text:
            failures.append(f"nginx does not block {prefix}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True)
    parser.add_argument("--compose", required=True)
    parser.add_argument("--nginx", required=True)
    args = parser.parse_args()
    try:
        values = parse_env(Path(args.env))
        failures = verify_values(values)
        failures.extend(verify_compose(Path(args.compose)))
        failures.extend(verify_nginx(Path(args.nginx)))
    except Exception as exc:
        failures = [str(exc)]
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("Emergency IR standalone verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
