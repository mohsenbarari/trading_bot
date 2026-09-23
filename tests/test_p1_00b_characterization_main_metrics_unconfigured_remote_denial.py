"""Characterization unit tests for main.get_metrics unconfigured-key remote denial.

Provenance:
- Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
- Seed: API-OPS-GET-MAIN-GET-METRICS-L540
- Target: main.get_metrics
- Scenario: unconfigured-key remote-denial scenario

Freezes unconfigured observability-key remote denial branch for main.get_metrics.
When settings.observability_api_key is None, remote requests are denied with 404,
auditing only when an invalid key header is supplied, and short-circuiting
metrics registry, Redis hydration, and rendering.
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
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import main


def _make_remote_request(host: str, key: str | None = None) -> SimpleNamespace:
    headers: dict[str, str] = {}
    if key is not None:
        headers[main.OBSERVABILITY_API_KEY_HEADER] = key
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=headers,
    )


class MainMetricsUnconfiguredRemoteDenialCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        super().setUp()
        self._initial_denied = len(_FENCE["denied_connects"])
        self._initial_constructs = len(_FENCE["live_redis_constructs"])

    def assert_network_fence_unchanged(self) -> None:
        self.assertEqual(len(_FENCE["denied_connects"]), self._initial_denied)
        self.assertEqual(
            len(_FENCE["live_redis_constructs"]), self._initial_constructs
        )

    async def test_nonempty_supplied_key_denied_when_unconfigured(self) -> None:
        remote_host = "198.51.100.42"
        supplied_key = "unconfigured-probe-key-alpha-9988"
        request = _make_remote_request(remote_host, key=supplied_key)

        with patch.object(
            main.settings, "observability_api_key", None
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

    async def test_missing_key_denied_when_unconfigured_without_audit(
        self,
    ) -> None:
        remote_host = "198.51.100.42"
        request = _make_remote_request(remote_host, key=None)

        with patch.object(
            main.settings, "observability_api_key", None
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


if __name__ == "__main__":
    unittest.main()
