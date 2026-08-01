from __future__ import annotations

import copy
import json
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.object_delta_baseline_manifest import (
    OBJECT_DELTA_BASELINE_MANIFEST_SCHEMA,
    ObjectDeltaReceiverRestoreAttestation,
    ObjectDeltaBaselineManifestError,
    assert_object_delta_baseline_matches_receiver_restore,
    build_object_delta_baseline_manifest,
    parse_object_delta_baseline_manifest_json,
    verify_object_delta_baseline_manifest,
)


CAMPAIGN = "wa-ir-baseline-manifest-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
STREAM = "fi-ir-baseline-stream-20260731"
FINGERPRINT = "0123456789abcdef"


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


def build(*, signer=None, **overrides):
    signer = signer or Ed25519PrivateKey.generate()
    baseline_snapshot = overrides.pop("snapshot", snapshot())
    gate_id = overrides.pop("write_gate_id", str(uuid4()))
    manifest = build_object_delta_baseline_manifest(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=STREAM,
        registry_fingerprint=FINGERPRINT,
        writer_epoch=7,
        writer_lease_id="writer-lease-7",
        snapshot=baseline_snapshot,
        write_gate_id=gate_id,
        source_signer=signer,
        **overrides,
    )
    public_key = signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return manifest, public_key


class ObjectDeltaBaselineManifestTests(unittest.TestCase):
    def verify(self, manifest, public_key, **overrides):
        values = {
            "expected_source_public_key": public_key,
            "expected_source_site": "webapp_fi",
            "expected_destination_site": "webapp_ir",
            "expected_campaign_id": CAMPAIGN,
            "expected_release_sha": RELEASE,
            "expected_stream_generation_id": STREAM,
            "expected_registry_fingerprint": FINGERPRINT,
        }
        values.update(overrides)
        return verify_object_delta_baseline_manifest(manifest, **values)

    def test_builds_and_verifies_a_fresh_genesis_baseline(self):
        manifest, public_key = build()

        verified = self.verify(manifest, public_key)

        self.assertEqual(OBJECT_DELTA_BASELINE_MANIFEST_SCHEMA, manifest["schema"])
        self.assertEqual(CAMPAIGN, verified.campaign_id)
        self.assertEqual(7, verified.writer_epoch)
        self.assertEqual("20260731T120000Z-0123456789abcdef", verified.snapshot_id)
        self.assertEqual(64, len(verified.manifest_sha256))

    def test_tampering_a_signed_snapshot_field_fails_signature_verification(self):
        manifest, public_key = build()
        changed = copy.deepcopy(manifest)
        changed["snapshot"]["database_sha256"] = "d" * 64

        with self.assertRaisesRegex(ObjectDeltaBaselineManifestError, "signature"):
            self.verify(changed, public_key)

    def test_signed_writer_snapshot_and_cutover_claims_cannot_change_after_signing(self):
        manifest, public_key = build()
        mutations = (
            ("writer_term", "epoch", 8),
            ("writer_term", "lease_id", "writer-lease-8"),
            ("snapshot", "uploads_sha256", "d" * 64),
            ("cutover", "write_gate_id", str(uuid4())),
        )
        for section, field, replacement in mutations:
            with self.subTest(section=section, field=field):
                changed = copy.deepcopy(manifest)
                changed[section][field] = replacement
                with self.assertRaisesRegex(ObjectDeltaBaselineManifestError, "signature"):
                    self.verify(changed, public_key)

    def test_receiver_binding_mismatch_is_rejected_even_with_a_valid_signature(self):
        manifest, public_key = build()

        with self.assertRaisesRegex(ObjectDeltaBaselineManifestError, "stream generation"):
            self.verify(manifest, public_key, expected_stream_generation_id="other-stream-20260731")
        with self.assertRaisesRegex(ObjectDeltaBaselineManifestError, "public key"):
            self.verify(manifest, Ed25519PrivateKey.generate().public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            ))

    def test_rejects_non_genesis_or_noncanonical_cutover_before_signature_acceptance(self):
        manifest, public_key = build()
        changed = copy.deepcopy(manifest)
        changed["cutover"]["first_sequence"] = 2
        with self.assertRaisesRegex(ObjectDeltaBaselineManifestError, "sequence one"):
            self.verify(changed, public_key)

        changed = copy.deepcopy(manifest)
        changed["cutover"]["write_gate_id"] = "not-a-uuid"
        with self.assertRaisesRegex(ObjectDeltaBaselineManifestError, "gate id"):
            self.verify(changed, public_key)

    def test_rejects_snapshot_release_or_descriptor_mismatch(self):
        signer = Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(ObjectDeltaBaselineManifestError, "snapshot release"):
            build(signer=signer, snapshot=snapshot(release_sha="0" * 40))

        manifest, public_key = build()
        changed = copy.deepcopy(manifest)
        changed["snapshot"].pop("uploads_sha256")
        with self.assertRaisesRegex(ObjectDeltaBaselineManifestError, "fields"):
            self.verify(changed, public_key)

    def test_strict_json_parser_rejects_duplicate_keys_and_returns_only_normalized_claims(self):
        manifest, public_key = build()
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")

        parsed = parse_object_delta_baseline_manifest_json(raw)

        self.assertEqual(manifest, parsed)
        self.assertEqual(CAMPAIGN, self.verify(parsed, public_key).campaign_id)
        duplicate = raw[:-1] + b',"campaign_id":"duplicate"}'
        with self.assertRaisesRegex(ObjectDeltaBaselineManifestError, "duplicate"):
            parse_object_delta_baseline_manifest_json(duplicate)

    def test_local_restore_attestation_must_match_every_snapshot_and_stream_binding(self):
        manifest, public_key = build()
        baseline = self.verify(manifest, public_key)
        restore = ObjectDeltaReceiverRestoreAttestation(
            source_site=baseline.source_site,
            destination_site=baseline.destination_site,
            campaign_id=baseline.campaign_id,
            release_sha=baseline.release_sha,
            stream_generation_id=baseline.stream_generation_id,
            registry_fingerprint=baseline.registry_fingerprint,
            source_generation=baseline.source_generation,
            snapshot_id=baseline.snapshot_id,
            alembic_revision=baseline.alembic_revision,
            manifest_object_key=baseline.manifest_object_key,
            manifest_object_version_id=baseline.manifest_object_version_id,
            manifest_ciphertext_sha256=baseline.manifest_ciphertext_sha256,
            manifest_ciphertext_bytes=baseline.manifest_ciphertext_bytes,
            database_sha256=baseline.database_sha256,
            uploads_sha256=baseline.uploads_sha256,
        )

        self.assertIsNone(assert_object_delta_baseline_matches_receiver_restore(baseline, restore))
        changed = ObjectDeltaReceiverRestoreAttestation(
            **{**restore.__dict__, "database_sha256": "d" * 64}
        )
        with self.assertRaisesRegex(ObjectDeltaBaselineManifestError, "database hash"):
            assert_object_delta_baseline_matches_receiver_restore(baseline, changed)

    def test_has_no_runtime_or_transport_dependencies(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "core/object_delta_baseline_manifest.py").read_text(
            encoding="utf-8"
        )
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
