import json
import base64
import hashlib
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.telegram_queue_stage4_harness import (
    STAGE4_LIVE_EVIDENCE_FILES,
    STAGE4_PLAN_FILES,
    Stage4HarnessValidationError,
    stage4_fault_catalog,
    stage4_config_binding_sha256,
    canonical_json,
    sha256_text,
    stage4_bound_value_fingerprint,
    validate_stage4_run_config,
    verify_stage4_live_evidence,
    verify_stage4_plan,
    write_stage4_plan,
)
from scripts import run_telegram_queue_stage4_harness as runner
from scripts.scan_telegram_queue_artifacts import scan_release_surfaces


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


def safe_config(*, environment="synthetic-test", provider=False):
    return {
        "schema_version": 2,
        "environment": environment,
        "run_id": "stage4-safe-test-run",
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
            "database": _fingerprint("a"),
            "redis": _fingerprint("b"),
            "primary_bot": _fingerprint("c"),
            "channel_editor_bot": _fingerprint("d"),
            "channel": _fingerprint("e"),
            "observer_database": _fingerprint("f"),
            "receiver_session": _fingerprint("0"),
        },
        "network_policy_fingerprints": {
            "sender": _fingerprint("6"),
            "observer": _fingerprint("7"),
        },
        "authorization_public_key": base64.b64encode(b"p" * 32).decode("ascii"),
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


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_passing_live_evidence(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    expected = _read_jsonl(root / "expected_business_ledger.jsonl")
    trace = _read_jsonl(root / "event_trace.jsonl")
    trace_by_event = {row["event_id"]: row for row in trace}
    obligations = _read_jsonl(root / "delivery_obligations.jsonl")
    cleanup_plan = _read_jsonl(root / "cleanup_plan.jsonl")
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
            "sender_network_policy_sha256": manifest[
                "network_policy_fingerprints"
            ]["sender"],
            "observer_network_policy_sha256": manifest[
                "network_policy_fingerprints"
            ]["observer"],
            "routing_policy_sha256": _fingerprint("f"),
        },
    )
    race_trades = {}
    for event in trace:
        if event.get("expiry_trade_race") and event["event_type"] == "trade_request":
            race_trades.setdefault(event["business_event_id"], []).append(event)
    for rows in race_trades.values():
        rows.sort(key=lambda row: (row["at_ms"], row["event_id"]))
    business_outcomes = []
    for row in expected:
        event = trace_by_event[row["event_id"]]
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
            "observed_at_ms": int(event["at_ms"]) + 50,
            "observation_sha256": _fingerprint("8"),
        }
        if event.get("expiry_trade_race") and event["event_type"] in {
            "trade_request",
            "manual_expiry_request",
        }:
            winner = event["race_expected_winner"]
            outcome["sender_release_elapsed_ms"] = int(event["at_ms"]) + 10
            if event["event_type"] == "manual_expiry_request":
                outcome["authoritative_result"] = (
                    "expiry_committed" if winner == "expiry" else "rejected_conflict"
                )
            else:
                first_trade = race_trades[event["business_event_id"]][0]["event_id"]
                outcome["authoritative_result"] = (
                    "trade_committed"
                    if winner == "trade" and event["event_id"] == first_trade
                    else "rejected_conflict"
                )
        business_outcomes.append(outcome)
    _write_jsonl(
        root / "business_outcomes.jsonl",
        business_outcomes,
    )
    results = [
        {
            "delivery_id": f"delivery-{index:05d}",
            "obligation_id": row["obligation_id"],
            "event_id": row["source_event_id"],
            "business_event_id": row["business_event_id"],
            "response_catalog_id": row["response_catalog_id"],
            "method": row["method"],
            "bot_role": row["bot_role"],
            "destination_class": row["destination_class"],
            "outcome": "sent",
            "provider_side_effect_count": 1,
            "enqueued_elapsed_ms": row["source_at_ms"] + 1,
            "provider_completed_elapsed_ms": row["source_at_ms"] + 2,
            "provider_observation_sha256": _fingerprint("9"),
            "source_version": row["source_version"],
            "causal_dependency_ids": row["causal_dependency_ids"],
        }
        for index, row in enumerate(obligations)
    ]
    _write_jsonl(root / "telegram_results.jsonl", results)
    _write_jsonl(
        root / "receiver_receipts.jsonl",
        (
            {
                "delivery_id": result["delivery_id"],
                "status": "observed",
                "observed_elapsed_ms": result["provider_completed_elapsed_ms"] + 1,
                "receiver_observation_sha256": _fingerprint("a"),
            }
            for obligation, result in zip(obligations, results, strict=True)
            if obligation["receiver_policy"] == "required_on_provider_effect"
        ),
    )
    _write_jsonl(
        root / "queue_metrics.jsonl",
        (
            {
                "elapsed_second": second,
                "ready_backlog": 0,
                "leased_backlog": 0,
                "unresolved_backlog": 0,
                "backlog_caused_disablements_delta": 0,
                "drain_complete": second == 600,
            }
            for second in range(601)
        ),
    )
    _write_jsonl(
        root / "fault_results.jsonl",
        (
            {
                "case_id": row["case_id"],
                "observed_disposition": row["expected_disposition"],
                "input_shape_sha256": row["input_shape_sha256"],
                "retry_after_source": row["retry_after_source"],
                "duplicate_provider_side_effect_count": 0,
                "provider_call_count": 1,
                "durable_state": row["allowed_durable_states"][0],
                "durable_observation_sha256": _fingerprint("b"),
                **(
                    {
                        "retry_after_applied_seconds": (
                            2_147_483_647
                            if row["case_id"] == "http_429_integer_max"
                            else 1
                        ),
                        "cooldown_deadline_source": row["retry_after_source"],
                    }
                    if row["case_id"].startswith("http_429_")
                    else {}
                ),
            }
            for row in stage4_fault_catalog()
        ),
    )
    _write_jsonl(
        root / "cleanup_ledger.jsonl",
        (
            {
                "cleanup_id": row["cleanup_id"],
                "status": "completed",
                "observer_source": "independent_database_observer",
                "observer_before_count": 1,
                "observer_after_count": 0,
                "cleanup_observation_sha256": _fingerprint("c"),
            }
            for row in cleanup_plan
        ),
    )
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
            "delivery_obligation_count": len(obligations),
            "provider_side_effect_count": len(results),
        },
    )
    submit_at = {
        row["business_event_id"]: row["at_ms"]
        for row in trace
        if row["event_type"] == "offer_submit_valid"
    }
    result_by_obligation = {row["obligation_id"]: row for row in results}
    publication_latencies = sorted(
        (
            result_by_obligation[row["obligation_id"]][
                "provider_completed_elapsed_ms"
            ]
            - submit_at[row["business_event_id"]]
        )
        / 1000.0
        for row in obligations
        if row["obligation_kind"] == "offer_channel_publication"
    )
    callback_latencies = sorted(
        (
            result_by_obligation[row["obligation_id"]][
                "provider_completed_elapsed_ms"
            ]
            - row["source_at_ms"]
        )
        / 1000.0
        for row in obligations
        if row["method"] == "answerCallbackQuery"
    )

    def nearest(values, percentile):
        rank = max(1, int(percentile / 100 * len(values) + 0.999999999))
        return values[rank - 1]

    acceptance_metrics = {
        "eligible_publication_before_deadline_ratio": 1.0,
        "publication_latency_p95_seconds": nearest(publication_latencies, 95),
        "publication_latency_p99_seconds": nearest(publication_latencies, 99),
        "callback_latency_p95_seconds": nearest(callback_latencies, 95),
        "callback_latency_p99_seconds": nearest(callback_latencies, 99),
        "missing_receiver_receipts": 0,
        "duplicate_receiver_receipts": 0,
        "backlog_caused_disablements": 0,
    }
    criteria = {
        "delivery_obligation_coverage": True,
        "eligible_publication_before_deadline": True,
        "publication_latency_p95": True,
        "publication_latency_p99": True,
        "callback_latency_p95": True,
        "callback_latency_p99": True,
        "receiver_reconciliation": True,
        "no_backlog_caused_disablement": True,
        "race_release_and_authoritative_outcome": True,
    }
    _write_json(
        root / "acceptance.json",
        {
            "decision": "pass",
            "stop_events": [],
            "input_counts": {"valid_accepted": 1800, "invalid_rejected": 400},
            "criteria": criteria,
            "metrics": acceptance_metrics,
        },
    )
    scanned_files = sorted([
        "manifest.json",
        *STAGE4_PLAN_FILES,
        *(name for name in STAGE4_LIVE_EVIDENCE_FILES if name != "security_scan.json"),
    ])
    scan_manifest_rows = []
    for name in scanned_files:
        blob = (root / name).read_bytes()
        scan_manifest_rows.append(
            f"{name}\t{hashlib.sha256(blob).hexdigest()}\t{len(blob)}"
        )
    _write_json(
        root / "security_scan.json",
        {
            "schema_version": 2,
            "status": "clean",
            "finding_count": 0,
            "scanned_files": scanned_files,
            "scanned_file_count": len(scanned_files),
            "scanned_file_manifest_sha256": hashlib.sha256(
                ("\n".join(scan_manifest_rows) + "\n").encode("utf-8")
            ).hexdigest(),
        },
    )


