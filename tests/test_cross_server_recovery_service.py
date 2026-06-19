import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.cross_server_recovery_service import (
    SyncRecoveryHealthSnapshot,
    finalize_outage_recovery,
)
from core.services.offer_expiry_service import OfferExpiryReason
from models.notification import Notification
from models.offer import OfferStatus


class FakeDB:
    def __init__(self):
        self.added = []
        self.commit = AsyncMock()

    def add(self, item):
        self.added.append(item)


def make_offer(**overrides):
    data = {
        "id": 12,
        "offer_public_id": "ofr_12",
        "user_id": 7,
        "home_server": "foreign",
        "status": OfferStatus.ACTIVE,
        "created_at": datetime(2026, 6, 19, 10, 0, 0),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class CrossServerRecoveryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_dirty_recovery_health_gates_without_mutation(self):
        db = FakeDB()
        offer = make_offer()
        dirty_health = SyncRecoveryHealthSnapshot(
            unsynced_change_log_count=2,
            outbound_queue=0,
            retry_queue=0,
        )

        with patch(
            "core.services.cross_server_recovery_service.load_active_publication_gate",
            new=AsyncMock(return_value={"enabled": True}),
        ), patch(
            "core.services.cross_server_recovery_service.count_recovery_active_offer_candidates",
            new=AsyncMock(return_value=1),
        ), patch(
            "core.services.cross_server_recovery_service.load_recovery_active_offer_candidates",
            new=AsyncMock(return_value=[offer]),
        ):
            report = await finalize_outage_recovery(
                db,
                outage_class="medium",
                cutoff=datetime(2026, 6, 19, 11, 0, 0),
                server_mode="foreign",
                dry_run=False,
                health_snapshot=dirty_health,
            )

        self.assertEqual(report["status"], "gated")
        self.assertEqual(offer.status, OfferStatus.ACTIVE)
        self.assertEqual(report["expired_count"], 0)
        self.assertEqual(report["owner_notification_count"], 0)
        db.commit.assert_not_awaited()

    async def test_clean_recovery_expires_candidates_and_creates_owner_notifications(self):
        db = FakeDB()
        offer = make_offer()
        clean_health = SyncRecoveryHealthSnapshot(
            unsynced_change_log_count=0,
            outbound_queue=0,
            retry_queue=0,
        )

        with patch(
            "core.services.cross_server_recovery_service.load_active_publication_gate",
            new=AsyncMock(return_value={"enabled": True}),
        ), patch(
            "core.services.cross_server_recovery_service.count_recovery_active_offer_candidates",
            new=AsyncMock(return_value=1),
        ), patch(
            "core.services.cross_server_recovery_service.load_recovery_active_offer_candidates",
            new=AsyncMock(return_value=[offer]),
        ), patch(
            "core.services.cross_server_recovery_service.clear_active_publication_gate",
            new=AsyncMock(return_value=True),
        ) as clear_gate:
            report = await finalize_outage_recovery(
                db,
                outage_class="long",
                cutoff=datetime(2026, 6, 19, 11, 0, 0),
                server_mode="foreign",
                dry_run=False,
                health_snapshot=clean_health,
            )

        self.assertEqual(report["status"], "finalized_pending_sync")
        self.assertEqual(report["expired_count"], 1)
        self.assertEqual(report["owner_notification_count"], 1)
        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertEqual(offer.expire_reason, OfferExpiryReason.RECOVERY_FINALIZATION)
        self.assertEqual(offer.expire_source_surface, "system")
        self.assertEqual(offer.expire_source_server, "foreign")
        self.assertIsNone(offer.expired_by_user_id)
        self.assertEqual(len(db.added), 1)
        self.assertIsInstance(db.added[0], Notification)
        self.assertEqual(db.added[0].user_id, 7)
        self.assertIn("ofr_12", db.added[0].message)
        db.commit.assert_awaited_once()
        clear_gate.assert_not_awaited()

    async def test_clean_recovery_with_no_candidates_clears_publication_gate(self):
        db = FakeDB()
        clean_health = SyncRecoveryHealthSnapshot(
            unsynced_change_log_count=0,
            outbound_queue=0,
            retry_queue=0,
        )

        with patch(
            "core.services.cross_server_recovery_service.load_active_publication_gate",
            new=AsyncMock(return_value={"enabled": True}),
        ), patch(
            "core.services.cross_server_recovery_service.count_recovery_active_offer_candidates",
            new=AsyncMock(return_value=0),
        ), patch(
            "core.services.cross_server_recovery_service.load_recovery_active_offer_candidates",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.services.cross_server_recovery_service.clear_active_publication_gate",
            new=AsyncMock(return_value=True),
        ) as clear_gate:
            report = await finalize_outage_recovery(
                db,
                outage_class="medium",
                cutoff=datetime(2026, 6, 19, 11, 0, 0),
                server_mode="foreign",
                dry_run=False,
                health_snapshot=clean_health,
            )

        self.assertEqual(report["status"], "recovered")
        self.assertTrue(report["gate_cleared"])
        clear_gate.assert_awaited_once()
        db.commit.assert_not_awaited()

    async def test_dry_run_reports_candidates_without_mutation(self):
        db = FakeDB()
        offer = make_offer()
        clean_health = SyncRecoveryHealthSnapshot(
            unsynced_change_log_count=0,
            outbound_queue=0,
            retry_queue=0,
        )

        with patch(
            "core.services.cross_server_recovery_service.load_active_publication_gate",
            new=AsyncMock(return_value={"enabled": True}),
        ), patch(
            "core.services.cross_server_recovery_service.count_recovery_active_offer_candidates",
            new=AsyncMock(return_value=1),
        ), patch(
            "core.services.cross_server_recovery_service.load_recovery_active_offer_candidates",
            new=AsyncMock(return_value=[offer]),
        ):
            report = await finalize_outage_recovery(
                db,
                outage_class="medium",
                cutoff=datetime(2026, 6, 19, 11, 0, 0),
                server_mode="foreign",
                dry_run=True,
                health_snapshot=clean_health,
            )

        self.assertEqual(report["status"], "would_finalize")
        self.assertEqual(offer.status, OfferStatus.ACTIVE)
        self.assertEqual(report["candidate_count"], 1)
        db.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
