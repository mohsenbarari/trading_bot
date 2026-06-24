import inspect
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.enums import NotificationCategory, NotificationLevel
from api.routers import trades as trades_router
from core.services import trade_webapp_delivery_service as service
from core.services.trade_notification_audience_service import (
    TradeNotificationAudience,
    TradeNotificationAudienceRecipient,
    TradeNotificationChannelRequirement,
)
from models.notification import Notification
from models.trade import TradeStatus, TradeType
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)


NOW = datetime(2026, 6, 23, 10, 10, tzinfo=timezone.utc)


class AsyncNullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeScalarResult:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        value = self.value
        values = list(self.values)
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
        self.begin_nested_count = 0

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
            if isinstance(obj, Notification) and getattr(obj, "id", None) is None:
                obj.id = 91
                obj.created_at = NOW

    async def commit(self):
        self.commit_count += 1

    def begin_nested(self):
        self.begin_nested_count += 1
        return AsyncNullContext()


def make_receipt(*, status=TradeDeliveryReceiptStatus.PROCESSING, user_id=20, notification_id=None, **overrides):
    data = {
        "id": 41,
        "event_type": "trade_completed",
        "dedupe_key": f"trade_completed:webapp:10025:{user_id}",
        "trade_id": 501,
        "trade_number": 10025,
        "offer_id": 77,
        "recipient_user_id": user_id,
        "recipient_role": "responder",
        "channel": TradeDeliveryChannel.WEBAPP,
        "destination_server": "iran",
        "status": status,
        "reason": "webapp_required",
        "notification_id": notification_id,
        "telegram_message_id": None,
        "worker_id": "worker",
        "lease_until": NOW,
        "attempt_count": 1,
        "next_retry_at": None,
        "last_error": None,
        "last_error_class": None,
        "audit_payload": None,
        "event_created_at": NOW,
        "sent_at": None,
        "terminal_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_trade(*, offer_home_server="foreign"):
    return SimpleNamespace(
        id=501,
        trade_number=10025,
        offer_id=77,
        offer=SimpleNamespace(home_server=offer_home_server, notes="notes"),
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


def requirement(channel, *, required=True, reason=None):
    return TradeNotificationChannelRequirement(
        channel=channel,
        destination_server="iran" if channel == "webapp" else "foreign",
        required=required,
        reason=reason or f"{channel}_required",
        telegram_id=9020 if channel == "telegram" and required else None,
        message=f"{channel} message",
    )


def recipient(user_id, *, role="responder", telegram_required=True):
    return TradeNotificationAudienceRecipient(
        recipient_user_id=user_id,
        recipient_role=role,
        principal_user_id=user_id if role != "accountant" else 10,
        side=role,
        counterparty_user_id=10 if user_id != 10 else 20,
        webapp_message="webapp fallback message",
        extra_payload={
            "route": f"/users/{user_id}",
            "counterparty_profile_user_id": 10,
            "counterparty_profile_account_name": "seller",
        },
        channel_requirements=(
            requirement("webapp", required=True, reason="webapp_required"),
            requirement(
                "telegram",
                required=telegram_required,
                reason="telegram_required" if telegram_required else "telegram_unlinked",
            ),
        ),
    )


def audience(*, recipients=None, offer_home_server="foreign"):
    return TradeNotificationAudience(
        event_type="trade_completed",
        trade_id=501,
        trade_number=10025,
        offer_id=77,
        offer_home_server=offer_home_server,
        trade_path_kind=None,
        trade_path_summary=None,
        recipients=tuple(recipients or [recipient(20), recipient(10, role="offer_owner")]),
    )


class TradeWebAppDeliveryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_iran_webapp_delivery_creates_receipt_notification_and_publishes_after_commit(self):
        claimed_receipt = make_receipt(status=TradeDeliveryReceiptStatus.PROCESSING, user_id=20)
        db = FakeDB([
            FakeScalarResult(),
            FakeScalarResult(claimed_receipt),
            FakeScalarResult(),
        ])

        with patch(
            "core.services.trade_webapp_delivery_service.publish_webapp_notification_after_commit",
            new=AsyncMock(),
        ) as publish_mock:
            result = await service.deliver_webapp_trade_notification(
                db,
                trade_number=10025,
                recipient_user_id=20,
                message="trade done",
                current_server="iran",
                trade_id=501,
                offer_id=77,
                recipient_role="responder",
                principal_user_id=20,
                side="responder",
                extra_payload={"route": "/users/10", "counterparty_profile_user_id": 10},
                event_created_at=NOW,
                now=NOW,
            )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_SENT)
        self.assertTrue(result.receipt_created)
        self.assertTrue(result.notification_created)
        self.assertTrue(result.sent_changed)
        self.assertTrue(result.realtime_published)
        self.assertEqual(db.commit_count, 1)
        notification = next(obj for obj in db.added if isinstance(obj, Notification))
        self.assertEqual(notification.dedupe_key, "trade_completed:webapp:10025:20")
        self.assertEqual(notification.extra_payload["trade_id"], 501)
        self.assertEqual(notification.extra_payload["trade_number"], 10025)
        self.assertEqual(notification.extra_payload["offer_id"], 77)
        self.assertEqual(notification.extra_payload["recipient_role"], "responder")
        self.assertEqual(notification.extra_payload["delivery_receipt_id"], 41)
        publish_mock.assert_awaited_once()

    async def test_production_test_isolation_skips_claimed_webapp_receipt_without_notification(self):
        claimed_receipt = make_receipt(status=TradeDeliveryReceiptStatus.PROCESSING, user_id=20)
        db = FakeDB([
            FakeScalarResult(),
            FakeScalarResult(claimed_receipt),
        ])

        with patch(
            "core.services.trade_webapp_delivery_service.should_suppress_user_notification",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.services.trade_webapp_delivery_service.publish_webapp_notification_after_commit",
            new=AsyncMock(),
        ) as publish_mock:
            result = await service.deliver_webapp_trade_notification(
                db,
                trade_number=10025,
                recipient_user_id=20,
                message="trade done",
                current_server="iran",
                trade_id=501,
                offer_id=77,
                recipient_role="responder",
                principal_user_id=20,
                side="responder",
                extra_payload={"route": "/users/10"},
                event_created_at=NOW,
                now=NOW,
            )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_SKIPPED)
        self.assertEqual(result.reason, service.WEBAPP_DELIVERY_PRODUCTION_TEST_ISOLATION_REASON)
        self.assertEqual(claimed_receipt.status, TradeDeliveryReceiptStatus.SKIPPED)
        self.assertEqual(claimed_receipt.reason, service.WEBAPP_DELIVERY_PRODUCTION_TEST_ISOLATION_REASON)
        self.assertIsNone(result.notification)
        self.assertFalse(any(isinstance(item, Notification) for item in db.added))
        self.assertEqual(db.commit_count, 1)
        publish_mock.assert_not_awaited()

    async def test_short_outage_remote_webapp_delivery_still_sends_after_sync_visibility(self):
        claimed_receipt = make_receipt(
            status=TradeDeliveryReceiptStatus.PROCESSING,
            user_id=20,
            audit_payload={"extra_payload": {"offer_home_server": "foreign"}},
            event_created_at=NOW - timedelta(seconds=90),
        )
        db = FakeDB([
            FakeScalarResult(),
            FakeScalarResult(claimed_receipt),
            FakeScalarResult(),
        ])

        with patch(
            "core.services.trade_webapp_delivery_service.publish_webapp_notification_after_commit",
            new=AsyncMock(),
        ) as publish_mock:
            result = await service.deliver_webapp_trade_notification(
                db,
                trade_number=10025,
                recipient_user_id=20,
                message="trade done",
                current_server="iran",
                extra_payload={"offer_home_server": "foreign", "route": "/users/10"},
                event_created_at=NOW - timedelta(seconds=90),
                now=NOW,
            )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_SENT)
        self.assertEqual(claimed_receipt.status, TradeDeliveryReceiptStatus.SENT)
        self.assertEqual(len([obj for obj in db.added if isinstance(obj, Notification)]), 1)
        publish_mock.assert_awaited_once()

    async def test_medium_outage_remote_webapp_delivery_is_skipped_without_user_facing_notification(self):
        claimed_receipt = make_receipt(
            status=TradeDeliveryReceiptStatus.PROCESSING,
            user_id=20,
            audit_payload={"extra_payload": {"offer_home_server": "foreign"}},
            event_created_at=NOW - timedelta(minutes=10),
        )
        db = FakeDB([
            FakeScalarResult(),
            FakeScalarResult(claimed_receipt),
        ])

        with patch(
            "core.services.trade_webapp_delivery_service.publish_webapp_notification_after_commit",
            new=AsyncMock(),
        ) as publish_mock:
            result = await service.deliver_webapp_trade_notification(
                db,
                trade_number=10025,
                recipient_user_id=20,
                message="trade done",
                current_server="iran",
                extra_payload={"offer_home_server": "foreign", "route": "/users/10"},
                event_created_at=NOW - timedelta(minutes=10),
                now=NOW,
            )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_SKIPPED)
        self.assertEqual(result.reason, "expired_delivery_after_outage")
        self.assertEqual(claimed_receipt.status, TradeDeliveryReceiptStatus.SKIPPED)
        self.assertEqual(claimed_receipt.reason, "expired_delivery_after_outage")
        self.assertEqual(len([obj for obj in db.added if isinstance(obj, Notification)]), 0)
        publish_mock.assert_not_awaited()
        self.assertEqual(db.commit_count, 1)

    async def test_foreign_server_only_queues_iran_owned_webapp_receipt(self):
        db = FakeDB([FakeScalarResult()])

        result = await service.deliver_webapp_trade_notification(
            db,
            trade_number=10025,
            recipient_user_id=20,
            message="trade done",
            current_server="foreign",
            trade_id=501,
            offer_id=77,
            recipient_role="responder",
            extra_payload={"route": "/users/10"},
            now=NOW,
        )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_QUEUED_FOR_IRAN)
        self.assertEqual(result.destination_server, "iran")
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(len([obj for obj in db.added if isinstance(obj, TradeDeliveryReceipt)]), 1)
        self.assertEqual(len([obj for obj in db.added if isinstance(obj, Notification)]), 0)

    async def test_foreign_home_trade_is_repaired_on_iran_after_sync_visibility(self):
        trade = make_trade(offer_home_server="foreign")
        recipients = [recipient(20, telegram_required=True), recipient(30, telegram_required=False)]
        call_log = []

        async def fake_deliver(_db, **kwargs):
            call_log.append(kwargs)
            return SimpleNamespace(status=service.WEBAPP_DELIVERY_STATUS_SENT)

        with patch(
            "core.services.trade_webapp_delivery_service.build_trade_completion_notification_audience",
            new=AsyncMock(return_value=audience(recipients=recipients, offer_home_server="foreign")),
        ), patch(
            "core.services.trade_webapp_delivery_service.deliver_webapp_trade_notification",
            new=AsyncMock(side_effect=fake_deliver),
        ):
            results = await service.repair_webapp_trade_delivery_for_trade(
                object(),
                trade,
                current_server="iran",
                now=NOW,
            )

        self.assertEqual(len(results), 2)
        self.assertEqual([call["recipient_user_id"] for call in call_log], [20, 30])
        self.assertTrue(all(call["current_server"] == "iran" for call in call_log))
        self.assertTrue(all(call["trade_id"] == 501 for call in call_log))
        self.assertTrue(all(call["offer_id"] == 77 for call in call_log))

    async def test_repair_includes_customer_chain_and_accountant_roles_but_ignores_telegram_channels(self):
        recipients = [
            recipient(20, role="responder", telegram_required=True),
            recipient(77, role="accountant", telegram_required=False),
            recipient(40, role="customer_owner", telegram_required=True),
        ]
        call_log = []

        async def fake_deliver(_db, **kwargs):
            call_log.append(kwargs)
            return SimpleNamespace(status=service.WEBAPP_DELIVERY_STATUS_SENT)

        with patch(
            "core.services.trade_webapp_delivery_service.build_trade_completion_notification_audience",
            new=AsyncMock(return_value=audience(recipients=recipients, offer_home_server="iran")),
        ), patch(
            "core.services.trade_webapp_delivery_service.deliver_webapp_trade_notification",
            new=AsyncMock(side_effect=fake_deliver),
        ):
            await service.repair_webapp_trade_delivery_for_trade(
                object(),
                make_trade(offer_home_server="iran"),
                current_server="iran",
            )

        self.assertEqual([call["recipient_role"] for call in call_log], ["responder", "accountant", "customer_owner"])
        self.assertEqual([call["recipient_user_id"] for call in call_log], [20, 77, 40])
        self.assertTrue(all(call["message"] == "webapp message" for call in call_log))

    async def test_duplicate_repair_loads_existing_notification_without_creating_duplicate_row(self):
        claimed_receipt = make_receipt(status=TradeDeliveryReceiptStatus.PROCESSING, user_id=20)
        existing_notification = Notification(
            id=91,
            user_id=20,
            message="existing",
            is_read=False,
            level=NotificationLevel.SUCCESS,
            category=NotificationCategory.TRADE,
            dedupe_key="trade_completed:webapp:10025:20",
            extra_payload={"route": "/users/10"},
        )
        db = FakeDB([
            FakeScalarResult(),
            FakeScalarResult(claimed_receipt),
            FakeScalarResult(existing_notification),
        ])

        with patch(
            "core.services.trade_webapp_delivery_service.publish_webapp_notification_after_commit",
            new=AsyncMock(),
        ):
            result = await service.deliver_webapp_trade_notification(
                db,
                trade_number=10025,
                recipient_user_id=20,
                message="trade done",
                current_server="iran",
                trade_id=501,
                offer_id=77,
                recipient_role="responder",
                extra_payload={"route": "/users/10"},
                now=NOW,
            )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_SENT)
        self.assertFalse(result.notification_created)
        self.assertIs(result.notification, existing_notification)
        self.assertEqual(len([obj for obj in db.added if isinstance(obj, Notification)]), 0)
        self.assertEqual(db.commit_count, 1)

    async def test_web_push_or_realtime_failure_does_not_roll_back_durable_notification(self):
        claimed_receipt = make_receipt(status=TradeDeliveryReceiptStatus.PROCESSING, user_id=20)
        db = FakeDB([
            FakeScalarResult(),
            FakeScalarResult(claimed_receipt),
            FakeScalarResult(),
        ])

        with patch(
            "core.services.trade_webapp_delivery_service.publish_webapp_notification_after_commit",
            new=AsyncMock(side_effect=RuntimeError("push down")),
        ), patch.object(service.logger, "warning") as warning_mock:
            result = await service.deliver_webapp_trade_notification(
                db,
                trade_number=10025,
                recipient_user_id=20,
                message="trade done",
                current_server="iran",
                trade_id=501,
                offer_id=77,
                recipient_role="responder",
                extra_payload={"route": "/users/10"},
                now=NOW,
            )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_SENT)
        self.assertEqual(result.publish_error_class, "RuntimeError")
        self.assertFalse(result.realtime_published)
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(len([obj for obj in db.added if isinstance(obj, Notification)]), 1)
        warning_mock.assert_called_once()

    async def test_worker_claims_next_webapp_receipt_on_iran(self):
        claimed_receipt = make_receipt(
            status=TradeDeliveryReceiptStatus.PROCESSING,
            user_id=20,
            audit_payload={
                "message": "trade done",
                "extra_payload": {"route": "/users/10", "trade_number": 10025},
            },
        )
        db = FakeDB([
            FakeScalarResult(claimed_receipt),
            FakeScalarResult(),
        ])

        with patch(
            "core.services.trade_webapp_delivery_service.publish_webapp_notification_after_commit",
            new=AsyncMock(),
        ) as publish_mock:
            result = await service.claim_and_deliver_next_webapp_receipt(
                db,
                current_server="iran",
                now=NOW,
            )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_SENT)
        self.assertEqual(result.trade_number, 10025)
        self.assertEqual(result.recipient_user_id, 20)
        self.assertEqual(claimed_receipt.status, TradeDeliveryReceiptStatus.SENT)
        self.assertEqual(db.commit_count, 1)
        publish_mock.assert_awaited_once()

    async def test_worker_noops_when_no_due_webapp_receipt_exists(self):
        db = FakeDB([FakeScalarResult()])

        result = await service.claim_and_deliver_next_webapp_receipt(
            db,
            current_server="iran",
            now=NOW,
        )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_NO_RECEIPT)
        self.assertEqual(result.reason, "no_due_webapp_receipt")
        self.assertEqual(db.commit_count, 0)

    async def test_worker_refuses_webapp_delivery_outside_iran_without_claiming(self):
        db = FakeDB()

        result = await service.claim_and_deliver_next_webapp_receipt(
            db,
            current_server="foreign",
            now=NOW,
        )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_BLOCKED_WRONG_SERVER)
        self.assertEqual(result.reason, "webapp_iran_only")
        self.assertEqual(db.execute_calls, [])

    async def test_worker_marks_malformed_webapp_receipt_permanent_failed_without_crashing(self):
        claimed_receipt = make_receipt(
            status=TradeDeliveryReceiptStatus.PROCESSING,
            user_id=20,
            audit_payload={"extra_payload": {"route": "/users/10"}},
        )
        db = FakeDB([FakeScalarResult(claimed_receipt)])

        result = await service.claim_and_deliver_next_webapp_receipt(
            db,
            current_server="iran",
            now=NOW,
        )

        self.assertEqual(result.status, service.WEBAPP_DELIVERY_STATUS_PERMANENT_FAILED)
        self.assertEqual(claimed_receipt.status, TradeDeliveryReceiptStatus.PERMANENT_FAILED)
        self.assertEqual(claimed_receipt.reason, "webapp_missing_message_payload")
        self.assertEqual(db.commit_count, 1)

    def test_receipt_backed_webapp_service_does_not_use_generic_notification_helper(self):
        source = inspect.getsource(service)

        self.assertNotIn("create_user_notification", source)
        self.assertNotIn("_legacy_create_user_notification", source)
        self.assertNotIn("send_telegram_message_sync", source)
        self.assertNotIn("_queue_trade_telegram_message", source)

    async def test_trade_router_wrapper_routes_trade_notifications_to_receipt_backed_helper(self):
        db = object()
        with patch(
            "api.routers.trades.deliver_webapp_trade_notification",
            new=AsyncMock(return_value=SimpleNamespace(notification="notif")),
        ) as deliver_mock, patch(
            "api.routers.trades._legacy_create_user_notification",
            new=AsyncMock(),
        ) as legacy_mock, patch(
            "api.routers.trades.current_server",
            return_value="iran",
        ):
            result = await trades_router.create_user_notification(
                db,
                20,
                "trade done",
                level=NotificationLevel.SUCCESS,
                category=NotificationCategory.TRADE,
                extra_payload={
                    "trade_number": 10025,
                    "trade_id": 501,
                    "offer_id": 77,
                    "recipient_role": "responder",
                    "route": "/users/10",
                },
            )

        self.assertEqual(result, "notif")
        legacy_mock.assert_not_awaited()
        deliver_mock.assert_awaited_once()
        kwargs = deliver_mock.await_args.kwargs
        self.assertEqual(kwargs["trade_number"], 10025)
        self.assertEqual(kwargs["recipient_user_id"], 20)
        self.assertEqual(kwargs["current_server"], "iran")
        self.assertEqual(kwargs["recipient_role"], "responder")

    async def test_trade_router_wrapper_requires_trade_number_for_trade_notifications(self):
        db = object()
        with patch(
            "api.routers.trades.deliver_webapp_trade_notification",
            new=AsyncMock(),
        ) as deliver_mock, patch(
            "api.routers.trades._legacy_create_user_notification",
            new=AsyncMock(),
        ) as legacy_mock:
            with self.assertRaisesRegex(ValueError, "trade_notification_requires_trade_number"):
                await trades_router.create_user_notification(
                    db,
                    20,
                    "trade done",
                    level=NotificationLevel.SUCCESS,
                    category=NotificationCategory.TRADE,
                    extra_payload={"route": "/users/10"},
                )

        deliver_mock.assert_not_awaited()
        legacy_mock.assert_not_awaited()

    async def test_trade_router_wrapper_keeps_legacy_helper_for_non_trade_notifications(self):
        db = object()
        with patch(
            "api.routers.trades.deliver_webapp_trade_notification",
            new=AsyncMock(),
        ) as deliver_mock, patch(
            "api.routers.trades._legacy_create_user_notification",
            new=AsyncMock(return_value="legacy"),
        ) as legacy_mock:
            result = await trades_router.create_user_notification(
                db,
                20,
                "system",
                category=NotificationCategory.SYSTEM,
                extra_payload={"route": "/users/10"},
            )

        self.assertEqual(result, "legacy")
        deliver_mock.assert_not_awaited()
        legacy_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
