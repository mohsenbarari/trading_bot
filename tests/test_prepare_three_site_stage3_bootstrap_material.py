from pathlib import Path
import unittest
from unittest import mock

from scripts.prepare_three_site_stage3_bootstrap_material import (
    BootstrapMaterialError,
    _read_s3_credentials,
    build_environment,
)


class PrepareStage3BootstrapMaterialTests(unittest.TestCase):
    def inventory(self):
        return {
            "release_sha": "a" * 40,
            "compose_project_namespace": "three-site-campaign-test",
            "canonical_domain": "staging.example.test",
            "object_storage": {"bucket": "stage-bucket", "prefix": "staging/campaign/"},
            "roles": [
                {"role": "bot_fi", "host_ip": "192.0.2.11"},
                {"role": "webapp_fi", "host_ip": "192.0.2.12"},
                {"role": "webapp_ir", "host_ip": "192.0.2.13"},
                {"role": "witness", "host_ip": "192.0.2.14"},
            ],
        }

    @mock.patch(
        "scripts.prepare_three_site_stage3_bootstrap_material.secrets.token_hex",
        side_effect=[f"{number:064x}" for number in range(1, 80)],
    )
    def test_environment_is_fresh_and_bound_to_inventory(self, _token_hex):
        template = Path("deploy/staging/env.three-site.staging.example").read_text()
        values = build_environment(
            template,
            inventory=self.inventory(),
            source_root=Path("/srv/stage/source"),
            remote_material_root=Path("/etc/stage/material"),
            witness_public_key="A" * 44,
        )
        self.assertFalse(any("change_me" in value.lower() for value in values.values()))
        self.assertEqual(values["WEBAPP_IR_DR_BIND_ADDRESS"], "192.0.2.13")
        self.assertEqual(values["WEBAPP_FI_WITNESS_IP"], "192.0.2.14")
        self.assertEqual(values["STAGING_STORAGE_NAMESPACE"], "three-site-campaign-test")
        self.assertEqual(values["TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED"], "false")
        passwords = [
            value
            for name, value in values.items()
            if name.endswith("_DB_PASSWORD") or name.endswith("_POSTGRES_PASSWORD")
        ]
        self.assertEqual(len(passwords), len(set(passwords)))

    def test_s3_parser_accepts_owner_env_without_exposing_values(self):
        with mock.patch(
            "scripts.prepare_three_site_stage3_bootstrap_material.read_secure_text",
            return_value=(
                "ARVAN_S3_ACCESS_KEY=access-key-value\n"
                f"ARVAN_S3_SECRET_KEY={'s' * 40}\n"
            ),
        ):
            parsed = _read_s3_credentials(Path("/owner/s3.env"))
        self.assertEqual(set(parsed), {"access_key", "secret_key"})

    def test_s3_parser_rejects_short_secret(self):
        with mock.patch(
            "scripts.prepare_three_site_stage3_bootstrap_material.read_secure_text",
            return_value='{"access_key":"long-enough","secret_key":"short"}',
        ):
            with self.assertRaises(BootstrapMaterialError):
                _read_s3_credentials(Path("/owner/s3.json"))


if __name__ == "__main__":
    unittest.main()
