#!/usr/bin/env python3
"""Provision and verify the immutable off-host Writer Witness S3 bucket.

The script uses only the Python standard library, never prints credentials, and
requires --apply before it creates a billable Hetzner Object Storage bucket.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Iterable
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import uuid
import xml.etree.ElementTree as ET


DEFAULT_ADMIN_ENV = Path(
    "/root/secure-envs/hetzner/witness-object-storage-admin.env"
)
DEFAULT_UPLOADER_ENV = Path(
    "/root/secure-envs/hetzner/witness-object-storage-uploader.env"
)
DEFAULT_BUCKET_ENV = Path(
    "/root/secure-envs/hetzner/witness-object-storage-bucket.env"
)
S3_XML_NAMESPACE = "http://s3.amazonaws.com/doc/2006-03-01/"
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


class ProvisioningError(RuntimeError):
    """A safe, redacted provisioning failure."""


@dataclass(frozen=True)
class Credential:
    access_key: str
    secret_key: str


@dataclass(frozen=True)
class S3Response:
    status: int
    body: bytes
    headers: dict[str, str]

    @property
    def error_code(self) -> str:
        try:
            root = ET.fromstring(self.body)
        except ET.ParseError:
            return "unreadable_response"
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] == "Code" and node.text:
                return node.text
        return "unknown_error"


def read_env(path: Path, *, required: Iterable[str]) -> dict[str, str]:
    if not path.is_file():
        raise ProvisioningError(f"missing credential file: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ProvisioningError(f"credential file must have mode 0600: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ProvisioningError(f"invalid environment line in {path}")
        values[key] = value.strip()
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ProvisioningError(f"missing variables in {path}: {','.join(missing)}")
    return values


def xml_text(root: ET.Element, local_name: str) -> str | None:
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == local_name:
            return node.text
    return None


class SignedS3Client:
    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        credential: Credential,
        timeout_seconds: int = 20,
    ) -> None:
        parsed = urlsplit(endpoint.rstrip("/"))
        if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/"):
            raise ProvisioningError("S3 endpoint must be a bare HTTPS origin")
        self.endpoint = f"https://{parsed.netloc}"
        self.host = parsed.netloc
        self.region = region
        self.credential = credential
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _signing_key(secret: str, date_stamp: str, region: str) -> bytes:
        key_date = hmac.new(
            f"AWS4{secret}".encode(), date_stamp.encode(), hashlib.sha256
        ).digest()
        key_region = hmac.new(key_date, region.encode(), hashlib.sha256).digest()
        key_service = hmac.new(key_region, b"s3", hashlib.sha256).digest()
        return hmac.new(key_service, b"aws4_request", hashlib.sha256).digest()

    def request(
        self,
        method: str,
        path: str = "/",
        *,
        query: Iterable[tuple[str, str]] = (),
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> S3Response:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        canonical_uri = quote(path if path.startswith("/") else f"/{path}", safe="/-_.~")
        query_items = sorted((str(key), str(value)) for key, value in query)
        canonical_query = "&".join(
            f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}"
            for key, value in query_items
        )
        signed = {
            "host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        for key, value in (headers or {}).items():
            signed[key.strip().lower()] = " ".join(str(value).strip().split())
        canonical_headers = "".join(
            f"{key}:{signed[key]}\n" for key in sorted(signed)
        )
        signed_headers = ";".join(sorted(signed))
        canonical_request = "\n".join(
            (
                method.upper(),
                canonical_uri,
                canonical_query,
                canonical_headers,
                signed_headers,
                payload_hash,
            )
        )
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            )
        )
        signature = hmac.new(
            self._signing_key(
                self.credential.secret_key,
                date_stamp,
                self.region,
            ),
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.credential.access_key}/{scope},"
            f"SignedHeaders={signed_headers},Signature={signature}"
        )
        request_headers = {
            **signed,
            "authorization": authorization,
        }
        url = f"{self.endpoint}{canonical_uri}"
        if canonical_query:
            url = f"{url}?{canonical_query}"
        request = Request(
            url,
            data=body if method.upper() in {"POST", "PUT", "PATCH"} else None,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return S3Response(
                    status=response.status,
                    body=response.read(),
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except HTTPError as exc:
            return S3Response(
                status=exc.code,
                body=exc.read(),
                headers={key.lower(): value for key, value in exc.headers.items()},
            )


def content_headers(body: bytes, content_type: str) -> dict[str, str]:
    return {
        "content-md5": base64.b64encode(hashlib.md5(body).digest()).decode("ascii"),
        "content-type": content_type,
    }


def require_status(response: S3Response, expected: set[int], operation: str) -> None:
    if response.status not in expected:
        raise ProvisioningError(
            f"{operation} failed: http={response.status} code={response.error_code}"
        )


def create_bucket(client: SignedS3Client, bucket: str, region: str) -> None:
    body = (
        f'<CreateBucketConfiguration xmlns="{S3_XML_NAMESPACE}">'
        f"<LocationConstraint>{region}</LocationConstraint>"
        "</CreateBucketConfiguration>"
    ).encode()
    response = client.request(
        "PUT",
        f"/{bucket}",
        headers={
            **content_headers(body, "application/xml"),
            "x-amz-acl": "private",
            "x-amz-bucket-object-lock-enabled": "true",
        },
        body=body,
    )
    require_status(response, {200}, "create bucket with object lock")


def configure_object_lock(client: SignedS3Client, bucket: str) -> None:
    body = (
        f'<ObjectLockConfiguration xmlns="{S3_XML_NAMESPACE}">'
        "<ObjectLockEnabled>Enabled</ObjectLockEnabled>"
        "<Rule><DefaultRetention><Mode>COMPLIANCE</Mode><Days>90</Days>"
        "</DefaultRetention></Rule></ObjectLockConfiguration>"
    ).encode()
    response: S3Response | None = None
    for delay_seconds in (0, 2, 5, 10):
        if delay_seconds:
            time.sleep(delay_seconds)
        response = client.request(
            "PUT",
            f"/{bucket}",
            query=(("object-lock", ""),),
            headers=content_headers(body, "application/xml"),
            body=body,
        )
        if response.status != 404 or response.error_code != "NoSuchBucket":
            break
    assert response is not None
    require_status(response, {200}, "configure compliance retention")


def verify_object_lock(client: SignedS3Client, bucket: str) -> None:
    response = client.request(
        "GET", f"/{bucket}", query=(("object-lock", ""),)
    )
    require_status(response, {200}, "read object-lock configuration")
    root = ET.fromstring(response.body)
    if (
        xml_text(root, "ObjectLockEnabled") != "Enabled"
        or xml_text(root, "Mode") != "COMPLIANCE"
        or xml_text(root, "Days") != "90"
    ):
        raise ProvisioningError("object-lock configuration did not persist safely")
    versioning = client.request(
        "GET", f"/{bucket}", query=(("versioning", ""),)
    )
    require_status(versioning, {200}, "read bucket versioning")
    if xml_text(ET.fromstring(versioning.body), "Status") != "Enabled":
        raise ProvisioningError("object lock did not enable bucket versioning")


def configure_uploader_policy(
    client: SignedS3Client,
    *,
    bucket: str,
    project_id: str,
    admin_access_key: str,
    uploader_access_key: str,
) -> None:
    admin_principal = f"arn:aws:iam:::user/p{project_id}:{admin_access_key}"
    uploader_principal = f"arn:aws:iam:::user/p{project_id}:{uploader_access_key}"
    bucket_arn = f"arn:aws:s3:::{bucket}"
    prefix_arn = f"{bucket_arn}/witness/*"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyAllKeysExceptAdminAndUploader",
                "Effect": "Deny",
                "NotPrincipal": {
                    "AWS": [admin_principal, uploader_principal],
                },
                "Action": "s3:*",
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
            },
            {
                "Sid": "AllowWriterWitnessUploads",
                "Effect": "Allow",
                "Principal": {"AWS": uploader_principal},
                "Action": "s3:PutObject",
                "Resource": prefix_arn,
            },
            {
                "Sid": "DenyUploaderReadDeleteAndControlActions",
                "Effect": "Deny",
                "Principal": {"AWS": uploader_principal},
                "Action": [
                    "s3:AbortMultipartUpload",
                    "s3:BypassGovernanceRetention",
                    "s3:CreateBucket",
                    "s3:DeleteBucket",
                    "s3:DeleteBucketPolicy",
                    "s3:DeleteObject",
                    "s3:DeleteObjectVersion",
                    "s3:GetBucketAcl",
                    "s3:GetBucketObjectLockConfiguration",
                    "s3:GetBucketPolicy",
                    "s3:GetBucketVersioning",
                    "s3:GetLifecycleConfiguration",
                    "s3:GetObject",
                    "s3:GetObjectAcl",
                    "s3:GetObjectLegalHold",
                    "s3:GetObjectRetention",
                    "s3:GetObjectVersion",
                    "s3:GetObjectVersionAcl",
                    "s3:ListBucket",
                    "s3:ListBucketMultipartUploads",
                    "s3:ListBucketVersions",
                    "s3:ListMultipartUploadParts",
                    "s3:PutBucketAcl",
                    "s3:PutBucketObjectLockConfiguration",
                    "s3:PutBucketPolicy",
                    "s3:PutBucketVersioning",
                    "s3:PutLifecycleConfiguration",
                    "s3:PutObjectAcl",
                    "s3:PutObjectLegalHold",
                    "s3:PutObjectRetention",
                    "s3:RestoreObject",
                ],
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
            },
            {
                "Sid": "DenyUploaderWritesOutsideWitnessPrefix",
                "Effect": "Deny",
                "Principal": {"AWS": uploader_principal},
                "Action": "s3:PutObject",
                "NotResource": prefix_arn,
            },
        ],
    }
    body = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    response = client.request(
        "PUT",
        f"/{bucket}",
        query=(("policy", ""),),
        headers=content_headers(body, "application/json"),
        body=body,
    )
    require_status(response, {204}, "configure uploader bucket policy")


def verify_private_acl(client: SignedS3Client, bucket: str) -> None:
    response = client.request("GET", f"/{bucket}", query=(("acl", ""),))
    require_status(response, {200}, "read bucket ACL")
    if b"AllUsers" in response.body or b"AuthenticatedUsers" in response.body:
        raise ProvisioningError("bucket ACL is not private")


def verify_uploader_boundary(
    admin: SignedS3Client,
    uploader: SignedS3Client,
    *,
    bucket: str,
) -> dict[str, object]:
    key = f"witness/security-probe/{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.bin"
    outside_key = f"security-probe/{uuid.uuid4().hex[:8]}.bin"
    body = b"writer-witness-object-storage-policy-probe-v1\n"
    list_response = uploader.request("GET", f"/{bucket}")
    if list_response.status != 403:
        raise ProvisioningError(
            f"uploader unexpectedly listed bucket objects: http={list_response.status}"
        )
    outside_upload = uploader.request(
        "PUT",
        f"/{bucket}/{outside_key}",
        headers={"content-type": "application/octet-stream"},
        body=body,
    )
    if outside_upload.status != 403:
        raise ProvisioningError(
            f"uploader unexpectedly wrote outside witness prefix: http={outside_upload.status}"
        )
    upload_response = uploader.request(
        "PUT",
        f"/{bucket}/{key}",
        headers={"content-type": "application/octet-stream"},
        body=body,
    )
    require_status(upload_response, {200}, "uploader policy probe upload")
    version_id = upload_response.headers.get("x-amz-version-id")
    if not version_id:
        raise ProvisioningError("uploaded probe has no S3 version id")
    uploader_get = uploader.request("GET", f"/{bucket}/{key}")
    if uploader_get.status != 403:
        raise ProvisioningError(
            f"uploader unexpectedly read an object: http={uploader_get.status}"
        )
    uploader_delete = uploader.request("DELETE", f"/{bucket}/{key}")
    if uploader_delete.status != 403:
        raise ProvisioningError(
            f"uploader unexpectedly deleted an object: http={uploader_delete.status}"
        )
    admin_get = admin.request("GET", f"/{bucket}/{key}")
    require_status(admin_get, {200}, "admin read policy probe")
    if admin_get.body != body:
        raise ProvisioningError("admin read returned a changed policy probe")
    retention = admin.request(
        "GET", f"/{bucket}/{key}", query=(("retention", ""),)
    )
    require_status(retention, {200}, "read probe retention")
    retention_root = ET.fromstring(retention.body)
    if xml_text(retention_root, "Mode") != "COMPLIANCE":
        raise ProvisioningError("uploaded probe is not compliance locked")
    retain_until = xml_text(retention_root, "RetainUntilDate")
    if not retain_until:
        raise ProvisioningError("uploaded probe has no retention deadline")
    return {
        "key": key,
        "version_id_present": True,
        "retention_mode": "COMPLIANCE",
        "retain_until": retain_until,
        "uploader_list": "denied",
        "uploader_outside_prefix_write": "denied",
        "uploader_read": "denied",
        "uploader_delete": "denied",
    }


def write_bucket_env(path: Path, *, bucket: str, endpoint: str, region: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = (
        f"HETZNER_S3_BUCKET={bucket}\n"
        f"HETZNER_S3_ENDPOINT={endpoint}\n"
        f"HETZNER_S3_REGION={region}\n"
    )
    if path.exists():
        existing = read_env(
            path,
            required=("HETZNER_S3_BUCKET", "HETZNER_S3_ENDPOINT", "HETZNER_S3_REGION"),
        )
        if existing["HETZNER_S3_BUCKET"] != bucket:
            raise ProvisioningError("bucket environment points to a different bucket")
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-env", type=Path, default=DEFAULT_ADMIN_ENV)
    parser.add_argument("--uploader-env", type=Path, default=DEFAULT_UPLOADER_ENV)
    parser.add_argument("--bucket-env", type=Path, default=DEFAULT_BUCKET_ENV)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    admin_values = read_env(
        args.admin_env,
        required=(
            "HETZNER_PROJECT_ID",
            "HETZNER_S3_ACCESS_KEY",
            "HETZNER_S3_SECRET_KEY",
            "HETZNER_S3_ENDPOINT",
            "HETZNER_S3_REGION",
        ),
    )
    uploader_values = read_env(
        args.uploader_env,
        required=(
            "HETZNER_S3_ACCESS_KEY",
            "HETZNER_S3_SECRET_KEY",
            "HETZNER_S3_ENDPOINT",
            "HETZNER_S3_REGION",
        ),
    )
    endpoint = admin_values["HETZNER_S3_ENDPOINT"].rstrip("/")
    region = admin_values["HETZNER_S3_REGION"]
    project_id = admin_values["HETZNER_PROJECT_ID"]
    if (
        endpoint != "https://hel1.your-objectstorage.com"
        or uploader_values["HETZNER_S3_ENDPOINT"].rstrip("/") != endpoint
        or region != "hel1"
        or uploader_values["HETZNER_S3_REGION"] != region
        or not project_id.isdigit()
    ):
        raise ProvisioningError("S3 endpoint, region, or project id is unsafe")
    admin_credential = Credential(
        admin_values["HETZNER_S3_ACCESS_KEY"],
        admin_values["HETZNER_S3_SECRET_KEY"],
    )
    uploader_credential = Credential(
        uploader_values["HETZNER_S3_ACCESS_KEY"],
        uploader_values["HETZNER_S3_SECRET_KEY"],
    )
    if admin_credential == uploader_credential:
        raise ProvisioningError("admin and uploader credentials must be distinct")

    bucket_env_exists = args.bucket_env.exists()
    if bucket_env_exists:
        bucket_values = read_env(
            args.bucket_env,
            required=("HETZNER_S3_BUCKET", "HETZNER_S3_ENDPOINT", "HETZNER_S3_REGION"),
        )
        if (
            bucket_values["HETZNER_S3_ENDPOINT"].rstrip("/") != endpoint
            or bucket_values["HETZNER_S3_REGION"] != region
        ):
            raise ProvisioningError("bucket environment endpoint or region mismatch")
        bucket = bucket_values["HETZNER_S3_BUCKET"]
    else:
        bucket = f"tb-witness-{project_id}-{uuid.uuid4().hex[:10]}"
    if not BUCKET_RE.fullmatch(bucket):
        raise ProvisioningError("generated or configured bucket name is unsafe")
    if not args.apply:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "bucket": bucket,
                    "region": region,
                    "object_lock": "COMPLIANCE:90d",
                    "billable_resource_created": False,
                },
                sort_keys=True,
            )
        )
        return 0

    admin = SignedS3Client(
        endpoint=endpoint,
        region=region,
        credential=admin_credential,
    )
    uploader = SignedS3Client(
        endpoint=endpoint,
        region=region,
        credential=uploader_credential,
    )
    existing = admin.request("GET", "/")
    require_status(existing, {200}, "list existing buckets")
    existing_names: set[str] = set()
    root = ET.fromstring(existing.body)
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "Bucket":
            name = xml_text(node, "Name")
            if name:
                existing_names.add(name)
    if not bucket_env_exists:
        recovery_candidates = sorted(
            name
            for name in existing_names
            if name.startswith(f"tb-witness-{project_id}-")
        )
        if len(recovery_candidates) == 1:
            bucket = recovery_candidates[0]
        elif len(recovery_candidates) > 1:
            raise ProvisioningError(
                "multiple unrecorded writer witness buckets require operator selection"
            )
    if bucket not in existing_names:
        create_bucket(admin, bucket, region)
    configure_object_lock(admin, bucket)
    verify_object_lock(admin, bucket)
    verify_private_acl(admin, bucket)
    configure_uploader_policy(
        admin,
        bucket=bucket,
        project_id=project_id,
        admin_access_key=admin_credential.access_key,
        uploader_access_key=uploader_credential.access_key,
    )
    policy_check = admin.request("GET", f"/{bucket}", query=(("policy", ""),))
    require_status(policy_check, {200}, "read uploader bucket policy")
    probe = verify_uploader_boundary(admin, uploader, bucket=bucket)
    write_bucket_env(args.bucket_env, bucket=bucket, endpoint=endpoint, region=region)
    print(
        json.dumps(
            {
                "status": "configured",
                "bucket": bucket,
                "region": region,
                "private": True,
                "object_lock": "COMPLIANCE:90d",
                "versioning": "Enabled",
                "policy": "uploader-put-only:witness-prefix",
                "probe": probe,
                "secrets_printed": False,
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
