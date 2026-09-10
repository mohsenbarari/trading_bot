"""Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card: P1-00b
Assignment ID: WA-P1-00B-FREEZE-20260904-001

Characterize the two helper-level early configuration exits of the real
api.routers.offers._selection_inference_for_missing_commodity under P1-00b.
This evidence is strictly limited to helper-level early exits, not handler/seed
or Stage closure.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from contextlib import contextmanager
from typing import Any, Iterator
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import api.routers.offers as offers_module
from api.routers.offers import _selection_inference_for_missing_commodity
from core.market_intelligence.product_snapshot_reader import (
    configured_product_snapshot_authority_path,
)

EXPECTED_UNCONFIGURED_PAYLOAD: dict[str, Any] = {
    "mode": "SELECTABLE",
    "status": "ABSTAIN",
    "decision_key": None,
    "snapshot_generated_at_utc": None,
    "snapshot_receipt": None,
    "reason": "SNAPSHOT_PATH_UNCONFIGURED",
    "candidates": [],
}


class SyntheticResolverError(RuntimeError):
    """Synthetic exception representing authority path resolution failure."""


class SyntheticSettings:
    """Isolated synthetic settings container for selection configuration exit tests."""

    def __init__(
        self,
        *,
        product_estimator_snapshot_mode: str = "LEGACY",
        coin_intelligence_inference_snapshot_path: str = "",
        product_estimator_private_primary_snapshot_path: str = "",
        coin_intelligence_inference_auto_selection_enabled: bool = False,
    ) -> None:
        self.product_estimator_snapshot_mode = product_estimator_snapshot_mode
        self.coin_intelligence_inference_snapshot_path = (
            coin_intelligence_inference_snapshot_path
        )
        self.product_estimator_private_primary_snapshot_path = (
            product_estimator_private_primary_snapshot_path
        )
        self.coin_intelligence_inference_auto_selection_enabled = (
            coin_intelligence_inference_auto_selection_enabled
        )


class StrictFakeAsyncSession:
    """Strict fake async session exposing only commit and rollback as AsyncMocks,

    rejecting any unexpected attribute access to guarantee complete isolation.
    """

    def __init__(self) -> None:
        self.commit = AsyncMock(name="StrictFakeAsyncSession.commit")
        self.rollback = AsyncMock(name="StrictFakeAsyncSession.rollback")

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"StrictFakeAsyncSession rejected unexpected DB attribute access: {name!r}"
        )


@contextmanager
def _selection_inference_spies(
    *,
    settings_obj: Any,
    resolver_spy: MagicMock,
) -> Iterator[dict[str, Any]]:
    reader_factory_spy = MagicMock(name="_configured_product_snapshot_reader_spy")
    datetime_mock = MagicMock(name="datetime_mock")
    observation_spy = AsyncMock(name="observe_coin_inference_shadow_spy")
    payload_formatter_spy = MagicMock(name="_catalog_inference_shadow_payload_spy")
    log_trading_event_spy = MagicMock(name="log_trading_event_spy")

    with patch.object(offers_module, "settings", settings_obj), \
         patch.object(
             offers_module,
             "configured_product_snapshot_authority_path",
             resolver_spy,
         ), \
         patch.object(
             offers_module,
             "_configured_product_snapshot_reader",
             reader_factory_spy,
         ), \
         patch.object(offers_module, "datetime", datetime_mock), \
         patch.object(
             offers_module,
             "observe_coin_inference_shadow",
             observation_spy,
         ), \
         patch.object(
             offers_module,
             "_catalog_inference_shadow_payload",
             payload_formatter_spy,
         ), \
         patch.object(
             offers_module,
             "log_trading_event",
             log_trading_event_spy,
         ):
        yield {
            "resolver_spy": resolver_spy,
            "reader_factory_spy": reader_factory_spy,
            "datetime_mock": datetime_mock,
            "observation_spy": observation_spy,
            "payload_formatter_spy": payload_formatter_spy,
            "log_trading_event_spy": log_trading_event_spy,
        }


class SelectionInferenceConfigurationExitsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def _assert_suppression(
        self,
        spies: dict[str, Any],
        strict_session: StrictFakeAsyncSession,
    ) -> None:
        reader_factory_spy = spies["reader_factory_spy"]
        datetime_mock = spies["datetime_mock"]
        observation_spy = spies["observation_spy"]
        payload_formatter_spy = spies["payload_formatter_spy"]
        log_trading_event_spy = spies["log_trading_event_spy"]

        reader_factory_spy.assert_not_called()
        self.assertEqual(reader_factory_spy.call_count, 0)

        datetime_mock.now.assert_not_called()
        self.assertEqual(datetime_mock.now.call_count, 0)

        observation_spy.assert_not_called()
        observation_spy.assert_not_awaited()
        self.assertEqual(observation_spy.call_count, 0)
        self.assertEqual(observation_spy.await_count, 0)

        payload_formatter_spy.assert_not_called()
        self.assertEqual(payload_formatter_spy.call_count, 0)

        log_trading_event_spy.assert_not_called()
        self.assertEqual(log_trading_event_spy.call_count, 0)

        strict_session.commit.assert_not_called()
        strict_session.commit.assert_not_awaited()
        self.assertEqual(strict_session.commit.call_count, 0)
        self.assertEqual(strict_session.commit.await_count, 0)

        strict_session.rollback.assert_not_called()
        strict_session.rollback.assert_not_awaited()
        self.assertEqual(strict_session.rollback.call_count, 0)
        self.assertEqual(strict_session.rollback.await_count, 0)

    async def test_empty_path_legacy_mode_whitespace(self) -> None:
        """LEGACY mode with whitespace snapshot path returns unconfigured fallback."""
        synthetic_settings = SyntheticSettings(
            product_estimator_snapshot_mode="LEGACY",
            coin_intelligence_inference_snapshot_path="   ",
            product_estimator_private_primary_snapshot_path="",
        )
        resolver_spy = MagicMock(
            wraps=configured_product_snapshot_authority_path
        )
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            result = await _selection_inference_for_missing_commodity(
                strict_session,
                price=50_000_000,
                settlement_type="cash",
                low_date_hint=False,
                pack_hint=False,
            )

        self.assertEqual(result, EXPECTED_UNCONFIGURED_PAYLOAD)
        self.assertEqual(len(result), 7)
        self.assertEqual(
            set(result.keys()),
            {
                "mode",
                "status",
                "decision_key",
                "snapshot_generated_at_utc",
                "snapshot_receipt",
                "reason",
                "candidates",
            },
        )
        self.assertIsNone(result["decision_key"])
        self.assertIsNone(result["snapshot_generated_at_utc"])
        self.assertIsNone(result["snapshot_receipt"])
        self.assertEqual(result["reason"], "SNAPSHOT_PATH_UNCONFIGURED")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["mode"], "SELECTABLE")
        self.assertEqual(result["status"], "ABSTAIN")

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self._assert_suppression(spies, strict_session)

    async def test_empty_path_legacy_mode_empty_string(self) -> None:
        """LEGACY mode with empty string snapshot path returns unconfigured fallback."""
        synthetic_settings = SyntheticSettings(
            product_estimator_snapshot_mode="LEGACY",
            coin_intelligence_inference_snapshot_path="",
            product_estimator_private_primary_snapshot_path="",
        )
        resolver_spy = MagicMock(
            wraps=configured_product_snapshot_authority_path
        )
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            result = await _selection_inference_for_missing_commodity(
                strict_session,
                price=50_000_000,
                settlement_type="tomorrow",
                low_date_hint=True,
                pack_hint=False,
            )

        self.assertEqual(result, EXPECTED_UNCONFIGURED_PAYLOAD)
        self.assertEqual(len(result), 7)
        self.assertEqual(
            set(result.keys()),
            {
                "mode",
                "status",
                "decision_key",
                "snapshot_generated_at_utc",
                "snapshot_receipt",
                "reason",
                "candidates",
            },
        )
        self.assertIsNone(result["decision_key"])
        self.assertIsNone(result["snapshot_generated_at_utc"])
        self.assertIsNone(result["snapshot_receipt"])
        self.assertEqual(result["reason"], "SNAPSHOT_PATH_UNCONFIGURED")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["mode"], "SELECTABLE")
        self.assertEqual(result["status"], "ABSTAIN")

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self._assert_suppression(spies, strict_session)

    async def test_empty_path_private_primary_mode_does_not_fall_back(self) -> None:
        """PRIVATE_PRIMARY with whitespace path does not fall back to nonempty legacy path."""
        synthetic_settings = SyntheticSettings(
            product_estimator_snapshot_mode="PRIVATE_PRIMARY",
            product_estimator_private_primary_snapshot_path="   ",
            coin_intelligence_inference_snapshot_path="/synthetic/legacy/authority_snapshot.json",
        )
        resolver_spy = MagicMock(
            wraps=configured_product_snapshot_authority_path
        )
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            result = await _selection_inference_for_missing_commodity(
                strict_session,
                price=75_000_000,
                settlement_type="cash",
                low_date_hint=False,
                pack_hint=True,
            )

        self.assertEqual(result, EXPECTED_UNCONFIGURED_PAYLOAD)
        self.assertEqual(len(result), 7)
        self.assertEqual(
            set(result.keys()),
            {
                "mode",
                "status",
                "decision_key",
                "snapshot_generated_at_utc",
                "snapshot_receipt",
                "reason",
                "candidates",
            },
        )
        self.assertIsNone(result["decision_key"])
        self.assertIsNone(result["snapshot_generated_at_utc"])
        self.assertIsNone(result["snapshot_receipt"])
        self.assertEqual(result["reason"], "SNAPSHOT_PATH_UNCONFIGURED")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["mode"], "SELECTABLE")
        self.assertEqual(result["status"], "ABSTAIN")

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self._assert_suppression(spies, strict_session)

    async def test_resolver_failure_escapes_outside_try_block(self) -> None:
        """Resolver exception escapes directly outside try block without being swallowed."""
        synthetic_settings = SyntheticSettings(
            product_estimator_snapshot_mode="LEGACY",
            coin_intelligence_inference_snapshot_path="/synthetic/path.json",
            product_estimator_private_primary_snapshot_path="",
        )
        synthetic_exc = SyntheticResolverError(
            "synthetic-authority-resolver-failure-sentinel"
        )
        resolver_spy = MagicMock(side_effect=synthetic_exc)
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            with self.assertRaises(SyntheticResolverError) as ctx:
                await _selection_inference_for_missing_commodity(
                    strict_session,
                    price=50_000_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )

            self.assertIs(ctx.exception, synthetic_exc)

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self._assert_suppression(spies, strict_session)


if __name__ == "__main__":
    unittest.main()
