import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import OfferCreate, create_offer
from core.enums import UserRole


class FakeDB:
    def __init__(self, *, scalar_result=None, get_result=None):
        self.scalar_result = scalar_result
        self.get_result = get_result

    async def scalar(self, _stmt):
        return self.scalar_result

    async def get(self, _model, _id):
        return self.get_result


def make_offer(**overrides):
    data = {
        "offer_type": "buy",
        "commodity_id": 1,
        "quantity": 10,
        "price": 123456,
        "is_wholesale": True,
        "lot_sizes": None,
        "notes": None,
        "republished_from_id": None,
    }
    data.update(overrides)
    return OfferCreate(**data)


def make_user(**overrides):
    data = {
        "id": 5,
        "role": UserRole.STANDARD,
        "trading_restricted_until": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class OffersRouterCreateGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_offer_rejects_watch_users(self):
        with self.assertRaises(HTTPException) as exc_info:
            await create_offer(make_offer(), db=FakeDB(), current_user=make_user(role=UserRole.WATCH))
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شما دسترسی به بخش معاملات را ندارید.")

    async def test_create_offer_rejects_temporarily_restricted_users(self):
        current_user = make_user(trading_restricted_until=datetime.utcnow() + timedelta(hours=2))

        with self.assertRaises(HTTPException) as exc_info:
            await create_offer(make_offer(), db=FakeDB(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertIn("حساب شما مسدود است", exc_info.exception.detail)

    async def test_create_offer_rejects_user_limit_failures(self):
        current_user = make_user()

        with patch(
            "api.routers.offers.check_user_limits",
            side_effect=[(False, "channel blocked")],
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=FakeDB(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "channel blocked")

        with patch(
            "api.routers.offers.check_user_limits",
            side_effect=[(True, None), (False, "trade blocked")],
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=FakeDB(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "trade blocked")

    async def test_create_offer_rejects_when_active_offer_limit_is_reached(self):
        current_user = make_user()
        db = FakeDB(scalar_result=5)
        settings = SimpleNamespace(max_active_offers=5)

        with patch(
            "api.routers.offers.check_user_limits",
            side_effect=[(True, None), (True, None)],
        ), patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "core.cache.get_active_offer_count",
            new=AsyncMock(return_value=None),
        ), patch("core.cache.set_active_offer_count", new=AsyncMock()):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=db, current_user=current_user)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "شما حداکثر 5 لفظ فعال دارید. لطفاً ابتدا یکی را منقضی کنید.",
        )

    async def test_create_offer_rejects_missing_commodity(self):
        current_user = make_user()
        db = FakeDB(scalar_result=0, get_result=None)
        settings = SimpleNamespace(max_active_offers=5)

        with patch(
            "api.routers.offers.check_user_limits",
            side_effect=[(True, None), (True, None)],
        ), patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "core.cache.get_active_offer_count",
            new=AsyncMock(return_value=0),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=db, current_user=current_user)

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "کالا یافت نشد.")

    async def test_create_offer_rejects_shared_validation_failures(self):
        current_user = make_user()
        commodity = SimpleNamespace(id=1)
        db = FakeDB(scalar_result=0, get_result=commodity)
        settings = SimpleNamespace(max_active_offers=5)

        common_patches = [
            patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]),
            patch("api.routers.offers.get_trading_settings", return_value=settings),
            patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)),
        ]

        with common_patches[0], common_patches[1], common_patches[2], patch(
            "core.services.trade_service.validate_quantity",
            return_value=(False, "bad quantity"),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=db, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "bad quantity")

        db = FakeDB(scalar_result=0, get_result=commodity)
        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch(
            "core.services.trade_service.validate_price",
            return_value=(False, "bad price"),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=db, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "bad price")

        db = FakeDB(scalar_result=0, get_result=commodity)
        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch(
            "core.services.trade_service.validate_price",
            return_value=(True, None),
        ), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(False, "not competitive")),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=db, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "not competitive")


if __name__ == "__main__":
    unittest.main()