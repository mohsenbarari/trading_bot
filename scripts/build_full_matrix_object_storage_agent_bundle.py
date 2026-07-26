#!/usr/bin/env python3
"""Build one exact, owner-only WA-IR pull-agent installation bundle."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "three-site-full-matrix-object-storage-agent-bundle-v1"
SOURCE_FILES = {
    "object_storage_agent.py": (
        REPO_ROOT / "scripts/full_matrix_live/object_storage_agent.py",
        0o700,
    ),
    "object_storage_protocol.py": (
        REPO_ROOT / "scripts/full_matrix_live/object_storage_protocol.py",
        0o600,
    ),
    "site_agent.py": (
        REPO_ROOT / "scripts/full_matrix_live/site_agent.py",
        0o700,
    ),
    "full-matrix-object-storage-agent.service": (
        REPO_ROOT / "deploy/staging/full-matrix-object-storage-agent.service",
        0o644,
    ),
}


class AgentBundleError(RuntimeError):
    """The agent installation bundle could not be built safely."""


def _read(path: Path, *, label: str, owner_only: bool) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise AgentBundleError(f"{label} path is unsafe")
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & (0o077 if owner_only else 0o022)
        or not 1 <= metadata.st_size <= 1024 * 1024
    ):
        raise AgentBundleError(f"{label} is unsafe")
    return path.read_bytes()


def build(args: argparse.Namespace) -> dict[str, Any]:
    if (
        args.output.exists()
        or args.output.is_symlink()
        or not args.output.is_absolute()
        or re.fullmatch(r"[0-9a-f]{40}", args.release_sha) is None
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{5,190}", args.campaign_id)
        is None
    ):
        raise AgentBundleError("agent bundle identity/output is invalid")
    inputs = {
        "agent-config.json": (_read(args.agent_config, label="agent config", owner_only=True), 0o600),
        "agent-ed25519.pem": (
            _read(args.agent_signing_key, label="agent signing key", owner_only=True),
            0o600,
        ),
        "agent-age-identity.txt": (
            _read(args.agent_age_identity, label="agent age identity", owner_only=True),
            0o600,
        ),
    }
    for name, (path, mode) in SOURCE_FILES.items():
        inputs[name] = (_read(path, label=name, owner_only=False), mode)
    manifest = {
        "schema": SCHEMA,
        "release_sha": args.release_sha,
        "campaign_id": args.campaign_id,
        "files": {
            name: {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "mode": mode,
            }
            for name, (payload, mode) in sorted(inputs.items())
        },
    }
    inputs["manifest.json"] = (
        (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
        0o600,
    )
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with args.output.open("xb") as raw:
        with tarfile.open(fileobj=raw, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            for name, (payload, mode) in sorted(inputs.items()):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = mode
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                info.mtime = 0
                archive.addfile(info, io.BytesIO(payload))
        raw.flush()
        os.fsync(raw.fileno())
    os.chmod(args.output, 0o600)
    payload = args.output.read_bytes()
    return {
        "status": "built",
        "output": str(args.output),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "release_sha": args.release_sha,
        "campaign_id": args.campaign_id,
        "member_count": len(inputs),
        "delete_operation_available": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-config", type=Path, required=True)
    parser.add_argument("--agent-signing-key", type=Path, required=True)
    parser.add_argument("--agent-age-identity", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build(args), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AgentBundleError, OSError, RuntimeError):
        raise SystemExit(1)
