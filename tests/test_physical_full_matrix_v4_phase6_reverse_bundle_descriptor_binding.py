"""Adversarial tests for the pure P6 reverse-bundle descriptor provenance seam."""

from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
from pathlib import Path
import unittest

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_v4_phase6_reverse_bundle_descriptor_binding as subject
from tests import test_physical_full_matrix_v4_phase6_failback_rebuild_admission as p6


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase6_reverse_bundle_descriptor_binding.py"
)


class PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = p6._Fixture()
        self.inputs, self.admission_config = self.fixture.inputs_with_exact_p5_completion()
        self.admission = p6.subject.admit_physical_full_matrix_v4_phase6_failback_rebuild(
            config=self.admission_config, inputs=self.inputs, now=self.fixture.now
        )
        self.plan = self.inputs.reverse_recovery_plan
        assert self.plan is not None
        payload = self.fixture.plan_payload
        self.versions_sha256 = hashlib.sha256(
            canonical_json_bytes(payload["object_versions"])
        ).hexdigest()
        self.descriptor = self._descriptor()
        self.config = subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingConfig(
            expected_admission_sha256=self.admission.admission_sha256,
            expected_reverse_recovery_plan_sha256=self.admission.reverse_recovery_plan_sha256,
            expected_route_binding_sha256=self.admission.route_binding_sha256,
            enabled=True,
        )

    def _descriptor(self, **overrides: object) -> subject.PhysicalFullMatrixV4Phase6InjectedStagedDescriptor:
        values: dict[str, object] = {
            "schema": "gold-trade-physical-full-matrix-v4-phase6-injected-staged-descriptor-v1",
            "status": "injected-staged-descriptor-identity-evidence-only",
            "staged_recovery_fd": 47,
            "descriptor_device": 2049,
            "descriptor_inode": 77123,
            "descriptor_identity_sha256": "",
            "descriptor_kind": "staged-recovery-directory",
            "descriptor_access": "read-only",
            "source_site": "webapp_ir",
            "destination_site": "webapp_fi",
            "route_binding_sha256": self.admission.route_binding_sha256,
            "reverse_recovery_plan_sha256": self.admission.reverse_recovery_plan_sha256,
            "bundle_id": self.admission.bundle_id,
            "stage_receipt_sha256": self.admission.stage_receipt_sha256,
            "object_versions_sha256": self.versions_sha256,
            "recovery_bundle_binding_sha256": self.fixture.plan_payload[
                "recovery_bundle_binding_sha256"
            ],
        }
        values.update(overrides)
        provisional = subject.PhysicalFullMatrixV4Phase6InjectedStagedDescriptor(**values)
        if "descriptor_identity_sha256" not in overrides:
            values["descriptor_identity_sha256"] = hashlib.sha256(
                canonical_json_bytes(subject._descriptor_identity_payload(provisional))
            ).hexdigest()
        return subject.PhysicalFullMatrixV4Phase6InjectedStagedDescriptor(**values)

    def _bind(self, **overrides: object):
        values: dict[str, object] = {
            "config": self.config,
            "inputs": subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingInputs(
                admission=self.admission,
                reverse_recovery_plan=self.plan,
                injected_staged_descriptor=self.descriptor,
            ),
        }
        values.update(overrides)
        return subject.bind_physical_full_matrix_v4_phase6_reverse_bundle_descriptor(**values)

    def test_binds_exact_admission_plan_versions_route_and_descriptor_identity(self) -> None:
        result = self._bind()
        self.assertIs(
            result,
            subject.require_bound_physical_full_matrix_v4_phase6_reverse_bundle_descriptor(result),
        )
        self.assertEqual(self.versions_sha256, result.object_versions_sha256)
        self.assertEqual(self.descriptor.descriptor_identity_sha256, result.descriptor_identity_sha256)
        self.assertEqual(47, result.staged_recovery_fd)
        self.assertFalse(result.source_descriptor_use_authorized)
        self.assertFalse(result.materialization_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertFalse(result.full_matrix_authorized)
        self.assertFalse(result.full_matrix_executed)
        with self.assertRaises(TypeError):
            result.__reduce_ex__(4)

    def test_default_off_wrong_or_tampered_claims_are_rejected(self) -> None:
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError, "CONFIG_INVALID"):
            self._bind(config=replace(self.config, enabled=False))
        bad = self._descriptor(bundle_id="0" * 64)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError, "DESCRIPTOR_MISMATCH"):
            self._bind(inputs=replace(
                subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingInputs(
                    admission=self.admission, reverse_recovery_plan=self.plan, injected_staged_descriptor=self.descriptor
                ), injected_staged_descriptor=bad
            ))
        result = self._bind()
        object.__setattr__(result, "object_versions_sha256", "0" * 64)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError, "TAMPERED"):
            subject.require_bound_physical_full_matrix_v4_phase6_reverse_bundle_descriptor(result)

    def test_descriptor_identity_and_exact_object_version_list_cannot_be_substituted(self) -> None:
        bad_identity = self._descriptor(descriptor_identity_sha256="f" * 64)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError, "DESCRIPTOR_INVALID"):
            self._bind(inputs=replace(
                subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingInputs(
                    admission=self.admission, reverse_recovery_plan=self.plan, injected_staged_descriptor=self.descriptor
                ), injected_staged_descriptor=bad_identity
            ))
        payload = dict(self.fixture.plan_payload)
        payload["object_versions"] = [
            {"object_key": "physical-failback/alternate.tar.age", "version_id": "v6alternate00001"}
        ]
        raw = canonical_json_bytes(payload)
        substituted_plan = p6.subject.PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence(
            canonical_plan=raw, plan_sha256=hashlib.sha256(raw).hexdigest()
        )
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError, "PLAN_MISMATCH"):
            self._bind(inputs=replace(
                subject.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingInputs(
                    admission=self.admission, reverse_recovery_plan=self.plan, injected_staged_descriptor=self.descriptor
                ), reverse_recovery_plan=substituted_plan
            ))

    def test_module_has_no_descriptor_io_transport_or_legacy_runtime_imports(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "physical_wa_fi_postgres_failback",
            "physical_wa_ir_postgres_failback",
            "physical_ir_to_fi_object_storage_failback_preflight",
            "subprocess",
            "paramiko",
            "os.fstat",
            "os.open",
            "open(",
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
        self.assertTrue({"os", "pathlib", "socket", "subprocess", "docker", "paramiko"}.isdisjoint(imports))
