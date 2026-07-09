from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import delete, text, update

from core.sync_outbox_guard import (
    SYNC_OUTBOX_PENDING_KEY,
    SYNC_OUTBOX_WAKEUP_NEEDED_KEY,
    SyncOutboxBypassError,
    SyncOutboxError,
    clear_sync_outbox_wakeup_after_rollback,
    collect_pending_sync_writes,
    guard_sync_bulk_or_raw_execute,
    mark_sync_outbox_recorded,
    publish_sync_outbox_wakeup_after_commit,
    statement_write_target_table,
    sync_table_requires_outbox,
    verify_pending_sync_outbox,
)
from models.message import Message
from models.offer import Offer


class FakeConnection:
    def __init__(self, *, is_sync=False):
        self.info = {}
        self._is_sync = is_sync

    def get_execution_options(self):
        return {"is_sync": self._is_sync}


class FakeSession:
    def __init__(
        self,
        *,
        new=None,
        dirty=None,
        deleted=None,
        modified=None,
        connection=None,
        info=None,
    ):
        self.new = list(new or [])
        self.dirty = list(dirty or [])
        self.deleted = list(deleted or [])
        self.info = dict(info or {})
        self._modified = set(modified or self.dirty)
        self._connection = connection or FakeConnection()

    def connection(self):
        return self._connection

    def is_modified(self, obj, include_collections=False):
        return obj in self._modified


class FakeOffer:
    __tablename__ = "offers"

    def __init__(self, record_id):
        self.id = record_id


class FakeMessage:
    __tablename__ = "messages"

    def __init__(self, record_id):
        self.id = record_id


class FakeTradingSetting:
    __tablename__ = "trading_settings"

    def __init__(self, key):
        self.key = key


class FakeRedis:
    def __init__(self, *, rpush_error=None):
        self.rpush_error = rpush_error
        self.rpush_calls = []

    def rpush(self, queue_name, payload):
        self.rpush_calls.append((queue_name, payload))
        if self.rpush_error:
            raise self.rpush_error


