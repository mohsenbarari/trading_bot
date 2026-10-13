from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_post_worker_one_remainder import (
    execute_worker_one_post_result_remainder,
)


def _setup_workspace(
    tmp_path: Path,
    run_prefix: str = "valid-run-001",
    timeout: int = 300,
) -> tuple[Path, Path, Path, Path, dict, dict, dict]:
    root = tmp_path.resolve()
    pipeline_dir = root / ".local/two-site-refactor/pipeline"
    pipeline_dir.mkdir(parents=True, mode=0o700)

    worker_run_id = f"{run_prefix}-worker-1"
    worker_dir = root / ".local/two-site-refactor/proposals" / worker_run_id
    worker_dir.mkdir(parents=True, mode=0o700)

    proposal_file = worker_dir / "result.json"
    proposal_file.write_text('{"status": "PROPOSAL_READY", "changes": []}', encoding="utf-8")

    task_file = root / "task.json"
    task_file.write_text('{"task_id": "T1"}', encoding="utf-8")

    post_ctx = {
        "base_context": {
            "artifacts": {"correction_origin": None},
            "evidence": {"receipt": None},
            "admitted_task_path": task_file,
            "admitted_request": {"timeout": timeout},
            "continuation_run_prefix": run_prefix,
            "task": {"task_id": "T1"},
            "sources": {"f.py": "content"},
            "source_head": "0123456789abcdef0123456789abcdef01234567",
            "initial_refs": {"refs/heads/main": "0123456789abcdef0123456789abcdef01234567"},
        },
        "worker_info": {
            "result": {"status": "PROPOSAL_READY", "changes": [{"path": "f.py"}]},
        },
        "returned_state": {
            "state": "WORKER_PROPOSAL_READY",
            "task_id": "T1",
            "source_head": "0123456789abcdef0123456789abcdef01234567",
            "final_worker_run_id": worker_run_id,
            "proposal_sha256": "a" * 64,
            "changed_paths": ["f.py"],
            "proposal_path": f".local/two-site-refactor/proposals/{worker_run_id}/result.json",
            "correction_count": 1,
            "semantic_task_fingerprint": "b" * 64,
        },
        "current_worker": (worker_run_id, "a" * 64, "{}", "conv-123"),
        "seen_convs": {"conv-123"},
        "seen_norm_findings": {"finding-1"},
        "seen_test_signatures": {"test-sig-1"},
        "seen_bootstrap_signatures": {"boot-sig-1"},
        "state_check": None,
    }

    caller_req = {"task_id": "T1"}
    caller_state = copy.deepcopy(post_ctx["returned_state"])
    return root, task_file, worker_dir, proposal_file, post_ctx, caller_req, caller_state


