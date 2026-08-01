"""Adversarial tests for durable-only aggregate admission into preflight gate."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import pickle
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_arvan_s3_four_role_live_iam_durable_admission_bridge as bridge
from core import physical_arvan_s3_four_role_live_iam_evidence as evidence
from core import physical_arvan_s3_four_role_live_iam_witness_ledger_runtime as runtime_module
from core import physical_ir_to_fi_object_storage_failback_preflight as failback


CAMPAIGN = "four-role-admission-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
NOW = datetime(2026, 7, 31, 16, 0, 0, tzinfo=timezone.utc)
NONCE = "1" * 64
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_four_role_live_iam_durable_admission_bridge.py"
)


def _public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _outcomes(role: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return (
        [{"operation": item, "outcome": "allowed"} for item in evidence._ROLE_ALLOWED[role]],
        [{"operation": item, "outcome": "denied"} for item in evidence._ROLE_DENIED[role]],
    )


class PhysicalArvanS3FourRoleLiveIamDurableAdmissionBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temporary.name)
        os.chmod(self.state_root, 0o700)
        self.witness = Ed25519PrivateKey.generate()
        self.signers = {
            "fi-publisher": Ed25519PrivateKey.generate(),
            "ir-receiver": Ed25519PrivateKey.generate(),
            "ir-publisher": Ed25519PrivateKey.generate(),
            "fi-receiver": Ed25519PrivateKey.generate(),
        }
        self.live_binding = self._binding(
            fi="5" * 64, ir_receiver="6" * 64, ir_publisher="7" * 64, fi_receiver="8" * 64
        )
        self.failback_binding = self._failback_binding(self.live_binding)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _binding(
        self, *, fi: str, ir_receiver: str, ir_publisher: str, fi_receiver: str
    ) -> evidence.PhysicalArvanS3FourRoleLiveIamEvidenceBinding:
        return evidence.build_physical_arvan_s3_four_role_live_iam_evidence_binding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            normal_route_scope_sha256="2" * 64,
            reverse_route_scope_sha256="3" * 64,
            four_role_binding_sha256="4" * 64,
            fi_publisher_identity_sha256=fi,
            ir_receiver_identity_sha256=ir_receiver,
            ir_publisher_identity_sha256=ir_publisher,
            fi_receiver_identity_sha256=fi_receiver,
            fi_publisher_signer_public_key=_public_key(self.signers["fi-publisher"]),
            ir_receiver_signer_public_key=_public_key(self.signers["ir-receiver"]),
            ir_publisher_signer_public_key=_public_key(self.signers["ir-publisher"]),
            fi_receiver_signer_public_key=_public_key(self.signers["fi-receiver"]),
        )

    @staticmethod
    def _failback_binding(
        value: evidence.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    ) -> failback.PhysicalIrToFiObjectStorageFailbackBinding:
        return failback.PhysicalIrToFiObjectStorageFailbackBinding(
            campaign_id=value.campaign_id,
            release_sha=value.release_sha,
            route_binding_sha256=value.four_role_binding_sha256,
            normal_route_scope_sha256=value.normal_route_scope_sha256,
            reverse_route_scope_sha256=value.reverse_route_scope_sha256,
            fi_publisher_identity_sha256=value.fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=value.ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=value.ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=value.fi_receiver_identity_sha256,
        )

    def _runtime(
        self, root: Path | None = None
    ) -> runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime:
        chosen = self.state_root if root is None else root
        return runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(
            runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
                state_root=chosen, evidence_binding=self.live_binding, enabled=True
            )
        )

    def _direction(
        self,
        *,
        publisher_role: str,
        permit: evidence.VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
        offset: int,
    ) -> tuple[
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
    ]:
        receiver_role = evidence._RECEIVER_BY_DIRECTION[evidence._DIRECTION_BY_PUBLISHER[publisher_role]]
        publisher_allowed, publisher_denied = _outcomes(publisher_role)
        receiver_allowed, receiver_denied = _outcomes(receiver_role)
        first = NOW + timedelta(seconds=offset)
        locator = evidence.make_physical_arvan_s3_live_iam_probe_locator(
            binding=self.live_binding,
            nonce=permit.nonce,
            publisher_role=publisher_role,
            object_version_id=f"version-{publisher_role}-{offset}",
            content_sha256=("a" if publisher_role == "fi-publisher" else "b") * 64,
            content_bytes=99 + offset,
        )
        publisher_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
            binding=self.live_binding,
            nonce_permit=permit,
            publisher_role=publisher_role,
            observed_at=first,
            probe_locator=locator,
            allowed_operation_outcomes=publisher_allowed,
            denied_operation_outcomes=publisher_denied,
            role_signer=self.signers[publisher_role],
        )
        publisher = evidence.verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
            publisher_raw,
            binding=self.live_binding,
            nonce_permit=permit,
            observed_at=first,
        )
        forwarded = first + timedelta(seconds=1)
        forward_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_witness_forward(
            binding=self.live_binding,
            nonce_permit=permit,
            publisher_observation=publisher,
            forwarded_at=forwarded,
            witness_signer=self.witness,
        )
        forward = evidence.verify_physical_arvan_s3_four_role_live_iam_witness_forward(
            forward_raw,
            binding=self.live_binding,
            nonce_permit=permit,
            witness_public_key=_public_key(self.witness),
            observed_at=forwarded,
        )
        received = forwarded + timedelta(seconds=1)
        receiver_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_receiver_observation(
            binding=self.live_binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=received,
            allowed_operation_outcomes=receiver_allowed,
            denied_operation_outcomes=receiver_denied,
            role_signer=self.signers[receiver_role],
        )
        receiver = evidence.verify_physical_arvan_s3_four_role_live_iam_receiver_observation(
            receiver_raw,
            binding=self.live_binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=received,
        )
        return publisher, forward, receiver

    def _commit(self, runtime: runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime) -> bytes:
        _state, permit_raw = runtime_module.issue_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce_permit(
            runtime=runtime,
            nonce=NONCE,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            witness_signer=self.witness,
        )
        permit = evidence.verify_physical_arvan_s3_four_role_live_iam_nonce_permit(
            permit_raw,
            binding=self.live_binding,
            witness_public_key=_public_key(self.witness),
            observed_at=NOW,
        )
        normal_publisher, normal_forward, normal_receiver = self._direction(
            publisher_role="fi-publisher", permit=permit, offset=1
        )
        reverse_publisher, reverse_forward, reverse_receiver = self._direction(
            publisher_role="ir-publisher", permit=permit, offset=10
        )
        _state, aggregate = runtime_module.seal_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate(
            runtime=runtime,
            nonce_permit=permit,
            normal_publisher_observation=normal_publisher,
            normal_witness_forward=normal_forward,
            normal_receiver_observation=normal_receiver,
            reverse_publisher_observation=reverse_publisher,
            reverse_witness_forward=reverse_forward,
            reverse_receiver_observation=reverse_receiver,
            committed_at=NOW + timedelta(seconds=20),
            witness_signer=self.witness,
        )
        return aggregate

    def _admit(
        self, runtime: runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime, aggregate: bytes
    ) -> bridge.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission:
        return bridge.admit_physical_arvan_s3_four_role_live_iam_durable_aggregate(
            runtime=runtime,
            aggregate=aggregate,
            witness_public_key=_public_key(self.witness),
            live_iam_binding=self.live_binding,
            failback_binding=self.failback_binding,
            observed_at=NOW + timedelta(seconds=21),
        )

    def test_valid_aggregate_is_admitted_only_via_durable_runtime(self) -> None:
        runtime = self._runtime()
        aggregate = self._commit(runtime)
        admission = self._admit(runtime, aggregate)
        self.assertEqual(2, admission.durable_ledger_sequence)
        self.assertEqual(admission.gate.aggregate_sha256, admission.aggregate_sha256)
        self.assertEqual(admission.durable_ledger_state.head_sha256, admission.durable_ledger_head_sha256)
        self.assertIs(
            admission,
            bridge.require_verified_physical_arvan_s3_four_role_live_iam_durable_admission(
                admission,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                observed_at=NOW + timedelta(seconds=22),
            ),
        )
        with self.assertRaises(TypeError):
            pickle.dumps(admission)

    def test_valid_raw_aggregate_outside_this_ledger_and_stale_aggregate_fail(self) -> None:
        source = self._runtime()
        aggregate = self._commit(source)
        isolated = tempfile.TemporaryDirectory()
        try:
            isolated_root = Path(isolated.name)
            os.chmod(isolated_root, 0o700)
            target = self._runtime(isolated_root)
            with self.assertRaisesRegex(bridge.PhysicalArvanS3FourRoleLiveIamDurableAdmissionError, "LEDGER_.*NONCE_COMMIT_MISSING"):
                self._admit(target, aggregate)
        finally:
            isolated.cleanup()
        with self.assertRaisesRegex(bridge.PhysicalArvanS3FourRoleLiveIamDurableAdmissionError, "LEDGER_.*AGGREGATE_STALE"):
            bridge.admit_physical_arvan_s3_four_role_live_iam_durable_aggregate(
                runtime=source,
                aggregate=aggregate,
                witness_public_key=_public_key(self.witness),
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                observed_at=NOW + timedelta(minutes=5),
            )

    def test_foreign_binding_and_stale_runtime_fail_before_admission(self) -> None:
        source = self._runtime()
        stale = self._runtime()
        aggregate = self._commit(source)
        foreign_live = self._binding(
            fi="a" * 64, ir_receiver="b" * 64, ir_publisher="c" * 64, fi_receiver="d" * 64
        )
        foreign_failback = self._failback_binding(foreign_live)
        with self.assertRaisesRegex(bridge.PhysicalArvanS3FourRoleLiveIamDurableAdmissionError, "GATE_"):
            bridge.admit_physical_arvan_s3_four_role_live_iam_durable_aggregate(
                runtime=source,
                aggregate=aggregate,
                witness_public_key=_public_key(self.witness),
                live_iam_binding=foreign_live,
                failback_binding=foreign_failback,
                observed_at=NOW + timedelta(seconds=21),
            )
        with self.assertRaisesRegex(bridge.PhysicalArvanS3FourRoleLiveIamDurableAdmissionError, "LEDGER_.*HEAD_ROLLBACK_OR_FORK"):
            self._admit(stale, aggregate)

    def test_forged_or_replaced_admission_receipt_fails_closed(self) -> None:
        runtime = self._runtime()
        admission = self._admit(runtime, self._commit(runtime))
        fake = bridge.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission(
            schema=admission.schema,
            gate=admission.gate,
            durable_ledger_state=admission.durable_ledger_state,
            aggregate_sha256=admission.aggregate_sha256,
            durable_ledger_head_sha256=admission.durable_ledger_head_sha256,
            durable_ledger_sequence=admission.durable_ledger_sequence,
            expires_at=admission.expires_at,
        )
        for candidate in (fake, replace(admission, aggregate_sha256="f" * 64)):
            with self.subTest(candidate=candidate):
                with self.assertRaises(bridge.PhysicalArvanS3FourRoleLiveIamDurableAdmissionError):
                    bridge.require_verified_physical_arvan_s3_four_role_live_iam_durable_admission(
                        candidate,
                        live_iam_binding=self.live_binding,
                        failback_binding=self.failback_binding,
                        observed_at=NOW + timedelta(seconds=22),
                    )

    def test_source_has_no_sdk_network_or_direct_filesystem_write_path(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imports
            & {"boto3", "botocore", "socket", "subprocess", "requests", "os", "pathlib", "urllib"}
        )
        self.assertNotIn("open(", source)
        self.assertNotIn("verify_physical_arvan_s3_four_role_live_iam_witness_aggregate(", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
