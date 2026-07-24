"""Action-bound human approvals issued after password + TOTP authentication.

The private signing key and the TOTP seed belong to the isolated approval
issuer.  Runtime roles receive only the public policy and an approval token.
Tokens are deliberately bound to an exact JSON subject and cannot authorize a
different artifact, release, environment, or action.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.canonical_json import canonical_json_bytes


POLICY_SCHEMA = "three-site-human-approval-policy-v1"
TOKEN_SCHEMA = "three-site-human-approval-token-v1"
SESSION_TOKEN_SCHEMA = "three-site-human-approval-session-token-v1"
SESSION_MAX_TTL_SECONDS = 48 * 60 * 60
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_AUTHENTICATION_METHODS = {
    ("password", "totp"),
    ("password", "recovery_code"),
}


class HumanApprovalError(RuntimeError):
    """Raised when a human approval policy or token fails closed."""


@dataclass(frozen=True)
class ApprovalActionPolicy:
    action: str
    environments: frozenset[str]
    max_ttl_seconds: int


@dataclass(frozen=True)
class HumanApprovalPolicy:
    policy_id: str
    issuer_id: str
    key_id: str
    operator: str
    authenticator_id: str
    public_key: bytes
    actions: dict[str, ApprovalActionPolicy]
    policy_hash: str


@dataclass(frozen=True)
class VerifiedHumanApproval:
    approval_id: str
    operator: str
    action: str
    environment: str
    subject: dict[str, Any]
    issued_at: datetime
    expires_at: datetime
    authentication_methods: tuple[str, str]
    token_hash: str


def _utc(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HumanApprovalError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HumanApprovalError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise HumanApprovalError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _current_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise HumanApprovalError("human approval verification time must include a timezone")
    return value.astimezone(timezone.utc)


def approval_policy_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def load_human_approval_policy(payload: Any) -> HumanApprovalPolicy:
    fields = {"schema", "policy_id", "issuer", "actions"}
    if (
        not isinstance(payload, dict)
        or set(payload) != fields
        or payload.get("schema") != POLICY_SCHEMA
    ):
        raise HumanApprovalError("human approval policy fields/schema are invalid")
    try:
        policy_id = str(UUID(str(payload["policy_id"])))
    except ValueError as exc:
        raise HumanApprovalError("human approval policy_id is invalid") from exc

    issuer = payload.get("issuer")
    issuer_fields = {
        "issuer_id", "key_id", "operator", "authenticator_id", "public_key"
    }
    if not isinstance(issuer, dict) or set(issuer) != issuer_fields:
        raise HumanApprovalError("human approval issuer fields are invalid")
    issuer_id = str(issuer["issuer_id"]).strip()
    key_id = str(issuer["key_id"]).strip()
    operator = str(issuer["operator"]).strip()
    authenticator_id = str(issuer["authenticator_id"]).strip()
    if not all((issuer_id, key_id, operator, authenticator_id)):
        raise HumanApprovalError("human approval issuer identity is incomplete")
    try:
        public_key = base64.b64decode(str(issuer["public_key"]), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HumanApprovalError("human approval issuer public key is invalid") from exc
    if len(public_key) != 32:
        raise HumanApprovalError("human approval issuer public key must be Ed25519")

    raw_actions = payload.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise HumanApprovalError("human approval policy has no allowed actions")
    actions: dict[str, ApprovalActionPolicy] = {}
    for raw in raw_actions:
        if not isinstance(raw, dict) or set(raw) != {
            "action", "environments", "max_ttl_seconds"
        }:
            raise HumanApprovalError("human approval action policy is invalid")
        action = str(raw["action"]).strip()
        environments = raw["environments"]
        ttl = raw["max_ttl_seconds"]
        if (
            not action
            or action in actions
            or not isinstance(environments, list)
            or not environments
            or any(value not in {"staging", "production"} for value in environments)
            or len(set(environments)) != len(environments)
            or type(ttl) is not int
            or not 30 <= ttl <= 24 * 60 * 60
        ):
            raise HumanApprovalError("human approval action constraints are invalid")
        actions[action] = ApprovalActionPolicy(
            action=action,
            environments=frozenset(environments),
            max_ttl_seconds=ttl,
        )
    return HumanApprovalPolicy(
        policy_id=policy_id,
        issuer_id=issuer_id,
        key_id=key_id,
        operator=operator,
        authenticator_id=authenticator_id,
        public_key=public_key,
        actions=actions,
        policy_hash=approval_policy_hash(payload),
    )


def approval_subject(
    *,
    artifact_type: str,
    artifact_sha256: str,
    release_sha: str,
    bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the canonical, exact subject shared by issuers and verifiers."""

    artifact_type = str(artifact_type).strip()
    artifact_sha256 = str(artifact_sha256).lower()
    release_sha = str(release_sha).lower()
    if not artifact_type or SHA256_RE.fullmatch(artifact_sha256) is None:
        raise HumanApprovalError("approval subject artifact identity is invalid")
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", release_sha) is None:
        raise HumanApprovalError("approval subject release SHA is invalid")
    extra = {} if bindings is None else bindings
    if not isinstance(extra, dict):
        raise HumanApprovalError("approval subject bindings must be an object")
    # Round-trip rejects values that are not strict JSON and gives callers an
    # isolated structure that cannot be mutated after validation.
    try:
        normalized = json.loads(canonical_json_bytes(extra))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HumanApprovalError("approval subject bindings are not strict JSON") from exc
    if not isinstance(normalized, dict):
        raise HumanApprovalError("approval subject bindings must remain an object")
    return {
        "artifact_type": artifact_type,
        "artifact_sha256": artifact_sha256,
        "release_sha": release_sha,
        "bindings": normalized,
    }


