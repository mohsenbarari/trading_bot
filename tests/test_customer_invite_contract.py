import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.customer_invite import (
    build_customer_invite_account_name,
    build_customer_invite_idempotency_key,
    check_customer_invite_sync_ready,
    normalize_customer_invite_management_name,
    normalize_customer_invite_mobile,
)
from models.customer_relation import CustomerTier


OWNER_IDENTITY = {
    "account_name": "owner7",
    "mobile_number": "09120000007",
    "telegram_id": 700,
}


class CustomerInviteContractTests(unittest.IsolatedAsyncioTestCase):
    def test_normalizes_mobile_account_name_and_idempotency_without_management_name(self):
        self.assertEqual(normalize_customer_invite_mobile("۰۹۱۲۳۴۵۶۷۸۹"), "09123456789")
        self.assertEqual(build_customer_invite_account_name("09123456789"), "customer_09123456789")
        self.assertEqual(normalize_customer_invite_management_name("  مشتری تست  "), "مشتری تست")

        first_key = build_customer_invite_idempotency_key(
            source_server="foreign",
            owner_identity=OWNER_IDENTITY,
            mobile_number="09123456789",
            customer_tier=CustomerTier.TIER_1,
        )
        second_key = build_customer_invite_idempotency_key(
            source_server="foreign",
            owner_identity=OWNER_IDENTITY,
            mobile_number="۰۹۱۲۳۴۵۶۷۸۹",
            customer_tier="tier1",
        )
        self.assertEqual(first_key, second_key)
        self.assertTrue(first_key.startswith("customer-invite:"))
        self.assertNotIn("09123456789", first_key)

    async def test_sync_gate_reads_iran_health_direction_and_local_foreign_queues(self):
        redis_client = SimpleNamespace(llen=AsyncMock(side_effect=[0, 0]))
        iran_health = {
            "redis_ok": True,
            "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
            "unsynced_by_table": {
                "users": 0,
                "customer_relations": 0,
                "accountant_relations": 0,
                "invitations": 0,
            },
        }

        with patch("core.customer_invite.current_server", return_value="foreign"), patch(
            "core.customer_invite.get_redis_client", return_value=redis_client
        ), patch(
            "core.customer_invite._fetch_iran_sync_health", new=AsyncMock(return_value=(iran_health, None))
        ):
            result = await check_customer_invite_sync_ready(wait_seconds=0)

        self.assertTrue(result.ready)

    async def test_sync_gate_blocks_iran_required_table_backlog(self):
        redis_client = SimpleNamespace(llen=AsyncMock(side_effect=[0, 0]))
        iran_health = {
            "redis_ok": True,
            "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
            "unsynced_by_table": {
                "users": 0,
                "customer_relations": 1,
                "accountant_relations": 0,
                "invitations": 0,
            },
        }

        with patch("core.customer_invite.current_server", return_value="foreign"), patch(
            "core.customer_invite.get_redis_client", return_value=redis_client
        ), patch(
            "core.customer_invite._fetch_iran_sync_health", new=AsyncMock(return_value=(iran_health, None))
        ):
            result = await check_customer_invite_sync_ready(wait_seconds=0)

        self.assertFalse(result.ready)
        self.assertEqual(result.reason, "iran_sync_dirty")

    async def test_sync_gate_uses_temporary_redis_client_when_singleton_is_uninitialized(self):
        temporary_client = SimpleNamespace(llen=AsyncMock(side_effect=[0, 0]), aclose=AsyncMock())
        iran_health = {
            "redis_ok": True,
            "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
            "unsynced_by_table": {
                "users": 0,
                "customer_relations": 0,
                "accountant_relations": 0,
                "invitations": 0,
            },
        }

        with patch("core.customer_invite.current_server", return_value="foreign"), patch(
            "core.customer_invite.get_redis_client", side_effect=RuntimeError("Redis client not initialized")
        ), patch("core.customer_invite.redis.Redis", return_value=temporary_client), patch(
            "core.customer_invite._fetch_iran_sync_health", new=AsyncMock(return_value=(iran_health, None))
        ):
            result = await check_customer_invite_sync_ready(wait_seconds=0)

        self.assertTrue(result.ready)
        temporary_client.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
