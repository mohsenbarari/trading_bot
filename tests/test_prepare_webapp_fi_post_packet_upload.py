"""Focused tests for FI-local post-packet request derivation."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_webapp_fi_post_packet_upload.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


helper = _load("prepare_webapp_fi_post_packet_upload_test", SCRIPT)


@unittest.skipUnless(os.geteuid() == 0, "FI controls enforce root-only execution")
class PostPacketUploadDerivationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="post-packet-helper-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.candidate = self.root / "candidate"
        self.packet_directory = self.candidate / helper.CONTROL_PACKET_DIRECTORY / "packet-one"
        self.packet_directory.mkdir(mode=0o700, parents=True)
        self.candidate.chmod(0o700)
        (self.candidate / helper.CONTROL_PACKET_DIRECTORY).chmod(0o700)
        self.packet_directory.chmod(0o700)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.workspace.chmod(0o700)
        self.exchange = helper._load_exact_sibling("manage_webapp_fi_source_exchange.py", "post_packet_test_exchange")
        self.policy = self.exchange.contract.SourceTransportPolicy(
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-artifacts",
            prefix="campaigns/three-site",
            age_binary="/usr/bin/age",
            workspace=self.workspace,
            controller_age_recipient="age1pppppppppppppppppppppppppppppppppppppppp",
            webapp_fi_age_recipient="age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
            webapp_ir_age_recipient="age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr",
            maximum_plaintext_bytes=1024 * 1024,
        )
        self.policy = self.exchange.contract.validate_policy(self.policy)
        self.packet_policy = {
            "schema": "gold-trade-webapp-fi-static-provenance-transport-policy-v1",
            "endpoint_host": "s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "private-artifacts",
            "prefix": "campaigns/three-site",
            "age_binary": "/usr/bin/age",
            "workspace": str(self.workspace),
            "controller_age_recipient": self.policy.controller_age_recipient,
            "webapp_fi_age_recipient": self.policy.webapp_fi_age_recipient,
            "webapp_ir_age_recipient": self.policy.webapp_ir_age_recipient,
            "maximum_plaintext_bytes": 1024 * 1024,
        }
        self.verified_packet = {
            "campaign_binding": {
                "campaign_id": "post-packet-fi-20260730",
                "application": {
                    "release_sha": "a" * 40,
                    "release_tree": "b" * 40,
                    "expected_alembic_revision": "f2c7d8e9a0b1",
                },
                "tooling": {"control_commit": "c" * 40, "control_tree": "d" * 40},
                "binding_sha256": "e" * 64,
            },
            "source_transport_policy": self.packet_policy,
        }
        self.patchers = [
            mock.patch.object(
                helper,
                "_load_static_packet_state",
                return_value=(
                    helper._load_exact_sibling(
                        "webapp_fi_static_provenance_control_packet.py", "post_packet_test_packet"
                    ),
                    object(),
                    {},
                    self.verified_packet,
                    self.candidate,
                    self.packet_directory,
                    self.packet_directory / helper.SOURCE_TRANSPORT_POLICY_NAME,
                    "f" * 64,
                ),
            ),
            mock.patch.object(helper, "_load_candidate_exchange", return_value=self.exchange),
            mock.patch.object(helper, "_validate_exchange_policy", return_value=self.policy),
            mock.patch.object(helper, "FI_SOURCE_EXPORT_ROOT", self.root / "exports"),
            mock.patch.object(helper, "FI_SOURCE_EVIDENCE_ROOT", self.root / "evidence"),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_raw_image_derives_only_the_controller_single_recipient_and_fixed_path(self) -> None:
        control = helper.derive_post_packet_upload(
            packet_id="packet-one", artifact_kind=helper.RAW_APP_IMAGE, artifact_id="image-one"
        )
        self.assertEqual("webapp_fi", control.request.source_site)
        self.assertEqual("controller", control.request.destination_site)
        self.assertEqual((self.policy.controller_age_recipient,), control.request.recipients)
        self.assertEqual(
            self.root / "exports" / "post-packet-fi-20260730" / "image-one" / helper.RAW_APP_IMAGE_FILENAME,
            control.plaintext_path,
        )
        self.assertEqual(
            self.workspace / "post-packet-raw-app-image-image-one", control.prepared_directory
        )

    def test_evidence_derives_only_the_controller_single_recipient_and_fixed_path(self) -> None:
        control = helper.derive_post_packet_upload(
            packet_id="packet-one", artifact_kind=helper.SOURCE_EVIDENCE, artifact_id="evidence-one"
        )
        self.assertEqual((self.policy.controller_age_recipient,), control.request.recipients)
        self.assertEqual(
            self.root / "evidence" / "post-packet-fi-20260730" / "evidence-one" / helper.SOURCE_EVIDENCE_FILENAME,
            control.plaintext_path,
        )
        self.assertEqual(
            self.workspace / "post-packet-source-evidence-evidence-one", control.prepared_directory
        )

    def test_rejects_any_non_enum_kind_or_unsafe_identifier_before_route_derivation(self) -> None:
        with self.assertRaisesRegex(helper.PostPacketUploadError, "artifact_kind"):
            helper.derive_post_packet_upload(packet_id="packet-one", artifact_kind="static", artifact_id="image-one")
        with self.assertRaisesRegex(helper.PostPacketUploadError, "artifact_id"):
            helper.derive_post_packet_upload(
                packet_id="packet-one", artifact_kind=helper.RAW_APP_IMAGE, artifact_id="../image"
            )
        parser = helper._parser()
        self.assertEqual(
            {"prepare-upload", "upload-prepared"},
            set(parser._subparsers._group_actions[0].choices),
        )
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "prepare-upload",
                    "--packet-id",
                    "packet-one",
                    "--artifact-kind",
                    helper.RAW_APP_IMAGE,
                    "--artifact-id",
                    "image-one",
                    "--policy",
                    "/unsafe",
                ]
            )

    def test_upload_rederives_only_the_fixed_prepared_directory_and_request(self) -> None:
        control = helper.derive_post_packet_upload(
            packet_id="packet-one", artifact_kind=helper.RAW_APP_IMAGE, artifact_id="image-two"
        )
        descriptor = {
            "object_key": control.exchange.contract.source_object_key(control.policy, control.request),
            "version_id": "put-version-one",
            "ciphertext_sha256": "a" * 64,
            "ciphertext_bytes": 123,
            "plaintext_sha256": "b" * 64,
            "plaintext_bytes": 100,
        }
        unsigned = control.exchange._upload_report_unsigned(request=control.request, descriptor=descriptor)
        report = {
            **unsigned,
            "report_sha256": control.exchange.sha256_bytes(
                control.exchange.canonical_json_bytes(unsigned)
            ),
        }
        with mock.patch.object(control.exchange, "upload_prepared", return_value=report) as upload:
            result = helper.upload_post_packet_prepared(
                packet_id="packet-one",
                artifact_kind=helper.RAW_APP_IMAGE,
                artifact_id="image-two",
                upload_url="https://transient.invalid/only-for-test",
            )
        upload.assert_called_once_with(
            policy=control.policy,
            prepared_dir=self.workspace / "post-packet-raw-app-image-image-two",
            upload_url="https://transient.invalid/only-for-test",
        )
        self.assertEqual(descriptor, result["object"])
        parser = helper._parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "upload-prepared",
                    "--packet-id",
                    "packet-one",
                    "--artifact-kind",
                    helper.RAW_APP_IMAGE,
                    "--artifact-id",
                    "image-two",
                    "--upload-url",
                    "https://transient.invalid/only-for-test",
                    "--prepared-dir",
                    "/unsafe",
                ]
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
