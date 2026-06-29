import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "report_bot_webapp_integration_matrix.py"


spec = importlib.util.spec_from_file_location("report_bot_webapp_integration_matrix", MODULE_PATH)
report_matrix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = report_matrix
spec.loader.exec_module(report_matrix)


class BotWebAppIntegrationMatrixTests(unittest.TestCase):
    def test_matrix_has_unique_scenarios_and_all_required_layers(self):
        scenario_ids = [scenario.scenario_id for scenario in report_matrix.SCENARIOS]
        self.assertEqual(len(scenario_ids), len(set(scenario_ids)))
        self.assertEqual(len(report_matrix.SCENARIOS), 27)

        covered_layers = {
            layer
            for scenario in report_matrix.SCENARIOS
            for layer in report_matrix.scenario_layers(scenario)
        }
        self.assertEqual(set(report_matrix.REQUIRED_LAYERS) - covered_layers, set())

    def test_matrix_preserves_step_11_required_scenario_contract(self):
        expected_titles = {
            "Bot-created offer stays foreign-home and becomes visible on Iran WebApp",
            "WebApp-created offer stays Iran-home and is published to foreign Telegram",
            "Stable offer public identity resolves links and callbacks without cross-server integer IDs",
            "Owner expiry from WebApp mutates only on the authoritative home server",
            "Owner expiry from Telegram bot records bot source and removes Telegram interactions",
            "User-triggered expiry records source platform, server, owner, and actor metadata",
            "Automatic expiry records lifetime/system metadata without fabricating a user actor",
            "Telegram terminal markers distinguish fully traded, partially traded, and expired offers",
            "Telegram terminal edits remove inline buttons and tolerate duplicate/not-modified edits",
            "WebApp and bot request/trade paths work against Iran-home and foreign-home offers",
            "Request ledger captures success, rejection, lot unavailable, stale/conflict, replay, and after-expiry outcomes",
            "Request ledger state machine is append-safe and terminal outcomes are not contradicted",
            "Public failure visibility is separated from internal failure context",
            "Customer requester ledger rows snapshot relation metadata and restrict visibility",
            "Offer detail link shows safe public fields and authorized metadata by viewer",
            "Offer detail ledger pagination, retention, and archive assumptions remain bounded",
            "The same user can be active on WebApp and bot without changing offer source ownership",
            "Near-simultaneous trade, expiry, and sync updates do not reactivate stale state",
            "Duplicate callback, duplicate sync delivery, worker replay, and direct push failure are idempotent/retryable",
            "Telegram publish failure is recorded, retried, and does not corrupt business offer state",
            "WebApp realtime failure is tolerated and recovered through sync/publication state",
            "Unknown table, forbidden table, unsupported version, and sensitive fields fail closed",
            "Short outage replay preserves committed payload identity and rejects stale terminal reversals",
            "Medium and long outage recovery gates active publication until full catch-up and expires local-only active offers",
            "Migration/cutover rehearsal requires zero active offers and fresh shared-sync state",
            "Rollback and fail-closed behavior preserve data without destructive cleanup",
            "Staging validation evidence is owner-led and separated from production peers/data",
        }

        self.assertEqual({scenario.title for scenario in report_matrix.SCENARIOS}, expected_titles)

    def test_referenced_coverage_files_and_snippets_exist(self):
        missing = report_matrix.missing_coverage_refs(REPO_ROOT)

        self.assertEqual(missing, [], "\n".join(missing))

    def test_evaluate_matrix_passes_but_keeps_manual_signoff_explicit(self):
        result = report_matrix.evaluate_matrix(REPO_ROOT)

        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["scenario_count"], 27)
        self.assertTrue(result["manual_signoff_required"])
        self.assertEqual(result["failures"], [])

    def test_cli_json_and_check_paths(self):
        payload = report_matrix.build_report_payload(REPO_ROOT)
        encoded = json.dumps(payload, ensure_ascii=False)
        decoded = json.loads(encoded)

        self.assertTrue(decoded["matrix"]["passed"])
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(report_matrix.main(["--check"]), 0)
        self.assertIn("Automated reference gate: PASSED", stdout.getvalue())

        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(report_matrix.main(["--json", "--check"]), 0)
        self.assertTrue(json.loads(stdout.getvalue())["matrix"]["passed"])

        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(report_matrix.main(["--markdown", "--check"]), 0)
        self.assertIn("# Bot/WebApp Integration Scenario Matrix", stdout.getvalue())

    def test_markdown_report_includes_manual_staging_evidence_requirements(self):
        markdown = report_matrix.build_markdown_report(REPO_ROOT)

        self.assertIn("Manual staging sign-off: required before production consideration", markdown)
        self.assertIn("S11-27 - Staging validation evidence is owner-led", markdown)
        self.assertIn("production peers/data are a stop condition", markdown)


if __name__ == "__main__":
    unittest.main()
