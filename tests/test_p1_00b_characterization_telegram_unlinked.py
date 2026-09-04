"""P1-00b: Telegram entry rejects missing and locally denied users before execute."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import (
    _handle_channel_trade,
    handle_channel_trade,
    handle_channel_trade_public,
)
from core.enums import UserAccountStatus, UserRole
from core.services.bot_access_policy import evaluate_bot_access_local_state

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
UNLINKED_NODE = (
    "tests/test_p1_00b_characterization_telegram_unlinked.py"
    "::P100bTelegramUnlinkedCharacterizationTests"
    ".test_telegram_entry_rejects_missing_and_locally_denied_users_injects"
)


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


def _callback():
    return SimpleNamespace(
        id="cb-unlinked",
        from_user=SimpleNamespace(id=300),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=200),
            message_id=50,
            edit_reply_markup=AsyncMock(),
        ),
    )


def _denied_user(**overrides):
    data = {
        "id": 5,
        "telegram_id": 555,
        "mobile_number": "0935",
        "account_name": "buyer",
        "role": UserRole.STANDARD,
        "account_status": UserAccountStatus.ACTIVE,
        "is_deleted": False,
        "trading_restricted_until": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class P100bTelegramUnlinkedCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_entry_stops_before_execute(self, handler, user):
        callback = _callback()
        session_factory = AsyncMock()
        session_factory.return_value.__aenter__ = AsyncMock(side_effect=AssertionError("session opened"))
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch("bot.handlers.trade_execute.AsyncSessionLocal", session_factory), patch(
            "bot.handlers.trade_execute._execute_trade_authoritatively_with_transient_retry",
            new=AsyncMock(side_effect=AssertionError("authoritative trade reached")),
        ) as execute, patch(
            "bot.handlers.trade_execute.forward_trade_to_home_server",
            new=AsyncMock(side_effect=AssertionError("forward reached")),
        ), patch(
            "bot.handlers.trade_execute.enqueue_current_offer_delivery",
            new=AsyncMock(side_effect=AssertionError("delivery reached")),
        ), patch(
            "core.services.session_service.handle_login_session",
            new=AsyncMock(side_effect=AssertionError("login session reached")),
        ), patch(
            "core.services.session_service.create_session",
            new=AsyncMock(side_effect=AssertionError("create_session reached")),
        ), patch(
            "bot.telegram_interaction_message.answer_callback_message_via_runtime",
            new=AsyncMock(side_effect=AssertionError("provider message reached")),
        ):
            await handler(
                callback,
                SimpleNamespace(offer_id=7, amount=2),
                user,
                bot,
            )
        session_factory.assert_not_called()
        execute.assert_not_awaited()
        bot.send_message.assert_not_awaited()
        callback.answer.assert_awaited()

    async def test_telegram_entry_rejects_missing_and_locally_denied_users_injects(self):
        """UNLINKED-BOT: missing and locally denied users never reach session or trade."""
        missing = evaluate_bot_access_local_state(None)
        self.assertFalse(missing.allowed)
        self.assertEqual(missing.reason, "pending_sync")

        inactive = _denied_user(account_status=UserAccountStatus.INACTIVE)
        deleted = _denied_user(is_deleted=True)
        watch = _denied_user(role=UserRole.WATCH)
        self.assertFalse(evaluate_bot_access_local_state(inactive).allowed)
        self.assertFalse(evaluate_bot_access_local_state(deleted).allowed)
        self.assertFalse(evaluate_bot_access_local_state(watch).allowed)

        for handler in (_handle_channel_trade, handle_channel_trade, handle_channel_trade_public):
            await self._assert_entry_stops_before_execute(handler, None)
            await self._assert_entry_stops_before_execute(handler, inactive)
            await self._assert_entry_stops_before_execute(handler, deleted)
            await self._assert_entry_stops_before_execute(handler, watch)

    def test_telegram_unlinked_cells_cite_entry_boundary(self):
        payload = _load_json("scenario-records.json")
        for row in payload["origin_required_interactions"]:
            if row["interaction"] != "UNLINKED-BOT":
                continue
            if row["request_origin"] != "TELEGRAM_BOT":
                self.assertEqual(row["applicability"], "NOT_APPLICABLE")
                continue
            self.assertEqual(row["applicability"], "ELIGIBLE")
            self.assertEqual(row["disposition"], "EVIDENCED")
            self.assertTrue(row.get("evidence_cases"))
            self.assertIn(UNLINKED_NODE, row["evidence_refs"][0])


if __name__ == "__main__":
    unittest.main()
