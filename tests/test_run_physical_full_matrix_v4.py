"""Safety tests for the V4 non-operational planning boundary."""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from uuid import UUID
import unittest
from unittest.mock import patch

from core import physical_full_matrix_execution_driver_v4 as driver
from core.physical_full_matrix_v2_gen2_witnessed_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS,
    PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness,
    VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness,
)
from scripts import run_physical_full_matrix_v4 as runner


NOW = datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "run_physical_full_matrix_v4.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _binding() -> driver.PhysicalFullMatrixV4ExecutionBinding:
    return driver.PhysicalFullMatrixV4ExecutionBinding(
        campaign_id="physical-full-matrix-v4-runner-20260731",
        release_sha="a" * 40,
        readiness_binding_sha256=_hash("readiness"),
        route_commitment_sha256=_hash("route"),
        four_role_binding_sha256=_hash("four-role"),
        writer_holder_site="webapp_fi",
        writer_epoch=7,
        writer_lease_id="writer-lease-v4-runner-000001",
        witnessed_term_proof_sha256=_hash("term"),
        source_site="webapp_fi",
        destination_site="webapp_ir",
        roundtrip_attestation_sha256=_hash("roundtrip"),
        roundtrip_configuration_sha256=_hash("configuration"),
        witness_transition_id="witness-transition-v4-runner-000001",
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


class _NeverCalledJournal:
    def __init__(self) -> None:
        self.calls = 0

    def _called(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        raise AssertionError("non-operational validation invoked a journal callback")

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
        raise AssertionError("non-operational validation invoked a resolver")


class _NeverCalledClock:
    def __init__(self) -> None:
        self.calls = 0

    def now_utc(self):
        self.calls += 1
        raise AssertionError("non-operational validation invoked a clock")


class _NeverCalledContinuity:
    def __init__(self) -> None:
        self.calls = 0

    def verify_campaign_continuity(self, **kwargs):
        del kwargs
        self.calls += 1
        raise AssertionError("non-operational validation invoked continuity")


class _NeverCalledPhaseAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute_phase(self, *, request):
        del request
        self.calls += 1
        raise AssertionError("non-operational validation invoked a phase adapter")


class RunPhysicalFullMatrixV4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = _binding()
        self.config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=self.binding,
            readiness=_readiness(self.binding),
            run_id=UUID("70a994a3-a50a-4f10-bdaf-c75278a0ea74"),
            enabled=True,
        )
        self.verifier = patch.object(
            driver,
            "require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
            side_effect=lambda item, *, now=None: item.report,
        )
        self.verifier.start()
        self.addCleanup(self.verifier.stop)

    def test_in_process_plan_and_adapter_validation_are_non_operational(self) -> None:
        plan = runner.plan_physical_full_matrix_v4_nonoperational(config=self.config)
        self.assertIs(driver.require_physical_full_matrix_v4_execution_plan(plan), plan)
        self.assertFalse(plan.materialization_authorized)
        self.assertFalse(plan.promotion_authorized)
        self.assertFalse(plan.execution_authorized)

        journal = _NeverCalledJournal()
        resolver = _NeverCalledResolver()
        clock = _NeverCalledClock()
        continuity = _NeverCalledContinuity()
        phase_adapters = {
            phase.name: _NeverCalledPhaseAdapter()
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        adapters = driver.PhysicalFullMatrixV4ExecutionAdapters(
            phase_adapters=phase_adapters,
            receipt_journal=journal,
            readiness_resolver=resolver,
            trusted_clock=clock,
            campaign_continuity_gate=continuity,
        )
        self.assertIs(
            runner.validate_physical_full_matrix_v4_nonoperational(
                config=self.config,
                plan=plan,
                adapters=adapters,
            ),
            plan,
        )
        self.assertEqual(0, journal.calls)
        self.assertEqual(0, resolver.calls)
        self.assertEqual(0, clock.calls)
        self.assertEqual(0, continuity.calls)
        self.assertTrue(all(item.calls == 0 for item in phase_adapters.values()))

    def test_plan_and_continuity_are_mutually_exclusive(self) -> None:
        plan = runner.plan_physical_full_matrix_v4_nonoperational(config=self.config)
        with self.assertRaisesRegex(
            runner.PhysicalFullMatrixV4NonOperationalRunnerError,
            "PLAN_CONTINUITY_AMBIGUOUS",
        ):
            runner.validate_physical_full_matrix_v4_nonoperational(
                config=self.config,
                plan=plan,
                continuity=object(),
            )

    def test_process_local_plan_is_cross_pinned_to_static_config(self) -> None:
        plan = runner.plan_physical_full_matrix_v4_nonoperational(config=self.config)
        mismatched_config = replace(
            self.config,
            run_id=UUID("71a994a3-a50a-4f10-bdaf-c75278a0ea74"),
        )
        with self.assertRaisesRegex(
            runner.PhysicalFullMatrixV4NonOperationalRunnerError,
            "PLAN_CONFIG_MISMATCH",
        ):
            runner.validate_physical_full_matrix_v4_nonoperational(
                config=mismatched_config,
                plan=plan,
            )

    def test_cli_is_default_off_and_cannot_accept_an_execution_mode(self) -> None:
        for argv, expected_mode in (([], "no-action"), (["--plan"], "plan"), (["--validate"], "validate")):
            with self.subTest(argv=argv):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, runner.main(argv))
                report = json.loads(output.getvalue())
                self.assertEqual(expected_mode, report["mode"])
                self.assertFalse(report["runner_enabled"])
                self.assertTrue(report["non_operational"])
                self.assertFalse(report["materialization_authorized"])
                self.assertFalse(report["promotion_authorized"])
                self.assertFalse(report["execution_authorized"])
                self.assertFalse(report["full_matrix_executed"])
                self.assertEqual("forbidden", report["direct_fi_to_ir_control"])
                self.assertEqual("forbidden", report["direct_ir_to_fi_control"])

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main(["--execute"])

    def test_static_boundary_excludes_legacy_peer_and_live_execution_surfaces(self) -> None:
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
        self.assertNotIn("core.physical_full_matrix_execution_driver_v4", imports)
        self.assertNotIn("core.physical_full_matrix_v4_receipt_journal", imports)
        self.assertNotIn("execute_next_physical_full_matrix_v4_phase", source)
        self.assertNotIn("fi-to-ir", source.lower())
        self.assertNotIn("ir-to-fi", source.lower())

        core_aliases = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "core"
            for alias in node.names
        }
        self.assertEqual(
            {
                "physical_full_matrix_execution_driver_v4",
                "physical_full_matrix_v4_plan_rehydration",
            },
            core_aliases,
        )
