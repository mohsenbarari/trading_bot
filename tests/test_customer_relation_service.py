import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from core.services.customer_relation_service import (
    apply_customer_commission,
    build_allowed_customer_chat_targets,
    build_customer_offer_read_model,
    CUSTOMER_INVITATION_PREFIX,
    get_active_customer_relation_for_customer,
    get_active_customer_relation_for_user,
    get_effective_max_customers,
    get_owner_for_customer,
    get_pending_customer_relation_by_invitation_token,
    is_customer_invitation_token,
    is_user_customer,
    list_active_customers_for_owner,
    list_owner_customer_relations,
    round_customer_price,
    sweep_expired_pending_customer_relations,
    validate_customer_capacity,
    validate_customer_trade_limits,
    validate_owner_customer_capacity,
)
from core.utils import utc_now
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.offer import OfferType


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def first(self):
        return self._values[0] if self._values else None


class FakeExecuteResult:
    def __init__(self, values=None, scalar_one_value=None):
        self._values = values or []
        self._scalar_one_value = scalar_one_value

    def scalars(self):
        return FakeScalarResult(self._values)

    def scalar_one(self):
        return self._scalar_one_value

    def scalar_one_or_none(self):
        return self._scalar_one_value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class CustomerRelationServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_customer_relation_status_and_tier_columns_use_database_values(self):
        self.assertEqual(
            CustomerRelation.__table__.c.status.type.enums,
            ["pending", "active", "expired", "revoked", "deleted"],
        )
        self.assertEqual(
            CustomerRelation.__table__.c.customer_tier.type.enums,
            ["tier1", "tier2"],
        )

    def test_get_effective_max_customers_clamps_invalid_values(self):
        self.assertEqual(get_effective_max_customers(SimpleNamespace(max_customers=6)), 6)
        self.assertEqual(get_effective_max_customers(SimpleNamespace(max_customers=-2)), 0)
        self.assertEqual(get_effective_max_customers(SimpleNamespace(max_customers="bad")), 5)
        self.assertEqual(get_effective_max_customers(SimpleNamespace()), 5)
        self.assertTrue(is_customer_invitation_token(f"{CUSTOMER_INVITATION_PREFIX}123"))
        self.assertFalse(is_customer_invitation_token("INV-123"))

    async def test_get_active_customer_relation_for_customer_returns_active_relation(self):
        relation = SimpleNamespace(id=41, owner_user=SimpleNamespace(id=7), customer_user=SimpleNamespace(id=9))
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        result = await get_active_customer_relation_for_customer(db, 9)

        self.assertIs(result, relation)

    async def test_get_active_customer_relation_for_user_alias_and_owner_lookup(self):
        owner = SimpleNamespace(id=7)
        relation = SimpleNamespace(id=41, owner_user=owner, customer_user=SimpleNamespace(id=9))

        alias_db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])
        owner_db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        alias_result = await get_active_customer_relation_for_user(alias_db, 9)
        owner_result = await get_owner_for_customer(owner_db, 9)

        self.assertIs(alias_result, relation)
        self.assertIs(owner_result, owner)

    async def test_list_active_customers_for_owner_returns_rows(self):
        relation_one = SimpleNamespace(id=1)
        relation_two = SimpleNamespace(id=2)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[relation_one, relation_two])])

        result = await list_active_customers_for_owner(db, 12)

        self.assertEqual(result, [relation_one, relation_two])

    async def test_sweep_expired_pending_customer_relations_marks_rows_deleted(self):
        expired = SimpleNamespace(
            status=CustomerRelationStatus.PENDING,
            deleted_at=None,
        )
        db = FakeDB(execute_results=[FakeExecuteResult(values=[expired])])

        expired_relations = await sweep_expired_pending_customer_relations(db)

        self.assertEqual(expired_relations, [expired])
        self.assertEqual(expired.status, CustomerRelationStatus.EXPIRED)
        self.assertIsNotNone(expired.deleted_at)

    async def test_get_pending_customer_relation_by_invitation_token_commits_expired_rows_before_lookup(self):
        pending_relation = SimpleNamespace(id=12)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[SimpleNamespace(status=CustomerRelationStatus.PENDING, deleted_at=None)]),
                FakeExecuteResult(scalar_one_value=pending_relation),
            ]
        )

        result = await get_pending_customer_relation_by_invitation_token(db, f"{CUSTOMER_INVITATION_PREFIX}token")

        self.assertIs(result, pending_relation)
        db.commit.assert_awaited_once()

    async def test_validate_customer_capacity_raises_when_owner_is_full(self):
        owner = SimpleNamespace(id=5, max_customers=2)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[]), FakeExecuteResult(scalar_one_value=2)])

        with self.assertRaises(HTTPException) as exc_info:
            await validate_customer_capacity(db, owner)

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Owner has reached the maximum number of customers")

    async def test_validate_customer_capacity_returns_current_count_and_limit(self):
        owner = SimpleNamespace(id=5, max_customers=4)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[]), FakeExecuteResult(scalar_one_value=2)])

        current_count, limit = await validate_customer_capacity(db, owner)

        self.assertEqual(current_count, 2)
        self.assertEqual(limit, 4)

    async def test_validate_owner_customer_capacity_alias(self):
        owner = SimpleNamespace(id=5, max_customers=4)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[]), FakeExecuteResult(scalar_one_value=2)])

        current_count, limit = await validate_owner_customer_capacity(db, owner)

        self.assertEqual(current_count, 2)
        self.assertEqual(limit, 4)

    async def test_list_owner_customer_relations_commits_expired_rows_then_returns_pending_and_active(self):
        relation_one = SimpleNamespace(id=1)
        relation_two = SimpleNamespace(id=2)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[SimpleNamespace(status=CustomerRelationStatus.PENDING, deleted_at=None)]),
                FakeExecuteResult(values=[relation_one, relation_two]),
            ]
        )

        result = await list_owner_customer_relations(db, 7)

        self.assertEqual(result, [relation_one, relation_two])
        db.commit.assert_awaited_once()

    async def test_is_user_customer_delegates_to_active_relation_lookup(self):
        relation = SimpleNamespace(id=9)
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        self.assertTrue(await is_user_customer(db, 5))

    def test_round_customer_price_uses_buy_floor_and_sell_ceil(self):
        self.assertEqual(round_customer_price("49750", OfferType.BUY), 49700)
        self.assertEqual(round_customer_price("50250", OfferType.SELL), 50300)

    def test_apply_customer_commission_supports_examples_and_zero_rate_passthrough(self):
        self.assertEqual(apply_customer_commission(50000, "0.5", OfferType.BUY), 49700)
        self.assertEqual(apply_customer_commission(50000, "0.5", OfferType.SELL), 50300)
        self.assertEqual(apply_customer_commission(192800, "0.5", OfferType.BUY), 191800)
        self.assertEqual(apply_customer_commission(53500, "0.5", OfferType.SELL), 53800)
        self.assertEqual(apply_customer_commission(53500, None, OfferType.SELL), 53500)
        self.assertEqual(apply_customer_commission(53500, 0, OfferType.SELL), 53500)

    def test_apply_customer_commission_rejects_invalid_inputs(self):
        with self.assertRaises(ValueError):
            apply_customer_commission(0, "0.5", OfferType.BUY)
        with self.assertRaises(ValueError):
            apply_customer_commission(50000, "bad", OfferType.BUY)
        with self.assertRaises(ValueError):
            round_customer_price("50000", "invalid")

    def test_build_customer_offer_read_model_keeps_tier1_prices_and_owner_only_badge(self):
        owner_relation = SimpleNamespace(
            owner_user_id=7,
            management_name="مشتری ویژه",
            customer_tier="tier1",
            status=CustomerRelationStatus.ACTIVE,
        )

        public_view = build_customer_offer_read_model(
            raw_price=52000,
            offer_type=OfferType.SELL,
            viewer_user_id=19,
            offer_owner_relation=owner_relation,
        )
        self.assertEqual(public_view.raw_price, 52000)
        self.assertEqual(public_view.market_published_price, 52000)
        self.assertEqual(public_view.viewer_effective_price, 52000)
        self.assertFalse(public_view.customer_badge_visible)
        self.assertIsNone(public_view.customer_management_name)
        self.assertIsNone(public_view.customer_tier)

        owner_view = build_customer_offer_read_model(
            raw_price=52000,
            offer_type=OfferType.SELL,
            viewer_user_id=7,
            offer_owner_relation=owner_relation,
        )
        self.assertEqual(owner_view.raw_price, 52000)
        self.assertEqual(owner_view.market_published_price, 52000)
        self.assertEqual(owner_view.viewer_effective_price, 52000)
        self.assertTrue(owner_view.customer_badge_visible)
        self.assertEqual(owner_view.customer_management_name, "مشتری ویژه")
        self.assertEqual(owner_view.customer_tier, "tier1")

    def test_build_customer_offer_read_model_projects_tier2_viewer_effective_price(self):
        viewer_relation = SimpleNamespace(
            customer_tier="tier2",
            commission_rate="0.5",
            status=CustomerRelationStatus.ACTIVE,
        )

        buy_view = build_customer_offer_read_model(
            raw_price=192800,
            offer_type=OfferType.BUY,
            viewer_user_id=9,
            viewer_customer_relation=viewer_relation,
        )
        self.assertEqual(buy_view.raw_price, 192800)
        self.assertEqual(buy_view.market_published_price, 192800)
        self.assertEqual(buy_view.viewer_effective_price, 191800)

        sell_view = build_customer_offer_read_model(
            raw_price=53500,
            offer_type=OfferType.SELL,
            viewer_user_id=9,
            viewer_customer_relation=viewer_relation,
        )
        self.assertEqual(sell_view.viewer_effective_price, 53800)

    def test_build_customer_offer_read_model_ignores_inactive_or_non_tier2_viewer_relations(self):
        inactive_view = build_customer_offer_read_model(
            raw_price=50000,
            offer_type=OfferType.BUY,
            viewer_customer_relation=SimpleNamespace(
                customer_tier="tier2",
                commission_rate="0.8",
                status=CustomerRelationStatus.PENDING,
            ),
        )
        self.assertEqual(inactive_view.viewer_effective_price, 50000)

        tier1_view = build_customer_offer_read_model(
            raw_price=50000,
            offer_type=OfferType.SELL,
            viewer_customer_relation=SimpleNamespace(
                customer_tier="tier1",
                commission_rate="0.8",
                status=CustomerRelationStatus.ACTIVE,
            ),
        )
        self.assertEqual(tier1_view.viewer_effective_price, 50000)

    def test_build_customer_offer_read_model_rejects_invalid_raw_price(self):
        with self.assertRaises(ValueError):
            build_customer_offer_read_model(raw_price=0, offer_type=OfferType.BUY)

    def test_validate_customer_trade_limits_accepts_valid_relation(self):
        relation = SimpleNamespace(
            status=CustomerRelationStatus.ACTIVE,
            trading_restricted_until=None,
            min_trade_quantity=5,
            max_trade_quantity=20,
            max_daily_trades=3,
            max_daily_commodity_volume=30,
        )

        validate_customer_trade_limits(
            relation,
            quantity=10,
            trades_today=2,
            commodity_volume_today=15,
            now=utc_now().replace(tzinfo=None),
        )

    def test_validate_customer_trade_limits_enforces_status_restriction_and_caps(self):
        now = utc_now().replace(tzinfo=None)
        inactive_relation = SimpleNamespace(status=CustomerRelationStatus.PENDING)
        with self.assertRaises(HTTPException) as exc_info:
            validate_customer_trade_limits(inactive_relation, quantity=5, now=now)
        self.assertEqual(exc_info.exception.detail, "Customer relation is not active")

        restricted_relation = SimpleNamespace(
            status=CustomerRelationStatus.ACTIVE,
            trading_restricted_until=now + timedelta(hours=1),
            min_trade_quantity=None,
            max_trade_quantity=None,
            max_daily_trades=None,
            max_daily_commodity_volume=None,
        )
        with self.assertRaises(HTTPException) as exc_info:
            validate_customer_trade_limits(restricted_relation, quantity=5, now=now)
        self.assertEqual(exc_info.exception.detail, "Customer is temporarily restricted from trading")

        capped_relation = SimpleNamespace(
            status=CustomerRelationStatus.ACTIVE,
            trading_restricted_until=None,
            min_trade_quantity=5,
            max_trade_quantity=20,
            max_daily_trades=3,
            max_daily_commodity_volume=30,
        )
        with self.assertRaises(HTTPException) as exc_info:
            validate_customer_trade_limits(capped_relation, quantity=4, now=now)
        self.assertEqual(exc_info.exception.detail, "Trade quantity is below the customer's minimum limit")

        with self.assertRaises(HTTPException) as exc_info:
            validate_customer_trade_limits(capped_relation, quantity=21, now=now)
        self.assertEqual(exc_info.exception.detail, "Trade quantity exceeds the customer's maximum limit")

        with self.assertRaises(HTTPException) as exc_info:
            validate_customer_trade_limits(capped_relation, quantity=5, trades_today=3, now=now)
        self.assertEqual(exc_info.exception.detail, "Customer has reached the daily trade limit")

        with self.assertRaises(HTTPException) as exc_info:
            validate_customer_trade_limits(capped_relation, quantity=16, trades_today=1, commodity_volume_today=15, now=now)
        self.assertEqual(exc_info.exception.detail, "Customer has reached the daily commodity volume limit")

    async def test_build_allowed_customer_chat_targets_includes_owner_accountants_and_super_admin_without_customers(self):
        relation = SimpleNamespace(owner_user_id=7)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=relation),
                FakeExecuteResult(values=[7, 40]),
                FakeExecuteResult(values=[11, 12]),
            ]
        )

        result = await build_allowed_customer_chat_targets(db, 9)

        self.assertEqual(result, [7, 11, 12, 40])


if __name__ == "__main__":
    unittest.main()