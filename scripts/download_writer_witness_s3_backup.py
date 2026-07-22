#!/usr/bin/env python3
"""Download the latest encrypted Writer Witness backup with the admin key."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from provision_writer_witness_object_storage import (
    Credential,
    ProvisioningError,
    SignedS3Client,
    read_env,
    require_status,
    xml_text,
)


DEFAULT_ADMIN_ENV = Path("/root/secure-envs/hetzner/witness-object-storage-admin.env")
DEFAULT_BUCKET_ENV = Path("/root/secure-envs/hetzner/witness-object-storage-bucket.env")
KEY_RE = re.compile(
    r"^witness/writer-witness-[0-9]{8}T[0-9]{6}Z\.dump\.age$"
)
MAX_BYTES = 64 * 1024 * 1024


def latest_key(body: bytes) -> tuple[str, int]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ProvisioningError("S3 object listing returned invalid XML") from exc
    candidates: list[tuple[str, int]] = []
    for contents in root.iter():
        if contents.tag.rsplit("}", 1)[-1] != "Contents":
            continue
        values: dict[str, str] = {}
        for child in contents:
            values[child.tag.rsplit("}", 1)[-1]] = child.text or ""
        key = values.get("Key", "")
        size = values.get("Size", "")
        if KEY_RE.fullmatch(key) and size.isdigit():
            candidates.append((key, int(size)))
    if not candidates:
        raise ProvisioningError("no encrypted Writer Witness backup exists in S3")
    key, size = max(candidates)
    if size < 1 or size > MAX_BYTES:
        raise ProvisioningError("latest encrypted S3 backup has an unsafe size")
    return key, size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-env", type=Path, default=DEFAULT_ADMIN_ENV)
    parser.add_argument("--bucket-env", type=Path, default=DEFAULT_BUCKET_ENV)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ProvisioningError("download output already exists")

    admin = read_env(
        args.admin_env,
        required=(
            "HETZNER_S3_ACCESS_KEY",
            "HETZNER_S3_SECRET_KEY",
            "HETZNER_S3_ENDPOINT",
            "HETZNER_S3_REGION",
        ),
    )
    bucket = read_env(
        args.bucket_env,
        required=("HETZNER_S3_BUCKET", "HETZNER_S3_ENDPOINT", "HETZNER_S3_REGION"),
    )
    endpoint = admin["HETZNER_S3_ENDPOINT"].rstrip("/")
    region = admin["HETZNER_S3_REGION"]
    if (
        endpoint != "https://hel1.your-objectstorage.com"
        or bucket["HETZNER_S3_ENDPOINT"].rstrip("/") != endpoint
        or region != "hel1"
        or bucket["HETZNER_S3_REGION"] != region
    ):
        raise ProvisioningError("S3 restore settings failed safety validation")
    client = SignedS3Client(
        endpoint=endpoint,
        region=region,
        credential=Credential(
            admin["HETZNER_S3_ACCESS_KEY"],
            admin["HETZNER_S3_SECRET_KEY"],
        ),
    )
    bucket_name = bucket["HETZNER_S3_BUCKET"]
    listing = client.request(
        "GET",
        f"/{bucket_name}",
        query=(("list-type", "2"), ("prefix", "witness/writer-witness-")),
    )
    require_status(listing, {200}, "list encrypted Writer Witness backups")
    key, expected_size = latest_key(listing.body)
    response = client.request("GET", f"/{bucket_name}/{key}")
    require_status(response, {200}, "download encrypted Writer Witness backup")
    if len(response.body) != expected_size:
        raise ProvisioningError("downloaded S3 backup size does not match listing")
    digest = hashlib.sha256(response.body).hexdigest()
    metadata_digest = response.headers.get("x-amz-meta-sha256")
    if metadata_digest != digest:
        raise ProvisioningError("downloaded S3 backup failed metadata checksum")
    version_id = response.headers.get("x-amz-version-id")
    if not version_id:
        raise ProvisioningError("downloaded S3 backup has no version id")
    retention = client.request(
        "GET",
        f"/{bucket_name}/{key}",
        query=(("retention", ""), ("versionId", version_id)),
    )
    require_status(retention, {200}, "read encrypted backup retention")
    retention_root = ET.fromstring(retention.body)
    retention_mode = xml_text(retention_root, "Mode")
    retain_until = xml_text(retention_root, "RetainUntilDate")
    if retention_mode != "COMPLIANCE" or not retain_until:
        raise ProvisioningError("encrypted S3 backup is not compliance locked")
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(response.body)
    print(
        json.dumps(
            {
                "status": "downloaded",
                "object_key": key,
                "bytes": len(response.body),
                "sha256": digest,
                "version_id_present": True,
                "retention_mode": retention_mode,
                "retain_until": retain_until,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvisioningError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
