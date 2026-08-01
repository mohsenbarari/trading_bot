from __future__ import annotations

import ast
import base64
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_wal_remote_ack import (
    PHYSICAL_WAL_REMOTE_ACK_RECEIPT_SCHEMA,
    PHYSICAL_WAL_REMOTE_ACK_REQUEST_SCHEMA,
    PhysicalWalRemoteAckError,
    VerifiedPhysicalWalRemoteAckEvidence,
    VerifiedPhysicalWalRemoteAckRequest,
    build_physical_wal_remote_ack_binding,
    build_physical_wal_remote_ack_receipt,
    build_physical_wal_remote_ack_request,
    canonical_physical_wal_remote_ack_receipt_bytes,
    canonical_physical_wal_remote_ack_request_bytes,
    require_verified_physical_wal_remote_ack_evidence,
    require_verified_physical_wal_remote_ack_request,
    verify_physical_wal_remote_ack_evidence,
    verify_physical_wal_remote_ack_request,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "physical-wal-ack-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT_FI = "age1pppppppppppppppppppppppppppppppppppppppppppppppp"
RECIPIENT_IR = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
BASE_HASH = "b" * 64
MANIFEST_HASHES = (BASE_HASH, "c" * 64, "d" * 64)


def _public_bytes(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _signature_mapping(signer: Ed25519PrivateKey, *, domain: bytes, unsigned: dict) -> dict[str, str]:
    signature = signer.sign(domain + canonical_json_bytes(unsigned))
    return {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")}


class PhysicalWalRemoteAckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi = Ed25519PrivateKey.generate()
        self.ir = Ed25519PrivateKey.generate()

    def _build(
        self,
        *,
        reverse: bool = False,
        binding=None,
        issued_at: datetime = NOW,
        acknowledged_at: datetime = NOW,
        request_id: str = "request-id-0000000001",
        request_nonce: str = "R" * 22,
        receipt_id: str = "receipt-id-0000000001",
        receipt_nonce: str = "S" * 22,
    ):
        source_site = "webapp_ir" if reverse else "webapp_fi"
        destination_site = "webapp_fi" if reverse else "webapp_ir"
        recipient = RECIPIENT_FI if reverse else RECIPIENT_IR
        source_signer = self.ir if reverse else self.fi
        destination_signer = self.fi if reverse else self.ir
        binding = binding or build_physical_wal_remote_ack_binding(
            source_site=source_site,
            destination_site=destination_site,
            destination_age_recipient=recipient,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id="physical-ack-stream-20260731",
            baseline_generation_id="physical-ack-base-20260731",
            baseline_manifest_sha256=BASE_HASH,
            writer_epoch=7,
            writer_holder_site=source_site,
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256="a" * 64,
            target_acknowledged_wal_lsn="0/2000000",
            blob_object_frontier_wal_lsn="0/2000000",
            manifest_sha256es=MANIFEST_HASHES,
            object_versions=(
                ("physical/fi-ir/base/backup-001.age", "base-version-001"),
                ("physical/fi-ir/wal/0001.age", "wal-version-0001"),
                ("physical/fi-ir/blob/inventory-001.age", "inventory-version-001"),
            ),
        )
        request = build_physical_wal_remote_ack_request(
            binding=binding,
            request_id=request_id,
            request_nonce=request_nonce,
            issued_at=issued_at,
            source_signer=source_signer,
        )
        receipt = build_physical_wal_remote_ack_receipt(
            source_request=request,
            receipt_id=receipt_id,
            receipt_nonce=receipt_nonce,
            acknowledged_at=acknowledged_at,
            destination_signer=destination_signer,
        )
        return {
            "binding": binding,
            "request": request,
            "receipt": receipt,
            "source_key": _public_bytes(source_signer),
            "destination_key": _public_bytes(destination_signer),
        }

    def _verify(self, built, **overrides):
        values = {
            "source_request": built["request"],
            "destination_receipt": built["receipt"],
            "expected_binding": built["binding"],
            "expected_source_public_key": built["source_key"],
            "expected_destination_public_key": built["destination_key"],
            "now": NOW,
        }
        values.update(overrides)
        return verify_physical_wal_remote_ack_evidence(**values)

    def _verify_request(self, built, **overrides):
        values = {
            "source_request": built["request"],
            "expected_binding": built["binding"],
            "expected_source_public_key": built["source_key"],
            "now": NOW,
        }
        values.update(overrides)
        return verify_physical_wal_remote_ack_request(**values)

    def test_verifies_exact_signed_pull_plane_evidence_but_returns_no_execution_authority(self):
        built = self._build()
        evidence = self._verify(built)

        self.assertIsInstance(evidence, VerifiedPhysicalWalRemoteAckEvidence)
        self.assertEqual("webapp_fi", evidence.binding.source_site)
        self.assertEqual("webapp_ir", evidence.binding.destination_site)
        self.assertEqual("0/2000000", evidence.binding.target_acknowledged_wal_lsn)
        self.assertEqual(MANIFEST_HASHES, evidence.binding.manifest_sha256es)
        self.assertEqual(3, len(evidence.binding.object_versions))
        self.assertIs(require_verified_physical_wal_remote_ack_evidence(evidence, now=NOW), evidence)
        self.assertFalse(hasattr(evidence, "commit"))
        self.assertFalse(hasattr(evidence, "promote"))

    def test_reverse_ir_to_fi_route_is_equally_bound(self):
        built = self._build(reverse=True)
        evidence = self._verify(built)

        self.assertEqual("webapp_ir", evidence.binding.source_site)
        self.assertEqual("webapp_fi", evidence.binding.destination_site)
        self.assertEqual(RECIPIENT_FI, evidence.binding.destination_age_recipient)

    def test_destination_can_verify_an_exact_source_request_before_signing_a_receipt(self):
        built = self._build()
        request = self._verify_request(built)

        self.assertIsInstance(request, VerifiedPhysicalWalRemoteAckRequest)
        self.assertEqual("request-id-0000000001", request.request_id)
        self.assertEqual("webapp_ir", request.binding.destination_site)
        self.assertIs(
            require_verified_physical_wal_remote_ack_request(request, now=NOW),
            request,
        )
        self.assertFalse(hasattr(request, "receipt"))
        self.assertFalse(hasattr(request, "promote"))

        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "request ID was replayed"):
            self._verify_request(
                built,
                consumed_request_ids={"request-id-0000000001"},
            )
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "route, term, recipient"):
            self._verify_request(
                built,
                expected_binding=build_physical_wal_remote_ack_binding(
                    source_site="webapp_fi",
                    destination_site="webapp_ir",
                    destination_age_recipient=RECIPIENT_FI,
                    campaign_id=CAMPAIGN,
                    release_sha=RELEASE,
                    stream_generation_id="physical-ack-stream-20260731",
                    baseline_generation_id="physical-ack-base-20260731",
                    baseline_manifest_sha256=BASE_HASH,
                    writer_epoch=7,
                    writer_holder_site="webapp_fi",
                    writer_lease_id="writer-lease-seven",
                    witnessed_term_proof_sha256="a" * 64,
                    target_acknowledged_wal_lsn="0/2000000",
                    blob_object_frontier_wal_lsn="0/2000000",
                    manifest_sha256es=MANIFEST_HASHES,
                    object_versions=(
                        ("physical/fi-ir/base/backup-001.age", "base-version-001"),
                    ),
                ),
            )

    def test_term_projection_holder_must_be_the_signed_source_site(self):
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "term holder"):
            build_physical_wal_remote_ack_binding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                destination_age_recipient=RECIPIENT_IR,
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                stream_generation_id="physical-ack-stream-20260731",
                baseline_generation_id="physical-ack-base-20260731",
                baseline_manifest_sha256=BASE_HASH,
                writer_epoch=7,
                writer_holder_site="webapp_ir",
                writer_lease_id="writer-lease-seven",
                witnessed_term_proof_sha256="a" * 64,
                target_acknowledged_wal_lsn="0/2000000",
                blob_object_frontier_wal_lsn="0/2000000",
                manifest_sha256es=MANIFEST_HASHES,
                object_versions=(("physical/fi-ir/base/backup-001.age", "base-version-001"),),
            )

    def test_tamper_wrong_keys_route_term_recipient_and_object_set_fail_closed(self):
        built = self._build()
        with self.subTest("tamper"):
            tampered = copy.deepcopy(built["request"])
            tampered["binding"]["target_acknowledged_wal_lsn"] = "0/1000000"
            with self.assertRaisesRegex(PhysicalWalRemoteAckError, "signature"):
                self._verify(built, source_request=tampered)

        with self.subTest("wrong source key"):
            with self.assertRaisesRegex(PhysicalWalRemoteAckError, "signer does not match"):
                self._verify(built, expected_source_public_key=_public_bytes(Ed25519PrivateKey.generate()))

        with self.subTest("wrong route recipient"):
            foreign_binding = build_physical_wal_remote_ack_binding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                destination_age_recipient=RECIPIENT_FI,
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                stream_generation_id="physical-ack-stream-20260731",
                baseline_generation_id="physical-ack-base-20260731",
                baseline_manifest_sha256=BASE_HASH,
                writer_epoch=7,
                writer_holder_site="webapp_fi",
                writer_lease_id="writer-lease-seven",
                witnessed_term_proof_sha256="a" * 64,
                target_acknowledged_wal_lsn="0/2000000",
                blob_object_frontier_wal_lsn="0/2000000",
                manifest_sha256es=MANIFEST_HASHES,
                object_versions=tuple((item.object_key, item.version_id) for item in built["binding"].object_versions),
            )
            with self.assertRaisesRegex(PhysicalWalRemoteAckError, "route, term, recipient"):
                self._verify(built, expected_binding=foreign_binding)

        with self.subTest("wrong term"):
            wrong_term = build_physical_wal_remote_ack_binding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                destination_age_recipient=RECIPIENT_IR,
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                stream_generation_id="physical-ack-stream-20260731",
                baseline_generation_id="physical-ack-base-20260731",
                baseline_manifest_sha256=BASE_HASH,
                writer_epoch=8,
                writer_holder_site="webapp_fi",
                writer_lease_id="writer-lease-eight",
                witnessed_term_proof_sha256="e" * 64,
                target_acknowledged_wal_lsn="0/2000000",
                blob_object_frontier_wal_lsn="0/2000000",
                manifest_sha256es=MANIFEST_HASHES,
                object_versions=tuple((item.object_key, item.version_id) for item in built["binding"].object_versions),
            )
            with self.assertRaisesRegex(PhysicalWalRemoteAckError, "route, term, recipient"):
                self._verify(built, expected_binding=wrong_term)

        with self.subTest("signed foreign exact Object-version set"):
            foreign_binding = build_physical_wal_remote_ack_binding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                destination_age_recipient=RECIPIENT_IR,
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                stream_generation_id="physical-ack-stream-20260731",
                baseline_generation_id="physical-ack-base-20260731",
                baseline_manifest_sha256=BASE_HASH,
                writer_epoch=7,
                writer_holder_site="webapp_fi",
                writer_lease_id="writer-lease-seven",
                witnessed_term_proof_sha256="a" * 64,
                target_acknowledged_wal_lsn="0/2000000",
                blob_object_frontier_wal_lsn="0/2000000",
                manifest_sha256es=MANIFEST_HASHES,
                object_versions=(
                    ("physical/fi-ir/base/backup-001.age", "base-version-002"),
                    ("physical/fi-ir/wal/0001.age", "wal-version-0001"),
                    ("physical/fi-ir/blob/inventory-001.age", "inventory-version-001"),
                ),
            )
            foreign = self._build(binding=foreign_binding)
            with self.assertRaisesRegex(PhysicalWalRemoteAckError, "route, term, recipient"):
                self._verify({**foreign, "binding": built["binding"]})

    def test_stale_future_replayed_ids_and_regressed_lsn_are_rejected(self):
        stale = self._build(
            issued_at=NOW - timedelta(seconds=61),
            acknowledged_at=NOW - timedelta(seconds=60),
        )
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "stale"):
            self._verify(stale)

        future = self._build(
            issued_at=NOW + timedelta(seconds=6),
            acknowledged_at=NOW + timedelta(seconds=7),
        )
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "future"):
            self._verify(future)

        current = self._build()
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "request ID was replayed"):
            self._verify(current, consumed_request_ids={"request-id-0000000001"})
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "receipt ID was replayed"):
            self._verify(current, consumed_receipt_ids={"receipt-id-0000000001"})
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "request ID was replayed"):
            self._verify(current, consumed_receipt_ids={"request-id-0000000001"})
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "regresses"):
            self._verify(current, minimum_acknowledged_wal_lsn="0/3000000")

    def test_incomplete_blob_binding_and_signed_incomplete_receipt_are_rejected(self):
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "incomplete"):
            build_physical_wal_remote_ack_binding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                destination_age_recipient=RECIPIENT_IR,
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                stream_generation_id="physical-ack-stream-20260731",
                baseline_generation_id="physical-ack-base-20260731",
                baseline_manifest_sha256=BASE_HASH,
                writer_epoch=7,
                writer_holder_site="webapp_fi",
                writer_lease_id="writer-lease-seven",
                witnessed_term_proof_sha256="a" * 64,
                target_acknowledged_wal_lsn="0/2000000",
                blob_object_frontier_wal_lsn="0/2000000",
                manifest_sha256es=MANIFEST_HASHES,
                object_versions=(("physical/fi-ir/base/backup-001.age", "base-version-001"),),
                objects_complete=False,
            )

        built = self._build()
        request = copy.deepcopy(built["request"])
        request["binding"]["objects_complete"] = False
        unsigned_request = {key: value for key, value in request.items() if key != "source_signature"}
        request["source_signature"] = _signature_mapping(
            self.fi,
            domain=b"gold-trade-physical-wal-remote-ack-request-v1\x00",
            unsigned=unsigned_request,
        )
        receipt = copy.deepcopy(built["receipt"])
        receipt["binding"]["objects_complete"] = False
        receipt["source_request_sha256"] = hashlib.sha256(canonical_json_bytes(request)).hexdigest()
        unsigned_receipt = {key: value for key, value in receipt.items() if key != "destination_signature"}
        receipt["destination_signature"] = _signature_mapping(
            self.ir,
            domain=b"gold-trade-physical-wal-remote-ack-receipt-v1\x00",
            unsigned=unsigned_receipt,
        )
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "incomplete"):
            self._verify(built, source_request=request, destination_receipt=receipt)

    def test_receipt_must_bind_the_exact_request_and_reverification_is_opaque(self):
        first = self._build()
        second = self._build(
            request_id="request-id-0000000002",
            request_nonce="T" * 22,
            receipt_id="receipt-id-0000000002",
            receipt_nonce="U" * 22,
        )
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "exact source request"):
            self._verify(first, destination_receipt=second["receipt"])

        evidence = self._verify(first)
        forged = replace(evidence, request_id="request-id-0000000099")
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "capability"):
            require_verified_physical_wal_remote_ack_evidence(forged, now=NOW)

    def test_canonical_bounded_json_and_aliases_fail_closed(self):
        built = self._build()
        request_bytes = canonical_physical_wal_remote_ack_request_bytes(built["request"])
        receipt_bytes = canonical_physical_wal_remote_ack_receipt_bytes(built["receipt"])
        self.assertEqual(request_bytes, canonical_json_bytes(built["request"]))
        self.assertEqual(receipt_bytes, canonical_json_bytes(built["receipt"]))
        duplicate = request_bytes[:-1] + b',"request_id":"request-id-0000000999"}'
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "duplicate"):
            self._verify(built, source_request=duplicate)
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "canonical"):
            self._verify(built, source_request=b"{ \"bad\": 1 }")
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "mutable alias"):
            build_physical_wal_remote_ack_binding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                destination_age_recipient=RECIPIENT_IR,
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                stream_generation_id="physical-ack-stream-20260731",
                baseline_generation_id="physical-ack-base-20260731",
                baseline_manifest_sha256=BASE_HASH,
                writer_epoch=7,
                writer_holder_site="webapp_fi",
                writer_lease_id="writer-lease-seven",
                witnessed_term_proof_sha256="a" * 64,
                target_acknowledged_wal_lsn="0/2000000",
                blob_object_frontier_wal_lsn="0/2000000",
                manifest_sha256es=MANIFEST_HASHES,
                object_versions=(("physical/fi-ir/wal/latest.age", "wal-version-0001"),),
            )
        with self.assertRaisesRegex(PhysicalWalRemoteAckError, "reuses its nonce"):
            build_physical_wal_remote_ack_request(
                binding=built["binding"],
                request_id="A" * 22,
                request_nonce="A" * 22,
                issued_at=NOW,
                source_signer=self.fi,
            )

    def test_module_has_no_transport_database_or_execution_adapter_import(self):
        import core.physical_wal_remote_ack as remote_ack_module

        tree = ast.parse(inspect.getsource(remote_ack_module))
        forbidden = {
            "boto3",
            "botocore",
            "docker",
            "http",
            "paramiko",
            "psycopg",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertFalse(forbidden & imported)
        self.assertNotIn("import os", inspect.getsource(remote_ack_module))


if __name__ == "__main__":
    unittest.main()
