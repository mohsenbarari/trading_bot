"""Off-host anchors for local hash-chained DR/operator audit logs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from core.config import settings
from core.dr_blob_worker import S3Config, _client
from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import sha256_secure_file, verify_hash_chained_jsonl


SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SITE_RE = re.compile(r"^(?:bot_fi|webapp_fi|webapp_ir|witness|orchestrator)$")


class DrAuditAnchorError(RuntimeError):
    pass


def build_audit_anchor(
    audit_path: Path,
    *,
    site: str,
    log_id: str,
    release_sha: str,
) -> tuple[dict[str, Any], str]:
    if not SITE_RE.fullmatch(site) or not SAFE_ID_RE.fullmatch(log_id):
        raise DrAuditAnchorError("audit anchor site/log identity is invalid")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", release_sha):
        raise DrAuditAnchorError("audit anchor requires an exact release SHA")
    records = verify_hash_chained_jsonl(audit_path, label="DR audit log")
    if not records:
        raise DrAuditAnchorError("an empty audit log cannot be anchored")
    file_hash, file_size = sha256_secure_file(audit_path, label="DR audit log")
    payload = {
        "schema": "three-site-audit-anchor-v1",
        "site": site,
        "log_id": log_id,
        "release_sha": release_sha,
        "record_count": len(records),
        "tail_event_hash": records[-1]["event_hash"],
        "audit_file_sha256": file_hash,
        "audit_file_size": file_size,
    }
    prefix = str(settings.dr_audit_anchor_object_prefix or "").strip("/")
    if not prefix or any(part in {"", ".", ".."} for part in prefix.split("/")):
        raise DrAuditAnchorError("audit anchor Object Storage prefix is unsafe")
    key = (
        f"{prefix}/{site}/{log_id}/{len(records):020d}-"
        f"{records[-1]['event_hash']}.json"
    )
    return payload, key


def _object_bytes(result: dict[str, Any]) -> bytes:
    body = result.get("Body")
    if body is None or not hasattr(body, "read"):
        raise DrAuditAnchorError("audit anchor read-back has no body")
    payload = body.read(64 * 1024 + 1)
    if len(payload) > 64 * 1024:
        raise DrAuditAnchorError("audit anchor read-back is oversized")
    return payload


def store_audit_anchor(
    config: S3Config,
    payload: dict[str, Any],
    key: str,
    *,
    client=None,  # noqa: ANN001
) -> dict[str, Any]:
    active_client = client or _client(config)
    versioning = active_client.get_bucket_versioning(Bucket=config.bucket)
    if versioning.get("Status") != "Enabled":
        raise DrAuditAnchorError("audit anchor bucket versioning is not enabled")
    encoded = canonical_json_bytes(payload)
    object_hash = hashlib.sha256(encoded).hexdigest()
    try:
        result = active_client.put_object(
            Bucket=config.bucket,
            Key=key,
            Body=encoded,
            ContentLength=len(encoded),
            ContentType="application/json",
            Metadata={"sha256": object_hash, "tail-event-hash": payload["tail_event_hash"]},
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
        )
        version_id = result.get("VersionId")
        if not version_id:
            raise DrAuditAnchorError("stored audit anchor lacks a version identity")
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code not in {"PreconditionFailed", "412"}:
            raise
        version_id = None
    read_back = active_client.get_object(Bucket=config.bucket, Key=key)
    if _object_bytes(read_back) != encoded:
        raise DrAuditAnchorError("audit anchor immutable read-back mismatch")
    observed_version = read_back.get("VersionId") or version_id
    if not observed_version:
        raise DrAuditAnchorError("audit anchor read-back lacks a version identity")
    if (read_back.get("Metadata") or {}).get("sha256") != object_hash:
        raise DrAuditAnchorError("audit anchor metadata hash mismatch")
    return {
        "status": "anchored",
        "object_key": key,
        "object_version_id": str(observed_version),
        "anchor_sha256": object_hash,
        "record_count": int(payload["record_count"]),
        "tail_event_hash": payload["tail_event_hash"],
    }


def verify_audit_anchor(
    config: S3Config,
    *,
    expected_payload: dict[str, Any],
    key: str,
    version_id: str,
    client=None,  # noqa: ANN001
) -> dict[str, Any]:
    if not version_id:
        raise DrAuditAnchorError("audit anchor verification requires an object version")
    active_client = client or _client(config)
    result = active_client.get_object(
        Bucket=config.bucket,
        Key=key,
        VersionId=version_id,
    )
    expected = canonical_json_bytes(expected_payload)
    if _object_bytes(result) != expected or result.get("VersionId") != version_id:
        raise DrAuditAnchorError("versioned audit anchor does not match local evidence")
    return {
        "status": "verified",
        "object_key": key,
        "object_version_id": version_id,
        "anchor_sha256": hashlib.sha256(expected).hexdigest(),
    }
