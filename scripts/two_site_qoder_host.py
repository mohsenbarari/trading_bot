"""Finite trusted-host dispatch; no arbitrary command or model-owned authority.

The monitor classifies only. Each selected adapter takes MON then AP itself and
revalidates current facts. The classifier never turns model prose into a right.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import sys
import subprocess

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def classify_host_action(root):
    from scripts import two_site_qoder_maintenance as maintenance
    root = Path(root).resolve(strict=True); maintenance.no_stop(root)
    # Modules are part of the reviewed host control catalog. Missing policy is
    # an explicit legacy no-action path, never a missing-module catch/bypass.
    from scripts.two_site_qoder_business_reconcile import (
        pending_business_reconciliation, classify_business_reconciliation,
    )
    pending = pending_business_reconciliation(root)
    if pending is not None:
        return "BUSINESS_RECONCILIATION"
    status_path = root / maintenance.STATE_REL / "status.json"
    if os.path.lexists(status_path):
        status = maintenance.decode(maintenance.read(root, status_path.relative_to(root), 65536, private=True))
        if status.get("state") in {"BLOCKED", "FAILED"} and type(status.get("evidence")) is str:
            if classify_business_reconciliation(root, Path(status["evidence"])) is not None:
                return "BUSINESS_RECONCILIATION"
    from scripts.two_site_qoder_authority import classify_lifecycle_action
    if classify_lifecycle_action(root) is not None:
        return "AUTHORITY_LIFECYCLE"
    return maintenance.monitor_action(root)


def dispatch_host_action(root):
    from scripts import two_site_codex_autopilot as ap
    from scripts import two_site_qoder_maintenance as maintenance
    root = Path(root).resolve(strict=True)
    # No AP -> blocking MON inversion: release this read-only classification
    # lock before an adapter acquires MON -> AP and repeats all its own gates.
    with ap.exclusive_supervisor_lock(root / maintenance.STATE_REL / "autopilot.lock"):
        action = classify_host_action(root)
    if action == "AUTHORITY_LIFECYCLE":
        from scripts.two_site_qoder_authority import dispatch_lifecycle
        result = dispatch_lifecycle(root)
        if type(result) is dict and result.get("resume_ordinary") is True:
            return resume_after_lifecycle(root, result)
        return result
    if action == "BUSINESS_RECONCILIATION":
        from scripts.two_site_qoder_business_reconcile import recover_business_reconciliation
        return recover_business_reconciliation(root)
    if action in {"REPAIR_TERMINAL_FAILURE", "FINISH_OWNED_MAINTENANCE", "CONTINUE_OWNED_PROVIDER", "RESUME_REVIEWED_MAINTENANCE"}:
        return maintenance.recover(root)
    return {"state": "NO_ACTION"}


def resume_after_lifecycle(root, result):
    """A model/adapter boolean grants no launch authority; replay host proof."""
    from scripts import two_site_codex_autopilot as ap
    from scripts import two_site_qoder_maintenance as maintenance
    from scripts.two_site_maintenance_transition_startup import authority_startup_locked
    root = Path(root).resolve(strict=True)
    with maintenance.monitor_hold(root):
        with ap.exclusive_supervisor_lock(root / maintenance.STATE_REL / "autopilot.lock"):
            maintenance.no_stop(root)
            current = maintenance.repository(root)
            if result.get("state") == "AUTHORITY_TRANSITION_READY":
                transition = result.get("transition")
                maintenance.need(type(transition) is dict
                    and maintenance.same(transition.get("new_repository"), current), "HOST_AUTHORITY_RESULT_BINDING")
                maintenance.need(authority_startup_locked(root, root / maintenance.STATE_REL, current),
                                 "HOST_AUTHORITY_STARTUP_REQUIRED")
            else:
                maintenance.need(result.get("state") == "LIFECYCLE_REVIEW_REJECTED", "HOST_LIFECYCLE_RESULT")
                checkpoint, digest = maintenance.checkpoint(root)
                maintenance.need(maintenance.same(result.get("repository"), current)
                    and result.get("prior_checkpoint_sha256") == digest
                    and ap._accepted_checkpoint_matches_repository(checkpoint, current), "HOST_REJECTION_CPP_BINDING")
            maintenance.no_stop(root)
        # All proof/Source checks complete; AP is released before the launcher.
        maintenance.no_stop(root)
        subprocess.run(["/usr/bin/bash", str(root / "scripts/two_site_autopilot_tmux.sh"),
            "start", "--quota-wait-enabled"], cwd=root, check=True, timeout=45)
    return {**result, "ordinary_handoff": "STARTED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--recover", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.inspect:
            from scripts import two_site_codex_autopilot as ap
            from scripts.two_site_qoder_maintenance import STATE_REL
            with ap.exclusive_supervisor_lock(args.root / STATE_REL / "autopilot.lock"):
                result = {"action": classify_host_action(args.root)}
        else:
            result = dispatch_host_action(args.root)
        print(json.dumps(result)); return 0
    except Exception as error:
        # No tracebacks, prompts, arbitrary exception values or partial success.
        print(json.dumps({"state": "BLOCKED", "error_type": type(error).__name__})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
