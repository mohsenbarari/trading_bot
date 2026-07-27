from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4

import yaml

from scripts.build_fresh_three_site_staging_role_package import build_role_package
from scripts.fresh_three_site_staging_preflight_agent import _install_role_package
from core.three_site_execution_safety import (
    DEDICATED_HOST_DESTRUCTIVE,
    SHARED_HOST_SAFE,
)
from scripts.build_fresh_three_site_staging_planned_inventory import (
    derive_fresh_planned_inventory,
)
from scripts.generate_fresh_three_site_staging_private_material import (
    FreshPrivateMaterialError,
    SECRET_ENV_NAMES,
    generate_fresh_private_material,
    main,
    telegram_read,
    verify_fresh_private_material_manifest,
)
from scripts.render_three_site_staging_role_compose import parse_env_values
from scripts.verify_three_site_staging_inventory import _canonical_bytes
from tests.test_three_site_staging_signed_inventory import _inventory


PRIMARY_TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"
EDITOR_TOKEN = "987654321:ABCDEFGHIJKLMNOPQRSTUVWXYZabcde"
CHANNEL_ID = "-1001234567890"
OLD_MARKERS = {
    "old-tls-private-key-must-not-copy",
    "old-keyring-secret-must-not-copy",
    "old-witness-secret-must-not-copy",
    "old-session-relay-secret-must-not-copy",
    "old-approval-token-must-not-copy",
}


