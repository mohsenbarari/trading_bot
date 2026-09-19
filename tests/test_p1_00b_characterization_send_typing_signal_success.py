"""Characterization tests for send_typing_signal success path.

Seed: API-MSG-POST-CHAT-SEND-TYPING-SIGNAL-L918 (api.routers.chat.send_typing_signal).
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md.
Assignment: WA-P1-00B-FREEZE-20260904-001.

Stage lifecycle state: P1-00 COMPLETE / P1-00b IN_PROGRESS / BLOCKED_EXPLICIT.

Evidence limits:
These tests characterize current success behavior for the send_typing_signal
endpoint strictly at handler boundaries as a recovery-mandated singleton. Scope
ends at mocked permission, sender-name resolution, and publication function
boundaries, alongside a patched publish_user_event callback used as an identity-bearing
reference. Underlying authorization policy decisions, database queries, Redis pub/sub
delivery, network transports, and FastAPI dependency injection mechanics are mocked
rather than executed or claimed. This module records current baseline behavior without
claiming seed closure, durable rollback, real publication, Stage acceptance, or tests
already run.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.chat import send_typing_signal
from api.routers.chat_schemas import TypingSignal
from models.user import User

fence = install_p1_00_network_fence()


class SendTypingSignalSuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_typing_signal_success_preserves_permission_name_and_publication_contract(self):
        receiver_id = 104
        sender_id = 204
        db = object()
        current_user = User(
            id=sender_id,
            account_name="account_sender_204",
            mobile_number="+989120000204",
            normalized_account_name="account_sender_204",
            normalized_mobile_number="+989120000204",
            full_name="Sender Full Name 204",
            address="Tehran, Iran",
        )
        typing_data = TypingSignal(receiver_id=receiver_id)

        resolved_sender_name = "resolved_display_name_204"
        self.assertNotEqual(current_user.account_name, resolved_sender_name)
        self.assertNotEqual(current_user.full_name, resolved_sender_name)
        self.assertNotEqual(current_user.account_name, current_user.full_name)

        awaited_events: list[str] = []

        async def fake_ensure_customer_access(*args, **kwargs):
            awaited_events.append("permission")
            return None

        async def fake_resolve_name(*args, **kwargs):
            awaited_events.append("resolve_name")
            return resolved_sender_name

        async def fake_publish_typing(*args, **kwargs):
            awaited_events.append("publish_typing")
            return None

        permission_mock = AsyncMock(side_effect=fake_ensure_customer_access)
        resolver_mock = AsyncMock(side_effect=fake_resolve_name)
        typing_mock = AsyncMock(side_effect=fake_publish_typing)
        publisher_mock = AsyncMock(name="publish_user_event")

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
            denied_before = len(fence["denied_connects"])
            redis_before = len(fence["live_redis_constructs"])

            result = await send_typing_signal(
                data=typing_data,
                current_user=current_user,
                db=db,
            )

            self.assertIsNone(result)
            self.assertEqual(len(fence["denied_connects"]), denied_before)
            self.assertEqual(len(fence["live_redis_constructs"]), redis_before)

        self.assertEqual(awaited_events, ["permission", "resolve_name", "publish_typing"])

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

        self.assertEqual(typing_mock.call_count, 1)
        self.assertEqual(typing_mock.await_count, 1)
        typing_mock.assert_called_once_with(
            receiver_id=receiver_id,
            sender_id=sender_id,
            sender_name=resolved_sender_name,
            publisher=publisher_mock,
        )
        typing_mock.assert_awaited_once_with(
            receiver_id=receiver_id,
            sender_id=sender_id,
            sender_name=resolved_sender_name,
            publisher=publisher_mock,
        )
        self.assertEqual(typing_mock.call_args.kwargs["receiver_id"], receiver_id)
        self.assertEqual(typing_mock.call_args.kwargs["sender_id"], sender_id)
        self.assertEqual(typing_mock.call_args.kwargs["sender_name"], resolved_sender_name)
        self.assertIs(typing_mock.call_args.kwargs["publisher"], publisher_mock)
        self.assertEqual(typing_mock.await_args.kwargs["receiver_id"], receiver_id)
        self.assertEqual(typing_mock.await_args.kwargs["sender_id"], sender_id)
        self.assertEqual(typing_mock.await_args.kwargs["sender_name"], resolved_sender_name)
        self.assertIs(typing_mock.await_args.kwargs["publisher"], publisher_mock)

        publisher_mock.assert_not_called()
        publisher_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
