from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import (
    DELTA_OBJECT_KIND,
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    IMMUTABLE_RECEIPT_STATUS,
    sha256_bytes,
)
from core.object_delta_baseline_manifest import build_object_delta_baseline_manifest
from core.object_delta_batch_assembler import assemble_object_delta_payload
from core.object_delta_runtime_binding import ObjectDeltaSourceRuntimeBinding
from core.object_delta_source_batch_attestation import (
    build_object_delta_source_batch_attestation,
    canonical_object_delta_source_batch_attestation_bytes,
)
from core.object_delta_source_batch_publication import (
    PreparedObjectDeltaSourceBatch,
    prepare_object_delta_source_batch,
)
from core.object_delta_source_cutover_attestation import (
    ObjectDeltaSourceCutoverRecord,
    build_object_delta_source_cutover_attestation,
    canonical_object_delta_source_cutover_attestation_bytes,
)
from core.object_delta_source_cutover_publication_gate import (
    AuthorizedObjectDeltaSourceBatch,
    AuthorizedObjectDeltaSourceAttestation,
    AuthorizedObjectDeltaSourceBatchAttestationArtifact,
    _legacy_test_only_authorized_object_delta_source_batch_attestation_artifact as authorized_object_delta_source_batch_attestation_artifact,
    _legacy_test_only_authorized_object_delta_source_ledger_entry as authorized_object_delta_source_ledger_entry,
    _legacy_test_only_authorize_object_delta_source_cutover_batch as authorize_object_delta_source_cutover_batch,
    _legacy_test_only_build_authorized_object_delta_source_batch_attestation as build_authorized_object_delta_source_batch_attestation,
    _legacy_test_only_require_authorized_object_delta_source_batch_attestation as require_authorized_object_delta_source_batch_attestation,
    _legacy_test_only_require_authorized_object_delta_source_cutover_batch as require_authorized_object_delta_source_cutover_batch,
    _legacy_test_only_verify_authorized_object_delta_source_batch_attestation as verify_authorized_object_delta_source_batch_attestation,
    ObjectDeltaSourceCutoverPublicationGateError,
    ObjectDeltaSourceCutoverPublicationPin,
    authorized_object_delta_source_batch_attestation_artifact as disabled_authorized_object_delta_source_batch_attestation_artifact,
    authorized_object_delta_source_ledger_entry as disabled_authorized_object_delta_source_ledger_entry,
    authorize_object_delta_source_cutover_batch as disabled_authorize_object_delta_source_cutover_batch,
    build_authorized_object_delta_source_batch_attestation as disabled_build_authorized_object_delta_source_batch_attestation,
    require_authorized_object_delta_source_cutover_batch as disabled_require_authorized_object_delta_source_cutover_batch,
    require_authorized_object_delta_source_batch_attestation as disabled_require_authorized_object_delta_source_batch_attestation,
    verify_authorized_object_delta_source_batch_attestation as disabled_verify_authorized_object_delta_source_batch_attestation,
)
from core.legacy_source_publication_fence import (
    LegacyObjectDeltaSourcePublicationDisabledError,
)
from core.object_delta_source_batch_ledger import SourceStreamIdentity
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    derive_object_delta_object_key,
)
from tests.test_object_delta_batch_assembler import FINGERPRINT, outbox_item


CAMPAIGN = "wa-ir-cutover-publication-gate-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-cutover-publication-gate-20260731"
FI_RECIPIENT = "age1" + "a" * 30
IR_RECIPIENT = "age1" + "c" * 30


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def binding(
    *,
    stream_generation_id: str = GENERATION,
    expected_registry_fingerprint: str = FINGERPRINT,
) -> ObjectDeltaSourceRuntimeBinding:
    return ObjectDeltaSourceRuntimeBinding(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=stream_generation_id,
        expected_registry_fingerprint=expected_registry_fingerprint,
    )


