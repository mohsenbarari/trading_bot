import unittest

from scripts import report_bot_webapp_capacity as capacity


def sample_summary(*, business_rps=620.0, attempt_start_rps=None, telegram_rps=744.0, errors=0):
    return {
        "total": 100,
        "business_request_rps": business_rps,
        "attempt_start_rps": business_rps if attempt_start_rps is None else attempt_start_rps,
        "telegram_update_rps": telegram_rps,
        "success": 1,
        "rejected": 99 - errors,
        "error": errors,
        "latency": {"p50_ms": 10, "p95_ms": 20, "p99_ms": 30, "max_ms": 40},
        "surfaces": {
            "telegram": {
                "total": 60,
                "success": 1,
                "rejected": 59 - errors,
                "error": errors,
                "latency": {"p50_ms": 11, "p95_ms": 21, "p99_ms": 31, "max_ms": 41},
            },
            "webapp": {
                "total": 40,
                "success": 0,
                "rejected": 40,
                "error": 0,
                "latency": {"p50_ms": 12, "p95_ms": 22, "p99_ms": 32, "max_ms": 42},
            },
        },
    }


def sample_report(
    *,
    business_rps=620.0,
    attempt_start_rps=None,
    errors=0,
    completed_ledger_count=1,
    completed_trade_quantity=5,
):
    return {
        "reports": {
            "webapp_full_fill": {
                "summary": sample_summary(
                    business_rps=business_rps,
                    attempt_start_rps=attempt_start_rps,
                    errors=errors,
                ),
                "offer_status": "completed",
                "offer_remaining_quantity": 0,
                "persisted_trade_count": 1,
                "expected_winner_count": 1,
                "persistence": {
                    "offer_status": "completed",
                    "remaining_quantity": 0,
                    "original_quantity": 5,
                    "completed_trade_quantity": completed_trade_quantity,
                    "persisted_trade_count": 1,
                    "completed_ledger_count": completed_ledger_count,
                    "trades_without_completed_ledger_count": 0,
                    "failed_internal_ledger_count": 0,
                },
            }
        }
    }


def sample_observability():
    return {
        "db_pool": {"checked_out": 3, "overflow": 0},
        "redis": {"p95_ms": 2, "errors": 0},
        "sync": {"max_lag_seconds": 0.4},
        "worker_backlog": {"unsynced_change_logs": 0},
    }


class BotWebappCapacityReportTests(unittest.TestCase):
    def test_build_capacity_report_contains_required_release_gate_fields(self):
        report = capacity.build_capacity_report(
            mixed_payload=sample_report(),
            observability=sample_observability(),
            telegram_gateway_boundary="mock",
            target_business_rps=600.0,
        )

        self.assertEqual(report["schema_version"], capacity.BOT_WEBAPP_CAPACITY_REPORT_SCHEMA_VERSION)
        self.assertEqual(report["attempt_start_rps"], 620.0)
        self.assertEqual(report["business_request_rps"], 620.0)
        self.assertEqual(report["telegram_update_rps"], 744.0)
        self.assertEqual(report["production_gate"]["status"], capacity.PRODUCTION_GATE_BLOCKED_STATUS)
        self.assertEqual(report["telegram_gateway_boundary"], "mock")
        self.assertEqual(report["observability"]["telegram_gateway_boundary"], "mock")
        self.assertEqual(report["roles"]["telegram_foreign"]["latency"]["p99_ms"], 31.0)
        self.assertEqual(report["roles"]["webapp_iran"]["counts"]["total"], 40)
        self.assertFalse(report["correctness_failures"])
        self.assertFalse(report["capacity_warnings"])

        validation = capacity.validate_capacity_report(report)
        self.assertEqual(validation["status"], "valid")
        self.assertEqual(validation["production_gate"], capacity.PRODUCTION_GATE_BLOCKED_STATUS)

    def test_low_attempt_start_rps_is_capacity_warning_not_correctness_failure(self):
        report = capacity.build_capacity_report(
            mixed_payload=sample_report(business_rps=550.0, attempt_start_rps=550.0),
            observability=sample_observability(),
            telegram_gateway_boundary="mock",
            target_business_rps=600.0,
        )

        self.assertEqual(report["correctness_failures"], [])
        self.assertEqual(len(report["capacity_warnings"]), 1)
        self.assertIn("below target", report["capacity_warnings"][0])

    def test_slow_ack_does_not_warn_when_attempt_start_rps_meets_target(self):
        report = capacity.build_capacity_report(
            mixed_payload=sample_report(business_rps=499.0, attempt_start_rps=600.6),
            observability=sample_observability(),
            telegram_gateway_boundary="mock",
            target_business_rps=600.0,
        )

        self.assertEqual(report["attempt_start_rps"], 600.6)
        self.assertEqual(report["business_request_rps"], 499.0)
        self.assertEqual(report["correctness_failures"], [])
        self.assertEqual(report["capacity_warnings"], [])

    def test_zero_top_level_hot_offer_values_are_preserved_without_persistence(self):
        mixed_payload = sample_report()
        report_payload = mixed_payload["reports"]["webapp_full_fill"]
        report_payload["persisted_trade_count"] = 0
        report_payload["expected_winner_count"] = 0
        report_payload["persistence"] = {}

        report = capacity.build_capacity_report(
            mixed_payload=mixed_payload,
            observability=sample_observability(),
            telegram_gateway_boundary="mock",
            target_business_rps=600.0,
        )

        hot_offer = report["scenarios"][0]["hot_offer"]
        self.assertEqual(hot_offer["remaining_quantity"], 0)
        self.assertEqual(hot_offer["persisted_trade_count"], 0)

    def test_trade_ledger_problem_is_correctness_failure(self):
        report = capacity.build_capacity_report(
            mixed_payload=sample_report(completed_ledger_count=0),
            observability=sample_observability(),
            telegram_gateway_boundary="mock",
            target_business_rps=600.0,
        )

        self.assertTrue(any("ledger is inconsistent" in item for item in report["correctness_failures"]))
        self.assertEqual(report["capacity_warnings"], [])

    def test_overtrade_is_correctness_failure(self):
        report = capacity.build_capacity_report(
            mixed_payload=sample_report(completed_trade_quantity=6),
            observability=sample_observability(),
            telegram_gateway_boundary="mock",
            target_business_rps=600.0,
        )

        self.assertTrue(any("over-traded" in item for item in report["correctness_failures"]))

    def test_validate_rejects_missing_observability_and_open_production_gate(self):
        report = capacity.build_capacity_report(
            mixed_payload=sample_report(),
            observability=sample_observability(),
            telegram_gateway_boundary="mock",
            target_business_rps=600.0,
        )

        broken = dict(report)
        broken["production_gate"] = {"status": "open"}
        with self.assertRaises(capacity.CapacityReportError):
            capacity.validate_capacity_report(broken)

        broken = dict(report)
        broken["observability"] = {"db_pool": {}, "redis": {}}
        with self.assertRaises(capacity.CapacityReportError):
            capacity.validate_capacity_report(broken)

        broken = dict(report)
        del broken["business_request_rps"]
        with self.assertRaises(capacity.CapacityReportError):
            capacity.validate_capacity_report(broken)

        broken = dict(report)
        del broken["attempt_start_rps"]
        with self.assertRaises(capacity.CapacityReportError):
            capacity.validate_capacity_report(broken)

        broken = dict(report)
        broken["telegram_gateway_boundary"] = "real"
        with self.assertRaises(capacity.CapacityReportError):
            capacity.validate_capacity_report(broken)


if __name__ == "__main__":
    unittest.main()