class GenerateFreshThreeSiteStagingPrivateMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template_paths = (
            "deploy/staging/env.three-site.staging.example",
            "deploy/staging/docker-compose.three-site.yml",
        )
        blobs = {name: (root / name).read_bytes() for name in template_paths}
        self.prove_release = patch(
            "scripts.generate_fresh_three_site_staging_private_material."
            "prove_exact_git_release",
            return_value=SimpleNamespace(
                blobs=blobs,
                blob_sha256={
                    name: hashlib.sha256(value).hexdigest()
                    for name, value in blobs.items()
                },
                recheck=lambda: None,
            ),
        )
        self.prove_release.start()

    def tearDown(self) -> None:
        self.prove_release.stop()

    def _inputs(
        self,
        root: Path,
        *,
        execution_class: str = DEDICATED_HOST_DESTRUCTIVE,
        suffix: str = "one",
    ) -> tuple[Path, Path, Path, dict]:
        template = _inventory()
        template["host_safety_mode"] = execution_class
        inventory, _subject = derive_fresh_planned_inventory(
            template_inventory=template,
            release_sha="b" * 40,
            campaign_id=str(uuid4()),
            deployment_id=f"three-site-fresh-material-{suffix}",
            execution_class=execution_class,
        )
        inventory_path = root / f"{suffix}.inventory.json"
        inventory_path.write_bytes(_canonical_bytes(inventory) + b"\n")
        inventory_path.chmod(0o600)

        provider_path = root / f"{suffix}.provider.env"
        provider_path.write_text(
            "\n".join(
                (
                    f"BOT_TOKEN={PRIMARY_TOKEN}",
                    "BOT_USERNAME=primary_bot",
                    f"CHANNEL_ID={CHANNEL_ID}",
                    (
                        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN="
                        f"{EDITOR_TOKEN}"
                    ),
                    "OLD_TLS_KEY=old-tls-private-key-must-not-copy",
                    "OLD_BLOB_KEYRING=old-keyring-secret-must-not-copy",
                    "OLD_WITNESS_KEY=old-witness-secret-must-not-copy",
                    "OLD_RELAY_SECRET=old-session-relay-secret-must-not-copy",
                    "OLD_APPROVAL=old-approval-token-must-not-copy",
                    "",
                )
            ),
            encoding="utf-8",
        )
        provider_path.chmod(0o600)

        s3_path = root / f"{suffix}.s3.json"
        s3_path.write_text(
            json.dumps(
                {
                    "access_key": "fresh-test-access",
                    "secret_key": "s" * 48,
                }
            ),
            encoding="utf-8",
        )
        s3_path.chmod(0o600)
        return provider_path, s3_path, inventory_path, inventory

    @staticmethod
    def _telegram(
        calls: list[tuple[str, str, dict[str, str] | None]] | None = None,
    ):
        def fake(
            token: str,
            method: str,
            payload: dict[str, str] | None,
        ) -> dict:
            if calls is not None:
                calls.append((token, method, payload))
            if method == "getMe":
                if token == PRIMARY_TOKEN:
                    return {"id": 111111111, "username": "primary_bot", "is_bot": True}
                return {"id": 222222222, "username": "editor_bot", "is_bot": True}
            if method == "getChat":
                return {
                    "id": int(CHANNEL_ID),
                    "type": "channel",
                    "invite_link": "https://t.me/+fresh_campaign_channel",
                }
            primary = token == PRIMARY_TOKEN
            enabled = (
                {"can_manage_chat": True, "can_edit_messages": True,
                 "can_post_messages": True, "can_restrict_members": True}
                if primary
                else {"can_manage_chat": True, "can_edit_messages": True}
            )
            return {
                "user": {"id": 111111111 if primary else 222222222, "is_bot": True},
                "status": "administrator", "is_anonymous": False,
                "can_be_edited": False, "can_manage_chat": False,
                "can_delete_messages": False, "can_manage_video_chats": False,
                "can_restrict_members": False, "can_promote_members": False,
                "can_change_info": False, "can_invite_users": False,
                "can_post_stories": False, "can_edit_stories": False,
                "can_delete_stories": False, **enabled,
            }

        return fake

    def _generate(
        self,
        root: Path,
        *,
        suffix: str,
        execution_class: str = DEDICATED_HOST_DESTRUCTIVE,
    ) -> tuple[Path, dict]:
        provider, s3, inventory_path, inventory = self._inputs(
            root,
            execution_class=execution_class,
            suffix=suffix,
        )
        output = root / f"{suffix}.material"
        generate_fresh_private_material(
            provider_environment=provider,
            provider_s3=s3,
            planned_inventory=inventory_path,
            output=output,
            execution_class=execution_class,
            telegram_reader=self._telegram(),
            now=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        )
        return output, inventory

    def test_generates_closed_role_topology_from_inventory_only(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            calls: list[tuple[str, str, dict[str, str] | None]] = []
            provider, s3, inventory_path, inventory = self._inputs(root)
            output = root / "material"

            result = generate_fresh_private_material(
                provider_environment=provider,
                provider_s3=s3,
                planned_inventory=inventory_path,
                output=output,
                execution_class=DEDICATED_HOST_DESTRUCTIVE,
                telegram_reader=self._telegram(calls),
                now=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(result["status"], "fresh-private-material-created")
            self.assertFalse(result["secret_values_printed"])
            self.assertFalse(result["seed_age_identity_generated"])
            self.assertEqual(
                [(method, payload) for _token, method, payload in calls],
                [
                    ("getMe", None), ("getChat", {"chat_id": CHANNEL_ID}),
                    ("getChatMember", {"chat_id": CHANNEL_ID, "user_id": "111111111"}),
                    ("getMe", None), ("getChat", {"chat_id": CHANNEL_ID}),
                    ("getChatMember", {"chat_id": CHANNEL_ID, "user_id": "222222222"}),
                ],
            )
            manifest = json.loads(
                (output / "material-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["schema"],
                "three-site-staging-private-material-manifest-v4",
            )
            self.assertEqual(
                manifest["inventory_sha256"],
                hashlib.sha256(_canonical_bytes(inventory)).hexdigest(),
            )
            self.assertEqual(
                manifest["object_storage"]["endpoint"],
                "https://s3.ir-thr-at1.arvanstorage.ir",
            )
            self.assertEqual(manifest["object_storage"]["region"], "ir-thr-at1")
            self.assertFalse(
                manifest["freshness"]["old_campaign_secret_material_copied"]
            )
            self.assertFalse(
                manifest["freshness"]["human_approval_relay_material_generated"]
            )
            self.assertFalse(manifest["freshness"]["seed_age_identity_generated"])
            self.assertNotIn("material-manifest.json", manifest["files"])
            self.assertEqual(
                manifest["controller_only_files"], ["secrets/staging-dr-ca.key"]
            )
            self.assertNotIn(
                "secrets/staging-dr-blob-s3.json", manifest["role_files"]["bot-fi"]
            )
            self.assertNotIn(
                "secrets/staging-dr-blob-s3.json", manifest["role_files"]["witness"]
            )
            self.assertNotIn(
                "secrets/staging-dr-ca.key", set().union(*map(set, manifest["role_files"].values()))
            )
            self.assertEqual(
                verify_fresh_private_material_manifest(output)["campaign_id"],
                inventory["campaign_id"],
            )

            by_role = {item["role"]: item for item in inventory["roles"]}
            expected_bind = {
                "bot-fi": ("BOT_FI_DR_BIND_ADDRESS", "bot_fi"),
                "webapp-fi": ("WEBAPP_FI_DR_BIND_ADDRESS", "webapp_fi"),
                "webapp-ir": ("WEBAPP_IR_DR_BIND_ADDRESS", "webapp_ir"),
                "witness": ("WITNESS_DR_BIND_ADDRESS", "witness"),
            }
            for role, (bind_name, inventory_role) in expected_bind.items():
                values = parse_env_values(
                    (output / "roles" / f"{role}.env").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    values[bind_name],
                    by_role[inventory_role]["host_ip"],
                )
                self.assertEqual(values["STAGING_RELEASE_SHA"], "b" * 40)
                self.assertEqual(
                    values["STAGING_SOURCE_ROOT"],
                    f"/srv/trading-bot-three-site/releases/{'b' * 40}",
                )
                self.assertEqual(
                    values["STAGING_STORAGE_NAMESPACE"],
                    inventory["compose_project_namespace"],
                )
                if role == "witness":
                    self.assertEqual(
                        values["STAGING_HUMAN_APPROVAL_RELAY_ENABLED"], "false"
                    )
                    self.assertEqual(
                        values["STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR"],
                        "/dev/null",
                    )
                compose = yaml.safe_load(
                    (output / "roles" / f"{role}.compose.yml").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    compose["name"],
                    f"{inventory['compose_project_namespace']}-{role}",
                )
            webapp_fi = parse_env_values(
                (output / "roles/webapp-fi.env").read_text(encoding="utf-8")
            )
            webapp_ir = parse_env_values(
                (output / "roles/webapp-ir.env").read_text(encoding="utf-8")
            )
            self.assertEqual(
                webapp_fi["WEBAPP_FI_PEER_BOT_FI_IP"],
                by_role["bot_fi"]["host_ip"],
            )
            self.assertEqual(
                webapp_fi["WEBAPP_FI_PEER_WEBAPP_IR_IP"],
                by_role["webapp_ir"]["host_ip"],
            )
            self.assertEqual(
                webapp_ir["WEBAPP_IR_WITNESS_IP"],
                by_role["witness"]["host_ip"],
            )

            combined = b"\n".join(
                path.read_bytes() for path in output.rglob("*") if path.is_file()
            )
            for marker in OLD_MARKERS:
                self.assertNotIn(marker.encode("utf-8"), combined)
            relative_names = {str(path.relative_to(output)) for path in output.rglob("*")}
            self.assertFalse(any("age" in name.lower() for name in relative_names))
            self.assertFalse(any("approval" in name.lower() for name in relative_names))
            self.assertFalse(any("session.json" in name.lower() for name in relative_names))

    def test_every_campaign_secret_and_private_key_is_fresh(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            first, _first_inventory = self._generate(root, suffix="first")
            second, _second_inventory = self._generate(root, suffix="second")

            for relative in (
                "secrets/staging-dr-ca.key",
                "secrets/bot-fi-dr.key",
                "secrets/webapp-fi-dr.key",
                "secrets/webapp-ir-dr.key",
                "secrets/witness-dr.key",
                "secrets/witness-ed25519-private.key",
                "secrets/staging-dr-blob-keyring.json",
            ):
                with self.subTest(relative=relative):
                    self.assertNotEqual(
                        (first / relative).read_bytes(),
                        (second / relative).read_bytes(),
                    )

            def secret_values(material: Path) -> set[str]:
                role_values = {
                    role: parse_env_values(
                        (material / "roles" / f"{role}.env").read_text(
                            encoding="utf-8"
                        )
                    )
                    for role in ("bot-fi", "webapp-fi", "webapp-ir", "witness")
                }
                values = {
                    value
                    for env in role_values.values()
                    for name, value in env.items()
                    if name in SECRET_ENV_NAMES
                }
                for env in role_values.values():
                    for name, raw in env.items():
                        if name.endswith("_DR_PAIRWISE_KEYS_JSON"):
                            values.update(
                                str(item["secret"]) for item in json.loads(raw)
                            )
                return values

            first_values = secret_values(first)
            second_values = secret_values(second)
            self.assertTrue(first_values)
            self.assertTrue(second_values)
            self.assertFalse(first_values & second_values)
            self.assertNotIn(PRIMARY_TOKEN, first_values)
            self.assertNotIn(EDITOR_TOKEN, first_values)

    def test_expected_directory_and_file_modes_are_closed(self):
        with tempfile.TemporaryDirectory() as raw_root:
            output, _inventory_payload = self._generate(Path(raw_root), suffix="modes")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((output / "roles").stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((output / "secrets").stat().st_mode), 0o700
            )
            for path in output.rglob("*"):
                if path.is_dir():
                    continue
                relative = str(path.relative_to(output))
                expected = (
                    0o640
                    if relative.endswith(".compose.yml")
                    else 0o644
                    if relative.endswith(".crt")
                    else 0o600
                )
                self.assertEqual(
                    stat.S_IMODE(path.stat().st_mode),
                    expected,
                    relative,
                )

    def test_shared_and_dedicated_inventory_modes_are_exactly_enforced(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            provider, s3, inventory_path, _inventory_payload = self._inputs(
                root,
                execution_class=SHARED_HOST_SAFE,
                suffix="shared",
            )
            output = root / "shared.material"
            generate_fresh_private_material(
                provider_environment=provider,
                provider_s3=s3,
                planned_inventory=inventory_path,
                output=output,
                execution_class=SHARED_HOST_SAFE,
                telegram_reader=self._telegram(),
            )
            manifest = json.loads(
                (output / "material-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["execution_class"], SHARED_HOST_SAFE)
            with self.assertRaises(FreshPrivateMaterialError):
                generate_fresh_private_material(
                    provider_environment=provider,
                    provider_s3=s3,
                    planned_inventory=inventory_path,
                    output=root / "wrong.material",
                    execution_class=DEDICATED_HOST_DESTRUCTIVE,
                    telegram_reader=self._telegram(),
                )

    def test_strict_private_inputs_reject_modes_links_and_duplicate_json(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            provider, s3, inventory_path, inventory = self._inputs(root)

            provider.chmod(0o644)
            with self.assertRaises(FreshPrivateMaterialError):
                generate_fresh_private_material(
                    provider_environment=provider,
                    provider_s3=s3,
                    planned_inventory=inventory_path,
                    output=root / "broad.material",
                    execution_class=DEDICATED_HOST_DESTRUCTIVE,
                    telegram_reader=self._telegram(),
                )
            provider.chmod(0o600)

            link = root / "provider-link.env"
            os.symlink(provider, link)
            with self.assertRaises(FreshPrivateMaterialError):
                generate_fresh_private_material(
                    provider_environment=link,
                    provider_s3=s3,
                    planned_inventory=inventory_path,
                    output=root / "link.material",
                    execution_class=DEDICATED_HOST_DESTRUCTIVE,
                    telegram_reader=self._telegram(),
                )

            duplicate_s3 = root / "duplicate-s3.json"
            duplicate_s3.write_text(
                '{"access_key":"first-key","access_key":"second-key",'
                f'"secret_key":"{"s" * 48}"}}',
                encoding="utf-8",
            )
            duplicate_s3.chmod(0o600)
            with self.assertRaisesRegex(
                FreshPrivateMaterialError, "not strict JSON"
            ):
                generate_fresh_private_material(
                    provider_environment=provider,
                    provider_s3=duplicate_s3,
                    planned_inventory=inventory_path,
                    output=root / "duplicate-s3.material",
                    execution_class=DEDICATED_HOST_DESTRUCTIVE,
                    telegram_reader=self._telegram(),
                )

            raw_inventory = _canonical_bytes(inventory).decode("utf-8")
            duplicate_inventory = root / "duplicate-inventory.json"
            duplicate_inventory.write_text(
                raw_inventory.replace(
                    '{"campaign_id":',
                    (
                        '{"schema":"three-site-staging-inventory-v3",'
                        '"campaign_id":'
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            duplicate_inventory.chmod(0o600)
            with self.assertRaisesRegex(
                FreshPrivateMaterialError, "not strict JSON"
            ):
                generate_fresh_private_material(
                    provider_environment=provider,
                    provider_s3=s3,
                    planned_inventory=duplicate_inventory,
                    output=root / "duplicate-inventory.material",
                    execution_class=DEDICATED_HOST_DESTRUCTIVE,
                    telegram_reader=self._telegram(),
                )

    def test_no_overwrite_and_mid_generation_failure_leave_no_partial_output(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            provider, s3, inventory_path, _inventory_payload = self._inputs(root)
            existing = root / "existing.material"
            existing.mkdir(mode=0o700)
            sentinel = existing / "sentinel"
            sentinel.write_text("preserve", encoding="utf-8")
            sentinel.chmod(0o600)
            with self.assertRaisesRegex(FreshPrivateMaterialError, "unavailable"):
                generate_fresh_private_material(
                    provider_environment=provider,
                    provider_s3=s3,
                    planned_inventory=inventory_path,
                    output=existing,
                    execution_class=DEDICATED_HOST_DESTRUCTIVE,
                    telegram_reader=self._telegram(),
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

            def fail_after_one_write(*, now, write):  # noqa: ANN001
                del now
                write("secrets/partial.key", b"partial-secret\n", 0o600)
                raise FreshPrivateMaterialError("injected generation failure")

            output = root / "failed.material"
            with patch(
                "scripts.generate_fresh_three_site_staging_private_material."
                "_certificate_material",
                side_effect=fail_after_one_write,
            ):
                with self.assertRaisesRegex(
                    FreshPrivateMaterialError, "injected generation"
                ):
                    generate_fresh_private_material(
                        provider_environment=provider,
                        provider_s3=s3,
                        planned_inventory=inventory_path,
                        output=output,
                        execution_class=DEDICATED_HOST_DESTRUCTIVE,
                        telegram_reader=self._telegram(),
                    )
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".failed.material.creating-*")), [])

    def test_default_telegram_transport_uses_get_only(self):
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return b'{"ok":true,"result":{"id":123,"username":"primary_bot"}}'

        def open_request(request, timeout):  # noqa: ANN001
            requests.append((request, timeout))
            return Response()

        with patch("urllib.request.urlopen", side_effect=open_request):
            result = telegram_read(PRIMARY_TOKEN, "getMe")
        self.assertEqual(result["id"], 123)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0].get_method(), "GET")
        self.assertIsNone(requests[0][0].data)
        with self.assertRaisesRegex(FreshPrivateMaterialError, "allowlisted"):
            telegram_read(PRIMARY_TOKEN, "sendMessage", {"text": "forbidden"})

    def test_manifest_verifier_rejects_a_tampered_role_file(self):
        with tempfile.TemporaryDirectory() as raw_root:
            output, _inventory = self._generate(Path(raw_root), suffix="tampered")
            path = output / "roles/webapp-fi.env"
            path.write_text(path.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(FreshPrivateMaterialError, "digest differs"):
                verify_fresh_private_material_manifest(output)

    def test_role_package_contains_only_the_verified_role_closure(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            material, inventory = self._generate(root, suffix="role-package")
            control_paths = []
            for name in (
                "planned-inventory.json",
                "planned-inventory-approval.json",
                "human-approval-policy.json",
            ):
                path = root / name
                path.write_text(
                    json.dumps(
                        {
                            "campaign_id": inventory["campaign_id"],
                            "deployment_id": inventory["deployment_id"],
                            "release_sha": inventory["release_sha"],
                        }
                    ),
                    encoding="utf-8",
                )
                path.chmod(0o600)
                control_paths.append(path)
            output = root / "role-package"
            exact = SimpleNamespace(recheck=lambda: None)
            with patch(
                "scripts.build_fresh_three_site_staging_role_package."
                "prove_exact_git_release",
                return_value=exact,
            ):
                result = build_role_package(
                    material_root=material,
                    planned_inventory=control_paths[0],
                    approval=control_paths[1],
                    approval_policy=control_paths[2],
                    role="bot-fi",
                    output=output,
                )
            self.assertEqual(result["status"], "fresh-role-package-created")
            with tarfile.open(output / "role-package.tar", "r") as archive:
                names = set(archive.getnames())
            manifest = verify_fresh_private_material_manifest(material)
            self.assertEqual(
                names,
                set(manifest["role_files"]["bot-fi"])
                | {
                    "planned-inventory.json",
                    "planned-inventory-approval.json",
                    "human-approval-policy.json",
                    "role-package-manifest.json",
                },
            )
            self.assertNotIn("secrets/staging-dr-ca.key", names)
            self.assertNotIn("secrets/staging-dr-blob-s3.json", names)

    def test_real_role_package_is_accepted_by_the_generic_agent(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            material, inventory = self._generate(root, suffix="agent-package")
            controls = []
            for name in ("inventory", "approval", "policy"):
                path = root / f"{name}.json"
                path.write_text(
                    json.dumps(
                        {
                            "campaign_id": inventory["campaign_id"],
                            "deployment_id": inventory["deployment_id"],
                            "release_sha": inventory["release_sha"],
                        }
                    ),
                    encoding="utf-8",
                )
                path.chmod(0o600)
                controls.append(path)
            package_output = root / "package-output"
            with patch(
                "scripts.build_fresh_three_site_staging_role_package."
                "prove_exact_git_release",
                return_value=SimpleNamespace(recheck=lambda: None),
            ):
                build_role_package(
                    material_root=material,
                    planned_inventory=controls[0],
                    approval=controls[1],
                    approval_policy=controls[2],
                    role="bot-fi",
                    output=package_output,
                )
            secure_root = root / "bootstrap"
            secure = (
                secure_root / inventory["campaign_id"] / inventory["deployment_id"] / "bot-fi"
            )
            secure.mkdir(mode=0o700, parents=True)
            identity = secure / "bootstrap.agekey"
            identity.write_text("AGE-SECRET-KEY-1TEST\n", encoding="utf-8")
            identity.chmod(0o600)
            agent_manifest = {
                "role": "bot-fi", "campaign_id": inventory["campaign_id"],
                "deployment_id": inventory["deployment_id"], "release_sha": inventory["release_sha"],
                "secure_dir": str(secure), "age_identity": str(identity),
            }
            with patch(
                "scripts.fresh_three_site_staging_preflight_agent."
                "_require_root_private_ancestors"
            ):
                installed, _package_manifest = _install_role_package(
                    package_output / "role-package.tar", manifest=agent_manifest
                )
            self.assertEqual(installed, secure)
            self.assertTrue((secure / "roles/bot-fi.env").is_file())
            self.assertFalse((secure / "secrets/staging-dr-blob-s3.json").exists())

    def test_cli_output_never_prints_provider_or_generated_secrets(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            provider, s3, inventory_path, _inventory_payload = self._inputs(root)
            output = root / "cli.material"
            stdout = io.StringIO()
            with patch(
                "scripts.generate_fresh_three_site_staging_private_material."
                "telegram_read",
                side_effect=self._telegram(),
            ), contextlib.redirect_stdout(stdout):
                status = main(
                    [
                        "--provider-environment",
                        str(provider),
                        "--provider-s3",
                        str(s3),
                        "--planned-inventory",
                        str(inventory_path),
                        "--execution-class",
                        DEDICATED_HOST_DESTRUCTIVE,
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(status, 0)
            report = stdout.getvalue()
            self.assertNotIn(PRIMARY_TOKEN, report)
            self.assertNotIn(EDITOR_TOKEN, report)
            self.assertNotIn("fresh-test-access", report)
            self.assertNotIn("s" * 48, report)
            for marker in OLD_MARKERS:
                self.assertNotIn(marker, report)
            self.assertFalse(json.loads(report)["secret_values_printed"])


if __name__ == "__main__":
    unittest.main()
