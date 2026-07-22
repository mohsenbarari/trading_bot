from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts.run_three_site_sync_timing_observer import (
    CONFIG_SCHEMA,
    SyncObserverError,
    _snapshot,
    load_config,
    observe,
)
from tests.test_build_three_site_sync_timing_evidence import _inputs
from tests.test_three_site_staging_signed_inventory import _inventory


class ThreeSiteSyncTimingObserverTests(unittest.TestCase):
    def _config(self, root: Path):  # noqa: ANN202
        artifacts = root / "artifacts"
        artifacts.mkdir(mode=0o700)
        inventory = _inventory()
        inventory_path = root / "inventory.json"
        inventory_path.write_text(json.dumps(inventory, sort_keys=True) + "\n")
        inventory_path.chmod(0o600)
        identity = root / "identity"
        known_hosts = root / "known_hosts"
        identity.write_text("test-only-private-key\n")
        identity.chmod(0o600)
        known_hosts.write_text("test-only-known-host\n")
        known_hosts.chmod(0o600)
        hosts = {
            item["role"]: item["host_ip"]
            for item in inventory["roles"]
            if item["role"] != "witness"
        }
        config = {
            "schema": CONFIG_SCHEMA,
            "campaign_id": inventory["campaign_id"],
            "release_sha": inventory["release_sha"],
            "production_forbidden": True,
            "artifact_root": str(artifacts),
            "inventory": {
                "path": str(inventory_path),
                "sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
            },
            "ssh": {
                "binary": "/usr/bin/ssh",
                "identity_file": str(identity),
                "known_hosts_file": str(known_hosts),
                "connect_timeout_seconds": 5,
            },
            "sites": {
                site: {
                    "host": hosts[site],
                    "port": 22,
                    "user": "root",
                    "repo_root": "/srv/trading-bot-three-site/current",
                    "compose_file": "/srv/trading-bot-three-site/current/deploy/staging/docker-compose.three-site.yml",
                    "env_file": f"/srv/trading-bot-three-site/runtime/{site}.env",
                }
                for site in ("bot_fi", "webapp_fi", "webapp_ir")
            },
            "limits": {
                "observation_timeout_seconds": 10,
                "poll_interval_seconds": 0.25,
                "command_timeout_seconds": 30,
            },
        }
        config_path = root / "observer.json"
        config_path.write_text(json.dumps(config, sort_keys=True) + "\n")
        config_path.chmod(0o600)
        return config, config_path, artifacts

    def test_runtime_is_bound_to_provisioned_inventory_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            config, config_path, artifacts = self._config(Path(directory))
            output = artifacts / "timing.json"
            loaded = load_config(
                config_path,
                campaign_id=config["campaign_id"],
                release_sha=config["release_sha"],
                output=output,
            )
            self.assertEqual(loaded["sites"]["bot_fi"]["host"], "10.30.0.1")

            config["sites"]["bot_fi"]["host"] = "10.30.0.99"
            config_path.write_text(json.dumps(config, sort_keys=True) + "\n")
            config_path.chmod(0o600)
            with self.assertRaisesRegex(SyncObserverError, "signed staging inventory"):
                load_config(
                    config_path,
                    campaign_id=config["campaign_id"],
                    release_sha=config["release_sha"],
                    output=output,
                )

    def test_observer_builds_timing_only_from_three_independent_snapshots(self):
        scenario_id = "three_site_sync_timing_steady_state"
        expected, manifest, snapshots = _inputs(scenario_id)
        observed_at = datetime.now(timezone.utc).isoformat()
        for snapshot in snapshots.values():
            snapshot["clock"]["observed_at"] = observed_at
        config = {
            "release_sha": "a" * 40,
            "limits": {
                "observation_timeout_seconds": 10,
                "poll_interval_seconds": 0.25,
                "command_timeout_seconds": 30,
            },
        }
        with patch(
            "scripts.run_three_site_sync_timing_observer._clock",
            side_effect=lambda _config, site: snapshots[site]["clock"],
        ):
            with patch(
                "scripts.run_three_site_sync_timing_observer._snapshot",
                side_effect=lambda _config, site, **_kwargs: snapshots[site],
            ):
                result = observe(
                    config=config,
                    manifest=manifest,
                    scenario_id=scenario_id,
                )
        self.assertEqual(result["summary"], expected["summary"])
        self.assertIn("bot_fi_to_webapp_fi", result["summary"]["physical_hops"])
        self.assertIn("webapp_fi_to_webapp_ir", result["summary"]["physical_hops"])

    def test_remote_snapshot_uses_only_dedicated_observer_service(self):
        config = {
            "release_sha": "a" * 40,
            "ssh": {
                "binary": "/usr/bin/ssh",
                "identity_file": "/secure/id",
                "known_hosts_file": "/secure/known",
                "connect_timeout_seconds": 5,
            },
            "sites": {
                "bot_fi": {
                    "host": "10.30.0.1", "port": 22, "user": "root",
                    "repo_root": "/srv/trading-bot-three-site/current",
                    "compose_file": "/srv/trading-bot-three-site/current/deploy/staging/docker-compose.three-site.yml",
                    "env_file": "/srv/trading-bot-three-site/runtime/bot-fi.env",
                }
            },
            "limits": {"command_timeout_seconds": 30},
        }
        clock = {
            "schema": "three-site-staging-host-clock-v1",
            "site": "bot_fi",
            "release_sha": "a" * 40,
            "synchronized": True,
            "offset_ms": 0.1,
            "observed_at": "2026-07-21T12:00:00+00:00",
            "measurement_source": "chronyc_tracking_csv",
            "measurement_raw_sha256": "b" * 64,
            "measurement_raw": "raw",
        }
        with patch(
            "scripts.run_three_site_sync_timing_observer._run_json",
            return_value={"site": "bot_fi"},
        ) as run:
            _snapshot(config, "bot_fi", correlation_prefix="matrix:timing:test:", clock=clock)
        command = run.call_args.args[0]
        self.assertIn("bot_fi_sync_observer", command)
        self.assertNotIn("bot_fi_migration", command)
        self.assertNotIn("bot_fi_delivery", " ".join(command))

    def test_manifest_correlation_is_rejected_before_any_ssh_call(self):
        scenario_id = "three_site_sync_timing_steady_state"
        _expected, manifest, _snapshots = _inputs(scenario_id)
        manifest["correlation_prefix"] = "unsafe;touch:/tmp/x"
        config = {
            "release_sha": "a" * 40,
            "limits": {
                "observation_timeout_seconds": 10,
                "poll_interval_seconds": 0.25,
                "command_timeout_seconds": 30,
            },
        }
        with patch("scripts.run_three_site_sync_timing_observer._clock") as clock:
            with self.assertRaisesRegex(SyncObserverError, "identity/scope"):
                observe(config=config, manifest=manifest, scenario_id=scenario_id)
        clock.assert_not_called()

    def test_recovery_backlog_series_starts_at_first_positive_sample(self):
        scenario_id = "reconnect_flap_and_bounded_catchup"
        _expected, manifest, final_snapshots = _inputs(scenario_id)
        observed_at = datetime.now(timezone.utc).isoformat()
        for snapshot in final_snapshots.values():
            snapshot["clock"]["observed_at"] = observed_at
        pre_backlog = copy.deepcopy(final_snapshots)
        positive = copy.deepcopy(final_snapshots)
        for snapshot in pre_backlog.values():
            snapshot["backlog"] = {"pending_events": 0, "oldest_pending_at": None}
        for snapshot in positive.values():
            snapshot["backlog"] = {"pending_events": 0, "oldest_pending_at": None}
        positive["bot_fi"]["backlog"] = {
            "pending_events": 1,
            "oldest_pending_at": observed_at,
        }
        added_event_id = final_snapshots["webapp_ir"]["events"][-1]["event_id"]
        for snapshot in positive.values():
            snapshot["events"] = [
                item for item in snapshot["events"] if item["event_id"] != added_event_id
            ]

        site_calls = {site: 0 for site in final_snapshots}
        lock = threading.Lock()

        def snapshot(_config, site, **_kwargs):  # noqa: ANN001, ANN202
            with lock:
                call = site_calls[site]
                site_calls[site] += 1
            if call == 0:
                return pre_backlog[site]
            if call == 1:
                return positive[site]
            return final_snapshots[site]

        config = {
            "release_sha": "a" * 40,
            "limits": {
                "observation_timeout_seconds": 10,
                "poll_interval_seconds": 0.25,
                "command_timeout_seconds": 30,
            },
        }
        with patch(
            "scripts.run_three_site_sync_timing_observer._clock",
            side_effect=lambda _config, site: final_snapshots[site]["clock"],
        ):
            with patch(
                "scripts.run_three_site_sync_timing_observer._snapshot",
                side_effect=snapshot,
            ):
                result = observe(
                    config=config,
                    manifest=manifest,
                    scenario_id=scenario_id,
                )
        self.assertGreaterEqual(result["backlog"]["live_ingress_events"], 1)
        self.assertGreater(result["backlog"]["samples"][0]["pending_events"], 0)
        self.assertEqual(result["backlog"]["samples"][-1]["pending_events"], 0)


if __name__ == "__main__":
    unittest.main()
