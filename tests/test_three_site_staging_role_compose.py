from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import yaml

from scripts.render_three_site_staging_role_compose import (
    ROLE_PREFIXES,
    RoleComposeError,
    canonical_role_env_bytes,
    canonical_role_compose_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
)


ROOT = Path(__file__).resolve().parents[1]


class ThreeSiteStagingRoleComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = yaml.safe_load(
            (ROOT / "deploy/staging/docker-compose.three-site.yml").read_text(
                encoding="utf-8"
            )
        )

    def test_each_role_render_is_closed_and_deterministic(self):
        forbidden_references = {
            "bot-fi": ("WEBAPP_FI_CONTROL_DB_PASSWORD", "WITNESS_POSTGRES_PASSWORD"),
            "webapp-fi": ("BOT_TOKEN", "WEBAPP_IR_CONTROL_DB_PASSWORD"),
            "webapp-ir": ("BOT_TOKEN", "WEBAPP_FI_CONTROL_DB_PASSWORD"),
            "witness": ("BOT_TOKEN", "WEBAPP_JWT_SECRET_KEY"),
        }
        for role, prefix in ROLE_PREFIXES.items():
            with self.subTest(role=role):
                first = render_role_compose(self.payload, role=role)
                second = render_role_compose(self.payload, role=role)
                self.assertEqual(
                    canonical_role_compose_bytes(first),
                    canonical_role_compose_bytes(second),
                )
                self.assertTrue(first["services"])
                self.assertTrue(
                    all(name.startswith(prefix) for name in first["services"])
                )
                material = json.dumps(first, sort_keys=True)
                for forbidden in forbidden_references[role]:
                    self.assertNotIn(forbidden, material)
                for service in first["services"].values():
                    self.assertNotIn("profiles", service)
                    depends_on = service.get("depends_on", {})
                    self.assertFalse(set(depends_on) - set(first["services"]))

    def test_role_environment_contains_exactly_referenced_variables(self):
        values = parse_env_values(
            (ROOT / "deploy/staging/env.three-site.staging.example").read_text(
                encoding="utf-8"
            )
        )
        for role in ROLE_PREFIXES:
            with self.subTest(role=role):
                rendered = render_role_compose(self.payload, role=role)
                required = referenced_environment_names(rendered)
                role_env = canonical_role_env_bytes(
                    values,
                    required_names=required,
                ).decode()
                observed = parse_env_values(role_env)
                self.assertEqual(set(observed), set(required))
                if role == "bot-fi":
                    self.assertNotIn("WEBAPP_JWT_SECRET_KEY", observed)
                    self.assertNotIn("WITNESS_POSTGRES_PASSWORD", observed)
                elif role.startswith("webapp"):
                    self.assertNotIn("BOT_TOKEN", observed)
                    self.assertNotIn("WITNESS_POSTGRES_PASSWORD", observed)
                else:
                    self.assertNotIn("BOT_TOKEN", observed)
                    self.assertNotIn("WEBAPP_JWT_SECRET_KEY", observed)

    def test_cross_role_dependency_is_rejected(self):
        payload = yaml.safe_load(
            (ROOT / "deploy/staging/docker-compose.three-site.yml").read_text(
                encoding="utf-8"
            )
        )
        payload["services"]["bot_fi_api"]["depends_on"] = {"webapp_fi_db": {}}
        with self.assertRaisesRegex(RoleComposeError, "cross-role"):
            render_role_compose(payload, role="bot-fi")


if __name__ == "__main__":
    unittest.main()
