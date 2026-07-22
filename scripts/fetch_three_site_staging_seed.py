#!/usr/bin/env python3
"""Fetch and decrypt exact-version staging seed objects for one target role."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import sha256_secure_file
from scripts.publish_three_site_staging_seed import (
    MAX_ARTIFACT_BYTES,
    _age_material,
    _client,
    _credentials,
    _prepare_output,
    _run_age,
    _streaming_hash,
)
from scripts.render_three_site_staging_role_compose import _atomic_write
from scripts.run_three_site_staging_source_backup import verify_tar_artifact
from scripts.verify_three_site_staging_inventory import load_inventory
from scripts.verify_three_site_staging_migration_plan import TARGET_SEED_MAP, verify_migration_plan


TARGET_ROLES = tuple(TARGET_SEED_MAP)
ARTIFACT_FILENAME = {
    "postgres": "postgres.custom",
    "uploads": "uploads.tar.gz",
    "audit": "audit.tar.gz",
}


class SeedFetchError(RuntimeError):
    pass


def confirmation_phrase(campaign_id: str, target_role: str, plan_hash: str) -> str:
    return f"fetch-seed:{campaign_id}:{target_role}:{plan_hash}"


def _fetch_one(
    client,
    *,
    bucket: str,
    item: dict[str, Any],
    identity_path: Path,
    output: Path,
) -> dict[str, Any]:
    encrypted = output.parent / f".{output.name}.ciphertext"
    response = client.get_object(
        Bucket=bucket,
        Key=item["object_key"],
        VersionId=item["version_id"],
    )
    if (
        str(response.get("VersionId") or "") != item["version_id"]
        or int(response.get("ContentLength") or -1) != item["ciphertext_bytes"]
        or response.get("Metadata") != {
            "plaintext-sha256": item["plaintext_sha256"],
            "ciphertext-sha256": item["ciphertext_sha256"],
            "artifact-kind": item["kind"],
        }
    ):
        raise SeedFetchError("target seed provider identity/metadata differs from manifest")
    ciphertext_hash, ciphertext_size = _streaming_hash(response["Body"], encrypted)
    close = getattr(response["Body"], "close", None)
    if callable(close):
        close()
    try:
        if (
            ciphertext_hash != item["ciphertext_sha256"]
            or ciphertext_size != item["ciphertext_bytes"]
        ):
            raise SeedFetchError("target seed ciphertext differs from signed manifest")
        _run_age(
            [
                "--decrypt", "--identity", str(identity_path),
                "--output", str(output), str(encrypted),
            ]
        )
        output.chmod(0o600)
        plaintext_hash, plaintext_size = sha256_secure_file(
            output,
            label=f"{item['kind']} target seed",
            max_size=MAX_ARTIFACT_BYTES,
        )
        if (
            plaintext_hash != item["plaintext_sha256"]
            or plaintext_size != item["plaintext_bytes"]
        ):
            output.unlink(missing_ok=True)
            raise SeedFetchError("decrypted target seed differs from signed manifest")
        if item["kind"] in {"uploads", "audit"}:
            verify_tar_artifact(output)
        return {
            "kind": item["kind"],
            "object_key": item["object_key"],
            "version_id": item["version_id"],
            "ciphertext_sha256": ciphertext_hash,
            "plaintext_sha256": plaintext_hash,
            "plaintext_bytes": plaintext_size,
            "path": str(output),
        }
    finally:
        encrypted.unlink(missing_ok=True)


def build_plan(
    *, campaign_id: str, target_role: str, plan_hash: str, source_role: str | None
) -> dict[str, Any]:
    return {
        "status": "planned",
        "campaign_id": campaign_id,
        "target_role": target_role,
        "source_role": source_role,
        "object_count": 0 if source_role is None else 3,
        "required_confirmation": confirmation_phrase(campaign_id, target_role, plan_hash),
    }


def execute(
    args: argparse.Namespace,
    *,
    verified_plan: dict[str, Any],
    inventory: dict[str, Any],
    seed_manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expected_confirmation = confirmation_phrase(
        verified_plan["campaign_id"], args.target_role, verified_plan["plan_sha256"]
    )
    if args.confirm != expected_confirmation:
        raise SeedFetchError("target seed fetch confirmation mismatch")
    source_role, mode = TARGET_SEED_MAP[args.target_role]
    _prepare_output(args.output_dir, repo=args.repo.resolve())
    if source_role is None:
        evidence = {
            "schema": "three-site-staging-target-seed-v1",
            "campaign_id": verified_plan["campaign_id"],
            "release_sha": verified_plan["release_sha"],
            "target_role": args.target_role,
            "source_role": None,
            "mode": mode,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "objects": [],
        }
    else:
        manifest = seed_manifests[source_role]
        _recipient, recipient_fingerprint = _age_material(args.recipient, args.identity)
        if recipient_fingerprint != manifest["recipient_fingerprint"]:
            raise SeedFetchError("target age recipient differs from signed seed manifest")
        access_key, secret_key = _credentials(args.credentials)
        client = _client(access_key=access_key, secret_key=secret_key)
        if client.get_bucket_versioning(Bucket=manifest["bucket"]).get("Status") != "Enabled":
            raise SeedFetchError("target seed bucket versioning is not enabled")
        objects = []
        for item in sorted(manifest["objects"], key=lambda value: value["kind"]):
            objects.append(
                _fetch_one(
                    client,
                    bucket=manifest["bucket"],
                    item=item,
                    identity_path=args.identity,
                    output=args.output_dir / ARTIFACT_FILENAME[item["kind"]],
                )
            )
        evidence = {
            "schema": "three-site-staging-target-seed-v1",
            "campaign_id": verified_plan["campaign_id"],
            "release_sha": verified_plan["release_sha"],
            "target_role": args.target_role,
            "source_role": source_role,
            "mode": mode,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "objects": objects,
        }
    encoded = (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode()
    evidence_path = args.output_dir / "target-seed.json"
    _atomic_write(evidence_path, encoded, mode=0o600)
    return {
        "status": "target-seed-verified",
        "campaign_id": verified_plan["campaign_id"],
        "target_role": args.target_role,
        "source_role": source_role,
        "evidence": str(evidence_path),
        "evidence_sha256": hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "object_count": len(evidence["objects"]),
    }


def _mapping(values: list[str], *, roles: tuple[str, ...], label: str):  # noqa: ANN001
    result = {}
    for value in values:
        role, separator, raw_path = value.partition("=")
        if not separator or role not in roles or role in result or not raw_path:
            raise SeedFetchError(f"{label} must use one unique role=/path mapping")
        result[role] = load_inventory(Path(raw_path))
    if set(result) != set(roles):
        raise SeedFetchError(f"{label} role set is incomplete")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-role", choices=TARGET_ROLES, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-approval", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--signer-policy", type=Path, required=True)
    parser.add_argument("--freeze-evidence", action="append", type=Path, required=True)
    parser.add_argument("--image-inventory", action="append", required=True)
    parser.add_argument("--backup-manifest", action="append", required=True)
    parser.add_argument("--seed-manifest", action="append", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--recipient", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        inventory = load_inventory(args.inventory)
        backups = _mapping(
            args.backup_manifest, roles=("bot_fi", "webapp_fi"), label="--backup-manifest"
        )
        seeds = _mapping(
            args.seed_manifest, roles=("bot_fi", "webapp_fi"), label="--seed-manifest"
        )
        verified = verify_migration_plan(
            load_inventory(args.plan),
            approval=load_inventory(args.plan_approval),
            inventory=inventory,
            inventory_approval=load_inventory(args.inventory_approval),
            signer_policy=load_inventory(args.signer_policy),
            freeze_evidence=[load_inventory(path) for path in args.freeze_evidence],
            image_inventories=_mapping(
                args.image_inventory,
                roles=("bot_fi", "webapp_fi", "webapp_ir", "witness"),
                label="--image-inventory",
            ),
            backup_manifests=backups,
            seed_manifests=seeds,
        )
        source_role, _mode = TARGET_SEED_MAP[args.target_role]
        result = build_plan(
            campaign_id=verified["campaign_id"],
            target_role=args.target_role,
            plan_hash=verified["plan_sha256"],
            source_role=source_role,
        )
        if args.apply:
            result = execute(
                args,
                verified_plan=verified,
                inventory=inventory,
                seed_manifests=seeds,
            )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
