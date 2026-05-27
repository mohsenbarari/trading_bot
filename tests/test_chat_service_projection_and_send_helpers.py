import json
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from core.enums import MessageType
from core.services import chat_service


def compile_sql(statement):
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def scalar_one_or_none_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


def first_result(value):
    scalars = Mock()
    scalars.first.return_value = value
    result = Mock()
    result.scalars.return_value = scalars
    return result


def all_result(items):
    scalars = Mock()
    scalars.all.return_value = items
    result = Mock()
    result.scalars.return_value = scalars
    return result


class FakeAsyncFileContext:
    def __init__(self):
        self.handle = SimpleNamespace(write=AsyncMock())

    async def __aenter__(self):
        return self.handle

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeHttpClientContext:
    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.get = AsyncMock(side_effect=self._get)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _get(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.response


class ChatServiceProjectionAndSendHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_conversation_projection_builders(self):
        projection_stmt, unread_count, resolved_last_message_at, hidden_flag = chat_service.build_direct_conversation_projection_stmt(7)
        projection_sql = compile_sql(projection_stmt)
        scope_sql = compile_sql(
            chat_service.build_direct_conversation_scope_condition(7).self_group()
        )
        list_sql = compile_sql(chat_service.build_direct_conversation_list_stmt(7))
        unread_sql = compile_sql(chat_service.build_direct_unread_poll_stmt(7))
        legacy_sql = compile_sql(
            chat_service.build_legacy_direct_message_condition(10, 20).self_group()
        )

        self.assertIn("conversations.id AS id", projection_sql)
        self.assertIn("messages_1.message_type AS last_message_type", projection_sql)
        self.assertIn("coalesce(", projection_sql)
        self.assertIn("profile_user_id", projection_sql)
        self.assertIn("profile_account_name", projection_sql)
        self.assertIn("resolved_from_accountant_id", projection_sql)
        self.assertIn("highlight_accountant_relation_display_name", projection_sql)
        self.assertIn("accountant_relations", projection_sql)
        self.assertIn("conversations.user1_id = 7", scope_sql)
        self.assertIn("conversations.user2_id = 7", scope_sql)
        self.assertIn("DESC NULLS LAST", list_sql)
        self.assertIn("> 0", unread_sql)
        self.assertIn("is_muted", projection_sql)
        self.assertIn("greatest", projection_sql.lower())
        self.assertIn("is_pinned", projection_sql)
        self.assertIn("coalesce(", unread_sql)
        self.assertIn("messages.sender_id = 10", legacy_sql)
        self.assertIn("messages.receiver_id = 20", legacy_sql)
        self.assertIsNotNone(unread_count)
        self.assertIsNotNone(resolved_last_message_at)
        self.assertIsNotNone(hidden_flag)

    async def test_direct_conversation_lookup_and_member_helpers(self):
        existing_conversation = SimpleNamespace(id=9)
        db = SimpleNamespace(execute=AsyncMock(return_value=scalar_one_or_none_result(existing_conversation)))
        loaded = await chat_service.get_existing_direct_conversation(db, 30, 10)
        self.assertIs(loaded, existing_conversation)

        create_db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(None)),
            add=Mock(),
            flush=AsyncMock(),
        )
        created = await chat_service.get_or_create_direct_conversation(create_db, 30, 10)
        self.assertEqual(created.user1_id, 10)
        self.assertEqual(created.user2_id, 30)
        self.assertEqual(created.unread_count_user1, 0)
        self.assertEqual(created.unread_count_user2, 0)
        create_db.add.assert_called_once_with(created)
        create_db.flush.assert_awaited_once()

        message = SimpleNamespace(id=55)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[scalar_one_or_none_result(message), scalar_one_or_none_result(44), all_result([
            SimpleNamespace(user_id=10, role="member"),
            SimpleNamespace(user_id=10, role="admin"),
            SimpleNamespace(user_id=20, role="member"),
        ])]))

        with patch(
            "core.services.chat_service.build_direct_message_lookup_condition",
            new=AsyncMock(return_value=True),
        ):
            latest = await chat_service._get_latest_direct_message(db, 10, 20)

        chat_id = await chat_service._find_existing_direct_chat_id(db, 10, 20)
        members = await chat_service._load_direct_chat_members(db, 44, 10, 20)

        self.assertIs(latest, message)
        self.assertEqual(chat_id, 44)
        self.assertEqual(sorted(members.keys()), [10, 20])
        self.assertEqual(members[10].role, "member")

    async def test_location_snapshot_and_prepare_helpers(self):
        fixed_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        file_context = FakeAsyncFileContext()
        db = SimpleNamespace(add=Mock(), flush=AsyncMock())
        ok_response = SimpleNamespace(status_code=200, content=b"png-bytes")

        with patch(
            "core.services.chat_service.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=ok_response),
        ), patch(
            "core.services.chat_service.aiofiles.open",
            return_value=file_context,
        ), patch("core.services.chat_service.os.getcwd", return_value="/tmp/chat-service"), patch(
            "core.services.chat_service.os.makedirs"
        ) as makedirs, patch("core.services.chat_service.uuid.uuid4", return_value=fixed_uuid):
            file_id = await chat_service.generate_direct_location_snapshot(db, 1, 35.7, 51.4)

        self.assertEqual(file_id, str(fixed_uuid))
        makedirs.assert_called_once()
        file_context.handle.write.assert_awaited_once_with(b"png-bytes")
        self.assertEqual(db.add.call_args.args[0].mime_type, "image/png")
        db.flush.assert_awaited_once()

        with patch(
            "core.services.chat_service.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=SimpleNamespace(status_code=503, content=b"")),
        ):
            self.assertIsNone(await chat_service.generate_direct_location_snapshot(SimpleNamespace(add=Mock(), flush=AsyncMock()), 1, 35.7, 51.4))

        with patch(
            "core.services.chat_service.httpx.AsyncClient",
            return_value=FakeHttpClientContext(error=RuntimeError("tiles down")),
        ), patch.object(chat_service, "logger") as logger:
            self.assertIsNone(await chat_service.generate_direct_location_snapshot(SimpleNamespace(add=Mock(), flush=AsyncMock()), 1, 35.7, 51.4))
        logger.warning.assert_called_once()

        sender = SimpleNamespace(id=5)
        receiver = SimpleNamespace(id=6, is_deleted=False)
        db = SimpleNamespace(
            get=AsyncMock(return_value=receiver),
            execute=AsyncMock(return_value=scalar_one_or_none_result(None)),
        )
        with patch(
            "core.services.chat_service.prepare_direct_location_content",
            new=AsyncMock(return_value='{"lat": 1.0, "lng": 2.0}'),
        ) as prepare_location:
            resolved_receiver, prepared = await chat_service.prepare_direct_message_send(
                db,
                sender=sender,
                receiver_id=6,
                content='{"lat": 1, "lng": 2}',
                message_type=MessageType.LOCATION,
            )

        self.assertIs(resolved_receiver, receiver)
        self.assertEqual(prepared, '{"lat": 1.0, "lng": 2.0}')
        prepare_location.assert_awaited_once()

        with self.assertRaises(HTTPException):
            await chat_service.prepare_direct_message_send(
                SimpleNamespace(get=AsyncMock()),
                sender=sender,
                receiver_id=sender.id,
                content="hello",
                message_type=MessageType.TEXT,
            )

        with self.assertRaises(HTTPException):
            await chat_service.prepare_direct_message_send(
                SimpleNamespace(get=AsyncMock(return_value=None)),
                sender=sender,
                receiver_id=99,
                content="hello",
                message_type=MessageType.TEXT,
            )

        with self.assertRaises(HTTPException):
            await chat_service.prepare_direct_message_send(
                SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id=7, is_deleted=True))),
                sender=sender,
                receiver_id=7,
                content="hello",
                message_type=MessageType.TEXT,
            )

        with patch(
            "core.services.chat_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=SimpleNamespace(id=9)),
        ), patch(
            "core.services.chat_service._resolve_customer_direct_chat_initiation_permission",
            new=AsyncMock(return_value=False),
        ):
            resolved_receiver, prepared = await chat_service.prepare_direct_message_send(
                SimpleNamespace(
                    get=AsyncMock(return_value=receiver),
                    execute=AsyncMock(return_value=scalar_one_or_none_result(None)),
                ),
                sender=sender,
                receiver_id=6,
                content="hello",
                message_type=MessageType.TEXT,
            )
        self.assertIs(resolved_receiver, receiver)
        self.assertEqual(prepared, "hello")

        with patch(
            "core.services.chat_service.generate_direct_location_snapshot",
            new=AsyncMock(return_value="snapshot-1"),
        ):
            payload = await chat_service.prepare_direct_location_content(
                db,
                uploader_id=5,
                content=json.dumps({"latitude": "35.7", "longitude": "51.4"}),
            )
        parsed = json.loads(payload)
        self.assertEqual(parsed["lat"], 35.7)
        self.assertEqual(parsed["lng"], 51.4)
        self.assertEqual(parsed["snapshot_id"], "snapshot-1")

        with patch.object(chat_service, "logger") as logger:
            original = await chat_service.prepare_direct_location_content(
                db,
                uploader_id=5,
                content="not-json",
            )
        self.assertEqual(original, "not-json")
        logger.warning.assert_called_once()

    async def test_serialize_reload_and_persist_send_helpers(self):
        now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        message = SimpleNamespace(id=1, created_at=now)

        self.assertEqual(
            chat_service.serialize_direct_message_for_response(
                message,
                serializer=lambda item: {"id": item.id},
            ),
            {"id": 1},
        )
        self.assertEqual(
            chat_service.serialize_direct_messages_for_response(
                [message, SimpleNamespace(id=2)],
                serializer=lambda item: {"id": item.id},
            ),
            [{"id": 1}, {"id": 2}],
        )
        payload = chat_service.build_direct_message_event_payload(
            message,
            serializer=lambda item: {"created_at": item.created_at},
            sender_name="demo",
        )
        self.assertEqual(payload["sender_name"], "demo")
        self.assertTrue(payload["created_at"].startswith("2025-01-01T12:00:00"))

        serializer_named_payload = chat_service.build_direct_message_event_payload(
            message,
            serializer=lambda item: {"created_at": item.created_at, "sender_name": "relation-aware"},
        )
        self.assertEqual(serializer_named_payload["sender_name"], "relation-aware")

        reloaded = SimpleNamespace(id=9)
        db = SimpleNamespace(execute=AsyncMock(return_value=first_result(reloaded)))
        result = await chat_service.reload_direct_message(db, 9, include_sender=True)
        self.assertIs(result, reloaded)
        statement = db.execute.await_args.args[0]
        self.assertEqual(len(statement._with_options), 3)

        sender = SimpleNamespace(id=10)
        receiver = SimpleNamespace(id=20)
        final_message = SimpleNamespace(id=91, content="final")

        async def refresh_created(created_message):
            created_message.id = 91
            created_message.created_at = now

        db = SimpleNamespace(add=Mock(), commit=AsyncMock(), refresh=AsyncMock(side_effect=refresh_created))
        with patch(
            "core.services.chat_service.sync_direct_message_threading",
            new=AsyncMock(),
        ) as sync_threading, patch(
            "core.services.chat_service.reload_direct_message",
            new=AsyncMock(return_value=final_message),
        ) as reload_message:
            persisted = await chat_service.persist_sent_direct_message(
                db,
                sender=sender,
                receiver=receiver,
                content="hello",
                message_type=MessageType.TEXT,
            )

        self.assertIs(persisted, final_message)
        db.add.assert_called_once()
        self.assertEqual(db.commit.await_count, 2)
        db.refresh.assert_awaited_once()
        sync_threading.assert_awaited_once()
        reload_message.assert_awaited_once_with(db, 91)

        reply_reloaded = SimpleNamespace(id=92, created_at=now)
        final_reply_message = SimpleNamespace(id=92, content="reply")

        async def refresh_reply(created_message):
            created_message.id = 92
            created_message.created_at = now

        db = SimpleNamespace(add=Mock(), commit=AsyncMock(), refresh=AsyncMock(side_effect=refresh_reply))
        with patch(
            "core.services.chat_service.sync_direct_message_threading",
            new=AsyncMock(),
        ) as sync_threading, patch(
            "core.services.chat_service.reload_direct_message",
            new=AsyncMock(side_effect=[reply_reloaded, final_reply_message]),
        ) as reload_message:
            persisted = await chat_service.persist_sent_direct_message(
                db,
                sender=sender,
                receiver=receiver,
                content="hello",
                message_type=MessageType.TEXT,
                reply_to_message_id=7,
            )

        self.assertIs(persisted, final_reply_message)
        sync_threading.assert_awaited_once_with(
            db,
            sender=sender,
            receiver=receiver,
            message=reply_reloaded,
        )
        self.assertEqual(reload_message.await_count, 2)


if __name__ == "__main__":
    unittest.main()