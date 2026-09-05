from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.two_site_agent_gateway import (  # noqa: E402
    ActorBinding,
    ActorRole,
    CandidateBinding,
    CommitGuard,
    EnvClass,
    GatewayContext,
    GatewayErrorCode,
    GatewayPlanError,
    GitSnapshot,
    ReceiptBinding,
    ReceiptKind,
    ReviewBinding,
    RESULTING_HEAD,
    plan_operation,
)


COORDINATOR = "11111111-1111-4111-8111-111111111111"
WORKER = "22222222-2222-4222-8222-222222222222"
VERIFIER = "33333333-3333-4333-8333-333333333333"
REQUEST_ID = "44444444-4444-4444-8444-444444444444"
ASSIGNMENT = "WA-P1-00B-GATEWAY-001"
STAGE = "P1-00b"
INTEGRATION_HEAD = "a" * 40
ASSIGNMENT_BASE = "b" * 40
CANDIDATE = "c" * 40
CANDIDATE_PARENT = "d" * 40
PATCH = "1" * 64
INTEGRATION_PATCH = "2" * 64
CLOSEOUT_PATCH = "3" * 64


class StrictFenceAPI:
    immutable_fields = (
        "stage_id",
        "branch",
        "worktree_path",
        "worktree_registry_id",
        "allowed_paths",
        "forbidden_paths",
        "locks",
        "parallel_peer",
        "deliverables",
        "tests",
        "failure_tests",
        "rollback_test",
        "external_access",
        "expires_at",
    )
    canonical_fields = {
        "assignment_id",
        "stage_id",
        "role",
        "status",
        "orchestrator",
        "orchestration_epoch",
        "supersedes",
        "base_sha",
        "branch",
        "worktree_path",
        "worktree_registry_id",
        "allowed_paths",
        "forbidden_paths",
        "locks",
        "parallel_peer",
        "deliverables",
        "tests",
        "failure_tests",
        "rollback_test",
        "external_access",
        "expires_at",
        "issued_by",
        "issued_at",
        "integration_branch",
        "integration_head_at_issue",
    }

    def __init__(self) -> None:
        self.fence_calls = 0
        self.assignment_calls = 0

    def immutable_contract_sha256(self, assignment: dict[str, Any]) -> str:
        payload = {key: assignment[key] for key in self.immutable_fields}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def validate_fence(self, payload: Any) -> None:
        self.fence_calls += 1
        if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
            raise ValueError("invalid fence")

    def validate_canonical_assignment(self, assignment: dict[str, Any]) -> None:
        self.assignment_calls += 1
        if set(assignment) != self.canonical_fields:
            raise ValueError("invalid canonical Assignment keys")
        if assignment["external_access"] != "NONE":
            raise ValueError("external Assignment")
        expires = datetime.fromisoformat(assignment["expires_at"].replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            raise ValueError("expired Assignment")


class GatewayFixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="two-site-gateway-test-")
        self.container = Path(self.temp.name)
        self.root = self.container / "integration"
        self.stage = self.container / "worktrees" / "P1-00b"
        self.root.mkdir(parents=True)
        self.stage.mkdir(parents=True)
        self.api = StrictFenceAPI()
        self.assignment = self._assignment()
        self._write_layout()

    def cleanup(self) -> None:
        self.temp.cleanup()

    def _assignment(self) -> dict[str, Any]:
        return {
            "assignment_id": ASSIGNMENT,
            "stage_id": STAGE,
            "role": "WORKER",
            "status": "IN_PROGRESS",
            "orchestrator": "ANTIGRAVITY",
            "orchestration_epoch": 7,
            "supersedes": "WA-P1-00B-OLD-001",
            "base_sha": ASSIGNMENT_BASE,
            "branch": "refactor/stage/P1-00b-gateway",
            "worktree_path": str(self.stage),
            "worktree_registry_id": "stage-P1-00b-gateway",
            "allowed_paths": [
                "tests/test_p1_00b_characterization_*.py",
                "docs/refactor/two-server/section-1/**",
            ],
            "forbidden_paths": ["scripts/**", ".env*"],
            "locks": ["shared-test-fixtures"],
            "parallel_peer": None,
            "deliverables": ["characterization"],
            "tests": ["focused characterization"],
            "failure_tests": ["no network"],
            "rollback_test": "revert candidate",
            "external_access": "NONE",
            "expires_at": "2099-09-10T00:00:00Z",
            "issued_by": "EXECUTION_COORDINATOR",
            "issued_at": "2026-09-04T00:00:00Z",
            "integration_branch": "refactor/two-site-architecture",
            "integration_head_at_issue": ASSIGNMENT_BASE,
        }

    def _write(self, path: Path, content: str, *, executable: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if executable:
            path.chmod(0o755)

    def _write_layout(self) -> None:
        local = self.root / ".local" / "two-site-refactor"
        self._write(
            local / "assignments" / f"{ASSIGNMENT}.md",
            f"# Assignment\n\n```yaml\n{yaml.safe_dump(self.assignment, sort_keys=False).rstrip()}\n```\n",
        )
        fence = {
            "schema_version": "1.0",
            "epoch": 7,
            "status": "ACTIVE",
            "binding_kind": "TAKEOVER",
            "platform": "ANTIGRAVITY",
            "coordinator_instance_id": COORDINATOR,
            "integration_branch": "refactor/two-site-architecture",
            "integration_head_sha": INTEGRATION_HEAD,
            "previous_assignment_id": "WA-P1-00B-OLD-001",
            "active_assignment_id": ASSIGNMENT,
            "assignment_base_sha": ASSIGNMENT_BASE,
            "assignment_integration_head_at_issue": ASSIGNMENT_BASE,
            "immutable_contract_sha256": self.api.immutable_contract_sha256(self.assignment),
            "claimed_at_utc": "2026-09-04T00:00:00Z",
            "supersedes": None,
        }
        self._write(local / "orchestrator-fence.json", json.dumps(fence))
        registry = {
            "schema_version": "1.0",
            "integration": {
                "path": str(self.root),
                "branch": "refactor/two-site-architecture",
                "head_sha": INTEGRATION_HEAD,
                "active_assignment_id": ASSIGNMENT,
                "orchestrator": "ANTIGRAVITY",
                "orchestration_epoch": 7,
            },
            "stage_worktrees": [
                {
                    "registry_id": "stage-P1-00b-gateway",
                    "stage_id": STAGE,
                    "assignment_id": ASSIGNMENT,
                    "owner_role": "EXECUTION_WORKER",
                    "path": str(self.stage),
                    "branch": "refactor/stage/P1-00b-gateway",
                    "base_sha": ASSIGNMENT_BASE,
                    "head_sha": CANDIDATE_PARENT,
                    "dirty_uncommitted_candidate": True,
                    "locks": ["shared-test-fixtures"],
                    "status": "RUNNING",
                    "orchestrator": "ANTIGRAVITY",
                    "orchestration_epoch": 7,
                }
            ],
        }
        self._write(local / "worktrees.yaml", yaml.safe_dump(registry, sort_keys=False))
        order = {
            "schema_version": "1.1",
            "waves": [{"id": "W0", "stages": ["P1-01"]}],
            "stages": {
                STAGE: {"state": "IN_PROGRESS", "section": 1, "depends_on": ["P1-00"], "production": False},
                "P1-00": {"state": "COMPLETE", "section": 1, "depends_on": [], "production": False},
                "P1-01": {"state": "PROPOSED", "section": 1,
                           "depends_on": ["P1-00", STAGE], "production": False},
            },
        }
        self._write(
            self.root / "docs" / "refactor" / "two-server" / "execution-order.yaml",
            yaml.safe_dump(order, sort_keys=False),
        )
        self._write(self.root / "scripts" / "two_site_orchestrator_fence.py", "# fixture canonical fence\n")
        self._write(self.root / "scripts" / "two_site_codex_final_review.sh", "#!/bin/sh\n", executable=True)
        self._write(self.root / "scripts" / "two_site_codex_closeout_review.sh", "#!/bin/sh\n", executable=True)
        self.test_path = self.stage / "tests" / "test_p1_00b_characterization_gateway.py"
        self._write(self.test_path, "def test_gateway():\n    pass\n")
        self.changed_doc = self.stage / "docs" / "refactor" / "two-server" / "section-1" / "evidence.md"
        self._write(self.changed_doc, "evidence\n")
        self.hook = self.root / ".git" / "hooks" / "pre-commit"
        self._write(
            self.hook,
            "#!/bin/sh\nset -eu\n\ngit diff --cached --check\ngit show main:scripts/check_memory_secrets.py | python3 -\n",
            executable=True,
        )
        self.verifier_path = local / "receipts" / "verifier.json"
        self.closeout_path = local / "review-bundles" / f"{STAGE}-{ASSIGNMENT}" / "CODEX_CLOSEOUT_PREACTION.json"
        self.final_path = local / "review-bundles" / f"{STAGE}-{ASSIGNMENT}" / "CODEX_FINAL_REVIEW.json"
        self._write(self.verifier_path, "verified receipt\n")
        self._write(self.closeout_path, "closeout receipt\n")
        self._write(self.final_path, "final receipt\n")
        bundle = local / "review-bundles" / f"{STAGE}-{ASSIGNMENT}"
        for name in ("assignment.md", "STAGE_REPORT.md", "FINAL_REVIEW_REQUEST.md", "HANDOFF.md", "commit.txt"):
            self._write(bundle / name, f"{name}\n")

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def request(self, operation: str, actor: str = WORKER, **extra: Any) -> dict[str, Any]:
        request = {
            "id": REQUEST_ID,
            "operation": operation,
            "actor_id": actor,
            "assignment": ASSIGNMENT,
            "epoch": 7,
            "integration_head": INTEGRATION_HEAD,
            **extra,
        }
        if operation == "worker-commit":
            request.setdefault("test_receipt_sha256", "4" * 64)
        if operation == "stage-advance":
            request.setdefault("worker_receipt_sha256", "6" * 64)
        return request

    def context(
        self,
        *,
        caller: str = WORKER,
        root_snapshot: GitSnapshot | None = None,
        stage_snapshot: GitSnapshot | None = None,
        receipts: tuple[ReceiptBinding, ...] | None = None,
        candidates: tuple[CandidateBinding, ...] = (),
        reviews: tuple[ReviewBinding, ...] | None = None,
        authorization: str | None = "TWO_SITE_REFACTOR_SOURCE_AND_EVIDENCE_TO_OPENAI_CODEX",
        commit_guard: CommitGuard | None | object = ...,  # sentinel: build valid default
    ) -> GatewayContext:
        if root_snapshot is None:
            root_snapshot = GitSnapshot(self.root, "refactor/two-site-architecture", INTEGRATION_HEAD)
        if stage_snapshot is None:
            stage_snapshot = GitSnapshot(
                self.stage,
                "refactor/stage/P1-00b-gateway",
                CANDIDATE_PARENT,
                parent_sha=ASSIGNMENT_BASE,
                unstaged_paths=("docs/refactor/two-server/section-1/evidence.md",),
                worktree_patch_sha256=PATCH,
            )
        if receipts is None:
            receipts = (
                ReceiptBinding(
                    ReceiptKind.VERIFIER,
                    str(self.verifier_path.relative_to(self.root)),
                    STAGE,
                    ASSIGNMENT,
                    "VERIFIED",
                    self.digest(self.verifier_path),
                    CANDIDATE_PARENT,
                    CANDIDATE,
                ),
                ReceiptBinding(
                    ReceiptKind.CODEX_CLOSEOUT_PREACTION,
                    str(self.closeout_path.relative_to(self.root)),
                    STAGE,
                    ASSIGNMENT,
                    "APPROVE",
                    self.digest(self.closeout_path),
                    ASSIGNMENT_BASE,
                    INTEGRATION_HEAD,
                    CLOSEOUT_PATCH,
                ),
                ReceiptBinding(
                    ReceiptKind.CODEX_FINAL,
                    str(self.final_path.relative_to(self.root)),
                    STAGE,
                    ASSIGNMENT,
                    "APPROVE",
                    self.digest(self.final_path),
                    ASSIGNMENT_BASE,
                    INTEGRATION_HEAD,
                ),
            )
        if commit_guard is ...:
            commit_guard = CommitGuard(
                self.hook,
                self.digest(self.hook),
                "f" * 64,
                INTEGRATION_HEAD,
                None,
                True,
            )
        if reviews is None:
            reviews = (
                ReviewBinding(
                    ASSIGNMENT,
                    f".local/two-site-refactor/review-bundles/{STAGE}-{ASSIGNMENT}",
                    ASSIGNMENT_BASE,
                    INTEGRATION_HEAD,
                ),
            )
        return GatewayContext(
            trusted_root=self.root,
            caller_id=caller,
            actors=(
                ActorBinding(COORDINATOR, ActorRole.COORDINATOR, ASSIGNMENT),
                ActorBinding(WORKER, ActorRole.WORKER, ASSIGNMENT),
                ActorBinding(VERIFIER, ActorRole.VERIFIER, ASSIGNMENT),
            ),
            mounted_workspaces=(self.root, self.stage),
            git_snapshots=(root_snapshot, stage_snapshot),
            receipts=receipts,
            candidates=candidates,
            reviews=reviews,
            fence_api=self.api,
            source_review_authorization=authorization,
            commit_guard=commit_guard if isinstance(commit_guard, CommitGuard) else None,
        )

    def set_stage_registry(self, **updates: Any) -> None:
        path = self.root / ".local" / "two-site-refactor" / "worktrees.yaml"
        payload = yaml.safe_load(path.read_text())
        payload["stage_worktrees"][0].update(updates)
        path.write_text(yaml.safe_dump(payload, sort_keys=False))

    def set_stage_flag(self, key: str, value: Any) -> None:
        path = self.root / "docs" / "refactor" / "two-server" / "execution-order.yaml"
        payload = yaml.safe_load(path.read_text())
        payload["stages"][STAGE][key] = value
        path.write_text(yaml.safe_dump(payload, sort_keys=False))


class GatewayPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = GatewayFixture()

    def tearDown(self) -> None:
        self.fx.cleanup()

    def assertBlocked(self, code: GatewayErrorCode, request: dict[str, Any], context: GatewayContext) -> None:
        with self.assertRaises(GatewayPlanError) as raised:
            plan_operation(request, context)
        self.assertEqual(raised.exception.code, code)

    def test_inspect_is_fixed_read_only_and_plan_is_frozen(self) -> None:
        request = self.fx.request("inspect")
        plan = plan_operation(request, self.fx.context())
        self.assertEqual(plan.commands[0].argv, ("git", "-C", str(self.fx.stage), "rev-parse", "--show-toplevel", "HEAD"))
        self.assertEqual(plan.commands[0].env_class, EnvClass.READ_ONLY)
        self.assertGreater(self.fx.api.fence_calls, 0)
        self.assertGreater(self.fx.api.assignment_calls, 0)
        with self.assertRaises(FrozenInstanceError):
            plan.operation = "status"  # type: ignore[misc]

    def test_unknown_operation_and_unknown_metadata_fail_closed(self) -> None:
        self.assertBlocked(GatewayErrorCode.UNKNOWN_OPERATION, self.fx.request("shell"), self.fx.context())
        request = self.fx.request("status")
        request["command"] = "pwd"
        self.assertBlocked(GatewayErrorCode.INVALID_REQUEST, request, self.fx.context())

    def test_actual_caller_cannot_be_spoofed_by_request(self) -> None:
        self.assertBlocked(
            GatewayErrorCode.CALLER_MISMATCH,
            self.fx.request("status", actor=COORDINATOR),
            self.fx.context(caller=WORKER),
        )

    def test_role_is_derived_from_context(self) -> None:
        request = self.fx.request(
            "worker-commit",
            actor=VERIFIER,
            expected_patch_sha256=PATCH,
            message=f"{STAGE}: evidence",
            allowed_paths=self.fx.assignment["allowed_paths"],
        )
        self.assertBlocked(GatewayErrorCode.ROLE_FORBIDDEN, request, self.fx.context(caller=VERIFIER))

    def test_stale_head_and_epoch_fail_before_planning(self) -> None:
        request = self.fx.request("status")
        request["integration_head"] = "e" * 40
        self.assertBlocked(GatewayErrorCode.STATE_MISMATCH, request, self.fx.context())
        request = self.fx.request("status")
        request["epoch"] = 8
        self.assertBlocked(GatewayErrorCode.STATE_MISMATCH, request, self.fx.context())

    def test_test_plan_accepts_only_explicit_assignment_scoped_test_file(self) -> None:
        request = self.fx.request("test", selectors=["tests/test_p1_00b_characterization_gateway.py::test_gateway"])
        plan = plan_operation(request, self.fx.context())
        pytest = [item for item in plan.commands if item.env_class is EnvClass.HERMETIC_TEST][0]
        self.assertIn("--", pytest.argv)
        self.assertEqual(pytest.argv[-1], "tests/test_p1_00b_characterization_gateway.py::test_gateway")

    def test_test_selector_rejects_traversal_flags_shell_and_globs(self) -> None:
        for selector in (
            "../tests/test_escape.py",
            "-c",
            "tests/test_p1_00b_characterization_gateway.py;id",
            "tests/test_*.py",
            "tests/test_p1_00b_characterization_gateway.py::test_gateway[$(id)]",
        ):
            with self.subTest(selector=selector):
                self.assertBlocked(
                    GatewayErrorCode.PATH_REJECTED
                    if selector.startswith("..") or selector.startswith("-") or "*" in selector
                    else GatewayErrorCode.INVALID_REQUEST,
                    self.fx.request("test", selectors=[selector]),
                    self.fx.context(),
                )

    def test_test_selector_rejects_symlink(self) -> None:
        link = self.fx.stage / "tests" / "test_p1_00b_characterization_link.py"
        link.symlink_to(self.fx.test_path)
        self.assertBlocked(
            GatewayErrorCode.SYMLINK_REJECTED,
            self.fx.request("test", selectors=["tests/test_p1_00b_characterization_link.py"]),
            self.fx.context(),
        )

    def test_worker_commit_plan_preserves_reviewed_hook_and_fixed_paths(self) -> None:
        request = self.fx.request(
            "worker-commit",
            expected_patch_sha256=PATCH,
            message=f"{STAGE}: add evidence",
            allowed_paths=self.fx.assignment["allowed_paths"],
        )
        plan = plan_operation(request, self.fx.context())
        add = plan.commands[1].argv
        commit = plan.commands[3].argv
        self.assertEqual(add[-1], "docs/refactor/two-server/section-1/evidence.md")
        self.assertEqual(commit[-2:], ("-m", f"{STAGE}: add evidence"))
        self.assertNotIn("--no-verify", commit)
        self.assertNotIn("--no-gpg-sign", commit)

    def test_worker_commit_rejects_scope_patch_and_allowed_path_drift(self) -> None:
        outside = self.fx.stage / "scripts" / "unsafe.py"
        self.fx._write(outside, "pass\n")
        snapshot = GitSnapshot(
            self.fx.stage,
            "refactor/stage/P1-00b-gateway",
            CANDIDATE_PARENT,
            unstaged_paths=("scripts/unsafe.py",),
            worktree_patch_sha256=PATCH,
        )
        base = self.fx.request(
            "worker-commit",
            expected_patch_sha256=PATCH,
            message=f"{STAGE}: evidence",
            allowed_paths=self.fx.assignment["allowed_paths"],
        )
        self.assertBlocked(GatewayErrorCode.PATH_REJECTED, base, self.fx.context(stage_snapshot=snapshot))
        drifted = dict(base)
        drifted["allowed_paths"] = ["tests/**"]
        self.assertBlocked(GatewayErrorCode.PATH_REJECTED, drifted, self.fx.context())
        bad_patch = dict(base)
        bad_patch["expected_patch_sha256"] = "9" * 64
        self.assertBlocked(GatewayErrorCode.PATCH_MISMATCH, bad_patch, self.fx.context())

    def test_worker_commit_rejects_message_injection_and_prestaged_content(self) -> None:
        for message in ("other: evidence", f"{STAGE}: evidence\nnext"):
            request = self.fx.request(
                "worker-commit",
                expected_patch_sha256=PATCH,
                message=message,
                allowed_paths=self.fx.assignment["allowed_paths"],
            )
            self.assertBlocked(GatewayErrorCode.INVALID_REQUEST, request, self.fx.context())
        snapshot = GitSnapshot(
            self.fx.stage,
            "refactor/stage/P1-00b-gateway",
            CANDIDATE_PARENT,
            staged_paths=("docs/refactor/two-server/section-1/evidence.md",),
            worktree_patch_sha256=PATCH,
            staged_patch_sha256=PATCH,
        )
        request = self.fx.request(
            "worker-commit",
            expected_patch_sha256=PATCH,
            message=f"{STAGE}: evidence",
            allowed_paths=self.fx.assignment["allowed_paths"],
        )
        self.assertBlocked(GatewayErrorCode.UNSUPPORTED_SAFE_WORKFLOW, request, self.fx.context(stage_snapshot=snapshot))

    def test_commit_guard_rejects_tamper_alternate_hooks_and_missing_attestation(self) -> None:
        request = self.fx.request(
            "worker-commit",
            expected_patch_sha256=PATCH,
            message=f"{STAGE}: evidence",
            allowed_paths=self.fx.assignment["allowed_paths"],
        )
        guard = self.fx.context().commit_guard
        assert guard is not None
        self.assertBlocked(
            GatewayErrorCode.COMMIT_GUARD_REJECTED,
            request,
            replace(self.fx.context(), commit_guard=replace(guard, core_hooks_path="/tmp/hooks")),
        )
        copied_hook = self.fx.container / "lookalike-pre-commit"
        self.fx._write(copied_hook, self.fx.hook.read_text(), executable=True)
        self.assertBlocked(
            GatewayErrorCode.COMMIT_GUARD_REJECTED,
            request,
            replace(
                self.fx.context(),
                commit_guard=replace(guard, hook_path=copied_hook, hook_sha256=self.fx.digest(copied_hook)),
            ),
        )
        self.fx.hook.write_text("#!/bin/sh\nexit 0\n")
        self.fx.hook.chmod(0o755)
        self.assertBlocked(GatewayErrorCode.COMMIT_GUARD_REJECTED, request, self.fx.context())
        self.assertBlocked(GatewayErrorCode.COMMIT_GUARD_REJECTED, request, self.fx.context(commit_guard=None))

    def test_stage_advance_is_coordinator_only_and_exact(self) -> None:
        clean = GitSnapshot(self.fx.stage, "refactor/stage/P1-00b-gateway", CANDIDATE, parent_sha=CANDIDATE_PARENT)
        self.fx.set_stage_registry(head_sha=CANDIDATE_PARENT, dirty_uncommitted_candidate=False, status="RUNNING")
        request = self.fx.request("stage-advance", actor=COORDINATOR, stage_head=CANDIDATE)
        plan = plan_operation(request, self.fx.context(caller=COORDINATOR, stage_snapshot=clean))
        self.assertIn("stage-advance", plan.commands[0].argv)
        self.assertEqual(plan.commands[0].argv[-2:], ("--stage-head", CANDIDATE))

    def test_verifier_submission_is_evidence_only_and_candidate_bound(self) -> None:
        self.fx.set_stage_registry(head_sha=CANDIDATE, dirty_uncommitted_candidate=False,
                                   status="COMPLETE_CANDIDATE")
        clean = GitSnapshot(self.fx.stage, "refactor/stage/P1-00b-gateway", CANDIDATE,
                            parent_sha=CANDIDATE_PARENT)
        request = self.fx.request(
            "verifier-submit", actor=VERIFIER, candidate_sha=CANDIDATE,
            test_receipt_sha256="5" * 64,
            test_selectors=["tests/test_p1_00b_characterization_gateway.py::test_gateway"],
            decision="VERIFIED", findings=[],
        )
        plan = plan_operation(request, self.fx.context(caller=VERIFIER, stage_snapshot=clean))
        self.assertEqual((), plan.commands)
        bad = dict(request, decision="VERIFIED", findings=["unresolved"])
        self.assertBlocked(GatewayErrorCode.INVALID_REQUEST, bad,
                           self.fx.context(caller=VERIFIER, stage_snapshot=clean))

    def _integration_context(self) -> GatewayContext:
        self.fx.set_stage_registry(head_sha=CANDIDATE, dirty_uncommitted_candidate=False, status="COMPLETE_CANDIDATE")
        stage_snapshot = GitSnapshot(
            self.fx.stage,
            "refactor/stage/P1-00b-gateway",
            CANDIDATE,
            parent_sha=CANDIDATE_PARENT,
        )
        verifier_digest = self.fx.digest(self.fx.verifier_path)
        candidate = CandidateBinding(
            ASSIGNMENT,
            CANDIDATE,
            CANDIDATE_PARENT,
            ("docs/refactor/two-server/section-1/evidence.md",),
            INTEGRATION_PATCH,
            ("tests/test_p1_00b_characterization_gateway.py::test_gateway",),
            verifier_digest,
        )
        return self.fx.context(
            caller=COORDINATOR,
            stage_snapshot=stage_snapshot,
            candidates=(candidate,),
        )

    def _integration_request(self) -> dict[str, Any]:
        return self.fx.request(
            "integrate",
            actor=COORDINATOR,
            candidate_sha=CANDIDATE,
            expected_patch_sha256=INTEGRATION_PATCH,
            test_selectors=["tests/test_p1_00b_characterization_gateway.py::test_gateway"],
            verifier_receipt_sha256=self.fx.digest(self.fx.verifier_path),
            message=f"{STAGE}: integrate verified evidence",
            allowed_paths=self.fx.assignment["allowed_paths"],
        )

    def test_non_ff_integration_uses_exact_candidate_checkpoint_test_commit_and_advance(self) -> None:
        plan = plan_operation(self._integration_request(), self._integration_context())
        cherry = [command for command in plan.commands if "cherry-pick" in command.argv][0]
        self.assertEqual(cherry.argv[-2:], ("--no-commit", CANDIDATE))
        self.assertTrue(any(command.env_class is EnvClass.HERMETIC_TEST for command in plan.commands))
        commit = [command for command in plan.commands if "commit" in command.argv][0]
        self.assertNotIn("--no-verify", commit.argv)
        self.assertEqual(len(plan.followups), 1)
        self.assertIn(RESULTING_HEAD, plan.followups[0].argv_template)
        self.assertIn("advance", plan.followups[0].argv_template)
        self.assertEqual(tuple(item.after_command_index for item in plan.checkpoints), (1, 3))
        self.assertTrue(all(item.expected_staged_patch_sha256 == INTEGRATION_PATCH for item in plan.checkpoints))

    def test_integration_rejects_unverified_or_drifted_candidate(self) -> None:
        request = self._integration_request()
        request["verifier_receipt_sha256"] = "9" * 64
        self.assertBlocked(GatewayErrorCode.PATCH_MISMATCH, request, self._integration_context())
        request = self._integration_request()
        request["test_selectors"] = ["tests/test_p1_00b_characterization_gateway.py"]
        self.assertBlocked(GatewayErrorCode.STATE_MISMATCH, request, self._integration_context())

    def test_review_request_uses_exact_bundle_base_head_and_external_env_class(self) -> None:
        request = self.fx.request(
            "review-request",
            actor=COORDINATOR,
            bundle=f".local/two-site-refactor/review-bundles/{STAGE}-{ASSIGNMENT}",
            base_sha=ASSIGNMENT_BASE,
            head_sha=INTEGRATION_HEAD,
        )
        plan = plan_operation(request, self.fx.context(caller=COORDINATOR))
        self.assertEqual(plan.commands[0].env_class, EnvClass.EXTERNAL_CODEX_REVIEW)
        self.assertEqual(plan.commands[0].argv[-4:], ("--base", ASSIGNMENT_BASE, "--head", INTEGRATION_HEAD))

    def test_bundle_and_closeout_prepare_are_fixed_no_command_host_operations(self) -> None:
        bundle = self.fx.request(
            "bundle-prepare", actor=COORDINATOR, base_sha=ASSIGNMENT_BASE,
            head_sha=INTEGRATION_HEAD, stage_report="report", handoff="handoff",
            final_review_request=f"review {ASSIGNMENT_BASE}..{INTEGRATION_HEAD}",
        )
        plan = plan_operation(bundle, self.fx.context(caller=COORDINATOR))
        self.assertEqual((), plan.commands)
        closeout = self.fx.request(
            "closeout-prepare", actor=COORDINATOR, accepted_head=INTEGRATION_HEAD,
            final_receipt_sha256=self.fx.digest(self.fx.final_path),
        )
        plan = plan_operation(closeout, self.fx.context(caller=COORDINATOR))
        self.assertEqual((), plan.commands)

    def test_closeout_review_accepts_only_complete_state_and_exact_staged_paths(self) -> None:
        self.fx.set_stage_flag("state", "COMPLETE")
        paths = tuple(sorted((
            "docs/refactor/two-server/CHANGE_LEDGER.md",
            "docs/refactor/two-server/execution-order.yaml",
            f"docs/refactor/two-server/reviews/{STAGE}-{ASSIGNMENT}.json",
            f"docs/refactor/two-server/reviews/{STAGE}-{ASSIGNMENT}.md",
        )))
        snapshot = GitSnapshot(self.fx.root, "refactor/two-site-architecture",
                               INTEGRATION_HEAD, staged_paths=paths,
                               staged_patch_sha256=CLOSEOUT_PATCH)
        request = self.fx.request(
            "closeout-review", actor=COORDINATOR, accepted_head=INTEGRATION_HEAD,
            final_receipt_sha256=self.fx.digest(self.fx.final_path),
        )
        plan = plan_operation(request, self.fx.context(caller=COORDINATOR,
                                                       root_snapshot=snapshot))
        self.assertEqual(EnvClass.EXTERNAL_CODEX_REVIEW, plan.commands[0].env_class)
        self.assertEqual(
            f".local/two-site-refactor/review-bundles/{STAGE}-{ASSIGNMENT}",
            dict(plan.bindings)["bundle"],
        )

    def test_review_rejects_missing_authorization_wrong_bundle_and_stale_head(self) -> None:
        base = self.fx.request(
            "review-request",
            actor=COORDINATOR,
            bundle=f".local/two-site-refactor/review-bundles/{STAGE}-{ASSIGNMENT}",
            base_sha=ASSIGNMENT_BASE,
            head_sha=INTEGRATION_HEAD,
        )
        self.assertBlocked(
            GatewayErrorCode.EXTERNAL_ACTION_REJECTED,
            base,
            self.fx.context(caller=COORDINATOR, authorization=None),
        )
        wrong = dict(base)
        wrong["bundle"] = "../bundle"
        self.assertBlocked(GatewayErrorCode.PATH_REJECTED, wrong, self.fx.context(caller=COORDINATOR))
        stale = dict(base)
        stale["head_sha"] = "e" * 40
        self.assertBlocked(GatewayErrorCode.STATE_MISMATCH, stale, self.fx.context(caller=COORDINATOR))
        self.assertBlocked(
            GatewayErrorCode.STATE_MISMATCH,
            base,
            self.fx.context(caller=COORDINATOR, reviews=()),
        )

    def _closeout_context(self) -> GatewayContext:
        self.fx.set_stage_flag("state", "COMPLETE")
        prefix = f"docs/refactor/two-server/reviews/{STAGE}-{ASSIGNMENT}"
        staged = tuple(
            sorted(
                (
                    "docs/refactor/two-server/CHANGE_LEDGER.md",
                    "docs/refactor/two-server/execution-order.yaml",
                    f"{prefix}.json",
                    f"{prefix}.md",
                )
            )
        )
        root = GitSnapshot(
            self.fx.root,
            "refactor/two-site-architecture",
            INTEGRATION_HEAD,
            staged_paths=staged,
            staged_patch_sha256=CLOSEOUT_PATCH,
            worktree_patch_sha256=CLOSEOUT_PATCH,
        )
        return self.fx.context(caller=COORDINATOR, root_snapshot=root)

    def test_closeout_requires_exact_patch_receipt_paths_and_postcommit_verification(self) -> None:
        request = self.fx.request(
            "closeout-commit",
            actor=COORDINATOR,
            accepted_head=INTEGRATION_HEAD,
            expected_patch_sha256=CLOSEOUT_PATCH,
            closeout_receipt_sha256=self.fx.digest(self.fx.closeout_path),
            message=f"{STAGE}: closeout",
        )
        plan = plan_operation(request, self._closeout_context())
        self.assertEqual(plan.commands[0].argv[-2:], ("-m", f"{STAGE}: closeout"))
        self.assertNotIn("--no-verify", plan.commands[0].argv)
        self.assertEqual(len(plan.followups), 1)
        self.assertIn("--verify-commit", plan.followups[0].argv_template)
        self.assertIn(RESULTING_HEAD, plan.followups[0].argv_template)

    def test_closeout_rejects_patch_receipt_and_path_mismatch(self) -> None:
        request = self.fx.request(
            "closeout-commit",
            actor=COORDINATOR,
            accepted_head=INTEGRATION_HEAD,
            expected_patch_sha256=CLOSEOUT_PATCH,
            closeout_receipt_sha256="9" * 64,
            message=f"{STAGE}: closeout",
        )
        self.assertBlocked(GatewayErrorCode.RECEIPT_MISMATCH, request, self._closeout_context())
        context = self._closeout_context()
        bad_root = replace(context.git_snapshots[0], staged_paths=("docs/refactor/two-server/CHANGE_LEDGER.md",))
        context = replace(context, git_snapshots=(bad_root, context.git_snapshots[1]))
        request["closeout_receipt_sha256"] = self.fx.digest(self.fx.closeout_path)
        self.assertBlocked(GatewayErrorCode.PATH_REJECTED, request, context)

    def test_closeout_advance_requires_persisted_verified_commit_receipt(self) -> None:
        self.fx.set_stage_flag("state", "COMPLETE")
        closeout_head = "7" * 40
        receipt_path = self.fx.root / ".local/two-site-refactor/gateway/results/closeout.json"
        self.fx._write(receipt_path, "verified closeout\n")
        authority = "8" * 64
        receipt = ReceiptBinding(
            ReceiptKind.CLOSEOUT_COMMIT,
            receipt_path.relative_to(self.fx.root).as_posix(), STAGE, ASSIGNMENT,
            "VERIFIED", authority, INTEGRATION_HEAD, closeout_head, CLOSEOUT_PATCH,
            self.fx.digest(receipt_path),
        )
        root = GitSnapshot(self.fx.root, "refactor/two-site-architecture", closeout_head,
                           parent_sha=INTEGRATION_HEAD)
        request = self.fx.request(
            "closeout-advance", actor=COORDINATOR, closeout_head=closeout_head,
            closeout_commit_receipt_sha256=authority,
        )
        plan = plan_operation(request, self.fx.context(caller=COORDINATOR,
                                                       root_snapshot=root, receipts=(receipt,)))
        self.assertIn("advance", plan.commands[0].argv)
        missing = self.fx.context(caller=COORDINATOR, root_snapshot=root, receipts=())
        self.assertBlocked(GatewayErrorCode.RECEIPT_MISMATCH, request, missing)

    def test_next_stage_chain_requires_exact_receipts_and_protected_contract(self) -> None:
        self.fx.set_stage_flag("state", "COMPLETE")
        closeout_file = self.fx.root / ".local/two-site-refactor/gateway/results/next-closeout.json"
        self.fx._write(closeout_file, "closeout authority\n")
        closeout_authority = "7" * 64
        closeout = ReceiptBinding(
            ReceiptKind.CLOSEOUT_COMMIT, str(closeout_file.relative_to(self.fx.root)),
            STAGE, ASSIGNMENT, "VERIFIED", closeout_authority,
            ASSIGNMENT_BASE, INTEGRATION_HEAD, CLOSEOUT_PATCH, self.fx.digest(closeout_file),
        )
        prepare_request = self.fx.request(
            "next-stage-prepare", actor=COORDINATOR, next_stage="P1-01",
            closeout_commit_receipt_sha256=closeout_authority,
        )
        plan = plan_operation(prepare_request, self.fx.context(
            caller=COORDINATOR, receipts=(closeout,),
            root_snapshot=GitSnapshot(self.fx.root, "refactor/two-site-architecture", INTEGRATION_HEAD),
        ))
        self.assertEqual(plan.commands[0].argv[-2:], ("--next-stage", "P1-01"))

        order = self.fx.root / "docs/refactor/two-server/execution-order.yaml"
        payload = yaml.safe_load(order.read_text())
        payload["stages"]["P1-01"]["state"] = "IN_PROGRESS"
        order.write_text(yaml.safe_dump(payload, sort_keys=False))
        prepare_file = self.fx.root / ".local/two-site-refactor/gateway/results/next-prepare.json"
        self.fx._write(prepare_file, "prepare authority\n")
        prepare_authority, patch = "8" * 64, "9" * 64
        prepared = ReceiptBinding(
            ReceiptKind.NEXT_STAGE_PREPARE, str(prepare_file.relative_to(self.fx.root)),
            STAGE, ASSIGNMENT, "PREPARED", prepare_authority,
            INTEGRATION_HEAD, INTEGRATION_HEAD, patch, self.fx.digest(prepare_file),
        )
        staged = GitSnapshot(
            self.fx.root, "refactor/two-site-architecture", INTEGRATION_HEAD,
            staged_paths=("docs/refactor/two-server/execution-order.yaml",),
            staged_patch_sha256=patch,
        )
        commit_request = self.fx.request(
            "next-stage-commit", actor=COORDINATOR, next_stage="P1-01",
            expected_patch_sha256=patch, prepare_receipt_sha256=prepare_authority,
            message="P1-01: open bounded cleanup Stage",
        )
        commit_plan = plan_operation(commit_request, self.fx.context(
            caller=COORDINATOR, receipts=(prepared,), root_snapshot=staged,
        ))
        self.assertEqual("commit", commit_plan.commands[0].argv[3])
        self.assertEqual("advance", commit_plan.followups[0].argv_template[2])

        commit_file = self.fx.root / ".local/two-site-refactor/gateway/results/next-commit.json"
        self.fx._write(commit_file, "next commit authority\n")
        next_authority = "a" * 64
        committed = ReceiptBinding(
            ReceiptKind.NEXT_STAGE_COMMIT, str(commit_file.relative_to(self.fx.root)),
            STAGE, ASSIGNMENT, "VERIFIED", next_authority,
            INTEGRATION_HEAD, INTEGRATION_HEAD, patch, self.fx.digest(commit_file),
        )
        contract = {
            "slug": "repo-cleanup", "allowed_paths": ["docs/refactor/two-server/section-1/**"],
            "forbidden_paths": sorted({
                ".agents/**", "docs/refactor/two-server/CHANGE_LEDGER.md",
                "docs/refactor/two-server/execution-order.yaml",
                "docs/refactor/two-server/reviews/**", "scripts/two_site_agent_*.py",
            }),
            "locks": ["shared-test-fixtures"], "deliverables": ["inventory"],
            "tests": ["dry run"], "failure_tests": ["path escape"],
            "rollback_test": "revert source commit", "scope_notes": "Inventory and tooling only.",
        }
        bind_request = self.fx.request(
            "next-stage-bind", actor=COORDINATOR, next_stage="P1-01",
            next_assignment="WA-P1-01-REPO-CLEANUP-20260905-001",
            next_stage_commit_receipt_sha256=next_authority, contract=contract,
        )
        bind_plan = plan_operation(bind_request, self.fx.context(
            caller=COORDINATOR, receipts=(committed,),
            root_snapshot=GitSnapshot(self.fx.root, "refactor/two-site-architecture", INTEGRATION_HEAD),
        ))
        bind_argv = bind_plan.commands[0].argv
        self.assertEqual(
            bind_argv[bind_argv.index("--next-assignment") + 1],
            "WA-P1-01-REPO-CLEANUP-20260905-001",
        )
        self.assertEqual(bind_argv[bind_argv.index("--request-id") + 1], bind_request["id"])
        self.assertEqual(
            bind_argv[bind_argv.index("--request-sha256") + 1],
            hashlib.sha256(json.dumps(
                bind_request, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode()).hexdigest(),
        )
        bind_request["contract"] = {**contract, "forbidden_paths": ["scripts/**"]}
        self.assertBlocked(GatewayErrorCode.PATH_REJECTED, bind_request, self.fx.context(
            caller=COORDINATOR, receipts=(committed,),
            root_snapshot=GitSnapshot(self.fx.root, "refactor/two-site-architecture", INTEGRATION_HEAD),
        ))

    def test_receipt_check_only_matches_controller_verified_binding(self) -> None:
        digest = self.fx.digest(self.fx.final_path)
        request = self.fx.request(
            "receipt-check",
            actor=COORDINATOR,
            receipt_kind="CODEX_FINAL",
            receipt_sha256=digest,
            reviewed_sha=INTEGRATION_HEAD,
        )
        plan = plan_operation(request, self.fx.context(caller=COORDINATOR))
        self.assertEqual(plan.commands, ())
        self.assertIn("NO_EXECUTION", plan.postconditions[0])
        request["reviewed_sha"] = "e" * 40
        self.assertBlocked(GatewayErrorCode.RECEIPT_MISMATCH, request, self.fx.context(caller=COORDINATOR))

    def test_external_or_unknown_stage_metadata_blocks_every_operation(self) -> None:
        self.fx.set_stage_flag("production", True)
        self.assertBlocked(GatewayErrorCode.EXTERNAL_ACTION_REJECTED, self.fx.request("status"), self.fx.context())
        self.fx.set_stage_flag("production", False)
        self.fx.set_stage_flag("unexpected", False)
        self.assertBlocked(GatewayErrorCode.EXTERNAL_ACTION_REJECTED, self.fx.request("status"), self.fx.context())

    def test_registered_worktree_symlink_and_unmounted_path_are_rejected(self) -> None:
        real = self.fx.container / "real-stage"
        real.mkdir()
        linked = self.fx.container / "linked-stage"
        linked.symlink_to(real, target_is_directory=True)
        assignment_path = self.fx.root / ".local" / "two-site-refactor" / "assignments" / f"{ASSIGNMENT}.md"
        assignment = dict(self.fx.assignment)
        assignment["worktree_path"] = str(linked)
        assignment_path.write_text(f"# Assignment\n\n```yaml\n{yaml.safe_dump(assignment, sort_keys=False).rstrip()}\n```\n")
        fence_path = self.fx.root / ".local" / "two-site-refactor" / "orchestrator-fence.json"
        fence = json.loads(fence_path.read_text())
        fence["immutable_contract_sha256"] = self.fx.api.immutable_contract_sha256(assignment)
        fence_path.write_text(json.dumps(fence))
        self.fx.set_stage_registry(path=str(linked))
        context = replace(self.fx.context(), mounted_workspaces=(self.fx.root, linked))
        self.assertBlocked(GatewayErrorCode.SYMLINK_REJECTED, self.fx.request("status"), context)


if __name__ == "__main__":
    unittest.main()
