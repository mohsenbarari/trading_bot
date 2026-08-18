#!/usr/bin/env python3
"""Plan/preflight/execute the Stage 16 offer-overtime staging acceptance matrix.

`plan` and `preflight` are non-mutating. `execute` remains fail-closed until the
confirm env is set, topology preflight is green, and every driver transport is
available. Fourteen domain scenarios run in staging app containers; the Telegram
axis delegates to the channel-safe B2B command/receipt harness.

This runner stays free of `core.db` / production-matrix imports so it can load
under staging env files that use sync database URLs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
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
DEFAULT_EXPECTED_BRANCH = "main"
DEFAULT_IRAN_BASE_URL = "https://staging.gold-trade.ir"
DEFAULT_FOREIGN_BASE_URL = "https://staging.362514.ir"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "staging-offer-overtime-acceptance"
EXECUTION_CONFIRM_ENV = "STAGING_OFFER_OVERTIME_ACCEPTANCE_CONFIRM"
EXECUTION_CONFIRM_VALUE = "execute-staging-offer-overtime-acceptance"
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
    {
        "id": "OT-TG-B2B-RECEIPT",
        "surface": "foreign",
        "requires": ["telegram_b2b_command_receipt", "immutable_publisher_owner"],
    },
    {"id": "OT-UI-RECONNECT", "surface": "webapp", "requires": ["webapp_poll_reconnect"]},
]

WIRED_IRAN_DRIVER_SCENARIOS = (
    "OT-PREF-WEBAPP-SAVE",
    "OT-PREF-BOT-SAVE",
    "OT-PREF-DISABLED-REGRESSION",
    "OT-OFFER-WEBAPP-ORIGIN",
    "OT-REQ-IRAN-TO-IRAN",
    "OT-CANCEL-REQUESTER",
    "OT-QUEUE-ORDER",
    "OT-FINAL-TAIL",
    "OT-UI-RECONNECT",
)
WIRED_FOREIGN_DRIVER_SCENARIOS = (
    "OT-OFFER-BOT-ORIGIN",
    "OT-REQ-FOREIGN-TO-FOREIGN",
    "OT-REQ-CROSS-FORWARD",
    "OT-CHANNEL-MARKER",
    "OT-SYNC-RECOVERY",
)
WIRED_B2B_DRIVER_SCENARIOS = ("OT-TG-B2B-RECEIPT",)
WIRED_DRIVER_SCENARIOS = (
    WIRED_IRAN_DRIVER_SCENARIOS
    + WIRED_FOREIGN_DRIVER_SCENARIOS
    + WIRED_B2B_DRIVER_SCENARIOS
)


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


def alembic_heads() -> list[str]:
    """Return the checkout's current migration heads.

    Acceptance must validate the migration graph it is about to exercise,
    rather than pinning a head that becomes stale whenever an independently
    reviewed migration line is merged.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return list(ScriptDirectory.from_config(config).get_heads())


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
    heads = alembic_heads()
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": "staging_two_server",
        "feature": "offer_overtime",
        "product_name": "وقت اضافه",
        "mutates_production": False,
        "expected_branch": args.expected_branch,
        "expected_release_sha": args.expected_release_sha or run_git_value(["rev-parse", "HEAD"]),
        "expected_alembic_head": heads[0] if len(heads) == 1 else None,
        "alembic_heads": sorted(heads),
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
    heads = alembic_heads()
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
            "single_alembic_head",
            "passed" if len(heads) == 1 else "failed",
            (
                f"acceptance resolved migration head {heads[0]}"
                if len(heads) == 1
                else f"acceptance requires one migration head, found {sorted(heads)}"
            ),
            payload={"alembic_heads": sorted(heads)},
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


