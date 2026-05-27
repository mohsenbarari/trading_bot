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


class _FakeAsyncClient:
    def __init__(self, *, response=None, side_effect=None):
        self._response = response or SimpleNamespace(raise_for_status=Mock())
        self.post = AsyncMock(side_effect=side_effect, return_value=self._response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class MarketTransitionServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_helper_functions_cover_default_and_naive_times(self):
        evaluation = MarketScheduleEvaluation(
            is_open=True,
            reason="daily_window_open",
            next_transition_at=None,
            timezone="Asia/Tehran",
        )
        now = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
        state = MarketRuntimeState(
            id=1,
            is_open=True,
            active_web_notice_visible=False,
            offers_since_last_open=0,
            last_transition_at=None,
        )

        with patch("core.services.market_transition_service.utc_now", return_value=now):
            self.assertEqual(market_transition_service._coerce_utc_now(), now)
            built_state = market_transition_service._build_initial_market_runtime_state(evaluation)

        self.assertEqual(
            market_transition_service._coerce_utc_now(datetime(2026, 5, 22, 12, 30)),
            datetime(2026, 5, 22, 12, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(built_state.last_transition_at, now)
        self.assertEqual(
            market_transition_service._build_market_event_payload(
                state,
                transition="opened",
                notice_text=None,
            ),
            {
                "is_open": True,
                "active_web_notice_visible": False,
                "offers_since_last_open": 0,
                "last_transition_at": None,
                "transition": "opened",
                "notice_text": None,
            },
        )

    async def test_send_market_channel_notice_skips_without_configured_channel(self):
        with patch.object(market_transition_service.settings, "bot_token", None), patch.object(
            market_transition_service.settings,
            "channel_id",
            None,
        ), patch("core.services.market_transition_service.os.getenv", return_value=None), patch(
            "core.services.market_transition_service.httpx.AsyncClient"
        ) as client_cls:
            await market_transition_service._send_market_channel_notice("opened")

        client_cls.assert_not_called()

    async def test_send_market_channel_notice_posts_to_telegram(self):
        fake_client = _FakeAsyncClient()

        with patch.object(market_transition_service.settings, "bot_token", None), patch.object(
            market_transition_service.settings,
            "channel_id",
            "@market",
        ), patch("core.services.market_transition_service.os.getenv", return_value="token-123"), patch(
            "core.services.market_transition_service.httpx.AsyncClient",
            return_value=fake_client,
        ):
            await market_transition_service._send_market_channel_notice("market opened")

        fake_client.post.assert_awaited_once_with(
            "https://api.telegram.org/bottoken-123/sendMessage",
            json={"chat_id": "@market", "text": "market opened"},
            timeout=10,
        )
        fake_client._response.raise_for_status.assert_called_once_with()

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

    async def test_get_or_create_market_runtime_state_returns_existing_row_without_commit(self):
        existing_state = MarketRuntimeState(id=1, is_open=False, active_web_notice_visible=False, offers_since_last_open=0)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ExecuteResult(first=existing_state)),
            add=Mock(),
            commit=AsyncMock(),
        )
        evaluation = MarketScheduleEvaluation(
            is_open=False,
            reason="after_daily_window_close",
            next_transition_at=None,
            timezone="Asia/Tehran",
        )

        state, created = await market_transition_service.get_or_create_market_runtime_state(db, evaluation=evaluation)

        self.assertIs(state, existing_state)
        self.assertFalse(created)
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_acquire_market_runtime_lock_uses_database_advisory_lock(self):
        db = SimpleNamespace(execute=AsyncMock())

        await market_transition_service._acquire_market_runtime_lock(db)

        stmt, params = db.execute.await_args.args
        self.assertIn("pg_advisory_xact_lock", str(stmt))
        self.assertEqual(params, {"lock_key": market_transition_service.MARKET_RUNTIME_ADVISORY_LOCK_KEY})

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

        with patch.object(market_transition_service, "_acquire_market_runtime_lock", new=AsyncMock()) as lock_mock, patch.object(
            market_transition_service, "_send_market_channel_notice", new=AsyncMock()
        ) as notice_mock, patch(
            "core.services.market_transition_service.publish_event_sync"
        ) as publish_mock:
            result = await market_transition_service.apply_market_schedule_transition(db, evaluation)

        lock_mock.assert_awaited_once_with(db)
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

        with patch.object(market_transition_service, "_acquire_market_runtime_lock", new=AsyncMock()) as lock_mock, patch.object(
            market_transition_service, "_send_market_channel_notice", new=AsyncMock()
        ) as notice_mock, patch(
            "core.services.market_transition_service.publish_event_sync"
        ) as publish_mock:
            result = await market_transition_service.apply_market_schedule_transition(
                db,
                evaluation,
                current_time=now,
            )

        lock_mock.assert_awaited_once_with(db)
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

        with patch.object(market_transition_service, "_acquire_market_runtime_lock", new=AsyncMock()) as lock_mock, patch.object(
            market_transition_service, "_send_market_channel_notice", new=AsyncMock()
        ) as notice_mock, patch(
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

        lock_mock.assert_awaited_once_with(db)
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

    async def test_get_market_runtime_view_defaults_to_empty_runtime_state(self):
        db = SimpleNamespace()
        evaluation = MarketScheduleEvaluation(
            is_open=True,
            reason="daily_window_open",
            next_transition_at=None,
            timezone="Asia/Tehran",
        )

        with patch.object(
            market_transition_service,
            "evaluate_current_market_schedule",
            new=AsyncMock(return_value=evaluation),
        ), patch.object(
            market_transition_service,
            "get_market_runtime_state",
            new=AsyncMock(return_value=None),
        ):
            result = await market_transition_service.get_market_runtime_view(db)

        self.assertTrue(result.is_open)
        self.assertFalse(result.active_web_notice_visible)
        self.assertEqual(result.offers_since_last_open, 0)
        self.assertIsNone(result.last_transition_at)
        self.assertIsNone(result.next_transition_at)

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
            "_acquire_market_runtime_lock",
            new=AsyncMock(),
        ) as lock_mock, patch.object(
            market_transition_service,
            "get_market_runtime_state",
            new=AsyncMock(return_value=state),
        ), patch("core.services.market_transition_service.publish_event_sync") as publish_mock:
            result = await market_transition_service.register_market_offer_created(db)

        lock_mock.assert_awaited_once_with(db)
        self.assertIs(result, state)
        self.assertEqual(state.offers_since_last_open, 2)
        self.assertFalse(state.active_web_notice_visible)
        db.commit.assert_awaited_once()
        publish_mock.assert_called_once()
        self.assertEqual(publish_mock.call_args.args[0], "market:notice_hidden")

    async def test_register_market_offer_created_initializes_missing_state(self):
        db = SimpleNamespace(commit=AsyncMock(), add=Mock())
        evaluation = MarketScheduleEvaluation(
            is_open=False,
            reason="after_daily_window_close",
            next_transition_at=None,
            timezone="Asia/Tehran",
        )
        now = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)

        with patch.object(
            market_transition_service,
            "evaluate_current_market_schedule",
            new=AsyncMock(return_value=evaluation),
        ), patch.object(
            market_transition_service,
            "_acquire_market_runtime_lock",
            new=AsyncMock(),
        ), patch.object(
            market_transition_service,
            "get_market_runtime_state",
            new=AsyncMock(return_value=None),
        ):
            state = await market_transition_service.register_market_offer_created(db, current_time=now)

        self.assertFalse(state.is_open)
        self.assertEqual(state.offers_since_last_open, 1)
        db.add.assert_called_once_with(state)
        db.commit.assert_awaited_once()

    async def test_register_market_offer_created_logs_notice_hidden_publish_failures(self):
        db = SimpleNamespace(commit=AsyncMock())
        evaluation = MarketScheduleEvaluation(
            is_open=True,
            reason="daily_window_open",
            next_transition_at=None,
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
            "_acquire_market_runtime_lock",
            new=AsyncMock(),
        ), patch.object(
            market_transition_service,
            "get_market_runtime_state",
            new=AsyncMock(return_value=state),
        ), patch("core.services.market_transition_service.publish_event_sync", side_effect=RuntimeError("redis down")), patch(
            "core.services.market_transition_service.logger"
        ) as logger:
            result = await market_transition_service.register_market_offer_created(db)

        self.assertIs(result, state)
        self.assertFalse(state.active_web_notice_visible)
        logger.warning.assert_called_once()

    async def test_apply_market_open_transition_logs_notice_and_event_failures(self):
        state = MarketRuntimeState(id=1, is_open=False, active_web_notice_visible=False, offers_since_last_open=2)
        db = SimpleNamespace(commit=AsyncMock())

        with patch.object(
            market_transition_service,
            "_send_market_channel_notice",
            new=AsyncMock(side_effect=RuntimeError("telegram down")),
        ), patch(
            "core.services.market_transition_service.publish_event_sync",
            side_effect=RuntimeError("pubsub down"),
        ), patch("core.services.market_transition_service.logger") as logger:
            result = await market_transition_service._apply_market_open_transition(db, state)

        self.assertTrue(result.changed)
        self.assertEqual(result.transition, "opened")
        self.assertTrue(state.is_open)
        self.assertTrue(state.active_web_notice_visible)
        self.assertEqual(logger.warning.call_count, 2)

    async def test_apply_market_closed_transition_logs_side_effect_failures(self):
        state = MarketRuntimeState(id=1, is_open=True, active_web_notice_visible=False, offers_since_last_open=1)
        offer = SimpleNamespace(id=11, status=OfferStatus.ACTIVE, channel_message_id=101, user_id=5)
        db = SimpleNamespace(commit=AsyncMock())

        with patch.object(
            market_transition_service,
            "_load_active_local_offers",
            new=AsyncMock(return_value=[offer]),
        ), patch(
            "core.services.market_transition_service.remove_channel_buttons",
            new=AsyncMock(side_effect=RuntimeError("telegram down")),
        ), patch(
            "core.cache.decr_active_offer_count",
            new=AsyncMock(side_effect=RuntimeError("cache down")),
        ), patch.object(
            market_transition_service,
            "_send_market_channel_notice",
            new=AsyncMock(side_effect=RuntimeError("notice down")),
        ), patch(
            "core.services.market_transition_service.publish_event_sync",
            side_effect=RuntimeError("event down"),
        ), patch("core.services.market_transition_service.logger") as logger:
            result = await market_transition_service._apply_market_closed_transition(db, state)

        self.assertTrue(result.changed)
        self.assertEqual(result.expired_offer_ids, (11,))
        self.assertEqual(offer.expire_reason, "market_closed")
        self.assertEqual(logger.warning.call_count, 4)

    async def test_apply_market_schedule_transition_initializes_missing_runtime_state_without_change(self):
        db = SimpleNamespace(commit=AsyncMock(), add=Mock())
        evaluation = MarketScheduleEvaluation(
            is_open=True,
            reason="daily_window_open",
            next_transition_at=None,
            timezone="Asia/Tehran",
        )

        with patch.object(market_transition_service, "_acquire_market_runtime_lock", new=AsyncMock()), patch.object(
            market_transition_service,
            "get_market_runtime_state",
            new=AsyncMock(return_value=None),
        ):
            result = await market_transition_service.apply_market_schedule_transition(db, evaluation)

        self.assertFalse(result.changed)
        self.assertIsNone(result.transition)
        db.add.assert_called_once_with(result.state)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()