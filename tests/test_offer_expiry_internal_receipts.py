import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import InternalOfferExpireRequest, expire_offer_internal
from core.offer_expiry_contracts import build_offer_expiry_command_identity
from core.services.offer_expiry_command_receipt_service import (
    OfferExpiryCommandReplayConflict,
)
from models.offer import OfferStatus


PUBLIC_ID = "ofr_stage11_internal_123456"


class SingleScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, offer=None):
        self.offer = offer
        self.execute = AsyncMock(side_effect=self._execute)
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def _execute(self, _stmt):
        return SingleScalarResult(self.offer)


def make_payload(
    *,
    offer_id=999,
    offer_public_id=PUBLIC_ID,
    owner_user_id=5,
    actor_user_id=8,
    source_surface="webapp",
    source_server="iran",
    expire_reason="cancel_all",
):
    identity = build_offer_expiry_command_identity(
        offer_public_id=offer_public_id,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        source_surface=source_surface,
        source_server=source_server,
        expire_reason=expire_reason,
    )
    return InternalOfferExpireRequest(
        offer_id=offer_id,
        offer_public_id=offer_public_id,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        source_surface=source_surface,
        source_server=source_server,
        expire_reason=expire_reason,
        command_id=identity.command_id,
        idempotency_key=identity.idempotency_key,
    )


def make_receipt(payload):
    identity = build_offer_expiry_command_identity(
        offer_public_id=payload.offer_public_id,
        owner_user_id=payload.owner_user_id,
        actor_user_id=payload.actor_user_id,
        source_surface=payload.source_surface,
        source_server=payload.source_server,
        expire_reason=payload.expire_reason,
    )
    return SimpleNamespace(
        command_id=identity.command_id,
        idempotency_key=identity.idempotency_key,
        request_hash=identity.request_hash,
        offer_public_id=payload.offer_public_id,
        source_server=payload.source_server,
        source_surface=payload.source_surface,
        expire_reason=payload.expire_reason,
        outcome_code=None,
        completed_at=None,
    )


def make_request():
    return SimpleNamespace(
        body=AsyncMock(return_value=b"{}"),
        headers={
            "x-source-server": "iran",
            "x-timestamp": "123",
            "x-signature": "sig",
            "x-api-key": "key",
        },
    )


