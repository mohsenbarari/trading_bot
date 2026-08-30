#!/usr/bin/env python3
"""CAS-bind a fresh two-host backup receipt into a production deploy manifest.

The source manifest is preserved.  Only the two reviewed backup-evidence keys
may change.  This is intentionally separate from the PRIVATE_PRIMARY manifest
preparer so its receipt can continue to prove a pure Legacy-to-Private
derivation.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Sequence

from scripts import prepare_production_private_primary_manifest as manifest_tools


APPROVED_BACKUP_ROOT = Path(
    "/root/secure-envs/trading-bot/production-backups/evidence"
)
CONFIRMATION = "bind-fresh-production-backup-receipt"
RECEIPT_SCHEMA = "production_backup_manifest_binding/1.0"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TARGET_KEYS = (
    "PRODUCTION_BACKUP_RECEIPT_PATH",
    "PRODUCTION_BACKUP_RECEIPT_SHA256",
)


class BackupManifestBindingError(RuntimeError):
    """Stable, value-free refusal."""


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _read_backup_receipt(path: Path, expected_digest: str) -> bytes:
    if not path.is_absolute() or path.parent != APPROVED_BACKUP_ROOT:
        raise BackupManifestBindingError("backup_receipt_scope_invalid")
    try:
        root = APPROVED_BACKUP_ROOT.resolve(strict=True)
        info = root.lstat()
    except OSError as exc:
        raise BackupManifestBindingError("backup_receipt_root_invalid") from exc
    if (
        root != APPROVED_BACKUP_ROOT
        or root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise BackupManifestBindingError("backup_receipt_root_invalid")
    try:
        payload = manifest_tools._read_stable_regular(
            path, label="backup_receipt"
        )
    except manifest_tools.ManifestPreparationError as exc:
        raise BackupManifestBindingError("backup_receipt_security_invalid") from exc
    if _digest(payload) != expected_digest:
        raise BackupManifestBindingError("backup_receipt_cas_mismatch")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupManifestBindingError("backup_receipt_contract_invalid") from exc
    results = document.get("results") if isinstance(document, dict) else None
    if (
        document.get("status") != "ok"
        or document.get("roles") != ["foreign", "iran"]
        or not isinstance(results, list)
        or len(results) != 2
        or {item.get("role") for item in results if isinstance(item, dict)}
        != {"foreign", "iran"}
        or any(
            not isinstance(item, dict)
            or item.get("status") != "ok"
            or (item.get("restore_smoke") or {}).get("status") != "passed"
            for item in results
        )
    ):
        raise BackupManifestBindingError("backup_receipt_contract_invalid")
    return payload


def _render(source: bytes, *, backup_path: Path, backup_digest: str) -> tuple[bytes, list[str]]:
    # Reuse the production manifest's strict schema, syntax, identity and
    # release-safety validation, but do not use its PRIVATE_PRIMARY rendering.
    manifest_tools._parse_and_render(source)
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BackupManifestBindingError("source_manifest_encoding_invalid") from exc
    replacements = {
        TARGET_KEYS[0]: str(backup_path),
        TARGET_KEYS[1]: backup_digest,
    }
    lines = text.splitlines(keepends=True)
    seen: set[str] = set()
    changed: list[str] = []
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, current = line.split("=", 1)
        if key not in replacements:
            continue
        if key in seen:
            raise BackupManifestBindingError("source_manifest_duplicate_key")
        seen.add(key)
        newline = "\r\n" if raw.endswith("\r\n") else "\n" if raw.endswith("\n") else ""
        value = replacements[key]
        if current != value:
            lines[index] = f"{key}={value}{newline}"
            changed.append(key)
    if seen != set(TARGET_KEYS):
        raise BackupManifestBindingError("source_manifest_backup_binding_missing")
    rendered = "".join(lines).encode("utf-8")
    if len(rendered) > manifest_tools.MAXIMUM_MANIFEST_BYTES:
        raise BackupManifestBindingError("rendered_manifest_too_large")
    return rendered, sorted(changed)


def prepare(args: argparse.Namespace) -> dict[str, object]:
    manifest_tools._require_root()
    if args.confirm != CONFIRMATION:
        raise BackupManifestBindingError("confirmation_invalid")
    if not HEX64.fullmatch(args.expected_source_sha256 or ""):
        raise BackupManifestBindingError("expected_source_sha256_invalid")
    if not HEX64.fullmatch(args.expected_backup_receipt_sha256 or ""):
        raise BackupManifestBindingError("expected_backup_receipt_sha256_invalid")
    source = manifest_tools._require_under_approved_root(
        Path(args.source), label="source_manifest"
    )
    output = manifest_tools._require_under_approved_root(
        Path(args.output), label="output_manifest"
    )
    receipt = manifest_tools._require_under_approved_root(
        Path(args.receipt), label="receipt"
    )
    backup = Path(args.backup_receipt)
    if len({source, output, receipt}) != 3:
        raise BackupManifestBindingError("manifest_output_alias")
    with manifest_tools._exclusive_preparation_lock():
        before = manifest_tools._read_secure_file(
            source, label="source_manifest"
        )
        if _digest(before) != args.expected_source_sha256:
            raise BackupManifestBindingError("source_manifest_cas_mismatch")
        _read_backup_receipt(backup, args.expected_backup_receipt_sha256)
        rendered, changed = _render(
            before,
            backup_path=backup,
            backup_digest=args.expected_backup_receipt_sha256,
        )
        if _digest(manifest_tools._read_secure_file(source, label="source_manifest")) != _digest(before):
            raise BackupManifestBindingError("source_manifest_changed_during_prepare")
        result: dict[str, object] = {
            "schema": RECEIPT_SCHEMA,
            "status": "PASS",
            "action": "BIND_FRESH_PRODUCTION_BACKUP_RECEIPT",
            "source_sha256": _digest(before),
            "output_sha256": _digest(rendered),
            "backup_receipt_sha256": args.expected_backup_receipt_sha256,
            "source_path_sha256": _digest(str(source).encode()),
            "output_path_sha256": _digest(str(output).encode()),
            "receipt_path_sha256": _digest(str(receipt).encode()),
            "backup_receipt_path_sha256": _digest(str(backup).encode()),
            "changed_keys": changed,
            "allowed_keys": sorted(TARGET_KEYS),
            "source_preserved_by_tool": True,
            "secrets_disclosed": False,
        }
        receipt_payload = (
            json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        manifest_tools._preflight_atomic_target(
            output, rendered, label="output_manifest"
        )
        manifest_tools._preflight_atomic_target(
            receipt, receipt_payload, label="receipt"
        )
        output_state = manifest_tools._write_atomic_or_verify(
            output, rendered, label="output_manifest"
        )
        if _digest(manifest_tools._read_secure_file(source, label="source_manifest")) != _digest(before):
            raise BackupManifestBindingError("source_manifest_changed_after_output")
        receipt_state = manifest_tools._write_atomic_or_verify(
            receipt, receipt_payload, label="receipt"
        )
    return {
        **result,
        "output_state": output_state,
        "receipt_state": receipt_state,
        "receipt_sha256": _digest(receipt_payload),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--backup-receipt", required=True)
    parser.add_argument("--expected-backup-receipt-sha256", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = prepare(args)
    except (
        OSError,
        BackupManifestBindingError,
        manifest_tools.ManifestPreparationError,
    ) as exc:
        reason = str(exc) if not isinstance(exc, OSError) else "os_error"
        print(json.dumps({
            "schema": RECEIPT_SCHEMA,
            "status": "FAILED",
            "reason_code": reason,
            "secrets_disclosed": False,
        }, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
