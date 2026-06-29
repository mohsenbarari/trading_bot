import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import market_schedule_loop
from core.services.market_schedule_service import MarketScheduleEvaluation
from core.services.market_transition_service import MarketTransitionResult


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class MarketScheduleLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_market_schedule_runtime_uses_settings_overrides_and_transition_service(self):
        session = SimpleNamespace()
        trading_settings = SimpleNamespace(market_timezone="Asia/Tehran")
        overrides = [SimpleNamespace(id=1)]
        evaluation = MarketScheduleEvaluation(True, "schedule_disabled", None, "Asia/Tehran")
        transition_result = MarketTransitionResult(
            changed=True,
            transition="opened",
            state=SimpleNamespace(is_open=True),
        )

        with patch("core.market_schedule_loop.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "core.market_schedule_loop.get_trading_settings_async",
            new=AsyncMock(return_value=trading_settings),
        ) as settings_mock, patch(
            "core.market_schedule_loop.get_market_timezone_name",
            return_value="Asia/Tehran",
        ) as timezone_mock, patch(
            "core.market_schedule_loop.load_market_schedule_overrides_window",
            new=AsyncMock(return_value=overrides),
        ) as overrides_mock, patch(
            "core.market_schedule_loop.evaluate_market_schedule",
            return_value=evaluation,
        ) as evaluate_mock, patch(
            "core.market_schedule_loop.apply_market_schedule_transition",
            new=AsyncMock(return_value=transition_result),
        ) as transition_mock, patch(
            "core.market_schedule_loop.current_server",
            return_value="iran",
        ):
            result = await market_schedule_loop.reconcile_market_schedule_runtime(current_time="marker-now")

        self.assertIs(result, transition_result)
        settings_mock.assert_awaited_once()
        timezone_mock.assert_called_once_with(trading_settings)
        overrides_mock.assert_awaited_once_with(session, timezone_name="Asia/Tehran", current_time="marker-now")
        evaluate_mock.assert_called_once_with(trading_settings, current_time="marker-now", overrides=overrides)
        transition_mock.assert_awaited_once_with(session, evaluation, current_time="marker-now")

    async def test_reconcile_market_schedule_runtime_on_foreign_only_reconciles_synced_side_effects(self):
        session = SimpleNamespace()
        trading_settings = SimpleNamespace(market_timezone="Asia/Tehran")
        overrides = [SimpleNamespace(id=1)]
        evaluation = MarketScheduleEvaluation(
            False,
            "after_daily_window_close",
            None,
            "Asia/Tehran",
            current_transition_at=None,
        )
        side_effect_result = MarketTransitionResult(
            changed=True,
            transition="closed_local_offer_expiry",
            state=SimpleNamespace(is_open=False),
            expired_offer_ids=(11,),
        )
        autonomy_result = MarketTransitionResult(changed=False, transition=None, state=None)
        retry_summary = SimpleNamespace(checked=0, sent=0, failed=0, skipped=0)

        with patch("core.market_schedule_loop.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "core.market_schedule_loop.current_server",
            return_value="foreign",
        ), patch(
            "core.market_schedule_loop.get_trading_settings_async",
            new=AsyncMock(return_value=trading_settings),
        ) as settings_mock, patch(
            "core.market_schedule_loop.get_market_timezone_name",
            return_value="Asia/Tehran",
        ) as timezone_mock, patch(
            "core.market_schedule_loop.load_market_schedule_overrides_window",
            new=AsyncMock(return_value=overrides),
        ) as overrides_mock, patch(
            "core.market_schedule_loop.evaluate_market_schedule",
            return_value=evaluation,
        ) as evaluate_mock, patch(
            "core.market_schedule_loop.apply_market_schedule_transition",
            new=AsyncMock(),
        ) as transition_mock, patch(
            "core.market_schedule_loop.reconcile_market_runtime_side_effects_for_current_state",
            new=AsyncMock(return_value=side_effect_result),
        ) as side_effect_mock, patch(
            "core.market_schedule_loop.reconcile_foreign_market_schedule_autonomy",
            new=AsyncMock(return_value=autonomy_result),
        ) as autonomy_mock, patch(
            "core.market_schedule_loop.reconcile_due_market_channel_notice_receipts",
            new=AsyncMock(return_value=retry_summary),
        ) as retry_mock:
            result = await market_schedule_loop.reconcile_market_schedule_runtime(current_time="marker-now")

        self.assertIs(result, side_effect_result)
        side_effect_mock.assert_awaited_once_with(session, source="market_schedule_loop")
        settings_mock.assert_awaited_once()
        timezone_mock.assert_called_once_with(trading_settings)
        overrides_mock.assert_awaited_once_with(session, timezone_name="Asia/Tehran", current_time="marker-now")
        evaluate_mock.assert_called_once_with(trading_settings, current_time="marker-now", overrides=overrides)
        autonomy_mock.assert_awaited_once_with(
            session,
            evaluation,
            current_time="marker-now",
            source="market_schedule_loop_autonomy",
        )
        retry_mock.assert_awaited_once_with(session, source="market_schedule_loop_retry")
        transition_mock.assert_not_awaited()

    async def test_reconcile_market_schedule_runtime_on_foreign_returns_autonomy_result_when_changed(self):
        session = SimpleNamespace()
        trading_settings = SimpleNamespace(market_timezone="Asia/Tehran")
        evaluation = MarketScheduleEvaluation(
            False,
            "after_daily_window_close",
            None,
            "Asia/Tehran",
            current_transition_at=None,
        )
        side_effect_result = MarketTransitionResult(changed=False, transition=None, state=SimpleNamespace(is_open=True))
        autonomy_result = MarketTransitionResult(
            changed=True,
            transition="closed_local_offer_expiry",
            state=SimpleNamespace(is_open=False),
            expired_offer_ids=(19,),
        )
        retry_summary = SimpleNamespace(checked=0, sent=0, failed=0, skipped=0)

        with patch("core.market_schedule_loop.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "core.market_schedule_loop.current_server",
            return_value="foreign",
        ), patch(
            "core.market_schedule_loop.get_trading_settings_async",
            new=AsyncMock(return_value=trading_settings),
        ), patch(
            "core.market_schedule_loop.get_market_timezone_name",
            return_value="Asia/Tehran",
        ), patch(
            "core.market_schedule_loop.load_market_schedule_overrides_window",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.market_schedule_loop.evaluate_market_schedule",
            return_value=evaluation,
        ), patch(
            "core.market_schedule_loop.reconcile_market_runtime_side_effects_for_current_state",
            new=AsyncMock(return_value=side_effect_result),
        ), patch(
            "core.market_schedule_loop.reconcile_foreign_market_schedule_autonomy",
            new=AsyncMock(return_value=autonomy_result),
        ) as autonomy_mock, patch(
            "core.market_schedule_loop.reconcile_due_market_channel_notice_receipts",
            new=AsyncMock(return_value=retry_summary),
        ):
            result = await market_schedule_loop.reconcile_market_schedule_runtime(current_time="marker-now")

        self.assertIs(result, autonomy_result)
        autonomy_mock.assert_awaited_once_with(
            session,
            evaluation,
            current_time="marker-now",
            source="market_schedule_loop_autonomy",
        )

    async def test_market_schedule_loop_logs_start_success_and_failure_cycles(self):
        sleep_calls = []

        async def stop_after_second_sleep(_delay):
            sleep_calls.append(_delay)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        with patch(
            "core.market_schedule_loop.reconcile_market_schedule_runtime",
            new=AsyncMock(side_effect=[MarketTransitionResult(True, "closed", SimpleNamespace(is_open=False), (1, 2)), RuntimeError("boom")]),
        ), patch(
            "core.market_schedule_loop.asyncio.sleep",
            side_effect=stop_after_second_sleep,
        ), patch.object(market_schedule_loop, "logger") as logger:
            with self.assertRaises(asyncio.CancelledError):
                await market_schedule_loop.market_schedule_loop()

        logger.info.assert_any_call(
            "⏰ Market schedule loop started (check every %ss)",
            market_schedule_loop.MARKET_SCHEDULE_LOOP_INTERVAL_SECONDS,
        )
        self.assertGreaterEqual(logger.info.call_count, 1)
        logger.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
