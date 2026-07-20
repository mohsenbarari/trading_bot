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

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.telegram_queue_stage4_workload import (
    STAGE4_DURATION_SECONDS,
    STAGE4_EVIDENCE_TIMELINE_SECONDS,
    Stage4Workload,
    build_stage4_workload,
    stage4_authoritative_result_constraints,
    validate_stage4_workload,
)


STAGE4_HARNESS_SCHEMA_VERSION = 3
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
    "fault_executions.jsonl",
    "fault_results.jsonl",
    "cleanup_ledger.jsonl",
    "reconciliation.json",
    "acceptance.json",
    "security_scan.json",
)
STAGE4_ROLE_EVIDENCE_FILES = {
    "sender": (
        "preflight.json",
        "telegram_results.jsonl",
        "queue_metrics.jsonl",
        "fault_executions.jsonl",
    ),
    "observer": (
        "business_outcomes.jsonl",
        "receiver_receipts.jsonl",
        "fault_results.jsonl",
        "cleanup_ledger.jsonl",
        "reconciliation.json",
        "acceptance.json",
    ),
}
STAGE4_ATTESTATION_FILES = (
    "execution_authorization.json",
    "sender_attestation.json",
    "observer_attestation.json",
)
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^stage4-[a-z0-9][a-z0-9-]{5,80}$")
_PLAN_NONCE = re.compile(r"^[0-9a-f]{64}$")
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


def stage4_observation_sha256(kind: str, row: Mapping[str, Any]) -> str:
    """Bind an observation fingerprint to the complete canonical raw record."""
    payload = {
        key: value
        for key, value in row.items()
        if key not in {
            "observation_sha256",
            "provider_observation_sha256",
            "receiver_observation_sha256",
            "durable_observation_sha256",
            "cleanup_observation_sha256",
            "adapter_observation_sha256",
        }
    }
    return "sha256:" + sha256_text(
        f"telegram-stage4-{kind}-observation-v1:" + canonical_json(payload)
    )


def stage4_authoritative_state_sha256(state: Mapping[str, Any]) -> str:
    """Return the code-owned digest of a complete synthetic offer snapshot."""
    return "sha256:" + sha256_text(
        "telegram-stage4-authoritative-offer-state-v1:" + canonical_json(state)
    )


def stage4_expected_authoritative_states(
    trace: Iterable[Mapping[str, Any]],
    outcomes_by_event: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any] | None]:
    """Reconstruct raw offer snapshots from the trace and observed winners.

    Losing concurrent requests observe the winner's terminal snapshot without
    incrementing its version.  Returning raw snapshots prevents an arbitrary,
    hash-shaped string from masquerading as authoritative state evidence.
    """
    rows = tuple(trace)
    valid_by_offer = {
        str(row["business_event_id"]): row
        for row in rows
        if row.get("event_type") == "offer_submit_valid"
    }
    expected: dict[str, dict[str, Any] | None] = {
        str(row["event_id"]): None for row in rows
    }
    lifecycle_by_offer: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row.get("event_type") in {
            "trade_request",
            "manual_expiry_request",
            "automatic_expiry",
        }:
            lifecycle_by_offer.setdefault(
                str(row["business_event_id"]), []
            ).append(row)

    def snapshot(
        *,
        offer_id: str,
        status: str,
        remaining_lots: int,
        state_version: int,
        terminal_event_id: str | None,
    ) -> dict[str, Any]:
        return {
            "business_event_id": offer_id,
            "status": status,
            "remaining_lots": remaining_lots,
            "state_version": state_version,
            "terminal_event_id": terminal_event_id,
        }

    for offer_id, submit in valid_by_offer.items():
        lot_count = int(submit["lot_count"])
        submit_id = str(submit["event_id"])
        confirm_id = f"confirm-{offer_id}"
        if confirm_id not in outcomes_by_event:
            raise Stage4HarnessValidationError(
                "stage4_authoritative_state_confirmation_missing"
            )
        expected[submit_id] = snapshot(
            offer_id=offer_id,
            status="pending_confirmation",
            remaining_lots=lot_count,
            state_version=1,
            terminal_event_id=None,
        )
        expected[confirm_id] = snapshot(
            offer_id=offer_id,
            status="active",
            remaining_lots=lot_count,
            state_version=2,
            terminal_event_id=None,
        )
        lifecycle = lifecycle_by_offer.get(offer_id, [])
        partials = [
            row
            for row in lifecycle
            if outcomes_by_event[str(row["event_id"])].get(
                "authoritative_result"
            )
            == "partial_trade_committed"
        ]
        terminals = [
            row
            for row in lifecycle
            if outcomes_by_event[str(row["event_id"])].get(
                "authoritative_result"
            )
            in {"trade_committed", "expiry_committed"}
        ]
        if len(partials) > 1 or len(terminals) != 1:
            raise Stage4HarnessValidationError(
                "stage4_authoritative_state_transition_cardinality_invalid"
            )
        partial_state: dict[str, Any] | None = None
        if partials:
            partial_event_id = str(partials[0]["event_id"])
            partial_state = snapshot(
                offer_id=offer_id,
                status="active",
                remaining_lots=max(1, lot_count - 1),
                state_version=3,
                terminal_event_id=None,
            )
            expected[partial_event_id] = partial_state
        terminal = terminals[0]
        terminal_event_id = str(terminal["event_id"])
        terminal_result = str(
            outcomes_by_event[terminal_event_id]["authoritative_result"]
        )
        terminal_state = snapshot(
            offer_id=offer_id,
            status=(
                "traded" if terminal_result == "trade_committed" else "expired"
            ),
            remaining_lots=(
                0
                if terminal_result == "trade_committed"
                else (
                    int(partial_state["remaining_lots"])
                    if partial_state is not None
                    else lot_count
                )
            ),
            state_version=4 if partial_state is not None else 3,
            terminal_event_id=terminal_event_id,
        )
        expected[terminal_event_id] = terminal_state
        winner_committed_ms = outcomes_by_event[terminal_event_id].get(
            "committed_elapsed_ms"
        )
        for row in lifecycle:
            event_id = str(row["event_id"])
            result = outcomes_by_event[event_id].get("authoritative_result")
            if result == "rejected_conflict":
                loser_committed_ms = outcomes_by_event[event_id].get(
                    "committed_elapsed_ms"
                )
                if (
                    isinstance(winner_committed_ms, bool)
                    or not isinstance(winner_committed_ms, (int, float))
                    or isinstance(loser_committed_ms, bool)
                    or not isinstance(loser_committed_ms, (int, float))
                    or float(loser_committed_ms) < float(winner_committed_ms)
                ):
                    raise Stage4HarnessValidationError(
                        "stage4_authoritative_conflict_precedes_winner"
                    )
                expected[event_id] = terminal_state
        if any(expected[str(row["event_id"])] is None for row in lifecycle):
            raise Stage4HarnessValidationError(
                "stage4_authoritative_state_transition_unresolved"
            )
    return expected


