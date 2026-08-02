from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from scripts.build_three_site_staging_migration_plan import build_plan
from scripts.verify_three_site_staging_migration_plan import ORDERED_PHASES


class BuildThreeSiteStagingMigrationPlanTests(unittest.TestCase):
    def test_builder_binds_exact_evidence_and_reviewed_order(self):
        inventory = {
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "release_sha": "a" * 40,
            "deployment_id": "stage4-test",
        }
        freezes = {}
        backups = {}
        seeds = {}
        for index, role in enumerate(("bot_fi", "webapp_fi"), start=1):
            freezes[role] = {"source_roles": [{"source_role": role}], "index": index}
            backups[role] = {
                "source_role": role,
                "source_release_sha": "b" * 40,
                "source_postgres_system_id": str(8000000000000000000 + index),
                "source_alembic_revision": "f2c7d8e9a0b1",
                "restore_drill": {"database_fingerprint_sha256": str(index) * 64},
            }
            seeds[role] = {
                "source_role": role,
                "object_prefix": f"staging/test/seed/{role}/",
                "encryption": "age-x25519",
                "recipient_fingerprint": "c" * 64,
                "readback_evidence_sha256": "d" * 64,
            }
        images = {
            role: {
                "role_compose_sha256": "e" * 64,
                "role_env_sha256": "f" * 64,
            }
            for role in ("bot_fi", "webapp_fi", "webapp_ir", "witness")
        }
        with patch(
            "scripts.build_three_site_staging_migration_plan.verify_backup_manifest"
        ), patch(
            "scripts.build_three_site_staging_migration_plan.verify_image_document",
            return_value={"status": "verified"},
        ):
            plan = build_plan(
                inventory=inventory,
                freezes=freezes,
                backups=backups,
                seeds=seeds,
                images=images,
                created_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
                ttl_minutes=180,
            )
        self.assertEqual(plan["ordered_phases"], list(ORDERED_PHASES))
        self.assertFalse(plan["source_freeze"]["redis_restore"])
        self.assertEqual(
            {row["target_role"]: row["source_role"] for row in plan["target_seed_map"]},
            {
                "bot_fi": "bot_fi",
                "webapp_fi": "webapp_fi",
                "webapp_ir": "webapp_fi",
                "witness": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
