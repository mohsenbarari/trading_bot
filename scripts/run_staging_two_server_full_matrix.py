#!/usr/bin/env python3
"""Plan and preflight the real two-server staging full matrix.

The runner is fail-closed. `plan` and `preflight` are non-mutating. Mutating
scenario execution must only be added after the real two-server staging
topology passes preflight and the command drivers are reviewed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import shlex
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import build_staging_two_server_full_matrix_manifest as manifest_builder


SCHEMA_VERSION = "staging_two_server_full_matrix_runner_v1"
DEFAULT_EXPECTED_BRANCH = os.getenv(
    "STAGING_TWO_SERVER_FULL_MATRIX_EXPECTED_BRANCH",
    "candidate/sync-parity-hardening",
)
DEFAULT_IRAN_BASE_URL = "https://staging.gold-trade.ir"
DEFAULT_FOREIGN_BASE_URL = "https://staging.362514.ir"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "staging-two-server-full-matrix"
CLAUDE_LOG_ROOT = REPO_ROOT / "tmp" / "claude" / "full_matrix_logs"
CHATGPT_LOG_ROOT = REPO_ROOT / "tmp" / "chatgpt" / "full_matrix_logs"
EXECUTION_CONFIRM_ENV = "STAGING_TWO_SERVER_FULL_MATRIX_CONFIRM"
EXECUTION_CONFIRM_VALUE = "execute-staging-two-server-full-matrix"
DEFAULT_IRAN_SSH_HOST = "root@65.109.220.59"
DEFAULT_IRAN_SSH_PORT = "37067"
WA_IR_OBJECT_STORAGE_ONLY_IP = "95.38.164.29"
DEFAULT_IRAN_APP_CONTAINER = "trading_bot_staging_iran-app-1"
DEFAULT_FOREIGN_APP_CONTAINER = "trading_bot_staging-foreign_app-1"
DEFAULT_IRAN_WORKDIR = "/srv/trading-bot/staging-iran"
LOCAL_STAGING_PROJECT_NAME = "trading_bot_staging"
LOCAL_STAGING_COMPOSE_FILE = REPO_ROOT / "deploy" / "staging" / "docker-compose.staging.yml"
LOCAL_STAGING_ENV_FILE = REPO_ROOT / ".env.staging"
REMOTE_STAGING_PROJECT_NAME = "trading_bot_staging_iran"
REMOTE_STAGING_COMPOSE_FILE = "deploy/staging/docker-compose.staging.yml"
REMOTE_STAGING_ENV_FILE = ".env.staging"
ROLE_START_BARRIER_DELAY_SECONDS = 30.0
RACE_START_BARRIER_DELAY_SECONDS = 90.0

DRIVER_SCENARIOS = [
    {
        "id": "DRIVER-WEBAPP-WHOLESALE-MIXED-BUY",
        "offer_origin": "webapp",
        "offer_type": "buy",
        "retail": False,
        "lot_sizes": "",
        "hot_offer_quantity": 5,
        "request_amount": 5,
        "expected_winner_count": 1,
        "expected_remaining_quantity": 0,
        "request_surface": "mixed",
        "telegram_ratio": 0.5,
        "hot_offer_requests": 8,
        "target_rps": 4.0,
        "idempotency_mode": "unique",
        "coverage": ["iran_to_iran", "iran_to_foreign", "wholesale_full", "notification_delivery"],
    },
    {
        "id": "DRIVER-BOT-WHOLESALE-MIXED-SELL",
        "offer_origin": "bot",
        "offer_type": "sell",
        "retail": False,
        "lot_sizes": "",
        "hot_offer_quantity": 5,
        "request_amount": 5,
        "expected_winner_count": 1,
        "expected_remaining_quantity": 0,
        "request_surface": "mixed",
        "telegram_ratio": 0.5,
        "hot_offer_requests": 8,
        "target_rps": 4.0,
        "idempotency_mode": "unique",
        "coverage": ["foreign_to_iran", "foreign_to_foreign", "wholesale_full", "publication_terminal_state"],
    },
    {
        "id": "DRIVER-WEBAPP-RETAIL-TWO-LOT-MIXED-SELL",
        "offer_origin": "webapp",
        "offer_type": "sell",
        "retail": True,
        "lot_sizes": "10,10",
        "hot_offer_quantity": 20,
        "request_amount": 10,
        "expected_winner_count": 2,
        "expected_remaining_quantity": 0,
        "request_surface": "mixed",
        "telegram_ratio": 0.5,
        "hot_offer_requests": 12,
        "target_rps": 4.0,
        "idempotency_mode": "unique",
        "coverage": ["retail_two_lot", "same_lot_contention", "mixed_surface_requests"],
    },
    {
        "id": "DRIVER-BOT-DUPLICATE-REPLAY-BUY",
        "offer_origin": "bot",
        "offer_type": "buy",
        "retail": False,
        "lot_sizes": "",
        "hot_offer_quantity": 5,
        "request_amount": 5,
        "expected_winner_count": 1,
        "expected_remaining_quantity": 0,
        "request_surface": "telegram",
        "telegram_ratio": 1.0,
        "hot_offer_requests": 6,
        "target_rps": 3.0,
        "idempotency_mode": "duplicate_replay",
        "coverage": ["idempotency_no_duplicate_trade", "foreign_home_offer", "telegram_only_replay"],
    },
    {
        "id": "DRIVER-WEBAPP-MANUAL-EXPIRY-RACE-SELL",
        "scenario_name": "manual_expire_trade_race",
        "offer_origin": "webapp",
        "offer_type": "sell",
        "retail": False,
        "lot_sizes": "",
        "hot_offer_quantity": 5,
        "request_amount": 5,
        "expected_winner_count": 1,
        "expected_remaining_quantity": 0,
        "request_surface": "webapp",
        "telegram_ratio": 0.0,
        "hot_offer_requests": 3,
        "target_rps": 3.0,
        "idempotency_mode": "unique",
        "require_terminal_completed": False,
        "race_kind": "manual_expiry",
        "coverage": ["trade_vs_manual_expiry", "bounded_contention", "iran_home_offer"],
    },
    {
        "id": "DRIVER-BOT-TIME-EXPIRY-RACE-BUY",
        "scenario_name": "time_expire_trade_race",
        "offer_origin": "bot",
        "offer_type": "buy",
        "retail": False,
        "lot_sizes": "",
        "hot_offer_quantity": 5,
        "request_amount": 5,
        "expected_winner_count": 1,
        "expected_remaining_quantity": 0,
        "request_surface": "telegram",
        "telegram_ratio": 1.0,
        "hot_offer_requests": 3,
        "target_rps": 3.0,
        "idempotency_mode": "unique",
        "require_terminal_completed": False,
        "race_kind": "time_expiry",
        "coverage": ["trade_vs_time_expiry", "bounded_contention", "foreign_home_offer"],
    },
]

FORBIDDEN_PRODUCTION_HOSTS = {
    "coin.gold-trade.ir",
    "coin.362514.ir",
    "iran.362514.ir",
    "kharej.362514.ir",
}
SECRET_PATTERNS = [
    re.compile(r"(?i)(bot[_-]?token|api[_-]?key|password|secret|jwt)[=:]\s*[^,\s]+"),
    re.compile(r"\b\d{10,15}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b09\d{9}\b"),
]
SECRET_KEY_PATTERN = re.compile(
    r"(?i)(^|[_-])(bot[_-]?token|api[_-]?key|password|secret|jwt|observability[_-]?api[_-]?key)$"
)
FORBIDDEN_SECRET_PATTERNS = [
    re.compile(r"(?i)(bot[_-]?token|api[_-]?key|password|secret|jwt)[=:]\s*(?!\[REDACTED\])[^,\s]+"),
    re.compile(r'(?i)"(bot[_-]?token|api[_-]?key|password|secret|jwt|observability[^"]*)"\s*:\s*"(?!\[REDACTED\])[^"]+"'),
    re.compile(r"\b\d{10,15}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b09\d{9}\b"),
]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    elapsed_seconds: float = 0.0
    payload: dict[str, Any] | None = None

    def asdict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "payload": self.payload or {},
        }


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    status: str
    returncode: int
    elapsed_seconds: float
    stdout_path: str
    stderr_path: str
    json_payload: dict[str, Any] | None = None

    def asdict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "returncode": self.returncode,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "command": redact_command(self.command),
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "json_payload": self.json_payload or {},
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_run_id() -> str:
    return f"S2FM-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"


def sanitize_text(value: str) -> str:
    sanitized = value
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub(lambda match: match.group(0).split("=", 1)[0] + "=[REDACTED]" if "=" in match.group(0) else "[REDACTED]", sanitized)
    return sanitized


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if SECRET_KEY_PATTERN.search(text_key) and item not in (None, "", False):
                sanitized[text_key] = "[REDACTED]"
            else:
                sanitized[text_key] = sanitize_payload(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item) for item in value]
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize_payload(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sanitize_payload(payload), ensure_ascii=False, sort_keys=True) + "\n")


def redact_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    secret_flags = {"--basic-auth-password", "--observability-api-key"}
    for part in command:
        if skip_next:
            redacted.append("[REDACTED]")
            skip_next = False
            continue
        if part in secret_flags:
            redacted.append(part)
            skip_next = True
            continue
        redacted.append(sanitize_text(part))
    return redacted


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sanitize_text(content), encoding="utf-8")


def detect_forbidden_secret_like_values(root: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if not root.exists():
        return {"detected": False, "findings": findings}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "redaction-report.json":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in FORBIDDEN_SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(
                    {
                        "path": str(path.relative_to(root)),
                        "pattern": pattern.pattern,
                        "sample_sha256": hashlib.sha256(match.group(0).encode("utf-8")).hexdigest()[:16],
                    }
                )
                break
    return {"detected": bool(findings), "findings": findings[:50]}


def run_git_value(args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def expected_release_sha(args: argparse.Namespace) -> str | None:
    if args.expected_release_sha:
        return args.expected_release_sha
    return run_git_value(["rev-parse", "--short=12", "HEAD"])


def host_of(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return (parsed.hostname or "").strip().lower()


def validate_staging_url(name: str, url: str, expected_host: str | None = None) -> CheckResult:
    started = time.perf_counter()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return CheckResult(name, "failed", "staging URL must use https", time.perf_counter() - started, {"url": url})
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return CheckResult(name, "failed", "staging URL has no hostname", time.perf_counter() - started, {"url": url})
    if hostname in FORBIDDEN_PRODUCTION_HOSTS:
        return CheckResult(name, "failed", "URL points to a forbidden production/retired host", time.perf_counter() - started, {"hostname": hostname})
    if expected_host and hostname != expected_host:
        return CheckResult(name, "failed", "URL hostname does not match expected staging host", time.perf_counter() - started, {"hostname": hostname, "expected_host": expected_host})
    return CheckResult(name, "passed", "URL identity is staging-safe", time.perf_counter() - started, {"hostname": hostname})


def fetch_json(url: str, *, basic_auth: tuple[str, str] | None, timeout_seconds: float = 10.0) -> tuple[int, dict[str, Any] | None, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "staging-two-server-full-matrix/1"})
    if basic_auth:
        raw = f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")
        request.add_header("Authorization", "Basic " + base64.b64encode(raw).decode("ascii"))
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=ssl.create_default_context()) as response:
            body = response.read(1024 * 1024).decode("utf-8", errors="replace")
            status_code = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read(8192).decode("utf-8", errors="replace")
        return int(exc.code), None, body
    except Exception as exc:  # noqa: BLE001 - preflight reports exact network failure type.
        return 0, None, f"{type(exc).__name__}: {exc}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return status_code, None, body[:8192]
    return status_code, payload if isinstance(payload, dict) else None, body[:8192]


def fetch_status(
    url: str,
    *,
    basic_auth: tuple[str, str] | None = None,
    method: str = "HEAD",
    timeout_seconds: float = 10.0,
) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "staging-two-server-full-matrix/1"},
        method=method,
    )
    if basic_auth:
        raw = f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")
        request.add_header("Authorization", "Basic " + base64.b64encode(raw).decode("ascii"))
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=ssl.create_default_context()) as response:
            body = response.read(8192).decode("utf-8", errors="replace")
            return int(response.status), dict(response.headers.items()), body
    except urllib.error.HTTPError as exc:
        body = exc.read(8192).decode("utf-8", errors="replace")
        return int(exc.code), dict(exc.headers.items()), body
    except Exception as exc:  # noqa: BLE001
        return 0, {}, f"{type(exc).__name__}: {exc}"


def fetch_observability_json(
    url: str,
    observability_key: str,
    *,
    basic_auth: tuple[str, str] | None,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, dict[str, Any] | None, str]:
    body: bytes | None = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "staging-two-server-full-matrix/1",
        "X-Observability-Api-Key": observability_key,
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(sanitize_payload(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    if basic_auth:
        raw = f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")
        request.add_header("Authorization", "Basic " + base64.b64encode(raw).decode("ascii"))
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=ssl.create_default_context()) as response:
            raw_body = response.read(1024 * 1024 * 5).decode("utf-8", errors="replace")
            status_code = int(response.status)
    except urllib.error.HTTPError as exc:
        raw_body = exc.read(8192).decode("utf-8", errors="replace")
        return int(exc.code), None, raw_body
    except Exception as exc:  # noqa: BLE001
        return 0, None, f"{type(exc).__name__}: {exc}"
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        return status_code, None, raw_body[:8192]
    return status_code, parsed if isinstance(parsed, dict) else None, raw_body[:8192]


def check_http_json(name: str, url: str, *, basic_auth: tuple[str, str] | None) -> CheckResult:
    started = time.perf_counter()
    status_code, payload, raw = fetch_json(url, basic_auth=basic_auth)
    elapsed = time.perf_counter() - started
    if status_code != 200 or payload is None:
        return CheckResult(
            name,
            "failed",
            f"HTTP JSON check failed with status={status_code}",
            elapsed,
            {"url": url, "status_code": status_code, "body": sanitize_text(raw[:1000])},
        )
    return CheckResult(name, "passed", "HTTP JSON check passed", elapsed, {"url": url, "status_code": status_code, "payload": payload})


def check_foreign_public_surface_guard(name: str, base_url: str, *, basic_auth: tuple[str, str] | None) -> CheckResult:
    """Foreign staging must not expose the WebApp/public config surface."""
    started = time.perf_counter()
    url = base_url.rstrip("/") + "/api/config"
    status_code, payload, raw = fetch_json(url, basic_auth=basic_auth)
    elapsed = time.perf_counter() - started
    if status_code == 404:
        return CheckResult(
            name,
            "passed",
            "foreign public config is blocked as expected",
            elapsed,
            {"url": url, "status_code": status_code},
        )
    if status_code == 200 and isinstance(payload, dict):
        return CheckResult(
            name,
            "failed",
            "foreign staging exposes public WebApp config; foreign must not serve WebApp surface",
            elapsed,
            {"url": url, "status_code": status_code, "payload_keys": sorted(payload.keys())},
        )
    return CheckResult(
        name,
        "failed",
        f"foreign public config guard returned unexpected status={status_code}",
        elapsed,
        {"url": url, "status_code": status_code, "body": sanitize_text(raw[:1000])},
    )


def check_internal_ingress_without_basic_auth(name: str, url: str) -> CheckResult:
    started = time.perf_counter()
    status_code, headers, raw = fetch_status(url, basic_auth=None, method="HEAD")
    elapsed = time.perf_counter() - started
    authenticate_header = str(headers.get("WWW-Authenticate") or headers.get("www-authenticate") or "")
    if status_code == 401 and ("basic" in raw.lower() or "basic" in authenticate_header.lower()):
        return CheckResult(
            name,
            "failed",
            "internal signed endpoint is still blocked by staging Basic Auth",
            elapsed,
            {
                "url": url,
                "status_code": status_code,
                "www_authenticate": sanitize_text(authenticate_header[:200]),
                "body": sanitize_text(raw[:500]),
            },
        )
    if status_code in {400, 401, 405, 422}:
        return CheckResult(
            name,
            "passed",
            "internal signed endpoint reaches FastAPI without Basic Auth",
            elapsed,
            {
                "url": url,
                "status_code": status_code,
                "www_authenticate": sanitize_text(authenticate_header[:200]),
                "body": sanitize_text(raw[:500]),
            },
        )
    return CheckResult(
        name,
        "failed",
        f"internal signed endpoint returned unexpected status={status_code}",
        elapsed,
        {"url": url, "status_code": status_code, "body": sanitize_text(raw[:1000])},
    )


def check_tls(name: str, base_url: str) -> CheckResult:
    started = time.perf_counter()
    hostname = host_of(base_url)
    if not hostname:
        return CheckResult(name, "failed", "missing hostname", time.perf_counter() - started)
    try:
        with socket.create_connection((hostname, 443), timeout=8) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=hostname) as tls:
                cert = tls.getpeercert()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name, "failed", f"TLS verification failed: {type(exc).__name__}: {exc}", time.perf_counter() - started, {"hostname": hostname})
    return CheckResult(name, "passed", "TLS verification passed", time.perf_counter() - started, {"hostname": hostname, "subject": cert.get("subject", [])})


def runtime_identity_python() -> str:
    return (
        "import json; "
        "from core.config import settings; "
        "print(json.dumps({"
        "'environment': settings.environment, "
        "'server_mode': settings.server_mode, "
        "'service': settings.trading_bot_service, "
        "'release_sha': settings.release_sha, "
        "'frontend_url': settings.frontend_url, "
        "'iran_server_url': settings.iran_server_url, "
        "'germany_server_url': settings.germany_server_url, "
        "'foreign_server_url': settings.foreign_server_url, "
        "'peer_server_url': settings.peer_server_url, "
        "'bot_token_configured': bool(settings.bot_token), "
        "'channel_id_configured': bool(settings.channel_id)"
        "}, sort_keys=True))"
    )


def storage_identity_python() -> str:
    return (
        "import asyncio, hashlib, json\n"
        "from sqlalchemy import text\n"
        "import redis.asyncio as redis\n"
        "from core.config import settings\n"
        "from core.db import AsyncSessionLocal\n"
        "async def main():\n"
        "    async with AsyncSessionLocal() as db:\n"
        "        row=(await db.execute(text(\"select pcs.system_identifier::text as cluster_id, current_database() as db from pg_control_system() as pcs\"))).mappings().one()\n"
        "    redis_run_id=''\n"
        "    redis_errors=0\n"
        "    try:\n"
        "        client=redis.Redis.from_url(settings.redis_url, decode_responses=True)\n"
        "        try:\n"
        "            info=await client.info('server')\n"
        "            redis_run_id=str(info.get('run_id') or '')\n"
        "        finally:\n"
        "            await client.aclose()\n"
        "    except Exception:\n"
        "        redis_errors=1\n"
        "    db_material=f\"{row['cluster_id']}:{row['db']}\"\n"
        "    redis_material=redis_run_id or 'missing'\n"
        "    print(json.dumps({"
        "'server_mode': settings.server_mode, "
        "'environment': settings.environment, "
        "'release_sha': settings.release_sha, "
        "'database_identity_hash': hashlib.sha256(db_material.encode()).hexdigest()[:16], "
        "'redis_identity_hash': hashlib.sha256(redis_material.encode()).hexdigest()[:16], "
        "'redis_errors': redis_errors"
        "}, sort_keys=True))\n"
        "asyncio.run(main())"
    )


def is_local_compose_url(value: Any) -> bool:
    hostname = host_of(str(value or "")).lower()
    return hostname in {"", "app", "foreign_app", "localhost", "127.0.0.1", "0.0.0.0"}


def normalized_url_host(value: Any) -> str:
    return host_of(str(value or "")).lower()


def run_json_command(command: list[str], *, timeout_seconds: float = 10.0) -> tuple[int, dict[str, Any] | None, str, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except Exception as exc:  # noqa: BLE001
        return 0, None, "", f"{type(exc).__name__}: {exc}"
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    try:
        payload = json.loads(stdout.splitlines()[-1]) if stdout else None
    except json.JSONDecodeError:
        payload = None
    return int(result.returncode), payload if isinstance(payload, dict) else None, stdout, stderr


def parse_last_json_object(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def command_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")[:120] or "command"


def run_logged_command(
    name: str,
    command: list[str],
    *,
    log_dir: Path,
    timeout_seconds: float = 300.0,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> CommandResult:
    log_dir.mkdir(parents=True, exist_ok=True)
    slug = command_slug(name)
    stdout_path = log_dir / f"{slug}.stdout.log"
    stderr_path = log_dir / f"{slug}.stderr.log"
    meta_path = log_dir / f"{slug}.command.json"
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = sanitize_text(result.stdout or "")
        stderr = sanitize_text(result.stderr or "")
        returncode = int(result.returncode)
        status_value = "passed" if returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        stdout = sanitize_text((exc.stdout or "") if isinstance(exc.stdout, str) else "")
        stderr = sanitize_text((exc.stderr or "") if isinstance(exc.stderr, str) else "")
        stderr = (stderr + "\n" if stderr else "") + f"TimeoutExpired after {timeout_seconds}s"
        returncode = 124
        status_value = "failed"
    elapsed = time.perf_counter() - started
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    payload = parse_last_json_object(stdout)
    command_result = CommandResult(
        name=name,
        command=command,
        status=status_value,
        returncode=returncode,
        elapsed_seconds=elapsed,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        json_payload=payload,
    )
    write_json(meta_path, command_result.asdict())
    return command_result


def run_logged_commands_parallel(
    commands: list[tuple[str, list[str]]],
    *,
    log_dir: Path,
    timeout_seconds: float = 300.0,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> list[CommandResult]:
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    processes: list[tuple[str, list[str], Path, Path, subprocess.Popen[str]]] = []
    for name, command in commands:
        slug = command_slug(name)
        stdout_path = log_dir / f"{slug}.stdout.log"
        stderr_path = log_dir / f"{slug}.stderr.log"
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append((name, command, stdout_path, stderr_path, process))

    deadline = time.monotonic() + timeout_seconds
    results: list[CommandResult] = []
    for name, command, stdout_path, stderr_path, process in processes:
        remaining = max(1.0, deadline - time.monotonic())
        try:
            stdout_raw, stderr_raw = process.communicate(timeout=remaining)
            returncode = int(process.returncode or 0)
            status_value = "passed" if returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            process.kill()
            stdout_raw, stderr_raw = process.communicate()
            returncode = 124
            status_value = "failed"
            stderr_raw = (stderr_raw or "") + f"\nTimeoutExpired after {timeout_seconds}s"
        stdout = sanitize_text(stdout_raw or "")
        stderr = sanitize_text(stderr_raw or "")
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        command_result = CommandResult(
            name=name,
            command=command,
            status=status_value,
            returncode=returncode,
            elapsed_seconds=time.perf_counter() - started,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            json_payload=parse_last_json_object(stdout),
        )
        write_json(log_dir / f"{command_slug(name)}.command.json", command_result.asdict())
        results.append(command_result)
    return results


def require_command_success(result: CommandResult) -> None:
    if result.status != "passed":
        raise RuntimeError(f"{result.name} failed with returncode={result.returncode}; see {result.stderr_path}")


def local_load_runner_command(
    service: str,
    artifact_dir: Path,
    worker_args: list[str],
    *,
    args: argparse.Namespace | None = None,
) -> list[str]:
    absolute_artifact_dir = artifact_dir.resolve()
    iran_peer_url = (getattr(args, "iran_base_url", None) or DEFAULT_IRAN_BASE_URL).rstrip("/")
    return [
        "docker",
        "compose",
        "-p",
        LOCAL_STAGING_PROJECT_NAME,
        "--env-file",
        str(LOCAL_STAGING_ENV_FILE),
        "-f",
        str(LOCAL_STAGING_COMPOSE_FILE),
        "--profile",
        "staging-load",
        "run",
        "--rm",
        "--no-deps",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        f"IRAN_SERVER_URL={iran_peer_url}",
        "-e",
        "GERMANY_SERVER_URL=http://foreign_app:8000",
        "-e",
        "FOREIGN_SERVER_URL=http://foreign_app:8000",
        "-v",
        f"{REPO_ROOT}:/app:ro",
        "-v",
        f"{absolute_artifact_dir}:/artifacts",
        service,
        "python",
        "scripts/trading_core_probe_worker.py",
        *worker_args,
    ]


def iran_ssh_command(args: argparse.Namespace, remote_command: str) -> list[str]:
    return ["ssh", "-p", str(getattr(args, "iran_ssh_port", DEFAULT_IRAN_SSH_PORT)), args.iran_ssh_host, remote_command]


def remote_shell_command(args: argparse.Namespace, inner: str) -> list[str]:
    return iran_ssh_command(args, f"cd {shlex.quote(args.iran_workdir)} && {inner}")


def remote_load_runner_command(args: argparse.Namespace, service: str, remote_artifact_dir: str, worker_args: list[str]) -> list[str]:
    quoted_worker = " ".join(shlex.quote(part) for part in worker_args)
    foreign_peer_url = args.foreign_base_url.rstrip("/") + "/foreign-sync"
    inner = (
        f"mkdir -p {shlex.quote(remote_artifact_dir)} && "
        "if docker compose version >/dev/null 2>&1; then compose=(docker compose); "
        "elif command -v docker-compose >/dev/null 2>&1; then compose=(docker-compose); "
        "else echo 'Docker Compose is unavailable on Iran staging.' >&2; exit 1; fi; "
        f"STAGING_RELEASE_SHA={shlex.quote(expected_release_sha(args) or '')} "
        "STAGING_APP_PORT=8100 "
        "STAGING_FRONTEND_DOCKER_DIST_DIR=mini_app_dist_staging "
        '"${compose[@]}" '
        f"--env-file {shlex.quote(REMOTE_STAGING_ENV_FILE)} "
        f"-p {shlex.quote(REMOTE_STAGING_PROJECT_NAME)} "
        f"-f {shlex.quote(REMOTE_STAGING_COMPOSE_FILE)} "
        "run --rm --no-deps "
        "-e PYTHONDONTWRITEBYTECODE=1 "
        f"-e GERMANY_SERVER_URL={shlex.quote(foreign_peer_url)} "
        f"-e FOREIGN_SERVER_URL={shlex.quote(foreign_peer_url)} "
        "-e IRAN_SERVER_URL=http://app:8000 "
        f"-v {shlex.quote(args.iran_workdir)}:/app:ro "
        f"-v {shlex.quote(remote_artifact_dir)}:/artifacts "
        f"{shlex.quote(service)} "
        "python scripts/trading_core_probe_worker.py "
        f"{quoted_worker}"
    )
    return remote_shell_command(args, inner)


def scp_from_iran(args: argparse.Namespace, remote_path: str, local_path: Path) -> list[str]:
    return [
        "scp",
        "-P",
        str(getattr(args, "iran_ssh_port", DEFAULT_IRAN_SSH_PORT)),
        f"{args.iran_ssh_host}:{remote_path}",
        str(local_path),
    ]


def scp_to_iran(args: argparse.Namespace, local_path: Path, remote_path: str) -> list[str]:
    return [
        "scp",
        "-P",
        str(getattr(args, "iran_ssh_port", DEFAULT_IRAN_SSH_PORT)),
        str(local_path),
        f"{args.iran_ssh_host}:{remote_path}",
    ]


def run_local_worker(
    name: str,
    *,
    args: argparse.Namespace | None = None,
    service: str,
    artifact_dir: Path,
    worker_args: list[str],
    log_dir: Path,
    timeout_seconds: float = 300.0,
) -> CommandResult:
    return run_logged_command(
        name,
        local_load_runner_command(service, artifact_dir, worker_args, args=args),
        log_dir=log_dir,
        timeout_seconds=timeout_seconds,
    )


def run_remote_worker(
    name: str,
    *,
    args: argparse.Namespace,
    service: str,
    remote_artifact_dir: str,
    worker_args: list[str],
    log_dir: Path,
    timeout_seconds: float = 300.0,
) -> CommandResult:
    return run_logged_command(
        name,
        remote_load_runner_command(args, service, remote_artifact_dir, worker_args),
        log_dir=log_dir,
        timeout_seconds=timeout_seconds,
    )


def check_container_runtime_identity(
    name: str,
    *,
    server: str,
    expected_server_mode: str,
    expected_release: str | None,
    args: argparse.Namespace,
) -> CheckResult:
    started = time.perf_counter()
    if server == "foreign":
        command = ["docker", "exec", args.foreign_app_container, "python", "-c", runtime_identity_python()]
    elif server == "iran":
        command = iran_ssh_command(
            args,
            f"docker exec {args.iran_app_container} python -c {json.dumps(runtime_identity_python())}",
        )
    else:
        return CheckResult(name, "failed", f"unsupported runtime identity server: {server}")
    returncode, payload, stdout, stderr = run_json_command(command)
    elapsed = time.perf_counter() - started
    if returncode != 0 or payload is None:
        return CheckResult(
            name,
            "failed",
            f"runtime identity command failed returncode={returncode}",
            elapsed,
            {"stdout": sanitize_text(stdout[:1000]), "stderr": sanitize_text(stderr[:1000])},
        )
    failures: list[str] = []
    if str(payload.get("environment") or "").lower() != "staging":
        failures.append("ENVIRONMENT is not staging")
    if str(payload.get("server_mode") or "").lower() != expected_server_mode:
        failures.append("SERVER_MODE mismatch")
    if expected_release and str(payload.get("release_sha") or "") != expected_release:
        failures.append("RELEASE_SHA does not match expected candidate SHA")
    if expected_server_mode == "iran" and payload.get("bot_token_configured"):
        failures.append("Iran staging app has Telegram bot token configured")
    if expected_server_mode == "iran" and payload.get("channel_id_configured"):
        failures.append("Iran staging app has Telegram channel id configured")
    if expected_server_mode == "foreign":
        iran_host = normalized_url_host(payload.get("iran_server_url"))
        if iran_host != host_of(DEFAULT_IRAN_BASE_URL):
            failures.append("foreign staging IRAN_SERVER_URL must point to real Iran staging")
        if is_local_compose_url(payload.get("iran_server_url")):
            failures.append("foreign staging IRAN_SERVER_URL points to local compose, not Iran staging")
    if expected_server_mode == "iran":
        foreign_host = normalized_url_host(payload.get("germany_server_url") or payload.get("foreign_server_url"))
        if foreign_host != host_of(DEFAULT_FOREIGN_BASE_URL):
            failures.append("Iran staging foreign peer URL must point to real foreign staging")
        if is_local_compose_url(payload.get("germany_server_url") or payload.get("foreign_server_url")):
            failures.append("Iran staging foreign peer URL points to local compose, not foreign staging")
    return CheckResult(
        name,
        "failed" if failures else "passed",
        "; ".join(failures) if failures else "runtime identity verified",
        elapsed,
        {
            "server": server,
            "expected_server_mode": expected_server_mode,
            "expected_release_sha": expected_release,
            "runtime": payload,
        },
    )


def storage_identity_command(server: str, args: argparse.Namespace) -> list[str]:
    script = storage_identity_python()
    wrapper = f"import base64; exec(base64.b64decode({base64.b64encode(script.encode()).decode('ascii')!r}))"
    if server == "foreign":
        return ["docker", "exec", args.foreign_app_container, "python", "-c", wrapper]
    if server == "iran":
        return iran_ssh_command(
            args,
            f"docker exec {shlex.quote(args.iran_app_container)} python -c {shlex.quote(wrapper)}",
        )
    raise ValueError(f"unsupported storage identity server: {server}")


def check_storage_identity_separation(name: str, *, args: argparse.Namespace) -> CheckResult:
    started = time.perf_counter()
    payloads: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for server in ("iran", "foreign"):
        returncode, payload, stdout, stderr = run_json_command(storage_identity_command(server, args), timeout_seconds=20.0)
        if returncode != 0 or payload is None:
            failures.append(f"{server} storage identity command failed")
            payloads[server] = {
                "stdout": sanitize_text(stdout[:1000]),
                "stderr": sanitize_text(stderr[:1000]),
            }
            continue
        payloads[server] = payload
    iran_payload = payloads.get("iran") or {}
    foreign_payload = payloads.get("foreign") or {}
    if iran_payload and str(iran_payload.get("server_mode") or "").lower() != "iran":
        failures.append("Iran storage identity server_mode mismatch")
    if foreign_payload and str(foreign_payload.get("server_mode") or "").lower() != "foreign":
        failures.append("foreign storage identity server_mode mismatch")
    if iran_payload and foreign_payload:
        if iran_payload.get("database_identity_hash") == foreign_payload.get("database_identity_hash"):
            failures.append("Iran and foreign staging database identities match")
        if iran_payload.get("redis_identity_hash") == foreign_payload.get("redis_identity_hash"):
            failures.append("Iran and foreign staging Redis identities match")
        if int(iran_payload.get("redis_errors") or 0) or int(foreign_payload.get("redis_errors") or 0):
            failures.append("Redis identity probe reported errors")
    return CheckResult(
        name,
        "failed" if failures else "passed",
        "; ".join(failures) if failures else "database and Redis identities are separate",
        time.perf_counter() - started,
        {"identities": payloads},
    )


def basic_auth_from_args(args: argparse.Namespace) -> tuple[str, str] | None:
    user = args.basic_auth_user or os.getenv("STAGING_BASIC_AUTH_USER")
    password = args.basic_auth_password or os.getenv("STAGING_BASIC_AUTH_PASSWORD")
    if user and password:
        return (user, password)
    return None


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    return manifest_builder.build_manifest(
        prefix=args.prefix,
        stress_max_parallel=args.stress_max_parallel,
        market_attempts=args.market_attempts,
    )


def manifest_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for section, items in (manifest.get("sections") or {}).items():
        for item in items:
            record = dict(item)
            record.setdefault("section", section)
            records.append(record)
    return records


def build_manifest_summary_md(manifest: dict[str, Any]) -> str:
    summary = manifest.get("summary") or {}
    lines = [
        "# Staging Two-Server Full Matrix Manifest",
        "",
        f"- Schema: `{manifest.get('schema_version')}`",
        f"- Prefix: `{manifest.get('prefix')}`",
        f"- Environment: `{manifest.get('environment')}`",
        f"- Total scenarios: `{summary.get('total_manifest_scenarios')}`",
        f"- Controlled no-pressure: `{summary.get('controlled_no_pressure')}`",
        "",
        "## Section Counts",
        "",
    ]
    for key in sorted(k for k in summary if k.endswith("_scenarios")):
        lines.append(f"- `{key}`: `{summary[key]}`")
    lines.extend(["", "## Branch-Change Area Counts", ""])
    for area, count in sorted((summary.get("branch_change_area_counts") or {}).items()):
        lines.append(f"- `{area}`: `{count}`")
    lines.append("")
    return "\n".join(lines)


def write_scenario_plan_jsonl(path: Path, manifest: dict[str, Any]) -> None:
    if path.exists():
        path.unlink()
    for index, record in enumerate(manifest_records(manifest), start=1):
        append_jsonl(
            path,
            {
                "event": "scenario_planned",
                "index": index,
                "total": (manifest.get("summary") or {}).get("total_manifest_scenarios"),
                "manifest_id": record.get("manifest_id"),
                "section": record.get("section"),
                "kind": record.get("kind"),
                "branch_change_area": record.get("branch_change_area"),
                "branch_change_areas": record.get("branch_change_areas") or [],
                "coverage_tags": record.get("coverage_tags") or [],
                "quadrant": record.get("quadrant"),
                "actor_pair_id": record.get("actor_pair_id"),
                "offer_surface": record.get("offer_surface"),
                "request_surface": record.get("request_surface"),
                "outage_id": record.get("outage_id"),
                "execution_status": "planned_not_executed",
            },
        )


def build_readme(args: argparse.Namespace, manifest: dict[str, Any], status: str) -> str:
    return "\n".join(
        [
            "# Full Matrix Logs",
            "",
            f"- Run id: `{args.run_id}`",
            f"- Status: `{status}`",
            f"- Branch: `{run_git_value(['branch', '--show-current'])}`",
            f"- Commit: `{run_git_value(['rev-parse', 'HEAD'])}`",
            f"- Iran staging URL: `{args.iran_base_url}`",
            f"- Foreign staging URL: `{args.foreign_base_url}`",
            f"- Prefix: `{manifest.get('prefix')}`",
            f"- Total scenarios: `{(manifest.get('summary') or {}).get('total_manifest_scenarios')}`",
            "",
            "This directory is sanitized and intended for external-agent review.",
            "Mutation evidence is valid only after preflight passes and scenario execution is explicitly recorded.",
            "",
        ]
    )


def build_run_metadata(args: argparse.Namespace, manifest: dict[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "schema_version": "staging_two_server_full_matrix_run_metadata_v1",
        "generated_at": utc_now_iso(),
        "run_id": args.run_id,
        "status": status,
        "branch": run_git_value(["branch", "--show-current"]),
        "expected_branch": args.expected_branch,
        "commit": run_git_value(["rev-parse", "HEAD"]),
        "expected_release_sha": expected_release_sha(args),
        "iran_base_url": args.iran_base_url,
        "foreign_base_url": args.foreign_base_url,
        "artifact_dir": str(args.artifact_dir),
        "agent_log_dirs": [
            str(CLAUDE_LOG_ROOT / args.run_id),
            str(CHATGPT_LOG_ROOT / args.run_id),
        ],
        "manifest_total": (manifest.get("summary") or {}).get("total_manifest_scenarios"),
        "driver_scenario_count": len(DRIVER_SCENARIOS),
        "limitations": [
            "driver suite evidence is not full release-gate evidence until every mandatory manifest group is executed or explicitly mapped",
            "real Telegram/channel side effects require separate smoke evidence when --patch-external-side-effects is used",
        ],
    }


def publish_agent_logs(artifact_dir: Path, args: argparse.Namespace, manifest: dict[str, Any], *, status: str) -> None:
    targets = [CLAUDE_LOG_ROOT / args.run_id, CHATGPT_LOG_ROOT / args.run_id]
    write_text(artifact_dir / "README.md", build_readme(args, manifest, status))
    write_json(artifact_dir / "run-metadata.json", build_run_metadata(args, manifest, status=status))
    redaction_scan = detect_forbidden_secret_like_values(artifact_dir)
    write_json(
        artifact_dir / "redaction-report.json",
        {
            "schema_version": "full_matrix_redaction_report_v1",
            "generated_at": utc_now_iso(),
            "patterns_applied": [
                "secret-like key/value strings",
                "Telegram bot token shape",
                "Iran mobile number shape",
            ],
            "forbidden_secret_like_value_detected": bool(redaction_scan["detected"]),
            "findings": redaction_scan["findings"],
        },
    )
    for target in targets:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(artifact_dir, target)


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_manifest(args)
    validation_errors = manifest_builder.validate_manifest(manifest)
    artifact_dir = args.artifact_dir
    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_json(artifact_dir / "manifest.json", manifest)
    write_text(artifact_dir / "manifest-summary.md", build_manifest_summary_md(manifest))
    write_scenario_plan_jsonl(artifact_dir / "scenario-results.jsonl", manifest)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "mode": args.mode,
        "run_id": args.run_id,
        "status": "plan_ready" if not validation_errors else "manifest_invalid",
        "generated_at": utc_now_iso(),
        "branch": run_git_value(["branch", "--show-current"]),
        "commit": run_git_value(["rev-parse", "HEAD"]),
        "artifact_dir": str(artifact_dir),
        "agent_log_dirs": [
            str(CLAUDE_LOG_ROOT / args.run_id),
            str(CHATGPT_LOG_ROOT / args.run_id),
        ],
        "validation_errors": validation_errors,
        "scenario_total": (manifest.get("summary") or {}).get("total_manifest_scenarios"),
        "branch_change_area_counts": (manifest.get("summary") or {}).get("branch_change_area_counts"),
        "iran_base_url": args.iran_base_url,
        "foreign_base_url": args.foreign_base_url,
        "execution": {
            "status": "not_started",
            "requires_preflight_passed": True,
            "requires_confirmation_env": EXECUTION_CONFIRM_ENV,
            "requires_confirmation_value": EXECUTION_CONFIRM_VALUE,
        },
    }
    write_json(artifact_dir / "summary.json", summary)
    write_text(artifact_dir / "summary.md", build_summary_md(summary))
    publish_agent_logs(artifact_dir, args, manifest, status=summary["status"])
    return {"manifest": manifest, "summary": summary}


def build_summary_md(summary: dict[str, Any]) -> str:
    execution = summary.get("execution") if isinstance(summary.get("execution"), dict) else {}
    driver_suite = execution.get("driver_suite") if isinstance(execution.get("driver_suite"), dict) else {}
    lines = [
        "# Full Matrix Summary",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Mode: `{summary.get('mode')}`",
        f"- Run id: `{summary.get('run_id')}`",
        f"- Branch: `{summary.get('branch')}`",
        f"- Commit: `{summary.get('commit')}`",
        f"- Manifest scenario space: `{summary.get('scenario_total')}`",
    ]
    if driver_suite:
        lines.append(f"- Driver scenarios executed: `{driver_suite.get('scenario_total')}`")
        lines.append(f"- Driver result counts: `{driver_suite.get('result_counts')}`")
    if execution.get("manifest_total") is not None:
        lines.append(f"- Execution manifest total: `{execution.get('manifest_total')}`")
    if execution.get("note"):
        lines.append(f"- Execution scope note: {execution.get('note')}")
    lines.extend(["", "## Branch-Change Areas", ""])
    for area, count in sorted((summary.get("branch_change_area_counts") or {}).items()):
        lines.append(f"- `{area}`: `{count}`")
    if summary.get("validation_errors"):
        lines.extend(["", "## Validation Errors", ""])
        for error in summary["validation_errors"]:
            lines.append(f"- {error}")
    lines.append("")
    return "\n".join(lines)


def preflight_checks(args: argparse.Namespace, manifest: dict[str, Any]) -> list[CheckResult]:
    auth = basic_auth_from_args(args)
    checks: list[CheckResult] = []
    current_branch = run_git_value(["branch", "--show-current"])
    current_commit = run_git_value(["rev-parse", "HEAD"])
    expected_branch = getattr(args, "expected_branch", None) or DEFAULT_EXPECTED_BRANCH
    expected_release = expected_release_sha(args)
    git_status = run_git_value(["status", "--short", "--branch"]) or ""
    checks.append(
        CheckResult(
            "git_branch",
            "passed" if current_branch == expected_branch else "failed",
            "branch matches expected candidate" if current_branch == expected_branch else "wrong branch",
            payload={"current_branch": current_branch, "expected_branch": expected_branch, "commit": current_commit},
        )
    )
    checks.append(
        CheckResult(
            "git_status",
            "passed",
            "git status captured; staged full-matrix docs are allowed",
            payload={"status": git_status},
        )
    )
    checks.append(
        CheckResult(
            "release_commit_binding",
            "passed" if expected_release and expected_release == current_commit else "failed",
            (
                "expected staging release is bound to the current immutable commit"
                if expected_release and expected_release == current_commit
                else "expected staging release SHA is missing or differs from current commit"
            ),
            payload={"expected_release_sha": expected_release, "current_commit": current_commit},
        )
    )
    checks.extend(
        [
            validate_staging_url("iran_url_identity", args.iran_base_url, expected_host=host_of(DEFAULT_IRAN_BASE_URL)),
            validate_staging_url("foreign_url_identity", args.foreign_base_url, expected_host=host_of(DEFAULT_FOREIGN_BASE_URL)),
            check_tls("iran_tls", args.iran_base_url),
            check_tls("foreign_tls", args.foreign_base_url),
            check_http_json("iran_public_config", args.iran_base_url.rstrip("/") + "/api/config", basic_auth=auth),
            check_foreign_public_surface_guard("foreign_public_surface_guard", args.foreign_base_url, basic_auth=auth),
            check_internal_ingress_without_basic_auth(
                "iran_sync_internal_ingress_without_basic_auth",
                args.iran_base_url.rstrip("/") + "/api/sync/receive",
            ),
            check_internal_ingress_without_basic_auth(
                "iran_trade_internal_ingress_without_basic_auth",
                args.iran_base_url.rstrip("/") + "/api/trades/internal/execute",
            ),
            check_internal_ingress_without_basic_auth(
                "iran_offer_expiry_internal_ingress_without_basic_auth",
                args.iran_base_url.rstrip("/") + "/api/offers/internal/expire",
            ),
            check_internal_ingress_without_basic_auth(
                "iran_session_internal_ingress_without_basic_auth",
                args.iran_base_url.rstrip("/") + "/api/sessions/internal/authority-check",
            ),
            check_internal_ingress_without_basic_auth(
                "iran_registration_internal_ingress_without_basic_auth",
                args.iran_base_url.rstrip("/") + "/api/auth/internal/telegram-registration/reconcile",
            ),
            check_internal_ingress_without_basic_auth(
                "iran_account_link_internal_ingress_without_basic_auth",
                args.iran_base_url.rstrip("/") + "/api/auth/internal/telegram-link/complete",
            ),
            check_internal_ingress_without_basic_auth(
                "iran_invitation_internal_ingress_without_basic_auth",
                args.iran_base_url.rstrip("/") + "/api/invitations/internal/create",
            ),
            check_internal_ingress_without_basic_auth(
                "iran_customer_relation_internal_ingress_without_basic_auth",
                args.iran_base_url.rstrip("/") + "/api/customers/internal/owner-relations",
            ),
            check_internal_ingress_without_basic_auth(
                "foreign_sync_internal_ingress_without_basic_auth",
                args.foreign_base_url.rstrip("/") + "/foreign-sync/api/sync/receive",
            ),
            check_internal_ingress_without_basic_auth(
                "foreign_trade_internal_ingress_without_basic_auth",
                args.foreign_base_url.rstrip("/") + "/foreign-sync/api/trades/internal/execute",
            ),
            check_internal_ingress_without_basic_auth(
                "foreign_offer_expiry_internal_ingress_without_basic_auth",
                args.foreign_base_url.rstrip("/") + "/foreign-sync/api/offers/internal/expire",
            ),
            check_internal_ingress_without_basic_auth(
                "foreign_session_internal_ingress_without_basic_auth",
                args.foreign_base_url.rstrip("/") + "/foreign-sync/api/sessions/internal/authority-check",
            ),
            check_internal_ingress_without_basic_auth(
                "foreign_telegram_otp_internal_ingress_without_basic_auth",
                args.foreign_base_url.rstrip("/") + "/foreign-sync/api/auth/internal/telegram-otp/deliver",
            ),
            check_container_runtime_identity(
                "iran_runtime_identity",
                server="iran",
                expected_server_mode="iran",
                expected_release=expected_release,
                args=args,
            ),
            check_container_runtime_identity(
                "foreign_runtime_identity",
                server="foreign",
                expected_server_mode="foreign",
                expected_release=expected_release,
                args=args,
            ),
            check_storage_identity_separation(
                "staging_storage_identity_separation",
                args=args,
            ),
        ]
    )
    if args.observability_api_key:
        checks.extend(
            [
                check_observability_json(
                    "iran_sync_health",
                    args.iran_base_url.rstrip("/") + "/api/sync/health",
                    args.observability_api_key,
                    expected_server_mode="iran",
                    basic_auth=auth,
                ),
                check_observability_json(
                    "foreign_sync_health",
                    args.foreign_base_url.rstrip("/") + "/foreign-sync/api/sync/health",
                    args.observability_api_key,
                    expected_server_mode="foreign",
                    basic_auth=auth,
                ),
            ]
        )
    else:
        checks.append(
            CheckResult(
                "sync_health_observability_key",
                "failed",
                "observability key is required to verify /api/sync/health on both staging peers",
            )
        )

    manifest_errors = manifest_builder.validate_manifest(manifest)
    checks.append(
        CheckResult(
            "manifest_validation",
            "passed" if not manifest_errors else "failed",
            "manifest validates" if not manifest_errors else "manifest validation failed",
            payload={"errors": manifest_errors},
        )
    )
    return checks


def check_observability_json(
    name: str,
    url: str,
    observability_key: str,
    *,
    expected_server_mode: str,
    basic_auth: tuple[str, str] | None,
) -> CheckResult:
    started = time.perf_counter()
    status_code, payload, body = fetch_observability_json(
        url,
        observability_key,
        basic_auth=basic_auth,
        timeout_seconds=10,
    )
    if payload is None:
        return CheckResult(
            name,
            "failed",
            f"sync health HTTP/JSON check failed with status={status_code}",
            time.perf_counter() - started,
            {"status_code": status_code, "body": sanitize_text(body[:1000])},
        )
    server_mode = str(payload.get("server_mode") or "")
    if status_code != 200 or server_mode != expected_server_mode:
        return CheckResult(
            name,
            "failed",
            "sync health server_mode mismatch or bad status",
            time.perf_counter() - started,
            {"status_code": status_code, "server_mode": server_mode, "expected_server_mode": expected_server_mode, "payload": payload},
        )
    return CheckResult(name, "passed", "sync health verified", time.perf_counter() - started, {"status_code": status_code, "payload": payload})


def int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def sync_health_gate_failures(
    peer: str,
    peer_payload: dict[str, Any],
    *,
    expected_server_mode: str,
    require_fresh_parity: bool,
) -> list[str]:
    failures: list[str] = []
    server_mode = str(peer_payload.get("server_mode") or "")
    if server_mode != expected_server_mode:
        failures.append(f"{peer} server_mode={server_mode or 'missing'} expected={expected_server_mode}")
    unsynced = int_or_zero(peer_payload.get("unsynced_change_log_count"))
    if unsynced != 0:
        failures.append(f"{peer} unsynced_change_log_count={unsynced}")
    queues = peer_payload.get("redis_queues")
    if not isinstance(queues, dict):
        queues = peer_payload.get("redis_counts")
    if not isinstance(queues, dict):
        queues = {}
    for queue_name in ("sync:outbound", "sync:retry"):
        queue_count = int_or_zero(queues.get(queue_name))
        if queue_count != 0:
            failures.append(f"{peer} {queue_name}={queue_count}")
    if require_fresh_parity:
        parity_status = peer_payload.get("parity_status")
        if not isinstance(parity_status, dict):
            failures.append(f"{peer} parity_status=missing")
        else:
            if parity_status.get("fresh") is not True:
                failures.append(f"{peer} parity_status.fresh=false")
            comparison_status = (
                parity_status.get("comparison_status")
                or parity_status.get("status")
                or ((parity_status.get("latest_comparison") or {}).get("status") if isinstance(parity_status.get("latest_comparison"), dict) else None)
            )
            if comparison_status not in {"ok", "clean", "non_business_difference"}:
                failures.append(f"{peer} parity_status.comparison_status={comparison_status or 'missing'}")
            comparison_payload = parity_status
            if isinstance(parity_status.get("latest_comparison"), dict):
                comparison_payload = parity_status["latest_comparison"]
            for field_name in (
                "business_drift_count",
                "critical_drift_count",
                "duplicate_identity_count",
                "incomplete_count",
                "truncated_table_count",
            ):
                field_value = int_or_zero(comparison_payload.get(field_name))
                if field_value != 0:
                    failures.append(f"{peer} parity_status.{field_name}={field_value}")
    return failures


def capture_sync_health(args: argparse.Namespace, *, label: str, require_fresh_parity: bool = False) -> dict[str, Any]:
    auth = basic_auth_from_args(args)
    payload: dict[str, Any] = {
        "schema_version": "staging_two_server_sync_health_pair_v1",
        "label": label,
        "captured_at": utc_now_iso(),
        "status": "failed",
        "require_fresh_parity": require_fresh_parity,
        "peers": {},
    }
    if not args.observability_api_key:
        payload["error"] = "missing_observability_api_key"
        write_json(args.artifact_dir / f"sync-health-{label}.json", payload)
        return payload
    peer_urls = {
        "iran": args.iran_base_url.rstrip("/") + "/api/sync/health",
        "foreign": args.foreign_base_url.rstrip("/") + "/foreign-sync/api/sync/health",
    }
    failures = []
    gate_failures: dict[str, list[str]] = {}
    for peer, url in peer_urls.items():
        status_code, peer_payload, raw = fetch_observability_json(
            url,
            args.observability_api_key,
            basic_auth=auth,
            timeout_seconds=20,
        )
        payload["peers"][peer] = {
            "status_code": status_code,
            "payload": peer_payload or {},
            "body": "" if peer_payload is not None else sanitize_text(raw[:1000]),
        }
        if status_code != 200 or not isinstance(peer_payload, dict):
            failures.append(peer)
            gate_failures[peer] = [f"{peer} sync health HTTP/JSON check failed with status={status_code}"]
            continue
        peer_gate_failures = sync_health_gate_failures(
            peer,
            peer_payload,
            expected_server_mode=peer,
            require_fresh_parity=require_fresh_parity,
        )
        if peer_gate_failures:
            failures.append(peer)
            gate_failures[peer] = peer_gate_failures
        payload["peers"][peer]["gate_failures"] = peer_gate_failures
    payload["status"] = "passed" if not failures else "failed"
    payload["failed_peers"] = sorted(set(failures))
    payload["gate_failures"] = gate_failures
    write_json(args.artifact_dir / f"sync-health-{label}.json", payload)
    return payload


def capture_parity(args: argparse.Namespace, *, label: str) -> dict[str, Any]:
    from core.sync_parity import compare_parity_snapshots
    from core.sync_parity_observability import infer_parity_comparison_mode, summarize_parity_comparison

    auth = basic_auth_from_args(args)
    mode = args.parity_mode
    payload: dict[str, Any] = {
        "schema_version": "staging_two_server_parity_pair_v1",
        "label": label,
        "captured_at": utc_now_iso(),
        "mode": mode,
        "status": "failed",
        "snapshots": {},
    }
    if not args.observability_api_key:
        payload["error"] = "missing_observability_api_key"
        write_json(args.artifact_dir / f"parity-{label}.json", payload)
        return payload
    peer_urls = {
        "iran": args.iran_base_url.rstrip("/") + f"/api/sync/parity/snapshot?mode={mode}&max_rows_per_table={args.parity_max_rows_per_table}",
        "foreign": args.foreign_base_url.rstrip("/") + f"/foreign-sync/api/sync/parity/snapshot?mode={mode}&max_rows_per_table={args.parity_max_rows_per_table}",
    }
    expected_server_modes = {"iran": "iran", "foreign": "foreign"}
    failures = []
    for peer, url in peer_urls.items():
        status_code, snapshot, raw = fetch_observability_json(
            url,
            args.observability_api_key,
            basic_auth=auth,
            timeout_seconds=45,
        )
        payload["snapshots"][peer] = {
            "status_code": status_code,
            "snapshot": snapshot or {},
            "body": "" if snapshot is not None else sanitize_text(raw[:1000]),
            "expected_server_mode": expected_server_modes[peer],
        }
        if status_code != 200 or not isinstance(snapshot, dict):
            failures.append(peer)
            continue
        observed_server_mode = str(snapshot.get("server_mode") or "").strip().lower()
        if observed_server_mode != expected_server_modes[peer]:
            payload["snapshots"][peer]["server_mode_mismatch"] = {
                "expected": expected_server_modes[peer],
                "observed": observed_server_mode,
            }
            failures.append(peer)
    iran_snapshot = (payload["snapshots"].get("iran") or {}).get("snapshot") or {}
    foreign_snapshot = (payload["snapshots"].get("foreign") or {}).get("snapshot") or {}
    if not failures:
        comparison = compare_parity_snapshots(iran_snapshot, foreign_snapshot)
        comparison["mode"] = infer_parity_comparison_mode(iran_snapshot, foreign_snapshot)
        comparison["compared_at"] = utc_now_iso()
        comparison["artifact_metadata"] = {
            "local_server_mode": "iran",
            "peer_server_mode": "foreign",
            "local_release_sha": iran_snapshot.get("release_sha") or expected_release_sha(args),
            "peer_release_sha": foreign_snapshot.get("release_sha") or expected_release_sha(args),
            "snapshot_mode": comparison["mode"],
            "local_table_count": len((iran_snapshot.get("tables") or {})),
            "peer_table_count": len((foreign_snapshot.get("tables") or {})),
            "local_snapshot_at": iran_snapshot.get("snapshot_at") or payload["captured_at"],
            "peer_snapshot_at": foreign_snapshot.get("snapshot_at") or payload["captured_at"],
            "artifact_reference": str(args.artifact_dir / f"parity-{label}.json"),
        }
        comparison["artifact_metadata"]["comparison_artifact_hash"] = hashlib.sha256(
            json.dumps(comparison, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        comparison["summary"] = summarize_parity_comparison(
            comparison,
            mode=comparison["mode"],
            observed_at=comparison["compared_at"],
        )
        payload["comparison"] = comparison
        payload["status"] = "passed" if comparison.get("status") in {"ok", "non_business_difference"} else "failed"
        record_urls = {
            "iran": args.iran_base_url.rstrip("/") + "/api/sync/parity/status",
            "foreign": args.foreign_base_url.rstrip("/") + "/foreign-sync/api/sync/parity/status",
        }
        for peer, record_url in record_urls.items():
            record_status, record_payload, record_raw = fetch_observability_json(
                record_url,
                args.observability_api_key,
                basic_auth=auth,
                method="POST",
                payload=comparison,
                timeout_seconds=30,
            )
            payload.setdefault("status_recording", {})[peer] = {
                "status_code": record_status,
                "payload": record_payload or {},
                "body": "" if record_payload is not None else sanitize_text(record_raw[:1000]),
            }
            if record_status != 200:
                payload["status"] = "failed"
    else:
        payload["failed_peers"] = failures
    write_json(args.artifact_dir / f"parity-{label}.json", payload)
    return payload


def run_preflight(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    plan = build_plan(args)
    manifest = plan["manifest"]
    checks = preflight_checks(args, manifest)
    if args.observability_api_key:
        parity_before = capture_parity(args, label="before")
        checks.append(
            CheckResult(
                "parity_before_artifact",
                "passed" if parity_before.get("status") == "passed" else "failed",
                "top-level parity-before artifact captured"
                if parity_before.get("status") == "passed"
                else "top-level parity-before artifact failed",
                payload={"artifact": str(args.artifact_dir / "parity-before.json")},
            )
        )
        sync_health_before = capture_sync_health(args, label="before", require_fresh_parity=True)
        checks.append(
            CheckResult(
                "sync_health_before_artifact",
                "passed" if sync_health_before.get("status") == "passed" else "failed",
                "top-level sync-health-before artifact captured with clean backlog and fresh parity"
                if sync_health_before.get("status") == "passed"
                else "top-level sync-health-before artifact failed clean backlog/fresh parity gate",
                payload={"artifact": str(args.artifact_dir / "sync-health-before.json")},
            )
        )
    failed = [item for item in checks if item.status != "passed"]
    preflight = {
        "schema_version": "staging_two_server_full_matrix_preflight_v1",
        "generated_at": utc_now_iso(),
        "run_id": args.run_id,
        "status": "passed" if not failed else "failed",
        "checks": [item.asdict() for item in checks],
        "failed_checks": [item.name for item in failed],
    }
    write_json(args.artifact_dir / "preflight.json", preflight)
    write_text(args.artifact_dir / "preflight.md", build_preflight_md(preflight))
    summary = dict(plan["summary"])
    summary["status"] = "preflight_passed" if not failed else "preflight_failed"
    summary["preflight_failed_checks"] = preflight["failed_checks"]
    write_json(args.artifact_dir / "summary.json", summary)
    write_text(args.artifact_dir / "summary.md", build_summary_md(summary))
    publish_agent_logs(args.artifact_dir, args, manifest, status=summary["status"])
    return {"manifest": manifest, "summary": summary, "preflight": preflight}, 0 if not failed else 1


def build_preflight_md(preflight: dict[str, Any]) -> str:
    lines = [
        "# Full Matrix Preflight",
        "",
        f"- Status: `{preflight.get('status')}`",
        f"- Run id: `{preflight.get('run_id')}`",
        "",
        "## Checks",
        "",
    ]
    for check in preflight.get("checks") or []:
        lines.append(f"- `{check.get('name')}`: `{check.get('status')}` - {check.get('detail')}")
    lines.append("")
    return "\n".join(lines)


def scenario_prefix(run_prefix: str, scenario_id: str) -> str:
    return f"{run_prefix}{command_slug(scenario_id)}_"


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {"status": "invalid_json", "path": str(path)}
    return payload if isinstance(payload, dict) else {"status": "invalid_json", "path": str(path)}


def run_cleanup_on_both_sides(
    *,
    args: argparse.Namespace,
    prefix: str,
    local_dir: Path,
    remote_dir: str,
    log_dir: Path,
    dry_run: bool = False,
    label: str = "cleanup",
) -> list[CommandResult]:
    artifact_name = f"{command_slug(label)}.json"
    worker_args = ["cleanup", "--prefix", prefix, "--artifact", f"/artifacts/{artifact_name}"]
    if dry_run:
        worker_args.append("--dry-run")
    results = [
        run_remote_worker(
            f"{label}_iran",
            args=args,
            service="load_webapp_iran",
            remote_artifact_dir=remote_dir,
            worker_args=worker_args,
            log_dir=log_dir,
            timeout_seconds=240,
        ),
        run_local_worker(
            f"{label}_foreign",
            service="load_telegram_foreign",
            artifact_dir=local_dir,
            worker_args=worker_args,
            log_dir=log_dir,
            timeout_seconds=240,
        ),
    ]
    for result in results:
        require_command_success(result)
    return results


def cleanup_planned_total(payload: dict[str, Any]) -> int:
    planned_counts = payload.get("planned_counts") if isinstance(payload, dict) else {}
    planned_counts = planned_counts if isinstance(planned_counts, dict) else {}
    return sum(int(value or 0) for value in planned_counts.values()) + int(payload.get("deleted_redis_keys") or 0)


def assert_cleanup_dry_run_zero(results: list[CommandResult], *, label: str) -> dict[str, Any]:
    details = []
    failures = []
    for result in results:
        payload = result.json_payload or {}
        planned_total = cleanup_planned_total(payload)
        details.append(
            {
                "name": result.name,
                "planned_total": planned_total,
                "planned_counts": payload.get("planned_counts") or {},
                "planned_redis_keys": payload.get("deleted_redis_keys") or 0,
            }
        )
        if planned_total != 0:
            failures.append(f"{result.name} planned_total={planned_total}")
    report = {
        "schema_version": "staging_two_server_cleanup_zero_proof_v1",
        "label": label,
        "status": "passed" if not failures else "failed",
        "details": details,
        "failures": failures,
    }
    if failures:
        raise RuntimeError(f"{label} cleanup dry-run is not zero: {'; '.join(failures)}")
    return report


def run_sync_catchup(
    *,
    args: argparse.Namespace,
    prefix: str,
    local_dir: Path,
    remote_dir: str,
    log_dir: Path,
) -> list[CommandResult]:
    results = [
        run_remote_worker(
            "sync_catchup_iran_to_foreign",
            args=args,
            service="load_webapp_iran",
            remote_artifact_dir=remote_dir,
            worker_args=[
                "sync-prefix-catchup",
                "--prefix",
                prefix,
                "--include-synced",
                "--output",
                "/artifacts/sync-catchup-iran.json",
            ],
            log_dir=log_dir,
            timeout_seconds=300,
        ),
        run_local_worker(
            "sync_catchup_foreign_to_iran",
            service="load_telegram_foreign",
            artifact_dir=local_dir,
            worker_args=[
                "sync-prefix-catchup",
                "--prefix",
                prefix,
                "--include-synced",
                "--output",
                "/artifacts/sync-catchup-foreign.json",
            ],
            log_dir=log_dir,
            timeout_seconds=300,
        ),
    ]
    for result in results:
        require_command_success(result)
    return results


def run_observability_snapshots(
    *,
    args: argparse.Namespace,
    local_dir: Path,
    remote_dir: str,
    log_dir: Path,
    label: str,
) -> list[CommandResult]:
    results = [
        run_remote_worker(
            f"observability_iran_{label}",
            args=args,
            service="load_webapp_iran",
            remote_artifact_dir=remote_dir,
            worker_args=["observability-snapshot", "--output", f"/artifacts/observability-iran-{label}.json"],
            log_dir=log_dir,
            timeout_seconds=180,
        ),
        run_local_worker(
            f"observability_foreign_{label}",
            service="load_telegram_foreign",
            artifact_dir=local_dir,
            worker_args=["observability-snapshot", "--output", f"/artifacts/observability-foreign-{label}.json"],
            log_dir=log_dir,
            timeout_seconds=180,
        ),
    ]
    for result in results:
        require_command_success(result)
    return results


def observability_unsynced_count(payload: dict[str, Any]) -> int:
    sync_payload = payload.get("sync") if isinstance(payload, dict) else {}
    worker_payload = payload.get("worker_backlog") if isinstance(payload, dict) else {}
    sync_payload = sync_payload if isinstance(sync_payload, dict) else {}
    worker_payload = worker_payload if isinstance(worker_payload, dict) else {}
    return max(
        int(sync_payload.get("unsynced_change_log_count") or 0),
        int(worker_payload.get("unsynced_change_logs") or 0),
    )


def assert_observability_clean(results: list[CommandResult], *, label: str) -> dict[str, Any]:
    details = []
    failures = []
    for result in results:
        payload = result.json_payload or {}
        unsynced = observability_unsynced_count(payload)
        details.append({"name": result.name, "unsynced_change_log_count": unsynced})
        if unsynced != 0:
            failures.append(f"{result.name} unsynced_change_log_count={unsynced}")
    report = {
        "schema_version": "staging_two_server_observability_clean_gate_v1",
        "label": label,
        "status": "passed" if not failures else "failed",
        "details": details,
        "failures": failures,
    }
    if failures:
        raise RuntimeError(f"{label} observability backlog is not clean: {'; '.join(failures)}")
    return report


def run_sync_catchup_until_clean(
    *,
    args: argparse.Namespace,
    prefix: str,
    local_dir: Path,
    remote_dir: str,
    log_dir: Path,
    label: str,
    max_rounds: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    command_results: list[dict[str, Any]] = []
    last_report: dict[str, Any] = {}
    for round_index in range(1, max(1, int(max_rounds)) + 1):
        command_results.extend(
            result.asdict()
            for result in run_sync_catchup(
                args=args,
                prefix=prefix,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
            )
        )
        snapshots = run_observability_snapshots(
            args=args,
            local_dir=local_dir,
            remote_dir=remote_dir,
            log_dir=log_dir,
            label=f"{label}_round_{round_index}",
        )
        command_results.extend(result.asdict() for result in snapshots)
        try:
            last_report = assert_observability_clean(snapshots, label=f"{label}_round_{round_index}")
            last_report["round"] = round_index
            return command_results, last_report
        except RuntimeError as exc:
            last_report = {
                "schema_version": "staging_two_server_observability_clean_gate_v1",
                "label": f"{label}_round_{round_index}",
                "status": "failed",
                "round": round_index,
                "error": str(exc),
            }
            if round_index < max_rounds:
                time.sleep(2)
                continue
            raise
    return command_results, last_report


def scenario_user_count(scenario: Mapping[str, Any]) -> int:
    return max(8, int(scenario["hot_offer_requests"]) + 2)


def seed_users_worker_args(scenario: Mapping[str, Any], prefix: str) -> list[str]:
    return [
        "seed-dual-role-users",
        "--output",
        "/artifacts/users.seed.json",
        "--prefix",
        prefix,
        "--user-count",
        str(scenario_user_count(scenario)),
        "--skip-initial-cleanup",
    ]


def prepare_worker_args(scenario: dict[str, Any], prefix: str) -> list[str]:
    worker_args = [
        "prepare-dual-role-run",
        "--output-dir",
        "/artifacts",
        "--prefix",
        prefix,
        "--run-id",
        scenario["id"],
        "--offer-origin",
        scenario["offer_origin"],
        "--scenario-name",
        str(scenario.get("scenario_name") or scenario["id"]),
        "--request-surface",
        scenario["request_surface"],
        "--idempotency-mode",
        scenario["idempotency_mode"],
        "--user-count",
        str(scenario_user_count(scenario)),
        "--users-artifact",
        "/artifacts/users.seed.json",
        "--skip-initial-cleanup",
        "--hot-offer-requests",
        str(scenario["hot_offer_requests"]),
        "--telegram-ratio",
        str(scenario["telegram_ratio"]),
        "--target-rps",
        str(scenario["target_rps"]),
        "--hot-offer-quantity",
        str(scenario["hot_offer_quantity"]),
        "--request-amount",
        str(scenario["request_amount"]),
        "--expected-winner-count",
        str(scenario["expected_winner_count"]),
        "--expected-remaining-quantity",
        str(scenario["expected_remaining_quantity"]),
        "--price",
        "100000",
        "--offer-type",
        scenario["offer_type"],
        "--barrier-delay-seconds",
        "45",
    ]
    if scenario.get("race_kind") == "time_expiry":
        worker_args.extend(["--offer-time-limit-buffer-minutes", "60"])
    if not scenario.get("require_terminal_completed", True):
        worker_args.append("--allow-nonterminal-offer")
    if scenario.get("retail"):
        worker_args.append("--retail")
        worker_args.extend(["--lot-sizes", str(scenario.get("lot_sizes") or "")])
    return worker_args


def seed_and_converge_dual_role_users(
    *,
    args: argparse.Namespace,
    scenario: Mapping[str, Any],
    prefix: str,
    local_dir: Path,
    remote_dir: str,
    log_dir: Path,
) -> list[CommandResult]:
    results: list[CommandResult] = []
    seed = run_remote_worker(
        "seed_users_on_iran",
        args=args,
        service="load_webapp_iran",
        remote_artifact_dir=remote_dir,
        worker_args=seed_users_worker_args(scenario, prefix),
        log_dir=log_dir,
        timeout_seconds=300,
    )
    require_command_success(seed)
    results.append(seed)

    sync_users = run_remote_worker(
        "sync_seeded_users_iran_to_foreign",
        args=args,
        service="load_webapp_iran",
        remote_artifact_dir=remote_dir,
        worker_args=[
            "sync-prefix-catchup",
            "--prefix",
            prefix,
            "--include-synced",
            "--table",
            "users",
            "--output",
            "/artifacts/users-sync-catchup.json",
        ],
        log_dir=log_dir,
        timeout_seconds=300,
    )
    require_command_success(sync_users)
    results.append(sync_users)

    copy_users = run_logged_command(
        "copy_seeded_users_iran_to_foreign",
        scp_from_iran(args, f"{remote_dir}/users.seed.json", local_dir / "users.seed.json"),
        log_dir=log_dir,
        timeout_seconds=120,
    )
    require_command_success(copy_users)
    results.append(copy_users)

    verify_users = run_local_worker(
        "verify_seeded_users_on_foreign",
        service="load_telegram_foreign",
        artifact_dir=local_dir,
        worker_args=[
            "verify-dual-role-users",
            "--users-artifact",
            "/artifacts/users.seed.json",
            "--prefix",
            prefix,
            "--user-count",
            str(scenario_user_count(scenario)),
            "--output",
            "/artifacts/users-foreign-verification.json",
        ],
        log_dir=log_dir,
        timeout_seconds=120,
    )
    require_command_success(verify_users)
    results.append(verify_users)
    return results


def copy_prepare_artifacts_to_peer(
    *,
    args: argparse.Namespace,
    scenario: dict[str, Any],
    local_dir: Path,
    remote_dir: str,
    log_dir: Path,
) -> list[CommandResult]:
    offer_origin = scenario["offer_origin"]
    results: list[CommandResult] = []
    if offer_origin == "webapp":
        for name in ("prepare.json", "manifest.json", "telegram_foreign.plan.json", "webapp_iran.plan.json"):
            result = run_logged_command(
                f"copy_iran_to_foreign_{name}",
                scp_from_iran(args, f"{remote_dir}/{name}", local_dir / name),
                log_dir=log_dir,
                timeout_seconds=120,
            )
            require_command_success(result)
            results.append(result)
    else:
        for name in ("prepare.json", "manifest.json", "telegram_foreign.plan.json", "webapp_iran.plan.json"):
            result = run_logged_command(
                f"copy_foreign_to_iran_{name}",
                scp_to_iran(args, local_dir / name, f"{remote_dir}/{name}"),
                log_dir=log_dir,
                timeout_seconds=120,
            )
            require_command_success(result)
            results.append(result)
    return results


def copy_role_result_from_iran(
    *,
    args: argparse.Namespace,
    local_dir: Path,
    remote_dir: str,
    log_dir: Path,
) -> CommandResult:
    result = run_logged_command(
        "copy_webapp_role_result_from_iran",
        scp_from_iran(args, f"{remote_dir}/webapp_iran.result.json", local_dir / "webapp_iran.result.json"),
        log_dir=log_dir,
        timeout_seconds=120,
    )
    require_command_success(result)
    return result


def rebase_role_plans_on_both_sides(
    *,
    args: argparse.Namespace,
    local_dir: Path,
    remote_dir: str,
    log_dir: Path,
) -> list[CommandResult]:
    results = [
        run_remote_worker(
            "rebase_webapp_plan_on_iran",
            args=args,
            service="load_webapp_iran",
            remote_artifact_dir=remote_dir,
            worker_args=[
                "rebase-role-plan",
                "--plan",
                "/artifacts/webapp_iran.plan.json",
                "--output",
                "/artifacts/webapp_iran.plan.json",
            ],
            log_dir=log_dir,
            timeout_seconds=180,
        ),
        run_local_worker(
            "rebase_telegram_plan_on_foreign",
            service="load_telegram_foreign",
            artifact_dir=local_dir,
            worker_args=[
                "rebase-role-plan",
                "--plan",
                "/artifacts/telegram_foreign.plan.json",
                "--output",
                "/artifacts/telegram_foreign.plan.json",
            ],
            log_dir=log_dir,
            timeout_seconds=180,
        ),
    ]
    for result in results:
        require_command_success(result)
    return results


def refresh_role_plan_barriers_on_both_sides(
    *,
    args: argparse.Namespace,
    local_dir: Path,
    remote_dir: str,
    log_dir: Path,
    scenario: Mapping[str, Any] | None = None,
) -> list[CommandResult]:
    barrier_delay = RACE_START_BARRIER_DELAY_SECONDS if scenario and scenario.get("race_kind") else ROLE_START_BARRIER_DELAY_SECONDS
    barrier_epoch = time.time() + barrier_delay
    barrier_arg = f"{barrier_epoch:.6f}"
    results = [
        run_remote_worker(
            "refresh_webapp_plan_barrier_on_iran",
            args=args,
            service="load_webapp_iran",
            remote_artifact_dir=remote_dir,
            worker_args=[
                "set-role-plan-barrier",
                "--plan",
                "/artifacts/webapp_iran.plan.json",
                "--output",
                "/artifacts/webapp_iran.plan.json",
                "--barrier-epoch",
                barrier_arg,
            ],
            log_dir=log_dir,
            timeout_seconds=180,
        ),
        run_local_worker(
            "refresh_telegram_plan_barrier_on_foreign",
            service="load_telegram_foreign",
            artifact_dir=local_dir,
            worker_args=[
                "set-role-plan-barrier",
                "--plan",
                "/artifacts/telegram_foreign.plan.json",
                "--output",
                "/artifacts/telegram_foreign.plan.json",
                "--barrier-epoch",
                barrier_arg,
            ],
            log_dir=log_dir,
            timeout_seconds=180,
        ),
    ]
    if scenario and scenario.get("race_kind"):
        prepare_worker_args = [
            "set-prepare-barrier",
            "--prepare",
            "/artifacts/prepare.json",
            "--output",
            "/artifacts/prepare.json",
            "--barrier-epoch",
            barrier_arg,
        ]
        if scenario["offer_origin"] == "webapp":
            results.append(
                run_remote_worker(
                    "refresh_prepare_barrier_on_iran",
                    args=args,
                    service="load_webapp_iran",
                    remote_artifact_dir=remote_dir,
                    worker_args=prepare_worker_args,
                    log_dir=log_dir,
                    timeout_seconds=180,
                )
            )
        else:
            results.append(
                run_local_worker(
                    "refresh_prepare_barrier_on_foreign",
                    service="load_telegram_foreign",
                    artifact_dir=local_dir,
                    worker_args=prepare_worker_args,
                    log_dir=log_dir,
                    timeout_seconds=180,
                )
            )
    for result in results:
        require_command_success(result)
    return results


def finalize_on_home(
    *,
    args: argparse.Namespace,
    scenario: dict[str, Any],
    local_dir: Path,
    remote_dir: str,
    log_dir: Path,
) -> CommandResult:
    if scenario["offer_origin"] == "webapp":
        copy_result = run_logged_command(
            "copy_merged_result_to_iran",
            scp_to_iran(args, local_dir / "merged.result.json", f"{remote_dir}/merged.result.json"),
            log_dir=log_dir,
            timeout_seconds=120,
        )
        require_command_success(copy_result)
        result = run_remote_worker(
            "finalize_on_iran",
            args=args,
            service="load_webapp_iran",
            remote_artifact_dir=remote_dir,
            worker_args=[
                "finalize-dual-role-run",
                "--prepare",
                "/artifacts/prepare.json",
                "--merged-result",
                "/artifacts/merged.result.json",
                "--output",
                "/artifacts/final.json",
                "--check",
            ] + finalize_race_worker_args(scenario),
            log_dir=log_dir,
            timeout_seconds=240,
        )
        require_command_success(result)
        pull_final = run_logged_command(
            "copy_final_from_iran",
            scp_from_iran(args, f"{remote_dir}/final.json", local_dir / "final.json"),
            log_dir=log_dir,
            timeout_seconds=120,
        )
        require_command_success(pull_final)
        return result
    result = run_local_worker(
        "finalize_on_foreign",
        service="load_telegram_foreign",
        artifact_dir=local_dir,
        worker_args=[
            "finalize-dual-role-run",
            "--prepare",
            "/artifacts/prepare.json",
            "--merged-result",
            "/artifacts/merged.result.json",
            "--output",
            "/artifacts/final.json",
            "--check",
        ] + finalize_race_worker_args(scenario),
        log_dir=log_dir,
        timeout_seconds=240,
    )
    require_command_success(result)
    return result


def finalize_race_worker_args(scenario: Mapping[str, Any]) -> list[str]:
    race_kind = scenario.get("race_kind")
    if race_kind == "manual_expiry":
        return ["--manual-expiry-result", "/artifacts/manual-expiry.result.json"]
    if race_kind == "time_expiry":
        return ["--time-expiry-result", "/artifacts/time-expiry.result.json"]
    return []


def race_role_command(
    *,
    args: argparse.Namespace,
    scenario: Mapping[str, Any],
    local_dir: Path,
    remote_dir: str,
) -> tuple[str, list[str]] | None:
    race_kind = scenario.get("race_kind")
    if race_kind == "manual_expiry":
        name = "run_manual_expiry_race_on_home"
        worker_args = [
            "run-manual-expiry-race",
            "--prepare",
            "/artifacts/prepare.json",
            "--output",
            "/artifacts/manual-expiry.result.json",
        ]
    elif race_kind == "time_expiry":
        name = "run_time_expiry_race_on_home"
        worker_args = [
            "run-time-expiry-race",
            "--prepare",
            "/artifacts/prepare.json",
            "--output",
            "/artifacts/time-expiry.result.json",
        ]
    else:
        return None
    if scenario["offer_origin"] == "webapp":
        return name, remote_load_runner_command(args, "load_webapp_iran", remote_dir, worker_args)
    return name, local_load_runner_command("load_telegram_foreign", local_dir, worker_args, args=args)


def execute_driver_scenario(args: argparse.Namespace, scenario: dict[str, Any], *, suite_dir: Path, remote_root: str) -> dict[str, Any]:
    scenario_id = scenario["id"]
    local_dir = suite_dir / scenario_id
    log_dir = local_dir / "logs"
    remote_dir = f"{remote_root}/{scenario_id}"
    local_dir.mkdir(parents=True, exist_ok=True)
    prefix = scenario_prefix(args.prefix or f"FMX_STAGE_{args.run_id}_", scenario_id)
    command_results: list[dict[str, Any]] = []
    started = time.perf_counter()
    status_value = "failed"
    error_detail = ""
    try:
        command_results.extend(
            result.asdict()
            for result in run_cleanup_on_both_sides(
                args=args,
                prefix=prefix,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
                dry_run=True,
                label="initial_cleanup_dry_run",
            )
        )
        command_results.extend(
            result.asdict()
            for result in run_cleanup_on_both_sides(
                args=args,
                prefix=prefix,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
                label="initial_cleanup_hard_delete",
            )
        )
        initial_zero_results = run_cleanup_on_both_sides(
            args=args,
            prefix=prefix,
            local_dir=local_dir,
            remote_dir=remote_dir,
            log_dir=log_dir,
            dry_run=True,
            label="initial_cleanup_zero_check",
        )
        command_results.extend(result.asdict() for result in initial_zero_results)
        write_json(
            local_dir / "initial-cleanup-zero-proof.json",
            assert_cleanup_dry_run_zero(initial_zero_results, label="initial_cleanup_zero_check"),
        )
        command_results.extend(
            result.asdict()
            for result in run_observability_snapshots(
                args=args,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
                label="before",
            )
        )
        command_results.extend(
            result.asdict()
            for result in seed_and_converge_dual_role_users(
                args=args,
                scenario=scenario,
                prefix=prefix,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
            )
        )
        if scenario["offer_origin"] == "webapp":
            prepare = run_remote_worker(
                "prepare_on_iran",
                args=args,
                service="load_webapp_iran",
                remote_artifact_dir=remote_dir,
                worker_args=prepare_worker_args(scenario, prefix),
                log_dir=log_dir,
                timeout_seconds=300,
            )
        else:
            prepare = run_local_worker(
                "prepare_on_foreign",
                service="load_telegram_foreign",
                artifact_dir=local_dir,
                worker_args=prepare_worker_args(scenario, prefix),
                log_dir=log_dir,
                timeout_seconds=300,
            )
        require_command_success(prepare)
        command_results.append(prepare.asdict())
        command_results.extend(
            result.asdict()
            for result in copy_prepare_artifacts_to_peer(
                args=args,
                scenario=scenario,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
            )
        )
        command_results.extend(
            result.asdict()
            for result in run_sync_catchup(
                args=args,
                prefix=prefix,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
            )
        )
        command_results.extend(
            result.asdict()
            for result in rebase_role_plans_on_both_sides(
                args=args,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
            )
        )
        if scenario["offer_origin"] == "webapp":
            wait_result = run_local_worker(
                "wait_offer_visible_on_foreign",
                service="load_telegram_foreign",
                artifact_dir=local_dir,
                worker_args=[
                    "wait-offer-visible",
                    "--plan",
                    "/artifacts/telegram_foreign.plan.json",
                    "--timeout-seconds",
                    "90",
                    "--poll-seconds",
                    "0.5",
                ],
                log_dir=log_dir,
                timeout_seconds=120,
            )
        else:
            wait_result = run_remote_worker(
                "wait_offer_visible_on_iran",
                args=args,
                service="load_webapp_iran",
                remote_artifact_dir=remote_dir,
                worker_args=[
                    "wait-offer-visible",
                    "--plan",
                    "/artifacts/webapp_iran.plan.json",
                    "--timeout-seconds",
                    "90",
                    "--poll-seconds",
                    "0.5",
                ],
                log_dir=log_dir,
                timeout_seconds=120,
            )
        require_command_success(wait_result)
        command_results.append(wait_result.asdict())
        command_results.extend(
            result.asdict()
            for result in refresh_role_plan_barriers_on_both_sides(
                args=args,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
                scenario=scenario,
            )
        )

        role_commands = [
            (
                "run_webapp_role_on_iran",
                remote_load_runner_command(
                    args,
                    "load_webapp_iran",
                    remote_dir,
                    [
                        "run-role-plan",
                        "--plan",
                        "/artifacts/webapp_iran.plan.json",
                        "--output",
                        "/artifacts/webapp_iran.result.json",
                        "--patch-external-side-effects",
                    ],
                ),
            ),
            (
                "run_telegram_role_on_foreign",
                local_load_runner_command(
                    "load_telegram_foreign",
                    local_dir,
                    [
                        "run-role-plan",
                        "--plan",
                        "/artifacts/telegram_foreign.plan.json",
                        "--output",
                        "/artifacts/telegram_foreign.result.json",
                        "--patch-external-side-effects",
                    ],
                    args=args,
                ),
            ),
        ]
        special_race_command = race_role_command(
            args=args,
            scenario=scenario,
            local_dir=local_dir,
            remote_dir=remote_dir,
        )
        if special_race_command is not None:
            role_commands.append(special_race_command)
        role_results = run_logged_commands_parallel(role_commands, log_dir=log_dir, timeout_seconds=360)
        for result in role_results:
            require_command_success(result)
            command_results.append(result.asdict())
        command_results.append(copy_role_result_from_iran(args=args, local_dir=local_dir, remote_dir=remote_dir, log_dir=log_dir).asdict())
        merge_result = run_local_worker(
            "merge_role_results",
            service="load_telegram_foreign",
            artifact_dir=local_dir,
            worker_args=[
                "merge-role-results",
                "--output",
                "/artifacts/merged.result.json",
                "/artifacts/webapp_iran.result.json",
                "/artifacts/telegram_foreign.result.json",
            ],
            log_dir=log_dir,
            timeout_seconds=180,
        )
        require_command_success(merge_result)
        command_results.append(merge_result.asdict())
        command_results.append(
            finalize_on_home(
                args=args,
                scenario=scenario,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
            ).asdict()
        )
        catchup_results, observability_clean_report = run_sync_catchup_until_clean(
            args=args,
            prefix=prefix,
            local_dir=local_dir,
            remote_dir=remote_dir,
            log_dir=log_dir,
            label="post_trade_catchup",
        )
        command_results.extend(catchup_results)
        write_json(local_dir / "post-trade-observability-clean.json", observability_clean_report)
        final_payload = read_json_file(local_dir / "final.json")
        status_value = "passed" if final_payload.get("status") == "ok" else "failed"
        if status_value != "passed":
            error_detail = "final artifact did not report ok"
    except Exception as exc:  # noqa: BLE001 - scenario evidence must include exact failure class.
        error_detail = f"{type(exc).__name__}: {exc}"
        status_value = "failed"
    finally:
        cleanup_results: list[CommandResult] = []
        try:
            cleanup_results = run_cleanup_on_both_sides(
                args=args,
                prefix=prefix,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
                label="final_cleanup_hard_delete",
            )
            command_results.extend(result.asdict() for result in cleanup_results)
            final_zero_results = run_cleanup_on_both_sides(
                args=args,
                prefix=prefix,
                local_dir=local_dir,
                remote_dir=remote_dir,
                log_dir=log_dir,
                dry_run=True,
                label="final_cleanup_zero_check",
            )
            command_results.extend(result.asdict() for result in final_zero_results)
            write_json(
                local_dir / "final-cleanup-zero-proof.json",
                assert_cleanup_dry_run_zero(final_zero_results, label="final_cleanup_zero_check"),
            )
        except Exception as exc:  # noqa: BLE001
            error_detail = (error_detail + "; " if error_detail else "") + f"cleanup_failed: {type(exc).__name__}: {exc}"
            status_value = "failed"

    scenario_result = {
        "event": "driver_scenario_executed",
        "manifest_id": scenario_id,
        "scenario_id": scenario_id,
        "status": status_value,
        "error": error_detail,
        "prefix": prefix,
        "local_artifact_dir": str(local_dir),
        "remote_artifact_dir": remote_dir,
        "coverage": scenario.get("coverage") or [],
        "scenario": scenario,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "commands": command_results,
        "final": read_json_file(local_dir / "final.json"),
        "merged": read_json_file(local_dir / "merged.result.json"),
    }
    write_json(local_dir / "driver-scenario-result.json", scenario_result)
    return scenario_result


def run_driver_suite(args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    suite_dir = args.artifact_dir / "driver-suite"
    suite_dir.mkdir(parents=True, exist_ok=True)
    remote_root = f"{args.iran_workdir}/tmp/full_matrix_logs/{args.run_id}"
    selected = DRIVER_SCENARIOS
    if args.driver_scenario_id:
        requested = set(args.driver_scenario_id)
        selected = [scenario for scenario in selected if scenario["id"] in requested]
        missing = sorted(requested - {scenario["id"] for scenario in selected})
        if missing:
            raise RuntimeError(f"unknown driver scenario id(s): {', '.join(missing)}")
    selected = selected[: max(1, int(args.driver_scenario_limit or len(selected)))]
    results: list[dict[str, Any]] = []
    for scenario in selected:
        result = execute_driver_scenario(args, scenario, suite_dir=suite_dir, remote_root=remote_root)
        results.append(result)
        append_jsonl(args.artifact_dir / "driver-scenario-results.jsonl", result)
        append_jsonl(
            args.artifact_dir / "scenario-results.jsonl",
            {
                "event": "driver_scenario_executed",
                "manifest_id": scenario["id"],
                "section": "two_server_driver_suite",
                "execution_status": result["status"],
                "coverage": result.get("coverage") or [],
                "artifact_dir": result.get("local_artifact_dir"),
                "remote_artifact_dir": result.get("remote_artifact_dir"),
                "elapsed_seconds": result.get("elapsed_seconds"),
                "error": result.get("error"),
            },
        )
    counts = Counter(str(item.get("status") or "unknown") for item in results)
    failed = [item for item in results if item.get("status") != "passed"]
    coverage_counter: Counter[str] = Counter()
    for item in results:
        for coverage in item.get("coverage") or []:
            coverage_counter[str(coverage)] += 1
    payload = {
        "schema_version": "staging_two_server_driver_suite_v1",
        "status": "passed" if not failed else "failed",
        "run_id": args.run_id,
        "scenario_total": len(results),
        "manifest_total": (manifest.get("summary") or {}).get("total_manifest_scenarios"),
        "coverage_counts": dict(sorted(coverage_counter.items())),
        "result_counts": dict(sorted(counts.items())),
        "failed_scenarios": [
            {
                "scenario_id": item.get("scenario_id"),
                "error": item.get("error"),
                "artifact_dir": item.get("local_artifact_dir"),
            }
            for item in failed
        ],
        "results": results,
    }
    write_json(args.artifact_dir / "driver-suite-summary.json", payload)
    return payload


def run_execute(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    plan, preflight_exit = run_preflight(args)
    summary = dict(plan["summary"])
    if preflight_exit != 0:
        summary["execution"] = {"status": "blocked", "reason": "preflight_failed"}
        write_json(args.artifact_dir / "summary.json", summary)
        publish_agent_logs(args.artifact_dir, args, plan["manifest"], status=summary["status"])
        return {**plan, "summary": summary}, 1
    if os.getenv(EXECUTION_CONFIRM_ENV) != EXECUTION_CONFIRM_VALUE:
        summary["status"] = "execution_blocked_confirmation_missing"
        summary["execution"] = {
            "status": "blocked",
            "reason": "confirmation_missing",
            "required_env": EXECUTION_CONFIRM_ENV,
            "required_value": EXECUTION_CONFIRM_VALUE,
        }
        write_json(args.artifact_dir / "summary.json", summary)
        write_text(args.artifact_dir / "summary.md", build_summary_md(summary))
        publish_agent_logs(args.artifact_dir, args, plan["manifest"], status=summary["status"])
        return {**plan, "summary": summary}, 2
    driver_suite = run_driver_suite(args, plan["manifest"])
    parity_after = capture_parity(args, label="after")
    sync_health_after = capture_sync_health(args, label="after", require_fresh_parity=True)
    post_execution_evidence: dict[str, Any] = {
        "parity_after": parity_after,
        "sync_health_after": sync_health_after,
    }
    post_execution_failures = [
        name
        for name, payload in post_execution_evidence.items()
        if not isinstance(payload, dict) or payload.get("status") != "passed"
    ]
    if post_execution_failures:
        driver_suite["status"] = "failed"
        driver_suite.setdefault("failed_scenarios", []).append(
            {
                "scenario_id": "post_execution_evidence",
                "error": f"post-execution evidence failed: {', '.join(post_execution_failures)}",
                "artifact_dir": str(args.artifact_dir),
            }
        )
    write_json(args.artifact_dir / "driver-suite-summary.json", driver_suite)
    summary["status"] = "execution_driver_suite_passed" if driver_suite["status"] == "passed" else "execution_driver_suite_failed"
    summary["execution"] = {
        "status": driver_suite["status"],
        "driver_suite": {
            "scenario_total": driver_suite["scenario_total"],
            "result_counts": driver_suite["result_counts"],
            "failed_scenarios": driver_suite["failed_scenarios"],
            "summary_path": str(args.artifact_dir / "driver-suite-summary.json"),
        },
        "post_execution_evidence": {
            name: {
                "status": payload.get("status") if isinstance(payload, dict) else "failed",
                "artifact": str(args.artifact_dir / f"{name.replace('_', '-')}.json"),
            }
            for name, payload in post_execution_evidence.items()
        },
        "manifest_total": driver_suite["manifest_total"],
        "note": (
            "This is the first mutating real two-server staging driver suite. "
            "It does not claim every manifest row was individually mutated; full release evidence remains invalid "
            "until the driver suite is expanded or mapped to every mandatory manifest coverage group."
        ),
    }
    write_json(args.artifact_dir / "summary.json", summary)
    write_text(args.artifact_dir / "summary.md", build_summary_md(summary))
    publish_agent_logs(args.artifact_dir, args, plan["manifest"], status=summary["status"])
    return {**plan, "summary": summary, "driver_suite": driver_suite}, 0 if driver_suite["status"] == "passed" else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["plan", "preflight", "execute"], default="plan")
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--iran-base-url", default=os.getenv("STAGING_IRAN_BASE_URL", DEFAULT_IRAN_BASE_URL))
    parser.add_argument("--foreign-base-url", default=os.getenv("STAGING_FOREIGN_BASE_URL", DEFAULT_FOREIGN_BASE_URL))
    parser.add_argument("--basic-auth-user", default=None)
    parser.add_argument("--basic-auth-password", default=None)
    parser.add_argument("--observability-api-key", default=os.getenv("STAGING_OBSERVABILITY_API_KEY"))
    parser.add_argument("--expected-release-sha", default=os.getenv("STAGING_EXPECTED_RELEASE_SHA"))
    parser.add_argument(
        "--expected-branch",
        default=os.getenv(
            "STAGING_TWO_SERVER_FULL_MATRIX_EXPECTED_BRANCH",
            os.getenv("STAGING_EXPECTED_BRANCH", DEFAULT_EXPECTED_BRANCH),
        ),
    )
    parser.add_argument("--iran-ssh-host", default=os.getenv("STAGING_IRAN_SSH_HOST", DEFAULT_IRAN_SSH_HOST))
    parser.add_argument("--iran-ssh-port", default=os.getenv("STAGING_IRAN_SSH_PORT", DEFAULT_IRAN_SSH_PORT))
    parser.add_argument("--iran-workdir", default=os.getenv("STAGING_IRAN_WORKDIR", DEFAULT_IRAN_WORKDIR))
    parser.add_argument("--iran-app-container", default=os.getenv("STAGING_IRAN_APP_CONTAINER", DEFAULT_IRAN_APP_CONTAINER))
    parser.add_argument("--foreign-app-container", default=os.getenv("STAGING_FOREIGN_APP_CONTAINER", DEFAULT_FOREIGN_APP_CONTAINER))
    parser.add_argument("--stress-max-parallel", type=int, default=manifest_builder.DEFAULT_STRESS_MAX_PARALLEL)
    parser.add_argument("--market-attempts", type=int, default=manifest_builder.DEFAULT_MARKET_ATTEMPTS)
    parser.add_argument("--driver-scenario-limit", type=int, default=int(os.getenv("STAGING_FULL_MATRIX_DRIVER_SCENARIO_LIMIT", "0") or 0))
    parser.add_argument("--driver-scenario-id", action="append", choices=[scenario["id"] for scenario in DRIVER_SCENARIOS])
    parser.add_argument("--parity-mode", choices=("quick", "deep"), default=os.getenv("STAGING_FULL_MATRIX_PARITY_MODE", "quick"))
    parser.add_argument("--parity-max-rows-per-table", type=int, default=int(os.getenv("STAGING_FULL_MATRIX_PARITY_MAX_ROWS", "5000") or 5000))
    args = parser.parse_args(argv)
    if args.iran_ssh_host.rsplit("@", 1)[-1] == WA_IR_OBJECT_STORAGE_ONLY_IP:
        parser.error(
            "the replacement WA-IR is Object-Storage-only; the legacy two-server "
            "SCP harness cannot target it"
        )
    if args.driver_scenario_limit <= 0:
        args.driver_scenario_limit = len(DRIVER_SCENARIOS)
    if args.artifact_dir is None:
        args.artifact_dir = DEFAULT_ARTIFACT_ROOT / args.run_id
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "plan":
        payload = build_plan(args)
        print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
        return 0 if payload["summary"]["status"] == "plan_ready" else 1
    if args.mode == "preflight":
        payload, exit_code = run_preflight(args)
        print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
        return exit_code
    payload, exit_code = run_execute(args)
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
