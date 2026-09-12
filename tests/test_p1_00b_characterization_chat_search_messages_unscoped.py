"""Direct-characterization tests for api.routers.chat.search_messages chat_id=None filtering and failure-ordering contract.

This is direct characterization only and does not update the executed-seed registry,
reduce the canonical blocker, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import search_messages  # noqa: E402


class CharacterizeChatSearchMessagesUnscopedTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct-characterization tests for search_messages unscoped chat_id=None contract."""

    @patch("api.routers.chat._serialize_direct_messages_with_accountant_contract", new_callable=AsyncMock)
    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_message_search_stmt", new_callable=AsyncMock)
    @patch("api.routers.chat._ensure_customer_can_access_direct_target", new_callable=AsyncMock)
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_unscoped_search_messages_success_filters_disallowed_and_preserves_order(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_ensure_customer_can_access_direct_target: AsyncMock,
        mock_build_direct_message_search_stmt: AsyncMock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_serialize_direct_messages_with_accountant_contract: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=10)
        allowed_targets = {20, 40, 99}
        statement_sentinel = object()

        msg1 = SimpleNamespace(id=101, sender_id=10, receiver_id=20, content="viewer to 20")
        msg2 = SimpleNamespace(id=102, sender_id=30, receiver_id=10, content="disallowed 30 to viewer")
        msg3 = SimpleNamespace(id=103, sender_id=40, receiver_id=10, content="allowed 40 to viewer")
        msg4 = SimpleNamespace(id=104, sender_id=10, receiver_id=20, content="viewer to 20 second")
        db_messages = [msg1, msg2, msg3, msg4]

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

        async def fake_filter_targets(db, *, current_user, target_user_ids, customer_allowed_target_ids):
            event_log.append((
                "_filter_customer_scoped_direct_target_ids",
                (db,),
                {
                    "current_user": current_user,
                    "target_user_ids": target_user_ids,
                    "customer_allowed_target_ids": customer_allowed_target_ids,
                },
            ))
            return {20, 40}

        serialized_sentinel = [SimpleNamespace(id=101), SimpleNamespace(id=103), SimpleNamespace(id=104)]

        async def fake_serialize(db, messages, *, viewer_user_id):
            event_log.append((
                "_serialize_direct_messages_with_accountant_contract",
                (db, messages),
                {"viewer_user_id": viewer_user_id},
            ))
            return serialized_sentinel

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets
        mock_build_direct_message_search_stmt.side_effect = fake_build_stmt
        mock_filter_customer_scoped_direct_target_ids.side_effect = fake_filter_targets
        mock_serialize_direct_messages_with_accountant_contract.side_effect = fake_serialize

        result = await search_messages(
            q="invoice-query",
            chat_id=None,
            limit=25,
            db=mock_db,
            current_user=current_user,
        )

        self.assertIs(result, serialized_sentinel)
        mock_ensure_customer_can_access_direct_target.assert_not_called()
        mock_ensure_customer_can_access_direct_target.assert_not_awaited()

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_message_search_stmt.assert_awaited_once_with(
            mock_db,
            current_user_id=10,
            query_text="invoice-query",
            other_user_id=None,
            limit=25,
        )
        mock_db.execute.assert_awaited_once_with(statement_sentinel)
        mock_result.scalars.assert_called_once_with()
        mock_scalars.all.assert_called_once_with()

        mock_filter_customer_scoped_direct_target_ids.assert_awaited_once_with(
            mock_db,
            current_user=current_user,
            target_user_ids={20, 30, 40},
            customer_allowed_target_ids=allowed_targets,
        )
        mock_serialize_direct_messages_with_accountant_contract.assert_awaited_once_with(
            mock_db,
            [msg1, msg3, msg4],
            viewer_user_id=10,
        )

        expected_events = [
            ("_get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
            (
                "build_direct_message_search_stmt",
                (mock_db,),
                {
                    "current_user_id": 10,
                    "query_text": "invoice-query",
                    "other_user_id": None,
                    "limit": 25,
                },
            ),
            ("db_execute", (statement_sentinel,), {}),
            ("result_scalars", (), {}),
            ("scalars_all", (), {}),
            (
                "_filter_customer_scoped_direct_target_ids",
                (mock_db,),
                {
                    "current_user": current_user,
                    "target_user_ids": {20, 30, 40},
                    "customer_allowed_target_ids": allowed_targets,
                },
            ),
            (
                "_serialize_direct_messages_with_accountant_contract",
                (mock_db, [msg1, msg3, msg4]),
                {"viewer_user_id": 10},
            ),
        ]
        self.assertEqual(event_log, expected_events)

    @patch("api.routers.chat._serialize_direct_messages_with_accountant_contract", new_callable=AsyncMock)
    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_message_search_stmt", new_callable=AsyncMock)
    @patch("api.routers.chat._ensure_customer_can_access_direct_target", new_callable=AsyncMock)
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_unscoped_search_messages_filtering_failure_propagates_before_serialization(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_ensure_customer_can_access_direct_target: AsyncMock,
        mock_build_direct_message_search_stmt: AsyncMock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_serialize_direct_messages_with_accountant_contract: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=11)
        allowed_targets = {25}
        statement_sentinel = object()
        filter_error = RuntimeError("filtering failed: sentinel-filter-error-11-unique")

        msg1 = SimpleNamespace(id=201, sender_id=11, receiver_id=25, content="msg-201")
        msg2 = SimpleNamespace(id=202, sender_id=35, receiver_id=11, content="msg-202")
        db_messages = [msg1, msg2]

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

        async def fake_filter_targets(db, *, current_user, target_user_ids, customer_allowed_target_ids):
            event_log.append((
                "_filter_customer_scoped_direct_target_ids",
                (db,),
                {
                    "current_user": current_user,
                    "target_user_ids": target_user_ids,
                    "customer_allowed_target_ids": customer_allowed_target_ids,
                },
            ))
            raise filter_error

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets
        mock_build_direct_message_search_stmt.side_effect = fake_build_stmt
        mock_filter_customer_scoped_direct_target_ids.side_effect = fake_filter_targets

        with self.assertRaises(RuntimeError) as ctx:
            await search_messages(
                q="query-filter-fail",
                chat_id=None,
                limit=10,
                db=mock_db,
                current_user=current_user,
            )

        self.assertIs(ctx.exception, filter_error)
        mock_ensure_customer_can_access_direct_target.assert_not_called()
        mock_ensure_customer_can_access_direct_target.assert_not_awaited()

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_message_search_stmt.assert_awaited_once_with(
            mock_db,
            current_user_id=11,
            query_text="query-filter-fail",
            other_user_id=None,
            limit=10,
        )
        mock_db.execute.assert_awaited_once_with(statement_sentinel)
        mock_result.scalars.assert_called_once_with()
        mock_scalars.all.assert_called_once_with()

        mock_filter_customer_scoped_direct_target_ids.assert_awaited_once_with(
            mock_db,
            current_user=current_user,
            target_user_ids={25, 35},
            customer_allowed_target_ids=allowed_targets,
        )
        mock_serialize_direct_messages_with_accountant_contract.assert_not_called()
        mock_serialize_direct_messages_with_accountant_contract.assert_not_awaited()

        expected_events = [
            ("_get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
            (
                "build_direct_message_search_stmt",
                (mock_db,),
                {
                    "current_user_id": 11,
                    "query_text": "query-filter-fail",
                    "other_user_id": None,
                    "limit": 10,
                },
            ),
            ("db_execute", (statement_sentinel,), {}),
            ("result_scalars", (), {}),
            ("scalars_all", (), {}),
            (
                "_filter_customer_scoped_direct_target_ids",
                (mock_db,),
                {
                    "current_user": current_user,
                    "target_user_ids": {25, 35},
                    "customer_allowed_target_ids": allowed_targets,
                },
            ),
        ]
        self.assertEqual(event_log, expected_events)

    @patch("api.routers.chat._serialize_direct_messages_with_accountant_contract", new_callable=AsyncMock)
    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_message_search_stmt", new_callable=AsyncMock)
    @patch("api.routers.chat._ensure_customer_can_access_direct_target", new_callable=AsyncMock)
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_unscoped_search_messages_serialization_failure_propagates_after_filtering(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_ensure_customer_can_access_direct_target: AsyncMock,
        mock_build_direct_message_search_stmt: AsyncMock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_serialize_direct_messages_with_accountant_contract: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=12)
        allowed_targets = {50}
        statement_sentinel = object()
        serialize_error = ValueError("serialization failed: sentinel-serialize-error-12-unique")

        msg1 = SimpleNamespace(id=301, sender_id=12, receiver_id=50, content="msg-301")
        msg2 = SimpleNamespace(id=302, sender_id=60, receiver_id=12, content="msg-302")
        msg3 = SimpleNamespace(id=303, sender_id=50, receiver_id=12, content="msg-303")
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

        async def fake_filter_targets(db, *, current_user, target_user_ids, customer_allowed_target_ids):
            event_log.append((
                "_filter_customer_scoped_direct_target_ids",
                (db,),
                {
                    "current_user": current_user,
                    "target_user_ids": target_user_ids,
                    "customer_allowed_target_ids": customer_allowed_target_ids,
                },
            ))
            return {50}

        async def fake_serialize(db, messages, *, viewer_user_id):
            event_log.append((
                "_serialize_direct_messages_with_accountant_contract",
                (db, messages),
                {"viewer_user_id": viewer_user_id},
            ))
            raise serialize_error

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets
        mock_build_direct_message_search_stmt.side_effect = fake_build_stmt
        mock_filter_customer_scoped_direct_target_ids.side_effect = fake_filter_targets
        mock_serialize_direct_messages_with_accountant_contract.side_effect = fake_serialize

        with self.assertRaises(ValueError) as ctx:
            await search_messages(
                q="query-serialize-fail",
                chat_id=None,
                limit=30,
                db=mock_db,
                current_user=current_user,
            )

        self.assertIs(ctx.exception, serialize_error)
        mock_ensure_customer_can_access_direct_target.assert_not_called()
        mock_ensure_customer_can_access_direct_target.assert_not_awaited()

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_message_search_stmt.assert_awaited_once_with(
            mock_db,
            current_user_id=12,
            query_text="query-serialize-fail",
            other_user_id=None,
            limit=30,
        )
        mock_db.execute.assert_awaited_once_with(statement_sentinel)
        mock_result.scalars.assert_called_once_with()
        mock_scalars.all.assert_called_once_with()

        mock_filter_customer_scoped_direct_target_ids.assert_awaited_once_with(
            mock_db,
            current_user=current_user,
            target_user_ids={50, 60},
            customer_allowed_target_ids=allowed_targets,
        )
        mock_serialize_direct_messages_with_accountant_contract.assert_awaited_once_with(
            mock_db,
            [msg1, msg3],
            viewer_user_id=12,
        )

        expected_events = [
            ("_get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
            (
                "build_direct_message_search_stmt",
                (mock_db,),
                {
                    "current_user_id": 12,
                    "query_text": "query-serialize-fail",
                    "other_user_id": None,
                    "limit": 30,
                },
            ),
            ("db_execute", (statement_sentinel,), {}),
            ("result_scalars", (), {}),
            ("scalars_all", (), {}),
            (
                "_filter_customer_scoped_direct_target_ids",
                (mock_db,),
                {
                    "current_user": current_user,
                    "target_user_ids": {50, 60},
                    "customer_allowed_target_ids": allowed_targets,
                },
            ),
            (
                "_serialize_direct_messages_with_accountant_contract",
                (mock_db, [msg1, msg3]),
                {"viewer_user_id": 12},
            ),
        ]
        self.assertEqual(event_log, expected_events)


if __name__ == "__main__":
    unittest.main()