def _parse_driver_stdout(stdout: str, stderr: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    try:
        return json.loads(text.splitlines()[-1]) if text else {}
    except json.JSONDecodeError:
        return {
            "passed": False,
            "error": "driver stdout was not JSON",
        }


def iran_driver_argv(
    scenario: str,
    run_prefix: str,
    minutes: int,
    *,
    extra_args: tuple[str, ...] = (),
) -> list[str] | None:
    """Build a shell-quoted remote Iran command from validated argv fields."""
    host = (os.getenv("STAGING_IRAN_SSH_HOST") or "").strip()
    if not host:
        return None
    if host.startswith("-") or not all(
        character.isalnum() or character in ".:-" for character in host
    ):
        raise ValueError("invalid STAGING_IRAN_SSH_HOST")
    port = (os.getenv("STAGING_IRAN_SSH_PORT") or "22").strip()
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("invalid STAGING_IRAN_SSH_PORT")
    container = (
        os.getenv("STAGING_IRAN_APP_CONTAINER") or "trading_bot_staging_iran-app-1"
    ).strip()
    driver_args = [
        "docker",
        "exec",
        container,
        "python",
        "scripts/staging_overtime_scenario_driver.py",
        "--scenario",
        scenario,
        "--run-prefix",
        run_prefix,
        "--minutes",
        str(minutes),
        *extra_args,
    ]
    remote = " ".join(shlex.quote(value) for value in driver_args)
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-p",
        port,
        f"root@{host}",
        remote,
    ]


