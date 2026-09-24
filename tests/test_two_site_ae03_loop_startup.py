"""Tests for AE03 runtime startup invariants in autopilot run_loop."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_assignment_scope import AssignmentScope
import scripts.two_site_codex_autopilot as _autopilot_mod
from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    AutopilotError,
    ensure_rescope_epoch_dir,
    publish_initial_rescope_receipt,
    publish_next_rescope_receipt,
    publish_rescope_intent,
    run_loop,
    unique_json,
)

RECEIPT_FILENAME = getattr(
    _autopilot_mod,
    "RESCOPE_RECEIPT_FILENAME",
    getattr(_autopilot_mod, "RECEIPT_FILENAME", "receipt.json"),
)

HEAD = "1" * 40
cycle_calls: list[dict] = []


def _repo_facts():
    return {
        "head": HEAD,
        "status": "",
        "protected_refs": {r: "2" * 40 for r in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "3" * 40 for p in CONTROL_PATHS},
        "assignment_scope": AssignmentScope(
            "AE03-ASSIGN",
            "P1-00b",
            "4" * 64,
            "2099-01-01T00:00:00Z",
            ("scripts/two_site_codex_autopilot.py",),
            (),
        ),
    }


def _rescope_result():
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": "rejected",
        "commit": None,
        "next_action": "rescope unit",
        "blockers": ["failure"],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": HEAD,
        "head_after": HEAD,
        "head_changed": False,
    }


def fake_run_cycle(
    root,
    codex,
    state_root,
    cycle,
    timeout,
    recovery=None,
    continuation=None,
    before_model=None,
):
    cycle_calls.append({"cycle": cycle, "recovery": recovery})
    if before_model is not None:
        before_model(_repo_facts())
    run_dir = state_root / "runs" / f"cycle-{cycle:04d}"
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return run_dir, {
        "schema_version": "1.0",
        "state": "COMPLETE",
        "disposition": "NONE",
        "category": "NONE",
        "summary": "done",
        "commit": None,
        "next_action": None,
        "blockers": [],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_after": HEAD,
    }


class TestAE03LoopStartup(unittest.TestCase):
    def setUp(self):
        cycle_calls.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.codex = self.root / "fake_codex"
        self.state_root = self.root / ".local/two-site-refactor/autopilot"
        self.repo = _repo_facts()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _patch_all(self):
        return (
            patch(
                "scripts.two_site_codex_autopilot.validate_repository",
                return_value=self.repo,
            ),
            patch(
                "scripts.two_site_codex_autopilot.git",
                side_effect=lambda r, *a: HEAD if "rev-parse" in a else "2" * 40,
            ),
            patch(
                "scripts.two_site_codex_autopilot.run_cycle",
                side_effect=fake_run_cycle,
            ),
        )

    def test_first_launch_absent_state_root_umask022(self):
        p_val, p_git, p_cycle = self._patch_all()
        with p_val, p_git, p_cycle:
            assert not self.state_root.exists()
            old_umask = os.umask(0o022)
            try:
                ret = run_loop(self.root, self.codex, 1, 300, True)
            finally:
                os.umask(old_umask)
            self.assertEqual(ret, 0)
            self.assertEqual(len(cycle_calls), 1)
            self.assertEqual(self.state_root.stat().st_mode & 0o777, 0o700)

    def test_startup_recovery_context_from_receipt1(self):
        self.state_root.mkdir(parents=True, mode=0o700)
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        publish_initial_rescope_receipt(epoch, _rescope_result(), self.repo)
        p_val, p_git, p_cycle = self._patch_all()
        with p_val, p_git, p_cycle:
            ret = run_loop(self.root, self.codex, 1, 300, True)
            self.assertEqual(ret, 0)
            self.assertEqual(len(cycle_calls), 1)
            rec = cycle_calls[0]["recovery"]
            self.assertEqual(rec["attempt"], 1)
            self.assertEqual(rec["attempt_count"], 1)
            self.assertFalse(rec["defer_prior_unit"])
            self.assertNotIn("deferred_unit", rec)

    def test_stale_intent_hard_blocked_zero_cycle(self):
        self.state_root.mkdir(parents=True, mode=0o700)
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        publish_rescope_intent(epoch, 1)
        p_val, p_git, p_cycle = self._patch_all()
        with p_val, p_git, p_cycle:
            ret = run_loop(self.root, self.codex, 1, 300, True)
            self.assertEqual(ret, 2)
            self.assertEqual(len(cycle_calls), 0)
            status = unique_json(
                (self.state_root / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["state"], "BLOCKED")
            self.assertTrue(any("already exists" in b for b in status["blockers"]))

    def test_startup_recovery_context_from_receipt2(self):
        self.state_root.mkdir(parents=True, mode=0o700)
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        publish_initial_rescope_receipt(epoch, _rescope_result(), self.repo)
        res2 = _rescope_result()
        res2["next_action"] = "rescope unit attempt 2 action"
        res2["blockers"] = ["rescope unit attempt 2 blocker"]
        publish_next_rescope_receipt(epoch, res2, self.repo)
        p_val, p_git, p_cycle = self._patch_all()
        with p_val, p_git, p_cycle:
            ret = run_loop(self.root, self.codex, 1, 300, True)
            self.assertEqual(ret, 0)
            self.assertEqual(len(cycle_calls), 1)
            rec = cycle_calls[0]["recovery"]
            self.assertEqual(rec["attempt"], 2)
            self.assertEqual(rec.get("count", rec.get("attempt_count")), 2)
            self.assertEqual(rec["attempt_count"], 2)
            self.assertTrue(rec["defer_prior_unit"])
            self.assertEqual(
                rec["deferred_unit"]["next_action"], res2["next_action"]
            )
            self.assertEqual(rec["deferred_unit"]["blockers"], res2["blockers"])

    def test_startup_receipt3_terminal_blocked_zero_cycle(self):
        self.state_root.mkdir(parents=True, mode=0o700)
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        publish_initial_rescope_receipt(epoch, _rescope_result(), self.repo)
        res2 = _rescope_result()
        res2["next_action"] = "rescope unit attempt 2 action"
        res2["blockers"] = ["rescope unit attempt 2 blocker"]
        publish_next_rescope_receipt(epoch, res2, self.repo)
        res3 = _rescope_result()
        res3["next_action"] = "rescope unit attempt 3 action"
        res3["blockers"] = ["rescope unit attempt 3 blocker"]
        publish_next_rescope_receipt(epoch, res3, self.repo)
        p_val, p_git, p_cycle = self._patch_all()
        with p_val, p_git, p_cycle:
            ret = run_loop(self.root, self.codex, 1, 300, True)
            self.assertEqual(ret, 2)
            self.assertEqual(len(cycle_calls), 0)
            status = unique_json(
                (self.state_root / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["state"], "BLOCKED")
            self.assertTrue(any("terminal" in b.lower() for b in status["blockers"]))

    def test_malformed_canonical_receipt_leaf_blocked_zero_cycle(self):
        self.state_root.mkdir(parents=True, mode=0o700)
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        publish_initial_rescope_receipt(epoch, _rescope_result(), self.repo)
        receipt_paths = [
            p for p in epoch.rglob("*") if p.is_file() and p.name == RECEIPT_FILENAME
        ]
        if not receipt_paths:
            receipt_paths = [epoch / RECEIPT_FILENAME]
        for rp in receipt_paths:
            rp.write_text("{malformed json: true", encoding="utf-8")
        p_val, p_git, p_cycle = self._patch_all()
        with p_val, p_git, p_cycle:
            ret = run_loop(self.root, self.codex, 1, 300, True)
            self.assertEqual(ret, 2)
            self.assertEqual(len(cycle_calls), 0)
            status = unique_json(
                (self.state_root / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["state"], "BLOCKED")
            self.assertTrue(len(status["blockers"]) > 0)

    def test_nonprivate_epoch_mode0755_blocked_zero_cycle(self):
        self.state_root.mkdir(parents=True, mode=0o700)
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        epoch.chmod(0o755)
        p_val, p_git, p_cycle = self._patch_all()
        with p_val, p_git, p_cycle:
            ret = run_loop(self.root, self.codex, 1, 300, True)
            self.assertEqual(ret, 2)
            self.assertEqual(len(cycle_calls), 0)
            status = unique_json(
                (self.state_root / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["state"], "BLOCKED")
            self.assertTrue(len(status["blockers"]) > 0)

    def test_stop_file_stops_before_preflights(self):
        self.state_root.mkdir(parents=True, mode=0o700)
        (self.state_root / "STOP").write_text("", encoding="utf-8")

        def _fail_preflight(*_args, **_kwargs):
            self.fail("Preflight called despite STOP file presence")

        with patch(
            "scripts.two_site_codex_autopilot.validate_repository",
            side_effect=_fail_preflight,
        ), patch(
            "scripts.two_site_codex_autopilot.prepare_rescope_attempt",
            side_effect=_fail_preflight,
        ), patch(
            "scripts.two_site_codex_autopilot.run_cycle",
            side_effect=fake_run_cycle,
        ):
            ret = run_loop(self.root, self.codex, 1, 300, True)
            self.assertEqual(ret, 0)
            self.assertEqual(len(cycle_calls), 0)

    def test_existing_state_root_mode0755_fails_closed(self):
        self.state_root.mkdir(parents=True, mode=0o755)
        self.state_root.chmod(0o755)
        p_val, p_git, p_cycle = self._patch_all()
        with p_val, p_git, p_cycle:
            with self.assertRaises(AutopilotError) as ctx:
                run_loop(self.root, self.codex, 1, 300, True)
            self.assertIn("mode must be 0700", str(ctx.exception))
            self.assertEqual(len(cycle_calls), 0)
            self.assertEqual(self.state_root.stat().st_mode & 0o777, 0o755)

    def test_validate_repository_huge_exception_status_bounded(self):
        self.state_root.mkdir(parents=True, mode=0o700)
        huge_msg = "E" * (1024 * 1024)
        with patch(
            "scripts.two_site_codex_autopilot.validate_repository",
            side_effect=RuntimeError(huge_msg),
        ), patch(
            "scripts.two_site_codex_autopilot.git",
            side_effect=lambda r, *a: HEAD if "rev-parse" in a else "2" * 40,
        ), patch(
            "scripts.two_site_codex_autopilot.run_cycle",
            side_effect=fake_run_cycle,
        ):
            ret = run_loop(self.root, self.codex, 1, 300, True)
            self.assertEqual(ret, 2)
            self.assertEqual(len(cycle_calls), 0)
            status_path = self.state_root / "status.json"
            self.assertTrue(status_path.exists())
            raw_bytes = status_path.read_bytes()
            self.assertLess(len(raw_bytes), 4096)
            status = unique_json(raw_bytes.decode("utf-8"))
            self.assertEqual(status["state"], "BLOCKED")
            self.assertEqual(len(status["blockers"]), 1)
            self.assertLessEqual(len(status["blockers"][0]), 512)

    def test_interrupt_and_system_exit_propagate_without_model(self):
        self.state_root.mkdir(parents=True, mode=0o700)
        for exc_cls in (KeyboardInterrupt, SystemExit):
            with self.subTest(exc=exc_cls.__name__):
                cycle_calls.clear()
                with patch(
                    "scripts.two_site_codex_autopilot.validate_repository",
                    side_effect=exc_cls,
                ), patch(
                    "scripts.two_site_codex_autopilot.git",
                    side_effect=lambda r, *a: HEAD if "rev-parse" in a else "2" * 40,
                ), patch(
                    "scripts.two_site_codex_autopilot.run_cycle",
                    side_effect=fake_run_cycle,
                ):
                    with self.assertRaises(exc_cls):
                        run_loop(self.root, self.codex, 1, 300, True)
                    self.assertEqual(len(cycle_calls), 0)


if __name__ == "__main__":
    unittest.main()
