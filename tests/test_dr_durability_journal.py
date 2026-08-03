from __future__ import annotations

import copy
from datetime import datetime, timezone
import unittest

from core.dr_durability_journal import (
    DurabilityJournalError,
    acknowledgement_payload,
    build_prepare,
    decrypt_prepare,
    parse_prepare,
    verify_acknowledgement,
)
from core.dr_event_protocol import (
    destination_transaction_hash,
    sha256_json,
    transaction_hash_from_envelopes,
)
from core.dr_sync_auth import sign_acknowledgement


_SECRET = "journal-encryption-secret-is-at-least-32-bytes"
_HMAC_SECRET = "journal-transport-secret-is-at-least-32-bytes"
_TRANSACTION_ID = "12345678-1234-4234-8234-123456789abc"


def _transaction() -> list[dict]:
    items: list[dict] = []
    for position, event_id in enumerate(
        (
            "12345678-1234-4234-8234-123456789ab1",
            "12345678-1234-4234-8234-123456789ab2",
        ),
        1,
    ):
        payload = {"id": position, "normalized_mobile": f"0912000000{position}"}
        items.append(
            {
                "protocol_version": 2,
                "event_id": event_id,
                "origin_authority": "webapp",
                "origin_physical_site": "webapp_fi",
                "producer_epoch": 7,
                "producer_sequence": position,
                "aggregate_type": "messages",
                "aggregate_id": f"message-{position}",
                "aggregate_db_id": str(position),
                "aggregate_version": position,
                "operation": "INSERT",
                "canonical_payload": payload,
                "canonical_payload_hash": sha256_json(payload),
                "schema_version": 1,
                "causation_id": None,
                "idempotency_key": f"message-{position}",
                "writer_epoch": 7,
                "tombstone": False,
                "created_at": "2026-08-03T12:00:00+00:00",
                "transaction_id": _TRANSACTION_ID,
                "transaction_position": position,
                "transaction_size": 2,
                "transaction_hash": "0" * 64,
                "destination_streams": {
                    "webapp_ir": {
                        "sequence": position,
                        "transaction_id": _TRANSACTION_ID,
                        "transaction_position": position,
                        "transaction_size": 2,
                        "transaction_hash": "0" * 64,
                    }
                },
            }
        )
    group_hash = transaction_hash_from_envelopes(items)
    destination_hash = destination_transaction_hash(items, destination_site="webapp_ir")
    for item in items:
        item["transaction_hash"] = group_hash
        item["destination_streams"]["webapp_ir"]["transaction_hash"] = destination_hash
    return items


class DurabilityJournalTests(unittest.TestCase):
    def _prepare(self):
        events = _transaction()
        return build_prepare(
            envelopes=events,
            origin_physical_site="webapp_fi",
            writer_epoch=7,
            transaction_id=_TRANSACTION_ID,
            transaction_hash=events[0]["transaction_hash"],
            release_sha="e00283c037ec5ca63340b9827768256b1c5ef144",
            encryption_key_id="staging-fi-journal-v1",
            encryption_secret=_SECRET,
        )

    def test_private_webapp_payload_is_opaque_and_round_trips(self):
        prepare = self._prepare()
        public = prepare.as_payload()

        self.assertNotIn("normalized_mobile", str(public))
        self.assertNotIn("canonical_payload", public)
        self.assertEqual(parse_prepare(public), prepare)
        restored = decrypt_prepare(prepare, encryption_secret=_SECRET)
        self.assertEqual([item["event_id"] for item in restored], list(prepare.event_ids))
        self.assertEqual(restored[0]["canonical_payload"]["normalized_mobile"], "09120000001")

    def test_ciphertext_tamper_wrong_key_and_metadata_tamper_fail_closed(self):
        prepare = self._prepare()
        tampered_ciphertext = prepare.as_payload()
        tampered_ciphertext["ciphertext"] = tampered_ciphertext["ciphertext"][:-4] + "AAAA"
        with self.assertRaisesRegex(DurabilityJournalError, "hash mismatch"):
            parse_prepare(tampered_ciphertext)

        with self.assertRaisesRegex(DurabilityJournalError, "cannot be authenticated"):
            decrypt_prepare(prepare, encryption_secret="x" * 32)

        tampered_metadata = prepare.as_payload()
        tampered_metadata["event_ids"] = list(reversed(tampered_metadata["event_ids"]))
        parsed = parse_prepare(tampered_metadata)
        with self.assertRaisesRegex(DurabilityJournalError, "cannot be authenticated"):
            decrypt_prepare(parsed, encryption_secret=_SECRET)

    def test_rejects_non_fi_or_incomplete_transaction(self):
        events = _transaction()
        with self.assertRaisesRegex(DurabilityJournalError, "WebApp-FI"):
            build_prepare(
                envelopes=events,
                origin_physical_site="webapp_ir",
                writer_epoch=7,
                transaction_id=_TRANSACTION_ID,
                transaction_hash=events[0]["transaction_hash"],
                release_sha="e00283c037ec5ca63340b9827768256b1c5ef144",
                encryption_key_id="staging-fi-journal-v1",
                encryption_secret=_SECRET,
            )
        incomplete = copy.deepcopy(events[:1])
        with self.assertRaisesRegex(DurabilityJournalError, "size is inconsistent"):
            build_prepare(
                envelopes=incomplete,
                origin_physical_site="webapp_fi",
                writer_epoch=7,
                transaction_id=_TRANSACTION_ID,
                transaction_hash=events[0]["transaction_hash"],
                release_sha="e00283c037ec5ca63340b9827768256b1c5ef144",
                encryption_key_id="staging-fi-journal-v1",
                encryption_secret=_SECRET,
            )

    def test_acknowledgement_binds_exact_prepared_transaction(self):
        prepare = self._prepare()
        request_hash = "c" * 64
        unsigned = acknowledgement_payload(
            prepare=prepare,
            state="prepared",
            request_hash=request_hash,
            resolved_at=datetime(2026, 8, 3, 12, 1, tzinfo=timezone.utc),
        )
        acknowledgement = {
            **unsigned,
            "acknowledgement_mac": sign_acknowledgement(
                payload=unsigned,
                secret=_HMAC_SECRET,
            ),
        }
        self.assertEqual(
            verify_acknowledgement(
                acknowledgement,
                prepare=prepare,
                request_hash=request_hash,
                shared_secret=_HMAC_SECRET,
                expected_state="prepared",
            )["state"],
            "prepared",
        )

        modified = dict(acknowledgement)
        modified["writer_epoch"] = 8
        with self.assertRaisesRegex(DurabilityJournalError, "signature|does not bind"):
            verify_acknowledgement(
                modified,
                prepare=prepare,
                request_hash=request_hash,
                shared_secret=_HMAC_SECRET,
                expected_state="prepared",
            )

    def test_parse_rejects_duplicate_event_id_and_wrong_destination_ack(self):
        prepare = self._prepare()
        malformed = prepare.as_payload()
        malformed["event_ids"] = [malformed["event_ids"][0]] * 2
        with self.assertRaisesRegex(DurabilityJournalError, "identities are inconsistent"):
            parse_prepare(malformed)

        with self.assertRaisesRegex(DurabilityJournalError, "receiver site"):
            acknowledgement_payload(
                prepare=prepare,
                state="prepared",
                request_hash="d" * 64,
                receiver_site="webapp_ir",
            )
