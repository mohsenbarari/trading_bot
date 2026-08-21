import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.offer_identity import build_offer_public_link, generate_offer_public_id, is_offer_public_id_shape
from core.offer_source import OfferSourceSurface
from core.enums import SettlementType
from core.services.offer_creation_service import (
    OfferCreationCommand,
    OfferCreationCustomerLimitExceededError,
    OfferCreationIdempotencyConflictError,
    OfferCreationQuotaPolicy,
    OfferCreationValidationError,
    OFFER_CREATION_FINGERPRINT_VERSION,
    build_authoritative_offer,
    canonical_offer_creation_payload,
    create_authoritative_offer,
    create_authoritative_offer_with_outcome,
    ensure_offer_idempotency_replay_matches,
    offer_creation_fingerprint,
    validate_offer_creation_command,
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

    async def scalar(self, _statement):
        return 0


class OfferCreationServiceAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_offer_admission_enforces_customer_relation_policy(self):
        from core.services.customer_relation_service import CustomerOfferLimitViolation

        db = FakeDB()
        command = OfferCreationCommand(
            source_surface=OfferSourceSurface.TELEGRAM_BOT,
            owner_user_id=5,
            actor_user_id=5,
            offer_type="sell",
            commodity_id=7,
            quantity=21,
            price=100_000,
        )
        owner = SimpleNamespace(id=5)

        with patch(
            "core.services.offer_creation_service.validate_offer_creation_command",
            new=AsyncMock(),
        ), patch(
            "core.services.offer_creation_service._lock_offer_owner",
            new=AsyncMock(return_value=owner),
        ), patch(
            "core.services.offer_creation_service._load_local_idempotency_replay",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.customer_relation_service.enforce_customer_offer_limits_for_creation",
            new=AsyncMock(
                side_effect=CustomerOfferLimitViolation(
                    customer_user_id=5,
                    relation_id=12,
                    reason_code="offer_above_maximum",
                )
            ),
        ):
            with self.assertRaises(OfferCreationCustomerLimitExceededError) as exc_info:
                await create_authoritative_offer_with_outcome(
                    db,
                    command,
                    commit=False,
                    refresh=False,
                    quota_policy=OfferCreationQuotaPolicy(
                        max_active_offers=5,
                        enforce_user_limits=False,
                    ),
                )

        self.assertEqual(
            exc_info.exception.reason,
            "customer_offer_limit:offer_above_maximum",
        )
        self.assertEqual(
            exc_info.exception.detail,
            "شما مجاز به انجام این فعالیت نیستید.",
        )
        self.assertEqual(db.added, [])

    async def test_pack_offer_is_exactly_one_indivisible_hundred_coin_pack(self):
        valid = OfferCreationCommand(
            source_surface=OfferSourceSurface.WEBAPP,
            owner_user_id=1,
            actor_user_id=1,
            offer_type="buy",
            commodity_id=81,
            commodity_name="پک نیم",
            quantity=100,
            price=100_600,
            is_wholesale=True,
        )
        with patch(
            "core.config.settings.offer_model_price_guard_enabled", True
        ), patch(
            "core.services.offer_model_price_guard.evaluate_offer_model_price_guard",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, message=None)),
        ):
            await validate_offer_creation_command(FakeDB(), valid)

            for invalid in (
                replace(valid, quantity=99),
                replace(valid, is_wholesale=False, lot_sizes=[50, 50]),
                replace(valid, lot_sizes=[100]),
            ):
                with self.subTest(command=invalid):
                    with self.assertRaisesRegex(
                        OfferCreationValidationError,
                        "آفر پک فقط یکجا",
                    ):
                        await validate_offer_creation_command(FakeDB(), invalid)

    async def test_model_guard_activation_disables_legacy_competitive_rejection(self):
        command = OfferCreationCommand(
            source_surface=OfferSourceSurface.WEBAPP,
            owner_user_id=1,
            actor_user_id=1,
            offer_type="sell",
            commodity_id=7,
            quantity=10,
            price=100_000,
            is_wholesale=True,
        )
        with patch(
            "core.config.settings.offer_model_price_guard_enabled",
            True,
        ), patch(
            "core.services.offer_model_price_guard.evaluate_offer_model_price_guard",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, message=None)),
        ) as model_guard, patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(False, "legacy rejection")),
        ) as legacy_guard:
            await validate_offer_creation_command(FakeDB(), command)

        model_guard.assert_awaited_once()
        legacy_guard.assert_not_awaited()

    async def test_authoritative_validation_rejects_model_outlier_for_direct_caller(self):
        command = OfferCreationCommand(
            source_surface=OfferSourceSurface.WEBAPP,
            owner_user_id=1,
            actor_user_id=1,
            offer_type="sell",
            commodity_id=7,
            quantity=10,
            price=999_999,
        )
        with patch(
            "core.config.settings.offer_model_price_guard_enabled", True
        ), patch(
            "core.services.offer_model_price_guard.evaluate_offer_model_price_guard",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    allowed=False,
                    message="قیمت فروش شما بالاست؛ قیمت بهتری در بازار وجود دارد.",
                )
            ),
        ) as model_guard:
            with self.assertRaisesRegex(
                OfferCreationValidationError,
                "قیمت فروش شما بالاست",
            ):
                await validate_offer_creation_command(FakeDB(), command)

        model_guard.assert_awaited_once()

    async def test_validate_market_false_cannot_bypass_model_guard_but_sync_can(self):
        command = OfferCreationCommand(
            source_surface=OfferSourceSurface.TELEGRAM_BOT,
            owner_user_id=1,
            actor_user_id=1,
            offer_type="buy",
            commodity_id=7,
            quantity=10,
            price=1,
        )
        decision = SimpleNamespace(
            allowed=False,
            message="قیمت خرید شما پایین است؛ قیمت بهتری در بازار وجود دارد.",
        )
        with patch(
            "core.config.settings.offer_model_price_guard_enabled", True
        ), patch(
            "core.services.offer_model_price_guard.evaluate_offer_model_price_guard",
            new=AsyncMock(return_value=decision),
        ) as model_guard:
            db = FakeDB()
            with self.assertRaises(OfferCreationValidationError):
                await create_authoritative_offer_with_outcome(
                    db,
                    command,
                    validate_market=False,
                    commit=False,
                    refresh=False,
                )
            self.assertEqual(db.added, [])

            sync_command = replace(
                command,
                source_surface=OfferSourceSurface.INTERNAL_SYNC,
                offer_public_id="ofr_synced_price_guard_bypass",
                incoming_home_server="foreign",
            )
            await create_authoritative_offer_with_outcome(
                db,
                sync_command,
                validate_market=False,
                commit=False,
                refresh=False,
            )

        model_guard.assert_awaited_once()

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
