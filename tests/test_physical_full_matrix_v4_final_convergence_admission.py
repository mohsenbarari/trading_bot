"""Focused fail-closed tests for the reserved V4 Phase-8 admission seam.

No test here starts a phase, contacts a host, signs an owner observation, or
performs a network/storage operation.  The module is intentionally only a
typed cross-pin contract until the four independent owner verifiers exist.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import pickle
import unittest
from uuid import UUID

from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v4_final_convergence_admission as subject


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
RUN_ID = UUID("4a1c6b42-5eaf-4298-af2e-8c897aa61453")
PLAN_SHA256 = "a" * 64
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_final_convergence_admission.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _binding(
    *,
    direction: tuple[str, str] = ("webapp_fi", "webapp_ir"),
    holder: str = "webapp_fi",
) -> driver.PhysicalFullMatrixV4ExecutionBinding:
    source, destination = direction
    return driver.PhysicalFullMatrixV4ExecutionBinding(
        campaign_id="physical-full-matrix-v4-20260801",
        release_sha="d" * 40,
        readiness_binding_sha256=_hash("readiness"),
        route_commitment_sha256=_hash("route"),
        four_role_binding_sha256=_hash("four-role"),
        writer_holder_site=holder,
        writer_epoch=11,
        writer_lease_id="writer-lease-v4-phase8-000001",
        witnessed_term_proof_sha256=_hash("term"),
        source_site=source,
        destination_site=destination,
        roundtrip_attestation_sha256=_hash("roundtrip"),
        roundtrip_configuration_sha256=_hash("configuration"),
        witness_transition_id="witness-transition-v4-phase8-000001",
        witness_sequence=31,
    )


def _adapter_request(
    *,
    phase_index: int = 7,
    binding: driver.PhysicalFullMatrixV4ExecutionBinding | None = None,
    with_predecessor_completion_proof: bool = False,
) -> driver.PhysicalFullMatrixV4ExecutionRequest:
    active = _binding() if binding is None else binding
    snapshot = driver._PlanSnapshot(
        canonical_plan=b"",
        plan_sha256=PLAN_SHA256,
        run_id=RUN_ID,
        binding=driver._snapshot_binding(
            active,
            direction=(active.source_site, active.destination_site),
        ),
        phases=driver._phase_snapshots(),
        maximum_oracle_age_seconds=120,
    )
    request = driver._request(
        snapshot=snapshot,
        phase=snapshot.phases[phase_index],
        binding=snapshot.binding,
    )
    claim = driver.PhysicalFullMatrixV4PhaseClaim(
        run_id=request.run_id,
        plan_sha256=request.plan_sha256,
        sequence=request.phase.sequence,
        phase_request_sha256=request.phase_request_sha256,
        effect_key=request.effect_key,
        claim_id=f"pfm-v4-phase-{request.phase.sequence:02d}-convergence-claim-000001",
    )
    effect_start = driver.PhysicalFullMatrixV4EffectStart(
        run_id=request.run_id,
        plan_sha256=request.plan_sha256,
        sequence=request.phase.sequence,
        phase_request_sha256=request.phase_request_sha256,
        effect_key=request.effect_key,
        claim_id=claim.claim_id,
    )
    authority = driver._mint_effect_start_authority(
        effect_start=effect_start,
        claim=claim,
        request=request,
    )
    anchor = driver._mint_physical_full_matrix_v4_effect_start_anchor_proof(
        request=request,
        effect_start=effect_start,
        journal_binding_sha256=_hash("journal-binding"),
        baseline_plan_binding_sha256=_hash("baseline-binding"),
        anchor_genesis_sequence=0,
        anchor_genesis_head_sha256="0" * 64,
        anchor_previous_sequence=2,
        anchor_previous_head_sha256=_hash("p7-completion-anchor-head"),
        anchor_sequence=3,
        anchor_head_sha256=_hash("anchor-head"),
        anchor_commitment_sha256=_hash("anchor-commitment"),
        anchor_attestation_sha256=_hash("anchor-attestation"),
        anchor_local_previous_record_sha256="0" * 64,
        anchor_local_event_sha256=_hash("anchor-local-event"),
        anchor_occurred_at=NOW,
    )
    adapter_request = driver._adapter_request_with_effect_start_authority(
        request=request,
        authority=authority,
        anchor_proof=anchor,
    )
    if not with_predecessor_completion_proof:
        return adapter_request
    if request.phase.sequence != 8:
        raise AssertionError("only the Phase-8 fixture builds a P7 bridge")
    predecessor_request = driver._request(
        snapshot=snapshot,
        phase=snapshot.phases[6],
        binding=snapshot.binding,
    )
    predecessor_start = driver.PhysicalFullMatrixV4EffectStart(
        run_id=predecessor_request.run_id,
        plan_sha256=predecessor_request.plan_sha256,
        sequence=predecessor_request.phase.sequence,
        phase_request_sha256=predecessor_request.phase_request_sha256,
        effect_key=predecessor_request.effect_key,
        claim_id="pfm-v4-phase-07-convergence-predecessor-claim-000001",
    )
    predecessor_start_head = _hash("p7-start-anchor-head")
    proof = driver._mint_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
        request=adapter_request,
        predecessor_effect_start=predecessor_start,
        journal_binding_sha256=_hash("journal-binding"),
        baseline_plan_binding_sha256=_hash("baseline-binding"),
        anchor_genesis_sequence=0,
        anchor_genesis_head_sha256="0" * 64,
        predecessor_effect_start_anchor_previous_sequence=0,
        predecessor_effect_start_anchor_previous_head_sha256="0" * 64,
        predecessor_effect_start_anchor_sequence=1,
        predecessor_effect_start_anchor_head_sha256=predecessor_start_head,
        predecessor_effect_start_anchor_commitment_sha256=_hash("p7-start-commitment"),
        predecessor_effect_start_anchor_attestation_sha256=_hash("p7-start-attestation"),
        predecessor_effect_start_anchor_local_previous_record_sha256="0" * 64,
        predecessor_effect_start_anchor_local_event_sha256=_hash("p7-start-local-event"),
        predecessor_effect_started_at=NOW - timedelta(seconds=2),
        predecessor_completion_receipt_sha256=_hash("p7-completion-receipt"),
        predecessor_completion_anchor_previous_sequence=1,
        predecessor_completion_anchor_previous_head_sha256=predecessor_start_head,
        predecessor_completion_anchor_sequence=2,
        predecessor_completion_anchor_head_sha256=_hash("p7-completion-anchor-head"),
        predecessor_completion_anchor_commitment_sha256=_hash("p7-completion-commitment"),
        predecessor_completion_anchor_attestation_sha256=_hash("p7-completion-attestation"),
        predecessor_completion_anchor_local_previous_record_sha256=_hash("p7-completion-local-previous"),
        predecessor_completion_anchor_local_event_sha256=_hash("p7-completion-local-event"),
        predecessor_completed_at=NOW - timedelta(seconds=1),
    )
    return driver._adapter_request_with_effect_start_authority(
        request=request,
        authority=authority,
        anchor_proof=anchor,
        predecessor_phase_completion_anchor_proof=proof,
    )


def _claim(
    request: driver.PhysicalFullMatrixV4ExecutionRequest,
    *,
    slot: str,
    suffix: str,
) -> subject.PhysicalFullMatrixV4FinalConvergenceEvidenceClaim:
    context = subject._phase8_context(request)
    phase8 = context.binding
    spec = next(item for item in subject._SLOT_SPECS if item.slot == slot)
    return subject.PhysicalFullMatrixV4FinalConvergenceEvidenceClaim(
        schema=subject.PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CLAIM_SCHEMA,
        slot=spec.slot,
        observed_site=spec.observed_site,
        evidence_kind=spec.evidence_kind,
        assertion=spec.assertion,
        campaign_id=phase8.final_binding.campaign_id,
        release_sha=phase8.final_binding.release_sha,
        run_id=phase8.run_id,
        plan_sha256=phase8.plan_sha256,
        phase_name=phase8.phase_name,
        phase_sequence=phase8.phase_sequence,
        effect_key=phase8.effect_key,
        phase_request_sha256=phase8.phase_request_sha256,
        final_binding=phase8,
        evidence_sha256=_hash(f"evidence-{suffix}"),
        observed_at=NOW,
        expires_at=NOW + timedelta(seconds=60),
    )


def _verified(
    claim: subject.PhysicalFullMatrixV4FinalConvergenceEvidenceClaim,
) -> object:
    makers = {
        "fi-primary-readback": (
            subject._mint_verified_physical_full_matrix_v4_fi_primary_readback
        ),
        "ir-standby-replay-readback": (
            subject._mint_verified_physical_full_matrix_v4_ir_standby_replay_readback
        ),
        "object-blob-lineage-parity": (
            subject._mint_verified_physical_full_matrix_v4_object_blob_lineage_parity
        ),
        "witness-route-fresh-state": (
            subject._mint_verified_physical_full_matrix_v4_witness_route_fresh_state
        ),
    }
    return makers[claim.slot](claim=claim)


def _p7_completion_provenance(
    request: driver.PhysicalFullMatrixV4ExecutionRequest,
) -> subject.VerifiedPhysicalFullMatrixV4P7CompletionAnchorProvenance:
    phase8 = subject.project_physical_full_matrix_v4_phase8_effect_start_anchor_binding(
        request=request
    )
    claim = subject.PhysicalFullMatrixV4P7CompletionAnchorProvenanceClaim(
        schema=(
            subject.PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_CLAIM_SCHEMA
        ),
        run_id=phase8.run_id,
        plan_sha256=phase8.plan_sha256,
        p7_phase_name=driver.PHYSICAL_FULL_MATRIX_V4_PHASES[6].name,
        p7_phase_sequence=7,
        p7_effect_key=_hash("p7-effect-key"),
        p7_phase_request_sha256=_hash("p7-request"),
        final_binding=phase8,
        p7_completion_receipt_sha256=_hash("p7-completion-receipt"),
        p7_completion_anchor_sequence=phase8.anchor_previous_sequence,
        p7_completion_anchor_head_sha256=phase8.anchor_previous_head_sha256,
        p7_completion_anchor_commitment_sha256=_hash("p7-completion-commitment"),
        p7_completion_anchor_attestation_sha256=_hash("p7-completion-attestation"),
        p7_completed_at=NOW - timedelta(seconds=1),
    )
    return subject._mint_verified_physical_full_matrix_v4_p7_completion_anchor_provenance(
        claim=claim
    )


def _complete_bundle(
    request: driver.PhysicalFullMatrixV4ExecutionRequest,
) -> subject.PhysicalFullMatrixV4FinalConvergenceEvidenceBundle:
    claims = {
        slot: _claim(request, slot=slot, suffix=slot)
        for slot in subject.PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_REQUIRED_EVIDENCE_SLOTS
    }
    return subject.PhysicalFullMatrixV4FinalConvergenceEvidenceBundle(
        p7_completion_anchor_provenance=_p7_completion_provenance(request),
        fi_primary_readback=_verified(claims["fi-primary-readback"]),
        ir_standby_replay_readback=_verified(claims["ir-standby-replay-readback"]),
        object_blob_lineage_parity=_verified(claims["object-blob-lineage-parity"]),
        witness_route_fresh_state=_verified(claims["witness-route-fresh-state"]),
    )


class PhysicalFullMatrixV4FinalConvergenceAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = _adapter_request()
        self.enabled = subject.PhysicalFullMatrixV4FinalConvergenceAdmissionConfig(
            enabled=True
        )

    def test_default_off_does_not_traverse_untrusted_inputs_or_authorize(self) -> None:
        result = subject.assess_physical_full_matrix_v4_final_convergence_admission(
            config=subject.PhysicalFullMatrixV4FinalConvergenceAdmissionConfig(),
            request=object(),
            evidence_bundle=object(),
        )
        self.assertEqual("blocked-default-off", result.status)
        self.assertEqual(
            ("final-convergence-admission-default-disabled",), result.reason_codes
        )
        self.assertIsNone(result.final_binding)
        self.assertIsNone(result.phase8_effect_start_anchor)
        self.assertFalse(result.final_convergence_admitted)
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertFalse(result.full_matrix_authorized)
        self.assertFalse(result.full_matrix_executed)

    def test_enabled_path_requires_private_exact_phase8_effect_start_and_anchor(self) -> None:
        ordinary = driver._request(
            snapshot=driver._PlanSnapshot(
                canonical_plan=b"",
                plan_sha256=PLAN_SHA256,
                run_id=RUN_ID,
                binding=driver._snapshot_binding(
                    _binding(), direction=("webapp_fi", "webapp_ir")
                ),
                phases=driver._phase_snapshots(),
                maximum_oracle_age_seconds=120,
            ),
            phase=driver._phase_snapshots()[7],
            binding=driver._snapshot_binding(
                _binding(), direction=("webapp_fi", "webapp_ir")
            ),
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "EFFECT_START_REQUIRED",
        ):
            subject.assess_physical_full_matrix_v4_final_convergence_admission(
                config=self.enabled,
                request=ordinary,
                evidence_bundle=subject.PhysicalFullMatrixV4FinalConvergenceEvidenceBundle(),
            )

    def test_generic_claims_and_generic_evidence_hashes_never_admit_convergence(self) -> None:
        raw = subject.PhysicalFullMatrixV4FinalConvergenceEvidenceBundle(
            p7_completion_anchor_provenance=_p7_completion_provenance(self.request),
            fi_primary_readback=_claim(
                self.request, slot="fi-primary-readback", suffix="raw-fi"
            ),
            ir_standby_replay_readback=_claim(
                self.request, slot="ir-standby-replay-readback", suffix="raw-ir"
            ),
            object_blob_lineage_parity=_claim(
                self.request, slot="object-blob-lineage-parity", suffix="raw-blob"
            ),
            witness_route_fresh_state=_claim(
                self.request, slot="witness-route-fresh-state", suffix="raw-route"
            ),
        )
        result = subject.assess_physical_full_matrix_v4_final_convergence_admission(
            config=self.enabled,
            request=self.request,
            evidence_bundle=raw,
        )
        self.assertEqual("blocked-typed-future-owner-evidence-required", result.status)
        self.assertEqual(
            {
                "typed-owner-evidence-required:fi-primary-readback",
                "typed-owner-evidence-required:ir-standby-replay-readback",
                "typed-owner-evidence-required:object-blob-lineage-parity",
                "typed-owner-evidence-required:witness-route-fresh-state",
            },
            set(result.reason_codes),
        )
        self.assertFalse(result.final_convergence_admitted)
        self.assertFalse(result.execution_authorized)

    def test_p8_start_anchor_cannot_substitute_for_explicit_p7_completion_proof(self) -> None:
        complete = _complete_bundle(self.request)
        result = subject.assess_physical_full_matrix_v4_final_convergence_admission(
            config=self.enabled,
            request=self.request,
            evidence_bundle=replace(complete, p7_completion_anchor_provenance=None),
        )
        self.assertEqual(
            "blocked-p7-completion-anchor-provenance-required", result.status
        )
        self.assertEqual(
            (
                "p7-completion-anchor-provenance-required",
                "p8-start-anchor-cannot-substitute-for-p7-completion-proof",
            ),
            result.reason_codes,
        )
        self.assertFalse(result.p7_completion_anchor_provenance_present)
        self.assertFalse(result.final_convergence_admitted)

    def test_shared_durable_p7_completion_bridge_projects_into_phase8_evidence(self) -> None:
        request = _adapter_request(with_predecessor_completion_proof=True)
        bridge = driver.require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
            request=request
        )
        projected = subject.project_physical_full_matrix_v4_p7_completion_anchor_provenance(
            request=request
        )
        self.assertEqual(bridge.predecessor_effect_key, projected.claim.p7_effect_key)
        self.assertEqual(
            bridge.predecessor_phase_request_sha256,
            projected.claim.p7_phase_request_sha256,
        )
        self.assertEqual(
            bridge.predecessor_completion_receipt_sha256,
            projected.claim.p7_completion_receipt_sha256,
        )
        self.assertEqual(
            bridge.predecessor_completion_anchor_sequence,
            projected.claim.p7_completion_anchor_sequence,
        )
        self.assertEqual(
            bridge.predecessor_completion_anchor_head_sha256,
            projected.claim.p7_completion_anchor_head_sha256,
        )
        self.assertFalse(projected.execution_authorized)
        self.assertFalse(projected.full_matrix_authorized)
        complete = _complete_bundle(request)
        result = subject.assess_physical_full_matrix_v4_final_convergence_admission(
            config=self.enabled,
            request=request,
            evidence_bundle=replace(
                complete,
                p7_completion_anchor_provenance=projected,
            ),
        )
        self.assertEqual(
            "blocked-typed-evidence-cross-pinned-not-final-admission",
            result.status,
        )
        self.assertTrue(result.p7_completion_anchor_provenance_present)
        self.assertFalse(result.final_convergence_admitted)

    def test_shared_p7_bridge_requires_the_exact_driver_attached_proof(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "P7_COMPLETION_ANCHOR_PROVENANCE_REQUIRED",
        ):
            subject.project_physical_full_matrix_v4_p7_completion_anchor_provenance(
                request=self.request
            )

    def test_four_distinct_opaque_slots_cross_pin_but_remain_non_authorizing(self) -> None:
        result = subject.assess_physical_full_matrix_v4_final_convergence_admission(
            config=self.enabled,
            request=self.request,
            evidence_bundle=_complete_bundle(self.request),
        )
        self.assertEqual(
            "blocked-typed-evidence-cross-pinned-not-final-admission", result.status
        )
        self.assertEqual(
            (
                "future-owner-verifiers-and-phase8-runtime-not-implemented",
                "generic-oracle-evidence-sha256-is-not-convergence-proof",
            ),
            result.reason_codes,
        )
        self.assertEqual(self.request.binding, result.final_binding)
        self.assertEqual(
            subject.project_physical_full_matrix_v4_phase8_effect_start_anchor_binding(
                request=self.request
            ),
            result.phase8_effect_start_anchor,
        )
        self.assertTrue(result.p7_completion_anchor_provenance_present)
        self.assertFalse(result.final_convergence_admitted)
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertFalse(result.full_matrix_authorized)
        self.assertFalse(result.full_matrix_executed)

    def test_missing_or_substituted_slot_fails_closed(self) -> None:
        complete = _complete_bundle(self.request)
        missing = replace(complete, witness_route_fresh_state=None)
        result = subject.assess_physical_full_matrix_v4_final_convergence_admission(
            config=self.enabled,
            request=self.request,
            evidence_bundle=missing,
        )
        self.assertEqual("blocked-typed-future-owner-evidence-required", result.status)
        self.assertEqual(
            ("typed-owner-evidence-required:witness-route-fresh-state",),
            result.reason_codes,
        )
        substituted = replace(
            complete,
            witness_route_fresh_state=complete.fi_primary_readback,
        )
        result = subject.assess_physical_full_matrix_v4_final_convergence_admission(
            config=self.enabled,
            request=self.request,
            evidence_bundle=substituted,
        )
        self.assertEqual("blocked-typed-future-owner-evidence-required", result.status)
        self.assertEqual(
            ("typed-owner-evidence-required:witness-route-fresh-state",),
            result.reason_codes,
        )

    def test_owner_verifier_domains_are_four_distinct_non_aliases(self) -> None:
        """Witness evidence must not stand in for Object Storage parity."""

        self.assertEqual(
            {"webapp_fi", "webapp_ir", "object_storage", "witness"},
            {spec.observed_site for spec in subject._SLOT_SPECS},
        )
        complete = _complete_bundle(self.request)
        object_parity = complete.object_blob_lineage_parity
        assert isinstance(
            object_parity,
            subject.VerifiedPhysicalFullMatrixV4ObjectBlobLineageParity,
        )
        relabelled = subject._mint_verified_physical_full_matrix_v4_object_blob_lineage_parity(
            claim=replace(object_parity.claim, observed_site="witness")
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "EVIDENCE_CROSS_PIN_MISMATCH",
        ):
            subject.assess_physical_full_matrix_v4_final_convergence_admission(
                config=self.enabled,
                request=self.request,
                evidence_bundle=replace(
                    complete,
                    object_blob_lineage_parity=relabelled,
                ),
            )

    def test_phase_eight_context_must_not_be_reused_from_another_phase(self) -> None:
        phase_one = _adapter_request(phase_index=0)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "PHASE8_CONTEXT_INVALID",
        ):
            subject.assess_physical_full_matrix_v4_final_convergence_admission(
                config=self.enabled,
                request=phase_one,
                evidence_bundle=subject.PhysicalFullMatrixV4FinalConvergenceEvidenceBundle(),
            )

    def test_final_binding_must_be_fi_primary_and_ir_standby(self) -> None:
        wrong = _adapter_request(
            binding=_binding(
                direction=("webapp_ir", "webapp_fi"),
                holder="webapp_ir",
            )
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "FINAL_BINDING_INVALID",
        ):
            subject.assess_physical_full_matrix_v4_final_convergence_admission(
                config=self.enabled,
                request=wrong,
                evidence_bundle=subject.PhysicalFullMatrixV4FinalConvergenceEvidenceBundle(),
            )

    def test_every_claim_must_exactly_cross_pin_live_start_and_anchor(self) -> None:
        complete = _complete_bundle(self.request)
        original = complete.ir_standby_replay_readback
        assert isinstance(
            original, subject.VerifiedPhysicalFullMatrixV4IrStandbyReplayReadback
        )
        forged_claim = replace(
            original.claim,
            final_binding=replace(
                original.claim.final_binding,
                anchor_head_sha256=_hash("foreign-anchor"),
            ),
        )
        forged = subject._mint_verified_physical_full_matrix_v4_ir_standby_replay_readback(
            claim=forged_claim
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "EVIDENCE_CROSS_PIN_MISMATCH",
        ):
            subject.assess_physical_full_matrix_v4_final_convergence_admission(
                config=self.enabled,
                request=self.request,
                evidence_bundle=replace(complete, ir_standby_replay_readback=forged),
            )

    def test_ir_standby_claim_cannot_reuse_a_stale_phase5_binding_instead_of_p7(self) -> None:
        complete = _complete_bundle(self.request)
        ir = complete.ir_standby_replay_readback
        assert isinstance(ir, subject.VerifiedPhysicalFullMatrixV4IrStandbyReplayReadback)
        stale_phase5 = replace(
            ir.claim.final_binding,
            final_binding=replace(
                ir.claim.final_binding.final_binding,
                writer_epoch=10,
                writer_lease_id="writer-lease-v4-phase5-000001",
                witnessed_term_proof_sha256=_hash("old-phase5-term"),
                readiness_binding_sha256=_hash("old-phase5-readiness"),
                route_commitment_sha256=_hash("old-phase5-route"),
                witness_transition_id="witness-transition-v4-phase5-000001",
                witness_sequence=29,
            ),
        )
        stale = subject._mint_verified_physical_full_matrix_v4_ir_standby_replay_readback(
            claim=replace(ir.claim, final_binding=stale_phase5)
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "EVIDENCE_CROSS_PIN_MISMATCH",
        ):
            subject.assess_physical_full_matrix_v4_final_convergence_admission(
                config=self.enabled,
                request=self.request,
                evidence_bundle=replace(complete, ir_standby_replay_readback=stale),
            )

    def test_p7_start_anchor_cannot_be_substituted_for_p7_completion_anchor(self) -> None:
        valid = _p7_completion_provenance(self.request)
        start_like = replace(
            valid.claim,
            p7_completion_anchor_sequence=(
                valid.claim.p7_completion_anchor_sequence - 1
            ),
            p7_completion_anchor_head_sha256=_hash("p7-start-anchor-head"),
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "P7_COMPLETION_ANCHOR_PROVENANCE_INVALID",
        ):
            subject._mint_verified_physical_full_matrix_v4_p7_completion_anchor_provenance(
                claim=start_like
            )

    def test_observation_must_be_after_the_exact_phase8_effect_date_and_short_lived(self) -> None:
        fi = _claim(self.request, slot="fi-primary-readback", suffix="stale-date")
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "EVIDENCE_CLAIM_INVALID",
        ):
            subject._mint_verified_physical_full_matrix_v4_fi_primary_readback(
                claim=replace(fi, observed_at=NOW - timedelta(microseconds=1))
            )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "EVIDENCE_CLAIM_INVALID",
        ):
            subject._mint_verified_physical_full_matrix_v4_fi_primary_readback(
                claim=replace(fi, expires_at=NOW + timedelta(seconds=301))
            )

    def test_mutated_opaque_evidence_and_reused_digest_are_rejected(self) -> None:
        complete = _complete_bundle(self.request)
        fi = complete.fi_primary_readback
        assert isinstance(fi, subject.VerifiedPhysicalFullMatrixV4FiPrimaryReadback)
        object.__setattr__(fi.claim, "evidence_sha256", _hash("tampered"))
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "EVIDENCE_TAMPERED",
        ):
            subject.assess_physical_full_matrix_v4_final_convergence_admission(
                config=self.enabled,
                request=self.request,
                evidence_bundle=complete,
            )

        complete = _complete_bundle(self.request)
        fi = complete.fi_primary_readback
        ir = complete.ir_standby_replay_readback
        assert isinstance(fi, subject.VerifiedPhysicalFullMatrixV4FiPrimaryReadback)
        assert isinstance(ir, subject.VerifiedPhysicalFullMatrixV4IrStandbyReplayReadback)
        reused = subject._mint_verified_physical_full_matrix_v4_ir_standby_replay_readback(
            claim=replace(ir.claim, evidence_sha256=fi.claim.evidence_sha256)
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FinalConvergenceAdmissionError,
            "EVIDENCE_REUSED",
        ):
            subject.assess_physical_full_matrix_v4_final_convergence_admission(
                config=self.enabled,
                request=self.request,
                evidence_bundle=replace(complete, ir_standby_replay_readback=reused),
            )

    def test_opaque_capabilities_cannot_be_constructed_copied_or_serialized(self) -> None:
        claim = _claim(self.request, slot="fi-primary-readback", suffix="opaque")
        with self.assertRaisesRegex(TypeError, "EVIDENCE_CONSTRUCTION_FORBIDDEN"):
            subject.VerifiedPhysicalFullMatrixV4FiPrimaryReadback(
                claim=claim,
                capability=object(),
            )
        value = subject._mint_verified_physical_full_matrix_v4_fi_primary_readback(
            claim=claim
        )
        for operation in (
            lambda: copy.copy(value),
            lambda: copy.deepcopy(value),
            lambda: pickle.dumps(value),
        ):
            with self.assertRaisesRegex(TypeError, "FINAL_CONVERGENCE_EVIDENCE"):
                operation()

    def test_static_boundary_has_no_generic_phase_oracle_or_live_client_import(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden_import_roots = {
            "boto3",
            "paramiko",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        self.assertFalse(
            any(name.split(".")[0] in forbidden_import_roots for name in imported),
            imported,
        )
        names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }
        self.assertNotIn("PhysicalFullMatrixV4PhaseOracle", names)
