from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import unittest

from core import physical_operational_failover_v1_writer_admission as admission


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RELEASE_SHA = "a" * 40


@dataclass(frozen=True)
class _TermEvidence:
    cluster_id: str = "gold-trade-three-site"
    holder_site: str = "webapp_fi"
    writer_epoch: int = 7
    writer_lease_id: str = "writer-lease-7"
    release_sha: str = RELEASE_SHA
    generation_id: str = "physical-generation-7"
    evidence_id: str = "witness-grant-0001"
    revalidation_id: str = "revalidation-0001"
    issued_at: datetime = NOW - timedelta(seconds=10)
    expires_at: datetime = NOW + timedelta(seconds=60)


class _Revalidator:
    def __init__(self, *results: object) -> None:
        self._results = list(results)
        self.requests: list[admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest] = []

    def revalidate_writer_term(
        self,
        *,
        request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
    ) -> object:
        self.requests.append(request)
        if not self._results:
            raise AssertionError("unexpected revalidation")
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _StateRestorer:
    def __init__(self, state: admission.PhysicalOperationalFailoverV1WriterAdmissionState) -> None:
        self.state = state
        self.bindings: list[admission.PhysicalOperationalFailoverV1WriterAdmissionBinding] = []

    def restore_writer_admission_state(
        self,
        *,
        binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        self.bindings.append(binding)
        return self.state


class PhysicalOperationalFailoverV1WriterAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id="gold-trade-three-site",
            local_site="webapp_fi",
            release_sha=RELEASE_SHA,
            generation_id="physical-generation-7",
        )
        self.config = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id="root-runtime-instance-0001",
            safety_margin_seconds=5,
            maximum_term_duration_seconds=90,
            maximum_evidence_age_seconds=60,
        )

    def state(self) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        return admission.new_physical_operational_failover_v1_writer_admission_state(
            binding=self.binding
        )

    def evidence(self, **overrides: object) -> _TermEvidence:
        values: dict[str, object] = {
            "revalidation_id": "revalidation-0001",
        }
        values.update(overrides)
        return replace(_TermEvidence(), **values)

    def activate(
        self,
        *,
        state: admission.PhysicalOperationalFailoverV1WriterAdmissionState | None = None,
        config: admission.PhysicalOperationalFailoverV1WriterAdmissionConfig | None = None,
        evidence: _TermEvidence | None = None,
        revalidation_id: str = "revalidation-0001",
        now: datetime = NOW,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        previous = self.state() if state is None else state
        selected_config = self.config if config is None else config
        transition = admission.revalidate_physical_operational_failover_v1_writer_admission(
            config=selected_config,
            state=previous,
            evidence_revalidator=_Revalidator(
                self.evidence(revalidation_id=revalidation_id)
                if evidence is None
                else evidence
            ),
            revalidation_id=revalidation_id,
            now=now,
        )
        self.assertIsNotNone(transition)
        assert transition is not None
        return admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=previous,
            transition=transition,
        )

    def raw_copy(
        self,
        state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        """Simulate a decoded durable record: no process-local capability."""

        return admission.PhysicalOperationalFailoverV1WriterAdmissionState(
            schema=state.schema,
            binding=state.binding,
            revision=state.revision,
            highest_writer_epoch=state.highest_writer_epoch,
            active_term=state.active_term,
            revalidated_runtime_instance_id=state.revalidated_runtime_instance_id,
            clock_floor=state.clock_floor,
            fence_generation=state.fence_generation,
            fenced=state.fenced,
            fence_reason=state.fence_reason,
            requires_fresh_witness_revalidation=state.requires_fresh_witness_revalidation,
        )

    def test_default_off_never_invokes_revalidator_or_validates_runtime_inputs(self) -> None:
        disabled = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(enabled=False)
        revalidator = _Revalidator(self.evidence())

        self.assertIsNone(
            admission.revalidate_physical_operational_failover_v1_writer_admission(
                config=disabled,
                state=object(),  # type: ignore[arg-type]
                evidence_revalidator=revalidator,
                revalidation_id="bad",
                now=object(),  # type: ignore[arg-type]
            )
        )
        self.assertIsNone(
            admission.begin_physical_operational_failover_v1_writer_operation(
                config=disabled,
                state=object(),  # type: ignore[arg-type]
                operation_kind="not-even-an-operation",
                now=object(),  # type: ignore[arg-type]
            )
        )
        self.assertIsNone(
            admission.require_physical_operational_failover_v1_writer_admission(
                config=disabled,
                state=object(),  # type: ignore[arg-type]
                operation=object(),  # type: ignore[arg-type]
                now=object(),  # type: ignore[arg-type]
            )
        )
        self.assertEqual(revalidator.requests, [])

    def test_fresh_revalidation_then_commit_and_external_effect_require_durable_transitions(self) -> None:
        active = self.activate()
        self.assertFalse(active.fenced)
        self.assertEqual(active.revision, 1)
        self.assertEqual(active.revalidated_runtime_instance_id, self.config.runtime_instance_id)

        commit_operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.config,
            state=active,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
            now=NOW + timedelta(seconds=1),
        )
        self.assertIsNotNone(commit_operation)
        assert commit_operation is not None
        commit_admission = admission.require_physical_operational_failover_v1_writer_admission(
            config=self.config,
            state=active,
            operation=commit_operation,
            now=NOW + timedelta(seconds=2),
        )
        self.assertIsNotNone(commit_admission)
        assert commit_admission is not None
        self.assertEqual(commit_admission.term.writer_epoch, 7)
        self.assertEqual(commit_admission.state_transition.prior_state.revision, 1)
        active = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=active,
            transition=commit_admission.state_transition,
        )
        self.assertEqual(active.revision, 2)
        self.assertEqual(active.clock_floor, NOW + timedelta(seconds=2))

        effect_operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.config,
            state=active,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_EXTERNAL_EFFECT,
            now=NOW + timedelta(seconds=3),
        )
        self.assertIsNotNone(effect_operation)
        assert effect_operation is not None
        effect_admission = admission.require_physical_operational_failover_v1_writer_admission(
            config=self.config,
            state=active,
            operation=effect_operation,
            now=NOW + timedelta(seconds=4),
        )
        self.assertIsNotNone(effect_admission)
        assert effect_admission is not None
        self.assertEqual(
            effect_admission.operation.operation_kind,
            admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_EXTERNAL_EFFECT,
        )
        active = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=active,
            transition=effect_admission.state_transition,
        )
        self.assertEqual(active.revision, 3)
        self.assertEqual(active.clock_floor, NOW + timedelta(seconds=4))

    def test_missing_or_expired_term_evidence_fails_closed(self) -> None:
        with self.subTest("missing"):
            with self.assertRaisesRegex(
                admission.PhysicalOperationalFailoverV1WriterAdmissionError,
                "TERM_EVIDENCE_INVALID",
            ):
                admission.revalidate_physical_operational_failover_v1_writer_admission(
                    config=self.config,
                    state=self.state(),
                    evidence_revalidator=_Revalidator(None),
                    revalidation_id="revalidation-0001",
                    now=NOW,
                )

        with self.subTest("expired-at-safety-margin"):
            with self.assertRaisesRegex(
                admission.PhysicalOperationalFailoverV1WriterAdmissionError,
                "TERM_EXPIRED",
            ):
                admission.revalidate_physical_operational_failover_v1_writer_admission(
                    config=self.config,
                    state=self.state(),
                    evidence_revalidator=_Revalidator(
                        self.evidence(expires_at=NOW + timedelta(seconds=5))
                    ),
                    revalidation_id="revalidation-0001",
                    now=NOW,
                )

    def test_short_canonical_writer_lease_is_admitted_but_colon_alias_is_rejected(self) -> None:
        active = self.activate(
            evidence=self.evidence(writer_lease_id="writer-lease-73"),
        )
        self.assertEqual("writer-lease-73", active.active_term.writer_lease_id)  # type: ignore[union-attr]
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "TERM_EVIDENCE_INVALID",
        ):
            self.activate(
                evidence=self.evidence(writer_lease_id="writer:lease-000073"),
            )

    def test_replayed_evidence_or_revalidation_identifier_fails_closed(self) -> None:
        active = self.activate()
        replay = self.evidence(
            evidence_id=active.active_term.evidence_id,  # type: ignore[union-attr]
            revalidation_id="revalidation-0002",
            issued_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(seconds=61),
        )
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "TERM_EVIDENCE_REPLAYED",
        ):
            admission.revalidate_physical_operational_failover_v1_writer_admission(
                config=self.config,
                state=active,
                evidence_revalidator=_Revalidator(replay),
                revalidation_id="revalidation-0002",
                now=NOW + timedelta(seconds=1),
            )

        source = _Revalidator(self.evidence())
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "REVALIDATION_REPLAYED",
        ):
            admission.revalidate_physical_operational_failover_v1_writer_admission(
                config=self.config,
                state=active,
                evidence_revalidator=source,
                revalidation_id="revalidation-0001",
                now=NOW + timedelta(seconds=1),
            )
        self.assertEqual(source.requests, [])

    def test_clock_regression_at_start_or_commit_fails_closed(self) -> None:
        active = self.activate()
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "CLOCK_REGRESSION",
        ):
            admission.begin_physical_operational_failover_v1_writer_operation(
                config=self.config,
                state=active,
                operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
                now=NOW - timedelta(seconds=1),
            )

        operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.config,
            state=active,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
            now=NOW + timedelta(seconds=2),
        )
        self.assertIsNotNone(operation)
        assert operation is not None
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "CLOCK_REGRESSION",
        ):
            admission.require_physical_operational_failover_v1_writer_admission(
                config=self.config,
                state=active,
                operation=operation,
                now=NOW + timedelta(seconds=1),
            )

    def test_newer_epoch_with_clock_regressed_term_evidence_fails_closed(self) -> None:
        active = self.activate()
        regressed_evidence = self.evidence(
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
            evidence_id="witness-grant-0002",
            revalidation_id="revalidation-0002",
            issued_at=NOW - timedelta(seconds=11),
            expires_at=NOW + timedelta(seconds=59),
        )
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "TERM_EVIDENCE_CLOCK_REGRESSION",
        ):
            admission.revalidate_physical_operational_failover_v1_writer_admission(
                config=self.config,
                state=active,
                evidence_revalidator=_Revalidator(regressed_evidence),
                revalidation_id="revalidation-0002",
                now=NOW + timedelta(seconds=1),
            )

    def test_restart_requires_fresh_witness_revalidation_for_new_runtime_instance(self) -> None:
        active = self.activate()
        restarted = replace(self.config, runtime_instance_id="root-runtime-instance-0002")
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "FRESH_REVALIDATION_REQUIRED",
        ):
            admission.begin_physical_operational_failover_v1_writer_operation(
                config=restarted,
                state=active,
                operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
                now=NOW + timedelta(seconds=1),
            )

        fresh_evidence = self.evidence(
            evidence_id="witness-grant-0002",
            revalidation_id="revalidation-0002",
            issued_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(seconds=61),
        )
        active = self.activate(
            state=active,
            config=restarted,
            evidence=fresh_evidence,
            revalidation_id="revalidation-0002",
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(active.revalidated_runtime_instance_id, "root-runtime-instance-0002")
        self.assertIsNotNone(
            admission.begin_physical_operational_failover_v1_writer_operation(
                config=restarted,
                state=active,
                operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
                now=NOW + timedelta(seconds=2),
            )
        )

    def test_forged_raw_active_state_cannot_begin_or_admit_a_writer_operation(self) -> None:
        active = self.activate()
        operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.config,
            state=active,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
            now=NOW + timedelta(seconds=1),
        )
        self.assertIsNotNone(operation)
        assert operation is not None
        forged = self.raw_copy(active)

        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "STATE_UNATTESTED",
        ):
            admission.begin_physical_operational_failover_v1_writer_operation(
                config=self.config,
                state=forged,
                operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
                now=NOW + timedelta(seconds=1),
            )
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "STATE_UNATTESTED",
        ):
            admission.require_physical_operational_failover_v1_writer_admission(
                config=self.config,
                state=forged,
                operation=operation,
                now=NOW + timedelta(seconds=2),
            )

    def test_explicit_root_owned_restore_reissues_state_but_requires_fresh_witness(self) -> None:
        active = self.activate()
        restarted = replace(self.config, runtime_instance_id="root-runtime-instance-0002")
        restorer = _StateRestorer(self.raw_copy(active))
        restored = admission.restore_physical_operational_failover_v1_writer_admission_state(
            config=restarted,
            state_restorer=restorer,
            now=NOW + timedelta(seconds=1),
        )
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restorer.bindings, [self.binding])
        self.assertTrue(restored.requires_fresh_witness_revalidation)
        self.assertIsNone(restored.revalidated_runtime_instance_id)
        self.assertEqual(restored.fence_generation, active.fence_generation + 1)
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "FRESH_REVALIDATION_REQUIRED",
        ):
            admission.begin_physical_operational_failover_v1_writer_operation(
                config=restarted,
                state=restored,
                operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
                now=NOW + timedelta(seconds=2),
            )

        fresh = self.evidence(
            evidence_id="witness-grant-0002",
            revalidation_id="revalidation-0002",
            issued_at=NOW + timedelta(seconds=2),
            expires_at=NOW + timedelta(seconds=62),
        )
        revalidated = self.activate(
            state=restored,
            config=restarted,
            evidence=fresh,
            revalidation_id="revalidation-0002",
            now=NOW + timedelta(seconds=2),
        )
        self.assertFalse(revalidated.requires_fresh_witness_revalidation)
        self.assertIsNotNone(
            admission.begin_physical_operational_failover_v1_writer_operation(
                config=restarted,
                state=revalidated,
                operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
                now=NOW + timedelta(seconds=3),
            )
        )

    def test_open_transaction_or_external_effect_after_local_fence_is_rejected(self) -> None:
        active = self.activate()
        transaction = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.config,
            state=active,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
            now=NOW + timedelta(seconds=1),
        )
        effect = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.config,
            state=active,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_EXTERNAL_EFFECT,
            now=NOW + timedelta(seconds=1),
        )
        self.assertIsNotNone(transaction)
        self.assertIsNotNone(effect)
        assert transaction is not None and effect is not None
        fence = admission.fence_physical_operational_failover_v1_writer_admission(
            config=self.config,
            state=active,
            fence_reason="witness_link_lost",
            now=NOW + timedelta(seconds=2),
        )
        self.assertIsNotNone(fence)
        assert fence is not None
        fenced = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=active,
            transition=fence,
        )
        self.assertTrue(fenced.fenced)
        self.assertEqual(fenced.fence_generation, 1)

        for operation in (transaction, effect):
            with self.subTest(operation=operation.operation_kind), self.assertRaisesRegex(
                admission.PhysicalOperationalFailoverV1WriterAdmissionError,
                "WRITER_ADMISSION_FENCED",
            ):
                admission.require_physical_operational_failover_v1_writer_admission(
                    config=self.config,
                    state=fenced,
                    operation=operation,
                    now=NOW + timedelta(seconds=3),
                )

    def test_wrong_site_epoch_lease_release_or_generation_is_rejected(self) -> None:
        active = self.activate()
        common = {
            "evidence_id": "witness-grant-0002",
            "revalidation_id": "revalidation-0002",
            "issued_at": NOW + timedelta(seconds=1),
            "expires_at": NOW + timedelta(seconds=61),
        }
        cases = (
            ("site", {"holder_site": "webapp_ir"}, "TERM_SITE_MISMATCH"),
            ("epoch", {"writer_epoch": 6}, "TERM_EPOCH_REPLAYED"),
            ("lease", {"writer_lease_id": "writer-lease-8"}, "TERM_LEASE_MISMATCH"),
            ("release", {"release_sha": "b" * 40}, "TERM_RELEASE_MISMATCH"),
            ("generation", {"generation_id": "physical-generation-8"}, "TERM_GENERATION_MISMATCH"),
        )
        for name, override, expected_code in cases:
            with self.subTest(name=name), self.assertRaisesRegex(
                admission.PhysicalOperationalFailoverV1WriterAdmissionError,
                expected_code,
            ):
                admission.revalidate_physical_operational_failover_v1_writer_admission(
                    config=self.config,
                    state=active,
                    evidence_revalidator=_Revalidator(self.evidence(**common, **override)),
                    revalidation_id="revalidation-0002",
                    now=NOW + timedelta(seconds=1),
                )

    def test_fence_requires_a_strictly_newer_epoch_before_writer_reactivation(self) -> None:
        active = self.activate()
        fence = admission.fence_physical_operational_failover_v1_writer_admission(
            config=self.config,
            state=active,
            fence_reason="witness_link_lost",
            now=NOW + timedelta(seconds=1),
        )
        self.assertIsNotNone(fence)
        assert fence is not None
        fenced = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=active,
            transition=fence,
        )
        same_epoch = self.evidence(
            evidence_id="witness-grant-0002",
            revalidation_id="revalidation-0002",
            issued_at=NOW + timedelta(seconds=2),
            expires_at=NOW + timedelta(seconds=62),
        )
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "FENCED_TERM_REACTIVATION",
        ):
            admission.revalidate_physical_operational_failover_v1_writer_admission(
                config=self.config,
                state=fenced,
                evidence_revalidator=_Revalidator(same_epoch),
                revalidation_id="revalidation-0002",
                now=NOW + timedelta(seconds=2),
            )

        successor = self.evidence(
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
            evidence_id="witness-grant-0003",
            revalidation_id="revalidation-0003",
            issued_at=NOW + timedelta(seconds=3),
            expires_at=NOW + timedelta(seconds=63),
        )
        revalidated = self.activate(
            state=fenced,
            evidence=successor,
            revalidation_id="revalidation-0003",
            now=NOW + timedelta(seconds=3),
        )
        self.assertFalse(revalidated.fenced)
        self.assertEqual(revalidated.highest_writer_epoch, 8)
        self.assertEqual(revalidated.active_term.writer_lease_id, "writer-lease-8")  # type: ignore[union-attr]

    def test_transition_is_bound_to_exact_prior_state_for_durable_cas(self) -> None:
        initial = self.state()
        transition = admission.revalidate_physical_operational_failover_v1_writer_admission(
            config=self.config,
            state=initial,
            evidence_revalidator=_Revalidator(self.evidence()),
            revalidation_id="revalidation-0001",
            now=NOW,
        )
        self.assertIsNotNone(transition)
        assert transition is not None
        active = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=initial,
            transition=transition,
        )
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "TRANSITION_STALE",
        ):
            admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
                state=active,
                transition=transition,
            )


if __name__ == "__main__":
    unittest.main()
