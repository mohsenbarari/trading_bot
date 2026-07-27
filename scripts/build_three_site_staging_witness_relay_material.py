#!/usr/bin/env python3
"""Build and validate a no-overwrite Witness relay-material revision.

Prepared material deliberately has no file or image attestation.  It may be
copied only to an inert revision directory.  A separate finalization step
requires a real, release-bound approval session and its matching public policy;
it still does not activate a service or claim an image attestation.
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

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from core.canonical_json import canonical_json_bytes
from core.human_approval import (
    RELAY_RECEIPT_SCHEMA,
    SESSION_TOKEN_SCHEMA,
    approval_policy_hash,
    approval_subject,
    load_human_approval_policy,
    staging_session_scope_sha256,
    verify_human_approval,
)
from core.human_approval_issuer import DEFAULT_STAGING_SESSION_ACTIONS
from core.secure_file_io import read_secure_text, write_secure_new_bytes
from scripts.render_three_site_staging_role_compose import (
    canonical_role_compose_bytes,
    canonical_role_env_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
)
from scripts.verify_three_site_staging_campaign_bundle import (
    ROLES,
    verify_campaign_bundle,
)
from scripts.verify_three_site_staging_inventory import (
    _canonical_bytes,
    verify_inventory,
)


PREPARED_SCHEMA = "three-site-staging-witness-relay-prepared-v2"
FINAL_SCHEMA = "three-site-staging-witness-relay-final-v2"
PREPARED_STATUS = "prepared-not-file-or-image-attested"
FINAL_STATUS = "final-transfer-bundle-not-installed-or-image-attested"
MANIFEST_NAME = "relay-material-manifest.json"
PREPARED_MANIFEST_NAME = "prepared-relay-material-manifest.json"
COMPOSE_NAME = "witness.compose.yml"
ENV_NAME = "witness.env"
ACTIVE_DIRECTORY_NAME = "active"
ARCHIVE_DIRECTORY_NAME = "archive"
JOURNAL_DIRECTORY_NAME = ".rotation-journal"
SESSION_NAME = "session.json"
POLICY_NAME = "policy.json"
PREPARED_FILE_MODES = {
    COMPOSE_NAME: 0o640,
    ENV_NAME: 0o600,
    MANIFEST_NAME: 0o600,
}
FINAL_FILE_MODES = {
    COMPOSE_NAME: 0o640,
    ENV_NAME: 0o600,
    f"{ACTIVE_DIRECTORY_NAME}/{SESSION_NAME}": 0o600,
    f"{ACTIVE_DIRECTORY_NAME}/{POLICY_NAME}": 0o600,
    PREPARED_MANIFEST_NAME: 0o600,
    MANIFEST_NAME: 0o600,
}
ALLOWED_ENV_CHANGES = frozenset(
    {
        "STAGING_SOURCE_ROOT",
        "STAGING_HUMAN_APPROVAL_RELAY_ENABLED",
        "STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR",
        "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID",
        "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET",
    }
)
REQUIRED_MATRIX_ACTIONS = DEFAULT_STAGING_SESSION_ACTIONS
REVISION_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{7,79}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
SOURCE_RELEASE_ROOT = Path("/srv/trading-bot-three-site/releases")
FORBIDDEN_OUTPUT_ROOTS = (
    Path("/boot"),
    Path("/dev"),
    Path("/etc"),
    Path("/proc"),
    Path("/run"),
    Path("/srv"),
    Path("/sys"),
    Path("/usr"),
    Path("/var/lib"),
    Path("/var/run"),
)


class WitnessRelayMaterialError(RuntimeError):
    """Witness relay material cannot be proven safe."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_json_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WitnessRelayMaterialError(f"{label} contains duplicate fields")
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=hook)
    except (UnicodeDecodeError, json.JSONDecodeError, WitnessRelayMaterialError) as exc:
        raise WitnessRelayMaterialError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise WitnessRelayMaterialError(f"{label} must be a JSON object")
    return value


def _manifest_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _utc_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise WitnessRelayMaterialError("revision timestamp must include a timezone")
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_revision_id(value: str) -> str:
    revision = str(value).strip()
    if REVISION_RE.fullmatch(revision) is None:
        raise WitnessRelayMaterialError("relay revision ID is invalid")
    return revision


def _safe_key_id(value: str) -> str:
    key_id = str(value).strip()
    if KEY_ID_RE.fullmatch(key_id) is None:
        raise WitnessRelayMaterialError("relay orchestrator key ID is invalid")
    return key_id


def _safe_secret(value: str) -> str:
    secret = str(value)
    if (
        len(secret.encode("utf-8")) < 32
        or "\x00" in secret
        or "\r" in secret
        or "\n" in secret
        or "change_me" in secret.lower()
        or "placeholder" in secret.lower()
    ):
        raise WitnessRelayMaterialError("relay orchestrator secret is unsafe")
    return secret


def _normalized_absolute(value: str, *, label: str) -> Path:
    path = Path(str(value))
    if not path.is_absolute() or ".." in path.parts or str(path) != os.path.normpath(str(path)):
        raise WitnessRelayMaterialError(f"{label} must be an absolute normalized path")
    return path


def _relay_material_directory(
    value: str,
    *,
    revision_id: str,
    campaign_id: str,
    deployment_id: str,
) -> str:
    path = _normalized_absolute(value, label="relay material directory")
    if (
        path.name != ACTIVE_DIRECTORY_NAME
        or path.parent.name != revision_id
        or path.parent.parent.name != "material-revisions"
        or path.parent.parent.parent.name != deployment_id
        or path.parent.parent.parent.parent.name != campaign_id
    ):
        raise WitnessRelayMaterialError(
            "relay material directory must be campaign/deployment/revision-bound"
        )
    return str(path)


