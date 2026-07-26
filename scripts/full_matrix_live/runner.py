#!/usr/bin/env python3
"""Source-owned real-host doer for the three-site staging Full Matrix."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path.cwd().resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.full_matrix_live.common import (  # noqa: E402
    RUNNER_SCHEMA,
    LiveMatrixError,
    child_identity,
    collect_all_host_snapshots,
    hash_summary,
    json_bytes,
    load_plan,
    operation_assertion_names,
    parse_common_args,
    scenario_contract,
    validate_catalog,
    verify_clean_release,
)
from scripts.full_matrix_live.recipes import recipe_for  # noqa: E402
from scripts.full_matrix_live.failover_coordinator import (  # noqa: E402
    preflight_transition_system,
)
from scripts.full_matrix_live.scenario_handlers import (  # noqa: E402
    execute_scenario,
    recover_active_faults,
)
from core.three_site_execution_safety import DEDICATED_HOST_DESTRUCTIVE  # noqa: E402


def _fault_state(plan: dict[str, Any]) -> dict[str, Any]:
    path = plan["_state_root"] / "active-faults.json"
    if not path.exists():
        return {"active_fault_count": 0, "active_faults": [], "state_file": None}
    # The actual remover is intentionally not guessed.  A retained fault must
    # be reconciled by its source-owned recipe before a campaign operation can
    # claim a clean state.
    raise LiveMatrixError("retained Full Matrix fault state requires recipe recovery")


def _artifact_bindings(plan: dict[str, Any]) -> dict[str, str]:
    return {
        name: str(binding["sha256"])
        for name, binding in sorted(plan["_bindings"].items())
    }


def _dedicated_host_provider_preflight(plan: dict[str, Any]) -> dict[str, Any]:
    """Read back the exact disposable-host identities before fault testing.

    This is deliberately unavailable to shared-host campaigns and calls only
    the provider's GET endpoints.  It does not attempt a power operation; the
    destructive scenario handler must still bind and authorize each later
    action independently.
    """

    if plan["execution_class"] != DEDICATED_HOST_DESTRUCTIVE:
        return {"required": False, "inspection": None}
    from scripts.provision_arvan_full_matrix_destructive_hosts import (
        TOKEN_FILE,
        inspect_existing_hosts,
    )
    from scripts.provision_arvan_witness_recovery_vps import read_private_text

    inspection = inspect_existing_hosts(read_private_text(TOKEN_FILE))
    if (
        inspection.get("status") != "passed"
        or inspection.get("read_only") is not True
        or inspection.get("delete_operation_available") is not False
        or set(inspection.get("roles") or {})
        != {"bot_fi", "webapp_fi", "webapp_ir", "witness"}
        or any(
            item.get("status") != "ACTIVE"
            for item in (inspection.get("roles") or {}).values()
            if isinstance(item, dict)
        )
    ):
        raise LiveMatrixError("dedicated-host provider preflight did not pass")
    return {"required": True, "inspection": inspection}


def execute_preflight(args: Any, plan: dict[str, Any]) -> dict[str, Any]:
    release = verify_clean_release(REPO_ROOT, args.release_sha)
    catalog = validate_catalog(args)
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    faults = _fault_state(plan)
    provider = _dedicated_host_provider_preflight(plan)
    failover = preflight_transition_system(plan)
    return {
        **child_identity(args, schema=RUNNER_SCHEMA),
        "release_checkout": release,
        "catalog_sha256": hash_summary(catalog),
        "artifact_bindings": _artifact_bindings(plan),
        "host_snapshots": snapshots,
        "fault_state": faults,
        "dedicated_host_provider_preflight": provider,
        "failover_transition_preflight": failover,
        "runtime_plan_sha256": plan["_sha256"],
    }


def execute_operation(args: Any, plan: dict[str, Any]) -> dict[str, Any]:
    if args.operation == "scenario":
        contract = scenario_contract(args)
        recipe = recipe_for(args.phase, args.scenario_id)
        if not recipe.implemented:
            raise LiveMatrixError(
                "live scenario recipe is not implemented: "
                f"{contract['phase']}/{contract['scenario_id']} "
                f"(doer={recipe.doer}, oracle={recipe.oracle})"
            )
        release = verify_clean_release(REPO_ROOT, args.release_sha)
        validate_catalog(args)
        faults = _fault_state(plan)
        if faults["active_fault_count"] != 0:
            raise LiveMatrixError("scenario cannot start with retained faults")
        scenario = execute_scenario(args, plan, recipe)
        return {
            **child_identity(args, schema=RUNNER_SCHEMA),
            "release_checkout": release,
            "fault_state": faults,
            "scenario_contract": contract,
            **scenario,
        }
    release = verify_clean_release(REPO_ROOT, args.release_sha)
    validate_catalog(args)
    recovered = (
        recover_active_faults(plan)
        if args.operation in {"recovery", "cleanup", "finalize"}
        else {"recovered_fault_count": 0, "recovered_kinds": []}
    )
    faults = _fault_state(plan)
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    names = operation_assertion_names(args.operation)
    return {
        **child_identity(args, schema=RUNNER_SCHEMA),
        "release_checkout": release,
        "host_snapshots": snapshots,
        "fault_state": faults,
        "recovery_actions": recovered,
        "operation_claims": {
            name: (
                False
                if name == "production_boundary"
                else 0
                if name == "residue_zero"
                else True
            )
            for name in names
        },
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_common_args(argv)
        if args.action != "execute":
            raise LiveMatrixError("runner accepts execute only")
        plan = load_plan(args)
        result = (
            execute_preflight(args, plan)
            if args.operation == "preflight"
            else execute_operation(args, plan)
        )
        sys.stdout.buffer.write(json_bytes(result))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
