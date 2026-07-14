import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.offer_identity import build_offer_public_link, generate_offer_public_id, is_offer_public_id_shape
from core.offer_source import OfferSourceSurface
from core.enums import SettlementType
from core.services.offer_creation_service import (
    OfferCreationCommand,
    OfferCreationIdempotencyConflictError,
    OfferCreationValidationError,
    OFFER_CREATION_FINGERPRINT_VERSION,
    build_authoritative_offer,
    canonical_offer_creation_payload,
    create_authoritative_offer,
    ensure_offer_idempotency_replay_matches,
    offer_creation_fingerprint,
)
from core.services.market_transition_service import (
    MarketOfferAdmissionClosedError,
    MarketOfferAdmissionUnavailableError,
)
from models.offer import OfferStatus, OfferType


class OfferCreationServiceTests(unittest.TestCase):
    @staticmethod
    def idempotent_command(**overrides):
        values = {
            "source_surface": OfferSourceSurface.WEBAPP,
            "owner_user_id": 11,
            "actor_user_id": 12,
            "offer_type": OfferType.BUY,
            "settlement_type": SettlementType.CASH,
            "commodity_id": 7,
            "quantity": 20,
            "price": 100_000,
            "is_wholesale": False,
            "lot_sizes": [5, 15],
            "notes": " urgent ",
            "idempotency_key": "offer-stage15-1",
            "republished_from_offer_public_id": "ofr_source_15",
        }
        values.update(overrides)
        return OfferCreationCommand(**values)

    def test_offer_creation_fingerprint_canonicalizes_equivalent_representations(self):
        first = self.idempotent_command()
        second = self.idempotent_command(
            source_surface="webapp",
            offer_type="buy",
            settlement_type="cash",
            lot_sizes=[15, 5],
            notes="urgent",
        )

        self.assertEqual(
            canonical_offer_creation_payload(first),
            canonical_offer_creation_payload(second),
        )
        self.assertEqual(offer_creation_fingerprint(first), offer_creation_fingerprint(second))

    def test_offer_creation_fingerprint_covers_every_economic_identity_field(self):
        original = self.idempotent_command()
        changes = {
            "source_surface": OfferSourceSurface.TELEGRAM_BOT,
            "owner_user_id": 99,
            "actor_user_id": 98,
            "offer_type": OfferType.SELL,
            "settlement_type": SettlementType.TOMORROW,
            "commodity_id": 8,
            "quantity": 21,
            "price": 100_001,
            "is_wholesale": True,
            "lot_sizes": [20],
            "notes": "different",
            "republished_from_offer_public_id": "ofr_source_16",
        }
        expected = offer_creation_fingerprint(original)
        for field_name, changed_value in changes.items():
            with self.subTest(field_name=field_name):
                self.assertNotEqual(
                    offer_creation_fingerprint(replace(original, **{field_name: changed_value})),
                    expected,
                )

    def test_idempotent_offer_stores_versioned_fingerprint(self):
        command = self.idempotent_command()
        offer = build_authoritative_offer(command)

        self.assertEqual(offer.idempotency_key, "offer-stage15-1")
        self.assertEqual(
            offer.idempotency_fingerprint_version,
            OFFER_CREATION_FINGERPRINT_VERSION,
        )
        self.assertEqual(offer.idempotency_fingerprint, offer_creation_fingerprint(command))
        ensure_offer_idempotency_replay_matches(offer, command)

    def test_replay_with_same_key_and_different_payload_is_rejected(self):
        command = self.idempotent_command()
        offer = build_authoritative_offer(command)

        with self.assertRaises(OfferCreationIdempotencyConflictError):
            ensure_offer_idempotency_replay_matches(
                offer,
                replace(command, price=command.price + 1),
            )

    def test_replay_with_partial_or_unknown_fingerprint_metadata_fails_closed(self):
        command = self.idempotent_command()
        offer = build_authoritative_offer(command)
        for version, fingerprint in (
            (99, offer.idempotency_fingerprint),
            (OFFER_CREATION_FINGERPRINT_VERSION, None),
            (None, offer.idempotency_fingerprint),
        ):
            with self.subTest(version=version, fingerprint=bool(fingerprint)):
                offer.idempotency_fingerprint_version = version
                offer.idempotency_fingerprint = fingerprint
                with self.assertRaises(OfferCreationIdempotencyConflictError):
                    ensure_offer_idempotency_replay_matches(offer, command)

    def test_legacy_offer_replays_only_when_stable_fields_reconstruct_exact_intent(self):
        command = self.idempotent_command()
        legacy_offer = SimpleNamespace(
            user_id=command.owner_user_id,
            actor_user_id=command.actor_user_id,
            home_server="iran",
            offer_type=OfferType.BUY,
            settlement_type=SettlementType.CASH,
            commodity_id=command.commodity_id,
            quantity=command.quantity,
            price=command.price,
            is_wholesale=command.is_wholesale,
            lot_sizes=[5],
            original_lot_sizes=[15, 5],
            notes="urgent",
            republished_from_offer_public_id="ofr_source_15",
            idempotency_fingerprint_version=None,
            idempotency_fingerprint=None,
        )

        ensure_offer_idempotency_replay_matches(legacy_offer, command)
        legacy_offer.actor_user_id = None
        with self.assertRaises(OfferCreationIdempotencyConflictError):
            ensure_offer_idempotency_replay_matches(legacy_offer, command)

    def test_webapp_offer_is_iran_home_with_public_identity(self):
        offer = build_authoritative_offer(
            OfferCreationCommand(
                source_surface=OfferSourceSurface.WEBAPP,
                owner_user_id=1,
                actor_user_id=1,
                offer_type="buy",
                settlement_type="tomorrow",
                commodity_id=7,
                quantity=12,
                price=1000,
            )
        )

        self.assertEqual(offer.home_server, "iran")
        self.assertEqual(offer.offer_type, OfferType.BUY)
        self.assertEqual(offer.settlement_type, SettlementType.TOMORROW)
        self.assertEqual(offer.status, OfferStatus.ACTIVE)
        self.assertTrue(is_offer_public_id_shape(offer.offer_public_id))

    def test_bot_offer_is_foreign_home_with_public_identity(self):
        offer = build_authoritative_offer(
            OfferCreationCommand(
                source_surface=OfferSourceSurface.TELEGRAM_BOT,
                owner_user_id=2,
                actor_user_id=2,
                offer_type=OfferType.SELL,
                commodity_id=7,
                quantity=20,
                price=2000,
                lot_sizes=[5, 15],
            )
        )

        self.assertEqual(offer.home_server, "foreign")
        self.assertEqual(offer.settlement_type, SettlementType.CASH)
        self.assertEqual(offer.original_lot_sizes, [5, 15])
        self.assertTrue(is_offer_public_id_shape(offer.offer_public_id))

    def test_republish_provenance_is_stored_only_on_new_offer(self):
        offer = build_authoritative_offer(
            OfferCreationCommand(
                source_surface=OfferSourceSurface.WEBAPP,
                owner_user_id=2,
                actor_user_id=2,
                offer_type=OfferType.SELL,
                commodity_id=7,
                quantity=8,
                price=2000,
                republished_from_offer_public_id="ofr_source_offer",
            )
        )

        self.assertEqual(offer.republished_from_offer_public_id, "ofr_source_offer")
        self.assertIsNone(offer.republished_offer_id)

    def test_internal_sync_preserves_incoming_home_and_public_id(self):
        offer = build_authoritative_offer(
            OfferCreationCommand(
                source_surface=OfferSourceSurface.INTERNAL_SYNC,
                owner_user_id=3,
                actor_user_id=4,
                offer_type="sell",
                commodity_id=8,
                quantity=5,
                price=3000,
                incoming_home_server="foreign",
                offer_public_id="ofr_remote_public",
            )
        )

        self.assertEqual(offer.home_server, "foreign")
        self.assertEqual(offer.offer_public_id, "ofr_remote_public")

    def test_internal_sync_requires_incoming_public_id(self):
        with self.assertRaises(ValueError):
            build_authoritative_offer(
                OfferCreationCommand(
                    source_surface=OfferSourceSurface.INTERNAL_SYNC,
                    owner_user_id=3,
                    actor_user_id=4,
                    offer_type="buy",
                    commodity_id=8,
                    quantity=5,
                    price=3000,
                    incoming_home_server="iran",
                )
            )

    def test_public_links_do_not_expose_integer_ids(self):
        public_id = generate_offer_public_id()
        link = build_offer_public_link(public_id, frontend_url="https://app.example")

        self.assertIn(public_id, link)
        self.assertNotIn("/123", link)
        self.assertTrue(link.startswith("https://app.example/market?offer=ofr_"))


