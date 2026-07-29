from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from core.production_snapshot_promotion import (
    MAX_PROMOTION_SNAPSHOT_AGE_SECONDS,
    MAX_STAGED_SNAPSHOT_AGE_SECONDS,
    PROMOTION_PROOF_SCHEMA,
    SnapshotPromotionError,
    build_promotion_proof,
    canonical_json_bytes,
    parse_restore_receipt,
    validate_promotion_proof,
)


NOW = datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc)
RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"


def _artifact(*, plain_hash: str, cipher_hash: str, key: str, version: str) -> dict:
    return {
        "sha256": plain_hash,
        "bytes": 123,
        "object_key": key,
        "version_id": version,
        "ciphertext_sha256": cipher_hash,
        "ciphertext_bytes": 456,
    }


def restore_receipt(
    *,
    source_site: str = "webapp_fi",
    destination_site: str = "webapp_ir",
    source_generation: str = "fi-generation-7",
    published_at: datetime | None = None,
) -> dict:
    published = published_at or NOW - timedelta(seconds=12)
    database = _artifact(
        plain_hash="a" * 64,
        cipher_hash="b" * 64,
        key="campaign/snapshots/database.age",
        version="version-db-1",
    )
    uploads = _artifact(
        plain_hash="c" * 64,
        cipher_hash="d" * 64,
        key="campaign/snapshots/uploads.age",
        version="version-uploads-1",
    )
    payload = {
        "schema": "gold-trade-snapshot-restore-receipt-v1",
        "status": "restored_verified",
        "source_site": source_site,
        "destination_site": destination_site,
        "source_generation": source_generation,
        "snapshot_id": "snapshot-20260729-0001",
        "release_sha": RELEASE_SHA,
        "alembic_revision": "f2c7d8e9a0b1",
        "source_db_snapshot_started_at": (published - timedelta(seconds=3)).isoformat(),
        "source_capture_completed_at": (published - timedelta(seconds=1)).isoformat(),
        "published_at": published.isoformat(),
        "ready_at": (published + timedelta(seconds=1)).isoformat(),
        "restored_at": (published + timedelta(seconds=2)).isoformat(),
        "restore_verified_at": (published + timedelta(seconds=3)).isoformat(),
        "stage_receipt_sha256": "e" * 64,
        "restored_database_sha256": database["sha256"],
        "restored_uploads_sha256": uploads["sha256"],
        "database": database,
        "uploads": uploads,
        "manifest": {
            "object_key": "campaign/snapshots/manifest.age",
            "version_id": "version-manifest-1",
            "ciphertext_sha256": "f" * 64,
            "ciphertext_bytes": 789,
        },
    }
    payload["receipt_sha256"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return payload


class SnapshotPromotionTests(unittest.TestCase):
    def test_fresh_verified_fi_receipt_allows_ir_promotion(self):
        receipt = parse_restore_receipt(restore_receipt(), action="promote_ir", now=NOW)

        self.assertEqual(receipt.source_site, "webapp_fi")
        self.assertEqual(receipt.destination_site, "webapp_ir")
        self.assertEqual(receipt.snapshot_age_seconds, 15)
        self.assertEqual(receipt.release_sha, RELEASE_SHA)

    def test_promotion_rejects_receipt_older_than_bounded_rpo(self):
        payload = restore_receipt(
            published_at=NOW - timedelta(seconds=MAX_PROMOTION_SNAPSHOT_AGE_SECONDS + 1)
        )

        with self.assertRaisesRegex(SnapshotPromotionError, "150 second"):
            parse_restore_receipt(payload, action="promote_ir", now=NOW)

    def test_promotion_uses_db_snapshot_start_not_later_publication(self):
        payload = restore_receipt(published_at=NOW - timedelta(seconds=140))
        payload["source_db_snapshot_started_at"] = (
            NOW - timedelta(seconds=MAX_PROMOTION_SNAPSHOT_AGE_SECONDS + 1)
        ).isoformat()
        payload["source_capture_completed_at"] = (NOW - timedelta(seconds=150)).isoformat()
        payload["ready_at"] = (NOW - timedelta(seconds=121)).isoformat()
        payload["restored_at"] = (NOW - timedelta(seconds=120)).isoformat()
        payload["restore_verified_at"] = (NOW - timedelta(seconds=119)).isoformat()
        unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
        payload["receipt_sha256"] = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()

        with self.assertRaisesRegex(SnapshotPromotionError, "150 second"):
            parse_restore_receipt(payload, action="promote_ir", now=NOW)

    def test_promotion_accepts_a_recently_staged_candidate_after_lease_handoff(self):
        payload = restore_receipt(published_at=NOW - timedelta(seconds=60))
        payload["source_db_snapshot_started_at"] = (NOW - timedelta(seconds=90)).isoformat()
        payload["source_capture_completed_at"] = (NOW - timedelta(seconds=89)).isoformat()
        payload["ready_at"] = (NOW - timedelta(seconds=60)).isoformat()
        payload["restored_at"] = (NOW - timedelta(seconds=59)).isoformat()
        payload["restore_verified_at"] = (NOW - timedelta(seconds=58)).isoformat()
        unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
        payload["receipt_sha256"] = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()

        receipt = parse_restore_receipt(payload, action="promote_ir", now=NOW)

        self.assertEqual(receipt.snapshot_age_seconds, 90)

    def test_promotion_rejects_a_candidate_that_took_too_long_to_stage(self):
        payload = restore_receipt(published_at=NOW - timedelta(seconds=30))
        payload["ready_at"] = (NOW - timedelta(seconds=1)).isoformat()
        payload["restored_at"] = NOW.isoformat()
        payload["restore_verified_at"] = (NOW + timedelta(seconds=1)).isoformat()
        unsigned = {key: value for key, value in payload.items() if key != "receipt_sha256"}
        payload["receipt_sha256"] = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()

        with self.assertRaisesRegex(
            SnapshotPromotionError,
            f"{MAX_STAGED_SNAPSHOT_AGE_SECONDS} second standby bound",
        ):
            parse_restore_receipt(payload, action="promote_ir", now=NOW)

    def test_failback_requires_exact_final_ir_generation(self):
        payload = restore_receipt(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            source_generation="ir-generation-19",
        )
        receipt = parse_restore_receipt(
            payload,
            action="failback_fi",
            expected_source_generation="ir-generation-19",
            now=NOW,
        )
        self.assertEqual(receipt.source_generation, "ir-generation-19")

        with self.assertRaisesRegex(SnapshotPromotionError, "final source generation"):
            parse_restore_receipt(
                payload,
                action="failback_fi",
                expected_source_generation="ir-generation-18",
                now=NOW,
            )

    def test_receipt_hash_and_restore_hash_must_bind_exact_snapshot(self):
        payload = restore_receipt()
        payload["restored_database_sha256"] = "0" * 64
        with self.assertRaisesRegex(SnapshotPromotionError, "restored database hash"):
            parse_restore_receipt(payload, action="promote_ir", now=NOW)

        payload = restore_receipt()
        payload["receipt_sha256"] = "0" * 64
        with self.assertRaisesRegex(SnapshotPromotionError, "receipt hash"):
            parse_restore_receipt(payload, action="promote_ir", now=NOW)

    def test_proof_is_self_hashed_and_binds_target_lease(self):
        snapshot = parse_restore_receipt(restore_receipt(), action="promote_ir", now=NOW)
        witness_proof = {
            "holder_site": "webapp_ir",
            "writer_epoch": 8,
            "lease_id": "lease-8",
            "issued_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(seconds=180)).isoformat(),
        }
        proof = build_promotion_proof(
            action="promote_ir",
            operation_id="9af4a844-6ce1-4b77-a6d2-627667a46175",
            snapshot=snapshot,
            witness_proof=witness_proof,
        )

        self.assertEqual(proof["schema"], PROMOTION_PROOF_SCHEMA)
        self.assertEqual(proof["target_site"], "webapp_ir")
        self.assertEqual(proof["epoch"], 8)
        unsigned = {key: value for key, value in proof.items() if key != "proof_sha256"}
        self.assertEqual(proof["proof_sha256"], hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest())
        self.assertEqual(
            validate_promotion_proof(proof, now=NOW)["lease_id"],
            "lease-8",
        )

        with self.assertRaisesRegex(SnapshotPromotionError, "DB snapshot recovery window"):
            validate_promotion_proof(
                proof,
                now=NOW + timedelta(seconds=MAX_PROMOTION_SNAPSHOT_AGE_SECONDS),
            )

        proof["target_site"] = "webapp_fi"
        with self.assertRaisesRegex(SnapshotPromotionError, "direction"):
            validate_promotion_proof(proof, now=NOW)


if __name__ == "__main__":
    unittest.main()
