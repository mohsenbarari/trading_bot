"""Fail-closed planning and evidence contract for the Stage 4 queue workload.

This module never opens a socket or starts a subprocess.  It turns the exact
workload into immutable, checksum-bound inputs for the separately authorized
staging orchestrator and validates the artifacts returned by its sender and
receiver processes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from core.telegram_queue_stage4_workload import (
    STAGE4_DURATION_SECONDS,
    Stage4Workload,
    build_stage4_workload,
    validate_stage4_workload,
)


STAGE4_HARNESS_SCHEMA_VERSION = 1
STAGE4_REQUIRED_FINGERPRINTS = frozenset(
    {"database", "redis", "primary_bot", "channel_editor_bot", "channel"}
)
STAGE4_PLAN_FILES = (
    "event_trace.jsonl",
    "expected_business_ledger.jsonl",
    "fault_catalog.json",
    "cleanup_plan.jsonl",
    "quota_report.json",
    "stop_thresholds.json",
)
STAGE4_LIVE_EVIDENCE_FILES = (
    "preflight.json",
    "business_outcomes.jsonl",
    "telegram_results.jsonl",
    "receiver_receipts.jsonl",
    "queue_metrics.jsonl",
    "fault_results.jsonl",
    "cleanup_ledger.jsonl",
    "reconciliation.json",
    "acceptance.json",
    "security_scan.json",
)
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^stage4-[a-z0-9][a-z0-9-]{5,80}$")
_FORBIDDEN_CONFIG_KEYS = re.compile(
    r"(?:^|_)(?:token|secret|password|chat_id|channel_id|bot_id|telegram_id|"
    r"database_url|redis_url|user_id)(?:$|_)",
    re.IGNORECASE,
)


class Stage4HarnessValidationError(ValueError):
    """Raised before provider/process access when a Stage 4 contract is unsafe."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage4_config_binding_sha256(config: Mapping[str, Any]) -> str:
    """Bind plan/live config while leaving provider enablement authorization-gated.

    The provider flag must be false during plan creation and true only during
    execution, so hashing its literal value would make every valid plan
    impossible to execute.  Every other config field—including driver command
    arguments and all redacted fingerprints—remains bound exactly.
    """
    bound = dict(config)
    bound.pop("provider_network_enabled", None)
    bound["provider_network_mode"] = "false-in-plan_true-only-after-authorization"
    return sha256_text(canonical_json(bound))


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            yield from _walk_keys(child)


