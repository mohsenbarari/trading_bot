#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


TRADE_DELIVERY_STAGING_REPORT_SCHEMA_VERSION = "trade_delivery_staging_validation_report_v1"
PRODUCTION_GATE_BLOCKED_STATUS = "blocked_until_owner_staging_validation"
STABLE_LATENCY_TARGET_SECONDS = 2.0
DEFAULT_USER_COUNT = 1000
DEFAULT_TARGET_RPS = 600.0
DEFAULT_TELEGRAM_RATIO = 0.6

REQUIRED_DIMENSIONS = ("role", "link_state", "server", "channel", "outage", "concurrency")
REQUIRED_LOG_CLASSES = ("app", "bot", "sync", "db", "worker")
REQUIRED_RECEIPT_STATUSES = (
    "pending",
    "processing",
    "retry_pending",
    "sent",
    "skipped",
    "not_required",
    "permanent_failed",
)


class TradeDeliveryStagingValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CoverageRef:
    path: str
    symbol: str


@dataclass(frozen=True)
class ValidationScenario:
    scenario_id: str
    title: str
    dimensions: tuple[str, ...]
    coverage_refs: tuple[CoverageRef, ...]
    required_staging_evidence: tuple[str, ...] = ()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TradeDeliveryStagingValidationError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TradeDeliveryStagingValidationError(f"{path} must contain a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_validation_matrix() -> list[ValidationScenario]:
    """Stage 11 delivery-specific matrix tied to existing deterministic tests.

    The broader Bot/WebApp load matrix covers the market behavior space. This
    matrix is narrower and intentionally delivery-centric: it documents the
    evidence that must exist before staging can be accepted for trade message
    delivery.
    """
    return [
        ValidationScenario(
            scenario_id="TDV-001",
            title="Direct linked users receive required WebApp and Telegram delivery for Iran-home trades",
            dimensions=("role:standard", "link_state:linked", "server:iran", "channel:both", "outage:stable"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_notification_audience_service.py",
                    "test_direct_user_trade_requires_webapp_and_linked_telegram_for_both_sides",
                ),
                CoverageRef(
                    "tests/test_trade_webapp_delivery_service.py",
                    "test_iran_webapp_delivery_creates_receipt_notification_and_publishes_after_commit",
                ),
                CoverageRef(
                    "tests/test_trade_telegram_delivery_service.py",
                    "test_success_marks_receipt_sent_with_telegram_message_id",
                ),
            ),
            required_staging_evidence=("receipt_metrics", "latency_distribution", "sanitized_logs"),
        ),
        ValidationScenario(
            scenario_id="TDV-002",
            title="Offer home server does not change audience rules for foreign-home trades",
            dimensions=("role:standard", "link_state:linked", "server:foreign", "channel:both", "outage:stable"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_notification_audience_service.py",
                    "test_offer_home_server_does_not_change_audience_rules",
                ),
                CoverageRef(
                    "tests/test_trade_telegram_delivery_service.py",
                    "test_worker_claims_only_telegram_receipts_on_foreign",
                ),
                CoverageRef(
                    "tests/test_trade_webapp_delivery_service.py",
                    "test_foreign_server_only_queues_iran_owned_webapp_receipt",
                ),
            ),
            required_staging_evidence=("receipt_metrics", "latency_distribution", "sanitized_logs"),
        ),
        ValidationScenario(
            scenario_id="TDV-003",
            title="Unlinked eligible users receive WebApp delivery and no Telegram backlog",
            dimensions=("role:standard", "link_state:unlinked", "server:any", "channel:webapp", "outage:stable"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_delivery_receipt_service.py",
                    "test_upsert_creates_pending_or_not_required_receipts_by_identity",
                ),
                CoverageRef(
                    "tests/test_trade_telegram_delivery_service.py",
                    "test_repair_queues_only_foreign_owned_telegram_receipts_from_audience",
                ),
            ),
            required_staging_evidence=("receipt_metrics", "sanitized_logs"),
        ),
        ValidationScenario(
            scenario_id="TDV-004",
            title="Accountant monitoring recipients remain WebApp-only",
            dimensions=("role:accountant", "link_state:not_applicable", "server:any", "channel:webapp", "outage:stable"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_notification_audience_service.py",
                    "test_accountant_monitoring_recipient_is_webapp_only",
                ),
            ),
            required_staging_evidence=("receipt_metrics",),
        ),
        ValidationScenario(
            scenario_id="TDV-005",
            title="Tier 1 customers keep owner-path delivery and linked Telegram access",
            dimensions=("role:customer_tier1", "link_state:linked", "server:any", "channel:both", "outage:stable"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_notification_audience_service.py",
                    "test_tier1_customer_keeps_owner_path_and_linked_telegram",
                ),
            ),
            required_staging_evidence=("receipt_metrics",),
        ),
        ValidationScenario(
            scenario_id="TDV-006",
            title="Tier 2 customers remain WebApp-only and counterparty-suppressed",
            dimensions=("role:customer_tier2", "link_state:not_applicable", "server:any", "channel:webapp", "outage:stable"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_notification_audience_service.py",
                    "test_tier2_customer_is_webapp_only_and_counterparty_is_suppressed",
                ),
                CoverageRef(
                    "tests/test_trade_execution_seams.py",
                    "test_notification_message_hides_counterparty_for_tier2_audience",
                ),
            ),
            required_staging_evidence=("receipt_metrics",),
        ),
        ValidationScenario(
            scenario_id="TDV-007",
            title="Actor, customer-chain, accountant, owner, WebApp, and Telegram notification fanout matrix is covered",
            dimensions=("role:mixed", "link_state:mixed", "server:both", "channel:both", "outage:stable", "outage:short", "outage:medium", "concurrency:not_pressure"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_notification_delivery_matrix.py",
                    "test_trade_notification_delivery_matrix_runs_all_actor_surface_pairs_against_audience_builder",
                ),
                CoverageRef(
                    "tests/test_trade_notification_delivery_matrix.py",
                    "test_delivery_scenario_catalog_covers_actor_surface_outage_product",
                ),
            ),
            required_staging_evidence=("receipt_metrics", "latency_distribution", "sanitized_logs", "outage_summary"),
        ),
        ValidationScenario(
            scenario_id="TDV-008",
            title="Short outage remote WebApp and Telegram delivery is still sent after sync visibility",
            dimensions=("role:standard", "link_state:linked", "server:opposite", "channel:both", "outage:short"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_webapp_delivery_service.py",
                    "test_short_outage_remote_webapp_delivery_still_sends_after_sync_visibility",
                ),
                CoverageRef(
                    "tests/test_trade_telegram_delivery_service.py",
                    "test_short_outage_remote_telegram_delivery_still_sends_after_sync_visibility",
                ),
            ),
            required_staging_evidence=("outage_summary", "receipt_metrics"),
        ),
        ValidationScenario(
            scenario_id="TDV-009",
            title="Medium outage remote delivery is skipped without user-facing stale messages",
            dimensions=("role:standard", "link_state:linked", "server:opposite", "channel:both", "outage:medium"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_webapp_delivery_service.py",
                    "test_medium_outage_remote_webapp_delivery_is_skipped_without_user_facing_notification",
                ),
                CoverageRef(
                    "tests/test_trade_delivery_receipt_service.py",
                    "test_outage_classification_skips_only_medium_or_long_opposite_server_delivery",
                ),
            ),
            required_staging_evidence=("outage_summary", "receipt_metrics"),
        ),
        ValidationScenario(
            scenario_id="TDV-010",
            title="Long outage remote Telegram delivery is skipped without stale lookup or send",
            dimensions=("role:standard", "link_state:linked", "server:opposite", "channel:telegram", "outage:long"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_telegram_delivery_service.py",
                    "test_long_outage_remote_telegram_delivery_skips_without_user_lookup_or_send",
                ),
                CoverageRef(
                    "tests/test_trade_delivery_receipt_service.py",
                    "test_outage_classification_skips_only_medium_or_long_opposite_server_delivery",
                ),
            ),
            required_staging_evidence=("outage_summary", "receipt_metrics"),
        ),
        ValidationScenario(
            scenario_id="TDV-011",
            title="High contention does not create duplicate trades or corrupted offer quantities",
            dimensions=("role:mixed", "link_state:mixed", "server:both", "channel:both", "outage:stable", "concurrency:high"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trading_core_mixed_load_helpers.py",
                    "test_hot_offer_acceptance_fails_closed_on_data_corruption",
                ),
                CoverageRef(
                    "tests/test_trading_core_mixed_load_helpers.py",
                    "test_hot_offer_acceptance_validates_quantities_and_ledger",
                ),
                CoverageRef(
                    "tests/test_bot_webapp_comprehensive_load_matrix.py",
                    "test_matrix_covers_required_scenario_families",
                ),
            ),
            required_staging_evidence=("load_profile", "receipt_metrics", "latency_distribution"),
        ),
        ValidationScenario(
            scenario_id="TDV-012",
            title="WebApp receipt dedupe prevents duplicate visible notifications",
            dimensions=("role:standard", "link_state:any", "server:iran", "channel:webapp", "outage:stable", "concurrency:duplicate_replay"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_delivery_receipt_service.py",
                    "test_duplicate_webapp_notification_conflict_loads_existing_without_commit",
                ),
                CoverageRef(
                    "tests/test_trade_webapp_delivery_service.py",
                    "test_duplicate_repair_loads_existing_notification_without_creating_duplicate_row",
                ),
            ),
            required_staging_evidence=("receipt_metrics",),
        ),
        ValidationScenario(
            scenario_id="TDV-013",
            title="Telegram user unreachable errors are skipped without crashing or permanent backlog",
            dimensions=("role:standard", "link_state:broken_telegram", "server:foreign", "channel:telegram", "outage:stable"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_telegram_delivery_service.py",
                    "test_user_unreachable_errors_are_skipped",
                ),
                CoverageRef(
                    "tests/test_trade_telegram_delivery_service.py",
                    "test_fixed_relinked_account_does_not_reopen_old_skipped_receipt_but_new_trade_sends",
                ),
            ),
            required_staging_evidence=("receipt_metrics",),
        ),
        ValidationScenario(
            scenario_id="TDV-014",
            title="Worker crash before send is recoverable through receipt retry",
            dimensions=("role:standard", "link_state:linked", "server:both", "channel:both", "outage:stable", "concurrency:worker_crash_before_send"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_delivery_receipt_service.py",
                    "test_state_machine_allows_expected_transitions_and_blocks_terminal_reopen",
                ),
                CoverageRef(
                    "tests/test_trade_delivery_reconciliation_service.py",
                    "test_replayed_completed_trade_missing_receipt_is_reported_repairable_without_duplicate_notification_gap",
                ),
            ),
            required_staging_evidence=("crash_probes", "receipt_metrics"),
        ),
        ValidationScenario(
            scenario_id="TDV-015",
            title="Crash after Telegram send before sent is explicitly isolated as the only accepted ambiguity",
            dimensions=("role:standard", "link_state:linked", "server:foreign", "channel:telegram", "outage:stable", "concurrency:worker_crash_after_send"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trade_delivery_receipt_service.py",
                    "test_upsert_preserves_terminal_receipt_from_reopen",
                ),
                CoverageRef(
                    "tests/test_trade_telegram_delivery_service.py",
                    "test_success_marks_receipt_sent_with_telegram_message_id",
                ),
            ),
            required_staging_evidence=("crash_probes", "receipt_metrics"),
        ),
        ValidationScenario(
            scenario_id="TDV-016",
            title="Load-style staging run preserves 1000 users, 600 rps target, and 60/40 Telegram/WebApp mix",
            dimensions=("role:mixed", "link_state:mixed", "server:both", "channel:both", "outage:stable", "concurrency:load_600rps"),
            coverage_refs=(
                CoverageRef(
                    "tests/test_trading_core_mixed_load_helpers.py",
                    "test_hot_offer_scenario_specs_cover_step_11b3_matrix",
                ),
                CoverageRef(
                    "tests/test_trading_core_mixed_load_helpers.py",
                    "test_dual_role_worker_plans_split_distribution_and_share_barrier",
                ),
                CoverageRef(
                    "tests/test_bot_webapp_capacity_report.py",
                    "test_build_capacity_report_contains_required_release_gate_fields",
                ),
                CoverageRef(
                    "tests/test_deployment_surface_guard.py",
                    "test_staging_load_runners_are_profile_gated_and_role_bound",
                ),
            ),
            required_staging_evidence=("load_profile", "sanitized_logs", "capacity_report"),
        ),
    ]


