from __future__ import annotations

import tempfile
from pathlib import Path

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import asyncio
import sqlite3
import unittest
from contextlib import ExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import ChannelTradePublicCallback
from bot.handlers.trade_execute import (
    _callback_offer_id,
    _callback_offer_public_id,
    handle_channel_trade_public,
)
from bot.utils.redis_helpers import _memory_fallback
from core.config import settings
from core.enums import UserRole
from core.server_routing import SERVER_FOREIGN
from models.offer_request import OfferRequestSourceSurface, OfferRequestStatus
from tests.test_p1_00b_characterization_origin_interactions import (
    OriginSession,
    _Result,
    _make_offer,
    _make_owner,
    _request_patches,
    _sqlite_counts,
    _sqlite_requests,
)


class _SessionWrapper:
    """Minimal local async context wrapper adapting OriginSession for trade_execute handlers."""

    def __init__(self, inner: OriginSession):
        self._inner = inner
        self.recorded_offer_statements = []

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def execute(self, statement, params=None):
        blob = str(statement).lower()
        if "offers." in blob or " from offers" in blob or " join offers" in blob:
            self.recorded_offer_statements.append(statement)
            if "offer_public_id" in blob:
                return _Result(single=self._inner.offer, single_or_none=self._inner.offer)
            return _Result(single=None, single_or_none=None)
        return await self._inner.execute(statement, params)

    def expunge(self, obj):
        pass

    @asynccontextmanager
    async def begin_nested(self):
        yield self


class P100bChannelTradeTelegramOfferSuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        _memory_fallback["confirmations"].clear()

    def tearDown(self):
        _memory_fallback["confirmations"].clear()
        super().tearDown()

    async def test_telegram_offer_telegram_public_identity_double_click_persists_once(self):
        owner = _make_owner(has_bot_access=True)
        self.assertEqual(owner.role, UserRole.STANDARD)
        offer = _make_offer("TELEGRAM_BOT")
        self.assertEqual(offer.home_server, SERVER_FOREIGN)
        trade_amount = 4

        callback_data = ChannelTradePublicCallback(
            offer_public_id=offer.offer_public_id,
            amount=trade_amount,
        )

        self.assertFalse(hasattr(callback_data, "offer_id"))
        self.assertIsNone(getattr(callback_data, "offer_id", None))
        self.assertIsNone(_callback_offer_id(callback_data))
        self.assertEqual(_callback_offer_public_id(callback_data), offer.offer_public_id)

        channel_id = getattr(settings, "channel_id", -100123456789) or -100123456789
        callback_message = SimpleNamespace(
            message_id=42,
            chat=SimpleNamespace(id=channel_id),
        )
        callback_data_str = f"ct2:{offer.offer_public_id}:{trade_amount}"
        if hasattr(callback_data, "pack"):
            self.assertEqual(callback_data.pack(), callback_data_str)

        callback = SimpleNamespace(
            id="cb_channel_trade_public",
            from_user=SimpleNamespace(id=owner.telegram_id),
            message=callback_message,
            data=callback_data_str,
        )
        bot = AsyncMock()
        mock_answer = AsyncMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = str(Path(temp_dir) / "test_channel_trade_telegram_offer.db")
            engine, Session = _sqlite_requests(sqlite_path)
            sync_session = Session()
            origin_session = OriginSession(sync_session, offer=offer, owner=owner)
            session_wrapper = _SessionWrapper(origin_session)

            @asynccontextmanager
            async def _session_scope():
                yield session_wrapper

            patches = _request_patches(SERVER_FOREIGN) + [
                patch("bot.utils.redis_helpers.get_redis_client", new=AsyncMock(return_value=None)),
                patch("bot.handlers.trade_execute.answer_callback_query_via_runtime", new=mock_answer),
                patch(
                    "bot.handlers.trade_execute.evaluate_bot_access_local_state",
                    return_value=SimpleNamespace(allowed=True, reason=None),
                ),
                patch(
                    "bot.handlers.trade_execute.evaluate_bot_access",
                    new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
                ),
                patch(
                    "bot.handlers.trade_execute.is_trade_blocked_by_principals",
                    new=AsyncMock(return_value=(False, None, None, None)),
                ),
                patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, "")),
                patch("bot.handlers.trade_execute.current_server", return_value=SERVER_FOREIGN),
                patch("bot.handlers.trade_execute.is_remote_home", return_value=False),
                patch("bot.handlers.trade_execute.edit_callback_reply_markup_via_runtime", new=AsyncMock()),
                patch("bot.handlers.trade_execute.upsert_trade_suggestion_record", new=AsyncMock()),
                patch("bot.handlers.trade_execute.schedule_trade_suggestion_cleanup", new=AsyncMock()),
                patch("bot.handlers.trade_execute.schedule_trade_suggestion_pending_reset", new=AsyncMock()),
                patch("bot.handlers.trade_execute.remove_trade_suggestion_record", new=AsyncMock()),
                patch("bot.handlers.trade_execute.AsyncSessionLocal", side_effect=_session_scope),
            ]

            try:
                with ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)

                    counts_before = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_before["trades"], 0)
                    self.assertEqual(counts_before["offer_requests"], 0)

                    await handle_channel_trade_public(
                        callback=callback,
                        callback_data=callback_data,
                        user=owner,
                        bot=bot,
                    )

                    counts_after_click1 = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_after_click1["trades"], 0)
                    self.assertEqual(counts_after_click1["offer_requests"], 0)

                    self.assertEqual(mock_answer.call_count, 1)
                    first_call = mock_answer.call_args_list[0]
                    self.assertEqual(first_call.args, (callback, "برای تایید دوباره روی همان دکمه بزنید ☑️"))
                    self.assertEqual(first_call.kwargs, {"show_alert": False})
                    self.assertNotIn("معامله ثبت شد ✅", [c.args[1] for c in mock_answer.call_args_list if len(c.args) > 1])

                    self.assertGreaterEqual(len(session_wrapper.recorded_offer_statements), 1)
                    for stmt in session_wrapper.recorded_offer_statements:
                        stmt_str = str(stmt).lower()
                        self.assertIn("offer_public_id", stmt_str)
                        self.assertNotIn("offers.id =", stmt_str)
                        if hasattr(stmt, "compile"):
                            compiled_params = getattr(stmt.compile(), "params", {})
                            self.assertIn(offer.offer_public_id, list(compiled_params.values()))

                    await handle_channel_trade_public(
                        callback=callback,
                        callback_data=callback_data,
                        user=owner,
                        bot=bot,
                    )

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
                    trade_dict = dict(trade_rows[0])
                    request_dict = dict(request_rows[0])

                    self.assertIsNotNone(trade_dict.get("id"))
                    self.assertIsNotNone(trade_dict.get("trade_number"))

                    request_source = (
                        request_dict.get("request_source_surface")
                        or request_dict.get("source_surface")
                        or request_dict.get("source")
                    )
                    self.assertEqual(request_source, OfferRequestSourceSurface.TELEGRAM_BOT.value)

                    request_result = (
                        request_dict.get("result")
                        or request_dict.get("request_status")
                        or request_dict.get("result_status")
                        or request_dict.get("status")
                        or request_dict.get("outcome")
                        or next(
                            (
                                val
                                for val in request_dict.values()
                                if str(getattr(val, "value", val)).lower()
                                == OfferRequestStatus.COMPLETED_TRADE.value
                            ),
                            None,
                        )
                    )
                    if hasattr(request_result, "value"):
                        request_result = request_result.value
                    if isinstance(request_result, str) and request_result.lower() == OfferRequestStatus.COMPLETED_TRADE.value:
                        request_result = OfferRequestStatus.COMPLETED_TRADE.value
                    self.assertEqual(request_result, OfferRequestStatus.COMPLETED_TRADE.value)

                    self.assertEqual(request_dict.get("request_home_server"), SERVER_FOREIGN)
                    self.assertEqual(request_dict.get("request_source_server"), SERVER_FOREIGN)
                    self.assertEqual(offer.home_server, SERVER_FOREIGN)
                    self.assertEqual(request_dict.get("offer_public_id"), offer.offer_public_id)
                    self.assertEqual(request_dict.get("resulting_trade_id"), trade_dict.get("id"))

                    self.assertEqual(mock_answer.call_count, 2)
                    second_call = mock_answer.call_args_list[1]
                    self.assertEqual(second_call.args, (callback, "معامله ثبت شد ✅"))
                    self.assertEqual(second_call.kwargs, {"show_alert": False})
            finally:
                _memory_fallback["confirmations"].clear()
                origin_session.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
