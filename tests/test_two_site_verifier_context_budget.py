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


from tests.two_site_generation_fixture import install_current_contract, seal_completion_fixture


SCHEMA_MARKER = "\nREQUIRED_RESPONSE_SCHEMA_DATA:\n"
SCHEMA_FOOTER = (
    "\nReturn exactly one JSON object directly, no Markdown or fences. "
    "The same exact schema is supplied to the CLI; no tool, shell or file action is authorized.\n"
)
# Independent fixed verifier envelope; this fixture tests budgets, not profile or receipt validity.
EXPECTED_VERIFIER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "covered_requirements", "findings", "changes"],
    "properties": {
        "status": {"type": "string", "enum": ["VERIFIED", "CHANGES_REQUIRED", "BLOCKED"]},
        "summary": {"type": "string"},
        "covered_requirements": {"type": "array", "items": {"type": "integer"}},
        "findings": {"type": "array", "items": {"type": "string"}},
        "changes": {"type": "array", "maxItems": 0,
                    "items": {"type": "object", "additionalProperties": False}},
    },
}


class PreProviderExecutionIntercepted(Exception):
    """Raised after complete prompt/schema construction, before profile or Popen."""


class TwoSiteVerifierContextBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(runner.MAX_CONTEXT, 160000)
        self.assertEqual(runner.MAX_REVIEW_TRANSPORT_BYTES, 160000)
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
        install_current_contract(tmp_dir)
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

        (worker_dir / "result.json").write_text(proposal_text, encoding="utf-8")
        seal_completion_fixture(tmp_dir, worker_dir)
        return proposal_path, proposal_dict, proposal_sha

    def _intercept_provider(
        self,
        cli_context_calls: list,
        captured_data: dict,
    ):
        # Observe the real first response-schema read. Never run profile preparation,
        # git-init, a version probe, or a provider; Popen remains an independent tripwire.
        real_read = runner.bounded_file_bytes

        @contextlib.contextmanager
        def fake_cli_context(root_path, cli=None):
            cli_context_calls.append((root_path, cli))
            proposals_dir = root_path / ".local/two-site-refactor/proposals"
            proposals_dir.mkdir(parents=True, exist_ok=True)

            def schema_read(root, path, limit, label):
                body = real_read(root, path, limit, label)
                if path.name != "response-schema.json" or label != "response schema":
                    return body
                self.assertEqual(root, root_path)
                self.assertEqual(limit, runner.MAX_CONTEXT)
                self.assertEqual(path.parent.parent, proposals_dir)
                self.assertNotIn("schema_read_args", captured_data)
                prompt_path, status_path = path.parent / "prompt.txt", path.parent / "status.json"
                prompt_raw = real_read(root, prompt_path, 2 * runner.MAX_CONTEXT, "budget fixture prompt")
                status_raw = real_read(root, status_path, runner.MAX_CONTEXT, "budget fixture status")
                expected_schema_raw = (json.dumps(EXPECTED_VERIFIER_SCHEMA, ensure_ascii=False, indent=2) + "\n").encode()
                self.assertEqual(body, expected_schema_raw)
                for observed_path, observed, cap in (
                    (path, body, runner.MAX_CONTEXT),
                    (prompt_path, prompt_raw, 2 * runner.MAX_CONTEXT),
                    (status_path, status_raw, runner.MAX_CONTEXT),
                ):
                    self.assertEqual(real_read(root, observed_path, cap, "budget fixture immutable reread"), observed)
                captured_data.update(
                    schema_read_args=(root, path, limit, label), artifact_stage="response-schema-first-read",
                    schema_raw=body, prompt_text=prompt_raw.decode("utf-8"),
                    status_json=json.loads(status_raw.decode("utf-8")),
                )
                self.assertNotIn("runtime_profile", captured_data["status_json"])
                self.assertNotIn("cli_pid", captured_data["status_json"])
                raise PreProviderExecutionIntercepted()

            with mock.patch.object(runner, "bounded_file_bytes", side_effect=schema_read):
                yield (
                    proposals_dir,
                    root_path / "fake_cli",
                    {
                        "path": "fake_cli",
                        "version": runner.EXPECTED_CLI_VERSION,
                        "sha256": runner.EXPECTED_CLI_SHA256,
                    },
                )

        def fake_popen(*args, **kwargs):
            raise AssertionError("budget fixture crossed the pre-profile/schema-read boundary")

        return fake_cli_context, fake_popen

    def _captured_context(self, captured_data: dict) -> dict:
        text = captured_data["prompt_text"]
        self.assertEqual(text.count("\nCONTEXT_JSON:\n"), 1)
        self.assertEqual(text.count(SCHEMA_MARKER), 1)
        context_and_schema = text.split("\nCONTEXT_JSON:\n", 1)[1]
        context_text, schema_tail = context_and_schema.split(SCHEMA_MARKER, 1)
        schema, end = json.JSONDecoder().raw_decode(schema_tail)
        self.assertEqual(schema, EXPECTED_VERIFIER_SCHEMA)
        self.assertEqual(schema_tail[end:], SCHEMA_FOOTER)
        self.assertEqual(json.loads(captured_data["schema_raw"].decode("utf-8")), schema)
        self.assertEqual(captured_data["artifact_stage"], "response-schema-first-read")
        return json.loads(context_text)

    def test_boundary_1_task_source_snapshot_construction(self) -> None:
        """Boundary 1: Task/source snapshot ceiling (MAX_CONTEXT = 160,000 bytes)."""
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td).resolve()
            _, task, sources, head = self._setup_workspace(tmp_dir)

            task_file = tmp_dir / "task.json"

            # Measure base size with empty padding in goal
            task["goal"] = "Gé"
            base_snapshot = json.dumps({"task": task, "sources": sources}).encode("utf-8")
            pad_160k = runner.MAX_CONTEXT - len(base_snapshot)
            self.assertGreaterEqual(pad_160k, 0)

            # Exact accepted boundary: 160,000 bytes
            task["goal"] = "Gé" + ("x" * pad_160k)
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
                mock_popen.assert_not_called()
                self.assertIn("prompt_text", captured_data)
                self.assertEqual(captured_data["schema_read_args"][1],
                                 tmp_dir / ".local/two-site-refactor/proposals/run-b1-exact/response-schema.json")
                ctx = self._captured_context(captured_data)
                self.assertEqual(ctx["task"]["task_id"], task["task_id"])
                self.assertEqual(ctx["sources"], sources)
                self.assertEqual(ctx["proposal"]["canonical_proposal_sha256"], exact_sha)
                self.assertEqual(
                    captured_data["status_json"]["input_proposal_sha256"], exact_sha
                )

            # One-byte overflow boundary: 160,001 bytes
            task["goal"] = "Gé" + ("x" * (pad_160k + 1))
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
                summary="Sé",
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
            exact_summary = "Sé" + ("x" * pad_160k)
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
            overflow_summary = "Sé" + ("x" * (pad_160k + 1))
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
                mock_popen.assert_not_called()
                self.assertIn("prompt_text", captured_data)
                self.assertEqual(captured_data["schema_read_args"][1],
                                 tmp_dir / ".local/two-site-refactor/proposals/run-b2-exact/response-schema.json")
                prompt_text = captured_data["prompt_text"]
                ctx = self._captured_context(captured_data)
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

            template_text = "# VERIFIER TASK é\nVerify the candidate proposal."

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
                + SCHEMA_MARKER
                + json.dumps(EXPECTED_VERIFIER_SCHEMA, ensure_ascii=False, separators=(",", ":"))
                + SCHEMA_FOOTER
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
                mock_popen.assert_not_called()
                self.assertIn("prompt_text", captured_data)
                self.assertEqual(captured_data["schema_read_args"][1],
                                 tmp_dir / ".local/two-site-refactor/proposals/run-b3-exact/response-schema.json")
                exact_prompt_bytes = captured_data["prompt_text"].encode("utf-8")
                self.assertEqual(len(exact_prompt_bytes), 2 * runner.MAX_CONTEXT)
                self.assertGreater(len(exact_prompt_bytes), len(captured_data["prompt_text"]))
                ctx = self._captured_context(captured_data)
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
            overflow_material = dict(base_prompt_mat, pre_review_test_evidence=evidence_data)
            overflow_prompt = (template_text + "\nROLE: verifier\nCONTEXT_JSON:\n"
                + json.dumps(overflow_material, ensure_ascii=False) + SCHEMA_MARKER
                + json.dumps(EXPECTED_VERIFIER_SCHEMA, ensure_ascii=False, separators=(",", ":")) + SCHEMA_FOOTER)
            self.assertEqual(len(overflow_prompt.encode("utf-8")), 2 * runner.MAX_CONTEXT + 1)
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