def _active_delivery_obligations(
    obligations: Iterable[Mapping[str, Any]],
    outcomes_by_event: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    active: list[dict[str, Any]] = []
    for raw in obligations:
        row = dict(raw)
        policy = str(row.get("activation_policy") or "")
        if policy == "always":
            if row.get("activation_results") != []:
                raise Stage4HarnessValidationError(
                    "stage4_delivery_activation_contract_invalid"
                )
            active.append(row)
            continue
        if policy != "authoritative_result":
            raise Stage4HarnessValidationError(
                "stage4_delivery_activation_contract_invalid"
            )
        event_id = str(row.get("activation_event_id") or "")
        allowed = row.get("activation_results")
        outcome = outcomes_by_event.get(event_id)
        if not isinstance(allowed, list) or not allowed or outcome is None:
            raise Stage4HarnessValidationError(
                "stage4_delivery_activation_contract_invalid"
            )
        if outcome.get("authoritative_result") in set(allowed):
            active.append(row)
    return tuple(active)


def _expected_receiver_rendered_offer_state(
    obligation: Mapping[str, Any],
    outcomes_by_event: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    if (
        obligation.get("destination_class") != "channel"
        or not str(obligation.get("method") or "").startswith("editMessage")
    ):
        return None
    state_event_id = str(obligation.get("required_state_event_id") or "")
    outcome = outcomes_by_event.get(state_event_id)
    state = outcome.get("authoritative_state") if outcome is not None else None
    if not isinstance(state, Mapping):
        raise Stage4HarnessValidationError(
            "stage4_receiver_rendered_state_source_invalid"
        )
    return {
        "status": state["status"],
        "remaining_lots": state["remaining_lots"],
        "action_buttons_present": state["status"] == "active",
    }


def verify_stage4_role_attestation(
    root: Path,
    *,
    role: str,
    manifest: Mapping[str, Any],
    public_key: bytes,
) -> None:
    if role not in STAGE4_ROLE_EVIDENCE_FILES or len(public_key) != 32:
        raise Stage4HarnessValidationError("stage4_evidence_trust_key_invalid")
    envelope = _load_json(root / f"{role}_attestation.json")
    if not isinstance(envelope, Mapping) or set(envelope) != {"payload", "signature"}:
        raise Stage4HarnessValidationError("stage4_evidence_attestation_invalid")
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "role",
        "run_id",
        "plan_nonce",
        "trace_sha256",
        "trust_policy_sha256",
        "network_policy_sha256",
        "driver_executable_sha256",
        "started_at",
        "completed_at",
        "files",
    }:
        raise Stage4HarnessValidationError("stage4_evidence_attestation_invalid")
    expected_files = set(STAGE4_ROLE_EVIDENCE_FILES[role])
    files = payload.get("files")
    if (
        payload.get("schema_version") != 1
        or payload.get("role") != role
        or payload.get("run_id") != manifest["run_id"]
        or payload.get("plan_nonce") != manifest["plan_nonce"]
        or payload.get("trace_sha256") != manifest["trace_sha256"]
        or payload.get("trust_policy_sha256") != manifest["trust_policy_sha256"]
        or payload.get("network_policy_sha256")
        != manifest["network_policy_fingerprints"][role]
        or _FINGERPRINT.fullmatch(
            "sha256:" + str(payload.get("driver_executable_sha256") or "")
        )
        is None
        or payload.get("driver_executable_sha256")
        != _load_json(root / "execution_authorization.json")[
            "driver_executables_sha256"
        ][role]
        or not isinstance(files, Mapping)
        or set(files) != expected_files
        or any(
            str(files[name]) != sha256_file(root / name)
            for name in expected_files
        )
    ):
        raise Stage4HarnessValidationError("stage4_evidence_attestation_mismatch")
    try:
        started = datetime.fromisoformat(str(payload["started_at"]).replace("Z", "+00:00"))
        completed = datetime.fromisoformat(str(payload["completed_at"]).replace("Z", "+00:00"))
        if (
            started.tzinfo is None
            or completed.tzinfo is None
            or completed < started
        ):
            raise ValueError("invalid attestation chronology")
        signature = base64.b64decode(str(envelope["signature"]), validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, canonical_json(payload).encode("utf-8")
        )
    except (ValueError, TypeError, KeyError, InvalidSignature) as exc:
        raise Stage4HarnessValidationError(
            "stage4_evidence_attestation_signature_invalid"
        ) from exc


def _verify_stage4_execution_authorization_evidence(
    root: Path,
    *,
    manifest: Mapping[str, Any],
    public_key: bytes,
) -> None:
    authorization = _load_json(root / "execution_authorization.json")
    expected_keys = {
        "schema_version",
        "allow_live_staging",
        "authorization_id",
        "run_id",
        "plan_nonce",
        "git_commit",
        "trace_sha256",
        "config_sha256",
        "trust_policy_sha256",
        "driver_commands_sha256",
        "driver_executables_sha256",
        "not_before",
        "expires_at",
        "signature",
    }
    if (
        not isinstance(authorization, Mapping)
        or set(authorization) != expected_keys
        or len(public_key) != 32
        or authorization.get("schema_version") != 2
        or authorization.get("allow_live_staging") is not True
        or authorization.get("run_id") != manifest["run_id"]
        or authorization.get("plan_nonce") != manifest["plan_nonce"]
        or authorization.get("git_commit") != manifest["git_commit"]
        or authorization.get("trace_sha256") != manifest["trace_sha256"]
        or authorization.get("config_sha256") != manifest["config_sha256"]
        or authorization.get("trust_policy_sha256")
        != manifest["trust_policy_sha256"]
        or not str(authorization.get("authorization_id") or "").startswith(
            "stage4-auth-"
        )
        or _FINGERPRINT.fullmatch(
            "sha256:" + str(authorization.get("driver_commands_sha256") or "")
        )
        is None
        or not isinstance(authorization.get("driver_executables_sha256"), Mapping)
        or set(authorization["driver_executables_sha256"])
        != {"sender", "observer"}
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value or "")) is None
            for value in authorization["driver_executables_sha256"].values()
        )
    ):
        raise Stage4HarnessValidationError(
            "stage4_execution_authorization_evidence_invalid"
        )
    try:
        not_before = datetime.fromisoformat(
            str(authorization["not_before"]).replace("Z", "+00:00")
        )
        expires_at = datetime.fromisoformat(
            str(authorization["expires_at"]).replace("Z", "+00:00")
        )
        if (
            not_before.tzinfo is None
            or expires_at.tzinfo is None
            or expires_at <= not_before
            or (expires_at - not_before).total_seconds() > 3600
        ):
            raise ValueError("invalid authorization chronology")
    except (KeyError, TypeError, ValueError) as exc:
        raise Stage4HarnessValidationError(
            "stage4_execution_authorization_evidence_invalid"
        ) from exc
    payload = {key: value for key, value in authorization.items() if key != "signature"}
    try:
        signature = base64.b64decode(str(authorization["signature"]), validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, canonical_json(payload).encode("utf-8")
        )
    except (ValueError, TypeError, KeyError, InvalidSignature) as exc:
        raise Stage4HarnessValidationError(
            "stage4_execution_authorization_evidence_signature_invalid"
        ) from exc


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
    trust_policy_sha256 = str(config.get("trust_policy_sha256") or "")
    if _FINGERPRINT.fullmatch(trust_policy_sha256) is None:
        raise Stage4HarnessValidationError("stage4_trust_policy_fingerprint_invalid")
    plan_nonce = str(config.get("plan_nonce") or "")
    if _PLAN_NONCE.fullmatch(plan_nonce) is None:
        raise Stage4HarnessValidationError("stage4_plan_nonce_invalid")
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
        "trust_policy_sha256": trust_policy_sha256,
        "plan_nonce": plan_nonce,
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
    retry_values: dict[str, Any] = {
        "http_429_integer_one": 1,
        "http_429_integer_max": 2_147_483_647,
        "http_429_boolean_retry_after": True,
        "http_429_string_retry_after": "1",
        "http_429_fractional_retry_after": 0.5,
        "http_429_zero_retry_after": 0,
        "http_429_negative_retry_after": -1,
        "http_429_integer_overflow": 2_147_483_648,
        "http_429_huge_integer": 9_223_372_036_854_775_808,
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
    result: list[dict[str, Any]] = []
    for case_id, expected in rows:
        if case_id.startswith("http_429_"):
            response_parameters = (
                {}
                if case_id == "http_429_missing_retry_after"
                else {"retry_after": retry_values[case_id]}
            )
            injected_input = {
                "http_status": 429,
                "provider_body": {
                    "ok": False,
                    "error_code": 429,
                    "response_parameters": response_parameters,
                },
            }
        else:
            injected_input = {
                "controlled_fault_class": case_id,
                "provider_body_kind": retry_shapes.get(case_id, case_id),
            }
        result.append({
            "case_id": case_id,
            "expected_disposition": expected,
            "required": True,
            "fault_injection_only": case_id not in {"ok_send_with_message_id", "ok_edit"},
            "injected_input": injected_input,
            "input_shape_sha256": "sha256:" + sha256_text(
                "telegram-stage4-fault-shape-v2:" + canonical_json(injected_input)
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
        })
    return tuple(result)


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
    event_rows = tuple(events)
    constraints = stage4_authoritative_result_constraints(event_rows)
    rows: list[dict[str, Any]] = []
    for event in event_rows:
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
                **constraints[str(event["event_id"])],
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
        receiver_policy: str = "required_for_sent_or_noop",
        ordinal: int = 1,
        causal_dependency_ids: Sequence[str] = (),
        activation_results: Sequence[str] = (),
        required_state_event_id: str | None = None,
    ) -> None:
        source = by_id[source_event_id]
        activation_policy = (
            "authoritative_result" if activation_results else "always"
        )
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
                "source_version": f"domain-event:{source_event_id}",
                "causal_dependency_ids": list(causal_dependency_ids),
                "activation_policy": activation_policy,
                "activation_event_id": source_event_id,
                "activation_results": list(activation_results),
                "required_state_event_id": required_state_event_id,
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
                required_state_event_id=event_id,
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
                    f"delivery:preview:submit-{event['business_event_id']}",
                ),
                required_state_event_id=event_id,
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
                required_state_event_id=event_id,
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
            for ordinal in (1, 2):
                add(
                    obligation_id=f"trade-party:{event_id}:{ordinal}",
                    source_event_id=event_id,
                    kind="trade_party_private_notification",
                    method="sendMessage",
                    bot_role="primary",
                    destination_class="private",
                    response_catalog_id="trade.party.notification",
                    ordinal=ordinal,
                    activation_results=(
                        "partial_trade_committed",
                        "trade_committed",
                    ),
                    causal_dependency_ids=(f"event:{event_id}",),
                    required_state_event_id=event_id,
                )
            if event.get("partial") is True:
                add(
                    obligation_id=f"partial-edit:{event_id}",
                    source_event_id=event_id,
                    kind="offer_channel_partial_edit",
                    method="editMessageText",
                    bot_role=editor_role,
                    destination_class="channel",
                    response_catalog_id="offer.channel.partial",
                    allowed_outcomes=("sent", "sent_noop", "superseded"),
                    activation_results=("partial_trade_committed",),
                    causal_dependency_ids=(
                        f"delivery:publication:confirm-{event['business_event_id']}",
                        f"event:{event_id}",
                    ),
                    required_state_event_id=event_id,
                )
            else:
                add(
                    obligation_id=f"terminal-edit:{event_id}",
                    source_event_id=event_id,
                    kind="offer_channel_trade_terminal_edit",
                    method="editMessageText",
                    bot_role=editor_role,
                    destination_class="channel",
                    response_catalog_id="offer.channel.terminal",
                    allowed_outcomes=("sent", "sent_noop"),
                    activation_results=("trade_committed",),
                    causal_dependency_ids=(
                        f"delivery:publication:confirm-{event['business_event_id']}",
                        f"event:{event_id}",
                    ),
                    required_state_event_id=event_id,
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
            add(
                obligation_id=f"terminal-edit:{event_id}",
                source_event_id=event_id,
                kind="offer_channel_manual_expiry_edit",
                method="editMessageText",
                bot_role=editor_role,
                destination_class="channel",
                response_catalog_id="offer.channel.terminal",
                allowed_outcomes=("sent", "sent_noop"),
                activation_results=("expiry_committed",),
                causal_dependency_ids=(
                    f"delivery:publication:confirm-{event['business_event_id']}",
                    f"event:{event_id}",
                ),
                required_state_event_id=event_id,
            )
        elif event_type == "automatic_expiry":
            add(
                obligation_id=f"terminal-edit:{event_id}",
                source_event_id=event_id,
                kind="offer_channel_automatic_expiry_edit",
                method="editMessageText",
                bot_role=editor_role,
                destination_class="channel",
                response_catalog_id="offer.channel.terminal",
                allowed_outcomes=("sent", "sent_noop"),
                activation_results=("expiry_committed",),
                causal_dependency_ids=(
                    f"delivery:publication:confirm-{event['business_event_id']}",
                    f"event:{event_id}",
                ),
                required_state_event_id=event_id,
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
        elif event_type == "market_status_notice":
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
            "plan_nonce": safe["plan_nonce"],
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
            "trust_policy_sha256": safe["trust_policy_sha256"],
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
    if _PLAN_NONCE.fullmatch(str(manifest.get("plan_nonce") or "")) is None:
        raise Stage4HarnessValidationError("stage4_manifest_plan_nonce_invalid")
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


def _verify_stage4_live_evidence_unchecked(
    output_dir: Path,
    *,
    evidence_public_keys: Mapping[str, bytes],
) -> dict[str, Any]:
    """Fail closed unless sender, receiver, cleanup and reconciliation agree."""
    root = Path(output_dir)
    manifest = verify_stage4_plan(root)
    if set(evidence_public_keys) != {"authorization", "sender", "observer"}:
        raise Stage4HarnessValidationError("stage4_evidence_trust_keys_required")
    for filename in STAGE4_ATTESTATION_FILES:
        path = root / filename
        if not path.is_file() or path.is_symlink():
            raise Stage4HarnessValidationError(
                f"stage4_live_evidence_missing:{filename}"
            )
    _verify_stage4_execution_authorization_evidence(
        root,
        manifest=manifest,
        public_key=evidence_public_keys["authorization"],
    )
    for role in ("sender", "observer"):
        verify_stage4_role_attestation(
            root,
            role=role,
            manifest=manifest,
            public_key=evidence_public_keys[role],
        )
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
    valid_offer_ids = {
        str(row["business_event_id"])
        for row in trace
        if row["event_type"] == "offer_submit_valid"
    }
    for event_id, expected_row in expected_by_event.items():
        observed = outcomes_by_event[event_id]
        allowed_results = set(expected_row["allowed_authoritative_results"])
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
        state_required = str(expected_row["business_event_id"]) in valid_offer_ids
        expected_fields = {
            "event_id",
            "business_event_id",
            "event_type",
            "observed_disposition",
            "offer_delta",
            "publication_intent_delta",
            "response_catalog_id",
            "status",
            "observer_source",
            "committed_elapsed_ms",
            "observed_at_ms",
            "authoritative_result",
            "state_version",
            "authoritative_state",
            "state_sha256",
            "observation_sha256",
        }
        if expected_row["authoritative_group_id"] is not None:
            expected_fields.add("sender_release_elapsed_ms")
        committed_at_ms = observed.get("committed_elapsed_ms")
        if (
            set(observed) != expected_fields
            or
            observed.get("observer_source") != "independent_database_observer"
            or isinstance(observed_at_ms, bool)
            or not isinstance(observed_at_ms, (int, float))
            or isinstance(committed_at_ms, bool)
            or not isinstance(committed_at_ms, (int, float))
            or float(committed_at_ms) < float(trace_by_event[event_id]["at_ms"])
            or float(observed_at_ms) < float(committed_at_ms)
            or observed.get("authoritative_result") not in allowed_results
            or (
                state_required
                and (
                    not str(observed.get("state_version") or "").startswith(
                        "state:"
                    )
                    or not isinstance(observed.get("authoritative_state"), Mapping)
                    or _FINGERPRINT.fullmatch(
                        str(observed.get("state_sha256") or "")
                    )
                    is None
                )
            )
            or (
                not state_required
                and (
                    observed.get("state_version") is not None
                    or observed.get("authoritative_state") is not None
                    or observed.get("state_sha256") is not None
                )
            )
            or observed.get("observation_sha256")
            != stage4_observation_sha256("business", observed)
        ):
            raise Stage4HarnessValidationError(
                "stage4_business_observer_evidence_invalid"
            )

    competition_groups: dict[str, list[dict[str, Any]]] = {}
    for event_id, expected_row in expected_by_event.items():
        group_id = expected_row.get("authoritative_group_id")
        if group_id is not None:
            competition_groups.setdefault(str(group_id), []).append(
                trace_by_event[event_id]
            )
    if len(competition_groups) != 162:
        raise Stage4HarnessValidationError(
            "stage4_live_competition_group_count_invalid"
        )
    for group_id, group in competition_groups.items():
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
        group_rule = str(
            expected_by_event[str(group[0]["event_id"])][
                "authoritative_group_rule"
            ]
        )
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
        if (
            group_rule in {"trade", "exactly_one_trade"}
            and (committed_trades, committed_expiries) != (1, 0)
        ) or (
            group_rule == "expiry"
            and (committed_trades, committed_expiries) != (0, 1)
        ) or group_rule not in {"trade", "expiry", "exactly_one_trade"}:
            raise Stage4HarnessValidationError(
                f"stage4_live_competition_authoritative_outcome_invalid:{group_id}"
            )

    expected_states = stage4_expected_authoritative_states(
        trace, outcomes_by_event
    )
    for event_id, state in expected_states.items():
        observed = outcomes_by_event[event_id]
        if state is None:
            if (
                observed.get("state_version") is not None
                or observed.get("authoritative_state") is not None
                or observed.get("state_sha256") is not None
            ):
                raise Stage4HarnessValidationError(
                    "stage4_non_offer_authoritative_state_present"
                )
            continue
        if (
            observed.get("authoritative_state") != state
            or observed.get("state_version") != f"state:{state['state_version']}"
            or observed.get("state_sha256")
            != stage4_authoritative_state_sha256(state)
        ):
            raise Stage4HarnessValidationError(
                "stage4_authoritative_state_snapshot_mismatch"
            )

    terminal_by_offer: dict[str, list[str]] = {}
    for event_id, observed in outcomes_by_event.items():
        if observed.get("authoritative_result") in {
            "trade_committed",
            "expiry_committed",
        }:
            terminal_by_offer.setdefault(
                str(observed["business_event_id"]), []
            ).append(event_id)
    if set(terminal_by_offer) != valid_offer_ids or any(
        len(event_ids) != 1 for event_ids in terminal_by_offer.values()
    ):
        raise Stage4HarnessValidationError(
            "stage4_live_terminal_state_cardinality_invalid"
        )

    obligations = _load_jsonl(root / "delivery_obligations.jsonl")
    active_obligations = _active_delivery_obligations(
        obligations, outcomes_by_event
    )
    obligations_by_id = {
        str(row["obligation_id"]): row for row in active_obligations
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
    results_by_delivery = {
        str(row.get("delivery_id") or ""): row for row in telegram_results
    }
    results_by_obligation = {
        str(row.get("obligation_id") or ""): row for row in telegram_results
    }
    if (
        not telegram_results
        or not all(result_ids)
        or len(set(result_ids)) != len(result_ids)
        or len(results_by_delivery) != len(telegram_results)
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
            "source_state_sha256",
            "payload_sha256",
            "provider_message_fingerprint",
            "target_message_fingerprint",
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
        required_state_event_id = obligation.get("required_state_event_id")
        required_state_sha256 = (
            outcomes_by_event[str(required_state_event_id)]["state_sha256"]
            if required_state_event_id is not None
            else None
        )
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
        method = str(obligation["method"])
        provider_message_fingerprint = row.get("provider_message_fingerprint")
        target_message_fingerprint = row.get("target_message_fingerprint")
        payload_sha256 = row.get("payload_sha256")
        message_identity_invalid = (
            (
                method == "sendMessage"
                and (
                    outcome == "sent"
                    and (
                        _FINGERPRINT.fullmatch(
                            str(provider_message_fingerprint or "")
                        )
                        is None
                    )
                    or target_message_fingerprint is not None
                )
            )
            or (
                method.startswith("editMessage")
                and (
                    _FINGERPRINT.fullmatch(
                        str(target_message_fingerprint or "")
                    )
                    is None
                    or (
                        outcome == "sent"
                        and provider_message_fingerprint
                        != target_message_fingerprint
                    )
                    or (
                        outcome != "sent"
                        and provider_message_fingerprint is not None
                    )
                )
            )
            or (
                method == "answerCallbackQuery"
                and (
                    provider_message_fingerprint is not None
                    or target_message_fingerprint is not None
                )
            )
        )
        target_delivery_dependency = next(
            (
                dependency.removeprefix("delivery:")
                for dependency in obligation["causal_dependency_ids"]
                if dependency.startswith("delivery:")
            ),
            None,
        )
        if method.startswith("editMessage") and target_delivery_dependency is not None:
            dependency_message_fingerprint = results_by_obligation[
                target_delivery_dependency
            ].get("provider_message_fingerprint")
            message_identity_invalid = (
                message_identity_invalid
                or target_message_fingerprint
                != dependency_message_fingerprint
            )
        if (
            set(row) != expected_result_fields
            or any(row.get(key) != value for key, value in expected_fields.items())
            or outcome not in set(obligation["allowed_outcomes"])
            or isinstance(enqueued_at, bool)
            or not isinstance(enqueued_at, (int, float))
            or isinstance(completed_at, bool)
            or not isinstance(completed_at, (int, float))
            or float(enqueued_at) < float(obligation["source_at_ms"])
            or (
                required_state_event_id is not None
                and float(enqueued_at)
                < float(
                    outcomes_by_event[str(required_state_event_id)][
                        "committed_elapsed_ms"
                    ]
                )
            )
            or float(completed_at) < float(enqueued_at)
            or causal_order_invalid
            or isinstance(row.get("provider_side_effect_count"), bool)
            or int(row.get("provider_side_effect_count", -1)) != provider_effect
            or row.get("source_state_sha256") != required_state_sha256
            or _FINGERPRINT.fullmatch(str(payload_sha256 or "")) is None
            or message_identity_invalid
            or row.get("provider_observation_sha256")
            != stage4_observation_sha256("provider", row)
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
        if receiver_policy == "required_for_sent_or_noop" and outcome in {
            "sent",
            "sent_noop",
        }:
            receiver_required_ids.add(delivery_id)
        elif receiver_policy not in {"required_for_sent_or_noop", "none"}:
            raise Stage4HarnessValidationError(
                "stage4_delivery_receiver_policy_invalid"
            )

    receipts = _load_jsonl(root / "receiver_receipts.jsonl")
    receipt_ids = [str(row.get("delivery_id") or "") for row in receipts]
    if (
        len(set(receipt_ids)) != len(receipt_ids)
        or set(receipt_ids) != receiver_required_ids
        or any(
            set(row)
            != {
                "delivery_id",
                "status",
                "observed_elapsed_ms",
                "message_fingerprint",
                "state_sha256",
                "payload_sha256",
                "rendered_offer_state",
                "receiver_observation_sha256",
            }
            or row.get("status") != "observed"
            or isinstance(row.get("observed_elapsed_ms"), bool)
            or not isinstance(row.get("observed_elapsed_ms"), (int, float))
            or float(row["observed_elapsed_ms"])
            < float(results_by_delivery[str(row["delivery_id"])]["provider_completed_elapsed_ms"])
            or row.get("message_fingerprint")
            != (
                results_by_delivery[str(row["delivery_id"])][
                    "target_message_fingerprint"
                ]
                if str(
                    results_by_delivery[str(row["delivery_id"])]["method"]
                ).startswith("editMessage")
                else results_by_delivery[str(row["delivery_id"])][
                    "provider_message_fingerprint"
                ]
            )
            or row.get("state_sha256")
            != results_by_delivery[str(row["delivery_id"])]["source_state_sha256"]
            or row.get("payload_sha256")
            != results_by_delivery[str(row["delivery_id"])]["payload_sha256"]
            or row.get("rendered_offer_state")
            != _expected_receiver_rendered_offer_state(
                obligations_by_id[
                    results_by_delivery[str(row["delivery_id"])]["obligation_id"]
                ],
                outcomes_by_event,
            )
            or row.get("receiver_observation_sha256")
            != stage4_observation_sha256("receiver", row)
            for row in receipts
        )
    ):
        raise Stage4HarnessValidationError("stage4_receiver_receipt_mismatch")

    # A partial edit is useful only while its partial state is authoritative.
    # If completion wins before the edit reaches Telegram, it must be recorded
    # as superseded rather than displaying stale remaining lots.
    terminal_commit_by_offer = {
        str(row["business_event_id"]): float(row["committed_elapsed_ms"])
        for row in outcomes
        if row["authoritative_result"] in {"trade_committed", "expiry_committed"}
    }
    if any(
        obligation["obligation_kind"] == "offer_channel_partial_edit"
        and result["outcome"] in {"sent", "sent_noop"}
        and float(result["provider_completed_elapsed_ms"])
        >= terminal_commit_by_offer[str(obligation["business_event_id"])]
        for obligation_id, obligation in obligations_by_id.items()
        for result in (results_by_obligation[obligation_id],)
    ):
        raise Stage4HarnessValidationError(
            "stage4_partial_edit_after_terminal_state"
        )

    metrics = _load_jsonl(root / "queue_metrics.jsonl")
    elapsed_seconds = [row.get("elapsed_second") for row in metrics]
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in elapsed_seconds
        )
        or len(set(elapsed_seconds)) != len(elapsed_seconds)
        or not set(range(STAGE4_EVIDENCE_TIMELINE_SECONDS + 1)).issubset(
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

    catalog = {row["case_id"]: row for row in stage4_fault_catalog()}
    fault_executions = _load_jsonl(root / "fault_executions.jsonl")
    execution_by_id = {
        str(row.get("case_id") or ""): row for row in fault_executions
    }
    if (
        len(execution_by_id) != len(fault_executions)
        or set(execution_by_id) != set(catalog)
    ):
        raise Stage4HarnessValidationError("stage4_fault_result_catalog_mismatch")
    for case_id, execution in execution_by_id.items():
        expected_fault = catalog[case_id]
        if (
            set(execution)
            != {
                "case_id",
                "observed_disposition",
                "injected_input",
                "input_shape_sha256",
                "retry_after_source",
                "duplicate_provider_side_effect_count",
                "provider_call_count",
                "adapter_observation_sha256",
            }
            or execution.get("observed_disposition")
            != expected_fault["expected_disposition"]
            or execution.get("injected_input") != expected_fault["injected_input"]
            or execution.get("input_shape_sha256")
            != expected_fault["input_shape_sha256"]
            or execution.get("input_shape_sha256")
            != "sha256:" + sha256_text(
                "telegram-stage4-fault-shape-v2:"
                + canonical_json(execution["injected_input"])
            )
            or execution.get("retry_after_source")
            != expected_fault["retry_after_source"]
            or isinstance(
                execution.get("duplicate_provider_side_effect_count"), bool
            )
            or int(execution.get("duplicate_provider_side_effect_count", -1)) != 0
            or isinstance(execution.get("provider_call_count"), bool)
            or int(execution.get("provider_call_count", -1)) != 1
            or execution.get("adapter_observation_sha256")
            != stage4_observation_sha256("adapter", execution)
        ):
            raise Stage4HarnessValidationError("stage4_fault_execution_mismatch")

    fault_results = _load_jsonl(root / "fault_results.jsonl")
    fault_by_id = {str(row.get("case_id") or ""): row for row in fault_results}
    if len(fault_by_id) != len(fault_results) or set(fault_by_id) != set(catalog):
        raise Stage4HarnessValidationError("stage4_fault_result_catalog_mismatch")
    for case_id, fault in fault_by_id.items():
        expected_fault = catalog[case_id]
        expected_fields = {
            "case_id",
            "observer_source",
            "durable_state",
            "durable_record",
            "durable_observation_sha256",
        }
        if case_id.startswith("http_429_"):
            expected_fields.update(
                {"retry_after_applied_seconds", "cooldown_deadline_source"}
            )
        durable_record = fault.get("durable_record")
        if (
            set(fault) != expected_fields
            or fault.get("observer_source") != "independent_database_observer"
            or fault.get("durable_state")
            not in expected_fault["allowed_durable_states"]
            or not isinstance(durable_record, Mapping)
            or durable_record.get("state") != fault.get("durable_state")
            or isinstance(
                durable_record.get("duplicate_provider_side_effect_count"), bool
            )
            or durable_record.get("duplicate_provider_side_effect_count") != 0
            or durable_record.get("retry_after_source")
            != expected_fault["retry_after_source"]
            or fault.get("durable_observation_sha256")
            != stage4_observation_sha256("durable", fault)
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
                or durable_record.get("retry_after_applied_seconds") != applied
                or durable_record.get("cooldown_deadline_source")
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
        or row.get("cleanup_observation_sha256")
        != stage4_observation_sha256("cleanup", row)
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
        "delivery_obligation_count": len(active_obligations),
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
        "delivery_obligation_coverage": len(telegram_results)
        == len(active_obligations),
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
    exact_inventory = {
        "manifest.json",
        *STAGE4_PLAN_FILES,
        *STAGE4_LIVE_EVIDENCE_FILES,
        *STAGE4_ATTESTATION_FILES,
    }
    actual_entries = {entry.name for entry in root.iterdir()}
    if actual_entries != exact_inventory or any(
        not (root / name).is_file() or (root / name).is_symlink()
        for name in exact_inventory
    ):
        raise Stage4HarnessValidationError("stage4_evidence_inventory_invalid")
    # The verifier, not either driver, executes the scanner over every
    # evidence input.  security_scan.json is the deterministic report of that
    # operation and is the sole self-excluded artifact.
    from scripts.scan_telegram_queue_artifacts import scan_paths

    scan_targets = [
        root / name for name in sorted(exact_inventory - {"security_scan.json"})
    ]
    recomputed_scan = scan_paths(scan_targets)
    compared_keys = {
        "schema_version",
        "status",
        "scanned_file_count",
        "scanned_files",
        "scanned_file_manifest_sha256",
        "finding_count",
        "findings",
    }
    if (
        recomputed_scan.get("status") != "clean"
        or int(recomputed_scan.get("finding_count", -1)) != 0
        or any(
            security_scan.get(key) != recomputed_scan.get(key)
            for key in compared_keys
        )
        or security_scan.get("self_excluded_file") != "security_scan.json"
        or security_scan.get("self_excluded_reason")
        != "verifier_generated_recursive_manifest"
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


def verify_stage4_live_evidence(
    output_dir: Path,
    *,
    evidence_public_keys: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Validate untrusted driver artifacts without leaking parser failures."""
    try:
        if evidence_public_keys is None:
            raise Stage4HarnessValidationError(
                "stage4_evidence_trust_keys_required"
            )
        return _verify_stage4_live_evidence_unchecked(
            output_dir,
            evidence_public_keys=evidence_public_keys,
        )
    except Stage4HarnessValidationError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise Stage4HarnessValidationError(
            "stage4_live_evidence_schema_invalid"
        ) from exc
