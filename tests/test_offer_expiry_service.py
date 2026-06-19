import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.offer_expiry_service import (
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpirySourceSurface,
    OfferNotAuthoritativeError,
    expire_offer_authoritatively,
)
from models.offer import OfferStatus


class OfferExpiryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_authoritative_expiry_records_owner_metadata_and_commits(self):
        db = SimpleNamespace(commit=AsyncMock())
        offer = SimpleNamespace(id=1, status=OfferStatus.ACTIVE, home_server="iran")

        with patch("core.services.offer_expiry_service.current_server", return_value="iran"):
            result = await expire_offer_authoritatively(
                db,
                offer,
                OfferExpiryCommand(
                    reason=OfferExpiryReason.MANUAL,
                    source_surface=OfferExpirySourceSurface.WEBAPP,
                    source_server="iran",
                    expired_by_user_id=10,
                    expired_by_actor_user_id=20,
                ),
            )

        self.assertEqual(result.expired_count, 1)
        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertIsNotNone(offer.expired_at)
        self.assertEqual(offer.expire_reason, "manual")
        self.assertEqual(offer.expired_by_user_id, 10)
        self.assertEqual(offer.expired_by_actor_user_id, 20)
        self.assertEqual(offer.expire_source_surface, "webapp")
        self.assertEqual(offer.expire_source_server, "iran")
        db.commit.assert_awaited_once()

    async def test_non_authoritative_expiry_is_rejected_without_local_mutation(self):
        db = SimpleNamespace(commit=AsyncMock())
        offer = SimpleNamespace(id=2, status=OfferStatus.ACTIVE, home_server="foreign")

        with patch("core.services.offer_expiry_service.current_server", return_value="iran"):
            with self.assertRaises(OfferNotAuthoritativeError):
                await expire_offer_authoritatively(
                    db,
                    offer,
                    OfferExpiryCommand(
                        reason=OfferExpiryReason.MANUAL,
                        source_surface=OfferExpirySourceSurface.WEBAPP,
                        source_server="iran",
                        expired_by_user_id=10,
                        expired_by_actor_user_id=10,
                    ),
                )

        self.assertEqual(offer.status, OfferStatus.ACTIVE)
        self.assertFalse(hasattr(offer, "expired_at"))
        db.commit.assert_not_awaited()

    async def test_system_recovery_finalization_has_no_false_user_actor(self):
        db = SimpleNamespace(commit=AsyncMock())
        offer = SimpleNamespace(id=3, status=OfferStatus.ACTIVE, home_server="foreign")

        with patch("core.services.offer_expiry_service.current_server", return_value="foreign"):
            await expire_offer_authoritatively(
                db,
                offer,
                OfferExpiryCommand(
                    reason=OfferExpiryReason.RECOVERY_FINALIZATION,
                    source_surface=OfferExpirySourceSurface.SYSTEM,
                    source_server="foreign",
                    expired_by_user_id=None,
                    expired_by_actor_user_id=None,
                ),
            )

        self.assertEqual(offer.expire_reason, "recovery_finalization")
        self.assertEqual(offer.expire_source_surface, "system")
        self.assertEqual(offer.expire_source_server, "foreign")
        self.assertIsNone(offer.expired_by_user_id)
        self.assertIsNone(offer.expired_by_actor_user_id)


if __name__ == "__main__":
    unittest.main()
