#!/usr/bin/env python3
"""Emit the safe CLI boundary for the physical Full-Matrix readiness oracle.

The real oracle deliberately accepts opaque capabilities and typed injected
evidence.  A JSON CLI cannot safely reconstruct those capabilities, so this
script never accepts paths, endpoints, credentials, raw receipts, runner
plans, or an execution flag.  It emits a deterministic blocked report and
directs integration code to call the pure Python API with already verified
objects.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.physical_full_matrix_campaign_readiness import (  # noqa: E402
    PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report why the physical Full-Matrix oracle requires typed injected evidence."
    )
    parser.add_argument(
        "--legacy-runner-artifact",
        action="append",
        default=[],
        metavar="IDENTIFIER",
        help="Record a historical runner artifact only to reject it; it is never opened or parsed.",
    )
    return parser


def report(*, legacy_runner_artifacts: list[str]) -> dict[str, object]:
    reason_codes = (
        ["legacy-runner-artifact-rejected"]
        if legacy_runner_artifacts
        else ["typed-injected-evidence-required"]
    )
    return {
        "schema": PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA,
        "status": PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED,
        "reason_codes": reason_codes,
        "external_execution_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(json.dumps(report(legacy_runner_artifacts=args.legacy_runner_artifact), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
