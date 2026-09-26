#!/usr/bin/env python3
"""Offline unit tests for worker-one file-backed result reader and binding contract."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import MagicMock, patch

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        test_failure_signature as producer_test_failure_signature,
        test_gate_feedback as producer_test_gate_feedback,
    )
    from scripts.two_site_proposal_runner import (
        validate_correction_feedback,
    )
    from scripts.two_site_pre_review_receipt import (
        p1_00_characterization_bootstrap_findings,
        validate_test_receipt,
    )
    from scripts.two_site_quota_resume_worker_one_file_reader import (
        read_and_bind_worker_one_result,
    )
    import scripts.two_site_quota_resume_post_worker_one as post_mod
    from scripts.two_site_quota_resume_post_worker_one import (
        _EXPECTED_BOUND_KEYS,
        worker_one_post_result_context,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        test_failure_signature as producer_test_failure_signature,
        test_gate_feedback as producer_test_gate_feedback,
    )
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        validate_correction_feedback,
    )
    from two_site_pre_review_receipt import (  # type: ignore[no-redef]
        p1_00_characterization_bootstrap_findings,
        validate_test_receipt,
    )
    from two_site_quota_resume_worker_one_file_reader import (  # type: ignore[no-redef]
        read_and_bind_worker_one_result,
    )
    import two_site_quota_resume_post_worker_one as post_mod  # type: ignore[no-redef]
    from two_site_quota_resume_post_worker_one import (  # type: ignore[no-redef]
        _EXPECTED_BOUND_KEYS,
        worker_one_post_result_context,
    )

try:
    from tests.test_two_site_proposal_pipeline import HEAD, make_runner
except ImportError:
    from test_two_site_proposal_pipeline import HEAD, make_runner  # type: ignore[no-redef]

_EXPECTED_NINE_KEYS = frozenset({
    "base_context",
    "worker_info",
    "returned_state",
    "current_worker",
    "seen_convs",
    "seen_norm_findings",
    "seen_test_signatures",
    "seen_bootstrap_signatures",
    "state_check",
})


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

    def _setup_test_gate_scenario(
        self,
        origin: str,
    ) -> tuple[dict[str, Any], dict[str, Any], Path, Path, str, str]:
        src_path = self.root / "src.py"
        src_path.write_text("# orig\n")
        src_sha = hashlib.sha256(b"# orig\n").hexdigest()

        prefix = f"proof-gate-{origin.lower()}"
        w0_run_id = f"{prefix}-worker-0"
        w1_run_id = f"{prefix}-worker-1"
        fingerprint = f"fp-proof-{origin.lower()}-5e92fa77"
        task_path = self.root / "task.json"

        if origin == "BOOTSTRAP":
            new_test = "tests/test_p1_00_characterization_example.py"
            task = {
                "task_id": "T1",
                "goal": "G",
                "context_files": ["src.py"],
                "allowed_files": ["src.py", new_test],
                "requirements": ["R1"],
                "test_selectors": ["tests/test_a.py"],
            }
            task_path.write_text(json.dumps(task))

            runner, _ = make_runner(
                self.root,
                [
                    {
                        "result_override": {
                            "status": "PROPOSAL_READY",
                            "summary": "w0 bootstrap defect",
                            "covered_requirements": [1],
                            "findings": [],
                            "changes": [
                                {
                                    "path": new_test,
                                    "original_sha256": None,
                                    "content": "import main\n",
                                }
                            ],
                        }
                    },
                    {"content": "# v2\n"},
                ],
            )
            w0_dir = runner(self.root, task_path, "worker", w0_run_id, 300)
            w0_result_path = w0_dir / "result.json"
            w0_status_path = w0_dir / "status.json"
            w0_result = json.loads(w0_result_path.read_text())
            w0_status = json.loads(w0_status_path.read_text())
            w0_sha = w0_status["result_sha256"]
            w0_result_text = w0_result_path.read_text()

            findings = p1_00_characterization_bootstrap_findings(w0_result)
            fb_payload = {
                "reviewer": "test-gate",
                "reviewed_proposal_sha256": w0_sha,
                "findings": findings,
                "test_evidence": [
                    "gate=P1-00-characterization-bootstrap",
                    f"proposal_sha256={w0_sha}",
                    f"finding_count={len(findings)}",
                ],
            }
            fb_data = validate_correction_feedback(
                json.dumps(fb_payload), w0_sha
            )
            sig = hashlib.sha256(
                json.dumps(findings, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            receipt = None
        else:
            task = {
                "task_id": "T1",
                "goal": "G",
                "context_files": ["src.py"],
                "allowed_files": ["src.py"],
                "requirements": ["R1"],
                "test_selectors": ["tests/test_a.py"],
            }
            task_path.write_text(json.dumps(task))

            runner, _ = make_runner(
                self.root,
                [
                    {"content": "# v1\n"},
                    {"content": "# v2\n"},
                ],
            )
            w0_dir = runner(self.root, task_path, "worker", w0_run_id, 300)
            w0_result_path = w0_dir / "result.json"
            w0_status_path = w0_dir / "status.json"
            w0_result = json.loads(w0_result_path.read_text())
            w0_status = json.loads(w0_status_path.read_text())
            w0_sha = w0_status["result_sha256"]
            w0_result_text = w0_result_path.read_text()

            raw_receipt = {
                "state": "FAILED",
                "proposal_sha256": w0_sha,
                "selectors": ["tests/test_a.py"],
                "unsupported_specs": [],
                "returncode": 1,
                "stdout_tail": "FAILED tests/test_a.py::test_case",
                "stderr_tail": "",
                "snapshot_files": 1,
                "snapshot_bytes": 10,
                "isolation": "test-double",
            }
            receipt = validate_test_receipt(raw_receipt, w0_sha)
            fb_data = producer_test_gate_feedback(receipt, w0_sha)
            sig = producer_test_failure_signature(receipt)

        fb_path = self.root / f"feedback-{origin.lower()}.json"
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

        artifacts: dict[str, Any] = {
            "worker_index": 1,
            "final_worker_run_id": w0_run_id,
            "proposal_sha256": w0_sha,
            "semantic_task_fingerprint": fingerprint,
            "correction_origin": origin,
            "reviewer_path": None,
            "final_reviewer_run_id": None,
        }
        if origin == "BOOTSTRAP":
            artifacts.update({
                "pre_review_test_path": None,
                "pre_review_test_sha256": None,
                "pre_review_test_state": None,
            })
        else:
            artifacts["pre_review_test_state"] = "FAILED"

        ctx: dict[str, Any] = {
            "continuation_run_prefix": prefix,
            "source_head": HEAD,
            "task": task,
            "sources": {
                "src.py": {
                    "sha256": src_sha,
                    "content": "# orig\n",
                },
            },
            "artifacts": artifacts,
            "evidence": {
                "worker_info": {
                    "result": copy.deepcopy(w0_result),
                    "status": copy.deepcopy(w0_status),
                    "result_sha": w0_sha,
                    "result_text": w0_result_text,
                    "conversation_id": w0_status["conversation_id"],
                },
                "reviewer_status": None,
                "reviewer_result": None,
                "reviewer_sha": None,
                "feedback": copy.deepcopy(fb_data),
                "feedback_sha": fb_sha,
                "receipt": copy.deepcopy(receipt),
            },
        }

        returned_state: dict[str, Any] = {
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

        return ctx, returned_state, w1_status_path, w1_result_path, fb_sha, sig

    def test_read_and_bind_worker_one_bootstrap_origin(self) -> None:
        ctx, returned_state, w1_status_path, w1_result_path, _, sig = (
            self._setup_test_gate_scenario("BOOTSTRAP")
        )
        bound = read_and_bind_worker_one_result(
            self.root, ctx, returned_state
        )

        w0_status = ctx["evidence"]["worker_info"]["status"]
        w1_status = json.loads(w1_status_path.read_text())
        w1_result_text = w1_result_path.read_text()
        w1_sha = w1_status["result_sha256"]

        self.assertEqual(
            bound["seen_convs"],
            {w0_status["conversation_id"], w1_status["conversation_id"]},
        )
        self.assertEqual(bound["seen_norm_findings"], set())
        self.assertEqual(bound["seen_test_signatures"], set())
        self.assertEqual(bound["seen_bootstrap_signatures"], {sig})

        curr_worker = bound["current_worker"]
        self.assertEqual(curr_worker[0], "proof-gate-bootstrap-worker-1")
        self.assertEqual(curr_worker[1], w1_sha)
        self.assertEqual(curr_worker[2], w1_result_text)
        self.assertEqual(curr_worker[3], w1_status["conversation_id"])

        self.assertEqual(bound["returned_state"]["changed_paths"], ["src.py"])
        self.assertEqual(bound["returned_state"]["proposal_sha256"], w1_sha)

    def test_read_and_bind_worker_one_test_failed_origin(self) -> None:
        ctx, returned_state, w1_status_path, w1_result_path, _, sig = (
            self._setup_test_gate_scenario("TEST_FAILED")
        )
        bound = read_and_bind_worker_one_result(
            self.root, ctx, returned_state
        )

        w0_status = ctx["evidence"]["worker_info"]["status"]
        w1_status = json.loads(w1_status_path.read_text())
        w1_result_text = w1_result_path.read_text()
        w1_sha = w1_status["result_sha256"]

        self.assertEqual(
            bound["seen_convs"],
            {w0_status["conversation_id"], w1_status["conversation_id"]},
        )
        self.assertEqual(bound["seen_norm_findings"], set())
        self.assertEqual(bound["seen_test_signatures"], {sig})
        self.assertEqual(bound["seen_bootstrap_signatures"], set())

        curr_worker = bound["current_worker"]
        self.assertEqual(curr_worker[0], "proof-gate-test_failed-worker-1")
        self.assertEqual(curr_worker[1], w1_sha)
        self.assertEqual(curr_worker[2], w1_result_text)
        self.assertEqual(curr_worker[3], w1_status["conversation_id"])

        self.assertEqual(bound["returned_state"]["changed_paths"], ["src.py"])
        self.assertEqual(bound["returned_state"]["proposal_sha256"], w1_sha)

    def test_test_gate_real_file_tamper_and_faults_fail_closed(self) -> None:
        ctx_b, ret_b, w1_st_path, w1_res_path, fb_sha, _ = (
            self._setup_test_gate_scenario("BOOTSTRAP")
        )

        # 1. Tamper status input_feedback_sha256
        st_data = json.loads(w1_st_path.read_text())
        st_data["input_feedback_sha256"] = "0" * 64
        w1_st_path.write_text(json.dumps(st_data))
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, ctx_b, ret_b)
        st_data["input_feedback_sha256"] = fb_sha
        w1_st_path.write_text(json.dumps(st_data))

        # 2. Tamper status result_sha256
        st_data["result_sha256"] = "0" * 64
        w1_st_path.write_text(json.dumps(st_data))
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, ctx_b, ret_b)
        st_data["result_sha256"] = ret_b["proposal_sha256"]
        w1_st_path.write_text(json.dumps(st_data))

        # 3. Tamper result bytes on disk
        orig_bytes = w1_res_path.read_bytes()
        w1_res_path.write_bytes(b'{"status": "PROPOSAL_READY", "tampered": true}')
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, ctx_b, ret_b)
        w1_res_path.write_bytes(orig_bytes)

        # 4. Fake Reviewer evidence injected into test-gate context
        bad_rev_ctx = copy.deepcopy(ctx_b)
        bad_rev_ctx["evidence"]["reviewer_status"] = {"conversation_id": "fake-rev"}
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, bad_rev_ctx, ret_b)

        bad_rev_art = copy.deepcopy(ctx_b)
        bad_rev_art["artifacts"]["final_reviewer_run_id"] = "fake-rev-run"
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, bad_rev_art, ret_b)

        # 5. Invalid FAILED receipt in TEST_FAILED origin
        ctx_tf, ret_tf, _, _, _, _ = self._setup_test_gate_scenario("TEST_FAILED")
        bad_rcpt_state = copy.deepcopy(ctx_tf)
        bad_rcpt_state["evidence"]["receipt"]["state"] = "PASSED"
        bad_rcpt_state["evidence"]["receipt"]["returncode"] = 0
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, bad_rcpt_state, ret_tf)

        bad_rcpt_missing = copy.deepcopy(ctx_tf)
        bad_rcpt_missing["evidence"]["receipt"] = None
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, bad_rcpt_missing, ret_tf)

        bad_boot_rcpt = copy.deepcopy(ctx_b)
        bad_boot_rcpt["evidence"]["receipt"] = {"state": "FAILED"}
        with self.assertRaises(ProposalError):
            read_and_bind_worker_one_result(self.root, bad_boot_rcpt, ret_b)

    @contextmanager
    def _post_result_gate_env(self, origin: str):
        ctx, returned_state, w1_status_path, w1_result_path, _, sig = (
            self._setup_test_gate_scenario(origin)
        )
        task_path = self.root / "task.json"
        ctx["admitted_request"] = {
            "role": "worker",
            "original_request_id": f"orig-{origin.lower()}-req",
        }
        ctx["admitted_task_path"] = task_path
        ctx["original_terminal_receipt"] = {
            "terminal_quota": f"receipt-{origin.lower()}"
        }
        ctx["initial_refs"] = {
            "refs/heads/main": "0123456789abcdef0123456789abcdef01234567"
        }

        lock_open = [False]

        @contextmanager
        def fake_continuation_context(root: Path, req: dict):
            lock_open[0] = True
            try:
                yield ctx
            finally:
                lock_open[0] = False

        val_mock = MagicMock(
            return_value=(ctx["admitted_request"], ctx["admitted_task_path"], None)
        )
        receipt_mock = MagicMock(return_value=ctx["original_terminal_receipt"])
        art_mock = MagicMock(return_value=ctx["artifacts"])
        ev_mock = MagicMock(return_value=ctx["evidence"])
        check_mock = MagicMock(return_value=None)
        bytes_mock = MagicMock(
            side_effect=AssertionError("bounded_file_bytes must not be called")
        )

        with patch.object(
            post_mod,
            "worker_correction_continuation_context",
            fake_continuation_context,
        ), patch.object(
            post_mod, "validate_quota_continuation_request", val_mock
        ), patch.object(
            post_mod, "read_terminal_quota_record", receipt_mock
        ), patch.object(
            post_mod, "derive_worker_resume_artifacts", art_mock
        ), patch.object(
            post_mod, "load_worker_correction_evidence", ev_mock
        ), patch.object(
            post_mod, "check_state", check_mock
        ), patch.object(
            post_mod, "bounded_file_bytes", bytes_mock
        ):
            yield {
                "ctx": ctx,
                "returned_state": returned_state,
                "w1_status_path": w1_status_path,
                "w1_result_path": w1_result_path,
                "sig": sig,
                "lock_open": lock_open,
                "val_mock": val_mock,
                "receipt_mock": receipt_mock,
                "art_mock": art_mock,
                "ev_mock": ev_mock,
                "check_mock": check_mock,
                "bytes_mock": bytes_mock,
                "request": {"continuation": True},
            }

    def test_post_result_context_both_test_gate_origins(self) -> None:
        for origin in ("BOOTSTRAP", "TEST_FAILED"):
            with self.subTest(origin=origin):
                with self._post_result_gate_env(origin) as env:
                    ctx = env["ctx"]
                    returned_state = env["returned_state"]
                    lock_open = env["lock_open"]
                    sig = env["sig"]
                    w0_conv = ctx["evidence"]["worker_info"]["status"]["conversation_id"]

                    self.assertFalse(lock_open[0])

                    with worker_one_post_result_context(
                        self.root, env["request"], returned_state
                    ) as post_ctx:
                        self.assertTrue(lock_open[0])
                        self.assertEqual(set(post_ctx.keys()), _EXPECTED_NINE_KEYS)

                        curr_worker = post_ctx["current_worker"]
                        w1_conv = curr_worker[3]
                        self.assertNotEqual(w0_conv, w1_conv)
                        self.assertEqual(post_ctx["seen_convs"], {w0_conv, w1_conv})
                        self.assertEqual(post_ctx["seen_norm_findings"], set())

                        if origin == "BOOTSTRAP":
                            self.assertEqual(
                                post_ctx["seen_bootstrap_signatures"], {sig}
                            )
                            self.assertEqual(post_ctx["seen_test_signatures"], set())
                        else:
                            self.assertEqual(post_ctx["seen_test_signatures"], {sig})
                            self.assertEqual(
                                post_ctx["seen_bootstrap_signatures"], set()
                            )

                        self.assertIsNot(post_ctx["base_context"], ctx)
                        self.assertIsNot(post_ctx["returned_state"], returned_state)
                        self.assertIsNot(post_ctx["seen_convs"], ctx["evidence"])
                        post_ctx["returned_state"]["tampered_key"] = True
                        self.assertNotIn("tampered_key", returned_state)

                        post_ctx["state_check"]()
                        env["bytes_mock"].assert_not_called()

                    self.assertFalse(lock_open[0])

    def test_post_result_context_tamper_and_drift_fail_closed(self) -> None:
        for origin in ("BOOTSTRAP", "TEST_FAILED"):
            with self.subTest(origin=origin):
                with self._post_result_gate_env(origin) as env:
                    with worker_one_post_result_context(
                        self.root, env["request"], env["returned_state"]
                    ) as post_ctx:
                        st_path = env["w1_status_path"]
                        orig_st_text = st_path.read_text()
                        st_data = json.loads(orig_st_text)
                        st_data["result_sha256"] = "0" * 64
                        st_path.write_text(json.dumps(st_data))
                        with self.assertRaises(ProposalError):
                            post_ctx["state_check"]()
                        st_path.write_text(orig_st_text)
                        post_ctx["state_check"]()

                        res_path = env["w1_result_path"]
                        orig_res_bytes = res_path.read_bytes()
                        res_path.write_bytes(
                            b'{"status": "PROPOSAL_READY", "tampered": true}'
                        )
                        with self.assertRaises(ProposalError):
                            post_ctx["state_check"]()
                        res_path.write_bytes(orig_res_bytes)
                        post_ctx["state_check"]()

                        drifted_ev = copy.deepcopy(env["ctx"]["evidence"])
                        drifted_ev["feedback"]["drift"] = "injected"
                        env["ev_mock"].return_value = drifted_ev
                        with self.assertRaises(ProposalError):
                            post_ctx["state_check"]()
                        env["ev_mock"].return_value = env["ctx"]["evidence"]
                        post_ctx["state_check"]()

                        env["check_mock"].side_effect = ProposalError(
                            "STOP: check_state detected drift"
                        )
                        with self.assertRaises(ProposalError):
                            post_ctx["state_check"]()
                        env["check_mock"].side_effect = None
                        post_ctx["state_check"]()

                        env["bytes_mock"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
