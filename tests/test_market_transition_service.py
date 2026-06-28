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
        self._response = response or SimpleNamespace(status_code=200, text="", raise_for_status=Mock())
        self.post = AsyncMock(side_effect=side_effect, return_value=self._response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class MarketTransitionServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        market_transition_service.invalidate_market_runtime_view_cache()

    def tearDown(self):
        market_transition_service.invalidate_market_runtime_view_cache()

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
            "core.telegram_gateway.httpx.AsyncClient"
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
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=fake_client,
        ):
            await market_transition_service._send_market_channel_notice("market opened")

        fake_client.post.assert_awaited_once_with(
            "https://api.telegram.org/bottoken-123/sendMessage",
            json={"chat_id": "@market", "text": "market opened"},
            timeout=10,
        )

    def test_market_channel_notice_dedupe_key_is_stable_and_transition_sensitive(self):
        transition_at = datetime(2026, 6, 28, 5, 30, tzinfo=timezone.utc)

        first = market_transition_service.market_channel_notice_dedupe_key(
            transition=market_transition_service.MARKET_NOTICE_TRANSITION_OPENED,
            transition_at=transition_at,
            notice_text=market_transition_service.MARKET_OPENED_CHANNEL_NOTICE,
        )
        second = market_transition_service.market_channel_notice_dedupe_key(
            transition=market_transition_service.MARKET_NOTICE_TRANSITION_OPENED,
            transition_at=transition_at,
            notice_text=market_transition_service.MARKET_OPENED_CHANNEL_NOTICE,
        )
        closed = market_transition_service.market_channel_notice_dedupe_key(
            transition=market_transition_service.MARKET_NOTICE_TRANSITION_CLOSED,
            transition_at=transition_at,
            notice_text=market_transition_service.MARKET_CLOSED_CHANNEL_NOTICE,
        )

        self.assertEqual(first, second)
        self.assertNotEqual(first, closed)
        self.assertTrue(first.startswith("market-channel-notice:opened:2026-06-28T05:30:00Z:"))

    async def test_reconcile_market_channel_notice_skips_on_non_foreign_server(self):
        state = MarketRuntimeState(
            id=1,
            is_open=True,
            active_web_notice_visible=True,
            offers_since_last_open=0,
            last_transition_at=datetime(2026, 6, 28, 5, 30, tzinfo=timezone.utc),
        )

        with patch("core.services.market_transition_service.current_server", return_value="iran"), patch.object(
            market_transition_service,
            "_get_or_create_market_notice_receipt",
            new=AsyncMock(),
        ) as receipt_mock:
            result = await market_transition_service.reconcile_market_channel_notice_for_state(
                SimpleNamespace(),
                state,
                source="sync_receive",
            )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "non_foreign_server")
        receipt_mock.assert_not_awaited()

    async def test_reconcile_market_channel_notice_sends_and_marks_receipt_sent_on_foreign(self):
        now = datetime(2026, 6, 28, 5, 31, tzinfo=timezone.utc)
        state = MarketRuntimeState(
            id=1,
            is_open=True,
            active_web_notice_visible=True,
            offers_since_last_open=0,
            last_transition_at=datetime(2026, 6, 28, 5, 30, tzinfo=timezone.utc),
        )
        receipt = SimpleNamespace(
            status=market_transition_service.MARKET_NOTICE_STATUS_PENDING,
            attempt_count=0,
            last_error_class=None,
        )
        db = SimpleNamespace(commit=AsyncMock())
        gateway_result = market_transition_service.telegram_gateway.TelegramGatewayResult(
            ok=True,
            method="sendMessage",
            response_json={"result": {"message_id": 44}},
        )

        with patch("core.services.market_transition_service.current_server", return_value="foreign"), patch.object(
            market_transition_service.settings,
            "channel_id",
            "-1001",
        ), patch.object(
            market_transition_service,
            "utc_now",
            return_value=now,
        ), patch.object(
            market_transition_service,
            "_get_or_create_market_notice_receipt",
            new=AsyncMock(return_value=receipt),
        ), patch.object(
            market_transition_service,
            "_send_market_channel_notice",
            new=AsyncMock(return_value=gateway_result),
        ) as send_mock:
            result = await market_transition_service.reconcile_market_channel_notice_for_state(
                db,
                state,
                source="sync_receive",
            )

        self.assertEqual(result.status, "sent")
        self.assertEqual(receipt.status, market_transition_service.MARKET_NOTICE_STATUS_SENT)
        self.assertEqual(receipt.telegram_message_id, 44)
        self.assertEqual(receipt.sent_at, now)
        self.assertEqual(receipt.channel_id, "-1001")
        self.assertEqual(receipt.attempt_count, 1)
        db.commit.assert_awaited_once()
        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args.kwargs["idempotency_key"], result.dedupe_key)

    async def test_reconcile_market_channel_notice_skips_already_sent_receipt(self):
        state = MarketRuntimeState(
            id=1,
            is_open=False,
            active_web_notice_visible=True,
            offers_since_last_open=0,
            last_transition_at=datetime(2026, 6, 28, 15, 30, tzinfo=timezone.utc),
        )
        receipt = SimpleNamespace(
            status=market_transition_service.MARKET_NOTICE_STATUS_SENT,
            attempt_count=1,
            last_error_class=None,
        )
        db = SimpleNamespace(commit=AsyncMock())

        with patch("core.services.market_transition_service.current_server", return_value="foreign"), patch.object(
            market_transition_service,
            "_get_or_create_market_notice_receipt",
            new=AsyncMock(return_value=receipt),
        ), patch.object(
            market_transition_service,
            "_send_market_channel_notice",
            new=AsyncMock(),
        ) as send_mock:
            result = await market_transition_service.reconcile_market_channel_notice_for_state(
                db,
                state,
                source="sync_receive",
            )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "already_sent")
        db.commit.assert_not_awaited()
        send_mock.assert_not_awaited()

    async def test_reconcile_market_channel_notice_marks_missing_channel_skipped(self):
        state = MarketRuntimeState(
            id=1,
            is_open=True,
            active_web_notice_visible=True,
            offers_since_last_open=0,
            last_transition_at=datetime(2026, 6, 28, 5, 30, tzinfo=timezone.utc),
        )
        receipt = SimpleNamespace(
            status=market_transition_service.MARKET_NOTICE_STATUS_PENDING,
            attempt_count=0,
            last_error_class=None,
        )
        db = SimpleNamespace(commit=AsyncMock())

        with patch("core.services.market_transition_service.current_server", return_value="foreign"), patch.object(
            market_transition_service.settings,
            "channel_id",
            None,
        ), patch.object(
            market_transition_service,
            "_get_or_create_market_notice_receipt",
            new=AsyncMock(return_value=receipt),
        ), patch.object(
            market_transition_service,
            "_send_market_channel_notice",
            new=AsyncMock(),
        ) as send_mock:
            result = await market_transition_service.reconcile_market_channel_notice_for_state(
                db,
                state,
                source="sync_receive",
            )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "missing_channel_id")
        self.assertEqual(receipt.status, market_transition_service.MARKET_NOTICE_STATUS_SKIPPED)
        self.assertEqual(receipt.last_error_class, "missing_channel_id")
        db.commit.assert_awaited_once()
        send_mock.assert_not_awaited()

    async def test_reconcile_market_channel_notice_marks_gateway_failure_for_retry(self):
        now = datetime(2026, 6, 28, 5, 31, tzinfo=timezone.utc)
        state = MarketRuntimeState(
            id=1,
            is_open=True,
            active_web_notice_visible=True,
            offers_since_last_open=0,
            last_transition_at=datetime(2026, 6, 28, 5, 30, tzinfo=timezone.utc),
        )
        receipt = SimpleNamespace(
            status=market_transition_service.MARKET_NOTICE_STATUS_PENDING,
            attempt_count=0,
            last_error_class=None,
        )
        db = SimpleNamespace(commit=AsyncMock())
        gateway_result = market_transition_service.telegram_gateway.TelegramGatewayResult(
            ok=False,
            method="sendMessage",
            status_code=403,
            response_text="Forbidden",
            error="Forbidden",
        )

        with patch("core.services.market_transition_service.current_server", return_value="foreign"), patch.object(
            market_transition_service.settings,
            "channel_id",
            "-1001",
        ), patch.object(
            market_transition_service,
            "utc_now",
            return_value=now,
        ), patch.object(
            market_transition_service,
            "_get_or_create_market_notice_receipt",
            new=AsyncMock(return_value=receipt),
        ), patch.object(
            market_transition_service,
            "_send_market_channel_notice",
            new=AsyncMock(return_value=gateway_result),
        ):
            result = await market_transition_service.reconcile_market_channel_notice_for_state(
                db,
                state,
                source="sync_receive",
            )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "Forbidden")
        self.assertEqual(receipt.status, market_transition_service.MARKET_NOTICE_STATUS_FAILED)
        self.assertEqual(receipt.last_error_class, "Forbidden")
        self.assertEqual(receipt.last_error, "Forbidden")
        self.assertEqual(receipt.next_retry_at, now + timedelta(seconds=60))
        self.assertEqual(receipt.attempt_count, 1)
        db.commit.assert_awaited_once()

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
            market_transition_service, "reconcile_market_channel_notice_for_state", new=AsyncMock()
        ) as notice_mock, patch(
            "core.services.market_transition_service.publish_event_sync"
        ) as publish_mock, patch(
            "core.services.market_transition_service.current_server",
            return_value="iran",
        ):
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
            market_transition_service, "reconcile_market_channel_notice_for_state", new=AsyncMock()
        ) as notice_mock, patch(
            "core.services.market_transition_service.publish_event_sync"
        ) as publish_mock, patch(
            "core.services.market_transition_service.current_server",
            return_value="iran",
        ):
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
        notice_mock.assert_awaited_once_with(db, state, source="local_transition")
        publish_mock.assert_called_once()
        self.assertEqual(publish_mock.call_args.args[0], "market:opened")

    async def test_apply_market_closed_transition_expires_local_offers_and_publishes_notice(self):
        state = MarketRuntimeState(id=1, is_open=True, active_web_notice_visible=False, offers_since_last_open=2)
        offers = [
            SimpleNamespace(id=11, status=OfferStatus.ACTIVE, home_server="iran", channel_message_id=101, user_id=5),
            SimpleNamespace(id=12, status=OfferStatus.ACTIVE, home_server="iran", channel_message_id=None, user_id=8),
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
            market_transition_service, "reconcile_market_channel_notice_for_state", new=AsyncMock()
        ) as notice_mock, patch(
            "core.services.market_transition_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.market_transition_service.apply_offer_channel_state",
            new=AsyncMock(),
        ) as apply_channel_state_mock, patch(
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
        self.assertEqual([offer.expire_source_surface for offer in offers], ["system", "system"])
        self.assertEqual([offer.expire_source_server for offer in offers], ["iran", "iran"])
        self.assertEqual([offer.expired_by_user_id for offer in offers], [None, None])
        self.assertEqual([offer.expired_by_actor_user_id for offer in offers], [None, None])
        db.commit.assert_awaited_once()
        self.assertEqual(apply_channel_state_mock.await_count, 2)
        apply_channel_state_mock.assert_any_await(offers[0], reason="market_close_expire")
        apply_channel_state_mock.assert_any_await(offers[1], reason="market_close_expire")
        self.assertEqual(decr_mock.await_count, 2)
        notice_mock.assert_awaited_once_with(db, state, source="local_transition")
        publish_mock.assert_called_once()
        self.assertEqual(publish_mock.call_args.args[0], "market:closed")

    async def test_load_active_local_offers_filters_by_active_status_and_current_server(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=_ExecuteResult(values=[])))

        with patch("core.services.market_transition_service.current_server", return_value="foreign"):
            await market_transition_service._load_active_local_offers(db)

        stmt = db.execute.await_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("offers.status = 'ACTIVE'", compiled)
        self.assertIn("offers.home_server = 'foreign'", compiled)

    async def test_duplicate_closed_schedule_does_not_reexpire_terminal_offers(self):
        state = MarketRuntimeState(
            id=1,
            is_open=False,
            active_web_notice_visible=True,
            offers_since_last_open=0,
            last_transition_at=datetime(2026, 5, 22, 18, 0, tzinfo=timezone.utc),
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ExecuteResult(first=state)),
            add=Mock(),
            commit=AsyncMock(),
        )
        evaluation = MarketScheduleEvaluation(
            is_open=False,
            reason="after_daily_window_close",
            next_transition_at=datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc),
            timezone="Asia/Tehran",
        )

        with patch.object(market_transition_service, "_acquire_market_runtime_lock", new=AsyncMock()) as lock_mock, patch.object(
            market_transition_service,
            "_load_active_local_offers",
            new=AsyncMock(),
        ) as load_offers_mock, patch.object(
            market_transition_service,
            "expire_offers_authoritatively",
            new=AsyncMock(),
        ) as expire_mock, patch.object(
            market_transition_service,
            "reconcile_market_channel_notice_for_state",
            new=AsyncMock(),
        ) as notice_mock, patch(
            "core.services.market_transition_service.publish_event_sync"
        ) as publish_mock, patch(
            "core.services.market_transition_service.current_server",
            return_value="iran",
        ):
            result = await market_transition_service.apply_market_schedule_transition(db, evaluation)

        lock_mock.assert_awaited_once_with(db)
        self.assertFalse(result.changed)
        self.assertIsNone(result.transition)
        self.assertEqual(result.expired_offer_ids, ())
        load_offers_mock.assert_not_awaited()
        expire_mock.assert_not_awaited()
        db.commit.assert_not_awaited()
        notice_mock.assert_not_awaited()
        publish_mock.assert_not_called()

    async def test_apply_market_schedule_transition_on_foreign_uses_side_effect_guard_without_runtime_write(self):
        state = MarketRuntimeState(
            id=1,
            is_open=False,
            active_web_notice_visible=True,
            offers_since_last_open=0,
            last_transition_at=datetime(2026, 5, 22, 18, 0, tzinfo=timezone.utc),
        )
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        evaluation = MarketScheduleEvaluation(
            is_open=True,
            reason="daily_window_open",
            next_transition_at=datetime(2026, 5, 23, 18, 0, tzinfo=timezone.utc),
            timezone="Asia/Tehran",
        )
        guard_result = market_transition_service.MarketTransitionResult(
            changed=False,
            transition=None,
            state=state,
        )

        with patch(
            "core.services.market_transition_service.current_server",
            return_value="foreign",
        ), patch.object(
            market_transition_service,
            "_acquire_market_runtime_lock",
            new=AsyncMock(),
        ) as lock_mock, patch.object(
            market_transition_service,
            "reconcile_market_runtime_side_effects_for_current_state",
            new=AsyncMock(return_value=guard_result),
        ) as guard_mock:
            result = await market_transition_service.apply_market_schedule_transition(db, evaluation)

        self.assertIs(result, guard_result)
        guard_mock.assert_awaited_once_with(db, source="foreign_schedule_guard")
        lock_mock.assert_not_awaited()
        db.execute.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_reconcile_market_runtime_side_effects_expires_foreign_home_offers_on_synced_close(self):
        close_time = datetime(2026, 5, 22, 18, 0, tzinfo=timezone.utc)
        state = MarketRuntimeState(
            id=1,
            is_open=False,
            active_web_notice_visible=True,
            offers_since_last_open=0,
            last_transition_at=close_time,
        )
        offers = [
            SimpleNamespace(id=21, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=201, user_id=5),
            SimpleNamespace(id=22, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=None, user_id=8),
        ]
        db = SimpleNamespace(commit=AsyncMock())

        with patch(
            "core.services.market_transition_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="foreign",
        ), patch.object(
            market_transition_service,
            "_load_active_local_offers",
            new=AsyncMock(return_value=offers),
        ) as load_offers_mock, patch(
            "core.services.market_transition_service.apply_offer_channel_state",
            new=AsyncMock(),
        ) as apply_channel_state_mock, patch(
            "core.cache.decr_active_offer_count",
            new=AsyncMock(),
        ) as decr_mock, patch.object(
            market_transition_service,
            "reconcile_market_channel_notice_for_state",
            new=AsyncMock(),
        ) as notice_mock:
            result = await market_transition_service.reconcile_market_runtime_side_effects_for_state(
                db,
                state,
                source="sync_receive",
            )

        self.assertTrue(result.changed)
        self.assertEqual(result.transition, "closed_local_offer_expiry")
        self.assertEqual(result.expired_offer_ids, (21, 22))
        self.assertEqual([offer.status for offer in offers], [OfferStatus.EXPIRED, OfferStatus.EXPIRED])
        self.assertEqual([offer.expire_reason for offer in offers], ["market_closed", "market_closed"])
        self.assertEqual([offer.expire_source_surface for offer in offers], ["system", "system"])
        self.assertEqual([offer.expire_source_server for offer in offers], ["foreign", "foreign"])
        self.assertEqual([offer.expired_at for offer in offers], [close_time, close_time])
        load_offers_mock.assert_awaited_once()
        db.commit.assert_awaited_once()
        self.assertEqual(apply_channel_state_mock.await_count, 2)
        self.assertEqual(decr_mock.await_count, 2)
        notice_mock.assert_awaited_once_with(db, state, source="sync_receive")

    async def test_reconcile_market_runtime_side_effects_does_not_expire_on_open_state(self):
        state = MarketRuntimeState(
            id=1,
            is_open=True,
            active_web_notice_visible=True,
            offers_since_last_open=0,
            last_transition_at=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
        )
        db = SimpleNamespace(commit=AsyncMock())

        with patch(
            "core.services.market_transition_service.current_server",
            return_value="foreign",
        ), patch.object(
            market_transition_service,
            "_load_active_local_offers",
            new=AsyncMock(),
        ) as load_offers_mock, patch.object(
            market_transition_service,
            "reconcile_market_channel_notice_for_state",
            new=AsyncMock(),
        ) as notice_mock:
            result = await market_transition_service.reconcile_market_runtime_side_effects_for_state(
                db,
                state,
                source="sync_receive",
            )

        self.assertFalse(result.changed)
        self.assertIsNone(result.transition)
        self.assertEqual(result.expired_offer_ids, ())
        load_offers_mock.assert_not_awaited()
        db.commit.assert_not_awaited()
        notice_mock.assert_awaited_once_with(db, state, source="sync_receive")

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

    async def test_get_market_runtime_view_uses_short_cache_for_live_reads(self):
        db = SimpleNamespace()
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
        evaluate_mock = AsyncMock(return_value=evaluation)
        state_mock = AsyncMock(return_value=state)

        with patch.object(
            market_transition_service,
            "evaluate_current_market_schedule",
            new=evaluate_mock,
        ), patch.object(
            market_transition_service,
            "get_market_runtime_state",
            new=state_mock,
        ):
            first = await market_transition_service.get_market_runtime_view(db)
            second = await market_transition_service.get_market_runtime_view(db)
            market_transition_service.invalidate_market_runtime_view_cache()
            third = await market_transition_service.get_market_runtime_view(db)

        self.assertIs(first, second)
        self.assertIsNot(first, third)
        self.assertEqual(evaluate_mock.await_count, 2)
        self.assertEqual(state_mock.await_count, 2)

    async def test_get_market_runtime_view_bypasses_cache_for_explicit_time(self):
        db = SimpleNamespace()
        evaluation = MarketScheduleEvaluation(
            is_open=True,
            reason="daily_window_open",
            next_transition_at=None,
            timezone="Asia/Tehran",
        )
        evaluate_mock = AsyncMock(return_value=evaluation)
        state_mock = AsyncMock(return_value=None)
        current_time = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)

        with patch.object(
            market_transition_service,
            "evaluate_current_market_schedule",
            new=evaluate_mock,
        ), patch.object(
            market_transition_service,
            "get_market_runtime_state",
            new=state_mock,
        ):
            await market_transition_service.get_market_runtime_view(db, current_time=current_time)
            await market_transition_service.get_market_runtime_view(db, current_time=current_time)

        self.assertEqual(evaluate_mock.await_count, 2)
        self.assertEqual(state_mock.await_count, 2)

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
            "reconcile_market_channel_notice_for_state",
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
            "core.services.market_transition_service.apply_offer_channel_state",
            new=AsyncMock(side_effect=RuntimeError("telegram down")),
        ), patch(
            "core.cache.decr_active_offer_count",
            new=AsyncMock(side_effect=RuntimeError("cache down")),
        ), patch.object(
            market_transition_service,
            "reconcile_market_channel_notice_for_state",
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
        ), patch(
            "core.services.market_transition_service.current_server",
            return_value="iran",
        ):
            result = await market_transition_service.apply_market_schedule_transition(db, evaluation)

        self.assertFalse(result.changed)
        self.assertIsNone(result.transition)
        db.add.assert_called_once_with(result.state)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
