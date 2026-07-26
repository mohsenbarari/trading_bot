#!/usr/bin/env python3
"""Publish one encrypted disposable-host bootstrap through private Arvan S3.

The uploaded object is versioned and read back byte-for-byte.  A short-lived
presigned GET descriptor is written owner-only and is never printed.  The tool
has no delete operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_text, write_secure_atomic_bytes  # noqa: E402
from scripts.publish_wa_ir_object_storage_preflight import (  # noqa: E402
    ARVAN_ENDPOINT,
    ARVAN_REGION,
    _client,
    _hash_regular,
    _presigned_get,
    _upload_and_readback,
    encrypt,
    require_private_versioned_bucket,
)
from scripts.verify_three_site_staging_inventory import PRODUCTION_BUCKETS  # noqa: E402


DEFAULT_CREDENTIALS = Path(
    "/root/secure-envs/trading-bot/"
    "three-site-staging-3138d0c2-dc32d903/secrets/staging-dr-blob-s3.json"
)
DEFAULT_RECIPIENT = Path(
    "/root/secure-envs/arvan/"
    "full-matrix-destructive-20260726.webapp_ir.age-recipient"
)
DEFAULT_DESCRIPTOR = Path(
    "/root/secure-envs/arvan/"
    "full-matrix-destructive-20260726.webapp_ir.bootstrap-descriptor.json"
)
DEFAULT_EVIDENCE = Path(
    "/root/secure-envs/arvan/"
    "full-matrix-destructive-20260726.webapp_ir.bootstrap-publication.json"
)
DEFAULT_BUCKET = "gold-trade-staging-three-site-dr"
DEFAULT_PREFIX = "full-matrix-destructive/20260726/webapp-ir/bootstrap"
MAX_BYTES = 32 * 1024 * 1024


class BootstrapPublicationError(RuntimeError):
    """Encrypted disposable bootstrap publication failed closed."""


def _private_regular(path: Path, *, label: str, max_size: int) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BootstrapPublicationError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or not 1 <= metadata.st_size <= max_size
    ):
        raise BootstrapPublicationError(f"{label} is unsafe")
    return metadata


def _credentials(path: Path) -> tuple[str, str, str, str]:
    _private_regular(path, label="S3 credential file", max_size=32_768)
    try:
        value = json.loads(
            read_secure_text(path, label="S3 credential file", max_size=32_768)
        )
    except (ValueError, TypeError) as exc:
        raise BootstrapPublicationError("S3 credential JSON is invalid") from exc
    if not isinstance(value, dict) or set(value) != {"access_key", "secret_key"}:
        raise BootstrapPublicationError("S3 credential fields are invalid")
    access = str(value["access_key"])
    secret = str(value["secret_key"])
    if len(access) < 8 or len(secret) < 32:
        raise BootstrapPublicationError("S3 credentials are malformed")
    return access, secret, ARVAN_ENDPOINT, ARVAN_REGION


def _recipient(path: Path) -> str:
    _private_regular(path, label="age recipient file", max_size=4096)
    value = read_secure_text(
        path,
        label="age recipient file",
        max_size=4096,
    ).strip()
    if re.fullmatch(r"age1[0-9a-z]{40,80}", value) is None:
        raise BootstrapPublicationError("age recipient is malformed")
    return value


def _write(path: Path, value: dict[str, Any], *, label: str) -> None:
    encoded = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    write_secure_atomic_bytes(
        path,
        encoded,
        label=label,
        max_size=1024 * 1024,
    )
    os.chmod(path, 0o600)


def confirmation(source_hash: str, bucket: str, prefix: str) -> str:
    return f"publish-full-matrix-bootstrap:{source_hash}:{bucket}:{prefix}"


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", args.bucket)
        or args.bucket in PRODUCTION_BUCKETS
    ):
        raise BootstrapPublicationError("bootstrap bucket is invalid or production-owned")
    prefix = str(args.prefix).strip("/")
    if (
        re.fullmatch(r"[a-z0-9][a-z0-9/_.-]{8,180}", prefix) is None
        or ".." in Path(prefix).parts
    ):
        raise BootstrapPublicationError("bootstrap prefix is invalid")
    _private_regular(args.source, label="bootstrap source", max_size=MAX_BYTES)
    source_hash, source_size = _hash_regular(
        args.source,
        label="bootstrap source",
        max_size=MAX_BYTES,
    )
    recipient = _recipient(args.recipient)
    expected = confirmation(source_hash, args.bucket, prefix)
    if not args.apply:
        return {
            "status": "planned",
            "source_sha256": source_hash,
            "source_bytes": source_size,
            "bucket": args.bucket,
            "prefix": prefix,
            "required_confirmation": expected,
            "transport": "private-versioned-object-storage-only",
            "encrypted": True,
            "delete_operation_available": False,
        }
    if args.confirm != expected:
        raise BootstrapPublicationError("bootstrap publication confirmation mismatch")
    client = _client(_credentials(args.credentials))
    require_private_versioned_bucket(client, bucket=args.bucket)
    with tempfile.TemporaryDirectory(
        prefix="full-matrix-destructive-bootstrap-"
    ) as raw:
        encrypted = Path(raw) / "bootstrap.tar.gz.age"
        ciphertext_hash, ciphertext_size = encrypt(
            args.source,
            encrypted,
            recipient,
        )
        key = f"{prefix}/{ciphertext_hash}.tar.gz.age"
        obj = _upload_and_readback(
            client,
            bucket=args.bucket,
            key=key,
            source=encrypted,
            metadata={
                "kind": "full-matrix-destructive-bootstrap",
                "role": "webapp-ir",
                "plaintext-sha256": source_hash,
            },
        )
        url = _presigned_get(
            client,
            bucket=args.bucket,
            obj=obj,
            ttl=int(args.url_ttl_seconds),
        )
    descriptor = {
        "schema": "full-matrix-destructive-bootstrap-descriptor-v1",
        "role": "webapp_ir",
        "artifact": {
            "url": url,
            "plaintext_sha256": source_hash,
            "plaintext_bytes": source_size,
            "ciphertext_sha256": ciphertext_hash,
            "ciphertext_bytes": ciphertext_size,
        },
        "expires_in_seconds": int(args.url_ttl_seconds),
    }
    evidence = {
        "schema": "full-matrix-destructive-bootstrap-publication-v1",
        "role": "webapp_ir",
        "bucket": args.bucket,
        "object_key": obj["object_key"],
        "version_id": obj["version_id"],
        "plaintext_sha256": source_hash,
        "plaintext_bytes": source_size,
        "ciphertext_sha256": ciphertext_hash,
        "ciphertext_bytes": ciphertext_size,
        "private_bucket": True,
        "versioned_object": True,
        "encrypted": True,
        "presigned_url_persisted_in_evidence": False,
        "delete_operation_available": False,
    }
    _write(args.descriptor, descriptor, label="bootstrap presigned descriptor")
    _write(args.evidence, evidence, label="bootstrap publication evidence")
    return {
        "status": "published",
        "bucket": args.bucket,
        "object_key": obj["object_key"],
        "version_id": obj["version_id"],
        "plaintext_sha256": source_hash,
        "ciphertext_sha256": ciphertext_hash,
        "descriptor": str(args.descriptor),
        "evidence": str(args.evidence),
        "presigned_url_printed": False,
        "delete_operation_available": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--recipient", type=Path, default=DEFAULT_RECIPIENT)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--descriptor", type=Path, default=DEFAULT_DESCRIPTOR)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--url-ttl-seconds", type=int, default=900)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if not 300 <= args.url_ttl_seconds <= 900:
        raise BootstrapPublicationError("presigned URL TTL must be 300..900 seconds")
    print(json.dumps(execute(args), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BootstrapPublicationError, OSError, RuntimeError) as exc:
        print(
            json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        raise SystemExit(1)
