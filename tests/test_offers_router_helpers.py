import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from api.routers import offers as offers_module
from core.telegram_gateway import TelegramGatewayResult
from models.offer import OfferStatus, OfferType
from models.trade import Trade


def compile_sql(statement):
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class FakeExecuteResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class FakeRowExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class CapturingDB:
    def __init__(self, result):
        self.result = result
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.result


class FakeHttpResponse:
    def __init__(self, status_code=200, text="ok", message_id=None):
        self.status_code = status_code
        self.text = text
        self._message_id = message_id

    def json(self):
        return {"result": {"message_id": self._message_id}}


class FakeAsyncClient:
    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.post = AsyncMock(side_effect=self._post)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _post(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.response


class BrokenCreatedAt:
    def timestamp(self):
        raise RuntimeError("boom")


def make_offer_model(**overrides):
    data = {
        "id": 10,
        "offer_public_id": "ofr_offer_10",
        "user_id": 5,
        "user": SimpleNamespace(account_name="owner"),
        "offer_type": OfferType.BUY,
        "commodity_id": 2,
        "commodity": SimpleNamespace(name="Gold"),
        "quantity": 12,
        "remaining_quantity": None,
        "price": 123456,
        "is_wholesale": True,
        "lot_sizes": None,
        "original_lot_sizes": None,
        "notes": "urgent",
        "status": OfferStatus.ACTIVE,
        "channel_message_id": 333,
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_offer_response(**overrides):
    data = {
        "id": 10,
        "offer_public_id": "ofr_offer_10",
        "public_link": "/market?offer=ofr_offer_10",
        "offer_type": "buy",
        "commodity_id": 2,
        "commodity_name": "Gold",
        "quantity": 12,
        "remaining_quantity": 12,
        "price": 123456,
        "raw_price": 123456,
        "market_published_price": 123456,
        "viewer_effective_price": 123456,
        "is_wholesale": True,
        "lot_sizes": None,
        "original_lot_sizes": None,
        "notes": None,
        "status": "active",
        "channel_message_id": None,
        "created_at": "1404/10/11 - 12:00",
    }
    data.update(overrides)
    return offers_module.OfferResponse(**data)


class OffersRouterHelperTests(unittest.IsolatedAsyncioTestCase):
    def test_trade_model_declares_completed_offer_history_index(self):
        indexes = {index.name: index for index in Trade.__table__.indexes}
        self.assertIn("ix_trades_completed_offer_history", indexes)
        history_index = indexes["ix_trades_completed_offer_history"]
        self.assertEqual([column.name for column in history_index.columns], ["offer_id", "created_at"])
        where = str(
            history_index.dialect_options["postgresql"]["where"].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("trades.status = 'COMPLETED'", where)
        self.assertIn("trades.offer_id IS NOT NULL", where)

    async def test_offer_to_response_handles_identity_and_expiry_paths(self):
        offer = make_offer_model()

        response = offers_module.offer_to_response(
            offer,
            SimpleNamespace(offer_expiry_minutes=15),
            viewer_user_id=5,
            include_owner_identity=True,
        )

        self.assertEqual(response.user_id, 5)
        self.assertEqual(response.offer_public_id, "ofr_offer_10")
        self.assertTrue(response.public_link.endswith("/market?offer=ofr_offer_10"))
        self.assertEqual(response.user_account_name, "owner")
        self.assertTrue(response.is_own_offer)
        self.assertEqual(response.remaining_quantity, 12)
        self.assertEqual(response.price, 123456)
        self.assertEqual(response.raw_price, 123456)
        self.assertEqual(response.market_published_price, 123456)
        self.assertEqual(response.viewer_effective_price, 123456)
        self.assertFalse(response.customer_badge_visible)
        self.assertEqual(response.expires_at_ts, int(offer.created_at.timestamp() + 15 * 60))

        hidden_response = offers_module.offer_to_response(
            offer,
            SimpleNamespace(offer_expiry_minutes=15),
            viewer_user_id=8,
            include_owner_identity=False,
        )
        self.assertIsNone(hidden_response.user_id)
        self.assertEqual(hidden_response.user_account_name, "")
        self.assertFalse(hidden_response.is_own_offer)
        self.assertEqual(hidden_response.viewer_effective_price, 123456)

        expired_offer = make_offer_model(status=OfferStatus.EXPIRED)
        expired_response = offers_module.offer_to_response(
            expired_offer,
            SimpleNamespace(offer_expiry_minutes=15),
        )
        self.assertIsNone(expired_response.expires_at_ts)

        with patch.object(offers_module, "logger") as logger, patch(
            "api.routers.offers.to_jalali_str",
            return_value="",
        ):
            broken_response = offers_module.offer_to_response(
                make_offer_model(created_at=BrokenCreatedAt()),
                SimpleNamespace(offer_expiry_minutes=15),
            )
        self.assertIsNone(broken_response.expires_at_ts)
        logger.error.assert_called_once()

        owner_relation = SimpleNamespace(
            owner_user_id=7,
            management_name="مشتری ویژه",
            customer_tier="tier1",
            status="active",
        )
        tier2_viewer_relation = SimpleNamespace(
            customer_tier="tier2",
            commission_rate="0.5",
            status="active",
        )
        projected_response = offers_module.offer_to_response(
            make_offer_model(price=53500, offer_type=OfferType.SELL),
            SimpleNamespace(offer_expiry_minutes=15),
            viewer_user_id=7,
            offer_owner_relation=owner_relation,
            viewer_customer_relation=tier2_viewer_relation,
        )
        self.assertEqual(projected_response.price, 53500)
        self.assertEqual(projected_response.raw_price, 53500)
        self.assertEqual(projected_response.market_published_price, 53500)
        self.assertEqual(projected_response.viewer_effective_price, 53800)
        self.assertTrue(projected_response.customer_badge_visible)
        self.assertEqual(projected_response.customer_management_name, "مشتری ویژه")
        self.assertEqual(projected_response.customer_tier, "tier1")

    async def test_offer_to_response_covers_public_owner_admin_and_tier2_pricing_matrix(self):
        owner_relation = SimpleNamespace(
            owner_user_id=7,
            management_name="مشتری ویژه",
            customer_tier="tier1",
            status="active",
        )
        tier2_viewer_relation = SimpleNamespace(
            customer_tier="tier2",
            commission_rate="0.5",
            status="active",
        )
        offer = make_offer_model(
            user_id=21,
            user=SimpleNamespace(account_name="tier1_source"),
            offer_type=OfferType.BUY,
            price=50000,
            exclude_from_competitive_price=True,
            price_warning_type="buy_above_highest_active",
        )

        public_view = offers_module.offer_to_response(
            offer,
            SimpleNamespace(offer_expiry_minutes=15),
            viewer_user_id=None,
            offer_owner_relation=owner_relation,
            include_owner_identity=False,
        )
        self.assertIsNone(public_view.user_id)
        self.assertEqual(public_view.user_account_name, "")
        self.assertEqual(public_view.raw_price, 50000)
        self.assertEqual(public_view.market_published_price, 50000)
        self.assertEqual(public_view.viewer_effective_price, 50000)
        self.assertFalse(public_view.customer_badge_visible)

        owner_view = offers_module.offer_to_response(
            offer,
            SimpleNamespace(offer_expiry_minutes=15),
            viewer_user_id=7,
            offer_owner_relation=owner_relation,
            include_owner_identity=True,
        )
        self.assertEqual(owner_view.user_id, 21)
        self.assertEqual(owner_view.user_account_name, "tier1_source")
        self.assertEqual(owner_view.viewer_effective_price, 50000)
        self.assertTrue(owner_view.customer_badge_visible)
        self.assertEqual(owner_view.customer_management_name, "مشتری ویژه")
        self.assertEqual(owner_view.customer_tier, "tier1")

        admin_like_view = offers_module.offer_to_response(
            offer,
            SimpleNamespace(offer_expiry_minutes=15),
            viewer_user_id=88,
            offer_owner_relation=owner_relation,
            include_owner_identity=True,
        )
        self.assertEqual(admin_like_view.user_id, 21)
        self.assertEqual(admin_like_view.user_account_name, "tier1_source")
        self.assertEqual(admin_like_view.viewer_effective_price, 50000)
        self.assertFalse(admin_like_view.customer_badge_visible)
        self.assertIsNone(admin_like_view.customer_management_name)

        tier2_view = offers_module.offer_to_response(
            offer,
            SimpleNamespace(offer_expiry_minutes=15),
            viewer_user_id=31,
            offer_owner_relation=owner_relation,
            viewer_customer_relation=tier2_viewer_relation,
            include_owner_identity=False,
        )
        self.assertIsNone(tier2_view.user_id)
        self.assertEqual(tier2_view.user_account_name, "")
        self.assertEqual(tier2_view.raw_price, 50000)
        self.assertEqual(tier2_view.market_published_price, 50000)
        self.assertEqual(tier2_view.viewer_effective_price, 49700)
        self.assertFalse(tier2_view.customer_badge_visible)

    async def test_send_offer_to_channel_builds_buttons_and_handles_failures(self):
        wholesale_offer = make_offer_model(price=75000)

        with patch("api.routers.offers.current_server", return_value="iran"):
            self.assertIsNone(await offers_module.send_offer_to_channel(wholesale_offer, SimpleNamespace(id=5)))

        with patch("api.routers.offers.current_server", return_value="foreign"), patch.object(
            offers_module.settings, "channel_id", "@offers"
        ), patch(
            "api.routers.offers.telegram_gateway.send_message",
            new=AsyncMock(
                return_value=TelegramGatewayResult(
                    ok=True,
                    method="sendMessage",
                    response_json={"result": {"message_id": 321}},
                )
            ),
        ) as send_mock:
            message_id = await offers_module.send_offer_to_channel(wholesale_offer, SimpleNamespace(id=5))

        self.assertEqual(message_id, 321)
        self.assertIn("🟢خرید Gold 12 عدد 75,000", send_mock.await_args.args[1])
        self.assertIn("توضیحات: urgent", send_mock.await_args.args[1])
        self.assertEqual(
            send_mock.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
            "ct2:ofr_offer_10:12",
        )

        retail_offer = make_offer_model(
            id=11,
            offer_type=OfferType.SELL,
            quantity=30,
            remaining_quantity=18,
            is_wholesale=False,
            lot_sizes=[10, 8, 10],
            original_lot_sizes=[10, 8, 10],
            notes=None,
        )
        with patch("api.routers.offers.current_server", return_value="foreign"), patch.object(
            offers_module.settings, "channel_id", "@offers"
        ), patch(
            "core.services.telegram_offer_channel_service.get_available_trade_amounts",
            return_value=[10, 8, 10],
        ), patch(
            "api.routers.offers.telegram_gateway.send_message",
            new=AsyncMock(
                return_value=TelegramGatewayResult(
                    ok=True,
                    method="sendMessage",
                    response_json={"result": {"message_id": 654}},
                )
            ),
        ) as send_mock:
            message_id = await offers_module.send_offer_to_channel(retail_offer, SimpleNamespace(id=5))

        self.assertEqual(message_id, 654)
        retail_buttons = send_mock.await_args.kwargs["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([button["text"] for button in retail_buttons], ["10 عدد", "8 عدد"])

        with patch("api.routers.offers.current_server", return_value="foreign"), patch.object(
            offers_module.settings, "channel_id", "@offers"
        ), patch(
            "api.routers.offers.telegram_gateway.send_message",
            new=AsyncMock(return_value=TelegramGatewayResult(ok=False, method="sendMessage", status_code=500, error="bad gateway")),
        ), patch("api.routers.offers.log_trading_event") as log_event:
            self.assertIsNone(await offers_module.send_offer_to_channel(wholesale_offer, SimpleNamespace(id=5)))
        log_event.assert_called_once()
        self.assertEqual(log_event.call_args.kwargs["level"], "warning")

        with patch("api.routers.offers.current_server", return_value="foreign"), patch.object(
            offers_module.settings, "channel_id", "@offers"
        ), patch(
            "api.routers.offers.telegram_gateway.send_message",
            new=AsyncMock(side_effect=RuntimeError("telegram down")),
        ), patch("api.routers.offers.log_trading_event") as log_event:
            self.assertIsNone(await offers_module.send_offer_to_channel(wholesale_offer, SimpleNamespace(id=5)))
        log_event.assert_called_once()
        self.assertEqual(log_event.call_args.kwargs["level"], "error")

    async def test_active_and_my_offer_queries_cover_filter_branches(self):
        current_user = SimpleNamespace(id=8)

        active_db = CapturingDB(FakeExecuteResult([]))
        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=20)),
        ), patch(
            "api.routers.offers.load_offer_customer_read_context",
            new=AsyncMock(return_value=({}, None)),
        ):
            result = await offers_module.get_active_offers(
                offer_type="sell",
                commodity_id=3,
                skip=4,
                limit=20,
                db=active_db,
                current_user=current_user,
            )

        self.assertEqual(result, [])
        active_sql = compile_sql(active_db.statements[0])
        self.assertIn("offers.commodity_id = 3", active_sql)
        self.assertIn("OFFSET 4", active_sql)
        self.assertIn("LIMIT 20", active_sql)

        since_hours_db = CapturingDB(FakeExecuteResult([]))
        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=20)),
        ), patch(
            "api.routers.offers.load_offer_customer_read_context",
            new=AsyncMock(return_value=({}, None)),
        ):
            result = await offers_module.get_my_offers(
                status_filter="active",
                since_hours=24,
                skip=0,
                limit=10,
                db=since_hours_db,
                current_user=current_user,
            )

        self.assertEqual(result, [])
        since_hours_sql = compile_sql(since_hours_db.statements[0])
        self.assertIn("offers.user_id = 8", since_hours_sql)
        self.assertIn("offers.republished_offer_id IS NULL", since_hours_sql)
        self.assertIn("offers.created_at >=", since_hours_sql)
        self.assertIn("offers.status =", since_hours_sql)

        recent_expired_db = CapturingDB(FakeExecuteResult([]))
        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=20)),
        ), patch(
            "api.routers.offers.load_offer_customer_read_context",
            new=AsyncMock(return_value=({}, None)),
        ):
            result = await offers_module.get_my_offers(
                status_filter="expired",
                since_hours=1,
                skip=0,
                limit=3,
                db=recent_expired_db,
                current_user=current_user,
            )

        self.assertEqual(result, [])
        recent_expired_sql = compile_sql(recent_expired_db.statements[0])
        self.assertIn("offers.user_id = 8", recent_expired_sql)
        self.assertNotIn("offers.expired_at IS NOT NULL", recent_expired_sql)
        self.assertIn("coalesce(offers.expired_at, offers.updated_at, offers.created_at) >=", recent_expired_sql)
        self.assertIn("offers.status = 'EXPIRED'", recent_expired_sql)
        self.assertIn("offers.status = 'ACTIVE'", recent_expired_sql)
        self.assertIn("offers.created_at <", recent_expired_sql)
        self.assertIn("offers.created_at >=", recent_expired_sql)
        self.assertIn("OR", recent_expired_sql)
        self.assertIn("ORDER BY coalesce(offers.expired_at, offers.updated_at, offers.created_at) DESC", recent_expired_sql)
        self.assertIn("LIMIT 3", recent_expired_sql)

        status_only_db = CapturingDB(FakeExecuteResult([]))
        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=20)),
        ), patch(
            "api.routers.offers.load_offer_customer_read_context",
            new=AsyncMock(return_value=({}, None)),
        ):
            result = await offers_module.get_my_offers(
                status_filter="cancelled",
                since_hours=None,
                skip=1,
                limit=5,
                db=status_only_db,
                current_user=current_user,
            )

        self.assertEqual(result, [])
        status_only_sql = compile_sql(status_only_db.statements[0])
        self.assertIn("offers.status =", status_only_sql)
        self.assertIn("OFFSET 1", status_only_sql)
        self.assertIn("LIMIT 5", status_only_sql)

    async def test_market_expired_offers_query_is_time_limit_only_and_customer_hidden(self):
        current_user = SimpleNamespace(id=8)

        expired_db = CapturingDB(FakeExecuteResult([]))
        with patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ):
            result = await offers_module.get_market_expired_offers(
                skip=25,
                limit=25,
                since_hours=48,
                db=expired_db,
                current_user=current_user,
            )

        self.assertEqual(result, [])
        expired_sql = compile_sql(expired_db.statements[0])
        self.assertIn("offers.status = 'EXPIRED'", expired_sql)
        self.assertIn("offers.expire_reason = 'time_limit'", expired_sql)
        self.assertIn("coalesce(offers.expired_at, offers.updated_at, offers.created_at) >=", expired_sql)
        self.assertIn("ORDER BY coalesce(offers.expired_at, offers.updated_at, offers.created_at) DESC", expired_sql)
        self.assertIn("OFFSET 25", expired_sql)
        self.assertIn("LIMIT 25", expired_sql)

        hidden_db = CapturingDB(FakeExecuteResult([]))
        with patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=SimpleNamespace(customer_tier="tier1")),
        ):
            result = await offers_module.get_market_expired_offers(
                db=hidden_db,
                current_user=current_user,
            )

        self.assertEqual(result, [])
        self.assertEqual(hidden_db.statements, [])

    async def test_market_offer_history_query_covers_completed_time_limit_and_traded_expired(self):
        current_user = SimpleNamespace(id=8)
        history_db = CapturingDB(FakeRowExecuteResult([]))

        with patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ):
            result = await offers_module.get_market_offer_history(
                skip=25,
                limit=25,
                since_hours=48,
                db=history_db,
                current_user=current_user,
            )

        self.assertEqual(result, [])
        history_sql = compile_sql(history_db.statements[0])
        self.assertIn("LEFT OUTER JOIN", history_sql)
        self.assertIn("trades.offer_id IS NOT NULL", history_sql)
        self.assertIn("trades.status = 'COMPLETED'", history_sql)
        self.assertIn("offers.status = 'COMPLETED'", history_sql)
        self.assertIn("offers.status = 'EXPIRED'", history_sql)
        self.assertIn("offers.expire_reason = 'time_limit'", history_sql)
        self.assertIn("coalesce(anon_1.traded_quantity, 0) > 0", history_sql)
        self.assertIn("coalesce(offers.expired_at, offers.updated_at, offers.created_at) >=", history_sql)
        self.assertIn("ORDER BY CASE", history_sql)
        self.assertIn("OFFSET 25", history_sql)
        self.assertIn("LIMIT 25", history_sql)

        hidden_db = CapturingDB(FakeRowExecuteResult([]))
        with patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=SimpleNamespace(customer_tier="tier1")),
        ):
            result = await offers_module.get_market_offer_history(
                db=hidden_db,
                current_user=current_user,
            )

        self.assertEqual(result, [])
        self.assertEqual(hidden_db.statements, [])

    async def test_market_offer_history_serializes_traded_and_partial_expired_metadata(self):
        current_user = SimpleNamespace(id=8)
        wholesale_completed_event_at = datetime(2026, 1, 2, 9, 0, 0)
        retail_completed_event_at = datetime(2026, 1, 2, 8, 45, 0)
        partial_event_at = datetime(2026, 1, 2, 8, 30, 0)
        pure_expired_event_at = datetime(2026, 1, 2, 8, 15, 0)
        wholesale_completed_offer = make_offer_model(
            id=21,
            status=OfferStatus.COMPLETED,
            quantity=10,
            remaining_quantity=0,
            is_wholesale=True,
        )
        retail_completed_offer = make_offer_model(
            id=22,
            status=OfferStatus.COMPLETED,
            quantity=20,
            remaining_quantity=0,
            is_wholesale=False,
        )
        partial_expired_offer = make_offer_model(
            id=23,
            status=OfferStatus.EXPIRED,
            expire_reason="manual",
            quantity=20,
            remaining_quantity=8,
            is_wholesale=False,
        )
        pure_expired_offer = make_offer_model(
            id=24,
            status=OfferStatus.EXPIRED,
            quantity=15,
            remaining_quantity=15,
            is_wholesale=True,
        )
        history_db = CapturingDB(
            FakeRowExecuteResult(
                [
                    (wholesale_completed_offer, 10, wholesale_completed_event_at),
                    (retail_completed_offer, 20, retail_completed_event_at),
                    (partial_expired_offer, 12, partial_event_at),
                    (pure_expired_offer, 0, pure_expired_event_at),
                ]
            )
        )
        serialized_rows = [
            make_offer_response(id=21, status="completed", quantity=10, remaining_quantity=0),
            make_offer_response(id=22, status="completed", quantity=20, remaining_quantity=0, is_wholesale=False),
            make_offer_response(id=23, status="expired", quantity=20, remaining_quantity=8, is_wholesale=False),
            make_offer_response(id=24, status="expired", quantity=15, remaining_quantity=15),
        ]

        with patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ), patch(
            "api.routers.offers._serialize_offer_responses",
            new=AsyncMock(return_value=serialized_rows),
        ) as serialize_mock, patch(
            "api.routers.offers.to_jalali_str",
            side_effect=lambda value, *_args, **_kwargs: f"jalali:{value.isoformat()}" if value else "",
        ):
            result = await offers_module.get_market_offer_history(
                skip=0,
                limit=25,
                since_hours=48,
                db=history_db,
                current_user=current_user,
            )

        self.assertEqual(len(result), 4)
        self.assertEqual(result[0].history_state, "traded")
        self.assertEqual(result[0].history_label, "معامله‌شده")
        self.assertEqual(result[0].traded_quantity, 10)
        self.assertFalse(result[0].is_partially_traded)
        self.assertTrue(result[0].is_read_only)
        self.assertEqual(result[0].history_event_at, "jalali:2026-01-02T09:00:00")
        self.assertEqual(result[1].history_state, "traded")
        self.assertEqual(result[1].history_label, "معامله‌شده")
        self.assertEqual(result[1].traded_quantity, 20)
        self.assertFalse(result[1].is_partially_traded)
        self.assertEqual(result[1].history_event_at, "jalali:2026-01-02T08:45:00")
        self.assertEqual(result[2].history_state, "traded")
        self.assertEqual(result[2].history_label, "معامله‌شده")
        self.assertEqual(result[2].traded_quantity, 12)
        self.assertTrue(result[2].is_partially_traded)
        self.assertEqual(result[2].history_event_at, "jalali:2026-01-02T08:30:00")
        self.assertEqual(result[3].history_state, "expired")
        self.assertEqual(result[3].history_label, "منقضی")
        self.assertEqual(result[3].traded_quantity, 0)
        self.assertFalse(result[3].is_partially_traded)
        self.assertEqual(result[3].history_event_at, "jalali:2026-01-02T08:15:00")
        serialize_mock.assert_awaited_once()
        self.assertEqual(serialize_mock.call_args.kwargs["viewer_user_id"], 8)
        self.assertFalse(serialize_mock.call_args.kwargs["include_owner_identity"])


if __name__ == "__main__":
    unittest.main()
