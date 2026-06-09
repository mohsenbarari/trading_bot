import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.sync import get_sync_health


class FakeSummaryResult:
    def __init__(self, value):
        self._value = value

    def one(self):
        return self._value


class FakeTableRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, *results):
        self._results = list(results)

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("Unexpected execute call")
        return self._results.pop(0)


class SyncHealthEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_health_requires_observability_key(self):
        request = SimpleNamespace(
            headers={},
            url=SimpleNamespace(path="/api/sync/health"),
            client=SimpleNamespace(host="198.51.100.10"),
        )
        with patch("api.routers.sync.settings.observability_api_key", "obs-key"):
            with self.assertRaises(HTTPException) as exc_info:
                await get_sync_health(request=request, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 403)

    async def test_sync_health_rejects_when_observability_key_is_unconfigured(self):
        request = SimpleNamespace(
            headers={"X-Observability-Api-Key": "obs-key"},
            url=SimpleNamespace(path="/api/sync/health"),
            client=SimpleNamespace(host="198.51.100.10"),
        )
        with patch("api.routers.sync.settings.observability_api_key", None):
            with self.assertRaises(HTTPException) as exc_info:
                await get_sync_health(request=request, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 503)

    async def test_sync_health_allows_loopback_without_observability_key(self):
        db = FakeDB(FakeSummaryResult((0, None)), FakeTableRows([]))
        redis_client = SimpleNamespace(llen=AsyncMock(side_effect=[0, 0]))
        request = SimpleNamespace(
            headers={},
            url=SimpleNamespace(path="/api/sync/health", hostname="public.example"),
            client=SimpleNamespace(host="127.0.0.1"),
        )

        with patch("api.routers.sync.settings.observability_api_key", None), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.default_peer_server_url", return_value=None), patch(
            "api.routers.sync.get_redis_client", return_value=redis_client
        ), patch("api.routers.sync.record_sync_health"):
            payload = await get_sync_health(request=request, db=db)

        self.assertEqual(payload["status"], "ok")

    async def test_sync_health_does_not_trust_host_header_for_loopback_bypass(self):
        request = SimpleNamespace(
            headers={},
            url=SimpleNamespace(path="/api/sync/health", hostname="127.0.0.1"),
            client=SimpleNamespace(host="198.51.100.10"),
        )
        with patch("api.routers.sync.settings.observability_api_key", "obs-key"):
            with self.assertRaises(HTTPException) as exc_info:
                await get_sync_health(request=request, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 403)

    async def test_sync_health_reports_backlog_and_queue_state(self):
        oldest = datetime(2026, 6, 9, 10, 0, 0, tzinfo=timezone.utc)
        db = FakeDB(
            FakeSummaryResult((3, oldest)),
            FakeTableRows([("offers", 2), ("trades", 1)]),
        )
        redis_client = SimpleNamespace(llen=AsyncMock(side_effect=[4, 1]))
        request = SimpleNamespace(
            headers={"X-Observability-Api-Key": "obs-key"},
            url=SimpleNamespace(path="/api/sync/health"),
            client=SimpleNamespace(host="198.51.100.10"),
        )

        with patch("api.routers.sync.settings.observability_api_key", "obs-key"), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.default_peer_server_url", return_value="https://iran.example"), patch(
            "api.routers.sync.get_redis_client", return_value=redis_client
        ), patch("api.routers.sync.record_sync_health") as record_sync_health:
            payload = await get_sync_health(request=request, db=db)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["server_mode"], "foreign")
        self.assertEqual(payload["unsynced_change_log_count"], 3)
        self.assertEqual(payload["redis_queues"], {"sync:outbound": 4, "sync:retry": 1})
        self.assertEqual(payload["unsynced_by_table"], {"offers": 2, "trades": 1})
        record_sync_health.assert_called_once()


if __name__ == "__main__":
    unittest.main()
