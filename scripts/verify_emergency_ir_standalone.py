#!/usr/bin/env python3
"""Fail closed on an Emergency IR Standalone deployment manifest."""

from __future__ import annotations

import argparse
import os
import re
import stat
from pathlib import Path


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SMSIR_TEMPLATE_PARAMETER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
AUTH_PROFILE_TELEGRAM_ONLY = "telegram-only"
AUTH_PROFILE_SMS_OTP = "sms-otp"
SMSIR_RELAY_URL = "http://sms-egress:8080"
COMMON_EXPECTED = {
    "COMPOSE_PROJECT_NAME": "trading-bot-emergency-ir",
    "EMERGENCY_RUNTIME_ENV_FILE": "/etc/trading-bot-emergency/standalone/runtime.env",
    "EMERGENCY_APP_PORT": "18000",
    "EMERGENCY_TRADING_SETTINGS_FILE": "/srv/trading-bot-emergency/current/trading_settings.json",
    "SERVER_MODE": "iran",
    "EMERGENCY_IR_STANDALONE": "true",
    "BACKGROUND_JOBS_ENABLED": "false",
    "TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH": "true",
    "WEB_PUSH_ENABLED": "false",
    "TELEGRAM_DIRECT_REGISTRATION_ENABLED": "false",
    "TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED": "false",
    "REGISTRATION_SYNC_V2_ENABLED": "false",
    "REGISTRATION_SYNC_ACCEPT_UNVERSIONED": "false",
    "TRUSTED_PROXY_CIDRS": "127.0.0.1/32,172.29.250.1/32,::1/128",
}
TELEGRAM_ONLY_EXPECTED = {
    "EMERGENCY_AUTH_PROFILE": AUTH_PROFILE_TELEGRAM_ONLY,
    "EMERGENCY_SMS_OTP_ENABLED": "false",
    "TELEGRAM_LOGIN_OTP_ENABLED": "false",
    "OTP_SMS_AUTO_FALLBACK_ENABLED": "false",
}
SMS_OTP_EXPECTED = {
    "EMERGENCY_AUTH_PROFILE": AUTH_PROFILE_SMS_OTP,
    "EMERGENCY_SMS_OTP_ENABLED": "true",
    # This legacy-named switch enables the Stage-6 state machine.  Fallback
    # stays false, so it cannot dispatch Telegram or a peer request.
    "TELEGRAM_LOGIN_OTP_ENABLED": "true",
    "OTP_SMS_AUTO_FALLBACK_ENABLED": "false",
    "SMSIR_BASE_URL": SMSIR_RELAY_URL,
    "SMSIR_TRUST_ENV": "false",
    "SMSIR_TIMEOUT_SECONDS": "10",
}
REQUIRED_COMMON = frozenset({
    *COMMON_EXPECTED,
    "SOURCE_RELEASE_SHA", "EMERGENCY_PATCH_SHA", "RELEASE_SHA", "EMERGENCY_APP_IMAGE",
    "EMERGENCY_POSTGRES_IMAGE", "EMERGENCY_REDIS_IMAGE", "POSTGRES_USER",
    "POSTGRES_DB", "POSTGRES_PASSWORD", "DATABASE_URL", "SYNC_DATABASE_URL",
    "REDIS_URL", "JWT_SECRET_KEY", "DEV_API_KEY", "WEBAPP_INITDATA_BOT_TOKEN", "FRONTEND_URL",
    "PUBLIC_WEBAPP_URL",
})
REQUIRED_SMS_OTP = frozenset({
    *REQUIRED_COMMON,
    *SMS_OTP_EXPECTED,
    "SMSIR_API_KEY", "SMSIR_OTP_TEMPLATE_ID", "SMSIR_OTP_TEMPLATE_PARAMETER",
    "OTP_DELIVERY_STATE_SECRET", "EMERGENCY_SMS_EGRESS_IMAGE",
})
FORBIDDEN_COMMON = frozenset({
    "BOT_TOKEN", "BOT_USERNAME", "SYNC_API_KEY", "PEER_SERVER_URL",
    "IRAN_SERVER_URL", "GERMANY_SERVER_URL", "FOREIGN_SERVER_URL",
    "SMSIR_LINE_NUMBER", "WEB_PUSH_VAPID_PRIVATE_KEY",
    "WEB_PUSH_VAPID_PUBLIC_KEY", "WRITER_WITNESS_CLIENT_SECRET",
    "WRITER_WITNESS_INTERNAL_URL", "DR_SYNC_PAIRWISE_KEYS_JSON",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
})
FORBIDDEN_TELEGRAM_ONLY = frozenset({
    *FORBIDDEN_COMMON,
    "SMSIR_API_KEY", "SMSIR_BASE_URL", "SMSIR_TRUST_ENV", "SMSIR_TIMEOUT_SECONDS",
    "SMSIR_OTP_TEMPLATE_ID", "SMSIR_OTP_TEMPLATE_PARAMETER", "OTP_DELIVERY_STATE_SECRET",
    "EMERGENCY_SMS_EGRESS_IMAGE",
})
BLOCKED_NGINX_PREFIXES = (
    "/api/sync", "/api/dr-sync", "/api/sessions/internal",
    "/api/trades/internal", "/api/offers/internal", "/api/invitations/internal",
    "/api/auth/internal/telegram-registration", "/api/auth/internal/telegram-link",
    "/api/auth/internal/telegram-otp",
    "/api/auth/request-otp", "/api/auth/resend-otp-sms", "/api/auth/verify-otp",
    "/api/auth/register-otp-request", "/api/auth/register-otp-verify",
    "/api/auth/register-complete", "/api/auth/telegram-link-token",
)
SMS_OTP_PUBLIC_PATHS = (
    "/api/auth/request-otp", "/api/auth/resend-otp-sms", "/api/auth/verify-otp",
)
SMS_OTP_BLOCKED_NGINX_PREFIXES = tuple(
    prefix for prefix in BLOCKED_NGINX_PREFIXES if prefix not in SMS_OTP_PUBLIC_PATHS
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


def _profile_expected(profile: str) -> dict[str, str] | None:
    if profile == AUTH_PROFILE_TELEGRAM_ONLY:
        return {**COMMON_EXPECTED, **TELEGRAM_ONLY_EXPECTED}
    if profile == AUTH_PROFILE_SMS_OTP:
        return {**COMMON_EXPECTED, **SMS_OTP_EXPECTED}
    return None


def _is_printable_single_line(value: str) -> bool:
    return bool(value) and all(33 <= ord(character) <= 126 for character in value)


def verify_values(
    values: dict[str, str],
    *,
    expected_profile: str | None = None,
) -> list[str]:
    failures: list[str] = []
    profile = values.get("EMERGENCY_AUTH_PROFILE", "")
    expected = _profile_expected(profile)
    if expected is None:
        return ["EMERGENCY_AUTH_PROFILE must select telegram-only or sms-otp"]
    if expected_profile is not None and profile != expected_profile:
        failures.append(f"runtime profile must equal {expected_profile!r}")
    required = REQUIRED_SMS_OTP if profile == AUTH_PROFILE_SMS_OTP else frozenset({
        *REQUIRED_COMMON,
        *TELEGRAM_ONLY_EXPECTED,
    })
    for key in required:
        if not values.get(key):
            failures.append(f"missing required runtime value: {key}")
    for key, expected_value in expected.items():
        if values.get(key, "").lower() != expected_value.lower():
            failures.append(f"{key} must equal {expected_value!r}")
    forbidden_set = (
        FORBIDDEN_TELEGRAM_ONLY
        if profile == AUTH_PROFILE_TELEGRAM_ONLY
        else FORBIDDEN_COMMON
    )
    forbidden = sorted(forbidden_set & values.keys())
    if forbidden:
        failures.append("forbidden runtime keys: " + ",".join(forbidden))
    source_sha = values.get("SOURCE_RELEASE_SHA", "")
    patch_sha = values.get("EMERGENCY_PATCH_SHA", "")
    if not SHA_RE.fullmatch(source_sha):
        failures.append("SOURCE_RELEASE_SHA must be one exact lowercase SHA")
    if not SHA_RE.fullmatch(patch_sha) or values.get("RELEASE_SHA") != patch_sha:
        failures.append("EMERGENCY_PATCH_SHA and RELEASE_SHA must be the same exact lowercase SHA")
    if values.get("EMERGENCY_APP_IMAGE") != f"trading_bot_emergency_ir_app:{patch_sha}":
        failures.append("application image must match the attested Emergency patch")
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
    initdata_token = values.get("WEBAPP_INITDATA_BOT_TOKEN", "")
    if not _is_printable_single_line(initdata_token):
        failures.append("WEBAPP_INITDATA_BOT_TOKEN must be one-line")
    if profile == AUTH_PROFILE_SMS_OTP:
        api_key = values.get("SMSIR_API_KEY", "")
        if not _is_printable_single_line(api_key):
            failures.append("SMSIR_API_KEY must be one-line")
        try:
            template_id = int(values.get("SMSIR_OTP_TEMPLATE_ID", ""))
        except ValueError:
            template_id = 0
        if template_id <= 0 or template_id > 2_147_483_647:
            failures.append("SMSIR_OTP_TEMPLATE_ID must be a positive integer")
        if not SMSIR_TEMPLATE_PARAMETER_RE.fullmatch(
            values.get("SMSIR_OTP_TEMPLATE_PARAMETER", "")
        ):
            failures.append("SMSIR_OTP_TEMPLATE_PARAMETER is invalid")
        if len(values.get("OTP_DELIVERY_STATE_SECRET", "")) < 32:
            failures.append("OTP_DELIVERY_STATE_SECRET must contain at least 32 characters")
        patch_sha = values.get("EMERGENCY_PATCH_SHA", "")
        if values.get("EMERGENCY_SMS_EGRESS_IMAGE") != (
            f"trading_bot_emergency_ir_sms_egress:{patch_sha}"
        ):
            failures.append("SMS egress image must match the attested Emergency patch")
    return failures


def verify_compose(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    for required in (
        "name: trading-bot-emergency-ir", "172.29.250.0/28", "127.0.0.1:${EMERGENCY_APP_PORT:-18000}:8000",
        "trading-bot-emergency-ir-postgres", "trading-bot-emergency-ir-redis",
        "trading-bot-emergency-ir-uploads", "trading-bot-emergency-ir-audit",
        "internal: true", "EMERGENCY_IR_STANDALONE: \"true\"",
        "--forwarded-allow-ips 127.0.0.1,172.29.250.1",
    ):
        if required not in text:
            failures.append(f"compose is missing required isolation contract: {required}")
    for forbidden in ("sync_worker:", "bot:", "writer_control", "dr_receiver", "dr_projection", "dr_delivery", "dr_blob", "dr_effect", "./api:/app/api", "./core:/app/core"):
        if forbidden in text:
            failures.append(f"compose contains forbidden service or source bind: {forbidden}")
    return failures


def verify_sms_otp_compose(path: Path) -> list[str]:
    """Validate the opt-in overlay without treating it as a generic egress path."""

    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    for required in (
        'profiles: ["sms-otp"]',
        'EMERGENCY_AUTH_PROFILE: "sms-otp"',
        'EMERGENCY_SMS_OTP_ENABLED: "true"',
        'TELEGRAM_LOGIN_OTP_ENABLED: "true"',
        'OTP_SMS_AUTO_FALLBACK_ENABLED: "false"',
        'SMSIR_BASE_URL: "http://sms-egress:8080"',
        'SMSIR_TRUST_ENV: "false"',
        'SMSIR_TIMEOUT_SECONDS: "10"',
        'EMERGENCY_SMS_EGRESS_IMAGE',
        '172.29.251.0/29', '172.29.251.2', '172.29.251.3',
        '172.29.252.0/29', '172.29.252.2',
        'emergency_ir_sms_relay:', 'emergency_ir_sms_egress:',
        'internal: true', 'internal: false',
        'read_only: true', 'no-new-privileges:true',
        'test -z "$${BOT_TOKEN:-}"', 'test -z "$${SYNC_API_KEY:-}"',
        'test -z "$${PEER_SERVER_URL:-}"',
        'migration:\n    profiles: ["sms-otp"]',
        'SMSIR_API_KEY: ""',
    ):
        if required not in text:
            failures.append(f"SMS OTP compose is missing required isolation contract: {required}")
    api_start = text.find("  api:")
    relay_start = text.find("  sms-egress:")
    api_block = text[api_start:relay_start] if api_start >= 0 and relay_start > api_start else ""
    if not api_block:
        failures.append("SMS OTP compose does not contain a bounded API overlay")
    elif "emergency_ir_sms_egress" in api_block:
        failures.append("API must not attach to the SMS egress network")
    relay_block = text[relay_start:text.find("\nnetworks:", relay_start)] if relay_start >= 0 else ""
    if relay_block and "emergency_ir_sms_relay" not in relay_block:
        failures.append("SMS relay must attach to the internal relay network")
    if relay_block and "emergency_ir_sms_egress" not in relay_block:
        failures.append("SMS relay must be the only service with SMS egress")
    if relay_block and "volumes:" in relay_block:
        failures.append("SMS relay must not mount a mutable host configuration")
    for forbidden in (
        "ports:", "network_mode:", "privileged:", "cap_add:", "\n      BOT_TOKEN:",
        "\n      SYNC_API_KEY:", "\n      PEER_SERVER_URL:", "bot:", "sync_worker:", "writer_control",
        "dr_receiver", "dr_projection", "dr_delivery", "dr_blob", "dr_effect",
    ):
        if forbidden in text:
            failures.append(f"SMS OTP compose contains forbidden egress or cross-site control: {forbidden}")
    return failures


def _verify_tls_certificate_contract(text: str, failures: list[str]) -> None:
    # The sealed template never consumes Certbot's mutable `live` leaf.  The
    # activator verifies that leaf through the trusted archive and renders its
    # root-only immutable snapshots into these two placeholders immediately
    # before Nginx prearm.
    certificate = "__EMERGENCY_TLS_FULLCHAIN__"
    key = "__EMERGENCY_TLS_PRIVATE_KEY__"
    if text.count(f"ssl_certificate {certificate};") != 2:
        failures.append("both TLS virtual hosts must load the pinned emergency certificate")
    if text.count(f"ssl_certificate_key {key};") != 2:
        failures.append("both TLS virtual hosts must load the pinned emergency certificate key")


def verify_nginx(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    for required in (
        "listen 80 default_server", "ssl_reject_handshake on", "server_name coin.gold-trade.ir",
        "__EMERGENCY_TLS_FULLCHAIN__",
        "proxy_pass http://127.0.0.1:18000",
        "location = /metrics { return 404; }",
    ):
        if required not in text:
            failures.append(f"nginx is missing required contract: {required}")
    for prefix in BLOCKED_NGINX_PREFIXES:
        if f"location ^~ {prefix} {{ return 404; }}" not in text:
            failures.append(f"nginx does not block {prefix}")
    _verify_tls_certificate_contract(text, failures)
    return failures


def verify_sms_otp_nginx(path: Path, rate_limit_path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    rate_limit = rate_limit_path.read_text(encoding="utf-8")
    failures: list[str] = []
    for required in (
        "listen 80 default_server", "ssl_reject_handshake on", "server_name coin.gold-trade.ir",
        "proxy_pass http://127.0.0.1:18000", "location = /metrics { return 404; }",
    ):
        if required not in text:
            failures.append(f"SMS OTP Nginx is missing required contract: {required}")
    for prefix in SMS_OTP_BLOCKED_NGINX_PREFIXES:
        if f"location ^~ {prefix} {{ return 404; }}" not in text:
            failures.append(f"SMS OTP Nginx does not block {prefix}")
    for path_value in SMS_OTP_PUBLIC_PATHS:
        block = (
            f"location = {path_value} {{\n"
            "        limit_req zone=emergency_sms_otp_per_ip burst=5 nodelay;"
        )
        if block not in text:
            failures.append(f"SMS OTP Nginx does not strictly rate-limit {path_value}")
        method_block = (
            f"location = {path_value} {{\n"
            "        limit_req zone=emergency_sms_otp_per_ip burst=5 nodelay;\n"
            "        limit_req_status 429;\n"
            "        limit_except POST { deny all; }"
        )
        if method_block not in text:
            failures.append(f"SMS OTP Nginx does not restrict {path_value} to POST")
        if f"location ^~ {path_value} {{ return 404; }}" in text:
            failures.append(f"SMS OTP Nginx still blocks required path {path_value}")
    for required in (
        "limit_req_zone $binary_remote_addr zone=emergency_sms_otp_per_ip:10m rate=10r/m;",
        "limit_req_status 429;",
    ):
        if required not in rate_limit:
            failures.append(f"SMS OTP rate-limit file is missing: {required}")
    _verify_tls_certificate_contract(text, failures)
    return failures


def verify_sms_egress_relay(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    for required in (
        "listen 172.29.251.3:8080 default_server;",
        "allow 172.29.251.2;", "deny all;", "access_log off;",
        "location = /v1/send/verify {", "limit_except POST { deny all; }",
        "proxy_pass https://api.sms.ir/v1/send/verify;",
        "proxy_pass_request_headers off;", "proxy_set_header Host api.sms.ir;",
        "proxy_set_header X-API-KEY $http_x_api_key;", "proxy_ssl_server_name on;",
        "proxy_ssl_name api.sms.ir;", "proxy_ssl_verify on;",
        "proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;",
        "proxy_connect_timeout 5s;", "proxy_send_timeout 10s;", "proxy_read_timeout 10s;",
        "location / { return 404; }",
    ):
        if required not in text:
            failures.append(f"SMS relay is missing fail-closed contract: {required}")
    if text.count("proxy_pass ") != 1:
        failures.append("SMS relay must have exactly one fixed upstream")
    for forbidden in (
        "resolver ", "proxy_pass $", "proxy_pass http://", "send/bulk", "ssl_verify off",
        "access_log /", "proxy_ssl_verify off", "proxy_pass_request_headers on",
    ):
        if forbidden in text:
            failures.append(f"SMS relay contains forbidden broad transport: {forbidden}")
    return failures


def verify_session_reset(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    normalized = text.lower()
    failures: list[str] = []
    for required in (
        "begin;", "update user_sessions", "set is_active = false",
        "update session_login_requests", "set status = 'expired'",
        "update single_session_recovery_requests", "set status = 'cancelled'",
        "commit;",
    ):
        if required not in normalized:
            failures.append(f"session reset is missing required isolated-state control: {required}")
    if "delete " in normalized or "update users" in normalized:
        failures.append("session reset must not delete or modify users")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=(AUTH_PROFILE_TELEGRAM_ONLY, AUTH_PROFILE_SMS_OTP),
        default=AUTH_PROFILE_TELEGRAM_ONLY,
        help="verify the default Telegram-only profile or the separately opted-in SMS profile",
    )
    parser.add_argument("--env", required=True)
    parser.add_argument("--compose", required=True)
    parser.add_argument("--nginx", required=True)
    parser.add_argument("--session-reset", required=True)
    parser.add_argument("--sms-compose")
    parser.add_argument("--sms-relay")
    parser.add_argument("--nginx-rate-limit")
    args = parser.parse_args()
    try:
        if args.profile == AUTH_PROFILE_SMS_OTP and not all(
            (args.sms_compose, args.sms_relay, args.nginx_rate_limit)
        ):
            raise EmergencyVerificationError(
                "SMS OTP verification requires --sms-compose, --sms-relay, and --nginx-rate-limit"
            )
        values = parse_env(Path(args.env))
        failures = verify_values(values, expected_profile=args.profile)
        failures.extend(verify_compose(Path(args.compose)))
        if args.profile == AUTH_PROFILE_SMS_OTP:
            failures.extend(
                verify_sms_otp_compose(Path(args.sms_compose))
            )
            failures.extend(
                verify_sms_egress_relay(Path(args.sms_relay))
            )
            failures.extend(
                verify_sms_otp_nginx(Path(args.nginx), Path(args.nginx_rate_limit))
            )
        else:
            failures.extend(verify_nginx(Path(args.nginx)))
        failures.extend(verify_session_reset(Path(args.session_reset)))
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
