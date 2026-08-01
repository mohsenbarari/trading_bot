"""Adversarial tests for the non-operational V4 root composition boundary."""

from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
from pathlib import Path
import pickle
from uuid import UUID
import unittest
from unittest.mock import patch

from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v4_root_composition as composition
from core.physical_full_matrix_v2_gen2_witnessed_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS,
    PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness,
    VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_root_composition.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _binding() -> driver.PhysicalFullMatrixV4ExecutionBinding:
    return driver.PhysicalFullMatrixV4ExecutionBinding(
        campaign_id="physical-full-matrix-v4-root-composition-20260801",
        release_sha="a" * 40,
        readiness_binding_sha256=_hash("readiness"),
        route_commitment_sha256=_hash("route"),
        four_role_binding_sha256=_hash("four-role"),
        writer_holder_site="webapp_fi",
        writer_epoch=7,
        writer_lease_id="writer-lease-v4-root-composition-000001",
        witnessed_term_proof_sha256=_hash("term"),
        source_site="webapp_fi",
        destination_site="webapp_ir",
        roundtrip_attestation_sha256=_hash("roundtrip"),
        roundtrip_configuration_sha256=_hash("configuration"),
        witness_transition_id="witness-transition-v4-root-composition-000001",
        witness_sequence=17,
    )


def _readiness(
    binding: driver.PhysicalFullMatrixV4ExecutionBinding,
) -> VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    return VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness(
        report=PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness(
            schema=PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
            status=(
                PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
            ),
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            binding_sha256=binding.readiness_binding_sha256,
            observed_slots=PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS,
            reason_codes=(),
        )
    )


class _NeverCalledPhaseAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute_phase(self, *, request):
        del request
        self.calls += 1
        raise AssertionError("root composition invoked a phase adapter")


