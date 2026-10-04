"""Stage P1-00b characterization: HTTP 200 middle-manager self-read.

Assignment: WA-P1-00B-NORMAL-RETURN-20261004-271 (digest 6a90dd308484348959339d5a687fed72d9addac06175dca26c3b0cc144fb22fd)
Parent: WA-P1-00B-FREEZE-20260904-001
Stage: P1-00b
Canonical Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-USERS-READ-USER-L440

Distinct seeds added: 0 (the seed is already canonical).
Single-unit reason: The fail-closed custom AsyncSession permits exactly one
query/result contract (db.get(User, 8)) and the success serializer/delegation
lifecycle is distinct from rejection paths.
Exact scenario: Authenticated middle-manager self-read (actor.id = target.id = 8)
returning HTTP 200 with live authorization guard, live attach_customer_user_context wrapper,
and live schemas.UserRead serializer.
Excluded scenarios / gates: Super-admin-target HTTP 403, middle-manager peer HTTP 403,
and missing user HTTP 404 rejection paths are already characterized and accepted under
prior units and are not repeated here.
"""

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import copy
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.deps
import api.routers.users
import core.db
from core.enums import UserAccountStatus, UserRole
import models.user
from tests.test_p1_00b_characterization_users_read_manager_peer_http import (
    FailClosedAsyncSession,
)
from tests.test_users_router_read_one import make_user


class UsersReadSelfHttpCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    def test_middle_manager_self_read_returns_http_200_with_live_serializer(self) -> None:
        target = make_user(
            id=8,
            role=UserRole.MIDDLE_MANAGER,
            created_at=datetime(2024, 3, 20, 0, 0, 0),
        )
        actor = SimpleNamespace(
            id=8,
            role=UserRole.MIDDLE_MANAGER,
        )

        target_snapshot = copy.deepcopy(target.__dict__)
        actor_snapshot = copy.deepcopy(actor.__dict__)

        app = FastAPI()
        app.include_router(api.routers.users.router, prefix="/api/users")

        fake_db = FailClosedAsyncSession(target_user=target)

        db_override_entries = 0
        auth_override_calls = 0

        async def override_get_db():
            nonlocal db_override_entries
            db_override_entries += 1
            yield fake_db

        async def override_verify_admin_or_dev_key():
            nonlocal auth_override_calls
            auth_override_calls += 1
            return actor

        app.dependency_overrides[core.db.get_db] = override_get_db
        app.dependency_overrides[api.deps.verify_admin_or_dev_key] = (
            override_verify_admin_or_dev_key
        )

        attach_mock = AsyncMock(return_value=[target])

        denied_connects_before = len(self.fence["denied_connects"])
        live_redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "api.routers.users.attach_user_management_relation_context",
            new=attach_mock,
        ):
            try:
                with TestClient(app) as client:
                    response = client.get("/api/users/8")
            finally:
                app.dependency_overrides.clear()

        self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertEqual(response.status_code, 200)
        body = response.json()

        expected_keys = {
            "id",
            "telegram_id",
            "username",
            "full_name",
            "account_name",
            "mobile_number",
            "address",
            "role",
            "account_status",
            "deactivated_at",
            "messenger_grace_expires_at",
            "messenger_blocked_at",
            "has_bot_access",
            "is_accountant",
            "accountant_owner_user_id",
            "accountant_owner_account_name",
            "is_customer",
            "customer_tier",
            "customer_owner_user_id",
            "customer_owner_account_name",
            "customer_management_name",
            "telegram_linked",
            "can_connect_telegram",
            "telegram_link_denial_reason",
            "is_deleted",
            "avatar_file_id",
            "created_at",
            "trading_restricted_until",
            "offer_overtime_minutes",
            "max_daily_trades",
            "max_active_commodities",
            "max_daily_requests",
            "limitations_expire_at",
            "trades_count",
            "commodities_traded_count",
            "channel_messages_count",
            "last_seen_at",
            "can_block_users",
            "max_blocked_users",
            "max_sessions",
            "max_accountants",
            "max_customers",
            "created_at_jalali",
            "global_lock_grace_expires_at",
            "global_web_locked_at",
            "trading_restricted_until_jalali",
            "limitations_expire_at_jalali",
        }
        self.assertEqual(set(body.keys()), expected_keys)

        self.assertEqual(body["id"], 8)
        self.assertEqual(body["role"], "مدیر میانی")
        self.assertEqual(body["account_status"], "active")
        self.assertEqual(body["account_name"], "user7")
        self.assertEqual(body["full_name"], "user7")
        self.assertEqual(body["mobile_number"], "09120000007")
        self.assertEqual(body["created_at"], "2024-03-20T00:00:00")
        self.assertIs(body["has_bot_access"], True)
        self.assertIs(body["is_customer"], False)
        self.assertIs(body["is_accountant"], False)

        self.assertIsNone(body["customer_tier"])
        self.assertIsNone(body["customer_owner_user_id"])
        self.assertIsNone(body["customer_owner_account_name"])
        self.assertIsNone(body["customer_management_name"])
        self.assertIsNone(body["accountant_owner_user_id"])
        self.assertIsNone(body["accountant_owner_account_name"])

        self.assertIsNone(body["telegram_id"])
        self.assertIsNone(body["username"])
        self.assertIsNone(body["address"])
        self.assertIsNone(body["deactivated_at"])
        self.assertIsNone(body["messenger_grace_expires_at"])
        self.assertIsNone(body["messenger_blocked_at"])
        self.assertIs(body["telegram_linked"], False)
        self.assertIs(body["can_connect_telegram"], False)
        self.assertIsNone(body["telegram_link_denial_reason"])
        self.assertIs(body["is_deleted"], False)
        self.assertIsNone(body["avatar_file_id"])
        self.assertIsNone(body["trading_restricted_until"])
        self.assertEqual(body["offer_overtime_minutes"], 0)
        self.assertIsNone(body["max_daily_trades"])
        self.assertIsNone(body["max_active_commodities"])
        self.assertIsNone(body["max_daily_requests"])
        self.assertIsNone(body["limitations_expire_at"])
        self.assertEqual(body["trades_count"], 0)
        self.assertEqual(body["commodities_traded_count"], 0)
        self.assertEqual(body["channel_messages_count"], 0)
        self.assertIsNone(body["last_seen_at"])
        self.assertIs(body["can_block_users"], True)
        self.assertEqual(body["max_blocked_users"], 10)
        self.assertEqual(body["max_sessions"], 1)
        self.assertEqual(body["max_accountants"], 3)
        self.assertEqual(body["max_customers"], 5)

        self.assertIsNone(body["global_lock_grace_expires_at"])
        self.assertIsNone(body["global_web_locked_at"])
        self.assertIsNone(body["trading_restricted_until_jalali"])
        self.assertIsNone(body["limitations_expire_at_jalali"])

        self.assertIsInstance(body["created_at_jalali"], str)
        self.assertTrue(len(body["created_at_jalali"]) > 0)

        self.assertIsInstance(body["id"], int)
        self.assertIsInstance(body["role"], str)
        self.assertIsInstance(body["account_status"], str)
        self.assertIsInstance(body["account_name"], str)
        self.assertIsInstance(body["full_name"], str)
        self.assertIsInstance(body["mobile_number"], str)
        self.assertIsInstance(body["created_at"], str)
        self.assertIsInstance(body["has_bot_access"], bool)
        self.assertIsInstance(body["is_customer"], bool)
        self.assertIsInstance(body["is_accountant"], bool)
        self.assertIsInstance(body["trades_count"], int)
        self.assertIsInstance(body["commodities_traded_count"], int)
        self.assertIsInstance(body["channel_messages_count"], int)
        self.assertIsInstance(body["can_block_users"], bool)
        self.assertIsInstance(body["max_blocked_users"], int)
        self.assertIsInstance(body["max_sessions"], int)
        self.assertIsInstance(body["max_accountants"], int)
        self.assertIsInstance(body["max_customers"], int)

        attach_mock.assert_awaited_once_with(fake_db, [target])
        self.assertEqual(fake_db.get_calls, [(models.user.User, 8)])
        self.assertEqual(db_override_entries, 1)
        self.assertEqual(auth_override_calls, 1)
        self.assertEqual(target.__dict__, target_snapshot)
        self.assertEqual(actor.__dict__, actor_snapshot)
        self.assertEqual(app.dependency_overrides, {})
