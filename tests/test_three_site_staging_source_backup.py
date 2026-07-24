from __future__ import annotations

import io
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts.run_three_site_staging_source_backup import (
    StagingBackupError,
    _compose_base,
    _wait_for_scratch_database,
    build_plan,
    confirmation_phrase,
    verify_backup_manifest,
    verify_tar_artifact,
)


class ThreeSiteStagingSourceBackupTests(unittest.TestCase):
    def test_restore_drill_waits_for_exact_database_not_socket_readiness(self):
        socket_ready = SimpleNamespace(returncode=0)
        database_missing = SimpleNamespace(returncode=2, stdout="")
        database_ready = SimpleNamespace(returncode=0, stdout="1\n")
        with patch(
            "scripts.run_three_site_staging_source_backup.subprocess.run",
            side_effect=[socket_ready, database_missing, socket_ready, database_ready],
        ) as run, patch("scripts.run_three_site_staging_source_backup.time.sleep") as sleep:
            _wait_for_scratch_database("scratch", attempts=2)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(sleep.call_count, 1)

    def test_webapp_backup_accepts_exact_iran_staging_project_only(self):
        prefix = _compose_base(
            Path("/secure/compose.yml"),
            Path("/secure/staging.env"),
            "webapp_fi",
            "trading_bot_staging_iran",
        )
        self.assertEqual(prefix[prefix.index("-p") + 1], "trading_bot_staging_iran")
        with self.assertRaisesRegex(StagingBackupError, "not approved"):
            _compose_base(
                Path("/secure/compose.yml"),
                Path("/secure/staging.env"),
                "bot_fi",
                "trading_bot_staging_iran",
            )

    def _manifest(self):
        return {
            "schema": "three-site-staging-source-backup-v2",
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "source_role": "webapp_fi",
            "source_release_sha": "b" * 40,
            "target_release_sha": "a" * 40,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_postgres_system_id": "9000000000000000001",
            "source_alembic_revision": "legacy-head",
            "source_freeze_evidence_sha256": "5" * 64,
            "redis_observation": {
                "dbsize": 3,
                "appendonly": True,
                "lastsave_unix": 1700000000,
                "restore": False,
            },
            "artifacts": {
                "postgres": {"path": "/secure/db", "bytes": 10, "sha256": "1" * 64},
                "uploads": {"path": "/secure/up", "bytes": 10, "sha256": "2" * 64, "safe_member_count": 1},
                "audit": {"path": "/secure/audit", "bytes": 10, "sha256": "3" * 64, "safe_member_count": 1},
            },
            "restore_drill": {
                "status": "passed",
                "restored_alembic_revision": "legacy-head",
                "scratch_postgres_system_id": "9000000000000000002",
                "database_fingerprint_sha256": "4" * 64,
                "database_row_count": 12,
                "public_table_count": 4,
            },
            "redis_restore": False,
            "application_mutation": False,
        }

    def test_backup_manifest_requires_independent_restore_and_exact_artifacts(self):
        manifest = self._manifest()
        result = verify_backup_manifest(
            manifest,
            campaign_id=manifest["campaign_id"],
            source_role="webapp_fi",
            source_release_sha="b" * 40,
            target_release_sha="a" * 40,
            verify_files=False,
        )
        self.assertEqual(result["status"], "verified")
        manifest["restore_drill"]["scratch_postgres_system_id"] = manifest[
            "source_postgres_system_id"
        ]
        with self.assertRaisesRegex(StagingBackupError, "restore-drill"):
            verify_backup_manifest(
                manifest,
                campaign_id=manifest["campaign_id"],
                source_role="webapp_fi",
                source_release_sha="b" * 40,
                target_release_sha="a" * 40,
                verify_files=False,
            )

    def test_plan_is_dry_and_bound_to_campaign_role_and_target_sha(self):
        args = SimpleNamespace(
            source_role="webapp_fi",
            expected_source_release_sha="b" * 40,
        )
        inventory = {
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "release_sha": "a" * 40,
        }
        result = build_plan(args, inventory)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["redis_restore"])
        self.assertEqual(
            result["required_confirmation"],
            confirmation_phrase(inventory["campaign_id"], "webapp_fi", "a" * 40),
        )

    def test_archive_verifier_rejects_links_and_parent_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            safe = Path(directory) / "safe.tar.gz"
            with tarfile.open(safe, "w:gz") as archive:
                info = tarfile.TarInfo("uploads/file.txt")
                payload = b"data"
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            self.assertEqual(verify_tar_artifact(safe), 1)

            unsafe = Path(directory) / "unsafe.tar.gz"
            with tarfile.open(unsafe, "w:gz") as archive:
                info = tarfile.TarInfo("../escape")
                payload = b"data"
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            with self.assertRaisesRegex(StagingBackupError, "unsafe member"):
                verify_tar_artifact(unsafe)


if __name__ == "__main__":
    unittest.main()