def policy() -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket="private-delta-bucket",
        prefix="campaigns/three-site",
        webapp_fi_age_recipient=FI_RECIPIENT,
        webapp_ir_age_recipient=IR_RECIPIENT,
    )


def stream(*, stream_generation_id: str = GENERATION) -> SourceStreamIdentity:
    return SourceStreamIdentity(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=stream_generation_id,
    )


def snapshot() -> dict[str, object]:
    return {
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


def make_prepared(
    *,
    source_binding: ObjectDeltaSourceRuntimeBinding,
    sequence: int = 1,
    writer_epoch: int = 7,
    writer_lease_id: str = "writer-lease-7",
) -> PreparedObjectDeltaSourceBatch:
    payload = assemble_object_delta_payload(
        stream=stream(stream_generation_id=source_binding.stream_generation_id),
        outbox_items=(
            outbox_item(
                sequence=sequence,
                epoch=writer_epoch,
                lease=writer_lease_id,
            ),
        ),
        expected_registry_fingerprint=source_binding.expected_registry_fingerprint,
    )
    receipt = {
        "schema": IMMUTABLE_RECEIPT_SCHEMA,
        "status": IMMUTABLE_RECEIPT_STATUS,
        "object_kind": DELTA_OBJECT_KIND,
        "object_key": derive_object_delta_object_key(
            policy(),
            source_site=source_binding.source_site,
            destination_site=source_binding.destination_site,
            campaign_id=source_binding.campaign_id,
            release_sha=source_binding.release_sha,
            stream_generation_id=source_binding.stream_generation_id,
            first_sequence=payload.first_sequence,
            last_sequence=payload.last_sequence,
            payload_sha256=payload.payload_sha256,
        ),
        "version_id": f"version-20260731-{sequence:02d}",
        "ciphertext_sha256": "d" * 64,
        "ciphertext_bytes": 1024,
    }
    return prepare_object_delta_source_batch(
        binding=source_binding,
        policy=policy(),
        prepared_payload=payload,
        prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
        verified_ciphertext_receipt=receipt,
    )


class ObjectDeltaSourceCutoverPublicationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = Ed25519PrivateKey.generate()
        self.source_public_key = public_key(self.signer)
        self.binding = binding()
        self.policy = policy()
        self.gate_id = str(uuid4())
        self.baseline = build_object_delta_baseline_manifest(
            source_site=self.binding.source_site,
            destination_site=self.binding.destination_site,
            campaign_id=self.binding.campaign_id,
            release_sha=self.binding.release_sha,
            stream_generation_id=self.binding.stream_generation_id,
            registry_fingerprint=self.binding.expected_registry_fingerprint,
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
            snapshot=snapshot(),
            write_gate_id=self.gate_id,
            source_signer=self.signer,
        )
        self.cutover = build_object_delta_source_cutover_attestation(
            cutover=ObjectDeltaSourceCutoverRecord(
                source_site=self.binding.source_site,
                destination_site=self.binding.destination_site,
                campaign_id=self.binding.campaign_id,
                release_sha=self.binding.release_sha,
                stream_generation_id=self.binding.stream_generation_id,
                state="baseline_published",
                registry_fingerprint=self.binding.expected_registry_fingerprint,
                writer_epoch=7,
                writer_lease_id="writer-lease-7",
                write_gate_id=self.gate_id,
                source_generation="webapp-fi-snapshot-20260731",
                snapshot_id="20260731T120000Z-0123456789abcdef",
                alembic_revision="f2c7d8e9a0b1",
                snapshot_manifest_object_key="campaigns/wa-ir/snapshots/manifest.json.age",
                snapshot_manifest_object_version_id="version-20260731-01",
                snapshot_manifest_ciphertext_sha256="a" * 64,
                snapshot_manifest_ciphertext_bytes=1024,
                database_sha256="b" * 64,
                uploads_sha256="c" * 64,
                baseline_manifest_object_key="campaigns/wa-ir/baselines/manifest.json.age",
                baseline_manifest_object_version_id="version-20260731-02",
                baseline_manifest_ciphertext_sha256="d" * 64,
                baseline_manifest_ciphertext_bytes=2048,
            ),
            baseline_manifest=self.baseline,
            source_signer=self.signer,
        )
        self.pin = ObjectDeltaSourceCutoverPublicationPin(
            binding=self.binding,
            expected_source_public_key=self.source_public_key,
            transport_policy=self.policy,
        )
        self.prepared = make_prepared(source_binding=self.binding)
        self.authorization = authorize_object_delta_source_cutover_batch(
            pin=self.pin,
            prepared=self.prepared,
            source_cutover_attestation=canonical_object_delta_source_cutover_attestation_bytes(
                self.cutover
            ),
        )

    def test_authorized_path_returns_only_the_verified_ledger_candidate_and_attestation(self):
        attestation = build_authorized_object_delta_source_batch_attestation(
            self.authorization,
            source_signer=self.signer,
        )
        verified = verify_authorized_object_delta_source_batch_attestation(
            self.authorization,
            attestation=attestation.canonical_attestation_bytes,
        )
        ledger = authorized_object_delta_source_ledger_entry(attestation)
        artifact = authorized_object_delta_source_batch_attestation_artifact(
            attestation,
        )

        self.assertEqual(self.prepared.ledger_entry, ledger)
        self.assertEqual(self.prepared.batch.batch_sha256, verified.batch_sha256)
        self.assertEqual(self.prepared.batch.batch_sha256, attestation.batch_sha256)
        self.assertEqual(
            attestation.canonical_attestation_bytes,
            artifact.canonical_attestation_bytes,
        )
        self.assertEqual(
            sha256_bytes(artifact.canonical_attestation_bytes),
            artifact.source_attestation_artifact_sha256,
        )
        self.assertEqual(
            len(artifact.canonical_attestation_bytes),
            artifact.source_attestation_artifact_bytes,
        )
        self.assertNotEqual(
            sha256_bytes(artifact.canonical_attestation_bytes[:-1]),
            artifact.source_attestation_artifact_sha256,
        )

    def test_artifact_requires_the_exact_canonical_newline_and_is_not_authority(self):
        attestation = build_authorized_object_delta_source_batch_attestation(
            self.authorization,
            source_signer=self.signer,
        )
        canonical = attestation.canonical_attestation_bytes
        with self.assertRaisesRegex(ObjectDeltaSourceCutoverPublicationGateError, "canonical"):
            verify_authorized_object_delta_source_batch_attestation(
                self.authorization,
                attestation=canonical[:-1],
            )

        artifact = AuthorizedObjectDeltaSourceBatchAttestationArtifact(
            canonical_attestation_bytes=canonical,
            source_key_id="ed25519-sha256:" + "a" * 64,
            batch_sha256="b" * 64,
            source_attestation_artifact_sha256=sha256_bytes(canonical),
            source_attestation_artifact_bytes=len(canonical),
        )
        direct = AuthorizedObjectDeltaSourceAttestation(
            batch_authorization=self.authorization,
            canonical_attestation_bytes=canonical,
            source_key_id=attestation.source_key_id,
            batch_sha256=attestation.batch_sha256,
            source_attestation_artifact_sha256=sha256_bytes(canonical),
            source_attestation_artifact_bytes=len(canonical),
        )
        for candidate in (artifact, direct, replace(attestation)):
            with self.subTest(candidate=candidate.__class__.__name__):
                with self.assertRaisesRegex(
                    ObjectDeltaSourceCutoverPublicationGateError,
                    "capability is required|not verified",
                ):
                    require_authorized_object_delta_source_batch_attestation(candidate)

    def test_direct_or_replaced_authorization_and_prepared_values_have_no_capability(self):
        raw_cutover = canonical_object_delta_source_cutover_attestation_bytes(self.cutover)
        direct = AuthorizedObjectDeltaSourceBatch(
            pin=self.pin,
            prepared=self.prepared,
            source_cutover_attestation=raw_cutover,
        )
        manual_prepared = PreparedObjectDeltaSourceBatch(
            batch=self.prepared.batch,
            transport_binding=self.prepared.transport_binding,
            ledger_entry=self.prepared.ledger_entry,
        )

        for candidate in (direct, replace(self.authorization)):
            with self.subTest(capability=candidate.__class__.__name__):
                with self.assertRaisesRegex(
                    ObjectDeltaSourceCutoverPublicationGateError,
                    "not verified",
                ):
                    require_authorized_object_delta_source_cutover_batch(candidate)
        for candidate in (manual_prepared, replace(self.prepared)):
            with self.subTest(prepared=candidate.__class__.__name__):
                with self.assertRaisesRegex(
                    ObjectDeltaSourceCutoverPublicationGateError,
                    "provenance",
                ):
                    authorize_object_delta_source_cutover_batch(
                        pin=self.pin,
                        prepared=candidate,
                        source_cutover_attestation=self.cutover,
                    )

    def test_registry_term_stream_and_baseline_hash_mismatches_fail_before_ledger_or_attestation(self):
        wrong_registry_pin = ObjectDeltaSourceCutoverPublicationPin(
            binding=binding(expected_registry_fingerprint="f" * 16),
            expected_source_public_key=self.source_public_key,
            transport_policy=self.policy,
        )
        wrong_term = make_prepared(
            source_binding=self.binding,
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
        )
        wrong_stream = make_prepared(
            source_binding=binding(stream_generation_id="fi-ir-cutover-publication-gate-20260731-b"),
        )
        wrong_baseline_hash = copy.deepcopy(self.cutover)
        wrong_baseline_hash["cutover"]["baseline_receipt"]["manifest_sha256"] = "0" * 64

        cases = (
            (wrong_registry_pin, self.prepared, self.cutover, "registry fingerprint"),
            (self.pin, wrong_term, self.cutover, "Writer Witness term"),
            (self.pin, wrong_stream, self.cutover, "provenance"),
            (self.pin, self.prepared, wrong_baseline_hash, "manifest hash"),
        )
        for candidate_pin, candidate_prepared, candidate_cutover, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ObjectDeltaSourceCutoverPublicationGateError, pattern):
                    authorize_object_delta_source_cutover_batch(
                        pin=candidate_pin,
                        prepared=candidate_prepared,
                        source_cutover_attestation=candidate_cutover,
                    )

    def test_signer_and_received_batch_attestation_must_match_the_same_authorized_batch(self):
        with self.assertRaisesRegex(
            ObjectDeltaSourceCutoverPublicationGateError,
            "does not match the root-pinned",
        ):
            build_authorized_object_delta_source_batch_attestation(
                self.authorization,
                source_signer=Ed25519PrivateKey.generate(),
            )

        other_prepared = make_prepared(source_binding=self.binding, sequence=2)
        other_attestation = build_object_delta_source_batch_attestation(
            batch=other_prepared.batch,
            transport_policy=self.policy,
            transport_binding=other_prepared.transport_binding,
            source_signer=self.signer,
        )
        with self.assertRaisesRegex(
            ObjectDeltaSourceCutoverPublicationGateError,
            "does not match the cutover-authorized batch",
        ):
            verify_authorized_object_delta_source_batch_attestation(
                self.authorization,
                attestation=other_attestation,
            )

    def test_former_public_authorization_and_attestation_entrypoints_are_hard_disabled(self):
        raw_cutover = canonical_object_delta_source_cutover_attestation_bytes(self.cutover)
        with patch(
            "core.object_delta_source_cutover_publication_gate._legacy_test_only_authorize_object_delta_source_cutover_batch",
            side_effect=AssertionError("disabled wrapper must not delegate"),
        ):
            with self.assertRaisesRegex(
                LegacyObjectDeltaSourcePublicationDisabledError,
                "hard-disabled.*locked source snapshot.*live Writer Witness",
            ):
                disabled_authorize_object_delta_source_cutover_batch(
                    pin=self.pin,
                    prepared=self.prepared,
                    source_cutover_attestation=raw_cutover,
                )
        with patch(
            "core.object_delta_source_cutover_publication_gate._legacy_test_only_build_authorized_object_delta_source_batch_attestation",
            side_effect=AssertionError("disabled wrapper must not delegate"),
        ):
            with self.assertRaisesRegex(LegacyObjectDeltaSourcePublicationDisabledError, "hard-disabled"):
                disabled_build_authorized_object_delta_source_batch_attestation(
                    self.authorization,
                    source_signer=self.signer,
                )
        with patch(
            "core.object_delta_source_cutover_publication_gate._legacy_test_only_verify_authorized_object_delta_source_batch_attestation",
            side_effect=AssertionError("disabled wrapper must not delegate"),
        ):
            with self.assertRaisesRegex(LegacyObjectDeltaSourcePublicationDisabledError, "hard-disabled"):
                disabled_verify_authorized_object_delta_source_batch_attestation(
                    self.authorization,
                    attestation=b"{}",
                )

        attestation = build_authorized_object_delta_source_batch_attestation(
            self.authorization,
            source_signer=self.signer,
        )
        for entrypoint, value in (
            (disabled_require_authorized_object_delta_source_cutover_batch, self.authorization),
            (disabled_require_authorized_object_delta_source_batch_attestation, attestation),
            (disabled_authorized_object_delta_source_ledger_entry, attestation),
            (disabled_authorized_object_delta_source_batch_attestation_artifact, attestation),
        ):
            with self.subTest(entrypoint=entrypoint.__name__):
                with self.assertRaisesRegex(
                    LegacyObjectDeltaSourcePublicationDisabledError,
                    "hard-disabled",
                ):
                    entrypoint(value)

    def test_gate_has_no_database_or_transport_adapter_dependency(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "core/object_delta_source_cutover_publication_gate.py"
        ).read_text(encoding="utf-8")
        forbidden_imports = (
            "import sqlalchemy",
            "from sqlalchemy",
            "import boto",
            "from boto",
            "import requests",
            "from requests",
            "import httpx",
            "from httpx",
            "import aiohttp",
            "from aiohttp",
            "import subprocess",
            "from subprocess",
            "import socket",
            "from socket",
            "models.",
        )
        self.assertFalse([item for item in forbidden_imports if item in source])

    def test_legacy_minting_names_are_not_star_exports(self):
        import core.object_delta_source_cutover_publication_gate as gate

        self.assertFalse(
            {
                "authorize_object_delta_source_cutover_batch",
                "build_authorized_object_delta_source_batch_attestation",
                "verify_authorized_object_delta_source_batch_attestation",
                "require_authorized_object_delta_source_cutover_batch",
                "require_authorized_object_delta_source_batch_attestation",
                "authorized_object_delta_source_ledger_entry",
                "authorized_object_delta_source_batch_attestation_artifact",
                "_legacy_test_only_authorize_object_delta_source_cutover_batch",
                "_legacy_test_only_build_authorized_object_delta_source_batch_attestation",
                "_legacy_test_only_verify_authorized_object_delta_source_batch_attestation",
                "_legacy_test_only_require_authorized_object_delta_source_cutover_batch",
                "_legacy_test_only_require_authorized_object_delta_source_batch_attestation",
                "_legacy_test_only_authorized_object_delta_source_ledger_entry",
                "_legacy_test_only_authorized_object_delta_source_batch_attestation_artifact",
            }
            & set(gate.__all__)
        )


if __name__ == "__main__":
    unittest.main()
