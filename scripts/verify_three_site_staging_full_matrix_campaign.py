#!/usr/bin/env python3
"""Verify one complete signed three-site staging Full Matrix evidence set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_atomic_bytes
from core.three_site_full_matrix_campaign import (
    BOUND_ARTIFACTS,
    FullMatrixCampaignError,
    secure_json,
    verify_complete_matrix,
)


def _mapping(values: list[str], *, expected: set[str] | frozenset[str], label: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or name not in expected or name in result or not raw_path:
            raise FullMatrixCampaignError(f"{label} mapping is invalid")
        result[name] = Path(raw_path)
    if set(result) != set(expected):
        raise FullMatrixCampaignError(f"{label} mapping is incomplete")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--approver-policy", type=Path, required=True)
    parser.add_argument("--bound-artifact", action="append", default=[])
    parser.add_argument("--phase-evidence", action="append", type=Path, default=[])
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--execution-journal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        campaign = secure_json(args.campaign, label="Full Matrix campaign")
        result = verify_complete_matrix(
            campaign=campaign,
            approver_policy=secure_json(
                args.approver_policy, label="Full Matrix approver policy"
            ),
            bound_artifacts=_mapping(
                args.bound_artifact,
                expected=BOUND_ARTIFACTS,
                label="--bound-artifact",
            ),
            phase_evidence=[
                secure_json(path, label="Full Matrix phase evidence")
                for path in args.phase_evidence
            ],
            artifact_root=args.artifact_root,
            execution_journal=args.execution_journal,
        )
        write_secure_atomic_bytes(
            args.output,
            (json.dumps(result, sort_keys=True, indent=2) + "\n").encode(),
            label="Full Matrix final report",
            mode=0o600,
            max_size=8 * 1024 * 1024,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
