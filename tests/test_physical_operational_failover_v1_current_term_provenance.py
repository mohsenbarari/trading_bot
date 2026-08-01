"""Adversarial tests for the opaque V1 current-term provenance seam."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import pickle
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_witness_term_revalidator as subject
from tests import test_physical_operational_failover_v1_witness_term_revalidator as support


NOW = support.NOW
RELEASE_SHA = support.RELEASE_SHA


def _id(prefix: str) -> str:
    return prefix + "-" + "x" * 24


def _public(key: Ed25519PrivateKey) -> bytes:
    return support._public(key)


class _FixedEvidenceRevalidator:
    def __init__(self, evidence: object) -> None:
        self.evidence = evidence

    def revalidate_writer_term(
        self,
        *,
        request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
    ) -> object:
        del request
        return self.evidence


class PhysicalOperationalFailoverV1CurrentTermProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.attestation_key = Ed25519PrivateKey.generate()
        self.promotion_key = Ed25519PrivateKey.generate()
        self.binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id="gold-trade-three-site-prod",
            local_site="webapp_fi",
            release_sha=RELEASE_SHA,
            generation_id=_id("physical-generation"),
        )
        self.writer_config = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id=_id("writer-runtime"),
            safety_margin_seconds=5,
            maximum_term_duration_seconds=90,
            maximum_evidence_age_seconds=60,
        )
        self.config = subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id=self.writer_config.runtime_instance_id,
            witness_current_term_signer_public_key=_public(self.attestation_key),
            witness_promotion_signer_public_key=_public(self.promotion_key),
            witness_current_term_signer_key_id=_id("witness-current-term-key"),
            durable_guard_id=_id("witness-term-replay-guard"),
            safety_margin_seconds=5,
            maximum_attestation_age_seconds=30,
            maximum_attestation_duration_seconds=90,
            maximum_reservation_duration_seconds=90,
        )
        self.term = support.wire.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id=_id("fi-writer-lease"),
            witness_transition_id=_id("witness-transition"),
            witnessed_term_proof_sha256="b" * 64,
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=60),
        )
        term_sha = support.ledger._term_sha256(self.term, code="test")
        ledger_state = support.ledger.PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=1,
            phase="fi-active",
            clock_floor=NOW - timedelta(seconds=10),
            active_term=self.term,
            active_term_sha256=term_sha,
        )
        entry = support.ledger._make_entry(
            sequence=1,
            previous_head_sha256="0" * 64,
            observed_at=NOW - timedelta(seconds=10),
            event="bootstrap-fi-active",
            state=ledger_state,
        )
        self.snapshot = support.ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot(
            version=1,
            head_sha256=entry.entry_sha256,
            entry=entry,
            state=ledger_state,
        )
        self.clock = support._Clock()

    def _bridge(self) -> subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidator:
        return subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidator(
            config=self.config,
            fetcher=support._Fetcher(
                signing_config=self.config,
                private_key=self.attestation_key,
                snapshot=self.snapshot,
            ),
            durable_guard=support._Guard(),
            clock=self.clock,
        )

    def _admission(
        self,
        *,
        bridge: subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidator,
        revalidation_id: str = _id("provenance-revalidation"),
    ) -> tuple[
        admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition,
        admission.PhysicalOperationalFailoverV1WriterAdmission,
    ]:
        startup = admission.new_physical_operational_failover_v1_writer_admission_state(
            binding=self.binding
        )
        revalidation = admission.revalidate_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=startup,
            evidence_revalidator=bridge,
            current_term_provenance_binder=bridge,
            revalidation_id=revalidation_id,
            now=NOW,
        )
        self.assertIsNotNone(revalidation)
        assert revalidation is not None
        operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.writer_config,
            state=revalidation.next_state,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
            now=NOW,
        )
        self.assertIsNotNone(operation)
        assert operation is not None
        result = admission.require_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=revalidation.next_state,
            operation=operation,
            now=NOW,
        )
        self.assertIsNotNone(result)
        assert result is not None
        return revalidation, result

    def test_exact_verified_term_and_exact_v1_admission_yield_one_scalar_projection(self) -> None:
        bridge = self._bridge()
        _revalidation, writer_admission = self._admission(bridge=bridge)
        handle = bridge.bind_current_term_provenance_to_writer_admission(
            writer_admission=writer_admission,
            writer_admission_config=self.writer_config,
        )
        self.assertEqual({"_capability"}, set(vars(handle)))
        with self.assertRaises(TypeError):
            pickle.dumps(handle)

        projection = subject.consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance(
            value=handle,
            config=self.config,
            now=NOW,
        )
        self.assertEqual(subject.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_PROVENANCE_SCHEMA, projection.schema)
        self.assertEqual(self.binding.local_site, projection.holder_site)
        self.assertEqual(41, projection.writer_epoch)
        self.assertEqual(self.term.witness_transition_id, projection.witness_transition_id)
        self.assertEqual(self.term.witnessed_term_proof_sha256, projection.witnessed_term_proof_sha256)
        self.assertEqual(writer_admission.admitted_at, projection.admitted_at)
        self.assertEqual(writer_admission.operation.opened_at, projection.operation_opened_at)
        self.assertNotIn("canonical_attestation", projection.__dataclass_fields__)
        self.assertNotIn("attestation_nonce", projection.__dataclass_fields__)
        self.assertEqual(
            subject.physical_operational_failover_v1_witness_current_term_revalidator_configuration_sha256(
                config=self.config
            ),
            projection.revalidator_configuration_sha256,
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "BOUND_HANDLE_INVALID",
        ):
            subject.consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance(
                value=handle,
                config=self.config,
                now=NOW,
            )

    def test_bridge_only_consume_requires_the_exact_v1_admission_identity(self) -> None:
        bridge = self._bridge()
        _revalidation, writer_admission = self._admission(bridge=bridge)
        handle = bridge.bind_current_term_provenance_to_writer_admission(
            writer_admission=writer_admission,
            writer_admission_config=self.writer_config,
        )
        # This is deliberately not a normal construction path: it models an
        # equal-looking in-memory object made by privileged Python code.  It
        # carries every public/private field of the real admission but must
        # still not claim the one-shot V1 provenance handle.
        foreign = object.__new__(type(writer_admission))
        for name in writer_admission.__dataclass_fields__:
            object.__setattr__(foreign, name, getattr(writer_admission, name))
        self.assertEqual(writer_admission, foreign)
        self.assertIsNot(writer_admission, foreign)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "WRITER_ADMISSION_MISMATCH",
        ):
            subject.consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance_for_writer_admission(
                value=handle,
                writer_admission=foreign,
                config=self.config,
                now=NOW,
            )
        # Identity mismatch burns the handle so it cannot be tried again with
        # the genuine admission after a failed substitution attempt.
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "BOUND_HANDLE_INVALID",
        ):
            subject.consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance_for_writer_admission(
                value=handle,
                writer_admission=writer_admission,
                config=self.config,
                now=NOW,
            )

    def test_equal_but_foreign_evidence_cannot_bind_to_the_verified_record(self) -> None:
        bridge = self._bridge()
        request = admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest(
            binding=self.binding,
            runtime_instance_id=self.writer_config.runtime_instance_id or "",
            revalidation_id=_id("foreign-evidence-revalidation"),
            minimum_writer_epoch=0,
            previous_writer_lease_id=None,
            previous_evidence_id=None,
            previous_revalidation_id=None,
            clock_floor=None,
        )
        genuine = bridge.revalidate_writer_term(request=request)
        foreign_equal_value = replace(genuine)
        self.assertEqual(genuine, foreign_equal_value)
        self.assertIsNot(genuine, foreign_equal_value)
        startup = admission.new_physical_operational_failover_v1_writer_admission_state(
            binding=self.binding
        )
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "EVIDENCE_CAPABILITY_REQUIRED",
        ):
            admission.revalidate_physical_operational_failover_v1_writer_admission(
                config=self.writer_config,
                state=startup,
                evidence_revalidator=_FixedEvidenceRevalidator(foreign_equal_value),
                current_term_provenance_binder=bridge,
                revalidation_id=request.revalidation_id,
                now=NOW,
            )

    def test_reconstructed_state_and_wrong_term_cannot_claim_the_identity_bound_record(self) -> None:
        bridge = self._bridge()
        revalidation, writer_admission = self._admission(bridge=bridge)

        reconstructed_state = deepcopy(revalidation.next_state)
        object.__setattr__(reconstructed_state, "_capability", admission._STATE_CAPABILITY)
        reconstructed_operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.writer_config,
            state=reconstructed_state,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
            now=NOW,
        )
        self.assertIsNotNone(reconstructed_operation)
        assert reconstructed_operation is not None
        reconstructed_admission = admission.require_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=reconstructed_state,
            operation=reconstructed_operation,
            now=NOW,
        )
        self.assertIsNotNone(reconstructed_admission)
        assert reconstructed_admission is not None
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "STATE_CAPABILITY_REQUIRED",
        ):
            bridge.bind_current_term_provenance_to_writer_admission(
                writer_admission=reconstructed_admission,
                writer_admission_config=self.writer_config,
            )

        object.__setattr__(writer_admission, "term", replace(writer_admission.term, writer_epoch=42))
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "WRITER_ADMISSION_INVALID",
        ):
            bridge.bind_current_term_provenance_to_writer_admission(
                writer_admission=writer_admission,
                writer_admission_config=self.writer_config,
            )

    def test_different_same_config_revalidator_cannot_claim_another_owner_record(self) -> None:
        owner = self._bridge()
        _revalidation, writer_admission = self._admission(
            bridge=owner,
            revalidation_id=_id("other-owner-revalidation"),
        )
        different_owner = self._bridge()
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "OWNER_MISMATCH",
        ):
            different_owner.bind_current_term_provenance_to_writer_admission(
                writer_admission=writer_admission,
                writer_admission_config=self.writer_config,
            )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "STATE_CAPABILITY_REQUIRED",
        ):
            owner.bind_current_term_provenance_to_writer_admission(
                writer_admission=writer_admission,
                writer_admission_config=self.writer_config,
            )

    def test_expired_or_mismatched_configuration_handle_fails_closed_and_burns_the_state_link(self) -> None:
        bridge = self._bridge()
        _revalidation, writer_admission = self._admission(bridge=bridge)
        self.clock.now = NOW + timedelta(seconds=46)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "PROVENANCE_STALE",
        ):
            bridge.bind_current_term_provenance_to_writer_admission(
                writer_admission=writer_admission,
                writer_admission_config=self.writer_config,
            )
        self.clock.now = NOW
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "STATE_CAPABILITY_REQUIRED",
        ):
            bridge.bind_current_term_provenance_to_writer_admission(
                writer_admission=writer_admission,
                writer_admission_config=self.writer_config,
            )

        bridge = self._bridge()
        _revalidation, writer_admission = self._admission(
            bridge=bridge,
            revalidation_id=_id("mismatched-config-revalidation"),
        )
        handle = bridge.bind_current_term_provenance_to_writer_admission(
            writer_admission=writer_admission,
            writer_admission_config=self.writer_config,
        )
        mismatched = replace(
            self.config,
            durable_guard_id=_id("different-guard"),
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "CONFIG_MISMATCH",
        ):
            subject.consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance(
                value=handle,
                config=mismatched,
                now=NOW,
            )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
            "BOUND_HANDLE_INVALID",
        ):
            subject.consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance(
                value=handle,
                config=self.config,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
