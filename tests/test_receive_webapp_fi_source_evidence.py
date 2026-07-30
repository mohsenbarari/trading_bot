"""Focused local tests for the controller WebApp-FI source-evidence boundary."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
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


evidence_receiver = _load(
    "test_receive_webapp_fi_source_evidence",
    "receive_webapp_fi_source_evidence.py",
)


def recipient(character: str) -> str:
    return "age1" + character * 40


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


class FakeBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        value = self.payload[self.offset : self.offset + size]
        self.offset += len(value)
        return value

    def close(self) -> None:
        self.closed = True


class FakeS3:
    def __init__(self, *, key: str, version_id: str, ciphertext: bytes, metadata: dict[str, str]) -> None:
        self.key = key
        self.version_id = version_id
        self.ciphertext = ciphertext
        self.metadata = metadata
        self.get_calls: list[dict[str, object]] = []
        self.events: list[str] = []

    def get_bucket_versioning(self, **_kwargs: object) -> dict[str, object]:
        return {"Status": "Enabled"}

    def get_bucket_acl(self, **_kwargs: object) -> dict[str, object]:
        return {
            "Owner": {"ID": "owner"},
            "Grants": [{"Grantee": {"Type": "CanonicalUser", "ID": "owner"}, "Permission": "FULL_CONTROL"}],
        }

    def list_object_versions(self, **_kwargs: object) -> dict[str, object]:
        return {
            "Versions": [{"Key": self.key, "VersionId": self.version_id, "IsLatest": True}],
            "DeleteMarkers": [],
            "IsTruncated": False,
        }

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.events.append("get")
        self.get_calls.append(dict(kwargs))
        return {
            "VersionId": self.version_id,
            "Metadata": self.metadata,
            "ContentLength": len(self.ciphertext),
            "Body": FakeBody(self.ciphertext),
        }


class SourceEvidenceReceiverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="source-evidence-receiver-test-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.campaign_id = "source-evidence-fixture-20260730"
        self.campaign_directory = self.root / self.campaign_id
        self.campaign_directory.mkdir(mode=0o700)
        self.campaign_directory.chmod(0o700)
        self.release = "a" * 40
        self.release_tree = "b" * 40
        self.control_commit = "c" * 40
        self.control_tree = "d" * 40
        self.binding = self._write_binding()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.workspace.chmod(0o700)
        self.data_root = self.root / "staging-volume"
        self.data_root.mkdir(mode=0o700)
        self.data_root.chmod(0o700)
        self.credentials = _private(self.root / "credentials.json", _canonical({"fixture": True}))
        self.policy = evidence_receiver.receiver.transport.SourceTransportPolicy(
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-artifacts",
            prefix="campaigns/three-site",
            age_binary="/usr/bin/age",
            workspace=self.workspace,
            controller_age_recipient=recipient("a"),
            webapp_fi_age_recipient=recipient("c"),
            webapp_ir_age_recipient=recipient("d"),
            maximum_plaintext_bytes=1024 * 1024,
        )
        self.controller_config = evidence_receiver.receiver.transport.ControllerS3Config(
            policy=self.policy,
            credentials_file=self.credentials,
        )
        self.test_age_keygen = self.root / "age-keygen-test-only"
        self.test_age_keygen.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        self.test_age_keygen.chmod(0o700)
        self._root_patch = mock.patch.object(
            evidence_receiver.receiver.identity_bootstrap,
            "CAMPAIGNS_ROOT",
            self.root,
        )
        self._data_root_patch = mock.patch.object(
            evidence_receiver.receiver,
            "CONTROLLER_SOURCE_RECEIVE_ROOT",
            self.data_root,
        )
        self._binary_patch = mock.patch.object(
            evidence_receiver.receiver.identity_bootstrap,
            "AGE_KEYGEN_BINARY",
            self.test_age_keygen,
        )
        self._recipient_patch = mock.patch.object(
            evidence_receiver.receiver.identity_bootstrap,
            "derive_recipient",
            return_value=self.policy.controller_age_recipient,
        )
        self._root_patch.start()
        self._data_root_patch.start()
        self._binary_patch.start()
        self._recipient_patch.start()
        layout = evidence_receiver.receiver.identity_bootstrap.identity_layout_for_campaign_binding(self.binding)
        _private(layout.identity_path, b"AGE-SECRET-KEY-1TEST\n")
        layout.controller_directory.chmod(0o700)
        receipt = evidence_receiver.receiver.identity_bootstrap._receipt_value(
            layout=layout,
            recipient=self.policy.controller_age_recipient,
        )
        _private(layout.receipt_path, _canonical(receipt))
        self.inputs = self.root / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.inputs.chmod(0o700)

    def tearDown(self) -> None:
        self._recipient_patch.stop()
        self._binary_patch.stop()
        self._data_root_patch.stop()
        self._root_patch.stop()
        self.temporary.cleanup()

    def _write_binding(self) -> Path:
        source_phase = self.campaign_directory / evidence_receiver.receiver.binding.SOURCE_PHASE_DIRECTORY
        source_phase.mkdir(mode=0o700)
        source_phase.chmod(0o700)
        value = evidence_receiver.receiver.binding.build_campaign_binding(
            campaign_id=self.campaign_id,
            application_release_sha=self.release,
            application_release_tree=self.release_tree,
            expected_alembic_revision="f" * 12,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
        )
        return _private(
            source_phase / evidence_receiver.receiver.binding.CAMPAIGN_BINDING_FILENAME,
            _canonical(value),
        )

    def _write_report(
        self,
        *,
        object_kind: str = evidence_receiver.receiver.contract.SOURCE_EVIDENCE_OBJECT_KIND,
        plaintext: bytes = b"sealed evidence envelope\n",
        ciphertext: bytes = b"fixture age ciphertext\n",
    ) -> tuple[Path, dict[str, object], bytes, bytes]:
        policy = evidence_receiver.receiver._policy_for_exchange(self.policy)
        request = evidence_receiver.receiver.exchange.contract.SourceObjectRequest(
            campaign_id=self.campaign_id,
            release_sha=self.release,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
            source_site="webapp_fi",
            destination_site="controller",
            object_kind=object_kind,
            object_id="evidence-object-fixture",
            mode=evidence_receiver.receiver.contract.SINGLE_MODE,
            recipients=(self.policy.controller_age_recipient,),
        )
        descriptor = {
            "object_key": evidence_receiver.receiver.exchange.contract.source_object_key(policy, request),
            "version_id": "version-evidence-fixture",
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "ciphertext_bytes": len(ciphertext),
            "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            "plaintext_bytes": len(plaintext),
        }
        unsigned = evidence_receiver.receiver.exchange._upload_report_unsigned(
            request=request,
            descriptor=descriptor,
        )
        report = {
            **unsigned,
            "report_sha256": evidence_receiver.receiver.exchange.sha256_bytes(
                evidence_receiver.receiver.exchange.canonical_json_bytes(unsigned)
            ),
        }
        return _private(self.inputs / "upload-report.json", _canonical(report)), report, plaintext, ciphertext

    def _plan(self, report_path: Path) -> object:
        base, binding_payload = evidence_receiver._receive_plan(
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
        )
        return evidence_receiver.EvidenceReceivePlan(
            receive_plan=base,
            campaign_binding_payload=binding_payload,
            canonical_release_tree_sha256="e" * 64,
            controller_public_key_base64="controller-public-fixture",
            source_signing_public_key_base64="source-public",
            controller_delivery_envelope_payload=b"LOCAL DELIVERY\n",
            signer_enrollment_certificate_payload=b"LOCAL CERTIFICATE\n",
            static_assets_provenance_payload=b"LOCAL STATIC\n",
            verification_time="2026-07-30T12:00:00Z",
        )

    def _controller_signing_authority(self, campaign: object) -> object:
        return SimpleNamespace(
            signer=None,
            signing_key=SimpleNamespace(
                public_key_base64="controller-public",
                key_id="ed25519-sha256:" + "a" * 64,
                receipt_sha256="b" * 64,
            ),
            campaign_binding=SimpleNamespace(
                campaign_id=campaign.campaign_id,
                application_release_sha=campaign.application_release_sha,
                application_release_tree=campaign.application_release_tree,
                expected_alembic_revision=campaign.expected_alembic_revision,
                control_commit=campaign.control_commit,
                control_tree=campaign.control_tree,
                binding_sha256=campaign.binding_sha256,
            ),
        )

    def test_exact_route_accepts_source_evidence_and_rejects_other_fi_objects(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report()
        plan, binding_payload = evidence_receiver._receive_plan(
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
        )
        self.assertEqual(evidence_receiver.SOURCE_EVIDENCE_PAYLOAD_NAME, plan.kind.plaintext_name)
        self.assertEqual(evidence_receiver.SOURCE_EVIDENCE_READBACK_SCHEMA, plan.kind.readback_schema)
        self.assertEqual(_canonical(json.loads(binding_payload)), binding_payload)

        bad_path, _report, _plaintext, _ciphertext = self._write_report(
            object_kind=evidence_receiver.receiver.contract.RAW_APP_IMAGE_OBJECT_KIND,
        )
        with self.assertRaisesRegex(evidence_receiver.SourceEvidenceReceiveError, "only the exact WebApp-FI-to-controller"):
            evidence_receiver._receive_plan(
                controller_config=self.controller_config,
                campaign_binding_path=self.binding,
                upload_report_path=bad_path,
            )

    def test_shared_capacity_margin_covers_evidence_composite_proofs(self) -> None:
        self.assertGreaterEqual(
            evidence_receiver.receiver.CAPACITY_MARGIN_BYTES,
            evidence_receiver.COMPOSITE_PROOF_RESERVE_BYTES,
        )

    def test_insufficient_composite_proof_margin_blocks_before_s3_get(self) -> None:
        report_path, report, _plaintext, ciphertext = self._write_report()
        plan = self._plan(report_path)
        descriptor = report["object"]
        assert isinstance(descriptor, dict)
        client = FakeS3(
            key=str(descriptor["object_key"]),
            version_id=str(descriptor["version_id"]),
            ciphertext=ciphertext,
            metadata=evidence_receiver.receiver.transport._ciphertext_metadata(
                str(descriptor["ciphertext_sha256"]),
                evidence_receiver.receiver.contract.SINGLE_MODE,
            ),
        )
        with mock.patch.object(
            evidence_receiver.receiver,
            "CAPACITY_MARGIN_BYTES",
            evidence_receiver.COMPOSITE_PROOF_RESERVE_BYTES - 1,
        ):
            with self.assertRaisesRegex(evidence_receiver.SourceEvidenceReceiveError, "composite proofs"):
                evidence_receiver.receive_source_evidence(client, plan=plan)

        self.assertEqual([], client.get_calls)

    def test_receive_reads_exact_version_and_composes_controller_proofs_only_from_local_inputs(self) -> None:
        report_path, report, plaintext, ciphertext = self._write_report()
        plan = self._plan(report_path)
        descriptor = report["object"]
        assert isinstance(descriptor, dict)
        client = FakeS3(
            key=str(descriptor["object_key"]),
            version_id=str(descriptor["version_id"]),
            ciphertext=ciphertext,
            metadata=evidence_receiver.receiver.transport._ciphertext_metadata(
                str(descriptor["ciphertext_sha256"]),
                evidence_receiver.receiver.contract.SINGLE_MODE,
            ),
        )
        fi_role = b"FI ROLE PROOF\n"
        fi_image = b"FI IMAGE PROOF\n"
        local_delivery = plan.controller_delivery_envelope_payload
        local_certificate = plan.signer_enrollment_certificate_payload
        local_static = plan.static_assets_provenance_payload
        verified = (
            {
                evidence_receiver.FI_ROLE_ATTESTATION_NAME: fi_role,
                evidence_receiver.FI_IMAGE_EXPORT_NAME: fi_image,
                evidence_receiver.CONTROLLER_DELIVERY_NAME: local_delivery,
                evidence_receiver.CONTROLLER_CERTIFICATE_NAME: local_certificate,
                evidence_receiver.CONTROLLER_STATIC_NAME: local_static,
            },
            {
                "evidence_id": "evidence-object-fixture",
                "source_signing_key_id": "ed25519-sha256:" + "a" * 64,
                "controller_key_id": "ed25519-sha256:" + "b" * 64,
                "proof_sha256": {},
            },
        )

        def decrypt(_plan: object, _ciphertext: Path, output: Path) -> None:
            output.write_bytes(plaintext)
            output.chmod(0o600)

        original_capacity = evidence_receiver.receiver._capacity_preflight

        def capacity(*args: object, **kwargs: object) -> object:
            client.events.append("capacity")
            return original_capacity(*args, **kwargs)

        with (
            mock.patch.object(evidence_receiver, "_verified_evidence_proofs", return_value=verified),
            mock.patch.object(evidence_receiver.receiver, "_capacity_preflight", side_effect=capacity),
        ):
            result = evidence_receiver.receive_source_evidence(client, plan=plan, decryptor=decrypt)

        candidate = Path(str(result["candidate_directory"]))
        proof_directory = Path(str(result["proof_directory"]))
        self.assertEqual(plaintext, (candidate / evidence_receiver.SOURCE_EVIDENCE_PAYLOAD_NAME).read_bytes())
        readback = json.loads(Path(str(result["readback_record"])).read_bytes())
        self.assertEqual(evidence_receiver.SOURCE_EVIDENCE_READBACK_SCHEMA, readback["schema"])
        self.assertEqual(fi_role, (proof_directory / evidence_receiver.FI_ROLE_ATTESTATION_NAME).read_bytes())
        self.assertEqual(fi_image, (proof_directory / evidence_receiver.FI_IMAGE_EXPORT_NAME).read_bytes())
        self.assertEqual(local_delivery, (proof_directory / evidence_receiver.CONTROLLER_DELIVERY_NAME).read_bytes())
        self.assertEqual(local_certificate, (proof_directory / evidence_receiver.CONTROLLER_CERTIFICATE_NAME).read_bytes())
        self.assertEqual(local_static, (proof_directory / evidence_receiver.CONTROLLER_STATIC_NAME).read_bytes())
        receipt = json.loads(Path(str(result["consumption_receipt"])).read_bytes())
        self.assertEqual("webapp_fi_source_evidence_envelope", receipt["proofs"][evidence_receiver.FI_ROLE_ATTESTATION_NAME]["origin"])
        self.assertEqual("controller_local_input", receipt["proofs"][evidence_receiver.CONTROLLER_DELIVERY_NAME]["origin"])
        self.assertEqual(
            [{"Bucket": self.policy.bucket, "Key": descriptor["object_key"], "VersionId": descriptor["version_id"]}],
            client.get_calls,
        )
        self.assertLess(client.events.index("capacity"), client.events.index("get"))
        self.assertEqual(0o700, stat.S_IMODE(proof_directory.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE((proof_directory / evidence_receiver.CONTROLLER_CERTIFICATE_NAME).stat().st_mode))

    def test_expander_uses_separate_controller_bytes_after_authority_validation(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report()
        plan = self._plan(report_path)
        role_payload = b"FI-ONLY-ROLE\n"
        image_payload = b"FI-ONLY-IMAGE\n"
        with (
            mock.patch.object(
                evidence_receiver.source_evidence,
                "verify_source_evidence_envelope_payload",
                return_value={
                    "role_attestation_payload": role_payload,
                    "image_export_receipt_payload": image_payload,
                    "role_attestation_sha256": hashlib.sha256(role_payload).hexdigest(),
                    "image_export_receipt_sha256": hashlib.sha256(image_payload).hexdigest(),
                    "evidence_id": "evidence-object-fixture",
                    "source_signing_key_id": "ed25519-sha256:" + "a" * 64,
                },
            ) as verify_envelope,
            mock.patch.object(
                evidence_receiver.provenance,
                "_parse",
                return_value={
                    "canonical_release_tree_sha256": "e" * 64,
                    "active_application_image": {"image_id": "sha256:" + "f" * 64, "image_reference": "fixture:app"},
                },
            ),
            mock.patch.object(evidence_receiver.provenance, "_sha", side_effect=lambda value, **_kwargs: value),
            mock.patch.object(evidence_receiver.provenance, "_controller_delivery_envelope", return_value={"package_id": "fixture"}),
            mock.patch.object(
                evidence_receiver.provenance,
                "_signer_enrollment_certificate",
                return_value={"source_signing_public_key_base64": "source-public", "controller_key_id": "ed25519-sha256:" + "b" * 64},
            ),
            mock.patch.object(
                evidence_receiver.provenance,
                "verify_webapp_fi_source_authority_payloads",
                return_value={
                    "proof_sha256": {
                        "source_role_attestation": hashlib.sha256(role_payload).hexdigest(),
                        "image_export_receipt": hashlib.sha256(image_payload).hexdigest(),
                    }
                },
            ),
        ):
            proofs, _metadata = evidence_receiver._verified_evidence_proofs(plan, b"FI ENVELOPE\n")

        self.assertEqual(
            "source-public",
            verify_envelope.call_args.kwargs["pinned_source_signing_public_key_base64"],
        )
        self.assertEqual(role_payload, proofs[evidence_receiver.FI_ROLE_ATTESTATION_NAME])
        self.assertEqual(image_payload, proofs[evidence_receiver.FI_IMAGE_EXPORT_NAME])
        self.assertEqual(plan.controller_delivery_envelope_payload, proofs[evidence_receiver.CONTROLLER_DELIVERY_NAME])
        self.assertEqual(plan.signer_enrollment_certificate_payload, proofs[evidence_receiver.CONTROLLER_CERTIFICATE_NAME])
        self.assertEqual(plan.static_assets_provenance_payload, proofs[evidence_receiver.CONTROLLER_STATIC_NAME])

    def test_expander_rejects_signed_evidence_id_that_differs_from_exact_object_request(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report()
        plan = self._plan(report_path)
        with mock.patch.object(
            evidence_receiver.source_evidence,
            "verify_source_evidence_envelope_payload",
            return_value={
                "role_attestation_payload": b"FI-ONLY-ROLE\n",
                "image_export_receipt_payload": b"FI-ONLY-IMAGE\n",
                "role_attestation_sha256": "a" * 64,
                "image_export_receipt_sha256": "b" * 64,
                "evidence_id": "different-evidence-id",
                "source_signing_key_id": "ed25519-sha256:" + "c" * 64,
            },
        ):
            with self.assertRaisesRegex(
                evidence_receiver.SourceEvidenceReceiveError,
                "cannot be rooted in the independently controller-local authority",
            ):
                evidence_receiver._verified_evidence_proofs(plan, b"FI ENVELOPE\n")

    def test_prepare_validates_controller_certificate_and_pins_its_source_key(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report()
        receive_plan, binding_payload = evidence_receiver._receive_plan(
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
        )
        delivery = {"package_id": "fixture", "sha256": "a" * 64, "object": {}}
        certificate = {"source_signing_public_key_base64": "source-public"}
        with (
            mock.patch.object(evidence_receiver, "_receive_plan", return_value=(receive_plan, binding_payload)),
            mock.patch.object(evidence_receiver, "_prepared_package_identity", return_value="e" * 64),
            mock.patch.object(
                evidence_receiver,
                "_load_campaign_bound_controller_signer",
                return_value=self._controller_signing_authority(receive_plan.campaign_binding),
            ),
            mock.patch.object(
                evidence_receiver,
                "_read_root_private_file",
                side_effect=[b"LOCAL DELIVERY\n", b"LOCAL CERTIFICATE\n", b"LOCAL STATIC\n"],
            ),
            mock.patch.object(evidence_receiver.provenance, "_timestamp"),
            mock.patch.object(evidence_receiver.provenance, "_controller_delivery_envelope", return_value=delivery),
            mock.patch.object(
                evidence_receiver.provenance,
                "_parse",
                return_value={"source_signing_public_key_base64": "source-public"},
            ),
            mock.patch.object(evidence_receiver.provenance, "_key", return_value=b"x" * 32),
            mock.patch.object(
                evidence_receiver.provenance,
                "_signer_enrollment_certificate",
                return_value=certificate,
            ) as verify_certificate,
            mock.patch.object(evidence_receiver.provenance, "_static_assets_provenance"),
        ):
            plan = evidence_receiver.prepare_source_evidence_receive(
                controller_config=self.controller_config,
                campaign_binding_path=self.binding,
                upload_report_path=report_path,
                source_adoption_package_directory=self.root / "controller-package",
                source_adoption_preparation_receipt=self.root / "controller-package" / "receipt.json",
                controller_delivery_envelope=self.inputs / "delivery.json",
                signer_enrollment_certificate=self.inputs / "certificate.json",
                static_assets_provenance=self.inputs / "static.json",
                verification_time="2026-07-30T12:00:00Z",
            )

        self.assertEqual("source-public", plan.source_signing_public_key_base64)
        self.assertEqual(
            "source-public",
            verify_certificate.call_args.kwargs["expected_source_signing_public_key_base64"],
        )
        self.assertIs(delivery, verify_certificate.call_args.kwargs["expected_delivery"])

    def test_main_blocks_before_creating_s3_client_when_full_local_plan_fails(self) -> None:
        arguments = [
            "--config", "/etc/controller-config.json",
            "--campaign-binding", "/etc/campaign-binding.json",
            "--upload-report", "/etc/upload-report.json",
            "--source-adoption-package-directory", "/srv/controller-package",
            "--source-adoption-preparation-receipt", "/srv/controller-package/receipt.json",
            "--controller-delivery-envelope", "/etc/delivery.json",
            "--signer-enrollment-certificate", "/etc/certificate.json",
            "--static-assets-provenance", "/etc/static.json",
            "--verification-time", "2026-07-30T12:00:00Z",
            "--apply",
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(evidence_receiver.receiver.transport, "load_controller_config", return_value=self.controller_config),
            mock.patch.object(
                evidence_receiver,
                "prepare_source_evidence_receive",
                side_effect=evidence_receiver.SourceEvidenceReceiveError("local proof is invalid"),
            ),
            mock.patch.object(evidence_receiver.receiver.transport, "create_s3_client") as create_client,
            mock.patch.object(sys, "stderr", stderr),
        ):
            self.assertEqual(2, evidence_receiver.main(arguments))

        create_client.assert_not_called()
        self.assertEqual("blocked", json.loads(stderr.getvalue())["status"])

    def test_main_normalizes_transport_config_failure_without_creating_s3_client(self) -> None:
        arguments = [
            "--config", "/etc/controller-config.json",
            "--campaign-binding", "/etc/campaign-binding.json",
            "--upload-report", "/etc/upload-report.json",
            "--source-adoption-package-directory", "/srv/controller-package",
            "--source-adoption-preparation-receipt", "/srv/controller-package/receipt.json",
            "--controller-delivery-envelope", "/etc/delivery.json",
            "--signer-enrollment-certificate", "/etc/certificate.json",
            "--static-assets-provenance", "/etc/static.json",
            "--verification-time", "2026-07-30T12:00:00Z",
            "--apply",
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(
                evidence_receiver.receiver.transport,
                "load_controller_config",
                side_effect=evidence_receiver.receiver.transport.SourceTransportError("invalid config"),
            ),
            mock.patch.object(evidence_receiver.receiver.transport, "create_s3_client") as create_client,
            mock.patch.object(sys, "stderr", stderr),
        ):
            self.assertEqual(2, evidence_receiver.main(arguments))

        create_client.assert_not_called()
        self.assertEqual("blocked", json.loads(stderr.getvalue())["status"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
