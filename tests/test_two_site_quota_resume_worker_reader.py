from __future__ import annotations

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
    from scripts.two_site_proposal_pipeline import (
        MAX_CONTEXT,
        ProposalError,
    )
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_zero
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        ProposalError,
    )
    from two_site_quota_resume_worker_reader import load_prior_worker_zero  # type: ignore[no-redef]

try:
    from tests.test_two_site_proposal_pipeline import HEAD, make_runner
except ImportError:
    from test_two_site_proposal_pipeline import HEAD, make_runner  # type: ignore[no-redef]


class TestTwoSiteQuotaResumeWorkerReader(unittest.TestCase):
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

    def tearDown(self):
        self.load_task_patcher.stop()
        self.tmp_dir.cleanup()

    def _create_artifacts(self, run_id: str, orig_prefix: str = "run-test", corr: int = 0) -> tuple[dict, Path]:
        runner, _ = make_runner(self.root, [{"content": "# v1\n"}])
        run_dir = runner(self.root, self.task_path, "worker", run_id, 300)
        res_sha = hashlib.sha256((run_dir / "result.json").read_bytes()).hexdigest()
        return {
            "task_id": "T1",
            "source_head": HEAD,
            "original_run_prefix": orig_prefix,
            "final_worker_run_id": run_id,
            "proposal_path": f".local/two-site-refactor/proposals/{run_id}/result.json",
            "proposal_sha256": res_sha,
            "correction_count": corr,
        }, run_dir

    def test_success_worker_zero_and_retry(self):
        for run_id in ("run-test-worker-0", "run-test-worker-0-retry-1", "run-test-worker-0-retry-2"):
            with self.subTest(run_id=run_id):
                artifacts, run_dir = self._create_artifacts(run_id)
                rec = load_prior_worker_zero(self.root, self.task_path, artifacts)
                self.assertEqual(rec["result_sha"], artifacts["proposal_sha256"])
                self.assertEqual(rec["result"]["status"], "PROPOSAL_READY")
                self.assertEqual(rec["status"]["run_id"], run_id)
                self.assertEqual(rec["conversation_id"], f"conv-{run_id}")
                self.assertEqual(rec["result_text"], (run_dir / "result.json").read_text("utf-8"))
                rec["result"]["summary"] = "mutated"
                self.assertNotEqual((run_dir / "result.json").read_text(), "mutated")

    def test_correction_count_rejections(self):
        artifacts, _ = self._create_artifacts("run-test-worker-0")
        for bad_corr in (1, 2):
            with self.subTest(bad_corr=bad_corr):
                arts = dict(artifacts, correction_count=bad_corr)
                with self.assertRaises(ProposalError) as ctx:
                    load_prior_worker_zero(self.root, self.task_path, arts)
                self.assertIn("unsupported", str(ctx.exception))
        for invalid_corr in (False, True, -1, 3, "0", None):
            with self.subTest(invalid_corr=invalid_corr):
                arts = dict(artifacts, correction_count=invalid_corr)
                with self.assertRaises(ProposalError):
                    load_prior_worker_zero(self.root, self.task_path, arts)

    def test_artifacts_validation_failures(self):
        artifacts, _ = self._create_artifacts("run-test-worker-0")
        with self.assertRaises(ProposalError):
            load_prior_worker_zero("nonexistent-root-dir-xyz", self.task_path, artifacts)
        with self.assertRaises(ProposalError):
            load_prior_worker_zero(self.root, self.task_path, "not-a-mapping")

        cases = [
            ("wrong_sha", {"proposal_sha256": "0" * 64}),
            ("invalid_sha_fmt", {"proposal_sha256": "not-hex"}),
            ("wrong_path", {"proposal_path": "other/path/result.json"}),
            ("wrong_head", {"source_head": "0" * 40}),
            ("invalid_head_fmt", {"source_head": "G" * 40}),
            ("wrong_task_id", {"task_id": "T2"}),
            ("empty_task_id", {"task_id": ""}),
            ("invalid_orig_prefix", {"original_run_prefix": "-invalid"}),
            ("mismatched_worker_id", {"final_worker_run_id": "other-worker-0"}),
            ("worker_1_rejected", {"final_worker_run_id": "run-test-worker-1"}),
            ("retry_3_rejected", {"final_worker_run_id": "run-test-worker-0-retry-3"}),
        ]
        for name, override in cases:
            with self.subTest(case=name):
                arts = dict(artifacts, **override)
                with self.assertRaises(ProposalError):
                    load_prior_worker_zero(self.root, self.task_path, arts)

    def test_tampered_status_and_snapshot(self):
        artifacts, run_dir = self._create_artifacts("run-test-worker-0")
        status_path = run_dir / "status.json"
        st = json.loads(status_path.read_text())
        st["result_sha256"] = "0" * 64
        status_path.write_text(json.dumps(st))
        with self.assertRaises(ProposalError):
            load_prior_worker_zero(self.root, self.task_path, artifacts)

        st["result_sha256"] = artifacts["proposal_sha256"]
        status_path.write_text(json.dumps(st))
        task_json_path = run_dir / "task.json"
        snap = json.loads(task_json_path.read_text())
        snap["source_head"] = "0" * 40
        task_json_path.write_text(json.dumps(snap))
        with self.assertRaises(ProposalError):
            load_prior_worker_zero(self.root, self.task_path, artifacts)

    def test_symlink_and_oversized(self):
        artifacts, run_dir = self._create_artifacts("run-test-worker-0")
        sym_dir = self.root / ".local/two-site-refactor/proposals/run-sym-worker-0"
        os.symlink(run_dir, sym_dir)
        arts_sym = dict(
            artifacts,
            original_run_prefix="run-sym",
            final_worker_run_id="run-sym-worker-0",
            proposal_path=".local/two-site-refactor/proposals/run-sym-worker-0/result.json",
        )
        with self.assertRaises(ProposalError):
            load_prior_worker_zero(self.root, self.task_path, arts_sym)

        art2, run_dir2 = self._create_artifacts("run-symf-worker-0", orig_prefix="run-symf")
        res_file = run_dir2 / "result.json"
        real_file = self.root / "real_result.json"
        real_file.write_bytes(res_file.read_bytes())
        res_file.unlink()
        os.symlink(real_file, res_file)
        with self.assertRaises(ProposalError):
            load_prior_worker_zero(self.root, self.task_path, art2)

        art3, run_dir3 = self._create_artifacts("run-big-worker-0", orig_prefix="run-big")
        (run_dir3 / "result.json").write_text("x" * (MAX_CONTEXT + 1024))
        with self.assertRaises(ProposalError):
            load_prior_worker_zero(self.root, self.task_path, art3)
