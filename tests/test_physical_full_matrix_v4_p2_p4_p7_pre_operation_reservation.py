"""Adversarial tests for the isolated V4 P2/P4/P7 reservation foundation."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import UUID

from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v4_p2_p4_p7_pre_operation_reservation as subject


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
RUN_ID = UUID("80efba86-6f9f-4ed5-9cee-6b1c5e9a439b")
PLAN_SHA256 = hashlib.sha256(b"p2-p4-p7-reservation-plan").hexdigest()
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_p2_p4_p7_pre_operation_reservation.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def now_utc(self) -> datetime:
        return self.now


class _Checkpoint:
    """Strict monotonic stand-in for the separate root/Witness seam."""

    def __init__(self) -> None:
        self.state: tuple[int, str, str] | None = None
        self.calls: list[tuple[str, int, str, str]] = []

    def attest_v4_p2_p4_p7_pre_operation_reservation_state(
        self,
        *,
        witness_reservation_scope_sha256: str,
        sequence: int,
        previous_record_sha256: str,
        record_sha256: str,
    ) -> None:
        current = (sequence, previous_record_sha256, record_sha256)
        self.calls.append((witness_reservation_scope_sha256, *current))
        if self.state is None:
            if current != (0, "0" * 64, "0" * 64):
                raise RuntimeError("checkpoint must begin at empty root")
            self.state = current
            return
        if current == self.state:
            return
        if (
            sequence == self.state[0] + 1
            and previous_record_sha256 == self.state[2]
            and record_sha256 != self.state[2]
        ):
            self.state = current
            return
        raise RuntimeError("rollback or branch")


class _PreEffectLinearizer:
    """Test double for the future atomic journal/driver bridge.

    The production bridge does not exist yet.  This fake makes the required
    contract observable: it rejects a claim that has already reached the
    journal effect-start boundary.
    """

    def __init__(self) -> None:
        self.started_claim_ids: set[str] = set()
        self.linearized_claim_ids: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def mark_effect_started(self, claim: driver.PhysicalFullMatrixV4PhaseClaim) -> None:
        assert claim.claim_id is not None
        self.started_claim_ids.add(claim.claim_id)

    def linearize_v4_p2_p4_p7_before_effect_start(
        self,
        *,
        witness_reservation_scope_sha256: str,
        run_id: UUID,
        plan_sha256: str,
        phase_sequence: int,
        phase: str,
        phase_request_sha256: str,
        effect_key: str,
        claim_id: str,
        reservation_identity_sha256: str,
    ) -> str:
        del (
            witness_reservation_scope_sha256,
            run_id,
            plan_sha256,
            phase_sequence,
            phase,
            phase_request_sha256,
            effect_key,
        )
        if claim_id in self.started_claim_ids or claim_id in self.linearized_claim_ids:
            raise RuntimeError("claim is already effect-started or linearized")
        self.calls.append((claim_id, reservation_identity_sha256))
        self.linearized_claim_ids.add(claim_id)
        return _hash("atomic-linearization:" + claim_id + ":" + reservation_identity_sha256)


class _Poison:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unexpected access: {name}")


def _binding(
    *,
    direction: tuple[str, str],
    epoch: int,
    suffix: str,
) -> driver.PhysicalFullMatrixV4ExecutionBinding:
    source, destination = direction
    return driver.PhysicalFullMatrixV4ExecutionBinding(
        campaign_id="physical-full-matrix-v4-reservation-20260801",
        release_sha="4" * 40,
        readiness_binding_sha256=_hash(f"readiness-{suffix}"),
        route_commitment_sha256=_hash(f"route-{suffix}"),
        four_role_binding_sha256=_hash("four-role"),
        writer_holder_site=source,
        writer_epoch=epoch,
        writer_lease_id=f"writer-lease-v4-reservation-{suffix}-000001",
        witnessed_term_proof_sha256=_hash(f"term-{suffix}"),
        source_site=source,
        destination_site=destination,
        roundtrip_attestation_sha256=_hash(f"roundtrip-{suffix}"),
        roundtrip_configuration_sha256=_hash("roundtrip-config"),
        witness_transition_id=f"witness-transition-v4-reservation-{suffix}-000001",
        witness_sequence=epoch + 10,
    )


def _request(
    *,
    sequence: int,
    binding: driver.PhysicalFullMatrixV4ExecutionBinding,
) -> driver.PhysicalFullMatrixV4ExecutionRequest:
    snapshot = driver._PlanSnapshot(
        canonical_plan=b"",
        plan_sha256=PLAN_SHA256,
        run_id=RUN_ID,
        binding=driver._snapshot_binding(
            binding,
            direction=(binding.source_site, binding.destination_site),
        ),
        phases=driver._phase_snapshots(),
        maximum_oracle_age_seconds=60,
    )
    return driver._request(
        snapshot=snapshot,
        phase=driver._phase_snapshots()[sequence - 1],
        binding=snapshot.binding,
        pre_effect_readiness_evidence=driver.PhysicalFullMatrixV4ReadinessEvidence(
            binding=binding,
            # The V4 verifier itself is patched only in this isolated
            # foundation suite.  Runtime integration must provide genuine
            # owner-minted Gen2 provenance instead.
            readiness=object(),
        ),
    )


def _claim(
    request: driver.PhysicalFullMatrixV4ExecutionRequest,
    *,
    suffix: str,
) -> driver.PhysicalFullMatrixV4PhaseClaim:
    return driver.PhysicalFullMatrixV4PhaseClaim(
        run_id=request.run_id,
        plan_sha256=request.plan_sha256,
        sequence=request.phase.sequence,
        phase_request_sha256=request.phase_request_sha256,
        effect_key=request.effect_key,
        claim_id=f"claim-v4-reservation-{suffix}-000001",
    )


def _post_start_request(
    *,
    pre_start: driver.PhysicalFullMatrixV4ExecutionRequest,
    claim: driver.PhysicalFullMatrixV4PhaseClaim,
    anchor_label: str,
) -> driver.PhysicalFullMatrixV4ExecutionRequest:
    assert claim.claim_id is not None
    effect_start = driver.PhysicalFullMatrixV4EffectStart(
        run_id=pre_start.run_id,
        plan_sha256=pre_start.plan_sha256,
        sequence=pre_start.phase.sequence,
        phase_request_sha256=pre_start.phase_request_sha256,
        effect_key=pre_start.effect_key,
        claim_id=claim.claim_id,
    )
    authority = driver._mint_effect_start_authority(
        effect_start=effect_start,
        claim=claim,
        request=pre_start,
    )
    anchor = driver._mint_physical_full_matrix_v4_effect_start_anchor_proof(
        request=pre_start,
        effect_start=effect_start,
        journal_binding_sha256=_hash("journal-binding"),
        baseline_plan_binding_sha256=_hash("baseline-binding"),
        anchor_genesis_sequence=0,
        anchor_genesis_head_sha256="0" * 64,
        anchor_previous_sequence=0,
        anchor_previous_head_sha256="0" * 64,
        anchor_sequence=1,
        anchor_head_sha256=_hash(f"anchor-head-{anchor_label}"),
        anchor_commitment_sha256=_hash(f"anchor-commitment-{anchor_label}"),
        anchor_attestation_sha256=_hash(f"anchor-attestation-{anchor_label}"),
        anchor_local_previous_record_sha256="0" * 64,
        anchor_local_event_sha256=_hash(f"anchor-event-{anchor_label}"),
        anchor_occurred_at=NOW,
    )
    return driver._adapter_request_with_effect_start_authority(
        request=pre_start,
        authority=authority,
        anchor_proof=anchor,
    )


def _successor_binding(
    request: driver.PhysicalFullMatrixV4ExecutionRequest,
) -> driver.PhysicalFullMatrixV4ExecutionBinding | None:
    if request.phase.name == "fence-fi-writer-v2":
        return None
    direction = (
        ("webapp_ir", "webapp_fi")
        if request.phase.name == "witness-promote-ir-v2"
        else ("webapp_fi", "webapp_ir")
    )
    return _binding(
        direction=direction,
        epoch=request.binding.writer_epoch + 1,
        suffix=f"successor{request.phase.sequence}",
    )


def _intent(
    request: driver.PhysicalFullMatrixV4ExecutionRequest,
) -> subject.PhysicalFullMatrixV4P2P4P7SuccessorIntent:
    return subject.build_physical_full_matrix_v4_p2_p4_p7_successor_intent(
        operation_phase_sequence=request.phase.sequence,
        operation_phase=request.phase.name,
        successor_binding=_successor_binding(request),
    )


@unittest.skipUnless(os.geteuid() == 0, "root-owned reservation tests require root")
class PhysicalFullMatrixV4P2P4P7PreOperationReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="v4-p2-p4-p7-reservation-",
            dir=Path(__file__).resolve().parents[1],
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "state"
        self.root.mkdir(mode=0o700)
        self.saved_root = (
            subject.FIXED_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_ROOT
        )
        subject.FIXED_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_ROOT = (
            self.root
        )
        self.addCleanup(self._restore_root)
        self.clock = _Clock()
        self.checkpoint = _Checkpoint()
        self.linearizer = _PreEffectLinearizer()
        self.readiness_validation = mock.patch.object(
            driver,
            "_validate_readiness_evidence",
            return_value=None,
        )
        self.readiness_validation.start()
        self.addCleanup(self.readiness_validation.stop)
        self.config = subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationConfig(
            enabled=True,
            witness_reservation_scope_sha256=_hash("witness-reservation-scope"),
            maximum_lifetime_seconds=30,
        )
        self.registry = subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry(
            self.config,
            clock=self.clock,
            rollback_checkpoint=self.checkpoint,
            pre_effect_linearizer=self.linearizer,
        )

    def _restore_root(self) -> None:
        subject.FIXED_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_ROOT = (
            self.saved_root
        )

    def _fixture(
        self, sequence: int = 2, *, term_suffix: str | None = None
    ) -> tuple[
        driver.PhysicalFullMatrixV4ExecutionRequest,
        driver.PhysicalFullMatrixV4PhaseClaim,
        driver.PhysicalFullMatrixV4ExecutionRequest,
        subject.PhysicalFullMatrixV4P2P4P7SuccessorIntent,
    ]:
        direction = (
            ("webapp_ir", "webapp_fi") if sequence == 7 else ("webapp_fi", "webapp_ir")
        )
        suffix = term_suffix or f"predecessor{sequence}"
        pre = _request(
            sequence=sequence,
            binding=_binding(direction=direction, epoch=7, suffix=suffix),
        )
        claim = _claim(pre, suffix=suffix)
        return pre, claim, _post_start_request(
            pre_start=pre,
            claim=claim,
            anchor_label=suffix,
        ), _intent(pre)

    def _reserve(
        self,
        *,
        registry: subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry
        | None = None,
        sequence: int = 2,
        expires_at: datetime | None = None,
    ):
        pre, claim, post, intent = self._fixture(sequence)
        service = self.registry if registry is None else registry
        with mock.patch.object(subject.os, "geteuid", return_value=0):
            receipt = service.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=intent,
                expires_at=expires_at or self.clock.now + timedelta(seconds=20),
            )
        return pre, claim, post, intent, receipt

    def _activate(self, *, receipt, post, registry=None):
        service = self.registry if registry is None else registry
        with mock.patch.object(subject.os, "geteuid", return_value=0):
            return service.activate_after_effect_start(reservation=receipt, request=post)

    def test_default_off_and_root_gate_fail_before_clock_or_pins(self) -> None:
        disabled = subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationConfig(
                enabled=False
            ),
            clock=_Poison(),
            rollback_checkpoint=_Poison(),
            pre_effect_linearizer=_Poison(),
        )
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "DISABLED",
        ):
            disabled.reserve_before_future_executor(
                claim=None, request=None, successor_intent=None, expires_at=NOW
            )
        with mock.patch.object(subject.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "ROOT_REQUIRED",
        ):
            self.registry.reserve_before_future_executor(
                claim=None, request=None, successor_intent=None, expires_at=NOW
            )

    def test_reservation_requires_a_real_pre_effect_linearizer(self) -> None:
        """There is intentionally no local timestamp/receipt fallback."""

        registry = subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry(
            self.config,
            clock=self.clock,
            rollback_checkpoint=self.checkpoint,
            pre_effect_linearizer=None,
        )
        pre, claim, _post, intent = self._fixture(4)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "PRE_EFFECT_LINEARIZER_MISSING",
        ):
            registry.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=intent,
                expires_at=NOW + timedelta(seconds=20),
            )
        self.assertEqual([], list((self.root / "records").glob("*.json")))

    def test_required_linearizer_contract_rejects_already_started_claim(self) -> None:
        """A fake documents the future bridge contract; it is not integration."""

        pre, claim, _post, intent = self._fixture()
        self.linearizer.mark_effect_started(claim)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "PRE_EFFECT_LINEARIZATION_FAILED",
        ):
            self.registry.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=intent,
                expires_at=NOW + timedelta(seconds=20),
            )
        self.assertEqual([], self.linearizer.calls)
        self.assertEqual([], list((self.root / "records").glob("*.json")))

    def test_pre_effect_readiness_is_required_and_revalidated_for_exact_binding(self) -> None:
        pre, claim, _post, intent = self._fixture()
        no_readiness = replace(pre, pre_effect_readiness_evidence=None)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "PINS_INVALID",
        ):
            self.registry.reserve_before_future_executor(
                claim=claim,
                request=no_readiness,
                successor_intent=intent,
                expires_at=NOW + timedelta(seconds=20),
            )
        mismatched_evidence = driver.PhysicalFullMatrixV4ReadinessEvidence(
            binding=_binding(
                direction=("webapp_fi", "webapp_ir"),
                epoch=8,
                suffix="wrong-pre-effect-readiness",
            ),
            readiness=object(),
        )
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "PINS_INVALID",
        ):
            self.registry.reserve_before_future_executor(
                claim=claim,
                request=replace(pre, pre_effect_readiness_evidence=mismatched_evidence),
                successor_intent=intent,
                expires_at=NOW + timedelta(seconds=20),
            )
        with mock.patch.object(
            driver,
            "_validate_readiness_evidence",
            side_effect=driver.PhysicalFullMatrixV4ExecutionDriverError(
                "INJECTED_READINESS_REVALIDATION_FAILURE"
            ),
        ), mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "PINS_INVALID",
        ):
            self.registry.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=intent,
                expires_at=NOW + timedelta(seconds=20),
            )

    def test_closed_p2_p4_p7_map_reserves_before_start_and_activates_once(self) -> None:
        for sequence in (2, 4, 7):
            with self.subTest(sequence=sequence), tempfile.TemporaryDirectory(
                prefix="v4-p2-p4-p7-map-",
                dir=Path(__file__).resolve().parents[1],
            ) as temporary:
                root = Path(temporary) / "state"
                root.mkdir(mode=0o700)
                checkpoint = _Checkpoint()
                registry = subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry(
                    self.config,
                    clock=self.clock,
                    rollback_checkpoint=checkpoint,
                    pre_effect_linearizer=_PreEffectLinearizer(),
                )
                with mock.patch.object(
                    subject,
                    "FIXED_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_ROOT",
                    root,
                ):
                    pre, claim, post, intent = self._fixture(sequence)
                    with mock.patch.object(subject.os, "geteuid", return_value=0):
                        receipt = registry.reserve_before_future_executor(
                            claim=claim,
                            request=pre,
                            successor_intent=intent,
                            expires_at=NOW + timedelta(seconds=20),
                        )
                    self.assertEqual(
                        "reserved-awaiting-effect-start-activation-not-authorized",
                        receipt.status,
                    )
                    for flag in (
                        "writer_authorized",
                        "promotion_authorized",
                        "traffic_switch_authorized",
                        "external_effect_authorized",
                        "execution_authorized",
                        "full_matrix_authorized",
                    ):
                        self.assertFalse(getattr(receipt, flag), flag)
                    with mock.patch.object(subject.os, "geteuid", return_value=0):
                        capability = registry.activate_after_effect_start(
                            reservation=receipt,
                            request=post,
                        )
                        self.assertIs(
                            capability,
                            subject.require_physical_full_matrix_v4_p2_p4_p7_pre_operation_reservation(
                                registry=registry,
                                capability=capability,
                                request=post,
                            ),
                        )
                    self.assertEqual(sequence, capability.phase_sequence)
                    self.assertFalse(capability.execution_authorized)
                    self.assertEqual(4, len(checkpoint.calls))

    def test_fixed_shared_root_is_not_a_p2_to_p4_to_p7_pipeline(self) -> None:
        """A later phase needs a separately designed durable reconciliation.

        The map accepts each phase in isolation, but the sole live RESERVED
        record is intentionally scope-wide and blocks a later phase rather
        than silently treating the earlier one as complete.
        """

        _p2_pre, _p2_claim, _p2_post, _p2_intent, _receipt = self._reserve(
            sequence=2
        )
        p4_pre, p4_claim, _p4_post, p4_intent = self._fixture(4)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "OUTSTANDING_INDETERMINATE",
        ):
            self.registry.reserve_before_future_executor(
                claim=p4_claim,
                request=p4_pre,
                successor_intent=p4_intent,
                expires_at=NOW + timedelta(seconds=20),
            )

    def test_replay_and_second_activation_fail_closed(self) -> None:
        pre, claim, post, intent, receipt = self._reserve()
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "OUTSTANDING_INDETERMINATE",
        ):
            self.registry.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=intent,
                expires_at=NOW + timedelta(seconds=20),
            )
        self._activate(receipt=receipt, post=post)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "ALREADY_ACTIVATED",
        ):
            self.registry.activate_after_effect_start(reservation=receipt, request=post)

    def test_ambiguous_pre_effect_linearization_never_gets_a_local_retry(self) -> None:
        """A crash/error after the bridge call remains blocked in this process.

        After restart the required bridge must retain the same fail-closed
        knowledge; this local module must not impersonate it.
        """

        pre, claim, _post, intent = self._fixture(4)
        with mock.patch.object(subject.os, "geteuid", return_value=0), mock.patch.object(
            subject,
            "_record_payload",
            side_effect=subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError(
                "INJECTED_POST_LINEARIZATION_CRASH"
            ),
        ), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "INJECTED_POST_LINEARIZATION_CRASH",
        ):
            self.registry.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=intent,
                expires_at=NOW + timedelta(seconds=20),
            )
        self.assertEqual(1, len(self.linearizer.calls))
        self.assertEqual([], list((self.root / "records").glob("*.json")))
        alternative_successor = _successor_binding(pre)
        assert alternative_successor is not None
        alternative_intent = (
            subject.build_physical_full_matrix_v4_p2_p4_p7_successor_intent(
                operation_phase_sequence=pre.phase.sequence,
                operation_phase=pre.phase.name,
                successor_binding=replace(
                    alternative_successor,
                    readiness_binding_sha256=_hash("alternative-successor-readiness"),
                ),
            )
        )
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "PRE_EFFECT_LINEARIZATION_INDETERMINATE",
        ):
            self.registry.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=alternative_intent,
                expires_at=NOW + timedelta(seconds=20),
            )
        self.assertEqual(1, len(self.linearizer.calls))

        restarted = subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry(
            self.config,
            clock=self.clock,
            rollback_checkpoint=self.checkpoint,
            pre_effect_linearizer=self.linearizer,
        )
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "PRE_EFFECT_LINEARIZATION_FAILED",
        ):
            restarted.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=intent,
                expires_at=NOW + timedelta(seconds=20),
            )

    def test_capability_is_consumed_before_any_future_executor_boundary(self) -> None:
        _pre, _claim_value, post, _intent_value, receipt = self._reserve()
        capability = self._activate(receipt=receipt, post=post)
        with mock.patch.object(subject.os, "geteuid", return_value=0):
            self.registry.require_live_capability(capability=capability, request=post)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "ALREADY_CONSUMED",
        ):
            self.registry.require_live_capability(capability=capability, request=post)

    def test_cross_phase_and_cross_term_substitution_fail_before_activation(self) -> None:
        pre, claim, post, intent, receipt = self._reserve()
        p4_pre, p4_claim, _p4_post, p4_intent = self._fixture(4)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "ACTIVATION_PINS_INVALID",
        ):
            self.registry.activate_after_effect_start(reservation=receipt, request=_p4_post)
        changed_pre, changed_claim, changed_post, _changed_intent = self._fixture(
            2, term_suffix="different-term"
        )
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "ACTIVATION_PINS_INVALID",
        ):
            self.registry.activate_after_effect_start(
                reservation=receipt,
                request=changed_post,
            )
        self.assertEqual(p4_claim.sequence, 4)
        self.assertNotEqual(changed_pre.binding.writer_lease_id, pre.binding.writer_lease_id)
        self.assertIsNotNone(intent)
        self.assertIsNotNone(claim)
        self.assertIsNotNone(post)

    def test_p4_successor_requires_changed_route_and_roundtrip_attestation(self) -> None:
        pre, claim, _post, _intent_value = self._fixture(4)
        successor = _successor_binding(pre)
        assert successor is not None
        for changed in (
            replace(
                successor,
                route_commitment_sha256=pre.binding.route_commitment_sha256,
            ),
            replace(
                successor,
                roundtrip_attestation_sha256=pre.binding.roundtrip_attestation_sha256,
            ),
        ):
            intent = subject.build_physical_full_matrix_v4_p2_p4_p7_successor_intent(
                operation_phase_sequence=pre.phase.sequence,
                operation_phase=pre.phase.name,
                successor_binding=changed,
            )
            with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
                subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
                "PINS_INVALID",
            ):
                self.registry.reserve_before_future_executor(
                    claim=claim,
                    request=pre,
                    successor_intent=intent,
                    expires_at=NOW + timedelta(seconds=20),
                )

    def test_wrong_or_missing_effect_start_anchor_cannot_activate(self) -> None:
        pre, _claim_value, _post, _intent_value, receipt = self._reserve()
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "ACTIVATION_PINS_INVALID",
        ):
            self.registry.activate_after_effect_start(reservation=receipt, request=pre)

    def test_restart_and_partial_write_are_indeterminate_not_reissued(self) -> None:
        pre, claim, _post, intent = self._fixture()
        with mock.patch.object(subject.os, "geteuid", return_value=0), mock.patch.object(
            subject,
            "_write_current_atomic",
            side_effect=subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError(
                "INJECTED_CURRENT_FAILURE"
            ),
        ), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "INJECTED_CURRENT_FAILURE",
        ):
            self.registry.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=intent,
                expires_at=NOW + timedelta(seconds=20),
            )
        restarted = subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry(
            self.config,
            clock=self.clock,
            rollback_checkpoint=self.checkpoint,
            pre_effect_linearizer=self.linearizer,
        )
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "INDETERMINATE",
        ):
            restarted.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=intent,
                expires_at=NOW + timedelta(seconds=20),
            )

    def test_restart_loses_live_receipt_and_blocks_new_capability(self) -> None:
        _pre, _claim_value, post, _intent_value, receipt = self._reserve()
        restarted = subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry(
            self.config,
            clock=self.clock,
            rollback_checkpoint=self.checkpoint,
            pre_effect_linearizer=self.linearizer,
        )
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "RECEIPT_INDETERMINATE",
        ):
            restarted.activate_after_effect_start(reservation=receipt, request=post)

    def test_expiry_never_reopens_or_auto_releases_reservation(self) -> None:
        pre, claim, post, intent, receipt = self._reserve(
            expires_at=NOW + timedelta(seconds=2)
        )
        self.clock.now = NOW + timedelta(seconds=3)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "EXPIRED_INDETERMINATE",
        ):
            self.registry.activate_after_effect_start(reservation=receipt, request=post)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "OUTSTANDING_INDETERMINATE",
        ):
            self.registry.reserve_before_future_executor(
                claim=claim,
                request=pre,
                successor_intent=intent,
                expires_at=self.clock.now + timedelta(seconds=10),
            )

    def test_clock_rollback_and_activation_time_of_check_use_fail_closed(self) -> None:
        _pre, _claim_value, post, _intent_value, receipt = self._reserve(
            expires_at=NOW + timedelta(seconds=2)
        )
        original_activation_pins = subject._activation_pins

        def expire_during_pin_validation(**kwargs):
            self.clock.now = NOW + timedelta(seconds=3)
            return original_activation_pins(**kwargs)

        # The first pre-activation read was at NOW; advancing only while
        # validating the anchor must be caught by the second, locked read
        # immediately before capability issuance.
        with mock.patch.object(
            subject,
            "_activation_pins",
            side_effect=expire_during_pin_validation,
        ), mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "EXPIRED_INDETERMINATE",
        ):
            self.registry.activate_after_effect_start(reservation=receipt, request=post)

        self.clock.now = NOW - timedelta(seconds=1)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "CLOCK_ROLLBACK",
        ):
            self.registry.activate_after_effect_start(reservation=receipt, request=post)

        # The future executor-boundary read has the same second-read rule.
        with tempfile.TemporaryDirectory(
            prefix="v4-p2-p4-p7-capability-clock-",
            dir=Path(__file__).resolve().parents[1],
        ) as temporary:
            root = Path(temporary) / "state"
            root.mkdir(mode=0o700)
            clock = _Clock()
            registry = subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry(
                self.config,
                clock=clock,
                rollback_checkpoint=_Checkpoint(),
                pre_effect_linearizer=_PreEffectLinearizer(),
            )
            with mock.patch.object(
                subject,
                "FIXED_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_ROOT",
                root,
            ), mock.patch.object(subject.os, "geteuid", return_value=0):
                pre, claim, post, intent = self._fixture()
                fresh_receipt = registry.reserve_before_future_executor(
                    claim=claim,
                    request=pre,
                    successor_intent=intent,
                    expires_at=NOW + timedelta(seconds=20),
                )
                capability = registry.activate_after_effect_start(
                    reservation=fresh_receipt,
                    request=post,
                )
                original_capability_pins = subject._activation_pins

                def expire_during_capability_validation(**kwargs):
                    clock.now = NOW + timedelta(seconds=21)
                    return original_capability_pins(**kwargs)

                with mock.patch.object(
                    subject,
                    "_activation_pins",
                    side_effect=expire_during_capability_validation,
                ), self.assertRaisesRegex(
                    subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
                    "EXPIRED_INDETERMINATE",
                ):
                    registry.require_live_capability(capability=capability, request=post)

    def test_capability_and_durable_record_mutation_fail_closed(self) -> None:
        _pre, _claim_value, post, _intent_value, receipt = self._reserve()
        capability = self._activate(receipt=receipt, post=post)
        object.__setattr__(capability, "execution_authorized", True)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "CAPABILITY_TAMPERED",
        ):
            self.registry.require_live_capability(capability=capability, request=post)

        # A fresh state gives a clean capability, then a byte-level durable
        # record mutation must fail before the capability can be required.
        with tempfile.TemporaryDirectory(
            prefix="v4-p2-p4-p7-record-mutation-",
            dir=Path(__file__).resolve().parents[1],
        ) as temporary:
            root = Path(temporary) / "state"
            root.mkdir(mode=0o700)
            checkpoint = _Checkpoint()
            registry = subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry(
                self.config,
                clock=self.clock,
                rollback_checkpoint=checkpoint,
                pre_effect_linearizer=_PreEffectLinearizer(),
            )
            with mock.patch.object(
                subject,
                "FIXED_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_ROOT",
                root,
            ):
                pre, claim, post, intent = self._fixture()
                with mock.patch.object(subject.os, "geteuid", return_value=0):
                    clean_receipt = registry.reserve_before_future_executor(
                        claim=claim,
                        request=pre,
                        successor_intent=intent,
                        expires_at=NOW + timedelta(seconds=20),
                    )
                    clean_capability = registry.activate_after_effect_start(
                        reservation=clean_receipt,
                        request=post,
                    )
                record = next((root / "records").glob("*.json"))
                record.write_bytes(record.read_bytes() + b" ")
                with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
                    subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
                    "RECORD_INVALID",
                ):
                    registry.require_live_capability(
                        capability=clean_capability,
                        request=post,
                    )

    def test_effect_start_anchor_mutation_cannot_validate_a_capability(self) -> None:
        _pre, _claim_value, post, _intent_value, receipt = self._reserve()
        capability = self._activate(receipt=receipt, post=post)
        anchor = post._effect_start_anchor_proof
        assert anchor is not None
        object.__setattr__(anchor, "anchor_head_sha256", _hash("mutated-anchor-head"))
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
            "CAPABILITY_PINS_MISMATCH",
        ):
            self.registry.require_live_capability(capability=capability, request=post)

    def test_module_has_no_direct_site_control_provider_or_network_access(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("physical_full_matrix_v4_retired_fi_predecessor_fence_runtime", source)
        self.assertNotIn("physical_full_matrix_v4_witness_successor_transition_runtime", source)
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
            {
                "subprocess",
                "socket",
                "requests",
                "paramiko",
                "urllib",
                "boto3",
                "docker",
            }.isdisjoint(imports)
        )
