from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    build_delta_batch,
    sha256_bytes,
    validate_delta_batch,
)
from core.object_delta_delivery_control_packet import (
    VerifiedObjectDeltaDeliveryControlPacket,
    build_unsigned_object_delta_delivery_control_packet,
    sign_object_delta_delivery_control_packet,
    verify_object_delta_delivery_control_packet,
)
from core.object_delta_receiver_delivery_nonce import (
    RECEIVER_DELIVERY_NONCE_ACTION_CONSUME,
    RECEIVER_DELIVERY_NONCE_ACTION_REPLAY,
    ObjectDeltaReceiverDeliveryNonceError,
    expected_object_delta_receiver_delivery_nonce_receipt,
    plan_object_delta_receiver_delivery_nonce_consumption,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
)


CAMPAIGN = "wa-ir-delta-nonce-receipt-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-delta-nonce-stream-20260731"
PAYLOAD = b'{"schema":"gold-trade-object-storage-append-only-sync-delta-payload-v1","items":[]}'
ISSUED_AT = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
EXPIRES_AT = ISSUED_AT + timedelta(minutes=4)
OBSERVED_AT = ISSUED_AT + timedelta(seconds=1)
PRIVATE_KEY = bytes(range(1, 33))


def _policy() -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket="private-delta-bucket",
        prefix="campaigns/three-site",
        webapp_fi_age_recipient="age1" + "a" * 30,
        webapp_ir_age_recipient="age1" + "c" * 30,
    )


def _batch(*, writer_epoch: int = 7) -> object:
    object_key = derive_object_delta_object_key(
        _policy(),
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        first_sequence=1,
        last_sequence=2,
        payload_sha256=sha256_bytes(PAYLOAD),
    )
    return validate_delta_batch(
        build_delta_batch(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=writer_epoch,
            writer_lease_id="writer-lease-7",
            stream_generation_id=GENERATION,
            stream_sequence_ids=(1, 2),
            payload=PAYLOAD,
            prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
            immutable_receipt={
                "schema": IMMUTABLE_RECEIPT_SCHEMA,
                "status": "read_back_verified",
                "object_kind": "sync_delta_batch",
                "object_key": object_key,
                "version_id": "version-20260731-02",
                "ciphertext_sha256": "d" * 64,
                "ciphertext_bytes": 1024,
            },
        )
    )


def _controller_public_key() -> bytes:
    return Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _verified_signed_packet(
    *,
    nonce: str = "b" * 64,
    writer_epoch: int = 7,
) -> VerifiedObjectDeltaDeliveryControlPacket:
    batch = _batch(writer_epoch=writer_epoch)
    signer = Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY)
    unsigned = build_unsigned_object_delta_delivery_control_packet(
        policy=_policy(),
        batch=batch,
        binding=bind_object_delta_batch(_policy(), batch),
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
        nonce=nonce,
        controller_public_key=_controller_public_key(),
    )
    sealed = sign_object_delta_delivery_control_packet(unsigned, controller_signer=signer)
    return verify_object_delta_delivery_control_packet(
        sealed,
        policy=_policy(),
        expected_destination_site="webapp_ir",
        pinned_controller_public_key=_controller_public_key(),
        observed_at=OBSERVED_AT,
    )


def _packet(*, nonce: str = "b" * 64, writer_epoch: int = 7) -> VerifiedObjectDeltaDeliveryControlPacket:
    """Return only a real controller-signed and pinned-verified packet."""

    return _verified_signed_packet(nonce=nonce, writer_epoch=writer_epoch)


def _manually_constructed_packet(
    packet: VerifiedObjectDeltaDeliveryControlPacket,
) -> VerifiedObjectDeltaDeliveryControlPacket:
    """A deliberately forged shape for capability-rejection coverage only."""

    return VerifiedObjectDeltaDeliveryControlPacket(
        issued_at=packet.issued_at,
        expires_at=packet.expires_at,
        nonce=packet.nonce,
        controller_key_id=packet.controller_key_id,
        bucket=packet.bucket,
        source_site=packet.source_site,
        destination_site=packet.destination_site,
        destination_age_recipient=packet.destination_age_recipient,
        campaign_id=packet.campaign_id,
        release_sha=packet.release_sha,
        writer_epoch=packet.writer_epoch,
        writer_lease_id=packet.writer_lease_id,
        stream_generation_id=packet.stream_generation_id,
        first_sequence=packet.first_sequence,
        last_sequence=packet.last_sequence,
        prior_chain_sha256=packet.prior_chain_sha256,
        batch_sha256=packet.batch_sha256,
        payload_sha256=packet.payload_sha256,
        object_key=packet.object_key,
        object_version_id=packet.object_version_id,
        ciphertext_sha256=packet.ciphertext_sha256,
        ciphertext_bytes=packet.ciphertext_bytes,
    )


