import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


PARENT_REVISION = "a163f4a5b7c8"
ROUNDTRIP_REVISION = "a274f5a6b8c9"
HEAD_REVISION = "e653f4a5b7c8"


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramPreAuthMigrationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_url, _ = DATABASE_URLS
        source = make_url(sync_url)
        cls.database_name = (
            "telegram_queue_stage3_preauth_" + uuid4().hex[:12] + "_test"
        )
        cls.admin_url = source.set(database="postgres").render_as_string(
            hide_password=False
        )
        cls.admin_engine = create_engine(
            cls.admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True
        )
        with cls.admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{cls.database_name}"')
        cls.sync_url = source.set(database=cls.database_name).render_as_string(
            hide_password=False
        )
        _run_alembic(cls.sync_url, "upgrade", ROUNDTRIP_REVISION)

    @classmethod
    def tearDownClass(cls):
        try:
            with cls.admin_engine.connect() as connection:
                connection.exec_driver_sql(
                    f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)'
                )
        finally:
            cls.admin_engine.dispose()
            super().tearDownClass()

    def _engine(self):
        return create_engine(self.sync_url, pool_pre_ping=True)

    def test_downgrade_refuses_live_preauth_evidence_then_round_trips_when_drained(self):
        engine = self._engine()
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "TRUNCATE TABLE telegram_scheduled_operations, "
                        "telegram_delivery_jobs RESTART IDENTITY CASCADE"
                    )
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO telegram_scheduled_operations (
                            dedupe_key, action_kind, source_id, source_version,
                            destination_class, chat_id, method, payload,
                            payload_hash, template_version, due_at, scope_allowed
                        ) VALUES (
                            'migration-preauth-live', 'preauth_interaction',
                            'migration-preauth-live', 1, 'private', 7007,
                            'sendMessage',
                            CAST(:payload AS json),
                            :payload_hash, 'migration-test-v1', now(), true
                        )
                        """
                    ),
                    {
                        "payload": '{"chat_id":7007,"text":"synthetic"}',
                        "payload_hash": "a" * 64,
                    },
                )

            with self.assertRaises(AssertionError):
                _run_alembic(self.sync_url, "downgrade", PARENT_REVISION)

            with engine.begin() as connection:
                self.assertEqual(
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
                    ROUNDTRIP_REVISION,
                )
                self.assertEqual(
                    connection.execute(
                        text(
                            "SELECT count(*) FROM telegram_scheduled_operations "
                            "WHERE action_kind = 'preauth_interaction'"
                        )
                    ).scalar_one(),
                    1,
                )
                connection.execute(
                    text(
                        "TRUNCATE TABLE telegram_scheduled_operations, "
                        "telegram_delivery_jobs RESTART IDENTITY CASCADE"
                    )
                )
        finally:
            engine.dispose()

        _run_alembic(self.sync_url, "downgrade", PARENT_REVISION)
        engine = self._engine()
        try:
            with engine.connect() as connection:
                values = set(
                    connection.execute(
                        text(
                            "SELECT enumlabel FROM pg_enum "
                            "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                            "WHERE pg_type.typname = 'telegramdeliveryaction'"
                        )
                    ).scalars()
                )
                self.assertNotIn("preauth_interaction", values)
                self.assertNotIn("preauth_interaction_edit", values)
        finally:
            engine.dispose()

        _run_alembic(self.sync_url, "upgrade", ROUNDTRIP_REVISION)
        engine = self._engine()
        try:
            with engine.connect() as connection:
                self.assertEqual(
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
                    ROUNDTRIP_REVISION,
                )
                values = set(
                    connection.execute(
                        text(
                            "SELECT enumlabel FROM pg_enum "
                            "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                            "WHERE pg_type.typname = 'telegramdeliveryaction'"
                        )
                    ).scalars()
                )
                self.assertIn("preauth_interaction", values)
                self.assertIn("preauth_interaction_edit", values)
        finally:
            engine.dispose()

        _run_alembic(self.sync_url, "upgrade", "head")
        with self.assertRaises(AssertionError):
            _run_alembic(self.sync_url, "downgrade", ROUNDTRIP_REVISION)
        engine = self._engine()
        try:
            with engine.connect() as connection:
                self.assertEqual(
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
                    HEAD_REVISION,
                )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
