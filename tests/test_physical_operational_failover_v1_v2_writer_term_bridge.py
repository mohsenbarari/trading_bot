from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import pickle
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1_v2_writer_term_bridge as subject


NOW = datetime(2034, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
RELEASE = "a" * 40


def _id(prefix: str) -> str:
    return prefix + "-" + "x" * 24


def _sha(letter: str) -> str:
    return letter * 64


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class PhysicalOperationalFailoverV1V2WriterTermBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.keys = [Ed25519PrivateKey.generate() for _ in range(10)]
        self.config = subject.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig(
            enabled=True,
            cluster_id="gold-trade-three-site-prod",
            local_site="webapp_fi",
            release_sha=RELEASE,
            generation_id=_id("generation"),
            expected_v1_revalidator_configuration_sha256=_sha("a"),
            expected_v2_strict_writer_configuration_sha256=_sha("b"),
            expected_v2_context_sha256=_sha("c"),
            expected_v2_activation_mode="normal_fi_writer",
            expected_v2_stream_generation_id="stream-gen-0001",
            bridge_signer_public_key=_public(self.keys[0]),
            bridge_signer_key_id=_id("bridge-key"),
            v1_current_term_signer_public_key=_public(self.keys[1]),
            v1_promotion_signer_public_key=_public(self.keys[2]),
            v2_witness_public_key=_public(self.keys[3]),
            v2_fi_outbox_public_key=_public(self.keys[4]),
            v2_ir_recovery_exporter_public_key=_public(self.keys[5]),
            v2_ir_durable_assertion_public_key=_public(self.keys[6]),
            v2_remote_source_public_key=_public(self.keys[7]),
            v2_remote_destination_public_key=_public(self.keys[8]),
            v2_local_commit_signer_public_key=_public(self.keys[9]),
            safety_margin_seconds=5,
            maximum_certificate_age_seconds=30,
        )

    def intent(self):
        end = NOW + timedelta(seconds=25)
        provenance = subject.PhysicalOperationalFailoverV1V2WriterTermBridgeCurrentTermProvenance(
            attestation_sha256=_sha("d"), attestation_id=_id("v1-attestation"), revalidation_id=_id("revalidation"), configuration_sha256=_sha("a"), reservation_id=_id("reservation"), request_sha256=_sha("e"), ledger_schema="gold-trade-v1-witness-ledger-v1", ledger_version=9, ledger_head_sha256=_sha("f"), ledger_entry_sha256=_sha("f"), ledger_previous_head_sha256="0" * 64, ledger_state_sha256=_sha("1"), ledger_phase="fi-active", active_term_sha256=_sha("2"), holder_site="webapp_fi", writer_epoch=17, writer_lease_id=_id("lease"), witness_transition_id=_id("witness-transition"), witnessed_term_proof_sha256=_sha("3"), attestation_issued_at=NOW - timedelta(seconds=1), attestation_expires_at=end, term_issued_at=NOW - timedelta(seconds=2), term_expires_at=end,
        )
        admission = subject.PhysicalOperationalFailoverV1V2WriterTermBridgeV1Admission(
            cluster_id=self.config.cluster_id or "", local_site="webapp_fi", release_sha=RELEASE, generation_id=self.config.generation_id or "", operation_kind="transaction_commit", prior_revision=7, next_revision=8, fence_generation=4, evidence_id=provenance.attestation_id, revalidation_id=provenance.revalidation_id, writer_epoch=17, writer_lease_id=provenance.writer_lease_id, opened_at=NOW, admitted_at=NOW, term_evidence_issued_at=provenance.attestation_issued_at, term_evidence_expires_at=provenance.attestation_expires_at,
        )
        v2 = subject.PhysicalOperationalFailoverV1V2WriterTermBridgeV2Instruction(
            strict_schema="gold-trade-v2-strict-v2", configuration_sha256=_sha("b"), atomic_commit_boundary="root-owned-atomic-local-response", commit_id="v2-witness-strict-writer-" + _sha("4"), attestation_sha256=_sha("5"), context_sha256=_sha("c"), writer_holder_site="webapp_fi", writer_epoch=17, writer_lease_id=provenance.writer_lease_id, witnessed_term_proof_sha256=provenance.witnessed_term_proof_sha256, witness_transition_id=provenance.witness_transition_id, activation_mode="normal_fi_writer", activation_stream_generation_id="stream-gen-0001", activation_route_artifact_sha256=_sha("6"), activation_source_cutover_attestation_sha256=_sha("7"), activation_receiver_permit_sha256=_sha("8"), attestation_issued_at=NOW - timedelta(seconds=1), attestation_expires_at=end, term_issued_at=provenance.term_issued_at, term_expires_at=provenance.term_expires_at,
        )
        return subject.PhysicalOperationalFailoverV1V2WriterTermBridgeIntent(admission, provenance, v2)

    def cert(self, intent=None):
        return subject.issue_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(
            config=self.config, intent=self.intent() if intent is None else intent, private_key=self.keys[0], now=NOW, expires_at=NOW + timedelta(seconds=20)
        )

    def parent(self, intent=None):
        selected = self.intent() if intent is None else intent
        a = selected.v1_admission
        return subject.PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt(
            commit_id=_id("parent-commit"), commit_sha256=_sha("9"), receipt_sha256=_sha("a"), cluster_id=a.cluster_id, local_site=a.local_site, release_sha=a.release_sha, generation_id=a.generation_id, prior_revision=a.prior_revision, next_revision=a.next_revision, fence_generation=a.fence_generation, writer_epoch=a.writer_epoch, writer_lease_id=a.writer_lease_id, evidence_id=a.evidence_id, revalidation_id=a.revalidation_id, admitted_at=a.admitted_at,
        )

    def test_preissued_certificate_binds_exact_parent_without_signer_work(self) -> None:
        verified = subject.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(value=self.cert(), config=self.config, now=NOW)
        bound = subject.bind_physical_operational_failover_v1_v2_writer_term_bridge_parent(certificate=verified, parent=self.parent(), config=self.config, now=NOW)
        projection = subject.require_bound_physical_operational_failover_v1_v2_writer_term_bridge_intent(value=bound, config=self.config, now=NOW)
        self.assertEqual(projection.parent_commit_id, self.parent().commit_id)
        self.assertEqual(len(projection.parent_binding_sha256), 64)
        self.assertEqual(projection.certificate_sha256, verified.certificate_sha256)

    def test_verifier_rejects_resigned_unsafe_certificate_windows(self) -> None:
        def resign(value: dict[str, object]) -> bytes:
            unsigned = dict(value)
            unsigned.pop("signature_base64")
            signature = self.keys[0].sign(subject._DOMAIN + subject._canonical(unsigned, code="test"))
            unsigned["signature_base64"] = base64.b64encode(signature).decode("ascii")
            return subject._canonical(unsigned, code="test")

        too_long = json.loads(self.cert().decode("ascii"))
        too_long["expires_at"] = (NOW + timedelta(seconds=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1V2WriterTermBridgeError, "CERTIFICATE_WINDOW_UNSAFE"):
            subject.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(value=resign(too_long), config=self.config, now=NOW)

        past_v1_v2_evidence = json.loads(self.cert().decode("ascii"))
        past_v1_v2_evidence["expires_at"] = (NOW + timedelta(seconds=26)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1V2WriterTermBridgeError, "CERTIFICATE_WINDOW_UNSAFE"):
            subject.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(value=resign(past_v1_v2_evidence), config=self.config, now=NOW)

    def test_signature_key_separation_and_expiry_fail_closed(self) -> None:
        raw = bytearray(self.cert())
        raw[-2] ^= 1
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1V2WriterTermBridgeError, "SIGNATURE|INVALID|MISMATCH"):
            subject.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(value=bytes(raw), config=self.config, now=NOW)
        collision = replace(self.config, v2_witness_public_key=self.config.bridge_signer_public_key)
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1V2WriterTermBridgeError, "KEY_ROLE_COLLISION"):
            self.cert()
            subject.issue_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(config=collision, intent=self.intent(), private_key=self.keys[0], now=NOW, expires_at=NOW + timedelta(seconds=20))
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1V2WriterTermBridgeError, "STALE"):
            subject.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(value=self.cert(), config=self.config, now=NOW + timedelta(seconds=16))

    def test_resigned_intent_tampering_and_parent_mismatch_fail_closed(self) -> None:
        decoded = json.loads(self.cert().decode("ascii"))
        decoded["intent"]["v2_instruction"]["writer_epoch"] = 18
        decoded["intent_sha256"] = hashlib.sha256(subject._canonical(decoded["intent"], code="test")).hexdigest()
        decoded["certificate_id"] = "v1-v2-writer-term-bridge-cert-" + decoded["intent_sha256"]
        decoded.pop("signature_base64")
        signature = self.keys[0].sign(subject._DOMAIN + subject._canonical(decoded, code="test"))
        decoded["signature_base64"] = base64.b64encode(signature).decode("ascii")
        tampered = subject._canonical(decoded, code="test")
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1V2WriterTermBridgeError, "CROSS_PIN|MISMATCH"):
            subject.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(value=tampered, config=self.config, now=NOW)
        v1_tampered = json.loads(self.cert().decode("ascii"))
        v1_tampered["intent"]["v1_admission"]["evidence_id"] = _id("wrong-evidence")
        v1_tampered["intent_sha256"] = hashlib.sha256(subject._canonical(v1_tampered["intent"], code="test")).hexdigest()
        v1_tampered["certificate_id"] = "v1-v2-writer-term-bridge-cert-" + v1_tampered["intent_sha256"]
        v1_tampered.pop("signature_base64")
        v1_signature = self.keys[0].sign(subject._DOMAIN + subject._canonical(v1_tampered, code="test"))
        v1_tampered["signature_base64"] = base64.b64encode(v1_signature).decode("ascii")
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1V2WriterTermBridgeError, "CROSS_PIN|MISMATCH"):
            subject.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(value=subject._canonical(v1_tampered, code="test"), config=self.config, now=NOW)
        verified = subject.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(value=self.cert(), config=self.config, now=NOW)
        bad_parent = replace(self.parent(), writer_epoch=18)
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1V2WriterTermBridgeError, "PARENT_INTENT_MISMATCH"):
            subject.bind_physical_operational_failover_v1_v2_writer_term_bridge_parent(certificate=verified, parent=bad_parent, config=self.config, now=NOW)

    def test_short_canonical_writer_lease_is_accepted_but_generic_alias_is_rejected(self) -> None:
        """Lease fields use the shared V1/V2 grammar, not generic IDs."""

        initial = self.intent()
        short_lease = "writer-lease-73"
        short = replace(
            initial,
            v1_admission=replace(initial.v1_admission, writer_lease_id=short_lease),
            v1_current_term=replace(
                initial.v1_current_term,
                writer_lease_id=short_lease,
            ),
            v2_instruction=replace(initial.v2_instruction, writer_lease_id=short_lease),
        )
        raw = self.cert(short)
        verified = subject.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(
            value=raw,
            config=self.config,
            now=NOW,
        )
        bound = subject.bind_physical_operational_failover_v1_v2_writer_term_bridge_parent(
            certificate=verified,
            parent=self.parent(short),
            config=self.config,
            now=NOW,
        )
        projection = subject.project_bound_physical_operational_failover_v1_v2_writer_term_bridge_intent(
            value=bound,
            config=self.config,
            now=NOW,
        )
        self.assertEqual(short_lease, projection.v1_admission.writer_lease_id)
        self.assertEqual(short_lease, projection.v2_instruction.writer_lease_id)

        invalid_lease = "writer:lease-000073"
        invalid = replace(
            initial,
            v1_admission=replace(initial.v1_admission, writer_lease_id=invalid_lease),
            v1_current_term=replace(
                initial.v1_current_term,
                writer_lease_id=invalid_lease,
            ),
            v2_instruction=replace(
                initial.v2_instruction,
                writer_lease_id=invalid_lease,
            ),
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeError,
            "INTENT_INVALID",
        ):
            self.cert(invalid)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeError,
            "PARENT_INVALID",
        ):
            subject.bind_physical_operational_failover_v1_v2_writer_term_bridge_parent(
                certificate=verified,
                parent=replace(self.parent(short), writer_lease_id=invalid_lease),
                config=self.config,
                now=NOW,
            )

    def test_opaque_handles_cannot_be_serialized(self) -> None:
        verified = subject.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(value=self.cert(), config=self.config, now=NOW)
        bound = subject.bind_physical_operational_failover_v1_v2_writer_term_bridge_parent(certificate=verified, parent=self.parent(), config=self.config, now=NOW)
        with self.assertRaises(TypeError):
            pickle.dumps(verified)
        with self.assertRaises(TypeError):
            pickle.dumps(bound)


if __name__ == "__main__":
    unittest.main()
