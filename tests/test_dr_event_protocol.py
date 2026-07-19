from __future__ import annotations

import copy
import unittest

from core.dr_event_protocol import (
    DrEventProtocolError,
    decide_receipt,
    initial_delivery_destinations,
    relay_destinations,
    sha256_json,
    transport_peers,
    transaction_hash_from_envelopes,
    destination_transaction_hash,
    validate_transport_path,
    validate_envelope,
)
from core.dr_data_policy import canonical_dr_row_payload, event_policy_rejection_reason


def envelope(*, sequence: int = 1, event_id: str = "12345678-1234-4234-8234-123456789abc"):
    payload = {"offer_public_id": "offer-1", "status": "active"}
    return {
        "protocol_version": 1,
        "event_id": event_id,
        "origin_authority": "webapp",
        "origin_physical_site": "webapp_ir",
        "producer_epoch": 7,
        "producer_sequence": sequence,
        "aggregate_type": "offers",
        "aggregate_id": "offer-1",
        "aggregate_db_id": "42",
        "aggregate_version": 3,
        "operation": "UPDATE",
        "canonical_payload": payload,
        "canonical_payload_hash": sha256_json(payload),
        "schema_version": 1,
        "causation_id": "55",
        "idempotency_key": "offer-1-v3",
        "writer_epoch": 7,
        "tombstone": False,
        "created_at": "2026-07-19T12:00:00+00:00",
    }


