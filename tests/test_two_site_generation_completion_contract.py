"""Offline proof/consumer/transaction regressions, NOT Gemini qualification."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import signal
import subprocess
import unittest
from unittest.mock import patch

from scripts import two_site_proposal_runner as runner
from scripts import two_site_proposal_pipeline as pipeline
from scripts import two_site_codex_autopilot as supervisor
from scripts.two_site_generation_completion import normal_observations
from tests import test_two_site_proposal_runner as legacy
from tests.two_site_generation_fixture import seal_completion_fixture


class GenerationCompletionContracts(unittest.TestCase):
    def setUp(self):
        self.fixture = legacy.ProposalContracts("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.task, self.sources = self.fixture.root, self.fixture.task, self.fixture.sources
        # Keep the existing independently tested router real, choose its Gemini cohort.
        self.task["task_id"] = next(
            f"COMPLETION-FIXTURE-{index}" for index in range(1000)
            if pipeline.expected_role_model("worker", f"COMPLETION-FIXTURE-{index}") == runner.WORKER_MODEL
        )
        self.fixture.save_task()
        self.base = self.root / ".local/two-site-refactor/proposals"
        self.base.mkdir(parents=True)
        self.worker = self.base / "worker-proof"
        self.proposal = self.fixture.worker_evidence(self.worker)
        self.result = json.loads(self.proposal.read_bytes())
        self.reviewer = self.base / "reviewer-proof"
        self.reviewer.mkdir()
        runner.write_json(self.reviewer / "result.json", dict(
            self.result, status="VERIFIED", changes=[],
        ))
        runner.write_json(self.reviewer / "task.json", {
            "task": self.task, "sources": self.sources, "source_head": "head",
            "proposal": self.proposal.read_text(),
        })
        worker_status = self.status(self.worker)
        runner.write_json(self.reviewer / "status.json", {
            "run_id": self.reviewer.name, "role": "verifier", "state": "VERIFIED",
            "head": "head", "model": runner.MODEL, "conversation_id": "reviewer-fresh",
            "result_sha256": runner.file_sha256(self.reviewer / "result.json"),
            "input_worker_run_id": self.worker.name,
            "input_worker_conversation_id": worker_status["conversation_id"],
            "input_proposal_sha256": worker_status["result_sha256"],
        })
        seal_completion_fixture(self.root, self.reviewer)

    def status(self, directory):
        return json.loads((directory / "status.json").read_bytes())

    def proof(self, directory):
        return json.loads((directory / "transport-completion.json").read_bytes())

    def save_proof(self, directory, proof, status=None):
        body = runner.serialize_json(proof)
        (directory / "transport-completion.json").write_bytes(body)
        (directory / "transport-completion.json").chmod(0o600)
        status = self.status(directory) if status is None else status
        status["completion_proof_sha256"] = runner.sha(body)
        runner.write_json(directory / "status.json", status)

    def gate(self, kind):
        if kind == "runner":
            return runner.load_prior_worker(
                self.root, self.proposal, self.proposal.read_text(),
                self.task, self.sources, "head",
            )[0]
        if kind == "pipeline":
            seen = set()
            result = pipeline.validate_run(
                self.root, self.worker, "worker", self.worker.name, "head",
                self.task, self.sources, seen,
            )[0]
            self.assertEqual(seen, {"worker-session"})
            return result
        return supervisor.validate_gemini_chain(self.root, "head", {
            "gemini_worker_run_id": self.worker.name,
            "gemini_reviewer_run_id": self.reviewer.name,
            "proposal_sha256": runner.file_sha256(self.proposal),
        })

    def recovered(self, directory):
        """Typed SYNTHETIC lifecycle observations; real OS paths tested separately."""
        proof, status = self.proof(directory), self.status(directory)
        facts = proof["observations"]
        facts.update(
            outcome="RECOVERED_COMPLETED_RESULT", normal_exit_observed=False,
            actual_returncode=-signal.SIGKILL, shutdown_timeout_observed=True,
            timeout_kind="POST_FINAL_SHUTDOWN", cleanup_signal=int(signal.SIGKILL),
            signal_attempted=True, owned_group_absent=True,
            observed_close_before_terminal_read=True, post_final_grace_seconds=5.0,
        )
        status.update(cli_returncode=-signal.SIGKILL, cli_normal_exit_observed=False,
                      generation_completion_outcome="RECOVERED_COMPLETED_RESULT")
        self.save_proof(directory, proof, status)

    def test_normal_and_recovered_use_the_same_mandatory_three_gates(self):
        for kind in ("runner", "pipeline", "supervisor"):
            with self.subTest(kind=kind, mode="normal"):
                self.assertEqual(self.gate(kind), self.result)
        self.recovered(self.worker)
        self.recovered(self.reviewer)
        for kind in ("runner", "pipeline", "supervisor"):
            with self.subTest(kind=kind, mode="recovered"):
                self.assertEqual(self.gate(kind), self.result)
        self.assertEqual(self.status(self.worker)["cli_returncode"], -signal.SIGKILL)
        self.assertIs(self.status(self.worker)["cli_normal_exit_observed"], False)

    def test_removing_both_markers_or_the_proof_never_selects_legacy(self):
        for change in ("both-markers", "missing-file", "bad-mode"):
            for kind in ("runner", "pipeline", "supervisor"):
                with self.subTest(change=change, kind=kind):
                    seal_completion_fixture(self.root, self.worker)
                    status = self.status(self.worker)
                    if change == "both-markers":
                        del status["completion_policy"]
                        del status["completion_proof_sha256"]
                        runner.write_json(self.worker / "status.json", status)
                    elif change == "missing-file":
                        (self.worker / "transport-completion.json").unlink()
                    else:
                        (self.worker / "transport-completion.json").chmod(0o644)
                    with self.assertRaises((runner.ProposalError, OSError)):
                        self.gate(kind)

    def test_coherently_rehashed_incomplete_or_ambiguous_recovery_facts_fail(self):
        mutations = {
            "no-stdout-EOF": ("stdout_eof", False),
            "no-stderr-EOF": ("stderr_eof", False),
            "incomplete-input": ("full_input_written", False),
            "no-writer-close": ("input_writer_closed", False),
            "read-before-close": ("observed_close_before_terminal_read", False),
            "unknown-group": ("owned_group_absent", None),
            "not-reaped": ("direct_child_reaped", False),
            "deadline": ("lifecycle_within_capture_deadline", False),
            "natural-nonzero": ("actual_returncode", 23),
            "natural-zero-race": ("actual_returncode", 0),
            "no-signal": ("signal_attempted", False),
            "wrong-timeout": ("timeout_kind", "TOTAL"),
            "wrong-grace": ("post_final_grace_seconds", 0.0),
            "bool-returncode": ("actual_returncode", True),
            "over-deadline": ("overall_timeout_seconds", 916.0),
        }
        for label, (key, value) in mutations.items():
            seal_completion_fixture(self.root, self.worker)
            self.recovered(self.worker)
            proof = self.proof(self.worker)
            proof["observations"][key] = value
            self.save_proof(self.worker, proof)
            for kind in ("runner", "pipeline", "supervisor"):
                with self.subTest(label=label, kind=kind):
                    with self.assertRaises((runner.ProposalError, OSError)):
                        self.gate(kind)

    def test_no_outer_exit_or_boolean_alias_cannot_become_eligible(self):
        for value in (False, 1, None):
            seal_completion_fixture(self.root, self.worker)
            proof = self.proof(self.worker)
            proof["outer_context_exited_normally"] = value
            self.save_proof(self.worker, proof)
            with self.subTest(value=value), self.assertRaises(runner.ProposalError):
                self.gate("runner")

    def test_raw_schema_formatting_drift_rejects_even_with_equal_json(self):
        original = (self.worker / "response-schema.json").read_bytes()
        (self.worker / "response-schema.json").write_bytes(b" " + original)
        self.assertEqual(json.loads(original), json.loads((self.worker / "response-schema.json").read_bytes()))
        proof = self.proof(self.worker)
        proof["response_schema_sha256"] = runner.sha(b" " + original)
        self.save_proof(self.worker, proof)
        for kind in ("runner", "pipeline", "supervisor"):
            with self.subTest(kind=kind), self.assertRaises(runner.ProposalError):
                self.gate(kind)

    def test_recovered_role_cross_and_effort_mutations_fail_after_rehash(self):
        baseline_status = self.status(self.worker)
        for mutation in ("role-model", "arbitrary-effort"):
            runner.write_json(self.worker / "status.json", baseline_status)
            seal_completion_fixture(self.root, self.worker)
            self.recovered(self.worker)
            proof, status = self.proof(self.worker), self.status(self.worker)
            if mutation == "role-model":
                events = [json.loads(line) for line in (self.worker / "stream.jsonl").read_text().splitlines()]
                events[0]["init"]["model"] = runner.MODEL
                raw = ("\n".join(json.dumps(event) for event in events) + "\n").encode()
                (self.worker / "stream.jsonl").write_bytes(raw)
                status["model"] = proof["model"] = runner.MODEL
                proof["observations"]["stdout_bytes"] = len(raw)
                proof["observations"]["stdout_sha256"] = runner.sha(raw)
            else:
                for key in ("effort", "prepared_effort", "generation_effort_requested"):
                    status[key] = "untrusted-effort"
                for key in ("selected_policy_effort", "prepared_effort", "requested_effort"):
                    proof[key] = "untrusted-effort"
            self.save_proof(self.worker, proof, status)
            with self.subTest(mutation=mutation), self.assertRaises(runner.ProposalError):
                self.gate("runner")

    def test_strict_final_checker_rejects_all_late_or_tool_variants(self):
        raw = (self.worker / "stream.jsonl").read_text()
        events = [json.loads(line) for line in raw.splitlines()]
        cid = self.status(self.worker)["conversation_id"]
        def checked(material):
            return runner._validate_final_generation_frame(
                material, self.task, self.sources, "worker", (),
                runner.WORKER_MODEL, cid, original_sources=self.sources,
            )[0]
        self.assertEqual(checked(raw), self.result)
        variants = [raw.rstrip("\n"), raw + "partial", raw + "{}\n",
                    raw + json.dumps(events[-1]) + "\n"]
        for changed in ("num_turns", "tool", "denied", "extra-init", "wrong-cid"):
            sample = deepcopy(events)
            if changed == "num_turns":
                sample[-1]["result"]["num_turns"] = True
            elif changed == "tool":
                sample.insert(1, {"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "finish"}})
            elif changed == "denied":
                sample[-1]["result"]["denied_actions"] = ["tool"]
            elif changed == "extra-init":
                sample.insert(0, deepcopy(sample[0]))
            else:
                sample[0]["conversation_id"] = "other"
            variants.append("\n".join(json.dumps(event) for event in sample) + "\n")
        for value in variants:
            with self.subTest(value=value[:32]), self.assertRaises((runner.ProposalError, ValueError)):
                checked(value)

    def test_correction_delta_and_prior_freshness_rederived_without_pipeline(self):
        directory = self.base / "corrected-worker"
        directory.mkdir()
        prior_text = self.proposal.read_text()
        final = deepcopy(self.result)
        final["changes"][0]["content"] = "assert 2 == 2\n"
        runner.write_json(directory / "result.json", final)
        runner.write_json(directory / "task.json", {
            "task": self.task, "sources": self.sources, "source_head": "head",
            "proposal": prior_text,
            "feedback": {"reviewer": "codex", "reviewed_proposal_sha256": runner.sha(prior_text.encode()),
                         "findings": ["Synthetic correction."], "test_evidence": []},
        })
        runner.write_json(directory / "status.json", {
            "run_id": directory.name, "role": "worker", "state": "PROPOSAL_READY",
            "head": "head", "model": runner.WORKER_MODEL, "conversation_id": "corrected-fresh",
            "result_sha256": runner.file_sha256(directory / "result.json"),
            "input_worker_run_id": self.worker.name, "input_worker_conversation_id": "worker-session",
            "input_proposal_sha256": runner.sha(prior_text.encode()),
        })
        seal_completion_fixture(self.root, directory)
        def read():
            return runner.validate_generation_completion(
                self.root, directory, task=self.task, sources=self.sources, role="worker",
                status=self.status(directory), result=final,
            )
        self.assertIsNotNone(read()["candidate_sources_sha256"])
        baseline_status = self.status(directory)
        for field, value in (("input_worker_conversation_id", "corrected-fresh"),
                             ("input_worker_run_id", directory.name)):
            runner.write_json(directory / "status.json", baseline_status)
            seal_completion_fixture(self.root, directory)
            status, proof = self.status(directory), self.proof(directory)
            status[field] = value
            proof["prior_worker_identity"]["conversation_id" if field.endswith("conversation_id") else "run_id"] = value
            self.save_proof(directory, proof, status)
            with self.subTest(field=field), self.assertRaises(runner.ProposalError):
                read()
        runner.write_json(directory / "status.json", baseline_status)
        seal_completion_fixture(self.root, directory)
        delta = json.loads((directory / "delta-result.json").read_bytes())
        delta["changes"][0]["content"] = "assert 3 == 3\n"
        delta_bytes = runner.serialize_json(delta)
        (directory / "delta-result.json").write_bytes(delta_bytes)
        proof, status = self.proof(directory), self.status(directory)
        proof["delta_result_sha256"] = status["delta_result_sha256"] = runner.sha(delta_bytes)
        self.save_proof(directory, proof, status)
        with self.assertRaises(runner.ProposalError):
            read()


class CompletionPublicationContracts(unittest.TestCase):
    def test_proof_file_and_directory_dual_faults_preserve_first_error(self):
        for directory_failure in (False, True):
            primary, secondary = RuntimeError("first fsync/write"), OSError("close cleanup")
            with self.subTest(directory=directory_failure), \
                 patch.object(runner.os, "open", side_effect=[91, 92]), \
                 patch.object(runner.os, "write", side_effect=(lambda fd, body: len(body)) if directory_failure else primary), \
                 patch.object(runner.os, "fsync", side_effect=[None, primary] if directory_failure else None), \
                 patch.object(runner.os, "close", side_effect=[None, secondary] if directory_failure else secondary):
                with self.assertRaises(RuntimeError) as caught:
                    runner._publish_generation_proof(Path("/synthetic/proof.json"), {})
                self.assertIs(caught.exception, primary)

    def fixture(self):
        fixture = legacy.ProposalContracts("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_context_cleanup_failure_prevents_result_proof_and_ready(self):
        fixture = self.fixture()
        original = runner.proposal_cli_context
        @contextmanager
        def failing_context(*args, **kwargs):
            with original(*args, **kwargs) as value:
                yield value
            raise RuntimeError("context exit failed")
        with patch.object(runner, "proposal_cli_context", failing_context):
            with self.assertRaisesRegex(RuntimeError, "context exit failed"):
                fixture.invoke_fake_cli(run_id="exit-failed")
        out = fixture.root / ".local/two-site-refactor/proposals/exit-failed"
        self.assertEqual(json.loads((out / "status.json").read_bytes())["state"], "BLOCKED")
        self.assertFalse((out / "result.json").exists())
        self.assertFalse((out / "transport-completion.json").exists())

    def test_first_capture_failure_survives_context_unwind_failure(self):
        fixture = self.fixture()
        original = runner.proposal_cli_context
        @contextmanager
        def failing_context(*args, **kwargs):
            try:
                with original(*args, **kwargs) as value:
                    yield value
            finally:
                raise RuntimeError("secondary context failure")
        primary = KeyboardInterrupt("original capture failure")
        def capture_failure(process, wire, stdout):
            raise primary
        with patch.object(runner, "proposal_cli_context", failing_context):
            with self.assertRaises(KeyboardInterrupt) as caught:
                fixture.invoke_fake_cli(run_id="dual-failed", capture_completion=capture_failure)
        self.assertIs(caught.exception, primary)
        out = fixture.root / ".local/two-site-refactor/proposals/dual-failed"
        self.assertEqual(json.loads((out / "status.json").read_bytes())["state"], "BLOCKED")
        self.assertFalse((out / "transport-completion.json").exists())

    def test_post_disposal_schema_byte_drift_cannot_publish_eligibility(self):
        fixture = self.fixture()
        original = runner.proposal_cli_context
        @contextmanager
        def changed_context(*args, **kwargs):
            with original(*args, **kwargs) as value:
                yield value
            schema_path = fixture.root / ".local/two-site-refactor/proposals/late-schema/response-schema.json"
            schema_path.write_bytes(b" " + schema_path.read_bytes())
        with patch.object(runner, "proposal_cli_context", changed_context):
            with self.assertRaisesRegex(runner.ProposalError, "dispatched response-schema bytes changed"):
                fixture.invoke_fake_cli(run_id="late-schema")
        out = fixture.root / ".local/two-site-refactor/proposals/late-schema"
        self.assertEqual(json.loads((out / "status.json").read_bytes())["state"], "BLOCKED")
        self.assertFalse((out / "transport-completion.json").exists())

    def test_late_stop_after_typed_recovery_blocks_publication_without_dispatch(self):
        fixture = self.fixture()
        events = fixture.events()
        events[0]["init"]["model"] = runner.WORKER_MODEL
        events[-1]["result"]["num_turns"] = 1
        checks = []
        def stop_guard():
            checks.append(True)
            if len(checks) >= 2:
                raise RuntimeError("STOP fence")
        def synthetic_recovered(process, wire, stdout):
            facts = normal_observations(0, wire, stdout, b"", 45)
            facts.update(outcome="RECOVERED_COMPLETED_RESULT", normal_exit_observed=False,
                         actual_returncode=-signal.SIGKILL, shutdown_timeout_observed=True,
                         timeout_kind="POST_FINAL_SHUTDOWN", cleanup_signal=int(signal.SIGKILL),
                         signal_attempted=True, owned_group_absent=True,
                         observed_close_before_terminal_read=True, post_final_grace_seconds=5.0)
            return facts
        with self.assertRaisesRegex(RuntimeError, "STOP fence"):
            fixture.invoke_fake_cli(
                run_id="late-stop", raw_output=("\n".join(json.dumps(event) for event in events) + "\n").encode(),
                process_returncode=-signal.SIGKILL,
                capture_completion=synthetic_recovered, pre_provider_check=stop_guard,
            )
        out = fixture.root / ".local/two-site-refactor/proposals/late-stop"
        self.assertEqual(len(checks), 2)
        self.assertEqual(json.loads((out / "status.json").read_bytes())["state"], "BLOCKED")
        self.assertFalse((out / "result.json").exists())
        self.assertFalse((out / "transport-completion.json").exists())

    def test_post_context_stop_guard_is_reached_after_normal_disposal(self):
        fixture = self.fixture()
        original = runner.proposal_cli_context
        exited = []
        checks = []
        @contextmanager
        def observed_context(*args, **kwargs):
            with original(*args, **kwargs) as value:
                yield value
            exited.append(True)
        def stop_guard():
            checks.append(bool(exited))
            if exited:
                raise RuntimeError("post-context STOP fence")
        events = fixture.events()
        events[0]["init"]["model"] = runner.WORKER_MODEL
        events[-1]["result"]["num_turns"] = 1
        raw = ("\n".join(json.dumps(event) for event in events) + "\n").encode()
        def synthetic_recovered(process, wire, stdout):
            facts = normal_observations(0, wire, stdout, b"", 45)
            facts.update(outcome="RECOVERED_COMPLETED_RESULT", normal_exit_observed=False,
                         actual_returncode=-signal.SIGKILL, shutdown_timeout_observed=True,
                         timeout_kind="POST_FINAL_SHUTDOWN", cleanup_signal=int(signal.SIGKILL),
                         signal_attempted=True, owned_group_absent=True,
                         observed_close_before_terminal_read=True, post_final_grace_seconds=5.0)
            return facts
        with patch.object(runner, "proposal_cli_context", observed_context):
            with self.assertRaisesRegex(RuntimeError, "post-context STOP fence"):
                fixture.invoke_fake_cli(
                    run_id="post-context-stop", raw_output=raw,
                    process_returncode=-signal.SIGKILL, capture_completion=synthetic_recovered,
                    pre_provider_check=stop_guard,
                )
        self.assertEqual(exited, [True])
        self.assertEqual(checks, [False, False, True])
        out = fixture.root / ".local/two-site-refactor/proposals/post-context-stop"
        self.assertEqual(json.loads((out / "status.json").read_bytes())["state"], "BLOCKED")
        self.assertFalse((out / "result.json").exists())
        self.assertFalse((out / "transport-completion.json").exists())
