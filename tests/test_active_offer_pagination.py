import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from api.routers import offers as offers_module
from api.routers import sync as sync_module
from api.routers.realtime import REALTIME_SOURCE_SYNC_APPLY
from models.offer import Offer, OfferStatus


def compile_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class FakeExecuteResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class CapturingDB:
    def __init__(self, values):
        self.values = list(values)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeExecuteResult(self.values)


def response_for(offer_id: int) -> offers_module.OfferResponse:
    return offers_module.OfferResponse(
        id=offer_id,
        offer_public_id=f"ofr_stage12_{offer_id:020d}",
        public_link=f"https://example.test/offers/{offer_id}",
        user_id=11,
        user_account_name="stage12",
        is_own_offer=True,
        offer_type="buy",
        settlement_type="cash",
        commodity_id=7,
        commodity_name="stage12 commodity",
        quantity=10,
        remaining_quantity=10,
        price=100_000,
        raw_price=100_000,
        market_published_price=100_000,
        viewer_effective_price=100_000,
        is_wholesale=True,
        lot_sizes=None,
        original_lot_sizes=None,
        notes=None,
        status="active",
        channel_message_id=None,
        created_at="1405/01/01 12:00",
    )


class ActiveOfferCursorTests(unittest.TestCase):
    def test_cursor_round_trip_is_bound_to_the_filter_set(self):
        signature = offers_module._active_offer_filter_signature(
            offer_type="buy",
            settlement_type="cash",
            commodity_id=7,
            own_only=True,
        )
        position = SimpleNamespace(
            id=51,
            created_at=datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc),
        )
        cursor = offers_module._encode_active_offer_cursor(
            position,
            filter_signature=signature,
        )

        self.assertEqual(
            offers_module._decode_active_offer_cursor(
                cursor,
                expected_filter_signature=signature,
            ),
            (position.created_at, position.id),
        )
        with self.assertRaises(HTTPException) as mismatch:
            offers_module._decode_active_offer_cursor(
                cursor,
                expected_filter_signature={**signature, "settlement_type": "tomorrow"},
            )
        self.assertEqual(mismatch.exception.status_code, 400)

    def test_malformed_cursor_is_rejected(self):
        signature = offers_module._active_offer_filter_signature(
            offer_type=None,
            settlement_type=None,
            commodity_id=None,
            own_only=False,
        )
        with self.assertRaises(HTTPException) as raised:
            offers_module._decode_active_offer_cursor(
                "not-valid-base64!",
                expected_filter_signature=signature,
            )
        self.assertEqual(raised.exception.status_code, 400)

    def test_query_applies_filters_stable_keyset_order_and_limit_plus_one(self):
        cursor_time = datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc)
        statement = offers_module._build_active_offer_page_query(
            owner_user_id=11,
            offer_type="buy",
            settlement_type="tomorrow",
            commodity_id=7,
            own_only=True,
            cursor_position=(cursor_time, 51),
            limit=50,
        )
        sql = compile_sql(statement)

        self.assertIn("offers.status = 'ACTIVE'", sql)
        self.assertIn("offers.offer_type = 'BUY'", sql)
        self.assertIn("offers.settlement_type = 'TOMORROW'", sql)
        self.assertIn("offers.commodity_id = 7", sql)
        self.assertIn("offers.user_id = 11", sql)
        self.assertIn("offers.created_at <", sql)
        self.assertIn("offers.id < 51", sql)
        self.assertIn("ORDER BY offers.created_at DESC, offers.id DESC", sql)
        self.assertIn("LIMIT 51", sql)

    def test_model_declares_partial_active_cursor_index(self):
        index = next(
            item for item in Offer.__table__.indexes
            if item.name == "ix_offers_active_created_id"
        )
        self.assertEqual(
            [expression.element.name for expression in index.expressions],
            ["created_at", "id"],
        )
        predicate = compile_sql(index.dialect_options["postgresql"]["where"])
        self.assertIn("offers.status", predicate)
        self.assertIn("ACTIVE", predicate)


class ActiveOfferPageEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_page_has_no_cursor(self):
        db = CapturingDB([])
        user = SimpleNamespace(id=11)
        context = SimpleNamespace(
            owner_user=user,
            actor_user=user,
            is_accountant_context=False,
        )

        response = await offers_module.get_active_offer_page(
            offer_type=None,
            settlement_type=None,
            commodity_id=None,
            own_only=False,
            cursor=None,
            limit=50,
            db=db,
            current_user=user,
            context=context,
        )

        self.assertEqual(response.items, [])
        self.assertFalse(response.has_more)
        self.assertIsNone(response.next_cursor)
        self.assertEqual(response.page_size, 0)

    async def test_page_uses_extra_row_only_for_has_more_and_cursor(self):
        created_at = datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc)
        rows = [
            SimpleNamespace(id=52, created_at=created_at),
            SimpleNamespace(id=51, created_at=created_at),
        ]
        db = CapturingDB(rows)
        user = SimpleNamespace(id=11)
        context = SimpleNamespace(
            owner_user=user,
            actor_user=user,
            is_accountant_context=False,
        )

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace()),
        ), patch.object(
            offers_module,
            "_serialize_offer_responses",
            new=AsyncMock(return_value=[response_for(52)]),
        ):
            response = await offers_module.get_active_offer_page(
                offer_type="buy",
                settlement_type="cash",
                commodity_id=7,
                own_only=True,
                cursor=None,
                limit=1,
                db=db,
                current_user=user,
                context=context,
            )

        self.assertEqual([item.id for item in response.items], [52])
        self.assertTrue(response.has_more)
        self.assertEqual(response.page_size, 1)
        self.assertIsNotNone(response.next_cursor)
        signature = offers_module._active_offer_filter_signature(
            offer_type="buy",
            settlement_type="cash",
            commodity_id=7,
            own_only=True,
        )
        self.assertEqual(
            offers_module._decode_active_offer_cursor(
                response.next_cursor,
                expected_filter_signature=signature,
            ),
            (created_at, 52),
        )
        self.assertIn("LIMIT 2", compile_sql(db.statements[0]))


class ActiveOfferRealtimeIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_synced_terminal_event_carries_public_identity_when_available(self):
        offer = SimpleNamespace(
            id=72,
            offer_public_id="ofr_stage12_shared",
            status=OfferStatus.EXPIRED,
            remaining_quantity=0,
            lot_sizes=None,
        )
        db = CapturingDB([offer])
        publish = AsyncMock()

        with patch("api.routers.realtime.publish_event", new=publish):
            await sync_module._publish_terminal_offer_realtime_after_sync(db, [72])

        publish.assert_awaited_once_with(
            "offer:expired",
            {"id": 72, "offer_public_id": "ofr_stage12_shared"},
            source=REALTIME_SOURCE_SYNC_APPLY,
        )


if __name__ == "__main__":
    unittest.main()
