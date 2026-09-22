"""Bounded read-only proof for resolved two-site preparation cycles (AE01-40).

Validates that the latest persisted cycle is a fully resolved, accepted CONTINUE
at the current clean Git HEAD with unchanged refs and active assignment scope.

LIMITATION: This helper proves resolved prior-cycle evidence for a later rollback
gate; it does NOT constitute complete rollback authority by itself. A future
caller must hold Supervisor/client/runner locks and verify that queues are idle
before initiating rollback actions. No AE02 checkpoint wiring is performed here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any

try:
    from scripts import two_site_codex_autopilot as ap
    from scripts import two_site_proposal_runner as runner
    from scripts.two_site_assignment_scope import AssignmentScope
    from scripts.two_site_preparation_contract import (
        PreparationConfigError,
        PreparationStartup,
    )
except ImportError:
    import two_site_codex_autopilot as ap
    import two_site_proposal_runner as runner
    from two_site_assignment_scope import AssignmentScope
    from two_site_preparation_contract import (
        PreparationConfigError,
        PreparationStartup,
    )

__all__ = ["validate_resolved_preparation_boundary"]

_PROPOSAL_ERROR = getattr(runner, "ProposalError", ap.AutopilotError)


def _read_bounded_json(root: Path, file_path: Path, label: str) -> dict[str, Any]:
    """Read and parse a bounded JSON file with strict pre-read lstat guards to avoid FIFO hangs."""
    try:
        st = os.lstat(file_path)
    except (OSError, ValueError):
        raise ap.AutopilotError(f"{label} is missing or unreadable") from None

    if stat.S_ISLNK(st.st_mode) or os.path.islink(file_path):
        raise ap.AutopilotError(f"{label} is a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise ap.AutopilotError(f"{label} is not a regular file")
    if st.st_nlink != 1:
        raise ap.AutopilotError(f"{label} has multiple hard links")
    if st.st_size > ap.MAX_RESULT_BYTES:
        raise ap.AutopilotError(f"{label} exceeds maximum allowed size")

    try:
        if file_path.resolve() != file_path:
            raise ap.AutopilotError(f"{label} path is not canonical")
        text = runner.bounded_file_text(root, file_path, ap.MAX_RESULT_BYTES, label)
        data = runner.unique_json(text)
    except ap.AutopilotError:
        raise
    except (_PROPOSAL_ERROR, OSError, UnicodeError, ValueError):
        raise ap.AutopilotError(f"{label} verification failed") from None

    if not isinstance(data, dict):
        raise ap.AutopilotError(f"{label} content is not a JSON object")
    return data


def validate_resolved_preparation_boundary(root: Path, state_root: Path) -> None:
    """Perform a bounded, read-only proof of resolved prior-cycle preparation evidence."""
    if not isinstance(root, Path) or not isinstance(state_root, Path):
        raise ap.AutopilotError("root and state_root must be Path instances")

    if not root.is_absolute():
        raise ap.AutopilotError("root must be an absolute path")

    try:
        if not root.is_dir() or root.resolve() != root:
            raise ap.AutopilotError("root must be an existing canonical directory")
    except (OSError, ValueError):
        raise ap.AutopilotError("root path verification failed")

    expected_state_root = root / ".local" / "two-site-refactor" / "autopilot"
    if state_root != expected_state_root:
        raise ap.AutopilotError("state_root does not match expected exact path")

    if not state_root.is_absolute():
        raise ap.AutopilotError("state_root must be an absolute path")

    try:
        if not state_root.is_dir() or state_root.resolve() != state_root:
            raise ap.AutopilotError("state_root must be an existing canonical directory")
    except (OSError, ValueError):
        raise ap.AutopilotError("state_root path verification failed")

    stop_path = state_root / "STOP"
    try:
        if os.path.lexists(stop_path):
            raise ap.AutopilotError("STOP path present")
    except (OSError, ValueError):
        raise ap.AutopilotError("STOP path verification failed")

    try:
        livefacts = ap.validate_repository(root, require_clean=True)
        if not isinstance(livefacts, dict):
            raise ap.AutopilotError("validate_repository returned invalid facts")

        current_head = livefacts.get("head")
        if not isinstance(current_head, str) or not re.fullmatch(r"[0-9a-f]{40}", current_head):
            raise ap.AutopilotError("live facts head is invalid")

        scope = livefacts.get("assignment_scope")
        if not isinstance(scope, AssignmentScope):
            raise ap.AutopilotError("live facts assignment_scope is invalid")

        globalstatus = _read_bounded_json(root, state_root / "status.json", "global status")

        raw_evidence = globalstatus.get("evidence")
        if not isinstance(raw_evidence, str):
            raise ap.AutopilotError("global status missing valid evidence")

        if not (1 <= len(raw_evidence) <= 1024):
            raise ap.AutopilotError("evidence path length exceeds bounds")

        if raw_evidence.strip() != raw_evidence:
            raise ap.AutopilotError("evidence path contains leading or trailing whitespace")

        if not os.path.isabs(raw_evidence):
            raise ap.AutopilotError("evidence path must be absolute")

        if os.path.normpath(raw_evidence) != raw_evidence:
            raise ap.AutopilotError("evidence path is not normalized")

        run_dir = Path(raw_evidence)
        runs_dir = state_root / "runs"
        if run_dir.parent != runs_dir:
            raise ap.AutopilotError("evidence run_dir is not a direct child of state_root/runs")

        if len(run_dir.name) > 80 or not ap.RUN_ID.fullmatch(run_dir.name):
            raise ap.AutopilotError("evidence run_dir name does not match RUN_ID pattern or exceeds 80 chars")

        try:
            if not run_dir.is_dir() or run_dir.resolve() != run_dir or os.path.islink(run_dir):
                raise ap.AutopilotError("evidence run_dir is not a canonical directory")
        except (OSError, ValueError):
            raise ap.AutopilotError("evidence run_dir path verification failed")

        meta = _read_bounded_json(root, run_dir / "status.json", "meta status")
        rawresult = _read_bounded_json(root, run_dir / "result.json", "raw result")

        if (
            globalstatus.get("state") != "CONTINUE"
            or globalstatus.get("blockers") != []
            or globalstatus.get("head") != current_head
            or globalstatus.get("head_after") != current_head
            or globalstatus.get("commit") != current_head
            or globalstatus.get("head_changed") is not True
        ):
            raise ap.AutopilotError("global status does not indicate resolved CONTINUE at current head")

        if (
            meta.get("state") != "CONTINUE"
            or meta.get("blockers") != []
            or meta.get("head_after") != current_head
        ):
            raise ap.AutopilotError("meta status does not indicate resolved CONTINUE at current head")

        head_before = meta.get("head_before")
        if (
            not isinstance(head_before, str)
            or not re.fullmatch(r"[0-9a-f]{40}", head_before)
            or head_before == current_head
        ):
            raise ap.AutopilotError("meta head_before is not valid distinct 40-hex commit")

        if meta.get("protected_refs") != livefacts.get("protected_refs"):
            raise ap.AutopilotError("meta protected_refs do not match live protected_refs")

        meta_scope = meta.get("assignment_scope")
        if not isinstance(meta_scope, dict):
            raise ap.AutopilotError("meta assignment_scope must be a dict")

        meta_id = meta_scope.get("assignment_id")
        meta_stage = meta_scope.get("stage_id")
        meta_sha = meta_scope.get("sha256")

        if (
            meta_id != scope.assignment_id
            or meta_stage != scope.stage_id
            or meta_sha != scope.sha256
        ):
            raise ap.AutopilotError("meta assignment_scope does not match live assignment scope")

        validated_result = ap.validate_result(rawresult)
        if (
            validated_result.get("state") != "CONTINUE"
            or validated_result.get("blockers") != []
            or validated_result.get("commit") != current_head
        ):
            raise ap.AutopilotError("raw result does not indicate resolved CONTINUE at current head")

        worker_id = rawresult.get("gemini_worker_run_id")
        reviewer_id = rawresult.get("gemini_reviewer_run_id")
        proposal_sha = rawresult.get("proposal_sha256")

        if (
            not isinstance(worker_id, str)
            or not isinstance(reviewer_id, str)
            or not isinstance(proposal_sha, str)
        ):
            raise ap.AutopilotError("raw result missing Gemini evidence identifiers")

        if (
            globalstatus.get("gemini_worker_run_id") != worker_id
            or globalstatus.get("gemini_reviewer_run_id") != reviewer_id
            or globalstatus.get("proposal_sha256") != proposal_sha
            or meta.get("gemini_worker_run_id") != worker_id
            or meta.get("gemini_reviewer_run_id") != reviewer_id
            or meta.get("proposal_sha256") != proposal_sha
        ):
            raise ap.AutopilotError("Gemini evidence identifiers do not bind across records")

        if "preparation_startup" not in meta:
            raise ap.AutopilotError("meta missing preparation_startup")

        try:
            startup = PreparationStartup.from_mapping(meta["preparation_startup"])
        except (PreparationConfigError, ValueError):
            raise ap.AutopilotError("meta preparation_startup mapping is invalid") from None

        startup_digest = meta.get("preparation_startup_sha256")

        if startup_digest != startup.fingerprint:
            raise ap.AutopilotError("meta preparation startup fingerprint mismatch")

        if startup.cycle_id != run_dir.name:
            raise ap.AutopilotError("preparation startup cycle_id does not match run_dir name")

        if startup.source_head != head_before:
            raise ap.AutopilotError("preparation startup source_head does not match head_before")

        if startup.assignment_sha256 != scope.sha256:
            raise ap.AutopilotError("preparation startup assignment_sha256 does not match scope sha256")

        expected_bytes = json.dumps(
            startup.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")

        ap._validate_startup_file(
            root,
            run_dir / "preparation-startup.json",
            startup,
            expected_bytes,
        )

        proposal = ap.validate_scoped_gemini_chain(root, head_before, rawresult, scope)
        ap.validate_scoped_committed_proposal(root, head_before, current_head, proposal, scope)

        final_facts = ap.validate_repository(root, require_clean=True)
        if not isinstance(final_facts, dict):
            raise ap.AutopilotError("second validate_repository returned invalid facts")

        if (
            final_facts.get("head") != livefacts.get("head")
            or final_facts.get("status") != livefacts.get("status")
            or final_facts.get("protected_refs") != livefacts.get("protected_refs")
            or final_facts.get("assignment_scope") != livefacts.get("assignment_scope")
        ):
            raise ap.AutopilotError("repository facts drifted during validation")
    except ap.AutopilotError:
        raise
    except (
        _PROPOSAL_ERROR,
        PreparationConfigError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ):
        raise ap.AutopilotError("preparation boundary verification failed") from None
