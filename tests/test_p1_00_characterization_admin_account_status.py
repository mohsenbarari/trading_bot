"""HIGH-B: account-status transition is not the block toggle; exercise the live service."""

from __future__ import annotations

import inspect
import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_user_toggle_account_status
from core.enums import NotificationCategory, NotificationLevel, UserAccountStatus, UserRole
from core.services import user_account_status_service as status_service

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
ACCOUNT_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-TOGGLE-ACCOUNT-STATUS-L1134"
BLOCK_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649"


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


class FakeScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, value):
        self.value = value
        self.commit = AsyncMock()
        self.flush = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        return FakeScalarOneResult(self.value)


def _callback():
    return SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
        data="user_toggle_account_status_9",
    )


def _user(*, status=UserAccountStatus.ACTIVE, role=UserRole.STANDARD, telegram_id=91):
    return SimpleNamespace(
        id=9,
        role=role,
        account_name="ali",
        account_status=status,
        telegram_id=telegram_id,
        deactivated_at=None if status == UserAccountStatus.ACTIVE else datetime(2026, 5, 16, 12, 0, 0),
        messenger_grace_expires_at=None,
        messenger_blocked_at=None,
        sync_version=3,
        trading_restricted_until=None,
        max_daily_trades=None,
        max_active_commodities=None,
        max_daily_requests=None,
        is_deleted=False,
        can_block_users=True,
        max_blocked_users=5,
    )


class P100AdminAccountStatusCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.notify = AsyncMock()
        self.enqueue = AsyncMock()
        self.invalidate = AsyncMock()
        self.patches = [
            patch("core.services.user_account_status_service.create_user_notification", new=self.notify),
            patch(
                "core.services.telegram_notification_outbox_service.enqueue_account_status_telegram_notification_once",
                new=self.enqueue,
            ),
            patch(
                "core.services.user_account_status_service._invalidate_overtime_for_inactive_user",
                new=self.invalidate,
            ),
            patch(
                "core.services.user_account_status_service.send_telegram_notification",
                new=AsyncMock(),
            ),
            patch(
                "core.services.user_account_status_service.remove_user_from_telegram_channel",
                new=AsyncMock(),
            ),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in self.patches:
            item.stop()

    async def test_live_transition_deactivates_with_notify_outbox_and_overtime(self):
        user = _user()
        result = await status_service.transition_user_account_status(SimpleNamespace(), user, UserAccountStatus.INACTIVE)
        self.assertTrue(result.changed)
        self.assertEqual(user.account_status, UserAccountStatus.INACTIVE)
        self.notify.assert_awaited_once()
        self.assertEqual(self.notify.await_args.args[1], 9)
        self.assertEqual(self.notify.await_args.args[3], NotificationLevel.WARNING)
        self.assertEqual(self.notify.await_args.args[4], NotificationCategory.SYSTEM)
        self.enqueue.assert_awaited_once()
        self.invalidate.assert_awaited_once()
        replay = await status_service.transition_user_account_status(SimpleNamespace(), user, UserAccountStatus.INACTIVE)
        self.assertFalse(replay.changed)
        self.assertEqual(self.notify.await_count, 1)
        self.assertEqual(self.enqueue.await_count, 1)
        self.assertEqual(self.invalidate.await_count, 1)

    async def test_handler_uses_live_transition_then_commits(self):
        target = _user()
        session = FakeSession(target)
        source = inspect.getsource(handle_user_toggle_account_status)
        self.assertIn("transition_user_account_status", source)
        self.assertNotIn("idempotency", source)
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=session
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ):
            await handle_user_toggle_account_status(_callback(), user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            self.assertEqual(target.account_status, UserAccountStatus.INACTIVE)
            self.notify.assert_awaited()
            self.enqueue.assert_awaited()
            self.invalidate.assert_awaited()
            session.commit.assert_awaited()
            notify_order = self.notify.await_count
            await handle_user_toggle_account_status(_callback(), user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            self.assertEqual(target.account_status, UserAccountStatus.ACTIVE)
        self.assertEqual(session.commit.await_count, 2)
        self.assertGreaterEqual(self.notify.await_count, notify_order)

    async def test_denied_handler_paths_do_not_transition(self):
        protected = _user(role=UserRole.SUPER_ADMIN)
        session = FakeSession(protected)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session):
            await handle_user_toggle_account_status(_callback(), user=None)
            await handle_user_toggle_account_status(
                _callback(),
                user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER),
            )
        self.notify.assert_not_awaited()
        self.enqueue.assert_not_awaited()
        self.invalidate.assert_not_awaited()
        session.commit.assert_not_awaited()
        self.assertEqual(protected.account_status, UserAccountStatus.ACTIVE)

    def test_account_status_seed_uses_its_own_contract(self):
        payload = _load_json("scenario-records.json")
        seeds = {item["behavior_id"]: item for item in _load_json("surface-bot.json")["entries"]}
        account = seeds[ACCOUNT_SEED]
        block = seeds[BLOCK_SEED]
        self.assertEqual(account["operation_kind"], "ACCOUNT_STATUS_TRANSITION")
        self.assertEqual(block["operation_kind"], "TOGGLE_MUTATION")
        rules = payload["seed_mapping"]["rules"]
        account_contract = next(
            rule["contract_id"]
            for rule in rules
            if rule["family"] == "ADMIN" and rule["operation_kind"] == "ACCOUNT_STATUS_TRANSITION"
        )
        block_contract = next(
            rule["contract_id"]
            for rule in rules
            if rule["family"] == "ADMIN" and rule["operation_kind"] == "TOGGLE_MUTATION"
        )
        self.assertEqual(account_contract, "BC-ADMIN-ACCOUNT_STATUS_TRANSITION-V1")
        self.assertEqual(block_contract, "BC-ADMIN-TOGGLE_MUTATION-V1")
        contract = next(item for item in payload["behavior_contracts"] if item["contract_id"] == account_contract)
        self.assertEqual(contract["events_outbox"]["status"], "APPLICABLE")
        self.assertEqual(contract["side_effects"]["status"], "APPLICABLE")
        talk = json.dumps(contract, ensure_ascii=False).lower()
        self.assertIn("notification", talk)
        self.assertIn("outbox", talk)
        self.assertIn("overtime", talk)
        self.assertIn("tests/test_p1_00_characterization_admin_account_status.py", account["test_refs"])
        toggle = next(item for item in payload["behavior_contracts"] if item["contract_id"] == block_contract)
        self.assertNotIn("handle_user_toggle_account_status", json.dumps(toggle, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