def foreign_driver_argv(
    scenario: str,
    run_prefix: str,
    minutes: int,
    *,
    extra_args: tuple[str, ...] = (),
) -> list[str] | None:
    """Build the local foreign-container argv without a command shell."""
    if (os.getenv("STAGING_FOREIGN_DRIVER_DISABLE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    container = (
        os.getenv("STAGING_FOREIGN_APP_CONTAINER") or "trading_bot_staging-foreign_app-1"
    ).strip()
    return [
        "docker",
        "exec",
        container,
        "python",
        "scripts/staging_overtime_scenario_driver.py",
        "--scenario",
        scenario,
        "--run-prefix",
        run_prefix,
        "--minutes",
        str(minutes),
        *extra_args,
    ]


def _run_argv(argv: list[str]) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.time()
    try:
        completed = subprocess.run(
            argv,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        completed = subprocess.CompletedProcess(
            args=argv,
            returncode=124,
            stdout="",
            stderr="driver_timeout",
        )
    return completed, round(time.time() - started, 3)


def iran_cleanup_argv(run_prefix: str) -> list[str] | None:
    return iran_driver_argv(
        SCENARIOS[0]["id"],
        run_prefix,
        0,
        extra_args=("--mode", "cleanup"),
    )


def run_two_peer_foreign_driver(
    *,
    scenario: str,
    run_prefix: str,
    minutes: int,
    foreign_extra_from_seed,
) -> dict[str, Any]:
    """Seed on Iran, mutate on foreign, retire on Iran (registration sync v2)."""
    seed_argv = iran_driver_argv(
        scenario,
        run_prefix,
        minutes,
        extra_args=("--phase", "seed", "--no-cleanup-after"),
    )
    foreign_probe = foreign_driver_argv(
        scenario,
        run_prefix,
        minutes,
        extra_args=(
            "--phase",
            "run",
            "--owner-user-id",
            "0",
            "--no-cleanup-after",
        ),
    )
    if seed_argv is None or foreign_probe is None:
        return {
            "id": scenario,
            "status": "blocked",
            "detail": (
                "set STAGING_IRAN_SSH_HOST for seed/cleanup and ensure the foreign "
                "app container is reachable"
            ),
            "run_prefix": run_prefix,
        }

    seed_completed, seed_elapsed = _run_argv(seed_argv)
    seed_payload = _parse_driver_stdout(seed_completed.stdout, seed_completed.stderr)
    if not (seed_payload.get("passed") and seed_completed.returncode == 0):
        return {
            "id": scenario,
            "status": "failed",
            "elapsed_seconds": seed_elapsed,
            "run_prefix": run_prefix,
            "phase": "seed",
            "returncode": seed_completed.returncode,
            "payload": seed_payload,
        }

    run_argv = foreign_driver_argv(
        scenario,
        run_prefix,
        minutes,
        extra_args=foreign_extra_from_seed(seed_payload),
    )
    assert run_argv is not None
    run_completed, run_elapsed = _run_argv(run_argv)
    run_payload = _parse_driver_stdout(run_completed.stdout, run_completed.stderr)

    cleanup_argv = iran_cleanup_argv(run_prefix)
    if cleanup_argv is not None:
        cleanup_completed, cleanup_elapsed = _run_argv(cleanup_argv)
        cleanup_payload = _parse_driver_stdout(
            cleanup_completed.stdout, cleanup_completed.stderr
        )
    else:
        cleanup_elapsed = 0.0
        cleanup_payload = {"passed": False, "error": "cleanup transport unavailable"}

    passed = (
        bool(run_payload.get("passed"))
        and run_completed.returncode == 0
        and bool(cleanup_payload.get("passed"))
    )
    return {
        "id": scenario,
        "status": "passed" if passed else "failed",
        "elapsed_seconds": round(seed_elapsed + run_elapsed + cleanup_elapsed, 3),
        "run_prefix": run_prefix,
        "seed": seed_payload,
        "run": run_payload,
        "cleanup": cleanup_payload,
    }


def run_offer_bot_origin_driver(args: argparse.Namespace, run_prefix: str) -> dict[str, Any]:
    del args
    return run_two_peer_foreign_driver(
        scenario="OT-OFFER-BOT-ORIGIN",
        run_prefix=run_prefix,
        minutes=5,
        foreign_extra_from_seed=lambda seed: (
            "--phase",
            "run",
            "--owner-user-id",
            str(int(seed["owner_user_id"])),
            "--no-cleanup-after",
        ),
    )


def run_req_foreign_to_foreign_driver(
    args: argparse.Namespace, run_prefix: str
) -> dict[str, Any]:
    del args
    return run_two_peer_foreign_driver(
        scenario="OT-REQ-FOREIGN-TO-FOREIGN",
        run_prefix=run_prefix,
        minutes=5,
        foreign_extra_from_seed=lambda seed: (
            "--phase",
            "run",
            "--owner-user-id",
            str(int(seed["owner_user_id"])),
            "--requester-user-id",
            str(int(seed["requester_user_id"])),
            "--no-cleanup-after",
        ),
    )


def run_channel_marker_driver(
    args: argparse.Namespace, run_prefix: str
) -> dict[str, Any]:
    del args
    return run_two_peer_foreign_driver(
        scenario="OT-CHANNEL-MARKER",
        run_prefix=run_prefix,
        minutes=5,
        foreign_extra_from_seed=lambda seed: (
            "--phase",
            "run",
            "--owner-user-id",
            str(int(seed["owner_user_id"])),
            "--no-cleanup-after",
        ),
    )


def run_b2b_receipt_driver(
    args: argparse.Namespace, run_prefix: str
) -> dict[str, Any]:
    """Exercise real command/ACK transport without a possible channel post."""
    del args
    if (os.getenv("STAGING_FOREIGN_DRIVER_DISABLE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return {
            "id": "OT-TG-B2B-RECEIPT",
            "status": "blocked",
            "detail": "foreign staging driver is disabled",
            "run_prefix": run_prefix,
        }
    container = (
        os.getenv("STAGING_FOREIGN_APP_CONTAINER")
        or "trading_bot_staging-foreign_app-1"
    ).strip()
    digest = hashlib.sha256(run_prefix.encode("utf-8")).hexdigest()[:16]
    argv = [
        "docker",
        "exec",
        "-w",
        "/app",
        "-e",
        "PYTHONPATH=/app",
        container,
        "python",
        "scripts/run_telegram_publisher_b2b_harness.py",
        "--authorize-live-staging",
        "--run-id",
        f"b2b-light-overtime-{digest}",
        "--messages-per-lane",
        "2",
    ]
    completed, elapsed = _run_argv(argv)
    payload = _parse_driver_stdout(completed.stdout, completed.stderr)
    passed = bool(payload.get("passed")) and completed.returncode == 0
    return {
        "id": "OT-TG-B2B-RECEIPT",
        "status": "passed" if passed else "failed",
        "elapsed_seconds": elapsed,
        "run_prefix": run_prefix,
        "returncode": completed.returncode,
        "payload": payload,
    }


def _sync_worker_containers() -> tuple[str, str]:
    iran = (
        os.getenv("STAGING_IRAN_SYNC_CONTAINER")
        or "trading_bot_staging_iran-sync_worker-1"
    ).strip()
    foreign = (
        os.getenv("STAGING_FOREIGN_SYNC_CONTAINER")
        or "trading_bot_staging-foreign_sync_worker-1"
    ).strip()
    return iran, foreign


def _iran_docker_argv(*docker_args: str) -> list[str] | None:
    host = (os.getenv("STAGING_IRAN_SSH_HOST") or "").strip()
    if not host:
        return None
    if host.startswith("-") or not all(
        character.isalnum() or character in ".:-" for character in host
    ):
        raise ValueError("invalid STAGING_IRAN_SSH_HOST")
    port = (os.getenv("STAGING_IRAN_SSH_PORT") or "22").strip()
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("invalid STAGING_IRAN_SSH_PORT")
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-p",
        port,
        f"root@{host}",
        " ".join(shlex.quote(value) for value in ("docker", *docker_args)),
    ]


def stop_staging_sync_workers() -> dict[str, Any]:
    iran_container, foreign_container = _sync_worker_containers()
    iran_argv = _iran_docker_argv("stop", iran_container)
    foreign_argv = ["docker", "stop", foreign_container]
    details: dict[str, Any] = {}
    if iran_argv is None:
        return {"ok": False, "detail": "STAGING_IRAN_SSH_HOST unset for sync stop"}
    iran_done, iran_elapsed = _run_argv(iran_argv)
    foreign_done, foreign_elapsed = _run_argv(foreign_argv)
    details["iran"] = {
        "returncode": iran_done.returncode,
        "elapsed_seconds": iran_elapsed,
    }
    details["foreign"] = {
        "returncode": foreign_done.returncode,
        "elapsed_seconds": foreign_elapsed,
    }
    details["ok"] = iran_done.returncode == 0 and foreign_done.returncode == 0
    return details


def start_staging_sync_workers() -> dict[str, Any]:
    iran_container, foreign_container = _sync_worker_containers()
    iran_argv = _iran_docker_argv("start", iran_container)
    foreign_argv = ["docker", "start", foreign_container]
    details: dict[str, Any] = {}
    if iran_argv is None:
        return {"ok": False, "detail": "STAGING_IRAN_SSH_HOST unset for sync start"}
    iran_done, iran_elapsed = _run_argv(iran_argv)
    foreign_done, foreign_elapsed = _run_argv(foreign_argv)
    details["iran"] = {
        "returncode": iran_done.returncode,
        "elapsed_seconds": iran_elapsed,
    }
    details["foreign"] = {
        "returncode": foreign_done.returncode,
        "elapsed_seconds": foreign_elapsed,
    }
    details["ok"] = iran_done.returncode == 0 and foreign_done.returncode == 0
    return details


def run_sync_recovery_driver(
    args: argparse.Namespace, run_prefix: str
) -> dict[str, Any]:
    """Interrupt sync workers, mutate under partition, then prove converge."""
    del args
    minutes = 5
    seed_argv = iran_driver_argv(
        "OT-SYNC-RECOVERY",
        run_prefix,
        minutes,
        extra_args=("--phase", "seed", "--no-cleanup-after"),
    )
    foreign_probe = foreign_driver_argv(
        "OT-SYNC-RECOVERY",
        run_prefix,
        minutes,
        extra_args=(
            "--phase",
            "assert_mirror",
            "--request-a-public-id",
            "x",
            "--no-cleanup-after",
        ),
    )
    if seed_argv is None or foreign_probe is None:
        return {
            "id": "OT-SYNC-RECOVERY",
            "status": "blocked",
            "detail": (
                "set STAGING_IRAN_SSH_HOST for seed/cleanup and ensure the foreign "
                "app container is reachable"
            ),
            "run_prefix": run_prefix,
        }

    phases: dict[str, Any] = {}
    workers_stopped = False
    restart_required = False
    try:
        seed_completed, seed_elapsed = _run_argv(seed_argv)
        seed_payload = _parse_driver_stdout(seed_completed.stdout, seed_completed.stderr)
        phases["seed"] = seed_payload
        if not (seed_payload.get("passed") and seed_completed.returncode == 0):
            return {
                "id": "OT-SYNC-RECOVERY",
                "status": "failed",
                "elapsed_seconds": seed_elapsed,
                "run_prefix": run_prefix,
                "phase": "seed",
                "phases": phases,
            }

        request_a = str(seed_payload["request_a_public_id"])
        owner_id = int(seed_payload["owner_user_id"])
        offer_public_id = str(seed_payload["offer_public_id"])

        mirror_argv = foreign_driver_argv(
            "OT-SYNC-RECOVERY",
            run_prefix,
            minutes,
            extra_args=(
                "--phase",
                "assert_mirror",
                "--request-a-public-id",
                request_a,
                "--no-cleanup-after",
            ),
        )
        assert mirror_argv is not None
        mirror_completed, mirror_elapsed = _run_argv(mirror_argv)
        mirror_payload = _parse_driver_stdout(
            mirror_completed.stdout, mirror_completed.stderr
        )
        phases["assert_mirror"] = mirror_payload
        if not (mirror_payload.get("passed") and mirror_completed.returncode == 0):
            return {
                "id": "OT-SYNC-RECOVERY",
                "status": "failed",
                "elapsed_seconds": round(seed_elapsed + mirror_elapsed, 3),
                "run_prefix": run_prefix,
                "phase": "assert_mirror",
                "phases": phases,
            }

        restart_required = True
        stop_details = stop_staging_sync_workers()
        phases["stop_sync_workers"] = stop_details
        workers_stopped = bool(stop_details.get("ok"))
        if not workers_stopped:
            return {
                "id": "OT-SYNC-RECOVERY",
                "status": "failed",
                "elapsed_seconds": round(seed_elapsed + mirror_elapsed, 3),
                "run_prefix": run_prefix,
                "phase": "stop_sync_workers",
                "phases": phases,
            }

        mutate_argv = iran_driver_argv(
            "OT-SYNC-RECOVERY",
            run_prefix,
            minutes,
            extra_args=(
                "--phase",
                "partition_mutate",
                "--request-a-public-id",
                request_a,
                "--no-cleanup-after",
            ),
        )
        assert mutate_argv is not None
        mutate_completed, mutate_elapsed = _run_argv(mutate_argv)
        mutate_payload = _parse_driver_stdout(
            mutate_completed.stdout, mutate_completed.stderr
        )
        phases["partition_mutate"] = mutate_payload
        if not (mutate_payload.get("passed") and mutate_completed.returncode == 0):
            return {
                "id": "OT-SYNC-RECOVERY",
                "status": "failed",
                "elapsed_seconds": round(
                    seed_elapsed + mirror_elapsed + mutate_elapsed, 3
                ),
                "run_prefix": run_prefix,
                "phase": "partition_mutate",
                "phases": phases,
            }

        request_b = str(mutate_payload["request_b_public_id"])
        skew_argv = foreign_driver_argv(
            "OT-SYNC-RECOVERY",
            run_prefix,
            minutes,
            extra_args=(
                "--phase",
                "assert_skew",
                "--request-a-public-id",
                request_a,
                "--request-b-public-id",
                request_b,
                "--no-cleanup-after",
            ),
        )
        assert skew_argv is not None
        skew_completed, skew_elapsed = _run_argv(skew_argv)
        skew_payload = _parse_driver_stdout(skew_completed.stdout, skew_completed.stderr)
        phases["assert_skew"] = skew_payload
        if not (skew_payload.get("passed") and skew_completed.returncode == 0):
            return {
                "id": "OT-SYNC-RECOVERY",
                "status": "failed",
                "elapsed_seconds": round(
                    seed_elapsed + mirror_elapsed + mutate_elapsed + skew_elapsed, 3
                ),
                "run_prefix": run_prefix,
                "phase": "assert_skew",
                "phases": phases,
            }
    finally:
        # Restart after every stop attempt, including a partial stop. Never
        # start workers that the operator had already stopped before this run.
        phases["start_sync_workers"] = (
            start_staging_sync_workers()
            if restart_required
            else {"ok": True, "skipped": "stop_not_attempted"}
        )

    # Give workers a brief moment to reconnect before converge polls.
    time.sleep(3)
    request_b = str(phases.get("partition_mutate", {}).get("request_b_public_id") or "")
    request_a = str(phases.get("seed", {}).get("request_a_public_id") or "")
    owner_id = int(phases.get("seed", {}).get("owner_user_id") or 0)
    offer_public_id = str(phases.get("seed", {}).get("offer_public_id") or "")

    converge_extra = (
        "--phase",
        "assert_converge",
        "--owner-user-id",
        str(owner_id),
        "--offer-public-id",
        offer_public_id,
        "--request-a-public-id",
        request_a,
        "--request-b-public-id",
        request_b,
        "--no-cleanup-after",
    )
    foreign_converge_argv = foreign_driver_argv(
        "OT-SYNC-RECOVERY", run_prefix, minutes, extra_args=converge_extra
    )
    iran_converge_argv = iran_driver_argv(
        "OT-SYNC-RECOVERY", run_prefix, minutes, extra_args=converge_extra
    )
    assert foreign_converge_argv is not None and iran_converge_argv is not None

    foreign_converge_completed, foreign_converge_elapsed = _run_argv(foreign_converge_argv)
    foreign_converge = _parse_driver_stdout(
        foreign_converge_completed.stdout, foreign_converge_completed.stderr
    )
    phases["assert_converge_foreign"] = foreign_converge

    iran_converge_completed, iran_converge_elapsed = _run_argv(iran_converge_argv)
    iran_converge = _parse_driver_stdout(
        iran_converge_completed.stdout, iran_converge_completed.stderr
    )
    phases["assert_converge_iran"] = iran_converge

    cleanup_argv = iran_cleanup_argv(run_prefix)
    if cleanup_argv is not None:
        cleanup_completed, cleanup_elapsed = _run_argv(cleanup_argv)
        cleanup_payload = _parse_driver_stdout(
            cleanup_completed.stdout, cleanup_completed.stderr
        )
    else:
        cleanup_elapsed = 0.0
        cleanup_payload = {"passed": False, "error": "cleanup transport unavailable"}
    phases["cleanup"] = cleanup_payload

    passed = (
        bool(foreign_converge.get("passed"))
        and foreign_converge_completed.returncode == 0
        and bool(iran_converge.get("passed"))
        and iran_converge_completed.returncode == 0
        and bool(cleanup_payload.get("passed"))
        and bool(phases.get("assert_skew", {}).get("passed"))
        and bool(phases.get("start_sync_workers", {}).get("ok"))
    )
    return {
        "id": "OT-SYNC-RECOVERY",
        "status": "passed" if passed else "failed",
        "elapsed_seconds": round(
            foreign_converge_elapsed + iran_converge_elapsed + cleanup_elapsed, 3
        ),
        "run_prefix": run_prefix,
        "phases": phases,
    }


def run_req_cross_forward_driver(
    args: argparse.Namespace, run_prefix: str
) -> dict[str, Any]:
    """Seed on Iran, re-pin overtime, mutate on foreign, retire on Iran."""
    del args
    minutes = 5
    seed_argv = iran_driver_argv(
        "OT-REQ-CROSS-FORWARD",
        run_prefix,
        minutes,
        extra_args=("--phase", "seed", "--no-cleanup-after"),
    )
    foreign_probe = foreign_driver_argv(
        "OT-REQ-CROSS-FORWARD",
        run_prefix,
        minutes,
        extra_args=(
            "--phase",
            "run",
            "--owner-user-id",
            "0",
            "--no-cleanup-after",
        ),
    )
    if seed_argv is None or foreign_probe is None:
        return {
            "id": "OT-REQ-CROSS-FORWARD",
            "status": "blocked",
            "detail": (
                "set STAGING_IRAN_SSH_HOST for seed/cleanup and ensure the foreign "
                "app container is reachable"
            ),
            "run_prefix": run_prefix,
        }

    seed_completed, seed_elapsed = _run_argv(seed_argv)
    seed_payload = _parse_driver_stdout(seed_completed.stdout, seed_completed.stderr)
    if not (seed_payload.get("passed") and seed_completed.returncode == 0):
        return {
            "id": "OT-REQ-CROSS-FORWARD",
            "status": "failed",
            "elapsed_seconds": seed_elapsed,
            "run_prefix": run_prefix,
            "phase": "seed",
            "returncode": seed_completed.returncode,
            "payload": seed_payload,
        }

    offer_public_id = str(seed_payload["offer_public_id"])
    rebackdate_argv = iran_driver_argv(
        "OT-REQ-CROSS-FORWARD",
        run_prefix,
        minutes,
        extra_args=(
            "--phase",
            "rebackdate",
            "--offer-public-id",
            offer_public_id,
            "--no-cleanup-after",
        ),
    )
    assert rebackdate_argv is not None
    rebackdate_completed, rebackdate_elapsed = _run_argv(rebackdate_argv)
    rebackdate_payload = _parse_driver_stdout(
        rebackdate_completed.stdout, rebackdate_completed.stderr
    )
    if not (rebackdate_payload.get("passed") and rebackdate_completed.returncode == 0):
        cleanup_argv = iran_cleanup_argv(run_prefix)
        cleanup_payload = {"passed": False}
        if cleanup_argv is not None:
            cleanup_completed, _ = _run_argv(cleanup_argv)
            cleanup_payload = _parse_driver_stdout(
                cleanup_completed.stdout, cleanup_completed.stderr
            )
        return {
            "id": "OT-REQ-CROSS-FORWARD",
            "status": "failed",
            "elapsed_seconds": round(seed_elapsed + rebackdate_elapsed, 3),
            "run_prefix": run_prefix,
            "phase": "rebackdate",
            "seed": seed_payload,
            "rebackdate": rebackdate_payload,
            "cleanup": cleanup_payload,
        }

    run_argv = foreign_driver_argv(
        "OT-REQ-CROSS-FORWARD",
        run_prefix,
        minutes,
        extra_args=(
            "--phase",
            "run",
            "--owner-user-id",
            str(int(seed_payload["owner_user_id"])),
            "--requester-user-id",
            str(int(seed_payload["requester_user_id"])),
            "--offer-public-id",
            offer_public_id,
            "--no-cleanup-after",
        ),
    )
    assert run_argv is not None
    run_completed, run_elapsed = _run_argv(run_argv)
    run_payload = _parse_driver_stdout(run_completed.stdout, run_completed.stderr)

    cleanup_argv = iran_cleanup_argv(run_prefix)
    if cleanup_argv is not None:
        cleanup_completed, cleanup_elapsed = _run_argv(cleanup_argv)
        cleanup_payload = _parse_driver_stdout(
            cleanup_completed.stdout, cleanup_completed.stderr
        )
    else:
        cleanup_elapsed = 0.0
        cleanup_payload = {"passed": False, "error": "cleanup transport unavailable"}

    passed = (
        bool(run_payload.get("passed"))
        and run_completed.returncode == 0
        and bool(cleanup_payload.get("passed"))
    )
    return {
        "id": "OT-REQ-CROSS-FORWARD",
        "status": "passed" if passed else "failed",
        "elapsed_seconds": round(
            seed_elapsed + rebackdate_elapsed + run_elapsed + cleanup_elapsed, 3
        ),
        "run_prefix": run_prefix,
        "seed": seed_payload,
        "rebackdate": rebackdate_payload,
        "run": run_payload,
        "cleanup": cleanup_payload,
    }


def run_wired_drivers(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Execute the wired Iran and foreign overtime drivers."""
    results: list[dict[str, Any]] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    for index, scenario in enumerate(WIRED_IRAN_DRIVER_SCENARIOS):
        run_prefix = f"OTACC_{stamp}_{index:02d}"
        minutes = (
            5
            if scenario
            in {
                "OT-OFFER-WEBAPP-ORIGIN",
                "OT-REQ-IRAN-TO-IRAN",
                "OT-CANCEL-REQUESTER",
                "OT-QUEUE-ORDER",
                "OT-FINAL-TAIL",
                "OT-UI-RECONNECT",
            }
            else 4
        )
        argv = iran_driver_argv(scenario, run_prefix, minutes)
        if argv is None:
            results.append(
                {
                    "id": scenario,
                    "status": "blocked",
                    "detail": (
                        "set STAGING_IRAN_SSH_HOST (and optional STAGING_IRAN_SSH_PORT / "
                        "STAGING_IRAN_APP_CONTAINER)"
                    ),
                }
            )
            continue
        completed, elapsed = _run_argv(argv)
        payload = _parse_driver_stdout(completed.stdout, completed.stderr)
        passed = bool(payload.get("passed")) and completed.returncode == 0
        result = {
            "id": scenario,
            "status": "passed" if passed else "failed",
            "elapsed_seconds": elapsed,
            "run_prefix": run_prefix,
            "returncode": completed.returncode,
            "payload": payload,
        }
        results.append(result)
        write_json(args.artifact_dir / f"driver-{scenario}.json", result)

    for index, scenario in enumerate(WIRED_FOREIGN_DRIVER_SCENARIOS):
        run_prefix = f"OTACC_{stamp}_F{index:02d}"
        if scenario == "OT-OFFER-BOT-ORIGIN":
            result = run_offer_bot_origin_driver(args, run_prefix)
        elif scenario == "OT-REQ-FOREIGN-TO-FOREIGN":
            result = run_req_foreign_to_foreign_driver(args, run_prefix)
        elif scenario == "OT-REQ-CROSS-FORWARD":
            result = run_req_cross_forward_driver(args, run_prefix)
        elif scenario == "OT-CHANNEL-MARKER":
            result = run_channel_marker_driver(args, run_prefix)
        elif scenario == "OT-SYNC-RECOVERY":
            result = run_sync_recovery_driver(args, run_prefix)
        else:
            result = {
                "id": scenario,
                "status": "blocked",
                "detail": "no foreign orchestration implemented",
                "run_prefix": run_prefix,
            }
        results.append(result)
        write_json(args.artifact_dir / f"driver-{scenario}.json", result)

    for index, scenario in enumerate(WIRED_B2B_DRIVER_SCENARIOS):
        run_prefix = f"OTACC_{stamp}_B{index:02d}"
        result = run_b2b_receipt_driver(args, run_prefix)
        results.append(result)
        write_json(args.artifact_dir / f"driver-{scenario}.json", result)
    return results

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

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    driver_results = run_wired_drivers(args)
    write_json(args.artifact_dir / "driver-results.json", {"results": driver_results})

    wired_failed = [item for item in driver_results if item["status"] == "failed"]
    wired_blocked = [item for item in driver_results if item["status"] == "blocked"]
    wired_passed = [item for item in driver_results if item["status"] == "passed"]
    unwired = [
        item["id"] for item in SCENARIOS if item["id"] not in WIRED_DRIVER_SCENARIOS
    ]

    if wired_failed:
        status = "execute_failed"
        detail = f"{len(wired_failed)} wired overtime drivers failed"
        exit_code = 1
    elif wired_blocked:
        status = "execute_blocked"
        detail = (
            "topology preflight passed, but driver transport is incomplete; "
            f"{len(WIRED_DRIVER_SCENARIOS)} drivers are implemented"
        )
        exit_code = 3
    elif unwired:
        status = "execute_partial"
        detail = (
            f"{len(wired_passed)}/{len(SCENARIOS)} scenarios passed via wired drivers; "
            f"{len(unwired)} remain unwired"
        )
        exit_code = 4
    else:
        status = "execute_passed"
        detail = "all overtime acceptance scenarios passed"
        exit_code = 0

    summary = write_artifact_bundle(
        args.artifact_dir,
        mode="execute",
        manifest=build_manifest(args),
        checks=None,
        status=status,
        detail=detail,
    )
    summary["wired_driver_results"] = driver_results
    summary["unwired_scenarios"] = unwired
    write_json(args.artifact_dir / "summary.json", summary)
    return summary, exit_code


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
