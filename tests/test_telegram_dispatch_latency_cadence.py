from pathlib import Path
import unittest

from pydantic import ValidationError

from core.config import Settings
from core.telegram_delivery_queue_limiter import _destination_digest
from core.telegram_dispatch_latency_cadence import (
    CADENCE_TRIAL_INTERVALS_SECONDS,
    SHARED_FLEET_MINIMUM_INTERVAL_SECONDS,
    locked_telegram_dispatch_cadence,
)
from tests.test_telegram_delivery_queue_config import _settings


class TelegramDispatchLatencyCadenceTests(unittest.TestCase):
    def test_production_interval_stays_locked_without_live_429_evidence(self):
        lock = locked_telegram_dispatch_cadence()
        fields = Settings.model_fields

        self.assertEqual(lock.schema_version, "telegram_dispatch_latency_cadence_v1")
        self.assertEqual(lock.evidence_kind, "code_derived_cadence_lock")
        self.assertEqual(
            lock.production_destination_interval_seconds,
            fields["telegram_delivery_queue_destination_min_interval_seconds"].default,
        )
        self.assertEqual(lock.production_destination_interval_seconds, 1.05)
        self.assertEqual(
            lock.shared_fleet_minimum_interval_seconds,
            SHARED_FLEET_MINIMUM_INTERVAL_SECONDS,
        )
        self.assertTrue(lock.shared_destination_gate)
        self.assertFalse(lock.method_dimension_enabled)
        self.assertFalse(lock.live_429_series_collected)
        self.assertEqual(lock.trial_intervals_seconds, CADENCE_TRIAL_INTERVALS_SECONDS)
        self.assertIn(1.05, lock.trial_intervals_seconds)
        self.assertTrue(any("was not lowered" in note for note in lock.notes))

    def test_shared_fleet_floor_and_shared_destination_digest_stay_in_force(self):
        with self.assertRaises(ValidationError):
            _settings(
                telegram_delivery_queue_shared_publisher_fleet_enabled=True,
                telegram_delivery_queue_destination_min_interval_seconds=1.00,
            )
        send_digest = _destination_digest("channel:-1001")
        edit_digest = _destination_digest("channel:-1001")
        self.assertEqual(send_digest, edit_digest)
        self.assertNotEqual(send_digest, _destination_digest("channel:-1002"))

    def test_cadence_document_does_not_invent_a_lower_production_rate(self):
        text = Path(
            "docs/TELEGRAM_DISPATCH_LATENCY_CADENCE_20260823.md"
        ).read_text(encoding="utf-8")

        self.assertIn("code_derived_cadence_lock", text)
        self.assertIn("۱٫۰۵", text)
        self.assertIn("destination_next", text)
        self.assertIn("جمع نشد", text)
        self.assertNotIn("p50=", text.lower())


if __name__ == "__main__":
    unittest.main()
