from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    build_delta_batch,
    sha256_bytes,
    validate_delta_batch,
)
from core.object_delta_baseline_manifest import (
    ObjectDeltaReceiverRestoreAttestation,
    build_object_delta_baseline_manifest,
)
from core.object_delta_delivery_control_packet import (
    ObjectDeltaReceiverDeliveryPermit,
    build_unsigned_object_delta_delivery_control_packet,
    controller_key_id_from_public_key,
    sign_object_delta_delivery_control_packet,
    verify_object_delta_delivery_control_packet,
)
from core.object_delta_receiver_apply_scope import authorize_object_delta_receiver_delivery
from core.object_delta_receiver_delivery_binding import ObjectDeltaReceiverDeliveryBinding
from core.object_delta_receiver_genesis_admission import (
    AuthorizedObjectDeltaReceiverGenesisAdmission,
    ObjectDeltaReceiverGenesisAdmissionError,
    VerifiedObjectDeltaReceiverGenesisBaseline,
    admit_object_delta_receiver_genesis,
    build_object_delta_receiver_restore_evidence,
    canonical_object_delta_receiver_restore_evidence_bytes,
    parse_object_delta_receiver_restore_evidence_json,
    require_object_delta_receiver_genesis_admission,
    validate_authorized_object_delta_receiver_genesis_admission,
    verify_object_delta_receiver_genesis_baseline,
    verify_object_delta_receiver_genesis_cutover,
    verify_object_delta_receiver_restore_evidence,
)
from core.object_delta_source_batch_attestation import (
    build_object_delta_source_batch_attestation,
    source_key_id_from_public_key,
)
from core.object_delta_source_cutover_attestation import (
    ObjectDeltaSourceCutoverRecord,
    build_object_delta_source_cutover_attestation,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
)


CAMPAIGN = "wa-ir-genesis-admission-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-genesis-admission-stream-20260731"
FINGERPRINT = "0123456789abcdef"
PAYLOAD = b'{"items":[],"schema":"gold-trade-object-storage-append-only-sync-delta-payload-v1"}'
ISSUED_AT = datetime(2026, 7, 31, 15, 0, 0, tzinfo=timezone.utc)
EXPIRES_AT = ISSUED_AT + timedelta(minutes=4)
SOURCE_PRIVATE_KEY = bytes(range(33, 65))
CONTROLLER_PRIVATE_KEY = bytes(range(1, 33))


def _source_signer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(SOURCE_PRIVATE_KEY)


def _source_public_key() -> bytes:
    return _source_signer().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _controller_public_key() -> bytes:
    return Ed25519PrivateKey.from_private_bytes(CONTROLLER_PRIVATE_KEY).public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _policy() -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket="private-delta-bucket",
        prefix="campaigns/three-site",
        webapp_fi_age_recipient="age1" + "a" * 30,
        webapp_ir_age_recipient="age1" + "c" * 30,
    )


def _snapshot() -> dict[str, object]:
    return {
        "source_generation": "webapp-fi-snapshot-20260731",
        "snapshot_id": "20260731T150000Z-0123456789abcdef",
        "release_sha": RELEASE,
        "alembic_revision": "f2c7d8e9a0b1",
        "manifest_object_key": "campaigns/wa-ir/snapshots/manifest.json.age",
        "manifest_object_version_id": "snapshot-version-20260731-01",
        "manifest_ciphertext_sha256": "a" * 64,
        "manifest_ciphertext_bytes": 1024,
        "database_sha256": "b" * 64,
        "uploads_sha256": "c" * 64,
    }


