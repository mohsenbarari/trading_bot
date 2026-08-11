import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.services.accountant_relation_service import (
    ACCOUNTANT_INVITATION_PREFIX,
    ACCOUNTANT_RELATION_DELETE_PRECONDITION_DETAIL,
    AccountantRelationDeletionAction,
    build_trade_notification_audience_user_ids,
    cancel_pending_accountant_relation,
    create_owner_accountant_relation,
    generate_accountant_invitation_token,
    generate_accountant_short_code,
    get_accountant_relation_by_invitation_token,
    get_active_accountant_relation_for_accountant,
    get_effective_max_accountants,
    get_owner_accountant_relation,
    get_pending_accountant_relation_by_invitation_token,
    list_active_accountants_for_owner,
    list_owner_accountant_relations,
    resolve_effective_owner_actor,
    sweep_expired_pending_accountant_relations,
    unlink_owner_accountant_relation,
    is_accountant_invitation_token,
    is_user_accountant,
    update_owner_accountant_relation,
    validate_accountant_capacity,
)
from core.services.canonical_invitation_creation_service import CanonicalInvitationCreationError
from models.accountant_relation import AccountantRelation
from models.accountant_relation import AccountantRelationStatus
from models.invitation import InvitationKind
from models.user import UserRole


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def first(self):
        return self._values[0] if self._values else None


class FakeExecuteResult:
    def __init__(self, values=None, scalar_one_value=None):
        self._values = values or []
        self._scalar_one_value = scalar_one_value

    def scalars(self):
        return FakeScalarResult(self._values)

    def scalar_one(self):
        return self._scalar_one_value

    def scalar_one_or_none(self):
        return self._scalar_one_value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.refresh = AsyncMock()
        self.added = []

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, item):
        self.added.append(item)


class AccountantRelationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        authority = patch(
            "core.services.accountant_relation_service.current_server",
            return_value="iran",
        )
        authority.start()
        self.addCleanup(authority.stop)
        sms_delivery = patch(
            "core.services.accountant_relation_service.prepare_invitation_sms_delivery",
            new=AsyncMock(),
        )
        sms_delivery.start()
        self.addCleanup(sms_delivery.stop)

    def test_accountant_relation_status_column_uses_database_values(self):
        self.assertEqual(
            AccountantRelation.__table__.c.status.type.enums,
            ["pending", "active", "expired", "revoked", "deleted"],
        )

    def test_get_effective_max_accountants_clamps_invalid_values(self):
        self.assertEqual(get_effective_max_accountants(SimpleNamespace(max_accountants=5)), 5)
        self.assertEqual(get_effective_max_accountants(SimpleNamespace(max_accountants=-2)), 0)
        self.assertEqual(get_effective_max_accountants(SimpleNamespace(max_accountants="bad")), 3)
        self.assertEqual(get_effective_max_accountants(SimpleNamespace()), 3)
        self.assertTrue(is_accountant_invitation_token(f"{ACCOUNTANT_INVITATION_PREFIX}123"))
        self.assertFalse(is_accountant_invitation_token("INV-123"))

    def test_accountant_invitation_generators_return_expected_shapes(self):
        invitation_token = generate_accountant_invitation_token()
        short_code = generate_accountant_short_code()

        self.assertTrue(invitation_token.startswith(ACCOUNTANT_INVITATION_PREFIX))
        self.assertEqual(len(short_code), 8)

    async def test_get_active_accountant_relation_for_accountant_returns_active_relation(self):
        relation = SimpleNamespace(id=41, owner_user=SimpleNamespace(id=7), accountant_user=SimpleNamespace(id=9))
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        result = await get_active_accountant_relation_for_accountant(db, 9)

        self.assertIs(result, relation)

    async def test_list_active_accountants_for_owner_returns_rows(self):
        relation_one = SimpleNamespace(id=1)
        relation_two = SimpleNamespace(id=2)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[relation_one, relation_two])])

        result = await list_active_accountants_for_owner(db, 12)

        self.assertEqual(result, [relation_one, relation_two])

    async def test_build_trade_notification_audience_user_ids_includes_owners_and_active_accountants(self):
        db = FakeDB(execute_results=[FakeExecuteResult(values=[21, 22, 21, None])])

        result = await build_trade_notification_audience_user_ids(db, [7, "8", 7, 0, None])

        self.assertEqual(result, [7, 8, 21, 22])

    async def test_build_trade_notification_audience_user_ids_handles_empty_owner_set(self):
        result = await build_trade_notification_audience_user_ids(FakeDB(), [None, 0, "bad"])

        self.assertEqual(result, [])

    async def test_sweep_expired_pending_accountant_relations_marks_rows_deleted(self):
        expired = SimpleNamespace(
            id=31,
            invitation_token=f"{ACCOUNTANT_INVITATION_PREFIX}sweep",
            status=AccountantRelationStatus.PENDING,
            deleted_at=None,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[expired]),
                FakeExecuteResult(scalar_one_value=expired),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=SimpleNamespace(id=81)),
        ), patch(
            "core.services.accountant_relation_service.release_invitation_identity",
            new=AsyncMock(),
        ):
            expired_relations = await sweep_expired_pending_accountant_relations(db)

        self.assertEqual(expired_relations, [expired])
        self.assertEqual(expired.status, AccountantRelationStatus.EXPIRED)
        self.assertIsNotNone(expired.deleted_at)

    async def test_get_pending_accountant_relation_by_invitation_token_commits_expired_rows_before_lookup(self):
        pending_relation = SimpleNamespace(id=12)
        expired = SimpleNamespace(
            id=13,
            invitation_token=f"{ACCOUNTANT_INVITATION_PREFIX}token",
            status=AccountantRelationStatus.PENDING,
            deleted_at=None,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[expired]),
                FakeExecuteResult(scalar_one_value=expired),
                FakeExecuteResult(scalar_one_value=pending_relation),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=SimpleNamespace(id=82)),
        ), patch(
            "core.services.accountant_relation_service.release_invitation_identity",
            new=AsyncMock(),
        ):
            result = await get_pending_accountant_relation_by_invitation_token(
                db,
                f"{ACCOUNTANT_INVITATION_PREFIX}token",
            )

        self.assertIs(result, pending_relation)
        db.commit.assert_awaited_once()

    async def test_get_accountant_relation_by_invitation_token_and_is_user_accountant(self):
        relation = SimpleNamespace(id=9)
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        result = await get_accountant_relation_by_invitation_token(db, f"{ACCOUNTANT_INVITATION_PREFIX}token")
        self.assertIs(result, relation)

        with patch(
            "core.services.accountant_relation_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=relation),
        ):
            self.assertTrue(await is_user_accountant(FakeDB(), 5))

    async def test_validate_accountant_capacity_raises_when_owner_is_full(self):
        owner = SimpleNamespace(id=5, max_accountants=2)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[]), FakeExecuteResult(scalar_one_value=2)])

        with self.assertRaises(HTTPException) as exc_info:
            await validate_accountant_capacity(db, owner)

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Owner has reached the maximum number of accountants")

    async def test_validate_accountant_capacity_returns_current_count_and_limit(self):
        owner = SimpleNamespace(id=5, max_accountants=4)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[]), FakeExecuteResult(scalar_one_value=2)])

        current_count, limit = await validate_accountant_capacity(db, owner)

        self.assertEqual(current_count, 2)
        self.assertEqual(limit, 4)

    async def test_create_owner_accountant_relation_creates_pending_relation_and_invitation(self):
        owner = SimpleNamespace(id=7, max_accountants=3)
        expected_expiry = datetime.utcnow() + timedelta(days=2)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[]),
                FakeExecuteResult(scalar_one_value=0),
                FakeExecuteResult(scalar_one_value=None),
                FakeExecuteResult(scalar_one_value=None),
                FakeExecuteResult(scalar_one_value=None),
            ]
        )
        invitation_fixture = SimpleNamespace(
            account_name="accountant_1",
            mobile_number="09123456789",
            role=UserRole.WATCH,
            kind=InvitationKind.ACCOUNTANT,
            token=f"{ACCOUNTANT_INVITATION_PREFIX}token",
            expires_at=expected_expiry,
        )

        with patch(
            "core.services.accountant_relation_service.create_or_reuse_canonical_invitation",
            new=AsyncMock(return_value=SimpleNamespace(invitation=invitation_fixture, created=True)),
        ):
            relation, invitation = await create_owner_accountant_relation(
                db,
                owner_user=owner,
                global_account_name=" accountant_1 ",
                relation_display_name="  حسابدار اول  ",
                mobile_number="۰۹۱۲۳۴۵۶۷۸۹",
                duty_description="  کارهای روزانه  ",
            )

        self.assertEqual(len(db.added), 1)
        self.assertEqual(invitation.account_name, "accountant_1")
        self.assertEqual(invitation.mobile_number, "09123456789")
        self.assertEqual(invitation.role, UserRole.WATCH)
        self.assertEqual(invitation.kind, InvitationKind.ACCOUNTANT)
        self.assertEqual(invitation.expires_at, expected_expiry)
        self.assertEqual(relation.expires_at, expected_expiry)
        self.assertEqual(invitation.token, f"{ACCOUNTANT_INVITATION_PREFIX}token")
        self.assertEqual(relation.owner_user_id, 7)
        self.assertEqual(relation.global_account_name, "accountant_1")
        self.assertEqual(relation.relation_display_name, "حسابدار اول")
        self.assertEqual(relation.mobile_number, "09123456789")
        self.assertEqual(relation.duty_description, "کارهای روزانه")
        self.assertEqual(relation.status, AccountantRelationStatus.PENDING)
        db.commit.assert_awaited_once()
        self.assertEqual(db.refresh.await_count, 2)

    async def test_create_owner_accountant_relation_rejects_duplicate_and_existing_user_states(self):
        owner = SimpleNamespace(id=7, max_accountants=3)

        with self.assertRaises(HTTPException) as exc_info:
            await create_owner_accountant_relation(
                FakeDB(),
                owner_user=owner,
                global_account_name="acc",
                relation_display_name="",
                mobile_number="0912",
            )
        self.assertEqual(exc_info.exception.status_code, 400)

        db = FakeDB()
        with patch(
            "core.services.accountant_relation_service.create_or_reuse_canonical_invitation",
            new=AsyncMock(side_effect=CanonicalInvitationCreationError("user_identity_exists")),
        ), self.assertRaises(HTTPException) as exc_info:
            await create_owner_accountant_relation(db, owner_user=owner, global_account_name="acc", relation_display_name="disp", mobile_number="09120000000")
        self.assertIn("قبلاً ثبت شده", exc_info.exception.detail)

        with patch(
            "core.services.accountant_relation_service.create_or_reuse_canonical_invitation",
            new=AsyncMock(side_effect=CanonicalInvitationCreationError("invitation_identity_conflict")),
        ), self.assertRaises(HTTPException) as exc_info:
            await create_owner_accountant_relation(FakeDB(), owner_user=owner, global_account_name="acc", relation_display_name="disp", mobile_number="09120000000")
        self.assertEqual(exc_info.exception.status_code, 409)

        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[]),
                FakeExecuteResult(scalar_one_value=0),
                FakeExecuteResult(scalar_one_value=SimpleNamespace(id=3)),
            ]
        )
        invitation_fixture = SimpleNamespace(token="ACCT-new", expires_at=datetime.utcnow() + timedelta(days=2))
        with patch(
            "core.services.accountant_relation_service.create_or_reuse_canonical_invitation",
            new=AsyncMock(return_value=SimpleNamespace(invitation=invitation_fixture, created=True)),
        ), self.assertRaises(HTTPException) as exc_info:
            await create_owner_accountant_relation(
                db,
                owner_user=owner,
                global_account_name="acc",
                relation_display_name="disp",
                mobile_number="09120000000",
            )
        self.assertIn("نام نمایشی", exc_info.exception.detail)

    async def test_list_owner_accountant_relations_commits_expired_rows_then_returns_pending_and_active(self):
        relation_one = SimpleNamespace(id=1)
        relation_two = SimpleNamespace(id=2)
        expired = SimpleNamespace(
            id=3,
            invitation_token=f"{ACCOUNTANT_INVITATION_PREFIX}list",
            status=AccountantRelationStatus.PENDING,
            deleted_at=None,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[expired]),
                FakeExecuteResult(scalar_one_value=expired),
                FakeExecuteResult(values=[relation_one, relation_two]),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=SimpleNamespace(id=83)),
        ), patch(
            "core.services.accountant_relation_service.release_invitation_identity",
            new=AsyncMock(),
        ):
            result = await list_owner_accountant_relations(db, 7)

        self.assertEqual(result, [relation_one, relation_two])
        db.commit.assert_awaited_once()

    async def test_get_owner_accountant_relation_returns_terminal_relation(self):
        terminal_relation = SimpleNamespace(
            id=17,
            owner_user_id=7,
            status=AccountantRelationStatus.DELETED,
            deleted_at=datetime.utcnow(),
            accountant_user=None,
        )
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=terminal_relation)])

        result = await get_owner_accountant_relation(
            db,
            owner_user_id=7,
            relation_id=17,
        )

        self.assertIs(result, terminal_relation)

    async def test_get_owner_accountant_relation_rejects_missing_or_other_owner_relation(self):
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=None)])

        with self.assertRaises(HTTPException) as exc_info:
            await get_owner_accountant_relation(
                db,
                owner_user_id=7,
                relation_id=17,
            )

        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_cancel_pending_accountant_relation_revokes_pending_relation(self):
        relation = SimpleNamespace(
            id=4,
            owner_user_id=7,
            status=AccountantRelationStatus.PENDING,
            deleted_at=None,
            invitation_token=f"{ACCOUNTANT_INVITATION_PREFIX}token",
        )
        invitation = SimpleNamespace(
            id=84,
            token=relation.invitation_token,
            is_used=False,
            revoked_at=None,
            expires_at=datetime.utcnow() + timedelta(days=1),
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=relation.invitation_token),
                FakeExecuteResult(scalar_one_value=relation),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=invitation),
        ), patch(
            "core.services.accountant_relation_service.release_invitation_identity",
            new=AsyncMock(),
        ):
            result = await cancel_pending_accountant_relation(
                db,
                owner_user_id=7,
                relation_id=4,
            )

        self.assertIs(result, relation)
        self.assertEqual(relation.status, AccountantRelationStatus.REVOKED)
        self.assertFalse(invitation.is_used)
        self.assertIsNotNone(invitation.revoked_at)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(relation)

    async def test_cancel_pending_accountant_relation_rejects_missing_or_non_pending_relation(self):
        with self.assertRaises(HTTPException) as exc_info:
            await cancel_pending_accountant_relation(FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=None)]), owner_user_id=7, relation_id=4)
        self.assertEqual(exc_info.exception.status_code, 404)

        relation = SimpleNamespace(
            id=4,
            owner_user_id=7,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
            invitation_token=f"{ACCOUNTANT_INVITATION_PREFIX}token",
        )
        active_db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=relation.invitation_token),
                FakeExecuteResult(scalar_one_value=relation),
            ]
        )
        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=None),
        ), self.assertRaises(HTTPException) as exc_info:
            await cancel_pending_accountant_relation(
                active_db,
                owner_user_id=7,
                relation_id=4,
            )
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_unlink_owner_accountant_relation_accepts_cancel_pending_action(self):
        invitation_token = f"{ACCOUNTANT_INVITATION_PREFIX}pending-delete"
        relation = SimpleNamespace(
            id=18,
            owner_user_id=7,
            status=AccountantRelationStatus.PENDING,
            deleted_at=None,
            invitation_token=invitation_token,
            accountant_user_id=None,
            accountant_user=None,
        )
        invitation = SimpleNamespace(
            id=85,
            token=invitation_token,
            is_used=False,
            revoked_at=None,
            expires_at=datetime.utcnow() + timedelta(days=1),
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=invitation_token),
                FakeExecuteResult(scalar_one_value=relation),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=invitation),
        ), patch(
            "core.services.accountant_relation_service.release_invitation_identity",
            new=AsyncMock(),
        ) as release_mock, patch(
            "core.services.user_deletion_service.delete_user_account",
            new=AsyncMock(),
        ) as delete_mock:
            result = await unlink_owner_accountant_relation(
                db,
                owner_user_id=7,
                relation_id=18,
                expected_action=AccountantRelationDeletionAction.CANCEL_PENDING,
            )

        self.assertIs(result, relation)
        self.assertEqual(relation.status, AccountantRelationStatus.REVOKED)
        self.assertIsNotNone(relation.deleted_at)
        self.assertIsNotNone(invitation.revoked_at)
        release_mock.assert_awaited_once_with(db, invitation_id=85)
        delete_mock.assert_not_awaited()
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(relation)

    async def test_unlink_owner_accountant_relation_requires_genuinely_pending_invitation(self):
        now = datetime.utcnow()
        invalid_invitations = {
            "missing": None,
            "expired": SimpleNamespace(
                id=86,
                is_used=False,
                revoked_at=None,
                expires_at=now - timedelta(seconds=1),
            ),
            "used": SimpleNamespace(
                id=87,
                is_used=True,
                registered_user_id=88,
                completed_at=now,
                completed_via="web",
                revoked_at=None,
                expires_at=now + timedelta(days=1),
            ),
            "revoked": SimpleNamespace(
                id=89,
                is_used=False,
                revoked_at=now,
                expires_at=now + timedelta(days=1),
            ),
        }

        for case, invitation in invalid_invitations.items():
            with self.subTest(case=case):
                invitation_token = f"{ACCOUNTANT_INVITATION_PREFIX}invalid-{case}"
                relation = SimpleNamespace(
                    id=18,
                    owner_user_id=7,
                    status=AccountantRelationStatus.PENDING,
                    deleted_at=None,
                    invitation_token=invitation_token,
                    accountant_user_id=None,
                    accountant_user=None,
                )
                db = FakeDB(
                    execute_results=[
                        FakeExecuteResult(scalar_one_value=invitation_token),
                        FakeExecuteResult(scalar_one_value=relation),
                    ]
                )

                with patch(
                    "core.services.accountant_relation_service.lock_invitation_for_transition",
                    new=AsyncMock(return_value=invitation),
                ), patch(
                    "core.services.accountant_relation_service.soft_revoke_invitation",
                ) as revoke_mock, patch(
                    "core.services.accountant_relation_service.release_invitation_identity",
                    new=AsyncMock(),
                ) as release_mock, patch(
                    "core.services.accountant_relation_service.release_invitation_identities_for_tokens",
                    new=AsyncMock(),
                ) as release_tokens_mock, patch(
                    "core.services.user_deletion_service.delete_user_account",
                    new=AsyncMock(),
                ) as delete_mock, self.assertRaises(HTTPException) as exc_info:
                    await unlink_owner_accountant_relation(
                        db,
                        owner_user_id=7,
                        relation_id=18,
                        expected_action=AccountantRelationDeletionAction.CANCEL_PENDING,
                    )

                self.assertEqual(exc_info.exception.status_code, 409)
                self.assertEqual(
                    exc_info.exception.detail,
                    ACCOUNTANT_RELATION_DELETE_PRECONDITION_DETAIL,
                )
                self.assertEqual(relation.status, AccountantRelationStatus.PENDING)
                self.assertIsNone(relation.deleted_at)
                revoke_mock.assert_not_called()
                release_mock.assert_not_awaited()
                release_tokens_mock.assert_not_awaited()
                delete_mock.assert_not_awaited()
                db.commit.assert_not_awaited()

    async def test_unlink_owner_accountant_relation_accepts_delete_relation_action(self):
        invitation_token = f"{ACCOUNTANT_INVITATION_PREFIX}orphan-delete"
        relation = SimpleNamespace(
            id=19,
            owner_user_id=7,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
            invitation_token=invitation_token,
            accountant_user_id=None,
            accountant_user=None,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=invitation_token),
                FakeExecuteResult(scalar_one_value=relation),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.accountant_relation_service.release_invitation_identities_for_tokens",
            new=AsyncMock(),
        ) as release_mock, patch(
            "core.services.user_deletion_service.delete_user_account",
            new=AsyncMock(),
        ) as delete_mock:
            result = await unlink_owner_accountant_relation(
                db,
                owner_user_id=7,
                relation_id=19,
                expected_action=AccountantRelationDeletionAction.DELETE_RELATION,
            )

        self.assertIs(result, relation)
        self.assertEqual(relation.status, AccountantRelationStatus.DELETED)
        self.assertIsNotNone(relation.deleted_at)
        release_mock.assert_awaited_once_with(
            db,
            invitation_tokens=[invitation_token],
        )
        delete_mock.assert_not_awaited()
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(relation)

    async def test_unlink_owner_accountant_relation_accepts_delete_account_action(self):
        invitation_token = f"{ACCOUNTANT_INVITATION_PREFIX}account-delete"
        accountant_user = SimpleNamespace(id=77, is_deleted=False)
        relation = SimpleNamespace(
            id=20,
            owner_user_id=7,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
            invitation_token=invitation_token,
            accountant_user_id=77,
            accountant_user=accountant_user,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=invitation_token),
                FakeExecuteResult(scalar_one_value=relation),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.user_deletion_service.delete_user_account",
            new=AsyncMock(return_value=SimpleNamespace(user_id=77)),
        ) as delete_mock:
            result = await unlink_owner_accountant_relation(
                db,
                owner_user_id=7,
                relation_id=20,
                expected_action=AccountantRelationDeletionAction.DELETE_ACCOUNT,
            )

        delete_mock.assert_awaited_once_with(db, accountant_user)
        self.assertIs(result, relation)
        self.assertEqual(relation.status, AccountantRelationStatus.DELETED)
        self.assertIsNotNone(relation.deleted_at)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(relation)

    async def test_unlink_owner_accountant_relation_rejects_pending_to_active_escalation_before_effects(self):
        invitation_token = f"{ACCOUNTANT_INVITATION_PREFIX}pending-became-active"
        accountant_user = SimpleNamespace(id=78, is_deleted=False)
        relation = SimpleNamespace(
            id=21,
            owner_user_id=7,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
            invitation_token=invitation_token,
            accountant_user_id=78,
            accountant_user=accountant_user,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=invitation_token),
                FakeExecuteResult(scalar_one_value=relation),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.accountant_relation_service.release_invitation_identity",
            new=AsyncMock(),
        ) as release_mock, patch(
            "core.services.user_deletion_service.delete_user_account",
            new=AsyncMock(),
        ) as delete_mock, self.assertRaises(HTTPException) as exc_info:
            await unlink_owner_accountant_relation(
                db,
                owner_user_id=7,
                relation_id=21,
                expected_action=AccountantRelationDeletionAction.CANCEL_PENDING,
            )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(
            exc_info.exception.detail,
            ACCOUNTANT_RELATION_DELETE_PRECONDITION_DETAIL,
        )
        self.assertEqual(relation.status, AccountantRelationStatus.ACTIVE)
        self.assertIsNone(relation.deleted_at)
        release_mock.assert_not_awaited()
        delete_mock.assert_not_awaited()
        db.commit.assert_not_awaited()
        db.refresh.assert_not_awaited()

    async def test_unlink_owner_accountant_relation_rejects_orphan_to_live_escalation_before_effects(self):
        invitation_token = f"{ACCOUNTANT_INVITATION_PREFIX}orphan-became-live"
        accountant_user = SimpleNamespace(id=79, is_deleted=False)
        relation = SimpleNamespace(
            id=22,
            owner_user_id=7,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
            invitation_token=invitation_token,
            accountant_user_id=79,
            accountant_user=accountant_user,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=invitation_token),
                FakeExecuteResult(scalar_one_value=relation),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.accountant_relation_service.release_invitation_identities_for_tokens",
            new=AsyncMock(),
        ) as release_mock, patch(
            "core.services.user_deletion_service.delete_user_account",
            new=AsyncMock(),
        ) as delete_mock, self.assertRaises(HTTPException) as exc_info:
            await unlink_owner_accountant_relation(
                db,
                owner_user_id=7,
                relation_id=22,
                expected_action=AccountantRelationDeletionAction.DELETE_RELATION,
            )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(
            exc_info.exception.detail,
            ACCOUNTANT_RELATION_DELETE_PRECONDITION_DETAIL,
        )
        release_mock.assert_not_awaited()
        delete_mock.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_unlink_owner_accountant_relation_rejects_delete_account_for_orphan(self):
        invitation_token = f"{ACCOUNTANT_INVITATION_PREFIX}account-became-orphan"
        relation = SimpleNamespace(
            id=23,
            owner_user_id=7,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
            invitation_token=invitation_token,
            accountant_user_id=None,
            accountant_user=None,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=invitation_token),
                FakeExecuteResult(scalar_one_value=relation),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.user_deletion_service.delete_user_account",
            new=AsyncMock(),
        ) as delete_mock, self.assertRaises(HTTPException) as exc_info:
            await unlink_owner_accountant_relation(
                db,
                owner_user_id=7,
                relation_id=23,
                expected_action=AccountantRelationDeletionAction.DELETE_ACCOUNT,
            )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(
            exc_info.exception.detail,
            ACCOUNTANT_RELATION_DELETE_PRECONDITION_DETAIL,
        )
        delete_mock.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_unlink_owner_accountant_relation_rejects_closed_relation_status(self):
        invitation_token = f"{ACCOUNTANT_INVITATION_PREFIX}closed"
        relation = SimpleNamespace(
            id=24,
            owner_user_id=7,
            status=AccountantRelationStatus.REVOKED,
            deleted_at=datetime.utcnow(),
            invitation_token=invitation_token,
            accountant_user_id=None,
            accountant_user=None,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=invitation_token),
                FakeExecuteResult(scalar_one_value=relation),
            ]
        )

        with patch(
            "core.services.accountant_relation_service.lock_invitation_for_transition",
            new=AsyncMock(return_value=None),
        ), self.assertRaises(HTTPException) as exc_info:
            await unlink_owner_accountant_relation(
                db,
                owner_user_id=7,
                relation_id=24,
                expected_action=AccountantRelationDeletionAction.DELETE_RELATION,
            )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(
            exc_info.exception.detail,
            ACCOUNTANT_RELATION_DELETE_PRECONDITION_DETAIL,
        )

    async def test_update_owner_accountant_relation_updates_only_duty_description(self):
        relation = SimpleNamespace(
            id=4,
            owner_user_id=7,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
            relation_display_name="حسابدار اول",
            duty_description="پیگیری",
        )
        refreshed_relation = SimpleNamespace(
            id=4,
            owner_user_id=7,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
            relation_display_name="حسابدار اول",
            duty_description="گزارش‌گیری",
            accountant_user=SimpleNamespace(account_name="acc-user"),
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=relation),
                FakeExecuteResult(scalar_one_value=refreshed_relation),
            ]
        )

        result = await update_owner_accountant_relation(
            db,
            owner_user_id=7,
            relation_id=4,
            duty_description="گزارش‌گیری",
        )

        self.assertIs(result, refreshed_relation)
        self.assertEqual(relation.relation_display_name, "حسابدار اول")
        self.assertEqual(relation.duty_description, "گزارش‌گیری")
        self.assertEqual(result.accountant_user.account_name, "acc-user")
        db.commit.assert_awaited_once()
        db.refresh.assert_not_awaited()

    async def test_update_owner_accountant_relation_rejects_inactive_relation(self):
        with self.assertRaises(HTTPException) as exc_info:
            await update_owner_accountant_relation(
                FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=None)]),
                owner_user_id=7,
                relation_id=4,
                duty_description=None,
            )
        self.assertEqual(exc_info.exception.status_code, 404)

        inactive_relation = SimpleNamespace(
            id=4,
            owner_user_id=7,
            status=AccountantRelationStatus.REVOKED,
            deleted_at=datetime.utcnow(),
            relation_display_name="حسابدار اول",
            duty_description=None,
        )
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=inactive_relation)])

        with self.assertRaises(HTTPException) as exc_info:
            await update_owner_accountant_relation(
                db,
                owner_user_id=7,
                relation_id=4,
                duty_description=None,
            )
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_resolve_effective_owner_actor_returns_self_context_without_relation(self):
        user = SimpleNamespace(id=10)

        with patch(
            "core.services.accountant_relation_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ):
            context = await resolve_effective_owner_actor(FakeDB(), user)

        self.assertIs(context.owner_user, user)
        self.assertIs(context.actor_user, user)
        self.assertIsNone(context.relation)
        self.assertFalse(context.is_accountant_context)

    async def test_resolve_effective_owner_actor_returns_owner_context_for_accountant(self):
        owner = SimpleNamespace(id=2)
        actor = SimpleNamespace(id=7)
        relation = SimpleNamespace(owner_user=owner)

        with patch(
            "core.services.accountant_relation_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=relation),
        ):
            context = await resolve_effective_owner_actor(FakeDB(), actor)

        self.assertIs(context.owner_user, owner)
        self.assertIs(context.actor_user, actor)
        self.assertIs(context.relation, relation)
        self.assertTrue(context.is_accountant_context)


if __name__ == "__main__":
    unittest.main()
