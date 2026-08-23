from pathlib import Path
import unittest

from core.config import Settings
from core.telegram_dispatch_latency_baseline import (
    BASELINE_CODE_BASE,
    BASELINE_COMMIT,
    code_derived_latency_floors,
)


class TelegramDispatchLatencyBaselineTests(unittest.TestCase):
    def test_floors_lock_current_defaults_and_single_command_cycles(self):
        floors = code_derived_latency_floors()
        fields = Settings.model_fields

        self.assertEqual(floors.schema_version, "telegram_dispatch_latency_baseline_v1")
        self.assertEqual(floors.evidence_kind, "code_derived_floors")
        self.assertEqual(floors.code_base, BASELINE_CODE_BASE)
        self.assertEqual(floors.roadmap_commit, BASELINE_COMMIT)
        self.assertEqual(
            floors.destination_min_interval_seconds,
            fields["telegram_delivery_queue_destination_min_interval_seconds"].default,
        )
        self.assertEqual(
            floors.publisher_idle_poll_interval_seconds,
            fields["telegram_delivery_queue_publisher_idle_poll_interval_seconds"].default,
        )
        self.assertEqual(
            floors.b2b_dispatch_interval_seconds,
            fields["telegram_b2b_dispatch_interval_seconds"].default,
        )
        self.assertEqual(floors.b2b_dispatch_batch_size, 1)
        self.assertEqual(floors.telegram_calls_per_channel_job, 3)
        self.assertEqual(floors.dead_wait_after_ack_seconds_max, 1.0)
        self.assertTrue(floors.shared_destination_gate)
        self.assertTrue(any("limit=1" in note for note in floors.notes))

    def test_baseline_document_does_not_invent_observed_percentiles(self):
        text = Path(
            "docs/TELEGRAM_DISPATCH_LATENCY_BASELINE_20260823.md"
        ).read_text(encoding="utf-8")

        self.assertIn("code_derived_floors", text)
        self.assertIn("صدک مشاهده‌شده ثبت نشد", text)
        self.assertIn("destination_next", text)
        self.assertNotIn("p50=", text.lower())


if __name__ == "__main__":
    unittest.main()
