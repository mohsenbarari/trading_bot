import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from core.services import telegram_admin_broadcast_service as service
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcast,
    TelegramAdminBroadcastAudienceType,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
    TelegramAdminBroadcastStatus,
)
from models.user import UserRole


class FakeQueueDB:
    def __init__(self):
        self.added = []
        self.add_all_batches = []
        self.flush_count = 0
        self._next_id = 100

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, objects):
        batch = list(objects)
        self.add_all_batches.append(batch)
        self.added.extend(batch)

    async def flush(self):
        self.flush_count += 1
        for obj in self.added:
            if isinstance(obj, (TelegramAdminBroadcast, TelegramAdminBroadcastReceipt)) and getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1


class FakeRowsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class FakeMetadataDB:
    def __init__(self, rows):
        self.rows = rows
        self.execute_calls = []

    async def execute(self, statement):
        self.execute_calls.append(statement)
        return FakeRowsResult(self.rows)


class TelegramAdminBroadcastServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_dedupe_key_is_stable_and_local_id_independent(self):
        self.assertEqual(
            service.telegram_admin_broadcast_dedupe_key(broadcast_id=42, recipient_user_id=7),
            "telegram-admin-broadcast:42:7",
        )

    def test_content_validation_rejects_empty_and_over_limit_text(self):
        with self.assertRaisesRegex(service.TelegramAdminBroadcastValidationError, "content_required"):
            service.validate_telegram_admin_broadcast_content("   ")

        with self.assertRaisesRegex(service.TelegramAdminBroadcastValidationError, "content_too_long"):
            service.validate_telegram_admin_broadcast_content("x" * (service.TELEGRAM_BROADCAST_TEXT_MAX_LENGTH + 1))

        self.assertEqual(service.validate_telegram_admin_broadcast_content("  پیام  "), "پیام")

    def test_group_taxonomy_sql_matches_locked_policy_shape(self):
        base_sql = str(service._base_bot_recipient_stmt().compile(dialect=postgresql.dialect()))
        self.assertIn("users.telegram_id IS NOT NULL", base_sql)
        self.assertIn("users.is_deleted IS false", base_sql)
        self.assertIn("accountant_relations", base_sql)
        self.assertIn("customer_relations", base_sql)

        ordinary_sql = str(
            service._apply_group_filters(
                service._base_bot_recipient_stmt(),
                [service.TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY],
            ).compile(dialect=postgresql.dialect())
        )
        self.assertIn("users.role =", ordinary_sql)
        self.assertIn("users.id NOT IN", ordinary_sql)

        managers_sql = str(
            service._apply_group_filters(
                service._base_bot_recipient_stmt(),
                [service.TELEGRAM_ADMIN_BROADCAST_GROUP_MANAGERS],
            ).compile(dialect=postgresql.dialect())
        )
        self.assertIn("users.role IN", managers_sql)

        tier1_sql = str(
            service._apply_group_filters(
                service._base_bot_recipient_stmt(),
                [service.TELEGRAM_ADMIN_BROADCAST_GROUP_TIER1_CUSTOMERS],
            ).compile(dialect=postgresql.dialect())
        )
        self.assertIn("customer_relations.customer_tier", tier1_sql)

        with self.assertRaisesRegex(service.TelegramAdminBroadcastValidationError, "unsupported_group"):
            service._normalize_groups(["ordinary", "unknown"])

    async def test_customer_management_name_is_used_for_recipient_display(self):
        db = FakeMetadataDB(rows=[(7, "tier1", "نام مدیریت‌شده")])
        recipients = await service._recipients_from_users(
            db,
            [
                SimpleNamespace(
                    id=7,
                    telegram_id=9007,
                    account_name="customer_9007",
                    full_name="Customer Raw",
                    username=None,
                    mobile_number="09120000000",
                    role=UserRole.STANDARD,
                )
            ],
        )

        self.assertEqual(len(recipients), 1)
        self.assertEqual(recipients[0].display_name, "نام مدیریت‌شده")
        self.assertEqual(recipients[0].customer_tier, "tier1")

    async def test_create_broadcast_queues_receipts_without_calling_telegram(self):
        db = FakeQueueDB()
        actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
        recipients = (
            service.TelegramAdminBroadcastRecipient(user_id=7, telegram_id=9007, account_name="user7"),
            service.TelegramAdminBroadcastRecipient(user_id=8, telegram_id=9008, account_name="user8"),
        )

        with patch(
            "core.services.telegram_admin_broadcast_service.resolve_telegram_admin_broadcast_recipients",
            new=AsyncMock(return_value=recipients),
        ) as resolver:
            result = await service.create_telegram_admin_broadcast(
                db,
                actor=actor,
                content=" پیام تست ",
                audience_type=TelegramAdminBroadcastAudienceType.SELECTED,
                selected_user_ids=[7, 8],
            )

        resolver.assert_awaited_once()
        self.assertEqual(result.broadcast.content, "پیام تست")
        self.assertEqual(result.broadcast.created_by_id, 1)
        self.assertEqual(result.broadcast.audience_type, TelegramAdminBroadcastAudienceType.SELECTED)
        self.assertEqual(result.broadcast.status, TelegramAdminBroadcastStatus.QUEUED)
        self.assertEqual(result.broadcast.recipient_count, 2)
        self.assertEqual(result.receipt_count, 2)

        receipts = [obj for obj in db.added if isinstance(obj, TelegramAdminBroadcastReceipt)]
        self.assertEqual(len(receipts), 2)
        self.assertEqual(
            {receipt.dedupe_key for receipt in receipts},
            {
                f"telegram-admin-broadcast:{result.broadcast.id}:7",
                f"telegram-admin-broadcast:{result.broadcast.id}:8",
            },
        )
        self.assertEqual({receipt.status for receipt in receipts}, {TelegramAdminBroadcastReceiptStatus.PENDING})
        self.assertEqual({receipt.telegram_id_at_enqueue for receipt in receipts}, {9007, 9008})

    async def test_create_empty_broadcast_completes_without_receipts(self):
        db = FakeQueueDB()
        actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)

        with patch(
            "core.services.telegram_admin_broadcast_service.resolve_telegram_admin_broadcast_recipients",
            new=AsyncMock(return_value=()),
        ):
            result = await service.create_telegram_admin_broadcast(
                db,
                actor=actor,
                content="بدون گیرنده",
                audience_type=TelegramAdminBroadcastAudienceType.ALL,
            )

        self.assertEqual(result.broadcast.status, TelegramAdminBroadcastStatus.COMPLETED)
        self.assertEqual(result.broadcast.recipient_count, 0)
        self.assertEqual(result.receipt_count, 0)
        self.assertFalse([obj for obj in db.added if isinstance(obj, TelegramAdminBroadcastReceipt)])

    async def test_create_requires_superadmin_and_selected_cap(self):
        with self.assertRaisesRegex(service.TelegramAdminBroadcastValidationError, "superadmin_required"):
            await service.create_telegram_admin_broadcast(
                FakeQueueDB(),
                actor=SimpleNamespace(id=2, role=UserRole.MIDDLE_MANAGER),
                content="پیام",
                audience_type=TelegramAdminBroadcastAudienceType.ALL,
            )

        with self.assertRaisesRegex(service.TelegramAdminBroadcastValidationError, "too_many_selected_recipients"):
            await service.resolve_telegram_admin_broadcast_recipients(
                FakeQueueDB(),
                audience_type=TelegramAdminBroadcastAudienceType.SELECTED,
                selected_user_ids=range(1, service.TELEGRAM_BROADCAST_SELECTED_RECIPIENT_CAP + 2),
            )


if __name__ == "__main__":
    unittest.main()
