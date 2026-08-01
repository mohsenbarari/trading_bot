"""Adversarial tests for Phase-1's post-effect checkpoint gap boundary."""

from __future__ import annotations

import ast
from dataclasses import fields, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import unittest
from uuid import UUID

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v2_gen2_witnessed_campaign_readiness as readiness_owner
from core import physical_full_matrix_v4_phase1_post_effect_strict_ack_boundary as subject
from core import physical_full_matrix_v4_phase1_strict_ack_provenance as provenance
from tests.test_physical_full_matrix_v2_gen2_witnessed_ack_chain import (
    Gen2WitnessedAckChainFixture,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase1_post_effect_strict_ack_boundary.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


class PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Gen2WitnessedAckChainFixture()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.now: datetime = self.fixture.now
        self.chain = self.fixture.mint_chain(now=self.now)
        binding = readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding(
            **{
                item.name: getattr(self.chain, item.name)
                for item in fields(
                    readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding
                )
            }
        )
        readiness_config = (
            readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig(
                binding=binding,
                gen2_witnessed_ack_chain_config=self.fixture.config,
                enabled=True,
            )
        )
        with self.fixture._all_owner_clocks(now=self.now):
            readiness = readiness_owner.mint_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                config=readiness_config,
                inputs=readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignInputs(
                    gen2_witnessed_ack_chain=self.chain
                ),
                now=self.now,
            )
        self.binding = driver.PhysicalFullMatrixV4ExecutionBinding(
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            readiness_binding_sha256=readiness.report.binding_sha256,
            route_commitment_sha256=binding.route_commitment_sha256,
            four_role_binding_sha256=binding.four_role_binding_sha256,
            writer_holder_site=binding.writer_holder_site,
            writer_epoch=binding.writer_epoch,
            writer_lease_id=binding.writer_lease_id,
            witnessed_term_proof_sha256=binding.witnessed_term_proof_sha256,
            source_site=binding.source_site,
            destination_site=binding.destination_site,
            roundtrip_attestation_sha256=binding.roundtrip_attestation_sha256,
            roundtrip_configuration_sha256=binding.roundtrip_configuration_sha256,
            witness_transition_id=binding.witness_transition_id,
            witness_sequence=binding.witness_sequence,
        )
        execution_config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=self.binding,
            readiness=readiness,
            run_id=UUID("0ab6d819-4f19-4ef7-bb6c-ef1b087da4d4"),
            enabled=True,
        )
        with self.fixture._all_owner_clocks(now=self.now):
            plan = driver.build_physical_full_matrix_v4_execution_plan(
                config=execution_config
            )
        snapshot = driver._snapshot(plan)
        self.pre_effect_request = driver._request(
            snapshot=snapshot,
            phase=snapshot.phases[0],
            binding=snapshot.binding,
            pre_effect_readiness_evidence=driver.PhysicalFullMatrixV4ReadinessEvidence(
                binding=self.binding, readiness=readiness
            ),
        )
        self.provenance_config = provenance.PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig(
            gen2_witnessed_ack_chain_config=self.fixture.config,
            bound_strict_writer_response_config=self.fixture.bound_config,
            enabled=True,
        )
        with self.fixture._all_owner_clocks(now=self.now):
            self.ack_provenance = provenance.mint_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
                config=self.provenance_config,
                request=self.pre_effect_request,
                gen2_witnessed_ack_chain=self.chain,
                bound_strict_writer_response=self.fixture.gen2_observation,
                now=self.now,
            )
        self.request = self._adapter_request(self.pre_effect_request)
        self.config = subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryConfig(
            strict_ack_provenance_config=self.provenance_config,
            enabled=True,
        )

    def _adapter_request(self, request):
        claim = driver.PhysicalFullMatrixV4PhaseClaim(
            run_id=request.run_id,
            plan_sha256=request.plan_sha256,
            sequence=request.phase.sequence,
            phase_request_sha256=request.phase_request_sha256,
            effect_key=request.effect_key,
            claim_id="phase-1-strict-ack-claim-000001",
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
            effect_start=effect_start, claim=claim, request=request
        )
        anchor = driver._mint_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request,
            effect_start=effect_start,
            journal_binding_sha256=_hash("journal-binding"),
            baseline_plan_binding_sha256=_hash("baseline-plan-binding"),
            anchor_genesis_sequence=0,
            anchor_genesis_head_sha256="0" * 64,
            anchor_previous_sequence=0,
            anchor_previous_head_sha256="0" * 64,
            anchor_sequence=1,
            anchor_head_sha256=_hash("phase1-start-head"),
            anchor_commitment_sha256=_hash("phase1-start-commitment"),
            anchor_attestation_sha256=_hash("phase1-start-attestation"),
            anchor_local_previous_record_sha256="0" * 64,
            anchor_local_event_sha256=_hash("phase1-start-event"),
            anchor_occurred_at=self.now,
        )
        return driver._adapter_request_with_effect_start_authority(
            request=request, authority=authority, anchor_proof=anchor
        )

    def _record(self):
        with self.fixture._all_owner_clocks(now=self.now):
            return subject.record_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_unavailable(
                config=self.config,
                request=self.request,
                strict_ack_provenance=self.ack_provenance,
                now=self.now,
            )

    def _require(self, value, *, request=None, ack_provenance=None):
        with self.fixture._all_owner_clocks(now=self.now):
            return subject.require_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_unavailable(
                value,
                config=self.config,
                request=self.request if request is None else request,
                strict_ack_provenance=(
                    self.ack_provenance if ack_provenance is None else ack_provenance
                ),
                now=self.now,
            )

    def test_only_outcome_is_correlated_checkpoint_unavailability(self) -> None:
        value = self._record()
        self.assertIs(value, self._require(value))
        self.assertEqual(
            subject.PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_STATUS,
            value.status,
        )
        self.assertEqual(self.ack_provenance.provenance_sha256, value.strict_ack_provenance_sha256)
        self.assertEqual(self.request.effect_key, value.effect_key)
        self.assertTrue(value.post_effect_checkpoint_required)
        self.assertFalse(value.post_effect_checkpoint_available)
        self.assertFalse(value.strict_ack_post_effect_bound)
        self.assertFalse(value.writer_authorized)
        self.assertFalse(value.promotion_authorized)
        self.assertFalse(value.phase_completion_evidenced)
        self.assertFalse(value.next_phase_start_authorized)
        self.assertFalse(value.execution_authorized)
        self.assertFalse(value.full_matrix_authorized)
        self.assertEqual(
            hashlib.sha256(
                canonical_json_bytes({
                    "schema": value.schema,
                    "status": value.status,
                    "strict_ack_provenance_sha256": value.strict_ack_provenance_sha256,
                    "run_id": str(value.run_id),
                    "plan_sha256": value.plan_sha256,
                    "phase_name": value.phase_name,
                    "phase_sequence": value.phase_sequence,
                    "effect_key": value.effect_key,
                    "phase_request_sha256": value.phase_request_sha256,
                    "claim_id": value.claim_id,
                    "journaled_effect_start_identity_sha256": value.journaled_effect_start_identity_sha256,
                    "effect_start_anchor_sequence": value.effect_start_anchor_sequence,
                    "effect_start_anchor_head_sha256": value.effect_start_anchor_head_sha256,
                    "strict_ack_post_effect_bound": False,
                    "post_effect_checkpoint_required": True,
                    "post_effect_checkpoint_available": False,
                    "writer_authorized": False,
                    "promotion_authorized": False,
                    "phase_completion_evidenced": False,
                    "next_phase_start_authorized": False,
                    "execution_authorized": False,
                    "full_matrix_authorized": False,
                })
            ).hexdigest(),
            value.boundary_sha256,
        )

    def test_pre_effect_or_lookalike_request_cannot_relabel_legacy_ack(self) -> None:
        with self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError,
            "CORRELATION_REQUIRED",
        ):
            subject.record_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_unavailable(
                config=self.config,
                request=self.pre_effect_request,
                strict_ack_provenance=self.ack_provenance,
                now=self.now,
            )
        value = self._record()
        lookalike = replace(self.request)
        with self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError,
            "CORRELATION_REQUIRED",
        ):
            self._require(value, request=lookalike)

    def test_exact_ack_provenance_and_checkpoint_absence_are_tamper_checked(self) -> None:
        value = self._record()
        object.__setattr__(value, "post_effect_checkpoint_available", True)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError,
            "TAMPERED",
        ):
            self._require(value)
        object.__setattr__(value, "post_effect_checkpoint_available", False)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError,
            "CORRELATION_MISMATCH",
        ):
            self._require(value, ack_provenance=replace(self.ack_provenance))

    def test_default_off_and_no_effectful_dependencies_or_success_entrypoint(self) -> None:
        with self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError,
            "CONFIG_INVALID",
        ):
            subject.record_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_unavailable(
                config=replace(self.config, enabled=False),
                request=self.request,
                strict_ack_provenance=self.ack_provenance,
                now=self.now,
            )
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertFalse(
            {"boto3", "docker", "paramiko", "requests", "socket", "subprocess", "urllib"}
            & imports
        )
        self.assertNotIn("execute_", source)
        self.assertNotIn("oracle-succeeded", source)
