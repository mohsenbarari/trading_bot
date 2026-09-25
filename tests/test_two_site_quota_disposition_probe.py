#!/usr/bin/env python3
"""Hermetic unit tests for strict quota disposition probe."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any
import unittest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_provider_broker import STATE_REL
from scripts.two_site_quota_disposition_probe import (
    QuotaProbeError,
    probe_cycle_quota_receipt,
)
from tests.test_two_site_quota_wait_sources import _setup_test_env


def _snapshot_tree(root: Path) -> dict[Path, tuple[int, int]]:
    return {
        p.relative_to(root): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def _setup_env(tmp_dir: str) -> dict[str, Any]:
    root, request, response, task_path, status_path, epoch, repo_facts, published_receipt = (
        _setup_test_env(tmp_dir)
    )
    state_root = root / STATE_REL
    reservation_path = list(root.rglob("broker-reservation.json"))[0]
    runs_dir = reservation_path.parent
    return {
        "root": root,
        "state_root": state_root,
        "runs_dir": runs_dir,
        "request": request,
        "response": response,
        "task_path": task_path,
        "status_path": status_path,
        "facts": repo_facts,
        "epoch": epoch,
        "receipt": published_receipt,
    }


class TestTwoSiteQuotaDispositionProbe(unittest.TestCase):
    def test_terminal_quota_positive_round_trip_no_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]

            snap_before = _snapshot_tree(root)
            status = probe_cycle_quota_receipt(root, cycle_id)
            self.assertEqual(status, "TERMINAL_QUOTA")

            snap_after = _snapshot_tree(root)
            self.assertEqual(snap_before, snap_after)

    def test_no_reservation_when_file_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            res_file = env["runs_dir"] / "broker-reservation.json"
            res_file.unlink()

            status = probe_cycle_quota_receipt(root, cycle_id)
            self.assertEqual(status, "NO_RESERVATION")

    def test_no_terminal_quota_when_receipt_removed_and_queues_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            request_id = env["request"]["request_id"]
            receipt_file = env["state_root"] / "terminal-quota" / f"{request_id}.json"
            receipt_file.unlink()

            for q_name in ("requests", "claimed", "responses"):
                q_files = list((env["state_root"] / q_name).iterdir())
                self.assertEqual(q_files, [])

            status = probe_cycle_quota_receipt(root, cycle_id)
            self.assertEqual(status, "NO_TERMINAL_QUOTA")

    def test_ambiguous_in_flight_in_requests_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            request_id = env["request"]["request_id"]
            receipt_file = env["state_root"] / "terminal-quota" / f"{request_id}.json"
            receipt_file.unlink()

            target_file = env["state_root"] / "requests" / f"{request_id}.json"
            target_file.write_bytes(b"{}")
            os.chmod(target_file, 0o600)

            with self.assertRaises(QuotaProbeError) as ctx:
                probe_cycle_quota_receipt(root, cycle_id)
            self.assertIn("ambiguous in-flight or unread Broker response", str(ctx.exception))

    def test_ambiguous_in_flight_in_claimed_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            request_id = env["request"]["request_id"]
            receipt_file = env["state_root"] / "terminal-quota" / f"{request_id}.json"
            receipt_file.unlink()

            target_file = env["state_root"] / "claimed" / f"{request_id}.json"
            target_file.write_bytes(b"{}")
            os.chmod(target_file, 0o600)

            with self.assertRaises(QuotaProbeError) as ctx:
                probe_cycle_quota_receipt(root, cycle_id)
            self.assertIn("ambiguous in-flight or unread Broker response", str(ctx.exception))

    def test_ambiguous_in_flight_in_responses_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            request_id = env["request"]["request_id"]
            receipt_file = env["state_root"] / "terminal-quota" / f"{request_id}.json"
            receipt_file.unlink()

            target_file = env["state_root"] / "responses" / f"{request_id}.json"
            target_file.write_bytes(b"{}")
            os.chmod(target_file, 0o600)

            with self.assertRaises(QuotaProbeError) as ctx:
                probe_cycle_quota_receipt(root, cycle_id)
            self.assertIn("ambiguous in-flight or unread Broker response", str(ctx.exception))

    def test_malformed_receipt_chmod_0644_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            request_id = env["request"]["request_id"]
            receipt_file = env["state_root"] / "terminal-quota" / f"{request_id}.json"
            os.chmod(receipt_file, 0o644)

            with self.assertRaises(QuotaProbeError) as ctx:
                probe_cycle_quota_receipt(root, cycle_id)
            self.assertNotEqual(str(ctx.exception), "NO_TERMINAL_QUOTA")

    def test_malformed_receipt_tampered_bytes_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            request_id = env["request"]["request_id"]
            receipt_file = env["state_root"] / "terminal-quota" / f"{request_id}.json"
            receipt_file.write_bytes(b'{"corrupted": true}')
            os.chmod(receipt_file, 0o600)

            with self.assertRaises(QuotaProbeError) as ctx:
                probe_cycle_quota_receipt(root, cycle_id)
            self.assertNotEqual(str(ctx.exception), "NO_TERMINAL_QUOTA")

    def test_reservation_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            res_file = env["runs_dir"] / "broker-reservation.json"
            target = env["runs_dir"] / "target-res.json"
            res_file.rename(target)
            res_file.symlink_to(target)

            with self.assertRaises(QuotaProbeError):
                probe_cycle_quota_receipt(root, cycle_id)


if __name__ == "__main__":
    unittest.main()
