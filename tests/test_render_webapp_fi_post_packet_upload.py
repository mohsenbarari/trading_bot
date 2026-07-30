"""Focused tests for controller-only FI post-packet upload rendering."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_webapp_fi_post_packet_upload.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


renderer = _load("render_webapp_fi_post_packet_upload_test", SCRIPT)

CAMPAIGN = "post-packet-control-20260730"
REVISION = "f2c7d8e9a0b1"
PACKET_ID = "post-packet-one"
RECIPIENTS = {
    "controller": "age1pppppppppppppppppppppppppppppppppppppppp",
    "fi": "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    "ir": "age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _write_private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _key_material() -> tuple[Ed25519PrivateKey, str]:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, base64.b64encode(public).decode("ascii")


@unittest.skipUnless(os.geteuid() == 0, "controller controls enforce root-only inputs")
class PostPacketUploadRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="post-packet-render-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.campaigns_root = self.root / "campaigns"
        self.packet_root = self.root / "packets"
        self.fi_root = self.root / "fi-bootstrap"
        for directory in (self.campaigns_root, self.packet_root, self.fi_root):
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)
        campaign_directory = self.campaigns_root / CAMPAIGN
        self.source_phase = campaign_directory / "webapp-fi-source"
        self.source_phase.mkdir(mode=0o700, parents=True)
        campaign_directory.chmod(0o700)
        self.source_phase.chmod(0o700)

        self.controller_key, self.controller_public = _key_material()
        _, self.fi_public = _key_material()
        self.release = "a" * 40
        self.release_tree = "b" * 40
        self.control_commit = "c" * 40
        self.control_tree = "d" * 40
        binding_value = renderer.initial.transport.campaign_binding.build_campaign_binding(
            campaign_id=CAMPAIGN,
            application_release_sha=self.release,
            application_release_tree=self.release_tree,
            expected_alembic_revision=REVISION,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
        )
        self.binding_path = _write_private(
            self.source_phase / "campaign-binding.json",
            renderer.initial.transport.campaign_binding.canonical_json_bytes(binding_value) + b"\n",
        )
        self.binding = renderer.initial.transport.campaign_binding.load_campaign_binding(self.binding_path)
        role_binding = renderer.role_config.binding.load_campaign_binding(self.binding_path)
        role_value = renderer.role_config.build_source_role_config(
            campaign_binding=role_binding,
            application_container="fixture_app",
            sync_worker_container="fixture_sync",
        )
        self.role_path = _write_private(self.source_phase / "source-role-config.json", _canonical(role_value))

        inputs = self.root / "inputs"
        inputs.mkdir(mode=0o700)
        credentials = _write_private(inputs / "credentials.json", b'{"access_key":"fixture","secret_key":"fixture"}\n')
        self.workspace = inputs / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.workspace.chmod(0o700)
        self.transport_config = _write_private(
            inputs / "source-transport.json",
            _canonical(
                {
                    "schema": renderer.initial.transport.CONFIG_SCHEMA,
                    "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
                    "region": "ir-thr-at1",
                    "bucket": "private-artifacts",
                    "prefix": "campaigns/three-site",
                    "credentials_file": str(credentials),
                    "age_binary": "/usr/bin/age",
                    "workspace": str(self.workspace),
                    "controller_age_recipient": RECIPIENTS["controller"],
                    "webapp_fi_age_recipient": RECIPIENTS["fi"],
                    "webapp_ir_age_recipient": RECIPIENTS["ir"],
                    "maximum_plaintext_bytes": 1024 * 1024,
                    "presign_expires_seconds": 300,
                }
            ),
        )
        self.policy = renderer.initial.transport.load_controller_config(self.transport_config).policy
        self.packet_payload = self._packet_payload(role_value)
        packet_directory = self.packet_root / CAMPAIGN / PACKET_ID
        packet_directory.mkdir(mode=0o700, parents=True)
        (self.packet_root / CAMPAIGN).chmod(0o700)
        packet_directory.chmod(0o700)
        _write_private(packet_directory / "static-provenance.json", self.packet_payload)

        self.fi_candidate = self.fi_root / ("installed-" + self.control_commit + "-package-one")
        self.fi_static_receipt = _write_private(inputs / "fi-static-packet.json", self._static_receipt())
        self.known_hosts = inputs / "fi-known_hosts"
        self.known_hosts.write_text(
            "65.109.220.59 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixtureKnownHostKey\n",
            encoding="ascii",
        )
        self.known_hosts.chmod(0o644)
        self.authority = SimpleNamespace(
            campaign_binding=self.binding,
            signing_key=SimpleNamespace(public_key_base64=self.controller_public),
            signer=self.controller_key,
        )
        self.patches = [
            mock.patch.object(renderer.packet_builder, "CONTROLLER_CAMPAIGN_ROOT", self.campaigns_root),
            mock.patch.object(renderer.packet_builder, "CONTROL_PACKET_ROOT", self.packet_root),
            mock.patch.object(renderer.initial, "FI_BOOTSTRAP_ROOT", self.fi_root),
            mock.patch.object(
                renderer.packet_builder,
                "_load_campaign_bound_controller_signer",
                return_value=self.authority,
            ),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.control = self._build()

    def _certificate(self) -> dict[str, object]:
        source_object = {
            "object_key": "source-adoption/package-one.age",
            "version_id": "fixture-version-1",
            "ciphertext_sha256": "1" * 64,
            "ciphertext_bytes": 100,
            "plaintext_sha256": "2" * 64,
            "plaintext_bytes": 90,
        }
        unsigned: dict[str, object] = {
            "schema": renderer.packet_control.SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA,
            "status": "issued",
            "certificate_id": "certificate-one",
            "operation_id": "operation-one",
            "issued_at": "2026-07-30T00:00:00Z",
            "not_before": "2026-07-30T00:00:00Z",
            "not_after": "2026-07-30T00:10:00Z",
            "campaign_id": CAMPAIGN,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "package_id": "package-one",
            "application": {"release_sha": self.release, "expected_alembic_revision": REVISION},
            "tooling": {"control_commit": self.control_commit, "control_tree": self.control_tree},
            "canonical_release_tree_sha256": "3" * 64,
            "source_adoption_install_receipt_sha256": "4" * 64,
            "delivery_envelope_sha256": "5" * 64,
            "source_adoption_object": source_object,
            "fi_bootstrap_recipient": RECIPIENTS["fi"],
            "fi_ssh_host_public_key_sha256": "6" * 64,
            "source_signing_public_key_base64": self.fi_public,
            "source_signing_key_id": renderer.packet_control.public_key_id(self.fi_public),
            "controller_public_key_base64": self.controller_public,
            "controller_key_id": renderer.packet_control.public_key_id(self.controller_public),
        }
        return {
            **unsigned,
            "controller_signature": self._sign_controller(
                unsigned,
                domain=renderer.packet_control.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN,
            ),
        }

    def _static_provenance(self) -> dict[str, object]:
        files = [{"path": "index.html", "sha256": "7" * 64, "bytes": 13}]
        unsigned: dict[str, object] = {
            "schema": renderer.packet_control.STATIC_ASSET_PROVENANCE_SCHEMA,
            "status": "verified",
            "campaign_id": CAMPAIGN,
            "application": {"release_sha": self.release, "expected_alembic_revision": REVISION},
            "source_kind": "deterministic_2c08_dist_manifest",
            "artifact": {
                "object_key": "static-assets/fixture.age",
                "version_id": "static-version-1",
                "ciphertext_sha256": "8" * 64,
                "ciphertext_bytes": 100,
                "plaintext_sha256": "9" * 64,
                "plaintext_bytes": 90,
            },
            "files": files,
            "files_sha256": renderer.packet_control.sha256_bytes(renderer.packet_control.canonical_json_bytes(files)),
            "controller_public_key_base64": self.controller_public,
        }
        return {
            **unsigned,
            "controller_signature": self._sign_controller(
                unsigned,
                domain=renderer.packet_control.STATIC_ASSET_SIGNATURE_DOMAIN,
            ),
        }

    def _exchange_policy(self) -> dict[str, object]:
        return {
            "schema": renderer.packet_control.EXCHANGE_POLICY_SCHEMA,
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "private-artifacts",
            "prefix": "campaigns/three-site",
            "age_binary": "/usr/bin/age",
            "workspace": str(self.workspace),
            "controller_age_recipient": RECIPIENTS["controller"],
            "webapp_fi_age_recipient": RECIPIENTS["fi"],
            "webapp_ir_age_recipient": RECIPIENTS["ir"],
            "maximum_plaintext_bytes": 1024 * 1024,
        }

    def _packet_payload(self, role_value: dict[str, object]) -> bytes:
        return renderer.packet_control.build_control_packet_payload_with_signer(
            created_at="2026-07-30T00:00:00Z",
            campaign_binding_payload=self.binding_path.read_bytes(),
            signer_enrollment_certificate_payload=_canonical(self._certificate()),
            source_role_config_payload=_canonical(role_value),
            static_assets_provenance_payload=_canonical(self._static_provenance()),
            source_transport_policy_payload=_canonical(self._exchange_policy()),
            packet_id=PACKET_ID,
            controller_signer=self.controller_key,
            controller_public_key_base64=self.controller_public,
        )

    def _sign_controller(self, unsigned: dict[str, object], *, domain: bytes) -> dict[str, str]:
        signature = self.controller_key.sign(
            domain + renderer.packet_control.canonical_json_bytes(unsigned)
        )
        return {
            "algorithm": "ed25519",
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }

    def _static_receipt(self) -> bytes:
        request = renderer.initial.transport.SourceObjectRequest(
            campaign_id=CAMPAIGN,
            release_sha=self.release,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
            source_site="controller",
            destination_site="webapp_fi",
            object_kind=renderer.initial.transport.STATIC_PROVENANCE_OBJECT_KIND,
            object_id=PACKET_ID,
            mode=renderer.initial.transport.SINGLE_MODE,
            recipients=(RECIPIENTS["fi"],),
        )
        descriptor = {
            "object_key": renderer.initial.transport.source_object_key(self.policy, request),
            "version_id": "static-version-1",
            "ciphertext_sha256": "a" * 64,
            "ciphertext_bytes": 1024,
            "plaintext_sha256": "b" * 64,
            "plaintext_bytes": 900,
        }
        verified = renderer.packet_control.verify_control_packet_payload(
            payload=self.packet_payload,
            pinned_controller_public_key_base64=self.controller_public,
        )
        unsigned: dict[str, object] = {
            "schema": renderer.STATIC_PACKET_INSTALL_RECEIPT_SCHEMA,
            "status": "installed",
            "installed_at": "2026-07-30T12:00:00Z",
            "candidate_directory": str(self.fi_candidate),
            "campaign_id": CAMPAIGN,
            "packet_id": PACKET_ID,
            "control_packet_sha256": hashlib.sha256(self.packet_payload).hexdigest(),
            "campaign_binding_sha256": verified["campaign_binding_sha256"],
            "signer_enrollment_certificate_sha256": verified["signer_enrollment_certificate_sha256"],
            "source_role_config_sha256": verified["source_role_config_sha256"],
            "static_assets_provenance_sha256": verified["static_assets_provenance_sha256"],
            "source_transport_policy_sha256": verified["source_transport_policy_sha256"],
            "exchange_receive_receipt_sha256": "c" * 64,
            "exchange_object": descriptor,
        }
        return _canonical({**unsigned, "receipt_sha256": renderer.sha256_bytes(renderer.canonical_json_bytes(unsigned))})

    def _build(self, *, artifact_kind: str = renderer.RAW_APP_IMAGE, artifact_id: str = "image-export-one"):
        return renderer.build_post_packet_upload_control(
            source_transport_config=self.transport_config,
            campaign_binding=self.binding_path,
            source_role_config=self.role_path,
            fi_static_packet_install_receipt=self.fi_static_receipt,
            packet_id=PACKET_ID,
            artifact_kind=artifact_kind,
            artifact_id=artifact_id,
        )

    def _prepared_receipt(self) -> Path:
        exchange_policy = renderer.initial._exchange_policy_from_control(self.control.policy)
        exchange_request = renderer.initial.exchange._request_from_value(
            renderer._request_value(self.control.request), policy=exchange_policy, field="fixture request"
        )
        value = renderer.initial.exchange._build_prepared_receipt(
            request=exchange_request,
            policy=exchange_policy,
            recipients=(RECIPIENTS["controller"],),
            plaintext={"sha256": "d" * 64, "bytes": 1234},
            ciphertext={"sha256": "e" * 64, "bytes": 1300, "name": renderer.initial.exchange.PREPARED_CIPHERTEXT_NAME},
        )
        return _write_private(self.root / "controller-output" / "prepared.json", _canonical(value))

    def _presigned_url(self) -> str:
        key = renderer.initial.transport.source_object_key(self.control.policy, self.control.request)
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        headers = ";".join(
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
            "https://s3.ir-thr-at1.arvanstorage.ir/private-artifacts/"
            + quote(key, safe="/")
            + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=fixture%2F"
            + now.strftime("%Y%m%d")
            + "%2Fir-thr-at1%2Fs3%2Faws4_request&X-Amz-Date="
            + now.strftime("%Y%m%dT%H%M%SZ")
            + "&X-Amz-Expires=300&X-Amz-SignedHeaders="
            + quote(headers, safe="")
            + "&X-Amz-Signature="
            + "a" * 64
        )

    def _upload_report(self, prepared: Path) -> Path:
        del prepared
        exchange_policy = renderer.initial._exchange_policy_from_control(self.control.policy)
        exchange_request = renderer.initial.exchange._request_from_value(
            renderer._request_value(self.control.request), policy=exchange_policy, field="fixture request"
        )
        descriptor = {
            "object_key": renderer.initial.exchange.contract.source_object_key(exchange_policy, exchange_request),
            "version_id": "upload-version-1",
            "ciphertext_sha256": "e" * 64,
            "ciphertext_bytes": 1300,
            "plaintext_sha256": "d" * 64,
            "plaintext_bytes": 1234,
        }
        unsigned = renderer.initial.exchange._upload_report_unsigned(request=exchange_request, descriptor=descriptor)
        report = {**unsigned, "report_sha256": renderer.initial.exchange.sha256_bytes(renderer.initial.exchange.canonical_json_bytes(unsigned))}
        return _write_private(self.root / "controller-output" / "upload.json", _canonical(report))

    def test_renders_only_fixed_pinned_prepare_control(self) -> None:
        with mock.patch("subprocess.run", side_effect=AssertionError("renderer must not execute SSH")):
            command = renderer.render_prepare_command(control=self.control, fi_known_hosts=self.known_hosts)
        outer = shlex.split(command)
        self.assertEqual("ssh", outer[0])
        self.assertIn("StrictHostKeyChecking=yes", outer)
        self.assertEqual(renderer.initial.REMOTE_HOST, outer[-2])
        remote = shlex.split(outer[-1])
        self.assertEqual(
            ["/usr/bin/python3", "-I", "-B", str(self.fi_candidate / renderer.FI_POST_PACKET_HELPER_MEMBER)],
            remote[:4],
        )
        self.assertEqual("prepare-upload", remote[4])
        self.assertEqual(PACKET_ID, remote[remote.index("--packet-id") + 1])
        self.assertEqual(renderer.RAW_APP_IMAGE, remote[remote.index("--artifact-kind") + 1])
        self.assertEqual("image-export-one", remote[remote.index("--artifact-id") + 1])
        self.assertNotIn("fixture", command.lower())
        self.assertNotIn("://", command)

    def test_prepared_report_and_transient_url_are_bound_to_controller_only_request(self) -> None:
        prepared = self._prepared_receipt()
        verified = renderer.validate_prepared_receipt(control=self.control, prepared_receipt=prepared)
        self.assertEqual([RECIPIENTS["controller"]], verified["recipients"])
        self.assertEqual(renderer.RAW_APP_IMAGE, verified["artifact_kind"])
        command = renderer.render_upload_command(
            control=self.control,
            fi_known_hosts=self.known_hosts,
            prepared_receipt=prepared,
            presigned_upload_url=self._presigned_url(),
        )
        remote = shlex.split(shlex.split(command)[-1])
        self.assertEqual("upload-prepared", remote[4])
        self.assertEqual(
            str(self.fi_candidate / renderer.FI_POST_PACKET_HELPER_MEMBER),
            remote[3],
        )
        self.assertEqual(PACKET_ID, remote[remote.index("--packet-id") + 1])
        self.assertEqual(renderer.RAW_APP_IMAGE, remote[remote.index("--artifact-kind") + 1])
        self.assertEqual("image-export-one", remote[remote.index("--artifact-id") + 1])
        self.assertNotIn("--policy", remote)
        self.assertNotIn("--prepared-dir", remote)
        self.assertEqual("--upload-url", remote[-2])
        self.assertEqual(self._presigned_url(), remote[-1])
        report = self._upload_report(prepared)
        result = renderer.validate_upload_report(control=self.control, prepared_receipt=prepared, upload_report=report)
        self.assertTrue(result["controller_readback_required"])
        self.assertEqual("upload-version-1", result["object"]["version_id"])

    def test_tampered_install_receipt_or_route_blocks_before_any_render(self) -> None:
        value = json.loads(self.fi_static_receipt.read_text(encoding="ascii"))
        value["source_role_config_sha256"] = "0" * 64
        unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
        value["receipt_sha256"] = renderer.sha256_bytes(renderer.canonical_json_bytes(unsigned))
        _write_private(self.fi_static_receipt, _canonical(value))
        with self.assertRaisesRegex(renderer.PostPacketUploadControlError, "not bound to the sealed packet"):
            self._build()

        with self.assertRaisesRegex(renderer.PostPacketUploadControlError, "artifact_kind"):
            self._build(artifact_kind="static")

    def test_evidence_enum_uses_the_same_controller_only_route_and_known_hosts_are_pinned(self) -> None:
        evidence = self._build(artifact_kind=renderer.SOURCE_EVIDENCE, artifact_id="evidence-one")
        self.assertEqual(renderer.SOURCE_EVIDENCE, evidence.request.object_kind)
        self.assertEqual((RECIPIENTS["controller"],), evidence.request.recipients)
        self.known_hosts.write_text(
            "65.109.220.60 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixtureKnownHostKey\n", encoding="ascii"
        )
        self.known_hosts.chmod(0o644)
        with self.assertRaisesRegex(renderer.PostPacketUploadControlError, "pinned FI SSH"):
            renderer.render_prepare_command(control=evidence, fi_known_hosts=self.known_hosts)

    def test_parser_has_no_execute_or_arbitrary_route_path_flags(self) -> None:
        choices = set(renderer._parser()._subparsers._group_actions[0].choices)
        self.assertEqual({"render-prepare", "verify-prepared", "render-upload", "verify-upload"}, choices)
        parser = renderer._parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "render-prepare",
                    "--source-transport-config",
                    str(self.transport_config),
                    "--campaign-binding",
                    str(self.binding_path),
                    "--source-role-config",
                    str(self.role_path),
                    "--fi-static-packet-install-receipt",
                    str(self.fi_static_receipt),
                    "--packet-id",
                    PACKET_ID,
                    "--artifact-kind",
                    "raw-app-image",
                    "--artifact-id",
                    "image-export-one",
                    "--fi-known-hosts",
                    str(self.known_hosts),
                    "--destination-site",
                    "webapp_ir",
                ]
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
