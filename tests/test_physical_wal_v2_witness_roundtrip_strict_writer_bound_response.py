"""Focused no-I/O tests for the isolated Gen2 V1-bound V2 response contract."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pickle
import unittest
from unittest.mock import patch
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1_v2_writer_term_bridge as bridge
from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_response as subject
from core import physical_wal_v2_witness_roundtrip_strict_writer_response as legacy


NOW = datetime(2034, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
RELEASE = "a" * 40


def _id(prefix: str) -> str:
    return prefix + "-" + "x" * 24


def _sha(letter: str) -> str:
    return letter * 64


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


class PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.keys = [Ed25519PrivateKey.generate() for _ in range(10)]
        self.base_configuration_sha256 = _sha("b")
        self.legacy_config = (
            legacy.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig(
                enabled=True,
                local_commit_signer_public_key=_public(self.keys[9]),
                maximum_evidence_age_seconds=30,
            )
        )
        self.bridge_config = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig(
            enabled=True,
            cluster_id="gold-trade-three-site-prod",
            local_site="webapp_fi",
            release_sha=RELEASE,
            generation_id=_id("generation"),
            expected_v1_revalidator_configuration_sha256=_sha("a"),
            expected_v2_strict_writer_configuration_sha256=self.base_configuration_sha256,
            expected_v2_context_sha256=_sha("c"),
            expected_v2_activation_mode="normal_fi_writer",
            expected_v2_stream_generation_id="stream-gen-0001",
            bridge_signer_public_key=_public(self.keys[0]),
            bridge_signer_key_id=_id("bridge-key"),
            v1_current_term_signer_public_key=_public(self.keys[1]),
            v1_promotion_signer_public_key=_public(self.keys[2]),
            v2_witness_public_key=_public(self.keys[3]),
            v2_fi_outbox_public_key=_public(self.keys[4]),
            v2_ir_recovery_exporter_public_key=_public(self.keys[5]),
            v2_ir_durable_assertion_public_key=_public(self.keys[6]),
            v2_remote_source_public_key=_public(self.keys[7]),
            v2_remote_destination_public_key=_public(self.keys[8]),
            v2_local_commit_signer_public_key=_public(self.keys[9]),
            safety_margin_seconds=5,
            maximum_certificate_age_seconds=30,
        )
        self.config = subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig(
            legacy_response_config=self.legacy_config,
            bridge_config=self.bridge_config,
            enabled=True,
            maximum_evidence_age_seconds=20,
        )
        self.base = legacy.PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction(
            schema=legacy.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA,
            configuration_sha256=self.base_configuration_sha256,
            atomic_commit_boundary=legacy.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY,
            commit_id="v2-witness-strict-writer-" + _sha("4"),
            attestation_sha256=_sha("5"),
            ir_durable_assertion_sha256=_sha("6"),
            context_certificate_sha256=_sha("7"),
            context_sha256=_sha("c"),
            source_envelope_sha256=_sha("8"),
            source_request_sha256=_sha("9"),
            destination_receipt_sha256=_sha("a"),
            durable_ledger_entry_sha256=_sha("b"),
            target_recovery_evidence_sha256=_sha("c"),
            readback_attestation_sha256=_sha("d"),
            stage_receipt_sha256=_sha("e"),
            witness_sequence=7,
            witness_ledger_entry_sha256=_sha("f"),
            witness_ledger_previous_head_sha256="0" * 64,
            witness_ledger_binding_sha256=_sha("1"),
            writer_holder_site="webapp_fi",
            writer_epoch=17,
            writer_lease_id=_id("lease"),
            witnessed_term_proof_sha256=_sha("2"),
            witness_transition_id=_id("witness-transition"),
            activation_mode="normal_fi_writer",
            activation_stream_generation_id="stream-gen-0001",
            activation_route_artifact_sha256=_sha("3"),
            activation_source_cutover_attestation_sha256=_sha("4"),
            activation_receiver_permit_sha256=_sha("5"),
            issued_at=NOW,
        )
        # Tests stub only the legacy opaque handoff; all bridge issuance,
        # canonical certificate verification, binding, and Gen2 signing are
        # real pure Ed25519 code.  A raw legacy instruction is never passed to
        # the Gen2 response itself.
        self.fake_legacy_prepared = (
            legacy.PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse(
                instruction=self.base,
                capability=legacy._PREPARED_CAPABILITY,
            )
        )

    def _bridge_intent(self):
        end = NOW + timedelta(seconds=25)
        current = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeCurrentTermProvenance(
            attestation_sha256=_sha("d"),
            attestation_id=_id("v1-attestation"),
            revalidation_id=_id("revalidation"),
            configuration_sha256=_sha("a"),
            reservation_id=_id("reservation"),
            request_sha256=_sha("e"),
            ledger_schema="gold-trade-v1-witness-ledger-v1",
            ledger_version=9,
            ledger_head_sha256=_sha("f"),
            ledger_entry_sha256=_sha("f"),
            ledger_previous_head_sha256="0" * 64,
            ledger_state_sha256=_sha("1"),
            ledger_phase="fi-active",
            active_term_sha256=_sha("2"),
            holder_site="webapp_fi",
            writer_epoch=self.base.writer_epoch,
            writer_lease_id=self.base.writer_lease_id,
            witness_transition_id=self.base.witness_transition_id,
            witnessed_term_proof_sha256=self.base.witnessed_term_proof_sha256,
            attestation_issued_at=NOW - timedelta(seconds=1),
            attestation_expires_at=end,
            term_issued_at=NOW - timedelta(seconds=2),
            term_expires_at=end,
        )
        admission = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeV1Admission(
            cluster_id=self.bridge_config.cluster_id or "",
            local_site="webapp_fi",
            release_sha=RELEASE,
            generation_id=self.bridge_config.generation_id or "",
            operation_kind="transaction_commit",
            prior_revision=7,
            next_revision=8,
            fence_generation=4,
            evidence_id=current.attestation_id,
            revalidation_id=current.revalidation_id,
            writer_epoch=self.base.writer_epoch,
            writer_lease_id=self.base.writer_lease_id,
            opened_at=NOW,
            admitted_at=NOW,
            term_evidence_issued_at=current.attestation_issued_at,
            term_evidence_expires_at=current.attestation_expires_at,
        )
        v2 = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeV2Instruction(
            strict_schema=self.base.schema,
            configuration_sha256=self.base.configuration_sha256,
            atomic_commit_boundary=self.base.atomic_commit_boundary,
            commit_id=self.base.commit_id,
            attestation_sha256=self.base.attestation_sha256,
            context_sha256=self.base.context_sha256,
            writer_holder_site=self.base.writer_holder_site,
            writer_epoch=self.base.writer_epoch,
            writer_lease_id=self.base.writer_lease_id,
            witnessed_term_proof_sha256=self.base.witnessed_term_proof_sha256,
            witness_transition_id=self.base.witness_transition_id,
            activation_mode=self.base.activation_mode,
            activation_stream_generation_id=self.base.activation_stream_generation_id,
            activation_route_artifact_sha256=self.base.activation_route_artifact_sha256,
            activation_source_cutover_attestation_sha256=(
                self.base.activation_source_cutover_attestation_sha256
            ),
            activation_receiver_permit_sha256=(
                self.base.activation_receiver_permit_sha256
            ),
            attestation_issued_at=NOW - timedelta(seconds=1),
            attestation_expires_at=end,
            term_issued_at=current.term_issued_at,
            term_expires_at=current.term_expires_at,
        )
        return bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeIntent(
            v1_admission=admission,
            v1_current_term=current,
            v2_instruction=v2,
        )

    def _bridge_bound(self):
        intent = self._bridge_intent()
        raw = bridge.issue_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(
            config=self.bridge_config,
            intent=intent,
            private_key=self.keys[0],
            now=NOW,
            expires_at=NOW + timedelta(seconds=20),
        )
        verified = bridge.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(
            value=raw,
            config=self.bridge_config,
            now=NOW,
        )
        parent = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt(
            commit_id=str(uuid.UUID("12345678-1234-4234-9234-123456789abc")),
            commit_sha256=_sha("9"),
            receipt_sha256=_sha("a"),
            cluster_id=intent.v1_admission.cluster_id,
            local_site=intent.v1_admission.local_site,
            release_sha=intent.v1_admission.release_sha,
            generation_id=intent.v1_admission.generation_id,
            prior_revision=intent.v1_admission.prior_revision,
            next_revision=intent.v1_admission.next_revision,
            fence_generation=intent.v1_admission.fence_generation,
            writer_epoch=intent.v1_admission.writer_epoch,
            writer_lease_id=intent.v1_admission.writer_lease_id,
            evidence_id=intent.v1_admission.evidence_id,
            revalidation_id=intent.v1_admission.revalidation_id,
            admitted_at=intent.v1_admission.admitted_at,
        )
        return bridge.bind_physical_operational_failover_v1_v2_writer_term_bridge_parent(
            certificate=verified,
            parent=parent,
            config=self.bridge_config,
            now=NOW,
        ), verified

    def _prepared_and_bound(self):
        bridge_bound, verified = self._bridge_bound()
        with (
            patch.object(subject, "_trusted_now", return_value=NOW),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=self.base,
            ),
        ):
            prepared = subject.prepare_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
                config=self.config,
                v2_prepared=self.fake_legacy_prepared,
            )
            bound = subject.bind_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
                prepared,
                bridge_bound=bridge_bound,
                config=self.config,
            )
        return prepared, bound, bridge_bound, verified

    def _complete(self):
        prepared, bound, bridge_bound, verified = self._prepared_and_bound()
        with (
            patch.object(subject, "_trusted_now", return_value=NOW),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=self.base,
            ),
        ):
            receipt = subject.sign_bound_physical_wal_v2_witness_roundtrip_strict_writer_runtime_receipt(
                bound,
                config=self.config,
                local_commit_private_key=self.keys[9],
                local_commit_record_id=_id("local-commit"),
                local_response_id=_id("local-response"),
                committed_at=NOW,
            )
            observation = subject.finalize_bound_physical_wal_v2_witness_roundtrip_strict_writer_response(
                bound,
                config=self.config,
                runtime_receipt=receipt,
            )
        return prepared, bound, bridge_bound, verified, receipt, observation

    def test_full_gen2_flow_signs_all_v2_v1_and_bridge_pins(self) -> None:
        _prepared, bound, _bridge_bound, _verified, receipt, observation = self._complete()
        decoded = json.loads(receipt.decode("ascii"))
        instruction = bound.instruction
        self.assertEqual(
            subject.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_COMMIT_RECEIPT_SCHEMA,
            decoded["schema"],
        )
        self.assertEqual(2, decoded["version"])
        self.assertEqual(instruction.commit_id, decoded["commit_id"])
        self.assertEqual(instruction.v2_base_commit_id, decoded["v2_base_commit_id"])
        self.assertEqual(
            instruction.v1_writer_admission_commit_id,
            decoded["v1_writer_admission_commit_id"],
        )
        self.assertEqual(
            instruction.v1_parent_term_expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            decoded["v1_parent_term_expires_at"],
        )
        self.assertEqual(
            instruction.v1_v2_writer_term_bridge_parent_binding_sha256,
            decoded["v1_v2_writer_term_bridge_parent_binding_sha256"],
        )
        self.assertEqual(
            instruction.canonical_v1_v2_writer_term_bridge_certificate,
            base64.b64decode(
                decoded["canonical_v1_v2_writer_term_bridge_certificate_base64"]
            ),
        )
        self.assertTrue(instruction.commit_id.startswith("v2-witness-strict-writer-g2-"))
        self.assertEqual(
            "v2-witness-consume-g2-" + self.base.attestation_sha256,
            observation.attestation_consumption_id,
        )
        with (
            patch.object(subject, "_trusted_now", return_value=NOW),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=self.base,
            ),
        ):
            projection = subject.project_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation(
                observation,
                config=self.config,
            )
        self.assertEqual(observation.observation_sha256, projection.observation_sha256)
        self.assertEqual(instruction, projection.instruction)

    def test_disabled_raw_gen1_and_gen1_receipt_have_no_fallback(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
            "CONFIG_DISABLED",
        ):
            subject.prepare_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
                config=replace(self.config, enabled=False),
                v2_prepared=self.fake_legacy_prepared,
            )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
            "BASE_PREPARED_CAPABILITY_REQUIRED",
        ):
            subject.prepare_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
                config=self.config,
                v2_prepared=self.base,  # type: ignore[arg-type]
            )
        _prepared, bound, _bridge_bound, _verified = self._prepared_and_bound()
        # A recognizable Gen1-shaped signed object cannot satisfy the exact
        # Gen2 schema/domain/parent/bridge field set.
        gen1_shaped = json.dumps(
            {
                "schema": legacy.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_COMMIT_RECEIPT_SCHEMA,
                "version": 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        with (
            patch.object(subject, "_trusted_now", return_value=NOW),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=self.base,
            ),
            self.assertRaisesRegex(
                subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
                "RUNTIME_RECEIPT",
            ),
        ):
            subject.finalize_bound_physical_wal_v2_witness_roundtrip_strict_writer_response(
                bound,
                config=self.config,
                runtime_receipt=gen1_shaped,
            )

    def test_opaque_capabilities_cannot_be_serialized_or_forged(self) -> None:
        prepared, bound, _bridge_bound, _verified, _receipt, observation = self._complete()
        for value in (prepared, bound, observation):
            with self.assertRaises(TypeError):
                pickle.dumps(value)
        forged = object.__new__(
            subject.BoundPreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse
        )
        object.__setattr__(forged, "instruction", bound.instruction)
        object.__setattr__(forged, "_capability", object())
        with self.assertRaisesRegex(
            subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
            "BINDING_CAPABILITY_REQUIRED",
        ):
            subject.require_bound_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                forged,
                config=self.config,
            )

    def test_bridge_and_parent_tampering_fail_before_gen2_signing(self) -> None:
        prepared, _bound, bridge_bound, verified = self._prepared_and_bound()
        object.__setattr__(bridge_bound, "parent_commit_sha256", _sha("e"))
        with (
            patch.object(subject, "_trusted_now", return_value=NOW),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=self.base,
            ),
            self.assertRaisesRegex(
                subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
                "BRIDGE_INVALID",
            ),
        ):
            subject.bind_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
                prepared,
                bridge_bound=bridge_bound,
                config=self.config,
            )
        # The verified certificate itself is opaque state; altering its public
        # bytes cannot become alternate certificate authority either.
        bridge_bound, verified = self._bridge_bound()
        object.__setattr__(verified, "canonical_certificate", b"{}")
        with (
            patch.object(subject, "_trusted_now", return_value=NOW),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=self.base,
            ),
            self.assertRaisesRegex(
                subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
                "BRIDGE_INVALID",
            ),
        ):
            subject.bind_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
                prepared,
                bridge_bound=bridge_bound,
                config=self.config,
            )

    def test_signed_parent_and_bridge_receipt_tamper_rejected(self) -> None:
        _prepared, bound, _bridge_bound, _verified, receipt, _observation = self._complete()
        for field, value in (
            ("v1_writer_admission_commit_sha256", _sha("e")),
            ("v1_v2_writer_term_bridge_parent_binding_sha256", _sha("f")),
            (
                "canonical_v1_v2_writer_term_bridge_certificate_base64",
                base64.b64encode(b"forged-certificate").decode("ascii"),
            ),
        ):
            decoded = json.loads(receipt.decode("ascii"))
            decoded[field] = value
            tampered = json.dumps(
                decoded,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            with (
                patch.object(subject, "_trusted_now", return_value=NOW),
                patch.object(
                    subject.legacy,
                    "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                    return_value=self.base,
                ),
                self.assertRaisesRegex(
                    subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
                    "BINDING_MISMATCH",
                ),
            ):
                subject.finalize_bound_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    bound,
                    config=self.config,
                    runtime_receipt=tampered,
                )

    def test_config_term_and_expiry_change_fail_closed(self) -> None:
        _prepared, bound, _bridge_bound, _verified = self._prepared_and_bound()
        with (
            patch.object(subject, "_trusted_now", return_value=NOW),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=self.base,
            ),
            self.assertRaisesRegex(
                subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
                "CONFIG_MISMATCH",
            ),
        ):
            subject.require_bound_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                bound,
                config=replace(self.config, maximum_evidence_age_seconds=19),
            )
        changed_term = replace(self.base, writer_epoch=18)
        with (
            patch.object(subject, "_trusted_now", return_value=NOW),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=changed_term,
            ),
            self.assertRaisesRegex(
                subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
                "BASE_INPUT_CHANGED",
            ),
        ):
            subject.require_bound_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                bound,
                config=self.config,
            )
        with (
            patch.object(subject, "_trusted_now", return_value=NOW + timedelta(seconds=16)),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=self.base,
            ),
            self.assertRaisesRegex(
                subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
                "BRIDGE_INVALID",
            ),
        ):
            subject.require_bound_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                bound,
                config=self.config,
            )

    def test_key_and_signing_domain_mismatch_rejected(self) -> None:
        _prepared, bound, _bridge_bound, _verified = self._prepared_and_bound()
        with (
            patch.object(subject, "_trusted_now", return_value=NOW),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=self.base,
            ),
            self.assertRaisesRegex(
                subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
                "SIGNER_KEY_MISMATCH",
            ),
        ):
            subject.sign_bound_physical_wal_v2_witness_roundtrip_strict_writer_runtime_receipt(
                bound,
                config=self.config,
                local_commit_private_key=self.keys[8],
                local_commit_record_id=_id("local-commit"),
                local_response_id=_id("local-response"),
                committed_at=NOW,
            )
        instruction = bound.instruction
        unsigned = subject._runtime_unsigned(
            instruction,
            local_commit_record_id=_id("local-commit"),
            local_response_id=_id("local-response"),
            attestation_consumption_id=(
                "v2-witness-consume-g2-" + instruction.attestation_sha256
            ),
            committed_at=NOW,
        )
        forged = dict(unsigned)
        forged["signature_base64"] = base64.b64encode(
            self.keys[9].sign(
                b"wrong-gen2-domain\x00" + subject._canonical(unsigned, code="test")
            )
        ).decode("ascii")
        forged_bytes = subject._canonical(forged, code="test")
        with (
            patch.object(subject, "_trusted_now", return_value=NOW),
            patch.object(
                subject.legacy,
                "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
                return_value=self.base,
            ),
            self.assertRaisesRegex(
                subject.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
                "SIGNATURE_INVALID",
            ),
        ):
            subject.finalize_bound_physical_wal_v2_witness_roundtrip_strict_writer_response(
                bound,
                config=self.config,
                runtime_receipt=forged_bytes,
            )


if __name__ == "__main__":
    unittest.main()
