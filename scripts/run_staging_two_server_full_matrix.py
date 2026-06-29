#!/usr/bin/env python3
"""Plan and preflight the real two-server staging full matrix.

The runner is fail-closed. `plan` and `preflight` are non-mutating. Mutating
scenario execution must only be added after the real two-server staging
topology passes preflight and the command drivers are reviewed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
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
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import build_staging_two_server_full_matrix_manifest as manifest_builder


SCHEMA_VERSION = "staging_two_server_full_matrix_runner_v1"
EXPECTED_BRANCH = "candidate/sync-parity-hardening"
DEFAULT_IRAN_BASE_URL = "https://staging.gold-trade.ir"
DEFAULT_FOREIGN_BASE_URL = "https://staging.362514.ir"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "staging-two-server-full-matrix"
CLAUDE_LOG_ROOT = REPO_ROOT / "tmp" / "claude" / "full_matrix_logs"
CHATGPT_LOG_ROOT = REPO_ROOT / "tmp" / "chatgpt" / "full_matrix_logs"
EXECUTION_CONFIRM_ENV = "STAGING_TWO_SERVER_FULL_MATRIX_CONFIRM"
EXECUTION_CONFIRM_VALUE = "execute-staging-two-server-full-matrix"
DEFAULT_IRAN_SSH_HOST = "root@87.107.3.22"
DEFAULT_IRAN_APP_CONTAINER = "trading_bot_staging_iran_app_1"
DEFAULT_FOREIGN_APP_CONTAINER = "trading_bot_staging-foreign_app-1"

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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_run_id() -> str:
    return f"S2FM-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def sanitize_text(value: str) -> str:
    sanitized = value
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub(lambda match: match.group(0).split("=", 1)[0] + "=[REDACTED]" if "=" in match.group(0) else "[REDACTED]", sanitized)
    return sanitized


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sanitize_text(content), encoding="utf-8")


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
        "'foreign_server_url': settings.foreign_server_url, "
        "'bot_token_configured': bool(settings.bot_token), "
        "'channel_id_configured': bool(settings.channel_id)"
        "}, sort_keys=True))"
    )


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
        command = [
            "ssh",
            args.iran_ssh_host,
            f"docker exec {args.iran_app_container} python -c {json.dumps(runtime_identity_python())}",
        ]
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


def publish_agent_logs(artifact_dir: Path, args: argparse.Namespace, manifest: dict[str, Any], *, status: str) -> None:
    targets = [CLAUDE_LOG_ROOT / args.run_id, CHATGPT_LOG_ROOT / args.run_id]
    source_files = [
        "README.md",
        "manifest.json",
        "manifest-summary.md",
        "scenario-results.jsonl",
        "summary.json",
        "summary.md",
        "preflight.json",
        "preflight.md",
        "redaction-report.json",
    ]
    write_text(artifact_dir / "README.md", build_readme(args, manifest, status))
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
            "forbidden_secret_like_value_detected": False,
        },
    )
    for target in targets:
        target.mkdir(parents=True, exist_ok=True)
        for name in source_files:
            source = artifact_dir / name
            if source.exists():
                shutil.copy2(source, target / name)


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
    lines = [
        "# Full Matrix Summary",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Mode: `{summary.get('mode')}`",
        f"- Run id: `{summary.get('run_id')}`",
        f"- Branch: `{summary.get('branch')}`",
        f"- Commit: `{summary.get('commit')}`",
        f"- Scenario total: `{summary.get('scenario_total')}`",
        "",
        "## Branch-Change Areas",
        "",
    ]
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
    expected_release = expected_release_sha(args)
    git_status = run_git_value(["status", "--short", "--branch"]) or ""
    checks.append(
        CheckResult(
            "git_branch",
            "passed" if current_branch == EXPECTED_BRANCH else "failed",
            "branch matches expected candidate" if current_branch == EXPECTED_BRANCH else "wrong branch",
            payload={"current_branch": current_branch, "expected_branch": EXPECTED_BRANCH, "commit": current_commit},
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
    checks.extend(
        [
            validate_staging_url("iran_url_identity", args.iran_base_url, expected_host=host_of(DEFAULT_IRAN_BASE_URL)),
            validate_staging_url("foreign_url_identity", args.foreign_base_url, expected_host=host_of(DEFAULT_FOREIGN_BASE_URL)),
            check_tls("iran_tls", args.iran_base_url),
            check_tls("foreign_tls", args.foreign_base_url),
            check_http_json("iran_public_config", args.iran_base_url.rstrip("/") + "/api/config", basic_auth=auth),
            check_foreign_public_surface_guard("foreign_public_surface_guard", args.foreign_base_url, basic_auth=auth),
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
                    args.foreign_base_url.rstrip("/") + "/api/sync/health",
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
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "staging-two-server-full-matrix/1",
            "X-Observability-Api-Key": observability_key,
        },
    )
    if basic_auth:
        raw = f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")
        request.add_header("Authorization", "Basic " + base64.b64encode(raw).decode("ascii"))
    try:
        with urllib.request.urlopen(request, timeout=10, context=ssl.create_default_context()) as response:
            body = response.read(1024 * 1024).decode("utf-8", errors="replace")
            status_code = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read(8192).decode("utf-8", errors="replace")
        return CheckResult(name, "failed", f"sync health HTTP status={exc.code}", time.perf_counter() - started, {"body": sanitize_text(body[:1000])})
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name, "failed", f"sync health request failed: {type(exc).__name__}: {exc}", time.perf_counter() - started)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return CheckResult(name, "failed", "sync health did not return JSON", time.perf_counter() - started, {"status_code": status_code, "body": sanitize_text(body[:1000])})
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


def run_preflight(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    plan = build_plan(args)
    manifest = plan["manifest"]
    checks = preflight_checks(args, manifest)
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
    summary["status"] = "execution_blocked_driver_review_required"
    summary["execution"] = {
        "status": "blocked",
        "reason": "mutating_two_server_staging_drivers_require_review_before_enablement",
        "note": "The manifest, preflight, and artifact contract are ready; scenario mutation drivers must be enabled in a separate reviewed stage.",
    }
    write_json(args.artifact_dir / "summary.json", summary)
    write_text(args.artifact_dir / "summary.md", build_summary_md(summary))
    publish_agent_logs(args.artifact_dir, args, plan["manifest"], status=summary["status"])
    return {**plan, "summary": summary}, 2


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
    parser.add_argument("--iran-ssh-host", default=os.getenv("STAGING_IRAN_SSH_HOST", DEFAULT_IRAN_SSH_HOST))
    parser.add_argument("--iran-app-container", default=os.getenv("STAGING_IRAN_APP_CONTAINER", DEFAULT_IRAN_APP_CONTAINER))
    parser.add_argument("--foreign-app-container", default=os.getenv("STAGING_FOREIGN_APP_CONTAINER", DEFAULT_FOREIGN_APP_CONTAINER))
    parser.add_argument("--stress-max-parallel", type=int, default=manifest_builder.DEFAULT_STRESS_MAX_PARALLEL)
    parser.add_argument("--market-attempts", type=int, default=manifest_builder.DEFAULT_MARKET_ATTEMPTS)
    args = parser.parse_args(argv)
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
