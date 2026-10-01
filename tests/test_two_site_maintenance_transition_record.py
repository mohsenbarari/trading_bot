"""Hermetic in-memory tests for AE03 reviewed maintenance-transition record codec."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from scripts.two_site_maintenance_transition_record import (
    ALLOWED_MAINTENANCE_CONTROL_PATHS,
    ASSIGNMENT_SCOPE_KEYS_ORDER,
    MAX_MAINTENANCE_COMMITS,
    MAX_MAINTENANCE_TRANSITION_RECORD_BYTES,
    MAINTENANCE_TRANSITION_KIND,
    MAINTENANCE_TRANSITION_SCHEMA_VERSION,
    MIN_MAINTENANCE_COMMITS,
    PROTECTED_REFS,
    REPOSITORY_FACT_KEYS,
    MaintenanceTransitionRecordError,
    build_maintenance_transition_record,
    parse_maintenance_transition_record,
    serialize_maintenance_transition_record,
    validate_maintenance_transition_record,
)


def _make_valid_repo(head: str = "a" * 40) -> dict[str, Any]:
    return {
        "head": head,
        "status": "",
        "protected_refs": {
            ref: f"{(idx % 9) + 1}" * 40 for idx, ref in enumerate(sorted(PROTECTED_REFS))
        },
        "control_blobs": {
            "scripts/two_site_autopilot_control.py": "4" * 40,
        },
        "assignment_scope": {
            "assignment_id": "assign-101",
            "stage_id": "stage-p1",
            "sha256": "5" * 64,
            "expires_at": "2026-10-01T12:00:00Z",
            "allowed_paths": ["scripts/two_site_worker.py"],
            "forbidden_paths": [],
        },
    }


def _make_commit(
    head: str,
    parent: str,
    paths: list[str] | None = None,
    worker_run_id: str = "worker-run-1",
    reviewer_run_id: str = "reviewer-run-1",
) -> dict[str, Any]:
    return {
        "head": head,
        "parent": parent,
        "paths": paths or ["scripts/two_site_worker.py"],
        "worker_run_id": worker_run_id,
        "reviewer_run_id": reviewer_run_id,
        "proposal_sha256": "7" * 64,
        "test_evidence_sha256": "8" * 64,
    }


def _make_valid_record(
    commits_count: int = 1,
    prior_head: str = "0" * 40,
) -> tuple[dict[str, Any], str, str]:
    heads = [f"{i:040x}" for i in range(1, commits_count + 1)]
    repo_head = heads[-1]
    repo = _make_valid_repo(head=repo_head)
    commits: list[dict[str, Any]] = []
    for idx, head in enumerate(heads):
        parent = prior_head if idx == 0 else heads[idx - 1]
        commits.append(
            _make_commit(
                head=head,
                parent=parent,
                worker_run_id=f"worker-{idx}",
                reviewer_run_id=f"reviewer-{idx}",
            )
        )
    record: dict[str, Any] = {
        "schema_version": MAINTENANCE_TRANSITION_SCHEMA_VERSION,
        "kind": MAINTENANCE_TRANSITION_KIND,
        "prior_checkpoint_sha256": "9" * 64,
        "prior_head": prior_head,
        "new_repository": repo,
        "maintenance_commits": commits,
    }
    return record, prior_head, repo_head


class TestMaintenanceTransitionRecord(unittest.TestCase):
    """Pure in-memory unit tests for reviewed maintenance transition record codec."""

    def test_single_commit_roundtrip(self) -> None:
        record, _, _ = _make_valid_record(commits_count=1)
        validated = validate_maintenance_transition_record(record)
        self.assertEqual(validated["kind"], MAINTENANCE_TRANSITION_KIND)
        self.assertEqual(len(validated["maintenance_commits"]), 1)

        raw = serialize_maintenance_transition_record(record)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertLessEqual(len(raw), MAX_MAINTENANCE_TRANSITION_RECORD_BYTES)

        parsed = parse_maintenance_transition_record(raw)
        self.assertEqual(parsed, validated)

    def test_eight_commits_roundtrip(self) -> None:
        record, _, _ = _make_valid_record(commits_count=8)
        raw = serialize_maintenance_transition_record(record)
        parsed = parse_maintenance_transition_record(raw)
        self.assertEqual(len(parsed["maintenance_commits"]), 8)
        self.assertEqual(parsed["schema_version"], "1.0")

    def test_twelve_commits_roundtrip(self) -> None:
        record, _, _ = _make_valid_record(commits_count=12)
        raw = serialize_maintenance_transition_record(record)
        parsed = parse_maintenance_transition_record(raw)
        self.assertEqual(len(parsed["maintenance_commits"]), 12)
        self.assertEqual(parsed["schema_version"], "1.0")

    def test_thirteen_commits_negative(self) -> None:
        record, _, _ = _make_valid_record(commits_count=13)
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "maintenance_commits count out of bounds")

    def test_build_record_helper(self) -> None:
        repo = _make_valid_repo(head="b" * 40)
        commits = [_make_commit(head="b" * 40, parent="0" * 40)]
        rec = build_maintenance_transition_record(
            prior_checkpoint_sha256="a" * 64,
            prior_head="0" * 40,
            new_repository=repo,
            maintenance_commits=commits,
        )
        self.assertEqual(rec["new_repository"]["head"], "b" * 40)

    def test_protected_refs_validation(self) -> None:
        record, _, _ = _make_valid_record(commits_count=1)

        # Missing a protected ref
        bad_refs = {ref: "1" * 40 for ref in sorted(PROTECTED_REFS)}
        first_ref = next(iter(sorted(PROTECTED_REFS)))
        del bad_refs[first_ref]
        record["new_repository"]["protected_refs"] = bad_refs
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "protected_refs keys mismatch")

        # Extra protected ref
        bad_refs = {ref: "1" * 40 for ref in sorted(PROTECTED_REFS)}
        bad_refs["refs/heads/unknown_extra_branch"] = "1" * 40
        record["new_repository"]["protected_refs"] = bad_refs
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "protected_refs keys mismatch")

        # Invalid ref sha
        bad_refs = {ref: "1" * 40 for ref in sorted(PROTECTED_REFS)}
        bad_refs[first_ref] = "invalid_sha"
        record["new_repository"]["protected_refs"] = bad_refs
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(
            str(ctx.exception),
            "protected ref identity must be 40 lowercase hex characters",
        )

    def test_commit_count_bounds(self) -> None:
        record, _, _ = _make_valid_record(commits_count=1)
        record["maintenance_commits"] = []
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "maintenance_commits count out of bounds")

        # 13 commits
        heads = [f"{i:040x}" for i in range(1, 14)]
        repo = _make_valid_repo(head=heads[-1])
        commits = [
            _make_commit(
                head=heads[i],
                parent=("0" * 40 if i == 0 else heads[i - 1]),
                worker_run_id=f"w-{i}",
                reviewer_run_id=f"r-{i}",
            )
            for i in range(13)
        ]
        record["new_repository"] = repo
        record["maintenance_commits"] = commits
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "maintenance_commits count out of bounds")

    def test_commit_chain_validation(self) -> None:
        # First commit parent mismatch
        record, _, _ = _make_valid_record(commits_count=2)
        record["maintenance_commits"][0]["parent"] = "f" * 40
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(
            str(ctx.exception), "first maintenance commit parent must match prior_head"
        )

        # Intermediate commit parent mismatch
        record, _, _ = _make_valid_record(commits_count=2)
        record["maintenance_commits"][1]["parent"] = "f" * 40
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(
            str(ctx.exception), "commit parent does not match previous commit head"
        )

        # Final commit head mismatch with new_repository head
        record, _, _ = _make_valid_record(commits_count=1)
        record["new_repository"]["head"] = "e" * 40
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(
            str(ctx.exception), "final commit head must match new_repository head"
        )

        # Head equals parent
        record, _, _ = _make_valid_record(commits_count=2)
        record["maintenance_commits"][1]["head"] = "f" * 40
        record["maintenance_commits"][1]["parent"] = "f" * 40
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "commit head cannot equal parent")

        # Head equals prior_head
        record, prior_head, _ = _make_valid_record(commits_count=2)
        record["maintenance_commits"][1]["head"] = prior_head
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "commit head cannot equal prior_head")

        # Duplicate commit head in 2-commit chain
        record, _, _ = _make_valid_record(commits_count=2)
        dup_head = record["maintenance_commits"][0]["head"]
        record["maintenance_commits"][1]["head"] = dup_head
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "commit head is not distinct")

        # Duplicate commit head in 3-commit chain (head != parent)
        record3, _, _ = _make_valid_record(commits_count=3)
        dup_head3 = record3["maintenance_commits"][0]["head"]
        record3["maintenance_commits"][2]["head"] = dup_head3
        record3["new_repository"]["head"] = dup_head3
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record3)
        self.assertEqual(str(ctx.exception), "commit head is not distinct")

    def test_commit_paths_validation(self) -> None:
        record, _, _ = _make_valid_record(commits_count=1)

        # Allowed patterns
        record["maintenance_commits"][0]["paths"] = [
            "scripts/two_site_core.py",
            "tests/test_two_site_core.py",
        ]
        self.assertTrue(validate_maintenance_transition_record(record))

        # Disallowed path pattern
        record["maintenance_commits"][0]["paths"] = ["src/other.py"]
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "commit path does not match allowed pattern")

        # Commit path in control blobs
        record["maintenance_commits"][0]["paths"] = [
            "scripts/two_site_autopilot_control.py"
        ]
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "commit path cannot be a control path")

        # Unsorted paths
        record["maintenance_commits"][0]["paths"] = [
            "tests/test_two_site_b.py",
            "scripts/two_site_a.py",
        ]
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "commit paths must be sorted")

        # Duplicate paths
        record["maintenance_commits"][0]["paths"] = [
            "scripts/two_site_a.py",
            "scripts/two_site_a.py",
        ]
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "duplicate commit path detected")

        # Path traversal and syntax errors
        for bad_path in [
            "../scripts/two_site_a.py",
            "scripts/two_site_a.py/..",
            "/scripts/two_site_a.py",
            "scripts\\two_site_a.py",
            "scripts/two_site_*.py",
            "C:/scripts/two_site_a.py",
        ]:
            record["maintenance_commits"][0]["paths"] = [bad_path]
            with self.assertRaises(MaintenanceTransitionRecordError):
                validate_maintenance_transition_record(record)

    def test_codex_finding_1_no_fabricated_fallback_constants(self) -> None:
        # Ensure required constants and symbols are imported directly
        import scripts.two_site_maintenance_transition_record as mod

        self.assertTrue(hasattr(mod, "ASSIGNMENT_SCOPE_KEYS_ORDER"))
        self.assertTrue(hasattr(mod, "PROTECTED_REFS"))
        self.assertTrue(hasattr(mod, "REPOSITORY_FACT_KEYS"))
        self.assertTrue(callable(mod.unique_json))
        self.assertEqual(
            mod.ALLOWED_MAINTENANCE_CONTROL_PATHS,
            frozenset({
                "scripts/two_site_autopilot_tmux.sh",
                "scripts/two_site_codex_autopilot.py",
                "scripts/two_site_cycle_tools.py",
            }),
        )

    def test_codex_finding_2_before_traversal_caps_and_byte_limit(self) -> None:
        record, _, _ = _make_valid_record(commits_count=1)

        # Commit paths cap > 256
        oversized_paths = [f"scripts/two_site_{i}.py" for i in range(257)]
        record["maintenance_commits"][0]["paths"] = oversized_paths
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "commit paths count out of bounds")

        # Control blobs cap > 256
        record, _, _ = _make_valid_record(commits_count=1)
        oversized_blobs = {f"scripts/ctrl_{i}.py": "1" * 40 for i in range(257)}
        record["new_repository"]["control_blobs"] = oversized_blobs
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(str(ctx.exception), "control_blobs count out of bounds")

        # Assignment scope allowed paths cap > 256
        record, _, _ = _make_valid_record(commits_count=1)
        record["new_repository"]["assignment_scope"]["allowed_paths"] = [
            "p" for _ in range(257)
        ]
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(
            str(ctx.exception), "assignment_scope allowed_paths count out of bounds"
        )

    def test_codex_finding_3_fixed_error_messages_no_echo(self) -> None:
        record, _, _ = _make_valid_record(commits_count=1)
        sentinel_path = "scripts/two_site_UNTRUSTED_SENTINEL_XYZ.py"
        record["maintenance_commits"][0]["paths"] = [
            sentinel_path,
            sentinel_path,
        ]
        try:
            validate_maintenance_transition_record(record)
            self.fail("Did not raise")
        except MaintenanceTransitionRecordError as exc:
            msg = str(exc)
            self.assertNotIn("UNTRUSTED", msg)
            self.assertNotIn(sentinel_path, msg)
            self.assertEqual(msg, "duplicate commit path detected")

        # Untrusted run ID not echoed
        record, _, _ = _make_valid_record(commits_count=1)
        untrusted_run_id = "BAD_ID_$$$_UNTRUSTED"
        record["maintenance_commits"][0]["worker_run_id"] = untrusted_run_id
        try:
            validate_maintenance_transition_record(record)
            self.fail("Did not raise")
        except MaintenanceTransitionRecordError as exc:
            msg = str(exc)
            self.assertNotIn("UNTRUSTED", msg)
            self.assertNotIn(untrusted_run_id, msg)
            self.assertEqual(msg, "worker_run_id format invalid")

    def test_codex_finding_4_strict_run_id_and_scope_sha256(self) -> None:
        record, _, _ = _make_valid_record(commits_count=1)

        # Valid run IDs
        for valid_id in ["W1", "w", "Worker_01.prod-run", "0run"]:
            record["maintenance_commits"][0]["worker_run_id"] = valid_id
            self.assertTrue(validate_maintenance_transition_record(record))

        # Invalid run IDs
        for invalid_id in [
            "-bad",
            ".bad",
            "_bad",
            "bad id",
            "bad@id",
            "",
            "a" * 129,
        ]:
            record["maintenance_commits"][0]["worker_run_id"] = invalid_id
            with self.assertRaises(MaintenanceTransitionRecordError):
                validate_maintenance_transition_record(record)

        # Worker and reviewer distinct
        record["maintenance_commits"][0]["worker_run_id"] = "same-run-id"
        record["maintenance_commits"][0]["reviewer_run_id"] = "same-run-id"
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record)
        self.assertEqual(
            str(ctx.exception),
            "commit worker_run_id and reviewer_run_id must be distinct",
        )

        # Assignment scope sha256 must be exact 64-hex lowercase
        record, _, _ = _make_valid_record(commits_count=1)
        for bad_sha in ["A" * 64, "5" * 63, "5" * 65, "5" * 40, "not_hex" * 8]:
            record["new_repository"]["assignment_scope"]["sha256"] = bad_sha
            with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
                validate_maintenance_transition_record(record)
            self.assertEqual(
                str(ctx.exception),
                "assignment_scope sha256 must be 64 lowercase hex characters",
            )

    def test_parse_rejections(self) -> None:
        record, _, _ = _make_valid_record(commits_count=1)
        canonical_bytes = serialize_maintenance_transition_record(record)

        # Non-canonical whitespace / formatting
        non_canonical = canonical_bytes.replace(b":", b": ")
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            parse_maintenance_transition_record(non_canonical)
        self.assertEqual(
            str(ctx.exception),
            "raw bytes do not match canonical JSON serialization",
        )

        # Missing trailing newline
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            parse_maintenance_transition_record(canonical_bytes.rstrip(b"\n"))
        self.assertEqual(
            str(ctx.exception),
            "raw bytes do not match canonical JSON serialization",
        )

        # Duplicate keys
        dup_json = b'{"schema_version":"1.0","kind":"REVIEWED_MAINTENANCE_TRANSITION","kind":"DUPLICATE"}\n'
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            parse_maintenance_transition_record(dup_json)
        self.assertEqual(
            str(ctx.exception),
            "Failed to parse JSON or duplicate key detected",
        )

        # Not bytes
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            parse_maintenance_transition_record("not_bytes")  # type: ignore[arg-type]
        self.assertEqual(str(ctx.exception), "raw must be bytes")

        # Empty bytes
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            parse_maintenance_transition_record(b"")
        self.assertEqual(str(ctx.exception), "raw bytes cannot be empty")

        # Exceeds max bytes
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            parse_maintenance_transition_record(
                b" " * (MAX_MAINTENANCE_TRANSITION_RECORD_BYTES + 1)
            )
        self.assertEqual(
            str(ctx.exception), "raw bytes exceed maximum allowed length"
        )

        # Invalid UTF-8
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            parse_maintenance_transition_record(b"\xff\xfe\x00")
        self.assertEqual(str(ctx.exception), "raw bytes are not valid UTF-8")

        # Non-dict JSON root
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            parse_maintenance_transition_record(b"[1, 2, 3]\n")
        self.assertEqual(str(ctx.exception), "Parsed JSON root must be an object")

    def test_codex_autopilot_control_path_allowlist_and_rejections(self) -> None:
        autopilot_path = "scripts/two_site_codex_autopilot.py"
        record, prior_head, repo_head = _make_valid_record(commits_count=1)
        record["new_repository"]["control_blobs"][autopilot_path] = "c" * 40
        record["maintenance_commits"][0]["paths"] = [autopilot_path]

        validated = validate_maintenance_transition_record(record)
        self.assertEqual(validated["kind"], MAINTENANCE_TRANSITION_KIND)
        self.assertIn(autopilot_path, validated["new_repository"]["control_blobs"])
        self.assertEqual(validated["maintenance_commits"][0]["paths"], [autopilot_path])

        raw = serialize_maintenance_transition_record(record)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertLessEqual(len(raw), MAX_MAINTENANCE_TRANSITION_RECORD_BYTES)

        parsed = parse_maintenance_transition_record(raw)
        self.assertEqual(parsed, validated)

        built = build_maintenance_transition_record(
            prior_checkpoint_sha256=record["prior_checkpoint_sha256"],
            prior_head=prior_head,
            new_repository=record["new_repository"],
            maintenance_commits=record["maintenance_commits"],
        )
        self.assertEqual(built, validated)

        # Different control path in control_blobs remains rejected
        record_diff, _, _ = _make_valid_record(commits_count=1)
        record_diff["new_repository"]["control_blobs"][autopilot_path] = "c" * 40
        record_diff["maintenance_commits"][0]["paths"] = [
            "scripts/two_site_autopilot_control.py"
        ]
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record_diff)
        self.assertEqual(str(ctx.exception), "commit path cannot be a control path")

        other_ctrl = "scripts/two_site_other_control.py"
        record_diff["new_repository"]["control_blobs"][other_ctrl] = "d" * 40
        record_diff["maintenance_commits"][0]["paths"] = [other_ctrl]
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record_diff)
        self.assertEqual(str(ctx.exception), "commit path cannot be a control path")

        # Spoof path matching grammar but in control_blobs rejected as control path
        spoof_ctrl = "scripts/two_site_codex_autopilot_spoof.py"
        record_spoof, _, _ = _make_valid_record(commits_count=1)
        record_spoof["new_repository"]["control_blobs"][spoof_ctrl] = "e" * 40
        record_spoof["maintenance_commits"][0]["paths"] = [spoof_ctrl]
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record_spoof)
        self.assertEqual(str(ctx.exception), "commit path cannot be a control path")

        # Spoof path with valid grammar and NOT in control_blobs accepted as normal path
        normal_spoof = "scripts/two_site_codex_autopilot_sub.py"
        record_normal, _, _ = _make_valid_record(commits_count=1)
        record_normal["maintenance_commits"][0]["paths"] = [normal_spoof]
        validated_normal = validate_maintenance_transition_record(record_normal)
        self.assertEqual(
            validated_normal["maintenance_commits"][0]["paths"], [normal_spoof]
        )

        # Suffix/prefix spoof path with invalid grammar is rejected by path validator
        for bad_spoof in [
            "scripts/two_site_codex_autopilot.py.bak",
            "prefix_scripts/two_site_codex_autopilot.py",
            "scripts/two_site_codex_autopilot.py/extra",
            "scripts/two_site_codex_autopilot.py2",
        ]:
            rec_bad, _, _ = _make_valid_record(commits_count=1)
            rec_bad["maintenance_commits"][0]["paths"] = [bad_spoof]
            with self.assertRaises(MaintenanceTransitionRecordError):
                validate_maintenance_transition_record(rec_bad)

        # Malformed path still rejected
        for malformed in [
            "/scripts/two_site_codex_autopilot.py",
            "../scripts/two_site_codex_autopilot.py",
            "scripts/../scripts/two_site_codex_autopilot.py",
            "scripts\\two_site_codex_autopilot.py",
            "scripts/two_site_codex_autopilot.py\x00",
            "scripts/two_site_codex_autopilot.py ",
        ]:
            rec_mal, _, _ = _make_valid_record(commits_count=1)
            rec_mal["maintenance_commits"][0]["paths"] = [malformed]
            with self.assertRaises(MaintenanceTransitionRecordError):
                validate_maintenance_transition_record(rec_mal)

    def test_autopilot_tmux_launcher_control_path_allowlist_and_rejections(self) -> None:
        launcher_path = "scripts/two_site_autopilot_tmux.sh"
        autopilot_path = "scripts/two_site_codex_autopilot.py"

        # 1. Launcher-only in control_blobs and paths
        record, prior_head, repo_head = _make_valid_record(commits_count=1)
        record["new_repository"]["control_blobs"][launcher_path] = "c" * 40
        record["maintenance_commits"][0]["paths"] = [launcher_path]

        validated = validate_maintenance_transition_record(record)
        self.assertEqual(validated["kind"], MAINTENANCE_TRANSITION_KIND)
        self.assertIn(launcher_path, validated["new_repository"]["control_blobs"])
        self.assertEqual(validated["maintenance_commits"][0]["paths"], [launcher_path])

        raw = serialize_maintenance_transition_record(record)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertLessEqual(len(raw), MAX_MAINTENANCE_TRANSITION_RECORD_BYTES)

        parsed = parse_maintenance_transition_record(raw)
        self.assertEqual(parsed, validated)

        built = build_maintenance_transition_record(
            prior_checkpoint_sha256=record["prior_checkpoint_sha256"],
            prior_head=prior_head,
            new_repository=record["new_repository"],
            maintenance_commits=record["maintenance_commits"],
        )
        self.assertEqual(built, validated)

        # 2. Both-control declarations in control_blobs and paths
        record_both, p_head, r_head = _make_valid_record(commits_count=1)
        record_both["new_repository"]["control_blobs"][launcher_path] = "c" * 40
        record_both["new_repository"]["control_blobs"][autopilot_path] = "d" * 40
        record_both["maintenance_commits"][0]["paths"] = sorted([launcher_path, autopilot_path])

        validated_both = validate_maintenance_transition_record(record_both)
        self.assertEqual(
            validated_both["maintenance_commits"][0]["paths"],
            [launcher_path, autopilot_path],
        )
        raw_both = serialize_maintenance_transition_record(record_both)
        self.assertEqual(parse_maintenance_transition_record(raw_both), validated_both)

        # 3. 1..12 contiguous commit semantics with launcher and both controls
        record_12, _, _ = _make_valid_record(commits_count=12)
        record_12["new_repository"]["control_blobs"][launcher_path] = "c" * 40
        record_12["new_repository"]["control_blobs"][autopilot_path] = "d" * 40
        record_12["maintenance_commits"][0]["paths"] = [launcher_path]
        record_12["maintenance_commits"][1]["paths"] = [autopilot_path]
        record_12["maintenance_commits"][2]["paths"] = sorted([launcher_path, autopilot_path])
        raw_12 = serialize_maintenance_transition_record(record_12)
        parsed_12 = parse_maintenance_transition_record(raw_12)
        self.assertEqual(len(parsed_12["maintenance_commits"]), 12)

        # 4. Unrelated control path in control_blobs rejected
        unrelated_ctrl = "scripts/two_site_autopilot_control.py"
        record_diff, _, _ = _make_valid_record(commits_count=1)
        record_diff["new_repository"]["control_blobs"][launcher_path] = "c" * 40
        record_diff["new_repository"]["control_blobs"][unrelated_ctrl] = "d" * 40
        record_diff["maintenance_commits"][0]["paths"] = [unrelated_ctrl]
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record_diff)
        self.assertEqual(str(ctx.exception), "commit path cannot be a control path")

        # 5. Malformed paths and arbitrary .sh scripts rejected
        for bad_sh in [
            "scripts/two_site_other.sh",
            "scripts/two_site_autopilot.sh",
            "scripts/other.sh",
            "tests/test_two_site_autopilot_tmux.sh",
            "scripts/two_site_autopilot_tmux.sh.bak",
            "scripts/two_site_autopilot_tmux.sh2",
            "scripts/two_site_autopilot_tmux.SH",
            "scripts/TWO_SITE_AUTOPILOT_TMUX.sh",
            "Scripts/two_site_autopilot_tmux.sh",
            "prefix_scripts/two_site_autopilot_tmux.sh",
            "scripts/two_site_autopilot_tmux.sh/child",
        ]:
            rec_bad, _, _ = _make_valid_record(commits_count=1)
            rec_bad["maintenance_commits"][0]["paths"] = [bad_sh]
            with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
                validate_maintenance_transition_record(rec_bad)
            self.assertEqual(str(ctx.exception), "commit path does not match allowed pattern")

        for malformed in [
            "/scripts/two_site_autopilot_tmux.sh",
            "../scripts/two_site_autopilot_tmux.sh",
            "scripts/../scripts/two_site_autopilot_tmux.sh",
            "scripts/./two_site_autopilot_tmux.sh",
            "scripts\\two_site_autopilot_tmux.sh",
            "scripts/two_site_autopilot_tmux.sh" + chr(0),
            "scripts/two_site_autopilot_tmux.sh ",
            " scripts/two_site_autopilot_tmux.sh",
        ]:
            rec_mal, _, _ = _make_valid_record(commits_count=1)
            rec_mal["maintenance_commits"][0]["paths"] = [malformed]
            with self.assertRaises(MaintenanceTransitionRecordError):
                validate_maintenance_transition_record(rec_mal)

        # 6. Old record roundtrip preserved
        old_record, _, _ = _make_valid_record(commits_count=2)
        old_raw = serialize_maintenance_transition_record(old_record)
        old_parsed = parse_maintenance_transition_record(old_raw)
        self.assertEqual(old_parsed, validate_maintenance_transition_record(old_record))

    def test_cycle_tools_control_path_allowlist_and_rejections(self) -> None:
        cycle_tools_path = "scripts/two_site_cycle_tools.py"
        test_path = "tests/test_two_site_cycle_tools.py"
        record, prior_head, repo_head = _make_valid_record(commits_count=1)
        record["new_repository"]["control_blobs"][cycle_tools_path] = "c" * 40
        record["maintenance_commits"][0]["paths"] = [cycle_tools_path, test_path]

        validated = validate_maintenance_transition_record(record)
        self.assertEqual(validated["kind"], MAINTENANCE_TRANSITION_KIND)
        self.assertIn(cycle_tools_path, validated["new_repository"]["control_blobs"])
        self.assertEqual(
            validated["maintenance_commits"][0]["paths"],
            [cycle_tools_path, test_path],
        )

        raw = serialize_maintenance_transition_record(record)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertLessEqual(len(raw), MAX_MAINTENANCE_TRANSITION_RECORD_BYTES)

        parsed = parse_maintenance_transition_record(raw)
        self.assertEqual(parsed, validated)

        built = build_maintenance_transition_record(
            prior_checkpoint_sha256=record["prior_checkpoint_sha256"],
            prior_head=prior_head,
            new_repository=record["new_repository"],
            maintenance_commits=record["maintenance_commits"],
        )
        self.assertEqual(built, validated)

        # Different control path in control_blobs remains rejected
        broker_ctrl = "scripts/two_site_provider_broker.py"
        record_diff, _, _ = _make_valid_record(commits_count=1)
        record_diff["new_repository"]["control_blobs"][cycle_tools_path] = "c" * 40
        record_diff["new_repository"]["control_blobs"][broker_ctrl] = "d" * 40
        record_diff["maintenance_commits"][0]["paths"] = sorted([broker_ctrl, test_path])
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record_diff)
        self.assertEqual(str(ctx.exception), "commit path cannot be a control path")

        # Unsorted commit paths rejected
        record_unsorted, _, _ = _make_valid_record(commits_count=1)
        record_unsorted["new_repository"]["control_blobs"][cycle_tools_path] = "c" * 40
        record_unsorted["maintenance_commits"][0]["paths"] = [test_path, cycle_tools_path]
        with self.assertRaises(MaintenanceTransitionRecordError) as ctx:
            validate_maintenance_transition_record(record_unsorted)
        self.assertEqual(str(ctx.exception), "commit paths must be sorted")


if __name__ == "__main__":
    unittest.main()
