import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


PREVIOUS_HEAD = "ff5a6b7c8d9e"
HEAD_REVISION = "a385f6b7c8d0"


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramAdminBroadcastVideoMigrationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        source = make_url(DATABASE_URLS.owner_sync)
        cls.database_name = (
            "telegram_queue_stage3_broadcast_video_" + uuid4().hex[:12] + "_test"
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
        _run_alembic(cls.sync_url, "upgrade", PREVIOUS_HEAD)

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

    def _columns(self, connection):
        return set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'telegram_admin_broadcasts'"
                )
            ).scalars()
        )

    def test_upgrade_downgrade_upgrade_keeps_one_head_and_media_contract(self):
        engine = self._engine()
        try:
            with engine.connect() as connection:
                self.assertNotIn("content_kind", self._columns(connection))
        finally:
            engine.dispose()

        _run_alembic(self.sync_url, "upgrade", HEAD_REVISION)
        engine = self._engine()
        try:
            with engine.begin() as connection:
                columns = self._columns(connection)
                for name in (
                    "content_kind",
                    "telegram_media_file_id",
                    "telegram_media_file_unique_id",
                    "media_duration_seconds",
                    "media_width",
                    "media_height",
                    "media_file_size",
                ):
                    self.assertIn(name, columns)
                labels = set(
                    connection.execute(
                        text(
                            "SELECT enumlabel FROM pg_enum "
                            "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                            "WHERE pg_type.typname = 'telegramadminbroadcastcontentkind'"
                        )
                    ).scalars()
                )
                self.assertEqual(labels, {"text", "video"})
                constraint = connection.execute(
                    text(
                        "SELECT 1 FROM pg_constraint "
                        "WHERE conname = 'ck_telegram_admin_broadcasts_content_kind_media'"
                    )
                ).scalar()
                self.assertEqual(constraint, 1)
                self.assertEqual(
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one(),
                    HEAD_REVISION,
                )
        finally:
            engine.dispose()

        _run_alembic(self.sync_url, "downgrade", PREVIOUS_HEAD)
        engine = self._engine()
        try:
            with engine.connect() as connection:
                self.assertNotIn("content_kind", self._columns(connection))
                self.assertEqual(
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one(),
                    PREVIOUS_HEAD,
                )
        finally:
            engine.dispose()

        _run_alembic(self.sync_url, "upgrade", "head")
        engine = self._engine()
        try:
            with engine.connect() as connection:
                self.assertIn("content_kind", self._columns(connection))
                self.assertEqual(
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one(),
                    HEAD_REVISION,
                )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
