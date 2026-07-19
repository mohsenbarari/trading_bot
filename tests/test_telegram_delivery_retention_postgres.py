import unittest
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.services.telegram_delivery_queue_service import enqueue_telegram_delivery_job
from core.services.telegram_delivery_retention_service import (
    TELEGRAM_DELIVERY_REDACTED_PAYLOAD,
    TelegramDeliveryRetentionError,
    run_telegram_delivery_retention_cycle,
    set_telegram_delivery_retention_legal_hold,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.utils import utc_now
from models.telegram_channel_membership_saga import TelegramChannelMembershipSaga
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_provider_outcome import (
    TELEGRAM_PROVIDER_OUTCOME_PENDING,
    TelegramDeliveryProviderOutcomeRecord,
)
from models.telegram_notification_outbox import (
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramDeliveryRetentionPostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_url, _ = DATABASE_URLS
        _run_alembic(sync_url, "upgrade", "head")

    async def asyncSetUp(self):
        _, async_url = DATABASE_URLS
        self.engine = create_async_engine(async_url, pool_pre_ping=True)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE "
                    "telegram_channel_membership_sagas, "
                    "telegram_delivery_provider_outcomes, "
                    "telegram_delivery_reconciliation_evidence, "
                    "telegram_notification_outbox, telegram_delivery_jobs "
                    "RESTART IDENTITY CASCADE"
                )
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _job(
        self,
        db,
        *,
        key: str,
        age_days: int,
        action: TelegramDeliveryAction = TelegramDeliveryAction.GENERAL_ANNOUNCEMENT,
        method: str = "sendMessage",
        payload: dict | None = None,
        destination_key: str = "private:8477001",
        destination_class: TelegramDestinationClass = TelegramDestinationClass.PRIVATE,
    ) -> TelegramDeliveryJobRecord:
        result = await enqueue_telegram_delivery_job(
            db,
            current_server="foreign",
            feeder=TelegramFeederKind.ADMIN_SYSTEM,
            source_natural_id=f"retention:{key}",
            source_version=1,
            action=action,
            bot_identity="primary",
            destination_key=destination_key,
            destination_class=destination_class,
            method=method,
            payload=payload
            or {"chat_id": 8_477_001, "text": f"private payload {key}"},
            template_version="retention-test-v1",
        )
        job = result.job
        job.state = TelegramDeliveryState.SENT
        job.sent_at = utc_now() - timedelta(days=age_days)
        job.terminal_at = utc_now() - timedelta(days=age_days)
        job.provider_response = {"ok": True, "result": {"message_id": 10}}
        job.last_error_message = "raw provider diagnostic"
        await db.flush()
        return job

    async def test_redacts_payload_and_provider_outcome_after_seven_days(self):
        async with self.Session() as db:
            job = await self._job(db, key="redact", age_days=8)
            db.add(
                TelegramDeliveryProviderOutcomeRecord(
                    job_id=job.id,
                    lease_token=1,
                    worker_id="worker-test",
                    bot_identity="primary",
                    method="sendMessage",
                    gateway_ok=True,
                    provider_status_code=200,
                    provider_response={"ok": True, "result": {"message_id": 10}},
                    outcome_hash="a" * 64,
                    apply_state="applied",
                    apply_attempt_count=1,
                    applied_at=utc_now(),
                    last_apply_error_message="raw feedback diagnostic",
                )
            )
            job_id = int(job.id)
            await db.commit()

        async with self.Session() as db:
            report = await run_telegram_delivery_retention_cycle(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            await db.commit()
            self.assertEqual(report.payload_redacted, 1)
            self.assertEqual(report.provider_outcomes_redacted, 1)

        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, job_id)
            outcome = (
                await db.execute(
                    select(TelegramDeliveryProviderOutcomeRecord).where(
                        TelegramDeliveryProviderOutcomeRecord.job_id == job_id
                    )
                )
            ).scalar_one()
            self.assertEqual(job.payload, TELEGRAM_DELIVERY_REDACTED_PAYLOAD)
            self.assertIsNone(job.provider_response)
            self.assertIsNone(job.last_error_message)
            self.assertIsNotNone(job.payload_redacted_at)
            self.assertIsNone(outcome.provider_response)
            self.assertIsNone(outcome.last_apply_error_message)
            self.assertIsNotNone(outcome.payload_redacted_at)

            replay = await run_telegram_delivery_retention_cycle(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            self.assertEqual(replay.payload_redacted, 0)

    async def test_legal_hold_and_dry_run_never_mutate(self):
        async with self.Session() as db:
            held = await self._job(db, key="held", age_days=40)
            dry = await self._job(db, key="dry", age_days=40)
            await set_telegram_delivery_retention_legal_hold(
                db,
                current_server="foreign",
                job_id=held.id,
                enabled=True,
                reason_code="security.case_open",
                now=utc_now(),
            )
            held_id = int(held.id)
            dry_id = int(dry.id)
            await db.commit()

        async with self.Session() as db:
            report = await run_telegram_delivery_retention_cycle(
                db,
                current_server="foreign",
                now=utc_now(),
                dry_run=True,
            )
            await db.commit()
            self.assertGreaterEqual(report.legal_hold_due, 1)
            self.assertEqual(report.terminal_purged, 0)
            self.assertIsNotNone(await db.get(TelegramDeliveryJobRecord, held_id))
            dry_job = await db.get(TelegramDeliveryJobRecord, dry_id)
            self.assertIsNotNone(dry_job)
            self.assertIsNone(dry_job.payload_redacted_at)
            self.assertNotEqual(dry_job.payload, TELEGRAM_DELIVERY_REDACTED_PAYLOAD)

    async def test_purge_skips_unresolved_and_active_source_then_detaches_terminal_source(self):
        async with self.Session() as db:
            purge = await self._job(db, key="purge", age_days=40)
            unresolved = await self._job(db, key="unresolved", age_days=40)
            active_source = await self._job(db, key="active-source", age_days=40)
            terminal_source = await self._job(db, key="terminal-source", age_days=40)
            db.add(
                TelegramDeliveryProviderOutcomeRecord(
                    job_id=unresolved.id,
                    lease_token=1,
                    worker_id="worker-test",
                    bot_identity="primary",
                    method="sendMessage",
                    gateway_ok=False,
                    outcome_hash="b" * 64,
                    apply_state=TELEGRAM_PROVIDER_OUTCOME_PENDING,
                    apply_attempt_count=0,
                )
            )
            db.add_all(
                [
                    TelegramNotificationOutbox(
                        dedupe_key="retention-active-source",
                        source_type="queue_action:general_announcement",
                        source_id="retention-active-source",
                        telegram_id_at_enqueue=8_477_002,
                        text="active source text",
                        status=TelegramNotificationOutboxStatus.PENDING,
                        queue_job_id=active_source.id,
                        queue_handed_off_at=utc_now() - timedelta(days=40),
                    ),
                    TelegramNotificationOutbox(
                        dedupe_key="retention-terminal-source",
                        source_type="queue_action:general_announcement",
                        source_id="retention-terminal-source",
                        telegram_id_at_enqueue=8_477_003,
                        text="terminal source text",
                        status=TelegramNotificationOutboxStatus.SENT,
                        queue_job_id=terminal_source.id,
                        queue_handed_off_at=utc_now() - timedelta(days=40),
                        terminal_at=utc_now() - timedelta(days=40),
                    ),
                ]
            )
            ids = {
                "purge": int(purge.id),
                "unresolved": int(unresolved.id),
                "active": int(active_source.id),
                "terminal": int(terminal_source.id),
            }
            await db.commit()

        async with self.Session() as db:
            report = await run_telegram_delivery_retention_cycle(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            await db.commit()
            self.assertEqual(report.terminal_purged, 2)
            self.assertEqual(report.unresolved_due, 1)
            self.assertEqual(report.source_blocked_due, 1)

        async with self.Session() as db:
            self.assertIsNone(await db.get(TelegramDeliveryJobRecord, ids["purge"]))
            self.assertIsNone(await db.get(TelegramDeliveryJobRecord, ids["terminal"]))
            self.assertIsNotNone(
                await db.get(TelegramDeliveryJobRecord, ids["unresolved"])
            )
            self.assertIsNotNone(await db.get(TelegramDeliveryJobRecord, ids["active"]))
            source = (
                await db.execute(
                    select(TelegramNotificationOutbox).where(
                        TelegramNotificationOutbox.dedupe_key
                        == "retention-terminal-source"
                    )
                )
            ).scalar_one()
            self.assertIsNone(source.queue_job_id)
            self.assertIsNone(source.queue_handed_off_at)

    async def test_membership_pair_purges_together_and_foreign_guard_fails_closed(self):
        async with self.Session() as db:
            ban = await self._job(
                db,
                key="membership-ban",
                age_days=40,
                action=TelegramDeliveryAction.CHANNEL_MEMBER_BAN,
                method="banChatMember",
                payload={
                    "chat_id": -1001234567890,
                    "user_id": 8_477_004,
                    "revoke_messages": False,
                },
                destination_key="channel:-1001234567890",
                destination_class=TelegramDestinationClass.CHANNEL,
            )
            unban = await self._job(
                db,
                key="membership-unban",
                age_days=40,
                action=TelegramDeliveryAction.CHANNEL_MEMBER_UNBAN,
                method="unbanChatMember",
                payload={
                    "chat_id": -1001234567890,
                    "user_id": 8_477_004,
                    "only_if_banned": True,
                },
                destination_key="channel:-1001234567890",
                destination_class=TelegramDestinationClass.CHANNEL,
            )
            saga = TelegramChannelMembershipSaga(
                source_dedupe_key="retention-membership-saga",
                source_kind="account_deleted",
                source_version=1,
                telegram_id=8_477_004,
                channel_id=-1001234567890,
                state="complete",
                ban_job_id=ban.id,
                unban_job_id=unban.id,
                terminal_at=utc_now() - timedelta(days=40),
                completed_at=utc_now() - timedelta(days=40),
            )
            db.add(saga)
            ids = (int(ban.id), int(unban.id))
            await db.commit()

        async with self.Session() as db:
            report = await run_telegram_delivery_retention_cycle(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            await db.commit()
            self.assertEqual(report.terminal_purged, 2)
            self.assertIsNone(await db.get(TelegramDeliveryJobRecord, ids[0]))
            self.assertIsNone(await db.get(TelegramDeliveryJobRecord, ids[1]))
            self.assertIsNone(
                (
                    await db.execute(select(TelegramChannelMembershipSaga))
                ).scalar_one_or_none()
            )
            with self.assertRaises(TelegramDeliveryRetentionError):
                await run_telegram_delivery_retention_cycle(
                    db,
                    current_server="iran",
                    now=utc_now(),
                )
