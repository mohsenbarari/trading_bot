import asyncio
import unittest
from unittest.mock import AsyncMock, patch

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


class TelegramDeliveryQueueWorkerSafetyTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _queue_runtime():
        return TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
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
        specs = build_telegram_delivery_queue_lane_specs(
            freshness_validators={
                "primary": validator,
                "channel_editor": validator,
            },
            gateway_calls={
                "primary": primary_gateway,
                "channel_editor": editor_gateway,
            },
            bot_identities=("primary", "channel_editor"),
        )
        self.assertEqual(
            tuple(spec.bot_identity for spec in specs),
            ("primary", "channel_editor"),
        )
        self.assertIs(specs[0].gateway_call, primary_gateway)
        self.assertIs(specs[1].gateway_call, editor_gateway)

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
                bot_identities=("primary", "channel_editor"),
            )

        with self.assertRaisesRegex(
            TelegramDeliveryQueueImplementationIncompleteError,
            "telegram_delivery_lane_set_invalid",
        ):
            build_telegram_delivery_queue_lane_specs(
                freshness_validators={},
                gateway_calls={},
                bot_identities=(),
            )

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
        ) as create_task:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "authoritative_freshness_validator_not_installed:primary",
            ):
                await telegram_delivery_queue_loop(bot_identities=("primary",))

        create_task.assert_not_called()

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
        ):
            supervisor = asyncio.create_task(
                telegram_delivery_queue_loop(
                    freshness_validators={
                        "primary": validator,
                        "channel_editor": validator,
                    },
                    gateway_calls={
                        "primary": AsyncMock(),
                        "channel_editor": AsyncMock(),
                    },
                    bot_identities=("primary", "channel_editor"),
                )
            )
            await asyncio.wait_for(all_started.wait(), timeout=1)
            self.assertEqual(started_lanes, {"primary", "channel_editor"})
            self.assertTrue(recovery_started.is_set())
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


if __name__ == "__main__":
    unittest.main()
