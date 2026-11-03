"""Bounded hermetic characterization for admin_users search FSM-boundary failures.

Provides synthetic dependency-failure evidence for two existing search handlers
in ``bot.handlers.admin_users`` (``start_search_user`` and
``handle_user_search_cancel``) using invented in-memory test fixtures rather
than user or operational data. Both scenarios inject a failure at an FSM seam
before any Telegram delivery is constructed or sent, so no delivery text,
inline keyboard, ``parse_mode``, or ``reply_markup`` is reached or asserted,
no database session is entered, and no successful delivery, deletion,
persistence, or state transition is claimed.

Explicit exclusions (unproved here): dispatcher/HTTP/Depends wiring, full
authorization matrix beyond the linked ``MIDDLE_MANAGER`` guard check, real
Telegram/queue/DB/persistence execution, concurrency/retry/reload behavior,
and origins/whole-handler/full-seed/High/Stage closure.
"""

from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import bot.handlers.admin_users as admin_users
from bot.handlers.admin_users import (
    _can_open_user_management,
    clear_state_retain_anchors,
    handle_user_search_cancel,
    start_search_user,
)
from bot.states import UserManagement
from core.enums import UserRole


class _InMemoryFSM:
    """In-memory FSM exposing AsyncMock seams over a real backing dict."""

    def __init__(  # noqa: PLR0913
        self,
        events: list[str],
        event_calls: list[tuple[str, tuple[object, ...], dict[str, object]]],
        *,
        initial_data: Mapping[str, object] | None = None,
        initial_state: object = None,
    ) -> None:
        self.data: dict[str, object] = dict(initial_data or {})
        self.current_state: object = initial_state
        self.failures: dict[str, BaseException] = {}
        self._events = events
        self._event_calls = event_calls

        async def _get_data(*args: object, **kwargs: object) -> dict[str, object]:
            self._events.append("get_data")
            self._event_calls.append(("get_data", args, dict(kwargs)))
            if "get_data" in self.failures:
                raise self.failures["get_data"]
            return dict(self.data)

        async def _clear(*args: object, **kwargs: object) -> None:
            self._events.append("clear")
            self._event_calls.append(("clear", args, dict(kwargs)))
            if "clear" in self.failures:
                raise self.failures["clear"]
            self.data.clear()
            self.current_state = None

        async def _update_data(
            *args: object, **kwargs: object
        ) -> dict[str, object]:
            self._events.append("update_data")
            self._event_calls.append(("update_data", args, dict(kwargs)))
            if "update_data" in self.failures:
                raise self.failures["update_data"]
            if args:
                if len(args) != 1 or not isinstance(args[0], Mapping):
                    raise TypeError("update_data expects at most one mapping")
                self.data.update(args[0])
            self.data.update(kwargs)
            return dict(self.data)

        async def _set_state(*args: object, **kwargs: object) -> None:
            self._events.append("set_state")
            self._event_calls.append(("set_state", args, dict(kwargs)))
            if "set_state" in self.failures:
                raise self.failures["set_state"]
            if len(args) == 1 and not kwargs:
                self.current_state = args[0]
            elif not args and set(kwargs) <= {"state"}:
                self.current_state = kwargs.get("state")
            else:
                raise TypeError("set_state expects one state argument")

        self.get_data = AsyncMock(side_effect=_get_data)
        self.clear = AsyncMock(side_effect=_clear)
        self.update_data = AsyncMock(side_effect=_update_data)
        self.set_state = AsyncMock(side_effect=_set_state)


