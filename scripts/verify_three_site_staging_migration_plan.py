#!/usr/bin/env python3
"""Verify the independently signed, evidence-bound three-site staging cutover plan."""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from scripts.run_three_site_staging_source_backup import verify_backup_manifest
from scripts.verify_three_site_staging_inventory import (
    _canonical_bytes,
    load_inventory,
    verify_signed_inventory,
)
from scripts.verify_three_site_staging_image_inventory import verify_image_document


SOURCE_ROLES = ("bot_fi", "webapp_fi")
TARGET_SEED_MAP = {
    "bot_fi": ("bot_fi", "restore"),
    "webapp_fi": ("webapp_fi", "restore"),
    "webapp_ir": ("webapp_fi", "clone"),
    "witness": (None, "empty"),
}
ORDERED_PHASES = (
    "validate_campaign_inputs",
    "attest_legacy_sources_quiesced",
    "verify_encrypted_seed_readback",
    "restore_target_databases",
    "restore_target_uploads_and_audit",
    "upgrade_reconciliation_migration",
    "provision_least_privilege_roles",
    "activate_database_fencing",
    "start_private_receivers_and_witness",
    "start_projection_delivery_blob_and_effect_workers",
    "prove_initial_convergence",
    "start_public_apps_with_routing_held",
    "run_acceptance_smoke",
    "commit_or_forward_rollback",
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
SUPPORTED_SOURCE_REVISIONS = frozenset(
    {
        "f1b6e7f8a9dc",  # immutable main parent used by this integration
        "a274f5a6b8c9",  # Telegram queue parent
        "e9e4f5a6b7c8",  # three-site DR parent
        "c431d2e3f5a6",  # reconciled integration head (repeat/recovery input)
        "e653f4a5b7c8",  # hardened integration head (repeat/recovery input)
    }
)


class MigrationPlanError(RuntimeError):
    pass


def _utc(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise MigrationPlanError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise MigrationPlanError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _strict_load(path: Path) -> dict[str, Any]:
    return load_inventory(path)


def _verify_signatures(
    plan: dict[str, Any],
    *,
    approval: dict[str, Any],
    signer_policy: dict[str, Any],
    release_sha: str,
    now: datetime,
) -> dict[str, Any]:
    policy_fields = {"schema", "policy_id", "release_sha", "signers"}
    if (
        not isinstance(signer_policy, dict)
        or set(signer_policy) != policy_fields
        or signer_policy.get("schema") != "three-site-staging-inventory-signers-v1"
        or str(signer_policy.get("release_sha", "")).lower() != release_sha
    ):
        raise MigrationPlanError("migration signer policy is invalid or release-mismatched")
    try:
        UUID(str(signer_policy["policy_id"]))
    except ValueError as exc:
        raise MigrationPlanError("migration signer policy_id must be a UUID") from exc
    signers: dict[str, tuple[str, str, bytes]] = {}
    operators: set[str] = set()
    custody_domains: set[str] = set()
    public_keys: set[bytes] = set()
    raw_signers = signer_policy["signers"]
    if not isinstance(raw_signers, list) or len(raw_signers) < 2:
        raise MigrationPlanError("migration policy requires at least two signers")
    for item in raw_signers:
        if not isinstance(item, dict) or set(item) != {
            "key_id", "operator", "custody_domain", "public_key"
        }:
            raise MigrationPlanError("migration signer fields are invalid")
        key_id = str(item["key_id"]).strip()
        operator = str(item["operator"]).strip()
        custody = str(item["custody_domain"]).strip()
        try:
            public_key = base64.b64decode(str(item["public_key"]), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise MigrationPlanError("migration signer public key is invalid") from exc
        if (
            not key_id
            or not operator
            or not custody
            or key_id in signers
            or len(public_key) != 32
            or operator in operators
            or custody in custody_domains
            or public_key in public_keys
        ):
            raise MigrationPlanError("migration signer identities/keys are not independent")
        signers[key_id] = (operator, custody, public_key)
        operators.add(operator)
        custody_domains.add(custody)
        public_keys.add(public_key)

    fields = {
        "schema", "plan_sha256", "release_sha", "policy_hash",
        "signed_at", "expires_at", "approvals",
    }
    if (
        not isinstance(approval, dict)
        or set(approval) != fields
        or approval.get("schema") != "three-site-staging-migration-approval-v1"
        or approval.get("plan_sha256") != hashlib.sha256(_canonical_bytes(plan)).hexdigest()
        or str(approval.get("release_sha", "")).lower() != release_sha
        or approval.get("policy_hash") != hashlib.sha256(
            _canonical_bytes(signer_policy)
        ).hexdigest()
    ):
        raise MigrationPlanError("migration approval is not bound to plan/policy/release")
    signed_at = _utc(approval["signed_at"], label="migration signed_at")
    expires_at = _utc(approval["expires_at"], label="migration expires_at")
    if (
        signed_at > now + timedelta(minutes=5)
        or expires_at <= now
        or expires_at <= signed_at
        or expires_at - signed_at > timedelta(hours=4)
    ):
        raise MigrationPlanError("migration approval is expired or outside its four-hour window")
    raw_approvals = approval["approvals"]
    if not isinstance(raw_approvals, list) or len(raw_approvals) != 2:
        raise MigrationPlanError("migration plan requires exactly two approvals")
    message = _canonical_bytes({name: approval[name] for name in fields - {"approvals"}})
    operators = set()
    custody_domains = set()
    key_ids: set[str] = set()
    for item in raw_approvals:
        if not isinstance(item, dict) or set(item) != {"operator", "key_id", "signature"}:
            raise MigrationPlanError("migration approval entry fields are invalid")
        key_id = str(item["key_id"]).strip()
        operator = str(item["operator"]).strip()
        signer = signers.get(key_id)
        if (
            signer is None
            or operator != signer[0]
            or key_id in key_ids
            or operator in operators
            or signer[1] in custody_domains
        ):
            raise MigrationPlanError("migration approvals are not independent authorized signers")
        try:
            signature = base64.b64decode(str(item["signature"]), validate=True)
            Ed25519PublicKey.from_public_bytes(signer[2]).verify(signature, message)
        except (ValueError, binascii.Error, InvalidSignature) as exc:
            raise MigrationPlanError("migration approval signature is invalid") from exc
        key_ids.add(key_id)
        operators.add(operator)
        custody_domains.add(signer[1])
    return {"approved_by": sorted(operators), "approval_expires_at": expires_at.isoformat()}


def verify_migration_plan(
    plan: dict[str, Any],
    *,
    approval: dict[str, Any],
    inventory: dict[str, Any],
    inventory_approval: dict[str, Any],
    signer_policy: dict[str, Any],
    freeze_evidence: list[dict[str, Any]],
    image_inventories: dict[str, dict[str, Any]],
    backup_manifests: dict[str, dict[str, Any]],
    seed_manifests: dict[str, dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    inventory_result = verify_signed_inventory(
        inventory,
        approval=inventory_approval,
        signer_policy=signer_policy,
        host_destructive=True,
        now=current,
    )
    if inventory_result["inventory_stage"] != "provisioned":
        raise MigrationPlanError("migration requires the freshly signed provisioned inventory")
    fields = {
        "schema", "campaign_id", "release_sha", "deployment_id",
        "provisioned_inventory_sha256", "created_at", "not_after",
        "source_freeze", "source_backups", "seed_bundles", "target_seed_map",
        "image_inventories", "ordered_phases", "rollback_policy",
    }
    if (
        not isinstance(plan, dict)
        or set(plan) != fields
        or plan.get("schema") != "three-site-staging-migration-plan-v1"
        or plan.get("campaign_id") != inventory_result["campaign_id"]
        or str(plan.get("release_sha", "")).lower() != inventory_result["release_sha"]
        or plan.get("deployment_id") != inventory_result["deployment_id"]
        or plan.get("provisioned_inventory_sha256") != inventory_result["inventory_sha256"]
    ):
        raise MigrationPlanError("migration plan identity is invalid or inventory-mismatched")
    created_at = _utc(plan["created_at"], label="migration plan created_at")
    not_after = _utc(plan["not_after"], label="migration plan not_after")
    if not_after <= created_at or not_after - created_at > timedelta(hours=4):
        raise MigrationPlanError("migration plan validity exceeds four hours")
    if not (created_at - timedelta(minutes=5) <= current < not_after):
        raise MigrationPlanError("migration plan is not currently valid")

    freeze = plan["source_freeze"]
    if (
        not isinstance(freeze, dict)
        or set(freeze) != {"required", "evidence", "redis_restore"}
        or freeze["required"] is not True
        or freeze["redis_restore"] is not False
        or not isinstance(freeze["evidence"], list)
        or not freeze["evidence"]
    ):
        raise MigrationPlanError("migration requires bound source-quiescence evidence and no Redis restore")

    source_rows = plan["source_backups"]
    if not isinstance(source_rows, list) or len(source_rows) != len(SOURCE_ROLES):
        raise MigrationPlanError("migration requires exactly two source backups")
    source_by_role: dict[str, dict[str, Any]] = {}
    target_system_ids = {str(role["postgres_system_id"]) for role in inventory["roles"]}
    production_system_ids = {
        str(value).lower()
        for value in inventory["production_boundaries"]["postgres_system_ids"]
    }
    for row in source_rows:
        expected = {
            "source_role", "source_release_sha", "manifest_sha256",
            "postgres_system_id", "alembic_revision", "database_fingerprint_sha256",
        }
        if not isinstance(row, dict) or set(row) != expected:
            raise MigrationPlanError("source-backup plan fields are invalid")
        role = str(row["source_role"])
        if role not in SOURCE_ROLES or role in source_by_role:
            raise MigrationPlanError("source-backup role is unknown or duplicate")
        if (
            not SHA_RE.fullmatch(str(row["source_release_sha"]))
            or not SHA256_RE.fullmatch(str(row["manifest_sha256"]))
            or not re.fullmatch(r"[0-9]{10,20}", str(row["postgres_system_id"]))
            or not REVISION_RE.fullmatch(str(row["alembic_revision"]))
            or str(row["alembic_revision"]) not in SUPPORTED_SOURCE_REVISIONS
            or not SHA256_RE.fullmatch(str(row["database_fingerprint_sha256"]))
            or str(row["postgres_system_id"]) in target_system_ids
            or str(row["postgres_system_id"]).lower() in production_system_ids
        ):
            raise MigrationPlanError("source-backup identity/hash is unsafe")
        manifest = backup_manifests.get(role)
        if manifest is None or hashlib.sha256(_canonical_bytes(manifest)).hexdigest() != row["manifest_sha256"]:
            raise MigrationPlanError("source-backup manifest bytes differ from the signed plan")
        verified = verify_backup_manifest(
            manifest,
            campaign_id=inventory_result["campaign_id"],
            source_role=role,
            source_release_sha=str(row["source_release_sha"]),
            target_release_sha=inventory_result["release_sha"],
            verify_files=False,
        )
        if (
            manifest["source_postgres_system_id"] != row["postgres_system_id"]
            or manifest["source_alembic_revision"] != row["alembic_revision"]
            or verified["database_fingerprint_sha256"] != row["database_fingerprint_sha256"]
        ):
            raise MigrationPlanError("source-backup evidence differs from the signed plan")
        source_by_role[role] = row
    if set(source_by_role) != set(SOURCE_ROLES):
        raise MigrationPlanError("source-backup role set is incomplete")

    planned_freeze_rows: dict[str, tuple[str, ...]] = {}
    for row in freeze["evidence"]:
        if not isinstance(row, dict) or set(row) != {"evidence_sha256", "source_roles"}:
            raise MigrationPlanError("source-freeze plan evidence fields are invalid")
        digest = str(row["evidence_sha256"])
        roles = row["source_roles"]
        if (
            not SHA256_RE.fullmatch(digest)
            or digest in planned_freeze_rows
            or not isinstance(roles, list)
            or not roles
            or any(role not in SOURCE_ROLES for role in roles)
            or len(set(roles)) != len(roles)
        ):
            raise MigrationPlanError("source-freeze plan evidence is invalid or duplicate")
        planned_freeze_rows[digest] = tuple(sorted(roles))
    observed_freeze_rows: dict[str, tuple[str, ...]] = {}
    roles_from_freeze: set[str] = set()
    for evidence in freeze_evidence:
        digest = hashlib.sha256(_canonical_bytes(evidence)).hexdigest()
        fields = {
            "schema", "campaign_id", "target_release_sha", "project_name", "observed_at",
            "source_roles", "previously_running_services", "stopped_services",
            "running_services", "postgres", "redis_observation", "legacy_restore_bundle",
        }
        if (
            not isinstance(evidence, dict)
            or set(evidence) != fields
            or evidence["schema"] != "three-site-staging-source-freeze-v1"
            or evidence["campaign_id"] != inventory_result["campaign_id"]
            or evidence["target_release_sha"] != inventory_result["release_sha"]
            or evidence["project_name"] != "trading_bot_staging"
            or evidence["running_services"] != ["db", "redis"]
            or not isinstance(evidence["stopped_services"], list)
            or "db" in evidence["stopped_services"]
            or "redis" in evidence["stopped_services"]
            or digest in observed_freeze_rows
            or not isinstance(evidence["legacy_restore_bundle"], dict)
            or set(evidence["legacy_restore_bundle"])
            != {"schema", "path", "sha256", "size"}
            or evidence["legacy_restore_bundle"].get("schema")
            != "three-site-staging-legacy-restore-bundle-reference-v1"
            or not Path(str(evidence["legacy_restore_bundle"].get("path", ""))).is_absolute()
            or not SHA256_RE.fullmatch(
                str(evidence["legacy_restore_bundle"].get("sha256", ""))
            )
            or type(evidence["legacy_restore_bundle"].get("size")) is not int
            or not 1 <= evidence["legacy_restore_bundle"]["size"] <= 1024 * 1024
        ):
            raise MigrationPlanError("source-freeze evidence identity/state is invalid")
        observed_at = _utc(evidence["observed_at"], label="source-freeze observed_at")
        rows = evidence["source_roles"]
        if not isinstance(rows, list) or not rows:
            raise MigrationPlanError("source-freeze role rows are invalid")
        document_roles: list[str] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "source_role", "app_service", "source_release_sha"
            }:
                raise MigrationPlanError("source-freeze role row fields are invalid")
            role = str(row["source_role"])
            expected_app = "foreign_app" if role == "bot_fi" else "app"
            if (
                role not in SOURCE_ROLES
                or role in roles_from_freeze
                or row["app_service"] != expected_app
                or row["source_release_sha"] != source_by_role[role]["source_release_sha"]
            ):
                raise MigrationPlanError("source-freeze role identity is invalid or duplicate")
            backup = backup_manifests[role]
            backup_at = _utc(backup["created_at"], label="source backup created_at")
            postgres = evidence["postgres"]
            if (
                not isinstance(postgres, dict)
                or postgres.get("system_id") != backup["source_postgres_system_id"]
                or postgres.get("alembic_revision") != backup["source_alembic_revision"]
                or postgres.get("database_fingerprint_sha256")
                != backup["restore_drill"]["database_fingerprint_sha256"]
                or backup["source_freeze_evidence_sha256"] != digest
                or backup["redis_observation"] != evidence["redis_observation"]
                or backup_at < observed_at
                or backup_at - observed_at > timedelta(hours=1)
            ):
                raise MigrationPlanError("source backup does not match its frozen source evidence")
            roles_from_freeze.add(role)
            document_roles.append(role)
        observed_freeze_rows[digest] = tuple(sorted(document_roles))
    if roles_from_freeze != set(SOURCE_ROLES) or observed_freeze_rows != planned_freeze_rows:
        raise MigrationPlanError("source-freeze evidence set differs from the signed plan")

    image_rows = plan["image_inventories"]
    if not isinstance(image_rows, list) or len(image_rows) != len(TARGET_SEED_MAP):
        raise MigrationPlanError("migration requires four image inventories")
    observed_image_ids: dict[str, str] = {}
    observed_repo_digests: dict[str, tuple[str, ...]] = {}
    image_inventory_hashes: dict[str, str] = {}
    seen_image_roles: set[str] = set()
    for row in image_rows:
        expected = {
            "role", "document_sha256", "role_compose_sha256", "role_env_sha256"
        }
        if not isinstance(row, dict) or set(row) != expected:
            raise MigrationPlanError("image-inventory plan fields are invalid")
        role = str(row["role"])
        if (
            role not in TARGET_SEED_MAP
            or role in seen_image_roles
            or not SHA256_RE.fullmatch(str(row["document_sha256"]))
            or not SHA256_RE.fullmatch(str(row["role_compose_sha256"]))
            or not SHA256_RE.fullmatch(str(row["role_env_sha256"]))
        ):
            raise MigrationPlanError("image-inventory role/hash is invalid or duplicate")
        document = image_inventories.get(role)
        if document is None or hashlib.sha256(_canonical_bytes(document)).hexdigest() != row["document_sha256"]:
            raise MigrationPlanError("image-inventory bytes differ from the signed plan")
        result = verify_image_document(
            document,
            role=role.replace("_", "-"),
            campaign_id=inventory_result["campaign_id"],
            release_sha=inventory_result["release_sha"],
            role_compose_sha256=str(row["role_compose_sha256"]),
            role_env_sha256=str(row["role_env_sha256"]),
        )
        for reference, image_id in result["image_ids"].items():
            previous_id = observed_image_ids.setdefault(reference, image_id)
            if previous_id != image_id:
                raise MigrationPlanError("same image reference resolves to different bytes across roles")
        for item in document["images"]:
            reference = str(item["reference"])
            digests = tuple(item["repo_digests"])
            previous = observed_repo_digests.setdefault(reference, digests)
            if previous != digests:
                raise MigrationPlanError("same image reference has different provider digests across roles")
        seen_image_roles.add(role)
        image_inventory_hashes[role] = str(row["document_sha256"])
    if seen_image_roles != set(TARGET_SEED_MAP) or set(image_inventories) != set(TARGET_SEED_MAP):
        raise MigrationPlanError("image-inventory role set is incomplete")

    bundle_rows = plan["seed_bundles"]
    if not isinstance(bundle_rows, list) or len(bundle_rows) != len(SOURCE_ROLES):
        raise MigrationPlanError("migration requires exactly two encrypted seed bundles")
    seen_bundles: set[str] = set()
    inventory_storage = inventory["object_storage"]
    for row in bundle_rows:
        expected = {
            "source_role", "manifest_sha256", "object_prefix", "encryption",
            "recipient_fingerprint", "readback_evidence_sha256",
        }
        if not isinstance(row, dict) or set(row) != expected:
            raise MigrationPlanError("seed-bundle plan fields are invalid")
        role = str(row["source_role"])
        if role not in SOURCE_ROLES or role in seen_bundles:
            raise MigrationPlanError("seed-bundle role is unknown or duplicate")
        if (
            row["encryption"] != "age-x25519"
            or not str(row["object_prefix"]).startswith(str(inventory_storage["prefix"]))
            or not SHA256_RE.fullmatch(str(row["manifest_sha256"]))
            or not SHA256_RE.fullmatch(str(row["recipient_fingerprint"]))
            or not SHA256_RE.fullmatch(str(row["readback_evidence_sha256"]))
        ):
            raise MigrationPlanError("encrypted seed-bundle identity is invalid")
        manifest = seed_manifests.get(role)
        if manifest is None or hashlib.sha256(_canonical_bytes(manifest)).hexdigest() != row["manifest_sha256"]:
            raise MigrationPlanError("seed manifest bytes differ from the signed plan")
        required_manifest = {
            "schema", "campaign_id", "release_sha", "source_role", "bucket",
            "object_prefix", "encryption", "recipient_fingerprint", "objects",
            "readback_evidence_sha256",
        }
        objects = manifest.get("objects") if isinstance(manifest, dict) else None
        if (
            set(manifest) != required_manifest
            or manifest["schema"] != "three-site-staging-seed-manifest-v1"
            or manifest["campaign_id"] != inventory_result["campaign_id"]
            or manifest["release_sha"] != inventory_result["release_sha"]
            or manifest["source_role"] != role
            or manifest["bucket"] != inventory_storage["bucket"]
            or manifest["object_prefix"] != row["object_prefix"]
            or manifest["encryption"] != row["encryption"]
            or manifest["recipient_fingerprint"] != row["recipient_fingerprint"]
            or manifest["readback_evidence_sha256"] != row["readback_evidence_sha256"]
            or not isinstance(objects, list)
            or len(objects) != 3
        ):
            raise MigrationPlanError("seed manifest identity is invalid")
        kinds: set[str] = set()
        keys: set[str] = set()
        for item in objects:
            if not isinstance(item, dict) or set(item) != {
                "kind", "object_key", "version_id", "plaintext_sha256",
                "plaintext_bytes", "ciphertext_sha256", "ciphertext_bytes",
            }:
                raise MigrationPlanError("seed object fields are invalid")
            kind = str(item["kind"])
            key = str(item["object_key"])
            version = str(item["version_id"])
            if (
                kind not in {"postgres", "uploads", "audit"}
                or kind in kinds
                or key in keys
                or not key.startswith(str(row["object_prefix"]))
                or not version
                or not SHA256_RE.fullmatch(str(item["plaintext_sha256"]))
                or not SHA256_RE.fullmatch(str(item["ciphertext_sha256"]))
                or type(item["plaintext_bytes"]) is not int
                or int(item["plaintext_bytes"]) <= 0
                or type(item["ciphertext_bytes"]) is not int
                or int(item["ciphertext_bytes"]) <= int(item["plaintext_bytes"])
            ):
                raise MigrationPlanError("seed object identity/hash/size is invalid")
            backup_artifact = backup_manifests[role]["artifacts"][kind]
            if (
                item["plaintext_sha256"] != backup_artifact["sha256"]
                or item["plaintext_bytes"] != backup_artifact["bytes"]
            ):
                raise MigrationPlanError("seed plaintext differs from source backup artifact")
            kinds.add(kind)
            keys.add(key)
        if kinds != {"postgres", "uploads", "audit"}:
            raise MigrationPlanError("seed object set is incomplete")
        seen_bundles.add(role)

    mappings = plan["target_seed_map"]
    if not isinstance(mappings, list) or len(mappings) != len(TARGET_SEED_MAP):
        raise MigrationPlanError("target seed map is incomplete")
    observed_map: dict[str, tuple[str | None, str]] = {}
    for row in mappings:
        if not isinstance(row, dict) or set(row) != {"target_role", "source_role", "mode"}:
            raise MigrationPlanError("target seed mapping fields are invalid")
        target = str(row["target_role"])
        mapping = (row["source_role"], str(row["mode"]))
        if target in observed_map:
            raise MigrationPlanError("target seed mapping is duplicate")
        observed_map[target] = mapping
    if observed_map != TARGET_SEED_MAP:
        raise MigrationPlanError("target seed map differs from the reviewed topology")
    if tuple(plan["ordered_phases"]) != ORDERED_PHASES:
        raise MigrationPlanError("migration phase order differs from the reviewed state machine")
    rollback = plan["rollback_policy"]
    if rollback != {
        "strategy": "forward-rollback-or-separate-restore",
        "alembic_downgrade": False,
        "routing_held_until_commit": True,
        "legacy_sources_retained": True,
        "cleanup_requires_explicit_finish": True,
    }:
        raise MigrationPlanError("migration rollback policy is unsafe")
    signed = _verify_signatures(
        plan,
        approval=approval,
        signer_policy=signer_policy,
        release_sha=inventory_result["release_sha"],
        now=current,
    )
    return {
        "status": "approved",
        "campaign_id": inventory_result["campaign_id"],
        "release_sha": inventory_result["release_sha"],
        "inventory_sha256": inventory_result["inventory_sha256"],
        "plan_sha256": hashlib.sha256(_canonical_bytes(plan)).hexdigest(),
        "source_roles": list(SOURCE_ROLES),
        "target_roles": sorted(TARGET_SEED_MAP),
        "image_inventory_sha256": image_inventory_hashes,
        **signed,
    }


def _mapping(
    values: list[str], *, label: str, roles: tuple[str, ...] = SOURCE_ROLES
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        role, separator, raw_path = value.partition("=")
        if not separator or role not in roles or role in result or not raw_path:
            raise MigrationPlanError(f"{label} must use one unique source_role=/path mapping")
        result[role] = _strict_load(Path(raw_path))
    if set(result) != set(roles):
        raise MigrationPlanError(f"{label} role set is incomplete")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--signer-policy", type=Path, required=True)
    parser.add_argument("--freeze-evidence", action="append", type=Path, required=True)
    parser.add_argument("--image-inventory", action="append", required=True)
    parser.add_argument("--backup-manifest", action="append", required=True)
    parser.add_argument("--seed-manifest", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_migration_plan(
            _strict_load(args.plan),
            approval=_strict_load(args.approval),
            inventory=_strict_load(args.inventory),
            inventory_approval=_strict_load(args.inventory_approval),
            signer_policy=_strict_load(args.signer_policy),
            freeze_evidence=[_strict_load(path) for path in args.freeze_evidence],
            image_inventories=_mapping(
                args.image_inventory,
                label="--image-inventory",
                roles=tuple(TARGET_SEED_MAP),
            ),
            backup_manifests=_mapping(args.backup_manifest, label="--backup-manifest"),
            seed_manifests=_mapping(args.seed_manifest, label="--seed-manifest"),
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
