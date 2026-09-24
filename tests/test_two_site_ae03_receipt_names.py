from __future__ import annotations

from types import MappingProxyType
import unittest

from scripts.two_site_codex_autopilot import _RESCOPE_RECEIPT_FILENAMES


class TestTwoSiteAe03ReceiptNames(unittest.TestCase):
    def test_exact_keys_values_and_immutability_type(self) -> None:
        self.assertIsInstance(_RESCOPE_RECEIPT_FILENAMES, MappingProxyType)
        expected = {
            "rescope_receipt.json": 1,
            "rescope_receipt_2.json": 2,
            "rescope_receipt_3.json": 3,
        }
        self.assertEqual(dict(_RESCOPE_RECEIPT_FILENAMES), expected)
        self.assertEqual(len(_RESCOPE_RECEIPT_FILENAMES), 3)
        self.assertEqual(list(_RESCOPE_RECEIPT_FILENAMES.keys()), list(expected.keys()))
        self.assertEqual(list(_RESCOPE_RECEIPT_FILENAMES.values()), [1, 2, 3])

    def test_item_mutation_and_broadening_rejected(self) -> None:
        with self.assertRaises(TypeError):
            _RESCOPE_RECEIPT_FILENAMES["rescope_receipt.json"] = 99  # type: ignore[index]
        with self.assertRaises(TypeError):
            _RESCOPE_RECEIPT_FILENAMES["rescope_receipt_4.json"] = 4  # type: ignore[index]
        with self.assertRaises(TypeError):
            del _RESCOPE_RECEIPT_FILENAMES["rescope_receipt.json"]  # type: ignore[misc]

    def test_mutation_methods_absent(self) -> None:
        self.assertFalse(hasattr(_RESCOPE_RECEIPT_FILENAMES, "clear"))
        self.assertFalse(hasattr(_RESCOPE_RECEIPT_FILENAMES, "update"))
        self.assertFalse(hasattr(_RESCOPE_RECEIPT_FILENAMES, "pop"))
        self.assertFalse(hasattr(_RESCOPE_RECEIPT_FILENAMES, "popitem"))
        self.assertEqual(len(_RESCOPE_RECEIPT_FILENAMES), 3)


if __name__ == "__main__":
    unittest.main()
