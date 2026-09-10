"""Hermetic characterization tests for api.routers.offers.parse_offer_text escaping exceptions.

Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Tracked gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001

Evidence limits:
Freezes handler-level escaping exception propagation only for parser and the two
inference helpers. Does not prove parser correctness, helper catch/rollback behavior,
DB durability, HTTP dependency resolution, seed closure, or Stage acceptance.
P1-00 remains COMPLETE; P1-00b remains IN_PROGRESS; gap remains BLOCKED_EXPLICIT.
No closure claim.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.deps import EffectiveOwnerActor
from api.routers.offers import ParseOfferRequest, parse_offer_text
from bot.utils.offer_parser import ParsedOffer
from core.enums import SettlementType


class SyntheticParserError(Exception):
    """Synthetic exception simulating a failure escaping bot.utils.offer_parser.parse_offer_text."""


class SyntheticSelectionInferenceError(Exception):
    """Synthetic exception simulating a failure escaping _selection_inference_for_missing_commodity."""


class SyntheticShadowInferenceError(Exception):
    """Synthetic exception simulating a failure escaping _shadow_inference_for_implicit_commodity."""


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
    owner_user = SimpleNamespace(id=owner_id)
    actor_user = SimpleNamespace(id=actor_id if actor_id is not None else owner_id)
    return EffectiveOwnerActor(
        owner_user=owner_user,
        actor_user=actor_user,
        relation=None,
        is_accountant_context=owner_user.id != actor_user.id,
    )


class ParseOfferTextEscapingExceptionsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_parser_boundary_escape(
        self,
        *,
        preview_enabled: bool,
        selection_enabled: bool,
    ) -> None:
        synthetic_exc = SyntheticParserError(
            f"synthetic parser escape [preview={preview_enabled}, selection={selection_enabled}]"
        )
        fake_db = StrictFakeDB()
        context = _make_context(owner_id=77)
        request = ParseOfferRequest(text="خ ن 10تا 186800")

        parser_mock = AsyncMock(side_effect=synthetic_exc)
        selection_mock = AsyncMock()
        shadow_mock = AsyncMock()

        with (
            patch("bot.utils.offer_parser.parse_offer_text", parser_mock),
            patch("api.routers.offers._selection_inference_for_missing_commodity", selection_mock),
            patch("api.routers.offers._shadow_inference_for_implicit_commodity", shadow_mock),
            patch(
                "api.routers.offers.settings.coin_intelligence_inference_preview_enabled",
                preview_enabled,
            ),
            patch(
                "api.routers.offers.settings.coin_intelligence_inference_selection_enabled",
                selection_enabled,
            ),
            patch("api.routers.offers.ParseOfferResponse") as response_mock,
        ):
            with self.assertRaises(SyntheticParserError) as exc_ctx:
                await parse_offer_text(request, context=context, db=fake_db)

            self.assertIs(exc_ctx.exception, synthetic_exc)

            self.assertEqual(parser_mock.call_count, 1)
            self.assertEqual(parser_mock.await_count, 1)

            if not preview_enabled and not selection_enabled:
                parser_mock.assert_called_once_with(request.text)
                parser_mock.assert_awaited_once_with(request.text)
            else:
                parser_mock.assert_called_once_with(
                    request.text,
                    capture_commodity_resolution=True,
                )
                parser_mock.assert_awaited_once_with(
                    request.text,
                    capture_commodity_resolution=True,
                )

            selection_mock.assert_not_called()
            selection_mock.assert_not_awaited()
            shadow_mock.assert_not_called()
            shadow_mock.assert_not_awaited()

            response_mock.assert_not_called()
            self.assertEqual(fake_db.call_log, [])

    async def test_parser_boundary_escaping_exception_flag_matrix(self) -> None:
        """Requirement 2: parser escaping exception across all four flag combinations."""
        for preview_enabled, selection_enabled in (
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ):
            with self.subTest(preview_enabled=preview_enabled, selection_enabled=selection_enabled):
                await self._assert_parser_boundary_escape(
                    preview_enabled=preview_enabled,
                    selection_enabled=selection_enabled,
                )

    async def test_selection_boundary_escaping_exception_and_precedence(self) -> None:
        """Requirement 3: selection helper escaping exception with normalization and precedence."""
        synthetic_selection_exc = SyntheticSelectionInferenceError(
            "synthetic selection inference helper failure"
        )
        fake_db = StrictFakeDB()
        context = _make_context(owner_id=77)
        request = ParseOfferRequest(text="خ ف پ 10تا 186800")

        parsed_offer = ParsedOffer(
            trade_type="buy",
            commodity_id=None,
            commodity_name=None,
            quantity=10,
            price=186_800,
            is_wholesale=True,
            lot_sizes=None,
            notes="فوری",
            settlement_type=SettlementType.TOMORROW,
            commodity_resolution="LOW_DATE_HINT",
            low_date_hint=True,
            pack_hint=False,
        )

        order_log: list[str] = []

        async def fake_parser(*args, **kwargs):
            order_log.append("parser_called")
            order_log.append("parser_completed")
            return parsed_offer, None

        parser_mock = AsyncMock(side_effect=fake_parser)

        async def fake_selection(*args, **kwargs):
            order_log.append("selection_awaited")
            raise synthetic_selection_exc

        selection_mock = AsyncMock(side_effect=fake_selection)
        shadow_mock = AsyncMock()

        with (
            patch("bot.utils.offer_parser.parse_offer_text", parser_mock),
            patch("api.routers.offers._selection_inference_for_missing_commodity", selection_mock),
            patch("api.routers.offers._shadow_inference_for_implicit_commodity", shadow_mock),
            patch(
                "api.routers.offers.settings.coin_intelligence_inference_preview_enabled",
                True,
            ),
            patch(
                "api.routers.offers.settings.coin_intelligence_inference_selection_enabled",
                True,
            ),
            patch("api.routers.offers.ParseOfferResponse") as response_mock,
        ):
            with self.assertRaises(SyntheticSelectionInferenceError) as exc_ctx:
                await parse_offer_text(request, context=context, db=fake_db)

            self.assertIs(exc_ctx.exception, synthetic_selection_exc)

            self.assertEqual(parser_mock.call_count, 1)
            self.assertEqual(parser_mock.await_count, 1)
            parser_mock.assert_called_once_with(
                request.text,
                capture_commodity_resolution=True,
            )
            parser_mock.assert_awaited_once_with(
                request.text,
                capture_commodity_resolution=True,
            )

            self.assertEqual(selection_mock.call_count, 1)
            self.assertEqual(selection_mock.await_count, 1)
            selection_mock.assert_called_once_with(
                fake_db,
                price=186_800,
                settlement_type="tomorrow",
                low_date_hint=True,
                pack_hint=False,
            )
            selection_mock.assert_awaited_once_with(
                fake_db,
                price=186_800,
                settlement_type="tomorrow",
                low_date_hint=True,
                pack_hint=False,
            )
            self.assertIs(selection_mock.call_args[0][0], fake_db)
            self.assertIs(selection_mock.await_args[0][0], fake_db)
            self.assertEqual(selection_mock.call_args.kwargs["price"], 186_800)
            self.assertEqual(selection_mock.await_args.kwargs["price"], 186_800)
            self.assertEqual(selection_mock.call_args.kwargs["settlement_type"], "tomorrow")
            self.assertEqual(selection_mock.await_args.kwargs["settlement_type"], "tomorrow")
            self.assertIs(type(selection_mock.call_args.kwargs["settlement_type"]), str)
            self.assertIs(type(selection_mock.await_args.kwargs["settlement_type"]), str)
            self.assertIsNot(type(selection_mock.call_args.kwargs["settlement_type"]), SettlementType)
            self.assertIsNot(type(selection_mock.await_args.kwargs["settlement_type"]), SettlementType)
            self.assertIs(selection_mock.call_args.kwargs["low_date_hint"], True)
            self.assertIs(selection_mock.await_args.kwargs["low_date_hint"], True)
            self.assertIs(selection_mock.call_args.kwargs["pack_hint"], False)
            self.assertIs(selection_mock.await_args.kwargs["pack_hint"], False)

            self.assertEqual(order_log, ["parser_called", "parser_completed", "selection_awaited"])
            self.assertLess(
                order_log.index("parser_completed"),
                order_log.index("selection_awaited"),
            )

            shadow_mock.assert_not_called()
            shadow_mock.assert_not_awaited()

            response_mock.assert_not_called()
            self.assertEqual(fake_db.call_log, [])

    async def test_shadow_boundary_escaping_exception(self) -> None:
        """Requirement 4: shadow helper escaping exception with resolution=OMITTED."""
        synthetic_shadow_exc = SyntheticShadowInferenceError(
            "synthetic shadow inference helper failure"
        )
        fake_db = StrictFakeDB()
        context = _make_context(owner_id=77)
        request = ParseOfferRequest(text="خ ن 10تا 186800")

        parsed_offer = ParsedOffer(
            trade_type="buy",
            commodity_id=None,
            commodity_name=None,
            quantity=10,
            price=186_800,
            is_wholesale=True,
            lot_sizes=None,
            notes="عادی",
            settlement_type=SettlementType.TOMORROW,
            commodity_resolution="OMITTED",
            low_date_hint=False,
            pack_hint=False,
        )

        order_log: list[str] = []

        async def fake_parser(*args, **kwargs):
            order_log.append("parser_called")
            order_log.append("parser_completed")
            return parsed_offer, None

        parser_mock = AsyncMock(side_effect=fake_parser)

        async def fake_shadow(*args, **kwargs):
            order_log.append("shadow_awaited")
            raise synthetic_shadow_exc

        shadow_mock = AsyncMock(side_effect=fake_shadow)
        selection_mock = AsyncMock()

        with (
            patch("bot.utils.offer_parser.parse_offer_text", parser_mock),
            patch("api.routers.offers._selection_inference_for_missing_commodity", selection_mock),
            patch("api.routers.offers._shadow_inference_for_implicit_commodity", shadow_mock),
            patch(
                "api.routers.offers.settings.coin_intelligence_inference_preview_enabled",
                True,
            ),
            patch(
                "api.routers.offers.settings.coin_intelligence_inference_selection_enabled",
                False,
            ),
            patch("api.routers.offers.ParseOfferResponse") as response_mock,
        ):
            with self.assertRaises(SyntheticShadowInferenceError) as exc_ctx:
                await parse_offer_text(request, context=context, db=fake_db)

            self.assertIs(exc_ctx.exception, synthetic_shadow_exc)

            self.assertEqual(parser_mock.call_count, 1)
            self.assertEqual(parser_mock.await_count, 1)
            parser_mock.assert_called_once_with(
                request.text,
                capture_commodity_resolution=True,
            )
            parser_mock.assert_awaited_once_with(
                request.text,
                capture_commodity_resolution=True,
            )

            self.assertEqual(shadow_mock.call_count, 1)
            self.assertEqual(shadow_mock.await_count, 1)
            shadow_mock.assert_called_once_with(
                fake_db,
                price=186_800,
                settlement_type="tomorrow",
            )
            shadow_mock.assert_awaited_once_with(
                fake_db,
                price=186_800,
                settlement_type="tomorrow",
            )
            self.assertIs(shadow_mock.call_args[0][0], fake_db)
            self.assertIs(shadow_mock.await_args[0][0], fake_db)
            self.assertEqual(shadow_mock.call_args.kwargs["price"], 186_800)
            self.assertEqual(shadow_mock.await_args.kwargs["price"], 186_800)
            self.assertEqual(shadow_mock.call_args.kwargs["settlement_type"], "tomorrow")
            self.assertEqual(shadow_mock.await_args.kwargs["settlement_type"], "tomorrow")
            self.assertIs(type(shadow_mock.call_args.kwargs["settlement_type"]), str)
            self.assertIs(type(shadow_mock.await_args.kwargs["settlement_type"]), str)
            self.assertIsNot(type(shadow_mock.call_args.kwargs["settlement_type"]), SettlementType)
            self.assertIsNot(type(shadow_mock.await_args.kwargs["settlement_type"]), SettlementType)

            self.assertEqual(order_log, ["parser_called", "parser_completed", "shadow_awaited"])
            self.assertLess(
                order_log.index("parser_completed"),
                order_log.index("shadow_awaited"),
            )

            selection_mock.assert_not_called()
            selection_mock.assert_not_awaited()

            response_mock.assert_not_called()
            self.assertEqual(fake_db.call_log, [])


if __name__ == "__main__":
    unittest.main()
