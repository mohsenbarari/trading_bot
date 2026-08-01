"""Tests for the quarantined, unavailable P1 same-root admission seam.

The module must never turn a local checkpoint/pending pair into evidence of a
database root transaction.  A real, reviewed transaction owner is required.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest

from core import physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint as checkpoint
from core import physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_admission as subject


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_admission.py"


class PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checkpoint_config = checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig(
            enabled=True
        )
        self.config = subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionConfig(
            checkpoint_config=self.checkpoint_config,
            enabled=True,
        )

    def test_live_admission_is_unconditionally_refused(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionError,
            "LIVE_ROOT_ENVELOPE_REQUIRED",
        ):
            subject.admit_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_transaction(
                config=self.config,
                request=object(),
                checkpoint=object(),
                pending_gen2_commit=object(),
            )

    def test_config_cannot_claim_a_transaction_or_authority(self) -> None:
        for value in (
            replace(self.config, enabled=False),
            replace(self.config, same_root_transaction_established=True),
            replace(self.config, phase_completion_evidenced=True),
            replace(self.config, full_matrix_authorized=True),
        ):
            with self.assertRaisesRegex(
                subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionError,
                "LIVE_ROOT_ENVELOPE_REQUIRED",
            ):
                subject.admit_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_transaction(
                    config=value,
                    request=object(), checkpoint=object(), pending_gen2_commit=object(),
                )

    def test_require_rejects_every_publicly_constructible_value(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootAdmissionError,
            "REQUEST_INVALID",
        ):
            subject.require_pending_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_admission(
                object(),
                config=self.config,
                request=object(), checkpoint=object(), pending_gen2_commit=object(),
            )

    def test_no_sql_session_or_external_execution_imports_or_calls(self) -> None:
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
                self.assertNotIn(node.func.attr, {"execute", "commit", "flush", "connect", "request", "send"})


if __name__ == "__main__":
    unittest.main()
