"""Hermetic tests for autopilot run_loop durable RESCOPE runtime wiring."""
from __future__ import annotations

from contextlib import contextmanager
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
    load_latest_rescope_receipt,
    rescope_intent_exists,
    run_loop,
    unique_json,
)

RECEIPT_FILENAME = getattr(
    _autopilot_mod,
    "RESCOPE_RECEIPT_FILENAME",
    getattr(_autopilot_mod, "RECEIPT_FILENAME", "receipt.json"),
)

HEAD = "1" * 40


def _repo_facts() -> dict:
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


def _rescope_result(
    attempt: int = 1,
    next_action: str = "rescope unit",
    blockers: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": f"rescope attempt {attempt} summary",
        "commit": None,
        "next_action": next_action,
        "blockers": blockers if blockers is not None else [f"rescope blocker {attempt}"],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": HEAD,
        "head_after": HEAD,
        "head_changed": False,
    }


def _load_all_epoch_receipts(epoch_dir: Path) -> list[dict]:
    by_attempt: dict[int, dict] = {}
    for p in sorted(epoch_dir.rglob("*")):
        if p.is_file():
            try:
                data = unique_json(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "attempt" in data and "result" in data:
                    attempt_num = int(data["attempt"])
                    by_attempt[attempt_num] = data
            except Exception:
                pass
    return [by_attempt[a] for a in sorted(by_attempt)]


class TestAE03RuntimeWire(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.codex = self.root / "fake_codex"
        self.state_root = self.root / ".local/two-site-refactor/autopilot"
        self.state_root.mkdir(parents=True, mode=0o700)
        self.repo = _repo_facts()

    def tearDown(self):
        self.temp_dir.cleanup()

    @contextmanager
    def _apply_patches(self, run_cycle_fn):
        with patch(
            "scripts.two_site_codex_autopilot.validate_repository",
            side_effect=lambda r, require_clean=True: self.repo,
        ), patch(
            "scripts.two_site_codex_autopilot.git",
            side_effect=lambda r, *a: HEAD if "rev-parse" in a else ("" if "status" in a else "2" * 40),
        ), patch(
            "scripts.two_site_codex_autopilot.ref_snapshot",
            return_value={"refs/heads/main": HEAD},
        ), patch(
            "scripts.two_site_codex_autopilot.prune_runs",
            return_value=None,
        ), patch(
            "scripts.two_site_codex_autopilot.run_cycle",
            side_effect=run_cycle_fn,
        ):
            yield

    def test_a_three_receipts_terminal_and_recovery(self):
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        cycle_calls: list[dict] = []

        def fake_run_cycle(
            root,
            codex,
            state_root,
            cycle,
            timeout,
            recovery=None,
            continuation=None,
            before_model=None,
            **kwargs,
        ):
            cycle_calls.append({"cycle": cycle, "recovery": recovery})
            if before_model is not None:
                before_model(self.repo)
            run_dir = state_root / "runs" / f"cycle-{cycle:04d}"
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            return run_dir, _rescope_result(
                attempt=cycle,
                next_action=f"rescope action {cycle}",
                blockers=[f"rescope blocker {cycle}"],
            )

        with self._apply_patches(fake_run_cycle):
            ret = run_loop(self.root, self.codex, max_cycles=10, timeout=300, once=False)

        self.assertEqual(ret, 2)
        self.assertEqual(len(cycle_calls), 3)

        self.assertIsNone(cycle_calls[0]["recovery"])

        rec2 = cycle_calls[1]["recovery"]
        self.assertIsNotNone(rec2)
        self.assertEqual(rec2["attempt"], 1)
        self.assertFalse(rec2["defer_prior_unit"])

        rec3 = cycle_calls[2]["recovery"]
        self.assertIsNotNone(rec3)
        self.assertEqual(rec3["attempt"], 2)
        self.assertTrue(rec3["defer_prior_unit"])
        self.assertEqual(rec3["deferred_unit"]["next_action"], "rescope action 2")
        self.assertEqual(rec3["deferred_unit"]["blockers"], ["rescope blocker 2"])

        receipts = _load_all_epoch_receipts(epoch)
        self.assertEqual(len(receipts), 3)
        self.assertEqual([r["attempt"] for r in receipts], [1, 2, 3])
        self.assertFalse(receipts[0]["terminal"])
        self.assertFalse(receipts[1]["terminal"])
        self.assertTrue(receipts[2]["terminal"])

        latest = load_latest_rescope_receipt(epoch, self.repo)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["attempt"], 3)
        self.assertTrue(latest["terminal"])

        self.assertTrue(rescope_intent_exists(epoch, 1))
        self.assertTrue(rescope_intent_exists(epoch, 2))
        self.assertTrue(rescope_intent_exists(epoch, 3))

        status = unique_json((self.state_root / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "BLOCKED")
        self.assertEqual(status["cycle"], 3)
        self.assertEqual(status["summary"], receipts[2]["result"]["summary"])
        self.assertEqual(status["next_action"], receipts[2]["result"]["next_action"])
        self.assertEqual(status["blockers"], receipts[2]["result"]["blockers"])
        self.assertIsNone(status.get("gemini_worker_run_id"))
        self.assertIsNone(status.get("gemini_reviewer_run_id"))
        self.assertIsNone(status.get("proposal_sha256"))

    def test_b_cross_process_restart_advances_to_attempt_two(self):
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        cycle_calls_1: list[dict] = []

        def fake_run_cycle_1(
            root,
            codex,
            state_root,
            cycle,
            timeout,
            recovery=None,
            continuation=None,
            before_model=None,
            **kwargs,
        ):
            cycle_calls_1.append({"cycle": cycle, "recovery": recovery})
            if before_model is not None:
                before_model(self.repo)
            run_dir = state_root / "runs" / f"run-p1-{cycle:04d}"
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            return run_dir, _rescope_result(
                1,
                next_action="rescope action 1",
                blockers=["blocker 1"],
            )

        with self._apply_patches(fake_run_cycle_1):
            ret1 = run_loop(self.root, self.codex, max_cycles=10, timeout=300, once=True)

        self.assertEqual(ret1, 2)
        self.assertEqual(len(cycle_calls_1), 1)
        self.assertIsNone(cycle_calls_1[0]["recovery"])

        self.assertTrue(rescope_intent_exists(epoch, 1))
        self.assertFalse(rescope_intent_exists(epoch, 2))
        latest1 = load_latest_rescope_receipt(epoch, self.repo)
        self.assertIsNotNone(latest1)
        self.assertEqual(latest1["attempt"], 1)
        self.assertFalse(latest1["terminal"])

        cycle_calls_2: list[dict] = []

        def fake_run_cycle_2(
            root,
            codex,
            state_root,
            cycle,
            timeout,
            recovery=None,
            continuation=None,
            before_model=None,
            **kwargs,
        ):
            cycle_calls_2.append({"cycle": cycle, "recovery": recovery})
            if before_model is not None:
                before_model(self.repo)
            run_dir = state_root / "runs" / f"run-p2-{cycle:04d}"
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            return run_dir, _rescope_result(
                2,
                next_action="rescope action 2",
                blockers=["blocker 2"],
            )

        with self._apply_patches(fake_run_cycle_2):
            ret2 = run_loop(self.root, self.codex, max_cycles=10, timeout=300, once=True)

        self.assertEqual(ret2, 2)
        self.assertEqual(len(cycle_calls_2), 1)
        rec2 = cycle_calls_2[0]["recovery"]
        self.assertIsNotNone(rec2)
        self.assertEqual(rec2["attempt"], 1)
        self.assertFalse(rec2["defer_prior_unit"])

        self.assertTrue(rescope_intent_exists(epoch, 1))
        self.assertTrue(rescope_intent_exists(epoch, 2))
        self.assertFalse(rescope_intent_exists(epoch, 3))

        receipts = _load_all_epoch_receipts(epoch)
        self.assertEqual(len(receipts), 2)
        self.assertEqual([r["attempt"] for r in receipts], [1, 2])
        self.assertFalse(receipts[0]["terminal"])
        self.assertFalse(receipts[1]["terminal"])
        self.assertEqual(receipts[0]["result"]["next_action"], "rescope action 1")
        self.assertEqual(receipts[1]["result"]["next_action"], "rescope action 2")

        latest2 = load_latest_rescope_receipt(epoch, self.repo)
        self.assertIsNotNone(latest2)
        self.assertEqual(latest2["attempt"], 2)
        self.assertFalse(latest2["terminal"])

        status = unique_json((self.state_root / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "RESCOPE")
        self.assertEqual(status["cycle"], 1)

    def test_c_post_marker_runtime_error_hard_blocked_no_retry(self):
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        calls: list[dict] = []

        def fake_run_cycle_err(
            root,
            codex,
            state_root,
            cycle,
            timeout,
            recovery=None,
            continuation=None,
            before_model=None,
            **kwargs,
        ):
            calls.append({"cycle": cycle})
            if before_model is not None:
                before_model(self.repo)
            raise RuntimeError("simulated model crash after marker")

        with self._apply_patches(fake_run_cycle_err):
            ret = run_loop(self.root, self.codex, max_cycles=5, timeout=300, once=False)

        self.assertEqual(ret, 2)
        self.assertEqual(len(calls), 1)

        self.assertTrue(rescope_intent_exists(epoch, 1))
        self.assertIsNone(load_latest_rescope_receipt(epoch, self.repo))
        self.assertEqual(len(_load_all_epoch_receipts(epoch)), 0)

        status = unique_json((self.state_root / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "BLOCKED")
        self.assertNotEqual(status.get("state"), "RETRYABLE_FAILURE")
        self.assertNotIn("retry", status)
        self.assertTrue(any("simulated model crash after marker" in b for b in status.get("blockers", [])))

    def test_d_missing_callback_hard_blocked_no_marker_or_receipt(self):
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        calls: list[dict] = []

        def fake_run_cycle_no_cb(
            root,
            codex,
            state_root,
            cycle,
            timeout,
            recovery=None,
            continuation=None,
            before_model=None,
            **kwargs,
        ):
            calls.append({"cycle": cycle})
            run_dir = state_root / "runs" / f"cycle-{cycle:04d}"
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            return run_dir, _rescope_result(1)

        with self._apply_patches(fake_run_cycle_no_cb):
            ret = run_loop(self.root, self.codex, max_cycles=5, timeout=300, once=False)

        self.assertEqual(ret, 2)
        self.assertEqual(len(calls), 1)

        self.assertFalse(rescope_intent_exists(epoch, 1))
        self.assertIsNone(load_latest_rescope_receipt(epoch, self.repo))
        self.assertEqual(len(_load_all_epoch_receipts(epoch)), 0)

        status = unique_json((self.state_root / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "BLOCKED")
        self.assertTrue(any("without before_model" in b for b in status.get("blockers", [])))

    def test_e_outcome_fault_hard_blocked_marker_stays_no_receipt(self):
        epoch = ensure_rescope_epoch_dir(self.state_root, self.repo)
        calls: list[dict] = []

        def fake_run_cycle_with_cb(
            root,
            codex,
            state_root,
            cycle,
            timeout,
            recovery=None,
            continuation=None,
            before_model=None,
            **kwargs,
        ):
            calls.append({"cycle": cycle})
            if before_model is not None:
                before_model(self.repo)
            run_dir = state_root / "runs" / f"cycle-{cycle:04d}"
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            return run_dir, _rescope_result(1)

        with self._apply_patches(fake_run_cycle_with_cb), patch(
            "scripts.two_site_codex_autopilot.publish_validated_rescope_outcome",
            side_effect=AutopilotError("injected outcome fault"),
        ):
            ret = run_loop(self.root, self.codex, max_cycles=5, timeout=300, once=False)

        self.assertEqual(ret, 2)
        self.assertEqual(len(calls), 1)

        self.assertTrue(rescope_intent_exists(epoch, 1))
        self.assertIsNone(load_latest_rescope_receipt(epoch, self.repo))
        self.assertEqual(len(_load_all_epoch_receipts(epoch)), 0)

        status = unique_json((self.state_root / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "BLOCKED")
        self.assertTrue(any("injected outcome fault" in b for b in status.get("blockers", [])))


if __name__ == "__main__":
    unittest.main()
