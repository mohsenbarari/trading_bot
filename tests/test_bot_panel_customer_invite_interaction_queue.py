import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import panel


class _State:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.state = None
        self.cleared = False

    async def clear(self):
        self.data.clear()
        self.state = None
        self.cleared = True

    async def update_data(self, **values):
        self.data.update(values)

    async def set_state(self, value):
        self.state = value

    async def get_data(self):
        return dict(self.data)


def _message(text="input"):
    return SimpleNamespace(text=text, answer=AsyncMock())


def _callback(identifier="callback-1"):
    return SimpleNamespace(
        id=identifier,
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
    )


def _user():
    return SimpleNamespace(
        id=7,
        role=panel.UserRole.STANDARD,
        account_name="owner7",
        mobile_number="09120000007",
        telegram_id=7007,
        account_status=None,
        messenger_blocked_at=None,
        messenger_grace_expires_at=None,
    )


class BotPanelCustomerInviteInteractionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_invite_flow_replies_use_durable_interaction_adapters(self):
        user = _user()
        callback = _callback()
        callback_direct_answer = callback.message.answer

        with (
            patch(
                "bot.handlers.panel.answer_callback_message_via_runtime",
                new=AsyncMock(),
            ) as enqueue_callback,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as enqueue_incoming,
            patch(
                "bot.handlers.panel.answer_callback_query_via_runtime",
                new=AsyncMock(),
            ),
        ):
            with (
                patch(
                    "bot.handlers.panel._customer_invite_access_allowed",
                    new=AsyncMock(return_value=(True, None)),
                ),
                patch(
                    "bot.handlers.panel.check_customer_invite_sync_ready",
                    new=AsyncMock(
                        return_value=SimpleNamespace(
                            ready=False,
                            message="همگام‌سازی آماده نیست",
                        )
                    ),
                ),
            ):
                await panel.start_user_panel_customer_invite_tier1(
                    callback,
                    _State(),
                    user,
                )

            with (
                patch(
                    "bot.handlers.panel._customer_invite_access_allowed",
                    new=AsyncMock(return_value=(True, None)),
                ),
                patch(
                    "bot.handlers.panel.check_customer_invite_sync_ready",
                    new=AsyncMock(
                        return_value=SimpleNamespace(ready=True, message=None)
                    ),
                ),
            ):
                await panel.start_user_panel_customer_invite_tier1(
                    callback,
                    _State(),
                    user,
                )

            denied_name_message = _message()
            with patch(
                "bot.handlers.panel._customer_invite_access_allowed",
                new=AsyncMock(return_value=(False, "عدم دسترسی نام")),
            ):
                await panel.process_customer_invite_management_name(
                    denied_name_message,
                    _State(),
                    user,
                )

            invalid_name_message = _message()
            with (
                patch(
                    "bot.handlers.panel._customer_invite_access_allowed",
                    new=AsyncMock(return_value=(True, None)),
                ),
                patch(
                    "bot.handlers.panel.normalize_customer_invite_management_name",
                    side_effect=ValueError("نام نامعتبر"),
                ),
            ):
                await panel.process_customer_invite_management_name(
                    invalid_name_message,
                    _State(),
                    user,
                )

            valid_name_message = _message()
            with (
                patch(
                    "bot.handlers.panel._customer_invite_access_allowed",
                    new=AsyncMock(return_value=(True, None)),
                ),
                patch(
                    "bot.handlers.panel.normalize_customer_invite_management_name",
                    return_value="مشتری تست",
                ),
            ):
                await panel.process_customer_invite_management_name(
                    valid_name_message,
                    _State(),
                    user,
                )

            denied_mobile_message = _message()
            with patch(
                "bot.handlers.panel._customer_invite_access_allowed",
                new=AsyncMock(return_value=(False, "عدم دسترسی موبایل")),
            ):
                await panel.process_customer_invite_mobile(
                    denied_mobile_message,
                    _State(),
                    user,
                )

            invalid_mobile_message = _message()
            with (
                patch(
                    "bot.handlers.panel._customer_invite_access_allowed",
                    new=AsyncMock(return_value=(True, None)),
                ),
                patch(
                    "bot.handlers.panel.normalize_customer_invite_mobile",
                    side_effect=ValueError("موبایل نامعتبر"),
                ),
            ):
                await panel.process_customer_invite_mobile(
                    invalid_mobile_message,
                    _State(),
                    user,
                )

            incomplete_message = _message()
            with (
                patch(
                    "bot.handlers.panel._customer_invite_access_allowed",
                    new=AsyncMock(return_value=(True, None)),
                ),
                patch(
                    "bot.handlers.panel.normalize_customer_invite_mobile",
                    return_value="09123456789",
                ),
            ):
                await panel.process_customer_invite_mobile(
                    incomplete_message,
                    _State(),
                    user,
                )

            valid_mobile_message = _message()
            with (
                patch(
                    "bot.handlers.panel._customer_invite_access_allowed",
                    new=AsyncMock(return_value=(True, None)),
                ),
                patch(
                    "bot.handlers.panel.normalize_customer_invite_mobile",
                    return_value="09123456789",
                ),
            ):
                await panel.process_customer_invite_mobile(
                    valid_mobile_message,
                    _State({"customer_invite_management_name": "مشتری تست"}),
                    user,
                )

            confirm_data = {
                "customer_invite_owner_id": user.id,
                "customer_invite_management_name": "مشتری تست",
                "customer_invite_mobile_number": "09123456789",
            }
            with (
                patch(
                    "bot.handlers.panel._customer_invite_access_allowed",
                    new=AsyncMock(return_value=(True, None)),
                ),
                patch(
                    "bot.handlers.panel.check_customer_invite_sync_ready",
                    new=AsyncMock(
                        return_value=SimpleNamespace(
                            ready=False,
                            message="همگام‌سازی تایید آماده نیست",
                        )
                    ),
                ),
            ):
                await panel.confirm_customer_invite_tier1(
                    callback,
                    _State(confirm_data),
                    user,
                )

            with (
                patch(
                    "bot.handlers.panel._customer_invite_access_allowed",
                    new=AsyncMock(return_value=(True, None)),
                ),
                patch(
                    "bot.handlers.panel.check_customer_invite_sync_ready",
                    new=AsyncMock(
                        return_value=SimpleNamespace(ready=True, message=None)
                    ),
                ),
                patch(
                    "bot.handlers.panel.forward_customer_invite_to_iran",
                    new=AsyncMock(side_effect=RuntimeError("forward failed")),
                ),
            ):
                await panel.confirm_customer_invite_tier1(
                    callback,
                    _State(confirm_data),
                    user,
                )

            with (
                patch(
                    "bot.handlers.panel._customer_invite_access_allowed",
                    new=AsyncMock(return_value=(True, None)),
                ),
                patch(
                    "bot.handlers.panel.check_customer_invite_sync_ready",
                    new=AsyncMock(
                        return_value=SimpleNamespace(ready=True, message=None)
                    ),
                ),
                patch(
                    "bot.handlers.panel.forward_customer_invite_to_iran",
                    new=AsyncMock(
                        return_value=(
                            201,
                            {"invitation_token": "CUST-pending"},
                        )
                    ),
                ),
                patch(
                    "bot.handlers.panel._wait_for_customer_invite_projection",
                    new=AsyncMock(return_value=False),
                ),
            ):
                await panel.confirm_customer_invite_tier1(
                    callback,
                    _State(confirm_data),
                    user,
                )

            await panel.cancel_customer_invite_tier1(
                callback,
                _State(confirm_data),
                user,
            )

        self.assertEqual(
            [call.kwargs["source_key"] for call in enqueue_callback.await_args_list],
            [
                "panel-invite-start-sync-unavailable",
                "panel-invite-name-prompt",
                "panel-invite-confirm-sync-unavailable",
                "panel-invite-forward-error",
                "panel-invite-projection-pending",
                "panel-invite-cancelled",
            ],
        )
        self.assertEqual(
            [call.kwargs["source_key"] for call in enqueue_incoming.await_args_list],
            [
                "panel-invite-name-access-denied",
                "panel-invite-name-invalid",
                "panel-invite-mobile-prompt",
                "panel-invite-mobile-access-denied",
                "panel-invite-mobile-invalid",
                "panel-invite-state-incomplete",
                "panel-invite-confirm-prompt",
            ],
        )
        self.assertTrue(enqueue_callback.await_args_list[1].kwargs["temporary_context_keyboard"])
        self.assertTrue(enqueue_incoming.await_args_list[1].kwargs["temporary_context_keyboard"])
        self.assertTrue(enqueue_incoming.await_args_list[-1].kwargs["temporary_context_keyboard"])
        self.assertEqual(enqueue_incoming.await_args_list[-1].kwargs["parse_mode"], "Markdown")
        callback_direct_answer.assert_not_awaited()
        for message in (
            denied_name_message,
            invalid_name_message,
            valid_name_message,
            denied_mobile_message,
            invalid_mobile_message,
            incomplete_message,
            valid_mobile_message,
        ):
            message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
