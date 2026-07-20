"""Opt-in real-PostgreSQL proof for bounded DR replay-nonce retention."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text

from core.dr_projection_worker import cleanup_expired_replay_nonces


PROJECTION_URL_ENV = "THREE_SITE_FENCING_TEST_PROJECTION_URL"


@unittest.skipUnless(
    os.environ.get(PROJECTION_URL_ENV),
    "scratch three-site projection URL is not configured",
)
class DrNonceRetentionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(os.environ[PROJECTION_URL_ENV], pool_pre_ping=True)
        with cls.engine.connect() as connection:
            database_name = str(connection.scalar(text("SELECT current_database()")))
            if not database_name.startswith("stage4_registration_"):
                raise RuntimeError("nonce retention test requires a guarded scratch database")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def test_cleanup_is_indexed_bounded_and_preserves_replay_window(self) -> None:
        key_id = "retention-" + uuid4().hex[:24]
        old_nonce = uuid4().hex
        recent_nonce = uuid4().hex
        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config('trading_bot.mutation_capability', "
                    "'projection', true)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO dr_replay_nonces "
                    "(key_id, nonce, source_site, destination_site, request_hash, expires_at) "
                    "VALUES (:key_id, :old_nonce, 'bot_fi', 'webapp_fi', repeat('a',64), "
                    ":old_expiry), (:key_id, :recent_nonce, 'bot_fi', 'webapp_fi', "
                    "repeat('b',64), :recent_expiry)"
                ),
                {
                    "key_id": key_id,
                    "old_nonce": old_nonce,
                    "recent_nonce": recent_nonce,
                    "old_expiry": now - timedelta(hours=1),
                    "recent_expiry": now - timedelta(seconds=1),
                },
            )

        deleted = asyncio.run(cleanup_expired_replay_nonces(now=now))

        with self.engine.connect() as connection:
            remaining = set(
                connection.execute(
                    text(
                        "SELECT nonce FROM dr_replay_nonces WHERE key_id=:key_id"
                    ),
                    {"key_id": key_id},
                ).scalars()
            )
            index_count = int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_indexes "
                        "WHERE schemaname='public' "
                        "AND indexname='ix_dr_replay_nonces_expires_at'"
                    )
                )
                or 0
            )
        self.assertGreaterEqual(deleted, 1)
        self.assertEqual(remaining, {recent_nonce})
        self.assertEqual(index_count, 1)


if __name__ == "__main__":
    unittest.main()