def validate_approval_subject(payload: Any) -> dict[str, Any]:
    """Validate and normalize the only subject shape accepted by the issuer."""

    fields = {"artifact_type", "artifact_sha256", "release_sha", "bindings"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise HumanApprovalError("human approval subject fields are invalid")
    return approval_subject(
        artifact_type=payload["artifact_type"],
        artifact_sha256=payload["artifact_sha256"],
        release_sha=payload["release_sha"],
        bindings=payload["bindings"],
    )


def verify_human_approval(
    token: Any,
    *,
    policy_payload: dict[str, Any],
    expected_action: str,
    expected_environment: str,
    expected_subject: dict[str, Any],
    now: datetime | None = None,
    require_fresh: bool = True,
) -> VerifiedHumanApproval:
    """Verify an exact token or a release-bound staging operator session.

    Long-running operations call this with ``require_fresh=True`` before their
    first journal entry.  Historical evidence and an idempotent journal resume
    may use ``require_fresh=False``; the signature, subject, action, policy and
    original bounded lifetime are still verified.
    """

    policy = load_human_approval_policy(policy_payload)
    if isinstance(token, dict) and token.get("schema") == SESSION_TOKEN_SCHEMA:
        return _verify_staging_session(
            token,
            policy=policy,
            expected_action=expected_action,
            expected_environment=expected_environment,
            expected_subject=expected_subject,
            now=now,
            require_fresh=require_fresh,
        )

    fields = {
        "schema", "approval_id", "policy_id", "policy_hash", "issuer_id",
        "key_id", "operator", "authenticator_id", "action", "environment",
        "subject", "issued_at", "expires_at", "authentication", "signature",
    }
    if (
        not isinstance(token, dict)
        or set(token) != fields
        or token.get("schema") != TOKEN_SCHEMA
    ):
        raise HumanApprovalError("human approval token fields/schema are invalid")
    try:
        approval_id = str(UUID(str(token["approval_id"])))
    except ValueError as exc:
        raise HumanApprovalError("human approval_id is invalid") from exc
    if (
        token.get("policy_id") != policy.policy_id
        or token.get("policy_hash") != policy.policy_hash
        or token.get("issuer_id") != policy.issuer_id
        or token.get("key_id") != policy.key_id
        or token.get("operator") != policy.operator
        or token.get("authenticator_id") != policy.authenticator_id
    ):
        raise HumanApprovalError("human approval token is not bound to its issuer policy")

    action = str(token.get("action"))
    environment = str(token.get("environment"))
    action_policy = policy.actions.get(action)
    if (
        action != expected_action
        or action_policy is None
        or environment != expected_environment
        or environment not in action_policy.environments
    ):
        raise HumanApprovalError("human approval action/environment is not authorized")
    try:
        normalized_expected_subject = validate_approval_subject(expected_subject)
        normalized_actual_subject = validate_approval_subject(token.get("subject"))
        expected_subject_bytes = canonical_json_bytes(normalized_expected_subject)
        actual_subject_bytes = canonical_json_bytes(normalized_actual_subject)
    except (TypeError, ValueError, HumanApprovalError) as exc:
        raise HumanApprovalError("human approval subject is not canonical JSON") from exc
    if not hmac.compare_digest(actual_subject_bytes, expected_subject_bytes):
        raise HumanApprovalError("human approval token is bound to a different subject")

    authentication = token.get("authentication")
    if not isinstance(authentication, dict) or set(authentication) != {"methods"}:
        raise HumanApprovalError("human approval authentication evidence is invalid")
    methods = authentication.get("methods")
    if not isinstance(methods, list) or tuple(methods) not in ALLOWED_AUTHENTICATION_METHODS:
        raise HumanApprovalError("human approval did not use password plus a possession factor")

    issued = _utc(token["issued_at"], label="human approval issued_at")
    expires = _utc(token["expires_at"], label="human approval expires_at")
    if (
        expires <= issued
        or expires - issued > timedelta(seconds=action_policy.max_ttl_seconds)
    ):
        raise HumanApprovalError("human approval lifetime exceeds its action policy")
    current = _current_utc(now)
    if issued > current + timedelta(seconds=30):
        raise HumanApprovalError("human approval is future-dated")
    if require_fresh and current >= expires:
        raise HumanApprovalError("human approval start window has expired")

    unsigned = {key: value for key, value in token.items() if key != "signature"}
    try:
        signature = base64.b64decode(str(token["signature"]), validate=True)
        Ed25519PublicKey.from_public_bytes(policy.public_key).verify(
            signature, canonical_json_bytes(unsigned)
        )
    except (ValueError, binascii.Error, InvalidSignature) as exc:
        raise HumanApprovalError("human approval signature is invalid") from exc
    return VerifiedHumanApproval(
        approval_id=approval_id,
        operator=policy.operator,
        action=action,
        environment=environment,
        subject=json.loads(actual_subject_bytes),
        issued_at=issued,
        expires_at=expires,
        authentication_methods=tuple(methods),
        token_hash=hashlib.sha256(canonical_json_bytes(token)).hexdigest(),
    )


def _verify_staging_session(
    token: dict[str, Any],
    *,
    policy: HumanApprovalPolicy,
    expected_action: str,
    expected_environment: str,
    expected_subject: dict[str, Any],
    now: datetime | None,
    require_fresh: bool,
) -> VerifiedHumanApproval:
    """Verify one 48-hour, release-bound staging operations session.

    A session deliberately replaces repeated password+TOTP prompts during one
    staging release campaign.  It never authorizes production and cannot cross
    a release SHA boundary.  The action-specific verifier still supplies the
    exact subject, so every downstream journal retains the artifact it used.
    """

    fields = {
        "schema", "approval_id", "policy_id", "policy_hash", "issuer_id",
        "key_id", "operator", "authenticator_id", "environment",
        "release_sha", "allowed_actions", "issued_at", "expires_at",
        "authentication", "signature",
    }
    if set(token) != fields:
        raise HumanApprovalError("human approval session fields/schema are invalid")
    try:
        approval_id = str(UUID(str(token["approval_id"])))
    except ValueError as exc:
        raise HumanApprovalError("human approval session id is invalid") from exc
    if (
        token.get("policy_id") != policy.policy_id
        or token.get("policy_hash") != policy.policy_hash
        or token.get("issuer_id") != policy.issuer_id
        or token.get("key_id") != policy.key_id
        or token.get("operator") != policy.operator
        or token.get("authenticator_id") != policy.authenticator_id
    ):
        raise HumanApprovalError("human approval session is not bound to its issuer policy")

    environment = str(token.get("environment"))
    if environment != "staging" or expected_environment != "staging":
        raise HumanApprovalError("human approval session cannot authorize production")
    actions = token.get("allowed_actions")
    if (
        not isinstance(actions, list)
        or not actions
        or any(not isinstance(action, str) or not action for action in actions)
        or actions != sorted(set(actions))
        or expected_action not in actions
    ):
        raise HumanApprovalError("human approval session does not authorize this action")
    for action in actions:
        action_policy = policy.actions.get(action)
        if action_policy is None or "staging" not in action_policy.environments:
            raise HumanApprovalError("human approval session contains an unauthorized action")

    try:
        normalized_subject = validate_approval_subject(expected_subject)
    except (TypeError, ValueError, HumanApprovalError) as exc:
        raise HumanApprovalError("human approval subject is not canonical JSON") from exc
    release_sha = str(token.get("release_sha")).lower()
    if (
        re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", release_sha) is None
        or not hmac.compare_digest(
            release_sha.encode("ascii"),
            normalized_subject["release_sha"].encode("ascii"),
        )
    ):
        raise HumanApprovalError("human approval session is bound to a different release")

    authentication = token.get("authentication")
    if not isinstance(authentication, dict) or set(authentication) != {"methods"}:
        raise HumanApprovalError("human approval session authentication evidence is invalid")
    methods = authentication.get("methods")
    if not isinstance(methods, list) or tuple(methods) not in ALLOWED_AUTHENTICATION_METHODS:
        raise HumanApprovalError(
            "human approval session did not use password plus a possession factor"
        )

    issued = _utc(token["issued_at"], label="human approval session issued_at")
    expires = _utc(token["expires_at"], label="human approval session expires_at")
    if (
        expires <= issued
        or expires - issued > timedelta(seconds=SESSION_MAX_TTL_SECONDS)
    ):
        raise HumanApprovalError("human approval session lifetime exceeds 48 hours")
    current = _current_utc(now)
    if issued > current + timedelta(seconds=30):
        raise HumanApprovalError("human approval session is future-dated")
    if require_fresh and current >= expires:
        raise HumanApprovalError("human approval session has expired")

    unsigned = {key: value for key, value in token.items() if key != "signature"}
    try:
        signature = base64.b64decode(str(token["signature"]), validate=True)
        Ed25519PublicKey.from_public_bytes(policy.public_key).verify(
            signature, canonical_json_bytes(unsigned)
        )
    except (ValueError, binascii.Error, InvalidSignature) as exc:
        raise HumanApprovalError("human approval session signature is invalid") from exc
    return VerifiedHumanApproval(
        approval_id=approval_id,
        operator=policy.operator,
        action=expected_action,
        environment=environment,
        subject=json.loads(canonical_json_bytes(normalized_subject)),
        issued_at=issued,
        expires_at=expires,
        authentication_methods=tuple(methods),
        token_hash=hashlib.sha256(canonical_json_bytes(token)).hexdigest(),
    )
