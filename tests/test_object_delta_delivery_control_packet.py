from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    build_delta_batch,
    validate_delta_batch,
)
from core.object_delta_delivery_control_packet import (
    MAX_CONTROL_PACKET_TTL,
    ObjectDeltaDeliveryControlPacketError,
    ObjectDeltaReceiverDeliveryPermit,
    VerifiedObjectDeltaDeliveryControlPacket,
    assert_verified_delivery_matches_receiver_permit,
    assert_verified_delivery_matches_batch,
    build_unsigned_object_delta_delivery_control_packet,
    controller_key_id_from_public_key,
    revalidate_verified_object_delta_delivery_control_packet,
    sign_object_delta_delivery_control_packet,
    unsigned_object_delta_delivery_control_packet_payload,
    verify_object_delta_delivery_control_packet,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
)


CAMPAIGN = "wa-ir-delta-delivery-control-20260730"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-delta-delivery-stream-20260730"
PAYLOAD = b'{"schema":"gold-trade-object-storage-append-only-sync-delta-payload-v1","items":[]}'
ISSUED_AT = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
EXPIRES_AT = ISSUED_AT + timedelta(minutes=4)
NONCE = "b" * 64
FI_RECIPIENT = "age1" + "a" * 30
IR_RECIPIENT = "age1" + "c" * 30
PRIVATE_KEY = bytes(range(1, 33))


def policy() -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket="private-delta-bucket",
        prefix="campaigns/three-site",
        webapp_fi_age_recipient=FI_RECIPIENT,
        webapp_ir_age_recipient=IR_RECIPIENT,
    )


def public_key() -> bytes:
    return signer().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def signer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY)


def batch(*, writer_epoch: int = 7) -> object:
    from core.append_only_sync_delta_batch import sha256_bytes

    object_key = derive_object_delta_object_key(
        policy(),
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
                "version_id": "version-20260730-02",
                "ciphertext_sha256": "d" * 64,
                "ciphertext_bytes": 1024,
            },
        )
    )


def unsigned_packet() -> dict:
    value = batch()
    return build_unsigned_object_delta_delivery_control_packet(
        policy=policy(),
        batch=value,
        binding=bind_object_delta_batch(policy(), value),
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
        nonce=NONCE,
        controller_public_key=public_key(),
    )


def sealed_packet() -> dict:
    return sign_object_delta_delivery_control_packet(
        unsigned_packet(),
        controller_signer=signer(),
    )


def receiver_permit(
    *,
    writer_epoch: int = 7,
    writer_lease_id: str = "writer-lease-7",
) -> ObjectDeltaReceiverDeliveryPermit:
    return ObjectDeltaReceiverDeliveryPermit(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        bucket="private-delta-bucket",
        destination_age_recipient=IR_RECIPIENT,
        controller_key_id=controller_key_id_from_public_key(public_key()),
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
    )


