import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live import capacity_fault_agent as agent


class FullMatrixCapacityFaultAgentTests(unittest.TestCase):
    def _args(self):
        return argparse.Namespace(
            campaign_id="fm-capacity-test",
            release_sha="a" * 40,
            operation_id="123e4567-e89b-42d3-a456-426614174000",
        )

    def test_arm_closes_marker_before_reserving_and_disarm_reopens_last(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dedicated-storage"
            storage = root / "campaign-one" / "webapp_fi"
            for name in ("postgres", "redis", "uploads", "audit"):
                (storage / name).mkdir(parents=True, exist_ok=True)
            env_file = Path(temporary) / "webapp-fi.env"
            env_file.write_text(
                "STAGING_DATA_ROOT=" + str(root) + "\n"
                "STAGING_STORAGE_NAMESPACE=campaign-one\n"
                "STAGING_RELEASE_SHA=" + "a" * 40 + "\n",
                encoding="utf-8",
            )
            os.chmod(env_file, 0o600)
            state_file = Path(temporary) / "state.json"
            observed_marker = []

            def reserve(_descriptor, _offset, _length):
                marker = storage / "capacity-guard" / "guard.json"
                observed_marker.append(json.loads(marker.read_text(encoding="utf-8")))

            with patch.object(agent, "ENV_FILE", env_file), patch.object(
                agent, "STATE_FILE", state_file
            ), patch.object(agent, "DATA_ROOT", root), patch.object(
                agent, "_verify_release"
            ), patch.object(agent, "MINIMUM_FREE_BYTES", 1_000_000_000), patch.object(
                agent, "_space", side_effect=[(10_000_000_000, 2_000_000_000), (10_000_000_000, 1_000_000_000), (10_000_000_000, 2_000_000_000)]
            ), patch.object(agent.os, "posix_fallocate", side_effect=reserve):
                armed = agent._arm(self._args())
                self.assertEqual(armed["status"], "armed")
                self.assertEqual(observed_marker[0]["state"], "preparing")
                self.assertTrue((storage / "capacity-guard" / "capacity-reserve.bin").is_file())
                cleared = agent._disarm(self._args())

            self.assertEqual(cleared["status"], "cleared")
            self.assertFalse((storage / "capacity-guard" / "guard.json").exists())
            self.assertFalse((storage / "capacity-guard" / "capacity-reserve.bin").exists())
            self.assertFalse(state_file.exists())

    def test_preparing_state_without_reserve_is_recoverable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dedicated-storage"
            storage = root / "campaign-one" / "webapp_fi"
            for name in ("postgres", "redis", "uploads", "audit", "capacity-guard"):
                (storage / name).mkdir(parents=True, exist_ok=True)
            env_file = Path(temporary) / "webapp-fi.env"
            env_file.write_text(
                "STAGING_DATA_ROOT=" + str(root) + "\n"
                "STAGING_STORAGE_NAMESPACE=campaign-one\n"
                "STAGING_RELEASE_SHA=" + "a" * 40 + "\n",
                encoding="utf-8",
            )
            os.chmod(env_file, 0o600)
            state_file = Path(temporary) / "state.json"
            state = {
                "schema": agent.STATE_SCHEMA,
                "campaign_id": "fm-capacity-test",
                "release_sha": "a" * 40,
                "operation_id": self._args().operation_id,
                "phase": "preparing",
                "hard_limit_bytes": 1000,
                "storage_total_bytes": 10_000,
                "reserve_name": agent.RESERVE_NAME,
            }
            with patch.object(agent, "ENV_FILE", env_file), patch.object(
                agent, "STATE_FILE", state_file
            ), patch.object(agent, "DATA_ROOT", root), patch.object(
                agent, "_verify_release"
            ), patch.object(agent, "MINIMUM_FREE_BYTES", 1000):
                agent._write_atomic(state_file, state, mode=0o600)
                agent._write_atomic(
                    storage / "capacity-guard" / "guard.json",
                    agent._marker(state, available=9_000), mode=0o444,
                )
                with patch.object(agent, "_space", return_value=(10_000, 9_000)):
                    result = agent._disarm(self._args())
            self.assertEqual(result["status"], "cleared")
