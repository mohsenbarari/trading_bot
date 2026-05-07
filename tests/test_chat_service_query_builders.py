import unittest
from unittest.mock import AsyncMock, patch

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from core.services.chat_service import (
    build_direct_message_history_statements,
    build_direct_message_lookup_condition,
    build_direct_message_read_options,
    build_direct_message_search_stmt,
)


def compile_sql(statement):
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class ChatServiceQueryBuilderTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_condition_falls_back_to_legacy_pair_when_no_direct_chat_exists(self):
        db = object()

        with patch("core.services.chat_service._find_existing_direct_chat_id", new=AsyncMock(return_value=None)):
            condition = await build_direct_message_lookup_condition(db, 10, 20)

        condition_sql = str(condition.compile(dialect=postgresql.dialect()))
        self.assertIn("messages.sender_id", condition_sql)
        self.assertIn("messages.receiver_id", condition_sql)
        self.assertNotIn("messages.chat_id", condition_sql)

    async def test_lookup_condition_bridges_chat_id_with_legacy_pair_when_direct_chat_exists(self):
        db = object()

        with patch("core.services.chat_service._find_existing_direct_chat_id", new=AsyncMock(return_value=77)):
            condition = await build_direct_message_lookup_condition(db, 10, 20)

        condition_sql = compile_sql(condition)
        self.assertIn("messages.chat_id = 77", condition_sql)
        self.assertIn("messages.sender_id", condition_sql)
        self.assertIn("messages.receiver_id", condition_sql)

    def test_build_direct_message_read_options_honors_include_sender_flag(self):
        with_sender = build_direct_message_read_options(include_sender=True)
        without_sender = build_direct_message_read_options(include_sender=False)

        self.assertEqual(len(with_sender), 3)
        self.assertEqual(len(without_sender), 2)

    async def test_build_direct_message_search_stmt_without_other_user_keeps_global_scope(self):
        db = object()

        with patch("core.services.chat_service.build_direct_message_lookup_condition", new=AsyncMock()) as lookup_mock:
            stmt = await build_direct_message_search_stmt(
                db,
                current_user_id=10,
                query_text="hello",
                other_user_id=None,
                limit=25,
            )

        lookup_mock.assert_not_awaited()
        sql = compile_sql(stmt)
        self.assertIn("ILIKE '%%hello%%'", sql)
        self.assertIn("LIMIT 25", sql)
        self.assertIn("messages.sender_id = 10", sql)
        self.assertIn("messages.receiver_id = 10", sql)

    async def test_build_direct_message_search_stmt_with_other_user_adds_lookup_bridge(self):
        db = object()
        fake_condition = sa.text("1 = 1")

        with patch(
            "core.services.chat_service.build_direct_message_lookup_condition",
            new=AsyncMock(return_value=fake_condition),
        ) as lookup_mock:
            stmt = await build_direct_message_search_stmt(
                db,
                current_user_id=10,
                query_text="hello",
                other_user_id=20,
                limit=10,
            )

        lookup_mock.assert_awaited_once_with(db, 10, 20)
        self.assertIn("1 = 1", compile_sql(stmt))

    async def test_build_direct_message_history_statements_supports_before_id_pagination(self):
        db = object()
        fake_condition = sa.text("1 = 1")

        with patch(
            "core.services.chat_service.build_direct_message_lookup_condition",
            new=AsyncMock(return_value=fake_condition),
        ):
            stmt, stmt_newer = await build_direct_message_history_statements(
                db,
                current_user_id=10,
                other_user_id=20,
                limit=40,
                before_id=500,
            )

        self.assertIsNone(stmt_newer)
        sql = compile_sql(stmt)
        self.assertIn("1 = 1", sql)
        self.assertIn("messages.id < 500", sql)
        self.assertIn("LIMIT 40", sql)
        self.assertIn("ORDER BY messages.created_at DESC", sql)

    async def test_build_direct_message_history_statements_split_around_message_window(self):
        db = object()
        fake_condition = sa.text("1 = 1")

        with patch(
            "core.services.chat_service.build_direct_message_lookup_condition",
            new=AsyncMock(return_value=fake_condition),
        ):
            stmt_older, stmt_newer = await build_direct_message_history_statements(
                db,
                current_user_id=10,
                other_user_id=20,
                limit=10,
                around_id=700,
            )

        self.assertIsNotNone(stmt_newer)
        older_sql = compile_sql(stmt_older)
        newer_sql = compile_sql(stmt_newer)
        self.assertIn("1 = 1", older_sql)
        self.assertIn("1 = 1", newer_sql)
        self.assertIn("messages.id < 700", older_sql)
        self.assertIn("LIMIT 5", older_sql)
        self.assertIn("messages.id >= 700", newer_sql)
        self.assertIn("LIMIT 6", newer_sql)
        self.assertIn("ORDER BY messages.created_at ASC", newer_sql)


if __name__ == "__main__":
    unittest.main()