#!/usr/bin/env python3
"""Locally aggregate four bounded dedicated-host read-only receipts.

This controller-side verifier intentionally has no network, process, cloud,
SSH, Docker, Object Storage, or file-write capability.  It reads only one
canonical manifest and exactly four canonical receipt files, all root-only,
and emits a URL-free aggregate of observations to standard output.  It never
emits a readiness decision.  A later delivery controller must reject this
local aggregation unless it separately binds authenticated host delivery and
provider readback; this tool does not do either of those things.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Sequence


# The verifier may run from an immutable staged release and must not leave
# bytecode residue while importing its local validation modules.
sys.dont_write_bytecode = True


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from core.dedicated_host_preflight_aggregate import (  # noqa: E402
    PREFLIGHT_MANIFEST_BINDING_SCHEMA,
    canonical_json_bytes,
    validate_preflight_aggregate,
)
from core.dedicated_host_preflight_receipt import MAX_RECEIPT_BYTES, parse_preflight_receipt  # noqa: E402
from scripts.dedicated_host_preflight_manifest import (  # noqa: E402
    ROLE_ORDER,
    manifest_sha256,
    parse_manifest_payload,
    validate_manifest,
)


MAX_MANIFEST_BYTES = 64 * 1024
REJECTION_SCHEMA = "three-site-dedicated-host-readonly-preflight-aggregate-rejection-v1"


class DedicatedHostPreflightVerificationError(RuntimeError):
    """The locally supplied manifest or receipts cannot be trusted."""


def _require_root() -> None:
    if os.geteuid() != 0:
        raise DedicatedHostPreflightVerificationError("preflight aggregate verifier must run as root")


def _require_root_controlled_ancestors(path: Path, *, field: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise DedicatedHostPreflightVerificationError(f"{field} ancestor cannot be inspected") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise DedicatedHostPreflightVerificationError(f"{field} ancestor is unsafe")


def _read_root_only_file(path: Path, *, field: str, maximum_bytes: int) -> bytes:
    path = Path(path)
    if not path.is_absolute():
        raise DedicatedHostPreflightVerificationError(f"{field} path must be absolute")
    _require_root_controlled_ancestors(path.parent, field=field)
    try:
        before = path.lstat()
    except OSError as exc:
        raise DedicatedHostPreflightVerificationError(f"{field} cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o077
        or not 1 <= before.st_size <= maximum_bytes
    ):
        raise DedicatedHostPreflightVerificationError(f"{field} is not a bounded root-only regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) & 0o077
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
        ):
            raise DedicatedHostPreflightVerificationError(f"{field} changed while being opened")
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, min(4096, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > maximum_bytes:
                raise DedicatedHostPreflightVerificationError(f"{field} exceeds its size bound")
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino, after.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
            or len(payload) != opened.st_size
        ):
            raise DedicatedHostPreflightVerificationError(f"{field} changed while being read")
        return bytes(payload)
    except DedicatedHostPreflightVerificationError:
        raise
    except OSError as exc:
        raise DedicatedHostPreflightVerificationError(f"{field} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def validated_manifest_binding(manifest: object) -> dict[str, Any]:
    """Project only the fixed safe identity fields from a validated manifest."""

    if not isinstance(manifest, bytes):
        raise DedicatedHostPreflightVerificationError("manifest payload must be canonical bytes")
    try:
        checked = validate_manifest(parse_manifest_payload(manifest))
    except ValueError as exc:
        raise DedicatedHostPreflightVerificationError("manifest payload is invalid") from exc
    return {
        "schema": PREFLIGHT_MANIFEST_BINDING_SCHEMA,
        "status": "validated",
        "campaign_id": checked["campaign_id"],
        "operation_id": checked["operation_id"],
        "release_sha": checked["release_sha"],
        "manifest_sha256": manifest_sha256(checked),
        "roles": [
            {
                "role": host["role"],
                "instance_id": host["instance_id"],
                "public_ipv4": host["public_ip"],
            }
            for host in checked["hosts"]
        ],
    }


def aggregate_files(*, manifest_path: Path, receipt_paths: Sequence[Path]) -> dict[str, Any]:
    """Read exactly four private inputs and bind them into one aggregate."""

    _require_root()
    if len(receipt_paths) != len(ROLE_ORDER):
        raise DedicatedHostPreflightVerificationError("exactly four receipt paths are required")
    manifest_raw = _read_root_only_file(
        manifest_path,
        field="preflight manifest",
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    binding = validated_manifest_binding(manifest_raw)
    try:
        receipts = [
            parse_preflight_receipt(
                _read_root_only_file(
                    path,
                    field="preflight receipt",
                    maximum_bytes=MAX_RECEIPT_BYTES,
                )
            )
            for path in receipt_paths
        ]
        return validate_preflight_aggregate(binding, receipts)
    except ValueError as exc:
        raise DedicatedHostPreflightVerificationError("preflight receipts do not bind the validated manifest") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path, action="append")
    return parser


def _rejection_payload() -> bytes:
    return canonical_json_bytes({"schema": REJECTION_SCHEMA, "status": "rejected"}) + b"\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        aggregate = aggregate_files(manifest_path=args.manifest, receipt_paths=args.receipt)
    except DedicatedHostPreflightVerificationError:
        sys.stdout.buffer.write(_rejection_payload())
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(aggregate) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
