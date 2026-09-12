"""Hermetic direct characterization for delete_pending_invitation handler.

Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-DELETE-INVITATIONS-DELETE-PENDING-INVITATION-L294
Target: api.routers.invitations.delete_pending_invitation

Note: This unit provides direct characterization for seed
API-IDENTITY-DELETE-INVITATIONS-DELETE-PENDING-INVITATION-L294 and the actual
api.routers.invitations.delete_pending_invitation handler under P1-00b network isolation.
This unit does not promote inventory evidence, change remaining-seed counts, or close P1-00b.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException
from api.routers.invitations import delete_pending_invitation
from models.invitation import Invitation
from models.user import User, UserRole


class SentinelAsyncSession:
    """Sentinel database session for direct handler characterization."""

    async def commit(self) -> None:
        raise NotImplementedError


class P100bDeletePendingInvitationCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic handler-level characterization tests for delete_pending_invitation."""

    async def test_delete_pending_invitation_non_super_admin_success(self) -> None:
        fixed_now = datetime(2026, 9, 12, 12, 0, 0)
        admin_id = 42
        invitation_id = 101
        admin = mock.Mock(spec=User, id=admin_id, role=UserRole.MIDDLE_MANAGER)
        invitation = mock.Mock(
            spec=Invitation,
            id=invitation_id,
            token="INV-TEST-101",
            created_by_id=admin_id,
            is_used=False,
            revoked_at=None,
            expires_at=datetime(2026, 9, 13, 12, 0, 0),
        )

        call_log: list[str] = []

        fake_db = mock.AsyncMock(spec=SentinelAsyncSession)

        async def fake_commit() -> None:
            call_log.append("db.commit")

        fake_db.commit.side_effect = fake_commit

        async def fake_lock(db: Any, *, invitation_id: int) -> Any:
            call_log.append("lock_invitation_for_transition")
            return invitation

        mock_lock = mock.AsyncMock(side_effect=fake_lock)

        def fake_soft_revoke(inv: Any) -> None:
            call_log.append("soft_revoke_invitation")

        mock_soft_revoke = mock.Mock(side_effect=fake_soft_revoke)

        async def fake_release_identity(db: Any, *, invitation_id: int) -> None:
            call_log.append("release_invitation_identity")

        mock_release_identity = mock.AsyncMock(side_effect=fake_release_identity)

        with mock.patch(
            "api.routers.invitations.lock_invitation_for_transition",
            new=mock_lock,
        ), mock.patch(
            "api.routers.invitations.utc_now_naive",
            return_value=fixed_now,
        ) as mock_utc_now, mock.patch(
            "api.routers.invitations.soft_revoke_invitation",
            new=mock_soft_revoke,
        ), mock.patch(
            "api.routers.invitations.release_invitation_identity",
            new=mock_release_identity,
        ):
            result = await delete_pending_invitation(
                invitation_id=invitation_id,
                db=fake_db,
                admin=admin,
            )

        self.assertIsNone(result)
        mock_lock.assert_awaited_once_with(fake_db, invitation_id=invitation_id)
        self.assertEqual(mock_lock.await_count, 1)
        mock_utc_now.assert_called_once_with()
        self.assertEqual(mock_utc_now.call_count, 1)
        mock_soft_revoke.assert_called_once_with(invitation)
        self.assertEqual(mock_soft_revoke.call_count, 1)
        mock_release_identity.assert_awaited_once_with(
            fake_db,
            invitation_id=invitation_id,
        )
        self.assertEqual(mock_release_identity.await_count, 1)
        fake_db.commit.assert_awaited_once_with()
        self.assertEqual(fake_db.commit.await_count, 1)

        # Exact lock then revoke then release then commit ordering
        self.assertEqual(
            call_log,
            [
                "lock_invitation_for_transition",
                "soft_revoke_invitation",
                "release_invitation_identity",
                "db.commit",
            ],
        )

    async def test_delete_pending_invitation_not_found_boundary(self) -> None:
        admin_id = 42
        admin = mock.Mock(spec=User, id=admin_id, role=UserRole.MIDDLE_MANAGER)

        cases = [
            (
                "lock_returns_no_invitation",
                None,
                admin,
                301,
            ),
            (
                "locked_token_not_inv_prefixed",
                mock.Mock(
                    spec=Invitation,
                    id=302,
                    token="NON-INV-TOKEN",
                    created_by_id=admin_id,
                    is_used=False,
                    revoked_at=None,
                    expires_at=datetime(2026, 9, 13, 12, 0, 0),
                ),
                admin,
                302,
            ),
            (
                "non_super_admin_does_not_own_invitation",
                mock.Mock(
                    spec=Invitation,
                    id=303,
                    token="INV-VALID-TOKEN",
                    created_by_id=99,
                    is_used=False,
                    revoked_at=None,
                    expires_at=datetime(2026, 9, 13, 12, 0, 0),
                ),
                admin,
                303,
            ),
        ]

        for name, locked_invitation, admin_user, invitation_id in cases:
            with self.subTest(case=name):
                fake_db = mock.AsyncMock(spec=SentinelAsyncSession)
                mock_lock = mock.AsyncMock(return_value=locked_invitation)
                mock_soft_revoke = mock.Mock()
                mock_release_identity = mock.AsyncMock()

                with mock.patch(
                    "api.routers.invitations.lock_invitation_for_transition",
                    new=mock_lock,
                ), mock.patch(
                    "api.routers.invitations.utc_now_naive",
                ) as mock_utc_now, mock.patch(
                    "api.routers.invitations.soft_revoke_invitation",
                    new=mock_soft_revoke,
                ), mock.patch(
                    "api.routers.invitations.release_invitation_identity",
                    new=mock_release_identity,
                ):
                    with self.assertRaises(HTTPException) as ctx:
                        await delete_pending_invitation(
                            invitation_id=invitation_id,
                            db=fake_db,
                            admin=admin_user,
                        )

                self.assertEqual(ctx.exception.status_code, 404)
                self.assertEqual(ctx.exception.detail, "دعوت\u200cنامه پیدا نشد")
                mock_lock.assert_awaited_once_with(fake_db, invitation_id=invitation_id)
                self.assertEqual(mock_lock.await_count, 1)
                mock_utc_now.assert_not_called()
                mock_soft_revoke.assert_not_called()
                mock_release_identity.assert_not_called()
                fake_db.commit.assert_not_called()

    async def test_delete_pending_invitation_non_pending_boundary(self) -> None:
        fixed_now = datetime(2026, 9, 12, 12, 0, 0)
        admin_id = 42
        admin = mock.Mock(spec=User, id=admin_id, role=UserRole.MIDDLE_MANAGER)

        cases = [
            (
                "used_invitation",
                mock.Mock(
                    spec=Invitation,
                    id=401,
                    token="INV-USED",
                    created_by_id=admin_id,
                    is_used=True,
                    revoked_at=None,
                    expires_at=fixed_now + timedelta(days=1),
                ),
                401,
                0,
            ),
            (
                "already_revoked_invitation",
                mock.Mock(
                    spec=Invitation,
                    id=402,
                    token="INV-REVOKED",
                    created_by_id=admin_id,
                    is_used=False,
                    revoked_at=datetime(2026, 9, 10, 12, 0, 0),
                    expires_at=fixed_now + timedelta(days=1),
                ),
                402,
                0,
            ),
            (
                "expiry_boundary_invitation",
                mock.Mock(
                    spec=Invitation,
                    id=403,
                    token="INV-EXPIRED",
                    created_by_id=admin_id,
                    is_used=False,
                    revoked_at=None,
                    expires_at=fixed_now,
                ),
                403,
                1,
            ),
        ]

        for name, locked_invitation, invitation_id, expected_time_calls in cases:
            with self.subTest(case=name):
                fake_db = mock.AsyncMock(spec=SentinelAsyncSession)
                mock_lock = mock.AsyncMock(return_value=locked_invitation)
                mock_soft_revoke = mock.Mock()
                mock_release_identity = mock.AsyncMock()

                with mock.patch(
                    "api.routers.invitations.lock_invitation_for_transition",
                    new=mock_lock,
                ), mock.patch(
                    "api.routers.invitations.utc_now_naive",
                    return_value=fixed_now,
                ) as mock_utc_now, mock.patch(
                    "api.routers.invitations.soft_revoke_invitation",
                    new=mock_soft_revoke,
                ), mock.patch(
                    "api.routers.invitations.release_invitation_identity",
                    new=mock_release_identity,
                ):
                    with self.assertRaises(HTTPException) as ctx:
                        await delete_pending_invitation(
                            invitation_id=invitation_id,
                            db=fake_db,
                            admin=admin,
                        )

                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "دعوت\u200cنامه pending نیست")
                mock_lock.assert_awaited_once_with(fake_db, invitation_id=invitation_id)
                self.assertEqual(mock_lock.await_count, 1)

                if expected_time_calls == 0:
                    mock_utc_now.assert_not_called()
                else:
                    mock_utc_now.assert_called_once_with()
                    self.assertEqual(mock_utc_now.call_count, 1)

                mock_soft_revoke.assert_not_called()
                mock_release_identity.assert_not_called()
                fake_db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
