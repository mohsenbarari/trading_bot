import unittest
from unittest.mock import Mock

from core.metrics import metrics_response_body, registry
from core.trading_observability import (
    log_trading_event,
    safe_trading_log_context,
    summarize_response_body,
)


class TradingObservabilityTests(unittest.TestCase):
    def setUp(self):
        registry.reset()

    def tearDown(self):
        registry.reset()

    def test_safe_trading_log_context_keeps_only_redacted_low_cardinality_fields(self):
        context = safe_trading_log_context(
            action="trade_execute",
            result="success",
            offer_id="42",
            trade_id="99",
            trade_number="1001",
            source_server="foreign",
            target_server="iran",
            status_code=201,
            error_class="RuntimeError",
            has_idempotency_key="idem-raw-secret",
            delegated_actor=True,
            chain_length=2,
            side_effect="telegram_message",
            reason="remote_home",
        )

        self.assertEqual(context["log_class"], "trading")
        self.assertEqual(context["action"], "trade_execute")
        self.assertEqual(context["result"], "success")
        self.assertEqual(context["offer_id"], 42)
        self.assertEqual(context["trade_id"], 99)
        self.assertEqual(context["trade_number"], 1001)
        self.assertEqual(context["source_server"], "foreign")
        self.assertEqual(context["target_server"], "iran")
        self.assertEqual(context["status_class"], "2xx")
        self.assertTrue(context["has_idempotency_key"])
        self.assertNotIn("idempotency_key", context)
        self.assertNotIn("mobile_number", context)
        self.assertNotIn("raw_body", context)

    def test_response_body_summary_never_contains_raw_payload(self):
        summary = summarize_response_body("mobile=09123456789 token=unsafe-token body")

        self.assertEqual(summary["response_body_size"], len("mobile=09123456789 token=unsafe-token body".encode()))
        self.assertRegex(summary["response_body_sha256"], r"^[a-f0-9]{64}$")
        self.assertNotIn("09123456789", repr(summary))
        self.assertNotIn("unsafe-token", repr(summary))

    def test_log_trading_event_records_low_cardinality_metrics_without_raw_ids(self):
        logger = Mock()

        extra = log_trading_event(
            logger,
            "trade_execute.accepted",
            action="trade_execute",
            result="success",
            offer_id=123,
            trade_id=456,
            trade_number=789,
            source_server="foreign",
            has_idempotency_key=True,
        )

        logger.info.assert_called_once_with("trade_execute.accepted", extra=extra)
        body = metrics_response_body()
        self.assertIn("trading_bot_trading_events_total", body)
        self.assertIn('action="trade_execute"', body)
        self.assertIn('result="success"', body)
        for raw in ("123", "456", "789", "idem"):
            self.assertNotIn(raw, body)

    def test_side_effect_metrics_are_separate_and_bounded(self):
        logger = Mock()

        log_trading_event(
            logger,
            "trade_telegram_message_failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="telegram_message",
            error_class="RequestError",
        )

        logger.warning.assert_called_once()
        body = metrics_response_body()
        self.assertIn("trading_bot_trading_side_effects_total", body)
        self.assertIn('side_effect="telegram_message"', body)
        self.assertIn('result="failure"', body)
        self.assertNotIn("RequestError", body)

    def test_trade_commit_slow_context_accepts_duration_without_raw_payload(self):
        logger = Mock()

        extra = log_trading_event(
            logger,
            "trade_commit.slow",
            level="warning",
            action="trade_commit",
            result="slow",
            total_duration_ms=1234.567,
        )

        logger.warning.assert_called_once_with("trade_commit.slow", extra=extra)
        self.assertEqual(extra["action"], "trade_commit")
        self.assertEqual(extra["result"], "slow")
        self.assertEqual(extra["total_duration_ms"], 1234.57)


if __name__ == "__main__":
    unittest.main()
