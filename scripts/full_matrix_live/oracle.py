#!/usr/bin/env python3
"""Independent real-host oracle for the three-site staging Full Matrix."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path.cwd().resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.full_matrix_live.common import (  # noqa: E402
    ORACLE_SCHEMA,
    LiveMatrixError,
    child_identity,
    collect_all_host_snapshots,
    hash_summary,
    json_bytes,
    load_plan,
    operation_assertion_names,
    parse_common_args,
    retained_runner,
    validate_catalog,
    verify_clean_release,
)
from scripts.full_matrix_live.recipes import recipe_for  # noqa: E402
from scripts.full_matrix_live.scenario_handlers import verify_scenario  # noqa: E402


def _assert_same_hosts(
    runner: dict[str, Any], observed: dict[str, Any]
) -> None:
    original = runner.get("host_snapshots")
    if not isinstance(original, dict) or set(original) != set(observed):
        raise LiveMatrixError("runner host snapshot set is incomplete")
    for role, fresh in observed.items():
        prior = original.get(role)
        stable = {
            "release_sha",
            "clean",
            "project",
            "machine_id",
            "files",
        }
        if (
            not isinstance(prior, dict)
            or any(prior.get(name) != fresh.get(name) for name in stable)
        ):
            raise LiveMatrixError(f"independent {role} host identity differs")


def verify_preflight(
    args: Any, plan: dict[str, Any], runner: dict[str, Any]
) -> dict[str, Any]:
    release = verify_clean_release(REPO_ROOT, args.release_sha)
    catalog = validate_catalog(args)
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    _assert_same_hosts(runner, snapshots)
    if (
        runner.get("release_checkout") != release
        or runner.get("catalog_sha256") != hash_summary(catalog)
        or runner.get("runtime_plan_sha256") != plan["_sha256"]
        or (runner.get("fault_state") or {}).get("active_fault_count") != 0
    ):
        raise LiveMatrixError("preflight runner claim differs from independent observation")
    return {
        **child_identity(args, schema=ORACLE_SCHEMA),
        "assertions": {
            "campaign_identity_bound": True,
            "prerequisites_verified": True,
            "topology_ready": True,
            "production_boundary": False,
        },
        "residue_count": 0,
        "independent_host_snapshots": snapshots,
        "catalog_sha256": hash_summary(catalog),
    }


def verify_operation(
    args: Any, plan: dict[str, Any], runner: dict[str, Any]
) -> dict[str, Any]:
    if args.operation == "scenario":
        recipe = recipe_for(args.phase, args.scenario_id)
        if not recipe.implemented:
            raise LiveMatrixError(
                "scenario oracle recipe is not implemented: "
                f"{recipe.phase}/{recipe.scenario_id} ({recipe.oracle})"
            )
        verify_clean_release(REPO_ROOT, args.release_sha)
        validate_catalog(args)
        scenario = verify_scenario(args, plan, recipe, runner)
        return {
            **child_identity(args, schema=ORACLE_SCHEMA),
            **scenario,
        }
    verify_clean_release(REPO_ROOT, args.release_sha)
    validate_catalog(args)
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    _assert_same_hosts(runner, snapshots)
    if (runner.get("fault_state") or {}).get("active_fault_count") != 0:
        raise LiveMatrixError("runner left active faults")
    claims = runner.get("operation_claims")
    names = operation_assertion_names(args.operation)
    expected = {
        name: (
            False
            if name == "production_boundary"
            else 0
            if name == "residue_zero"
            else True
        )
        for name in names
    }
    if claims != expected:
        raise LiveMatrixError("runner operation claims are incomplete")
    return {
        **child_identity(args, schema=ORACLE_SCHEMA),
        "assertions": expected,
        "residue_count": 0,
        "independent_host_snapshots": snapshots,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_common_args(argv)
        if args.action != "verify":
            raise LiveMatrixError("oracle accepts verify only")
        plan = load_plan(args)
        runner = retained_runner(args.runner_evidence, args)
        result = (
            verify_preflight(args, plan, runner)
            if args.operation == "preflight"
            else verify_operation(args, plan, runner)
        )
        sys.stdout.buffer.write(json_bytes(result))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
