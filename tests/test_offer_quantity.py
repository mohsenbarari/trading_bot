import unittest

from core.offer_quantity import coalesce_offer_remaining_quantity


class OfferQuantityTests(unittest.TestCase):
    def test_only_none_falls_back_to_initial_quantity(self):
        cases = (
            (None, 20, 20),
            (0, 20, 0),
            (8, 20, 8),
            (20, 20, 20),
        )
        for remaining, quantity, expected in cases:
            with self.subTest(remaining=remaining, quantity=quantity):
                self.assertEqual(
                    coalesce_offer_remaining_quantity(remaining, quantity),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
