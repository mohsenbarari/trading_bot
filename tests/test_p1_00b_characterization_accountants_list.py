"""Hermetic direct characterization for list_my_accountants endpoint.

Seed: API-IDENTITY-GET-ACCOUNTANTS-LIST-MY-ACCOUNTANTS-L167
Target: api.routers.accountants.list_my_accountants

Note: This unit provides direct characterization for the currently
unexecuted seed API-IDENTITY-GET-ACCOUNTANTS-LIST-MY-ACCOUNTANTS-L167
under P1-00b network isolation. It does NOT add this seed to
EXECUTED_SEED_IDS or claim that the canonical 330 remaining-seed blocker
has been reduced; evidence promotion is reserved for a separate later unit.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException

from api.routers.accountants import list_my_accountants
from models.accountant_relation import AccountantRelationStatus


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self, invitations: list[Any] | None = None) -> None:
        self.invitations = invitations or []
        self.executed_tokens: list[str] = []
        self._result_mock = mock.MagicMock()
        self._result_mock.scalars.return_value.all.return_value = self.invitations
        self.execute = mock.AsyncMock(side_effect=self._execute)

    async def _execute(self, stmt: Any) -> Any:
        whereclause = getattr(stmt, "whereclause", None)
        right = getattr(whereclause, "right", None)
        val = getattr(right, "value", None)
        if isinstance(val, (list, tuple, set)):
            self.executed_tokens.extend(val)
        elif val is not None:
            self.executed_tokens.append(val)
        return self._result_mock


class P100bAccountantsListCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for API-IDENTITY-GET-ACCOUNTANTS-LIST-MY-ACCOUNTANTS-L167."""

    async def test_list_my_accountants_serializes_invitation_and_sms_status(self) -> None:
        owner_id = 42
        matching_token = "tok-pending-001"
        invitation_id = 101
        invitation_short_code = "INV12345"

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )

        relation_pending = mock.Mock(
            id=1,
            owner_user_id=owner_id,
            accountant_user_id=None,
            accountant_user=None,
            global_account_name="pending_acct",
            relation_display_name="Pending Accountant",
            duty_description="Tax Filings",
            mobile_number="+989120000001",
            status=AccountantRelationStatus.PENDING,
            invitation_token=matching_token,
            expires_at=None,
            activated_at=None,
            deleted_at=None,
            created_at=None,
        )

        relation_active = mock.Mock(
            id=2,
            owner_user_id=owner_id,
            accountant_user_id=88,
            accountant_user=mock.Mock(account_name="active_acct"),
            global_account_name="active_acct",
            relation_display_name="Active Accountant",
            duty_description="Bookkeeping",
            mobile_number="+989120000002",
            status=AccountantRelationStatus.ACTIVE,
            invitation_token=None,
            expires_at=None,
            activated_at=None,
            deleted_at=None,
            created_at=None,
        )

        invitation = mock.Mock(
            id=invitation_id,
            token=matching_token,
            short_code=invitation_short_code,
        )

        fake_db = FakeAsyncSession(invitations=[invitation])

        with mock.patch(
            "api.routers.accountants.list_owner_accountant_relations",
            new_callable=mock.AsyncMock,
            return_value=[relation_pending, relation_active],
        ) as mock_list_relations, mock.patch(
            "api.routers.accountants.load_invitation_sms_status_map",
            new_callable=mock.AsyncMock,
            return_value={invitation_id: "delivered"},
        ) as mock_load_sms, mock.patch(
            "api.routers.accountants.public_webapp_url_for_links",
            return_value="https://webapp.test",
        ), mock.patch(
            "api.routers.accountants.build_canonical_invitation_web_link",
            side_effect=lambda code, web_origin: f"{web_origin}/invite/{code}",
        ), mock.patch(
            "api.routers.accountants.is_canonical_invitation_short_code",
            return_value=True,
        ):
            results = await list_my_accountants(context=context, db=fake_db)

        mock_list_relations.assert_awaited_once_with(fake_db, owner_user_id=owner_id)

        fake_db.execute.assert_awaited_once()
        executed_stmt = fake_db.execute.call_args[0][0]
        selected_tokens = list(getattr(getattr(getattr(executed_stmt, "whereclause", None), "right", None), "value", []))
        self.assertEqual(selected_tokens, [matching_token])
        self.assertEqual(fake_db.executed_tokens, [matching_token])

        mock_load_sms.assert_awaited_once_with(fake_db, [invitation_id])

        self.assertEqual(len(results), 2)
        pending_result, active_result = results[0], results[1]

        self.assertEqual(pending_result["id"], 1)
        self.assertEqual(pending_result["owner_user_id"], owner_id)
        self.assertEqual(pending_result["status"], AccountantRelationStatus.PENDING)
        self.assertEqual(pending_result["short_code"], invitation_short_code)
        self.assertIsNotNone(pending_result["registration_link"])
        self.assertEqual(
            pending_result["registration_link"],
            f"https://webapp.test/invite/{invitation_short_code}",
        )
        self.assertEqual(pending_result["sms_status"], "delivered")

        self.assertEqual(active_result["id"], 2)
        self.assertEqual(active_result["owner_user_id"], owner_id)
        self.assertEqual(active_result["status"], AccountantRelationStatus.ACTIVE)
        self.assertIsNone(active_result["short_code"])
        self.assertIsNone(active_result["registration_link"])
        self.assertIsNone(active_result["sms_status"])

    async def test_list_my_accountants_denial_stops_before_reads(self) -> None:
        context = mock.Mock(
            is_accountant_context=True,
            actor_user=None,
            owner_user=mock.Mock(id=42, is_customer=False),
        )
        fake_db = FakeAsyncSession()

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
            side_effect=HTTPException(
                status_code=403,
                detail="Accountants cannot manage owner accountants",
            ),
        ) as mock_guard, mock.patch(
            "api.routers.accountants.list_owner_accountant_relations",
            new_callable=mock.AsyncMock,
        ) as mock_list_relations, mock.patch(
            "api.routers.accountants.load_invitation_sms_status_map",
            new_callable=mock.AsyncMock,
        ) as mock_sms_map:
            with self.assertRaises(HTTPException) as ctx:
                await list_my_accountants(context=context, db=fake_db)

            self.assertEqual(ctx.exception.status_code, 403)
            mock_guard.assert_awaited_once_with(context, fake_db)
            mock_list_relations.assert_not_called()
            fake_db.execute.assert_not_called()
            mock_sms_map.assert_not_called()


if __name__ == "__main__":
    unittest.main()
