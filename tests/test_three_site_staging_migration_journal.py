from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.three_site_staging_migration_journal import (
    MigrationJournal,
    MigrationJournalError,
    ROLE_PHASES,
)


class ThreeSiteStagingMigrationJournalTests(unittest.TestCase):
    def test_interrupted_phase_is_durably_rollback_only(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = MigrationJournal(Path(directory) / "role.json")
            journal.create(
                campaign_id="11111111-1111-4111-8111-111111111111",
                release_sha="a" * 40,
                plan_sha256="b" * 64,
                role="webapp_fi",
                role_compose_sha256="c" * 64,
                role_env_sha256="d" * 64,
                image_inventory_sha256="e" * 64,
            )
            started = journal.begin_phase("seed_restored")
            self.assertEqual(started["status"], "rollback_required")
            reloaded = MigrationJournal(journal.path).load()
            self.assertEqual(reloaded["started_phase"], "seed_restored")
            with self.assertRaises(MigrationJournalError):
                journal.begin_phase("database_configured")
            rolled_back = journal.complete_rollback()
            self.assertEqual(rolled_back["status"], "rolled_back")

    def test_phases_are_strictly_ordered_and_finish_requires_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = MigrationJournal(Path(directory) / "role.json")
            journal.create(
                campaign_id="11111111-1111-4111-8111-111111111111",
                release_sha="a" * 40,
                plan_sha256="b" * 64,
                role="witness",
                role_compose_sha256="c" * 64,
                role_env_sha256="d" * 64,
                image_inventory_sha256="e" * 64,
            )
            with self.assertRaisesRegex(MigrationJournalError, "out of order"):
                journal.begin_phase("database_configured")
            for phase in ROLE_PHASES["witness"]:
                journal.begin_phase(phase)
                journal.complete_phase(phase)
            committed = journal.commit(acceptance_evidence_sha256="c" * 64)
            self.assertEqual(committed["status"], "committed")
            self.assertEqual(journal.finish()["status"], "finished")

    def test_journal_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "role.json"
            journal = MigrationJournal(path)
            journal.create(
                campaign_id="11111111-1111-4111-8111-111111111111",
                release_sha="a" * 40,
                plan_sha256="b" * 64,
                role="bot_fi",
                role_compose_sha256="c" * 64,
                role_env_sha256="d" * 64,
                image_inventory_sha256="e" * 64,
            )
            path.write_text(path.read_text().replace('"status": "active"', '"status": "finished"'))
            path.chmod(0o600)
            with self.assertRaisesRegex(MigrationJournalError, "schema/state/hash"):
                journal.load()

    def test_non_hex_acceptance_digest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = MigrationJournal(Path(directory) / "role.json")
            journal.create(
                campaign_id="11111111-1111-4111-8111-111111111111",
                release_sha="a" * 40,
                plan_sha256="b" * 64,
                role="witness",
                role_compose_sha256="c" * 64,
                role_env_sha256="d" * 64,
                image_inventory_sha256="e" * 64,
            )
            for phase in ROLE_PHASES["witness"]:
                journal.begin_phase(phase)
                journal.complete_phase(phase)
            with self.assertRaises(MigrationJournalError):
                journal.commit(acceptance_evidence_sha256="z" * 64)

    def test_writer_lease_bootstrap_retry_reuses_one_durable_request_id(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = MigrationJournal(Path(directory) / "role.json")
            journal.create(
                campaign_id="11111111-1111-4111-8111-111111111111",
                release_sha="a" * 40,
                plan_sha256="b" * 64,
                role="webapp_fi",
                role_compose_sha256="c" * 64,
                role_env_sha256="d" * 64,
                image_inventory_sha256="e" * 64,
            )
            for phase in (
                "seed_restored",
                "database_configured",
                "private_ready",
            ):
                journal.begin_phase(phase)
                journal.complete_phase(phase)
            request_id = "22222222-2222-4222-8222-222222222222"
            started = journal.begin_writer_lease_bootstrap(
                request_id=request_id,
            )
            retried = journal.begin_writer_lease_bootstrap(
                request_id=request_id,
            )
            self.assertEqual(retried["state_sha256"], started["state_sha256"])
            with self.assertRaisesRegex(MigrationJournalError, "current state"):
                journal.begin_writer_lease_bootstrap(
                    request_id="33333333-3333-4333-8333-333333333333",
                )
            completed = journal.complete_phase("writer_lease_bootstrapped")
            self.assertEqual(completed["writer_lease_request_id"], request_id)
            self.assertIn(
                "writer_lease_bootstrapped",
                completed["completed_phases"],
            )

    def test_interrupted_writer_bootstrap_can_roll_back_with_forensic_request(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = MigrationJournal(Path(directory) / "role.json")
            journal.create(
                campaign_id="11111111-1111-4111-8111-111111111111",
                release_sha="a" * 40,
                plan_sha256="b" * 64,
                role="webapp_fi",
                role_compose_sha256="c" * 64,
                role_env_sha256="d" * 64,
                image_inventory_sha256="e" * 64,
            )
            for phase in (
                "seed_restored",
                "database_configured",
                "private_ready",
            ):
                journal.begin_phase(phase)
                journal.complete_phase(phase)
            request_id = "44444444-4444-4444-8444-444444444444"
            journal.begin_writer_lease_bootstrap(request_id=request_id)
            rolled_back = journal.complete_rollback()
            self.assertEqual(rolled_back["status"], "rolled_back")
            self.assertEqual(
                rolled_back["writer_lease_request_id"],
                request_id,
            )


if __name__ == "__main__":
    unittest.main()
