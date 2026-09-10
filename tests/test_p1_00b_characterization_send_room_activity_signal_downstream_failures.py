"""Downstream failure characterization tests for send_room_activity_signal boundary.

Seed: API-MSG-POST-CHAT-SEND-ROOM-ACTIVITY-SIGNAL-L962
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001 (/root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md)

Lifecycle status:
- P1-00: COMPLETE
- P1-00b: IN_PROGRESS
- Tracked HIGH seed-handler gap: BLOCKED_EXPLICIT

Evidence limits:
This unit characterizes three downstream failure boundaries of
api.routers.chat.send_room_activity_signal:
1. sender-name resolution failure after successful membership verification,
2. member listing failure after successful sender-name resolution, and
3. room activity publication failure after successful member listing,
exercised across both GROUP and CHANNEL room types.
All real execution ends strictly at the seven patched handler dependency boundaries
(room lookup, group membership guard, channel membership guard, sender-name resolution,
member listing, room activity publication, and publish_user_event callback).
Evidence stops at mocked handler boundaries and does not establish service policy,
persistence, delivery, retries of transport, durable rollback, seed closure or Stage
acceptance. P1-00 COMPLETE, P1-00b IN_PROGRESS, and gap BLOCKED_EXPLICIT remain unchanged.
Preserve prior coverage and runtime behavior.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import inspect
from types import SimpleNamespace
from typing import Any, Iterator
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.chat import send_room_activity_signal
from api.routers.chat_schemas import RoomChatActivitySignal
from core.enums import ChatType


class SyntheticSenderResolutionError(RuntimeError):
    """Synthetic exception for sender-name resolution failure characterization."""


class SyntheticMemberListingError(RuntimeError):
    """Synthetic exception for member-listing failure characterization."""


class SyntheticPublicationError(RuntimeError):
    """Synthetic exception for publication failure characterization."""


@dataclass
class ActivitySignalCollaboratorMocks:
    get_room_or_404: AsyncMock
    get_active_group_member_or_403: AsyncMock
    get_active_channel_member_or_403: AsyncMock
    resolve_relation_aware_sender_display_name: AsyncMock
    list_active_room_member_user_ids: AsyncMock
    publish_room_activity_event: AsyncMock
    publish_user_event: AsyncMock
    await_order: list[str]
    completed_awaits: list[str]
    await_trace: list[tuple[str, tuple[Any, ...], dict[str, Any]]]


@contextmanager
def _patch_activity_boundaries(
    *,
    room_side_effect: Any = None,
    room_return: Any = None,
    group_member_side_effect: Any = None,
    group_member_return: Any = None,
    channel_member_side_effect: Any = None,
    channel_member_return: Any = None,
    sender_name_side_effect: Any = None,
    sender_name_return: Any = None,
    member_user_ids_side_effect: Any = None,
    member_user_ids_return: Any = None,
    publish_room_side_effect: Any = None,
    publish_room_return: Any = None,
    publish_user_event_side_effect: Any = None,
    publish_user_event_return: Any = None,
) -> Iterator[ActivitySignalCollaboratorMocks]:
    await_order: list[str] = []
    completed_awaits: list[str] = []
    await_trace: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _make_traced_mock(
        name: str,
        *,
        return_value: Any = None,
        side_effect: Any = None,
    ) -> AsyncMock:
        mock = AsyncMock()

        async def _traced_action(*args: Any, **kwargs: Any) -> Any:
            await_order.append(name)
            await_trace.append((name, args, kwargs))
            if isinstance(side_effect, BaseException):
                raise side_effect
            if callable(side_effect):
                result = side_effect(*args, **kwargs)
                if inspect.iscoroutine(result):
                    result = await result
                completed_awaits.append(name)
                return result
            completed_awaits.append(name)
            return return_value

        mock.side_effect = _traced_action
        return mock

    room_mock = _make_traced_mock(
        "get_room_or_404",
        return_value=room_return,
        side_effect=room_side_effect,
    )
    group_member_mock = _make_traced_mock(
        "get_active_group_member_or_403",
        return_value=group_member_return,
        side_effect=group_member_side_effect,
    )
    channel_member_mock = _make_traced_mock(
        "get_active_channel_member_or_403",
        return_value=channel_member_return,
        side_effect=channel_member_side_effect,
    )
    sender_name_mock = _make_traced_mock(
        "resolve_relation_aware_sender_display_name",
        return_value=sender_name_return,
        side_effect=sender_name_side_effect,
    )
    list_members_mock = _make_traced_mock(
        "list_active_room_member_user_ids",
        return_value=member_user_ids_return,
        side_effect=member_user_ids_side_effect,
    )
    publish_room_mock = _make_traced_mock(
        "publish_room_activity_event",
        return_value=publish_room_return,
        side_effect=publish_room_side_effect,
    )
    publish_user_event_mock = _make_traced_mock(
        "publish_user_event",
        return_value=publish_user_event_return,
        side_effect=publish_user_event_side_effect,
    )

    with ExitStack() as stack:
        stack.enter_context(patch("api.routers.chat.get_room_or_404", new=room_mock))
        stack.enter_context(patch("api.routers.chat.get_active_group_member_or_403", new=group_member_mock))
        stack.enter_context(patch("api.routers.chat.get_active_channel_member_or_403", new=channel_member_mock))
        stack.enter_context(
            patch("api.routers.chat.resolve_relation_aware_sender_display_name", new=sender_name_mock)
        )
        stack.enter_context(patch("api.routers.chat.list_active_room_member_user_ids", new=list_members_mock))
        stack.enter_context(patch("api.routers.chat.publish_room_activity_event", new=publish_room_mock))
        stack.enter_context(patch("api.routers.chat.publish_user_event", new=publish_user_event_mock))
        yield ActivitySignalCollaboratorMocks(
            get_room_or_404=room_mock,
            get_active_group_member_or_403=group_member_mock,
            get_active_channel_member_or_403=channel_member_mock,
            resolve_relation_aware_sender_display_name=sender_name_mock,
            list_active_room_member_user_ids=list_members_mock,
            publish_room_activity_event=publish_room_mock,
            publish_user_event=publish_user_event_mock,
            await_order=await_order,
            completed_awaits=completed_awaits,
            await_trace=await_trace,
        )


class SendRoomActivitySignalDownstreamFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_sender_name_resolution_failure(self, chat_type: ChatType) -> None:
        requested_chat_id = 7001
        returned_chat_id = 8002
        self.assertNotEqual(requested_chat_id, returned_chat_id)

        returned_chat = SimpleNamespace(id=returned_chat_id, type=chat_type)
        current_user = SimpleNamespace(id=101, account_name="user_101")
        db = object()

        activity_payload = RoomChatActivitySignal(activity="typing", active=True)
        synthetic_exc = SyntheticSenderResolutionError("Synthetic sender resolution failure")

        with _patch_activity_boundaries(
            room_return=returned_chat,
            sender_name_side_effect=synthetic_exc,
        ) as mocks:
            with self.assertRaises(SyntheticSenderResolutionError) as exc_info:
                await send_room_activity_signal(
                    chat_id=requested_chat_id,
                    data=activity_payload,
                    current_user=current_user,
                    db=db,
                )

            self.assertIs(exc_info.exception, synthetic_exc)

            mocks.get_room_or_404.assert_called_once_with(db, requested_chat_id)
            mocks.get_room_or_404.assert_awaited_once_with(db, requested_chat_id)

            if chat_type == ChatType.GROUP:
                mocks.get_active_group_member_or_403.assert_called_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                mocks.get_active_group_member_or_403.assert_awaited_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                self.assertIs(mocks.get_active_group_member_or_403.call_args.kwargs["chat"], returned_chat)
                self.assertEqual(mocks.get_active_group_member_or_403.call_args.kwargs["chat"].id, returned_chat_id)
                self.assertNotEqual(mocks.get_active_group_member_or_403.call_args.kwargs["chat"].id, requested_chat_id)
                self.assertEqual(mocks.get_active_group_member_or_403.call_args.kwargs["user_id"], current_user.id)

                mocks.get_active_channel_member_or_403.assert_not_called()
                mocks.get_active_channel_member_or_403.assert_not_awaited()
            else:
                mocks.get_active_channel_member_or_403.assert_called_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                mocks.get_active_channel_member_or_403.assert_awaited_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                self.assertIs(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"], returned_chat)
                self.assertEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"].id, returned_chat_id)
                self.assertNotEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"].id, requested_chat_id)
                self.assertEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["user_id"], current_user.id)

                mocks.get_active_group_member_or_403.assert_not_called()
                mocks.get_active_group_member_or_403.assert_not_awaited()

            mocks.resolve_relation_aware_sender_display_name.assert_called_once_with(db, user=current_user)
            mocks.resolve_relation_aware_sender_display_name.assert_awaited_once_with(db, user=current_user)
            self.assertIs(mocks.resolve_relation_aware_sender_display_name.call_args.kwargs["user"], current_user)

            mocks.list_active_room_member_user_ids.assert_not_called()
            mocks.list_active_room_member_user_ids.assert_not_awaited()
            mocks.publish_room_activity_event.assert_not_called()
            mocks.publish_room_activity_event.assert_not_awaited()
            mocks.publish_user_event.assert_not_called()
            mocks.publish_user_event.assert_not_awaited()

            selected_guard_name = (
                "get_active_group_member_or_403" if chat_type == ChatType.GROUP else "get_active_channel_member_or_403"
            )
            expected_await_order = [
                "get_room_or_404",
                selected_guard_name,
                "resolve_relation_aware_sender_display_name",
            ]
            expected_completed_awaits = [
                "get_room_or_404",
                selected_guard_name,
            ]
            self.assertEqual(mocks.await_order, expected_await_order)
            self.assertEqual(mocks.completed_awaits, expected_completed_awaits)

            expected_await_trace = [
                ("get_room_or_404", (db, requested_chat_id), {}),
                (selected_guard_name, (db,), {"chat": returned_chat, "user_id": current_user.id}),
                ("resolve_relation_aware_sender_display_name", (db,), {"user": current_user}),
            ]
            self.assertEqual(mocks.await_trace, expected_await_trace)

    async def _assert_member_listing_failure(self, chat_type: ChatType) -> None:
        requested_chat_id = 7001
        returned_chat_id = 8002
        self.assertNotEqual(requested_chat_id, returned_chat_id)

        returned_chat = SimpleNamespace(id=returned_chat_id, type=chat_type)
        current_user = SimpleNamespace(id=101, account_name="user_101")
        db = object()

        activity_payload = RoomChatActivitySignal(activity="typing", active=True)
        resolved_sender_name = "Accountant Office (Special)"
        synthetic_exc = SyntheticMemberListingError("Synthetic member listing failure")

        with _patch_activity_boundaries(
            room_return=returned_chat,
            sender_name_return=resolved_sender_name,
            member_user_ids_side_effect=synthetic_exc,
        ) as mocks:
            with self.assertRaises(SyntheticMemberListingError) as exc_info:
                await send_room_activity_signal(
                    chat_id=requested_chat_id,
                    data=activity_payload,
                    current_user=current_user,
                    db=db,
                )

            self.assertIs(exc_info.exception, synthetic_exc)

            mocks.get_room_or_404.assert_called_once_with(db, requested_chat_id)
            mocks.get_room_or_404.assert_awaited_once_with(db, requested_chat_id)

            if chat_type == ChatType.GROUP:
                mocks.get_active_group_member_or_403.assert_called_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                mocks.get_active_group_member_or_403.assert_awaited_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                self.assertIs(mocks.get_active_group_member_or_403.call_args.kwargs["chat"], returned_chat)
                self.assertEqual(mocks.get_active_group_member_or_403.call_args.kwargs["chat"].id, returned_chat_id)
                self.assertNotEqual(mocks.get_active_group_member_or_403.call_args.kwargs["chat"].id, requested_chat_id)
                self.assertEqual(mocks.get_active_group_member_or_403.call_args.kwargs["user_id"], current_user.id)

                mocks.get_active_channel_member_or_403.assert_not_called()
                mocks.get_active_channel_member_or_403.assert_not_awaited()
            else:
                mocks.get_active_channel_member_or_403.assert_called_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                mocks.get_active_channel_member_or_403.assert_awaited_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                self.assertIs(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"], returned_chat)
                self.assertEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"].id, returned_chat_id)
                self.assertNotEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"].id, requested_chat_id)
                self.assertEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["user_id"], current_user.id)

                mocks.get_active_group_member_or_403.assert_not_called()
                mocks.get_active_group_member_or_403.assert_not_awaited()

            mocks.resolve_relation_aware_sender_display_name.assert_called_once_with(db, user=current_user)
            mocks.resolve_relation_aware_sender_display_name.assert_awaited_once_with(db, user=current_user)
            self.assertIs(mocks.resolve_relation_aware_sender_display_name.call_args.kwargs["user"], current_user)

            mocks.list_active_room_member_user_ids.assert_called_once_with(db, chat_id=returned_chat.id)
            mocks.list_active_room_member_user_ids.assert_awaited_once_with(db, chat_id=returned_chat.id)
            self.assertEqual(mocks.list_active_room_member_user_ids.call_args.kwargs["chat_id"], returned_chat_id)
            self.assertNotEqual(mocks.list_active_room_member_user_ids.call_args.kwargs["chat_id"], requested_chat_id)

            mocks.publish_room_activity_event.assert_not_called()
            mocks.publish_room_activity_event.assert_not_awaited()
            mocks.publish_user_event.assert_not_called()
            mocks.publish_user_event.assert_not_awaited()

            selected_guard_name = (
                "get_active_group_member_or_403" if chat_type == ChatType.GROUP else "get_active_channel_member_or_403"
            )
            expected_await_order = [
                "get_room_or_404",
                selected_guard_name,
                "resolve_relation_aware_sender_display_name",
                "list_active_room_member_user_ids",
            ]
            expected_completed_awaits = [
                "get_room_or_404",
                selected_guard_name,
                "resolve_relation_aware_sender_display_name",
            ]
            self.assertEqual(mocks.await_order, expected_await_order)
            self.assertEqual(mocks.completed_awaits, expected_completed_awaits)

            expected_await_trace = [
                ("get_room_or_404", (db, requested_chat_id), {}),
                (selected_guard_name, (db,), {"chat": returned_chat, "user_id": current_user.id}),
                ("resolve_relation_aware_sender_display_name", (db,), {"user": current_user}),
                ("list_active_room_member_user_ids", (db,), {"chat_id": returned_chat.id}),
            ]
            self.assertEqual(mocks.await_trace, expected_await_trace)

    async def _assert_publication_failure(self, chat_type: ChatType) -> None:
        requested_chat_id = 7001
        returned_chat_id = 8002
        self.assertNotEqual(requested_chat_id, returned_chat_id)

        returned_chat = SimpleNamespace(id=returned_chat_id, type=chat_type)
        current_user = SimpleNamespace(id=101, account_name="user_101")
        db = object()

        activity_payload = RoomChatActivitySignal(activity="uploading_file", active=False)
        resolved_sender_name = "Accountant Office (Special)"
        returned_member_user_ids = [current_user.id, 9101, 9102, 9103]
        synthetic_exc = SyntheticPublicationError("Synthetic publication failure")

        with _patch_activity_boundaries(
            room_return=returned_chat,
            sender_name_return=resolved_sender_name,
            member_user_ids_return=returned_member_user_ids,
            publish_room_side_effect=synthetic_exc,
        ) as mocks:
            with self.assertRaises(SyntheticPublicationError) as exc_info:
                await send_room_activity_signal(
                    chat_id=requested_chat_id,
                    data=activity_payload,
                    current_user=current_user,
                    db=db,
                )

            self.assertIs(exc_info.exception, synthetic_exc)

            mocks.get_room_or_404.assert_called_once_with(db, requested_chat_id)
            mocks.get_room_or_404.assert_awaited_once_with(db, requested_chat_id)

            if chat_type == ChatType.GROUP:
                mocks.get_active_group_member_or_403.assert_called_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                mocks.get_active_group_member_or_403.assert_awaited_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                self.assertIs(mocks.get_active_group_member_or_403.call_args.kwargs["chat"], returned_chat)
                self.assertEqual(mocks.get_active_group_member_or_403.call_args.kwargs["chat"].id, returned_chat_id)
                self.assertNotEqual(mocks.get_active_group_member_or_403.call_args.kwargs["chat"].id, requested_chat_id)
                self.assertEqual(mocks.get_active_group_member_or_403.call_args.kwargs["user_id"], current_user.id)

                mocks.get_active_channel_member_or_403.assert_not_called()
                mocks.get_active_channel_member_or_403.assert_not_awaited()
            else:
                mocks.get_active_channel_member_or_403.assert_called_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                mocks.get_active_channel_member_or_403.assert_awaited_once_with(
                    db, chat=returned_chat, user_id=current_user.id
                )
                self.assertIs(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"], returned_chat)
                self.assertEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"].id, returned_chat_id)
                self.assertNotEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"].id, requested_chat_id)
                self.assertEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["user_id"], current_user.id)

                mocks.get_active_group_member_or_403.assert_not_called()
                mocks.get_active_group_member_or_403.assert_not_awaited()

            mocks.resolve_relation_aware_sender_display_name.assert_called_once_with(db, user=current_user)
            mocks.resolve_relation_aware_sender_display_name.assert_awaited_once_with(db, user=current_user)
            self.assertIs(mocks.resolve_relation_aware_sender_display_name.call_args.kwargs["user"], current_user)

            mocks.list_active_room_member_user_ids.assert_called_once_with(db, chat_id=returned_chat.id)
            mocks.list_active_room_member_user_ids.assert_awaited_once_with(db, chat_id=returned_chat.id)
            self.assertEqual(mocks.list_active_room_member_user_ids.call_args.kwargs["chat_id"], returned_chat_id)
            self.assertNotEqual(mocks.list_active_room_member_user_ids.call_args.kwargs["chat_id"], requested_chat_id)

            mocks.publish_room_activity_event.assert_called_once_with(
                chat=returned_chat,
                sender_id=current_user.id,
                sender_name=resolved_sender_name,
                member_user_ids=returned_member_user_ids,
                activity="uploading_file",
                active=False,
                publisher=mocks.publish_user_event,
            )
            mocks.publish_room_activity_event.assert_awaited_once_with(
                chat=returned_chat,
                sender_id=current_user.id,
                sender_name=resolved_sender_name,
                member_user_ids=returned_member_user_ids,
                activity="uploading_file",
                active=False,
                publisher=mocks.publish_user_event,
            )

            pub_kwargs = mocks.publish_room_activity_event.call_args.kwargs
            self.assertIs(pub_kwargs["chat"], returned_chat)
            self.assertEqual(pub_kwargs["sender_id"], current_user.id)
            self.assertEqual(pub_kwargs["sender_name"], resolved_sender_name)
            self.assertIs(pub_kwargs["member_user_ids"], returned_member_user_ids)
            self.assertEqual(pub_kwargs["member_user_ids"], [current_user.id, 9101, 9102, 9103])
            self.assertEqual(pub_kwargs["activity"], "uploading_file")
            self.assertIs(pub_kwargs["active"], False)
            self.assertIs(pub_kwargs["publisher"], mocks.publish_user_event)

            mocks.publish_user_event.assert_not_called()
            mocks.publish_user_event.assert_not_awaited()

            selected_guard_name = (
                "get_active_group_member_or_403" if chat_type == ChatType.GROUP else "get_active_channel_member_or_403"
            )
            expected_await_order = [
                "get_room_or_404",
                selected_guard_name,
                "resolve_relation_aware_sender_display_name",
                "list_active_room_member_user_ids",
                "publish_room_activity_event",
            ]
            expected_completed_awaits = [
                "get_room_or_404",
                selected_guard_name,
                "resolve_relation_aware_sender_display_name",
                "list_active_room_member_user_ids",
            ]
            self.assertEqual(mocks.await_order, expected_await_order)
            self.assertEqual(mocks.completed_awaits, expected_completed_awaits)

            expected_await_trace = [
                ("get_room_or_404", (db, requested_chat_id), {}),
                (selected_guard_name, (db,), {"chat": returned_chat, "user_id": current_user.id}),
                ("resolve_relation_aware_sender_display_name", (db,), {"user": current_user}),
                ("list_active_room_member_user_ids", (db,), {"chat_id": returned_chat.id}),
                (
                    "publish_room_activity_event",
                    (),
                    {
                        "chat": returned_chat,
                        "sender_id": current_user.id,
                        "sender_name": resolved_sender_name,
                        "member_user_ids": returned_member_user_ids,
                        "activity": "uploading_file",
                        "active": False,
                        "publisher": mocks.publish_user_event,
                    },
                ),
            ]
            self.assertEqual(mocks.await_trace, expected_await_trace)

    async def test_sender_name_resolution_failure_raises_and_guards_downstream(self) -> None:
        for chat_type in (ChatType.GROUP, ChatType.CHANNEL):
            with self.subTest(chat_type=chat_type.value):
                await self._assert_sender_name_resolution_failure(chat_type)

    async def test_member_listing_failure_raises_and_guards_downstream(self) -> None:
        for chat_type in (ChatType.GROUP, ChatType.CHANNEL):
            with self.subTest(chat_type=chat_type.value):
                await self._assert_member_listing_failure(chat_type)

    async def test_publication_failure_raises_and_preserves_payload(self) -> None:
        for chat_type in (ChatType.GROUP, ChatType.CHANNEL):
            with self.subTest(chat_type=chat_type.value):
                await self._assert_publication_failure(chat_type)


if __name__ == "__main__":
    unittest.main()