def _batch(*, first_sequence: int = 1):
    sequence_ids = (first_sequence, first_sequence + 1)
    object_key = derive_object_delta_object_key(
        _policy(),
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        first_sequence=sequence_ids[0],
        last_sequence=sequence_ids[-1],
        payload_sha256=sha256_bytes(PAYLOAD),
    )
    return validate_delta_batch(
        build_delta_batch(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
            stream_generation_id=GENERATION,
            stream_sequence_ids=sequence_ids,
            payload=PAYLOAD,
            prior_chain_sha256=(
                GENESIS_PRIOR_CHAIN_SHA256 if first_sequence == 1 else "e" * 64
            ),
            immutable_receipt={
                "schema": IMMUTABLE_RECEIPT_SCHEMA,
                "status": "read_back_verified",
                "object_kind": "sync_delta_batch",
                "object_key": object_key,
                "version_id": f"version-20260731-{first_sequence}",
                "ciphertext_sha256": "d" * 64,
                "ciphertext_bytes": 1024,
            },
        )
    )


def _authorization(*, first_sequence: int = 1):
    batch = _batch(first_sequence=first_sequence)
    policy = _policy()
    source_public_key = _source_public_key()
    controller_public_key = _controller_public_key()
    controller_key_id = controller_key_id_from_public_key(controller_public_key)
    unsigned_packet = build_unsigned_object_delta_delivery_control_packet(
        policy=policy,
        batch=batch,
        binding=bind_object_delta_batch(policy=policy, batch=batch),
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
        nonce="b" * 64,
        controller_public_key=controller_public_key,
    )
    packet = verify_object_delta_delivery_control_packet(
        sign_object_delta_delivery_control_packet(
            unsigned_packet,
            controller_signer=Ed25519PrivateKey.from_private_bytes(CONTROLLER_PRIVATE_KEY),
        ),
        policy=policy,
        expected_destination_site="webapp_ir",
        pinned_controller_public_key=controller_public_key,
        observed_at=ISSUED_AT + timedelta(seconds=1),
    )
    binding = ObjectDeltaReceiverDeliveryBinding(
        policy=policy,
        permit=ObjectDeltaReceiverDeliveryPermit(
            source_site=batch.source_site,
            destination_site=batch.destination_site,
            campaign_id=batch.campaign_id,
            release_sha=batch.release_sha,
            stream_generation_id=batch.stream.generation_id,
            bucket=policy.bucket,
            destination_age_recipient=packet.destination_age_recipient,
            controller_key_id=controller_key_id,
            writer_epoch=batch.writer_term.epoch,
            writer_lease_id=batch.writer_term.lease_id,
        ),
        source_public_key=source_public_key,
        source_key_id=source_key_id_from_public_key(source_public_key),
        controller_public_key=controller_public_key,
        expected_registry_fingerprint=FINGERPRINT,
    )
    return authorize_object_delta_receiver_delivery(
        binding=binding,
        verified_packet=packet,
        batch=batch,
        source_attestation=build_object_delta_source_batch_attestation(
            batch=batch,
            transport_policy=policy,
            transport_binding=bind_object_delta_batch(policy=policy, batch=batch),
            source_signer=_source_signer(),
        ),
    )


