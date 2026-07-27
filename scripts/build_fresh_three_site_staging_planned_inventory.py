#!/usr/bin/env python3
"""Build one fresh, unapproved three-site planned inventory transaction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.human_approval import approval_subject
from core.three_site_execution_safety import (
    DEDICATED_HOST_DESTRUCTIVE,
    EXECUTION_CLASSES,
)
from scripts.fresh_campaign_secure_io import (
    SecureOutputDirectory,
    prove_exact_git_release,
    read_secure_root_file,
)
from scripts.verify_three_site_staging_inventory import (
    ROLE_VOLUME_LOGICAL_NAMES,
    _canonical_bytes,
    verify_inventory,
)


RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
DEPLOYMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,95}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
CREDENTIAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
INVENTORY_NAME = "planned-inventory.json"
SUBJECT_NAME = "planned-inventory-approval-subject.json"


class FreshInventoryError(RuntimeError):
    """A fresh planned inventory cannot be proven safe."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FreshInventoryError("template inventory contains duplicate fields")
        result[key] = value
    return result


def _strict_json(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, FreshInventoryError) as exc:
        raise FreshInventoryError("template inventory is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise FreshInventoryError("template inventory must be a JSON object")
    return payload


def _canonical_campaign_id(value: str) -> str:
    try:
        canonical = str(UUID(str(value)))
    except ValueError as exc:
        raise FreshInventoryError("campaign ID must be a canonical UUID") from exc
    if str(value) != canonical:
        raise FreshInventoryError("campaign ID must be a canonical lowercase UUID")
    return canonical


def _host_destructive(execution_class: str) -> bool:
    if execution_class not in EXECUTION_CLASSES:
        raise FreshInventoryError("execution class is invalid")
    return execution_class == DEDICATED_HOST_DESTRUCTIVE


def canonical_object_prefix(
    *,
    release_sha: str,
    campaign_id: str,
    deployment_id: str,
) -> str:
    return (
        f"staging/three-site/{release_sha}/{campaign_id}/{deployment_id}/"
    )


def _validate_storage_identity(template_inventory: dict[str, Any]) -> None:
    storage = template_inventory.get("object_storage")
    if (
        not isinstance(storage, dict)
        or BUCKET_RE.fullmatch(str(storage.get("bucket") or "")) is None
        or CREDENTIAL_ID_RE.fullmatch(
            str(storage.get("credential_id") or "")
        )
        is None
        or storage.get("private") is not True
        or storage.get("versioning") is not True
    ):
        raise FreshInventoryError("template Object Storage identity is invalid")


def _require_distinct_role_hosts(payload: dict[str, Any]) -> None:
    roles = payload.get("roles")
    if not isinstance(roles, list) or len(roles) != 4:
        raise FreshInventoryError("exactly four role hosts are required")
    for field in ("host_ip", "machine_id", "docker_daemon_id"):
        values = [str(role.get(field) or "") for role in roles]
        if any(not value for value in values) or len(set(values)) != 4:
            raise FreshInventoryError(
                f"fresh campaign requires four distinct role {field} values"
            )


def derive_fresh_planned_inventory(
    *,
    template_inventory: dict[str, Any],
    release_sha: str,
    campaign_id: str,
    deployment_id: str,
    execution_class: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a fresh planned inventory and exact approve_inventory subject."""

    expected_destructive = _host_destructive(execution_class)
    template_result = verify_inventory(
        template_inventory,
        host_destructive=expected_destructive,
    )
    if template_result["host_safety_mode"] != execution_class:
        raise FreshInventoryError("template execution class differs from the request")
    _require_distinct_role_hosts(template_inventory)
    _validate_storage_identity(template_inventory)

    release = str(release_sha)
    campaign = _canonical_campaign_id(campaign_id)
    deployment = str(deployment_id)
    if RELEASE_RE.fullmatch(release) is None:
        raise FreshInventoryError("release SHA must be exactly 40 lowercase hex characters")
    if (
        deployment != deployment.strip().lower()
        or DEPLOYMENT_RE.fullmatch(deployment) is None
    ):
        raise FreshInventoryError("deployment ID is invalid")
    if release == template_result["release_sha"]:
        raise FreshInventoryError("fresh release identity is required")
    if (
        campaign == template_result["campaign_id"]
        or deployment == template_result["deployment_id"]
    ):
        raise FreshInventoryError("fresh campaign and deployment identities are required")
    prefix = canonical_object_prefix(
        release_sha=release,
        campaign_id=campaign,
        deployment_id=deployment,
    )
    if prefix == template_inventory["object_storage"]["prefix"]:
        raise FreshInventoryError("fresh Object Storage prefix is required")

    result = json.loads(json.dumps(template_inventory))
    result["inventory_stage"] = "planned"
    result["campaign_id"] = campaign
    result["release_sha"] = release
    result["deployment_id"] = deployment
    result["object_storage"]["prefix"] = prefix
    namespace = f"three-site-{release[:16]}-{campaign.replace('-', '')}"
    if len(campaign.replace("-", "")) != 32:
        raise FreshInventoryError("campaign namespace lacks 128-bit identity")
    result["compose_project_namespace"] = namespace

    old_mutable_ids: set[str] = set()
    for role in template_inventory["roles"]:
        for field in ROLE_VOLUME_LOGICAL_NAMES[str(role["role"])]:
            if role.get(field) is not None:
                old_mutable_ids.add(str(role[field]))

    new_mutable_ids: set[str] = set()
    for role in result["roles"]:
        name = str(role["role"])
        role["postgres_system_id"] = None
        role["release_sha"] = release
        role["deployment_id"] = deployment
        project = f"{namespace}-{name.replace('_', '-')}"
        for field, logical_name in ROLE_VOLUME_LOGICAL_NAMES[name].items():
            if role.get(field) is not None:
                value = f"{project}_{logical_name}"
                role[field] = value
                new_mutable_ids.add(value)
    if not new_mutable_ids or old_mutable_ids & new_mutable_ids:
        raise FreshInventoryError("campaign-owned volume/audit identities are not fresh")

    verified = verify_inventory(result, host_destructive=expected_destructive)
    _require_distinct_role_hosts(result)
    if (
        verified["inventory_stage"] != "planned"
        or verified["host_safety_mode"] != execution_class
        or verified["host_destructive"] != expected_destructive
    ):
        raise FreshInventoryError("fresh planned inventory verification is inconsistent")
    digest = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    subject = approval_subject(
        artifact_type="three-site-staging-inventory-v3",
        artifact_sha256=digest,
        release_sha=release,
        bindings={
            "campaign_id": campaign,
            "deployment_id": deployment,
            "host_safety_mode": execution_class,
            "inventory_stage": "planned",
        },
    )
    return result, subject


def _outside_repo(path: Path) -> None:
    try:
        path.relative_to(REPO_ROOT)
    except ValueError:
        return
    raise FreshInventoryError("fresh inventory inputs/outputs must be outside the release")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-inventory", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--execution-class", choices=sorted(EXECUTION_CLASSES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        _outside_repo(args.template_inventory)
        _outside_repo(args.output)
        exact_release = prove_exact_git_release(
            repo_root=REPO_ROOT,
            release_sha=args.release_sha,
            bound_files=(
                Path(__file__).resolve(),
                (REPO_ROOT / "scripts/fresh_campaign_secure_io.py").resolve(),
            ),
        )
        inventory, subject = derive_fresh_planned_inventory(
            template_inventory=_strict_json(
                read_secure_root_file(
                    args.template_inventory,
                    label="topology-only template inventory",
                    expected_mode=0o600,
                    max_size=4 * 1024 * 1024,
                )
            ),
            release_sha=args.release_sha,
            campaign_id=args.campaign_id,
            deployment_id=args.deployment_id,
            execution_class=args.execution_class,
        )
        inventory_bytes = _canonical_bytes(inventory) + b"\n"
        subject_bytes = (
            json.dumps(subject, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        with SecureOutputDirectory(args.output) as transaction:
            transaction.write(INVENTORY_NAME, inventory_bytes, mode=0o600)
            transaction.write(SUBJECT_NAME, subject_bytes, mode=0o600)
            transaction.publish(before_publish=exact_release.recheck)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "status": "fresh-planned-inventory-created-unapproved",
                "action": "approve_inventory",
                "campaign_id": inventory["campaign_id"],
                "deployment_id": inventory["deployment_id"],
                "execution_class": inventory["host_safety_mode"],
                "release_sha": inventory["release_sha"],
                "object_prefix": inventory["object_storage"]["prefix"],
                "inventory_sha256": subject["artifact_sha256"],
                "output": str(args.output),
                "secret_inputs_read": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
