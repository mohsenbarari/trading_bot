#!/usr/bin/env python3
"""Harden and prove the pinned Stage 3 Arvan Object Storage boundary.

The operation is intentionally narrow: it cannot create or delete buckets or
objects, accepts only the dedicated staging bucket and one campaign prefix,
and requires an exact confirmation phrase before mutation.  The readback probe
is encrypted client-side with an ephemeral AES-256-GCM key that is never
persisted or printed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
import uuid

from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_atomic_bytes
from scripts.publish_wa_ir_object_storage_preflight import _client, _credentials


STAGING_BUCKET = "gold-trade-staging-three-site-dr"
PREFIX_RE = re.compile(
    r"^staging/([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/$"
)
PUBLIC_GRANTEE_SUFFIXES = ("/AllUsers", "/AuthenticatedUsers")
PROBE_BYTES = 4096
MAX_READBACK_BYTES = 64 * 1024


class Stage3ObjectStorageError(RuntimeError):
    """A redacted, fail-closed Stage 3 Object Storage error."""


def confirmation_phrase(bucket: str, prefix: str) -> str:
    return f"harden-stage3-object-storage:{bucket}:{prefix.rstrip('/')}"


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code") or "")


def _optional_configuration(call, *, missing_codes: set[str]) -> dict[str, Any]:  # noqa: ANN001
    try:
        return call()
    except ClientError as exc:
        if _error_code(exc) in missing_codes:
            return {}
        raise


def _acl_is_private(acl: dict[str, Any]) -> bool:
    for grant in acl.get("Grants", []):
        grantee = grant.get("Grantee") if isinstance(grant, dict) else None
        uri = str(grantee.get("URI") or "") if isinstance(grantee, dict) else ""
        if any(uri.endswith(suffix) for suffix in PUBLIC_GRANTEE_SUFFIXES):
            return False
    return True


def _public_access_block_is_strict(configuration: dict[str, Any]) -> bool:
    return all(
        configuration.get(name) is True
        for name in (
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        )
    )


def _encryption_is_aes256(configuration: dict[str, Any]) -> bool:
    rules = configuration.get("Rules") or []
    return any(
        isinstance(rule, dict)
        and isinstance(rule.get("ApplyServerSideEncryptionByDefault"), dict)
        and rule["ApplyServerSideEncryptionByDefault"].get("SSEAlgorithm") == "AES256"
        for rule in rules
    )


def _lifecycle_rule(campaign_id: str, prefix: str) -> dict[str, Any]:
    return {
        "ID": f"three-site-stage3-{campaign_id}-retention",
        "Filter": {"Prefix": prefix},
        "Status": "Enabled",
        "Expiration": {"Days": 45},
        "NoncurrentVersionExpiration": {"NoncurrentDays": 14},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
    }


def _matching_rule(rules: list[dict[str, Any]], expected: dict[str, Any]) -> bool:
    return any(rule == expected for rule in rules)


def audit(client, *, bucket: str, lifecycle_rule: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN001
    client.head_bucket(Bucket=bucket)
    versioning = client.get_bucket_versioning(Bucket=bucket)
    acl = client.get_bucket_acl(Bucket=bucket)
    encryption = _optional_configuration(
        lambda: client.get_bucket_encryption(Bucket=bucket),
        missing_codes={"ServerSideEncryptionConfigurationNotFoundError"},
    ).get("ServerSideEncryptionConfiguration", {})
    lifecycle = _optional_configuration(
        lambda: client.get_bucket_lifecycle_configuration(Bucket=bucket),
        missing_codes={"NoSuchLifecycleConfiguration"},
    )
    public_access_block = _optional_configuration(
        lambda: client.get_public_access_block(Bucket=bucket),
        missing_codes={"NoSuchPublicAccessBlockConfiguration"},
    ).get("PublicAccessBlockConfiguration", {})
    policy_status = client.get_bucket_policy_status(Bucket=bucket).get("PolicyStatus") or {}
    rules = lifecycle.get("Rules") or []
    return {
        "bucket_exists": True,
        "versioning_enabled": versioning.get("Status") == "Enabled",
        "private_acl": _acl_is_private(acl),
        "bucket_policy_public": policy_status.get("IsPublic") is True,
        "server_default_encryption_configured": bool(encryption),
        "default_encryption_aes256": _encryption_is_aes256(encryption),
        "public_access_block_configured": bool(public_access_block),
        "strict_public_access_block": _public_access_block_is_strict(public_access_block),
        "campaign_lifecycle_exact": _matching_rule(rules, lifecycle_rule),
        "lifecycle_rule_count": len(rules),
    }


def _merge_lifecycle_rule(
    client, *, bucket: str, expected: dict[str, Any]
) -> list[dict[str, Any]]:  # noqa: ANN001
    current = _optional_configuration(
        lambda: client.get_bucket_lifecycle_configuration(Bucket=bucket),
        missing_codes={"NoSuchLifecycleConfiguration"},
    )
    rules = list(current.get("Rules") or [])
    for rule in rules:
        if rule.get("ID") == expected["ID"] and rule != expected:
            raise Stage3ObjectStorageError("campaign lifecycle rule ID has conflicting content")
    if not _matching_rule(rules, expected):
        rules.append(expected)
    return rules


def _read_body(body) -> bytes:  # noqa: ANN001
    payload = bytearray()
    try:
        while True:
            chunk = body.read(8192)
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > MAX_READBACK_BYTES:
                raise Stage3ObjectStorageError("encrypted probe readback exceeded its bound")
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    return bytes(payload)


def _encrypted_roundtrip(client, *, bucket: str, prefix: str, campaign_id: str) -> dict[str, Any]:  # noqa: ANN001
    plaintext = os.urandom(PROBE_BYTES)
    plaintext_hash = hashlib.sha256(plaintext).hexdigest()
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    aad = f"three-site-stage3:{campaign_id}".encode()
    ciphertext = nonce + AESGCM(key).encrypt(nonce, plaintext, aad)
    ciphertext_hash = hashlib.sha256(ciphertext).hexdigest()
    object_key = f"{prefix}readiness/{uuid.uuid4().hex}.aes256gcm"
    metadata = {
        "schema": "three-site-stage3-encrypted-readback-v1",
        "campaign-id": campaign_id,
        "client-side-encryption": "aes-256-gcm",
        "plaintext-sha256": plaintext_hash,
        "ciphertext-sha256": ciphertext_hash,
    }
    response = client.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=ciphertext,
        ContentLength=len(ciphertext),
        ContentType="application/octet-stream",
        Metadata=metadata,
    )
    head = client.head_object(Bucket=bucket, Key=object_key)
    version_id = str(head.get("VersionId") or response.get("VersionId") or "")
    if (
        not version_id
        or int(head.get("ContentLength") or -1) != len(ciphertext)
        or head.get("Metadata") != metadata
    ):
        raise Stage3ObjectStorageError("encrypted probe lacks exact versioned metadata")
    remote = client.get_object(Bucket=bucket, Key=object_key, VersionId=version_id)
    observed = _read_body(remote["Body"])
    if hashlib.sha256(observed).hexdigest() != ciphertext_hash:
        raise Stage3ObjectStorageError("encrypted probe ciphertext readback hash differs")
    if len(observed) < 13:
        raise Stage3ObjectStorageError("encrypted probe ciphertext is malformed")
    restored = AESGCM(key).decrypt(observed[:12], observed[12:], aad)
    if hashlib.sha256(restored).hexdigest() != plaintext_hash or restored != plaintext:
        raise Stage3ObjectStorageError("encrypted probe plaintext readback hash differs")
    return {
        "object_key": object_key,
        "version_id": version_id,
        "plaintext_sha256": plaintext_hash,
        "ciphertext_sha256": ciphertext_hash,
        "plaintext_bytes": len(plaintext),
        "ciphertext_bytes": len(ciphertext),
        "client_side_encryption": "AES-256-GCM",
        "server_side_encryption": head.get("ServerSideEncryption"),
        "ephemeral_key_persisted": False,
        "readback_verified": True,
    }


def execute(args: argparse.Namespace, *, client=None) -> dict[str, Any]:  # noqa: ANN001
    bucket = str(args.bucket)
    prefix = str(args.prefix)
    match = PREFIX_RE.fullmatch(prefix)
    if bucket != STAGING_BUCKET:
        raise Stage3ObjectStorageError("bucket is not the pinned Stage 3 staging bucket")
    if not match:
        raise Stage3ObjectStorageError("prefix must be one exact Stage 3 campaign UUID")
    campaign_id = match.group(1)
    expected_rule = _lifecycle_rule(campaign_id, prefix)
    expected_confirmation = confirmation_phrase(bucket, prefix)
    if client is None:
        client = _client(_credentials(args.credentials))
    before = audit(client, bucket=bucket, lifecycle_rule=expected_rule)
    if not before["versioning_enabled"]:
        raise Stage3ObjectStorageError("staging bucket versioning is not enabled")
    if not before["private_acl"]:
        raise Stage3ObjectStorageError("staging bucket ACL is public")
    if before["bucket_policy_public"]:
        raise Stage3ObjectStorageError("staging bucket policy is public")
    if (
        before["server_default_encryption_configured"]
        and not before["default_encryption_aes256"]
    ):
        raise Stage3ObjectStorageError("unexpected server-side encryption configuration")
    if (
        before["public_access_block_configured"]
        and not before["strict_public_access_block"]
    ):
        raise Stage3ObjectStorageError("unexpected public-access-block configuration")
    if not args.apply:
        return {
            "status": "planned",
            "bucket": bucket,
            "prefix": prefix,
            "campaign_id": campaign_id,
            "before": before,
            "required_confirmation": expected_confirmation,
            "mutating_operations": [
                "remove_incompatible_default_sse_configuration_if_present",
                "remove_incompatible_public_access_block_configuration_if_present",
                f"put_lifecycle_rule:{expected_rule['ID']}",
                "put_client-side-AES-256-GCM-readback-probe",
            ],
            "bucket_or_object_delete": False,
        }
    if args.confirm != expected_confirmation:
        raise Stage3ObjectStorageError("Object Storage confirmation mismatch")
    rules = _merge_lifecycle_rule(client, bucket=bucket, expected=expected_rule)
    output_dir = args.output_dir.resolve()
    if output_dir.is_relative_to(REPO_ROOT.resolve()):
        raise Stage3ObjectStorageError("evidence output directory must be outside the repository")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    if stat.S_IMODE(output_dir.stat().st_mode) != 0o700:
        raise Stage3ObjectStorageError("evidence output directory must be mode 0700")

    if before["server_default_encryption_configured"]:
        client.delete_bucket_encryption(Bucket=bucket)
    if before["public_access_block_configured"]:
        client.delete_public_access_block(Bucket=bucket)
    client.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={"Rules": rules},
    )
    after = audit(client, bucket=bucket, lifecycle_rule=expected_rule)
    required = (
        "versioning_enabled",
        "private_acl",
        "campaign_lifecycle_exact",
    )
    if (
        not all(after[name] for name in required)
        or after["bucket_policy_public"]
        or after["server_default_encryption_configured"]
        or after["public_access_block_configured"]
    ):
        raise Stage3ObjectStorageError("post-change Object Storage controls are incomplete")
    probe = _encrypted_roundtrip(
        client, bucket=bucket, prefix=prefix, campaign_id=campaign_id
    )
    evidence = {
        "schema": "three-site-stage3-object-storage-readiness-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "private-versioned-lifecycle-client-encrypted-readback-verified",
        "bucket": bucket,
        "prefix": prefix,
        "campaign_id": campaign_id,
        "before": before,
        "after": after,
        "lifecycle": {
            "id": expected_rule["ID"],
            "expiration_days": 45,
            "noncurrent_version_expiration_days": 14,
            "abort_incomplete_multipart_days": 1,
        },
        "provider_compatibility": {
            "provider": "Arvan Object Storage",
            "default_sse_put_compatible": False,
            "strict_public_access_block_put_compatible": False,
            "required_payload_encryption": "client-side AES-256-GCM",
            "private_boundary": "private ACL plus non-public bucket policy",
        },
        "probe": probe,
        "bucket_created": False,
        "bucket_or_object_deleted": False,
        "credentials_persisted": False,
    }
    encoded = (json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n").encode()
    evidence_path = output_dir / "object-storage-readiness.json"
    write_secure_atomic_bytes(
        evidence_path,
        encoded,
        label="Stage 3 Object Storage readiness evidence",
        max_size=1024 * 1024,
    )
    return {
        "status": evidence["status"],
        "evidence": str(evidence_path),
        "evidence_sha256": hashlib.sha256(encoded).hexdigest(),
        "object_key": probe["object_key"],
        "version_id": probe["version_id"],
        "secrets_printed": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        result = execute(parse_args(argv))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
