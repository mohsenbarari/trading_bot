"""Fail-closed planning and evidence contract for the Stage 4 queue workload.

This module never opens a socket or starts a subprocess.  It turns the exact
workload into immutable, checksum-bound inputs for the separately authorized
staging orchestrator and validates the artifacts returned by its sender and
receiver processes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import base64
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


STAGE4_HARNESS_SCHEMA_VERSION = 2
STAGE4_REQUIRED_FINGERPRINTS = frozenset(
    {
        "database",
        "redis",
        "primary_bot",
        "channel_editor_bot",
        "channel",
        "observer_database",
        "receiver_session",
    }
)
STAGE4_NETWORK_POLICY_ROLES = frozenset({"sender", "observer"})
STAGE4_PLAN_FILES = (
    "event_trace.jsonl",
    "expected_business_ledger.jsonl",
    "delivery_obligations.jsonl",
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


def stage4_bound_value_fingerprint(value: str) -> str:
    normalized = str(value or "")
    if not normalized:
        raise Stage4HarnessValidationError("stage4_bound_value_missing")
    return "sha256:" + sha256_text(
        "telegram-stage4-bound-value-v1:" + normalized
    )


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
    network_policies = config.get("network_policy_fingerprints")
    if (
        not isinstance(network_policies, Mapping)
        or set(network_policies) != STAGE4_NETWORK_POLICY_ROLES
        or any(
            _FINGERPRINT.fullmatch(str(value)) is None
            for value in network_policies.values()
        )
        or len({str(value) for value in network_policies.values()}) != 2
    ):
        raise Stage4HarnessValidationError(
            "stage4_network_policy_fingerprints_invalid"
        )
    public_key_text = str(config.get("authorization_public_key") or "")
    try:
        public_key = base64.b64decode(public_key_text, validate=True)
    except (ValueError, TypeError) as exc:
        raise Stage4HarnessValidationError(
            "stage4_authorization_public_key_invalid"
        ) from exc
    if len(public_key) != 32:
        raise Stage4HarnessValidationError(
            "stage4_authorization_public_key_invalid"
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
        "network_policy_fingerprints": {
            key: str(network_policies[key])
            for key in sorted(STAGE4_NETWORK_POLICY_ROLES)
        },
        "authorization_public_key": public_key_text,
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
        ("http_429_integer_one", "pending_retry_full_retry_after"),
        ("http_429_integer_max", "pending_retry_full_retry_after"),
        ("http_429_missing_retry_after", "pending_retry_bounded_fallback"),
        ("http_429_boolean_retry_after", "pending_retry_bounded_fallback"),
        ("http_429_string_retry_after", "pending_retry_bounded_fallback"),
        ("http_429_fractional_retry_after", "pending_retry_bounded_fallback"),
        ("http_429_zero_retry_after", "pending_retry_bounded_fallback"),
        ("http_429_negative_retry_after", "pending_retry_bounded_fallback"),
        ("http_429_integer_overflow", "pending_retry_bounded_fallback"),
        ("http_429_huge_integer", "pending_retry_bounded_fallback"),
        ("http_5xx_send", "ambiguous"),
        ("http_5xx_edit", "pending_retry_or_reconcile"),
        ("pre_write_dns_connect_tls", "pending_retry"),
        ("unknown_write_timeout_reset_send", "ambiguous"),
        ("unknown_write_timeout_reset_edit", "pending_retry_or_reconcile"),
        ("response_received_then_close", "use_received_response"),
        ("migrate_to_chat_id", "pause_without_retarget"),
        ("provider_fact_database_outage", "persist_then_replay_without_second_call"),
    )
    retry_shapes = {
        "http_429_integer_one": "integer:1",
        "http_429_integer_max": "integer:2147483647",
        "http_429_missing_retry_after": "missing",
        "http_429_boolean_retry_after": "boolean:true",
        "http_429_string_retry_after": "string:numeric",
        "http_429_fractional_retry_after": "number:fractional",
        "http_429_zero_retry_after": "integer:zero",
        "http_429_negative_retry_after": "integer:negative",
        "http_429_integer_overflow": "integer:2147483648",
        "http_429_huge_integer": "integer:more-than-64-bit",
    }
    durable_states = {
        "ok_send_with_message_id": ("sent",),
        "ok_edit": ("sent", "sent_noop"),
        "http_200_ok_false": ("failed_permanent",),
        "ok_send_missing_message_id": ("ambiguous",),
        "malformed_json": ("ambiguous", "reconciliation_pending"),
        "http_400_bad_payload": ("failed_permanent",),
        "http_400_callback_expired": ("expired",),
        "http_400_edit_not_modified": ("sent_noop",),
        "http_400_edit_target_missing": ("failed_permanent",),
        "http_401": ("blocked_bot",),
        "http_403_private": ("failed_permanent",),
        "http_403_channel_editor": ("blocked_destination",),
        "http_404_method": ("blocked_gateway",),
        "http_409": ("blocked_bot",),
        "http_429_integer_one": ("pending_retry",),
        "http_429_integer_max": ("pending_retry",),
        "http_429_missing_retry_after": ("pending_retry",),
        "http_429_boolean_retry_after": ("pending_retry",),
        "http_429_string_retry_after": ("pending_retry",),
        "http_429_fractional_retry_after": ("pending_retry",),
        "http_429_zero_retry_after": ("pending_retry",),
        "http_429_negative_retry_after": ("pending_retry",),
        "http_429_integer_overflow": ("pending_retry",),
        "http_429_huge_integer": ("pending_retry",),
        "http_5xx_send": ("ambiguous",),
        "http_5xx_edit": ("pending_retry", "reconciliation_pending"),
        "pre_write_dns_connect_tls": ("pending_retry",),
        "unknown_write_timeout_reset_send": ("ambiguous",),
        "unknown_write_timeout_reset_edit": (
            "pending_retry",
            "reconciliation_pending",
        ),
        "response_received_then_close": (
            "sent",
            "sent_noop",
            "failed_permanent",
            "pending_retry",
        ),
        "migrate_to_chat_id": ("blocked_destination",),
        "provider_fact_database_outage": ("sent", "sent_noop"),
    }
    return tuple(
        {
            "case_id": case_id,
            "expected_disposition": expected,
            "required": True,
            "fault_injection_only": case_id not in {"ok_send_with_message_id", "ok_edit"},
            "input_shape_sha256": sha256_text(
                f"telegram-stage4-fault-shape-v1:{retry_shapes.get(case_id, case_id)}"
            ),
            "retry_after_source": (
                "provider_integer"
                if case_id in {"http_429_integer_one", "http_429_integer_max"}
                else (
                    "bounded_malformed_fallback"
                    if case_id.startswith("http_429_")
                    else "not_applicable"
                )
            ),
            "allowed_durable_states": list(durable_states[case_id]),
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


def _delivery_obligation_rows(
    events: Iterable[Mapping[str, Any]],
    *,
    bot_mode: str,
) -> tuple[dict[str, Any], ...]:
    """Build the code-owned delivery oracle for the exact business trace."""
    event_rows = tuple(events)
    by_id = {str(row["event_id"]): row for row in event_rows}
    valid_by_offer = {
        str(row["business_event_id"]): row
        for row in event_rows
        if row["event_type"] == "offer_submit_valid"
    }
    trades_by_offer: dict[str, list[Mapping[str, Any]]] = {}
    for row in event_rows:
        if row["event_type"] == "trade_request":
            trades_by_offer.setdefault(str(row["business_event_id"]), []).append(row)
    editor_role = (
        "channel_editor"
        if bot_mode == "primary-and-channel-editor"
        else "primary"
    )
    obligations: list[dict[str, Any]] = []

    def add(
        *,
        obligation_id: str,
        source_event_id: str,
        kind: str,
        method: str,
        bot_role: str,
        destination_class: str,
        response_catalog_id: str,
        allowed_outcomes: Sequence[str] = ("sent",),
        receiver_policy: str = "required_on_provider_effect",
        ordinal: int = 1,
        causal_dependency_ids: Sequence[str] = (),
    ) -> None:
        source = by_id[source_event_id]
        obligations.append(
            {
                "obligation_id": obligation_id,
                "source_event_id": source_event_id,
                "business_event_id": source["business_event_id"],
                "source_event_type": source["event_type"],
                "source_at_ms": int(source["at_ms"]),
                "obligation_kind": kind,
                "method": method,
                "bot_role": bot_role,
                "destination_class": destination_class,
                "response_catalog_id": response_catalog_id,
                "allowed_outcomes": list(allowed_outcomes),
                "receiver_policy": receiver_policy,
                "ordinal": ordinal,
                "source_version": f"trace-event:{source_event_id}",
                "causal_dependency_ids": list(causal_dependency_ids),
            }
        )

    for event in event_rows:
        event_id = str(event["event_id"])
        event_type = str(event["event_type"])
        if event_type == "offer_submit_valid":
            add(
                obligation_id=f"preview:{event_id}",
                source_event_id=event_id,
                kind="offer_private_preview",
                method="sendMessage",
                bot_role="primary",
                destination_class="private",
                response_catalog_id=str(event["response_catalog_id"]),
            )
        elif event_type == "offer_confirm":
            add(
                obligation_id=f"confirmation:{event_id}",
                source_event_id=event_id,
                kind="offer_private_confirmation_edit",
                method="editMessageText",
                bot_role="primary",
                destination_class="private",
                response_catalog_id=str(event["response_catalog_id"]),
                allowed_outcomes=("sent", "sent_noop"),
                causal_dependency_ids=(
                    f"event:submit-{event['business_event_id']}",
                ),
            )
            add(
                obligation_id=f"publication:{event_id}",
                source_event_id=event_id,
                kind="offer_channel_publication",
                method="sendMessage",
                bot_role="primary",
                destination_class="channel",
                response_catalog_id="offer.channel.publication",
                causal_dependency_ids=(
                    f"event:{event_id}",
                ),
            )
            valid = valid_by_offer[str(event["business_event_id"])]
            if valid.get("manual_expiry_requested"):
                terminal_kind = "offer_channel_manual_expiry_or_terminal_edit"
            elif valid.get("trade_mode"):
                terminal_kind = "offer_channel_trade_terminal_edit"
            else:
                terminal_kind = "offer_channel_automatic_expiry_edit"
            add(
                obligation_id=f"terminal-edit:{event_id}",
                source_event_id=event_id,
                kind=terminal_kind,
                method="editMessageText",
                bot_role=editor_role,
                destination_class="channel",
                response_catalog_id="offer.channel.terminal",
                allowed_outcomes=("sent", "sent_noop"),
                causal_dependency_ids=(
                    f"delivery:publication:{event_id}",
                ),
            )
        elif event_type == "offer_submit_invalid":
            add(
                obligation_id=f"invalid-response:{event_id}",
                source_event_id=event_id,
                kind="offer_invalid_private_response",
                method="sendMessage",
                bot_role="primary",
                destination_class="private",
                response_catalog_id=str(event["response_catalog_id"]),
            )
        elif event_type == "trade_request":
            add(
                obligation_id=f"trade-callback:{event_id}",
                source_event_id=event_id,
                kind="trade_callback_acknowledgement",
                method="answerCallbackQuery",
                bot_role="primary",
                destination_class="private",
                response_catalog_id=str(event["response_catalog_id"]),
            )
        elif event_type == "manual_expiry_request":
            add(
                obligation_id=f"expiry-callback:{event_id}",
                source_event_id=event_id,
                kind="manual_expiry_callback_acknowledgement",
                method="answerCallbackQuery",
                bot_role="primary",
                destination_class="private",
                response_catalog_id=str(event["response_catalog_id"]),
            )
        elif event_type == "admin_broadcast_delivery":
            add(
                obligation_id=f"admin-delivery:{event_id}",
                source_event_id=event_id,
                kind="admin_private_delivery",
                method="sendMessage",
                bot_role="primary",
                destination_class="private",
                response_catalog_id=str(event["response_catalog_id"]),
            )
        elif event_type == "market_status_change":
            add(
                obligation_id=f"market-notice:{event_id}",
                source_event_id=event_id,
                kind="market_channel_status_notice",
                method="sendMessage",
                bot_role="primary",
                destination_class="channel",
                response_catalog_id=str(event["response_catalog_id"]),
                causal_dependency_ids=(f"event:{event_id}",),
            )

    for offer_id, valid in valid_by_offer.items():
        trade_mode = valid.get("trade_mode")
        trade_rows = sorted(
            trades_by_offer.get(offer_id, ()),
            key=lambda row: (int(row["at_ms"]), str(row["event_id"])),
        )
        successful_trade_rows: list[Mapping[str, Any]] = []
        if trade_mode == "normal" and trade_rows:
            successful_trade_rows = trade_rows[:1]
        elif trade_mode == "concurrent" and trade_rows:
            if valid.get("race_expected_winner") != "expiry":
                successful_trade_rows = trade_rows[:1]
        elif trade_mode == "partial_then_complete":
            successful_trade_rows = trade_rows
            if trade_rows:
                add(
                    obligation_id=f"partial-edit:{offer_id}",
                    source_event_id=str(trade_rows[0]["event_id"]),
                    kind="offer_channel_partial_edit",
                    method="editMessageText",
                    bot_role=editor_role,
                    destination_class="channel",
                    response_catalog_id="offer.channel.partial",
                    allowed_outcomes=("sent", "sent_noop", "superseded"),
                    causal_dependency_ids=(
                        f"delivery:publication:confirm-{offer_id}",
                        f"event:{trade_rows[0]['event_id']}",
                    ),
                )
        for trade_row in successful_trade_rows:
            for ordinal in (1, 2):
                add(
                    obligation_id=(
                        f"trade-party:{trade_row['event_id']}:{ordinal}"
                    ),
                    source_event_id=str(trade_row["event_id"]),
                    kind="trade_party_private_notification",
                    method="sendMessage",
                    bot_role="primary",
                    destination_class="private",
                    response_catalog_id="trade.party.notification",
                    ordinal=ordinal,
                    causal_dependency_ids=(
                        f"event:{trade_row['event_id']}",
                    ),
                )
    return tuple(
        sorted(obligations, key=lambda row: str(row["obligation_id"]))
    )


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
        _write_jsonl(
            root / "delivery_obligations.jsonl",
            _delivery_obligation_rows(
                workload.events,
                bot_mode=safe["bot_mode"],
            ),
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
            "network_policy_fingerprints": safe["network_policy_fingerprints"],
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


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise Stage4HarnessValidationError("stage4_latency_population_empty")
    ordered = sorted(float(value) for value in values)
    rank = max(1, int((percentile / 100.0 * len(ordered)) + 0.999999999))
    return ordered[min(len(ordered), rank) - 1]


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
    delivery_obligations = _load_jsonl(root / "delivery_obligations.jsonl")
    if delivery_obligations != _delivery_obligation_rows(
        trace,
        bot_mode=str(manifest.get("bot_mode") or ""),
    ):
        raise Stage4HarnessValidationError(
            "stage4_delivery_obligation_ledger_incomplete"
        )
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
        "sender_network_policy_sha256": manifest[
            "network_policy_fingerprints"
        ]["sender"],
        "observer_network_policy_sha256": manifest[
            "network_policy_fingerprints"
        ]["observer"],
    }
    if any(preflight.get(key) != value for key, value in expected_preflight.items()):
        raise Stage4HarnessValidationError("stage4_preflight_mismatch")
    if _FINGERPRINT.fullmatch(str(preflight.get("routing_policy_sha256") or "")) is None:
        raise Stage4HarnessValidationError("stage4_preflight_routing_policy_hash_invalid")

    expected = _load_jsonl(root / "expected_business_ledger.jsonl")
    trace = _load_jsonl(root / "event_trace.jsonl")
    trace_by_event = {str(row["event_id"]): row for row in trace}
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
        observed_at_ms = observed.get("observed_at_ms")
        if (
            observed.get("observer_source") != "independent_database_observer"
            or isinstance(observed_at_ms, bool)
            or not isinstance(observed_at_ms, (int, float))
            or float(observed_at_ms) < float(trace_by_event[event_id]["at_ms"])
            or _FINGERPRINT.fullmatch(
                str(observed.get("observation_sha256") or "")
            )
            is None
        ):
            raise Stage4HarnessValidationError(
                "stage4_business_observer_evidence_invalid"
            )

    race_groups: dict[str, list[dict[str, Any]]] = {}
    for event_id, event in trace_by_event.items():
        if event.get("expiry_trade_race") is True and event.get("event_type") in {
            "trade_request",
            "manual_expiry_request",
        }:
            race_groups.setdefault(str(event["business_event_id"]), []).append(event)
    if len(race_groups) != 18:
        raise Stage4HarnessValidationError("stage4_live_race_group_count_invalid")
    for offer_id, group in race_groups.items():
        observed_group = [outcomes_by_event[str(event["event_id"])] for event in group]
        releases = [row.get("sender_release_elapsed_ms") for row in observed_group]
        if (
            any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in releases
            )
            or max(float(value) for value in releases)
            - min(float(value) for value in releases)
            > 100.0
        ):
            raise Stage4HarnessValidationError(
                "stage4_live_race_release_skew_exceeded"
            )
        expected_winner = str(group[0].get("race_expected_winner") or "")
        committed_trades = sum(
            row.get("authoritative_result") == "trade_committed"
            for event, row in zip(group, observed_group, strict=True)
            if event["event_type"] == "trade_request"
        )
        committed_expiries = sum(
            row.get("authoritative_result") == "expiry_committed"
            for event, row in zip(group, observed_group, strict=True)
            if event["event_type"] == "manual_expiry_request"
        )
        allowed_results = {
            "trade_request": {"trade_committed", "rejected_conflict"},
            "manual_expiry_request": {"expiry_committed", "rejected_conflict"},
        }
        if any(
            row.get("authoritative_result")
            not in allowed_results[str(event["event_type"])]
            for event, row in zip(group, observed_group, strict=True)
        ) or (
            expected_winner == "trade"
            and (committed_trades, committed_expiries) != (1, 0)
        ) or (
            expected_winner == "expiry"
            and (committed_trades, committed_expiries) != (0, 1)
        ):
            raise Stage4HarnessValidationError(
                f"stage4_live_race_authoritative_outcome_invalid:{offer_id}"
            )

    obligations = _load_jsonl(root / "delivery_obligations.jsonl")
    obligations_by_id = {
        str(row["obligation_id"]): row for row in obligations
    }
    for obligation in obligations:
        dependencies = obligation.get("causal_dependency_ids")
        if not isinstance(dependencies, list) or any(
            not isinstance(dependency, str)
            or (
                dependency.startswith("event:")
                and dependency.removeprefix("event:") not in trace_by_event
            )
            or (
                dependency.startswith("delivery:")
                and dependency.removeprefix("delivery:") not in obligations_by_id
            )
            or not dependency.startswith(("event:", "delivery:"))
            for dependency in dependencies
        ):
            raise Stage4HarnessValidationError(
                "stage4_delivery_obligation_dependency_invalid"
            )
    telegram_results = _load_jsonl(root / "telegram_results.jsonl")
    result_ids = [str(row.get("delivery_id") or "") for row in telegram_results]
    results_by_obligation = {
        str(row.get("obligation_id") or ""): row for row in telegram_results
    }
    if (
        not telegram_results
        or not all(result_ids)
        or len(set(result_ids)) != len(result_ids)
        or len(results_by_obligation) != len(telegram_results)
        or set(results_by_obligation) != set(obligations_by_id)
    ):
        raise Stage4HarnessValidationError(
            "stage4_telegram_obligation_coverage_invalid"
        )
    receiver_required_ids: set[str] = set()
    provider_effect_by_delivery: dict[str, int] = {}
    for obligation_id, obligation in obligations_by_id.items():
        row = results_by_obligation[obligation_id]
        expected_result_fields = {
            "delivery_id",
            "obligation_id",
            "event_id",
            "business_event_id",
            "response_catalog_id",
            "method",
            "bot_role",
            "destination_class",
            "outcome",
            "provider_side_effect_count",
            "enqueued_elapsed_ms",
            "provider_completed_elapsed_ms",
            "provider_observation_sha256",
            "source_version",
            "causal_dependency_ids",
        }
        outcome = str(row.get("outcome") or "")
        expected_fields = {
            "event_id": obligation["source_event_id"],
            "business_event_id": obligation["business_event_id"],
            "response_catalog_id": obligation["response_catalog_id"],
            "method": obligation["method"],
            "bot_role": obligation["bot_role"],
            "destination_class": obligation["destination_class"],
            "source_version": obligation["source_version"],
            "causal_dependency_ids": obligation["causal_dependency_ids"],
        }
        enqueued_at = row.get("enqueued_elapsed_ms")
        completed_at = row.get("provider_completed_elapsed_ms")
        causal_order_invalid = any(
            (
                dependency.startswith("event:")
                and float(
                    trace_by_event[dependency.removeprefix("event:")]["at_ms"]
                )
                > float(enqueued_at)
            )
            or (
                dependency.startswith("delivery:")
                and float(
                    results_by_obligation[
                        dependency.removeprefix("delivery:")
                    ]["provider_completed_elapsed_ms"]
                )
                > float(completed_at)
            )
            for dependency in obligation["causal_dependency_ids"]
        ) if isinstance(enqueued_at, (int, float)) and not isinstance(
            enqueued_at, bool
        ) and isinstance(completed_at, (int, float)) and not isinstance(
            completed_at, bool
        ) else True
        provider_effect = 1 if outcome == "sent" else 0
        if (
            set(row) != expected_result_fields
            or any(row.get(key) != value for key, value in expected_fields.items())
            or outcome not in set(obligation["allowed_outcomes"])
            or isinstance(enqueued_at, bool)
            or not isinstance(enqueued_at, (int, float))
            or isinstance(completed_at, bool)
            or not isinstance(completed_at, (int, float))
            or float(enqueued_at) < float(obligation["source_at_ms"])
            or float(completed_at) < float(enqueued_at)
            or causal_order_invalid
            or isinstance(row.get("provider_side_effect_count"), bool)
            or int(row.get("provider_side_effect_count", -1)) != provider_effect
            or _FINGERPRINT.fullmatch(
                str(row.get("provider_observation_sha256") or "")
            )
            is None
        ):
            raise Stage4HarnessValidationError(
                "stage4_telegram_obligation_result_invalid"
            )
        if row["bot_role"] == "channel_editor" and (
            row["destination_class"] != "channel"
            or row["method"] not in {"editMessageText", "editMessageReplyMarkup"}
        ):
            raise Stage4HarnessValidationError("stage4_editor_route_violation")
        delivery_id = str(row["delivery_id"])
        provider_effect_by_delivery[delivery_id] = provider_effect
        receiver_policy = str(obligation["receiver_policy"])
        if receiver_policy == "required_on_provider_effect" and provider_effect == 1:
            receiver_required_ids.add(delivery_id)
        elif receiver_policy not in {"required_on_provider_effect", "none"}:
            raise Stage4HarnessValidationError(
                "stage4_delivery_receiver_policy_invalid"
            )

    receipts = _load_jsonl(root / "receiver_receipts.jsonl")
    receipt_ids = [str(row.get("delivery_id") or "") for row in receipts]
    if (
        len(set(receipt_ids)) != len(receipt_ids)
        or set(receipt_ids) != receiver_required_ids
        or any(
            row.get("status") != "observed"
            or _FINGERPRINT.fullmatch(
                str(row.get("receiver_observation_sha256") or "")
            )
            is None
            or isinstance(row.get("observed_elapsed_ms"), bool)
            or not isinstance(row.get("observed_elapsed_ms"), (int, float))
            or float(row["observed_elapsed_ms"])
            < float(
                next(
                    result["provider_completed_elapsed_ms"]
                    for result in telegram_results
                    if result["delivery_id"] == row["delivery_id"]
                )
            )
            for row in receipts
        )
    ):
        raise Stage4HarnessValidationError("stage4_receiver_receipt_mismatch")

    metrics = _load_jsonl(root / "queue_metrics.jsonl")
    elapsed_seconds = [row.get("elapsed_second") for row in metrics]
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in elapsed_seconds
        )
        or len(set(elapsed_seconds)) != len(elapsed_seconds)
        or not set(range(STAGE4_DURATION_SECONDS + 1)).issubset(
            set(elapsed_seconds)
        )
        or any(
            isinstance(row.get(field), bool)
            or not isinstance(row.get(field), int)
            or int(row[field]) < 0
            for row in metrics
            for field in (
                "ready_backlog",
                "leased_backlog",
                "unresolved_backlog",
                "backlog_caused_disablements_delta",
            )
        )
    ):
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
        expected_fault = catalog[case_id]
        if (
            fault.get("observed_disposition")
            != expected_fault["expected_disposition"]
            or fault.get("input_shape_sha256")
            != expected_fault["input_shape_sha256"]
            or fault.get("retry_after_source")
            != expected_fault["retry_after_source"]
            or int(fault.get("duplicate_provider_side_effect_count", -1)) != 0
            or isinstance(fault.get("provider_call_count"), bool)
            or int(fault.get("provider_call_count", -1)) != 1
            or fault.get("durable_state")
            not in expected_fault["allowed_durable_states"]
            or _FINGERPRINT.fullmatch(
                str(fault.get("durable_observation_sha256") or "")
            )
            is None
        ):
            raise Stage4HarnessValidationError("stage4_fault_result_mismatch")
        if case_id.startswith("http_429_"):
            applied = fault.get("retry_after_applied_seconds")
            if (
                isinstance(applied, bool)
                or not isinstance(applied, int)
                or (
                    expected_fault["retry_after_source"] == "provider_integer"
                    and applied
                    != (2_147_483_647 if case_id.endswith("_max") else 1)
                )
                or (
                    expected_fault["retry_after_source"]
                    == "bounded_malformed_fallback"
                    and not 1 <= applied <= 300
                )
                or fault.get("cooldown_deadline_source")
                != expected_fault["retry_after_source"]
            ):
                raise Stage4HarnessValidationError(
                    "stage4_fault_retry_after_evidence_invalid"
                )

    cleanup_plan = _load_jsonl(root / "cleanup_plan.jsonl")
    cleanup = _load_jsonl(root / "cleanup_ledger.jsonl")
    if {row.get("cleanup_id") for row in cleanup} != {
        row.get("cleanup_id") for row in cleanup_plan
    } or len(cleanup) != len(cleanup_plan) or any(
        row.get("status") != "completed"
        or row.get("observer_source") != "independent_database_observer"
        or isinstance(row.get("observer_before_count"), bool)
        or not isinstance(row.get("observer_before_count"), int)
        or int(row["observer_before_count"]) < 1
        or isinstance(row.get("observer_after_count"), bool)
        or int(row.get("observer_after_count", -1)) != 0
        or _FINGERPRINT.fullmatch(
            str(row.get("cleanup_observation_sha256") or "")
        )
        is None
        for row in cleanup
    ):
        raise Stage4HarnessValidationError("stage4_cleanup_ledger_incomplete")

    reconciliation = _load_json(root / "reconciliation.json")
    acceptance = _load_json(root / "acceptance.json")
    submit_at_by_offer = {
        str(row["business_event_id"]): int(row["at_ms"])
        for row in trace
        if row["event_type"] == "offer_submit_valid"
    }
    publication_latencies: list[float] = []
    eligible_publications = 0
    for obligation_id, obligation in obligations_by_id.items():
        if obligation["obligation_kind"] != "offer_channel_publication":
            continue
        result = results_by_obligation[obligation_id]
        submit_at = submit_at_by_offer[str(obligation["business_event_id"])]
        completed_at = float(result["provider_completed_elapsed_ms"])
        publication_latencies.append((completed_at - submit_at) / 1000.0)
        if result["outcome"] == "sent" and completed_at <= submit_at + 115_000:
            eligible_publications += 1
    callback_latencies = [
        (
            float(results_by_obligation[obligation_id]["provider_completed_elapsed_ms"])
            - float(obligation["source_at_ms"])
        )
        / 1000.0
        for obligation_id, obligation in obligations_by_id.items()
        if obligation["method"] == "answerCallbackQuery"
    ]
    backlog_disablements = sum(
        int(row["backlog_caused_disablements_delta"]) for row in metrics
    )
    expected_reconciliation = {
        "status": "clean",
        "unresolved_jobs": 0,
        "duplicate_provider_side_effects": 0,
        "missing_business_outcomes": 0,
        "missing_receiver_receipts": 0,
        "invalid_offer_publication_intents": 0,
        "cross_lane_route_mutations": 0,
        "backlog_caused_disablements": backlog_disablements,
        "delivery_obligation_count": len(obligations),
        "provider_side_effect_count": sum(provider_effect_by_delivery.values()),
    }
    if reconciliation != expected_reconciliation:
        raise Stage4HarnessValidationError("stage4_live_reconciliation_not_clean")
    thresholds = stage4_stop_thresholds()["acceptance"]
    recomputed_metrics = {
        "eligible_publication_before_deadline_ratio": (
            eligible_publications / len(publication_latencies)
        ),
        "publication_latency_p95_seconds": _nearest_rank_percentile(
            publication_latencies, 95
        ),
        "publication_latency_p99_seconds": _nearest_rank_percentile(
            publication_latencies, 99
        ),
        "callback_latency_p95_seconds": _nearest_rank_percentile(
            callback_latencies, 95
        ),
        "callback_latency_p99_seconds": _nearest_rank_percentile(
            callback_latencies, 99
        ),
        "missing_receiver_receipts": 0,
        "duplicate_receiver_receipts": 0,
        "backlog_caused_disablements": backlog_disablements,
    }
    criteria = {
        "delivery_obligation_coverage": len(telegram_results) == len(obligations),
        "eligible_publication_before_deadline": recomputed_metrics[
            "eligible_publication_before_deadline_ratio"
        ]
        >= float(thresholds["eligible_publication_before_deadline_ratio"]),
        "publication_latency_p95": recomputed_metrics[
            "publication_latency_p95_seconds"
        ]
        <= float(thresholds["publication_latency_p95_seconds_max"]),
        "publication_latency_p99": recomputed_metrics[
            "publication_latency_p99_seconds"
        ]
        <= float(thresholds["publication_latency_p99_seconds_max"]),
        "callback_latency_p95": recomputed_metrics[
            "callback_latency_p95_seconds"
        ]
        <= float(thresholds["callback_latency_p95_seconds_max"]),
        "callback_latency_p99": recomputed_metrics[
            "callback_latency_p99_seconds"
        ]
        <= float(thresholds["callback_latency_p99_seconds_max"]),
        "receiver_reconciliation": True,
        "no_backlog_caused_disablement": backlog_disablements == 0,
        "race_release_and_authoritative_outcome": True,
    }
    expected_acceptance = {
        "decision": "pass" if all(criteria.values()) else "fail",
        "stop_events": [] if all(criteria.values()) else ["acceptance_threshold_failed"],
        "input_counts": {"valid_accepted": 1800, "invalid_rejected": 400},
        "criteria": criteria,
        "metrics": recomputed_metrics,
    }
    if acceptance != expected_acceptance or acceptance["decision"] != "pass":
        raise Stage4HarnessValidationError("stage4_live_acceptance_not_clean")

    security_scan = _load_json(root / "security_scan.json")
    required_scanned_files = {
        "manifest.json",
        *STAGE4_PLAN_FILES,
        *(name for name in STAGE4_LIVE_EVIDENCE_FILES if name != "security_scan.json"),
    }
    scanned_files = security_scan.get("scanned_files")
    if (
        int(security_scan.get("schema_version", 0)) not in {2, 3}
        or not isinstance(scanned_files, list)
        or isinstance(security_scan.get("scanned_file_count"), bool)
        or int(security_scan.get("scanned_file_count", -1))
        != len(scanned_files or [])
        or _FINGERPRINT.fullmatch(
            "sha256:"
            + str(security_scan.get("scanned_file_manifest_sha256") or "")
        )
        is None
        or
        security_scan.get("status") != "clean"
        or int(security_scan.get("finding_count", -1)) != 0
        or not required_scanned_files.issubset(
            {str(name) for name in scanned_files or []}
        )
    ):
        raise Stage4HarnessValidationError("stage4_security_scan_incomplete")
    normalized_scanned_files = [str(name) for name in scanned_files]
    if len(set(normalized_scanned_files)) != len(normalized_scanned_files):
        raise Stage4HarnessValidationError("stage4_security_scan_file_invalid")
    scan_manifest_rows: list[str] = []
    for filename in normalized_scanned_files:
        relative = Path(filename)
        artifact = root / relative
        if (
            relative.is_absolute()
            or relative.name != filename
            or filename == "security_scan.json"
            or not artifact.is_file()
            or artifact.is_symlink()
        ):
            raise Stage4HarnessValidationError("stage4_security_scan_file_invalid")
        blob = artifact.read_bytes()
        scan_manifest_rows.append(
            f"{filename}\t{hashlib.sha256(blob).hexdigest()}\t{len(blob)}"
        )
    recomputed_scan_manifest = hashlib.sha256(
        ("\n".join(sorted(scan_manifest_rows)) + "\n").encode("utf-8")
    ).hexdigest()
    if security_scan.get("scanned_file_manifest_sha256") != recomputed_scan_manifest:
        raise Stage4HarnessValidationError("stage4_security_scan_manifest_mismatch")
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
