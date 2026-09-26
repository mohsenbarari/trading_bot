from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_post_worker_zero_remainder import (
    execute_worker_zero_post_result_remainder,
)


def _setup_workspace(
    tmp_path: Path,
    run_prefix: str = "valid-run-001",
    timeout: int = 300,
) -> tuple[Path, Path, Path, dict, dict]:
    root = tmp_path.resolve()
    pipeline_dir = root / ".local/two-site-refactor/pipeline"
    pipeline_dir.mkdir(parents=True, mode=0o700)

    worker_run_id = f"{run_prefix}-worker-0"
    worker_dir = root / ".local/two-site-refactor/proposals" / worker_run_id
    worker_dir.mkdir(parents=True, mode=0o700)

    proposal_file = worker_dir / "result.json"
    proposal_file.write_text('{"status": "PROPOSAL_READY", "changes": []}', encoding="utf-8")

    task_file = root / "task.json"
    task_file.write_text('{"task_id": "T1"}', encoding="utf-8")

    post_ctx = {
        "base_context": {
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
            "correction_count": 0,
            "semantic_task_fingerprint": "b" * 64,
        },
        "current_worker": (worker_run_id, "a" * 64, "{}", "conv-123"),
        "seen_convs": {"conv-123"},
        "state_check": None,
    }

    caller_req = {"task_id": "T1"}
    caller_state = copy.deepcopy(post_ctx["returned_state"])
    return root, task_file, worker_dir, post_ctx, caller_req, caller_state


