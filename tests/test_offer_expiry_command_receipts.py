import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from core.offer_expiry_command import (
    build_republish_command_identity,
    offer_expiry_command_hash,
)
from core.services.offer_expiry_command_receipt_service import (
    OfferExpiryCommandReplayConflict,
    finalize_offer_expiry_command_receipt,
    prepare_offer_expiry_command_receipt,
)
from models.database import Base
from models.offer_expiry_command_receipt import OfferExpiryCommandReceipt
from api.routers.sync import _sync_item_authority_rejection_reason


class ScalarRows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)


class ExecuteResult:
    def __init__(self, values=()):
        self.values = list(values)

    def scalars(self):
        return ScalarRows(self.values)


class FakeDB:
    def __init__(self, execute_results):
        self.execute_results = list(execute_results)
        self.execute_calls = []
        self.added = []
        self.flush = AsyncMock()

    async def execute(self, statement, params=None):
        self.execute_calls.append((statement, params))
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, value):
        self.added.append(value)


class OfferExpiryCommandIdentityTests(unittest.TestCase):
    def test_republish_identity_is_stable_and_changes_with_request_identity(self):
        first = build_republish_command_identity(
            owner_user_id=7,
            source_offer_public_id="ofr_source_7",
            create_idempotency_key="offer:attempt-1",
        )
        replay = build_republish_command_identity(
            owner_user_id=7,
            source_offer_public_id="ofr_source_7",
            create_idempotency_key="offer:attempt-1",
        )
        changed = build_republish_command_identity(
            owner_user_id=7,
            source_offer_public_id="ofr_source_7",
            create_idempotency_key="offer:attempt-2",
        )

        self.assertEqual(first, replay)
        self.assertNotEqual(first.command_id, changed.command_id)
        self.assertTrue(first.replacement_offer_public_id.startswith("ofr_rp_"))
        self.assertLessEqual(len(first.replacement_offer_public_id), 40)

    def test_command_hash_ignores_peer_local_offer_id_but_detects_business_changes(self):
        payload = {
            "command_id": "56af09b7-52b6-4c9e-af7a-b4cda5de9b56",
            "idempotency_key": "offer-republish:stable",
            "offer_id": 11,
            "offer_public_id": "ofr_source_11",
            "owner_user_id": 7,
            "actor_user_id": 8,
            "source_surface": "webapp",
            "source_server": "iran",
            "expire_reason": "republished",
            "replacement_offer_public_id": "ofr_replacement_11",
        }
        peer_payload = {**payload, "offer_id": 901}
        changed_payload = {**payload, "replacement_offer_public_id": "ofr_replacement_12"}

        self.assertEqual(offer_expiry_command_hash(payload), offer_expiry_command_hash(peer_payload))
        self.assertNotEqual(offer_expiry_command_hash(payload), offer_expiry_command_hash(changed_payload))

    def test_sync_rejects_offer_updates_emitted_by_a_non_home_mirror(self):
        item = {
            "table": "offers",
            "sync_meta": {"source_server": "foreign"},
            "data": {"offer_public_id": "ofr_source_11", "home_server": "iran", "status": "active"},
        }
        self.assertEqual(
            _sync_item_authority_rejection_reason(item, "offers"),
            "source_authority_forbidden:foreign",
        )

        item["sync_meta"]["source_server"] = "iran"
        self.assertIsNone(_sync_item_authority_rejection_reason(item, "offers"))


class OfferExpiryCommandReceiptServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_and_finalize_new_receipt_without_committing(self):
        command_id = UUID("56af09b7-52b6-4c9e-af7a-b4cda5de9b56")
        db = FakeDB([ExecuteResult(), ExecuteResult(), ExecuteResult()])

        receipt, replayed = await prepare_offer_expiry_command_receipt(
            db,
            command_id=command_id,
            idempotency_key="offer-republish:stable",
            request_hash="a" * 64,
            offer_public_id="ofr_source_11",
            replacement_offer_public_id="ofr_replacement_11",
            source_server="iran",
            source_surface="webapp",
            expire_reason="republished",
        )

        self.assertFalse(replayed)
        self.assertEqual(db.added, [receipt])
        db.flush.assert_awaited_once()
        completed_at = datetime(2026, 7, 14, tzinfo=timezone.utc)
        finalize_offer_expiry_command_receipt(receipt, outcome_code="expired", completed_at=completed_at)
        self.assertEqual(receipt.outcome_code, "expired")
        self.assertEqual(receipt.completed_at, completed_at)

    async def test_replay_returns_terminal_receipt_and_changed_payload_is_rejected(self):
        command_id = UUID("56af09b7-52b6-4c9e-af7a-b4cda5de9b56")
        existing = SimpleNamespace(
            command_id=command_id,
            idempotency_key="offer-republish:stable",
            request_hash="a" * 64,
            outcome_code="expired",
        )
        db = FakeDB([ExecuteResult(), ExecuteResult(), ExecuteResult([existing])])

        receipt, replayed = await prepare_offer_expiry_command_receipt(
            db,
            command_id=command_id,
            idempotency_key="offer-republish:stable",
            request_hash="a" * 64,
            offer_public_id="ofr_source_11",
            replacement_offer_public_id="ofr_replacement_11",
            source_server="iran",
            source_surface="webapp",
            expire_reason="republished",
        )
        self.assertTrue(replayed)
        self.assertIs(receipt, existing)
        self.assertEqual(db.added, [])

        changed_db = FakeDB([ExecuteResult(), ExecuteResult(), ExecuteResult([existing])])
        with self.assertRaisesRegex(OfferExpiryCommandReplayConflict, "changed_payload_replay"):
            await prepare_offer_expiry_command_receipt(
                changed_db,
                command_id=command_id,
                idempotency_key="offer-republish:stable",
                request_hash="b" * 64,
                offer_public_id="ofr_source_11",
                replacement_offer_public_id="ofr_replacement_12",
                source_server="iran",
                source_surface="webapp",
                expire_reason="republished",
            )

    def test_receipt_model_and_registry_metadata_are_additive(self):
        table = Base.metadata.tables["offer_expiry_command_receipts"]
        self.assertIs(OfferExpiryCommandReceipt.__table__, table)
        self.assertIn("command_id", table.c)
        self.assertIn("request_hash", table.c)
        self.assertIn("replacement_offer_public_id", table.c)
        self.assertIn("republished_offer_public_id", Base.metadata.tables["offers"].c)


if __name__ == "__main__":
    unittest.main()
