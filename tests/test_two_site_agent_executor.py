import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.two_site_agent_executor import (
    LocalOperationRunner,
    PinnedPaths,
    execute_plan,
    observe_git,
)
from scripts.two_site_agent_gateway import (
    ActorRole,
    CommandPlan,
    EnvClass,
    FollowupCommandPlan,
    GitCheckpoint,
    OperationPlan,
    RESULTING_HEAD,
)


ASSIGNMENT = "P1-00b-antigravity-1"
STAGE = "P1-00b"
EPOCH = 9
ACTOR = "11111111-1111-4111-8111-111111111111"
COORDINATOR = "22222222-2222-4222-8222-222222222222"


class RecordingRunner:
    def __init__(self, *, test_returncode=0, mutate_after_command=None):
        self.local = LocalOperationRunner()
        self.test_returncode = test_returncode
        self.mutate_after_command = mutate_after_command
        self.commands = []
        self.hermetic = []

    def run_command(self, argv, *, cwd, env, timeout, output_limit):
        self.commands.append((tuple(argv), cwd, dict(env)))
        result = self.local.run_command(
            argv, cwd=cwd, env=env, timeout=timeout, output_limit=output_limit
        )
        if self.mutate_after_command is not None:
            callback, self.mutate_after_command = self.mutate_after_command, None
            callback()
        return result

    def run_hermetic(self, worktree, selectors, *, expected_head, extra_paths, timeout):
        self.hermetic.append((worktree, tuple(selectors), expected_head, tuple(extra_paths), timeout))
        return {
            "returncode": self.test_returncode,
            "stdout": "tests passed\n" if self.test_returncode == 0 else "",
            "stderr": "" if self.test_returncode == 0 else "test failed\n",
            "isolation": "fixture-hermetic-double",
        }


class GitFixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="two-site-executor-test-")
        self.container = Path(self.temporary.name)
        self.root = self.container / "integration"
        self.stage = self.container / "stage"
        self.root.mkdir()
        self.git(self.root, "init", "-q", "-b", "refactor/two-site-architecture")
        self.git(self.root, "config", "user.name", "Executor Test")
        self.git(self.root, "config", "user.email", "executor@test.invalid")
        self.git(self.root, "config", "commit.gpgsign", "false")
        self.write(self.root / "app.txt", "base\n")
        self.write(self.root / "other.txt", "other\n")
        self.write(self.root / "config" / "unit-test.env.example", "APP_ENV=test\n")
        self.write(self.root / "tests" / "test_sample.py", "def test_sample():\n    assert True\n")
        self.write(self.root / "scripts" / "check_memory_secrets.py", "raise SystemExit(0)\n")
        self.write(self.root / "scripts" / "two_site_orchestrator_fence.py", "raise SystemExit(0)\n")
        self.write(self.root / "scripts" / "two_site_codex_final_review.sh", "#!/bin/sh\nexit 0\n", executable=True)
        self.write(self.root / "scripts" / "two_site_codex_closeout_review.sh", "#!/bin/sh\nexit 0\n", executable=True)
        self.git(self.root, "add", "--all")
        self.git(self.root, "commit", "-qm", "base")
        self.git(self.root, "branch", "main")
        self.base = self.head(self.root)
        hook = self.root / ".git" / "hooks" / "pre-commit"
        self.write(
            hook,
            "#!/bin/sh\nset -eu\n\ngit diff --cached --check\ngit show main:scripts/check_memory_secrets.py | python3 -\n",
            executable=True,
        )
        self.git(
            self.root,
            "worktree", "add", "-q", "-b", "refactor/stage/P1-00b-gateway", str(self.stage), self.base,
        )
        self.write(self.root / "integration.txt", "integration\n")
        self.git(self.root, "add", "integration.txt")
        self.git(self.root, "commit", "-qm", "integration baseline")
        self.integration_head = self.head(self.root)
        self.pins = self.make_pins()

    def cleanup(self):
        self.temporary.cleanup()

    @staticmethod
    def write(path: Path, value: str, *, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        if executable:
            path.chmod(0o755)

    @staticmethod
    def git(repo: Path, *arguments: str, check=True):
        return subprocess.run(
            ["/usr/bin/git", "-C", str(repo), *arguments],
            check=check,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": os.environ.get("HOME", "/root")},
        )

    def head(self, repo: Path):
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    @staticmethod
    def digest(path: Path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def make_pins(self):
        python = Path(sys.executable).resolve()
        git = Path("/usr/bin/git")
        fence = self.root / "scripts" / "two_site_orchestrator_fence.py"
        final = self.root / "scripts" / "two_site_codex_final_review.sh"
        closeout = self.root / "scripts" / "two_site_codex_closeout_review.sh"
        return PinnedPaths(
            self.root,
            self.stage,
            "refactor/two-site-architecture",
            "refactor/stage/P1-00b-gateway",
            git,
            self.digest(git),
            python,
            self.digest(python),
            fence,
            self.digest(fence),
            final,
            self.digest(final),
            closeout,
            self.digest(closeout),
        )

    def request(self, operation, **extra):
        return {
            "id": str(uuid.uuid4()),
            "operation": operation,
            "actor_id": extra.pop("actor_id", ACTOR),
            "assignment": ASSIGNMENT,
            "epoch": EPOCH,
            "integration_head": self.integration_head,
            **extra,
        }

    def bindings(self, request, **extra):
        values = {
            "assignment": ASSIGNMENT,
            "stage": STAGE,
            "epoch": str(EPOCH),
            "integration_head": request["integration_head"],
            **{key: str(value) for key, value in extra.items()},
        }
        return tuple(sorted(values.items()))

    def commit_bindings(self):
        hook = self.root / ".git" / "hooks" / "pre-commit"
        guard = self.git(self.root, "show", "main:scripts/check_memory_secrets.py").stdout.encode()
        return {
            "hook_sha256": self.digest(hook),
            "memory_guard_sha256": hashlib.sha256(guard).hexdigest(),
        }

    def plan(self, request, role, commands, *, checkpoints=(), followups=(), **bindings):
        return OperationPlan(
            request["id"],
            request["operation"],
            request["actor_id"],
            role,
            tuple(commands),
            tuple(followups),
            tuple(checkpoints),
            (),
            (),
            self.bindings(request, **bindings),
        )

    def fence(self, subcommand, *, integration_head=None, stage_head=None):
        argv = (
            sys.executable,
            str(self.pins.fence_script),
            subcommand,
            "--platform", "ANTIGRAVITY",
            "--instance", COORDINATOR,
            "--epoch", str(EPOCH),
            "--integration-head", integration_head or self.integration_head,
            "--assignment", ASSIGNMENT,
        )
        if stage_head is not None:
            argv += ("--stage-head", stage_head)
        return CommandPlan(argv, str(self.root), EnvClass.FENCE, f"fence {subcommand}")

    @staticmethod
    def status(path):
        return CommandPlan(
            ("git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"),
            str(path), EnvClass.READ_ONLY, "status",
        )

    def worker_commit_plan(self, expected_patch):
        request = self.request(
            "worker-commit",
            expected_patch_sha256=expected_patch,
            test_receipt_sha256="9" * 64,
            message=f"{STAGE}: candidate",
            allowed_paths=["app.txt"],
        )
        checkpoint = GitCheckpoint(1, str(self.stage), self.head(self.stage), ("app.txt",), expected_patch, True, True)
        commands = (
            self.fence("worker-check"),
            CommandPlan(("git", "-C", str(self.stage), "add", "--", "app.txt"), str(self.stage), EnvClass.GIT_MUTATION, "stage"),
            CommandPlan(("git", "-C", str(self.stage), "diff", "--cached", "--check"), str(self.stage), EnvClass.READ_ONLY, "check"),
            CommandPlan(("git", "-C", str(self.stage), "commit", "-m", request["message"]), str(self.stage), EnvClass.GIT_MUTATION, "commit"),
            self.status(self.stage),
        )
        return request, self.plan(
            request,
            ActorRole.WORKER,
            commands,
            checkpoints=(checkpoint,),
            **self.commit_bindings(),
        )


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.fx = GitFixture()
        self.boundaries = []

    def tearDown(self):
        self.fx.cleanup()

    def before(self, boundary):
        self.boundaries.append(boundary)

    def test_observe_git_includes_untracked_without_changing_real_index_or_objects(self):
        (self.fx.stage / "app.txt").write_text("changed\n")
        (self.fx.stage / "other.txt").write_text("staged\n")
        self.fx.git(self.fx.stage, "add", "other.txt")
        (self.fx.stage / "untracked.txt").write_text("new\n")
        index = Path(self.fx.git(self.fx.stage, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout.strip())
        before_index = index.read_bytes()
        objects = self.fx.root / ".git" / "objects"
        before_objects = sorted(str(path.relative_to(objects)) for path in objects.rglob("*") if path.is_file())
        snapshot = observe_git(self.fx.stage)
        self.assertEqual(("other.txt",), snapshot.staged_paths)
        self.assertEqual(("app.txt",), snapshot.unstaged_paths)
        self.assertEqual(("untracked.txt",), snapshot.untracked_paths)
        self.assertRegex(snapshot.worktree_patch_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(snapshot.staged_patch_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(before_index, index.read_bytes())
        self.assertEqual(before_objects, sorted(str(path.relative_to(objects)) for path in objects.rglob("*") if path.is_file()))

    def test_forged_argv_and_cwd_are_blocked_before_runner(self):
        request = self.fx.request("status")
        plan = self.fx.plan(
            request,
            ActorRole.WORKER,
            (CommandPlan(("sh", "-c", "id"), str(self.fx.stage), EnvClass.READ_ONLY, "forged"),),
        )
        runner = RecordingRunner()
        outcome = execute_plan(plan, request, runner=runner, pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("BLOCKED", outcome["status"])
        self.assertEqual([], runner.commands)
        valid = self.fx.plan(request, ActorRole.WORKER, (self.fx.status(self.fx.stage),))
        drifted = replace(valid, commands=(replace(valid.commands[0], cwd="/tmp"),))
        outcome = execute_plan(drifted, request, runner=runner, pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("BLOCKED", outcome["status"])
        self.assertEqual([], runner.commands)

    def test_forged_worker_commit_role_is_blocked_before_mutation(self):
        (self.fx.stage / "app.txt").write_text("candidate\n")
        expected = observe_git(self.fx.stage).worktree_patch_sha256
        request, plan = self.fx.worker_commit_plan(expected)
        forged = replace(plan, role=ActorRole.COORDINATOR)
        runner = RecordingRunner()
        outcome = execute_plan(
            forged, request, runner=runner, pinned_paths=self.fx.pins, before_mutation=self.before
        )
        self.assertEqual("BLOCKED", outcome["status"])
        self.assertEqual([], runner.commands)
        self.assertEqual([], self.boundaries)

    def test_readonly_source_drift_is_detected(self):
        request = self.fx.request("status")
        plan = self.fx.plan(request, ActorRole.WORKER, (self.fx.status(self.fx.stage),))
        runner = RecordingRunner(mutate_after_command=lambda: (self.fx.stage / "app.txt").write_text("drift\n"))
        outcome = execute_plan(plan, request, runner=runner, pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("FAILED", outcome["status"])
        self.assertIn("changed Git state", outcome["reason"])

    def test_stale_integration_head_blocks_before_command(self):
        request = self.fx.request("status")
        request["integration_head"] = "e" * 40
        plan = self.fx.plan(request, ActorRole.WORKER, (self.fx.status(self.fx.stage),))
        runner = RecordingRunner()
        outcome = execute_plan(plan, request, runner=runner, pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("BLOCKED", outcome["status"])
        self.assertEqual([], runner.commands)

    def test_worker_commit_runs_real_hook_and_proves_direct_exact_commit(self):
        (self.fx.stage / "app.txt").write_text("candidate\n")
        expected = observe_git(self.fx.stage).worktree_patch_sha256
        request, plan = self.fx.worker_commit_plan(expected)
        parent = self.fx.head(self.fx.stage)
        outcome = execute_plan(
            plan, request, runner=RecordingRunner(), pinned_paths=self.fx.pins, before_mutation=self.before
        )
        self.assertEqual("SUCCESS", outcome["status"], outcome)
        self.assertEqual(parent, self.fx.git(self.fx.stage, "rev-parse", "HEAD^").stdout.strip())
        self.assertEqual("", self.fx.git(self.fx.stage, "status", "--porcelain").stdout)
        self.assertEqual(2, len(self.boundaries))
        commit_argv = outcome["commands"][3]
        self.assertEqual(0, commit_argv["returncode"])

    def test_memory_guard_failure_is_not_bypassed(self):
        guard = self.fx.root / "scripts" / "check_memory_secrets.py"
        guard.write_text("raise SystemExit(23)\n")
        self.fx.git(self.fx.root, "add", str(guard.relative_to(self.fx.root)))
        self.fx.git(self.fx.root, "commit", "-qm", "make main guard reject")
        self.fx.git(self.fx.root, "branch", "-f", "main", "HEAD")
        self.fx.integration_head = self.fx.head(self.fx.root)
        self.fx.pins = self.fx.make_pins()
        (self.fx.stage / "app.txt").write_text("candidate\n")
        expected = observe_git(self.fx.stage).worktree_patch_sha256
        request, plan = self.fx.worker_commit_plan(expected)
        parent = self.fx.head(self.fx.stage)
        outcome = execute_plan(
            plan, request, runner=RecordingRunner(), pinned_paths=self.fx.pins, before_mutation=self.before
        )
        self.assertEqual("FAILED", outcome["status"])
        self.assertEqual(parent, self.fx.head(self.fx.stage))
        self.assertIn("app.txt", self.fx.git(self.fx.stage, "diff", "--cached", "--name-only").stdout)

    def test_checkpoint_rejects_staged_patch_drift_before_commit(self):
        (self.fx.stage / "app.txt").write_text("candidate\n")
        request, plan = self.fx.worker_commit_plan("f" * 64)
        parent = self.fx.head(self.fx.stage)
        runner = RecordingRunner()
        outcome = execute_plan(plan, request, runner=runner, pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("FAILED", outcome["status"])
        self.assertIn("checkpoint staged patch", outcome["reason"])
        self.assertEqual(parent, self.fx.head(self.fx.stage))
        self.assertFalse(any("commit" in call[0] for call in runner.commands))

    def test_hermetic_test_failure_stops_and_preserves_git(self):
        request = self.fx.request("test", selectors=["tests/test_sample.py"])
        commands = (
            self.fx.status(self.fx.stage),
            self.fx.fence("worker-check"),
            CommandPlan(
                (sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1", "--", "tests/test_sample.py"),
                str(self.fx.stage), EnvClass.HERMETIC_TEST, "test",
            ),
            self.fx.status(self.fx.stage),
        )
        plan = self.fx.plan(request, ActorRole.WORKER, commands)
        runner = RecordingRunner(test_returncode=1)
        before = observe_git(self.fx.stage)
        outcome = execute_plan(plan, request, runner=runner, pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("FAILED", outcome["status"])
        self.assertEqual(before, observe_git(self.fx.stage))
        self.assertEqual(1, len(runner.hermetic))

    def test_hermetic_test_receives_only_controller_observed_untracked_sources(self):
        new_test = self.fx.stage / "tests" / "test_new_worker_source.py"
        new_test.write_text("def test_new():\n    assert True\n")
        request = self.fx.request("test", selectors=["tests/test_new_worker_source.py"])
        commands = (
            self.fx.status(self.fx.stage),
            self.fx.fence("worker-check"),
            CommandPlan(
                (
                    sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1", "--",
                    "tests/test_new_worker_source.py",
                ),
                str(self.fx.stage), EnvClass.HERMETIC_TEST, "test untracked source",
            ),
            self.fx.status(self.fx.stage),
        )
        plan = self.fx.plan(request, ActorRole.WORKER, commands)
        runner = RecordingRunner()
        outcome = execute_plan(plan, request, runner=runner, pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("SUCCESS", outcome["status"], outcome)
        self.assertEqual(("tests/test_new_worker_source.py",), runner.hermetic[0][3])

    def test_local_runner_forwards_observed_untracked_paths_to_source_snapshot(self):
        with patch(
            "scripts.two_site_agent_executor.run_tests",
            return_value={"returncode": 0, "stdout": "ok", "stderr": ""},
        ) as sandbox:
            result = LocalOperationRunner().run_hermetic(
                self.fx.stage,
                ("tests/test_new_worker_source.py",),
                expected_head=self.fx.base,
                extra_paths=("tests/test_new_worker_source.py",),
                timeout=1200,
            )
        self.assertEqual(0, result["returncode"])
        sandbox.assert_called_once_with(
            self.fx.stage,
            ["tests/test_new_worker_source.py"],
            extra_paths=["tests/test_new_worker_source.py"],
            timeout=1200,
            git_ancestry=None,
        )

    def test_external_review_without_supervisor_is_blocked_not_executed(self):
        request = self.fx.request(
            "review-request",
            actor_id=COORDINATOR,
            bundle=f".local/two-site-refactor/review-bundles/{STAGE}-{ASSIGNMENT}",
            base_sha=self.fx.base,
            head_sha=self.fx.integration_head,
        )
        command = CommandPlan(
            (
                str(self.fx.pins.final_review_bridge), "--stage", STAGE, "--assignment", ASSIGNMENT,
                "--bundle", request["bundle"], "--base", request["base_sha"], "--head", request["head_sha"],
            ),
            str(self.fx.root), EnvClass.EXTERNAL_CODEX_REVIEW, "review",
        )
        plan = self.fx.plan(request, ActorRole.COORDINATOR, (command,))
        runner = RecordingRunner()
        outcome = execute_plan(plan, request, runner=runner, pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("BLOCKED", outcome["status"])
        self.assertEqual([], runner.commands)

    def test_real_next_stage_transition_commit_and_fence_advance(self):
        order = self.fx.root / "docs/refactor/two-server/execution-order.yaml"
        self.fx.write(order, "stages:\n  P1-01: {state: IN_PROGRESS}\n")
        self.fx.git(self.fx.root, "add", str(order.relative_to(self.fx.root)))
        patch_sha = observe_git(self.fx.root).staged_patch_sha256
        self.assertIsNotNone(patch_sha)
        request = self.fx.request(
            "next-stage-commit", actor_id=COORDINATOR, next_stage="P1-01",
            expected_patch_sha256=patch_sha, prepare_receipt_sha256="a" * 64,
            message="P1-01: open bounded Stage",
        )
        command = CommandPlan(
            ("git", "-C", str(self.fx.root), "commit", "-m", request["message"]),
            str(self.fx.root), EnvClass.GIT_MUTATION, "transition",
        )
        fence = self.fx.fence("advance", integration_head=RESULTING_HEAD)
        followup = FollowupCommandPlan(
            fence.argv, fence.cwd, fence.env_class, RESULTING_HEAD, "advance",
        )
        plan = self.fx.plan(
            request, ActorRole.COORDINATOR, (command,), followups=(followup,),
            next_stage="P1-01", patch=patch_sha, prepare_receipt="receipt.json",
            **self.fx.commit_bindings(),
        )
        outcome = execute_plan(
            plan, request, runner=RecordingRunner(), pinned_paths=self.fx.pins,
            before_mutation=self.before,
        )
        self.assertEqual("SUCCESS", outcome["status"], outcome)
        self.assertRegex(outcome["resulting_head"], r"^[0-9a-f]{40}$")
        self.assertEqual(outcome["resulting_head"], self.fx.head(self.fx.root))

    def test_receipt_check_empty_plan_executes_no_runner_or_git_observation(self):
        request = self.fx.request(
            "receipt-check",
            actor_id=COORDINATOR,
            receipt_kind="CODEX_FINAL",
            receipt_sha256="c" * 64,
            reviewed_sha=self.fx.integration_head,
        )
        plan = self.fx.plan(request, ActorRole.COORDINATOR, ())
        runner = RecordingRunner()
        # A dirty path would be visible to observe_git; receipt-check intentionally
        # consumes only the planner/controller-validated receipt binding.
        (self.fx.stage / "unobserved.txt").write_text("dirty\n")
        outcome = execute_plan(plan, request, runner=runner, pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("SUCCESS", outcome["status"])
        self.assertEqual([], outcome["commands"])
        self.assertEqual([], outcome["pre_git"])
        self.assertEqual([], runner.commands)

    def test_real_non_ff_integration_checkpoints_commit_and_fence_followup(self):
        (self.fx.stage / "app.txt").write_text("verified candidate\n")
        self.fx.git(self.fx.stage, "add", "app.txt")
        self.fx.git(self.fx.stage, "commit", "-qm", f"{STAGE}: candidate")
        candidate = self.fx.head(self.fx.stage)
        candidate_parent = self.fx.git(self.fx.stage, "rev-parse", "HEAD^").stdout.strip()
        patch = subprocess.run(
            ["/usr/bin/git", "-C", str(self.fx.stage), "diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-renames", candidate_parent, candidate, "--"],
            check=True, capture_output=True,
        ).stdout
        expected = hashlib.sha256(patch).hexdigest()
        request = self.fx.request(
            "integrate",
            actor_id=COORDINATOR,
            candidate_sha=candidate,
            expected_patch_sha256=expected,
            test_selectors=["tests/test_sample.py"],
            verifier_receipt_sha256="a" * 64,
            message=f"{STAGE}: integrate candidate",
            allowed_paths=["app.txt"],
        )
        paths = ("app.txt",)
        checkpoints = (
            GitCheckpoint(1, str(self.fx.root), self.fx.integration_head, paths, expected, True, True),
            GitCheckpoint(3, str(self.fx.root), self.fx.integration_head, paths, expected, True, True),
        )
        commands = (
            self.fx.fence("check"),
            CommandPlan(("git", "-C", str(self.fx.root), "cherry-pick", "--no-commit", candidate), str(self.fx.root), EnvClass.GIT_MUTATION, "cherry-pick"),
            CommandPlan(("git", "-C", str(self.fx.root), "diff", "--cached", "--check"), str(self.fx.root), EnvClass.READ_ONLY, "check"),
            CommandPlan((sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1", "--", "tests/test_sample.py"), str(self.fx.root), EnvClass.HERMETIC_TEST, "test"),
            CommandPlan(("git", "-C", str(self.fx.root), "diff", "--cached", "--binary", "--full-index", "--"), str(self.fx.root), EnvClass.READ_ONLY, "patch"),
            CommandPlan(("git", "-C", str(self.fx.root), "commit", "-m", request["message"]), str(self.fx.root), EnvClass.GIT_MUTATION, "commit"),
        )
        advance = self.fx.fence("advance", integration_head=RESULTING_HEAD)
        followup = FollowupCommandPlan(advance.argv, advance.cwd, advance.env_class, RESULTING_HEAD, "advance")
        plan = self.fx.plan(
            request,
            ActorRole.COORDINATOR,
            commands,
            checkpoints=checkpoints,
            followups=(followup,),
            **self.fx.commit_bindings(),
        )
        parent = self.fx.integration_head
        outcome = execute_plan(plan, request, runner=RecordingRunner(), pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("SUCCESS", outcome["status"], outcome)
        self.assertEqual(parent, self.fx.git(self.fx.root, "rev-parse", "HEAD^").stdout.strip())
        self.assertEqual("", self.fx.git(self.fx.root, "status", "--porcelain").stdout)
        self.assertEqual(3, len(self.boundaries))

    def test_real_closeout_commit_verifies_then_advances_without_approval_claim(self):
        paths = (
            "docs/refactor/two-server/CHANGE_LEDGER.md",
            "docs/refactor/two-server/execution-order.yaml",
            f"docs/refactor/two-server/reviews/{STAGE}-{ASSIGNMENT}.json",
            f"docs/refactor/two-server/reviews/{STAGE}-{ASSIGNMENT}.md",
        )
        for relative in paths:
            self.fx.write(self.fx.root / relative, relative + "\n")
        self.fx.git(self.fx.root, "add", "--", *paths)
        expected = observe_git(self.fx.root).staged_patch_sha256
        request = self.fx.request(
            "closeout-commit",
            actor_id=COORDINATOR,
            accepted_head=self.fx.integration_head,
            expected_patch_sha256=expected,
            closeout_receipt_sha256="b" * 64,
            message=f"{STAGE}: closeout",
        )
        command = CommandPlan(("git", "-C", str(self.fx.root), "commit", "-m", request["message"]), str(self.fx.root), EnvClass.GIT_MUTATION, "closeout")
        bundle = f".local/two-site-refactor/review-bundles/{STAGE}-{ASSIGNMENT}"
        verify = FollowupCommandPlan(
            (
                str(self.fx.pins.closeout_review_bridge), "--verify-commit", "--stage", STAGE,
                "--assignment", ASSIGNMENT, "--bundle", bundle, "--accepted-head", self.fx.integration_head,
                "--closeout-head", RESULTING_HEAD,
            ),
            str(self.fx.root), EnvClass.READ_ONLY, RESULTING_HEAD, "verify",
        )
        plan = self.fx.plan(
            request,
            ActorRole.COORDINATOR,
            (command,),
            followups=(verify,),
            **self.fx.commit_bindings(),
        )
        outcome = execute_plan(plan, request, runner=RecordingRunner(), pinned_paths=self.fx.pins, before_mutation=self.before)
        self.assertEqual("SUCCESS", outcome["status"], outcome)
        self.assertIsNone(outcome["receipt_decision"])
        self.assertEqual(self.fx.integration_head, self.fx.git(self.fx.root, "rev-parse", "HEAD^").stdout.strip())
        self.assertEqual("", self.fx.git(self.fx.root, "status", "--porcelain").stdout)


if __name__ == "__main__":
    unittest.main()
