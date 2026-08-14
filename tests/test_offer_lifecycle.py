"""Stage 3: shared offer lifecycle projection and intake boundaries."""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.offer_lifecycle import (
    OfferLifecyclePhase,
    OfferRequestIntakePhase,
    classify_request_intake_phase,
    compute_lifecycle_deadlines,
    project_offer_lifecycle,
    publication_freshness_deadline_at,
)
from api.routers import trades


CREATED = datetime(2026, 8, 5, 12, 0, 0)


class DeadlineMathTests(unittest.TestCase):
    def test_zero_overtime_final_equals_normal(self):
        normal, final = compute_lifecycle_deadlines(
            CREATED,
            normal_lifetime_minutes=2,
            overtime_minutes_snapshot=0,
        )
        self.assertEqual(normal, CREATED + timedelta(minutes=2))
        self.assertEqual(final, normal)

    def test_ten_minute_overtime_extends_final_only(self):
        normal, final = compute_lifecycle_deadlines(
            CREATED,
            normal_lifetime_minutes=2,
            overtime_minutes_snapshot=10,
        )
        self.assertEqual(normal, CREATED + timedelta(minutes=2))
        self.assertEqual(final, CREATED + timedelta(minutes=12))


class IntakeBoundaryTests(unittest.TestCase):
    def _deadlines(self, overtime=0, normal=2):
        return compute_lifecycle_deadlines(
            CREATED,
            normal_lifetime_minutes=normal,
            overtime_minutes_snapshot=overtime,
        )

    def test_snapshot_zero_triplet(self):
        normal, final = self._deadlines(0)
        before = classify_request_intake_phase(
            receipt_at=normal - timedelta(seconds=1),
            normal_deadline_at=normal,
            final_deadline_at=final,
            overtime_minutes_snapshot=0,
        )
        at = classify_request_intake_phase(
            receipt_at=normal,
            normal_deadline_at=normal,
            final_deadline_at=final,
            overtime_minutes_snapshot=0,
        )
        after = classify_request_intake_phase(
            receipt_at=normal + timedelta(seconds=1),
            normal_deadline_at=normal,
            final_deadline_at=final,
            overtime_minutes_snapshot=0,
        )
        self.assertEqual(before, OfferRequestIntakePhase.AUTOMATIC)
        self.assertEqual(at, OfferRequestIntakePhase.REJECTED)
        self.assertEqual(after, OfferRequestIntakePhase.REJECTED)

    def test_overtime_triplet_around_normal_and_final(self):
        normal, final = self._deadlines(1)
        self.assertEqual(
            classify_request_intake_phase(
                receipt_at=normal - timedelta(seconds=1),
                normal_deadline_at=normal,
                final_deadline_at=final,
                overtime_minutes_snapshot=1,
            ),
            OfferRequestIntakePhase.AUTOMATIC,
        )
        self.assertEqual(
            classify_request_intake_phase(
                receipt_at=normal,
                normal_deadline_at=normal,
                final_deadline_at=final,
                overtime_minutes_snapshot=1,
            ),
            OfferRequestIntakePhase.REJECTED,
        )
        self.assertEqual(
            classify_request_intake_phase(
                receipt_at=normal + timedelta(seconds=1),
                normal_deadline_at=normal,
                final_deadline_at=final,
                overtime_minutes_snapshot=1,
            ),
            OfferRequestIntakePhase.APPROVAL,
        )
        self.assertEqual(
            classify_request_intake_phase(
                receipt_at=final - timedelta(seconds=1),
                normal_deadline_at=normal,
                final_deadline_at=final,
                overtime_minutes_snapshot=1,
            ),
            OfferRequestIntakePhase.APPROVAL,
        )
        self.assertEqual(
            classify_request_intake_phase(
                receipt_at=final,
                normal_deadline_at=normal,
                final_deadline_at=final,
                overtime_minutes_snapshot=1,
            ),
            OfferRequestIntakePhase.REJECTED,
        )

    def test_late_home_arrival_does_not_change_phase(self):
        """A receipt inside overtime stays APPROVAL even if evaluated later."""
        normal, final = self._deadlines(5)
        receipt = normal + timedelta(seconds=30)
        self.assertEqual(
            classify_request_intake_phase(
                receipt_at=receipt,
                normal_deadline_at=normal,
                final_deadline_at=final,
                overtime_minutes_snapshot=5,
            ),
            OfferRequestIntakePhase.APPROVAL,
        )


