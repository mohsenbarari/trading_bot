#!/usr/bin/env python3
"""Execute/resume or independently verify the authoritative staging Matrix."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_atomic_bytes
from core.three_site_full_matrix_campaign import (
    BOUND_ARTIFACTS,
    PHASES,
    secure_json,
    verify_complete_matrix,
)
from core.three_site_full_matrix_command_backend import CommandFullMatrixBackend
from core.three_site_full_matrix_runner import run_full_matrix_campaign


CONFIRM_ENV = "THREE_SITE_STAGING_FULL_MATRIX_CONFIRM"
CONFIRM_VALUE = "execute-authoritative-three-site-staging-full-matrix"


def _bound(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or name in result:
            raise ValueError("--bound-artifact must be a unique name=/absolute/path")
        path = Path(raw_path)
        if name not in BOUND_ARTIFACTS or not path.is_absolute():
            raise ValueError("bound artifact name/path is invalid")
        result[name] = path
    if set(result) != BOUND_ARTIFACTS:
        raise ValueError("all Full Matrix bound artifacts are required")
    return result


def _phase_evidence(campaign: dict, artifact_root: Path) -> list[dict]:
    return [
        secure_json(
            artifact_root
            / f"{campaign['campaign_id']}-i{iteration:02d}-{phase}-evidence.json",
            label="Full Matrix phase evidence",
        )
        for iteration in range(1, campaign["repetitions"] + 1)
        for phase in PHASES
    ]


async def _execute(args: argparse.Namespace) -> dict:
    if os.environ.get(CONFIRM_ENV) != CONFIRM_VALUE:
        raise RuntimeError(
            f"live staging execution requires {CONFIRM_ENV}={CONFIRM_VALUE}"
        )
    campaign = secure_json(args.campaign, label="approved Full Matrix campaign")
    policy = secure_json(args.approver_policy, label="Full Matrix approver policy")
    bound_artifacts = _bound(args.bound_artifact)
    if args.backend_config.resolve() != bound_artifacts["full_matrix_backend_config"].resolve():
        raise RuntimeError(
            "execution backend config must be the campaign-bound full_matrix_backend_config"
        )
    config = secure_json(args.backend_config, label="Full Matrix backend config")
    backend = CommandFullMatrixBackend(
        config=config,
        repo_root=REPO_ROOT,
        artifact_root=args.artifact_root,
        campaign_id=str(campaign.get("campaign_id")),
        release_sha=str(campaign.get("release_sha")),
    )
    return await run_full_matrix_campaign(
        campaign=campaign,
        approver_policy=policy,
        bound_artifacts=bound_artifacts,
        artifact_root=args.artifact_root,
        journal=args.journal,
        backend=backend,
    )


def _verify(args: argparse.Namespace) -> dict:
    campaign = secure_json(args.campaign, label="approved Full Matrix campaign")
    policy = secure_json(args.approver_policy, label="Full Matrix approver policy")
    return verify_complete_matrix(
        campaign=campaign,
        approver_policy=policy,
        bound_artifacts=_bound(args.bound_artifact),
        phase_evidence=_phase_evidence(campaign, args.artifact_root),
        artifact_root=args.artifact_root,
        execution_journal=args.journal,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("execute", "verify"))
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--approver-policy", type=Path, required=True)
    parser.add_argument("--backend-config", type=Path)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--bound-artifact", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "execute":
            if args.backend_config is None:
                raise ValueError("execute requires --backend-config")
            result = asyncio.run(_execute(args))
        else:
            result = _verify(args)
        write_secure_atomic_bytes(
            args.output,
            (json.dumps(result, sort_keys=True, indent=2) + "\n").encode(),
            label="Full Matrix final report",
            mode=0o600,
            max_size=8 * 1024 * 1024,
        )
        print(json.dumps({"status": result["status"], "report_hash": result["report_hash"]}))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
