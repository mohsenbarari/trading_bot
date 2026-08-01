from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    build_delta_batch,
    canonical_json_bytes,
    sha256_bytes,
    validate_delta_batch,
)
from core.append_only_sync_delta_payload import build_object_delta_payload
from core.object_delta_delivery_control_packet import (
    ObjectDeltaReceiverDeliveryPermit,
    build_unsigned_object_delta_delivery_control_packet,
    controller_key_id_from_public_key,
    sign_object_delta_delivery_control_packet,
    verify_object_delta_delivery_control_packet,
)
from core.object_delta_receiver_apply_scope import authorize_object_delta_receiver_delivery
from core.object_delta_receiver_delivery_binding import ObjectDeltaReceiverDeliveryBinding
from core.object_delta_receiver_payload_admission import (
    AuthorizedObjectDeltaReceiverPayload,
    ObjectDeltaReceiverPayloadAdmissionError,
    authorize_object_delta_receiver_payload,
    plan_authorized_object_delta_receiver_payload_import,
    require_authorized_object_delta_receiver_payload,
)
from core.object_delta_source_batch_attestation import (
    build_object_delta_source_batch_attestation,
    source_key_id_from_public_key,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
)
from core.sync_metadata import build_sync_metadata, build_sync_public_identity
from core.sync_protocol import (
    SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
    SYNC_PAYLOAD_SCHEMA_VERSION,
    SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
    SYNC_PROTOCOL_VERSION,
    SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
    SYNC_REGISTRY_VERSION,
)


CAMPAIGN = "wa-ir-receiver-payload-admission-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-receiver-payload-admission-20260731"
FINGERPRINT = "0123456789abcdef"
ISSUED_AT = datetime(2026, 7, 31, 16, 0, 0, tzinfo=timezone.utc)
EXPIRES_AT = ISSUED_AT + timedelta(minutes=4)
CONTROLLER_PRIVATE_KEY = bytes(range(1, 33))
SOURCE_PRIVATE_KEY = bytes(range(33, 65))


def _controller_signer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(CONTROLLER_PRIVATE_KEY)


def _controller_public_key() -> bytes:
    return _controller_signer().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _source_signer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(SOURCE_PRIVATE_KEY)


