import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("market_watchdog", Path(__file__).parents[1] / "scripts/market_pipeline_watchdog.py")
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


def health(role, now=1000):
    from datetime import datetime, timezone
    return {"role": role, "schema": watchdog.SCHEMAS[role],
            "updated_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat()}


class WatchdogTests(unittest.TestCase):
    def test_fresh_heartbeat_cannot_mask_failed_tls_http(self):
        self.assertEqual(watchdog.assess("market-fact-receiver", health("market-fact-receiver"), {}, 1000, endpoint_ok=False), "MTLS_HTTP_UNRESPONSIVE")

    def test_quiet_source_and_catchup_do_not_trigger_restart(self):
        for role in watchdog.ROLES["web"]:
            document = {**health(role), "status": "live-degraded", "queue_depth": 150000}
            self.assertIsNone(watchdog.assess(role, document, {}, 1000))

    def test_stalled_adapter_requires_outstanding_upstream_data(self):
        document = {**health("market-store-adapter"), "streams": {"g1": 1}}
        previous = {"streams": {"g1": 1}, "last_progress_at": 700}
        self.assertIsNone(watchdog.assess("market-store-adapter", document, previous, 1000, upstream={"streams": {"g1": 1}}))
        self.assertEqual(watchdog.assess("market-store-adapter", document, previous, 1000, upstream={"streams": {"g1": 2}}), "ADAPTER_PROGRESS_STALLED")

    def test_sender_requires_newer_snapshot_before_recovery(self):
        document = {**health("estimator-snapshot-sender"), "latest": {"snapshot_version": 10}}
        previous = {"snapshot_version": 10, "last_progress_at": 700}
        self.assertIsNone(watchdog.assess("estimator-snapshot-sender", document, previous, 1000, upstream={"latest": {"snapshot_version": 10}}))
        self.assertEqual(watchdog.assess("estimator-snapshot-sender", document, previous, 1000, upstream={"latest": {"snapshot_version": 11}}), "SNAPSHOT_PROGRESS_STALLED")

    def test_stale_heartbeat_is_a_liveness_failure(self):
        self.assertEqual(watchdog.assess("market-processor", health("market-processor", 700), {}, 1000), "HEARTBEAT_STALLED")
