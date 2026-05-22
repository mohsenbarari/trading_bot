from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core.services.market_schedule_service import MarketScheduleEvaluation
from core.services import market_transition_service
from models.market_runtime_state import MarketRuntimeState
from models.offer import OfferStatus


class _ScalarsResult:
    def __init__(self, *, first=None, values=None):
        self._first = first
        self._values = values or []

    def first(self):
        return self._first

    def all(self):
        return list(self._values)


class _ExecuteResult:
    def __init__(self, *, first=None, values=None):
        self._scalars = _ScalarsResult(first=first, values=values)

    def scalars(self):
        return self._scalars


class MarketTransitionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_or_create_market_runtime_state_creates_initial_row(self):
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ExecuteResult(first=None)),
            add=Mock(),
            commit=AsyncMock(),
        )
        evaluation = MarketScheduleEvaluation(
            is_open=True,
            reason="schedule_disabled",
            next_transition_at=None,
            timezone="Asia/Tehran",
        )
        now = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)

        state, created = await market_transition_service.get_or_create_market_runtime_state(
            db,
            evaluation=evaluation,
            current_time=now,
        )

        self.assertTrue(created)
        self.assertTrue(state.is_open)
        self.assertFalse(state.active_web_notice_visible)
        self.assertEqual(state.last_transition_at, now)
        db.add.assert_called_once_with(state)
        db.commit.assert_awaited_once()

    async def test_apply_market_schedule_transition_is_idempotent_when_runtime_matches(self):
        state = MarketRuntimeState(id=1, is_open=True, active_web_notice_visible=False, offers_since_last_open=1)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ExecuteResult(first=state)),
            add=Mock(),
            commit=AsyncMock(),
        )
        evaluation = MarketScheduleEvaluation(
            is_open=True,
            reason="daily_window_open",
            next_transition_at=None,
            timezone="Asia/Tehran",
        )

        with patch.object(market_transition_service, "_send_market_channel_notice", new=AsyncMock()) as notice_mock, patch(
            "core.services.market_transition_service.publish_event_sync"
        ) as publish_mock:
            result = await market_transition_service.apply_market_schedule_transition(db, evaluation)

        self.assertFalse(result.changed)
        self.assertIsNone(result.transition)
        db.commit.assert_not_awaited()
        notice_mock.assert_not_awaited()
        publish_mock.assert_not_called()

    async def test_apply_market_open_transition_updates_runtime_and_publishes_notice(self):
        state = MarketRuntimeState(id=1, is_open=False, active_web_notice_visible=False, offers_since_last_open=7)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ExecuteResult(first=state)),
            add=Mock(),
            commit=AsyncMock(),
        )
        next_transition = datetime(2026, 5, 22, 18, 0, tzinfo=timezone.utc)
        evaluation = MarketScheduleEvaluation(
            is_open=True,
            reason="daily_window_open",
            next_transition_at=next_transition,
            timezone="Asia/Tehran",
        )
        now = datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc)

        with patch.object(market_transition_service, "_send_market_channel_notice", new=AsyncMock()) as notice_mock, patch(
            "core.services.market_transition_service.publish_event_sync"
        ) as publish_mock:
            result = await market_transition_service.apply_market_schedule_transition(
                db,
                evaluation,
                current_time=now,
            )

        self.assertTrue(result.changed)
        self.assertEqual(result.transition, "opened")
        self.assertTrue(state.is_open)
        self.assertTrue(state.active_web_notice_visible)
        self.assertEqual(state.offers_since_last_open, 0)
        self.assertEqual(state.last_transition_at, now)
        db.commit.assert_awaited_once()
        notice_mock.assert_awaited_once_with(market_transition_service.MARKET_OPENED_CHANNEL_NOTICE)
        publish_mock.assert_called_once()
        self.assertEqual(publish_mock.call_args.args[0], "market:opened")

    async def test_apply_market_closed_transition_expires_local_offers_and_publishes_notice(self):
        state = MarketRuntimeState(id=1, is_open=True, active_web_notice_visible=False, offers_since_last_open=2)
        offers = [
            SimpleNamespace(id=11, status=OfferStatus.ACTIVE, channel_message_id=101, user_id=5),
            SimpleNamespace(id=12, status=OfferStatus.ACTIVE, channel_message_id=None, user_id=8),
        ]
        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    _ExecuteResult(first=state),
                    _ExecuteResult(values=offers),
                ]
            ),
            add=Mock(),
            commit=AsyncMock(),
        )
        evaluation = MarketScheduleEvaluation(
            is_open=False,
            reason="after_daily_window_close",
            next_transition_at=datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc),
            timezone="Asia/Tehran",
        )
        now = datetime(2026, 5, 22, 18, 0, tzinfo=timezone.utc)

        with patch.object(market_transition_service, "_send_market_channel_notice", new=AsyncMock()) as notice_mock, patch(
            "core.services.market_transition_service.remove_channel_buttons",
            new=AsyncMock(),
        ) as remove_buttons_mock, patch(
            "core.cache.decr_active_offer_count",
            new=AsyncMock(),
        ) as decr_mock, patch(
            "core.services.market_transition_service.publish_event_sync"
        ) as publish_mock:
            result = await market_transition_service.apply_market_schedule_transition(
                db,
                evaluation,
                current_time=now,
            )

        self.assertTrue(result.changed)
        self.assertEqual(result.transition, "closed")
        self.assertEqual(result.expired_offer_ids, (11, 12))
        self.assertFalse(state.is_open)
        self.assertTrue(state.active_web_notice_visible)
        self.assertEqual(state.offers_since_last_open, 0)
        self.assertEqual(state.last_transition_at, now)
        self.assertEqual([offer.status for offer in offers], [OfferStatus.EXPIRED, OfferStatus.EXPIRED])
        self.assertEqual([offer.expire_reason for offer in offers], ["market_closed", "market_closed"])
        db.commit.assert_awaited_once()
        remove_buttons_mock.assert_awaited_once_with(101)
        self.assertEqual(decr_mock.await_count, 2)
        notice_mock.assert_awaited_once_with(market_transition_service.MARKET_CLOSED_CHANNEL_NOTICE)
        publish_mock.assert_called_once()
        self.assertEqual(publish_mock.call_args.args[0], "market:closed")

    async def test_load_market_schedule_overrides_window_uses_local_date_range(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=_ExecuteResult(values=[])))
        current_time = datetime(2026, 5, 22, 6, 0, tzinfo=timezone.utc)

        await market_transition_service.load_market_schedule_overrides_window(
            db,
            timezone_name="Asia/Tehran",
            current_time=current_time,
            lookahead_days=3,
        )

        stmt = db.execute.await_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("market_schedule_overrides.date >= '2026-05-22'", compiled)
        self.assertIn("market_schedule_overrides.date <= '2026-05-25'", compiled)

    async def test_evaluate_current_market_schedule_loads_settings_overrides_and_evaluates(self):
        db = SimpleNamespace()
        current_time = datetime(2026, 5, 22, 8, 30, tzinfo=timezone.utc)
        trading_settings = SimpleNamespace(market_schedule_enabled=True)
        evaluation = MarketScheduleEvaluation(
            is_open=False,
            reason="after_daily_window_close",
            next_transition_at=datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc),
            timezone="Asia/Tehran",
        )
        overrides = [SimpleNamespace(id=1)]

        with patch(
            "core.services.market_transition_service.get_trading_settings_async",
            new=AsyncMock(return_value=trading_settings),
        ), patch(
            "core.services.market_transition_service.get_market_timezone_name",
            return_value="Asia/Tehran",
        ), patch.object(
            market_transition_service,
            "load_market_schedule_overrides_window",
            new=AsyncMock(return_value=overrides),
        ) as load_mock, patch(
            "core.services.market_transition_service.evaluate_market_schedule",
            return_value=evaluation,
        ) as evaluate_mock:
            result = await market_transition_service.evaluate_current_market_schedule(
                db,
                current_time=current_time,
            )

        self.assertIs(result, evaluation)
        load_mock.assert_awaited_once_with(
            db,
            timezone_name="Asia/Tehran",
            current_time=current_time,
        )
        evaluate_mock.assert_called_once_with(
            trading_settings,
            current_time=current_time,
            overrides=overrides,
        )

    async def test_get_market_runtime_view_merges_runtime_notice_state_with_current_evaluation(self):
        db = SimpleNamespace()
        evaluation = MarketScheduleEvaluation(
            is_open=False,
            reason="after_daily_window_close",
            next_transition_at=datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc),
            timezone="Asia/Tehran",
        )
        state = MarketRuntimeState(
            id=1,
            is_open=True,
            active_web_notice_visible=True,
            offers_since_last_open=1,
            last_transition_at=datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc),
        )

        with patch.object(
            market_transition_service,
            "evaluate_current_market_schedule",
            new=AsyncMock(return_value=evaluation),
        ), patch.object(
            market_transition_service,
            "get_market_runtime_state",
            new=AsyncMock(return_value=state),
        ):
            result = await market_transition_service.get_market_runtime_view(db)

        self.assertFalse(result.is_open)
        self.assertTrue(result.active_web_notice_visible)
        self.assertEqual(result.offers_since_last_open, 1)
        self.assertEqual(result.last_transition_at, state.last_transition_at)
        self.assertEqual(result.next_transition_at, evaluation.next_transition_at)

    async def test_register_market_offer_created_hides_notice_after_second_offer(self):
        db = SimpleNamespace(commit=AsyncMock())
        evaluation = MarketScheduleEvaluation(
            is_open=True,
            reason="daily_window_open",
            next_transition_at=datetime(2026, 5, 22, 18, 0, tzinfo=timezone.utc),
            timezone="Asia/Tehran",
        )
        state = MarketRuntimeState(
            id=1,
            is_open=True,
            active_web_notice_visible=True,
            offers_since_last_open=1,
            last_transition_at=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
        )

        with patch.object(
            market_transition_service,
            "evaluate_current_market_schedule",
            new=AsyncMock(return_value=evaluation),
        ), patch.object(
            market_transition_service,
            "get_or_create_market_runtime_state",
            new=AsyncMock(return_value=(state, False)),
        ), patch("core.services.market_transition_service.publish_event_sync") as publish_mock:
            result = await market_transition_service.register_market_offer_created(db)

        self.assertIs(result, state)
        self.assertEqual(state.offers_since_last_open, 2)
        self.assertFalse(state.active_web_notice_visible)
        db.commit.assert_awaited_once()
        publish_mock.assert_called_once()
        self.assertEqual(publish_mock.call_args.args[0], "market:notice_hidden")


if __name__ == "__main__":
    unittest.main()