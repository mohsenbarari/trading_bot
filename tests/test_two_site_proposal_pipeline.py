#!/usr/bin/env python3
"""Offline unit tests for two-site proposal pipeline orchestration."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import scripts.two_site_proposal_pipeline as pipeline
    from scripts.two_site_proposal_pipeline import (
        BRANCH, ProposalError, normalize_finding, run_pipeline,
    )
    from scripts.two_site_proposal_runner import MODEL, file_sha256, write_json
except ModuleNotFoundError:
    import two_site_proposal_pipeline as pipeline  # type: ignore[no-redef]
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        BRANCH, ProposalError, normalize_finding, run_pipeline,
    )
    from two_site_proposal_runner import MODEL, file_sha256, write_json  # type: ignore[no-redef]

HEAD = "a" * 40


def fake_git(_root: Path, *args: str) -> str:
    cmd = " ".join(args)
    if "branch --show-current" in cmd:
        return BRANCH
    if "rev-parse HEAD" in cmd:
        return HEAD
    if "status --porcelain" in cmd:
        return ""
    return "b" * 40 if "show-ref" in cmd else ""


def make_runner(root: Path, responses: list[dict]):
    calls = []
    src_sha = file_sha256(root / "src.py")

    def mock_run(_r, _t, role, run_id, timeout, feedback=None, proposal=None, cli=None):
        calls.append((role, run_id, timeout, feedback, proposal, cli))
        resp = responses[len(calls) - 1]
        out = root / ".local/two-site-refactor/proposals" / run_id
        out.mkdir(parents=True, exist_ok=True)
        st_val = resp.get("status", "PROPOSAL_READY" if role == "worker" else "VERIFIED")
        ch = ([{"path": "src.py", "original_sha256": src_sha, "content": resp.get("content", f"# c {run_id}\n")}]
              if role == "worker" and st_val == "PROPOSAL_READY" else [])
        cov = [1] if st_val in ("PROPOSAL_READY", "VERIFIED") else []
        fnd = resp.get("findings", []) if st_val in ("CHANGES_REQUIRED", "BLOCKED") else []
        result = resp.get("result_override", {"status": st_val, "summary": f"{role} {run_id}",
                                              "covered_requirements": cov, "findings": fnd, "changes": ch})
        write_json(out / "result.json", result)
        res_sha = file_sha256(out / "result.json")

        snapshot = {"source_head": HEAD, "task": json.loads((root / "task.json").read_text()),
                    "sources": {"src.py": {"sha256": src_sha, "content": "# orig\n"}}}
        if proposal is not None:
            snapshot["proposal"] = proposal.read_text()
        if "task_override" in resp:
            snapshot.update(resp["task_override"])
        write_json(out / "task.json", snapshot)

        st = {"run_id": run_id, "role": role, "head": HEAD, "model": MODEL,
              "result_sha256": res_sha, "conversation_id": resp.get("conv_id", f"conv-{run_id}"),
              "state": st_val}
        if proposal is not None:
            pst = json.loads((proposal.parent / "status.json").read_text())
            st.update(input_worker_run_id=pst["run_id"], input_worker_conversation_id=pst["conversation_id"],
                      input_proposal_sha256=pst["result_sha256"])
        if feedback is not None:
            st["input_feedback_sha256"] = file_sha256(feedback)
        if "status_override" in resp:
            st.update(resp["status_override"])
        write_json(out / "status.json", st)
        return out

    return mock_run, calls


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.task_path = self.root / "task.json"
        self.src_file = self.root / "src.py"
        self.src_file.write_text("# orig\n")
        self.task = {"task_id": "T1", "goal": "G", "context_files": ["src.py"],
                     "allowed_files": ["src.py"], "requirements": ["R1"], "test_selectors": ["test.py"]}
        write_json(self.task_path, self.task)
        self.state = {"state": "TASK_BLOCKED", "task_id": "", "source_head": "",
                      "final_worker_run_id": None, "final_reviewer_run_id": None,
                      "proposal_sha256": None, "changed_paths": [], "proposal_path": None,
                      "reviewer_path": None, "correction_count": 0, "blockers": []}

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_happy_path(self):
        runner, calls = make_runner(self.root, [{"content": "# v1\n"}, {"status": "VERIFIED"}])
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=runner):
            res = run_pipeline(self.root, self.task_path, "run-happy", 300, None, self.state)
        w_dir = self.root / ".local/two-site-refactor/proposals/run-happy-worker-0"
        r_dir = self.root / ".local/two-site-refactor/proposals/run-happy-reviewer-0"
        self.assertEqual(calls, [
            ("worker", "run-happy-worker-0", 300, None, None, None),
            ("verifier", "run-happy-reviewer-0", 300, None, w_dir / "result.json", None),
        ])
        w_st = json.loads((w_dir / "status.json").read_text())
        r_st = json.loads((r_dir / "status.json").read_text())
        self.assertNotEqual(w_st["conversation_id"], r_st["conversation_id"])
        w_sha = file_sha256(w_dir / "result.json")
        expected = {
            "state": "READY_FOR_CODEX", "task_id": "T1", "source_head": HEAD,
            "final_worker_run_id": "run-happy-worker-0", "final_reviewer_run_id": "run-happy-reviewer-0",
            "proposal_sha256": w_sha, "changed_paths": ["src.py"],
            "proposal_path": ".local/two-site-refactor/proposals/run-happy-worker-0/result.json",
            "reviewer_path": ".local/two-site-refactor/proposals/run-happy-reviewer-0/result.json",
            "correction_count": 0, "blockers": [],
        }
        self.assertEqual(len(res), 11)
        self.assertEqual(res, expected)
        self.assertFalse(Path(res["proposal_path"]).is_absolute() or Path(res["reviewer_path"]).is_absolute())
        self.assertEqual(self.src_file.read_text(), "# orig\n")
        self.assertEqual(json.loads(self.task_path.read_text()), self.task)

    def test_correction_flow(self):
        runner, calls = make_runner(self.root, [
            {"content": "# v1\n"},
            {"status": "CHANGES_REQUIRED", "findings": ["fix bug"]},
            {"content": "# v2\n"},
            {"status": "VERIFIED"},
        ])
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=runner):
            res = run_pipeline(self.root, self.task_path, "run-corr", 300, None, self.state)
        self.assertEqual(res["state"], "READY_FOR_CODEX")
        self.assertEqual(res["correction_count"], 1)
        self.assertEqual(len(calls), 4)

        w0_dir = self.root / ".local/two-site-refactor/proposals/run-corr-worker-0"
        w0_sha = file_sha256(w0_dir / "result.json")
        w0_st = json.loads((w0_dir / "status.json").read_text())
        fb_path = self.root / ".local/two-site-refactor/pipeline/run-corr/feedback-correction-1.json"
        self.assertTrue(fb_path.exists())
        fb_data = json.loads(fb_path.read_bytes().decode("utf-8"))
        self.assertEqual(fb_data, {
            "reviewer": "gemini", "reviewed_proposal_sha256": w0_sha,
            "findings": ["fix bug"], "test_evidence": [],
        })
        self.assertEqual(len(fb_data), 4)

        self.assertEqual(calls[2], ("worker", "run-corr-worker-1", 300, fb_path, w0_dir / "result.json", None))
        w1_dir = self.root / ".local/two-site-refactor/proposals/run-corr-worker-1"
        w1_sha = file_sha256(w1_dir / "result.json")
        w1_st = json.loads((w1_dir / "status.json").read_text())
        self.assertEqual((w1_st["input_worker_run_id"], w1_st["input_proposal_sha256"],
                          w1_st["input_worker_conversation_id"], w1_st["input_feedback_sha256"]),
                         ("run-corr-worker-0", w0_sha, w0_st["conversation_id"], file_sha256(fb_path)))
        self.assertNotEqual(w1_st["conversation_id"], w0_st["conversation_id"])
        self.assertEqual(json.loads((w1_dir / "task.json").read_text())["proposal"], (w0_dir / "result.json").read_text())

        self.assertEqual(calls[3], ("verifier", "run-corr-reviewer-1", 300, None, w1_dir / "result.json", None))
        r1_dir = self.root / ".local/two-site-refactor/proposals/run-corr-reviewer-1"
        r1_st = json.loads((r1_dir / "status.json").read_text())
        self.assertEqual((r1_st["input_worker_run_id"], r1_st["input_proposal_sha256"],
                          r1_st["input_worker_conversation_id"]),
                         ("run-corr-worker-1", w1_sha, w1_st["conversation_id"]))
        self.assertEqual(json.loads((r1_dir / "task.json").read_text())["proposal"], (w1_dir / "result.json").read_text())
        self.assertTrue(r1_st["conversation_id"] not in {w1_st["conversation_id"], w0_st["conversation_id"]})

    def test_two_corrections_allowed_and_distinct_findings(self):
        runner, calls = make_runner(self.root, [
            {"content": "# v1\n"},
            {"status": "CHANGES_REQUIRED", "findings": ["finding a"]},
            {"content": "# v2\n"},
            {"status": "CHANGES_REQUIRED", "findings": ["distinct finding b"]},
            {"content": "# v3\n"},
            {"status": "VERIFIED"},
        ])
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=runner):
            res = run_pipeline(self.root, self.task_path, "run-2corr", 300, None, self.state)
        self.assertEqual(res["state"], "READY_FOR_CODEX")
        self.assertEqual(res["correction_count"], 2)
        self.assertEqual(res["final_worker_run_id"], "run-2corr-worker-2")
        self.assertEqual(res["final_reviewer_run_id"], "run-2corr-reviewer-2")
        self.assertEqual(len(calls), 6)

    def test_correction_exhaustion(self):
        runner, calls = make_runner(self.root, [
            {"content": "# v1\n"}, {"status": "CHANGES_REQUIRED", "findings": ["e1"]},
            {"content": "# v2\n"}, {"status": "CHANGES_REQUIRED", "findings": ["e2"]},
            {"content": "# v3\n"}, {"status": "CHANGES_REQUIRED", "findings": ["e3"]},
        ])
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=runner):
            with self.assertRaises(ProposalError) as ctx:
                run_pipeline(self.root, self.task_path, "run-exh", 300, None, self.state)
        self.assertIn("maximum 2 corrections exceeded", str(ctx.exception))
        self.assertEqual(len(calls), 6)
        self.assertNotEqual(self.state["state"], "READY_FOR_CODEX")

    def test_unchanged_proposal_and_recurring_findings(self):
        runner1, _ = make_runner(self.root, [
            {"content": "# v1\n"}, {"status": "CHANGES_REQUIRED", "findings": ["f1"]},
            {"content": "# v1\n"},
        ])
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=runner1):
            with self.assertRaises(ProposalError) as ctx1:
                run_pipeline(self.root, self.task_path, "run-unchg", 300, None, dict(self.state))
        self.assertIn("unchanged correction proposal", str(ctx1.exception))

        runner2, calls2 = make_runner(self.root, [
            {"content": "# v1\n"},
            {"status": "CHANGES_REQUIRED", "findings": ["Fix Typo In Header"]},
            {"content": "# v2\n"},
            {"status": "CHANGES_REQUIRED", "findings": ["  fix   typo   in   header  "]},
        ])
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=runner2):
            with self.assertRaises(ProposalError) as ctx2:
                run_pipeline(self.root, self.task_path, "run-recur", 300, None, dict(self.state))
        self.assertIn("recurring normalized findings", str(ctx2.exception))
        self.assertEqual(len(calls2), 4)
        self.assertEqual(normalize_finding("  Fix \t Typo \n In   Header  "), "fix typo in header")

    def test_freshness_and_blocked_states(self):
        r_conv, _ = make_runner(self.root, [{"conv_id": "c-dup"}, {"status": "VERIFIED", "conv_id": "c-dup"}])
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=r_conv):
            with self.assertRaises(ProposalError) as ctx:
                run_pipeline(self.root, self.task_path, "run-c1", 300, None, dict(self.state))
        self.assertIn("reused conversation_id", str(ctx.exception))

        (self.root / ".local/two-site-refactor/proposals/run-dup-worker-0").mkdir(parents=True)
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=r_conv):
            with self.assertRaises(ProposalError) as ctx:
                run_pipeline(self.root, self.task_path, "run-dup", 300, None, dict(self.state))
        self.assertIn("worker run ID already exists", str(ctx.exception))

        r_bind, _ = make_runner(self.root, [{"content": "# v1\n"}, {"status": "VERIFIED", "status_override": {"input_proposal_sha256": "0" * 64}}])
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=r_bind):
            with self.assertRaises(ProposalError) as ctx:
                run_pipeline(self.root, self.task_path, "run-bind", 300, None, dict(self.state))
        self.assertIn("input worker binding mismatch", str(ctx.exception))

        r_fb, _ = make_runner(self.root, [{"content": "# v1\n"}, {"status": "CHANGES_REQUIRED", "findings": ["fix"]},
                                           {"content": "# v2\n", "status_override": {"input_feedback_sha256": "0" * 64}}])
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=r_fb):
            with self.assertRaises(ProposalError) as ctx:
                run_pipeline(self.root, self.task_path, "run-fb", 300, None, dict(self.state))
        self.assertIn("feedback SHA mismatch", str(ctx.exception))

        for status_val, role_val, err_text in (("BLOCKED", "worker", "worker did not complete PROPOSAL_READY"),
                                              ("BLOCKED", "verifier", "reviewer returned invalid state BLOCKED")):
            resps = ([{"status": status_val, "findings": ["b"]}] if role_val == "worker"
                     else [{"content": "# v1\n"}, {"status": status_val, "findings": ["b"]}])
            r_blk, _ = make_runner(self.root, resps)
            st_blk = dict(self.state)
            with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=r_blk):
                with self.assertRaises(ProposalError) as ctx:
                    run_pipeline(self.root, self.task_path, f"run-blk-{role_val}", 300, None, st_blk)
            self.assertIn(err_text, str(ctx.exception))
            self.assertNotEqual(st_blk["state"], "READY_FOR_CODEX")

        r_mal, _ = make_runner(self.root, [{"content": "# v1\n"}, {"status_override": {"state": "MALFORMED"},
                                                                   "result_override": {"status": "MALFORMED", "summary": "mal",
                                                                                       "covered_requirements": [1], "findings": [], "changes": []}}])
        st_m = dict(self.state)
        with patch.object(pipeline, "git", side_effect=fake_git), patch.object(pipeline, "runner_run", side_effect=r_mal):
            with self.assertRaises(ProposalError):
                run_pipeline(self.root, self.task_path, "run-mal", 300, None, st_m)
        self.assertNotEqual(st_m["state"], "READY_FOR_CODEX")


if __name__ == "__main__":
    unittest.main()
