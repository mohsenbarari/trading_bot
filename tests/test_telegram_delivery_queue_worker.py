import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from core import telegram_delivery_queue_worker as worker
from core.telegram_delivery_credentials import TelegramDeliveryCredentialRegistry
from core.telegram_delivery_queue_limiter import (
    TelegramDeliveryDispatchAdmission,
    TelegramDeliveryLimiterUnavailableError,
)
from core.telegram_delivery_preflight import (
    TelegramDeliveryPreflightFailedError,
    TelegramDeliveryPreflightIdentityReport,
    TelegramDeliveryPreflightReport,
    TelegramDeliveryPreflightRateLimitedError,
)
from core.telegram_delivery_queue_worker import (
    TelegramDeliveryQueueImplementationIncompleteError,
    build_telegram_delivery_queue_lane_specs,
    configured_telegram_delivery_lane_identities,
    run_telegram_delivery_queue_cycle,
    telegram_delivery_queue_loop,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)


class _AllowLimiter:
    async def acquire(self, _job, *, now):
        return TelegramDeliveryDispatchAdmission(allowed=True)

    async def observe(self, _job, _decision, *, now):
        return None

    async def extend_destination_cooldown(self, _job, *, until):
        return None

    async def extend_bot_cooldown(self, _bot_identity, *, until):
        return None

    async def prepare_preflight(self, _bot_identity):
        return True

    async def preflight_gate_open(self, _bot_identity):
        return True


class _CancelledAfterProbeLimiter(_AllowLimiter):
    def __init__(self):
        self.observations = []

    async def acquire(self, _job, *, now):
        # Model a Redis EVAL that reserved the probe before cancellation was
        # delivered to the Python caller.
        raise asyncio.CancelledError

    async def observe(self, job, decision, *, now):
        self.observations.append((job, decision, now))


class _RecordingCooldownLimiter(_AllowLimiter):
    def __init__(self):
        self.bot_cooldowns = []

    async def extend_bot_cooldown(self, bot_identity, *, until):
        self.bot_cooldowns.append((bot_identity, until))


class _FailingCooldownLimiter(_AllowLimiter):
    async def extend_bot_cooldown(self, _bot_identity, *, until):
        raise TelegramDeliveryLimiterUnavailableError(
            "synthetic_preflight_cooldown_persistence_failure"
        )


class _NoopLifecycleFeedback:
    async def assert_dispatchable(self, _db, _job, _now):
        return None

    async def apply_freshness(self, _db, _job, _decision, _now):
        return None

    async def apply_delivery_result(self, _db, _job, _decision, _now):
        return None


class TelegramDeliveryQueueWorkerSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._channel_id_patcher = patch.object(
            worker.settings,
            "channel_id",
            -1001234567890,
        )
        self._channel_id_patcher.start()
        self.addCleanup(self._channel_id_patcher.stop)
        self._preflight_success_patcher = patch.object(
            worker,
            "_persist_preflight_success_gate",
            new=AsyncMock(),
        )
        self.persist_preflight_success = self._preflight_success_patcher.start()
        self.addCleanup(self._preflight_success_patcher.stop)

    @staticmethod
    def _queue_runtime():
        return TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )

    @staticmethod
    def _rehydration(
        *,
        restored_count=0,
        blocked_bots=(),
        cooldown_destinations=(),
        hard_destinations=(),
        gateway_blocked=False,
    ):
        return worker.TelegramDeliveryLimiterRehydrationReport(
            restored_count=restored_count,
            blocked_bot_identities=tuple(blocked_bots),
            cooldown_destination_keys=tuple(cooldown_destinations),
            hard_blocked_destination_keys=tuple(hard_destinations),
            gateway_blocked=gateway_blocked,
        )

    @staticmethod
    def _credentials(*, editor=False):
        return TelegramDeliveryCredentialRegistry.from_values(
            primary_token="test-primary-token",
            editor_enabled=editor,
            editor_token="test-editor-token" if editor else None,
        )

    @staticmethod
    def _preflight_report(*identities):
        return TelegramDeliveryPreflightReport(
            approved_bot_identities=tuple(identities),
            channel_fingerprint="test-channel-fingerprint",
            identities=tuple(
                TelegramDeliveryPreflightIdentityReport(
                    bot_identity=identity,
                    credential_fingerprint=f"credential-{identity}",
                    bot_fingerprint=f"bot-{identity}",
                    channel_fingerprint="test-channel-fingerprint",
                    member_status="administrator",
                    effective_permissions=("can_manage_chat",),
                )
                for identity in identities
            ),
        )

    async def test_channel_cooldown_keeps_primary_private_lane_available(self):
        channel_destination = f"channel:{int(worker.settings.channel_id)}"
        cooldown = self._rehydration(
            cooldown_destinations=(channel_destination,),
        )
        primary_mode = worker._telegram_delivery_lane_start_mode(
            SimpleNamespace(bot_identity="primary"),
            rehydration=cooldown,
            channel_destination_key=channel_destination,
        )
        editor_mode = worker._telegram_delivery_lane_start_mode(
            SimpleNamespace(bot_identity="channel_editor"),
            rehydration=cooldown,
            channel_destination_key=channel_destination,
        )
        self.assertEqual(primary_mode, (True, True))
        self.assertEqual(editor_mode, (False, False))

    async def test_private_only_lane_exits_for_full_preflight_when_channel_gate_clears(self):
        channel_destination = f"channel:{int(worker.settings.channel_id)}"
        limiter = _AllowLimiter()
        lane = SimpleNamespace(
            bot_identity="primary",
            freshness_validator=AsyncMock(),
            lifecycle_feedback=_NoopLifecycleFeedback(),
            gateway_call=AsyncMock(),
            dispatch_limiter=limiter,
        )
        cycle = AsyncMock()
        with patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=AsyncMock(
                side_effect=(
                    self._rehydration(
                        cooldown_destinations=(channel_destination,),
                    ),
                    self._rehydration(),
                )
            ),
        ), patch(
            "core.telegram_delivery_queue_worker.run_telegram_delivery_queue_cycle",
            new=cycle,
        ), patch(
            "core.telegram_delivery_queue_worker.asyncio.sleep",
            new=AsyncMock(),
        ):
            await worker.telegram_delivery_private_only_lane_loop(
                lane,
                channel_destination_key=channel_destination,
            )

        cycle.assert_awaited_once()
        self.assertEqual(
            cycle.await_args.kwargs["allowed_destination_classes"],
            {worker.TelegramDestinationClass.PRIVATE},
        )

    async def test_cycle_without_authoritative_freshness_adapter_refuses_before_db_touch(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal"
        ) as session_factory, patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "authoritative_freshness_validator_not_installed",
            ):
                await run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    gateway_call=AsyncMock(),
                )

        session_factory.assert_not_called()

    async def test_cancelled_acquire_releases_possible_probe_without_masking_cancellation(self):
        job = SimpleNamespace(
            id=41,
            lease_token=7,
            bot_identity="primary",
            destination_key="private:1001",
        )
        db = AsyncMock()
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=db)
        session_context.__aexit__ = AsyncMock(return_value=False)
        limiter = _CancelledAfterProbeLimiter()
        release = AsyncMock()

        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            return_value=session_context,
        ), patch(
            "core.telegram_delivery_queue_worker.claim_next_telegram_delivery_job",
            new=AsyncMock(return_value=job),
        ), patch(
            "core.telegram_delivery_queue_worker.apply_telegram_delivery_freshness_result",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.telegram_delivery_queue_worker._recover_expired_leases",
            new=AsyncMock(return_value=0),
        ), patch(
            "core.telegram_delivery_queue_worker._release_after_predispatch_error",
            new=release,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    freshness_validator=AsyncMock(return_value=object()),
                    lifecycle_feedback=_NoopLifecycleFeedback(),
                    gateway_call=AsyncMock(),
                    dispatch_limiter=limiter,
                    recover_leases=False,
                    limit=1,
                )

        release.assert_awaited_once_with(
            job_id=41,
            worker_id=ANY,
            lease_token=7,
            reason="worker_cancelled_before_dispatch",
        )
        self.assertEqual(len(limiter.observations), 1)
        self.assertIs(limiter.observations[0][0], job)
        self.assertEqual(
            limiter.observations[0][1].reason,
            "rate_limit_probe_cancelled_before_dispatch",
        )

    async def test_cycle_refuses_before_db_when_queue_is_not_runtime_owner(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=TelegramDeliveryRuntimeDecision(
                mode=TelegramDeliveryRuntimeMode.LEGACY,
                legacy_workers_enabled=True,
                queue_worker_enabled=False,
            ),
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal"
        ) as session_factory:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "queue_worker_is_not_runtime_owner",
            ):
                await run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    freshness_validator=AsyncMock(),
                    gateway_call=AsyncMock(),
                )

        session_factory.assert_not_called()

    async def test_cycle_without_lane_gateway_refuses_before_db_touch(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal"
        ) as session_factory, patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "telegram_lane_gateway_not_installed:primary",
            ):
                await run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    freshness_validator=AsyncMock(),
                )

        session_factory.assert_not_called()

    async def test_cycle_without_lifecycle_feedback_refuses_before_db_touch(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal"
        ) as session_factory, patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "telegram_lifecycle_feedback_not_installed:primary",
            ):
                await run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    freshness_validator=AsyncMock(),
                    gateway_call=AsyncMock(),
                )

        session_factory.assert_not_called()

    async def test_cycle_rejects_unknown_lane_before_db_touch(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal"
        ) as session_factory, patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "telegram_delivery_lane_not_allowlisted",
            ):
                await run_telegram_delivery_queue_cycle(
                    bot_identity="arbitrary-bot",
                    freshness_validator=AsyncMock(),
                    gateway_call=AsyncMock(),
                )

        session_factory.assert_not_called()

    def test_lane_specs_require_explicit_adapters_for_each_enabled_identity(self):
        validator = AsyncMock()
        primary_gateway = AsyncMock()
        editor_gateway = AsyncMock()
        feedback = _NoopLifecycleFeedback()
        limiter = _AllowLimiter()
        specs = build_telegram_delivery_queue_lane_specs(
            freshness_validators={
                "primary": validator,
                "channel_editor": validator,
            },
            lifecycle_feedbacks={
                "primary": feedback,
                "channel_editor": feedback,
            },
            gateway_calls={
                "primary": primary_gateway,
                "channel_editor": editor_gateway,
            },
            dispatch_limiter=limiter,
            bot_identities=("primary", "channel_editor"),
        )
        self.assertEqual(
            tuple(spec.bot_identity for spec in specs),
            ("primary", "channel_editor"),
        )
        self.assertIs(specs[0].gateway_call, primary_gateway)
        self.assertIs(specs[1].gateway_call, editor_gateway)
        self.assertIs(specs[0].dispatch_limiter, limiter)
        self.assertIs(specs[1].dispatch_limiter, limiter)

        with self.assertRaisesRegex(
            TelegramDeliveryQueueImplementationIncompleteError,
            "telegram_lane_gateway_not_installed:channel_editor",
        ):
            build_telegram_delivery_queue_lane_specs(
                freshness_validators={
                    "primary": validator,
                    "channel_editor": validator,
                },
                lifecycle_feedbacks={
                    "primary": feedback,
                    "channel_editor": feedback,
                },
                gateway_calls={"primary": primary_gateway},
                dispatch_limiter=limiter,
                bot_identities=("primary", "channel_editor"),
            )

        with self.assertRaisesRegex(
            TelegramDeliveryQueueImplementationIncompleteError,
            "telegram_delivery_lane_set_invalid",
        ):
            build_telegram_delivery_queue_lane_specs(
                freshness_validators={},
                lifecycle_feedbacks={},
                gateway_calls={},
                dispatch_limiter=limiter,
                bot_identities=(),
            )

        with self.assertRaisesRegex(
            TelegramDeliveryQueueImplementationIncompleteError,
            "telegram_lifecycle_feedback_not_installed:channel_editor",
        ):
            build_telegram_delivery_queue_lane_specs(
                freshness_validators={
                    "primary": validator,
                    "channel_editor": validator,
                },
                lifecycle_feedbacks={"primary": feedback},
                gateway_calls={
                    "primary": primary_gateway,
                    "channel_editor": editor_gateway,
                },
                dispatch_limiter=limiter,
                bot_identities=("primary", "channel_editor"),
            )

    async def test_cycle_without_durable_limiter_refuses_before_db_touch(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal"
        ) as session_factory, patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "telegram_delivery_dispatch_limiter_not_installed",
            ):
                await run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    freshness_validator=AsyncMock(),
                    lifecycle_feedback=_NoopLifecycleFeedback(),
                    gateway_call=AsyncMock(),
                )

        session_factory.assert_not_called()

    def test_editor_lane_is_disabled_by_default_and_has_an_independent_flag(self):
        self.assertEqual(configured_telegram_delivery_lane_identities(), ("primary",))
        with patch(
            "core.telegram_delivery_queue_worker.settings.telegram_delivery_queue_channel_editor_enabled",
            True,
        ):
            self.assertEqual(
                configured_telegram_delivery_lane_identities(),
                ("primary", "channel_editor"),
            )

    async def test_supervisor_refuses_missing_lane_adapters_before_task_creation(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.asyncio.create_task"
        ) as create_task, patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=AsyncMock(),
        ) as preflight:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "authoritative_freshness_validator_not_installed:primary",
            ):
                await telegram_delivery_queue_loop(
                    bot_identities=("primary",),
                    credential_registry=self._credentials(),
                    dispatch_limiter=_AllowLimiter(),
                )

        create_task.assert_not_called()
        preflight.assert_not_awaited()

    async def test_supervisor_starts_lanes_recovery_and_feeders_as_independent_tasks(self):
        started_lanes: set[str] = set()
        recovery_started = asyncio.Event()
        trade_feeder_started = asyncio.Event()
        admin_feeder_started = asyncio.Event()
        notification_feeder_started = asyncio.Event()
        market_feeder_started = asyncio.Event()
        scheduled_feeder_started = asyncio.Event()
        offer_feeder_started = asyncio.Event()
        all_started = asyncio.Event()

        async def lane_loop(lane):
            started_lanes.add(lane.bot_identity)
            if (
                len(started_lanes) == 2
                and recovery_started.is_set()
                and trade_feeder_started.is_set()
                and admin_feeder_started.is_set()
                and notification_feeder_started.is_set()
                and market_feeder_started.is_set()
                and offer_feeder_started.is_set()
            ):
                all_started.set()
            await asyncio.Event().wait()

        async def recovery_loop():
            recovery_started.set()
            if (
                len(started_lanes) == 2
                and trade_feeder_started.is_set()
                and admin_feeder_started.is_set()
                and notification_feeder_started.is_set()
                and market_feeder_started.is_set()
                and offer_feeder_started.is_set()
            ):
                all_started.set()
            await asyncio.Event().wait()

        async def trade_feeder_loop():
            trade_feeder_started.set()
            if (
                len(started_lanes) == 2
                and recovery_started.is_set()
                and admin_feeder_started.is_set()
                and notification_feeder_started.is_set()
                and market_feeder_started.is_set()
                and offer_feeder_started.is_set()
            ):
                all_started.set()
            await asyncio.Event().wait()

        async def admin_feeder_loop():
            admin_feeder_started.set()
            if (
                len(started_lanes) == 2
                and recovery_started.is_set()
                and trade_feeder_started.is_set()
                and notification_feeder_started.is_set()
                and market_feeder_started.is_set()
                and offer_feeder_started.is_set()
            ):
                all_started.set()
            await asyncio.Event().wait()

        async def notification_feeder_loop():
            notification_feeder_started.set()
            if (
                len(started_lanes) == 2
                and recovery_started.is_set()
                and trade_feeder_started.is_set()
                and admin_feeder_started.is_set()
                and market_feeder_started.is_set()
                and offer_feeder_started.is_set()
            ):
                all_started.set()
            await asyncio.Event().wait()

        async def market_feeder_loop():
            market_feeder_started.set()
            if (
                len(started_lanes) == 2
                and recovery_started.is_set()
                and trade_feeder_started.is_set()
                and admin_feeder_started.is_set()
                and notification_feeder_started.is_set()
                and offer_feeder_started.is_set()
            ):
                all_started.set()
            await asyncio.Event().wait()

        async def scheduled_feeder_loop():
            scheduled_feeder_started.set()
            await asyncio.Event().wait()

        async def offer_feeder_loop():
            offer_feeder_started.set()
            if (
                len(started_lanes) == 2
                and recovery_started.is_set()
                and trade_feeder_started.is_set()
                and admin_feeder_started.is_set()
                and notification_feeder_started.is_set()
                and market_feeder_started.is_set()
            ):
                all_started.set()
            await asyncio.Event().wait()

        validator = AsyncMock()
        credentials = self._credentials(editor=True)
        limiter = _AllowLimiter()
        async def preflight_for_selected_role(**kwargs):
            return self._preflight_report(*kwargs["bot_identities"])

        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_lane_loop",
            side_effect=lane_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_recovery_loop",
            side_effect=recovery_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_trade_result_queue_handoff_loop",
            side_effect=trade_feeder_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_admin_broadcast_queue_handoff_loop",
            side_effect=admin_feeder_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_notification_outbox_queue_handoff_loop",
            side_effect=notification_feeder_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_market_notice_queue_handoff_loop",
            side_effect=market_feeder_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_scheduled_operation_queue_handoff_loop",
            side_effect=scheduled_feeder_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_offer_queue_handoff_loop",
            side_effect=offer_feeder_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=AsyncMock(side_effect=preflight_for_selected_role),
        ) as preflight, patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=AsyncMock(return_value=self._rehydration()),
        ) as rehydrate:
            supervisor = asyncio.create_task(
                telegram_delivery_queue_loop(
                    freshness_validators={
                        "primary": validator,
                        "channel_editor": validator,
                    },
                    lifecycle_feedbacks={
                        "primary": _NoopLifecycleFeedback(),
                        "channel_editor": _NoopLifecycleFeedback(),
                    },
                    credential_registry=credentials,
                    dispatch_limiter=limiter,
                    bot_identities=("primary", "channel_editor"),
                )
            )
            await asyncio.wait_for(all_started.wait(), timeout=1)
            self.assertEqual(started_lanes, {"primary", "channel_editor"})
            self.assertTrue(recovery_started.is_set())
            self.assertTrue(trade_feeder_started.is_set())
            self.assertTrue(admin_feeder_started.is_set())
            self.assertTrue(notification_feeder_started.is_set())
            self.assertTrue(market_feeder_started.is_set())
            self.assertTrue(scheduled_feeder_started.is_set())
            self.assertTrue(offer_feeder_started.is_set())
            self.assertEqual(preflight.await_count, 2)
            self.assertEqual(
                {call.kwargs["bot_identities"] for call in preflight.await_args_list},
                {("primary",), ("channel_editor",)},
            )
            self.assertTrue(
                all(
                    call.kwargs["identity_only_bot_identities"] == ()
                    for call in preflight.await_args_list
                )
            )
            self.assertEqual(rehydrate.await_count, 3)
            supervisor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await supervisor

    async def test_supervisor_stops_before_tasks_when_rate_limit_replay_fails(self):
        validator = AsyncMock()
        credentials = self._credentials()
        limiter = _AllowLimiter()
        preflight_report = self._preflight_report("primary")
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.asyncio.create_task"
        ) as create_task, patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=AsyncMock(return_value=preflight_report),
        ), patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=AsyncMock(
                side_effect=TelegramDeliveryLimiterUnavailableError(
                    "synthetic_rate_limit_replay_failure"
                )
            ),
        ):
            with self.assertRaisesRegex(
                TelegramDeliveryLimiterUnavailableError,
                "synthetic_rate_limit_replay_failure",
            ):
                await telegram_delivery_queue_loop(
                    freshness_validators={"primary": validator},
                    lifecycle_feedbacks={
                        "primary": _NoopLifecycleFeedback()
                    },
                    credential_registry=credentials,
                    dispatch_limiter=limiter,
                    bot_identities=("primary",),
                )

        create_task.assert_not_called()

    async def test_supervisor_starts_editor_when_primary_bot_is_durably_paused(self):
        started = asyncio.Event()
        started_lanes = []

        async def lane_loop(lane):
            started_lanes.append(lane.bot_identity)
            started.set()
            await asyncio.Event().wait()

        async def idle_loop():
            await asyncio.Event().wait()

        validator = AsyncMock()
        credentials = self._credentials(editor=True)
        limiter = _AllowLimiter()
        preflight_report = self._preflight_report("channel_editor")
        preflight = AsyncMock(return_value=preflight_report)
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_lane_loop",
            side_effect=lane_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_recovery_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_trade_result_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_admin_broadcast_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_notification_outbox_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_market_notice_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_scheduled_operation_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_offer_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=preflight,
        ), patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=AsyncMock(
                return_value=self._rehydration(
                    restored_count=1,
                    blocked_bots=("primary",),
                )
            ),
        ):
            supervisor = asyncio.create_task(
                telegram_delivery_queue_loop(
                    freshness_validators={
                        "primary": validator,
                        "channel_editor": validator,
                    },
                    lifecycle_feedbacks={
                        "primary": _NoopLifecycleFeedback(),
                        "channel_editor": _NoopLifecycleFeedback(),
                    },
                    credential_registry=credentials,
                    dispatch_limiter=limiter,
                    bot_identities=("primary", "channel_editor"),
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertEqual(started_lanes, ["channel_editor"])
            preflight.assert_awaited_once_with(
                settings=worker.settings,
                credential_registry=credentials,
                bot_identities=("channel_editor",),
                identity_only_bot_identities=(),
            )
            supervisor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await supervisor

    async def test_hard_paused_channel_keeps_only_primary_identity_lane(self):
        started = asyncio.Event()
        started_lanes = []

        async def lane_loop(lane):
            started_lanes.append(lane.bot_identity)
            started.set()
            await asyncio.Event().wait()

        async def private_lane_loop(lane, **_kwargs):
            await lane_loop(lane)

        async def idle_loop():
            await asyncio.Event().wait()

        validator = AsyncMock()
        credentials = self._credentials(editor=True)
        limiter = _AllowLimiter()
        preflight = AsyncMock(return_value=self._preflight_report("primary"))
        channel_destination = f"channel:{int(worker.settings.channel_id)}"
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_lane_loop",
            side_effect=lane_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_private_only_lane_loop",
            side_effect=private_lane_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_recovery_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_trade_result_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_admin_broadcast_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_notification_outbox_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_market_notice_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_scheduled_operation_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_offer_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=preflight,
        ), patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=AsyncMock(
                return_value=self._rehydration(
                    restored_count=1,
                    hard_destinations=(channel_destination,),
                )
            ),
        ):
            supervisor = asyncio.create_task(
                telegram_delivery_queue_loop(
                    freshness_validators={
                        "primary": validator,
                        "channel_editor": validator,
                    },
                    lifecycle_feedbacks={
                        "primary": _NoopLifecycleFeedback(),
                        "channel_editor": _NoopLifecycleFeedback(),
                    },
                    credential_registry=credentials,
                    dispatch_limiter=limiter,
                    bot_identities=("primary", "channel_editor"),
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertEqual(started_lanes, ["primary"])
            preflight.assert_awaited_once_with(
                settings=worker.settings,
                credential_registry=credentials,
                bot_identities=("primary",),
                identity_only_bot_identities=("primary",),
            )
            supervisor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await supervisor

    async def test_supervisor_keeps_every_blocked_lane_under_deferred_supervision(self):
        validator = AsyncMock()
        credentials = self._credentials(editor=True)
        channel_destination = f"channel:{int(worker.settings.channel_id)}"
        cases = (
            self._rehydration(blocked_bots=("primary", "channel_editor")),
            self._rehydration(cooldown_destinations=(channel_destination,)),
        )
        for rehydration in cases:
            deferred_roles = set()
            all_deferred = asyncio.Event()

            async def deferred_loop(lane, **_kwargs):
                deferred_roles.add(lane.bot_identity)
                if len(deferred_roles) == 2:
                    all_deferred.set()
                await asyncio.Event().wait()

            async def idle_loop():
                await asyncio.Event().wait()

            with self.subTest(rehydration=rehydration), patch(
                "core.telegram_delivery_queue_worker.assert_background_job_authority"
            ), patch(
                "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
                return_value=self._queue_runtime(),
            ), patch(
                "core.telegram_delivery_queue_worker._telegram_delivery_deferred_lane_activation_loop",
                side_effect=deferred_loop,
            ), patch(
                "core.telegram_delivery_queue_worker.telegram_delivery_queue_recovery_loop",
                side_effect=idle_loop,
            ), patch(
                "core.telegram_delivery_queue_worker.telegram_trade_result_queue_handoff_loop",
                side_effect=idle_loop,
            ), patch(
                "core.telegram_delivery_queue_worker.telegram_admin_broadcast_queue_handoff_loop",
                side_effect=idle_loop,
            ), patch(
                "core.telegram_delivery_queue_worker.telegram_notification_outbox_queue_handoff_loop",
                side_effect=idle_loop,
            ), patch(
                "core.telegram_delivery_queue_worker.telegram_market_notice_queue_handoff_loop",
                side_effect=idle_loop,
            ), patch(
                "core.telegram_delivery_queue_worker.telegram_scheduled_operation_queue_handoff_loop",
                side_effect=idle_loop,
            ), patch(
                "core.telegram_delivery_queue_worker.telegram_offer_queue_handoff_loop",
                side_effect=idle_loop,
            ), patch(
                "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
                new=AsyncMock(),
            ) as preflight, patch(
                "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
                new=AsyncMock(return_value=rehydration),
            ):
                supervisor = asyncio.create_task(
                    telegram_delivery_queue_loop(
                        freshness_validators={
                            "primary": validator,
                            "channel_editor": validator,
                        },
                        lifecycle_feedbacks={
                            "primary": _NoopLifecycleFeedback(),
                            "channel_editor": _NoopLifecycleFeedback(),
                        },
                        credential_registry=credentials,
                        dispatch_limiter=_AllowLimiter(),
                        bot_identities=("primary", "channel_editor"),
                    )
                )
                await asyncio.wait_for(all_deferred.wait(), timeout=1)
                self.assertEqual(
                    deferred_roles,
                    {"primary", "channel_editor"},
                )
                supervisor.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await supervisor
            preflight.assert_not_awaited()

    async def test_gateway_pause_defers_lane_without_ending_supervisor(self):
        validator = AsyncMock()
        credentials = self._credentials()
        limiter = _AllowLimiter()
        preflight = AsyncMock()
        deferred_started = asyncio.Event()

        async def deferred_loop(_lane, **_kwargs):
            deferred_started.set()
            await asyncio.Event().wait()

        async def idle_loop():
            await asyncio.Event().wait()

        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker._telegram_delivery_deferred_lane_activation_loop",
            side_effect=deferred_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_recovery_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_trade_result_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_admin_broadcast_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_notification_outbox_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_market_notice_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_scheduled_operation_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_offer_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=preflight,
        ), patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=AsyncMock(
                return_value=self._rehydration(
                    restored_count=1,
                    gateway_blocked=True,
                )
            ),
        ):
            supervisor = asyncio.create_task(
                telegram_delivery_queue_loop(
                    freshness_validators={"primary": validator},
                    lifecycle_feedbacks={
                        "primary": _NoopLifecycleFeedback()
                    },
                    credential_registry=credentials,
                    dispatch_limiter=limiter,
                    bot_identities=("primary",),
                )
            )
            await asyncio.wait_for(deferred_started.wait(), timeout=1)
            supervisor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await supervisor

        preflight.assert_not_awaited()

    async def test_deferred_lane_rehydrates_then_preflights_and_reactivates(self):
        credentials = self._credentials()
        limiter = _AllowLimiter()
        lane = build_telegram_delivery_queue_lane_specs(
            freshness_validators={"primary": AsyncMock()},
            lifecycle_feedbacks={"primary": _NoopLifecycleFeedback()},
            gateway_calls=credentials.build_gateway_calls(),
            dispatch_limiter=limiter,
            bot_identities=("primary",),
        )[0]
        rehydrate = AsyncMock(
            side_effect=(
                self._rehydration(blocked_bots=("primary",)),
                self._rehydration(),
                self._rehydration(),
            )
        )
        preflight = AsyncMock(return_value=self._preflight_report("primary"))
        lane_loop = AsyncMock(side_effect=asyncio.CancelledError)
        with patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=rehydrate,
        ), patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=preflight,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_lane_loop",
            new=lane_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await worker._telegram_delivery_deferred_lane_activation_loop(
                    lane,
                    credential_registry=credentials,
                    channel_destination_key=f"channel:{int(worker.settings.channel_id)}",
                )

        self.assertEqual(rehydrate.await_count, 3)
        sleep.assert_awaited_once()
        preflight.assert_awaited_once_with(
            settings=worker.settings,
            credential_registry=credentials,
            bot_identities=("primary",),
            identity_only_bot_identities=(),
        )
        lane_loop.assert_awaited_once_with(lane)

    async def test_lane_activation_rechecks_new_gate_after_preflight(self):
        credentials = self._credentials()
        lane = build_telegram_delivery_queue_lane_specs(
            freshness_validators={"primary": AsyncMock()},
            lifecycle_feedbacks={"primary": _NoopLifecycleFeedback()},
            gateway_calls=credentials.build_gateway_calls(),
            dispatch_limiter=_AllowLimiter(),
            bot_identities=("primary",),
        )[0]
        rehydrate = AsyncMock(
            side_effect=(
                self._rehydration(),
                self._rehydration(blocked_bots=("primary",)),
                asyncio.CancelledError(),
            )
        )
        lane_loop = AsyncMock()
        with patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=rehydrate,
        ), patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=AsyncMock(return_value=self._preflight_report("primary")),
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_lane_loop",
            new=lane_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await worker._telegram_delivery_deferred_lane_activation_loop(
                    lane,
                    credential_registry=credentials,
                    channel_destination_key=f"channel:{int(worker.settings.channel_id)}",
                )

        self.assertEqual(rehydrate.await_count, 3)
        self.assertEqual(sleep.await_count, 2)
        lane_loop.assert_not_awaited()

    async def test_runtime_loop_fails_before_task_work_when_queue_cannot_own_execution(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            side_effect=TelegramDeliveryRuntimeConfigurationError(
                "queue_implementation_not_cutover_ready"
            ),
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal"
        ) as session_factory:
            with self.assertRaisesRegex(
                TelegramDeliveryRuntimeConfigurationError,
                "queue_implementation_not_cutover_ready",
            ):
                await telegram_delivery_queue_loop()

        session_factory.assert_not_called()

    async def test_supervisor_refuses_missing_credential_registry_before_task_creation(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.asyncio.create_task"
        ) as create_task:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "telegram_delivery_credential_registry_not_installed",
            ):
                await telegram_delivery_queue_loop(
                    freshness_validators={"primary": AsyncMock()},
                    dispatch_limiter=_AllowLimiter(),
                    bot_identities=("primary",),
                )

        create_task.assert_not_called()

    async def test_lane_activation_retries_failed_or_mismatched_preflight(self):
        credentials = self._credentials()
        limiter = _AllowLimiter()
        lane = build_telegram_delivery_queue_lane_specs(
            freshness_validators={"primary": AsyncMock()},
            lifecycle_feedbacks={"primary": _NoopLifecycleFeedback()},
            gateway_calls=credentials.build_gateway_calls(),
            dispatch_limiter=limiter,
            bot_identities=("primary",),
        )[0]
        cases = (
            TelegramDeliveryPreflightFailedError("synthetic_preflight_failure"),
            self._preflight_report("primary", "channel_editor"),
        )
        for first_result in cases:
            preflight = AsyncMock(
                side_effect=(first_result, self._preflight_report("primary"))
            )
            lane_loop = AsyncMock(side_effect=asyncio.CancelledError)
            sleep = AsyncMock()
            with self.subTest(first_result=type(first_result).__name__), patch(
                "core.telegram_delivery_queue_worker."
                "run_configured_telegram_delivery_preflight",
                new=preflight,
            ), patch(
                "core.telegram_delivery_queue_worker."
                "rehydrate_telegram_delivery_limiter_state",
                new=AsyncMock(return_value=self._rehydration()),
            ), patch(
                "core.telegram_delivery_queue_worker.telegram_delivery_queue_lane_loop",
                new=lane_loop,
            ), patch(
                "core.telegram_delivery_queue_worker.asyncio.sleep",
                new=sleep,
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await worker._telegram_delivery_deferred_lane_activation_loop(
                        lane,
                        credential_registry=credentials,
                        channel_destination_key=(
                            f"channel:{int(worker.settings.channel_id)}"
                        ),
                    )
            self.assertEqual(preflight.await_count, 2)
            sleep.assert_awaited_once()
            lane_loop.assert_awaited_once_with(lane)

    async def test_lane_activation_honors_preflight_429_retry_after(self):
        credentials = self._credentials()
        limiter = _RecordingCooldownLimiter()
        lane = build_telegram_delivery_queue_lane_specs(
            freshness_validators={"primary": AsyncMock()},
            lifecycle_feedbacks={"primary": _NoopLifecycleFeedback()},
            gateway_calls=credentials.build_gateway_calls(),
            dispatch_limiter=limiter,
            bot_identities=("primary",),
        )[0]
        rehydrate = AsyncMock(
            side_effect=(self._rehydration(), asyncio.CancelledError())
        )
        preflight = AsyncMock(
            side_effect=TelegramDeliveryPreflightRateLimitedError(
                "telegram_preflight_rate_limited:primary:getMe",
                retry_after_seconds=2.5,
            )
        )
        sampled_now = worker.utc_now()
        durable_gate = AsyncMock()
        with patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=rehydrate,
        ), patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=preflight,
        ), patch(
            "core.telegram_delivery_queue_worker.utc_now",
            return_value=sampled_now,
        ), patch(
            "core.telegram_delivery_queue_worker._retry_after_safety_seconds",
            return_value=0.1,
        ), patch(
            "core.telegram_delivery_queue_worker._persist_preflight_rate_limit_gate",
            new=durable_gate,
        ), patch(
            "core.telegram_delivery_queue_worker.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await worker._telegram_delivery_deferred_lane_activation_loop(
                    lane,
                    credential_registry=credentials,
                    channel_destination_key=(
                        f"channel:{int(worker.settings.channel_id)}"
                    ),
                )

        self.assertEqual(limiter.bot_cooldowns, [
            ("primary", sampled_now + worker.timedelta(seconds=2.6))
        ])
        durable_gate.assert_awaited_once_with(
            bot_identity="primary",
            retry_after_seconds=2.5,
        )
        sleep.assert_awaited_once_with(2.6)

    async def test_preflight_429_keeps_lane_deferred_when_cooldown_persistence_fails(self):
        credentials = self._credentials()
        lane = build_telegram_delivery_queue_lane_specs(
            freshness_validators={"primary": AsyncMock()},
            lifecycle_feedbacks={"primary": _NoopLifecycleFeedback()},
            gateway_calls=credentials.build_gateway_calls(),
            dispatch_limiter=_FailingCooldownLimiter(),
            bot_identities=("primary",),
        )[0]
        rehydrate = AsyncMock(
            side_effect=(self._rehydration(), asyncio.CancelledError())
        )
        preflight = AsyncMock(
            side_effect=TelegramDeliveryPreflightRateLimitedError(
                "telegram_preflight_rate_limited:primary:getMe",
                retry_after_seconds=2.5,
            )
        )
        durable_gate = AsyncMock()
        with patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=rehydrate,
        ), patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=preflight,
        ), patch(
            "core.telegram_delivery_queue_worker._retry_after_safety_seconds",
            return_value=0.1,
        ), patch(
            "core.telegram_delivery_queue_worker._persist_preflight_rate_limit_gate",
            new=durable_gate,
        ), patch(
            "core.telegram_delivery_queue_worker.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await worker._telegram_delivery_deferred_lane_activation_loop(
                    lane,
                    credential_registry=credentials,
                    channel_destination_key=(
                        f"channel:{int(worker.settings.channel_id)}"
                    ),
                )

        preflight.assert_awaited_once()
        durable_gate.assert_awaited_once()
        sleep.assert_awaited_once_with(2.6)

    async def test_editor_preflight_failure_does_not_interrupt_primary_lane(self):
        primary_started = asyncio.Event()
        editor_preflight_failed = asyncio.Event()

        async def lane_loop(lane):
            if lane.bot_identity == "primary":
                primary_started.set()
            await asyncio.Event().wait()

        async def preflight(**kwargs):
            role = kwargs["bot_identities"][0]
            if role == "channel_editor":
                editor_preflight_failed.set()
                raise TelegramDeliveryPreflightFailedError(
                    "synthetic_editor_preflight_failure"
                )
            return self._preflight_report(role)

        async def idle_loop():
            await asyncio.Event().wait()

        validator = AsyncMock()
        credentials = self._credentials(editor=True)
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_lane_loop",
            side_effect=lane_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_delivery_queue_recovery_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_trade_result_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_admin_broadcast_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_notification_outbox_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_market_notice_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_scheduled_operation_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.telegram_offer_queue_handoff_loop",
            side_effect=idle_loop,
        ), patch(
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            side_effect=preflight,
        ), patch(
            "core.telegram_delivery_queue_worker.rehydrate_telegram_delivery_limiter_state",
            new=AsyncMock(return_value=self._rehydration()),
        ):
            supervisor = asyncio.create_task(
                telegram_delivery_queue_loop(
                    freshness_validators={
                        "primary": validator,
                        "channel_editor": validator,
                    },
                    lifecycle_feedbacks={
                        "primary": _NoopLifecycleFeedback(),
                        "channel_editor": _NoopLifecycleFeedback(),
                    },
                    credential_registry=credentials,
                    dispatch_limiter=_AllowLimiter(),
                    bot_identities=("primary", "channel_editor"),
                )
            )
            await asyncio.wait_for(primary_started.wait(), timeout=1)
            await asyncio.wait_for(editor_preflight_failed.wait(), timeout=1)
            self.assertFalse(supervisor.done())
            supervisor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await supervisor


if __name__ == "__main__":
    unittest.main()
