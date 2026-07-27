from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = (
    ROOT / "deploy" / "production" / "docker-compose.webapp-ir-dark-standby.yml"
)


class WebAppIrDarkStandbyComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = COMPOSE_PATH.read_text(encoding="utf-8")
        self.compose = yaml.safe_load(self.raw)

    def test_manifest_contains_only_one_database_service(self) -> None:
        self.assertEqual(set(self.compose), {"name", "services", "volumes"})
        self.assertEqual(set(self.compose["services"]), {"db"})
        service = self.compose["services"]["db"]
        for forbidden in (
            "build",
            "container_name",
            "depends_on",
            "expose",
            "networks",
            "ports",
            "profiles",
        ):
            self.assertNotIn(forbidden, service)
        self.assertEqual(service["network_mode"], "none")

    def test_image_is_preloaded_and_id_pinned(self) -> None:
        service = self.compose["services"]["db"]
        self.assertEqual(service["pull_policy"], "never")
        self.assertEqual(
            service["image"],
            "${DARK_STANDBY_DB_IMAGE_ID:?immutable target-local database image ID required}",
        )

    def test_project_and_volume_are_required_per_operation(self) -> None:
        operation_expression = (
            "${DARK_STANDBY_OPERATION_ID:?canonical operation id required}"
        )
        self.assertIn(operation_expression, self.compose["name"])
        volume = self.compose["volumes"]["postgres_data"]
        self.assertIn(operation_expression, volume["name"])
        self.assertNotIn(":-", self.compose["name"])
        self.assertNotIn(":-", volume["name"])


if __name__ == "__main__":
    unittest.main()