class ObjectDeltaDeliveryControlPacketTests(unittest.TestCase):
    def test_valid_packet_binds_exact_object_and_batch_after_download(self) -> None:
        sealed = sealed_packet()

        verified = verify_object_delta_delivery_control_packet(
            sealed,
            policy=policy(),
            expected_destination_site="webapp_ir",
            pinned_controller_public_key=public_key(),
            observed_at=ISSUED_AT + timedelta(seconds=1),
        )
        binding = assert_verified_delivery_matches_batch(
            verified,
            policy=policy(),
            batch=batch(),
        )
        permit = assert_verified_delivery_matches_receiver_permit(
            verified,
            policy=policy(),
            permit=receiver_permit(),
        )

        self.assertEqual("webapp_ir", verified.destination_site)
        self.assertEqual(IR_RECIPIENT, verified.destination_age_recipient)
        self.assertEqual("version-20260730-02", verified.object_version_id)
        self.assertEqual(binding.object_key, verified.object_key)
        self.assertEqual("writer-lease-7", permit.writer_lease_id)
        self.assertEqual(controller_key_id_from_public_key(public_key()), verified.controller_key_id)

    def test_unsigned_payload_is_deterministic_and_has_no_url_credential_or_payload_bytes(self) -> None:
        packet = unsigned_packet()
        first = unsigned_object_delta_delivery_control_packet_payload(packet)
        reordered = {key: packet[key] for key in reversed(tuple(packet))}
        second = unsigned_object_delta_delivery_control_packet_payload(reordered)

        self.assertEqual(first, second)
        self.assertNotIn(b"://", first)
        self.assertNotIn(b"credential", first.lower())
        self.assertNotIn(b"payload_bytes", first)
        self.assertNotIn(b"presigned", first.lower())
        self.assertNotIn("payload_bytes", packet["delivery"])
        self.assertNotIn("url", packet["delivery"])

    def test_tampered_immutable_version_or_recipient_fails_signature_verification(self) -> None:
        for field, replacement in (
            ("object_version_id", "version-20260730-03"),
            ("destination_age_recipient", FI_RECIPIENT),
            ("ciphertext_sha256", "e" * 64),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(sealed_packet())
                tampered["delivery"][field] = replacement
                with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "signature verification"):
                    verify_object_delta_delivery_control_packet(
                        tampered,
                        policy=policy(),
                        expected_destination_site="webapp_ir",
                        pinned_controller_public_key=public_key(),
                        observed_at=ISSUED_AT + timedelta(seconds=1),
                    )

    def test_signed_packet_with_wrong_recipient_is_rejected_against_local_policy(self) -> None:
        wrong_recipient = unsigned_packet()
        wrong_recipient["delivery"]["destination_age_recipient"] = FI_RECIPIENT
        sealed = sign_object_delta_delivery_control_packet(
            wrong_recipient,
            controller_signer=signer(),
        )

        with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "recipient does not match"):
            verify_object_delta_delivery_control_packet(
                sealed,
                policy=policy(),
                expected_destination_site="webapp_ir",
                pinned_controller_public_key=public_key(),
                observed_at=ISSUED_AT + timedelta(seconds=1),
            )

    def test_expired_wrong_destination_and_wrong_policy_fail_closed(self) -> None:
        sealed = sealed_packet()
        with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "not currently valid"):
            verify_object_delta_delivery_control_packet(
                sealed,
                policy=policy(),
                expected_destination_site="webapp_ir",
                pinned_controller_public_key=public_key(),
                observed_at=EXPIRES_AT,
            )
        with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "destination does not match"):
            verify_object_delta_delivery_control_packet(
                sealed,
                policy=policy(),
                expected_destination_site="webapp_fi",
                pinned_controller_public_key=public_key(),
                observed_at=ISSUED_AT + timedelta(seconds=1),
            )
        with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "bucket does not match"):
            verify_object_delta_delivery_control_packet(
                sealed,
                policy=ObjectDeltaTransportPolicy(
                    bucket="other-private-delta-bucket",
                    prefix="campaigns/three-site",
                    webapp_fi_age_recipient=FI_RECIPIENT,
                    webapp_ir_age_recipient=IR_RECIPIENT,
                ),
                expected_destination_site="webapp_ir",
                pinned_controller_public_key=public_key(),
                observed_at=ISSUED_AT + timedelta(seconds=1),
            )

    def test_packet_builder_rejects_unmatched_binding_and_excessive_expiry(self) -> None:
        value = batch()
        binding = bind_object_delta_batch(policy(), value)
        with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "does not match the batch"):
            build_unsigned_object_delta_delivery_control_packet(
                policy=policy(),
                batch=value,
                binding=type(binding)(
                    **{**binding.__dict__, "object_version_id": "version-20260730-other"}
                ),
                issued_at=ISSUED_AT,
                expires_at=EXPIRES_AT,
                nonce=NONCE,
                controller_public_key=public_key(),
            )
        with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "expiry window"):
            build_unsigned_object_delta_delivery_control_packet(
                policy=policy(),
                batch=value,
                binding=binding,
                issued_at=ISSUED_AT,
                expires_at=ISSUED_AT + MAX_CONTROL_PACKET_TTL + timedelta(seconds=1),
                nonce=NONCE,
                controller_public_key=public_key(),
            )

    def test_post_download_batch_mismatch_is_rejected_even_with_a_valid_signature(self) -> None:
        verified = verify_object_delta_delivery_control_packet(
            sealed_packet(),
            policy=policy(),
            expected_destination_site="webapp_ir",
            pinned_controller_public_key=public_key(),
            observed_at=ISSUED_AT + timedelta(seconds=1),
        )
        changed = batch(writer_epoch=8)

        with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "does not match the downloaded"):
            assert_verified_delivery_matches_batch(
                verified,
                policy=policy(),
                batch=changed,
            )

    def test_root_only_receiver_permit_contract_requires_exact_witness_term(self) -> None:
        verified = verify_object_delta_delivery_control_packet(
            sealed_packet(),
            policy=policy(),
            expected_destination_site="webapp_ir",
            pinned_controller_public_key=public_key(),
            observed_at=ISSUED_AT + timedelta(seconds=1),
        )

        with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "receiver delivery permit"):
            assert_verified_delivery_matches_receiver_permit(
                verified,
                policy=policy(),
                permit=replace(receiver_permit(), writer_lease_id="writer-lease-8"),
            )

    def test_direct_or_replaced_verified_packet_cannot_bypass_signature_verification(self) -> None:
        verified = verify_object_delta_delivery_control_packet(
            sealed_packet(),
            policy=policy(),
            expected_destination_site="webapp_ir",
            pinned_controller_public_key=public_key(),
            observed_at=ISSUED_AT + timedelta(seconds=1),
        )
        manually_constructed = VerifiedObjectDeltaDeliveryControlPacket(
            **{
                field_name: getattr(verified, field_name)
                for field_name, definition in verified.__dataclass_fields__.items()
                if definition.init
            }
        )
        for forged in (
            manually_constructed,
            replace(verified, nonce="c" * 64),
        ):
            with self.subTest(forged=forged):
                with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "not produced"):
                    revalidate_verified_object_delta_delivery_control_packet(forged)
                with self.assertRaisesRegex(ObjectDeltaDeliveryControlPacketError, "not produced"):
                    assert_verified_delivery_matches_batch(
                        forged,
                        policy=policy(),
                        batch=batch(),
                    )


if __name__ == "__main__":
    unittest.main()