def matrix_as_dicts(matrix: Iterable[ValidationScenario]) -> list[dict[str, Any]]:
    return [
        {
            **asdict(scenario),
            "coverage_refs": [asdict(ref) for ref in scenario.coverage_refs],
        }
        for scenario in matrix
    ]


def coverage_ref_exists(repo_root: Path, ref: CoverageRef) -> bool:
    path = repo_root / ref.path
    if not path.exists():
        return False
    return ref.symbol in path.read_text(encoding="utf-8")


def collect_matrix_gaps(repo_root: Path, matrix: list[ValidationScenario]) -> list[str]:
    gaps: list[str] = []
    scenario_ids = [scenario.scenario_id for scenario in matrix]
    duplicated_ids = sorted({scenario_id for scenario_id in scenario_ids if scenario_ids.count(scenario_id) > 1})
    for scenario_id in duplicated_ids:
        gaps.append(f"duplicate scenario id: {scenario_id}")

    covered_dimensions = set()
    for scenario in matrix:
        covered_dimensions.update(item.split(":", 1)[0] for item in scenario.dimensions)
        if not scenario.coverage_refs:
            gaps.append(f"{scenario.scenario_id}: no deterministic coverage refs")
        for ref in scenario.coverage_refs:
            if not coverage_ref_exists(repo_root, ref):
                gaps.append(f"{scenario.scenario_id}: missing coverage ref {ref.path}::{ref.symbol}")

    for dimension in REQUIRED_DIMENSIONS:
        if dimension not in covered_dimensions:
            gaps.append(f"matrix does not cover required dimension: {dimension}")
    return gaps


