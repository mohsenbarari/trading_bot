"""Real-owner tests and reusable fixture for the isolated Gen2 ACK chain.

``Gen2WitnessedAckChainFixture`` deliberately mints all three positive inputs
through their actual owners: recovery evidence, portable Witness attestation,
and the Gen2 V1-bound strict response.  It is reusable by the later V4 test
without reaching into an owner implementation or substituting opaque doubles.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
import pickle
import unittest
from unittest.mock import patch
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_full_matrix_v2_gen2_witnessed_ack_chain as subject
from core import physical_operational_failover_v1_v2_writer_term_bridge as bridge
from core import physical_wal_v2_witness_roundtrip_contract as roundtrip
from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_response as bound
from core import physical_wal_v2_witness_roundtrip_strict_writer_response as legacy
from tests import test_physical_wal_v2_witness_roundtrip_contract as contract_tests
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _sha(letter: str) -> str:
    return letter * 64


def _id(prefix: str) -> str:
    return prefix + "-" + "x" * 24


@dataclass(frozen=True)
class _Term:
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    proof_sha256: str
    witness_transition_id: str


class Gen2WitnessedAckChainFixture:
    """Public pure fixture that creates a true Gen2 owner-minted ACK chain."""

    def setUp(self) -> None:
        # All upstream signed artifacts are intentionally minted at this
        # narrow fixture clock.  Consumers must use it for fresh owner
        # revalidation; substituting an unrelated later test clock should
        # remain a genuine expiry failure, not be papered over by a patch.
        self.now = NOW
        self.contract = contract_tests.PhysicalWalV2WitnessRoundtripContractTests(
            "runTest"
        )
        self.contract.setUp()
        _certificate, _envelope, assertion, _issued = self.contract._full_chain()
        raw_attestation = self.contract._attestation(assertion)
        self.attestation = roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            raw_attestation,
            config=self.contract.config,
            now=NOW,
        )
        self.assertion = assertion
        self.local_commit_key = Ed25519PrivateKey.generate()
        self.legacy_config = (
            legacy.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig(
                roundtrip_config=self.contract.config,
                local_commit_signer_public_key=_public(self.local_commit_key),
                enabled=True,
                maximum_evidence_age_seconds=45,
            )
        )
        self.term = _Term(
            holder_site=self.attestation.writer_holder_site,
            writer_epoch=self.attestation.writer_epoch,
            writer_lease_id=self.attestation.writer_lease_id,
            proof_sha256=self.attestation.witnessed_term_proof_sha256,
            witness_transition_id=self.attestation.witness_transition_id,
        )
        self.activation = object()
        self.live = legacy._LiveActivationFacts(
            mode=self.attestation.activation_mode,
            stream_generation_id=self.attestation.activation_stream_generation_id,
            route_artifact_sha256=self.attestation.activation_route_artifact_sha256,
            source_cutover_attestation_sha256=(
                self.attestation.activation_source_cutover_attestation_sha256
            ),
            receiver_permit_sha256=self.attestation.activation_receiver_permit_sha256,
            witness_transition_id=self.attestation.witness_transition_id,
        )
        with self._legacy_live_clock():
            self.legacy_prepared = (
                legacy.prepare_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    config=self.legacy_config,
                    attestation=self.attestation,
                    witnessed_term=self.term,
                    activation=self.activation,
                )
            )
        self.bridge_config = self._bridge_config(self.legacy_prepared.instruction)
        self.bound_config = (
            bound.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig(
                legacy_response_config=self.legacy_config,
                bridge_config=self.bridge_config,
                enabled=True,
                maximum_evidence_age_seconds=15,
            )
        )
        self.bridge_bound = self._bridge_bound(self.legacy_prepared.instruction)
        with self._all_owner_clocks():
            gen2_prepared = (
                bound.prepare_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
                    config=self.bound_config,
                    v2_prepared=self.legacy_prepared,
                )
            )
            self.gen2_bound = (
                bound.bind_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
                    gen2_prepared,
                    bridge_bound=self.bridge_bound,
                    config=self.bound_config,
                )
            )
            receipt = (
                bound.sign_bound_physical_wal_v2_witness_roundtrip_strict_writer_runtime_receipt(
                    self.gen2_bound,
                    config=self.bound_config,
                    local_commit_private_key=self.local_commit_key,
                    local_commit_record_id="gen2-local-commit-000001",
                    local_response_id="gen2-local-response-000001",
                    committed_at=NOW,
                )
            )
            self.gen2_observation = (
                bound.finalize_bound_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    self.gen2_bound,
                    config=self.bound_config,
                    runtime_receipt=receipt,
                )
            )
        self.config = subject.PhysicalFullMatrixV2Gen2WitnessedAckChainConfig(
            roundtrip_config=self.contract.config,
            bound_response_config=self.bound_config,
            enabled=True,
            maximum_evidence_age_seconds=10,
        )
        self.inputs = subject.PhysicalFullMatrixV2Gen2WitnessedAckChainInputs(
            recovery_evidence=self.contract.fixture.target_recovery,
            witness_roundtrip_attestation=self.attestation,
            bound_strict_writer_response=self.gen2_observation,
        )

    def tearDown(self) -> None:
        self.contract.tearDown()

    @contextmanager
    def _legacy_live_clock(self, *, now=NOW):
        with (
            patch.object(legacy, "_trusted_now", return_value=now),
            patch.object(
                legacy,
                "_live_activation_facts",
                return_value=(self.term, self.activation, self.live),
            ),
        ):
            yield

    @contextmanager
    def _all_owner_clocks(self, *, now=NOW):
        with self._legacy_live_clock(now=now), patch.object(
            bound, "_trusted_now", return_value=now
        ):
            yield

    def _bridge_config(
        self,
        instruction: legacy.PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
    ) -> bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig:
        keys = [Ed25519PrivateKey.generate() for _ in range(9)]
        self.bridge_signer = keys[0]
        binding = self.contract.fixture.target_recovery.transfer_binding
        return bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig(
            enabled=True,
            cluster_id="gold-trade-three-site-prod",
            local_site=instruction.writer_holder_site,
            release_sha=binding.release_sha,
            generation_id=_id("generation"),
            expected_v1_revalidator_configuration_sha256=_sha("a"),
            expected_v2_strict_writer_configuration_sha256=instruction.configuration_sha256,
            expected_v2_context_sha256=instruction.context_sha256,
            expected_v2_activation_mode=instruction.activation_mode,
            expected_v2_stream_generation_id=instruction.activation_stream_generation_id,
            bridge_signer_public_key=_public(keys[0]),
            bridge_signer_key_id=_id("bridge-key"),
            v1_current_term_signer_public_key=_public(keys[1]),
            v1_promotion_signer_public_key=_public(keys[2]),
            v2_witness_public_key=_public(keys[3]),
            v2_fi_outbox_public_key=_public(keys[4]),
            v2_ir_recovery_exporter_public_key=_public(keys[5]),
            v2_ir_durable_assertion_public_key=_public(keys[6]),
            v2_remote_source_public_key=_public(keys[7]),
            v2_remote_destination_public_key=_public(keys[8]),
            v2_local_commit_signer_public_key=_public(self.local_commit_key),
            safety_margin_seconds=5,
            maximum_certificate_age_seconds=30,
        )

    def _bridge_bound(
        self,
        instruction: legacy.PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
    ) -> bridge.BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent:
        end = NOW + timedelta(seconds=30)
        current = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeCurrentTermProvenance(
            attestation_sha256=_sha("d"),
            attestation_id=_id("v1-attestation"),
            revalidation_id=_id("revalidation"),
            configuration_sha256=_sha("a"),
            reservation_id=_id("reservation"),
            request_sha256=_sha("e"),
            ledger_schema="gold-trade-v1-witness-ledger-v1",
            ledger_version=9,
            ledger_head_sha256=_sha("f"),
            ledger_entry_sha256=_sha("f"),
            ledger_previous_head_sha256="0" * 64,
            ledger_state_sha256=_sha("1"),
            ledger_phase="fi-active",
            active_term_sha256=_sha("2"),
            holder_site=instruction.writer_holder_site,
            writer_epoch=instruction.writer_epoch,
            writer_lease_id=instruction.writer_lease_id,
            witness_transition_id=instruction.witness_transition_id,
            witnessed_term_proof_sha256=instruction.witnessed_term_proof_sha256,
            attestation_issued_at=NOW - timedelta(seconds=1),
            attestation_expires_at=end,
            term_issued_at=NOW - timedelta(seconds=2),
            term_expires_at=end,
        )
        binding = self.contract.fixture.target_recovery.transfer_binding
        admission = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeV1Admission(
            cluster_id=self.bridge_config.cluster_id or "",
            local_site=instruction.writer_holder_site,
            release_sha=binding.release_sha,
            generation_id=self.bridge_config.generation_id or "",
            operation_kind="transaction_commit",
            prior_revision=7,
            next_revision=8,
            fence_generation=4,
            evidence_id=current.attestation_id,
            revalidation_id=current.revalidation_id,
            writer_epoch=instruction.writer_epoch,
            writer_lease_id=instruction.writer_lease_id,
            opened_at=NOW,
            admitted_at=NOW,
            term_evidence_issued_at=current.attestation_issued_at,
            term_evidence_expires_at=current.attestation_expires_at,
        )
        v2 = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeV2Instruction(
            strict_schema=instruction.schema,
            configuration_sha256=instruction.configuration_sha256,
            atomic_commit_boundary=instruction.atomic_commit_boundary,
            commit_id=instruction.commit_id,
            attestation_sha256=instruction.attestation_sha256,
            context_sha256=instruction.context_sha256,
            writer_holder_site=instruction.writer_holder_site,
            writer_epoch=instruction.writer_epoch,
            writer_lease_id=instruction.writer_lease_id,
            witnessed_term_proof_sha256=instruction.witnessed_term_proof_sha256,
            witness_transition_id=instruction.witness_transition_id,
            activation_mode=instruction.activation_mode,
            activation_stream_generation_id=instruction.activation_stream_generation_id,
            activation_route_artifact_sha256=instruction.activation_route_artifact_sha256,
            activation_source_cutover_attestation_sha256=(
                instruction.activation_source_cutover_attestation_sha256
            ),
            activation_receiver_permit_sha256=instruction.activation_receiver_permit_sha256,
            attestation_issued_at=NOW - timedelta(seconds=1),
            attestation_expires_at=end,
            term_issued_at=current.term_issued_at,
            term_expires_at=current.term_expires_at,
        )
        intent = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeIntent(
            v1_admission=admission,
            v1_current_term=current,
            v2_instruction=v2,
        )
        raw = bridge.issue_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(
            config=self.bridge_config,
            intent=intent,
            private_key=self.bridge_signer,
            now=NOW,
            expires_at=NOW + timedelta(seconds=20),
        )
        verified = bridge.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(
            value=raw,
            config=self.bridge_config,
            now=NOW,
        )
        parent = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt(
            commit_id=str(uuid.UUID("12345678-1234-4234-9234-123456789abc")),
            commit_sha256=_sha("9"),
            receipt_sha256=_sha("a"),
            cluster_id=admission.cluster_id,
            local_site=admission.local_site,
            release_sha=admission.release_sha,
            generation_id=admission.generation_id,
            prior_revision=admission.prior_revision,
            next_revision=admission.next_revision,
            fence_generation=admission.fence_generation,
            writer_epoch=admission.writer_epoch,
            writer_lease_id=admission.writer_lease_id,
            evidence_id=admission.evidence_id,
            revalidation_id=admission.revalidation_id,
            admitted_at=admission.admitted_at,
        )
        return bridge.bind_physical_operational_failover_v1_v2_writer_term_bridge_parent(
            certificate=verified,
            parent=parent,
            config=self.bridge_config,
            now=NOW,
        )

    def mint_chain(self, *, now=None, inputs=None, config=None):
        observed = self.now if now is None else now
        with self._all_owner_clocks(now=observed):
            return subject.mint_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
                config=config or self.config,
                inputs=inputs or self.inputs,
                now=observed,
            )

    def require_chain(self, value, *, now=None):
        observed = self.now if now is None else now
        with self._all_owner_clocks(now=observed):
            return subject.require_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
                value,
                config=self.config,
                now=observed,
            )

    def alternate_real_attestation(self):
        raw = self.contract._attestation(
            self.assertion,
            attestation_id="v2-witness-attestation-000002",
            attestation_nonce="U" * 22,
        )
        return roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            raw,
            config=self.contract.config,
            now=NOW,
        )


class PhysicalFullMatrixV2Gen2WitnessedAckChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Gen2WitnessedAckChainFixture()
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def test_real_owners_mint_cross_pin_and_project_every_gen2_surface(self) -> None:
        chain = self.fixture.mint_chain()
        self.assertIs(chain, self.fixture.require_chain(chain))
        self.assertFalse(chain.recovery_authorized)
        self.assertFalse(chain.promotion_authorized)
        self.assertFalse(chain.execution_authorized)
        instruction = self.fixture.gen2_observation.instruction
        expected = {
            "schema": "strict_instruction_schema",
            "configuration_sha256": "strict_configuration_sha256",
            "v2_base_configuration_sha256": "strict_v2_base_configuration_sha256",
            "atomic_commit_boundary": "strict_atomic_commit_boundary",
            "commit_id": "strict_commit_id",
            "v2_base_commit_id": "strict_v2_base_commit_id",
            "attestation_sha256": "roundtrip_attestation_sha256",
            "ir_durable_assertion_sha256": "ir_durable_assertion_sha256",
            "context_certificate_sha256": "context_certificate_sha256",
            "context_sha256": "context_sha256",
            "source_envelope_sha256": "source_envelope_sha256",
            "source_request_sha256": "source_request_sha256",
            "destination_receipt_sha256": "destination_receipt_sha256",
            "durable_ledger_entry_sha256": "durable_ledger_entry_sha256",
            "target_recovery_evidence_sha256": "target_recovery_evidence_sha256",
            "readback_attestation_sha256": "readback_attestation_sha256",
            "stage_receipt_sha256": "stage_receipt_sha256",
            "witness_sequence": "witness_sequence",
            "witness_ledger_entry_sha256": "witness_ledger_entry_sha256",
            "witness_ledger_previous_head_sha256": "witness_ledger_previous_head_sha256",
            "witness_ledger_binding_sha256": "witness_ledger_binding_sha256",
            "writer_holder_site": "writer_holder_site",
            "writer_epoch": "writer_epoch",
            "writer_lease_id": "writer_lease_id",
            "witnessed_term_proof_sha256": "witnessed_term_proof_sha256",
            "witness_transition_id": "witness_transition_id",
            "activation_mode": "activation_mode",
            "activation_stream_generation_id": "activation_stream_generation_id",
            "activation_route_artifact_sha256": "activation_route_artifact_sha256",
            "activation_source_cutover_attestation_sha256": "activation_source_cutover_attestation_sha256",
            "activation_receiver_permit_sha256": "activation_receiver_permit_sha256",
        }
        for source_name, target_name in expected.items():
            self.assertEqual(getattr(instruction, source_name), getattr(chain, target_name))
        for source_name in (
            "v1_parent_cluster_id",
            "v1_parent_local_site",
            "v1_parent_release_sha",
            "v1_parent_generation_id",
            "v1_writer_admission_commit_id",
            "v1_writer_admission_commit_sha256",
            "v1_writer_admission_receipt_sha256",
            "v1_parent_prior_revision",
            "v1_parent_next_revision",
            "v1_parent_fence_generation",
            "v1_parent_holder_site",
            "v1_parent_evidence_id",
            "v1_parent_revalidation_id",
            "v1_parent_writer_epoch",
            "v1_parent_writer_lease_id",
            "v1_parent_term_issued_at",
            "v1_parent_term_expires_at",
            "v1_parent_admitted_at",
            "v1_v2_writer_term_bridge_certificate_id",
            "v1_v2_writer_term_bridge_intent_sha256",
            "v1_v2_writer_term_bridge_certificate_sha256",
            "v1_v2_writer_term_bridge_parent_binding_sha256",
        ):
            self.assertEqual(
                getattr(instruction, source_name),
                getattr(chain, "strict_" + source_name),
            )
        attestation_expected = {
            "attestation_sha256": "roundtrip_attestation_sha256",
            "ir_durable_assertion_sha256": "ir_durable_assertion_sha256",
            "context_certificate_sha256": "context_certificate_sha256",
            "context_sha256": "context_sha256",
            "source_envelope_sha256": "source_envelope_sha256",
            "source_request_sha256": "source_request_sha256",
            "destination_receipt_sha256": "destination_receipt_sha256",
            "durable_ledger_entry_sha256": "durable_ledger_entry_sha256",
            "target_recovery_evidence_sha256": "target_recovery_evidence_sha256",
            "readback_attestation_sha256": "readback_attestation_sha256",
            "stage_receipt_sha256": "stage_receipt_sha256",
            "writer_holder_site": "writer_holder_site",
            "writer_epoch": "writer_epoch",
            "writer_lease_id": "writer_lease_id",
            "witnessed_term_proof_sha256": "witnessed_term_proof_sha256",
            "witness_transition_id": "witness_transition_id",
            "activation_mode": "activation_mode",
            "activation_stream_generation_id": "activation_stream_generation_id",
            "activation_route_artifact_sha256": "activation_route_artifact_sha256",
            "activation_source_cutover_attestation_sha256": "activation_source_cutover_attestation_sha256",
            "activation_receiver_permit_sha256": "activation_receiver_permit_sha256",
            "mediation_id": "witness_mediation_id",
            "witness_sequence": "witness_sequence",
            "witness_ledger_entry_sha256": "witness_ledger_entry_sha256",
            "witness_ledger_previous_head_sha256": "witness_ledger_previous_head_sha256",
            "witness_ledger_binding_sha256": "witness_ledger_binding_sha256",
            "attestation_id": "roundtrip_attestation_id",
            "attestation_nonce": "roundtrip_attestation_nonce",
            "issued_at": "roundtrip_attestation_issued_at",
            "expires_at": "roundtrip_attestation_expires_at",
        }
        for source_name, target_name in attestation_expected.items():
            self.assertEqual(
                getattr(self.fixture.attestation, source_name),
                getattr(chain, target_name),
            )
        with self.fixture._all_owner_clocks():
            projection = subject.project_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
                chain,
                config=self.fixture.config,
                now=NOW,
            )
        self.assertEqual(chain.chain_sha256, projection.chain_sha256)
        self.assertEqual(
            self.fixture.attestation.attestation_id,
            projection.roundtrip_attestation_id,
        )
        self.assertEqual(
            self.fixture.attestation.mediation_id,
            projection.witness_mediation_id,
        )

    def test_real_other_valid_attestation_cannot_cross_pin_to_gen2_observation(self) -> None:
        inputs = replace(
            self.fixture.inputs,
            witness_roundtrip_attestation=self.fixture.alternate_real_attestation(),
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV2Gen2WitnessedAckChainError,
            "CROSS_PIN_MISMATCH",
        ):
            self.fixture.mint_chain(inputs=inputs)

    def test_expiry_forgery_legacy_and_malformed_base_identifier_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV2Gen2WitnessedAckChainError,
            "EVIDENCE_STALE_OR_FUTURE",
        ):
            self.fixture.mint_chain(now=NOW + timedelta(seconds=11))
        forged = object.__new__(subject.VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV2Gen2WitnessedAckChainError,
            "CAPABILITY_REQUIRED",
        ):
            self.fixture.require_chain(forged)
        legacy_observation = object.__new__(
            legacy.VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV2Gen2WitnessedAckChainError,
            "INPUTS_INVALID",
        ):
            self.fixture.mint_chain(
                inputs=replace(
                    self.fixture.inputs,
                    bound_strict_writer_response=legacy_observation,
                )
            )
        malformed_base = replace(
            self.fixture.gen2_observation.instruction,
            v2_base_commit_id="v2-witness-strict-writer-not-a-sha",
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV2Gen2WitnessedAckChainError,
            "STRICT_SHAPE_INVALID",
        ):
            subject._validate_strict_shape(malformed_base)
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(self.fixture.gen2_observation)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
