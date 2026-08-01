from __future__ import annotations

import ast
import base64
from dataclasses import replace
from datetime import timedelta
import hashlib
import inspect
import json
import unittest

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_contract as roundtrip
from core import physical_wal_v2_witness_roundtrip_delivery_contract as delivery
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW
from tests import test_physical_wal_v2_witness_roundtrip_contract as roundtrip_contract_tests


class PhysicalWalV2WitnessRoundtripDeliveryContractTests(unittest.TestCase):
    """The delivery grammar stays portable and pins each fixed one-way hop."""

    def setUp(self) -> None:
        self.fixture = roundtrip_contract_tests.PhysicalWalV2WitnessRoundtripContractTests(
            "runTest"
        )
        self.fixture.setUp()

        recovery_export = self.fixture._recovery_export()
        certificate = self.fixture._certificate(recovery_export)
        envelope = self.fixture._source_envelope(certificate)
        assertion, _issued = self.fixture._assertion(envelope)
        attestation = self.fixture._attestation(assertion)

        self.certificate = (
            roundtrip.verify_physical_wal_v2_witness_context_certificate(
                certificate,
                config=self.fixture.config,
                now=NOW,
            ).canonical_certificate
        )
        self.envelope = roundtrip.verify_physical_wal_v2_witness_source_envelope(
            envelope,
            config=self.fixture.config,
            now=NOW,
        ).canonical_envelope
        self.assertion = roundtrip.verify_physical_wal_v2_witness_ir_durable_assertion(
            assertion,
            config=self.fixture.config,
            now=NOW,
        ).canonical_assertion
        self.attestation = roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            attestation,
            config=self.fixture.config,
            now=NOW,
        ).canonical_attestation
        self.binding = delivery.build_physical_wal_v2_witness_roundtrip_delivery_binding(
            context_certificate=self.certificate,
            roundtrip_config=self.fixture.config,
            now=NOW,
        )

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _policy(self, mailbox: str, **changes: object):
        values: dict[str, object] = {
            "roundtrip_config": self.fixture.config,
            "binding": self.binding,
            "receiver_mailbox": mailbox,
            "enabled": True,
        }
        values.update(changes)
        return delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig(**values)

    @staticmethod
    def _payload(value: bytes) -> dict[str, object]:
        return json.loads(value.decode("ascii"))

    @staticmethod
    def _canonical_payload(value: dict[str, object]) -> bytes:
        return canonical_json_bytes(value)

    def test_all_four_fixed_mailboxes_verify_and_preserve_the_exact_chain(self) -> None:
        fi_to_witness = delivery.build_physical_wal_v2_witness_fi_to_witness_delivery(
            context_certificate=self.certificate,
            source_envelope=self.envelope,
            config=self._policy("fi-to-witness"),
            now=NOW,
        )
        witness_to_ir = delivery.build_physical_wal_v2_witness_witness_to_ir_delivery(
            context_certificate=self.certificate,
            source_envelope=self.envelope,
            config=self._policy("witness-to-ir"),
            now=NOW,
        )
        ir_to_witness = delivery.build_physical_wal_v2_witness_ir_to_witness_delivery(
            ir_durable_assertion=self.assertion,
            config=self._policy("ir-to-witness"),
            now=NOW,
        )
        witness_to_fi = delivery.build_physical_wal_v2_witness_witness_to_fi_delivery(
            roundtrip_attestation=self.attestation,
            config=self._policy("witness-to-fi"),
            now=NOW,
        )

        verified_fi_to_witness = (
            delivery.verify_physical_wal_v2_witness_fi_to_witness_delivery(
                fi_to_witness,
                config=self._policy("fi-to-witness"),
                now=NOW,
            )
        )
        verified_witness_to_ir = (
            delivery.verify_physical_wal_v2_witness_witness_to_ir_delivery(
                witness_to_ir,
                config=self._policy("witness-to-ir"),
                now=NOW,
            )
        )
        verified_ir_to_witness = delivery.verify_physical_wal_v2_witness_ir_to_witness_delivery(
            ir_to_witness,
            config=self._policy("ir-to-witness"),
            now=NOW,
        )
        verified_witness_to_fi = delivery.verify_physical_wal_v2_witness_witness_to_fi_delivery(
            witness_to_fi,
            config=self._policy("witness-to-fi"),
            now=NOW,
        )

        self.assertEqual("fi-to-witness", verified_fi_to_witness.mailbox)
        self.assertEqual("witness-to-ir", verified_witness_to_ir.mailbox)
        self.assertEqual("ir-to-witness", verified_ir_to_witness.mailbox)
        self.assertEqual("witness-to-fi", verified_witness_to_fi.mailbox)
        self.assertEqual(
            self.binding.context_certificate_sha256,
            verified_fi_to_witness.prior_delivery_sha256,
        )
        self.assertEqual(
            hashlib.sha256(fi_to_witness).hexdigest(),
            verified_witness_to_ir.prior_delivery_sha256,
        )
        self.assertEqual(
            hashlib.sha256(witness_to_ir).hexdigest(),
            verified_ir_to_witness.prior_delivery_sha256,
        )
        self.assertEqual(
            hashlib.sha256(ir_to_witness).hexdigest(),
            verified_witness_to_fi.prior_delivery_sha256,
        )

        fi_payload = self._payload(fi_to_witness)
        wi_payload = self._payload(witness_to_ir)
        ir_payload = self._payload(ir_to_witness)
        wf_payload = self._payload(witness_to_fi)
        self.assertEqual(
            ("webapp_fi", "witness", "fi-writer-source-outbox", "witness-fi-ingress"),
            tuple(fi_payload[key] for key in ("sender_site", "recipient_site", "sender_role", "recipient_role")),
        )
        self.assertEqual(
            ("witness", "webapp_ir", "witness-ir-egress", "ir-standby-ack-inbox"),
            tuple(wi_payload[key] for key in ("sender_site", "recipient_site", "sender_role", "recipient_role")),
        )
        self.assertEqual(
            ("webapp_ir", "witness", "ir-durable-ack-outbox", "witness-ir-ingress"),
            tuple(ir_payload[key] for key in ("sender_site", "recipient_site", "sender_role", "recipient_role")),
        )
        self.assertEqual(
            ("witness", "webapp_fi", "witness-fi-egress", "fi-writer-ack-inbox"),
            tuple(wf_payload[key] for key in ("sender_site", "recipient_site", "sender_role", "recipient_role")),
        )
        self.assertEqual(
            base64.b64encode(self.certificate).decode("ascii"),
            fi_payload["context_certificate_base64"],
        )
        self.assertEqual(fi_payload["context_certificate_base64"], wi_payload["context_certificate_base64"])
        self.assertEqual(fi_payload["source_envelope_base64"], wi_payload["source_envelope_base64"])
        self.assertEqual(base64.b64encode(self.assertion).decode("ascii"), ir_payload["ir_durable_assertion_base64"])
        self.assertEqual(base64.b64encode(self.attestation).decode("ascii"), wf_payload["roundtrip_attestation_base64"])
        self.assertEqual({None}, {fi_payload["ir_durable_assertion_base64"], fi_payload["roundtrip_attestation_base64"]})
        self.assertEqual({None}, {wi_payload["ir_durable_assertion_base64"], wi_payload["roundtrip_attestation_base64"]})
        self.assertEqual({None}, {ir_payload["context_certificate_base64"], ir_payload["source_envelope_base64"], ir_payload["roundtrip_attestation_base64"]})
        self.assertEqual({None}, {wf_payload["context_certificate_base64"], wf_payload["source_envelope_base64"], wf_payload["ir_durable_assertion_base64"]})
        receipts = [item["immutable_object_pin_receipt"] for item in (fi_payload, wi_payload, ir_payload, wf_payload)]
        receipt_shas = [item["immutable_object_pin_receipt_sha256"] for item in (fi_payload, wi_payload, ir_payload, wf_payload)]
        self.assertEqual([receipts[0]] * 4, receipts)
        self.assertEqual([self.binding.immutable_object_pin_receipt_sha256] * 4, receipt_shas)
        self.assertEqual([self.binding.recipient_key_id] * 4, [item["recipient_key_id"] for item in (fi_payload, wi_payload, ir_payload, wf_payload)])

    def test_role_policy_outer_pin_tampering_and_substitution_fail_closed(self) -> None:
        packet = delivery.build_physical_wal_v2_witness_fi_to_witness_delivery(
            context_certificate=self.certificate,
            source_envelope=self.envelope,
            config=self._policy("fi-to-witness"),
            now=NOW,
        )
        with self.assertRaisesRegex(
            delivery.PhysicalWalV2WitnessRoundtripDeliveryError,
            "DELIVERY_CONFIG_INVALID",
        ):
            delivery.verify_physical_wal_v2_witness_fi_to_witness_delivery(
                packet,
                config=self._policy("witness-to-ir"),
                now=NOW,
            )
        with self.assertRaisesRegex(
            delivery.PhysicalWalV2WitnessRoundtripDeliveryError,
            "DELIVERY_BINDING_INVALID",
        ):
            delivery.build_physical_wal_v2_witness_fi_to_witness_delivery(
                context_certificate=self.certificate,
                source_envelope=self.envelope,
                config=self._policy(
                    "fi-to-witness",
                    binding=replace(
                        self.binding,
                        recipient_key_id="age-recipient-sha256:" + "f" * 64,
                    ),
                ),
                now=NOW,
            )

        outer_tamper = self._payload(packet)
        outer_tamper["route_commitment_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            delivery.PhysicalWalV2WitnessRoundtripDeliveryError,
            "DELIVERY_PACKET_CROSS_PIN_MISMATCH",
        ):
            delivery.verify_physical_wal_v2_witness_fi_to_witness_delivery(
                self._canonical_payload(outer_tamper),
                config=self._policy("fi-to-witness"),
                now=NOW,
            )

        alternate_envelope = self.fixture._source_envelope(
            self.fixture._certificate(self.fixture._recovery_export()),
            outbox_id="v2-witness-outbox-000002",
            outbox_nonce="N" * 22,
        )
        alternate_raw = roundtrip.verify_physical_wal_v2_witness_source_envelope(
            alternate_envelope,
            config=self.fixture.config,
            now=NOW,
        ).canonical_envelope
        substitution = self._payload(packet)
        substitution["source_envelope_base64"] = base64.b64encode(alternate_raw).decode("ascii")
        with self.assertRaisesRegex(
            delivery.PhysicalWalV2WitnessRoundtripDeliveryError,
            "DELIVERY_PACKET_CROSS_PIN_MISMATCH",
        ):
            delivery.verify_physical_wal_v2_witness_fi_to_witness_delivery(
                self._canonical_payload(substitution),
                config=self._policy("fi-to-witness"),
                now=NOW,
            )

    def test_stage_final_ledger_object_receipt_and_predecessor_pins_are_rebuilt_exactly(self) -> None:
        witness_to_ir = delivery.build_physical_wal_v2_witness_witness_to_ir_delivery(
            context_certificate=self.certificate,
            source_envelope=self.envelope,
            config=self._policy("witness-to-ir"),
            now=NOW,
        )
        final_packet = delivery.build_physical_wal_v2_witness_witness_to_fi_delivery(
            roundtrip_attestation=self.attestation,
            config=self._policy("witness-to-fi"),
            now=NOW,
        )
        tampered_predecessor = self._payload(witness_to_ir)
        tampered_predecessor["prior_delivery_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            delivery.PhysicalWalV2WitnessRoundtripDeliveryError,
            "DELIVERY_PACKET_CROSS_PIN_MISMATCH",
        ):
            delivery.verify_physical_wal_v2_witness_witness_to_ir_delivery(
                self._canonical_payload(tampered_predecessor),
                config=self._policy("witness-to-ir"),
                now=NOW,
            )

        for field_name, replacement in (
            ("context_witness_sequence", 99),
            ("context_witness_ledger_entry_sha256", "f" * 64),
            ("context_witness_ledger_previous_head_sha256", "e" * 64),
            ("delivery_witness_sequence", 99),
            ("delivery_witness_ledger_entry_sha256", "f" * 64),
            ("delivery_witness_ledger_previous_head_sha256", "e" * 64),
            ("immutable_object_pin_receipt_sha256", "f" * 64),
        ):
            with self.subTest(field=field_name):
                tampered = self._payload(final_packet)
                tampered[field_name] = replacement
                with self.assertRaisesRegex(
                    delivery.PhysicalWalV2WitnessRoundtripDeliveryError,
                    "DELIVERY_PACKET_CROSS_PIN_MISMATCH",
                ):
                    delivery.verify_physical_wal_v2_witness_witness_to_fi_delivery(
                        self._canonical_payload(tampered),
                        config=self._policy("witness-to-fi"),
                        now=NOW,
                    )
        receipt_tamper = self._payload(final_packet)
        receipt = dict(receipt_tamper["immutable_object_pin_receipt"])
        receipt["object_count"] = receipt["object_count"] + 1
        receipt_tamper["immutable_object_pin_receipt"] = receipt
        with self.assertRaisesRegex(
            delivery.PhysicalWalV2WitnessRoundtripDeliveryError,
            "DELIVERY_PACKET_CROSS_PIN_MISMATCH",
        ):
            delivery.verify_physical_wal_v2_witness_witness_to_fi_delivery(
                self._canonical_payload(receipt_tamper),
                config=self._policy("witness-to-fi"),
                now=NOW,
            )

    def test_expired_nested_artifact_never_becomes_a_mailbox_message(self) -> None:
        with self.assertRaisesRegex(
            delivery.PhysicalWalV2WitnessRoundtripDeliveryError,
            "DELIVERY_ATTESTATION_INVALID",
        ):
            delivery.build_physical_wal_v2_witness_witness_to_fi_delivery(
                roundtrip_attestation=self.attestation,
                config=self._policy("witness-to-fi"),
                now=NOW + timedelta(seconds=16),
            )

    def test_contract_is_fixed_v2_grammar_without_transport_or_local_state_api(self) -> None:
        source = inspect.getsource(delivery)
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
            "httpx",
            "paramiko",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertNotIn("physical_wal_v1", source)
        self.assertNotIn("preflight", source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertNotIn(
                    node.name,
                    {"send", "deliver", "upload", "download", "connect", "request", "open"},
                )
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "connect", "send", "request"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
