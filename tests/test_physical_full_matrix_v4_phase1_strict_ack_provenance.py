"""Real-owner tests for the V4 Phase-1 Strict-ACK provenance boundary.

This suite intentionally uses the reusable Gen2 fixture rather than an ACK
double.  The subject remains pure: it does not execute a V4 phase or a local
capture, and the assertions ensure it cannot be mistaken for either.
"""

from __future__ import annotations

import ast
from dataclasses import fields, replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from uuid import UUID
import unittest
from unittest.mock import patch

from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v2_gen2_witnessed_campaign_readiness as readiness_owner
from core import physical_full_matrix_v4_phase1_strict_ack_provenance as subject
from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_response as bound
from tests.test_physical_full_matrix_v2_gen2_witnessed_ack_chain import (
    Gen2WitnessedAckChainFixture,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase1_strict_ack_provenance.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


class PhysicalFullMatrixV4Phase1StrictAckProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Gen2WitnessedAckChainFixture()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.now: datetime = self.fixture.now
        self.chain = self.fixture.mint_chain(now=self.now)

        readiness_binding = readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding(
            **{
                item.name: getattr(self.chain, item.name)
                for item in fields(
                    readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding
                )
            }
        )
        readiness_config = (
            readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig(
                binding=readiness_binding,
                gen2_witnessed_ack_chain_config=self.fixture.config,
                enabled=True,
            )
        )
        with self.fixture._all_owner_clocks(now=self.now):
            self.readiness = (
                readiness_owner.mint_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                    config=readiness_config,
                    inputs=readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignInputs(
                        gen2_witnessed_ack_chain=self.chain,
                    ),
                    now=self.now,
                )
            )
        self.binding = driver.PhysicalFullMatrixV4ExecutionBinding(
            campaign_id=readiness_binding.campaign_id,
            release_sha=readiness_binding.release_sha,
            readiness_binding_sha256=self.readiness.report.binding_sha256,
            route_commitment_sha256=readiness_binding.route_commitment_sha256,
            four_role_binding_sha256=readiness_binding.four_role_binding_sha256,
            writer_holder_site=readiness_binding.writer_holder_site,
            writer_epoch=readiness_binding.writer_epoch,
            writer_lease_id=readiness_binding.writer_lease_id,
            witnessed_term_proof_sha256=readiness_binding.witnessed_term_proof_sha256,
            source_site=readiness_binding.source_site,
            destination_site=readiness_binding.destination_site,
            roundtrip_attestation_sha256=readiness_binding.roundtrip_attestation_sha256,
            roundtrip_configuration_sha256=(
                readiness_binding.roundtrip_configuration_sha256
            ),
            witness_transition_id=readiness_binding.witness_transition_id,
            witness_sequence=readiness_binding.witness_sequence,
        )
        self.execution_config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=self.binding,
            readiness=self.readiness,
            run_id=UUID("9432c1f7-3c5f-4fb2-8cd5-41e4ce3eb9c8"),
            enabled=True,
        )
        with self.fixture._all_owner_clocks(now=self.now):
            self.plan = driver.build_physical_full_matrix_v4_execution_plan(
                config=self.execution_config
            )
        snapshot = driver._snapshot(self.plan)
        self.pre_effect = driver.PhysicalFullMatrixV4ReadinessEvidence(
            binding=self.binding,
            readiness=self.readiness,
        )
        self.request = driver._request(
            snapshot=snapshot,
            phase=snapshot.phases[0],
            binding=snapshot.binding,
            pre_effect_readiness_evidence=self.pre_effect,
        )
        self.config = subject.PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig(
            gen2_witnessed_ack_chain_config=self.fixture.config,
            bound_strict_writer_response_config=self.fixture.bound_config,
            enabled=True,
        )

    def _mint(self):
        with self.fixture._all_owner_clocks(now=self.now):
            return subject.mint_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
                config=self.config,
                request=self.request,
                gen2_witnessed_ack_chain=self.chain,
                bound_strict_writer_response=self.fixture.gen2_observation,
                now=self.now,
            )

    def _require(self, value, *, request=None):
        with self.fixture._all_owner_clocks(now=self.now):
            return subject.require_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
                value,
                config=self.config,
                request=self.request if request is None else request,
                now=self.now,
            )

    def test_real_existing_owners_cross_pin_full_phase1_trace_without_phase_success(self) -> None:
        value = self._mint()
        self.assertIs(value, self._require(value))
        self.assertEqual(
            "verified-gen2-strict-ack-provenance-unsequenced-not-v4-phase-success",
            value.status,
        )
        self.assertEqual(self.request.run_id, value.run_id)
        self.assertEqual(self.request.plan_sha256, value.plan_sha256)
        self.assertEqual(self.request.effect_key, value.effect_key)
        self.assertEqual(self.request.phase_request_sha256, value.phase_request_sha256)
        self.assertEqual(self.binding, value.binding)
        self.assertEqual(self.chain.chain_sha256, value.strict_ack_chain.chain_sha256)
        self.assertEqual(
            self.fixture.gen2_observation.observation_sha256,
            value.strict_ack_chain.strict_observation_sha256,
        )
        self.assertEqual(
            self.fixture.gen2_observation.runtime_commit_receipt_sha256,
            value.strict_ack_chain.strict_runtime_commit_receipt_sha256,
        )
        self.assertFalse(value.strict_ack_post_effect_bound)
        self.assertEqual(
            subject.PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_POST_EFFECT_REQUIRED_CORRELATION_FIELDS,
            value.strict_ack_post_effect_missing_correlation_fields,
        )
        self.assertFalse(value.capture_handoff_verified)
        self.assertFalse(value.phase_effect_authorized)
        self.assertFalse(value.execution_authorized)
        self.assertFalse(value.full_matrix_authorized)
        self.assertFalse(value.full_matrix_executed)

        payload = json.loads(value.canonical_provenance)
        self.assertEqual(value.provenance_sha256, hashlib.sha256(value.canonical_provenance).hexdigest())
        self.assertEqual(self.request.effect_key, payload["v4_request"]["effect_key"])
        self.assertEqual(
            list(
                subject.PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_POST_EFFECT_REQUIRED_CORRELATION_FIELDS
            ),
            payload["strict_ack_post_effect_missing_correlation_fields"],
        )
        self.assertFalse(payload["full_matrix_executed"])
        self.assertEqual(
            self.fixture.gen2_observation.instruction.v1_v2_writer_term_bridge_certificate_sha256,
            payload["strict_ack_chain"]["strict_v1_v2_writer_term_bridge_certificate_sha256"],
        )
        self.assertEqual(
            set(subject._STRICT_CHAIN_FIELDS),
            {
                name
                for name in payload["strict_ack_chain"]
                if name.startswith("strict_")
            },
        )

    def test_real_post_effect_start_cannot_relabel_legacy_gen2_ack_as_completion(self) -> None:
        """A genuine P1 authority/anchor does not repair a legacy wire gap."""

        value = self._mint()
        claim = driver.PhysicalFullMatrixV4PhaseClaim(
            run_id=self.request.run_id,
            plan_sha256=self.request.plan_sha256,
            sequence=self.request.phase.sequence,
            phase_request_sha256=self.request.phase_request_sha256,
            effect_key=self.request.effect_key,
            claim_id="phase1-strict-ack-claim-000001",
        )
        effect_start = driver.PhysicalFullMatrixV4EffectStart(
            run_id=claim.run_id,
            plan_sha256=claim.plan_sha256,
            sequence=claim.sequence,
            phase_request_sha256=claim.phase_request_sha256,
            effect_key=claim.effect_key,
            claim_id=claim.claim_id,
        )
        authority = driver._mint_effect_start_authority(
            effect_start=effect_start,
            claim=claim,
            request=self.request,
        )
        anchor = driver._mint_physical_full_matrix_v4_effect_start_anchor_proof(
            request=self.request,
            effect_start=effect_start,
            journal_binding_sha256=_hash("p1-journal-binding"),
            baseline_plan_binding_sha256=_hash("p1-baseline-binding"),
            anchor_genesis_sequence=0,
            anchor_genesis_head_sha256="0" * 64,
            anchor_previous_sequence=0,
            anchor_previous_head_sha256="0" * 64,
            anchor_sequence=1,
            anchor_head_sha256=_hash("p1-anchor-head"),
            anchor_commitment_sha256=_hash("p1-anchor-commitment"),
            anchor_attestation_sha256=_hash("p1-anchor-attestation"),
            anchor_local_previous_record_sha256="0" * 64,
            anchor_local_event_sha256=_hash("p1-anchor-local-event"),
            anchor_occurred_at=self.now,
        )
        post_effect_request = driver._adapter_request_with_effect_start_authority(
            request=self.request,
            authority=authority,
            anchor_proof=anchor,
        )
        self.assertIs(
            authority,
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=post_effect_request
            ),
        )
        self.assertIs(
            anchor,
            driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=post_effect_request
            ),
        )
        self.assertIs(value, self._require(value, request=post_effect_request))
        self.assertFalse(value.strict_ack_post_effect_bound)
        self.assertEqual(
            subject.PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_POST_EFFECT_REQUIRED_CORRELATION_FIELDS,
            value.strict_ack_post_effect_missing_correlation_fields,
        )

        legacy_signed_fields = set(
            subject._ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainPins.__dataclass_fields__
        ) | set(
            bound.PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction.__dataclass_fields__
        ) | set(
            bound.VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation.__dataclass_fields__
        )
        runtime_state = bound._OBSERVATION_STATES.get(self.fixture.gen2_observation)
        self.assertIsNotNone(runtime_state)
        assert runtime_state is not None
        legacy_signed_fields.update(
            json.loads(runtime_state.canonical_runtime_receipt).keys()
        )
        self.assertTrue(
            set(
                subject.PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_POST_EFFECT_REQUIRED_CORRELATION_FIELDS
            ).isdisjoint(legacy_signed_fields)
        )

    def test_request_digest_is_recomputed_not_accepted_as_a_label(self) -> None:
        forged = replace(self.request, effect_key=_hash("forged-effect"))
        with self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1StrictAckProvenanceError,
            "REQUEST_HASH_MISMATCH",
        ):
            subject.mint_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
                config=self.config,
                request=forged,
                gen2_witnessed_ack_chain=self.chain,
                bound_strict_writer_response=self.fixture.gen2_observation,
                now=self.now,
            )

    def test_revalidation_requires_the_exact_pre_effect_readiness_object(self) -> None:
        value = self._mint()
        lookalike_pre_effect = driver.PhysicalFullMatrixV4ReadinessEvidence(
            binding=self.binding,
            readiness=self.readiness,
        )
        request = replace(
            self.request,
            pre_effect_readiness_evidence=lookalike_pre_effect,
        )
        with self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1StrictAckProvenanceError,
            "PRE_EFFECT_IDENTITY_MISMATCH",
        ):
            subject.require_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
                value,
                config=self.config,
                request=request,
                now=self.now,
            )

    def test_fresh_owner_revalidation_does_not_rewrite_the_minted_provenance(self) -> None:
        value = self._mint()
        later = self.now + timedelta(seconds=1)
        with self.fixture._all_owner_clocks(now=later):
            self.assertIs(
                value,
                subject.require_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
                    value,
                    config=self.config,
                    request=self.request,
                    now=later,
                ),
            )

    def test_bound_observation_projection_must_match_every_chain_strict_field(self) -> None:
        with self.fixture._all_owner_clocks(now=self.now):
            projection = (
                bound.project_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation(
                    self.fixture.gen2_observation,
                    config=self.fixture.bound_config,
                )
            )
        forged = replace(
            projection,
            local_response_id="forged-local-response-000001",
        )
        with patch.object(
            subject._bound_response,
            "project_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation",
            return_value=forged,
        ), self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1StrictAckProvenanceError,
            "BOUND_CHAIN_MISMATCH",
        ):
            subject.mint_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
                config=self.config,
                request=self.request,
                gen2_witnessed_ack_chain=self.chain,
                bound_strict_writer_response=self.fixture.gen2_observation,
                now=self.now,
            )

    def test_chain_projection_must_match_the_v4_writer_binding(self) -> None:
        with self.fixture._all_owner_clocks(now=self.now):
            projection = subject._ack_chain.project_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
                self.chain,
                config=self.fixture.config,
                now=self.now,
            )
        forged = replace(projection, writer_epoch=projection.writer_epoch + 1)
        with patch.object(
            subject._ack_chain,
            "project_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain",
            return_value=forged,
        ), self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1StrictAckProvenanceError,
            "CHAIN_BINDING_MISMATCH",
        ):
            subject.mint_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
                config=self.config,
                request=self.request,
                gen2_witnessed_ack_chain=self.chain,
                bound_strict_writer_response=self.fixture.gen2_observation,
                now=self.now,
            )

    def test_default_off_and_static_effect_fences_remain_in_force(self) -> None:
        with self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1StrictAckProvenanceError,
            "CONFIG_INVALID",
        ):
            subject.mint_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
                config=replace(self.config, enabled=False),
                request=self.request,
                gen2_witnessed_ack_chain=self.chain,
                bound_strict_writer_response=self.fixture.gen2_observation,
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
        self.assertNotIn("execute_next_physical_full_matrix_v4_phase", source)
        self.assertNotIn("execute_physical_wa_fi_postgres_helper_capture_bridge", source)
