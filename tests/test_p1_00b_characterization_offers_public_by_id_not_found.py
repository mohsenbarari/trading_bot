"""Hermetic characterization test for get_public_offer_by_public_id missing public offer."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    _FENCE,
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException, status
from sqlalchemy.sql import Select

from api.routers.offers import get_public_offer_by_public_id
from models.offer import Offer

PUBLIC_ID = "ofr_public_identifier_12345"


def _has_commodity_option(stmt: Any) -> bool:
    options = getattr(stmt, "_with_options", ())
    for opt in options:
        path = getattr(opt, "path", None)
        if path is not None:
            if "commodity" in str(path).lower() or "commodity" in repr(path).lower():
                return True
            try:
                for elem in path:
                    if elem is Offer.commodity or getattr(elem, "key", None) == "commodity":
                        return True
                    if "commodity" in str(elem).lower() or "commodity" in repr(elem).lower():
                        return True
            except TypeError:
                pass
        for attr in ("_path_tokens", "_to_bind", "prop", "context"):
            val = getattr(opt, attr, None)
            if val is not None and ("commodity" in str(val).lower() or "commodity" in repr(val).lower()):
                return True
    return False


class MissingPublicOfferResult:
    """Dedicated result returning None for scalar_one_or_none."""

    def __init__(self) -> None:
        self.scalar_one_or_none_calls: int = 0

    def scalar_one_or_none(self) -> None:
        self.scalar_one_or_none_calls += 1
        return None


class MissingPublicOfferDB:
    """Query-specific async DB adapter for missing public offer lookup."""

    def __init__(self, expected_public_id: str) -> None:
        self.expected_public_id = expected_public_id
        self.statements: list[Any] = []
        self.result = MissingPublicOfferResult()

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> MissingPublicOfferResult:
        self.statements.append(stmt)
        if len(self.statements) > 1:
            raise AssertionError(f"Unexpected additional execute call: {stmt}")

        sql_text = str(stmt)
        if "offers.offer_public_id" not in sql_text or "offers" not in sql_text:
            raise AssertionError(f"Statement is not public-id Offer query: {sql_text}")

        if not _has_commodity_option(stmt):
            raise AssertionError("Statement lacks commodity selectinload option contract")

        compiled = stmt.compile()
        if not any(val == self.expected_public_id for val in compiled.params.values()):
            raise AssertionError(
                f"Statement params do not match expected public id {self.expected_public_id}: {compiled.params}"
            )

        return self.result


class OffersPublicByIdNotFoundCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_loader_missing_public_offer_raises_exact_404(self) -> None:
        fence_connects_before = len(_FENCE["denied_connects"])
        fence_constructs_before = len(_FENCE["live_redis_constructs"])

        expected_id = PUBLIC_ID
        self.assertIs(expected_id, PUBLIC_ID)
        self.assertEqual(expected_id, "ofr_public_identifier_12345")

        db = MissingPublicOfferDB(expected_public_id=expected_id)
        dedicated_result = db.result
        self.assertIs(db.result, dedicated_result)

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(),
        ) as mock_get_trading_settings, patch(
            "api.routers.offers._build_public_offer_response",
            new=Mock(),
        ) as mock_build_response:
            with self.assertRaises(HTTPException) as ctx:
                await get_public_offer_by_public_id(expected_id, db=db)

            self.assertEqual(ctx.exception.status_code, status.HTTP_404_NOT_FOUND)
            self.assertEqual(ctx.exception.detail, "لفظ یافت نشد.")

            mock_get_trading_settings.assert_not_called()
            mock_build_response.assert_not_called()

        self.assertEqual(len(db.statements), 1)
        executed_stmt = db.statements[0]
        self.assertIsInstance(executed_stmt, Select)
        self.assertIn("offers.offer_public_id", str(executed_stmt))
        self.assertIn("offers", str(executed_stmt))

        self.assertTrue(
            _has_commodity_option(executed_stmt),
            "Expected Offer.commodity selectinload option in executed statement",
        )

        compiled = executed_stmt.compile()
        self.assertIn(expected_id, compiled.params.values())

        self.assertEqual(dedicated_result.scalar_one_or_none_calls, 1)

        self.assertEqual(len(_FENCE["denied_connects"]), fence_connects_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), fence_constructs_before)


if __name__ == "__main__":
    unittest.main()
