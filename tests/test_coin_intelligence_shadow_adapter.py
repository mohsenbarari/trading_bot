from __future__ import annotations

import unittest
from unittest.mock import patch

from core.config import Settings
from core.market_intelligence import shadow


class FailingService:
    def observe_implicit_commodity(self, **_kwargs):
        raise RuntimeError("must not reach the parser")


class CoinIntelligenceShadowAdapterTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_shadow_feature_is_disabled_by_default(self) -> None:
        self.assertFalse(
            Settings.model_fields[
                "coin_intelligence_shadow_enabled"
            ].default
        )

    async def test_disabled_adapter_is_a_noop(self) -> None:
        with patch.object(
            shadow,
            "_configured_service",
            return_value=None,
        ), patch.object(
            shadow,
            "record_shadow_observation",
        ) as record:
            await shadow.observe_implicit_commodity_shadow(
                price=75_800,
                settlement="cash",
                current_commodity="امام",
            )

        record.assert_not_called()

    async def test_unexpected_runtime_failure_is_contained_and_counted(
        self,
    ) -> None:
        recorded = []
        with patch.object(
            shadow,
            "_configured_service",
            return_value=FailingService(),
        ), patch.object(
            shadow,
            "record_shadow_observation",
            side_effect=recorded.append,
        ):
            await shadow.observe_implicit_commodity_shadow(
                price=75_800,
                settlement="cash",
                current_commodity="امام",
            )

        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0].status, "RUNTIME_ERROR")
        self.assertEqual(recorded[0].current_commodity, "امام")
        self.assertIsNone(recorded[0].inferred_commodity)


if __name__ == "__main__":
    unittest.main()
