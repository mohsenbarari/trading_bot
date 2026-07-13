import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.telegram_link_token_service import (
    build_telegram_deep_link,
    build_telegram_start_parameter,
    create_telegram_link_token,
    consume_telegram_link_token,
    generate_telegram_link_token,
    hash_telegram_link_token,
    load_pending_telegram_link_token_user_for_update,
    TelegramLinkTokenError,
)
from core.enums import UserAccountStatus, UserRole
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.bot_access_policy import BotAccessDecision
from core.utils import utc_now
from models.telegram_link_token import TelegramLinkTokenStatus


class FakeResult:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self.values))


class FakeDB:
    def __init__(self, results):
        self.results = list(results)
        self.execute_calls = []
        self.added = []
        self.flush_count = 0

    async def execute(self, stmt, *_args, **_kwargs):
        self.execute_calls.append(stmt)
        if self.results:
            return self.results.pop(0)
        return FakeResult()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1


class TelegramLinkTokenServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_generated_token_fits_telegram_start_parameter_limit(self):
        token = generate_telegram_link_token()
        start_parameter = build_telegram_start_parameter(token)

        self.assertTrue(start_parameter.startswith("link_"))
        self.assertLessEqual(len(start_parameter), 64)
        self.assertEqual(len(hash_telegram_link_token(token)), 64)

    def test_deep_link_uses_clean_bot_username(self):
        self.assertEqual(
            build_telegram_deep_link("@example_bot", "abc123"),
            "https://t.me/example_bot?start=link_abc123",
        )

    async def test_create_token_locks_user_row_before_revoking_pending_tokens(self):
        user = SimpleNamespace(
            id=7,
            mobile_number="09121112233",
            account_name="safe-user",
            telegram_id=None,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
        )
        pending = SimpleNamespace(
            status=TelegramLinkTokenStatus.PENDING,
            revoked_at=None,
        )
        db = FakeDB(
            [FakeResult(), FakeResult(), FakeResult(user), FakeResult(values=[pending])]
        )

        result = await create_telegram_link_token(db, user)

        self.assertIsNotNone(getattr(db.execute_calls[2], "_for_update_arg", None))
        self.assertEqual(pending.status, TelegramLinkTokenStatus.REVOKED)
        self.assertIsNotNone(pending.revoked_at)
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0].token_hash, result.token_hash)
        self.assertEqual(db.flush_count, 1)

    async def test_load_pending_token_rejects_missing_mismatched_terminal_and_expired(self):
        user = SimpleNamespace(id=7)
        pending = SimpleNamespace(
            user_id=7,
            status=TelegramLinkTokenStatus.PENDING,
            expires_at=utc_now() + timedelta(minutes=1),
        )
        cases = (
            ([FakeResult(None)], "invalid"),
            ([FakeResult(pending), FakeResult(None), FakeResult(pending)], "invalid"),
            (
                [
                    FakeResult(pending),
                    FakeResult(user),
                    FakeResult(SimpleNamespace(**{**vars(pending), "status": TelegramLinkTokenStatus.USED})),
                ],
                "used",
            ),
            (
                [
                    FakeResult(pending),
                    FakeResult(user),
                    FakeResult(SimpleNamespace(**{**vars(pending), "expires_at": utc_now() - timedelta(seconds=1)})),
                ],
                "expired",
            ),
        )
        for results, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(TelegramLinkTokenError) as raised:
                await load_pending_telegram_link_token_user_for_update(FakeDB(results), "raw-token")
            self.assertEqual(raised.exception.reason, reason)

    async def test_load_pending_token_applies_bot_access_decision(self):
        user = SimpleNamespace(id=7)
        token = SimpleNamespace(
            user_id=7,
            status=TelegramLinkTokenStatus.PENDING,
            expires_at=utc_now() + timedelta(minutes=1),
        )
        with patch(
            "core.services.telegram_link_token_service.evaluate_bot_access",
            new=AsyncMock(return_value=BotAccessDecision(False, "sync_pending")),
        ):
            with self.assertRaises(TelegramLinkTokenError) as raised:
                await load_pending_telegram_link_token_user_for_update(
                    FakeDB([FakeResult(token), FakeResult(user), FakeResult(token)]),
                    "raw-token",
                )
        self.assertEqual(raised.exception.reason, "sync_pending")

        allowed = BotAccessDecision(True, None)
        with patch(
            "core.services.telegram_link_token_service.evaluate_bot_access",
            new=AsyncMock(return_value=allowed),
        ):
            loaded = await load_pending_telegram_link_token_user_for_update(
                FakeDB([FakeResult(token), FakeResult(user), FakeResult(token)]),
                "raw-token",
            )
        self.assertEqual(loaded, (token, user, allowed))

    async def test_consume_token_enforces_iran_and_unique_telegram_identity(self):
        token = SimpleNamespace(status=TelegramLinkTokenStatus.PENDING)
        user = SimpleNamespace(
            id=7,
            account_name="same-name",
            full_name="same-name",
            has_bot_access=False,
            telegram_id=None,
            username=None,
        )
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.telegram_link_token_service.settings.registration_sync_v2_enabled",
            True,
        ), self.assertRaises(TelegramLinkTokenError) as raised:
            await consume_telegram_link_token(
                FakeDB([]), token, user, telegram_id=99, username=None, full_name=None
            )
        self.assertEqual(raised.exception.reason, "iran_authority_required")

        with override_current_server(SERVER_IRAN), self.assertRaises(TelegramLinkTokenError) as raised:
            await consume_telegram_link_token(
                FakeDB([FakeResult(SimpleNamespace(id=8))]),
                token,
                user,
                telegram_id=99,
                username=None,
                full_name=None,
            )
        self.assertEqual(raised.exception.reason, "telegram_id_already_used")

        with override_current_server(SERVER_IRAN):
            await consume_telegram_link_token(
                FakeDB([FakeResult(None)]),
                token,
                user,
                telegram_id=99,
                username="telegram-user",
                full_name="Telegram Name",
            )
        self.assertEqual(user.telegram_id, 99)
        self.assertEqual(user.username, "telegram-user")
        self.assertEqual(user.full_name, "same-name")
        self.assertEqual(token.status, TelegramLinkTokenStatus.USED)


if __name__ == "__main__":
    unittest.main()
