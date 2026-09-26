from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    from scripts.two_site_quota_resume_post_worker_zero import (
        _EXPECTED_RESULT_STATE_KEYS,
        worker_zero_post_result_context,
    )
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_zero
except ImportError:
    from two_site_proposal_pipeline import ProposalError  # type: ignore[no-redef]
    from two_site_quota_resume_post_worker_zero import (  # type: ignore[no-redef]
        _EXPECTED_RESULT_STATE_KEYS,
        worker_zero_post_result_context,
    )
    from two_site_quota_resume_worker_reader import (  # type: ignore[no-redef]
        load_prior_worker_zero,
    )

try:
    from tests.test_two_site_proposal_pipeline import HEAD, make_runner
except ImportError:
    from test_two_site_proposal_pipeline import HEAD, make_runner  # type: ignore[no-redef]


class TestTwoSiteQuotaResumePostWorkerZero(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.task_path = self.root / "task.json"
        self.src_file = self.root / "src.py"
        self.src_file.write_text("# orig\n")
        self.task = {
            "task_id": "T1",
            "goal": "G",
            "context_files": ["src.py"],
            "allowed_files": ["src.py"],
            "requirements": ["R1"],
            "test_selectors": ["test.py"],
        }
        self.task_path.write_text(json.dumps(self.task))

        reader_mod = sys.modules[load_prior_worker_zero.__module__]
        self.load_task_patcher = patch.object(
            reader_mod,
            "load_task",
            side_effect=lambda *args, **_kw: (
                copy.deepcopy(self.task),
                {
                    "src.py": {
                        "sha256": hashlib.sha256(self.src_file.read_bytes()).hexdigest(),
                        "content": self.src_file.read_text(),
                    }
                },
            ),
        )
        self.load_task_patcher.start()

        self.lock_held = False
        self.base_ctx = {
            "continuation_run_prefix": "run-post",
            "admitted_request": {"role": "worker"},
            "artifacts": {
                "worker_index": 0,
                "semantic_task_fingerprint": "fp-post-0",
            },
            "task": copy.deepcopy(self.task),
            "source_head": HEAD,
            "admitted_task_path": self.task_path,
        }

        @contextmanager
        def fake_continuation_ctx(root_path, req):
            self.lock_held = True
            try:
                yield copy.deepcopy(self.base_ctx)
            finally:
                self.lock_held = False

        post_mod = sys.modules[worker_zero_post_result_context.__module__]
        self.continuation_patcher = patch.object(
            post_mod,
            "worker_zero_continuation_context",
            side_effect=fake_continuation_ctx,
        )
        self.continuation_patcher.start()

    def tearDown(self):
        self.continuation_patcher.stop()
        self.load_task_patcher.stop()
        self.tmp_dir.cleanup()

    def _create_real_worker_zero(self, prefix: str = "run-post") -> tuple[dict, Path, str]:
        run_id = f"{prefix}-worker-0"
        runner, _ = make_runner(self.root, [{"content": "# v1\n"}])
        run_dir = runner(self.root, self.task_path, "worker", run_id, 300)
        res_file = run_dir / "result.json"
        res_sha = hashlib.sha256(res_file.read_bytes()).hexdigest()
        returned_state = {
            "state": "WORKER_PROPOSAL_READY",
            "task_id": "T1",
            "source_head": HEAD,
            "final_worker_run_id": run_id,
            "proposal_sha256": res_sha,
            "changed_paths": ["src.py"],
            "proposal_path": f".local/two-site-refactor/proposals/{run_id}/result.json",
            "correction_count": 0,
            "semantic_task_fingerprint": "fp-post-0",
        }
        return returned_state, run_dir, res_sha

    def test_file_backed_success_and_detached_schema(self):
        returned_state, run_dir, res_sha = self._create_real_worker_zero()
        run_id = returned_state["final_worker_run_id"]

        self.assertFalse(self.lock_held)
        with worker_zero_post_result_context(self.root, {}, returned_state) as ctx:
            self.assertTrue(self.lock_held)
            expected_loader_keys = {
                "result",
                "status",
                "result_sha",
                "result_text",
                "conversation_id",
            }
            self.assertEqual(set(ctx["worker_info"].keys()), expected_loader_keys)
            self.assertEqual(ctx["worker_info"]["result_sha"], res_sha)
            self.assertEqual(ctx["worker_info"]["result"]["status"], "PROPOSAL_READY")
            self.assertEqual(ctx["worker_info"]["status"]["run_id"], run_id)
            self.assertEqual(ctx["worker_info"]["result_text"], (run_dir / "result.json").read_text("utf-8"))
            self.assertEqual(ctx["worker_info"]["conversation_id"], f"conv-{run_id}")

            self.assertEqual(set(ctx["returned_state"].keys()), _EXPECTED_RESULT_STATE_KEYS)
            self.assertEqual(ctx["returned_state"], returned_state)

            expected_current = (
                run_id,
                res_sha,
                ctx["worker_info"]["result_text"],
                f"conv-{run_id}",
            )
            self.assertEqual(ctx["current_worker"], expected_current)
            self.assertEqual(ctx["seen_convs"], {f"conv-{run_id}"})

            ctx["worker_info"]["result"]["summary"] = "mutated-worker-info"
            ctx["returned_state"]["state"] = "mutated-state"
            self.assertEqual(returned_state["state"], "WORKER_PROPOSAL_READY")
            self.assertNotIn("mutated-worker-info", (run_dir / "result.json").read_text("utf-8"))

        self.assertFalse(self.lock_held)

    def test_fail_closed_sha_and_path_drift(self):
        returned_state, _, _ = self._create_real_worker_zero()

        bad_sha_state = dict(returned_state, proposal_sha256="0" * 64)
        with self.assertRaises(ProposalError):
            with worker_zero_post_result_context(self.root, {}, bad_sha_state):
                pass

        bad_path_state = dict(
            returned_state,
            proposal_path=".local/two-site-refactor/proposals/other-worker-0/result.json",
        )
        with self.assertRaises(ProposalError):
            with worker_zero_post_result_context(self.root, {}, bad_path_state):
                pass

        bad_changes_state = dict(returned_state, changed_paths=["other.py"])
        with self.assertRaises(ProposalError):
            with worker_zero_post_result_context(self.root, {}, bad_changes_state):
                pass

    def test_canonical_run_dir_symlink_rejection(self):
        returned_state, run_dir, _ = self._create_real_worker_zero()
        backup_dir = run_dir.parent / f"{run_dir.name}-backup"
        run_dir.rename(backup_dir)
        os.symlink(backup_dir, run_dir)
        try:
            with self.assertRaises(ProposalError) as err_ctx:
                with worker_zero_post_result_context(self.root, {}, returned_state):
                    pass
            self.assertIn("symlink", str(err_ctx.exception).lower())
        finally:
            run_dir.unlink()
            backup_dir.rename(run_dir)
