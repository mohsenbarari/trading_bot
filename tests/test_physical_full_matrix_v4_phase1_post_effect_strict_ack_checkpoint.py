"""Fail-closed tests for the quarantined V4 Phase-1 checkpoint grammar.

The signed grammar remains private format-validation code only.  These tests
intentionally do not synthesize a checkpoint: Gen2's public pending handoff
does not prove which database root transaction flushed it.
"""

from __future__ import annotations

import ast
from dataclasses import fields, replace
from datetime import datetime
import hashlib
from pathlib import Path
import unittest
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v2_gen2_witnessed_campaign_readiness as readiness_owner
from core import physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint as subject
from tests.test_physical_full_matrix_v2_gen2_witnessed_ack_chain import (
    Gen2WitnessedAckChainFixture,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = Gen2WitnessedAckChainFixture()
        cls.fixture.setUp()
        cls.now: datetime = cls.fixture.now
        chain = cls.fixture.mint_chain(now=cls.now)
        readiness_binding = readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding(
            **{
                item.name: getattr(chain, item.name)
                for item in fields(
                    readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding
                )
            }
        )
        readiness_config = (
            readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig(
                binding=readiness_binding,
                gen2_witnessed_ack_chain_config=cls.fixture.config,
                enabled=True,
            )
        )
        with cls.fixture._all_owner_clocks(now=cls.now):
            readiness = (
                readiness_owner.mint_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                    config=readiness_config,
                    inputs=readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignInputs(
                        gen2_witnessed_ack_chain=chain,
                    ),
                    now=cls.now,
                )
            )
        cls.binding = driver.PhysicalFullMatrixV4ExecutionBinding(
            campaign_id=readiness_binding.campaign_id,
            release_sha=readiness_binding.release_sha,
            readiness_binding_sha256=readiness.report.binding_sha256,
            route_commitment_sha256=readiness_binding.route_commitment_sha256,
            four_role_binding_sha256=readiness_binding.four_role_binding_sha256,
            writer_holder_site=readiness_binding.writer_holder_site,
            writer_epoch=readiness_binding.writer_epoch,
            writer_lease_id=readiness_binding.writer_lease_id,
            witnessed_term_proof_sha256=readiness_binding.witnessed_term_proof_sha256,
            source_site=readiness_binding.source_site,
            destination_site=readiness_binding.destination_site,
            roundtrip_attestation_sha256=readiness_binding.roundtrip_attestation_sha256,
            roundtrip_configuration_sha256=readiness_binding.roundtrip_configuration_sha256,
            witness_transition_id=readiness_binding.witness_transition_id,
            witness_sequence=readiness_binding.witness_sequence,
        )
        execution_config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=cls.binding,
            readiness=readiness,
            run_id=UUID("f176d09f-5105-4b86-b123-4142c3997434"),
            enabled=True,
        )
        with cls.fixture._all_owner_clocks(now=cls.now):
            plan = driver.build_physical_full_matrix_v4_execution_plan(
                config=execution_config
            )
        snapshot = driver._snapshot(plan)
        cls.pre_effect_request = driver._request(
            snapshot=snapshot,
            phase=snapshot.phases[0],
            binding=snapshot.binding,
            pre_effect_readiness_evidence=driver.PhysicalFullMatrixV4ReadinessEvidence(
                binding=cls.binding,
                readiness=readiness,
            ),
        )
        cls.request = cls._post_effect_request(cls.pre_effect_request)
        cls.private_key = Ed25519PrivateKey.generate()
        public = _public(cls.private_key)
        cls.config = subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig(
            fi_checkpoint_signer_public_key=public,
            fi_checkpoint_signer_key_id="ed25519-sha256:"
            + hashlib.sha256(public).hexdigest(),
            enabled=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.tearDown()

    @classmethod
    def _post_effect_request(cls, request):
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
            effect_start=effect_start,
            claim=claim,
            request=request,
        )
        anchor = driver._mint_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request,
            effect_start=effect_start,
            journal_binding_sha256=_hash("checkpoint-journal-binding"),
            baseline_plan_binding_sha256=_hash("checkpoint-baseline-plan-binding"),
            anchor_genesis_sequence=0,
            anchor_genesis_head_sha256="0" * 64,
            anchor_previous_sequence=0,
            anchor_previous_head_sha256="0" * 64,
            anchor_sequence=1,
            anchor_head_sha256=_hash("checkpoint-phase1-start-head"),
            anchor_commitment_sha256=_hash("checkpoint-phase1-start-commitment"),
            anchor_attestation_sha256=_hash("checkpoint-phase1-start-attestation"),
            anchor_local_previous_record_sha256="0" * 64,
            anchor_local_event_sha256=_hash("checkpoint-phase1-start-event"),
            anchor_occurred_at=cls.now,
        )
        return driver._adapter_request_with_effect_start_authority(
            request=request,
            authority=authority,
            anchor_proof=anchor,
        )

    def _capture(self):
        return subject.begin_physical_full_matrix_v4_phase1_post_effect_strict_ack_capture(
            config=self.config,
            request=self.request,
            now=self.now,
        )

    def test_post_effect_capture_is_non_authorizing_but_pre_effect_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError,
            "POST_EFFECT_AUTHORITY_REQUIRED",
        ):
            subject.begin_physical_full_matrix_v4_phase1_post_effect_strict_ack_capture(
                config=self.config,
                request=self.pre_effect_request,
                now=self.now,
            )
        capture = self._capture()
        self.assertFalse(capture.writer_authorized)
        self.assertFalse(capture.promotion_authorized)
        self.assertFalse(capture.execution_authorized)
        self.assertFalse(capture.full_matrix_authorized)
        self.assertFalse(capture.full_matrix_executed)
        self.assertFalse(subject._CAPTURE_STATES[capture].consumed)
        self.assertFalse(subject._CAPTURE_STATES[capture].same_root_envelope_claimed)

    def test_public_raw_pending_prepare_and_projection_are_unavailable(self) -> None:
        capture = self._capture()
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError,
            "SAME_ROOT_ENVELOPE_REQUIRED",
        ):
            subject.prepare_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint(
                config=self.config,
                request=self.request,
                capture=capture,
                pending_gen2_commit=object(),
                fi_checkpoint_private_key=self.private_key,
                now=self.now,
            )
        self.assertFalse(subject._CAPTURE_STATES[capture].consumed)
        self.assertFalse(subject._CAPTURE_STATES[capture].same_root_envelope_claimed)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError,
            "SAME_ROOT_ENVELOPE_REQUIRED",
        ):
            subject.project_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_row_values(
                object(),
                config=self.config,
                request=self.request,
                pending_gen2_commit=object(),
            )

    def test_default_off_and_public_exports_remain_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError,
            "CHECKPOINT_DISABLED",
        ):
            subject.begin_physical_full_matrix_v4_phase1_post_effect_strict_ack_capture(
                config=replace(self.config, enabled=False),
                request=self.request,
                now=self.now,
            )
        self.assertNotIn(
            "prepare_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint",
            subject.__all__,
        )
        self.assertNotIn(
            "project_prepared_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_row_values",
            subject.__all__,
        )

    def test_module_has_no_live_transaction_or_phase_success_surface(self) -> None:
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
            {
                "asyncio", "boto3", "botocore", "docker", "httpx",
                "paramiko", "requests", "socket", "subprocess", "urllib",
            }
            & imports
        )
        self.assertNotIn("execute_", source)
        self.assertNotIn("oracle-succeeded", source)
        self.assertNotIn("async def", source)


if __name__ == "__main__":
    unittest.main()