class DrEventProtocolTests(unittest.TestCase):
    def test_exact_envelope_is_canonical_and_relay_identity_is_unchanged(self):
        first = validate_envelope(envelope())
        relayed = validate_envelope(copy.deepcopy(first.payload))
        self.assertEqual(relayed.envelope_hash, first.envelope_hash)
        self.assertEqual(relay_destinations("webapp_ir", "webapp_fi"), ("bot_fi",))

    def test_gap_duplicate_and_conflict_decisions_are_distinct(self):
        incoming = validate_envelope(envelope(sequence=4))
        gap = decide_receipt(contiguous_sequence=1, incoming=incoming)
        self.assertEqual((gap.action, gap.missing_from, gap.missing_to), ("blocked_gap", 2, 3))
        duplicate = decide_receipt(
            contiguous_sequence=4,
            incoming=incoming,
            existing_event_hash=incoming.envelope_hash,
        )
        self.assertEqual(duplicate.action, "duplicate")
        conflict = decide_receipt(
            contiguous_sequence=3,
            incoming=incoming,
            existing_sequence_hash="f" * 64,
        )
        self.assertEqual(conflict.reason, "same_sequence_different_hash")

    def test_payload_tamper_wrong_epoch_and_missing_metadata_fail_closed(self):
        tampered = envelope()
        tampered["canonical_payload"]["status"] = "completed"
        with self.assertRaisesRegex(DrEventProtocolError, "hash mismatch"):
            validate_envelope(tampered)
        wrong_epoch = envelope()
        wrong_epoch["writer_epoch"] = 8
        with self.assertRaisesRegex(DrEventProtocolError, "must equal"):
            validate_envelope(wrong_epoch)
        missing = envelope()
        del missing["producer_sequence"]
        with self.assertRaisesRegex(DrEventProtocolError, "fields"):
            validate_envelope(missing)

    def test_delete_requires_tombstone_and_foreign_event_has_no_writer_epoch(self):
        deleted = envelope()
        deleted["operation"] = "DELETE"
        with self.assertRaisesRegex(DrEventProtocolError, "tombstone"):
            validate_envelope(deleted)
        foreign = envelope()
        foreign.update(
            origin_authority="foreign",
            origin_physical_site="bot_fi",
            producer_epoch=11,
            writer_epoch=None,
        )
        validate_envelope(foreign)

    def test_sparse_topology_and_private_webapp_rows_never_reach_bot(self):
        self.assertEqual(transport_peers("bot_fi"), ("webapp_fi",))
        self.assertEqual(
            initial_delivery_destinations("webapp_fi", aggregate_type="messages"),
            ("webapp_ir",),
        )
        self.assertEqual(
            initial_delivery_destinations("webapp_ir", aggregate_type="messages"),
            ("webapp_fi",),
        )
        self.assertEqual(
            relay_destinations(
                "webapp_ir", "webapp_fi", aggregate_type="messages"
            ),
            (),
        )

    def test_private_webapp_replica_retains_required_sensitive_columns(self):
        payload = {
            "id": 7,
            "invitation_id": 9,
            "normalized_mobile": "09121112233",
            "normalized_account_name": "alice",
        }
        self.assertEqual(
            canonical_dr_row_payload("invitation_identity_reservations", payload),
            payload,
        )
        with self.assertRaises(DrEventProtocolError):
            validate_transport_path(
                origin_site="webapp_ir",
                sender_site="webapp_ir",
                destination_site="bot_fi",
            )

    def test_data_policy_rejects_private_replica_at_bot_and_forbidden_fields(self):
        self.assertEqual(
            event_policy_rejection_reason(
                table_name="messages",
                origin_authority="webapp",
                origin_site="webapp_ir",
                destination_site="bot_fi",
                payload={"id": "m-1"},
            ),
            "webapp_replica_destination_forbidden",
        )
        self.assertEqual(
            event_policy_rejection_reason(
                table_name="users",
                origin_authority="webapp",
                origin_site="webapp_fi",
                destination_site="webapp_ir",
                payload={"id": 1, "admin_password_hash": "forbidden"},
            ),
            "forbidden_or_unsanitized_payload_fields",
        )

    def test_origin_authority_must_match_physical_producer(self):
        invalid = envelope()
        invalid["origin_authority"] = "foreign"
        with self.assertRaisesRegex(DrEventProtocolError, "physical producer"):
            validate_envelope(invalid)

    def test_protocol_v2_binds_every_member_to_one_transaction_hash(self):
        first = envelope(sequence=10, event_id="12345678-1234-4234-8234-123456789ab1")
        second = envelope(sequence=11, event_id="12345678-1234-4234-8234-123456789ab2")
        for position, item in enumerate((first, second), 1):
            item.update(
                protocol_version=2,
                transaction_id="12345678-1234-4234-8234-123456789abc",
                transaction_position=position,
                transaction_size=2,
                transaction_hash="0" * 64,
                destination_streams={
                    "webapp_fi": {
                        "sequence": position,
                        "transaction_id": "12345678-1234-4234-8234-123456789abc",
                        "transaction_position": position,
                        "transaction_size": 2,
                        "transaction_hash": "0" * 64,
                    }
                },
            )
        group_hash = transaction_hash_from_envelopes([first, second])
        destination_hash = destination_transaction_hash(
            [first, second], destination_site="webapp_fi"
        )
        first["transaction_hash"] = group_hash
        second["transaction_hash"] = group_hash
        first["destination_streams"]["webapp_fi"]["transaction_hash"] = destination_hash
        second["destination_streams"]["webapp_fi"]["transaction_hash"] = destination_hash

        validate_envelope(first)
        validate_envelope(second)
        tampered = copy.deepcopy(second)
        tampered["transaction_position"] = 1
        self.assertNotEqual(
            transaction_hash_from_envelopes([first, tampered]),
            group_hash,
        )

    def test_destination_streams_remove_private_event_gaps_without_leaking_payload(self):
        product = envelope(sequence=20, event_id="12345678-1234-4234-8234-123456789ab3")
        private = envelope(sequence=21, event_id="12345678-1234-4234-8234-123456789ab4")
        product["aggregate_type"] = "offers"
        private["aggregate_type"] = "messages"
        transaction_id = "12345678-1234-4234-8234-123456789abd"
        for position, item in enumerate((product, private), 1):
            item.update(
                protocol_version=2,
                transaction_id=transaction_id,
                transaction_position=position,
                transaction_size=2,
                transaction_hash="0" * 64,
            )
        product["destination_streams"] = {
            "webapp_fi": {
                "sequence": 10, "transaction_id": transaction_id,
                "transaction_position": 1, "transaction_size": 2,
                "transaction_hash": "0" * 64,
            },
            "bot_fi": {
                "sequence": 7, "transaction_id": transaction_id,
                "transaction_position": 1, "transaction_size": 1,
                "transaction_hash": "0" * 64,
            },
        }
        private["destination_streams"] = {
            "webapp_fi": {
                "sequence": 11, "transaction_id": transaction_id,
                "transaction_position": 2, "transaction_size": 2,
                "transaction_hash": "0" * 64,
            },
        }
        global_hash = transaction_hash_from_envelopes([product, private])
        for item in (product, private):
            item["transaction_hash"] = global_hash
        webapp_hash = destination_transaction_hash(
            [product, private], destination_site="webapp_fi"
        )
        bot_hash = destination_transaction_hash([product], destination_site="bot_fi")
        for item in (product, private):
            item["destination_streams"]["webapp_fi"]["transaction_hash"] = webapp_hash
        product["destination_streams"]["bot_fi"]["transaction_hash"] = bot_hash

        product_validated = validate_envelope(product)
        private_validated = validate_envelope(private)
        self.assertEqual(product_validated.destination_stream("bot_fi")["sequence"], 7)
        with self.assertRaisesRegex(DrEventProtocolError, "not authorized"):
            private_validated.destination_stream("bot_fi")
