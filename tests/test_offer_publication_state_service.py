import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from core.services.offer_publication_state_service import (
    apply_publication_state_update,
    build_offer_publication_state,
    mark_publication_lagged_if_overdue,
)
from models.offer_publication_state import (
    OfferPublicationStatus,
    OfferPublicationSurface,
)


def make_offer(status="active", version_id=2):
    return SimpleNamespace(
        id=8,
        offer_public_id="ofr_8",
        home_server="foreign",
        version_id=version_id,
        status=SimpleNamespace(value=status),
    )


class OfferPublicationStateServiceTests(unittest.TestCase):
    def test_business_offer_state_is_separate_from_failed_telegram_publication(self):
        now = datetime(2026, 1, 2, 12, 0, 0)
        offer = make_offer(status="active", version_id=2)
        state = build_offer_publication_state(
            offer,
            OfferPublicationSurface.TELEGRAM_CHANNEL,
            now=now,
        )

        result = apply_publication_state_update(
            state,
            offer_status=offer.status,
            offer_version_id=offer.version_id,
            requested_status=OfferPublicationStatus.FAILED,
            now=now,
            error_code="telegram_send_failed",
        )

        self.assertTrue(result.applied)
        self.assertEqual(offer.status.value, "active")
        self.assertEqual(state.status, OfferPublicationStatus.FAILED)
        self.assertEqual(state.error_code, "telegram_send_failed")
        self.assertEqual(state.publication_owner_server, "foreign")

    def test_webapp_publication_state_uses_iran_owner_surface(self):
        state = build_offer_publication_state(
            make_offer(),
            OfferPublicationSurface.WEBAPP_MARKET,
            now=datetime(2026, 1, 2, 12, 0, 0),
        )

        self.assertEqual(state.publication_owner_server, "iran")
        self.assertEqual(state.status, OfferPublicationStatus.PENDING)
        self.assertEqual(state.dedupe_key, "offer-publication:webapp_market:ofr_8")

    def test_terminal_offer_converts_active_publication_update_to_disabled(self):
        now = datetime(2026, 1, 2, 12, 0, 0)
        state = build_offer_publication_state(make_offer(status="active", version_id=3), "telegram_channel", now=now)

        result = apply_publication_state_update(
            state,
            offer_status="completed",
            offer_version_id=4,
            requested_status=OfferPublicationStatus.SENT,
            now=now,
        )

        self.assertTrue(result.applied)
        self.assertEqual(result.reason, "converted_terminal_offer")
        self.assertEqual(state.status, OfferPublicationStatus.DISABLED)
        self.assertEqual(state.error_code, "offer_terminal")
        self.assertEqual(state.disabled_at, now)

    def test_old_publication_event_does_not_rewrite_newer_terminal_projection(self):
        now = datetime(2026, 1, 2, 12, 0, 0)
        state = build_offer_publication_state(make_offer(status="expired", version_id=5), "telegram_channel", now=now)
        state.status = OfferPublicationStatus.DISABLED
        state.offer_version_id = 5

        result = apply_publication_state_update(
            state,
            offer_status="active",
            offer_version_id=4,
            requested_status=OfferPublicationStatus.SENT,
            now=now,
            telegram_message_id=700,
        )

        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "stale_offer_version")
        self.assertEqual(state.status, OfferPublicationStatus.DISABLED)
        self.assertIsNone(state.telegram_message_id)

    def test_pending_publication_becomes_lagged_after_threshold(self):
        now = datetime(2026, 1, 2, 12, 0, 3)
        state = build_offer_publication_state(
            make_offer(),
            OfferPublicationSurface.WEBAPP_MARKET,
            now=now - timedelta(seconds=3),
        )

        changed = mark_publication_lagged_if_overdue(state, now=now, threshold_seconds=2)

        self.assertTrue(changed)
        self.assertEqual(state.status, OfferPublicationStatus.LAGGED)
        self.assertEqual(state.error_code, "publication_lagged")
        self.assertEqual(state.lagged_at, now)

    def test_update_without_offer_version_does_not_clear_existing_version(self):
        now = datetime(2026, 1, 2, 12, 0, 0)
        state = build_offer_publication_state(make_offer(version_id=6), "webapp_market", now=now)

        result = apply_publication_state_update(
            state,
            offer_status="active",
            offer_version_id=None,
            requested_status=OfferPublicationStatus.VISIBLE,
            now=now,
        )

        self.assertTrue(result.applied)
        self.assertEqual(state.status, OfferPublicationStatus.VISIBLE)
        self.assertEqual(state.offer_version_id, 6)