class DisplayProjectionTests(unittest.TestCase):
    def test_snapshot_zero_expires_at_matches_legacy_normal_end(self):
        offer = SimpleNamespace(
            created_at=CREATED,
            status="active",
            overtime_minutes_snapshot=0,
        )
        projection = project_offer_lifecycle(
            offer,
            normal_lifetime_minutes=2,
            as_of=CREATED + timedelta(seconds=30),
        )
        self.assertEqual(projection.phase, OfferLifecyclePhase.NORMAL)
        self.assertEqual(projection.expires_at_ts, projection.normal_deadline_ts)
        self.assertEqual(projection.timer_total_seconds, 120)
        self.assertTrue(projection.accepts_new_public_interaction)
        self.assertFalse(projection.terminal_expiry_due)

    def test_worker_and_intake_agree_at_exact_normal_for_zero_snapshot(self):
        offer = SimpleNamespace(
            created_at=CREATED,
            status="active",
            overtime_minutes_snapshot=0,
        )
        at_deadline = CREATED + timedelta(minutes=2)
        projection = project_offer_lifecycle(
            offer,
            normal_lifetime_minutes=2,
            as_of=at_deadline,
        )
        self.assertTrue(projection.terminal_expiry_due)
        self.assertFalse(projection.accepts_automatic_trade)
        intake = classify_request_intake_phase(
            receipt_at=at_deadline,
            normal_deadline_at=projection.normal_deadline_at,
            final_deadline_at=projection.final_deadline_at,
            overtime_minutes_snapshot=0,
        )
        self.assertEqual(intake, OfferRequestIntakePhase.REJECTED)

    def test_overtime_defers_terminal_expiry(self):
        offer = SimpleNamespace(
            created_at=CREATED,
            status="active",
            overtime_minutes_snapshot=3,
        )
        during_overtime = CREATED + timedelta(minutes=2, seconds=30)
        projection = project_offer_lifecycle(
            offer,
            normal_lifetime_minutes=2,
            as_of=during_overtime,
        )
        self.assertEqual(projection.phase, OfferLifecyclePhase.OVERTIME)
        self.assertFalse(projection.terminal_expiry_due)
        self.assertTrue(projection.accepts_overtime_request)
        self.assertFalse(projection.accepts_automatic_trade)

    def test_final_tail_defers_terminal_expiry(self):
        offer = SimpleNamespace(
            created_at=CREATED,
            status="active",
            overtime_minutes_snapshot=1,
        )
        after_final = CREATED + timedelta(minutes=3, seconds=5)
        projection = project_offer_lifecycle(
            offer,
            normal_lifetime_minutes=2,
            as_of=after_final,
            has_final_tail_request=True,
        )
        self.assertEqual(projection.phase, OfferLifecyclePhase.FINAL_TAIL)
        self.assertFalse(projection.terminal_expiry_due)
        self.assertFalse(projection.accepts_new_public_interaction)

    def test_publication_freshness_keeps_five_second_margin_before_final(self):
        from datetime import timezone

        deadline = publication_freshness_deadline_at(
            CREATED,
            normal_lifetime_minutes=2,
            overtime_minutes_snapshot=1,
            safety_seconds=5,
        )
        self.assertEqual(
            deadline,
            (CREATED + timedelta(minutes=3, seconds=-5)).replace(tzinfo=timezone.utc),
        )


class TradeGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_transit_grace_no_longer_keeps_late_receipt_automatic(self):
        """Old behavior: edge before deadline + transit<=grace → accept.
        New behavior: receipt after normal deadline → not automatic.
        """
        offer = SimpleNamespace(
            created_at=CREATED,
            overtime_minutes_snapshot=0,
            status="active",
        )
        with patch(
            "core.trading_settings.get_trading_settings_async",
            AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=10)),
        ), patch.object(trades.settings, "trade_forward_grace_seconds", 120), patch(
            "api.routers.trades.datetime"
        ) as datetime_mock:
            # now is 30s after deadline; receipt was 15s before deadline.
            datetime_mock.utcnow.return_value = CREATED + timedelta(minutes=10, seconds=30)
            expired = await trades._is_offer_expired_for_trade(
                offer,
                edge_received_at=CREATED + timedelta(minutes=9, seconds=45),
            )
        # Receipt still automatic, so not expired for automatic path.
        self.assertFalse(expired)

        with patch(
            "core.trading_settings.get_trading_settings_async",
            AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=10)),
        ), patch.object(trades.settings, "trade_forward_grace_seconds", 120), patch(
            "api.routers.trades.datetime"
        ) as datetime_mock:
            datetime_mock.utcnow.return_value = CREATED + timedelta(minutes=10, seconds=30)
            expired = await trades._is_offer_expired_for_trade(
                offer,
                edge_received_at=CREATED + timedelta(minutes=10, seconds=5),
            )
        # Receipt after normal deadline: rejected even inside old grace window.
        self.assertTrue(expired)

    async def test_exact_normal_boundary_rejects_automatic_trade(self):
        offer = SimpleNamespace(
            created_at=CREATED,
            overtime_minutes_snapshot=0,
            status="active",
        )
        with patch(
            "core.trading_settings.get_trading_settings_async",
            AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=10)),
        ):
            expired = await trades._is_offer_expired_for_trade(
                offer,
                edge_received_at=CREATED + timedelta(minutes=10),
            )
        self.assertTrue(expired)

    async def test_in_flight_grace_still_bounds_worker_race(self):
        edge = CREATED + timedelta(minutes=5)
        with patch.object(trades.settings, "trade_forward_grace_seconds", 3):
            self.assertTrue(
                trades._within_in_flight_finalization_grace(
                    edge_received_at=edge,
                    now=edge + timedelta(seconds=2),
                )
            )
            self.assertFalse(
                trades._within_in_flight_finalization_grace(
                    edge_received_at=edge,
                    now=edge + timedelta(seconds=4),
                )
            )


if __name__ == "__main__":
    unittest.main()
