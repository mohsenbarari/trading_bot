import os
import unittest

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.runtime_identity import RuntimeIdentity
from core.webapp_writer_control import load_writer_snapshot
from core.writer_fencing import (
    WriterFenceError,
    register_writer_fence_listener,
    writer_fence_scope,
)


def test_database_url() -> str:
    return str(os.getenv("WRITER_FENCING_TEST_DATABASE_URL", "")).strip()


def is_guarded_scratch_url(value: str) -> bool:
    if not value:
        return False
    try:
        database = str(make_url(value).database or "")
    except Exception:
        return False
    return database.startswith("stage4_registration_writerfence_")


@unittest.skipUnless(
    is_guarded_scratch_url(test_database_url()),
    "set WRITER_FENCING_TEST_DATABASE_URL to a guarded writer-fencing scratch database",
)
class WriterFencingPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(test_database_url(), pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        register_writer_fence_listener()
        async with self.sessions() as session:
            await session.execute(
                text(
                    """
                    UPDATE webapp_writer_state
                    SET active_site = 'webapp_fi',
                        writer_epoch = 10,
                        control_state = 'active',
                        transition_id = 'integration-active-term',
                        witness_lease_id = 'integration-lease',
                        witness_lease_expires_at = clock_timestamp() + interval '180 seconds',
                        witness_proof_hash = repeat('a', 64),
                        witness_transition_id = 'integration-witness-term',
                        updated_by = 'integration-test',
                        reason = 'integration setup'
                    WHERE authority = 'webapp'
                    """
                )
            )
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_commit_rechecks_durable_term_and_rejects_stale_writer(self):
        identity = RuntimeIdentity("webapp", "webapp_fi", "iran", False)
        async with self.sessions() as session:
            snapshot = await load_writer_snapshot(session)

        async with self.sessions() as control_session:
            await control_session.execute(
                text(
                    """
                    UPDATE webapp_writer_state
                    SET active_site = NULL,
                        control_state = 'fenced',
                        transition_id = 'integration-fenced-term',
                        updated_by = 'integration-test',
                        reason = 'prove stale commit rejection'
                    WHERE authority = 'webapp'
                    """
                )
            )
            await control_session.commit()

        async with self.sessions() as stale_session:
            with writer_fence_scope(identity, snapshot, source="postgres_integration"):
                with self.assertRaises(WriterFenceError):
                    await stale_session.commit()
                await stale_session.rollback()

    async def test_commit_rejects_witness_lease_inside_safety_margin(self):
        identity = RuntimeIdentity("webapp", "webapp_fi", "iran", False)
        async with self.sessions() as session:
            snapshot = await load_writer_snapshot(session)

        async with self.sessions() as control_session:
            await control_session.execute(
                text(
                    """
                    UPDATE webapp_writer_state
                    SET witness_lease_expires_at = clock_timestamp() + interval '5 seconds'
                    WHERE authority = 'webapp'
                    """
                )
            )
            await control_session.commit()

        async with self.sessions() as stale_session:
            with writer_fence_scope(
                identity,
                snapshot,
                source="postgres_witness_integration",
                require_witness_lease=True,
            ):
                with self.assertRaises(WriterFenceError):
                    await stale_session.commit()
                await stale_session.rollback()


if __name__ == "__main__":
    unittest.main()
