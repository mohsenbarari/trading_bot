from datetime import datetime, timezone
import unittest

from scripts.run_three_site_stage3_postgres_restore_drill import (
    RestoreDrillError,
    logical_dump_hash,
    verify_drill_document,
)


class Stage3PostgresRestoreDrillTests(unittest.TestCase):
    def document(self):
        digest = "a" * 64
        return {
            "schema": "three-site-stage3-postgres-restore-drill-v1",
            "status": "backup-restored-and-compared",
            "campaign_id": "fd34231d-f52e-498a-aab4-438c99d88fc5",
            "release_sha": "0" * 40,
            "role": "bot-fi",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "source_database": "trading_bot_bot_fi_staging",
            "scratch_database": "stage3_restore_fd34231d_bot_fi",
            "postgres_system_id": "7669505181206511650",
            "backup": {
                "path": "/srv/trading-bot-three-site-staging-data/backups/drill.dump",
                "bytes": 4096,
                "sha256": "b" * 64,
                "format": "pg_dump-custom",
            },
            "source_fingerprints": {
                "schema_sha256": digest,
                "data_sha256": "c" * 64,
            },
            "restored_fingerprints": {
                "schema_sha256": digest,
                "data_sha256": "c" * 64,
            },
            "scratch_removed": True,
            "database_restarted": False,
            "application_started": False,
            "production_touched": False,
        }

    def verify(self, document):
        return verify_drill_document(
            document,
            role="bot-fi",
            campaign_id="fd34231d-f52e-498a-aab4-438c99d88fc5",
            release_sha="0" * 40,
            postgres_system_id="7669505181206511650",
        )

    def test_matching_backup_restore_evidence_passes(self):
        self.assertEqual(self.verify(self.document())["status"], "verified")

    def test_logical_drift_fails_closed(self):
        document = self.document()
        document["restored_fingerprints"]["data_sha256"] = "d" * 64
        with self.assertRaisesRegex(RestoreDrillError, "fingerprint"):
            self.verify(document)

    def test_cleanup_or_production_claim_fails_closed(self):
        for field in ("scratch_removed", "production_touched"):
            document = self.document()
            document[field] = not document[field]
            with self.subTest(field=field), self.assertRaises(RestoreDrillError):
                self.verify(document)

    def test_pg_dump_restrict_nonce_is_not_part_of_logical_identity(self):
        first = b"-- dump\n\\restrict ABC123\nCREATE TABLE x ();\n\\unrestrict ABC123\n"
        second = b"-- dump\n\\restrict XYZ789\nCREATE TABLE x ();\n\\unrestrict XYZ789\n"
        self.assertEqual(logical_dump_hash(first), logical_dump_hash(second))
        self.assertNotEqual(
            logical_dump_hash(first),
            logical_dump_hash(second.replace(b"x", b"y")),
        )


if __name__ == "__main__":
    unittest.main()
