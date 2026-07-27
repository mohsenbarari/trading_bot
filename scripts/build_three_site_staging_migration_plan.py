#!/usr/bin/env python3
"""Build a deterministic, evidence-bound three-site staging migration plan."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from core.three_site_full_matrix_campaign import secure_json
from core.three_site_staging_source_contract import legacy_staging_project_allowed
from scripts.run_three_site_staging_source_backup import verify_backup_manifest
from scripts.verify_three_site_staging_image_inventory import verify_image_document
from scripts.verify_three_site_staging_inventory import verify_approved_inventory
from scripts.verify_three_site_staging_inventory import (
    _canonical_bytes as migration_canonical_bytes,
)
from scripts.verify_three_site_staging_migration_plan import (
    ORDERED_PHASES,
    SOURCE_RECIPIENT_TARGETS,
    SOURCE_ROLES,
    SUPPORTED_SOURCE_REVISIONS,
    TARGET_SEED_MAP,
    migration_approval_subject,
)


SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SYSTEM_ID = re.compile(r"^[0-9]{10,20}$")
REVISION = re.compile(r"^[0-9a-f]{12}$")
TARGET_ROLES = tuple(TARGET_SEED_MAP)
SEED_KINDS = frozenset({"postgres", "uploads", "audit"})


class MigrationPlanBuildError(RuntimeError):
    """Raised when source evidence cannot produce one exact migration plan."""


def _utc(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MigrationPlanBuildError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise MigrationPlanBuildError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(migration_canonical_bytes(value)).hexdigest()


def _publish_exact_or_new(
    path: Path,
    payload: bytes,
    *,
    label: str,
    max_size: int,
) -> None:
    """Publish once, or resume only when the existing owner-only bytes match."""
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=0o600,
            max_size=max_size,
        )
    except SecureFileError:
        existing = read_secure_bytes(path, label=label, max_size=max_size)
        if existing != payload:
            raise MigrationPlanBuildError(
                f"existing {label} differs; refusing overwrite"
            )


def _documents_by_role(
    values: list[str],
    *,
    label: str,
    roles: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        role, separator, raw_path = value.partition("=")
        if not separator or role not in roles or role in result or not raw_path:
            raise MigrationPlanBuildError(
                f"{label} requires one unique role=/root-only/path mapping"
            )
        result[role] = secure_json(Path(raw_path), label=f"{label} {role}")
    if set(result) != set(roles):
        raise MigrationPlanBuildError(f"{label} role set is incomplete")
    return result


def _freeze_rows(
    freezes: dict[str, dict[str, Any]],
    *,
    campaign_id: str,
    release_sha: str,
    backups: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    expected_fields = {
        "schema",
        "campaign_id",
        "target_release_sha",
        "project_name",
        "observed_at",
        "source_roles",
        "previously_running_services",
        "stopped_services",
        "running_services",
        "postgres",
        "redis_observation",
        "legacy_restore_bundle",
    }
    for role in SOURCE_ROLES:
        evidence = freezes[role]
        role_rows = evidence.get("source_roles") if isinstance(evidence, dict) else None
        postgres = evidence.get("postgres") if isinstance(evidence, dict) else None
        redis = evidence.get("redis_observation") if isinstance(evidence, dict) else None
        restore_bundle = (
            evidence.get("legacy_restore_bundle") if isinstance(evidence, dict) else None
        )
        backup = backups[role]
        expected_app = "foreign_app" if role == "bot_fi" else "app"
        if (
            set(evidence) != expected_fields
            or evidence.get("schema") != "three-site-staging-source-freeze-v1"
            or evidence.get("campaign_id") != campaign_id
            or evidence.get("target_release_sha") != release_sha
            or not legacy_staging_project_allowed(
                str(evidence.get("project_name") or ""), (role,)
            )
            or evidence.get("running_services") != ["db", "redis"]
            or not isinstance(evidence.get("stopped_services"), list)
            or "db" in evidence["stopped_services"]
            or "redis" in evidence["stopped_services"]
            or not isinstance(role_rows, list)
            or len(role_rows) != 1
            or not isinstance(role_rows[0], dict)
            or set(role_rows[0])
            != {"source_role", "app_service", "source_release_sha"}
            or role_rows[0].get("source_role") != role
            or role_rows[0].get("app_service") != expected_app
            or role_rows[0].get("source_release_sha")
            != backup.get("source_release_sha")
            or not isinstance(postgres, dict)
            or set(postgres)
            != {
                "system_id",
                "alembic_revision",
                "database_fingerprint_sha256",
                "database_row_count",
                "public_table_count",
            }
            or postgres.get("system_id") != backup.get("source_postgres_system_id")
            or postgres.get("alembic_revision") != backup.get("source_alembic_revision")
            or not isinstance(backup.get("restore_drill"), dict)
            or postgres.get("database_fingerprint_sha256")
            != backup["restore_drill"].get("database_fingerprint_sha256")
            or not isinstance(redis, dict)
            or redis != backup.get("redis_observation")
            or not isinstance(restore_bundle, dict)
            or set(restore_bundle) != {"schema", "path", "sha256", "size"}
            or restore_bundle.get("schema")
            != "three-site-staging-legacy-restore-bundle-reference-v1"
            or not Path(str(restore_bundle.get("path") or "")).is_absolute()
            or SHA256.fullmatch(str(restore_bundle.get("sha256") or "")) is None
            or type(restore_bundle.get("size")) is not int
            or not 1 <= restore_bundle["size"] <= 1024 * 1024
        ):
            raise MigrationPlanBuildError(
                f"{role} freeze evidence is not exact for this campaign"
            )
        digest = _canonical_hash(evidence)
        observed_at = _utc(str(evidence["observed_at"]), label=f"{role} freeze observed_at")
        backup_at = _utc(str(backup.get("created_at") or ""), label=f"{role} backup created_at")
        if backup_at < observed_at or backup_at - observed_at > timedelta(hours=1):
            raise MigrationPlanBuildError(
                f"{role} backup timestamp is outside its freeze window"
            )
        if backup.get("source_freeze_evidence_sha256") != digest:
            raise MigrationPlanBuildError(
                f"{role} backup is not bound to its exact freeze evidence"
            )
        rows.append({"evidence_sha256": digest, "source_roles": [role]})
    return rows


def _backup_rows(
    backups: dict[str, dict[str, Any]],
    *,
    campaign_id: str,
    release_sha: str,
    inventory: dict[str, Any],
) -> list[dict[str, Any]]:
    inventory_roles = inventory.get("roles")
    boundaries = inventory.get("production_boundaries")
    if not isinstance(inventory_roles, list) or not isinstance(boundaries, dict):
        raise MigrationPlanBuildError("inventory source/target boundaries are incomplete")
    target_system_ids = {
        str(row.get("postgres_system_id"))
        for row in inventory_roles
        if isinstance(row, dict)
    }
    protected_system_ids = {
        str(value).lower()
        for value in boundaries.get("postgres_system_ids", [])
    }
    rows: list[dict[str, Any]] = []
    source_system_ids: set[str] = set()
    for role in SOURCE_ROLES:
        manifest = backups[role]
        source_release = str(manifest.get("source_release_sha") or "")
        verify_backup_manifest(
            manifest,
            campaign_id=campaign_id,
            source_role=role,
            source_release_sha=source_release,
            target_release_sha=release_sha,
            verify_files=False,
        )
        system_id = str(manifest.get("source_postgres_system_id") or "")
        revision = str(manifest.get("source_alembic_revision") or "")
        fingerprint = str(
            manifest.get("restore_drill", {}).get("database_fingerprint_sha256")
            if isinstance(manifest.get("restore_drill"), dict)
            else ""
        )
        if (
            SHA40.fullmatch(source_release) is None
            or SYSTEM_ID.fullmatch(system_id) is None
            or REVISION.fullmatch(revision) is None
            or revision not in SUPPORTED_SOURCE_REVISIONS
            or SHA256.fullmatch(fingerprint) is None
            or system_id in target_system_ids
            or system_id.lower() not in protected_system_ids
            or system_id in source_system_ids
        ):
            raise MigrationPlanBuildError(f"{role} backup identity is unsupported")
        source_system_ids.add(system_id)
        rows.append(
            {
                "source_role": role,
                "source_release_sha": source_release,
                "manifest_sha256": _canonical_hash(manifest),
                "postgres_system_id": system_id,
                "alembic_revision": revision,
                "database_fingerprint_sha256": fingerprint,
            }
        )
    if len(source_system_ids) != len(SOURCE_ROLES):
        raise MigrationPlanBuildError("source database system identities are not distinct")
    return rows


def _image_rows(
    images: dict[str, dict[str, Any]],
    *,
    campaign_id: str,
    release_sha: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    content_by_reference: dict[str, str] = {}
    for role in TARGET_ROLES:
        document = images[role]
        compose_hash = str(document.get("role_compose_sha256") or "")
        env_hash = str(document.get("role_env_sha256") or "")
        if SHA256.fullmatch(compose_hash) is None or SHA256.fullmatch(env_hash) is None:
            raise MigrationPlanBuildError(f"{role} image inventory binding is malformed")
        verified = verify_image_document(
            document,
            role=role.replace("_", "-"),
            campaign_id=campaign_id,
            release_sha=release_sha,
            role_compose_sha256=compose_hash,
            role_env_sha256=env_hash,
        )
        for reference, identity in verified["content_identities"].items():
            previous = content_by_reference.setdefault(reference, identity)
            if previous != identity:
                raise MigrationPlanBuildError(
                    "one image reference has different content across target roles"
                )
        rows.append(
            {
                "role": role,
                "document_sha256": _canonical_hash(document),
                "role_compose_sha256": compose_hash,
                "role_env_sha256": env_hash,
            }
        )
    return rows


def _seed_rows(
    seeds: dict[str, dict[str, Any]],
    *,
    campaign_id: str,
    release_sha: str,
    backups: dict[str, dict[str, Any]],
    inventory: dict[str, Any],
) -> list[dict[str, Any]]:
    storage = inventory.get("object_storage")
    if not isinstance(storage, dict):
        raise MigrationPlanBuildError("inventory Object Storage binding is missing")
    bucket = storage.get("bucket")
    prefix = storage.get("prefix")
    if not isinstance(bucket, str) or not bucket or not isinstance(prefix, str):
        raise MigrationPlanBuildError("inventory Object Storage identity is invalid")

    rows: list[dict[str, Any]] = []
    all_keys: set[str] = set()
    all_fingerprints: dict[str, str] = {}
    owner_ids: set[str] = set()
    manifest_fields = {
        "schema",
        "campaign_id",
        "release_sha",
        "source_role",
        "bucket",
        "bucket_owner_id",
        "object_prefix",
        "encryption",
        "recipient_fingerprints",
        "objects",
        "readback_evidence_sha256",
    }
    object_fields = {
        "kind",
        "object_key",
        "version_id",
        "plaintext_sha256",
        "plaintext_bytes",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "publication_intent",
    }
    for role in SOURCE_ROLES:
        manifest = seeds[role]
        fingerprints = manifest.get("recipient_fingerprints")
        objects = manifest.get("objects")
        expected_prefix = f"{prefix}seed-v2/{campaign_id}/{release_sha}/{role}/"
        if (
            set(manifest) != manifest_fields
            or manifest.get("schema") != "three-site-staging-seed-manifest-v2"
            or manifest.get("campaign_id") != campaign_id
            or manifest.get("release_sha") != release_sha
            or manifest.get("source_role") != role
            or manifest.get("bucket") != bucket
            or not str(manifest.get("bucket_owner_id") or "").strip()
            or manifest.get("object_prefix") != expected_prefix
            or manifest.get("encryption") != "age-x25519-multi-recipient"
            or not isinstance(fingerprints, dict)
            or set(fingerprints) != SOURCE_RECIPIENT_TARGETS[role]
            or any(SHA256.fullmatch(str(value)) is None for value in fingerprints.values())
            or len(set(fingerprints.values())) != len(fingerprints)
            or SHA256.fullmatch(str(manifest.get("readback_evidence_sha256") or ""))
            is None
            or not isinstance(objects, list)
            or len(objects) != 3
        ):
            raise MigrationPlanBuildError(f"{role} sealed seed manifest is invalid")
        owner_ids.add(str(manifest["bucket_owner_id"]))
        for target, fingerprint in fingerprints.items():
            previous = all_fingerprints.setdefault(target, str(fingerprint))
            if previous != fingerprint:
                raise MigrationPlanBuildError(
                    "target age recipient fingerprint is inconsistent"
                )
        kinds: set[str] = set()
        for item in objects:
            if not isinstance(item, dict) or set(item) != object_fields:
                raise MigrationPlanBuildError(f"{role} seed object row is invalid")
            kind = str(item.get("kind") or "")
            key = str(item.get("object_key") or "")
            version_id = str(item.get("version_id") or "")
            artifact = backups[role].get("artifacts", {}).get(kind)
            if (
                kind not in SEED_KINDS
                or kind in kinds
                or key != f"{expected_prefix}{kind}.age"
                or key in all_keys
                or not version_id
                or version_id == "null"
                or not isinstance(artifact, dict)
                or item.get("plaintext_sha256") != artifact.get("sha256")
                or item.get("plaintext_bytes") != artifact.get("bytes")
                or SHA256.fullmatch(str(item.get("ciphertext_sha256") or "")) is None
                or SHA256.fullmatch(str(item.get("publication_intent") or "")) is None
                or type(item.get("ciphertext_bytes")) is not int
                or type(item.get("plaintext_bytes")) is not int
                or item["ciphertext_bytes"] <= item["plaintext_bytes"]
            ):
                raise MigrationPlanBuildError(f"{role} seed object is not exact")
            kinds.add(kind)
            all_keys.add(key)
        if kinds != SEED_KINDS:
            raise MigrationPlanBuildError(f"{role} seed object set is incomplete")
        rows.append(
            {
                "source_role": role,
                "manifest_sha256": _canonical_hash(manifest),
                "object_prefix": expected_prefix,
                "encryption": "age-x25519-multi-recipient",
                "recipient_fingerprints": dict(sorted(fingerprints.items())),
                "readback_evidence_sha256": manifest["readback_evidence_sha256"],
            }
        )
    if (
        len(all_keys) != 6
        or len(owner_ids) != 1
        or set(all_fingerprints) != set(TARGET_ROLES) - {"witness"}
        or len(set(all_fingerprints.values())) != 3
    ):
        raise MigrationPlanBuildError(
            "global seed keys, owner, or recipient identities are not exact"
        )
    return rows


def build_migration_plan(
    *,
    inventory: dict[str, Any],
    inventory_approval: dict[str, Any],
    approval_policy: dict[str, Any],
    freezes: dict[str, dict[str, Any]],
    backups: dict[str, dict[str, Any]],
    seeds: dict[str, dict[str, Any]],
    images: dict[str, dict[str, Any]],
    created_at: str,
    not_after: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    inventory_result = verify_approved_inventory(
        inventory,
        approval=inventory_approval,
        approval_policy=approval_policy,
        host_destructive=None,
        now=current,
        require_fresh_approval=True,
    )
    if inventory_result.get("inventory_stage") != "provisioned":
        raise MigrationPlanBuildError("migration plan requires provisioned inventory")
    created = _utc(created_at, label="created_at")
    expires = _utc(not_after, label="not_after")
    if (
        expires <= created
        or expires - created > timedelta(hours=4)
        or not created - timedelta(minutes=5) <= current < expires
    ):
        raise MigrationPlanBuildError(
            "migration plan timestamps must form one currently valid window of at most four hours"
        )
    campaign_id = str(inventory_result["campaign_id"])
    release_sha = str(inventory_result["release_sha"])
    backup_rows = _backup_rows(
        backups,
        campaign_id=campaign_id,
        release_sha=release_sha,
        inventory=inventory,
    )
    freeze_rows = _freeze_rows(
        freezes,
        campaign_id=campaign_id,
        release_sha=release_sha,
        backups=backups,
    )
    plan = {
        "schema": "three-site-staging-migration-plan-v1",
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "deployment_id": inventory_result["deployment_id"],
        "provisioned_inventory_sha256": inventory_result["inventory_sha256"],
        "created_at": created.isoformat(),
        "not_after": expires.isoformat(),
        "source_freeze": {
            "required": True,
            "evidence": freeze_rows,
            "redis_restore": False,
        },
        "source_backups": backup_rows,
        "seed_bundles": _seed_rows(
            seeds,
            campaign_id=campaign_id,
            release_sha=release_sha,
            backups=backups,
            inventory=inventory,
        ),
        "target_seed_map": [
            {"target_role": role, "source_role": source, "mode": mode}
            for role, (source, mode) in TARGET_SEED_MAP.items()
        ],
        "image_inventories": _image_rows(
            images, campaign_id=campaign_id, release_sha=release_sha
        ),
        "ordered_phases": list(ORDERED_PHASES),
        "rollback_policy": {
            "strategy": "forward-rollback-or-separate-restore",
            "alembic_downgrade": False,
            "routing_held_until_commit": True,
            "legacy_sources_retained": True,
            "cleanup_requires_explicit_finish": True,
        },
    }
    # This also enforces the exact top-level plan field set before publication.
    migration_approval_subject(plan)
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--freeze-evidence", action="append", default=[])
    parser.add_argument("--backup-manifest", action="append", default=[])
    parser.add_argument("--seed-manifest", action="append", default=[])
    parser.add_argument("--image-inventory", action="append", default=[])
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--not-after", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval-subject-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output == args.approval_subject_output:
            raise MigrationPlanBuildError("plan and approval subject outputs must differ")
        inventory = secure_json(args.inventory, label="provisioned inventory")
        plan = build_migration_plan(
            inventory=inventory,
            inventory_approval=secure_json(
                args.inventory_approval, label="provisioned inventory approval"
            ),
            approval_policy=secure_json(
                args.approval_policy, label="human approval policy"
            ),
            freezes=_documents_by_role(
                args.freeze_evidence,
                label="source freeze evidence",
                roles=SOURCE_ROLES,
            ),
            backups=_documents_by_role(
                args.backup_manifest,
                label="source backup manifest",
                roles=SOURCE_ROLES,
            ),
            seeds=_documents_by_role(
                args.seed_manifest,
                label="seed manifest",
                roles=SOURCE_ROLES,
            ),
            images=_documents_by_role(
                args.image_inventory,
                label="image inventory",
                roles=TARGET_ROLES,
            ),
            created_at=args.created_at,
            not_after=args.not_after,
        )
        subject = migration_approval_subject(plan)
        plan_bytes = migration_canonical_bytes(plan) + b"\n"
        subject_bytes = migration_canonical_bytes(subject) + b"\n"
        _publish_exact_or_new(
            args.output,
            plan_bytes,
            label="migration plan",
            max_size=16 * 1024 * 1024,
        )
        _publish_exact_or_new(
            args.approval_subject_output,
            subject_bytes,
            label="migration approval subject",
            max_size=1024 * 1024,
        )
        print(
            json.dumps(
                {
                    "status": "built",
                    "campaign_id": plan["campaign_id"],
                    "release_sha": plan["release_sha"],
                    "plan_sha256": _canonical_hash(plan),
                    "output": str(args.output),
                    "approval_subject_output": str(args.approval_subject_output),
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
