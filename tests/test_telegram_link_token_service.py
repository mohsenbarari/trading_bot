import unittest
from types import SimpleNamespace

from core.services.telegram_link_token_service import (
    build_telegram_deep_link,
    build_telegram_start_parameter,
    create_telegram_link_token,
    generate_telegram_link_token,
    hash_telegram_link_token,
)
from core.enums import UserAccountStatus, UserRole
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


if __name__ == "__main__":
    unittest.main()
