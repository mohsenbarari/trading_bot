from __future__ import annotations

import asyncio
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from core.config import settings
from fastapi import HTTPException
from api.routers import invitations as invitations_router
from core.invitation_creation_contracts import (
    InternalInvitationCreateRequest,
    InvitationRequesterIdentity,
    build_standard_invitation_idempotency_key,
)
from core.registration_contracts import (
    InvitationSMSStatus,
    InvitationDerivedState,
    OTPDeliveryStatus,
    TelegramRegistrationCommand,
    TelegramRegistrationOutcome,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.canonical_invitation_creation_service import (
    CanonicalInvitationCreationError,
    create_or_reuse_canonical_invitation,
    generate_invitation_token,
)
from core.services.invitation_identity_reservation_service import (
    InvitationIdentityReservationConflict,
    NormalizedInvitationIdentity,
    invitation_creator_lock_key,
    normalize_invitation_identity,
    prune_terminal_identity_reservations,
    release_invitation_identities_for_tokens,
    reserve_invitation_identity,
)
from core.services.invitation_sms_delivery_service import (
    deliver_invitation_sms_once,
    load_invitation_sms_status_map,
    prepare_invitation_sms_delivery,
)
from core.services.otp_delivery_state_service import (
    OTPDeliveryClaim,
    build_otp_delivery_state,
    cancel_otp_delivery,
    load_otp_delivery_state,
    record_sms_delivery_result,
    select_due_otp_requests,
)
from core.services import otp_delivery_state_service
from core import otp_sms_fallback_worker
from core.services import (
    accountant_relation_service,
    authoritative_telegram_account_link_service as account_link_service,
    customer_relation_service,
    telegram_registration_intent_service as intent_service,
    user_deletion_service,
)
from core.services.authoritative_registration_service import AuthoritativeRegistrationError
from core.services.registration_command_receipt_service import RegistrationCommandReplayConflict
from core.utils import utc_now
from core.services.otp_sms_delivery_service import OTPSMSAttemptResult
from core.sms import SMSDeliveryOutcome
from core.telegram_account_link_contracts import build_telegram_account_link_command
from models.invitation import InvitationKind
from models.accountant_relation import AccountantRelationStatus
from models.customer_relation import CustomerRelationStatus, CustomerTier
from models.telegram_registration_intent import TelegramRegistrationIntentStatus
from models.telegram_link_token import TelegramLinkTokenStatus
from models.user import UserRole


class _Scalars:
    def __init__(self, values=()):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _Result:
    def __init__(self, *, one=None, values=()):
        self._one = one
        self._values = values

    def scalar_one_or_none(self):
        return self._one

    def scalar_one(self):
        if self._one is None:
            raise AssertionError("expected one result")
        return self._one

    def scalars(self):
        return _Scalars(self._values)


class _DB:
    def __init__(self, results=()):
        self.results = list(results)
        self.execute = AsyncMock(side_effect=self._execute)
        self.flush = AsyncMock(side_effect=self._flush)
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.refresh = AsyncMock()
        self.added = []

    async def _execute(self, *_args, **_kwargs):
        return self.results.pop(0) if self.results else _Result()

    async def _flush(self):
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = 901

    def add(self, value):
        self.added.append(value)


class Stage9CanonicalInvitationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_token_and_identity_policy_rejections_are_explicit(self):
        with self.assertRaisesRegex(CanonicalInvitationCreationError, "invitation_kind_not_creatable"):
            generate_invitation_token("unknown")
        with self.assertRaisesRegex(ValueError, "نام کاربری نامعتبر"):
            normalize_invitation_identity(mobile_number="09121112233", account_name="  ")
        with self.assertRaisesRegex(ValueError, "positive"):
            invitation_creator_lock_key(0)

    async def test_canonical_creation_rejects_invalid_inputs_and_existing_identity(self):
        cases = (
            ({"creator_user_id": 0}, "invalid_creator"),
            ({"role": "unknown"}, "invalid_invitation_policy"),
            ({"kind": InvitationKind.LEGACY_UNKNOWN}, "invitation_kind_not_creatable"),
            ({"account_name": ""}, "invalid_identity"),
        )
        defaults = {
            "creator_user_id": 7,
            "account_name": "stage9-user",
            "mobile_number": "09121112233",
            "role": UserRole.STANDARD,
            "kind": InvitationKind.STANDARD,
        }
        with override_current_server(SERVER_IRAN):
            for overrides, error in cases:
                with self.subTest(error=error), self.assertRaisesRegex(
                    CanonicalInvitationCreationError, error
                ):
                    await create_or_reuse_canonical_invitation(
                        _DB(), **{**defaults, **overrides}
                    )

            with patch(
                "core.services.canonical_invitation_creation_service.acquire_invitation_creation_locks",
                new=AsyncMock(),
            ), patch(
                "core.services.canonical_invitation_creation_service.prune_terminal_identity_reservations",
                new=AsyncMock(),
            ):
                with self.assertRaisesRegex(CanonicalInvitationCreationError, "user_identity_exists"):
                    await create_or_reuse_canonical_invitation(
                        _DB([_Result(one=1)]), **defaults
                    )

    async def test_canonical_creation_rejects_orphan_reservation(self):
        reservation = SimpleNamespace(invitation_id=91)
        with override_current_server(SERVER_IRAN), patch(
            "core.services.canonical_invitation_creation_service.acquire_invitation_creation_locks",
            new=AsyncMock(),
        ), patch(
            "core.services.canonical_invitation_creation_service.prune_terminal_identity_reservations",
            new=AsyncMock(),
        ), patch(
            "core.services.canonical_invitation_creation_service.find_identity_reservation",
            new=AsyncMock(return_value=reservation),
        ), self.assertRaisesRegex(CanonicalInvitationCreationError, "reservation_integrity_error"):
            await create_or_reuse_canonical_invitation(
                _DB([_Result(one=None), _Result(one=None)]),
                creator_user_id=7,
                account_name="stage9-user",
                mobile_number="09121112233",
                role=UserRole.STANDARD,
                kind=InvitationKind.STANDARD,
            )


class Stage9IdentityReservationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_reservations_are_pruned(self):
        identity = NormalizedInvitationIdentity("09121112233", "stage9-user")
        reservation = SimpleNamespace(id=77)
        invitation = SimpleNamespace(
            is_used=True,
            registered_user_id=5,
            completed_at=utc_now(),
            completed_via="web",
            revoked_at=None,
            expires_at=utc_now() + timedelta(days=1),
        )
        rows = SimpleNamespace(all=Mock(return_value=[(reservation, invitation)]))
        db = _DB([rows, _Result()])
        await prune_terminal_identity_reservations(db, identity)
        self.assertEqual(db.execute.await_count, 2)

    async def test_reservation_requires_flushed_invitation_and_classifies_conflicts(self):
        identity = NormalizedInvitationIdentity("09121112233", "stage9-user")
        with self.assertRaisesRegex(ValueError, "flushed"):
            await reserve_invitation_identity(
                _DB(), invitation=SimpleNamespace(id=None), identity=identity
            )

        conflicts = (
            (SimpleNamespace(invitation_id=2, normalized_mobile=identity.mobile_number, normalized_account_name=identity.account_name), "identity_reserved"),
            (SimpleNamespace(invitation_id=2, normalized_mobile=identity.mobile_number, normalized_account_name="other"), "mobile_reserved"),
            (SimpleNamespace(invitation_id=2, normalized_mobile="09129999999", normalized_account_name=identity.account_name), "account_name_reserved"),
        )
        for existing, code in conflicts:
            with self.subTest(code=code), patch(
                "core.services.invitation_identity_reservation_service.find_identity_reservation",
                new=AsyncMock(return_value=existing),
            ), self.assertRaisesRegex(InvitationIdentityReservationConflict, code):
                await reserve_invitation_identity(
                    _DB(), invitation=SimpleNamespace(id=1), identity=identity
                )

    async def test_release_tokens_is_noop_for_empty_and_executes_for_normalized_tokens(self):
        db = _DB()
        await release_invitation_identities_for_tokens(
            db, invitation_tokens=["", "  "]
        )
        db.execute.assert_not_awaited()
        await release_invitation_identities_for_tokens(
            db, invitation_tokens=[" INV-a ", "INV-a", "INV-b"]
        )
        db.execute.assert_awaited_once()


class Stage9InvitationSMSBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_map_handles_empty_and_malformed_status(self):
        self.assertEqual(await load_invitation_sms_status_map(_DB(), []), {})
        delivery = SimpleNamespace(invitation_id=5, status="future-status")
        result = await load_invitation_sms_status_map(
            _DB([_Result(values=[delivery])]), [5, 5]
        )
        self.assertEqual(result, {5: InvitationSMSStatus.AMBIGUOUS})

    async def test_prepare_enforces_owner_and_flushes_new_invitation(self):
        with override_current_server(SERVER_FOREIGN), self.assertRaisesRegex(
            RuntimeError, "requires_iran"
        ):
            await prepare_invitation_sms_delivery(
                _DB(), invitation=SimpleNamespace(id=1), enabled=False, newly_created=True
            )

        invitation = SimpleNamespace(id=None)
        db = _DB([_Result(one=None)])
        async def flush_invitation():
            invitation.id = 901

        db.flush.side_effect = flush_invitation
        with override_current_server(SERVER_IRAN):
            delivery = await prepare_invitation_sms_delivery(
                db, invitation=invitation, enabled=False, newly_created=True
            )
        self.assertEqual(invitation.id, 901)
        self.assertEqual(delivery.status, InvitationSMSStatus.DISABLED.value)

    async def test_delivery_enforces_owner_and_requires_row(self):
        with override_current_server(SERVER_FOREIGN), self.assertRaisesRegex(
            RuntimeError, "requires_iran"
        ):
            await deliver_invitation_sms_once(
                _DB(), invitation_id=1, newly_created=True, sender=Mock()
            )
        with override_current_server(SERVER_IRAN), self.assertRaisesRegex(
            RuntimeError, "missing"
        ):
            await deliver_invitation_sms_once(
                _DB([_Result(one=None)]), invitation_id=1, newly_created=True, sender=Mock()
            )

    async def test_delivery_replay_generation_and_terminal_races_fail_closed(self):
        now = utc_now()
        live_claim = SimpleNamespace(
            invitation_id=1,
            status=InvitationSMSStatus.PENDING.value,
            attempt_count=1,
            claimed_at=now.replace(tzinfo=None),
            completed_at=None,
        )
        with override_current_server(SERVER_IRAN):
            result = await deliver_invitation_sms_once(
                _DB([_Result(one=live_claim)]),
                invitation_id=1,
                newly_created=False,
                sender=Mock(),
            )
        self.assertEqual(result, InvitationSMSStatus.PENDING)

        for final_row, expected in (
            (
                SimpleNamespace(
                    status=InvitationSMSStatus.AMBIGUOUS.value,
                    attempt_count=2,
                    claimed_at=now,
                    completed_at=now,
                ),
                InvitationSMSStatus.AMBIGUOUS,
            ),
            (
                SimpleNamespace(
                    status=InvitationSMSStatus.ACCEPTED.value,
                    attempt_count=1,
                    claimed_at=now,
                    completed_at=now,
                ),
                InvitationSMSStatus.ACCEPTED,
            ),
            (
                SimpleNamespace(
                    status=InvitationSMSStatus.AMBIGUOUS.value,
                    attempt_count=1,
                    claimed_at=now,
                    completed_at=now,
                ),
                InvitationSMSStatus.AMBIGUOUS,
            ),
        ):
            initial = SimpleNamespace(
                status=InvitationSMSStatus.PENDING.value,
                attempt_count=0,
                claimed_at=None,
                completed_at=None,
            )
            db = _DB([_Result(one=initial), _Result(one=final_row)])
            with self.subTest(expected=expected), override_current_server(SERVER_IRAN):
                result = await deliver_invitation_sms_once(
                    db,
                    invitation_id=2,
                    newly_created=True,
                    sender=Mock(return_value=False),
                )
            self.assertEqual(result, expected)


class Stage9OTPStateBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_secret = settings.otp_delivery_state_secret
        settings.otp_delivery_state_secret = "stage9-boundary-secret-0123456789abcdef"

    def tearDown(self):
        settings.otp_delivery_state_secret = self.original_secret

    def test_secret_and_naive_time_fail_safe_boundaries(self):
        settings.otp_delivery_state_secret = "short"
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            otp_delivery_state_service._state_secret()
        naive = datetime(2026, 7, 12, 10, 30)
        self.assertEqual(
            otp_delivery_state_service._utc(naive).tzinfo,
            timezone.utc,
        )

    async def test_load_requires_selector_and_handles_missing_state(self):
        redis = SimpleNamespace(get=AsyncMock(return_value=None), hgetall=AsyncMock(return_value={}))
        with self.assertRaisesRegex(ValueError, "required"):
            await load_otp_delivery_state(redis)
        self.assertIsNone(await load_otp_delivery_state(redis, mobile="09121112233"))
        self.assertIsNone(await load_otp_delivery_state(redis, request_id=uuid4()))

    async def test_record_sms_rejects_non_terminal_outcome(self):
        state = build_otp_delivery_state(mobile="09121112233", ttl_seconds=120)
        claim = OTPDeliveryClaim(
            claim_id=uuid4(),
            request_id=state.otp_request_id,
            mobile_number="09121112233",
            otp_code="12345",
            lease_until=utc_now() + timedelta(seconds=30),
        )
        with self.assertRaisesRegex(ValueError, "terminal"):
            await record_sms_delivery_result(
                SimpleNamespace(eval=AsyncMock()),
                claim=claim,
                outcome=OTPDeliveryStatus.PENDING,
            )

    async def test_due_selection_isolates_malformed_missing_and_invalid_contract(self):
        valid_id = uuid4()
        missing_id = uuid4()
        invalid_id = uuid4()
        redis = SimpleNamespace(
            zrangebyscore=AsyncMock(
                side_effect=[[b"not-a-uuid", str(missing_id), str(invalid_id), str(valid_id)], []]
            ),
            zrem=AsyncMock(),
            hget=AsyncMock(return_value="pending"),
        )
        valid_state = build_otp_delivery_state(mobile="09121112233", ttl_seconds=120).model_copy(
            update={"otp_request_id": valid_id}
        )
        with patch(
            "core.services.otp_delivery_state_service.load_otp_delivery_state",
            new=AsyncMock(side_effect=[None, ValueError("invalid"), valid_state]),
        ), patch(
            "core.services.otp_delivery_state_service.isolate_invalid_otp_fallback_state",
            new=AsyncMock(),
        ) as isolate:
            selection = await select_due_otp_requests(
                redis, now=datetime.now(timezone.utc), limit=1
            )
        self.assertEqual(selection.request_ids, (valid_id,))
        self.assertEqual(selection.isolated_counts["malformed_request_id"], 1)
        self.assertEqual(selection.isolated_counts["missing_or_terminal"], 1)
        self.assertEqual(selection.isolated_counts["invalid_contract"], 1)
        isolate.assert_awaited_once()

    async def test_cancel_without_and_with_state(self):
        redis = SimpleNamespace(delete=AsyncMock(), hset=AsyncMock(), zrem=AsyncMock())
        with patch(
            "core.services.otp_delivery_state_service.load_otp_delivery_state",
            new=AsyncMock(return_value=None),
        ):
            await cancel_otp_delivery(redis, mobile="09121112233")
        redis.hset.assert_not_awaited()

        state = build_otp_delivery_state(mobile="09121112233", ttl_seconds=120)
        with patch(
            "core.services.otp_delivery_state_service.load_otp_delivery_state",
            new=AsyncMock(return_value=state),
        ):
            await cancel_otp_delivery(redis, mobile="09121112233")
        redis.hset.assert_awaited_once()
        redis.zrem.assert_awaited_once()


class Stage9RelationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_relation_expiry_comparison_normalizes_aware_datetimes(self):
        aware = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        naive = aware.replace(tzinfo=None)
        self.assertTrue(accountant_relation_service._same_utc_moment(aware, naive))

    def test_relation_activation_and_error_mapping_boundaries(self):
        customer = SimpleNamespace(
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
            customer_user_id=None,
        )
        with self.assertRaisesRegex(ValueError, "not_pending"):
            customer_relation_service.activate_customer_relation_for_registration(
                customer, user_id=1, activated_at=utc_now()
            )
        customer.status = CustomerRelationStatus.PENDING
        customer.customer_user_id = 2
        with self.assertRaisesRegex(ValueError, "already_bound"):
            customer_relation_service.activate_customer_relation_for_registration(
                customer, user_id=1, activated_at=utc_now()
            )

        accountant = SimpleNamespace(
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
            accountant_user_id=None,
        )
        with self.assertRaisesRegex(ValueError, "not_pending"):
            accountant_relation_service.activate_accountant_relation_for_registration(
                accountant, user_id=1, activated_at=utc_now()
            )
        accountant.status = AccountantRelationStatus.PENDING
        accountant.accountant_user_id = 2
        with self.assertRaisesRegex(ValueError, "already_bound"):
            accountant_relation_service.activate_accountant_relation_for_registration(
                accountant, user_id=1, activated_at=utc_now()
            )

        for module in (customer_relation_service, accountant_relation_service):
            self.assertEqual(
                module._canonical_customer_error(CanonicalInvitationCreationError("iran_authority_required")).status_code
                if module is customer_relation_service
                else module._canonical_accountant_error(CanonicalInvitationCreationError("iran_authority_required")).status_code,
                403,
            )
            self.assertEqual(
                module._canonical_customer_error(CanonicalInvitationCreationError("invalid_identity")).status_code
                if module is customer_relation_service
                else module._canonical_accountant_error(CanonicalInvitationCreationError("invalid_identity")).status_code,
                400,
            )

    async def test_expiry_sweeps_release_by_token_when_invitation_is_missing(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cases = (
            (
                customer_relation_service,
                customer_relation_service.sweep_expired_pending_customer_relations,
                CustomerRelationStatus.EXPIRED,
            ),
            (
                accountant_relation_service,
                accountant_relation_service.sweep_expired_pending_accountant_relations,
                AccountantRelationStatus.EXPIRED,
            ),
        )
        for module, sweep, expected_status in cases:
            candidate = SimpleNamespace(id=8, invitation_token="INV-missing")
            relation = SimpleNamespace(
                id=8,
                invitation_token="INV-missing",
                status=None,
                deleted_at=None,
            )
            db = _DB([_Result(values=[candidate]), _Result(one=relation), _Result()])
            with self.subTest(module=module.__name__), patch.object(
                module, "current_server", return_value=SERVER_IRAN
            ), patch.object(
                module, "_utcnow_naive", return_value=now
            ), patch.object(
                module,
                "lock_invitation_for_transition",
                new=AsyncMock(return_value=None),
            ), patch.object(
                module,
                "release_invitation_identities_for_tokens",
                new=AsyncMock(),
            ) as release_tokens:
                expired = await sweep(db)
            self.assertEqual(expired, [relation])
            self.assertEqual(relation.status, expected_status)
            release_tokens.assert_awaited_once_with(
                db, invitation_tokens=["INV-missing"]
            )

    async def test_pending_lookup_commits_only_after_expiry_sweep_mutates(self):
        cases = (
            (
                accountant_relation_service,
                accountant_relation_service.get_pending_accountant_relation_by_invitation_token,
                "sweep_expired_pending_accountant_relations",
            ),
            (
                customer_relation_service,
                customer_relation_service.get_pending_customer_relation_by_invitation_token,
                "sweep_expired_pending_customer_relations",
            ),
        )
        for module, lookup, sweep_name in cases:
            db = _DB([_Result(one=None)])
            with self.subTest(module=module.__name__), patch.object(
                module, "current_server", return_value=SERVER_IRAN
            ), patch.object(
                module, sweep_name, new=AsyncMock(return_value=[SimpleNamespace()])
            ):
                self.assertIsNone(await lookup(db, "INV-expired"))
            db.commit.assert_awaited_once()

    async def test_cancel_rechecks_locked_relation_and_invitation_state(self):
        cases = (
            (
                customer_relation_service,
                customer_relation_service.cancel_pending_customer_relation,
                CustomerRelationStatus.PENDING,
            ),
            (
                accountant_relation_service,
                accountant_relation_service.cancel_pending_accountant_relation,
                AccountantRelationStatus.PENDING,
            ),
        )
        for module, cancel, pending_status in cases:
            with self.subTest(module=module.__name__, case="missing_after_lock"), patch.object(
                module,
                "lock_invitation_for_transition",
                new=AsyncMock(return_value=None),
            ):
                with self.assertRaisesRegex(Exception, "یافت نشد"):
                    await cancel(
                        _DB([_Result(one="INV-a"), _Result(one=None)]),
                        owner_user_id=1,
                        relation_id=2,
                    )

            relation = SimpleNamespace(
                deleted_at=None,
                status=pending_status,
            )
            invitation = SimpleNamespace(is_used=True, revoked_at=None)
            with self.subTest(module=module.__name__, case="used_invitation"), patch.object(
                module,
                "lock_invitation_for_transition",
                new=AsyncMock(return_value=invitation),
            ):
                with self.assertRaisesRegex(Exception, "pending"):
                    await cancel(
                        _DB([_Result(one="INV-a"), _Result(one=relation)]),
                        owner_user_id=1,
                        relation_id=2,
                    )

    async def test_exact_relation_retries_reuse_canonical_invitation(self):
        owner = SimpleNamespace(id=7)
        expires_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

        accountant_invitation = SimpleNamespace(
            token="ACCT-stage9",
            expires_at=expires_at,
        )
        accountant_relation = SimpleNamespace(
            owner_user_id=7,
            created_by_user_id=7,
            accountant_user_id=None,
            deleted_at=None,
            status=AccountantRelationStatus.PENDING,
            global_account_name="stage9-accountant",
            mobile_number="09121112233",
            relation_display_name="حسابدار تست",
            duty_description=None,
            expires_at=expires_at.replace(tzinfo=None),
        )
        accountant_db = _DB([_Result(one=accountant_relation)])
        with patch.object(
            accountant_relation_service,
            "create_or_reuse_canonical_invitation",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    invitation=accountant_invitation,
                    created=False,
                )
            ),
        ), patch.object(
            accountant_relation_service,
            "prepare_invitation_sms_delivery",
            new=AsyncMock(),
        ):
            result = await accountant_relation_service.create_or_reuse_owner_accountant_relation(
                accountant_db,
                owner_user=owner,
                global_account_name="stage9-accountant",
                relation_display_name="حسابدار تست",
                mobile_number="09121112233",
            )
        self.assertFalse(result.created)

        mismatched = SimpleNamespace(**{**vars(accountant_relation), "relation_display_name": "متفاوت"})
        with patch.object(
            accountant_relation_service,
            "create_or_reuse_canonical_invitation",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    invitation=accountant_invitation,
                    created=False,
                )
            ),
        ), self.assertRaisesRegex(Exception, "متفاوت"):
            await accountant_relation_service.create_or_reuse_owner_accountant_relation(
                _DB([_Result(one=mismatched)]),
                owner_user=owner,
                global_account_name="stage9-accountant",
                relation_display_name="حسابدار تست",
                mobile_number="09121112233",
            )

        customer_invitation = SimpleNamespace(
            token="CUST-stage9",
            expires_at=expires_at,
        )
        customer_relation = SimpleNamespace(
            owner_user_id=7,
            created_by_user_id=7,
            customer_user_id=None,
            deleted_at=None,
            status=CustomerRelationStatus.PENDING,
            management_name="مشتری تست",
            customer_tier=CustomerTier.TIER_1,
            commission_rate=None,
            min_trade_quantity=None,
            max_trade_quantity=None,
            max_daily_trades=None,
            max_daily_commodity_volume=None,
            expires_at=expires_at,
        )
        customer_db = _DB([_Result(one=customer_relation)])
        with patch.object(
            customer_relation_service,
            "create_or_reuse_canonical_invitation",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    invitation=customer_invitation,
                    created=False,
                )
            ),
        ), patch.object(
            customer_relation_service,
            "prepare_invitation_sms_delivery",
            new=AsyncMock(),
        ):
            result = await customer_relation_service.create_or_reuse_owner_customer_relation(
                customer_db,
                owner_user=owner,
                account_name="stage9-customer",
                management_name="مشتری تست",
                mobile_number="09121112234",
                customer_tier=CustomerTier.TIER_1,
            )
        self.assertFalse(result.created)

    async def test_pending_lookup_without_expiry_and_cancel_without_invitation_id(self):
        for module, lookup, sweep_name in (
            (
                accountant_relation_service,
                accountant_relation_service.get_pending_accountant_relation_by_invitation_token,
                "sweep_expired_pending_accountant_relations",
            ),
            (
                customer_relation_service,
                customer_relation_service.get_pending_customer_relation_by_invitation_token,
                "sweep_expired_pending_customer_relations",
            ),
        ):
            db = _DB([_Result(one=None)])
            with patch.object(module, "current_server", return_value=SERVER_IRAN), patch.object(
                module, sweep_name, new=AsyncMock(return_value=[])
            ):
                self.assertIsNone(await lookup(db, "INV-none"))
            db.commit.assert_not_awaited()

        relation = SimpleNamespace(
            deleted_at=None,
            status=AccountantRelationStatus.PENDING,
        )
        invitation = SimpleNamespace(id=None, is_used=False, revoked_at=None)
        db = _DB([_Result(one="ACCT-a"), _Result(one=relation)])
        with patch.object(
            accountant_relation_service,
            "lock_invitation_for_transition",
            new=AsyncMock(return_value=invitation),
        ), patch.object(
            accountant_relation_service, "soft_revoke_invitation"
        ):
            await accountant_relation_service.cancel_pending_accountant_relation(
                db, owner_user_id=1, relation_id=2
            )
        db.commit.assert_awaited_once()

        customer_relation = SimpleNamespace(
            deleted_at=None,
            status=CustomerRelationStatus.PENDING,
        )
        customer_invitation = SimpleNamespace(id=None, is_used=False, revoked_at=None)
        customer_db = _DB([_Result(one="CUST-a"), _Result(one=customer_relation)])
        with patch.object(
            customer_relation_service,
            "lock_invitation_for_transition",
            new=AsyncMock(return_value=customer_invitation),
        ), patch.object(
            customer_relation_service, "soft_revoke_invitation"
        ):
            await customer_relation_service.cancel_pending_customer_relation(
                customer_db, owner_user_id=1, relation_id=2
            )
        customer_db.commit.assert_awaited_once()

    async def test_accountant_expiry_sweep_skips_candidate_removed_after_lock(self):
        candidate = SimpleNamespace(id=1, invitation_token="ACCT-a")
        db = _DB([_Result(values=[candidate]), _Result(one=None)])
        with patch.object(
            accountant_relation_service, "current_server", return_value=SERVER_IRAN
        ), patch.object(
            accountant_relation_service,
            "lock_invitation_for_transition",
            new=AsyncMock(return_value=None),
        ):
            self.assertEqual(
                await accountant_relation_service.sweep_expired_pending_accountant_relations(db),
                [],
            )


