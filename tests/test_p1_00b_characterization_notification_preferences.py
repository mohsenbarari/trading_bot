"""Hermetic characterization tests for notification preference handlers under Stage P1-00b.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed IDs:
- API-NOTIFY-GET-NOTIFICATIONS-GET-NOTIFICATION-PREFERENCES-L220: get_notification_preferences
- API-NOTIFY-PATCH-NOTIFICATIONS-UPDATE-NOTIFICATION-PREFERENCES-L233: update_notification_preferences

This bounded direct-handler characterization does not evidence FastAPI routing/dependency/Pydantic
request validation, production/schema parity, concurrency, the full blocker, or Stage completion.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from api.routers.notifications import (
    NotificationPreferencesRead,
    NotificationPreferencesUpdate,
    get_notification_preferences,
    update_notification_preferences,
)
from models.user_notification_preference import UserNotificationPreference


class TrackingAsyncSessionAdapter:
    """Minimal async session adapter delegating to a sync Session while recording exact calls."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.execute_calls: list[Any] = []
        self.commit_calls: int = 0
        self.add_calls: list[Any] = []
        self.refresh_calls: list[Any] = []
        self.delete_calls: int = 0
        self.flush_calls: int = 0
        self.rollback_calls: int = 0

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.execute_calls.append(statement)
        return self._session.execute(statement, *args, **kwargs)

    def add(self, instance: Any, _warn: bool = True) -> None:
        self.add_calls.append(instance)
        self._session.add(instance)

    async def commit(self) -> None:
        self.commit_calls += 1
        self._session.commit()

    async def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.refresh_calls.append(instance)
        self._session.refresh(instance, *args, **kwargs)

    def delete(self, instance: Any) -> None:
        self.delete_calls += 1
        raise AssertionError("TrackingAsyncSessionAdapter.delete must not be called")

    async def flush(self, objects: Any = None) -> None:
        self.flush_calls += 1
        raise AssertionError("TrackingAsyncSessionAdapter.flush must not be called")

    async def rollback(self) -> None:
        self.rollback_calls += 1
        raise AssertionError("TrackingAsyncSessionAdapter.rollback must not be called")

    async def close(self) -> None:
        self._session.close()


def _snapshot_preference(item: UserNotificationPreference) -> dict[str, Any]:
    """Capture mapped user notification preference column values for exact snapshot verification."""
    return {
        "id": item.id,
        "user_id": item.user_id,
        "market_offer_push_enabled": item.market_offer_push_enabled,
    }


class NotificationPreferencesCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization tests for notification preference handlers."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_preferences.db")
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        UserNotificationPreference.__table__.create(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_get_preferences_absent_returns_default_without_writes(self) -> None:
        """API-NOTIFY-GET-NOTIFICATIONS-GET-NOTIFICATION-PREFERENCES-L220: default response when absent."""
        current_user = SimpleNamespace(id=10)
        session = self.session_factory()
        adapter = TrackingAsyncSessionAdapter(session)

        try:
            result = await get_notification_preferences(
                current_user=current_user,
                db=adapter,
            )
        finally:
            await adapter.close()

        self.assertIsInstance(result, NotificationPreferencesRead)
        self.assertIs(result.market_offer_push_enabled, True)

        self.assertEqual(len(adapter.execute_calls), 1)
        self.assertEqual(adapter.commit_calls, 0)
        self.assertEqual(len(adapter.add_calls), 0)
        self.assertEqual(len(adapter.refresh_calls), 0)
        self.assertEqual(adapter.delete_calls, 0)
        self.assertEqual(adapter.flush_calls, 0)
        self.assertEqual(adapter.rollback_calls, 0)

        with self.session_factory() as verify_session:
            rows = verify_session.execute(
                select(UserNotificationPreference)
            ).scalars().all()
            self.assertEqual(len(rows), 0)

    async def test_get_preferences_returns_only_current_users_existing_row_without_writes(self) -> None:
        """API-NOTIFY-GET-NOTIFICATIONS-GET-NOTIFICATION-PREFERENCES-L220: user_id filter without writes."""
        with self.session_factory() as seed_session:
            seed_session.add_all([
                UserNotificationPreference(
                    user_id=10,
                    market_offer_push_enabled=False,
                ),
                UserNotificationPreference(
                    user_id=20,
                    market_offer_push_enabled=True,
                ),
            ])
            seed_session.commit()

        with self.session_factory() as baseline_session:
            baseline_rows = {
                item.user_id: _snapshot_preference(item)
                for item in baseline_session.execute(
                    select(UserNotificationPreference).order_by(UserNotificationPreference.user_id)
                ).scalars().all()
            }

        self.assertEqual(set(baseline_rows.keys()), {10, 20})
        self.assertIs(baseline_rows[10]["market_offer_push_enabled"], False)
        self.assertIs(baseline_rows[20]["market_offer_push_enabled"], True)

        current_user = SimpleNamespace(id=10)
        session = self.session_factory()
        adapter = TrackingAsyncSessionAdapter(session)

        try:
            result = await get_notification_preferences(
                current_user=current_user,
                db=adapter,
            )
        finally:
            await adapter.close()

        self.assertIsInstance(result, NotificationPreferencesRead)
        self.assertIs(result.market_offer_push_enabled, False)

        self.assertEqual(len(adapter.execute_calls), 1)
        self.assertEqual(adapter.commit_calls, 0)
        self.assertEqual(len(adapter.add_calls), 0)
        self.assertEqual(len(adapter.refresh_calls), 0)
        self.assertEqual(adapter.delete_calls, 0)
        self.assertEqual(adapter.flush_calls, 0)
        self.assertEqual(adapter.rollback_calls, 0)

        with self.session_factory() as verify_session:
            persisted_rows = {
                item.user_id: _snapshot_preference(item)
                for item in verify_session.execute(
                    select(UserNotificationPreference).order_by(UserNotificationPreference.user_id)
                ).scalars().all()
            }
            self.assertEqual(persisted_rows, baseline_rows)

    async def test_update_preferences_create_then_update_persists_owner_only(self) -> None:
        """API-NOTIFY-PATCH-NOTIFICATIONS-UPDATE-NOTIFICATION-PREFERENCES-L233: create then update flow."""
        owner_id = 10
        other_user_id = 20
        owner_user = SimpleNamespace(id=owner_id)

        payload_create = NotificationPreferencesUpdate(market_offer_push_enabled=False)
        session_create = self.session_factory()
        adapter_create = TrackingAsyncSessionAdapter(session_create)

        try:
            result_create = await update_notification_preferences(
                payload=payload_create,
                current_user=owner_user,
                db=adapter_create,
            )
        finally:
            await adapter_create.close()

        self.assertIsInstance(result_create, NotificationPreferencesRead)
        self.assertIs(result_create.market_offer_push_enabled, False)

        self.assertEqual(len(adapter_create.execute_calls), 1)
        self.assertEqual(len(adapter_create.add_calls), 1)
        self.assertEqual(adapter_create.commit_calls, 1)
        self.assertEqual(len(adapter_create.refresh_calls), 1)
        self.assertEqual(adapter_create.delete_calls, 0)
        self.assertEqual(adapter_create.flush_calls, 0)
        self.assertEqual(adapter_create.rollback_calls, 0)

        added_instance = adapter_create.add_calls[0]
        self.assertIsInstance(added_instance, UserNotificationPreference)
        self.assertEqual(added_instance.user_id, owner_id)

        with self.session_factory() as verify_session_1:
            rows_1 = verify_session_1.execute(
                select(UserNotificationPreference)
            ).scalars().all()
            self.assertEqual(len(rows_1), 1)
            self.assertEqual(rows_1[0].user_id, owner_id)
            self.assertIs(rows_1[0].market_offer_push_enabled, False)

        with self.session_factory() as seed_session:
            seed_session.add(
                UserNotificationPreference(
                    user_id=other_user_id,
                    market_offer_push_enabled=True,
                )
            )
            seed_session.commit()

        with self.session_factory() as pre_update_session:
            pre_update_rows = {
                item.user_id: _snapshot_preference(item)
                for item in pre_update_session.execute(
                    select(UserNotificationPreference).order_by(UserNotificationPreference.user_id)
                ).scalars().all()
            }
            self.assertEqual(set(pre_update_rows.keys()), {owner_id, other_user_id})
            self.assertIs(pre_update_rows[owner_id]["market_offer_push_enabled"], False)
            self.assertIs(pre_update_rows[other_user_id]["market_offer_push_enabled"], True)

        payload_update = NotificationPreferencesUpdate(market_offer_push_enabled=True)
        session_update = self.session_factory()
        adapter_update = TrackingAsyncSessionAdapter(session_update)

        try:
            result_update = await update_notification_preferences(
                payload=payload_update,
                current_user=owner_user,
                db=adapter_update,
            )
        finally:
            await adapter_update.close()

        self.assertIsInstance(result_update, NotificationPreferencesRead)
        self.assertIs(result_update.market_offer_push_enabled, True)

        self.assertEqual(len(adapter_update.add_calls), 0)
        self.assertEqual(len(adapter_update.execute_calls), 1)
        self.assertEqual(adapter_update.commit_calls, 1)
        self.assertEqual(len(adapter_update.refresh_calls), 1)
        self.assertEqual(adapter_update.delete_calls, 0)
        self.assertEqual(adapter_update.flush_calls, 0)
        self.assertEqual(adapter_update.rollback_calls, 0)

        refreshed_instance = adapter_update.refresh_calls[0]
        self.assertIsInstance(refreshed_instance, UserNotificationPreference)
        self.assertEqual(refreshed_instance.user_id, owner_id)

        with self.session_factory() as verify_session_2:
            post_update_rows = {
                item.user_id: _snapshot_preference(item)
                for item in verify_session_2.execute(
                    select(UserNotificationPreference).order_by(UserNotificationPreference.user_id)
                ).scalars().all()
            }
            self.assertEqual(len(post_update_rows), 2)
            self.assertEqual(set(post_update_rows.keys()), {owner_id, other_user_id})
            self.assertIs(post_update_rows[other_user_id]["market_offer_push_enabled"], True)
            self.assertIs(post_update_rows[owner_id]["market_offer_push_enabled"], True)


if __name__ == "__main__":
    unittest.main()
