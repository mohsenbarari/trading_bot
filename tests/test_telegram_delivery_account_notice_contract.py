import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from core.telegram_delivery_account_notice_contract import (
    RESTRICTION_KIND_BLOCK,
    RESTRICTION_KIND_LIMITATIONS,
    active_restriction_snapshot_matches_user,
    build_active_restriction_snapshot,
    build_deleted_account_snapshot,
    deleted_account_snapshot_matches_user,
    validate_active_restriction_snapshot,
    validate_deleted_account_snapshot,
)


class TelegramDeliveryAccountNoticeContractTests(unittest.TestCase):
    def test_block_snapshot_is_canonical_and_rejects_state_change(self):
        user = SimpleNamespace(
            trading_restricted_until=datetime(2026, 7, 20, 10, 30),
        )
        snapshot = build_active_restriction_snapshot(
            user,
            restriction_kind=RESTRICTION_KIND_BLOCK,
        )

        self.assertEqual(
            snapshot,
            {"trading_restricted_until": "2026-07-20T10:30:00+00:00"},
        )
        self.assertEqual(
            validate_active_restriction_snapshot(
                snapshot,
                restriction_kind=RESTRICTION_KIND_BLOCK,
            ),
            snapshot,
        )
        self.assertTrue(
            active_restriction_snapshot_matches_user(
                snapshot,
                user,
                restriction_kind=RESTRICTION_KIND_BLOCK,
                now=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )
        )
        user.trading_restricted_until = datetime(2026, 7, 21, tzinfo=timezone.utc)
        self.assertFalse(
            active_restriction_snapshot_matches_user(
                snapshot,
                user,
                restriction_kind=RESTRICTION_KIND_BLOCK,
                now=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )
        )

    def test_limitation_snapshot_requires_an_active_limit(self):
        user = SimpleNamespace(
            max_daily_trades=4,
            max_active_commodities=None,
            max_daily_requests=8,
            limitations_expire_at=None,
        )
        snapshot = build_active_restriction_snapshot(
            user,
            restriction_kind=RESTRICTION_KIND_LIMITATIONS,
        )

        self.assertEqual(
            snapshot,
            {
                "max_daily_trades": 4,
                "max_active_commodities": None,
                "max_daily_requests": 8,
                "limitations_expire_at": None,
            },
        )
        self.assertTrue(
            active_restriction_snapshot_matches_user(
                snapshot,
                user,
                restriction_kind=RESTRICTION_KIND_LIMITATIONS,
                now=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )
        )
        user.max_daily_requests = None
        self.assertFalse(
            active_restriction_snapshot_matches_user(
                snapshot,
                user,
                restriction_kind=RESTRICTION_KIND_LIMITATIONS,
                now=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )
        )

        expiring_user = SimpleNamespace(
            max_daily_trades=4,
            max_active_commodities=None,
            max_daily_requests=8,
            limitations_expire_at=datetime(
                2026,
                7,
                18,
                12,
                tzinfo=timezone.utc,
            ),
        )
        expiring_snapshot = build_active_restriction_snapshot(
            expiring_user,
            restriction_kind=RESTRICTION_KIND_LIMITATIONS,
        )
        self.assertFalse(
            active_restriction_snapshot_matches_user(
                expiring_snapshot,
                expiring_user,
                restriction_kind=RESTRICTION_KIND_LIMITATIONS,
                now=datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
            )
        )

        empty_user = SimpleNamespace(
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            limitations_expire_at=None,
        )
        with self.assertRaisesRegex(ValueError, "limitations_empty"):
            build_active_restriction_snapshot(
                empty_user,
                restriction_kind=RESTRICTION_KIND_LIMITATIONS,
            )

    def test_deleted_snapshot_requires_deleted_user_and_cleared_route(self):
        user = SimpleNamespace(
            is_deleted=True,
            deleted_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
            telegram_id=None,
        )
        snapshot = build_deleted_account_snapshot(user)

        self.assertEqual(
            snapshot,
            {"deleted_at": "2026-07-18T12:00:00+00:00"},
        )
        self.assertEqual(validate_deleted_account_snapshot(snapshot), snapshot)
        self.assertTrue(deleted_account_snapshot_matches_user(snapshot, user))

        user.telegram_id = 7007
        self.assertFalse(deleted_account_snapshot_matches_user(snapshot, user))
        with self.assertRaisesRegex(ValueError, "route_not_cleared"):
            build_deleted_account_snapshot(user)


if __name__ == "__main__":
    unittest.main()
