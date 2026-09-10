"""Characterization tests for send_typing_signal failure branches.

Seed: API-MSG-POST-CHAT-SEND-TYPING-SIGNAL-L918 (api.routers.chat.send_typing_signal).
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md.
Assignment: WA-P1-00B-FREEZE-20260904-001.

Stage lifecycle state: P1-00 COMPLETE / P1-00b IN_PROGRESS / BLOCKED_EXPLICIT.

Evidence limits:
These tests characterize current failure behavior for the send_typing_signal
endpoint strictly at handler boundaries. Scope ends at mocked permission,
sender-name resolution, and publication function boundaries, alongside a patched
publish_user_event callback used as an identity-bearing reference. Underlying
authorization policy decisions, database queries, Redis pub/sub delivery, network
transports, and FastAPI dependency injection mechanics are mocked rather than
executed or claimed. This module records current baseline behavior without claiming
seed closure, durable rollback, real publication, Stage acceptance, or tests already run.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.chat import send_typing_signal
from api.routers.chat_schemas import TypingSignal


class SendTypingSignalFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_typing_signal_permission_rejection_propagates_and_short_circuits(self):
        receiver_id = 101
        sender_id = 201
        db = object()
        current_user = SimpleNamespace(id=sender_id, account_name="user_sender_201")
        typing_data = TypingSignal(receiver_id=receiver_id)

        rejection_exc = HTTPException(status_code=404, detail="User not found")
        awaited_events: list[str] = []

        async def fake_ensure_customer_access(*args, **kwargs):
            awaited_events.append("permission")
            raise rejection_exc

        async def fake_resolve_name(*args, **kwargs):
            awaited_events.append("resolve_name")
            return "unexpected_name"

        async def fake_publish_typing(*args, **kwargs):
            awaited_events.append("publish_typing")
            return None

        async def fake_publish_user_event(*args, **kwargs):
            awaited_events.append("publish_user_event")
            return None

        permission_mock = AsyncMock(side_effect=fake_ensure_customer_access)
        resolver_mock = AsyncMock(side_effect=fake_resolve_name)
        typing_mock = AsyncMock(side_effect=fake_publish_typing)
        publisher_mock = AsyncMock(side_effect=fake_publish_user_event)

        with patch(
            "api.routers.chat._ensure_customer_can_access_direct_target",
            new=permission_mock,
        ), patch(
            "api.routers.chat.resolve_direct_sender_display_name",
            new=resolver_mock,
        ), patch(
            "api.routers.chat.publish_direct_typing_event",
            new=typing_mock,
        ), patch(
            "api.routers.chat.publish_user_event",
            new=publisher_mock,
        ):
            with self.assertRaises(HTTPException) as cm:
                await send_typing_signal(
                    data=typing_data,
                    current_user=current_user,
                    db=db,
                )

        self.assertIs(cm.exception, rejection_exc)
        self.assertEqual(cm.exception.status_code, 404)
        self.assertEqual(cm.exception.detail, "User not found")

        self.assertEqual(awaited_events, ["permission"])

        self.assertEqual(permission_mock.call_count, 1)
        self.assertEqual(permission_mock.await_count, 1)
        permission_mock.assert_called_once_with(
            db,
            current_user=current_user,
            target_user_id=receiver_id,
        )
        permission_mock.assert_awaited_once_with(
            db,
            current_user=current_user,
            target_user_id=receiver_id,
        )
        self.assertIs(permission_mock.call_args.args[0], db)
        self.assertIs(permission_mock.call_args.kwargs["current_user"], current_user)
        self.assertEqual(permission_mock.call_args.kwargs["target_user_id"], receiver_id)
        self.assertIs(permission_mock.await_args.args[0], db)
        self.assertIs(permission_mock.await_args.kwargs["current_user"], current_user)
        self.assertEqual(permission_mock.await_args.kwargs["target_user_id"], receiver_id)

        resolver_mock.assert_not_called()
        resolver_mock.assert_not_awaited()
        typing_mock.assert_not_called()
        typing_mock.assert_not_awaited()
        publisher_mock.assert_not_called()
        publisher_mock.assert_not_awaited()

    async def test_send_typing_signal_name_resolution_failure_propagates_and_prevents_publication(self):
        receiver_id = 102
        sender_id = 202
        db = object()
        current_user = SimpleNamespace(id=sender_id, account_name="user_sender_202")
        typing_data = TypingSignal(receiver_id=receiver_id)

        resolution_exc = RuntimeError("synthetic name resolution failure")
        awaited_events: list[str] = []

        async def fake_ensure_customer_access(*args, **kwargs):
            awaited_events.append("permission")
            return None

        async def fake_resolve_name(*args, **kwargs):
            awaited_events.append("resolve_name")
            raise resolution_exc

        async def fake_publish_typing(*args, **kwargs):
            awaited_events.append("publish_typing")
            return None

        async def fake_publish_user_event(*args, **kwargs):
            awaited_events.append("publish_user_event")
            return None

        permission_mock = AsyncMock(side_effect=fake_ensure_customer_access)
        resolver_mock = AsyncMock(side_effect=fake_resolve_name)
        typing_mock = AsyncMock(side_effect=fake_publish_typing)
        publisher_mock = AsyncMock(side_effect=fake_publish_user_event)

        with patch(
            "api.routers.chat._ensure_customer_can_access_direct_target",
            new=permission_mock,
        ), patch(
            "api.routers.chat.resolve_direct_sender_display_name",
            new=resolver_mock,
        ), patch(
            "api.routers.chat.publish_direct_typing_event",
            new=typing_mock,
        ), patch(
            "api.routers.chat.publish_user_event",
            new=publisher_mock,
        ):
            with self.assertRaises(RuntimeError) as cm:
                await send_typing_signal(
                    data=typing_data,
                    current_user=current_user,
                    db=db,
                )

        self.assertIs(cm.exception, resolution_exc)

        self.assertEqual(awaited_events, ["permission", "resolve_name"])

        self.assertEqual(permission_mock.call_count, 1)
        self.assertEqual(permission_mock.await_count, 1)
        permission_mock.assert_called_once_with(
            db,
            current_user=current_user,
            target_user_id=receiver_id,
        )
        permission_mock.assert_awaited_once_with(
            db,
            current_user=current_user,
            target_user_id=receiver_id,
        )
        self.assertIs(permission_mock.call_args.args[0], db)
        self.assertIs(permission_mock.call_args.kwargs["current_user"], current_user)
        self.assertEqual(permission_mock.call_args.kwargs["target_user_id"], receiver_id)
        self.assertIs(permission_mock.await_args.args[0], db)
        self.assertIs(permission_mock.await_args.kwargs["current_user"], current_user)
        self.assertEqual(permission_mock.await_args.kwargs["target_user_id"], receiver_id)

        self.assertEqual(resolver_mock.call_count, 1)
        self.assertEqual(resolver_mock.await_count, 1)
        resolver_mock.assert_called_once_with(
            db,
            user=current_user,
        )
        resolver_mock.assert_awaited_once_with(
            db,
            user=current_user,
        )
        self.assertIs(resolver_mock.call_args.args[0], db)
        self.assertIs(resolver_mock.call_args.kwargs["user"], current_user)
        self.assertIs(resolver_mock.await_args.args[0], db)
        self.assertIs(resolver_mock.await_args.kwargs["user"], current_user)

        typing_mock.assert_not_called()
        typing_mock.assert_not_awaited()
        publisher_mock.assert_not_called()
        publisher_mock.assert_not_awaited()

    async def test_send_typing_signal_publication_failure_propagates_without_retries(self):
        receiver_id = 103
        sender_id = 203
        db = object()
        current_user = SimpleNamespace(id=sender_id, account_name="user_sender_203")
        typing_data = TypingSignal(receiver_id=receiver_id)

        resolved_sender_name = "synthetic_display_name_distinct_203"
        self.assertNotEqual(resolved_sender_name, current_user.account_name)

        publication_exc = RuntimeError("synthetic typing publication failure")
        awaited_events: list[str] = []

        async def fake_ensure_customer_access(*args, **kwargs):
            awaited_events.append("permission")
            return None

        async def fake_resolve_name(*args, **kwargs):
            awaited_events.append("resolve_name")
            return resolved_sender_name

        async def fake_publish_typing(*args, **kwargs):
            awaited_events.append("publish_typing")
            raise publication_exc

        async def fake_publish_user_event(*args, **kwargs):
            awaited_events.append("publish_user_event")
            return None

        permission_mock = AsyncMock(side_effect=fake_ensure_customer_access)
        resolver_mock = AsyncMock(side_effect=fake_resolve_name)
        typing_mock = AsyncMock(side_effect=fake_publish_typing)
        publisher_mock = AsyncMock(side_effect=fake_publish_user_event)

        with patch(
            "api.routers.chat._ensure_customer_can_access_direct_target",
            new=permission_mock,
        ), patch(
            "api.routers.chat.resolve_direct_sender_display_name",
            new=resolver_mock,
        ), patch(
            "api.routers.chat.publish_direct_typing_event",
            new=typing_mock,
        ), patch(
            "api.routers.chat.publish_user_event",
            new=publisher_mock,
        ):
            with self.assertRaises(RuntimeError) as cm:
                await send_typing_signal(
                    data=typing_data,
                    current_user=current_user,
                    db=db,
                )

        self.assertIs(cm.exception, publication_exc)

        self.assertEqual(awaited_events, ["permission", "resolve_name", "publish_typing"])

        permission_mock.assert_awaited_once_with(
            db,
            current_user=current_user,
            target_user_id=receiver_id,
        )
        self.assertIs(permission_mock.await_args.args[0], db)
        self.assertIs(permission_mock.await_args.kwargs["current_user"], current_user)
        self.assertEqual(permission_mock.await_args.kwargs["target_user_id"], receiver_id)

        resolver_mock.assert_awaited_once_with(
            db,
            user=current_user,
        )
        self.assertIs(resolver_mock.await_args.args[0], db)
        self.assertIs(resolver_mock.await_args.kwargs["user"], current_user)

        typing_mock.assert_awaited_once_with(
            receiver_id=receiver_id,
            sender_id=sender_id,
            sender_name=resolved_sender_name,
            publisher=publisher_mock,
        )
        self.assertEqual(typing_mock.await_args.kwargs["receiver_id"], receiver_id)
        self.assertEqual(typing_mock.await_args.kwargs["sender_id"], sender_id)
        self.assertEqual(typing_mock.await_args.kwargs["sender_name"], resolved_sender_name)
        self.assertIs(typing_mock.await_args.kwargs["publisher"], publisher_mock)

        self.assertEqual(permission_mock.call_count, 1)
        self.assertEqual(permission_mock.await_count, 1)
        self.assertEqual(resolver_mock.call_count, 1)
        self.assertEqual(resolver_mock.await_count, 1)
        self.assertEqual(typing_mock.call_count, 1)
        self.assertEqual(typing_mock.await_count, 1)

        publisher_mock.assert_not_called()
        publisher_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
