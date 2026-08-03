from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.dr_durability_health import (
    BlobReceiptEvidence,
    DurabilityHealthError,
    build_durability_health_update,
)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
RELEASE = "a" * 40
GID = "_sa_1234567890abcdef1234567890abcdef"


def _journal(*, release: str = RELEASE, state: str = "committed"):
    return {
        "state": state,
        "local_transaction_gid": GID,
        "prepared_transaction_gid": GID,
        "release_sha": release,
        "transaction_hash": "b" * 64,
        "ciphertext_hash": "c" * 64,
    }


def _blob(*, acknowledged_at: datetime = NOW - timedelta(seconds=10), destination: str = "webapp_ir"):
    return BlobReceiptEvidence(
        content_hash="d" * 64,
        destination_site=destination,
        delivery_status="acknowledged",
        acknowledged_at=acknowledged_at,
        acknowledgement_hash="e" * 64,
        manifest_state="uploaded",
        object_version_id="version-immutable-123",
        object_ciphertext_hash="f" * 64,
        object_ciphertext_size=2048,
        encryption_key_id="staging-blob-key-v1",
        encryption_algorithm="AES-256-GCM-v1",
    )


class DurabilityHealthTests(unittest.TestCase):
    def _build(self, **changes):
        values = {
            "connectivity_mode": "online",
            "connectivity_evidence_hash": "1" * 64,
            "connectivity_evidence_expires_at": NOW + timedelta(seconds=90),
            "journal_gid": GID,
            "journal": _journal(),
            "blob": _blob(),
            "release_sha": RELEASE,
            "operator": "stage4r-controller",
            "now": NOW,
            "max_blob_age_seconds": 120,
            "ttl_seconds": 60,
        }
        values.update(changes)
        return build_durability_health_update(**values)

    def test_current_committed_journal_and_exact_blob_ack_build_bounded_evidence(self):
        result = self._build()
        self.assertEqual(len(result.evidence_hash), 64)
        self.assertEqual(result.evidence_expires_at, NOW + timedelta(seconds=60))
        self.assertEqual(result.updated_by, "stage4r-controller")

    def test_refresh_never_extends_connectivity_observation_expiry(self):
        result = self._build(
            connectivity_evidence_expires_at=NOW + timedelta(seconds=25),
            ttl_seconds=60,
        )
        self.assertEqual(result.evidence_expires_at, NOW + timedelta(seconds=25))

    def test_rejects_non_online_or_expired_connectivity_evidence(self):
        with self.assertRaisesRegex(DurabilityHealthError, "not online"):
            self._build(connectivity_mode="isolated")
        with self.assertRaisesRegex(DurabilityHealthError, "expired"):
            self._build(connectivity_evidence_expires_at=NOW)

    def test_rejects_journal_not_bound_to_a_current_release_committed_gid(self):
        with self.assertRaisesRegex(DurabilityHealthError, "committed current-release"):
            self._build(journal=_journal(state="prepared"))
        with self.assertRaisesRegex(DurabilityHealthError, "committed current-release"):
            self._build(journal=_journal(release="9" * 40))

    def test_rejects_stale_blob_or_missing_exact_version(self):
        with self.assertRaisesRegex(DurabilityHealthError, "stale"):
            self._build(blob=_blob(acknowledged_at=NOW - timedelta(seconds=121)))
        blob = _blob()
        with self.assertRaisesRegex(DurabilityHealthError, "exact Object Storage version"):
            self._build(blob=BlobReceiptEvidence(**{**blob.__dict__, "object_version_id": None}))

    def test_rejects_blob_ack_for_any_destination_other_than_webapp_ir(self):
        with self.assertRaisesRegex(DurabilityHealthError, "WebApp-IR"):
            self._build(blob=_blob(destination="bot_fi"))


if __name__ == "__main__":
    unittest.main()
