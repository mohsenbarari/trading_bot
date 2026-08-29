#!/usr/bin/env python3
"""Rehearse Market Pipeline archive migration on an isolated restore clone.

The live source project is never started, stopped, or composed.  The dump from
an official backup receipt is restored into a labelled unpublished PostgreSQL
container, the exact new image runs ``migrate`` twice, and all clone resources
are removed.  Secret values are never printed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence

if __package__:
    from scripts import backup_market_pipeline_archive as backup
    from scripts.migrate_market_pipeline_archive import (
        MARKET_SCHEMA_TABLE_COUNT,
        MARKET_SCHEMA_VERSION,
        _migration_result,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import backup_market_pipeline_archive as backup
    from scripts.migrate_market_pipeline_archive import (
        MARKET_SCHEMA_TABLE_COUNT,
        MARKET_SCHEMA_VERSION,
        _migration_result,
    )


CONFIRMATION = "rehearse-production-market-pipeline-archive-migration"
RECEIPT_SCHEMA = "market_pipeline_migration_rehearsal/1.0"
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DUMMY_PASSWORD = "rehearsal-isolated-trust-placeholder\n"


class RehearsalError(RuntimeError):
    """Stable, secret-free refusal."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    parent = path.parent
    if not parent.is_absolute() or parent in {Path("/"), Path("/root"), Path("/srv"), Path("/tmp")}:
        raise RehearsalError("receipt_parent_invalid")
    if any(part in str(parent) for part in ("/tmp/", "/var/tmp/", "staging")):
        raise RehearsalError("receipt_parent_forbidden")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RehearsalError("receipt_output_invalid")
    candidate = parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)
    os.chmod(path, 0o600)


def _load_backup_receipt(path: Path) -> dict[str, Any]:
    backup._secure_regular(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        document.get("schema") != backup.RECEIPT_SCHEMA
        or document.get("status") != "PASS"
        or document.get("database_mutated") is not False
        or not isinstance(document.get("backup"), dict)
        or not isinstance(document.get("source"), dict)
        or not isinstance(document.get("restore_smoke"), dict)
        or document["restore_smoke"].get("status") != "PASS"
    ):
        raise RehearsalError("backup_receipt_not_reusable")
    artifact = Path(str(document["backup"]["path"]))
    if not artifact.is_absolute() or artifact.is_symlink() or not artifact.is_file():
        raise RehearsalError("backup_artifact_unavailable")
    if backup.file_digest(artifact) != document["backup"]["sha256"]:
        raise RehearsalError("backup_artifact_digest_mismatch")
    return document


