from pathlib import Path
import unittest

from core.config import Settings
from core.telegram_dispatch_latency_baseline import code_derived_latency_floors
from core.telegram_dispatch_latency_midpoint import (
    MIDPOINT_AFTER_STAGES,
    ack_path_emits_wakeup,
    b2b_dispatch_skips_auth_middleware,
    claim_index_covers_sent,
    code_derived_latency_midpoint,
    current_code_derived_latency_floors,
    dispatcher_uses_configured_batch,
    gateway_reuses_http_client,
    retention_purges_terminal_commands,
)


class TelegramDispatchLatencyMidpointTests(unittest.TestCase):
    def test_current_floors_follow_settings_and_completed_low_risk_stages(self):
        fields = Settings.model_fields
        current = current_code_derived_latency_floors()

        self.assertEqual(current.evidence_kind, "code_derived_current_floors")
        self.assertEqual(
            current.destination_min_interval_seconds,
            fields["telegram_delivery_queue_destination_min_interval_seconds"].default,
        )
        self.assertEqual(
            current.b2b_dispatch_batch_size,
            fields["telegram_b2b_dispatch_batch_size"].default,
        )
        self.assertGreater(current.b2b_dispatch_batch_size, 1)
        self.assertEqual(current.telegram_calls_per_channel_job, 3)
        self.assertEqual(current.dead_wait_after_ack_seconds_max, 0.0)
        self.assertEqual(current.full_batch_cycle_sleep_seconds, 0.0)
        self.assertTrue(current.shared_destination_gate)
        self.assertTrue(current.gateway_reuses_http_client)
        self.assertTrue(current.b2b_dispatch_skips_auth_middleware)
        self.assertTrue(current.claim_index_covers_sent)
        self.assertTrue(current.retention_purges_terminal_commands)

    def test_inspectors_match_current_source(self):
        self.assertTrue(ack_path_emits_wakeup())
        self.assertTrue(gateway_reuses_http_client())
        self.assertTrue(dispatcher_uses_configured_batch())
        self.assertTrue(b2b_dispatch_skips_auth_middleware())
        self.assertTrue(claim_index_covers_sent())
        self.assertTrue(retention_purges_terminal_commands())

    def test_midpoint_compares_locked_baseline_without_inventing_percentiles(self):
        midpoint = code_derived_latency_midpoint()
        baseline = code_derived_latency_floors()
        by_metric = {row.metric: row for row in midpoint.rows}

        self.assertEqual(midpoint.schema_version, "telegram_dispatch_latency_midpoint_v1")
        self.assertEqual(midpoint.evidence_kind, "code_derived_midpoint")
        self.assertEqual(midpoint.compared_after_stages, MIDPOINT_AFTER_STAGES)
        self.assertFalse(midpoint.live_percentiles_collected)
        self.assertEqual(midpoint.baseline, baseline)
        self.assertTrue(by_metric["dead_wait_after_ack_seconds_max"].changed)
        self.assertEqual(by_metric["dead_wait_after_ack_seconds_max"].current, 0.0)
        self.assertTrue(by_metric["b2b_dispatch_batch_size"].changed)
        self.assertFalse(by_metric["telegram_calls_per_channel_job"].changed)
        self.assertFalse(by_metric["destination_min_interval_seconds"].changed)
        self.assertFalse(by_metric["shared_destination_gate"].changed)
        self.assertTrue(by_metric["gateway_reuses_http_client"].changed)
        self.assertTrue(
            any("percentiles were not collected" in note for note in midpoint.notes)
        )

    def test_midpoint_document_does_not_invent_observed_percentiles(self):
        text = Path(
            "docs/TELEGRAM_DISPATCH_LATENCY_MIDPOINT_20260823.md"
        ).read_text(encoding="utf-8")

        self.assertIn("code_derived_midpoint", text)
        self.assertIn("صدک مشاهده‌شده ثبت نشد", text)
        self.assertIn("destination_next", text)
        self.assertIn("۱٫۰۵", text)
        self.assertNotIn("p50=", text.lower())
        self.assertNotIn("p95=", text.lower())


if __name__ == "__main__":
    unittest.main()
