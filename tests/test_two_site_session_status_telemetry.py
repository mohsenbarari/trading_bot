"""Hermetic tests for session gate status telemetry."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

try:
    from scripts.two_site_proposal_runner import (
        MAX_SESSION_FLOW_SYMBOLS,
        MAX_SESSION_QUERY_HINTS,
        ProposalError,
        derive_session_gate,
        file_sha256,
        run,
    )
except ImportError:
    from two_site_proposal_runner import (
        MAX_SESSION_FLOW_SYMBOLS,
        MAX_SESSION_QUERY_HINTS,
        ProposalError,
        derive_session_gate,
        file_sha256,
        run,
    )


class SessionGateStatusTelemetryTests(unittest.TestCase):
    def test_derive_session_gate_not_requested(self) -> None:
        expected = {
            "state": "NOT_REQUESTED",
            "activation_reason": "NOT_REQUESTED",
            "root_count": 0,
            "required_symbol_count": 0,
            "query_operation_count": 0,
        }
        self.assertEqual(derive_session_gate({"dependency_context": {}}), expected)
        self.assertEqual(
            derive_session_gate({"dependency_context": {"session_context": None}}),
            expected,
        )
        self.assertEqual(
            derive_session_gate({
                "dependency_context": {
                    "session_context": {
                        "requested": False,
                        "activation_reason": "NOT_REQUESTED",
                        "root_symbols": [],
                        "required_symbol_count": 0,
                        "query_operations": [],
                    }
                }
            }),
            expected,
        )

    def test_derive_session_gate_requested_and_passed(self) -> None:
        explicit_notes = {
            "dependency_context": {
                "session_context": {
                    "requested": True,
                    "activation_reason": "EXPLICIT_DECLARATION",
                    "root_symbols": ["api/v1/orders.py:create_order"],
                    "required_symbol_count": 4,
                    "query_operations": [{"op": "execute"}],
                    "missing_symbols": [],
                    "unresolved_symbols": [],
                    "omitted_required_symbol_count": 0,
                    "omitted_query_operation_count": 0,
                    "omitted_missing_symbol_count": 0,
                    "omitted_unresolved_symbol_count": 0,
                }
            }
        }
        self.assertEqual(
            derive_session_gate(explicit_notes),
            {
                "state": "REQUESTED_AND_PASSED",
                "activation_reason": "EXPLICIT_DECLARATION",
                "root_count": 1,
                "required_symbol_count": 4,
                "query_operation_count": 1,
            },
        )

        legacy_notes = {
            "dependency_context": {
                "session_context": {
                    "requested": True,
                    "activation_reason": "LEGACY_HEURISTIC",
                    "root_symbols": [
                        "api/v1/orders.py:create_order",
                        "api/v1/users.py:get_user",
                    ],
                    "required_symbol_count": 12,
                    "query_operations": [{"op": "execute"}, {"op": "scalar"}],
                    "missing_symbols": [],
                    "unresolved_symbols": [],
                    "omitted_required_symbol_count": 0,
                    "omitted_query_operation_count": 0,
                    "omitted_missing_symbol_count": 0,
                    "omitted_unresolved_symbol_count": 0,
                }
            }
        }
        self.assertEqual(
            derive_session_gate(legacy_notes),
            {
                "state": "REQUESTED_AND_PASSED",
                "activation_reason": "LEGACY_HEURISTIC",
                "root_count": 2,
                "required_symbol_count": 12,
                "query_operation_count": 2,
            },
        )

    def test_derive_session_gate_fails_closed(self) -> None:
        invalid_cases = [
            {},
            {"dependency_context": "invalid"},
            {"dependency_context": {"session_context": "invalid"}},
            {
                "dependency_context": {
                    "session_context": {
                        "requested": True,
                        "activation_reason": "NOT_REQUESTED",
                        "root_symbols": ["a.py:f"],
                        "required_symbol_count": 1,
                        "query_operations": [{}],
                    }
                }
            },
            {
                "dependency_context": {
                    "session_context": {
                        "requested": True,
                        "activation_reason": "EXPLICIT_DECLARATION",
                        "root_symbols": ["a.py:f"],
                        "required_symbol_count": True,
                        "query_operations": [{}],
                    }
                }
            },
            {
                "dependency_context": {
                    "session_context": {
                        "requested": True,
                        "activation_reason": "EXPLICIT_DECLARATION",
                        "root_symbols": ["a.py:f"],
                        "required_symbol_count": MAX_SESSION_FLOW_SYMBOLS + 1,
                        "query_operations": [{}],
                    }
                }
            },
            {
                "dependency_context": {
                    "session_context": {
                        "requested": True,
                        "activation_reason": "EXPLICIT_DECLARATION",
                        "root_symbols": ["a.py:f"] * 9,
                        "required_symbol_count": 1,
                        "query_operations": [{}],
                    }
                }
            },
            {
                "dependency_context": {
                    "session_context": {
                        "requested": True,
                        "activation_reason": "EXPLICIT_DECLARATION",
                        "root_symbols": ["a.py:f"],
                        "required_symbol_count": 1,
                        "query_operations": [{}] * (MAX_SESSION_QUERY_HINTS + 1),
                    }
                }
            },
            {
                "dependency_context": {
                    "session_context": {
                        "requested": True,
                        "activation_reason": "EXPLICIT_DECLARATION",
                        "root_symbols": ["a.py:f"],
                        "required_symbol_count": 1,
                        "query_operations": [{}],
                        "omitted_required_symbol_count": 1,
                    }
                }
            },
            {
                "dependency_context": {
                    "session_context": {
                        "requested": False,
                        "activation_reason": "EXPLICIT_DECLARATION",
                    }
                }
            },
        ]
        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaises(ProposalError):
                    derive_session_gate(case)

    def _setup_run_tree(self, root: Path, test_file: str = "app.py") -> tuple[Path, dict]:
        task_data = {
            "task_id": "test-task",
            "goal": "Ensure session status telemetry",
            "context_files": [test_file],
            "allowed_files": [test_file],
            "requirements": ["Requirement 1"],
            "test_selectors": ["tests/test_app.py"],
        }
        task_path = root / "task_input.json"
        task_path.write_text(json.dumps(task_data), encoding="utf-8")
        app_path = root / test_file
        app_path.write_text("def run():\n    return 42\n", encoding="utf-8")
        return task_path, task_data

    def _run_with_mocks(
        self,
        root: Path,
        task_path: Path,
        role: str,
        run_id: str,
        prep_notes: dict,
        fail_stream: bool = False,
        proposal: Path | None = None,
        observed_running: list[dict] | None = None,
    ) -> Path:
        local_proposals = root / ".local/two-site-refactor/proposals"
        local_proposals.mkdir(parents=True, exist_ok=True)
        dummy_cli = root / "fake_cli"
        dummy_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        @contextlib.contextmanager
        def fake_cli_ctx(r: Path, c: Path | None = None):
            yield local_proposals, dummy_cli, {
                "path": str(dummy_cli),
                "version": "1.1.27",
                "sha256": "0" * 64,
            }

        def fake_capture(proc, input_bytes, stdout_path, stderr_path, timeout, **kwargs):
            status_path = stdout_path.parent / "status.json"
            if observed_running is not None and status_path.exists():
                observed_running.append(json.loads(status_path.read_text(encoding="utf-8")))
            stdout_path.write_bytes(b'{"event":"init"}\n')
            stderr_path.write_bytes(b"")

        app_sha = file_sha256(root / "app.py")
        if role == "worker":
            provider_result = {
                "status": "PROPOSAL_READY",
                "summary": "Completed change",
                "covered_requirements": [1],
                "findings": [],
                "changes": [{
                    "path": "app.py",
                    "original_sha256": app_sha,
                    "content": "def run():\n    return 99\n",
                }],
            }
        else:
            provider_result = {
                "status": "VERIFIED",
                "summary": "Verified proposal",
                "covered_requirements": [1],
                "findings": [],
                "changes": [],
            }

        class FakeProcess:
            returncode = 0
            pid = 99999
            stdin = MagicMock()
            stdout = MagicMock()
            stderr = MagicMock()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

        with patch("scripts.two_site_proposal_runner.validate_runtime_contract"), \
             patch("scripts.two_site_proposal_runner.git", side_effect=lambda *args: "a" * 40 if args[1] == "rev-parse" else ""), \
             patch("scripts.two_site_proposal_runner.enrich_repository_dependency_context", return_value=prep_notes), \
             patch("scripts.two_site_proposal_runner.validate_task_readiness"), \
             patch("scripts.two_site_proposal_runner.proposal_cli_context", side_effect=fake_cli_ctx), \
             patch("scripts.two_site_proposal_runner.cli_identity", return_value={"path": str(dummy_cli), "version": "1.1.27", "sha256": "0" * 64}), \
             patch("scripts.two_site_proposal_runner.subprocess.Popen", return_value=FakeProcess()), \
             patch("scripts.two_site_proposal_runner.capture_bounded_process", side_effect=fake_capture), \
             patch("scripts.two_site_proposal_runner._parse_stream") as mock_parse:
            if fail_stream:
                mock_parse.side_effect = ProposalError("CLI parse failed")
            else:
                mock_parse.return_value = (
                    provider_result,
                    {"conversation_id": f"conv-{run_id}", "usage": {"prompt_tokens": 10}},
                )
            return run(root, task_path, role, run_id, 30, cli=dummy_cli, proposal=proposal)

    def test_run_status_telemetry_lifecycle_and_prompt_absence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_path, _ = self._setup_run_tree(root)
            explicit_notes = {
                "dependency_context": {
                    "session_context": {
                        "requested": True,
                        "activation_reason": "EXPLICIT_DECLARATION",
                        "root_symbols": ["api/v1/orders.py:create_order"],
                        "required_symbol_count": 3,
                        "query_operations": [{"op": "execute"}],
                        "missing_symbols": [],
                        "unresolved_symbols": [],
                        "omitted_required_symbol_count": 0,
                        "omitted_query_operation_count": 0,
                        "omitted_missing_symbol_count": 0,
                        "omitted_unresolved_symbol_count": 0,
                    }
                }
            }
            expected_gate = {
                "state": "REQUESTED_AND_PASSED",
                "activation_reason": "EXPLICIT_DECLARATION",
                "root_count": 1,
                "required_symbol_count": 3,
                "query_operation_count": 1,
            }

            # 1. RUNNING and PROPOSAL_READY rewrite on worker success
            observed_running: list[dict] = []
            out_worker = self._run_with_mocks(
                root,
                task_path,
                "worker",
                "run-worker-1",
                explicit_notes,
                observed_running=observed_running,
            )
            self.assertTrue(observed_running)
            self.assertEqual(observed_running[0]["state"], "RUNNING")
            self.assertEqual(observed_running[0]["session_gate"], expected_gate)

            status_worker = json.loads((out_worker / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status_worker["state"], "PROPOSAL_READY")
            self.assertEqual(status_worker["session_gate"], expected_gate)

            # Ensure session_gate is ABSENT from prompt text / model input
            prompt_text = (out_worker / "prompt.txt").read_text(encoding="utf-8")
            self.assertNotIn("session_gate", prompt_text)
            self.assertNotIn("REQUESTED_AND_PASSED", prompt_text)

            # 2. VERIFIED rewrite and identical telemetry across role/run IDs
            out_verifier = self._run_with_mocks(
                root,
                task_path,
                "verifier",
                "run-verifier-1",
                explicit_notes,
                proposal=out_worker / "result.json",
            )
            status_verifier = json.loads((out_verifier / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status_verifier["state"], "VERIFIED")
            self.assertEqual(status_verifier["session_gate"], expected_gate)
            self.assertEqual(status_verifier["session_gate"], status_worker["session_gate"])

            out_worker_2 = self._run_with_mocks(
                root,
                task_path,
                "worker",
                "run-worker-2",
                explicit_notes,
            )
            status_worker_2 = json.loads((out_worker_2 / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status_worker_2["session_gate"], expected_gate)

            # 3. Terminal rewrite for BLOCKED state preserves session_gate
            with self.assertRaises(ProposalError):
                self._run_with_mocks(
                    root, task_path, "worker", "run-blocked", explicit_notes, fail_stream=True
                )
            blocked_status_file = root / ".local/two-site-refactor/proposals/run-blocked/status.json"
            self.assertTrue(blocked_status_file.exists())
            blocked_status = json.loads(blocked_status_file.read_text(encoding="utf-8"))
            self.assertEqual(blocked_status["state"], "BLOCKED")
            self.assertEqual(blocked_status["session_gate"], expected_gate)

            # 4. LEGACY_HEURISTIC REQUESTED_AND_PASSED in run()
            legacy_notes = {
                "dependency_context": {
                    "session_context": {
                        "requested": True,
                        "activation_reason": "LEGACY_HEURISTIC",
                        "root_symbols": ["api/v1/orders.py:create_order"],
                        "required_symbol_count": 2,
                        "query_operations": [{"op": "execute"}],
                        "missing_symbols": [],
                        "unresolved_symbols": [],
                        "omitted_required_symbol_count": 0,
                        "omitted_query_operation_count": 0,
                        "omitted_missing_symbol_count": 0,
                        "omitted_unresolved_symbol_count": 0,
                    }
                }
            }
            out_legacy = self._run_with_mocks(
                root, task_path, "worker", "run-legacy", legacy_notes
            )
            legacy_status = json.loads((out_legacy / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(
                legacy_status["session_gate"],
                {
                    "state": "REQUESTED_AND_PASSED",
                    "activation_reason": "LEGACY_HEURISTIC",
                    "root_count": 1,
                    "required_symbol_count": 2,
                    "query_operation_count": 1,
                },
            )

            # 5. NOT_REQUESTED case in run()
            unreq_notes = {"dependency_context": {}}
            out_unreq = self._run_with_mocks(
                root, task_path, "worker", "run-unreq", unreq_notes
            )
            unreq_status = json.loads((out_unreq / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(
                unreq_status["session_gate"],
                {
                    "state": "NOT_REQUESTED",
                    "activation_reason": "NOT_REQUESTED",
                    "root_count": 0,
                    "required_symbol_count": 0,
                    "query_operation_count": 0,
                },
            )

    def test_preflight_rejection_emits_no_status_or_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_path, _ = self._setup_run_tree(root)
            run_id = "run-preflight-fail"

            with patch("scripts.two_site_proposal_runner.validate_task_readiness", side_effect=ProposalError("preflight validation failed")), \
                 patch("scripts.two_site_proposal_runner.validate_runtime_contract"), \
                 patch("scripts.two_site_proposal_runner.git", return_value=""):
                with self.assertRaises(ProposalError):
                    run(root, task_path, "worker", run_id, 30)

            self.assertFalse((root / ".local/two-site-refactor/proposals" / run_id).exists())


if __name__ == "__main__":
    unittest.main()
