"""Characterization unit tests for main.get_metrics handler.

Provenance:
- Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
- Seed: API-OPS-GET-MAIN-GET-METRICS-L540
- Target: main.get_metrics

Freezes authorization-denial audit cardinality, denial short-circuit boundaries,
and authorized gauge/Redis-hydration/render dependency sequence.
This unit neither promotes inventory evidence nor closes P1-00b.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    _FENCE,
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import main


def _make_request(host: str | None, key: str | None = None) -> SimpleNamespace:
    headers: dict[str, str] = {}
    if key is not None:
        headers[main.OBSERVABILITY_API_KEY_HEADER] = key
    return SimpleNamespace(
        client=SimpleNamespace(host=host) if host is not None else None,
        headers=headers,
    )


class MainGetMetricsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._initial_denied = len(_FENCE["denied_connects"])
        self._initial_constructs = len(_FENCE["live_redis_constructs"])

    def assert_network_fence_unchanged(self) -> None:
        self.assertEqual(len(_FENCE["denied_connects"]), self._initial_denied)
        self.assertEqual(
            len(_FENCE["live_redis_constructs"]), self._initial_constructs
        )

    async def test_wrong_supplied_observability_key_denied_with_audit_log_and_short_circuits(
        self,
    ) -> None:
        configured_key = "expected-obs-key-scenario1-alpha"
        supplied_key = "wrong-obs-key-scenario1-beta"
        request = _make_request("203.0.113.10", key=supplied_key)

        with patch.object(
            main.settings, "observability_api_key", configured_key
        ), patch.object(main, "audit_log") as mock_audit_log, patch.object(
            main, "uptime_seconds"
        ) as mock_uptime_seconds, patch.object(
            main.registry, "gauge"
        ) as mock_gauge, patch.object(
            main, "get_redis_client"
        ) as mock_get_redis_client, patch.object(
            main, "refresh_registration_job_metrics"
        ) as mock_refresh_metrics, patch.object(
            main.asyncio, "wait_for"
        ) as mock_wait_for, patch.object(
            main, "metrics_response_body"
        ) as mock_metrics_response_body:

            with self.assertRaises(HTTPException) as exc_info:
                await main.get_metrics(request)

            self.assertEqual(exc_info.exception.status_code, 404)
            self.assertEqual(exc_info.exception.detail, "Not found")

            mock_audit_log.assert_called_once_with(
                "observability.metrics_access",
                target_type="metrics",
                result="denied",
                reason="invalid_observability_api_key",
                extra={"path": "/metrics", "status_code": 404},
            )
            mock_uptime_seconds.assert_not_called()
            mock_gauge.assert_not_called()
            mock_get_redis_client.assert_not_called()
            mock_refresh_metrics.assert_not_called()
            mock_wait_for.assert_not_called()
            mock_metrics_response_body.assert_not_called()
            self.assert_network_fence_unchanged()

    async def test_missing_observability_key_denied_without_audit_log_and_short_circuits(
        self,
    ) -> None:
        configured_key = "expected-obs-key-scenario2-alpha"
        request = _make_request("203.0.113.10", key=None)

        with patch.object(
            main.settings, "observability_api_key", configured_key
        ), patch.object(main, "audit_log") as mock_audit_log, patch.object(
            main, "uptime_seconds"
        ) as mock_uptime_seconds, patch.object(
            main.registry, "gauge"
        ) as mock_gauge, patch.object(
            main, "get_redis_client"
        ) as mock_get_redis_client, patch.object(
            main, "refresh_registration_job_metrics"
        ) as mock_refresh_metrics, patch.object(
            main.asyncio, "wait_for"
        ) as mock_wait_for, patch.object(
            main, "metrics_response_body"
        ) as mock_metrics_response_body:

            with self.assertRaises(HTTPException) as exc_info:
                await main.get_metrics(request)

            self.assertEqual(exc_info.exception.status_code, 404)
            self.assertEqual(exc_info.exception.detail, "Not found")

            mock_audit_log.assert_not_called()
            mock_uptime_seconds.assert_not_called()
            mock_gauge.assert_not_called()
            mock_get_redis_client.assert_not_called()
            mock_refresh_metrics.assert_not_called()
            mock_wait_for.assert_not_called()
            mock_metrics_response_body.assert_not_called()
            self.assert_network_fence_unchanged()

    async def test_authorized_metrics_dependency_sequence_and_rendering(
        self,
    ) -> None:
        configured_key = "authorized-obs-key-scenario3-gamma"
        request = _make_request("203.0.113.10", key=configured_key)

        distinctive_uptime = 54321.125
        redis_sentinel = object()
        distinctive_payload = (
            "# HELP test_uptime_seconds Process uptime\n"
            "# TYPE test_uptime_seconds gauge\n"
            "test_uptime_seconds 54321.125\n"
        )

        event_trace: list[tuple[Any, ...]] = []
        uptime_calls: list[float] = []
        gauge_calls: list[tuple[str, str, float, dict[str, Any]]] = []
        get_redis_calls: int = 0
        refresh_awaits: list[Any] = []
        wait_for_calls: list[tuple[Any, Any]] = []
        metrics_body_calls: int = 0

        def fake_uptime_seconds(started_at: float) -> float:
            uptime_calls.append(started_at)
            event_trace.append(("uptime_seconds", started_at))
            return distinctive_uptime

        def fake_gauge(
            name: str, help_text: str, value: float, **labels: Any
        ) -> None:
            gauge_calls.append((name, help_text, value, labels))
            event_trace.append(
                ("registry.gauge", name, help_text, value, labels)
            )

        def fake_get_redis_client() -> Any:
            nonlocal get_redis_calls
            get_redis_calls += 1
            event_trace.append(("get_redis_client",))
            return redis_sentinel

        async def fake_refresh_registration_job_metrics(client: Any) -> None:
            refresh_awaits.append(client)
            event_trace.append(
                ("refresh_registration_job_metrics_await", client)
            )

        async def fake_wait_for(fut: Any, timeout: float | None = None) -> Any:
            wait_for_calls.append((fut, timeout))
            event_trace.append(("wait_for_enter", timeout))
            try:
                return await fut
            finally:
                event_trace.append(("wait_for_exit", timeout))

        def fake_metrics_response_body() -> str:
            nonlocal metrics_body_calls
            metrics_body_calls += 1
            event_trace.append(("metrics_response_body",))
            return distinctive_payload

        with patch.object(
            main.settings, "observability_api_key", configured_key
        ), patch.object(main, "audit_log") as mock_audit_log, patch.object(
            main, "uptime_seconds", side_effect=fake_uptime_seconds
        ), patch.object(
            main.registry, "gauge", side_effect=fake_gauge
        ), patch.object(
            main, "get_redis_client", side_effect=fake_get_redis_client
        ), patch.object(
            main,
            "refresh_registration_job_metrics",
            side_effect=fake_refresh_registration_job_metrics,
        ) as mock_refresh_metrics, patch.object(
            main.asyncio, "wait_for", side_effect=fake_wait_for
        ) as mock_wait_for, patch.object(
            main, "metrics_response_body", side_effect=fake_metrics_response_body
        ):

            response = await main.get_metrics(request)

        mock_audit_log.assert_not_called()

        self.assertEqual(len(uptime_calls), 1)
        self.assertIs(uptime_calls[0], main._PROCESS_STARTED_AT)

        self.assertEqual(
            gauge_calls,
            [
                (
                    "trading_bot_process_uptime_seconds",
                    "Process uptime in seconds.",
                    distinctive_uptime,
                    {},
                )
            ],
        )

        self.assertEqual(get_redis_calls, 1)

        mock_refresh_metrics.assert_called_once_with(redis_sentinel)
        mock_refresh_metrics.assert_awaited_once_with(redis_sentinel)
        self.assertEqual(len(refresh_awaits), 1)
        self.assertIs(refresh_awaits[0], redis_sentinel)

        mock_wait_for.assert_called_once()
        mock_wait_for.assert_awaited_once()
        self.assertEqual(len(wait_for_calls), 1)
        self.assertEqual(wait_for_calls[0][1], 0.25)

        self.assertEqual(metrics_body_calls, 1)

        expected_event_trace = [
            ("uptime_seconds", main._PROCESS_STARTED_AT),
            (
                "registry.gauge",
                "trading_bot_process_uptime_seconds",
                "Process uptime in seconds.",
                distinctive_uptime,
                {},
            ),
            ("get_redis_client",),
            ("wait_for_enter", 0.25),
            ("refresh_registration_job_metrics_await", redis_sentinel),
            ("wait_for_exit", 0.25),
            ("metrics_response_body",),
        ]
        self.assertEqual(event_trace, expected_event_trace)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.media_type, "text/plain; version=0.0.4; charset=utf-8"
        )
        self.assertEqual(response.body, distinctive_payload.encode("utf-8"))

        self.assert_network_fence_unchanged()


if __name__ == "__main__":
    unittest.main()
