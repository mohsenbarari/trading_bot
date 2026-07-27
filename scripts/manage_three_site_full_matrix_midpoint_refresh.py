#!/usr/bin/env python3
"""Prepare subjects and assemble the Full Matrix midpoint refresh proof.

This controller-side helper never accepts the reusable Witness session.  The
session must be rotated on Witness before these receipts are requested.  An
atomic host-side rename is visible without a service restart only when the
session's parent directory is bind-mounted; Docker's current single-file bind
pins the old inode and must not be treated as an atomic rotation mechanism.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import canonical_json_bytes  # noqa: E402
from core.secure_file_io import write_secure_new_bytes  # noqa: E402
from core.three_site_full_matrix_campaign import secure_json  # noqa: E402
from core.three_site_full_matrix_midpoint import (  # noqa: E402
    MIDPOINT_ACTIONS,
    assemble_midpoint_bundle,
    midpoint_subjects,
)


class MidpointRefreshHelperError(RuntimeError):
    pass


def _identity(campaign: dict[str, Any], paused: dict[str, Any]) -> tuple[str, str]:
    unsigned = {key: value for key, value in campaign.items() if key != "approvals"}
    campaign_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    pre_pause_head = str(paused.get("pre_pause_journal_head") or "")
    if (
        paused.get("schema") != "three-site-staging-full-matrix-paused-v1"
        or paused.get("status") != "paused"
        or paused.get("campaign_id") != campaign.get("campaign_id")
        or paused.get("campaign_hash") != campaign_hash
        or paused.get("release_sha") != campaign.get("release_sha")
        or paused.get("completed_iteration") != 1
        or paused.get("next_iteration") != 2
        or len(pre_pause_head) != 64
    ):
        raise MidpointRefreshHelperError("campaign/pause identity is invalid")
    return campaign_hash, pre_pause_head


def _write_new_json(path: Path, payload: dict[str, Any], *, label: str) -> None:
    write_secure_new_bytes(
        path,
        json.dumps(payload, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        label=label,
        mode=0o600,
        max_size=4 * 1024 * 1024,
    )


def _subjects(args: argparse.Namespace) -> dict[str, Any]:
    campaign = secure_json(args.campaign, label="Full Matrix campaign")
    paused = secure_json(args.paused, label="Full Matrix paused result")
    campaign_hash, pre_pause_head = _identity(campaign, paused)
    expected = midpoint_subjects(
        campaign=campaign,
        campaign_hash=campaign_hash,
        pre_pause_journal_head=pre_pause_head,
    )
    supplied = paused.get("refresh_subjects")
    if supplied != [
        {"action": action, "subject": expected[action]}
        for action in MIDPOINT_ACTIONS
    ]:
        raise MidpointRefreshHelperError("paused refresh subjects differ")
    outputs: dict[str, str] = {}
    for action in MIDPOINT_ACTIONS:
        path = args.output_directory / f"{action}-subject.json"
        _write_new_json(
            path,
            expected[action],
            label=f"Full Matrix {action} midpoint subject",
        )
        outputs[action] = str(path)
    return {"status": "subjects_prepared", "outputs": outputs}


def _receipts(values: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        action, separator, raw_path = value.partition("=")
        if (
            not separator
            or action not in MIDPOINT_ACTIONS
            or action in result
            or not Path(raw_path).is_absolute()
        ):
            raise MidpointRefreshHelperError(
                "--receipt must be one unique action=/absolute/path per action"
            )
        result[action] = secure_json(
            Path(raw_path), label=f"Full Matrix {action} midpoint receipt"
        )
    if set(result) != set(MIDPOINT_ACTIONS):
        raise MidpointRefreshHelperError("all three midpoint receipts are required")
    return result


def _assemble(args: argparse.Namespace) -> dict[str, Any]:
    campaign = secure_json(args.campaign, label="Full Matrix campaign")
    paused = secure_json(args.paused, label="Full Matrix paused result")
    campaign_hash, pre_pause_head = _identity(campaign, paused)
    bundle = assemble_midpoint_bundle(
        campaign=campaign,
        campaign_hash=campaign_hash,
        pre_pause_journal_head=pre_pause_head,
        receipts=_receipts(args.receipt),
    )
    _write_new_json(
        args.output,
        bundle,
        label="Full Matrix midpoint refresh bundle",
    )
    return {
        "status": "bundle_assembled",
        "output": str(args.output),
        "bundle_sha256": hashlib.sha256(canonical_json_bytes(bundle)).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subjects = subparsers.add_parser("subjects")
    subjects.add_argument("--campaign", type=Path, required=True)
    subjects.add_argument("--paused", type=Path, required=True)
    subjects.add_argument("--output-directory", type=Path, required=True)
    subjects.set_defaults(handler=_subjects)
    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--campaign", type=Path, required=True)
    assemble.add_argument("--paused", type=Path, required=True)
    assemble.add_argument("--receipt", action="append", default=[])
    assemble.add_argument("--output", type=Path, required=True)
    assemble.set_defaults(handler=_assemble)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(args.handler(args), sort_keys=True))
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