def _source_root(release_sha: str) -> str:
    release = str(release_sha).lower()
    if RELEASE_RE.fullmatch(release) is None:
        raise WitnessRelayMaterialError("inventory release SHA is invalid")
    return str(SOURCE_RELEASE_ROOT / release)


def _required_actions(*, policy: dict[str, Any]) -> tuple[str, ...]:
    actions = tuple(REQUIRED_MATRIX_ACTIONS)
    if actions != tuple(sorted(set(actions))):
        raise WitnessRelayMaterialError("required relay actions are not canonical")
    parsed_policy = load_human_approval_policy(policy)
    if any(
        action not in parsed_policy.actions
        or "staging" not in parsed_policy.actions[action].environments
        for action in actions
    ):
        raise WitnessRelayMaterialError(
            "required relay action is absent from the staging approval policy"
        )
    return actions


def _assert_base_compose(
    *,
    canonical_compose: dict[str, Any],
    inventory: dict[str, Any],
    compose_bytes: bytes,
) -> frozenset[str]:
    role_payload = render_role_compose(
        canonical_compose,
        role="witness",
        project_namespace=str(
            inventory.get(
                "compose_project_namespace", "trading-bot-three-site-staging"
            )
        ),
    )
    expected = canonical_role_compose_bytes(role_payload)
    if compose_bytes != expected:
        raise WitnessRelayMaterialError(
            "base Witness Compose differs from the canonical renderer"
        )
    return referenced_environment_names(role_payload)


