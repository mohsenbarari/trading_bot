#!/usr/bin/env python3
"""Build and finalize one non-circular production-shadow authorization.

The template is the final cutover manifest with only
``cutover_approval_sha256`` set to 64 zeroes.  ``subject`` creates the exact
public document to be approved on the isolated Witness.  ``finalize`` verifies
that approval and its pinned public policy, canonicalizes both, inserts the
token hash, validates the final manifest, and publishes all outputs without
overwriting a differing file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import canonical_json_bytes  # noqa: E402
from core.human_approval import HumanApprovalError, verify_human_approval  # noqa: E402
from core.production_shadow_authorization import (  # noqa: E402
    APPROVAL_HASH_FIELD,
    AUTHORIZATION_ACTION,
    AUTHORIZATION_ENVIRONMENT,
    POLICY_HASH_FIELD,
    ZERO_SHA256,
    ProductionShadowAuthorizationError,
    authorization_basis_sha256,
    authorization_subject_from_manifest,
    verify_authorization_documents,
)
from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from scripts.production_shadow_cutover_controller import (  # noqa: E402
    CutoverContractError,
    validate_manifest,
)


MAX_DOCUMENT_BYTES = 16 * 1024 * 1024


class CutoverManifestFinalizationError(RuntimeError):
    """The cutover authorization template cannot be safely finalized."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CutoverManifestFinalizationError(
                f"JSON contains duplicate field: {key}"
            )
        result[key] = value
    return result


