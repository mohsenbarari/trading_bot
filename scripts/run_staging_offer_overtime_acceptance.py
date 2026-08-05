#!/usr/bin/env python3
"""Plan/preflight/execute the Stage 16 offer-overtime staging acceptance matrix.

`plan` and `preflight` are non-mutating. `execute` remains fail-closed until the
confirm env is set and topology preflight is green. Scenario drivers that mutate
staging data are intentionally not wired yet; execute records a blocked status
when drivers are unavailable so the contract stays honest.

This runner stays free of `core.db` / production-matrix imports so it can load
under staging env files that use sync database URLs.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "staging_offer_overtime_acceptance_v1"
DEFAULT_EXPECTED_BRANCH = "candidate/offer-overtime"
DEFAULT_IRAN_BASE_URL = "https://staging.gold-trade.ir"
DEFAULT_FOREIGN_BASE_URL = "https://staging.362514.ir"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "staging-offer-overtime-acceptance"
EXECUTION_CONFIRM_ENV = "STAGING_OFFER_OVERTIME_ACCEPTANCE_CONFIRM"
EXECUTION_CONFIRM_VALUE = "execute-staging-offer-overtime-acceptance"
EXPECTED_ALEMBIC_HEAD = "e8a4b5c6d7e9"
FORBIDDEN_PRODUCTION_HOSTS = frozenset(
    {
        "gold-trade.ir",
        "www.gold-trade.ir",
        "362514.ir",
        "www.362514.ir",
    }
)

SCENARIOS = [
    {"id": "OT-PREF-WEBAPP-SAVE", "surface": "webapp", "requires": ["iran_webapp", "user_sync"]},
    {"id": "OT-PREF-BOT-SAVE", "surface": "bot", "requires": ["foreign_bot", "iran_internal_preference"]},
    {"id": "OT-PREF-DISABLED-REGRESSION", "surface": "both", "requires": ["iran_webapp", "foreign_bot"]},
    {"id": "OT-OFFER-WEBAPP-ORIGIN", "surface": "webapp", "requires": ["iran_webapp", "lifecycle_projection"]},
    {"id": "OT-OFFER-BOT-ORIGIN", "surface": "bot", "requires": ["foreign_bot", "lifecycle_projection"]},
    {"id": "OT-REQ-IRAN-TO-IRAN", "surface": "webapp", "requires": ["iran_webapp", "overtime_request_api"]},
    {"id": "OT-REQ-FOREIGN-TO-FOREIGN", "surface": "bot", "requires": ["foreign_bot", "overtime_request_api"]},
    {"id": "OT-REQ-CROSS-FORWARD", "surface": "mixed", "requires": ["cross_server_forward", "m18_pending_ack"]},
    {"id": "OT-QUEUE-ORDER", "surface": "both", "requires": ["owner_queue", "promote_next"]},
    {"id": "OT-CANCEL-REQUESTER", "surface": "both", "requires": ["cancel_path"]},
    {"id": "OT-FINAL-TAIL", "surface": "both", "requires": ["final_tail"]},
    {"id": "OT-CHANNEL-MARKER", "surface": "foreign", "requires": ["channel_queue", "overtime_channel_edit"]},
    {"id": "OT-SYNC-RECOVERY", "surface": "both", "requires": ["sync_workers", "reconciliation"]},
    {"id": "OT-TG-RETRY", "surface": "foreign", "requires": ["telegram_queue_retry"]},
    {"id": "OT-UI-RECONNECT", "surface": "webapp", "requires": ["webapp_poll_reconnect"]},
]


@dataclass
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


def default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"OT-ACC-{stamp}"


def host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").strip().lower()


def run_git_value(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sanitize_text(value: str) -> str:
    redacted = value
    for key in ("password", "token", "authorization", "api_key", "secret"):
        if key in redacted.lower():
            redacted = "[redacted]"
            break
    return redacted[:1000]


def validate_staging_url(name: str, url: str, expected_host: str | None = None) -> CheckResult:
    started = time.perf_counter()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return CheckResult(name, "failed", "staging URL must use https", time.perf_counter() - started, {"url": url})
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return CheckResult(name, "failed", "staging URL has no hostname", time.perf_counter() - started, {"url": url})
    if hostname in FORBIDDEN_PRODUCTION_HOSTS:
        return CheckResult(
            name,
            "failed",
            "URL points to a forbidden production host",
            time.perf_counter() - started,
            {"hostname": hostname},
        )
    if expected_host and hostname != expected_host:
        return CheckResult(
            name,
            "failed",
            "URL hostname does not match expected staging host",
            time.perf_counter() - started,
            {"hostname": hostname, "expected_host": expected_host},
        )
    return CheckResult(name, "passed", "URL identity is staging-safe", time.perf_counter() - started, {"hostname": hostname})


def _request(
    url: str,
    *,
    basic_auth: tuple[str, str] | None,
    timeout_seconds: float = 12.0,
    method: str = "GET",
    data: bytes | None = None,
) -> tuple[int, str]:
    headers = {"Accept": "application/json"}
    if basic_auth is not None:
        token = base64.b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            return int(response.status), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), body
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


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
        return CheckResult(
            name,
            "failed",
            f"TLS verification failed: {type(exc).__name__}: {exc}",
            time.perf_counter() - started,
            {"hostname": hostname},
        )
    return CheckResult(
        name,
        "passed",
        "TLS verification passed",
        time.perf_counter() - started,
        {"hostname": hostname, "subject": cert.get("subject", [])},
    )


def check_http_json(name: str, url: str, *, basic_auth: tuple[str, str] | None) -> CheckResult:
    started = time.perf_counter()
    status_code, raw = _request(url, basic_auth=basic_auth)
    elapsed = time.perf_counter() - started
    payload = None
    if status_code == 200:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
    if status_code != 200 or payload is None:
        return CheckResult(
            name,
            "failed",
            f"HTTP JSON check failed with status={status_code}",
            elapsed,
            {"url": url, "status_code": status_code, "body": sanitize_text(raw)},
        )
    return CheckResult(
        name,
        "passed",
        "HTTP JSON check passed",
        elapsed,
        {"url": url, "status_code": status_code},
    )


def check_foreign_public_surface_guard(
    name: str,
    base_url: str,
    *,
    basic_auth: tuple[str, str] | None,
) -> CheckResult:
    started = time.perf_counter()
    status_code, raw = _request(base_url.rstrip("/") + "/", basic_auth=basic_auth, timeout_seconds=12.0)
    elapsed = time.perf_counter() - started
    # Foreign public surface should not serve the Iran WebApp shell.
    if status_code in {401, 403, 404, 502}:
        return CheckResult(
            name,
            "passed",
            f"foreign public surface remains non-webapp (status={status_code})",
            elapsed,
            {"status_code": status_code},
        )
    lowered = raw.lower()
    if "mini_app" in lowered or "vite" in lowered or "market" in lowered and status_code == 200:
        return CheckResult(
            name,
            "failed",
            "foreign host appears to serve a WebApp surface",
            elapsed,
            {"status_code": status_code, "body": sanitize_text(raw)},
        )
    return CheckResult(
        name,
        "passed",
        f"foreign public surface guard accepted status={status_code}",
        elapsed,
        {"status_code": status_code},
    )


def check_internal_ingress_without_basic_auth(name: str, url: str) -> CheckResult:
    started = time.perf_counter()
    status_code, raw = _request(url, basic_auth=None, method="POST", data=b"{}")
    elapsed = time.perf_counter() - started
    if status_code in {401, 403} and "www-authenticate" in raw.lower():
        return CheckResult(
            name,
            "failed",
            "internal ingress appears blocked by Basic Auth",
            elapsed,
            {"status_code": status_code, "body": sanitize_text(raw)},
        )
    if status_code == 401 and "basic" in raw.lower():
        return CheckResult(
            name,
            "failed",
            "internal ingress rejected with Basic Auth challenge",
            elapsed,
            {"status_code": status_code, "body": sanitize_text(raw)},
        )
    # App-level auth failures / method validation prove Nginx reached FastAPI.
    if status_code in {400, 401, 403, 405, 415, 422, 500}:
        return CheckResult(
            name,
            "passed",
            f"internal ingress reached application layer (status={status_code})",
            elapsed,
            {"status_code": status_code},
        )
    if status_code == 0:
        return CheckResult(
            name,
            "failed",
            f"internal ingress unreachable: {sanitize_text(raw)}",
            elapsed,
            {"status_code": status_code},
        )
    return CheckResult(
        name,
        "failed",
        f"unexpected internal ingress status={status_code}",
        elapsed,
        {"status_code": status_code, "body": sanitize_text(raw)},
    )


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": "staging_two_server",
        "feature": "offer_overtime",
        "product_name": "وقت اضافه",
        "mutates_production": False,
        "expected_branch": args.expected_branch,
        "expected_release_sha": args.expected_release_sha or run_git_value(["rev-parse", "HEAD"]),
        "expected_alembic_head": EXPECTED_ALEMBIC_HEAD,
        "iran_base_url": args.iran_base_url,
        "foreign_base_url": args.foreign_base_url,
        "scenarios": SCENARIOS,
        "summary": {
            "total_scenarios": len(SCENARIOS),
            "controlled_no_pressure": True,
        },
    }


def basic_auth_from_args(args: argparse.Namespace) -> tuple[str, str] | None:
    if args.basic_auth_user and args.basic_auth_password:
        return (args.basic_auth_user, args.basic_auth_password)
    user = os.getenv("STAGING_BASIC_AUTH_USER")
    password = os.getenv("STAGING_BASIC_AUTH_PASSWORD")
    if user and password:
        return (user, password)
    return None


def preflight_checks(args: argparse.Namespace) -> list[CheckResult]:
    auth = basic_auth_from_args(args)
    current_branch = run_git_value(["branch", "--show-current"])
    current_commit = run_git_value(["rev-parse", "HEAD"])
    expected_release = args.expected_release_sha or current_commit
    return [
        CheckResult(
            "git_branch",
            "passed" if current_branch == args.expected_branch else "failed",
            "branch matches expected candidate"
            if current_branch == args.expected_branch
            else "wrong branch",
            payload={"current_branch": current_branch, "expected_branch": args.expected_branch},
        ),
        CheckResult(
            "release_commit_binding",
            "passed" if expected_release == current_commit else "failed",
            "expected release matches HEAD"
            if expected_release == current_commit
            else "expected release differs from HEAD",
            payload={"expected_release_sha": expected_release, "current_commit": current_commit},
        ),
        CheckResult(
            "scenario_catalog",
            "passed" if SCENARIOS else "failed",
            f"{len(SCENARIOS)} overtime acceptance scenarios registered",
            payload={"scenario_ids": [item["id"] for item in SCENARIOS]},
        ),
        CheckResult(
            "expected_alembic_head",
            "passed",
            f"acceptance expects alembic head {EXPECTED_ALEMBIC_HEAD}",
            payload={"expected_alembic_head": EXPECTED_ALEMBIC_HEAD},
        ),
        validate_staging_url("iran_url_identity", args.iran_base_url, expected_host=host_of(DEFAULT_IRAN_BASE_URL)),
        validate_staging_url(
            "foreign_url_identity",
            args.foreign_base_url,
            expected_host=host_of(DEFAULT_FOREIGN_BASE_URL),
        ),
        check_tls("iran_tls", args.iran_base_url),
        check_tls("foreign_tls", args.foreign_base_url),
        check_http_json(
            "iran_public_config",
            args.iran_base_url.rstrip("/") + "/api/config",
            basic_auth=auth,
        ),
        check_foreign_public_surface_guard(
            "foreign_public_surface_guard",
            args.foreign_base_url,
            basic_auth=auth,
        ),
        check_internal_ingress_without_basic_auth(
            "iran_sync_internal_ingress_without_basic_auth",
            args.iran_base_url.rstrip("/") + "/api/sync/receive",
        ),
        check_internal_ingress_without_basic_auth(
            "foreign_sync_internal_ingress_without_basic_auth",
            args.foreign_base_url.rstrip("/") + "/foreign-sync/api/sync/receive",
        ),
        check_internal_ingress_without_basic_auth(
            "iran_offer_expiry_internal_ingress_without_basic_auth",
            args.iran_base_url.rstrip("/") + "/api/offers/internal/expire",
        ),
        check_internal_ingress_without_basic_auth(
            "foreign_offer_expiry_internal_ingress_without_basic_auth",
            args.foreign_base_url.rstrip("/") + "/foreign-sync/api/offers/internal/expire",
        ),
    ]


def write_artifact_bundle(
    artifact_dir: Path,
    *,
    mode: str,
    manifest: dict[str, Any],
    checks: list[CheckResult] | None,
    status: str,
    detail: str,
) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "status": status,
        "detail": detail,
        "run_id": artifact_dir.name,
        "scenario_count": len(SCENARIOS),
        "failed_checks": [item.name for item in (checks or []) if item.status != "passed"],
    }
    write_json(artifact_dir / "manifest.json", manifest)
    write_json(artifact_dir / "summary.json", summary)
    if checks is not None:
        write_json(artifact_dir / "preflight.json", {"checks": [item.asdict() for item in checks]})
    (artifact_dir / "README.md").write_text(
        "\n".join(
            [
                f"# Offer overtime acceptance ({artifact_dir.name})",
                "",
                f"- mode: `{mode}`",
                f"- status: `{status}`",
                f"- detail: {detail}",
                f"- scenarios: {len(SCENARIOS)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    zip_path = artifact_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in artifact_dir.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(artifact_dir.parent)))
    summary["evidence_zip"] = str(zip_path)
    write_json(artifact_dir / "summary.json", summary)
    return summary


def run_plan(args: argparse.Namespace) -> dict[str, Any]:
    return write_artifact_bundle(
        args.artifact_dir,
        mode="plan",
        manifest=build_manifest(args),
        checks=None,
        status="plan_ready",
        detail="overtime acceptance catalog ready; no staging mutation performed",
    )


def run_preflight(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    checks = preflight_checks(args)
    failed = [item for item in checks if item.status != "passed"]
    status = "preflight_passed" if not failed else "preflight_failed"
    detail = (
        "staging topology ready for overtime acceptance"
        if not failed
        else f"{len(failed)} preflight checks failed"
    )
    summary = write_artifact_bundle(
        args.artifact_dir,
        mode="preflight",
        manifest=build_manifest(args),
        checks=checks,
        status=status,
        detail=detail,
    )
    return summary, 0 if not failed else 1


def run_execute(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if os.getenv(EXECUTION_CONFIRM_ENV) != EXECUTION_CONFIRM_VALUE:
        summary = write_artifact_bundle(
            args.artifact_dir,
            mode="execute",
            manifest=build_manifest(args),
            checks=None,
            status="execute_blocked",
            detail=(
                f"set {EXECUTION_CONFIRM_ENV}={EXECUTION_CONFIRM_VALUE} after green preflight"
            ),
        )
        return summary, 2

    summary, code = run_preflight(args)
    if code != 0:
        summary["status"] = "execute_blocked_by_preflight"
        write_json(args.artifact_dir / "summary.json", summary)
        return summary, 1

    summary = write_artifact_bundle(
        args.artifact_dir,
        mode="execute",
        manifest=build_manifest(args),
        checks=None,
        status="execute_blocked",
        detail=(
            "topology preflight passed, but mutating overtime scenario drivers "
            "are not wired yet; deploy migration-first code and add drivers next"
        ),
    )
    return summary, 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["plan", "preflight", "execute"], default="plan")
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--iran-base-url", default=os.getenv("STAGING_IRAN_BASE_URL", DEFAULT_IRAN_BASE_URL))
    parser.add_argument(
        "--foreign-base-url",
        default=os.getenv("STAGING_FOREIGN_BASE_URL", DEFAULT_FOREIGN_BASE_URL),
    )
    parser.add_argument("--basic-auth-user", default=os.getenv("STAGING_BASIC_AUTH_USER"))
    parser.add_argument("--basic-auth-password", default=os.getenv("STAGING_BASIC_AUTH_PASSWORD"))
    parser.add_argument("--expected-release-sha", default=os.getenv("STAGING_EXPECTED_RELEASE_SHA"))
    parser.add_argument(
        "--expected-branch",
        default=os.getenv(
            "STAGING_OFFER_OVERTIME_EXPECTED_BRANCH",
            os.getenv("STAGING_EXPECTED_BRANCH", DEFAULT_EXPECTED_BRANCH),
        ),
    )
    args = parser.parse_args(argv)
    if args.artifact_dir is None:
        args.artifact_dir = DEFAULT_ARTIFACT_ROOT / args.run_id
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "plan":
        summary = run_plan(args)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["status"] == "plan_ready" else 1
    if args.mode == "preflight":
        summary, code = run_preflight(args)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return code
    summary, code = run_execute(args)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
