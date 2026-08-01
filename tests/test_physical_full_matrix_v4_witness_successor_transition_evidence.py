"""Adversarial tests for the portable V4 phase-4/phase-7 evidence grammar."""

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
from core import physical_full_matrix_v4_witness_successor_transition_evidence as subject


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_witness_successor_transition_evidence.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class _TransitionFixture:
    def __init__(self, phase_name: str) -> None:
        self.now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        self.phase = next(
            phase for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES if phase.name == phase_name
        )
        self.executor_key = Ed25519PrivateKey.generate()
        self.observer_key = Ed25519PrivateKey.generate()
        self.witness_key = Ed25519PrivateKey.generate()
        predecessor_pair, successor_pair = subject._TRANSITION_DIRECTIONS[phase_name]
        self.predecessor = self._binding(
            label=f"{phase_name}-predecessor",
            source=predecessor_pair[0],
            destination=predecessor_pair[1],
            epoch=21,
            sequence=42,
        )
        self.successor = self._binding(
            label=f"{phase_name}-successor",
            source=successor_pair[0],
            destination=successor_pair[1],
            epoch=22,
            sequence=43,
        )
        # V4 requires this configuration pin to remain stable through a term
        # transition even though route/readiness/attestation rotate.
        self.successor = replace(
            self.successor,
            four_role_binding_sha256=self.predecessor.four_role_binding_sha256,
            roundtrip_configuration_sha256=self.predecessor.roundtrip_configuration_sha256,
        )
        self.effect_start = self._effect_start()
        self.anchor = self._anchor()
        self.readiness = subject.PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin(
            schema=subject.PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_READINESS_EVIDENCE_SCHEMA,
            status=subject.PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_READINESS_EVIDENCE_STATUS,
            gen2_readiness_schema=(
                "gold-trade-physical-full-matrix-v2-gen2-witnessed-campaign-readiness-v1"
            ),
            gen2_readiness_status="v2-gen2-witnessed-ack-chain-observed",
            campaign_id=self.successor.campaign_id,
            release_sha=self.successor.release_sha,
            successor_readiness_binding_sha256=self.successor.readiness_binding_sha256,
            gen2_observed_slots=("v2-gen2-witness-mediated-ack-chain",),
            gen2_reason_codes=(),
            readiness_evidence_sha256=_hash(f"{self.phase.name}-readiness-evidence"),
            observed_at=self.now - timedelta(seconds=2),
            expires_at=self.now + timedelta(seconds=90),
        )
        self.pins = subject.PhysicalFullMatrixV4SuccessorTransitionEvidencePins(
            executor_installation_attestation_sha256=_hash(f"{phase_name}-executor-install"),
            executor_scope_policy_sha256=_hash(f"{phase_name}-executor-scope"),
            executor_transition_evidence_sha256=_hash(f"{phase_name}-executor-evidence"),
            observer_installation_attestation_sha256=_hash(f"{phase_name}-observer-install"),
            observer_scope_policy_sha256=_hash(f"{phase_name}-observer-scope"),
            observer_transition_evidence_sha256=_hash(f"{phase_name}-observer-evidence"),
            successor_installation_attestation_sha256=_hash(f"{phase_name}-target-install"),
        )
        self.policy = subject.PhysicalFullMatrixV4SuccessorTransitionReplayPolicy(
            anti_replay_namespace=(
                subject.PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_ANTI_REPLAY_NAMESPACE
            ),
            witness_ledger_scope_sha256=_hash(f"{phase_name}-witness-scope"),
        )
        self.config = subject.PhysicalFullMatrixV4SuccessorTransitionVerificationConfig(
            expected_effect_start=self.effect_start,
            expected_effect_start_anchor=self.anchor,
            expected_predecessor_binding=self.predecessor,
            expected_successor_binding=self.successor,
            expected_successor_readiness=self.readiness,
            expected_evidence_pins=self.pins,
            expected_replay_policy=self.policy,
            executor_signer_public_key=_public(self.executor_key),
            observer_signer_public_key=_public(self.observer_key),
            witness_signer_public_key=_public(self.witness_key),
            enabled=True,
        )

    def _binding(
        self, *, label: str, source: str, destination: str, epoch: int, sequence: int
    ) -> driver.PhysicalFullMatrixV4ExecutionBinding:
        return driver.PhysicalFullMatrixV4ExecutionBinding(
            campaign_id="matrix-successor-transition-2026",
            release_sha="2" * 40,
            readiness_binding_sha256=_hash(f"{label}-readiness"),
            route_commitment_sha256=_hash(f"{label}-route"),
            four_role_binding_sha256=_hash(f"{label}-four-role"),
            writer_holder_site=source,
            writer_epoch=epoch,
            writer_lease_id=f"{source}-writer-lease-{epoch}",
            witnessed_term_proof_sha256=_hash(f"{label}-term"),
            source_site=source,
            destination_site=destination,
            roundtrip_attestation_sha256=_hash(f"{label}-roundtrip"),
            roundtrip_configuration_sha256=_hash(f"{label}-roundtrip-config"),
            witness_transition_id=f"witness-transition-{label}-000{epoch}",
            witness_sequence=sequence,
        )

    def _effect_start(self):
        run_id = (
            UUID("f448bbd0-15ee-4cf5-95e1-7fac68d79162")
            if self.phase.sequence == 4
            else UUID("63b9410f-274a-4f1a-a811-99e5d1951510")
        )
        plan = _hash(f"{self.phase.name}-plan")
        effect_key = _hash(f"{self.phase.name}-effect")
        request = _hash(f"{self.phase.name}-request")
        claim = f"{self.phase.name}-claim-000001"
        identity = driver.derive_physical_full_matrix_v4_effect_start_identity_sha256(
            driver.PhysicalFullMatrixV4EffectStart(
                run_id=run_id,
                plan_sha256=plan,
                sequence=self.phase.sequence,
                phase_request_sha256=request,
                effect_key=effect_key,
                claim_id=claim,
            )
        )
        return subject.PhysicalFullMatrixV4SuccessorTransitionEffectStartPin(
            run_id=run_id,
            plan_sha256=plan,
            phase=self.phase,
            effect_key=effect_key,
            phase_request_sha256=request,
            binding=self.predecessor,
            claim_id=claim,
            journaled_effect_start_identity_sha256=identity,
        )

    def _anchor(self):
        return subject.PhysicalFullMatrixV4SuccessorTransitionAnchorPin(
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
            journal_binding_sha256=_hash(f"{self.phase.name}-journal"),
            baseline_plan_binding_sha256=_hash(f"{self.phase.name}-baseline"),
            anchor_genesis_sequence=7,
            anchor_genesis_head_sha256=_hash(f"{self.phase.name}-anchor-genesis"),
            anchor_previous_sequence=12,
            anchor_previous_head_sha256=_hash(f"{self.phase.name}-anchor-previous"),
            anchor_sequence=13,
            anchor_head_sha256=_hash(f"{self.phase.name}-anchor-head"),
            anchor_commitment_sha256=_hash(f"{self.phase.name}-anchor-commitment"),
            anchor_attestation_sha256=_hash(f"{self.phase.name}-anchor-attestation"),
            anchor_local_previous_record_sha256=_hash(f"{self.phase.name}-anchor-local-prev"),
            anchor_local_event_sha256=_hash(f"{self.phase.name}-anchor-local-event"),
            anchor_occurred_at=self.now - timedelta(seconds=5),
        )

    @staticmethod
    def _signed(mapping: dict[str, object], *, key: Ed25519PrivateKey, domain: bytes) -> bytes:
        unsigned = dict(mapping)
        signature = key.sign(domain + canonical_json_bytes(unsigned))
        payload = dict(unsigned)
        payload["signature_base64"] = base64.b64encode(signature).decode("ascii")
        return canonical_json_bytes(payload)

    def binding_mapping(self, *, transition_id: str = "v4-successor-transition-000001"):
        return {
            "schema": subject.PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_SCHEMA,
            "version": 1,
            "status": subject.PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_STATUS,
            "transition_id": transition_id,
            "transition_nonce": "V4SuccessorTransitionNonce000001",
            "requested_at": (self.now - timedelta(seconds=4)).isoformat().replace("+00:00", "Z"),
            "expires_at": (self.now + timedelta(seconds=90)).isoformat().replace("+00:00", "Z"),
            "effect_start": subject._effect_start_mapping(self.effect_start, code="TEST")[1],
            "effect_start_anchor": subject._anchor_mapping(self.anchor, code="TEST")[1],
            "predecessor_binding": subject._binding_mapping(
                self.predecessor,
                direction=subject._TRANSITION_DIRECTIONS[self.phase.name][0],
                code="TEST",
            )[1],
            "successor_binding": subject._binding_mapping(
                self.successor,
                direction=subject._TRANSITION_DIRECTIONS[self.phase.name][1],
                code="TEST",
            )[1],
            "successor_readiness": subject._readiness_mapping(
                self.readiness,
                successor=self.successor,
                now=None,
                maximum_age=90,
                code="TEST",
            )[1],
            "evidence_pins": subject._evidence_pins_mapping(self.pins, code="TEST")[1],
        }

    def receipt(self, *, binding: dict[str, object], executor: bool) -> bytes:
        return self._signed(
            {
                "schema": (
                    subject.PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_EXECUTOR_RECEIPT_SCHEMA
                    if executor
                    else subject.PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_OBSERVER_RECEIPT_SCHEMA
                ),
                "version": 1,
                "kind": (
                    "target-root-successor-transition-executor-evidence"
                    if executor
                    else "independent-successor-transition-observer-evidence"
                ),
                "signer_role": (
                    "target-root-successor-transition-executor"
                    if executor
                    else "independent-successor-transition-observer"
                ),
                "transition_binding": binding,
                "transition_binding_sha256": hashlib.sha256(
                    canonical_json_bytes(binding)
                ).hexdigest(),
            },
            key=self.executor_key if executor else self.observer_key,
            domain=subject._EXECUTOR_DOMAIN if executor else subject._OBSERVER_DOMAIN,
        )

    def witness(
        self, *, binding: dict[str, object], replay_key: str | None = None, committed_at: datetime | None = None
    ) -> bytes:
        key = replay_key or subject.derive_physical_full_matrix_v4_witness_successor_transition_replay_key_sha256(
            effect_start=self.effect_start,
            predecessor_binding=self.predecessor,
        )
        return self._signed(
            {
                "schema": subject.PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_WITNESS_ADMISSION_SCHEMA,
                "version": 1,
                "kind": "witness-durable-successor-transition-admission",
                "signer_role": "witness-durable-successor-transition-ledger",
                "transition_id": binding["transition_id"],
                "transition_nonce": binding["transition_nonce"],
                "transition_binding_sha256": hashlib.sha256(canonical_json_bytes(binding)).hexdigest(),
                "replay_key_sha256": key,
                "predecessor_writer_epoch": self.predecessor.writer_epoch,
                "predecessor_writer_lease_id": self.predecessor.writer_lease_id,
                "successor_writer_holder_site": self.successor.writer_holder_site,
                "successor_writer_epoch": self.successor.writer_epoch,
                "successor_writer_lease_id": self.successor.writer_lease_id,
                "successor_witness_transition_id": self.successor.witness_transition_id,
                "successor_witnessed_term_proof_sha256": self.successor.witnessed_term_proof_sha256,
                "successor_readiness_binding_sha256": self.successor.readiness_binding_sha256,
                "anti_replay_namespace": self.policy.anti_replay_namespace,
                "anti_replay_mode": "witness-durable-single-use-successor-transition-v1",
                "witness_ledger_scope_sha256": self.policy.witness_ledger_scope_sha256,
                "admission_id": "witness-successor-admission-000001",
                "admission_nonce": "WitnessSuccessorAdmissionNonce001",
                "committed_at": (committed_at or (self.now - timedelta(seconds=1))).isoformat().replace("+00:00", "Z"),
                "expires_at": binding["expires_at"],
                "witness_ledger_sequence": 44,
                "witness_ledger_entry_sha256": _hash(f"{self.phase.name}-witness-entry"),
                "witness_ledger_previous_head_sha256": _hash(f"{self.phase.name}-witness-prev"),
            },
            key=self.witness_key,
            domain=subject._WITNESS_DOMAIN,
        )

    def evidence(self, *, binding: dict[str, object] | None = None):
        actual = self.binding_mapping() if binding is None else binding
        return self.receipt(binding=actual, executor=True), self.receipt(binding=actual, executor=False), self.witness(binding=actual)

    def verify(self, evidence=None, *, config=None, now=None):
        executor, observer, witness = self.evidence() if evidence is None else evidence
        return subject.verify_physical_full_matrix_v4_witness_successor_transition(
            executor_receipt=executor,
            observer_receipt=observer,
            witness_admission_receipt=witness,
            config=self.config if config is None else config,
            now=self.now if now is None else now,
        )