class TestExecuteWorkerOnePostResultRemainder(unittest.TestCase):
    def test_exact_bound_mapping_and_two_state_checks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, task_file, worker_dir, _pfile, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )
            state_check_calls: list[int] = []

            def mock_state_check() -> None:
                state_check_calls.append(len(state_check_calls) + 1)

            post_ctx["state_check"] = mock_state_check

            context_open = False
            binder_calls = 0

            @contextmanager
            def mock_binder(r, req, st):
                nonlocal context_open, binder_calls
                binder_calls += 1
                context_open = True
                try:
                    yield post_ctx
                finally:
                    context_open = False

            recorded_kwargs: dict = {}
            expected_kwargs = {
                "root", "task_candidate", "task", "sources", "initial_head",
                "initial_refs", "run_prefix", "timeout", "cli", "state",
                "evidence_dir", "state_check", "worker_dir", "worker_res",
                "current_worker", "seen_convs", "correction_count",
                "seen_norm_findings", "seen_test_signatures",
                "seen_bootstrap_signatures", "seen_branch_factors",
            }
            remainder_calls = 0

            def mock_remainder(**kwargs) -> None:
                nonlocal remainder_calls
                remainder_calls += 1
                self.assertTrue(context_open)
                recorded_kwargs.update(kwargs)
                self.assertEqual(set(kwargs), expected_kwargs)
                self.assertEqual(state_check_calls, [1])
                ev_dir = kwargs["evidence_dir"]
                self.assertTrue(ev_dir.is_dir())
                self.assertEqual(oct(ev_dir.stat().st_mode & 0o777), "0o700")
                expected_keys = {
                    "state", "task_id", "source_head", "final_worker_run_id",
                    "final_reviewer_run_id", "proposal_sha256", "changed_paths",
                    "proposal_path", "reviewer_path", "correction_count",
                    "blockers", "pre_review_test_path", "pre_review_test_sha256",
                    "pre_review_test_state", "semantic_task_fingerprint",
                }
                self.assertEqual(set(kwargs["state"].keys()), expected_keys)
                self.assertEqual(len(kwargs["state"]), 15)
                kwargs["state"]["state"] = "READY_FOR_CODEX"
                kwargs["state"]["final_reviewer_run_id"] = "valid-run-001-reviewer-1"

            with patch(
                "scripts.two_site_quota_resume_post_worker_one_remainder.worker_one_post_result_context",
                mock_binder,
            ), patch(
                "scripts.two_site_quota_resume_post_worker_one_remainder._run_post_worker_remainder",
                mock_remainder,
            ):
                final_state = execute_worker_one_post_result_remainder(
                    root, caller_req, caller_state
                )

            self.assertEqual(binder_calls, 1)
            self.assertFalse(context_open)
            self.assertEqual(remainder_calls, 1)
            self.assertEqual(state_check_calls, [1, 2])
            self.assertEqual(final_state["state"], "READY_FOR_CODEX")
            self.assertEqual(
                final_state["final_reviewer_run_id"], "valid-run-001-reviewer-1"
            )

            expected_ev_dir = root / ".local/two-site-refactor/pipeline/valid-run-001"
            self.assertEqual(recorded_kwargs["root"], root)
            self.assertEqual(recorded_kwargs["task_candidate"], task_file)
            self.assertEqual(recorded_kwargs["task"], {"task_id": "T1"})
            self.assertEqual(recorded_kwargs["sources"], {"f.py": "content"})
            self.assertEqual(
                recorded_kwargs["initial_head"],
                "0123456789abcdef0123456789abcdef01234567",
            )
            self.assertEqual(
                recorded_kwargs["initial_refs"],
                {"refs/heads/main": "0123456789abcdef0123456789abcdef01234567"},
            )
            self.assertEqual(recorded_kwargs["run_prefix"], "valid-run-001")
            self.assertEqual(recorded_kwargs["timeout"], 300)
            self.assertIsNone(recorded_kwargs["cli"])
            self.assertEqual(recorded_kwargs["evidence_dir"], expected_ev_dir)
            self.assertEqual(recorded_kwargs["state_check"], mock_state_check)
            self.assertEqual(recorded_kwargs["worker_dir"], worker_dir)
            self.assertEqual(
                recorded_kwargs["worker_res"],
                {"status": "PROPOSAL_READY", "changes": [{"path": "f.py"}]},
            )
            self.assertEqual(recorded_kwargs["current_worker"], post_ctx["current_worker"])
            self.assertEqual(recorded_kwargs["correction_count"], 1)
            self.assertEqual(recorded_kwargs["seen_convs"], {"conv-123"})
            self.assertEqual(recorded_kwargs["seen_norm_findings"], {"finding-1"})
            self.assertEqual(recorded_kwargs["seen_test_signatures"], {"test-sig-1"})
            self.assertEqual(recorded_kwargs["seen_bootstrap_signatures"], {"boot-sig-1"})
            self.assertEqual(recorded_kwargs["seen_branch_factors"], set())

    def test_caller_mutation_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, _task, worker_dir, _pfile, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )
            state_check_calls = []
            post_ctx["state_check"] = lambda: state_check_calls.append(True)

            @contextmanager
            def mock_binder(r, req, st):
                caller_state["final_worker_run_id"] = "MUTATED-RUN-ID"
                caller_state["proposal_path"] = ".local/two-site-refactor/proposals/MUTATED/result.json"
                caller_state["task_id"] = "MUTATED-TASK"
                caller_state["changed_paths"] = ["MUTATED.py"]
                caller_state["correction_count"] = 99
                caller_req["timeout"] = 9999
                yield post_ctx

            recorded_kwargs: dict = {}

            def mock_remainder(**kwargs) -> None:
                recorded_kwargs.update(kwargs)
                kwargs["state"]["state"] = "READY_FOR_CODEX"
                kwargs["state"]["final_reviewer_run_id"] = "valid-run-001-reviewer-1"

            with patch(
                "scripts.two_site_quota_resume_post_worker_one_remainder.worker_one_post_result_context",
                mock_binder,
            ), patch(
                "scripts.two_site_quota_resume_post_worker_one_remainder._run_post_worker_remainder",
                mock_remainder,
            ):
                final_state = execute_worker_one_post_result_remainder(
                    root, caller_req, caller_state
                )

            self.assertEqual(state_check_calls, [True, True])
            self.assertEqual(recorded_kwargs["worker_dir"], worker_dir)
            self.assertEqual(recorded_kwargs["timeout"], 300)
            self.assertEqual(recorded_kwargs["correction_count"], 1)
            self.assertEqual(
                recorded_kwargs["state"]["final_worker_run_id"],
                "valid-run-001-worker-1",
            )
            self.assertEqual(recorded_kwargs["state"]["task_id"], "T1")
            self.assertEqual(recorded_kwargs["state"]["changed_paths"], ["f.py"])
            self.assertEqual(recorded_kwargs["state"]["correction_count"], 1)
            self.assertEqual(final_state["final_worker_run_id"], "valid-run-001-worker-1")
            self.assertEqual(final_state["state"], "READY_FOR_CODEX")

    def test_invalid_timeout_precondition_failures(self) -> None:
        invalid_timeouts = [None, True, False, "300", 9, 901, 300.0]
        for bad_t in invalid_timeouts:
            with self.subTest(timeout=bad_t), tempfile.TemporaryDirectory() as td:
                root, _task, _wdir, _pfile, post_ctx, caller_req, caller_state = (
                    _setup_workspace(Path(td))
                )
                post_ctx["state_check"] = lambda: None
                if bad_t is None:
                    post_ctx["base_context"]["admitted_request"].pop("timeout", None)
                else:
                    post_ctx["base_context"]["admitted_request"]["timeout"] = bad_t

                remainder_calls = []
                binder_calls = 0

                @contextmanager
                def mock_binder(r, req, st):
                    nonlocal binder_calls
                    binder_calls += 1
                    yield post_ctx

                with patch(
                    "scripts.two_site_quota_resume_post_worker_one_remainder.worker_one_post_result_context",
                    mock_binder,
                ), patch(
                    "scripts.two_site_quota_resume_post_worker_one_remainder._run_post_worker_remainder",
                    lambda **kwargs: remainder_calls.append(True),
                ):
                    with self.assertRaises(ProposalError):
                        execute_worker_one_post_result_remainder(
                            root, caller_req, caller_state
                        )

                self.assertEqual(binder_calls, 1)
                self.assertEqual(remainder_calls, [])
                self.assertFalse(
                    (root / ".local/two-site-refactor/pipeline/valid-run-001").exists()
                )

    def test_noncanonical_or_symlink_task_path_failures(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, task_file, _wdir, _pfile, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )
            post_ctx["state_check"] = lambda: None
            symlink_task = root / "symlink_task.json"
            symlink_task.symlink_to(task_file)

            bad_task_paths = [
                Path("task.json"),
                root / "sub/../task.json",
                Path(td) / "outside.json",
                symlink_task,
            ]
            for bad_p in bad_task_paths:
                with self.subTest(task_path=bad_p):
                    post_ctx["base_context"]["admitted_task_path"] = bad_p
                    remainder_calls = []
                    binder_calls = 0

                    @contextmanager
                    def mock_binder(r, req, st):
                        nonlocal binder_calls
                        binder_calls += 1
                        yield post_ctx

                    with patch(
                        "scripts.two_site_quota_resume_post_worker_one_remainder.worker_one_post_result_context",
                        mock_binder,
                    ), patch(
                        "scripts.two_site_quota_resume_post_worker_one_remainder._run_post_worker_remainder",
                        lambda **kwargs: remainder_calls.append(True),
                    ):
                        with self.assertRaises(ProposalError):
                            execute_worker_one_post_result_remainder(
                                root, caller_req, caller_state
                            )

                    self.assertEqual(binder_calls, 1)
                    self.assertEqual(remainder_calls, [])
                    self.assertFalse(
                        (root / ".local/two-site-refactor/pipeline/valid-run-001").exists()
                    )

    def test_noncanonical_or_symlink_worker_result_path_failures(self) -> None:
        # Case 1: proposal path mismatch
        with tempfile.TemporaryDirectory() as td:
            root, _task, _wdir, _pfile, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )
            post_ctx["state_check"] = lambda: None
            post_ctx["returned_state"]["proposal_path"] = (
                ".local/two-site-refactor/proposals/other-worker-1/result.json"
            )
            remainder_calls = []
            binder_calls = 0

            @contextmanager
            def mock_binder(r, req, st):
                nonlocal binder_calls
                binder_calls += 1
                yield post_ctx

            with patch(
                "scripts.two_site_quota_resume_post_worker_one_remainder.worker_one_post_result_context",
                mock_binder,
            ), patch(
                "scripts.two_site_quota_resume_post_worker_one_remainder._run_post_worker_remainder",
                lambda **kwargs: remainder_calls.append(True),
            ):
                with self.assertRaises(ProposalError):
                    execute_worker_one_post_result_remainder(
                        root, caller_req, caller_state
                    )

            self.assertEqual(binder_calls, 1)
            self.assertEqual(remainder_calls, [])

        # Case 2: proposal file is symlink
        with tempfile.TemporaryDirectory() as td:
            root, _task, _wdir, proposal_file, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )
            post_ctx["state_check"] = lambda: None
            proposal_file.unlink()
            target_file = root / "real_result.json"
            target_file.write_text("{}", encoding="utf-8")
            proposal_file.symlink_to(target_file)
            remainder_calls = []
            binder_calls = 0

            @contextmanager
            def mock_binder(r, req, st):
                nonlocal binder_calls
                binder_calls += 1
                yield post_ctx

            with patch(
                "scripts.two_site_quota_resume_post_worker_one_remainder.worker_one_post_result_context",
                mock_binder,
            ), patch(
                "scripts.two_site_quota_resume_post_worker_one_remainder._run_post_worker_remainder",
                lambda **kwargs: remainder_calls.append(True),
            ):
                with self.assertRaises(ProposalError):
                    execute_worker_one_post_result_remainder(
                        root, caller_req, caller_state
                    )

            self.assertEqual(binder_calls, 1)
            self.assertEqual(remainder_calls, [])

    def test_preexisting_evidence_dir_collision(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, _task, _wdir, _pfile, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )
            post_ctx["state_check"] = lambda: None
            collision_dir = root / ".local/two-site-refactor/pipeline/valid-run-001"
            collision_dir.mkdir(parents=True, mode=0o700)
            remainder_calls = []
            binder_calls = 0

            @contextmanager
            def mock_binder(r, req, st):
                nonlocal binder_calls
                binder_calls += 1
                yield post_ctx

            with patch(
                "scripts.two_site_quota_resume_post_worker_one_remainder.worker_one_post_result_context",
                mock_binder,
            ), patch(
                "scripts.two_site_quota_resume_post_worker_one_remainder._run_post_worker_remainder",
                lambda **kwargs: remainder_calls.append(True),
            ):
                with self.assertRaises(ProposalError):
                    execute_worker_one_post_result_remainder(
                        root, caller_req, caller_state
                    )

            self.assertEqual(binder_calls, 1)
            self.assertEqual(remainder_calls, [])

    def test_strict_bound_history_context_and_empty_origins(self) -> None:
        cases = (None, "REVIEWER", "BOOTSTRAP", "missing artifacts", "bad artifacts",
                 "missing evidence", "bad evidence", "missing receipt", "unknown origin")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td:
                root, _, _, _, ctx, request, returned = _setup_workspace(Path(td))
                base = ctx["base_context"]
                good = case in (None, "REVIEWER", "BOOTSTRAP")
                if good:
                    base["artifacts"]["correction_origin"] = case
                elif case == "missing artifacts":
                    del base["artifacts"]
                elif case == "bad artifacts":
                    base["artifacts"] = None
                elif case == "missing evidence":
                    del base["evidence"]
                elif case == "bad evidence":
                    base["evidence"] = None
                elif case == "missing receipt":
                    base["artifacts"]["correction_origin"] = "TEST_FAILED"
                    del base["evidence"]["receipt"]
                else:
                    base["artifacts"]["correction_origin"] = "UNKNOWN"
                checks, calls = [], []
                ctx["state_check"] = lambda: checks.append(True)

                @contextmanager
                def binder(*args):
                    yield ctx

                def remainder(**kwargs):
                    calls.append(kwargs)
                    self.assertEqual(kwargs["seen_branch_factors"], set())
                    kwargs["state"].update(state="READY_FOR_CODEX", final_reviewer_run_id="reviewer")

                with patch("scripts.two_site_quota_resume_post_worker_one_remainder.worker_one_post_result_context", binder), \
                     patch("scripts.two_site_quota_resume_post_worker_one_remainder._run_post_worker_remainder", remainder):
                    if good:
                        result = execute_worker_one_post_result_remainder(root, request, returned)
                        self.assertEqual(result["state"], "READY_FOR_CODEX")
                    else:
                        with self.assertRaises(ProposalError):
                            execute_worker_one_post_result_remainder(root, request, returned)
                self.assertEqual(checks, [True, True] if good else [True])
                self.assertEqual(len(calls), 1 if good else 0)
                if not good:
                    self.assertFalse((root / ".local/two-site-refactor/pipeline/valid-run-001").exists())

    def test_missing_final_reviewer_id_failures(self) -> None:
        bad_reviewer_ids = [None, "", "   ", 123]
        for bad_id in bad_reviewer_ids:
            with self.subTest(reviewer_id=bad_id), tempfile.TemporaryDirectory() as td:
                root, _task, _wdir, _pfile, post_ctx, caller_req, caller_state = (
                    _setup_workspace(Path(td))
                )
                post_ctx["state_check"] = lambda: None
                binder_calls = 0
                remainder_calls = 0

                @contextmanager
                def mock_binder(r, req, st):
                    nonlocal binder_calls
                    binder_calls += 1
                    yield post_ctx

                def mock_remainder(**kwargs) -> None:
                    nonlocal remainder_calls
                    remainder_calls += 1
                    kwargs["state"]["state"] = "READY_FOR_CODEX"
                    kwargs["state"]["final_reviewer_run_id"] = bad_id

                with patch(
                    "scripts.two_site_quota_resume_post_worker_one_remainder.worker_one_post_result_context",
                    mock_binder,
                ), patch(
                    "scripts.two_site_quota_resume_post_worker_one_remainder._run_post_worker_remainder",
                    mock_remainder,
                ):
                    with self.assertRaises(ProposalError):
                        execute_worker_one_post_result_remainder(
                            root, caller_req, caller_state
                        )

                self.assertEqual(binder_calls, 1)
                self.assertEqual(remainder_calls, 1)


if __name__ == "__main__":
    unittest.main()
