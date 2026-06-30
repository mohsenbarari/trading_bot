import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.services import block_service


def scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


def rows_result(values):
    result = Mock()
    result.all.return_value = values
    return result


class BlockServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        customer_relation_patcher = patch(
            "core.services.block_service.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        )
        customer_relation_patcher.start()
        self.addCleanup(customer_relation_patcher.stop)

    async def test_can_user_block_returns_not_found_when_user_missing(self):
        db = SimpleNamespace(get=AsyncMock(return_value=None), scalar=AsyncMock())

        can_block, error, status = await block_service.can_user_block(db, 10)

        self.assertFalse(can_block)
        self.assertEqual(error, "کاربر یافت نشد.")
        self.assertEqual(status, {})
        db.scalar.assert_not_awaited()

    async def test_can_user_block_returns_disabled_status(self):
        user = SimpleNamespace(can_block_users=False, max_blocked_users=5)
        db = SimpleNamespace(get=AsyncMock(return_value=user), scalar=AsyncMock())

        can_block, error, status = await block_service.can_user_block(db, 11)

        self.assertFalse(can_block)
        self.assertIn("غیرفعال", error)
        self.assertEqual(status["remaining"], 0)
        self.assertFalse(status["can_block_now"])
        self.assertEqual(status["reason_code"], block_service.BLOCK_STATUS_REASON_CAPABILITY_DISABLED)
        db.scalar.assert_not_awaited()

    async def test_can_user_block_returns_customer_delegated_status(self):
        user = SimpleNamespace(can_block_users=True, max_blocked_users=5)
        db = SimpleNamespace(get=AsyncMock(return_value=user), execute=AsyncMock(), scalar=AsyncMock())

        with patch("core.services.block_service.is_user_customer", AsyncMock(return_value=True)):
            can_block, error, status = await block_service.can_user_block(db, 11)

        self.assertFalse(can_block)
        self.assertIn("مالک", error)
        self.assertFalse(status["can_block"])
        self.assertFalse(status["can_block_now"])
        self.assertEqual(status["reason_code"], block_service.BLOCK_STATUS_REASON_CUSTOMER_DELEGATED)
        db.scalar.assert_not_awaited()

    async def test_can_user_block_returns_accountant_delegated_status(self):
        user = SimpleNamespace(can_block_users=True, max_blocked_users=5)
        db = SimpleNamespace(get=AsyncMock(return_value=user), execute=AsyncMock(), scalar=AsyncMock())

        with patch("core.services.block_service.is_user_customer", AsyncMock(return_value=False)), patch(
            "core.services.block_service.is_user_accountant", AsyncMock(return_value=True)
        ):
            can_block, error, status = await block_service.can_user_block(db, 11)

        self.assertFalse(can_block)
        self.assertIn("سرگروه", error)
        self.assertFalse(status["can_block"])
        self.assertFalse(status["can_block_now"])
        self.assertEqual(status["reason_code"], block_service.BLOCK_STATUS_REASON_ACCOUNTANT_DELEGATED)
        db.scalar.assert_not_awaited()

    async def test_can_user_block_returns_limit_reached_status(self):
        user = SimpleNamespace(can_block_users=True, max_blocked_users=2)
        db = SimpleNamespace(get=AsyncMock(return_value=user), scalar=AsyncMock(return_value=2))

        can_block, error, status = await block_service.can_user_block(db, 12)

        self.assertFalse(can_block)
        self.assertIn("حداکثر 2", error)
        self.assertEqual(status["current_blocked"], 2)
        self.assertEqual(status["remaining"], 0)
        self.assertTrue(status["can_block"])
        self.assertFalse(status["can_block_now"])
        self.assertEqual(status["reason_code"], block_service.BLOCK_STATUS_REASON_LIMIT_REACHED)

    async def test_can_user_block_returns_remaining_capacity(self):
        user = SimpleNamespace(can_block_users=True, max_blocked_users=5)
        db = SimpleNamespace(get=AsyncMock(return_value=user), scalar=AsyncMock(return_value=2))

        can_block, error, status = await block_service.can_user_block(db, 13)

        self.assertTrue(can_block)
        self.assertEqual(error, "")
        self.assertEqual(status["remaining"], 3)
        self.assertTrue(status["can_block_now"])
        self.assertIsNone(status["reason_code"])

    async def test_block_user_rejects_self_block(self):
        db = SimpleNamespace(scalar=AsyncMock(), add=Mock(), commit=AsyncMock())

        success, message = await block_service.block_user(db, 14, 14)

        self.assertFalse(success)
        self.assertIn("خودتان", message)
        db.scalar.assert_not_awaited()
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_block_user_rejects_missing_or_deleted_target(self):
        missing_db = SimpleNamespace(get=AsyncMock(return_value=None), scalar=AsyncMock(), add=Mock(), commit=AsyncMock())
        success, message = await block_service.block_user(missing_db, 15, 16)
        self.assertFalse(success)
        self.assertEqual(message, "کاربر یافت نشد.")
        missing_db.scalar.assert_not_awaited()
        missing_db.add.assert_not_called()
        missing_db.commit.assert_not_awaited()

        deleted_db = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(is_deleted=True)),
            scalar=AsyncMock(),
            add=Mock(),
            commit=AsyncMock(),
        )
        success, message = await block_service.block_user(deleted_db, 15, 17)
        self.assertFalse(success)
        self.assertEqual(message, "کاربر یافت نشد.")
        deleted_db.scalar.assert_not_awaited()
        deleted_db.add.assert_not_called()
        deleted_db.commit.assert_not_awaited()

    async def test_block_user_rejects_existing_block(self):
        db = SimpleNamespace(scalar=AsyncMock(return_value=object()), add=Mock(), commit=AsyncMock())

        success, message = await block_service.block_user(db, 15, 16)

        self.assertFalse(success)
        self.assertEqual(message, "این کاربر قبلاً مسدود شده است.")
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_block_user_respects_limit_failure(self):
        db = SimpleNamespace(scalar=AsyncMock(return_value=None), add=Mock(), commit=AsyncMock())

        with patch("core.services.block_service.can_user_block", AsyncMock(return_value=(False, "❌ محدودیت", {"remaining": 0}))):
            success, message = await block_service.block_user(db, 17, 18)

        self.assertFalse(success)
        self.assertEqual(message, "❌ محدودیت")
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_block_user_rejects_non_group_customer_target(self):
        db = SimpleNamespace(scalar=AsyncMock(return_value=None), add=Mock(), commit=AsyncMock())
        relation = SimpleNamespace(owner_user_id=70)

        with patch(
            "core.services.block_service.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=relation),
        ), patch(
            "core.services.block_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch("core.services.block_service.can_user_block", AsyncMock(return_value=(True, "", {"remaining": 2}))):
            success, message = await block_service.block_user(db, 19, 20)

        self.assertFalse(success)
        self.assertEqual(message, block_service.NON_GROUP_CUSTOMER_BLOCK_MESSAGE)
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_block_user_allows_owner_to_block_own_customer_target(self):
        db = SimpleNamespace(scalar=AsyncMock(return_value=None), add=Mock(), commit=AsyncMock())
        relation = SimpleNamespace(owner_user_id=19)

        with patch(
            "core.services.block_service.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=relation),
        ), patch("core.services.block_service.can_user_block", AsyncMock(return_value=(True, "", {"remaining": 2}))):
            success, message = await block_service.block_user(db, 19, 20)

        self.assertTrue(success)
        self.assertIn("موفقیت", message)
        new_block = db.add.call_args.args[0]
        self.assertEqual(new_block.blocker_id, 19)
        self.assertEqual(new_block.blocked_id, 20)

    async def test_block_user_creates_record_and_commits(self):
        db = SimpleNamespace(scalar=AsyncMock(return_value=None), add=Mock(), commit=AsyncMock())

        with patch("core.services.block_service.can_user_block", AsyncMock(return_value=(True, "", {"remaining": 2}))):
            success, message = await block_service.block_user(db, 19, 20)

        self.assertTrue(success)
        self.assertIn("موفقیت", message)
        db.add.assert_called_once()
        new_block = db.add.call_args.args[0]
        self.assertEqual(new_block.blocker_id, 19)
        self.assertEqual(new_block.blocked_id, 20)
        db.commit.assert_awaited_once()

    async def test_unblock_user_handles_missing_and_existing_block(self):
        missing_db = SimpleNamespace(scalar=AsyncMock(return_value=None), delete=AsyncMock(), commit=AsyncMock())
        success, message = await block_service.unblock_user(missing_db, 21, 22)
        self.assertFalse(success)
        self.assertEqual(message, "این کاربر مسدود نشده است.")
        missing_db.delete.assert_not_awaited()
        missing_db.commit.assert_not_awaited()

        block = object()
        existing_db = SimpleNamespace(scalar=AsyncMock(return_value=block), delete=AsyncMock(), commit=AsyncMock())
        success, message = await block_service.unblock_user(existing_db, 21, 22)
        self.assertTrue(success)
        self.assertIn("برداشته شد", message)
        existing_db.delete.assert_awaited_once_with(block)
        existing_db.commit.assert_awaited_once()

    async def test_is_blocked_reports_directional_blocker(self):
        db_a_blocks_b = SimpleNamespace(scalar=AsyncMock(side_effect=[object()]))
        blocked, blocker_id = await block_service.is_blocked(db_a_blocks_b, 30, 31)
        self.assertTrue(blocked)
        self.assertEqual(blocker_id, 30)

        db_b_blocks_a = SimpleNamespace(scalar=AsyncMock(side_effect=[None, object()]))
        blocked, blocker_id = await block_service.is_blocked(db_b_blocks_a, 30, 31)
        self.assertTrue(blocked)
        self.assertEqual(blocker_id, 31)

        db_none = SimpleNamespace(scalar=AsyncMock(side_effect=[None, None]))
        blocked, blocker_id = await block_service.is_blocked(db_none, 30, 31)
        self.assertFalse(blocked)
        self.assertIsNone(blocker_id)

    async def test_is_trade_blocked_by_principals_checks_customer_owner_pair(self):
        db = SimpleNamespace()
        customer_relation = SimpleNamespace(owner_user_id=90)
        block_mock = AsyncMock(side_effect=[(False, None), (True, 90)])

        with patch("core.services.block_service.is_blocked", new=block_mock):
            blocked, blocker_id, user_a_principal_id, user_b_principal_id = (
                await block_service.is_trade_blocked_by_principals(
                    db,
                    30,
                    40,
                    user_a_customer_relation=customer_relation,
                    user_b_customer_relation=None,
                )
            )

        self.assertTrue(blocked)
        self.assertEqual(blocker_id, 90)
        self.assertEqual(user_a_principal_id, 90)
        self.assertEqual(user_b_principal_id, 40)
        self.assertEqual(block_mock.await_args_list[0].args, (db, 30, 40))
        self.assertEqual(block_mock.await_args_list[1].args, (db, 30, 90))

    async def test_get_blocked_users_shapes_joined_rows(self):
        block = SimpleNamespace(created_at="2026-05-07T20:00:00")
        user = SimpleNamespace(id=41, account_name="ali", mobile_number="0912", full_name="Ali")
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            rows_result([(block, user)]),
            rows_result([(41, "مشتری علی")]),
        ]))

        result = await block_service.get_blocked_users(db, 40)

        self.assertEqual(result, [{
            "id": 41,
            "account_name": "مشتری علی",
            "mobile_number": "0912",
            "full_name": "Ali",
            "blocked_at": "2026-05-07T20:00:00",
        }])

    async def test_get_block_status_handles_missing_user_and_remaining_capacity(self):
        missing_db = SimpleNamespace(get=AsyncMock(return_value=None), scalar=AsyncMock())
        self.assertEqual(await block_service.get_block_status(missing_db, 50), {"error": "کاربر یافت نشد"})
        missing_db.scalar.assert_not_awaited()

        user = SimpleNamespace(can_block_users=True, max_blocked_users=4)
        db = SimpleNamespace(get=AsyncMock(return_value=user), scalar=AsyncMock(return_value=1))
        status = await block_service.get_block_status(db, 51)
        self.assertEqual(status["remaining"], 3)
        self.assertTrue(status["can_block"])
        self.assertTrue(status["can_block_now"])
        self.assertIsNone(status["reason_code"])

        disabled_user = SimpleNamespace(can_block_users=False, max_blocked_users=4)
        disabled_db = SimpleNamespace(get=AsyncMock(return_value=disabled_user), scalar=AsyncMock(return_value=0))
        disabled_status = await block_service.get_block_status(disabled_db, 52)
        self.assertFalse(disabled_status["can_block_now"])
        self.assertEqual(disabled_status["reason_code"], block_service.BLOCK_STATUS_REASON_CAPABILITY_DISABLED)

        customer_user = SimpleNamespace(can_block_users=True, max_blocked_users=4)
        customer_db = SimpleNamespace(get=AsyncMock(return_value=customer_user), execute=AsyncMock(), scalar=AsyncMock())
        with patch("core.services.block_service.is_user_customer", AsyncMock(return_value=True)):
            customer_status = await block_service.get_block_status(customer_db, 54)
        self.assertFalse(customer_status["can_block"])
        self.assertEqual(customer_status["reason_code"], block_service.BLOCK_STATUS_REASON_CUSTOMER_DELEGATED)
        customer_db.scalar.assert_not_awaited()

        accountant_user = SimpleNamespace(can_block_users=True, max_blocked_users=4)
        accountant_db = SimpleNamespace(get=AsyncMock(return_value=accountant_user), execute=AsyncMock(), scalar=AsyncMock())
        with patch("core.services.block_service.is_user_customer", AsyncMock(return_value=False)), patch(
            "core.services.block_service.is_user_accountant", AsyncMock(return_value=True)
        ):
            accountant_status = await block_service.get_block_status(accountant_db, 55)
        self.assertFalse(accountant_status["can_block"])
        self.assertEqual(accountant_status["reason_code"], block_service.BLOCK_STATUS_REASON_ACCOUNTANT_DELEGATED)
        accountant_db.scalar.assert_not_awaited()

        full_user = SimpleNamespace(can_block_users=True, max_blocked_users=2)
        full_db = SimpleNamespace(get=AsyncMock(return_value=full_user), scalar=AsyncMock(return_value=2))
        full_status = await block_service.get_block_status(full_db, 53)
        self.assertTrue(full_status["can_block"])
        self.assertFalse(full_status["can_block_now"])
        self.assertEqual(full_status["reason_code"], block_service.BLOCK_STATUS_REASON_LIMIT_REACHED)

    async def test_is_blocked_by_returns_boolean(self):
        db = SimpleNamespace(scalar=AsyncMock(side_effect=[object(), None]))

        self.assertTrue(await block_service.is_blocked_by(db, 60, 61))
        self.assertFalse(await block_service.is_blocked_by(db, 60, 62))

    async def test_search_users_for_block_short_query_returns_empty(self):
        db = SimpleNamespace(execute=AsyncMock())

        result = await block_service.search_users_for_block(db, "a", 70)

        self.assertEqual(result, [])
        db.execute.assert_not_awaited()

    async def test_search_users_for_block_maps_results_and_block_flags(self):
        users = [
            SimpleNamespace(id=81, account_name="ali", mobile_number="0912", full_name="Ali", is_deleted=False),
            SimpleNamespace(id=82, account_name="sara", mobile_number="0935", full_name="Sara", is_deleted=False),
        ]
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            scalars_result(users),
            rows_result([(81, "مشتری علی")]),
        ]))

        with patch("core.services.block_service.is_blocked_by", AsyncMock(side_effect=[True, False])) as is_blocked_by:
            result = await block_service.search_users_for_block(db, "09", 80)

        self.assertEqual(result, [
            {
                "id": 81,
                "account_name": "مشتری علی",
                "mobile_number": "0912",
                "full_name": "Ali",
                "is_blocked": True,
            },
            {
                "id": 82,
                "account_name": "sara",
                "mobile_number": "0935",
                "full_name": "Sara",
                "is_blocked": False,
            },
        ])
        self.assertEqual(is_blocked_by.await_count, 2)
        stmt_text = str(db.execute.await_args_list[0].args[0]).lower()
        self.assertIn("customer_relations", stmt_text)
        self.assertIn("owner_user_id", stmt_text)


if __name__ == "__main__":
    unittest.main()
