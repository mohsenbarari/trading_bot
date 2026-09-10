"""Negative-path characterization tests for send_room_activity_signal early failure boundaries.

Seed: API-MSG-POST-CHAT-SEND-ROOM-ACTIVITY-SIGNAL-L962
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001 (.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md)

Lifecycle status:
- P1-00: COMPLETE
- P1-00b: IN_PROGRESS
- Tracked HIGH seed-handler gap: BLOCKED_EXPLICIT

Evidence limits:
This unit characterizes three early negative-path failure boundaries of
api.routers.chat.send_room_activity_signal (room lookup 404, group membership 403,
and channel membership 403). All real execution ends strictly at the seven patched
handler dependency boundaries (room lookup, group membership guard, channel membership
guard, sender-name resolution, member listing, room activity publication, and
publish_user_event callback). This characterization provides handler-level negative-path
evidence without claiming or evidencing policy enforcement, persistence, real delivery,
rollback, seed closure, or tests run.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import inspect
from types import SimpleNamespace
from typing import Any, Iterator
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, status

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.chat import send_room_activity_signal
from api.routers.chat_schemas import RoomChatActivitySignal
from core.enums import ChatType


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
    sender_name_return: Any = None,
    member_user_ids_return: Any = None,
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
    )
    list_members_mock = _make_traced_mock(
        "list_active_room_member_user_ids",
        return_value=member_user_ids_return,
    )
    publish_room_mock = _make_traced_mock("publish_room_activity_event")
    publish_user_event_mock = _make_traced_mock("publish_user_event")

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


class SendRoomActivitySignalGuardCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_room_lookup_failure_raises_404_and_guards_collaborators(self) -> None:
        requested_chat_id = 901
        current_user = SimpleNamespace(id=11)
        db = object()
        sentinel_headers = {"X-Sentinel-Room-Lookup": "sentinel-room-404-check"}
        synthetic_exc = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found",
            headers=sentinel_headers,
        )
        activity_payload = RoomChatActivitySignal(activity="typing", active=True)

        with _patch_activity_boundaries(room_side_effect=synthetic_exc) as mocks:
            with self.assertRaises(HTTPException) as exc_info:
                await send_room_activity_signal(
                    chat_id=requested_chat_id,
                    data=activity_payload,
                    current_user=current_user,
                    db=db,
                )

            self.assertIs(exc_info.exception, synthetic_exc)
            self.assertEqual(exc_info.exception.status_code, 404)
            self.assertEqual(exc_info.exception.detail, "Room not found")
            self.assertEqual(exc_info.exception.headers, sentinel_headers)

            mocks.get_room_or_404.assert_called_once_with(db, requested_chat_id)
            mocks.get_room_or_404.assert_awaited_once_with(db, requested_chat_id)

            self.assertEqual(mocks.await_order, ["get_room_or_404"])
            self.assertEqual(mocks.completed_awaits, [])
            self.assertEqual(
                mocks.await_trace,
                [("get_room_or_404", (db, requested_chat_id), {})],
            )

            mocks.get_active_group_member_or_403.assert_not_called()
            mocks.get_active_group_member_or_403.assert_not_awaited()
            mocks.get_active_channel_member_or_403.assert_not_called()
            mocks.get_active_channel_member_or_403.assert_not_awaited()
            mocks.resolve_relation_aware_sender_display_name.assert_not_called()
            mocks.resolve_relation_aware_sender_display_name.assert_not_awaited()
            mocks.list_active_room_member_user_ids.assert_not_called()
            mocks.list_active_room_member_user_ids.assert_not_awaited()
            mocks.publish_room_activity_event.assert_not_called()
            mocks.publish_room_activity_event.assert_not_awaited()
            mocks.publish_user_event.assert_not_called()
            mocks.publish_user_event.assert_not_awaited()

    async def test_group_membership_rejection_raises_403_and_guards_collaborators(self) -> None:
        requested_chat_id = 101
        returned_chat_id = 202
        self.assertNotEqual(requested_chat_id, returned_chat_id)
        group_chat = SimpleNamespace(id=returned_chat_id, type=ChatType.GROUP)
        current_user = SimpleNamespace(id=33)
        db = object()
        sentinel_headers = {"X-Sentinel-Group-Guard": "sentinel-group-403-check"}
        synthetic_exc = HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this group",
            headers=sentinel_headers,
        )
        activity_payload = RoomChatActivitySignal(activity="typing", active=True)

        with _patch_activity_boundaries(
            room_return=group_chat,
            group_member_side_effect=synthetic_exc,
        ) as mocks:
            with self.assertRaises(HTTPException) as exc_info:
                await send_room_activity_signal(
                    chat_id=requested_chat_id,
                    data=activity_payload,
                    current_user=current_user,
                    db=db,
                )

            self.assertIs(exc_info.exception, synthetic_exc)
            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(exc_info.exception.detail, "You are not a member of this group")
            self.assertEqual(exc_info.exception.headers, sentinel_headers)

            mocks.get_room_or_404.assert_called_once_with(db, requested_chat_id)
            mocks.get_room_or_404.assert_awaited_once_with(db, requested_chat_id)
            mocks.get_active_group_member_or_403.assert_called_once_with(
                db,
                chat=group_chat,
                user_id=current_user.id,
            )
            mocks.get_active_group_member_or_403.assert_awaited_once_with(
                db,
                chat=group_chat,
                user_id=current_user.id,
            )

            self.assertEqual(
                mocks.await_order,
                ["get_room_or_404", "get_active_group_member_or_403"],
            )
            self.assertEqual(mocks.completed_awaits, ["get_room_or_404"])
            self.assertEqual(
                mocks.await_trace,
                [
                    ("get_room_or_404", (db, requested_chat_id), {}),
                    (
                        "get_active_group_member_or_403",
                        (db,),
                        {"chat": group_chat, "user_id": current_user.id},
                    ),
                ],
            )

            self.assertIs(mocks.get_active_group_member_or_403.call_args.kwargs["chat"], group_chat)
            self.assertEqual(mocks.get_active_group_member_or_403.call_args.kwargs["chat"].id, returned_chat_id)
            self.assertNotEqual(mocks.get_active_group_member_or_403.call_args.kwargs["chat"].id, requested_chat_id)

            mocks.get_active_channel_member_or_403.assert_not_called()
            mocks.get_active_channel_member_or_403.assert_not_awaited()
            mocks.resolve_relation_aware_sender_display_name.assert_not_called()
            mocks.resolve_relation_aware_sender_display_name.assert_not_awaited()
            mocks.list_active_room_member_user_ids.assert_not_called()
            mocks.list_active_room_member_user_ids.assert_not_awaited()
            mocks.publish_room_activity_event.assert_not_called()
            mocks.publish_room_activity_event.assert_not_awaited()
            mocks.publish_user_event.assert_not_called()
            mocks.publish_user_event.assert_not_awaited()

    async def test_channel_membership_rejection_raises_403_and_guards_collaborators(self) -> None:
        requested_chat_id = 501
        returned_chat_id = 602
        self.assertNotEqual(requested_chat_id, returned_chat_id)
        channel_chat = SimpleNamespace(id=returned_chat_id, type=ChatType.CHANNEL)
        current_user = SimpleNamespace(id=44)
        db = object()
        sentinel_headers = {"X-Sentinel-Channel-Guard": "sentinel-channel-403-check"}
        synthetic_exc = HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this channel",
            headers=sentinel_headers,
        )
        activity_payload = RoomChatActivitySignal(activity="uploading_file", active=False)

        with _patch_activity_boundaries(
            room_return=channel_chat,
            channel_member_side_effect=synthetic_exc,
        ) as mocks:
            with self.assertRaises(HTTPException) as exc_info:
                await send_room_activity_signal(
                    chat_id=requested_chat_id,
                    data=activity_payload,
                    current_user=current_user,
                    db=db,
                )

            self.assertIs(exc_info.exception, synthetic_exc)
            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(exc_info.exception.detail, "You are not a member of this channel")
            self.assertEqual(exc_info.exception.headers, sentinel_headers)

            mocks.get_room_or_404.assert_called_once_with(db, requested_chat_id)
            mocks.get_room_or_404.assert_awaited_once_with(db, requested_chat_id)
            mocks.get_active_channel_member_or_403.assert_called_once_with(
                db,
                chat=channel_chat,
                user_id=current_user.id,
            )
            mocks.get_active_channel_member_or_403.assert_awaited_once_with(
                db,
                chat=channel_chat,
                user_id=current_user.id,
            )

            self.assertEqual(
                mocks.await_order,
                ["get_room_or_404", "get_active_channel_member_or_403"],
            )
            self.assertEqual(mocks.completed_awaits, ["get_room_or_404"])
            self.assertEqual(
                mocks.await_trace,
                [
                    ("get_room_or_404", (db, requested_chat_id), {}),
                    (
                        "get_active_channel_member_or_403",
                        (db,),
                        {"chat": channel_chat, "user_id": current_user.id},
                    ),
                ],
            )

            self.assertIs(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"], channel_chat)
            self.assertEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"].id, returned_chat_id)
            self.assertNotEqual(mocks.get_active_channel_member_or_403.call_args.kwargs["chat"].id, requested_chat_id)

            mocks.get_active_group_member_or_403.assert_not_called()
            mocks.get_active_group_member_or_403.assert_not_awaited()
            mocks.resolve_relation_aware_sender_display_name.assert_not_called()
            mocks.resolve_relation_aware_sender_display_name.assert_not_awaited()
            mocks.list_active_room_member_user_ids.assert_not_called()
            mocks.list_active_room_member_user_ids.assert_not_awaited()
            mocks.publish_room_activity_event.assert_not_called()
            mocks.publish_room_activity_event.assert_not_awaited()
            mocks.publish_user_event.assert_not_called()
            mocks.publish_user_event.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
