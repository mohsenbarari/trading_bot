from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta
import inspect
import pickle
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2_remote_ack_receiver_ledger as receiver_ledger
from core import physical_wal_v2_witness_roundtrip_contract as roundtrip
from core.physical_wal_v2_remote_ack import (
    PhysicalWalV2RemoteAckError,
    verify_physical_wal_v2_remote_ack_request,
)
from core.physical_wal_v2_remote_ack_receiver_ledger import (
    PhysicalWalV2RemoteAckReceiverLedgerError,
    require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt,
)
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW
from tests import test_physical_wal_v2_remote_ack as remote_ack_tests


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalWalV2WitnessRoundtripContractTests(unittest.TestCase):
    """Portable grammar tests; all durable state remains in upstream fixtures."""

    def setUp(self) -> None:
        self.fixture = remote_ack_tests.PhysicalWalV2RemoteAckReceiverLedgerTests(
            "runTest"
        )
        self.fixture.setUp()
        self.ir_recovery_exporter = Ed25519PrivateKey.generate()
        self.fi_outbox = Ed25519PrivateKey.generate()
        self.ir_assertion = Ed25519PrivateKey.generate()
        self.witness = Ed25519PrivateKey.generate()
        self.config = roundtrip.PhysicalWalV2WitnessRoundtripConfig(
            remote_ack_config=self.fixture.remote_config,
            ir_recovery_exporter_public_key=_public(self.ir_recovery_exporter),
            fi_outbox_public_key=_public(self.fi_outbox),
            ir_durable_assertion_public_key=_public(self.ir_assertion),
            witness_public_key=_public(self.witness),
            enabled=True,
            maximum_evidence_age_seconds=45,
        )
        self.live = roundtrip._ActivationFacts(
            activation_mode="normal_fi_writer",
            activation_stream_generation_id=self.fixture.target_recovery.stream_generation_id,
            activation_route_artifact_sha256="a" * 64,
            activation_source_cutover_attestation_sha256="b" * 64,
            activation_receiver_permit_sha256="c" * 64,
            witness_transition_id=self.fixture.target_recovery.witness_transition_id,
        )

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _recovery_export(self, *, now=NOW, expires_at=None) -> dict[str, object]:
        return roundtrip.build_physical_wal_v2_witness_recovery_export(
            config=self.config,
            context=self.fixture.context,
            target_recovery_evidence=self.fixture.target_recovery,
            export_id="v2-witness-export-000001",
            export_nonce="E" * 22,
            expires_at=expires_at or now + timedelta(seconds=40),
            ir_recovery_exporter_signer=self.ir_recovery_exporter,
            now=now,
        )

    def _certificate(
        self,
        recovery_export: dict[str, object],
        *,
        now=NOW,
        witness_sequence=1,
        ledger_entry="1" * 64,
        previous_head="0" * 64,
        binding="2" * 64,
        expires_at=None,
    ) -> dict[str, object]:
        verified_export = roundtrip.verify_physical_wal_v2_witness_recovery_export(
            recovery_export,
            config=self.config,
            now=now,
        )
        with patch.object(roundtrip, "_check_live_activation", return_value=self.live):
            return roundtrip.build_physical_wal_v2_witness_context_certificate(
                config=self.config,
                recovery_export=verified_export,
                witness_sequence=witness_sequence,
                witness_ledger_entry_sha256=ledger_entry,
                witness_ledger_previous_head_sha256=previous_head,
                witness_ledger_binding_sha256=binding,
                certificate_id="v2-witness-context-cert-000001",
                certificate_nonce="C" * 22,
                expires_at=expires_at or now + timedelta(seconds=35),
                witnessed_term=object(),
                activation=object(),
                witness_signer=self.witness,
                now=now,
            )

    def _source_envelope(
        self,
        certificate: dict[str, object],
        *,
        outbox_id="v2-witness-outbox-000001",
        outbox_nonce="O" * 22,
    ) -> dict[str, object]:
        verified_certificate = roundtrip.verify_physical_wal_v2_witness_context_certificate(
            certificate,
            config=self.config,
            now=NOW,
        )
        return roundtrip.build_physical_wal_v2_witness_source_envelope(
            config=self.config,
            context_certificate=verified_certificate,
            source_request=self.fixture.request.canonical_request,
            outbox_id=outbox_id,
            outbox_nonce=outbox_nonce,
            expires_at=NOW + timedelta(seconds=25),
            fi_outbox_signer=self.fi_outbox,
            now=NOW,
        )

    def _assertion(
        self, envelope: dict[str, object]
    ) -> tuple[dict[str, object], object]:
        verified_envelope = roundtrip.verify_physical_wal_v2_witness_source_envelope(
            envelope,
            config=self.config,
            now=NOW,
        )
        with patch.object(receiver_ledger, "_trusted_now", return_value=NOW):
            issued = self.fixture._issue(destination_signer=None)
            assertion = roundtrip.build_physical_wal_v2_witness_ir_durable_assertion(
                config=self.config,
                source_envelope=verified_envelope,
                remote_ack_evidence=issued.remote_ack_evidence,
                receiver_recovery_evidence=self.fixture.recovery,
                target_recovery_evidence=self.fixture.target_recovery,
                receiver_ledger_receipt=issued.receipt,
                receiver_ledger_config=self.fixture.ledger_config,
                assertion_id="v2-witness-assertion-000001",
                assertion_nonce="A" * 22,
                expires_at=NOW + timedelta(seconds=20),
                ir_durable_assertion_signer=self.ir_assertion,
                now=NOW,
            )
        return assertion, issued

    def _attestation(
        self, assertion: dict[str, object], *, now=NOW, live=None, **changes: object
    ) -> dict[str, object]:
        verified_assertion = roundtrip.verify_physical_wal_v2_witness_ir_durable_assertion(
            assertion,
            config=self.config,
            now=now,
        )
        values: dict[str, object] = {
            "config": self.config,
            "ir_durable_assertion": verified_assertion,
            "mediation_id": "v2-witness-mediation-000001",
            "witness_sequence": 2,
            "witness_ledger_entry_sha256": "3" * 64,
            "witness_ledger_previous_head_sha256": "4" * 64,
            "witness_ledger_binding_sha256": "2" * 64,
            "attestation_id": "v2-witness-attestation-000001",
            "attestation_nonce": "T" * 22,
            "expires_at": now + timedelta(seconds=15),
            "witnessed_term": object(),
            "activation": object(),
            "witness_signer": self.witness,
            "now": now,
        }
        values.update(changes)
        with patch.object(roundtrip, "_check_live_activation", return_value=live or self.live):
            return roundtrip.build_physical_wal_v2_witness_roundtrip_attestation(**values)

    def _full_chain(self) -> tuple[dict[str, object], dict[str, object], dict[str, object], object]:
        export = self._recovery_export()
        certificate = self._certificate(export)
        envelope = self._source_envelope(certificate)
        assertion, issued = self._assertion(envelope)
        return certificate, envelope, assertion, issued

    def test_full_signed_roundtrip_and_v2_request_grammar(self) -> None:
        certificate, _envelope, assertion, _issued = self._full_chain()
        verified_certificate = roundtrip.verify_physical_wal_v2_witness_context_certificate(
            certificate,
            config=self.config,
            now=NOW,
        )
        request = roundtrip.build_physical_wal_v2_witness_source_request(
            config=self.config,
            context_certificate=verified_certificate,
            request_id="v2-witness-source-request-000001",
            request_nonce="Q" * 22,
            expires_at=NOW + timedelta(seconds=25),
            source_signer=self.fixture.source,
            now=NOW,
        )
        verified_request = verify_physical_wal_v2_remote_ack_request(
            source_request=request,
            config=self.fixture.remote_config,
            now=NOW,
        )
        self.assertEqual(self.fixture.context.context_sha256, verified_request.context_sha256)
        attestation = self._attestation(assertion)
        verified = roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            attestation,
            config=self.config,
            now=NOW,
        )
        projection = roundtrip.project_verified_physical_wal_v2_witness_roundtrip_attestation(
            verified,
            config=self.config,
            now=NOW,
        )
        self.assertEqual(verified_certificate.certificate_sha256, verified.context_certificate_sha256)
        self.assertEqual("2" * 64, projection.witness_ledger_binding_sha256)
        self.assertEqual("4" * 64, projection.witness_ledger_previous_head_sha256)
        self.assertEqual(2, projection.witness_sequence)

    def test_raw_context_and_forged_capabilities_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            roundtrip.PhysicalWalV2WitnessRoundtripError,
            "CONTEXT_CERTIFICATE_CAPABILITY_REQUIRED",
        ):
            roundtrip.build_physical_wal_v2_witness_source_request(
                config=self.config,
                context_certificate=self.fixture.context,
                request_id="v2-witness-source-request-000001",
                request_nonce="Q" * 22,
                expires_at=NOW + timedelta(seconds=25),
                source_signer=self.fixture.source,
                now=NOW,
            )
        export = self._recovery_export()
        certificate = self._certificate(export)
        verified = roundtrip.verify_physical_wal_v2_witness_context_certificate(
            certificate,
            config=self.config,
            now=NOW,
        )
        forged = replace(verified, witness_ledger_entry_sha256="f" * 64)
        with self.assertRaisesRegex(
            roundtrip.PhysicalWalV2WitnessRoundtripError,
            "CONTEXT_CERTIFICATE_CAPABILITY_REQUIRED",
        ):
            roundtrip.build_physical_wal_v2_witness_source_request(
                config=self.config,
                context_certificate=forged,
                request_id="v2-witness-source-request-000001",
                request_nonce="Q" * 22,
                expires_at=NOW + timedelta(seconds=25),
                source_signer=self.fixture.source,
                now=NOW,
            )

    def test_signed_stage_ledger_fields_and_final_chain_are_tamper_evident(self) -> None:
        certificate, _envelope, assertion, _issued = self._full_chain()
        for field_name, value in (
            ("witness_sequence", 99),
            ("witness_ledger_entry_sha256", "f" * 64),
            ("witness_ledger_previous_head_sha256", "e" * 64),
            ("witness_ledger_binding_sha256", "d" * 64),
        ):
            tampered = dict(certificate)
            tampered[field_name] = value
            with self.subTest(stage="context", field=field_name), self.assertRaisesRegex(
                roundtrip.PhysicalWalV2WitnessRoundtripError,
                "CONTEXT_CERTIFICATE_INVALID",
            ):
                roundtrip.verify_physical_wal_v2_witness_context_certificate(
                    tampered,
                    config=self.config,
                    now=NOW,
                )
        attestation = self._attestation(assertion)
        for field_name, value in (
            ("witness_sequence", 99),
            ("witness_ledger_entry_sha256", "f" * 64),
            ("witness_ledger_previous_head_sha256", "e" * 64),
            ("witness_ledger_binding_sha256", "d" * 64),
            ("context_certificate_sha256", "c" * 64),
        ):
            tampered = dict(attestation)
            tampered[field_name] = value
            with self.subTest(stage="roundtrip", field=field_name), self.assertRaisesRegex(
                roundtrip.PhysicalWalV2WitnessRoundtripError,
                "ATTESTATION_INVALID",
            ):
                roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
                    tampered,
                    config=self.config,
                    now=NOW,
                )

    def test_final_builder_rejects_term_activation_binding_or_sequence_flip(self) -> None:
        _certificate, _envelope, assertion, _issued = self._full_chain()
        flipped = replace(self.live, activation_mode="unexpected-promoted-writer")
        with self.assertRaisesRegex(
            roundtrip.PhysicalWalV2WitnessRoundtripError,
            "LIVE_ACTIVATION_CHANGED",
        ):
            self._attestation(assertion, live=flipped)
        with self.assertRaisesRegex(
            roundtrip.PhysicalWalV2WitnessRoundtripError,
            "ATTESTATION_SEQUENCE_INVALID",
        ):
            self._attestation(assertion, witness_sequence=1)
        with self.assertRaisesRegex(
            roundtrip.PhysicalWalV2WitnessRoundtripError,
            "ATTESTATION_LEDGER_BINDING_MISMATCH",
        ):
            self._attestation(assertion, witness_ledger_binding_sha256="d" * 64)

    def test_key_role_swap_clock_and_nested_wire_substitution_fail_closed(self) -> None:
        export = self._recovery_export()
        certificate = self._certificate(export)
        with self.assertRaisesRegex(
            roundtrip.PhysicalWalV2WitnessRoundtripError,
            "CONFIG_ROLE_KEY_REUSE",
        ):
            roundtrip.verify_physical_wal_v2_witness_context_certificate(
                certificate,
                config=replace(
                    self.config,
                    fi_outbox_public_key=self.config.ir_recovery_exporter_public_key,
                ),
                now=NOW,
            )
        with self.assertRaisesRegex(
            roundtrip.PhysicalWalV2WitnessRoundtripError,
            "EVIDENCE_STALE_OR_EXPIRED",
        ):
            roundtrip.verify_physical_wal_v2_witness_context_certificate(
                certificate,
                config=self.config,
                now=NOW + timedelta(seconds=36),
            )
        future_export = self._recovery_export(now=NOW + timedelta(seconds=10))
        future_certificate = self._certificate(
            future_export,
            now=NOW + timedelta(seconds=10),
        )
        with self.assertRaisesRegex(
            roundtrip.PhysicalWalV2WitnessRoundtripError,
            "EVIDENCE_STALE_OR_EXPIRED",
        ):
            roundtrip.verify_physical_wal_v2_witness_context_certificate(
                future_certificate,
                config=self.config,
                now=NOW,
            )
        _certificate, envelope, assertion, _issued = self._full_chain()
        other_envelope = self._source_envelope(
            _certificate,
            outbox_id="v2-witness-outbox-000002",
            outbox_nonce="N" * 22,
        )
        unsigned = dict(assertion)
        unsigned.pop("signature_base64")
        other_verified = roundtrip.verify_physical_wal_v2_witness_source_envelope(
            other_envelope,
            config=self.config,
            now=NOW,
        )
        unsigned["source_envelope_base64"] = roundtrip._b64_text(
            other_verified.canonical_envelope
        )
        resigned = roundtrip._make_message(
            unsigned,
            signer=self.ir_assertion,
            domain=roundtrip._IR_ASSERTION_DOMAIN,
            code="test",
        )
        with self.assertRaisesRegex(
            roundtrip.PhysicalWalV2WitnessRoundtripError,
            "IR_ASSERTION_INVALID",
        ):
            roundtrip.verify_physical_wal_v2_witness_ir_durable_assertion(
                resigned,
                config=self.config,
                now=NOW,
            )

    def test_local_opaque_receipt_never_serializes_or_reflects_as_wire(self) -> None:
        _certificate, envelope, _assertion, issued = self._full_chain()
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(issued.receipt)
        with self.assertRaisesRegex(PhysicalWalV2RemoteAckReceiverLedgerError, "CAPABILITY_REQUIRED"):
            require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt(
                replace(issued.receipt, durable_ledger_entry_sha256="f" * 64),
                config=self.fixture.ledger_config,
                source_request=self.fixture.request,
                receiver_recovery_evidence=self.fixture.recovery,
                target_recovery_evidence=self.fixture.target_recovery,
                remote_ack_evidence=issued.remote_ack_evidence,
                now=NOW,
            )
        verified_envelope = roundtrip.verify_physical_wal_v2_witness_source_envelope(
            envelope,
            config=self.config,
            now=NOW,
        )
        with self.assertRaisesRegex(
            roundtrip.PhysicalWalV2WitnessRoundtripError,
            "IR_LEDGER_CONFIG_REQUIRED",
        ):
            roundtrip.build_physical_wal_v2_witness_ir_durable_assertion(
                config=self.config,
                source_envelope=verified_envelope,
                remote_ack_evidence=issued.remote_ack_evidence,
                receiver_recovery_evidence=self.fixture.recovery,
                target_recovery_evidence=self.fixture.target_recovery,
                receiver_ledger_receipt=issued.receipt,
                receiver_ledger_config=object(),
                assertion_id="v2-witness-assertion-000001",
                assertion_nonce="A" * 22,
                expires_at=NOW + timedelta(seconds=20),
                ir_durable_assertion_signer=self.ir_assertion,
                now=NOW,
            )

    def test_contract_is_v2_only_and_has_no_transport_or_local_state_side_effect(self) -> None:
        source = inspect.getsource(roundtrip)
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        for forbidden in (
            "os",
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "boto",
            "urllib",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertNotIn("physical_wal_remote_ack", source)
        self.assertNotIn("physical_wal_v1", source)
        self.assertNotIn("connect(", source)
        self.assertNotIn("open(", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
