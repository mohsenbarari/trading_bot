from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone
from dataclasses import replace

from scripts import adopt_three_site_staging_frozen_source_and_backup as helper


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
HISTORICAL_CAMPAIGN_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
HISTORICAL_RELEASE_SHA = "b" * 40
SOURCE_RELEASE_SHA = "c" * 40
APP_IMAGE = "sha256:" + "1" * 64
DB_IMAGE = "sha256:" + "2" * 64
REDIS_IMAGE = "sha256:" + "3" * 64
SCRATCH_IMAGE = "sha256:" + "4" * 64
RUN_ID = "3" * 16


def _role_contract() -> helper.HistoricalContract:
    return helper.HistoricalContract(
        role="bot_fi",
        project="trading_bot_staging",
        app_service="foreign_app",
        env_file=Path("/root/secure-envs/trading-bot/source.env"),
        env_sha256="0" * 64,
        inventory_approval=Path(
            "/root/secure-envs/trading-bot/current/"
            "provisioned-inventory-approval-bot-fi-"
            f"{hashlib.sha256(b'a').hexdigest()}.json"
        ),
        expected_approval_id="33333333-3333-4333-8333-333333333333",
        expected_approval_token_sha256="5" * 64,
        expected_approval_raw_sha256=hashlib.sha256(b"a").hexdigest(),
        source_adoption_subject=Path(
            f"/root/secure-envs/trading-bot/current/"
            f"source-adoption-subject-bot-fi-{RUN_ID}.json"
        ),
        source_adoption_approval=Path(
            f"/root/secure-envs/trading-bot/current/"
            f"source-adoption-approval-bot-fi-{RUN_ID}.json"
        ),
        output_dir=Path(
            f"/root/secure-envs/trading-bot/current/"
            f"source-adoption-output-bot-fi-{RUN_ID}"
        ),
        run_id=RUN_ID,
        restore_evidence_path=Path("/root/secure-envs/trading-bot/history/restore.json"),
        restore_evidence_sha256="6" * 64,
        adopted_freeze_path=Path("/root/secure-envs/trading-bot/history/adopted.json"),
        adopted_freeze_sha256="7" * 64,
        freeze_evidence_path=Path("/root/secure-envs/trading-bot/history/freeze.json"),
        freeze_evidence_sha256="8" * 64,
        restore_bundle_path=Path(
            "/srv/trading-bot-three-site-staging-data/legacy-rollback/history/bundle.json"
        ),
        restore_bundle_sha256="9" * 64,
        compose_path=Path(
            "/srv/trading-bot-three-site-staging-data/legacy-rollback/history/compose.yaml"
        ),
        compose_sha256="a" * 64,
        service_images=(
            ("db", DB_IMAGE),
            ("foreign_app", APP_IMAGE),
            ("redis", REDIS_IMAGE),
        ),
    )


def _valid_contract_payload() -> dict[str, object]:
    campaign_root = (
        "/root/secure-envs/trading-bot/three-site-staging-a1111111-11111111"
    )
    release_root = f"/srv/trading-bot-three-site/releases/{RELEASE_SHA}"
    history_root = "/root/secure-envs/trading-bot/three-site-staging-history"
    rollback_root = (
        "/srv/trading-bot-three-site-staging-data/legacy-rollback/history/official"
    )
    return {
        "schema": "three-site-staging-frozen-source-adoption-contract-v2",
        "current": {
            "campaign_id": CAMPAIGN_ID,
            "release_sha": RELEASE_SHA,
            "deployment_id": "three-site-a1111111-11111111",
            "host_safety_mode": "shared-host-safe",
            "campaign_root": campaign_root,
            "release_root": release_root,
            "inventory": {
                "path": (
                    f"{campaign_root}/"
                    f"provisioned-inventory-snapshot-{'9' * 64}.json"
                ),
                "raw_sha256": "9" * 64,
                "canonical_sha256": "a" * 64,
            },
            "approval_policy": {
                "path": (
                    f"{campaign_root}/"
                    f"human-approval-policy-snapshot-{'6' * 64}.json"
                ),
                "raw_sha256": "6" * 64,
                "canonical_sha256": "7" * 64,
                "public_key_sha256": "8" * 64,
            },
            "scratch_postgres_image": {
                "id": SCRATCH_IMAGE,
                "entrypoint": ["docker-entrypoint.sh"],
                "cmd": ["postgres"],
            },
            "helper": {
                "path": (
                    f"{release_root}/scripts/"
                    "adopt_three_site_staging_frozen_source_and_backup.py"
                ),
                "sha256": "d" * 64,
            },
        },
        "historical": {
            "campaign_id": HISTORICAL_CAMPAIGN_ID,
            "target_release_sha": HISTORICAL_RELEASE_SHA,
            "source_release_sha": SOURCE_RELEASE_SHA,
            "evidence_root": history_root,
            "rollback_storage_root": rollback_root,
        },
        "roles": {
            "bot_fi": {
                "project_name": "trading_bot_staging",
                "app_service": "foreign_app",
                "env_file": f"{history_root}/source.env",
                "env_sha256": "0" * 64,
                "inventory_approval_path": (
                    f"{campaign_root}/"
                    "provisioned-inventory-approval-bot-fi-"
                    f"{'f' * 64}.json"
                ),
                "expected_approval_id": "33333333-3333-4333-8333-333333333333",
                "expected_approval_token_sha256": "e" * 64,
                "expected_approval_raw_sha256": "f" * 64,
                "source_adoption_subject_path": (
                    f"{campaign_root}/"
                    f"source-adoption-subject-bot-fi-{RUN_ID}.json"
                ),
                "source_adoption_approval_path": (
                    f"{campaign_root}/"
                    f"source-adoption-approval-bot-fi-{RUN_ID}.json"
                ),
                "output_dir": (
                    f"{campaign_root}/source-adoption-output-bot-fi-{RUN_ID}"
                ),
                "run_id": RUN_ID,
                "restore_evidence": {
                    "path": f"{history_root}/restore.json",
                    "sha256": "1" * 64,
                },
                "adopted_freeze_evidence": {
                    "path": f"{history_root}/adopted.json",
                    "sha256": "2" * 64,
                },
                "freeze_evidence": {
                    "path": f"{history_root}/freeze.json",
                    "sha256": "3" * 64,
                },
                "restore_bundle": {
                    "path": f"{rollback_root}/bot-fi/bundle.json",
                    "sha256": "4" * 64,
                },
                "compose": {
                    "path": f"{rollback_root}/bot-fi/compose.yaml",
                    "sha256": "5" * 64,
                },
                "service_images": {
                    "db": DB_IMAGE,
                    "foreign_app": APP_IMAGE,
                    "redis": REDIS_IMAGE,
                },
                "source_volumes": {
                    "/app/uploads": {
                        "name": "trading_bot_staging_staging_uploads",
                        "compose_volume": "staging_uploads",
                    },
                    "/app/audit_trail": {
                        "name": "trading_bot_staging_staging_audit",
                        "compose_volume": "staging_audit",
                    },
                },
            }
        },
    }


class FrozenSourceAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        contract = _role_contract()
        self.contract = contract
        self.runtime = patch.multiple(
            helper,
            CURRENT_CAMPAIGN_ID=CAMPAIGN_ID,
            CURRENT_RELEASE_SHA=RELEASE_SHA,
            CURRENT_DEPLOYMENT_ID="three-site-a1111111-11111111",
            CURRENT_HOST_SAFETY_MODE="shared-host-safe",
            CURRENT_CAMPAIGN_ROOT=Path("/root/secure-envs/trading-bot/current"),
            CURRENT_RELEASE_ROOT=Path(
                f"/srv/trading-bot-three-site/releases/{RELEASE_SHA}"
            ),
            CURRENT_INVENTORY_PATH=Path(
                "/root/secure-envs/trading-bot/current/inventory.json"
            ),
            CURRENT_INVENTORY_RAW_SHA256=hashlib.sha256(b"i").hexdigest(),
            CURRENT_INVENTORY_SHA256=helper._canonical_hash(
                {"production_boundaries": {}}
            ),
            CURRENT_APPROVAL_POLICY_PATH=Path(
                "/root/secure-envs/trading-bot/current/policy.json"
            ),
            CURRENT_APPROVAL_POLICY_RAW_SHA256="6" * 64,
            CURRENT_APPROVAL_POLICY_SHA256="7" * 64,
            CURRENT_APPROVAL_PUBLIC_KEY_SHA256="8" * 64,
            SCRATCH_POSTGRES_IMAGE_ID=SCRATCH_IMAGE,
            SCRATCH_POSTGRES_ENTRYPOINT=("docker-entrypoint.sh",),
            SCRATCH_POSTGRES_CMD=("postgres",),
            ADOPTION_CONTRACT_PATH=Path(
                "/root/secure-envs/trading-bot/current/adoption.json"
            ),
            ADOPTION_CONTRACT_SHA256="f" * 64,
            EXPECTED_HELPER_PATH=Path(
                "/root/secure-envs/trading-bot/current/adopt.py"
            ),
            EXPECTED_HELPER_SHA256="d" * 64,
            HISTORICAL_CAMPAIGN_ID=HISTORICAL_CAMPAIGN_ID,
            HISTORICAL_TARGET_RELEASE_SHA=HISTORICAL_RELEASE_SHA,
            SOURCE_RELEASE_SHA=SOURCE_RELEASE_SHA,
            HISTORICAL_ROOT=Path("/root/secure-envs/trading-bot/history"),
            ROLLBACK_STORAGE_ROOT=Path(
                "/srv/trading-bot-three-site-staging-data/legacy-rollback/history"
            ),
            CONTRACTS={"bot_fi": contract},
            EXPECTED_SOURCE_VOLUMES={
                "bot_fi": {
                    "/app/uploads": (
                        "trading_bot_staging_staging_uploads",
                        "staging_uploads",
                    ),
                    "/app/audit_trail": (
                        "trading_bot_staging_staging_audit",
                        "staging_audit",
                    ),
                }
            },
        )
        self.runtime.start()

    def tearDown(self) -> None:
        self.runtime.stop()

    def _preflight(self, tracker: helper.TemporaryResources) -> dict[str, object]:
        snapshot = {
            "services": {
                "foreign_app": {"image_id": APP_IMAGE},
            },
            "app_source_volumes": {
                "/app/uploads": "trading_bot_staging_staging_uploads",
                "/app/audit_trail": "trading_bot_staging_staging_audit",
            },
        }
        measurement = {
            "postgres": {"system_id": "7000000000000000001"},
            "redis_observation": {
                "dbsize": 3,
                "appendonly": True,
                "lastsave_unix": 100,
                "restore": False,
            },
        }
        return {
            "contract": self.contract,
            "inventory_approval": {
                "approval_id": "33333333-3333-4333-8333-333333333333",
                "approval_token_sha256": "5" * 64,
                "approval_expires_at": "2099-01-01T00:00:00+00:00",
                "inventory_sha256": "6" * 64,
                "approval_policy_sha256": "7" * 64,
                "_production_boundaries": {
                    "postgres_system_ids": ["7000000000000000001"],
                    "volume_ids": [
                        "trading_bot_staging_staging_uploads",
                        "trading_bot_staging_staging_audit",
                    ],
                    "audit_root_ids": [
                        "trading_bot_staging_staging_audit"
                    ],
                },
            },
            "source_adoption_approval": {
                "action": helper.SOURCE_ADOPTION_ACTION,
                "environment": "staging",
                "approval_path": str(
                    self.contract.source_adoption_approval
                ),
                "approval_id": "44444444-4444-4444-8444-444444444444",
                "approval_token_sha256": "a" * 64,
                "approval_token_raw_sha256": "b" * 64,
                "approval_issued_at": "2026-07-27T00:00:00+00:00",
                "approval_expires_at": "2026-07-27T01:00:00+00:00",
                "approval_policy_sha256": "7" * 64,
                "approval_subject_sha256": helper._canonical_hash(
                    helper.source_adoption_approval_subject("bot_fi")
                ),
                "adoption_contract_sha256": "f" * 64,
            },
            "snapshot": snapshot,
            "measurement": measurement,
            "protected_identities": {
                "postgres_system_id": "7000000000000000001",
                "uploads_volume_id": "trading_bot_staging_staging_uploads",
                "audit_volume_id": "trading_bot_staging_staging_audit",
            },
            "historical": {
                "freeze_raw_sha256": self.contract.freeze_evidence_sha256,
                "service_images": dict(self.contract.service_images),
            },
            "env": {},
            "tracker": tracker,
        }

    def test_contract_loader_is_generic_strict_and_duplicate_key_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = _valid_contract_payload()
            raw = json.dumps(payload).encode()
            campaign_root = Path(payload["current"]["campaign_root"])
            path = campaign_root / (
                f"source-adoption-contract-{hashlib.sha256(raw).hexdigest()}.json"
            )
            with patch.object(
                helper, "_secure_json", return_value=(payload, raw)
            ):
                helper._load_adoption_contract(path)
            self.assertEqual(helper.CURRENT_CAMPAIGN_ID, CAMPAIGN_ID)
            self.assertEqual(helper.CURRENT_RELEASE_SHA, RELEASE_SHA)
            self.assertEqual(helper.CURRENT_HOST_SAFETY_MODE, "shared-host-safe")
            self.assertEqual(
                dict(helper.CONTRACTS["bot_fi"].service_images)["db"], DB_IMAGE
            )

            dedicated_payload = _valid_contract_payload()
            dedicated_payload["current"]["host_safety_mode"] = (
                "dedicated-host-destructive"
            )
            dedicated_raw = json.dumps(dedicated_payload).encode()
            dedicated = campaign_root / (
                "source-adoption-contract-"
                f"{hashlib.sha256(dedicated_raw).hexdigest()}.json"
            )
            with patch.object(
                helper,
                "_secure_json",
                return_value=(dedicated_payload, dedicated_raw),
            ):
                helper._load_adoption_contract(dedicated)
            self.assertEqual(
                helper.CURRENT_HOST_SAFETY_MODE,
                "dedicated-host-destructive",
            )

            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
            duplicate.chmod(0o600)
            with self.assertRaisesRegex(helper.AdoptionError, "duplicate"):
                helper._secure_json(duplicate)

            insecure = Path(directory) / "insecure.json"
            insecure.write_text("{}", encoding="utf-8")
            insecure.chmod(0o644)
            with self.assertRaisesRegex(helper.AdoptionError, "mode/owner/size"):
                helper._secure_json(insecure)

            symlink = Path(directory) / "linked.json"
            symlink.symlink_to(path)
            with self.assertRaisesRegex(helper.AdoptionError, "securely open"):
                helper._secure_json(symlink)

    def test_inventory_verifier_binds_shared_and_dedicated_execution_class(self):
        args = SimpleNamespace(
            source_role="bot_fi",
            inventory=helper.CURRENT_INVENTORY_PATH,
            inventory_approval=self.contract.inventory_approval,
            approval_policy=helper.CURRENT_APPROVAL_POLICY_PATH,
        )
        inventory = {"production_boundaries": {}}
        fake_module = types.ModuleType(
            "scripts.verify_three_site_staging_inventory"
        )
        calls: list[tuple[bool, bool]] = []

        def verify(
            _inventory,
            *,
            approval,
            approval_policy,
            host_destructive,
            require_fresh_approval,
        ):
            del approval, approval_policy
            calls.append((host_destructive, require_fresh_approval))
            return {
                "inventory_stage": "provisioned",
                "campaign_id": CAMPAIGN_ID,
                "release_sha": RELEASE_SHA,
                "deployment_id": "three-site-a1111111-11111111",
                "host_safety_mode": helper.CURRENT_HOST_SAFETY_MODE,
                "inventory_sha256": helper.CURRENT_INVENTORY_SHA256,
                "approval_id": self.contract.expected_approval_id,
                "approval_token_sha256": (
                    self.contract.expected_approval_token_sha256
                ),
            }

        fake_module.verify_approved_inventory = verify
        with patch.object(
            helper,
            "_import_exact_release_module",
            return_value=fake_module,
        ), patch.object(
            helper, "_verify_approval_policy_binding"
        ), patch.object(
            helper,
            "_secure_json",
            side_effect=[(inventory, b"i"), ({}, b"a"), ({}, b"p")] * 2,
        ):
            helper._verify_current_approval(args, require_fresh=True)
            with patch.object(
                helper,
                "CURRENT_HOST_SAFETY_MODE",
                "dedicated-host-destructive",
            ):
                helper._verify_current_approval(args, require_fresh=False)
        self.assertEqual(calls, [(False, True), (True, False)])

    def test_direct_action_approval_is_subject_bound_and_rejects_sessions(self):
        args = SimpleNamespace(
            source_role="bot_fi",
            source_adoption_approval=self.contract.source_adoption_approval,
            approval_policy=helper.CURRENT_APPROVAL_POLICY_PATH,
        )
        observed: dict[str, object] = {}

        def verify(token, **kwargs):
            observed.update(kwargs)
            self.assertEqual(token, {"token": "direct"})
            return SimpleNamespace(
                approval_id="44444444-4444-4444-8444-444444444444",
                token_hash="a" * 64,
                issued_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
                expires_at=datetime(
                    2026, 7, 27, 1, tzinfo=timezone.utc
                ),
            )

        with patch.object(
            helper,
            "_secure_json",
            side_effect=[
                ({"token": "direct"}, b"direct-token"),
                ({"issuer": {}}, b"policy"),
            ],
        ), patch.object(
            helper, "_verify_approval_policy_binding"
        ), patch.object(
            helper,
            "_import_exact_release_module",
            side_effect=lambda name, _path: (
                SimpleNamespace(verify_human_approval=verify)
                if name == "core.human_approval"
                else SimpleNamespace()
            ),
        ):
            result = helper._verify_source_adoption_approval(
                args, require_fresh=True
            )
        self.assertEqual(
            observed["expected_action"],
            "approve_source_adoption_backup",
        )
        self.assertEqual(observed["expected_environment"], "staging")
        self.assertIs(observed["allow_session"], False)
        self.assertIs(observed["require_fresh"], True)
        bindings = observed["expected_subject"]["bindings"]
        self.assertEqual(bindings["run_id"], RUN_ID)
        self.assertEqual(
            bindings["operation_id"],
            helper._operation_id("bot_fi", RUN_ID),
        )
        self.assertEqual(
            bindings["output_dir"], str(self.contract.output_dir)
        )
        self.assertEqual(
            result["adoption_contract_sha256"],
            helper.ADOPTION_CONTRACT_SHA256,
        )

    def test_direct_action_lifetime_over_one_hour_is_rejected(self):
        args = SimpleNamespace(
            source_role="bot_fi",
            source_adoption_approval=self.contract.source_adoption_approval,
            approval_policy=helper.CURRENT_APPROVAL_POLICY_PATH,
        )
        issued = datetime(2026, 7, 27, tzinfo=timezone.utc)
        verified = SimpleNamespace(
            approval_id="44444444-4444-4444-8444-444444444444",
            token_hash="a" * 64,
            issued_at=issued,
            expires_at=issued + timedelta(hours=2),
        )
        with patch.object(
            helper,
            "_secure_json",
            side_effect=[
                ({"token": "direct"}, b"direct-token"),
                ({"issuer": {}}, b"policy"),
            ],
        ), patch.object(
            helper, "_verify_approval_policy_binding"
        ), patch.object(
            helper,
            "_import_exact_release_module",
            side_effect=lambda name, _path: (
                SimpleNamespace(
                    verify_human_approval=lambda *_args, **_kwargs: verified
                )
                if name == "core.human_approval"
                else SimpleNamespace()
            ),
        ):
            with self.assertRaisesRegex(
                helper.AdoptionError, "exceeds one hour"
            ):
                helper._verify_source_adoption_approval(
                    args, require_fresh=True
                )

    def test_approval_subject_dispatch_never_requires_token_or_docker(self):
        argv = [
            "--adoption-contract",
            str(helper.ADOPTION_CONTRACT_PATH),
            "--mode",
            "approval-subject",
            "--source-role",
            "bot_fi",
        ]
        with patch.object(
            helper, "_load_adoption_contract"
        ), patch.object(
            helper,
            "_emit_approval_subject",
            return_value={"status": "source-adoption-approval-subject-ready"},
        ) as emit, patch.object(
            helper, "_preflight"
        ) as preflight, patch(
            "sys.stdout", new_callable=io.StringIO
        ):
            self.assertEqual(helper.main(argv), 0)
        emit.assert_called_once()
        preflight.assert_not_called()

    def test_approval_subject_emission_is_create_only_and_token_free(self):
        contract = self.contract
        args = SimpleNamespace(
            source_role="bot_fi",
            inventory=helper.CURRENT_INVENTORY_PATH,
            inventory_approval=contract.inventory_approval,
            approval_policy=helper.CURRENT_APPROVAL_POLICY_PATH,
            approval_subject_output=contract.source_adoption_subject,
            output_dir=contract.output_dir,
        )
        inventory_approval = {
            "inventory_sha256": "6" * 64,
            "approval_token_sha256": "5" * 64,
        }
        written: list[tuple[Path, bytes]] = []
        with patch.object(
            helper, "_validate_new_output_path"
        ), patch.object(
            helper, "_verify_exact_release"
        ), patch.object(
            helper,
            "_verify_current_approval",
            return_value=inventory_approval,
        ) as inventory, patch.object(
            helper,
            "_secure_bytes",
            return_value=b"historical-freeze",
        ), patch.object(
            helper,
            "_sha256",
            side_effect=lambda raw: (
                contract.freeze_evidence_sha256
                if raw == b"historical-freeze"
                else __import__("hashlib").sha256(raw).hexdigest()
            ),
        ), patch.object(
            helper,
            "_exclusive_write",
            side_effect=lambda path, raw: written.append((path, raw)),
        ), patch.object(
            helper,
            "_verify_source_adoption_approval",
            side_effect=AssertionError("direct token must not be read"),
        ):
            result = helper._emit_approval_subject(args)
        inventory.assert_called_once_with(args, require_fresh=False)
        self.assertEqual(
            written[0][0], contract.source_adoption_subject
        )
        self.assertEqual(result["docker_access"], False)
        self.assertEqual(result["run_id"], RUN_ID)

    def test_output_replay_and_operation_drift_are_contract_rejected(self):
        contract = self.contract
        args = SimpleNamespace(
            source_role="bot_fi",
            inventory=helper.CURRENT_INVENTORY_PATH,
            inventory_approval=contract.inventory_approval,
            source_adoption_approval=contract.source_adoption_approval,
            approval_policy=helper.CURRENT_APPROVAL_POLICY_PATH,
            output_dir=contract.output_dir.with_name("replayed-output"),
            historical_restore_evidence=contract.restore_evidence_path,
            historical_adopted_freeze_evidence=contract.adopted_freeze_path,
            historical_freeze_evidence=contract.freeze_evidence_path,
            historical_restore_bundle=contract.restore_bundle_path,
            env_file=contract.env_file,
            scratch_postgres_image_id=SCRATCH_IMAGE,
        )
        with self.assertRaisesRegex(helper.AdoptionError, "runtime paths"):
            helper._preflight(args)
        subject = helper.source_adoption_approval_subject("bot_fi")
        self.assertEqual(subject["bindings"]["run_id"], contract.run_id)
        self.assertEqual(
            subject["bindings"]["operation_id"],
            helper._operation_id("bot_fi", contract.run_id),
        )

    def test_ambient_shadow_module_is_rejected_before_recovery_authority(self):
        modules = (
            ("core.human_approval", "core/human_approval.py"),
            (
                "scripts.verify_three_site_staging_inventory",
                "scripts/verify_three_site_staging_inventory.py",
            ),
            (
                "scripts.restore_three_site_staging_sources",
                "scripts/restore_three_site_staging_sources.py",
            ),
            (
                "scripts.run_three_site_staging_source_backup",
                "scripts/run_three_site_staging_source_backup.py",
            ),
        )
        for module_name, relative_path in modules:
            with self.subTest(module_name=module_name):
                with tempfile.TemporaryDirectory() as directory:
                    release_root = Path(directory)
                    expected = release_root / relative_path
                    expected.parent.mkdir(parents=True)
                    expected.write_text("# exact\n", encoding="utf-8")
                    shadow = types.ModuleType(module_name)
                    shadow.__file__ = f"/tmp/ambient/{relative_path}"
                    with patch.object(
                        helper, "CURRENT_RELEASE_ROOT", release_root
                    ), patch.dict(
                        sys.modules, {module_name: shadow}
                    ):
                        with self.assertRaisesRegex(
                            helper.AdoptionError, "shadowing rejected"
                        ):
                            helper._import_exact_release_module(
                                module_name,
                                relative_path,
                            )

    def test_exact_release_imports_never_write_bytecode(self):
        with tempfile.TemporaryDirectory() as directory:
            release_root = Path(directory)
            module_name = "tb3_exact_release_probe"
            (release_root / f"{module_name}.py").write_text(
                "VALUE = 1\n", encoding="utf-8"
            )
            with patch.object(
                helper, "CURRENT_RELEASE_ROOT", release_root
            ):
                imported = helper._import_exact_release_module(
                    module_name, f"{module_name}.py"
                )
            self.assertEqual(imported.VALUE, 1)
            self.assertTrue(helper.sys.dont_write_bytecode)
            self.assertFalse((release_root / "__pycache__").exists())
            sys.modules.pop(module_name, None)
            while str(release_root) in sys.path:
                sys.path.remove(str(release_root))

    def test_subprocess_environment_forces_read_only_git_and_no_pyc(self):
        self.assertEqual(helper.SAFE_ENV["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(
            helper.SAFE_ENV["PYTHONDONTWRITEBYTECODE"], "1"
        )

    def test_source_has_no_previous_campaign_or_approval_constants(self):
        source = Path(helper.__file__).read_text(encoding="utf-8")
        for stale in (
            "727c1f33-7b75-41d7-a565-0b7f11fe8159",
            "771c957bf22d094c40e9e406f5b136c88d056930",
            "dc32d903-92b0-4229-b6b1-f0442adfd8e3",
            "1768baca-1ba1-4ce5-a4b9-a138e02165de",
        ):
            self.assertNotIn(stale, source)

    def test_adopted_freeze_requires_exact_reviewed_raw_bytes(self):
        restore = {
            "freeze_evidence_sha256": "0" * 64,
            "legacy_restore_bundle_sha256": "1" * 64,
            "service_images": dict(self.contract.service_images),
            "restored_at": "2026-07-26T02:40:00+00:00",
        }
        with patch.object(
            helper,
            "_secure_json",
            return_value=({"schema": "untrusted"}, b"changed-bytes"),
        ):
            with self.assertRaisesRegex(helper.AdoptionError, "exact adopted"):
                helper._validate_adopted_restore_chain(
                    contract=self.contract,
                    path=self.contract.adopted_freeze_path,
                    restore=restore,
                )

    def test_worker_commands_are_exact_image_networkless_and_bounded(self):
        archive = helper.archive_container_create_command(
            name="tb3-test-uploads",
            operation_id="frozen-backup-test",
            source_volume="legacy_uploads",
            app_image_id=APP_IMAGE,
        )
        self.assertIn("--pull=never", archive)
        self.assertIn("--network=none", archive)
        self.assertIn("--read-only", archive)
        self.assertIn("--cap-drop=ALL", archive)
        self.assertIn("--memory=256m", archive)
        self.assertIn(
            "type=volume,src=legacy_uploads,dst=/source,readonly,volume-nocopy",
            archive,
        )
        scratch = helper.scratch_container_create_command(
            name="tb3-test-restore",
            operation_id="frozen-backup-test",
            volume="tb3-test-pgdata",
            image_id=SCRATCH_IMAGE,
        )
        self.assertIn("--pull=never", scratch)
        self.assertIn("--network=none", scratch)
        self.assertIn("--read-only", scratch)
        self.assertEqual(scratch.count("--mount"), 1)

    def test_pinned_compose_removes_builds_and_uses_only_exact_images(self):
        source = {
            "services": {
                "db": {"image": "tag-db", "build": "."},
                "redis": {"image": "tag-redis"},
                "foreign_app": {
                    "image": "tag-app",
                    "build": {"context": "."},
                    "depends_on": {"redis": {}, "migration": {}},
                },
                "migration": {"image": "tag-app", "build": "."},
            },
            "volumes": {"staging_uploads": {}},
        }
        import yaml

        historical = {
            "compose_raw": yaml.safe_dump(source).encode(),
            "service_images": dict(self.contract.service_images),
            "restore_raw_sha256": "1" * 64,
            "freeze_raw_sha256": "2" * 64,
            "bundle_raw_sha256": "3" * 64,
            "compose_sha256": "4" * 64,
        }
        rendered = yaml.safe_load(
            helper._derive_pinned_compose(
                historical,
                role="bot_fi",
                raw_copy_path=Path("/secure/historical.yaml"),
            )
        )
        for service in ("db", "redis", "foreign_app", "migration"):
            self.assertNotIn("build", rendered["services"][service])
            self.assertEqual(rendered["services"][service]["pull_policy"], "never")
        self.assertEqual(rendered["services"]["db"]["image"], DB_IMAGE)
        self.assertEqual(rendered["services"]["redis"]["image"], REDIS_IMAGE)
        self.assertEqual(rendered["services"]["foreign_app"]["image"], APP_IMAGE)
        self.assertEqual(rendered["services"]["migration"]["image"], APP_IMAGE)

    def test_postgres_dump_forces_readonly_transaction(self):
        command = helper.postgres_dump_command(
            container_id="a" * 64,
            user="trading",
            database="trading",
        )
        self.assertIn("PGOPTIONS=-c default_transaction_read_only=on", command)

    def test_archive_verifier_rejects_devices_links_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            safe = Path(directory) / "safe.tar.gz"
            with tarfile.open(safe, "w:gz") as archive:
                info = tarfile.TarInfo("uploads/file.txt")
                payload = b"data"
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            self.assertEqual(helper._verify_tar_artifact(safe), 1)

            unsafe = Path(directory) / "unsafe.tar.gz"
            with tarfile.open(unsafe, "w:gz") as archive:
                fifo = tarfile.TarInfo("../pipe")
                fifo.type = tarfile.FIFOTYPE
                archive.addfile(fifo)
            with self.assertRaisesRegex(helper.AdoptionError, "unsafe member"):
                helper._verify_tar_artifact(unsafe)

    def test_protected_source_identity_must_match_inventory_and_contract(self):
        expected = helper.EXPECTED_SOURCE_VOLUMES["bot_fi"]
        snapshot = {
            "app_source_volumes": {
                destination: identity[0]
                for destination, identity in expected.items()
            }
        }
        system_id = "7000000000000000001"
        measurement = {"postgres": {"system_id": system_id}}
        boundaries = {
            "postgres_system_ids": [system_id],
            "volume_ids": [identity[0] for identity in expected.values()],
            "audit_root_ids": [expected["/app/audit_trail"][0]],
        }

        def run(arguments, *, timeout=60):  # noqa: ARG001
            volume_name = arguments[-1]
            template = arguments[-2]
            if "com.docker.compose.project" in template:
                return self.contract.project
            return next(
                logical
                for name, logical in expected.values()
                if name == volume_name
            )

        with patch.object(helper, "_run", side_effect=run):
            result = helper._validate_protected_source_identities(
                contract=self.contract,
                boundaries=boundaries,
                snapshot=snapshot,
                measurement=measurement,
                scratch_volume_name="tb3-new-scratch",
            )
        self.assertEqual(
            result["audit_volume_id"], expected["/app/audit_trail"][0]
        )
        snapshot["app_source_volumes"]["/app/uploads"] = "foreign_volume"
        with self.assertRaisesRegex(helper.AdoptionError, "fixed role contract"):
            helper._validate_protected_source_identities(
                contract=self.contract,
                boundaries=boundaries,
                snapshot=snapshot,
                measurement=measurement,
                scratch_volume_name="tb3-new-scratch",
            )

    def test_stopped_application_chronology_rejects_later_restart(self):
        historical = {"freeze": {"observed_at": "2026-07-26T02:40:56+00:00"}}
        snapshot = {
            "services": {
                "foreign_app": {
                    "created_at": "2026-07-25T00:00:00+00:00",
                    "started_at": "2026-07-26T02:39:00+00:00",
                    "finished_at": "2026-07-26T02:40:55+00:00",
                }
            }
        }
        helper._validate_app_stop_chronology(
            contract=self.contract,
            snapshot=snapshot,
            historical=historical,
        )
        snapshot["services"]["foreign_app"]["finished_at"] = (
            "2026-07-26T03:00:00+00:00"
        )
        with self.assertRaisesRegex(helper.AdoptionError, "restarted"):
            helper._validate_app_stop_chronology(
                contract=self.contract,
                snapshot=snapshot,
                historical=historical,
            )

    def test_temporary_resource_budget_is_three_containers_one_volume(self):
        tracker = helper.TemporaryResources(role="bot_fi", run_id="0" * 16)
        absent = subprocess.CompletedProcess([], 1, "", "")
        with patch.object(helper, "_probe", return_value=absent):
            for purpose in ("uploads", "audit", "restore"):
                tracker.reserve_container(
                    purpose,
                    expected_image_id=(
                        SCRATCH_IMAGE if purpose == "restore" else APP_IMAGE
                    ),
                    expected_mount_volume=(
                        f"{tracker.prefix}-pgdata"
                        if purpose == "restore"
                        else f"legacy_{purpose}"
                    ),
                    expected_mount_destination=(
                        "/var/lib/postgresql/data"
                        if purpose == "restore"
                        else "/source"
                    ),
                    expected_mount_readonly=purpose != "restore",
                )
            with self.assertRaisesRegex(helper.AdoptionError, "budget"):
                tracker.reserve_container(
                    "uploads",
                    expected_image_id=APP_IMAGE,
                    expected_mount_volume="legacy_uploads",
                    expected_mount_destination="/source",
                    expected_mount_readonly=True,
                )
            tracker.reserve_volume()
            with self.assertRaisesRegex(helper.AdoptionError, "volume"):
                tracker.reserve_volume()

    def test_create_success_output_lost_is_reconciled_by_full_identity(self):
        tracker = helper.TemporaryResources(role="bot_fi", run_id="1" * 16)
        full_id = "d" * 64
        source_volume = "legacy_uploads"
        exists = {"value": False}
        removals: list[list[str]] = []
        name = f"{tracker.prefix}-uploads"

        def probe(arguments, *, timeout=30):  # noqa: ARG001
            if arguments[1:3] == ["container", "inspect"]:
                return subprocess.CompletedProcess(
                    arguments, 0 if exists["value"] else 1, "", ""
                )
            if arguments[1:5] == ["container", "rm", "-f", "-v"]:
                removals.append(arguments)
                exists["value"] = False
                return subprocess.CompletedProcess(arguments, 0, full_id, "")
            raise AssertionError(arguments)

        values = {
            "{{.Id}}": full_id,
            "{{.Name}}": f"/{name}",
            "{{.Image}}": APP_IMAGE,
            f'{{{{index .Config.Labels "{helper.TEMP_LABEL_KEY}"}}}}': (
                tracker.operation_id
            ),
            "{{.State.Status}}": "running",
            "{{.HostConfig.NetworkMode}}": "none",
            "{{.HostConfig.ReadonlyRootfs}}": "true",
            "{{.HostConfig.LogConfig.Type}}": "none",
            "{{.HostConfig.RestartPolicy.Name}}": "no",
            "{{.HostConfig.Memory}}": "268435456",
            "{{.HostConfig.NanoCpus}}": "1000000000",
            "{{.HostConfig.PidsLimit}}": "128",
            "{{.HostConfig.Privileged}}": "false",
            "{{json .HostConfig.Binds}}": "null",
            "{{json .HostConfig.Tmpfs}}": "{}",
            "{{json .HostConfig.CapDrop}}": '["ALL"]',
            "{{json .HostConfig.CapAdd}}": "null",
            "{{json .HostConfig.Devices}}": "null",
            "{{json .HostConfig.DeviceRequests}}": "null",
            "{{json .HostConfig.SecurityOpt}}": '["no-new-privileges"]',
            "{{json .HostConfig.PortBindings}}": "null",
            "{{.HostConfig.PidMode}}": "",
            "{{.HostConfig.IpcMode}}": "private",
            "{{json .Config.Entrypoint}}": '["tar"]',
            "{{json .Config.Cmd}}": '["-C","/source","-czf","-","."]',
            "{{json .Mounts}}": json.dumps(
                [
                    {
                        "Type": "volume",
                        "Name": source_volume,
                        "Destination": "/source",
                        "RW": False,
                    }
                ]
            ),
        }
        with patch.object(helper, "_probe", side_effect=probe), patch.object(
            helper, "_inspect_value", side_effect=lambda _identity, template: values[template]
        ):
            tracker.reserve_container(
                "uploads",
                expected_image_id=APP_IMAGE,
                expected_mount_volume=source_volume,
                expected_mount_destination="/source",
                expected_mount_readonly=True,
            )
            exists["value"] = True
            tracker.remove_container(name)
        self.assertEqual(
            removals,
            [[helper.DOCKER, "container", "rm", "-f", "-v", full_id]],
        )

    def test_recovery_rejects_extra_bind_or_tmpfs_without_deleting(self):
        for extra_kind in ("bind", "tmpfs"):
            with self.subTest(extra_kind=extra_kind):
                tracker = helper.TemporaryResources(
                    role="bot_fi", run_id=("7" if extra_kind == "bind" else "8") * 16
                )
                name = f"{tracker.prefix}-uploads"
                full_id = ("7" if extra_kind == "bind" else "8") * 64
                source_volume = "legacy_uploads"
                exists = {"value": False}
                removals: list[list[str]] = []

                def probe(arguments, *, timeout=30):  # noqa: ARG001
                    if arguments[1:3] == ["container", "inspect"]:
                        return subprocess.CompletedProcess(
                            arguments, 0 if exists["value"] else 1, "", ""
                        )
                    if arguments[1:5] == ["container", "rm", "-f", "-v"]:
                        removals.append(arguments)
                        return subprocess.CompletedProcess(arguments, 0, "", "")
                    raise AssertionError(arguments)

                mounts = [
                    {
                        "Type": "volume",
                        "Name": source_volume,
                        "Destination": "/source",
                        "RW": False,
                    }
                ]
                tmpfs = {}
                if extra_kind == "bind":
                    mounts.append(
                        {
                            "Type": "bind",
                            "Source": "/host",
                            "Destination": "/unexpected",
                            "RW": True,
                        }
                    )
                else:
                    tmpfs["/unexpected"] = "rw,size=1m"
                values = {
                    "{{.Id}}": full_id,
                    "{{.Name}}": f"/{name}",
                    "{{.Image}}": APP_IMAGE,
                    f'{{{{index .Config.Labels "{helper.TEMP_LABEL_KEY}"}}}}': (
                        tracker.operation_id
                    ),
                    "{{.State.Status}}": "running",
                    "{{.HostConfig.NetworkMode}}": "none",
                    "{{.HostConfig.ReadonlyRootfs}}": "true",
                    "{{.HostConfig.LogConfig.Type}}": "none",
                    "{{.HostConfig.RestartPolicy.Name}}": "no",
                    "{{.HostConfig.Memory}}": "268435456",
                    "{{.HostConfig.NanoCpus}}": "1000000000",
                    "{{.HostConfig.PidsLimit}}": "128",
                    "{{.HostConfig.Privileged}}": "false",
                    "{{json .HostConfig.Binds}}": "null",
                    "{{json .HostConfig.Tmpfs}}": json.dumps(tmpfs),
                    "{{json .HostConfig.CapDrop}}": '["ALL"]',
                    "{{json .HostConfig.CapAdd}}": "null",
                    "{{json .HostConfig.Devices}}": "null",
                    "{{json .HostConfig.DeviceRequests}}": "null",
                    "{{json .HostConfig.SecurityOpt}}": (
                        '["no-new-privileges"]'
                    ),
                    "{{json .HostConfig.PortBindings}}": "null",
                    "{{.HostConfig.PidMode}}": "",
                    "{{.HostConfig.IpcMode}}": "private",
                    "{{json .Config.Entrypoint}}": '["tar"]',
                    "{{json .Config.Cmd}}": (
                        '["-C","/source","-czf","-","."]'
                    ),
                    "{{json .Mounts}}": json.dumps(mounts),
                }
                with patch.object(
                    helper, "_probe", side_effect=probe
                ), patch.object(
                    helper,
                    "_inspect_value",
                    side_effect=lambda _identity, template: values[template],
                ):
                    tracker.reserve_container(
                        "uploads",
                        expected_image_id=APP_IMAGE,
                        expected_mount_volume=source_volume,
                        expected_mount_destination="/source",
                        expected_mount_readonly=True,
                    )
                    exists["value"] = True
                    with self.assertRaisesRegex(
                        helper.AdoptionError,
                        "boundaries differ",
                    ):
                        tracker.remove_container(name)
                self.assertEqual(removals, [])

    def test_recovery_rejects_restore_command_drift_without_deleting(self):
        tracker = helper.TemporaryResources(
            role="bot_fi", run_id="9" * 16
        )
        name = f"{tracker.prefix}-restore"
        full_id = "9" * 64
        volume = f"{tracker.prefix}-pgdata"
        exists = {"value": False}
        removals: list[list[str]] = []

        def probe(arguments, *, timeout=30):  # noqa: ARG001
            if arguments[1:3] == ["container", "inspect"]:
                return subprocess.CompletedProcess(
                    arguments, 0 if exists["value"] else 1, "", ""
                )
            if arguments[1:5] == ["container", "rm", "-f", "-v"]:
                removals.append(arguments)
                return subprocess.CompletedProcess(arguments, 0, "", "")
            raise AssertionError(arguments)

        values = {
            "{{.Id}}": full_id,
            "{{.Name}}": f"/{name}",
            "{{.Image}}": SCRATCH_IMAGE,
            f'{{{{index .Config.Labels "{helper.TEMP_LABEL_KEY}"}}}}': (
                tracker.operation_id
            ),
            "{{.State.Status}}": "created",
            "{{.HostConfig.NetworkMode}}": "none",
            "{{.HostConfig.ReadonlyRootfs}}": "true",
            "{{.HostConfig.LogConfig.Type}}": "none",
            "{{.HostConfig.RestartPolicy.Name}}": "no",
            "{{.HostConfig.Memory}}": "1073741824",
            "{{.HostConfig.NanoCpus}}": "2000000000",
            "{{.HostConfig.PidsLimit}}": "256",
            "{{.HostConfig.Privileged}}": "false",
            "{{json .HostConfig.Binds}}": "null",
            "{{json .HostConfig.Tmpfs}}": json.dumps(
                {
                    "/tmp": "rw,noexec,nosuid,nodev,size=64m",
                    "/var/run/postgresql": (
                        "rw,noexec,nosuid,nodev,size=16m"
                    ),
                }
            ),
            "{{json .HostConfig.CapDrop}}": "null",
            "{{json .HostConfig.CapAdd}}": "null",
            "{{json .HostConfig.Devices}}": "null",
            "{{json .HostConfig.DeviceRequests}}": "null",
            "{{json .HostConfig.SecurityOpt}}": (
                '["no-new-privileges"]'
            ),
            "{{json .HostConfig.PortBindings}}": "null",
            "{{.HostConfig.PidMode}}": "",
            "{{.HostConfig.IpcMode}}": "private",
            "{{json .Config.Entrypoint}}": '["/malicious"]',
            "{{json .Config.Cmd}}": '["postgres"]',
            "{{json .Config.Env}}": json.dumps(
                [
                    "POSTGRES_USER=restore",
                    "POSTGRES_DB=restore",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                ]
            ),
            "{{json .Mounts}}": json.dumps(
                [
                    {
                        "Type": "volume",
                        "Name": volume,
                        "Destination": "/var/lib/postgresql/data",
                        "RW": True,
                    },
                    {
                        "Type": "tmpfs",
                        "Destination": "/tmp",
                        "RW": True,
                    },
                    {
                        "Type": "tmpfs",
                        "Destination": "/var/run/postgresql",
                        "RW": True,
                    },
                ]
            ),
        }
        with patch.object(
            helper, "_probe", side_effect=probe
        ), patch.object(
            helper,
            "_inspect_value",
            side_effect=lambda _identity, template: values[template],
        ):
            tracker.reserve_container(
                "restore",
                expected_image_id=SCRATCH_IMAGE,
                expected_mount_volume=volume,
                expected_mount_destination="/var/lib/postgresql/data",
                expected_mount_readonly=False,
                recovery=True,
            )
            exists["value"] = True
            with self.assertRaisesRegex(
                helper.AdoptionError, "command differs"
            ):
                tracker.remove_container(name)
        self.assertEqual(removals, [])

    def test_foreign_residue_is_never_removed(self):
        tracker = helper.TemporaryResources(role="bot_fi", run_id="2" * 16)
        exists = {"value": False}
        removals: list[list[str]] = []
        name = f"{tracker.prefix}-audit"

        def probe(arguments, *, timeout=30):  # noqa: ARG001
            if arguments[1:3] == ["container", "inspect"]:
                return subprocess.CompletedProcess(
                    arguments, 0 if exists["value"] else 1, "", ""
                )
            if arguments[1:5] == ["container", "rm", "-f", "-v"]:
                removals.append(arguments)
                return subprocess.CompletedProcess(arguments, 0, "", "")
            raise AssertionError(arguments)

        values = {
            "{{.Id}}": "e" * 64,
            "{{.Name}}": f"/{name}",
            "{{.Image}}": APP_IMAGE,
            f'{{{{index .Config.Labels "{helper.TEMP_LABEL_KEY}"}}}}': "foreign",
        }
        with patch.object(helper, "_probe", side_effect=probe), patch.object(
            helper, "_inspect_value", side_effect=lambda _identity, template: values[template]
        ):
            tracker.reserve_container(
                "audit",
                expected_image_id=APP_IMAGE,
                expected_mount_volume="legacy_audit",
                expected_mount_destination="/source",
                expected_mount_readonly=True,
            )
            exists["value"] = True
            with self.assertRaisesRegex(helper.AdoptionError, "ownership"):
                tracker.remove_container(name)
        self.assertEqual(removals, [])

    def test_create_success_output_lost_volume_is_checked_then_removed(self):
        tracker = helper.TemporaryResources(role="bot_fi", run_id="4" * 16)
        exists = {"value": False}
        removals: list[list[str]] = []
        name = f"{tracker.prefix}-pgdata"

        def probe(arguments, *, timeout=30):  # noqa: ARG001
            if arguments[1:3] == ["volume", "inspect"]:
                return subprocess.CompletedProcess(
                    arguments, 0 if exists["value"] else 1, "", ""
                )
            if arguments[1:3] == ["volume", "rm"]:
                removals.append(arguments)
                exists["value"] = False
                return subprocess.CompletedProcess(arguments, 0, name, "")
            raise AssertionError(arguments)

        def run(arguments, *, timeout=60):  # noqa: ARG001
            template = arguments[-2]
            if template == "{{.Name}}":
                return name
            if helper.TEMP_LABEL_KEY in template:
                return tracker.operation_id
            if template == "{{.Driver}}":
                return "local"
            if template == "{{.Scope}}":
                return "local"
            raise AssertionError(arguments)

        with patch.object(helper, "_probe", side_effect=probe), patch.object(
            helper, "_run", side_effect=run
        ):
            self.assertEqual(tracker.reserve_volume(), name)
            exists["value"] = True
            tracker.remove_volume(name)
        self.assertEqual(removals, [[helper.DOCKER, "volume", "rm", name]])

    def test_journal_precedes_artifacts_and_any_docker_create(self):
        tracker = MagicMock(unsafe=True)
        tracker.role = "bot_fi"
        tracker.created_container_ids = {}
        tracker.container_reservations = {}
        tracker.container_ids = {}
        tracker.active_volume = None
        preflight = self._preflight(tracker)
        events: list[str] = []
        journal = {"path": Path("/secure/journal"), "raw_sha256": "a" * 64}

        with patch.object(
            helper, "_prepare_output_directory", return_value=Path("/secure/output")
        ), patch.object(
            helper,
            "_write_resource_journal",
            side_effect=lambda **_kwargs: events.append("journal") or journal,
        ), patch.object(
            helper,
            "_write_rollback_and_freeze",
            side_effect=lambda **_kwargs: events.append("artifacts") or {},
        ), patch.object(
            helper,
            "_create_source_backups",
            side_effect=lambda **_kwargs: (
                events.append("docker"),
                (_ for _ in ()).throw(helper.ControlledInterruption("kill")),
            )[1],
        ), patch.object(
            helper, "_project_snapshot", return_value=preflight["snapshot"]
        ), patch.object(
            helper, "_write_cleanup_completion", return_value={}
        ):
            with self.assertRaises(helper.ControlledInterruption):
                helper._execute_locked(
                    SimpleNamespace(
                        output_dir=Path("/secure/output"),
                        scratch_postgres_image_id=SCRATCH_IMAGE,
                    ),
                    preflight,
                )
        self.assertEqual(events, ["journal", "artifacts", "docker"])

    def test_apply_reverifies_contract_and_live_approval_before_mutation(self):
        events: list[str] = []
        inventory_approval = {
            "approval_id": "33333333-3333-4333-8333-333333333333",
            "approval_token_sha256": "1" * 64,
            "inventory_sha256": "2" * 64,
            "approval_policy_sha256": "3" * 64,
            "approval_expires_at": "2099-01-01T00:00:00+00:00",
        }
        source_adoption_approval = self._preflight(
            helper.TemporaryResources(role="bot_fi", run_id=RUN_ID)
        )["source_adoption_approval"]
        source_adoption_approval = {
            **source_adoption_approval,
            "approval_issued_at": "2098-12-31T23:00:00+00:00",
            "approval_expires_at": "2099-01-01T00:00:00+00:00",
        }
        preflight = {
            "contract": self.contract,
            "required_confirmation": "confirm",
            "inventory_approval": inventory_approval,
            "source_adoption_approval": source_adoption_approval,
        }
        args = SimpleNamespace(
            confirm="confirm",
            source_role="bot_fi",
            scratch_postgres_image_id=SCRATCH_IMAGE,
        )

        class Lock:
            def __init__(self, role):
                self.role = role

            def __enter__(self):
                events.append("lock")

            def __exit__(self, *_args):
                return False

        with patch.object(
            helper,
            "_verify_adoption_contract_unchanged",
            side_effect=lambda: events.append("contract"),
        ), patch.object(
            helper,
            "_verify_exact_release",
            side_effect=lambda: events.append("release"),
        ), patch.object(
            helper,
            "_verify_current_approval",
            side_effect=lambda _args, **_kwargs: events.append("inventory")
            or inventory_approval,
        ), patch.object(
            helper,
            "_verify_source_adoption_approval",
            side_effect=lambda _args, **_kwargs: events.append("action")
            or source_adoption_approval,
        ), patch.object(
            helper, "_secure_env", return_value={}
        ), patch.object(
            helper, "_verify_scratch_image_identity"
        ), patch.object(
            helper, "RoleApplyLock", Lock
        ), patch.object(
            helper,
            "_revalidate_apply_state",
            side_effect=lambda _args, _preflight: events.append("state"),
        ), patch.object(
            helper,
            "_execute_locked",
            side_effect=lambda _args, _preflight: events.append("execute")
            or {"status": "done"},
        ):
            helper._execute(args, preflight)
        self.assertEqual(
            events,
            [
                "contract",
                "release",
                "inventory",
                "action",
                "lock",
                "contract",
                "state",
                "contract",
                "release",
                "action",
                "execute",
            ],
        )

    def test_action_expiry_during_revalidation_blocks_before_output(self):
        fixture = self._preflight(
            helper.TemporaryResources(role="bot_fi", run_id=RUN_ID)
        )
        inventory_approval = fixture["inventory_approval"]
        action = {
            **fixture["source_adoption_approval"],
            "approval_issued_at": (
                datetime.now(timezone.utc) - timedelta(minutes=40)
            ).isoformat(),
            "approval_expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=30)
            ).isoformat(),
        }
        preflight = {
            "required_confirmation": "confirm",
            "inventory_approval": inventory_approval,
            "source_adoption_approval": action,
        }
        args = SimpleNamespace(confirm="confirm", source_role="bot_fi")

        class Lock:
            def __init__(self, _role):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def expire(_args, _preflight):
            action["approval_expires_at"] = (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat()

        with patch.object(
            helper,
            "_refresh_authority",
            return_value=(inventory_approval, action),
        ), patch.object(
            helper, "RoleApplyLock", Lock
        ), patch.object(
            helper, "_verify_adoption_contract_unchanged"
        ), patch.object(
            helper, "_verify_exact_release"
        ), patch.object(
            helper, "_revalidate_apply_state", side_effect=expire
        ), patch.object(
            helper,
            "_verify_source_adoption_approval",
            return_value=action,
        ), patch.object(
            helper, "_execute_locked"
        ) as execute:
            with self.assertRaisesRegex(
                helper.AdoptionError, "before the first effect"
            ):
                helper._execute(args, preflight)
        execute.assert_not_called()

    def test_env_mutation_after_approval_blocks_before_first_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            env_path = root / "source.env"
            original = b"POSTGRES_USER=staging\nPOSTGRES_DB=trading\n"
            env_path.write_bytes(original)
            env_path.chmod(0o600)
            contract = replace(
                self.contract,
                env_file=env_path,
                env_sha256=hashlib.sha256(original).hexdigest(),
            )
            fixture = self._preflight(
                helper.TemporaryResources(role="bot_fi", run_id=RUN_ID)
            )
            action = {
                **fixture["source_adoption_approval"],
                "approval_issued_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "approval_expires_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=30)
                ).isoformat(),
            }
            preflight = {
                "contract": contract,
                "required_confirmation": "confirm",
                "inventory_approval": fixture["inventory_approval"],
                "source_adoption_approval": action,
            }
            args = SimpleNamespace(
                confirm="confirm", source_role="bot_fi"
            )

            class Lock:
                def __init__(self, _role):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            def mutate_env(_args, _preflight):
                env_path.write_bytes(
                    b"POSTGRES_USER=attacker\nPOSTGRES_DB=other\n"
                )
                env_path.chmod(0o600)

            with patch.object(
                helper,
                "_refresh_authority",
                return_value=(fixture["inventory_approval"], action),
            ), patch.object(
                helper, "RoleApplyLock", Lock
            ), patch.object(
                helper, "_verify_adoption_contract_unchanged"
            ), patch.object(
                helper, "_verify_exact_release"
            ), patch.object(
                helper,
                "_revalidate_apply_state",
                side_effect=mutate_env,
            ), patch.object(
                helper,
                "_verify_source_adoption_approval",
                return_value=action,
            ), patch.object(
                helper, "_execute_locked"
            ) as execute:
                with self.assertRaisesRegex(
                    helper.AdoptionError, "environment bytes differ"
                ):
                    helper._execute(args, preflight)
            execute.assert_not_called()

    def test_later_effect_rejects_env_or_scratch_identity_drift(self):
        fixture = self._preflight(
            helper.TemporaryResources(role="bot_fi", run_id=RUN_ID)
        )
        fixture["env"] = {"POSTGRES_USER": "staging"}
        args = SimpleNamespace(
            source_role="bot_fi",
            scratch_postgres_image_id=SCRATCH_IMAGE,
        )
        common = (
            patch.object(helper, "_verify_adoption_contract_unchanged"),
            patch.object(helper, "_verify_exact_release"),
            patch.object(
                helper,
                "_verify_source_adoption_approval",
                return_value=fixture["source_adoption_approval"],
            ),
        )
        with common[0], common[1], common[2], patch.object(
            helper,
            "_secure_env",
            return_value={"POSTGRES_USER": "attacker"},
        ), patch.object(
            helper, "_verify_scratch_image_identity"
        ) as scratch:
            with self.assertRaisesRegex(
                helper.AdoptionError, "environment semantics"
            ):
                helper._authorize_next_apply_effect(args, fixture)
        scratch.assert_not_called()

        with patch.object(
            helper, "_verify_adoption_contract_unchanged"
        ), patch.object(
            helper, "_verify_exact_release"
        ), patch.object(
            helper,
            "_verify_source_adoption_approval",
            return_value=fixture["source_adoption_approval"],
        ), patch.object(
            helper, "_secure_env", return_value=fixture["env"]
        ), patch.object(
            helper,
            "_verify_scratch_image_identity",
            side_effect=helper.AdoptionError(
                "scratch image command identity differs"
            ),
        ):
            with self.assertRaisesRegex(
                helper.AdoptionError, "scratch image command"
            ):
                helper._authorize_next_apply_effect(args, fixture)

    def test_later_effect_denial_still_cleans_created_container(self):
        tracker = MagicMock(unsafe=True)
        tracker.operation_id = "operation"
        tracker.reserve_container.return_value = "archive"
        tracker.record_container.return_value = "a" * 64
        tracker.active_containers = {"archive"}
        authorize = MagicMock(
            side_effect=[
                None,
                helper.AdoptionError("approval expired"),
            ]
        )
        with patch.object(
            helper, "_run", return_value="a" * 64
        ) as run, patch.object(
            helper, "_verify_archive_worker"
        ), patch.object(
            helper, "_stream_command_to_exclusive_file"
        ) as stream:
            with self.assertRaisesRegex(
                helper.AdoptionError, "approval expired"
            ):
                helper._create_archive(
                    tracker=tracker,
                    purpose="uploads",
                    target=Path("/secure/archive.tar.gz"),
                    source_volume="legacy_uploads",
                    app_image_id=APP_IMAGE,
                    authorize_effect=authorize,
                )
        self.assertEqual(authorize.call_count, 2)
        self.assertEqual(run.call_count, 1)
        stream.assert_not_called()
        tracker.remove_container.assert_called_once_with("archive")

    def test_expired_exact_token_is_valid_only_for_cleanup_recovery(self):
        fixture = self._preflight(
            helper.TemporaryResources(role="bot_fi", run_id="5" * 16)
        )
        inventory_approval = fixture["inventory_approval"]
        source_adoption_approval = {
            **fixture["source_adoption_approval"],
            "approval_issued_at": "2019-12-31T23:00:00+00:00",
            "approval_expires_at": "2020-01-01T00:00:00+00:00",
        }
        preflight = {
            "inventory_approval": inventory_approval,
            "source_adoption_approval": source_adoption_approval,
            "recovery_journal": {
                "payload": {
                    "inventory_approval": {
                        field: inventory_approval[field]
                        for field in helper.INVENTORY_APPROVAL_BINDING_FIELDS
                    },
                    "source_adoption_approval": source_adoption_approval,
                }
            },
        }
        observed: list[tuple[str, bool]] = []

        def verify_inventory(_args, *, require_fresh=True):
            observed.append(("inventory", require_fresh))
            return inventory_approval

        def verify_action(_args, *, require_fresh=True):
            observed.append(("action", require_fresh))
            return source_adoption_approval

        with patch.object(
            helper, "_verify_adoption_contract_unchanged"
        ), patch.object(
            helper, "_verify_exact_release"
        ), patch.object(
            helper, "_verify_current_approval", side_effect=verify_inventory
        ), patch.object(
            helper,
            "_verify_source_adoption_approval",
            side_effect=verify_action,
        ):
            result = helper._refresh_recovery_authority(
                SimpleNamespace(), preflight
            )
        self.assertEqual(
            result, (inventory_approval, source_adoption_approval)
        )
        self.assertEqual(
            observed, [("inventory", False), ("action", False)]
        )

    def test_recovery_rejects_token_substitution_or_renewal(self):
        fixture = self._preflight(
            helper.TemporaryResources(role="bot_fi", run_id="6" * 16)
        )
        inventory_approval = fixture["inventory_approval"]
        source_adoption_approval = fixture["source_adoption_approval"]
        substituted = {
            **source_adoption_approval,
            "approval_token_sha256": "8" * 64,
        }
        preflight = {
            "inventory_approval": inventory_approval,
            "source_adoption_approval": source_adoption_approval,
            "recovery_journal": {
                "payload": {
                    "inventory_approval": {
                        field: inventory_approval[field]
                        for field in helper.INVENTORY_APPROVAL_BINDING_FIELDS
                    },
                    "source_adoption_approval": source_adoption_approval,
                }
            },
        }
        with patch.object(
            helper, "_verify_adoption_contract_unchanged"
        ), patch.object(
            helper, "_verify_exact_release"
        ), patch.object(
            helper,
            "_verify_current_approval",
            return_value=inventory_approval,
        ), patch.object(
            helper,
            "_verify_source_adoption_approval",
            return_value=substituted,
        ):
            with self.assertRaisesRegex(
                helper.AdoptionError, "source-adoption approval"
            ):
                helper._refresh_recovery_authority(
                    SimpleNamespace(), preflight
                )

    def test_sigkill_restart_reconstructs_journal_and_proves_zero_residue(self):
        tracker = helper.TemporaryResources(role="bot_fi", run_id="3" * 16)
        preflight = self._preflight(tracker)
        uploads_name = f"{tracker.prefix}-uploads"
        uploads_id = "f" * 64
        exists = {"uploads": True}
        removals: list[list[str]] = []

        def probe(arguments, *, timeout=30):  # noqa: ARG001
            if arguments[1:3] == ["container", "inspect"]:
                identity = arguments[-1]
                present = exists["uploads"] and identity in {
                    uploads_name,
                    uploads_id,
                }
                return subprocess.CompletedProcess(
                    arguments, 0 if present else 1, "", ""
                )
            if arguments[1:3] == ["volume", "inspect"]:
                return subprocess.CompletedProcess(arguments, 1, "", "")
            if arguments[1:5] == ["container", "rm", "-f", "-v"]:
                removals.append(arguments)
                exists["uploads"] = False
                return subprocess.CompletedProcess(arguments, 0, uploads_id, "")
            raise AssertionError(arguments)

        values = {
            "{{.Id}}": uploads_id,
            "{{.Name}}": f"/{uploads_name}",
            "{{.Image}}": APP_IMAGE,
            f'{{{{index .Config.Labels "{helper.TEMP_LABEL_KEY}"}}}}': (
                tracker.operation_id
            ),
            "{{.State.Status}}": "running",
            "{{.HostConfig.NetworkMode}}": "none",
            "{{.HostConfig.ReadonlyRootfs}}": "true",
            "{{.HostConfig.LogConfig.Type}}": "none",
            "{{.HostConfig.RestartPolicy.Name}}": "no",
            "{{.HostConfig.Memory}}": "268435456",
            "{{.HostConfig.NanoCpus}}": "1000000000",
            "{{.HostConfig.PidsLimit}}": "128",
            "{{.HostConfig.Privileged}}": "false",
            "{{json .HostConfig.Binds}}": "null",
            "{{json .HostConfig.Tmpfs}}": "{}",
            "{{json .HostConfig.CapDrop}}": '["ALL"]',
            "{{json .HostConfig.CapAdd}}": "null",
            "{{json .HostConfig.Devices}}": "null",
            "{{json .HostConfig.DeviceRequests}}": "null",
            "{{json .HostConfig.SecurityOpt}}": '["no-new-privileges"]',
            "{{json .HostConfig.PortBindings}}": "null",
            "{{.HostConfig.PidMode}}": "",
            "{{.HostConfig.IpcMode}}": "private",
            "{{json .Config.Entrypoint}}": '["tar"]',
            "{{json .Config.Cmd}}": '["-C","/source","-czf","-","."]',
            "{{json .Mounts}}": json.dumps(
                [
                    {
                        "Type": "volume",
                        "Name": "trading_bot_staging_staging_uploads",
                        "Destination": "/source",
                        "RW": False,
                    }
                ]
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            output.chmod(0o700)
            preflight["source_adoption_approval"]["approval_issued_at"] = (
                "2019-12-31T23:00:00+00:00"
            )
            preflight["source_adoption_approval"]["approval_expires_at"] = (
                "2020-01-01T00:00:00+00:00"
            )
            journal = helper._write_resource_journal(
                output_dir=output,
                tracker=tracker,
                preflight=preflight,
                scratch_image_id=SCRATCH_IMAGE,
            )
            with patch.object(helper, "_probe", side_effect=probe):
                recovered, loaded = helper._load_recovery_tracker(
                    output_dir=output,
                    preflight=preflight,
                    scratch_image_id=SCRATCH_IMAGE,
                )
            self.assertEqual(loaded["raw_sha256"], journal["raw_sha256"])
            self.assertEqual(len(recovered.container_reservations), 3)
            preflight["tracker"] = recovered
            preflight["recovery_journal"] = loaded
            with patch.object(helper, "_probe", side_effect=probe), patch.object(
                helper, "_run", return_value=""
            ), patch.object(
                helper,
                "_inspect_value",
                side_effect=lambda _identity, template: values[template],
            ), patch.object(
                helper, "_project_snapshot", return_value=preflight["snapshot"]
            ), patch.object(
                helper, "_measure_source", return_value=preflight["measurement"]
            ), patch.object(
                helper,
                "_write_cleanup_completion",
                return_value={"raw_sha256": "b" * 64},
            ):
                result = helper._recover_locked(
                    SimpleNamespace(output_dir=output, source_role="bot_fi"),
                    preflight,
                )
        self.assertTrue(result["zero_residue_verified"])
        self.assertEqual(
            removals,
            [[helper.DOCKER, "container", "rm", "-f", "-v", uploads_id]],
        )
        self.assertEqual(
            result["status"], "recovered-zero-residue-restart-with-new-output"
        )

    def test_cleanup_failure_emits_signal_and_never_claims_completion(self):
        tracker = MagicMock(unsafe=True)
        tracker.role = "bot_fi"
        tracker.operation_id = "operation"
        tracker.cleanup.side_effect = helper.AdoptionError("cannot remove")
        preflight = self._preflight(tracker)
        preflight["recovery_journal"] = {"raw_sha256": "a" * 64}
        with patch.object(
            helper, "_project_snapshot", return_value=preflight["snapshot"]
        ), patch.object(
            helper, "_measure_source", return_value=preflight["measurement"]
        ), patch.object(
            helper, "_write_cleanup_signal"
        ) as signal, patch.object(
            helper, "_write_cleanup_completion"
        ) as completion:
            with self.assertRaisesRegex(helper.AdoptionError, "failed closed"):
                helper._recover_locked(
                    SimpleNamespace(
                        output_dir=Path("/secure/output"), source_role="bot_fi"
                    ),
                    preflight,
                )
        signal.assert_called_once()
        completion.assert_not_called()

    def test_exclusive_outputs_never_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact"
            helper._exclusive_write(path, b"first")
            with self.assertRaises(FileExistsError):
                helper._exclusive_write(path, b"second")
            self.assertEqual(path.read_bytes(), b"first")

    def test_output_directory_and_campaign_entry_are_fsynced_before_use(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            output = root / "fixed-output"
            calls: list[int] = []
            real_fsync = os.fsync

            def recording_fsync(descriptor):
                calls.append(descriptor)
                return real_fsync(descriptor)

            with patch.object(
                helper, "CURRENT_CAMPAIGN_ROOT", root
            ), patch.object(
                os, "fsync", side_effect=recording_fsync
            ):
                self.assertEqual(
                    helper._prepare_output_directory(output), output
                )
            self.assertTrue(output.is_dir())
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
