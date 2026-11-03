"""Read-only startup admission for a committed models-JSON-only change.

The existing accepted checkpoint remains historical evidence. This admits a fresh
cycle; it neither adopts an old Task nor creates acceptance or reservation credit.
"""
from __future__ import annotations

from pathlib import Path

from scripts import two_site_codex_autopilot as autopilot
from scripts.two_site_ai_config import AI_CONFIG_REL, load_ai_config


def is_reviewed_model_config_transition(root, checkpoint, repository) -> bool:
    """Return True only for a clean, same-Scope, config-only descendant of the CP.

    Caller holds the ordinary Supervisor startup lock. Every fact is re-read; a
    missing, stale or malformed checkpoint is not a model-change authorization.
    """
    try:
        root = Path(root)
        if not root.is_absolute() or root.resolve(strict=True) != root:
            return False
        state_root = root / ".local/two-site-refactor/autopilot"
        accepted = autopilot._load_accepted_checkpoint(state_root)
        if accepted is None or autopilot.serialize_json(accepted) != autopilot.serialize_json(checkpoint):
            return False
        current = autopilot.validate_accepted_repository(repository)
        if autopilot.validate_accepted_repository(autopilot.validate_repository(root, require_clean=True)) != current:
            return False
        prior = accepted["repository"]
        config_path = AI_CONFIG_REL.as_posix()
        if (prior["head"] == current["head"]
                or prior["protected_refs"] != current["protected_refs"]
                or prior["assignment_scope"] != current["assignment_scope"]
                or set(prior["control_blobs"]) != set(current["control_blobs"])
                or config_path not in prior["control_blobs"]
                or {path for path in prior["control_blobs"]
                    if prior["control_blobs"][path] != current["control_blobs"][path]} != {config_path}):
            return False
        autopilot.git(root, "merge-base", "--is-ancestor", prior["head"], current["head"])
        changed = autopilot.git(root, "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z",
                                prior["head"], current["head"], "--")
        if changed != config_path + "\0":
            return False
        # Bind the historical CP's declared controls to their actual Git objects.
        # Current controls were independently re-derived by validate_repository.
        for path, blob in prior["control_blobs"].items():
            if autopilot.git(root, "rev-parse", f"{prior['head']}:{path}") != blob:
                return False
        load_ai_config(root)
        # Recheck after the bounded Git/config reads; no startup state is written.
        return (autopilot._load_accepted_checkpoint(state_root) == accepted
                and autopilot.validate_accepted_repository(
                    autopilot.validate_repository(root, require_clean=True)) == current)
    except Exception:
        return False
