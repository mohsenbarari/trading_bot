import unittest

from core.offer_source import (
    OfferSourceSurface,
    offer_home_server_for_source,
)


class OfferSourceSurfaceTests(unittest.TestCase):
    def test_offer_home_server_is_decided_by_source_surface(self):
        self.assertEqual(offer_home_server_for_source(OfferSourceSurface.WEBAPP), "iran")
        self.assertEqual(offer_home_server_for_source(OfferSourceSurface.TELEGRAM_BOT), "foreign")
        self.assertEqual(
            offer_home_server_for_source(OfferSourceSurface.INTERNAL_SYNC, incoming_home_server="iran"),
            "iran",
        )
        self.assertEqual(
            offer_home_server_for_source(OfferSourceSurface.INTERNAL_SYNC, incoming_home_server="foreign"),
            "foreign",
        )

    def test_invalid_or_missing_source_surface_fails_closed(self):
        for value in (None, "", "mobile_app"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    offer_home_server_for_source(value)

    def test_internal_sync_requires_incoming_home_server(self):
        with self.assertRaises(ValueError):
            offer_home_server_for_source(OfferSourceSurface.INTERNAL_SYNC)


if __name__ == "__main__":
    unittest.main()