class OfferExpiryInternalReceiptTests(unittest.IsolatedAsyncioTestCase):
    def make_offer(self, *, status=OfferStatus.ACTIVE):
        return SimpleNamespace(
            id=21,
            user_id=5,
            status=status,
            home_server="foreign",
            offer_public_id=PUBLIC_ID,
            channel_message_id=None,
            version_id=1,
        )

    async def call(self, payload, db, **patches):
        defaults = {
            "prepare": AsyncMock(),
            "side_effects": AsyncMock(),
        }
        defaults.update(patches)
        with patch("api.routers.offers.verify_internal_signature", return_value=True), patch(
            "api.routers.offers.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="foreign",
        ), patch(
            "api.routers.offers.prepare_offer_expiry_command_receipt",
            new=defaults["prepare"],
        ), patch(
            "api.routers.offers._expire_offer_side_effects",
            new=defaults["side_effects"],
        ), patch(
            "api.routers.offers.enforce_manual_offer_expire_limits",
            new=defaults.get("limits", AsyncMock()),
        ):
            return await expire_offer_internal(payload, make_request(), db=db)

    async def test_signature_and_source_checks_run_before_receipt_or_mutation(self):
        payload = make_payload()
        db = FakeDB(self.make_offer())
        prepare = AsyncMock()
        with patch("api.routers.offers.verify_internal_signature", return_value=False), patch(
            "api.routers.offers.current_server",
            return_value="foreign",
        ), patch(
            "api.routers.offers.prepare_offer_expiry_command_receipt",
            new=prepare,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await expire_offer_internal(payload, make_request(), db=db)
        self.assertEqual(exc_info.exception.status_code, 401)
        prepare.assert_not_awaited()
        db.execute.assert_not_awaited()

        mismatched_request = make_request()
        mismatched_request.headers["x-source-server"] = "foreign"
        with patch("api.routers.offers.verify_internal_signature", return_value=True), patch(
            "api.routers.offers.current_server",
            return_value="foreign",
        ), patch(
            "api.routers.offers.prepare_offer_expiry_command_receipt",
            new=prepare,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await expire_offer_internal(payload, mismatched_request, db=db)
        self.assertEqual(exc_info.exception.status_code, 401)
        prepare.assert_not_awaited()
        db.execute.assert_not_awaited()

    async def test_commit_lost_response_and_replay_return_same_terminal_success_once(self):
        payload = make_payload()
        receipt = make_receipt(payload)
        prepare = AsyncMock(side_effect=[(receipt, False), (receipt, True)])
        side_effects = AsyncMock()
        db = FakeDB(self.make_offer())

        first = await self.call(
            payload,
            db,
            prepare=prepare,
            side_effects=side_effects,
        )
        replay = await self.call(
            payload,
            db,
            prepare=prepare,
            side_effects=side_effects,
        )

        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["command_id"], replay["command_id"])
        self.assertEqual(first["outcome"], "expired")
        self.assertEqual(db.execute.await_count, 1)
        self.assertEqual(db.commit.await_count, 2)
        self.assertEqual(side_effects.await_count, 1)

    async def test_home_resolves_public_identity_not_remote_numeric_id(self):
        payload = make_payload(offer_id=987654)
        receipt = make_receipt(payload)
        offer = self.make_offer()
        db = FakeDB(offer)

        result = await self.call(
            payload,
            db,
            prepare=AsyncMock(return_value=(receipt, False)),
        )

        self.assertTrue(result["expired"])
        self.assertEqual(result["offer_public_id"], PUBLIC_ID)
        self.assertEqual(offer.id, 21)
        self.assertEqual(offer.status, OfferStatus.EXPIRED)

    async def test_same_key_with_changed_payload_is_rejected_before_mutation(self):
        original = make_payload()
        changed = original.model_copy(update={"offer_public_id": "ofr_stage11_changed_654321"})
        prepare = AsyncMock()
        db = FakeDB(self.make_offer())

        with self.assertRaises(HTTPException) as exc_info:
            await self.call(changed, db, prepare=prepare)

        self.assertEqual(exc_info.exception.status_code, 409)
        prepare.assert_not_awaited()
        db.execute.assert_not_awaited()
        db.rollback.assert_awaited_once()

    async def test_receipt_collision_is_rejected_and_rolled_back(self):
        payload = make_payload()
        db = FakeDB(self.make_offer())
        with self.assertRaises(HTTPException) as exc_info:
            await self.call(
                payload,
                db,
                prepare=AsyncMock(
                    side_effect=OfferExpiryCommandReplayConflict("changed_payload_replay")
                ),
            )

        self.assertEqual(exc_info.exception.status_code, 409)
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()

    async def test_independent_command_on_inactive_offer_keeps_current_rejection(self):
        payload = make_payload(expire_reason="manual")
        receipt = make_receipt(payload)
        side_effects = AsyncMock()
        db = FakeDB(self.make_offer(status=OfferStatus.EXPIRED))

        with self.assertRaises(HTTPException) as exc_info:
            await self.call(
                payload,
                db,
                prepare=AsyncMock(return_value=(receipt, False)),
                side_effects=side_effects,
            )

        self.assertEqual(exc_info.exception.status_code, 400)
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()
        side_effects.assert_not_awaited()

    async def test_replay_bypasses_mutable_manual_limits(self):
        payload = make_payload(expire_reason="manual")
        receipt = make_receipt(payload)
        receipt.outcome_code = "expired"
        receipt.completed_at = datetime.now(timezone.utc)
        limits = AsyncMock(side_effect=RuntimeError("must not run"))
        db = FakeDB(None)

        result = await self.call(
            payload,
            db,
            prepare=AsyncMock(return_value=(receipt, True)),
            limits=limits,
        )

        self.assertTrue(result["replayed"])
        limits.assert_not_awaited()
        db.execute.assert_not_awaited()

    async def test_commit_failure_rolls_back_and_never_dispatches_side_effect(self):
        payload = make_payload()
        receipt = make_receipt(payload)
        side_effects = AsyncMock()
        db = FakeDB(self.make_offer())
        db.commit.side_effect = RuntimeError("commit failed")

        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            await self.call(
                payload,
                db,
                prepare=AsyncMock(return_value=(receipt, False)),
                side_effects=side_effects,
            )

        db.rollback.assert_awaited_once()
        side_effects.assert_not_awaited()

    async def test_side_effect_failure_occurs_after_commit_and_replay_does_not_repeat_it(self):
        payload = make_payload()
        receipt = make_receipt(payload)
        prepare = AsyncMock(side_effect=[(receipt, False), (receipt, True)])
        failing_side_effect = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        db = FakeDB(self.make_offer())

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await self.call(
                payload,
                db,
                prepare=prepare,
                side_effects=failing_side_effect,
            )
        self.assertEqual(db.commit.await_count, 1)

        replay_side_effect = AsyncMock()
        result = await self.call(
            payload,
            db,
            prepare=prepare,
            side_effects=replay_side_effect,
        )
        self.assertTrue(result["replayed"])
        replay_side_effect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
