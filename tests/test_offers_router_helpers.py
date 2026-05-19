import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from api.routers import offers as offers_module
from models.offer import OfferStatus, OfferType


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


class OffersRouterHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_offer_to_response_handles_identity_and_expiry_paths(self):
        offer = make_offer_model()

        response = offers_module.offer_to_response(
            offer,
            SimpleNamespace(offer_expiry_minutes=15),
            viewer_user_id=5,
            include_owner_identity=True,
        )

        self.assertEqual(response.user_id, 5)
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

    async def test_send_offer_to_channel_builds_buttons_and_handles_failures(self):
        wholesale_offer = make_offer_model(price=75000)

        with patch("api.routers.offers.os.getenv", return_value=None):
            self.assertIsNone(await offers_module.send_offer_to_channel(wholesale_offer, SimpleNamespace(id=5)))

        wholesale_client = FakeAsyncClient(response=FakeHttpResponse(message_id=321))
        with patch("api.routers.offers.os.getenv", return_value="bot-token"), patch.object(
            offers_module.settings, "channel_id", "@offers"
        ), patch("api.routers.offers.httpx.AsyncClient", return_value=wholesale_client):
            message_id = await offers_module.send_offer_to_channel(wholesale_offer, SimpleNamespace(id=5))

        self.assertEqual(message_id, 321)
        wholesale_payload = wholesale_client.post.await_args.kwargs["json"]
        self.assertIn("🟢خرید Gold 12 عدد 75,000", wholesale_payload["text"])
        self.assertIn("توضیحات: urgent", wholesale_payload["text"])
        self.assertEqual(
            wholesale_payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
            "channel_trade:10:12",
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
        retail_client = FakeAsyncClient(response=FakeHttpResponse(message_id=654))
        with patch("api.routers.offers.os.getenv", return_value="bot-token"), patch.object(
            offers_module.settings, "channel_id", "@offers"
        ), patch(
            "api.routers.offers.get_available_trade_amounts",
            return_value=[10, 8, 10],
        ), patch("api.routers.offers.httpx.AsyncClient", return_value=retail_client):
            message_id = await offers_module.send_offer_to_channel(retail_offer, SimpleNamespace(id=5))

        self.assertEqual(message_id, 654)
        retail_buttons = retail_client.post.await_args.kwargs["json"]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([button["text"] for button in retail_buttons], ["10 عدد", "8 عدد"])

        with patch("api.routers.offers.os.getenv", return_value="bot-token"), patch.object(
            offers_module.settings, "channel_id", "@offers"
        ), patch(
            "api.routers.offers.httpx.AsyncClient",
            return_value=FakeAsyncClient(response=FakeHttpResponse(status_code=500, text="bad gateway")),
        ), patch.object(offers_module, "logger") as logger:
            self.assertIsNone(await offers_module.send_offer_to_channel(wholesale_offer, SimpleNamespace(id=5)))
        logger.error.assert_called_once()

        with patch("api.routers.offers.os.getenv", return_value="bot-token"), patch.object(
            offers_module.settings, "channel_id", "@offers"
        ), patch(
            "api.routers.offers.httpx.AsyncClient",
            return_value=FakeAsyncClient(error=RuntimeError("telegram down")),
        ), patch.object(offers_module, "logger") as logger:
            self.assertIsNone(await offers_module.send_offer_to_channel(wholesale_offer, SimpleNamespace(id=5)))
        logger.error.assert_called_once()

    async def test_active_and_my_offer_queries_cover_filter_branches(self):
        current_user = SimpleNamespace(id=8)

        active_db = CapturingDB(FakeExecuteResult([]))
        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=20)),
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

        status_only_db = CapturingDB(FakeExecuteResult([]))
        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=20)),
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


if __name__ == "__main__":
    unittest.main()