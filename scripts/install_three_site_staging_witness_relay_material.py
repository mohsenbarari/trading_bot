#!/usr/bin/env python3
"""Install verified Witness relay material into a new inert directory only.

This helper never writes to /etc, /run, /srv, a ``current`` path, Docker, or a
service manager.  The destination is always ``INERT_ROOT/REVISION_ID`` and must
not already exist.  Installation is not activation and is not image evidence.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from core.secure_file_io import write_secure_new_bytes
from scripts.build_three_site_staging_witness_relay_material import (
    ACTIVE_DIRECTORY_NAME,
    ARCHIVE_DIRECTORY_NAME,
    COMPOSE_NAME,
    ENV_NAME,
    FINAL_FILE_MODES,
    FINAL_SCHEMA,
    MANIFEST_NAME,
    JOURNAL_DIRECTORY_NAME,
    POLICY_NAME,
    PREPARED_FILE_MODES,
    PREPARED_MANIFEST_NAME,
    PREPARED_SCHEMA,
    SESSION_NAME,
    WitnessRelayMaterialError,
    _read_final_directory,
    _read_prepared_directory,
    assert_root_controlled_ancestors,
    read_exact_json_file,
    read_exact_material_file,
    verify_final_structure,
    verify_prepared_structure,
)
from scripts.render_three_site_staging_role_compose import parse_env_values


FORBIDDEN_ROOTS = (
    Path("/boot"),
    Path("/dev"),
    Path("/etc"),
    Path("/proc"),
    Path("/run"),
    Path("/srv"),
    Path("/sys"),
    Path("/usr"),
    Path("/var/lib"),
    Path("/var/run"),
)


class InertRelayInstallError(WitnessRelayMaterialError):
    """A relay bundle cannot be installed without touching live state."""


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _assert_inert_root(root: Path) -> Path:
    if not root.is_absolute() or ".." in root.parts:
        raise InertRelayInstallError("inert root must be an absolute normalized path")
    if "current" in root.parts:
        raise InertRelayInstallError("inert root must not be a current path")
    if any(_is_within(root, forbidden) for forbidden in FORBIDDEN_ROOTS):
        raise InertRelayInstallError("inert root overlaps a forbidden live-system path")
    if root.name != "material-revisions":
        raise InertRelayInstallError(
            "inert root must be a dedicated material-revisions directory"
        )
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise InertRelayInstallError("inert root must already exist") from exc
    if resolved != root:
        raise InertRelayInstallError("inert root must not traverse a symlink")
    assert_root_controlled_ancestors(root, label="inert root")
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or os.geteuid() != 0
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise InertRelayInstallError(
            "inert root must be an owner-controlled mode-0700 directory"
        )
    return root


def _assert_source_directory(directory: Path) -> None:
    if not directory.is_absolute() or ".." in directory.parts:
        raise InertRelayInstallError(
            "relay bundle source must be an absolute normalized path"
        )
    try:
        resolved = directory.resolve(strict=True)
    except OSError as exc:
        raise InertRelayInstallError("relay bundle source is unavailable") from exc
    if resolved != directory:
        raise InertRelayInstallError("relay bundle source must not traverse a symlink")
    assert_root_controlled_ancestors(directory, label="relay bundle source")
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise InertRelayInstallError(
            "relay bundle source must be an owner-controlled mode-0700 directory"
        )


def _copy_new_directory(
    *,
    destination: Path,
    files: dict[str, tuple[bytes, int]],
) -> None:
    if destination.exists() or destination.is_symlink():
        raise InertRelayInstallError(
            "inert revision already exists; choose a new revision ID"
        )
    destination.mkdir(mode=0o700)
    published: list[Path] = []
    created_directories: list[Path] = []
    try:
        for name in (
            ACTIVE_DIRECTORY_NAME,
            ARCHIVE_DIRECTORY_NAME,
            JOURNAL_DIRECTORY_NAME,
        ):
            child = destination / name
            child.mkdir(mode=0o700)
            created_directories.append(child)
        for name, (payload, mode) in files.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or len(relative.parts) > 2:
                raise InertRelayInstallError("inert relay relative path is unsafe")
            target = destination / relative
            write_secure_new_bytes(
                target,
                payload,
                label=f"inert Witness relay material {name}",
                mode=mode,
            )
            published.append(target)
        expected_top_level = {
            ACTIVE_DIRECTORY_NAME,
            ARCHIVE_DIRECTORY_NAME,
            JOURNAL_DIRECTORY_NAME,
            *{
                Path(name).parts[0]
                for name in files
                if len(Path(name).parts) == 1
            },
        }
        if set(path.name for path in destination.iterdir()) != expected_top_level:
            raise InertRelayInstallError("inert relay installation file set changed")
        expected_active = {
            Path(name).name
            for name in files
            if Path(name).parent == Path(ACTIVE_DIRECTORY_NAME)
        }
        if {
            path.name for path in (destination / ACTIVE_DIRECTORY_NAME).iterdir()
        } != expected_active:
            raise InertRelayInstallError("inert relay active file set changed")
        for name in (ARCHIVE_DIRECTORY_NAME, JOURNAL_DIRECTORY_NAME):
            if list((destination / name).iterdir()):
                raise InertRelayInstallError("inert relay control directory is not empty")
        for name, (payload, mode) in files.items():
            target = destination / Path(name)
            metadata = target.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != mode
                or target.read_bytes() != payload
            ):
                raise InertRelayInstallError(
                    "inert relay installation failed its read-back"
                )
    except Exception:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        for path in reversed(created_directories):
            path.rmdir()
        destination.rmdir()
        raise


def install_inert_bundle(
    *,
    canonical_compose: dict[str, Any],
    base_compose_bytes: bytes,
    base_env_bytes: bytes,
    inventory: dict[str, Any],
    approval_policy: dict[str, Any],
    bundle_directory: Path,
    inert_root: Path,
) -> dict[str, Any]:
    """Structurally validate and create one non-live revision directory."""

    _assert_source_directory(bundle_directory)
    root = _assert_inert_root(inert_root)
    root_identity = (root.lstat().st_dev, root.lstat().st_ino)
    manifest_path = bundle_directory / MANIFEST_NAME
    try:
        schema = json.loads(manifest_path.read_text(encoding="utf-8")).get("schema")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise InertRelayInstallError("relay bundle manifest is unavailable") from exc

    if schema == PREPARED_SCHEMA:
        raise InertRelayInstallError(
            "prepared relay bundles are controller-only and cannot be installed"
        )
    if schema == FINAL_SCHEMA:
        (
            compose,
            env,
            prepared_manifest_bytes,
            prepared_manifest,
            final_manifest_bytes,
            final_manifest,
            policy_bytes,
            policy,
            session_bytes,
            session,
        ) = _read_final_directory(bundle_directory)
        if policy != approval_policy:
            raise InertRelayInstallError(
                "final bundle policy differs from the supplied campaign policy"
            )
        validation = verify_final_structure(
            canonical_compose=canonical_compose,
            base_compose_bytes=base_compose_bytes,
            base_env_bytes=base_env_bytes,
            final_compose_bytes=compose,
            final_env_bytes=env,
            prepared_manifest=prepared_manifest,
            prepared_manifest_bytes=prepared_manifest_bytes,
            final_manifest=final_manifest,
            inventory=inventory,
            policy=policy,
            policy_bytes=policy_bytes,
            session=session,
            session_bytes=session_bytes,
        )
        files = {
            COMPOSE_NAME: (compose, FINAL_FILE_MODES[COMPOSE_NAME]),
            ENV_NAME: (env, FINAL_FILE_MODES[ENV_NAME]),
            f"{ACTIVE_DIRECTORY_NAME}/{SESSION_NAME}": (
                session_bytes,
                FINAL_FILE_MODES[f"{ACTIVE_DIRECTORY_NAME}/{SESSION_NAME}"],
            ),
            f"{ACTIVE_DIRECTORY_NAME}/{POLICY_NAME}": (
                policy_bytes,
                FINAL_FILE_MODES[f"{ACTIVE_DIRECTORY_NAME}/{POLICY_NAME}"],
            ),
            PREPARED_MANIFEST_NAME: (
                prepared_manifest_bytes,
                FINAL_FILE_MODES[PREPARED_MANIFEST_NAME],
            ),
            MANIFEST_NAME: (
                final_manifest_bytes,
                FINAL_FILE_MODES[MANIFEST_NAME],
            ),
        }
    else:
        raise InertRelayInstallError("relay bundle manifest schema is invalid")

    revision_id = str(validation["revision_id"])
    destination = root / revision_id
    if destination.parent != root or "current" in destination.parts:
        raise InertRelayInstallError("inert revision destination is unsafe")
    if (
        root.parent.name != str(inventory.get("deployment_id", ""))
        or root.parent.parent.name != str(inventory.get("campaign_id", ""))
    ):
        raise InertRelayInstallError(
            "inert material root is not campaign/deployment-bound"
        )
    try:
        environment = parse_env_values(env.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise InertRelayInstallError("Witness environment is not UTF-8") from exc
    if environment.get("STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR") != str(
        destination / ACTIVE_DIRECTORY_NAME
    ):
        raise InertRelayInstallError(
            "Witness environment material directory differs from install destination"
        )
    rechecked_root = _assert_inert_root(inert_root)
    if (
        rechecked_root != root
        or (rechecked_root.lstat().st_dev, rechecked_root.lstat().st_ino)
        != root_identity
    ):
        raise InertRelayInstallError("inert root changed during bundle validation")
    _copy_new_directory(destination=destination, files=files)
    return {
        "status": "installed-inert-not-activated",
        "stage": validation["stage"],
        "revision_id": revision_id,
        "destination": str(destination),
        "file_count": len(files),
        "validation_mode": "structural-no-file-or-image-attestation",
        "file_attestation": False,
        "image_attestation": False,
        "current_changed": False,
        "service_changed": False,
        "activation": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument("--base-witness-compose", type=Path, required=True)
    parser.add_argument("--base-witness-env", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--bundle-directory", type=Path, required=True)
    parser.add_argument("--inert-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        canonical = yaml.safe_load(
            args.canonical_compose.read_text(encoding="utf-8")
        )
        if not isinstance(canonical, dict):
            raise InertRelayInstallError("canonical Compose is invalid")
        result = install_inert_bundle(
            canonical_compose=canonical,
            base_compose_bytes=read_exact_material_file(
                args.base_witness_compose,
                expected_mode=0o640,
                label="base Witness Compose",
            ),
            base_env_bytes=read_exact_material_file(
                args.base_witness_env,
                expected_mode=0o600,
                label="base Witness environment",
            ),
            inventory=read_exact_json_file(
                args.inventory, label="provisioned inventory"
            ),
            approval_policy=read_exact_json_file(
                args.approval_policy, label="approval policy"
            ),
            bundle_directory=args.bundle_directory,
            inert_root=args.inert_root,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
