"""Adversarial tests for the root-gated, non-executing P6 source-FD seam."""

from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_v4_phase6_reverse_bundle_descriptor_binding as descriptor_subject
from core import physical_full_matrix_v4_phase6_source_fd_attestation as subject
from tests import test_physical_full_matrix_v4_phase6_failback_rebuild_admission as p6


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase6_source_fd_attestation.py"
)


class PhysicalFullMatrixV4Phase6SourceFdAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        os.chmod(self.workspace.name, 0o700)
        self.source_fd = os.open(self.workspace.name, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(self._close, self.source_fd)

        self.fixture = p6._Fixture()
        self.admission_inputs, admission_config = self.fixture.inputs_with_exact_p5_completion()
        self.admission = p6.subject.admit_physical_full_matrix_v4_phase6_failback_rebuild(
            config=admission_config,
            inputs=self.admission_inputs,
            now=self.fixture.now,
        )
        self.plan = self.admission_inputs.reverse_recovery_plan
        assert self.plan is not None
        self.descriptor_binding = self._descriptor_binding(self.source_fd)
        self.config = subject.PhysicalFullMatrixV4Phase6SourceFdAttestationConfig(
            expected_admission_sha256=self.admission.admission_sha256,
            expected_reverse_bundle_descriptor_binding_sha256=self.descriptor_binding.binding_sha256,
            enabled=True,
        )

    @staticmethod
    def _close(fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass

    def _descriptor_binding(self, fd: int):
        metadata = os.fstat(fd)
        payload = self.fixture.plan_payload
        versions_sha256 = hashlib.sha256(
            canonical_json_bytes(payload["object_versions"])
        ).hexdigest()
        values: dict[str, object] = {
            "schema": "gold-trade-physical-full-matrix-v4-phase6-injected-staged-descriptor-v1",
            "status": "injected-staged-descriptor-identity-evidence-only",
            "staged_recovery_fd": fd,
            "descriptor_device": metadata.st_dev,
            "descriptor_inode": metadata.st_ino,
            "descriptor_identity_sha256": "",
            "descriptor_kind": "staged-recovery-directory",
            "descriptor_access": "read-only",
            "source_site": "webapp_ir",
            "destination_site": "webapp_fi",
            "route_binding_sha256": self.admission.route_binding_sha256,
            "reverse_recovery_plan_sha256": self.admission.reverse_recovery_plan_sha256,
            "bundle_id": self.admission.bundle_id,
            "stage_receipt_sha256": self.admission.stage_receipt_sha256,
            "object_versions_sha256": versions_sha256,
            "recovery_bundle_binding_sha256": payload["recovery_bundle_binding_sha256"],
        }
        provisional = descriptor_subject.PhysicalFullMatrixV4Phase6InjectedStagedDescriptor(
            **values
        )
        values["descriptor_identity_sha256"] = hashlib.sha256(
            canonical_json_bytes(descriptor_subject._descriptor_identity_payload(provisional))
        ).hexdigest()
        injected = descriptor_subject.PhysicalFullMatrixV4Phase6InjectedStagedDescriptor(
            **values
        )
        binding_config = (
            descriptor_subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingConfig(
                expected_admission_sha256=self.admission.admission_sha256,
                expected_reverse_recovery_plan_sha256=self.admission.reverse_recovery_plan_sha256,
                expected_route_binding_sha256=self.admission.route_binding_sha256,
                enabled=True,
            )
        )
        return descriptor_subject.bind_physical_full_matrix_v4_phase6_reverse_bundle_descriptor(
            config=binding_config,
            inputs=descriptor_subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingInputs(
                admission=self.admission,
                reverse_recovery_plan=self.plan,
                injected_staged_descriptor=injected,
            ),
        )

    def _attest(self, **overrides: object):
        values: dict[str, object] = {
            "config": self.config,
            "inputs": subject.PhysicalFullMatrixV4Phase6SourceFdAttestationInputs(
                admission=self.admission,
                reverse_bundle_descriptor_binding=self.descriptor_binding,
                staged_recovery_fd=self.source_fd,
            ),
        }
        values.update(overrides)
        return subject.attest_physical_full_matrix_v4_phase6_source_fd(**values)

    def test_root_gated_attestation_cross_pins_provenance_and_duplicates_noninheritable_fd(self) -> None:
        result = self._attest()
        self.addCleanup(self._close, result.staged_recovery_fd)
        self.assertIs(
            result,
            subject.require_attested_physical_full_matrix_v4_phase6_source_fd(result),
        )
        self.assertNotEqual(self.source_fd, result.staged_recovery_fd)
        self.assertFalse(os.get_inheritable(result.staged_recovery_fd))
        self.assertEqual(os.fstat(self.source_fd).st_ino, os.fstat(result.staged_recovery_fd).st_ino)
        for name in (
            "source_descriptor_use_authorized",
            "fd_attester_authorized",
            "materialization_authorized",
            "runner_authorized",
            "promotion_authorized",
            "writer_authorized",
            "traffic_switch_authorized",
            "execution_authorized",
            "full_matrix_authorized",
            "full_matrix_executed",
        ):
            self.assertFalse(getattr(result, name))
        with self.assertRaises(TypeError):
            result.__reduce_ex__(4)

    def test_default_off_nonroot_inheritable_and_descriptor_substitution_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6SourceFdAttestationError, "CONFIG_INVALID"
        ):
            self._attest(config=replace(self.config, enabled=False))
        with patch.object(subject.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                subject.PhysicalFullMatrixV4Phase6SourceFdAttestationError, "ROOT_REQUIRED"
            ):
                self._attest()
        os.set_inheritable(self.source_fd, True)
        try:
            with self.assertRaisesRegex(
                subject.PhysicalFullMatrixV4Phase6SourceFdAttestationError, "DESCRIPTOR_UNSAFE"
            ):
                self._attest()
        finally:
            os.set_inheritable(self.source_fd, False)
        alternate = os.open(self.workspace.name, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(self._close, alternate)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6SourceFdAttestationError, "DESCRIPTOR_MISMATCH"
        ):
            self._attest(inputs=subject.PhysicalFullMatrixV4Phase6SourceFdAttestationInputs(
                admission=self.admission,
                reverse_bundle_descriptor_binding=self.descriptor_binding,
                staged_recovery_fd=alternate,
            ))

    def test_regular_fd_tampering_and_foreign_provenance_are_rejected(self) -> None:
        regular_path = Path(self.workspace.name) / "not-a-directory"
        regular_path.write_text("inert", encoding="ascii")
        regular_fd = os.open(regular_path, os.O_RDONLY)
        self.addCleanup(self._close, regular_fd)
        bad_binding = self._descriptor_binding(regular_fd)
        bad_config = replace(
            self.config,
            expected_reverse_bundle_descriptor_binding_sha256=bad_binding.binding_sha256,
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6SourceFdAttestationError, "DESCRIPTOR_UNSAFE"
        ):
            subject.attest_physical_full_matrix_v4_phase6_source_fd(
                config=bad_config,
                inputs=subject.PhysicalFullMatrixV4Phase6SourceFdAttestationInputs(
                    admission=self.admission,
                    reverse_bundle_descriptor_binding=bad_binding,
                    staged_recovery_fd=regular_fd,
                ),
            )
        result = self._attest()
        self.addCleanup(self._close, result.staged_recovery_fd)
        object.__setattr__(result, "bundle_id", "0" * 64)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase6SourceFdAttestationError, "TAMPERED"
        ):
            subject.require_attested_physical_full_matrix_v4_phase6_source_fd(result)

    def test_module_has_no_path_traversal_transport_or_legacy_runtime_imports(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "physical_wa_fi_postgres_failback",
            "physical_wa_ir_postgres_failback",
            "physical_postgres_standby_bootstrap_materialization",
            "physical_blob_receiver_exact_pull_staging",
            "subprocess",
            "paramiko",
            "os.open",
            "os.listdir",
            "open(",
            "pathlib",
            "socket",
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
        self.assertTrue({"pathlib", "socket", "subprocess", "docker", "paramiko"}.isdisjoint(imports))


if __name__ == "__main__":
    unittest.main()