def _derive_environment(
    *,
    base_env_bytes: bytes,
    required_names: frozenset[str],
    release_sha: str,
    material_directory: str,
    relay_key_id: str,
    relay_secret: str,
) -> tuple[bytes, dict[str, str], dict[str, str]]:
    try:
        base_values = parse_env_values(base_env_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise WitnessRelayMaterialError("base Witness environment is not UTF-8") from exc
    if set(base_values) != set(required_names):
        raise WitnessRelayMaterialError(
            "base Witness environment is not the exact closed variable set"
        )
    if base_env_bytes != canonical_role_env_bytes(
        base_values, required_names=required_names
    ):
        raise WitnessRelayMaterialError("base Witness environment is not canonical")
    if base_values.get("STAGING_RELEASE_SHA", "").lower() != release_sha:
        raise WitnessRelayMaterialError(
            "base Witness environment differs from the inventory release"
        )
    disabled = (
        base_values.get("STAGING_HUMAN_APPROVAL_RELAY_ENABLED"),
        base_values.get("STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR"),
        base_values.get("STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID"),
        base_values.get("STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET"),
    )
    if disabled != ("false", "/dev/null", "", ""):
        raise WitnessRelayMaterialError(
            "base Witness environment is not the exact disabled relay baseline"
        )
    exact_source_root = _source_root(release_sha)
    if base_values.get("STAGING_SOURCE_ROOT") == exact_source_root:
        raise WitnessRelayMaterialError(
            "base source root already equals the immutable release; five changes required"
        )

    revised_values = dict(base_values)
    revised_values.update(
        {
            "STAGING_SOURCE_ROOT": exact_source_root,
            "STAGING_HUMAN_APPROVAL_RELAY_ENABLED": "true",
            "STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR": material_directory,
            "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID": relay_key_id,
            "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET": relay_secret,
        }
    )
    changed = {
        name
        for name in set(base_values) | set(revised_values)
        if base_values.get(name) != revised_values.get(name)
    }
    if changed != ALLOWED_ENV_CHANGES:
        raise WitnessRelayMaterialError(
            "relay revision does not change exactly the five allowed variables"
        )
    revised = canonical_role_env_bytes(
        revised_values, required_names=required_names
    )
    return revised, base_values, revised_values


def derive_prepared_revision(
    *,
    canonical_compose: dict[str, Any],
    base_compose_bytes: bytes,
    base_env_bytes: bytes,
    inventory: dict[str, Any],
    approval_policy: dict[str, Any],
    revision_id: str,
    material_directory: str,
    relay_key_id: str,
    relay_secret: str,
    created_at: datetime | None = None,
) -> tuple[bytes, bytes, dict[str, Any]]:
    """Derive prepared bytes without claiming file or image attestation."""

    verified_inventory = verify_inventory(inventory, host_destructive=None)
    if verified_inventory["inventory_stage"] != "provisioned":
        raise WitnessRelayMaterialError(
            "Witness relay revision requires a provisioned inventory"
        )
    revision = _safe_revision_id(revision_id)
    key_id = _safe_key_id(relay_key_id)
    secret = _safe_secret(relay_secret)
    material_path = _relay_material_directory(
        material_directory,
        revision_id=revision,
        campaign_id=verified_inventory["campaign_id"],
        deployment_id=verified_inventory["deployment_id"],
    )
    actions = _required_actions(policy=approval_policy)
    required_names = _assert_base_compose(
        canonical_compose=canonical_compose,
        inventory=inventory,
        compose_bytes=base_compose_bytes,
    )
    revised_env, _base_values, _revised_values = _derive_environment(
        base_env_bytes=base_env_bytes,
        required_names=required_names,
        release_sha=verified_inventory["release_sha"],
        material_directory=material_path,
        relay_key_id=key_id,
        relay_secret=secret,
    )
    compose_hash = _sha256(base_compose_bytes)
    inventory_hash = _sha256(_canonical_bytes(inventory))
    manifest = {
        "schema": PREPARED_SCHEMA,
        "stage": "prepared",
        "status": PREPARED_STATUS,
        "revision_id": revision,
        "created_at": _utc_timestamp(created_at),
        "campaign": {
            "campaign_id": verified_inventory["campaign_id"],
            "deployment_id": verified_inventory["deployment_id"],
            "release_sha": verified_inventory["release_sha"],
            "inventory_sha256": inventory_hash,
            "approval_policy_sha256": approval_policy_hash(approval_policy),
        },
        "base": {
            "role_compose_sha256": compose_hash,
            "role_env_sha256": _sha256(base_env_bytes),
        },
        "prepared": {
            "role_compose_sha256": compose_hash,
            "role_env_sha256": _sha256(revised_env),
        },
        "allowed_environment_changes": sorted(ALLOWED_ENV_CHANGES),
        "bindings": {
            "source_root": _source_root(verified_inventory["release_sha"]),
            "material_directory": material_path,
            "relay_key_id": key_id,
            "relay_secret_sha256": _sha256(secret.encode("utf-8")),
        },
        "required_session_actions": list(actions),
        "attestations": {
            "role_files": False,
            "images": False,
            "activation": False,
        },
    }
    return base_compose_bytes, revised_env, manifest


def _assert_manifest_keys(
    manifest: dict[str, Any],
    *,
    expected: set[str],
    label: str,
) -> None:
    if set(manifest) != expected:
        raise WitnessRelayMaterialError(f"{label} fields are invalid")


def verify_prepared_structure(
    *,
    canonical_compose: dict[str, Any],
    base_compose_bytes: bytes,
    base_env_bytes: bytes,
    prepared_compose_bytes: bytes,
    prepared_env_bytes: bytes,
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    approval_policy: dict[str, Any],
) -> dict[str, Any]:
    """Verify the five-field revision without file or image attestation."""

    _assert_manifest_keys(
        manifest,
        expected={
            "schema",
            "stage",
            "status",
            "revision_id",
            "created_at",
            "campaign",
            "base",
            "prepared",
            "allowed_environment_changes",
            "bindings",
            "required_session_actions",
            "attestations",
        },
        label="prepared relay manifest",
    )
    if (
        manifest["schema"] != PREPARED_SCHEMA
        or manifest["stage"] != "prepared"
        or manifest["status"] != PREPARED_STATUS
        or manifest["attestations"]
        != {"role_files": False, "images": False, "activation": False}
        or manifest["allowed_environment_changes"] != sorted(ALLOWED_ENV_CHANGES)
    ):
        raise WitnessRelayMaterialError(
            "prepared relay manifest overstates or changes its safety boundary"
        )
    revision = _safe_revision_id(str(manifest["revision_id"]))
    try:
        created = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise WitnessRelayMaterialError("prepared relay timestamp is invalid") from exc
    if created.tzinfo is None:
        raise WitnessRelayMaterialError("prepared relay timestamp lacks a timezone")

    verified_inventory = verify_inventory(inventory, host_destructive=None)
    if verified_inventory["inventory_stage"] != "provisioned":
        raise WitnessRelayMaterialError(
            "prepared relay material requires a provisioned inventory"
        )
    campaign = manifest["campaign"]
    base = manifest["base"]
    prepared = manifest["prepared"]
    bindings = manifest["bindings"]
    if not all(isinstance(value, dict) for value in (campaign, base, prepared, bindings)):
        raise WitnessRelayMaterialError("prepared relay manifest sections are invalid")
    _assert_manifest_keys(
        campaign,
        expected={
            "campaign_id",
            "deployment_id",
            "release_sha",
            "inventory_sha256",
            "approval_policy_sha256",
        },
        label="prepared relay campaign",
    )
    _assert_manifest_keys(
        base,
        expected={"role_compose_sha256", "role_env_sha256"},
        label="prepared relay base",
    )
    _assert_manifest_keys(
        prepared,
        expected={"role_compose_sha256", "role_env_sha256"},
        label="prepared relay output",
    )
    _assert_manifest_keys(
        bindings,
        expected={
            "source_root",
            "material_directory",
            "relay_key_id",
            "relay_secret_sha256",
        },
        label="prepared relay bindings",
    )
    expected_campaign = {
        "campaign_id": verified_inventory["campaign_id"],
        "deployment_id": verified_inventory["deployment_id"],
        "release_sha": verified_inventory["release_sha"],
        "inventory_sha256": _sha256(_canonical_bytes(inventory)),
        "approval_policy_sha256": approval_policy_hash(approval_policy),
    }
    if campaign != expected_campaign:
        raise WitnessRelayMaterialError(
            "prepared relay manifest differs from inventory or approval policy"
        )
    hashes = (
        base.get("role_compose_sha256"),
        base.get("role_env_sha256"),
        prepared.get("role_compose_sha256"),
        prepared.get("role_env_sha256"),
        bindings.get("relay_secret_sha256"),
    )
    if any(not isinstance(value, str) or SHA256_RE.fullmatch(value) is None for value in hashes):
        raise WitnessRelayMaterialError("prepared relay manifest hash is invalid")
    if (
        base["role_compose_sha256"] != _sha256(base_compose_bytes)
        or base["role_env_sha256"] != _sha256(base_env_bytes)
        or prepared["role_compose_sha256"] != _sha256(prepared_compose_bytes)
        or prepared["role_env_sha256"] != _sha256(prepared_env_bytes)
        or prepared_compose_bytes != base_compose_bytes
    ):
        raise WitnessRelayMaterialError(
            "prepared relay artifact bytes differ from the manifest"
        )

    required_names = _assert_base_compose(
        canonical_compose=canonical_compose,
        inventory=inventory,
        compose_bytes=base_compose_bytes,
    )
    material_directory = _relay_material_directory(
        str(bindings["material_directory"]),
        revision_id=revision,
        campaign_id=verified_inventory["campaign_id"],
        deployment_id=verified_inventory["deployment_id"],
    )
    _safe_key_id(str(bindings["relay_key_id"]))
    expected_source = _source_root(verified_inventory["release_sha"])
    if bindings["source_root"] != expected_source:
        raise WitnessRelayMaterialError(
            "prepared relay source root is not the immutable release"
        )
    try:
        base_values = parse_env_values(base_env_bytes.decode("utf-8"))
        prepared_values = parse_env_values(prepared_env_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise WitnessRelayMaterialError("Witness environment is not UTF-8") from exc
    if (
        set(base_values) != set(required_names)
        or set(prepared_values) != set(required_names)
        or base_env_bytes
        != canonical_role_env_bytes(base_values, required_names=required_names)
        or prepared_env_bytes
        != canonical_role_env_bytes(prepared_values, required_names=required_names)
    ):
        raise WitnessRelayMaterialError(
            "prepared Witness environment is not canonical and closed"
        )
    changed = {
        name
        for name in set(base_values) | set(prepared_values)
        if base_values.get(name) != prepared_values.get(name)
    }
    disabled = (
        base_values.get("STAGING_HUMAN_APPROVAL_RELAY_ENABLED"),
        base_values.get("STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR"),
        base_values.get("STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID"),
        base_values.get("STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET"),
    )
    if changed != ALLOWED_ENV_CHANGES or disabled != (
        "false",
        "/dev/null",
        "",
        "",
    ):
        raise WitnessRelayMaterialError(
            "prepared relay material is not an exact five-field disabled-baseline revision"
        )
    expected_values = {
        "STAGING_SOURCE_ROOT": expected_source,
        "STAGING_HUMAN_APPROVAL_RELAY_ENABLED": "true",
        "STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR": material_directory,
        "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID": str(
            bindings["relay_key_id"]
        ),
    }
    if any(prepared_values.get(name) != value for name, value in expected_values.items()):
        raise WitnessRelayMaterialError(
            "prepared Witness environment differs from its manifest bindings"
        )
    relay_secret = _safe_secret(
        prepared_values.get(
            "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET", ""
        )
    )
    if _sha256(relay_secret.encode("utf-8")) != bindings["relay_secret_sha256"]:
        raise WitnessRelayMaterialError(
            "prepared relay secret differs from its manifest hash"
        )
    actions = _required_actions(policy=approval_policy)
    if list(actions) != manifest["required_session_actions"]:
        raise WitnessRelayMaterialError(
            "prepared relay required actions are not canonical"
        )
    return {
        "status": PREPARED_STATUS,
        "stage": "prepared",
        "revision_id": revision,
        "campaign_id": verified_inventory["campaign_id"],
        "release_sha": verified_inventory["release_sha"],
        "role_compose_sha256": _sha256(prepared_compose_bytes),
        "role_env_sha256": _sha256(prepared_env_bytes),
        "file_attestation": False,
        "image_attestation": False,
        "activation": False,
    }


def validate_prepared_campaign(
    *,
    canonical_compose: dict[str, Any],
    base_compose_bytes: bytes,
    base_env_bytes: bytes,
    prepared_compose_bytes: bytes,
    prepared_env_bytes: bytes,
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    approval: dict[str, Any],
    approval_policy: dict[str, Any],
    other_bundles: dict[str, tuple[bytes, bytes]],
    witness_relay_public_key: str | None = None,
) -> dict[str, Any]:
    """Validate prepared material with verify_files=False, never as image evidence."""

    structural = verify_prepared_structure(
        canonical_compose=canonical_compose,
        base_compose_bytes=base_compose_bytes,
        base_env_bytes=base_env_bytes,
        prepared_compose_bytes=prepared_compose_bytes,
        prepared_env_bytes=prepared_env_bytes,
        manifest=manifest,
        inventory=inventory,
        approval_policy=approval_policy,
    )
    if (
        approval.get("schema") == RELAY_RECEIPT_SCHEMA
        and not str(witness_relay_public_key or "").strip()
    ):
        raise WitnessRelayMaterialError(
            "prepared campaign relay receipt requires an explicitly pinned Witness key"
        )
    if set(other_bundles) != set(ROLES) - {"witness"}:
        raise WitnessRelayMaterialError(
            "prepared validation requires exactly the three unchanged role bundles"
        )
    campaign = verify_campaign_bundle(
        canonical_compose=canonical_compose,
        bundles={
            **other_bundles,
            "witness": (prepared_compose_bytes, prepared_env_bytes),
        },
        inventory=inventory,
        approval=approval,
        approval_policy=approval_policy,
        verify_files=False,
        witness_relay_public_key=witness_relay_public_key,
    )
    if campaign["file_attestation"]:
        raise WitnessRelayMaterialError(
            "prepared relay validation unexpectedly claimed file attestation"
        )
    return {
        **structural,
        "status": "prepared-relay-material-verified-not-image-attested",
        "campaign_bundle_sha256": campaign["campaign_bundle_sha256"],
        "next_gate": "finalize-with-real-session-and-policy-before-any-activation",
    }


def _session_probe_subject(session: dict[str, Any], *, release_sha: str) -> dict[str, Any]:
    actions = session.get("allowed_actions")
    return approval_subject(
        artifact_type=SESSION_TOKEN_SCHEMA,
        artifact_sha256=_sha256(
            canonical_json_bytes(
                {"release_sha": release_sha, "allowed_actions": actions}
            )
        ),
        release_sha=release_sha,
        bindings={},
    )


def _verify_final_session(
    *,
    session: dict[str, Any],
    policy: dict[str, Any],
    prepared_manifest: dict[str, Any],
    now: datetime | None,
) -> dict[str, Any]:
    if session.get("schema") != SESSION_TOKEN_SCHEMA:
        raise WitnessRelayMaterialError(
            "final relay bundle requires a real staging approval session"
        )
    release_sha = str(prepared_manifest["campaign"]["release_sha"])
    required_actions = list(prepared_manifest["required_session_actions"])
    if session.get("allowed_actions") != required_actions:
        raise WitnessRelayMaterialError(
            "final relay session action scope is not the exact live matrix scope"
        )
    session_scope = staging_session_scope_sha256(
        release_sha=release_sha,
        allowed_actions=required_actions,
    )
    probe = _session_probe_subject(session, release_sha=release_sha)
    verified = None
    for action in required_actions:
        verified = verify_human_approval(
            session,
            policy_payload=policy,
            expected_action=action,
            expected_environment="staging",
            expected_subject=probe,
            now=now,
            require_fresh=True,
            allow_session=True,
        )
    if verified is None:
        raise WitnessRelayMaterialError(
            "final relay session has no required action to verify"
        )
    return {
        "approval_id": verified.approval_id,
        "expires_at": verified.expires_at.isoformat(),
        "session_token_sha256": verified.token_hash,
        "session_scope_sha256": session_scope,
    }


def finalize_revision(
    *,
    canonical_compose: dict[str, Any],
    base_compose_bytes: bytes,
    base_env_bytes: bytes,
    prepared_compose_bytes: bytes,
    prepared_env_bytes: bytes,
    prepared_manifest: dict[str, Any],
    prepared_manifest_bytes: bytes,
    inventory: dict[str, Any],
    policy: dict[str, Any],
    policy_bytes: bytes,
    session: dict[str, Any],
    session_bytes: bytes,
    created_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bind real session/policy bytes into a new final transfer bundle."""

    structural = verify_prepared_structure(
        canonical_compose=canonical_compose,
        base_compose_bytes=base_compose_bytes,
        base_env_bytes=base_env_bytes,
        prepared_compose_bytes=prepared_compose_bytes,
        prepared_env_bytes=prepared_env_bytes,
        manifest=prepared_manifest,
        inventory=inventory,
        approval_policy=policy,
    )
    if prepared_manifest_bytes != _manifest_bytes(prepared_manifest):
        raise WitnessRelayMaterialError("prepared relay manifest bytes are not canonical")
    session_result = _verify_final_session(
        session=session,
        policy=policy,
        prepared_manifest=prepared_manifest,
        now=now,
    )
    if (
        _strict_json_bytes(policy_bytes, label="relay policy") != policy
        or policy_bytes != _manifest_bytes(policy)
    ):
        raise WitnessRelayMaterialError(
            "relay policy bytes are not canonical or differ from parsed policy"
        )
    if (
        _strict_json_bytes(session_bytes, label="relay session") != session
        or session_bytes != _manifest_bytes(session)
    ):
        raise WitnessRelayMaterialError(
            "relay session bytes are not canonical or differ from parsed session"
        )
    return {
        "schema": FINAL_SCHEMA,
        "stage": "final",
        "status": FINAL_STATUS,
        "revision_id": structural["revision_id"],
        "created_at": _utc_timestamp(created_at),
        "prepared_manifest_sha256": _sha256(prepared_manifest_bytes),
        "campaign": dict(prepared_manifest["campaign"]),
        "base": dict(prepared_manifest["base"]),
        "prepared": dict(prepared_manifest["prepared"]),
        "bindings": dict(prepared_manifest["bindings"]),
        "required_session_actions": list(
            prepared_manifest["required_session_actions"]
        ),
        "final": {
            "policy_file_sha256": _sha256(policy_bytes),
            "approval_policy_sha256": approval_policy_hash(policy),
            "session_file_sha256": _sha256(session_bytes),
            **session_result,
        },
        "attestations": {
            "role_files": False,
            "images": False,
            "activation": False,
        },
    }


def verify_final_structure(
    *,
    canonical_compose: dict[str, Any],
    base_compose_bytes: bytes,
    base_env_bytes: bytes,
    final_compose_bytes: bytes,
    final_env_bytes: bytes,
    prepared_manifest: dict[str, Any],
    prepared_manifest_bytes: bytes,
    final_manifest: dict[str, Any],
    inventory: dict[str, Any],
    policy: dict[str, Any],
    policy_bytes: bytes,
    session: dict[str, Any],
    session_bytes: bytes,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a final transfer bundle without installing or activating it."""

    _assert_manifest_keys(
        final_manifest,
        expected={
            "schema",
            "stage",
            "status",
            "revision_id",
            "created_at",
            "prepared_manifest_sha256",
            "campaign",
            "base",
            "prepared",
            "bindings",
            "required_session_actions",
            "final",
            "attestations",
        },
        label="final relay manifest",
    )
    if (
        final_manifest["schema"] != FINAL_SCHEMA
        or final_manifest["stage"] != "final"
        or final_manifest["status"] != FINAL_STATUS
        or final_manifest["attestations"]
        != {"role_files": False, "images": False, "activation": False}
    ):
        raise WitnessRelayMaterialError(
            "final relay manifest overstates or changes its safety boundary"
        )
    try:
        created = datetime.fromisoformat(
            str(final_manifest["created_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise WitnessRelayMaterialError("final relay timestamp is invalid") from exc
    if created.tzinfo is None:
        raise WitnessRelayMaterialError("final relay timestamp lacks a timezone")
    structural = verify_prepared_structure(
        canonical_compose=canonical_compose,
        base_compose_bytes=base_compose_bytes,
        base_env_bytes=base_env_bytes,
        prepared_compose_bytes=final_compose_bytes,
        prepared_env_bytes=final_env_bytes,
        manifest=prepared_manifest,
        inventory=inventory,
        approval_policy=policy,
    )
    session_result = _verify_final_session(
        session=session,
        policy=policy,
        prepared_manifest=prepared_manifest,
        now=now,
    )
    final_values = final_manifest["final"]
    if not isinstance(final_values, dict):
        raise WitnessRelayMaterialError("final relay evidence is invalid")
    _assert_manifest_keys(
        final_values,
        expected={
            "policy_file_sha256",
            "approval_policy_sha256",
            "session_file_sha256",
            "approval_id",
            "expires_at",
            "session_token_sha256",
            "session_scope_sha256",
        },
        label="final relay evidence",
    )
    expected_final = {
        "policy_file_sha256": _sha256(policy_bytes),
        "approval_policy_sha256": approval_policy_hash(policy),
        "session_file_sha256": _sha256(session_bytes),
        **session_result,
    }
    copied_fields = (
        "revision_id",
        "campaign",
        "base",
        "prepared",
        "bindings",
        "required_session_actions",
    )
    if (
        prepared_manifest_bytes != _manifest_bytes(prepared_manifest)
        or final_manifest["prepared_manifest_sha256"]
        != _sha256(prepared_manifest_bytes)
        or any(
            final_manifest[field] != prepared_manifest[field]
            for field in copied_fields
        )
        or final_values != expected_final
        or _strict_json_bytes(policy_bytes, label="relay policy") != policy
        or _strict_json_bytes(session_bytes, label="relay session") != session
        or policy_bytes != _manifest_bytes(policy)
        or session_bytes != _manifest_bytes(session)
    ):
        raise WitnessRelayMaterialError(
            "final relay bundle differs from its prepared/session/policy bindings"
        )
    return {
        **structural,
        "status": "final-relay-transfer-bundle-verified-not-installed",
        "stage": "final",
        "session_expires_at": session_result["expires_at"],
        "file_attestation": False,
        "image_attestation": False,
        "activation": False,
        "next_gate": "explicitly-authorized-host-install-and-service-activation",
    }


def _assert_output_directory(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise WitnessRelayMaterialError(
            "bundle output directory must be absolute and normalized"
        )
    if "current" in path.parts or any(
        path == forbidden or forbidden in path.parents
        for forbidden in FORBIDDEN_OUTPUT_ROOTS
    ):
        raise WitnessRelayMaterialError(
            "bundle output directory overlaps a forbidden live-system path"
        )
    if path.exists() or path.is_symlink():
        raise WitnessRelayMaterialError(
            "bundle output directory already exists; choose a new revision path"
        )
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise WitnessRelayMaterialError(
            "bundle output parent must already exist"
        ) from exc
    if parent != path.parent:
        raise WitnessRelayMaterialError(
            "bundle output parent must not traverse a symlink"
        )
    metadata = parent.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise WitnessRelayMaterialError(
            "bundle output parent is not owner-controlled"
        )


def _publish_new_bundle(
    directory: Path,
    files: dict[str, tuple[bytes, int]],
) -> None:
    _assert_output_directory(directory)
    directory.mkdir(mode=0o700)
    published: list[Path] = []
    created_directories: list[Path] = []
    try:
        relative_paths: dict[str, Path] = {}
        for name in files:
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or len(relative.parts) > 2:
                raise WitnessRelayMaterialError("relay bundle relative path is unsafe")
            relative_paths[name] = relative
        for parent in sorted(
            {relative.parent for relative in relative_paths.values() if relative.parent != Path(".")},
            key=lambda value: len(value.parts),
        ):
            destination_parent = directory / parent
            destination_parent.mkdir(mode=0o700)
            created_directories.append(destination_parent)
        for name, (payload, mode) in files.items():
            destination = directory / relative_paths[name]
            write_secure_new_bytes(
                destination,
                payload,
                label=f"Witness relay bundle {name}",
                mode=mode,
            )
            published.append(destination)
    except Exception:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        for path in reversed(created_directories):
            path.rmdir()
        directory.rmdir()
        raise


def _assert_existing_bundle_directory(directory: Path) -> None:
    if not directory.is_absolute() or ".." in directory.parts:
        raise WitnessRelayMaterialError(
            "relay bundle directory must be an absolute normalized path"
        )
    try:
        resolved = directory.resolve(strict=True)
    except OSError as exc:
        raise WitnessRelayMaterialError("relay bundle directory is unavailable") from exc
    if resolved != directory:
        raise WitnessRelayMaterialError(
            "relay bundle directory must not traverse a symlink"
        )
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise WitnessRelayMaterialError(
            "relay bundle directory must be owner-controlled mode 0700"
        )


def read_exact_material_file(
    path: Path,
    *,
    expected_mode: int,
    label: str,
    max_size: int = 1024 * 1024,
) -> bytes:
    """Read one stable owner-controlled material file without following links."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WitnessRelayMaterialError(f"cannot securely open {label}: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != expected_mode
            or before.st_size <= 0
            or before.st_size > max_size
        ):
            raise WitnessRelayMaterialError(
                f"{label} must be an owner-controlled, single-link "
                f"mode-{expected_mode:04o} file"
            )
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
        )
        if (
            len(payload) > max_size
            or any(getattr(before, field) != getattr(after, field) for field in stable)
        ):
            raise WitnessRelayMaterialError(f"{label} changed while it was read")
        return payload
    finally:
        os.close(descriptor)


def read_exact_json_file(path: Path, *, label: str) -> dict[str, Any]:
    return _strict_json_bytes(
        read_exact_material_file(path, expected_mode=0o600, label=label),
        label=label,
    )


def _read_relay_secret_file(path: Path) -> str:
    source = read_secure_text(path, label="relay orchestrator secret")
    value = source[:-1] if source.endswith("\n") else source
    if "\n" in value or value != value.strip():
        raise WitnessRelayMaterialError(
            "relay orchestrator secret file must contain exactly one unpadded line"
        )
    return _safe_secret(value)


def _read_prepared_directory(
    directory: Path,
) -> tuple[bytes, bytes, bytes, dict[str, Any]]:
    _assert_existing_bundle_directory(directory)
    compose = read_exact_material_file(
        directory / COMPOSE_NAME,
        expected_mode=0o640,
        label="prepared Witness Compose",
    )
    env = read_exact_material_file(
        directory / ENV_NAME,
        expected_mode=0o600,
        label="prepared Witness environment",
    )
    manifest_bytes = read_exact_material_file(
        directory / MANIFEST_NAME,
        expected_mode=0o600,
        label="prepared relay manifest",
    )
    manifest = _strict_json_bytes(manifest_bytes, label="prepared relay manifest")
    if manifest_bytes != _manifest_bytes(manifest):
        raise WitnessRelayMaterialError("prepared relay manifest bytes are not canonical")
    if set(path.name for path in directory.iterdir()) != set(PREPARED_FILE_MODES):
        raise WitnessRelayMaterialError("prepared relay bundle file set is not exact")
    return compose, env, manifest_bytes, manifest


def _read_final_directory(
    directory: Path,
) -> tuple[
    bytes,
    bytes,
    bytes,
    dict[str, Any],
    bytes,
    dict[str, Any],
    bytes,
    dict[str, Any],
    bytes,
    dict[str, Any],
]:
    _assert_existing_bundle_directory(directory)
    expected_top_level = {
        COMPOSE_NAME,
        ENV_NAME,
        PREPARED_MANIFEST_NAME,
        MANIFEST_NAME,
        ACTIVE_DIRECTORY_NAME,
    }
    if set(path.name for path in directory.iterdir()) != expected_top_level:
        raise WitnessRelayMaterialError("final relay bundle file set is not exact")
    active = directory / ACTIVE_DIRECTORY_NAME
    active_metadata = active.lstat()
    if (
        not stat.S_ISDIR(active_metadata.st_mode)
        or stat.S_ISLNK(active_metadata.st_mode)
        or active_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(active_metadata.st_mode) != 0o700
        or {path.name for path in active.iterdir()} != {SESSION_NAME, POLICY_NAME}
    ):
        raise WitnessRelayMaterialError("final relay active directory is not exact")
    compose = read_exact_material_file(
        directory / COMPOSE_NAME,
        expected_mode=0o640,
        label="final Witness Compose",
    )
    env = read_exact_material_file(
        directory / ENV_NAME,
        expected_mode=0o600,
        label="final Witness environment",
    )
    prepared_manifest_bytes = read_exact_material_file(
        directory / PREPARED_MANIFEST_NAME,
        expected_mode=0o600,
        label="prepared relay manifest",
    )
    final_manifest_bytes = read_exact_material_file(
        directory / MANIFEST_NAME,
        expected_mode=0o600,
        label="final relay manifest",
    )
    policy_bytes = read_exact_material_file(
        active / POLICY_NAME,
        expected_mode=0o600,
        label="final relay policy",
    )
    session_bytes = read_exact_material_file(
        active / SESSION_NAME,
        expected_mode=0o600,
        label="final relay session",
    )
    prepared_manifest = _strict_json_bytes(
        prepared_manifest_bytes, label="prepared relay manifest"
    )
    final_manifest = _strict_json_bytes(
        final_manifest_bytes, label="final relay manifest"
    )
    policy = _strict_json_bytes(policy_bytes, label="relay policy")
    session = _strict_json_bytes(session_bytes, label="relay session")
    if (
        prepared_manifest_bytes != _manifest_bytes(prepared_manifest)
        or final_manifest_bytes != _manifest_bytes(final_manifest)
    ):
        raise WitnessRelayMaterialError("relay manifest bytes are not canonical")
    return (
        compose,
        env,
        prepared_manifest_bytes,
        prepared_manifest,
        final_manifest_bytes,
        final_manifest,
        policy_bytes,
        policy,
        session_bytes,
        session,
    )


def _parse_other_bundle(value: str) -> tuple[str, Path, Path]:
    role_and_paths = value.split("=", 1)
    paths = role_and_paths[1].split(",", 1) if len(role_and_paths) == 2 else []
    if (
        len(role_and_paths) != 2
        or len(paths) != 2
        or role_and_paths[0] not in set(ROLES) - {"witness"}
    ):
        raise WitnessRelayMaterialError(
            "--bundle must use non-Witness role=/path/compose.yml,/path/role.env"
        )
    return role_and_paths[0], Path(paths[0]), Path(paths[1])


def _canonical_compose(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WitnessRelayMaterialError("canonical Compose is invalid")
    return value


def _base_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument("--base-witness-compose", type=Path, required=True)
    parser.add_argument("--base-witness-env", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)


def _load_base(args: argparse.Namespace) -> tuple[dict[str, Any], bytes, bytes, dict[str, Any]]:
    return (
        _canonical_compose(args.canonical_compose),
        read_exact_material_file(
            args.base_witness_compose,
            expected_mode=0o640,
            label="base Witness Compose",
        ),
        read_exact_material_file(
            args.base_witness_env,
            expected_mode=0o600,
            label="base Witness environment",
        ),
        read_exact_json_file(args.inventory, label="provisioned inventory"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    derive = commands.add_parser("derive-prepared")
    _base_args(derive)
    derive.add_argument("--approval-policy", type=Path, required=True)
    derive.add_argument("--revision-id", required=True)
    derive.add_argument("--material-directory", required=True)
    derive.add_argument("--relay-key-id", required=True)
    derive.add_argument("--relay-secret-file", type=Path, required=True)
    derive.add_argument("--output-directory", type=Path, required=True)

    prepared = commands.add_parser("validate-prepared")
    _base_args(prepared)
    prepared.add_argument("--approval", type=Path, required=True)
    prepared.add_argument("--approval-policy", type=Path, required=True)
    prepared.add_argument("--bundle", action="append", required=True)
    prepared.add_argument("--prepared-directory", type=Path, required=True)
    prepared.add_argument("--witness-relay-public-key-file", type=Path)

    finalize = commands.add_parser("finalize")
    _base_args(finalize)
    finalize.add_argument("--prepared-directory", type=Path, required=True)
    finalize.add_argument("--session", type=Path, required=True)
    finalize.add_argument("--policy", type=Path, required=True)
    finalize.add_argument("--output-directory", type=Path, required=True)

    final = commands.add_parser("validate-final")
    _base_args(final)
    final.add_argument("--final-directory", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        canonical, base_compose, base_env, inventory = _load_base(args)
        if args.command == "derive-prepared":
            policy = read_exact_json_file(
                args.approval_policy, label="approval policy"
            )
            relay_secret = _read_relay_secret_file(args.relay_secret_file)
            compose, env, manifest = derive_prepared_revision(
                canonical_compose=canonical,
                base_compose_bytes=base_compose,
                base_env_bytes=base_env,
                inventory=inventory,
                approval_policy=policy,
                revision_id=args.revision_id,
                material_directory=args.material_directory,
                relay_key_id=args.relay_key_id,
                relay_secret=relay_secret,
            )
            _publish_new_bundle(
                args.output_directory,
                {
                    COMPOSE_NAME: (compose, 0o640),
                    ENV_NAME: (env, 0o600),
                    MANIFEST_NAME: (_manifest_bytes(manifest), 0o600),
                },
            )
            result = {
                "status": PREPARED_STATUS,
                "stage": "prepared",
                "revision_id": manifest["revision_id"],
                "output_directory": str(args.output_directory),
                "file_attestation": False,
                "image_attestation": False,
                "activation": False,
            }
        elif args.command == "validate-prepared":
            compose, env, _manifest_raw, manifest = _read_prepared_directory(
                args.prepared_directory
            )
            parsed = [_parse_other_bundle(value) for value in args.bundle]
            if len(parsed) != 3 or len({role for role, _c, _e in parsed}) != 3:
                raise WitnessRelayMaterialError(
                    "exactly three distinct unchanged role bundles are required"
                )
            other_bundles = {
                role: (
                    read_exact_material_file(
                        compose_path,
                        expected_mode=0o640,
                        label=f"{role} Compose",
                    ),
                    read_exact_material_file(
                        env_path,
                        expected_mode=0o600,
                        label=f"{role} environment",
                    ),
                )
                for role, compose_path, env_path in parsed
            }
            result = validate_prepared_campaign(
                canonical_compose=canonical,
                base_compose_bytes=base_compose,
                base_env_bytes=base_env,
                prepared_compose_bytes=compose,
                prepared_env_bytes=env,
                manifest=manifest,
                inventory=inventory,
                approval=read_exact_json_file(
                    args.approval, label="inventory approval"
                ),
                approval_policy=read_exact_json_file(
                    args.approval_policy, label="approval policy"
                ),
                other_bundles=other_bundles,
                witness_relay_public_key=(
                    read_exact_material_file(
                        args.witness_relay_public_key_file,
                        expected_mode=0o600,
                        label="pinned Witness relay public key",
                    )
                    .decode("utf-8")
                    .strip()
                    if args.witness_relay_public_key_file is not None
                    else None
                ),
            )
        elif args.command == "finalize":
            (
                compose,
                env,
                prepared_manifest_bytes,
                prepared_manifest,
            ) = _read_prepared_directory(args.prepared_directory)
            policy_bytes = read_exact_material_file(
                args.policy,
                expected_mode=0o600,
                label="relay policy",
            )
            session_bytes = read_exact_material_file(
                args.session,
                expected_mode=0o600,
                label="relay session",
            )
            policy = _strict_json_bytes(policy_bytes, label="relay policy")
            session = _strict_json_bytes(session_bytes, label="relay session")
            final_manifest = finalize_revision(
                canonical_compose=canonical,
                base_compose_bytes=base_compose,
                base_env_bytes=base_env,
                prepared_compose_bytes=compose,
                prepared_env_bytes=env,
                prepared_manifest=prepared_manifest,
                prepared_manifest_bytes=prepared_manifest_bytes,
                inventory=inventory,
                policy=policy,
                policy_bytes=policy_bytes,
                session=session,
                session_bytes=session_bytes,
            )
            _publish_new_bundle(
                args.output_directory,
                {
                    COMPOSE_NAME: (compose, 0o640),
                    ENV_NAME: (env, 0o600),
                    f"{ACTIVE_DIRECTORY_NAME}/{SESSION_NAME}": (
                        session_bytes,
                        0o600,
                    ),
                    f"{ACTIVE_DIRECTORY_NAME}/{POLICY_NAME}": (
                        policy_bytes,
                        0o600,
                    ),
                    PREPARED_MANIFEST_NAME: (prepared_manifest_bytes, 0o600),
                    MANIFEST_NAME: (_manifest_bytes(final_manifest), 0o600),
                },
            )
            result = {
                "status": FINAL_STATUS,
                "stage": "final",
                "revision_id": final_manifest["revision_id"],
                "session_expires_at": final_manifest["final"]["expires_at"],
                "output_directory": str(args.output_directory),
                "file_attestation": False,
                "image_attestation": False,
                "activation": False,
            }
        else:
            (
                compose,
                env,
                prepared_manifest_bytes,
                prepared_manifest,
                _final_manifest_bytes,
                final_manifest,
                policy_bytes,
                policy,
                session_bytes,
                session,
            ) = _read_final_directory(args.final_directory)
            result = verify_final_structure(
                canonical_compose=canonical,
                base_compose_bytes=base_compose,
                base_env_bytes=base_env,
                final_compose_bytes=compose,
                final_env_bytes=env,
                prepared_manifest=prepared_manifest,
                prepared_manifest_bytes=prepared_manifest_bytes,
                final_manifest=final_manifest,
                inventory=inventory,
                policy=policy,
                policy_bytes=policy_bytes,
                session=session,
                session_bytes=session_bytes,
            )
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
