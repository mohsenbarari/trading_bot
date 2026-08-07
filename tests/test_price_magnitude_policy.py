from __future__ import annotations

from decimal import Decimal
import unittest

from core.market_intelligence.market_contracts import (
    MarketObservation,
    MarketStoreContractError,
    derive_event_key,
)
from core.market_intelligence.price_magnitude_policy import (
    PriceUnitPolicyError,
    assert_project_toman_field,
    canonicalize_legacy_public_price,
    forbid_irt_unit,
)


class PriceMagnitudePolicyTests(unittest.TestCase):
    def test_forbid_irt_unit_names(self) -> None:
        with self.assertRaises(PriceUnitPolicyError):
            forbid_irt_unit("IRT_PER_USD")

    def test_legacy_melted_toman_label_is_kept(self) -> None:
        price, unit, currency, attrs = canonicalize_legacy_public_price(
            price=Decimal("80620000"),
            price_unit="IRT_PER_MESGHAL_750",
            currency="IRT",
        )
        self.assertEqual(price, Decimal("80620000"))
        self.assertEqual(unit, "TOMAN_PER_MESGHAL_750")
        self.assertEqual(currency, "TOMAN")
        self.assertIn("legacy_unit_relabeled_from", attrs)

    def test_legacy_true_rial_is_divided(self) -> None:
        price, unit, currency, attrs = canonicalize_legacy_public_price(
            price=Decimal("806200000"),
            price_unit="IRT_PER_MESGHAL_750",
            currency="IRT",
        )
        self.assertEqual(price, Decimal("80620000"))
        self.assertEqual(unit, "TOMAN_PER_MESGHAL_750")
        self.assertTrue(attrs.get("legacy_price_scale_fixed"))

    def test_contract_rejects_rial_scale_as_toman(self) -> None:
        with self.assertRaises(MarketStoreContractError):
            MarketObservation(
                event_key=derive_event_key("test", 1),
                source_code="MELTED_AGGREGATE",
                source_family="TELEGRAM_PUBLIC",
                event_time_utc="2026-08-05T10:00:00Z",
                available_at_utc="2026-08-05T10:00:00Z",
                instrument="MELTED_GOLD_AGGREGATE",
                market_label="PUBLIC_MELTED_GOLD_AGGREGATE",
                settlement_term="UNKNOWN",
                trade_form="PHYSICAL",
                event_type="QUOTE",
                side="MID",
                price=806_200_000,
                price_unit="TOMAN_PER_MESGHAL_750",
                currency="TOMAN",
            ).normalized()

    def test_contract_accepts_toman_mesghal(self) -> None:
        normalized = MarketObservation(
            event_key=derive_event_key("test", 2),
            source_code="MELTED_AGGREGATE",
            source_family="TELEGRAM_PUBLIC",
            event_time_utc="2026-08-05T10:00:00Z",
            available_at_utc="2026-08-05T10:00:00Z",
            instrument="MELTED_GOLD_AGGREGATE",
            market_label="PUBLIC_MELTED_GOLD_AGGREGATE",
            settlement_term="UNKNOWN",
            trade_form="PHYSICAL",
            event_type="QUOTE",
            side="MID",
            price=80_620_000,
            price_unit="TOMAN_PER_MESGHAL_750",
            currency="TOMAN",
        ).normalized()
        self.assertEqual(normalized.price_unit, "TOMAN_PER_MESGHAL_750")
        self.assertEqual(normalized.currency, "TOMAN")

    def test_project_field_rejects_full_toman(self) -> None:
        with self.assertRaises(PriceUnitPolicyError):
            assert_project_toman_field(185_000_000, field="price")
        self.assertEqual(assert_project_toman_field(185_000, field="price"), 185_000)


if __name__ == "__main__":
    unittest.main()
