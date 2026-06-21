import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from api.routers.trades import (
    TRADE_NUMBER_ALLOCATION_LOCK_ID,
    TradeAtomicityError,
    TradeIdempotencyConflictError,
    _allocate_next_trade_number,
    _apply_offer_trade_mutation,
    _commit_trade_execution,
    _lock_trade_idempotency_key,
    _try_lock_trade_offer_execution,
    _validate_idempotent_trade_replay,
)
from core.services.chat_room_service import MANDATORY_CHANNEL_LOCK_KEY
from models.offer import OfferStatus


class FakeBind:
    def __init__(self, dialect_name: str):
        self.dialect = SimpleNamespace(name=dialect_name)


class FakeDB:
    def __init__(self, *, dialect_name: str | None = None, scalar_result=None, commit_side_effect=None):
        self._bind = FakeBind(dialect_name) if dialect_name else None
        self.execute = AsyncMock()
        self.scalar = AsyncMock(return_value=scalar_result)
        self.commit = AsyncMock(side_effect=commit_side_effect)
        self.rollback = AsyncMock()

    def get_bind(self):
        return self._bind


def make_offer(**overrides):
    data = {
        "id": 7,
        "user_id": 9,
        "commodity_id": 1,
        "remaining_quantity": 10,
        "lot_sizes": None,
        "is_wholesale": True,
        "status": OfferStatus.ACTIVE,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_user(user_id: int):
    return SimpleNamespace(id=user_id)


class TradeAtomicityHardeningTests(unittest.IsolatedAsyncioTestCase):
    def test_trade_number_lock_does_not_share_mandatory_channel_lock_key(self):
        self.assertNotEqual(TRADE_NUMBER_ALLOCATION_LOCK_ID, MANDATORY_CHANNEL_LOCK_KEY)

    async def test_allocate_next_trade_number_takes_postgresql_advisory_lock(self):
        db = FakeDB(dialect_name="postgresql", scalar_result=10010)

        next_trade_number = await _allocate_next_trade_number(db)

        self.assertEqual(next_trade_number, 10011)
        db.execute.assert_awaited_once()
        db.scalar.assert_awaited_once()

    async def test_allocate_next_trade_number_skips_advisory_lock_outside_postgresql(self):
        db = FakeDB(dialect_name="sqlite", scalar_result=None)

        next_trade_number = await _allocate_next_trade_number(db)

        self.assertEqual(next_trade_number, 10000)
        db.execute.assert_not_awaited()
        db.scalar.assert_awaited_once()

    async def test_idempotency_key_lock_is_postgresql_only(self):
        postgres_db = FakeDB(dialect_name="postgresql")
        sqlite_db = FakeDB(dialect_name="sqlite")

        self.assertTrue(await _lock_trade_idempotency_key(postgres_db, "idem-1"))
        self.assertFalse(await _lock_trade_idempotency_key(sqlite_db, "idem-1"))
        self.assertFalse(await _lock_trade_idempotency_key(postgres_db, None))

        postgres_db.execute.assert_awaited_once()
        sqlite_db.execute.assert_not_awaited()

    async def test_offer_execution_lock_uses_postgresql_try_advisory_lock(self):
        postgres_busy_db = FakeDB(dialect_name="postgresql", scalar_result=False)
        postgres_free_db = FakeDB(dialect_name="postgresql", scalar_result=True)
        sqlite_db = FakeDB(dialect_name="sqlite")

        self.assertFalse(await _try_lock_trade_offer_execution(postgres_busy_db, 7))
        self.assertTrue(await _try_lock_trade_offer_execution(postgres_free_db, 7))
        self.assertTrue(await _try_lock_trade_offer_execution(sqlite_db, 7))

        postgres_busy_db.scalar.assert_awaited_once()
        postgres_free_db.scalar.assert_awaited_once()
        sqlite_db.scalar.assert_not_awaited()

    def test_idempotent_replay_rejects_mismatched_economic_request(self):
        existing_trade = SimpleNamespace(
            offer_id=7,
            offer_user_id=9,
            responder_user_id=5,
            actor_user_id=5,
            commodity_id=1,
            quantity=4,
            price=100_000,
        )

        with self.assertRaises(TradeIdempotencyConflictError):
            _validate_idempotent_trade_replay(
                existing_trade=existing_trade,
                offer=make_offer(id=8),
                owner_user=make_user(5),
                actor_user=make_user(5),
                trade_quantity=4,
                expected_price=100_000,
                uses_customer_trade_chain=False,
            )

        with self.assertRaises(TradeIdempotencyConflictError):
            _validate_idempotent_trade_replay(
                existing_trade=existing_trade,
                offer=make_offer(id=7),
                owner_user=make_user(5),
                actor_user=make_user(5),
                trade_quantity=5,
                expected_price=100_000,
                uses_customer_trade_chain=False,
            )

    def test_idempotent_replay_allows_matching_chain_final_leg(self):
        existing_trade = SimpleNamespace(
            offer_id=None,
            offer_user_id=77,
            responder_user_id=52,
            actor_user_id=52,
            commodity_id=1,
            quantity=4,
            price=100_700,
        )

        _validate_idempotent_trade_replay(
            existing_trade=existing_trade,
            offer=make_offer(id=7, user_id=9),
            owner_user=make_user(52),
            actor_user=make_user(52),
            trade_quantity=4,
            expected_price=100_700,
            uses_customer_trade_chain=True,
        )

    def test_offer_trade_mutation_guards_negative_remaining_and_missing_retail_lot(self):
        with self.assertRaises(TradeAtomicityError):
            _apply_offer_trade_mutation(make_offer(remaining_quantity=3), 4)

        with self.assertRaises(TradeAtomicityError):
            _apply_offer_trade_mutation(
                make_offer(remaining_quantity=10, is_wholesale=False, lot_sizes=[6, 4]),
                5,
            )

    def test_offer_trade_mutation_updates_lots_and_completion_state(self):
        partial_offer = make_offer(remaining_quantity=10, is_wholesale=False, lot_sizes=[6, 4])

        self.assertTrue(_apply_offer_trade_mutation(partial_offer, 4))
        self.assertEqual(partial_offer.remaining_quantity, 6)
        self.assertEqual(partial_offer.lot_sizes, [6])
        self.assertEqual(partial_offer.status, OfferStatus.ACTIVE)

        final_offer = make_offer(remaining_quantity=4, is_wholesale=False, lot_sizes=[4])
        self.assertTrue(_apply_offer_trade_mutation(final_offer, 4))
        self.assertEqual(final_offer.remaining_quantity, 0)
        self.assertIsNone(final_offer.lot_sizes)
        self.assertEqual(final_offer.status, OfferStatus.COMPLETED)

    async def test_commit_rolls_back_and_maps_stale_or_unique_conflicts(self):
        stale_db = FakeDB(commit_side_effect=RuntimeError("StaleDataError could not update row"))
        with self.assertRaises(HTTPException) as stale_exc:
            await _commit_trade_execution(stale_db)
        self.assertEqual(stale_exc.exception.status_code, 409)
        stale_db.rollback.assert_awaited_once()

        unique_db = FakeDB(commit_side_effect=RuntimeError("duplicate key value violates unique constraint"))
        with self.assertRaises(HTTPException) as unique_exc:
            await _commit_trade_execution(unique_db)
        self.assertEqual(unique_exc.exception.status_code, 409)
        unique_db.rollback.assert_awaited_once()

    async def test_commit_rolls_back_and_reraises_unknown_errors(self):
        db = FakeDB(commit_side_effect=RuntimeError("database unavailable"))

        with self.assertRaises(RuntimeError):
            await _commit_trade_execution(db)

        db.rollback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
