#!/usr/bin/env python3
"""Build one immutable host-local production source-snapshot binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping, Sequence

from core.secure_file_io import (
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from scripts.produce_production_shadow_source_snapshot import (
    BINDING_FIELDS,
    BINDING_SCHEMA,
    MODES,
    ROLE_NAMES,
    SOURCE_CONTAINERS,
    SOURCE_IMAGE_REFERENCES,
    SOURCE_PROJECTS,
    VOLUME_SUFFIXES,
    load_binding,
)
from scripts.production_shadow_cutover_controller import (
    CutoverContractError,
    read_root_only_manifest,
)


MAX_JSON_BYTES = 2 * 1024 * 1024
OUTPUT_NAMES = {
    (role, mode): (
        f"source-snapshot-binding-{role.replace('_', '-')}-{mode}.json"
    )
    for role in ROLE_NAMES
    for mode in MODES
}


class SourceSnapshotBindingBuildError(RuntimeError):
    """The source-snapshot binding could not be proven exact."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _assert_private_directory(path: Path) -> None:
    if not path.is_absolute():
        raise SourceSnapshotBindingBuildError(
            "output directory must be absolute"
        )
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise SourceSnapshotBindingBuildError(
            "output directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise SourceSnapshotBindingBuildError(
            "output directory must be a real root-owned mode 0700 directory"
        )


def load_controller(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_absolute():
        raise SourceSnapshotBindingBuildError(
            "controller manifest path must be absolute"
        )
    try:
        document, digest = read_root_only_manifest(
            path,
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        raw = read_secure_bytes(
            path,
            label="production cutover manifest",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except (CutoverContractError, SecureFileError) as exc:
        raise SourceSnapshotBindingBuildError(
            "production cutover manifest is invalid"
        ) from exc
    if raw != _canonical_json(document):
        raise SourceSnapshotBindingBuildError(
            "production cutover manifest is not canonical JSON"
        )
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SourceSnapshotBindingBuildError(
            "production cutover manifest digest changed"
        )
    return document, digest


def build_binding(
    controller: Mapping[str, Any],
    *,
    controller_sha256: str,
    role: str,
    mode: str,
) -> dict[str, Any]:
    if role not in ROLE_NAMES or mode not in MODES:
        raise SourceSnapshotBindingBuildError(
            "source snapshot role or mode is invalid"
        )
    project = SOURCE_PROJECTS[role]
    document = {
        "schema": BINDING_SCHEMA,
        "operation_id": controller["operation_id"],
        "release_sha": controller["release_sha"],
        "legacy_release_sha": controller["legacy_release_sha"],
        "role": role,
        "source_project": project,
        "containers": dict(SOURCE_CONTAINERS),
        "images": {
            **SOURCE_IMAGE_REFERENCES[role],
            "restore_postgres": controller["artifacts"][
                "postgres_image_ref"
            ],
        },
        "volumes": {
            kind: f"{project}_{suffix}"
            for kind, suffix in VOLUME_SUFFIXES.items()
        },
        "controller_manifest_sha256": controller_sha256,
        "approval_sha256": controller["artifacts"][
            "cutover_approval_sha256"
        ],
        "mode": mode,
    }
    if set(document) != BINDING_FIELDS:
        raise SourceSnapshotBindingBuildError(
            "source snapshot binding fields differ"
        )
    return document


def confirmation_phrase(
    operation_id: str,
    role: str,
    mode: str,
    release_sha: str,
) -> str:
    return (
        "build-production-shadow-source-snapshot-binding:"
        f"{operation_id}:{role}:{mode}:{release_sha}"
    )


def _existing_payload(path: Path, expected: bytes) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SourceSnapshotBindingBuildError(
            "cannot inspect existing source snapshot binding"
        ) from exc
    try:
        observed = read_secure_bytes(
            path,
            label="source snapshot binding",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise SourceSnapshotBindingBuildError(
            "existing source snapshot binding is unsafe"
        ) from exc
    if observed != expected:
        raise SourceSnapshotBindingBuildError(
            "refusing to overwrite a different source snapshot binding"
        )
    return True


def publish_binding(path: Path, document: Mapping[str, Any]) -> str:
    payload = _canonical_json(document)
    if _existing_payload(path, payload):
        return "reused"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="source snapshot binding",
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
        observed = read_secure_bytes(
            path,
            label="source snapshot binding",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise SourceSnapshotBindingBuildError(
            "source snapshot binding publication failed closed"
        ) from exc
    if observed != payload:
        raise SourceSnapshotBindingBuildError(
            "published source snapshot binding differs"
        )
    return "created"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-manifest", type=Path, required=True)
    parser.add_argument("--role", choices=ROLE_NAMES, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise SourceSnapshotBindingBuildError(
                "source snapshot binding builder must run as root"
            )
        _assert_private_directory(args.output_directory)
        controller, controller_sha256 = load_controller(
            args.controller_manifest
        )
        document = build_binding(
            controller,
            controller_sha256=controller_sha256,
            role=args.role,
            mode=args.mode,
        )
        output = args.output_directory / OUTPUT_NAMES[(args.role, args.mode)]
        required = confirmation_phrase(
            controller["operation_id"],
            args.role,
            args.mode,
            controller["release_sha"],
        )
        result: dict[str, Any] = {
            "schema": BINDING_SCHEMA,
            "operation_id": controller["operation_id"],
            "release_sha": controller["release_sha"],
            "legacy_release_sha": controller["legacy_release_sha"],
            "role": args.role,
            "mode": args.mode,
            "controller_manifest_sha256": controller_sha256,
            "approval_sha256": controller["artifacts"][
                "cutover_approval_sha256"
            ],
            "binding_sha256": hashlib.sha256(
                _canonical_json(document)
            ).hexdigest(),
            "output": str(output),
            "required_confirmation": required,
            "docker_contacted": False,
            "network_io": False,
            "production_mutated": False,
        }
        if not args.apply:
            if args.confirm is not None:
                raise SourceSnapshotBindingBuildError(
                    "--confirm is valid only with --apply"
                )
            result.update(status="planned", output_mutated=False)
        else:
            if args.confirm != required:
                raise SourceSnapshotBindingBuildError(
                    f"apply requires --confirm {required}"
                )
            result.update(
                status="published",
                output_mutated=True,
                publication=publish_binding(output, document),
            )
            loaded = load_binding(output)
            if loaded.canonical_sha256 != result["binding_sha256"]:
                raise SourceSnapshotBindingBuildError(
                    "source snapshot producer rejected the published binding"
                )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except SourceSnapshotBindingBuildError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "production_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": "source snapshot binding build failed closed",
                    "error_class": "SourceSnapshotBindingBuildError",
                    "production_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
