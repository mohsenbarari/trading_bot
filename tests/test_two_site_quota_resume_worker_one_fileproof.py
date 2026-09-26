#!/usr/bin/env python3
"""Offline unit tests for worker-one file-backed result reader and binding contract."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    from scripts.two_site_quota_resume_worker_one_file_reader import (
        read_and_bind_worker_one_result,
    )
except ImportError:
    from two_site_proposal_pipeline import ProposalError  # type: ignore[no-redef]
    from two_site_quota_resume_worker_one_file_reader import (  # type: ignore[no-redef]
        read_and_bind_worker_one_result,
    )

try:
    from tests.test_two_site_proposal_pipeline import HEAD, make_runner
except ImportError:
    from test_two_site_proposal_pipeline import HEAD, make_runner  # type: ignore[no-redef]


class TestTwoSiteQuotaResumeWorkerOneFileproof(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _setup_scenario(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], Path, Path, str]:
        src_path = self.root / "src.py"
        src_path.write_text("# orig\n")
        task_path = self.root / "task.json"
        task = {
            "task_id": "T1",
            "goal": "G",
            "context_files": ["src.py"],
            "allowed_files": ["src.py"],
            "requirements": ["R1"],
            "test_selectors": ["test.py"],
        }
        task_path.write_text(json.dumps(task))

        runner, _ = make_runner(
            self.root,
            [
                {"content": "# v1\n"},
                {"status": "CHANGES_REQUIRED", "findings": ["fix bug"]},
                {"content": "# v2\n"},
            ],
        )

        prefix = "proof-run"
        w0_run_id = f"{prefix}-worker-0"
        r0_run_id = f"{prefix}-reviewer-0"
        w1_run_id = f"{prefix}-worker-1"

        w0_dir = runner(self.root, task_path, "worker", w0_run_id, 300)
        w0_result_path = w0_dir / "result.json"
        w0_status_path = w0_dir / "status.json"
        w0_result = json.loads(w0_result_path.read_text())
        w0_status = json.loads(w0_status_path.read_text())
        w0_sha = w0_status["result_sha256"]
        w0_result_text = w0_result_path.read_text()

        r0_dir = runner(
            self.root,
            task_path,
            "verifier",
            r0_run_id,
            300,
            proposal=w0_result_path,
        )
        r0_result_path = r0_dir / "result.json"
        r0_status_path = r0_dir / "status.json"
        r0_result = json.loads(r0_result_path.read_text())
        r0_status = json.loads(r0_status_path.read_text())

        fb_path = self.root / "feedback.json"
        fb_data = {"reviewer": "gemini", "findings": ["fix bug"]}
        fb_path.write_text(json.dumps(fb_data))
        fb_sha = hashlib.sha256(fb_path.read_bytes()).hexdigest()

        w1_dir = runner(
            self.root,
            task_path,
            "worker",
            w1_run_id,
            300,
            feedback=fb_path,
            proposal=w0_result_path,
        )
        w1_result_path = w1_dir / "result.json"
        w1_status_path = w1_dir / "status.json"
        w1_status = json.loads(w1_status_path.read_text())
        w1_sha = w1_status["result_sha256"]

        fingerprint = "fp-proof-5e92fa77131c"
        src_sha = hashlib.sha256(b"# orig\n").hexdigest()

        ctx = {
            "continuation_run_prefix": prefix,
            "source_head": HEAD,
            "task": task,
            "sources": {
                "src.py": {
                    "sha256": src_sha,
                    "content": "# orig\n",
                },
            },
            "artifacts": {
                "worker_index": 1,
                "final_worker_run_id": w0_run_id,
                "proposal_sha256": w0_sha,
                "semantic_task_fingerprint": fingerprint,
            },
            "evidence": {
                "worker_info": {
                    "result": copy.deepcopy(w0_result),
                    "status": copy.deepcopy(w0_status),
                    "result_sha": w0_sha,
                    "result_text": w0_result_text,
                    "conversation_id": w0_status["conversation_id"],
                },
                "reviewer_status": {
                    "conversation_id": r0_status["conversation_id"],
                },
                "reviewer_result": copy.deepcopy(r0_result),
                "feedback": {
                    "reviewer": "gemini",
                    "findings": ["fix bug"],
                },
                "feedback_sha": fb_sha,
            },
        }

        returned_state = {
            "state": "WORKER_PROPOSAL_READY",
            "task_id": "T1",
            "source_head": HEAD,
            "final_worker_run_id": w1_run_id,
            "proposal_sha256": w1_sha,
            "changed_paths": ["src.py"],
            "proposal_path": f".local/two-site-refactor/proposals/{w1_run_id}/result.json",
            "correction_count": 1,
            "semantic_task_fingerprint": fingerprint,
        }

        return ctx, returned_state, w1_status_path, w1_result_path, fb_sha

    def test_read_and_bind_worker_one_result_success(self) -> None:
        ctx, returned_state, w1_status_path, w1_result_path, _ = (
            self._setup_scenario()
        )
        bound = read_and_bind_worker_one_result(
            self.root, ctx, returned_state
        )

        expected_bound_keys = {
            "worker_info",
            "returned_state",
            "current_worker",
            "seen_convs",
            "seen_norm_findings",
            "seen_test_signatures",
            "seen_bootstrap_signatures",
        }
        self.assertEqual(set(bound.keys()), expected_bound_keys)

        expected_worker_info_keys = {
            "result",
            "status",
            "result_sha",
            "result_text",
            "conversation_id",
        }
        self.assertEqual(
            set(bound["worker_info"].keys()), expected_worker_info_keys
        )

        w1_status = json.loads(w1_status_path.read_text())
        w1_result_text = w1_result_path.read_text()
        w1_sha = w1_status["result_sha256"]
        w0_status = ctx["evidence"]["worker_info"]["status"]
        r0_conv = ctx["evidence"]["reviewer_status"]["conversation_id"]

        curr_worker = bound["current_worker"]
        self.assertEqual(curr_worker[0], "proof-run-worker-1")
        self.assertEqual(curr_worker[1], w1_sha)
        self.assertEqual(curr_worker[2], w1_result_text)
        self.assertEqual(curr_worker[3], w1_status["conversation_id"])

        expected_seen_convs = {
            w0_status["conversation_id"],
            r0_conv,
            w1_status["conversation_id"],
        }
        self.assertEqual(bound["seen_convs"], expected_seen_convs)
        self.assertEqual(bound["seen_norm_findings"], {"fix bug"})

    def test_tamper_and_returned_state_mismatches_reject(self) -> None:
        ctx, returned_state, w1_status_path, w1_result_path, fb_sha = (
            self._setup_scenario()
        )

        # 1. Tamper status input_feedback_sha256
        st_data = json.loads(w1_status_path.read_text())
        st_data["input_feedback_sha256"] = "0" * 64
        w1_status_path.write_text(json.dumps(st_data))
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, ctx, returned_state)

        # Restore status
        st_data["input_feedback_sha256"] = fb_sha
        w1_status_path.write_text(json.dumps(st_data))

        # 2. Tamper result bytes
        orig_result_bytes = w1_result_path.read_bytes()
        w1_result_path.write_bytes(b'{"status": "PROPOSAL_READY", "tampered": true}')
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, ctx, returned_state)

        # Restore result bytes
        w1_result_path.write_bytes(orig_result_bytes)

        # 3. Wrong returned_state proposal_sha256
        bad_sha_state = copy.deepcopy(returned_state)
        bad_sha_state["proposal_sha256"] = "0" * 64
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, ctx, bad_sha_state)

        # 4. Wrong returned_state changed_paths
        bad_paths_state = copy.deepcopy(returned_state)
        bad_paths_state["changed_paths"] = ["other.py"]
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, ctx, bad_paths_state)


if __name__ == "__main__":
    unittest.main()
