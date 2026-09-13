#!/usr/bin/env python3
"""Offline unit tests for pre-review candidate-test gate state machine."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping
import unittest
from unittest.mock import patch

try:
    import scripts.two_site_proposal_pipeline as pipeline
    from scripts.two_site_proposal_pipeline import (
        BRANCH, ProposalError, run_pipeline,
    )
    from scripts.two_site_proposal_runner import MODEL, WORKER_MODEL, file_sha256, write_json
    from scripts.two_site_pre_review_receipt import (
        EXPECTED_RECEIPT_KEYS, validate_test_receipt,
    )
except ModuleNotFoundError:
    import two_site_proposal_pipeline as pipeline  # type: ignore[no-redef]
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        BRANCH, ProposalError, run_pipeline,
    )
    from two_site_proposal_runner import MODEL, WORKER_MODEL, file_sha256, write_json  # type: ignore[no-redef]
    from two_site_pre_review_receipt import (  # type: ignore[no-redef]
        EXPECTED_RECEIPT_KEYS, validate_test_receipt,
    )

HEAD = "a" * 40
DEFAULT_SELECTOR = "tests/test_two_site_pipeline_pre_review_gate.py"


def fake_git(_root: Path, *args: str) -> str:
    cmd = " ".join(args)
    if "branch --show-current" in cmd:
        return BRANCH
    if "rev-parse HEAD" in cmd:
        return HEAD
    if "status --porcelain" in cmd:
        return ""
    return "b" * 40 if "show-ref" in cmd else ""


def build_test_receipt(
    proposal_sha: str = "0" * 64,
    *,
    state: str = "PASSED",
    returncode: int = 0,
    stdout_tail: str = "1 passed in 0.05s\n",
    stderr_tail: str = "",
    selectors: list[str] | None = None,
    unsupported_specs: list[str] | None = None,
    snapshot_files: int = 1,
    snapshot_bytes: int = 100,
    isolation: str = "test-double",
) -> dict[str, Any]:
    if selectors is None:
        selectors = [DEFAULT_SELECTOR]
    if unsupported_specs is None:
        unsupported_specs = []
    receipt = {
        "state": state,
        "proposal_sha256": proposal_sha,
        "selectors": list(selectors),
        "unsupported_specs": list(unsupported_specs),
        "returncode": returncode,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "snapshot_files": snapshot_files,
        "snapshot_bytes": snapshot_bytes,
        "isolation": isolation,
    }
    return validate_test_receipt(receipt, proposal_sha)


def make_runner(root: Path, responses: list[dict[str, Any]]):
    calls: list[tuple[Any, ...]] = []
    src_sha = file_sha256(root / "src.py")

    def mock_run(
        _r: Path,
        _t: Path,
        role: str,
        run_id: str,
        timeout: int,
        feedback: Path | None = None,
        proposal: Path | None = None,
        cli: Path | None = None,
        test_evidence: Path | None = None,
    ) -> Path:
        calls.append((role, run_id, timeout, feedback, proposal, cli, test_evidence))
        resp = responses[len(calls) - 1]
        out = root / ".local/two-site-refactor/proposals" / run_id
        out.mkdir(parents=True, exist_ok=True)
        st_val = resp.get("status", "PROPOSAL_READY" if role == "worker" else "VERIFIED")
        ch = (
            [
                {
                    "path": "src.py",
                    "original_sha256": src_sha,
                    "content": resp.get("content", f"# c {run_id}\n"),
                }
            ]
            if role == "worker" and st_val == "PROPOSAL_READY"
            else []
        )
        cov = [1] if st_val in ("PROPOSAL_READY", "VERIFIED") else []
        fnd = resp.get("findings", []) if st_val in ("CHANGES_REQUIRED", "BLOCKED") else []
        result = resp.get(
            "result_override",
            {
                "status": st_val,
                "summary": f"{role} {run_id}",
                "covered_requirements": cov,
                "findings": fnd,
                "changes": ch,
            },
        )
        write_json(out / "result.json", result)
        res_sha = file_sha256(out / "result.json")

        snapshot: dict[str, Any] = {
            "source_head": HEAD,
            "task": json.loads((root / "task.json").read_text()),
            "sources": {"src.py": {"sha256": src_sha, "content": "# orig\n"}},
        }
        if proposal is not None:
            snapshot["proposal"] = proposal.read_text()
        if test_evidence is not None:
            snapshot["test_evidence"] = json.loads(Path(test_evidence).read_text())
        if "task_override" in resp:
            snapshot.update(resp["task_override"])
        write_json(out / "task.json", snapshot)

        st: dict[str, Any] = {
            "run_id": run_id,
            "role": role,
            "head": HEAD,
            "model": WORKER_MODEL if role == "worker" else MODEL,
            "result_sha256": res_sha,
            "conversation_id": resp.get("conv_id", f"conv-{run_id}"),
            "state": st_val,
        }
        if proposal is not None:
            pst = json.loads((proposal.parent / "status.json").read_text())
            st.update(
                input_worker_run_id=pst["run_id"],
                input_worker_conversation_id=pst["conversation_id"],
                input_proposal_sha256=pst["result_sha256"],
            )
        if feedback is not None:
            st["input_feedback_sha256"] = file_sha256(feedback)
        if test_evidence is not None:
            st["input_test_evidence_sha256"] = file_sha256(test_evidence)
        if "status_override" in resp:
            st.update(resp["status_override"])
        write_json(out / "status.json", st)
        return out

    return mock_run, calls


def make_candidate_tester(receipt_templates: list[dict[str, Any]]):
    calls: list[dict[str, Any]] = []

    def mock_run_candidate_tests(
        root: str | Path,
        task: Mapping[str, Any],
        worker_result: Mapping[str, Any],
        worker_sha: str,
        timeout: int = 1200,
    ) -> dict[str, Any]:
        call_idx = len(calls)
        calls.append({
            "root": root,
            "task": task,
            "worker_result": worker_result,
            "worker_sha": worker_sha,
            "timeout": timeout,
        })
        template = receipt_templates[call_idx]
        receipt = dict(template)
        receipt["proposal_sha256"] = worker_sha
        return validate_test_receipt(receipt, worker_sha)

    return mock_run_candidate_tests, calls


class TestPipelinePreReviewGate(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.task_path = self.root / "task.json"
        self.src_file = self.root / "src.py"
        self.src_file.write_text("# orig\n")
        self.task = {
            "task_id": "T-PRE-REVIEW-GATE",
            "goal": "Gate regression tests",
            "context_files": ["src.py"],
            "allowed_files": ["src.py"],
            "requirements": ["R1"],
            "test_selectors": [DEFAULT_SELECTOR],
        }
        write_json(self.task_path, self.task)
        self.reserve_record = {
            "schema_version": 1,
            "fingerprint": "0" * 64,
            "source_sha256": "0" * 64,
            "stage_id": "P1-00b",
            "gap_reference": "docs/refactor/two-server/PROPOSAL_AUTOMATION.md#semantic-task-dedup",
            "scenario_key": "automation_pre_review_gate",
            "task_id": "T-PRE-REVIEW-GATE",
            "run_prefix": "run",
            "state": "RESERVED",
            "created_at": 1000.0,
        }
        self.reserve_semantic_patcher = patch.object(
            pipeline,
            "reserve_semantic_attempt",
            return_value=self.reserve_record,
            create=True,
        )
        self.reserve_semantic_patcher.start()
        self.state = {
            "state": "TASK_BLOCKED",
            "task_id": "",
            "source_head": "",
            "final_worker_run_id": None,
            "final_reviewer_run_id": None,
            "proposal_sha256": None,
            "changed_paths": [],
            "proposal_path": None,
            "reviewer_path": None,
            "correction_count": 0,
            "blockers": [],
            "pre_review_test_path": None,
            "pre_review_test_sha256": None,
            "pre_review_test_state": None,
            "semantic_task_fingerprint": None,
        }

    def tearDown(self):
        self.reserve_semantic_patcher.stop()
        self.tmp_dir.cleanup()

    def test_fail_then_pass_orchestration(self):
        # Worker-0 fails candidate tests; Worker-1 fixes and passes tests; Reviewer-1 verifies.
        receipt0 = build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail="FAILED tests/test_two_site_pipeline_pre_review_gate.py::test_alpha - AssertionError: gate check failed\n",
            stderr_tail="",
        )
        receipt1 = build_test_receipt(
            state="PASSED",
            returncode=0,
            stdout_tail="1 passed in 0.04s\n",
            stderr_tail="",
        )
        runner, runner_calls = make_runner(self.root, [
            {"content": "# v1\n"},
            {"content": "# v2\n"},
            {"status": "VERIFIED"},
        ])
        candidate_tester, candidate_calls = make_candidate_tester([receipt0, receipt1])

        with patch.object(pipeline, "git", side_effect=fake_git), \
             patch.object(pipeline, "runner_run", side_effect=runner), \
             patch.object(pipeline, "run_candidate_tests", side_effect=candidate_tester):
            res = run_pipeline(self.root, self.task_path, "run-f2p", 300, None, self.state)

        # Verify call roles and sequence
        self.assertEqual(len(runner_calls), 3)
        self.assertEqual(runner_calls[0][:2], ("worker", "run-f2p-worker-0"))
        self.assertEqual(runner_calls[1][:2], ("worker", "run-f2p-worker-1"))
        self.assertEqual(runner_calls[2][:2], ("verifier", "run-f2p-reviewer-1"))

        # Reviewer-0 must never be invoked
        self.assertNotIn("run-f2p-reviewer-0", [c[1] for c in runner_calls])
        self.assertNotIn("verifier", [c[0] for c in runner_calls[:2]])

        w0_dir = self.root / ".local/two-site-refactor/proposals/run-f2p-worker-0"
        w0_sha = file_sha256(w0_dir / "result.json")
        w0_st = json.loads((w0_dir / "status.json").read_text())

        # Worker-0 pre-review test receipt on disk
        rcpt0_path = self.root / ".local/two-site-refactor/pipeline/run-f2p/pre-review-tests-0.json"
        self.assertTrue(rcpt0_path.exists())
        rcpt0_data = json.loads(rcpt0_path.read_text())
        self.assertEqual(rcpt0_data["state"], "FAILED")
        self.assertEqual(rcpt0_data["returncode"], 1)
        self.assertEqual(rcpt0_data["proposal_sha256"], w0_sha)
        self.assertEqual(set(rcpt0_data.keys()), EXPECTED_RECEIPT_KEYS)
        self.assertEqual(len(rcpt0_data), 10)

        # Feedback-correction-1 check
        fb1_path = self.root / ".local/two-site-refactor/pipeline/run-f2p/feedback-correction-1.json"
        self.assertTrue(fb1_path.exists())
        fb1_data = json.loads(fb1_path.read_text())
        self.assertEqual(fb1_data["reviewer"], "test-gate")
        self.assertEqual(fb1_data["reviewed_proposal_sha256"], w0_sha)
        self.assertIsInstance(fb1_data["findings"], list)
        self.assertTrue(any("exit code 1" in f for f in fb1_data["findings"]))
        self.assertIsInstance(fb1_data["test_evidence"], list)
        self.assertTrue(any("returncode: 1" in e for e in fb1_data["test_evidence"]))
        self.assertTrue(any("stdout_tail:" in e for e in fb1_data["test_evidence"]))
        for ev in fb1_data["test_evidence"]:
            self.assertLessEqual(len(ev.encode("utf-8")), 8192)
        fb1_sha = file_sha256(fb1_path)

        # Worker-1 input binding
        w1_dir = self.root / ".local/two-site-refactor/proposals/run-f2p-worker-1"
        w1_sha = file_sha256(w1_dir / "result.json")
        w1_st = json.loads((w1_dir / "status.json").read_text())
        self.assertNotEqual(w0_sha, w1_sha)
        self.assertEqual(w1_st["input_worker_run_id"], "run-f2p-worker-0")
        self.assertEqual(w1_st["input_proposal_sha256"], w0_sha)
        self.assertEqual(w1_st["input_worker_conversation_id"], w0_st["conversation_id"])
        self.assertEqual(w1_st["input_feedback_sha256"], fb1_sha)
        self.assertNotEqual(w1_st["conversation_id"], w0_st["conversation_id"])
        self.assertEqual(runner_calls[1][3], fb1_path)
        self.assertEqual(runner_calls[1][4], w0_dir / "result.json")

        # Worker-1 pre-review test receipt on disk
        rcpt1_path = self.root / ".local/two-site-refactor/pipeline/run-f2p/pre-review-tests-1.json"
        self.assertTrue(rcpt1_path.exists())
        rcpt1_data = json.loads(rcpt1_path.read_text())
        self.assertEqual(rcpt1_data["state"], "PASSED")
        self.assertEqual(rcpt1_data["returncode"], 0)
        self.assertEqual(rcpt1_data["proposal_sha256"], w1_sha)
        self.assertEqual(set(rcpt1_data.keys()), EXPECTED_RECEIPT_KEYS)
        self.assertEqual(len(rcpt1_data), 10)

        # Reviewer-1 received PASSED receipt path as test evidence
        r1_dir = self.root / ".local/two-site-refactor/proposals/run-f2p-reviewer-1"
        r1_st = json.loads((r1_dir / "status.json").read_text())
        self.assertEqual(runner_calls[2][6], rcpt1_path)
        self.assertEqual(r1_st["input_test_evidence_sha256"], file_sha256(rcpt1_path))

        # Final pipeline state
        self.assertEqual(res["state"], "READY_FOR_CODEX")
        self.assertEqual(res["correction_count"], 1)
        self.assertEqual(res["pre_review_test_state"], "PASSED")
        self.assertEqual(res["pre_review_test_path"], rcpt1_path.relative_to(self.root).as_posix())
        self.assertEqual(res["pre_review_test_sha256"], file_sha256(rcpt1_path))
        self.assertEqual(res["final_worker_run_id"], "run-f2p-worker-1")
        self.assertEqual(res["final_reviewer_run_id"], "run-f2p-reviewer-1")
        self.assertEqual(res["proposal_sha256"], w1_sha)
        self.assertEqual(res["blockers"], [])

    def test_repeated_failure_signature_aborts_immediately(self):
        # Two identical failure receipts cause ProposalError immediately after candidate test 1
        rcpt0 = build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail="FAILED test_gate - NameError: name 'x' is not defined\n",
            stderr_tail="",
        )
        rcpt1 = build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail="FAILED test_gate - NameError: name 'x' is not defined\n",
            stderr_tail="",
        )
        runner, runner_calls = make_runner(self.root, [
            {"content": "# v1\n"},
            {"content": "# v2\n"},
        ])
        candidate_tester, candidate_calls = make_candidate_tester([rcpt0, rcpt1])

        with patch.object(pipeline, "git", side_effect=fake_git), \
             patch.object(pipeline, "runner_run", side_effect=runner), \
             patch.object(pipeline, "run_candidate_tests", side_effect=candidate_tester):
            with self.assertRaises(ProposalError) as ctx:
                run_pipeline(self.root, self.task_path, "run-rep", 300, None, self.state)

        err_msg = str(ctx.exception)
        self.assertIn("repeated test failure signature", err_msg)
        expected_sig = pipeline.test_failure_signature(rcpt0)
        self.assertIn(expected_sig, err_msg)

        # Exactly 2 worker runs, no Reviewer invocation, and no Worker-2 invocation
        self.assertEqual(len(runner_calls), 2)
        self.assertEqual(runner_calls[0][:2], ("worker", "run-rep-worker-0"))
        self.assertEqual(runner_calls[1][:2], ("worker", "run-rep-worker-1"))
        self.assertFalse(any(c[0] == "verifier" for c in runner_calls))
        self.assertFalse(any(c[1] == "run-rep-worker-2" for c in runner_calls))
        self.assertEqual(len(candidate_calls), 2)

        # Pre-review receipts 0 and 1 exist; receipt 2 does not exist
        rcpt0_path = self.root / ".local/two-site-refactor/pipeline/run-rep/pre-review-tests-0.json"
        rcpt1_path = self.root / ".local/two-site-refactor/pipeline/run-rep/pre-review-tests-1.json"
        rcpt2_path = self.root / ".local/two-site-refactor/pipeline/run-rep/pre-review-tests-2.json"
        self.assertTrue(rcpt0_path.exists())
        self.assertTrue(rcpt1_path.exists())
        self.assertFalse(rcpt2_path.exists())

        # Feedback 1 exists, but feedback 2 was not created because abort was immediate
        fb1_path = self.root / ".local/two-site-refactor/pipeline/run-rep/feedback-correction-1.json"
        fb2_path = self.root / ".local/two-site-refactor/pipeline/run-rep/feedback-correction-2.json"
        self.assertTrue(fb1_path.exists())
        self.assertFalse(fb2_path.exists())

        self.assertNotEqual(self.state["state"], "READY_FOR_CODEX")

    def test_shared_correction_exhaustion_on_three_distinct_failures(self):
        # Three distinct FAILED receipts trigger correction exhaustion after Worker-0, 1, 2
        rcpt0 = build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail="FAILED failure-phase-0-syntax-error\n",
            stderr_tail="",
        )
        rcpt1 = build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail="FAILED failure-phase-1-type-error\n",
            stderr_tail="",
        )
        rcpt2 = build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail="FAILED failure-phase-2-assertion-error\n",
            stderr_tail="",
        )

        sig0 = pipeline.test_failure_signature(rcpt0)
        sig1 = pipeline.test_failure_signature(rcpt1)
        sig2 = pipeline.test_failure_signature(rcpt2)
        self.assertEqual(len({sig0, sig1, sig2}), 3)

        runner, runner_calls = make_runner(self.root, [
            {"content": "# v1\n"},
            {"content": "# v2\n"},
            {"content": "# v3\n"},
        ])
        candidate_tester, candidate_calls = make_candidate_tester([rcpt0, rcpt1, rcpt2])

        with patch.object(pipeline, "git", side_effect=fake_git), \
             patch.object(pipeline, "runner_run", side_effect=runner), \
             patch.object(pipeline, "run_candidate_tests", side_effect=candidate_tester):
            with self.assertRaises(ProposalError) as ctx:
                run_pipeline(self.root, self.task_path, "run-exh", 300, None, self.state)

        self.assertIn("maximum 2 corrections exceeded", str(ctx.exception))

        # Exactly 3 worker runs, no Reviewer invocation
        self.assertEqual(len(runner_calls), 3)
        self.assertEqual(runner_calls[0][:2], ("worker", "run-exh-worker-0"))
        self.assertEqual(runner_calls[1][:2], ("worker", "run-exh-worker-1"))
        self.assertEqual(runner_calls[2][:2], ("worker", "run-exh-worker-2"))
        self.assertFalse(any(c[0] == "verifier" for c in runner_calls))
        self.assertEqual(len(candidate_calls), 3)

        self.assertNotEqual(self.state["state"], "READY_FOR_CODEX")
        self.assertEqual(self.state["correction_count"], 2)

        # Verify receipts 0, 1, 2 exist and are SHA-bound to their respective proposals
        w0_sha = file_sha256(self.root / ".local/two-site-refactor/proposals/run-exh-worker-0/result.json")
        w1_sha = file_sha256(self.root / ".local/two-site-refactor/proposals/run-exh-worker-1/result.json")
        w2_sha = file_sha256(self.root / ".local/two-site-refactor/proposals/run-exh-worker-2/result.json")
        self.assertEqual(len({w0_sha, w1_sha, w2_sha}), 3)

        rcpt0_path = self.root / ".local/two-site-refactor/pipeline/run-exh/pre-review-tests-0.json"
        rcpt1_path = self.root / ".local/two-site-refactor/pipeline/run-exh/pre-review-tests-1.json"
        rcpt2_path = self.root / ".local/two-site-refactor/pipeline/run-exh/pre-review-tests-2.json"
        self.assertTrue(rcpt0_path.exists())
        self.assertTrue(rcpt1_path.exists())
        self.assertTrue(rcpt2_path.exists())

        d0 = json.loads(rcpt0_path.read_text())
        d1 = json.loads(rcpt1_path.read_text())
        d2 = json.loads(rcpt2_path.read_text())
        self.assertEqual(d0["proposal_sha256"], w0_sha)
        self.assertEqual(d1["proposal_sha256"], w1_sha)
        self.assertEqual(d2["proposal_sha256"], w2_sha)
        self.assertEqual(file_sha256(rcpt0_path), hashlib.sha256(rcpt0_path.read_bytes()).hexdigest())
        self.assertEqual(file_sha256(rcpt1_path), hashlib.sha256(rcpt1_path.read_bytes()).hexdigest())
        self.assertEqual(file_sha256(rcpt2_path), hashlib.sha256(rcpt2_path.read_bytes()).hexdigest())

    def test_receipt_fixtures_ten_key_schema_and_signature_behavior(self):
        # Verify exact 10-key schema and failure signature hashing logic
        rcpt_passed = build_test_receipt(state="PASSED", returncode=0)
        self.assertEqual(len(rcpt_passed), 10)
        self.assertEqual(set(rcpt_passed.keys()), EXPECTED_RECEIPT_KEYS)

        rcpt_failed = build_test_receipt(state="FAILED", returncode=2)
        self.assertEqual(len(rcpt_failed), 10)
        self.assertEqual(set(rcpt_failed.keys()), EXPECTED_RECEIPT_KEYS)

        # Distinct stdout_tail values produce distinct failure signatures
        r_alpha = build_test_receipt(state="FAILED", returncode=1, stdout_tail="error alpha\n")
        r_beta = build_test_receipt(state="FAILED", returncode=1, stdout_tail="error beta\n")
        sig_alpha = pipeline.test_failure_signature(r_alpha)
        sig_beta = pipeline.test_failure_signature(r_beta)
        self.assertNotEqual(sig_alpha, sig_beta)

        # Different proposals with identical failure fields produce identical failure signature
        sha_1 = "1" * 64
        sha_2 = "2" * 64
        r_p1 = build_test_receipt(proposal_sha=sha_1, state="FAILED", returncode=1, stdout_tail="common error\n")
        r_p2 = build_test_receipt(proposal_sha=sha_2, state="FAILED", returncode=1, stdout_tail="common error\n")
        self.assertEqual(pipeline.test_failure_signature(r_p1), pipeline.test_failure_signature(r_p2))

        # test_gate_feedback validation
        fb = pipeline.test_gate_feedback(r_p1, sha_1)
        self.assertEqual(fb["reviewer"], "test-gate")
        self.assertEqual(fb["reviewed_proposal_sha256"], sha_1)
        self.assertGreaterEqual(len(fb["findings"]), 1)
        self.assertIn("exit code 1", fb["findings"][0])
        self.assertIsInstance(fb["test_evidence"], list)
        for ev in fb["test_evidence"]:
            self.assertLessEqual(len(ev.encode("utf-8")), 8192)


if __name__ == "__main__":
    unittest.main()
