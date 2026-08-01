#!/usr/bin/env python3
"""Non-operational planning boundary for the V4 Witness Full-Matrix.

This module deliberately is *not* an execution runner.  It can only build or
rehydrate V4's process-local, non-authorizing plan and inspect the shape of
root-owned adapters supplied by in-process integration code.  It has no
network, peer, provider, SSH, subprocess, host, storage, Docker, promotion,
or phase-execution path.

The CLI accepts no serialized configuration, readiness capability, receipt,
continuity projection, adapter, endpoint, or credential.  Those are
intentionally typed process-local boundaries, so CLI use emits only a
default-off report.  Integrators that already possess the typed root-owned
objects may use ``plan_physical_full_matrix_v4_nonoperational`` and
``validate_physical_full_matrix_v4_nonoperational`` directly.

Those V4 objects accept only the driver's opaque Gen2 witnessed readiness;
historical Gen1 readiness has no CLI, adapter, or fallback route.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Final


REPO_ROOT = Path(__file__).parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import physical_full_matrix_execution_driver_v4 as _driver  # noqa: E402
from core import physical_full_matrix_v4_plan_rehydration as _rehydration  # noqa: E402


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_NON_OPERATIONAL_RUNNER_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_NON_OPERATIONAL_RUNNER_SCHEMA",
    "PhysicalFullMatrixV4NonOperationalRunnerError",
    "nonoperational_physical_full_matrix_v4_report",
    "plan_physical_full_matrix_v4_nonoperational",
    "validate_physical_full_matrix_v4_nonoperational",
)


PHYSICAL_FULL_MATRIX_V4_NON_OPERATIONAL_RUNNER_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-non-operational-runner-v1"
)
PHYSICAL_FULL_MATRIX_V4_NON_OPERATIONAL_RUNNER_DEFAULT_ENABLED: Final = False


class PhysicalFullMatrixV4NonOperationalRunnerError(ValueError):
    """The non-operational planner was given an ambiguous typed request."""


def _require_config_bound_plan(
    *,
    config: _driver.PhysicalFullMatrixV4ExecutionConfig,
    plan: _driver.PhysicalFullMatrixV4ExecutionPlan,
) -> _driver.PhysicalFullMatrixV4ExecutionPlan:
    """Cross-pin a process-local plan to static V4 config without an effect.

    Rehydration intentionally does not revalidate the initial normal-writer
    readiness after a successful writer transition.  Therefore this uses the
    driver's static config check rather than rebuilding a plan from the
    possibly retired initial capability.
    """

    binding, run_id, maximum_age = _driver._static_config(config, require_enabled=True)
    candidate = _driver.require_physical_full_matrix_v4_execution_plan(plan)
    snapshot = _driver._snapshot(candidate)
    if (
        snapshot.binding != binding
        or snapshot.run_id != run_id
        or snapshot.maximum_oracle_age_seconds != maximum_age
    ):
        raise PhysicalFullMatrixV4NonOperationalRunnerError(
            "PHYSICAL_FULL_MATRIX_V4_NON_OPERATIONAL_PLAN_CONFIG_MISMATCH"
        )
    return candidate


def plan_physical_full_matrix_v4_nonoperational(
    *,
    config: _driver.PhysicalFullMatrixV4ExecutionConfig,
) -> _driver.PhysicalFullMatrixV4ExecutionPlan:
    """Build and recheck one process-local V4 plan without any adapter call."""

    plan = _driver.build_physical_full_matrix_v4_execution_plan(config=config)
    return _require_config_bound_plan(config=config, plan=plan)


def validate_physical_full_matrix_v4_nonoperational(
    *,
    config: _driver.PhysicalFullMatrixV4ExecutionConfig,
    plan: _driver.PhysicalFullMatrixV4ExecutionPlan | None = None,
    continuity: object | None = None,
    adapters: _driver.PhysicalFullMatrixV4ExecutionAdapters | None = None,
) -> _driver.PhysicalFullMatrixV4ExecutionPlan:
    """Validate/rehydrate V4 planning inputs without executing a phase.

    ``continuity`` is deliberately an opaque object here: rehydration accepts
    only the journal-minted process-local capability.  No raw receipt or JSON
    counterpart can be supplied.  When ``adapters`` is present, this invokes
    only V4's interface-preparation check; none of the journal, resolver,
    clock, continuity, or phase-adapter methods is called.
    """

    if plan is not None and continuity is not None:
        raise PhysicalFullMatrixV4NonOperationalRunnerError(
            "PHYSICAL_FULL_MATRIX_V4_NON_OPERATIONAL_PLAN_CONTINUITY_AMBIGUOUS"
        )
    if continuity is not None:
        candidate = _rehydration.rehydrate_physical_full_matrix_v4_execution_plan(
            config=config,
            continuity=continuity,  # type: ignore[arg-type]
        )
    elif plan is not None:
        candidate = plan
    else:
        candidate = plan_physical_full_matrix_v4_nonoperational(config=config)

    candidate = _require_config_bound_plan(config=config, plan=candidate)
    if adapters is not None:
        _driver.prepare_physical_full_matrix_v4_execution_adapters(
            plan=candidate,
            adapters=adapters,
        )
    return candidate


def nonoperational_physical_full_matrix_v4_report(
    *,
    mode: str,
) -> dict[str, object]:
    """Return a CLI-safe report that cannot represent execution authority."""

    if mode not in {"no-action", "plan", "validate"}:
        raise PhysicalFullMatrixV4NonOperationalRunnerError(
            "PHYSICAL_FULL_MATRIX_V4_NON_OPERATIONAL_MODE_INVALID"
        )
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_NON_OPERATIONAL_RUNNER_SCHEMA,
        "status": "blocked-typed-injected-v4-objects-required",
        "mode": mode,
        "runner_enabled": PHYSICAL_FULL_MATRIX_V4_NON_OPERATIONAL_RUNNER_DEFAULT_ENABLED,
        "non_operational": True,
        "reason_codes": [
            "cli-does-not-deserialize-v4-capabilities-or-adapters",
            "no-phase-execution-path",
        ],
        "materialization_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "full_matrix_executed": False,
        "direct_fi_to_ir_control": "forbidden",
        "direct_ir_to_fi_control": "forbidden",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a non-operational V4 plan/validation report. Typed V4 objects "
            "are accepted only by the in-process API, never by this CLI."
        )
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--plan",
        action="store_true",
        help="Report the non-authorizing planning mode; it does not build from CLI input.",
    )
    modes.add_argument(
        "--validate",
        action="store_true",
        help="Report the non-authorizing validation mode; it does not invoke adapters.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = "plan" if args.plan else "validate" if args.validate else "no-action"
    print(json.dumps(nonoperational_physical_full_matrix_v4_report(mode=mode), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
