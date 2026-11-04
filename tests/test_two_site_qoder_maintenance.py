"""Offline policy/proof admission tests; no CLI/model, live lock or network calls."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import shutil
import unittest
import types
from unittest.mock import Mock
from unittest.mock import patch

from scripts import two_site_qoder_maintenance as maintenance
from scripts import two_site_maintenance_transition_record as codec


def repo(head="1" * 40):
    from scripts import two_site_codex_autopilot as ap
    return {"head": head, "status": "", "protected_refs": {key: "2" * 40 for key in codec.PROTECTED_REFS},
        "control_blobs": {str(key): "3" * 40 for key in ap.CONTROL_PATHS}, "assignment_scope": {
            "assignment_id": "business-assignment", "stage_id": "P1-00b", "sha256": "4" * 64,
            "expires_at": "2026-11-21T00:00:00Z", "allowed_paths": ["tests/test_business.py"], "forbidden_paths": []}}


def record():
    return {"schema_version": "1.0", "kind": maintenance.KIND,
        "prior_checkpoint_sha256": "5" * 64, "prior_head": "0" * 40,
        "new_repository": repo(), "policy_sha256": "6" * 64,
        "maintenance_commits": [{"head": "1" * 40, "parent": "0" * 40,
            "paths": ["scripts/two_site_demo.py"], "worker_run_id": "fresh-qoder-author",
            "reviewer_run_id": "fresh-gemini-reviewer", "proposal_sha256": "7" * 64,
            "test_evidence_sha256": "8" * 64}],
        "evidence": [{"job_id": "maint-" + "9" * 32, "execution_receipt_sha256": "a" * 64}]}


class TestMaintenancePolicy(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="maintenance-offline-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = Path(maintenance.__file__).resolve().parents[1] / maintenance.POLICY_REL
        target = self.root / maintenance.POLICY_REL
        target.parent.mkdir(parents=True); target.write_bytes(source.read_bytes())
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Offline fixture")
        self.git("add", "."); self.git("commit", "-qm", "Fixture authority")

    def git(self, *args):
        return subprocess.check_output(["/usr/bin/git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL)

    def test_policy_is_actual_tracked_bytes_not_private_override(self):
        policy, digest = maintenance.load_policy(self.root)
        self.assertTrue(policy["enabled"])
        self.assertEqual(digest, maintenance.digest((self.root / maintenance.POLICY_REL).read_bytes()))
        (self.root / maintenance.POLICY_REL).write_text(json.dumps({**policy, "max_files": 7}))
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "NOT_COMMITTED"):
            maintenance.load_policy(self.root)

    def test_no_production_main_private_paths_or_self_authority(self):
        policy, _ = maintenance.load_policy(self.root)
        for path in ["core/config.py", "api/auth.py", ".env", ".git/config", "../scripts/two_site_x.py",
            "scripts/two_site_assignment_scope.py", "scripts/two_site_semantic_attempts.py",
            "scripts/two_site_quota_accounting.py", "scripts/two_site_qoder_maintenance.py",
            maintenance.POLICY_REL.as_posix()]:
            with self.subTest(path=path):
                self.assertFalse(maintenance.allowed_path(policy, path))
        for path in ["scripts/two_site_proposal_runner.py", "scripts/two_site_provider_broker.py",
            "tests/test_two_site_focused_regression.py", "docs/refactor/two-server/QODER_AUTOMATION.md"]:
            self.assertTrue(maintenance.allowed_path(policy, path))

    def test_policy_boolean_budgets_rejected(self):
        path = self.root / maintenance.POLICY_REL
        policy = json.loads(path.read_text()); policy["max_files"] = True
        path.write_text(json.dumps(policy)); self.git("add", "."); self.git("commit", "-qm", "Bad fixture")
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "POLICY_BUDGET"):
            maintenance.load_policy(self.root)

    def test_private_reads_reject_links_mode_and_descriptor_drift(self):
        path = self.root / "private.json"; path.write_text("{}\n"); path.chmod(0o600)
        self.assertEqual(maintenance.read(self.root, path.name, private=True), b"{}\n")
        path.chmod(0o644)
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "PRIVATE_MODE"):
            maintenance.read(self.root, path.name, private=True)
        path.chmod(0o600); (self.root / "alias").symlink_to(path)
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "PATH_ALIAS"):
            maintenance.read(self.root, "alias", private=True)

    def test_immutable_publication_never_overwrites_attempt(self):
        maintenance.write_new(self.root, "once.json", {"value": 1})
        with self.assertRaises(FileExistsError):
            maintenance.write_new(self.root, "once.json", {"value": 2})
        self.assertEqual(maintenance.decode(maintenance.read(self.root, "once.json")), {"value": 1})

    def test_job_binds_exact_task_source_policy_and_original_failure(self):
        from scripts import two_site_proposal_runner as runner
        from scripts.two_site_ai_config import AI_CONFIG_REL
        for name, content in [("scripts/two_site_demo.py", "def run(): return 1\n"),
                              ("tests/test_two_site_demo.py", "def test_demo(): assert True\n"),
                              (AI_CONFIG_REL.as_posix(), "{}\n")]:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
        self.git("add", "."); self.git("commit", "-qm", "Fixture public inputs")
        before = repo(self.git("rev-parse", "HEAD").decode().strip())
        task = {"task_id": "automation-fix-fixture", "goal": "Repair actual demo regression; preserve safety.",
            "context_files": ["scripts/two_site_demo.py"], "allowed_files": ["scripts/two_site_demo.py"],
            "requirements": ["Correct one concrete regression."], "test_selectors": ["tests/test_two_site_demo.py"]}
        failed = self.root / maintenance.STATE_REL / "runs/cycle-0001-20261104T000000Z-1"
        failure = {"cycle_id": failed.name, "request_id": "c" * 32}
        with patch.object(maintenance, "repository", return_value=before), \
                patch.object(maintenance, "checkpoint", return_value=({"repository": before}, "d" * 64)), \
                patch.object(maintenance, "inspect_failed_cycle", return_value=failure), \
                patch.object(runner, "validate_task_readiness", return_value={}):
            auth = maintenance.prepare_maintenance_task(self.root, failed, task)
            path = self.root / maintenance.JOBS_REL / auth["job_id"] / "task.json"
            prepared, sources = maintenance.load_maintenance_task(self.root, path, auth)
            self.assertIn("CANONICAL_SOURCE_EXCERPTS_JSON", prepared["goal"])
            self.assertIn(maintenance.POLICY_REL.as_posix(), prepared["context_files"])
            with self.assertRaises(FileExistsError):
                maintenance.prepare_maintenance_task(self.root, failed, task)
            changed = copy.deepcopy(prepared); changed["requirements"] = ["Weaken a guard"]
            with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "JOB_BINDINGS"):
                maintenance.validate_maintenance_authorization(self.root, auth, task=changed, sources=sources,
                    source_head=before["head"])
            (self.root / "scripts/two_site_demo.py").write_text("def run(): return 2\n")
            with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "FAILURE_OR_SOURCE_CHANGED"):
                maintenance.load_maintenance_task(self.root, path, auth)
            # Historical replay proves original Git inputs and cannot admit generation.
            maintenance.load_maintenance_task(self.root, path, auth, evidence_only=True)

    def test_real_210k_controller_unique_replacement_keeps_business_limit(self):
        from scripts import two_site_proposal_runner as runner
        source = Path(runner.__file__).read_text()
        self.assertGreater(len(source.encode()), 210000)
        target = self.root / "scripts/two_site_proposal_runner.py"
        target.parent.mkdir(); target.write_text(source)
        test = self.root / "tests/test_two_site_offline_controller.py"
        test.parent.mkdir(); test.write_text("def test_fixture(): assert True\n")
        self.git("add", "."); self.git("commit", "-qm", "Real public controller fixture")
        policy, _ = maintenance.load_policy(self.root)
        task = {"task_id": "large-controller-offline", "goal": "Repair one concrete parser regression; retain all authority gates.",
            "context_files": [maintenance.POLICY_REL.as_posix()], "allowed_files": [str(target.relative_to(self.root))],
            "requirements": ["Retain source, quota, proof and independent review gates."],
            "test_selectors": [str(test.relative_to(self.root))]}
        sources = maintenance.task_sources(self.root, task, policy)
        self.assertEqual(sources[str(target.relative_to(self.root))]["content"], source)
        self.assertEqual(runner.MAX_CONTEXT, 160000)
        first = source.splitlines(keepends=True)[0]
        self.assertEqual(source.count(first), 1)
        replacements = [{"old": first, "new": first + "# OFFLINE replacement engine canary only.\n"}]
        changed = runner.materialize_replacements(source, replacements)
        self.assertEqual(changed, replacements[0]["new"] + source[len(first):])
        compile(changed, "offline-controller-canary.py", "exec")

    def test_successful_planner_capture_replays_without_second_paid_process(self):
        from scripts import two_site_qoder_driver as qoder
        from scripts import two_site_ai_config as config
        directory = self.root / ".local/two-site-refactor/maintenance/jobs/plan-offline"
        directory.mkdir(parents=True)
        before = {"head": self.git("rev-parse", "HEAD").decode().strip()}
        role = {"provider": "qoder", "model": "offline-configured-model", "reasoning_effort": "max"}
        cli = {"path": "/offline/pinned/qoder", "version": "fixture", "sha256": "a" * 64}
        maintenance.write_new(self.root, directory.relative_to(self.root) / "capture.json",
            {"exit_code": 0, "normal_exit_observed": True, "cli": cli})
        with patch.object(config, "load_runtime_config", return_value={"coordinator": {"path": cli["path"]}}), \
             patch.object(config, "role_settings", return_value=role), \
             patch.object(qoder, "qoder_identity", return_value=cli), \
             patch.object(qoder, "qoder_command", return_value=[cli["path"]]), \
             patch.object(qoder, "finalize_qoder_output", return_value={"decision": "NO_REPAIR", "task": None}) as finalize, \
             patch("scripts.two_site_qoder_review._contained_command", side_effect=lambda _r, _d, command, _e: command), \
             patch.object(maintenance, "repository", return_value=before), \
             patch.object(maintenance, "subprocess", types.SimpleNamespace(Popen=Mock(side_effect=AssertionError("planner cannot replay a paid process")))):
            self.assertIsNone(maintenance.plan_maintenance(self.root, {"closed": "fixture"}, before, directory))
            self.assertIsNone(maintenance.plan_maintenance(self.root, {"closed": "fixture"}, before, directory))
            self.assertEqual(finalize.call_count, 2)
            self.assertIs(finalize.call_args.kwargs["write_result"], False)

    def test_real_job_scope_checkpoint_and_210k_prepared_result_without_auth_stub(self):
        """Actual readers over a disposable public Git/receipt fixture, no providers.

        The canonical accepted checkpoint and failed request are TEST FIXTURES,
        not a claimed genuine project acceptance. No authorization function is
        mocked: real policy/Git/Scope/job/CPP/request custody is exercised.
        """
        import yaml
        from scripts import two_site_codex_autopilot as ap
        from scripts import two_site_proposal_runner as runner
        from scripts import two_site_provider_broker as broker
        from scripts import two_site_maintenance_lkg as lkg
        from scripts.two_site_preparation_contract import PreparationStartup
        (self.root / ".gitignore").write_text(".local/\n")
        self.git("branch", "-M", ap.BRANCH)
        for name in set(ap.CONTROL_PATHS) | set(runner.GEMINI_RUNTIME_REFERENCE_PATHS) | {Path(x) for x in lkg.GOLDENS} | {Path("scripts/two_site_qoder_host.py"), Path("scripts/two_site_maintenance_quota.py")}:
            target = self.root / name; target.parent.mkdir(parents=True, exist_ok=True)
            source = runner.ROOT / name
            # Hidden instructional controls may be absent from a native public
            # snapshot. Their fixture bytes are explicit, not historical claims.
            target.write_bytes(source.read_bytes() if source.is_file() else b"automation-models.json\n")
        order = self.root / "docs/refactor/two-server/execution-order.yaml"
        order.write_text(yaml.safe_dump({"stages": {"P1-00b": {"state": "IN_PROGRESS"}}}))
        test = self.root / "tests/test_two_site_controller_fixture.py"
        test.parent.mkdir(exist_ok=True); test.write_text("def test_fixture(): assert True\n")
        self.git("add", "."); self.git("commit", "-qm", "Public controller admission fixture")
        head = self.git("rev-parse", "HEAD").decode().strip()
        for reference in ap.PROTECTED_REFS:
            self.git("update-ref", reference, head)
        shared = self.root / ".local/two-site-refactor"
        shared.mkdir(parents=True, exist_ok=True)
        aid = "WA-MAINTENANCE-ADMISSION-FIXTURE"
        (shared / "worktrees.yaml").write_text(yaml.safe_dump({"integration": {
            "path": str(self.root), "branch": ap.BRANCH, "active_assignment_id": aid}}))
        assignment = {"id": aid, "status": "READY", "role": "WORKER", "integration_branch": ap.BRANCH,
            "external_access": "NONE", "expires_at": "2099-01-01T00:00:00Z", "stage_id": "P1-00b",
            "allowed_paths": ["tests/test_p1_00b_*.py"], "forbidden_paths": ["scripts/**"]}
        (shared / "assignments").mkdir()
        (shared / "assignments" / (aid + ".md")).write_text("# Fixture\n```yaml\n" + yaml.safe_dump(assignment) + "```\n")
        actual_repo = ap.validate_repository(self.root, require_clean=True)
        accepted = {"schema_version": "1.0", "state": "CONTINUE", "disposition": "NONE", "category": "NONE",
            "summary": "Canonical offline fixture checkpoint only.", "commit": head, "next_action": "Exercise offline admission.",
            "blockers": [], "gemini_worker_run_id": "fixture-author", "gemini_reviewer_run_id": "fixture-reviewer",
            "proposal_sha256": "d" * 64, "head_before": "0" * 40, "head_after": head, "head_changed": True}
        state = self.root / maintenance.STATE_REL; state.mkdir(mode=0o700)
        ap._persist_accepted_checkpoint(state, accepted, actual_repo)
        cycle = "cycle-0001-20261104T000000Z-123"
        (state / "runs").mkdir(mode=0o700)
        run = state / "runs" / cycle; run.mkdir(mode=0o700)
        startup = PreparationStartup(schema_version=1, route="one_shot", cycle_id=cycle,
            source_head=head, assignment_sha256=actual_repo["assignment_scope"].sha256).to_mapping()
        maintenance.write_new(self.root, run.relative_to(self.root) / "preparation-startup.json", startup)
        old_task = shared / "tasks/failed.json"; old_task.parent.mkdir()
        maintenance.write_new(self.root, old_task.relative_to(self.root), {"fixture": "original failed business request"})
        request = {"schema_version": "1.0", "request_id": "a" * 32, "task_path": str(old_task.relative_to(self.root)),
            "task_sha256": maintenance.digest(old_task.read_bytes()), "run_prefix": "failed-offline-fixture", "timeout": 900,
            "source_head": head, "assignment_id": aid, "assignment_sha256": actual_repo["assignment_scope"].sha256,
            "controller_run_id": cycle, "created_at": time.time()}
        broker.publish_cycle_reservation(self.root, request)
        maintenance.write_new(self.root, run.relative_to(self.root) / broker._CYCLE_DISPATCH,
            {"schema_version": "1.0", "request": request, "broker_pid": os.getpid(), "broker_started_at": time.time()})
        _base, _status, _requests, _claimed, responses = broker.broker_paths(self.root)
        maintenance.write_new(self.root, responses.relative_to(self.root) / (request["request_id"] + ".json"),
            {"schema_version": "1.0", "request_id": request["request_id"], "run_prefix": request["run_prefix"],
             "source_head": head, "state": "COMPLETED", "exit_code": 1, "stdout": "offline failed request",
             "stderr": "", "finished_at": time.time()})
        rejected = {key: value for key, value in accepted.items() if key in ap.RESULT_KEYS}
        rejected.update(state="BLOCKED", disposition="RESCOPE", category="INTERNAL_REJECTION", commit=None,
            blockers=["Offline implementation regression fixture"], gemini_worker_run_id=None,
            gemini_reviewer_run_id=None, proposal_sha256=None)
        maintenance.write_new(self.root, run.relative_to(self.root) / "result.json", rejected)
        maintenance.write_new(self.root, run.relative_to(self.root) / "status.json", {"state": "BLOCKED",
            "head_before": head, "head_after": head, "protected_refs": actual_repo["protected_refs"]})
        maintenance.write_new(self.root, maintenance.STATE_REL / "status.json", {"state": "BLOCKED", "head": head,
            "evidence": str(run), "dirty": False, "ambiguous_change": False})
        task = {"task_id": "real-large-admission-fixture", "goal": "Repair one concrete controller fixture fault.",
            "context_files": [maintenance.POLICY_REL.as_posix(), "tests/test_two_site_qoder_author.py"], "allowed_files": ["scripts/two_site_proposal_runner.py"],
            "requirements": ["Preserve every existing authority and proof gate."], "test_selectors": [str(test.relative_to(self.root))]}
        auth = maintenance.prepare_maintenance_task(self.root, run, task)
        task_path = self.root / maintenance.JOBS_REL / auth["job_id"] / "task.json"
        prepared, sources = maintenance.load_maintenance_task(self.root, task_path, auth)
        source = sources[task["allowed_files"][0]]["content"]
        self.assertGreater(len(source.encode()), 210000)
        first = source.splitlines(keepends=True)[0]
        raw = {"status": "PROPOSAL_READY", "summary": "Offline unique-hunk source fixture.",
            "covered_requirements": [1], "findings": [], "changes": [{"path": task["allowed_files"][0],
            "original_sha256": maintenance.digest(source.encode()), "content": None,
            "replacements": [{"old": first, "new": first + "# OFFLINE authorized replacement fixture.\n"}]}]}
        normalized = runner.normalize_result(raw, prepared, sources, "worker", strict_transport=True)
        admitted = runner.validate_prepared_result(self.root, auth, normalized, prepared, sources, "worker", source_head=head)
        self.assertEqual(admitted["changes"][0]["content"], raw["changes"][0]["replacements"][0]["new"] + source[len(first):])
        transport = runner.build_prepared_review_transport(self.root, auth, admitted, prepared, sources,
            source_head=head, proposal_sha256=runner.sha(runner.serialize_json(admitted)), raw_transport=raw)
        self.assertEqual(transport["raw_worker_transport"], raw)
        full = json.dumps({"task": prepared, "sources": sources, "proposal": transport}, ensure_ascii=False)
        self.assertEqual(full.count(json.dumps(source, ensure_ascii=False)), 1)
        self.assertLess(len(full.encode()), 320000)
        # Retained correction/Reviewer snapshots contain full original sources
        # plus canonical prior output. Their larger metadata allowance is NOT a
        # provider input increase: the model receives the proved small delta.
        snapshot = {"task": prepared, "source_head": head, "sources": sources,
            "maintenance_authorization": auth, "author_authorization": auth,
            "proposal": runner.serialize_json(admitted).decode(), "prior_transport": raw,
            "feedback": {"proposal_sha256": runner.sha(runner.serialize_json(admitted)),
                         "findings": ["One offline bounded regression fixture."]}}
        metadata = runner.serialize_json(snapshot)
        self.assertGreater(len(metadata), 3 * 160000)
        self.assertLess(len(metadata), 3 * 320000)
        snapshot_path = self.root / ".local/two-site-refactor/proposals/offline-correction/task.json"
        snapshot_path.parent.mkdir(parents=True, mode=0o700)
        maintenance.write_new(self.root, snapshot_path.relative_to(self.root), snapshot)
        observed_raw, observed_snapshot = runner.read_task_snapshot(self.root, snapshot_path)
        self.assertEqual(maintenance.digest(observed_raw), maintenance.digest(maintenance.canonical(snapshot)))
        self.assertEqual(observed_snapshot, snapshot)
        reviewer_snapshot = {**snapshot, "test_evidence": {"bounded_public_log": "n" * 32768}}
        reviewer_path = snapshot_path.parent.parent / "offline-reviewer/task.json"
        reviewer_path.parent.mkdir(parents=True, mode=0o700)
        maintenance.write_new(self.root, reviewer_path.relative_to(self.root), reviewer_snapshot)
        reviewer_raw, reviewer_observed = runner.read_task_snapshot(self.root, reviewer_path)
        self.assertEqual(reviewer_observed, reviewer_snapshot)
        print("PUBLIC_OFFLINE_BOUNDS=" + json.dumps({"controller_source_bytes": len(source.encode()),
            "regression_source_bytes": len(sources["tests/test_two_site_qoder_author.py"]["content"].encode()),
            "source_once_review_bytes": len(full.encode()), "correction_snapshot_bytes": len(observed_raw),
            "reviewer_snapshot_bytes": len(reviewer_raw), "authenticated_snapshot_cap": 960000,
            "ordinary_input_cap": runner.MAX_CONTEXT}, sort_keys=True))
        spoof = copy.deepcopy(snapshot); spoof["maintenance_authorization"]["sha256"] = "f" * 64
        with self.assertRaises(maintenance.QoderMaintenanceError):
            runner.validate_task_snapshot_bytes(self.root, runner.serialize_json(spoof))
        ordinary_snapshot = {key: value for key, value in snapshot.items() if key != "maintenance_authorization"}
        with self.assertRaisesRegex(runner.ProposalError, "snapshot exceeds"):
            runner.validate_task_snapshot_bytes(self.root, runner.serialize_json(ordinary_snapshot))
        # Synthetic quota SDK over the REAL prepared job/Scope/CPP/snapshot
        # readers. No auth/config/Source reader is stubbed and no model runs.
        from datetime import datetime, timedelta, timezone
        from scripts import two_site_maintenance_quota as quota
        from scripts.two_site_ai_config import role_settings
        quota_base = auth["job_id"] + "-reviewer-0"
        quota_directory = snapshot_path.parent.parent / quota_base
        quota_directory.mkdir(mode=0o700)
        now = datetime.now(timezone.utc)
        reset = (now - timedelta(minutes=5)).replace(microsecond=0)
        started = reset - timedelta(minutes=5)
        reviewer_model = role_settings(self.root, "reviewer")["model"]
        cid = "offline-real-job-zero-token-quota"
        events = [{"event": "init", "conversation_id": cid, "init": {"model": reviewer_model}},
            {"event": "result", "result": {"conversation_id": cid, "status": "ERROR",
             "usage": {"input_tokens": 0, "output_tokens": 0},
             "error": "RESOURCE_EXHAUSTED quota reset at " + reset.strftime("%Y-%m-%dT%H:%M:%SZ")}}]
        quota_stream = ("\n".join(json.dumps(item) for item in events) + "\n").encode()
        quota_evidence = quota.classify_gemini_quota_stream(quota_stream.decode(), reviewer_model, started)
        self.assertEqual(quota_evidence["kind"], "QUOTA_RESET_KNOWN")
        (quota_directory / "stream.jsonl").write_bytes(quota_stream)
        (quota_directory / "stream.jsonl").chmod(0o600)
        (quota_directory / "stderr.txt").write_bytes(b"")
        (quota_directory / "stderr.txt").chmod(0o600)
        maintenance.write_new(self.root, (quota_directory / "task.json").relative_to(self.root), reviewer_snapshot)
        quota_status = {"state": "BLOCKED", "role": "verifier", "head": head, "run_id": quota_base,
            "task_id": prepared["task_id"], "model": reviewer_model, "conversation_id": cid,
            "controller_run_id": maintenance.decode(maintenance.read(self.root,
                maintenance.JOBS_REL / auth["job_id"] / "job.json", maintenance.MAX_BYTES, private=True))["controller_run_id"],
            "started_at": started.timestamp(), "duration_seconds": 1,
            "quota_evidence": quota_evidence, "failure_retained_outputs": {
                "stream.jsonl": {"utf8_bytes": len(quota_stream), "sha256": maintenance.digest(quota_stream)},
                "stderr.txt": {"utf8_bytes": 0, "sha256": maintenance.digest(b"")}}}
        status_sha = maintenance.write_new(self.root, (quota_directory / "status.json").relative_to(self.root), quota_status)
        args = dict(task=prepared, sources=sources, expected_head=head, base_run_id=quota_base,
            attempt_id=quota_base, attempts=(quota_base, quota_base + "-retry-1", quota_base + "-retry-2"),
            quota_evidence=quota_evidence,
            quota_attempt={"role": "verifier", "run_id": quota_base, "status_sha256": status_sha})
        originals = {p.name: p.read_bytes() for p in quota_directory.iterdir()}
        self.assertIs(quota.maintenance_quota_resume_due(self.root, auth, **args), True)
        self.assertIs(quota.maintenance_quota_resume_due(self.root, auth, **args), True)
        self.assertEqual({p.name: p.read_bytes() for p in quota_directory.iterdir()}, originals)
        self.assertFalse((quota_directory.parent / (quota_base + "-retry-1")).exists())
        with self.assertRaises(maintenance.QoderMaintenanceError):
            quota.maintenance_quota_resume_due(self.root, {**auth, "sha256": "f" * 64}, **args)
        assignment_path = shared / "assignments" / (aid + ".md")
        original_assignment = assignment_path.read_bytes()
        assignment_path.write_bytes(original_assignment + b"# offline Scope drift fixture\n")
        try:
            with self.assertRaises(maintenance.QoderMaintenanceError):
                quota.maintenance_quota_resume_due(self.root, auth, **args)
        finally:
            assignment_path.write_bytes(original_assignment)
        wrong = copy.deepcopy(raw); wrong["changes"][0]["replacements"][0]["new"] += "# mismatch\n"
        with self.assertRaises(runner.ProposalError):
            runner.build_prepared_review_transport(self.root, auth, admitted, prepared, sources,
                source_head=head, proposal_sha256=runner.sha(runner.serialize_json(admitted)), raw_transport=wrong)
        self.assertEqual(runner.MAX_CONTEXT, 160000)
        ordinary_path = "tests/test_p1_00b_ordinary_large.py"
        ordinary = {"task_id": "ordinary-too-large-fixture", "goal": aid + ": ordinary scoped fixture.",
            "context_files": [maintenance.POLICY_REL.as_posix(), "scripts/two_site_proposal_runner.py"],
            "allowed_files": [ordinary_path], "requirements": ["One ordinary scenario."], "test_selectors": [ordinary_path]}
        normal_path = shared / "tasks/ordinary-large.json"
        maintenance.write_new(self.root, normal_path.relative_to(self.root), ordinary)
        with patch.dict(os.environ, {"TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED": "1", "TWO_SITE_ASSIGNMENT_ID": aid,
                        "TWO_SITE_ASSIGNMENT_SHA256": actual_repo["assignment_scope"].sha256}):
            with self.assertRaisesRegex(runner.ProposalError, "TASK_BUDGET_EXCEEDED"):
                runner.load_task(self.root, normal_path)
        with tempfile.TemporaryDirectory(prefix="host-lkg-install-fixture-") as temp_store:
            store = Path(temp_store)
            original_write = lkg._write_new
            interrupted = []
            def write_partial_once(path, data):
                if path.name == "two_site_proposal_runner.py" and not interrupted:
                    interrupted.append(path); original_write(path, data[:31])
                    raise OSError("offline interrupted public-code copy")
                return original_write(path, data)
            with patch.object(lkg, "_write_new", side_effect=write_partial_once):
                with self.assertRaisesRegex(OSError, "interrupted public-code"):
                    lkg.install(self.root, store)
            self.assertFalse((store / "current.json").exists())
            first_install = lkg.install(self.root, store)
            self.assertEqual(first_install["head"], head)
            self.assertEqual(lkg.install(self.root, store)["head"], head)
            self.assertEqual(lkg.current_capsule(store)[1]["head"], head)
            prior = head
            for index in range(2):
                (self.root / ("tests/test_p1_00b_capsule_marker_" + str(index) + ".py")).write_text("# public business fixture marker\n")
                self.git("add", "."); self.git("commit", "-qm", "Next offline accepted-source fixture")
                now = self.git("rev-parse", "HEAD").decode().strip()
                receipt = {**accepted, "commit": now, "head_before": prior, "head_after": now}
                ap._persist_accepted_checkpoint(state, receipt, ap.validate_repository(self.root, require_clean=True))
                lkg.install(self.root, store); prior = now
            self.assertEqual(len(list((store / "capsules").iterdir())), 2)
            self.assertFalse((store / "capsules" / head).exists())
            self.assertTrue((store / "retired" / (head + ".json")).is_file())


class TestMaintenanceQuotaObservation(unittest.TestCase):
    def test_wait_retains_same_job_and_original_failed_status_without_replay(self):
        from scripts import two_site_proposal_pipeline as pipeline
        with tempfile.TemporaryDirectory(prefix="maintenance-quota-offline-") as temp:
            root = Path(temp); authorization = {"kind": maintenance.KIND,
                "job_id": "maint-" + "1" * 32, "sha256": "a" * 64}
            directory = root / maintenance.JOBS_REL / authorization["job_id"]
            directory.mkdir(parents=True)
            original = b'{"state":"BLOCKED","original":"immutable failed-cycle fixture"}\n'
            status = root / maintenance.STATE_REL / "status.json"; status.parent.mkdir(parents=True)
            status.write_bytes(original)
            evidence = {"kind": "QUOTA_RESET_UNKNOWN", "provider": "google",
                        "model": "gemini-4-argon-high", "conversation_id": "actual-fixture-session", "reset_at_utc": None}
            error = pipeline.QuotaBlockError(evidence)
            # Provider namespace follows the existing pipeline classifier.
            from scripts.two_site_quota_evidence import provider_for_model
            evidence["provider"] = provider_for_model(evidence["model"])
            error = pipeline.QuotaBlockError(evidence)
            with patch("scripts.two_site_ai_config.role_settings", return_value={"model": evidence["model"]}):
                value = maintenance._retain_quota_wait(root, authorization, error)
                self.assertEqual(maintenance._retain_quota_wait(root, authorization, error), value)
            self.assertEqual(value["state"], "MAINTENANCE_WAITING_QUOTA")
            self.assertEqual(status.read_bytes(), original)
            self.assertEqual(len(list(directory.iterdir())), 1)
            record = maintenance.decode((root / value["wait_receipt"]).read_bytes())
            self.assertEqual(record["authorization"], authorization)
            self.assertEqual(record["effect"], "OBSERVATIONAL_ONLY_NO_REPLAY_OR_RESET")



class TestExplicitTransitionProfile(unittest.TestCase):
    def test_new_profile_roundtrip_and_input_unchanged(self):
        original = record(); old = copy.deepcopy(original)
        raw = codec.serialize_maintenance_transition_record(original)
        self.assertEqual(codec.parse_maintenance_transition_record(raw), original)
        self.assertEqual(original, old)

    def test_legacy_kind_is_not_relabelled_or_broadened(self):
        value = record(); value["kind"] = codec.MAINTENANCE_TRANSITION_KIND
        with self.assertRaises(codec.MaintenanceTransitionRecordError):
            codec.validate_maintenance_transition_record(value)

    def test_unknown_key_duplicate_chain_and_same_reviewer_fail_closed(self):
        for mutate in [lambda x: x.update(extra=True),
                       lambda x: x["maintenance_commits"][0].update(parent="b" * 40),
                       lambda x: x["maintenance_commits"][0].update(reviewer_run_id="fresh-qoder-author"),
                       lambda x: x["evidence"][0].update(execution_receipt_sha256="not-a-proof"),
                       lambda x: x["maintenance_commits"][0].update(paths=["core/config.py"])]:
            value = record(); mutate(value)
            with self.assertRaises(ValueError):
                maintenance.validate_transition_record(value)

    def test_bounded_chain_not_business_counter_reset(self):
        value = record()
        for index in range(1, 5):
            row = copy.deepcopy(value["maintenance_commits"][0]); row["parent"] = value["maintenance_commits"][-1]["head"]
            row["head"] = str(index + 1) * 40
            value["maintenance_commits"].append(row)
            value["evidence"].append({"job_id": "maint-" + str(index) * 32,
                                      "execution_receipt_sha256": "a" * 64})
        value["new_repository"]["head"] = value["maintenance_commits"][-1]["head"]
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "TRANSITION_BUDGET"):
            maintenance.validate_transition_record(value)

    def test_monitor_never_restarts_complete_quota_or_arbitrary_blocked(self):
        with tempfile.TemporaryDirectory(prefix="maintenance-monitor-") as temporary:
            root = Path(temporary)
            with patch.object(maintenance, "load_policy", return_value=({}, "a" * 64)), \
                    patch.object(maintenance, "repository", return_value=repo()), \
                    patch.object(maintenance, "read", return_value=maintenance.canonical({"state": "COMPLETE"})):
                self.assertEqual(maintenance.monitor_action(root), "NOT_ELIGIBLE")
            with patch.object(maintenance, "load_policy", return_value=({}, "a" * 64)), \
                    patch.object(maintenance, "repository", return_value=repo()), \
                    patch.object(maintenance, "read", return_value=maintenance.canonical({"state": "BLOCKED"})), \
                    patch.object(maintenance, "inspect_failed_cycle", side_effect=maintenance.QoderMaintenanceError("NOT_QUALIFIED")):
                with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "NOT_QUALIFIED"):
                    maintenance.monitor_action(root)
            (root / "STOP").touch()
            with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "MAINTENANCE_STOP"):
                maintenance.monitor_action(root)


class TestOwnedEffectReconciliation(unittest.TestCase):
    """Real disposable Git and actual CAS helpers, never a project worktree."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="maintenance-effects-")
        self.addCleanup(self.temporary.cleanup); self.root = Path(self.temporary.name)
        self.git("init", "-q"); self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Offline fixture")
        (self.root / ".gitignore").write_text(".local/\n")
        self.path = "scripts/two_site_demo.py"
        target = self.root / self.path; target.parent.mkdir(); target.write_text("old\n")
        self.git("add", "."); self.git("commit", "-qm", "Before")
        self.before = repo(self.git("rev-parse", "HEAD").decode().strip())
        self.sources = {self.path: {"sha256": maintenance.digest(b"old\n"), "content": "old\n"}}
        self.changes = [{"path": self.path, "is_new": False, "orig_sha": maintenance.digest(b"old\n"),
                         "orig_content": "old\n", "new_content": "approved\n"}]
        self.refs = patch("scripts.two_site_codex_autopilot.ref_snapshot", return_value=self.before["protected_refs"])
        self.scope = patch("scripts.two_site_assignment_scope.load_active_assignment_scope", return_value="scope")
        self.scope_data = patch("scripts.two_site_codex_autopilot.assignment_scope_context",
                                return_value=self.before["assignment_scope"])
        self.refs.start(); self.scope.start(); self.scope_data.start()
        self.addCleanup(self.refs.stop); self.addCleanup(self.scope.stop); self.addCleanup(self.scope_data.stop)

    def git(self, *args):
        return subprocess.check_output(["/usr/bin/git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL)

    def test_original_partial_applied_staged_and_committed_are_exact(self):
        value = maintenance.reconcile_exact_effects(self.root, self.before, self.sources, self.changes)
        self.assertEqual(value["pending_paths"], [self.path])
        maintenance.reconcile_exact_effects(self.root, self.before, self.sources, self.changes, publish=True)
        self.assertEqual((self.root / self.path).read_bytes(), b"approved\n")
        # Reconciliation is idempotent; no new model and no second CAS needed.
        self.assertEqual(maintenance.reconcile_exact_effects(self.root, self.before, self.sources,
            self.changes, publish=True)["pending_paths"], [])
        self.git("add", "--", self.path)
        self.assertFalse(maintenance.reconcile_exact_effects(self.root, self.before, self.sources, self.changes)["committed"])
        self.git("commit", "-qm", "Approved fixture repair")
        self.assertTrue(maintenance.reconcile_exact_effects(self.root, self.before, self.sources, self.changes)["committed"])

    def test_foreign_dirty_source_or_changed_approved_bytes_aborts(self):
        (self.root / self.path).write_text("not approved\n")
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "FOREIGN_SOURCE_BYTES"):
            maintenance.reconcile_exact_effects(self.root, self.before, self.sources, self.changes, publish=True)
        (self.root / self.path).write_text("old\n"); (self.root / "unexpected.py").write_text("user work\n")
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "FOREIGN_DIRTY_SOURCE"):
            maintenance.reconcile_exact_effects(self.root, self.before, self.sources, self.changes, publish=True)
        self.assertEqual((self.root / self.path).read_bytes(), b"old\n")

    def test_unrelated_commit_not_adopted_and_stop_prevents_publish(self):
        (self.root / self.path).write_text("approved\n"); self.git("add", "."); self.git("commit", "-qm", "First")
        self.git("commit", "--allow-empty", "-qm", "Unrelated child")
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "UNRELATED_COMMIT"):
            maintenance.reconcile_exact_effects(self.root, self.before, self.sources, self.changes)
        (self.root / "STOP").touch()
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "MAINTENANCE_STOP"):
            maintenance.reconcile_exact_effects(self.root, self.before, self.sources, self.changes, publish=True)

    def test_phase_marker_same_bytes_only_and_failed_native_is_retained(self):
        value = {"returncode": 1, "isolation": "offline-fixture"}
        first = maintenance.once_or_same(self.root, "native.json", value)
        self.assertEqual(maintenance.once_or_same(self.root, "native.json", value), first)
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "PHASE_CHANGED"):
            maintenance.once_or_same(self.root, "native.json", {"returncode": 0})
        self.assertEqual(maintenance.decode(maintenance.read(self.root, "native.json")), value)

    def test_finish_commit_crash_resumes_without_models_or_native_replay(self):
        """Real Git/effects, mocked genuine-review boundary and native executor only."""
        policy = {"max_files": 8, "native_timeout_seconds": 1200,
                  "allowed_paths": ["scripts/two_site_*.py"], "denied_paths": []}
        auth = {"kind": maintenance.KIND, "job_id": "maint-" + "b" * 32, "sha256": "c" * 64}
        relative = maintenance.JOBS_REL / auth["job_id"]
        (self.root / relative).mkdir(parents=True, mode=0o700)
        maintenance.write_new(self.root, relative / "pipeline-ready.json", {"fixture": "review-boundary"})
        maintenance.write_new(self.root, relative / "job.json", {"policy_sha256": "d" * 64,
            "prior_checkpoint_sha256": "e" * 64})
        task = {"test_selectors": ["tests/test_two_site_demo.py"]}
        ids = ["real-proof-boundary-mocked-author", "distinct-review-boundary", "f" * 64]
        original = maintenance.once_or_same
        def crash_after_real_commit(root, path, value):
            if str(path).endswith("execution.json"):
                raise OSError("offline crash fixture")
            return original(root, path, value)
        with patch.object(maintenance, "_approved_changes", return_value=(task, self.sources, self.before, ids, self.changes)), \
                patch.object(maintenance, "load_policy", return_value=(policy, "d" * 64)), \
                patch.object(maintenance, "maintenance_native_overlays", return_value={}), \
                patch.object(maintenance, "checkpoint", return_value=({}, "e" * 64)), \
                patch.object(maintenance, "repository", side_effect=lambda _root: repo(self.git("rev-parse", "HEAD").decode().strip())), \
                patch("scripts.two_site_agent_sandbox.run_tests", return_value={"returncode": 0, "fixture": "native-boundary"}) as native:
            with patch.object(maintenance, "once_or_same", side_effect=crash_after_real_commit):
                with self.assertRaisesRegex(OSError, "crash fixture"):
                    maintenance.finish_maintenance(self.root, auth)
            committed = self.git("rev-parse", "HEAD").decode().strip()
            self.assertNotEqual(committed, self.before["head"])
            self.assertTrue((self.root / relative / "commit-intent.json").is_file())
            self.assertFalse((self.root / relative / "execution.json").exists())
            receipt = maintenance.finish_maintenance(self.root, auth)
            self.assertEqual(receipt["head_after"], committed)
            self.assertEqual(receipt["business_acceptance_credit"], 0)
            self.assertIs(receipt["checkpoint_written"], False)
            native.assert_called_once()
            self.assertEqual(self.git("rev-list", "--count", "HEAD").decode().strip(), "2")

    def test_pending_provider_never_gets_fresh_author_or_semantic_reset(self):
        directory = self.root / maintenance.JOBS_REL / ("maint-" + "b" * 32)
        directory.mkdir(parents=True, mode=0o700)
        maintenance.write_new(self.root, directory.relative_to(self.root) / "job.json",
                              {"before_repository": self.before})
        maintenance.write_new(self.root, directory.relative_to(self.root) / "pipeline-started.json",
                              {"retained": "original finite provider budget"})
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "PROVIDER_CONTINUATION_REQUIRED"):
            maintenance.pending_maintenance(self.root, self.before["head"])


if __name__ == "__main__":
    unittest.main()
