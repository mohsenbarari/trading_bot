"""Adversarial tests for the V4 Phase-6 FD-only target binder."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from core import physical_full_matrix_v4_phase6_fd_only_rebuild_binder as subject
from tests import test_physical_full_matrix_v4_phase6_failback_rebuild_admission as p6


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase6_fd_only_rebuild_binder.py"
)


class PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderTests(unittest.TestCase):
    def setUp(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("the root-only FD binder requires the root-owned CI container")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "pgdata"
        self.target.mkdir(mode=0o700)
        os.chmod(self.target, 0o700)
        self.fd = os.open(self.target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.fixture = p6._Fixture()
        self.inputs, self.config = self.fixture.inputs_with_exact_p5_completion()
        self.admission = p6.subject.admit_physical_full_matrix_v4_phase6_failback_rebuild(
            config=self.config, inputs=self.inputs, now=self.fixture.now
        )

    def tearDown(self) -> None:
        if getattr(self, "fd", -1) >= 0:
            os.close(self.fd)
        self.temp.cleanup()

    def _bind(self, **overrides: object):
        values: dict[str, object] = {
            "admission": self.admission,
            "target_pgdata_fd": self.fd,
        }
        values.update(overrides)
        return subject.bind_physical_full_matrix_v4_phase6_fd_only_rebuild_target(**values)

    def test_only_a_root_owned_empty_directory_is_duplicated_noninheritable(self) -> None:
        result = self._bind()
        self.addCleanup(os.close, result.target_pgdata_fd)
        self.assertEqual(
            subject.PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDER_SCHEMA,
            result.schema,
        )
        self.assertEqual(self.admission.admission_sha256, result.admission_sha256)
        self.assertEqual(self.admission.plan_sha256, result.plan_sha256)
        self.assertNotEqual(self.fd, result.target_pgdata_fd)
        self.assertFalse(os.get_inheritable(result.target_pgdata_fd))
        original = os.fstat(self.fd)
        duplicate = os.fstat(result.target_pgdata_fd)
        self.assertEqual((original.st_dev, original.st_ino), (duplicate.st_dev, duplicate.st_ino))
        self.assertTrue(stat.S_ISDIR(duplicate.st_mode))
        self.assertIs(
            result,
            subject.require_bound_physical_full_matrix_v4_phase6_fd_only_rebuild_target(result),
        )
        self.assertFalse(result.fd_binder_authorized)
        self.assertFalse(result.runner_authorized)
        self.assertFalse(result.materialization_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.traffic_switch_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertFalse(result.full_matrix_authorized)
        self.assertFalse(result.full_matrix_executed)

    def test_rejects_forged_admission_and_nonempty_or_aliased_descriptor(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderError, "ADMISSION_REQUIRED"
        ):
            self._bind(admission=object())
        child = self.target / "unexpected"
        child.write_bytes(b"x")
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderError, "TARGET_UNSAFE"
        ):
            self._bind()
        child.unlink()
        with patch.object(os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                subject.PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderError, "ROOT_REQUIRED"
            ):
                self._bind()

    def test_bound_result_is_process_local_nonserializable_and_tamper_detected(self) -> None:
        result = self._bind()
        self.addCleanup(os.close, result.target_pgdata_fd)
        with self.assertRaises(TypeError):
            result.__reduce_ex__(4)
        object.__setattr__(result, "plan_sha256", "0" * 64)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderError, "TAMPERED"
        ):
            subject.require_bound_physical_full_matrix_v4_phase6_fd_only_rebuild_target(result)

    def test_module_is_fd_only_and_has_no_runtime_or_transport_imports(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "physical_wa_fi_postgres_failback",
            "physical_ir_to_fi_object_storage_failback_preflight",
            "physical_operational_failover_v1",
            "physical_postgres_promotion_coordinator",
            "subprocess",
            "paramiko",
        ):
            self.assertNotIn(forbidden, source)
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
        self.assertTrue({"pathlib", "subprocess", "socket", "docker", "paramiko"}.isdisjoint(imports))
        self.assertNotIn("os.open", source)
        self.assertNotIn("os.connect", source)