def validate_matrix(repo_root: Path) -> dict[str, Any]:
    matrix = build_validation_matrix()
    gaps = collect_matrix_gaps(repo_root, matrix)
    if gaps:
        raise TradeDeliveryStagingValidationError("; ".join(gaps))
    return {
        "schema_version": TRADE_DELIVERY_STAGING_REPORT_SCHEMA_VERSION,
        "status": "valid",
        "scenario_count": len(matrix),
        "required_dimensions": list(REQUIRED_DIMENSIONS),
    }


def _require_mapping(payload: MappingLike, key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise TradeDeliveryStagingValidationError(f"{key} must be a JSON object")
    return value


MappingLike = dict[str, Any]


def validate_production_gate(report: dict[str, Any]) -> None:
    gate = report.get("production_gate") or {}
    if gate.get("status") != PRODUCTION_GATE_BLOCKED_STATUS:
        raise TradeDeliveryStagingValidationError("production_gate must remain blocked until owner staging validation")


def validate_scenario_results(report: dict[str, Any]) -> list[str]:
    matrix = build_validation_matrix()
    expected_ids = {scenario.scenario_id for scenario in matrix}
    results = report.get("scenario_results")
    if not isinstance(results, list):
        raise TradeDeliveryStagingValidationError("scenario_results must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            raise TradeDeliveryStagingValidationError("each scenario result must be a JSON object")
        scenario_id = str(item.get("scenario_id") or "")
        if not scenario_id:
            raise TradeDeliveryStagingValidationError("scenario result is missing scenario_id")
        if scenario_id in by_id:
            raise TradeDeliveryStagingValidationError(f"duplicate scenario result: {scenario_id}")
        by_id[scenario_id] = item

    missing = sorted(expected_ids - set(by_id))
    if missing:
        raise TradeDeliveryStagingValidationError(f"scenario_results missing required scenarios: {', '.join(missing)}")

    warnings: list[str] = []
    for scenario_id in sorted(expected_ids):
        status = by_id[scenario_id].get("status")
        if status == "failed":
            raise TradeDeliveryStagingValidationError(f"{scenario_id}: scenario failed")
        if status == "skipped":
            reason = str(by_id[scenario_id].get("skip_reason") or "").strip()
            if not reason:
                raise TradeDeliveryStagingValidationError(f"{scenario_id}: skipped scenario must include skip_reason")
            warnings.append(f"{scenario_id}: skipped - {reason}")
        elif status != "passed":
            raise TradeDeliveryStagingValidationError(f"{scenario_id}: invalid scenario status {status!r}")
    return warnings


def validate_log_artifacts(report: dict[str, Any]) -> None:
    artifacts = report.get("log_artifacts")
    if not isinstance(artifacts, dict):
        raise TradeDeliveryStagingValidationError("log_artifacts must be a JSON object")

    missing = [name for name in REQUIRED_LOG_CLASSES if name not in artifacts]
    if missing:
        raise TradeDeliveryStagingValidationError(f"log_artifacts missing required classes: {', '.join(missing)}")

    for name in REQUIRED_LOG_CLASSES:
        item = artifacts.get(name)
        if not isinstance(item, dict):
            raise TradeDeliveryStagingValidationError(f"log_artifacts.{name} must be a JSON object")
        if not item.get("path"):
            raise TradeDeliveryStagingValidationError(f"log_artifacts.{name}.path is required")
        if item.get("sanitized") is not True:
            raise TradeDeliveryStagingValidationError(f"log_artifacts.{name} must be sanitized")
        sensitive_findings = as_int(
            item.get("sensitive_findings", item.get("raw_secret_findings")),
            default=-1,
        )
        if sensitive_findings != 0:
            raise TradeDeliveryStagingValidationError(
                f"log_artifacts.{name} has {sensitive_findings} sensitive findings"
            )
        source = str(item.get("source") or "staging").lower()
        if source == "production":
            raise TradeDeliveryStagingValidationError(f"log_artifacts.{name} cannot come from production")


def validate_receipt_metrics(report: dict[str, Any]) -> list[str]:
    metrics = _require_mapping(report, "delivery_metrics")
    warnings: list[str] = []

    for field in (
        "duplicate_trade_count",
        "duplicate_visible_notification_count",
        "missing_required_delivery_count",
    ):
        if as_int(metrics.get(field), default=-1) != 0:
            raise TradeDeliveryStagingValidationError(f"delivery_metrics.{field} must be zero")

    receipt_status_counts = metrics.get("receipt_status_counts")
    if not isinstance(receipt_status_counts, dict):
        raise TradeDeliveryStagingValidationError("delivery_metrics.receipt_status_counts must be a JSON object")
    missing_statuses = [status for status in REQUIRED_RECEIPT_STATUSES if status not in receipt_status_counts]
    if missing_statuses:
        raise TradeDeliveryStagingValidationError(
            f"delivery_metrics.receipt_status_counts missing statuses: {', '.join(missing_statuses)}"
        )

    latency = metrics.get("stable_delivery_latency_seconds")
    if not isinstance(latency, dict):
        raise TradeDeliveryStagingValidationError(
            "delivery_metrics.stable_delivery_latency_seconds must be a JSON object"
        )
    for field in ("p50", "p95", "p99", "max"):
        if field not in latency:
            raise TradeDeliveryStagingValidationError(
                f"delivery_metrics.stable_delivery_latency_seconds missing {field}"
            )

    threshold_miss_count = as_int(metrics.get("stable_threshold_miss_count"))
    threshold_explanations = metrics.get("stable_threshold_miss_explanations")
    if threshold_explanations is None:
        threshold_explanations = []
    if not isinstance(threshold_explanations, list):
        raise TradeDeliveryStagingValidationError(
            "delivery_metrics.stable_threshold_miss_explanations must be a list"
        )
    if threshold_miss_count and len(threshold_explanations) < threshold_miss_count:
        raise TradeDeliveryStagingValidationError(
            "stable latency threshold misses must be explained one by one"
        )
    if threshold_miss_count:
        warnings.append(f"{threshold_miss_count} stable latency threshold misses were explained")

    max_latency = as_float(latency.get("max"))
    if max_latency > STABLE_LATENCY_TARGET_SECONDS and not threshold_explanations:
        raise TradeDeliveryStagingValidationError(
            f"stable max delivery latency {max_latency} exceeds {STABLE_LATENCY_TARGET_SECONDS}s without explanation"
        )

    accepted_ambiguous = as_int(metrics.get("accepted_ambiguous_telegram_duplicate_count"))
    if accepted_ambiguous:
        warnings.append(
            f"{accepted_ambiguous} accepted ambiguous Telegram crash-after-send duplicate risks were recorded"
        )
    return warnings


def validate_outage_summary(report: dict[str, Any]) -> None:
    outage = _require_mapping(report, "outage_summary")
    if as_int(outage.get("short_outage_sent_count"), default=-1) < 1:
        raise TradeDeliveryStagingValidationError("outage_summary.short_outage_sent_count must be at least 1")
    if as_int(outage.get("medium_outage_skipped_count"), default=-1) < 1:
        raise TradeDeliveryStagingValidationError("outage_summary.medium_outage_skipped_count must be at least 1")
    if as_int(outage.get("long_outage_skipped_count"), default=-1) < 1:
        raise TradeDeliveryStagingValidationError("outage_summary.long_outage_skipped_count must be at least 1")
    if as_int(outage.get("unexpected_old_remote_delivery_count"), default=-1) != 0:
        raise TradeDeliveryStagingValidationError(
            "outage_summary.unexpected_old_remote_delivery_count must be zero"
        )


def validate_crash_probes(report: dict[str, Any]) -> list[str]:
    probes = _require_mapping(report, "crash_probes")
    before_send = probes.get("before_send") or {}
    after_send = probes.get("after_send_before_sent") or {}
    if not isinstance(before_send, dict) or before_send.get("status") != "recovered":
        raise TradeDeliveryStagingValidationError("crash_probes.before_send.status must be recovered")
    if as_int(before_send.get("duplicate_visible_notification_count"), default=-1) != 0:
        raise TradeDeliveryStagingValidationError(
            "crash_probes.before_send.duplicate_visible_notification_count must be zero"
        )

    if not isinstance(after_send, dict):
        raise TradeDeliveryStagingValidationError("crash_probes.after_send_before_sent must be a JSON object")
    status = after_send.get("status")
    accepted_statuses = {"sent", "accepted_ambiguous_telegram_duplicate_risk"}
    if status not in accepted_statuses:
        raise TradeDeliveryStagingValidationError(
            "crash_probes.after_send_before_sent.status must be sent or accepted_ambiguous_telegram_duplicate_risk"
        )

    warnings: list[str] = []
    ambiguous_count = as_int(after_send.get("accepted_ambiguous_telegram_duplicate_count"))
    duplicate_visible_count = as_int(after_send.get("duplicate_visible_notification_count"))
    if status == "accepted_ambiguous_telegram_duplicate_risk":
        if not str(after_send.get("explanation") or "").strip():
            raise TradeDeliveryStagingValidationError(
                "accepted ambiguous Telegram crash-after-send probes must include explanation"
            )
        if ambiguous_count < 1:
            raise TradeDeliveryStagingValidationError(
                "accepted ambiguous Telegram crash-after-send probes must count the ambiguity"
            )
        warnings.append("Telegram crash-after-send ambiguity accepted and isolated")
    elif ambiguous_count or duplicate_visible_count:
        raise TradeDeliveryStagingValidationError(
            "non-ambiguous crash-after-send probes cannot report duplicate visible notifications"
        )

    if duplicate_visible_count and duplicate_visible_count != ambiguous_count:
        raise TradeDeliveryStagingValidationError(
            "crash-after-send duplicate count must be isolated to accepted ambiguous Telegram cases"
        )
    return warnings


def validate_load_profile(report: dict[str, Any]) -> list[str]:
    profile = _require_mapping(report, "load_profile")
    warnings: list[str] = []
    user_count = as_int(profile.get("user_count"), default=-1)
    target_rps = as_float(profile.get("target_rps"), default=-1.0)
    telegram_ratio = as_float(profile.get("telegram_ratio"), default=-1.0)

    capacity_limited = profile.get("capacity_limited") is True
    capacity_reason = str(profile.get("capacity_limited_reason") or "").strip()

    if user_count < DEFAULT_USER_COUNT:
        if not capacity_limited or not capacity_reason:
            raise TradeDeliveryStagingValidationError(
                f"load_profile.user_count {user_count} is below {DEFAULT_USER_COUNT} without capacity_limited_reason"
            )
        warnings.append(f"load user_count below target: {user_count}")
    if target_rps < DEFAULT_TARGET_RPS:
        if not capacity_limited or not capacity_reason:
            raise TradeDeliveryStagingValidationError(
                f"load_profile.target_rps {target_rps} is below {DEFAULT_TARGET_RPS} without capacity_limited_reason"
            )
        warnings.append(f"load target_rps below target: {target_rps}")
    if abs(telegram_ratio - DEFAULT_TELEGRAM_RATIO) > 0.01:
        raise TradeDeliveryStagingValidationError(
            f"load_profile.telegram_ratio must stay near {DEFAULT_TELEGRAM_RATIO}"
        )
    return warnings


def validate_capacity_report_ref(report: dict[str, Any]) -> None:
    capacity_report = _require_mapping(report, "capacity_report")
    if capacity_report.get("schema_version") != "bot_webapp_capacity_report_v1":
        raise TradeDeliveryStagingValidationError("capacity_report.schema_version must be bot_webapp_capacity_report_v1")
    if capacity_report.get("production_gate") != PRODUCTION_GATE_BLOCKED_STATUS:
        raise TradeDeliveryStagingValidationError("capacity_report.production_gate must remain blocked")
    if not capacity_report.get("path"):
        raise TradeDeliveryStagingValidationError("capacity_report.path is required")


def validate_staging_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != TRADE_DELIVERY_STAGING_REPORT_SCHEMA_VERSION:
        raise TradeDeliveryStagingValidationError("unsupported trade delivery staging report schema_version")
    if report.get("environment") != "staging":
        raise TradeDeliveryStagingValidationError("environment must be staging")

    validate_production_gate(report)
    warnings: list[str] = []
    warnings.extend(validate_scenario_results(report))
    validate_log_artifacts(report)
    warnings.extend(validate_receipt_metrics(report))
    validate_outage_summary(report)
    warnings.extend(validate_crash_probes(report))
    warnings.extend(validate_load_profile(report))
    validate_capacity_report_ref(report)

    return {
        "schema_version": report["schema_version"],
        "status": "valid",
        "scenario_count": len(build_validation_matrix()),
        "warning_count": len(warnings),
        "warnings": warnings,
        "manual_signoff_required": True,
        "production_gate": PRODUCTION_GATE_BLOCKED_STATUS,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or validate Stage 11 trade delivery staging artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    matrix = subparsers.add_parser("matrix", help="print the Stage 11 validation matrix")
    matrix.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    matrix.add_argument("--output", type=Path)

    validate = subparsers.add_parser("validate", help="validate an existing Stage 11 staging report")
    validate.add_argument("--artifact", required=True, type=Path)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.command == "matrix":
            validation = validate_matrix(args.repo_root)
            payload = {
                **validation,
                "matrix": matrix_as_dicts(build_validation_matrix()),
                "production_gate": {
                    "status": PRODUCTION_GATE_BLOCKED_STATUS,
                    "reason": "Owner-led staging validation must review this artifact before production consideration.",
                },
            }
            if args.output:
                write_json(args.output, payload)
            print(json.dumps(validation, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "validate":
            print(json.dumps(validate_staging_report(read_json(args.artifact)), ensure_ascii=False, sort_keys=True))
            return 0

    except TradeDeliveryStagingValidationError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1

    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
