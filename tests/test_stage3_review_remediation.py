import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

import schemas
from core.invitation_creation_contracts import (
    InvitationRequesterIdentity,
    build_standard_invitation_idempotency_key,
)
from core.registration_contracts import InvitationSMSStatus
from core.sms import SMSDeliveryOutcome
from core.services.invitation_requester_service import (
    InvitationRequesterResolutionError,
    resolve_current_invitation_requester,
)
from core.services.invitation_sms_delivery_service import (
    deliver_invitation_sms_once,
    prepare_invitation_sms_delivery,
)
from core.services.accountant_relation_service import (
    get_pending_accountant_relation_by_invitation_token,
    sweep_expired_pending_accountant_relations,
)
from core.services.customer_relation_service import (
    get_pending_customer_relation_by_invitation_token,
    sweep_expired_pending_customer_relations,
)
from core.sync_authority import IRAN_AUTHORITATIVE_SYNC_TABLES
from models.invitation_sms_delivery import InvitationSMSDelivery


class _ScalarResult:
    def __init__(self, *, one=None, rows=None):
        self.one = one
        self.rows = list(rows or [])

    def scalar_one_or_none(self):
        return self.one

    def scalar_one(self):
        return self.one

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self.rows))


class Stage3ReviewRemediationTests(unittest.IsolatedAsyncioTestCase):
    def test_signed_principal_and_customer_contracts_are_strict_and_canonical(self):
        principal = InvitationRequesterIdentity(
            account_name=" Manager۷ ",
            mobile_number="۰۹۱۲۰۰۰۰۰۰۷",
            telegram_id=7007,
        )
        key = build_standard_invitation_idempotency_key(
            requester_identity=principal,
            account_name="target",
            mobile_number="09121111111",
            role="عادی",
        )
        self.assertNotIn("09120000007", key)
        self.assertEqual(
            key,
            build_standard_invitation_idempotency_key(
                requester_identity={
                    "account_name": "manager7",
                    "mobile_number": "09120000007",
                    "telegram_id": 7007,
                },
                account_name="target",
                mobile_number="09121111111",
                role="عادی",
            ),
        )
        valid = {
            "owner_identity": principal.model_dump(mode="json"),
            "account_name": "customer_09121111111",
            "management_name": "Customer",
            "mobile_number": "09121111111",
            "customer_tier": "tier1",
            "idempotency_key": "customer-invite:" + "a" * 40,
            "source_server": "foreign",
        }
        schemas.InternalCustomerInviteRequest.model_validate(valid)
        with self.assertRaises(ValidationError):
            schemas.InternalCustomerInviteRequest.model_validate(
                {**valid, "future_business_field": True}
            )

    async def test_requester_resolution_ignores_peer_id_hint_and_fails_closed_on_split_or_inactive(self):
        identity = InvitationRequesterIdentity(
            account_name="manager7",
            mobile_number="09120000007",
            telegram_id=7007,
        )
        current = SimpleNamespace(
            id=88,
            account_name="manager7",
            mobile_number="09120000007",
            telegram_id=7007,
            is_deleted=False,
            account_status="active",
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(rows=[current])))
        self.assertIs(
            await resolve_current_invitation_requester(db, identity=identity),
            current,
        )

        split = SimpleNamespace(
            id=7,
            account_name="other",
            mobile_number="09129999999",
            telegram_id=7007,
            is_deleted=False,
            account_status="active",
        )
        db.execute.return_value = _ScalarResult(rows=[current, split])
        with self.assertRaisesRegex(InvitationRequesterResolutionError, "identity_conflict"):
            await resolve_current_invitation_requester(db, identity=identity)

        current.account_status = "inactive"
        db.execute.return_value = _ScalarResult(rows=[current])
        with self.assertRaisesRegex(InvitationRequesterResolutionError, "inactive"):
            await resolve_current_invitation_requester(db, identity=identity)

    def test_relation_and_invitation_sync_authority_is_iran_only(self):
        self.assertTrue(
            {"invitations", "accountant_relations", "customer_relations"}
            <= IRAN_AUTHORITATIVE_SYNC_TABLES
        )

    async def test_foreign_pending_relation_lookup_is_read_only_and_sweeps_fail_closed(self):
        pending = SimpleNamespace(id=71)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ScalarResult(one=pending)),
            commit=AsyncMock(),
        )
        with patch(
            "core.services.accountant_relation_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.accountant_relation_service.sweep_expired_pending_accountant_relations",
            new=AsyncMock(),
        ) as accountant_sweep:
            self.assertIs(
                await get_pending_accountant_relation_by_invitation_token(db, "ACCT-read-only"),
                pending,
            )
        accountant_sweep.assert_not_awaited()
        db.commit.assert_not_awaited()

        db.execute.reset_mock()
        with patch(
            "core.services.customer_relation_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.customer_relation_service.sweep_expired_pending_customer_relations",
            new=AsyncMock(),
        ) as customer_sweep:
            self.assertIs(
                await get_pending_customer_relation_by_invitation_token(db, "CUST-read-only"),
                pending,
            )
        customer_sweep.assert_not_awaited()
        db.commit.assert_not_awaited()

        with patch(
            "core.services.accountant_relation_service.current_server",
            return_value="foreign",
        ), self.assertRaisesRegex(RuntimeError, "requires_iran"):
            await sweep_expired_pending_accountant_relations(db)
        with patch(
            "core.services.customer_relation_service.current_server",
            return_value="foreign",
        ), self.assertRaisesRegex(RuntimeError, "requires_iran"):
            await sweep_expired_pending_customer_relations(db)

    async def test_invitation_sms_result_is_replay_stable_and_ambiguous_is_not_retried(self):
        delivery = InvitationSMSDelivery(
            invitation_id=41,
            status=InvitationSMSStatus.PENDING.value,
            attempt_count=0,
        )
        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    _ScalarResult(one=delivery),
                    _ScalarResult(one=delivery),
                ]
            ),
            commit=AsyncMock(),
        )
        sender = Mock(return_value=True)
        with patch(
            "core.services.invitation_sms_delivery_service.current_server",
            return_value="iran",
        ):
            status = await deliver_invitation_sms_once(
                db,
                invitation_id=41,
                newly_created=True,
                sender=sender,
            )
        self.assertEqual(status, InvitationSMSStatus.ACCEPTED)
        sender.assert_called_once_with()
        self.assertEqual(db.commit.await_count, 2)

        replay_db = SimpleNamespace(
            execute=AsyncMock(return_value=_ScalarResult(one=delivery)),
            commit=AsyncMock(),
        )
        replay_sender = Mock()
        with patch(
            "core.services.invitation_sms_delivery_service.current_server",
            return_value="iran",
        ):
            replay = await deliver_invitation_sms_once(
                replay_db,
                invitation_id=41,
                newly_created=False,
                sender=replay_sender,
            )
        self.assertEqual(replay, InvitationSMSStatus.ACCEPTED)
        replay_sender.assert_not_called()

        uncertain = InvitationSMSDelivery(
            invitation_id=42,
            status=InvitationSMSStatus.PENDING.value,
            attempt_count=1,
            claimed_at=datetime.now(timezone.utc),
        )
        uncertain_db = SimpleNamespace(
            execute=AsyncMock(return_value=_ScalarResult(one=uncertain)),
            commit=AsyncMock(),
        )
        uncertain_sender = Mock()
        with patch(
            "core.services.invitation_sms_delivery_service.current_server",
            return_value="iran",
        ):
            uncertain_status = await deliver_invitation_sms_once(
                uncertain_db,
                invitation_id=42,
                newly_created=False,
                sender=uncertain_sender,
            )
        self.assertEqual(uncertain_status, InvitationSMSStatus.AMBIGUOUS)
        uncertain_sender.assert_not_called()

    async def test_sms_prepare_marks_enabled_legacy_retry_ambiguous_without_send(self):
        invitation = SimpleNamespace(id=51)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ScalarResult(one=None)),
            add=Mock(),
            flush=AsyncMock(),
        )
        with patch(
            "core.services.invitation_sms_delivery_service.current_server",
            return_value="iran",
        ):
            delivery = await prepare_invitation_sms_delivery(
                db,
                invitation=invitation,
                enabled=True,
                newly_created=False,
            )
        self.assertEqual(delivery.status, InvitationSMSStatus.AMBIGUOUS.value)
        self.assertIsNotNone(delivery.completed_at)

    async def test_sms_delivery_persists_explicit_failure_and_transport_ambiguity(self):
        for invitation_id, sender, expected in (
            (61, Mock(return_value=SMSDeliveryOutcome.FAILED), InvitationSMSStatus.FAILED),
            (62, Mock(side_effect=TimeoutError("lost response")), InvitationSMSStatus.AMBIGUOUS),
        ):
            delivery = InvitationSMSDelivery(
                invitation_id=invitation_id,
                status=InvitationSMSStatus.PENDING.value,
                attempt_count=0,
            )
            db = SimpleNamespace(
                execute=AsyncMock(
                    side_effect=[
                        _ScalarResult(one=delivery),
                        _ScalarResult(one=delivery),
                    ]
                ),
                commit=AsyncMock(),
            )
            with patch(
                "core.services.invitation_sms_delivery_service.current_server",
                return_value="iran",
            ):
                status = await deliver_invitation_sms_once(
                    db,
                    invitation_id=invitation_id,
                    newly_created=True,
                    sender=sender,
                )
            self.assertEqual(status, expected)
            self.assertEqual(delivery.status, expected.value)
            self.assertEqual(delivery.attempt_count, 1)
            self.assertEqual(db.commit.await_count, 2)


if __name__ == "__main__":
    unittest.main()
