"""Tests for the fail-closed, one-shot P4/P7 root boundary."""

from __future__ import annotations

import ast
import copy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest import mock
import unittest

from core import physical_full_matrix_v4_witness_successor_transition_runtime as subject
from tests import test_physical_full_matrix_v4_witness_successor_transition_evidence as _evidence_tests


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "physical_full_matrix_v4_witness_successor_transition_runtime.py"


class _Triplet:
    def __init__(self, evidence: tuple[bytes, bytes, bytes]) -> None:
        self.evidence = evidence
        self.calls: list[object] = []

    def execute_witness_successor_transition(self, request: object) -> bytes:
        self.calls.append(("executor", request))
        return self.evidence[0]

    def observe_witness_successor_transition(self, request: object, *, executor_receipt_sha256: str) -> bytes:
        self.calls.append(("observer", request, executor_receipt_sha256))
        return self.evidence[1]

    def admit_witness_successor_transition(self, request: object, *, executor_receipt_sha256: str, observer_receipt_sha256: str) -> bytes:
        self.calls.append(("witness", request, executor_receipt_sha256, observer_receipt_sha256))
        return self.evidence[2]


class _Poison:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"must not read {name}")


class WitnessSuccessorTransitionRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _evidence_tests._TransitionFixture("witness-promote-ir-v2")
        self.triplet = _Triplet(self.fixture.evidence())
        policy = subject.derive_physical_full_matrix_v4_witness_successor_transition_runtime_policy_sha256(
            verification_config=self.fixture.config
        )
        self.config = subject.PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeConfig(
            enabled=True, verification_config=self.fixture.config, runtime_policy_sha256=policy
        )

    def _build(self, *, config=None, triplet=None):
        with mock.patch.object(subject.os, "geteuid", return_value=0):
            return subject.build_physical_full_matrix_v4_witness_successor_transition_runtime(
                config=self.config if config is None else config,
                executor=self.triplet if triplet is None else triplet,
                observer=self.triplet if triplet is None else triplet,
                witness_admission=self.triplet if triplet is None else triplet,
            )

    def _execute(self, runtime):
        with mock.patch.object(subject.os, "geteuid", return_value=0):
            return subject.execute_physical_full_matrix_v4_witness_successor_transition_runtime(
                runtime=runtime, now=self.fixture.now
            )

    def test_root_gated_default_off_and_build_never_calls_a_seam(self) -> None:
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError, "RUNTIME_DISABLED"
        ):
            subject.build_physical_full_matrix_v4_witness_successor_transition_runtime(
                config=replace(self.config, enabled=False), executor=_Poison(), observer=_Poison(), witness_admission=_Poison()
            )
        with mock.patch.object(subject.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError, "ROOT_REQUIRED"
        ):
            subject.build_physical_full_matrix_v4_witness_successor_transition_runtime(
                config=self.config, executor=self.triplet, observer=self.triplet, witness_admission=self.triplet
            )
        runtime = self._build()
        self.assertEqual([], self.triplet.calls)
        self.assertFalse(runtime.promotion_authorized)
        self.assertFalse(runtime.next_phase_start_authorized)

    def test_exact_signed_evidence_is_collected_once_in_order_but_never_authorizes_transition(self) -> None:
        runtime = self._build()
        result = self._execute(runtime)
        self.assertEqual(["executor", "observer", "witness"], [call[0] for call in self.triplet.calls])
        self.assertTrue(all(call[1] is runtime.request for call in self.triplet.calls))
        self.assertIs(result, subject.require_physical_full_matrix_v4_witness_successor_transition_execution_observation(
            result, runtime=runtime, now=self.fixture.now
        ))
        self.assertEqual("p4-p7-successor-transition-evidence-verified-not-authorized", result.status)
        for name in (
            "writer_authorized", "promotion_authorized", "traffic_switch_authorized",
            "external_effect_authorized", "phase_completion_evidenced", "next_phase_start_authorized",
            "execution_authorized", "full_matrix_authorized",
        ):
            self.assertFalse(getattr(result, name), name)

    def test_attempt_is_consumed_before_ambiguous_failure_and_no_later_seam_is_called(self) -> None:
        class BrokenExecutor(_Triplet):
            def execute_witness_successor_transition(self, request: object) -> bytes:
                self.calls.append(("executor", request))
                raise RuntimeError("ambiguous transition result")

        broken = BrokenExecutor(self.fixture.evidence())
        runtime = self._build(triplet=broken)
        with self.assertRaisesRegex(RuntimeError, "ambiguous transition result"):
            self._execute(runtime)
        self.assertEqual(["executor"], [call[0] for call in broken.calls])
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError, "ATTEMPT_ALREADY_CONSUMED"):
            self._execute(runtime)

    def test_runtime_or_request_tampering_cannot_reach_a_later_seam(self) -> None:
        runtime = self._build()
        object.__setattr__(runtime.request, "promotion_authorized", True)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError, "RUNTIME_TAMPERED"
        ):
            self._execute(runtime)
        self.assertEqual([], self.triplet.calls)

        class MutatingExecutor(_Triplet):
            def execute_witness_successor_transition(self, request: object) -> bytes:
                self.calls.append(("executor", request))
                object.__setattr__(request, "next_phase_start_authorized", True)
                return self.evidence[0]

        mutating = MutatingExecutor(self.fixture.evidence())
        runtime = self._build(triplet=mutating)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError, "RUNTIME_TAMPERED"
        ):
            self._execute(runtime)
        self.assertEqual(["executor"], [call[0] for call in mutating.calls])

    def test_invalid_evidence_direct_control_and_authority_flags_fail_closed(self) -> None:
        bad = list(self.fixture.evidence())
        bad[1] += b" "
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError, "EVIDENCE_INVALID"):
            self._execute(self._build(triplet=_Triplet(tuple(bad))))
        for changed in (
            replace(self.config, runtime_policy_sha256="1" * 64),
            replace(self.config, direct_fi_to_ir_control="allowed"),
            replace(self.config, direct_ir_to_fi_control="allowed"),
            replace(self.config, object_storage_authority="allowed"),
            replace(self.config, promotion_authorized=True),
            replace(self.config, next_phase_start_authorized=True),
        ):
            with self.assertRaises(subject.PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError):
                self._build(config=changed)

    def test_observation_tampering_staleness_and_copy_fail_closed(self) -> None:
        runtime = self._build()
        result = self._execute(runtime)
        with self.assertRaises(TypeError):
            copy.copy(result)
        with self.assertRaises(TypeError):
            copy.deepcopy(result)
        object.__setattr__(result, "phase_completion_evidenced", True)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError, "OBSERVATION_INVALID"):
            subject.require_physical_full_matrix_v4_witness_successor_transition_execution_observation(
                result, runtime=runtime, now=self.fixture.now
            )
        clean_runtime = self._build(triplet=_Triplet(self.fixture.evidence()))
        clean = self._execute(clean_runtime)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError, "OBSERVATION_INVALID"):
            subject.require_physical_full_matrix_v4_witness_successor_transition_execution_observation(
                clean, runtime=clean_runtime, now=self.fixture.now + timedelta(seconds=91)
            )

    def test_module_has_no_host_network_or_legacy_runtime(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("physical_operational_failover_v1", source)
        self.assertNotIn("production_writer_lease", source)
        tree = ast.parse(source)
        imports = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imports.update(node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None)
        self.assertTrue({"subprocess", "socket", "requests", "paramiko", "urllib"}.isdisjoint(imports))
