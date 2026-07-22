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


if __name__ == "__main__":
    unittest.main()
