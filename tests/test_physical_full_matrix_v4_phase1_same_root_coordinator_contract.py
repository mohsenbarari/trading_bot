"""Tests for the quarantined P1 coordinator contract placeholder."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest

from core import physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint as checkpoint
from core import physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_admission as admission
from core import physical_full_matrix_v4_phase1_same_root_coordinator_contract as subject


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "physical_full_matrix_v4_phase1_same_root_coordinator_contract.py"


class PhysicalFullMatrixV4Phase1SameRootCoordinatorContractTests(unittest.TestCase):
    def setUp(self) -> None:
        checkpoint_config = checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig(enabled=True)
        admission_config = admission.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig(
            checkpoint_config=checkpoint_config, enabled=True,
        )
        self.config = subject.PhysicalFullMatrixV4Phase1SameRootCoordinatorContractConfig(
            same_root_admission_config=admission_config, enabled=True,
        )

    def test_public_preflight_rejects_without_a_real_pending_pair(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1SameRootCoordinatorContractError,
            "REQUEST_INVALID",
        ):
            subject.preflight_physical_full_matrix_v4_phase1_same_root_coordinator_contract(
                config=self.config,
                request=object(), checkpoint=object(), pending_gen2_commit=object(), admission=object(),
            )

    def test_config_cannot_claim_owner_installation_or_authority(self) -> None:
        for value in (
            replace(self.config, enabled=False),
            replace(self.config, atomic_owner_installed=True),
            replace(self.config, post_commit_reconciler_installed=True),
            replace(self.config, phase_completion_evidenced=True),
            replace(self.config, full_matrix_authorized=True),
        ):
            with self.assertRaisesRegex(
                subject.PhysicalFullMatrixV4Phase1SameRootCoordinatorContractError,
                "CONFIG_INVALID",
            ):
                subject.preflight_physical_full_matrix_v4_phase1_same_root_coordinator_contract(
                    config=value,
                    request=object(), checkpoint=object(), pending_gen2_commit=object(), admission=object(),
                )

    def test_require_rejects_every_publicly_constructible_value(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1SameRootCoordinatorContractError,
            "REQUEST_INVALID",
        ):
            subject.require_unavailable_physical_full_matrix_v4_phase1_same_root_coordinator_preflight(
                object(), config=self.config,
                request=object(), checkpoint=object(), pending_gen2_commit=object(), admission=object(),
            )

    def test_no_database_transaction_or_external_execution_imports_or_calls(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        forbidden_import_roots = {
            "sqlalchemy", "asyncio", "socket", "requests", "httpx", "subprocess",
            "os", "pathlib", "boto3", "docker",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], forbidden_import_roots)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], forbidden_import_roots)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, {"execute", "commit", "rollback", "flush", "connect", "request", "send"})


if __name__ == "__main__":
    unittest.main()