class PhysicalFullMatrixV4WitnessSuccessorTransitionTests(unittest.TestCase):
    def test_phase4_and_phase7_cross_pin_the_only_valid_direction_and_are_non_authorizing(self) -> None:
        for phase_name, expected_holder in (
            ("witness-promote-ir-v2", "webapp_ir"),
            ("witness-restore-fi-writer-v2", "webapp_fi"),
        ):
            with self.subTest(phase=phase_name):
                fixture = _TransitionFixture(phase_name)
                value = fixture.verify()
                self.assertIs(
                    value,
                    subject.require_verified_physical_full_matrix_v4_witness_successor_transition(
                        value, config=fixture.config, now=fixture.now
                    ),
                )
                self.assertEqual(expected_holder, value.successor_binding.writer_holder_site)
                self.assertGreater(
                    value.successor_binding.writer_epoch,
                    value.predecessor_binding.writer_epoch,
                )
                self.assertEqual(fixture.anchor, value.effect_start_anchor)
                self.assertFalse(value.writer_authorized)
                self.assertFalse(value.promotion_authorized)
                self.assertFalse(value.traffic_switch_authorized)
                self.assertFalse(value.execution_authorized)
                self.assertFalse(value.full_matrix_authorized)
                self.assertFalse(value.phase_completion_evidenced)
                self.assertFalse(value.next_phase_start_authorized)

    def test_nonmonotonic_or_wrong_direction_successors_are_rejected_before_receipt_parsing(self) -> None:
        fixture = _TransitionFixture("witness-promote-ir-v2")
        nonmonotonic = replace(fixture.successor, writer_epoch=fixture.predecessor.writer_epoch)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "CONFIG_SUCCESSOR_INVALID"
        ):
            fixture.verify(config=replace(fixture.config, expected_successor_binding=nonmonotonic))
        wrong_direction = replace(
            fixture.successor,
            writer_holder_site="webapp_fi",
            source_site="webapp_fi",
            destination_site="webapp_ir",
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "CONFIG_INVALID"
        ):
            fixture.verify(config=replace(fixture.config, expected_successor_binding=wrong_direction))

    def test_effect_identity_anchor_and_signed_cross_effect_substitution_fail_closed(self) -> None:
        fixture = _TransitionFixture("witness-promote-ir-v2")
        forged = replace(
            fixture.effect_start,
            journaled_effect_start_identity_sha256=_hash("forged-start"),
        )
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "CONFIG_INVALID"):
            fixture.verify(config=replace(fixture.config, expected_effect_start=forged))
        changed = copy.deepcopy(fixture.binding_mapping())
        anchor = changed["effect_start_anchor"]
        assert isinstance(anchor, dict)
        alternate_effect = _hash("other-phase4-effect")
        anchor["effect_key"] = alternate_effect
        anchor["journaled_effect_start_identity_sha256"] = (
            driver.derive_physical_full_matrix_v4_effect_start_identity_sha256(
                driver.PhysicalFullMatrixV4EffectStart(
                    run_id=fixture.effect_start.run_id,
                    plan_sha256=fixture.effect_start.plan_sha256,
                    sequence=fixture.effect_start.phase.sequence,
                    phase_request_sha256=fixture.effect_start.phase_request_sha256,
                    effect_key=alternate_effect,
                    claim_id=fixture.effect_start.claim_id,
                )
            )
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "EXPECTED_PINS_MISMATCH"
        ):
            fixture.verify(fixture.evidence(binding=changed))

    def test_executor_observer_signer_separation_and_exact_binding_are_mandatory(self) -> None:
        fixture = _TransitionFixture("witness-promote-ir-v2")
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "DISABLED"
        ):
            fixture.verify(config=replace(fixture.config, enabled=False))
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "SIGNER_SEPARATION"
        ):
            fixture.verify(
                config=replace(
                    fixture.config,
                    observer_signer_public_key=fixture.config.executor_signer_public_key,
                )
            )
        binding = fixture.binding_mapping()
        executor = fixture.receipt(binding=binding, executor=True)
        alternate = fixture.binding_mapping(transition_id="v4-successor-transition-000099")
        observer = fixture.receipt(binding=alternate, executor=False)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "EXECUTOR_OBSERVER_MISMATCH"
        ):
            fixture.verify((executor, observer, fixture.witness(binding=binding)))

        # A record bearing the right schema and transition hash is still not
        # executor evidence when signed by the independent observer key.
        forged_executor = fixture._signed(
            {
                "schema": subject.PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_EXECUTOR_RECEIPT_SCHEMA,
                "version": 1,
                "kind": "target-root-successor-transition-executor-evidence",
                "signer_role": "target-root-successor-transition-executor",
                "transition_binding": binding,
                "transition_binding_sha256": hashlib.sha256(
                    canonical_json_bytes(binding)
                ).hexdigest(),
            },
            key=fixture.observer_key,
            domain=subject._EXECUTOR_DOMAIN,
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError,
            "EXECUTOR_RECEIPT_INVALID",
        ):
            fixture.verify(
                (
                    forged_executor,
                    fixture.receipt(binding=binding, executor=False),
                    fixture.witness(binding=binding),
                )
            )

    def test_readiness_is_fresh_pre_witness_evidence_not_an_authority_or_precredit(self) -> None:
        fixture = _TransitionFixture("witness-restore-fi-writer-v2")
        authority_forgery = replace(fixture.readiness, writer_authorized=True)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "CONFIG_INVALID"):
            fixture.verify(
                config=replace(fixture.config, expected_successor_readiness=authority_forgery)
            )
        binding = fixture.binding_mapping()
        executor = fixture.receipt(binding=binding, executor=True)
        observer = fixture.receipt(binding=binding, executor=False)
        early_witness = fixture.witness(
            binding=binding, committed_at=fixture.now - timedelta(seconds=3)
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "ORDER_INVALID"
        ):
            fixture.verify((executor, observer, early_witness))

    def test_replay_key_excludes_caller_transition_id_and_wrong_key_is_rejected(self) -> None:
        fixture = _TransitionFixture("witness-promote-ir-v2")
        first = subject.derive_physical_full_matrix_v4_witness_successor_transition_replay_key_sha256(
            effect_start=fixture.effect_start, predecessor_binding=fixture.predecessor
        )
        self.assertEqual(
            first,
            subject.derive_physical_full_matrix_v4_witness_successor_transition_replay_key_sha256(
                effect_start=fixture.effect_start, predecessor_binding=fixture.predecessor
            ),
        )
        binding = fixture.binding_mapping(transition_id="v4-successor-transition-000099")
        evidence = fixture.evidence(binding=binding)
        self.assertIsInstance(evidence[2], bytes)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "REPLAY_KEY_MISMATCH"
        ):
            fixture.verify(
                (
                    evidence[0],
                    evidence[1],
                    fixture.witness(binding=binding, replay_key=_hash("wrong-replay")),
                )
            )

    def test_start_bound_evidence_cannot_assert_completion_or_start_the_next_phase(self) -> None:
        fixture = _TransitionFixture("witness-promote-ir-v2")
        binding = fixture.binding_mapping()
        # The exact canonical wire shape has no continuation or completion
        # claim.  A later P5/P8-like admission must consume a separately
        # typed completion receipt/anchor bridge, never decorate P4/P7 start
        # evidence with a caller-selected next phase.
        binding["next_phase"] = "ir-writer-v2-witness-roundtrip-strict-ack-matrix"
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError,
            "EXECUTOR_RECEIPT_INVALID",
        ):
            fixture.verify(fixture.evidence(binding=binding))

        value = fixture.verify()
        object.__setattr__(value, "phase_completion_evidenced", True)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "TAMPERED"
        ):
            subject.require_verified_physical_full_matrix_v4_witness_successor_transition(
                value, config=fixture.config, now=fixture.now
            )

    def test_expiry_tampering_and_manual_construction_fail_closed(self) -> None:
        fixture = _TransitionFixture("witness-promote-ir-v2")
        value = fixture.verify()
        with self.assertRaises(TypeError):
            copy.copy(value)
        with self.assertRaises(TypeError):
            copy.deepcopy(value)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "STALE_OR_EXPIRED"):
            subject.require_verified_physical_full_matrix_v4_witness_successor_transition(
                value, config=fixture.config, now=fixture.now + timedelta(seconds=91)
            )
        object.__setattr__(value, "writer_authorized", True)
        with self.assertRaisesRegex(subject.PhysicalFullMatrixV4WitnessSuccessorTransitionError, "TAMPERED"):
            subject.require_verified_physical_full_matrix_v4_witness_successor_transition(
                value, config=fixture.config, now=fixture.now
            )
        with self.assertRaises(TypeError):
            subject.VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition(capability=object())

    def test_module_has_no_v1_lease_or_protocol_runtime_fallback(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("physical_operational_failover_v1", source)
        self.assertNotIn("production_writer_lease", source)
        self.assertNotIn("physical_postgres_promotion_coordinator", source)
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
