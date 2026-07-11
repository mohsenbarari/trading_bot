import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from core.services import telegram_admin_broadcast_service as service
from core.enums import UserAccountStatus
from models.accountant_relation import AccountantRelationStatus
from models.customer_relation import CustomerRelationStatus, CustomerTier
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


class AsyncSyncSession:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)


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

    async def test_recipient_resolution_behaves_for_supported_user_taxonomy(self):
        engine = create_engine("sqlite:///:memory:")
        session_cls = sessionmaker(bind=engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY,
                        account_name VARCHAR,
                        mobile_number VARCHAR,
                        telegram_id BIGINT,
                        username VARCHAR,
                        full_name VARCHAR,
                        address TEXT,
                        avatar_file_id VARCHAR,
                        role VARCHAR,
                        account_status VARCHAR,
                        deactivated_at DATETIME,
                        messenger_grace_expires_at DATETIME,
                        messenger_blocked_at DATETIME,
                        has_bot_access BOOLEAN,
                        bot_onboarding_required_step INTEGER,
                        bot_onboarding_completed_step INTEGER,
                        bot_onboarding_completed_at DATETIME,
                        is_deleted BOOLEAN,
                        deleted_at DATETIME,
                        admin_password_hash VARCHAR,
                        must_change_password BOOLEAN,
                        trading_restricted_until DATETIME,
                        max_daily_trades INTEGER,
                        max_active_commodities INTEGER,
                        max_daily_requests INTEGER,
                        limitations_expire_at DATETIME,
                        trades_count INTEGER,
                        commodities_traded_count INTEGER,
                        channel_messages_count INTEGER,
                        max_sessions INTEGER,
                        max_accountants INTEGER,
                        max_customers INTEGER,
                        home_server VARCHAR,
                        sync_version BIGINT NOT NULL DEFAULT 1,
                        can_block_users BOOLEAN,
                        max_blocked_users INTEGER,
                        last_seen_at DATETIME,
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE customer_relations (
                        id INTEGER PRIMARY KEY,
                        owner_user_id INTEGER,
                        customer_user_id INTEGER,
                        customer_tier VARCHAR,
                        management_name VARCHAR,
                        status VARCHAR,
                        deleted_at DATETIME
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE accountant_relations (
                        id INTEGER PRIMARY KEY,
                        owner_user_id INTEGER,
                        accountant_user_id INTEGER,
                        status VARCHAR,
                        deleted_at DATETIME
                    )
                    """
                )
            )

            role_values = {role: role.name for role in UserRole}
            active_status = UserAccountStatus.ACTIVE.value
            inactive_status = UserAccountStatus.INACTIVE.value
            user_rows = [
                (1, "ordinary", UserRole.STANDARD, active_status, 9001, False),
                (2, "police", UserRole.POLICE, active_status, 9002, False),
                (3, "manager", UserRole.MIDDLE_MANAGER, active_status, 9003, False),
                (4, "superadmin", UserRole.SUPER_ADMIN, active_status, 9004, False),
                (5, "watch", UserRole.WATCH, active_status, 9005, False),
                (6, "tier1_raw", UserRole.STANDARD, active_status, 9006, False),
                (7, "tier2_raw", UserRole.STANDARD, active_status, 9007, False),
                (8, "accountant", UserRole.STANDARD, active_status, 9008, False),
                (9, "inactive", UserRole.STANDARD, inactive_status, 9009, False),
                (10, "deleted", UserRole.STANDARD, active_status, 9010, True),
                (11, "unlinked", UserRole.STANDARD, active_status, None, False),
            ]
            for user_id, account_name, role, status, telegram_id, is_deleted in user_rows:
                connection.execute(
                    text(
                        """
                        INSERT INTO users (
                            id, account_name, mobile_number, telegram_id, full_name, address,
                            role, account_status, has_bot_access, bot_onboarding_required_step,
                            bot_onboarding_completed_step, is_deleted, must_change_password,
                            trades_count, commodities_traded_count, channel_messages_count,
                            max_sessions, max_accountants, max_customers, home_server,
                            can_block_users, max_blocked_users
                        )
                        VALUES (
                            :id, :account_name, :mobile_number, :telegram_id, :full_name, '',
                            :role, :account_status, 1, 0,
                            0, :is_deleted, 0,
                            0, 0, 0,
                            1, 3, 5, 'foreign',
                            1, 10
                        )
                        """
                    ),
                    {
                        "id": user_id,
                        "account_name": account_name,
                        "mobile_number": f"091200000{user_id:02d}",
                        "telegram_id": telegram_id,
                        "full_name": f"User {user_id}",
                        "role": role_values[role],
                        "account_status": status,
                        "is_deleted": int(is_deleted),
                    },
                )

            connection.execute(
                text(
                    """
                    INSERT INTO customer_relations (
                        id, owner_user_id, customer_user_id, customer_tier, management_name, status, deleted_at
                    )
                    VALUES
                        (1, 100, 6, :tier1, 'Tier One Managed', :active, NULL),
                        (2, 100, 7, :tier2, 'Tier Two Managed', :active, NULL)
                    """
                ),
                {
                    "tier1": CustomerTier.TIER_1.value,
                    "tier2": CustomerTier.TIER_2.value,
                    "active": CustomerRelationStatus.ACTIVE.value,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO accountant_relations (
                        id, owner_user_id, accountant_user_id, status, deleted_at
                    )
                    VALUES (1, 100, 8, :active, NULL)
                    """
                ),
                {"active": AccountantRelationStatus.ACTIVE.value},
            )

        with session_cls() as sync_session:
            db = AsyncSyncSession(sync_session)

            all_recipients = await service.resolve_telegram_admin_broadcast_recipients(
                db,
                audience_type=TelegramAdminBroadcastAudienceType.ALL,
                sender_user_id=99,
            )
            self.assertEqual({recipient.user_id for recipient in all_recipients}, {1, 2, 3, 4, 6})

            ordinary_recipients = await service.resolve_telegram_admin_broadcast_recipients(
                db,
                audience_type=TelegramAdminBroadcastAudienceType.GROUP,
                target_groups=[service.TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY],
            )
            self.assertEqual([recipient.user_id for recipient in ordinary_recipients], [1])

            manager_recipients = await service.resolve_telegram_admin_broadcast_recipients(
                db,
                audience_type=TelegramAdminBroadcastAudienceType.GROUP,
                target_groups=[service.TELEGRAM_ADMIN_BROADCAST_GROUP_MANAGERS],
            )
            self.assertEqual({recipient.user_id for recipient in manager_recipients}, {3, 4})

            tier1_recipients = await service.resolve_telegram_admin_broadcast_recipients(
                db,
                audience_type=TelegramAdminBroadcastAudienceType.GROUP,
                target_groups=[service.TELEGRAM_ADMIN_BROADCAST_GROUP_TIER1_CUSTOMERS],
            )
            self.assertEqual([recipient.user_id for recipient in tier1_recipients], [6])
            self.assertEqual(tier1_recipients[0].display_name, "Tier One Managed")

            selected_recipients = await service.resolve_telegram_admin_broadcast_recipients(
                db,
                audience_type=TelegramAdminBroadcastAudienceType.SELECTED,
                selected_user_ids=[1, 2, 5, 7, 8, 9, 10, 11],
            )
            self.assertEqual({recipient.user_id for recipient in selected_recipients}, {1, 2})

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
