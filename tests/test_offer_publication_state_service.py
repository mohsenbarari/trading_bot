import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from core.services.offer_publication_state_service import (
    CanonicalPublicationIdentityError,
    TELEGRAM_PRIMARY_PUBLISHER_BOT_IDENTITY,
    apply_publication_state_update,
    build_offer_publication_state,
    canonical_telegram_publication_identity,
    ensure_telegram_publication_publisher_identity,
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
        self.assertEqual(
            state.publisher_bot_identity,
            TELEGRAM_PRIMARY_PUBLISHER_BOT_IDENTITY,
        )

    def test_webapp_publication_state_uses_iran_owner_surface(self):
        state = build_offer_publication_state(
            make_offer(),
            OfferPublicationSurface.WEBAPP_MARKET,
            now=datetime(2026, 1, 2, 12, 0, 0),
        )

        self.assertEqual(state.publication_owner_server, "iran")
        self.assertEqual(state.status, OfferPublicationStatus.PENDING)
        self.assertEqual(state.dedupe_key, "offer-publication:webapp_market:ofr_8")
        self.assertIsNone(state.publisher_bot_identity)

    def test_canonical_telegram_identity_requires_primary_chat_and_message(self):
        state = build_offer_publication_state(
            make_offer(),
            OfferPublicationSurface.TELEGRAM_CHANNEL,
        )
        state.telegram_chat_id = -100123
        state.telegram_message_id = 700

        identity = canonical_telegram_publication_identity(state)

        self.assertEqual(identity.publisher_bot_identity, "primary")
        self.assertEqual(identity.destination_chat_id, -100123)
        self.assertEqual(identity.message_id, 700)

    def test_canonical_telegram_identity_fails_closed_on_missing_or_wrong_fields(self):
        state = build_offer_publication_state(
            make_offer(),
            OfferPublicationSurface.TELEGRAM_CHANNEL,
        )
        state.telegram_chat_id = -100123
        state.telegram_message_id = 700
        invalid_values = (
            ("surface", None),
            ("publisher_bot_identity", None),
            ("publisher_bot_identity", "channel_editor"),
            ("telegram_chat_id", None),
            ("telegram_chat_id", 0),
            ("telegram_message_id", None),
            ("telegram_message_id", -1),
        )

        for field, value in invalid_values:
            with self.subTest(field=field, value=value):
                original = getattr(state, field)
                setattr(state, field, value)
                with self.assertRaises(CanonicalPublicationIdentityError):
                    canonical_telegram_publication_identity(state)
                setattr(state, field, original)

    def test_missing_publisher_can_only_be_repaired_for_telegram_surface(self):
        state = build_offer_publication_state(
            make_offer(),
            OfferPublicationSurface.TELEGRAM_CHANNEL,
        )
        state.publisher_bot_identity = None

        self.assertEqual(
            ensure_telegram_publication_publisher_identity(state),
            "primary",
        )
        self.assertEqual(state.publisher_bot_identity, "primary")

        webapp_state = build_offer_publication_state(
            make_offer(),
            OfferPublicationSurface.WEBAPP_MARKET,
        )
        with self.assertRaises(CanonicalPublicationIdentityError):
            ensure_telegram_publication_publisher_identity(webapp_state)

    def test_canonical_telegram_identity_cannot_be_rewritten(self):
        now = datetime(2026, 1, 2, 12, 0, 0)
        state = build_offer_publication_state(
            make_offer(),
            OfferPublicationSurface.TELEGRAM_CHANNEL,
            now=now,
        )
        state.telegram_chat_id = -100123
        state.telegram_message_id = 700
        state.surface_resource_id = "700"

        mismatches = (
            {"publisher_bot_identity": "channel_editor"},
            {"telegram_chat_id": -100999},
            {"telegram_message_id": 701},
            {"surface_resource_id": "701"},
        )
        for values in mismatches:
            with self.subTest(values=values):
                with self.assertRaises(CanonicalPublicationIdentityError):
                    apply_publication_state_update(
                        state,
                        offer_status="active",
                        offer_version_id=state.offer_version_id,
                        requested_status=OfferPublicationStatus.FAILED,
                        now=now,
                        **values,
                    )
                self.assertEqual(state.status, OfferPublicationStatus.PENDING)
                self.assertEqual(state.telegram_chat_id, -100123)
                self.assertEqual(state.telegram_message_id, 700)
                self.assertEqual(state.surface_resource_id, "700")

        state.publisher_bot_identity = None
        with self.assertRaises(CanonicalPublicationIdentityError):
            apply_publication_state_update(
                state,
                offer_status="active",
                offer_version_id=state.offer_version_id,
                requested_status=OfferPublicationStatus.FAILED,
                publisher_bot_identity="channel_editor",
            )
        self.assertIsNone(state.publisher_bot_identity)
        self.assertEqual(state.status, OfferPublicationStatus.PENDING)

        with self.assertRaises(ValueError):
            apply_publication_state_update(
                state,
                offer_status="active",
                offer_version_id=state.offer_version_id,
                requested_status="not-a-publication-status",
            )
        self.assertIsNone(state.publisher_bot_identity)
        self.assertEqual(state.status, OfferPublicationStatus.PENDING)

    def test_webapp_update_rejects_telegram_identity_fields(self):
        state = build_offer_publication_state(
            make_offer(),
            OfferPublicationSurface.WEBAPP_MARKET,
        )

        with self.assertRaises(CanonicalPublicationIdentityError):
            apply_publication_state_update(
                state,
                offer_status="active",
                offer_version_id=state.offer_version_id,
                requested_status=OfferPublicationStatus.VISIBLE,
                publisher_bot_identity="primary",
            )

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
