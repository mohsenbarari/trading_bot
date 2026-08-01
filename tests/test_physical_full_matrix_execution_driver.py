"""Focused no-I/O tests for the physical Full-Matrix phase boundary."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import pickle
from uuid import UUID
import unittest
from unittest.mock import patch

import core.physical_full_matrix_execution_driver as driver
import core.physical_full_matrix_campaign_readiness as readiness_boundary
from core.physical_full_matrix_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
    PhysicalFullMatrixCampaignReadiness,
    PhysicalFullMatrixCampaignInputs,
    PhysicalFullMatrixCampaignReadinessConfig,
    VerifiedPhysicalFullMatrixCampaignReadiness,
    mint_verified_physical_full_matrix_campaign_readiness,
)


NOW = datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)


def binding() -> driver.PhysicalFullMatrixExecutionBinding:
    return driver.PhysicalFullMatrixExecutionBinding(
        campaign_id="physical-full-matrix-20260731",
        release_sha="a" * 40,
        readiness_binding_sha256="b" * 64,
        release_manifest_sha256="c" * 64,
        route_binding_sha256="d" * 64,
        writer_epoch=7,
        writer_lease_id="lease-20260731",
        witness_transition_id="transition-20260731",
        witnessed_term_proof_sha256="e" * 64,
    )


def readiness() -> PhysicalFullMatrixCampaignReadiness:
    item = binding()
    return PhysicalFullMatrixCampaignReadiness(
        schema=PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA,
        status=PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
        reason_codes=(),
        campaign_id=item.campaign_id,
        release_sha=item.release_sha,
        binding_sha256=item.readiness_binding_sha256,
        observed_slots=driver.PHYSICAL_FULL_MATRIX_EXECUTION_REQUIRED_READINESS_SLOTS,
    )


def _recompute_plan_after_route_mutation(
    plan: driver.PhysicalFullMatrixExecutionPlan,
) -> None:
    """Make a public-plan mutation self-consistent to exercise provenance."""

    canonical = driver._canonical(
        driver._plan_body(
            binding=plan.binding,
            run_id=plan.run_id,
            maximum_age=plan.maximum_oracle_age_seconds,
        ),
        code="PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_INVALID",
    ) + b"\n"
    object.__setattr__(plan, "canonical_plan", canonical)
    object.__setattr__(plan, "plan_sha256", hashlib.sha256(canonical).hexdigest())


def _mutate_plan_route_and_recompute(
    plan: driver.PhysicalFullMatrixExecutionPlan,
) -> None:
    object.__setattr__(plan.binding, "route_binding_sha256", "f" * 64)
    _recompute_plan_after_route_mutation(plan)


def _recompute_request_after_route_mutation(
    request: driver.PhysicalFullMatrixExecutionRequest,
) -> None:
    phase = request.phase
    body = {
        "schema": driver.PHYSICAL_FULL_MATRIX_EXECUTION_DRIVER_SCHEMA,
        "run_id": str(request.run_id),
        "plan_sha256": request.plan_sha256,
        "sequence": phase.sequence,
        "phase": phase.name,
        "oracle": phase.oracle,
        "transport_profile": phase.transport_profile,
        **driver._binding_body(request.binding),
        "direct_fi_to_ir_control": "forbidden",
        "direct_ir_to_fi_control": "forbidden",
    }
    object.__setattr__(
        request,
        "phase_request_sha256",
        hashlib.sha256(
            driver._canonical(
                body,
                code="PHYSICAL_FULL_MATRIX_EXECUTION_REQUEST_INVALID",
            )
        ).hexdigest(),
    )


class _Journal:
    def __init__(self) -> None:
        self.receipts: list[bytes] = []
        self.claims: set[tuple[UUID, str, int, str]] = set()

    def read_receipts(self, *, run_id: UUID):
        return tuple(self.receipts)

    def claim_phase(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        sequence: int,
        phase_request_sha256: str,
    ):
        for receipt in self.receipts:
            parsed = driver.parse_physical_full_matrix_run_receipt(receipt)
            if (
                parsed.run_id == run_id
                and parsed.plan_sha256 == plan_sha256
                and parsed.sequence == sequence
                and parsed.phase_request_sha256 == phase_request_sha256
            ):
                return driver.PhysicalFullMatrixPhaseClaim(
                    run_id=run_id,
                    plan_sha256=plan_sha256,
                    sequence=sequence,
                    phase_request_sha256=phase_request_sha256,
                    existing_receipt=receipt,
                )
        key = (run_id, plan_sha256, sequence, phase_request_sha256)
        if key in self.claims:
            return driver.PhysicalFullMatrixPhaseClaim(
                run_id=run_id,
                plan_sha256=plan_sha256,
                sequence=sequence,
                phase_request_sha256=phase_request_sha256,
            )
        self.claims.add(key)
        return driver.PhysicalFullMatrixPhaseClaim(
            run_id=run_id,
            plan_sha256=plan_sha256,
            sequence=sequence,
            phase_request_sha256=phase_request_sha256,
            claim_id=f"claim-{sequence}",
        )

    def append_claimed(self, *, claim, canonical_receipt: bytes):
        key = (
            claim.run_id,
            claim.plan_sha256,
            claim.sequence,
            claim.phase_request_sha256,
        )
        if claim.claim_id != f"claim-{claim.sequence}" or key not in self.claims:
            raise AssertionError("claim is not live")
        if canonical_receipt in self.receipts:
            raise AssertionError("receipt was already persisted")
        self.receipts.append(canonical_receipt)
        return canonical_receipt


class _RaceJournal(_Journal):
    """Makes the first chain read stale after a receipt was durably appended."""

    def __init__(self) -> None:
        super().__init__()
        self.hide_next_read = False

    def read_receipts(self, *, run_id: UUID):
        if self.hide_next_read:
            self.hide_next_read = False
            return ()
        return super().read_receipts(run_id=run_id)


class _Adapter:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.requests: list[driver.PhysicalFullMatrixExecutionRequest] = []
        self.emit_successor = True

    def execute_phase(self, *, request: driver.PhysicalFullMatrixExecutionRequest):
        self.requests.append(request)
        phase = request.phase
        item = request.binding
        successor = None
        if self.emit_successor and phase.name == "witness-promote-ir":
            successor = driver.PhysicalFullMatrixExecutionSuccessorBinding(
                source_site="webapp_ir",
                destination_site="webapp_fi",
                readiness_binding_sha256="f" * 64,
                route_binding_sha256="1" * 64,
                writer_epoch=item.writer_epoch + 1,
                writer_lease_id="lease-20260731-ir",
                witness_transition_id="transition-20260731-ir",
                witnessed_term_proof_sha256="2" * 64,
                transition_evidence_sha256="3" * 64,
            )
        elif self.emit_successor and phase.name == "witness-restore-fi-writer":
            successor = driver.PhysicalFullMatrixExecutionSuccessorBinding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                readiness_binding_sha256="4" * 64,
                route_binding_sha256="5" * 64,
                writer_epoch=item.writer_epoch + 1,
                writer_lease_id="lease-20260731-fi-restored",
                witness_transition_id="transition-20260731-fi-restored",
                witnessed_term_proof_sha256="6" * 64,
                transition_evidence_sha256="7" * 64,
            )
        return driver.PhysicalFullMatrixPhaseOracle(
            schema=driver.PHYSICAL_FULL_MATRIX_EXECUTION_DRIVER_SCHEMA,
            status="oracle-succeeded",
            phase=phase.name,
            oracle=phase.oracle,
            transport_profile=phase.transport_profile,
            campaign_id=item.campaign_id,
            release_sha=item.release_sha,
            release_manifest_sha256=item.release_manifest_sha256,
            route_binding_sha256=item.route_binding_sha256,
            writer_epoch=item.writer_epoch,
            writer_lease_id=item.writer_lease_id,
            witness_transition_id=item.witness_transition_id,
            witnessed_term_proof_sha256=item.witnessed_term_proof_sha256,
            evidence_sha256=(str(phase.sequence) * 64)[:64],
            observed_at=self.now,
            source_site=item.source_site,
            destination_site=item.destination_site,
            successor_binding=successor,
        )


class _PlanMutatingGetattrAdapter:
    """Mutates/re-signs the public plan while prepare probes a descriptor."""

    def __init__(self, plan: driver.PhysicalFullMatrixExecutionPlan) -> None:
        self.plan = plan
        self.execute_calls = 0
        self.getattr_calls = 0

    @property
    def execute_phase(self):
        self.getattr_calls += 1
        _mutate_plan_route_and_recompute(self.plan)

        def _unreachable(*, request: driver.PhysicalFullMatrixExecutionRequest):
            del request
            self.execute_calls += 1
            raise AssertionError("mutated plan must fail during prepare")

        return _unreachable


class _PlanMutatingJournal(_Journal):
    """Mutates/re-signs the public plan from one journal callback."""

    def __init__(
        self,
        plan: driver.PhysicalFullMatrixExecutionPlan,
        *,
        mutate_on: str,
    ) -> None:
        super().__init__()
        self.plan = plan
        self.mutate_on = mutate_on
        self.mutations = 0

    def _mutate(self) -> None:
        self.mutations += 1
        _mutate_plan_route_and_recompute(self.plan)

    def read_receipts(self, *, run_id: UUID):
        if self.mutate_on == "read":
            self._mutate()
        return super().read_receipts(run_id=run_id)

    def claim_phase(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        sequence: int,
        phase_request_sha256: str,
    ):
        if self.mutate_on == "claim":
            self._mutate()
        return super().claim_phase(
            run_id=run_id,
            plan_sha256=plan_sha256,
            sequence=sequence,
            phase_request_sha256=phase_request_sha256,
        )

    def append_claimed(self, *, claim, canonical_receipt: bytes):
        if self.mutate_on == "append":
            self._mutate()
        return super().append_claimed(claim=claim, canonical_receipt=canonical_receipt)


class _PlanMutatingExecutionAdapter(_Adapter):
    def __init__(
        self,
        now: datetime,
        plan: driver.PhysicalFullMatrixExecutionPlan,
    ) -> None:
        super().__init__(now)
        self.plan = plan

    def execute_phase(self, *, request: driver.PhysicalFullMatrixExecutionRequest):
        _mutate_plan_route_and_recompute(self.plan)
        return super().execute_phase(request=request)


class _RequestMutatingAdapter(_Adapter):
    """Attempts the old shared-request route/hash recomputation attack."""

    def execute_phase(self, *, request: driver.PhysicalFullMatrixExecutionRequest):
        object.__setattr__(request.binding, "route_binding_sha256", "f" * 64)
        object.__setattr__(request.phase, "oracle", "forged-route-oracle-v1")
        _recompute_request_after_route_mutation(request)
        return super().execute_phase(request=request)


class PhysicalFullMatrixExecutionDriverTests(unittest.TestCase):
    def setUp(self) -> None:
        # These are phase-driver mechanics tests.  They deliberately do not
        # duplicate the readiness boundary's full evidence fixture, so mock
        # only that verifier here.  Provenance rejection itself is covered by
        # the two tests below after this mock has been removed.
        self._readiness_verifier = patch.object(
            driver,
            "require_verified_physical_full_matrix_campaign_readiness",
            side_effect=lambda value, **_kwargs: value,
        )
        self._readiness_verifier.start()
        self.addCleanup(self._readiness_verifier.stop)

    def test_execution_slots_require_four_role_immutability_not_retired_two_role_slot(self) -> None:
        self.assertIn(
            "four-role-arvan-object-storage-immutability-preflight",
            driver.PHYSICAL_FULL_MATRIX_EXECUTION_REQUIRED_READINESS_SLOTS,
        )
        self.assertNotIn(
            "arvan-object-storage-immutability-preflight",
            driver.PHYSICAL_FULL_MATRIX_EXECUTION_REQUIRED_READINESS_SLOTS,
        )
        self.assertIn(
            "arvan-object-storage-failback-preflight",
            driver.PHYSICAL_FULL_MATRIX_EXECUTION_REQUIRED_READINESS_SLOTS,
        )

    def config(self, **changes: object) -> driver.PhysicalFullMatrixExecutionConfig:
        values: dict[str, object] = {
            "binding": binding(),
            "readiness": readiness(),
            "run_id": UUID("a3ab6521-c025-4d31-80f3-3739f7e5338d"),
            "enabled": True,
            "maximum_oracle_age_seconds": 120,
        }
        values.update(changes)
        return driver.PhysicalFullMatrixExecutionConfig(**values)

    def plan(self, **changes: object) -> driver.PhysicalFullMatrixExecutionPlan:
        with patch.object(driver.os, "geteuid", return_value=0):
            return driver.build_physical_full_matrix_execution_plan(
                config=self.config(**changes)
            )

    def adapters(self, now: datetime = NOW):
        journal = _Journal()
        adapter_map = {phase.name: _Adapter(now) for phase in driver.PHYSICAL_FULL_MATRIX_PHASES}
        return driver.PhysicalFullMatrixExecutionAdapters(
            phase_adapters=adapter_map, receipt_journal=journal
        ), journal, adapter_map

    def test_exact_phase_graph_and_plan_are_non_authorizing(self) -> None:
        plan = self.plan()
        self.assertEqual(8, len(plan.phases))
        self.assertEqual(
            (
                "normal-fi-writer-durable-ack-matrix",
                "fence-fi-writer",
                "recover-ir-through-object-storage",
                "witness-promote-ir",
                "ir-writer-durable-ack-matrix",
                "rebuild-fi-through-object-storage",
                "witness-restore-fi-writer",
            ),
            driver.PHYSICAL_FULL_MATRIX_DESTRUCTIVE_PHASES,
        )
        self.assertFalse(plan.materialization_authorized)
        self.assertFalse(plan.promotion_authorized)
        self.assertFalse(plan.execution_authorized)
        self.assertNotIn(b"ssh", plan.canonical_plan.lower())
        self.assertNotIn(b"scp", plan.canonical_plan.lower())
        self.assertIn(b'"direct_ir_to_fi_control":"forbidden"', plan.canonical_plan)
        self.assertIs(plan, driver.require_physical_full_matrix_execution_plan(plan))

    def test_plan_is_process_local_and_detached_from_config_and_public_catalog(self) -> None:
        config = self.config()
        with patch.object(driver.os, "geteuid", return_value=0):
            plan = driver.build_physical_full_matrix_execution_plan(config=config)
        self.assertIsNot(plan.binding, config.binding)
        self.assertIsNot(plan.phases[0], driver.PHYSICAL_FULL_MATRIX_PHASES[0])

        # Mutating a caller's config binding or the exported display catalog
        # cannot alter this plan's private build snapshot.
        object.__setattr__(config.binding, "route_binding_sha256", "f" * 64)
        exported_phase = driver.PHYSICAL_FULL_MATRIX_PHASES[0]
        original_oracle = exported_phase.oracle
        object.__setattr__(exported_phase, "oracle", "forged-public-catalog-oracle-v1")
        try:
            with patch.object(driver.os, "geteuid", return_value=0):
                second = driver.build_physical_full_matrix_execution_plan(
                    config=self.config()
                )
            self.assertEqual("d" * 64, plan.binding.route_binding_sha256)
            self.assertEqual(
                "normal-fi-writer-durable-ack-oracle-v1",
                second.phases[0].oracle,
            )
            self.assertIs(plan, driver.require_physical_full_matrix_execution_plan(plan))
            with self.assertRaisesRegex(TypeError, "COPY_FORBIDDEN"):
                copy.copy(plan)
            with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
                pickle.dumps(plan)
        finally:
            object.__setattr__(exported_phase, "oracle", original_oracle)

    def test_prepare_descriptor_mutation_of_rehashed_plan_refuses_before_journal_or_adapter(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        mutator = _PlanMutatingGetattrAdapter(plan)
        mapped[plan.phases[0].name] = mutator
        adversarial = driver.PhysicalFullMatrixExecutionAdapters(
            phase_adapters=mapped,
            receipt_journal=journal,
        )

        with patch.object(driver.os, "geteuid", return_value=0), self.assertRaisesRegex(
            driver.PhysicalFullMatrixExecutionDriverError,
            "PLAN_TAMPERED",
        ):
            driver.execute_next_physical_full_matrix_phase(
                config=self.config(),
                plan=plan,
                adapters=adversarial,
                now=NOW,
            )

        self.assertEqual(1, mutator.getattr_calls)
        self.assertEqual(0, mutator.execute_calls)
        self.assertEqual([], journal.receipts)
        self.assertTrue(
            all(not item.requests for item in mapped.values() if isinstance(item, _Adapter))
        )

    def test_journal_callback_plan_mutation_refuses_before_each_next_effect(self) -> None:
        for mutate_on, expected_adapter_calls, expected_receipts in (
            ("read", 0, 0),
            ("claim", 0, 0),
            # Append has already durably received one receipt, but the guard
            # rejects before it can be consumed by a subsequent journal read
            # or reported as completion.
            ("append", 1, 1),
        ):
            with self.subTest(mutate_on=mutate_on):
                plan = self.plan()
                journal = _PlanMutatingJournal(plan, mutate_on=mutate_on)
                mapped = {
                    phase.name: _Adapter(NOW)
                    for phase in driver.PHYSICAL_FULL_MATRIX_PHASES
                }
                adapters = driver.PhysicalFullMatrixExecutionAdapters(
                    phase_adapters=mapped,
                    receipt_journal=journal,
                )
                with patch.object(driver.os, "geteuid", return_value=0), self.assertRaisesRegex(
                    driver.PhysicalFullMatrixExecutionDriverError,
                    "PLAN_TAMPERED",
                ):
                    driver.execute_next_physical_full_matrix_phase(
                        config=self.config(),
                        plan=plan,
                        adapters=adapters,
                        now=NOW,
                    )
                self.assertEqual(1, journal.mutations)
                self.assertEqual(expected_receipts, len(journal.receipts))
                self.assertEqual(
                    expected_adapter_calls,
                    len(mapped[plan.phases[0].name].requests),
                )

    def test_adapter_plan_or_request_mutation_cannot_rebind_expected_receipt(self) -> None:
        # First cover an adapter changing the public plan after it has claimed
        # a phase: no append is allowed after the callback returns.
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        mapped[plan.phases[0].name] = _PlanMutatingExecutionAdapter(NOW, plan)
        with patch.object(driver.os, "geteuid", return_value=0), self.assertRaisesRegex(
            driver.PhysicalFullMatrixExecutionDriverError,
            "PLAN_TAMPERED",
        ):
            driver.execute_next_physical_full_matrix_phase(
                config=self.config(),
                plan=plan,
                adapters=adapters,
                now=NOW,
            )
        self.assertEqual([], journal.receipts)

        # Then reproduce the old shared-request attack: mutate route d -> f,
        # alter the phase oracle, and recompute the request hash.  The driver
        # validates the returned oracle against a distinct expected request,
        # so it cannot accept the adapter-owned projection.
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        attacker = _RequestMutatingAdapter(NOW)
        mapped[plan.phases[0].name] = attacker
        with patch.object(driver.os, "geteuid", return_value=0), self.assertRaisesRegex(
            driver.PhysicalFullMatrixExecutionDriverError,
            "ORACLE_BINDING_MISMATCH",
        ):
            driver.execute_next_physical_full_matrix_phase(
                config=self.config(),
                plan=plan,
                adapters=adapters,
                now=NOW,
            )
        self.assertEqual([], journal.receipts)
        self.assertEqual(1, len(attacker.requests))
        self.assertEqual("f" * 64, attacker.requests[0].binding.route_binding_sha256)
        self.assertEqual("forged-route-oracle-v1", attacker.requests[0].phase.oracle)
        self.assertIsNot(attacker.requests[0].binding, plan.binding)
        self.assertIsNot(attacker.requests[0].phase, plan.phases[0])

    def test_disabled_nonroot_incomplete_or_legacy_input_refuse_before_adapter(self) -> None:
        with self.assertRaisesRegex(driver.PhysicalFullMatrixExecutionDriverError, "DISABLED"):
            self.plan(enabled=False)
        with patch.object(driver.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            driver.PhysicalFullMatrixExecutionDriverError, "ROOT_RUNTIME_REQUIRED"
        ):
            driver.build_physical_full_matrix_execution_plan(config=self.config())
        bad = replace(readiness(), observed_slots=())
        with self.assertRaisesRegex(driver.PhysicalFullMatrixExecutionDriverError, "READINESS_INCOMPLETE"):
            self.plan(readiness=bad)
        with self.assertRaisesRegex(driver.PhysicalFullMatrixExecutionDriverError, "LEGACY_RUNNER_REJECTED"):
            self.plan(legacy_runner_artifacts=("scripts/run_production_full_matrix.py",))

    def test_missing_adapter_set_refuses_without_phase_execution(self) -> None:
        plan = self.plan()
        adapters, _journal, mapped = self.adapters()
        del mapped[driver.PHYSICAL_FULL_MATRIX_PHASES[0].name]
        bad = driver.PhysicalFullMatrixExecutionAdapters(
            phase_adapters=mapped, receipt_journal=adapters.receipt_journal
        )
        with self.assertRaisesRegex(driver.PhysicalFullMatrixExecutionDriverError, "ADAPTER_SET_INVALID"):
            driver.prepare_physical_full_matrix_execution_adapters(plan=plan, adapters=bad)

    def test_execution_disabled_refuses_before_journal_or_phase_adapter(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        with self.assertRaisesRegex(driver.PhysicalFullMatrixExecutionDriverError, "DISABLED"):
            driver.execute_next_physical_full_matrix_phase(
                config=self.config(enabled=False), plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)
        self.assertTrue(all(not item.requests for item in mapped.values()))

    def test_raw_or_forged_readiness_refuses_before_journal_or_phase_adapter(self) -> None:
        # Build the non-authorizing plan under the mechanics mock, then remove
        # it before the effectful boundary.  Both a report-shaped raw value
        # and a caller-constructed wrapper must fail before journal access or
        # adapter preparation.
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        self._readiness_verifier.stop()
        forged = VerifiedPhysicalFullMatrixCampaignReadiness(report=readiness())

        for candidate in (readiness(), forged):
            with patch.object(driver.os, "geteuid", return_value=0), self.assertRaisesRegex(
                driver.PhysicalFullMatrixExecutionDriverError,
                "READINESS_PROVENANCE_INVALID",
            ):
                driver.build_physical_full_matrix_execution_plan(
                    config=self.config(readiness=candidate)
                )
            with patch.object(driver.os, "geteuid", return_value=0), self.assertRaisesRegex(
                driver.PhysicalFullMatrixExecutionDriverError,
                "READINESS_PROVENANCE_INVALID",
            ):
                driver.execute_next_physical_full_matrix_phase(
                    config=self.config(readiness=candidate),
                    plan=plan,
                    adapters=adapters,
                    now=NOW,
                )

        self.assertEqual([], journal.receipts)
        self.assertTrue(all(not item.requests for item in mapped.values()))

    def test_execution_rechecks_verified_readiness_before_journal_or_phase_adapter(self) -> None:
        self._readiness_verifier.stop()
        positive = readiness()
        blocked = replace(
            positive,
            status="blocked",
            reason_codes=("revalidation-became-blocked",),
        )
        source_config = PhysicalFullMatrixCampaignReadinessConfig(
            binding=None,
            enabled=True,
        )
        source_inputs = PhysicalFullMatrixCampaignInputs()

        # The first assessor result is positive, so minting succeeds.  The
        # second is the execution-time re-assessment and must reject before
        # any adapter or journal is touched.
        with patch.object(
            readiness_boundary,
            "assess_physical_full_matrix_campaign_readiness",
            side_effect=(positive, blocked),
        ):
            verified = mint_verified_physical_full_matrix_campaign_readiness(
                config=source_config,
                inputs=source_inputs,
                now=NOW,
            )
            with patch.object(driver.os, "geteuid", return_value=0):
                plan = driver.build_physical_full_matrix_execution_plan(
                    config=self.config(readiness=verified)
                )
                adapters, journal, mapped = self.adapters()
                with self.assertRaisesRegex(
                    driver.PhysicalFullMatrixExecutionDriverError,
                    "READINESS_PROVENANCE_INVALID",
                ):
                    driver.execute_next_physical_full_matrix_phase(
                        config=self.config(readiness=verified),
                        plan=plan,
                        adapters=adapters,
                        now=NOW + timedelta(seconds=1),
                    )

        self.assertEqual([], journal.receipts)
        self.assertTrue(all(not item.requests for item in mapped.values()))

    def test_one_phase_only_receipt_chain_and_idempotent_reentry(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        with patch.object(driver.os, "geteuid", return_value=0):
            first = driver.execute_next_physical_full_matrix_phase(
                config=self.config(), plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual("completed-redacted-phase-receipt", first.status)
        self.assertEqual(plan.phases[0].name, first.phase)
        self.assertEqual(plan.phases[1].name, first.next_phase)
        self.assertEqual(1, len(journal.receipts))
        self.assertEqual(1, len(mapped[plan.phases[0].name].requests))
        self.assertEqual(0, len(mapped[plan.phases[1].name].requests))
        parsed = driver.parse_physical_full_matrix_run_receipt(journal.receipts[0])
        self.assertEqual(plan.plan_sha256, parsed.plan_sha256)
        self.assertNotIn(b"credential", journal.receipts[0].lower())
        self.assertNotIn(b"ssh", journal.receipts[0].lower())

    def test_witness_transition_receipts_switch_direction_only_after_their_phase(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        results = []
        with patch.object(driver.os, "geteuid", return_value=0):
            for _ in driver.PHYSICAL_FULL_MATRIX_PHASES:
                results.append(
                    driver.execute_next_physical_full_matrix_phase(
                        config=self.config(), plan=plan, adapters=adapters, now=NOW
                    )
                )
        self.assertEqual(8, len(journal.receipts))
        self.assertEqual(
            ("webapp_fi", "webapp_ir"),
            (
                mapped["recover-ir-through-object-storage"].requests[0].binding.source_site,
                mapped["recover-ir-through-object-storage"].requests[0].binding.destination_site,
            ),
        )
        self.assertEqual(
            ("webapp_ir", "webapp_fi"),
            (
                mapped["ir-writer-durable-ack-matrix"].requests[0].binding.source_site,
                mapped["ir-writer-durable-ack-matrix"].requests[0].binding.destination_site,
            ),
        )
        self.assertEqual(
            ("webapp_fi", "webapp_ir"),
            (
                mapped["final-three-site-convergence-oracle"].requests[0].binding.source_site,
                mapped["final-three-site-convergence-oracle"].requests[0].binding.destination_site,
            ),
        )
        promoted = driver.parse_physical_full_matrix_run_receipt(journal.receipts[3])
        self.assertIsNotNone(promoted.successor_binding)
        self.assertEqual("webapp_ir", promoted.successor_binding.source_site)  # type: ignore[union-attr]
        self.assertEqual("completed-redacted-phase-receipt", results[-1].status)

    def test_witness_transition_phase_refuses_missing_successor_before_receipt_append(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        mapped["witness-promote-ir"].emit_successor = False
        with patch.object(driver.os, "geteuid", return_value=0):
            for _ in range(3):
                driver.execute_next_physical_full_matrix_phase(
                    config=self.config(), plan=plan, adapters=adapters, now=NOW
                )
            with self.assertRaisesRegex(
                driver.PhysicalFullMatrixExecutionDriverError,
                "SUCCESSOR_REQUIRED",
            ):
                driver.execute_next_physical_full_matrix_phase(
                    config=self.config(), plan=plan, adapters=adapters, now=NOW
                )
        self.assertEqual(3, len(journal.receipts))

    def test_reverse_direct_control_or_nonmonotonic_successor_refuse_before_append(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        for _ in range(3):
            with patch.object(driver.os, "geteuid", return_value=0):
                driver.execute_next_physical_full_matrix_phase(
                    config=self.config(), plan=plan, adapters=adapters, now=NOW
                )

        original = mapped["witness-promote-ir"].execute_phase

        def direct_reverse(*, request):
            oracle = original(request=request)
            return replace(oracle, direct_ir_to_fi_control="permitted")

        mapped["witness-promote-ir"].execute_phase = direct_reverse  # type: ignore[method-assign]
        with patch.object(driver.os, "geteuid", return_value=0), self.assertRaisesRegex(
            driver.PhysicalFullMatrixExecutionDriverError,
            "ORACLE_BINDING_MISMATCH",
        ):
            driver.execute_next_physical_full_matrix_phase(
                config=self.config(), plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual(3, len(journal.receipts))

    def test_atomic_claim_returns_durable_existing_receipt_without_second_adapter_call(self) -> None:
        plan = self.plan()
        journal = _RaceJournal()
        mapped = {phase.name: _Adapter(NOW) for phase in driver.PHYSICAL_FULL_MATRIX_PHASES}
        adapters = driver.PhysicalFullMatrixExecutionAdapters(
            phase_adapters=mapped,
            receipt_journal=journal,
        )
        with patch.object(driver.os, "geteuid", return_value=0):
            driver.execute_next_physical_full_matrix_phase(
                config=self.config(), plan=plan, adapters=adapters, now=NOW
            )
            journal.hide_next_read = True
            result = driver.execute_next_physical_full_matrix_phase(
                config=self.config(), plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual("already-completed-from-append-only-receipt", result.status)
        self.assertEqual(plan.phases[0].name, result.phase)
        self.assertEqual(1, len(mapped[plan.phases[0].name].requests))
        self.assertEqual(1, len(journal.receipts))

    def test_live_atomic_claim_refuses_without_second_adapter_call(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        request = driver._request_for(plan=plan, phase=plan.phases[0])
        journal.claim_phase(
            run_id=request.run_id,
            plan_sha256=request.plan_sha256,
            sequence=request.phase.sequence,
            phase_request_sha256=request.phase_request_sha256,
        )
        with patch.object(driver.os, "geteuid", return_value=0), self.assertRaisesRegex(
            driver.PhysicalFullMatrixExecutionDriverError,
            "PHASE_CLAIM_BUSY",
        ):
            driver.execute_next_physical_full_matrix_phase(
                config=self.config(), plan=plan, adapters=adapters, now=NOW
            )
        self.assertTrue(all(not item.requests for item in mapped.values()))

    def test_oracle_binding_staleness_and_receipt_tampering_refuse(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters(NOW - timedelta(seconds=121))
        with patch.object(driver.os, "geteuid", return_value=0), self.assertRaisesRegex(
            driver.PhysicalFullMatrixExecutionDriverError, "ORACLE_STALE"
        ):
            driver.execute_next_physical_full_matrix_phase(
                config=self.config(), plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)
        adapters, journal, _mapped = self.adapters()
        with patch.object(driver.os, "geteuid", return_value=0):
            driver.execute_next_physical_full_matrix_phase(
                config=self.config(), plan=plan, adapters=adapters, now=NOW
            )
        tampered = journal.receipts[0].replace(b'"sequence":1', b'"sequence":2')
        journal.receipts[:] = [tampered]
        with patch.object(driver.os, "geteuid", return_value=0), self.assertRaisesRegex(
            driver.PhysicalFullMatrixExecutionDriverError,
            "RECEIPT_CHAIN_MISMATCH|RECEIPT_NONCANONICAL|RECEIPT_PHASE_INVALID",
        ):
            driver.execute_next_physical_full_matrix_phase(
                config=self.config(), plan=plan, adapters=adapters, now=NOW
            )
