"""Characterization tests for bot.handlers.trade_manage.handle_expire_offer callback.

Seed: BOT-TRADE-CALLBACK-QUERY-TRADE-MANAGE-HANDLE-EXPIRE-OFFER-L118
Bounded characterization suite under Stage P1-00b and Assignment WA-P1-00B-FREEZE-20260904-001.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_manage import (
    OFFER_EXPIRY_LOCK_BUSY_TEXT,
    handle_expire_offer,
)
from core.services.offer_expiry_limits import OfferManualExpireLimitError
from models.offer import Offer, OfferStatus


class FakeLease:
    """Minimal fake lease tracking release lifecycle and ordering."""

    def __init__(self, acquired: bool = True, events: list[str] | None = None):
        self.acquired = acquired
        self.events = events if events is not None else []
        self.release_calls = 0

        async def _release():
            self.release_calls += 1
            self.events.append("lease_release")

        self.release = AsyncMock(side_effect=_release)


class FakeAsyncSession:
    """Minimal fake async session recording method calls and execution events."""

    def __init__(
        self,
        offers_by_id: dict[int, Any] | None = None,
        events: list[str] | None = None,
    ):
        self.offers_by_id: dict[int, Any] = dict(offers_by_id or {})
        self.events: list[str] = events if events is not None else []
        self.get_calls: list[dict[str, Any]] = []
        self.expunged_objects: list[Any] = []
        self.commits: int = 0
        self.rollbacks: int = 0
        self.commit_side_effect: Exception | None = None

    async def get(self, entity_cls, ident, *args, **kwargs):
        self.get_calls.append(
            {
                "entity_cls": entity_cls,
                "ident": ident,
                "args": args,
                "kwargs": kwargs,
            }
        )
        self.events.append("session_get")
        return self.offers_by_id.get(ident)

    def expunge(self, obj):
        self.expunged_objects.append(obj)
        self.events.append("session_expunge")

    async def scalar(self, stmt, *args, **kwargs):
        self.events.append("session_scalar")
        return 0

    async def commit(self):
        self.commits += 1
        self.events.append("session_commit")
        if self.commit_side_effect:
            raise self.commit_side_effect

    async def rollback(self):
        self.rollbacks += 1
        self.events.append("session_rollback")


class FakeSessionFactory:
    """Context-manager factory for FakeAsyncSession."""

    def __init__(self, session: FakeAsyncSession):
        self.session = session
        self.opened = 0
        self.closed = 0

    def __call__(self):
        factory_self = self

        class _SessionContextManager:
            async def __aenter__(self):
                factory_self.opened += 1
                return factory_self.session

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                factory_self.closed += 1
                return False

        return _SessionContextManager()


def make_fake_callback(events: list[str] | None = None) -> SimpleNamespace:
    """Create a minimal CallbackQuery fake tracking UI interaction events."""
    ev = events if events is not None else []

    async def _answer(*args, **kwargs):
        ev.append("callback_answer")

    async def _edit_reply_markup(*args, **kwargs):
        ev.append("edit_reply_markup")

    async def _message_answer(*args, **kwargs):
        ev.append("callback_message_answer")

    answer_mock = AsyncMock(side_effect=_answer)
    edit_markup_mock = AsyncMock(side_effect=_edit_reply_markup)
    msg_answer_mock = AsyncMock(side_effect=_message_answer)

    return SimpleNamespace(
        id="cb-trade-expire-test",
        answer=answer_mock,
        message=SimpleNamespace(
            edit_reply_markup=edit_markup_mock,
            answer=msg_answer_mock,
        ),
    )


class CharacterizationTradeExpireOfferTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for handle_expire_offer callback.

    Seed: BOT-TRADE-CALLBACK-QUERY-TRADE-MANAGE-HANDLE-EXPIRE-OFFER-L118
    Characterizes boundary interactions, guard ordering, rate limiting,
    authoritative expiry, channel notifications, UI lifecycle, and failure handling.
    """

    async def asyncSetUp(self):
        self.trading_settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=99,
        )

        patcher_ts = patch(
            "bot.handlers.trade_manage.get_trading_settings",
            return_value=self.trading_settings,
        )
        self.mock_get_trading_settings = patcher_ts.start()
        self.addCleanup(patcher_ts.stop)

        patcher_runtime = patch(
            "bot.handlers.trade_manage.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="legacy"),
        )
        self.mock_delivery_runtime = patcher_runtime.start()
        self.addCleanup(patcher_runtime.stop)

        patcher_srv_bot = patch(
            "bot.handlers.trade_manage.current_server",
            return_value="srv-local",
        )
        self.mock_current_server_bot = patcher_srv_bot.start()
        self.addCleanup(patcher_srv_bot.stop)

        patcher_srv_svc = patch(
            "core.services.offer_expiry_service.current_server",
            return_value="srv-local",
        )
        self.mock_current_server_svc = patcher_srv_svc.start()
        self.addCleanup(patcher_srv_svc.stop)

        patcher_remote = patch(
            "bot.handlers.trade_manage.is_remote_home",
            return_value=False,
        )
        self.mock_is_remote_home = patcher_remote.start()
        self.addCleanup(patcher_remote.stop)

        patcher_overtime = patch(
            "core.services.offer_expiry_service._invalidate_overtime_after_offer_expiry",
            new=AsyncMock(),
        )
        self.mock_invalidate_overtime = patcher_overtime.start()
        self.addCleanup(patcher_overtime.stop)

        self.mock_apply_offer_channel_state = AsyncMock()
        patcher_channel = patch(
            "bot.handlers.trade_manage.apply_offer_channel_state",
            new=self.mock_apply_offer_channel_state,
        )
        patcher_channel.start()
        self.addCleanup(patcher_channel.stop)

        self.mock_build_keyboard = AsyncMock(return_value="NAV_KEYBOARD")
        patcher_nav = patch(
            "bot.handlers.trade_manage.build_persistent_navigation_keyboard",
            new=self.mock_build_keyboard,
        )
        patcher_nav.start()
        self.addCleanup(patcher_nav.stop)

        self.mock_enforce_manual_limits = AsyncMock()
        patcher_limits = patch(
            "bot.handlers.trade_manage.enforce_manual_offer_expire_limits",
            new=self.mock_enforce_manual_limits,
        )
        patcher_limits.start()
        self.addCleanup(patcher_limits.stop)

        self.mock_acquire_gate = AsyncMock()
        patcher_gate = patch(
            "bot.handlers.trade_manage.try_acquire_offer_expiry_gate",
            new=self.mock_acquire_gate,
        )
        patcher_gate.start()
        self.addCleanup(patcher_gate.stop)

        self.session_factory = FakeSessionFactory(FakeAsyncSession())
        patcher_session = patch(
            "bot.handlers.trade_manage.AsyncSessionLocal",
            new=self.session_factory,
        )
        patcher_session.start()
        self.addCleanup(patcher_session.stop)

    def _create_context(self):
        events: list[str] = []
        lease = FakeLease(acquired=True, events=events)
        session = FakeAsyncSession(events=events)
        self.session_factory.session = session
        self.mock_acquire_gate.return_value = lease
        callback = make_fake_callback(events=events)

        async def _record_channel_apply(*args, **kwargs):
            events.append("apply_channel_state")

        self.mock_apply_offer_channel_state.side_effect = _record_channel_apply
        self.mock_apply_offer_channel_state.reset_mock()

        async def _record_manual_limits(*args, **kwargs):
            events.append("enforce_manual_limits")

        self.mock_enforce_manual_limits.side_effect = _record_manual_limits
        self.mock_enforce_manual_limits.reset_mock()
        self.mock_build_keyboard.reset_mock()
        self.mock_acquire_gate.reset_mock()
        self.mock_get_trading_settings.reset_mock()

        return events, lease, session, callback

    async def test_local_home_active_offer_success_commits_before_channel_and_ui_effects(self):
        """Test local-home active offer expiry success ordering and effects.

        Exercises the real handle_expire_offer callback and expire_offer_authoritatively service.
        Proves from the ordered event log that the database commit occurs strictly before
        channel application and user-facing success effects.
        """
        events, lease, session, callback = self._create_context()
        offer = SimpleNamespace(
            id=42,
            user_id=7,
            status=OfferStatus.ACTIVE,
            home_server="srv-local",
            channel_message_id=101,
            offer_public_id="ofr_test_42",
        )
        session.offers_by_id[42] = offer

        user = SimpleNamespace(id=7)
        callback_data = SimpleNamespace(offer_id=42)

        await handle_expire_offer(
            callback,
            callback_data,
            user=user,
            bot=SimpleNamespace(),
        )

        # 1. Assert row-lock re-read with exact query options
        self.assertEqual(len(session.get_calls), 2)
        self.assertIs(session.get_calls[0]["entity_cls"], Offer)
        self.assertEqual(session.get_calls[0]["ident"], 42)
        self.assertEqual(session.get_calls[0]["args"], ())
        self.assertEqual(session.get_calls[0]["kwargs"], {})
        self.assertIs(session.get_calls[1]["entity_cls"], Offer)
        self.assertEqual(session.get_calls[1]["ident"], 42)
        self.assertEqual(session.get_calls[1]["args"], ())
        self.assertEqual(
            session.get_calls[1]["kwargs"],
            {"with_for_update": {"nowait": True}, "populate_existing": True},
        )
        self.assertEqual(session.expunged_objects, [offer])

        # 2. Consumes manual-limit boundary once for that owner and trading settings
        self.mock_enforce_manual_limits.assert_awaited_once_with(
            session,
            owner_user_id=7,
            trading_settings=self.trading_settings,
        )

        # 3. Runs actual expiry service and mutates offer exactly
        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertEqual(offer.expire_reason, "manual")
        self.assertEqual(offer.expired_by_user_id, 7)
        self.assertEqual(offer.expired_by_actor_user_id, 7)
        self.assertEqual(offer.expire_source_surface, "telegram_bot")
        self.assertEqual(offer.expire_source_server, "srv-local")
        self.assertIsNotNone(getattr(offer, "expired_at", None))

        # 4. Exactly one commit in legacy delivery mode
        self.assertEqual(session.commits, 1)

        # 5. Channel application with exact parameters
        self.mock_apply_offer_channel_state.assert_awaited_once_with(
            offer,
            reason="manual_expire",
            timeout=10,
        )

        # 6. Callback acknowledgement
        callback.answer.assert_awaited_once_with()

        # 7. Reply-markup removal
        callback.message.edit_reply_markup.assert_awaited_once_with(
            reply_markup=None,
        )

        # 8. Preserved Persian success message and persistent navigation keyboard call
        callback.message.answer.assert_awaited_once()
        self.assertIn("✅ لفظ شما منقضی شد.", callback.message.answer.await_args.args[0])
        self.assertEqual(
            callback.message.answer.await_args.kwargs.get("reply_markup"),
            "NAV_KEYBOARD",
        )
        self.mock_build_keyboard.assert_awaited_once()

        # 9. Exactly one lease release
        self.assertEqual(lease.release_calls, 1)
        lease.release.assert_awaited_once()

        # 10. Prove from ordered event log that commit occurs before channel application and UI effects
        self.assertIn("session_commit", events)
        self.assertIn("apply_channel_state", events)
        self.assertIn("callback_answer", events)
        self.assertIn("edit_reply_markup", events)
        self.assertIn("callback_message_answer", events)
        self.assertIn("lease_release", events)

        commit_idx = events.index("session_commit")
        channel_idx = events.index("apply_channel_state")
        answer_idx = events.index("callback_answer")
        edit_markup_idx = events.index("edit_reply_markup")
        msg_answer_idx = events.index("callback_message_answer")
        lease_release_idx = events.index("lease_release")

        self.assertLess(commit_idx, channel_idx)
        self.assertLess(commit_idx, answer_idx)
        self.assertLess(commit_idx, edit_markup_idx)
        self.assertLess(commit_idx, msg_answer_idx)
        self.assertLess(msg_answer_idx, lease_release_idx)

    async def test_rate_limit_rejection_answers_prefixed_detail_and_aborts_mutation(self):
        """Test rate-limit rejection handling after row-lock re-read.

        When enforce_manual_offer_expire_limits raises OfferManualExpireLimitError,
        the handler must answer with the prefixed error detail ('❌ <detail>'),
        leave the offer ACTIVE with no expiry fields set, refrain from commit,
        channel application, or UI updates, and release the lease exactly once.
        """
        events, lease, session, callback = self._create_context()
        offer = SimpleNamespace(
            id=42,
            user_id=7,
            status=OfferStatus.ACTIVE,
            home_server="srv-local",
            channel_message_id=101,
            offer_public_id="ofr_test_42",
        )
        session.offers_by_id[42] = offer

        deterministic_detail = "سقف تعداد منقضی دستی در دقیقه پر شده است"

        async def _raise_limit(*args, **kwargs):
            events.append("enforce_manual_limits")
            raise OfferManualExpireLimitError(deterministic_detail)

        self.mock_enforce_manual_limits.side_effect = _raise_limit

        user = SimpleNamespace(id=7)
        callback_data = SimpleNamespace(offer_id=42)

        await handle_expire_offer(
            callback,
            callback_data,
            user=user,
            bot=SimpleNamespace(),
        )

        # 1. Row-lock re-read occurred before the limit check
        self.assertEqual(len(session.get_calls), 2)
        self.assertIs(session.get_calls[0]["entity_cls"], Offer)
        self.assertEqual(session.get_calls[0]["ident"], 42)
        self.assertEqual(session.get_calls[0]["args"], ())
        self.assertEqual(session.get_calls[0]["kwargs"], {})
        self.assertIs(session.get_calls[1]["entity_cls"], Offer)
        self.assertEqual(session.get_calls[1]["ident"], 42)
        self.assertEqual(session.get_calls[1]["args"], ())
        self.assertEqual(
            session.get_calls[1]["kwargs"],
            {"with_for_update": {"nowait": True}, "populate_existing": True},
        )
        self.assertEqual(session.expunged_objects, [offer])
        self.assertLess(events.index("session_get"), events.index("enforce_manual_limits"))

        # 2. Answers with preserved prefixed detail
        callback.answer.assert_awaited_once_with(f"❌ {deterministic_detail}")

        # 3. Does not call real expiry mutation path: offer remains ACTIVE with no expiry fields
        self.assertEqual(offer.status, OfferStatus.ACTIVE)
        self.assertIsNone(getattr(offer, "expired_at", None))
        self.assertIsNone(getattr(offer, "expire_reason", None))
        self.assertIsNone(getattr(offer, "expired_by_user_id", None))
        self.assertIsNone(getattr(offer, "expired_by_actor_user_id", None))
        self.assertIsNone(getattr(offer, "expire_source_surface", None))
        self.assertIsNone(getattr(offer, "expire_source_server", None))

        # 4. No commit, no channel application, no markup edit, no success navigation
        self.assertEqual(session.commits, 0)
        self.mock_apply_offer_channel_state.assert_not_awaited()
        callback.message.edit_reply_markup.assert_not_awaited()
        callback.message.answer.assert_not_awaited()
        self.mock_build_keyboard.assert_not_awaited()

        # 5. Releases the lease exactly once
        self.assertEqual(lease.release_calls, 1)
        lease.release.assert_awaited_once()

    async def test_ownership_and_inactive_guards_reject_before_row_lock_and_expiry(self):
        """Test ownership and inactive guards in separate subtests.

        Verifies that non-owner and inactive offers reject before row-lock re-read,
        manual limits, commit, channel application, or UI updates, releasing the lease.
        """
        user = SimpleNamespace(id=7)
        callback_data = SimpleNamespace(offer_id=42)

        with self.subTest("non_owner_active"):
            events, lease, session, callback = self._create_context()
            non_owner_offer = SimpleNamespace(
                id=42,
                user_id=999,
                status=OfferStatus.ACTIVE,
                home_server="srv-local",
                channel_message_id=101,
            )
            session.offers_by_id[42] = non_owner_offer

            await handle_expire_offer(
                callback,
                callback_data,
                user=user,
                bot=SimpleNamespace(),
            )

            callback.answer.assert_awaited_once_with("❌ شما مالک این لفظ نیستید")
            self.assertEqual(len(session.get_calls), 1)
            self.assertIs(session.get_calls[0]["entity_cls"], Offer)
            self.assertEqual(session.get_calls[0]["ident"], 42)
            self.assertEqual(session.get_calls[0]["args"], ())
            self.assertEqual(session.get_calls[0]["kwargs"], {})
            self.assertEqual(len(session.expunged_objects), 0)
            self.mock_enforce_manual_limits.assert_not_awaited()
            self.assertEqual(session.commits, 0)
            self.mock_apply_offer_channel_state.assert_not_awaited()
            callback.message.edit_reply_markup.assert_not_awaited()
            callback.message.answer.assert_not_awaited()
            self.mock_build_keyboard.assert_not_awaited()
            self.assertEqual(lease.release_calls, 1)
            lease.release.assert_awaited_once()

        with self.subTest("owner_completed"):
            events, lease, session, callback = self._create_context()
            completed_offer = SimpleNamespace(
                id=42,
                user_id=7,
                status=OfferStatus.COMPLETED,
                home_server="srv-local",
                channel_message_id=101,
            )
            session.offers_by_id[42] = completed_offer

            await handle_expire_offer(
                callback,
                callback_data,
                user=user,
                bot=SimpleNamespace(),
            )

            callback.answer.assert_awaited_once_with("❌ این لفظ دیگر فعال نیست")
            self.assertEqual(len(session.get_calls), 1)
            self.assertIs(session.get_calls[0]["entity_cls"], Offer)
            self.assertEqual(session.get_calls[0]["ident"], 42)
            self.assertEqual(session.get_calls[0]["args"], ())
            self.assertEqual(session.get_calls[0]["kwargs"], {})
            self.assertEqual(len(session.expunged_objects), 0)
            self.mock_enforce_manual_limits.assert_not_awaited()
            self.assertEqual(session.commits, 0)
            self.mock_apply_offer_channel_state.assert_not_awaited()
            callback.message.edit_reply_markup.assert_not_awaited()
            callback.message.answer.assert_not_awaited()
            self.mock_build_keyboard.assert_not_awaited()
            self.assertEqual(lease.release_calls, 1)
            lease.release.assert_awaited_once()

    async def test_missing_user_and_busy_gate_guards(self):
        """Test missing-user and busy-gate guard behaviors in separate subtests."""
        callback_data = SimpleNamespace(offer_id=42)

        with self.subTest("missing_user"):
            events, lease, session, callback = self._create_context()

            await handle_expire_offer(
                callback,
                callback_data,
                user=None,
                bot=SimpleNamespace(),
            )

            callback.answer.assert_awaited_once_with()
            self.mock_acquire_gate.assert_not_awaited()
            self.assertEqual(self.session_factory.opened, 0)
            self.mock_get_trading_settings.assert_not_called()
            self.assertEqual(lease.release_calls, 0)

        with self.subTest("busy_gate"):
            events, lease, session, callback = self._create_context()
            unacquired_lease = FakeLease(acquired=False, events=events)
            self.mock_acquire_gate.return_value = unacquired_lease

            user = SimpleNamespace(id=7)
            await handle_expire_offer(
                callback,
                callback_data,
                user=user,
                bot=SimpleNamespace(),
            )

            self.mock_acquire_gate.assert_awaited_once_with(offer_id=42)
            callback.answer.assert_awaited_once_with(OFFER_EXPIRY_LOCK_BUSY_TEXT)
            self.assertEqual(self.session_factory.opened, 0)
            self.mock_enforce_manual_limits.assert_not_awaited()
            self.mock_apply_offer_channel_state.assert_not_awaited()
            # Characterize current behavior: lease.release() is not invoked because
            # the handler returns before entering the try/finally block.
            self.assertEqual(unacquired_lease.release_calls, 0)
            unacquired_lease.release.assert_not_awaited()

    async def test_commit_failure_propagates_runtime_error_proving_control_flow_without_durable_rollback_claim(self):
        """Prove control-flow ordering on commit failure without durable rollback claims.

        This test exercises the real expire_offer_authoritatively path with a simulated
        session commit failure (distinct RuntimeError). It proves control-flow ordering
        only (error propagation, single commit attempt, cancellation of downstream channel
        and UI updates, exactly one lease release) and makes no claim about durable database
        rollback semantics.
        """
        events, lease, session, callback = self._create_context()
        offer = SimpleNamespace(
            id=42,
            user_id=7,
            status=OfferStatus.ACTIVE,
            home_server="srv-local",
            channel_message_id=101,
            offer_public_id="ofr_test_42",
        )
        session.offers_by_id[42] = offer
        session.commit_side_effect = RuntimeError("simulated session commit failure")

        user = SimpleNamespace(id=7)
        callback_data = SimpleNamespace(offer_id=42)

        with self.assertRaises(RuntimeError) as ctx:
            await handle_expire_offer(
                callback,
                callback_data,
                user=user,
                bot=SimpleNamespace(),
            )

        self.assertEqual(str(ctx.exception), "simulated session commit failure")
        self.assertEqual(session.commits, 1)
        self.assertEqual(len(session.get_calls), 2)
        self.assertIs(session.get_calls[0]["entity_cls"], Offer)
        self.assertEqual(session.get_calls[0]["ident"], 42)
        self.assertEqual(session.get_calls[0]["args"], ())
        self.assertEqual(session.get_calls[0]["kwargs"], {})
        self.assertIs(session.get_calls[1]["entity_cls"], Offer)
        self.assertEqual(session.get_calls[1]["ident"], 42)
        self.assertEqual(session.get_calls[1]["args"], ())
        self.assertEqual(
            session.get_calls[1]["kwargs"],
            {"with_for_update": {"nowait": True}, "populate_existing": True},
        )
        self.assertEqual(session.expunged_objects, [offer])
        self.mock_apply_offer_channel_state.assert_not_awaited()
        callback.message.edit_reply_markup.assert_not_awaited()
        callback.message.answer.assert_not_awaited()
        self.mock_build_keyboard.assert_not_awaited()
        self.assertEqual(lease.release_calls, 1)
        lease.release.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
