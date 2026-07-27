"""Exact human-approval binding for one production-shadow deployment.

The approval subject cannot directly hash the final cutover manifest because
that manifest also records the approval token hash.  The authorization basis
therefore contains the complete validated manifest except for that one
circular field.  The final manifest and every runtime verifier can derive the
same basis again and verify one production approval against it.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Mapping

from core.canonical_json import CanonicalJSONError, canonical_json_bytes
from core.human_approval import (
    HumanApprovalError,
    VerifiedHumanApproval,
    approval_subject,
    verify_human_approval,
)


AUTHORIZATION_BASIS_SCHEMA = (
    "production-shadow-cutover-authorization-basis-v1"
)
AUTHORIZATION_ACTION = "deploy_three_site_production"
AUTHORIZATION_ENVIRONMENT = "production"
APPROVAL_HASH_FIELD = "cutover_approval_sha256"
POLICY_HASH_FIELD = "human_approval_policy_sha256"
ZERO_SHA256 = "0" * 64
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "operation_id",
        "created_at",
        "release_sha",
        "release_tree_sha",
        "legacy_release_sha",
        "topology",
        "deployment",
        "artifacts",
        "policy",
    }
)


class ProductionShadowAuthorizationError(RuntimeError):
    """The production deployment is not bound to one exact human approval."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionShadowAuthorizationError(
                f"authorization JSON contains duplicate field: {key}"
            )
        result[key] = value
    return result


def parse_canonical_json_object(
    raw: bytes,
    *,
    label: str,
) -> dict[str, Any]:
    """Parse strict JSON and require its bytes to be the canonical encoding."""

    if not isinstance(raw, bytes) or not raw:
        raise ProductionShadowAuthorizationError(f"{label} is empty")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except ProductionShadowAuthorizationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionShadowAuthorizationError(
            f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(document, dict):
        raise ProductionShadowAuthorizationError(
            f"{label} root must be an object"
        )
    try:
        canonical = canonical_json_bytes(document)
    except CanonicalJSONError as exc:
        raise ProductionShadowAuthorizationError(
            f"{label} is not canonical JSON"
        ) from exc
    if raw != canonical:
        raise ProductionShadowAuthorizationError(
            f"{label} bytes are not canonical JSON"
        )
    return document


def authorization_basis_from_manifest(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the non-circular, complete approval basis from a manifest."""

    if not isinstance(manifest, Mapping) or set(manifest) != MANIFEST_FIELDS:
        raise ProductionShadowAuthorizationError(
            "cutover manifest fields are not exact for authorization"
        )
    artifacts = manifest.get("artifacts")
    if (
        not isinstance(artifacts, Mapping)
        or APPROVAL_HASH_FIELD not in artifacts
        or POLICY_HASH_FIELD not in artifacts
    ):
        raise ProductionShadowAuthorizationError(
            "cutover manifest lacks approval or policy binding"
        )
    try:
        normalized = json.loads(canonical_json_bytes(dict(manifest)))
    except (CanonicalJSONError, json.JSONDecodeError) as exc:
        raise ProductionShadowAuthorizationError(
            "cutover manifest is not strict canonical JSON data"
        ) from exc
    normalized_artifacts = normalized["artifacts"]
    del normalized_artifacts[APPROVAL_HASH_FIELD]
    return {
        "schema": AUTHORIZATION_BASIS_SCHEMA,
        "campaign_id": normalized["campaign_id"],
        "operation_id": normalized["operation_id"],
        "created_at": normalized["created_at"],
        "release_sha": normalized["release_sha"],
        "release_tree_sha": normalized["release_tree_sha"],
        "legacy_release_sha": normalized["legacy_release_sha"],
        "topology": normalized["topology"],
        "deployment": normalized["deployment"],
        "artifacts": normalized_artifacts,
        "policy": normalized["policy"],
    }


def authorization_basis_sha256(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(authorization_basis_from_manifest(manifest))
    ).hexdigest()


def authorization_subject_from_manifest(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact subject shown to and signed by the human operator."""

    basis_sha256 = authorization_basis_sha256(manifest)
    try:
        deployment = manifest["deployment"]
        policy = manifest["policy"]
        bindings = {
            "authorization_basis_schema": AUTHORIZATION_BASIS_SCHEMA,
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "legacy_release_sha": manifest["legacy_release_sha"],
            "production_hostname": deployment["production_hostname"],
            "shadow_compose_project": deployment[
                "shadow_compose_project"
            ],
            "rollback_before_first_write_only": policy[
                "rollback_before_first_write_only"
            ],
            "postcommit_forward_recovery_required": policy[
                "postcommit_forward_recovery_required"
            ],
            "object_storage_private_versioned_age_required": policy[
                "object_storage_private_versioned_age_required"
            ],
        }
        return approval_subject(
            artifact_type=AUTHORIZATION_BASIS_SCHEMA,
            artifact_sha256=basis_sha256,
            release_sha=str(manifest["release_sha"]),
            bindings=bindings,
        )
    except (KeyError, TypeError, HumanApprovalError) as exc:
        raise ProductionShadowAuthorizationError(
            "cutover manifest cannot produce an exact approval subject"
        ) from exc


def verify_authorization_documents(
    manifest: Mapping[str, Any],
    *,
    approval_bytes: bytes,
    policy_bytes: bytes,
    now: datetime | None = None,
    require_fresh: bool = True,
) -> VerifiedHumanApproval:
    """Verify canonical files, pinned hashes, issuer signature and subject."""

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ProductionShadowAuthorizationError(
            "cutover manifest artifacts are unavailable"
        )
    approval_sha256 = hashlib.sha256(approval_bytes).hexdigest()
    policy_sha256 = hashlib.sha256(policy_bytes).hexdigest()
    if approval_sha256 != artifacts.get(APPROVAL_HASH_FIELD):
        raise ProductionShadowAuthorizationError(
            "production approval bytes differ from the cutover manifest"
        )
    if policy_sha256 != artifacts.get(POLICY_HASH_FIELD):
        raise ProductionShadowAuthorizationError(
            "human approval policy bytes differ from the cutover manifest"
        )
    approval = parse_canonical_json_object(
        approval_bytes,
        label="production approval",
    )
    policy = parse_canonical_json_object(
        policy_bytes,
        label="human approval policy",
    )
    subject = authorization_subject_from_manifest(manifest)
    try:
        verified = verify_human_approval(
            approval,
            policy_payload=policy,
            expected_action=AUTHORIZATION_ACTION,
            expected_environment=AUTHORIZATION_ENVIRONMENT,
            expected_subject=subject,
            now=now,
            require_fresh=require_fresh,
        )
    except HumanApprovalError as exc:
        raise ProductionShadowAuthorizationError(
            "production approval verification failed"
        ) from exc
    if verified.token_hash != approval_sha256:
        raise ProductionShadowAuthorizationError(
            "production approval canonical hash is inconsistent"
        )
    return verified
