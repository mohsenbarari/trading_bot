from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml

from core.secure_file_io import SecureFileError
from scripts import verify_three_site_staging_role_bundle as role_verifier
from scripts.render_three_site_staging_role_compose import (
    canonical_role_compose_bytes,
    canonical_role_env_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
)
from scripts.verify_three_site_staging_role_bundle import (
    RoleBundleError,
    _secure_json_document,
    _verify_bundle_source,
    _verify_file,
    _verify_relay_material_directory,
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
                    approval_policy=self.policy,
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
                approval_policy=self.policy,
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
                approval_policy=self.policy,
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
                approval_policy=self.policy,
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
                approval_policy=self.policy,
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
                approval_policy=self.policy,
                verify_files=False,
            )

    def test_cli_bundle_sources_require_exact_modes_and_no_links(self):
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

    def test_cli_security_documents_use_owner_only_duplicate_safe_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "inventory.json"
            document.write_text('{"schema":"one"}\n', encoding="utf-8")
            document.chmod(0o600)
            self.assertEqual(
                _secure_json_document(document, label="inventory"),
                {"schema": "one"},
            )
            document.write_text('{"schema":"one","schema":"two"}\n', encoding="utf-8")
            with self.assertRaisesRegex(RoleBundleError, "strict JSON"):
                _secure_json_document(document, label="inventory")
            document.write_text('{"schema":"one"}\n', encoding="utf-8")
            document.chmod(0o640)
            with self.assertRaises(SecureFileError):
                _secure_json_document(document, label="inventory")

    def test_role_files_require_root_controlled_paths_and_stable_single_links(self):
        if os.geteuid() != 0:
            self.skipTest("root-owned role-file validation requires uid 0")
        secure_test_root = Path("/root/trading-bot/trading_bot/tmp")
        if not secure_test_root.is_dir():
            self.skipTest("no root-controlled writable test directory is available")
        with tempfile.TemporaryDirectory(
            prefix=".three-site-role-file-test-", dir=secure_test_root
        ) as directory:
            root = Path(directory)
            private = root / "private.key"
            private.write_text("secret\n", encoding="utf-8")
            private.chmod(0o600)
            _verify_file(str(private), private=True)

            hardlink = root / "private-hardlink.key"
            hardlink.hardlink_to(private)
            with self.assertRaisesRegex(RoleBundleError, "single-link"):
                _verify_file(str(private), private=True)
            hardlink.unlink()

            with self.assertRaisesRegex(RoleBundleError, "absolute"):
                _verify_file("relative/private.key", private=True)

            root.chmod(0o777)
            with self.assertRaisesRegex(RoleBundleError, "root-controlled"):
                _verify_file(str(private), private=True)
            root.chmod(0o700)

    def test_enabled_relay_directory_requires_exact_root_only_active_files(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            active = Path(directory) / "active"
            active.mkdir(mode=0o700)
            for name in ("session.json", "policy.json"):
                path = active / name
                path.write_text("{}\n", encoding="utf-8")
                path.chmod(0o600)
            _verify_relay_material_directory(str(active))
            (active / "session.json").chmod(0o640)
            with self.assertRaisesRegex(RoleBundleError, "mode-0600"):
                _verify_relay_material_directory(str(active))
            (active / "session.json").chmod(0o600)
            extra = active / "unexpected"
            extra.write_text("x", encoding="utf-8")
            extra.chmod(0o600)
            with self.assertRaisesRegex(RoleBundleError, "exactly"):
                _verify_relay_material_directory(str(active))
            real = Path(directory) / "real"
            linked_active = real / "active"
            linked_active.mkdir(mode=0o700, parents=True)
            for name in ("session.json", "policy.json"):
                path = linked_active / name
                path.write_text("{}\n", encoding="utf-8")
                path.chmod(0o600)
            alias = Path(directory) / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(
                RoleBundleError,
                "unavailable|root-controlled|root-owned",
            ):
                _verify_relay_material_directory(str(alias / "active"))

    def test_relay_material_rejects_replaceable_ancestor_and_linked_child(self):
        if os.geteuid() != 0:
            self.skipTest("root-controlled relay validation requires uid 0")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsafe = root / "unsafe"
            active = unsafe / "active"
            active.mkdir(mode=0o700, parents=True)
            for name in ("session.json", "policy.json"):
                child = active / name
                child.write_text("{}\n", encoding="utf-8")
                child.chmod(0o600)
            unsafe.chmod(0o777)
            with self.assertRaisesRegex(RoleBundleError, "root-controlled"):
                _verify_relay_material_directory(str(active))
            unsafe.chmod(0o700)
            _verify_relay_material_directory(str(active))

            session = active / "session.json"
            original = unsafe / "session-original.json"
            session.rename(original)
            session.symlink_to(original)
            with self.assertRaisesRegex(RoleBundleError, "unavailable"):
                _verify_relay_material_directory(str(active))
            session.unlink()
            session.hardlink_to(original)
            with self.assertRaisesRegex(RoleBundleError, "single-link"):
                _verify_relay_material_directory(str(active))

    def test_enabled_relay_file_attestation_reaches_exact_directory_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = (
                root
                / self.inventory["campaign_id"]
                / self.inventory["deployment_id"]
                / "material-revisions"
                / "relay-attestation-r001"
                / "active"
            )
            active.mkdir(mode=0o700, parents=True)
            active.chmod(0o700)
            for name in ("session.json", "policy.json"):
                path = active / name
                path.write_text("{}\n", encoding="utf-8")
                path.chmod(0o600)
            values = dict(self.values)
            values.update(
                STAGING_HUMAN_APPROVAL_RELAY_ENABLED="true",
                STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR=str(active),
                STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID=(
                    "relay-attestation-key"
                ),
                STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET=(
                    "relay-attestation-secret-that-is-at-least-32-bytes"
                ),
            )
            role_payload = render_role_compose(self.canonical, role="witness")
            compose_bytes = canonical_role_compose_bytes(role_payload)
            env_bytes = canonical_role_env_bytes(
                values,
                required_names=referenced_environment_names(role_payload),
            )
            with mock.patch.object(role_verifier, "_verify_file"):
                result = verify_role_bundle(
                    role="witness",
                    canonical_compose=self.canonical,
                    role_compose_bytes=compose_bytes,
                    env_bytes=env_bytes,
                    inventory=self.inventory,
                    approval=self.approval,
                    approval_policy=self.policy,
                    verify_files=True,
                )
            self.assertTrue(result["file_attestation"])

if __name__ == "__main__":
    unittest.main()
