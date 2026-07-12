import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.sync import get_sync_health, get_sync_parity_snapshot, record_sync_parity_status


PUBLICATION_SUMMARY = {
    "status": "ok",
    "state_counts": {},
    "finding_counts": {},
}


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
        redis_client = SimpleNamespace(llen=AsyncMock(side_effect=[0, 0]), get=AsyncMock(return_value=None))
        request = SimpleNamespace(
            headers={},
            url=SimpleNamespace(path="/api/sync/health", hostname="public.example"),
            client=SimpleNamespace(host="127.0.0.1"),
        )

        with patch("api.routers.sync.settings.observability_api_key", None), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.default_peer_server_url", return_value=None), patch(
            "api.routers.sync.get_redis_client", return_value=redis_client
        ), patch("api.routers.sync.record_sync_health"), patch(
            "api.routers.sync.record_offer_publication_health"
        ), patch(
            "api.routers.sync.publication_observability_summary",
            new=AsyncMock(return_value=PUBLICATION_SUMMARY),
        ):
            payload = await get_sync_health(request=request, db=db)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["publication_reconciliation"], PUBLICATION_SUMMARY)
        self.assertEqual(payload["parity_status"]["status"], "available")
        self.assertEqual(payload["parity_status"]["comparison_status"], "missing")
        self.assertFalse(payload["parity_status"]["fresh"])

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
        redis_client = SimpleNamespace(
            llen=AsyncMock(side_effect=[4, 1]),
            get=AsyncMock(return_value='{"enabled": true, "outage_class": "long"}'),
        )
        request = SimpleNamespace(
            headers={"X-Observability-Api-Key": "obs-key"},
            url=SimpleNamespace(path="/api/sync/health"),
            client=SimpleNamespace(host="198.51.100.10"),
        )

        with patch("api.routers.sync.settings.observability_api_key", "obs-key"), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.default_peer_server_url", return_value="https://iran.example"), patch(
            "api.routers.sync.get_redis_client", return_value=redis_client
        ), patch("api.routers.sync.record_sync_health") as record_sync_health, patch(
            "api.routers.sync.record_offer_publication_health"
        ) as record_publication_health, patch(
            "api.routers.sync.publication_observability_summary",
            new=AsyncMock(return_value={
                "status": "action_required",
                "state_counts": {"telegram_channel": {"failed": 1}},
                "finding_counts": {"failed_telegram_publication": 1},
            }),
        ):
            payload = await get_sync_health(request=request, db=db)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["server_mode"], "foreign")
        self.assertEqual(payload["unsynced_change_log_count"], 3)
        self.assertEqual(payload["redis_queues"], {"sync:outbound": 4, "sync:retry": 1})
        self.assertEqual(payload["unsynced_by_table"], {"offers": 2, "trades": 1})
        self.assertEqual(payload["active_publication_gate"]["enabled"], True)
        self.assertEqual(payload["active_publication_gate"]["outage_class"], "long")
        self.assertEqual(payload["publication_reconciliation"]["status"], "action_required")
        self.assertEqual(payload["parity_status"]["snapshot_endpoint"], "/api/sync/parity/snapshot")
        self.assertEqual(payload["parity_status"]["comparison_status"], "missing")
        self.assertEqual(
            payload["registration_jobs"]["thresholds"],
            {
                "heartbeat_max_age_seconds": 60,
                "registration_pending_max_age_seconds": 300,
                "otp_fallback_max_lag_seconds": 2,
            },
        )
        self.assertEqual(
            set(payload["registration_jobs"]["jobs"]),
            {"telegram_registration_reconciliation", "otp_sms_fallback"},
        )
        record_sync_health.assert_called_once()
        record_publication_health.assert_called_once()

    async def test_sync_health_reports_fresh_stored_parity_status(self):
        db = FakeDB(FakeSummaryResult((0, None)), FakeTableRows([]))
        parity_summary = {
            "status": "ok",
            "fresh": True,
            "mode": "deep",
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "business_drift_count": 0,
            "critical_drift_count": 0,
            "incomplete_count": 0,
            "duplicate_identity_count": 0,
            "truncated_table_count": 0,
        }
        redis_client = SimpleNamespace(
            llen=AsyncMock(side_effect=[0, 0]),
            get=AsyncMock(side_effect=[json.dumps(parity_summary), '{"enabled": false}']),
        )
        request = SimpleNamespace(
            headers={"X-Observability-Api-Key": "obs-key"},
            url=SimpleNamespace(path="/api/sync/health"),
            client=SimpleNamespace(host="198.51.100.10"),
        )

        with patch("api.routers.sync.settings.observability_api_key", "obs-key"), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.settings.sync_parity_status_max_age_seconds", 3600), patch(
            "api.routers.sync.default_peer_server_url", return_value="https://iran.example"
        ), patch("api.routers.sync.get_redis_client", return_value=redis_client), patch(
            "api.routers.sync.record_sync_health"
        ), patch("api.routers.sync.record_offer_publication_health"), patch(
            "api.routers.sync.record_sync_parity_summary"
        ) as record_parity_summary, patch(
            "api.routers.sync.publication_observability_summary",
            new=AsyncMock(return_value=PUBLICATION_SUMMARY),
        ):
            payload = await get_sync_health(request=request, db=db)

        self.assertEqual(payload["parity_status"]["comparison_status"], "ok")
        self.assertTrue(payload["parity_status"]["fresh"])
        self.assertEqual(payload["parity_status"]["latest_comparison"]["mode"], "deep")
        record_parity_summary.assert_called_once()

    async def test_record_sync_parity_status_stores_summary_in_redis(self):
        stored = {}

        async def fake_set(key, value, ex=None):
            stored["key"] = key
            stored["value"] = value
            stored["ex"] = ex
            return True

        redis_client = SimpleNamespace(set=fake_set)
        request = SimpleNamespace(
            headers={"X-Observability-Api-Key": "obs-key"},
            url=SimpleNamespace(path="/api/sync/parity/status"),
            client=SimpleNamespace(host="198.51.100.10"),
        )
        comparison = {
            "status": "business_drift",
            "mode": "deep",
            "compared_at": "2026-06-28T05:00:00Z",
            "severity_counts": {"business_drift": 2, "critical_drift": 0, "incomplete": 0},
            "tables": {},
            "artifact_metadata": {
                "local_server_mode": "foreign",
                "peer_server_mode": "iran",
                "local_release_sha": "local-sha",
                "peer_release_sha": "peer-sha",
                "snapshot_mode": "deep",
                "local_table_count": 20,
                "peer_table_count": 20,
                "local_snapshot_at": "2026-06-28T04:58:00Z",
                "peer_snapshot_at": "2026-06-28T04:58:02Z",
                "comparison_artifact_hash": "sha256:comparison",
                "artifact_reference": "tmp/parity/comparison.json",
            },
        }

        with patch("api.routers.sync.settings.observability_api_key", "obs-key"), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.settings.sync_parity_status_max_age_seconds", 900), patch(
            "api.routers.sync.get_redis_client", return_value=redis_client
        ), patch("api.routers.sync.record_sync_parity_summary") as record_parity_summary:
            payload = await record_sync_parity_status(request=request, comparison=comparison)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["parity_status"]["business_drift_count"], 2)
        self.assertTrue(payload["parity_status"]["artifact_metadata_complete"])
        self.assertEqual(
            payload["parity_status"]["artifact_metadata"]["comparison_artifact_hash"],
            "sha256:comparison",
        )
        stored_summary = json.loads(stored["value"])
        self.assertTrue(stored_summary["artifact_metadata_complete"])
        self.assertEqual(stored["key"], "sync:parity:latest_comparison")
        self.assertGreaterEqual(stored["ex"], 3600)
        record_parity_summary.assert_called_once()

    async def test_parity_snapshot_requires_observability_key_and_redacted_snapshot_builder(self):
        request = SimpleNamespace(
            headers={"X-Observability-Api-Key": "obs-key"},
            url=SimpleNamespace(path="/api/sync/parity/snapshot"),
            client=SimpleNamespace(host="198.51.100.10"),
        )
        expected = {"status": "ok", "mode": "quick", "tables": {}}

        with patch("api.routers.sync.settings.observability_api_key", "obs-key"), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch(
            "api.routers.sync.build_database_parity_snapshot",
            new=AsyncMock(return_value=dict(expected)),
        ) as builder:
            payload = await get_sync_parity_snapshot(
                request=request,
                mode="quick",
                max_rows_per_table=25,
                db=FakeDB(),
            )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["server_mode"], "foreign")
        builder.assert_awaited_once()
        self.assertEqual(builder.await_args.kwargs["mode"], "quick")
        self.assertEqual(builder.await_args.kwargs["max_rows_per_table"], 25)

    async def test_parity_snapshot_rejects_invalid_mode_and_limit(self):
        request = SimpleNamespace(
            headers={"X-Observability-Api-Key": "obs-key"},
            url=SimpleNamespace(path="/api/sync/parity/snapshot"),
            client=SimpleNamespace(host="198.51.100.10"),
        )

        with patch("api.routers.sync.settings.observability_api_key", "obs-key"):
            with self.assertRaises(HTTPException) as exc_info:
                await get_sync_parity_snapshot(request=request, mode="full", db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        with patch("api.routers.sync.settings.observability_api_key", "obs-key"):
            with self.assertRaises(HTTPException) as exc_info:
                await get_sync_parity_snapshot(request=request, max_rows_per_table=0, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
