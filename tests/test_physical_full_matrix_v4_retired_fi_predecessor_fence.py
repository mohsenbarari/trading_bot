"""Boundary tests for the P2 retired-FI predecessor evidence grammar."""

from __future__ import annotations

import base64
import copy
import ast
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
from core import physical_full_matrix_v4_retired_fi_predecessor_fence as subject


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_retired_fi_predecessor_fence.py"
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class RetiredFiPredecessorFenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        self.executor_key = Ed25519PrivateKey.generate()
        self.observer_key = Ed25519PrivateKey.generate()
        self.witness_key = Ed25519PrivateKey.generate()
        self.binding = driver.PhysicalFullMatrixV4ExecutionBinding(
            campaign_id="matrix-p2-fi-retire-2026",
            release_sha="1" * 40,
            readiness_binding_sha256=_sha("readiness"),
            route_commitment_sha256=_sha("route"),
            four_role_binding_sha256=_sha("four-role"),
            writer_holder_site="webapp_fi",
            writer_epoch=17,
            writer_lease_id="fi-writer-lease-17",
            witnessed_term_proof_sha256=_sha("term-proof"),
            source_site="webapp_fi",
            destination_site="webapp_ir",
            roundtrip_attestation_sha256=_sha("roundtrip-attestation"),
            roundtrip_configuration_sha256=_sha("roundtrip-config"),
            witness_transition_id="witness-transition-fi-00017",
            witness_sequence=31,
        )
        self.start_identity = (
            driver.derive_physical_full_matrix_v4_effect_start_identity_sha256(
                driver.PhysicalFullMatrixV4EffectStart(
                    run_id=UUID("a8824e44-dd7c-48a8-a69e-7c9286a1e808"),
                    plan_sha256=_sha("v4-plan"),
                    sequence=driver.PHYSICAL_FULL_MATRIX_V4_PHASES[1].sequence,
                    phase_request_sha256=_sha("phase-2-request"),
                    effect_key=_sha("phase-2-effect"),
                    claim_id="phase-2-fence-claim-000001",
                )
            )
        )
        self.effect_start = subject.PhysicalFullMatrixV4EffectStartPin(
            run_id=UUID("a8824e44-dd7c-48a8-a69e-7c9286a1e808"),
            plan_sha256=_sha("v4-plan"),
            phase=driver.PHYSICAL_FULL_MATRIX_V4_PHASES[1],
            effect_key=_sha("phase-2-effect"),
            phase_request_sha256=_sha("phase-2-request"),
            binding=self.binding,
            claim_id="phase-2-fence-claim-000001",
            journaled_effect_start_identity_sha256=self.start_identity,
        )
        self.anchor = subject.PhysicalFullMatrixV4EffectStartAnchorPin(
            schema=driver.PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA,
            run_id=self.effect_start.run_id,
            plan_sha256=self.effect_start.plan_sha256,
            phase=self.effect_start.phase,
            effect_key=self.effect_start.effect_key,
            phase_request_sha256=self.effect_start.phase_request_sha256,
            binding=self.effect_start.binding,
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
        self.term = subject.RetiredFiPredecessorFenceTermPin(
            holder_site="webapp_fi",
            writer_epoch=self.binding.writer_epoch,
            writer_lease_id=self.binding.writer_lease_id,
            witness_transition_id=self.binding.witness_transition_id,
            witnessed_term_proof_sha256=self.binding.witnessed_term_proof_sha256,
        )
        self.evidence_pins = subject.RetiredFiPredecessorFenceEvidencePins(
            executor_installation_attestation_sha256=_sha("executor-install"),
            executor_scope_policy_sha256=_sha("executor-scope"),
            executor_fence_evidence_sha256=_sha("executor-fence"),
            observer_installation_attestation_sha256=_sha("observer-install"),
            observer_scope_policy_sha256=_sha("observer-scope"),
            observer_fence_evidence_sha256=_sha("observer-fence"),
        )
        self.policy = subject.RetiredFiPredecessorFenceAntiReplayPolicy(
            anti_replay_namespace=(
                subject.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_ANTI_REPLAY_NAMESPACE
            ),
            witness_ledger_scope_sha256=_sha("witness-retirement-ledger-scope"),
        )
        self.config = subject.RetiredFiPredecessorFenceVerificationConfig(
            expected_effect_start=self.effect_start,
            expected_effect_start_anchor=self.anchor,
            expected_predecessor_term=self.term,
            expected_evidence_pins=self.evidence_pins,
            expected_anti_replay_policy=self.policy,
            executor_signer_public_key=_public(self.executor_key),
            observer_signer_public_key=_public(self.observer_key),
            witness_anti_replay_signer_public_key=_public(self.witness_key),
            enabled=True,
        )

    def _binding(self, *, fence_id: str = "phase-2-fi-retirement-000001") -> dict[str, object]:
        effect = subject._effect_start_mapping(
            self.effect_start, code="TEST_INVALID"
        )[1]
        anchor = subject._effect_start_anchor_mapping(self.anchor, code="TEST_INVALID")[1]
        term = subject._term_mapping(self.term, code="TEST_INVALID")[1]
        evidence = subject._evidence_pins_mapping(self.evidence_pins, code="TEST_INVALID")[1]
        return {
            "schema": subject.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_SCHEMA,
            "version": 1,
            "status": subject.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_STATUS,
            "retirement_mode": "server-side-fi-writer-retired-v1",
            "fence_id": fence_id,
            "fence_nonce": "FiP2RetirementNonce0000001",
            "retired_at": self.now.isoformat().replace("+00:00", "Z"),
            "expires_at": (self.now + timedelta(seconds=90)).isoformat().replace(
                "+00:00", "Z"
            ),
            "effect_start": effect,
            "effect_start_anchor": anchor,
            "predecessor_term": term,
            "evidence_pins": evidence,
        }

    @staticmethod
    def _signed(mapping: dict[str, object], *, key: Ed25519PrivateKey, domain: bytes) -> bytes:
        unsigned = dict(mapping)
        signature = key.sign(domain + canonical_json_bytes(unsigned))
        payload = dict(unsigned)
        payload["signature_base64"] = base64.b64encode(signature).decode("ascii")
        return canonical_json_bytes(payload)

    def _receipt(
        self,
        *,
        binding: dict[str, object],
        kind: str,
        schema: str,
        role: str,
        key: Ed25519PrivateKey,
        domain: bytes,
    ) -> bytes:
        binding_sha = hashlib.sha256(canonical_json_bytes(binding)).hexdigest()
        return self._signed(
            {
                "schema": schema,
                "version": 1,
                "kind": kind,
                "signer_role": role,
                "fence_binding": binding,
                "fence_binding_sha256": binding_sha,
            },
            key=key,
            domain=domain,
        )

    def _witness(self, *, binding: dict[str, object], replay_key: str | None = None) -> bytes:
        binding_sha = hashlib.sha256(canonical_json_bytes(binding)).hexdigest()
        key = replay_key or subject.derive_retired_fi_predecessor_fence_replay_key_sha256(
            effect_start=self.effect_start,
            predecessor_term=self.term,
        )
        return self._signed(
            {
                "schema": subject.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_WITNESS_ADMISSION_SCHEMA,
                "version": 1,
                "kind": "witness-durable-anti-replay-admission",
                "signer_role": "witness-durable-anti-replay-ledger",
                "fence_id": binding["fence_id"],
                "fence_nonce": binding["fence_nonce"],
                "fence_binding_sha256": binding_sha,
                "replay_key_sha256": key,
                "anti_replay_namespace": self.policy.anti_replay_namespace,
                "anti_replay_mode": "witness-durable-single-use-admission-v1",
                "witness_ledger_scope_sha256": self.policy.witness_ledger_scope_sha256,
                "admission_id": "witness-p2-admission-000001",
                "admission_nonce": "WitnessP2AdmissionNonce000001",
                "admitted_at": (self.now + timedelta(seconds=1)).isoformat().replace(
                    "+00:00", "Z"
                ),
                "expires_at": binding["expires_at"],
                "witness_ledger_sequence": 32,
                "witness_ledger_entry_sha256": _sha("witness-ledger-entry"),
                "witness_ledger_previous_head_sha256": _sha("witness-ledger-head"),
            },
            key=self.witness_key,
            domain=subject._WITNESS_DOMAIN,
        )

    def _evidence(self, *, binding: dict[str, object] | None = None) -> tuple[bytes, bytes, bytes]:
        actual = self._binding() if binding is None else binding
        executor = self._receipt(
            binding=actual,
            schema=subject.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_EXECUTOR_RECEIPT_SCHEMA,
            kind="fi-root-fence-executor-evidence",
            role="fi-root-fence-executor",
            key=self.executor_key,
            domain=subject._EXECUTOR_DOMAIN,
        )
        observer = self._receipt(
            binding=actual,
            schema=subject.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_OBSERVER_RECEIPT_SCHEMA,
            kind="fi-independent-fence-observer-evidence",
            role="fi-independent-fence-observer",
            key=self.observer_key,
            domain=subject._OBSERVER_DOMAIN,
        )
        return executor, observer, self._witness(binding=actual)

    def _verify(self, evidence: tuple[bytes, bytes, bytes] | None = None, *, config=None, now=None):
        executor, observer, witness = self._evidence() if evidence is None else evidence
        return subject.verify_retired_fi_predecessor_fence(
            executor_receipt=executor,
            observer_receipt=observer,
            witness_admission_receipt=witness,
            config=self.config if config is None else config,
            now=self.now if now is None else now,
        )

    def test_verifies_three_independent_signed_receipts_and_never_grants_authority(self) -> None:
        value = self._verify()
        self.assertIs(
            value,
            subject.require_verified_retired_fi_predecessor_fence(
                value, config=self.config, now=self.now
            ),
        )
        self.assertEqual(self.effect_start, value.effect_start)
        self.assertEqual(self.anchor, value.effect_start_anchor)
        self.assertEqual(self.term, value.predecessor_term)
        self.assertEqual(self.evidence_pins, value.evidence_pins)
        self.assertEqual(
            subject.derive_retired_fi_predecessor_fence_replay_key_sha256(
                effect_start=self.effect_start, predecessor_term=self.term
            ),
            value.replay_key_sha256,
        )
        self.assertFalse(value.writer_authorized)
        self.assertFalse(value.promotion_authorized)
        self.assertFalse(value.traffic_switch_authorized)
        self.assertFalse(value.external_effect_authorized)
        self.assertFalse(value.execution_authorized)
        self.assertFalse(value.full_matrix_authorized)
        with self.assertRaises(TypeError):
            copy.copy(value)

    def test_disabled_or_nonseparated_signers_fail_closed(self) -> None:
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "DISABLED"):
            self._verify(config=replace(self.config, enabled=False))
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "SIGNER_SEPARATION"):
            self._verify(
                config=replace(
                    self.config,
                    observer_signer_public_key=self.config.executor_signer_public_key,
                )
            )

    def test_expected_v4_predecessor_and_evidence_pins_are_exact(self) -> None:
        wrong_term = replace(self.term, writer_epoch=self.term.writer_epoch + 1)
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "CONFIG_PREDECESSOR_MISMATCH"):
            self._verify(config=replace(self.config, expected_predecessor_term=wrong_term))
        wrong_effect = replace(
            self.effect_start,
            effect_key=_sha("different-effect"),
        )
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "CONFIG_INVALID"):
            self._verify(config=replace(self.config, expected_effect_start=wrong_effect))
        wrong_anchor = replace(self.anchor, anchor_head_sha256=_sha("different-anchor"))
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "EXPECTED_PINS_MISMATCH"):
            self._verify(
                config=replace(self.config, expected_effect_start_anchor=wrong_anchor)
            )
        alternate_anchor_effect_key = _sha("anchor-for-other-effect")
        substituted_anchor = replace(
            self.anchor,
            effect_key=alternate_anchor_effect_key,
            journaled_effect_start_identity_sha256=(
                driver.derive_physical_full_matrix_v4_effect_start_identity_sha256(
                    driver.PhysicalFullMatrixV4EffectStart(
                        run_id=self.effect_start.run_id,
                        plan_sha256=self.effect_start.plan_sha256,
                        sequence=self.effect_start.phase.sequence,
                        phase_request_sha256=self.effect_start.phase_request_sha256,
                        effect_key=alternate_anchor_effect_key,
                        claim_id=self.effect_start.claim_id,
                    )
                )
            ),
        )
        with self.assertRaisesRegex(
            subject.RetiredFiPredecessorFenceError, "CONFIG_ANCHOR_EFFECT_MISMATCH"
        ):
            self._verify(
                config=replace(
                    self.config,
                    expected_effect_start_anchor=substituted_anchor,
                )
            )
        cross_effect_binding = copy.deepcopy(self._binding())
        anchor_mapping = cross_effect_binding["effect_start_anchor"]
        assert isinstance(anchor_mapping, dict)
        alternate_effect_key = _sha("signed-anchor-for-other-effect")
        anchor_mapping["effect_key"] = alternate_effect_key
        anchor_mapping["journaled_effect_start_identity_sha256"] = (
            driver.derive_physical_full_matrix_v4_effect_start_identity_sha256(
                driver.PhysicalFullMatrixV4EffectStart(
                    run_id=self.effect_start.run_id,
                    plan_sha256=self.effect_start.plan_sha256,
                    sequence=self.effect_start.phase.sequence,
                    phase_request_sha256=self.effect_start.phase_request_sha256,
                    effect_key=alternate_effect_key,
                    claim_id=self.effect_start.claim_id,
                )
            )
        )
        with self.assertRaisesRegex(
            subject.RetiredFiPredecessorFenceError, "EXPECTED_PINS_MISMATCH"
        ):
            self._verify(self._evidence(binding=cross_effect_binding))
        wrong_evidence = replace(
            self.evidence_pins,
            observer_fence_evidence_sha256=_sha("different-observer-fence"),
        )
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "EXPECTED_PINS_MISMATCH"):
            self._verify(config=replace(self.config, expected_evidence_pins=wrong_evidence))

    def test_effect_start_identity_is_recomputed_not_a_caller_label(self) -> None:
        forged = replace(
            self.effect_start,
            journaled_effect_start_identity_sha256=_sha("forged-start-identity"),
        )
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "CONFIG_INVALID"):
            self._verify(config=replace(self.config, expected_effect_start=forged))

    def test_executor_and_observer_must_sign_one_identical_binding(self) -> None:
        binding = self._binding()
        executor = self._receipt(
            binding=binding,
            schema=subject.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_EXECUTOR_RECEIPT_SCHEMA,
            kind="fi-root-fence-executor-evidence",
            role="fi-root-fence-executor",
            key=self.executor_key,
            domain=subject._EXECUTOR_DOMAIN,
        )
        conflicting = self._binding(fence_id="phase-2-fi-retirement-000002")
        observer = self._receipt(
            binding=conflicting,
            schema=subject.PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_OBSERVER_RECEIPT_SCHEMA,
            kind="fi-independent-fence-observer-evidence",
            role="fi-independent-fence-observer",
            key=self.observer_key,
            domain=subject._OBSERVER_DOMAIN,
        )
        witness = self._witness(binding=binding)
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "EXECUTOR_OBSERVER_MISMATCH"):
            self._verify((executor, observer, witness))

    def test_witness_admission_binds_deterministic_replay_key_not_receipt_nonce(self) -> None:
        first = subject.derive_retired_fi_predecessor_fence_replay_key_sha256(
            effect_start=self.effect_start,
            predecessor_term=self.term,
        )
        # Different receipt IDs are intentionally absent from the replay key.
        changed_binding = self._binding(fence_id="phase-2-fi-retirement-000099")
        second = subject.derive_retired_fi_predecessor_fence_replay_key_sha256(
            effect_start=self.effect_start,
            predecessor_term=self.term,
        )
        self.assertEqual(first, second)
        evidence = self._evidence(binding=changed_binding)
        # The new receipt ID is a different signed binding, not a second P2
        # replay identity; a real Witness ledger must reject that reuse.
        self.assertEqual(first, subject.derive_retired_fi_predecessor_fence_replay_key_sha256(
            effect_start=self.effect_start, predecessor_term=self.term
        ))
        self.assertIsInstance(evidence[2], bytes)
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "REPLAY_KEY_MISMATCH"):
            self._verify(
                (
                    evidence[0],
                    evidence[1],
                    self._witness(binding=changed_binding, replay_key=_sha("wrong-replay")),
                )
            )

    def test_expiry_signatures_and_canonicality_are_rechecked(self) -> None:
        value = self._verify()
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "STALE_OR_EXPIRED"):
            subject.require_verified_retired_fi_predecessor_fence(
                value,
                config=self.config,
                now=self.now + timedelta(seconds=91),
            )
        executor, observer, witness = self._evidence()
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "EXECUTOR_RECEIPT_INVALID"):
            self._verify((executor + b" ", observer, witness))
        forged = bytearray(witness)
        forged[-2] ^= 1
        with self.assertRaises(subject.RetiredFiPredecessorFenceError):
            self._verify((executor, observer, bytes(forged)))

    def test_verified_object_tampering_and_manual_construction_fail_closed(self) -> None:
        value = self._verify()
        object.__setattr__(value, "writer_authorized", True)
        with self.assertRaisesRegex(subject.RetiredFiPredecessorFenceError, "TAMPERED"):
            subject.require_verified_retired_fi_predecessor_fence(
                value, config=self.config, now=self.now
            )
        with self.assertRaises(TypeError):
            subject.VerifiedRetiredFiPredecessorFence(
                canonical_executor_receipt=b"x",
                canonical_observer_receipt=b"x",
                canonical_witness_admission_receipt=b"x",
                executor_receipt_sha256=_sha("x"),
                observer_receipt_sha256=_sha("y"),
                witness_admission_receipt_sha256=_sha("z"),
                effect_start=self.effect_start,
                effect_start_anchor=self.anchor,
                predecessor_term=self.term,
                evidence_pins=self.evidence_pins,
                anti_replay_policy=self.policy,
                fence_id="phase-2-fi-retirement-000001",
                fence_nonce="FiP2RetirementNonce0000001",
                replay_key_sha256=_sha("r"),
                retired_at=self.now,
                expires_at=self.now + timedelta(seconds=1),
                admission_id="witness-p2-admission-000001",
                admission_nonce="WitnessP2AdmissionNonce000001",
                admitted_at=self.now,
                witness_ledger_sequence=1,
                witness_ledger_entry_sha256=_sha("entry"),
                witness_ledger_previous_head_sha256="0" * 64,
                capability=object(),
            )

    def test_module_neither_adapts_v1_self_fence_nor_contains_live_operators(self) -> None:
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
        self.assertTrue({"subprocess", "socket", "requests", "paramiko"}.isdisjoint(imports))