class FakeDB:
    def __init__(self):
        self.added = []

    def add(self, offer):
        self.added.append(offer)

    async def commit(self):
        return None

    async def refresh(self, offer):
        return None


class OfferCreationServiceAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_authoritative_offer_validates_before_add(self):
        db = FakeDB()
        command = OfferCreationCommand(
            source_surface=OfferSourceSurface.WEBAPP,
            owner_user_id=1,
            actor_user_id=1,
            offer_type="buy",
            commodity_id=7,
            quantity=0,
            price=1000,
        )

        with patch("core.services.trade_service.validate_quantity", return_value=(False, "bad quantity")):
            with self.assertRaises(OfferCreationValidationError):
                await create_authoritative_offer(db, command)

        self.assertEqual(db.added, [])

    async def test_create_authoritative_offer_holds_final_market_fence_through_commit(self):
        db = FakeDB()
        command = OfferCreationCommand(
            source_surface=OfferSourceSurface.WEBAPP,
            owner_user_id=1,
            actor_user_id=1,
            offer_type="buy",
            commodity_id=7,
            quantity=10,
            price=1000,
        )

        with patch(
            "core.services.offer_creation_service.validate_offer_creation_command",
            new=AsyncMock(),
        ) as validate_mock, patch(
            "core.services.offer_creation_service.acquire_market_offer_admission_fence",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True)),
        ) as fence_mock:
            offer = await create_authoritative_offer(
                db,
                command,
                enforce_market_admission=True,
            )

        validate_mock.assert_awaited_once_with(db, command)
        fence_mock.assert_awaited_once_with(db)
        self.assertIs(db.added[0], offer)

    async def test_create_authoritative_offer_rejected_final_fence_adds_nothing(self):
        command = OfferCreationCommand(
            source_surface=OfferSourceSurface.TELEGRAM_BOT,
            owner_user_id=1,
            actor_user_id=1,
            offer_type="sell",
            commodity_id=7,
            quantity=10,
            price=1000,
        )

        errors = (
            MarketOfferAdmissionClosedError("market_closed_during_offer_admission"),
            MarketOfferAdmissionUnavailableError("market_offer_admission_fence_unavailable"),
        )
        for rejection in errors:
            with self.subTest(rejection=type(rejection).__name__):
                db = FakeDB()
                with patch(
                    "core.services.offer_creation_service.validate_offer_creation_command",
                    new=AsyncMock(),
                ), patch(
                    "core.services.offer_creation_service.acquire_market_offer_admission_fence",
                    new=AsyncMock(side_effect=rejection),
                ):
                    with self.assertRaises(type(rejection)):
                        await create_authoritative_offer(
                            db,
                            command,
                            enforce_market_admission=True,
                        )

                self.assertEqual(db.added, [])


if __name__ == "__main__":
    unittest.main()
