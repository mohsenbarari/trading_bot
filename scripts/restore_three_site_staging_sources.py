#!/usr/bin/env python3
"""Restore the exact legacy staging service set recorded by source freeze."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.freeze_three_site_staging_sources import DATA_SERVICES, _compose, _run
from scripts.render_three_site_staging_role_compose import _atomic_write
from scripts.run_three_site_staging_source_backup import GIT, _secure_env
from scripts.verify_three_site_staging_inventory import (
    _strict_object,
    load_inventory,
    verify_signed_inventory,
)
from scripts.verify_three_site_staging_role_bundle import _verify_bundle_source


class SourceRestoreError(RuntimeError):
    pass


def confirmation_phrase(campaign_id: str, evidence_hash: str) -> str:
    return f"restore-legacy-staging:{campaign_id}:{evidence_hash}"


def _canonical_hash(value) -> str:  # noqa: ANN001
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify_restore_input(
    evidence: dict,
    *,
    campaign_id: str,
    release_sha: str,
    project_name: str,
) -> dict[str, object]:
    expected_fields = {
        "schema", "campaign_id", "target_release_sha", "project_name", "observed_at",
        "source_roles", "previously_running_services", "stopped_services",
        "running_services", "postgres", "redis_observation",
    }
    previous = evidence.get("previously_running_services") if isinstance(evidence, dict) else None
    stopped = evidence.get("stopped_services") if isinstance(evidence, dict) else None
    if (
        not isinstance(evidence, dict)
        or set(evidence) != expected_fields
        or evidence.get("schema") != "three-site-staging-source-freeze-v1"
        or evidence.get("campaign_id") != campaign_id
        or evidence.get("target_release_sha") != release_sha
        or evidence.get("project_name") != project_name
        or evidence.get("running_services") != ["db", "redis"]
        or not isinstance(previous, list)
        or len(previous) != len(set(previous))
        or not DATA_SERVICES.issubset(previous)
        or not isinstance(stopped, list)
        or set(stopped) != set(previous) - DATA_SERVICES
    ):
        raise SourceRestoreError("legacy source-freeze evidence cannot authorize restore")
    return {
        "evidence_sha256": _canonical_hash(evidence),
        "previously_running_services": sorted(previous),
        "services_to_start": sorted(set(previous) - DATA_SERVICES),
    }


def execute(
    args: argparse.Namespace,
    *,
    inventory_result: dict[str, object],
    evidence: dict,
) -> dict[str, object]:
    verified = verify_restore_input(
        evidence,
        campaign_id=str(inventory_result["campaign_id"]),
        release_sha=str(inventory_result["release_sha"]),
        project_name=args.project_name,
    )
    required = confirmation_phrase(
        str(inventory_result["campaign_id"]), str(verified["evidence_sha256"])
    )
    if args.confirm != required:
        raise SourceRestoreError("legacy source restore confirmation mismatch")
    repo = args.repo.resolve()
    if args.compose.resolve() != (repo / "deploy/staging/docker-compose.staging.yml").resolve():
        raise SourceRestoreError("legacy restore is locked to the reviewed staging Compose")
    if _run([GIT, "-C", str(repo), "rev-parse", "HEAD"]) != inventory_result["release_sha"]:
        raise SourceRestoreError("legacy restore controller is not the signed target release")
    if _run([GIT, "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"]):
        raise SourceRestoreError("legacy restore controller repository must be clean")
    _secure_env(args.env_file)
    prefix = _compose(args)
    current = {
        value for value in _run(
            [*prefix, "ps", "--status", "running", "--services"]
        ).splitlines() if value
    }
    expected = set(verified["previously_running_services"])
    if not (current == DATA_SERVICES or current == expected):
        raise SourceRestoreError("legacy staging has an unexpected partial service state")
    if current != expected:
        _run(
            [*prefix, "up", "-d", "--no-build", *verified["services_to_start"]],
            timeout=300,
        )
    running_after = {
        value for value in _run(
            [*prefix, "ps", "--status", "running", "--services"]
        ).splitlines() if value
    }
    if running_after != expected:
        raise SourceRestoreError("legacy staging service set did not restore exactly")
    result = {
        "schema": "three-site-staging-source-restore-v1",
        "status": "restored",
        "campaign_id": inventory_result["campaign_id"],
        "release_sha": inventory_result["release_sha"],
        "freeze_evidence_sha256": verified["evidence_sha256"],
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "running_services": sorted(running_after),
    }
    _atomic_write(
        args.output,
        (json.dumps(result, sort_keys=True, indent=2) + "\n").encode(),
        mode=0o600,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--project-name", default="trading_bot_staging")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--signer-policy", type=Path, required=True)
    parser.add_argument("--freeze-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        inventory_result = verify_signed_inventory(
            load_inventory(args.inventory),
            approval=load_inventory(args.inventory_approval),
            signer_policy=load_inventory(args.signer_policy),
            host_destructive=True,
        )
        if inventory_result["inventory_stage"] != "provisioned":
            raise SourceRestoreError("legacy source restore requires provisioned inventory")
        evidence = json.loads(
            _verify_bundle_source(
                args.freeze_evidence, expected_mode=0o600
            ).decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
        verified = verify_restore_input(
            evidence,
            campaign_id=str(inventory_result["campaign_id"]),
            release_sha=str(inventory_result["release_sha"]),
            project_name=args.project_name,
        )
        result = {
            "status": "planned",
            "campaign_id": inventory_result["campaign_id"],
            "services_to_start": verified["services_to_start"],
            "required_confirmation": confirmation_phrase(
                str(inventory_result["campaign_id"]), str(verified["evidence_sha256"])
            ),
        }
        if args.apply:
            result = execute(
                args,
                inventory_result=inventory_result,
                evidence=evidence,
            )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
