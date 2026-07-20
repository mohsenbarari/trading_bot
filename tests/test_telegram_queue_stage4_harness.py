import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import telegram_queue_stage4_harness as harness
from core.telegram_queue_stage4_harness import (
    STAGE4_ATTESTATION_FILES,
    STAGE4_LIVE_EVIDENCE_FILES,
    STAGE4_PLAN_FILES,
    STAGE4_ROLE_EVIDENCE_FILES,
    Stage4HarnessValidationError,
    canonical_json,
    sha256_file,
    sha256_text,
    stage4_bound_value_fingerprint,
    stage4_config_binding_sha256,
    stage4_authoritative_state_sha256,
    stage4_fault_catalog,
    stage4_observation_sha256,
    validate_stage4_run_config,
    verify_stage4_live_evidence,
    verify_stage4_plan,
    write_stage4_plan,
)
from core.telegram_queue_stage4_workload import STAGE4_EVIDENCE_TIMELINE_SECONDS
from scripts import run_telegram_queue_stage4_harness as runner


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


def safe_config(*, environment="synthetic-test", provider=False):
    return {
        "schema_version": 3,
        "environment": environment,
        "run_id": "stage4-safe-test-run",
        "plan_nonce": "a" * 64,
        "trust_policy_sha256": _fingerprint("b"),
        "bot_mode": "primary-and-channel-editor",
        "provider_network_enabled": provider,
        "staging_fingerprints": {
            "database": _fingerprint("1"),
            "redis": _fingerprint("2"),
            "primary_bot": _fingerprint("3"),
            "channel_editor_bot": _fingerprint("4"),
            "channel": _fingerprint("5"),
            "observer_database": _fingerprint("8"),
            "receiver_session": _fingerprint("9"),
        },
        "production_fingerprints": {
            "database": _fingerprint("c"),
            "redis": _fingerprint("d"),
            "primary_bot": _fingerprint("e"),
            "channel_editor_bot": _fingerprint("f"),
            "channel": _fingerprint("0"),
            "observer_database": _fingerprint("6"),
            "receiver_session": _fingerprint("7"),
        },
        "network_policy_fingerprints": {
            "sender": _fingerprint("a"),
            "observer": _fingerprint("9"),
        },
        "runtime_limits": {
            "staging_max_active_offers": 10,
            "default_max_active_offers": 4,
            "production_max_active_offers": 4,
            "offer_expiry_seconds": 120,
        },
    }