class ObjectDeltaReceiverDeliveryNonceTests(unittest.TestCase):
    def test_receipt_covers_the_exact_verified_packet_and_batch(self) -> None:
        batch = _batch()
        receipt = expected_object_delta_receiver_delivery_nonce_receipt(
            packet=_packet(), batch=batch, observed_at=OBSERVED_AT
        )

        self.assertEqual(_packet().controller_key_id, receipt.controller_key_id)
        self.assertEqual("b" * 64, receipt.nonce)
        self.assertEqual("private-delta-bucket", receipt.bucket)
        self.assertEqual("age1" + "c" * 30, receipt.destination_age_recipient)
        self.assertEqual(batch.batch_sha256, receipt.batch_sha256)
        self.assertEqual(batch.immutable_receipt.version_id, receipt.object_version_id)
        self.assertEqual(64, len(receipt.packet_claim_sha256))
        self.assertEqual(EXPIRES_AT, receipt.expires_at)

    def test_packet_and_batch_must_match_before_a_nonce_is_consumable(self) -> None:
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryNonceError, "does not match"):
            expected_object_delta_receiver_delivery_nonce_receipt(
                packet=_packet(), batch=_batch(writer_epoch=8), observed_at=OBSERVED_AT
            )

    def test_first_consumption_inserts_and_exact_replay_is_zero_write(self) -> None:
        receipt = expected_object_delta_receiver_delivery_nonce_receipt(
            packet=_packet(), batch=_batch(), observed_at=OBSERVED_AT
        )
        first = plan_object_delta_receiver_delivery_nonce_consumption(
            expected=receipt, existing=None
        )
        replay = plan_object_delta_receiver_delivery_nonce_consumption(
            expected=receipt, existing=receipt
        )

        self.assertEqual(RECEIVER_DELIVERY_NONCE_ACTION_CONSUME, first.action)
        self.assertEqual(receipt, first.receipt_to_insert)
        self.assertEqual(RECEIVER_DELIVERY_NONCE_ACTION_REPLAY, replay.action)
        self.assertIsNone(replay.receipt_to_insert)

    def test_reused_nonce_with_a_different_claim_fails_closed(self) -> None:
        receipt = expected_object_delta_receiver_delivery_nonce_receipt(
            packet=_packet(), batch=_batch(), observed_at=OBSERVED_AT
        )
        conflicting = replace(receipt, batch_sha256="e" * 64)
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryNonceError, "conflicts"):
            plan_object_delta_receiver_delivery_nonce_consumption(
                expected=receipt,
                existing=conflicting,
            )

    def test_manually_constructed_or_replaced_packet_is_rejected(self) -> None:
        verified = _packet()
        for packet in (
            _manually_constructed_packet(verified),
            replace(verified, source_site="not-a-site"),
        ):
            with self.subTest(packet=packet):
                with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryNonceError, "verified delivery packet"):
                    expected_object_delta_receiver_delivery_nonce_receipt(
                        packet=packet,
                        batch=_batch(),
                        observed_at=OBSERVED_AT,
                    )

    def test_signed_packet_verification_precedes_nonce_receipt_derivation(self) -> None:
        packet = _verified_signed_packet()

        receipt = expected_object_delta_receiver_delivery_nonce_receipt(
            packet=packet,
            batch=_batch(),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(packet.controller_key_id, receipt.controller_key_id)
        self.assertEqual(packet.bucket, receipt.bucket)
        self.assertEqual(packet.destination_age_recipient, receipt.destination_age_recipient)

    def test_replaced_claim_fields_cannot_derive_a_second_nonce_receipt(self) -> None:
        for changed_packet in (
            replace(_packet(), bucket="other-private-delta-bucket"),
            replace(_packet(), destination_age_recipient="age1" + "a" * 30),
        ):
            with self.subTest(changed_packet=changed_packet):
                with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryNonceError, "verified delivery packet"):
                    expected_object_delta_receiver_delivery_nonce_receipt(
                        packet=changed_packet,
                        batch=_batch(),
                        observed_at=OBSERVED_AT,
                    )

    def test_receipt_derivation_rejects_an_expired_verified_packet(self) -> None:
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryNonceError, "not currently valid"):
            expected_object_delta_receiver_delivery_nonce_receipt(
                packet=_packet(),
                batch=_batch(),
                observed_at=EXPIRES_AT,
            )
