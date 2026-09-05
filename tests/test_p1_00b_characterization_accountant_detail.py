"""Hermetic direct characterization for get_my_accountant endpoint.

Seed: API-IDENTITY-GET-ACCOUNTANTS-GET-MY-ACCOUNTANT-L199
Target: api.routers.accountants.get_my_accountant

Note: This unit provides direct characterization for the currently
unexecuted seed API-IDENTITY-GET-ACCOUNTANTS-GET-MY-ACCOUNTANT-L199
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

from api.routers.accountants import (
    get_my_accountant,
    serialize_accountant_relation,
)
from models.accountant_relation import AccountantRelationStatus


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self, invitation: Any | None = None) -> None:
        self.invitation = invitation
        self.executed_tokens: list[str] = []
        self._result_mock = mock.MagicMock()
        self._result_mock.scalar_one_or_none.return_value = self.invitation
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


class P100bAccountantDetailCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for API-IDENTITY-GET-ACCOUNTANTS-GET-MY-ACCOUNTANT-L199."""

    async def test_get_my_accountant_serializes_matching_invitation_and_sms_status(self) -> None:
        owner_id = 42
        relation_id = 7
        matching_token = "tok-pending-detail-001"
        invitation_id = 202
        invitation_short_code = "INV98765"

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )

        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=None,
            accountant_user=None,
            global_account_name="detail_acct",
            relation_display_name="Detail Accountant",
            duty_description="Audit Prep",
            mobile_number="+989120000003",
            status=AccountantRelationStatus.PENDING,
            invitation_token=matching_token,
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

        fake_db = FakeAsyncSession(invitation=invitation)

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=relation,
        ) as mock_get_relation, mock.patch(
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
            result = await get_my_accountant(
                relation_id=relation_id,
                context=context,
                db=fake_db,
            )

        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        fake_db.execute.assert_awaited_once()
        executed_stmt = fake_db.execute.call_args[0][0]
        selected_token = getattr(
            getattr(getattr(executed_stmt, "whereclause", None), "right", None),
            "value",
            None,
        )
        self.assertEqual(selected_token, matching_token)
        self.assertEqual(fake_db.executed_tokens, [matching_token])

        mock_load_sms.assert_awaited_once_with(fake_db, [invitation_id])

        self.assertEqual(result["id"], relation_id)
        self.assertEqual(result["owner_user_id"], owner_id)
        self.assertEqual(result["status"], AccountantRelationStatus.PENDING)
        self.assertEqual(result["short_code"], invitation_short_code)
        self.assertEqual(
            result["registration_link"],
            f"https://webapp.test/invite/{invitation_short_code}",
        )
        self.assertEqual(result["web_registration_link"], result["registration_link"])
        self.assertEqual(result["web_short_link"], result["registration_link"])
        self.assertIsNone(result["bot_registration_link"])
        self.assertEqual(result["sms_status"], "delivered")
        self.assertEqual(result["global_account_name"], "detail_acct")
        self.assertEqual(result["relation_display_name"], "Detail Accountant")
        self.assertEqual(result["duty_description"], "Audit Prep")
        self.assertEqual(result["mobile_number"], "+989120000003")

    async def test_get_my_accountant_no_invitation_branch(self) -> None:
        owner_id = 42
        relation_id = 8
        token_with_no_invitation = "tok-missing-002"

        context = mock.Mock(
            is_accountant_context=False,
            actor_user=None,
            owner_user=mock.Mock(id=owner_id, is_customer=False),
        )

        relation = mock.Mock(
            id=relation_id,
            owner_user_id=owner_id,
            accountant_user_id=99,
            accountant_user=mock.Mock(account_name="active_user"),
            global_account_name="active_acct",
            relation_display_name="Active Accountant",
            duty_description="Payroll",
            mobile_number="+989120000004",
            status=AccountantRelationStatus.ACTIVE,
            invitation_token=token_with_no_invitation,
            expires_at=None,
            activated_at=None,
            deleted_at=None,
            created_at=None,
        )

        fake_db = FakeAsyncSession(invitation=None)

        with mock.patch(
            "api.routers.accountants.ensure_owner_context",
            new_callable=mock.AsyncMock,
        ) as mock_guard, mock.patch(
            "api.routers.accountants.get_owner_accountant_relation",
            new_callable=mock.AsyncMock,
            return_value=relation,
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.load_invitation_sms_status_map",
            new_callable=mock.AsyncMock,
        ) as mock_load_sms, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
            wraps=serialize_accountant_relation,
        ) as mock_serialize:
            result = await get_my_accountant(
                relation_id=relation_id,
                context=context,
                db=fake_db,
            )

        mock_guard.assert_awaited_once_with(context, fake_db)
        mock_get_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_id,
            relation_id=relation_id,
        )
        fake_db.execute.assert_awaited_once()
        self.assertEqual(fake_db.executed_tokens, [token_with_no_invitation])

        mock_load_sms.assert_not_called()
        mock_serialize.assert_called_once_with(
            relation,
            invitation=None,
            sms_status=None,
        )

        self.assertEqual(result["id"], relation_id)
        self.assertEqual(result["owner_user_id"], owner_id)
        self.assertEqual(result["status"], AccountantRelationStatus.ACTIVE)
        self.assertIsNone(result["short_code"])
        self.assertIsNone(result["registration_link"])
        self.assertIsNone(result["sms_status"])

    async def test_get_my_accountant_denial_stops_before_reads(self) -> None:
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
            "api.routers.accountants.get_owner_accountant_relation",
            new_callable=mock.AsyncMock,
        ) as mock_get_relation, mock.patch(
            "api.routers.accountants.load_invitation_sms_status_map",
            new_callable=mock.AsyncMock,
        ) as mock_sms_map, mock.patch(
            "api.routers.accountants.serialize_accountant_relation",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as ctx:
                await get_my_accountant(
                    relation_id=1,
                    context=context,
                    db=fake_db,
                )

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, "Accountants cannot manage owner accountants")
            mock_guard.assert_awaited_once_with(context, fake_db)
            mock_get_relation.assert_not_called()
            fake_db.execute.assert_not_called()
            mock_sms_map.assert_not_called()
            mock_serialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
