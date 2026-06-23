import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts import report_trade_delivery_staging_validation as report


REPO_ROOT = Path(__file__).resolve().parents[1]


def sample_scenario_results(status="passed"):
    return [
        {"scenario_id": scenario.scenario_id, "status": status}
        for scenario in report.build_validation_matrix()
    ]


def sample_log_artifacts():
    return {
        name: {
            "path": f"artifacts/stage11/{name}.log.sanitized",
            "sanitized": True,
            "sensitive_findings": 0,
            "source": "staging",
        }
        for name in report.REQUIRED_LOG_CLASSES
    }


def sample_delivery_metrics(**overrides):
    metrics = {
        "duplicate_trade_count": 0,
        "duplicate_visible_notification_count": 0,
        "missing_required_delivery_count": 0,
        "accepted_ambiguous_telegram_duplicate_count": 0,
        "receipt_status_counts": {
            "pending": 0,
            "processing": 0,
            "retry_pending": 0,
            "sent": 120,
            "skipped": 4,
            "not_required": 12,
            "permanent_failed": 0,
        },
        "stable_delivery_latency_seconds": {"p50": 0.45, "p95": 1.2, "p99": 1.7, "max": 1.95},
        "stable_threshold_miss_count": 0,
        "stable_threshold_miss_explanations": [],
    }
    metrics.update(overrides)
    return metrics


def sample_staging_report(**overrides):
    payload = {
        "schema_version": report.TRADE_DELIVERY_STAGING_REPORT_SCHEMA_VERSION,
        "environment": "staging",
        "branch": "candidate/bot-webapp-integration",
        "commit": "abc123",
        "scenario_results": sample_scenario_results(),
        "log_artifacts": sample_log_artifacts(),
        "delivery_metrics": sample_delivery_metrics(),
        "outage_summary": {
            "short_outage_sent_count": 6,
            "medium_outage_skipped_count": 4,
            "long_outage_skipped_count": 4,
            "unexpected_old_remote_delivery_count": 0,
        },
        "crash_probes": {
            "before_send": {
                "status": "recovered",
                "duplicate_visible_notification_count": 0,
            },
            "after_send_before_sent": {
                "status": "sent",
                "duplicate_visible_notification_count": 0,
                "accepted_ambiguous_telegram_duplicate_count": 0,
            },
        },
        "load_profile": {
            "user_count": 1000,
            "target_rps": 600.0,
            "telegram_ratio": 0.6,
            "capacity_limited": False,
        },
        "capacity_report": {
            "path": "artifacts/stage11/bot_webapp_capacity.json",
            "schema_version": "bot_webapp_capacity_report_v1",
            "production_gate": report.PRODUCTION_GATE_BLOCKED_STATUS,
        },
        "production_gate": {
            "status": report.PRODUCTION_GATE_BLOCKED_STATUS,
            "reason": "Owner-led staging validation must review this artifact before production consideration.",
        },
    }
    payload.update(overrides)
    return payload


