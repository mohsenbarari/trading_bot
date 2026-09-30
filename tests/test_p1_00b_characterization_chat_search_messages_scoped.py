"""Direct-characterization tests for api.routers.chat.search_messages scoped customer chat_id contract.

Characterizes the scoped customer search path where chat_id is provided, validating both
the allowed-target query execution path and the disallowed-target 404 guard path.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException  # noqa: E402
from api.routers.chat import search_messages  # noqa: E402


class CharacterizeChatSearchMessagesScopedTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct-characterization tests for search_messages scoped chat_id contract."""

    @patch("api.routers.chat._serialize_direct_messages_with_accountant_contract", new_callable=AsyncMock)
    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_message_search_stmt", new_callable=AsyncMock)
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_allowed_customer_target_search_uses_scoped_query_and_preserves_order(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_message_search_stmt: AsyncMock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_serialize_direct_messages_with_accountant_contract: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=10)
        target_chat_id = 20
        allowed_targets = {20, 40, 99}
        statement_sentinel = object()

        msg1 = SimpleNamespace(id=101, sender_id=10, receiver_id=20, content="msg-1-ordered")
        msg2 = SimpleNamespace(id=102, sender_id=20, receiver_id=10, content="msg-2-ordered")
        msg3 = SimpleNamespace(id=103, sender_id=10, receiver_id=20, content="msg-3-ordered")
        db_messages = [msg1, msg2, msg3]

        async def fake_get_customer_targets(db, *, current_user):
            event_log.append(("_get_customer_visible_direct_target_ids", (db,), {"current_user": current_user}))
            return allowed_targets

        async def fake_build_stmt(db, *, current_user_id, query_text, other_user_id, limit):
            event_log.append((
                "build_direct_message_search_stmt",
                (db,),
                {
                    "current_user_id": current_user_id,
                    "query_text": query_text,
                    "other_user_id": other_user_id,
                    "limit": limit,
                },
            ))
            return statement_sentinel

        mock_scalars = Mock()

        def fake_scalars_all():
            event_log.append(("scalars_all", (), {}))
            return db_messages

        mock_scalars.all.side_effect = fake_scalars_all

        mock_result = Mock()

        def fake_result_scalars():
            event_log.append(("result_scalars", (), {}))
            return mock_scalars

        mock_result.scalars.side_effect = fake_result_scalars

        mock_db = AsyncMock()

        async def fake_db_execute(stmt):
            event_log.append(("db_execute", (stmt,), {}))
            return mock_result

        mock_db.execute.side_effect = fake_db_execute

        serialized_sentinel = [
            SimpleNamespace(id=101, content="msg-1-ordered"),
            SimpleNamespace(id=102, content="msg-2-ordered"),
            SimpleNamespace(id=103, content="msg-3-ordered"),
        ]

        async def fake_serialize(db, messages, *, viewer_user_id):
            event_log.append((
                "_serialize_direct_messages_with_accountant_contract",
                (db, messages),
                {"viewer_user_id": viewer_user_id},
            ))
            return serialized_sentinel

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets
        mock_build_direct_message_search_stmt.side_effect = fake_build_stmt
        mock_serialize_direct_messages_with_accountant_contract.side_effect = fake_serialize

        result = await search_messages(
            q="receipt-query",
            chat_id=target_chat_id,
            limit=25,
            db=mock_db,
            current_user=current_user,
        )

        self.assertIs(result, serialized_sentinel)

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_message_search_stmt.assert_awaited_once_with(
            mock_db,
            current_user_id=10,
            query_text="receipt-query",
            other_user_id=20,
            limit=25,
        )
        mock_db.execute.assert_awaited_once_with(statement_sentinel)
        mock_result.scalars.assert_called_once_with()
        mock_scalars.all.assert_called_once_with()

        mock_filter_customer_scoped_direct_target_ids.assert_not_called()
        mock_filter_customer_scoped_direct_target_ids.assert_not_awaited()

        mock_serialize_direct_messages_with_accountant_contract.assert_awaited_once_with(
            mock_db,
            [msg1, msg2, msg3],
            viewer_user_id=10,
        )

        expected_events = [
            ("_get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
            (
                "build_direct_message_search_stmt",
                (mock_db,),
                {
                    "current_user_id": 10,
                    "query_text": "receipt-query",
                    "other_user_id": 20,
                    "limit": 25,
                },
            ),
            ("db_execute", (statement_sentinel,), {}),
            ("result_scalars", (), {}),
            ("scalars_all", (), {}),
            (
                "_serialize_direct_messages_with_accountant_contract",
                (mock_db, [msg1, msg2, msg3]),
                {"viewer_user_id": 10},
            ),
        ]
        self.assertEqual(event_log, expected_events)

    @patch("api.routers.chat._serialize_direct_messages_with_accountant_contract", new_callable=AsyncMock)
    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_message_search_stmt", new_callable=AsyncMock)
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_disallowed_customer_target_returns_404_before_query(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_message_search_stmt: AsyncMock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_serialize_direct_messages_with_accountant_contract: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=10)
        disallowed_target_chat_id = 999
        allowed_targets = {20, 40}

        async def fake_get_customer_targets(db, *, current_user):
            event_log.append(("_get_customer_visible_direct_target_ids", (db,), {"current_user": current_user}))
            return allowed_targets

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets

        mock_db = AsyncMock()

        with self.assertRaises(HTTPException) as ctx:
            await search_messages(
                q="forbidden-search",
                chat_id=disallowed_target_chat_id,
                limit=15,
                db=mock_db,
                current_user=current_user,
            )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "User not found")

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_message_search_stmt.assert_not_called()
        mock_build_direct_message_search_stmt.assert_not_awaited()
        mock_db.execute.assert_not_called()
        mock_db.execute.assert_not_awaited()
        mock_filter_customer_scoped_direct_target_ids.assert_not_called()
        mock_filter_customer_scoped_direct_target_ids.assert_not_awaited()
        mock_serialize_direct_messages_with_accountant_contract.assert_not_called()
        mock_serialize_direct_messages_with_accountant_contract.assert_not_awaited()

        expected_events = [
            ("_get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
        ]
        self.assertEqual(event_log, expected_events)


if __name__ == "__main__":
    unittest.main()