class ObjectDeltaReceiverGenesisAdmissionTests(unittest.TestCase):
    def _verified_inputs(self):
        source_signer = _source_signer()
        source_public_key = _source_public_key()
        write_gate_id = str(uuid4())
        manifest = build_object_delta_baseline_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id=GENERATION,
            registry_fingerprint=FINGERPRINT,
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
            snapshot=_snapshot(),
            write_gate_id=write_gate_id,
            source_signer=source_signer,
        )
        baseline = verify_object_delta_receiver_genesis_baseline(
            manifest,
            expected_source_public_key=source_public_key,
            expected_source_site="webapp_fi",
            expected_destination_site="webapp_ir",
            expected_campaign_id=CAMPAIGN,
            expected_release_sha=RELEASE,
            expected_stream_generation_id=GENERATION,
            expected_registry_fingerprint=FINGERPRINT,
        )
        source = baseline.baseline
        restore = ObjectDeltaReceiverRestoreAttestation(
            source_site=source.source_site,
            destination_site=source.destination_site,
            campaign_id=source.campaign_id,
            release_sha=source.release_sha,
            stream_generation_id=source.stream_generation_id,
            registry_fingerprint=source.registry_fingerprint,
            source_generation=source.source_generation,
            snapshot_id=source.snapshot_id,
            alembic_revision=source.alembic_revision,
            manifest_object_key=source.manifest_object_key,
            manifest_object_version_id=source.manifest_object_version_id,
            manifest_ciphertext_sha256=source.manifest_ciphertext_sha256,
            manifest_ciphertext_bytes=source.manifest_ciphertext_bytes,
            database_sha256=source.database_sha256,
            uploads_sha256=source.uploads_sha256,
        )
        receiver_signer = Ed25519PrivateKey.generate()
        receiver_public_key = receiver_signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        restore_raw = build_object_delta_receiver_restore_evidence(
            restore=restore,
            baseline_manifest_sha256=source.manifest_sha256,
            receiver_verifier_signer=receiver_signer,
        )
        restore_evidence = verify_object_delta_receiver_restore_evidence(
            restore_raw,
            expected_receiver_verifier_public_key=receiver_public_key,
            baseline=baseline,
        )
        cutover_record = ObjectDeltaSourceCutoverRecord(
            source_site=source.source_site,
            destination_site=source.destination_site,
            campaign_id=source.campaign_id,
            release_sha=source.release_sha,
            stream_generation_id=source.stream_generation_id,
            state="baseline_published",
            registry_fingerprint=source.registry_fingerprint,
            writer_epoch=source.writer_epoch,
            writer_lease_id=source.writer_lease_id,
            write_gate_id=source.write_gate_id,
            source_generation=source.source_generation,
            snapshot_id=source.snapshot_id,
            alembic_revision=source.alembic_revision,
            snapshot_manifest_object_key=source.manifest_object_key,
            snapshot_manifest_object_version_id=source.manifest_object_version_id,
            snapshot_manifest_ciphertext_sha256=source.manifest_ciphertext_sha256,
            snapshot_manifest_ciphertext_bytes=source.manifest_ciphertext_bytes,
            database_sha256=source.database_sha256,
            uploads_sha256=source.uploads_sha256,
            baseline_manifest_object_key="campaigns/wa-ir/baseline/manifest.json.age",
            baseline_manifest_object_version_id="baseline-version-20260731-01",
            baseline_manifest_ciphertext_sha256="f" * 64,
            baseline_manifest_ciphertext_bytes=2048,
        )
        cutover_raw = build_object_delta_source_cutover_attestation(
            cutover=cutover_record,
            baseline_manifest=manifest,
            source_signer=source_signer,
        )
        cutover = verify_object_delta_receiver_genesis_cutover(
            cutover_raw,
            expected_source_public_key=source_public_key,
            baseline=baseline,
        )
        return {
            "manifest": manifest,
            "baseline": baseline,
            "restore": restore,
            "restore_raw": restore_raw,
            "restore_public_key": receiver_public_key,
            "restore_evidence": restore_evidence,
            "cutover_raw": cutover_raw,
            "cutover": cutover,
        }

    def _admission(self):
        values = self._verified_inputs()
        authorization = _authorization()
        admission = admit_object_delta_receiver_genesis(
            baseline=values["baseline"],
            restore_evidence=values["restore_evidence"],
            cutover=values["cutover"],
            authorization=authorization,
        )
        return values, authorization, admission

    def test_real_independent_signatures_mint_one_exact_genesis_capability(self):
        _values, authorization, admission = self._admission()

        self.assertIs(
            admission,
            validate_authorized_object_delta_receiver_genesis_admission(admission),
        )
        self.assertIs(
            authorization,
            require_object_delta_receiver_genesis_admission(
                authorization=authorization,
                admission=admission,
            ),
        )
        self.assertEqual(1, admission.authorization.batch.stream.first_sequence)

    def test_directly_constructed_wrappers_and_admission_fail_closed(self):
        values, authorization, admission = self._admission()
        forged_baseline = VerifiedObjectDeltaReceiverGenesisBaseline(
            baseline=values["baseline"].baseline,
        )
        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "not verified"):
            admit_object_delta_receiver_genesis(
                baseline=forged_baseline,
                restore_evidence=values["restore_evidence"],
                cutover=values["cutover"],
                authorization=authorization,
            )

        forged_admission = AuthorizedObjectDeltaReceiverGenesisAdmission(
            baseline=values["baseline"],
            restore_evidence=values["restore_evidence"],
            cutover=values["cutover"],
            authorization=authorization,
        )
        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "was not authorized"):
            validate_authorized_object_delta_receiver_genesis_admission(forged_admission)
        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "was not authorized"):
            validate_authorized_object_delta_receiver_genesis_admission(replace(admission))

    def test_raw_restore_shape_is_never_admission_evidence(self):
        values, authorization, _admission = self._admission()

        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "restore evidence is required"):
            admit_object_delta_receiver_genesis(
                baseline=values["baseline"],
                restore_evidence=values["restore"],
                cutover=values["cutover"],
                authorization=authorization,
            )

    def test_restore_signature_tampering_duplicate_json_and_wrong_pin_fail_closed(self):
        values = self._verified_inputs()
        tampered = copy.deepcopy(values["restore_raw"])
        tampered["restore"]["database_sha256"] = "d" * 64
        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "signature"):
            verify_object_delta_receiver_restore_evidence(
                tampered,
                expected_receiver_verifier_public_key=values["restore_public_key"],
                baseline=values["baseline"],
            )
        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "receiver pin"):
            verify_object_delta_receiver_restore_evidence(
                values["restore_raw"],
                expected_receiver_verifier_public_key=Ed25519PrivateKey.generate()
                .public_key()
                .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
                baseline=values["baseline"],
            )
        raw = json.dumps(values["restore_raw"], sort_keys=True, separators=(",", ":")).encode("utf-8")
        duplicate = raw[:-1] + b',"schema":"duplicate"}'
        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "duplicate"):
            parse_object_delta_receiver_restore_evidence_json(duplicate)
        canonical = canonical_object_delta_receiver_restore_evidence_bytes(values["restore_raw"])
        self.assertEqual(values["restore_raw"], parse_object_delta_receiver_restore_evidence_json(canonical))
        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "canonical"):
            parse_object_delta_receiver_restore_evidence_json(
                json.dumps(values["restore_raw"], sort_keys=True, indent=2).encode("utf-8")
            )

    def test_cutover_tampering_cannot_mint_the_opaque_cutover_capability(self):
        values = self._verified_inputs()
        tampered = copy.deepcopy(values["cutover_raw"])
        tampered["cutover"]["write_gate_id"] = str(uuid4())

        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "cutover signature verification"):
            verify_object_delta_receiver_genesis_cutover(
                tampered,
                expected_source_public_key=_source_public_key(),
                baseline=values["baseline"],
            )

    def test_non_genesis_or_different_delivery_cannot_use_admission(self):
        values = self._verified_inputs()
        later_authorization = _authorization(first_sequence=2)
        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "sequence-one"):
            admit_object_delta_receiver_genesis(
                baseline=values["baseline"],
                restore_evidence=values["restore_evidence"],
                cutover=values["cutover"],
                authorization=later_authorization,
            )

        _values, authorization, admission = self._admission()
        same_metadata_new_object = _authorization()
        with self.assertRaisesRegex(ObjectDeltaReceiverGenesisAdmissionError, "cannot be reused"):
            require_object_delta_receiver_genesis_admission(
                authorization=same_metadata_new_object,
                admission=admission,
            )
        self.assertIsNot(authorization, same_metadata_new_object)

    def test_contract_has_no_runtime_or_transport_dependencies(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "core/object_delta_receiver_genesis_admission.py"
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
