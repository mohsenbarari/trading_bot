import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from core.offer_expiry_contracts import (
    OfferExpiryCommandIdentityError,
    build_offer_expiry_command_identity,
    build_offer_expiry_forward_payload,
    validate_offer_expiry_command_identity,
)
from core.services.offer_expiry_command_receipt_service import (
    OFFER_EXPIRY_COMMAND_RECEIPT_RETENTION_DAYS,
    OfferExpiryCommandReceiptIncomplete,
    OfferExpiryCommandReplayConflict,
    OfferExpiryReceiptOutcome,
    finalize_offer_expiry_command_receipt,
    offer_expiry_side_effect_dedupe_key,
    prepare_offer_expiry_command_receipt,
    replay_offer_expiry_receipt_outcome,
    terminal_offer_expiry_receipt_cleanup_statement,
)
from models.offer_expiry_command_receipt import OfferExpiryCommandReceipt


def command_kwargs(**overrides):
    values = {
        "offer_public_id": "ofr_stage11_contract_123456",
        "owner_user_id": 17,
        "actor_user_id": 17,
        "source_surface": "webapp",
        "source_server": "iran",
        "expire_reason": "manual",
    }
    values.update(overrides)
    return values


class OfferExpiryCommandContractTests(unittest.TestCase):
    def test_identity_is_stable_when_numeric_offer_ids_differ(self):
        first = build_offer_expiry_forward_payload(
            SimpleNamespace(id=11, offer_public_id="ofr_stage11_contract_123456"),
            owner_user_id=17,
            actor_user_id=17,
            source_surface="webapp",
            source_server="iran",
            expire_reason="manual",
            include_command_identity=True,
        )
        second = build_offer_expiry_forward_payload(
            SimpleNamespace(id=999, offer_public_id="ofr_stage11_contract_123456"),
            owner_user_id=17,
            actor_user_id=17,
            source_surface="webapp",
            source_server="iran",
            expire_reason="manual",
            include_command_identity=True,
        )

        self.assertNotEqual(first["offer_id"], second["offer_id"])
        self.assertEqual(first["command_id"], second["command_id"])
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])

    def test_identity_changes_with_business_payload_and_rejects_forgery(self):
        original = build_offer_expiry_command_identity(**command_kwargs())
        changed_offer = build_offer_expiry_command_identity(
            **command_kwargs(offer_public_id="ofr_stage11_contract_654321")
        )
        changed_reason = build_offer_expiry_command_identity(
            **command_kwargs(expire_reason="cancel_all")
        )

        self.assertNotEqual(original.command_id, changed_offer.command_id)
        self.assertNotEqual(original.command_id, changed_reason.command_id)
        with self.assertRaises(OfferExpiryCommandIdentityError):
            validate_offer_expiry_command_identity(
                command_id=uuid4(),
                idempotency_key=original.idempotency_key,
                **command_kwargs(),
            )

    def test_legacy_payload_remains_additive_and_has_no_receipt_identity(self):
        payload = build_offer_expiry_forward_payload(
            SimpleNamespace(id=11, offer_public_id=None),
            owner_user_id=17,
            actor_user_id=None,
            source_surface="webapp",
            source_server="iran",
            expire_reason="manual",
            include_command_identity=False,
        )

        self.assertNotIn("command_id", payload)
        self.assertNotIn("idempotency_key", payload)
        self.assertIsNone(payload["offer_public_id"])

    def test_new_payload_fails_closed_without_public_identity(self):
        with self.assertRaises(OfferExpiryCommandIdentityError):
            build_offer_expiry_forward_payload(
                SimpleNamespace(id=11, offer_public_id=None),
                owner_user_id=17,
                actor_user_id=17,
                source_surface="webapp",
                source_server="iran",
                expire_reason="manual",
                include_command_identity=True,
            )


class FakeReceiptDB:
    def __init__(self):
        self.added = []
        self.flush = AsyncMock()

    def add(self, value):
        self.added.append(value)


class OfferExpiryCommandReceiptServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_creates_pending_receipt_and_finalize_is_atomic_shape(self):
        identity = build_offer_expiry_command_identity(**command_kwargs())
        db = FakeReceiptDB()
        with patch(
            "core.services.offer_expiry_command_receipt_service.acquire_offer_expiry_command_locks",
            new=AsyncMock(),
        ), patch(
            "core.services.offer_expiry_command_receipt_service.load_offer_expiry_command_receipt",
            new=AsyncMock(return_value=None),
        ):
            receipt, replayed = await prepare_offer_expiry_command_receipt(
                db,
                command_id=identity.command_id,
                idempotency_key=identity.idempotency_key,
                request_hash=identity.request_hash,
                offer_public_id=command_kwargs()["offer_public_id"],
                source_server="iran",
                source_surface="webapp",
                expire_reason="manual",
            )

        self.assertFalse(replayed)
        self.assertEqual(db.added, [receipt])
        self.assertIsNone(receipt.outcome_code)
        self.assertIsNone(receipt.completed_at)
        finalize_offer_expiry_command_receipt(
            receipt,
            outcome=OfferExpiryReceiptOutcome.EXPIRED,
        )
        self.assertEqual(replay_offer_expiry_receipt_outcome(receipt), OfferExpiryReceiptOutcome.EXPIRED)

    async def test_prepare_replays_only_matching_terminal_receipt(self):
        identity = build_offer_expiry_command_identity(**command_kwargs())
        receipt = OfferExpiryCommandReceipt(
            command_id=identity.command_id,
            idempotency_key=identity.idempotency_key,
            request_hash=identity.request_hash,
            offer_public_id=command_kwargs()["offer_public_id"],
            source_server="iran",
            source_surface="webapp",
            expire_reason="manual",
            outcome_code="expired",
            completed_at=datetime.now(timezone.utc),
        )
        db = FakeReceiptDB()
        with patch(
            "core.services.offer_expiry_command_receipt_service.acquire_offer_expiry_command_locks",
            new=AsyncMock(),
        ), patch(
            "core.services.offer_expiry_command_receipt_service.load_offer_expiry_command_receipt",
            new=AsyncMock(return_value=receipt),
        ):
            loaded, replayed = await prepare_offer_expiry_command_receipt(
                db,
                command_id=identity.command_id,
                idempotency_key=identity.idempotency_key,
                request_hash=identity.request_hash,
                offer_public_id=command_kwargs()["offer_public_id"],
                source_server="iran",
                source_surface="webapp",
                expire_reason="manual",
            )

        self.assertIs(loaded, receipt)
        self.assertTrue(replayed)
        self.assertEqual(db.added, [])

    async def test_prepare_rejects_changed_payload_and_incomplete_receipt(self):
        identity = build_offer_expiry_command_identity(**command_kwargs())
        receipt = OfferExpiryCommandReceipt(
            command_id=identity.command_id,
            idempotency_key=identity.idempotency_key,
            request_hash="0" * 64,
            offer_public_id=command_kwargs()["offer_public_id"],
            source_server="iran",
            source_surface="webapp",
            expire_reason="manual",
        )
        db = FakeReceiptDB()
        patches = (
            patch(
                "core.services.offer_expiry_command_receipt_service.acquire_offer_expiry_command_locks",
                new=AsyncMock(),
            ),
            patch(
                "core.services.offer_expiry_command_receipt_service.load_offer_expiry_command_receipt",
                new=AsyncMock(return_value=receipt),
            ),
        )
        with patches[0], patches[1]:
            with self.assertRaises(OfferExpiryCommandReplayConflict):
                await prepare_offer_expiry_command_receipt(
                    db,
                    command_id=identity.command_id,
                    idempotency_key=identity.idempotency_key,
                    request_hash=identity.request_hash,
                    offer_public_id=command_kwargs()["offer_public_id"],
                    source_server="iran",
                    source_surface="webapp",
                    expire_reason="manual",
                )

        receipt.request_hash = identity.request_hash
        with patch(
            "core.services.offer_expiry_command_receipt_service.acquire_offer_expiry_command_locks",
            new=AsyncMock(),
        ), patch(
            "core.services.offer_expiry_command_receipt_service.load_offer_expiry_command_receipt",
            new=AsyncMock(return_value=receipt),
        ):
            with self.assertRaises(OfferExpiryCommandReceiptIncomplete):
                await prepare_offer_expiry_command_receipt(
                    db,
                    command_id=identity.command_id,
                    idempotency_key=identity.idempotency_key,
                    request_hash=identity.request_hash,
                    offer_public_id=command_kwargs()["offer_public_id"],
                    source_server="iran",
                    source_surface="webapp",
                    expire_reason="manual",
                )

    def test_retention_deletes_only_old_terminal_receipts(self):
        statement = terminal_offer_expiry_receipt_cleanup_statement(
            current_time=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )
        sql = str(statement)

        self.assertEqual(OFFER_EXPIRY_COMMAND_RECEIPT_RETENTION_DAYS, 365)
        self.assertIn("completed_at IS NOT NULL", sql)
        self.assertIn("completed_at <", sql)

    def test_side_effect_dedupe_key_uses_command_public_identity_and_version(self):
        command_id = uuid4()
        key = offer_expiry_side_effect_dedupe_key(
            command_id=command_id,
            offer_public_id="ofr_stage11_contract_123456",
            offer_version=3,
        )

        self.assertIn(str(command_id), key)
        self.assertIn("ofr_stage11_contract_123456", key)
        self.assertTrue(key.endswith(":v3"))


if __name__ == "__main__":
    unittest.main()
