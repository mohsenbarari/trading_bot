#!/usr/bin/env python3
"""Build a validated exact subject for one password+TOTP approval."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.human_approval import approval_subject
from core.secure_file_io import write_secure_atomic_bytes
from core.dr_failover_orchestrator import failover_approval_subject, parse_plan
from scripts.verify_three_site_staging_inventory import (
    _canonical_bytes,
    load_inventory,
    verify_inventory,
)
from scripts.verify_three_site_staging_migration_plan import migration_approval_subject


class ApprovalSubjectError(RuntimeError):
    pass


def inventory_subject(path: Path) -> tuple[dict, dict]:
    payload = load_inventory(path)
    verified = verify_inventory(payload, host_destructive=None)
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    subject = approval_subject(
        artifact_type="three-site-staging-inventory-v3",
        artifact_sha256=digest,
        release_sha=verified["release_sha"],
        bindings={
            "campaign_id": verified["campaign_id"],
            "deployment_id": verified["deployment_id"],
            "host_safety_mode": verified["host_safety_mode"],
            "inventory_stage": verified["inventory_stage"],
        },
    )
    return subject, {
        "action": "approve_inventory",
        "environment": "staging",
        "artifact_sha256": digest,
        "release_sha": verified["release_sha"],
        "campaign_id": verified["campaign_id"],
        "inventory_stage": verified["inventory_stage"],
    }


def migration_subject(path: Path) -> tuple[dict, dict]:
    plan = load_inventory(path)
    subject = migration_approval_subject(plan)
    return subject, {
        "action": "approve_migration",
        "environment": "staging",
        "artifact_sha256": subject["artifact_sha256"],
        "release_sha": subject["release_sha"],
        "campaign_id": subject["bindings"]["campaign_id"],
    }


def failover_subject(path: Path) -> tuple[dict, dict]:
    payload = load_inventory(path)
    plan = parse_plan(payload, require_approval=False)
    subject = failover_approval_subject(plan)
    return subject, {
        "action": plan.action,
        "environment": "staging",
        "artifact_sha256": plan.plan_hash,
        "release_sha": plan.release_sha,
        "operation_id": plan.operation_id,
        "source_site": plan.source_site,
        "target_site": plan.target_site,
        "expected_epoch": plan.expected_epoch,
        "target_epoch": plan.target_epoch,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("inventory", "migration", "failover"))
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        builder = {
            "inventory": inventory_subject,
            "migration": migration_subject,
            "failover": failover_subject,
        }[args.kind]
        subject, summary = builder(args.artifact)
        write_secure_atomic_bytes(
            args.output,
            (json.dumps(subject, sort_keys=True, indent=2) + "\n").encode(),
            label="human approval subject",
            mode=0o600,
        )
        print(json.dumps({"status": "subject-ready", "output": str(args.output), **summary}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
