#!/usr/bin/env python3
"""Publish a complete WA-IR preflight delivery through private Object Storage.

The controller uploads an exact Git bundle, encrypted role materials, and the
small WA-IR agent.  It then publishes a short-lived manifest and writes the
presigned bootstrap values only to an owner-only ephemeral descriptor.  No S3
credential is placed on WA-IR and no payload is sent with SSH/SCP.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any
import uuid

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import boto3

from core.secure_file_io import read_secure_text, sha256_secure_file, write_secure_atomic_bytes
from scripts.wa_ir_object_storage_preflight_agent import (
    ARVAN_OBJECT_STORAGE_HOST,
    FILE_TRANSFER_IDENTITY,
    SCHEMA as AGENT_MANIFEST_SCHEMA,
    SECURE_ROOT_BASE,
    _is_safe_child,
    _validate_object_storage_url,
)


ARVAN_ENDPOINT = f"https://{ARVAN_OBJECT_STORAGE_HOST}"
ARVAN_REGION = "ir-thr-at1"
AGE = "/usr/bin/age"
MAX_MATERIAL_FILE_BYTES = 16 * 1024 * 1024
MAX_RELEASE_BYTES = 2 * 1024 * 1024 * 1024
MAX_URL_TTL_SECONDS = 900
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[0-9a-z]+$")
REQUIRED_ROLE_MATERIALS = (
    ("planned-inventory.json", 0o600),
    ("planned-inventory-approval.json", 0o600),
    ("inventory-signers.json", 0o600),
    ("roles/webapp-ir.compose.yml", 0o640),
    ("roles/webapp-ir.env", 0o600),
    ("secrets/staging-dr-ca.crt", 0o644),
    ("secrets/webapp-ir-dr.crt", 0o644),
    ("secrets/webapp-ir-dr.key", 0o600),
    ("secrets/staging-dr-blob-s3.json", 0o600),
    ("secrets/staging-dr-blob-keyring.json", 0o600),
)
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


class PublicationError(RuntimeError):
    pass


def require_private_versioned_bucket(client, *, bucket: str) -> None:  # noqa: ANN001
    if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
        raise PublicationError("WA-IR delivery requires a versioned bucket")
    acl = client.get_bucket_acl(Bucket=bucket)
    for grant in acl.get("Grants", []):
        grantee = grant.get("Grantee") if isinstance(grant, dict) else None
        uri = str(grantee.get("URI") or "") if isinstance(grantee, dict) else ""
        if uri.endswith("/AllUsers") or uri.endswith("/AuthenticatedUsers"):
            raise PublicationError("WA-IR delivery bucket ACL is public")


def _credentials(path: Path) -> tuple[str, str, str, str]:
    values: dict[str, str] = {}
    try:
        text = read_secure_text(path, label="WA-IR Object Storage credentials", max_size=16_384)
    except Exception as exc:
        raise PublicationError("Object Storage credential file is unavailable or unsafe") from exc
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PublicationError("Object Storage credential file has an invalid line")
        key, value = line.split("=", 1)
        if key in values:
            raise PublicationError("Object Storage credential file has a duplicate key")
        values[key] = value
    required = {
        "ARVAN_S3_ACCESS_KEY",
        "ARVAN_S3_SECRET_KEY",
        "ARVAN_S3_ENDPOINT",
        "ARVAN_S3_REGION",
    }
    if set(values) != required:
        raise PublicationError("Object Storage credential fields are invalid")
    if values["ARVAN_S3_ENDPOINT"].rstrip("/") != ARVAN_ENDPOINT:
        raise PublicationError("Object Storage endpoint is not the approved Arvan endpoint")
    if values["ARVAN_S3_REGION"] != ARVAN_REGION:
        raise PublicationError("Object Storage region is not the approved Tehran region")
    if len(values["ARVAN_S3_ACCESS_KEY"]) < 8 or len(values["ARVAN_S3_SECRET_KEY"]) < 32:
        raise PublicationError("Object Storage credentials are malformed")
    return (
        values["ARVAN_S3_ACCESS_KEY"],
        values["ARVAN_S3_SECRET_KEY"],
        values["ARVAN_S3_ENDPOINT"],
        values["ARVAN_S3_REGION"],
    )


def _client(credentials: tuple[str, str, str, str]):  # noqa: ANN001
    access_key, secret_key, endpoint, region = credentials
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _read_material(path: Path, *, label: str, expected_mode: int) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise PublicationError(f"cannot securely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != expected_mode
            or before.st_size <= 0
            or before.st_size > MAX_MATERIAL_FILE_BYTES
        ):
            raise PublicationError(f"{label} is not one bounded owner-controlled file")
        chunks: list[bytes] = []
        remaining = MAX_MATERIAL_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(payload) > MAX_MATERIAL_FILE_BYTES or any(
            getattr(before, field) != getattr(after, field)
            for field in ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size")
        ):
            raise PublicationError(f"{label} changed while it was read")
        return payload
    finally:
        os.close(descriptor)


def build_role_materials(source: Path, output: Path) -> tuple[str, int]:
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with output.open("xb") as raw:
        with tarfile.open(fileobj=raw, mode="w") as archive:
            for relative, mode in REQUIRED_ROLE_MATERIALS:
                payload = _read_material(
                    source / relative,
                    label=f"WA-IR role material {relative}",
                    expected_mode=mode,
                )
                info = tarfile.TarInfo(relative)
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
    output.chmod(0o600)
    return sha256_secure_file(output, label="WA-IR role materials", max_size=128 * 1024 * 1024)


def create_release_bundle(repo: Path, release_sha: str, output: Path) -> tuple[str, int]:
    if not RELEASE_RE.fullmatch(release_sha):
        raise PublicationError("release SHA must be exactly 40 lowercase hexadecimal characters")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=SAFE_ENV,
    ).stdout.strip()
    if head != release_sha:
        raise PublicationError("release SHA is not the current controller HEAD")
    result = subprocess.run(
        ["git", "-C", str(repo), "bundle", "create", str(output), "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
        env=SAFE_ENV,
    )
    if result.returncode != 0:
        raise PublicationError("failed to create exact release Git bundle")
    output.chmod(0o600)
    verify = subprocess.run(
        ["git", "bundle", "verify", str(output)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        env=SAFE_ENV,
    )
    if verify.returncode != 0:
        raise PublicationError("created release Git bundle failed verification")
    return sha256_secure_file(output, label="WA-IR release bundle", max_size=MAX_RELEASE_BYTES)


def encrypt(source: Path, output: Path, recipient: str) -> tuple[str, int]:
    if not AGE_RECIPIENT_RE.fullmatch(recipient):
        raise PublicationError("WA-IR age recipient is malformed")
    if not Path(AGE).is_file():
        raise PublicationError("age is not installed at /usr/bin/age")
    result = subprocess.run(
        [AGE, "--encrypt", "--recipient", recipient, "--output", str(output), str(source)],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=1800,
        env=SAFE_ENV,
    )
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        raise PublicationError("WA-IR artifact encryption failed closed")
    output.chmod(0o600)
    return sha256_secure_file(output, label="encrypted WA-IR artifact", max_size=MAX_RELEASE_BYTES)


def _hash_regular(path: Path, *, label: str, max_size: int) -> tuple[str, int]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except Exception as exc:
        raise PublicationError(f"{label} is unavailable or unsafe") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size <= 0
            or before.st_size > max_size
        ):
            raise PublicationError(f"{label} is unavailable or unsafe")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_size:
                raise PublicationError(f"{label} exceeds its size bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if any(
            getattr(before, field) != getattr(after, field)
            for field in ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size")
        ):
            raise PublicationError(f"{label} changed while it was hashed")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _upload_and_readback(
    client, *, bucket: str, key: str, source: Path, metadata: dict[str, str]
) -> dict[str, Any]:  # noqa: ANN001
    digest, size = _hash_regular(source, label="WA-IR upload source", max_size=MAX_RELEASE_BYTES)
    with source.open("rb") as body:
        response = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentLength=size,
            ContentType="application/octet-stream",
            Metadata=metadata,
        )
    head = client.head_object(Bucket=bucket, Key=key)
    version_id = str(head.get("VersionId") or response.get("VersionId") or "")
    if (
        not version_id
        or int(head.get("ContentLength") or -1) != size
        or head.get("Metadata") != metadata
    ):
        raise PublicationError("uploaded WA-IR object lacks exact versioned metadata")
    remote = client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
    observed = hashlib.sha256()
    observed_size = 0
    try:
        while True:
            chunk = remote["Body"].read(1024 * 1024)
            if not chunk:
                break
            observed_size += len(chunk)
            if observed_size > MAX_RELEASE_BYTES:
                raise PublicationError("WA-IR object readback exceeded its bound")
            observed.update(chunk)
    finally:
        close = getattr(remote["Body"], "close", None)
        if callable(close):
            close()
    if observed.hexdigest() != digest or observed_size != size:
        raise PublicationError("WA-IR Object Storage readback differs from uploaded bytes")
    return {
        "object_key": key,
        "version_id": version_id,
        "sha256": digest,
        "bytes": size,
    }


def _presigned_get(client, *, bucket: str, obj: dict[str, Any], ttl: int) -> str:  # noqa: ANN001
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": obj["object_key"], "VersionId": obj["version_id"]},
        ExpiresIn=ttl,
    )
    _validate_object_storage_url(url, label="generated presigned GET")
    return url


def _json_file(path: Path, payload: dict[str, Any], *, label: str) -> tuple[str, int]:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    write_secure_atomic_bytes(path, encoded, label=label, max_size=1024 * 1024)
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def confirmation_phrase(release_sha: str, bucket: str, prefix: str) -> str:
    return f"publish-wa-ir-preflight:{release_sha}:{bucket}:{prefix.rstrip('/')}"


def execute(args: argparse.Namespace, *, client=None) -> dict[str, Any]:  # noqa: ANN001
    release_sha = str(args.release_sha)
    prefix = str(args.prefix).strip("/") + "/"
    if not RELEASE_RE.fullmatch(release_sha):
        raise PublicationError("release SHA is invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9./_-]{4,180}/", prefix) or ".." in Path(prefix).parts:
        raise PublicationError("Object Storage prefix is invalid")
    if not 60 <= int(args.url_ttl_seconds) <= MAX_URL_TTL_SECONDS:
        raise PublicationError("presigned URL lifetime must be between 60 and 900 seconds")
    if not _is_safe_child(args.remote_secure_materials_dir, SECURE_ROOT_BASE):
        raise PublicationError("remote secure-material directory is outside the approved root")
    if args.remote_age_identity != FILE_TRANSFER_IDENTITY:
        raise PublicationError("remote age identity differs from the pinned WA-IR identity")
    expected = confirmation_phrase(release_sha, args.bucket, prefix)
    if not args.apply:
        return {
            "status": "planned",
            "release_sha": release_sha,
            "bucket": args.bucket,
            "prefix": prefix,
            "required_confirmation": expected,
            "transport": "private-versioned-object-storage-only",
            "ssh_payload_transfer": False,
        }
    if args.confirm != expected:
        raise PublicationError("WA-IR publication confirmation mismatch")
    if args.output_dir.resolve().is_relative_to(args.repo.resolve()):
        raise PublicationError("publication evidence must be outside the Git repository")
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    if stat.S_IMODE(args.output_dir.stat().st_mode) != 0o700:
        raise PublicationError("publication output directory must be mode 0700")
    if client is None:
        client = _client(_credentials(args.credentials))
    require_private_versioned_bucket(client, bucket=args.bucket)

    recipient = read_secure_text(
        args.recipient, label="WA-IR age recipient", max_size=4096
    ).strip()
    if not AGE_RECIPIENT_RE.fullmatch(recipient):
        raise PublicationError("WA-IR age recipient is malformed")
    publication_id = uuid.uuid4().hex
    object_prefix = f"{prefix}{release_sha}/{publication_id}/"
    with tempfile.TemporaryDirectory(prefix="wa-ir-publication-") as raw:
        work = Path(raw)
        release_plain = work / "release.bundle"
        release_plain_hash, release_plain_size = create_release_bundle(
            args.repo.resolve(), release_sha, release_plain
        )
        materials_plain = work / "role-materials.tar"
        materials_plain_hash, materials_plain_size = build_role_materials(
            args.secure_materials_dir.resolve(), materials_plain
        )
        release_encrypted = work / "release.bundle.age"
        release_cipher_hash, release_cipher_size = encrypt(
            release_plain, release_encrypted, recipient
        )
        materials_encrypted = work / "role-materials.tar.age"
        materials_cipher_hash, materials_cipher_size = encrypt(
            materials_plain, materials_encrypted, recipient
        )
        agent_path = args.repo.resolve() / "scripts/wa_ir_object_storage_preflight_agent.py"
        agent_hash, agent_size = _hash_regular(
            agent_path, label="WA-IR preflight agent", max_size=4 * 1024 * 1024
        )

        release_object = _upload_and_readback(
            client,
            bucket=args.bucket,
            key=f"{object_prefix}{release_cipher_hash}.release.bundle.age",
            source=release_encrypted,
            metadata={"kind": "release", "release-sha": release_sha, "plaintext-sha256": release_plain_hash},
        )
        materials_object = _upload_and_readback(
            client,
            bucket=args.bucket,
            key=f"{object_prefix}{materials_cipher_hash}.role-materials.tar.age",
            source=materials_encrypted,
            metadata={"kind": "role-materials", "release-sha": release_sha, "plaintext-sha256": materials_plain_hash},
        )
        agent_object = _upload_and_readback(
            client,
            bucket=args.bucket,
            key=f"{object_prefix}{agent_hash}.preflight-agent.py",
            source=agent_path,
            metadata={"kind": "preflight-agent", "release-sha": release_sha},
        )

        evidence_key = f"{object_prefix}evidence/webapp-ir-fresh-preflight.json"
        evidence_url = client.generate_presigned_url(
            "put_object",
            Params={"Bucket": args.bucket, "Key": evidence_key, "ContentType": "application/json"},
            ExpiresIn=int(args.url_ttl_seconds),
        )
        _validate_object_storage_url(evidence_url, label="generated evidence PUT")
        manifest = {
            "schema": AGENT_MANIFEST_SCHEMA,
            "role": "webapp-ir",
            "release_sha": release_sha,
            "secure_materials_dir": str(args.remote_secure_materials_dir),
            "release_bundle": {
                "url": _presigned_get(client, bucket=args.bucket, obj=release_object, ttl=int(args.url_ttl_seconds)),
                "sha256": release_plain_hash,
                "bytes": release_plain_size,
                "encrypted": True,
                "ciphertext_sha256": release_cipher_hash,
                "ciphertext_bytes": release_cipher_size,
            },
            "role_materials": {
                "url": _presigned_get(client, bucket=args.bucket, obj=materials_object, ttl=int(args.url_ttl_seconds)),
                "sha256": materials_plain_hash,
                "bytes": materials_plain_size,
                "encrypted": True,
                "ciphertext_sha256": materials_cipher_hash,
                "ciphertext_bytes": materials_cipher_size,
            },
            "preflight_output": f"{str(args.remote_secure_materials_dir).rstrip('/')}/webapp-ir-fresh-preflight.json",
            "age_identity": str(args.remote_age_identity),
            "evidence_upload": {
                "url": evidence_url,
                "method": "PUT",
                "headers": {"content-type": "application/json"},
                "expected_status": [200, 201, 204],
            },
        }
        manifest_path = work / "manifest.json"
        manifest_hash, manifest_size = _json_file(
            manifest_path, manifest, label="ephemeral WA-IR preflight manifest"
        )
        manifest_object = _upload_and_readback(
            client,
            bucket=args.bucket,
            key=f"{object_prefix}{manifest_hash}.manifest.json",
            source=manifest_path,
            metadata={"kind": "preflight-manifest", "release-sha": release_sha},
        )
        descriptor = {
            "schema": "three-site-wa-ir-object-storage-bootstrap-v1",
            "release_sha": release_sha,
            "expires_in_seconds": int(args.url_ttl_seconds),
            "agent": {
                "url": _presigned_get(client, bucket=args.bucket, obj=agent_object, ttl=int(args.url_ttl_seconds)),
                "sha256": agent_hash,
                "bytes": agent_size,
            },
            "manifest": {
                "url": _presigned_get(client, bucket=args.bucket, obj=manifest_object, ttl=int(args.url_ttl_seconds)),
                "sha256": manifest_hash,
                "bytes": manifest_size,
            },
        }
        _json_file(
            args.output_dir / "ephemeral-bootstrap.json",
            descriptor,
            label="ephemeral WA-IR bootstrap descriptor",
        )

    durable = {
        "schema": "three-site-wa-ir-object-storage-publication-v1",
        "status": "published-and-readback-verified",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release_sha": release_sha,
        "bucket": args.bucket,
        "object_prefix": object_prefix,
        "recipient_sha256": hashlib.sha256((recipient + "\n").encode()).hexdigest(),
        "objects": {
            "release": release_object,
            "role_materials": materials_object,
            "agent": agent_object,
            "manifest": manifest_object,
            "evidence": {"object_key": evidence_key},
        },
        "presigned_urls_persisted": False,
        "ssh_payload_transfer": False,
    }
    _json_file(
        args.output_dir / "publication-evidence.json",
        durable,
        label="WA-IR publication evidence",
    )
    return {
        "status": durable["status"],
        "release_sha": release_sha,
        "publication_evidence": str(args.output_dir / "publication-evidence.json"),
        "ephemeral_bootstrap": str(args.output_dir / "ephemeral-bootstrap.json"),
        "object_count": 4,
        "presigned_urls_printed": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--secure-materials-dir", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--recipient", type=Path, required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--remote-secure-materials-dir", type=Path, required=True)
    parser.add_argument(
        "--remote-age-identity",
        type=Path,
        default=Path("/root/secure-envs/trading-bot/wa-ir-object-storage-age-identity.txt"),
    )
    parser.add_argument("--url-ttl-seconds", type=int, default=900)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = execute(args)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
