#!/usr/bin/env python3
"""Build an unapproved provisioned inventory from four measured host snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.render_three_site_staging_role_compose import _atomic_write
from scripts.verify_three_site_staging_host_identity import verify_host_snapshot
from scripts.verify_three_site_staging_inventory import (
    ROLES,
    load_inventory,
    verify_inventory,
    verify_approved_inventory,
)


ROLE_CLI = {role.replace("_", "-"): role for role in ROLES}


class InventoryFinalizationError(RuntimeError):
    pass


def finalize_inventory(
    *,
    planned: dict,
    approval: dict,
    approval_policy: dict,
    snapshots: dict[str, dict],
) -> dict:
    approved = verify_approved_inventory(
        planned,
        approval=approval,
        approval_policy=approval_policy,
        host_destructive=None,
    )
    if approved["inventory_stage"] != "planned":
        raise InventoryFinalizationError("inventory finalization requires an approved planned inventory")
    if set(snapshots) != set(ROLE_CLI):
        raise InventoryFinalizationError("exactly four measured role snapshots are required")
    by_role = {item["role"]: item for item in planned["roles"]}
    measured_system_ids: dict[str, str] = {}
    for cli_role, inventory_role in ROLE_CLI.items():
        role_inventory = by_role[inventory_role]
        verify_host_snapshot(
            snapshots[cli_role],
            role=cli_role,
            role_inventory=role_inventory,
            release_sha=approved["release_sha"],
            stage="measure-provisioned",
        )
        measured_system_ids[inventory_role] = str(
            snapshots[cli_role]["postgres_system_id"]
        )
    if len(set(measured_system_ids.values())) != len(measured_system_ids):
        raise InventoryFinalizationError("measured PostgreSQL system identifiers are not distinct")
    production_ids = {
        str(value).lower()
        for value in planned["production_boundaries"]["postgres_system_ids"]
    }
    if {value.lower() for value in measured_system_ids.values()} & production_ids:
        raise InventoryFinalizationError("measured PostgreSQL identity overlaps production")

    result = json.loads(json.dumps(planned))
    result["inventory_stage"] = "provisioned"
    for role in result["roles"]:
        role["postgres_system_id"] = measured_system_ids[role["role"]]
    verify_inventory(result, host_destructive=None)
    return result


def _parse_snapshot(value: str) -> tuple[str, Path]:
    role, separator, path = value.partition("=")
    if not separator or role not in ROLE_CLI or not path:
        raise InventoryFinalizationError("--snapshot must use role=/path/snapshot.json")
    return role, Path(path)


def _load_snapshot(path: Path) -> dict:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise InventoryFinalizationError(f"host snapshot is unavailable: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size <= 0
    ):
        raise InventoryFinalizationError(
            f"host snapshot must be a non-linked mode-0600 file: {path}"
        )
    return load_inventory(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--planned-inventory", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--snapshot", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        parsed = [_parse_snapshot(value) for value in args.snapshot]
        if len(parsed) != len(ROLE_CLI) or len({role for role, _path in parsed}) != len(ROLE_CLI):
            raise InventoryFinalizationError("four distinct snapshots are required")
        result = finalize_inventory(
            planned=load_inventory(args.planned_inventory),
            approval=load_inventory(args.approval),
            approval_policy=load_inventory(args.approval_policy),
            snapshots={role: _load_snapshot(path) for role, path in parsed},
        )
        encoded = (
            json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode()
        _atomic_write(args.output, encoded, mode=0o600)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "provisioned-inventory-created-unapproved",
                "output": str(args.output),
                "inventory_sha256": hashlib.sha256(
                    json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "next_gate": "issue one fresh password-plus-TOTP approval bound to this provisioned inventory",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
