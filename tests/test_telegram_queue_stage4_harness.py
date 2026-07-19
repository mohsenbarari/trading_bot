import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.telegram_queue_stage4_harness import (
    STAGE4_LIVE_EVIDENCE_FILES,
    STAGE4_PLAN_FILES,
    Stage4HarnessValidationError,
    stage4_fault_catalog,
    stage4_config_binding_sha256,
    validate_stage4_run_config,
    verify_stage4_live_evidence,
    verify_stage4_plan,
    write_stage4_plan,
)
from scripts import run_telegram_queue_stage4_harness as runner


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


def safe_config(*, environment="synthetic-test", provider=False):
    return {
        "schema_version": 1,
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
        },
        "production_fingerprints": {
            "database": _fingerprint("a"),
            "redis": _fingerprint("b"),
            "primary_bot": _fingerprint("c"),
            "channel_editor_bot": _fingerprint("d"),
            "channel": _fingerprint("e"),
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
            "routing_policy_sha256": _fingerprint("f"),
        },
    )
    _write_jsonl(
        root / "business_outcomes.jsonl",
        (
            {
                "event_id": row["event_id"],
                "business_event_id": row["business_event_id"],
                "event_type": row["event_type"],
                "observed_disposition": row["expected_disposition"],
                "offer_delta": row["expected_offer_delta"],
                "publication_intent_delta": row[
                    "expected_publication_intent_delta"
                ],
                "response_catalog_id": row["response_catalog_id"],
                "status": "resolved",
            }
            for row in expected
        ),
    )
    results = [
        {
            "delivery_id": f"delivery-{index:05d}",
            "event_id": row["event_id"],
            "response_catalog_id": row["response_catalog_id"],
            "method": "sendMessage",
            "bot_role": "primary",
            "destination_class": "private",
            "outcome": "sent",
            "provider_side_effect_count": 1,
            "latency_ms": 1.0,
            "receiver_required": True,
        }
        for index, row in enumerate(expected)
    ]
    _write_jsonl(root / "telegram_results.jsonl", results)
    _write_jsonl(
        root / "receiver_receipts.jsonl",
        (
            {"delivery_id": row["delivery_id"], "status": "observed"}
            for row in results
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
                "status": "pass",
                "duplicate_provider_side_effect_count": 0,
            }
            for row in stage4_fault_catalog()
        ),
    )
    _write_jsonl(
        root / "cleanup_ledger.jsonl",
        (
            {"cleanup_id": row["cleanup_id"], "status": "completed"}
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
        },
    )
    _write_json(
        root / "acceptance.json",
        {
            "decision": "pass",
            "stop_events": [],
            "input_counts": {"valid_accepted": 1800, "invalid_rejected": 400},
            "criteria": {"all_required_gates": True},
            "metrics": {
                "eligible_publication_before_deadline_ratio": 1.0,
                "publication_latency_p95_seconds": 1.0,
                "publication_latency_p99_seconds": 2.0,
                "callback_latency_p95_seconds": 0.1,
                "callback_latency_p99_seconds": 0.2,
                "missing_receiver_receipts": 0,
                "duplicate_receiver_receipts": 0,
                "backlog_caused_disablements": 0,
            },
        },
    )
    _write_json(
        root / "security_scan.json",
        {
            "status": "clean",
            "finding_count": 0,
            "scanned_files": [
                "manifest.json",
                *STAGE4_PLAN_FILES,
                *(name for name in STAGE4_LIVE_EVIDENCE_FILES if name != "security_scan.json"),
            ],
        },
    )


class TelegramQueueStage4HarnessTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
