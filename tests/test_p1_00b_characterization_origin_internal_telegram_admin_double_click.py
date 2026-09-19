"""Hermetic characterization test for ORIGIN-INTERNAL__TELEGRAM_BOT__POLICY-SNAPSHOT admin double-click path.

Verifies that an internal offer clicked twice by a SUPER_ADMIN user via Telegram bot:
- Evaluates live admin bot access policy on both clicks (is_user_accountant=False, customer_relation=None)
- Passes policy checks on the same AsyncSession per click without fallback
- Registers confirmation on click one (no database persistence)
- Persists exactly one trade and offer_request on click two
"""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import ChannelTradePublicCallback
from bot.handlers.trade_execute import (
    _callback_offer_id,
    _callback_offer_public_id,
    handle_channel_trade_public,
)
from bot.utils.redis_helpers import _memory_fallback
from core.config import settings
from core.enums import UserRole
from core.server_routing import SERVER_IRAN
from models.offer_request import OfferRequestSourceSurface, OfferRequestStatus
from tests.test_p1_00b_characterization_origin_interactions import (
    _make_offer,
    _make_owner,
    _persist_graph,
    _request_patches,
    _sqlite_counts,
    _sqlite_requests,
)
from tests.test_p1_00b_characterization_real_async_session_helper import real_async_session


