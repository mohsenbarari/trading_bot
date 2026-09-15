"""Direct characterization tests for overtime requester cancel entry guards and success routing.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-REQUEST-CALLBACK-QUERY-OFFER-OVERTIME-CALLBACKS-HANDLE-OVERTIME-REQUESTER-CANCEL-L74

Provenance & Scope:
This module provides direct characterization for the entry guards, pre-mutation guards,
and matched-requester success routing (remote-home forwarding and local-home cancellation
with absent or present owner) of the real `handle_overtime_requester_cancel` handler. It does not update
registries or close the seed, blocker, or Stage. Evidenced exclusions apply: this unit
does not characterize durable database behavior, origin policy, notification audience,
side-effect retry, blocker closure, or Stage closure.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Requirement 1: Install the fail-closed network fence before handler imports.
install_p1_00_network_fence()

from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.offer_overtime_callbacks import (
    handle_overtime_requester_cancel,
)
from bot.overtime_request_status import (
    edit_owner_overtime_approval_message,
    empty_inline_keyboard,
    owner_terminal_text_for_reason,
)
from core.offer_lifecycle import (
    read_normal_lifetime_minutes,
)
from core.offer_overtime_bot_copy import (
    M15_REQUESTER_STATUS_CANCELLED,
    M33_OWNER_ALREADY_TERMINAL_MESSAGE,
)
from core.server_routing import (
    normalize_server,
)
from core.telegram_delivery_overtime_requester_status_contract import (
    parse_overtime_requester_cancel_callback_data,
)
from models.offer_request import (
    OfferRequestWorkflow,
)
from models.user import User


class _MockAsyncSessionContext:
    def __init__(self, session: MagicMock, order: list[str]) -> None:
        self.session = session
        self.order = order

    async def __aenter__(self) -> MagicMock:
        self.order.append("session_enter")
        return self.session

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> bool:
        self.order.append("session_exit")
        return False


class OvertimeRequesterCancelGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_unauthenticated_guard_answers_m33_without_reading_callback_data_or_downstream_work(
        self,
    ):
        # Requirement 2: user is None, callback fixture lacks .data attribute.
        callback = SimpleNamespace()
        user = None
        bot = MagicMock()

        fail_session = MagicMock(
            side_effect=AssertionError("AsyncSessionLocal must not be called")
        )
        mock_forward = AsyncMock()
        mock_edit = AsyncMock()

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new_callable=AsyncMock,
            ) as mock_answer,
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_requester_cancel_callback_data",
            ) as mock_parser,
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                new=fail_session,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                new=mock_forward,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                new=mock_edit,
            ),
        ):
            await handle_overtime_requester_cancel(callback, user, bot)

            self.assertFalse(hasattr(callback, "data"))
            mock_answer.assert_awaited_once_with(
                callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True
            )
            mock_parser.assert_not_called()
            fail_session.assert_not_called()
            mock_forward.assert_not_awaited()
            mock_edit.assert_not_awaited()
            bot.assert_not_called()

    async def test_malformed_callback_guard_answers_m33_without_session_or_downstream_work(
        self,
    ):
        # Requirement 3: authenticated user, malformed callback data.
        callback = SimpleNamespace(data="otc:12:cancel")
        user = SimpleNamespace(id=71)
        bot = MagicMock()

        fail_session = MagicMock(
            side_effect=AssertionError("AsyncSessionLocal must not be called")
        )
        mock_forward = AsyncMock()
        mock_edit = AsyncMock()

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new_callable=AsyncMock,
            ) as mock_answer,
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_requester_cancel_callback_data",
                wraps=parse_overtime_requester_cancel_callback_data,
            ) as wrapped_parser,
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                new=fail_session,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                new=mock_forward,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                new=mock_edit,
            ),
        ):
            await handle_overtime_requester_cancel(callback, user, bot)

            wrapped_parser.assert_called_once_with("otc:12:cancel")
            parsed_result = parse_overtime_requester_cancel_callback_data(
                "otc:12:cancel"
            )
            self.assertIsNone(parsed_result)
            self.assertIsNone(
                wrapped_parser.spy_return
                if hasattr(wrapped_parser, "spy_return")
                else parsed_result
            )
            mock_answer.assert_awaited_once_with(
                callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True
            )
            fail_session.assert_not_called()
            mock_forward.assert_not_awaited()
            mock_edit.assert_not_awaited()
            bot.assert_not_called()


    async def test_valid_callback_pre_mutation_ledger_and_requester_guards_answer_m33_with_single_unlocked_load(
        self,
    ):
        cases = (
            ("missing_ledger", None),
            (
                "direct_workflow_ledger",
                SimpleNamespace(
                    workflow_kind=OfferRequestWorkflow.DIRECT,
                    requester_user_id=71,
                ),
            ),
            (
                "mismatched_requester_ledger",
                SimpleNamespace(
                    workflow_kind=OfferRequestWorkflow.OVERTIME,
                    requester_user_id=72,
                ),
            ),
        )

        for case_name, case_ledger in cases:
            with self.subTest(case=case_name):
                callback = SimpleNamespace(
                    data="otc:req_0123456789abcdef:cancel"
                )
                user = SimpleNamespace(id=71)
                bot = MagicMock()
                order: list[str] = []

                mock_session = MagicMock()
                mock_session.execute = AsyncMock()
                mock_session.get = AsyncMock()
                mock_session.rollback = AsyncMock()
                mock_session.commit = AsyncMock()

                session_ctx = _MockAsyncSessionContext(mock_session, order)
                session_factory = MagicMock(return_value=session_ctx)

                def tracked_parse(callback_data: Any) -> str | None:
                    order.append("parse")
                    return parse_overtime_requester_cancel_callback_data(
                        callback_data
                    )

                mock_parser = MagicMock(side_effect=tracked_parse)

                async def tracked_load(
                    session: Any, public_id: str, *, for_update: bool = False
                ) -> Any:
                    order.append("load")
                    return case_ledger

                mock_load = AsyncMock(side_effect=tracked_load)

                async def tracked_answer(
                    cb: Any, text: str, *, alert: bool = False
                ) -> None:
                    order.append("answer")

                mock_answer = AsyncMock(side_effect=tracked_answer)

                mock_normalize_server = MagicMock()
                mock_current_server = MagicMock()
                mock_get_settings = AsyncMock()
                mock_cancel = AsyncMock()
                mock_forward = AsyncMock()
                mock_edit = AsyncMock()
                mock_edit_owner = AsyncMock()

                with (
                    patch(
                        "bot.handlers.offer_overtime_callbacks.parse_overtime_requester_cancel_callback_data",
                        new=mock_parser,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                        new=session_factory,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                        new=mock_load,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks._answer",
                        new=mock_answer,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.normalize_server",
                        new=mock_normalize_server,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.current_server",
                        new=mock_current_server,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                        new=mock_get_settings,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.cancel_by_requester",
                        new=mock_cancel,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                        new=mock_forward,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                        new=mock_edit,
                    ),
                    patch(
                        "bot.handlers.offer_overtime_callbacks.edit_owner_overtime_approval_message",
                        new=mock_edit_owner,
                    ),
                ):
                    await handle_overtime_requester_cancel(callback, user, bot)

                self.assertEqual(
                    order,
                    ["parse", "session_enter", "load", "answer", "session_exit"],
                )
                mock_parser.assert_called_once_with(
                    "otc:req_0123456789abcdef:cancel"
                )
                self.assertEqual(
                    parse_overtime_requester_cancel_callback_data(
                        "otc:req_0123456789abcdef:cancel"
                    ),
                    "req_0123456789abcdef",
                )
                session_factory.assert_called_once_with()
                mock_load.assert_awaited_once_with(
                    mock_session, "req_0123456789abcdef", for_update=False
                )
                self.assertEqual(mock_load.await_count, 1)
                self.assertFalse(
                    any(
                        call.kwargs.get("for_update") is True
                        for call in mock_load.await_args_list
                    )
                )
                mock_answer.assert_awaited_once_with(
                    callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True
                )
                self.assertEqual(mock_answer.await_count, 1)
                mock_normalize_server.assert_not_called()
                mock_current_server.assert_not_called()
                mock_session.execute.assert_not_called()
                mock_session.get.assert_not_called()
                mock_session.rollback.assert_not_called()
                mock_session.commit.assert_not_called()
                mock_get_settings.assert_not_called()
                mock_cancel.assert_not_called()
                mock_forward.assert_not_called()
                mock_edit.assert_not_called()
                mock_edit_owner.assert_not_called()
                bot.assert_not_called()

    async def test_matched_requester_overtime_remote_home_success_scenario(
        self,
    ):
        # Requirement 2: Matched-requester OVERTIME remote-home success.
        public_id = "req_0123456789abcdef"
        callback = SimpleNamespace(
            data=f"otc:{public_id}:cancel"
        )
        user = SimpleNamespace(id=71)
        bot = MagicMock()
        order: list[str] = []

        unlocked_ledger = SimpleNamespace(
            request_public_id=public_id,
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=71,
            request_home_server="iran",
            request_source_server="foreign",
        )

        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.get = AsyncMock()

        async def tracked_rollback() -> None:
            order.append("rollback")

        mock_session.rollback = AsyncMock(side_effect=tracked_rollback)

        async def tracked_commit() -> None:
            order.append("commit")

        mock_session.commit = AsyncMock(side_effect=tracked_commit)

        session_ctx = _MockAsyncSessionContext(mock_session, order)
        session_factory = MagicMock(return_value=session_ctx)

        def tracked_parse(callback_data: Any) -> str | None:
            order.append("parse")
            return parse_overtime_requester_cancel_callback_data(callback_data)

        mock_parser = MagicMock(side_effect=tracked_parse)

        fixtures_by_for_update = {
            False: unlocked_ledger,
        }

        async def tracked_load(
            session: Any, pid: str, *, for_update: bool = False
        ) -> Any:
            if pid != public_id:
                raise AssertionError(f"Unexpected public_id: {pid}")
            if for_update not in fixtures_by_for_update:
                raise AssertionError(
                    f"Unexpected for_update flag: {for_update}; locked reload is forbidden"
                )
            order.append("load_unlocked")
            return fixtures_by_for_update[for_update]

        mock_load = AsyncMock(side_effect=tracked_load)

        receipt = {
            "request_public_id": public_id,
            "result_status": "overtime_cancelled_by_requester",
            "replayed": False,
        }

        async def tracked_forward(
            target_server: str, payload: dict[str, Any]
        ) -> tuple[int, Any]:
            order.append("forward")
            return 200, receipt

        mock_forward = AsyncMock(side_effect=tracked_forward)

        async def tracked_edit(*args: Any, **kwargs: Any) -> None:
            order.append("edit_forwarded")

        mock_edit = AsyncMock(side_effect=tracked_edit)

        async def tracked_answer(
            cb: Any, text: str, *, alert: bool = False
        ) -> None:
            order.append("answer")

        mock_answer = AsyncMock(side_effect=tracked_answer)

        mock_get_settings = AsyncMock()
        mock_cancel = AsyncMock()
        mock_edit_owner = AsyncMock()

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_requester_cancel_callback_data",
                new=mock_parser,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                new=session_factory,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                new=mock_load,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.current_server",
                return_value="foreign",
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                new=mock_forward,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                new=mock_edit,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new=mock_answer,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                new=mock_get_settings,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.cancel_by_requester",
                new=mock_cancel,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_owner_overtime_approval_message",
                new=mock_edit_owner,
            ),
        ):
            await handle_overtime_requester_cancel(callback, user, bot)

        # Assert critical ordering through session exit
        self.assertEqual(
            order,
            [
                "parse",
                "session_enter",
                "load_unlocked",
                "rollback",
                "forward",
                "edit_forwarded",
                "commit",
                "answer",
                "session_exit",
            ],
        )

        # Prove the first ledger load is unlocked
        mock_load.assert_awaited_once_with(
            mock_session, public_id, for_update=False
        )
        self.assertFalse(
            any(
                call.kwargs.get("for_update") is True
                for call in mock_load.await_args_list
            )
        )

        # Prove normalized home differs from fixed current server and normalized source equals it
        self.assertNotEqual(
            normalize_server(unlocked_ledger.request_home_server, "foreign"),
            "foreign",
        )
        self.assertEqual(
            normalize_server(unlocked_ledger.request_source_server, "foreign"),
            "foreign",
        )

        # Prove rollback occurs before forwarding and both occur once
        mock_session.rollback.assert_awaited_once_with()
        self.assertLess(order.index("rollback"), order.index("forward"))

        # Prove forwarded target and payload are exact
        mock_forward.assert_awaited_once_with(
            "iran",
            {
                "request_public_id": public_id,
                "requester_user_id": 71,
                "source_server": "foreign",
            },
        )

        # Prove no locked reload, settings read, local cancel, owner lookup, or owner-message edit occurs
        mock_get_settings.assert_not_called()
        mock_cancel.assert_not_called()
        mock_session.get.assert_not_called()
        mock_edit_owner.assert_not_called()
        bot.assert_not_called()

        # On sub-400 receipt, prove requester edit arguments
        mock_edit.assert_awaited_once_with(
            callback,
            user,
            M15_REQUESTER_STATUS_CANCELLED,
            source_key="ot-cancel-edit-forwarded",
            reply_markup=empty_inline_keyboard(),
            session=mock_session,
            commit=False,
        )
        self.assertIs(mock_edit.call_args.args[0], callback)
        self.assertIs(mock_edit.call_args.args[1], user)
        self.assertEqual(
            mock_edit.call_args.args[2], M15_REQUESTER_STATUS_CANCELLED
        )
        self.assertEqual(
            mock_edit.call_args.kwargs["source_key"], "ot-cancel-edit-forwarded"
        )
        self.assertEqual(
            mock_edit.call_args.kwargs["reply_markup"], empty_inline_keyboard()
        )
        self.assertIs(mock_edit.call_args.kwargs["session"], mock_session)
        self.assertIs(mock_edit.call_args.kwargs["commit"], False)

        # Prove session commits once and callback answered once with M15 and alert=False
        mock_session.commit.assert_awaited_once_with()
        mock_answer.assert_awaited_once_with(
            callback, M15_REQUESTER_STATUS_CANCELLED, alert=False
        )

    async def test_matched_requester_overtime_local_home_success_scenario_with_absent_owner(
        self,
    ):
        # Requirement 3: Matched-requester OVERTIME local-home success with absent owner.
        public_id = "req_0123456789abcdef"
        callback = SimpleNamespace(
            data=f"otc:{public_id}:cancel"
        )
        user = SimpleNamespace(id=71)
        bot = MagicMock()
        order: list[str] = []

        unlocked_ledger = SimpleNamespace(
            request_public_id=public_id,
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=71,
            request_home_server="iran",
            request_source_server="iran",
            offer_owner_user_id=None,
        )
        locked_ledger = SimpleNamespace(
            request_public_id=public_id,
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=71,
            request_home_server="iran",
            request_source_server="iran",
            offer_owner_user_id=None,
        )

        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.get = AsyncMock()
        mock_session.rollback = AsyncMock()

        async def tracked_commit() -> None:
            order.append("commit")

        mock_session.commit = AsyncMock(side_effect=tracked_commit)

        session_ctx = _MockAsyncSessionContext(mock_session, order)
        session_factory = MagicMock(return_value=session_ctx)

        def tracked_parse(callback_data: Any) -> str | None:
            order.append("parse")
            return parse_overtime_requester_cancel_callback_data(callback_data)

        mock_parser = MagicMock(side_effect=tracked_parse)

        fixtures_by_for_update = {
            False: unlocked_ledger,
            True: locked_ledger,
        }

        async def tracked_load(
            session: Any, pid: str, *, for_update: bool = False
        ) -> Any:
            if pid != public_id:
                raise AssertionError(f"Unexpected public_id: {pid}")
            if for_update not in fixtures_by_for_update:
                raise AssertionError(
                    f"Unexpected for_update flag: {for_update}"
                )
            order.append("load_locked" if for_update else "load_unlocked")
            return fixtures_by_for_update[for_update]

        mock_load = AsyncMock(side_effect=tracked_load)

        settings_fixture = SimpleNamespace(offer_expiry_minutes=25)
        expected_normal_lifetime = read_normal_lifetime_minutes(
            settings_fixture
        )
        self.assertEqual(expected_normal_lifetime, 25)

        async def tracked_get_settings() -> Any:
            order.append("settings_read")
            return settings_fixture

        mock_get_settings = AsyncMock(side_effect=tracked_get_settings)

        fixed_now = datetime(2026, 9, 15, 12, 0, 0)
        mock_datetime = MagicMock()
        mock_datetime.utcnow.return_value = fixed_now

        async def tracked_cancel(
            session: Any,
            ledger: Any,
            *,
            requester_user_id: int,
            now: Any,
            normal_lifetime_minutes: int,
        ) -> Any:
            order.append("local_cancel")
            return ledger

        mock_cancel = AsyncMock(side_effect=tracked_cancel)

        async def tracked_edit(*args: Any, **kwargs: Any) -> None:
            order.append("edit_local")

        mock_edit = AsyncMock(side_effect=tracked_edit)

        async def tracked_answer(
            cb: Any, text: str, *, alert: bool = False
        ) -> None:
            order.append("answer")

        mock_answer = AsyncMock(side_effect=tracked_answer)

        mock_forward = AsyncMock()
        mock_edit_owner = AsyncMock()

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_requester_cancel_callback_data",
                new=mock_parser,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                new=session_factory,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                new=mock_load,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.current_server",
                return_value="iran",
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                new=mock_get_settings,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.datetime",
                new=mock_datetime,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.cancel_by_requester",
                new=mock_cancel,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                new=mock_edit,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new=mock_answer,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                new=mock_forward,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_owner_overtime_approval_message",
                new=mock_edit_owner,
            ),
        ):
            await handle_overtime_requester_cancel(callback, user, bot)

        # Assert critical ordering through session exit and post-exit answer
        self.assertEqual(
            order,
            [
                "parse",
                "session_enter",
                "load_unlocked",
                "load_locked",
                "settings_read",
                "local_cancel",
                "edit_local",
                "commit",
                "session_exit",
                "answer",
            ],
        )

        # Prove one unlocked load followed by one locked load selected by for_update
        self.assertEqual(mock_load.await_count, 2)
        mock_load.assert_has_awaits(
            [
                unittest.mock.call(mock_session, public_id, for_update=False),
                unittest.mock.call(mock_session, public_id, for_update=True),
            ]
        )
        self.assertFalse(mock_load.await_args_list[0].kwargs["for_update"])
        self.assertTrue(mock_load.await_args_list[1].kwargs["for_update"])

        # Prove one settings read
        mock_get_settings.assert_awaited_once_with()

        # Prove one cancel_by_requester call with the same locked ledger
        mock_cancel.assert_awaited_once_with(
            mock_session,
            locked_ledger,
            requester_user_id=71,
            now=fixed_now,
            normal_lifetime_minutes=expected_normal_lifetime,
        )
        self.assertIs(mock_cancel.call_args.args[1], locked_ledger)
        self.assertEqual(mock_cancel.call_args.kwargs["requester_user_id"], 71)
        self.assertEqual(mock_cancel.call_args.kwargs["now"], fixed_now)
        self.assertEqual(
            mock_cancel.call_args.kwargs["normal_lifetime_minutes"],
            expected_normal_lifetime,
        )
        mock_datetime.utcnow.assert_called_once_with()

        # Prove no rollback, forwarding, or owner lookup/edit
        mock_session.rollback.assert_not_called()
        mock_forward.assert_not_called()
        mock_session.get.assert_not_called()
        mock_edit_owner.assert_not_called()
        bot.assert_not_called()

        # Prove requester edit arguments
        mock_edit.assert_awaited_once_with(
            callback,
            user,
            M15_REQUESTER_STATUS_CANCELLED,
            source_key="ot-cancel-edit",
            reply_markup=empty_inline_keyboard(),
            session=mock_session,
            commit=False,
        )
        self.assertIs(mock_edit.call_args.args[0], callback)
        self.assertIs(mock_edit.call_args.args[1], user)
        self.assertEqual(
            mock_edit.call_args.args[2], M15_REQUESTER_STATUS_CANCELLED
        )
        self.assertEqual(
            mock_edit.call_args.kwargs["source_key"], "ot-cancel-edit"
        )
        self.assertEqual(
            mock_edit.call_args.kwargs["reply_markup"], empty_inline_keyboard()
        )
        self.assertIs(mock_edit.call_args.kwargs["session"], mock_session)
        self.assertIs(mock_edit.call_args.kwargs["commit"], False)

        # Prove commit once, session exit, and answer once with M15 and alert=False
        mock_session.commit.assert_awaited_once_with()
        mock_answer.assert_awaited_once_with(
            callback, M15_REQUESTER_STATUS_CANCELLED, alert=False
        )

    async def test_matched_requester_overtime_local_home_success_scenario_with_present_owner(
        self,
    ):
        # Requirement 2 & 3: Matched-requester OVERTIME local-home success with present owner.
        public_id = "req_0123456789abcdef"
        callback = SimpleNamespace(data=f"otc:{public_id}:cancel")
        user = SimpleNamespace(id=71)
        bot = MagicMock()
        order: list[str] = []

        unlocked_ledger = SimpleNamespace(
            request_public_id=public_id,
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=71,
            request_home_server="iran",
            request_source_server="iran",
            offer_owner_user_id=88,
        )
        locked_ledger = SimpleNamespace(
            request_public_id=public_id,
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=71,
            request_home_server="iran",
            request_source_server="iran",
            offer_owner_user_id=88,
        )
        self.assertIsNot(unlocked_ledger, locked_ledger)

        owner = User(
            id=88,
            account_name="owner_user",
            mobile_number="+989120000088",
            normalized_account_name="owner_user",
            normalized_mobile_number="+989120000088",
            full_name="Owner User",
            address="Tehran, Iran",
            telegram_id=888888,
        )

        session = AsyncSession()

        async def tracked_get(entity: Any, ident: Any, **kwargs: Any) -> Any:
            order.append("owner_lookup")
            if entity is User and ident == 88:
                return owner
            raise AssertionError(
                f"AsyncSession.get accepted only (User, 88), called with entity={entity}, ident={ident}, kwargs={kwargs}"
            )

        session.get = AsyncMock(side_effect=tracked_get)

        async def tracked_commit() -> None:
            order.append("commit")

        session.commit = AsyncMock(side_effect=tracked_commit)

        def _fail_session_call(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Unexpected AsyncSession call reached")

        session.execute = AsyncMock(side_effect=_fail_session_call)
        session.scalar = AsyncMock(side_effect=_fail_session_call)
        session.scalars = AsyncMock(side_effect=_fail_session_call)
        session.rollback = AsyncMock(side_effect=_fail_session_call)

        session_ctx = _MockAsyncSessionContext(session, order)
        session_factory = MagicMock(return_value=session_ctx)

        def tracked_parse(callback_data: Any) -> str | None:
            order.append("parse")
            return parse_overtime_requester_cancel_callback_data(callback_data)

        mock_parser = MagicMock(side_effect=tracked_parse)

        fixtures_by_for_update = {
            False: unlocked_ledger,
            True: locked_ledger,
        }

        async def tracked_load(
            s: Any, pid: str, *, for_update: bool = False
        ) -> Any:
            if pid != public_id:
                raise AssertionError(f"Unexpected public_id: {pid}")
            if for_update not in fixtures_by_for_update:
                raise AssertionError(
                    f"Unexpected for_update flag: {for_update}"
                )
            order.append("load_locked" if for_update else "load_unlocked")
            return fixtures_by_for_update[for_update]

        mock_load = AsyncMock(side_effect=tracked_load)

        settings_fixture = SimpleNamespace(offer_expiry_minutes=25)
        expected_normal_lifetime = read_normal_lifetime_minutes(
            settings_fixture
        )
        self.assertEqual(expected_normal_lifetime, 25)

        async def tracked_get_settings() -> Any:
            order.append("settings_read")
            return settings_fixture

        mock_get_settings = AsyncMock(side_effect=tracked_get_settings)

        fixed_now = datetime(2026, 9, 15, 12, 0, 0)
        mock_datetime = MagicMock()
        mock_datetime.utcnow.return_value = fixed_now

        async def tracked_cancel(
            s: Any,
            l: Any,
            *,
            requester_user_id: int,
            now: Any,
            normal_lifetime_minutes: int,
        ) -> Any:
            order.append("local_cancel")
            return locked_ledger

        mock_cancel = AsyncMock(side_effect=tracked_cancel)

        async def tracked_edit(*args: Any, **kwargs: Any) -> None:
            order.append("edit_local")

        mock_edit = AsyncMock(side_effect=tracked_edit)

        async def tracked_edit_owner(*args: Any, **kwargs: Any) -> None:
            order.append("owner_edit")

        mock_edit_owner = AsyncMock(side_effect=tracked_edit_owner)

        async def tracked_answer(
            cb: Any, text: str, *, alert: bool = False
        ) -> None:
            order.append("answer")

        mock_answer = AsyncMock(side_effect=tracked_answer)

        mock_forward = AsyncMock()

        with (
            patch(
                "bot.handlers.offer_overtime_callbacks.parse_overtime_requester_cancel_callback_data",
                new=mock_parser,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
                new=session_factory,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
                new=mock_load,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.current_server",
                return_value="iran",
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.get_trading_settings_async",
                new=mock_get_settings,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.datetime",
                new=mock_datetime,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.cancel_by_requester",
                new=mock_cancel,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                new=mock_edit,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks._answer",
                new=mock_answer,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                new=mock_forward,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_owner_overtime_approval_message",
                new=mock_edit_owner,
            ),
        ):
            await handle_overtime_requester_cancel(callback, user, bot)

        # Assert critical ordering through session exit and post-exit answer
        self.assertEqual(
            order,
            [
                "parse",
                "session_enter",
                "load_unlocked",
                "load_locked",
                "settings_read",
                "local_cancel",
                "edit_local",
                "owner_lookup",
                "owner_edit",
                "commit",
                "session_exit",
                "answer",
            ],
        )

        # Prove one unlocked load followed by one locked load selected by for_update
        self.assertEqual(mock_load.await_count, 2)
        mock_load.assert_has_awaits(
            [
                unittest.mock.call(session, public_id, for_update=False),
                unittest.mock.call(session, public_id, for_update=True),
            ]
        )
        self.assertFalse(mock_load.await_args_list[0].kwargs["for_update"])
        self.assertTrue(mock_load.await_args_list[1].kwargs["for_update"])

        # Prove one settings read
        mock_get_settings.assert_awaited_once_with()

        # Prove one cancel_by_requester call with the locked ledger
        mock_cancel.assert_awaited_once_with(
            session,
            locked_ledger,
            requester_user_id=71,
            now=fixed_now,
            normal_lifetime_minutes=expected_normal_lifetime,
        )
        self.assertIs(mock_cancel.call_args.args[0], session)
        self.assertIs(mock_cancel.call_args.args[1], locked_ledger)
        self.assertEqual(mock_cancel.call_args.kwargs["requester_user_id"], 71)
        self.assertEqual(mock_cancel.call_args.kwargs["now"], fixed_now)
        self.assertEqual(
            mock_cancel.call_args.kwargs["normal_lifetime_minutes"],
            expected_normal_lifetime,
        )
        mock_datetime.utcnow.assert_called_once_with()

        # Prove requester edit arguments
        mock_edit.assert_awaited_once_with(
            callback,
            user,
            M15_REQUESTER_STATUS_CANCELLED,
            source_key="ot-cancel-edit",
            reply_markup=empty_inline_keyboard(),
            session=session,
            commit=False,
        )
        self.assertIs(mock_edit.call_args.args[0], callback)
        self.assertIs(mock_edit.call_args.args[1], user)
        self.assertEqual(
            mock_edit.call_args.args[2], M15_REQUESTER_STATUS_CANCELLED
        )
        self.assertEqual(
            mock_edit.call_args.kwargs["source_key"], "ot-cancel-edit"
        )
        self.assertEqual(
            mock_edit.call_args.kwargs["reply_markup"], empty_inline_keyboard()
        )
        self.assertIs(mock_edit.call_args.kwargs["session"], session)
        self.assertIs(mock_edit.call_args.kwargs["commit"], False)

        # Prove exact owner lookup via session.get(User, 88)
        session.get.assert_awaited_once_with(User, 88)

        # Prove owner approval message edit arguments
        mock_edit_owner.assert_awaited_once_with(
            session=session,
            owner=owner,
            ledger=locked_ledger,
            text=owner_terminal_text_for_reason(),
            bot=bot,
        )
        self.assertIs(mock_edit_owner.call_args.kwargs["session"], session)
        self.assertIs(mock_edit_owner.call_args.kwargs["owner"], owner)
        self.assertIs(mock_edit_owner.call_args.kwargs["ledger"], locked_ledger)
        self.assertEqual(
            mock_edit_owner.call_args.kwargs["text"],
            owner_terminal_text_for_reason(),
        )
        self.assertIs(mock_edit_owner.call_args.kwargs["bot"], bot)

        # Prove no session rollback, execute, scalar, scalars, or remote forward
        session.rollback.assert_not_called()
        session.execute.assert_not_called()
        session.scalar.assert_not_called()
        session.scalars.assert_not_called()
        mock_forward.assert_not_called()

        # Prove commit once, session exit, and answer once with M15 and alert=False
        session.commit.assert_awaited_once_with()
        mock_answer.assert_awaited_once_with(
            callback, M15_REQUESTER_STATUS_CANCELLED, alert=False
        )

        # Close the AsyncSession after the invocation
        await session.close()


if __name__ == "__main__":
    unittest.main()
