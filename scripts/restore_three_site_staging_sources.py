#!/usr/bin/env python3
"""Restore the exact legacy staging service set recorded by source freeze."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.freeze_three_site_staging_sources import DATA_SERVICES, DOCKER, _run
from core.three_site_staging_source_contract import (
    LEGACY_STAGING_PROJECTS,
    legacy_staging_project_allowed,
)
from scripts.render_three_site_staging_role_compose import _atomic_write
from scripts.verify_three_site_staging_inventory import (
    _strict_object,
    load_inventory,
    verify_approved_inventory,
)
from scripts.verify_three_site_staging_role_bundle import _verify_bundle_source


class SourceRestoreError(RuntimeError):
    pass


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def confirmation_phrase(campaign_id: str, evidence_hash: str) -> str:
    return f"restore-legacy-staging:{campaign_id}:{evidence_hash}"


def _canonical_hash(value) -> str:  # noqa: ANN001
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify_restore_input(
    evidence: dict,
    *,
    campaign_id: str,
    release_sha: str,
    project_name: str,
) -> dict[str, object]:
    expected_fields = {
        "schema", "campaign_id", "target_release_sha", "project_name", "observed_at",
        "source_roles", "previously_running_services", "stopped_services",
        "running_services", "postgres", "redis_observation", "legacy_restore_bundle",
    }
    previous = evidence.get("previously_running_services") if isinstance(evidence, dict) else None
    stopped = evidence.get("stopped_services") if isinstance(evidence, dict) else None
    source_rows = evidence.get("source_roles") if isinstance(evidence, dict) else None
    source_roles = [
        str(row.get("source_role"))
        for row in source_rows
        if isinstance(row, dict)
    ] if isinstance(source_rows, list) else []
    previous_set = (
        set(previous)
        if isinstance(previous, list) and all(isinstance(value, str) for value in previous)
        else set()
    )
    observed_stopped = (
        set(stopped)
        if isinstance(stopped, list) and all(isinstance(value, str) for value in stopped)
        else set()
    )
    expected_stopped = previous_set - DATA_SERVICES
    if (
        not isinstance(evidence, dict)
        or set(evidence) != expected_fields
        or evidence.get("schema") != "three-site-staging-source-freeze-v1"
        or evidence.get("campaign_id") != campaign_id
        or evidence.get("target_release_sha") != release_sha
        or evidence.get("project_name") != project_name
        or len(source_roles) != len(source_rows or [])
        or not legacy_staging_project_allowed(project_name, source_roles)
        or evidence.get("running_services") != ["db", "redis"]
        or not isinstance(previous, list)
        or len(previous) != len(previous_set)
        or not DATA_SERVICES.issubset(previous_set)
        or not isinstance(stopped, list)
        or len(stopped) != len(observed_stopped)
        or bool(observed_stopped & DATA_SERVICES)
        # Freeze issues a closed stop request for every configured mutating
        # service. Docker treats requests for already-stopped services as
        # harmless no-ops, so historical evidence can include services that
        # were not in the pre-freeze running set. Restore never starts from
        # this field: it derives its exact start set from
        # previously_running_services. Every actually-running mutating
        # service must still have been in the stop request.
        or not expected_stopped.issubset(observed_stopped)
        or not isinstance(evidence.get("legacy_restore_bundle"), dict)
        or set(evidence["legacy_restore_bundle"]) != {"schema", "path", "sha256", "size"}
        or evidence["legacy_restore_bundle"].get("schema")
        != "three-site-staging-legacy-restore-bundle-reference-v1"
        or not Path(str(evidence["legacy_restore_bundle"].get("path", ""))).is_absolute()
        or SHA256_RE.fullmatch(str(evidence["legacy_restore_bundle"].get("sha256", ""))) is None
        or type(evidence["legacy_restore_bundle"].get("size")) is not int
        or not 1 <= evidence["legacy_restore_bundle"]["size"] <= 1024 * 1024
    ):
        raise SourceRestoreError("legacy source-freeze evidence cannot authorize restore")
    return {
        "evidence_sha256": _canonical_hash(evidence),
        "previously_running_services": sorted(previous),
        "services_to_start": sorted(expected_stopped),
        "legacy_restore_bundle": dict(evidence["legacy_restore_bundle"]),
    }


def _strict_json_bytes(raw: bytes, *, label: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceRestoreError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise SourceRestoreError(f"{label} must be an object")
    return value


def _load_legacy_restore_bundle(
    reference: dict,
    *,
    evidence: dict,
) -> tuple[dict, Path]:
    manifest_path = Path(reference["path"])
    manifest_bytes = _verify_bundle_source(manifest_path, expected_mode=0o600)
    if (
        len(manifest_bytes) != reference["size"]
        or hashlib.sha256(manifest_bytes).hexdigest() != reference["sha256"]
    ):
        raise SourceRestoreError("legacy restore bundle differs from freeze evidence")
    manifest = _strict_json_bytes(manifest_bytes, label="legacy restore bundle")
    fields = {
        "schema", "campaign_id", "target_release_sha", "project_name", "captured_at",
        "source_releases", "previously_running_services", "compose", "service_images",
    }
    expected_releases = {
        str(row["source_role"]): str(row["source_release_sha"])
        for row in evidence["source_roles"]
    }
    images = manifest.get("service_images")
    compose = manifest.get("compose")
    if (
        set(manifest) != fields
        or manifest.get("schema") != "three-site-staging-legacy-restore-bundle-v1"
        or manifest.get("campaign_id") != evidence["campaign_id"]
        or manifest.get("target_release_sha") != evidence["target_release_sha"]
        or manifest.get("project_name") != evidence["project_name"]
        or manifest.get("source_releases") != expected_releases
        or manifest.get("previously_running_services")
        != sorted(evidence["previously_running_services"])
        or not isinstance(images, dict)
        or set(images) != set(evidence["previously_running_services"])
        or any(IMAGE_ID_RE.fullmatch(str(value)) is None for value in images.values())
        or not isinstance(compose, dict)
        or set(compose) != {"path", "sha256", "size"}
        or not Path(str(compose.get("path", ""))).is_absolute()
        or SHA256_RE.fullmatch(str(compose.get("sha256", ""))) is None
        or type(compose.get("size")) is not int
        or not 1 <= compose["size"] <= 10 * 1024 * 1024
        or any(SHA_RE.fullmatch(value) is None for value in expected_releases.values())
    ):
        raise SourceRestoreError("legacy restore bundle identity/content is invalid")
    compose_path = Path(compose["path"])
    compose_bytes = _verify_bundle_source(compose_path, expected_mode=0o600)
    if (
        len(compose_bytes) != compose["size"]
        or hashlib.sha256(compose_bytes).hexdigest() != compose["sha256"]
    ):
        raise SourceRestoreError("resolved legacy Compose differs from rollback bundle")
    return manifest, compose_path


def _legacy_prefix(*, project_name: str, compose_path: Path) -> list[str]:
    if project_name not in LEGACY_STAGING_PROJECTS:
        raise SourceRestoreError("legacy restore project is outside the closed allowlist")
    return [DOCKER, "compose", "-p", project_name, "-f", str(compose_path)]


def _verify_local_images(service_images: dict[str, str]) -> None:
    for service, expected_image_id in sorted(service_images.items()):
        observed = _run(
            [DOCKER, "image", "inspect", "--format", "{{.Id}}", expected_image_id]
        )
        if observed != expected_image_id:
            raise SourceRestoreError(f"legacy image is missing or changed: {service}")


def _observe_service_images(prefix: list[str], services: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for service in sorted(services):
        container_id = _run([*prefix, "ps", "-q", service])
        if not container_id:
            raise SourceRestoreError(f"restored legacy service has no container: {service}")
        result[service] = _run(
            [DOCKER, "inspect", "--format", "{{.Image}}", container_id]
        )
    return result


def execute(
    args: argparse.Namespace,
    *,
    inventory_result: dict[str, object],
    evidence: dict,
) -> dict[str, object]:
    verified = verify_restore_input(
        evidence,
        campaign_id=str(inventory_result["campaign_id"]),
        release_sha=str(inventory_result["release_sha"]),
        project_name=args.project_name,
    )
    required = confirmation_phrase(
        str(inventory_result["campaign_id"]), str(verified["evidence_sha256"])
    )
    if args.confirm != required:
        raise SourceRestoreError("legacy source restore confirmation mismatch")
    manifest, compose_path = _load_legacy_restore_bundle(
        verified["legacy_restore_bundle"], evidence=evidence
    )
    prefix = _legacy_prefix(project_name=args.project_name, compose_path=compose_path)
    _run([*prefix, "config", "--quiet"])
    configured = {
        value for value in _run([*prefix, "config", "--services"]).splitlines() if value
    }
    expected = set(verified["previously_running_services"])
    if not expected.issubset(configured):
        raise SourceRestoreError("resolved legacy Compose lacks a recorded service")
    _verify_local_images(manifest["service_images"])
    current = {
        value for value in _run(
            [*prefix, "ps", "--status", "running", "--services"]
        ).splitlines() if value
    }
    if not (current == DATA_SERVICES or current == expected):
        raise SourceRestoreError("legacy staging has an unexpected partial service state")
    if current != expected:
        _run(
            [
                *prefix, "up", "-d", "--no-build", "--pull", "never",
                *verified["services_to_start"],
            ],
            timeout=300,
        )
    running_after = {
        value for value in _run(
            [*prefix, "ps", "--status", "running", "--services"]
        ).splitlines() if value
    }
    if running_after != expected:
        raise SourceRestoreError("legacy staging service set did not restore exactly")
    observed_images = _observe_service_images(prefix, expected)
    if observed_images != manifest["service_images"]:
        raise SourceRestoreError("restored containers do not use the frozen image IDs")
    result = {
        "schema": "three-site-staging-source-restore-v1",
        "status": "restored",
        "campaign_id": inventory_result["campaign_id"],
        "release_sha": inventory_result["release_sha"],
        "freeze_evidence_sha256": verified["evidence_sha256"],
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "running_services": sorted(running_after),
        "legacy_restore_bundle_sha256": verified["legacy_restore_bundle"]["sha256"],
        "service_images": observed_images,
    }
    _atomic_write(
        args.output,
        (json.dumps(result, sort_keys=True, indent=2) + "\n").encode(),
        mode=0o600,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default="trading_bot_staging")
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--freeze-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        inventory_result = verify_approved_inventory(
            load_inventory(args.inventory),
            approval=load_inventory(args.inventory_approval),
            approval_policy=load_inventory(args.approval_policy),
            host_destructive=None,
        )
        if inventory_result["inventory_stage"] != "provisioned":
            raise SourceRestoreError("legacy source restore requires provisioned inventory")
        evidence = json.loads(
            _verify_bundle_source(
                args.freeze_evidence, expected_mode=0o600
            ).decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
        verified = verify_restore_input(
            evidence,
            campaign_id=str(inventory_result["campaign_id"]),
            release_sha=str(inventory_result["release_sha"]),
            project_name=args.project_name,
        )
        result = {
            "status": "planned",
            "campaign_id": inventory_result["campaign_id"],
            "services_to_start": verified["services_to_start"],
            "required_confirmation": confirmation_phrase(
                str(inventory_result["campaign_id"]), str(verified["evidence_sha256"])
            ),
        }
        if args.apply:
            result = execute(
                args,
                inventory_result=inventory_result,
                evidence=evidence,
            )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
