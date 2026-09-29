from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from scripts.two_site_codex_autopilot import prune_runs


class TestAcceptedLineagePruneGuard(unittest.TestCase):
    def _create_run(
        self,
        runs_dir: Path,
        name: str,
        state: str = "CONTINUE",
        lineage_type: str | None = None,
        mtime: float | None = None,
    ) -> Path:
        run_path = runs_dir / name
        run_path.mkdir(parents=True, exist_ok=True)
        status_data = {"state": state, "summary": f"status for {name}"}
        (run_path / "status.json").write_text(json.dumps(status_data), encoding="utf-8")

        lineage_path = run_path / "accepted-lineage.json"
        if lineage_type == "valid":
            lineage_path.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
        elif lineage_type == "malformed":
            lineage_path.write_text("{not-valid-json", encoding="utf-8")
        elif lineage_type == "symlink":
            target = run_path / "dangling_target.json"
            lineage_path.symlink_to(target)
        elif lineage_type == "hardlink":
            os.link(run_path / "status.json", lineage_path)

        if mtime is not None:
            os.utime(run_path, (mtime, mtime))
        return run_path

    def test_prune_guard_preserves_lineage_entries_and_prunes_plain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_root = Path(temp_dir)
            runs_dir = state_root / "runs"
            runs_dir.mkdir(parents=True, mode=0o700)

            base_time = 1_700_000_000.0
            run_valid = self._create_run(runs_dir, "cycle-0001-20260925T120001Z-101", state="CONTINUE", lineage_type="valid", mtime=base_time + 10)
            run_malformed = self._create_run(runs_dir, "cycle-0002-20260925T120002Z-102", state="CONTINUE", lineage_type="malformed", mtime=base_time + 20)
            run_symlink = self._create_run(runs_dir, "cycle-0003-20260925T120003Z-103", state="CONTINUE", lineage_type="symlink", mtime=base_time + 30)
            run_hardlink = self._create_run(runs_dir, "cycle-0004-20260925T120004Z-104", state="CONTINUE", lineage_type="hardlink", mtime=base_time + 40)
            run_plain = self._create_run(runs_dir, "cycle-0005-20260925T120005Z-105", state="CONTINUE", lineage_type=None, mtime=base_time + 50)

            removed = prune_runs(state_root, keep=0)

            self.assertIn("cycle-0005-20260925T120005Z-105", removed)
            self.assertNotIn("cycle-0001-20260925T120001Z-101", removed)
            self.assertNotIn("cycle-0002-20260925T120002Z-102", removed)
            self.assertNotIn("cycle-0003-20260925T120003Z-103", removed)
            self.assertNotIn("cycle-0004-20260925T120004Z-104", removed)

            self.assertFalse(run_plain.exists())
            self.assertTrue(run_valid.exists())
            self.assertTrue(run_malformed.exists())
            self.assertTrue(run_symlink.exists())
            self.assertTrue(run_hardlink.exists())

    def test_prune_guard_injected_lstat_oserror_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_root = Path(temp_dir)
            runs_dir = state_root / "runs"
            runs_dir.mkdir(parents=True, mode=0o700)

            base_time = 1_700_000_000.0
            run_err = self._create_run(runs_dir, "cycle-0010-20260925T120010Z-110", state="CONTINUE", lineage_type=None, mtime=base_time + 10)
            run_plain = self._create_run(runs_dir, "cycle-0011-20260925T120011Z-111", state="CONTINUE", lineage_type=None, mtime=base_time + 20)

            real_lstat = os.lstat

            def selective_lstat(path, *args, **kwargs):
                p_str = str(path)
                if "cycle-0010" in p_str and "accepted-lineage.json" in p_str:
                    raise OSError(errno.EIO, "Injected disk I/O error")
                return real_lstat(path, *args, **kwargs)

            with patch("os.lstat", side_effect=selective_lstat):
                removed = prune_runs(state_root, keep=0)

            self.assertIn("cycle-0011-20260925T120011Z-111", removed)
            self.assertNotIn("cycle-0010-20260925T120010Z-110", removed)
            self.assertTrue(run_err.exists())
            self.assertFalse(run_plain.exists())

    def test_plain_run_retention_and_eligibility_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_root = Path(temp_dir)
            runs_dir = state_root / "runs"
            runs_dir.mkdir(parents=True, mode=0o700)

            base_time = 1_700_000_000.0
            run_old = self._create_run(runs_dir, "cycle-0020-20260925T120020Z-120", state="CONTINUE", lineage_type=None, mtime=base_time + 10)
            run_mid = self._create_run(runs_dir, "cycle-0021-20260925T120021Z-121", state="COMPLETE", lineage_type=None, mtime=base_time + 20)
            run_new = self._create_run(runs_dir, "cycle-0022-20260925T120022Z-122", state="CONTINUE", lineage_type=None, mtime=base_time + 30)
            run_running = self._create_run(runs_dir, "cycle-0023-20260925T120023Z-123", state="RUNNING", lineage_type=None, mtime=base_time + 5)

            removed = prune_runs(state_root, keep=1)

            self.assertIn("cycle-0020-20260925T120020Z-120", removed)
            self.assertIn("cycle-0021-20260925T120021Z-121", removed)
            self.assertNotIn("cycle-0022-20260925T120022Z-122", removed)
            self.assertNotIn("cycle-0023-20260925T120023Z-123", removed)

            self.assertFalse(run_old.exists())
            self.assertFalse(run_mid.exists())
            self.assertTrue(run_new.exists())
            self.assertTrue(run_running.exists())

    def test_no_live_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_root = Path(temp_dir)
            runs_dir = state_root / "runs"
            runs_dir.mkdir(parents=True, mode=0o700)
            self._create_run(runs_dir, "cycle-0030-20260925T120030Z-130", state="CONTINUE", lineage_type="valid")
            removed = prune_runs(state_root, keep=0)
            self.assertEqual(removed, [])


if __name__ == "__main__":
    unittest.main()
