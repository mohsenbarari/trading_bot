import importlib.util
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("market_watchdog", Path(__file__).parents[1] / "scripts/market_pipeline_watchdog.py")
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


def health(role, now=1000):
    from datetime import datetime, timezone
    return {"role": role, "schema": watchdog.SCHEMAS[role],
            "updated_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat()}


class WatchdogTests(unittest.TestCase):
    def test_fresh_old_release_heartbeat_cannot_mask_new_runtime_startup(self):
        document = {**health("market-store-adapter"), "release_sha": "old"}
        self.assertEqual(watchdog.assess("market-store-adapter", document, {}, 1000, expected_release="new"), "HEARTBEAT_RELEASE_MISMATCH")

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

    def test_other_source_progress_cannot_mask_one_stalled_adapter_stream(self):
        document = {**health("market-store-adapter"), "streams": {"g1": 1, "usd": 9}}
        previous = {"streams": {"g1": 1, "usd": 8}, "last_progress_at": 995,
                    "stream_progress_at": {"g1": 700, "usd": 995}}
        upstream = {"streams": {"g1": 2, "usd": 9}}
        self.assertEqual(watchdog.assess("market-store-adapter", document, previous, 1000, upstream=upstream), "ADAPTER_PROGRESS_STALLED")

    def test_stale_heartbeat_is_a_liveness_failure(self):
        self.assertEqual(watchdog.assess("market-processor", health("market-processor", 700), {}, 1000), "HEARTBEAT_STALLED")

    def test_recovery_requires_two_samples_and_preserves_restart_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ops = root / "ops"
            ops.mkdir()
            deployment = root / "deployment.lock"
            deployment.touch()
            roles = watchdog.ROLES["bot"]
            containers = []
            for role in roles:
                state = root / role / role
                state.mkdir(parents=True)
                (state / "health.json").write_text(json.dumps(health(role)))
                containers.append({
                    "Id": role,
                    "Config": {"Labels": {
                        "com.docker.compose.project": "test-pipeline",
                        "com.docker.compose.service": role,
                    }},
                    "State": {"Running": True, "StartedAt": "1970-01-01T00:00:00Z"},
                })
            args = SimpleNamespace(host_role="bot", project="test-pipeline",
                                   state_root=root, ops_root=ops,
                                   deployment_lock=deployment, apply=True)
            restarts = []

            def run(command, timeout=10):
                if command[1] == "inspect":
                    return SimpleNamespace(returncode=0, stdout=json.dumps(containers))
                if command[1] == "exec":
                    return SimpleNamespace(returncode=1, stdout="")
                if command[1] == "restart":
                    restarts.append(command[-1])
                    return SimpleNamespace(returncode=0, stdout="")
                self.fail("Unexpected watchdog command")

            with patch.object(watchdog.argparse.ArgumentParser, "parse_args", return_value=args), \
                    patch.object(watchdog.os, "geteuid", return_value=0), \
                    patch.object(watchdog.time, "time", return_value=1000), \
                    patch.object(watchdog, "run", side_effect=run), \
                    contextlib.redirect_stdout(io.StringIO()):
                watchdog.main()
                self.assertEqual(restarts, [])
                watchdog.main()
                self.assertEqual(restarts, ["market-fact-receiver"])
                report = json.loads((ops / "watchdog-health.json").read_text())
                self.assertEqual(report["roles"][0]["status"], "RESTARTED")
                watchdog.main()
                self.assertEqual(restarts, ["market-fact-receiver"])
                state = json.loads((ops / "watchdog-state.json").read_text())
                self.assertEqual(state["market-fact-receiver"]["last_restart_at"], 1000)

    def test_deployment_lock_prevents_any_runtime_inspection_or_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "deployment.lock"
            lock.touch()
            args = SimpleNamespace(host_role="bot", project="test-pipeline",
                                   state_root=root, ops_root=root,
                                   deployment_lock=lock, apply=True)
            with open(lock, "r") as descriptor:
                watchdog.fcntl.flock(descriptor, watchdog.fcntl.LOCK_EX)
                with patch.object(watchdog.argparse.ArgumentParser, "parse_args", return_value=args), \
                        patch.object(watchdog.os, "geteuid", return_value=0), \
                        patch.object(watchdog, "run") as run, \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    watchdog.main()
                    run.assert_not_called()
                    self.assertIn("DEPLOYMENT_IN_PROGRESS", output.getvalue())
