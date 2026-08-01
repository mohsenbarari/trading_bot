"""Adversarial tests for the pure V4 Phase-6 FI rebuild admission seam."""

from __future__ import annotations

import ast
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
import unittest
from unittest.mock import patch

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v4_phase6_failback_rebuild_admission as subject


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase6_failback_rebuild_admission.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _live_phase6_evidence(
    *,
    binding: driver.PhysicalFullMatrixV4ExecutionBinding,
    phase5_completion_receipt_sha256: str,
    phase5_completion_anchor: dict[str, object],
) -> tuple[
    subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionConfig,
    subject.PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence,
    subject.PhysicalFullMatrixV4Phase6SocketOnlyFailbackInputs,
]:
    """Build only canonical test evidence for a completed in-memory P5."""

    plan_payload: dict[str, object] = {
        "schema": "gold-trade-physical-full-matrix-v4-phase6-reverse-recovery-plan-v1",
        "status": "canonical-reverse-recovery-plan-evidence-only",
        "plan_id": _hash("live-p6-reverse-plan-id"),
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "source_site": "webapp_ir",
        "destination_site": "webapp_fi",
        "object_storage_namespace": "physical-failback",
        "route_binding_sha256": binding.route_commitment_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "phase5_completion_receipt_sha256": phase5_completion_receipt_sha256,
        "phase5_completion_anchor_sequence": phase5_completion_anchor["sequence"],
        "phase5_completion_anchor_head_sha256": phase5_completion_anchor["head_sha256"],
        "phase5_completion_anchor_commitment_sha256": phase5_completion_anchor[
            "commitment_sha256"
        ],
        "phase5_completion_anchor_attestation_sha256": phase5_completion_anchor[
            "attestation_sha256"
        ],
        "writer_term": {
            "holder_site": "webapp_ir",
            "writer_epoch": binding.writer_epoch,
            "writer_lease_id": binding.writer_lease_id,
            "witness_transition_id": binding.witness_transition_id,
            "witnessed_term_proof_sha256": binding.witnessed_term_proof_sha256,
        },
        "bundle_id": _hash("live-p6-bundle"),
        "stage_receipt_sha256": _hash("live-p6-stage-receipt"),
        "manifest_sha256es": [_hash("live-p6-manifest")],
        "object_versions": [
            {
                "object_key": "physical-failback/live-base.tar.age",
                "version_id": "phase6-live-exact-version-000001",
            }
        ],
        "terminal_wal_lsn": "0/1A2B3C",
        "recovery_evidence_sha256": _hash("live-p6-recovery-evidence"),
        "recovery_bundle_binding_sha256": _hash("live-p6-recovery-bundle-binding"),
    }
    plan_raw = canonical_json_bytes(plan_payload)
    plan = subject.PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence(
        canonical_plan=plan_raw,
        plan_sha256=hashlib.sha256(plan_raw).hexdigest(),
    )
    socket_payload: dict[str, object] = {
        "schema": "gold-trade-physical-full-matrix-v4-phase6-socket-only-failback-input-v1",
        "status": "default-off-socket-only-failback-rebuild-input",
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "reverse_recovery_plan_sha256": plan.plan_sha256,
        "phase5_completion_receipt_sha256": phase5_completion_receipt_sha256,
        "route_binding_sha256": binding.route_commitment_sha256,
        "postgres_image": "postgres:15.8@sha256:" + "a" * 64,
        "postgres_major": 15,
        "network_mode": "none",
        "tcp_listener": "disabled",
        "unix_socket_directory": "/var/run/postgresql",
        "unix_socket_port": 5432,
        "socket_authentication": "peer-local-only",
        "recovery_mode": "standby-replay-only",
        "direct_site_control": "forbidden",
        "destination_object_ingest": "pull-only",
        "fd_binder_authorized": False,
        "runner_authorized": False,
        "materialization_authorized": False,
        "promotion_authorized": False,
        "writer_authorized": False,
        "traffic_switch_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    socket_raw = canonical_json_bytes(socket_payload)
    socket = subject.PhysicalFullMatrixV4Phase6SocketOnlyFailbackInputs(
        canonical_input=socket_raw,
        input_sha256=hashlib.sha256(socket_raw).hexdigest(),
    )
    config = subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionConfig(
        expected_phase5_ir_writer_binding=binding,
        expected_reverse_recovery_plan_sha256=plan.plan_sha256,
        expected_socket_only_input_sha256=socket.input_sha256,
        enabled=True,
    )
    return config, plan, socket


class _InMemoryPhase6AdmissionAdapter:
    """A semantic-driver test adapter; it neither rebuilds nor runs anything."""

    def __init__(
        self,
        *,
        config: subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionConfig,
        phase5_completion_receipt: bytes,
        reverse_recovery_plan: subject.PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence,
        socket_only_inputs: subject.PhysicalFullMatrixV4Phase6SocketOnlyFailbackInputs,
        now: datetime,
    ) -> None:
        self.config = config
        self.phase5_completion_receipt = phase5_completion_receipt
        self.reverse_recovery_plan = reverse_recovery_plan
        self.socket_only_inputs = socket_only_inputs
        self.now = now
        self.request: driver.PhysicalFullMatrixV4ExecutionRequest | None = None
        self.admission: subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission | None = None

    def execute_phase(self, *, request: driver.PhysicalFullMatrixV4ExecutionRequest):
        self.request = request
        self.admission = subject.admit_physical_full_matrix_v4_phase6_failback_rebuild(
            config=self.config,
            inputs=subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionInputs(
                adapter_request=request,
                phase5_completion_anchor_proof=(
                    driver.require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
                        request=request
                    )
                ),
                phase5_completion_receipt=self.phase5_completion_receipt,
                reverse_recovery_plan=self.reverse_recovery_plan,
                rendered_socket_only_inputs=self.socket_only_inputs,
            ),
            now=self.now,
        )
        return driver.PhysicalFullMatrixV4PhaseOracle(
            schema=driver.PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA,
            status="oracle-succeeded",
            phase=request.phase.name,
            oracle=request.phase.oracle,
            transport_profile=request.phase.transport_profile,
            effect_key=request.effect_key,
            evidence_sha256=_hash("live-p6-admission-oracle"),
            observed_at=self.now,
            readiness_evidence=request.pre_effect_readiness_evidence,
        )


