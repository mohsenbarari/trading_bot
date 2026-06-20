import importlib.util
import io
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "report_bot_webapp_cutover_readiness.py"


spec = importlib.util.spec_from_file_location("report_bot_webapp_cutover_readiness", MODULE_PATH)
cutover = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = cutover
spec.loader.exec_module(cutover)


def passing_snapshot():
    return deepcopy(cutover.template_snapshot())


class BotWebAppCutoverReadinessTests(unittest.TestCase):
    def test_passing_staging_snapshot_is_ready_for_owner_review(self):
        result = cutover.evaluate_snapshot(passing_snapshot())

        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["ready_for_owner_production_review"])
        self.assertEqual(result["failure_count"], 0)
        self.assertEqual(result["role_summaries"]["iran"]["active_offer_count"], 0)
        self.assertEqual(result["capacity_summary"]["correctness_failure_count"], 0)
        self.assertIn("performs no production deploy", result["notice"])

    def test_production_snapshot_or_production_data_fails_closed(self):
        snapshot = passing_snapshot()
        snapshot["metadata"]["environment"] = "production"
        snapshot["metadata"]["production_data_used"] = True

        result = cutover.evaluate_snapshot(snapshot)

        self.assertEqual(result["status"], "failed")
        failed_ids = {failure["gate_id"] for failure in result["failures"]}
        self.assertIn("C12-ENV-01", failed_ids)
        self.assertIn("C12-ENV-02", failed_ids)

    def test_any_active_offer_blocks_cutover(self):
        snapshot = passing_snapshot()
        snapshot["roles"]["iran"]["active_offer_count"] = 1

        result = cutover.evaluate_snapshot(snapshot)

        self.assertEqual(result["status"], "failed")
        self.assertIn("C12-IRAN-ACTIVE", {failure["gate_id"] for failure in result["failures"]})

    def test_missing_public_identity_blocks_link_and_callback_cutover(self):
        snapshot = passing_snapshot()
        report = snapshot["roles"]["foreign"]["backfill_report"]
        report["total_offers"] = 12
        report["offers_with_public_id_before"] = 10
        report["offers_with_public_id_after"] = 11
        report["offers_missing_public_id"] = 1

        result = cutover.evaluate_snapshot(snapshot)

        self.assertEqual(result["status"], "failed")
        self.assertIn("C12-FOREIGN-PUBLIC-ID", {failure["gate_id"] for failure in result["failures"]})

    def test_historical_request_backfill_requires_explicit_approval_and_never_invents_rows(self):
        snapshot = passing_snapshot()
        ledger = snapshot["roles"]["iran"]["offer_request_ledger"]
        ledger["historical_request_backfill_attempted"] = True

        result = cutover.evaluate_snapshot(snapshot)

        self.assertEqual(result["status"], "failed")
        self.assertIn("C12-IRAN-LEDGER", {failure["gate_id"] for failure in result["failures"]})

        ledger["historical_request_backfill_explicitly_approved"] = True
        result = cutover.evaluate_snapshot(snapshot)
        self.assertEqual(result["status"], "passed")
        self.assertIn("C12-IRAN-LEDGER-APPROVED-BACKFILL", {warning["gate_id"] for warning in result["warnings"]})

        ledger["invented_missing_request_attempts"] = 1
        result = cutover.evaluate_snapshot(snapshot)
        self.assertEqual(result["status"], "failed")

    def test_sync_publication_guards_registry_sensitive_rollback_backup_and_signoff_are_required(self):
        snapshot = passing_snapshot()
        snapshot["roles"]["foreign"]["sync_health"]["redis_retry_queue"] = 2
        snapshot["roles"]["iran"]["publication_state"]["pending_count"] = 1
        snapshot["roles"]["iran"]["runtime_guards"]["telegram_blocked"] = False
        snapshot["roles"]["foreign"]["runtime_guards"]["webapp_user_surface_blocked"] = False
        snapshot["global"]["registry_coverage_complete"] = False
        snapshot["global"]["sensitive_field_policy_complete"] = False
        snapshot["global"]["rollback"]["destructive_cleanup_required"] = True
        snapshot["global"]["observability"]["sync_health_reviewed"] = False
        snapshot["global"]["backups"]["restore_smoke_passed"] = False
        snapshot["global"]["staging_validation"]["owner_signoff"] = False

        result = cutover.evaluate_snapshot(snapshot)

        failed_ids = {failure["gate_id"] for failure in result["failures"]}
        self.assertIn("C12-FOREIGN-SYNC", failed_ids)
        self.assertIn("C12-IRAN-PUBLICATION", failed_ids)
        self.assertIn("C12-IRAN-TELEGRAM-GUARD", failed_ids)
        self.assertIn("C12-FOREIGN-WEBAPP-GUARD", failed_ids)
        self.assertIn("C12-GLOBAL-REGISTRY", failed_ids)
        self.assertIn("C12-GLOBAL-SENSITIVE-POLICY", failed_ids)
        self.assertIn("C12-GLOBAL-ROLLBACK", failed_ids)
        self.assertIn("C12-GLOBAL-OBSERVABILITY", failed_ids)
        self.assertIn("C12-GLOBAL-BACKUPS", failed_ids)
        self.assertIn("C12-GLOBAL-STAGING-SIGNOFF", failed_ids)

    def test_step11b_capacity_report_is_required_and_must_remain_production_gated(self):
        snapshot = passing_snapshot()
        capacity_report = snapshot["global"]["staging_validation"]["capacity_report"]
        capacity_report["production_gate"]["status"] = "open"

        result = cutover.evaluate_snapshot(snapshot)

        self.assertEqual(result["status"], "failed")
        self.assertIn("C12-GLOBAL-CAPACITY-REPORT", {failure["gate_id"] for failure in result["failures"]})

        snapshot = passing_snapshot()
        capacity_report = snapshot["global"]["staging_validation"]["capacity_report"]
        capacity_report["correctness_failure_count"] = 1

        result = cutover.evaluate_snapshot(snapshot)

        self.assertEqual(result["status"], "failed")
        self.assertIn("C12-GLOBAL-CAPACITY-CORRECTNESS", {failure["gate_id"] for failure in result["failures"]})

    def test_step11b_capacity_report_accepts_real_artifact_failure_and_warning_lists(self):
        snapshot = passing_snapshot()
        capacity_report = snapshot["global"]["staging_validation"]["capacity_report"]
        del capacity_report["correctness_failure_count"]
        del capacity_report["capacity_warning_count"]
        capacity_report["correctness_failures"] = []
        capacity_report["capacity_warnings"] = ["webapp_full_fill: business_request_rps below target"]
        capacity_report["capacity_warnings_reviewed"] = True

        result = cutover.evaluate_snapshot(snapshot)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["capacity_summary"]["correctness_failure_count"], 0)
        self.assertEqual(result["capacity_summary"]["capacity_warning_count"], 1)
        self.assertEqual(result["capacity_summary"]["production_gate"], cutover.STEP11_CAPACITY_PRODUCTION_GATE_STATUS)
        self.assertIn("C12-GLOBAL-CAPACITY-WARNINGS", {warning["gate_id"] for warning in result["warnings"]})

    def test_reviewed_capacity_warnings_are_visible_warnings_not_hidden_failures(self):
        snapshot = passing_snapshot()
        capacity_report = snapshot["global"]["staging_validation"]["capacity_report"]
        capacity_report["capacity_warning_count"] = 2
        capacity_report["capacity_warnings_reviewed"] = True

        result = cutover.evaluate_snapshot(snapshot)

        self.assertEqual(result["status"], "passed")
        self.assertIn("C12-GLOBAL-CAPACITY-WARNINGS", {warning["gate_id"] for warning in result["warnings"]})

        capacity_report["capacity_warnings_reviewed"] = False
        result = cutover.evaluate_snapshot(snapshot)

        self.assertEqual(result["status"], "failed")
        self.assertIn("C12-GLOBAL-CAPACITY-WARNINGS", {failure["gate_id"] for failure in result["failures"]})

    def test_legacy_unknown_and_old_channel_bindings_are_warnings_not_fabricated_history(self):
        snapshot = passing_snapshot()
        report = snapshot["roles"]["foreign"]["backfill_report"]
        report["legacy_unknown_expiry_source_rows"] = 3
        report["old_channel_message_bindings"] = 4
        snapshot["roles"]["foreign"]["offer_request_ledger"]["unknown_legacy_rows"] = 5

        result = cutover.evaluate_snapshot(snapshot)

        self.assertEqual(result["status"], "passed")
        warning_ids = {warning["gate_id"] for warning in result["warnings"]}
        self.assertIn("C12-FOREIGN-LEGACY-UNKNOWN", warning_ids)
        self.assertIn("C12-FOREIGN-OLD-CHANNEL-BINDINGS", warning_ids)
        self.assertIn("C12-FOREIGN-LEDGER-UNKNOWN-LEGACY", warning_ids)

    def test_cli_template_check_json_and_markdown_paths(self):
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(cutover.main(["--template"]), 0)
        template = json.loads(stdout.getvalue())
        self.assertEqual(template["metadata"]["environment"], "staging")
        self.assertFalse(template["metadata"]["production_data_used"])

        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "snapshot.json"
            report_path = Path(tmp_dir) / "report.md"
            snapshot_path.write_text(json.dumps(passing_snapshot()), encoding="utf-8")

            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(cutover.main(["--input", str(snapshot_path), "--json", "--check"]), 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "passed")

            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(
                    cutover.main(
                        [
                            "--input",
                            str(snapshot_path),
                            "--markdown",
                            "--report-out",
                            str(report_path),
                            "--check",
                        ]
                    ),
                    0,
                )
            self.assertIn("# Bot/WebApp Cutover Readiness Report", stdout.getvalue())
            self.assertIn("No Production", report_path.read_text(encoding="utf-8"))
            self.assertIn("Step 11B Capacity Summary", report_path.read_text(encoding="utf-8"))

            failed = passing_snapshot()
            failed["roles"]["iran"]["active_offer_count"] = 1
            snapshot_path.write_text(json.dumps(failed), encoding="utf-8")
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(cutover.main(["--input", str(snapshot_path), "--check"]), 1)

    def test_cli_requires_input_unless_template(self):
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(cutover.main(["--check"]), 2)
        self.assertIn("--input is required", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
