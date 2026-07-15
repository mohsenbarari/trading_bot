import unittest
from unittest.mock import patch

from sqlalchemy.orm.attributes import set_committed_value

from core import events
from core.user_counter_sync import (
    InvalidUserCounterMutation,
    USER_COUNTER_FIELDS,
    USER_COUNTER_MAX_EPOCH,
    USER_COUNTER_MAX_VALUE,
    build_user_counter_event,
    build_user_sync_identity,
    increment_user_counters,
    normalize_counter_event_occurred_at,
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
    def __init__(self):
        self.executed = []

    def get_execution_options(self):
        return {}

    def execute(self, statement, parameters):
        self.executed.append((statement, parameters))


class UserCounterSyncTests(unittest.TestCase):
    def test_counter_time_and_increment_inputs_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "occurred_at is required"):
            normalize_counter_event_occurred_at(None)
        with self.assertRaisesRegex(ValueError, "non-negative"):
            increment_user_counters(_persisted_user(), trades=-1)

    def test_identity_snapshot_preserves_previous_value_and_handles_unmapped_objects(self):
        user = _persisted_user()
        user.account_name = "counter_user_changed"
        identity = build_user_sync_identity(user, include_previous=True)
        self.assertEqual(identity["previous"]["account_name"], "counter_user")
        self.assertEqual(identity["current"]["account_name"], "counter_user_changed")

        fallback = build_user_sync_identity(
            type("Unmapped", (), {"account_name": "plain", "mobile_number": None, "telegram_id": None})(),
            include_previous=True,
        )
        self.assertEqual(fallback, {"current": {"account_name": "plain"}, "previous": {}})

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
        self.assertEqual(payload["_sync_contract"], "user_counter_event_v2")
        self.assertIn("_counter_occurred_at", payload)
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

    def test_event_builder_rejects_inconsistent_or_invalid_histories(self):
        transient = User(
            account_name="transient_counter",
            mobile_number="09120000001",
            full_name="Transient",
            address="Tehran transient address",
            role=UserRole.STANDARD,
            home_server="iran",
        )
        transient.trades_count = 1
        with self.assertRaisesRegex(InvalidUserCounterMutation, "missing previous value"):
            build_user_counter_event(transient, {"trades_count"})

        self.assertIsNone(build_user_counter_event(_persisted_user(), {"address"}))

        inconsistent = _persisted_user()
        inconsistent.counter_epoch = 5
        inconsistent.trades_count = 3
        with self.assertRaisesRegex(InvalidUserCounterMutation, "epoch history is inconsistent"):
            build_user_counter_event(inconsistent, {"trades_count"})

    def test_reset_event_requires_exact_epoch_and_zero_counters(self):
        wrong_epoch = _persisted_user()
        wrong_epoch.counter_epoch = 6
        for field in ("trades_count", "commodities_traded_count", "channel_messages_count"):
            wrong_epoch.__setattr__(field, 0)
        with self.assertRaisesRegex(InvalidUserCounterMutation, "increment epoch exactly once"):
            build_user_counter_event(wrong_epoch, {"counter_epoch", *USER_COUNTER_FIELDS})

        too_high = _persisted_user()
        set_committed_value(too_high, "counter_epoch", USER_COUNTER_MAX_EPOCH)
        too_high.counter_epoch = USER_COUNTER_MAX_EPOCH + 1
        for field in ("trades_count", "commodities_traded_count", "channel_messages_count"):
            too_high.__setattr__(field, 0)
        with self.assertRaisesRegex(InvalidUserCounterMutation, "epoch exceeds"):
            build_user_counter_event(too_high, {"counter_epoch", *USER_COUNTER_FIELDS})

        nonzero = _persisted_user()
        nonzero.counter_epoch = 5
        with self.assertRaisesRegex(InvalidUserCounterMutation, "zero every counter"):
            build_user_counter_event(nonzero, {"counter_epoch"})

    def test_increment_event_rejects_oversized_delta_and_ignores_zero_delta(self):
        oversized = _persisted_user()
        set_committed_value(oversized, "trades_count", 0)
        oversized.trades_count = USER_COUNTER_MAX_VALUE + 1
        with self.assertRaisesRegex(InvalidUserCounterMutation, "supported range"):
            build_user_counter_event(oversized, {"trades_count"})

        with patch(
            "core.user_counter_sync._history_values",
            side_effect=(
                (1, 1, False),
                (2, 2, True),
                (0, 0, False),
                (0, 0, False),
            ),
        ):
            self.assertIsNone(build_user_counter_event(object(), {"trades_count"}))

    def test_counter_and_epoch_bounds_fail_before_mutation(self):
        user = _persisted_user()
        set_committed_value(user, "trades_count", USER_COUNTER_MAX_VALUE)
        with self.assertRaisesRegex(ValueError, "aggregate range"):
            increment_user_counters(user, trades=1)
        self.assertEqual(user.trades_count, USER_COUNTER_MAX_VALUE)

        set_committed_value(user, "counter_epoch", USER_COUNTER_MAX_EPOCH)
        with self.assertRaisesRegex(ValueError, "epoch exceeds"):
            reset_user_counters_in_memory(user)
        self.assertEqual(user.counter_epoch, USER_COUNTER_MAX_EPOCH)

    def test_v2_counter_only_update_emits_dedicated_users_outbox_event(self):
        registry = {}
        with patch("core.events.event.listens_for", side_effect=_capture_listeners(registry)):
            events.setup_user_events()

        user = _persisted_user()
        increment_user_counters(user, channel_messages=1)
        connection = _Connection()
        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode",
            "foreign",
        ), patch("core.events.log_change") as log_change:
            registry[("User", "after_update")](None, connection, user)

        log_change.assert_called_once()
        table_name, operation, payload = (
            log_change.call_args.args[1],
            log_change.call_args.args[3],
            log_change.call_args.args[4],
        )
        self.assertEqual((table_name, operation), ("users", "UPDATE"))
        self.assertEqual(payload["_counter_deltas"], {"channel_messages_count": 1})
        self.assertNotIn("sync_version", payload)
        self.assertEqual(len(connection.executed), 1)
        self.assertNotIn("counter_user", repr(connection.executed[0]))

    def test_v2_mixed_profile_and_counter_update_fails_if_counter_ledger_fails(self):
        registry = {}
        with patch("core.events.event.listens_for", side_effect=_capture_listeners(registry)):
            events.setup_user_events()

        user = _persisted_user()
        user.address = "Updated Iran address"
        increment_user_counters(user, trades=1)
        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode",
            "iran",
        ), patch("core.events.log_change") as log_change, patch(
            "core.events._record_local_user_counter_event",
            side_effect=RuntimeError("ledger unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "ledger unavailable"):
                registry[("User", "after_update")](None, _Connection(), user)

        log_change.assert_called_once()
        self.assertNotIn("_sync_contract", log_change.call_args.args[4])

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

    def test_v2_foreign_observed_username_update_is_allowed_and_emitted(self):
        registry = {}
        with patch("core.events.event.listens_for", side_effect=_capture_listeners(registry)):
            events.setup_user_events()
        user = _persisted_user()
        user.username = "new_telegram_username"
        connection = _Connection()

        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode",
            "foreign",
        ), patch("core.events.log_change") as log_change:
            registry[("User", "before_update")](None, connection, user)
            registry[("User", "after_update")](None, connection, user)

        log_change.assert_called_once()
        table_name, operation, payload = (
            log_change.call_args.args[1],
            log_change.call_args.args[3],
            log_change.call_args.args[4],
        )
        self.assertEqual((table_name, operation), ("users", "UPDATE"))
        self.assertEqual(payload["username"], "new_telegram_username")
        self.assertNotIn("address", payload)

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
