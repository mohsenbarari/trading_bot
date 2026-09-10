"""Characterization tests for _shadow_inference_for_implicit_commodity cancellation boundaries.

Tracked inventory gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Gap ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed ID: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card path: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

This test module characterizes the asyncio cancellation boundaries
for api.routers.offers._shadow_inference_for_implicit_commodity:
1. Cancellation during the awaited shadow observation call.
2. Cancellation during the awaited database session commit call.
3. Cancellation during the awaited database session rollback call after shadow observation failure.
4. Cancellation during the awaited database session rollback call after database session commit failure.

It provides narrow helper-only mocked cancellation evidence with no real
estimator/audit persistence, no database or Redis access, and no full
parser/handler/seed/Stage closure.
Preserves existing product behavior and established accepted test suites.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterator
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import api.routers.offers as offers_module
from api.routers.offers import _shadow_inference_for_implicit_commodity


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
SYNTHETIC_OBSERVATION = SimpleNamespace(
    decision=object(),
    decision_key="synthetic_decision_key_0123456789abcdef",
    edit_candidates=(),
)


@contextmanager
def _shadow_cancellation_spies(
    *,
    call_order: list[str],
    observer_side_effect: Any,
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
    logger_mock = MagicMock(name="logger_mock")

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
         ), \
         patch.object(
             offers_module,
             "logger",
             logger_mock,
         ):
        yield {
            "resolver_spy": resolver_spy,
            "datetime_mock": datetime_mock,
            "reader_factory_spy": reader_factory_spy,
            "observation_spy": observation_spy,
            "payload_formatter_spy": payload_formatter_spy,
            "log_trading_event_spy": log_trading_event_spy,
            "logger_mock": logger_mock,
        }


class ShadowCancellationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _assert_common_upstream_and_observer_contracts(
        self,
        *,
        spies: dict[str, Any],
        session: StrictFakeAsyncSession,
        price: int,
        settlement_term: str,
    ) -> None:
        # Resolver contract: called once with identical synthetic settings and no keywords
        spies["resolver_spy"].assert_called_once_with(SYNTHETIC_SETTINGS)
        self.assertEqual(spies["resolver_spy"].call_count, 1)
        self.assertEqual(len(spies["resolver_spy"].call_args.args), 1)
        self.assertIs(spies["resolver_spy"].call_args.args[0], SYNTHETIC_SETTINGS)
        self.assertEqual(spies["resolver_spy"].call_args.kwargs, {})

        # Clock contract: datetime.now called once with timezone.utc and no keywords
        spies["datetime_mock"].now.assert_called_once_with(timezone.utc)
        self.assertEqual(spies["datetime_mock"].now.call_count, 1)
        self.assertEqual(len(spies["datetime_mock"].now.call_args.args), 1)
        self.assertIs(spies["datetime_mock"].now.call_args.args[0], timezone.utc)
        self.assertEqual(spies["datetime_mock"].now.call_args.kwargs, {})

        # Reader factory contract: called once with no arguments
        spies["reader_factory_spy"].assert_called_once_with()
        self.assertEqual(spies["reader_factory_spy"].call_count, 1)
        self.assertEqual(spies["reader_factory_spy"].call_args.args, ())
        self.assertEqual(spies["reader_factory_spy"].call_args.kwargs, {})

        # Observer contract: called and awaited with session as sole positional arg and exact kwargs
        expected_kwargs: dict[str, Any] = {
            "snapshot_path": SYNTHETIC_SNAPSHOT_PATH,
            "submitted_project_price": price,
            "settlement_term": settlement_term,
            "source_surface": "WEBAPP",
            "now_utc": FIXED_NOW_UTC,
            "snapshot_reader": SYNTHETIC_READER_SENTINEL,
        }

        spies["observation_spy"].assert_called_once_with(session, **expected_kwargs)
        spies["observation_spy"].assert_awaited_once_with(session, **expected_kwargs)
        self.assertEqual(spies["observation_spy"].call_count, 1)
        self.assertEqual(spies["observation_spy"].await_count, 1)

        # Call args validation
        self.assertEqual(spies["observation_spy"].call_args.args, (session,))
        self.assertIs(spies["observation_spy"].call_args.args[0], session)
        self.assertEqual(spies["observation_spy"].call_args.kwargs, expected_kwargs)
        self.assertIs(
            spies["observation_spy"].call_args.kwargs["snapshot_path"],
            SYNTHETIC_SNAPSHOT_PATH,
        )
        self.assertEqual(
            spies["observation_spy"].call_args.kwargs["submitted_project_price"],
            price,
        )
        self.assertEqual(
            spies["observation_spy"].call_args.kwargs["settlement_term"],
            settlement_term,
        )
        self.assertEqual(
            spies["observation_spy"].call_args.kwargs["source_surface"],
            "WEBAPP",
        )
        self.assertIs(
            spies["observation_spy"].call_args.kwargs["now_utc"],
            FIXED_NOW_UTC,
        )
        self.assertIs(
            spies["observation_spy"].call_args.kwargs["snapshot_reader"],
            SYNTHETIC_READER_SENTINEL,
        )

        # Await args validation
        self.assertEqual(spies["observation_spy"].await_args.args, (session,))
        self.assertIs(spies["observation_spy"].await_args.args[0], session)
        self.assertEqual(spies["observation_spy"].await_args.kwargs, expected_kwargs)
        self.assertIs(
            spies["observation_spy"].await_args.kwargs["snapshot_path"],
            SYNTHETIC_SNAPSHOT_PATH,
        )
        self.assertEqual(
            spies["observation_spy"].await_args.kwargs["submitted_project_price"],
            price,
        )
        self.assertEqual(
            spies["observation_spy"].await_args.kwargs["settlement_term"],
            settlement_term,
        )
        self.assertEqual(
            spies["observation_spy"].await_args.kwargs["source_surface"],
            "WEBAPP",
        )
        self.assertIs(
            spies["observation_spy"].await_args.kwargs["now_utc"],
            FIXED_NOW_UTC,
        )
        self.assertIs(
            spies["observation_spy"].await_args.kwargs["snapshot_reader"],
            SYNTHETIC_READER_SENTINEL,
        )

        # Ensure no selection-only keyword arguments appear
        expected_keys = frozenset(expected_kwargs.keys())
        self.assertEqual(
            set(spies["observation_spy"].call_args.kwargs.keys()),
            expected_keys,
        )
        self.assertEqual(
            set(spies["observation_spy"].await_args.kwargs.keys()),
            expected_keys,
        )
        self.assertNotIn("candidate_scope", spies["observation_spy"].call_args.kwargs)
        self.assertNotIn("force_confirmation", spies["observation_spy"].call_args.kwargs)
        self.assertNotIn("candidate_scope", spies["observation_spy"].await_args.kwargs)
        self.assertNotIn("force_confirmation", spies["observation_spy"].await_args.kwargs)

    async def test_observer_await_cancellation_propagates_without_commit_rollback_or_logging(
        self,
    ) -> None:
        session = StrictFakeAsyncSession()
        call_order: list[str] = []

        async def commit_callback(*args: Any, **kwargs: Any) -> None:
            call_order.append("commit_await")

        async def rollback_callback(*args: Any, **kwargs: Any) -> None:
            call_order.append("rollback_await")

        session.commit.side_effect = commit_callback
        session.rollback.side_effect = rollback_callback

        observer_cancelled_error = asyncio.CancelledError(
            "Synthetic cancellation during shadow observation await"
        )

        async def observer_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("observer_await")
            raise observer_cancelled_error

        with _shadow_cancellation_spies(
            call_order=call_order,
            observer_side_effect=observer_side_effect,
        ) as spies:
            with self.assertRaises(asyncio.CancelledError) as exc_context:
                await _shadow_inference_for_implicit_commodity(
                    session,
                    price=72_000_000,
                    settlement_type="cash",
                )

        # Assert identical exception object escapes
        self.assertIs(exc_context.exception, observer_cancelled_error)

        # Assert exact event execution order
        self.assertEqual(
            call_order,
            [
                "resolution",
                "clock",
                "reader_construction",
                "observer_await",
            ],
        )

        # Assert observer called and awaited exactly once
        self.assertEqual(spies["observation_spy"].call_count, 1)
        self.assertEqual(spies["observation_spy"].await_count, 1)

        # Assert commit and rollback each neither called nor awaited
        session.commit.assert_not_called()
        session.commit.assert_not_awaited()
        self.assertEqual(session.commit.call_count, 0)
        self.assertEqual(session.commit.await_count, 0)

        session.rollback.assert_not_called()
        session.rollback.assert_not_awaited()
        self.assertEqual(session.rollback.call_count, 0)
        self.assertEqual(session.rollback.await_count, 0)

        # Assert formatter and logging never called
        spies["payload_formatter_spy"].assert_not_called()
        self.assertEqual(spies["payload_formatter_spy"].call_count, 0)

        spies["log_trading_event_spy"].assert_not_called()
        self.assertEqual(spies["log_trading_event_spy"].call_count, 0)
        spies["logger_mock"].warning.assert_not_called()
        spies["logger_mock"].error.assert_not_called()
        spies["logger_mock"].info.assert_not_called()
        spies["logger_mock"].exception.assert_not_called()

        # Assert full upstream and observer contracts
        self._assert_common_upstream_and_observer_contracts(
            spies=spies,
            session=session,
            price=72_000_000,
            settlement_term="CASH",
        )

    async def test_commit_await_cancellation_propagates_without_rollback_or_logging(
        self,
    ) -> None:
        session = StrictFakeAsyncSession()
        call_order: list[str] = []

        commit_cancelled_error = asyncio.CancelledError(
            "Synthetic cancellation during database commit await"
        )

        async def commit_callback(*args: Any, **kwargs: Any) -> None:
            call_order.append("commit_await")
            raise commit_cancelled_error

        async def rollback_callback(*args: Any, **kwargs: Any) -> None:
            call_order.append("rollback_await")

        session.commit.side_effect = commit_callback
        session.rollback.side_effect = rollback_callback

        async def observer_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("observer_await")
            call_order.append("observer_success")
            return SYNTHETIC_OBSERVATION

        with _shadow_cancellation_spies(
            call_order=call_order,
            observer_side_effect=observer_side_effect,
        ) as spies:
            with self.assertRaises(asyncio.CancelledError) as exc_context:
                await _shadow_inference_for_implicit_commodity(
                    session,
                    price=93_500_000,
                    settlement_type="tomorrow",
                )

        # Assert identical exception object escapes
        self.assertIs(exc_context.exception, commit_cancelled_error)

        # Assert exact event execution order and show observer success precedes commit await
        self.assertEqual(
            call_order,
            [
                "resolution",
                "clock",
                "reader_construction",
                "observer_await",
                "observer_success",
                "commit_await",
            ],
        )
        observer_success_index = call_order.index("observer_success")
        commit_await_index = call_order.index("commit_await")
        self.assertLess(observer_success_index, commit_await_index)

        # Assert observer called and awaited exactly once
        self.assertEqual(spies["observation_spy"].call_count, 1)
        self.assertEqual(spies["observation_spy"].await_count, 1)

        # Assert commit called and awaited exactly once with no args
        session.commit.assert_called_once_with()
        session.commit.assert_awaited_once_with()
        self.assertEqual(session.commit.call_count, 1)
        self.assertEqual(session.commit.await_count, 1)
        self.assertEqual(session.commit.call_args.args, ())
        self.assertEqual(session.commit.call_args.kwargs, {})
        self.assertEqual(session.commit.await_args.args, ())
        self.assertEqual(session.commit.await_args.kwargs, {})

        # Assert rollback neither called nor awaited
        session.rollback.assert_not_called()
        session.rollback.assert_not_awaited()
        self.assertEqual(session.rollback.call_count, 0)
        self.assertEqual(session.rollback.await_count, 0)

        # Assert formatter and logging never called
        spies["payload_formatter_spy"].assert_not_called()
        self.assertEqual(spies["payload_formatter_spy"].call_count, 0)

        spies["log_trading_event_spy"].assert_not_called()
        self.assertEqual(spies["log_trading_event_spy"].call_count, 0)
        spies["logger_mock"].warning.assert_not_called()
        spies["logger_mock"].error.assert_not_called()
        spies["logger_mock"].info.assert_not_called()
        spies["logger_mock"].exception.assert_not_called()

        # Assert full upstream and observer contracts
        self._assert_common_upstream_and_observer_contracts(
            spies=spies,
            session=session,
            price=93_500_000,
            settlement_term="TOMORROW",
        )

    async def test_rollback_await_cancellation_after_observer_failure_propagates_with_context(
        self,
    ) -> None:
        session = StrictFakeAsyncSession()
        call_order: list[str] = []

        async def commit_callback(*args: Any, **kwargs: Any) -> None:
            call_order.append("commit_await")

        async def rollback_callback(*args: Any, **kwargs: Any) -> None:
            call_order.append("rollback_await")
            raise rollback_cancelled_error

        session.commit.side_effect = commit_callback
        session.rollback.side_effect = rollback_callback

        observer_failure_error = RuntimeError(
            "Synthetic ordinary failure during shadow observation await"
        )
        rollback_cancelled_error = asyncio.CancelledError(
            "Synthetic cancellation during database rollback await after observation failure"
        )

        async def observer_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("observer_await")
            raise observer_failure_error

        with _shadow_cancellation_spies(
            call_order=call_order,
            observer_side_effect=observer_side_effect,
        ) as spies:
            with self.assertRaises(asyncio.CancelledError) as exc_context:
                await _shadow_inference_for_implicit_commodity(
                    session,
                    price=84_000_000,
                    settlement_type="cash",
                )

        # Assert exact cancellation instance escapes and its __context__ is the observer exception
        self.assertIs(exc_context.exception, rollback_cancelled_error)
        self.assertIs(exc_context.exception.__context__, observer_failure_error)
        self.assertIsInstance(exc_context.exception, asyncio.CancelledError)

        # Assert exact event execution order from resolution through clock, reader construction, observer await, and rollback await
        self.assertEqual(
            call_order,
            [
                "resolution",
                "clock",
                "reader_construction",
                "observer_await",
                "rollback_await",
            ],
        )
        observer_await_index = call_order.index("observer_await")
        rollback_await_index = call_order.index("rollback_await")
        self.assertLess(observer_await_index, rollback_await_index)

        # Assert observer called and awaited exactly once
        self.assertEqual(spies["observation_spy"].call_count, 1)
        self.assertEqual(spies["observation_spy"].await_count, 1)

        # Assert commit is neither called nor awaited
        session.commit.assert_not_called()
        session.commit.assert_not_awaited()
        self.assertEqual(session.commit.call_count, 0)
        self.assertEqual(session.commit.await_count, 0)

        # Assert rollback is called and awaited exactly once with no arguments
        session.rollback.assert_called_once_with()
        session.rollback.assert_awaited_once_with()
        self.assertEqual(session.rollback.call_count, 1)
        self.assertEqual(session.rollback.await_count, 1)
        self.assertEqual(session.rollback.call_args.args, ())
        self.assertEqual(session.rollback.call_args.kwargs, {})
        self.assertEqual(session.rollback.await_args.args, ())
        self.assertEqual(session.rollback.await_args.kwargs, {})

        # Assert payload formatter, log_trading_event, and logger methods are never called
        spies["payload_formatter_spy"].assert_not_called()
        self.assertEqual(spies["payload_formatter_spy"].call_count, 0)

        spies["log_trading_event_spy"].assert_not_called()
        self.assertEqual(spies["log_trading_event_spy"].call_count, 0)
        spies["logger_mock"].warning.assert_not_called()
        spies["logger_mock"].error.assert_not_called()
        spies["logger_mock"].info.assert_not_called()
        spies["logger_mock"].exception.assert_not_called()
        self.assertEqual(spies["logger_mock"].warning.call_count, 0)
        self.assertEqual(spies["logger_mock"].error.call_count, 0)
        self.assertEqual(spies["logger_mock"].info.call_count, 0)
        self.assertEqual(spies["logger_mock"].exception.call_count, 0)

        # Assert exact upstream and observer call and await contracts
        self._assert_common_upstream_and_observer_contracts(
            spies=spies,
            session=session,
            price=84_000_000,
            settlement_term="CASH",
        )

    async def test_rollback_await_cancellation_after_commit_failure_propagates_with_context(
        self,
    ) -> None:
        session = StrictFakeAsyncSession()
        call_order: list[str] = []

        commit_failure_error = RuntimeError(
            "Synthetic ordinary failure during database commit await"
        )
        rollback_cancelled_error = asyncio.CancelledError(
            "Synthetic cancellation during database rollback await after commit failure"
        )

        async def commit_callback(*args: Any, **kwargs: Any) -> None:
            call_order.append("commit_await")
            raise commit_failure_error

        async def rollback_callback(*args: Any, **kwargs: Any) -> None:
            call_order.append("rollback_await")
            raise rollback_cancelled_error

        session.commit.side_effect = commit_callback
        session.rollback.side_effect = rollback_callback

        async def observer_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("observer_await")
            call_order.append("observer_success")
            return SYNTHETIC_OBSERVATION

        with _shadow_cancellation_spies(
            call_order=call_order,
            observer_side_effect=observer_side_effect,
        ) as spies:
            with self.assertRaises(asyncio.CancelledError) as exc_context:
                await _shadow_inference_for_implicit_commodity(
                    session,
                    price=102_500_000,
                    settlement_type="tomorrow",
                )

        # Assert exact cancellation instance escapes and its __context__ is the commit exception
        self.assertIs(exc_context.exception, rollback_cancelled_error)
        self.assertIs(exc_context.exception.__context__, commit_failure_error)
        self.assertIsInstance(exc_context.exception, asyncio.CancelledError)

        # Assert exact event execution order and independently prove observer success precedes commit failure and rollback cancellation
        self.assertEqual(
            call_order,
            [
                "resolution",
                "clock",
                "reader_construction",
                "observer_await",
                "observer_success",
                "commit_await",
                "rollback_await",
            ],
        )
        observer_success_index = call_order.index("observer_success")
        commit_await_index = call_order.index("commit_await")
        rollback_await_index = call_order.index("rollback_await")
        self.assertLess(observer_success_index, commit_await_index)
        self.assertLess(commit_await_index, rollback_await_index)
        self.assertLess(observer_success_index, rollback_await_index)

        # Assert observer called and awaited exactly once
        self.assertEqual(spies["observation_spy"].call_count, 1)
        self.assertEqual(spies["observation_spy"].await_count, 1)

        # Assert commit called and awaited exactly once with no arguments
        session.commit.assert_called_once_with()
        session.commit.assert_awaited_once_with()
        self.assertEqual(session.commit.call_count, 1)
        self.assertEqual(session.commit.await_count, 1)
        self.assertEqual(session.commit.call_args.args, ())
        self.assertEqual(session.commit.call_args.kwargs, {})
        self.assertEqual(session.commit.await_args.args, ())
        self.assertEqual(session.commit.await_args.kwargs, {})

        # Assert rollback called and awaited exactly once with no arguments
        session.rollback.assert_called_once_with()
        session.rollback.assert_awaited_once_with()
        self.assertEqual(session.rollback.call_count, 1)
        self.assertEqual(session.rollback.await_count, 1)
        self.assertEqual(session.rollback.call_args.args, ())
        self.assertEqual(session.rollback.call_args.kwargs, {})
        self.assertEqual(session.rollback.await_args.args, ())
        self.assertEqual(session.rollback.await_args.kwargs, {})

        # Assert payload formatter, log_trading_event, and logger methods are never called
        spies["payload_formatter_spy"].assert_not_called()
        self.assertEqual(spies["payload_formatter_spy"].call_count, 0)

        spies["log_trading_event_spy"].assert_not_called()
        self.assertEqual(spies["log_trading_event_spy"].call_count, 0)
        spies["logger_mock"].warning.assert_not_called()
        spies["logger_mock"].error.assert_not_called()
        spies["logger_mock"].info.assert_not_called()
        spies["logger_mock"].exception.assert_not_called()
        self.assertEqual(spies["logger_mock"].warning.call_count, 0)
        self.assertEqual(spies["logger_mock"].error.call_count, 0)
        self.assertEqual(spies["logger_mock"].info.call_count, 0)
        self.assertEqual(spies["logger_mock"].exception.call_count, 0)

        # Assert exact upstream and observer call and await contracts
        self._assert_common_upstream_and_observer_contracts(
            spies=spies,
            session=session,
            price=102_500_000,
            settlement_term="TOMORROW",
        )


if __name__ == "__main__":
    unittest.main()
