from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import bot.handlers.admin_users as admin_users
from bot.handlers.admin_users import (
    _can_open_user_management,
    clear_state_retain_anchors,
    handle_limit_cancel,
    handle_user_search_cancel,
)
from core.enums import UserRole


class AdminUsersCancelFSMClearFailuresCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        super().setUp()
        self.fence = install_p1_00_network_fence()
        self.denied_connects_before = len(self.fence["denied_connects"])
        self.live_redis_constructs_before = len(
            self.fence["live_redis_constructs"]
        )

        self.orig_can_open = admin_users._can_open_user_management
        self.orig_clear_retain = admin_users.clear_state_retain_anchors
        self.orig_limit_cancel = admin_users.handle_limit_cancel
        self.orig_search_cancel = admin_users.handle_user_search_cancel

        self.linked_actor = SimpleNamespace(
            id=42,
            telegram_id=420042,
            role=UserRole.MIDDLE_MANAGER,
        )

        self.call_order: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.clear_error = RuntimeError(
            f"fsm_clear_failure_{self.id()}"
        )
        self.initial_state_data = {
            "anchor_id": 101,
            "users_menu_id": 202,
            "transient_step": "pending_cancel",
        }

        async def _get_data(*args: object, **kwargs: object) -> dict[str, object]:
            self.call_order.append(("state.get_data", args, dict(kwargs)))
            return dict(self.initial_state_data)

        async def _clear(*args: object, **kwargs: object) -> None:
            self.call_order.append(("state.clear", args, dict(kwargs)))
            raise self.clear_error

        async def _update_data(*args: object, **kwargs: object) -> None:
            self.call_order.append(("state.update_data", args, dict(kwargs)))
            raise AssertionError("state.update_data must not be called")

        self.state = SimpleNamespace(
            get_data=AsyncMock(side_effect=_get_data),
            clear=AsyncMock(side_effect=_clear),
            update_data=AsyncMock(side_effect=_update_data),
        )

        self.message = SimpleNamespace(
            message_id=707,
            chat=SimpleNamespace(id=808),
            answer=AsyncMock(
                side_effect=AssertionError("callback.message.answer must not be called")
            ),
            edit_text=AsyncMock(
                side_effect=AssertionError("callback.message.edit_text must not be called")
            ),
            delete=AsyncMock(
                side_effect=AssertionError("callback.message.delete must not be called")
            ),
        )
        self.bot = SimpleNamespace(
            delete_message=AsyncMock(
                side_effect=AssertionError("callback.bot.delete_message must not be called")
            )
        )
        self.callback = SimpleNamespace(
            id="cb_cancel_fsm_clear_failure",
            data="user_search_cancel",
            message=self.message,
            bot=self.bot,
            answer=AsyncMock(
                side_effect=AssertionError("callback.answer must not be called")
            ),
        )

        self.async_session_local_mock = MagicMock(
            side_effect=AssertionError("AsyncSessionLocal must not be called")
        )
        self.answer_callback_query_mock = AsyncMock(
            side_effect=AssertionError(
                "answer_callback_query_via_runtime must not be called"
            )
        )
        self.answer_callback_message_mock = AsyncMock(
            side_effect=AssertionError(
                "answer_callback_message_via_runtime must not be called"
            )
        )
        self.edit_callback_message_mock = AsyncMock(
            side_effect=AssertionError(
                "edit_callback_message_via_runtime must not be called"
            )
        )
        self.build_nav_keyboard_mock = AsyncMock(
            side_effect=AssertionError(
                "build_users_management_navigation_keyboard must not be called"
            )
        )
        self.update_anchor_mock = AsyncMock(
            side_effect=AssertionError("update_anchor must not be called")
        )
        self.get_user_profile_text_mock = AsyncMock(
            side_effect=AssertionError("get_user_profile_text must not be called")
        )
        self.get_user_settings_keyboard_mock = MagicMock(
            side_effect=AssertionError(
                "get_user_settings_keyboard must not be called"
            )
        )

        patch_specs = [
            ("AsyncSessionLocal", self.async_session_local_mock),
            ("answer_callback_query_via_runtime", self.answer_callback_query_mock),
            ("answer_callback_message_via_runtime", self.answer_callback_message_mock),
            ("edit_callback_message_via_runtime", self.edit_callback_message_mock),
            (
                "build_users_management_navigation_keyboard",
                self.build_nav_keyboard_mock,
            ),
            ("update_anchor", self.update_anchor_mock),
            ("get_user_profile_text", self.get_user_profile_text_mock),
            ("get_user_settings_keyboard", self.get_user_settings_keyboard_mock),
        ]
        for attr_name, replacement in patch_specs:
            patcher = patch.object(admin_users, attr_name, new=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _assert_shared_postconditions(self, caught_exc: BaseException) -> None:
        self.assertIs(caught_exc, self.clear_error)
        self.assertIs(
            admin_users._can_open_user_management,
            _can_open_user_management,
        )
        self.assertIs(
            admin_users.clear_state_retain_anchors,
            clear_state_retain_anchors,
        )
        self.assertIs(admin_users.handle_limit_cancel, handle_limit_cancel)
        self.assertIs(
            admin_users.handle_user_search_cancel,
            handle_user_search_cancel,
        )
        self.assertTrue(_can_open_user_management(self.linked_actor))

        self.assertEqual(
            self.call_order,
            [
                ("state.get_data", (), {}),
                ("state.clear", (), {}),
            ],
        )
        self.state.get_data.assert_called_once_with()
        self.state.get_data.assert_awaited_once_with()
        self.state.clear.assert_called_once_with()
        self.state.clear.assert_awaited_once_with()
        self.state.update_data.assert_not_called()
        self.state.update_data.assert_not_awaited()

        self.async_session_local_mock.assert_not_called()
        self.answer_callback_query_mock.assert_not_called()
        self.answer_callback_query_mock.assert_not_awaited()
        self.answer_callback_message_mock.assert_not_called()
        self.answer_callback_message_mock.assert_not_awaited()
        self.edit_callback_message_mock.assert_not_called()
        self.edit_callback_message_mock.assert_not_awaited()
        self.build_nav_keyboard_mock.assert_not_called()
        self.build_nav_keyboard_mock.assert_not_awaited()
        self.update_anchor_mock.assert_not_called()
        self.update_anchor_mock.assert_not_awaited()
        self.get_user_profile_text_mock.assert_not_called()
        self.get_user_profile_text_mock.assert_not_awaited()
        self.get_user_settings_keyboard_mock.assert_not_called()

        self.callback.answer.assert_not_called()
        self.callback.answer.assert_not_awaited()
        self.message.answer.assert_not_called()
        self.message.answer.assert_not_awaited()
        self.message.edit_text.assert_not_called()
        self.message.edit_text.assert_not_awaited()
        self.message.delete.assert_not_called()
        self.message.delete.assert_not_awaited()
        self.bot.delete_message.assert_not_called()
        self.bot.delete_message.assert_not_awaited()

        self.assertEqual(
            len(self.fence["denied_connects"]),
            self.denied_connects_before,
        )
        self.assertEqual(
            len(self.fence["live_redis_constructs"]),
            self.live_redis_constructs_before,
        )

    async def test_limit_cancel_propagates_fsm_clear_failure_before_session_or_delivery(
        self,
    ) -> None:
        self.callback.data = "limit_cancel_9"
        with self.assertRaises(RuntimeError) as exc_ctx:
            await handle_limit_cancel(
                self.callback,
                self.linked_actor,
                self.state,
            )
        self._assert_shared_postconditions(exc_ctx.exception)

    async def test_user_search_cancel_propagates_fsm_clear_failure_before_navigation_or_delivery(
        self,
    ) -> None:
        self.callback.data = "user_search_cancel"
        with self.assertRaises(RuntimeError) as exc_ctx:
            await handle_user_search_cancel(
                self.callback,
                self.state,
                self.linked_actor,
            )
        self._assert_shared_postconditions(exc_ctx.exception)


if __name__ == "__main__":
    unittest.main()