def _fingerprint_map(value: Any, *, name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != STAGE4_REQUIRED_FINGERPRINTS:
        raise Stage4HarnessValidationError(f"stage4_{name}_fingerprints_incomplete")
    result = {str(key): str(item) for key, item in value.items()}
    if any(_FINGERPRINT.fullmatch(item) is None for item in result.values()):
        raise Stage4HarnessValidationError(f"stage4_{name}_fingerprint_invalid")
    if len(set(result.values())) != len(result):
        raise Stage4HarnessValidationError(f"stage4_{name}_fingerprints_not_distinct")
    return result


def validate_stage4_run_config(
    config: Mapping[str, Any],
    *,
    live_execution: bool,
) -> dict[str, Any]:
    """Validate only redacted identities and fixed staging safety invariants."""
    if int(config.get("schema_version", 0)) != STAGE4_HARNESS_SCHEMA_VERSION:
        raise Stage4HarnessValidationError("stage4_config_schema_invalid")
    environment = str(config.get("environment") or "")
    allowed_environments = {"staging"} if live_execution else {"synthetic-test", "staging"}
    if environment not in allowed_environments:
        raise Stage4HarnessValidationError("stage4_environment_not_allowed")
    if any(_FORBIDDEN_CONFIG_KEYS.search(key) for key in _walk_keys(config)):
        raise Stage4HarnessValidationError("stage4_config_contains_raw_identity_or_secret_key")
    run_id = str(config.get("run_id") or "")
    if _RUN_ID.fullmatch(run_id) is None:
        raise Stage4HarnessValidationError("stage4_run_id_invalid")
    staging = _fingerprint_map(config.get("staging_fingerprints"), name="staging")
    production = _fingerprint_map(
        config.get("production_fingerprints"), name="production"
    )
    collisions = sorted(
        key for key in STAGE4_REQUIRED_FINGERPRINTS if staging[key] == production[key]
    )
    if collisions:
        raise Stage4HarnessValidationError(
            "stage4_staging_production_fingerprint_collision:" + ",".join(collisions)
        )
    limits = config.get("runtime_limits")
    if not isinstance(limits, Mapping) or {
        "staging_max_active_offers": int(limits.get("staging_max_active_offers", -1)),
        "default_max_active_offers": int(limits.get("default_max_active_offers", -1)),
        "production_max_active_offers": int(limits.get("production_max_active_offers", -1)),
        "offer_expiry_seconds": int(limits.get("offer_expiry_seconds", -1)),
    } != {
        "staging_max_active_offers": 10,
        "default_max_active_offers": 4,
        "production_max_active_offers": 4,
        "offer_expiry_seconds": 120,
    }:
        raise Stage4HarnessValidationError("stage4_runtime_limits_invalid")
    if bool(config.get("provider_network_enabled")) != bool(live_execution):
        raise Stage4HarnessValidationError("stage4_provider_network_mode_invalid")
    mode = str(config.get("bot_mode") or "")
    if mode not in {"primary-only", "primary-and-channel-editor"}:
        raise Stage4HarnessValidationError("stage4_bot_mode_invalid")
    return {
        "environment": environment,
        "run_id": run_id,
        "bot_mode": mode,
        "staging_fingerprints": staging,
        "production_fingerprints": production,
        "runtime_limits": dict(limits),
        "provider_network_enabled": bool(live_execution),
    }


def stage4_fault_catalog() -> tuple[dict[str, Any], ...]:
    """Code-owned mandatory provider/transport matrix for adapter execution."""
    rows = (
        ("ok_send_with_message_id", "sent"),
        ("ok_edit", "sent"),
        ("http_200_ok_false", "classified_error"),
        ("ok_send_missing_message_id", "ambiguous"),
        ("malformed_json", "method_aware"),
        ("http_400_bad_payload", "terminal_failed"),
        ("http_400_callback_expired", "expired_interaction"),
        ("http_400_edit_not_modified", "sent_noop"),
        ("http_400_edit_target_missing", "permanent_undeliverable"),
        ("http_401", "bot_paused"),
        ("http_403_private", "permanent_undeliverable"),
        ("http_403_channel_editor", "editor_destination_paused_only"),
        ("http_404_method", "gateway_paused"),
        ("http_409", "bot_paused"),
        ("http_429_integer", "pending_retry_full_retry_after"),
        ("http_429_missing_retry_after", "pending_retry_bounded_fallback"),
        ("http_429_malformed_retry_after", "pending_retry_bounded_fallback"),
        ("http_5xx_send", "ambiguous"),
        ("http_5xx_edit", "pending_retry_or_reconcile"),
        ("pre_write_dns_connect_tls", "pending_retry"),
        ("unknown_write_timeout_reset_send", "ambiguous"),
        ("unknown_write_timeout_reset_edit", "pending_retry_or_reconcile"),
        ("response_received_then_close", "use_received_response"),
        ("migrate_to_chat_id", "pause_without_retarget"),
        ("provider_fact_database_outage", "persist_then_replay_without_second_call"),
    )
    return tuple(
        {
            "case_id": case_id,
            "expected_disposition": expected,
            "required": True,
            "fault_injection_only": case_id not in {"ok_send_with_message_id", "ok_edit"},
        }
        for case_id, expected in rows
    )


def stage4_stop_thresholds() -> dict[str, Any]:
    return {
        "immediate_stop": {
            "production_fingerprint_collision": 1,
            "unexpected_destination_or_bot_role": 1,
            "raw_secret_or_identity_in_artifact": 1,
            "duplicate_provider_side_effect": 1,
            "invalid_offer_delivery_obligation": 1,
            "unbounded_persistent_429": 1,
        },
        "acceptance": {
            "eligible_publication_before_deadline_ratio": 1.0,
            "publication_latency_p95_seconds_max": 10.0,
            "publication_latency_p99_seconds_max": 30.0,
            "callback_latency_p95_seconds_max": 1.0,
            "callback_latency_p99_seconds_max": 2.0,
            "missing_receiver_receipts_max": 0,
            "duplicate_receiver_receipts_max": 0,
            "backlog_caused_disablements_max": 0,
        },
    }


def _expected_business_rows(
    events: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_type = str(event["event_type"])
        if event_type == "offer_submit_valid":
            disposition = "accepted_with_durable_publication_and_private_confirmation"
            offer_delta = 1
            publication_intent_delta = 1
        elif event_type == "offer_submit_invalid":
            disposition = "rejected_without_offer_or_publication_intent"
            offer_delta = 0
            publication_intent_delta = 0
        else:
            disposition = "resolved_by_authoritative_business_state"
            offer_delta = 0
            publication_intent_delta = 0
        rows.append(
            {
                "event_id": event["event_id"],
                "business_event_id": event["business_event_id"],
                "event_type": event_type,
                "expected_disposition": disposition,
                "expected_offer_delta": offer_delta,
                "expected_publication_intent_delta": publication_intent_delta,
                # Invalid submissions still owe the user the ordinary private
                # validation response; what they must never create is an Offer
                # or publication intent.
                "response_receipt_required": True,
                "response_catalog_id": event.get("response_catalog_id"),
            }
        )
    return tuple(rows)


def _cleanup_rows(workload: Stage4Workload) -> tuple[dict[str, Any], ...]:
    offer_ids = sorted(
        {
            str(event["business_event_id"])
            for event in workload.events
            if event["event_type"] == "offer_submit_valid"
        }
    )
    rows = [
        {
            "cleanup_id": f"offer-{offer_id}",
            "scope": "synthetic_offer_and_run_scoped_delivery_state",
            "business_event_id": offer_id,
            "during_measurement": False,
            "provider_delete_priority": "M7",
        }
        for offer_id in offer_ids
    ]
    rows.append(
        {
            "cleanup_id": "run-scoped-admin-and-receiver-state",
            "scope": "synthetic_admin_receiver_and_observer_state",
            "business_event_id": "run",
            "during_measurement": False,
            "provider_delete_priority": "M7",
        }
    )
    return tuple(rows)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_stage4_plan(
    *,
    output_dir: Path,
    config: Mapping[str, Any],
    fixture: Mapping[str, Any],
    seed: int,
    git_commit: str,
) -> dict[str, Any]:
    safe = validate_stage4_run_config(config, live_execution=False)
    if not re.fullmatch(r"[0-9a-f]{40}", str(git_commit)):
        raise Stage4HarnessValidationError("stage4_git_commit_invalid")
    root = Path(output_dir)
    if root.is_symlink() or (root.exists() and any(root.iterdir())):
        raise Stage4HarnessValidationError("stage4_output_directory_must_be_new_or_empty")
    root.mkdir(parents=True, exist_ok=True)
    incomplete = root / ".incomplete"
    incomplete.write_text("stage4-plan-in-progress\n", encoding="utf-8")
    try:
        workload = build_stage4_workload(seed=int(seed), fixture=fixture)
        commodities = tuple(
            str(row["key"])
            for row in fixture["commodities"]
            if row.get("active") is True
        )
        quota_report = validate_stage4_workload(workload, commodities=commodities)
        _write_jsonl(root / "event_trace.jsonl", workload.events)
        _write_jsonl(
            root / "expected_business_ledger.jsonl",
            _expected_business_rows(workload.events),
        )
        _write_json(root / "fault_catalog.json", stage4_fault_catalog())
        _write_jsonl(root / "cleanup_plan.jsonl", _cleanup_rows(workload))
        _write_json(root / "quota_report.json", quota_report)
        _write_json(root / "stop_thresholds.json", stage4_stop_thresholds())
        files = {
            filename: {"sha256": sha256_file(root / filename), "bytes": (root / filename).stat().st_size}
            for filename in STAGE4_PLAN_FILES
        }
        manifest = {
            "schema_version": STAGE4_HARNESS_SCHEMA_VERSION,
            "run_id": safe["run_id"],
            "environment": safe["environment"],
            "bot_mode": safe["bot_mode"],
            "seed": int(seed),
            "git_commit": str(git_commit),
            "trace_sha256": workload.trace_sha256,
            "fixture_sha256": workload.fixture_sha256,
            "config_sha256": stage4_config_binding_sha256(config),
            "provider_network_enabled": False,
            "live_execution_authorized": False,
            "staging_fingerprints": safe["staging_fingerprints"],
            "runtime_limits": safe["runtime_limits"],
            "files": files,
        }
        _write_json(root / "manifest.json", manifest)
    except BaseException:
        raise
    else:
        incomplete.unlink()
    verify_stage4_plan(root)
    return manifest


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Stage4HarnessValidationError(f"stage4_artifact_invalid:{path.name}") from exc


def _load_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        rows = tuple(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Stage4HarnessValidationError(f"stage4_artifact_invalid:{path.name}") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise Stage4HarnessValidationError(f"stage4_artifact_rows_invalid:{path.name}")
    return rows


def verify_stage4_plan(output_dir: Path) -> dict[str, Any]:
    root = Path(output_dir)
    if root.is_symlink() or (root / ".incomplete").exists():
        raise Stage4HarnessValidationError("stage4_plan_incomplete_or_symlinked")
    manifest = _load_json(root / "manifest.json")
    if int(manifest.get("schema_version", 0)) != STAGE4_HARNESS_SCHEMA_VERSION:
        raise Stage4HarnessValidationError("stage4_manifest_schema_invalid")
    if manifest.get("provider_network_enabled") is not False:
        raise Stage4HarnessValidationError("stage4_plan_provider_network_must_be_disabled")
    declared = manifest.get("files")
    if not isinstance(declared, Mapping) or set(declared) != set(STAGE4_PLAN_FILES):
        raise Stage4HarnessValidationError("stage4_plan_file_manifest_incomplete")
    for filename in STAGE4_PLAN_FILES:
        path = root / filename
        expected = declared[filename]
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256_file(path) != str(expected.get("sha256"))
            or path.stat().st_size != int(expected.get("bytes", -1))
        ):
            raise Stage4HarnessValidationError(f"stage4_plan_file_mismatch:{filename}")
    trace = _load_jsonl(root / "event_trace.jsonl")
    rendered = "".join(canonical_json(row) + "\n" for row in trace)
    if sha256_text(rendered) != str(manifest.get("trace_sha256")):
        raise Stage4HarnessValidationError("stage4_manifest_trace_hash_mismatch")
    expected_rows = _load_jsonl(root / "expected_business_ledger.jsonl")
    if expected_rows != _expected_business_rows(trace):
        raise Stage4HarnessValidationError("stage4_expected_ledger_incomplete")
    faults = _load_json(root / "fault_catalog.json")
    if faults != list(stage4_fault_catalog()):
        raise Stage4HarnessValidationError("stage4_fault_catalog_incomplete")
    cleanup = _load_jsonl(root / "cleanup_plan.jsonl")
    if cleanup != _cleanup_rows(
        Stage4Workload(
            seed=int(manifest.get("seed", 0)),
            events=trace,
            peak_windows=(),
            trace_sha256=str(manifest.get("trace_sha256") or ""),
            fixture_sha256=str(manifest.get("fixture_sha256") or ""),
        )
    ):
        raise Stage4HarnessValidationError("stage4_cleanup_plan_incomplete")
    if _load_json(root / "stop_thresholds.json") != stage4_stop_thresholds():
        raise Stage4HarnessValidationError("stage4_stop_thresholds_mismatch")
    return manifest


def _verify_stage4_live_evidence_unchecked(output_dir: Path) -> dict[str, Any]:
    """Fail closed unless sender, receiver, cleanup and reconciliation agree."""
    root = Path(output_dir)
    manifest = verify_stage4_plan(root)
    for filename in STAGE4_LIVE_EVIDENCE_FILES:
        path = root / filename
        if not path.is_file() or path.is_symlink():
            raise Stage4HarnessValidationError(f"stage4_live_evidence_missing:{filename}")

    preflight = _load_json(root / "preflight.json")
    expected_preflight = {
        "status": "pass",
        "run_id": manifest["run_id"],
        "trace_sha256": manifest["trace_sha256"],
        "bot_mode": manifest["bot_mode"],
        "production_fingerprint_collision_count": 0,
        "observer_provider_network_call_count": 0,
        "primary_publication_ready": True,
        "channel_editor_edit_ready": (
            manifest["bot_mode"] == "primary-and-channel-editor"
        ),
        "channel_editor_send_allowed": False,
        "channel_editor_delete_allowed": False,
        "runtime_limits": manifest["runtime_limits"],
    }
    if any(preflight.get(key) != value for key, value in expected_preflight.items()):
        raise Stage4HarnessValidationError("stage4_preflight_mismatch")
    if _FINGERPRINT.fullmatch(str(preflight.get("routing_policy_sha256") or "")) is None:
        raise Stage4HarnessValidationError("stage4_preflight_routing_policy_hash_invalid")

    expected = _load_jsonl(root / "expected_business_ledger.jsonl")
    outcomes = _load_jsonl(root / "business_outcomes.jsonl")
    expected_by_event = {str(row["event_id"]): row for row in expected}
    outcomes_by_event = {str(row.get("event_id") or ""): row for row in outcomes}
    if (
        len(outcomes_by_event) != len(outcomes)
        or set(outcomes_by_event) != set(expected_by_event)
    ):
        raise Stage4HarnessValidationError("stage4_business_outcome_ledger_mismatch")
    for event_id, expected_row in expected_by_event.items():
        observed = outcomes_by_event[event_id]
        required = {
            "business_event_id": expected_row["business_event_id"],
            "event_type": expected_row["event_type"],
            "observed_disposition": expected_row["expected_disposition"],
            "offer_delta": expected_row["expected_offer_delta"],
            "publication_intent_delta": expected_row[
                "expected_publication_intent_delta"
            ],
            "response_catalog_id": expected_row["response_catalog_id"],
            "status": "resolved",
        }
        if any(observed.get(key) != value for key, value in required.items()):
            raise Stage4HarnessValidationError(
                "stage4_business_outcome_value_mismatch"
            )

    telegram_results = _load_jsonl(root / "telegram_results.jsonl")
    result_ids = [str(row.get("delivery_id") or "") for row in telegram_results]
    if (
        not telegram_results
        or not all(result_ids)
        or len(set(result_ids)) != len(result_ids)
    ):
        raise Stage4HarnessValidationError("stage4_telegram_result_identity_invalid")
    catalog_receipts: set[tuple[str, str]] = set()
    receiver_required_ids: set[str] = set()
    allowed_methods = {
        "sendMessage",
        "editMessageText",
        "editMessageReplyMarkup",
        "answerCallbackQuery",
        "sendDocument",
        "banChatMember",
        "unbanChatMember",
    }
    allowed_outcomes = {
        "sent",
        "sent_noop",
        "expired_interaction",
        "permanent_undeliverable",
        "superseded",
        "disabled",
    }
    for row in telegram_results:
        event_id = str(row.get("event_id") or "")
        if event_id not in expected_by_event:
            raise Stage4HarnessValidationError("stage4_telegram_result_event_unknown")
        method = str(row.get("method") or "")
        bot_role = str(row.get("bot_role") or "")
        destination_class = str(row.get("destination_class") or "")
        if (
            method not in allowed_methods
            or bot_role not in {"primary", "channel_editor"}
            or destination_class not in {"private", "channel"}
            or str(row.get("outcome") or "") not in allowed_outcomes
            or isinstance(row.get("provider_side_effect_count"), bool)
            or int(row.get("provider_side_effect_count", -1)) not in {0, 1}
            or float(row.get("latency_ms", -1)) < 0
            or not isinstance(row.get("receiver_required"), bool)
        ):
            raise Stage4HarnessValidationError("stage4_telegram_result_invalid")
        if bot_role == "channel_editor" and (
            destination_class != "channel"
            or method not in {"editMessageText", "editMessageReplyMarkup"}
        ):
            raise Stage4HarnessValidationError("stage4_editor_route_violation")
        response_catalog_id = str(row.get("response_catalog_id") or "")
        if response_catalog_id:
            catalog_receipts.add((event_id, response_catalog_id))
        if row["receiver_required"] and row["outcome"] in {"sent", "sent_noop"}:
            receiver_required_ids.add(str(row["delivery_id"]))
    required_catalog_receipts = {
        (event_id, str(row["response_catalog_id"]))
        for event_id, row in expected_by_event.items()
        if row.get("response_receipt_required") is True
    }
    if not required_catalog_receipts.issubset(catalog_receipts):
        raise Stage4HarnessValidationError("stage4_response_catalog_receipt_missing")

    receipts = _load_jsonl(root / "receiver_receipts.jsonl")
    receipt_ids = [str(row.get("delivery_id") or "") for row in receipts]
    if (
        len(set(receipt_ids)) != len(receipt_ids)
        or set(receipt_ids) != receiver_required_ids
        or any(row.get("status") != "observed" for row in receipts)
    ):
        raise Stage4HarnessValidationError("stage4_receiver_receipt_mismatch")

    metrics = _load_jsonl(root / "queue_metrics.jsonl")
    observed_seconds = {
        int(row.get("elapsed_second", -1))
        for row in metrics
        if not isinstance(row.get("elapsed_second"), bool)
    }
    if not set(range(STAGE4_DURATION_SECONDS + 1)).issubset(observed_seconds):
        raise Stage4HarnessValidationError("stage4_queue_metric_timeline_incomplete")
    final_metric = max(metrics, key=lambda row: int(row.get("elapsed_second", -1)))
    if (
        final_metric.get("drain_complete") is not True
        or int(final_metric.get("ready_backlog", -1)) != 0
        or int(final_metric.get("leased_backlog", -1)) != 0
        or int(final_metric.get("unresolved_backlog", -1)) != 0
    ):
        raise Stage4HarnessValidationError("stage4_queue_not_drained")

    fault_results = _load_jsonl(root / "fault_results.jsonl")
    fault_by_id = {str(row.get("case_id") or ""): row for row in fault_results}
    catalog = {row["case_id"]: row for row in stage4_fault_catalog()}
    if len(fault_by_id) != len(fault_results) or set(fault_by_id) != set(catalog):
        raise Stage4HarnessValidationError("stage4_fault_result_catalog_mismatch")
    for case_id, fault in fault_by_id.items():
        if (
            fault.get("status") != "pass"
            or fault.get("observed_disposition")
            != catalog[case_id]["expected_disposition"]
            or int(fault.get("duplicate_provider_side_effect_count", -1)) != 0
        ):
            raise Stage4HarnessValidationError("stage4_fault_result_mismatch")

    cleanup_plan = _load_jsonl(root / "cleanup_plan.jsonl")
    cleanup = _load_jsonl(root / "cleanup_ledger.jsonl")
    if {row.get("cleanup_id") for row in cleanup} != {
        row.get("cleanup_id") for row in cleanup_plan
    } or len(cleanup) != len(cleanup_plan) or any(
        row.get("status") not in {"completed", "not_applicable"} for row in cleanup
    ):
        raise Stage4HarnessValidationError("stage4_cleanup_ledger_incomplete")

    reconciliation = _load_json(root / "reconciliation.json")
    acceptance = _load_json(root / "acceptance.json")
    zero_reconciliation_fields = (
        "unresolved_jobs",
        "duplicate_provider_side_effects",
        "missing_business_outcomes",
        "missing_receiver_receipts",
        "invalid_offer_publication_intents",
        "cross_lane_route_mutations",
        "backlog_caused_disablements",
    )
    if reconciliation.get("status") != "clean" or any(
        int(reconciliation.get(key, -1)) != 0
        for key in zero_reconciliation_fields
    ):
        raise Stage4HarnessValidationError("stage4_live_reconciliation_not_clean")
    acceptance_metrics = acceptance.get("metrics")
    input_counts = acceptance.get("input_counts")
    criteria = acceptance.get("criteria")
    thresholds = stage4_stop_thresholds()["acceptance"]
    if (
        acceptance.get("decision") != "pass"
        or acceptance.get("stop_events") != []
        or input_counts
        != {"valid_accepted": 1800, "invalid_rejected": 400}
        or not isinstance(criteria, Mapping)
        or not criteria
        or any(value is not True for value in criteria.values())
        or not isinstance(acceptance_metrics, Mapping)
        or float(acceptance_metrics.get("eligible_publication_before_deadline_ratio", -1))
        < float(thresholds["eligible_publication_before_deadline_ratio"])
        or float(acceptance_metrics.get("publication_latency_p95_seconds", 10**9))
        > float(thresholds["publication_latency_p95_seconds_max"])
        or float(acceptance_metrics.get("publication_latency_p99_seconds", 10**9))
        > float(thresholds["publication_latency_p99_seconds_max"])
        or float(acceptance_metrics.get("callback_latency_p95_seconds", 10**9))
        > float(thresholds["callback_latency_p95_seconds_max"])
        or float(acceptance_metrics.get("callback_latency_p99_seconds", 10**9))
        > float(thresholds["callback_latency_p99_seconds_max"])
        or int(acceptance_metrics.get("missing_receiver_receipts", -1))
        > int(thresholds["missing_receiver_receipts_max"])
        or int(acceptance_metrics.get("duplicate_receiver_receipts", -1))
        > int(thresholds["duplicate_receiver_receipts_max"])
        or int(acceptance_metrics.get("backlog_caused_disablements", -1))
        > int(thresholds["backlog_caused_disablements_max"])
    ):
        raise Stage4HarnessValidationError("stage4_live_acceptance_not_clean")

    security_scan = _load_json(root / "security_scan.json")
    required_scanned_files = {
        "manifest.json",
        *STAGE4_PLAN_FILES,
        *(name for name in STAGE4_LIVE_EVIDENCE_FILES if name != "security_scan.json"),
    }
    if (
        security_scan.get("status") != "clean"
        or int(security_scan.get("finding_count", -1)) != 0
        or not required_scanned_files.issubset(
            {str(name) for name in security_scan.get("scanned_files", [])}
        )
    ):
        raise Stage4HarnessValidationError("stage4_security_scan_incomplete")
    return {
        "run_id": manifest["run_id"],
        "status": "verified",
        "evidence_sha256": {
            filename: sha256_file(root / filename)
            for filename in STAGE4_LIVE_EVIDENCE_FILES
        },
    }


def verify_stage4_live_evidence(output_dir: Path) -> dict[str, Any]:
    """Validate untrusted driver artifacts without leaking parser failures."""
    try:
        return _verify_stage4_live_evidence_unchecked(output_dir)
    except Stage4HarnessValidationError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise Stage4HarnessValidationError(
            "stage4_live_evidence_schema_invalid"
        ) from exc
