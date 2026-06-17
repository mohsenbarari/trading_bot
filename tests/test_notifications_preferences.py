import unittest
from types import SimpleNamespace

from api.routers.notifications import (
    NotificationPreferencesUpdate,
    get_notification_preferences,
    update_notification_preferences,
)
from models.user_notification_preference import UserNotificationPreference


class FakeExecuteResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.added = []
        self.commit_count = 0
        self.refreshed = []

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commit_count += 1

    async def refresh(self, item):
        self.refreshed.append(item)


class NotificationPreferencesTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_notification_preferences_defaults_market_push_to_enabled(self):
        db = FakeDB([FakeExecuteResult(None)])
        current_user = SimpleNamespace(id=7)

        result = await get_notification_preferences(current_user=current_user, db=db)

        self.assertTrue(result.market_offer_push_enabled)
        self.assertEqual(db.added, [])
        self.assertEqual(db.commit_count, 0)

    async def test_update_notification_preferences_creates_user_row(self):
        db = FakeDB([FakeExecuteResult(None)])
        current_user = SimpleNamespace(id=7)

        result = await update_notification_preferences(
            NotificationPreferencesUpdate(market_offer_push_enabled=False),
            current_user=current_user,
            db=db,
        )

        self.assertFalse(result.market_offer_push_enabled)
        self.assertEqual(len(db.added), 1)
        self.assertIsInstance(db.added[0], UserNotificationPreference)
        self.assertEqual(db.added[0].user_id, 7)
        self.assertFalse(db.added[0].market_offer_push_enabled)
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(db.refreshed, [db.added[0]])

    async def test_update_notification_preferences_reuses_existing_row(self):
        preferences = SimpleNamespace(user_id=7, market_offer_push_enabled=False)
        db = FakeDB([FakeExecuteResult(preferences)])
        current_user = SimpleNamespace(id=7)

        result = await update_notification_preferences(
            NotificationPreferencesUpdate(market_offer_push_enabled=True),
            current_user=current_user,
            db=db,
        )

        self.assertTrue(result.market_offer_push_enabled)
        self.assertTrue(preferences.market_offer_push_enabled)
        self.assertEqual(db.added, [])
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(db.refreshed, [preferences])


if __name__ == "__main__":
    unittest.main()
