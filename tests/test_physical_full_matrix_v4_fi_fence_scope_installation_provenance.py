"""Adversarial tests for the pure Phase-2 FI fence scope provenance seam."""

from __future__ import annotations

import ast
import base64
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from uuid import UUID
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v4_fi_fence_scope_installation_provenance as subject
from core import physical_full_matrix_v4_retired_fi_predecessor_fence as p2


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_fi_fence_scope_installation_provenance.py"
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class FiFenceScopeInstallationProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        self.executor_key = Ed25519PrivateKey.generate()
        self.observer_key = Ed25519PrivateKey.generate()
        self.binding = driver.PhysicalFullMatrixV4ExecutionBinding(
            campaign_id="matrix-p2-fi-fence-scope-2026",
            release_sha="2" * 40,
            readiness_binding_sha256=_sha("readiness"),
            route_commitment_sha256=_sha("route"),
            four_role_binding_sha256=_sha("four-role"),
            writer_holder_site="webapp_fi",
            writer_epoch=21,
            writer_lease_id="fi-writer-lease-21",
            witnessed_term_proof_sha256=_sha("term-proof"),
            source_site="webapp_fi",
            destination_site="webapp_ir",
            roundtrip_attestation_sha256=_sha("roundtrip-attestation"),
            roundtrip_configuration_sha256=_sha("roundtrip-config"),
            witness_transition_id="witness-transition-fi-00021",
            witness_sequence=41,
        )
        effect_start = driver.PhysicalFullMatrixV4EffectStart(
            run_id=UUID("d44842e0-44e8-4e0a-9a92-4e7f6c6e4e91"),
            plan_sha256=_sha("v4-plan"),
            sequence=driver.PHYSICAL_FULL_MATRIX_V4_PHASES[1].sequence,
            phase_request_sha256=_sha("phase-2-request"),
            effect_key=_sha("phase-2-effect"),
            claim_id="phase-2-fi-fence-scope-claim-000001",
        )
        self.effect_start = p2.PhysicalFullMatrixV4EffectStartPin(
            run_id=effect_start.run_id,
            plan_sha256=effect_start.plan_sha256,
            phase=driver.PHYSICAL_FULL_MATRIX_V4_PHASES[1],
            effect_key=effect_start.effect_key,
            phase_request_sha256=effect_start.phase_request_sha256,
            binding=self.binding,
            claim_id=effect_start.claim_id,
            journaled_effect_start_identity_sha256=(
                driver.derive_physical_full_matrix_v4_effect_start_identity_sha256(
                    effect_start
                )
            ),
        )
        self.anchor = p2.PhysicalFullMatrixV4EffectStartAnchorPin(
            schema=driver.PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA,
            run_id=self.effect_start.run_id,
            plan_sha256=self.effect_start.plan_sha256,
            phase=self.effect_start.phase,
            effect_key=self.effect_start.effect_key,
            phase_request_sha256=self.effect_start.phase_request_sha256,
            binding=self.binding,
            claim_id=self.effect_start.claim_id,
            journaled_effect_start_identity_sha256=(
                self.effect_start.journaled_effect_start_identity_sha256
            ),
            journal_binding_sha256=_sha("journal-binding"),
            baseline_plan_binding_sha256=_sha("baseline-plan-binding"),
            anchor_genesis_sequence=7,
            anchor_genesis_head_sha256=_sha("anchor-genesis"),
            anchor_previous_sequence=12,
            anchor_previous_head_sha256=_sha("anchor-previous"),
            anchor_sequence=13,
            anchor_head_sha256=_sha("anchor-current"),
            anchor_commitment_sha256=_sha("anchor-commitment"),
            anchor_attestation_sha256=_sha("anchor-attestation"),
            anchor_local_previous_record_sha256=_sha("anchor-local-previous"),
            anchor_local_event_sha256=_sha("anchor-local-event"),
            anchor_occurred_at=self.now - timedelta(seconds=1),
        )
        self.term = p2.RetiredFiPredecessorFenceTermPin(
            holder_site="webapp_fi",
            writer_epoch=self.binding.writer_epoch,
            writer_lease_id=self.binding.writer_lease_id,
            witness_transition_id=self.binding.witness_transition_id,
            witnessed_term_proof_sha256=self.binding.witnessed_term_proof_sha256,
        )
        self.config = subject.PhysicalFullMatrixV4FiFenceScopeInstallationVerificationConfig(
            expected_effect_start=self.effect_start,
            expected_effect_start_anchor=self.anchor,
            expected_predecessor_term=self.term,
            executor_signer_public_key=_public(self.executor_key),
            observer_signer_public_key=_public(self.observer_key),
            enabled=True,
        )

    def _facts(self):
        return subject._p2_facts(self.config)

    @staticmethod
    def _signed(
        body: dict[str, object], *, key: Ed25519PrivateKey, domain: bytes
    ) -> bytes:
        signature = key.sign(domain + canonical_json_bytes(body))
        return canonical_json_bytes(
            {**body, "signature": base64.b64encode(signature).decode("ascii")}
        )

    def _scope_body(self, role: str) -> dict[str, object]:
        facts = self._facts()
        return {
            "schema": subject.PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_POLICY_SCHEMA,
            "version": 1,
            "status": subject._SCOPE_STATUS,
            "scope_mode": subject._SCOPE_MODE,
            "signer_role": role,
            "signer_key_id": subject._key_id(
                facts.executor_public_key
                if role == subject._EXECUTOR_ROLE
                else facts.observer_public_key
            ),
            "p2_binding_sha256": facts.p2_binding_sha256,
            "phase2_effect_start": copy.deepcopy(facts.effect_start_mapping),
            "phase2_effect_start_anchor": copy.deepcopy(facts.effect_start_anchor_mapping),
            "predecessor_term": copy.deepcopy(facts.predecessor_term_mapping),
            "mandatory_coverage": copy.deepcopy(subject._REQUIRED_COVERAGE_CANONICAL),
            "scope_policy_binding_sha256": subject._scope_policy_binding_sha256(
                role=role, facts=facts
            ),
            "writer_authorized": False,
            "promotion_authorized": False,
            "external_effect_authorized": False,
            "installation_authorized": False,
            "execution_authorized": False,
            "full_matrix_authorized": False,
            "signature_algorithm": "ed25519",
        }

    def _scope(
        self, role: str, *, body: dict[str, object] | None = None
    ) -> bytes:
        key = self.executor_key if role == subject._EXECUTOR_ROLE else self.observer_key
        return self._signed(
            self._scope_body(role) if body is None else body,
            key=key,
            domain=subject._ROLE_TO_DOMAIN[role],
        )

    def _installation_body(
        self,
        role: str,
        *,
        scope_raw: bytes,
        installed_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, object]:
        facts = self._facts()
        scope_sha = hashlib.sha256(scope_raw).hexdigest()
        scope_binding = subject._scope_policy_binding_sha256(role=role, facts=facts)
        implementation = _sha(f"{role}-implementation")
        configuration = _sha(f"{role}-configuration")
        issued = self.now - timedelta(seconds=1) if installed_at is None else installed_at
        expires = self.now + timedelta(seconds=30) if expires_at is None else expires_at
        return {
            "schema": subject.PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_SCHEMA,
            "version": 1,
            "status": subject._INSTALLATION_STATUS,
            "signer_role": role,
            "signer_key_id": subject._key_id(
                facts.executor_public_key
                if role == subject._EXECUTOR_ROLE
                else facts.observer_public_key
            ),
            "p2_binding_sha256": facts.p2_binding_sha256,
            "scope_policy_sha256": scope_sha,
            "scope_policy_binding_sha256": scope_binding,
            "installation_implementation_sha256": implementation,
            "installation_configuration_sha256": configuration,
            "installation_binding_sha256": subject._installation_binding_sha256(
                role=role,
                facts=facts,
                scope_sha256=scope_sha,
                scope_binding_sha256=scope_binding,
                implementation_sha256=implementation,
                configuration_sha256=configuration,
            ),
            "installed_at": subject._render_timestamp(
                issued,
                code="TEST_TIME_INVALID",
            ),
            "expires_at": subject._render_timestamp(
                expires,
                code="TEST_TIME_INVALID",
            ),
            "writer_authorized": False,
            "promotion_authorized": False,
            "external_effect_authorized": False,
            "installation_authorized": False,
            "execution_authorized": False,
            "full_matrix_authorized": False,
            "signature_algorithm": "ed25519",
        }

    def _installation(
        self,
        role: str,
        *,
        scope_raw: bytes,
        body: dict[str, object] | None = None,
    ) -> bytes:
        key = self.executor_key if role == subject._EXECUTOR_ROLE else self.observer_key
        return self._signed(
            self._installation_body(role, scope_raw=scope_raw) if body is None else body,
            key=key,
            domain=subject._ROLE_TO_INSTALLATION_DOMAIN[role],
        )

    def _evidence(
        self,
        *,
        executor_scope: bytes | None = None,
        executor_installation: bytes | None = None,
        observer_scope: bytes | None = None,
        observer_installation: bytes | None = None,
    ) -> subject.PhysicalFullMatrixV4FiFenceScopeInstallationEvidence:
        executor_scope = self._scope(subject._EXECUTOR_ROLE) if executor_scope is None else executor_scope
        observer_scope = self._scope(subject._OBSERVER_ROLE) if observer_scope is None else observer_scope
        return subject.PhysicalFullMatrixV4FiFenceScopeInstallationEvidence(
            executor_scope_policy=executor_scope,
            executor_installation_attestation=(
                self._installation(subject._EXECUTOR_ROLE, scope_raw=executor_scope)
                if executor_installation is None
                else executor_installation
            ),
            observer_scope_policy=observer_scope,
            observer_installation_attestation=(
                self._installation(subject._OBSERVER_ROLE, scope_raw=observer_scope)
                if observer_installation is None
                else observer_installation
            ),
        )

    def _verify(self, evidence=None, *, config=None, now=None):
        return subject.verify_physical_full_matrix_v4_fi_fence_scope_installation_provenance(
            evidence=self._evidence() if evidence is None else evidence,
            config=self.config if config is None else config,
            now=self.now if now is None else now,
        )

    def test_verifies_all_mandatory_surfaces_and_projects_only_p2_evidence_pins(self) -> None:
        value = self._verify()
        self.assertIs(
            value,
            subject.require_verified_physical_full_matrix_v4_fi_fence_scope_installation_provenance(
                value, config=self.config
            ),
        )
        self.assertEqual(str(self.effect_start.run_id), value.run_id)
        self.assertEqual(self.effect_start.plan_sha256, value.plan_sha256)
        self.assertEqual(
            self.effect_start.journaled_effect_start_identity_sha256,
            value.phase2_effect_start_identity_sha256,
        )
        self.assertEqual(self.anchor.anchor_sequence, value.phase2_anchor_sequence)
        self.assertEqual(self.anchor.anchor_head_sha256, value.phase2_anchor_head_sha256)
        self.assertFalse(value.writer_authorized)
        self.assertFalse(value.promotion_authorized)
        self.assertFalse(value.external_effect_authorized)
        self.assertFalse(value.installation_authorized)
        self.assertFalse(value.execution_authorized)
        self.assertFalse(value.full_matrix_authorized)
        pins = subject.project_physical_full_matrix_v4_fi_fence_scope_installation_evidence_pins(
            value,
            executor_fence_evidence_sha256=_sha("executor-post-fence-observation"),
            observer_fence_evidence_sha256=_sha("observer-post-fence-observation"),
            config=self.config,
        )
        self.assertEqual(value.executor_scope_policy_sha256, pins.executor_scope_policy_sha256)
        self.assertEqual(
            value.executor_installation_attestation_sha256,
            pins.executor_installation_attestation_sha256,
        )
        self.assertEqual(value.observer_scope_policy_sha256, pins.observer_scope_policy_sha256)
        self.assertEqual(
            value.observer_installation_attestation_sha256,
            pins.observer_installation_attestation_sha256,
        )
        self.assertEqual(6, len({
            pins.executor_installation_attestation_sha256,
            pins.executor_scope_policy_sha256,
            pins.executor_fence_evidence_sha256,
            pins.observer_installation_attestation_sha256,
            pins.observer_scope_policy_sha256,
            pins.observer_fence_evidence_sha256,
        }))
        self.assertIs(
            pins,
            p2._evidence_pins_mapping(pins, code="TEST_INVALID")[0],
        )

    def test_default_off_refuses_before_untrusted_evidence_is_interpreted(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FiFenceScopeInstallationError,
            "SCOPE_INSTALLATION_DISABLED",
        ):
            self._verify(
                evidence=subject.PhysicalFullMatrixV4FiFenceScopeInstallationEvidence(),
                config=replace(self.config, enabled=False),
            )

    def test_scope_policy_cannot_omit_a_required_writer_surface_even_when_resigned(self) -> None:
        body = self._scope_body(subject._EXECUTOR_ROLE)
        del body["mandatory_coverage"]["application_writer_surfaces"]["migration"]
        evidence = self._evidence(executor_scope=self._scope(subject._EXECUTOR_ROLE, body=body))
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FiFenceScopeInstallationError,
            "SCOPE_POLICY_COVERAGE_INVALID",
        ):
            self._verify(evidence)

    def test_hash_only_generic_policy_cannot_replace_fixed_coverage(self) -> None:
        body = self._scope_body(subject._EXECUTOR_ROLE)
        body["mandatory_coverage"] = {"generic_policy_sha256": _sha("generic")}
        evidence = self._evidence(executor_scope=self._scope(subject._EXECUTOR_ROLE, body=body))
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FiFenceScopeInstallationError,
            "SCOPE_POLICY_COVERAGE_INVALID",
        ):
            self._verify(evidence)

    def test_scope_is_exactly_bound_to_the_phase2_anchor_and_former_term(self) -> None:
        body = self._scope_body(subject._EXECUTOR_ROLE)
        body["phase2_effect_start_anchor"]["anchor_head_sha256"] = _sha("foreign-anchor")
        evidence = self._evidence(executor_scope=self._scope(subject._EXECUTOR_ROLE, body=body))
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FiFenceScopeInstallationError,
            "SCOPE_POLICY_BINDING_MISMATCH",
        ):
            self._verify(evidence)

    def test_executor_artifact_cannot_be_relabelled_as_independent_observer_artifact(self) -> None:
        evidence = self._evidence()
        swapped = replace(
            evidence,
            observer_scope_policy=evidence.executor_scope_policy,
            observer_installation_attestation=evidence.executor_installation_attestation,
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FiFenceScopeInstallationError,
            "SCOPE_POLICY_SIGNATURE_INVALID",
        ):
            self._verify(swapped)

    def test_distinct_signer_keys_are_mandatory(self) -> None:
        unsafe = replace(
            self.config,
            observer_signer_public_key=self.config.executor_signer_public_key,
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FiFenceScopeInstallationError,
            "SIGNER_SEPARATION_REQUIRED",
        ):
            self._verify(config=unsafe)

    def test_installation_attestation_is_fresh_and_has_no_authority(self) -> None:
        executor_scope = self._scope(subject._EXECUTOR_ROLE)
        body = self._installation_body(
            subject._EXECUTOR_ROLE,
            scope_raw=executor_scope,
            installed_at=self.now - timedelta(seconds=100),
            expires_at=self.now - timedelta(seconds=1),
        )
        stale = self._installation(
            subject._EXECUTOR_ROLE,
            scope_raw=executor_scope,
            body=body,
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FiFenceScopeInstallationError,
            "INSTALLATION_ATTESTATION_STALE",
        ):
            self._verify(
                self._evidence(
                    executor_scope=executor_scope,
                    executor_installation=stale,
                )
            )
        active = self._installation_body(subject._EXECUTOR_ROLE, scope_raw=executor_scope)
        active["execution_authorized"] = True
        unsafe = self._installation(
            subject._EXECUTOR_ROLE,
            scope_raw=executor_scope,
            body=active,
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FiFenceScopeInstallationError,
            "INSTALLATION_ATTESTATION_BINDING_MISMATCH",
        ):
            self._verify(
                self._evidence(
                    executor_scope=executor_scope,
                    executor_installation=unsafe,
                )
            )

    def test_projection_rejects_collapsed_post_fence_pins(self) -> None:
        value = self._verify()
        duplicate = _sha("one-post-fence-observation")
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FiFenceScopeInstallationError,
            "EVIDENCE_PINS_NOT_DISTINCT",
        ):
            subject.project_physical_full_matrix_v4_fi_fence_scope_installation_evidence_pins(
                value,
                executor_fence_evidence_sha256=duplicate,
                observer_fence_evidence_sha256=duplicate,
                config=self.config,
            )

    def test_opaque_result_rejects_forgery_and_public_field_tampering(self) -> None:
        with self.assertRaises(TypeError):
            subject.VerifiedPhysicalFullMatrixV4FiFenceScopeInstallationProvenance(
                capability=object()  # type: ignore[call-arg]
            )
        value = self._verify()
        object.__setattr__(value, "executor_scope_policy_sha256", _sha("tampered"))
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4FiFenceScopeInstallationError,
            "PROVENANCE_TAMPERED",
        ):
            subject.require_verified_physical_full_matrix_v4_fi_fence_scope_installation_provenance(
                value, config=self.config
            )

    def test_module_has_no_operational_or_network_imports(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(
            imported
            & {
                "os",
                "pathlib",
                "socket",
                "subprocess",
                "requests",
                "urllib",
                "boto3",
                "docker",
            }
        )

