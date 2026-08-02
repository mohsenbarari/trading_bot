#!/usr/bin/env python3
"""Rotate only the Object Storage secret in existing Stage 3 bootstrap material."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (
    read_secure_bytes,
    read_secure_text,
    write_secure_atomic_bytes,
)
from scripts.publish_wa_ir_object_storage_preflight import _credentials


STAGING_BUCKET = "gold-trade-staging-three-site-dr"
STAGING_PREFIX = "staging/fd34231d-f52e-498a-aab4-438c99d88fc5/"
CAMPAIGN_ID = "fd34231d-f52e-498a-aab4-438c99d88fc5"
DEPLOYMENT_ID = "stage3-0e63a7ec-fd34231d"
EXPECTED_RELEASE_SHA = "0e63a7ec1b08bef29ea199041215298a021b56ef"
READINESS_STATUS = "private-versioned-lifecycle-client-encrypted-readback-verified"
CONFIRMATION = f"rotate-stage3-object-storage-material:{CAMPAIGN_ID}"


class MaterialRotationError(RuntimeError):
    """A redacted, fail-closed material rotation error."""


def _sha256(value: bytes | str) -> str:
    payload = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _secure_json(path: Path, *, label: str) -> tuple[dict[str, object], bytes]:
    payload = read_secure_bytes(path, label=label, max_size=1024 * 1024)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialRotationError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise MaterialRotationError(f"{label} must be one JSON object")
    return value, payload


def _old_credentials(path: Path) -> tuple[dict[str, str], bytes]:
    value, payload = _secure_json(path, label="current Object Storage credential")
    if set(value) != {"access_key", "secret_key"}:
        raise MaterialRotationError("current Object Storage credential fields are invalid")
    access_key = str(value["access_key"])
    secret_key = str(value["secret_key"])
    if len(access_key) < 8 or len(secret_key) < 32:
        raise MaterialRotationError("current Object Storage credential is malformed")
    return {"access_key": access_key, "secret_key": secret_key}, payload


def _verify_root(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise MaterialRotationError("bootstrap material root must be owner-controlled mode 0700")


def _validate_manifest(manifest: dict[str, object]) -> None:
    if manifest.get("schema") not in {
        "three-site-stage3-bootstrap-material-v1",
        "three-site-stage3-bootstrap-material-v2",
    }:
        raise MaterialRotationError("bootstrap material schema is unsupported")
    expected = {
        "campaign_id": CAMPAIGN_ID,
        "deployment_id": DEPLOYMENT_ID,
        "release_sha": EXPECTED_RELEASE_SHA,
    }
    if any(manifest.get(name) != value for name, value in expected.items()):
        raise MaterialRotationError("bootstrap material identity is not the pinned Stage 3 release")


def _validate_readiness(
    evidence: dict[str, object],
    *,
    access_key_sha256: str,
    secret_key_sha256: str,
) -> None:
    fingerprints = evidence.get("credential_fingerprints_sha256")
    expected = {
        "access_key_sha256": access_key_sha256,
        "secret_key_sha256": secret_key_sha256,
    }
    if (
        evidence.get("status") != READINESS_STATUS
        or evidence.get("bucket") != STAGING_BUCKET
        or evidence.get("prefix") != STAGING_PREFIX
        or evidence.get("campaign_id") != CAMPAIGN_ID
        or fingerprints != expected
        or not isinstance(evidence.get("probe"), dict)
        or evidence["probe"].get("readback_verified") is not True
        or not evidence["probe"].get("version_id")
    ):
        raise MaterialRotationError("readiness evidence is not bound to the new credential")


def rotate(args: argparse.Namespace) -> dict[str, object]:
    root = args.material_root.resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[1]):
        raise MaterialRotationError("bootstrap material must remain outside the repository")
    _verify_root(root)
    credential_path = root / "secrets/staging-dr-blob-s3.json"
    manifest_path = root / "bootstrap-material-manifest.json"
    current, current_bytes = _old_credentials(credential_path)
    manifest, current_manifest_bytes = _secure_json(
        manifest_path,
        label="bootstrap material manifest",
    )
    _validate_manifest(manifest)
    readiness, readiness_bytes = _secure_json(
        args.readiness_evidence,
        label="rotated credential readiness evidence",
    )
    new_access, new_secret, _endpoint, _region = _credentials(args.new_credentials)
    old_access_sha256 = _sha256(current["access_key"])
    old_secret_sha256 = _sha256(current["secret_key"])
    new_access_sha256 = _sha256(new_access)
    new_secret_sha256 = _sha256(new_secret)
    if current["secret_key"] == new_secret:
        raise MaterialRotationError("Object Storage secret did not rotate")
    _validate_readiness(
        readiness,
        access_key_sha256=new_access_sha256,
        secret_key_sha256=new_secret_sha256,
    )
    new_credential_bytes = _json_bytes(
        {"access_key": new_access, "secret_key": new_secret}
    )
    updated_manifest = dict(manifest)
    updated_manifest.update(
        {
            "schema": "three-site-stage3-bootstrap-material-v2",
            "object_storage_access_key_sha256": new_access_sha256,
            "object_storage_secret_key_sha256": new_secret_sha256,
            "object_storage_credentials_file_sha256": _sha256(new_credential_bytes),
            "object_storage_rotation": {
                "rotated_at_utc": datetime.now(timezone.utc).isoformat(),
                "previous_access_key_sha256": old_access_sha256,
                "previous_secret_key_sha256": old_secret_sha256,
                "new_access_key_sha256": new_access_sha256,
                "new_secret_key_sha256": new_secret_sha256,
                "readiness_evidence_sha256": _sha256(readiness_bytes),
                "readback_version_id": readiness["probe"]["version_id"],
            },
        }
    )
    new_manifest_bytes = _json_bytes(updated_manifest)
    result = {
        "status": "planned",
        "campaign_id": CAMPAIGN_ID,
        "access_key_changed": current["access_key"] != new_access,
        "secret_key_changed": True,
        "old_secret_key_sha256": old_secret_sha256,
        "new_secret_key_sha256": new_secret_sha256,
        "new_credential_file_sha256": _sha256(new_credential_bytes),
        "new_manifest_sha256": _sha256(new_manifest_bytes),
        "readiness_evidence_sha256": _sha256(readiness_bytes),
        "required_confirmation": CONFIRMATION,
        "files_changed": [
            "secrets/staging-dr-blob-s3.json",
            "bootstrap-material-manifest.json",
        ],
        "other_material_regenerated": False,
    }
    if not args.apply:
        return result
    if args.confirm != CONFIRMATION:
        raise MaterialRotationError("material rotation confirmation mismatch")
    write_secure_atomic_bytes(
        credential_path,
        new_credential_bytes,
        label="rotated Stage 3 Object Storage credential",
    )
    try:
        write_secure_atomic_bytes(
            manifest_path,
            new_manifest_bytes,
            label="rotated Stage 3 bootstrap manifest",
        )
    except Exception:
        write_secure_atomic_bytes(
            credential_path,
            current_bytes,
            label="Stage 3 Object Storage credential rollback",
        )
        raise
    observed_credential = read_secure_bytes(
        credential_path,
        label="installed rotated Object Storage credential",
    )
    observed_manifest = read_secure_bytes(
        manifest_path,
        label="installed rotated bootstrap manifest",
    )
    if observed_credential != new_credential_bytes or observed_manifest != new_manifest_bytes:
        write_secure_atomic_bytes(
            credential_path,
            current_bytes,
            label="Stage 3 Object Storage credential verification rollback",
        )
        write_secure_atomic_bytes(
            manifest_path,
            current_manifest_bytes,
            label="Stage 3 bootstrap manifest verification rollback",
        )
        raise MaterialRotationError("rotated bootstrap material did not persist exactly")
    result["status"] = "rotated"
    result["required_confirmation"] = None
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--material-root", type=Path, required=True)
    parser.add_argument("--new-credentials", type=Path, required=True)
    parser.add_argument("--readiness-evidence", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        result = rotate(parse_args(argv))
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
