from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot.utils import offer_parser


TRADING_SETTINGS = SimpleNamespace(
    offer_min_quantity=5,
    offer_max_quantity=75,
    lot_min_size=5,
    lot_max_count=4,
)


class OfferParserCoinIntelligenceShadowTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_implicit_imam_rule_is_preserved_and_shadow_observed(
        self,
    ) -> None:
        observer = Mock(return_value=True)
        with patch.object(
            offer_parser,
            "get_trading_settings",
            return_value=TRADING_SETTINGS,
        ), patch.object(
            offer_parser,
            "find_commodity",
            new=AsyncMock(return_value=(41, "امام")),
        ), patch(
            "core.market_intelligence.shadow."
            "schedule_implicit_commodity_shadow",
            new=observer,
        ), patch(
            "core.market_intelligence.shadow.schedule_gemma_parser_shadow",
            new=Mock(return_value=True),
        ) as gemma:
            parsed, error = await offer_parser.parse_offer_text(
                "خ ن 30تا 75800"
            )

        self.assertIsNone(error)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.commodity_id, 41)
        self.assertEqual(parsed.commodity_name, "امام")
        self.assertFalse(parsed.commodity_was_explicit)
        observer.assert_called_once_with(
            price=75800,
            settlement="cash",
            current_commodity="امام",
        )
        gemma.assert_called_once()

    async def test_explicit_commodity_does_not_enter_implicit_shadow(
        self,
    ) -> None:
        observer = Mock(return_value=True)
        with patch.object(
            offer_parser,
            "get_trading_settings",
            return_value=TRADING_SETTINGS,
        ), patch.object(
            offer_parser,
            "find_commodity",
            new=AsyncMock(return_value=(41, "امام")),
        ), patch(
            "core.market_intelligence.shadow."
            "schedule_implicit_commodity_shadow",
            new=observer,
        ), patch(
            "core.market_intelligence.shadow.schedule_gemma_parser_shadow",
            new=Mock(return_value=True),
        ) as gemma:
            parsed, error = await offer_parser.parse_offer_text(
                "خ ن امام 30تا 75800"
            )

        self.assertIsNone(error)
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.commodity_was_explicit)
        observer.assert_not_called()
        gemma.assert_called_once_with(
            text="خ ن امام 30تا 75800",
            side="buy",
            settlement="cash",
            quantity=30,
            price=75800,
            current_commodity="امام",
        )


if __name__ == "__main__":
    unittest.main()
