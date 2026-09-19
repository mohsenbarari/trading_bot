"""Hermetic characterization tests for customer-actor fast denial in accountants router handlers."""

from __future__ import annotations

import os
import unittest
from unittest import mock

# Module bootstrap: establish test environment and install network fence before application imports
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from fastapi import HTTPException

from api.routers.accountants import (
    create_my_accountant,
    get_my_accountant,
    list_my_accountants,
    update_my_accountant,
)
from core.enums import UserAccountStatus
from core.services.accountant_relation_service import EffectiveOwnerActor
from models.user import User, UserRole


class StrictDatabaseSentinel:
    """Fail-closed database sentinel that forbids and records any attribute access."""

    def __init__(self) -> None:
        object.__setattr__(self, "accessed_attributes", [])

    def __getattr__(self, name: str) -> None:
        self.accessed_attributes.append(name)
        raise AssertionError(f"StrictDatabaseSentinel attribute {name!r} was unexpectedly accessed")


class StrictPayloadSentinel:
    """Opaque payload sentinel that records and forbids any attribute read or mutation."""

    def __init__(self) -> None:
        object.__setattr__(self, "accessed_attributes", [])

    def __getattr__(self, name: str) -> None:
        self.accessed_attributes.append(name)
        raise AssertionError(f"StrictPayloadSentinel attribute {name!r} was unexpectedly accessed")


class AccountantsCustomerActorGuardsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization tests for ensure_owner_context customer-actor fast denial."""

    def setUp(self) -> None:
        super().setUp()
        self.fence = install_p1_00_network_fence()
        self.db = StrictDatabaseSentinel()
        self.actor_user = User(
            id=101,
            account_name="customer_actor",
            mobile_number="09121111111",
            normalized_account_name="customer_actor",
            normalized_mobile_number="09121111111",
            full_name="Customer Actor",
            address="Tehran, District 1",
            role=UserRole.WATCH,
            account_status=UserAccountStatus.ACTIVE,
        )
        self.actor_user.is_customer = True

        self.owner_user = User(
            id=202,
            account_name="business_owner",
            mobile_number="09122222222",
            normalized_account_name="business_owner",
            normalized_mobile_number="09122222222",
            full_name="Business Owner",
            address="Tehran, District 2",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
        )
        self.owner_user.is_customer = False

        self.context = EffectiveOwnerActor(
            owner_user=self.owner_user,
            actor_user=self.actor_user,
            relation=None,
            is_accountant_context=False,
        )

    async def test_list_my_accountants_denies_customer_actor_before_reads(self) -> None:
        before_denied = len(self.fence["denied_connects"])
        before_redis = len(self.fence["live_redis_constructs"])

        with mock.patch("api.routers.accountants.list_owner_accountant_relations") as mock_list, \
             mock.patch("api.routers.accountants.load_invitation_sms_status_map") as mock_sms, \
             mock.patch("api.routers.accountants.serialize_accountant_relation") as mock_serialize, \
             mock.patch("api.routers.accountants.is_user_customer") as mock_is_customer:

            with self.assertRaises(HTTPException) as exc_info:
                await list_my_accountants(context=self.context, db=self.db)

            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(exc_info.exception.detail, "Customers cannot manage owner accountants")
            self.assertEqual(len(self.fence["denied_connects"]), before_denied)
            self.assertEqual(len(self.fence["live_redis_constructs"]), before_redis)
            self.assertEqual(self.db.accessed_attributes, [])
            mock_list.assert_not_called()
            mock_sms.assert_not_called()
            mock_serialize.assert_not_called()
            mock_is_customer.assert_not_called()

    async def test_get_my_accountant_denies_customer_actor_before_relation_lookup(self) -> None:
        before_denied = len(self.fence["denied_connects"])
        before_redis = len(self.fence["live_redis_constructs"])

        with mock.patch("api.routers.accountants.get_owner_accountant_relation") as mock_get, \
             mock.patch("api.routers.accountants.load_invitation_sms_status_map") as mock_sms, \
             mock.patch("api.routers.accountants.serialize_accountant_relation") as mock_serialize, \
             mock.patch("api.routers.accountants.is_user_customer") as mock_is_customer:

            with self.assertRaises(HTTPException) as exc_info:
                await get_my_accountant(relation_id=42, context=self.context, db=self.db)

            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(exc_info.exception.detail, "Customers cannot manage owner accountants")
            self.assertEqual(len(self.fence["denied_connects"]), before_denied)
            self.assertEqual(len(self.fence["live_redis_constructs"]), before_redis)
            self.assertEqual(self.db.accessed_attributes, [])
            mock_get.assert_not_called()
            mock_sms.assert_not_called()
            mock_serialize.assert_not_called()
            mock_is_customer.assert_not_called()

    async def test_create_my_accountant_denies_customer_actor_before_payload_or_creation(self) -> None:
        before_denied = len(self.fence["denied_connects"])
        before_redis = len(self.fence["live_redis_constructs"])
        payload = StrictPayloadSentinel()

        with mock.patch("api.routers.accountants.public_webapp_url_for_links") as mock_url, \
             mock.patch("api.routers.accountants.create_or_reuse_owner_accountant_relation") as mock_create, \
             mock.patch("api.routers.accountants.build_accountant_registration_link") as mock_link, \
             mock.patch("api.routers.accountants.deliver_invitation_sms_once") as mock_sms, \
             mock.patch("api.routers.accountants.audit_log") as mock_audit, \
             mock.patch("api.routers.accountants.serialize_accountant_relation") as mock_serialize, \
             mock.patch("api.routers.accountants.is_user_customer") as mock_is_customer:

            with self.assertRaises(HTTPException) as exc_info:
                await create_my_accountant(payload=payload, context=self.context, db=self.db)

            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(exc_info.exception.detail, "Customers cannot manage owner accountants")
            self.assertEqual(len(self.fence["denied_connects"]), before_denied)
            self.assertEqual(len(self.fence["live_redis_constructs"]), before_redis)
            self.assertEqual(self.db.accessed_attributes, [])
            self.assertEqual(payload.accessed_attributes, [])
            mock_url.assert_not_called()
            mock_create.assert_not_called()
            mock_link.assert_not_called()
            mock_sms.assert_not_called()
            mock_audit.assert_not_called()
            mock_serialize.assert_not_called()
            mock_is_customer.assert_not_called()

    async def test_update_my_accountant_denies_customer_actor_before_payload_or_update(self) -> None:
        before_denied = len(self.fence["denied_connects"])
        before_redis = len(self.fence["live_redis_constructs"])
        payload = StrictPayloadSentinel()

        with mock.patch("api.routers.accountants.update_owner_accountant_relation") as mock_update, \
             mock.patch("api.routers.accountants.audit_log") as mock_audit, \
             mock.patch("api.routers.accountants.serialize_accountant_relation") as mock_serialize, \
             mock.patch("api.routers.accountants.is_user_customer") as mock_is_customer:

            with self.assertRaises(HTTPException) as exc_info:
                await update_my_accountant(relation_id=42, payload=payload, context=self.context, db=self.db)

            self.assertEqual(exc_info.exception.status_code, 403)
            self.assertEqual(exc_info.exception.detail, "Customers cannot manage owner accountants")
            self.assertEqual(len(self.fence["denied_connects"]), before_denied)
            self.assertEqual(len(self.fence["live_redis_constructs"]), before_redis)
            self.assertEqual(self.db.accessed_attributes, [])
            self.assertEqual(payload.accessed_attributes, [])
            mock_update.assert_not_called()
            mock_audit.assert_not_called()
            mock_serialize.assert_not_called()
            mock_is_customer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
