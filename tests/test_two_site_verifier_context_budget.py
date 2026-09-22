"""Hermetic proof of AE01-20A2 verifier context budget boundaries.

Proves three pre-provider boundaries under scripts/two_site_proposal_runner.py:
1. Task/source snapshot construction ceiling (MAX_CONTEXT = 160,000 bytes)
2. Materialized Worker proposal review transport ceiling (MAX_REVIEW_TRANSPORT_BYTES = 160,000 bytes)
3. Complete Verifier prompt envelope ceiling (2 * MAX_CONTEXT = 320,000 bytes)
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

try:
    import scripts.two_site_proposal_runner as runner
except ImportError:
    import two_site_proposal_runner as runner


class PreProviderExecutionIntercepted(Exception):
    """Raised when execution reaches provider invocation without errors."""


class TwoSiteVerifierContextBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
                "TWO_SITE_ASSIGNMENT_ID",
                "TWO_SITE_ASSIGNMENT_SHA256",
                "TWO_SITE_AUTOPILOT_RUN_ID",
            )
        }
        env_patcher = mock.patch.dict(os.environ, clean_env, clear=True)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def _setup_workspace(
        self,
        tmp_dir: Path,
        source_rel: str = "core/sample.py",
        source_bytes: bytes = b"value = 1\n",
    ) -> tuple[Path, dict, dict, str]:
        src_path = tmp_dir / source_rel
        src_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.write_bytes(source_bytes)

        head = "356f828e3d1f6a95abc97bcff8c08ec007dd266c"
        src_sha = hashlib.sha256(source_bytes).hexdigest()
        sources = {source_rel: {"sha256": src_sha, "content": source_bytes.decode("utf-8")}}

        task = {
            "task_id": "AE01-20A2-BUDGET-TEST",
            "goal": "Verify pre-provider verifier context budget boundaries",
            "context_files": [source_rel],
            "allowed_files": [source_rel],
            "requirements": ["Requirement 1"],
            "test_selectors": ["tests/test_sample.py"],
        }
        return tmp_dir, task, sources, head

    def _create_worker_artifacts(
        self,
        tmp_dir: Path,
        task: dict,
        sources: dict,
        head: str,
        materialized_content: str = "value = 2\n",
        summary: str = "Implemented sample change",
        run_id: str = "worker-run-001",
    ) -> tuple[Path, dict, str]:
        source_rel = task["allowed_files"][0]
        worker_dir = tmp_dir / f".local/two-site-refactor/proposals/{run_id}"
        worker_dir.mkdir(parents=True, exist_ok=True)

        proposal_dict = {
            "status": "PROPOSAL_READY",
            "summary": summary,
            "covered_requirements": [1],
            "findings": [],
            "changes": [
                {
                    "path": source_rel,
                    "original_sha256": sources[source_rel]["sha256"],
                    "content": materialized_content,
                }
            ],
        }
        proposal_text = json.dumps(proposal_dict, ensure_ascii=False)
        proposal_sha = hashlib.sha256(proposal_text.encode("utf-8")).hexdigest()
        proposal_path = worker_dir / "proposal.json"
        proposal_path.write_text(proposal_text, encoding="utf-8")

        status_dict = {
            "role": "worker",
            "state": "PROPOSAL_READY",
            "result_sha256": proposal_sha,
            "run_id": run_id,
            "conversation_id": f"conv-{run_id}",
        }
        (worker_dir / "status.json").write_text(
            json.dumps(status_dict, ensure_ascii=False), encoding="utf-8"
        )

        task_snapshot = {"task": task, "source_head": head, "sources": sources}
        (worker_dir / "task.json").write_text(
            json.dumps(task_snapshot, ensure_ascii=False), encoding="utf-8"
        )

        return proposal_path, proposal_dict, proposal_sha

    def _intercept_provider(
        self,
        cli_context_calls: list,
        captured_data: dict,
    ):
        @contextlib.contextmanager
        def fake_cli_context(root_path, cli=None):
            cli_context_calls.append((root_path, cli))
            proposals_dir = root_path / ".local/two-site-refactor/proposals"
            proposals_dir.mkdir(parents=True, exist_ok=True)
            yield (
                proposals_dir,
                root_path / "fake_cli",
                {
                    "path": "fake_cli",
                    "version": runner.EXPECTED_CLI_VERSION,
                    "sha256": runner.EXPECTED_CLI_SHA256,
                },
            )

        def fake_popen(cmd, **kwargs):
            cwd = Path(kwargs.get("cwd", "."))
            out_dir = cwd.parent
            prompt_file = out_dir / "prompt.txt"
            if prompt_file.is_file():
                captured_data["prompt_text"] = prompt_file.read_text(encoding="utf-8")
            status_file = out_dir / "status.json"
            if status_file.is_file():
                captured_data["status_json"] = json.loads(
                    status_file.read_text(encoding="utf-8")
                )
            raise PreProviderExecutionIntercepted()

        return fake_cli_context, fake_popen

    def test_boundary_1_task_source_snapshot_construction(self) -> None:
        """Boundary 1: Task/source snapshot ceiling (MAX_CONTEXT = 160,000 bytes)."""
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td).resolve()
            _, task, sources, head = self._setup_workspace(tmp_dir)

            task_file = tmp_dir / "task.json"

            # Measure base size with empty padding in goal
            task["goal"] = "G"
            base_snapshot = json.dumps({"task": task, "sources": sources}).encode("utf-8")
            pad_160k = runner.MAX_CONTEXT - len(base_snapshot)
            self.assertGreaterEqual(pad_160k, 0)

            # Exact accepted boundary: 160,000 bytes
            task["goal"] = "G" + ("x" * pad_160k)
            exact_snapshot = json.dumps({"task": task, "sources": sources}).encode("utf-8")
            self.assertEqual(len(exact_snapshot), runner.MAX_CONTEXT)
            task_file.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")

            loaded_task, loaded_sources = runner.load_task(tmp_dir, task_file)
            self.assertEqual(loaded_task["task_id"], task["task_id"])
            self.assertEqual(loaded_sources["core/sample.py"]["sha256"], sources["core/sample.py"]["sha256"])

            # Exact under-limit runner.run execution
            cli_context_calls: list = []
            captured_data: dict = {}
            fake_cli_ctx, fake_popen = self._intercept_provider(
                cli_context_calls, captured_data
            )
            exact_prop_path, _, exact_sha = self._create_worker_artifacts(
                tmp_dir, task, sources, head, run_id="worker-run-b1"
            )

            def fake_git(root_path, *args, **kwargs):
                if args and args[0] == "rev-parse":
                    return head
                return ""

            with mock.patch.object(
                runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT
            ), mock.patch.object(
                runner, "git", side_effect=fake_git
            ), mock.patch.object(
                runner, "proposal_cli_context", side_effect=fake_cli_ctx
            ), mock.patch.object(
                runner, "cli_identity", return_value={
                    "path": "fake_cli",
                    "version": runner.EXPECTED_CLI_VERSION,
                    "sha256": runner.EXPECTED_CLI_SHA256,
                }
            ), mock.patch(
                "subprocess.Popen", side_effect=fake_popen
            ) as mock_popen:
                with self.assertRaises(PreProviderExecutionIntercepted):
                    runner.run(
                        root=tmp_dir,
                        task_path=task_file,
                        role="verifier",
                        run_id="run-b1-exact",
                        timeout=300,
                        proposal=exact_prop_path,
                    )
                self.assertEqual(len(cli_context_calls), 1)
                self.assertIn("prompt_text", captured_data)
                ctx = json.loads(captured_data["prompt_text"].split("CONTEXT_JSON:\n", 1)[1])
                self.assertEqual(ctx["task"]["task_id"], task["task_id"])
                self.assertEqual(ctx["sources"], sources)
                self.assertEqual(ctx["proposal"]["canonical_proposal_sha256"], exact_sha)
                self.assertEqual(
                    captured_data["status_json"]["input_proposal_sha256"], exact_sha
                )

            # One-byte overflow boundary: 160,001 bytes
            task["goal"] = "G" + ("x" * (pad_160k + 1))
            overflow_snapshot = json.dumps({"task": task, "sources": sources}).encode("utf-8")
            self.assertEqual(len(overflow_snapshot), runner.MAX_CONTEXT + 1)
            task_file.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(runner.ProposalError) as cm:
                runner.load_task(tmp_dir, task_file)
            self.assertIn("TASK_BUDGET_EXCEEDED", str(cm.exception))

            # Verify deterministic pre-provider rejection in runner.run()
            cli_context_calls.clear()
            captured_data.clear()

            with mock.patch.object(
                runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT
            ), mock.patch.object(
                runner, "git", side_effect=fake_git
            ), mock.patch.object(
                runner, "proposal_cli_context", side_effect=fake_cli_ctx
            ), mock.patch(
                "subprocess.Popen", side_effect=fake_popen
            ) as mock_popen:
                with self.assertRaises(runner.ProposalError) as run_cm:
                    runner.run(
                        root=tmp_dir,
                        task_path=task_file,
                        role="verifier",
                        run_id="run-b1-overflow",
                        timeout=300,
                        proposal=exact_prop_path,
                    )
                self.assertIn("TASK_BUDGET_EXCEEDED", str(run_cm.exception))
                self.assertEqual(len(cli_context_calls), 0)
                mock_popen.assert_not_called()

    def test_boundary_2_materialized_worker_review_transport(self) -> None:
        """Boundary 2: Materialized review transport ceiling (MAX_REVIEW_TRANSPORT_BYTES = 160,000 bytes)."""
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td).resolve()
            _, task, sources, head = self._setup_workspace(tmp_dir)

            task_file = tmp_dir / "task.json"
            task_file.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")

            # Measure padding to hit exact 160,000 bytes on review transport using genuine worker artifacts
            base_prop_path, base_prop_dict, base_sha = self._create_worker_artifacts(
                tmp_dir, task, sources, head,
                materialized_content="value = 999\n",
                summary="S",
                run_id="worker-run-base",
            )

            # Prove distinguishing materialized review transport from raw replacements:
            # build_review_transport creates unified_diff from materialized content, not raw replacements.
            base_transport = runner.build_review_transport(
                base_prop_dict, sources, base_sha
            )
            self.assertIn("unified_diff", base_transport)
            self.assertNotIn("replacements", base_transport)
            self.assertIn("-value = 1", base_transport["unified_diff"])
            self.assertIn("+value = 999", base_transport["unified_diff"])

            base_transport_bytes = runner.serialize_json(base_transport)
            pad_160k = runner.MAX_REVIEW_TRANSPORT_BYTES - len(base_transport_bytes)
            self.assertGreaterEqual(pad_160k, 0)

            # Exact accepted boundary: 160,000 bytes
            exact_summary = "S" + ("x" * pad_160k)
            exact_prop_path, exact_prop_dict, exact_sha = self._create_worker_artifacts(
                tmp_dir, task, sources, head,
                materialized_content="value = 999\n",
                summary=exact_summary,
                run_id="worker-run-exact",
            )
            exact_transport = runner.build_review_transport(
                exact_prop_dict, sources, exact_sha
            )
            self.assertEqual(
                len(runner.serialize_json(exact_transport)),
                runner.MAX_REVIEW_TRANSPORT_BYTES,
            )

            # One-byte overflow boundary: 160,001 bytes
            overflow_summary = "S" + ("x" * (pad_160k + 1))
            overflow_prop_path, overflow_prop_dict, overflow_sha = self._create_worker_artifacts(
                tmp_dir, task, sources, head,
                materialized_content="value = 999\n",
                summary=overflow_summary,
                run_id="worker-run-overflow",
            )
            overflow_transport = runner.build_review_transport(
                overflow_prop_dict, sources, overflow_sha
            )
            self.assertEqual(
                len(runner.serialize_json(overflow_transport)),
                runner.MAX_REVIEW_TRANSPORT_BYTES + 1,
            )

            cli_context_calls: list = []
            captured_data: dict = {}
            fake_cli_ctx, fake_popen = self._intercept_provider(
                cli_context_calls, captured_data
            )

            def fake_git(root_path, *args, **kwargs):
                if args and args[0] == "rev-parse":
                    return head
                return ""

            # Pre-provider test: overflow rejects before proposal_cli_context or Popen
            with mock.patch.object(
                runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT
            ), mock.patch.object(
                runner, "git", side_effect=fake_git
            ), mock.patch.object(
                runner, "proposal_cli_context", side_effect=fake_cli_ctx
            ), mock.patch.object(
                runner, "cli_identity", return_value={
                    "path": "fake_cli",
                    "version": runner.EXPECTED_CLI_VERSION,
                    "sha256": runner.EXPECTED_CLI_SHA256,
                }
            ), mock.patch(
                "subprocess.Popen", side_effect=fake_popen
            ) as mock_popen:
                with self.assertRaises(runner.ProposalError) as cm:
                    runner.run(
                        root=tmp_dir,
                        task_path=task_file,
                        role="verifier",
                        run_id="run-b2-overflow",
                        timeout=300,
                        proposal=overflow_prop_path,
                    )
                self.assertIn("review transport exceeds context budget", str(cm.exception))
                self.assertEqual(len(cli_context_calls), 0)
                mock_popen.assert_not_called()

            # Pre-provider test: exact under-limit accepted and preserves semantics
            cli_context_calls.clear()
            captured_data.clear()

            with mock.patch.object(
                runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT
            ), mock.patch.object(
                runner, "git", side_effect=fake_git
            ), mock.patch.object(
                runner, "proposal_cli_context", side_effect=fake_cli_ctx
            ), mock.patch.object(
                runner, "cli_identity", return_value={
                    "path": "fake_cli",
                    "version": runner.EXPECTED_CLI_VERSION,
                    "sha256": runner.EXPECTED_CLI_SHA256,
                }
            ), mock.patch(
                "subprocess.Popen", side_effect=fake_popen
            ) as mock_popen:
                with self.assertRaises(PreProviderExecutionIntercepted):
                    runner.run(
                        root=tmp_dir,
                        task_path=task_file,
                        role="verifier",
                        run_id="run-b2-exact",
                        timeout=300,
                        proposal=exact_prop_path,
                    )
                self.assertEqual(len(cli_context_calls), 1)
                self.assertIn("prompt_text", captured_data)
                prompt_text = captured_data["prompt_text"]
                ctx = json.loads(prompt_text.split("CONTEXT_JSON:\n", 1)[1])
                self.assertEqual(ctx["proposal"]["canonical_proposal_sha256"], exact_sha)
                self.assertEqual(ctx["sources"], sources)
                self.assertIn("-value = 1", ctx["proposal"]["unified_diff"])
                self.assertIn("+value = 999", ctx["proposal"]["unified_diff"])
                self.assertEqual(
                    captured_data["status_json"]["input_proposal_sha256"], exact_sha
                )
                self.assertEqual(
                    captured_data["status_json"]["review_transport_sha256"],
                    hashlib.sha256(runner.serialize_json(exact_transport)).hexdigest(),
                )

    def test_boundary_3_complete_verifier_prompt_envelope(self) -> None:
        """Boundary 3: Complete prompt envelope ceiling (2 * MAX_CONTEXT = 320,000 bytes)."""
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td).resolve()
            _, task, sources, head = self._setup_workspace(tmp_dir)

            # Keep task snapshot safely under 160,000
            task["goal"] = "G" + ("x" * 100_000)
            task_file = tmp_dir / "task.json"
            task_file.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")

            # Keep review transport safely under 160,000
            prop_path, prop_dict, prop_sha = self._create_worker_artifacts(
                tmp_dir, task, sources, head,
                materialized_content="value = 500\n",
                summary="S" + ("x" * 100_000),
                run_id="worker-run-b3",
            )

            # Test evidence receipt base under 160,000
            evidence_file = tmp_dir / "test_evidence.json"
            evidence_data = {"status": "PASSED", "proposal_sha256": prop_sha, "detail": "D"}
            evidence_file.write_text(json.dumps(evidence_data), encoding="utf-8")

            template_text = "# VERIFIER TASK\nVerify the candidate proposal."

            dummy_notes = {
                "schema_version": 1,
                "exact_blocker_ids": [],
                "warnings": [],
                "literal_hints": [],
                "omitted_literal_count": 0,
                "dependency_context": {
                    "persistence_contract_requested": False,
                    "required_full_model_paths": [],
                    "missing_full_model_paths": [],
                    "omitted_model_path_count": 0,
                    "model_contract_hints": [],
                    "omitted_model_contract_hint_count": 0,
                    "session_context": None,
                },
            }
            rev_trans = runner.build_review_transport(prop_dict, sources, prop_sha)
            base_prompt_mat = {
                "task": task,
                "source_head": head,
                "sources": sources,
                "proposal": rev_trans,
                "pre_review_test_evidence": evidence_data,
                "preparation_notes": dummy_notes,
            }
            base_prompt = (
                template_text
                + "\nROLE: verifier\nCONTEXT_JSON:\n"
                + json.dumps(base_prompt_mat, ensure_ascii=False)
            )
            pad_320k = (2 * runner.MAX_CONTEXT) - len(base_prompt.encode("utf-8"))
            self.assertGreaterEqual(pad_320k, 0)

            # Exact accepted boundary: 320,000 bytes
            evidence_data["detail"] = "D" + ("y" * pad_320k)
            evidence_file.write_text(json.dumps(evidence_data), encoding="utf-8")

            cli_context_calls: list = []
            captured_data: dict = {}
            fake_cli_ctx, fake_popen = self._intercept_provider(
                cli_context_calls, captured_data
            )

            orig_bounded_text = runner.bounded_file_text

            def fake_bounded_text(r, p, limit, label):
                if "prompt template" in label:
                    return template_text
                return orig_bounded_text(r, p, limit, label)

            def fake_git(root_path, *args, **kwargs):
                if args and args[0] == "rev-parse":
                    return head
                return ""

            with mock.patch.object(
                runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT
            ), mock.patch.object(
                runner, "git", side_effect=fake_git
            ), mock.patch.object(
                runner, "bounded_file_text", side_effect=fake_bounded_text
            ), mock.patch.object(
                runner, "task_preparation_notes", return_value=dummy_notes
            ), mock.patch.object(
                runner, "validate_test_receipt", side_effect=lambda receipt, *a, **k: receipt
            ), mock.patch.object(
                runner, "proposal_cli_context", side_effect=fake_cli_ctx
            ), mock.patch.object(
                runner, "cli_identity", return_value={
                    "path": "fake_cli",
                    "version": runner.EXPECTED_CLI_VERSION,
                    "sha256": runner.EXPECTED_CLI_SHA256,
                }
            ), mock.patch(
                "subprocess.Popen", side_effect=fake_popen
            ) as mock_popen:
                with self.assertRaises(PreProviderExecutionIntercepted):
                    runner.run(
                        root=tmp_dir,
                        task_path=task_file,
                        role="verifier",
                        run_id="run-b3-exact",
                        timeout=300,
                        proposal=prop_path,
                        test_evidence=evidence_file,
                    )
                self.assertEqual(len(cli_context_calls), 1)
                self.assertIn("prompt_text", captured_data)
                exact_prompt_bytes = captured_data["prompt_text"].encode("utf-8")
                self.assertEqual(len(exact_prompt_bytes), 2 * runner.MAX_CONTEXT)
                ctx = json.loads(captured_data["prompt_text"].split("CONTEXT_JSON:\n", 1)[1])
                self.assertEqual(ctx["proposal"]["canonical_proposal_sha256"], prop_sha)
                self.assertEqual(ctx["sources"], sources)
                self.assertEqual(ctx["pre_review_test_evidence"]["status"], "PASSED")
                self.assertEqual(
                    captured_data["status_json"]["input_proposal_sha256"], prop_sha
                )
                self.assertEqual(
                    captured_data["status_json"]["review_transport_sha256"],
                    hashlib.sha256(runner.serialize_json(rev_trans)).hexdigest(),
                )

            # One-byte overflow boundary: 320,001 bytes
            evidence_data["detail"] = "D" + ("y" * (pad_320k + 1))
            evidence_file.write_text(json.dumps(evidence_data), encoding="utf-8")
            cli_context_calls.clear()
            captured_data.clear()

            with mock.patch.object(
                runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT
            ), mock.patch.object(
                runner, "git", side_effect=fake_git
            ), mock.patch.object(
                runner, "bounded_file_text", side_effect=fake_bounded_text
            ), mock.patch.object(
                runner, "task_preparation_notes", return_value=dummy_notes
            ), mock.patch.object(
                runner, "validate_test_receipt", side_effect=lambda receipt, *a, **k: receipt
            ), mock.patch.object(
                runner, "proposal_cli_context", side_effect=fake_cli_ctx
            ), mock.patch.object(
                runner, "cli_identity", return_value={
                    "path": "fake_cli",
                    "version": runner.EXPECTED_CLI_VERSION,
                    "sha256": runner.EXPECTED_CLI_SHA256,
                }
            ), mock.patch(
                "subprocess.Popen", side_effect=fake_popen
            ) as mock_popen:
                with self.assertRaises(runner.ProposalError) as cm:
                    runner.run(
                        root=tmp_dir,
                        task_path=task_file,
                        role="verifier",
                        run_id="run-b3-overflow",
                        timeout=300,
                        proposal=prop_path,
                        test_evidence=evidence_file,
                    )
                self.assertIn("split task or feedback into a smaller coherent unit", str(cm.exception))
                self.assertEqual(len(cli_context_calls), 0)
                mock_popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
