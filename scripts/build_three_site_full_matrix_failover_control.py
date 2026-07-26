#!/usr/bin/env python3
"""Build the secret-free, hash-bound JIT failover control configuration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_bytes, write_secure_atomic_bytes  # noqa: E402
from core.three_site_execution_safety import EXECUTION_CLASSES  # noqa: E402


SCHEMA = "three-site-full-matrix-failover-control-v1"


class FullMatrixFailoverControlBuildError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise FullMatrixFailoverControlBuildError(
                "failover backend config contains a duplicate field"
            )
        value[key] = item
    return value


def _owner_file(path: Path, *, label: str, max_size: int) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise FullMatrixFailoverControlBuildError(f"{label} path is unsafe")
    try:
        read_secure_bytes(path, label=label, max_size=max_size)
    except Exception as exc:
        raise FullMatrixFailoverControlBuildError(f"{label} is unavailable or unsafe") from exc
    return path.resolve()


def _owner_directory(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise FullMatrixFailoverControlBuildError("failover journal root is unsafe")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise FullMatrixFailoverControlBuildError("failover journal root is not owner-only")
    return path.resolve()


def build(
    *,
    campaign_id: str,
    gate_group_id: str,
    execution_class: str,
    release_sha: str,
    backend_config: Path,
    relay_credentials: Path,
    witness_relay_public_key_file: Path,
    journal_root: Path,
) -> dict[str, str]:
    try:
        campaign = str(UUID(campaign_id))
        gate_group = str(UUID(gate_group_id))
    except ValueError as exc:
        raise FullMatrixFailoverControlBuildError("campaign/group identity is invalid") from exc
    if (
        execution_class not in EXECUTION_CLASSES
        or len(release_sha) != 40
        or any(char not in "0123456789abcdef" for char in release_sha)
    ):
        raise FullMatrixFailoverControlBuildError("release or execution class is invalid")
    backend = _owner_file(backend_config, label="failover backend config", max_size=512 * 1024)
    credentials = _owner_file(relay_credentials, label="approval relay credential", max_size=64 * 1024)
    witness_key = _owner_file(
        witness_relay_public_key_file,
        label="Witness relay public key",
        max_size=16 * 1024,
    )
    # The backend is checked again, with inventory/approval evidence, at live
    # execution.  This early check prevents a typo from producing an artifact
    # that can never represent the requested campaign.
    try:
        backend_value = json.loads(
            read_secure_bytes(
                backend,
                label="failover backend config",
                max_size=512 * 1024,
            ),
            object_pairs_hook=_strict_object,
        )
    except Exception as exc:
        raise FullMatrixFailoverControlBuildError("failover backend config JSON is invalid") from exc
    if (
        not isinstance(backend_value, dict)
        or backend_value.get("schema") != "three-site-staging-failover-backend-v1"
        or backend_value.get("campaign_id") != campaign
        or backend_value.get("release_sha") != release_sha
    ):
        raise FullMatrixFailoverControlBuildError("failover backend config differs from campaign")
    return {
        "schema": SCHEMA,
        "campaign_id": campaign,
        "gate_group_id": gate_group,
        "execution_class": execution_class,
        "release_sha": release_sha,
        "backend_config": str(backend),
        "relay_credentials": str(credentials),
        "witness_relay_public_key_file": str(witness_key),
        "journal_root": str(_owner_directory(journal_root)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--gate-group-id", required=True)
    parser.add_argument("--execution-class", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--backend-config", required=True, type=Path)
    parser.add_argument("--relay-credentials", required=True, type=Path)
    parser.add_argument("--witness-relay-public-key-file", required=True, type=Path)
    parser.add_argument("--journal-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        value = build(
            campaign_id=args.campaign_id,
            gate_group_id=args.gate_group_id,
            execution_class=args.execution_class,
            release_sha=args.release_sha,
            backend_config=args.backend_config,
            relay_credentials=args.relay_credentials,
            witness_relay_public_key_file=args.witness_relay_public_key_file,
            journal_root=args.journal_root,
        )
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        write_secure_atomic_bytes(
            args.output,
            raw,
            label="Full Matrix failover control configuration",
            mode=0o600,
            max_size=64 * 1024,
        )
        print(json.dumps({"status": "built", "output": str(args.output)}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error_class": type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
