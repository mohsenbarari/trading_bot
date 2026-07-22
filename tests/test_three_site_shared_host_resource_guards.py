from __future__ import annotations

from pathlib import Path
import unittest

import yaml


class ThreeSiteSharedHostResourceGuardTests(unittest.TestCase):
    def test_aggregate_slice_template_has_cpu_memory_and_task_hard_limits(self):
        unit = Path(
            "deploy/staging/trading-bot-three-site-staging.slice.example"
        ).read_text(encoding="utf-8")
        for required in (
            "CPUAccounting=true",
            "CPUQuota=",
            "MemoryAccounting=true",
            "MemoryHigh=",
            "MemoryMax=",
            "TasksAccounting=true",
            "TasksMax=",
        ):
            self.assertIn(required, unit)

    def test_every_service_has_staging_cgroup_and_explicit_resource_ceilings(self):
        compose = yaml.safe_load(
            Path("deploy/staging/docker-compose.three-site.yml").read_text(
                encoding="utf-8"
            )
        )
        services = compose["services"]
        self.assertGreater(len(services), 0)
        for name, service in services.items():
            with self.subTest(service=name):
                self.assertEqual(
                    service.get("cgroup_parent"),
                    "${STAGING_CGROUP_PARENT:?dedicated staging cgroup required}",
                )
                self.assertIn("cpus", service)
                self.assertIn("mem_limit", service)
                self.assertIn("pids_limit", service)

    def test_every_mutable_volume_is_bound_below_the_dedicated_data_mount(self):
        compose = yaml.safe_load(
            Path("deploy/staging/docker-compose.three-site.yml").read_text(
                encoding="utf-8"
            )
        )
        volumes = compose["volumes"]
        self.assertEqual(len(volumes), 14)
        devices = set()
        for name, volume in volumes.items():
            with self.subTest(volume=name):
                self.assertEqual(volume.get("driver"), "local")
                options = volume.get("driver_opts")
                self.assertEqual(options.get("type"), "none")
                self.assertEqual(options.get("o"), "bind")
                device = options.get("device")
                self.assertTrue(
                    device.startswith(
                        "${STAGING_DATA_ROOT:?dedicated staging data mount required}/"
                    )
                )
                self.assertNotIn("..", device)
                devices.add(device)
        self.assertEqual(len(devices), len(volumes))

    def test_host_boundary_provisioner_does_not_restart_production_services(self):
        script = Path(
            "scripts/provision_three_site_staging_host_boundary.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("systemctl enable --now \"$mount_unit\"", script)
        self.assertIn("systemctl enable --now \"$SLICE_NAME\"", script)
        for forbidden in (
            "systemctl restart docker",
            "docker compose up",
            "docker-compose up",
            "systemctl reboot",
        ):
            self.assertNotIn(forbidden, script)


if __name__ == "__main__":
    unittest.main()
