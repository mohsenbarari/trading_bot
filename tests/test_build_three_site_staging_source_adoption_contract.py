from __future__ import annotations

import hashlib
import json
import base64
from pathlib import Path
import sys
import tempfile
import types
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import build_three_site_staging_source_adoption_contract as builder


RELEASE_SHA = "a" * 40
HISTORICAL_RELEASE_SHA = "b" * 40
SOURCE_RELEASE_SHA = "c" * 40
APP_IMAGE = "sha256:" + "1" * 64
DB_IMAGE = "sha256:" + "2" * 64
REDIS_IMAGE = "sha256:" + "3" * 64
SCRATCH_IMAGE = "sha256:" + "4" * 64
CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"


class SourceAdoptionContractBuilderTests(unittest.TestCase):
    def test_policy_must_bound_direct_action_to_one_hour_staging_only(self):
        for ttl in (
            builder.adoption.MIN_APPROVAL_REMAINING_SECONDS - 1,
            3601,
        ):
            with self.subTest(ttl=ttl):
                policy = {
                    "issuer": {
                        "public_key": base64.b64encode(b"k" * 32).decode()
                    },
                    "actions": [
                        {
                            "action": "approve_source_adoption_backup",
                            "environments": ["staging"],
                            "max_ttl_seconds": ttl,
                        }
                    ],
                }
                with self.assertRaisesRegex(
                    builder.ContractBuildError, "bounded direct"
                ):
                    builder._policy_reference(policy, b"policy")

    def test_docker_observation_is_read_only_and_exact(self):
        container_id = "d" * 64
        calls: list[list[str]] = []

        def run(arguments, *, timeout=60):  # noqa: ARG001
            calls.append(arguments)
            if arguments[1:3] == ["container", "ls"]:
                return container_id
            if arguments[1] == "inspect":
                template = arguments[-2]
                if template == "{{.Image}}":
                    return APP_IMAGE
                if "compose.project" in template:
                    return "trading_bot_staging"
                if "compose.service" in template:
                    return "foreign_app"
                if template == "{{json .Mounts}}":
                    return json.dumps(
                        [
                            {
                                "Type": "volume",
                                "Name": "legacy_uploads",
                                "Destination": "/app/uploads",
                                "RW": True,
                            },
                            {
                                "Type": "volume",
                                "Name": "legacy_audit",
                                "Destination": "/app/audit_trail",
                                "RW": True,
                            },
                        ]
                    )
            if arguments[1:3] == ["volume", "inspect"]:
                name = arguments[-1]
                template = arguments[-2]
                if "compose.volume" in template:
                    return {
                        "legacy_uploads": "staging_uploads",
                        "legacy_audit": "staging_audit",
                    }[name]
                return "trading_bot_staging"
            raise AssertionError(arguments)

        with patch.object(builder, "_run", side_effect=run):
            result = builder._observe_source_volumes(
                project="trading_bot_staging",
                app_service="foreign_app",
                app_image_id=APP_IMAGE,
            )
        self.assertEqual(result["/app/uploads"]["name"], "legacy_uploads")
        rendered = [" ".join(command) for command in calls]
        self.assertFalse(
            any(
                token in command
                for command in rendered
                for token in (
                    " create ",
                    " rm ",
                    " start ",
                    " stop ",
                    " run ",
                    " exec ",
                )
            )
        )

    def test_snapshot_writer_is_mode_0600_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / "snapshot.json"
            self.assertTrue(builder._write_new(path, b"one"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                builder._write_new(path, b"two")
            self.assertEqual(path.read_bytes(), b"one")
            self.assertFalse(
                builder._write_new(path, b"one", reuse_exact=True)
            )
            with self.assertRaisesRegex(
                builder.ContractBuildError, "bytes differ"
            ):
                builder._write_new(path, b"two", reuse_exact=True)
            self.assertEqual(path.read_bytes(), b"one")

    def test_snapshot_writer_never_exposes_partial_final_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / "snapshot.json"
            with patch.object(
                builder.os, "write", side_effect=OSError("injected crash")
            ):
                with self.assertRaisesRegex(OSError, "injected crash"):
                    builder._write_new(path, b"complete bytes")
            self.assertFalse(path.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_helper_source_cannot_be_overridden(self):
        self.assertNotIn(
            "--helper-source", builder._parser()._option_string_actions
        )

    def test_builder_rejects_ambient_inventory_verifier(self):
        with tempfile.TemporaryDirectory() as directory:
            release_root = Path(directory)
            expected = (
                release_root
                / "scripts"
                / "verify_three_site_staging_inventory.py"
            )
            expected.parent.mkdir()
            expected.write_text("# exact\n", encoding="utf-8")
            module_name = "scripts.verify_three_site_staging_inventory"
            shadow = types.ModuleType(module_name)
            shadow.__file__ = (
                "/tmp/ambient/verify_three_site_staging_inventory.py"
            )
            with patch.dict(sys.modules, {module_name: shadow}):
                with self.assertRaisesRegex(
                    builder.ContractBuildError, "shadowing rejected"
                ):
                    builder._import_exact_release_module(
                        release_root,
                        module_name,
                        "scripts/verify_three_site_staging_inventory.py",
                    )
        self.assertTrue(builder.sys.dont_write_bytecode)

    def test_builder_is_deterministic_and_content_addresses_every_snapshot(self):
        campaign_root = Path(
            "/root/secure-envs/trading-bot/three-site-staging-a1111111"
        )
        history_root = Path(
            "/root/secure-envs/trading-bot/three-site-staging-history"
        )
        rollback_root = Path(
            "/srv/trading-bot-three-site-staging-data/legacy-rollback/history"
        )
        inventory_path = campaign_root / "provisioned-inventory.json"
        approval_path = campaign_root / "approval.json"
        policy_path = campaign_root / "policy.json"
        restore_path = history_root / "restore.json"
        adopted_path = history_root / "adopted.json"
        freeze_path = history_root / "freeze.json"
        bundle_path = rollback_root / "bundle.json"
        compose_path = rollback_root / "compose.yaml"
        env_path = history_root / "source.env"
        release_root = Path(
            f"/srv/trading-bot-three-site/releases/{RELEASE_SHA}"
        )
        run_id = "3" * 16
        output_directory = campaign_root / (
            f"source-adoption-output-bot-fi-{run_id}"
        )
        inventory = {
            "release_sha": RELEASE_SHA,
            "host_safety_mode": "shared-host-safe",
            "roles": [{"role": "bot_fi"}],
        }
        approval = {"schema": "direct-inventory-token"}
        policy = {"issuer": {"public_key": "unused"}}
        service_images = {
            "db": DB_IMAGE,
            "foreign_app": APP_IMAGE,
            "redis": REDIS_IMAGE,
        }
        bundle_raw = b"bundle"
        compose_raw = b"compose"
        restore = {
            "campaign_id": "22222222-2222-4222-8222-222222222222",
            "release_sha": HISTORICAL_RELEASE_SHA,
            "service_images": service_images,
        }
        freeze = {
            "campaign_id": restore["campaign_id"],
            "target_release_sha": HISTORICAL_RELEASE_SHA,
            "source_roles": [
                {
                    "source_role": "bot_fi",
                    "app_service": "foreign_app",
                    "source_release_sha": SOURCE_RELEASE_SHA,
                }
            ],
            "legacy_restore_bundle": {
                "path": str(bundle_path),
                "sha256": hashlib.sha256(bundle_raw).hexdigest(),
                "size": len(bundle_raw),
            },
        }
        bundle = {
            "service_images": service_images,
            "compose": {
                "path": str(compose_path),
                "sha256": hashlib.sha256(compose_raw).hexdigest(),
                "size": len(compose_raw),
            },
        }
        documents = {
            inventory_path: (inventory, b"inventory"),
            approval_path: (approval, b"approval"),
            policy_path: (policy, b"policy"),
            restore_path: (restore, b"restore"),
            adopted_path: ({"adopted": True}, b"adopted"),
            freeze_path: (freeze, b"freeze"),
            bundle_path: (bundle, bundle_raw),
        }
        verified = {
            "campaign_id": CAMPAIGN_ID,
            "release_sha": RELEASE_SHA,
            "deployment_id": "three-site-a1111111",
            "host_safety_mode": "shared-host-safe",
            "inventory_stage": "provisioned",
            "inventory_sha256": hashlib.sha256(
                builder._canonical_bytes(inventory)
            ).hexdigest(),
            "approval_id": "33333333-3333-4333-8333-333333333333",
            "approval_token_sha256": hashlib.sha256(
                builder._canonical_bytes(approval)
            ).hexdigest(),
        }
        args = SimpleNamespace(
            source_role="bot_fi",
            inventory=inventory_path,
            inventory_approval=approval_path,
            approval_policy=policy_path,
            release_root=release_root,
            env_file=env_path,
            historical_restore_evidence=restore_path,
            historical_adopted_freeze_evidence=adopted_path,
            historical_freeze_evidence=freeze_path,
            historical_evidence_root=history_root,
            rollback_storage_root=rollback_root,
            scratch_postgres_image_id=SCRATCH_IMAGE,
            run_id=run_id,
            output_directory=output_directory,
            output=None,
        )

        def secure_bytes(path, **_kwargs):
            if path == compose_path:
                return compose_raw
            if path == Path(builder.adoption.__file__).resolve():
                return b"helper"
            if path == env_path:
                return b"ENV=value\n"
            raise AssertionError(path)

        def one_build():
            writes: list[tuple[Path, bytes, int, bool]] = []
            with patch.object(
                builder,
                "_secure_json",
                side_effect=lambda path, **_kwargs: documents[path],
            ), patch.object(
                builder, "_secure_bytes", side_effect=secure_bytes
            ), patch.object(
                builder, "_verify_release"
            ), patch.object(
                builder, "_verify_inventory", return_value=verified
            ), patch.object(
                builder,
                "_policy_reference",
                return_value=("6" * 64, "7" * 64, "8" * 64),
            ), patch.object(
                builder, "_verify_local_image"
            ), patch.object(
                builder,
                "_image_command_identity",
                return_value={
                    "id": SCRATCH_IMAGE,
                    "entrypoint": ["docker-entrypoint.sh"],
                    "cmd": ["postgres"],
                },
            ), patch.object(
                builder,
                "_observe_source_volumes",
                return_value={
                    "/app/uploads": {
                        "name": "legacy_uploads",
                        "compose_volume": "staging_uploads",
                    },
                    "/app/audit_trail": {
                        "name": "legacy_audit",
                        "compose_volume": "staging_audit",
                    },
                },
            ), patch.object(
                Path, "exists", return_value=False
            ), patch.object(
                Path, "is_symlink", return_value=False
            ), patch.object(
                builder.adoption, "_validate_historical_chain"
            ), patch.object(
                builder.adoption,
                "_project_snapshot",
                return_value={
                    "app_source_volumes": {
                        "/app/uploads": "legacy_uploads",
                        "/app/audit_trail": "legacy_audit",
                    }
                },
            ), patch.object(
                builder,
                "_write_new",
                side_effect=lambda path, raw, mode=0o600, reuse_exact=False: writes.append(
                    (path, raw, mode, reuse_exact)
                ),
            ):
                result = builder.build_contract(args)
            return result, writes

        first, first_writes = one_build()
        second, second_writes = one_build()
        self.assertEqual(first["contract"], second["contract"])
        self.assertEqual(first_writes, second_writes)
        contract_path = Path(first["contract"]["path"])
        self.assertEqual(
            contract_path.name,
            f"source-adoption-contract-{first['contract']['sha256']}.json",
        )
        written_names = {
            path.name for path, _raw, _mode, _reuse in first_writes
        }
        self.assertTrue(
            any(name.startswith("provisioned-inventory-snapshot-") for name in written_names)
        )
        self.assertTrue(
            any(name.startswith("human-approval-policy-snapshot-") for name in written_names)
        )
        helper_rows = [
            row
            for row in first_writes
            if row[0].name.startswith("adopt-three-site-frozen-source-")
        ]
        self.assertEqual(helper_rows[0][2], 0o700)
        self.assertTrue(helper_rows[0][3])
        contract_rows = [
            row
            for row in first_writes
            if row[0].name.startswith("source-adoption-contract-")
        ]
        self.assertFalse(contract_rows[0][3])


if __name__ == "__main__":
    unittest.main()
