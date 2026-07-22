from __future__ import annotations

import unittest

from scripts.verify_three_site_staging_host_identity import (
    HostIdentityError,
    ROLE_VOLUME_FIELDS,
    verify_host_snapshot,
)
from tests.test_three_site_staging_signed_inventory import _inventory


class ThreeSiteStagingHostIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = _inventory()
        self.role = "webapp-fi"
        self.role_inventory = next(
            item for item in self.inventory["roles"] if item["role"] == "webapp_fi"
        )

    def _role_inventory(self, stage: str):
        role_inventory = dict(self.role_inventory)
        if stage == "fresh-preflight":
            role_inventory["postgres_system_id"] = None
        return role_inventory

    def _snapshot(self, stage: str):
        role_inventory = self._role_inventory(stage)
        volumes = {
            field: (
                None if stage == "fresh-preflight" else role_inventory[field]
            )
            for field in ROLE_VOLUME_FIELDS[self.role]
        }
        return {
            "schema": "three-site-staging-host-snapshot-v2",
            "role": self.role,
            "stage": stage,
            "release_sha": self.inventory["release_sha"],
            "worktree_clean": True,
            "machine_id": self.role_inventory["machine_id"],
            "docker_daemon_id": self.role_inventory["docker_daemon_id"],
            "ipv4_addresses": ["127.0.0.1", self.role_inventory["host_ip"]],
            "timezone": "UTC",
            "ntp_synchronized": True,
            "clock_measurement_tool": "chronyc",
            "volumes": volumes,
            "storage": {
                "root": role_inventory["storage_root"],
                "source": "/dev/disk/by-uuid/" + role_inventory["storage_mount_uuid"],
                "filesystem": "ext4",
                "mount_uuid": role_inventory["storage_mount_uuid"],
                "total_bytes": 60 * 1024**3,
                "available_bytes": 55 * 1024**3,
            },
            "resource_boundary": {
                "slice": "trading-bot-three-site-staging.slice",
                **role_inventory["resource_limits"],
            },
            "postgres_system_id": (
                None
                if stage == "fresh-preflight"
                else role_inventory["postgres_system_id"]
            ),
        }

    def test_fresh_and_provisioned_attestations_are_distinct_and_valid(self):
        for stage in ("fresh-preflight", "provisioned"):
            with self.subTest(stage=stage):
                result = verify_host_snapshot(
                    self._snapshot(stage),
                    role=self.role,
                    role_inventory=self._role_inventory(stage),
                    release_sha=self.inventory["release_sha"],
                    stage=stage,
                )
                self.assertEqual(result["status"], "verified")
                self.assertEqual(len(result["host_snapshot_sha256"]), 64)

    def test_wrong_host_dirty_tree_or_preexisting_volume_fails_closed(self):
        snapshot = self._snapshot("fresh-preflight")
        snapshot["ipv4_addresses"] = ["10.30.0.99"]
        with self.assertRaises(HostIdentityError):
            verify_host_snapshot(
                snapshot,
                role=self.role,
                role_inventory=self._role_inventory("fresh-preflight"),
                release_sha=self.inventory["release_sha"],
                stage="fresh-preflight",
            )

        snapshot = self._snapshot("fresh-preflight")
        snapshot["worktree_clean"] = False
        with self.assertRaises(HostIdentityError):
            verify_host_snapshot(
                snapshot,
                role=self.role,
                role_inventory=self._role_inventory("fresh-preflight"),
                release_sha=self.inventory["release_sha"],
                stage="fresh-preflight",
            )

        snapshot = self._snapshot("fresh-preflight")
        snapshot["volumes"]["postgres_volume_id"] = self.role_inventory[
            "postgres_volume_id"
        ]
        with self.assertRaisesRegex(HostIdentityError, "already has"):
            verify_host_snapshot(
                snapshot,
                role=self.role,
                role_inventory=self._role_inventory("fresh-preflight"),
                release_sha=self.inventory["release_sha"],
                stage="fresh-preflight",
            )

    def test_provisioned_postgres_identity_must_match(self):
        snapshot = self._snapshot("provisioned")
        snapshot["postgres_system_id"] = "wrong-system"
        with self.assertRaisesRegex(HostIdentityError, "PostgreSQL system"):
            verify_host_snapshot(
                snapshot,
                role=self.role,
                role_inventory=self.role_inventory,
                release_sha=self.inventory["release_sha"],
                stage="provisioned",
            )

    def test_wrong_or_undersized_storage_mount_fails_closed(self):
        snapshot = self._snapshot("fresh-preflight")
        snapshot["storage"]["mount_uuid"] = "00000000-0000-4000-8000-999999999999"
        with self.assertRaisesRegex(HostIdentityError, "storage"):
            verify_host_snapshot(
                snapshot,
                role=self.role,
                role_inventory=self._role_inventory("fresh-preflight"),
                release_sha=self.inventory["release_sha"],
                stage="fresh-preflight",
            )

    def test_resource_boundary_must_equal_signed_limits(self):
        snapshot = self._snapshot("fresh-preflight")
        snapshot["resource_boundary"]["memory_max_bytes"] += 1
        with self.assertRaisesRegex(HostIdentityError, "resource boundary"):
            verify_host_snapshot(
                snapshot,
                role=self.role,
                role_inventory=self._role_inventory("fresh-preflight"),
                release_sha=self.inventory["release_sha"],
                stage="fresh-preflight",
            )

        snapshot = self._snapshot("fresh-preflight")
        snapshot["storage"]["total_bytes"] = 8 * 1024**3
        with self.assertRaisesRegex(HostIdentityError, "storage"):
            verify_host_snapshot(
                snapshot,
                role=self.role,
                role_inventory=self._role_inventory("fresh-preflight"),
                release_sha=self.inventory["release_sha"],
                stage="fresh-preflight",
            )

    def test_sync_role_requires_an_offset_capable_clock_tool(self):
        snapshot = self._snapshot("fresh-preflight")
        snapshot["clock_measurement_tool"] = None
        with self.assertRaisesRegex(HostIdentityError, "time policy"):
            verify_host_snapshot(
                snapshot,
                role=self.role,
                role_inventory=self._role_inventory("fresh-preflight"),
                release_sha=self.inventory["release_sha"],
                stage="fresh-preflight",
            )


if __name__ == "__main__":
    unittest.main()
