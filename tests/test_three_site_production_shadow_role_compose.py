from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

import yaml

from scripts.render_three_site_production_shadow_role_compose import (
    ROLE_PREFIXES,
    ProductionShadowRoleError,
    _atomic_write,
    canonical_role_compose_bytes,
    canonical_role_env_bytes,
    main,
    parse_env_values,
    referenced_environment_names,
    required_environment_names,
    render_role_compose,
)
from scripts.verify_three_site_production_shadow_compose import _required_values


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = (
    REPO_ROOT / "deploy/production/docker-compose.three-site-shadow.yml"
)


class ThreeSiteProductionShadowRoleComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose_bytes = COMPOSE_PATH.read_bytes()
        cls.source_text = cls.compose_bytes.decode("utf-8")
        cls.payload = yaml.safe_load(cls.source_text)

    def role_values(self) -> dict[str, str]:
        values = {
            name: f"value-{name.lower().replace('_', '-')}"
            for name in _required_values(self.source_text)
        }
        values.update(
            {
                "PRODUCTION_SHADOW_OPERATION_ID": (
                    "123e4567-e89b-42d3-a456-426614174000"
                ),
                "PRODUCTION_SHADOW_PROJECT": (
                    "tb3p-123e4567e89b42d3a456426614174000"
                ),
                "PRODUCTION_SHADOW_CGROUP_PARENT": (
                    "tb3p-123e4567e89b42d3a456426614174000"
                ),
                "PRODUCTION_SHADOW_PROJECT_ROOT": "/srv/operation",
                "PRODUCTION_SHADOW_RELEASE_ROOT": "/srv/operation/release",
                "PRODUCTION_SHADOW_DATA_ROOT": "/srv/operation/data",
                "PRODUCTION_SHADOW_SECRET_ROOT": "/srv/operation/secrets",
                "PRODUCTION_SHADOW_APP_IMAGE_ID": f"sha256:{'1' * 64}",
                "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": f"sha256:{'2' * 64}",
                "PRODUCTION_SHADOW_REDIS_IMAGE_ID": f"sha256:{'3' * 64}",
                "PRODUCTION_SHADOW_NGINX_IMAGE_ID": f"sha256:{'4' * 64}",
            }
        )
        port = 19000
        for name in sorted(values):
            if name.endswith("_PORT"):
                values[name] = str(port)
                port += 1
            elif name.endswith("_BIND_ADDRESS") or (
                "_PEER_" in name and name.endswith("_IP")
            ):
                values[name] = "203.0.113.10"
        return values

    def test_full_role_renders_are_closed_deterministic_and_secret_scoped(self):
        forbidden = {
            "bot-fi": (
                "WEBAPP_FI_POSTGRES_PASSWORD",
                "WEBAPP_IR_POSTGRES_PASSWORD",
                "WEBAPP_PROVIDER_CONFIG_SHA256",
                "PRODUCTION_SHADOW_WITNESS_URL",
            ),
            "webapp-fi": (
                "${BOT_TOKEN",
                "BOT_FI_POSTGRES_PASSWORD",
                "WEBAPP_IR_POSTGRES_PASSWORD",
            ),
            "webapp-ir": (
                "${BOT_TOKEN",
                "BOT_FI_POSTGRES_PASSWORD",
                "WEBAPP_FI_POSTGRES_PASSWORD",
            ),
        }
        for role, prefix in ROLE_PREFIXES.items():
            with self.subTest(role=role):
                first = render_role_compose(self.payload, role=role)
                second = render_role_compose(self.payload, role=role)
                self.assertEqual(
                    canonical_role_compose_bytes(first),
                    canonical_role_compose_bytes(second),
                )
                self.assertEqual(
                    first["name"],
                    (
                        "${PRODUCTION_SHADOW_PROJECT:"
                        "?operation-bound project is required}"
                        f"-{role}"
                    ),
                )
                self.assertTrue(first["services"])
                self.assertTrue(
                    all(name.startswith(prefix) for name in first["services"])
                )
                material = json.dumps(first, sort_keys=True)
                for marker in forbidden[role]:
                    self.assertNotIn(marker, material)
                for service in first["services"].values():
                    dependencies = set(service.get("depends_on", {}))
                    self.assertFalse(
                        dependencies - set(first["services"])
                    )
                    self.assertTrue(
                        all(
                            profile.startswith(f"{role}-")
                            for profile in service["profiles"]
                        )
                    )

    def test_prepare_scope_has_exact_closed_service_sets(self):
        expected = {
            "webapp-fi": {
                "webapp_fi_db",
                "webapp_fi_restore_tool",
                "webapp_fi_db_roles",
                "webapp_fi_migration",
                "webapp_fi_db_roles_post_migration",
                "webapp_fi_db_fencing",
            },
            "webapp-ir": {
                "webapp_ir_db",
                "webapp_ir_restore_tool",
                "webapp_ir_db_roles",
                "webapp_ir_migration",
                "webapp_ir_db_roles_post_migration",
                "webapp_ir_db_fencing",
                "webapp_ir_writer_fence",
            },
        }
        for role, expected_services in expected.items():
            with self.subTest(role=role):
                rendered = render_role_compose(
                    self.payload,
                    role=role,
                    scope="prepare",
                )
                self.assertEqual(set(rendered["services"]), expected_services)
                self.assertEqual(set(rendered["networks"]), {role.replace("-", "_")})
                self.assertNotIn("PRODUCTION_SHADOW_WITNESS_URL", json.dumps(rendered))
                self.assertNotIn("ports", json.dumps(rendered["services"]))
                for service in rendered["services"].values():
                    self.assertFalse(
                        set(service.get("depends_on", {}))
                        - expected_services
                    )

    def test_role_environment_contains_only_role_references(self):
        values = self.role_values()
        for role in ROLE_PREFIXES:
            with self.subTest(role=role):
                rendered = render_role_compose(self.payload, role=role)
                required = required_environment_names(rendered)
                role_env = canonical_role_env_bytes(
                    values,
                    required_names=required,
                ).decode("utf-8")
                observed = parse_env_values(role_env)
                self.assertEqual(set(observed), set(required))
                if role == "bot-fi":
                    self.assertNotIn("WEBAPP_JWT_SECRET_KEY", observed)
                    self.assertNotIn("WEBAPP_PROVIDER_CONFIG_SHA256", observed)
                    self.assertNotIn("PRODUCTION_SHADOW_WITNESS_URL", observed)
                elif role == "webapp-fi":
                    self.assertNotIn("BOT_FI_POSTGRES_PASSWORD", observed)
                    self.assertNotIn("WEBAPP_IR_POSTGRES_PASSWORD", observed)
                else:
                    self.assertNotIn("BOT_FI_POSTGRES_PASSWORD", observed)
                    self.assertNotIn("WEBAPP_FI_POSTGRES_PASSWORD", observed)

    def test_dotenv_ambiguity_is_rejected_instead_of_reinterpreted(self):
        for value in (
            "has$dollar",
            "'quoted'",
            '"quoted"',
            "has#comment",
            r"has\backslash",
            "has`backtick",
            "has space",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ProductionShadowRoleError,
                    "invalid or duplicate",
                ):
                    parse_env_values(f"SECRET={value}\n")
        canonical_json = '{"site":"webapp_ir","secret":"abc123"}'
        self.assertEqual(
            parse_env_values(f"PAIRWISE={canonical_json}\n")["PAIRWISE"],
            canonical_json,
        )

    def test_create_only_output_is_idempotent_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            parent.chmod(0o700)
            output = parent / "role.yml"
            _atomic_write(output, b"first\n", mode=0o600)
            _atomic_write(output, b"first\n", mode=0o600)
            self.assertEqual(output.read_bytes(), b"first\n")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaisesRegex(
                ProductionShadowRoleError,
                "overwrite different",
            ):
                _atomic_write(output, b"second\n", mode=0o600)
            self.assertEqual(output.read_bytes(), b"first\n")

    def test_main_rejects_wrong_canonical_hash_without_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            parent.chmod(0o700)
            env_source = parent / "canonical.env"
            env_source.write_text(
                "\n".join(
                    f"{name}={value}"
                    for name, value in sorted(self.role_values().items())
                )
                + "\n",
                encoding="utf-8",
            )
            env_source.chmod(0o600)
            compose_output = parent / "role.yml"
            env_output = parent / "role.env"
            status = main(
                [
                    "--role",
                    "webapp-ir",
                    "--compose",
                    str(COMPOSE_PATH),
                    "--env-source",
                    str(env_source),
                    "--compose-output",
                    str(compose_output),
                    "--env-output",
                    str(env_output),
                    "--expected-compose-sha256",
                    "0" * 64,
                ]
            )
            self.assertEqual(status, 1)
            self.assertFalse(compose_output.exists())
            self.assertFalse(env_output.exists())

    def test_rendered_roles_pass_compose_config_with_only_role_env(self):
        values = self.role_values()
        for role in ROLE_PREFIXES:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as tmpdir:
                parent = Path(tmpdir)
                parent.chmod(0o700)
                rendered = render_role_compose(self.payload, role=role)
                compose_output = parent / "role.yml"
                env_output = parent / "role.env"
                _atomic_write(
                    compose_output,
                    canonical_role_compose_bytes(rendered),
                    mode=0o600,
                )
                _atomic_write(
                    env_output,
                    canonical_role_env_bytes(
                        values,
                        required_names=required_environment_names(rendered),
                    ),
                    mode=0o600,
                )
                completed = subprocess.run(
                    [
                        "/usr/bin/docker",
                        "compose",
                        "--env-file",
                        str(env_output),
                        "--file",
                        str(compose_output),
                        "--profile",
                        "*",
                        "config",
                        "--no-env-resolution",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=30,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "HOME": "/root",
                        "LANG": "C.UTF-8",
                        "LC_ALL": "C.UTF-8",
                    },
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
