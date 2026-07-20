import unittest

from sqlalchemy import create_engine, text

from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


PARENT_REVISION = "a163f4a5b7c8"
HEAD_REVISION = "b320c1d2e3f4"


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramPreAuthMigrationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_url, _ = DATABASE_URLS
        cls.sync_url = sync_url
        _run_alembic(sync_url, "upgrade", "head")

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
                    HEAD_REVISION,
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

        _run_alembic(self.sync_url, "upgrade", "head")
        engine = self._engine()
        try:
            with engine.connect() as connection:
                self.assertEqual(
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
                    HEAD_REVISION,
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


if __name__ == "__main__":
    unittest.main()
