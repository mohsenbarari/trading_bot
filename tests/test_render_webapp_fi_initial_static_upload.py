"""Focused tests for the controller-only first FI static exchange renderer."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_webapp_fi_initial_static_upload.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


renderer = _load("render_webapp_fi_initial_static_upload_test", SCRIPT)
fixtures = _load(
    "source_stage_fixture_helpers_for_initial_static_upload_test",
    ROOT / "tests" / "source_stage_fixture_helpers.py",
)


CAMPAIGN = "initial-static-control-20260730"
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
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def _commit(repository: Path) -> str:
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")
    return subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()


@unittest.skipUnless(os.geteuid() == 0, "controller controls enforce root-only inputs")
class InitialStaticRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="initial-static-render-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.fi_root = self.root / "fi-bootstrap"
        self.fi_root.mkdir(mode=0o700)

        self.control = self.root / "control"
        self.application = self.root / "application"
        for repository in (self.control, self.application):
            repository.mkdir(mode=0o700)
            _git(repository, "init")
            _git(repository, "config", "user.email", "fixture@example.invalid")
            _git(repository, "config", "user.name", "Initial Static Fixture")
        for relative in renderer.preparer.SOURCE_PAYLOAD_FILES:
            destination = self.control / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        self.control_commit = _commit(self.control)
        self.control_tree = subprocess.check_output(
            ["git", "-C", str(self.control), "rev-parse", self.control_commit + "^{tree}"], text=True
        ).strip()
        (self.application / "main.py").write_text("print('fixture')\n", encoding="ascii")
        (self.application / "mini_app_dist").mkdir(mode=0o700)
        (self.application / "mini_app_dist" / "index.html").write_text("<!doctype html>fixture\n", encoding="ascii")
        self.release = _commit(self.application)
        self.release_tree = subprocess.check_output(
            ["git", "-C", str(self.application), "rev-parse", self.release + "^{tree}"], text=True
        ).strip()

        campaign = self.root / "campaigns" / CAMPAIGN
        source_phase = campaign / "webapp-fi-source"
        source_phase.mkdir(mode=0o700, parents=True)
        os.chmod(campaign.parent, 0o700)
        os.chmod(campaign, 0o700)
        binding_unsigned = renderer.transport.campaign_binding.build_campaign_binding(
            campaign_id=CAMPAIGN,
            application_release_sha=self.release,
            application_release_tree=self.release_tree,
            expected_alembic_revision=REVISION,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
        )
        self.binding = _write_private(
            source_phase / "campaign-binding.json",
            renderer.transport.campaign_binding.canonical_json_bytes(binding_unsigned) + b"\n",
        )
        self.expected_static_assets_manifest = fixtures.make_expected_static_assets_manifest(
            root=self.root,
            campaign_id=CAMPAIGN,
            application_repository=self.application,
            application_release_sha=self.release,
            expected_alembic_revision=REVISION,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
        )
        inputs = self.root / "controller-inputs"
        inputs.mkdir(mode=0o700)
        credentials = fixtures.make_trusted_e53_s3_environment(self.root)
        self.controller_campaigns_root = self.root / "controller-campaigns"
        self.source_transport_workspace_root = self.root / "source-transport-workspaces"
        controller_directory = self.controller_campaigns_root / CAMPAIGN / "controller"
        controller_directory.mkdir(mode=0o700, parents=True)
        for directory in (self.controller_campaigns_root, controller_directory.parent, controller_directory):
            directory.chmod(0o700)
        self.config = _write_private(
            controller_directory / "source-transport.json",
            _canonical(
                {
                    "schema": renderer.transport.CONFIG_SCHEMA,
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

        self.package = self.root / "packages" / "initial-package"
        self.package.parent.mkdir(mode=0o700)
        self.initial_object_id = "initial-static-20260730"
        controller_transport = renderer.preparer._load_controller_source_transport()
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
            mock.patch.object(renderer.preparer, "_load_controller_source_transport", return_value=controller_transport),
            mock.patch.object(renderer.transport, "CAMPAIGNS_ROOT", self.controller_campaigns_root),
            mock.patch.object(
                renderer.transport.contract,
                "SOURCE_TRANSPORT_WORKSPACE_ROOT",
                self.source_transport_workspace_root,
            ),
            mock.patch.object(renderer.transport, "TRUSTED_E53_S3_ENVIRONMENT_PATH", credentials),
        )
        for patcher in transport_patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fi_workspace_patch = mock.patch.object(
            renderer.preparer,
            "INITIAL_STATIC_FI_WORKSPACE",
            str(self.fi_root),
        )
        self.fi_workspace_patch.start()
        self.addCleanup(self.fi_workspace_patch.stop)
        result = renderer.preparer.prepare_source_adoption_package(
            source_repository=self.control,
            application_source_repository=self.application,
            expected_static_assets_manifest=self.expected_static_assets_manifest,
            control_commit=self.control_commit,
            application_release_sha=self.release,
            expected_alembic_revision=REVISION,
            source_transport_config=self.config,
            campaign_binding_path=self.binding,
            initial_static_object_id=self.initial_object_id,
            package_id="initial-package",
            destination=self.package,
            apply=True,
        )
        self.preparation = self.package / renderer.PREPARATION_RECEIPT_NAME
        self.prepared = renderer.preparer.verify_prepared_source_adoption_package(
            package_directory=self.package,
            preparation_receipt=self.preparation,
            expected_control_commit=self.control_commit,
            expected_application_release_sha=self.release,
        )
        members = renderer.preparer._read_archive_members(self.package / renderer.PACKAGE_ARCHIVE_NAME)
        files = {
            relative: renderer.preparer.sha256_bytes(members[relative])
            for relative in renderer.preparer.PACKAGE_PAYLOAD_FILES
        }
        candidate = self.fi_root / ("installed-" + self.control_commit + "-initial-package")
        install_unsigned: dict[str, object] = {
            "schema": renderer.SOURCE_ADOPTION_INSTALL_RECEIPT_SCHEMA,
            "status": "installed",
            "installed_at": "2026-07-30T12:00:00Z",
            "candidate_directory": str(candidate),
            "source_site": "bot_fi",
            "destination_site": "webapp_fi",
            "campaign_id": CAMPAIGN,
            "package_id": "initial-package",
            "application": self.prepared["application"],
            "tooling": self.prepared["tooling"],
            "files": files,
            "canonical_release_tree_sha256": self.prepared["canonical_release_tree_sha256"],
            "package": {
                "archive_sha256": self.prepared["archive_sha256"],
                "archive_bytes": self.prepared["archive_bytes"],
                "preparation_receipt_sha256": self.prepared["preparation_receipt_sha256"],
                "delivery_receipt_sha256": "a" * 64,
                "delivery_envelope_sha256": "b" * 64,
                "controller_public_key_base64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                "fi_bootstrap_recipient": RECIPIENTS["fi"],
                "object_key": "source-adoption/initial-package.age",
                "version_id": "version-fixture-001",
                "ciphertext_sha256": "c" * 64,
                "ciphertext_bytes": 1024,
            },
        }
        install = {
            **install_unsigned,
            "receipt_sha256": renderer.sha256_bytes(renderer.canonical_json_bytes(install_unsigned)),
        }
        self.install_receipt = _write_private(inputs / "fi-install.json", _canonical(install))
        self.known_hosts = inputs / "fi-known_hosts"
        self.known_hosts.write_text(
            "65.109.220.59 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixtureKnownHostKey\n",
            encoding="ascii",
        )
        self.known_hosts.chmod(0o644)
        self.fi_root_patch = mock.patch.object(renderer, "FI_BOOTSTRAP_ROOT", self.fi_root)
        self.fi_root_patch.start()
        self.addCleanup(self.fi_root_patch.stop)
        self.control_value = renderer.build_initial_static_control(
            source_transport_config=self.config,
            campaign_binding=self.binding,
            source_adoption_package_directory=self.package,
            preparation_receipt=self.preparation,
            fi_install_receipt=self.install_receipt,
        )

    def _prepared_receipt(self, *, plaintext_sha: str = "d" * 64) -> Path:
        exchange_policy = renderer._exchange_policy_from_control(self.control_value.policy)
        exchange_request = renderer.exchange._request_from_value(
            renderer._request_value(self.control_value.request),
            policy=exchange_policy,
            field="fixture request",
        )
        value = renderer.exchange._build_prepared_receipt(
            request=exchange_request,
            policy=exchange_policy,
            recipients=(RECIPIENTS["controller"], RECIPIENTS["ir"]),
            plaintext={"sha256": plaintext_sha, "bytes": 1234},
            ciphertext={"sha256": "e" * 64, "bytes": 1300, "name": renderer.exchange.PREPARED_CIPHERTEXT_NAME},
        )
        return _write_private(self.root / "controller-output" / "prepared.json", _canonical(value))

    def _presigned_url(self) -> str:
        key = renderer.transport.source_object_key(self.control_value.policy, self.control_value.request)
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        signed_headers = ";".join(
            (
                "content-type",
                "host",
                "if-none-match",
                "x-amz-meta-ciphertext-sha256",
                "x-amz-meta-encryption",
                "x-amz-meta-recipient-mode",
                "x-amz-meta-transport-schema",
            )
        )
        return (
            "https://s3.ir-thr-at1.arvanstorage.ir/three-site-private/"
            + quote(key, safe="/")
            + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=fixture%2F"
            + now.strftime("%Y%m%d")
            + "%2Fir-thr-at1%2Fs3%2Faws4_request&X-Amz-Date="
            + now.strftime("%Y%m%dT%H%M%SZ")
            + "&X-Amz-Expires=300&X-Amz-SignedHeaders="
            + quote(signed_headers, safe="")
            + "&X-Amz-Signature="
            + "a" * 64
        )

    def test_render_prepare_is_exact_pinned_ssh_without_payload_or_credentials(self) -> None:
        with mock.patch("subprocess.run", side_effect=AssertionError("renderer must not execute SSH")):
            command = renderer.render_prepare_command(control=self.control_value, fi_known_hosts=self.known_hosts)
        outer = shlex.split(command)
        self.assertEqual("ssh", outer[0])
        self.assertIn("StrictHostKeyChecking=yes", outer)
        self.assertIn("GlobalKnownHostsFile=/dev/null", outer)
        self.assertIn("UserKnownHostsFile=" + str(self.known_hosts), outer)
        self.assertEqual(renderer.REMOTE_HOST, outer[-2])
        remote = shlex.split(outer[-1])
        self.assertEqual(
            ["/usr/bin/python3", "-I", "-B", str(self.control_value.candidate_directory / renderer.EXCHANGE_SCRIPT_MEMBER)],
            remote[:4],
        )
        self.assertEqual("prepare-upload", remote[4])
        plaintext_index = remote.index("--plaintext")
        self.assertEqual(str(self.control_value.static_archive), remote[plaintext_index + 1])
        self.assertEqual("mini_app_dist.tar", Path(remote[plaintext_index + 1]).name)
        self.assertIn(str(self.control_value.prepared_directory), remote)
        self.assertNotIn("fixture", command.lower())
        self.assertNotIn("://", command)

    def test_initial_static_policy_uses_precreated_fi_bootstrap_workspace_not_controller_workspace(self) -> None:
        self.assertEqual(CAMPAIGN, self.control_value.controller_config.campaign_id)
        controller_workspace = self.source_transport_workspace_root / CAMPAIGN
        self.assertFalse(controller_workspace.exists())
        self.assertEqual(self.fi_root, self.control_value.policy.workspace)
        self.assertNotEqual(controller_workspace, self.control_value.policy.workspace)

        members = renderer.preparer._read_archive_members(self.package / renderer.PACKAGE_ARCHIVE_NAME)
        policy = json.loads(members[renderer.INITIAL_STATIC_POLICY_MEMBER].decode("ascii"))
        self.assertEqual(str(self.fi_root), policy["workspace"])
        self.assertNotEqual(str(controller_workspace), policy["workspace"])

        fi_policy = _write_private(
            self.fi_root / "initial-static-transport-policy.json",
            renderer.canonical_json_bytes(policy) + b"\n",
        )
        loaded = renderer.exchange.load_policy(fi_policy)
        self.assertEqual(self.fi_root, loaded.workspace)

    def test_controller_workspace_policy_is_rejected_before_fi_install_receipt(self) -> None:
        controller_workspace = self.source_transport_workspace_root / CAMPAIGN
        self.assertFalse(controller_workspace.exists())
        leaked_package = self.root / "packages" / "controller-workspace-package"
        with mock.patch.object(
            renderer.preparer,
            "INITIAL_STATIC_FI_WORKSPACE",
            str(controller_workspace),
        ):
            renderer.preparer.prepare_source_adoption_package(
                source_repository=self.control,
                application_source_repository=self.application,
                expected_static_assets_manifest=self.expected_static_assets_manifest,
                control_commit=self.control_commit,
                application_release_sha=self.release,
                expected_alembic_revision=REVISION,
                source_transport_config=self.config,
                campaign_binding_path=self.binding,
                initial_static_object_id=self.initial_object_id,
                package_id="controller-workspace-package",
                destination=leaked_package,
                apply=True,
            )
        with self.assertRaisesRegex(
            renderer.InitialStaticControlError,
            "workspace is not the fixed FI bootstrap root",
        ):
            renderer.build_initial_static_control(
                source_transport_config=self.config,
                campaign_binding=self.binding,
                source_adoption_package_directory=leaked_package,
                preparation_receipt=leaked_package / renderer.PREPARATION_RECEIPT_NAME,
                fi_install_receipt=self.install_receipt,
            )

    def test_cross_campaign_controller_config_blocks_before_initial_static_control(self) -> None:
        other_campaign = "initial-static-control-other-20260730"
        other_config = (
            self.controller_campaigns_root
            / other_campaign
            / renderer.transport.CONTROLLER_DIRECTORY_NAME
            / renderer.transport.SOURCE_TRANSPORT_CONFIG_FILENAME
        )
        other_config.parent.mkdir(mode=0o700, parents=True)
        for directory in (other_config.parent.parent, other_config.parent):
            directory.chmod(0o700)
        shutil.copy2(self.config, other_config)
        other_config.chmod(0o600)

        with mock.patch.object(
            renderer.preparer,
            "verify_prepared_source_adoption_package",
            side_effect=AssertionError("cross-campaign config reached initial-static package verification"),
        ):
            with self.assertRaisesRegex(
                renderer.InitialStaticControlError,
                "controller source transport config does not bind the campaign",
            ):
                renderer.build_initial_static_control(
                    source_transport_config=other_config,
                    campaign_binding=self.binding,
                    source_adoption_package_directory=self.package,
                    preparation_receipt=self.preparation,
                    fi_install_receipt=self.install_receipt,
                )

    def test_package_canonical_release_tree_must_match_campaign_binding(self) -> None:
        binding = json.loads(self.binding.read_text(encoding="utf-8"))
        binding["application"]["release_tree"] = "0" * 40
        unsigned = {key: value for key, value in binding.items() if key != "binding_sha256"}
        binding["binding_sha256"] = renderer.transport.campaign_binding.sha256_bytes(
            renderer.transport.campaign_binding.canonical_json_bytes(unsigned)
        )
        _write_private(self.binding, _canonical(binding))
        with self.assertRaisesRegex(
            renderer.InitialStaticControlError,
            "canonical release tree is not bound to the campaign",
        ):
            renderer.build_initial_static_control(
                source_transport_config=self.config,
                campaign_binding=self.binding,
                source_adoption_package_directory=self.package,
                preparation_receipt=self.preparation,
                fi_install_receipt=self.install_receipt,
            )

    def test_prepared_and_upload_report_are_bound_to_the_package_request(self) -> None:
        prepared = self._prepared_receipt()
        expected = renderer.validate_prepared_receipt(control=self.control_value, prepared_receipt=prepared)
        self.assertEqual("verified", expected["status"])
        self.assertEqual(self.control_value.request.object_id, self.initial_object_id)
        self.assertEqual([RECIPIENTS["controller"], RECIPIENTS["ir"]], expected["recipients"])

        upload = renderer.render_upload_command(
            control=self.control_value,
            fi_known_hosts=self.known_hosts,
            prepared_receipt=prepared,
            presigned_upload_url=self._presigned_url(),
        )
        self.assertEqual(1, upload.count("X-Amz-Signature"))
        remote = shlex.split(shlex.split(upload)[-1])
        self.assertEqual("--upload-url", remote[-2])
        self.assertEqual(self._presigned_url(), remote[-1])

        exchange_policy = renderer._exchange_policy_from_control(self.control_value.policy)
        exchange_request = renderer.exchange._request_from_value(
            renderer._request_value(self.control_value.request), policy=exchange_policy, field="fixture request"
        )
        descriptor = {
            "object_key": renderer.exchange.contract.source_object_key(exchange_policy, exchange_request),
            "version_id": "version-fixture-002",
            "ciphertext_sha256": "e" * 64,
            "ciphertext_bytes": 1300,
            "plaintext_sha256": "d" * 64,
            "plaintext_bytes": 1234,
        }
        unsigned = renderer.exchange._upload_report_unsigned(request=exchange_request, descriptor=descriptor)
        report = {**unsigned, "report_sha256": renderer.exchange.sha256_bytes(renderer.exchange.canonical_json_bytes(unsigned))}
        report_path = _write_private(self.root / "controller-output" / "upload.json", _canonical(report))
        verified = renderer.validate_upload_report(
            control=self.control_value, prepared_receipt=prepared, upload_report=report_path
        )
        self.assertEqual(descriptor, verified["object"])
        self.assertTrue(verified["controller_readback_required"])

    def test_known_hosts_must_pin_the_exact_fi_host(self) -> None:
        self.known_hosts.write_text(
            "65.109.220.60 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixtureKnownHostKey\n", encoding="ascii"
        )
        self.known_hosts.chmod(0o644)
        with self.assertRaisesRegex(renderer.InitialStaticControlError, "lacks the exact FI host key"):
            renderer.render_prepare_command(control=self.control_value, fi_known_hosts=self.known_hosts)

    def test_bad_prepared_or_upload_report_never_advances(self) -> None:
        prepared = self._prepared_receipt(plaintext_sha="d" * 64)
        raw = json.loads(prepared.read_bytes())
        raw["request"]["object_id"] = "wrong-object"
        tampered = _write_private(self.root / "controller-output" / "prepared-tampered.json", _canonical(raw))
        with self.assertRaisesRegex(renderer.InitialStaticControlError, "prepared receipt"):
            renderer.validate_prepared_receipt(control=self.control_value, prepared_receipt=tampered)

        exchange_policy = renderer._exchange_policy_from_control(self.control_value.policy)
        exchange_request = renderer.exchange._request_from_value(
            renderer._request_value(self.control_value.request), policy=exchange_policy, field="fixture request"
        )
        descriptor = {
            "object_key": renderer.exchange.contract.source_object_key(exchange_policy, exchange_request),
            "version_id": "version-fixture-003",
            "ciphertext_sha256": "e" * 64,
            "ciphertext_bytes": 1301,
            "plaintext_sha256": "d" * 64,
            "plaintext_bytes": 1234,
        }
        unsigned = renderer.exchange._upload_report_unsigned(request=exchange_request, descriptor=descriptor)
        report = {**unsigned, "report_sha256": renderer.exchange.sha256_bytes(renderer.exchange.canonical_json_bytes(unsigned))}
        report_path = _write_private(self.root / "controller-output" / "upload-tampered.json", _canonical(report))
        with self.assertRaisesRegex(renderer.InitialStaticControlError, "differs from the prepared expectation"):
            renderer.validate_upload_report(control=self.control_value, prepared_receipt=prepared, upload_report=report_path)

    def test_direct_cli_upload_never_reads_or_prints_the_transient_url(self) -> None:
        url = "https://fixture.invalid/create-only?X-Amz-Signature=" + "a" * 64
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                renderer,
                "_read_presigned_url_stdin",
                side_effect=AssertionError("direct CLI must not read the URL"),
            ),
            mock.patch.object(
                renderer,
                "build_initial_static_control",
                side_effect=AssertionError("direct CLI must block before control construction"),
            ),
            mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO((url + "\n").encode("ascii")))),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = renderer.main(
                [
                    "render-upload",
                    "--source-transport-config",
                    "/ignored/source-transport.json",
                    "--campaign-binding",
                    "/ignored/campaign-binding.json",
                    "--source-adoption-package-directory",
                    "/ignored/package",
                    "--preparation-receipt",
                    "/ignored/preparation.json",
                    "--fi-install-receipt",
                    "/ignored/fi-install.json",
                    "--fi-known-hosts",
                    "/ignored/known_hosts",
                    "--prepared-receipt",
                    "/ignored/prepared.json",
                    "--presigned-upload-url-stdin",
                ]
            )
        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(2, status)
        self.assertIn("disabled", output)
        self.assertNotIn(url, output)
        self.assertNotIn("ssh ", output)

    def test_cli_render_only_never_invokes_ssh(self) -> None:
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT)]),
            mock.patch("subprocess.run", side_effect=AssertionError("CLI must not execute SSH")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            # The module deliberately has no execution action, and its parser
            # exposes only render/verify paths.
            self.assertNotIn("execute", renderer._parser()._subparsers._group_actions[0].choices)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
