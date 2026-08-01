"""Adversarial tests for the non-materializing Phase-6 source/target handoff."""

from __future__ import annotations

import ast
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import physical_full_matrix_v4_phase6_fd_only_rebuild_binder as target_subject
from core import physical_full_matrix_v4_phase6_reconstruction_handoff as subject
from tests import test_physical_full_matrix_v4_phase6_source_fd_attestation as source_fixture


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase6_reconstruction_handoff.py"
)


class PhysicalFullMatrixV4Phase6ReconstructionHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("the root-only handoff requires the root-owned CI container")
        self.source_fixture = source_fixture.PhysicalFullMatrixV4Phase6SourceFdAttestationTests()
        self.source_fixture.setUp()
        self.addCleanup(self.source_fixture.doCleanups)
        self.source_fd = self.source_fixture.source_fd
        self.admission = self.source_fixture.admission
        self.source_attestation = self.source_fixture._attest()
        self.addCleanup(self._close, self.source_attestation.staged_recovery_fd)
        self.target_workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.target_workspace.cleanup)
        self.target_dir = Path(self.target_workspace.name)
        os.chmod(self.target_dir, 0o700)
        self.target_fd = os.open(self.target_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.addCleanup(self._close, self.target_fd)
        self.target_binding = target_subject.bind_physical_full_matrix_v4_phase6_fd_only_rebuild_target(
            admission=self.admission, target_pgdata_fd=self.target_fd
        )
        self.addCleanup(self._close, self.target_binding.target_pgdata_fd)
        self.config = subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffConfig(
            expected_admission_sha256=self.admission.admission_sha256,
            expected_target_binding_sha256=self.target_binding.binding_sha256,
            expected_source_attestation_sha256=self.source_attestation.attestation_sha256,
            enabled=True,
        )

    @staticmethod
    def _close(fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass

    def _prepare(self, **overrides: object):
        values: dict[str, object] = {
            "config": self.config,
            "inputs": subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffInputs(
                admission=self.admission,
                target_binding=self.target_binding,
                source_attestation=self.source_attestation,
            ),
        }
        values.update(overrides)
        return subject.prepare_physical_full_matrix_v4_phase6_reconstruction_handoff(**values)

    def test_exact_admitted_source_and_target_become_noninheritable_evidence_only_handoff(self) -> None:
        result = self._prepare()
        self.addCleanup(self._close, result.source_staged_recovery_fd)
        self.addCleanup(self._close, result.target_pgdata_fd)
        self.assertIs(result, subject.require_prepared_physical_full_matrix_v4_phase6_reconstruction_handoff(result))
        self.assertNotEqual(result.source_staged_recovery_fd, self.source_attestation.staged_recovery_fd)
        self.assertNotEqual(result.target_pgdata_fd, self.target_binding.target_pgdata_fd)
        self.assertFalse(os.get_inheritable(result.source_staged_recovery_fd))
        self.assertFalse(os.get_inheritable(result.target_pgdata_fd))
        self.assertNotEqual(
            (os.fstat(result.source_staged_recovery_fd).st_dev, os.fstat(result.source_staged_recovery_fd).st_ino),
            (os.fstat(result.target_pgdata_fd).st_dev, os.fstat(result.target_pgdata_fd).st_ino),
        )
        for name in (
            "handoff_authorized", "materialization_authorized", "runner_authorized",
            "promotion_authorized", "writer_authorized", "traffic_switch_authorized",
            "execution_authorized", "full_matrix_authorized", "full_matrix_executed",
        ):
            self.assertFalse(getattr(result, name))
        with self.assertRaises(TypeError):
            result.__reduce_ex__(4)

    def test_default_off_nonroot_missing_or_mismatched_provenance_is_rejected(self) -> None:
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffError, "CONFIG_INVALID"):
            self._prepare(config=replace(self.config, enabled=False))
        with patch.object(subject.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffError, "ROOT_REQUIRED"):
                self._prepare()
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffError, "TARGET_REQUIRED"):
            self._prepare(inputs=subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffInputs(
                admission=self.admission, target_binding=object(), source_attestation=self.source_attestation
            ))
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffError, "PROVENANCE_MISMATCH"):
            self._prepare(config=replace(self.config, expected_source_attestation_sha256="f" * 64))

    def test_source_target_alias_and_tampering_are_rejected(self) -> None:
        alias_binding = target_subject.bind_physical_full_matrix_v4_phase6_fd_only_rebuild_target(
            admission=self.admission, target_pgdata_fd=self.source_fd
        )
        self.addCleanup(self._close, alias_binding.target_pgdata_fd)
        alias_config = replace(self.config, expected_target_binding_sha256=alias_binding.binding_sha256)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffError, "SOURCE_TARGET_ALIAS"):
            self._prepare(config=alias_config, inputs=subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffInputs(
                admission=self.admission, target_binding=alias_binding, source_attestation=self.source_attestation
            ))
        result = self._prepare()
        self.addCleanup(self._close, result.source_staged_recovery_fd)
        self.addCleanup(self._close, result.target_pgdata_fd)
        object.__setattr__(result, "bundle_id", "0" * 64)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffError, "TAMPERED"):
            subject.require_prepared_physical_full_matrix_v4_phase6_reconstruction_handoff(result)
        result = self._prepare()
        self.addCleanup(self._close, result.source_staged_recovery_fd)
        self.addCleanup(self._close, result.target_pgdata_fd)
        os.set_inheritable(result.source_staged_recovery_fd, True)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReconstructionHandoffError, "TAMPERED"):
            subject.require_prepared_physical_full_matrix_v4_phase6_reconstruction_handoff(result)

    def test_module_has_no_transport_storage_runner_or_legacy_runtime_imports(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "physical_wa_fi_postgres_failback", "physical_wa_ir_postgres_recovery",
            "physical_postgres_standby_bootstrap", "physical_blob_receiver_exact_pull_staging",
            "subprocess", "paramiko", "socket", "os.open", "os.listdir", "open(",
        ):
            self.assertNotIn(forbidden, source)
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        self.assertTrue({"pathlib", "socket", "subprocess", "docker", "paramiko"}.isdisjoint(imports))


if __name__ == "__main__":
    unittest.main()
