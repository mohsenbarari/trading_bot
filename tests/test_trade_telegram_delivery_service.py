import inspect
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.telegram_gateway import TelegramGatewayResult
from core.services import trade_telegram_delivery_service as service
from core.services.trade_notification_audience_service import (
    TradeNotificationAudience,
    TradeNotificationAudienceRecipient,
    TradeNotificationChannelRequirement,
)
from models.trade import TradeStatus, TradeType
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)


NOW = datetime(2026, 6, 23, 11, 15, tzinfo=timezone.utc)


class FakeScalarResult:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        values = list(self.values)
        value = self.value
        return SimpleNamespace(
            first=lambda: value if value is not None else (values[0] if values else None),
            all=lambda: values if values else ([value] if value is not None else []),
        )


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.execute_calls = []
        self.added = []
        self.flush_count = 0
        self.commit_count = 0

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        if self.execute_results:
            return self.execute_results.pop(0)
        return FakeScalarResult()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flush_count += 1
        for obj in self.added:
            if isinstance(obj, TradeDeliveryReceipt) and getattr(obj, "id", None) is None:
                obj.id = 41

    async def commit(self):
        self.commit_count += 1


def make_receipt(**overrides):
    data = {
        "id": 41,
        "event_type": "trade_completed",
        "dedupe_key": "trade_completed:telegram:10025:20",
        "trade_id": 501,
        "trade_number": 10025,
        "offer_id": 77,
        "recipient_user_id": 20,
        "recipient_role": "responder",
        "channel": TradeDeliveryChannel.TELEGRAM,
        "destination_server": "foreign",
        "status": TradeDeliveryReceiptStatus.PROCESSING,
        "reason": "telegram_required",
        "notification_id": None,
        "telegram_message_id": None,
        "worker_id": "telegram-worker",
        "lease_until": NOW,
        "attempt_count": 1,
        "next_retry_at": None,
        "last_error": None,
        "last_error_class": None,
        "audit_payload": {"message": "🟢 <b>خرید</b>\n\n🔢 شماره معامله: 10025"},
        "event_created_at": NOW,
        "sent_at": None,
        "terminal_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_user(**overrides):
    data = {
        "id": 20,
        "telegram_id": 9020,
        "account_name": "dev",
        "full_name": "Dev User",
        "role": SimpleNamespace(value="عادی"),
        "is_deleted": False,
        "account_status": SimpleNamespace(value="active"),
        "has_bot_access": True,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_trade():
    return SimpleNamespace(
        id=501,
        trade_number=10025,
        offer_id=77,
        offer=SimpleNamespace(home_server="iran", notes="notes"),
        offer_user_id=10,
        responder_user_id=20,
        commodity_id=3,
        commodity=SimpleNamespace(name="coin"),
        trade_type=TradeType.BUY,
        quantity=20,
        price=150000,
        status=TradeStatus.COMPLETED,
        created_at=NOW,
    )


def requirement(*, channel="telegram", required=True, reason="telegram_required", message="telegram message", telegram_id=9020):
    return TradeNotificationChannelRequirement(
        channel=channel,
        destination_server="foreign",
        required=required,
        reason=reason,
        telegram_id=telegram_id if required else None,
        message=message,
    )


def recipient(user_id=20, *, telegram_required=True, role="responder"):
    return TradeNotificationAudienceRecipient(
        recipient_user_id=user_id,
        recipient_role=role,
        principal_user_id=user_id,
        side=role,
        counterparty_user_id=10,
        webapp_message="webapp fallback",
        extra_payload={"route": "/users/10"},
        channel_requirements=(
            requirement(
                required=telegram_required,
                reason="telegram_required" if telegram_required else "telegram_unlinked",
                message="telegram message",
            ),
        ),
    )


def audience(*, recipients=None):
    return TradeNotificationAudience(
        event_type="trade_completed",
        trade_id=501,
        trade_number=10025,
        offer_id=77,
        offer_home_server="iran",
        trade_path_kind=None,
        trade_path_summary=None,
        recipients=tuple(recipients or [recipient(20)]),
    )


class TradeTelegramClassifierTests(unittest.TestCase):
    def test_success_with_message_id_is_sent(self):
        classification = service.classify_telegram_gateway_result(
            TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                response_json={"ok": True, "result": {"message_id": 555}},
            )
        )

        self.assertEqual(classification.status, TradeDeliveryReceiptStatus.SENT)
        self.assertEqual(classification.reason, "telegram_sent")

    def test_429_retry_after_is_retry_pending(self):
        classification = service.classify_telegram_gateway_result(
            TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=429,
                response_text="Too Many Requests: retry after 12",
                response_json={"ok": False, "parameters": {"retry_after": 12}},
            )
        )

        self.assertEqual(classification.status, TradeDeliveryReceiptStatus.RETRY_PENDING)
        self.assertEqual(classification.reason, "telegram_rate_limited")
        self.assertEqual(classification.retry_after_seconds, 12)

    def test_timeout_network_and_telegram_5xx_are_retry_pending(self):
        for result in (
            TelegramGatewayResult(ok=False, method="sendMessage", error="ReadTimeout"),
            TelegramGatewayResult(ok=False, method="sendMessage", status_code=502, response_text="Bad Gateway"),
        ):
            with self.subTest(result=result):
                classification = service.classify_telegram_gateway_result(result)
                self.assertEqual(classification.status, TradeDeliveryReceiptStatus.RETRY_PENDING)

    def test_user_unreachable_errors_are_skipped(self):
        samples = [
            (403, "Forbidden: bot was blocked by the user"),
            (400, "Bad Request: chat not found"),
            (403, "Forbidden: user is deactivated"),
        ]
        for status_code, response_text in samples:
            with self.subTest(response_text=response_text):
                classification = service.classify_telegram_gateway_result(
                    TelegramGatewayResult(
                        ok=False,
                        method="sendMessage",
                        status_code=status_code,
                        response_text=response_text,
                    )
                )
                self.assertEqual(classification.status, TradeDeliveryReceiptStatus.SKIPPED)
                self.assertEqual(classification.reason, "telegram_user_unreachable")

    def test_config_and_malformed_payload_errors_are_permanent_failed(self):
        samples = [
            TelegramGatewayResult(ok=False, method="sendMessage", error="missing_bot_token"),
            TelegramGatewayResult(ok=False, method="sendMessage", status_code=401, response_text="Unauthorized"),
            TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=400,
                response_text="Bad Request: can't parse entities",
            ),
        ]
        for result in samples:
            with self.subTest(result=result):
                classification = service.classify_telegram_gateway_result(result)
                self.assertEqual(classification.status, TradeDeliveryReceiptStatus.PERMANENT_FAILED)
                self.assertTrue(classification.alert_required)

    def test_unknown_errors_are_not_silently_skipped(self):
        classification = service.classify_telegram_gateway_result(
            TelegramGatewayResult(ok=False, method="sendMessage", error="UnexpectedTelegramShape")
        )

        self.assertEqual(classification.status, TradeDeliveryReceiptStatus.PERMANENT_FAILED)
        self.assertEqual(classification.reason, "telegram_unknown_failure")
        self.assertTrue(classification.alert_required)


class TradeTelegramDeliveryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_iran_cannot_execute_telegram_delivery(self):
        receipt = make_receipt()
        db = FakeDB()
        gateway_send = AsyncMock()

        result = await service.deliver_claimed_telegram_receipt(
            db,
            receipt=receipt,
            current_server="iran",
            gateway_send=gateway_send,
            now=NOW,
        )

        self.assertEqual(result.status, service.TELEGRAM_DELIVERY_STATUS_BLOCKED_WRONG_SERVER)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.PROCESSING)
        gateway_send.assert_not_awaited()
        self.assertEqual(db.commit_count, 0)

    async def test_success_marks_receipt_sent_with_telegram_message_id(self):
        receipt = make_receipt()
        user = make_user(telegram_id=9033)
        db = FakeDB([FakeScalarResult(user)])
        gateway_send = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                response_json={"ok": True, "result": {"message_id": 777}},
            )
        )

        result = await service.deliver_claimed_telegram_receipt(
            db,
            receipt=receipt,
            current_server="foreign",
            gateway_send=gateway_send,
            now=NOW,
        )

        self.assertEqual(result.status, service.TELEGRAM_DELIVERY_STATUS_SENT)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SENT)
        self.assertEqual(receipt.telegram_message_id, 777)
        self.assertEqual(receipt.sent_at, NOW)
        self.assertEqual(receipt.terminal_at, NOW)
        self.assertEqual(db.commit_count, 1)
        gateway_send.assert_awaited_once()
        self.assertEqual(gateway_send.await_args.args[0], 9033)
        self.assertEqual(gateway_send.await_args.kwargs["parse_mode"], "HTML")

    async def test_short_outage_remote_telegram_delivery_still_sends_after_sync_visibility(self):
        receipt = make_receipt(
            audit_payload={
                "message": "trade message",
                "extra_payload": {"offer_home_server": "iran"},
            },
            event_created_at=NOW - timedelta(seconds=90),
        )
        user = make_user(telegram_id=9033)
        db = FakeDB([FakeScalarResult(user)])
        gateway_send = AsyncMock(return_value=TelegramGatewayResult(ok=True, method="sendMessage"))

        result = await service.deliver_claimed_telegram_receipt(
            db,
            receipt=receipt,
            current_server="foreign",
            gateway_send=gateway_send,
            now=NOW,
        )

        self.assertEqual(result.status, service.TELEGRAM_DELIVERY_STATUS_SENT)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SENT)
        gateway_send.assert_awaited_once()

    async def test_long_outage_remote_telegram_delivery_skips_without_user_lookup_or_send(self):
        receipt = make_receipt(
            audit_payload={
                "message": "trade message",
                "extra_payload": {"offer_home_server": "iran"},
            },
            event_created_at=NOW - timedelta(hours=2),
        )
        db = FakeDB()
        gateway_send = AsyncMock()

        result = await service.deliver_claimed_telegram_receipt(
            db,
            receipt=receipt,
            current_server="foreign",
            gateway_send=gateway_send,
            now=NOW,
        )

        self.assertEqual(result.status, service.TELEGRAM_DELIVERY_STATUS_SKIPPED)
        self.assertEqual(result.reason, "expired_delivery_after_outage")
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SKIPPED)
        self.assertEqual(receipt.reason, "expired_delivery_after_outage")
        self.assertEqual(db.execute_calls, [])
        self.assertEqual(db.commit_count, 1)
        gateway_send.assert_not_awaited()

    async def test_rate_limit_marks_retry_pending_with_bounded_due_time(self):
        receipt = make_receipt(attempt_count=3)
        user = make_user()
        db = FakeDB([FakeScalarResult(user)])
        gateway_send = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=429,
                response_text="Too Many Requests",
                response_json={"ok": False, "parameters": {"retry_after": 12}},
            )
        )

        result = await service.deliver_claimed_telegram_receipt(
            db,
            receipt=receipt,
            current_server="foreign",
            gateway_send=gateway_send,
            now=NOW,
            max_jitter_seconds=0,
        )

        self.assertEqual(result.status, service.TELEGRAM_DELIVERY_STATUS_RETRY_PENDING)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.RETRY_PENDING)
        self.assertEqual(receipt.next_retry_at, NOW.replace(second=12))
        self.assertEqual(receipt.reason, "telegram_rate_limited")
        self.assertIsNone(receipt.worker_id)
        self.assertIsNone(receipt.lease_until)

    async def test_blocked_user_is_skipped_without_retry_backlog(self):
        receipt = make_receipt()
        user = make_user()
        db = FakeDB([FakeScalarResult(user)])
        gateway_send = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=403,
                response_text="Forbidden: bot was blocked by the user",
            )
        )

        result = await service.deliver_claimed_telegram_receipt(
            db,
            receipt=receipt,
            current_server="foreign",
            gateway_send=gateway_send,
            now=NOW,
        )

        self.assertEqual(result.status, service.TELEGRAM_DELIVERY_STATUS_SKIPPED)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SKIPPED)
        self.assertEqual(receipt.reason, "telegram_user_unreachable")
        self.assertIsNone(receipt.next_retry_at)
        self.assertEqual(receipt.terminal_at, NOW)

    async def test_latest_linked_telegram_identity_is_reloaded_on_attempt(self):
        receipt = make_receipt(audit_payload={"message": "trade message", "telegram_id_at_audience_build": 1000})
        user = make_user(telegram_id=2000)
        db = FakeDB([FakeScalarResult(user)])
        gateway_send = AsyncMock(return_value=TelegramGatewayResult(ok=True, method="sendMessage"))

        await service.deliver_claimed_telegram_receipt(
            db,
            receipt=receipt,
            current_server="foreign",
            gateway_send=gateway_send,
            now=NOW,
        )

        self.assertEqual(gateway_send.await_args.args[0], 2000)

    async def test_unlinked_current_user_is_skipped_not_pending(self):
        receipt = make_receipt()
        user = make_user(telegram_id=None)
        db = FakeDB([FakeScalarResult(user)])
        gateway_send = AsyncMock()

        result = await service.deliver_claimed_telegram_receipt(
            db,
            receipt=receipt,
            current_server="foreign",
            gateway_send=gateway_send,
            now=NOW,
        )

        self.assertEqual(result.status, service.TELEGRAM_DELIVERY_STATUS_SKIPPED)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SKIPPED)
        self.assertEqual(receipt.reason, "telegram_unlinked_current")
        gateway_send.assert_not_awaited()

    async def test_fixed_relinked_account_does_not_reopen_old_skipped_receipt_but_new_trade_sends(self):
        old_skipped = make_receipt(status=TradeDeliveryReceiptStatus.SKIPPED, terminal_at=NOW, reason="telegram_user_unreachable")
        claimed_new = make_receipt(id=42, trade_number=10026, dedupe_key="trade_completed:telegram:10026:20")
        user = make_user(telegram_id=9010)
        db_old = FakeDB([FakeScalarResult(old_skipped)])
        gateway_send = AsyncMock(return_value=TelegramGatewayResult(ok=True, method="sendMessage"))

        old_result = await service.deliver_telegram_trade_notification(
            db_old,
            trade_number=10025,
            recipient_user_id=20,
            message="old message",
            current_server="foreign",
            gateway_send=gateway_send,
            now=NOW,
        )

        self.assertEqual(old_result.status, service.TELEGRAM_DELIVERY_STATUS_TERMINAL_PRESERVED)
        gateway_send.assert_not_awaited()

        db_new = FakeDB([
            FakeScalarResult(),
            FakeScalarResult(claimed_new),
            FakeScalarResult(user),
        ])
        new_result = await service.deliver_telegram_trade_notification(
            db_new,
            trade_number=10026,
            recipient_user_id=20,
            message="new message",
            current_server="foreign",
            gateway_send=gateway_send,
            now=NOW,
        )

        self.assertEqual(new_result.status, service.TELEGRAM_DELIVERY_STATUS_SENT)
        gateway_send.assert_awaited_once()

    async def test_repair_queues_only_foreign_owned_telegram_receipts_from_audience(self):
        call_log = []

        async def fake_deliver(_db, **kwargs):
            call_log.append(kwargs)
            return SimpleNamespace(status=service.TELEGRAM_DELIVERY_STATUS_QUEUED_FOR_FOREIGN)

        with patch(
            "core.services.trade_telegram_delivery_service.build_trade_completion_notification_audience",
            new=AsyncMock(return_value=audience(recipients=[recipient(20), recipient(30, telegram_required=False)])),
        ), patch(
            "core.services.trade_telegram_delivery_service.deliver_telegram_trade_notification",
            new=AsyncMock(side_effect=fake_deliver),
        ):
            results = await service.repair_telegram_trade_delivery_for_trade(
                object(),
                make_trade(),
                current_server="iran",
            )

        self.assertEqual(len(results), 2)
        self.assertEqual([call["recipient_user_id"] for call in call_log], [20, 30])
        self.assertTrue(all(call["current_server"] == "iran" for call in call_log))
        self.assertTrue(all(call["reason"] in {"telegram_required", "telegram_unlinked"} for call in call_log))

    async def test_worker_claims_only_telegram_receipts_on_foreign(self):
        receipt = make_receipt()
        user = make_user()
        db = FakeDB([FakeScalarResult(receipt), FakeScalarResult(user)])
        gateway_send = AsyncMock(return_value=TelegramGatewayResult(ok=True, method="sendMessage"))

        result = await service.claim_and_deliver_next_telegram_receipt(
            db,
            current_server="foreign",
            gateway_send=gateway_send,
            now=NOW,
        )

        self.assertEqual(result.status, service.TELEGRAM_DELIVERY_STATUS_SENT)
        self.assertGreaterEqual(len(db.execute_calls), 2)

    def test_service_does_not_use_legacy_trade_router_telegram_helpers(self):
        source = inspect.getsource(service)

        self.assertNotIn("send_telegram_message_sync", source)
        self.assertNotIn("_queue_trade_telegram_message", source)


if __name__ == "__main__":
    unittest.main()
