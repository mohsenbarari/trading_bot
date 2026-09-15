"""Direct characterization tests for overtime requester cancel terminal mapping to M33.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-REQUEST-CALLBACK-QUERY-OFFER-OVERTIME-CALLBACKS-HANDLE-OVERTIME-REQUESTER-CANCEL-L74

Subcases:
- OvertimeRequestErrorCode.NOT_REQUESTER
- OvertimeRequestErrorCode.ALREADY_TERMINAL

Outcome:
Each error code is hidden behind exactly _answer(callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True).

Exclusions:
Evidenced exclusions apply: service mutation correctness, database durability,
successful cancellation, owner notification, remote forwarding behavior,
origin SUCCESS/policy/notification/retry cells, seed/blocker closure,
reconciliation, and Stage closure.
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

# Requirement 1: Install fail-closed P1-00 network fence before handler/application imports.
install_p1_00_network_fence()

from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_p1_00b_characterization_parse_offer_text_results import (
    StrictFakeDB,
)
from bot.handlers.offer_overtime_callbacks import (
    handle_overtime_requester_cancel,
)
from core.offer_lifecycle import (
    read_normal_lifetime_minutes,
)
from core.offer_overtime_bot_copy import (
    M33_OWNER_ALREADY_TERMINAL_MESSAGE,
)
from core.server_routing import (
    SERVER_FOREIGN,
)
from core.services.offer_overtime_request_service import (
    OvertimeRequestError,
    OvertimeRequestErrorCode,
    cancel_by_requester as authoritative_cancel_by_requester,
)
from core.telegram_delivery_overtime_requester_status_contract import (
    parse_overtime_requester_cancel_callback_data,
)
from models.offer_request import (
    OfferRequestStatus,
    OfferRequestWorkflow,
)


class _MockAsyncSessionContext:
    def __init__(self, session: Any, order: list[str]) -> None:
        self.session = session
        self.order = order

    async def __aenter__(self) -> Any:
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


class OvertimeRequesterCancelTerminalMappingCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def _run_terminal_mapping_subcase(
        self,
        error_code: OvertimeRequestErrorCode,
        noncanonical_detail: str,
    ) -> None:
        public_id = "req_0123456789abcdef"
        callback = SimpleNamespace(data=f"otc:{public_id}:cancel")
        user = SimpleNamespace(id=71)
        bot = MagicMock()
        order: list[str] = []

        unlocked_ledger = SimpleNamespace(
            request_public_id=public_id,
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=71,
            request_home_server=SERVER_FOREIGN,
            offer_owner_user_id=88,
        )
        locked_ledger = SimpleNamespace(
            request_public_id=public_id,
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=71,
            request_home_server=SERVER_FOREIGN,
            offer_owner_user_id=88,
        )
        self.assertIsNot(unlocked_ledger, locked_ledger)

        session = AsyncSession()

        def _fail_session_call(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Unexpected AsyncSession call reached")

        session.execute = AsyncMock(side_effect=_fail_session_call)
        session.scalar = AsyncMock(side_effect=_fail_session_call)
        session.scalars = AsyncMock(side_effect=_fail_session_call)
        session.get = AsyncMock(side_effect=_fail_session_call)
        session.flush = AsyncMock(side_effect=_fail_session_call)
        session.rollback = AsyncMock(side_effect=_fail_session_call)
        session.commit = AsyncMock(side_effect=_fail_session_call)

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
            order.append("cancel_call")
            raise OvertimeRequestError(
                error_code,
                noncanonical_detail,
            )

        mock_cancel = AsyncMock(side_effect=tracked_cancel)

        async def tracked_answer(
            cb: Any, text: str, *, alert: bool = False
        ) -> None:
            order.append("answer")

        mock_answer = AsyncMock(side_effect=tracked_answer)

        mock_edit = AsyncMock()
        mock_empty_keyboard = MagicMock()
        mock_edit_owner = AsyncMock()
        mock_forward = AsyncMock()

        try:
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
                    return_value=SERVER_FOREIGN,
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
                    "bot.handlers.offer_overtime_callbacks._answer",
                    new=mock_answer,
                ),
                patch(
                    "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                    new=mock_edit,
                ),
                patch(
                    "bot.handlers.offer_overtime_callbacks.empty_inline_keyboard",
                    new=mock_empty_keyboard,
                ),
                patch(
                    "bot.handlers.offer_overtime_callbacks.edit_owner_overtime_approval_message",
                    new=mock_edit_owner,
                ),
                patch(
                    "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                    new=mock_forward,
                ),
            ):
                await handle_overtime_requester_cancel(callback, user, bot)

            # Assert execution order and normal async context exit
            self.assertEqual(
                order,
                [
                    "parse",
                    "session_enter",
                    "load_unlocked",
                    "load_locked",
                    "settings_read",
                    "cancel_call",
                    "answer",
                    "session_exit",
                ],
            )

            # Prove one unlocked load followed by one locked load
            self.assertEqual(mock_load.await_count, 2)
            mock_load.assert_has_awaits(
                [
                    unittest.mock.call(
                        session, public_id, for_update=False
                    ),
                    unittest.mock.call(
                        session, public_id, for_update=True
                    ),
                ]
            )
            self.assertFalse(
                mock_load.await_args_list[0].kwargs["for_update"]
            )
            self.assertTrue(
                mock_load.await_args_list[1].kwargs["for_update"]
            )

            # Prove one settings read
            mock_get_settings.assert_awaited_once_with()

            # Prove cancel_by_requester call shape
            mock_cancel.assert_awaited_once_with(
                session,
                locked_ledger,
                requester_user_id=71,
                now=fixed_now,
                normal_lifetime_minutes=expected_normal_lifetime,
            )
            self.assertIs(mock_cancel.call_args.args[0], session)
            self.assertIs(mock_cancel.call_args.args[1], locked_ledger)
            self.assertEqual(
                mock_cancel.call_args.kwargs["requester_user_id"], 71
            )
            self.assertEqual(mock_cancel.call_args.kwargs["now"], fixed_now)
            self.assertIsInstance(
                mock_cancel.call_args.kwargs["now"], datetime
            )
            self.assertEqual(
                mock_cancel.call_args.kwargs["normal_lifetime_minutes"],
                expected_normal_lifetime,
            )
            mock_datetime.utcnow.assert_called_once_with()

            # Prove exactly one _answer with M33 and alert=True, hiding noncanonical detail
            mock_answer.assert_awaited_once_with(
                callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True
            )
            self.assertEqual(mock_answer.await_count, 1)
            self.assertNotEqual(
                mock_answer.call_args.args[1], noncanonical_detail
            )
            self.assertNotIn(
                noncanonical_detail, str(mock_answer.call_args)
            )

            # Prove downstream suppression after caught error
            mock_edit.assert_not_called()
            mock_empty_keyboard.assert_not_called()
            mock_edit_owner.assert_not_called()
            mock_forward.assert_not_called()
            bot.assert_not_called()
            session.execute.assert_not_called()
            session.scalar.assert_not_called()
            session.scalars.assert_not_called()
            session.get.assert_not_called()
            session.flush.assert_not_called()
            session.rollback.assert_not_called()
            session.commit.assert_not_called()
        finally:
            await session.close()

    async def test_overtime_requester_cancel_not_requester_error_maps_to_m33(
        self,
    ) -> None:
        await self._run_terminal_mapping_subcase(
            OvertimeRequestErrorCode.NOT_REQUESTER,
            "NONCANONICAL_DETAIL_NOT_REQUESTER_EXPLICIT_TEST_REJECTION",
        )

    async def test_overtime_requester_cancel_already_terminal_error_maps_to_m33(
        self,
    ) -> None:
        await self._run_terminal_mapping_subcase(
            OvertimeRequestErrorCode.ALREADY_TERMINAL,
            "NONCANONICAL_DETAIL_ALREADY_TERMINAL_EXPLICIT_TEST_REJECTION",
        )

    async def test_overtime_requester_cancel_illegal_transition_relays_source_detail(
        self,
    ) -> None:
        """Characterize ILLEGAL_TRANSITION relaying source detail via _answer.

        Stage: P1-00b
        Assignment: WA-P1-00B-FREEZE-20260904-001
        Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
        Scenario: overtime_requester_cancel_illegal_transition_detail_mapping

        Exclusions:
        Evidenced exclusions apply: service mutation correctness, database durability,
        successful cancellation, owner notification, remote forwarding behavior,
        origin SUCCESS/policy/notification/retry cells, seed/blocker closure,
        reconciliation, and Stage closure.
        """
        public_id = "req_0123456789abcdef"
        callback = SimpleNamespace(data=f"otc:{public_id}:cancel")
        user = SimpleNamespace(id=71)
        bot = MagicMock()
        order: list[str] = []

        unlocked_ledger = SimpleNamespace(
            request_public_id=public_id,
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=71,
            request_home_server=SERVER_FOREIGN,
            offer_owner_user_id=88,
            result_status=OfferRequestStatus.OVERTIME_QUEUED,
        )
        locked_ledger = SimpleNamespace(
            request_public_id=public_id,
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            requester_user_id=71,
            request_home_server=SERVER_FOREIGN,
            offer_owner_user_id=88,
            result_status=OfferRequestStatus.RECEIVED,
        )
        self.assertIsNot(unlocked_ledger, locked_ledger)

        session = StrictFakeDB()
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
            order.append("cancel_call")
            return await authoritative_cancel_by_requester(
                s,
                l,
                requester_user_id=requester_user_id,
                now=now,
                flush=False,
                promote_next=False,
                normal_lifetime_minutes=normal_lifetime_minutes,
            )

        mock_cancel = AsyncMock(side_effect=tracked_cancel)

        async def tracked_answer(
            cb: Any, text: str, *, alert: bool = False
        ) -> None:
            order.append("answer")

        mock_answer = AsyncMock(side_effect=tracked_answer)

        mock_edit = AsyncMock()
        mock_empty_keyboard = MagicMock()
        mock_edit_owner = AsyncMock()
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
                return_value=SERVER_FOREIGN,
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
                "bot.handlers.offer_overtime_callbacks._answer",
                new=mock_answer,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
                new=mock_edit,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.empty_inline_keyboard",
                new=mock_empty_keyboard,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.edit_owner_overtime_approval_message",
                new=mock_edit_owner,
            ),
            patch(
                "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
                new=mock_forward,
            ),
        ):
            await handle_overtime_requester_cancel(callback, user, bot)

        # Assert execution order and normal async context exit
        self.assertEqual(
            order,
            [
                "parse",
                "session_enter",
                "load_unlocked",
                "load_locked",
                "settings_read",
                "cancel_call",
                "answer",
                "session_exit",
            ],
        )

        # Prove one unlocked load followed by one locked load
        self.assertEqual(mock_load.await_count, 2)
        mock_load.assert_has_awaits(
            [
                unittest.mock.call(
                    session, public_id, for_update=False
                ),
                unittest.mock.call(
                    session, public_id, for_update=True
                ),
            ]
        )
        self.assertFalse(
            mock_load.await_args_list[0].kwargs["for_update"]
        )
        self.assertTrue(
            mock_load.await_args_list[1].kwargs["for_update"]
        )

        # Prove one settings read
        mock_get_settings.assert_awaited_once_with()

        # Prove cancel_by_requester call shape and exact inputs
        mock_cancel.assert_awaited_once_with(
            session,
            locked_ledger,
            requester_user_id=71,
            now=fixed_now,
            normal_lifetime_minutes=expected_normal_lifetime,
        )
        self.assertIs(mock_cancel.call_args.args[0], session)
        self.assertIs(mock_cancel.call_args.args[1], locked_ledger)
        self.assertEqual(
            mock_cancel.call_args.kwargs["requester_user_id"], 71
        )
        self.assertEqual(mock_cancel.call_args.kwargs["now"], fixed_now)
        self.assertIsInstance(
            mock_cancel.call_args.kwargs["now"], datetime
        )
        self.assertEqual(
            mock_cancel.call_args.kwargs["normal_lifetime_minutes"],
            expected_normal_lifetime,
        )
        mock_datetime.utcnow.assert_called_once_with()

        # Prove exactly one _answer with exact current detail from service and alert=True
        expected_detail = (
            "illegal overtime transition: received -> overtime_cancelled_by_requester"
        )
        mock_answer.assert_awaited_once_with(
            callback, expected_detail, alert=True
        )
        self.assertEqual(mock_answer.await_count, 1)

        # Prove no session-adapter operation occurred
        self.assertEqual(session.call_log, [])
        self.assertNotIn("rollback", session.call_log)
        self.assertNotIn("commit", session.call_log)
        self.assertNotIn("flush", session.call_log)
        self.assertNotIn("get", session.call_log)
        self.assertNotIn("execute", session.call_log)
        self.assertNotIn("scalar", session.call_log)
        self.assertNotIn("scalars", session.call_log)

        # Prove downstream suppression after caught error
        mock_edit.assert_not_called()
        mock_empty_keyboard.assert_not_called()
        mock_edit_owner.assert_not_called()
        mock_forward.assert_not_called()
        bot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
