import asyncio
import base64
import os
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.writer_witness_contract import validate_witness_lease_proof
from core.writer_witness_control import (
    ACTION_ACQUIRE,
    WriterWitnessError,
    load_witness_snapshot,
    transition_witness_state,
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


def keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(private_raw).decode(), base64.b64encode(public_raw).decode()


@unittest.skipUnless(
    is_guarded_scratch_url(test_database_url()),
    "set WRITER_FENCING_TEST_DATABASE_URL to a guarded writer-fencing scratch database",
)
class WriterWitnessPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(test_database_url(), pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.private_key, self.public_key = keypair()
        async with self.sessions() as session:
            await session.execute(text("DELETE FROM webapp_writer_witness_receipts"))
            await session.execute(
                text(
                    """
                    UPDATE webapp_writer_witness_state
                    SET holder_site = NULL,
                        writer_epoch = 0,
                        lease_id = NULL,
                        lease_status = 'vacant',
                        issued_at = NULL,
                        expires_at = NULL,
                        transition_id = 'integration-reset',
                        updated_by = 'integration-test',
                        reason = 'reset witness concurrency test'
                    WHERE authority = 'webapp'
                    """
                )
            )
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _acquire(self, site: str, request_id: str):
        async with self.sessions() as session:
            try:
                result = await transition_witness_state(
                    session,
                    action=ACTION_ACQUIRE,
                    requester_site=site,
                    expected_epoch=0,
                    expected_lease_id=None,
                    request_id=request_id,
                    operator="integration@example",
                    reason=f"concurrent acquisition by {site}",
                    private_key_base64=self.private_key,
                )
                await session.commit()
                return result
            except WriterWitnessError as exc:
                await session.rollback()
                return exc

    async def test_concurrent_acquisition_grants_exactly_one_signed_term(self):
        outcomes = await asyncio.gather(
            self._acquire("webapp_fi", "integration-fi"),
            self._acquire("webapp_ir", "integration-ir"),
        )
        winners = [value for value in outcomes if not isinstance(value, Exception)]
        losers = [value for value in outcomes if isinstance(value, Exception)]

        self.assertEqual(len(winners), 1)
        self.assertEqual(len(losers), 1)
        winner = winners[0]
        validated = validate_witness_lease_proof(
            winner.proof,
            public_key_base64=self.public_key,
            expected_site=winner.state.holder_site,
            expected_epoch=1,
        )
        self.assertEqual(validated.lease_id, winner.state.lease_id)

        async with self.sessions() as session:
            durable = await load_witness_snapshot(session)
            receipt_count = (
                await session.execute(text("SELECT count(*) FROM webapp_writer_witness_receipts"))
            ).scalar_one()
        self.assertEqual(durable.writer_epoch, 1)
        self.assertEqual(durable.holder_site, winner.state.holder_site)
        self.assertEqual(receipt_count, 1)


if __name__ == "__main__":
    unittest.main()
