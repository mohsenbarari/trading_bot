from __future__ import annotations

from datetime import datetime, timezone
import unittest

from scripts.restore_three_site_staging_sources import SourceRestoreError, verify_restore_input


def _evidence():
    return {
        "schema": "three-site-staging-source-freeze-v1",
        "campaign_id": "11111111-1111-4111-8111-111111111111",
        "target_release_sha": "a" * 40,
        "project_name": "trading_bot_staging",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_roles": [
            {"source_role": "webapp_fi", "app_service": "app", "source_release_sha": "b" * 40}
        ],
        "previously_running_services": ["app", "db", "redis", "sync_worker"],
        "stopped_services": ["app", "sync_worker"],
        "running_services": ["db", "redis"],
        "postgres": {
            "system_id": "8000000000000000001",
            "alembic_revision": "f1b6e7f8a9dc",
            "database_fingerprint_sha256": "c" * 64,
            "database_row_count": 2,
            "public_table_count": 3,
        },
        "redis_observation": {
            "dbsize": 1, "appendonly": True, "lastsave_unix": 1700000000, "restore": False
        },
    }


class RestoreThreeSiteStagingSourcesTests(unittest.TestCase):
    def test_exact_previous_service_set_is_the_only_restore_target(self):
        evidence = _evidence()
        result = verify_restore_input(
            evidence,
            campaign_id=evidence["campaign_id"],
            release_sha=evidence["target_release_sha"],
            project_name="trading_bot_staging",
        )
        self.assertEqual(result["services_to_start"], ["app", "sync_worker"])

    def test_evidence_cannot_restart_an_unrecorded_service(self):
        evidence = _evidence()
        evidence["stopped_services"].append("bot")
        with self.assertRaisesRegex(SourceRestoreError, "cannot authorize"):
            verify_restore_input(
                evidence,
                campaign_id=evidence["campaign_id"],
                release_sha=evidence["target_release_sha"],
                project_name="trading_bot_staging",
            )


if __name__ == "__main__":
    unittest.main()