class TelegramQueueStage4HarnessTests(unittest.TestCase):
    def test_live_authorization_is_signed_short_lived_and_binary_bound(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        config = safe_config(environment="staging", provider=True)
        config["authorization_public_key"] = base64.b64encode(public_key).decode(
            "ascii"
        )
        now = datetime.now(timezone.utc)
        sender = ("/staging/sender", "--fixed")
        observer = ("/staging/observer",)
        driver_hashes = {"sender": "1" * 64, "observer": "2" * 64}
        manifest = {
            "run_id": config["run_id"],
            "trace_sha256": "3" * 64,
        }
        authorization = {
            "schema_version": 1,
            "allow_live_staging": True,
            "authorization_id": "stage4-auth-unit-test",
            "not_before": (now - timedelta(seconds=1)).isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "run_id": manifest["run_id"],
            "git_commit": "4" * 40,
            "trace_sha256": manifest["trace_sha256"],
            "config_sha256": stage4_config_binding_sha256(config),
            "driver_commands_sha256": sha256_text(
                canonical_json(
                    {"sender": list(sender), "observer": list(observer)}
                )
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
        tampered = {**authorization, "trace_sha256": "5" * 64}
        with self.assertRaisesRegex(
            Stage4HarnessValidationError, "authorization_mismatch|signature_invalid"
        ):
            runner._validate_authorization(
                authorization=tampered,
                config=config,
                manifest=manifest,
                git_commit="4" * 40,
                sender=sender,
                observer=observer,
                driver_executables_sha256=driver_hashes,
                trusted_public_key=public_key,
            )
        ambiguous = {**authorization, "unsigned_policy_override": True}
        ambiguous["signature"] = base64.b64encode(
            private_key.sign(
                canonical_json(
                    {key: value for key, value in ambiguous.items() if key != "signature"}
                ).encode("utf-8")
            )
        ).decode("ascii")
        with self.assertRaisesRegex(
            Stage4HarnessValidationError, "authorization_fields_invalid"
        ):
            runner._validate_authorization(
                authorization=ambiguous,
                config=config,
                manifest=manifest,
                git_commit="4" * 40,
                sender=sender,
                observer=observer,
                driver_executables_sha256=driver_hashes,
                trusted_public_key=public_key,
            )

        attacker_private_key = Ed25519PrivateKey.generate()
        attacker_public_key = attacker_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        attacker_config = {
            **config,
            "authorization_public_key": base64.b64encode(
                attacker_public_key
            ).decode("ascii"),
        }
        attacker_authorization = {
            **authorization,
            "config_sha256": stage4_config_binding_sha256(attacker_config),
        }
        attacker_authorization["signature"] = base64.b64encode(
            attacker_private_key.sign(
                canonical_json(
                    {
                        key: value
                        for key, value in attacker_authorization.items()
                        if key != "signature"
                    }
                ).encode("utf-8")
            )
        ).decode("ascii")
        with self.assertRaisesRegex(
            Stage4HarnessValidationError, "authorization_signature_invalid"
        ):
            runner._validate_authorization(
                authorization=attacker_authorization,
                config=attacker_config,
                manifest=manifest,
                git_commit="4" * 40,
                sender=sender,
                observer=observer,
                driver_executables_sha256=driver_hashes,
                trusted_public_key=public_key,
            )

    def test_trusted_authorization_key_must_be_external_and_not_group_writable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "run"
            output.mkdir()
            trusted = root / "trusted-ed25519.pub"
            trusted.write_bytes(b"t" * 32)
            trusted.chmod(0o600)
            self.assertEqual(
                runner._trusted_authorization_public_key(
                    trusted.resolve(), output_dir=output
                ),
                b"t" * 32,
            )
            trusted.chmod(0o620)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "permissions_invalid"
            ):
                runner._trusted_authorization_public_key(
                    trusted.resolve(), output_dir=output
                )
            inside = output / "self-declared.pub"
            inside.write_bytes(b"t" * 32)
            inside.chmod(0o600)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "key_file_invalid"
            ):
                runner._trusted_authorization_public_key(
                    inside.resolve(), output_dir=output
                )

    def test_driver_environment_does_not_inherit_production_or_generic_secrets(self):
        manifest = {
            "bot_mode": "primary-and-channel-editor",
            "network_policy_fingerprints": safe_config()[
                "network_policy_fingerprints"
            ],
            "staging_fingerprints": {
                "database": stage4_bound_value_fingerprint("staging-db"),
                "redis": stage4_bound_value_fingerprint("staging-redis"),
                "primary_bot": stage4_bound_value_fingerprint("staging-primary"),
                "channel_editor_bot": stage4_bound_value_fingerprint(
                    "staging-editor"
                ),
                "channel": stage4_bound_value_fingerprint("staging-channel"),
                "observer_database": stage4_bound_value_fingerprint(
                    "staging-observer-db"
                ),
                "receiver_session": stage4_bound_value_fingerprint(
                    "staging-receiver-session"
                ),
            },
        }
        environment = {
            "PATH": "/usr/bin",
            "DATABASE_URL": "must-not-cross-boundary",
            "REDIS_URL": "must-not-cross-boundary",
            "BOT_TOKEN": "must-not-cross-boundary",
            "STAGE4_STAGING_DATABASE_URL": "staging-db",
            "STAGE4_STAGING_REDIS_URL": "staging-redis",
            "STAGE4_STAGING_PRIMARY_BOT_TOKEN": "staging-primary",
            "STAGE4_STAGING_CHANNEL_EDITOR_BOT_TOKEN": "staging-editor",
            "STAGE4_STAGING_CHANNEL_ID": "staging-channel",
        }
        with tempfile.TemporaryDirectory() as output, patch.dict(
            os.environ, environment, clear=True
        ):
            child = runner._driver_environment(
                profile="sender",
                output_dir=Path(output),
                manifest=manifest,
            )
        self.assertNotIn("DATABASE_URL", child)
        self.assertNotIn("REDIS_URL", child)
        self.assertNotIn("BOT_TOKEN", child)
        self.assertEqual(child["STAGE4_NETWORK_PROFILE"], "sender")

        mismatched = {**environment, "STAGE4_STAGING_DATABASE_URL": "production-db"}
        with tempfile.TemporaryDirectory() as output, patch.dict(
            os.environ, mismatched, clear=True
        ), self.assertRaisesRegex(
            Stage4HarnessValidationError, "environment_fingerprint_mismatch"
        ):
            runner._driver_environment(
                profile="sender",
                output_dir=Path(output),
                manifest=manifest,
            )

        observer_environment = {
            "PATH": "/usr/bin",
            "BOT_TOKEN": "must-not-cross-boundary",
            "STAGE4_STAGING_OBSERVER_DATABASE_URL": "staging-observer-db",
            "STAGE4_STAGING_RECEIVER_SESSION": "staging-receiver-session",
            "STAGE4_STAGING_CHANNEL_ID": "staging-channel",
        }
        with tempfile.TemporaryDirectory() as output, patch.dict(
            os.environ, observer_environment, clear=True
        ):
            observer_child = runner._driver_environment(
                profile="observer",
                output_dir=Path(output),
                manifest=manifest,
            )
        self.assertNotIn("BOT_TOKEN", observer_child)
        self.assertNotIn("STAGE4_STAGING_PRIMARY_BOT_TOKEN", observer_child)
        self.assertEqual(
            observer_child["STAGE4_STAGING_CHANNEL_ID"], "staging-channel"
        )
        self.assertEqual(observer_child["STAGE4_NETWORK_PROFILE"], "observer")

    def test_plan_and_live_config_bindings_differ_only_by_authorized_network_mode(self):
        plan = safe_config(environment="staging", provider=False)
        live = safe_config(environment="staging", provider=True)
        self.assertEqual(
            stage4_config_binding_sha256(plan),
            stage4_config_binding_sha256(live),
        )
        changed = {**live, "run_id": "stage4-different-safe-run"}
        self.assertNotEqual(
            stage4_config_binding_sha256(plan),
            stage4_config_binding_sha256(changed),
        )

    def test_plan_is_deterministic_complete_and_provider_free(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            manifest_a = write_stage4_plan(
                output_dir=Path(first),
                config=safe_config(),
                fixture=FIXTURE,
                seed=2026071501,
                git_commit="1" * 40,
            )
            manifest_b = write_stage4_plan(
                output_dir=Path(second),
                config=safe_config(),
                fixture=FIXTURE,
                seed=2026071501,
                git_commit="1" * 40,
            )
            self.assertEqual(manifest_a["trace_sha256"], manifest_b["trace_sha256"])
            self.assertFalse(manifest_a["provider_network_enabled"])
            self.assertEqual(verify_stage4_plan(Path(first))["run_id"], "stage4-safe-test-run")

    def test_production_and_fingerprint_collision_fail_closed(self):
        unsafe = safe_config()
        unsafe["environment"] = "production"
        with self.assertRaisesRegex(Stage4HarnessValidationError, "environment_not_allowed"):
            validate_stage4_run_config(unsafe, live_execution=False)
        collision = safe_config()
        collision["production_fingerprints"]["channel"] = collision["staging_fingerprints"]["channel"]
        with self.assertRaisesRegex(Stage4HarnessValidationError, "fingerprint_collision"):
            validate_stage4_run_config(collision, live_execution=False)

    def test_raw_identity_or_secret_field_is_rejected_without_echo(self):
        unsafe = {**safe_config(), "bot_token": "must-never-be-rendered"}
        with self.assertRaisesRegex(Stage4HarnessValidationError, "raw_identity_or_secret") as caught:
            validate_stage4_run_config(unsafe, live_execution=False)
        self.assertNotIn("must-never-be-rendered", str(caught.exception))

    def test_tampered_trace_fails_manifest_verification(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            write_stage4_plan(
                output_dir=root,
                config=safe_config(),
                fixture=FIXTURE,
                seed=1,
                git_commit="2" * 40,
            )
            trace = root / "event_trace.jsonl"
            trace.write_text(trace.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
            with self.assertRaisesRegex(Stage4HarnessValidationError, "file_mismatch"):
                verify_stage4_plan(root)

    def test_live_execute_refuses_before_process_creation_without_explicit_flag(self):
        args = unittest.mock.MagicMock(
            authorize_live_staging=False,
            config=Path("unused"),
            output_dir=Path("unused"),
        )
        with patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(Stage4HarnessValidationError, "confirmation_flag"):
                runner._execute_live(args, Path("."))
        popen.assert_not_called()

    def test_live_verifier_reconciles_all_ledgers_and_rejects_missing_receipt(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            write_stage4_plan(
                output_dir=root,
                config=safe_config(),
                fixture=FIXTURE,
                seed=2026071501,
                git_commit="3" * 40,
            )
            _write_passing_live_evidence(root)
            report = verify_stage4_live_evidence(root)
            self.assertEqual(report["status"], "verified")
            (root / "receiver_receipts.jsonl").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(
                Stage4HarnessValidationError,
                "receiver_receipt_mismatch",
            ):
                verify_stage4_live_evidence(root)

    def test_live_verifier_rejects_driver_fabrication_and_recomputes_evidence(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            write_stage4_plan(
                output_dir=root,
                config=safe_config(),
                fixture=FIXTURE,
                seed=2026071501,
                git_commit="6" * 40,
            )
            _write_passing_live_evidence(root)

            # The previous fixture's one-primary/private-send per input event
            # must no longer satisfy the code-owned delivery oracle.
            expected = _read_jsonl(root / "expected_business_ledger.jsonl")
            _write_jsonl(
                root / "telegram_results.jsonl",
                (
                    {
                        "delivery_id": f"fabricated-{index}",
                        "obligation_id": f"fabricated-{index}",
                        "event_id": row["event_id"],
                        "business_event_id": row["business_event_id"],
                        "method": "sendMessage",
                        "bot_role": "primary",
                        "destination_class": "private",
                        "outcome": "sent",
                    }
                    for index, row in enumerate(expected)
                ),
            )
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "obligation_coverage"
            ):
                verify_stage4_live_evidence(root)

            _write_passing_live_evidence(root)
            results = _read_jsonl(root / "telegram_results.jsonl")
            publication = next(
                row
                for row in results
                if row["obligation_id"].startswith("publication:")
            )
            publication["bot_role"] = "channel_editor"
            _write_jsonl(root / "telegram_results.jsonl", results)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "obligation_result_invalid"
            ):
                verify_stage4_live_evidence(root)

            _write_passing_live_evidence(root)
            outcomes = _read_jsonl(root / "business_outcomes.jsonl")
            race = next(row for row in outcomes if "sender_release_elapsed_ms" in row)
            race["sender_release_elapsed_ms"] += 1_000
            _write_jsonl(root / "business_outcomes.jsonl", outcomes)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "race_release_skew"
            ):
                verify_stage4_live_evidence(root)

            _write_passing_live_evidence(root)
            acceptance = json.loads(
                (root / "acceptance.json").read_text(encoding="utf-8")
            )
            acceptance["metrics"]["publication_latency_p99_seconds"] = 0.0
            _write_json(root / "acceptance.json", acceptance)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "acceptance_not_clean"
            ):
                verify_stage4_live_evidence(root)

    def test_live_verifier_rejects_each_delivery_cleanup_and_aggregate_forgery(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            write_stage4_plan(
                output_dir=root,
                config=safe_config(),
                fixture=FIXTURE,
                seed=2026071503,
                git_commit="8" * 40,
            )

            for prefix in ("publication:", "trade-party:", "terminal-edit:"):
                _write_passing_live_evidence(root)
                results = _read_jsonl(root / "telegram_results.jsonl")
                target = next(
                    row for row in results if row["obligation_id"].startswith(prefix)
                )
                _write_jsonl(
                    root / "telegram_results.jsonl",
                    (row for row in results if row is not target),
                )
                with self.subTest(missing_obligation=prefix), self.assertRaisesRegex(
                    Stage4HarnessValidationError, "obligation_coverage"
                ):
                    verify_stage4_live_evidence(root)

            _write_passing_live_evidence(root)
            results = _read_jsonl(root / "telegram_results.jsonl")
            results[0]["receiver_required"] = False
            _write_jsonl(root / "telegram_results.jsonl", results)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "obligation_result_invalid"
            ):
                verify_stage4_live_evidence(root)

            _write_passing_live_evidence(root)
            results = _read_jsonl(root / "telegram_results.jsonl")
            results[0]["provider_side_effect_count"] = 2
            _write_jsonl(root / "telegram_results.jsonl", results)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "obligation_result_invalid"
            ):
                verify_stage4_live_evidence(root)

            _write_passing_live_evidence(root)
            results = _read_jsonl(root / "telegram_results.jsonl")
            results[1]["delivery_id"] = results[0]["delivery_id"]
            _write_jsonl(root / "telegram_results.jsonl", results)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "obligation_coverage"
            ):
                verify_stage4_live_evidence(root)

            _write_passing_live_evidence(root)
            reconciliation = json.loads(
                (root / "reconciliation.json").read_text(encoding="utf-8")
            )
            reconciliation["unresolved_jobs"] = 1
            _write_json(root / "reconciliation.json", reconciliation)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "reconciliation_not_clean"
            ):
                verify_stage4_live_evidence(root)

            _write_passing_live_evidence(root)
            cleanup = _read_jsonl(root / "cleanup_ledger.jsonl")
            cleanup[0]["observer_after_count"] = 1
            _write_jsonl(root / "cleanup_ledger.jsonl", cleanup)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "cleanup_ledger_incomplete"
            ):
                verify_stage4_live_evidence(root)

            _write_passing_live_evidence(root)
            faults = _read_jsonl(root / "fault_results.jsonl")
            faults[0]["durable_state"] = "self_declared_pass"
            _write_jsonl(root / "fault_results.jsonl", faults)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "fault_result_mismatch"
            ):
                verify_stage4_live_evidence(root)

    def test_real_scanner_output_is_accepted_by_live_verifier(self):
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            write_stage4_plan(
                output_dir=root,
                config=safe_config(),
                fixture=FIXTURE,
                seed=2026071502,
                git_commit="7" * 40,
            )
            _write_passing_live_evidence(root)
            scanned_paths = [
                root / "manifest.json",
                *(root / name for name in STAGE4_PLAN_FILES),
                *(
                    root / name
                    for name in STAGE4_LIVE_EVIDENCE_FILES
                    if name != "security_scan.json"
                ),
            ]
            report = scan_release_surfaces(
                artifact_paths=scanned_paths,
                tracked_source_root=None,
            )
            _write_json(root / "security_scan.json", report)
            self.assertEqual(report["status"], "clean")
            self.assertEqual(
                verify_stage4_live_evidence(root)["status"], "verified"
            )

            report["scanned_file_manifest_sha256"] = "d" * 64
            _write_json(root / "security_scan.json", report)
            with self.assertRaisesRegex(
                Stage4HarnessValidationError, "security_scan_manifest_mismatch"
            ):
                verify_stage4_live_evidence(root)


if __name__ == "__main__":
    unittest.main()
