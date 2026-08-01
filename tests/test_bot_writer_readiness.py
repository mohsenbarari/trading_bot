from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiogram.methods import GetUpdates

from bot import writer_readiness
from core.application_writer_term import ValidatedWriterTerm


def active_term(*, epoch: int = 9, lease_id: str = "lease-9") -> ValidatedWriterTerm:
    now = datetime.now(timezone.utc)
    return ValidatedWriterTerm(
        holder_site="webapp_fi",
        writer_epoch=epoch,
        lease_id=lease_id,
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(seconds=55),
        witness_transition_id="transition-9",
    )


class BotWriterReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.marker = Path(self.temporary.name) / "ready.json"
        self.term = active_term()
        self.term_patcher = patch.object(
            writer_readiness,
            "require_application_writer_term",
            return_value=self.term,
        )
        self.term_patcher.start()
        self.addCleanup(self.term_patcher.stop)

    def test_write_and_check_bind_fresh_marker_to_live_term_without_secrets(self) -> None:
        payload = writer_readiness.write_writer_ready_marker(self.marker)

        checked = writer_readiness.check_writer_ready_marker(
            self.marker,
            maximum_age_seconds=45,
        )

        self.assertEqual(payload, checked)
        self.assertEqual(writer_readiness.MARKER_SCHEMA, checked["schema"])
        self.assertEqual(os.getpid(), checked["pid"])
        self.assertNotIn("token", " ".join(checked).lower())
        self.assertEqual(0o600, self.marker.stat().st_mode & 0o777)

    def test_stale_marker_and_term_mismatch_fail_closed(self) -> None:
        base = datetime.now(timezone.utc)
        writer_readiness.write_writer_ready_marker(self.marker, now=base)

        with self.assertRaisesRegex(writer_readiness.BotWriterReadinessError, "stale"):
            writer_readiness.check_writer_ready_marker(
                self.marker,
                maximum_age_seconds=10,
                now=base + timedelta(seconds=11),
            )

        with patch.object(
            writer_readiness,
            "require_application_writer_term",
            return_value=active_term(epoch=10, lease_id="lease-10"),
        ), self.assertRaisesRegex(writer_readiness.BotWriterReadinessError, "term epoch"):
            writer_readiness.check_writer_ready_marker(
                self.marker,
                maximum_age_seconds=45,
                now=base + timedelta(seconds=1),
            )

    def test_missing_enabled_term_or_unsafe_leaf_is_rejected(self) -> None:
        with patch.object(writer_readiness, "require_application_writer_term", return_value=None), self.assertRaisesRegex(
            writer_readiness.BotWriterReadinessError, "requires enabled"
        ):
            writer_readiness.write_writer_ready_marker(self.marker)

        self.marker.write_text("{}\n", encoding="ascii")
        self.marker.chmod(0o644)
        with self.assertRaisesRegex(writer_readiness.BotWriterReadinessError, "root-only"):
            writer_readiness.check_writer_ready_marker(self.marker, maximum_age_seconds=45)

    def test_clear_removes_only_a_valid_private_marker(self) -> None:
        writer_readiness.write_writer_ready_marker(self.marker)
        writer_readiness.clear_writer_ready_marker(self.marker)
        self.assertFalse(self.marker.exists())
        writer_readiness.clear_writer_ready_marker(self.marker)


if __name__ == "__main__":
    unittest.main()
