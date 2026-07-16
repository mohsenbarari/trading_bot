import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.orm.exc import StaleDataError

from core.services.telegram_username_observation_service import (
    normalize_observed_telegram_username,
    refresh_observed_telegram_username,
)


class TelegramUsernameObservationServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_normalize_accepts_telegram_shapes_and_rejects_other_types(self):
        self.assertEqual(normalize_observed_telegram_username(" @coin_owner "), "coin_owner")
        self.assertIsNone(normalize_observed_telegram_username("  "))
        self.assertIsNone(normalize_observed_telegram_username(None))
        with self.assertRaisesRegex(TypeError, "string or null"):
            normalize_observed_telegram_username(123)

    async def test_unchanged_username_does_not_write(self):
        db = AsyncMock()
        user = SimpleNamespace(id=7, telegram_id=10, username="coin_owner")

        result = await refresh_observed_telegram_username(
            db,
            user=user,
            telegram_id=10,
            observed_username="coin_owner",
        )

        self.assertIs(result, user)
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()

    async def test_changed_or_removed_username_is_committed(self):
        for observed, expected in (("new_owner", "new_owner"), (None, None)):
            with self.subTest(observed=observed):
                db = AsyncMock()
                user = SimpleNamespace(id=7, telegram_id=10, username="old_owner")

                result = await refresh_observed_telegram_username(
                    db,
                    user=user,
                    telegram_id=10,
                    observed_username=observed,
                )

                self.assertIs(result, user)
                self.assertEqual(user.username, expected)
                db.commit.assert_awaited_once()
                db.rollback.assert_not_awaited()

    async def test_stale_write_reloads_and_retries_against_current_user(self):
        db = AsyncMock()
        db.commit.side_effect = [StaleDataError("stale"), None]
        original = SimpleNamespace(id=7, telegram_id=10, username="old_owner")
        reloaded = SimpleNamespace(id=7, telegram_id=10, username="other_value")

        with patch(
            "core.services.telegram_username_observation_service._reload_linked_user",
            new=AsyncMock(return_value=reloaded),
        ) as reload_user:
            result = await refresh_observed_telegram_username(
                db,
                user=original,
                telegram_id=10,
                observed_username="new_owner",
            )

        self.assertIs(result, reloaded)
        self.assertEqual(reloaded.username, "new_owner")
        self.assertEqual(db.commit.await_count, 2)
        db.rollback.assert_awaited_once()
        reload_user.assert_awaited_once_with(db, user_id=7, telegram_id=10)

    async def test_database_failure_rolls_back_and_does_not_block_bot(self):
        db = AsyncMock()
        db.commit.side_effect = RuntimeError("database unavailable")
        original = SimpleNamespace(id=7, telegram_id=10, username="old_owner")
        current = SimpleNamespace(id=7, telegram_id=10, username="old_owner")

        with patch(
            "core.services.telegram_username_observation_service._reload_linked_user",
            new=AsyncMock(return_value=current),
        ):
            result = await refresh_observed_telegram_username(
                db,
                user=original,
                telegram_id=10,
                observed_username="new_owner",
            )

        self.assertIs(result, current)
        db.rollback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
