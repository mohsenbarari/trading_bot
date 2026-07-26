#!/usr/bin/env python3
"""Encrypt, upload, read back, decrypt, and attest one staging source seed."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import boto3
except ModuleNotFoundError:  # Empty-seed target roles do not require the SDK.
    boto3 = None

from core.secure_file_io import read_secure_text, sha256_secure_file
from scripts.render_three_site_staging_role_compose import _atomic_write
from scripts.run_three_site_staging_source_backup import verify_backup_manifest
from scripts.verify_three_site_staging_inventory import (
    _canonical_bytes,
    load_inventory,
    verify_approved_inventory,
)


ARVAN_ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
ARVAN_REGION = "ir-thr-at1"
AGE = "/usr/bin/age"
SOURCE_ROLES = ("bot_fi", "webapp_fi")
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


class SeedPublicationError(RuntimeError):
    pass


def confirmation_phrase(campaign_id: str, source_role: str, backup_hash: str) -> str:
    return f"publish-seed:{campaign_id}:{source_role}:{backup_hash}"


def _safe_private_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(read_secure_text(path, label=label, max_size=16 * 1024))
    except Exception as exc:
        raise SeedPublicationError(f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise SeedPublicationError(f"{label} must contain one JSON object")
    return payload


def _credentials(path: Path) -> tuple[str, str]:
    payload = _safe_private_json(path, label="staging seed S3 credential file")
    if set(payload) != {"access_key", "secret_key"}:
        raise SeedPublicationError("staging seed S3 credential fields are invalid")
    access_key = str(payload["access_key"])
    secret_key = str(payload["secret_key"])
    if len(access_key) < 8 or len(secret_key) < 32:
        raise SeedPublicationError("staging seed S3 credentials are malformed")
    return access_key, secret_key


def _age_material(recipient_path: Path, identity_path: Path) -> tuple[str, str]:
    try:
        recipient = read_secure_text(
            recipient_path, label="staging seed age recipient", max_size=4096
        ).strip()
        identity = read_secure_text(
            identity_path, label="staging seed age identity", max_size=4096
        )
    except Exception as exc:
        raise SeedPublicationError("staging seed age material is unavailable or unsafe") from exc
    if not re.fullmatch(r"age1[0-9a-z]+", recipient):
        raise SeedPublicationError("staging seed age recipient is malformed")
    if "AGE-SECRET-KEY-" not in identity:
        raise SeedPublicationError("staging seed age identity is malformed")
    return recipient, hashlib.sha256((recipient + "\n").encode()).hexdigest()


def _client(*, access_key: str, secret_key: str):  # noqa: ANN001
    if boto3 is None:
        raise SeedPublicationError("boto3 is required for Object Storage seed transfer")
    return boto3.client(
        "s3",
        endpoint_url=ARVAN_ENDPOINT,
        region_name=ARVAN_REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _prepare_output(path: Path, *, repo: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(repo.resolve())
    except ValueError:
        pass
    else:
        raise SeedPublicationError("seed evidence must be stored outside the Git repository")
    if path.exists():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise SeedPublicationError("seed output directory must be absent or empty")
        path.chmod(0o700)
    else:
        path.mkdir(mode=0o700, parents=True)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise SeedPublicationError("seed output directory must be mode 0700")


def _run_age(arguments: list[str], *, timeout: int = 1800) -> None:
    if not Path(AGE).is_file():
        raise SeedPublicationError("age executable is not installed at /usr/bin/age")
    try:
        result = subprocess.run(
            [AGE, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SeedPublicationError("age encryption/decryption command failed closed") from exc
    if result.returncode != 0:
        raise SeedPublicationError("age encryption/decryption command failed closed")


def _streaming_hash(stream, target: Path) -> tuple[str, int]:  # noqa: ANN001
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ARTIFACT_BYTES + 1024 * 1024:
                    raise SeedPublicationError("encrypted seed object exceeds its size bound")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), size


def _publish_one(
    client,
    *,
    bucket: str,
    prefix: str,
    kind: str,
    artifact: dict[str, Any],
    recipient: str,
    identity_path: Path,
    temporary_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = Path(str(artifact["path"]))
    plaintext_hash, plaintext_size = sha256_secure_file(
        source,
        label=f"{kind} source seed",
        max_size=MAX_ARTIFACT_BYTES,
    )
    if plaintext_hash != artifact["sha256"] or plaintext_size != artifact["bytes"]:
        raise SeedPublicationError(f"{kind} source artifact changed after backup verification")
    encrypted = temporary_root / f"{kind}.age"
    _run_age(["--encrypt", "--recipient", recipient, "--output", str(encrypted), str(source)])
    encrypted.chmod(0o600)
    ciphertext_hash, ciphertext_size = sha256_secure_file(
        encrypted,
        label=f"{kind} encrypted seed",
        max_size=MAX_ARTIFACT_BYTES + 1024 * 1024,
    )
    if ciphertext_size <= plaintext_size:
        raise SeedPublicationError("age ciphertext size is inconsistent")
    object_key = f"{prefix}{ciphertext_hash}.age"
    metadata = {
        "plaintext-sha256": plaintext_hash,
        "ciphertext-sha256": ciphertext_hash,
        "artifact-kind": kind,
    }
    with encrypted.open("rb") as body:
        response = client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=body,
            ContentLength=ciphertext_size,
            ContentType="application/octet-stream",
            Metadata=metadata,
        )
    head = client.head_object(Bucket=bucket, Key=object_key)
    version_id = str(head.get("VersionId") or response.get("VersionId") or "")
    if (
        not version_id
        or int(head.get("ContentLength") or -1) != ciphertext_size
        or head.get("Metadata") != metadata
    ):
        raise SeedPublicationError("uploaded seed object lacks exact versioned metadata")
    downloaded = temporary_root / f"{kind}.readback.age"
    remote = client.get_object(Bucket=bucket, Key=object_key, VersionId=version_id)
    readback_hash, readback_size = _streaming_hash(remote["Body"], downloaded)
    close = getattr(remote["Body"], "close", None)
    if callable(close):
        close()
    if readback_hash != ciphertext_hash or readback_size != ciphertext_size:
        raise SeedPublicationError("downloaded seed ciphertext differs from uploaded bytes")
    restored = temporary_root / f"{kind}.readback.plain"
    _run_age(
        ["--decrypt", "--identity", str(identity_path), "--output", str(restored), str(downloaded)]
    )
    restored.chmod(0o600)
    restored_hash, restored_size = sha256_secure_file(
        restored,
        label=f"{kind} decrypted readback",
        max_size=MAX_ARTIFACT_BYTES,
    )
    if restored_hash != plaintext_hash or restored_size != plaintext_size:
        raise SeedPublicationError("decrypted Object Storage readback differs from backup artifact")
    object_evidence = {
        "kind": kind,
        "object_key": object_key,
        "version_id": version_id,
        "plaintext_sha256": plaintext_hash,
        "plaintext_bytes": plaintext_size,
        "ciphertext_sha256": ciphertext_hash,
        "ciphertext_bytes": ciphertext_size,
    }
    return object_evidence, {
        "kind": kind,
        "object_key": object_key,
        "version_id": version_id,
        "ciphertext_sha256": readback_hash,
        "plaintext_sha256": restored_hash,
    }


def build_plan(
    *, source_role: str, backup: dict[str, Any], inventory_result: dict[str, Any]
) -> dict[str, Any]:
    backup_hash = hashlib.sha256(_canonical_bytes(backup)).hexdigest()
    return {
        "status": "planned",
        "campaign_id": inventory_result["campaign_id"],
        "release_sha": inventory_result["release_sha"],
        "source_role": source_role,
        "backup_manifest_sha256": backup_hash,
        "encryption": "age-x25519",
        "required_confirmation": confirmation_phrase(
            inventory_result["campaign_id"], source_role, backup_hash
        ),
    }


def execute(
    args: argparse.Namespace,
    *,
    inventory: dict[str, Any],
    inventory_result: dict[str, Any],
    backup: dict[str, Any],
) -> dict[str, Any]:
    backup_hash = hashlib.sha256(_canonical_bytes(backup)).hexdigest()
    expected_confirmation = confirmation_phrase(
        inventory_result["campaign_id"], args.source_role, backup_hash
    )
    if args.confirm != expected_confirmation:
        raise SeedPublicationError("seed publication confirmation mismatch")
    verify_backup_manifest(
        backup,
        campaign_id=inventory_result["campaign_id"],
        source_role=args.source_role,
        source_release_sha=str(backup["source_release_sha"]),
        target_release_sha=inventory_result["release_sha"],
        verify_files=True,
    )
    repo = args.repo.resolve()
    _prepare_output(args.output_dir, repo=repo)
    access_key, secret_key = _credentials(args.credentials)
    recipient, recipient_fingerprint = _age_material(args.recipient, args.identity)
    client = _client(access_key=access_key, secret_key=secret_key)
    bucket = str(inventory["object_storage"]["bucket"])
    versioning = client.get_bucket_versioning(Bucket=bucket)
    if versioning.get("Status") != "Enabled":
        raise SeedPublicationError("staging seed bucket versioning is not enabled")
    prefix = f"{inventory['object_storage']['prefix']}seed/{args.source_role}/"
    objects: list[dict[str, Any]] = []
    readbacks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="three-site-seed-") as raw_temporary:
        temporary_root = Path(raw_temporary)
        for kind in ("postgres", "uploads", "audit"):
            object_evidence, readback = _publish_one(
                client,
                bucket=bucket,
                prefix=prefix,
                kind=kind,
                artifact=backup["artifacts"][kind],
                recipient=recipient,
                identity_path=args.identity,
                temporary_root=temporary_root,
            )
            objects.append(object_evidence)
            readbacks.append(readback)
    readback_document = {
        "schema": "three-site-staging-seed-readback-v1",
        "campaign_id": inventory_result["campaign_id"],
        "release_sha": inventory_result["release_sha"],
        "source_role": args.source_role,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "objects": readbacks,
    }
    readback_encoded = (
        json.dumps(readback_document, sort_keys=True, indent=2) + "\n"
    ).encode()
    readback_hash = hashlib.sha256(_canonical_bytes(readback_document)).hexdigest()
    manifest = {
        "schema": "three-site-staging-seed-manifest-v1",
        "campaign_id": inventory_result["campaign_id"],
        "release_sha": inventory_result["release_sha"],
        "source_role": args.source_role,
        "bucket": bucket,
        "object_prefix": prefix,
        "encryption": "age-x25519",
        "recipient_fingerprint": recipient_fingerprint,
        "objects": objects,
        "readback_evidence_sha256": readback_hash,
    }
    manifest_encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    _atomic_write(args.output_dir / "readback.json", readback_encoded, mode=0o600)
    _atomic_write(args.output_dir / "seed-manifest.json", manifest_encoded, mode=0o600)
    return {
        "status": "published-and-readback-verified",
        "campaign_id": inventory_result["campaign_id"],
        "source_role": args.source_role,
        "manifest": str(args.output_dir / "seed-manifest.json"),
        "manifest_sha256": hashlib.sha256(_canonical_bytes(manifest)).hexdigest(),
        "readback_evidence_sha256": readback_hash,
        "object_count": len(objects),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-role", choices=SOURCE_ROLES, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--recipient", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        inventory = load_inventory(args.inventory)
        inventory_result = verify_approved_inventory(
            inventory,
            approval=load_inventory(args.inventory_approval),
            approval_policy=load_inventory(args.approval_policy),
            host_destructive=None,
        )
        if inventory_result["inventory_stage"] != "provisioned":
            raise SeedPublicationError("seed publication requires provisioned inventory")
        backup = load_inventory(args.backup_manifest)
        if not args.apply:
            result = build_plan(
                source_role=args.source_role,
                backup=backup,
                inventory_result=inventory_result,
            )
        else:
            result = execute(
                args,
                inventory=inventory,
                inventory_result=inventory_result,
                backup=backup,
            )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
