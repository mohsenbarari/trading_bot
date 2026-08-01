#!/usr/bin/env python3
"""Render a fresh root-only runtime environment for Emergency IR Standalone.

This intentionally generates new local credentials on WA-IR.  The only copied
credential is a narrowly scoped Telegram WebApp initData validation token; it
never becomes BOT_TOKEN and cannot start a bot.  The runtime never copies sync,
witness, SMS, or web-push credentials.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import sys
from pathlib import Path


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
APP_IMAGE_RE = re.compile(r"^trading_bot_emergency_ir_app:([0-9a-f]{40})$")
BASE_IMAGE_RE = re.compile(r"^trading_bot_emergency_ir_(?:postgres|redis):[a-z0-9][a-z0-9._-]{0,127}$")
DOMAIN = "coin.gold-trade.ir"
RUNTIME_PATH = Path("/etc/trading-bot-emergency/standalone/runtime.env")
SETTINGS_PATH = Path("/srv/trading-bot-emergency/current/trading_settings.json")
NETWORK_GATEWAY = "172.29.250.1"


class EmergencyEnvError(RuntimeError):
    pass


def _require_root() -> None:
    if os.geteuid() != 0:
        raise EmergencyEnvError("Emergency runtime env must be rendered as root")


def _validate_output(path: Path) -> None:
    if path != RUNTIME_PATH:
        raise EmergencyEnvError(f"output must be exactly {RUNTIME_PATH}")
    if not path.is_absolute():
        raise EmergencyEnvError("output path must be absolute")


def _validate_release(
    source_release_sha: str,
    emergency_patch_sha: str,
    app_image: str,
    postgres_image: str,
    redis_image: str,
) -> None:
    if not SHA_RE.fullmatch(source_release_sha):
        raise EmergencyEnvError("source release SHA must be exactly 40 lowercase hex characters")
    if not SHA_RE.fullmatch(emergency_patch_sha):
        raise EmergencyEnvError("Emergency patch SHA must be exactly 40 lowercase hex characters")
    match = APP_IMAGE_RE.fullmatch(app_image)
    if match is None or match.group(1) != emergency_patch_sha:
        raise EmergencyEnvError("app image must be tagged by the exact Emergency patch SHA")
    if not BASE_IMAGE_RE.fullmatch(postgres_image):
        raise EmergencyEnvError("PostgreSQL image must use the emergency namespace")
    if not BASE_IMAGE_RE.fullmatch(redis_image):
        raise EmergencyEnvError("Redis image must use the emergency namespace")
    if "staging" in app_image.lower() or "three_site" in app_image.lower():
        raise EmergencyEnvError("staging/three-site image names are forbidden")


def _validate_webapp_initdata_token(raw_value: str) -> str:
    """Accept the token only for local Telegram WebApp HMAC verification.

    This runtime never starts a bot process and its Docker network has no
    external egress.  The token is nevertheless required for the production
    WebApp login endpoint to validate Telegram ``initData`` locally.
    """

    value = raw_value.strip()
    if not value or len(value) > 1024:
        raise EmergencyEnvError("Telegram WebApp validation token is invalid")
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise EmergencyEnvError("Telegram WebApp validation token is invalid")
    return value


def _secure_atomic_write(path: Path, payload: bytes) -> None:
    """Replace a root-owned regular file without following symlinks."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(path.parent, directory_flags)
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    temporary_fd = -1
    try:
        metadata = os.fstat(directory_fd)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise EmergencyEnvError("runtime env directory is not root-controlled")
        try:
            existing = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not stat.S_ISREG(existing.st_mode) or existing.st_uid != 0 or stat.S_IMODE(existing.st_mode) & 0o077:
                raise EmergencyEnvError("existing runtime env is not a root-only regular file")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        temporary_fd = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(temporary_fd, view[offset:])
            if written <= 0:
                raise EmergencyEnvError("runtime env write made no progress")
            offset += written
        os.fchmod(temporary_fd, 0o600)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        os.replace(temporary_name, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def render(
    *,
    output: Path,
    source_release_sha: str,
    emergency_patch_sha: str,
    app_image: str,
    postgres_image: str,
    redis_image: str,
    webapp_initdata_token: str,
) -> None:
    _require_root()
    _validate_output(output)
    _validate_release(
        source_release_sha, emergency_patch_sha, app_image, postgres_image, redis_image
    )
    webapp_initdata_token = _validate_webapp_initdata_token(webapp_initdata_token)
    password = secrets.token_urlsafe(48)
    jwt_secret = secrets.token_urlsafe(64)
    values = {
        "COMPOSE_PROJECT_NAME": "trading-bot-emergency-ir",
        "EMERGENCY_RUNTIME_ENV_FILE": str(RUNTIME_PATH),
        "EMERGENCY_APP_PORT": "18000",
        "EMERGENCY_TRADING_SETTINGS_FILE": str(SETTINGS_PATH),
        "SOURCE_RELEASE_SHA": source_release_sha,
        "EMERGENCY_PATCH_SHA": emergency_patch_sha,
        "RELEASE_SHA": emergency_patch_sha,
        "EMERGENCY_APP_IMAGE": app_image,
        "EMERGENCY_POSTGRES_IMAGE": postgres_image,
        "EMERGENCY_REDIS_IMAGE": redis_image,
        "TZ": "UTC",
        "PGTZ": "UTC",
        "SERVER_MODE": "iran",
        "ENVIRONMENT": "emergency-ir-standalone",
        "EMERGENCY_IR_STANDALONE": "true",
        "FRONTEND_URL": f"https://{DOMAIN}",
        "PUBLIC_WEBAPP_URL": f"https://{DOMAIN}",
        "POSTGRES_USER": "emergency_webapp",
        "POSTGRES_DB": "trading_bot_emergency",
        "POSTGRES_PASSWORD": password,
        "DATABASE_URL": f"postgresql+asyncpg://emergency_webapp:{password}@db/trading_bot_emergency",
        "SYNC_DATABASE_URL": f"postgresql://emergency_webapp:{password}@db/trading_bot_emergency",
        "REDIS_URL": "redis://redis:6379/0",
        "REDIS_HOST": "redis",
        "JWT_SECRET_KEY": jwt_secret,
        "DEV_API_KEY": secrets.token_urlsafe(32),
        # This is used only by /api/auth/webapp-login for Telegram initData
        # HMAC verification.  No bot service is present in this Compose file.
        "WEBAPP_INITDATA_BOT_TOKEN": webapp_initdata_token,
        "OBSERVABILITY_TELEGRAM_USER_HASH_SALT": secrets.token_urlsafe(32),
        "TRUSTED_PROXY_CIDRS": f"127.0.0.1/32,{NETWORK_GATEWAY}/32,::1/128",
        "BACKGROUND_JOBS_ENABLED": "false",
        "TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH": "true",
        "WEB_PUSH_ENABLED": "false",
        "TELEGRAM_DIRECT_REGISTRATION_ENABLED": "false",
        "TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED": "false",
        "TELEGRAM_LOGIN_OTP_ENABLED": "false",
        "OTP_SMS_AUTO_FALLBACK_ENABLED": "false",
        "INVITATION_SMS_STANDARD_ENABLED": "false",
        "INVITATION_SMS_CUSTOMER_TIER1_ENABLED": "false",
        "INVITATION_SMS_ACCOUNTANT_ENABLED": "false",
        "INVITATION_SMS_CUSTOMER_TIER2_ENABLED": "false",
        "INVITATION_CONTRACT_V2_ENABLED": "false",
        "REGISTRATION_SYNC_V2_ENABLED": "false",
        "REGISTRATION_SYNC_ACCEPT_UNVERSIONED": "false",
        "DB_POOL_SIZE": "8",
        "DB_MAX_OVERFLOW": "4",
        "DB_POOL_RECYCLE_SECONDS": "3600",
        "POSTGRES_MAX_CONNECTIONS": "80",
        "POSTGRES_SHARED_BUFFERS": "256MB",
        "POSTGRES_EFFECTIVE_CACHE_SIZE": "768MB",
        "POSTGRES_WORK_MEM": "4MB",
        "POSTGRES_MAINTENANCE_WORK_MEM": "128MB",
        "REDIS_APPENDONLY": "yes",
        "REDIS_APPENDFSYNC": "everysec",
        "REDIS_MAXMEMORY": "192mb",
        "REDIS_MAXMEMORY_POLICY": "noeviction",
        "DOCKER_LOG_MAX_SIZE": "20m",
        "DOCKER_LOG_MAX_FILE": "5",
        "LOG_FORMAT": "json",
        "LOG_LEVEL": "INFO",
    }
    forbidden = {
        "BOT_TOKEN", "SYNC_API_KEY", "PEER_SERVER_URL", "IRAN_SERVER_URL",
        "GERMANY_SERVER_URL", "FOREIGN_SERVER_URL", "SMSIR_API_KEY",
        "WEB_PUSH_VAPID_PRIVATE_KEY", "WRITER_WITNESS_CLIENT_SECRET",
    }
    if forbidden & values.keys():
        raise EmergencyEnvError("renderer attempted to include a forbidden cross-site credential")
    payload = (
        "# Generated locally on WA-IR. This file contains independent Emergency credentials.\n"
        + "\n".join(f"{key}={values[key]}" for key in sorted(values))
        + "\n"
    ).encode("utf-8")
    _secure_atomic_write(output, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-release-sha", required=True)
    parser.add_argument("--emergency-patch-sha", required=True)
    parser.add_argument("--app-image", required=True)
    parser.add_argument("--postgres-image", required=True)
    parser.add_argument("--redis-image", required=True)
    parser.add_argument(
        "--webapp-initdata-token-stdin",
        action="store_true",
        required=True,
        help="read the Telegram WebApp HMAC token from stdin without persisting it separately",
    )
    args = parser.parse_args()
    raw_token = sys.stdin.buffer.read(1025)
    if len(raw_token) > 1024:
        raise EmergencyEnvError("Telegram WebApp validation token is invalid")
    try:
        webapp_initdata_token = raw_token.decode("ascii")
    except UnicodeDecodeError as exc:
        raise EmergencyEnvError("Telegram WebApp validation token is invalid") from exc
    render(
        output=Path(args.output),
        source_release_sha=args.source_release_sha,
        emergency_patch_sha=args.emergency_patch_sha,
        app_image=args.app_image,
        postgres_image=args.postgres_image,
        redis_image=args.redis_image,
        webapp_initdata_token=webapp_initdata_token,
    )
    print("Emergency IR standalone runtime env rendered; values suppressed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