class TestExecuteWorkerZeroPostResultRemainder(unittest.TestCase):
    def test_exact_bound_mapping_and_two_state_checks(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root, task_file, worker_dir, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )

            state_check_calls: list[int] = []

            def mock_state_check() -> None:
                state_check_calls.append(len(state_check_calls) + 1)

            post_ctx["state_check"] = mock_state_check

            @contextmanager
            def mock_binder(r, req, st):
                yield post_ctx

            recorded_kwargs: dict = {}

            def mock_remainder(**kwargs) -> None:
                recorded_kwargs.update(kwargs)
                self.assertEqual(state_check_calls, [1])
                self.assertTrue(kwargs["evidence_dir"].is_dir())
                kwargs["state"]["state"] = "READY_FOR_CODEX"
                kwargs["state"]["final_reviewer_run_id"] = "valid-run-001-reviewer-0"

            with patch(
                "scripts.two_site_quota_resume_post_worker_zero_remainder.worker_zero_post_result_context",
                mock_binder,
            ), patch(
                "scripts.two_site_quota_resume_post_worker_zero_remainder._run_post_worker_remainder",
                mock_remainder,
            ):
                final_state = execute_worker_zero_post_result_remainder(
                    root, caller_req, caller_state
                )

            self.assertEqual(state_check_calls, [1, 2])
            self.assertEqual(final_state["state"], "READY_FOR_CODEX")
            self.assertEqual(
                final_state["final_reviewer_run_id"], "valid-run-001-reviewer-0"
            )

            expected_evidence_dir = root / ".local/two-site-refactor/pipeline/valid-run-001"
            self.assertEqual(recorded_kwargs["root"], root)
            self.assertEqual(recorded_kwargs["task_candidate"], task_file)
            self.assertEqual(recorded_kwargs["task"], {"task_id": "T1"})
            self.assertEqual(recorded_kwargs["sources"], {"f.py": "content"})
            self.assertEqual(
                recorded_kwargs["initial_head"],
                "0123456789abcdef0123456789abcdef01234567",
            )
            self.assertEqual(recorded_kwargs["run_prefix"], "valid-run-001")
            self.assertEqual(recorded_kwargs["timeout"], 300)
            self.assertIsNone(recorded_kwargs["cli"])
            self.assertEqual(recorded_kwargs["evidence_dir"], expected_evidence_dir)
            self.assertEqual(recorded_kwargs["state_check"], mock_state_check)
            self.assertEqual(recorded_kwargs["worker_dir"], worker_dir)
            self.assertEqual(
                recorded_kwargs["worker_res"],
                {"status": "PROPOSAL_READY", "changes": [{"path": "f.py"}]},
            )
            self.assertEqual(recorded_kwargs["current_worker"], post_ctx["current_worker"])
            self.assertEqual(recorded_kwargs["seen_convs"], {"conv-123"})
            self.assertEqual(recorded_kwargs["correction_count"], 0)
            self.assertEqual(recorded_kwargs["seen_norm_findings"], set())
            self.assertEqual(recorded_kwargs["seen_test_signatures"], set())
            self.assertEqual(recorded_kwargs["seen_bootstrap_signatures"], set())

    def test_caller_mutation_during_yield_remains_bound(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root, task_file, worker_dir, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )
            post_ctx["state_check"] = lambda: None

            @contextmanager
            def mock_binder(r, req, st):
                caller_state["final_worker_run_id"] = "MUTATED-RUN-ID"
                caller_state["proposal_path"] = ".local/two-site-refactor/proposals/MUTATED/result.json"
                caller_state["task_id"] = "MUTATED-TASK"
                caller_state["changed_paths"] = ["MUTATED.py"]
                yield post_ctx

            recorded_kwargs: dict = {}

            def mock_remainder(**kwargs) -> None:
                recorded_kwargs.update(kwargs)
                kwargs["state"]["state"] = "READY_FOR_CODEX"
                kwargs["state"]["final_reviewer_run_id"] = "valid-run-001-reviewer-0"

            with patch(
                "scripts.two_site_quota_resume_post_worker_zero_remainder.worker_zero_post_result_context",
                mock_binder,
            ), patch(
                "scripts.two_site_quota_resume_post_worker_zero_remainder._run_post_worker_remainder",
                mock_remainder,
            ):
                final_state = execute_worker_zero_post_result_remainder(
                    root, caller_req, caller_state
                )

            self.assertEqual(recorded_kwargs["worker_dir"], worker_dir)
            self.assertEqual(
                recorded_kwargs["state"]["final_worker_run_id"],
                "valid-run-001-worker-0",
            )
            self.assertEqual(recorded_kwargs["state"]["task_id"], "T1")
            self.assertEqual(recorded_kwargs["state"]["changed_paths"], ["f.py"])
            self.assertEqual(final_state["final_worker_run_id"], "valid-run-001-worker-0")

    def test_timeout_strictness_failures(self) -> None:
        import tempfile
        invalid_timeouts = [None, True, False, "300", 9, 901, 300.0]
        for bad_t in invalid_timeouts:
            with tempfile.TemporaryDirectory() as td:
                root, _task, _wdir, post_ctx, caller_req, caller_state = (
                    _setup_workspace(Path(td))
                )
                post_ctx["state_check"] = lambda: None
                if bad_t is None:
                    post_ctx["base_context"]["admitted_request"].pop("timeout", None)
                else:
                    post_ctx["base_context"]["admitted_request"]["timeout"] = bad_t

                remainder_called = []

                @contextmanager
                def mock_binder(r, req, st):
                    yield post_ctx

                with patch(
                    "scripts.two_site_quota_resume_post_worker_zero_remainder.worker_zero_post_result_context",
                    mock_binder,
                ), patch(
                    "scripts.two_site_quota_resume_post_worker_zero_remainder._run_post_worker_remainder",
                    lambda **kwargs: remainder_called.append(True),
                ):
                    with self.assertRaises(ProposalError):
                        execute_worker_zero_post_result_remainder(
                            root, caller_req, caller_state
                        )

                evidence_dir = root / ".local/two-site-refactor/pipeline/valid-run-001"
                self.assertFalse(evidence_dir.exists())
                self.assertEqual(remainder_called, [])

    def test_noncanonical_task_path_failures(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root, task_file, _wdir, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )
            post_ctx["state_check"] = lambda: None

            symlink_task = root / "symlink_task.json"
            symlink_task.symlink_to(task_file)

            bad_paths = [
                Path("task.json"),
                root / "sub/../task.json",
                Path(td) / "outside.json",
                symlink_task,
            ]

            for bad_p in bad_paths:
                post_ctx["base_context"]["admitted_task_path"] = bad_p
                remainder_called = []

                @contextmanager
                def mock_binder(r, req, st):
                    yield post_ctx

                with patch(
                    "scripts.two_site_quota_resume_post_worker_zero_remainder.worker_zero_post_result_context",
                    mock_binder,
                ), patch(
                    "scripts.two_site_quota_resume_post_worker_zero_remainder._run_post_worker_remainder",
                    lambda **kwargs: remainder_called.append(True),
                ):
                    with self.assertRaises(ProposalError):
                        execute_worker_zero_post_result_remainder(
                            root, caller_req, caller_state
                        )

                evidence_dir = root / ".local/two-site-refactor/pipeline/valid-run-001"
                self.assertFalse(evidence_dir.exists())
                self.assertEqual(remainder_called, [])

    def test_preexisting_evidence_dir_collision(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root, _task, _wdir, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )
            post_ctx["state_check"] = lambda: None

            collision_dir = root / ".local/two-site-refactor/pipeline/valid-run-001"
            collision_dir.mkdir(parents=True, mode=0o700)

            remainder_called = []

            @contextmanager
            def mock_binder(r, req, st):
                yield post_ctx

            with patch(
                "scripts.two_site_quota_resume_post_worker_zero_remainder.worker_zero_post_result_context",
                mock_binder,
            ), patch(
                "scripts.two_site_quota_resume_post_worker_zero_remainder._run_post_worker_remainder",
                lambda **kwargs: remainder_called.append(True),
            ):
                with self.assertRaises(ProposalError):
                    execute_worker_zero_post_result_remainder(
                        root, caller_req, caller_state
                    )

            self.assertEqual(remainder_called, [])

    def test_failed_state_check_propagation(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root, _task, _wdir, post_ctx, caller_req, caller_state = (
                _setup_workspace(Path(td))
            )

            def failing_state_check() -> None:
                raise ProposalError("pre-check state drift")

            post_ctx["state_check"] = failing_state_check
            remainder_called = []

            @contextmanager
            def mock_binder(r, req, st):
                yield post_ctx

            with patch(
                "scripts.two_site_quota_resume_post_worker_zero_remainder.worker_zero_post_result_context",
                mock_binder,
            ), patch(
                "scripts.two_site_quota_resume_post_worker_zero_remainder._run_post_worker_remainder",
                lambda **kwargs: remainder_called.append(True),
            ):
                with self.assertRaises(ProposalError) as cm:
                    execute_worker_zero_post_result_remainder(
                        root, caller_req, caller_state
                    )
                self.assertIn("pre-check state drift", str(cm.exception))

            evidence_dir = root / ".local/two-site-refactor/pipeline/valid-run-001"
            self.assertFalse(evidence_dir.exists())
            self.assertEqual(remainder_called, [])


if __name__ == "__main__":
    unittest.main()
