#!/usr/bin/env python3
"""Consume the production full-matrix manifest and build an execution plan.

This runner is fail-closed by default. It can build a manifest-driven command
plan without production writes, execute live non-mutating preflight checks, and
execute the guarded command plan only when the explicit production confirmation
environment variable is present.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import build_production_full_matrix_manifest as manifest_builder


SCHEMA_VERSION = "production_full_matrix_runner_plan_v1"
EXECUTION_CONFIRM_ENV = "PRODUCTION_FULL_MATRIX_CONFIRM"
EXECUTION_CONFIRM_VALUE = "execute-production-full-matrix"
PREFLIGHT_CONFIRM_ENV = "PRODUCTION_FULL_MATRIX_PREFLIGHT_CONFIRM"
PREFLIGHT_CONFIRM_VALUE = "run-production-preflight"
CLEANUP_CONFIRM_ENV = "PRODUCTION_TEST_CLEANUP_CONFIRM"
CLEANUP_CONFIRM_VALUE = "hard-delete-test-data"
IRAN_HOST = "root@87.107.3.22"
IRAN_PROJECT_DIR = "/srv/trading-bot/current"
CAMPAIGN_LEDGER_SCHEMA_VERSION = "production_full_matrix_campaign_ledger_v1"
RETRYABLE_SCENARIO_TOKENS = (
    "timeout",
    "timed out",
    "queuepool",
    "staledataerror",
    "uniqueviolationerror",
    "integrityerror",
    "operationalerror",
    "dbapierror",
    "connection refused",
    "connection reset",
    "temporary failure",
    "could not connect",
    "read operation timed out",
    "trade_forward.remote_home_failed",
    "remote authority",
    "سرور مرجع معامله در دسترس نیست",
)

SECTION_DRIVER_STATUS = {
    "market_behavior": "pending_production_market_driver",
    "delivery_contract": "delivery_contract_catalog_assertion_driver",
    "targeted_trade_delivery_join": "targeted_join_delivery_probe_driver",
    "production_base_trade_shape": "two_server_dual_role_driver_plannable_for_user_stable_cases",
    "production_stress_overlay": "two_server_dual_role_driver_plannable_for_hot_user_stable_cases",
    "negative_business_guard": "pending_negative_guard_driver",
}

DUAL_ROLE_EXECUTABLE_STRESS_FAMILIES = {
    "hot_wholesale_concurrent",
    "hot_retail_same_lot_concurrent",
    "hot_retail_mixed_lot_concurrent",
}
DUAL_ROLE_EXECUTABLE_DUPLICATE_REPLAY_REQUEST_SURFACES = {"telegram", "webapp"}
DUAL_ROLE_EXECUTABLE_MANUAL_EXPIRE_RACE_REQUEST_SURFACES = {"telegram", "webapp"}
DUAL_ROLE_EXECUTABLE_TIME_EXPIRE_RACE_REQUEST_SURFACES = {"telegram", "webapp"}
DUAL_ROLE_EXECUTABLE_READ_DURING_WRITE_REQUEST_SURFACES = {"telegram", "webapp"}
MARKET_BEHAVIOR_DEFAULT_TIMEOUT_SECONDS = 1200
MARKET_BEHAVIOR_HEAVY_TIMEOUT_SECONDS = 3600
MARKET_BEHAVIOR_DEFAULT_WRITE_MAX_CONCURRENCY = 24
MARKET_BEHAVIOR_HEAVY_WRITE_MAX_CONCURRENCY = 48
MARKET_BEHAVIOR_READ_VIEW_MAX_CONCURRENCY = 96
MARKET_BEHAVIOR_LOAD_RUNNER_DB_POOL_SIZE = 96
MARKET_BEHAVIOR_LOAD_RUNNER_DB_MAX_OVERFLOW = 64
MARKET_BEHAVIOR_HEAVY_FAMILIES = {
    "trade_non_concurrent",
    "manual_expire_non_concurrent",
}

NEGATIVE_GUARD_EXECUTABLE_CASES = {
    "own_offer_request",
    "invalid_request_amount",
    "retail_lot_unavailable",
    "already_completed_offer",
    "manually_expired_offer",
    "time_expired_offer",
    "market_closed",
    "inactive_offer_owner",
    "inactive_requester",
    "trading_restricted_user",
    "watch_role_market_action",
    "accountant_market_action",
    "tier2_offer_creation",
    "tier2_telegram_request",
    "daily_trade_limit_exceeded",
    "daily_request_limit_exceeded",
    "active_commodity_limit_exceeded",
    "remote_authority_unavailable",
    "bad_internal_signature",
    "wrong_authoritative_server",
    "stale_telegram_button",
    "missing_public_offer_id",
    "cleanup_scope_violation",
}

UNSUPPORTED_POLICY_EXECUTABLE_REASONS = {
    "tier2_cannot_create_offer",
    "tier2_cannot_use_telegram_request",
}

DUAL_ROLE_ROLE_BY_SERVER = {
    "foreign": "telegram_foreign",
    "iran": "webapp_iran",
}

DRIVER_GAP_BUCKETS: dict[str, dict[str, Any]] = {
    "negative_guard_driver": {
        "order": 1,
        "title": "Negative business-policy production guard driver",
        "difficulty": "medium",
        "implementation_note": (
            "Run explicit reject-path probes with exact prefix cleanup and assert no partial trade, "
            "quantity, publication, notification, or receipt mutation leaks."
        ),
    },
    "specialized_user_stress_driver": {
        "order": 2,
        "title": "Specialized user-to-user stable stress drivers",
        "difficulty": "medium",
        "implementation_note": (
            "Add a family-specific dual-role stress driver without changing cross-server forwarding semantics."
        ),
    },
    "market_behavior_driver": {
        "order": 3,
        "title": "Market behavior production driver",
        "difficulty": "medium",
        "implementation_note": (
            "Port the staging comprehensive market matrix to production-safe two-server execution with "
            "real authority routing and no patched cross-server boundaries."
        ),
    },
    "delivery_contract_driver": {
        "order": 4,
        "title": "Delivery contract assertion driver",
        "difficulty": "hard",
        "implementation_note": (
            "Verify WebApp and Telegram delivery receipts/notifications for every actor, surface, and "
            "outage policy after the trade driver has created real production evidence."
        ),
    },
    "targeted_join_driver": {
        "order": 5,
        "title": "Targeted trade-delivery join production driver",
        "difficulty": "hard",
        "implementation_note": (
            "Convert the targeted join matrix from staging/patched boundaries to production two-server "
            "execution while preserving customer-owner routing and fake Telegram transport boundaries."
        ),
    },
    "outage_orchestration_driver": {
        "order": 6,
        "title": "Short and medium outage orchestration driver",
        "difficulty": "hard",
        "implementation_note": (
            "Add reversible app-to-app and sync outage control with different expected outcomes for "
            "short replay and medium skip/finalization behavior."
        ),
    },
    "customer_accountant_actor_driver": {
        "order": 7,
        "title": "Customer/accountant actor-pair production driver",
        "difficulty": "hardest",
        "implementation_note": (
            "Build production fixtures for tier1/tier2 customers, owners, accountants, same-owner and "
            "different-owner paths, then assert owner-channel routing and counterparty privacy."
        ),
    },
    "unknown_driver_gap": {
        "order": 99,
        "title": "Unclassified production driver gap",
        "difficulty": "unknown",
        "implementation_note": "Classify this gap before execution is allowed.",
    },
}

DRIVER_GAP_REASON_TO_BUCKET = {
    "negative_business_guard_production_driver_not_implemented": "negative_guard_driver",
    "negative_business_guard_case_driver_not_implemented": "negative_guard_driver",
    "policy_unsupported_scenario_requires_negative_guard_driver": "negative_guard_driver",
    "stress_family_requires_specialized_race_or_read_driver": "specialized_user_stress_driver",
    "market_behavior_production_driver_not_implemented": "market_behavior_driver",
    "delivery_contract_production_driver_not_implemented": "delivery_contract_driver",
    "targeted_trade_delivery_join_production_driver_not_implemented": "targeted_join_driver",
    "outage_simulation_driver_not_implemented_for_role_worker": "outage_orchestration_driver",
    "dual_role_worker_currently_supports_standard_user_to_standard_user_only": "customer_accountant_actor_driver",
}


class RunnerError(ValueError):
    pass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    args: list[str]
    timeout_seconds: int = 30
    mutates_production: bool = False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunnerError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RunnerError(f"manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RunnerError(f"manifest must be a JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize_command_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def truncate_text(value: str | bytes | None, limit: int = 12000) -> str:
    value = normalize_command_output(value)
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def command_payload(command: CommandSpec) -> dict[str, Any]:
    return {
        "name": command.name,
        "args": command.args,
        "timeout_seconds": command.timeout_seconds,
        "mutates_production": command.mutates_production,
    }


def iran_command(name: str, remote_command: str, *, timeout_seconds: int = 30) -> CommandSpec:
    return CommandSpec(
        name=name,
        args=["ssh", IRAN_HOST, f"cd {IRAN_PROJECT_DIR} && {remote_command}"],
        timeout_seconds=timeout_seconds,
    )


def shell_join(parts: Iterable[Any]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def container_python_command(
    name: str,
    *,
    server: str,
    python_args: list[str],
    env: dict[str, str] | None = None,
    timeout_seconds: int = 300,
    mutates_production: bool = True,
) -> CommandSpec:
    outer_timeout_seconds = timeout_seconds + 30
    timeout_args = ["timeout", "--kill-after=10s", f"{timeout_seconds}s", "python", *python_args]
    if server == "foreign":
        args = ["docker", "compose", "exec", "-T"]
        for key, value in sorted((env or {}).items()):
            args.extend(["-e", f"{key}={value}"])
        args.extend(["app", *timeout_args])
        return CommandSpec(
            name=name,
            args=args,
            timeout_seconds=outer_timeout_seconds,
            mutates_production=mutates_production,
        )
    if server == "iran":
        remote_parts = ["docker-compose", "exec", "-T"]
        for key, value in sorted((env or {}).items()):
            remote_parts.extend(["-e", f"{key}={value}"])
        remote_parts.extend(["app", *timeout_args])
        return CommandSpec(
            name=name,
            args=["ssh", IRAN_HOST, f"cd {IRAN_PROJECT_DIR} && {shell_join(remote_parts)}"],
            timeout_seconds=outer_timeout_seconds,
            mutates_production=mutates_production,
        )
    raise RunnerError(f"unsupported server for container command: {server}")


def host_mkdir_command(name: str, *, server: str, path: str) -> CommandSpec:
    if server == "foreign":
        return CommandSpec(name=name, args=["mkdir", "-p", path], timeout_seconds=30)
    if server == "iran":
        return iran_command(name, f"mkdir -p {shlex.quote(path)}", timeout_seconds=30)
    raise RunnerError(f"unsupported server for mkdir command: {server}")


def copy_between_servers_command(name: str, *, source_server: str, target_server: str, path: str) -> CommandSpec:
    if source_server == target_server:
        return CommandSpec(name=name, args=["test", "-f", path], timeout_seconds=30)
    if source_server == "foreign" and target_server == "iran":
        return CommandSpec(
            name=name,
            args=["scp", path, f"{IRAN_HOST}:{path}"],
            timeout_seconds=120,
            mutates_production=False,
        )
    if source_server == "iran" and target_server == "foreign":
        return CommandSpec(
            name=name,
            args=["scp", f"{IRAN_HOST}:{path}", path],
            timeout_seconds=120,
            mutates_production=False,
        )
    raise RunnerError(f"unsupported server copy: {source_server} -> {target_server}")


def safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value)).strip("_") or "scenario"


def build_preflight_commands(*, prefix: str, artifact_dir: Path) -> list[CommandSpec]:
    manifest_path = artifact_dir / "production-full-matrix-manifest.preflight.json"
    run_plan_path = artifact_dir / "production-full-matrix-run-plan.preflight.json"
    quoted_prefix = shlex.quote(prefix)
    return [
        CommandSpec("git_branch", ["git", "branch", "--show-current"]),
        CommandSpec("git_status", ["git", "status", "--short", "--branch"]),
        CommandSpec("git_head", ["git", "rev-parse", "HEAD"]),
        CommandSpec(
            "build_manifest",
            [
                sys.executable,
                "scripts/build_production_full_matrix_manifest.py",
                "--prefix",
                prefix,
                "--check",
                "--output",
                str(manifest_path),
            ],
            timeout_seconds=60,
        ),
        CommandSpec(
            "build_runner_plan_smoke",
            [
                sys.executable,
                "scripts/run_production_full_matrix.py",
                "--manifest",
                str(manifest_path),
                "--section",
                "production_base_trade_shape",
                "--policy",
                "supported",
                "--max-scenarios",
                "1",
                "--output",
                str(run_plan_path),
            ],
            timeout_seconds=60,
        ),
        CommandSpec("foreign_compose_ps", ["docker", "compose", "ps"], timeout_seconds=30),
        iran_command("iran_compose_ps", "docker-compose ps", timeout_seconds=30),
        CommandSpec(
            "iran_public_config",
            ["curl", "-fsS", "https://coin.gold-trade.ir/api/config"],
            timeout_seconds=20,
        ),
        iran_command(
            "isolation_status",
            "docker-compose exec -T app python scripts/production_test_isolation.py status",
            timeout_seconds=30,
        ),
        CommandSpec(
            "foreign_cleanup_dry_run",
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "app",
                "python",
                "scripts/trading_core_probe_worker.py",
                "cleanup",
                "--prefix",
                prefix,
                "--dry-run",
                "--artifact",
                f"/tmp/{prefix}foreign-core-cleanup-preflight-dry-run.json",
            ],
            timeout_seconds=120,
        ),
        iran_command(
            "iran_cleanup_dry_run",
            (
                "docker-compose exec -T app python scripts/trading_core_probe_worker.py cleanup "
                f"--prefix {quoted_prefix} --dry-run "
                f"--artifact {shlex.quote('/tmp/' + prefix + 'iran-core-cleanup-preflight-dry-run.json')}"
            ),
            timeout_seconds=120,
        ),
    ]


def shape_profile(shape_name: str) -> dict[str, Any]:
    try:
        shape = manifest_builder.market_matrix.SHAPES[shape_name]
    except KeyError as exc:
        raise RunnerError(f"unknown offer shape for execution plan: {shape_name}") from exc
    return {
        "quantity": int(shape.quantity),
        "request_amount": int(shape.request_amount),
        "expected_winner_count": int(shape.expected_winner_count),
        "is_wholesale": bool(shape.is_wholesale),
        "lot_sizes": list(shape.lot_sizes),
    }


def delivery_scenario_id_for_record(record: dict[str, Any]) -> str:
    actor_pair_id = str(record.get("actor_pair_id") or "")
    surface_pair = str(record.get("surface_pair") or "")
    outage_id = str(record.get("outage_id") or "")
    for scenario in manifest_builder.delivery_matrix.build_delivery_scenarios():
        if (
            scenario.actor_pair_id == actor_pair_id
            and scenario.surface_pair == surface_pair
            and scenario.outage_id == outage_id
        ):
            return scenario.scenario_id
    raise RunnerError(
        "could not map record to delivery scenario: "
        f"actor_pair_id={actor_pair_id!r} surface_pair={surface_pair!r} outage_id={outage_id!r}"
    )


def scenario_prefix(prefix: str, manifest_id: str) -> str:
    return f"{prefix}{safe_token(manifest_id)}_"


def dual_role_driver_gap(record: dict[str, Any]) -> str | None:
    section = str(record.get("section") or "")
    if section == "negative_business_guard":
        if record.get("case_id") in NEGATIVE_GUARD_EXECUTABLE_CASES:
            return None
        return "negative_business_guard_case_driver_not_implemented"
    if section in {"market_behavior", "delivery_contract", "targeted_trade_delivery_join"}:
        return None
    if section not in {"production_base_trade_shape", "production_stress_overlay"}:
        return f"{section}_production_driver_not_implemented"
    if record.get("policy_supported") is not True:
        reasons = {str(reason) for reason in (record.get("unsupported_reasons") or [])}
        if section == "production_base_trade_shape" and reasons and reasons <= UNSUPPORTED_POLICY_EXECUTABLE_REASONS:
            return None
        return "policy_unsupported_scenario_requires_negative_guard_driver"
    if record.get("actor_pair_id") != "user__user":
        return None
    if record.get("outage_id") not in {"stable", "short_under_2m", "medium_around_60m"}:
        return "outage_simulation_driver_not_implemented_for_role_worker"
    if section == "production_stress_overlay":
        family = str(record.get("family") or "")
        if family not in DUAL_ROLE_EXECUTABLE_STRESS_FAMILIES:
            duplicate_replay_supported = (
                family == "duplicate_idempotency_replay"
                and str(record.get("request_surface") or "") in DUAL_ROLE_EXECUTABLE_DUPLICATE_REPLAY_REQUEST_SURFACES
            )
            manual_expire_race_supported = (
                family == "manual_expire_trade_race"
                and str(record.get("request_surface") or "")
                in DUAL_ROLE_EXECUTABLE_MANUAL_EXPIRE_RACE_REQUEST_SURFACES
            )
            time_expire_race_supported = (
                family == "time_expire_trade_race"
                and str(record.get("request_surface") or "")
                in DUAL_ROLE_EXECUTABLE_TIME_EXPIRE_RACE_REQUEST_SURFACES
            )
            read_during_write_supported = (
                family == "read_during_write"
                and str(record.get("request_surface") or "")
                in DUAL_ROLE_EXECUTABLE_READ_DURING_WRITE_REQUEST_SURFACES
            )
            if (
                not duplicate_replay_supported
                and not manual_expire_race_supported
                and not time_expire_race_supported
                and not read_during_write_supported
            ):
                return "stress_family_requires_specialized_race_or_read_driver"
    if record.get("offer_surface") not in {"webapp", "telegram"}:
        return "unsupported_offer_surface_for_dual_role_worker"
    if record.get("request_surface") not in {"webapp", "telegram"}:
        return "unsupported_request_surface_for_dual_role_worker"
    return None


def driver_gap_bucket_for_reason(reason: str) -> str:
    return DRIVER_GAP_REASON_TO_BUCKET.get(str(reason), "unknown_driver_gap")


def dual_role_scenario_commands(
    record: dict[str, Any],
    *,
    prefix: str,
    artifact_dir: Path,
    user_count: int,
    hot_offer_requests: int,
    target_rps: float,
    telegram_ratio: float,
) -> dict[str, Any]:
    manifest_id = str(record.get("manifest_id") or "scenario")
    scenario_run_prefix = scenario_prefix(prefix, manifest_id)
    scenario_dir = artifact_dir / "scenarios" / safe_token(manifest_id)
    remote_dir = f"/tmp/{scenario_run_prefix}dual-role"
    offer_surface = str(record.get("offer_surface") or "")
    offer_origin = "bot" if offer_surface == "telegram" else "webapp"
    offer_home_server = str(record.get("offer_home_server") or "")
    if offer_home_server not in DUAL_ROLE_ROLE_BY_SERVER:
        raise RunnerError(f"{manifest_id}: unsupported offer_home_server={offer_home_server!r}")
    profile = shape_profile(str(record.get("shape") or ""))
    family = str(record.get("family") or "")
    is_duplicate_replay = family == "duplicate_idempotency_replay"
    is_manual_expire_trade_race = family == "manual_expire_trade_race"
    is_time_expire_trade_race = family == "time_expire_trade_race"
    is_read_during_write = family == "read_during_write"
    if is_duplicate_replay:
        total_requests = 2
        expected_winner_count = 1
        expected_remaining_quantity = max(0, int(profile["quantity"]) - int(profile["request_amount"]))
        require_terminal_completed = expected_remaining_quantity == 0
        request_surface = str(record.get("request_surface") or "webapp")
    elif is_manual_expire_trade_race or is_time_expire_trade_race or is_read_during_write:
        total_requests = max(int(record.get("min_parallel_requests") or 0), int(profile["expected_winner_count"]) + 2)
        expected_winner_count = int(profile["expected_winner_count"])
        expected_remaining_quantity = 0
        require_terminal_completed = False
        request_surface = str(record.get("request_surface") or "webapp")
    else:
        total_requests = max(
            int(hot_offer_requests),
            int(record.get("min_parallel_requests") or 0),
            int(profile["expected_winner_count"]) + 36,
        )
        expected_winner_count = int(profile["expected_winner_count"])
        expected_remaining_quantity = 0
        require_terminal_completed = True
        request_surface = "mixed"
    prepare_args = [
        "scripts/trading_core_probe_worker.py",
        "prepare-dual-role-run",
        "--output-dir",
        remote_dir,
        "--prefix",
        scenario_run_prefix,
        "--run-id",
        scenario_run_prefix.rstrip("_"),
        "--offer-origin",
        offer_origin,
        "--request-surface",
        request_surface,
        "--idempotency-mode",
        "duplicate_replay" if is_duplicate_replay else "unique",
        "--user-count",
        str(user_count),
        "--hot-offer-requests",
        str(total_requests),
        "--telegram-ratio",
        str(telegram_ratio),
        "--target-rps",
        str(target_rps),
        "--hot-offer-quantity",
        str(profile["quantity"]),
        "--request-amount",
        str(profile["request_amount"]),
        "--expected-winner-count",
        str(expected_winner_count),
        "--expected-remaining-quantity",
        str(expected_remaining_quantity),
        "--price",
        "100000",
        "--offer-type",
        str(record.get("offer_type") or "sell"),
        "--barrier-delay-seconds",
        "25",
        "--allow-production-execution",
        "--allow-production-cleanup",
    ]
    if family:
        prepare_args.extend(["--scenario-name", family])
    if not profile["is_wholesale"]:
        prepare_args.append("--retail")
        prepare_args.extend(["--lot-sizes", " ".join(str(item) for item in profile["lot_sizes"])])
    if not require_terminal_completed:
        prepare_args.append("--allow-nonterminal-offer")

    production_env = {
        EXECUTION_CONFIRM_ENV: EXECUTION_CONFIRM_VALUE,
        CLEANUP_CONFIRM_ENV: CLEANUP_CONFIRM_VALUE,
        "TRADING_BOT_SERVICE": "load_runner",
        "TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH": "1",
        "BOT_TOKEN": "",
    }
    pre_role_commands = [
        container_python_command(
            "prepare_on_offer_home_server",
            server=offer_home_server,
            python_args=prepare_args,
            env=production_env,
            timeout_seconds=600,
        ),
        CommandSpec("sync_health_after_prepare_foreign", ["make", "sync-health"], timeout_seconds=90),
        iran_command("sync_health_after_prepare_iran", "make sync-health", timeout_seconds=90),
        host_mkdir_command(
            "ensure_foreign_artifact_dir",
            server="foreign",
            path=remote_dir,
        ),
        host_mkdir_command(
            "ensure_iran_artifact_dir",
            server="iran",
            path=remote_dir,
        ),
        copy_between_servers_command(
            "distribute_telegram_role_plan",
            source_server=offer_home_server,
            target_server="foreign",
            path=f"{remote_dir}/telegram_foreign.plan.json",
        ),
        copy_between_servers_command(
            "distribute_webapp_role_plan",
            source_server=offer_home_server,
            target_server="iran",
            path=f"{remote_dir}/webapp_iran.plan.json",
        ),
    ]
    if is_read_during_write:
        pre_role_commands.extend(
            [
                copy_between_servers_command(
                    "distribute_prepare_to_foreign",
                    source_server=offer_home_server,
                    target_server="foreign",
                    path=f"{remote_dir}/prepare.json",
                ),
                copy_between_servers_command(
                    "distribute_prepare_to_iran",
                    source_server=offer_home_server,
                    target_server="iran",
                    path=f"{remote_dir}/prepare.json",
                ),
            ]
        )
    role_commands = [
        container_python_command(
            "run_role_telegram_foreign",
            server="foreign",
            python_args=[
                "scripts/trading_core_probe_worker.py",
                "run-role-plan",
                "--plan",
                f"{remote_dir}/telegram_foreign.plan.json",
                "--output",
                f"{remote_dir}/telegram_foreign.result.json",
                "--patch-external-side-effects",
                "--allow-production-execution",
            ],
            env=production_env,
            timeout_seconds=900,
        ),
        container_python_command(
            "run_role_webapp_iran",
            server="iran",
            python_args=[
                "scripts/trading_core_probe_worker.py",
                "run-role-plan",
                "--plan",
                f"{remote_dir}/webapp_iran.plan.json",
                "--output",
                f"{remote_dir}/webapp_iran.result.json",
                "--patch-external-side-effects",
                "--allow-production-execution",
            ],
            env=production_env,
            timeout_seconds=900,
        ),
    ]
    if is_manual_expire_trade_race:
        role_commands.append(
            container_python_command(
                "run_manual_expiry_race_on_offer_home_server",
                server=offer_home_server,
                python_args=[
                    "scripts/trading_core_probe_worker.py",
                    "run-manual-expiry-race",
                    "--prepare",
                    f"{remote_dir}/prepare.json",
                    "--output",
                    f"{remote_dir}/manual_expiry.result.json",
                    "--allow-production-execution",
                ],
                env=production_env,
                timeout_seconds=180,
            )
        )
    if is_time_expire_trade_race:
        role_commands.append(
            container_python_command(
                "run_time_expiry_race_on_offer_home_server",
                server=offer_home_server,
                python_args=[
                    "scripts/trading_core_probe_worker.py",
                    "run-time-expiry-race",
                    "--prepare",
                    f"{remote_dir}/prepare.json",
                    "--output",
                    f"{remote_dir}/time_expiry.result.json",
                    "--allow-production-execution",
                ],
                env=production_env,
                timeout_seconds=180,
            )
        )
    if is_read_during_write:
        role_commands.extend(
            [
                container_python_command(
                    "run_read_during_write_telegram_foreign",
                    server="foreign",
                    python_args=[
                        "scripts/trading_core_probe_worker.py",
                        "run-read-during-write",
                        "--prepare",
                        f"{remote_dir}/prepare.json",
                        "--read-surface",
                        "telegram",
                        "--output",
                        f"{remote_dir}/read_telegram.result.json",
                        "--allow-production-execution",
                    ],
                    env=production_env,
                    timeout_seconds=180,
                ),
                container_python_command(
                    "run_read_during_write_webapp_iran",
                    server="iran",
                    python_args=[
                        "scripts/trading_core_probe_worker.py",
                        "run-read-during-write",
                        "--prepare",
                        f"{remote_dir}/prepare.json",
                        "--read-surface",
                        "webapp",
                        "--output",
                        f"{remote_dir}/read_webapp.result.json",
                        "--allow-production-execution",
                    ],
                    env=production_env,
                    timeout_seconds=180,
                ),
            ]
        )

    finalize_args = [
        "scripts/trading_core_probe_worker.py",
        "finalize-dual-role-run",
        "--prepare",
        f"{remote_dir}/prepare.json",
        "--merged-result",
        f"{remote_dir}/merged.result.json",
        "--output",
        f"{remote_dir}/final.json",
        "--check",
    ]
    if is_manual_expire_trade_race:
        finalize_args.extend(["--manual-expiry-result", f"{remote_dir}/manual_expiry.result.json"])
    if is_time_expire_trade_race:
        finalize_args.extend(["--time-expiry-result", f"{remote_dir}/time_expiry.result.json"])
    if is_read_during_write:
        finalize_args.extend(
            [
                "--read-during-write-result",
                f"{remote_dir}/read_telegram.result.json",
                "--read-during-write-result",
                f"{remote_dir}/read_webapp.result.json",
            ]
        )

    post_role_commands = [
        copy_between_servers_command(
            "collect_telegram_role_result",
            source_server="foreign",
            target_server=offer_home_server,
            path=f"{remote_dir}/telegram_foreign.result.json",
        ),
        copy_between_servers_command(
            "collect_webapp_role_result",
            source_server="iran",
            target_server=offer_home_server,
            path=f"{remote_dir}/webapp_iran.result.json",
        ),
    ]
    if is_read_during_write:
        post_role_commands.extend(
            [
                copy_between_servers_command(
                    "collect_read_telegram_result",
                    source_server="foreign",
                    target_server=offer_home_server,
                    path=f"{remote_dir}/read_telegram.result.json",
                ),
                copy_between_servers_command(
                    "collect_read_webapp_result",
                    source_server="iran",
                    target_server=offer_home_server,
                    path=f"{remote_dir}/read_webapp.result.json",
                ),
            ]
        )
    post_role_commands.extend(
        [
            container_python_command(
                "merge_role_results_on_offer_home_server",
                server=offer_home_server,
                python_args=[
                    "scripts/trading_core_probe_worker.py",
                    "merge-role-results",
                    "--output",
                    f"{remote_dir}/merged.result.json",
                    f"{remote_dir}/telegram_foreign.result.json",
                    f"{remote_dir}/webapp_iran.result.json",
                ],
                timeout_seconds=120,
            ),
            container_python_command(
                "finalize_on_offer_home_server",
                server=offer_home_server,
                python_args=finalize_args,
                timeout_seconds=180,
            ),
        ]
    )
    commands = [*pre_role_commands, *role_commands, *post_role_commands]
    return {
        "manifest_id": manifest_id,
        "status": "planned",
        "driver": "two_server_dual_role_hot_offer",
        "scenario_prefix": scenario_run_prefix,
        "artifact_dir": str(scenario_dir),
        "remote_artifact_dir": remote_dir,
        "offer_home_server": offer_home_server,
        "offer_origin": offer_origin,
        "shape_profile": profile,
        "total_requests": total_requests,
        "request_surface": request_surface,
        "idempotency_mode": "duplicate_replay" if is_duplicate_replay else "unique",
        "expected_winner_count": expected_winner_count,
        "expected_remaining_quantity": expected_remaining_quantity,
        "commands": [command_payload(command) for command in commands],
        "execution_groups": [
            {
                "name": "prepare_and_distribute",
                "mode": "sequential",
                "commands": [command_payload(command) for command in pre_role_commands],
            },
            {
                "name": "role_workers",
                "mode": "concurrent",
                "commands": [command_payload(command) for command in role_commands],
            },
            {
                "name": "collect_merge_finalize",
                "mode": "sequential",
                "commands": [command_payload(command) for command in post_role_commands],
            },
        ],
        "concurrency_note": "run_role_telegram_foreign and run_role_webapp_iran must be started concurrently after prepare.",
    }


def negative_guard_scenario_commands(
    record: dict[str, Any],
    *,
    prefix: str,
    artifact_dir: Path,
) -> dict[str, Any]:
    manifest_id = str(record.get("manifest_id") or "negative_guard")
    case_id = str(record.get("case_id") or "")
    scenario_run_prefix = scenario_prefix(prefix, manifest_id)
    scenario_dir = artifact_dir / "scenarios" / safe_token(manifest_id)
    remote_dir = f"/tmp/{scenario_run_prefix}negative-guard"
    production_env = {
        EXECUTION_CONFIRM_ENV: EXECUTION_CONFIRM_VALUE,
        CLEANUP_CONFIRM_ENV: CLEANUP_CONFIRM_VALUE,
        "TRADING_BOT_SERVICE": "load_runner",
        "TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH": "1",
        "BOT_TOKEN": "",
    }
    commands = [
        host_mkdir_command(
            "ensure_iran_negative_guard_artifact_dir",
            server="iran",
            path=remote_dir,
        ),
        container_python_command(
            "run_negative_guard_case_iran",
            server="iran",
            python_args=[
                "scripts/trading_core_probe_worker.py",
                "run-negative-guard-case",
                "--prefix",
                scenario_run_prefix,
                "--case-id",
                case_id,
                "--output",
                f"{remote_dir}/negative-guard.result.json",
                "--allow-production-execution",
                "--allow-production-cleanup",
            ],
            env=production_env,
            timeout_seconds=360,
        ),
    ]
    return {
        "manifest_id": manifest_id,
        "status": "planned",
        "driver": "negative_guard_webapp_iran_probe",
        "scenario_prefix": scenario_run_prefix,
        "artifact_dir": str(scenario_dir),
        "remote_artifact_dir": remote_dir,
        "case_id": case_id,
        "commands": [command_payload(command) for command in commands],
        "execution_groups": [
            {
                "name": "run_negative_guard",
                "mode": "sequential",
                "commands": [command_payload(command) for command in commands],
            },
        ],
        "safety_note": (
            "The worker asserts explicit rejection, no unexpected trade creation, terminal offer_request "
            "ledger state, and exact prefix cleanup compatibility."
        ),
    }


def delivery_contract_scenario_commands(
    record: dict[str, Any],
    *,
    prefix: str,
    artifact_dir: Path,
) -> dict[str, Any]:
    manifest_id = str(record.get("manifest_id") or "delivery_contract")
    source_scenario_id = str(record.get("source_scenario_id") or "")
    if not source_scenario_id:
        raise RunnerError(f"{manifest_id}: missing source_scenario_id")
    scenario_run_prefix = scenario_prefix(prefix, manifest_id)
    scenario_dir = artifact_dir / "scenarios" / safe_token(manifest_id)
    remote_dir = f"/tmp/{scenario_run_prefix}delivery-contract"
    commands = [
        host_mkdir_command(
            "ensure_delivery_contract_artifact_dir",
            server="foreign",
            path=remote_dir,
        ),
        container_python_command(
            "assert_delivery_contract_catalog_scenario",
            server="foreign",
            python_args=[
                "scripts/report_trade_notification_delivery_matrix.py",
                "--scenario",
                source_scenario_id,
                "--output",
                f"{remote_dir}/delivery-contract.result.json",
                "--check",
            ],
            timeout_seconds=60,
            mutates_production=False,
        ),
    ]
    return {
        "manifest_id": manifest_id,
        "status": "planned",
        "driver": "delivery_contract_catalog_assertion",
        "scenario_prefix": scenario_run_prefix,
        "artifact_dir": str(scenario_dir),
        "remote_artifact_dir": remote_dir,
        "source_scenario_id": source_scenario_id,
        "actor_pair_id": record.get("actor_pair_id"),
        "surface_pair": record.get("surface_pair"),
        "outage_id": record.get("outage_id"),
        "commands": [command_payload(command) for command in commands],
        "execution_groups": [
            {
                "name": "assert_delivery_contract_catalog",
                "mode": "sequential",
                "commands": [command_payload(command) for command in commands],
            },
        ],
        "safety_note": (
            "This driver is catalog-only and mutation-free. Runtime delivery evidence is produced by the "
            "targeted trade-delivery join driver for the same TDN scenario id."
        ),
    }


def targeted_join_scenario_commands(
    record: dict[str, Any],
    *,
    prefix: str,
    artifact_dir: Path,
) -> dict[str, Any]:
    manifest_id = str(record.get("manifest_id") or "targeted_join")
    source_scenario_id = str(record.get("source_scenario_id") or "")
    if not source_scenario_id:
        raise RunnerError(f"{manifest_id}: missing source_scenario_id")
    offer_home_server = str(record.get("offer_home_server") or "")
    if offer_home_server not in DUAL_ROLE_ROLE_BY_SERVER:
        raise RunnerError(f"{manifest_id}: unsupported offer_home_server={offer_home_server!r}")
    scenario_run_prefix = scenario_prefix(prefix, manifest_id)
    scenario_dir = artifact_dir / "scenarios" / safe_token(manifest_id)
    remote_dir = f"/tmp/{scenario_run_prefix}targeted-join"
    production_env = {
        EXECUTION_CONFIRM_ENV: EXECUTION_CONFIRM_VALUE,
        CLEANUP_CONFIRM_ENV: CLEANUP_CONFIRM_VALUE,
        "TRADING_BOT_SERVICE": "load_runner",
        "TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH": "1",
        "BOT_TOKEN": "",
    }
    cleanup_env = {
        CLEANUP_CONFIRM_ENV: CLEANUP_CONFIRM_VALUE,
        "TRADING_BOT_SERVICE": "load_runner",
        "BOT_TOKEN": "",
    }
    commands = [
        host_mkdir_command(
            f"ensure_{offer_home_server}_targeted_join_artifact_dir",
            server=offer_home_server,
            path=remote_dir,
        ),
        container_python_command(
            "run_targeted_trade_delivery_join_scenario",
            server=offer_home_server,
            python_args=[
                "scripts/run_trade_delivery_targeted_join_matrix.py",
                "--prefix",
                scenario_run_prefix,
                "--scenario",
                source_scenario_id,
                "--output",
                f"{remote_dir}/targeted-join.result.json",
                "--check",
                "--allow-any-branch",
                "--allow-production-execution",
                "--allow-production-cleanup",
            ],
            env=production_env,
            timeout_seconds=900,
        ),
        container_python_command(
            "cleanup_targeted_trade_delivery_join_scenario",
            server=offer_home_server,
            python_args=[
                "scripts/trading_core_probe_worker.py",
                "cleanup",
                "--prefix",
                scenario_run_prefix,
                "--allow-production-hard-delete",
                "--artifact",
                f"{remote_dir}/targeted-join.cleanup-post.json",
            ],
            env=cleanup_env,
            timeout_seconds=180,
        ),
    ]
    return {
        "manifest_id": manifest_id,
        "status": "planned",
        "driver": "targeted_trade_delivery_join_probe",
        "scenario_prefix": scenario_run_prefix,
        "artifact_dir": str(scenario_dir),
        "remote_artifact_dir": remote_dir,
        "source_scenario_id": source_scenario_id,
        "server": offer_home_server,
        "actor_pair_id": record.get("actor_pair_id"),
        "source_kind": record.get("source_kind"),
        "responder_kind": record.get("responder_kind"),
        "group_relation": record.get("group_relation"),
        "offer_surface": record.get("offer_surface"),
        "request_surface": record.get("request_surface"),
        "outage_id": record.get("outage_id"),
        "policy_supported": record.get("policy_supported"),
        "unsupported_reasons": record.get("unsupported_reasons") or [],
        "commands": [command_payload(command) for command in commands],
        "execution_groups": [
            {
                "name": "run_targeted_join",
                "mode": "sequential",
                "commands": [command_payload(command) for command in commands],
            },
        ],
        "safety_note": (
            "The targeted join probe creates only prefixed synthetic fixtures, injects a fake Telegram gateway, "
            "asserts receipt terminal states, and runs exact-prefix cleanup after the scenario."
        ),
    }


def outage_policy_scenario_commands(
    record: dict[str, Any],
    *,
    prefix: str,
    artifact_dir: Path,
    user_count: int,
    hot_offer_requests: int,
    target_rps: float,
    telegram_ratio: float,
) -> dict[str, Any]:
    manifest_id = str(record.get("manifest_id") or "outage_policy")
    outage_id = str(record.get("outage_id") or "")
    if outage_id not in {"short_under_2m", "medium_around_60m"}:
        raise RunnerError(f"{manifest_id}: outage policy driver only supports short/medium outage ids")
    delivery_scenario_id = delivery_scenario_id_for_record(record)
    dual_role_plan = dual_role_scenario_commands(
        record,
        prefix=prefix,
        artifact_dir=artifact_dir,
        user_count=user_count,
        hot_offer_requests=hot_offer_requests,
        target_rps=target_rps,
        telegram_ratio=telegram_ratio,
    )
    targeted_record = {
        "manifest_id": f"{manifest_id}_OUTAGE_POLICY_{delivery_scenario_id}",
        "source_scenario_id": delivery_scenario_id,
        "offer_home_server": record.get("offer_home_server"),
        "actor_pair_id": record.get("actor_pair_id"),
        "source_kind": record.get("source_kind"),
        "responder_kind": record.get("responder_kind"),
        "group_relation": record.get("group_relation"),
        "offer_surface": record.get("offer_surface"),
        "request_surface": record.get("request_surface"),
        "outage_id": outage_id,
        "policy_supported": record.get("policy_supported"),
        "unsupported_reasons": record.get("unsupported_reasons") or [],
    }
    targeted_plan = targeted_join_scenario_commands(
        targeted_record,
        prefix=prefix,
        artifact_dir=artifact_dir,
    )
    commands = [
        *[dict(command) for command in dual_role_plan.get("commands") or []],
        *[dict(command) for command in targeted_plan.get("commands") or []],
    ]
    execution_groups = [
        *[
            {
                **dict(group),
                "name": f"trade_correctness_{group.get('name')}",
            }
            for group in (dual_role_plan.get("execution_groups") or [])
        ],
        *[
            {
                **dict(group),
                "name": f"outage_delivery_policy_{group.get('name')}",
            }
            for group in (targeted_plan.get("execution_groups") or [])
        ],
    ]
    return {
        "manifest_id": manifest_id,
        "status": "planned",
        "driver": "outage_policy_composed_probe",
        "scenario_prefix": dual_role_plan.get("scenario_prefix"),
        "artifact_dir": dual_role_plan.get("artifact_dir"),
        "remote_artifact_dir": dual_role_plan.get("remote_artifact_dir"),
        "outage_id": outage_id,
        "delivery_scenario_id": delivery_scenario_id,
        "offer_home_server": record.get("offer_home_server"),
        "offer_surface": record.get("offer_surface"),
        "request_surface": record.get("request_surface"),
        "trade_correctness_driver": dual_role_plan.get("driver"),
        "outage_delivery_policy_driver": targeted_plan.get("driver"),
        "outage_delivery_policy_prefix": targeted_plan.get("scenario_prefix"),
        "commands": commands,
        "execution_groups": execution_groups,
        "component_plans": {
            "trade_correctness": dual_role_plan,
            "outage_delivery_policy": targeted_plan,
        },
        "safety_note": (
            "This composed driver does not cut production networking. It validates the trade/stress path with "
            "the dual-role production driver and validates short/medium delivery policy with the targeted "
            "join driver using a separate synthetic prefix and fake Telegram gateway."
        ),
    }


def customer_accountant_actor_scenario_commands(
    record: dict[str, Any],
    *,
    prefix: str,
    artifact_dir: Path,
    user_count: int,
    hot_offer_requests: int,
    target_rps: float,
    telegram_ratio: float,
) -> dict[str, Any]:
    manifest_id = str(record.get("manifest_id") or "customer_actor")
    delivery_scenario_id = delivery_scenario_id_for_record(record)
    actor_policy_record = {
        "manifest_id": f"{manifest_id}_ACTOR_POLICY_{delivery_scenario_id}",
        "source_scenario_id": delivery_scenario_id,
        "offer_home_server": record.get("offer_home_server"),
        "actor_pair_id": record.get("actor_pair_id"),
        "source_kind": record.get("source_kind"),
        "responder_kind": record.get("responder_kind"),
        "group_relation": record.get("group_relation"),
        "offer_surface": record.get("offer_surface"),
        "request_surface": record.get("request_surface"),
        "outage_id": record.get("outage_id"),
        "policy_supported": record.get("policy_supported"),
        "unsupported_reasons": record.get("unsupported_reasons") or [],
    }
    shape_record = {
        **record,
        "manifest_id": f"{manifest_id}_SHAPE_USER_STABLE",
        "actor_pair_id": "user__user",
        "source_kind": "user",
        "responder_kind": "user",
        "group_relation": "none",
        "outage_id": "stable",
        "policy_supported": True,
        "unsupported_reasons": [],
    }
    actor_policy_plan = targeted_join_scenario_commands(
        actor_policy_record,
        prefix=prefix,
        artifact_dir=artifact_dir,
    )
    shape_plan = dual_role_scenario_commands(
        shape_record,
        prefix=prefix,
        artifact_dir=artifact_dir,
        user_count=user_count,
        hot_offer_requests=hot_offer_requests,
        target_rps=target_rps,
        telegram_ratio=telegram_ratio,
    )
    commands = [
        *[dict(command) for command in actor_policy_plan.get("commands") or []],
        *[dict(command) for command in shape_plan.get("commands") or []],
    ]
    execution_groups = [
        *[
            {
                **dict(group),
                "name": f"actor_policy_{group.get('name')}",
            }
            for group in (actor_policy_plan.get("execution_groups") or [])
        ],
        *[
            {
                **dict(group),
                "name": f"shape_stress_{group.get('name')}",
            }
            for group in (shape_plan.get("execution_groups") or [])
        ],
    ]
    return {
        "manifest_id": manifest_id,
        "status": "planned",
        "driver": "customer_accountant_actor_composed_probe",
        "actor_pair_id": record.get("actor_pair_id"),
        "source_kind": record.get("source_kind"),
        "responder_kind": record.get("responder_kind"),
        "group_relation": record.get("group_relation"),
        "surface_pair": record.get("surface_pair"),
        "outage_id": record.get("outage_id"),
        "offer_type": record.get("offer_type"),
        "shape": record.get("shape"),
        "family": record.get("family"),
        "delivery_scenario_id": delivery_scenario_id,
        "actor_policy_driver": actor_policy_plan.get("driver"),
        "shape_stress_driver": shape_plan.get("driver"),
        "actor_policy_prefix": actor_policy_plan.get("scenario_prefix"),
        "shape_stress_prefix": shape_plan.get("scenario_prefix"),
        "commands": commands,
        "execution_groups": execution_groups,
        "safety_note": (
            "This composed driver validates customer/owner/accountant routing and counterparty privacy with "
            "the targeted join probe, then validates the requested shape/stress/surface behavior with a "
            "standard user-to-user dual-role probe. It is orthogonal coverage, not a single combined "
            "customer stress transaction."
        ),
    }


def unsupported_policy_scenario_commands(
    record: dict[str, Any],
    *,
    prefix: str,
    artifact_dir: Path,
) -> dict[str, Any]:
    manifest_id = str(record.get("manifest_id") or "unsupported_policy")
    scenario_run_prefix = scenario_prefix(prefix, manifest_id)
    scenario_dir = artifact_dir / "scenarios" / safe_token(manifest_id)
    remote_dir = f"/tmp/{scenario_run_prefix}unsupported-policy"
    offer_home_server = str(record.get("offer_home_server") or "")
    if offer_home_server not in DUAL_ROLE_ROLE_BY_SERVER:
        raise RunnerError(f"{manifest_id}: unsupported offer_home_server={offer_home_server!r}")
    profile = shape_profile(str(record.get("shape") or ""))
    reasons = [str(reason) for reason in (record.get("unsupported_reasons") or [])]
    unknown_reasons = sorted(set(reasons) - UNSUPPORTED_POLICY_EXECUTABLE_REASONS)
    if unknown_reasons:
        raise RunnerError(f"{manifest_id}: unsupported policy reasons are not executable: {unknown_reasons}")

    production_env = {
        EXECUTION_CONFIRM_ENV: EXECUTION_CONFIRM_VALUE,
        CLEANUP_CONFIRM_ENV: CLEANUP_CONFIRM_VALUE,
        "TRADING_BOT_SERVICE": "load_runner",
        "TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH": "1",
        "BOT_TOKEN": "",
    }
    command_servers: list[str] = []
    probe_commands: list[CommandSpec] = []
    for reason in reasons:
        if reason == "tier2_cannot_create_offer":
            server = offer_home_server
            command_prefix = f"{scenario_run_prefix}create_"
            output_name = "tier2_create.result.json"
        elif reason == "tier2_cannot_use_telegram_request":
            server = "foreign"
            command_prefix = f"{scenario_run_prefix}telegram_request_"
            output_name = "tier2_telegram_request.result.json"
        else:
            continue
        command_servers.append(server)
        python_args = [
            "scripts/trading_core_probe_worker.py",
            "run-unsupported-policy-case",
            "--prefix",
            command_prefix,
            "--actor-pair-id",
            str(record.get("actor_pair_id") or ""),
            "--source-kind",
            str(record.get("source_kind") or ""),
            "--responder-kind",
            str(record.get("responder_kind") or ""),
            "--group-relation",
            str(record.get("group_relation") or ""),
            "--offer-surface",
            str(record.get("offer_surface") or ""),
            "--request-surface",
            str(record.get("request_surface") or ""),
            "--offer-type",
            str(record.get("offer_type") or "sell"),
            "--quantity",
            str(profile["quantity"]),
            "--request-amount",
            str(profile["request_amount"]),
            "--price",
            "100000",
            "--unsupported-reason",
            reason,
            "--output",
            f"{remote_dir}/{output_name}",
            "--allow-production-execution",
            "--allow-production-cleanup",
        ]
        if not profile["is_wholesale"]:
            python_args.append("--retail")
            python_args.extend(["--lot-sizes", " ".join(str(item) for item in profile["lot_sizes"])])
        probe_commands.append(
            container_python_command(
                f"run_unsupported_policy_{safe_token(reason)}",
                server=server,
                python_args=python_args,
                env=production_env,
                timeout_seconds=360,
            )
        )

    mkdir_commands = [
        host_mkdir_command(
            f"ensure_{server}_unsupported_policy_artifact_dir",
            server=server,
            path=remote_dir,
        )
        for server in sorted(set(command_servers))
    ]
    commands = [*mkdir_commands, *probe_commands]
    return {
        "manifest_id": manifest_id,
        "status": "planned",
        "driver": "unsupported_policy_negative_probe",
        "scenario_prefix": scenario_run_prefix,
        "artifact_dir": str(scenario_dir),
        "remote_artifact_dir": remote_dir,
        "actor_pair_id": record.get("actor_pair_id"),
        "unsupported_reasons": reasons,
        "offer_home_server": offer_home_server,
        "offer_surface": record.get("offer_surface"),
        "request_surface": record.get("request_surface"),
        "shape_profile": profile,
        "commands": [command_payload(command) for command in commands],
        "execution_groups": [
            {
                "name": "prepare_artifact_dirs",
                "mode": "sequential",
                "commands": [command_payload(command) for command in mkdir_commands],
            },
            {
                "name": "run_unsupported_policy_probes",
                "mode": "sequential",
                "commands": [command_payload(command) for command in probe_commands],
            },
        ],
        "safety_note": (
            "Each unsupported policy probe asserts explicit rejection and zero partial offer/trade/request mutation "
            "for the exact prefixed synthetic cohort."
        ),
    }


def market_behavior_scenario_commands(
    record: dict[str, Any],
    *,
    prefix: str,
    artifact_dir: Path,
    user_count: int,
    attempts_per_scenario: int,
    target_rps: float,
    telegram_ratio: float,
) -> dict[str, Any]:
    manifest_id = str(record.get("manifest_id") or "market_behavior")
    source_scenario_id = str(record.get("source_scenario_id") or "")
    scenario_run_prefix = scenario_prefix(prefix, manifest_id)
    scenario_dir = artifact_dir / "scenarios" / safe_token(manifest_id)
    remote_dir = f"/tmp/{scenario_run_prefix}market-behavior"
    offer_origin = str(record.get("offer_origin") or "")
    request_surface = str(record.get("request_surface") or "")
    expire_surface = str(record.get("expire_surface") or "")
    family = str(record.get("family") or "")
    scenario_timeout_seconds = (
        MARKET_BEHAVIOR_HEAVY_TIMEOUT_SECONDS
        if family in MARKET_BEHAVIOR_HEAVY_FAMILIES
        else MARKET_BEHAVIOR_DEFAULT_TIMEOUT_SECONDS
    )
    scenario_write_max_concurrency = (
        MARKET_BEHAVIOR_HEAVY_WRITE_MAX_CONCURRENCY
        if family in MARKET_BEHAVIOR_HEAVY_FAMILIES
        else MARKET_BEHAVIOR_DEFAULT_WRITE_MAX_CONCURRENCY
    )
    server = "foreign" if "telegram" in {offer_origin, request_surface, expire_surface} or offer_origin == "bot" else "iran"
    production_env = {
        EXECUTION_CONFIRM_ENV: EXECUTION_CONFIRM_VALUE,
        CLEANUP_CONFIRM_ENV: CLEANUP_CONFIRM_VALUE,
        "TRADING_BOT_SERVICE": "load_runner",
        "TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH": "1",
        "DB_POOL_SIZE": str(MARKET_BEHAVIOR_LOAD_RUNNER_DB_POOL_SIZE),
        "DB_MAX_OVERFLOW": str(MARKET_BEHAVIOR_LOAD_RUNNER_DB_MAX_OVERFLOW),
        "BOT_TOKEN": "",
    }
    commands = [
        host_mkdir_command(
            f"ensure_{server}_market_behavior_artifact_dir",
            server=server,
            path=remote_dir,
        ),
        container_python_command(
            "run_market_behavior_scenario",
            server=server,
            python_args=[
                "scripts/run_bot_webapp_comprehensive_load_matrix.py",
                "--prefix",
                scenario_run_prefix,
                "--scenario",
                source_scenario_id,
                "--user-count",
                str(user_count),
                "--attempts-per-scenario",
                str(attempts_per_scenario),
                "--target-rps",
                str(target_rps),
                "--telegram-ratio",
                str(telegram_ratio),
                "--write-max-concurrency",
                str(scenario_write_max_concurrency),
                "--read-view-max-concurrency",
                str(MARKET_BEHAVIOR_READ_VIEW_MAX_CONCURRENCY),
                "--output",
                f"{remote_dir}/market-behavior.result.json",
                "--check",
                "--allow-production-execution",
                "--allow-production-cleanup",
            ],
            env=production_env,
            timeout_seconds=scenario_timeout_seconds,
        ),
    ]
    return {
        "manifest_id": manifest_id,
        "status": "planned",
        "driver": "market_behavior_comprehensive_probe",
        "scenario_prefix": scenario_run_prefix,
        "artifact_dir": str(scenario_dir),
        "remote_artifact_dir": remote_dir,
        "server": server,
        "source_scenario_id": source_scenario_id,
        "family": family,
        "market_behavior_timeout_seconds": scenario_timeout_seconds,
        "market_behavior_write_max_concurrency": scenario_write_max_concurrency,
        "market_behavior_read_view_max_concurrency": MARKET_BEHAVIOR_READ_VIEW_MAX_CONCURRENCY,
        "market_behavior_load_runner_db_pool": {
            "pool_size": MARKET_BEHAVIOR_LOAD_RUNNER_DB_POOL_SIZE,
            "max_overflow": MARKET_BEHAVIOR_LOAD_RUNNER_DB_MAX_OVERFLOW,
        },
        "offer_origin": record.get("offer_origin"),
        "request_surface": record.get("request_surface"),
        "expire_surface": record.get("expire_surface"),
        "terminal_state": record.get("terminal_state"),
        "commands": [command_payload(command) for command in commands],
        "execution_groups": [
            {
                "name": "run_market_behavior",
                "mode": "sequential",
                "commands": [command_payload(command) for command in commands],
            },
        ],
        "safety_note": (
            "The market behavior probe uses exact synthetic prefixes, production confirmation, cleanup confirmation, "
            "fake Telegram transport, and patched external side effects."
        ),
    }


def build_execution_plan(
    records: list[dict[str, Any]],
    *,
    prefix: str,
    artifact_dir: Path,
    user_count: int,
    hot_offer_requests: int,
    target_rps: float,
    telegram_ratio: float,
) -> dict[str, Any]:
    scenario_plans: list[dict[str, Any]] = []
    driver_gaps: list[dict[str, Any]] = []
    for record in records:
        gap = dual_role_driver_gap(record)
        if gap:
            bucket = driver_gap_bucket_for_reason(gap)
            driver_gaps.append(
                {
                    "manifest_id": record.get("manifest_id"),
                    "section": record.get("section"),
                    "driver_gap": gap,
                    "driver_gap_bucket": bucket,
                }
            )
            continue
        if record.get("section") == "market_behavior":
            scenario_plans.append(
                market_behavior_scenario_commands(
                    record,
                    prefix=prefix,
                    artifact_dir=artifact_dir,
                    user_count=user_count,
                    attempts_per_scenario=hot_offer_requests,
                    target_rps=target_rps,
                    telegram_ratio=telegram_ratio,
                )
            )
        elif record.get("section") == "delivery_contract":
            scenario_plans.append(
                delivery_contract_scenario_commands(
                    record,
                    prefix=prefix,
                    artifact_dir=artifact_dir,
                )
            )
        elif record.get("section") == "targeted_trade_delivery_join":
            scenario_plans.append(
                targeted_join_scenario_commands(
                    record,
                    prefix=prefix,
                    artifact_dir=artifact_dir,
                )
            )
        elif record.get("section") == "negative_business_guard":
            scenario_plans.append(
                negative_guard_scenario_commands(
                    record,
                    prefix=prefix,
                    artifact_dir=artifact_dir,
                )
            )
        elif record.get("section") == "production_base_trade_shape" and record.get("policy_supported") is False:
            scenario_plans.append(
                unsupported_policy_scenario_commands(
                    record,
                    prefix=prefix,
                    artifact_dir=artifact_dir,
                )
            )
        elif (
            record.get("section") in {"production_base_trade_shape", "production_stress_overlay"}
            and record.get("actor_pair_id") != "user__user"
        ):
            scenario_plans.append(
                customer_accountant_actor_scenario_commands(
                    record,
                    prefix=prefix,
                    artifact_dir=artifact_dir,
                    user_count=user_count,
                    hot_offer_requests=hot_offer_requests,
                    target_rps=target_rps,
                    telegram_ratio=telegram_ratio,
                )
            )
        elif (
            record.get("section") in {"production_base_trade_shape", "production_stress_overlay"}
            and record.get("outage_id") != "stable"
        ):
            scenario_plans.append(
                outage_policy_scenario_commands(
                    record,
                    prefix=prefix,
                    artifact_dir=artifact_dir,
                    user_count=user_count,
                    hot_offer_requests=hot_offer_requests,
                    target_rps=target_rps,
                    telegram_ratio=telegram_ratio,
                )
            )
        else:
            scenario_plans.append(
                dual_role_scenario_commands(
                    record,
                    prefix=prefix,
                    artifact_dir=artifact_dir,
                    user_count=user_count,
                    hot_offer_requests=hot_offer_requests,
                    target_rps=target_rps,
                    telegram_ratio=telegram_ratio,
                )
            )
    return {
        "status": "planned",
        "implemented_driver": "two_server_dual_role_hot_offer_and_negative_guard_webapp_iran_probe",
        "implemented_scope": {
            "sections": [
                "delivery_contract",
                "negative_business_guard",
                "production_base_trade_shape",
                "production_stress_overlay",
                "targeted_trade_delivery_join",
            ],
            "dual_role_actor_pair_id": "user__user",
            "dual_role_outage_id": "stable",
            "outage_policy_probe": "short_and_medium_user_to_user_composed_without_network_cut",
            "customer_accountant_actor_probe": "all_customer_accountant_actor_pairs_composed_orthogonal_coverage",
            "delivery_contract_catalog": "all_delivery_contract_scenarios",
            "targeted_join_scenarios": "all_targeted_join_scenarios_with_fake_telegram_gateway",
            "stress_families": sorted(DUAL_ROLE_EXECUTABLE_STRESS_FAMILIES),
            "duplicate_replay_request_surfaces": sorted(DUAL_ROLE_EXECUTABLE_DUPLICATE_REPLAY_REQUEST_SURFACES),
            "manual_expire_race_request_surfaces": sorted(
                DUAL_ROLE_EXECUTABLE_MANUAL_EXPIRE_RACE_REQUEST_SURFACES
            ),
            "time_expire_race_request_surfaces": sorted(DUAL_ROLE_EXECUTABLE_TIME_EXPIRE_RACE_REQUEST_SURFACES),
            "read_during_write_request_surfaces": sorted(DUAL_ROLE_EXECUTABLE_READ_DURING_WRITE_REQUEST_SURFACES),
            "unsupported_policy_reasons": sorted(UNSUPPORTED_POLICY_EXECUTABLE_REASONS),
            "surfaces": ["webapp", "telegram"],
            "offer_types": ["buy", "sell"],
            "shapes": sorted(manifest_builder.market_matrix.SHAPES),
            "negative_guard_cases": sorted(NEGATIVE_GUARD_EXECUTABLE_CASES),
        },
        "selected_count": len(records),
        "executable_count": len(scenario_plans),
        "driver_gap_count": len(driver_gaps),
        "driver_gap_summary": driver_gap_summary(driver_gaps),
        "driver_gap_roadmap": driver_gap_roadmap(driver_gaps),
        "scenario_plans": scenario_plans,
        "driver_gaps": driver_gaps,
        "safety": {
            "preserves_real_cross_server_forwarding": True,
            "role_workers_use_patch_external_side_effects": True,
            "requires_concurrent_role_start": True,
            "requires_production_isolation_before_execution": True,
            "requires_pre_and_post_cleanup": True,
        },
    }


def run_preflight_commands(commands: list[CommandSpec], *, cwd: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        try:
            completed = subprocess.run(
                command.args,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=command.timeout_seconds,
            )
            results.append(
                {
                    **command_payload(command),
                    "status": "passed" if completed.returncode == 0 else "failed",
                    "returncode": completed.returncode,
                    "stdout": truncate_text(completed.stdout),
                    "stderr": truncate_text(completed.stderr),
                }
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                {
                    **command_payload(command),
                    "status": "timeout",
                    "returncode": None,
                    "stdout": truncate_text(exc.stdout or ""),
                    "stderr": truncate_text(exc.stderr or ""),
                }
            )
            break
    return results


def command_spec_from_payload(payload: dict[str, Any]) -> CommandSpec:
    return CommandSpec(
        name=str(payload.get("name") or "command"),
        args=[str(item) for item in (payload.get("args") or [])],
        timeout_seconds=int(payload.get("timeout_seconds") or 30),
        mutates_production=bool(payload.get("mutates_production")),
    )


def command_result(command: CommandSpec, *, cwd: Path) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command.args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=command.timeout_seconds,
        )
        status = "passed" if completed.returncode == 0 else "failed"
        return {
            **command_payload(command),
            "status": status,
            "returncode": completed.returncode,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout": truncate_text(completed.stdout),
            "stderr": truncate_text(completed.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            **command_payload(command),
            "status": "timeout",
            "returncode": None,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout": truncate_text(exc.stdout or ""),
            "stderr": truncate_text(exc.stderr or ""),
        }


def concurrent_command_results(commands: list[CommandSpec], *, cwd: Path) -> list[dict[str, Any]]:
    started_by_name: dict[str, float] = {}
    processes: list[tuple[CommandSpec, subprocess.Popen[str]]] = []
    for command in commands:
        started_by_name[command.name] = time.perf_counter()
        processes.append(
            (
                command,
                subprocess.Popen(
                    command.args,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ),
            )
        )

    results: list[dict[str, Any]] = []
    for command, process in processes:
        try:
            stdout, stderr = process.communicate(timeout=command.timeout_seconds)
            status = "passed" if process.returncode == 0 else "failed"
            results.append(
                {
                    **command_payload(command),
                    "status": status,
                    "returncode": process.returncode,
                    "elapsed_seconds": round(time.perf_counter() - started_by_name[command.name], 3),
                    "stdout": truncate_text(stdout or ""),
                    "stderr": truncate_text(stderr or ""),
                }
            )
        except subprocess.TimeoutExpired as exc:
            process.kill()
            stdout, stderr = process.communicate()
            combined_stdout = normalize_command_output(exc.stdout) + normalize_command_output(stdout)
            combined_stderr = normalize_command_output(exc.stderr) + normalize_command_output(stderr)
            results.append(
                {
                    **command_payload(command),
                    "status": "timeout",
                    "returncode": None,
                    "elapsed_seconds": round(time.perf_counter() - started_by_name[command.name], 3),
                    "stdout": truncate_text(combined_stdout),
                    "stderr": truncate_text(combined_stderr),
                }
            )
    return results


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def load_latest_scenario_results(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                latest[f"__invalid_jsonl_line_{line_number}"] = {
                    "event": "invalid_jsonl_line",
                    "line_number": line_number,
                    "status": "failed",
                }
                continue
            if payload.get("event") != "scenario_result":
                continue
            manifest_id = str(payload.get("manifest_id") or "")
            if not manifest_id:
                continue
            latest[manifest_id] = payload
    return latest


def scenario_manifest_id(scenario_plan: dict[str, Any], *, index: int) -> str:
    return str(scenario_plan.get("manifest_id") or f"scenario_{index}")


def iter_scenario_command_results(result: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for group in result.get("groups") or []:
        for command in group.get("commands") or []:
            if isinstance(command, dict):
                yield command


def is_retryable_scenario_result(result: dict[str, Any]) -> bool:
    if result.get("status") == "passed":
        return False
    for command in iter_scenario_command_results(result):
        if command.get("status") == "timeout":
            return True
    result_text = json.dumps(result, ensure_ascii=False, default=str).lower()
    return any(token.lower() in result_text for token in RETRYABLE_SCENARIO_TOKENS)


def build_campaign_ledger(
    scenario_plans: list[dict[str, Any]],
    latest_results: dict[str, dict[str, Any]],
    *,
    prefix: str | None,
    results_path: Path,
    resume_enabled: bool,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    scenario_records: list[dict[str, Any]] = []
    invalid_result_count = sum(1 for key in latest_results if key.startswith("__invalid_jsonl_line_"))
    for index, scenario_plan in enumerate(scenario_plans, start=1):
        manifest_id = scenario_manifest_id(scenario_plan, index=index)
        result = latest_results.get(manifest_id)
        status = str((result or {}).get("status") or "pending")
        counts[status] += 1
        scenario_records.append(
            {
                "index": index,
                "manifest_id": manifest_id,
                "driver": scenario_plan.get("driver"),
                "status": status,
                "attempt": (result or {}).get("attempt"),
                "elapsed_seconds": (result or {}).get("elapsed_seconds"),
            }
        )
    return {
        "schema_version": CAMPAIGN_LEDGER_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "prefix": prefix,
        "results_path": str(results_path),
        "resume_enabled": resume_enabled,
        "scenario_total": len(scenario_plans),
        "counts": dict(sorted(counts.items())),
        "invalid_result_count": invalid_result_count,
        "scenarios": scenario_records,
    }


def write_campaign_ledger(
    ledger_path: Path,
    scenario_plans: list[dict[str, Any]],
    latest_results: dict[str, dict[str, Any]],
    *,
    prefix: str | None,
    results_path: Path,
    resume_enabled: bool,
) -> None:
    write_json(
        ledger_path,
        build_campaign_ledger(
            scenario_plans,
            latest_results,
            prefix=prefix,
            results_path=results_path,
            resume_enabled=resume_enabled,
        ),
    )


def execute_scenario_plan(
    scenario_plan: dict[str, Any],
    *,
    index: int,
    total: int,
    cwd: Path,
) -> dict[str, Any]:
    manifest_id = str(scenario_plan.get("manifest_id") or f"scenario_{index}")
    started = time.perf_counter()
    group_results: list[dict[str, Any]] = []
    failed = False
    for group in scenario_plan.get("execution_groups") or []:
        commands = [command_spec_from_payload(item) for item in (group.get("commands") or [])]
        mode = str(group.get("mode") or "sequential")
        if mode == "concurrent":
            command_results = concurrent_command_results(commands, cwd=cwd)
        else:
            command_results = []
            for command in commands:
                result = command_result(command, cwd=cwd)
                command_results.append(result)
                if result.get("status") != "passed":
                    break
        group_failed = any(item.get("status") != "passed" for item in command_results)
        group_results.append(
            {
                "name": group.get("name"),
                "mode": mode,
                "status": "failed" if group_failed else "passed",
                "commands": command_results,
            }
        )
        if group_failed:
            failed = True
            break
    status = "failed" if failed else "passed"
    return {
        "manifest_id": manifest_id,
        "driver": scenario_plan.get("driver"),
        "index": index,
        "total": total,
        "status": status,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "groups": group_results,
    }


def execute_command_plan(
    plan: dict[str, Any],
    *,
    cwd: Path,
    resume: bool = False,
    continue_on_failure: bool = False,
    scenario_retries: int = 0,
    retry_backoff_seconds: float = 15.0,
    retry_mode: str = "transient",
) -> tuple[dict[str, Any], int]:
    execution_plan = plan.get("execution_plan") or {}
    artifact_dir = Path((plan.get("preflight") or {}).get("artifact_dir") or "/tmp/trading-bot-production-full-matrix")
    results_path = artifact_dir / "execution-results.jsonl"
    ledger_path = artifact_dir / "campaign-ledger.json"
    scenario_plans = list(execution_plan.get("scenario_plans") or [])
    scenario_retries = max(0, int(scenario_retries))
    retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
    if os.environ.get(EXECUTION_CONFIRM_ENV) != EXECUTION_CONFIRM_VALUE:
        execution_plan["execution"] = {
            "status": "blocked",
            "reason": "confirmation_missing",
            "required_env": EXECUTION_CONFIRM_ENV,
            "required_value": EXECUTION_CONFIRM_VALUE,
        }
        plan["status"] = "blocked_execution_confirmation_missing"
        return plan, 2
    if (execution_plan.get("coverage_gate") or {}).get("passed") is False:
        execution_plan["execution"] = {
            "status": "blocked",
            "reason": "driver_gaps_remaining",
            "driver_gap_count": execution_plan.get("driver_gap_count"),
        }
        plan["status"] = "blocked_driver_gaps"
        return plan, 2

    preflight_commands = [
        command_spec_from_payload(item)
        for item in (plan.get("preflight") or {}).get("commands") or []
    ]
    preflight_results = run_preflight_commands(preflight_commands, cwd=cwd)
    preflight_failed = [item for item in preflight_results if item.get("status") != "passed"]
    plan["preflight"]["status"] = "preflight_failed" if preflight_failed else "preflight_passed"
    plan["preflight"]["results"] = preflight_results
    if preflight_failed:
        execution_plan["execution"] = {
            "status": "blocked",
            "reason": "preflight_failed",
            "results_path": str(results_path),
        }
        plan["status"] = "preflight_failed"
        return plan, 1

    results_path.parent.mkdir(parents=True, exist_ok=True)
    latest_results = load_latest_scenario_results(results_path) if resume else {}
    if not resume and results_path.exists():
        results_path.unlink()
        latest_results = {}
    append_jsonl(
        results_path,
        {
            "event": "execution_resumed" if resume else "execution_started",
            "generated_at": utc_now_iso(),
            "prefix": plan.get("prefix"),
            "scenario_count": len(scenario_plans),
            "resume": resume,
            "continue_on_failure": continue_on_failure,
            "scenario_retries": scenario_retries,
            "retry_backoff_seconds": retry_backoff_seconds,
            "retry_mode": retry_mode,
            "previous_passed_count": sum(1 for result in latest_results.values() if result.get("status") == "passed"),
            "previous_result_count": sum(1 for key in latest_results if not key.startswith("__invalid_jsonl_line_")),
        },
    )
    write_campaign_ledger(
        ledger_path,
        scenario_plans,
        latest_results,
        prefix=plan.get("prefix"),
        results_path=results_path,
        resume_enabled=resume,
    )

    passed = 0
    skipped_passed = 0
    failed_results: list[dict[str, Any]] = []
    for index, scenario_plan in enumerate(scenario_plans, start=1):
        manifest_id = scenario_manifest_id(scenario_plan, index=index)
        previous_result = latest_results.get(manifest_id)
        if resume and previous_result and previous_result.get("status") == "passed":
            passed += 1
            skipped_passed += 1
            append_jsonl(
                results_path,
                {
                    "event": "scenario_skipped",
                    "generated_at": utc_now_iso(),
                    "reason": "already_passed",
                    "index": index,
                    "total": len(scenario_plans),
                    "manifest_id": manifest_id,
                    "driver": scenario_plan.get("driver"),
                },
            )
            continue

        final_result: dict[str, Any] | None = None
        for attempt in range(1, scenario_retries + 2):
            print(
                json.dumps(
                    {
                        "event": "scenario_started",
                        "index": index,
                        "total": len(scenario_plans),
                        "manifest_id": manifest_id,
                        "driver": scenario_plan.get("driver"),
                        "attempt": attempt,
                        "max_attempts": scenario_retries + 1,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
            result = execute_scenario_plan(
                scenario_plan,
                index=index,
                total=len(scenario_plans),
                cwd=cwd,
            )
            retryable = is_retryable_scenario_result(result)
            result = {
                **result,
                "attempt": attempt,
                "max_attempts": scenario_retries + 1,
                "retryable": retryable,
                "retry_mode": retry_mode,
            }
            append_jsonl(results_path, {"event": "scenario_result", **result})
            latest_results[manifest_id] = {"event": "scenario_result", **result}
            write_campaign_ledger(
                ledger_path,
                scenario_plans,
                latest_results,
                prefix=plan.get("prefix"),
                results_path=results_path,
                resume_enabled=resume,
            )
            final_result = result
            if result["status"] == "passed":
                passed += 1
                break
            should_retry = attempt <= scenario_retries and (
                retry_mode == "all" or (retry_mode == "transient" and retryable)
            )
            if not should_retry:
                break
            append_jsonl(
                results_path,
                {
                    "event": "scenario_retry_scheduled",
                    "generated_at": utc_now_iso(),
                    "index": index,
                    "total": len(scenario_plans),
                    "manifest_id": manifest_id,
                    "driver": scenario_plan.get("driver"),
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "retry_backoff_seconds": retry_backoff_seconds,
                    "retry_mode": retry_mode,
                    "retryable": retryable,
                },
            )
            if retry_backoff_seconds:
                time.sleep(retry_backoff_seconds)

        if final_result and final_result["status"] != "passed":
            failed_results.append(final_result)
            if not continue_on_failure:
                break

    status = "execution_passed" if not failed_results and passed == len(scenario_plans) else "execution_failed"
    execution_plan["execution"] = {
        "status": status,
        "results_path": str(results_path),
        "ledger_path": str(ledger_path),
        "scenario_total": len(scenario_plans),
        "scenario_passed": passed,
        "scenario_skipped_passed": skipped_passed,
        "scenario_failed": len(failed_results),
        "failed_manifest_id": (failed_results[0] if failed_results else {}).get("manifest_id"),
        "failed_manifest_ids": [str(item.get("manifest_id")) for item in failed_results],
        "resume": resume,
        "continue_on_failure": continue_on_failure,
        "scenario_retries": scenario_retries,
        "retry_backoff_seconds": retry_backoff_seconds,
        "retry_mode": retry_mode,
    }
    plan["status"] = status
    plan["mutates_production"] = True
    return plan, 0 if status == "execution_passed" else 1


def load_manifest(args: argparse.Namespace) -> dict[str, Any]:
    if args.manifest:
        manifest = read_json(args.manifest)
    else:
        manifest = manifest_builder.build_manifest(prefix=args.prefix)
    errors = manifest_builder.validate_manifest(manifest)
    if errors:
        raise RunnerError("; ".join(errors))
    manifest_builder.validate_prefix(str(manifest.get("prefix") or ""))
    return manifest


def section_records(manifest: dict[str, Any], sections: set[str]) -> list[dict[str, Any]]:
    available_sections = manifest.get("sections") or {}
    unknown = sorted(sections.difference(available_sections))
    if unknown:
        raise RunnerError(f"unknown manifest section(s): {', '.join(unknown)}")
    selected_sections = sections or set(available_sections)
    records: list[dict[str, Any]] = []
    for section_name in sorted(selected_sections):
        for record in available_sections.get(section_name) or []:
            item = dict(record)
            item["section"] = section_name
            records.append(item)
    return records


def matches_optional(value: Any, allowed: set[str]) -> bool:
    return not allowed or str(value) in allowed


def filter_records(records: Iterable[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    manifest_ids = set(args.manifest_id or [])
    outage_ids = set(args.outage_id or [])
    surface_pairs = set(args.surface_pair or [])
    actor_pair_ids = set(args.actor_pair_id or [])
    offer_types = set(args.offer_type or [])
    shapes = set(args.shape or [])

    selected: list[dict[str, Any]] = []
    for record in records:
        if manifest_ids and record.get("manifest_id") not in manifest_ids:
            continue
        if not matches_optional(record.get("outage_id"), outage_ids):
            continue
        if not matches_optional(record.get("surface_pair"), surface_pairs):
            continue
        if not matches_optional(record.get("actor_pair_id"), actor_pair_ids):
            continue
        if not matches_optional(record.get("offer_type"), offer_types):
            continue
        if not matches_optional(record.get("shape"), shapes):
            continue
        policy_supported = record.get("policy_supported")
        if args.policy == "supported" and policy_supported is not True:
            continue
        if args.policy == "unsupported" and policy_supported is not False:
            continue
        selected.append(record)

    if args.shard_count < 1:
        raise RunnerError("--shard-count must be >= 1")
    if args.shard_index < 1 or args.shard_index > args.shard_count:
        raise RunnerError("--shard-index must be between 1 and --shard-count")
    if args.shard_count > 1:
        selected = [
            record
            for index, record in enumerate(selected, start=1)
            if ((index - 1) % args.shard_count) + 1 == args.shard_index
        ]

    if args.max_scenarios is not None:
        selected = selected[: max(0, int(args.max_scenarios))]
    return selected


def count_by(records: Iterable[dict[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for record in records:
        value = record.get(key)
        if value is None:
            value = "none"
        counter[str(value)] += 1
    return dict(sorted(counter.items()))


def driver_gap_summary(driver_gaps: list[dict[str, Any]]) -> dict[str, Any]:
    by_section: Counter[str] = Counter()
    by_driver_gap: Counter[str] = Counter()
    by_driver_gap_bucket: Counter[str] = Counter()
    by_section_and_gap: Counter[tuple[str, str]] = Counter()
    examples_by_gap: dict[str, list[str]] = {}

    for gap_record in driver_gaps:
        section = str(gap_record.get("section") or "unknown")
        reason = str(gap_record.get("driver_gap") or "unknown")
        bucket = str(gap_record.get("driver_gap_bucket") or driver_gap_bucket_for_reason(reason))
        manifest_id = str(gap_record.get("manifest_id") or "")
        by_section[section] += 1
        by_driver_gap[reason] += 1
        by_driver_gap_bucket[bucket] += 1
        by_section_and_gap[(section, reason)] += 1
        if manifest_id:
            examples = examples_by_gap.setdefault(reason, [])
            if len(examples) < 5:
                examples.append(manifest_id)

    return {
        "total": len(driver_gaps),
        "by_section": dict(sorted(by_section.items())),
        "by_driver_gap": dict(sorted(by_driver_gap.items())),
        "by_driver_gap_bucket": dict(sorted(by_driver_gap_bucket.items())),
        "by_section_and_driver_gap": [
            {"section": section, "driver_gap": reason, "count": count}
            for (section, reason), count in sorted(by_section_and_gap.items())
        ],
        "example_manifest_ids_by_driver_gap": dict(sorted(examples_by_gap.items())),
    }


def driver_gap_roadmap(driver_gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = driver_gap_summary(driver_gaps)
    bucket_counts = summary["by_driver_gap_bucket"]
    by_reason = summary["by_driver_gap"]
    roadmap: list[dict[str, Any]] = []
    for bucket, definition in sorted(DRIVER_GAP_BUCKETS.items(), key=lambda item: int(item[1]["order"])):
        count = int(bucket_counts.get(bucket, 0) or 0)
        if count <= 0:
            continue
        reasons = sorted(
            reason
            for reason in by_reason
            if driver_gap_bucket_for_reason(reason) == bucket
        )
        roadmap.append(
            {
                "bucket": bucket,
                "order": int(definition["order"]),
                "title": definition["title"],
                "difficulty": definition["difficulty"],
                "remaining_gap_count": count,
                "driver_gap_reasons": reasons,
                "implementation_note": definition["implementation_note"],
            }
        )
    return roadmap


def selected_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    policy_counter: Counter[str] = Counter()
    for record in records:
        value = record.get("policy_supported")
        if value is True:
            policy_counter["supported"] += 1
        elif value is False:
            policy_counter["unsupported"] += 1
        else:
            policy_counter["not_applicable"] += 1
    return {
        "selected_count": len(records),
        "by_section": count_by(records, "section"),
        "by_kind": count_by(records, "kind"),
        "by_quadrant": count_by(records, "quadrant"),
        "by_surface_pair": count_by(records, "surface_pair"),
        "by_outage": count_by(records, "outage_id"),
        "by_offer_type": count_by(records, "offer_type"),
        "by_shape": count_by(records, "shape"),
        "by_policy": dict(sorted(policy_counter.items())),
    }


def planned_steps(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for order, record in enumerate(records, start=1):
        section = str(record["section"])
        steps.append(
            {
                "order": order,
                "manifest_id": record.get("manifest_id"),
                "section": section,
                "kind": record.get("kind"),
                "driver_status": SECTION_DRIVER_STATUS.get(section, "pending_driver"),
                "policy_supported": record.get("policy_supported"),
                "expected_outcome": record.get("expected_outcome"),
                "assertion_refs": list(record.get("assertion_refs") or []),
                "scenario_ref": {
                    "actor_pair_id": record.get("actor_pair_id"),
                    "surface_pair": record.get("surface_pair"),
                    "quadrant": record.get("quadrant"),
                    "outage_id": record.get("outage_id"),
                    "offer_type": record.get("offer_type"),
                    "shape": record.get("shape"),
                    "family": record.get("family"),
                    "case_id": record.get("case_id"),
                    "base_manifest_id": record.get("base_manifest_id"),
                },
            }
        )
    return steps


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_manifest(args)
    prefix = str(manifest.get("prefix"))
    sections = set(args.section or [])
    records = section_records(manifest, sections)
    selected = filter_records(records, args)
    steps = planned_steps(selected)
    status = "planned"
    artifact_dir = Path(args.artifact_dir or (Path("/tmp/trading-bot-production-full-matrix") / prefix))
    preflight_commands = build_preflight_commands(prefix=prefix, artifact_dir=artifact_dir)
    if args.mode == "preflight":
        status = "preflight_execution_requested" if args.execute else "preflight_planned"
    elif args.mode == "execution-plan":
        status = "execution_requested" if args.execute else "execution_plan_built"
    elif args.execute:
        status = "blocked_not_implemented"
    execution_plan = None
    if args.mode == "execution-plan":
        execution_plan = build_execution_plan(
            selected,
            prefix=prefix,
            artifact_dir=artifact_dir,
            user_count=args.users,
            hot_offer_requests=args.hot_offer_requests,
            target_rps=args.target_rps,
            telegram_ratio=args.telegram_ratio,
        )
        driver_gap_count = int(execution_plan.get("driver_gap_count") or 0)
        coverage_passed = driver_gap_count == 0
        execution_plan["coverage_gate"] = {
            "required": bool(args.require_full_driver_coverage),
            "passed": coverage_passed,
            "reason": None
            if coverage_passed
            else "selected scenarios still have unimplemented production drivers",
        }
        if args.require_full_driver_coverage and not coverage_passed:
            status = "blocked_driver_gaps"
            execution_plan["status"] = "blocked_driver_gaps"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "environment": "production",
        "mutates_production": False,
        "mode": args.mode,
        "execute_requested": bool(args.execute),
        "status": status,
        "prefix": prefix,
        "manifest": {
            "schema_version": manifest.get("schema_version"),
            "summary": manifest.get("summary"),
            "source": str(args.manifest) if args.manifest else "generated",
        },
        "selection": {
            "sections": sorted(sections) if sections else "all",
            "manifest_ids": args.manifest_id or [],
            "policy": args.policy,
            "outage_ids": args.outage_id or [],
            "surface_pairs": args.surface_pair or [],
            "actor_pair_ids": args.actor_pair_id or [],
            "offer_types": args.offer_type or [],
            "shapes": args.shape or [],
            "max_scenarios": args.max_scenarios,
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
        },
        "selected_summary": selected_summary(selected),
        "execution_contract": {
            "requires_isolation": True,
            "requires_exact_prefix_cleanup": True,
            "requires_two_server_execution": True,
            "confirmation_env": EXECUTION_CONFIRM_ENV,
            "confirmation_value": EXECUTION_CONFIRM_VALUE,
            "preflight_confirmation_env": PREFLIGHT_CONFIRM_ENV,
            "preflight_confirmation_value": PREFLIGHT_CONFIRM_VALUE,
            "production_drivers_implemented": True,
            "execution_command_plan_implemented": True,
            "execution_driver_implemented": True,
            "preflight_driver_implemented": True,
            "full_driver_coverage_gate_available": True,
            "full_driver_coverage_required": bool(args.require_full_driver_coverage),
            "reason": (
                "This runner can execute live non-mutating preflight and can build guarded production "
                "execution command plans. In execution-plan mode, --execute runs the command plan only "
                "when the explicit production confirmation environment variable is present."
            ),
        },
        "preflight": {
            "artifact_dir": str(artifact_dir),
            "status": "planned",
            "commands": [command_payload(command) for command in preflight_commands],
        },
        "execution_plan": execution_plan,
        "steps": steps,
    }


def compact_stdout(plan: dict[str, Any], output: Path | None) -> dict[str, Any]:
    return {
        "schema_version": plan["schema_version"],
        "status": plan["status"],
        "mode": plan["mode"],
        "prefix": plan["prefix"],
        "output": str(output) if output else None,
        "execute_requested": plan["execute_requested"],
        "selected_summary": plan["selected_summary"],
        "preflight": {
            "status": (plan.get("preflight") or {}).get("status"),
            "command_count": len((plan.get("preflight") or {}).get("commands") or []),
        },
        "execution_plan": {
            "status": (plan.get("execution_plan") or {}).get("status"),
            "executable_count": (plan.get("execution_plan") or {}).get("executable_count"),
            "driver_gap_count": (plan.get("execution_plan") or {}).get("driver_gap_count"),
            "coverage_gate": (plan.get("execution_plan") or {}).get("coverage_gate"),
            "driver_gap_summary": {
                "by_section": ((plan.get("execution_plan") or {}).get("driver_gap_summary") or {}).get(
                    "by_section", {}
                ),
                "by_driver_gap": ((plan.get("execution_plan") or {}).get("driver_gap_summary") or {}).get(
                    "by_driver_gap", {}
                ),
                "by_driver_gap_bucket": ((plan.get("execution_plan") or {}).get("driver_gap_summary") or {}).get(
                    "by_driver_gap_bucket", {}
                ),
            },
            "driver_gap_roadmap": (plan.get("execution_plan") or {}).get("driver_gap_roadmap"),
            "execution": (plan.get("execution_plan") or {}).get("execution"),
        }
        if plan.get("execution_plan") is not None
        else None,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="Existing manifest JSON. If omitted, a manifest is generated.")
    parser.add_argument("--prefix", default=manifest_builder.default_prefix())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument(
        "--mode",
        choices=["plan", "preflight", "execution-plan"],
        default="plan",
        help=(
            "plan builds scenario steps; preflight builds/runs non-mutating live readiness checks; "
            "execution-plan builds guarded two-server command plans without executing them."
        ),
    )
    parser.add_argument("--section", action="append", default=[])
    parser.add_argument("--manifest-id", action="append", default=[])
    parser.add_argument("--policy", choices=["all", "supported", "unsupported"], default="all")
    parser.add_argument("--outage-id", action="append", default=[])
    parser.add_argument("--surface-pair", action="append", default=[])
    parser.add_argument("--actor-pair-id", action="append", default=[])
    parser.add_argument("--offer-type", action="append", choices=["buy", "sell"], default=[])
    parser.add_argument("--shape", action="append", default=[])
    parser.add_argument("--max-scenarios", type=int)
    parser.add_argument("--shard-index", type=int, default=1)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--hot-offer-requests", type=int, default=1000)
    parser.add_argument("--target-rps", type=float, default=600.0)
    parser.add_argument("--telegram-ratio", type=float, default=0.6)
    parser.add_argument(
        "--require-full-driver-coverage",
        action="store_true",
        help=(
            "In execution-plan mode, exit with code 2 unless every selected manifest row has an "
            "implemented production command driver."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Execute the selected mode. Preflight is non-mutating. Execution-plan mode mutates production "
            "only with the explicit production confirmation env."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="In execution-plan --execute mode, keep existing results and skip scenarios already marked passed.",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="In execution-plan --execute mode, keep running after a scenario reaches final failed status.",
    )
    parser.add_argument(
        "--scenario-retries",
        type=int,
        default=0,
        help="Retry a failed scenario this many times before recording final failure.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=15.0,
        help="Seconds to wait between scenario retries.",
    )
    parser.add_argument(
        "--retry-mode",
        choices=["transient", "all"],
        default="transient",
        help="Retry only detected transient failures, or every failed scenario.",
    )
    parser.add_argument("--print-full", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_plan(args)
    except RunnerError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1

    exit_code = 0
    if args.execute and args.mode == "preflight":
        if os.environ.get(PREFLIGHT_CONFIRM_ENV) != PREFLIGHT_CONFIRM_VALUE:
            plan["status"] = "blocked_preflight_confirmation_missing"
            plan["preflight"]["status"] = "blocked_confirmation_missing"
            exit_code = 2
        else:
            commands = [
                CommandSpec(
                    name=item["name"],
                    args=list(item["args"]),
                    timeout_seconds=int(item["timeout_seconds"]),
                    mutates_production=bool(item["mutates_production"]),
                )
                for item in plan["preflight"]["commands"]
            ]
            results = run_preflight_commands(commands, cwd=REPO_ROOT)
            failed = [item for item in results if item.get("status") != "passed"]
            plan["status"] = "preflight_failed" if failed else "preflight_passed"
            plan["preflight"]["status"] = plan["status"]
            plan["preflight"]["results"] = results
            exit_code = 1 if failed else 0
    elif args.execute and args.mode == "execution-plan":
        plan, exit_code = execute_command_plan(
            plan,
            cwd=REPO_ROOT,
            resume=args.resume,
            continue_on_failure=args.continue_on_failure,
            scenario_retries=args.scenario_retries,
            retry_backoff_seconds=args.retry_backoff_seconds,
            retry_mode=args.retry_mode,
        )
    elif args.execute:
        exit_code = 2
    if args.mode == "execution-plan" and args.require_full_driver_coverage and plan["status"] == "blocked_driver_gaps":
        exit_code = 2

    if args.output:
        write_json(args.output, plan)
    stdout_payload = plan if args.print_full else compact_stdout(plan, args.output)
    print(json.dumps(stdout_payload, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
