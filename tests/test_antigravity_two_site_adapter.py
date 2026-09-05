import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_antigravity_two_site_adapter.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("antigravity_adapter_validator", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Antigravity adapter validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def smoke_stream(response="ANTIGRAVITY_RUNTIME_SMOKE_OK"):
    return "\n".join(json.dumps(event) for event in [
        {"event": "init", "conversation_id": "runtime-test", "init": {
            "cwd": str(ROOT), "model": "gemini-3.8-flash-high", "permission_mode": "request-review"}},
        {"event": "step_update", "step_update": {
            "conversation_id": "runtime-test", "step_type": "agent_response", "state": "DONE"}},
        {"event": "result", "result": {"conversation_id": "runtime-test", "status": "SUCCESS",
            "response": response, "num_turns": 1}},
    ])


def file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def load_fence_module():
    path = ROOT / "scripts" / "two_site_orchestrator_fence.py"
    spec = importlib.util.spec_from_file_location("two_site_orchestrator_fence", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load orchestrator fence")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_fence_fixture(directory: str):
    def git(repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    root = Path(directory) / "integration"
    stage_root = Path(directory) / "stage"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fence Test")
    git(root, "config", "user.email", "fence@example.invalid")
    git(root, "switch", "-qc", "refactor/two-site-architecture")
    (root / ".gitignore").write_text("/.local/\n", encoding="utf-8")
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
    order = root / "docs" / "refactor" / "two-server" / "execution-order.yaml"
    order.parent.mkdir(parents=True)
    order.write_text(
        "stages:\n"
        "  P1-00b: {state: COMPLETE, depends_on: [], production: false}\n"
        "  P1-01: {state: IN_PROGRESS, depends_on: [P1-00b], production: false}\n",
        encoding="utf-8",
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "integration base")
    integration_head = git(root, "rev-parse", "HEAD")

    git(
        root,
        "worktree",
        "add",
        "-q",
        "-b",
        "refactor/stage/P1-00b-freeze",
        str(stage_root),
        integration_head,
    )
    (stage_root / "stage.txt").write_text("candidate\n", encoding="utf-8")
    git(stage_root, "add", ".")
    git(stage_root, "commit", "-qm", "stage candidate")
    stage_head = git(stage_root, "rev-parse", "HEAD")

    local = root / ".local" / "two-site-refactor"
    assignments = local / "assignments"
    assignments.mkdir(parents=True)
    old_id = "WA-P1-00B-OLD"
    new_id = "WA-P1-00B-ANTIGRAVITY"
    old_assignment = f"""# Work Assignment — `{old_id}`

```yaml
assignment_id: {old_id}
stage_id: P1-00b
role: WORKER
status: READY
supersedes: null
base_sha: {integration_head}
branch: refactor/stage/P1-00b-freeze
worktree_path: {stage_root}
worktree_registry_id: stage-P1-00b
allowed_paths: [tests/**]
forbidden_paths: [scripts/**]
locks: [shared-test-fixtures]
parallel_peer: null
deliverables: [freeze]
tests: [focused]
failure_tests: [drift]
rollback_test: revert
external_access: NONE
expires_at: '2099-01-01T00:00:00Z'
issued_by: CURSOR_COORDINATOR
```
"""
    (assignments / f"{old_id}.md").write_text(old_assignment, encoding="utf-8")
    registry = {
        "schema_version": "1.0",
        "updated_at": "2026-09-04T00:00:00Z",
        "integration": {
            "head_sha": integration_head,
            "active_assignment_id": old_id,
            "owner_role": "CURSOR_COORDINATOR",
        },
        "stage_worktrees": [
            {
                "registry_id": "stage-P1-00b",
                "stage_id": "P1-00b",
                "assignment_id": old_id,
                "owner_role": "CURSOR_WORKER",
                "path": str(stage_root),
                "branch": "refactor/stage/P1-00b-freeze",
                "base_sha": integration_head,
                "head_sha": stage_head,
                "dirty_uncommitted_candidate": False,
                "locks": ["shared-test-fixtures"],
                "status": "IDLE_INTEGRATED",
            }
        ],
    }
    (local / "worktrees.yaml").write_text(
        yaml.safe_dump(registry, sort_keys=False), encoding="utf-8"
    )
    fence = load_fence_module()
    fence.ROOT = root
    fence.LOCAL = local
    fence.FENCE = local / "orchestrator-fence.json"
    fence.LOCK = local / "orchestrator-fence.lock"
    fence.REGISTRY = local / "worktrees.yaml"
    fence.EXECUTION_ORDER = order
    fence.validate_antigravity_runtime = lambda _instance: None
    fence.validate_antigravity_execution = lambda _instance: None
    execution_id = "10000000-0000-4000-8000-000000000001"
    return SimpleNamespace(
        git=git,
        root=root,
        stage_root=stage_root,
        local=local,
        assignments=assignments,
        old_id=old_id,
        new_id=new_id,
        integration_head=integration_head,
        stage_head=stage_head,
        execution_id=execution_id,
        fence=fence,
    )


class AntigravityTwoSiteAdapterTests(unittest.TestCase):
    def test_static_contract(self):
        validator = load_validator()
        self.assertEqual([], validator.validate())

    def test_work_assignment_template_matches_executable_contract(self):
        fence = load_fence_module()
        template = (
            ROOT / "docs" / "refactor" / "two-server" / "templates" / "WORK_ASSIGNMENT.md"
        ).read_text(encoding="utf-8")
        contract = template.split("```yaml\n", 1)[1].split("\n```", 1)[0]
        for field in fence.CANONICAL_ASSIGNMENT_FIELDS:
            self.assertRegex(contract, rf"(?m)^{field}:")
        order = yaml.safe_load(
            (ROOT / "docs" / "refactor" / "two-server" / "execution-order.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [
                "CODEX_FINAL_REVIEW_RECEIPT",
                "CODEX_CLOSEOUT_PREACTION_RECEIPT",
                "CLOSEOUT_POST_COMMIT_VERIFIED",
            ],
            order["authorization"]["stage_completion_requires"],
        )

    def test_verifier_has_only_protected_request_file_transport(self):
        validator = load_validator()
        metadata = validator.parse_frontmatter(
            ROOT / ".agents" / "agents" / "two-site-stage-verifier" / "agent.md"
        )
        tools = set(metadata["tools"])
        self.assertIn("write_to_file", tools)
        self.assertTrue(tools.isdisjoint(validator.WRITE_TOOLS - {"write_to_file"}))
        verifier = (
            ROOT / ".agents" / "agents" / "two-site-stage-verifier" / "agent.md"
        ).read_text(encoding="utf-8")
        self.assertIn("gateway request directory", verifier)
        coordinator = (
            ROOT / ".agents" / "agents" / "two-site-coordinator" / "agent.md"
        ).read_text(encoding="utf-8")
        self.assertIn("git status --porcelain=v1 --untracked-files=all", verifier)
        self.assertIn("byte-for-byte unchanged", verifier)
        self.assertIn("candidate HEAD plus full", coordinator)
        self.assertIn("invalidates that verification", coordinator)

    def test_validator_honors_explicit_root(self):
        validator = load_validator()
        with tempfile.TemporaryDirectory() as directory:
            errors = validator.validate(Path(directory))
        self.assertTrue(errors)
        self.assertTrue(any("missing required file" in error for error in errors))

    def test_codex_bridges_fail_closed_on_untracked_and_closeout_drift(self):
        final_bridge = (ROOT / "scripts" / "two_site_codex_final_review.sh").read_text(
            encoding="utf-8"
        )
        closeout_bridge = (
            ROOT / "scripts" / "two_site_codex_closeout_review.sh"
        ).read_text(encoding="utf-8")
        governance_bridge = (
            ROOT / "scripts" / "two_site_codex_governance_review.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("status --porcelain=v1 --untracked-files=all", final_bridge)
        self.assertIn('cat-file -e "${head_sha}:${schema_rel}"', final_bridge)
        self.assertIn("STAGE_CLOSEOUT_PREACTION", closeout_bridge)
        self.assertIn("patch_hash_cached", closeout_bridge)
        self.assertIn("patch_hash_commit", closeout_bridge)
        self.assertIn("CLOSEOUT_COMMIT=VERIFIED", closeout_bridge)
        self.assertIn("EXECUTION_GOVERNANCE_AMENDMENT", governance_bridge)
        self.assertIn("stage_state_unchanged", governance_bridge)
        self.assertIn("repository must be fully clean", governance_bridge)
        self.assertIn("[0-9]+[a-z]?", governance_bridge)

    def test_closeout_post_commit_verifies_exact_authorized_patch(self):
        def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                args,
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )

        stage = "P1-01"
        assignment = "WA-P1-01-TEST"
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            run(repo, "git", "init", "-q")
            run(repo, "git", "config", "user.name", "Adapter Test")
            run(repo, "git", "config", "user.email", "adapter@example.invalid")
            docs = repo / "docs" / "refactor" / "two-server"
            reviews = docs / "reviews"
            reviews.mkdir(parents=True)
            (repo / ".gitignore").write_text("/.local/\n", encoding="utf-8")
            (docs / "CHANGE_LEDGER.md").write_text("# ledger\n", encoding="utf-8")
            (docs / "execution-order.yaml").write_text(
                "P1-01: {state: IN_PROGRESS}\n", encoding="utf-8"
            )
            run(repo, "git", "add", ".")
            run(repo, "git", "commit", "-qm", "accepted stage")
            accepted = run(repo, "git", "rev-parse", "HEAD").stdout.strip()

            (docs / "CHANGE_LEDGER.md").write_text(
                "# ledger\nP1-01 approved\n", encoding="utf-8"
            )
            (docs / "execution-order.yaml").write_text(
                "P1-01: {state: COMPLETE}\n", encoding="utf-8"
            )
            durable = reviews / f"{stage}-{assignment}"
            durable.with_suffix(".json").write_text("{}\n", encoding="utf-8")
            durable.with_suffix(".md").write_text("# approved\n", encoding="utf-8")
            run(repo, "git", "add", ".")
            run(repo, "git", "commit", "-qm", "closeout")
            closeout = run(repo, "git", "rev-parse", "HEAD").stdout.strip()
            patch = subprocess.check_output(
                [
                    "git",
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                    accepted,
                    closeout,
                ],
                cwd=repo,
            )
            patch_hash = hashlib.sha256(patch).hexdigest()

            bundle = (
                repo
                / ".local"
                / "two-site-refactor"
                / "review-bundles"
                / f"{stage}-{assignment}"
            )
            bundle.mkdir(parents=True)
            (bundle / "CODEX_FINAL_REVIEW.json").write_text(
                json.dumps(
                    {
                        "reviewer_authority": "CODEX_FINAL_REVIEWER",
                        "receipt_type": "STAGE_FINAL",
                        "stage_id": stage,
                        "assignment_id": assignment,
                        "reviewed_head_sha": accepted,
                        "verdict": "APPROVE",
                        "decision": {"accepted_commit": accepted},
                    }
                ),
                encoding="utf-8",
            )
            (bundle / "CODEX_CLOSEOUT_PREACTION.json").write_text(
                json.dumps(
                    {
                        "reviewer_authority": "CODEX_FINAL_REVIEWER",
                        "receipt_type": "STAGE_CLOSEOUT_PREACTION",
                        "stage_id": stage,
                        "assignment_id": assignment,
                        "accepted_stage_sha": accepted,
                        "proposed_patch_sha256": patch_hash,
                        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                        "verdict": "APPROVE",
                        "decision": {"commit_authorized": True},
                    }
                ),
                encoding="utf-8",
            )

            result = run(
                repo,
                "bash",
                str(ROOT / "scripts" / "two_site_codex_closeout_review.sh"),
                "--verify-commit",
                "--stage",
                stage,
                "--assignment",
                assignment,
                "--bundle",
                f".local/two-site-refactor/review-bundles/{stage}-{assignment}",
                "--accepted-head",
                accepted,
                "--closeout-head",
                closeout,
            )
            self.assertIn("CLOSEOUT_COMMIT=VERIFIED", result.stdout)

    def test_workspace_rule_is_within_platform_limit(self):
        rule = ROOT / ".agents" / "rules" / "two-site-refactor-safety.md"
        self.assertLessEqual(len(rule.read_text(encoding="utf-8")), 12_000)

    def test_orchestrator_fence_cli_is_available(self):
        result = subprocess.run(
            ["/usr/bin/python3", "-I",
             str(ROOT / "scripts" / "two_site_orchestrator_fence.py"), "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("claim", result.stdout)
        self.assertIn("activate", result.stdout)
        self.assertIn("check", result.stdout)
        self.assertIn("worker-check", result.stdout)
        self.assertIn("stage-advance", result.stdout)
        self.assertIn("stage-bind", result.stdout)
        self.assertIn("advance", result.stdout)
        self.assertIn("release", result.stdout)

    def test_direct_nonisolated_fence_and_validator_cli_fail_closed(self):
        for relative in (
            "scripts/two_site_orchestrator_fence.py",
            "scripts/validate_antigravity_two_site_adapter.py",
        ):
            with self.subTest(relative=relative):
                result = subprocess.run(
                    ["/usr/bin/python3", str(ROOT / relative), "--help"],
                    cwd=ROOT, check=False, capture_output=True, text=True,
                )
                self.assertEqual(1, result.returncode)
                self.assertRegex(result.stdout + result.stderr, r"requires /usr/bin/python3 -I")

    def test_runtime_validation_requires_exact_custom_coordinator_smoke(self):
        validator = load_validator()
        agents_envelope = json.dumps(
            {
                "status": "SUCCESS",
                "command": {
                    "data": {
                        "agents": [
                            {"name": "two-site-coordinator"},
                            {"name": "two-site-stage-worker"},
                            {"name": "two-site-stage-verifier"},
                        ]
                    }
                },
            }
        )
        invalid_smoke = smoke_stream("almost correct")
        def runner(command, **_kwargs):
            if command[0] == "git":
                output = "a" * 40 + "\n" if "rev-parse" in command else ""
                return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
            output = agents_envelope if "agents" in command else invalid_smoke
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

        with mock.patch.dict(os.environ, {"ANTIGRAVITY_CLI": "/fake/agy"}), mock.patch.object(
            validator.subprocess, "run", side_effect=runner
        ) as runner, mock.patch.object(validator, "runtime_sessions", return_value={"coordinator": "runtime-test", "execution_coordinator": "execution-test"}), mock.patch.object(validator, "runtime_bindings", return_value=[]):
            errors = validator.validate_runtime()
        self.assertEqual(
            ["agy read-only smoke failed: exact response/session/mode contract failed"],
            errors,
        )
        smoke_command = [
            call.args[0]
            for call in runner.call_args_list
            if call.args[0][0] == "/fake/agy" and "agents" not in call.args[0]
        ][0]
        self.assertIn("--conversation", smoke_command)
        self.assertIn("runtime-test", smoke_command)
        self.assertNotIn("--agent", smoke_command)
        self.assertIn("--mode", smoke_command)
        self.assertIn("plan", smoke_command)

    def test_runtime_validation_reports_auth_after_binding(self):
        validator = load_validator()
        agents_envelope = json.dumps(
            {"status": "SUCCESS", "command": {"data": {"agents": []}}}
        )

        def runner(command, **_kwargs):
            if command[0] == "git":
                output = "a" * 40 + "\n" if "rev-parse" in command else ""
                return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
            if "agents" in command:
                return subprocess.CompletedProcess(
                    command, 0, stdout=agents_envelope, stderr=""
                )
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="You are not logged into Antigravity.",
            )

        with mock.patch.dict(os.environ, {"ANTIGRAVITY_CLI": "/fake/agy"}), mock.patch.object(
            validator.subprocess, "run", side_effect=runner
        ), mock.patch.object(validator, "runtime_sessions", return_value={"coordinator": "runtime-test", "execution_coordinator": "execution-test"}), mock.patch.object(validator, "runtime_bindings", return_value=[]):
            errors = validator.validate_runtime()
        self.assertEqual(
            [
                "agy read-only coordinator smoke failed: authentication required; "
                "complete one interactive agy login"
            ],
            errors,
        )

    def test_runtime_validation_detects_repository_mutation(self):
        validator = load_validator()
        agents = json.dumps(
            {
                "status": "SUCCESS",
                "command": {
                    "data": {
                        "agents": [
                            metadata["name"] for metadata in validator.AGENTS.values()
                        ]
                    }
                },
            }
        )
        smoke = smoke_stream()
        status_reads = 0

        def runner(command, **_kwargs):
            nonlocal status_reads
            if command[0] == "git" and "rev-parse" in command:
                return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n", stderr="")
            if command[0] == "git":
                status_reads += 1
                output = "" if status_reads == 1 else " M changed.txt\n"
                return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
            output = agents if "agents" in command else smoke
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

        with mock.patch.dict(os.environ, {"ANTIGRAVITY_CLI": "/fake/agy"}), mock.patch.object(
            validator.subprocess, "run", side_effect=runner
        ), mock.patch.object(validator, "runtime_sessions", return_value={"coordinator": "runtime-test", "execution_coordinator": "execution-test"}), mock.patch.object(validator, "runtime_bindings", return_value=[]):
            errors = validator.validate_runtime()
        self.assertIn(
            "Antigravity runtime validation changed repository HEAD, full status, or local orchestration state",
            errors,
        )

    def test_runtime_stream_rejects_tools_foreign_session_and_failed_history(self):
        validator = load_validator()
        good = [json.loads(line) for line in smoke_stream().splitlines()]
        self.assertEqual([], validator.check_smoke_stream(smoke_stream(), "runtime-test"))
        changes = [
            (0, "conversation_id", "different"),
            (0, "init", {"cwd": str(ROOT), "permission_mode": "always-proceed"}),
            (1, "step_update", {"conversation_id": "runtime-test", "step_type": "tool", "tool_name": "run_command"}),
            (1, "step_update", {"conversation_id": "other", "step_type": "agent_response", "state": "DONE"}),
            (2, "result", {**good[2]["result"], "status": "ERROR"}),
            (2, "result", {**good[2]["result"], "num_turns": True}),
        ]
        for index, key, value in changes:
            with self.subTest(index=index, key=key, value=value):
                candidate = json.loads(json.dumps(good))
                candidate[index][key] = value
                self.assertTrue(validator.check_smoke_stream("\n".join(map(json.dumps, candidate)), "runtime-test"))
        for output in ("{}", "[]", "not json", smoke_stream()+"\n"+json.dumps(good[-1])):
            self.assertTrue(validator.check_smoke_stream(output, "runtime-test"))

    def test_runtime_missing_binding_never_starts_cli(self):
        validator = load_validator()
        with mock.patch.dict(os.environ, {"ANTIGRAVITY_CLI": "/fake/agy"}), mock.patch.object(
            validator, "runtime_sessions", side_effect=ValueError("missing observed sessions")
        ), mock.patch.object(validator.subprocess, "run") as runner:
            self.assertTrue(validator.validate_runtime())
            runner.assert_not_called()

    def test_runtime_resumes_all_three_exact_roles_without_discovery_fallback(self):
        validator = load_validator()
        calls = []
        def run(command, **_kwargs):
            if command[0] == "git":
                output = "a" * 40 + "\n" if "rev-parse" in command else ""
            else:
                calls.append(command)
                session = command[command.index("--conversation") + 1]
                token = command[-1].split()[-1]
                output = smoke_stream(token).replace("runtime-test", session)
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
        sessions = {role: f"session-{role}" for role in ("coordinator", "worker", "verifier")}
        sessions["execution_coordinator"] = "session-execution"
        with mock.patch.dict(os.environ, {"ANTIGRAVITY_CLI": "/fake/agy"}), mock.patch.object(
            validator, "runtime_sessions", return_value=sessions
        ), mock.patch.object(validator, "runtime_bindings", return_value=[]), mock.patch.object(
            validator.subprocess, "run", side_effect=run
        ):
            self.assertEqual([], validator.validate_runtime())
        self.assertEqual(3, len(calls))
        self.assertEqual(
            [sessions[role] for role in validator.SMOKE_ROLES],
            [call[call.index("--conversation") + 1] for call in calls],
        )
        self.assertTrue(all("--agent" not in call and "agents" not in call for call in calls))

    def test_execution_coordinator_binding_is_registered_distinct_and_active(self):
        validator = load_validator()
        sessions = {
            "coordinator": "10000000-0000-4000-8000-000000000001",
            "worker": "10000000-0000-4000-8000-000000000002",
            "verifier": "10000000-0000-4000-8000-000000000003",
            "execution_coordinator": "10000000-0000-4000-8000-000000000004",
        }
        expected = {"envelope_sha256": "a" * 64}
        with mock.patch.object(validator, "binding", return_value=expected) as bound:
            self.assertEqual(
                expected,
                validator.execution_binding(sessions, sessions["execution_coordinator"]),
            )
        self.assertEqual("ACTIVE_EXECUTION", bound.call_args.kwargs["history_policy"])
        with self.assertRaisesRegex(ValueError, "not the registered"):
            validator.execution_binding(
                sessions, "10000000-0000-4000-8000-000000000005"
            )
        duplicate = {**sessions, "execution_coordinator": sessions["coordinator"]}
        with self.assertRaisesRegex(ValueError, "distinct"):
            validator.execution_binding(duplicate, duplicate["execution_coordinator"])

    def test_binding_only_validation_never_resumes_execution_conversation(self):
        validator = load_validator()
        execution_id = "10000000-0000-4000-8000-000000000004"
        sessions = {"execution_coordinator": execution_id}
        with mock.patch.object(
            validator, "antigravity_cli", return_value="/fake/agy"
        ), mock.patch.object(
            validator, "runtime_sessions", return_value=sessions
        ), mock.patch.object(
            validator, "execution_binding", return_value={}
        ) as bound, mock.patch.object(validator.subprocess, "run") as runner:
            self.assertEqual([], validator.validate_execution_conversation(execution_id))
        bound.assert_called_once_with(sessions, execution_id)
        runner.assert_not_called()

    def test_fence_uses_live_smoke_only_for_claim_and_binding_only_afterward(self):
        fence = load_fence_module()
        execution_id = "10000000-0000-4000-8000-000000000004"
        completed = subprocess.CompletedProcess([], 0, stdout="PASS\n", stderr="")
        with mock.patch.object(fence.subprocess, "run", return_value=completed) as runner:
            fence.validate_antigravity_runtime(execution_id)
            fence.validate_antigravity_execution(execution_id)
        live, offline = [call.args[0] for call in runner.call_args_list]
        self.assertIn("--runtime", live)
        self.assertNotIn("--runtime", offline)
        for command in (live, offline):
            self.assertEqual([sys.executable, "-I"], command[:2])
            self.assertEqual(
                execution_id,
                command[command.index("--execution-conversation") + 1],
            )
            self.assertNotIn("--conversation", command)

    def test_isolated_validator_ignores_hostile_candidate_import_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'candidate'
            scripts = root / 'scripts'
            scripts.mkdir(parents=True)
            for name in (
                'validate_antigravity_two_site_adapter.py',
                'two_site_antigravity_session.py',
                'antigravity_cli_1_1_26_profile.json',
            ):
                shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
            marker = Path(directory) / 'poison-executed'
            poison = (
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('executed')\n"
                "raise RuntimeError('candidate import poison')\n"
            )
            for relative in ('yaml.py', 'sitecustomize.py', 'scripts/yaml.py',
                             'scripts/sitecustomize.py'):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(poison)
            (scripts / SCRIPT.name).chmod(0o755)
            result = subprocess.run(
                [str(scripts / SCRIPT.name), '--help'],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('--execution-conversation', result.stdout)
            self.assertFalse(marker.exists())

        validator = load_validator()
        self.assertEqual(
            (ROOT / 'scripts/two_site_antigravity_session.py').resolve(),
            Path(validator._SESSION_ADAPTER.__file__).resolve(),
        )
        self.assertFalse(Path(sys.modules['yaml'].__file__).resolve().is_relative_to(ROOT))

    def test_direct_fence_shebang_ignores_hostile_candidate_import_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'candidate'
            scripts = root / 'scripts'
            scripts.mkdir(parents=True)
            fence = scripts / 'two_site_orchestrator_fence.py'
            shutil.copyfile(ROOT / 'scripts' / fence.name, fence)
            fence.chmod(0o755)
            marker = Path(directory) / 'poison-executed'
            poison = (
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('executed')\n"
                "raise RuntimeError('candidate import poison')\n"
            )
            for relative in ('yaml.py', 'sitecustomize.py', 'scripts/yaml.py',
                             'scripts/sitecustomize.py'):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(poison)
            result = subprocess.run(
                [str(fence), '--help'], cwd=root,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('stage-bind', result.stdout)
            self.assertFalse(marker.exists())

    def test_binding_only_cli_fails_closed_with_clear_error(self):
        validator = load_validator()
        execution_id = "10000000-0000-4000-8000-000000000004"
        failure = "Antigravity execution Coordinator binding is not ready: wrong model"
        with mock.patch.object(validator, "validate", return_value=[]), mock.patch.object(
            validator, "validate_execution_conversation", return_value=[failure]
        ), mock.patch.object(
            validator, "isolated_runtime", return_value=True
        ), mock.patch.object(
            validator.sys,
            "argv",
            [str(SCRIPT), "--execution-conversation", execution_id],
        ), mock.patch("builtins.print") as output:
            self.assertEqual(1, validator.main())
        self.assertTrue(any(f"FAIL: {failure}" in str(call) for call in output.call_args_list))

    def test_runtime_registry_requires_distinct_execution_coordinator(self):
        validator = load_validator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "agy"
            binary.write_bytes(b"reviewed CLI")
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            local = root / ".local" / "two-site-refactor"
            local.mkdir(parents=True)
            sessions = {
                "schema_version": "1.0",
                "cli_sha256": digest,
                "coordinator": "10000000-0000-4000-8000-000000000001",
                "worker": "10000000-0000-4000-8000-000000000002",
                "verifier": "10000000-0000-4000-8000-000000000003",
                "execution_coordinator": "10000000-0000-4000-8000-000000000004",
            }
            registry = local / "antigravity-runtime-sessions.json"
            registry.write_text(json.dumps(sessions), encoding="utf-8")
            with mock.patch.object(validator, "ROOT", root), mock.patch.object(
                validator, "VALIDATED_CLI_SHA256", digest
            ):
                self.assertEqual(sessions, validator.runtime_sessions(str(binary)))
                sessions["execution_coordinator"] = sessions["coordinator"]
                registry.write_text(json.dumps(sessions), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "four distinct"):
                    validator.runtime_sessions(str(binary))

    def test_official_but_unapproved_tool_is_rejected(self):
        validator = load_validator()
        original = validator.parse_frontmatter
        def read(path):
            data = original(path)
            if path.name == "agent.md":
                data["tools"].append("search_web")
            return data
        with mock.patch.object(validator, "parse_frontmatter", side_effect=read):
            self.assertTrue(any("exact reviewed role allowlist" in error for error in validator.validate()))

    def test_antigravity_claim_binding_failure_is_prewrite_and_uuid_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_fence_fixture(directory)
            fence = fixture.fence
            fence.LOCK.touch()
            baseline = file_snapshot(fixture.local)
            rejection = mock.Mock(
                side_effect=fence.FenceError("execution Coordinator binding rejected")
            )
            fence.validate_antigravity_runtime = rejection
            claim = SimpleNamespace(
                platform="ANTIGRAVITY",
                instance=fixture.execution_id,
                integration_head=fixture.integration_head,
                previous_assignment=fixture.old_id,
                replacement_assignment=fixture.new_id,
                expected_epoch=0,
            )
            with self.assertRaisesRegex(fence.FenceError, "binding rejected"):
                fence.claim(claim)
            rejection.assert_called_once_with(fixture.execution_id)
            self.assertEqual(baseline, file_snapshot(fixture.local))

            claim.instance = "not-a-conversation-uuid"
            rejection.reset_mock()
            with self.assertRaisesRegex(fence.FenceError, "conversation UUID"):
                fence.claim(claim)
            rejection.assert_not_called()
            self.assertEqual(baseline, file_snapshot(fixture.local))

    def test_antigravity_binding_is_rechecked_before_activate_and_active_access(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_fence_fixture(directory)
            fence = fixture.fence
            claim = SimpleNamespace(
                platform="ANTIGRAVITY",
                instance=fixture.execution_id,
                integration_head=fixture.integration_head,
                previous_assignment=fixture.old_id,
                replacement_assignment=fixture.new_id,
                expected_epoch=0,
            )
            original_reconcile = fence.reconcile_takeover
            fence.reconcile_takeover = mock.Mock(
                side_effect=fence.FenceError("injected reconciliation interruption")
            )
            with self.assertRaisesRegex(fence.FenceError, "interruption"):
                fence.claim(claim)
            fence.reconcile_takeover = original_reconcile
            activate = SimpleNamespace(
                platform="ANTIGRAVITY",
                instance=fixture.execution_id,
                epoch=1,
                integration_head=fixture.integration_head,
                assignment=fixture.new_id,
            )
            reject = mock.Mock(side_effect=fence.FenceError("binding drift"))
            fence.validate_antigravity_execution = reject
            before_activate = file_snapshot(fixture.local)
            with self.assertRaisesRegex(fence.FenceError, "binding drift"):
                fence.activate(activate)
            reject.assert_called_once_with(fixture.execution_id)
            self.assertEqual(before_activate, file_snapshot(fixture.local))

            fence.validate_antigravity_execution = lambda _instance: None
            fence.activate(activate)
            reject = mock.Mock(side_effect=fence.FenceError("binding drift"))
            fence.validate_antigravity_execution = reject
            before_check = file_snapshot(fixture.local)
            with self.assertRaisesRegex(fence.FenceError, "binding drift"):
                fence.check(activate)
            reject.assert_called_once_with(fixture.execution_id)
            self.assertEqual(before_check, file_snapshot(fixture.local))

            reject.reset_mock()
            with self.assertRaisesRegex(fence.FenceError, "binding drift"):
                fence.advance(activate)
            reject.assert_called_once_with(fixture.execution_id)
            self.assertEqual(before_check, file_snapshot(fixture.local))

            reject.reset_mock()
            stage_bind = SimpleNamespace(
                **vars(activate), next_assignment="WA-P1-01-ANTIGRAVITY"
            )
            with self.assertRaisesRegex(fence.FenceError, "binding drift"):
                fence.stage_bind(stage_bind)
            reject.assert_called_once_with(fixture.execution_id)
            self.assertEqual(before_check, file_snapshot(fixture.local))

    def test_cursor_instance_remains_opaque_and_skips_antigravity_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_fence_fixture(directory)
            fence = fixture.fence
            runtime = mock.Mock(side_effect=AssertionError("unexpected runtime validation"))
            execution = mock.Mock(side_effect=AssertionError("unexpected execution binding"))
            fence.validate_antigravity_runtime = runtime
            fence.validate_antigravity_execution = execution
            claim = SimpleNamespace(
                platform="CURSOR",
                instance="opaque-cursor-instance",
                integration_head=fixture.integration_head,
                previous_assignment=fixture.old_id,
                replacement_assignment=fixture.new_id,
                expected_epoch=0,
            )
            fence.claim(claim)
            check = SimpleNamespace(
                platform="CURSOR",
                instance="opaque-cursor-instance",
                epoch=1,
                integration_head=fixture.integration_head,
                assignment=fixture.new_id,
            )
            fence.check(check)
            runtime.assert_not_called()
            execution.assert_not_called()

    def test_orchestrator_fence_recovers_each_reconciliation_write_boundary(self):
        for failed_write in range(2, 6):
            with self.subTest(failed_write=failed_write), tempfile.TemporaryDirectory() as directory:
                fixture = make_fence_fixture(directory)
                fence = fixture.fence
                claim = SimpleNamespace(
                    platform="ANTIGRAVITY",
                    instance=fixture.execution_id,
                    integration_head=fixture.integration_head,
                    previous_assignment=fixture.old_id,
                    replacement_assignment=fixture.new_id,
                    expected_epoch=0,
                )
                original_write = fence.atomic_write_text
                writes = 0

                def fail_once(path, content, mode=0o600):
                    nonlocal writes
                    writes += 1
                    if writes == failed_write:
                        raise fence.FenceError(f"injected crash before write {failed_write}")
                    return original_write(path, content, mode)

                fence.atomic_write_text = fail_once
                with self.assertRaises(fence.FenceError):
                    fence.claim(claim)
                fence.atomic_write_text = original_write
                interrupted = fence.load_fence()
                self.assertEqual("RECONCILING", interrupted["status"])
                activate = SimpleNamespace(
                    platform="ANTIGRAVITY",
                    instance=fixture.execution_id,
                    epoch=1,
                    integration_head=fixture.integration_head,
                    assignment=fixture.new_id,
                )
                fence.activate(activate)
                fence.check(activate)

    def test_takeover_rejects_prior_mismatch_before_superseding(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_fence_fixture(directory)
            fence = fixture.fence
            old_path = fixture.assignments / f"{fixture.old_id}.md"
            old_path.write_text(
                old_path.read_text(encoding="utf-8").replace(
                    str(fixture.stage_root), f"{fixture.stage_root}-wrong"
                ),
                encoding="utf-8",
            )
            claim = SimpleNamespace(
                platform="ANTIGRAVITY",
                instance=fixture.execution_id,
                integration_head=fixture.integration_head,
                previous_assignment=fixture.old_id,
                replacement_assignment=fixture.new_id,
                expected_epoch=0,
            )
            with self.assertRaises(fence.FenceError):
                fence.claim(claim)
            self.assertFalse(fence.FENCE.exists())
            self.assertEqual("READY", fence.load_assignment(fixture.old_id)["status"])
            self.assertFalse((fixture.assignments / f"{fixture.new_id}.md").exists())

    def test_fresh_takeover_rejects_superseded_assignment_and_registry_base_drift(self):
        for drift in ("superseded", "registry-base", "malformed-scope"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as directory:
                fixture = make_fence_fixture(directory)
                fence = fixture.fence
                if drift == "superseded":
                    old_path = fixture.assignments / f"{fixture.old_id}.md"
                    old_path.write_text(
                        old_path.read_text(encoding="utf-8").replace(
                            "status: READY", "status: SUPERSEDED"
                        ),
                        encoding="utf-8",
                    )
                elif drift == "registry-base":
                    registry = yaml.safe_load(fence.REGISTRY.read_text(encoding="utf-8"))
                    registry["stage_worktrees"][0]["base_sha"] = "0" * 40
                    fence.REGISTRY.write_text(
                        yaml.safe_dump(registry, sort_keys=False), encoding="utf-8"
                    )
                else:
                    old_path = fixture.assignments / f"{fixture.old_id}.md"
                    old_path.write_text(
                        old_path.read_text(encoding="utf-8").replace(
                            "allowed_paths: [tests/**]", "allowed_paths: tests/**"
                        ),
                        encoding="utf-8",
                    )
                claim = SimpleNamespace(
                    platform="ANTIGRAVITY",
                    instance=fixture.execution_id,
                    integration_head=fixture.integration_head,
                    previous_assignment=fixture.old_id,
                    replacement_assignment=fixture.new_id,
                    expected_epoch=0,
                )
                with self.assertRaises(fence.FenceError):
                    fence.claim(claim)
                self.assertFalse(fence.FENCE.exists())
                self.assertFalse((fixture.assignments / f"{fixture.new_id}.md").exists())

    def test_takeover_generated_mismatch_stays_reconciling_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_fence_fixture(directory)
            fence = fixture.fence
            claim = SimpleNamespace(
                platform="ANTIGRAVITY",
                instance=fixture.execution_id,
                integration_head=fixture.integration_head,
                previous_assignment=fixture.old_id,
                replacement_assignment=fixture.new_id,
                expected_epoch=0,
            )
            original_document = fence.assignment_document

            def mismatched_document(source, assignment_id, changes, note):
                document = original_document(source, assignment_id, changes, note)
                if assignment_id == fixture.new_id:
                    return document.replace(
                        str(fixture.stage_root), f"{fixture.stage_root}-wrong", 1
                    )
                return document

            fence.assignment_document = mismatched_document
            with self.assertRaises(fence.FenceError):
                fence.claim(claim)
            self.assertEqual("RECONCILING", fence.load_fence()["status"])
            self.assertEqual("READY", fence.load_assignment(fixture.old_id)["status"])
            self.assertFalse((fixture.assignments / f"{fixture.new_id}.md").exists())

            fence.assignment_document = original_document
            activate = SimpleNamespace(
                platform="ANTIGRAVITY",
                instance=fixture.execution_id,
                epoch=1,
                integration_head=fixture.integration_head,
                assignment=fixture.new_id,
            )
            fence.activate(activate)
            fence.check(activate)

    def test_takeover_rejects_worktree_from_unrelated_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_fence_fixture(directory)
            fence = fixture.fence
            unrelated = Path(directory) / "unrelated"
            subprocess.run(
                ["git", "clone", "-q", str(fixture.stage_root), str(unrelated)],
                check=True,
                capture_output=True,
                text=True,
            )
            old_path = fixture.assignments / f"{fixture.old_id}.md"
            old_path.write_text(
                old_path.read_text(encoding="utf-8").replace(
                    str(fixture.stage_root), str(unrelated)
                ),
                encoding="utf-8",
            )
            registry = yaml.safe_load(fence.REGISTRY.read_text(encoding="utf-8"))
            registry["stage_worktrees"][0]["path"] = str(unrelated)
            fence.REGISTRY.write_text(
                yaml.safe_dump(registry, sort_keys=False), encoding="utf-8"
            )
            claim = SimpleNamespace(
                platform="ANTIGRAVITY",
                instance=fixture.execution_id,
                integration_head=fixture.integration_head,
                previous_assignment=fixture.old_id,
                replacement_assignment=fixture.new_id,
                expected_epoch=0,
            )
            with self.assertRaises(fence.FenceError):
                fence.claim(claim)
            self.assertFalse(fence.FENCE.exists())

    def test_orchestrator_fence_transaction_and_drift_guards(self):
        def git(repo: Path, *args: str) -> str:
            return subprocess.run(
                ["git", *args], cwd=repo, check=True, capture_output=True, text=True
            ).stdout.strip()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "integration"
            stage_root = Path(directory) / "stage"
            root.mkdir()
            git(root, "init", "-q")
            git(root, "config", "user.name", "Fence Test")
            git(root, "config", "user.email", "fence@example.invalid")
            git(root, "switch", "-qc", "refactor/two-site-architecture")
            (root / ".gitignore").write_text("/.local/\n", encoding="utf-8")
            (root / "tracked.txt").write_text("one\n", encoding="utf-8")
            order = root / "docs" / "refactor" / "two-server" / "execution-order.yaml"
            order.parent.mkdir(parents=True)
            order.write_text(
                "stages:\n"
                "  P1-00b: {state: COMPLETE, depends_on: [], production: false}\n"
                "  P1-01: {state: IN_PROGRESS, depends_on: [P1-00b], production: false}\n",
                encoding="utf-8",
            )
            git(root, "add", ".")
            git(root, "commit", "-qm", "integration base")
            integration_head = git(root, "rev-parse", "HEAD")

            git(
                root,
                "worktree",
                "add",
                "-q",
                "-b",
                "refactor/stage/P1-00b-freeze",
                str(stage_root),
                integration_head,
            )
            (stage_root / "stage.txt").write_text("candidate\n", encoding="utf-8")
            git(stage_root, "add", ".")
            git(stage_root, "commit", "-qm", "stage candidate")
            stage_head = git(stage_root, "rev-parse", "HEAD")

            local = root / ".local" / "two-site-refactor"
            assignments = local / "assignments"
            assignments.mkdir(parents=True)
            old_id = "WA-P1-00B-OLD"
            new_id = "WA-P1-00B-ANTIGRAVITY"
            old_assignment = f"""# Work Assignment — `{old_id}`

```yaml
assignment_id: {old_id}
stage_id: P1-00b
role: WORKER
status: READY
supersedes: null
base_sha: {integration_head}
branch: refactor/stage/P1-00b-freeze
worktree_path: {stage_root}
worktree_registry_id: stage-P1-00b
allowed_paths: [tests/**]
forbidden_paths: [scripts/**]
locks: [shared-test-fixtures]
parallel_peer: null
deliverables: [freeze]
tests: [focused]
failure_tests: [drift]
rollback_test: revert
external_access: NONE
expires_at: '2099-01-01T00:00:00Z'
issued_by: CURSOR_COORDINATOR
```
"""
            (assignments / f"{old_id}.md").write_text(old_assignment, encoding="utf-8")
            registry = {
                "schema_version": "1.0",
                "updated_at": "2026-09-04T00:00:00Z",
                "integration": {
                    "head_sha": integration_head,
                    "active_assignment_id": old_id,
                    "owner_role": "CURSOR_COORDINATOR",
                },
                "stage_worktrees": [
                    {
                        "registry_id": "stage-P1-00b",
                        "stage_id": "P1-00b",
                        "assignment_id": old_id,
                        "owner_role": "CURSOR_WORKER",
                        "path": str(stage_root),
                        "branch": "refactor/stage/P1-00b-freeze",
                        "base_sha": integration_head,
                        "head_sha": stage_head,
                        "dirty_uncommitted_candidate": False,
                        "locks": ["shared-test-fixtures"],
                        "status": "IDLE_INTEGRATED",
                    }
                ],
            }
            (local / "worktrees.yaml").write_text(
                yaml.safe_dump(registry, sort_keys=False), encoding="utf-8"
            )

            fence = load_fence_module()
            fence.ROOT = root
            fence.LOCAL = local
            fence.FENCE = local / "orchestrator-fence.json"
            fence.LOCK = local / "orchestrator-fence.lock"
            fence.REGISTRY = local / "worktrees.yaml"
            fence.EXECUTION_ORDER = order
            fence.validate_antigravity_runtime = lambda _instance: None
            fence.validate_antigravity_execution = lambda _instance: None
            execution_id = "10000000-0000-4000-8000-000000000001"
            claim = SimpleNamespace(
                platform="ANTIGRAVITY",
                instance=execution_id,
                integration_head=integration_head,
                previous_assignment=old_id,
                replacement_assignment=new_id,
                expected_epoch=0,
            )

            dirty = stage_root / "untracked.txt"
            dirty.write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(fence.FenceError):
                fence.claim(claim)
            self.assertFalse(fence.FENCE.exists())
            dirty.unlink()

            integration_dirty = root / "untracked-integration.txt"
            integration_dirty.write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(fence.FenceError):
                fence.claim(claim)
            self.assertFalse(fence.FENCE.exists())
            integration_dirty.unlink()

            fence.claim(claim)
            payload = json.loads(fence.FENCE.read_text(encoding="utf-8"))
            self.assertEqual((payload["status"], payload["epoch"]), ("ACTIVE", 1))
            self.assertEqual(payload["previous_assignment_id"], old_id)
            self.assertEqual(payload["active_assignment_id"], new_id)
            self.assertEqual(fence.load_assignment(old_id)["status"], "SUPERSEDED")
            replacement = fence.load_assignment(new_id)
            self.assertEqual(replacement["supersedes"], old_id)
            self.assertEqual(replacement["base_sha"], stage_head)

            runtime_called = False

            def unexpected_runtime(_instance):
                nonlocal runtime_called
                runtime_called = True

            fence.validate_antigravity_runtime = unexpected_runtime
            competing_claim = SimpleNamespace(
                platform="ANTIGRAVITY",
                instance="10000000-0000-4000-8000-000000000002",
                integration_head=integration_head,
                previous_assignment=new_id,
                replacement_assignment="WA-P1-00B-COMPETING",
                expected_epoch=1,
            )
            with self.assertRaises(fence.FenceError):
                fence.claim(competing_claim)
            self.assertFalse(runtime_called)

            check = SimpleNamespace(
                platform="ANTIGRAVITY",
                instance=execution_id,
                epoch=1,
                integration_head=integration_head,
                assignment=new_id,
            )
            fence.check(check)
            replacement_path = assignments / f"{new_id}.md"
            previous_path = assignments / f"{old_id}.md"
            valid_replacement = replacement_path.read_text(encoding="utf-8")
            valid_previous = previous_path.read_text(encoding="utf-8")
            valid_fence = fence.FENCE.read_text(encoding="utf-8")

            replacement_path.write_text(
                valid_replacement.replace(stage_head, "0" * 40, 1),
                encoding="utf-8",
            )
            with self.assertRaises(fence.FenceError):
                fence.check(check)
            replacement_path.write_text(valid_replacement, encoding="utf-8")

            replacement_path.write_text(
                valid_replacement.replace("external_access: NONE", "external_access: LIVE"),
                encoding="utf-8",
            )
            with self.assertRaises(fence.FenceError):
                fence.check(check)
            replacement_path.write_text(valid_replacement, encoding="utf-8")

            previous_path.write_text(
                valid_previous.replace("external_access: NONE", "external_access: LIVE"),
                encoding="utf-8",
            )
            with self.assertRaises(fence.FenceError):
                fence.check(check)
            previous_path.write_text(valid_previous, encoding="utf-8")

            invalid_path = f"{stage_root}-wrong"
            previous_path.write_text(
                valid_previous.replace(str(stage_root), invalid_path), encoding="utf-8"
            )
            replacement_path.write_text(
                valid_replacement.replace(str(stage_root), invalid_path), encoding="utf-8"
            )
            fence_payload = json.loads(valid_fence)
            fence_payload["immutable_contract_sha256"] = fence.immutable_contract_sha256(
                fence.load_assignment(new_id)
            )
            fence.FENCE.write_text(json.dumps(fence_payload), encoding="utf-8")
            with self.assertRaises(fence.FenceError):
                fence.check(check)
            previous_path.write_text(valid_previous, encoding="utf-8")
            replacement_path.write_text(valid_replacement, encoding="utf-8")
            fence.FENCE.write_text(valid_fence, encoding="utf-8")

            previous_path.write_text(
                valid_previous.replace("2099-01-01T00:00:00Z", "2000-01-01T00:00:00Z"),
                encoding="utf-8",
            )
            replacement_path.write_text(
                valid_replacement.replace("2099-01-01T00:00:00Z", "2000-01-01T00:00:00Z"),
                encoding="utf-8",
            )
            fence_payload = json.loads(valid_fence)
            fence_payload["immutable_contract_sha256"] = fence.immutable_contract_sha256(
                fence.load_assignment(new_id)
            )
            fence.FENCE.write_text(json.dumps(fence_payload), encoding="utf-8")
            with self.assertRaises(fence.FenceError):
                fence.check(check)
            previous_path.write_text(valid_previous, encoding="utf-8")
            replacement_path.write_text(valid_replacement, encoding="utf-8")
            fence.FENCE.write_text(valid_fence, encoding="utf-8")

            worker_check = SimpleNamespace(**vars(check))
            (stage_root / "stage.txt").write_text("candidate two\n", encoding="utf-8")
            fence.worker_check(worker_check)
            with self.assertRaises(fence.FenceError):
                fence.check(check)
            git(stage_root, "add", "stage.txt")
            git(stage_root, "commit", "-qm", "worker candidate child")
            candidate_head = git(stage_root, "rev-parse", "HEAD")
            fence.worker_check(worker_check)
            stage_advance = SimpleNamespace(**{**vars(check), "stage_head": candidate_head})
            fence.stage_advance(stage_advance)
            fence.check(check)

            (root / "tracked.txt").write_text("two\n", encoding="utf-8")
            git(root, "add", "tracked.txt")
            git(root, "commit", "-qm", "integration child")
            next_head = git(root, "rev-parse", "HEAD")
            advance = SimpleNamespace(**{**vars(check), "integration_head": next_head})
            fence.advance(advance)
            check.integration_head = next_head
            fence.check(check)

            next_stage_root = Path(directory) / "stage-next"
            git(
                root,
                "worktree",
                "add",
                "-q",
                "-b",
                "refactor/stage/P1-01",
                str(next_stage_root),
                next_head,
            )
            next_stage_head = git(next_stage_root, "rev-parse", "HEAD")
            next_assignment_id = "WA-P1-01-ANTIGRAVITY"
            (assignments / f"{next_assignment_id}.md").write_text(
                f"""# Work Assignment — `{next_assignment_id}`

```yaml
assignment_id: {next_assignment_id}
stage_id: P1-01
role: WORKER
status: READY
supersedes: null
base_sha: {next_stage_head}
branch: refactor/stage/P1-01
worktree_path: {next_stage_root}
worktree_registry_id: stage-P1-01
allowed_paths: [src/**]
forbidden_paths: [deploy/**]
locks: []
parallel_peer: null
deliverables: [implementation]
tests: [focused]
failure_tests: [negative]
rollback_test: revert
external_access: NONE
expires_at: '2099-01-01T00:00:00Z'
issued_by: EXECUTION_COORDINATOR
issued_at: '2099-01-01T00:00:00Z'
orchestrator: ANTIGRAVITY
orchestration_epoch: 1
integration_branch: refactor/two-site-architecture
integration_head_at_issue: {next_head}
```
""",
                encoding="utf-8",
            )
            registry = yaml.safe_load(fence.REGISTRY.read_text(encoding="utf-8"))
            registry["stage_worktrees"].append(
                {
                    "registry_id": "stage-P1-01",
                    "stage_id": "P1-01",
                    "assignment_id": next_assignment_id,
                    "owner_role": "EXECUTION_WORKER",
                    "orchestrator": "ANTIGRAVITY",
                    "orchestration_epoch": 1,
                    "path": str(next_stage_root),
                    "branch": "refactor/stage/P1-01",
                    "base_sha": next_stage_head,
                    "head_sha": next_stage_head,
                    "dirty_uncommitted_candidate": False,
                    "locks": [],
                    "status": "READY",
                }
            )
            fence.REGISTRY.write_text(
                yaml.safe_dump(registry, sort_keys=False), encoding="utf-8"
            )
            stage_bind = SimpleNamespace(
                **vars(check), next_assignment=next_assignment_id
            )
            next_assignment_path = assignments / f"{next_assignment_id}.md"
            valid_next_assignment = next_assignment_path.read_text(encoding="utf-8")
            canonical_next = fence.load_assignment(next_assignment_id)
            unknown = dict(canonical_next)
            unknown["future_contract_field"] = True
            with self.assertRaises(fence.FenceError):
                fence.validate_canonical_assignment(unknown)
            for field in (
                "allowed_paths",
                "forbidden_paths",
                "locks",
                "deliverables",
                "tests",
                "failure_tests",
            ):
                malformed = dict(canonical_next)
                malformed[field] = "not-an-array"
                with self.assertRaises(fence.FenceError):
                    fence.validate_canonical_assignment(malformed)
            for field, value in (
                ("status", "COMPLETE"),
                ("supersedes", 7),
                ("parallel_peer", []),
                ("worktree_registry_id", []),
                ("rollback_test", []),
                ("external_access", True),
            ):
                malformed = dict(canonical_next)
                malformed[field] = value
                with self.assertRaises(fence.FenceError):
                    fence.validate_canonical_assignment(malformed)

            original_load_yaml = fence.load_yaml
            stage_open = SimpleNamespace(**vars(check), next_stage="P1-01")
            for flag in (
                "production",
                "destructive",
                "dns_change",
                "writer_change",
                "secret_change",
                "future_external_risk",
            ):
                guarded_order = yaml.safe_load(order.read_text(encoding="utf-8"))
                guarded_order["stages"]["P1-01"][flag] = True

                def guarded_load(path, payload=guarded_order):
                    return payload if path == order else original_load_yaml(path)

                with mock.patch.object(fence, "load_yaml", side_effect=guarded_load):
                    with self.assertRaises(fence.FenceError):
                        fence.stage_bind(stage_bind)
                self.assertEqual(new_id, fence.load_fence()["active_assignment_id"])

                open_order = yaml.safe_load(order.read_text(encoding="utf-8"))
                open_order["stages"]["P1-01"]["state"] = "PROPOSED"
                open_order["stages"]["P1-01"][flag] = True

                def guarded_open_load(path, payload=open_order):
                    return payload if path == order else original_load_yaml(path)

                with mock.patch.object(fence, "load_yaml", side_effect=guarded_open_load):
                    with self.assertRaises(fence.FenceError):
                        fence.stage_open_check(stage_open)

            proposed_order = yaml.safe_load(order.read_text(encoding="utf-8"))
            proposed_order["stages"]["P1-01"]["state"] = "PROPOSED"

            def proposed_load(path):
                return proposed_order if path == order else original_load_yaml(path)

            with mock.patch.object(fence, "load_yaml", side_effect=proposed_load):
                fence.stage_open_check(stage_open)

            next_assignment_path.write_text(
                valid_next_assignment.replace("external_access: NONE", "external_access: LIVE"),
                encoding="utf-8",
            )
            with self.assertRaises(fence.FenceError):
                fence.stage_bind(stage_bind)
            self.assertEqual(new_id, fence.load_fence()["active_assignment_id"])
            next_assignment_path.write_text(valid_next_assignment, encoding="utf-8")

            stale_base_assignment = valid_next_assignment.replace(
                f"base_sha: {next_stage_head}", f"base_sha: {stage_head}"
            )
            next_assignment_path.write_text(stale_base_assignment, encoding="utf-8")
            with self.assertRaises(fence.FenceError):
                fence.stage_bind(stage_bind)
            next_assignment_path.write_text(valid_next_assignment, encoding="utf-8")

            valid_registry = fence.REGISTRY.read_text(encoding="utf-8")
            mismatched_registry = yaml.safe_load(valid_registry)
            next_record = [
                item
                for item in mismatched_registry["stage_worktrees"]
                if item["assignment_id"] == next_assignment_id
            ][0]
            next_record["base_sha"] = stage_head
            fence.REGISTRY.write_text(
                yaml.safe_dump(mismatched_registry, sort_keys=False), encoding="utf-8"
            )
            with self.assertRaises(fence.FenceError):
                fence.stage_bind(stage_bind)
            fence.REGISTRY.write_text(valid_registry, encoding="utf-8")

            mismatched_head_registry = yaml.safe_load(valid_registry)
            next_head_record = [
                item
                for item in mismatched_head_registry["stage_worktrees"]
                if item["assignment_id"] == next_assignment_id
            ][0]
            next_head_record["head_sha"] = stage_head
            fence.REGISTRY.write_text(
                yaml.safe_dump(mismatched_head_registry, sort_keys=False), encoding="utf-8"
            )
            with self.assertRaises(fence.FenceError):
                fence.stage_bind(stage_bind)
            fence.REGISTRY.write_text(valid_registry, encoding="utf-8")

            unrelated_next = Path(directory) / "unrelated-next"
            subprocess.run(
                ["git", "clone", "-q", str(root), str(unrelated_next)],
                check=True,
                capture_output=True,
                text=True,
            )
            git(
                unrelated_next,
                "switch",
                "-qc",
                "refactor/stage/P1-01",
                next_head,
            )
            next_assignment_path.write_text(
                valid_next_assignment.replace(str(next_stage_root), str(unrelated_next)),
                encoding="utf-8",
            )
            unrelated_registry = yaml.safe_load(valid_registry)
            unrelated_record = [
                item
                for item in unrelated_registry["stage_worktrees"]
                if item["assignment_id"] == next_assignment_id
            ][0]
            unrelated_record["path"] = str(unrelated_next)
            fence.REGISTRY.write_text(
                yaml.safe_dump(unrelated_registry, sort_keys=False), encoding="utf-8"
            )
            with self.assertRaises(fence.FenceError):
                fence.stage_bind(stage_bind)
            next_assignment_path.write_text(valid_next_assignment, encoding="utf-8")
            fence.REGISTRY.write_text(valid_registry, encoding="utf-8")

            fence.stage_bind(stage_bind)
            next_check = SimpleNamespace(
                **{**vars(check), "assignment": next_assignment_id}
            )
            fence.check(next_check)
            bound_registry = yaml.safe_load(fence.REGISTRY.read_text(encoding="utf-8"))
            self.assertEqual(
                next_assignment_id,
                bound_registry["integration"]["active_assignment_id"],
            )
            closed = [
                item
                for item in bound_registry["stage_worktrees"]
                if item["assignment_id"] == new_id
            ][0]
            self.assertEqual("CLOSED", closed["status"])
            fence.release(next_check)
            self.assertEqual(fence.load_fence()["status"], "RELEASED")

    def test_governance_review_blocks_lowercase_stage_suffix_change(self):
        def git(repo: Path, *args: str) -> str:
            return subprocess.run(
                ["git", *args], cwd=repo, check=True, capture_output=True, text=True
            ).stdout.strip()

        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            repo = sandbox / "repo"
            fake_bin = sandbox / "bin"
            repo.mkdir()
            fake_bin.mkdir()
            git(repo, "init", "-q")
            git(repo, "config", "user.name", "Governance Test")
            git(repo, "config", "user.email", "governance@example.invalid")
            git(repo, "switch", "-qc", "refactor/two-site-architecture")
            (repo / ".gitignore").write_text("/.local/\n", encoding="utf-8")
            script = repo / "scripts" / "two_site_codex_governance_review.sh"
            schema = (
                repo
                / "docs"
                / "refactor"
                / "two-server"
                / "templates"
                / "governance-review.schema.json"
            )
            order = repo / "docs" / "refactor" / "two-server" / "execution-order.yaml"
            script.parent.mkdir(parents=True)
            schema.parent.mkdir(parents=True)
            script.write_text(
                (ROOT / "scripts" / "two_site_codex_governance_review.sh").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            script.chmod(0o755)
            schema.write_text(
                (
                    ROOT
                    / "docs"
                    / "refactor"
                    / "two-server"
                    / "templates"
                    / "governance-review.schema.json"
                ).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            order.write_text(
                "stages:\n  P1-00: {state: COMPLETE}\n  P1-00b: {state: IN_PROGRESS}\n",
                encoding="utf-8",
            )
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "governance base")
            base = git(repo, "rev-parse", "HEAD")
            order.write_text(
                "stages:\n  P1-00: {state: COMPLETE}\n  P1-00b: {state: COMPLETE}\n",
                encoding="utf-8",
            )
            git(repo, "add", str(order.relative_to(repo)))
            git(repo, "commit", "-qm", "illicit state change")
            head = git(repo, "rev-parse", "HEAD")

            fake_codex = fake_bin / "codex"
            fake_codex.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_codex.chmod(0o755)
            amendment = "TEST-GOV-1"
            local = repo / ".local" / "two-site-refactor"
            bundle = local / "governance-bundles" / amendment
            bundle.mkdir(parents=True)
            for name in ("SCOPE.md", "tests.txt"):
                (bundle / name).write_text("test\n", encoding="utf-8")
            identity = f"base={base}\nhead={head}\n"
            (bundle / "commit.txt").write_text(identity, encoding="utf-8")
            (bundle / "FINAL_REVIEW_REQUEST.md").write_text(identity, encoding="utf-8")
            authorization = local / "authorizations" / "CODEX_SOURCE_REVIEW.md"
            authorization.parent.mkdir(parents=True)
            authorization.write_text(
                "authorization: APPROVED\n"
                "scope: TWO_SITE_REFACTOR_SOURCE_AND_EVIDENCE_TO_OPENAI_CODEX\n",
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            result = subprocess.run(
                [
                    "bash",
                    str(script),
                    "--amendment",
                    amendment,
                    "--bundle",
                    f".local/two-site-refactor/governance-bundles/{amendment}",
                    "--base",
                    base,
                    "--head",
                    head,
                ],
                cwd=repo,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("changed authoritative Stage state", result.stderr)


if __name__ == "__main__":
    unittest.main()