class P100bOriginInternalTelegramAdminDoubleClickCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        _memory_fallback["confirmations"].clear()

    def tearDown(self) -> None:
        _memory_fallback["confirmations"].clear()
        super().tearDown()

    async def test_internal_offer_telegram_super_admin_double_click_persists_once_with_policy_snapshot(self) -> None:
        admin = _make_owner(has_bot_access=True, role=UserRole.SUPER_ADMIN)
        self.assertEqual(admin.role, UserRole.SUPER_ADMIN)
        self.assertEqual(admin.role.value, "مدیر ارشد")
        self.assertEqual(UserRole.SUPER_ADMIN.value, "مدیر ارشد")

        offer = _make_offer("INTERNAL")
        self.assertEqual(offer.home_server, SERVER_IRAN)
        self.assertEqual(offer.home_server, "iran")
        self.assertEqual(offer.offer_public_id, "ofr_internal_7")
        self.assertTrue(offer.offer_public_id.startswith("ofr_internal_"))

        trade_amount = offer.quantity
        self.assertEqual(trade_amount, 4)

        callback_data = ChannelTradePublicCallback(
            offer_public_id=offer.offer_public_id,
            amount=trade_amount,
        )

        self.assertFalse(hasattr(callback_data, "offer_id"))
        self.assertIsNone(getattr(callback_data, "offer_id", None))
        self.assertIsNone(_callback_offer_id(callback_data))
        self.assertEqual(_callback_offer_public_id(callback_data), offer.offer_public_id)

        callback_data_str = f"ct2:{offer.offer_public_id}:{trade_amount}"
        if hasattr(callback_data, "pack"):
            self.assertEqual(callback_data.pack(), callback_data_str)
            packed_callback_data = callback_data.pack()
        else:
            packed_callback_data = callback_data_str

        channel_id = getattr(settings, "channel_id", -100123456789) or -100123456789
        callback_message = SimpleNamespace(
            message_id=42,
            chat=SimpleNamespace(id=channel_id),
        )
        callback = SimpleNamespace(
            id="cb_channel_trade_public_admin",
            from_user=SimpleNamespace(id=admin.telegram_id),
            message=callback_message,
            data=packed_callback_data,
        )
        bot = AsyncMock()
        mock_answer = AsyncMock()
        mock_forward = AsyncMock()

        mock_is_user_accountant = AsyncMock(return_value=False)
        mock_get_active_customer_relation = AsyncMock(return_value=None)

        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = str(Path(temp_dir) / "test_admin_double_click.db")
            engine, Session = _sqlite_requests(sqlite_path)

            sync_session = Session()
            try:
                _persist_graph(sync_session, admin, offer)
            finally:
                sync_session.close()

            patches = _request_patches(SERVER_IRAN) + [
                patch("bot.utils.redis_helpers.get_redis_client", new=AsyncMock(return_value=None)),
                patch("bot.handlers.trade_execute.answer_callback_query_via_runtime", new=mock_answer),
                patch("bot.handlers.trade_execute.forward_trade_to_home_server", new=mock_forward),
                patch("core.trade_forwarding.forward_trade_to_home_server", new=mock_forward),
                patch("bot.handlers.trade_execute.current_server", return_value=SERVER_IRAN),
                patch("bot.handlers.trade_execute.is_remote_home", return_value=False),
                patch(
                    "bot.handlers.trade_execute.is_trade_blocked_by_principals",
                    new=AsyncMock(return_value=(False, None, None, None)),
                ),
                patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, "")),
                patch("bot.handlers.trade_execute.edit_callback_reply_markup_via_runtime", new=AsyncMock()),
                patch("bot.handlers.trade_execute.upsert_trade_suggestion_record", new=AsyncMock()),
                patch("bot.handlers.trade_execute.schedule_trade_suggestion_cleanup", new=AsyncMock()),
                patch("bot.handlers.trade_execute.schedule_trade_suggestion_pending_reset", new=AsyncMock()),
                patch("bot.handlers.trade_execute.remove_trade_suggestion_record", new=AsyncMock()),
                patch("bot.handlers.trade_execute.AsyncSessionLocal", side_effect=lambda: real_async_session(engine)),
                patch("core.db.AsyncSessionLocal", side_effect=lambda: real_async_session(engine)),
                patch("core.services.bot_access_policy.is_user_accountant", new=mock_is_user_accountant),
                patch("core.services.accountant_relation_service.is_user_accountant", new=mock_is_user_accountant),
                patch(
                    "core.services.bot_access_policy.get_active_customer_relation_for_user",
                    new=mock_get_active_customer_relation,
                ),
                patch(
                    "core.services.customer_relation_service.get_active_customer_relation_for_user",
                    new=mock_get_active_customer_relation,
                ),
            ]

            try:
                with ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)

                    counts_before = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_before["trades"], 0)
                    self.assertEqual(counts_before["offer_requests"], 0)

                    # Click 1: Normal double-click gate first click
                    await handle_channel_trade_public(
                        callback=callback,
                        callback_data=callback_data,
                        user=admin,
                        bot=bot,
                        trade_contention_preconfirmed=False,
                    )

                    counts_after_click1 = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_after_click1["trades"], 0)
                    self.assertEqual(counts_after_click1["offer_requests"], 0)

                    self.assertEqual(mock_answer.call_count, 1)
                    first_call = mock_answer.call_args_list[0]
                    self.assertEqual(first_call.args, (callback, "برای تایید دوباره روی همان دکمه بزنید ☑️"))
                    self.assertEqual(first_call.kwargs, {"show_alert": False})

                    self.assertEqual(mock_is_user_accountant.await_count, 1)
                    self.assertEqual(mock_get_active_customer_relation.await_count, 1)
                    self.assertFalse(mock_is_user_accountant.return_value)
                    self.assertIsNone(mock_get_active_customer_relation.return_value)

                    c1_acc = mock_is_user_accountant.call_args_list[0]
                    c1_rel = mock_get_active_customer_relation.call_args_list[0]
                    c1_acc_session = c1_acc.args[0] if c1_acc.args else c1_acc.kwargs["db"]
                    c1_acc_user_id = c1_acc.args[1] if len(c1_acc.args) > 1 else c1_acc.kwargs["user_id"]
                    c1_rel_session = c1_rel.args[0] if c1_rel.args else c1_rel.kwargs["db"]
                    c1_rel_user_id = c1_rel.args[1] if len(c1_rel.args) > 1 else c1_rel.kwargs["user_id"]

                    self.assertEqual(c1_acc_user_id, admin.id)
                    self.assertEqual(c1_rel_user_id, admin.id)
                    self.assertIs(c1_acc_session, c1_rel_session)
                    self.assertIsInstance(c1_acc_session, AsyncSession)

                    # Click 2: Second click through normal gate
                    await handle_channel_trade_public(
                        callback=callback,
                        callback_data=callback_data,
                        user=admin,
                        bot=bot,
                        trade_contention_preconfirmed=False,
                    )

                    self.assertEqual(mock_is_user_accountant.await_count, 2)
                    self.assertEqual(mock_get_active_customer_relation.await_count, 2)

                    c2_acc = mock_is_user_accountant.call_args_list[1]
                    c2_rel = mock_get_active_customer_relation.call_args_list[1]
                    c2_acc_session = c2_acc.args[0] if c2_acc.args else c2_acc.kwargs["db"]
                    c2_acc_user_id = c2_acc.args[1] if len(c2_acc.args) > 1 else c2_acc.kwargs["user_id"]
                    c2_rel_session = c2_rel.args[0] if c2_rel.args else c2_rel.kwargs["db"]
                    c2_rel_user_id = c2_rel.args[1] if len(c2_rel.args) > 1 else c2_rel.kwargs["user_id"]

                    self.assertEqual(c2_acc_user_id, admin.id)
                    self.assertEqual(c2_rel_user_id, admin.id)
                    self.assertIs(c2_acc_session, c2_rel_session)
                    self.assertIsInstance(c2_acc_session, AsyncSession)
                    self.assertIsNot(c1_acc_session, c2_acc_session)

                    self.assertEqual(mock_answer.call_count, 2)
                    second_call = mock_answer.call_args_list[1]
                    self.assertEqual(second_call.args, (callback, "معامله ثبت شد ✅"))
                    self.assertEqual(second_call.kwargs, {"show_alert": False})

                    counts_after_click2 = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_after_click2["trades"], 1)
                    self.assertEqual(counts_after_click2["offer_requests"], 1)
                    self.assertIsNotNone(counts_after_click2["trade_number"])
                    self.assertIsNotNone(counts_after_click2["resulting_trade_id"])

                    with sqlite3.connect(sqlite_path) as conn:
                        conn.row_factory = sqlite3.Row
                        trade_rows = conn.execute("SELECT * FROM trades").fetchall()
                        request_rows = conn.execute("SELECT * FROM offer_requests").fetchall()

                    self.assertEqual(len(trade_rows), 1)
                    self.assertEqual(len(request_rows), 1)
                    trade_row = trade_rows[0]
                    request_row = request_rows[0]

                    self.assertIsNotNone(trade_row["id"])
                    self.assertIsNotNone(trade_row["trade_number"])
                    self.assertGreater(trade_row["trade_number"], 0)

                    self.assertEqual(request_row["resulting_trade_id"], trade_row["id"])
                    self.assertEqual(
                        request_row["request_source_surface"],
                        OfferRequestSourceSurface.TELEGRAM_BOT.value,
                    )
                    self.assertEqual(request_row["request_source_surface"], "telegram_bot")
                    self.assertEqual(request_row["request_home_server"], SERVER_IRAN)
                    self.assertEqual(request_row["request_home_server"], "iran")
                    self.assertEqual(request_row["request_source_server"], SERVER_IRAN)
                    self.assertEqual(request_row["request_source_server"], "iran")
                    self.assertEqual(request_row["offer_public_id"], offer.offer_public_id)
                    self.assertEqual(request_row["offer_public_id"], "ofr_internal_7")
                    self.assertEqual(
                        request_row["result_status"],
                        OfferRequestStatus.COMPLETED_TRADE.value,
                    )
                    self.assertEqual(request_row["result_status"], "completed_trade")

                    self.assertEqual(mock_forward.await_count, 0)
                    mock_forward.assert_not_awaited()
            finally:
                _memory_fallback["confirmations"].clear()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
