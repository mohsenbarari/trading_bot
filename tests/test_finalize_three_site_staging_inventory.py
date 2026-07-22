from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.finalize_three_site_staging_inventory import (
    InventoryFinalizationError,
    _load_snapshot,
    finalize_inventory,
)
from scripts.verify_three_site_staging_host_identity import ROLE_VOLUME_FIELDS
from tests.test_three_site_staging_signed_inventory import _inventory, _signed_documents


class FinalizeThreeSiteStagingInventoryTests(unittest.TestCase):
    def _documents(self):
        planned = _inventory()
        planned["inventory_stage"] = "planned"
        for role in planned["roles"]:
            role["postgres_system_id"] = None
        policy, approval = _signed_documents(planned, datetime.now(timezone.utc))
        snapshots = {}
        for number, cli_role in enumerate(
            ("bot-fi", "webapp-fi", "webapp-ir", "witness"), 1
        ):
            physical = cli_role.replace("-", "_")
            role = next(item for item in planned["roles"] if item["role"] == physical)
            snapshots[cli_role] = {
                "schema": "three-site-staging-host-snapshot-v2",
                "role": cli_role,
                "stage": "measure-provisioned",
                "release_sha": planned["release_sha"],
                "worktree_clean": True,
                "machine_id": role["machine_id"],
                "docker_daemon_id": role["docker_daemon_id"],
                "ipv4_addresses": [role["host_ip"]],
                "timezone": "UTC",
                "ntp_synchronized": True,
                "clock_measurement_tool": "chronyc",
                "volumes": {
                    field: role[field] for field in ROLE_VOLUME_FIELDS[cli_role]
                },
                "storage": {
                    "root": role["storage_root"],
                    "source": "/dev/disk/by-uuid/" + role["storage_mount_uuid"],
                    "filesystem": "ext4",
                    "mount_uuid": role["storage_mount_uuid"],
                    "total_bytes": 60 * 1024**3,
                    "available_bytes": 55 * 1024**3,
                },
                "resource_boundary": {
                    "slice": "trading-bot-three-site-staging.slice",
                    **role["resource_limits"],
                },
                "postgres_system_id": str(9100000000000000000 + number),
            }
        return planned, policy, approval, snapshots

    def test_measured_hosts_create_a_structurally_valid_unsigned_final_inventory(self):
        planned, policy, approval, snapshots = self._documents()
        result = finalize_inventory(
            planned=planned,
            approval=approval,
            approval_policy=policy,
            snapshots=snapshots,
        )
        self.assertEqual(result["inventory_stage"], "provisioned")
        self.assertTrue(all(role["postgres_system_id"] for role in result["roles"]))

    def test_duplicate_or_production_postgres_identity_is_rejected(self):
        planned, policy, approval, snapshots = self._documents()
        snapshots["webapp-ir"]["postgres_system_id"] = snapshots["webapp-fi"][
            "postgres_system_id"
        ]
        with self.assertRaisesRegex(InventoryFinalizationError, "not distinct"):
            finalize_inventory(
                planned=planned,
                approval=approval,
                approval_policy=policy,
                snapshots=snapshots,
            )

    def test_snapshot_loader_requires_owner_only_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.json"
            snapshot.write_text(json.dumps({"role": "bot-fi"}), encoding="utf-8")
            snapshot.chmod(0o644)
            with self.assertRaisesRegex(InventoryFinalizationError, "mode-0600"):
                _load_snapshot(snapshot)
            snapshot.chmod(0o600)
            self.assertEqual(_load_snapshot(snapshot), {"role": "bot-fi"})

        planned, policy, approval, snapshots = self._documents()
        production_id = snapshots["witness"]["postgres_system_id"]
        planned["production_boundaries"]["postgres_system_ids"] = [production_id]
        policy, approval = _signed_documents(planned, datetime.now(timezone.utc))
        with self.assertRaisesRegex(InventoryFinalizationError, "overlaps production"):
            finalize_inventory(
                planned=planned,
                approval=approval,
                approval_policy=policy,
                snapshots=snapshots,
            )


if __name__ == "__main__":
    unittest.main()
