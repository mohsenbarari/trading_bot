#!/usr/bin/env python3
"""Upload one immutable Writer Witness backup with an S3 SigV4 PUT.

The service credential is intentionally write-only. This helper therefore does
not list, read, overwrite deliberately, or delete remote objects.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import sys
from urllib.parse import quote, urlsplit


MAX_BYTES = 64 * 1024 * 1024
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
KEY_RE = re.compile(
    r"^witness/writer-witness-[0-9]{8}T[0-9]{6}Z\.dump\.age$"
)


class UploadError(RuntimeError):
    pass


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise UploadError(f"missing required setting: {name}")
    return value


def signing_key(secret: str, date_stamp: str, region: str) -> bytes:
    key_date = hmac.new(
        f"AWS4{secret}".encode(), date_stamp.encode(), hashlib.sha256
    ).digest()
    key_region = hmac.new(key_date, region.encode(), hashlib.sha256).digest()
    key_service = hmac.new(key_region, b"s3", hashlib.sha256).digest()
    return hmac.new(key_service, b"aws4_request", hashlib.sha256).digest()


def upload(source: Path, object_key: str) -> dict[str, object]:
    endpoint = required_environment("WRITER_WITNESS_S3_ENDPOINT").rstrip("/")
    region = required_environment("WRITER_WITNESS_S3_REGION")
    bucket = required_environment("WRITER_WITNESS_S3_BUCKET")
    access_key = required_environment("WRITER_WITNESS_S3_ACCESS_KEY")
    secret_key = required_environment("WRITER_WITNESS_S3_SECRET_KEY")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in ("", "/")
        or not parsed.hostname.endswith(".your-objectstorage.com")
    ):
        raise UploadError("S3 endpoint is not an approved HTTPS Object Storage origin")
    if region not in {"fsn1", "hel1", "nbg1"}:
        raise UploadError("S3 region is unsupported")
    if not BUCKET_RE.fullmatch(bucket):
        raise UploadError("S3 bucket name is unsafe")
    if not KEY_RE.fullmatch(object_key):
        raise UploadError("S3 object key is outside the immutable witness prefix")
    if not source.is_file() or source.is_symlink():
        raise UploadError("encrypted backup source is missing or unsafe")
    size = source.stat().st_size
    if size < 1 or size > MAX_BYTES:
        raise UploadError("encrypted backup size is outside the allowed range")
    body = source.read_bytes()
    if len(body) != size:
        raise UploadError("encrypted backup changed while it was read")

    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_uri = quote(f"/{bucket}/{object_key}", safe="/-_.~")
    signed = {
        "content-type": "application/octet-stream",
        "host": parsed.netloc,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "x-amz-meta-sha256": payload_hash,
    }
    canonical_headers = "".join(f"{key}:{signed[key]}\n" for key in sorted(signed))
    signed_headers = ";".join(sorted(signed))
    canonical_request = "\n".join(
        (
            "PUT",
            canonical_uri,
            "",
            canonical_headers,
            signed_headers,
            payload_hash,
        )
    )
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        (
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        )
    )
    signature = hmac.new(
        signing_key(secret_key, date_stamp, region),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{scope},"
        f"SignedHeaders={signed_headers},Signature={signature}"
    )
    connection = http.client.HTTPSConnection(
        parsed.hostname,
        parsed.port or 443,
        timeout=90,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(
            "PUT",
            canonical_uri,
            body=body,
            headers={**signed, "authorization": authorization},
        )
        response = connection.getresponse()
        response.read()
    finally:
        connection.close()
    if response.status != 200:
        raise UploadError(f"S3 upload failed with HTTP {response.status}")
    version_id = response.getheader("x-amz-version-id")
    if not version_id:
        raise UploadError("S3 upload succeeded without a version id")
    return {
        "status": "uploaded",
        "object_key": object_key,
        "sha256": payload_hash,
        "bytes": size,
        "version_id": version_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    print(json.dumps(upload(args.file, args.key), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UploadError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
