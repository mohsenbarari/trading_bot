"""Tests for validate_run evidence and conversation binding."""
from pathlib import Path
import tempfile
import unittest

try:
    from scripts.two_site_proposal_pipeline import (
        MODEL, ProposalError, file_sha256, validate_run, write_json,
    )
except ModuleNotFoundError:
    from two_site_proposal_pipeline import (
        MODEL, ProposalError, file_sha256, validate_run, write_json,
    )

HEAD = "7b3c9ce8bf402db10474faf10556a3fc48c7c151"
TASK = {"requirements": ["r1"], "allowed_files": ["f.py"]}
SOURCES = {"f.py": {"sha256": "s0", "content": "c0"}}
WORKER_RES = {
    "status": "PROPOSAL_READY", "summary": "ok", "covered_requirements": [1],
    "findings": [], "changes": [{"path": "f.py", "original_sha256": "s0", "content": "c1"}],
}
REVIEWER_RES = {
    "status": "VERIFIED", "summary": "ok", "covered_requirements": [1],
    "findings": [], "changes": [],
}


class TestPipelineBinding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.w_dir = self.root / "w"
        self.r_dir = self.root / "r"
        self.seen = set()

        self.w_st = {
            "run_id": "w0", "role": "worker", "head": HEAD, "model": MODEL,
            "state": "PROPOSAL_READY", "conversation_id": "conv-w",
        }
        self.w_sn = {"source_head": HEAD, "task": TASK, "sources": SOURCES}
        self.w_sha, self.w_txt = self._write_run(self.w_dir, self.w_st, self.w_sn, WORKER_RES)
        self.w_st["result_sha256"] = self.w_sha

        self.prior = ("w0", self.w_sha, self.w_txt, "conv-w")
        self.r_st = {
            "run_id": "r0", "role": "verifier", "head": HEAD, "model": MODEL,
            "state": "VERIFIED", "conversation_id": "conv-r",
            "input_worker_run_id": "w0", "input_proposal_sha256": self.w_sha,
            "input_worker_conversation_id": "conv-w", "input_feedback_sha256": "fb0",
        }
        self.r_sn = {"source_head": HEAD, "task": TASK, "sources": SOURCES, "proposal": self.w_txt}
        self.r_sha, self.r_txt = self._write_run(self.r_dir, self.r_st, self.r_sn, REVIEWER_RES)
        self.r_st["result_sha256"] = self.r_sha

    def _write_run(self, run_dir, status, snapshot, result):
        run_dir.mkdir(parents=True, exist_ok=True)
        write_json(run_dir / "result.json", result)
        sha = file_sha256(run_dir / "result.json")
        st = dict(status)
        st.setdefault("result_sha256", sha)
        write_json(run_dir / "status.json", st)
        write_json(run_dir / "task.json", snapshot)
        return sha, (run_dir / "result.json").read_text()

    def test_valid_baselines_accepted_and_distinct_convs(self):
        w_res, w_st, w_sha, _ = validate_run(
            self.root, self.w_dir, "worker", "w0", HEAD, TASK, SOURCES, self.seen,
        )
        self.assertEqual(w_res["status"], "PROPOSAL_READY")
        self.assertEqual(w_sha, self.w_sha)
        self.assertEqual(self.seen, {"conv-w"})

        r_res, r_st, r_sha, _ = validate_run(
            self.root, self.r_dir, "verifier", "r0", HEAD, TASK, SOURCES, self.seen,
            prior_worker=self.prior, expected_feedback_sha="fb0",
        )
        self.assertEqual(r_res["status"], "VERIFIED")
        self.assertEqual(r_sha, self.r_sha)
        self.assertEqual(self.seen, {"conv-w", "conv-r"})
        self.assertNotEqual(w_st["conversation_id"], r_st["conversation_id"])

    def test_table_driven_mutations(self):
        cases = [
            ("wrong run ID", "w", "status", "run_id", "bad", "metadata or snapshot mismatch"),
            ("wrong role", "w", "status", "role", "verifier", "metadata or snapshot mismatch"),
            ("wrong model", "w", "status", "model", "bad", "metadata or snapshot mismatch"),
            ("wrong state", "w", "status", "state", "BLOCKED", "worker did not complete PROPOSAL_READY"),
            ("wrong result SHA", "w", "status", "result_sha256", "bad", "metadata or snapshot mismatch"),
            ("wrong source HEAD status", "w", "status", "head", "bad", "metadata or snapshot mismatch"),
            ("wrong source HEAD snapshot", "w", "snapshot", "source_head", "bad", "metadata or snapshot mismatch"),
            ("wrong task", "w", "snapshot", "task", {}, "metadata or snapshot mismatch"),
            ("wrong sources", "w", "snapshot", "sources", {}, "metadata or snapshot mismatch"),
            ("wrong prior Worker run ID", "r", "status", "input_worker_run_id", "bad", "input worker binding mismatch"),
            ("wrong proposal SHA", "r", "status", "input_proposal_sha256", "bad", "input worker binding mismatch"),
            ("wrong Worker conversation binding", "r", "status", "input_worker_conversation_id", "bad", "input worker binding mismatch"),
            ("wrong proposal snapshot", "r", "snapshot", "proposal", "bad", "input worker binding mismatch"),
            ("wrong feedback SHA", "r", "status", "input_feedback_sha256", "bad", "feedback SHA mismatch"),
            ("missing conversation", "w", "status", "conversation_id", "", "conversation_id missing or empty"),
            ("reused conversation", "w", "status", "conversation_id", "c-seen", "reused conversation_id"),
        ]
        for idx, (name, role_key, file_type, field, val, err) in enumerate(cases):
            with self.subTest(case=name):
                mut_dir = self.root / f"m_{idx}"
                is_w = role_key == "w"
                st = dict(self.w_st if is_w else self.r_st)
                sn = dict(self.w_sn if is_w else self.r_sn)
                res = WORKER_RES if is_w else REVIEWER_RES
                target = st if file_type == "status" else sn
                target[field] = val
                self._write_run(mut_dir, st, sn, res)

                seen = {"c-seen"} if name == "reused conversation" else set()
                prior = None if is_w else self.prior
                fb = None if is_w else "fb0"
                run_id = "w0" if is_w else "r0"
                role = "worker" if is_w else "verifier"

                with self.assertRaises(ProposalError) as ctx:
                    validate_run(self.root, mut_dir, role, run_id, HEAD, TASK, SOURCES, seen, prior, fb)
                self.assertIn(err, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