class Stage9OTPFallbackWorkerBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_secret = settings.otp_delivery_state_secret
        settings.otp_delivery_state_secret = "stage9-worker-secret-0123456789abcdef"

    def tearDown(self):
        settings.otp_delivery_state_secret = self.original_secret

    def _enabled_patches(self):
        return (
            patch.object(otp_sms_fallback_worker, "assert_background_job_authority"),
            patch.object(
                otp_sms_fallback_worker.settings,
                "telegram_login_otp_enabled",
                True,
            ),
            patch.object(
                otp_sms_fallback_worker.settings,
                "otp_sms_auto_fallback_enabled",
                True,
            ),
            patch.object(otp_sms_fallback_worker, "get_redis_client", return_value=object()),
        )

    async def test_cycle_rejects_disabled_and_reports_preisolated_queue_items(self):
        with patch.object(
            otp_sms_fallback_worker, "assert_background_job_authority"
        ), patch.object(
            otp_sms_fallback_worker.settings, "telegram_login_otp_enabled", False
        ), self.assertRaisesRegex(RuntimeError, "disabled"):
            await otp_sms_fallback_worker.run_otp_sms_fallback_cycle()

        patches = self._enabled_patches()
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            otp_sms_fallback_worker,
            "select_due_otp_requests",
            new=AsyncMock(
                return_value=otp_delivery_state_service.OTPDueSelection(
                    request_ids=(),
                    isolated_counts={"invalid_contract": 2},
                )
            ),
        ), patch.object(
            otp_sms_fallback_worker, "record_otp_event"
        ) as metric, patch.object(
            otp_sms_fallback_worker, "audit_log"
        ) as audit, patch.object(
            otp_sms_fallback_worker.logger, "warning"
        ) as warning:
            report = await otp_sms_fallback_worker.run_otp_sms_fallback_cycle()
        self.assertEqual(report.due_count, 0)
        metric.assert_called_once_with(
            event="fallback_state_isolated", outcome="invalid_contract", count=2
        )
        audit.assert_called_once()
        warning.assert_called_once()

    async def test_cycle_classifies_missing_not_claimed_and_unrecorded_delivery(self):
        missing_id, unclaimed_id, delivered_id = uuid4(), uuid4(), uuid4()
        now = utc_now()
        unclaimed_state = build_otp_delivery_state(
            mobile="09121112233", ttl_seconds=120
        ).model_copy(update={"otp_request_id": unclaimed_id})
        delivered_state = build_otp_delivery_state(
            mobile="09121112234", ttl_seconds=120
        ).model_copy(
            update={
                "otp_request_id": delivered_id,
                "sms_fallback_at": now - timedelta(seconds=2),
            }
        )
        claim = OTPDeliveryClaim(
            claim_id=uuid4(),
            request_id=delivered_id,
            mobile_number="09121112234",
            otp_code="12345",
            lease_until=now + timedelta(seconds=30),
        )
        patches = self._enabled_patches()
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            otp_sms_fallback_worker,
            "select_due_otp_requests",
            new=AsyncMock(
                return_value=otp_delivery_state_service.OTPDueSelection(
                    request_ids=(missing_id, unclaimed_id, delivered_id),
                    isolated_counts={},
                )
            ),
        ), patch.object(
            otp_sms_fallback_worker,
            "load_otp_delivery_state",
            new=AsyncMock(side_effect=[None, unclaimed_state, delivered_state]),
        ), patch.object(
            otp_sms_fallback_worker,
            "claim_sms_delivery",
            new=AsyncMock(side_effect=[None, claim]),
        ), patch.object(
            otp_sms_fallback_worker,
            "execute_claimed_otp_sms_delivery",
            new=AsyncMock(
                return_value=OTPSMSAttemptResult(
                    outcome=SMSDeliveryOutcome.AMBIGUOUS,
                    provider_attempted=True,
                    result_recorded=False,
                )
            ),
        ), patch.object(
            otp_sms_fallback_worker, "observe_otp_fallback_delay"
        ) as observe:
            report = await otp_sms_fallback_worker.run_otp_sms_fallback_cycle()
        self.assertEqual(
            report.outcome_counts,
            {"missing": 1, "not_claimed": 1, "ambiguous_unrecorded": 1},
        )
        observe.assert_called_once()

    async def test_cycle_isolation_failure_is_contained_and_cancellation_propagates(self):
        request_id = uuid4()
        patches = self._enabled_patches()
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            otp_sms_fallback_worker,
            "select_due_otp_requests",
            new=AsyncMock(
                return_value=otp_delivery_state_service.OTPDueSelection(
                    request_ids=(request_id,), isolated_counts={}
                )
            ),
        ), patch.object(
            otp_sms_fallback_worker,
            "load_otp_delivery_state",
            new=AsyncMock(side_effect=ValueError("bad state")),
        ), patch.object(
            otp_sms_fallback_worker,
            "isolate_invalid_otp_fallback_state",
            new=AsyncMock(side_effect=RuntimeError("redis failed")),
        ), patch.object(otp_sms_fallback_worker.logger, "exception") as log:
            report = await otp_sms_fallback_worker.run_otp_sms_fallback_cycle()
        self.assertEqual(report.outcome_counts, {"worker_exception": 1})
        log.assert_called_once()

        state = build_otp_delivery_state(mobile="09121112233", ttl_seconds=120)
        patches = self._enabled_patches()
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            otp_sms_fallback_worker,
            "select_due_otp_requests",
            new=AsyncMock(
                return_value=otp_delivery_state_service.OTPDueSelection(
                    request_ids=(state.otp_request_id,), isolated_counts={}
                )
            ),
        ), patch.object(
            otp_sms_fallback_worker,
            "load_otp_delivery_state",
            new=AsyncMock(return_value=state),
        ), patch.object(
            otp_sms_fallback_worker,
            "claim_sms_delivery",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await otp_sms_fallback_worker.run_otp_sms_fallback_cycle()

    async def test_loop_records_success_error_and_health_record_failure(self):
        queue = SimpleNamespace(
            pending_count=3,
            oldest_pending_age_seconds=4.0,
            lag_seconds=5.0,
        )
        report = otp_sms_fallback_worker.OTPFallbackCycleReport(
            due_count=1, outcome_counts={"accepted": 1}
        )
        with patch.object(
            otp_sms_fallback_worker, "assert_background_job_authority"
        ), patch.object(
            otp_sms_fallback_worker,
            "job_context",
            return_value=nullcontext("run-1"),
        ), patch.object(
            otp_sms_fallback_worker,
            "run_otp_sms_fallback_cycle",
            new=AsyncMock(return_value=report),
        ), patch.object(
            otp_sms_fallback_worker, "get_redis_client", return_value=object()
        ), patch.object(
            otp_sms_fallback_worker,
            "summarize_otp_fallback_queue",
            new=AsyncMock(return_value=queue),
        ), patch.object(
            otp_sms_fallback_worker,
            "record_registration_job_snapshot",
            new=AsyncMock(),
        ) as snapshot, patch.object(
            otp_sms_fallback_worker.asyncio,
            "sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await otp_sms_fallback_worker.otp_sms_fallback_loop()
        snapshot.assert_awaited_once()

        with patch.object(
            otp_sms_fallback_worker, "assert_background_job_authority"
        ), patch.object(
            otp_sms_fallback_worker,
            "job_context",
            return_value=nullcontext("run-2"),
        ), patch.object(
            otp_sms_fallback_worker,
            "run_otp_sms_fallback_cycle",
            new=AsyncMock(side_effect=ValueError("cycle failed")),
        ), patch.object(
            otp_sms_fallback_worker, "get_redis_client", return_value=object()
        ), patch.object(
            otp_sms_fallback_worker,
            "load_registration_job_snapshot",
            new=AsyncMock(return_value={"pending_count": 2}),
        ), patch.object(
            otp_sms_fallback_worker,
            "record_registration_job_snapshot",
            new=AsyncMock(side_effect=RuntimeError("health failed")),
        ), patch.object(
            otp_sms_fallback_worker.logger, "warning"
        ) as warning, patch.object(
            otp_sms_fallback_worker._loop_errors, "log"
        ) as loop_log, patch.object(
            otp_sms_fallback_worker.asyncio,
            "sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await otp_sms_fallback_worker.otp_sms_fallback_loop()
        warning.assert_called_once()
        loop_log.assert_called_once()

    async def test_loop_quiet_cycle_and_cancellation_paths(self):
        queue = SimpleNamespace(
            pending_count=0,
            oldest_pending_age_seconds=0.0,
            lag_seconds=0.0,
        )
        with patch.object(
            otp_sms_fallback_worker, "assert_background_job_authority"
        ), patch.object(
            otp_sms_fallback_worker,
            "job_context",
            return_value=nullcontext("run-quiet"),
        ), patch.object(
            otp_sms_fallback_worker,
            "run_otp_sms_fallback_cycle",
            new=AsyncMock(
                return_value=otp_sms_fallback_worker.OTPFallbackCycleReport(
                    due_count=0, outcome_counts={}
                )
            ),
        ), patch.object(
            otp_sms_fallback_worker, "get_redis_client", return_value=object()
        ), patch.object(
            otp_sms_fallback_worker,
            "summarize_otp_fallback_queue",
            new=AsyncMock(return_value=queue),
        ), patch.object(
            otp_sms_fallback_worker,
            "record_registration_job_snapshot",
            new=AsyncMock(),
        ), patch.object(
            otp_sms_fallback_worker.asyncio,
            "sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await otp_sms_fallback_worker.otp_sms_fallback_loop()

        with patch.object(
            otp_sms_fallback_worker, "assert_background_job_authority"
        ), patch.object(
            otp_sms_fallback_worker,
            "job_context",
            return_value=nullcontext("run-cancelled"),
        ), patch.object(
            otp_sms_fallback_worker,
            "run_otp_sms_fallback_cycle",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await otp_sms_fallback_worker.otp_sms_fallback_loop()


def _account_link_command(*, mode="link_token", telegram_id=771100, mobile="09121112233"):
    return build_telegram_account_link_command(
        mode=mode,
        link_token=("t" * 40 if mode == "link_token" else None),
        mobile_number=mobile,
        telegram_id=telegram_id,
        telegram_username="stage9_user",
        telegram_full_name="Stage Nine",
        address="Stage 9 complete address",
        contact_verified_at=(utc_now() if mode == "link_token" else None),
    )


class Stage9AuthoritativeAccountLinkBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_address_and_receipt_boundaries(self):
        for address, expected in (
            (None, True),
            ("", True),
            ("System Default", True),
            ("complete address", False),
        ):
            self.assertEqual(
                account_link_service._address_is_incomplete(
                    SimpleNamespace(address=address)
                ),
                expected,
            )
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            account_link_service._receipt_result(
                SimpleNamespace(completed_at=None, outcome_code=None)
            )

    async def test_token_candidate_rejects_missing_user_and_locked_drift(self):
        command = _account_link_command()
        db = _DB([_Result(one=None)])
        db.get = AsyncMock(return_value=None)
        with self.assertRaises(account_link_service.AuthoritativeTelegramAccountLinkError) as raised:
            await account_link_service._load_token_candidate(db, command)
        self.assertEqual(raised.exception.outcome, TelegramRegistrationOutcome.LINK_TOKEN_NOT_FOUND)

        probe_token = SimpleNamespace(user_id=8)
        db = _DB([_Result(one=probe_token)])
        db.get = AsyncMock(return_value=None)
        with self.assertRaises(account_link_service.AuthoritativeTelegramAccountLinkError) as raised:
            await account_link_service._load_token_candidate(db, command)
        self.assertEqual(
            raised.exception.outcome,
            TelegramRegistrationOutcome.AUTHORITATIVE_USER_MISSING,
        )

        probe_user = SimpleNamespace(
            id=8,
            mobile_number=command.mobile_number,
            account_name="stage9-user",
        )
        db = _DB([_Result(one=probe_token), _Result(one=None)])
        db.get = AsyncMock(return_value=probe_user)
        with patch.object(
            account_link_service,
            "acquire_invitation_transition_locks",
            new=AsyncMock(),
        ), self.assertRaises(account_link_service.AuthoritativeTelegramAccountLinkError) as raised:
            await account_link_service._load_token_candidate(db, command)
        self.assertEqual(
            raised.exception.outcome,
            TelegramRegistrationOutcome.AUTHORITATIVE_USER_MISSING,
        )

        locked_user = SimpleNamespace(**vars(probe_user))
        db = _DB(
            [
                _Result(one=probe_token),
                _Result(one=locked_user),
                _Result(one=SimpleNamespace(user_id=99)),
            ]
        )
        db.get = AsyncMock(return_value=probe_user)
        with patch.object(
            account_link_service,
            "acquire_invitation_transition_locks",
            new=AsyncMock(),
        ), self.assertRaises(account_link_service.AuthoritativeTelegramAccountLinkError) as raised:
            await account_link_service._load_token_candidate(db, command)
        self.assertEqual(raised.exception.outcome, TelegramRegistrationOutcome.LINK_TOKEN_NOT_FOUND)

    async def test_existing_link_lookup_rejects_split_and_post_lock_drift(self):
        command = _account_link_command(mode="existing_linked_user")
        with self.assertRaises(account_link_service.AuthoritativeTelegramAccountLinkError) as raised:
            await account_link_service._load_existing_linked_user(
                _DB([_Result(values=[])]), command
            )
        self.assertEqual(raised.exception.outcome, TelegramRegistrationOutcome.IDENTITY_CONFLICT)

        exact = SimpleNamespace(
            id=8,
            mobile_number=command.mobile_number,
            account_name="stage9-user",
            telegram_id=command.telegram_id,
        )
        db = _DB([_Result(values=[exact]), _Result(one=exact)])
        with patch.object(
            account_link_service,
            "acquire_invitation_transition_locks",
            new=AsyncMock(),
        ):
            self.assertIs(
                await account_link_service._load_existing_linked_user(db, command),
                exact,
            )

        drifted = SimpleNamespace(**{**vars(exact), "telegram_id": command.telegram_id + 1})
        db = _DB([_Result(values=[exact]), _Result(one=drifted)])
        with patch.object(
            account_link_service,
            "acquire_invitation_transition_locks",
            new=AsyncMock(),
        ), self.assertRaises(account_link_service.AuthoritativeTelegramAccountLinkError) as raised:
            await account_link_service._load_existing_linked_user(db, command)
        self.assertEqual(raised.exception.outcome, TelegramRegistrationOutcome.IDENTITY_CONFLICT)

    async def test_token_state_terminal_matrix(self):
        command = _account_link_command()
        user = SimpleNamespace(telegram_id=command.telegram_id)
        future = utc_now() + timedelta(minutes=5)
        cases = (
            (TelegramLinkTokenStatus.REVOKED, future, None, TelegramRegistrationOutcome.LINK_TOKEN_REVOKED),
            (TelegramLinkTokenStatus.PENDING, utc_now() - timedelta(seconds=1), None, TelegramRegistrationOutcome.LINK_TOKEN_EXPIRED),
            (TelegramLinkTokenStatus.USED, future, command.telegram_id + 1, TelegramRegistrationOutcome.LINK_TOKEN_ALREADY_USED),
            ("future", future, None, TelegramRegistrationOutcome.INVALID_COMMAND),
        )
        for status, expires, used_id, expected in cases:
            token = SimpleNamespace(
                status=status,
                expires_at=expires.replace(tzinfo=None),
                used_telegram_id=used_id,
            )
            with self.subTest(expected=expected), self.assertRaises(
                account_link_service.AuthoritativeTelegramAccountLinkError
            ) as raised:
                await account_link_service._validate_token_state(token, user, command)
            self.assertEqual(raised.exception.outcome, expected)
        token = SimpleNamespace(
            status=TelegramLinkTokenStatus.USED,
            expires_at=future,
            used_telegram_id=command.telegram_id,
        )
        self.assertEqual(
            await account_link_service._validate_token_state(token, user, command),
            TelegramRegistrationOutcome.ALREADY_LINKED,
        )
        expired = SimpleNamespace(
            status=TelegramLinkTokenStatus.EXPIRED,
            expires_at=future,
            used_telegram_id=None,
        )
        with self.assertRaises(account_link_service.AuthoritativeTelegramAccountLinkError):
            await account_link_service._validate_token_state(expired, user, command)

    async def test_complete_enforces_owner_replay_and_successful_token_transition(self):
        command = _account_link_command()
        with override_current_server(SERVER_FOREIGN), self.assertRaisesRegex(
            RuntimeError, "requires_iran"
        ):
            await account_link_service.complete_authoritative_telegram_account_link(
                _DB(), command=command, source_server=SERVER_FOREIGN
            )

        completed_receipt = SimpleNamespace(
            completed_at=utc_now(),
            outcome_code=TelegramRegistrationOutcome.LINKED_EXISTING.value,
            authoritative_user_id=8,
        )
        db = _DB()
        with override_current_server(SERVER_IRAN), patch.object(
            account_link_service,
            "prepare_internal_registration_command_receipt",
            new=AsyncMock(return_value=(completed_receipt, True)),
        ):
            result = await account_link_service.complete_authoritative_telegram_account_link(
                db, command=command, source_server=SERVER_FOREIGN
            )
        self.assertTrue(result.replayed)

        receipt = SimpleNamespace()
        token = SimpleNamespace(
            status=TelegramLinkTokenStatus.PENDING,
            used_at=None,
            used_telegram_id=None,
        )
        user = SimpleNamespace(
            id=8,
            mobile_number=command.mobile_number,
            telegram_id=None,
            username=None,
            address="System Default",
            has_bot_access=False,
        )
        db = _DB([_Result(one=None)])
        with override_current_server(SERVER_IRAN), patch.object(
            account_link_service,
            "prepare_internal_registration_command_receipt",
            new=AsyncMock(return_value=(receipt, False)),
        ), patch.object(
            account_link_service,
            "_load_token_candidate",
            new=AsyncMock(return_value=(token, user)),
        ), patch.object(
            account_link_service,
            "_validate_token_state",
            new=AsyncMock(return_value=None),
        ), patch.object(
            account_link_service,
            "validate_current_telegram_eligibility",
            new=AsyncMock(),
        ), patch.object(
            account_link_service,
            "ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ), patch.object(
            account_link_service, "finalize_registration_command_receipt"
        ) as finalize:
            result = await account_link_service.complete_authoritative_telegram_account_link(
                db, command=command, source_server=SERVER_FOREIGN
            )
        self.assertEqual(result.outcome, TelegramRegistrationOutcome.LINKED_EXISTING)
        self.assertEqual(user.telegram_id, command.telegram_id)
        self.assertEqual(user.address, command.address)
        self.assertEqual(token.status, TelegramLinkTokenStatus.USED)
        finalize.assert_called_once()

    async def test_complete_classifies_expected_and_unexpected_failures(self):
        command = _account_link_command()
        receipt = SimpleNamespace()
        base_user = SimpleNamespace(
            id=8,
            mobile_number=command.mobile_number,
            telegram_id=None,
            username=None,
            address="complete address",
            has_bot_access=False,
        )
        token = SimpleNamespace(status=TelegramLinkTokenStatus.PENDING)

        async def run_case(*, user=None, eligibility=None, duplicate=None):
            db = _DB([_Result(one=duplicate)])
            with override_current_server(SERVER_IRAN), patch.object(
                account_link_service,
                "prepare_internal_registration_command_receipt",
                new=AsyncMock(return_value=(receipt, False)),
            ), patch.object(
                account_link_service,
                "_load_token_candidate",
                new=AsyncMock(return_value=(token, user or base_user)),
            ), patch.object(
                account_link_service,
                "_validate_token_state",
                new=AsyncMock(return_value=None),
            ), patch.object(
                account_link_service,
                "validate_current_telegram_eligibility",
                new=AsyncMock(side_effect=eligibility),
            ), patch.object(
                account_link_service, "finalize_registration_command_receipt"
            ):
                return await account_link_service.complete_authoritative_telegram_account_link(
                    db, command=command, source_server=SERVER_FOREIGN
                )

        eligibility = AuthoritativeRegistrationError(
            TelegramRegistrationOutcome.ACCOUNT_DELETED,
            public_detail="کاربر حذف شده است",
        )
        self.assertEqual(
            (await run_case(eligibility=eligibility)).outcome,
            TelegramRegistrationOutcome.ACCOUNT_DELETED,
        )
        wrong_mobile = SimpleNamespace(**{**vars(base_user), "mobile_number": "09129999999"})
        self.assertEqual(
            (await run_case(user=wrong_mobile)).outcome,
            TelegramRegistrationOutcome.CONTACT_MOBILE_MISMATCH,
        )
        self.assertEqual(
            (await run_case(duplicate=SimpleNamespace(id=99))).outcome,
            TelegramRegistrationOutcome.TELEGRAM_ID_ALREADY_USED,
        )
        conflicting = SimpleNamespace(**{**vars(base_user), "telegram_id": command.telegram_id + 2})
        self.assertEqual(
            (await run_case(user=conflicting)).outcome,
            TelegramRegistrationOutcome.TELEGRAM_ACCOUNT_CONFLICT,
        )

        for replay_error, expected in (
            (TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY.value, TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY),
            ("other", TelegramRegistrationOutcome.INVALID_COMMAND),
        ):
            db = _DB()
            with override_current_server(SERVER_IRAN), patch.object(
                account_link_service,
                "prepare_internal_registration_command_receipt",
                new=AsyncMock(side_effect=RegistrationCommandReplayConflict(replay_error)),
            ):
                result = await account_link_service.complete_authoritative_telegram_account_link(
                    db, command=command, source_server=SERVER_FOREIGN
                )
            self.assertEqual(result.outcome, expected)

        db = _DB()
        with override_current_server(SERVER_IRAN), patch.object(
            account_link_service,
            "prepare_internal_registration_command_receipt",
            new=AsyncMock(return_value=(receipt, False)),
        ), patch.object(
            account_link_service,
            "_load_token_candidate",
            new=AsyncMock(side_effect=RuntimeError("unexpected")),
        ), self.assertRaisesRegex(RuntimeError, "unexpected"):
            await account_link_service.complete_authoritative_telegram_account_link(
                db, command=command, source_server=SERVER_FOREIGN
            )
        db.rollback.assert_awaited_once()

    async def test_complete_existing_mode_updates_username_and_receiptless_error_propagates(self):
        command = _account_link_command(mode="existing_linked_user")
        receipt = SimpleNamespace()
        user = SimpleNamespace(
            id=8,
            mobile_number=command.mobile_number,
            account_name="stage9-user",
            telegram_id=command.telegram_id,
            username="old",
            address="complete address",
            has_bot_access=False,
        )
        db = _DB([_Result(one=None)])
        with override_current_server(SERVER_IRAN), patch.object(
            account_link_service,
            "prepare_internal_registration_command_receipt",
            new=AsyncMock(return_value=(receipt, False)),
        ), patch.object(
            account_link_service,
            "_load_existing_linked_user",
            new=AsyncMock(return_value=user),
        ), patch.object(
            account_link_service,
            "validate_current_telegram_eligibility",
            new=AsyncMock(),
        ), patch.object(
            account_link_service,
            "ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ), patch.object(
            account_link_service, "finalize_registration_command_receipt"
        ):
            result = await account_link_service.complete_authoritative_telegram_account_link(
                db, command=command, source_server=SERVER_FOREIGN
            )
        self.assertEqual(result.outcome, TelegramRegistrationOutcome.ALREADY_LINKED)
        self.assertEqual(user.username, command.telegram_username)

        no_username_command = command.model_copy(update={"telegram_username": None})
        db = _DB([_Result(one=None)])
        with override_current_server(SERVER_IRAN), patch.object(
            account_link_service,
            "prepare_internal_registration_command_receipt",
            new=AsyncMock(return_value=(SimpleNamespace(), False)),
        ), patch.object(
            account_link_service,
            "_load_existing_linked_user",
            new=AsyncMock(return_value=user),
        ), patch.object(
            account_link_service,
            "validate_current_telegram_eligibility",
            new=AsyncMock(),
        ), patch.object(
            account_link_service,
            "ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ), patch.object(
            account_link_service, "finalize_registration_command_receipt"
        ):
            await account_link_service.complete_authoritative_telegram_account_link(
                db, command=no_username_command, source_server=SERVER_FOREIGN
            )

        expected = account_link_service._error(
            TelegramRegistrationOutcome.LINK_TOKEN_NOT_FOUND,
            "bad link",
        )
        db = _DB()
        with override_current_server(SERVER_IRAN), patch.object(
            account_link_service,
            "prepare_internal_registration_command_receipt",
            new=AsyncMock(side_effect=expected),
        ), self.assertRaises(account_link_service.AuthoritativeTelegramAccountLinkError):
            await account_link_service.complete_authoritative_telegram_account_link(
                db,
                command=_account_link_command(),
                source_server=SERVER_FOREIGN,
            )
        db.rollback.assert_awaited_once()


def _registration_command(**overrides):
    now = utc_now()
    values = {
        "command_id": uuid4(),
        "idempotency_key": "telegram-registration:stage9-boundary",
        "invitation_token": "INV-stage9-boundary",
        "mobile_number": "09121112233",
        "telegram_id": 771100,
        "telegram_username": "stage9_user",
        "telegram_full_name": "Stage Nine",
        "address": "Stage 9 complete address",
        "contact_verified_at": now - timedelta(minutes=2),
        "local_completed_at": now - timedelta(minutes=1),
        "invitation_expires_at_snapshot": now + timedelta(hours=1),
    }
    values.update(overrides)
    return TelegramRegistrationCommand(**values)


def _intent_from_command(command, **overrides):
    values = {
        "id": command.command_id,
        "idempotency_key": command.idempotency_key,
        "invitation_token": command.invitation_token,
        "normalized_mobile": command.mobile_number,
        "telegram_id": command.telegram_id,
        "telegram_username": command.telegram_username,
        "telegram_full_name": command.telegram_full_name,
        "address": command.address,
        "contact_verified_at": command.contact_verified_at,
        "completed_at": command.local_completed_at,
        "invitation_expires_at_snapshot": command.invitation_expires_at_snapshot,
        "status": TelegramRegistrationIntentStatus.FORWARDING,
        "retry_count": 1,
        "next_retry_at": None,
        "last_error_code": None,
        "authoritative_user_id": None,
        "projected_user_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Stage9RegistrationIntentBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_naive_time_and_not_ready_intent_boundaries(self):
        naive = datetime(2026, 7, 12, 12, 0)
        self.assertEqual(intent_service._utc(naive).tzinfo, timezone.utc)
        with self.assertRaisesRegex(intent_service.TelegramRegistrationIntentError, "not_ready"):
            intent_service.intent_to_command(
                SimpleNamespace(address=None, contact_verified_at=None, completed_at=None)
            )

    async def test_authority_and_simple_read_boundaries(self):
        with override_current_server(SERVER_IRAN):
            for operation in (
                intent_service.create_or_reuse_ready_registration_intent(
                    _DB(),
                    invitation_token="INV-stage9-boundary",
                    mobile_number="09121112233",
                    telegram_id=771100,
                    telegram_username=None,
                    telegram_full_name=None,
                    address="Stage 9 complete address",
                    contact_verified_at=utc_now(),
                    completed_at=utc_now(),
                    invitation_expires_at_snapshot=utc_now() + timedelta(hours=1),
                ),
                intent_service.claim_due_registration_intents(
                    _DB(), limit=1, lease_seconds=5
                ),
            ):
                with self.assertRaisesRegex(
                    intent_service.TelegramRegistrationIntentError,
                    "foreign_authority_required",
                ):
                    await operation

        latest = SimpleNamespace(id=uuid4())
        self.assertIs(
            await intent_service.get_latest_registration_intent_for_telegram(
                _DB([_Result(one=latest)]), telegram_id=771100
            ),
            latest,
        )

    async def test_activation_block_short_circuits_absent_identity(self):
        self.assertIsNone(
            await intent_service.registration_activation_block_for_user(
                _DB(), user=SimpleNamespace(telegram_id=None)
            )
        )
        user = SimpleNamespace(
            id=8,
            telegram_id=771100,
            mobile_number="",
            address="",
        )
        self.assertIsNone(
            await intent_service.registration_activation_block_for_user(
                _DB([_Result(one=None)]), user=user
            )
        )

    async def test_schedule_retry_and_finalize_state_guards(self):
        command = _registration_command()
        terminal = _intent_from_command(
            command, status=TelegramRegistrationIntentStatus.REJECTED
        )
        for candidate in (None, terminal):
            self.assertFalse(
                await intent_service.schedule_registration_intent_retry(
                    _DB([_Result(one=candidate)]),
                    intent_id=command.command_id,
                    attempt=1,
                    error_code="transport",
                    next_retry_at=utc_now(),
                )
            )

        intent = _intent_from_command(command)
        db = _DB([_Result(one=intent)])
        self.assertTrue(
            await intent_service.schedule_registration_intent_retry(
                db,
                intent_id=command.command_id,
                attempt=1,
                error_code="UPSTREAM TIMEOUT!",
                next_retry_at=datetime(2026, 7, 12, 12, 0),
                authoritative_user_id=91,
            )
        )
        self.assertEqual(intent.status, TelegramRegistrationIntentStatus.RETRY_WAIT)
        self.assertEqual(intent.last_error_code, "internal_error")
        self.assertEqual(intent.authoritative_user_id, 91)
        no_authority = _intent_from_command(command)
        self.assertTrue(
            await intent_service.schedule_registration_intent_retry(
                _DB([_Result(one=no_authority)]),
                intent_id=command.command_id,
                attempt=1,
                error_code="transport",
                next_retry_at=utc_now(),
            )
        )
        self.assertIsNone(no_authority.authoritative_user_id)

        for candidate in (None, terminal):
            self.assertFalse(
                await intent_service.finalize_registration_intent(
                    _DB([_Result(one=candidate)]),
                    intent_id=command.command_id,
                    attempt=1,
                    outcome=TelegramRegistrationOutcome.CREATED,
                    authoritative_user_id=8,
                    projected_user_id=8,
                )
            )

        mismatch = _intent_from_command(command, retry_count=2)
        self.assertFalse(
            await intent_service.finalize_registration_intent(
                _DB([_Result(one=mismatch)]),
                intent_id=command.command_id,
                attempt=1,
                outcome=TelegramRegistrationOutcome.CREATED,
                authoritative_user_id=8,
                projected_user_id=8,
            )
        )
        for auth_id, projection_id, code in (
            (None, 8, "success_user_missing"),
            (8, None, "success_projection_missing"),
        ):
            with self.assertRaisesRegex(intent_service.TelegramRegistrationIntentError, code):
                await intent_service.finalize_registration_intent(
                    _DB([_Result(one=_intent_from_command(command))]),
                    intent_id=command.command_id,
                    attempt=1,
                    outcome=TelegramRegistrationOutcome.CREATED,
                    authoritative_user_id=auth_id,
                    projected_user_id=projection_id,
                )

        with self.assertRaisesRegex(intent_service.TelegramRegistrationIntentError, "failure_user_present"):
            await intent_service.finalize_registration_intent(
                _DB([_Result(one=_intent_from_command(command))]),
                intent_id=command.command_id,
                attempt=1,
                outcome=TelegramRegistrationOutcome.INVITATION_NOT_FOUND,
                authoritative_user_id=8,
            )

        success = _intent_from_command(command)
        self.assertTrue(
            await intent_service.finalize_registration_intent(
                _DB([_Result(one=success)]),
                intent_id=command.command_id,
                attempt=1,
                outcome=TelegramRegistrationOutcome.CREATED,
                authoritative_user_id=8,
                projected_user_id=9,
            )
        )
        self.assertEqual(success.projected_user_id, 9)

        expired = _intent_from_command(command)
        self.assertTrue(
            await intent_service.finalize_registration_intent(
                _DB([_Result(one=expired)]),
                intent_id=command.command_id,
                attempt=1,
                outcome=TelegramRegistrationOutcome.INVITATION_EXPIRED,
                authoritative_user_id=None,
            )
        )
        self.assertEqual(expired.status, TelegramRegistrationIntentStatus.EXPIRED)

    async def test_claim_rejects_invalid_local_intent_and_returns_valid_attempt(self):
        command = _registration_command()
        invalid = _intent_from_command(command, address=None, retry_count=0)
        valid = _intent_from_command(command, retry_count=0)
        db = _DB([_Result(values=[invalid, valid])])
        with override_current_server(SERVER_FOREIGN):
            attempts = await intent_service.claim_due_registration_intents(
                db,
                limit=2,
                lease_seconds=0,
                now=datetime(2026, 7, 12, 12, 0),
            )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(invalid.status, TelegramRegistrationIntentStatus.REJECTED)
        self.assertEqual(valid.status, TelegramRegistrationIntentStatus.FORWARDING)

    async def test_projection_rejection_and_success_matrix(self):
        command = _registration_command()
        completed = utc_now()
        invitation = SimpleNamespace(
            token=command.invitation_token,
            is_used=True,
            revoked_at=None,
            completed_at=completed,
            mobile_number=command.mobile_number,
            account_name="stage9-user",
            role=UserRole.STANDARD,
            kind=InvitationKind.STANDARD,
            registered_user_id=8,
        )
        user = SimpleNamespace(
            id=8,
            mobile_number=command.mobile_number,
            telegram_id=command.telegram_id,
            account_name="stage9-user",
            role=UserRole.STANDARD,
            is_deleted=False,
            account_status="active",
        )

        self.assertIsNone(
            await intent_service.registration_projection_is_ready(
                _DB([_Result(one=None)]), command=command
            )
        )
        bad_mobile = SimpleNamespace(**{**vars(invitation), "mobile_number": "09129999999"})
        self.assertIsNone(
            await intent_service.registration_projection_is_ready(
                _DB([_Result(one=bad_mobile)]), command=command
            )
        )
        self.assertIsNone(
            await intent_service.registration_projection_is_ready(
                _DB([_Result(one=invitation), _Result(values=[])]), command=command
            )
        )
        wrong_role = SimpleNamespace(**{**vars(user), "role": UserRole.WATCH})
        self.assertIsNone(
            await intent_service.registration_projection_is_ready(
                _DB([_Result(one=invitation), _Result(values=[wrong_role])]),
                command=command,
            )
        )

        split_relations = _DB(
            [
                _Result(one=invitation),
                _Result(values=[user]),
                _Result(values=[SimpleNamespace(), SimpleNamespace()]),
                _Result(values=[]),
            ]
        )
        self.assertIsNone(
            await intent_service.registration_projection_is_ready(
                split_relations, command=command
            )
        )
        self.assertIsNone(
            await intent_service.registration_projection_is_ready(
                _DB(
                    [
                        _Result(one=invitation),
                        _Result(values=[user]),
                        _Result(values=[]),
                        _Result(values=[SimpleNamespace()]),
                    ]
                ),
                command=command,
            )
        )

        invalid_kind = SimpleNamespace(**{**vars(invitation), "kind": "future"})
        self.assertIsNone(
            await intent_service.registration_projection_is_ready(
                _DB(
                    [
                        _Result(one=invalid_kind),
                        _Result(values=[user]),
                        _Result(values=[]),
                        _Result(values=[]),
                    ]
                ),
                command=command,
            )
        )

        with patch.object(
            intent_service,
            "evaluate_bot_access_projection",
            return_value=SimpleNamespace(allowed=True),
        ):
            resolved = await intent_service.registration_projection_is_ready(
                _DB(
                    [
                        _Result(one=invitation),
                        _Result(values=[user]),
                        _Result(values=[]),
                        _Result(values=[]),
                    ]
                ),
                command=command,
            )
        self.assertEqual(resolved.local_user_id, user.id)

        customer_invitation = SimpleNamespace(
            **{**vars(invitation), "kind": InvitationKind.CUSTOMER}
        )
        wrong_customer = SimpleNamespace(customer_user_id=99, customer_tier=CustomerTier.TIER_1)
        self.assertIsNone(
            await intent_service.registration_projection_is_ready(
                _DB(
                    [
                        _Result(one=customer_invitation),
                        _Result(values=[user]),
                        _Result(values=[]),
                        _Result(values=[wrong_customer]),
                    ]
                ),
                command=command,
            )
        )

        accountant_invitation = SimpleNamespace(
            **{**vars(invitation), "kind": InvitationKind.ACCOUNTANT}
        )
        self.assertIsNone(
            await intent_service.registration_projection_is_ready(
                _DB(
                    [
                        _Result(one=accountant_invitation),
                        _Result(values=[user]),
                        _Result(values=[]),
                        _Result(values=[]),
                    ]
                ),
                command=command,
            )
        )


class Stage9UserDeletionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_invitation_wrappers_and_pending_revoke_boundaries(self):
        db = _DB()
        with patch.object(
            user_deletion_service,
            "_soft_revoke_pending_invitation",
            new=AsyncMock(),
        ) as revoke:
            await user_deletion_service._invalidate_accountant_invitation(
                db, "ACCT-a", utc_now()
            )
            await user_deletion_service._invalidate_customer_invitation(
                db, "CUST-a", utc_now()
            )
        self.assertEqual(revoke.await_count, 2)

        with patch.object(
            user_deletion_service,
            "lock_invitation_for_transition",
            new=AsyncMock(return_value=None),
        ):
            await user_deletion_service._soft_revoke_pending_invitation(
                db, invitation_token="INV-none", now=utc_now()
            )

        with patch.object(
            user_deletion_service,
            "lock_invitation_for_transition",
            new=AsyncMock(return_value=SimpleNamespace(id=6)),
        ), patch.object(
            user_deletion_service,
            "derive_invitation_state",
            return_value=InvitationDerivedState.COMPLETED,
        ):
            await user_deletion_service._soft_revoke_pending_invitation(
                db, invitation_token="INV-complete", now=utc_now()
            )

        invitation = SimpleNamespace(id=5)
        with patch.object(
            user_deletion_service,
            "derive_invitation_state",
            return_value=InvitationDerivedState.PENDING,
        ), patch.object(
            user_deletion_service, "soft_revoke_invitation"
        ) as soft_revoke, patch.object(
            user_deletion_service,
            "release_invitation_identity",
            new=AsyncMock(),
        ) as release:
            await user_deletion_service._revoke_pending_relation_invitation(
                db, invitation=invitation, now=utc_now()
            )
        soft_revoke.assert_called_once()
        release.assert_awaited_once()

        with self.assertRaisesRegex(RuntimeError, "missing"):
            await user_deletion_service._revoke_pending_relation_invitation(
                db, invitation=None, now=utc_now()
            )
        with patch.object(
            user_deletion_service,
            "derive_invitation_state",
            return_value=InvitationDerivedState.COMPLETED,
        ), self.assertRaisesRegex(RuntimeError, "conflict"):
            await user_deletion_service._revoke_pending_relation_invitation(
                db, invitation=invitation, now=utc_now()
            )

    async def test_identity_revoke_skips_missing_and_terminal_then_revokes_pending(self):
        pending = SimpleNamespace(id=7)
        terminal = SimpleNamespace(id=6)
        db = _DB([_Result(values=[5, 6, 7])])
        with patch.object(
            user_deletion_service,
            "lock_invitation_for_transition",
            new=AsyncMock(side_effect=[None, terminal, pending]),
        ), patch.object(
            user_deletion_service,
            "derive_invitation_state",
            side_effect=[InvitationDerivedState.COMPLETED, InvitationDerivedState.PENDING],
        ), patch.object(
            user_deletion_service, "soft_revoke_invitation"
        ) as soft_revoke, patch.object(
            user_deletion_service,
            "release_invitation_identity",
            new=AsyncMock(),
        ) as release:
            await user_deletion_service._soft_revoke_pending_invitations_for_user_identity(
                db,
                mobile_number="09121112233",
                account_name="stage9-user",
                now=utc_now(),
            )
        soft_revoke.assert_called_once_with(pending, revoked_at=soft_revoke.call_args.kwargs["revoked_at"])
        release.assert_awaited_once_with(db, invitation_id=7)

    async def test_relation_lock_detects_invitation_token_drift(self):
        invitation = SimpleNamespace(id=1)
        for method, relation in (
            (
                user_deletion_service._lock_accountant_relation_transition,
                SimpleNamespace(invitation_token="changed"),
            ),
            (
                user_deletion_service._lock_customer_relation_transition,
                SimpleNamespace(invitation_token="changed"),
            ),
        ):
            with self.subTest(method=method.__name__), patch.object(
                user_deletion_service,
                "lock_invitation_for_transition",
                new=AsyncMock(return_value=invitation),
            ), self.assertRaisesRegex(RuntimeError, "changed_during_transition_lock"):
                await method(
                    _DB([_Result(one=relation)]),
                    relation_id=1,
                    invitation_token="original",
                )

        matching = SimpleNamespace(invitation_token="original")
        for method in (
            user_deletion_service._lock_accountant_relation_transition,
            user_deletion_service._lock_customer_relation_transition,
        ):
            with self.subTest(method=method.__name__, case="matching"), patch.object(
                user_deletion_service,
                "lock_invitation_for_transition",
                new=AsyncMock(return_value=invitation),
            ):
                locked_invitation, locked_relation = await method(
                    _DB([_Result(one=matching)]),
                    relation_id=1,
                    invitation_token="original",
                )
            self.assertIs(locked_invitation, invitation)
            self.assertIs(locked_relation, matching)

    async def test_relation_closers_skip_candidates_that_disappear_after_lock(self):
        user = SimpleNamespace(id=3)
        candidate = SimpleNamespace(id=4, invitation_token="INV-a")
        cases = (
            (
                user_deletion_service._close_owned_accountant_relations,
                "_lock_accountant_relation_transition",
                True,
            ),
            (
                user_deletion_service._close_linked_accountant_relations,
                "_lock_accountant_relation_transition",
                False,
            ),
            (
                user_deletion_service._close_owned_customer_relations,
                "_lock_customer_relation_transition",
                True,
            ),
            (
                user_deletion_service._close_linked_customer_relations,
                "_lock_customer_relation_transition",
                False,
            ),
        )
        for method, lock_name, owned in cases:
            with self.subTest(method=method.__name__), patch.object(
                user_deletion_service,
                lock_name,
                new=AsyncMock(return_value=(None, None)),
            ):
                kwargs = (
                    {"processed_user_ids": set(), "effects": []}
                    if owned
                    else {}
                )
                await method(_DB([_Result(values=[candidate])]), user, **kwargs)


class _RawRequest:
    def __init__(self, *, source=SERVER_FOREIGN):
        self.headers = {
            "x-timestamp": "1700000000",
            "x-signature": "signature",
            "x-api-key": "key",
            "x-source-server": source,
        }

    async def body(self):
        return b"{}"


def _internal_invitation_payload(**overrides):
    requester = InvitationRequesterIdentity(
        account_name="stage9-admin",
        mobile_number="09121110000",
        telegram_id=770001,
    )
    values = {
        "requester_identity": requester,
        "account_name": "stage9-invitee",
        "mobile_number": "09121112233",
        "role": UserRole.STANDARD,
        "source_server": SERVER_FOREIGN,
    }
    values["idempotency_key"] = build_standard_invitation_idempotency_key(
        requester_identity=requester,
        account_name=values["account_name"],
        mobile_number=values["mobile_number"],
        role=values["role"],
    )
    values.update(overrides)
    return InternalInvitationCreateRequest(**values)


class Stage9InvitationRouterBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_link_builder_and_creation_error_mapping(self):
        contract = SimpleNamespace(
            bot_link=None,
            web_link="https://web.example/invite",
            web_short_link=None,
        )
        with patch.object(
            invitations_router, "build_invitation_contract_v2", return_value=contract
        ):
            self.assertEqual(
                invitations_router.build_invitation_links(SimpleNamespace()),
                ("", contract.web_link, None),
            )

        cases = (
            ("iran_authority_required", 403),
            ("user_identity_exists", 400),
            ("invalid_identity", 400),
            ("identity_reserved", 409),
            ("future_error", 409),
        )
        for code, status_code in cases:
            with self.subTest(code=code):
                error = invitations_router._creation_error_http_exception(
                    CanonicalInvitationCreationError(code)
                )
                self.assertEqual(error.status_code, status_code)

    async def test_internal_endpoint_rejects_surface_source_key_requester_and_role(self):
        payload = _internal_invitation_payload()
        common = patch.object(invitations_router, "verify_internal_signature", return_value=True)
        with common, override_current_server(SERVER_FOREIGN), self.assertRaises(HTTPException) as raised:
            await invitations_router.create_invitation_internal_from_bot(
                payload, _RawRequest(), db=_DB()
            )
        self.assertEqual(raised.exception.status_code, 403)

        with patch.object(
            invitations_router, "verify_internal_signature", return_value=True
        ), override_current_server(SERVER_IRAN), self.assertRaises(HTTPException) as raised:
            await invitations_router.create_invitation_internal_from_bot(
                payload, _RawRequest(source=SERVER_IRAN), db=_DB()
            )
        self.assertEqual(raised.exception.status_code, 401)

        bad_key = payload.model_copy(update={"idempotency_key": "standard-invitation:" + "b" * 40})
        with patch.object(
            invitations_router, "verify_internal_signature", return_value=True
        ), override_current_server(SERVER_IRAN), self.assertRaises(HTTPException) as raised:
            await invitations_router.create_invitation_internal_from_bot(
                bad_key, _RawRequest(), db=_DB()
            )
        self.assertEqual(raised.exception.status_code, 400)

        for code, status_code in (("requester_missing", 404), ("requester_inactive", 403)):
            with self.subTest(code=code), patch.object(
                invitations_router, "verify_internal_signature", return_value=True
            ), patch.object(
                invitations_router,
                "resolve_current_invitation_requester",
                new=AsyncMock(
                    side_effect=invitations_router.InvitationRequesterResolutionError(code)
                ),
            ), override_current_server(SERVER_IRAN), self.assertRaises(HTTPException) as raised:
                await invitations_router.create_invitation_internal_from_bot(
                    payload, _RawRequest(), db=_DB()
                )
            self.assertEqual(raised.exception.status_code, status_code)

        unauthorized = SimpleNamespace(role=UserRole.STANDARD)
        with patch.object(
            invitations_router, "verify_internal_signature", return_value=True
        ), patch.object(
            invitations_router,
            "resolve_current_invitation_requester",
            new=AsyncMock(return_value=unauthorized),
        ), override_current_server(SERVER_IRAN), self.assertRaises(HTTPException) as raised:
            await invitations_router.create_invitation_internal_from_bot(
                payload, _RawRequest(), db=_DB()
            )
        self.assertEqual(raised.exception.status_code, 403)

    async def test_delete_pending_without_database_id_commits_soft_revoke(self):
        invitation = SimpleNamespace(
            id=None,
            token="INV-stage9",
            created_by_id=7,
            is_used=False,
            revoked_at=None,
            expires_at=utc_now().replace(tzinfo=None) + timedelta(hours=1),
        )
        admin = SimpleNamespace(id=7, role=UserRole.SUPER_ADMIN)
        db = _DB()
        with patch.object(
            invitations_router,
            "lock_invitation_for_transition",
            new=AsyncMock(return_value=invitation),
        ), patch.object(
            invitations_router, "soft_revoke_invitation"
        ) as revoke, patch.object(
            invitations_router,
            "release_invitation_identity",
            new=AsyncMock(),
        ) as release:
            await invitations_router.delete_pending_invitation(1, db=db, admin=admin)
        revoke.assert_called_once()
        release.assert_not_awaited()
        db.commit.assert_awaited_once()

    async def test_public_lookup_and_validate_terminal_and_customer_paths(self):
        future = utc_now().replace(tzinfo=None) + timedelta(hours=1)

        def invitation(**overrides):
            values = {
                "id": 8,
                "token": "INV-stage9",
                "short_code": "STAGE900",
                "is_used": False,
                "revoked_at": None,
                "kind": InvitationKind.STANDARD,
                "expires_at": future,
            }
            values.update(overrides)
            return SimpleNamespace(**values)

        terminal_cases = (
            ("lookup", invitation(revoked_at=utc_now()), "Invitation revoked"),
            ("lookup", invitation(kind=InvitationKind.LEGACY_UNKNOWN), "ambiguous"),
            ("validate", invitation(revoked_at=utc_now()), "Invitation revoked"),
            ("validate", invitation(kind=InvitationKind.LEGACY_UNKNOWN), "ambiguous"),
        )
        for endpoint, inv, detail in terminal_cases:
            method = (
                invitations_router.lookup_invitation
                if endpoint == "lookup"
                else invitations_router.validate_invitation
            )
            key = inv.short_code if endpoint == "lookup" else inv.token
            with self.subTest(endpoint=endpoint, detail=detail), patch.object(
                invitations_router.settings, "invitation_contract_v2_enabled", False
            ), self.assertRaises(HTTPException) as raised:
                await method(key, db=_DB([_Result(one=inv)]))
            self.assertIn(detail.lower(), str(raised.exception.detail).lower())

        customer = invitation(token="CUST-stage9", kind=InvitationKind.CUSTOMER)
        relation = SimpleNamespace(customer_tier=CustomerTier.TIER_2)
        with patch.object(
            invitations_router.settings, "invitation_contract_v2_enabled", False
        ), patch.object(
            invitations_router, "derive_invitation_state", return_value=InvitationDerivedState.PENDING
        ), patch.object(
            invitations_router,
            "get_pending_customer_relation_by_invitation_token",
            new=AsyncMock(return_value=relation),
        ), patch.object(invitations_router, "audit_log"):
            result = await invitations_router.lookup_invitation(
                customer.short_code, db=_DB([_Result(one=customer)])
            )
        self.assertEqual(result, {"token": customer.token})

        accountant = invitation(token="ACCT-stage9", kind=InvitationKind.ACCOUNTANT)
        with patch.object(
            invitations_router.settings, "invitation_contract_v2_enabled", False
        ), patch.object(
            invitations_router, "derive_invitation_state", return_value=InvitationDerivedState.PENDING
        ), patch.object(
            invitations_router,
            "get_pending_accountant_relation_by_invitation_token",
            new=AsyncMock(return_value=SimpleNamespace()),
        ), patch.object(invitations_router, "audit_log"):
            result = await invitations_router.lookup_invitation(
                accountant.short_code, db=_DB([_Result(one=accountant)])
            )
        self.assertEqual(result, {"token": accountant.token})


if __name__ == "__main__":
    unittest.main()
