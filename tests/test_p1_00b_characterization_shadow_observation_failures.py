"""Characterization tests for _shadow_inference_for_implicit_commodity observer failures.

Tracked inventory gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Gap ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed ID: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card path: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

This test module characterizes helper-level observer and rollback failure
boundaries for api.routers.offers._shadow_inference_for_implicit_commodity.
It provides narrow helper-level observer/rollback/logging proof only using a
mocked observer, with no estimator/audit persistence proof and no full
parser/handler/seed/Stage closure.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterator
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import api.routers.offers as offers_module
from api.routers.offers import _shadow_inference_for_implicit_commodity


class SyntheticObserverError(Exception):
    """Distinctive synthetic exception raised during shadow observation."""


class SyntheticRollbackError(Exception):
    """Distinctive synthetic exception raised during session rollback."""


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


SYNTHETIC_SETTINGS = SimpleNamespace(
    coin_intelligence_inference_preview_enabled=True,
    product_estimator_snapshot_mode="LEGACY",
    coin_intelligence_inference_snapshot_path="/synthetic/snapshot/path.json",
)
SYNTHETIC_SNAPSHOT_PATH = "/synthetic/snapshot/authority/path.json"
FIXED_NOW_UTC = datetime(2026, 9, 10, 7, 15, 0, tzinfo=timezone.utc)
SYNTHETIC_READER_SENTINEL = object()

EXPECTED_UNAVAILABLE_RESULT: dict[str, Any] = {
    "mode": "SHADOW_ONLY",
    "status": "ABSTAIN",
    "decision_key": None,
    "snapshot_generated_at_utc": None,
    "snapshot_receipt": None,
    "reason": "INFERENCE_UNAVAILABLE",
    "candidates": [],
}
EXPECTED_UNAVAILABLE_KEYS = frozenset(EXPECTED_UNAVAILABLE_RESULT.keys())


@contextmanager
def _shadow_observation_failure_spies(
    *,
    call_order: list[str],
    observer_exception: Exception,
) -> Iterator[dict[str, Any]]:
    def resolver_side_effect(settings_arg: Any) -> str:
        call_order.append("resolution")
        return SYNTHETIC_SNAPSHOT_PATH

    def clock_side_effect(tz: Any = None) -> datetime:
        call_order.append("clock")
        return FIXED_NOW_UTC

    def reader_factory_side_effect() -> Any:
        call_order.append("reader_construction")
        return SYNTHETIC_READER_SENTINEL

    async def observer_side_effect(*args: Any, **kwargs: Any) -> Any:
        call_order.append("observer_await")
        raise observer_exception

    def log_trading_event_side_effect(*args: Any, **kwargs: Any) -> dict[str, Any]:
        call_order.append("logging")
        return {}

    resolver_spy = MagicMock(
        name="configured_product_snapshot_authority_path_spy",
        side_effect=resolver_side_effect,
    )
    datetime_mock = MagicMock(name="datetime_mock")
    datetime_mock.now.side_effect = clock_side_effect
    reader_factory_spy = MagicMock(
        name="_configured_product_snapshot_reader_spy",
        side_effect=reader_factory_side_effect,
    )
    observation_spy = AsyncMock(
        name="observe_coin_inference_shadow_spy",
        side_effect=observer_side_effect,
    )
    payload_formatter_spy = MagicMock(name="_catalog_inference_shadow_payload_spy")
    log_trading_event_spy = MagicMock(
        name="log_trading_event_spy",
        side_effect=log_trading_event_side_effect,
    )

    with patch.object(offers_module, "settings", SYNTHETIC_SETTINGS), \
         patch.object(
             offers_module,
             "configured_product_snapshot_authority_path",
             resolver_spy,
         ), \
         patch.object(offers_module, "datetime", datetime_mock), \
         patch.object(
             offers_module,
             "_configured_product_snapshot_reader",
             reader_factory_spy,
         ), \
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
            "datetime_mock": datetime_mock,
            "reader_factory_spy": reader_factory_spy,
            "observation_spy": observation_spy,
            "payload_formatter_spy": payload_formatter_spy,
            "log_trading_event_spy": log_trading_event_spy,
        }


class ShadowObservationFailuresCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _assert_common_upstream_and_observer_contracts(
        self,
        *,
        spies: dict[str, Any],
        session: StrictFakeAsyncSession,
        price: int,
        settlement_term: str,
    ) -> None:
        spies["resolver_spy"].assert_called_once_with(SYNTHETIC_SETTINGS)
        self.assertIs(spies["resolver_spy"].call_args.args[0], SYNTHETIC_SETTINGS)
        self.assertEqual(spies["resolver_spy"].call_args.kwargs, {})

        spies["datetime_mock"].now.assert_called_once_with(timezone.utc)
        self.assertIs(spies["datetime_mock"].now.call_args.args[0], timezone.utc)
        self.assertEqual(spies["datetime_mock"].now.call_args.kwargs, {})

        spies["reader_factory_spy"].assert_called_once_with()
        self.assertEqual(spies["reader_factory_spy"].call_args.args, ())
        self.assertEqual(spies["reader_factory_spy"].call_args.kwargs, {})

        expected_kwargs = {
            "snapshot_path": SYNTHETIC_SNAPSHOT_PATH,
            "submitted_project_price": price,
            "settlement_term": settlement_term,
            "source_surface": "WEBAPP",
            "now_utc": FIXED_NOW_UTC,
            "snapshot_reader": SYNTHETIC_READER_SENTINEL,
        }
        spies["observation_spy"].assert_called_once_with(session, **expected_kwargs)
        spies["observation_spy"].assert_awaited_once_with(session, **expected_kwargs)

        self.assertEqual(spies["observation_spy"].call_args.args, (session,))
        self.assertIs(spies["observation_spy"].call_args.args[0], session)
        self.assertEqual(spies["observation_spy"].call_args.kwargs, expected_kwargs)
        self.assertIs(
            spies["observation_spy"].call_args.kwargs["snapshot_path"],
            SYNTHETIC_SNAPSHOT_PATH,
        )
        self.assertIs(
            spies["observation_spy"].call_args.kwargs["now_utc"],
            FIXED_NOW_UTC,
        )
        self.assertIs(
            spies["observation_spy"].call_args.kwargs["snapshot_reader"],
            SYNTHETIC_READER_SENTINEL,
        )

        self.assertEqual(spies["observation_spy"].await_args.args, (session,))
        self.assertIs(spies["observation_spy"].await_args.args[0], session)
        self.assertEqual(spies["observation_spy"].await_args.kwargs, expected_kwargs)
        self.assertIs(
            spies["observation_spy"].await_args.kwargs["snapshot_path"],
            SYNTHETIC_SNAPSHOT_PATH,
        )
        self.assertIs(
            spies["observation_spy"].await_args.kwargs["now_utc"],
            FIXED_NOW_UTC,
        )
        self.assertIs(
            spies["observation_spy"].await_args.kwargs["snapshot_reader"],
            SYNTHETIC_READER_SENTINEL,
        )

        self.assertEqual(
            set(spies["observation_spy"].call_args.kwargs.keys()),
            set(expected_kwargs.keys()),
        )
        self.assertEqual(
            set(spies["observation_spy"].await_args.kwargs.keys()),
            set(expected_kwargs.keys()),
        )

        session.rollback.assert_called_once_with()
        session.rollback.assert_awaited_once_with()
        session.commit.assert_not_called()
        session.commit.assert_not_awaited()
        spies["payload_formatter_spy"].assert_not_called()

    async def test_observer_failure_with_successful_rollback_returns_unavailable_fallback(
        self,
    ) -> None:
        session = StrictFakeAsyncSession()
        call_order: list[str] = []

        async def rollback_callback() -> None:
            call_order.append("rollback")

        session.rollback.side_effect = rollback_callback

        synthetic_observer_error = SyntheticObserverError(
            "Synthetic observer error during shadow inference"
        )

        with _shadow_observation_failure_spies(
            call_order=call_order,
            observer_exception=synthetic_observer_error,
        ) as spies:
            result = await _shadow_inference_for_implicit_commodity(
                session,
                price=50_000_000,
                settlement_type="cash",
            )

        self.assertEqual(result, EXPECTED_UNAVAILABLE_RESULT)
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result.keys()), EXPECTED_UNAVAILABLE_KEYS)
        self.assertEqual(len(result), 7)
        self.assertNotIn("edit_candidates", result)

        self._assert_common_upstream_and_observer_contracts(
            spies=spies,
            session=session,
            price=50_000_000,
            settlement_term="CASH",
        )

        self.assertEqual(
            call_order,
            [
                "resolution",
                "clock",
                "reader_construction",
                "observer_await",
                "rollback",
                "logging",
            ],
        )

        spies["log_trading_event_spy"].assert_called_once_with(
            offers_module.logger,
            "coin_inference_parse_shadow.unavailable",
            level="warning",
            action="coin_inference_parse_shadow",
            result="abstained",
            error_class="SyntheticObserverError",
        )
        self.assertIs(
            spies["log_trading_event_spy"].call_args.args[0],
            offers_module.logger,
        )
        self.assertEqual(
            spies["log_trading_event_spy"].call_args.args,
            (offers_module.logger, "coin_inference_parse_shadow.unavailable"),
        )
        self.assertEqual(
            spies["log_trading_event_spy"].call_args.kwargs,
            {
                "level": "warning",
                "action": "coin_inference_parse_shadow",
                "result": "abstained",
                "error_class": "SyntheticObserverError",
            },
        )

    async def test_observer_failure_with_failing_rollback_propagates_rollback_exception(
        self,
    ) -> None:
        session = StrictFakeAsyncSession()
        call_order: list[str] = []
        synthetic_rollback_error = SyntheticRollbackError(
            "Synthetic database rollback failure"
        )

        async def rollback_failure_callback() -> None:
            call_order.append("rollback")
            raise synthetic_rollback_error

        session.rollback.side_effect = rollback_failure_callback

        synthetic_observer_error = SyntheticObserverError(
            "Synthetic observer error during shadow inference"
        )

        with _shadow_observation_failure_spies(
            call_order=call_order,
            observer_exception=synthetic_observer_error,
        ) as spies:
            with self.assertRaises(SyntheticRollbackError) as exc_context:
                await _shadow_inference_for_implicit_commodity(
                    session,
                    price=85_000_000,
                    settlement_type="tomorrow",
                )

        self.assertIs(exc_context.exception, synthetic_rollback_error)

        self._assert_common_upstream_and_observer_contracts(
            spies=spies,
            session=session,
            price=85_000_000,
            settlement_term="TOMORROW",
        )

        self.assertEqual(
            call_order,
            [
                "resolution",
                "clock",
                "reader_construction",
                "observer_await",
                "rollback",
            ],
        )

        spies["log_trading_event_spy"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
