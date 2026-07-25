#!/usr/bin/env python3
"""Derive fresh, unapproved three-site staging campaign material safely.

An abandoned campaign's inventory cannot be reused for a different release.
This tool preserves the reviewed staging topology and secret material while
creating a *planned* inventory with a new immutable campaign/deployment
identity.  It deliberately drops all measured PostgreSQL system identifiers:
the new campaign must fresh-preflight and measure its own target boundary.

It never issues an approval and publishes only new owner-only files.
"""

from __future__ import annotations

import argparse
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

from core.human_approval import approval_subject
from core.secure_file_io import read_secure_text, write_secure_new_bytes
from scripts.render_three_site_staging_role_compose import (
    canonical_role_env_bytes,
    parse_env_values,
)
from scripts.verify_three_site_staging_inventory import (
    _canonical_bytes,
    verify_inventory,
)


SHA40 = re.compile(r"^[0-9a-f]{40}$")
DEPLOYMENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{7,95}$")
OBJECT_PREFIX = re.compile(r"^staging/[a-z0-9][a-z0-9._/-]{2,511}/$")


class CampaignMaterialError(RuntimeError):
    """Fresh campaign material cannot be safely derived."""


def _strict_json(source: str, *, label: str) -> dict[str, Any]:
    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CampaignMaterialError(f"{label} contains duplicate fields")
            result[key] = value
        return result

    try:
        value = json.loads(source, object_pairs_hook=hook)
    except (json.JSONDecodeError, CampaignMaterialError) as exc:
        raise CampaignMaterialError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise CampaignMaterialError(f"{label} must be a JSON object")
    return value


def _campaign_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise CampaignMaterialError("campaign ID must be a UUID") from exc


def derive_campaign_material(
    *,
    template_inventory: dict[str, Any],
    template_env: str,
    release_sha: str,
    campaign_id: str,
    deployment_id: str,
    object_prefix: str,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Return a fresh planned inventory, full env bytes, and approval subject."""

    template = verify_inventory(template_inventory, host_destructive=None)
    release = str(release_sha).lower()
    campaign = _campaign_id(campaign_id)
    deployment = str(deployment_id).strip().lower()
    prefix = str(object_prefix).strip()
    if SHA40.fullmatch(release) is None:
        raise CampaignMaterialError("release SHA must be an exact 40-hex Git SHA")
    if DEPLOYMENT_ID.fullmatch(deployment) is None:
        raise CampaignMaterialError("deployment ID is invalid")
    if (
        OBJECT_PREFIX.fullmatch(prefix) is None
        or "//" in prefix
        or "/../" in prefix
        or prefix.endswith("/./")
    ):
        raise CampaignMaterialError("Object Storage prefix is invalid")
    if campaign == template["campaign_id"] or deployment == template["deployment_id"]:
        raise CampaignMaterialError("fresh campaign/deployment identity is required")
    if prefix == template_inventory["object_storage"]["prefix"]:
        raise CampaignMaterialError("fresh Object Storage prefix is required")

    values = parse_env_values(template_env)
    if any("change_me" in value.lower() for value in values.values()):
        raise CampaignMaterialError("template environment contains placeholders")
    required = {"STAGING_RELEASE_SHA", "DR_BLOB_OBJECT_PREFIX"}
    if not required <= set(values):
        raise CampaignMaterialError("template environment lacks required campaign bindings")
    if values["STAGING_RELEASE_SHA"].lower() != template["release_sha"]:
        raise CampaignMaterialError("template environment does not match template inventory")

    result = json.loads(json.dumps(template_inventory))
    result["inventory_stage"] = "planned"
    result["campaign_id"] = campaign
    result["release_sha"] = release
    result["deployment_id"] = deployment
    result["object_storage"]["prefix"] = prefix
    namespace = f"three-site-{campaign.replace('-', '')[:12]}"
    result["compose_project_namespace"] = namespace
    for role in result["roles"]:
        role["postgres_system_id"] = None
        role["release_sha"] = release
        role["deployment_id"] = deployment
        project = f"{namespace}-{role['role'].replace('_', '-')}"
        for field, logical in (
            ("postgres_volume_id", f"{role['role']}_postgres"),
            ("redis_volume_id", f"{role['role']}_redis"),
            ("uploads_volume_id", f"{role['role']}_uploads"),
            ("audit_root_id", f"{role['role']}_audit"),
        ):
            if role.get(field) is not None:
                role[field] = f"{project}_{logical}"
    verified = verify_inventory(result, host_destructive=None)

    values["STAGING_RELEASE_SHA"] = release
    values["DR_BLOB_OBJECT_PREFIX"] = f"{prefix}blobs/sha256"
    rendered_env = canonical_role_env_bytes(values, required_names=frozenset(values))
    digest = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    subject = approval_subject(
        artifact_type="three-site-staging-inventory-v3",
        artifact_sha256=digest,
        release_sha=release,
        bindings={
            "campaign_id": verified["campaign_id"],
            "deployment_id": verified["deployment_id"],
            "host_safety_mode": verified["host_safety_mode"],
            "inventory_stage": "planned",
        },
    )
    return result, rendered_env, subject


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-inventory", type=Path, required=True)
    parser.add_argument("--template-env", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--object-prefix", required=True)
    parser.add_argument("--inventory-output", type=Path, required=True)
    parser.add_argument("--env-output", type=Path, required=True)
    parser.add_argument("--approval-subject-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        outputs = {
            args.inventory_output.resolve(),
            args.env_output.resolve(),
            args.approval_subject_output.resolve(),
        }
        if len(outputs) != 3:
            raise CampaignMaterialError("campaign material outputs must be distinct")
        inventory, env_bytes, subject = derive_campaign_material(
            template_inventory=_strict_json(
                read_secure_text(args.template_inventory, label="template inventory"),
                label="template inventory",
            ),
            template_env=read_secure_text(args.template_env, label="template environment"),
            release_sha=args.release_sha,
            campaign_id=args.campaign_id,
            deployment_id=args.deployment_id,
            object_prefix=args.object_prefix,
        )
        write_secure_new_bytes(
            args.inventory_output,
            _canonical_bytes(inventory) + b"\n",
            label="fresh planned staging inventory",
            mode=0o600,
        )
        write_secure_new_bytes(
            args.env_output,
            env_bytes,
            label="fresh full staging environment",
            mode=0o600,
        )
        write_secure_new_bytes(
            args.approval_subject_output,
            json.dumps(subject, sort_keys=True, indent=2).encode("utf-8") + b"\n",
            label="fresh planned inventory approval subject",
            mode=0o600,
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "status": "planned-material-created-unapproved",
                "campaign_id": inventory["campaign_id"],
                "deployment_id": inventory["deployment_id"],
                "release_sha": inventory["release_sha"],
                "inventory_sha256": hashlib.sha256(_canonical_bytes(inventory)).hexdigest(),
                "next_gate": "issue password-plus-TOTP approval for the exact planned inventory subject",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
