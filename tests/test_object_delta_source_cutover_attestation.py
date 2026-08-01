from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.object_delta_baseline_manifest import build_object_delta_baseline_manifest
from core.object_delta_source_cutover_attestation import (
    OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SCHEMA,
    ObjectDeltaSourceCutoverAttestationError,
    ObjectDeltaSourceCutoverRecord,
    build_object_delta_source_cutover_attestation,
    canonical_object_delta_source_cutover_attestation_bytes,
    parse_object_delta_source_cutover_attestation_json,
    verify_object_delta_source_cutover_attestation,
)


CAMPAIGN = "wa-ir-cutover-attestation-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
STREAM = "fi-ir-cutover-stream-20260731"
FINGERPRINT = "0123456789abcdef"


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def snapshot(**overrides):
    value = {
        "source_generation": "webapp-fi-snapshot-20260731",
        "snapshot_id": "20260731T120000Z-0123456789abcdef",
        "release_sha": RELEASE,
        "alembic_revision": "f2c7d8e9a0b1",
        "manifest_object_key": "campaigns/wa-ir/snapshots/manifest.json.age",
        "manifest_object_version_id": "version-20260731-01",
        "manifest_ciphertext_sha256": "a" * 64,
        "manifest_ciphertext_bytes": 1024,
        "database_sha256": "b" * 64,
        "uploads_sha256": "c" * 64,
    }
    value.update(overrides)
    return value


def record(*, write_gate_id: str, **overrides) -> ObjectDeltaSourceCutoverRecord:
    value = {
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "campaign_id": CAMPAIGN,
        "release_sha": RELEASE,
        "stream_generation_id": STREAM,
        "state": "baseline_published",
        "registry_fingerprint": FINGERPRINT,
        "writer_epoch": 7,
        "writer_lease_id": "writer-lease-7",
        "write_gate_id": write_gate_id,
        "source_generation": "webapp-fi-snapshot-20260731",
        "snapshot_id": "20260731T120000Z-0123456789abcdef",
        "alembic_revision": "f2c7d8e9a0b1",
        "snapshot_manifest_object_key": "campaigns/wa-ir/snapshots/manifest.json.age",
        "snapshot_manifest_object_version_id": "version-20260731-01",
        "snapshot_manifest_ciphertext_sha256": "a" * 64,
        "snapshot_manifest_ciphertext_bytes": 1024,
        "database_sha256": "b" * 64,
        "uploads_sha256": "c" * 64,
        "baseline_manifest_object_key": "campaigns/wa-ir/baselines/manifest.json.age",
        "baseline_manifest_object_version_id": "version-20260731-02",
        "baseline_manifest_ciphertext_sha256": "d" * 64,
        "baseline_manifest_ciphertext_bytes": 2048,
    }
    value.update(overrides)
    return ObjectDeltaSourceCutoverRecord(**value)


class ObjectDeltaSourceCutoverAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = Ed25519PrivateKey.generate()
        self.public_key = public_key(self.signer)
        self.gate_id = str(uuid4())
        self.record = record(write_gate_id=self.gate_id)
        self.baseline = build_object_delta_baseline_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id=STREAM,
            registry_fingerprint=FINGERPRINT,
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
            snapshot=snapshot(),
            write_gate_id=self.gate_id,
            source_signer=self.signer,
        )

    def attest(self):
        return build_object_delta_source_cutover_attestation(
            cutover=self.record,
            baseline_manifest=self.baseline,
            source_signer=self.signer,
        )

    def verify(self, attestation, **overrides):
        values = {
            "expected_source_public_key": self.public_key,
            "expected_source_site": "webapp_fi",
            "expected_destination_site": "webapp_ir",
            "expected_campaign_id": CAMPAIGN,
            "expected_release_sha": RELEASE,
            "expected_stream_generation_id": STREAM,
            "expected_registry_fingerprint": FINGERPRINT,
        }
        values.update(overrides)
        return verify_object_delta_source_cutover_attestation(attestation, **values)

    def test_builds_and_verifies_committed_cutover_from_canonical_raw_bytes(self):
        attestation = self.attest()
        raw = canonical_object_delta_source_cutover_attestation_bytes(attestation)

        verified = self.verify(raw)

        self.assertEqual(OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SCHEMA, attestation["schema"])
        self.assertEqual(self.gate_id, verified.write_gate_id)
        self.assertEqual(self.record.snapshot_manifest_object_key, verified.snapshot_manifest_object_key)
        self.assertEqual(self.record.baseline_manifest_object_version_id, verified.baseline_manifest_object_version_id)
        self.assertEqual(64, len(verified.baseline_manifest_sha256))
        self.assertEqual(64, len(verified.attestation_sha256))
        self.assertEqual(self.gate_id, verified.baseline.write_gate_id)

    def test_cutover_and_baseline_must_bind_every_writer_gate_and_snapshot_fact(self):
        wrong_gate = record(write_gate_id=str(uuid4()))
        with self.assertRaisesRegex(ObjectDeltaSourceCutoverAttestationError, "write gate"):
            build_object_delta_source_cutover_attestation(
                cutover=wrong_gate,
                baseline_manifest=self.baseline,
                source_signer=self.signer,
            )

        wrong_snapshot = record(write_gate_id=self.gate_id, database_sha256="e" * 64)
        with self.assertRaisesRegex(ObjectDeltaSourceCutoverAttestationError, "database hash"):
            build_object_delta_source_cutover_attestation(
                cutover=wrong_snapshot,
                baseline_manifest=self.baseline,
                source_signer=self.signer,
            )

    def test_requires_the_durable_baseline_published_state_before_signing(self):
        pending = record(
            write_gate_id=self.gate_id,
            state="outbox_active_baseline_pending",
        )

        with self.assertRaisesRegex(ObjectDeltaSourceCutoverAttestationError, "not baseline published"):
            build_object_delta_source_cutover_attestation(
                cutover=pending,
                baseline_manifest=self.baseline,
                source_signer=self.signer,
            )

    def test_outer_and_nested_signed_evidence_cannot_be_tampered(self):
        changed_receipt = copy.deepcopy(self.attest())
        changed_receipt["cutover"]["baseline_receipt"]["manifest_object_key"] = (
            "campaigns/wa-ir/baselines/other.json.age"
        )
        with self.assertRaisesRegex(ObjectDeltaSourceCutoverAttestationError, "signature"):
            self.verify(changed_receipt)

        changed_baseline = copy.deepcopy(self.attest())
        changed_baseline["baseline_manifest"]["snapshot"]["uploads_sha256"] = "e" * 64
        with self.assertRaisesRegex(ObjectDeltaSourceCutoverAttestationError, "baseline manifest"):
            self.verify(changed_baseline)

        changed_baseline_hash = copy.deepcopy(self.attest())
        changed_baseline_hash["cutover"]["baseline_receipt"]["manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(ObjectDeltaSourceCutoverAttestationError, "manifest hash"):
            self.verify(changed_baseline_hash)

    def test_parser_rejects_duplicate_and_noncanonical_wire_forms(self):
        raw = canonical_object_delta_source_cutover_attestation_bytes(self.attest())

        self.assertEqual(self.attest().keys(), parse_object_delta_source_cutover_attestation_json(raw).keys())
        duplicate = raw[:-2] + b',"schema":"duplicate"}\n'
        with self.assertRaisesRegex(ObjectDeltaSourceCutoverAttestationError, "duplicate"):
            parse_object_delta_source_cutover_attestation_json(duplicate)
        noncanonical = json.dumps(self.attest(), sort_keys=True, indent=2).encode("utf-8")
        with self.assertRaisesRegex(ObjectDeltaSourceCutoverAttestationError, "canonical"):
            parse_object_delta_source_cutover_attestation_json(noncanonical)

    def test_verifier_requires_a_pinned_source_key_and_never_accepts_a_record_dataclass(self):
        attestation = self.attest()
        other = public_key(Ed25519PrivateKey.generate())
        with self.assertRaisesRegex(ObjectDeltaSourceCutoverAttestationError, "not pinned"):
            self.verify(attestation, expected_source_public_key=other)
        with self.assertRaisesRegex(ObjectDeltaSourceCutoverAttestationError, "invalid"):
            self.verify(self.record)

    def test_has_no_runtime_or_transport_dependencies(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "core/object_delta_source_cutover_attestation.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "sqlalchemy",
            "models.",
            "api.routers",
            "boto",
            "httpx",
            "aiohttp",
            "subprocess",
            "socket",
            "requests",
        )
        self.assertFalse([name for name in forbidden if name in source])


if __name__ == "__main__":
    unittest.main()
