import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "run_trade_delivery_targeted_join_matrix.py"

spec = importlib.util.spec_from_file_location("run_trade_delivery_targeted_join_matrix", MODULE_PATH)
targeted_matrix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = targeted_matrix
spec.loader.exec_module(targeted_matrix)


class TradeDeliveryTargetedJoinMatrixTests(unittest.TestCase):
    def test_catalog_covers_delivery_matrix_and_marks_policy_unsupported_paths(self):
        scenarios = targeted_matrix.build_targeted_join_scenarios()

        self.assertEqual(len(scenarios), 17 * 4 * 3)
        self.assertEqual({scenario.outage_id for scenario in scenarios}, {
            "stable",
            "short_under_2m",
            "medium_around_60m",
        })
        self.assertEqual({scenario.surface_pair for scenario in scenarios}, {
            "webapp_offer__webapp_request",
            "webapp_offer__telegram_request",
            "telegram_offer__webapp_request",
            "telegram_offer__telegram_request",
        })
        self.assertEqual({scenario.offer_home_server for scenario in scenarios}, {"iran", "foreign"})

        catalog = targeted_matrix.summarize_catalog(scenarios)
        self.assertEqual(catalog["scenario_count"], 204)
        self.assertEqual(catalog["executable_count"], 108)
        self.assertEqual(catalog["policy_unsupported_count"], 96)
        self.assertEqual(catalog["unsupported_reason_counts"]["tier2_cannot_create_offer"], 72)
        self.assertEqual(catalog["unsupported_reason_counts"]["tier2_cannot_use_telegram_request"], 36)

    def test_tier2_offer_creation_and_tier2_telegram_request_are_not_silently_omitted(self):
        scenarios = targeted_matrix.build_targeted_join_scenarios()

        tier2_source = [scenario for scenario in scenarios if scenario.source_kind == "tier2"]
        self.assertTrue(tier2_source)
        self.assertTrue(all(not scenario.policy_supported for scenario in tier2_source))
        self.assertTrue(
            all("tier2_cannot_create_offer" in scenario.unsupported_reasons for scenario in tier2_source)
        )

        tier2_telegram_request = [
            scenario
            for scenario in scenarios
            if scenario.responder_kind == "tier2" and scenario.request_surface == "telegram"
        ]
        self.assertTrue(tier2_telegram_request)
        self.assertTrue(
            all(
                "tier2_cannot_use_telegram_request" in scenario.unsupported_reasons
                for scenario in tier2_telegram_request
            )
        )

    def test_dry_run_cli_writes_artifact_without_database_access(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "targeted-join.json"

            with patch("sys.stdout", new_callable=io.StringIO):
                exit_code = targeted_matrix.main(
                    [
                        "--dry-run",
                        "--check",
                        "--prefix",
                        "P7_STAGE_TARGETED_TEST_",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], "trade_delivery_targeted_join_matrix_v1")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["execution"]["status"], "planned")
        self.assertEqual(payload["catalog"]["scenario_count"], 204)
        self.assertEqual(payload["catalog"]["outage_class_count"], 3)

    def test_run_git_value_falls_back_to_matrix_env_when_git_binary_is_missing(self):
        def missing_git(*_args, **_kwargs):
            raise FileNotFoundError("git")

        with patch.object(targeted_matrix.subprocess, "run", side_effect=missing_git), patch.dict(
            targeted_matrix.os.environ,
            {
                "CANDIDATE_MATRIX_BRANCH": "candidate/bot-webapp-integration",
                "CANDIDATE_MATRIX_COMMIT": "abc123",
            },
            clear=False,
        ):
            self.assertEqual(
                targeted_matrix.run_git_value(["branch", "--show-current"]),
                "candidate/bot-webapp-integration",
            )
            self.assertEqual(targeted_matrix.run_git_value(["rev-parse", "HEAD"]), "abc123")
            self.assertIsNone(targeted_matrix.run_git_value(["status", "--short"]))

    def test_fixture_identity_depends_on_matrix_prefix_and_retry_attempt(self):
        first_phone = targeted_matrix.phone_for("P7_STAGE_FIRST_", 1, 1)
        second_phone = targeted_matrix.phone_for("P7_STAGE_SECOND_", 1, 1)
        retry_phone = targeted_matrix.phone_for("P7_STAGE_FIRST_", 1, 1, attempt=1)

        self.assertRegex(first_phone, r"^09\d{9}$")
        self.assertNotEqual(first_phone, second_phone)
        self.assertNotEqual(first_phone, retry_phone)
        self.assertNotEqual(
            targeted_matrix.telegram_id_for("P7_STAGE_FIRST_", 1, 1),
            targeted_matrix.telegram_id_for("P7_STAGE_SECOND_", 1, 1),
        )

    def test_matrix_run_key_keeps_targeted_join_idempotency_run_scoped(self):
        first_key = f"tdj:{targeted_matrix.matrix_run_key('P7_STAGE_FIRST_')}:TDN-001:1"
        second_key = f"tdj:{targeted_matrix.matrix_run_key('P7_STAGE_SECOND_')}:TDN-001:1"

        self.assertLessEqual(len(first_key), 64)
        self.assertNotEqual(first_key, second_key)


if __name__ == "__main__":
    unittest.main()
