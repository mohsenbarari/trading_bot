import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.invitation_transition_lock_service import (
    lock_invitation_for_transition,
    lock_invitation_row_for_transition,
    normalized_invitation_identity_or_none,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, *values):
        self.values = list(values)
        self.execute = AsyncMock(side_effect=lambda *_args, **_kwargs: _Result(self.values.pop(0)))


class InvitationTransitionLockServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_row_lock_requires_exactly_one_invitation_identity(self):
        for kwargs in ({}, {"invitation_id": 1, "invitation_token": "INV-one"}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(
                ValueError,
                "exactly_one_invitation_identity_required",
            ):
                await lock_invitation_row_for_transition(_DB(), **kwargs)

    def test_invalid_invitation_identity_is_not_used_for_advisory_locking(self):
        invitation = SimpleNamespace(mobile_number="invalid", account_name="")
        self.assertIsNone(normalized_invitation_identity_or_none(invitation))

    async def test_missing_id_probe_continues_to_authoritative_row_lock(self):
        db = _DB(None, None)
        result = await lock_invitation_for_transition(db, invitation_id=7)
        self.assertIsNone(result)
        self.assertEqual(db.execute.await_count, 2)

    async def test_identity_change_between_probe_and_row_lock_fails_closed(self):
        probe = SimpleNamespace(
            token="INV-one",
            account_name="target-one",
            mobile_number="09120000001",
        )
        changed = SimpleNamespace(
            token="INV-one",
            account_name="target-two",
            mobile_number="09120000002",
        )
        db = _DB(probe, changed)
        with patch(
            "core.services.invitation_transition_lock_service.acquire_locked_invitation_transition_locks",
            new=AsyncMock(),
        ), self.assertRaisesRegex(
            RuntimeError,
            "invitation_identity_changed_during_transition_lock",
        ):
            await lock_invitation_for_transition(db, invitation_token="INV-one")

    async def test_stable_identity_returns_locked_invitation(self):
        invitation = SimpleNamespace(
            token="INV-one",
            account_name="target-one",
            mobile_number="09120000001",
        )
        db = _DB(invitation, invitation)
        with patch(
            "core.services.invitation_transition_lock_service.acquire_locked_invitation_transition_locks",
            new=AsyncMock(),
        ):
            result = await lock_invitation_for_transition(db, invitation_token="INV-one")
        self.assertIs(result, invitation)


if __name__ == "__main__":
    unittest.main()
