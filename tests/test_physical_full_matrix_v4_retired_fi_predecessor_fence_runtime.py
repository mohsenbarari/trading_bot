"""Tests for the fail-closed, one-shot P2 root execution boundary."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest import mock
import unittest

from tests import test_physical_full_matrix_v4_retired_fi_predecessor_fence as _fence_tests
from core import physical_full_matrix_v4_retired_fi_predecessor_fence_runtime as subject


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_retired_fi_predecessor_fence_runtime.py"
)


class _Triplet:
    def __init__(self, evidence: tuple[bytes, bytes, bytes]) -> None:
        self.evidence = evidence
        self.calls: list[object] = []

    def execute_retired_fi_predecessor_fence(self, request: object) -> bytes:
        self.calls.append(("executor", request))
        return self.evidence[0]

    def observe_retired_fi_predecessor_fence(
        self, request: object, *, executor_receipt_sha256: str
    ) -> bytes:
        self.calls.append(("observer", request, executor_receipt_sha256))
        return self.evidence[1]

    def admit_retired_fi_predecessor_fence(
        self,
        request: object,
        *,
        executor_receipt_sha256: str,
        observer_receipt_sha256: str,
    ) -> bytes:
        self.calls.append(
            ("witness", request, executor_receipt_sha256, observer_receipt_sha256)
        )
        return self.evidence[2]


class _Poison:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"must not read {name}")


class RetiredFiPredecessorFenceRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = _fence_tests.RetiredFiPredecessorFenceTests("runTest")
        fixture.setUp()
        self.fixture = fixture
        self.triplet = _Triplet(fixture._evidence())
        self.policy = (
            subject.derive_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime_policy_sha256(
                verification_config=fixture.config
            )
        )
        self.config = subject.PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeConfig(
            enabled=True,
            verification_config=fixture.config,
            runtime_policy_sha256=self.policy,
        )

    def _build(self, *, config=None, triplet=None):
        value = self.config if config is None else config
        seams = self.triplet if triplet is None else triplet
        with mock.patch.object(subject.os, "geteuid", return_value=0):
            return subject.build_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime(
                config=value,
                executor=seams,
                observer=seams,
                witness_admission=seams,
            )

    def _execute(self, runtime):
        with mock.patch.object(subject.os, "geteuid", return_value=0):
            return subject.execute_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime(
                runtime=runtime,
                now=self.fixture.now,
            )

    def test_build_is_root_gated_default_off_and_never_calls_a_seam(self) -> None:
        disabled = replace(self.config, enabled=False)
        with mock.patch.object(subject.os, "geteuid", return_value=0), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError,
            "RUNTIME_DISABLED",
        ):
            subject.build_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime(
                config=disabled,
                executor=_Poison(),
                observer=_Poison(),
                witness_admission=_Poison(),
            )
        with mock.patch.object(subject.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError,
            "ROOT_REQUIRED",
        ):
            subject.build_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime(
                config=self.config,
                executor=self.triplet,
                observer=self.triplet,
                witness_admission=self.triplet,
            )
        runtime = self._build()
        self.assertEqual([], self.triplet.calls)
        self.assertFalse(runtime.execution_authorized)
        self.assertFalse(runtime.full_matrix_authorized)

    def test_enabled_runtime_invokes_the_three_injected_seams_once_in_order(self) -> None:
        runtime = self._build()
        result = self._execute(runtime)
        self.assertEqual(["executor", "observer", "witness"], [item[0] for item in self.triplet.calls])
        self.assertIs(runtime.request, self.triplet.calls[0][1])
        self.assertIs(runtime.request, self.triplet.calls[1][1])
        self.assertIs(runtime.request, self.triplet.calls[2][1])
        self.assertEqual(result.status, "p2-retired-fi-evidence-verified-not-authorized")
        self.assertIs(
            result,
            subject.require_physical_full_matrix_v4_retired_fi_predecessor_fence_execution_observation(
                result, runtime=runtime, now=self.fixture.now
            ),
        )
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.traffic_switch_authorized)
        self.assertFalse(result.external_effect_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertFalse(result.full_matrix_authorized)

    def test_attempt_is_consumed_before_an_ambiguous_or_invalid_seam_result(self) -> None:
        class BrokenExecutor(_Triplet):
            def execute_retired_fi_predecessor_fence(self, request: object) -> bytes:
                self.calls.append(("executor", request))
                raise RuntimeError("ambiguous local result")

        broken = BrokenExecutor(self.fixture._evidence())
        runtime = self._build(triplet=broken)
        with self.assertRaisesRegex(RuntimeError, "ambiguous local result"):
            self._execute(runtime)
        self.assertEqual(["executor"], [item[0] for item in broken.calls])
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError,
            "ATTEMPT_ALREADY_CONSUMED",
        ):
            self._execute(runtime)

    def test_invalid_evidence_fails_closed_and_does_not_make_a_result(self) -> None:
        evidence = list(self.fixture._evidence())
        evidence[1] += b" "
        runtime = self._build(triplet=_Triplet(tuple(evidence)))
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError,
            "EVIDENCE_INVALID",
        ):
            self._execute(runtime)

    def test_policy_is_exact_and_direct_cross_site_or_authority_flags_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError,
            "POLICY_MISMATCH",
        ):
            self._build(config=replace(self.config, runtime_policy_sha256="1" * 64))
        for changed in (
            replace(self.config, direct_fi_to_ir_control="allowed"),
            replace(self.config, direct_ir_to_fi_control="allowed"),
            replace(self.config, object_storage_authority="allowed"),
            replace(self.config, execution_authorized=True),
        ):
            with self.assertRaises(subject.PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError):
                self._build(config=changed)

    def test_result_tampering_or_staleness_fails_closed(self) -> None:
        runtime = self._build()
        result = self._execute(runtime)
        object.__setattr__(result, "execution_authorized", True)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError,
            "OBSERVATION_INVALID",
        ):
            subject.require_physical_full_matrix_v4_retired_fi_predecessor_fence_execution_observation(
                result, runtime=runtime, now=self.fixture.now
            )
        clean_runtime = self._build(triplet=_Triplet(self.fixture._evidence()))
        clean = self._execute(clean_runtime)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError,
            "OBSERVATION_INVALID",
        ):
            subject.require_physical_full_matrix_v4_retired_fi_predecessor_fence_execution_observation(
                clean,
                runtime=clean_runtime,
                now=self.fixture.now + timedelta(seconds=91),
            )

    def test_module_has_no_live_operator_or_legacy_path(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("physical_operational_failover_v1", source)
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
        self.assertTrue({"subprocess", "socket", "requests", "paramiko", "urllib"}.isdisjoint(imports))
