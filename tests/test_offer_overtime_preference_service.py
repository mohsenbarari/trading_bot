"""Stage 2: the canonical preference rules and the offer snapshot."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.events import build_user_sync_payload
from core.registration_sync_policy import (
    USER_SYNC_FOREIGN_FIELDS,
    allowed_user_fields_for_source,
)
from core.services.offer_overtime_preference_service import (
    INVALID_OVERTIME_VALUE_MESSAGE,
    OVERTIME_MAX_MINUTES,
    OfferOvertimePreferenceError,
    evaluate_overtime_preference_eligibility,
    normalize_overtime_minutes,
    read_persisted_overtime_minutes,
    snapshot_overtime_minutes_for_new_offer,
)
from models.customer_relation import CustomerTier


class NormalizeOvertimeMinutesTests(unittest.TestCase):
    def test_accepts_every_supported_value(self):
        for minutes in range(0, OVERTIME_MAX_MINUTES + 1):
            with self.subTest(minutes=minutes):
                self.assertEqual(normalize_overtime_minutes(minutes), minutes)

    def test_accepts_digits_typed_into_the_bot(self):
        self.assertEqual(normalize_overtime_minutes("5"), 5)
        self.assertEqual(normalize_overtime_minutes(" 7 "), 7)
        self.assertEqual(normalize_overtime_minutes("۵"), 5)
        self.assertEqual(normalize_overtime_minutes("١٠"), 10)

    def test_rejects_values_outside_the_range(self):
        for value in (-1, 11, 60, "11", "۱۱"):
            with self.subTest(value=value):
                with self.assertRaises(OfferOvertimePreferenceError):
                    normalize_overtime_minutes(value)

    def test_rejects_non_numeric_input(self):
        for value in (None, "", "abc", "5m", "۵ دقیقه", 2.5, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(OfferOvertimePreferenceError):
                    normalize_overtime_minutes(value)

    def test_rejects_booleans_rather_than_reading_true_as_one_minute(self):
        for value in (True, False):
            with self.subTest(value=value):
                with self.assertRaises(OfferOvertimePreferenceError):
                    normalize_overtime_minutes(value)

    def test_error_carries_the_approved_message(self):
        with self.assertRaises(OfferOvertimePreferenceError) as caught:
            normalize_overtime_minutes("nope")
        self.assertEqual(caught.exception.message, INVALID_OVERTIME_VALUE_MESSAGE)


class PersistedValueTests(unittest.TestCase):
    def test_reads_the_stored_value(self):
        self.assertEqual(read_persisted_overtime_minutes(SimpleNamespace(offer_overtime_minutes=7)), 7)

    def test_a_corrupt_row_never_blocks_offer_creation(self):
        """Clamping, not raising: a bad row must not stop the market."""
        for stored, expected in ((-5, 0), (99, 10), (None, 0), ("5", 0), (True, 0)):
            with self.subTest(stored=stored):
                owner = SimpleNamespace(offer_overtime_minutes=stored)
                self.assertEqual(read_persisted_overtime_minutes(owner), expected)

    def test_missing_attribute_reads_as_disabled(self):
        self.assertEqual(read_persisted_overtime_minutes(SimpleNamespace()), 0)
        self.assertEqual(read_persisted_overtime_minutes(None), 0)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_comes_from_the_owner_row(self):
        owner = SimpleNamespace(offer_overtime_minutes=4)
        self.assertEqual(snapshot_overtime_minutes_for_new_offer(owner), 4)

    def test_snapshot_does_not_follow_a_later_preference_change(self):
        owner = SimpleNamespace(offer_overtime_minutes=3)
        frozen = snapshot_overtime_minutes_for_new_offer(owner)
        owner.offer_overtime_minutes = 9
        self.assertEqual(frozen, 3)


class EligibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = object()
        self.user = SimpleNamespace(id=11)

    async def _evaluate(self, *, accountant=False, relation=None):
        with patch(
            "core.services.accountant_relation_service.is_user_accountant",
            new=AsyncMock(return_value=accountant),
        ), patch(
            "core.services.customer_relation_service.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=relation),
        ):
            return await evaluate_overtime_preference_eligibility(self.db, self.user)

    async def test_ordinary_user_is_eligible(self):
        decision = await self._evaluate()
        self.assertTrue(decision.allowed)

    async def test_tier1_customer_is_eligible_because_offers_are_their_own(self):
        relation = SimpleNamespace(customer_tier=CustomerTier.TIER_1)
        decision = await self._evaluate(relation=relation)
        self.assertTrue(decision.allowed)

    async def test_tier2_customer_is_not_eligible(self):
        relation = SimpleNamespace(customer_tier=CustomerTier.TIER_2)
        decision = await self._evaluate(relation=relation)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "tier2_customer_cannot_own_offers")

    async def test_accountant_is_not_eligible(self):
        decision = await self._evaluate(accountant=True)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "accountant_has_no_market_access")

    async def test_accountant_check_wins_over_customer_tier(self):
        relation = SimpleNamespace(customer_tier=CustomerTier.TIER_1)
        decision = await self._evaluate(accountant=True, relation=relation)
        self.assertFalse(decision.allowed)

    async def test_unknown_user_is_not_eligible(self):
        self.user = SimpleNamespace(id=None)
        decision = await self._evaluate()
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "unknown_user")


class OfferCreationSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """The snapshot must come from the locked owner row, not the caller."""

    async def _create(self, *, owner, quota_policy):
        from core.offer_source import OfferSourceSurface
        from core.services import offer_creation_service
        from models.offer import OfferStatus, OfferType

        command = offer_creation_service.OfferCreationCommand(
            source_surface=OfferSourceSurface.WEBAPP,
            owner_user_id=getattr(owner, "id", 1),
            actor_user_id=getattr(owner, "id", 1),
            offer_type=OfferType.SELL,
            commodity_id=3,
            quantity=10,
            price=1000,
            status=OfferStatus.ACTIVE,
        )
        db = SimpleNamespace(add=lambda _obj: None, commit=AsyncMock(), refresh=AsyncMock())
        with patch.object(
            offer_creation_service,
            "_admit_local_offer_quota",
            new=AsyncMock(return_value=(owner, None, 0)),
        ), patch("core.user_counter_sync.increment_user_counters"):
            outcome = await offer_creation_service.create_authoritative_offer_with_outcome(
                db,
                command,
                commit=False,
                refresh=False,
                validate_market=False,
                quota_policy=quota_policy,
            )
        return outcome.offer

    async def test_new_offer_freezes_the_owners_persisted_preference(self):
        owner = SimpleNamespace(id=1, offer_overtime_minutes=8)
        offer = await self._create(owner=owner, quota_policy=object())
        self.assertEqual(offer.overtime_minutes_snapshot, 8)

    async def test_owner_with_the_feature_off_produces_a_zero_snapshot(self):
        owner = SimpleNamespace(id=1, offer_overtime_minutes=0)
        offer = await self._create(owner=owner, quota_policy=object())
        self.assertEqual(offer.overtime_minutes_snapshot, 0)

    async def test_out_of_range_owner_value_is_clamped_not_raised(self):
        owner = SimpleNamespace(id=1, offer_overtime_minutes=99)
        offer = await self._create(owner=owner, quota_policy=object())
        self.assertEqual(offer.overtime_minutes_snapshot, 10)


class SyncAuthorityTests(unittest.TestCase):
    @staticmethod
    def _user(**overrides):
        base = {
            name: None
            for name in (
                "id", "telegram_id", "username", "full_name", "mobile_number",
                "account_name", "address", "role", "account_status",
                "deactivated_at", "messenger_grace_expires_at",
                "messenger_blocked_at", "has_bot_access",
                "bot_onboarding_completed_at", "home_server", "is_deleted",
                "deleted_at", "can_block_users", "max_blocked_users",
                "max_daily_trades", "max_active_commodities",
                "max_daily_requests", "trading_restricted_until",
                "limitations_expire_at", "trades_count",
                "commodities_traded_count", "channel_messages_count",
                "max_sessions", "last_seen_at", "updated_at",
            )
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_preference_crosses_the_wire_in_the_user_payload(self):
        payload = build_user_sync_payload(self._user(offer_overtime_minutes=6))
        self.assertEqual(payload["offer_overtime_minutes"], 6)

    def test_payload_reports_disabled_when_the_attribute_is_absent(self):
        payload = build_user_sync_payload(self._user())
        self.assertEqual(payload["offer_overtime_minutes"], 0)

    def test_iran_may_write_the_preference_and_foreign_may_not(self):
        self.assertIn("offer_overtime_minutes", allowed_user_fields_for_source("iran"))
        self.assertNotIn("offer_overtime_minutes", allowed_user_fields_for_source("foreign"))
        self.assertNotIn("offer_overtime_minutes", USER_SYNC_FOREIGN_FIELDS)


if __name__ == "__main__":
    unittest.main()
