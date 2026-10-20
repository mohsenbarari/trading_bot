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
    from scripts.two_site_proposal_runner import RetryableProviderOutputError
    from scripts.two_site_quota_resume_worker_reader import (
        load_prior_worker_chain,
        load_prior_worker_zero,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        ProposalError,
    )
    from two_site_proposal_runner import RetryableProviderOutputError  # type: ignore[no-redef]
    from two_site_quota_resume_worker_reader import (  # type: ignore[no-redef]
        load_prior_worker_chain,
        load_prior_worker_zero,
    )


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))

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

    def test_hardlink_result_rejected(self):
        artifacts, run_dir = self._create_artifacts("run-hard-worker-0", orig_prefix="run-hard")
        res_file = run_dir / "result.json"
        link_target = self.root / "hardlink_result.json"
        os.link(res_file, link_target)
        self.assertEqual(os.stat(res_file).st_nlink, 2)
        self.assertEqual(
            hashlib.sha256(res_file.read_bytes()).hexdigest(),
            artifacts["proposal_sha256"],
        )
        with self.assertRaises(ProposalError) as ctx:
            load_prior_worker_zero(self.root, self.task_path, artifacts)
        self.assertIn("hardlink", str(ctx.exception).lower())

    def _create_chain_artifacts(
        self,
        corr: int = 1,
        orig_prefix: str = "run-chain",
        final_worker_run_id: str | None = None,
        responses: list[dict] | None = None,
    ) -> tuple[dict, list[Path]]:
        if responses is None:
            responses = [{"content": f"# c {i}\n"} for i in range(corr + 1)]
        runner, _ = make_runner(self.root, responses)
        run_dirs = []
        prior_dir = None
        prior_sha = None

        for j in range(corr + 1):
            if j == corr and final_worker_run_id is not None:
                r_id = final_worker_run_id
            else:
                r_id = f"{orig_prefix}-worker-{j}"

            fb_path = None
            prop_path = None
            if j > 0:
                fb_path = self.root / ".local" / "two-site-refactor" / "pipeline" / orig_prefix / f"feedback-correction-{j}.json"
                fb_data = {
                    "reviewer": "codex",
                    "reviewed_proposal_sha256": prior_sha,
                    "findings": [f"fix-{j}"],
                    "test_evidence": [],
                }
                _write_json(fb_path, fb_data)
                prop_path = prior_dir / "result.json"

            r_dir = runner(
                self.root,
                self.task_path,
                "worker",
                r_id,
                300,
                feedback=fb_path,
                proposal=prop_path,
            )
            run_dirs.append(r_dir)
            prior_dir = r_dir
            prior_sha = hashlib.sha256((r_dir / "result.json").read_bytes()).hexdigest()

        final_id = run_dirs[-1].name
        artifacts = {
            "task_id": "T1",
            "source_head": HEAD,
            "original_run_prefix": orig_prefix,
            "final_worker_run_id": final_id,
            "proposal_path": f".local/two-site-refactor/proposals/{final_id}/result.json",
            "proposal_sha256": prior_sha,
            "correction_count": corr,
        }
        return artifacts, run_dirs

    def test_chain_success_corr_0_1_2(self):
        art0, _ = self._create_chain_artifacts(corr=0, orig_prefix="run-c0")
        rec0 = load_prior_worker_chain(self.root, self.task_path, art0)
        self.assertEqual(rec0["result_sha"], art0["proposal_sha256"])
        self.assertEqual(rec0["status"]["run_id"], "run-c0-worker-0")

        art1, run_dirs1 = self._create_chain_artifacts(corr=1, orig_prefix="run-c1")
        rec1 = load_prior_worker_chain(self.root, self.task_path, art1)
        self.assertEqual(rec1["result_sha"], art1["proposal_sha256"])
        self.assertEqual(rec1["status"]["run_id"], "run-c1-worker-1")
        self.assertEqual(rec1["conversation_id"], "conv-run-c1-worker-1")
        rec1["result"]["summary"] = "mutated"
        self.assertNotEqual((run_dirs1[1] / "result.json").read_text(), "mutated")

        art1r, _ = self._create_chain_artifacts(
            corr=1,
            orig_prefix="run-c1r",
            final_worker_run_id="run-c1r-worker-1-retry-1",
        )
        rec1r = load_prior_worker_chain(self.root, self.task_path, art1r)
        self.assertEqual(rec1r["status"]["run_id"], "run-c1r-worker-1-retry-1")

        art2, run_dirs2 = self._create_chain_artifacts(corr=2, orig_prefix="run-c2")
        rec2 = load_prior_worker_chain(self.root, self.task_path, art2)
        self.assertEqual(rec2["result_sha"], art2["proposal_sha256"])
        self.assertEqual(rec2["status"]["run_id"], "run-c2-worker-2")
        self.assertEqual(rec2["conversation_id"], "conv-run-c2-worker-2")

    def test_chain_invalid_correction_count(self):
        art1, _ = self._create_chain_artifacts(corr=1, orig_prefix="run-cc")
        for invalid_corr in (-1, 3, "1", None, True, False):
            with self.subTest(corr=invalid_corr):
                bad_art = dict(art1, correction_count=invalid_corr)
                with self.assertRaises(ProposalError):
                    load_prior_worker_chain(self.root, self.task_path, bad_art)

    def test_chain_backward_prior_id_tamper(self):
        art1, run_dirs1 = self._create_chain_artifacts(corr=1, orig_prefix="run-bkw")
        st1_path = run_dirs1[1] / "status.json"
        st1 = json.loads(st1_path.read_text())

        cases = [
            ("self_prior_id", dict(input_worker_run_id="run-bkw-worker-1")),
            ("wrong_prefix", dict(input_worker_run_id="other-worker-0")),
            ("invalid_retry", dict(input_worker_run_id="run-bkw-worker-0-retry-3")),
            ("non_hex_sha", dict(input_proposal_sha256="G" * 64)),
            ("short_sha", dict(input_proposal_sha256="0" * 63)),
        ]
        for name, override in cases:
            with self.subTest(case=name):
                tampered = dict(st1, **override)
                _write_json(st1_path, tampered)
                with self.assertRaises(ProposalError):
                    load_prior_worker_chain(self.root, self.task_path, art1)
        _write_json(st1_path, st1)

    def test_chain_feedback_tamper(self):
        art1, run_dirs1 = self._create_chain_artifacts(corr=1, orig_prefix="run-fb")
        fb_path = self.root / ".local" / "two-site-refactor" / "pipeline" / "run-fb" / "feedback-correction-1.json"
        fb = json.loads(fb_path.read_text())

        fb_cases = [
            ("invalid_reviewer", dict(reviewer="unknown")),
            ("wrong_reviewed_sha", dict(reviewed_proposal_sha256="0" * 64)),
            ("empty_findings", dict(findings=[])),
            ("invalid_findings_type", dict(findings="not-a-list")),
        ]
        for name, override in fb_cases:
            with self.subTest(case=name):
                _write_json(fb_path, dict(fb, **override))
                with self.assertRaises(ProposalError):
                    load_prior_worker_chain(self.root, self.task_path, art1)
        _write_json(fb_path, fb)

        st1_path = run_dirs1[1] / "status.json"
        st1 = json.loads(st1_path.read_text())
        st1["input_feedback_sha256"] = "1" * 64
        _write_json(st1_path, st1)
        with self.assertRaises(ProposalError):
            load_prior_worker_chain(self.root, self.task_path, art1)
        st1["input_feedback_sha256"] = hashlib.sha256(fb_path.read_bytes()).hexdigest()
        _write_json(st1_path, st1)

        fb_path.unlink()
        with self.assertRaises(ProposalError):
            load_prior_worker_chain(self.root, self.task_path, art1)

    def test_chain_reused_conversation_and_unchanged_proposal(self):
        art1, run_dirs1 = self._create_chain_artifacts(corr=1, orig_prefix="run-re")
        st1_path = run_dirs1[1] / "status.json"
        st0_path = run_dirs1[0] / "status.json"
        st1 = json.loads(st1_path.read_text())
        st0 = json.loads(st0_path.read_text())
        st1["conversation_id"] = st0["conversation_id"]
        _write_json(st1_path, st1)
        with self.assertRaises(ProposalError) as ctx:
            load_prior_worker_chain(self.root, self.task_path, art1)
        self.assertIn("conversation_id", str(ctx.exception))

        # A successful current-policy correction cannot emit an unchanged delta.
        # Model the real Producer rejection before any completion proof is sealed.
        with self.assertRaises(RetryableProviderOutputError) as ctx:
            self._create_chain_artifacts(
                corr=1,
                orig_prefix="run-same",
                responses=[{"content": "# same\n"}, {"content": "# same\n"}],
            )
        self.assertEqual(str(ctx.exception), "provider returned unusable proposal transport")
        self.assertIs(type(ctx.exception.__cause__), ProposalError)
        self.assertEqual(str(ctx.exception.__cause__), "empty or unchanged file")
        proposals = self.root / ".local/two-site-refactor/proposals"
        self.assertTrue((proposals / "run-same-worker-0/transport-completion.json").is_file())
        self.assertEqual(
            json.loads((proposals / "run-same-worker-0/result.json").read_text())["changes"][0]["content"],
            "# same\n",
        )
        for leaf in ("result.json", "status.json", "task.json", "transport-completion.json"):
            self.assertFalse((proposals / "run-same-worker-1" / leaf).exists())
        self.assertFalse((proposals / "run-same-worker-2").exists())
        self.assertFalse((proposals / "run-same-reviewer-1").exists())
        self.assertEqual(self.src_file.read_text(), "# orig\n")

    def test_chain_status_drift(self):
        art1, run_dirs1 = self._create_chain_artifacts(corr=1, orig_prefix="run-drift")
        reader_mod = sys.modules[load_prior_worker_chain.__module__]
        orig_validate_run = reader_mod.validate_run

        def mutating_validate_run(*args, **kwargs):
            res = orig_validate_run(*args, **kwargs)
            st_path = run_dirs1[1] / "status.json"
            st = json.loads(st_path.read_text())
            st["extra"] = "drifted"
            _write_json(st_path, st)
            return res

        with patch.object(reader_mod, "validate_run", side_effect=mutating_validate_run):
            with self.assertRaises(ProposalError) as ctx:
                load_prior_worker_chain(self.root, self.task_path, art1)
            self.assertIn("drift", str(ctx.exception).lower())

    def test_chain_symlink_hardlink_oversize_and_sha_mismatch(self):
        art1, run_dirs1 = self._create_chain_artifacts(corr=1, orig_prefix="run-sec")
        res_file = run_dirs1[1] / "result.json"
        link_target = self.root / "hardlink_chain_result.json"
        os.link(res_file, link_target)
        with self.assertRaises(ProposalError) as ctx:
            load_prior_worker_chain(self.root, self.task_path, art1)
        self.assertIn("hardlink", str(ctx.exception).lower())
        link_target.unlink()

        sym_dir = self.root / ".local/two-site-refactor/proposals/run-sec-sym-worker-1"
        os.symlink(run_dirs1[1], sym_dir)
        bad_sym_art = dict(
            art1,
            final_worker_run_id="run-sec-sym-worker-1",
            original_run_prefix="run-sec-sym",
            proposal_path=".local/two-site-refactor/proposals/run-sec-sym-worker-1/result.json",
        )
        with self.assertRaises(ProposalError):
            load_prior_worker_chain(self.root, self.task_path, bad_sym_art)
        sym_dir.unlink()

        st1_path = run_dirs1[1] / "status.json"
        orig_st_bytes = st1_path.read_bytes()
        st1_path.write_text("x" * (MAX_CONTEXT + 1024))
        with self.assertRaises(ProposalError):
            load_prior_worker_chain(self.root, self.task_path, art1)
        st1_path.write_bytes(orig_st_bytes)

        fb_path = self.root / ".local" / "two-site-refactor" / "pipeline" / "run-sec" / "feedback-correction-1.json"
        orig_fb_bytes = fb_path.read_bytes()
        fb_path.write_text("x" * (MAX_CONTEXT + 1024))
        with self.assertRaises(ProposalError):
            load_prior_worker_chain(self.root, self.task_path, art1)
        fb_path.write_bytes(orig_fb_bytes)

        bad_sha_art = dict(art1, proposal_sha256="0" * 64)
        with self.assertRaises(ProposalError) as ctx:
            load_prior_worker_chain(self.root, self.task_path, bad_sha_art)
        self.assertIn("proposal sha mismatch", str(ctx.exception).lower())
