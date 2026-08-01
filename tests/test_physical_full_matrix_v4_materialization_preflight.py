"""Adversarial tests for the static, fresh V4 materialization preflight."""

from __future__ import annotations

import ast
from dataclasses import fields, replace
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
from uuid import UUID
import unittest
from unittest.mock import patch

from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v2_gen2_witnessed_campaign_readiness as readiness_owner
from core import physical_full_matrix_v2_witnessed_campaign_readiness as legacy_readiness
from core import physical_full_matrix_v4_materialization_preflight as subject
from core import physical_full_matrix_v4_root_composition as composition
from core import physical_full_matrix_v4_witness_anchor_wire as wire
from tests.test_physical_full_matrix_v2_gen2_witnessed_ack_chain import (
    Gen2WitnessedAckChainFixture,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_materialization_preflight.py"
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


class _SequenceClock:
    """A root-owned clock test double; root composition must not call it."""

    def __init__(self) -> None:
        self.calls = 0
        self.values: list[object] = []

    def reset(self, *values: object) -> None:
        self.values = list(values)

    def now_utc(self) -> object:
        self.calls += 1
        if not self.values:
            raise AssertionError("materialization preflight sampled an unconfigured clock")
        return self.values.pop(0)


class _NeverCalledPhaseAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute_phase(self, *, request: object) -> object:
        del request
        self.calls += 1
        raise AssertionError("materialization preflight invoked a phase adapter")


class _NeverCalledPostEffectVerifier:
    def __init__(self, phase: object) -> None:
        self.phase_name = phase.name
        self.phase_sequence = phase.sequence
        self.oracle = phase.oracle
        self.transport_profile = phase.transport_profile
        self.calls = 0

    def require_post_effect_completion(self, **kwargs: object) -> None:
        del kwargs
        self.calls += 1
        raise AssertionError("materialization preflight invoked a post-effect verifier")


class _NeverCalledJournal:
    def __init__(self) -> None:
        self.calls = 0

    def _called(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.calls += 1
        raise AssertionError("materialization preflight invoked a journal")

    read_receipts = _called
    claim_phase = _called
    mark_effect_started = _called
    project_effect_start_anchor_proof = _called
    project_predecessor_phase_completion_anchor_proof = _called
    append_started = _called


class _NeverCalledResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve_readiness(self, *, binding: object) -> object:
        del binding
        self.calls += 1
        raise AssertionError("materialization preflight invoked a readiness resolver")


class _NeverCalledContinuity:
    def __init__(self) -> None:
        self.calls = 0

    def verify_campaign_continuity(self, **kwargs: object) -> None:
        del kwargs
        self.calls += 1
        raise AssertionError("materialization preflight invoked a continuity gate")


class _NeverCalledWitnessAnchor:
    def __init__(self) -> None:
        self.calls = 0

    def read_head(self, **kwargs: object) -> object:
        del kwargs
        self.calls += 1
        raise AssertionError("materialization preflight invoked a Witness anchor")

    def append_commitment(self, **kwargs: object) -> object:
        del kwargs
        self.calls += 1
        raise AssertionError("materialization preflight invoked a Witness anchor")


class PhysicalFullMatrixV4MaterializationPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = Gen2WitnessedAckChainFixture()
        cls.fixture.setUp()
        cls.now = cls.fixture.now
        chain = cls.fixture.mint_chain(now=cls.now)
        cls.readiness_binding = (
            readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding(
                **{
                    item.name: getattr(chain, item.name)
                    for item in fields(
                        readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding
                    )
                }
            )
        )
        cls.readiness_config = (
            readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig(
                binding=cls.readiness_binding,
                gen2_witnessed_ack_chain_config=cls.fixture.config,
                enabled=True,
            )
        )
        cls.readiness_inputs = (
            readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignInputs(
                gen2_witnessed_ack_chain=chain,
            )
        )
        with cls.fixture._all_owner_clocks(now=cls.now):
            cls.readiness = (
                readiness_owner.mint_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                    config=cls.readiness_config,
                    inputs=cls.readiness_inputs,
                    now=cls.now,
                )
            )
        cls.binding = driver.PhysicalFullMatrixV4ExecutionBinding(
            campaign_id=cls.readiness_binding.campaign_id,
            release_sha=cls.readiness_binding.release_sha,
            readiness_binding_sha256=cls.readiness.report.binding_sha256,
            route_commitment_sha256=cls.readiness_binding.route_commitment_sha256,
            four_role_binding_sha256=cls.readiness_binding.four_role_binding_sha256,
            writer_holder_site=cls.readiness_binding.writer_holder_site,
            writer_epoch=cls.readiness_binding.writer_epoch,
            writer_lease_id=cls.readiness_binding.writer_lease_id,
            witnessed_term_proof_sha256=cls.readiness_binding.witnessed_term_proof_sha256,
            source_site=cls.readiness_binding.source_site,
            destination_site=cls.readiness_binding.destination_site,
            roundtrip_attestation_sha256=(
                cls.readiness_binding.roundtrip_attestation_sha256
            ),
            roundtrip_configuration_sha256=(
                cls.readiness_binding.roundtrip_configuration_sha256
            ),
            witness_transition_id=cls.readiness_binding.witness_transition_id,
            witness_sequence=cls.readiness_binding.witness_sequence,
        )
        cls.run_id = UUID("a1f9b6df-2e94-4b4d-9f8f-b167cff543bc")
        cls.execution_config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=cls.binding,
            readiness=cls.readiness,
            run_id=cls.run_id,
            enabled=True,
        )
        cls.plan = driver.build_physical_full_matrix_v4_execution_plan(
            config=cls.execution_config
        )
        cls.policy_sha256 = (
            composition.derive_physical_full_matrix_v4_root_composition_policy_sha256(
                binding=cls.binding,
                run_id=cls.run_id,
                maximum_oracle_age_seconds=cls.execution_config.maximum_oracle_age_seconds,
            )
        )
        cls.root_config = composition.PhysicalFullMatrixV4RootCompositionConfig(
            enabled=True,
            campaign_id=cls.binding.campaign_id,
            release_sha=cls.binding.release_sha,
            run_id=cls.run_id,
            maximum_oracle_age_seconds=cls.execution_config.maximum_oracle_age_seconds,
            policy_sha256=cls.policy_sha256,
        )
        cls.clock = _SequenceClock()
        cls.phase_adapters = {
            phase.name: _NeverCalledPhaseAdapter()
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        cls.phase_bindings = {
            phase.name: composition.PhysicalFullMatrixV4RootPhaseAdapterBinding(
                phase_name=phase.name,
                phase_sequence=phase.sequence,
                oracle=phase.oracle,
                transport_profile=phase.transport_profile,
                destructive=phase.destructive,
                campaign_id=cls.binding.campaign_id,
                release_sha=cls.binding.release_sha,
                policy_sha256=cls.policy_sha256,
                phase_adapter=cls.phase_adapters[phase.name],
            )
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        cls.phase_post_effect_verifiers = {
            phase.name: _NeverCalledPostEffectVerifier(phase)
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        cls.journal = _NeverCalledJournal()
        cls.resolver = _NeverCalledResolver()
        cls.continuity = _NeverCalledContinuity()
        with patch.object(composition.os, "geteuid", return_value=0):
            cls.composition = composition.build_physical_full_matrix_v4_root_composition(
                root_config=cls.root_config,
                execution_config=cls.execution_config,
                plan=cls.plan,
                phase_adapters=cls.phase_bindings,
                phase_post_effect_verifiers=cls.phase_post_effect_verifiers,
                receipt_journal=cls.journal,
                readiness_resolver=cls.resolver,
                trusted_clock=cls.clock,
                campaign_continuity_gate=cls.continuity,
            )
        cls.anchor = _NeverCalledWitnessAnchor()
        cls.anchor_identity = wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity(
            schema=wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA,
            journal_binding_sha256=_sha("journal-binding"),
            baseline_plan_binding_sha256=_sha("baseline-plan-binding"),
            run_id=cls.run_id,
            plan_sha256=cls.plan.plan_sha256,
            anchor_genesis_sequence=0,
            anchor_genesis_head_sha256=_sha("anchor-genesis-head"),
            canonical_genesis_sha256=_sha("canonical-genesis"),
        )
        cls.anchor_binding = subject.PhysicalFullMatrixV4MaterializationWitnessAnchorBinding(
            identity=cls.anchor_identity,
            anchor=cls.anchor,
        )
        cls.config = subject.PhysicalFullMatrixV4MaterializationPreflightConfig(
            enabled=True
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.tearDown()

    def setUp(self) -> None:
        self.clock.calls = 0
        self.clock.reset()

    def _inputs(self, **overrides: object) -> subject.PhysicalFullMatrixV4MaterializationPreflightInputs:
        values: dict[str, object] = {
            "composition": self.composition,
            "readiness": self.readiness,
            "phase_adapter_material": self.composition.phase_bindings,
            "witness_anchor": self.anchor_binding,
            "trusted_clock": self.clock,
        }
        values.update(overrides)
        return subject.PhysicalFullMatrixV4MaterializationPreflightInputs(**values)

    def _prepare(
        self,
        *,
        now: datetime,
        inputs: subject.PhysicalFullMatrixV4MaterializationPreflightInputs | None = None,
        config: subject.PhysicalFullMatrixV4MaterializationPreflightConfig | None = None,
    ) -> subject.PhysicalFullMatrixV4MaterializationPreflight:
        self.clock.reset(now)
        with self.fixture._all_owner_clocks(now=now):
            return subject.prepare_physical_full_matrix_v4_materialization_preflight(
                config=self.config if config is None else config,
                inputs=self._inputs() if inputs is None else inputs,
            )

    def _require(
        self,
        value: subject.PhysicalFullMatrixV4MaterializationPreflight,
        *,
        now: datetime,
    ) -> subject.PhysicalFullMatrixV4MaterializationPreflight:
        self.clock.reset(now)
        with self.fixture._all_owner_clocks(now=now):
            return subject.require_prepared_physical_full_matrix_v4_materialization_preflight(
                value,
                config=self.config,
            )

    def _assert_no_effect_callback(self) -> None:
        self.assertEqual(0, self.journal.calls)
        self.assertEqual(0, self.resolver.calls)
        self.assertEqual(0, self.continuity.calls)
        self.assertEqual(0, self.anchor.calls)
        self.assertTrue(all(item.calls == 0 for item in self.phase_adapters.values()))

    def test_exact_gen2_eight_named_material_and_witness_interface_are_static_only(self) -> None:
        result = self._prepare(now=self.now)
        self.assertEqual(self.now, result.prepared_at)
        self.assertEqual(
            tuple(phase.name for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES),
            result.adapter_names,
        )
        self.assertIs(result.witness_anchor_identity, self.anchor_identity)
        self.assertFalse(result.materialization_authorized)
        self.assertFalse(result.host_provider_installation_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertEqual(1, self.clock.calls)
        self._assert_no_effect_callback()

    def test_require_resamples_root_clock_and_revalidates_not_cached_gen2_capability(self) -> None:
        result = self._prepare(now=self.now)
        later = self.now + timedelta(seconds=1)
        self.assertIs(result, self._require(result, now=later))
        self.assertEqual(self.now, result.prepared_at)
        self.assertEqual(2, self.clock.calls)
        self._assert_no_effect_callback()

    def test_expired_cached_readiness_cannot_pass_fresh_preflight(self) -> None:
        result = self._prepare(now=self.now)
        expired = self.now + timedelta(seconds=11)
        self.clock.reset(expired)
        with self.fixture._all_owner_clocks(now=expired), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4MaterializationPreflightError,
            "GEN2_READINESS_INVALID",
        ):
            subject.require_prepared_physical_full_matrix_v4_materialization_preflight(
                result,
                config=self.config,
            )
        self.assertEqual(2, self.clock.calls)
        self._assert_no_effect_callback()

    def test_naive_mismatched_and_regressing_clock_fail_closed(self) -> None:
        naive = self.now.replace(tzinfo=None)
        self.clock.reset(naive)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4MaterializationPreflightError,
            "TRUSTED_CLOCK_INVALID",
        ):
            subject.prepare_physical_full_matrix_v4_materialization_preflight(
                config=self.config,
                inputs=self._inputs(),
            )
        alternate_clock = _SequenceClock()
        alternate_clock.reset(self.now)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4MaterializationPreflightError,
            "TRUSTED_CLOCK_MISMATCH",
        ):
            subject.prepare_physical_full_matrix_v4_materialization_preflight(
                config=self.config,
                inputs=self._inputs(trusted_clock=alternate_clock),
            )
        result = self._prepare(now=self.now + timedelta(seconds=1))
        self.clock.reset(self.now)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4MaterializationPreflightError,
            "TRUSTED_CLOCK_REGRESSION",
        ):
            subject.require_prepared_physical_full_matrix_v4_materialization_preflight(
                result,
                config=self.config,
            )
        self._assert_no_effect_callback()

    def test_gen1_partial_duplicate_and_mismatched_adapter_material_are_rejected(self) -> None:
        historical = object.__new__(
            legacy_readiness.VerifiedPhysicalFullMatrixV2WitnessedCampaignReadiness
        )
        scenarios: list[tuple[str, subject.PhysicalFullMatrixV4MaterializationPreflightInputs, str]] = []
        scenarios.append(
            (
                "gen1",
                self._inputs(readiness=historical),
                "GEN2_READINESS_REQUIRED",
            )
        )
        partial = dict(self.composition.phase_bindings)
        partial.pop(next(iter(partial)))
        scenarios.append(
            (
                "partial",
                self._inputs(phase_adapter_material=partial),
                "PHASE_SET_INVALID",
            )
        )
        duplicate = dict(self.composition.phase_bindings)
        first, second = tuple(duplicate)[:2]
        duplicate[second] = replace(
            duplicate[second],
            phase_adapter=duplicate[first].phase_adapter,
        )
        scenarios.append(
            (
                "duplicate",
                self._inputs(phase_adapter_material=duplicate),
                "DUPLICATE_ADAPTER_MATERIAL",
            )
        )
        mismatched = dict(self.composition.phase_bindings)
        name = next(iter(mismatched))
        mismatched[name] = replace(mismatched[name])
        scenarios.append(
            (
                "mismatch",
                self._inputs(phase_adapter_material=mismatched),
                "PHASE_MATERIAL_MISMATCH",
            )
        )
        for label, inputs, expected in scenarios:
            with self.subTest(label=label):
                self.clock.reset(self.now)
                with self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
                    subject.PhysicalFullMatrixV4MaterializationPreflightError,
                    expected,
                ):
                    subject.prepare_physical_full_matrix_v4_materialization_preflight(
                        config=self.config,
                        inputs=inputs,
                    )
        self._assert_no_effect_callback()

    def test_witness_identity_interface_default_off_and_forged_capability_fail_closed(self) -> None:
        mismatched_identity = replace(
            self.anchor_identity,
            run_id=UUID("b1f9b6df-2e94-4b4d-9f8f-b167cff543bc"),
        )
        self.clock.reset(self.now)
        with self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4MaterializationPreflightError,
            "WITNESS_IDENTITY_MISMATCH",
        ):
            subject.prepare_physical_full_matrix_v4_materialization_preflight(
                config=self.config,
                inputs=self._inputs(
                    witness_anchor=replace(
                        self.anchor_binding,
                        identity=mismatched_identity,
                    )
                ),
            )
        self.clock.reset(self.now)
        with self.fixture._all_owner_clocks(now=self.now), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4MaterializationPreflightError,
            "WITNESS_INTERFACE_INVALID",
        ):
            subject.prepare_physical_full_matrix_v4_materialization_preflight(
                config=self.config,
                inputs=self._inputs(
                    witness_anchor=replace(self.anchor_binding, anchor=object())
                ),
            )
        self.clock.reset(self.now)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4MaterializationPreflightError,
            "PREFLIGHT_DISABLED",
        ):
            subject.prepare_physical_full_matrix_v4_materialization_preflight(
                config=subject.PhysicalFullMatrixV4MaterializationPreflightConfig(),
                inputs=self._inputs(),
            )
        forged = object.__new__(subject.PhysicalFullMatrixV4MaterializationPreflight)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4MaterializationPreflightError,
            "CAPABILITY_REQUIRED",
        ):
            subject.require_prepared_physical_full_matrix_v4_materialization_preflight(
                forged,
                config=self.config,
            )
        self._assert_no_effect_callback()

    def test_static_boundary_excludes_live_transport_and_runner(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertFalse(
            {
                "boto3",
                "botocore",
                "docker",
                "http",
                "paramiko",
                "requests",
                "socket",
                "subprocess",
                "urllib",
            }
            & {item.split(".")[0] for item in imports}
        )
        self.assertNotIn("core.physical_full_matrix_execution_driver", imports)
        self.assertNotIn("core.physical_full_matrix_execution_driver_v3", imports)
        self.assertNotIn("core.physical_full_matrix_receipt_journal", imports)
        self.assertNotIn("execute_next_physical_full_matrix_v4_phase", source)
        self.assertNotIn("run_physical_full_matrix_v4", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
