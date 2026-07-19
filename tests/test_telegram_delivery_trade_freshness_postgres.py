import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.enums import SettlementType, UserAccountStatus
from core.services.telegram_delivery_queue_service import (
    claim_next_telegram_delivery_job,
    enqueue_telegram_delivery_job,
)
from core.services.trade_delivery_receipt_service import (
    trade_completed_receipt_dedupe_key,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessOutcome,
)
from core.telegram_delivery_trade_freshness import (
    TRADE_RESULT_TEMPLATE_VERSION,
    build_trade_result_delivery_payload,
    telegram_private_user_destination_key,
    trade_result_delivery_deadline,
    trade_result_delivery_source_version,
    trade_result_source_natural_id,
    validate_trade_result_telegram_delivery_freshness,
)
from core.telegram_delivery_trade_result_binding import (
    trade_result_queue_receipt_worker_id,
)
from models.commodity import Commodity
from models.trade import Trade, TradeStatus, TradeType
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)
from models.user import User, UserRole
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramDeliveryTradeFreshnessPostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_url, _ = DATABASE_URLS
        _run_alembic(sync_url, "upgrade", "head")

    async def asyncSetUp(self):
        _, async_url = DATABASE_URLS
        self.engine = create_async_engine(async_url, pool_pre_ping=True)
        self.Session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        async with self.Session() as db:
            await db.execute(
                text(
                    "TRUNCATE TABLE telegram_delivery_runtime_gates, "
                    "telegram_delivery_jobs, trade_delivery_receipts, "
                    "trades, users, commodities RESTART IDENTITY CASCADE"
                )
            )
            await db.execute(
                text(
                    "ALTER SEQUENCE telegram_delivery_jobs_enqueued_seq_seq "
                    "RESTART WITH 1"
                )
            )
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _user(*, account_name: str, mobile: str, telegram_id: int) -> User:
        return User(
            account_name=account_name,
            mobile_number=mobile,
            full_name=account_name,
            address="test",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            telegram_id=telegram_id,
            sync_version=1,
            is_deleted=False,
            has_bot_access=True,
        )

    @staticmethod
    def _receipt_payload(
        *,
        trade_number: int,
        recipient_user_id: int,
        recipient_role: str,
        telegram_id: int,
        message: str,
    ) -> dict:
        return {
            "message": message,
            "telegram_id_at_audience_build": telegram_id,
            "extra_payload": {
                "trade_number": trade_number,
                "recipient_user_id": recipient_user_id,
                "recipient_role": recipient_role,
                "side": recipient_role,
            },
        }

    async def _enqueue_trade_result(self, db, receipt, user):
        result = await enqueue_telegram_delivery_job(
            db,
            current_server="foreign",
            feeder=TelegramFeederKind.TRADE,
            source_natural_id=trade_result_source_natural_id(receipt),
            source_version=trade_result_delivery_source_version(user),
            action=TelegramDeliveryAction.TRADE_RESULT,
            bot_identity="primary",
            destination_key=telegram_private_user_destination_key(
                receipt.recipient_user_id
            ),
            destination_class=TelegramDestinationClass.PRIVATE,
            method="sendMessage",
            payload=build_trade_result_delivery_payload(receipt, user),
            template_version=TRADE_RESULT_TEMPLATE_VERSION,
            delivery_deadline_at=trade_result_delivery_deadline(
                receipt.event_created_at
            ),
            run_id="trade-freshness-postgres",
        )
        receipt.worker_id = trade_result_queue_receipt_worker_id(
            int(result.job.id)
        )
        await db.flush()
        return result

    async def test_two_recipients_are_independent_and_overdue_pending_result_is_m0(self):
        committed_at = datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)
        async with self.Session() as db:
            buyer = self._user(
                account_name="trade_freshness_buyer",
                mobile="09120000101",
                telegram_id=910101,
            )
            seller = self._user(
                account_name="trade_freshness_seller",
                mobile="09120000102",
                telegram_id=910102,
            )
            commodity = Commodity(name="trade_freshness_gold")
            db.add_all([buyer, seller, commodity])
            await db.flush()
            trade = Trade(
                trade_number=18025,
                offer_user_id=seller.id,
                responder_user_id=buyer.id,
                commodity_id=commodity.id,
                trade_type=TradeType.BUY,
                settlement_type=SettlementType.CASH,
                quantity=2,
                price=75_000_000,
                status=TradeStatus.COMPLETED,
                created_at=committed_at,
            )
            db.add(trade)
            await db.flush()

            pending = TradeDeliveryReceipt(
                event_type="trade_completed",
                dedupe_key=trade_completed_receipt_dedupe_key(
                    channel=TradeDeliveryChannel.TELEGRAM,
                    trade_number=trade.trade_number,
                    recipient_user_id=buyer.id,
                ),
                trade_id=trade.id,
                trade_number=trade.trade_number,
                recipient_user_id=buyer.id,
                recipient_role="responder",
                channel=TradeDeliveryChannel.TELEGRAM,
                destination_server="foreign",
                status=TradeDeliveryReceiptStatus.PENDING,
                audit_payload=self._receipt_payload(
                    trade_number=trade.trade_number,
                    recipient_user_id=buyer.id,
                    recipient_role="responder",
                    telegram_id=int(buyer.telegram_id),
                    message="نتیجه معامله خریدار",
                ),
                event_created_at=committed_at,
            )
            sent = TradeDeliveryReceipt(
                event_type="trade_completed",
                dedupe_key=trade_completed_receipt_dedupe_key(
                    channel=TradeDeliveryChannel.TELEGRAM,
                    trade_number=trade.trade_number,
                    recipient_user_id=seller.id,
                ),
                trade_id=trade.id,
                trade_number=trade.trade_number,
                recipient_user_id=seller.id,
                recipient_role="offer_owner",
                channel=TradeDeliveryChannel.TELEGRAM,
                destination_server="foreign",
                status=TradeDeliveryReceiptStatus.SENT,
                telegram_message_id=77102,
                audit_payload=self._receipt_payload(
                    trade_number=trade.trade_number,
                    recipient_user_id=seller.id,
                    recipient_role="offer_owner",
                    telegram_id=int(seller.telegram_id),
                    message="نتیجه معامله فروشنده",
                ),
                event_created_at=committed_at,
                sent_at=committed_at + timedelta(seconds=1),
                terminal_at=committed_at + timedelta(seconds=1),
            )
            db.add_all([pending, sent])
            await db.flush()
            pending_job = (await self._enqueue_trade_result(db, pending, buyer)).job
            sent_job = (await self._enqueue_trade_result(db, sent, seller)).job
            await enqueue_telegram_delivery_job(
                db,
                current_server="foreign",
                feeder=TelegramFeederKind.OFFER_CONTROL,
                source_natural_id="trade-freshness-competing-offer",
                source_version=1,
                action=TelegramDeliveryAction.OFFER_PUBLISH,
                bot_identity="primary",
                destination_key="channel:-100100",
                destination_class=TelegramDestinationClass.CHANNEL,
                method="sendMessage",
                payload={"chat_id": -100100, "text": "offer"},
                template_version="test-v1",
            )
            await db.commit()

        async with self.Session() as db:
            pending_decision = await validate_trade_result_telegram_delivery_freshness(
                db,
                pending_job,
                committed_at + timedelta(seconds=4),
            )
            sent_decision = await validate_trade_result_telegram_delivery_freshness(
                db,
                sent_job,
                committed_at + timedelta(seconds=4),
            )
        self.assertEqual(pending_decision.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(sent_decision.outcome, TelegramFreshnessOutcome.SENT_NOOP)

        async with self.Session() as db:
            before_deadline = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id="trade-before-deadline",
                request_timeout_seconds=10,
                lease_seconds=25,
                now=committed_at + timedelta(seconds=4),
            )
            await db.commit()
        self.assertEqual(
            before_deadline.action_kind,
            TelegramDeliveryAction.OFFER_PUBLISH,
        )

        async with self.Session() as db:
            at_deadline = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id="trade-at-deadline",
                request_timeout_seconds=10,
                lease_seconds=25,
                now=committed_at + timedelta(seconds=5),
            )
            await db.commit()
        self.assertEqual(at_deadline.id, pending_job.id)

    async def test_recipient_linkage_race_is_suppressed_after_database_reread(self):
        committed_at = datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc)
        async with self.Session() as db:
            user = self._user(
                account_name="trade_freshness_relink",
                mobile="09120000103",
                telegram_id=910103,
            )
            counterparty = self._user(
                account_name="trade_freshness_counterparty",
                mobile="09120000104",
                telegram_id=910104,
            )
            commodity = Commodity(name="trade_freshness_silver")
            db.add_all([user, counterparty, commodity])
            await db.flush()
            trade = Trade(
                trade_number=18026,
                offer_user_id=counterparty.id,
                responder_user_id=user.id,
                commodity_id=commodity.id,
                trade_type=TradeType.BUY,
                settlement_type=SettlementType.CASH,
                quantity=1,
                price=5_000_000,
                status=TradeStatus.COMPLETED,
                created_at=committed_at,
            )
            db.add(trade)
            await db.flush()
            receipt = TradeDeliveryReceipt(
                event_type="trade_completed",
                dedupe_key=trade_completed_receipt_dedupe_key(
                    channel=TradeDeliveryChannel.TELEGRAM,
                    trade_number=trade.trade_number,
                    recipient_user_id=user.id,
                ),
                trade_id=trade.id,
                trade_number=trade.trade_number,
                recipient_user_id=user.id,
                recipient_role="responder",
                channel=TradeDeliveryChannel.TELEGRAM,
                destination_server="foreign",
                status=TradeDeliveryReceiptStatus.PENDING,
                audit_payload=self._receipt_payload(
                    trade_number=trade.trade_number,
                    recipient_user_id=user.id,
                    recipient_role="responder",
                    telegram_id=int(user.telegram_id),
                    message="نتیجه معامله",
                ),
                event_created_at=committed_at,
            )
            db.add(receipt)
            await db.flush()
            job = (await self._enqueue_trade_result(db, receipt, user)).job
            await db.commit()

        async with self.Session() as db:
            first = await validate_trade_result_telegram_delivery_freshness(
                db,
                job,
                committed_at + timedelta(seconds=1),
            )
            current_user = await db.get(User, user.id)
            current_user.telegram_id = 920103
            current_user.sync_version = 2
            await db.commit()
        async with self.Session() as db:
            after_relink = await validate_trade_result_telegram_delivery_freshness(
                db,
                job,
                committed_at + timedelta(seconds=2),
            )

        self.assertEqual(first.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(after_relink.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(
            after_relink.reason,
            "trade_freshness_recipient_relinked",
        )


if __name__ == "__main__":
    unittest.main()
