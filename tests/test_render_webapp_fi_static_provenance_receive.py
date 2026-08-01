"""Focused local tests for the FI static-provenance receive renderer."""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


renderer = _load(
    "test_render_webapp_fi_static_provenance_receive",
    "render_webapp_fi_static_provenance_receive.py",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _binding() -> SimpleNamespace:
    return SimpleNamespace(
        campaign_id="packet-render-20260730",
        application_release_sha="a" * 40,
        application_release_tree="b" * 40,
        expected_alembic_revision="c" * 12,
        control_commit="d" * 40,
        control_tree="e" * 40,
        binding_sha256="f" * 64,
    )


def _policy(workspace: Path):
    return renderer.transport.SourceTransportPolicy(
        endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
        region="ir-thr-at1",
        bucket="private-artifacts",
        prefix="campaigns/current",
        age_binary="/usr/bin/age",
        workspace=workspace,
        controller_age_recipient="age1" + "a" * 40,
        webapp_fi_age_recipient="age1" + "c" * 40,
        webapp_ir_age_recipient="age1" + "d" * 40,
        maximum_plaintext_bytes=1024 * 1024,
    )


def _packet_policy(policy, workspace: Path) -> dict[str, object]:
    return {
        "schema": renderer.packet.SOURCE_TRANSPORT_POLICY_SCHEMA,
        "endpoint_host": "s3.ir-thr-at1.arvanstorage.ir",
        "region": policy.region,
        "bucket": policy.bucket,
        "prefix": policy.prefix,
        "age_binary": policy.age_binary,
        "workspace": str(workspace),
        "controller_age_recipient": policy.controller_age_recipient,
        "webapp_fi_age_recipient": policy.webapp_fi_age_recipient,
        "webapp_ir_age_recipient": policy.webapp_ir_age_recipient,
        "maximum_plaintext_bytes": policy.maximum_plaintext_bytes,
    }


@unittest.skipUnless(os.geteuid() == 0, "renderer control inputs enforce root-only ownership")
class StaticProvenanceReceiveRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="static-provenance-receive-render-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.fi_workspace = self.root / "fi-workspace"
        self.fi_workspace.mkdir(mode=0o700)
        self.binding = _binding()
        self.controller_workspace_root = self.root / "controller-workspaces"
        self.controller_workspace_root.mkdir(mode=0o700)
        self._workspace_root_patch = mock.patch.object(
            renderer.transport.contract,
            "SOURCE_TRANSPORT_WORKSPACE_ROOT",
            self.controller_workspace_root,
        )
        self._workspace_root_patch.start()
        self.addCleanup(self._workspace_root_patch.stop)
        self.controller_workspace = renderer.transport.contract.source_transport_workspace_for_campaign(
            self.binding.campaign_id
        )
        self.controller_workspace.mkdir(mode=0o700)
        self.policy = _policy(self.controller_workspace)
        self.controller_config = renderer.transport.ControllerS3Config(
            policy=self.policy,
            credentials_file=self.root / "controller-transport-credentials",
            campaign_id=self.binding.campaign_id,
        )
        self.candidate = self.root / "fi-bootstrap" / ("installed-" + self.binding.control_commit + "-package-one")
        self.candidate.parent.mkdir(mode=0o700)
        self.candidate.mkdir(mode=0o700)
        self.initial_control = SimpleNamespace(
            campaign_binding=self.binding,
            policy=_policy(self.fi_workspace),
            package_id="package-one",
            candidate_directory=self.candidate,
            fi_install_receipt_sha256="1" * 64,
        )
        self.authority = SimpleNamespace(
            campaign_binding=self.binding,
            signing_key=SimpleNamespace(public_key_base64="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
        )
        self.packet_id = "packet-one"
        self.packet_payload = b'{"packet":"fixture"}\n'
        self.packet_policy = _packet_policy(self.policy, self.fi_workspace)
        self.packet_verified = {
            "packet_id": self.packet_id,
            "source_transport_policy": self.packet_policy,
            "source_role_config_payload": _canonical({"schema": "role"}),
            "campaign_binding_sha256": self.binding.binding_sha256,
            "signer_enrollment_certificate_sha256": "2" * 64,
            "source_role_config_sha256": "3" * 64,
            "static_assets_provenance_sha256": "4" * 64,
            "source_transport_policy_sha256": "5" * 64,
            "signer_enrollment_certificate_payload": _canonical({"certificate": "fixture"}),
        }
        request = renderer.transport.SourceObjectRequest(
            campaign_id=self.binding.campaign_id,
            release_sha=self.binding.application_release_sha,
            control_commit=self.binding.control_commit,
            control_tree=self.binding.control_tree,
            source_site="controller",
            destination_site="webapp_fi",
            object_kind=renderer.transport.STATIC_PROVENANCE_OBJECT_KIND,
            object_id=self.packet_id,
            mode=renderer.transport.SINGLE_MODE,
            recipients=(self.policy.webapp_fi_age_recipient,),
        )
        descriptor = {
            "object_key": renderer.transport.source_object_key(self.policy, request),
            "version_id": "version-fixture-001",
            "ciphertext_sha256": "6" * 64,
            "ciphertext_bytes": 1024,
            "plaintext_sha256": renderer.sha256_bytes(self.packet_payload),
            "plaintext_bytes": len(self.packet_payload),
        }
        self.transport_receipt = renderer.transport.build_publish_receipt(
            config=self.policy, request=request, descriptor=descriptor
        )
        self.known_hosts = self.root / "inputs" / "fi-known-hosts"
        self.known_hosts.parent.mkdir(mode=0o700)
        self.known_hosts.write_text(
            "65.109.220.59 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixtureKnownHostKey\n",
            encoding="ascii",
        )
        self.known_hosts.chmod(0o644)

    def _control(self) -> renderer.StaticProvenanceReceiveControl:
        return renderer.StaticProvenanceReceiveControl(
            controller_config=self.controller_config,
            campaign_binding=self.binding,
            authority=self.authority,
            initial_control=self.initial_control,
            packet_id=self.packet_id,
            packet_path=self.root / "packet.json",
            packet_payload=self.packet_payload,
            verified_packet=self.packet_verified,
            transport_receipt=self.transport_receipt,
            candidate_directory=self.candidate,
            received_directory=self.fi_workspace / (renderer.REMOTE_RECEIVE_DIRECTORY_PREFIX + self.packet_id),
            fi_install_receipt_sha256=self.initial_control.fi_install_receipt_sha256,
        )

    def test_build_binds_current_apis_and_exact_packet_route(self) -> None:
        receipt_path = _private(self.root / "inputs" / "publish.json", _canonical(self.transport_receipt))
        packet_path = _private(self.root / "packet.json", self.packet_payload)
        layout = SimpleNamespace(role_config_path=self.root / "role.json", campaign_id=self.binding.campaign_id, campaign_binding_sha256=self.binding.binding_sha256)
        calls: list[str] = []

        with (
            mock.patch.object(renderer, "_load_canonical_controller_binding", return_value=(self.binding, layout, {"schema": "role"})) as load_binding,
            mock.patch.object(renderer.transport, "load_controller_config", return_value=self.controller_config),
            mock.patch.object(renderer.initial, "build_initial_static_control", return_value=self.initial_control),
            mock.patch.object(renderer, "_load_campaign_authority", return_value=self.authority),
            mock.patch.object(renderer, "_packet_path", return_value=packet_path),
            mock.patch.object(renderer, "_verify_packet", return_value=(self.packet_payload, self.packet_verified)),
            mock.patch.object(renderer, "_validate_packet_against_fi_adoption_descriptor", side_effect=lambda **_kwargs: calls.append("certificate")),
            mock.patch.object(renderer, "_verify_exact_transport_receipt", return_value=self.transport_receipt),
        ):
            control = renderer.build_static_provenance_receive_control(
                source_transport_config=self.root / "inputs" / "transport.json",
                campaign_binding=self.root / "campaign-binding.json",
                source_adoption_package_directory=self.root / "package",
                preparation_receipt=self.root / "preparation.json",
                fi_install_receipt=self.root / "fi-install.json",
                packet_id=self.packet_id,
                transport_publish_receipt=receipt_path,
            )

        self.assertEqual(self.packet_id, control.packet_id)
        self.assertEqual(self.transport_receipt, control.transport_receipt)
        self.assertEqual(["certificate"], calls)
        load_binding.assert_called_once()

    def test_build_rejects_a_valid_other_campaign_config_before_initial_control(self) -> None:
        other_campaign = "packet-render-other-20260730"
        other_workspace = renderer.transport.contract.source_transport_workspace_for_campaign(other_campaign)
        other_workspace.mkdir(mode=0o700)
        other_config = renderer.transport.ControllerS3Config(
            policy=dataclasses.replace(self.policy, workspace=other_workspace),
            credentials_file=self.root / "controller-transport-credentials",
            campaign_id=other_campaign,
        )
        with (
            mock.patch.object(
                renderer,
                "_load_canonical_controller_binding",
                return_value=(self.binding, SimpleNamespace(), {"schema": "role"}),
            ),
            mock.patch.object(renderer.transport, "load_controller_config", return_value=other_config),
            mock.patch.object(renderer.initial, "build_initial_static_control") as blocked_initial_control,
            self.assertRaisesRegex(
                renderer.StaticProvenanceReceiveRenderError,
                "config does not bind the canonical campaign",
            ),
        ):
            renderer.build_static_provenance_receive_control(
                source_transport_config=self.root / "inputs" / "transport.json",
                campaign_binding=self.root / "campaign-binding.json",
                source_adoption_package_directory=self.root / "package",
                preparation_receipt=self.root / "preparation.json",
                fi_install_receipt=self.root / "fi-install.json",
                packet_id=self.packet_id,
                transport_publish_receipt=self.root / "inputs" / "publish.json",
            )
        blocked_initial_control.assert_not_called()

    def test_render_is_one_pinned_ssh_control_and_url_is_only_final_remote_argument(self) -> None:
        control = self._control()
        url = "https://fixture.invalid/exact"
        with mock.patch.object(renderer.transport, "require_version_bound_presigned_get_url", return_value=url):
            command = renderer.render_receive_install_command(
                control=control, fi_known_hosts=self.known_hosts, presigned_download_url=url
            )
        outer = shlex.split(command)
        self.assertEqual("ssh", outer[0])
        self.assertIn("StrictHostKeyChecking=yes", outer)
        self.assertIn("GlobalKnownHostsFile=/dev/null", outer)
        self.assertIn("UserKnownHostsFile=" + str(self.known_hosts), outer)
        self.assertEqual(renderer.initial.REMOTE_HOST, outer[-2])
        remote = shlex.split(outer[-1])
        self.assertEqual(["/usr/bin/python3", "-I", "-B", "-c", renderer.REMOTE_LAUNCHER], remote[:5])
        self.assertEqual("--", remote[-2])
        self.assertEqual(url, remote[-1])
        self.assertEqual(1, command.count(url))
        config = json.loads(base64.b64decode(remote[-3]).decode("ascii"))
        self.assertNotIn("url", json.dumps(config, sort_keys=True).lower())
        self.assertEqual(str(self.candidate), config["candidate_directory"])
        self.assertEqual(str(self.fi_workspace / (renderer.REMOTE_RECEIVE_DIRECTORY_PREFIX + self.packet_id)), config["received_directory"])

    def test_render_rejects_known_hosts_without_the_fixed_fi_pin(self) -> None:
        self.known_hosts.write_text(
            "65.109.220.60 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixtureKnownHostKey\n",
            encoding="ascii",
        )
        self.known_hosts.chmod(0o644)
        with mock.patch.object(renderer.transport, "require_version_bound_presigned_get_url", return_value="https://fixture.invalid/exact"):
            with self.assertRaisesRegex(renderer.StaticProvenanceReceiveRenderError, "pinned FI SSH control"):
                renderer.render_receive_install_command(
                    control=self._control(),
                    fi_known_hosts=self.known_hosts,
                    presigned_download_url="https://fixture.invalid/exact",
                )

    def test_verify_install_receipt_requires_every_packet_and_object_binding(self) -> None:
        control = self._control()
        unsigned = {
            "schema": "gold-trade-webapp-fi-static-provenance-install-receipt-v1",
            "status": "installed",
            "installed_at": "2026-07-30T12:00:00Z",
            "candidate_directory": str(self.candidate),
            "campaign_id": self.binding.campaign_id,
            "packet_id": self.packet_id,
            "control_packet_sha256": renderer.sha256_bytes(self.packet_payload),
            "campaign_binding_sha256": self.packet_verified["campaign_binding_sha256"],
            "signer_enrollment_certificate_sha256": self.packet_verified["signer_enrollment_certificate_sha256"],
            "source_role_config_sha256": self.packet_verified["source_role_config_sha256"],
            "static_assets_provenance_sha256": self.packet_verified["static_assets_provenance_sha256"],
            "source_transport_policy_sha256": self.packet_verified["source_transport_policy_sha256"],
            "exchange_receive_receipt_sha256": "7" * 64,
            "exchange_object": dict(self.transport_receipt["object"]),
        }
        receipt = {**unsigned, "receipt_sha256": renderer.sha256_bytes(renderer.canonical_json_bytes(unsigned))}
        output = {"schema": renderer.INSTALL_OUTPUT_SCHEMA, "status": "installed", "install_receipt": receipt}
        output_path = _private(self.root / "outputs" / "install.json", _canonical(output))

        verified = renderer.validate_fi_static_provenance_install_receipt(control=control, install_output=output_path)
        self.assertEqual("verified", verified["status"])
        self.assertEqual(self.packet_id, verified["packet_id"])

        receipt["exchange_object"] = {**receipt["exchange_object"], "version_id": "wrong-version"}
        receipt["receipt_sha256"] = renderer.sha256_bytes(
            renderer.canonical_json_bytes({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        )
        _private(output_path, _canonical({"schema": renderer.INSTALL_OUTPUT_SCHEMA, "status": "installed", "install_receipt": receipt}))
        with self.assertRaisesRegex(renderer.StaticProvenanceReceiveRenderError, "not bound"):
            renderer.validate_fi_static_provenance_install_receipt(control=control, install_output=output_path)

    def test_remote_config_rejects_transient_or_secret_material(self) -> None:
        config = renderer._remote_config(self._control())
        config["controller_publish_receipt"] = {"url": "https://should-not-persist.invalid"}
        with self.assertRaisesRegex(renderer.StaticProvenanceReceiveRenderError, "transient or secret"):
            renderer._assert_remote_config(config)
        config["controller_publish_receipt"] = {"session_token": "must-not-cross-ssh"}
        with self.assertRaisesRegex(renderer.StaticProvenanceReceiveRenderError, "transient or secret"):
            renderer._assert_remote_config(config)
        config["controller_publish_receipt"] = {"opaque_payload_base64": "must-not-cross-ssh"}
        with self.assertRaisesRegex(renderer.StaticProvenanceReceiveRenderError, "transient or secret"):
            renderer._assert_remote_config(config)

    def test_install_output_firewall_rejects_nonsecret_shape_violations_before_binding_verify(self) -> None:
        output = {
            "schema": renderer.INSTALL_OUTPUT_SCHEMA,
            "status": "installed",
            "install_receipt": {
                "schema": "gold-trade-webapp-fi-static-provenance-install-receipt-v1",
                "status": "installed",
                "session_token": "must-not-cross-ssh",
            },
        }
        with self.assertRaisesRegex(renderer.StaticProvenanceReceiveRenderError, "transient or secret"):
            renderer._parse_canonical_install_output(_canonical(output))
        self.assertIn("load_nonsecret_install_receipt", renderer.REMOTE_RECEIVER_SOURCE)

    def test_direct_cli_render_never_reads_or_prints_the_transient_url(self) -> None:
        url = "https://fixture.invalid/download?X-Amz-Signature=" + "a" * 64
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
                "build_static_provenance_receive_control",
                side_effect=AssertionError("direct CLI must block before control construction"),
            ),
            mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO((url + "\n").encode("ascii")))),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = renderer.main(
                [
                    "render",
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
                    "--packet-id",
                    "packet-one",
                    "--transport-publish-receipt",
                    "/ignored/publish.json",
                    "--fi-known-hosts",
                    "/ignored/known_hosts",
                    "--presigned-download-url-stdin",
                ]
            )
        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(2, status)
        self.assertIn("disabled", output)
        self.assertNotIn(url, output)
        self.assertNotIn("ssh ", output)

    def test_cli_has_render_and_verify_only(self) -> None:
        choices = renderer._parser()._subparsers._group_actions[0].choices
        self.assertEqual({"render", "verify-install"}, set(choices))
        self.assertNotIn("execute", choices)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