FIXTURE = {
    "schema_version": 1,
    "source": "sanitized-test",
    "commodities": [
        {"key": "commodity-a", "active": True},
        {"key": "commodity-b", "active": True},
    ],
}


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows) -> None:
    path.write_text(
        "".join(canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _independent_reference_states(trace, outcomes_by_event):
    """Small test-only state machine, intentionally independent of production."""
    states = {row["event_id"]: None for row in trace}
    valid = {
        row["business_event_id"]: row
        for row in trace
        if row["event_type"] == "offer_submit_valid"
    }
    lifecycle = {}
    for row in trace:
        if row["event_type"] in {
            "trade_request",
            "manual_expiry_request",
            "automatic_expiry",
        }:
            lifecycle.setdefault(row["business_event_id"], []).append(row)

    def snapshot(offer_id, status, remaining, version, terminal_event_id=None):
        return {
            "business_event_id": offer_id,
            "status": status,
            "remaining_lots": remaining,
            "state_version": version,
            "terminal_event_id": terminal_event_id,
        }

    for offer_id, submit in valid.items():
        lots = int(submit["lot_count"])
        states[submit["event_id"]] = snapshot(
            offer_id, "pending_confirmation", lots, 1
        )
        states[f"confirm-{offer_id}"] = snapshot(offer_id, "active", lots, 2)
        events = lifecycle[offer_id]
        partials = [
            row
            for row in events
            if outcomes_by_event[row["event_id"]]["authoritative_result"]
            == "partial_trade_committed"
        ]
        terminals = [
            row
            for row in events
            if outcomes_by_event[row["event_id"]]["authoritative_result"]
            in {"trade_committed", "expiry_committed"}
        ]
        if len(partials) > 1 or len(terminals) != 1:
            raise AssertionError("independent reference transition cardinality")
        remaining = lots
        version = 2
        if partials:
            remaining -= 1
            version += 1
            states[partials[0]["event_id"]] = snapshot(
                offer_id, "active", remaining, version
            )
        terminal = terminals[0]
        terminal_id = terminal["event_id"]
        terminal_result = outcomes_by_event[terminal_id]["authoritative_result"]
        version += 1
        final = snapshot(
            offer_id,
            "traded" if terminal_result == "trade_committed" else "expired",
            0 if terminal_result == "trade_committed" else remaining,
            version,
            terminal_id,
        )
        states[terminal_id] = final
        for row in events:
            if (
                outcomes_by_event[row["event_id"]]["authoritative_result"]
                == "rejected_conflict"
            ):
                states[row["event_id"]] = final
        if any(states[row["event_id"]] is None for row in events):
            raise AssertionError("independent reference unresolved transition")
    return states


def _raw_public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _role_attestation(
    root: Path,
    *,
    role: str,
    private_key: Ed25519PrivateKey,
    driver_hash: str,
) -> None:
    manifest = _read_json(root / "manifest.json")
    payload = {
        "schema_version": 1,
        "role": role,
        "run_id": manifest["run_id"],
        "plan_nonce": manifest["plan_nonce"],
        "trace_sha256": manifest["trace_sha256"],
        "trust_policy_sha256": manifest["trust_policy_sha256"],
        "network_policy_sha256": manifest["network_policy_fingerprints"][role],
        "driver_executable_sha256": driver_hash,
        "started_at": "2026-07-19T00:00:00+00:00",
        "completed_at": "2026-07-19T00:20:00+00:00",
        "files": {
            name: sha256_file(root / name)
            for name in STAGE4_ROLE_EVIDENCE_FILES[role]
        },
    }
    _write_json(
        root / f"{role}_attestation.json",
        {
            "payload": payload,
            "signature": base64.b64encode(
                private_key.sign(canonical_json(payload).encode("utf-8"))
            ).decode("ascii"),
        },
    )


def _refresh_integrity(root: Path, keys) -> None:
    authorization = _read_json(root / "execution_authorization.json")
    for role in ("sender", "observer"):
        _role_attestation(
            root,
            role=role,
            private_key=keys[role],
            driver_hash=authorization["driver_executables_sha256"][role],
        )
    (root / "security_scan.json").unlink(missing_ok=True)
    runner._write_security_scan(root)


def _write_passing_live_evidence(root: Path):
    manifest = _read_json(root / "manifest.json")
    expected = _read_jsonl(root / "expected_business_ledger.jsonl")
    trace = _read_jsonl(root / "event_trace.jsonl")
    trace_by_event = {row["event_id"]: row for row in trace}
    obligation_templates = _read_jsonl(root / "delivery_obligations.jsonl")
    cleanup_plan = _read_jsonl(root / "cleanup_plan.jsonl")

    private_keys = {
        "authorization": Ed25519PrivateKey.generate(),
        "sender": Ed25519PrivateKey.generate(),
        "observer": Ed25519PrivateKey.generate(),
    }
    public_keys = {name: _raw_public_key(key) for name, key in private_keys.items()}
    driver_hashes = {"sender": "1" * 64, "observer": "2" * 64}
    now = datetime.now(timezone.utc)
    authorization = {
        "schema_version": 2,
        "allow_live_staging": True,
        "authorization_id": "stage4-auth-passing-fixture",
        "run_id": manifest["run_id"],
        "plan_nonce": manifest["plan_nonce"],
        "git_commit": manifest["git_commit"],
        "trace_sha256": manifest["trace_sha256"],
        "config_sha256": manifest["config_sha256"],
        "trust_policy_sha256": manifest["trust_policy_sha256"],
        "driver_commands_sha256": "3" * 64,
        "driver_executables_sha256": driver_hashes,
        "not_before": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
    }
    authorization["signature"] = base64.b64encode(
        private_keys["authorization"].sign(
            canonical_json(authorization).encode("utf-8")
        )
    ).decode("ascii")
    _write_json(root / "execution_authorization.json", authorization)

    _write_json(
        root / "preflight.json",
        {
            "status": "pass",
            "run_id": manifest["run_id"],
            "trace_sha256": manifest["trace_sha256"],
            "bot_mode": manifest["bot_mode"],
            "production_fingerprint_collision_count": 0,
            "observer_provider_network_call_count": 0,
            "primary_publication_ready": True,
            "channel_editor_edit_ready": True,
            "channel_editor_send_allowed": False,
            "channel_editor_delete_allowed": False,
            "runtime_limits": manifest["runtime_limits"],
            "sender_network_policy_sha256": manifest["network_policy_fingerprints"]["sender"],
            "observer_network_policy_sha256": manifest["network_policy_fingerprints"]["observer"],
            "routing_policy_sha256": _fingerprint("f"),
        },
    )

    groups = {}
    for row in expected:
        if row["authoritative_group_id"] is not None:
            groups.setdefault(row["authoritative_group_id"], []).append(row)
    group_winners = {}
    group_release = {}
    for group_id, rows in groups.items():
        rule = rows[0]["authoritative_group_rule"]
        candidates = sorted(
            (
                row
                for row in rows
                if row["event_type"]
                == ("manual_expiry_request" if rule == "expiry" else "trade_request")
            ),
            key=lambda row: row["event_id"],
        )
        group_winners[group_id] = candidates[0]["event_id"]
        group_release[group_id] = max(
            int(trace_by_event[row["event_id"]]["at_ms"]) for row in rows
        ) + 10

    outcomes = []
    for row in expected:
        event = trace_by_event[row["event_id"]]
        group_id = row["authoritative_group_id"]
        allowed = row["allowed_authoritative_results"]
        if group_id is None:
            authoritative_result = allowed[0]
        elif row["event_id"] == group_winners[group_id]:
            authoritative_result = (
                "expiry_committed"
                if row["event_type"] == "manual_expiry_request"
                else "trade_committed"
            )
        else:
            authoritative_result = "rejected_conflict"
        outcome = {
            "event_id": row["event_id"],
            "business_event_id": row["business_event_id"],
            "event_type": row["event_type"],
            "observed_disposition": row["expected_disposition"],
            "offer_delta": row["expected_offer_delta"],
            "publication_intent_delta": row["expected_publication_intent_delta"],
            "response_catalog_id": row["response_catalog_id"],
            "status": "resolved",
            "observer_source": "independent_database_observer",
            "committed_elapsed_ms": int(event["at_ms"]) + 10,
            "observed_at_ms": int(event["at_ms"]) + 20,
            "authoritative_result": authoritative_result,
            "state_version": None,
            "authoritative_state": None,
            "state_sha256": None,
        }
        if group_id is not None:
            outcome["sender_release_elapsed_ms"] = group_release[group_id]
        outcomes.append(outcome)
    outcomes_by_event = {row["event_id"]: row for row in outcomes}
    expected_states = _independent_reference_states(trace, outcomes_by_event)
    for outcome in outcomes:
        state = expected_states[outcome["event_id"]]
        if state is not None:
            outcome["state_version"] = f"state:{state['state_version']}"
            outcome["authoritative_state"] = state
            outcome["state_sha256"] = stage4_authoritative_state_sha256(state)
        outcome["observation_sha256"] = stage4_observation_sha256(
            "business", outcome
        )
    _write_jsonl(root / "business_outcomes.jsonl", outcomes)
    active = [
        row
        for row in obligation_templates
        if row["activation_policy"] == "always"
        or outcomes_by_event[row["activation_event_id"]]["authoritative_result"]
        in row["activation_results"]
    ]

    pending = {row["obligation_id"]: row for row in active}
    results_by_obligation = {}
    message_counter = 1000
    while pending:
        progressed = False
        for obligation_id, row in tuple(pending.items()):
            delivery_dependencies = [
                value.removeprefix("delivery:")
                for value in row["causal_dependency_ids"]
                if value.startswith("delivery:")
            ]
            if any(value not in results_by_obligation for value in delivery_dependencies):
                continue
            message_counter += 1
            method = row["method"]
            target_delivery_dependency = next(iter(delivery_dependencies), None)
            if method == "sendMessage":
                provider_message_fingerprint = "sha256:" + hashlib.sha256(
                    f"message:{message_counter}".encode()
                ).hexdigest()
                target_message_fingerprint = None
            elif method.startswith("editMessage"):
                target_message_fingerprint = results_by_obligation[target_delivery_dependency][
                    "provider_message_fingerprint"
                ]
                provider_message_fingerprint = target_message_fingerprint
            else:
                provider_message_fingerprint = None
                target_message_fingerprint = None
            committed = outcomes_by_event[row["source_event_id"]][
                "committed_elapsed_ms"
            ]
            dependency_completed = max(
                (
                    results_by_obligation[value]["provider_completed_elapsed_ms"]
                    for value in delivery_dependencies
                ),
                default=0,
            )
            enqueued = max(int(row["source_at_ms"]) + 11, committed + 1)
            completed = max(enqueued + 1, dependency_completed + 1)
            state_event = row["required_state_event_id"]
            source_state_sha256 = (
                outcomes_by_event[state_event]["state_sha256"]
                if state_event is not None
                else None
            )
            result = {
                "delivery_id": f"delivery-{len(results_by_obligation):05d}",
                "obligation_id": obligation_id,
                "event_id": row["source_event_id"],
                "business_event_id": row["business_event_id"],
                "response_catalog_id": row["response_catalog_id"],
                "method": method,
                "bot_role": row["bot_role"],
                "destination_class": row["destination_class"],
                "outcome": "sent",
                "provider_side_effect_count": 1,
                "enqueued_elapsed_ms": enqueued,
                "provider_completed_elapsed_ms": completed,
                "source_version": row["source_version"],
                "causal_dependency_ids": row["causal_dependency_ids"],
                "source_state_sha256": source_state_sha256,
                "payload_sha256": "sha256:"
                + hashlib.sha256(
                    canonical_json(
                        {
                            "obligation_id": obligation_id,
                            "source_state_sha256": source_state_sha256,
                            "synthetic_payload": "independent-fixture-rendering",
                        }
                    ).encode("utf-8")
                ).hexdigest(),
                "provider_message_fingerprint": provider_message_fingerprint,
                "target_message_fingerprint": target_message_fingerprint,
            }
            result["provider_observation_sha256"] = stage4_observation_sha256(
                "provider", result
            )
            results_by_obligation[obligation_id] = result
            del pending[obligation_id]
            progressed = True
        if not progressed:
            raise AssertionError("fixture obligation dependency cycle")
    results = list(results_by_obligation.values())
    _write_jsonl(root / "telegram_results.jsonl", results)

    receipts = []
    active_by_obligation = {row["obligation_id"]: row for row in active}
    for row in results:
        obligation = active_by_obligation[row["obligation_id"]]
        state_event = obligation["required_state_event_id"]
        authoritative_state = (
            outcomes_by_event[state_event]["authoritative_state"]
            if state_event is not None
            else None
        )
        receipt = {
            "delivery_id": row["delivery_id"],
            "status": "observed",
            "observed_elapsed_ms": row["provider_completed_elapsed_ms"] + 1,
            "message_fingerprint": (
                row["target_message_fingerprint"]
                if row["method"].startswith("editMessage")
                else row["provider_message_fingerprint"]
            ),
            "state_sha256": row["source_state_sha256"],
            "payload_sha256": row["payload_sha256"],
            "rendered_offer_state": (
                {
                    "status": authoritative_state["status"],
                    "remaining_lots": authoritative_state["remaining_lots"],
                    "action_buttons_present": authoritative_state["status"]
                    == "active",
                }
                if row["destination_class"] == "channel"
                and row["method"].startswith("editMessage")
                else None
            ),
        }
        receipt["receiver_observation_sha256"] = stage4_observation_sha256(
            "receiver", receipt
        )
        receipts.append(receipt)
    _write_jsonl(root / "receiver_receipts.jsonl", receipts)
    _write_jsonl(
        root / "queue_metrics.jsonl",
        (
            {
                "elapsed_second": second,
                "ready_backlog": 0,
                "leased_backlog": 0,
                "unresolved_backlog": 0,
                "backlog_caused_disablements_delta": 0,
                "drain_complete": second == STAGE4_EVIDENCE_TIMELINE_SECONDS,
            }
            for second in range(STAGE4_EVIDENCE_TIMELINE_SECONDS + 1)
        ),
    )
    fault_executions = []
    faults = []
    for row in stage4_fault_catalog():
        execution = {
            "case_id": row["case_id"],
            "observed_disposition": row["expected_disposition"],
            "injected_input": row["injected_input"],
            "input_shape_sha256": row["input_shape_sha256"],
            "retry_after_source": row["retry_after_source"],
            "duplicate_provider_side_effect_count": 0,
            "provider_call_count": 1,
        }
        execution["adapter_observation_sha256"] = stage4_observation_sha256(
            "adapter", execution
        )
        fault_executions.append(execution)
        durable_record = {
            "state": row["allowed_durable_states"][0],
            "duplicate_provider_side_effect_count": 0,
            "retry_after_source": row["retry_after_source"],
        }
        fault = {
            "case_id": row["case_id"],
            "observer_source": "independent_database_observer",
            "durable_state": row["allowed_durable_states"][0],
            "durable_record": durable_record,
        }
        if row["case_id"].startswith("http_429_"):
            fault.update(
                {
                    "retry_after_applied_seconds": (
                        2_147_483_647
                        if row["case_id"] == "http_429_integer_max"
                        else 1
                    ),
                    "cooldown_deadline_source": row["retry_after_source"],
                }
            )
            durable_record.update(
                {
                    "retry_after_applied_seconds": fault[
                        "retry_after_applied_seconds"
                    ],
                    "cooldown_deadline_source": row["retry_after_source"],
                }
            )
        fault["durable_observation_sha256"] = stage4_observation_sha256(
            "durable", fault
        )
        faults.append(fault)
    _write_jsonl(root / "fault_executions.jsonl", fault_executions)
    _write_jsonl(root / "fault_results.jsonl", faults)
    cleanup = []
    for row in cleanup_plan:
        cleaned = {
            "cleanup_id": row["cleanup_id"],
            "status": "completed",
            "observer_source": "independent_database_observer",
            "observer_before_count": 1,
            "observer_after_count": 0,
        }
        cleaned["cleanup_observation_sha256"] = stage4_observation_sha256(
            "cleanup", cleaned
        )
        cleanup.append(cleaned)
    _write_jsonl(root / "cleanup_ledger.jsonl", cleanup)
    _write_json(
        root / "reconciliation.json",
        {
            "status": "clean",
            "unresolved_jobs": 0,
            "duplicate_provider_side_effects": 0,
            "missing_business_outcomes": 0,
            "missing_receiver_receipts": 0,
            "invalid_offer_publication_intents": 0,
            "cross_lane_route_mutations": 0,
            "backlog_caused_disablements": 0,
            "delivery_obligation_count": len(active),
            "provider_side_effect_count": len(results),
        },
    )
    submit_at = {
        row["business_event_id"]: row["at_ms"]
        for row in trace
        if row["event_type"] == "offer_submit_valid"
    }
    publication_latencies = sorted(
        (
            results_by_obligation[row["obligation_id"]]["provider_completed_elapsed_ms"]
            - submit_at[row["business_event_id"]]
        )
        / 1000.0
        for row in active
        if row["obligation_kind"] == "offer_channel_publication"
    )
    callback_latencies = sorted(
        (
            results_by_obligation[row["obligation_id"]]["provider_completed_elapsed_ms"]
            - row["source_at_ms"]
        )
        / 1000.0
        for row in active
        if row["method"] == "answerCallbackQuery"
    )

    def nearest(values, percentile):
        rank = max(1, int(percentile / 100 * len(values) + 0.999999999))
        return values[rank - 1]

    _write_json(
        root / "acceptance.json",
        {
            "decision": "pass",
            "stop_events": [],
            "input_counts": {"valid_accepted": 1800, "invalid_rejected": 400},
            "criteria": {
                "delivery_obligation_coverage": True,
                "eligible_publication_before_deadline": True,
                "publication_latency_p95": True,
                "publication_latency_p99": True,
                "callback_latency_p95": True,
                "callback_latency_p99": True,
                "receiver_reconciliation": True,
                "no_backlog_caused_disablement": True,
                "race_release_and_authoritative_outcome": True,
            },
            "metrics": {
                "eligible_publication_before_deadline_ratio": 1.0,
                "publication_latency_p95_seconds": nearest(publication_latencies, 95),
                "publication_latency_p99_seconds": nearest(publication_latencies, 99),
                "callback_latency_p95_seconds": nearest(callback_latencies, 95),
                "callback_latency_p99_seconds": nearest(callback_latencies, 99),
                "missing_receiver_receipts": 0,
                "duplicate_receiver_receipts": 0,
                "backlog_caused_disablements": 0,
            },
        },
    )
    _refresh_integrity(root, private_keys)
    return private_keys, public_keys


class TelegramQueueStage4HarnessTests(unittest.TestCase):
    def _plan(self, root: Path, *, seed=2026071501, commit="3" * 40):
        return write_stage4_plan(
            output_dir=root,
            config=safe_config(),
            fixture=FIXTURE,
            seed=seed,
            git_commit=commit,
        )

    def _trust_policy(self, root: Path, *, host_identity_sha256=None):
        root.chmod(0o755)
        key_root = root / "keys"
        registry_root = root / "registry"
        workspace_root = root / "workspace"
        driver_root = root / "drivers"
        for directory in (key_root, registry_root, workspace_root, driver_root):
            directory.mkdir()
            directory.chmod(0o755)
        private_keys = {
            "authorization": Ed25519PrivateKey.generate(),
            "sender": Ed25519PrivateKey.generate(),
            "observer": Ed25519PrivateKey.generate(),
        }
        key_paths = {}
        for role, uid, gid in (("sender", 61001, 61001), ("observer", 61002, 61002)):
            key_path = key_root / f"{role}.key"
            key_path.write_bytes(
                private_keys[role].private_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PrivateFormat.Raw,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            os.chown(key_path, uid, gid)
            key_path.chmod(0o600)
            key_paths[role] = key_path
        policy = {
            "schema_version": 1,
            "authorization_public_key": base64.b64encode(
                _raw_public_key(private_keys["authorization"])
            ).decode("ascii"),
            "sender_evidence_public_key": base64.b64encode(
                _raw_public_key(private_keys["sender"])
            ).decode("ascii"),
            "observer_evidence_public_key": base64.b64encode(
                _raw_public_key(private_keys["observer"])
            ).decode("ascii"),
            "sender_uid": 61001,
            "sender_gid": 61001,
            "observer_uid": 61002,
            "observer_gid": 61002,
            "sender_signing_key_path": str(key_paths["sender"]),
            "observer_signing_key_path": str(key_paths["observer"]),
            "authorization_registry_path": str(registry_root / "used.sqlite3"),
            "evidence_workspace_root": str(workspace_root),
            "driver_root": str(driver_root),
            "host_identity_sha256": host_identity_sha256 or _fingerprint("5"),
        }
        policy_path = root / "trust-policy.json"
        _write_json(policy_path, policy)
        policy_path.chmod(0o600)
        return policy_path, policy, private_keys, driver_root

    def test_authorization_is_exact_signed_and_bound_to_nonce_and_trust_policy(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = _raw_public_key(private_key)
        config = safe_config(environment="staging", provider=True)
        now = datetime.now(timezone.utc)
        sender = ("/trusted/sender", "--fixed")
        observer = ("/trusted/observer",)
        driver_hashes = {"sender": "1" * 64, "observer": "2" * 64}
        manifest = {
            "run_id": config["run_id"],
            "plan_nonce": config["plan_nonce"],
            "trace_sha256": "3" * 64,
            "trust_policy_sha256": config["trust_policy_sha256"],
        }
        authorization = {
            "schema_version": 2,
            "allow_live_staging": True,
            "authorization_id": "stage4-auth-unit-test",
            "not_before": (now - timedelta(seconds=1)).isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "run_id": manifest["run_id"],
            "plan_nonce": manifest["plan_nonce"],
            "git_commit": "4" * 40,
            "trace_sha256": manifest["trace_sha256"],
            "config_sha256": stage4_config_binding_sha256(config),
            "trust_policy_sha256": manifest["trust_policy_sha256"],
            "driver_commands_sha256": sha256_text(
                canonical_json({"sender": list(sender), "observer": list(observer)})
            ),
            "driver_executables_sha256": driver_hashes,
        }
        authorization["signature"] = base64.b64encode(
            private_key.sign(canonical_json(authorization).encode("utf-8"))
        ).decode("ascii")
        runner._validate_authorization(
            authorization=authorization,
            config=config,
            manifest=manifest,
            git_commit="4" * 40,
            sender=sender,
            observer=observer,
            driver_executables_sha256=driver_hashes,
            trusted_public_key=public_key,
        )
        attacker = Ed25519PrivateKey.generate()
        forged = {**authorization, "plan_nonce": "f" * 64}
        forged["signature"] = base64.b64encode(
            attacker.sign(
                canonical_json(
                    {key: value for key, value in forged.items() if key != "signature"}
                ).encode("utf-8")
            )
        ).decode("ascii")
        with self.assertRaisesRegex(
            Stage4HarnessValidationError, "authorization_mismatch|signature_invalid"
        ):
            runner._validate_authorization(
                authorization=forged,
                config=config,
                manifest=manifest,
                git_commit="4" * 40,
                sender=sender,
                observer=observer,
                driver_executables_sha256=driver_hashes,
                trusted_public_key=public_key,
            )

    def test_authorization_consumption_is_global_not_output_directory_local(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = Path(temporary) / "authorizations.sqlite3"
            authorization = {"authorization_id": "stage4-auth-global", "x": 1}
            manifest = {"run_id": "stage4-safe", "plan_nonce": "a" * 64}
            runner._consume_authorization_once(
                registry_path=registry,
                authorization=authorization,
                manifest=manifest,
            )
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "already_consumed"
            ):
                runner._consume_authorization_once(
                    registry_path=registry,
                    authorization=authorization,
                    manifest=manifest,
                )

    @unittest.skipUnless(os.geteuid() == 0, "requires root-owned trust fixture")
    def test_trust_policy_is_host_bound_key_separated_and_fixed_by_cli(self):
        with tempfile.TemporaryDirectory(dir="/root") as temporary:
            root = Path(temporary)
            policy_path, policy, _keys, _drivers = self._trust_policy(root)
            with patch.object(
                runner, "_host_identity_sha256", return_value=_fingerprint("5")
            ):
                loaded = runner._load_trust_policy(policy_path)
            self.assertEqual(loaded["raw"], policy)
            self.assertEqual(set(loaded["public_keys"]), {"authorization", "sender", "observer"})
            policy["host_identity_sha256"] = _fingerprint("0")
            _write_json(policy_path, policy)
            with patch.object(
                runner, "_host_identity_sha256", return_value=_fingerprint("5")
            ), self.assertRaisesRegex(
                Stage4HarnessValidationError, "host_identity_mismatch"
            ):
                runner._load_trust_policy(policy_path)
            policy["host_identity_sha256"] = _fingerprint("5")
            policy["sender_evidence_public_key"] = policy[
                "observer_evidence_public_key"
            ]
            _write_json(policy_path, policy)
            with patch.object(
                runner, "_host_identity_sha256", return_value=_fingerprint("5")
            ), self.assertRaisesRegex(
                Stage4HarnessValidationError, "key_pair_mismatch"
            ):
                runner._load_trust_policy(policy_path)
        parser_source = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--trust-policy", parser_source)
        self.assertNotIn("--trusted-authorization-public-key", parser_source)

    @unittest.skipUnless(os.geteuid() == 0, "requires root-owned driver fixture")
    def test_driver_executable_must_be_root_owned_and_immutable(self):
        with tempfile.TemporaryDirectory(dir="/root") as temporary:
            root = Path(temporary)
            _policy_path, _policy, _keys, driver_root = self._trust_policy(root)
            executable = driver_root / "sender-driver"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o555)
            self.assertEqual(
                runner._command(
                    [str(executable)],
                    name="sender",
                    driver_root=driver_root,
                ),
                (str(executable),),
            )
            executable.chmod(0o755)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "not_immutable_or_trusted"
            ):
                runner._command(
                    [str(executable)], name="sender", driver_root=driver_root
                )
            executable.chmod(0o555)
            driver_root.chmod(0o775)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "parent_permissions_invalid"
            ):
                runner._command(
                    [str(executable)], name="sender", driver_root=driver_root
                )

    @unittest.skipUnless(os.geteuid() == 0, "requires observer-owned ready fixture")
    def test_observer_ready_receipt_requires_role_owned_fresh_signature(self):
        private_key = Ed25519PrivateKey.generate()
        manifest = {
            "run_id": "stage4-ready-test",
            "plan_nonce": "a" * 64,
            "trace_sha256": "b" * 64,
            "trust_policy_sha256": _fingerprint("c"),
            "network_policy_fingerprints": {"observer": _fingerprint("d")},
        }
        started = datetime.now(timezone.utc) - timedelta(seconds=1)
        payload = {
            "schema_version": 1,
            "role": "observer",
            "run_id": manifest["run_id"],
            "plan_nonce": manifest["plan_nonce"],
            "trace_sha256": manifest["trace_sha256"],
            "trust_policy_sha256": manifest["trust_policy_sha256"],
            "network_policy_sha256": manifest["network_policy_fingerprints"]["observer"],
            "driver_executable_sha256": "e" * 64,
            "process_pid": 4242,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
        }
        envelope = {
            "payload": payload,
            "signature": base64.b64encode(
                private_key.sign(canonical_json(payload).encode("utf-8"))
            ).decode("ascii"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / "observer.ready.json"
            _write_json(ready, envelope)
            os.chown(ready, 61002, 61002)
            ready.chmod(0o600)
            arguments = {
                "manifest": manifest,
                "process_pid": 4242,
                "observer_executable_sha256": "e" * 64,
                "observer_public_key": _raw_public_key(private_key),
                "observer_uid": 61002,
                "not_before": started,
            }
            self.assertTrue(runner._verify_ready_receipt(ready, **arguments))
            ready.chmod(0o644)
            self.assertFalse(runner._verify_ready_receipt(ready, **arguments))
            ready.chmod(0o600)
            envelope["signature"] = base64.b64encode(b"invalid-signature").decode(
                "ascii"
            )
            _write_json(ready, envelope)
            os.chown(ready, 61002, 61002)
            ready.chmod(0o600)
            self.assertFalse(runner._verify_ready_receipt(ready, **arguments))

    def test_driver_environment_has_separate_outputs_and_no_parent_secrets(self):
        config = safe_config()
        manifest = {
            "bot_mode": config["bot_mode"],
            "run_id": config["run_id"],
            "plan_nonce": config["plan_nonce"],
            "trust_policy_sha256": config["trust_policy_sha256"],
            "network_policy_fingerprints": config["network_policy_fingerprints"],
            "staging_fingerprints": {
                "database": stage4_bound_value_fingerprint("staging-db"),
                "redis": stage4_bound_value_fingerprint("staging-redis"),
                "primary_bot": stage4_bound_value_fingerprint("staging-primary"),
                "channel_editor_bot": stage4_bound_value_fingerprint("staging-editor"),
                "channel": stage4_bound_value_fingerprint("staging-channel"),
                "observer_database": stage4_bound_value_fingerprint("observer-db"),
                "receiver_session": stage4_bound_value_fingerprint("receiver"),
            },
        }
        environment = {
            "DATABASE_URL": "must-not-cross",
            "BOT_TOKEN": "must-not-cross",
            "STAGE4_STAGING_DATABASE_URL": "staging-db",
            "STAGE4_STAGING_REDIS_URL": "staging-redis",
            "STAGE4_STAGING_PRIMARY_BOT_TOKEN": "staging-primary",
            "STAGE4_STAGING_CHANNEL_EDITOR_BOT_TOKEN": "staging-editor",
            "STAGE4_STAGING_CHANNEL_ID": "staging-channel",
        }
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, environment, clear=True
        ):
            root = Path(temporary)
            child = runner._driver_environment(
                profile="sender",
                output_dir=root / "sender",
                plan_dir=root / "plan",
                manifest=manifest,
                signing_key_path=root / "sender.key",
            )
        self.assertNotIn("DATABASE_URL", child)
        self.assertNotIn("BOT_TOKEN", child)
        self.assertNotIn("STAGE4_RUN_DIR", child)
        self.assertEqual(child["STAGE4_OUTPUT_DIR"], str((root / "sender").resolve()))

    @unittest.skipUnless(os.geteuid() == 0, "requires root UID isolation")
    def test_sender_and_observer_cannot_overwrite_each_others_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            workspace.chmod(0o755)
            sender = runner._isolated_output_directory(
                workspace_root=workspace,
                run_id="stage4-boundary-test",
                role="sender",
                uid=61001,
                gid=61001,
            )
            observer = runner._isolated_output_directory(
                workspace_root=workspace,
                run_id="stage4-boundary-test",
                role="observer",
                uid=61002,
                gid=61002,
            )
            program = "from pathlib import Path; Path(__import__('sys').argv[1]).write_text('x')"
            sender_own = subprocess.run(
                [sys.executable, "-c", program, str(sender / "own")],
                check=False,
                user=61001,
                group=61001,
                extra_groups=(),
            )
            sender_attack = subprocess.run(
                [sys.executable, "-c", program, str(observer / "forged")],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                user=61001,
                group=61001,
                extra_groups=(),
            )
            observer_attack = subprocess.run(
                [sys.executable, "-c", program, str(sender / "forged")],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                user=61002,
                group=61002,
                extra_groups=(),
            )
            self.assertEqual(sender_own.returncode, 0)
            self.assertNotEqual(sender_attack.returncode, 0)
            self.assertNotEqual(observer_attack.returncode, 0)

    def test_plan_is_deterministic_complete_and_provider_free(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            manifest_a = self._plan(Path(first), commit="1" * 40)
            manifest_b = self._plan(Path(second), commit="1" * 40)
            self.assertEqual(manifest_a["trace_sha256"], manifest_b["trace_sha256"])
            self.assertEqual(manifest_a["plan_nonce"], "a" * 64)
            self.assertFalse(manifest_a["provider_network_enabled"])
            self.assertEqual(verify_stage4_plan(Path(first))["schema_version"], 3)

    def test_trace_has_no_undeclared_trade_expiry_overlap_and_explicit_auto_expiry(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            trace = _read_jsonl(root / "event_trace.jsonl")
            trades = {
                row["business_event_id"]
                for row in trace
                if row["event_type"] == "trade_request"
            }
            manual = {
                row["business_event_id"]
                for row in trace
                if row["event_type"] == "manual_expiry_request"
                and not row.get("expiry_trade_race")
            }
            self.assertFalse(trades & manual)
            self.assertTrue(any(row["event_type"] == "automatic_expiry" for row in trace))
            self.assertFalse(any(row["event_type"] == "market_status_change" for row in trace))
            self.assertEqual(
                {row["event_type"] for row in trace if row["event_type"].startswith("market_")},
                {"market_status_notice"},
            )

    def test_live_verifier_accepts_independently_attested_reference_fixture(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            _private, public = _write_passing_live_evidence(root)
            self.assertEqual(
                verify_stage4_live_evidence(
                    root, evidence_public_keys=public
                )["status"],
                "verified",
            )

    def test_live_verifier_requires_external_evidence_keys(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            _write_passing_live_evidence(root)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "trust_keys_required"
            ):
                verify_stage4_live_evidence(root)

    def test_terminal_edit_before_authoritative_commit_is_rejected(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            private, public = _write_passing_live_evidence(root)
            results = _read_jsonl(root / "telegram_results.jsonl")
            terminal = next(
                row for row in results if row["obligation_id"].startswith("terminal-edit:")
            )
            terminal["enqueued_elapsed_ms"] = 0
            terminal["provider_observation_sha256"] = stage4_observation_sha256(
                "provider", terminal
            )
            _write_jsonl(root / "telegram_results.jsonl", results)
            _refresh_integrity(root, private)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "obligation_result_invalid"
            ):
                verify_stage4_live_evidence(root, evidence_public_keys=public)

    def test_resigned_wrong_terminal_text_and_buttons_are_rejected(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            private, public = _write_passing_live_evidence(root)
            results = _read_jsonl(root / "telegram_results.jsonl")
            terminal = next(
                row for row in results if row["obligation_id"].startswith("terminal-edit:")
            )
            forged_payload = _fingerprint("7")
            terminal["payload_sha256"] = forged_payload
            terminal["provider_observation_sha256"] = stage4_observation_sha256(
                "provider", terminal
            )
            _write_jsonl(root / "telegram_results.jsonl", results)
            receipts = _read_jsonl(root / "receiver_receipts.jsonl")
            receipt = next(
                row for row in receipts if row["delivery_id"] == terminal["delivery_id"]
            )
            receipt["payload_sha256"] = forged_payload
            receipt["rendered_offer_state"] = {
                "status": "active",
                "remaining_lots": 99,
                "action_buttons_present": True,
            }
            receipt["receiver_observation_sha256"] = stage4_observation_sha256(
                "receiver", receipt
            )
            _write_jsonl(root / "receiver_receipts.jsonl", receipts)
            _refresh_integrity(root, private)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "receiver_receipt_mismatch"
            ):
                verify_stage4_live_evidence(root, evidence_public_keys=public)

    def test_partial_edit_after_completion_must_be_superseded(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            private, public = _write_passing_live_evidence(root)
            results = _read_jsonl(root / "telegram_results.jsonl")
            partial = next(
                row for row in results if row["obligation_id"].startswith("partial-edit:")
            )
            outcomes = _read_jsonl(root / "business_outcomes.jsonl")
            terminal_commit = next(
                row["committed_elapsed_ms"]
                for row in outcomes
                if row["business_event_id"] == partial["business_event_id"]
                and row["authoritative_result"] == "trade_committed"
            )
            partial["provider_completed_elapsed_ms"] = terminal_commit + 1
            partial["provider_observation_sha256"] = stage4_observation_sha256(
                "provider", partial
            )
            _write_jsonl(root / "telegram_results.jsonl", results)
            receipts = _read_jsonl(root / "receiver_receipts.jsonl")
            receipt = next(
                row for row in receipts if row["delivery_id"] == partial["delivery_id"]
            )
            receipt["observed_elapsed_ms"] = terminal_commit + 2
            receipt["receiver_observation_sha256"] = stage4_observation_sha256(
                "receiver", receipt
            )
            _write_jsonl(root / "receiver_receipts.jsonl", receipts)
            _refresh_integrity(root, private)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "partial_edit_after_terminal_state"
            ):
                verify_stage4_live_evidence(root, evidence_public_keys=public)

    def test_every_concurrent_group_requires_exactly_one_authoritative_winner(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            private, public = _write_passing_live_evidence(root)
            expected = _read_jsonl(root / "expected_business_ledger.jsonl")
            ordinary_group = next(
                row["authoritative_group_id"]
                for row in expected
                if row["authoritative_group_rule"] == "exactly_one_trade"
            )
            members = {
                row["event_id"]
                for row in expected
                if row["authoritative_group_id"] == ordinary_group
            }
            outcomes = _read_jsonl(root / "business_outcomes.jsonl")
            rejected = next(
                row
                for row in outcomes
                if row["event_id"] in members
                and row["authoritative_result"] == "rejected_conflict"
            )
            rejected["authoritative_result"] = "trade_committed"
            rejected["observation_sha256"] = stage4_observation_sha256(
                "business", rejected
            )
            _write_jsonl(root / "business_outcomes.jsonl", outcomes)
            _refresh_integrity(root, private)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "competition_authoritative_outcome_invalid"
            ):
                verify_stage4_live_evidence(root, evidence_public_keys=public)

    def test_sent_noop_terminal_edit_still_requires_receiver_readback(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            private, public = _write_passing_live_evidence(root)
            results = _read_jsonl(root / "telegram_results.jsonl")
            terminal = next(
                row for row in results if row["obligation_id"].startswith("terminal-edit:")
            )
            terminal["outcome"] = "sent_noop"
            terminal["provider_side_effect_count"] = 0
            terminal["provider_message_fingerprint"] = None
            terminal["provider_observation_sha256"] = stage4_observation_sha256(
                "provider", terminal
            )
            _write_jsonl(root / "telegram_results.jsonl", results)
            receipts = _read_jsonl(root / "receiver_receipts.jsonl")
            _write_jsonl(
                root / "receiver_receipts.jsonl",
                (row for row in receipts if row["delivery_id"] != terminal["delivery_id"]),
            )
            _refresh_integrity(root, private)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "receiver_receipt_mismatch"
            ):
                verify_stage4_live_evidence(root, evidence_public_keys=public)

    def test_fully_resigned_invented_authoritative_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            private, public = _write_passing_live_evidence(root)
            outcomes = _read_jsonl(root / "business_outcomes.jsonl")
            observed = next(
                row for row in outcomes if row["authoritative_state"] is not None
            )
            observed["authoritative_state"]["remaining_lots"] += 99
            observed["state_sha256"] = stage4_authoritative_state_sha256(
                observed["authoritative_state"]
            )
            observed["observation_sha256"] = stage4_observation_sha256(
                "business", observed
            )
            _write_jsonl(root / "business_outcomes.jsonl", outcomes)
            _refresh_integrity(root, private)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "state_snapshot_mismatch"
            ):
                verify_stage4_live_evidence(root, evidence_public_keys=public)

    def test_fully_resigned_fault_input_outside_code_owned_catalog_is_rejected(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            private, public = _write_passing_live_evidence(root)
            executions = _read_jsonl(root / "fault_executions.jsonl")
            execution = next(
                row
                for row in executions
                if row["case_id"] == "http_429_integer_one"
            )
            execution["injected_input"]["provider_body"]["response_parameters"][
                "retry_after"
            ] = 2
            execution["input_shape_sha256"] = "sha256:" + sha256_text(
                "telegram-stage4-fault-shape-v2:"
                + canonical_json(execution["injected_input"])
            )
            execution["adapter_observation_sha256"] = stage4_observation_sha256(
                "adapter", execution
            )
            _write_jsonl(root / "fault_executions.jsonl", executions)
            _refresh_integrity(root, private)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "fault_execution_mismatch"
            ):
                verify_stage4_live_evidence(root, evidence_public_keys=public)

    def test_valid_signature_cannot_cover_mutated_role_file(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            _private, public = _write_passing_live_evidence(root)
            (root / "business_outcomes.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "attestation_mismatch"
            ):
                verify_stage4_live_evidence(root, evidence_public_keys=public)

    def test_hand_authored_scan_or_extra_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            _private, public = _write_passing_live_evidence(root)
            (root / "unlisted-secret.log").write_text("secret", encoding="utf-8")
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "inventory_invalid"
            ):
                verify_stage4_live_evidence(root, evidence_public_keys=public)

    def test_scanner_report_is_directly_recomputed(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            self._plan(root)
            _private, public = _write_passing_live_evidence(root)
            report = _read_json(root / "security_scan.json")
            report["scanned_file_manifest_sha256"] = "d" * 64
            _write_json(root / "security_scan.json", report)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "security_scan_incomplete"
            ):
                verify_stage4_live_evidence(root, evidence_public_keys=public)

    def test_production_collision_and_raw_secret_keys_fail_closed(self):
        unsafe = safe_config()
        unsafe["environment"] = "production"
        with self.assertRaisesRegex(Stage4HarnessValidationError, "environment_not_allowed"):
            validate_stage4_run_config(unsafe, live_execution=False)
        collision = safe_config()
        collision["production_fingerprints"]["channel"] = collision[
            "staging_fingerprints"
        ]["channel"]
        with self.assertRaisesRegex(Stage4HarnessValidationError, "fingerprint_collision"):
            validate_stage4_run_config(collision, live_execution=False)
        raw = {**safe_config(), "bot_token": "must-never-be-rendered"}
        with self.assertRaisesRegex(Stage4HarnessValidationError, "raw_identity_or_secret"):
            validate_stage4_run_config(raw, live_execution=False)

    def test_live_execute_refuses_before_trust_or_process_without_explicit_flag(self):
        args = unittest.mock.MagicMock(
            authorize_live_staging=False,
            config=Path("unused"),
            output_dir=Path("unused"),
        )
        with patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(Stage4HarnessValidationError, "confirmation_flag"):
                runner._execute_live(args, Path("."))
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