class _Fixture:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        self.binding = driver.PhysicalFullMatrixV4ExecutionBinding(
            campaign_id="matrix-phase6-failback-2026",
            release_sha="6" * 40,
            readiness_binding_sha256=_hash("p5-readiness"),
            route_commitment_sha256=_hash("p5-reverse-route"),
            four_role_binding_sha256=_hash("p5-four-role"),
            writer_holder_site="webapp_ir",
            writer_epoch=31,
            writer_lease_id="webapp-ir-writer-lease-31",
            witnessed_term_proof_sha256=_hash("p5-term"),
            source_site="webapp_ir",
            destination_site="webapp_fi",
            roundtrip_attestation_sha256=_hash("p5-roundtrip"),
            roundtrip_configuration_sha256=_hash("p5-roundtrip-config"),
            witness_transition_id="witness-transition-p5-ir-writer-000031",
            witness_sequence=61,
        )
        self.phase5_receipt_sha = _hash("p5-completion-receipt")
        self.phase5_completion_head = _hash("p5-completion-anchor-head")
        self.phase5_completion_commitment = _hash("p5-completion-anchor-commitment")
        self.phase5_completion_attestation = _hash("p5-completion-anchor-attestation")
        self.plan_payload = self._plan_payload()
        self.plan_raw = canonical_json_bytes(self.plan_payload)
        self.plan = subject.PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence(
            canonical_plan=self.plan_raw,
            plan_sha256=hashlib.sha256(self.plan_raw).hexdigest(),
        )
        self.socket_payload = self._socket_payload()
        self.socket_raw = canonical_json_bytes(self.socket_payload)
        self.socket = subject.PhysicalFullMatrixV4Phase6SocketOnlyFailbackInputs(
            canonical_input=self.socket_raw,
            input_sha256=hashlib.sha256(self.socket_raw).hexdigest(),
        )
        self.config = subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionConfig(
            expected_phase5_ir_writer_binding=self.binding,
            expected_reverse_recovery_plan_sha256=self.plan.plan_sha256,
            expected_socket_only_input_sha256=self.socket.input_sha256,
            enabled=True,
        )
        self.request = driver.PhysicalFullMatrixV4ExecutionRequest(
            run_id=UUID("35652072-b565-4803-8b9c-72d0f5ff18bb"),
            plan_sha256=_hash("v4-plan"),
            phase=driver.PHYSICAL_FULL_MATRIX_V4_PHASES[5],
            effect_key=_hash("p6-effect"),
            phase_request_sha256=_hash("p6-request"),
            binding=self.binding,
        )

    def _plan_payload(self) -> dict[str, object]:
        return {
            "schema": "gold-trade-physical-full-matrix-v4-phase6-reverse-recovery-plan-v1",
            "status": "canonical-reverse-recovery-plan-evidence-only",
            "plan_id": _hash("p6-reverse-plan-id"),
            "campaign_id": self.binding.campaign_id,
            "release_sha": self.binding.release_sha,
            "source_site": "webapp_ir",
            "destination_site": "webapp_fi",
            "object_storage_namespace": "physical-failback",
            "route_binding_sha256": self.binding.route_commitment_sha256,
            "four_role_binding_sha256": self.binding.four_role_binding_sha256,
            "phase5_completion_receipt_sha256": self.phase5_receipt_sha,
            "phase5_completion_anchor_sequence": 51,
            "phase5_completion_anchor_head_sha256": self.phase5_completion_head,
            "phase5_completion_anchor_commitment_sha256": self.phase5_completion_commitment,
            "phase5_completion_anchor_attestation_sha256": self.phase5_completion_attestation,
            "writer_term": {
                "holder_site": "webapp_ir",
                "writer_epoch": self.binding.writer_epoch,
                "writer_lease_id": self.binding.writer_lease_id,
                "witness_transition_id": self.binding.witness_transition_id,
                "witnessed_term_proof_sha256": self.binding.witnessed_term_proof_sha256,
            },
            "bundle_id": _hash("p6-bundle"),
            "stage_receipt_sha256": _hash("p6-stage-receipt"),
            "manifest_sha256es": [_hash("p6-manifest-a")],
            "object_versions": [
                {"object_key": "physical-failback/base.tar.age", "version_id": "v6exactobject0001"}
            ],
            "terminal_wal_lsn": "0/1A2B3C",
            "recovery_evidence_sha256": _hash("p6-recovery-evidence"),
            "recovery_bundle_binding_sha256": _hash("p6-recovery-bundle-binding"),
        }

    def _socket_payload(self) -> dict[str, object]:
        return {
            "schema": "gold-trade-physical-full-matrix-v4-phase6-socket-only-failback-input-v1",
            "status": "default-off-socket-only-failback-rebuild-input",
            "campaign_id": self.binding.campaign_id,
            "release_sha": self.binding.release_sha,
            "reverse_recovery_plan_sha256": self.plan.plan_sha256,
            "phase5_completion_receipt_sha256": self.phase5_receipt_sha,
            "route_binding_sha256": self.binding.route_commitment_sha256,
            "postgres_image": "postgres:15.8@sha256:" + "a" * 64,
            "postgres_major": 15,
            "network_mode": "none",
            "tcp_listener": "disabled",
            "unix_socket_directory": "/var/run/postgresql",
            "unix_socket_port": 5432,
            "socket_authentication": "peer-local-only",
            "recovery_mode": "standby-replay-only",
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
            "fd_binder_authorized": False,
            "runner_authorized": False,
            "materialization_authorized": False,
            "promotion_authorized": False,
            "writer_authorized": False,
            "traffic_switch_authorized": False,
            "execution_authorized": False,
            "full_matrix_authorized": False,
            "full_matrix_executed": False,
        }

    def plan_with(self, payload: dict[str, object]) -> subject.PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence:
        raw = canonical_json_bytes(payload)
        return subject.PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence(
            canonical_plan=raw, plan_sha256=hashlib.sha256(raw).hexdigest()
        )

    def socket_with(self, payload: dict[str, object]) -> subject.PhysicalFullMatrixV4Phase6SocketOnlyFailbackInputs:
        raw = canonical_json_bytes(payload)
        return subject.PhysicalFullMatrixV4Phase6SocketOnlyFailbackInputs(
            canonical_input=raw, input_sha256=hashlib.sha256(raw).hexdigest()
        )

    def _phase5_completion_receipt(
        self, *, effect_start: driver.PhysicalFullMatrixV4EffectStart
    ) -> bytes:
        """Make parser-valid P5 receipt bytes for this pure downstream test.

        This deliberately does not execute P5.  Production obtains the bytes
        from the root journal only after durable append/readback; the receipt
        journal suite owns that behavior.  Here we only exercise that P6
        rejects anything which is not an exact parsed P5 receipt.
        """

        request = driver.PhysicalFullMatrixV4ExecutionRequest(
            run_id=self.request.run_id,
            plan_sha256=self.request.plan_sha256,
            phase=driver.PHYSICAL_FULL_MATRIX_V4_PHASES[4],
            effect_key=effect_start.effect_key,
            phase_request_sha256=effect_start.phase_request_sha256,
            binding=self.binding,
        )
        phase = driver._phase_snapshots()[4]
        oracle = driver.PhysicalFullMatrixV4PhaseOracle(
            schema=driver.PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA,
            status="oracle-succeeded",
            phase=phase.name,
            oracle=phase.oracle,
            transport_profile=phase.transport_profile,
            effect_key=effect_start.effect_key,
            evidence_sha256=_hash("p5-completion-oracle"),
            observed_at=self.now,
            readiness_evidence=None,
        )
        return driver._canonical(
            driver._receipt_body(
                request=request,
                phase=phase,
                oracle=oracle,
                successor=None,
                previous_receipt_sha256="0" * 64,
                recorded_at=self.now,
            ),
            code="TEST_P5_RECEIPT_INVALID",
        ) + b"\n"

    def inputs_with_exact_p5_completion(
        self,
    ) -> tuple[
        subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionInputs,
        subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionConfig,
    ]:
        """Return only root-shaped P5-completion/P6-start correlations.

        The private driver mints below stand in for facts which a real root
        receipt journal has already persisted and re-read.  This gives the
        Phase-6 seam a positive-path test without importing a legacy runtime
        or opening any storage/network/database boundary.
        """

        p5_effect = driver.PhysicalFullMatrixV4EffectStart(
            run_id=self.request.run_id,
            plan_sha256=self.request.plan_sha256,
            sequence=driver.PHYSICAL_FULL_MATRIX_V4_PHASES[4].sequence,
            phase_request_sha256=_hash("p5-request"),
            effect_key=_hash("p5-effect"),
            claim_id="phase-5-ir-writer-claim-000001",
        )
        receipt = self._phase5_completion_receipt(effect_start=p5_effect)
        receipt_sha256 = driver.parse_physical_full_matrix_v4_run_receipt(
            receipt
        ).receipt_sha256

        p6_claim = driver.PhysicalFullMatrixV4PhaseClaim(
            run_id=self.request.run_id,
            plan_sha256=self.request.plan_sha256,
            sequence=self.request.phase.sequence,
            phase_request_sha256=self.request.phase_request_sha256,
            effect_key=self.request.effect_key,
            claim_id="phase-6-fi-rebuild-claim-000001",
        )
        p6_effect = driver.PhysicalFullMatrixV4EffectStart(
            run_id=p6_claim.run_id,
            plan_sha256=p6_claim.plan_sha256,
            sequence=p6_claim.sequence,
            phase_request_sha256=p6_claim.phase_request_sha256,
            effect_key=p6_claim.effect_key,
            claim_id=p6_claim.claim_id,
        )
        authority = driver._mint_effect_start_authority(
            effect_start=p6_effect, claim=p6_claim, request=self.request
        )
        anchor = driver._mint_physical_full_matrix_v4_effect_start_anchor_proof(
            request=self.request,
            effect_start=p6_effect,
            journal_binding_sha256=_hash("p6-journal-binding"),
            baseline_plan_binding_sha256=_hash("p6-baseline-binding"),
            anchor_genesis_sequence=0,
            anchor_genesis_head_sha256="0" * 64,
            anchor_previous_sequence=51,
            anchor_previous_head_sha256=self.phase5_completion_head,
            anchor_sequence=52,
            anchor_head_sha256=_hash("p6-start-anchor-head"),
            anchor_commitment_sha256=_hash("p6-start-anchor-commitment"),
            anchor_attestation_sha256=_hash("p6-start-anchor-attestation"),
            anchor_local_previous_record_sha256=_hash("p6-start-local-previous"),
            anchor_local_event_sha256=_hash("p6-start-local-event"),
            anchor_occurred_at=self.now,
        )
        p6_request = driver._adapter_request_with_effect_start_authority(
            request=self.request,
            authority=authority,
            anchor_proof=anchor,
        )
        completion = driver._mint_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
            request=p6_request,
            predecessor_effect_start=p5_effect,
            journal_binding_sha256=_hash("p6-journal-binding"),
            baseline_plan_binding_sha256=_hash("p6-baseline-binding"),
            anchor_genesis_sequence=0,
            anchor_genesis_head_sha256="0" * 64,
            predecessor_effect_start_anchor_previous_sequence=49,
            predecessor_effect_start_anchor_previous_head_sha256=_hash("p5-before-start"),
            predecessor_effect_start_anchor_sequence=50,
            predecessor_effect_start_anchor_head_sha256=_hash("p5-start-head"),
            predecessor_effect_start_anchor_commitment_sha256=_hash("p5-start-commitment"),
            predecessor_effect_start_anchor_attestation_sha256=_hash("p5-start-attestation"),
            predecessor_effect_start_anchor_local_previous_record_sha256=_hash("p5-start-local-previous"),
            predecessor_effect_start_anchor_local_event_sha256=_hash("p5-start-local-event"),
            predecessor_effect_started_at=self.now,
            predecessor_completion_receipt_sha256=receipt_sha256,
            predecessor_completion_anchor_previous_sequence=50,
            predecessor_completion_anchor_previous_head_sha256=_hash("p5-start-head"),
            predecessor_completion_anchor_sequence=51,
            predecessor_completion_anchor_head_sha256=self.phase5_completion_head,
            predecessor_completion_anchor_commitment_sha256=self.phase5_completion_commitment,
            predecessor_completion_anchor_attestation_sha256=self.phase5_completion_attestation,
            predecessor_completion_anchor_local_previous_record_sha256=_hash("p5-completion-local-previous"),
            predecessor_completion_anchor_local_event_sha256=_hash("p5-completion-local-event"),
            predecessor_completed_at=self.now,
        )
        request = driver._adapter_request_with_effect_start_authority(
            request=self.request,
            authority=authority,
            anchor_proof=anchor,
            predecessor_phase_completion_anchor_proof=completion,
        )
        plan_payload = dict(self.plan_payload)
        plan_payload["phase5_completion_receipt_sha256"] = receipt_sha256
        plan = self.plan_with(plan_payload)
        socket_payload = dict(self.socket_payload)
        socket_payload["reverse_recovery_plan_sha256"] = plan.plan_sha256
        socket_payload["phase5_completion_receipt_sha256"] = receipt_sha256
        socket = self.socket_with(socket_payload)
        config = replace(
            self.config,
            expected_reverse_recovery_plan_sha256=plan.plan_sha256,
            expected_socket_only_input_sha256=socket.input_sha256,
        )
        return subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionInputs(
            adapter_request=request,
            phase5_completion_anchor_proof=completion,
            phase5_completion_receipt=receipt,
            reverse_recovery_plan=plan,
            rendered_socket_only_inputs=socket,
        ), config


class PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionTests(unittest.TestCase):
    def test_default_off_and_public_pre_effect_request_fail_closed(self) -> None:
        fixture = _Fixture()
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError, "DISABLED"
        ):
            subject.admit_physical_full_matrix_v4_phase6_failback_rebuild(
                config=replace(fixture.config, enabled=False),
                inputs=subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionInputs(),
                now=fixture.now,
            )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError,
            "EFFECT_START_REQUIRED",
        ):
            subject.admit_physical_full_matrix_v4_phase6_failback_rebuild(
                config=fixture.config,
                inputs=subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionInputs(
                    adapter_request=fixture.request,
                    reverse_recovery_plan=fixture.plan,
                    rendered_socket_only_inputs=fixture.socket,
                ),
                now=fixture.now,
            )

    def test_reverse_plan_wire_is_canonical_reverse_only_and_term_complete(self) -> None:
        fixture = _Fixture()
        parsed = subject._canonical_mapping(
            fixture.plan.canonical_plan,
            fields=subject._PLAN_FIELDS,
            code="TEST",
        )
        self.assertEqual("webapp_ir", parsed["source_site"])
        self.assertEqual("webapp_fi", parsed["destination_site"])
        self.assertEqual(fixture.binding.writer_epoch, parsed["writer_term"]["writer_epoch"])
        self.assertEqual(fixture.phase5_receipt_sha, parsed["phase5_completion_receipt_sha256"])

        wrong_direction = copy.deepcopy(fixture.plan_payload)
        wrong_direction["source_site"] = "webapp_fi"
        wrong_plan = fixture.plan_with(wrong_direction)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError,
            "REVERSE_PLAN_INVALID",
        ):
            subject._plan(
                wrong_plan,
                expected=replace(
                    subject._config(fixture.config),
                    reverse_plan_sha256=wrong_plan.plan_sha256,
                ),
                # Direction is rejected before this intentionally incomplete
                # completion seam can be observed.
                completion=subject._CompletionFacts(
                    proof=SimpleNamespace(), receipt=None
                ),
            )

        noncanonical = fixture.plan.canonical_plan.replace(b'"webapp_ir"', b'"webapp_ir" ', 1)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError, "TEST"
        ):
            subject._canonical_mapping(noncanonical, fields=subject._PLAN_FIELDS, code="TEST")

    def test_socket_only_wire_cross_pins_the_exact_plan_and_forbids_authority(self) -> None:
        fixture = _Fixture()
        expected = subject._config(fixture.config)
        plan = subject._PlanFacts(
            plan_id=fixture.plan_payload["plan_id"],
            plan_sha256=fixture.plan.plan_sha256,
            campaign_id=fixture.binding.campaign_id,
            release_sha=fixture.binding.release_sha,
            route_binding_sha256=fixture.binding.route_commitment_sha256,
            four_role_binding_sha256=fixture.binding.four_role_binding_sha256,
            bundle_id=fixture.plan_payload["bundle_id"],
            stage_receipt_sha256=fixture.plan_payload["stage_receipt_sha256"],
            writer_epoch=fixture.binding.writer_epoch,
            writer_lease_id=fixture.binding.writer_lease_id,
            witness_transition_id=fixture.binding.witness_transition_id,
            witnessed_term_proof_sha256=fixture.binding.witnessed_term_proof_sha256,
            phase5_completion_receipt_sha256=fixture.phase5_receipt_sha,
            phase5_completion_anchor_sequence=51,
            phase5_completion_anchor_head_sha256=fixture.phase5_completion_head,
        )
        facts = subject._socket(fixture.socket, expected=expected, plan=plan)
        self.assertEqual(fixture.socket.input_sha256, facts.input_sha256)

        for forbidden_flag in (
            "promotion_authorized",
            "execution_authorized",
            "full_matrix_executed",
        ):
            with self.subTest(forbidden_flag=forbidden_flag):
                changed_payload = copy.deepcopy(fixture.socket_payload)
                changed_payload[forbidden_flag] = True
                changed = fixture.socket_with(changed_payload)
                with self.assertRaisesRegex(
                    subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError,
                    "SOCKET_INPUT_INVALID",
                ):
                    subject._socket(
                        changed,
                        expected=replace(
                            expected, socket_input_sha256=changed.input_sha256
                        ),
                        plan=plan,
                    )

        stale_plan = copy.deepcopy(fixture.socket_payload)
        stale_plan["reverse_recovery_plan_sha256"] = _hash("stale-plan")
        changed = fixture.socket_with(stale_plan)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError,
            "SOCKET_INPUT_INVALID",
        ):
            subject._socket(
                changed,
                expected=replace(expected, socket_input_sha256=changed.input_sha256),
                plan=plan,
            )

    def test_only_the_driver_attached_p5_completion_proof_is_acceptable(self) -> None:
        fixture = _Fixture()
        request = subject._RequestFacts(
            request=fixture.request,
            authority=SimpleNamespace(),
            anchor=SimpleNamespace(),
        )
        expected = subject._config(fixture.config)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError,
            "P5_COMPLETION_PROOF_REQUIRED",
        ):
            subject._completion(None, request=request, expected=expected)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError,
            "P5_COMPLETION_PROOF_UNAVAILABLE",
        ):
            subject._completion(object(), request=request, expected=expected)

    def test_config_requires_exact_ir_writer_term_and_explicit_policy_hashes(self) -> None:
        fixture = _Fixture()
        wrong_direction = replace(
            fixture.binding,
            writer_holder_site="webapp_fi",
            source_site="webapp_fi",
            destination_site="webapp_ir",
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError, "CONFIG_INVALID"
        ):
            subject._config(
                replace(fixture.config, expected_phase5_ir_writer_binding=wrong_direction)
            )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError, "CONFIG_INVALID"
        ):
            subject._config(replace(fixture.config, expected_reverse_recovery_plan_sha256=""))

    def test_exact_p5_completion_to_p6_start_cross_pin_is_evidence_only(self) -> None:
        fixture = _Fixture()
        inputs, config = fixture.inputs_with_exact_p5_completion()
        result = subject.admit_physical_full_matrix_v4_phase6_failback_rebuild(
            config=config,
            inputs=inputs,
            now=fixture.now,
        )
        self.assertEqual(
            subject.PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_STATUS,
            result.status,
        )
        self.assertEqual(51, result.phase5_completion_anchor_sequence)
        self.assertEqual(52, result.phase6_anchor_sequence)
        self.assertEqual("webapp_ir", fixture.binding.writer_holder_site)
        self.assertFalse(result.legacy_runtime_compatible)
        self.assertFalse(result.fd_binder_authorized)
        self.assertFalse(result.runner_authorized)
        self.assertFalse(result.materialization_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.traffic_switch_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertFalse(result.full_matrix_authorized)
        self.assertFalse(result.full_matrix_executed)
        self.assertIs(
            result,
            subject.require_admitted_physical_full_matrix_v4_phase6_failback_rebuild(result),
        )

    def test_driver_journal_bridge_rejects_evidence_only_p6_as_completion(self) -> None:
        """P6 admission evidence cannot self-attest an external completion."""

        from tests import test_physical_full_matrix_execution_driver_v4 as semantic

        initial_binding = semantic._binding()
        execution_config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=initial_binding,
            readiness=semantic._opaque_readiness(initial_binding),
            run_id=UUID("f80b3315-9218-498b-9a46-68d97d5c4f99"),
            enabled=True,
        )
        with patch.object(
            driver,
            "require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
            side_effect=lambda item, *, now=None: item.report,
        ):
            plan = driver.build_physical_full_matrix_v4_execution_plan(
                config=execution_config
            )
            journal = semantic._Journal()
            verifiers = {
                phase.name: semantic._PostEffectVerifier(phase)
                for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
            }
            mapped = {
                phase.name: semantic._Adapter(
                    semantic.NOW,
                    verifier=verifiers[phase.name],
                )
                for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
            }
            adapters = driver.PhysicalFullMatrixV4ExecutionAdapters(
                phase_adapters=mapped,
                receipt_journal=journal,
                readiness_resolver=semantic._Resolver(),
                trusted_clock=semantic._Clock(semantic.NOW),
                campaign_continuity_gate=semantic._CampaignContinuityGate(),
                phase_post_effect_verifiers=verifiers,
            )
            for _ in range(5):
                driver.execute_next_physical_full_matrix_v4_phase(
                    config=execution_config,
                    plan=plan,
                    adapters=adapters,
                    now=semantic.NOW,
                )

            phase5_receipt = driver.parse_physical_full_matrix_v4_run_receipt(
                journal.receipts[4]
            )
            phase5_key = next(
                key
                for key, start in journal.effect_starts.items()
                if start.sequence == 5
            )
            admission_config, reverse_plan, socket_inputs = _live_phase6_evidence(
                binding=phase5_receipt.binding,
                phase5_completion_receipt_sha256=phase5_receipt.receipt_sha256,
                phase5_completion_anchor=journal._completion_anchors[phase5_key],
            )
            p6_adapter = _InMemoryPhase6AdmissionAdapter(
                config=admission_config,
                phase5_completion_receipt=journal.receipts[4],
                reverse_recovery_plan=reverse_plan,
                socket_only_inputs=socket_inputs,
                now=semantic.NOW,
            )
            mapped[driver.PHYSICAL_FULL_MATRIX_V4_PHASES[5].name] = p6_adapter
            with self.assertRaisesRegex(
                driver.PhysicalFullMatrixV4ExecutionDriverError,
                "PHASE_POST_EFFECT_COMPLETION_REQUIRED",
            ):
                driver.execute_next_physical_full_matrix_v4_phase(
                    config=execution_config,
                    plan=plan,
                    adapters=adapters,
                    now=semantic.NOW,
                )

        self.assertIsNotNone(p6_adapter.request)
        self.assertIsNotNone(p6_adapter.admission)
        admission = subject.require_admitted_physical_full_matrix_v4_phase6_failback_rebuild(
            p6_adapter.admission
        )
        self.assertEqual(phase5_receipt.receipt_sha256, admission.phase5_completion_receipt_sha256)
        self.assertEqual(
            journal._completion_anchors[phase5_key]["sequence"],
            admission.phase5_completion_anchor_sequence,
        )
        self.assertFalse(admission.runner_authorized)
        self.assertFalse(admission.materialization_authorized)
        self.assertFalse(admission.promotion_authorized)
        self.assertFalse(admission.writer_authorized)
        self.assertFalse(admission.execution_authorized)
        self.assertFalse(admission.full_matrix_authorized)
        self.assertFalse(admission.full_matrix_executed)
        # Admission remains useful evidence, but has no phase-owned
        # post-effect capability.  The generic driver therefore retains the
        # durable P6 start as indeterminate and writes no P6 receipt.
        self.assertEqual(5, len(journal.receipts))
        self.assertTrue(
            any(start.sequence == 6 for start in journal.effect_starts.values())
        )

    def test_module_has_no_legacy_path_fd_provider_or_runtime_import(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "physical_wa_fi_postgres_failback",
            "physical_ir_to_fi_object_storage_failback_preflight",
            "physical_operational_failover_v1",
            "physical_postgres_promotion_coordinator",
        ):
            self.assertNotIn(forbidden, source)
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        self.assertTrue(
            {"os", "pathlib", "subprocess", "socket", "docker", "paramiko"}.isdisjoint(
                imports
            )
        )
        self.assertNotIn("Protocol", source)
