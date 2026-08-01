"""Adversarial tests for the pure V4 Phase-3 retired-FI admission seam.

Nothing here binds an FD, starts PostgreSQL, invokes a runner, contacts a
host, contacts Object Storage, or changes a Writer-Witness term.  The tests
exercise only canonical evidence and the process-local V4 driver handles.
"""

from __future__ import annotations

import ast
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v4_phase3_recovery_admission as subject
from core import physical_full_matrix_v4_retired_fi_predecessor_fence as retired


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
RUN_ID = UUID("1fe9c26f-3b93-4ef4-91cc-891556c6b1a0")
PLAN_SHA256 = hashlib.sha256(b"v4-phase3-plan").hexdigest()
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase3_recovery_admission.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class PhysicalFullMatrixV4Phase3RecoveryAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = driver.PhysicalFullMatrixV4ExecutionBinding(
            campaign_id="matrix-p3-recovery-2026",
            release_sha="3" * 40,
            readiness_binding_sha256=_hash("readiness"),
            route_commitment_sha256=_hash("route"),
            four_role_binding_sha256=_hash("four-role"),
            writer_holder_site="webapp_fi",
            writer_epoch=23,
            writer_lease_id="fi-writer-lease-23",
            witnessed_term_proof_sha256=_hash("term-proof"),
            source_site="webapp_fi",
            destination_site="webapp_ir",
            roundtrip_attestation_sha256=_hash("roundtrip-attestation"),
            roundtrip_configuration_sha256=_hash("roundtrip-config"),
            witness_transition_id="witness-transition-fi-00023",
            witness_sequence=41,
        )
        self.p2_effect_start = self._p2_effect_start()
        self.p2_anchor = self._p2_anchor()
        self.p2_term = retired.RetiredFiPredecessorFenceTermPin(
            holder_site="webapp_fi",
            writer_epoch=self.binding.writer_epoch,
            writer_lease_id=self.binding.writer_lease_id,
            witness_transition_id=self.binding.witness_transition_id,
            witnessed_term_proof_sha256=self.binding.witnessed_term_proof_sha256,
        )
        self.evidence_pins = retired.RetiredFiPredecessorFenceEvidencePins(
            executor_installation_attestation_sha256=_hash("executor-install"),
            executor_scope_policy_sha256=_hash("executor-scope"),
            executor_fence_evidence_sha256=_hash("executor-fence"),
            observer_installation_attestation_sha256=_hash("observer-install"),
            observer_scope_policy_sha256=_hash("observer-scope"),
            observer_fence_evidence_sha256=_hash("observer-fence"),
        )
        self.anti_replay_policy = retired.RetiredFiPredecessorFenceAntiReplayPolicy(
            anti_replay_namespace=(
                retired.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_ANTI_REPLAY_NAMESPACE
            ),
            witness_ledger_scope_sha256=_hash("p2-witness-ledger-scope"),
        )
        self.executor_key = Ed25519PrivateKey.generate()
        self.observer_key = Ed25519PrivateKey.generate()
        self.witness_key = Ed25519PrivateKey.generate()
        self.p2_config = retired.RetiredFiPredecessorFenceVerificationConfig(
            expected_effect_start=self.p2_effect_start,
            expected_effect_start_anchor=self.p2_anchor,
            expected_predecessor_term=self.p2_term,
            expected_evidence_pins=self.evidence_pins,
            expected_anti_replay_policy=self.anti_replay_policy,
            executor_signer_public_key=_public(self.executor_key),
            observer_signer_public_key=_public(self.observer_key),
            witness_anti_replay_signer_public_key=_public(self.witness_key),
            enabled=True,
        )
        self.fence = self._verified_fence()
        self.request = self._p3_request()
        self.phase2_completion_anchor_proof = self._phase2_completion_anchor_proof(
            self.request
        )
        self.request = driver._adapter_request_with_effect_start_authority(
            request=self.request,
            authority=driver.require_physical_full_matrix_v4_effect_start_authority(
                request=self.request
            ),
            anchor_proof=driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=self.request
            ),
            predecessor_phase_completion_anchor_proof=(
                self.phase2_completion_anchor_proof
            ),
        )
        self.bootstrap_plan = self._bootstrap_plan()
        self.socket_inputs = self._socket_inputs()
        self.config = subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionConfig(
            enabled=True,
            retired_fi_predecessor_fence_config=self.p2_config,
        )

    def _p2_effect_start(self) -> retired.PhysicalFullMatrixV4EffectStartPin:
        effect = driver.PhysicalFullMatrixV4EffectStart(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=driver.PHYSICAL_FULL_MATRIX_V4_PHASES[1].sequence,
            phase_request_sha256=_hash("phase-2-request"),
            effect_key=_hash("phase-2-effect"),
            claim_id="phase-2-fence-claim-000001",
        )
        return retired.PhysicalFullMatrixV4EffectStartPin(
            run_id=effect.run_id,
            plan_sha256=effect.plan_sha256,
            phase=driver.PHYSICAL_FULL_MATRIX_V4_PHASES[1],
            effect_key=effect.effect_key,
            phase_request_sha256=effect.phase_request_sha256,
            binding=self.binding,
            claim_id=effect.claim_id,
            journaled_effect_start_identity_sha256=(
                driver.derive_physical_full_matrix_v4_effect_start_identity_sha256(effect)
            ),
        )

    def _p2_anchor(self) -> retired.PhysicalFullMatrixV4EffectStartAnchorPin:
        effect = self.p2_effect_start
        return retired.PhysicalFullMatrixV4EffectStartAnchorPin(
            schema=driver.PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA,
            run_id=effect.run_id,
            plan_sha256=effect.plan_sha256,
            phase=effect.phase,
            effect_key=effect.effect_key,
            phase_request_sha256=effect.phase_request_sha256,
            binding=effect.binding,
            claim_id=effect.claim_id,
            journaled_effect_start_identity_sha256=(
                effect.journaled_effect_start_identity_sha256
            ),
            journal_binding_sha256=_hash("journal-binding"),
            baseline_plan_binding_sha256=_hash("baseline-plan-binding"),
            anchor_genesis_sequence=7,
            anchor_genesis_head_sha256=_hash("anchor-genesis"),
            anchor_previous_sequence=9,
            anchor_previous_head_sha256=_hash("anchor-p2-previous"),
            anchor_sequence=10,
            anchor_head_sha256=_hash("anchor-p2-head"),
            anchor_commitment_sha256=_hash("anchor-p2-commitment"),
            anchor_attestation_sha256=_hash("anchor-p2-attestation"),
            anchor_local_previous_record_sha256=_hash("anchor-p2-local-previous"),
            anchor_local_event_sha256=_hash("anchor-p2-local-event"),
            anchor_occurred_at=NOW - timedelta(seconds=2),
        )

    @staticmethod
    def _signed(
        mapping: dict[str, object], *, key: Ed25519PrivateKey, domain: bytes
    ) -> bytes:
        unsigned = dict(mapping)
        signature = key.sign(domain + canonical_json_bytes(unsigned))
        payload = dict(unsigned)
        payload["signature_base64"] = base64.b64encode(signature).decode("ascii")
        return canonical_json_bytes(payload)

    def _fence_binding(self) -> dict[str, object]:
        effect = retired._effect_start_mapping(
            self.p2_effect_start, code="TEST_INVALID"
        )[1]
        anchor = retired._effect_start_anchor_mapping(
            self.p2_anchor, code="TEST_INVALID"
        )[1]
        term = retired._term_mapping(self.p2_term, code="TEST_INVALID")[1]
        evidence = retired._evidence_pins_mapping(
            self.evidence_pins, code="TEST_INVALID"
        )[1]
        return {
            "schema": retired.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_SCHEMA,
            "version": 1,
            "status": retired.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_STATUS,
            "retirement_mode": "server-side-fi-writer-retired-v1",
            "fence_id": "phase-2-fi-retirement-fence-000001",
            "fence_nonce": "P2RetirementNonce00000001",
            "retired_at": (NOW - timedelta(seconds=1)).isoformat().replace(
                "+00:00", "Z"
            ),
            "expires_at": (NOW + timedelta(seconds=80)).isoformat().replace(
                "+00:00", "Z"
            ),
            "effect_start": effect,
            "effect_start_anchor": anchor,
            "predecessor_term": term,
            "evidence_pins": evidence,
        }

    def _receipt(
        self,
        *,
        binding: dict[str, object],
        schema: str,
        kind: str,
        role: str,
        key: Ed25519PrivateKey,
        domain: bytes,
    ) -> bytes:
        binding_sha256 = hashlib.sha256(canonical_json_bytes(binding)).hexdigest()
        return self._signed(
            {
                "schema": schema,
                "version": 1,
                "kind": kind,
                "signer_role": role,
                "fence_binding": binding,
                "fence_binding_sha256": binding_sha256,
            },
            key=key,
            domain=domain,
        )

    def _witness_receipt(self, *, binding: dict[str, object]) -> bytes:
        binding_sha256 = hashlib.sha256(canonical_json_bytes(binding)).hexdigest()
        replay_key = retired.derive_retired_fi_predecessor_fence_replay_key_sha256(
            effect_start=self.p2_effect_start,
            predecessor_term=self.p2_term,
        )
        return self._signed(
            {
                "schema": retired.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_WITNESS_ADMISSION_SCHEMA,
                "version": 1,
                "kind": "witness-durable-anti-replay-admission",
                "signer_role": "witness-durable-anti-replay-ledger",
                "fence_id": binding["fence_id"],
                "fence_nonce": binding["fence_nonce"],
                "fence_binding_sha256": binding_sha256,
                "replay_key_sha256": replay_key,
                "anti_replay_namespace": self.anti_replay_policy.anti_replay_namespace,
                "anti_replay_mode": "witness-durable-single-use-admission-v1",
                "witness_ledger_scope_sha256": (
                    self.anti_replay_policy.witness_ledger_scope_sha256
                ),
                "admission_id": "p2-witness-admission-000001",
                "admission_nonce": "P2WitnessAdmissionNonce0001",
                "admitted_at": (NOW - timedelta(microseconds=500000))
                .isoformat()
                .replace("+00:00", "Z"),
                "expires_at": binding["expires_at"],
                "witness_ledger_sequence": 42,
                "witness_ledger_entry_sha256": _hash("p2-ledger-entry"),
                "witness_ledger_previous_head_sha256": _hash("p2-ledger-head"),
            },
            key=self.witness_key,
            domain=retired._WITNESS_DOMAIN,
        )

    def _verified_fence(self) -> retired.VerifiedRetiredFiPredecessorFence:
        binding = self._fence_binding()
        executor = self._receipt(
            binding=binding,
            schema=retired.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_EXECUTOR_RECEIPT_SCHEMA,
            kind="fi-root-fence-executor-evidence",
            role="fi-root-fence-executor",
            key=self.executor_key,
            domain=retired._EXECUTOR_DOMAIN,
        )
        observer = self._receipt(
            binding=binding,
            schema=retired.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_OBSERVER_RECEIPT_SCHEMA,
            kind="fi-independent-fence-observer-evidence",
            role="fi-independent-fence-observer",
            key=self.observer_key,
            domain=retired._OBSERVER_DOMAIN,
        )
        return retired.verify_retired_fi_predecessor_fence(
            executor_receipt=executor,
            observer_receipt=observer,
            witness_admission_receipt=self._witness_receipt(binding=binding),
            config=self.p2_config,
            now=NOW,
        )

    def _p3_request(
        self,
        *,
        previous_sequence: int | None = None,
        previous_head_sha256: str | None = None,
        sequence: int | None = None,
    ) -> driver.PhysicalFullMatrixV4ExecutionRequest:
        snapshot = driver._PlanSnapshot(
            canonical_plan=b"",
            plan_sha256=PLAN_SHA256,
            run_id=RUN_ID,
            binding=driver._snapshot_binding(
                self.binding, direction=("webapp_fi", "webapp_ir")
            ),
            phases=driver._phase_snapshots(),
            maximum_oracle_age_seconds=120,
        )
        request = driver._request(
            snapshot=snapshot,
            phase=snapshot.phases[2],
            binding=snapshot.binding,
        )
        claim = driver.PhysicalFullMatrixV4PhaseClaim(
            run_id=request.run_id,
            plan_sha256=request.plan_sha256,
            sequence=request.phase.sequence,
            phase_request_sha256=request.phase_request_sha256,
            effect_key=request.effect_key,
            claim_id="phase-3-recovery-claim-000001",
        )
        start = driver.PhysicalFullMatrixV4EffectStart(
            run_id=request.run_id,
            plan_sha256=request.plan_sha256,
            sequence=request.phase.sequence,
            phase_request_sha256=request.phase_request_sha256,
            effect_key=request.effect_key,
            claim_id=claim.claim_id,
        )
        authority = driver._mint_effect_start_authority(
            effect_start=start, claim=claim, request=request
        )
        p2 = self.p2_anchor
        anchor = driver._mint_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request,
            effect_start=start,
            journal_binding_sha256=p2.journal_binding_sha256,
            baseline_plan_binding_sha256=p2.baseline_plan_binding_sha256,
            anchor_genesis_sequence=p2.anchor_genesis_sequence,
            anchor_genesis_head_sha256=p2.anchor_genesis_head_sha256,
            anchor_previous_sequence=(
                p2.anchor_sequence + 1
                if previous_sequence is None
                else previous_sequence
            ),
            anchor_previous_head_sha256=(
                _hash("anchor-p2-completion-head")
                if previous_head_sha256 is None
                else previous_head_sha256
            ),
            anchor_sequence=(p2.anchor_sequence + 2 if sequence is None else sequence),
            anchor_head_sha256=_hash("anchor-p3-head"),
            anchor_commitment_sha256=_hash("anchor-p3-commitment"),
            anchor_attestation_sha256=_hash("anchor-p3-attestation"),
            anchor_local_previous_record_sha256=_hash("anchor-p3-local-previous"),
            anchor_local_event_sha256=_hash("anchor-p3-local-event"),
            anchor_occurred_at=NOW,
        )
        return driver._adapter_request_with_effect_start_authority(
            request=request, authority=authority, anchor_proof=anchor
        )

    def _phase2_completion_anchor_proof(
        self,
        request: driver.PhysicalFullMatrixV4ExecutionRequest,
        *,
        predecessor_effect_start: driver.PhysicalFullMatrixV4EffectStart | None = None,
    ) -> driver.PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof:
        """Private test seam mirroring the journal's already-validated facts.

        Production never calls this driver-private mint: it obtains this
        process-local object exclusively from the root receipt journal after
        durable append/readback.  The journal suite separately covers that
        source; this P3 suite exercises the downstream exact cross-pins.
        """

        p2 = self.p2_effect_start
        p2_anchor = self.p2_anchor
        predecessor = predecessor_effect_start
        if predecessor is None:
            predecessor = driver.PhysicalFullMatrixV4EffectStart(
                run_id=p2.run_id,
                plan_sha256=p2.plan_sha256,
                sequence=p2.phase.sequence,
                phase_request_sha256=p2.phase_request_sha256,
                effect_key=p2.effect_key,
                claim_id=p2.claim_id,
            )
        return driver._mint_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
            request=request,
            predecessor_effect_start=predecessor,
            journal_binding_sha256=p2_anchor.journal_binding_sha256,
            baseline_plan_binding_sha256=p2_anchor.baseline_plan_binding_sha256,
            anchor_genesis_sequence=p2_anchor.anchor_genesis_sequence,
            anchor_genesis_head_sha256=p2_anchor.anchor_genesis_head_sha256,
            predecessor_effect_start_anchor_previous_sequence=(
                p2_anchor.anchor_previous_sequence
            ),
            predecessor_effect_start_anchor_previous_head_sha256=(
                p2_anchor.anchor_previous_head_sha256
            ),
            predecessor_effect_start_anchor_sequence=p2_anchor.anchor_sequence,
            predecessor_effect_start_anchor_head_sha256=p2_anchor.anchor_head_sha256,
            predecessor_effect_start_anchor_commitment_sha256=(
                p2_anchor.anchor_commitment_sha256
            ),
            predecessor_effect_start_anchor_attestation_sha256=(
                p2_anchor.anchor_attestation_sha256
            ),
            predecessor_effect_start_anchor_local_previous_record_sha256=(
                p2_anchor.anchor_local_previous_record_sha256
            ),
            predecessor_effect_start_anchor_local_event_sha256=(
                p2_anchor.anchor_local_event_sha256
            ),
            predecessor_effect_started_at=p2_anchor.anchor_occurred_at,
            predecessor_completion_receipt_sha256=_hash("p2-completion-receipt"),
            predecessor_completion_anchor_previous_sequence=p2_anchor.anchor_sequence,
            predecessor_completion_anchor_previous_head_sha256=p2_anchor.anchor_head_sha256,
            predecessor_completion_anchor_sequence=p2_anchor.anchor_sequence + 1,
            predecessor_completion_anchor_head_sha256=_hash("anchor-p2-completion-head"),
            predecessor_completion_anchor_commitment_sha256=(
                _hash("anchor-p2-completion-commitment")
            ),
            predecessor_completion_anchor_attestation_sha256=(
                _hash("anchor-p2-completion-attestation")
            ),
            predecessor_completion_anchor_local_previous_record_sha256=(
                _hash("anchor-p2-completion-local-previous")
            ),
            predecessor_completion_anchor_local_event_sha256=(
                _hash("anchor-p2-completion-local-event")
            ),
            predecessor_completed_at=NOW - timedelta(microseconds=250000),
        )

    def _bootstrap_plan(
        self,
        *,
        writer_epoch: int | None = None,
        route_binding_sha256: str | None = None,
    ) -> subject.PhysicalFullMatrixV4Phase3BootstrapPlanEvidence:
        writer_epoch = self.binding.writer_epoch if writer_epoch is None else writer_epoch
        route = (
            self.binding.route_commitment_sha256
            if route_binding_sha256 is None
            else route_binding_sha256
        )
        payload = {
            "schema": "gold-trade-physical-postgres-standby-bootstrap-plan-v1",
            "kind": "local_standby_bootstrap_materialization_intent",
            "bootstrap_id": _hash("bootstrap"),
            "source_site": "webapp_fi",
            "receiver_site": "webapp_ir",
            "receiver_role": "standby",
            "bundle_id": _hash("bundle"),
            "stage_receipt_sha256": _hash("stage-receipt"),
            "route_binding_sha256": route,
            "manifest_sha256es": [_hash("manifest")],
            "object_versions": [
                {"object_key": "normal/base-backup.age", "version_id": "v1"}
            ],
            "terminal_wal_lsn": "0/1",
            "writer_term": {
                "holder_site": "webapp_fi",
                "writer_epoch": writer_epoch,
                "writer_lease_id": self.binding.writer_lease_id,
                "witness_transition_id": self.binding.witness_transition_id,
                "witnessed_term_proof_sha256": self.binding.witnessed_term_proof_sha256,
            },
            "recovery_evidence_sha256": _hash("recovery-evidence"),
            "source_stage_device": 101,
            "source_stage_inode": 102,
            "target_pgdata_device": 103,
            "target_pgdata_inode": 104,
            "recovery_signal_seed_sha256": _hash("recovery-signal-seed"),
        }
        raw = canonical_json_bytes(payload)
        return subject.PhysicalFullMatrixV4Phase3BootstrapPlanEvidence(
            canonical_plan=raw,
            plan_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _socket_inputs(
        self,
        *,
        route_binding_sha256: str | None = None,
    ) -> subject.PhysicalFullMatrixV4Phase3SocketOnlyRecoveryInputs:
        route = (
            self.binding.route_commitment_sha256
            if route_binding_sha256 is None
            else route_binding_sha256
        )
        payload = {
            "schema": "gold-trade-physical-wa-ir-postgres-socket-only-recovery-input-v1",
            "status": "default-off-socket-only-recovery-input",
            "campaign_id": self.binding.campaign_id,
            "release_sha": self.binding.release_sha,
            "sealed_release_descriptor_sha256": _hash("sealed-release"),
            "deployment_manifest_lock_sha256": _hash("manifest-lock"),
            "route_binding_sha256": route,
            "postgres_image": "registry.example/gold-trade/postgres@sha256:" + _hash("postgres"),
            "postgres_major": 15,
            "network_mode": "none",
            "tcp_listener": "disabled",
            "unix_socket_directory": "/var/run/postgresql",
            "unix_socket_port": 5432,
            "socket_authentication": "peer-local-only",
            "recovery_mode": "standby-replay-only",
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
            "promotion_authorized": False,
            "full_matrix_authorized": False,
        }
        raw = canonical_json_bytes(payload)
        return subject.PhysicalFullMatrixV4Phase3SocketOnlyRecoveryInputs(
            canonical_input=raw,
            input_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _inputs(self, **changes: object) -> subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionInputs:
        values: dict[str, object] = {
            "adapter_request": self.request,
            "retired_fi_predecessor_fence": self.fence,
            "bootstrap_plan": self.bootstrap_plan,
            "rendered_socket_only_inputs": self.socket_inputs,
        }
        values.update(changes)
        return subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionInputs(**values)

    def _admit(self, **changes: object) -> subject.PhysicalFullMatrixV4Phase3RecoveryAdmission:
        return subject.admit_physical_full_matrix_v4_phase3_recovery(
            config=self.config, inputs=self._inputs(**changes), now=NOW
        )

    def test_real_p2_fence_and_exact_completion_bridge_admit_evidence_only(self) -> None:
        admitted = self._admit()
        self.assertIs(
            admitted,
            subject.require_admitted_physical_full_matrix_v4_phase3_recovery(admitted),
        )
        self.assertEqual(
            self.phase2_completion_anchor_proof.predecessor_completion_receipt_sha256,
            admitted.predecessor_completion_receipt_sha256,
        )
        self.assertEqual(
            self.phase2_completion_anchor_proof.predecessor_completion_anchor_sequence,
            admitted.predecessor_completion_anchor_sequence,
        )
        self.assertEqual(
            self.phase2_completion_anchor_proof.predecessor_completion_anchor_head_sha256,
            admitted.predecessor_completion_anchor_head_sha256,
        )
        self.assertFalse(admitted.runner_authorized)
        self.assertFalse(admitted.execution_authorized)
        self.assertFalse(admitted.full_matrix_executed)

    def test_missing_completion_bridge_is_refused(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionError,
            "P2_COMPLETION_ANCHOR_REQUIRED",
        ):
            self._admit(adapter_request=self._p3_request())
        with self.assertRaises(TypeError):
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionInputs(
                phase2_completion_anchor_proof=object()
            )

    def test_completion_bridge_must_repeat_the_exact_verified_p2_effect(self) -> None:
        """A valid driver bridge for another P2 cannot replace this P2 fence."""

        base = self._p3_request()
        foreign_predecessor = driver.PhysicalFullMatrixV4EffectStart(
            run_id=self.p2_effect_start.run_id,
            plan_sha256=self.p2_effect_start.plan_sha256,
            sequence=self.p2_effect_start.phase.sequence,
            phase_request_sha256=_hash("foreign-p2-request"),
            effect_key=_hash("foreign-p2-effect"),
            claim_id="foreign-phase-2-claim-000001",
        )
        foreign_bridge = self._phase2_completion_anchor_proof(
            base,
            predecessor_effect_start=foreign_predecessor,
        )
        request = driver._adapter_request_with_effect_start_authority(
            request=base,
            authority=driver.require_physical_full_matrix_v4_effect_start_authority(
                request=base
            ),
            anchor_proof=driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=base
            ),
            predecessor_phase_completion_anchor_proof=foreign_bridge,
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionError,
            "P2_COMPLETION_ANCHOR_INVALID",
        ):
            self._admit(adapter_request=request)

    def test_tampered_attached_completion_bridge_is_refused(self) -> None:
        """The P3 seam maps a driver capability-tamper refusal to its gate."""

        object.__setattr__(
            self.phase2_completion_anchor_proof,
            "predecessor_completion_anchor_head_sha256",
            _hash("tampered-p2-completion-head"),
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionError,
            "P2_COMPLETION_ANCHOR_INVALID",
        ):
            self._admit()

    def test_default_off_refuses_before_traversing_untrusted_inputs(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionError, "DISABLED"
        ):
            subject.admit_physical_full_matrix_v4_phase3_recovery(
                config=subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionConfig(),
                inputs=object(),
                now=NOW,
            )

    def test_ordinary_phase3_request_without_private_start_handles_is_refused(self) -> None:
        snapshot = driver._PlanSnapshot(
            canonical_plan=b"",
            plan_sha256=PLAN_SHA256,
            run_id=RUN_ID,
            binding=driver._snapshot_binding(
                self.binding, direction=("webapp_fi", "webapp_ir")
            ),
            phases=driver._phase_snapshots(),
            maximum_oracle_age_seconds=120,
        )
        ordinary = driver._request(
            snapshot=snapshot, phase=snapshot.phases[2], binding=snapshot.binding
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionError,
            "EFFECT_START_REQUIRED",
        ):
            self._admit(adapter_request=ordinary)

    def test_predecessor_term_and_v4_route_binding_are_exact(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionError,
            "PREDECESSOR_TERM_MISMATCH",
        ):
            self._admit(bootstrap_plan=self._bootstrap_plan(writer_epoch=24))
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionError,
            "PREDECESSOR_TERM_MISMATCH",
        ):
            self._admit(
                bootstrap_plan=self._bootstrap_plan(
                    route_binding_sha256=_hash("other-route")
                ),
                rendered_socket_only_inputs=self._socket_inputs(
                    route_binding_sha256=_hash("other-route")
                ),
            )

    def test_socket_only_input_must_match_the_exact_bootstrap_route(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionError,
            "SOCKET_INPUT_INVALID",
        ):
            self._admit(rendered_socket_only_inputs=self._socket_inputs(route_binding_sha256=_hash("other-route")))

    def test_intervening_or_foreign_anchor_cannot_replace_completion_bridge(self) -> None:
        request = self._p3_request(
            previous_sequence=self.p2_anchor.anchor_sequence + 1,
            previous_head_sha256=_hash("foreign-intermediate-anchor-head"),
            sequence=self.p2_anchor.anchor_sequence + 2,
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionError,
            "P2_COMPLETION_ANCHOR_REQUIRED",
        ):
            self._admit(adapter_request=request)

    def test_p2_anchor_must_repeat_its_exact_phase2_effect_projection(self) -> None:
        request_facts = subject._request(self.request)
        plan_facts = subject._plan(self.bootstrap_plan)
        rendered_facts = subject._rendered(self.socket_inputs, plan=plan_facts)
        forged = SimpleNamespace(
            effect_start=self.p2_effect_start,
            predecessor_term=self.p2_term,
            effect_start_anchor=replace(
                self.p2_anchor, claim_id="phase-2-different-claim-000001"
            ),
            retired_at=NOW - timedelta(seconds=1),
            admitted_at=NOW - timedelta(microseconds=500000),
            expires_at=NOW + timedelta(seconds=80),
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase3RecoveryAdmissionError,
            "RETIRED_FI_ANCHOR_INVALID",
        ):
            subject._cross_pin(
                request=request_facts,
                retired=forged,
                plan=plan_facts,
                rendered=rendered_facts,
                now=NOW,
            )

    def test_source_has_no_legacy_runtime_runner_fd_or_live_term_import(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imported
            & {
                "boto3",
                "botocore",
                "docker",
                "http",
                "httpx",
                "os",
                "paramiko",
                "pathlib",
                "requests",
                "socket",
                "subprocess",
                "urllib",
            }
        )
        for forbidden in (
            "physical_postgres_standby_bootstrap_materialization",
            "physical_wa_ir_postgres_recovery_materialization_runtime",
            "require_live_object_delta_role_matrix_witnessed_term",
            "run_root_owned_wa_ir_postgres_recovery_materialization",
            "bind_wa_ir_postgres_socket_only_recovery_materialization_fds",
        ):
            self.assertNotIn(forbidden, source)