class TradeDeliveryStagingValidationReportTests(unittest.TestCase):
    def test_matrix_covers_required_dimensions_and_existing_deterministic_tests(self):
        validation = report.validate_matrix(REPO_ROOT)
        matrix = report.build_validation_matrix()

        self.assertEqual(validation["status"], "valid")
        self.assertEqual(validation["scenario_count"], 16)
        self.assertFalse(report.collect_matrix_gaps(REPO_ROOT, matrix))
        self.assertEqual(len({scenario.scenario_id for scenario in matrix}), len(matrix))

    def test_valid_staging_report_keeps_production_gate_blocked(self):
        validation = report.validate_staging_report(sample_staging_report())

        self.assertEqual(validation["status"], "valid")
        self.assertTrue(validation["manual_signoff_required"])
        self.assertEqual(validation["production_gate"], report.PRODUCTION_GATE_BLOCKED_STATUS)
        self.assertEqual(validation["warning_count"], 0)

    def test_scenario_skips_require_reason_and_failures_stop_validation(self):
        skipped = sample_scenario_results()
        skipped[0] = {"scenario_id": skipped[0]["scenario_id"], "status": "skipped"}
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(scenario_results=skipped))

        skipped[0]["skip_reason"] = "staging capacity window closed"
        validation = report.validate_staging_report(sample_staging_report(scenario_results=skipped))
        self.assertEqual(validation["warning_count"], 1)

        failed = sample_scenario_results()
        failed[0] = {"scenario_id": failed[0]["scenario_id"], "status": "failed"}
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(scenario_results=failed))

    def test_missing_or_unsanitized_log_artifacts_fail_closed(self):
        missing = sample_log_artifacts()
        del missing["sync"]
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(log_artifacts=missing))

        unsanitized = sample_log_artifacts()
        unsanitized["bot"] = dict(unsanitized["bot"], sanitized=False)
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(log_artifacts=unsanitized))

        production = sample_log_artifacts()
        production["db"] = dict(production["db"], source="production")
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(log_artifacts=production))

    def test_duplicate_or_missing_delivery_metrics_fail_closed(self):
        for field in (
            "duplicate_trade_count",
            "duplicate_visible_notification_count",
            "missing_required_delivery_count",
        ):
            with self.subTest(field=field):
                metrics = sample_delivery_metrics(**{field: 1})
                with self.assertRaises(report.TradeDeliveryStagingValidationError):
                    report.validate_staging_report(sample_staging_report(delivery_metrics=metrics))

    def test_latency_threshold_misses_must_be_explained(self):
        metrics = sample_delivery_metrics(
            stable_delivery_latency_seconds={"p50": 0.5, "p95": 1.3, "p99": 2.4, "max": 2.9},
            stable_threshold_miss_count=1,
            stable_threshold_miss_explanations=[],
        )
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(delivery_metrics=metrics))

        metrics["stable_threshold_miss_explanations"] = ["one p99 sample waited behind staging DB pool pressure"]
        validation = report.validate_staging_report(sample_staging_report(delivery_metrics=metrics))
        self.assertEqual(validation["warning_count"], 1)

    def test_short_medium_long_outage_counts_are_required(self):
        outage = {
            "short_outage_sent_count": 0,
            "medium_outage_skipped_count": 4,
            "long_outage_skipped_count": 4,
            "unexpected_old_remote_delivery_count": 0,
        }
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(outage_summary=outage))

        outage["short_outage_sent_count"] = 1
        outage["unexpected_old_remote_delivery_count"] = 1
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(outage_summary=outage))

    def test_crash_after_send_ambiguity_is_allowed_only_when_isolated_and_explained(self):
        crash_probes = copy.deepcopy(sample_staging_report()["crash_probes"])
        crash_probes["after_send_before_sent"] = {
            "status": "accepted_ambiguous_telegram_duplicate_risk",
            "duplicate_visible_notification_count": 1,
            "accepted_ambiguous_telegram_duplicate_count": 1,
            "explanation": "Telegram accepted send, worker crashed before marking sent.",
        }
        metrics = sample_delivery_metrics(accepted_ambiguous_telegram_duplicate_count=1)

        validation = report.validate_staging_report(
            sample_staging_report(crash_probes=crash_probes, delivery_metrics=metrics)
        )

        self.assertEqual(validation["warning_count"], 2)

        crash_probes["after_send_before_sent"]["explanation"] = ""
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(crash_probes=crash_probes))

    def test_load_profile_can_be_capacity_limited_only_with_reason(self):
        low_load = {
            "user_count": 400,
            "target_rps": 250,
            "telegram_ratio": 0.6,
            "capacity_limited": False,
        }
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(load_profile=low_load))

        low_load["capacity_limited"] = True
        low_load["capacity_limited_reason"] = "staging window intentionally capped to protect shared DB"
        validation = report.validate_staging_report(sample_staging_report(load_profile=low_load))
        self.assertEqual(validation["warning_count"], 2)

        bad_ratio = dict(low_load, user_count=1000, target_rps=600, telegram_ratio=0.5)
        with self.assertRaises(report.TradeDeliveryStagingValidationError):
            report.validate_staging_report(sample_staging_report(load_profile=bad_ratio))

    def test_cli_matrix_and_validate_commands(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "trade-delivery-stage11.json"
            artifact.write_text(json.dumps(sample_staging_report()), encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(report.main(["matrix", "--repo-root", str(REPO_ROOT)]), 0)
                self.assertEqual(report.main(["validate", "--artifact", str(artifact)]), 0)

            broken = Path(tmpdir) / "broken.json"
            broken_payload = sample_staging_report()
            broken_payload["production_gate"] = {"status": "open"}
            broken.write_text(json.dumps(broken_payload), encoding="utf-8")
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(report.main(["validate", "--artifact", str(broken)]), 1)


if __name__ == "__main__":
    unittest.main()
