import asyncio
import base64
from datetime import datetime, timedelta, timezone
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
    WriterWitnessCampaignExpiredError,
    WriterWitnessError,
    load_witness_snapshot,
    persist_witness_rejection,
    transition_witness_state,
)


NOW = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)


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

    async def test_durable_rejection_blocks_delayed_acquisition_replay(self):
        async with self.sessions() as session:
            acquired = await transition_witness_state(
                session,
                action=ACTION_ACQUIRE,
                requester_site="webapp_fi",
                expected_epoch=0,
                expected_lease_id=None,
                request_id="postgres-fi-acquire",
                operator="integration@example",
                reason="initial term",
                private_key_base64=self.private_key,
                now=NOW,
            )
            await session.commit()

        command = dict(
            action=ACTION_ACQUIRE,
            requester_site="webapp_ir",
            expected_epoch=1,
            expected_lease_id=acquired.state.lease_id,
            request_id="postgres-delayed-ir",
            operator="hmac:webapp_ir:ir-key",
            reason="outage promotion",
            private_key_base64=self.private_key,
        )
        async with self.sessions() as session:
            try:
                await transition_witness_state(
                    session,
                    **command,
                    now=NOW + timedelta(seconds=10),
                )
            except WriterWitnessError as exc:
                rejection = await persist_witness_rejection(
                    session,
                    action=command["action"],
                    requester_site=command["requester_site"],
                    expected_epoch=command["expected_epoch"],
                    expected_lease_id=command["expected_lease_id"],
                    request_id=command["request_id"],
                    operator=command["operator"],
                    reason=command["reason"],
                    lease_duration_seconds=180,
                    error=exc,
                )
                self.assertFalse(rejection.replayed)
                await session.commit()
            else:
                self.fail("live FI lease must reject IR acquisition")

        async with self.sessions() as session:
            with self.assertRaises(WriterWitnessError) as replay:
                await transition_witness_state(
                    session,
                    **command,
                    now=NOW + timedelta(seconds=181),
                )
            await session.rollback()
            durable = await load_witness_snapshot(session)

        self.assertTrue(replay.exception.replayed)
        self.assertEqual(durable.holder_site, "webapp_fi")
        self.assertEqual(durable.writer_epoch, 1)

    async def test_persistent_fi_one_ir_two_fi_three_lifecycle_without_reset(self):
        transitions = (
            ("webapp_fi", 0, None, "persistent-fi-epoch-1", NOW),
            ("webapp_ir", 1, "previous", "persistent-ir-epoch-2", NOW + timedelta(seconds=181)),
            ("webapp_fi", 2, "previous", "persistent-fi-epoch-3", NOW + timedelta(seconds=362)),
        )
        previous_lease_id = None
        observed = []
        for site, expected_epoch, lease_selector, request_id, transition_time in transitions:
            async with self.sessions() as session:
                result = await transition_witness_state(
                    session,
                    action=ACTION_ACQUIRE,
                    requester_site=site,
                    expected_epoch=expected_epoch,
                    expected_lease_id=(
                        previous_lease_id if lease_selector == "previous" else None
                    ),
                    request_id=request_id,
                    operator="integration@example",
                    reason=f"persistent lifecycle transition to {site}",
                    private_key_base64=self.private_key,
                    now=transition_time,
                )
                await session.commit()
            observed.append((result.state.holder_site, result.state.writer_epoch))
            previous_lease_id = result.state.lease_id

        async with self.sessions() as session:
            durable = await load_witness_snapshot(session)
            receipt_count = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM webapp_writer_witness_receipts "
                        "WHERE request_id LIKE 'persistent-%-epoch-%'"
                    )
                )
            ).scalar_one()

        self.assertEqual(
            observed,
            [("webapp_fi", 1), ("webapp_ir", 2), ("webapp_fi", 3)],
        )
        self.assertEqual(durable.holder_site, "webapp_fi")
        self.assertEqual(durable.writer_epoch, 3)
        self.assertEqual(durable.lease_id, previous_lease_id)
        self.assertEqual(receipt_count, 3)

    async def test_campaign_expiry_is_rechecked_after_waiting_for_writer_row_lock(self):
        async with self.sessions() as blocker, self.sessions() as contender, self.sessions() as observer:
            await blocker.execute(
                text(
                    "SELECT authority FROM webapp_writer_witness_state "
                    "WHERE authority = 'webapp' FOR UPDATE"
                )
            )
            expires_at = (
                await observer.execute(
                    text("SELECT clock_timestamp() + interval '1 second'")
                )
            ).scalar_one()
            contender_pid = (
                await contender.execute(text("SELECT pg_backend_pid()"))
            ).scalar_one()

            transition = asyncio.create_task(
                transition_witness_state(
                    contender,
                    action=ACTION_ACQUIRE,
                    requester_site="webapp_fi",
                    expected_epoch=0,
                    expected_lease_id=None,
                    request_id="campaign-post-lock-expiry",
                    operator="hmac:webapp_fi",
                    reason="prove post-lock campaign expiry",
                    private_key_base64=self.private_key,
                    authorization_not_after=expires_at,
                )
            )
            for _ in range(50):
                wait_event = (
                    await observer.execute(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity "
                            "WHERE pid = :pid"
                        ),
                        {"pid": contender_pid},
                    )
                ).scalar_one_or_none()
                if wait_event == "Lock":
                    break
                await asyncio.sleep(0.02)
            else:
                transition.cancel()
                await blocker.rollback()
                self.fail("campaign contender never reached the PostgreSQL row-lock barrier")

            await observer.execute(
                text(
                    "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM "
                    "(:expires_at - clock_timestamp()))) + 0.1)"
                ),
                {"expires_at": expires_at},
            )
            await blocker.commit()
            with self.assertRaises(WriterWitnessCampaignExpiredError):
                await transition
            await contender.rollback()

            receipt_count = (
                await observer.execute(
                    text(
                        "SELECT count(*) FROM webapp_writer_witness_receipts "
                        "WHERE request_id = 'campaign-post-lock-expiry'"
                    )
                )
            ).scalar_one()
            state = await load_witness_snapshot(observer)
            self.assertEqual(receipt_count, 0)
            self.assertEqual(state.writer_epoch, 0)
            self.assertIsNone(state.holder_site)


if __name__ == "__main__":
    unittest.main()
