"""Tests for AE01 gate-first work selection policy and stock autopilot prompt rendering."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

import pytest

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_codex_autopilot import PROMPT_REL, prompt_for_cycle


def test_template_preserves_original_and_gate_priority() -> None:
    frozen_original_sha256 = "2c007088449c307aadb4bd7acbc9c97a1fb71aca15a3f54182f73472af085531"
    original_byte_len = 17264
    template_path = Path("docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md")
    assert template_path.is_file(), f"Template file missing: {template_path}"

    with template_path.open("rb") as f:
        raw_bytes = f.read(24001)
    assert len(raw_bytes) < 24001, "Template size exceeds bounded read threshold of 24001 bytes"
    assert len(raw_bytes) > original_byte_len, "Template must include appended policy bytes"

    prefix_bytes = raw_bytes[:original_byte_len]
    actual_sha = hashlib.sha256(prefix_bytes).hexdigest()
    assert actual_sha == frozen_original_sha256, (
        f"Original template prefix SHA mismatch: expected {frozen_original_sha256}, got {actual_sha}"
    )

    appended_text = raw_bytes[original_byte_len:].decode("utf-8")

    def assert_gate_first_policy_invariants(policy_text: str) -> None:
        assert "Gate-first work selection" in policy_text, "Missing 'Gate-first work selection' heading"
        assert "validated_previous_cycle_hint" in policy_text, "Missing validated_previous_cycle_hint"
        assert "next_action" in policy_text, "Missing next_action"
        assert "not a mandatory queue of suggested helper files" in policy_text, "Missing advisory hint clause"
        assert "Prefer the smallest complete dependency-ready action that closes the actual pending gate" in policy_text, (
            "Missing gate-priority preference"
        )
        assert "At pending-five" in policy_text, "Missing pending-five threshold clause"
        assert "do NOT accept a sixth behavior" in policy_text, "Missing sixth behavior unit prohibition"
        assert "Reconcile only exact accepted evidence, in smaller nonoverlapping packets" in policy_text, (
            "Missing smaller nonoverlapping packet reconciliation requirement"
        )
        assert "If the candidate reveals a real defect that blocks its exact validation, repair that specific defect" in policy_text, (
            "Missing defect exception phrase"
        )
        assert "Do NOT invent an exhaustive generic validator mutation matrix" in policy_text, (
            "Missing generic validator matrix prohibition"
        )
        assert "suggestion to test another generic missing field may be bypassed ONLY after current evidence proves it is not required" in policy_text, (
            "Missing bypass rule for generic validator tests"
        )
        assert "Missing or contradictory evidence remains UNKNOWN/blocked" in policy_text, (
            "Missing UNKNOWN/blocked clause"
        )
        assert "The policy explicitly forbids acceptance inflation" in policy_text, (
            "Missing acceptance inflation prohibition"
        )
        assert "auxiliary tests, documentation, and CONTINUE are not new accepted product behavior" in policy_text, (
            "Missing definition of non-acceptance items"
        )
        assert "Coordinator review, test, security, exact-byte, ref, role, and semantic-reservation rules still apply" in policy_text, (
            "Missing preservation of coordinator standing controls"
        )
        assert "No permissions expansion, direct Provider, root-authored project edits, automatic replay, reset, or external action" in policy_text, (
            "Missing prohibition on permission expansion / direct Provider"
        )

    # 1. Meaningful assertion helper before mutation
    assert_gate_first_policy_invariants(appended_text)

    # 2. In-memory policy omission causes AssertionError
    omitted_policy = appended_text.replace(
        "If the candidate reveals a real defect that blocks its exact validation, repair that specific defect under scope with independent tests and review.",
        "",
    )
    with pytest.raises(AssertionError):
        assert_gate_first_policy_invariants(omitted_policy)

    # 3. Meaningful assertion helper after restoration
    assert_gate_first_policy_invariants(appended_text)


def test_stock_prompt_retains_valid_advisory_hint() -> None:
    template_path = Path("docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md")
    cand_bytes = template_path.read_bytes()
    cand_sha = hashlib.sha256(cand_bytes).hexdigest()

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        target_template = root / PROMPT_REL
        target_template.parent.mkdir(parents=True, exist_ok=True)
        target_template.write_bytes(cand_bytes)

        scope = AssignmentScope(
            assignment_id="ae01-work-selection-step3",
            stage_id="P1-00b",
            allowed_paths=(
                "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md",
                "tests/test_two_site_gate_first_priority.py",
            ),
            forbidden_paths=("config/secrets.env",),
            expires_at="2026-10-02T23:59:59Z",
            sha256="c29aac86ed90cdd32f35af2d49fe6e36762c8ce50dde4740ab02598b366eb691",
        )

        head = "56d6662137b6791cb351a0a0d5d9ea300a7ea643"
        before: dict[str, object] = {
            "head": head,
            "protected_refs": ["refs/heads/main", "refs/heads/production"],
            "assignment_scope": scope,
        }

        continuation: dict[str, object] = {
            "state": "CONTINUE",
            "head_changed": True,
            "blockers": [],
            "head_after": head,
            "commit": head,
            "summary": "Accepted normal161 c13 unit",
            "next_action": "scripts/two_site_cycle_packet.py auxiliary validator test",
            "gemini_worker_run_id": "worker-run-168-01",
            "gemini_reviewer_run_id": "reviewer-run-168-01",
            "proposal_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        }

        before_snapshot = copy.deepcopy(before)
        continuation_snapshot = copy.deepcopy(continuation)

        rendered = prompt_for_cycle(
            root=root,
            cycle=1,
            before=before,
            continuation=continuation,
        )

        # Assert fixture bytes and inputs immutable
        assert hashlib.sha256(target_template.read_bytes()).hexdigest() == cand_sha, "Fixture template bytes mutated"
        assert before == before_snapshot, "Input dict 'before' was mutated"
        assert continuation == continuation_snapshot, "Input dict 'continuation' was mutated"

        marker = "\n\nSUPERVISOR_CONTEXT_JSON:\n"
        assert marker in rendered, "SUPERVISOR_CONTEXT_JSON marker missing from rendered prompt"
        prompt_prefix, context_json_str = rendered.split(marker, 1)

        # Full prompt prefix equals exact candidate template and includes gate-priority policy
        assert prompt_prefix == cand_bytes.decode("utf-8"), "Prompt prefix does not match candidate template"
        assert "Gate-first work selection" in prompt_prefix, "Gate-first work selection missing from prompt prefix"

        actual_context = json.loads(context_json_str)
        expected_context: dict[str, object] = {
            "assignment_scope": {
                "allowed_paths": [
                    "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md",
                    "tests/test_two_site_gate_first_priority.py",
                ],
                "assignment_id": "ae01-work-selection-step3",
                "expires_at": "2026-10-02T23:59:59Z",
                "forbidden_paths": ["config/secrets.env"],
                "sha256": "c29aac86ed90cdd32f35af2d49fe6e36762c8ce50dde4740ab02598b366eb691",
                "stage_id": "P1-00b",
            },
            "cycle": 1,
            "protected_refs": ["refs/heads/main", "refs/heads/production"],
            "starting_head": head,
            "startup_validation": True,
            "validated_previous_cycle_hint": {
                "commit": head,
                "gemini_reviewer_run_id": "reviewer-run-168-01",
                "gemini_worker_run_id": "worker-run-168-01",
                "head_after": head,
                "next_action": "scripts/two_site_cycle_packet.py auxiliary validator test",
                "proposal_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "summary": "Accepted normal161 c13 unit",
            },
        }

        assert actual_context == expected_context, f"Context mismatch: expected {expected_context}, got {actual_context}"
        hint = actual_context["validated_previous_cycle_hint"]
        assert isinstance(hint, dict)
        assert len(hint) == 7, "validated_previous_cycle_hint must have exactly 7 fields"
        assert hint["next_action"] == "scripts/two_site_cycle_packet.py auxiliary validator test"


def test_stock_prompt_rejects_stale_hint_without_weakening_gates() -> None:
    template_path = Path("docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md")
    cand_bytes = template_path.read_bytes()
    cand_sha = hashlib.sha256(cand_bytes).hexdigest()

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        target_template = root / PROMPT_REL
        target_template.parent.mkdir(parents=True, exist_ok=True)
        target_template.write_bytes(cand_bytes)

        scope = AssignmentScope(
            assignment_id="ae01-work-selection-step3",
            stage_id="P1-00b",
            allowed_paths=(
                "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md",
                "tests/test_two_site_gate_first_priority.py",
            ),
            forbidden_paths=("config/secrets.env",),
            expires_at="2026-10-02T23:59:59Z",
            sha256="c29aac86ed90cdd32f35af2d49fe6e36762c8ce50dde4740ab02598b366eb691",
        )

        current_head = "56d6662137b6791cb351a0a0d5d9ea300a7ea643"
        stale_head = "1111111111111111111111111111111111111111"

        before: dict[str, object] = {
            "head": current_head,
            "protected_refs": ["refs/heads/main", "refs/heads/production"],
            "assignment_scope": scope,
        }

        stale_continuation: dict[str, object] = {
            "state": "CONTINUE",
            "head_changed": True,
            "blockers": [],
            "head_after": stale_head,
            "commit": stale_head,
            "summary": "Stale prior cycle result",
            "next_action": "scripts/two_site_cycle_packet.py auxiliary validator test",
            "gemini_worker_run_id": "worker-run-167-01",
            "gemini_reviewer_run_id": "reviewer-run-167-01",
            "proposal_sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        }

        before_snapshot = copy.deepcopy(before)
        continuation_snapshot = copy.deepcopy(stale_continuation)

        rendered = prompt_for_cycle(
            root=root,
            cycle=2,
            before=before,
            continuation=stale_continuation,
        )

        # Assert fixture bytes and inputs immutable
        assert hashlib.sha256(target_template.read_bytes()).hexdigest() == cand_sha, "Fixture template bytes mutated"
        assert before == before_snapshot, "Input dict 'before' was mutated"
        assert stale_continuation == continuation_snapshot, "Input dict 'stale_continuation' was mutated"

        marker = "\n\nSUPERVISOR_CONTEXT_JSON:\n"
        assert marker in rendered, "Marker missing"
        prompt_prefix, context_json_str = rendered.split(marker, 1)

        assert prompt_prefix == cand_bytes.decode("utf-8"), "Prompt prefix does not match candidate template"
        actual_context = json.loads(context_json_str)

        expected_stale_context: dict[str, object] = {
            "assignment_scope": {
                "allowed_paths": [
                    "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md",
                    "tests/test_two_site_gate_first_priority.py",
                ],
                "assignment_id": "ae01-work-selection-step3",
                "expires_at": "2026-10-02T23:59:59Z",
                "forbidden_paths": ["config/secrets.env"],
                "sha256": "c29aac86ed90cdd32f35af2d49fe6e36762c8ce50dde4740ab02598b366eb691",
                "stage_id": "P1-00b",
            },
            "cycle": 2,
            "protected_refs": ["refs/heads/main", "refs/heads/production"],
            "starting_head": current_head,
            "startup_validation": True,
        }

        def assert_stale_suppressed_oracle(ctx: dict[str, object]) -> None:
            assert "validated_previous_cycle_hint" not in ctx, "Stale hint must not be included in supervisor context"
            assert ctx == expected_stale_context, f"Context mismatch: expected {expected_stale_context}, got {ctx}"

        # 1. Oracle before
        assert_stale_suppressed_oracle(actual_context)

        # 2. Oracle within raises on a deliberately mutated context
        mutated_ctx = dict(actual_context)
        mutated_ctx["validated_previous_cycle_hint"] = {
            "commit": stale_head,
            "head_after": stale_head,
            "next_action": "should not exist",
        }
        with pytest.raises(AssertionError):
            assert_stale_suppressed_oracle(mutated_ctx)

        # 3. Oracle after restoration
        assert_stale_suppressed_oracle(actual_context)