class SyncOutboxGuardTests(unittest.TestCase):
    def test_synced_write_requires_recorded_change_log_marker(self):
        offer = FakeOffer(7)
        session = FakeSession(new=[offer])
        flush_context = object()

        collect_pending_sync_writes(session, flush_context, None)
        self.assertIn(SYNC_OUTBOX_PENDING_KEY, session.info)

        with self.assertRaisesRegex(SyncOutboxError, "offers:7"):
            verify_pending_sync_outbox(session, flush_context)

    def test_synced_write_with_recorded_marker_passes(self):
        offer = FakeOffer(8)
        connection = FakeConnection()
        session = FakeSession(new=[offer], connection=connection)
        flush_context = object()

        collect_pending_sync_writes(session, flush_context, None)
        mark_sync_outbox_recorded(connection, "offers", "INSERT", 8, {"id": 8})
        verify_pending_sync_outbox(session, flush_context)

        self.assertNotIn(SYNC_OUTBOX_PENDING_KEY, session.info)
        self.assertTrue(session.info[SYNC_OUTBOX_WAKEUP_NEEDED_KEY])

    def test_after_commit_wakes_sync_worker_only_after_verified_outbox(self):
        offer = FakeOffer(81)
        connection = FakeConnection()
        session = FakeSession(new=[offer], connection=connection)
        flush_context = object()
        fake_redis = FakeRedis()

        collect_pending_sync_writes(session, flush_context, None)
        mark_sync_outbox_recorded(connection, "offers", "INSERT", 81, {"id": 81})
        verify_pending_sync_outbox(session, flush_context)

        with patch("core.sync_outbox_guard._get_sync_wakeup_redis", return_value=fake_redis):
            publish_sync_outbox_wakeup_after_commit(session)

        self.assertEqual(len(fake_redis.rpush_calls), 1)
        self.assertEqual(fake_redis.rpush_calls[0][0], "sync:outbound")
        self.assertNotIn(SYNC_OUTBOX_WAKEUP_NEEDED_KEY, session.info)

    def test_after_rollback_clears_pending_sync_worker_wakeup(self):
        session = FakeSession(info={SYNC_OUTBOX_WAKEUP_NEEDED_KEY: True})

        clear_sync_outbox_wakeup_after_rollback(session)

        self.assertNotIn(SYNC_OUTBOX_WAKEUP_NEEDED_KEY, session.info)

    def test_after_commit_wakeup_failure_does_not_fail_transaction_close(self):
        session = FakeSession(info={SYNC_OUTBOX_WAKEUP_NEEDED_KEY: True})
        fake_redis = FakeRedis(rpush_error=RuntimeError("redis down"))

        with patch("core.sync_outbox_guard._get_sync_wakeup_redis", return_value=fake_redis), patch(
            "core.sync_outbox_guard.logger"
        ) as logger:
            publish_sync_outbox_wakeup_after_commit(session)

        logger.warning.assert_called_once()
        self.assertNotIn(SYNC_OUTBOX_WAKEUP_NEEDED_KEY, session.info)

    def test_non_synced_and_sync_apply_writes_do_not_require_outbox(self):
        message = FakeMessage(3)
        session = FakeSession(new=[message])
        flush_context = object()

        collect_pending_sync_writes(session, flush_context, None)
        self.assertNotIn(SYNC_OUTBOX_PENDING_KEY, session.info)
        verify_pending_sync_outbox(session, flush_context)

        offer = FakeOffer(9)
        sync_session = FakeSession(new=[offer], connection=FakeConnection(is_sync=True))
        collect_pending_sync_writes(sync_session, flush_context, None)
        self.assertNotIn(SYNC_OUTBOX_PENDING_KEY, sync_session.info)

    def test_trading_settings_uses_key_identity_despite_dummy_record_id(self):
        setting = FakeTradingSetting("offer_expiry_minutes")
        connection = FakeConnection()
        session = FakeSession(dirty=[setting], connection=connection)
        flush_context = object()

        collect_pending_sync_writes(session, flush_context, None)
        mark_sync_outbox_recorded(
            connection,
            "trading_settings",
            "UPDATE",
            0,
            {"key": "offer_expiry_minutes"},
        )
        verify_pending_sync_outbox(session, flush_context)

    def test_bulk_and_raw_writes_to_sync_tables_are_blocked(self):
        self.assertTrue(sync_table_requires_outbox("offers"))
        self.assertFalse(sync_table_requires_outbox("messages"))

        with self.assertRaisesRegex(SyncOutboxBypassError, "offers"):
            guard_sync_bulk_or_raw_execute(
                SimpleNamespace(execution_options={}, statement=update(Offer))
            )

        with self.assertRaisesRegex(SyncOutboxBypassError, "offers"):
            guard_sync_bulk_or_raw_execute(
                SimpleNamespace(
                    execution_options={},
                    statement=text("UPDATE offers SET status = 'expired' WHERE id = 1"),
                )
            )

        with self.assertRaisesRegex(SyncOutboxBypassError, "offers"):
            guard_sync_bulk_or_raw_execute(
                SimpleNamespace(execution_options={}, statement=delete(Offer))
            )

        guard_sync_bulk_or_raw_execute(
            SimpleNamespace(execution_options={"is_sync": True}, statement=update(Offer))
        )
        guard_sync_bulk_or_raw_execute(
            SimpleNamespace(execution_options={}, statement=update(Message))
        )
        guard_sync_bulk_or_raw_execute(
            SimpleNamespace(
                execution_options={},
                statement=text("DELETE FROM messages WHERE id = 1"),
            )
        )
        guard_sync_bulk_or_raw_execute(
            SimpleNamespace(execution_options={}, statement=text("SELECT * FROM offers"))
        )

    def test_statement_write_target_table_parses_core_and_raw_sql(self):
        self.assertEqual(statement_write_target_table(update(Offer)), "offers")
        self.assertEqual(
            statement_write_target_table(text('DELETE FROM public."offers" WHERE id = 1')),
            "offers",
        )
        self.assertIsNone(statement_write_target_table(text("SELECT * FROM offers")))


if __name__ == "__main__":
    unittest.main()