class AdminUsersSearchFsmBoundaryFailuresCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        super().setUp()
        self.fence = install_p1_00_network_fence()
        self.denied_connects_before = len(self.fence["denied_connects"])
        self.live_redis_constructs_before = len(
            self.fence["live_redis_constructs"]
        )

        self.real_admin_users_targets: list[tuple[str, object]] = [
            ("_can_open_user_management", admin_users._can_open_user_management),
            ("clear_state_retain_anchors", admin_users.clear_state_retain_anchors),
            ("start_search_user", admin_users.start_search_user),
            ("handle_user_search_cancel", admin_users.handle_user_search_cancel),
            ("router", admin_users.router),
        ]

        self.actor = SimpleNamespace(
            id=42,
            telegram_id=420042,
            sync_version=1,
            role=UserRole.MIDDLE_MANAGER,
        )

        self.events: list[str] = []
        self.event_calls: list[
            tuple[str, tuple[object, ...], dict[str, object]]
        ] = []

        self.state = _InMemoryFSM(
            self.events,
            self.event_calls,
            initial_data={
                "anchor_id": 101,
                "users_menu_id": 202,
                "search_query_draft": "synthetic_pending_search",
            },
            initial_state=None,
        )

        self.bot = SimpleNamespace(
            id=990099,
            delete_message=self._make_fail_closed_async_mock(
                "bot.delete_message"
            ),
        )
        self.message = SimpleNamespace(
            message_id=701,
            chat=SimpleNamespace(id=808, type="private"),
            from_user=SimpleNamespace(id=420042),
            bot=self.bot,
            answer=self._make_fail_closed_async_mock("message.answer"),
            delete=self._make_fail_closed_async_mock("message.delete"),
            edit_text=self._make_fail_closed_async_mock("message.edit_text"),
        )
        self.callback = SimpleNamespace(
            id="cb_admin_users_search_cancel_901",
            data="user_search_cancel",
            from_user=SimpleNamespace(id=420042),
            message=self.message,
            bot=self.bot,
            answer=self._make_fail_closed_async_mock("callback.answer"),
        )

        self.allow_delete_user_message = False

        async def _delete_user_message(
            *args: object, **kwargs: object
        ) -> None:
            self.events.append("delete_user_message")
            self.event_calls.append(("delete_user_message", args, dict(kwargs)))
            if not self.allow_delete_user_message:
                raise AssertionError("delete_user_message must not be awaited")

        self.delete_user_message_mock = AsyncMock(
            side_effect=_delete_user_message
        )
        self.answer_incoming_message_mock = self._make_fail_closed_async_mock(
            "answer_incoming_message_via_runtime"
        )
        self.answer_callback_message_mock = self._make_fail_closed_async_mock(
            "answer_callback_message_via_runtime"
        )
        self.edit_callback_message_mock = self._make_fail_closed_async_mock(
            "edit_callback_message_via_runtime"
        )
        self.build_nav_keyboard_mock = self._make_fail_closed_async_mock(
            "build_users_management_navigation_keyboard"
        )
        self.update_anchor_mock = self._make_fail_closed_async_mock(
            "update_anchor"
        )
        self.answer_callback_query_mock = self._make_fail_closed_async_mock(
            "answer_callback_query_via_runtime"
        )
        self.async_session_local_mock = MagicMock(
            side_effect=AssertionError(
                "AsyncSessionLocal must not be constructed"
            )
        )
        self.create_task_mock = MagicMock(
            side_effect=AssertionError("asyncio.create_task must not be called")
        )

        self.module_patch_specs: list[tuple[object, str, object]] = [
            (admin_users, "delete_user_message", self.delete_user_message_mock),
            (
                admin_users,
                "answer_incoming_message_via_runtime",
                self.answer_incoming_message_mock,
            ),
            (
                admin_users,
                "answer_callback_message_via_runtime",
                self.answer_callback_message_mock,
            ),
            (
                admin_users,
                "edit_callback_message_via_runtime",
                self.edit_callback_message_mock,
            ),
            (
                admin_users,
                "build_users_management_navigation_keyboard",
                self.build_nav_keyboard_mock,
            ),
            (admin_users, "update_anchor", self.update_anchor_mock),
            (
                admin_users,
                "answer_callback_query_via_runtime",
                self.answer_callback_query_mock,
            ),
            (
                admin_users,
                "AsyncSessionLocal",
                self.async_session_local_mock,
            ),
        ]

        self.saved_fixture_methods: list[tuple[object, str, object]] = [
            (self.state, "get_data", self.state.get_data),
            (self.state, "clear", self.state.clear),
            (self.state, "update_data", self.state.update_data),
            (self.state, "set_state", self.state.set_state),
            (self.callback, "answer", self.callback.answer),
            (self.message, "answer", self.message.answer),
            (self.message, "delete", self.message.delete),
            (self.message, "edit_text", self.message.edit_text),
            (self.bot, "delete_message", self.bot.delete_message),
        ]

    def _make_fail_closed_async_mock(self, name: str) -> AsyncMock:
        async def _unexpected(*args: object, **kwargs: object) -> None:
            self.events.append(name)
            self.event_calls.append((name, args, dict(kwargs)))
            raise AssertionError(f"{name} must not be awaited")

        return AsyncMock(side_effect=_unexpected)

    def _all_async_seams(self) -> dict[str, AsyncMock]:
        return {
            "delete_user_message": self.delete_user_message_mock,
            "state.get_data": self.state.get_data,
            "state.clear": self.state.clear,
            "state.update_data": self.state.update_data,
            "state.set_state": self.state.set_state,
            "answer_incoming_message_via_runtime": (
                self.answer_incoming_message_mock
            ),
            "answer_callback_message_via_runtime": (
                self.answer_callback_message_mock
            ),
            "edit_callback_message_via_runtime": (
                self.edit_callback_message_mock
            ),
            "build_users_management_navigation_keyboard": (
                self.build_nav_keyboard_mock
            ),
            "update_anchor": self.update_anchor_mock,
            "answer_callback_query_via_runtime": (
                self.answer_callback_query_mock
            ),
            "callback.answer": self.callback.answer,
            "message.answer": self.message.answer,
            "message.delete": self.message.delete,
            "message.edit_text": self.message.edit_text,
            "bot.delete_message": self.bot.delete_message,
        }

    def _assert_real_targets_unpatched(self) -> None:
        self.assertIs(
            admin_users._can_open_user_management,
            _can_open_user_management,
        )
        self.assertIs(
            admin_users.clear_state_retain_anchors,
            clear_state_retain_anchors,
        )
        self.assertIs(admin_users.start_search_user, start_search_user)
        self.assertIs(
            admin_users.handle_user_search_cancel,
            handle_user_search_cancel,
        )
        for attr_name, orig_target in self.real_admin_users_targets:
            self.assertIs(getattr(admin_users, attr_name), orig_target)

    def _assert_async_not_invoked(self, mock: AsyncMock) -> None:
        mock.assert_not_called()
        mock.assert_not_awaited()
        self.assertEqual(mock.call_count, 0)
        self.assertEqual(mock.await_count, 0)

    def _assert_async_awaited_once_with(
        self,
        mock: AsyncMock,
        *expected_args: object,
        **expected_kwargs: object,
    ) -> None:
        self.assertEqual(mock.call_count, 1)
        self.assertEqual(mock.await_count, 1)
        mock.assert_called_once_with(*expected_args, **expected_kwargs)
        mock.assert_awaited_once_with(*expected_args, **expected_kwargs)
        self.assertEqual(mock.call_args.args, expected_args)
        self.assertEqual(mock.call_args.kwargs, expected_kwargs)
        self.assertEqual(mock.await_args.args, expected_args)
        self.assertEqual(mock.await_args.kwargs, expected_kwargs)

    async def _run_with_boundary_patches(
        self,
        invoke: Callable[[], Awaitable[object]],
    ) -> RuntimeError:
        saved_module_attrs: list[tuple[object, str, object]] = [
            (owner, attr_name, getattr(owner, attr_name))
            for owner, attr_name, _ in self.module_patch_specs
        ]
        orig_create_task = asyncio.create_task

        stack = ExitStack()
        self.addCleanup(stack.close)
        try:
            for owner, attr_name, replacement in self.module_patch_specs:
                stack.enter_context(
                    patch.object(owner, attr_name, new=replacement)
                )
                self.assertIs(getattr(owner, attr_name), replacement)

            self._assert_real_targets_unpatched()

            with patch.object(
                asyncio, "create_task", new=self.create_task_mock
            ):
                self.assertIs(asyncio.create_task, self.create_task_mock)
                with self.assertRaises(RuntimeError) as exc_ctx:
                    await invoke()
            self.assertIs(asyncio.create_task, orig_create_task)
        finally:
            stack.close()
            self.assertIs(asyncio.create_task, orig_create_task)
            for owner, attr_name, orig_value in saved_module_attrs:
                self.assertIs(getattr(owner, attr_name), orig_value)

        self._assert_real_targets_unpatched()
        for owner, attr_name, orig_method in self.saved_fixture_methods:
            self.assertIs(getattr(owner, attr_name), orig_method)
        return exc_ctx.exception

    def _assert_boundary_postconditions(  # noqa: PLR0913
        self,
        *,
        caught_exc: BaseException,
        expected_exc: RuntimeError,
        expected_events: list[str],
        expected_event_calls: list[
            tuple[str, tuple[object, ...], dict[str, object]]
        ],
        expected_async_calls: Mapping[
            str, tuple[tuple[object, ...], dict[str, object]]
        ],
        expected_state_data: Mapping[str, object],
        expected_current_state: object,
    ) -> None:
        self.assertIs(caught_exc, expected_exc)
        self.assertIsNot(self.message, self.callback)
        self.assertIsNot(self.message, self.bot)
        self.assertIsNot(self.callback, self.bot)
        self.assertEqual(self.actor.id, 42)
        self.assertEqual(self.actor.telegram_id, 420042)
        self.assertEqual(self.actor.sync_version, 1)
        self.assertIs(self.actor.role, UserRole.MIDDLE_MANAGER)
        self.assertTrue(_can_open_user_management(self.actor))

        self.assertEqual(self.events, expected_events)
        self.assertEqual(self.event_calls, expected_event_calls)
        self.assertEqual(self.state.data, dict(expected_state_data))
        self.assertEqual(self.state.current_state, expected_current_state)

        all_async_seams = self._all_async_seams()
        self.assertTrue(set(expected_async_calls).issubset(all_async_seams))
        for seam_name, seam_mock in all_async_seams.items():
            if seam_name in expected_async_calls:
                exp_args, exp_kwargs = expected_async_calls[seam_name]
                self._assert_async_awaited_once_with(
                    seam_mock, *exp_args, **exp_kwargs
                )
            else:
                self._assert_async_not_invoked(seam_mock)

        self.async_session_local_mock.assert_not_called()
        self.assertEqual(self.async_session_local_mock.call_count, 0)
        self.create_task_mock.assert_not_called()
        self.assertEqual(self.create_task_mock.call_count, 0)

        self.assertTrue(self.fence["installed"])
        self.assertEqual(
            len(self.fence["denied_connects"]),
            self.denied_connects_before,
        )
        self.assertEqual(
            len(self.fence["live_redis_constructs"]),
            self.live_redis_constructs_before,
        )

    async def test_search_entry_set_state_failure_propagates_before_delivery_or_anchor(
        self,
    ) -> None:
        self.state.data.clear()
        self.state.data.update({"anchor_id": 101, "users_menu_id": 202})
        self.state.current_state = None
        self.allow_delete_user_message = True
        entry_error = RuntimeError(
            "admin_users_search_entry_set_state_boundary_failure"
        )
        self.state.failures["set_state"] = entry_error

        caught_exc = await self._run_with_boundary_patches(
            lambda: start_search_user(self.message, self.state, self.actor)
        )

        self._assert_boundary_postconditions(
            caught_exc=caught_exc,
            expected_exc=entry_error,
            expected_events=["delete_user_message", "set_state"],
            expected_event_calls=[
                ("delete_user_message", (self.message,), {}),
                ("set_state", (UserManagement.awaiting_search_query,), {}),
            ],
            expected_async_calls={
                "delete_user_message": ((self.message,), {}),
                "state.set_state": (
                    (UserManagement.awaiting_search_query,),
                    {},
                ),
            },
            expected_state_data={"anchor_id": 101, "users_menu_id": 202},
            expected_current_state=None,
        )
        self.assertIs(
            self.delete_user_message_mock.await_args.args[0],
            self.message,
        )

    async def test_search_cancel_get_data_failure_propagates_before_clear_or_navigation(
        self,
    ) -> None:
        expected_initial_data = {
            "anchor_id": 101,
            "users_menu_id": 202,
            "search_query_draft": "synthetic_pending_search",
        }
        self.state.current_state = UserManagement.awaiting_search_query
        self.allow_delete_user_message = False
        cancel_error = RuntimeError(
            "admin_users_search_cancel_get_data_boundary_failure"
        )
        self.state.failures["get_data"] = cancel_error

        caught_exc = await self._run_with_boundary_patches(
            lambda: handle_user_search_cancel(
                self.callback, self.state, self.actor
            )
        )

        self._assert_boundary_postconditions(
            caught_exc=caught_exc,
            expected_exc=cancel_error,
            expected_events=["get_data"],
            expected_event_calls=[("get_data", (), {})],
            expected_async_calls={"state.get_data": ((), {})},
            expected_state_data=expected_initial_data,
            expected_current_state=UserManagement.awaiting_search_query,
        )


if __name__ == "__main__":
    unittest.main()
