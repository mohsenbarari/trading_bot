import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from core.services import customer_relation_service
from core.services.customer_relation_service import (
    apply_customer_commission,
    build_allowed_customer_chat_targets,
    build_customer_offer_read_model,
    CAPACITY_TRACKED_CUSTOMER_RELATION_STATUSES,
    cancel_pending_customer_relation,
    create_owner_customer_relation,
    CUSTOMER_INVITATION_PREFIX,
    get_active_customer_relation_for_customer,
    get_active_customer_relation_for_user,
    get_effective_max_customers,
    get_owner_for_customer,
    get_pending_customer_relation_by_invitation_token,
    is_customer_invitation_token,
    is_user_customer,
    load_offer_customer_read_context,
    load_customer_relation_invitation_map,
    list_active_customers_for_owner,
    list_shared_group_accountant_ids_for_customer,
    list_owner_customer_relations,
    round_customer_price,
    sweep_expired_pending_customer_relations,
    unlink_owner_customer_relation,
    update_owner_customer_relation,
    validate_customer_capacity,
    validate_customer_trade_limits,
    validate_owner_customer_capacity,
)
from core.utils import utc_now
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.offer import OfferType
from models.user import UserRole
from unittest.mock import patch


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
        self.refresh = AsyncMock()
        self.added = []

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, item):
        self.added.append(item)


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

    def test_customer_relation_partial_unique_indexes_protect_live_management_names_and_customers(self):
        indexes = {index.name: index for index in CustomerRelation.__table__.indexes}

        management_index = indexes["ux_customer_relations_owner_management_active"]
        self.assertTrue(management_index.unique)
        self.assertEqual([column.name for column in management_index.columns], ["owner_user_id", "management_name"])
        self.assertIn("deleted_at IS NULL", str(management_index.dialect_options["postgresql"]["where"]))

        active_customer_index = indexes["ux_customer_relations_customer_active"]
        self.assertTrue(active_customer_index.unique)
        self.assertEqual([column.name for column in active_customer_index.columns], ["customer_user_id"])
        self.assertIn("customer_user_id IS NOT NULL", str(active_customer_index.dialect_options["postgresql"]["where"]))
        self.assertIn("deleted_at IS NULL", str(active_customer_index.dialect_options["postgresql"]["where"]))

    def test_capacity_tracked_statuses_only_include_pending_and_active(self):
        self.assertEqual(
            CAPACITY_TRACKED_CUSTOMER_RELATION_STATUSES,
            (CustomerRelationStatus.PENDING, CustomerRelationStatus.ACTIVE),
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

    async def test_load_customer_relation_invitation_map_returns_tokens(self):
        invitation = SimpleNamespace(token=f"{CUSTOMER_INVITATION_PREFIX}abc", account_name="cust-1")
        db = FakeDB(execute_results=[FakeExecuteResult(values=[invitation])])

        result = await load_customer_relation_invitation_map(db, {invitation.token})

        self.assertEqual(result, {invitation.token: invitation})

    async def test_owner_lookup_and_invitation_map_empty_inputs_return_none_or_empty(self):
        relation_db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=None)])

        self.assertIsNone(await get_owner_for_customer(relation_db, 9))
        self.assertEqual(await load_customer_relation_invitation_map(FakeDB(), ["", None, "   "]), {})

    async def test_unlink_owner_customer_relation_soft_deletes_active_customer_and_marks_relation_deleted(self):
        customer_user = SimpleNamespace(id=81, is_deleted=False)
        relation = SimpleNamespace(
            id=14,
            owner_user_id=7,
            customer_user=customer_user,
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
        )
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        with patch("core.services.user_deletion_service.delete_user_account", new=AsyncMock()) as delete_mock:
            result = await unlink_owner_customer_relation(db, owner_user_id=7, relation_id=14)

        self.assertIs(result, relation)
        delete_mock.assert_awaited_once_with(db, customer_user)
        self.assertEqual(relation.status, CustomerRelationStatus.DELETED)
        self.assertIsNotNone(relation.deleted_at)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(relation)

    async def test_create_owner_customer_relation_creates_pending_relation_and_standard_invitation(self):
        owner = SimpleNamespace(id=7, max_customers=4)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[]),
                FakeExecuteResult(scalar_one_value=1),
                FakeExecuteResult(scalar_one_value=None),
                FakeExecuteResult(scalar_one_value=None),
                FakeExecuteResult(scalar_one_value=None),
            ]
        )

        relation, invitation = await create_owner_customer_relation(
            db,
            owner_user=owner,
            account_name="customer_one",
            management_name="مشتری اول",
            mobile_number="09120000000",
            customer_tier=CustomerTier.TIER_2,
            commission_rate="0.5",
            min_trade_quantity=1,
            max_trade_quantity=5,
            max_daily_trades=3,
            max_daily_commodity_volume=10,
        )

        self.assertEqual(invitation.role, UserRole.STANDARD)
        self.assertTrue(invitation.token.startswith(CUSTOMER_INVITATION_PREFIX))
        self.assertEqual(relation.owner_user_id, 7)
        self.assertEqual(relation.customer_tier, CustomerTier.TIER_2)
        self.assertEqual(str(relation.commission_rate), "0.50")
        self.assertEqual(relation.management_name, "مشتری اول")
        self.assertEqual(len(db.added), 2)
        db.commit.assert_awaited_once()
        self.assertEqual(db.refresh.await_count, 2)

    async def test_create_owner_customer_relation_rejects_invalid_identity_inputs(self):
        owner = SimpleNamespace(id=7, max_customers=4)

        with self.assertRaises(HTTPException) as account_exc:
            await create_owner_customer_relation(
                FakeDB(),
                owner_user=owner,
                account_name="   ",
                management_name="مشتری",
                mobile_number="09120000000",
            )
        self.assertEqual(account_exc.exception.detail, "نام کاربری نامعتبر است")

        with self.assertRaises(HTTPException) as name_exc:
            await create_owner_customer_relation(
                FakeDB(),
                owner_user=owner,
                account_name="customer_one",
                management_name="   ",
                mobile_number="09120000000",
            )
        self.assertEqual(name_exc.exception.detail, "نام مدیریتی مشتری الزامی است")

        with self.assertRaises(HTTPException) as mobile_exc:
            await create_owner_customer_relation(
                FakeDB(),
                owner_user=owner,
                account_name="customer_one",
                management_name="مشتری",
                mobile_number="0912",
            )
        self.assertEqual(mobile_exc.exception.detail, "شماره موبایل نامعتبر است")

    async def test_create_owner_customer_relation_rejects_duplicate_user_relation_and_management_name(self):
        owner = SimpleNamespace(id=7, max_customers=4)

        duplicate_user_db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[]),
                FakeExecuteResult(scalar_one_value=1),
                FakeExecuteResult(scalar_one_value=SimpleNamespace(id=90)),
            ]
        )
        with self.assertRaises(HTTPException) as user_exc:
            await create_owner_customer_relation(
                duplicate_user_db,
                owner_user=owner,
                account_name="customer_one",
                management_name="مشتری",
                mobile_number="09120000000",
            )
        self.assertEqual(user_exc.exception.detail, "کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است")

        duplicate_relation_db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[]),
                FakeExecuteResult(scalar_one_value=1),
                FakeExecuteResult(scalar_one_value=None),
                FakeExecuteResult(scalar_one_value=SimpleNamespace(id=91)),
            ]
        )
        with self.assertRaises(HTTPException) as relation_exc:
            await create_owner_customer_relation(
                duplicate_relation_db,
                owner_user=owner,
                account_name="customer_two",
                management_name="مشتری دوم",
                mobile_number="09120000001",
            )
        self.assertEqual(relation_exc.exception.detail, "یک مشتری pending یا active با این نام کاربری یا موبایل وجود دارد")

        duplicate_management_db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[]),
                FakeExecuteResult(scalar_one_value=1),
                FakeExecuteResult(scalar_one_value=None),
                FakeExecuteResult(scalar_one_value=None),
                FakeExecuteResult(scalar_one_value=SimpleNamespace(id=92)),
            ]
        )
        with self.assertRaises(HTTPException) as management_exc:
            await create_owner_customer_relation(
                duplicate_management_db,
                owner_user=owner,
                account_name="customer_three",
                management_name="مشتری مشترک",
                mobile_number="09120000002",
            )
        self.assertEqual(management_exc.exception.detail, "این نام مدیریتی قبلاً برای یکی از مشتریان این مالک استفاده شده است")

    async def test_cancel_pending_customer_relation_revokes_relation_and_marks_invitation_used(self):
        relation = SimpleNamespace(
            id=9,
            owner_user_id=7,
            status=CustomerRelationStatus.PENDING,
            deleted_at=None,
            invitation_token=f"{CUSTOMER_INVITATION_PREFIX}cancel",
        )
        invitation = SimpleNamespace(token=relation.invitation_token, is_used=False, expires_at=None)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=relation),
                FakeExecuteResult(scalar_one_value=invitation),
            ]
        )

        result = await cancel_pending_customer_relation(db, owner_user_id=7, relation_id=9)

        self.assertIs(result, relation)
        self.assertEqual(relation.status, CustomerRelationStatus.REVOKED)
        self.assertTrue(invitation.is_used)
        self.assertIsNotNone(relation.deleted_at)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(relation)

    async def test_cancel_unlink_and_update_relation_guard_paths(self):
        with self.assertRaises(HTTPException) as cancel_missing_exc:
            await cancel_pending_customer_relation(
                FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=None)]),
                owner_user_id=7,
                relation_id=1,
            )
        self.assertEqual(cancel_missing_exc.exception.detail, "رابطه مشتری یافت نشد")

        closed_pending = SimpleNamespace(
            id=2,
            owner_user_id=7,
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
        )
        with self.assertRaises(HTTPException) as cancel_closed_exc:
            await cancel_pending_customer_relation(
                FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=closed_pending)]),
                owner_user_id=7,
                relation_id=2,
            )
        self.assertEqual(cancel_closed_exc.exception.detail, "فقط دعوت مشتری pending قابل لغو است")

        with self.assertRaises(HTTPException) as unlink_missing_exc:
            await unlink_owner_customer_relation(
                FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=None)]),
                owner_user_id=7,
                relation_id=3,
            )
        self.assertEqual(unlink_missing_exc.exception.detail, "رابطه مشتری یافت نشد")

        closed_relation = SimpleNamespace(
            id=4,
            owner_user_id=7,
            status=CustomerRelationStatus.EXPIRED,
            deleted_at=None,
            customer_user=None,
        )
        with self.assertRaises(HTTPException) as unlink_closed_exc:
            await unlink_owner_customer_relation(
                FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=closed_relation)]),
                owner_user_id=7,
                relation_id=4,
            )
        self.assertEqual(unlink_closed_exc.exception.detail, "این رابطه قبلاً بسته شده است")

        pending_relation = SimpleNamespace(
            id=5,
            owner_user_id=7,
            status=CustomerRelationStatus.PENDING,
            deleted_at=None,
            customer_user=None,
        )
        pending_db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=pending_relation)])
        with patch(
            "core.services.customer_relation_service.cancel_pending_customer_relation",
            new=AsyncMock(return_value="cancelled"),
        ) as cancel_mock:
            result = await unlink_owner_customer_relation(pending_db, owner_user_id=7, relation_id=5)
        self.assertEqual(result, "cancelled")
        cancel_mock.assert_awaited_once_with(pending_db, owner_user_id=7, relation_id=5)

        invalid_active_state = SimpleNamespace(
            id=6,
            owner_user_id=7,
            status="unexpected",
            deleted_at=None,
            customer_user=None,
        )
        with self.assertRaises(HTTPException) as unlink_invalid_exc:
            await unlink_owner_customer_relation(
                FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=invalid_active_state)]),
                owner_user_id=7,
                relation_id=6,
            )
        self.assertEqual(unlink_invalid_exc.exception.detail, "فقط مشتری pending یا active قابل قطع ارتباط است")

        with self.assertRaises(HTTPException) as update_missing_exc:
            await update_owner_customer_relation(
                FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=None)]),
                owner_user_id=7,
                relation_id=7,
                update_data={},
            )
        self.assertEqual(update_missing_exc.exception.detail, "رابطه مشتری یافت نشد")

        update_closed_relation = SimpleNamespace(
            id=8,
            owner_user_id=7,
            status=CustomerRelationStatus.DELETED,
            deleted_at=utc_now().replace(tzinfo=None),
        )
        with self.assertRaises(HTTPException) as update_closed_exc:
            await update_owner_customer_relation(
                FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=update_closed_relation)]),
                owner_user_id=7,
                relation_id=8,
                update_data={},
            )
        self.assertEqual(update_closed_exc.exception.detail, "فقط مشتری pending یا active قابل ویرایش است")

    async def test_update_owner_customer_relation_updates_limits_and_clears_commission_for_tier1(self):
        relation = SimpleNamespace(
            id=13,
            owner_user_id=7,
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
            customer_tier=CustomerTier.TIER_2,
            commission_rate="0.75",
            min_trade_quantity=1,
            max_trade_quantity=9,
            max_daily_trades=2,
            max_daily_commodity_volume=20,
        )
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        result = await update_owner_customer_relation(
            db,
            owner_user_id=7,
            relation_id=13,
            update_data={
                "customer_tier": CustomerTier.TIER_1,
                "commission_rate": None,
                "min_trade_quantity": 2,
                "max_trade_quantity": 8,
                "max_daily_trades": 4,
                "max_daily_commodity_volume": 25,
            },
        )

        self.assertIs(result, relation)
        self.assertEqual(relation.customer_tier, CustomerTier.TIER_1)
        self.assertIsNone(relation.commission_rate)
        self.assertEqual(relation.min_trade_quantity, 2)
        self.assertEqual(relation.max_trade_quantity, 8)
        self.assertEqual(relation.max_daily_trades, 4)
        self.assertEqual(relation.max_daily_commodity_volume, 25)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(relation)

    async def test_is_user_customer_delegates_to_active_relation_lookup(self):
        relation = SimpleNamespace(id=9)
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        self.assertTrue(await is_user_customer(db, 5))

    def test_round_customer_price_uses_buy_floor_and_sell_ceil(self):
        self.assertEqual(round_customer_price("49750", OfferType.BUY), 49700)
        self.assertEqual(round_customer_price("50250", OfferType.SELL), 50300)

    def test_round_customer_price_keeps_exact_hundreds_and_non_midpoints_on_the_correct_side(self):
        self.assertEqual(round_customer_price("49701", OfferType.BUY), 49700)
        self.assertEqual(round_customer_price("49799", OfferType.BUY), 49700)
        self.assertEqual(round_customer_price("50201", OfferType.SELL), 50300)
        self.assertEqual(round_customer_price("50299", OfferType.SELL), 50300)
        self.assertEqual(round_customer_price("50300", OfferType.SELL), 50300)
        self.assertEqual(round_customer_price("49700", OfferType.BUY), 49700)

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

    def test_customer_normalizer_helpers_cover_invalid_ranges(self):
        with self.assertRaises(ValueError):
            customer_relation_service._normalize_non_negative_int("bad", name="quantity")
        with self.assertRaises(ValueError):
            customer_relation_service._normalize_non_negative_int(-1, name="quantity")

        with self.assertRaises(HTTPException) as exc_info:
            customer_relation_service._normalize_customer_tier_input("bad-tier")
        self.assertEqual(exc_info.exception.detail, "سطح مشتری نامعتبر است")

        self.assertIsNone(
            customer_relation_service._normalize_customer_commission_rate(
                None,
                customer_tier=CustomerTier.TIER_2,
            )
        )
        self.assertIsNone(
            customer_relation_service._normalize_customer_commission_rate(
                "",
                customer_tier=CustomerTier.TIER_2,
            )
        )
        self.assertIsNone(
            customer_relation_service._normalize_customer_commission_rate(
                "1.5",
                customer_tier=CustomerTier.TIER_1,
            )
        )

        with self.assertRaises(HTTPException) as invalid_rate_exc:
            customer_relation_service._normalize_customer_commission_rate(
                "bad",
                customer_tier=CustomerTier.TIER_2,
            )
        self.assertEqual(invalid_rate_exc.exception.detail, "نرخ کارمزد مشتری نامعتبر است")

        with self.assertRaises(HTTPException) as range_exc:
            customer_relation_service._normalize_customer_commission_rate(
                "101",
                customer_tier=CustomerTier.TIER_2,
            )
        self.assertEqual(range_exc.exception.detail, "نرخ کارمزد مشتری باید بین ۰ تا ۱۰۰ باشد")

        self.assertIsNone(customer_relation_service._normalize_optional_customer_limit("", name="حداکثر"))
        with self.assertRaises(HTTPException):
            customer_relation_service._normalize_optional_customer_limit("bad", name="حداکثر")
        with self.assertRaises(HTTPException):
            customer_relation_service._normalize_optional_customer_limit(-1, name="حداکثر")
        with self.assertRaises(HTTPException):
            customer_relation_service._validate_customer_trade_limit_bounds(min_trade_quantity=5, max_trade_quantity=4)

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

    def test_build_customer_offer_read_model_is_additive_noop_without_customer_context(self):
        read_model = build_customer_offer_read_model(
            raw_price=65000,
            offer_type=OfferType.SELL,
        )

        self.assertEqual(read_model.raw_price, 65000)
        self.assertEqual(read_model.market_published_price, 65000)
        self.assertEqual(read_model.viewer_effective_price, 65000)
        self.assertFalse(read_model.customer_badge_visible)
        self.assertIsNone(read_model.customer_management_name)
        self.assertIsNone(read_model.customer_tier)

    async def test_load_offer_customer_read_context_reuses_viewer_relation_from_offer_owner_map(self):
        relation_one = SimpleNamespace(customer_user_id=9, owner_user_id=7, status=CustomerRelationStatus.ACTIVE)
        relation_two = SimpleNamespace(customer_user_id=12, owner_user_id=8, status=CustomerRelationStatus.ACTIVE)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[relation_one, relation_two])])

        owner_relation_map, viewer_relation = await load_offer_customer_read_context(
            db,
            offer_owner_user_ids={9, 12, 12, -1},
            viewer_user_id=9,
        )

        self.assertEqual(owner_relation_map, {9: relation_one, 12: relation_two})
        self.assertIs(viewer_relation, relation_one)

    async def test_load_offer_customer_read_context_falls_back_to_viewer_relation_lookup(self):
        owner_relation = SimpleNamespace(customer_user_id=12, owner_user_id=8, status=CustomerRelationStatus.ACTIVE)
        viewer_relation = SimpleNamespace(customer_user_id=33, owner_user_id=18, status=CustomerRelationStatus.ACTIVE)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[owner_relation]),
                FakeExecuteResult(scalar_one_value=viewer_relation),
            ]
        )

        owner_relation_map, resolved_viewer_relation = await load_offer_customer_read_context(
            db,
            offer_owner_user_ids=[12],
            viewer_user_id=33,
        )

        self.assertEqual(owner_relation_map, {12: owner_relation})
        self.assertIs(resolved_viewer_relation, viewer_relation)

    async def test_load_offer_customer_read_context_skips_invalid_owner_ids(self):
        owner_relation = SimpleNamespace(customer_user_id=9, owner_user_id=7, status=CustomerRelationStatus.ACTIVE)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[owner_relation])])

        owner_relation_map, viewer_relation = await load_offer_customer_read_context(
            db,
            offer_owner_user_ids=[None, "bad", 0, -1, 9, 9],
            viewer_user_id=None,
        )

        self.assertEqual(owner_relation_map, {9: owner_relation})
        self.assertIsNone(viewer_relation)

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

    def test_validate_customer_trade_limits_rejects_missing_relation_and_non_positive_quantity(self):
        now = utc_now().replace(tzinfo=None)
        with self.assertRaises(HTTPException) as missing_exc:
            validate_customer_trade_limits(None, quantity=5, now=now)
        self.assertEqual(missing_exc.exception.detail, "Customer relation is required")

        active_relation = SimpleNamespace(
            status=CustomerRelationStatus.ACTIVE,
            trading_restricted_until=None,
            min_trade_quantity=None,
            max_trade_quantity=None,
            max_daily_trades=None,
            max_daily_commodity_volume=None,
        )
        with self.assertRaises(HTTPException) as quantity_exc:
            validate_customer_trade_limits(active_relation, quantity=0, now=now)
        self.assertEqual(quantity_exc.exception.detail, "Trade quantity must be positive")

    async def test_build_allowed_customer_chat_targets_includes_owner_accountants_and_super_admin_without_customers(self):
        relation = SimpleNamespace(owner_user_id=7)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=relation),
                FakeExecuteResult(values=[7, 40]),
                FakeExecuteResult(values=[11, 12]),
                FakeExecuteResult(values=[]),
            ]
        )

        result = await build_allowed_customer_chat_targets(db, 9)

        self.assertEqual(result, [7, 11, 12, 40])

    async def test_build_allowed_customer_chat_targets_includes_shared_group_accountants(self):
        relation = SimpleNamespace(owner_user_id=7)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=relation),
                FakeExecuteResult(values=[7, 40]),
                FakeExecuteResult(values=[11, 12]),
                FakeExecuteResult(values=[55, 12]),
            ]
        )

        result = await build_allowed_customer_chat_targets(db, 9)

        self.assertEqual(result, [7, 11, 12, 40, 55])

    async def test_list_shared_group_accountant_ids_for_customer_returns_execute_rows(self):
        db = FakeDB(execute_results=[FakeExecuteResult(values=[44, 55])])

        result = await list_shared_group_accountant_ids_for_customer(db, 9)

        self.assertEqual(result, [44, 55])

    async def test_build_allowed_customer_chat_targets_returns_empty_without_relation(self):
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=None)])

        self.assertEqual(await build_allowed_customer_chat_targets(db, 9), [])


if __name__ == "__main__":
    unittest.main()