"""Hermetic direct characterization for list_pending_invitations query and failure boundaries.

Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-INVITATIONS-LIST-PENDING-INVITATIONS-L266
Target: api.routers.invitations.list_pending_invitations

Note: This unit provides direct characterization for query construction, owner filtering,
SMS status mapping, and failure boundaries (SMS status lookup failure and first-invitation
serialization failure) of seed API-IDENTITY-GET-INVITATIONS-LIST-PENDING-INVITATIONS-L266
under P1-00b network isolation. It does NOT promote inventory evidence, reduce canonical
remaining-seed counts, or close P1-00b; evidence promotion is a separate unit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from sqlalchemy.sql import Select

from api.routers.invitations import (
    list_pending_invitations,
    pending_invitation_select,
)
from core.registration_contracts import InvitationSMSStatus
from models.invitation import Invitation, InvitationKind
from models.user import User, UserRole


class SentinelAsyncSession:
    """Sentinel database session for direct handler characterization."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class SMSStatusMapLookupSentinelError(Exception):
    """Unique sentinel exception for SMS-status-map lookup failure."""


class FirstInvitationSerializationSentinelError(Exception):
    """Unique sentinel exception for first-invitation serialization failure."""


class P100bListPendingInvitationsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic handler-level characterization tests for list_pending_invitations."""

    async def test_list_pending_invitations_non_super_admin_success(self) -> None:
        fixed_now = datetime(2026, 9, 12, 12, 0, 0)
        admin_id = 42
        admin = mock.Mock(spec=User, id=admin_id, role=UserRole.MIDDLE_MANAGER)

        invitation_1 = mock.Mock(spec=Invitation, id=101)
        invitation_2 = mock.Mock(spec=Invitation, id=102)

        call_log: list[str] = []

        fake_db = mock.AsyncMock(spec=SentinelAsyncSession)
        mock_result = mock.Mock()
        mock_scalars = mock.Mock()
        mock_scalars.all.return_value = [invitation_1, invitation_2]
        mock_result.scalars.return_value = mock_scalars

        async def fake_execute(stmt: Any) -> Any:
            call_log.append("db.execute")
            return mock_result

        fake_db.execute.side_effect = fake_execute

        fake_sms_map = {101: InvitationSMSStatus.ACCEPTED}

        async def fake_load_sms(session: Any, ids: Any) -> Any:
            call_log.append("load_invitation_sms_status_map")
            return fake_sms_map

        mock_load_sms = mock.AsyncMock(side_effect=fake_load_sms)

        serialized_1 = {
            "id": 101,
            "account_name": "user1",
            "mobile_number": "+989121111111",
            "role": UserRole.STANDARD,
            "short_code": "CODE1111",
            "bot_link": None,
            "web_link": "https://example.com/w/CODE1111",
            "web_short_link": "https://example.com/s/CODE1111",
            "short_link": "https://example.com/s/CODE1111",
            "bot_available": False,
            "web_available": True,
            "state": "pending",
            "kind": "standard",
            "sms_status": InvitationSMSStatus.ACCEPTED,
            "expires_at": datetime(2026, 9, 15, 12, 0, 0),
            "created_at": datetime(2026, 9, 10, 12, 0, 0),
            "created_by_id": admin_id,
        }
        serialized_2 = {
            "id": 102,
            "account_name": "user2",
            "mobile_number": "+989122222222",
            "role": UserRole.STANDARD,
            "short_code": "CODE2222",
            "bot_link": None,
            "web_link": "https://example.com/w/CODE2222",
            "web_short_link": "https://example.com/s/CODE2222",
            "short_link": "https://example.com/s/CODE2222",
            "bot_available": False,
            "web_available": True,
            "state": "pending",
            "kind": "standard",
            "sms_status": InvitationSMSStatus.AMBIGUOUS,
            "expires_at": datetime(2026, 9, 16, 12, 0, 0),
            "created_at": datetime(2026, 9, 9, 12, 0, 0),
            "created_by_id": admin_id,
        }

        def fake_serialize(
            invitation: Any, *, sms_status: Any = InvitationSMSStatus.AMBIGUOUS
        ) -> dict[str, Any]:
            call_log.append(f"serialize:{invitation.id}")
            if invitation.id == 101:
                return serialized_1
            return serialized_2

        mock_serialize = mock.Mock(side_effect=fake_serialize)

        with mock.patch(
            "api.routers.invitations.utc_now_naive",
            return_value=fixed_now,
        ), mock.patch(
            "api.routers.invitations.load_invitation_sms_status_map",
            new=mock_load_sms,
        ), mock.patch(
            "api.routers.invitations.serialize_pending_invitation",
            new=mock_serialize,
        ):
            result = await list_pending_invitations(db=fake_db, admin=admin)

        fake_db.execute.assert_awaited_once()
        captured_stmt = fake_db.execute.call_args[0][0]
        self.assertIsInstance(captured_stmt, Select)

        clauses = list(captured_stmt.whereclause.clauses)
        self.assertEqual(len(clauses), 6)

        # Base predicate 1: INV-% token
        self.assertEqual(clauses[0].left.name, "token")
        self.assertEqual(clauses[0].right.value, "INV-%")
        self.assertIn("LIKE", str(clauses[0]).upper())
        self.assertTrue(clauses[0].compare(Invitation.token.like("INV-%")))

        # Base predicate 2: is_used == False
        self.assertEqual(clauses[1].left.name, "is_used")
        self.assertEqual(type(clauses[1].right).__name__, "False_")
        self.assertIn("is_used = false", str(clauses[1]).lower())
        self.assertTrue(clauses[1].compare(Invitation.is_used == False))

        # Base predicate 3: revoked_at IS NULL
        self.assertEqual(clauses[2].left.name, "revoked_at")
        self.assertIn("IS NULL", str(clauses[2]).upper())
        self.assertTrue(clauses[2].compare(Invitation.revoked_at.is_(None)))

        # Base predicate 4: kind != InvitationKind.LEGACY_UNKNOWN
        self.assertEqual(clauses[3].left.name, "kind")
        self.assertIn(
            getattr(clauses[3].right, "value", str(clauses[3].right)),
            (
                InvitationKind.LEGACY_UNKNOWN,
                InvitationKind.LEGACY_UNKNOWN.value,
                "invitationkind.legacy_unknown",
            ),
        )
        self.assertIn("!=", str(clauses[3]))
        self.assertTrue(
            clauses[3].compare(Invitation.kind != InvitationKind.LEGACY_UNKNOWN)
        )

        # Base predicate 5: expires_at > fixed_now
        self.assertEqual(clauses[4].left.name, "expires_at")
        self.assertEqual(getattr(clauses[4].right, "value", None), fixed_now)
        self.assertIn(">", str(clauses[4]))
        self.assertTrue(clauses[4].compare(Invitation.expires_at > fixed_now))

        # Owner predicate: created_by_id == admin.id
        self.assertEqual(clauses[5].left.name, "created_by_id")
        self.assertEqual(getattr(clauses[5].right, "value", None), admin_id)
        self.assertTrue(clauses[5].compare(Invitation.created_by_id == admin_id))

        # Confirm alignment with pending_invitation_select base predicates
        base_stmt = pending_invitation_select(fixed_now)
        base_clauses = list(base_stmt.whereclause.clauses)
        self.assertEqual(len(base_clauses), 5)
        for idx in range(5):
            self.assertEqual(
                str(clauses[idx]),
                str(base_clauses[idx]),
            )
            self.assertTrue(clauses[idx].compare(base_clauses[idx]))

        # Ordering: created_at desc, id desc
        order_by = list(captured_stmt._order_by_clauses)
        self.assertEqual(len(order_by), 2)
        self.assertEqual(order_by[0].element.name, "created_at")
        self.assertIn("DESC", str(order_by[0]).upper())
        self.assertEqual(order_by[1].element.name, "id")
        self.assertIn("DESC", str(order_by[1]).upper())

        # Limit: 100
        limit_val = getattr(captured_stmt, "_limit", None)
        if hasattr(limit_val, "value"):
            limit_val = limit_val.value
        elif limit_val is None and hasattr(captured_stmt, "_limit_clause"):
            limit_clause = getattr(captured_stmt, "_limit_clause", None)
            limit_val = getattr(limit_clause, "value", limit_clause)
        if hasattr(limit_val, "value"):
            limit_val = limit_val.value
        self.assertEqual(int(limit_val), 100)

        # SMS-status lookup assertion
        mock_load_sms.assert_awaited_once_with(
            fake_db,
            [invitation_1.id, invitation_2.id],
        )

        # Serialization assertions
        mock_serialize.assert_has_calls(
            [
                mock.call(
                    invitation_1,
                    sms_status=InvitationSMSStatus.ACCEPTED,
                ),
                mock.call(
                    invitation_2,
                    sms_status=InvitationSMSStatus.AMBIGUOUS,
                ),
            ],
            any_order=False,
        )
        self.assertEqual(mock_serialize.call_count, 2)

        # Result order assertion
        self.assertEqual(result, [serialized_1, serialized_2])
        self.assertIs(result[0], serialized_1)
        self.assertIs(result[1], serialized_2)

        # Sequential call ordering assertion
        self.assertEqual(
            call_log,
            [
                "db.execute",
                "load_invitation_sms_status_map",
                "serialize:101",
                "serialize:102",
            ],
        )

    async def test_list_pending_invitations_sms_status_lookup_failure(
        self,
    ) -> None:
        fixed_now = datetime(2026, 9, 12, 12, 0, 0)
        admin_id = 42
        admin = mock.Mock(spec=User, id=admin_id, role=UserRole.MIDDLE_MANAGER)

        invitation_1 = mock.Mock(spec=Invitation, id=101)
        invitation_2 = mock.Mock(spec=Invitation, id=102)

        call_log: list[str] = []

        fake_db = mock.AsyncMock(spec=SentinelAsyncSession)
        mock_result = mock.Mock()
        mock_scalars = mock.Mock()
        mock_scalars.all.return_value = [invitation_1, invitation_2]
        mock_result.scalars.return_value = mock_scalars

        async def fake_execute(stmt: Any) -> Any:
            call_log.append("db.execute")
            return mock_result

        fake_db.execute.side_effect = fake_execute

        sms_lookup_error = SMSStatusMapLookupSentinelError(
            "sms-status lookup failed"
        )

        async def fake_load_sms(session: Any, ids: Any) -> Any:
            call_log.append("load_invitation_sms_status_map")
            raise sms_lookup_error

        mock_load_sms = mock.AsyncMock(side_effect=fake_load_sms)
        mock_serialize = mock.Mock()

        with mock.patch(
            "api.routers.invitations.utc_now_naive",
            return_value=fixed_now,
        ), mock.patch(
            "api.routers.invitations.load_invitation_sms_status_map",
            new=mock_load_sms,
        ), mock.patch(
            "api.routers.invitations.serialize_pending_invitation",
            new=mock_serialize,
        ):
            with self.assertRaises(SMSStatusMapLookupSentinelError) as ctx:
                await list_pending_invitations(db=fake_db, admin=admin)

        self.assertIs(ctx.exception, sms_lookup_error)
        fake_db.execute.assert_awaited_once()
        self.assertEqual(fake_db.execute.await_count, 1)
        mock_load_sms.assert_awaited_once_with(
            fake_db,
            [invitation_1.id, invitation_2.id],
        )
        self.assertEqual(mock_load_sms.await_count, 1)
        mock_serialize.assert_not_called()

        # Sequential call ordering assertion
        self.assertEqual(
            call_log,
            [
                "db.execute",
                "load_invitation_sms_status_map",
            ],
        )

    async def test_list_pending_invitations_first_invitation_serialization_failure(
        self,
    ) -> None:
        fixed_now = datetime(2026, 9, 12, 12, 0, 0)
        admin_id = 42
        admin = mock.Mock(spec=User, id=admin_id, role=UserRole.MIDDLE_MANAGER)

        invitation_1 = mock.Mock(spec=Invitation, id=101)
        invitation_2 = mock.Mock(spec=Invitation, id=102)
        self.assertIsNot(invitation_1, invitation_2)

        call_log: list[str] = []

        fake_db = mock.AsyncMock(spec=SentinelAsyncSession)
        mock_result = mock.Mock()
        mock_scalars = mock.Mock()
        mock_scalars.all.return_value = [invitation_1, invitation_2]
        mock_result.scalars.return_value = mock_scalars

        async def fake_execute(stmt: Any) -> Any:
            call_log.append("db.execute")
            return mock_result

        fake_db.execute.side_effect = fake_execute

        fake_sms_map = {
            101: InvitationSMSStatus.ACCEPTED,
            102: InvitationSMSStatus.PENDING,
        }

        async def fake_load_sms(session: Any, ids: Any) -> Any:
            call_log.append("load_invitation_sms_status_map")
            return fake_sms_map

        mock_load_sms = mock.AsyncMock(side_effect=fake_load_sms)

        serialization_error = FirstInvitationSerializationSentinelError(
            "first invitation serialization failed"
        )

        def fake_serialize(
            invitation: Any, *, sms_status: Any = InvitationSMSStatus.AMBIGUOUS
        ) -> dict[str, Any]:
            call_log.append(f"serialize:{invitation.id}")
            raise serialization_error

        mock_serialize = mock.Mock(side_effect=fake_serialize)

        with mock.patch(
            "api.routers.invitations.utc_now_naive",
            return_value=fixed_now,
        ), mock.patch(
            "api.routers.invitations.load_invitation_sms_status_map",
            new=mock_load_sms,
        ), mock.patch(
            "api.routers.invitations.serialize_pending_invitation",
            new=mock_serialize,
        ):
            with self.assertRaises(FirstInvitationSerializationSentinelError) as ctx:
                await list_pending_invitations(db=fake_db, admin=admin)

        self.assertIs(ctx.exception, serialization_error)
        mock_serialize.assert_called_once_with(
            invitation_1,
            sms_status=InvitationSMSStatus.ACCEPTED,
        )
        self.assertEqual(mock_serialize.call_count, 1)
        self.assertNotIn(
            mock.call(invitation_2, sms_status=mock.ANY),
            mock_serialize.mock_calls,
        )
        fake_db.execute.assert_awaited_once()
        self.assertEqual(fake_db.execute.await_count, 1)
        mock_load_sms.assert_awaited_once_with(
            fake_db,
            [invitation_1.id, invitation_2.id],
        )
        self.assertEqual(mock_load_sms.await_count, 1)

        # Sequential call ordering assertion
        self.assertEqual(
            call_log,
            [
                "db.execute",
                "load_invitation_sms_status_map",
                "serialize:101",
            ],
        )


if __name__ == "__main__":
    unittest.main()
