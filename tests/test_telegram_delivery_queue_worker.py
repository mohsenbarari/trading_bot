import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from core import telegram_delivery_queue_worker as worker
from core.telegram_delivery_credentials import TelegramDeliveryCredentialRegistry
from core.telegram_delivery_queue_limiter import TelegramDeliveryDispatchAdmission
from core.telegram_delivery_preflight import (
    TelegramDeliveryPreflightFailedError,
    TelegramDeliveryPreflightIdentityReport,
    TelegramDeliveryPreflightReport,
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


class TelegramDeliveryQueueWorkerSafetyTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _queue_runtime():
        return TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
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
        limiter = _AllowLimiter()
        specs = build_telegram_delivery_queue_lane_specs(
            freshness_validators={
                "primary": validator,
                "channel_editor": validator,
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
                gateway_calls={},
                dispatch_limiter=limiter,
                bot_identities=(),
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

    async def test_supervisor_starts_primary_editor_and_recovery_as_independent_tasks(self):
        started_lanes: set[str] = set()
        recovery_started = asyncio.Event()
        all_started = asyncio.Event()

        async def lane_loop(lane):
            started_lanes.add(lane.bot_identity)
            if len(started_lanes) == 2 and recovery_started.is_set():
                all_started.set()
            await asyncio.Event().wait()

        async def recovery_loop():
            recovery_started.set()
            if len(started_lanes) == 2:
                all_started.set()
            await asyncio.Event().wait()

        validator = AsyncMock()
        credentials = self._credentials(editor=True)
        preflight_report = self._preflight_report("primary", "channel_editor")
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
            "core.telegram_delivery_queue_worker.run_configured_telegram_delivery_preflight",
            new=AsyncMock(return_value=preflight_report),
        ) as preflight:
            supervisor = asyncio.create_task(
                telegram_delivery_queue_loop(
                    freshness_validators={
                        "primary": validator,
                        "channel_editor": validator,
                    },
                    credential_registry=credentials,
                    dispatch_limiter=_AllowLimiter(),
                    bot_identities=("primary", "channel_editor"),
                )
            )
            await asyncio.wait_for(all_started.wait(), timeout=1)
            self.assertEqual(started_lanes, {"primary", "channel_editor"})
            self.assertTrue(recovery_started.is_set())
            preflight.assert_awaited_once_with(
                settings=worker.settings,
                credential_registry=credentials,
            )
            supervisor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await supervisor

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

    async def test_supervisor_refuses_failed_or_mismatched_preflight_before_task_creation(self):
        validator = AsyncMock()
        credentials = self._credentials()
        cases = (
            (
                TelegramDeliveryPreflightFailedError("synthetic_preflight_failure"),
                TelegramDeliveryPreflightFailedError,
                "synthetic_preflight_failure",
            ),
            (
                self._preflight_report("primary", "channel_editor"),
                TelegramDeliveryQueueImplementationIncompleteError,
                "telegram_delivery_preflight_lane_mismatch",
            ),
        )
        for preflight_value, error_class, reason in cases:
            with self.subTest(reason=reason), patch(
                "core.telegram_delivery_queue_worker.assert_background_job_authority"
            ), patch(
                "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
                return_value=self._queue_runtime(),
            ), patch(
                "core.telegram_delivery_queue_worker.asyncio.create_task"
            ) as create_task, patch(
                "core.telegram_delivery_queue_worker.AsyncSessionLocal"
            ) as session_factory:
                preflight = AsyncMock(
                    side_effect=preflight_value
                    if isinstance(preflight_value, Exception)
                    else None,
                    return_value=None
                    if isinstance(preflight_value, Exception)
                    else preflight_value,
                )
                with patch(
                    "core.telegram_delivery_queue_worker."
                    "run_configured_telegram_delivery_preflight",
                    new=preflight,
                ), self.assertRaisesRegex(error_class, reason):
                    await telegram_delivery_queue_loop(
                        freshness_validators={"primary": validator},
                        credential_registry=credentials,
                        dispatch_limiter=_AllowLimiter(),
                        bot_identities=("primary",),
                    )
            create_task.assert_not_called()
            session_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
