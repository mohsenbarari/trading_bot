"""Focused tests for controller-local FI source-role config rendering."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


renderer = _load(
    "test_render_webapp_fi_source_role_config",
    "render_webapp_fi_source_role_config.py",
)
installer = _load(
    "install_webapp_fi_source_adoption_for_source_role_config_test",
    "install_webapp_fi_source_adoption.py",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


class RenderWebAppFiSourceRoleConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="webapp-fi-source-role-config-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.campaigns_root = self.root / "campaigns"
        self.campaigns_root.mkdir(mode=0o700)
        self.campaigns_root.chmod(0o700)
        self.campaign_id = "source-role-config-20260730"
        self.campaign_directory = self.campaigns_root / self.campaign_id
        self.source_phase = self.campaign_directory / renderer.SOURCE_PHASE_DIRECTORY
        self.source_phase.mkdir(mode=0o700, parents=True)
        self.campaign_directory.chmod(0o700)
        self.source_phase.chmod(0o700)
        value = renderer.binding.build_campaign_binding(
            campaign_id=self.campaign_id,
            application_release_sha="a" * 40,
            application_release_tree="b" * 40,
            expected_alembic_revision="c" * 12,
            control_commit="d" * 40,
            control_tree="e" * 40,
        )
        self.binding_path = _private(
            self.source_phase / renderer.CAMPAIGN_BINDING_FILENAME,
            _canonical(value),
        )
        self.binding = renderer.binding.load_campaign_binding(self.binding_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def role_path(self) -> Path:
        return self.source_phase / renderer.SOURCE_ROLE_CONFIG_FILENAME

    def _render(self, *, apply: bool = False, application: str = "trading_bot_app", sync: str = "trading_bot_sync_worker"):
        return renderer.render_source_role_config(
            campaign_binding_path=self.binding_path,
            application_container=application,
            sync_worker_container=sync,
            campaigns_root=self.campaigns_root,
            apply=apply,
        )

    def test_read_only_preflight_derives_all_immutable_identity_without_writing(self) -> None:
        result = self._render()

        self.assertEqual("planned", result["status"])
        self.assertEqual(str(self.role_path), result["role_config_path"])
        self.assertFalse(self.role_path.exists())
        self.assertEqual(self.campaign_id, result["campaign_id"])
        self.assertEqual(self.binding.binding_sha256, result["campaign_binding_sha256"])
        self.assertEqual(
            {
                "release_sha": "a" * 40,
                "release_tree": "b" * 40,
                "expected_alembic_revision": "c" * 12,
            },
            result["application"],
        )
        self.assertEqual({"control_commit": "d" * 40, "control_tree": "e" * 40}, result["tooling"])
        self.assertEqual(
            {"application": "trading_bot_app", "sync_worker": "trading_bot_sync_worker"},
            result["runtime_containers"],
        )

    def test_apply_creates_one_root_only_canonical_config_then_loader_revalidates_it(self) -> None:
        result = self._render(apply=True)
        payload = self.role_path.read_bytes()
        value = json.loads(payload)

        self.assertEqual("rendered", result["status"])
        self.assertEqual(payload, _canonical(value))
        self.assertEqual(0o600, stat.S_IMODE(self.role_path.stat().st_mode))
        self.assertEqual(renderer.SOURCE_ROLE_CONFIG_SCHEMA, value["schema"])
        self.assertEqual(self.campaign_id, value["campaign_id"])
        self.assertEqual(self.binding.binding_sha256, value["campaign_binding_sha256"])
        self.assertEqual(
            renderer.expected_source_signing_key_path(self.campaign_id),
            value["source_signing_private_key_file"],
        )
        self.assertEqual(
            value,
            renderer.load_source_role_config(path=self.role_path, campaign_binding=self.binding),
        )

    def test_existing_output_is_never_reused_or_overwritten_in_plan_or_apply(self) -> None:
        original = _private(self.role_path, b"retained forensic role config\n")
        before = original.read_bytes()

        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "already exists and will not be reused"):
            self._render()
        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "already exists and will not be reused"):
            self._render(apply=True)
        self.assertEqual(before, original.read_bytes())

    def test_validator_rejects_binding_control_and_runtime_drift(self) -> None:
        payload = _canonical(
            renderer.build_source_role_config(
                campaign_binding=self.binding,
                application_container="trading_bot_app",
                sync_worker_container="trading_bot_sync_worker",
            )
        )
        value = json.loads(payload)

        value["tooling"]["control_tree"] = "f" * 40
        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "tooling does not match binding"):
            renderer.validate_source_role_config_payload(payload=_canonical(value), campaign_binding=self.binding)

        value = json.loads(payload)
        value["application"]["release_tree"] = "f" * 40
        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "application does not match binding"):
            renderer.validate_source_role_config_payload(payload=_canonical(value), campaign_binding=self.binding)

        value = json.loads(payload)
        value["campaign_binding_sha256"] = "0" * 64
        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "binding does not match campaign"):
            renderer.validate_source_role_config_payload(payload=_canonical(value), campaign_binding=self.binding)

        value = json.loads(payload)
        value["sync_worker_container"] = value["application_container"]
        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "must be distinct"):
            renderer.validate_source_role_config_payload(payload=_canonical(value), campaign_binding=self.binding)

    def test_explicit_runtime_fields_reject_placeholder_and_ambient_like_values(self) -> None:
        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "application_container is invalid"):
            self._render(application="${APP_CONTAINER}")
        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "sync_worker_container is invalid"):
            self._render(sync="trading bot sync")
        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "must be distinct"):
            self._render(application="same", sync="same")
        parser = renderer._parser()
        parsed = parser.parse_args(
            [
                "render",
                "--campaign-binding",
                str(self.binding_path),
                "--application-container",
                "fixture-app",
                "--sync-worker-container",
                "fixture-sync",
                "--campaigns-root",
                str(self.campaigns_root),
            ]
        )
        self.assertFalse(parsed.apply)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "render",
                    "--campaign-binding",
                    str(self.binding_path),
                    "--role-example",
                    "deploy/production/webapp-fi-source-role.json.example",
                ]
            )

    def test_fixed_layout_rejects_an_equivalent_binding_outside_supplied_campaign_root(self) -> None:
        alternate_root = self.root / "alternate"
        alternate_root.mkdir(mode=0o700)
        alternate_root.chmod(0o700)
        alternate_phase = alternate_root / self.campaign_id / renderer.SOURCE_PHASE_DIRECTORY
        alternate_phase.mkdir(mode=0o700, parents=True)
        (alternate_root / self.campaign_id).chmod(0o700)
        alternate_phase.chmod(0o700)
        alternate_binding = _private(
            alternate_phase / renderer.CAMPAIGN_BINDING_FILENAME,
            self.binding_path.read_bytes(),
        )

        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "fixed controller campaign path"):
            renderer.render_source_role_config(
                campaign_binding_path=alternate_binding,
                application_container="trading_bot_app",
                sync_worker_container="trading_bot_sync_worker",
                campaigns_root=self.campaigns_root,
            )
        self.assertFalse(self.role_path.exists())

    def test_loader_rejects_url_and_noncanonical_payloads_before_returning_config(self) -> None:
        value = renderer.build_source_role_config(
            campaign_binding=self.binding,
            application_container="trading_bot_app",
            sync_worker_container="trading_bot_sync_worker",
        )
        value["application_container"] = "https://capability.invalid"
        _private(self.role_path, _canonical(value))
        with self.assertRaisesRegex(renderer.SourceRoleConfigError, "forbidden URL"):
            renderer.load_source_role_config(path=self.role_path, campaign_binding=self.binding)

    def test_fi_consumer_accepts_only_the_campaign_bound_v3_shape(self) -> None:
        value = renderer.build_source_role_config(
            campaign_binding=self.binding,
            application_container="trading_bot_app",
            sync_worker_container="trading_bot_sync_worker",
        )
        _private(self.role_path, _canonical(value))
        signer = _private(self.root / "fi-key" / "source-signing-ed25519.raw", b"k" * 32)
        original_require = installer.require_root_only_file

        def require_with_fixture_key(path: Path, **kwargs):
            if Path(path) == Path(value["source_signing_private_key_file"]):
                return signer
            return original_require(path, **kwargs)

        with mock.patch.object(installer, "require_root_only_file", side_effect=require_with_fixture_key):
            loaded = installer.load_source_role_config(
                self.role_path,
                expected_application={
                    "release_sha": self.binding.application_release_sha,
                    "expected_alembic_revision": self.binding.expected_alembic_revision,
                },
            )
        self.assertEqual(self.campaign_id, loaded["campaign_binding"]["campaign_id"])
        self.assertEqual(self.binding.binding_sha256, loaded["campaign_binding"]["campaign_binding_sha256"])
        self.assertEqual(self.binding.control_commit, loaded["campaign_binding"]["control_commit"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
