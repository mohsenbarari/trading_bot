"""Focused adversarial tests for the pure operational-failover evidence wire."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1 as subject


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _digest(letter: str) -> str:
    return letter * 64


def _identifier(prefix: str) -> str:
    return prefix + "-" + "x" * 24


def _nonce(letter: str) -> str:
    return letter * 24


def _key_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _canonical(mapping: object) -> bytes:
    return json.dumps(
        mapping,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


class PhysicalOperationalFailoverV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi_key = Ed25519PrivateKey.generate()
        self.ir_request_key = Ed25519PrivateKey.generate()
        self.witness_key = Ed25519PrivateKey.generate()
        self.ir_completion_key = Ed25519PrivateKey.generate()
        self.pins = subject.PhysicalOperationalFailoverV1Pins(
            cluster_id="gold-trade-three-site-prod",
            release_sha="a" * 40,
            stream_generation_id=_identifier("stream-generation"),
            route_binding_sha256=_digest("b"),
            baseline_generation_id=_identifier("baseline-generation"),
            baseline_manifest_sha256=_digest("c"),
            recovery_frontier_wal_lsn="0/20",
            blob_frontier_wal_lsn="0/30",
        )
        self.config = subject.PhysicalOperationalFailoverV1VerificationConfig(
            pins=self.pins,
            fi_self_fence_signer_public_key=_key_bytes(self.fi_key),
            ir_promotion_request_signer_public_key=_key_bytes(self.ir_request_key),
            witness_term_signer_public_key=_key_bytes(self.witness_key),
            ir_promotion_completion_signer_public_key=_key_bytes(self.ir_completion_key),
            enabled=True,
            maximum_evidence_age_seconds=60,
        )
        self.predecessor = subject.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id=_identifier("fi-lease"),
            witness_transition_id=_identifier("fi-transition"),
            witnessed_term_proof_sha256=_digest("d"),
            issued_at=NOW - timedelta(seconds=20),
            expires_at=NOW + timedelta(seconds=40),
        )
        self.successor = subject.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_ir",
            writer_epoch=42,
            writer_lease_id=_identifier("ir-lease"),
            witness_transition_id=_identifier("ir-transition"),
            witnessed_term_proof_sha256=_digest("e"),
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=50),
        )

    def _fence_input(self, **changes: object) -> subject.PhysicalOperationalFailoverV1FiSelfFenceReceiptInput:
        values: dict[str, object] = {
            "receipt_id": _identifier("fi-fence"),
            "receipt_nonce": _nonce("a"),
            "issued_at": NOW,
            "expires_at": NOW + timedelta(seconds=50),
            "replay_key_sha256": _digest("f"),
            "pins": self.pins,
            "predecessor_term": self.predecessor,
            "fence_reason": "ack-unavailable",
            "last_final_ack_sha256": _digest("1"),
            "last_committed_frontier_wal_lsn": "0/20",
        }
        values.update(changes)
        return subject.PhysicalOperationalFailoverV1FiSelfFenceReceiptInput(**values)

    def _request_input(self, fence: object, **changes: object) -> subject.PhysicalOperationalFailoverV1IrPromotionRequestInput:
        values: dict[str, object] = {
            "request_id": _identifier("ir-promotion-request"),
            "request_nonce": _nonce("b"),
            "issued_at": NOW,
            "expires_at": NOW + timedelta(seconds=50),
            "replay_key_sha256": _digest("2"),
            "pins": self.pins,
            "predecessor_term": self.predecessor,
            "predecessor_termination_reason": "fi-self-fence-receipt",
            "fi_self_fence_receipt_sha256": fence.receipt_sha256,
            "recovery_evidence_sha256": _digest("3"),
            "p0_policy_bundle_sha256": _digest("4"),
        }
        values.update(changes)
        return subject.PhysicalOperationalFailoverV1IrPromotionRequestInput(**values)

    def _grant_input(self, request: object, **changes: object) -> subject.PhysicalOperationalFailoverV1WitnessPromotionGrantInput:
        values: dict[str, object] = {
            "grant_id": _identifier("witness-promotion-grant"),
            "grant_nonce": _nonce("c"),
            "issued_at": NOW,
            "expires_at": NOW + timedelta(seconds=50),
            "replay_key_sha256": _digest("5"),
            "pins": self.pins,
            "request_sha256": request.request_sha256,
            "request_id": request.request_id,
            "request_nonce": request.request_nonce,
            "predecessor_term": self.predecessor,
            "predecessor_termination_reason": "fi-self-fence-receipt",
            "fi_self_fence_receipt_sha256": request.fi_self_fence_receipt_sha256,
            "successor_term": self.successor,
            "activation_route_artifact_sha256": _digest("6"),
            "activation_receiver_permit_sha256": _digest("7"),
            "witness_ledger_sequence": 17,
            "witness_ledger_entry_sha256": _digest("8"),
            "witness_ledger_previous_head_sha256": _digest("9"),
        }
        values.update(changes)
        return subject.PhysicalOperationalFailoverV1WitnessPromotionGrantInput(**values)

    def _completion_input(self, grant: object, **changes: object) -> subject.PhysicalOperationalFailoverV1IrPromotionCompletionInput:
        values: dict[str, object] = {
            "completion_id": _identifier("ir-promotion-completion"),
            "completion_nonce": _nonce("d"),
            "issued_at": NOW + timedelta(seconds=1),
            "expires_at": NOW + timedelta(seconds=51),
            "replay_key_sha256": _digest("a"),
            "pins": self.pins,
            "predecessor_term": self.predecessor,
            "predecessor_termination_reason": "fi-self-fence-receipt",
            "fi_self_fence_receipt_sha256": grant.fi_self_fence_receipt_sha256,
            "grant_sha256": grant.grant_sha256,
            "grant_id": grant.grant_id,
            "grant_nonce": grant.grant_nonce,
            "successor_term": self.successor,
            "activation_route_artifact_sha256": grant.activation_route_artifact_sha256,
            "activation_receiver_permit_sha256": grant.activation_receiver_permit_sha256,
            "promotion_record_sha256": _digest("b"),
            "recovery_evidence_sha256": _digest("c"),
            "p0_execution_sha256": _digest("d"),
            "traffic_fence_receipt_sha256": _digest("e"),
        }
        values.update(changes)
        return subject.PhysicalOperationalFailoverV1IrPromotionCompletionInput(**values)

    def _chain(self) -> tuple[object, object, object, object]:
        fence_raw = subject.sign_physical_operational_failover_v1_fi_self_fence_receipt(
            value=self._fence_input(), config=self.config, private_key=self.fi_key, now=NOW
        )
        fence = subject.verify_physical_operational_failover_v1_fi_self_fence_receipt(
            fence_raw, config=self.config, now=NOW
        )
        request_raw = subject.sign_physical_operational_failover_v1_ir_promotion_request(
            value=self._request_input(fence), config=self.config, private_key=self.ir_request_key, now=NOW
        )
        request = subject.verify_physical_operational_failover_v1_ir_promotion_request(
            request_raw, config=self.config, now=NOW
        )
        grant_raw = subject.sign_physical_operational_failover_v1_witness_promotion_grant(
            value=self._grant_input(request),
            config=self.config,
            private_key=self.witness_key,
            now=NOW,
            expected_request=request,
        )
        grant = subject.verify_physical_operational_failover_v1_witness_promotion_grant(
            grant_raw, config=self.config, now=NOW, expected_request=request
        )
        completion_raw = subject.sign_physical_operational_failover_v1_ir_promotion_completion(
            value=self._completion_input(grant),
            config=self.config,
            private_key=self.ir_completion_key,
            now=NOW + timedelta(seconds=1),
            expected_grant=grant,
        )
        completion = subject.verify_physical_operational_failover_v1_ir_promotion_completion(
            completion_raw,
            config=self.config,
            now=NOW + timedelta(seconds=1),
            expected_grant=grant,
        )
        return fence, request, grant, completion

    def test_valid_chain_is_evidence_only_and_revalidates_exact_types(self) -> None:
        fence, request, grant, completion = self._chain()
        for item in (fence, request, grant, completion):
            self.assertFalse(item.promotion_authorized)
            self.assertFalse(item.writer_authorized)
            self.assertFalse(item.traffic_authorized)
        self.assertEqual(
            subject.require_verified_physical_operational_failover_v1_fi_self_fence_receipt(
                fence, config=self.config, now=NOW
            ),
            fence,
        )
        self.assertEqual(
            subject.require_verified_physical_operational_failover_v1_ir_promotion_request(
                request, config=self.config, now=NOW
            ),
            request,
        )
        self.assertEqual(
            subject.require_verified_physical_operational_failover_v1_witness_promotion_grant(
                grant, config=self.config, now=NOW, expected_request=request
            ),
            grant,
        )
        self.assertEqual(
            subject.require_verified_physical_operational_failover_v1_ir_promotion_completion(
                completion, config=self.config, now=NOW + timedelta(seconds=1), expected_grant=grant
            ),
            completion,
        )

    def test_default_off_config_and_wrong_role_signer_fail_closed(self) -> None:
        disabled = subject.PhysicalOperationalFailoverV1VerificationConfig(
            pins=self.pins,
            fi_self_fence_signer_public_key=_key_bytes(self.fi_key),
            ir_promotion_request_signer_public_key=_key_bytes(self.ir_request_key),
            witness_term_signer_public_key=_key_bytes(self.witness_key),
            ir_promotion_completion_signer_public_key=_key_bytes(self.ir_completion_key),
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1Error, "CONFIG_DISABLED"):
            subject.sign_physical_operational_failover_v1_fi_self_fence_receipt(
                value=self._fence_input(), config=disabled, private_key=self.fi_key, now=NOW
            )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1Error, "FI_FENCE_SIGNER_INVALID"):
            subject.sign_physical_operational_failover_v1_fi_self_fence_receipt(
                value=self._fence_input(), config=self.config, private_key=self.witness_key, now=NOW
            )

    def test_role_key_separation_and_message_domains_fail_closed(self) -> None:
        fence, _request, _grant, _completion = self._chain()
        reused_key_config = subject.PhysicalOperationalFailoverV1VerificationConfig(
            pins=self.pins,
            fi_self_fence_signer_public_key=_key_bytes(self.fi_key),
            ir_promotion_request_signer_public_key=_key_bytes(self.fi_key),
            witness_term_signer_public_key=_key_bytes(self.witness_key),
            ir_promotion_completion_signer_public_key=_key_bytes(self.ir_completion_key),
            enabled=True,
            maximum_evidence_age_seconds=60,
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1Error, "CONFIG_ROLE_KEY_REUSE"):
            subject.verify_physical_operational_failover_v1_fi_self_fence_receipt(
                fence.canonical_receipt, config=reused_key_config, now=NOW
            )
        with self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
            subject.verify_physical_operational_failover_v1_ir_promotion_request(
                fence.canonical_receipt, config=self.config, now=NOW
            )

    def test_signers_cannot_mint_chain_free_grant_or_completion(self) -> None:
        fence_raw = subject.sign_physical_operational_failover_v1_fi_self_fence_receipt(
            value=self._fence_input(), config=self.config, private_key=self.fi_key, now=NOW
        )
        fence = subject.verify_physical_operational_failover_v1_fi_self_fence_receipt(
            fence_raw, config=self.config, now=NOW
        )
        request_raw = subject.sign_physical_operational_failover_v1_ir_promotion_request(
            value=self._request_input(fence), config=self.config, private_key=self.ir_request_key, now=NOW
        )
        request = subject.verify_physical_operational_failover_v1_ir_promotion_request(
            request_raw, config=self.config, now=NOW
        )
        with self.assertRaises(TypeError):
            subject.sign_physical_operational_failover_v1_witness_promotion_grant(
                value=self._grant_input(request),
                config=self.config,
                private_key=self.witness_key,
                now=NOW,
            )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1Error, "IR_REQUEST_EVIDENCE_REQUIRED"):
            subject.sign_physical_operational_failover_v1_witness_promotion_grant(
                value=self._grant_input(request),
                config=self.config,
                private_key=self.witness_key,
                now=NOW,
                expected_request=None,
            )
        grant_raw = subject.sign_physical_operational_failover_v1_witness_promotion_grant(
            value=self._grant_input(request),
            config=self.config,
            private_key=self.witness_key,
            now=NOW,
            expected_request=request,
        )
        grant = subject.verify_physical_operational_failover_v1_witness_promotion_grant(
            grant_raw, config=self.config, now=NOW, expected_request=request
        )
        with self.assertRaises(TypeError):
            subject.sign_physical_operational_failover_v1_ir_promotion_completion(
                value=self._completion_input(grant),
                config=self.config,
                private_key=self.ir_completion_key,
                now=NOW + timedelta(seconds=1),
            )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1Error, "WITNESS_GRANT_EVIDENCE_REQUIRED"):
            subject.sign_physical_operational_failover_v1_ir_promotion_completion(
                value=self._completion_input(grant),
                config=self.config,
                private_key=self.ir_completion_key,
                now=NOW + timedelta(seconds=1),
                expected_grant=None,
            )

    def test_full_chain_cross_binds_every_predecessor_and_is_not_authorization(self) -> None:
        fence, request, grant, completion = self._chain()
        self.assertEqual(request.pins, fence.pins)
        self.assertEqual(request.predecessor_term_sha256, fence.predecessor_term_sha256)
        self.assertEqual(request.predecessor_term, fence.predecessor_term)
        self.assertEqual(request.fi_self_fence_receipt_sha256, fence.receipt_sha256)
        self.assertEqual(grant.request_sha256, request.request_sha256)
        self.assertEqual(grant.request_id, request.request_id)
        self.assertEqual(grant.request_nonce, request.request_nonce)
        self.assertEqual(grant.pins, request.pins)
        self.assertEqual(grant.predecessor_term_sha256, request.predecessor_term_sha256)
        self.assertEqual(grant.fi_self_fence_receipt_sha256, request.fi_self_fence_receipt_sha256)
        self.assertEqual(completion.grant_sha256, grant.grant_sha256)
        self.assertEqual(completion.grant_id, grant.grant_id)
        self.assertEqual(completion.grant_nonce, grant.grant_nonce)
        self.assertEqual(completion.pins, grant.pins)
        self.assertEqual(completion.predecessor_term_sha256, grant.predecessor_term_sha256)
        self.assertEqual(completion.successor_term_sha256, grant.successor_term_sha256)
        self.assertEqual(completion.successor_term, grant.successor_term)
        for item in (fence, request, grant, completion):
            self.assertFalse(item.promotion_authorized)
            self.assertFalse(item.writer_authorized)
            self.assertFalse(item.traffic_authorized)

    def test_expired_evidence_fails_when_reverified(self) -> None:
        fence, request, grant, completion = self._chain()
        expired_now = NOW + timedelta(seconds=52)
        for raw, verifier in (
            (fence.canonical_receipt, subject.verify_physical_operational_failover_v1_fi_self_fence_receipt),
            (request.canonical_request, subject.verify_physical_operational_failover_v1_ir_promotion_request),
            (grant.canonical_grant, subject.verify_physical_operational_failover_v1_witness_promotion_grant),
            (completion.canonical_completion, subject.verify_physical_operational_failover_v1_ir_promotion_completion),
        ):
            with self.subTest(verifier=verifier.__name__), self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
                verifier(raw, config=self.config, now=expired_now)

    def test_fence_receipt_hash_is_only_a_correlation_pin(self) -> None:
        fence_raw = subject.sign_physical_operational_failover_v1_fi_self_fence_receipt(
            value=self._fence_input(), config=self.config, private_key=self.fi_key, now=NOW
        )
        fence = subject.verify_physical_operational_failover_v1_fi_self_fence_receipt(
            fence_raw, config=self.config, now=NOW
        )
        synthetic_hash = _digest("e")
        request_raw = subject.sign_physical_operational_failover_v1_ir_promotion_request(
            value=self._request_input(fence, fi_self_fence_receipt_sha256=synthetic_hash),
            config=self.config,
            private_key=self.ir_request_key,
            now=NOW,
        )
        request = subject.verify_physical_operational_failover_v1_ir_promotion_request(
            request_raw, config=self.config, now=NOW
        )
        self.assertEqual(request.fi_self_fence_receipt_sha256, synthetic_hash)
        self.assertFalse(request.promotion_authorized)
        self.assertFalse(request.writer_authorized)
        self.assertFalse(request.traffic_authorized)

    def test_all_wire_types_reject_tampering(self) -> None:
        fence, request, grant, completion = self._chain()
        cases = (
            (fence.canonical_receipt, subject.verify_physical_operational_failover_v1_fi_self_fence_receipt),
            (request.canonical_request, subject.verify_physical_operational_failover_v1_ir_promotion_request),
            (grant.canonical_grant, subject.verify_physical_operational_failover_v1_witness_promotion_grant),
            (completion.canonical_completion, subject.verify_physical_operational_failover_v1_ir_promotion_completion),
        )
        for raw, verifier in cases:
            mutated = bytearray(raw)
            mutated[-2] = ord("A") if mutated[-2] != ord("A") else ord("B")
            with self.subTest(verifier=verifier.__name__), self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
                verifier(bytes(mutated), config=self.config, now=NOW)

    def test_noncanonical_and_duplicate_json_are_rejected(self) -> None:
        fence, _request, _grant, _completion = self._chain()
        noncanonical = b" " + fence.canonical_receipt
        with self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
            subject.verify_physical_operational_failover_v1_fi_self_fence_receipt(
                noncanonical, config=self.config, now=NOW
            )
        duplicate = b'{"schema":"x","schema":"x"}'
        with self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
            subject.verify_physical_operational_failover_v1_fi_self_fence_receipt(
                duplicate, config=self.config, now=NOW
            )

    def test_wrong_public_signer_and_wrong_issuer_site_are_rejected(self) -> None:
        fence, _request, _grant, _completion = self._chain()
        wrong_config = subject.PhysicalOperationalFailoverV1VerificationConfig(
            pins=self.pins,
            fi_self_fence_signer_public_key=_key_bytes(Ed25519PrivateKey.generate()),
            ir_promotion_request_signer_public_key=_key_bytes(self.ir_request_key),
            witness_term_signer_public_key=_key_bytes(self.witness_key),
            ir_promotion_completion_signer_public_key=_key_bytes(self.ir_completion_key),
            enabled=True,
            maximum_evidence_age_seconds=60,
        )
        with self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
            subject.verify_physical_operational_failover_v1_fi_self_fence_receipt(
                fence.canonical_receipt, config=wrong_config, now=NOW
            )
        mapping = json.loads(fence.canonical_receipt)
        mapping["issuer_site"] = "webapp_ir"
        unsigned = {key: value for key, value in mapping.items() if key != "signature_base64"}
        domain = ("gold-trade-physical-operational-failover-v1/fi-self-fence-receipt-v1\x00").encode("ascii")
        mapping["signature_base64"] = base64.b64encode(self.fi_key.sign(domain + _canonical(unsigned))).decode("ascii")
        with self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
            subject.verify_physical_operational_failover_v1_fi_self_fence_receipt(
                _canonical(mapping), config=self.config, now=NOW
            )

    def test_stale_and_future_evidence_fail_closed(self) -> None:
        with self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
            subject.sign_physical_operational_failover_v1_fi_self_fence_receipt(
                value=self._fence_input(
                    issued_at=NOW - timedelta(seconds=61),
                    expires_at=NOW - timedelta(seconds=1),
                ),
                config=self.config,
                private_key=self.fi_key,
                now=NOW,
            )
        with self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
            subject.sign_physical_operational_failover_v1_fi_self_fence_receipt(
                value=self._fence_input(
                    issued_at=NOW + timedelta(seconds=6),
                    expires_at=NOW + timedelta(seconds=56),
                ),
                config=self.config,
                private_key=self.fi_key,
                now=NOW,
            )

    def test_cross_release_and_cross_route_pins_are_rejected(self) -> None:
        fence, _request, _grant, _completion = self._chain()
        for name, replacement in (("release_sha", "f" * 40), ("route_binding_sha256", _digest("a"))):
            values = self.pins.__dict__.copy()
            values[name] = replacement
            changed = subject.PhysicalOperationalFailoverV1Pins(**values)
            changed_config = subject.PhysicalOperationalFailoverV1VerificationConfig(
                pins=changed,
                fi_self_fence_signer_public_key=_key_bytes(self.fi_key),
                ir_promotion_request_signer_public_key=_key_bytes(self.ir_request_key),
                witness_term_signer_public_key=_key_bytes(self.witness_key),
                ir_promotion_completion_signer_public_key=_key_bytes(self.ir_completion_key),
                enabled=True,
                maximum_evidence_age_seconds=60,
            )
            with self.subTest(pin=name), self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
                subject.verify_physical_operational_failover_v1_fi_self_fence_receipt(
                    fence.canonical_receipt, config=changed_config, now=NOW
                )

    def test_grant_rejects_altered_request_nonce_and_predecessor(self) -> None:
        fence, request, _grant, _completion = self._chain()
        altered_request_raw = subject.sign_physical_operational_failover_v1_ir_promotion_request(
            value=self._request_input(fence, request_nonce=_nonce("z")),
            config=self.config,
            private_key=self.ir_request_key,
            now=NOW,
        )
        altered_request = subject.verify_physical_operational_failover_v1_ir_promotion_request(
            altered_request_raw, config=self.config, now=NOW
        )
        grant_raw = subject.sign_physical_operational_failover_v1_witness_promotion_grant(
            value=self._grant_input(request),
            config=self.config,
            private_key=self.witness_key,
            now=NOW,
            expected_request=request,
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1Error, "GRANT_REQUEST_MISMATCH"):
            subject.verify_physical_operational_failover_v1_witness_promotion_grant(
                grant_raw, config=self.config, now=NOW, expected_request=altered_request
            )

        alternate_predecessor = subject.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_fi",
            writer_epoch=40,
            writer_lease_id=_identifier("alternate-fi-lease"),
            witness_transition_id=_identifier("alternate-fi-transition"),
            witnessed_term_proof_sha256=_digest("f"),
            issued_at=NOW - timedelta(seconds=30),
            expires_at=NOW + timedelta(seconds=30),
        )
        alternate_successor = subject.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_ir",
            writer_epoch=42,
            writer_lease_id=self.successor.writer_lease_id,
            witness_transition_id=self.successor.witness_transition_id,
            witnessed_term_proof_sha256=self.successor.witnessed_term_proof_sha256,
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=50),
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1Error, "GRANT_REQUEST_MISMATCH"):
            subject.sign_physical_operational_failover_v1_witness_promotion_grant(
                value=self._grant_input(request, predecessor_term=alternate_predecessor, successor_term=alternate_successor),
                config=self.config,
                private_key=self.witness_key,
                now=NOW,
                expected_request=request,
            )

    def test_completion_rejects_altered_grant_nonce_and_term_binding(self) -> None:
        _fence, request, grant, _completion = self._chain()
        altered_grant_raw = subject.sign_physical_operational_failover_v1_witness_promotion_grant(
            value=self._grant_input(
                request,
                grant_nonce=_nonce("y"),
            ),
            config=self.config,
            private_key=self.witness_key,
            now=NOW,
            expected_request=request,
        )
        altered_grant = subject.verify_physical_operational_failover_v1_witness_promotion_grant(
            altered_grant_raw, config=self.config, now=NOW, expected_request=request
        )
        completion_raw = subject.sign_physical_operational_failover_v1_ir_promotion_completion(
            value=self._completion_input(altered_grant),
            config=self.config,
            private_key=self.ir_completion_key,
            now=NOW + timedelta(seconds=1),
            expected_grant=altered_grant,
        )
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1Error, "COMPLETION_GRANT_MISMATCH"):
            subject.verify_physical_operational_failover_v1_ir_promotion_completion(
                completion_raw,
                config=self.config,
                now=NOW + timedelta(seconds=1),
                expected_grant=grant,
            )

    def test_expiry_path_requires_expired_predecessor_and_no_fence_hash(self) -> None:
        expired = subject.PhysicalOperationalFailoverV1Term(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id=self.predecessor.writer_lease_id,
            witness_transition_id=self.predecessor.witness_transition_id,
            witnessed_term_proof_sha256=self.predecessor.witnessed_term_proof_sha256,
            issued_at=NOW - timedelta(seconds=60),
            expires_at=NOW,
        )
        request_raw = subject.sign_physical_operational_failover_v1_ir_promotion_request(
            value=self._request_input(
                type("Fence", (), {"receipt_sha256": _digest("1")})(),
                predecessor_term=expired,
                predecessor_termination_reason="predecessor-term-expired",
                fi_self_fence_receipt_sha256=None,
            ),
            config=self.config,
            private_key=self.ir_request_key,
            now=NOW,
        )
        request = subject.verify_physical_operational_failover_v1_ir_promotion_request(
            request_raw, config=self.config, now=NOW
        )
        self.assertIsNone(request.fi_self_fence_receipt_sha256)
        with self.assertRaises(subject.PhysicalOperationalFailoverV1Error):
            subject.sign_physical_operational_failover_v1_ir_promotion_request(
                value=self._request_input(
                    type("Fence", (), {"receipt_sha256": _digest("1")})(),
                    predecessor_termination_reason="predecessor-term-expired",
                    fi_self_fence_receipt_sha256=None,
                ),
                config=self.config,
                private_key=self.ir_request_key,
                now=NOW,
            )

    def test_writer_lease_uses_canonical_shared_grammar(self) -> None:
        short = replace(self.predecessor, writer_lease_id="writer-lease-73")
        raw = subject.sign_physical_operational_failover_v1_fi_self_fence_receipt(
            value=self._fence_input(predecessor_term=short),
            config=self.config,
            private_key=self.fi_key,
            now=NOW,
        )
        verified = subject.verify_physical_operational_failover_v1_fi_self_fence_receipt(
            raw,
            config=self.config,
            now=NOW,
        )
        self.assertEqual("writer-lease-73", verified.predecessor_term.writer_lease_id)

        # This used to satisfy V1's generic audit-ID regex; a writer lease
        # instead has the narrower shared V1/V2 grammar.
        invalid = replace(self.predecessor, writer_lease_id="writer:lease-000073")
        with self.assertRaisesRegex(subject.PhysicalOperationalFailoverV1Error, "INVALID"):
            subject.sign_physical_operational_failover_v1_fi_self_fence_receipt(
                value=self._fence_input(predecessor_term=invalid),
                config=self.config,
                private_key=self.fi_key,
                now=NOW,
            )

    def test_module_has_no_v2_v4_or_operational_io_imports(self) -> None:
        source = Path(subject.__file__).read_text(encoding="utf-8")
        self.assertNotIn("physical_full_matrix_v4", source)
        self.assertNotIn("physical_wal_v2", source)
        self.assertNotIn("import os", source)
        self.assertNotIn("import socket", source)
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("boto3", source)


if __name__ == "__main__":
    unittest.main()
