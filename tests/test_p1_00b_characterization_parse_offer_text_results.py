"""
Characterization tests for parse_offer_text results under Stage P1-00b.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides handler-result characterization only for the three outcome
paths of api.routers.offers.parse_offer_text (explicit-commodity success, parser
error response, and no-result response) with coin_intelligence inference disabled.
This is characterization only and does not close the seed, gap, or Stage.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Install fail-closed network fence before importing router under test
install_p1_00_network_fence()

from api.deps import EffectiveOwnerActor
from api.routers.offers import ParseOfferRequest, ParseOfferResponse, parse_offer_text
from bot.utils.offer_parser import ParseError, ParsedOffer
from core.config import settings
from core.enums import SettlementType, UserRole


class StrictFakeDB:
    """Hermetic fake DB session recording all operations and denying unexpected calls."""

    def __init__(self) -> None:
        self.call_log: list[str] = []

    async def execute(self, *args, **kwargs):
        self.call_log.append("execute")
        raise AssertionError("StrictFakeDB.execute must not be called")

    async def commit(self, *args, **kwargs):
        self.call_log.append("commit")
        raise AssertionError("StrictFakeDB.commit must not be called")

    async def rollback(self, *args, **kwargs):
        self.call_log.append("rollback")
        raise AssertionError("StrictFakeDB.rollback must not be called")

    def __getattr__(self, name: str):
        def _unexpected(*args, **kwargs):
            self.call_log.append(name)
            raise AssertionError(f"StrictFakeDB.{name} must not be called")

        return _unexpected


def _make_context(owner_id: int = 77, actor_id: int | None = None) -> EffectiveOwnerActor:
    owner_user = SimpleNamespace(id=owner_id, role=UserRole.STANDARD)
    actor_user = SimpleNamespace(id=actor_id if actor_id is not None else owner_id, role=UserRole.STANDARD)
    return EffectiveOwnerActor(
        owner_user=owner_user,  # type: ignore[arg-type]
        actor_user=actor_user,  # type: ignore[arg-type]
        relation=None,
        is_accountant_context=owner_user.id != actor_user.id,
    )


class TestP100bParseOfferTextResults(unittest.IsolatedAsyncioTestCase):
    """Hermetic live characterization tests for api.routers.offers.parse_offer_text outcomes."""

    async def test_parse_offer_text_explicit_commodity_success(self) -> None:
        context = _make_context(owner_id=77)
        db = StrictFakeDB()
        request = ParseOfferRequest(text="فروش 5 سکه امامی نقد 45000000 : تحویل فوری")

        parsed_offer = ParsedOffer(
            trade_type="sell",
            commodity_id=12,
            commodity_name="سکه امامی",
            quantity=5,
            price=45000000,
            is_wholesale=False,
            lot_sizes=[2, 3],
            notes="تحویل فوری",
            settlement_type=SettlementType.CASH.value,
            commodity_resolution="EXPLICIT",
            low_date_hint=False,
            pack_hint=False,
        )

        with patch.object(
            settings, "coin_intelligence_inference_preview_enabled", False
        ), patch.object(
            settings, "coin_intelligence_inference_selection_enabled", False
        ), patch(
            "bot.utils.offer_parser.parse_offer_text",
            new_callable=AsyncMock,
        ) as mock_parser, patch(
            "api.routers.offers._selection_inference_for_missing_commodity",
            new_callable=AsyncMock,
        ) as mock_selection, patch(
            "api.routers.offers._shadow_inference_for_implicit_commodity",
            new_callable=AsyncMock,
        ) as mock_shadow:
            mock_parser.return_value = (parsed_offer, None)

            response = await parse_offer_text(request=request, context=context, db=db)

            # Assert parser was awaited exactly once with only request.text and no capture keyword
            mock_parser.assert_awaited_once_with(request.text)
            self.assertEqual(mock_parser.await_count, 1)
            args, kwargs = mock_parser.await_args
            self.assertEqual(args, (request.text,))
            self.assertEqual(kwargs, {})
            self.assertNotIn("capture_commodity_resolution", kwargs)

            # Assert both inference helpers were neither called nor awaited
            mock_selection.assert_not_called()
            mock_shadow.assert_not_called()
            self.assertEqual(mock_selection.await_count, 0)
            self.assertEqual(mock_shadow.await_count, 0)

            # Assert no DB or Offer mutation occurred
            self.assertEqual(db.call_log, [])

            # Assert exact real ParseOfferResponse content for every returned data field and flag
            self.assertIsInstance(response, ParseOfferResponse)
            self.assertTrue(response.success)
            self.assertIsNone(response.error)
            self.assertIsInstance(response.data, dict)

            expected_data = {
                "trade_type": "sell",
                "settlement_type": "cash",
                "commodity_id": 12,
                "commodity_name": "سکه امامی",
                "commodity_resolution": "EXPLICIT",
                "low_date_hint": False,
                "pack_hint": False,
                "quantity": 5,
                "price": 45000000,
                "is_wholesale": False,
                "lot_sizes": [2, 3],
                "notes": "تحویل فوری",
            }
            self.assertEqual(response.data, expected_data)
            self.assertEqual(response.data["trade_type"], "sell")
            self.assertEqual(response.data["settlement_type"], "cash")
            self.assertEqual(response.data["commodity_id"], 12)
            self.assertEqual(response.data["commodity_name"], "سکه امامی")
            self.assertEqual(response.data["commodity_resolution"], "EXPLICIT")
            self.assertIs(response.data["low_date_hint"], False)
            self.assertIs(response.data["pack_hint"], False)
            self.assertEqual(response.data["quantity"], 5)
            self.assertEqual(response.data["price"], 45000000)
            self.assertIs(response.data["is_wholesale"], False)
            self.assertEqual(response.data["lot_sizes"], [2, 3])
            self.assertEqual(response.data["notes"], "تحویل فوری")
            self.assertNotIn("commodity_inference", response.data)

    async def test_parse_offer_text_parser_error(self) -> None:
        context = _make_context(owner_id=77)
        db = StrictFakeDB()
        request = ParseOfferRequest(text="متن نامعتبر برای آفر")
        error_message = "❌ قیمت یافت نشد"

        with patch.object(
            settings, "coin_intelligence_inference_preview_enabled", False
        ), patch.object(
            settings, "coin_intelligence_inference_selection_enabled", False
        ), patch(
            "bot.utils.offer_parser.parse_offer_text",
            new_callable=AsyncMock,
        ) as mock_parser, patch(
            "api.routers.offers._selection_inference_for_missing_commodity",
            new_callable=AsyncMock,
        ) as mock_selection, patch(
            "api.routers.offers._shadow_inference_for_implicit_commodity",
            new_callable=AsyncMock,
        ) as mock_shadow:
            mock_parser.return_value = (None, ParseError(message=error_message))

            response = await parse_offer_text(request=request, context=context, db=db)

            # Assert parser was awaited exactly once with only request.text and no capture keyword
            mock_parser.assert_awaited_once_with(request.text)
            self.assertEqual(mock_parser.await_count, 1)
            args, kwargs = mock_parser.await_args
            self.assertEqual(args, (request.text,))
            self.assertEqual(kwargs, {})
            self.assertNotIn("capture_commodity_resolution", kwargs)

            # Assert both inference helpers were neither called nor awaited
            mock_selection.assert_not_called()
            mock_shadow.assert_not_called()
            self.assertEqual(mock_selection.await_count, 0)
            self.assertEqual(mock_shadow.await_count, 0)

            # Assert empty StrictFakeDB operation log
            self.assertEqual(db.call_log, [])

            # Assert exact real failure response
            self.assertIsInstance(response, ParseOfferResponse)
            self.assertFalse(response.success)
            self.assertEqual(response.error, error_message)
            self.assertIsNone(response.data)

    async def test_parse_offer_text_no_result(self) -> None:
        context = _make_context(owner_id=77)
        db = StrictFakeDB()
        request = ParseOfferRequest(text="متن بدون نتیجه و بدون خطا")

        with patch.object(
            settings, "coin_intelligence_inference_preview_enabled", False
        ), patch.object(
            settings, "coin_intelligence_inference_selection_enabled", False
        ), patch(
            "bot.utils.offer_parser.parse_offer_text",
            new_callable=AsyncMock,
        ) as mock_parser, patch(
            "api.routers.offers._selection_inference_for_missing_commodity",
            new_callable=AsyncMock,
        ) as mock_selection, patch(
            "api.routers.offers._shadow_inference_for_implicit_commodity",
            new_callable=AsyncMock,
        ) as mock_shadow:
            mock_parser.return_value = (None, None)

            response = await parse_offer_text(request=request, context=context, db=db)

            # Assert parser was awaited exactly once with only request.text and no capture keyword
            mock_parser.assert_awaited_once_with(request.text)
            self.assertEqual(mock_parser.await_count, 1)
            args, kwargs = mock_parser.await_args
            self.assertEqual(args, (request.text,))
            self.assertEqual(kwargs, {})
            self.assertNotIn("capture_commodity_resolution", kwargs)

            # Assert both inference helpers were neither called nor awaited
            mock_selection.assert_not_called()
            mock_shadow.assert_not_called()
            self.assertEqual(mock_selection.await_count, 0)
            self.assertEqual(mock_shadow.await_count, 0)

            # Assert empty StrictFakeDB operation log
            self.assertEqual(db.call_log, [])

            # Assert exact real failure response
            self.assertIsInstance(response, ParseOfferResponse)
            self.assertFalse(response.success)
            self.assertEqual(response.error, "متن قابل تشخیص نیست.")
            self.assertIsNone(response.data)


if __name__ == "__main__":
    unittest.main()
