import unittest
from unittest.mock import patch

from sqlalchemy.orm.attributes import set_committed_value

from core import events
from core.user_counter_sync import (
    InvalidUserCounterMutation,
    build_user_counter_event,
    increment_user_counters,
    reset_user_counters_in_memory,
    user_counter_event_payload,
)
from models.user import User, UserRole


def _capture_listeners(registry):
    def listens_for(model, event_name):
        def decorator(func):
            registry[(model.__name__, event_name)] = func
            return func

        return decorator

    return listens_for


def _persisted_user() -> User:
    user = User(
        account_name="counter_user",
        mobile_number="09120000000",
        telegram_id=123456789,
        username="counter",
        full_name="Counter User",
        address="Tehran counter address",
        role=UserRole.STANDARD,
        home_server="iran",
    )
    for attribute in User.__mapper__.column_attrs:
        set_committed_value(user, attribute.key, getattr(user, attribute.key, None))
    committed = {
        "id": 7,
        "account_name": "counter_user",
        "mobile_number": "09120000000",
        "telegram_id": 123456789,
        "trades_count": 2,
        "commodities_traded_count": 5,
        "channel_messages_count": 3,
        "counter_epoch": 4,
        "sync_version": 2,
    }
    for field_name, value in committed.items():
        set_committed_value(user, field_name, value)
    return user


class _Connection:
    def get_execution_options(self):
        return {}


class UserCounterSyncTests(unittest.TestCase):
    def test_increment_builds_positive_delta_event_without_identity_snapshot_fields(self):
        user = _persisted_user()
        increment_user_counters(user, trades=1, commodities=4)

        event = build_user_counter_event(
            user,
            {"trades_count", "commodities_traded_count"},
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "increment")
        self.assertEqual(event.epoch, 4)
        self.assertEqual(
            event.deltas,
            {"trades_count": 1, "commodities_traded_count": 4},
        )
        payload = user_counter_event_payload(user, event)
        self.assertEqual(payload["_sync_contract"], "user_counter_event_v1")
        self.assertNotIn("address", payload)
        self.assertNotIn("full_name", payload)

    def test_reset_advances_epoch_even_when_counters_are_already_zero(self):
        user = _persisted_user()
        for field_name in (
            "trades_count",
            "commodities_traded_count",
            "channel_messages_count",
        ):
            set_committed_value(user, field_name, 0)

        reset_user_counters_in_memory(user)
        event = build_user_counter_event(user, {"counter_epoch"})

        self.assertEqual(user.counter_epoch, 5)
        self.assertEqual(event.kind, "reset")
        self.assertEqual(event.epoch, 5)
        self.assertEqual(event.deltas, {})

    def test_decrease_without_epoch_is_rejected(self):
        user = _persisted_user()
        user.trades_count = 1
        with self.assertRaisesRegex(InvalidUserCounterMutation, "requires an epoch reset"):
            build_user_counter_event(user, {"trades_count"})

    def test_v2_counter_only_update_emits_dedicated_users_outbox_event(self):
        registry = {}
        with patch("core.events.event.listens_for", side_effect=_capture_listeners(registry)):
            events.setup_user_events()

        user = _persisted_user()
        increment_user_counters(user, channel_messages=1)
        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode",
            "foreign",
        ), patch("core.events.log_change") as log_change:
            registry[("User", "after_update")](None, _Connection(), user)

        log_change.assert_called_once()
        table_name, operation, payload = (
            log_change.call_args.args[1],
            log_change.call_args.args[3],
            log_change.call_args.args[4],
        )
        self.assertEqual((table_name, operation), ("users", "UPDATE"))
        self.assertEqual(payload["_counter_deltas"], {"channel_messages_count": 1})
        self.assertNotIn("sync_version", payload)

    def test_v2_foreign_identity_mutation_fails_before_flush(self):
        registry = {}
        with patch("core.events.event.listens_for", side_effect=_capture_listeners(registry)):
            events.setup_user_events()
        user = _persisted_user()
        user.address = "Foreign must not mutate this address"

        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode",
            "foreign",
        ):
            with self.assertRaisesRegex(RuntimeError, "foreign_user_write_authority_forbidden:address"):
                registry[("User", "before_update")](None, _Connection(), user)

    def test_v2_foreign_counter_reset_is_not_a_local_write_authority(self):
        registry = {}
        with patch("core.events.event.listens_for", side_effect=_capture_listeners(registry)):
            events.setup_user_events()
        user = _persisted_user()
        reset_user_counters_in_memory(user)

        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode",
            "foreign",
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "foreign_user_write_authority_forbidden:counter_epoch",
            ):
                registry[("User", "before_update")](None, _Connection(), user)


if __name__ == "__main__":
    unittest.main()