def _run_migrate(container: str, image_id: str, password_file: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            f"container:{container}",
            "--user",
            "10001:10001",
            "-e",
            "MARKET_POSTGRES_HOST=127.0.0.1",
            "-e",
            "MARKET_POSTGRES_USER=restore",
            "-e",
            "MARKET_POSTGRES_DB=restore",
            "-e",
            "MARKET_POSTGRES_PASSWORD_FILE=/run/secrets/market_postgres_password",
            "--mount",
            f"type=bind,source={password_file},target=/run/secrets/market_postgres_password,readonly=true",
            "--tmpfs",
            "/var/lib/market-data/state:uid=10001,gid=10001,mode=0700",
            image_id,
            "migrate",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        reason = ""
        try:
            payload = json.loads((result.stderr or "").strip().splitlines()[-1])
            if isinstance(payload, dict) and payload.get("reason_code"):
                reason = f"_{payload['reason_code']}"
        except (json.JSONDecodeError, IndexError, TypeError):
            reason = ""
        raise RehearsalError(f"migration_pass_failed_rc_{result.returncode}{reason}")
    return _migration_result(result.stdout, second=False)


def rehearse(
    *,
    backup_receipt: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    image_input_signature: str,
) -> dict[str, Any]:
    if not HEX40.fullmatch(release_sha) or not HEX40.fullmatch(release_tree):
        raise RehearsalError("release_identity_invalid")
    if not IMAGE_ID.fullmatch(image_id) or not HEX64.fullmatch(image_input_signature):
        raise RehearsalError("image_identity_invalid")
    backup_document = _load_backup_receipt(backup_receipt)
    artifact = Path(str(backup_document["backup"]["path"]))
    source = backup_document["source"]
    restore_inventory = backup_document["restore_smoke"]
    work = receipt.parent / f".rehearsal-{os.getpid()}"
    work.mkdir(mode=0o700)
    password_file = work / "postgres-password"
    password_file.write_text(DUMMY_PASSWORD, encoding="ascii")
    os.chmod(password_file, 0o440)
    try:
        os.chown(password_file, 10001, 10001)
    except OSError:
        os.chmod(password_file, 0o0444)
    first: dict[str, Any] | None = None
    second: dict[str, Any] | None = None
    after: dict[str, Any] | None = None

    def before_cleanup(container: str) -> None:
        nonlocal first, second, after
        first = _run_migrate(container, image_id, password_file)
        second_raw = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                f"container:{container}",
                "--user",
                "10001:10001",
                "-e",
                "MARKET_POSTGRES_HOST=127.0.0.1",
                "-e",
                "MARKET_POSTGRES_USER=restore",
                "-e",
                "MARKET_POSTGRES_DB=restore",
                "-e",
                "MARKET_POSTGRES_PASSWORD_FILE=/run/secrets/market_postgres_password",
                "--mount",
                f"type=bind,source={password_file},target=/run/secrets/market_postgres_password,readonly=true",
                "--tmpfs",
                "/var/lib/market-data/state:uid=10001,gid=10001,mode=0700",
                image_id,
                "migrate",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if second_raw.returncode:
            raise RehearsalError(f"migration_second_pass_failed_rc_{second_raw.returncode}")
        second = _migration_result(second_raw.stdout, second=True)
        after = backup._database_invariants(
            lambda sql, *, label: backup._restore_query(container, sql)
        )
        if (
            after["fact_count"] != restore_inventory["fact_count"]
            or after["table_count"] != restore_inventory["table_count"]
            or after["table_count"] != MARKET_SCHEMA_TABLE_COUNT
            or after["sequence_values"] != restore_inventory["sequence_values"]
        ):
            raise RehearsalError("rehearsal_inventory_regressed")

    try:
        restore = backup.restore_smoke(
            artifact,
            source,
            before_cleanup=before_cleanup,
        )
    finally:
        for child in work.iterdir():
            child.unlink()
        work.rmdir()
    if first is None or second is None or after is None:
        raise RehearsalError("rehearsal_hook_did_not_run")
    payload = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "created_at_utc": _now(),
        "release_sha": release_sha,
        "release_tree": release_tree,
        "image_id": image_id,
        "image_input_signature": image_input_signature,
        "source_backup_receipt_sha256": sha256(backup_receipt.read_bytes()).hexdigest(),
        "source_backup_release_sha": backup_document.get("release_sha"),
        "first_pass": first,
        "second_pass": second,
        "schema_version": MARKET_SCHEMA_VERSION,
        "table_count": after["table_count"],
        "fact_count": after["fact_count"],
        "source_fact_count": source["fact_count"],
        "restore_smoke": {"status": restore.get("status"), "cleanup_status": restore.get("cleanup_status")},
        "source_database_mutated": False,
        "services_started": False,
        "isolated_clone": True,
        "secrets_disclosed": False,
    }
    _atomic_json(receipt, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-receipt", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--release-tree", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--image-input-signature", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.confirm != CONFIRMATION:
            raise RehearsalError("confirmation_invalid")
        payload = rehearse(
            backup_receipt=args.backup_receipt,
            receipt=args.receipt,
            release_sha=args.release_sha,
            release_tree=args.release_tree,
            image_id=args.image_id,
            image_input_signature=args.image_input_signature,
        )
    except (OSError, RehearsalError, backup.BackupError, json.JSONDecodeError) as exc:
        reason = str(exc) if not isinstance(exc, OSError) else "os_error"
        print(json.dumps({"status": "FAIL", "reason_code": reason, "secrets_disclosed": False}))
        return 2
    print(
        json.dumps(
            {
                "status": "pass",
                "schema": payload["schema"],
                "first_pass": payload["first_pass"]["status"],
                "second_pass": payload["second_pass"]["status"],
                "fact_count": payload["fact_count"],
                "table_count": payload["table_count"],
                "isolated_clone": True,
                "source_database_mutated": False,
                "secrets_disclosed": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
