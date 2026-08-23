import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


PREVIOUS_HEAD = "ff5a6b7c8d9e"
VIDEO_REVISION = "a385f6b7c8d0"
HEAD_REVISION = "a496c8d0e1f2"


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

    def _insert_owner(self, connection):
        return connection.execute(
            text(
                """
                INSERT INTO users (
                    account_name, mobile_number, full_name, address, role,
                    has_bot_access, is_deleted, must_change_password, home_server
                ) VALUES (
                    :account, :mobile, 'Scratch Owner', 'Scratch address',
                    'SUPER_ADMIN', true, false, false, 'foreign'
                ) RETURNING id
                """
            ),
            {
                "account": "scratch_broadcast_owner",
                "mobile": "09120000999",
            },
        ).scalar_one()

    def test_upgrade_downgrade_upgrade_enforces_media_and_creation_key(self):
        engine = self._engine()
        try:
            with engine.connect() as connection:
                self.assertNotIn("content_kind", self._columns(connection))
                self.assertNotIn("creation_key", self._columns(connection))
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
                    "creation_key",
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
                self.assertEqual(
                    connection.execute(
                        text(
                            "SELECT 1 FROM pg_constraint "
                            "WHERE conname = 'ck_telegram_admin_broadcasts_content_kind_media'"
                        )
                    ).scalar(),
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        text(
                            "SELECT 1 FROM pg_constraint "
                            "WHERE conname = 'ux_telegram_admin_broadcasts_creation_key'"
                        )
                    ).scalar(),
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one(),
                    HEAD_REVISION,
                )
                owner_id = self._insert_owner(connection)
                connection.execute(
                    text(
                        """
                        INSERT INTO telegram_admin_broadcasts (
                            content, content_kind, created_by_id, audience_type,
                            target_groups, recipient_count, status, creation_key
                        ) VALUES (
                            'متن معتبر', 'text', :owner_id, 'all',
                            '[]', 0, 'queued', 'opaque-scratch-key-01'
                        )
                        """
                    ),
                    {"owner_id": owner_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO telegram_admin_broadcasts (
                            content, content_kind, telegram_media_file_id,
                            telegram_media_file_unique_id, media_duration_seconds,
                            media_width, media_height, media_file_size,
                            created_by_id, audience_type, target_groups,
                            recipient_count, status, creation_key
                        ) VALUES (
                            'آموزش امکانات بات', 'video', 'AgAC-scratch-file',
                            'AQAD-scratch-unique', 0, 640, 360, 2048,
                            :owner_id, 'all', '[]',
                            0, 'queued', 'opaque-scratch-key-02'
                        )
                        """
                    ),
                    {"owner_id": owner_id},
                )
        finally:
            engine.dispose()

        invalid_cases = (
            (
                "text with leftover media",
                """
                INSERT INTO telegram_admin_broadcasts (
                    content, content_kind, telegram_media_file_id,
                    created_by_id, audience_type, target_groups,
                    recipient_count, status
                ) VALUES (
                    'متن', 'text', 'leftover', :owner_id, 'all', '[]', 0, 'queued'
                )
                """,
            ),
            (
                "video missing file unique id",
                """
                INSERT INTO telegram_admin_broadcasts (
                    content, content_kind, telegram_media_file_id,
                    created_by_id, audience_type, target_groups,
                    recipient_count, status
                ) VALUES (
                    'کپشن', 'video', 'AgAC-only-file', :owner_id, 'all', '[]', 0, 'queued'
                )
                """,
            ),
            (
                "video width zero",
                """
                INSERT INTO telegram_admin_broadcasts (
                    content, content_kind, telegram_media_file_id,
                    telegram_media_file_unique_id, media_width,
                    created_by_id, audience_type, target_groups,
                    recipient_count, status
                ) VALUES (
                    'کپشن', 'video', 'AgAC-file', 'AQAD-unique', 0,
                    :owner_id, 'all', '[]', 0, 'queued'
                )
                """,
            ),
            (
                "duplicate creation key",
                """
                INSERT INTO telegram_admin_broadcasts (
                    content, content_kind, created_by_id, audience_type,
                    target_groups, recipient_count, status, creation_key
                ) VALUES (
                    'تکرار', 'text', :owner_id, 'all',
                    '[]', 0, 'queued', 'opaque-scratch-key-01'
                )
                """,
            ),
        )
        engine = self._engine()
        try:
            with engine.connect() as connection:
                owner_id = connection.execute(
                    text("SELECT id FROM users WHERE account_name = 'scratch_broadcast_owner'")
                ).scalar_one()
            for label, statement in invalid_cases:
                with self.subTest(label=label):
                    with engine.connect() as connection:
                        with self.assertRaises(IntegrityError):
                            connection.execute(text(statement), {"owner_id": owner_id})
                            connection.commit()
        finally:
            engine.dispose()

        _run_alembic(self.sync_url, "downgrade", PREVIOUS_HEAD)
        engine = self._engine()
        try:
            with engine.connect() as connection:
                self.assertNotIn("content_kind", self._columns(connection))
                self.assertNotIn("creation_key", self._columns(connection))
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
                self.assertIn("creation_key", self._columns(connection))
                self.assertEqual(
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one(),
                    HEAD_REVISION,
                )
                self.assertEqual(
                    connection.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar_one(),
                    1,
                )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
