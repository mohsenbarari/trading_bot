#!/usr/bin/env python3
"""Build one no-replace, role-scoped package for the fresh preflight agent."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
from typing import Any


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.fresh_campaign_secure_io import (
    SecureOutputDirectory,
    prove_exact_git_release,
    read_secure_material_tree,
    read_secure_root_file,
)
from scripts.generate_fresh_three_site_staging_private_material import (
    ROLE_NAMES,
    verify_fresh_private_material_manifest,
)


PACKAGE_SCHEMA = "three-site-fresh-role-package-v1"
CONTROL_FILES = (
    "planned-inventory.json",
    "planned-inventory-approval.json",
    "human-approval-policy.json",
)


class FreshRolePackageError(RuntimeError):
    pass


def _strict_json(payload: bytes, *, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FreshRolePackageError(f"{label} contains duplicate fields")
            result[key] = value
        return result

    try:
        result = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError, FreshRolePackageError):
        raise FreshRolePackageError(f"{label} is not strict JSON") from None
    if not isinstance(result, dict):
        raise FreshRolePackageError(f"{label} must be an object")
    return result


def _control_file(path: Path, *, label: str) -> bytes:
    try:
        return read_secure_root_file(
            path, label=label, expected_mode=0o600, max_size=4 * 1024 * 1024
        )
    except Exception:
        raise FreshRolePackageError(f"{label} is unavailable or unsafe") from None


def _identity(payload: bytes, *, label: str) -> tuple[str, str, str]:
    value = _strict_json(payload, label=label)
    try:
        return (
            str(value["campaign_id"]), str(value["deployment_id"]),
            str(value["release_sha"]),
        )
    except KeyError:
        raise FreshRolePackageError(f"{label} lacks campaign identity") from None


def build_role_package(
    *,
    material_root: Path,
    planned_inventory: Path,
    approval: Path,
    approval_policy: Path,
    role: str,
    output: Path,
) -> dict[str, Any]:
    if role not in ROLE_NAMES:
        raise FreshRolePackageError("role is invalid")
    material = verify_fresh_private_material_manifest(material_root)
    tree = read_secure_material_tree(material_root)
    control_payloads = {
        CONTROL_FILES[0]: _control_file(planned_inventory, label="planned inventory"),
        CONTROL_FILES[1]: _control_file(approval, label="planned inventory approval"),
        CONTROL_FILES[2]: _control_file(approval_policy, label="human approval policy"),
    }
    identity = (
        str(material["campaign_id"]), str(material["deployment_id"]),
        str(material["release_sha"]),
    )
    exact_release = prove_exact_git_release(
        repo_root=REPO_ROOT,
        release_sha=identity[2],
        bound_files=(
            Path(__file__).resolve(),
            (REPO_ROOT / "scripts/fresh_campaign_secure_io.py").resolve(),
            (REPO_ROOT / "scripts/generate_fresh_three_site_staging_private_material.py").resolve(),
        ),
    )
    if any(_identity(payload, label=name) != identity for name, payload in control_payloads.items()):
        raise FreshRolePackageError("control material identity differs from fresh material")
    role_files = material["role_files"][role]
    if not isinstance(role_files, list) or not role_files:
        raise FreshRolePackageError("role file closure is invalid")
    payloads = {name: tree[name] for name in role_files}
    if any(name not in tree for name in role_files):
        raise FreshRolePackageError("role file closure is incomplete")
    files: dict[str, dict[str, Any]] = {}
    contents: dict[str, tuple[bytes, int]] = {**payloads}
    contents.update((name, (value, 0o600)) for name, value in control_payloads.items())
    for name, (payload, mode) in contents.items():
        files[name] = {
            "sha256": hashlib.sha256(payload).hexdigest(), "mode": mode,
            "bytes": len(payload),
        }
    package_manifest = {
        "schema": PACKAGE_SCHEMA, "role": role, "campaign_id": identity[0],
        "deployment_id": identity[1], "release_sha": identity[2], "files": files,
    }
    manifest_bytes = json.dumps(package_manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, (payload, mode) in sorted(contents.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.uid, member.gid, member.mtime = len(payload), mode, 0, 0, 0
            archive.addfile(member, io.BytesIO(payload))
        member = tarfile.TarInfo("role-package-manifest.json")
        member.size, member.mode, member.uid, member.gid, member.mtime = len(manifest_bytes), 0o600, 0, 0, 0
        archive.addfile(member, io.BytesIO(manifest_bytes))
    package = stream.getvalue()
    if not package or len(package) > 128 * 1024 * 1024:
        raise FreshRolePackageError("role package size is invalid")
    try:
        with SecureOutputDirectory(output) as transaction:
            transaction.write("role-package.tar", package, mode=0o600)
            transaction.write("role-package-manifest.json", manifest_bytes, mode=0o600)
            transaction.publish(before_publish=exact_release.recheck)
    except Exception:
        raise FreshRolePackageError("role package output is unavailable") from None
    return {
        "status": "fresh-role-package-created", "role": role,
        "campaign_id": identity[0], "deployment_id": identity[1], "release_sha": identity[2],
        "package_sha256": hashlib.sha256(package).hexdigest(), "package_bytes": len(package),
        "secret_values_printed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--material-root", type=Path, required=True)
    parser.add_argument("--planned-inventory", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--role", choices=sorted(ROLE_NAMES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_role_package(**vars(args))
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
