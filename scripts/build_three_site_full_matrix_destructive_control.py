#!/usr/bin/env python3
"""Build the hash-bound pointer configuration for destructive Matrix actions.

The configuration deliberately retains only owner-only file locations.  It is
disabled, with no provider pointers, for a shared-host campaign; consequently
adding this binding cannot make an ordinary campaign capable of an Arvan power
operation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
import sys
from uuid import UUID


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_atomic_bytes  # noqa: E402


SCHEMA = "three-site-full-matrix-destructive-control-v1"
EXECUTION_CLASSES = {"shared-host-safe", "dedicated-host-destructive"}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")


class DestructiveControlBuildError(RuntimeError):
    """The destructive controller binding cannot be made safe."""


def _owner_path(path: Path, *, directory: bool) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise DestructiveControlBuildError("destructive control path is unsafe")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise DestructiveControlBuildError("destructive control path is unavailable") from exc
    if (
        metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or (directory and not stat.S_ISDIR(metadata.st_mode))
        or (not directory and not stat.S_ISREG(metadata.st_mode))
    ):
        raise DestructiveControlBuildError("destructive control path is not owner-only")
    return path.resolve()


def build_payload(
    *,
    campaign_id: str,
    gate_group_id: str,
    execution_class: str,
    release_sha: str,
    provider_state_file: Path | None = None,
    provider_token_file: Path | None = None,
    audit_root: Path | None = None,
) -> dict[str, object]:
    try:
        campaign = str(UUID(campaign_id))
        group = str(UUID(gate_group_id))
    except ValueError as exc:
        raise DestructiveControlBuildError("destructive control identity is invalid") from exc
    if execution_class not in EXECUTION_CLASSES or SHA40.fullmatch(release_sha) is None:
        raise DestructiveControlBuildError("destructive control release is invalid")
    enabled = execution_class == "dedicated-host-destructive"
    supplied = (provider_state_file, provider_token_file, audit_root)
    if not enabled:
        if any(value is not None for value in supplied):
            raise DestructiveControlBuildError("shared-host control must not carry provider pointers")
        pointers = {"provider_state_file": "", "provider_token_file": "", "audit_root": ""}
    else:
        if any(value is None for value in supplied):
            raise DestructiveControlBuildError("dedicated destructive control pointers are incomplete")
        pointers = {
            "provider_state_file": str(_owner_path(provider_state_file, directory=False)),
            "provider_token_file": str(_owner_path(provider_token_file, directory=False)),
            "audit_root": str(_owner_path(audit_root, directory=True)),
        }
    return {
        "schema": SCHEMA,
        "campaign_id": campaign,
        "gate_group_id": group,
        "execution_class": execution_class,
        "release_sha": release_sha,
        "enabled": enabled,
        **pointers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--gate-group-id", required=True)
    parser.add_argument("--execution-class", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--provider-state-file", type=Path)
    parser.add_argument("--provider-token-file", type=Path)
    parser.add_argument("--audit-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = build_payload(
            campaign_id=args.campaign_id,
            gate_group_id=args.gate_group_id,
            execution_class=args.execution_class,
            release_sha=args.release_sha,
            provider_state_file=args.provider_state_file,
            provider_token_file=args.provider_token_file,
            audit_root=args.audit_root,
        )
        write_secure_atomic_bytes(
            args.output,
            (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            label="Full Matrix destructive control configuration",
            mode=0o600,
            max_size=64 * 1024,
        )
        print(json.dumps({"status": "built", "output": str(args.output)}, sort_keys=True))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