def _parse_strict_object(raw: bytes, *, label: str) -> dict[str, Any]:
    if not raw:
        raise CutoverManifestFinalizationError(f"{label} is empty")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except CutoverManifestFinalizationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CutoverManifestFinalizationError(
            f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(document, dict):
        raise CutoverManifestFinalizationError(
            f"{label} root must be an object"
        )
    return document


def _read_document(
    path: Path,
    *,
    label: str,
    required_uid: int,
    require_canonical: bool,
) -> tuple[dict[str, Any], bytes]:
    try:
        raw = read_secure_bytes(
            path,
            label=label,
            owner_uid=required_uid,
            max_size=MAX_DOCUMENT_BYTES,
        )
    except SecureFileError as exc:
        raise CutoverManifestFinalizationError(str(exc)) from exc
    document = _parse_strict_object(raw, label=label)
    canonical = canonical_json_bytes(document)
    if require_canonical and raw != canonical:
        raise CutoverManifestFinalizationError(
            f"{label} bytes are not canonical JSON"
        )
    return document, canonical


def _validated_template(document: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = json.loads(canonical_json_bytes(document))
        artifacts = normalized["artifacts"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CutoverManifestFinalizationError(
            "cutover manifest template structure is invalid"
        ) from exc
    if (
        not isinstance(artifacts, dict)
        or artifacts.get(APPROVAL_HASH_FIELD) != ZERO_SHA256
    ):
        raise CutoverManifestFinalizationError(
            "cutover manifest template must contain only a pending approval hash"
        )
    provisional = json.loads(canonical_json_bytes(normalized))
    provisional["artifacts"][APPROVAL_HASH_FIELD] = "1" * 64
    try:
        validate_manifest(provisional)
    except CutoverContractError as exc:
        raise CutoverManifestFinalizationError(
            "cutover manifest template fails the production contract"
        ) from exc
    return normalized


def _preflight_destination(
    path: Path,
    payload: bytes,
    *,
    label: str,
    required_uid: int,
) -> str | None:
    if path.exists() or path.is_symlink():
        try:
            existing = read_secure_bytes(
                path,
                label=label,
                owner_uid=required_uid,
                max_size=MAX_DOCUMENT_BYTES,
            )
        except SecureFileError as exc:
            raise CutoverManifestFinalizationError(str(exc)) from exc
        if existing != payload:
            raise CutoverManifestFinalizationError(
                f"{label} already exists with different bytes"
            )
        return "existing-exact"
    return None


def _publish_exact(
    path: Path,
    payload: bytes,
    *,
    label: str,
    required_uid: int,
) -> str:
    existing = _preflight_destination(
        path,
        payload,
        label=label,
        required_uid=required_uid,
    )
    if existing is not None:
        return existing
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=0o600,
            max_size=MAX_DOCUMENT_BYTES,
        )
    except SecureFileError as exc:
        raise CutoverManifestFinalizationError(str(exc)) from exc
    return "created"


def build_subject(
    *,
    template_path: Path,
    output_path: Path,
    required_uid: int,
) -> dict[str, Any]:
    template, _ = _read_document(
        template_path,
        label="production cutover manifest template",
        required_uid=required_uid,
        require_canonical=True,
    )
    template = _validated_template(template)
    subject = authorization_subject_from_manifest(template)
    subject_bytes = canonical_json_bytes(subject)
    publication = _publish_exact(
        output_path,
        subject_bytes,
        label="production cutover approval subject",
        required_uid=required_uid,
    )
    return {
        "status": "subject-ready",
        "operation_id": template["operation_id"],
        "release_sha": template["release_sha"],
        "authorization_basis_sha256": authorization_basis_sha256(template),
        "subject_sha256": hashlib.sha256(subject_bytes).hexdigest(),
        "publication": publication,
        "output": str(output_path),
    }


def finalize_manifest(
    *,
    template_path: Path,
    policy_path: Path,
    approval_path: Path,
    manifest_output: Path,
    policy_output: Path,
    approval_output: Path,
    required_uid: int,
) -> dict[str, Any]:
    template, _ = _read_document(
        template_path,
        label="production cutover manifest template",
        required_uid=required_uid,
        require_canonical=True,
    )
    template = _validated_template(template)
    policy, policy_bytes = _read_document(
        policy_path,
        label="human approval policy source",
        required_uid=required_uid,
        require_canonical=False,
    )
    approval, approval_bytes = _read_document(
        approval_path,
        label="production approval source",
        required_uid=required_uid,
        require_canonical=False,
    )
    policy_sha256 = hashlib.sha256(policy_bytes).hexdigest()
    if policy_sha256 != template["artifacts"].get(POLICY_HASH_FIELD):
        raise CutoverManifestFinalizationError(
            "human approval policy differs from the manifest template"
        )
    subject = authorization_subject_from_manifest(template)
    try:
        verified = verify_human_approval(
            approval,
            policy_payload=policy,
            expected_action=AUTHORIZATION_ACTION,
            expected_environment=AUTHORIZATION_ENVIRONMENT,
            expected_subject=subject,
            require_fresh=True,
        )
    except HumanApprovalError as exc:
        raise CutoverManifestFinalizationError(
            "production approval is invalid, expired, or bound elsewhere"
        ) from exc
    if hashlib.sha256(approval_bytes).hexdigest() != verified.token_hash:
        raise CutoverManifestFinalizationError(
            "production approval canonical hash is inconsistent"
        )

    final_manifest = json.loads(canonical_json_bytes(template))
    final_manifest["artifacts"][APPROVAL_HASH_FIELD] = verified.token_hash
    try:
        validate_manifest(final_manifest)
        verify_authorization_documents(
            final_manifest,
            approval_bytes=approval_bytes,
            policy_bytes=policy_bytes,
            require_fresh=True,
        )
    except (
        CutoverContractError,
        ProductionShadowAuthorizationError,
    ) as exc:
        raise CutoverManifestFinalizationError(
            "final production cutover manifest verification failed"
        ) from exc
    manifest_bytes = canonical_json_bytes(final_manifest)

    destinations = (
        (
            policy_output,
            policy_bytes,
            "canonical human approval policy",
        ),
        (
            approval_output,
            approval_bytes,
            "canonical production approval",
        ),
        (
            manifest_output,
            manifest_bytes,
            "final production cutover manifest",
        ),
    )
    for destination, payload, label in destinations:
        _preflight_destination(
            destination,
            payload,
            label=label,
            required_uid=required_uid,
        )
    publications = {
        "policy": _publish_exact(
            policy_output,
            policy_bytes,
            label="canonical human approval policy",
            required_uid=required_uid,
        ),
        "approval": _publish_exact(
            approval_output,
            approval_bytes,
            label="canonical production approval",
            required_uid=required_uid,
        ),
        "manifest": _publish_exact(
            manifest_output,
            manifest_bytes,
            label="final production cutover manifest",
            required_uid=required_uid,
        ),
    }
    return {
        "status": "finalized",
        "campaign_id": final_manifest["campaign_id"],
        "operation_id": final_manifest["operation_id"],
        "release_sha": final_manifest["release_sha"],
        "authorization_basis_sha256": authorization_basis_sha256(
            final_manifest
        ),
        "approval_sha256": verified.token_hash,
        "policy_sha256": policy_sha256,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "approval_expires_at": verified.expires_at.isoformat(),
        "publications": publications,
        "manifest_output": str(manifest_output),
        "approval_output": str(approval_output),
        "policy_output": str(policy_output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subject = subparsers.add_parser("subject")
    subject.add_argument("--manifest-template", type=Path, required=True)
    subject.add_argument("--output", type=Path, required=True)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--manifest-template", type=Path, required=True)
    finalize.add_argument("--policy", type=Path, required=True)
    finalize.add_argument("--approval", type=Path, required=True)
    finalize.add_argument("--manifest-output", type=Path, required=True)
    finalize.add_argument("--policy-output", type=Path, required=True)
    finalize.add_argument("--approval-output", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise CutoverManifestFinalizationError(
                "production cutover authorization must run as root"
            )
        if args.command == "subject":
            result = build_subject(
                template_path=args.manifest_template,
                output_path=args.output,
                required_uid=0,
            )
        else:
            result = finalize_manifest(
                template_path=args.manifest_template,
                policy_path=args.policy,
                approval_path=args.approval,
                manifest_output=args.manifest_output,
                policy_output=args.policy_output,
                approval_output=args.approval_output,
                required_uid=0,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "production_contacted": False,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