def _source_public_key() -> bytes:
    return _source_signer().public_key().public_bytes(
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


def _payload_value(
    *,
    registry_fingerprint: str,
    table: str = "users",
    operation: str = "UPDATE",
    source_server: str = "foreign",
    data: dict[str, object] | None = None,
) -> dict[str, object]:
    if data is None:
        data = {"id": 101, "full_name": "Registry Bound User"}
    record_id = data["id"]
    assert type(record_id) is int
    item: dict[str, object] = {
        "logical_sequence": 1,
        "type": "db_change",
        "operation": operation,
        "table": table,
        "id": record_id,
        "data": data,
        "hash": sha256_bytes(canonical_json_bytes({"change_log_id": 41})),
        "timestamp": 1_785_000_000.0,
        "change_log_id": 41,
        "sync_protocol": {
            "protocol_version": SYNC_PROTOCOL_VERSION,
            "min_consumer_protocol_version": SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
            "payload_schema_version": SYNC_PAYLOAD_SCHEMA_VERSION,
            "min_consumer_payload_schema_version": SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
            "registry_version": SYNC_REGISTRY_VERSION,
            "min_consumer_registry_version": SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
            "registry_fingerprint": registry_fingerprint,
            "producer": {"server_mode": source_server},
        },
        "sync_meta": build_sync_metadata(
            table,
            record_id,
            operation,
            data,
            change_log_id=41,
            source_server=source_server,
        ),
    }
    public_identity = build_sync_public_identity(table, record_id, data)
    if public_identity is not None:
        item["public_identity"] = public_identity
    return build_object_delta_payload(
        stream_generation_id=GENERATION,
        items=(item,),
    )


def _batch_and_payload(
    *,
    payload_registry_fingerprint: str,
    terminal_newline: bool,
    source_site: str = "webapp_fi",
    destination_site: str = "webapp_ir",
    source_server: str = "foreign",
    table: str = "users",
    operation: str = "UPDATE",
    data: dict[str, object] | None = None,
):
    raw = canonical_json_bytes(
        _payload_value(
            registry_fingerprint=payload_registry_fingerprint,
            table=table,
            operation=operation,
            source_server=source_server,
            data=data,
        )
    )
    if terminal_newline:
        raw += b"\n"
    payload_hash = sha256_bytes(raw)
    object_key = derive_object_delta_object_key(
        _policy(),
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        first_sequence=1,
        last_sequence=1,
        payload_sha256=payload_hash,
    )
    batch = validate_delta_batch(
        build_delta_batch(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
            stream_generation_id=GENERATION,
            stream_sequence_ids=(1,),
            payload=raw,
            prior_chain_sha256=GENESIS_PRIOR_CHAIN_SHA256,
            immutable_receipt={
                "schema": IMMUTABLE_RECEIPT_SCHEMA,
                "status": "read_back_verified",
                "object_kind": "sync_delta_batch",
                "object_key": object_key,
                "version_id": "version-20260731-payload-admission-01",
                "ciphertext_sha256": "d" * 64,
                "ciphertext_bytes": 1024,
            },
        )
    )
    return batch, raw


def _authorization(
    *,
    payload_registry_fingerprint: str = FINGERPRINT,
    expected_registry_fingerprint: str = FINGERPRINT,
    terminal_newline: bool = False,
    source_site: str = "webapp_fi",
    destination_site: str = "webapp_ir",
    source_server: str = "foreign",
    table: str = "users",
    operation: str = "UPDATE",
    data: dict[str, object] | None = None,
):
    batch, raw = _batch_and_payload(
        payload_registry_fingerprint=payload_registry_fingerprint,
        terminal_newline=terminal_newline,
        source_site=source_site,
        destination_site=destination_site,
        source_server=source_server,
        table=table,
        operation=operation,
        data=data,
    )
    policy = _policy()
    controller_public_key = _controller_public_key()
    source_public_key = _source_public_key()
    controller_key_id = controller_key_id_from_public_key(controller_public_key)
    packet = verify_object_delta_delivery_control_packet(
        sign_object_delta_delivery_control_packet(
            build_unsigned_object_delta_delivery_control_packet(
                policy=policy,
                batch=batch,
                binding=bind_object_delta_batch(policy=policy, batch=batch),
                issued_at=ISSUED_AT,
                expires_at=EXPIRES_AT,
                nonce="b" * 64,
                controller_public_key=controller_public_key,
            ),
            controller_signer=_controller_signer(),
        ),
        policy=policy,
        expected_destination_site=destination_site,
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
        expected_registry_fingerprint=expected_registry_fingerprint,
    )
    authorization = authorize_object_delta_receiver_delivery(
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
    return authorization, raw


class ObjectDeltaReceiverPayloadAdmissionTests(unittest.TestCase):
    def test_exact_source_attested_payload_is_admitted_and_planned_from_the_local_pin(self):
        authorization, raw = _authorization(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            source_server="iran",
            table="commodities",
            operation="INSERT",
            data={"id": 101, "name": "Registry Bound Commodity"},
        )

        admitted = authorize_object_delta_receiver_payload(
            authorization=authorization,
            raw_payload=raw,
        )
        plan = plan_authorized_object_delta_receiver_payload_import(
            payload_admission=admitted,
            receiver_cursor=None,
            receipt_by_object=None,
            receipt_by_stream=None,
        )

        self.assertIs(admitted, require_authorized_object_delta_receiver_payload(admitted))
        self.assertEqual(FINGERPRINT, admitted.registry_fingerprint)
        self.assertEqual(authorization.batch.payload_sha256, admitted.payload_sha256)
        self.assertEqual((1,), tuple(change.logical_sequence for change in plan.changes_to_apply))
        self.assertEqual("Registry Bound Commodity", plan.changes_to_apply[0].intent.name)

    def test_broad_sync_payload_can_be_admitted_but_cannot_cross_the_receiver_execution_registry(self):
        authorization, raw = _authorization()
        admitted = authorize_object_delta_receiver_payload(
            authorization=authorization,
            raw_payload=raw,
        )

        with self.assertRaisesRegex(ObjectDeltaReceiverPayloadAdmissionError, "cannot derive an import plan"):
            plan_authorized_object_delta_receiver_payload_import(
                payload_admission=admitted,
                receiver_cursor=None,
                receipt_by_object=None,
                receipt_by_stream=None,
            )

    def test_payload_registry_mismatch_cannot_be_authorized_even_when_batch_packet_and_source_signature_are_valid(self):
        authorization, raw = _authorization(payload_registry_fingerprint="f" * 16)

        with self.assertRaisesRegex(ObjectDeltaReceiverPayloadAdmissionError, "registry pin"):
            authorize_object_delta_receiver_payload(
                authorization=authorization,
                raw_payload=raw,
            )

    def test_exact_hash_and_byte_count_are_checked_before_payload_parsing(self):
        authorization, raw = _authorization()

        with self.assertRaisesRegex(ObjectDeltaReceiverPayloadAdmissionError, "byte count"):
            authorize_object_delta_receiver_payload(
                authorization=authorization,
                raw_payload=raw + b" ",
            )
        altered = bytearray(raw)
        altered[-1] = ord("0") if altered[-1] != ord("0") else ord("1")
        with self.assertRaisesRegex(ObjectDeltaReceiverPayloadAdmissionError, "hash"):
            authorize_object_delta_receiver_payload(
                authorization=authorization,
                raw_payload=bytes(altered),
            )

    def test_canonical_terminal_newline_is_bound_when_it_is_the_exact_signed_plaintext(self):
        authorization, raw = _authorization(terminal_newline=True)

        admitted = authorize_object_delta_receiver_payload(
            authorization=authorization,
            raw_payload=raw,
        )

        self.assertTrue(admitted.payload_had_terminal_newline)
        self.assertEqual(len(raw), admitted.payload_bytes)

    def test_direct_or_replaced_payload_authority_cannot_reach_planning(self):
        authorization, raw = _authorization()
        admitted = authorize_object_delta_receiver_payload(
            authorization=authorization,
            raw_payload=raw,
        )
        direct = AuthorizedObjectDeltaReceiverPayload(
            authorization=admitted.authorization,
            payload=admitted.payload,
            registry_fingerprint=admitted.registry_fingerprint,
            payload_sha256=admitted.payload_sha256,
            payload_bytes=admitted.payload_bytes,
            payload_had_terminal_newline=admitted.payload_had_terminal_newline,
        )
        for forged in (direct, replace(admitted, registry_fingerprint="f" * 16)):
            with self.subTest(forged=forged):
                with self.assertRaisesRegex(ObjectDeltaReceiverPayloadAdmissionError, "not admitted"):
                    require_authorized_object_delta_receiver_payload(forged)
                with self.assertRaisesRegex(ObjectDeltaReceiverPayloadAdmissionError, "not admitted"):
                    plan_authorized_object_delta_receiver_payload_import(
                        payload_admission=forged,
                        receiver_cursor=None,
                        receipt_by_object=None,
                        receipt_by_stream=None,
                    )

    def test_has_no_runtime_or_transport_dependencies(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "core/object_delta_receiver_payload_admission.py"
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
