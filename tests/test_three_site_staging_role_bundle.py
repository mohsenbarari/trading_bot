from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

import yaml

from scripts.render_three_site_staging_role_compose import (
    canonical_role_compose_bytes,
    canonical_role_env_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
)
from scripts.verify_three_site_staging_role_bundle import (
    RoleBundleError,
    _verify_bundle_source,
    verify_role_bundle,
)
from tests.test_three_site_staging_signed_inventory import (
    _inventory,
    _signed_documents,
)


ROOT = Path(__file__).resolve().parents[1]


class ThreeSiteStagingRoleBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical = yaml.safe_load(
            (ROOT / "deploy/staging/docker-compose.three-site.yml").read_text(
                encoding="utf-8"
            )
        )
        raw_values = parse_env_values(
            (ROOT / "deploy/staging/env.three-site.staging.example").read_text(
                encoding="utf-8"
            )
        )
        cls.values = {
            name: value.replace("CHANGE_ME", f"test_{name.lower()}")
            for name, value in raw_values.items()
        }
        cls.inventory = _inventory()
        cls.values["DR_BLOB_OBJECT_BUCKET"] = cls.inventory["object_storage"]["bucket"]
        cls.values["DR_BLOB_OBJECT_PREFIX"] = (
            cls.inventory["object_storage"]["prefix"] + "blobs/sha256"
        )
        bind_names = {
            "bot_fi": "BOT_FI_DR_BIND_ADDRESS",
            "webapp_fi": "WEBAPP_FI_DR_BIND_ADDRESS",
            "webapp_ir": "WEBAPP_IR_DR_BIND_ADDRESS",
            "witness": "WITNESS_DR_BIND_ADDRESS",
        }
        for role in cls.inventory["roles"]:
            cls.values[bind_names[role["role"]]] = role["host_ip"]
        by_role = {role["role"]: role for role in cls.inventory["roles"]}
        cls.values.update(
            BOT_FI_PEER_WEBAPP_FI_IP=by_role["webapp_fi"]["host_ip"],
            WEBAPP_FI_PEER_BOT_FI_IP=by_role["bot_fi"]["host_ip"],
            WEBAPP_FI_PEER_WEBAPP_IR_IP=by_role["webapp_ir"]["host_ip"],
            WEBAPP_FI_WITNESS_IP=by_role["witness"]["host_ip"],
            WEBAPP_IR_PEER_WEBAPP_FI_IP=by_role["webapp_fi"]["host_ip"],
            WEBAPP_IR_WITNESS_IP=by_role["witness"]["host_ip"],
        )
        cls.policy, cls.approval = _signed_documents(
            cls.inventory, datetime.now(timezone.utc)
        )

    def _bundle(self, role: str):
        role_payload = render_role_compose(self.canonical, role=role)
        compose_bytes = canonical_role_compose_bytes(role_payload)
        env_bytes = canonical_role_env_bytes(
            self.values,
            required_names=referenced_environment_names(role_payload),
        )
        return compose_bytes, env_bytes

    def test_all_four_role_bundles_match_signed_inventory_and_closed_topology(self):
        for role in ("bot-fi", "webapp-fi", "webapp-ir", "witness"):
            with self.subTest(role=role):
                compose_bytes, env_bytes = self._bundle(role)
                result = verify_role_bundle(
                    role=role,
                    canonical_compose=self.canonical,
                    role_compose_bytes=compose_bytes,
                    env_bytes=env_bytes,
                    inventory=self.inventory,
                    approval=self.approval,
                    signer_policy=self.policy,
                    verify_files=False,
                )
                self.assertEqual(result["status"], "verified")
                self.assertEqual(result["release_sha"], "a" * 40)

    def test_tampered_compose_or_queue_cutover_env_is_rejected(self):
        compose_bytes, env_bytes = self._bundle("bot-fi")
        with self.assertRaisesRegex(RoleBundleError, "canonical renderer"):
            verify_role_bundle(
                role="bot-fi",
                canonical_compose=self.canonical,
                role_compose_bytes=compose_bytes + b"\n# tampered\n",
                env_bytes=env_bytes,
                inventory=self.inventory,
                approval=self.approval,
                signer_policy=self.policy,
                verify_files=False,
            )

        unsafe_env = env_bytes.replace(
            b"TELEGRAM_DELIVERY_EXECUTION_OWNER=legacy",
            b"TELEGRAM_DELIVERY_EXECUTION_OWNER=queue-v1",
        )
        with self.assertRaisesRegex(RoleBundleError, "legacy Telegram"):
            verify_role_bundle(
                role="bot-fi",
                canonical_compose=self.canonical,
                role_compose_bytes=compose_bytes,
                env_bytes=unsafe_env,
                inventory=self.inventory,
                approval=self.approval,
                signer_policy=self.policy,
                verify_files=False,
            )

        unsafe_expected_owner = env_bytes.replace(
            b"TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER=legacy",
            b"TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER=queue-v1",
        )
        with self.assertRaisesRegex(RoleBundleError, "legacy Telegram"):
            verify_role_bundle(
                role="bot-fi",
                canonical_compose=self.canonical,
                role_compose_bytes=compose_bytes,
                env_bytes=unsafe_expected_owner,
                inventory=self.inventory,
                approval=self.approval,
                signer_policy=self.policy,
                verify_files=False,
            )

    def test_bind_address_and_peer_port_are_bound_to_signed_topology(self):
        compose_bytes, env_bytes = self._bundle("webapp-fi")
        wrong_bind = env_bytes.replace(
            b"WEBAPP_FI_DR_BIND_ADDRESS=10.30.0.2",
            b"WEBAPP_FI_DR_BIND_ADDRESS=10.30.0.99",
        )
        with self.assertRaisesRegex(RoleBundleError, "bind address"):
            verify_role_bundle(
                role="webapp-fi",
                canonical_compose=self.canonical,
                role_compose_bytes=compose_bytes,
                env_bytes=wrong_bind,
                inventory=self.inventory,
                approval=self.approval,
                signer_policy=self.policy,
                verify_files=False,
            )

        wrong_peer = env_bytes.replace(b":8443\"}", b"\"}")
        with self.assertRaisesRegex(RoleBundleError, "peer URL"):
            verify_role_bundle(
                role="webapp-fi",
                canonical_compose=self.canonical,
                role_compose_bytes=compose_bytes,
                env_bytes=wrong_peer,
                inventory=self.inventory,
                approval=self.approval,
                signer_policy=self.policy,
                verify_files=False,
            )

    def test_cli_bundle_sources_require_exact_modes_and_no_links(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / "role.env"
            env.write_text("A=b\n")
            env.chmod(0o640)
            with self.assertRaisesRegex(RoleBundleError, "mode-0600"):
                _verify_bundle_source(env, expected_mode=0o600)
            env.chmod(0o600)
            self.assertEqual(_verify_bundle_source(env, expected_mode=0o600), b"A=b\n")
            link = root / "role-link.env"
            link.symlink_to(env)
            with self.assertRaises(RoleBundleError):
                _verify_bundle_source(link, expected_mode=0o600)

if __name__ == "__main__":
    unittest.main()
