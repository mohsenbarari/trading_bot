"""Characterization tests for _shadow_inference_for_implicit_commodity configuration exits.

Tracked inventory gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed ID: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card path: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

This test module characterizes helper-level early configuration exits for
api.routers.offers._shadow_inference_for_implicit_commodity and proves only
helper-level configuration exits, not full handler/seed/Stage closure.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import api.routers.offers as offers_module
from api.routers.offers import _shadow_inference_for_implicit_commodity
from core.market_intelligence.product_snapshot_reader import (
    configured_product_snapshot_authority_path,
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


EXPECTED_UNCONFIGURED_RESULT: dict[str, Any] = {
    "mode": "SHADOW_ONLY",
    "status": "ABSTAIN",
    "decision_key": None,
    "snapshot_generated_at_utc": None,
    "snapshot_receipt": None,
    "reason": "SNAPSHOT_PATH_UNCONFIGURED",
    "candidates": [],
}


@contextmanager
def _shadow_inference_spies(
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


class ShadowConfigurationExitsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _assert_downstream_unreached(
        self,
        spies: dict[str, Any],
        session: StrictFakeAsyncSession,
    ) -> None:
        spies["reader_factory_spy"].assert_not_called()
        spies["datetime_mock"].now.assert_not_called()
        spies["observation_spy"].assert_not_called()
        spies["observation_spy"].assert_not_awaited()
        spies["payload_formatter_spy"].assert_not_called()
        spies["log_trading_event_spy"].assert_not_called()
        session.commit.assert_not_called()
        session.commit.assert_not_awaited()
        session.rollback.assert_not_called()
        session.rollback.assert_not_awaited()

    async def test_preview_disabled_returns_none_without_calling_resolver(self) -> None:
        session = StrictFakeAsyncSession()
        settings_obj = SimpleNamespace(
            coin_intelligence_inference_preview_enabled=False,
        )
        exploding_resolver = MagicMock(
            side_effect=AssertionError(
                "configured_product_snapshot_authority_path must not be called when preview is disabled"
            )
        )

        with _shadow_inference_spies(
            settings_obj=settings_obj,
            resolver_spy=exploding_resolver,
        ) as spies:
            result = await _shadow_inference_for_implicit_commodity(
                session,
                price=50_000_000,
                settlement_type="cash",
            )

        self.assertIsNone(result)
        exploding_resolver.assert_not_called()
        self._assert_downstream_unreached(spies, session)

    async def test_whitespace_legacy_authority_path_returns_snapshot_path_unconfigured(
        self,
    ) -> None:
        session = StrictFakeAsyncSession()
        settings_obj = SimpleNamespace(
            coin_intelligence_inference_preview_enabled=True,
            product_estimator_snapshot_mode="LEGACY",
            coin_intelligence_inference_snapshot_path="   \t  \n  ",
        )
        resolver_spy = MagicMock(wraps=configured_product_snapshot_authority_path)

        with _shadow_inference_spies(
            settings_obj=settings_obj,
            resolver_spy=resolver_spy,
        ) as spies:
            result = await _shadow_inference_for_implicit_commodity(
                session,
                price=75_000_000,
                settlement_type="tomorrow",
            )

        resolver_spy.assert_called_once_with(settings_obj)
        self.assertIs(resolver_spy.call_args.args[0], settings_obj)
        self.assertEqual(result, EXPECTED_UNCONFIGURED_RESULT)
        self._assert_downstream_unreached(spies, session)

    async def test_resolver_exception_propagates_unswallowed(self) -> None:
        session = StrictFakeAsyncSession()
        settings_obj = SimpleNamespace(
            coin_intelligence_inference_preview_enabled=True,
            product_estimator_snapshot_mode="LEGACY",
            coin_intelligence_inference_snapshot_path="/configured/path/to/snapshot.json",
        )
        synthetic_error = RuntimeError(
            "Synthetic resolver exception during snapshot authority lookup"
        )
        resolver_spy = MagicMock(side_effect=synthetic_error)

        with _shadow_inference_spies(
            settings_obj=settings_obj,
            resolver_spy=resolver_spy,
        ) as spies:
            with self.assertRaises(RuntimeError) as exc_context:
                await _shadow_inference_for_implicit_commodity(
                    session,
                    price=90_000_000,
                    settlement_type="cash",
                )

        self.assertIs(exc_context.exception, synthetic_error)
        resolver_spy.assert_called_once_with(settings_obj)
        self.assertIs(resolver_spy.call_args.args[0], settings_obj)
        self._assert_downstream_unreached(spies, session)


if __name__ == "__main__":
    unittest.main()
