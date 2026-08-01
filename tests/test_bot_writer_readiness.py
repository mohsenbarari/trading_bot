from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


@unittest.skipUnless(os.geteuid() == 0, "readiness marker production contract requires root")
class BotWriterReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.marker = Path(self.temporary.name) / "ready.json"
        self.term_patcher = patch.object(
            writer_readiness,
            "require_application_writer_term",
            return_value=active_term(),
        )
        self.term_patcher.start()
        self.addCleanup(self.term_patcher.stop)

    def test_marker_is_private_fresh_and_bound_to_live_term(self) -> None:
        written = writer_readiness.write_writer_ready_marker(self.marker)
        checked = writer_readiness.check_writer_ready_marker(
            self.marker,
            maximum_age_seconds=45,
        )

        self.assertEqual(written, checked)
        self.assertEqual(self.marker.stat().st_mode & 0o777, 0o600)
        self.assertEqual(checked["writer_epoch"], 9)
        self.assertNotIn("token", " ".join(checked).lower())

    def test_stale_marker_or_changed_term_fails_closed(self) -> None:
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

    def test_unsafe_existing_marker_is_not_removed_or_accepted(self) -> None:
        self.marker.write_text("{}\n", encoding="ascii")
        self.marker.chmod(0o644)
        with self.assertRaisesRegex(writer_readiness.BotWriterReadinessError, "root-only"):
            writer_readiness.clear_writer_ready_marker(self.marker)
        self.assertTrue(self.marker.exists())

    def test_disabled_term_cannot_write_marker(self) -> None:
        with patch.object(writer_readiness, "require_application_writer_term", return_value=None), self.assertRaisesRegex(
            writer_readiness.BotWriterReadinessError, "requires enabled"
        ):
            writer_readiness.write_writer_ready_marker(self.marker)


if __name__ == "__main__":
    unittest.main()