class _NeverCalledJournal:
    def __init__(self) -> None:
        self.calls = 0

    def _called(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        raise AssertionError("root composition invoked a journal callback")

    read_receipts = _called
    claim_phase = _called
    mark_effect_started = _called
    project_effect_start_anchor_proof = _called
    project_predecessor_phase_completion_anchor_proof = _called
    append_started = _called


class _NeverCalledResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve_readiness(self, *, binding):
        del binding
        self.calls += 1
        raise AssertionError("root composition invoked a readiness resolver")


class _NeverCalledClock:
    def __init__(self) -> None:
        self.calls = 0

    def now_utc(self):
        self.calls += 1
        raise AssertionError("root composition invoked a trusted clock")


class _NeverCalledContinuity:
    def __init__(self) -> None:
        self.calls = 0

    def verify_campaign_continuity(self, **kwargs):
        del kwargs
        self.calls += 1
        raise AssertionError("root composition invoked a continuity gate")


class _NeverCalledPostEffectVerifier:
    def __init__(self, phase) -> None:
        self.phase_name = phase.name
        self.phase_sequence = phase.sequence
        self.oracle = phase.oracle
        self.transport_profile = phase.transport_profile
        self.calls = 0

    def require_post_effect_completion(self, **kwargs):
        del kwargs
        self.calls += 1
        raise AssertionError("root composition invoked a post-effect verifier")


class PhysicalFullMatrixV4RootCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = _binding()
        self.run_id = UUID("81a994a3-a50a-4f10-bdaf-c75278a0ea74")
        self.execution_config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=self.binding,
            readiness=_readiness(self.binding),
            run_id=self.run_id,
            enabled=True,
        )
        self.verifier = patch.object(
            driver,
            "require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
            side_effect=lambda item, *, now=None: item.report,
        )
        self.verifier.start()
        self.addCleanup(self.verifier.stop)
        self.plan = driver.build_physical_full_matrix_v4_execution_plan(
            config=self.execution_config
        )
        self.policy_sha256 = (
            composition.derive_physical_full_matrix_v4_root_composition_policy_sha256(
                binding=self.binding,
                run_id=self.run_id,
                maximum_oracle_age_seconds=self.execution_config.maximum_oracle_age_seconds,
            )
        )
        self.root_config = composition.PhysicalFullMatrixV4RootCompositionConfig(
            enabled=True,
            campaign_id=self.binding.campaign_id,
            release_sha=self.binding.release_sha,
            run_id=self.run_id,
            maximum_oracle_age_seconds=self.execution_config.maximum_oracle_age_seconds,
            policy_sha256=self.policy_sha256,
        )
        self.journal = _NeverCalledJournal()
        self.resolver = _NeverCalledResolver()
        self.clock = _NeverCalledClock()
        self.continuity = _NeverCalledContinuity()
        self.phase_objects = {
            phase.name: _NeverCalledPhaseAdapter()
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        self.phase_bindings = {
            phase.name: composition.PhysicalFullMatrixV4RootPhaseAdapterBinding(
                phase_name=phase.name,
                phase_sequence=phase.sequence,
                oracle=phase.oracle,
                transport_profile=phase.transport_profile,
                destructive=phase.destructive,
                campaign_id=self.binding.campaign_id,
                release_sha=self.binding.release_sha,
                policy_sha256=self.policy_sha256,
                phase_adapter=self.phase_objects[phase.name],
            )
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        self.verifier_objects = {
            phase.name: _NeverCalledPostEffectVerifier(phase)
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }

    def _build(self, **overrides):
        arguments = {
            "root_config": self.root_config,
            "execution_config": self.execution_config,
            "plan": self.plan,
            "phase_adapters": self.phase_bindings,
            "phase_post_effect_verifiers": self.verifier_objects,
            "receipt_journal": self.journal,
            "readiness_resolver": self.resolver,
            "trusted_clock": self.clock,
            "campaign_continuity_gate": self.continuity,
        }
        arguments.update(overrides)
        return composition.build_physical_full_matrix_v4_root_composition(**arguments)

    def test_builds_exact_non_operational_typed_composition_without_callbacks(self) -> None:
        result = self._build()
        self.assertIs(composition.require_physical_full_matrix_v4_root_composition(result), result)
        self.assertIs(result.plan, self.plan)
        self.assertEqual(
            tuple(phase.name for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES),
            tuple(result.phase_bindings),
        )
        self.assertEqual(
            set(phase.name for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES),
            set(result.execution_adapters.phase_adapters or {}),
        )
        self.assertEqual(
            set(phase.name for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES),
            set(result.execution_adapters.phase_post_effect_verifiers or {}),
        )
        self.assertFalse(result.materialization_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertFalse(result.full_matrix_executed)
        self.assertEqual("forbidden", result.direct_fi_to_ir_control)
        self.assertEqual("forbidden", result.direct_ir_to_fi_control)
        self.assertEqual("forbidden", result.object_storage_authority)
        self.assertEqual("future-callback-carrier-only", result.object_storage_role)
        self.assertEqual("forbidden", result.controller_key_or_journal_cross_site_copy)
        self.assertEqual(0, self.journal.calls)
        self.assertEqual(0, self.resolver.calls)
        self.assertEqual(0, self.clock.calls)
        self.assertEqual(0, self.continuity.calls)
        self.assertTrue(all(item.calls == 0 for item in self.phase_objects.values()))
        self.assertTrue(all(item.calls == 0 for item in self.verifier_objects.values()))
        with self.assertRaises(TypeError):
            result.phase_bindings["unexpected"] = object()  # type: ignore[index]
        with self.assertRaises(TypeError):
            (result.execution_adapters.phase_adapters or {})["unexpected"] = object()  # type: ignore[index]
        with self.assertRaises(TypeError):
            (result.execution_adapters.phase_post_effect_verifiers or {})["unexpected"] = object()  # type: ignore[index]

    def test_default_off_and_non_root_runtime_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "COMPOSITION_DISABLED",
        ):
            self._build(root_config=replace(self.root_config, enabled=False))
        with patch.object(composition.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                composition.PhysicalFullMatrixV4RootCompositionError,
                "ROOT_RUNTIME_REQUIRED",
            ):
                self._build()

    def test_phase_set_name_catalog_and_adapter_substitution_fail_closed(self) -> None:
        omitted = dict(self.phase_bindings)
        omitted.pop(next(iter(omitted)))
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "PHASE_ADAPTER_SET_INVALID",
        ):
            self._build(phase_adapters=omitted)

        extra = dict(self.phase_bindings)
        extra["legacy-direct-fi-ir-runner"] = next(iter(extra.values()))
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "PHASE_ADAPTER_SET_INVALID",
        ):
            self._build(phase_adapters=extra)

        name = driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0].name
        substituted = dict(self.phase_bindings)
        substituted[name] = replace(substituted[name], phase_sequence=999)
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "PHASE_ADAPTER_SUBSTITUTION_REJECTED",
        ):
            self._build(phase_adapters=substituted)

        shared_adapter = _NeverCalledPhaseAdapter()
        duplicated = {
            phase.name: replace(self.phase_bindings[phase.name], phase_adapter=shared_adapter)
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "PHASE_ADAPTER_SUBSTITUTION_REJECTED",
        ):
            self._build(phase_adapters=duplicated)

    def test_verifier_set_binding_and_aliasing_fail_closed(self) -> None:
        omitted = dict(self.verifier_objects)
        omitted.pop(next(iter(omitted)))
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "VERIFIER_SET_INVALID",
        ):
            self._build(phase_post_effect_verifiers=omitted)

        phase = driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0]
        mismatch = dict(self.verifier_objects)
        mismatch[phase.name] = _NeverCalledPostEffectVerifier(
            type("Phase", (), {"name": phase.name, "sequence": 999, "oracle": phase.oracle, "transport_profile": phase.transport_profile})()
        )
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "VERIFIER_BINDING_MISMATCH",
        ):
            self._build(phase_post_effect_verifiers=mismatch)

        shared = _NeverCalledPostEffectVerifier(phase)
        aliased = dict(self.verifier_objects)
        for item in driver.PHYSICAL_FULL_MATRIX_V4_PHASES:
            aliased[item.name] = shared
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "VERIFIER_(BINDING_MISMATCH|ALIAS)",
        ):
            self._build(phase_post_effect_verifiers=aliased)

    def test_phase_five_compatibility_aliases_and_partial_v2r_relabel_are_rejected(self) -> None:
        """The current V4 catalog cannot be repurposed as the V2R ABI.

        A future reverse strict-ACK catalog requires a fresh generation; it
        cannot smuggle either an old V3 name or a V2R name/oracle/profile
        through the existing V4 composition and policy digest.
        """

        phase_five = next(
            phase
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
            if phase.sequence == 5
        )
        current = self.phase_bindings[phase_five.name]
        for alias in (
            "ir-writer-v2-strict-ack-matrix",
            "ir-writer-v2r-witness-roundtrip-strict-ack-matrix",
        ):
            with self.subTest(alias=alias):
                aliases = dict(self.phase_bindings)
                aliases.pop(phase_five.name)
                aliases[alias] = replace(current, phase_name=alias)
                with self.assertRaisesRegex(
                    composition.PhysicalFullMatrixV4RootCompositionError,
                    "PHASE_ADAPTER_SET_INVALID",
                ):
                    self._build(phase_adapters=aliases)

        for field_name, candidate in (
            (
                "oracle",
                "ir-writer-v2r-witness-roundtrip-strict-ack-oracle-v1",
            ),
            (
                "transport_profile",
                "ir-v2r-witness-roundtrip-strict-ack-v1",
            ),
        ):
            with self.subTest(field_name=field_name):
                relabelled = dict(self.phase_bindings)
                relabelled[phase_five.name] = replace(current, **{field_name: candidate})
                with self.assertRaisesRegex(
                    composition.PhysicalFullMatrixV4RootCompositionError,
                    "PHASE_ADAPTER_SUBSTITUTION_REJECTED",
                ):
                    self._build(phase_adapters=relabelled)

    def test_campaign_release_policy_and_plan_configuration_drift_are_rejected(self) -> None:
        for root_config, expected in (
            (replace(self.root_config, campaign_id="wrong-campaign-000001"), "CONFIGURATION_DRIFT"),
            (replace(self.root_config, release_sha="b" * 40), "CONFIGURATION_DRIFT"),
            (replace(self.root_config, policy_sha256=_hash("wrong-policy")), "POLICY_PIN_MISMATCH"),
            (
                replace(
                    self.root_config,
                    maximum_oracle_age_seconds=(
                        self.execution_config.maximum_oracle_age_seconds + 1
                    ),
                ),
                "CONFIGURATION_DRIFT",
            ),
        ):
            with self.subTest(root_config=root_config):
                with self.assertRaisesRegex(
                    composition.PhysicalFullMatrixV4RootCompositionError,
                    expected,
                ):
                    self._build(root_config=root_config)

        changed_execution_config = replace(
            self.execution_config,
            maximum_oracle_age_seconds=(
                self.execution_config.maximum_oracle_age_seconds + 1
            ),
        )
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "CONFIGURATION_DRIFT",
        ):
            self._build(execution_config=changed_execution_config)

    def test_legacy_direct_control_storage_authority_and_cross_site_copy_are_rejected(self) -> None:
        candidates = (
            replace(self.root_config, legacy_runner_artifacts=("legacy-v3",)),
            replace(self.root_config, legacy_runner_compatibility="enabled"),
            replace(self.root_config, direct_fi_to_ir_control="enabled"),
            replace(self.root_config, direct_ir_to_fi_control="enabled"),
            replace(self.root_config, object_storage_authority="writer-election"),
            replace(self.root_config, object_storage_role="authority"),
            replace(self.root_config, controller_key_or_journal_cross_site_copy="enabled"),
        )
        for root_config in candidates:
            with self.subTest(root_config=root_config):
                with self.assertRaises(composition.PhysicalFullMatrixV4RootCompositionError):
                    self._build(root_config=root_config)

        name = driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0].name
        direct_phase = dict(self.phase_bindings)
        direct_phase[name] = replace(
            direct_phase[name],
            direct_fi_to_ir_control="ssh",
        )
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "PHASE_CONTROL_FORBIDDEN",
        ):
            self._build(phase_adapters=direct_phase)

    def test_required_control_seams_are_present_but_never_called(self) -> None:
        for argument, value, expected in (
            ("receipt_journal", object(), "RECEIPT_JOURNAL_MISSING"),
            ("readiness_resolver", object(), "READINESS_RESOLVER_MISSING"),
            ("trusted_clock", object(), "TRUSTED_CLOCK_MISSING"),
            ("campaign_continuity_gate", object(), "CONTINUITY_GATE_MISSING"),
        ):
            with self.subTest(argument=argument):
                with self.assertRaisesRegex(
                    composition.PhysicalFullMatrixV4RootCompositionError,
                    expected,
                ):
                    self._build(**{argument: value})
        self.assertEqual(0, self.journal.calls)
        self.assertEqual(0, self.resolver.calls)
        self.assertEqual(0, self.clock.calls)
        self.assertEqual(0, self.continuity.calls)
        self.assertTrue(all(item.calls == 0 for item in self.phase_objects.values()))

    def test_process_local_objects_cannot_be_serialized_or_forged(self) -> None:
        result = self._build()
        for value in (self.root_config, next(iter(self.phase_bindings.values())), result):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
                    pickle.dumps(value)

        forged = composition.PhysicalFullMatrixV4RootComposition(
            schema=composition.PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_SCHEMA,
            plan=self.plan,
            execution_adapters=result.execution_adapters,
            phase_bindings=result.phase_bindings,
            campaign_id=self.binding.campaign_id,
            release_sha=self.binding.release_sha,
            run_id=self.run_id,
            plan_sha256=self.plan.plan_sha256,
            policy_sha256=self.policy_sha256,
            maximum_oracle_age_seconds=self.execution_config.maximum_oracle_age_seconds,
        )
        with self.assertRaisesRegex(
            composition.PhysicalFullMatrixV4RootCompositionError,
            "COMPOSITION_INVALID",
        ):
            composition.require_physical_full_matrix_v4_root_composition(forged)

    def test_static_boundary_excludes_live_control_and_legacy_surfaces(self) -> None:
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
        self.assertNotIn("core.physical_full_matrix_v2_witness_roundtrip_full_bundle_issuer", imports)
        self.assertNotIn("core.physical_wal_v2_witness_roundtrip_full_bundle_issuer", imports)
        self.assertNotIn("execute_next_physical_full_matrix_v4_phase", source)
        self.assertNotIn("run_physical_full_matrix_v4", source)


if __name__ == "__main__":
    unittest.main()
