import unittest
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import OfferCreate, create_offer
from core.enums import UserAccountStatus, UserRole
from models.customer_relation import CustomerTier


class FakeDB:
    def __init__(self, *, scalar_result=None, get_result=None, execute_scalar_result=None):
        self.scalar_result = scalar_result
        self.get_result = get_result
        self.execute_scalar_result = execute_scalar_result

    async def scalar(self, _stmt):
        return self.scalar_result

    async def get(self, _model, _id):
        return self.get_result

    async def execute(self, _stmt):
        return SimpleNamespace(
            scalar_one_or_none=lambda: self.execute_scalar_result,
        )


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
        "republished_from_public_id": None,
        "idempotency_key": "test-offer-request-0001",
    }
    data.update(overrides)
    return OfferCreate(**data)


def make_user(**overrides):
    data = {
        "id": 5,
        "role": UserRole.STANDARD,
        "account_status": UserAccountStatus.ACTIVE,
        "trading_restricted_until": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_context(owner_user=None, actor_user=None):
    owner = owner_user or make_user()
    actor = actor_user or owner
    return SimpleNamespace(owner_user=owner, actor_user=actor, relation=None, is_accountant_context=owner.id != actor.id)


class OffersRouterCreateGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        market_eval_patcher = patch(
            "api.routers.offers.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True, reason="daily_window_open")),
        )
        self.market_eval_mock = market_eval_patcher.start()
        self.addCleanup(market_eval_patcher.stop)

    def test_offer_create_requires_a_stable_well_formed_idempotency_key(self):
        base = {
            "offer_type": "buy",
            "commodity_id": 1,
            "quantity": 10,
            "price": 123456,
        }
        for invalid in (None, "", "short", "contains whitespace"):
            payload = dict(base)
            if invalid is not None:
                payload["idempotency_key"] = invalid
            with self.subTest(idempotency_key=invalid), self.assertRaises(ValueError):
                OfferCreate(**payload)

    async def test_create_offer_rejects_watch_users(self):
        with self.assertRaises(HTTPException) as exc_info:
            await create_offer(make_offer(), db=FakeDB(), context=make_context(make_user(role=UserRole.WATCH)))
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شما دسترسی به بخش معاملات را ندارید.")

    async def test_create_offer_rejects_accountant_context(self):
        with self.assertRaises(HTTPException) as exc_info:
            await create_offer(
                make_offer(),
                db=FakeDB(),
                context=make_context(make_user(id=5), make_user(id=9)),
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حسابدار دسترسی به بازار ندارد.")

    async def test_create_offer_rejects_temporarily_restricted_users(self):
        current_user = make_user(trading_restricted_until=datetime.utcnow() + timedelta(hours=2))

        with self.assertRaises(HTTPException) as exc_info:
            await create_offer(make_offer(), db=FakeDB(), context=make_context(current_user))
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertIn("حساب شما مسدود است", exc_info.exception.detail)

    async def test_create_offer_rejects_inactive_users(self):
        inactive_user = make_user(account_status=UserAccountStatus.INACTIVE)

        with self.assertRaises(HTTPException) as exc_info:
            await create_offer(make_offer(), db=FakeDB(), context=make_context(inactive_user))

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حساب شما غیرفعال است و دسترسی شما به بازار بسته شده است.")

    async def test_create_offer_rejects_when_market_is_closed(self):
        current_user = make_user()
        self.market_eval_mock.return_value = SimpleNamespace(is_open=False, reason="after_daily_window_close")

        with patch("api.routers.offers.check_user_limits") as check_limits_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=FakeDB(), context=make_context(current_user))

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(
            exc_info.exception.detail,
            "بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید.",
        )
        check_limits_mock.assert_not_called()

    async def test_create_offer_rejects_tier2_customers(self):
        current_user = make_user()
        tier2_relation = SimpleNamespace(customer_tier=CustomerTier.TIER_2)

        with patch(
            "api.routers.offers.check_user_limits",
            side_effect=[(True, None), (True, None)],
        ), patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=tier2_relation),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=FakeDB(), context=make_context(current_user))

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "مشتری سطح 2 مجاز به ثبت لفظ نیست و فقط می‌تواند روی لفظ‌های دیگر درخواست بزند.",
        )

    async def test_create_offer_rejects_user_limit_failures(self):
        current_user = make_user()

        with patch(
            "api.routers.offers.check_user_limits",
            side_effect=[(False, "channel blocked")],
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=FakeDB(), context=make_context(current_user))
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "channel blocked")

        with patch(
            "api.routers.offers.check_user_limits",
            side_effect=[(True, None), (False, "trade blocked")],
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=FakeDB(), context=make_context(current_user))
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
                await create_offer(make_offer(), db=db, context=make_context(current_user))

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "شما حداکثر 5 لفظ فعال دارید. لطفاً ابتدا یکی را منقضی کنید.",
        )

    async def test_republish_is_an_independent_offer_for_active_quota(self):
        current_user = make_user()
        db = FakeDB(scalar_result=5)
        settings = SimpleNamespace(max_active_offers=5)
        source = SimpleNamespace(
            id=31,
            offer_public_id="ofr_source_31",
            offer_type="buy",
            settlement_type="cash",
            commodity_id=1,
            quantity=10,
            remaining_quantity=10,
            price=123456,
            is_wholesale=True,
            lot_sizes=None,
            notes=None,
        )

        lock_order = []

        async def acquire_fence(_db):
            lock_order.append("market_fence")
            return SimpleNamespace(is_open=True)

        async def lock_source(*_args, **_kwargs):
            lock_order.append("source_offer")
            return source

        with patch(
            "api.routers.offers.lock_repeatable_offer",
            new=AsyncMock(side_effect=lock_source),
        ), patch(
            "api.routers.offers.acquire_market_offer_admission_fence",
            new=AsyncMock(side_effect=acquire_fence),
        ), patch(
            "api.routers.offers.check_user_limits",
            side_effect=[(True, None), (True, None)],
        ), patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "core.cache.get_active_offer_count",
            new=AsyncMock(return_value=5),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(
                    make_offer(
                        republished_from_id=31,
                        republished_from_public_id="ofr_source_31",
                        idempotency_key="repeat-source-31",
                    ),
                    db=db,
                    context=make_context(current_user),
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "شما حداکثر 5 لفظ فعال دارید. لطفاً ابتدا یکی را منقضی کنید.",
        )
        self.assertEqual(lock_order, [])

    async def test_republish_requires_source_public_identity(self):
        with patch("api.routers.offers.lock_repeatable_offer", new=AsyncMock()) as lock_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(
                    make_offer(
                        republished_from_id=31,
                        idempotency_key="repeat-source-31",
                    ),
                    db=FakeDB(),
                    context=make_context(),
                )

        self.assertEqual(exc_info.exception.status_code, 400)
        lock_mock.assert_not_awaited()

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
                await create_offer(make_offer(), db=db, context=make_context(current_user))

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
                await create_offer(make_offer(), db=db, context=make_context(current_user))
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
                await create_offer(make_offer(), db=db, context=make_context(current_user))
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
                await create_offer(make_offer(), db=db, context=make_context(current_user))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "not competitive")

    async def test_create_offer_rejects_missing_or_invalid_retail_lots(self):
        current_user = make_user()
        commodity = SimpleNamespace(id=1)
        settings = SimpleNamespace(max_active_offers=5)

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(
                    make_offer(is_wholesale=False, lot_sizes=None),
                    db=FakeDB(scalar_result=0, get_result=commodity),
                    context=make_context(current_user),
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "برای آفر خُرد باید لات‌ها مشخص شوند.")

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_lot_sizes",
            return_value=(False, "bad lots", [5, 5]),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(
                    make_offer(is_wholesale=False, lot_sizes=[4, 6]),
                    db=FakeDB(scalar_result=0, get_result=commodity),
                    context=make_context(current_user),
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "bad lots")

    async def test_create_offer_returns_warning_response_before_publishing_outlier_offer(self):
        current_user = make_user()
        commodity = SimpleNamespace(id=1)
        db = FakeDB(scalar_result=0, get_result=commodity)
        settings = SimpleNamespace(max_active_offers=5)
        warning_payload = {
            "error_code": "OFFER_PRICE_WARNING",
            "warning_type": "sell_below_lowest_active",
            "title": "هشدار قیمت فروش",
            "detail": "قیمت فروش شما از پایین\u200cترین فروش فعال مشابه پایین\u200cتر است.",
            "message": "warning message",
            "reference_label": "پایین\u200cترین قیمت فروش فعال",
            "reference_price": 100000,
            "proposed_price": 99900,
            "difference_percent": 0.1,
        }

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=warning_payload),
        ):
            response = await create_offer(
                make_offer(offer_type="sell", price=99900),
                db=db,
                context=make_context(current_user),
            )

        self.assertEqual(response.status_code, 409)
        payload = json.loads(response.body)
        self.assertEqual(payload["error_code"], "OFFER_PRICE_WARNING")
        self.assertEqual(payload["warning"]["reference_price"], 100000)

    async def test_create_offer_blocks_accountant_context_before_owner_limits(self):
        owner_user = make_user(id=5)
        actor_user = make_user(id=44, role=UserRole.WATCH)

        with patch(
            "api.routers.offers.check_user_limits",
            side_effect=[(False, "owner blocked")],
        ) as limits_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=FakeDB(), context=make_context(owner_user, actor_user))

        self.assertEqual(exc_info.exception.detail, "حسابدار دسترسی به بازار ندارد.")
        limits_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
