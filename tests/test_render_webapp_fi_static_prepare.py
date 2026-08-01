"""Focused tests for the controller-only FI static-preparation renderer."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_webapp_fi_static_prepare.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


renderer = _load("render_webapp_fi_static_prepare_test", SCRIPT)
fixtures = _load(
    "source_stage_fixture_helpers_for_static_prepare_test",
    ROOT / "tests" / "source_stage_fixture_helpers.py",
)

CAMPAIGN = "static-prepare-control-20260730"
REVISION = "f2c7d8e9a0b1"
RECIPIENTS = {
    "controller": "age1pppppppppppppppppppppppppppppppppppppppp",
    "fi": "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    "ir": "age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr",
}


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _write_private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _commit(repository: Path) -> str:
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")
    return subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()


@unittest.skipUnless(os.geteuid() == 0, "controller controls enforce root-only inputs")
class StaticPreparationRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="fi-static-prepare-render-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.fi_root = self.root / "fi-bootstrap"
        self.fi_root.mkdir(mode=0o700)

        self.control_repo = self.root / "control"
        self.application_repo = self.root / "application"
        for repository in (self.control_repo, self.application_repo):
            repository.mkdir(mode=0o700)
            _git(repository, "init")
            _git(repository, "config", "user.email", "fixture@example.invalid")
            _git(repository, "config", "user.name", "FI Static Prepare Fixture")
        for relative in renderer.initial.preparer.SOURCE_PAYLOAD_FILES:
            target = self.control_repo / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        self.control_commit = _commit(self.control_repo)
        self.control_tree = subprocess.check_output(
            ["git", "-C", str(self.control_repo), "rev-parse", self.control_commit + "^{tree}"], text=True
        ).strip()
        (self.application_repo / "main.py").write_text("print('fixture')\n", encoding="ascii")
        (self.application_repo / "mini_app_dist").mkdir(mode=0o700)
        (self.application_repo / "mini_app_dist" / "index.html").write_text("<!doctype html>fixture\n", encoding="ascii")
        self.release = _commit(self.application_repo)
        self.release_tree = subprocess.check_output(
            ["git", "-C", str(self.application_repo), "rev-parse", self.release + "^{tree}"], text=True
        ).strip()

        campaign = self.root / "campaigns" / CAMPAIGN
        source_phase = campaign / "webapp-fi-source"
        source_phase.mkdir(mode=0o700, parents=True)
        os.chmod(campaign.parent, 0o700)
        os.chmod(campaign, 0o700)
        binding_unsigned = renderer.initial.transport.campaign_binding.build_campaign_binding(
            campaign_id=CAMPAIGN,
            application_release_sha=self.release,
            application_release_tree=self.release_tree,
            expected_alembic_revision=REVISION,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
        )
        self.binding = _write_private(
            source_phase / "campaign-binding.json",
            renderer.initial.transport.campaign_binding.canonical_json_bytes(binding_unsigned) + b"\n",
        )
        self.expected_static_assets_manifest = fixtures.make_expected_static_assets_manifest(
            root=self.root,
            campaign_id=CAMPAIGN,
            application_repository=self.application_repo,
            application_release_sha=self.release,
            expected_alembic_revision=REVISION,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
        )
        role_binding = renderer.role_config.binding.load_campaign_binding(self.binding)
        role_value = renderer.role_config.build_source_role_config(
            campaign_binding=role_binding,
            application_container="trading_bot_app",
            sync_worker_container="trading_bot_sync_worker",
        )
        self.role_config = _write_private(source_phase / "source-role-config.json", _canonical(role_value))

        inputs = self.root / "controller-inputs"
        inputs.mkdir(mode=0o700)
        credentials = fixtures.make_trusted_e53_s3_environment(self.root)
        self.controller_campaigns_root = self.root / "controller-campaigns"
        self.source_transport_workspace_root = self.root / "source-transport-workspaces"
        controller_directory = self.controller_campaigns_root / CAMPAIGN / "controller"
        controller_directory.mkdir(mode=0o700, parents=True)
        for directory in (self.controller_campaigns_root, controller_directory.parent, controller_directory):
            directory.chmod(0o700)
        self.transport_config = _write_private(
            controller_directory / "source-transport.json",
            _canonical(
                {
                    "schema": renderer.initial.transport.CONFIG_SCHEMA,
                    "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
                    "bucket": "three-site-private",
                    "prefix": "campaign-current/artifacts",
                    "credentials_file": str(credentials),
                    "controller_age_recipient": RECIPIENTS["controller"],
                    "webapp_fi_age_recipient": RECIPIENTS["fi"],
                    "webapp_ir_age_recipient": RECIPIENTS["ir"],
                    "presign_expires_seconds": 300,
                }
            ),
        )
        self.initial_object_id = "initial-static-20260730"
        self.package = self.root / "packages" / "source-package"
        self.package.parent.mkdir(mode=0o700)
        controller_transport = renderer.initial.preparer._load_controller_source_transport()
        transport_patches = (
            mock.patch.object(controller_transport, "CAMPAIGNS_ROOT", self.controller_campaigns_root),
            mock.patch.object(
                controller_transport.contract,
                "SOURCE_TRANSPORT_WORKSPACE_ROOT",
                self.source_transport_workspace_root,
            ),
            mock.patch.object(
                controller_transport,
                "TRUSTED_E53_S3_ENVIRONMENT_PATH",
                credentials,
            ),
            mock.patch.object(
                renderer.initial.preparer,
                "_load_controller_source_transport",
                return_value=controller_transport,
            ),
            mock.patch.object(renderer.initial.transport, "CAMPAIGNS_ROOT", self.controller_campaigns_root),
            mock.patch.object(
                renderer.initial.transport.contract,
                "SOURCE_TRANSPORT_WORKSPACE_ROOT",
                self.source_transport_workspace_root,
            ),
            mock.patch.object(renderer.initial.transport, "TRUSTED_E53_S3_ENVIRONMENT_PATH", credentials),
        )
        self.bootstrap_patch = mock.patch.object(renderer.initial, "FI_BOOTSTRAP_ROOT", self.fi_root)
        self.bootstrap_patch.start()
        self.fi_workspace_patch = mock.patch.object(
            renderer.initial.preparer,
            "INITIAL_STATIC_FI_WORKSPACE",
            str(self.fi_root),
        )
        self.fi_workspace_patch.start()
        for patcher in transport_patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.bootstrap_patch.stop)
        self.addCleanup(self.fi_workspace_patch.stop)
        renderer.initial.preparer.prepare_source_adoption_package(
            source_repository=self.control_repo,
            application_source_repository=self.application_repo,
            expected_static_assets_manifest=self.expected_static_assets_manifest,
            control_commit=self.control_commit,
            application_release_sha=self.release,
            expected_alembic_revision=REVISION,
            source_transport_config=self.transport_config,
            campaign_binding_path=self.binding,
            initial_static_object_id=self.initial_object_id,
            package_id="source-package",
            destination=self.package,
            apply=True,
        )
        self.preparation = self.package / renderer.initial.PREPARATION_RECEIPT_NAME
        prepared = renderer.initial.preparer.verify_prepared_source_adoption_package(
            package_directory=self.package,
            preparation_receipt=self.preparation,
            expected_control_commit=self.control_commit,
            expected_application_release_sha=self.release,
        )
        members = renderer.initial.preparer._read_archive_members(
            self.package / renderer.initial.PACKAGE_ARCHIVE_NAME
        )
        files = {
            relative: renderer.initial.preparer.sha256_bytes(members[relative])
            for relative in renderer.initial.preparer.PACKAGE_PAYLOAD_FILES
        }
        candidate = self.fi_root / ("installed-" + self.control_commit + "-source-package")
        install_unsigned: dict[str, object] = {
            "schema": renderer.initial.SOURCE_ADOPTION_INSTALL_RECEIPT_SCHEMA,
            "status": "installed",
            "installed_at": "2026-07-30T12:00:00Z",
            "candidate_directory": str(candidate),
            "source_site": "bot_fi",
            "destination_site": "webapp_fi",
            "campaign_id": CAMPAIGN,
            "package_id": "source-package",
            "application": prepared["application"],
            "tooling": prepared["tooling"],
            "files": files,
            "canonical_release_tree_sha256": prepared["canonical_release_tree_sha256"],
            "package": {
                "archive_sha256": prepared["archive_sha256"],
                "archive_bytes": prepared["archive_bytes"],
                "preparation_receipt_sha256": prepared["preparation_receipt_sha256"],
                "delivery_receipt_sha256": "a" * 64,
                "delivery_envelope_sha256": "b" * 64,
                "controller_public_key_base64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                "fi_bootstrap_recipient": RECIPIENTS["fi"],
                "object_key": "source-adoption/source-package.age",
                "version_id": "version-fixture-001",
                "ciphertext_sha256": "c" * 64,
                "ciphertext_bytes": 1024,
            },
        }
        install = {
            **install_unsigned,
            "receipt_sha256": renderer.initial.sha256_bytes(renderer.initial.canonical_json_bytes(install_unsigned)),
        }
        self.install_receipt = _write_private(inputs / "fi-install.json", _canonical(install))
        self.known_hosts = inputs / "fi-known_hosts"
        self.known_hosts.write_text(
            "65.109.220.59 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixtureKnownHostKey\n",
            encoding="ascii",
        )
        self.known_hosts.chmod(0o644)
        self.control = self._build()

    def _build(self, *, static_output_id: str | None = None):
        return renderer.build_static_preparation_control(
            source_transport_config=self.transport_config,
            campaign_binding=self.binding,
            source_adoption_package_directory=self.package,
            preparation_receipt=self.preparation,
            fi_install_receipt=self.install_receipt,
            source_role_config=self.role_config,
            static_output_id=self.initial_object_id if static_output_id is None else static_output_id,
        )

    def _receipt(self) -> Path:
        output = self.control.static_output_directory
        files_sha = self.control.initial_control.expected_static_files_sha256
        file_count = self.control.initial_control.expected_static_file_count
        archive = {"name": renderer.static_preparer.STATIC_ARCHIVE_NAME, "sha256": "a" * 64, "bytes": 2048}
        capacity = {
            "archive_upper_bound_bytes": 2560,
            "file_manifest_bytes": 512,
            "source_bytes": 1024,
            "file_count": file_count,
            "receipt_reserve_bytes": renderer.static_preparer.RECEIPT_RESERVE_BYTES,
            "margin_bytes": renderer.static_preparer.CAPACITY_MARGIN_BYTES,
            "required_free_bytes": 2560 + 512 + renderer.static_preparer.RECEIPT_RESERVE_BYTES + renderer.static_preparer.CAPACITY_MARGIN_BYTES,
            "available_free_bytes": 20 * 1024 * 1024,
        }
        common = {
            "object_storage_action": False,
            "age_action": False,
            "ssh_action": False,
            "docker_action": False,
            "service_changed": False,
        }
        value: dict[str, object] = {
            "status": "prepared",
            "campaign_id": CAMPAIGN,
            "application": {"release_sha": self.release, "expected_alembic_revision": REVISION},
            "source_site": "webapp_fi",
            "runtime_source_root": str(renderer.FI_RUNTIME_SOURCE_ROOT),
            "static_source_root": str(Path(str(renderer.FI_RUNTIME_SOURCE_ROOT)) / "mini_app_dist"),
            "output_directory": str(output),
            "archive_name": renderer.static_preparer.STATIC_ARCHIVE_NAME,
            "files_sha256": files_sha,
            "file_count": file_count,
            "capacity_preflight": capacity,
            **common,
            "archive": archive,
            "file_manifest_path": str(output / renderer.static_preparer.STATIC_FILE_MANIFEST_NAME),
            "preparation_receipt_path": str(output / renderer.static_preparer.STATIC_PREPARATION_RECEIPT_NAME),
            "file_manifest_sha256": "c" * 64,
            "preparation_receipt_sha256": "d" * 64,
            "verification": {
                "status": "verified",
                "output_directory": str(output),
                "archive": archive,
                "files_sha256": files_sha,
                "file_count": file_count,
                "file_manifest_sha256": "c" * 64,
                "preparation_receipt_sha256": "d" * 64,
                **common,
            },
        }
        return _write_private(self.root / "controller-output" / "static-preparation.json", _canonical(value))

    def test_render_is_fixed_pinned_ssh_with_no_operator_remote_paths_or_payload(self) -> None:
        with mock.patch("subprocess.run", side_effect=AssertionError("renderer must not execute SSH")):
            command = renderer.render_prepare_command(control=self.control, fi_known_hosts=self.known_hosts)
        outer = shlex.split(command)
        self.assertEqual("ssh", outer[0])
        self.assertIn("StrictHostKeyChecking=yes", outer)
        self.assertIn("UserKnownHostsFile=" + str(self.known_hosts), outer)
        self.assertEqual(renderer.initial.REMOTE_HOST, outer[-2])
        remote = shlex.split(outer[-1])
        self.assertEqual(
            ["/usr/bin/python3", "-I", "-B", str(self.control.initial_control.candidate_directory / renderer.STATIC_PREPARER_MEMBER)],
            remote[:4],
        )
        self.assertEqual(str(renderer.FI_RUNTIME_SOURCE_ROOT), remote[remote.index("--runtime-source-root") + 1])
        self.assertEqual(str(self.control.static_output_directory), remote[remote.index("--output-directory") + 1])
        self.assertEqual(self.release, remote[remote.index("--release-sha") + 1])
        self.assertIn("--apply", remote)
        self.assertNotIn("fixture", command.lower())
        self.assertNotIn("://", command)
        self.assertNotIn("credentials", command.lower())

    def test_wrong_output_id_or_role_binding_blocks_before_render(self) -> None:
        with self.assertRaisesRegex(renderer.StaticPreparationControlError, "package-bound initial static object"):
            self._build(static_output_id="other-static-output")
        role = json.loads(self.role_config.read_text(encoding="ascii"))
        role["campaign_binding_sha256"] = "0" * 64
        _write_private(self.role_config, _canonical(role))
        with self.assertRaisesRegex(renderer.StaticPreparationControlError, "source role config"):
            self._build()

    def test_validates_only_url_free_nonsecret_fixed_receipt(self) -> None:
        receipt = self._receipt()
        verified = renderer.validate_preparation_receipt(control=self.control, receipt=receipt)
        self.assertEqual("verified", verified["status"])
        self.assertEqual(self.initial_object_id, verified["static_output_id"])
        self.assertEqual("a" * 64, verified["archive"]["sha256"])

        value = json.loads(receipt.read_text(encoding="ascii"))
        value["static_source_root"] = "https://example.invalid/mini_app_dist"
        _write_private(receipt, _canonical(value))
        with self.assertRaisesRegex(renderer.StaticPreparationControlError, "URL-free and nonsecret"):
            renderer.validate_preparation_receipt(control=self.control, receipt=receipt)

    def test_receipt_must_match_controller_bound_expected_static_manifest(self) -> None:
        receipt = self._receipt()
        value = json.loads(receipt.read_text(encoding="ascii"))
        value["files_sha256"] = "0" * 64
        value["verification"]["files_sha256"] = "0" * 64
        _write_private(receipt, _canonical(value))
        with self.assertRaisesRegex(
            renderer.StaticPreparationControlError,
            "does not match the controller-bound expected static manifest",
        ):
            renderer.validate_preparation_receipt(control=self.control, receipt=receipt)

    def test_parser_has_no_execute_action(self) -> None:
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT)]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual({"render", "verify-receipt"}, set(renderer._parser()._subparsers._group_actions[0].choices))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
