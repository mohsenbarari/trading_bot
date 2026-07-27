#!/usr/bin/env python3
"""Fail-closed controller contract for a side-by-side production cutover.

This module does not execute production commands.  It verifies one root-only
manifest, renders fixed argv for a future bounded host agent, and maintains a
crash-visible local journal.  Crossing the first-business-write boundary is a
separate explicit journal operation because rollback to the untouched legacy
database is no longer valid after that point.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Iterable
from uuid import UUID


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from core.three_site_topology import (  # noqa: E402
    BOT_FI_HOST,
    PRODUCTION_WITNESS_HOST,
    WEBAPP_FI_HOST,
    WEBAPP_IR_HOST,
)


MANIFEST_SCHEMA = "production-shadow-cutover-manifest-v1"
PLAN_SCHEMA = "production-shadow-cutover-plan-v1"
JOURNAL_SCHEMA = "production-shadow-cutover-journal-v1"
APPLY_CONFIRMATION = "APPLY-PRODUCTION-SHADOW-CUTOVER-JOURNAL"
FIRST_WRITE_COMMIT_CONFIRMATION = "COMMIT-PRODUCTION-SHADOW-FIRST-BUSINESS-WRITE"
CONTROLLER_PATH = "/usr/local/sbin/trading-bot-production-shadow-controller"
REMOTE_AGENT_PATH = "/usr/local/sbin/trading-bot-production-shadow-agent"
PRODUCTION_HOSTNAME = "coin.gold-trade.ir"
LEGACY_COMPOSE_PROJECT = "trading_bot"
ZERO_SHA256 = "0" * 64

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9_]{7,62}$")

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
TOPOLOGY_FIELDS = frozenset({"role", "host", "ssh_user", "ssh_port", "transport"})
DEPLOYMENT_FIELDS = frozenset(
    {
        "production_hostname",
        "legacy_compose_project",
        "shadow_compose_project",
        "shadow_root",
        "controller_journal_path",
        "controller_evidence_root",
    }
)
ARTIFACT_FIELDS = frozenset(
    {
        "release_bundle_sha256",
        "role_material_sha256",
        "app_image_id",
        "postgres_image_id",
        "postgres_image_ref",
        "legacy_bot_rollback_sha256",
        "legacy_webapp_rollback_sha256",
        "shadow_compose_sha256",
        "cutover_approval_sha256",
    }
)
POLICY_FIELDS = frozenset(
    {
        "plan_only_default",
        "write_block_required",
        "legacy_writers_stop_required",
        "zero_client_readback_required",
        "final_snapshot_hashes_required",
        "witness_lease_required",
        "readonly_switch_required",
        "rollback_before_first_write_only",
        "object_storage_private_versioned_age_required",
        "direct_payload_to_webapp_ir_forbidden",
        "staging_forbidden",
        "current_path_mutation_forbidden",
        "destructive_cleanup_forbidden",
        "database_downgrade_forbidden",
    }
)

EXPECTED_TOPOLOGY: dict[str, dict[str, Any]] = {
    "bot_fi": {
        "role": "bot_fi",
        "host": BOT_FI_HOST,
        "ssh_user": None,
        "ssh_port": None,
        "transport": "local-controller",
    },
    "webapp_fi": {
        "role": "webapp_fi",
        "host": WEBAPP_FI_HOST,
        "ssh_user": "root",
        "ssh_port": 37067,
        "transport": "ssh-control",
    },
    "webapp_ir": {
        "role": "webapp_ir",
        "host": WEBAPP_IR_HOST,
        "ssh_user": "root",
        "ssh_port": 22,
        "transport": "ssh-control-object-storage-payload-only",
    },
    "witness": {
        "role": "witness",
        "host": PRODUCTION_WITNESS_HOST,
        "ssh_user": "root",
        "ssh_port": 22,
        "transport": "ssh-control-object-storage-payload-only",
    },
}


class CutoverContractError(RuntimeError):
    """Raised when a cutover contract fails closed."""


@dataclass(frozen=True)
class PhaseSpec:
    phase: str
    operation: str
    roles: tuple[str, ...]
    description: str
    mutates_production: bool


# The order is the rollback boundary.  None of these phases permits a business
# write.  The separate commit gate below is the first point after which rollback
# to the legacy database is prohibited.
PHASE_SPECS: tuple[PhaseSpec, ...] = (
    PhaseSpec(
        "pre_freeze_evidence",
        "capture-pre-freeze-evidence",
        ("bot_fi", "webapp_fi", "webapp_ir", "witness"),
        "Capture immutable health, release, topology, backup, and rollback evidence.",
        False,
    ),
    PhaseSpec(
        "write_block_install",
        "install-production-write-block",
        ("webapp_fi",),
        "Install the production write-block configuration without reloading it.",
        True,
    ),
    PhaseSpec(
        "write_block_nginx_test",
        "test-production-nginx-configuration",
        ("webapp_fi",),
        "Validate the write-block configuration before reload.",
        False,
    ),
    PhaseSpec(
        "write_block_reload",
        "reload-production-nginx-write-block",
        ("webapp_fi",),
        "Reload the validated write block.",
        True,
    ),
    PhaseSpec(
        "stop_legacy_writers",
        "stop-legacy-writer-processes",
        ("bot_fi", "webapp_fi"),
        "Stop only legacy processes capable of authoritative writes.",
        True,
    ),
    PhaseSpec(
        "zero_client_readback",
        "verify-zero-legacy-writer-clients",
        ("bot_fi", "webapp_fi"),
        "Prove the legacy databases have no writer clients.",
        False,
    ),
    PhaseSpec(
        "write_block_readback",
        "verify-production-write-block-readback",
        ("bot_fi",),
        "Read back the public write block from the controller.",
        False,
    ),
    PhaseSpec(
        "final_snapshot_hashes",
        "capture-final-frozen-snapshot-hashes",
        ("bot_fi", "webapp_fi"),
        "Capture final frozen DB, Redis, upload, audit, and manifest hashes.",
        True,
    ),
    PhaseSpec(
        "shadow_restore",
        "restore-shadow-production-state",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Restore only into operation-owned shadow volumes.",
        True,
    ),
    PhaseSpec(
        "shadow_migrate",
        "migrate-shadow-databases-forward",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Run forward-only migrations on the shadow databases.",
        True,
    ),
    PhaseSpec(
        "shadow_roles",
        "bind-shadow-least-privilege-roles",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Create and bind distinct least-privilege runtime roles.",
        True,
    ),
    PhaseSpec(
        "shadow_fence",
        "enable-shadow-database-fencing",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Enable the database event and writer fence on shadow databases.",
        True,
    ),
    PhaseSpec(
        "witness_lease",
        "acquire-shadow-writer-witness-lease",
        ("witness", "webapp_fi", "webapp_ir"),
        "Acquire and verify the live Witness lease without enabling a business write.",
        True,
    ),
    PhaseSpec(
        "convergence_gate",
        "verify-shadow-three-site-convergence",
        ("bot_fi", "webapp_fi", "webapp_ir", "witness"),
        "Require schema, role, fence, queue, parity, and DR convergence evidence.",
        False,
    ),
    PhaseSpec(
        "readonly_upstream_switch",
        "switch-production-upstream-shadow-readonly",
        ("webapp_fi",),
        "Switch the public upstream to shadow while all business writes remain blocked.",
        True,
    ),
    PhaseSpec(
        "pre_first_write_acceptance",
        "verify-pre-first-write-acceptance",
        ("bot_fi", "webapp_fi", "webapp_ir", "witness"),
        "Re-read every gate and prove rollback remains available before commit.",
        False,
    ),
)
PHASES = tuple(spec.phase for spec in PHASE_SPECS)

ROLLBACK_SPECS: tuple[PhaseSpec, ...] = (
    PhaseSpec(
        "rollback_readonly_upstream",
        "restore-production-upstream-legacy-readonly",
        ("webapp_fi",),
        "Route readonly traffic back to the untouched legacy stack.",
        True,
    ),
    PhaseSpec(
        "rollback_nginx_test",
        "test-production-nginx-configuration",
        ("webapp_fi",),
        "Validate the legacy readonly upstream.",
        False,
    ),
    PhaseSpec(
        "rollback_nginx_reload",
        "reload-production-nginx-legacy-readonly",
        ("webapp_fi",),
        "Reload the validated legacy readonly upstream.",
        True,
    ),
    PhaseSpec(
        "rollback_shadow_stop",
        "stop-shadow-services-preserve-state",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Stop shadow services while preserving every shadow volume and artifact.",
        True,
    ),
    PhaseSpec(
        "rollback_legacy_writers",
        "start-legacy-writer-processes",
        ("bot_fi", "webapp_fi"),
        "Restart the untouched legacy writer processes under the write block.",
        True,
    ),
    PhaseSpec(
        "rollback_write_block_restore",
        "restore-legacy-production-write-policy",
        ("webapp_fi",),
        "Restore the legacy write policy only after legacy health is proven.",
        True,
    ),
    PhaseSpec(
        "rollback_final_readback",
        "verify-legacy-production-readback",
        ("bot_fi", "webapp_fi"),
        "Verify legacy health and routing after rollback.",
        False,
    ),
)

OPERATIONAL_GAPS = (
    {
        "component": REMOTE_AGENT_PATH,
        "status": "missing",
        "required_for": "Every rendered host argv; the controller never executes it.",
    },
    {
        "component": "immutable-release-image-compose-verifier",
        "status": "missing",
        "required_for": "Pre-freeze release, image ID, compose hash, and host identity attestation.",
    },
    {
        "component": "write-block-and-legacy-writer-worker",
        "status": "missing",
        "required_for": "Nginx install/test/reload, legacy writer stop/start, zero-client and public readback.",
    },
    {
        "component": "final-frozen-snapshot-worker",
        "status": "missing",
        "required_for": "Final DB/Redis/uploads/audit snapshot, restore-smoke, hash, and readback evidence.",
    },
    {
        "component": "shadow-restore-migration-role-fence-worker",
        "status": "missing",
        "required_for": "Operation-owned restore plus forward migration, least-privilege roles, and fencing.",
    },
    {
        "component": "witness-lease-and-clock-validator",
        "status": "missing",
        "required_for": "Live Witness lease, boot clock, epoch, transition, and renewal evidence.",
    },
    {
        "component": "three-site-convergence-validator",
        "status": "missing",
        "required_for": "Queue, cursor, DR, parity, quarantine, lag, TLS, and role convergence evidence.",
    },
    {
        "component": "readonly-upstream-and-rollback-worker",
        "status": "missing",
        "required_for": "Readonly shadow switch, pre-commit rollback rehearsal, and legacy readback.",
    },
    {
        "component": "phase-evidence-schema-verifiers",
        "status": "missing",
        "required_for": "Semantic verification of each evidence file before its digest completes a phase.",
    },
    {
        "component": "first-business-write-executor",
        "status": "intentionally-absent",
        "required_for": "A later reviewed slice after the explicit irreversible journal commit gate.",
    },
)


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _state_hash(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "state_sha256"}
    return hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CutoverContractError(f"duplicate manifest field: {key}")
        result[key] = value
    return result


def _require_exact_fields(value: Any, fields: frozenset[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CutoverContractError(f"{label} fields are not exact")
    return value


def _canonical_campaign_id(value: Any) -> str:
    if not isinstance(value, str):
        raise CutoverContractError("campaign_id must be a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise CutoverContractError("campaign_id must be a canonical UUID") from exc
    if str(parsed) != value or parsed.version not in {1, 2, 3, 4, 5}:
        raise CutoverContractError("campaign_id must be a canonical UUID")
    return value


def _shadow_project(campaign_id: str) -> str:
    return f"tb_prod_{campaign_id.replace('-', '')[:16]}"


def _secure_root(campaign_id: str) -> PurePosixPath:
    return PurePosixPath("/root/secure-envs/trading-bot/production-cutover") / campaign_id


def _shadow_root(campaign_id: str) -> PurePosixPath:
    return PurePosixPath("/srv/trading-bot-production-shadow") / campaign_id


def validate_manifest(document: Any) -> dict[str, Any]:
    manifest = _require_exact_fields(document, MANIFEST_FIELDS, label="manifest")
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise CutoverContractError("manifest schema is invalid")
    campaign_id = _canonical_campaign_id(manifest["campaign_id"])
    operation_id = _canonical_campaign_id(manifest["operation_id"])
    if operation_id == campaign_id:
        raise CutoverContractError("operation_id must be distinct from campaign_id")
    if not _valid_timestamp(manifest["created_at"]):
        raise CutoverContractError("manifest created_at must be timezone-aware")
    for field in ("release_sha", "release_tree_sha", "legacy_release_sha"):
        if not isinstance(manifest[field], str) or SHA_RE.fullmatch(manifest[field]) is None:
            raise CutoverContractError(f"{field} must be an exact lowercase Git SHA")
    if manifest["release_sha"] == manifest["legacy_release_sha"]:
        raise CutoverContractError("shadow and legacy release SHAs must differ")

    topology = _require_exact_fields(
        manifest["topology"], frozenset(EXPECTED_TOPOLOGY), label="topology"
    )
    for role, expected in EXPECTED_TOPOLOGY.items():
        actual = _require_exact_fields(topology[role], TOPOLOGY_FIELDS, label=f"topology.{role}")
        if actual != expected:
            raise CutoverContractError(f"topology.{role} does not match the canonical production pin")
    if len({topology[role]["host"] for role in topology}) != len(topology):
        raise CutoverContractError("production topology hosts must be physically distinct")

    deployment = _require_exact_fields(
        manifest["deployment"], DEPLOYMENT_FIELDS, label="deployment"
    )
    expected_deployment = {
        "production_hostname": PRODUCTION_HOSTNAME,
        "legacy_compose_project": LEGACY_COMPOSE_PROJECT,
        "shadow_compose_project": _shadow_project(campaign_id),
        "shadow_root": str(_shadow_root(campaign_id)),
        "controller_journal_path": str(_secure_root(campaign_id) / "journal.json"),
        "controller_evidence_root": str(_secure_root(campaign_id) / "evidence"),
    }
    if deployment != expected_deployment:
        raise CutoverContractError("deployment paths or production identity are not exact")
    if PROJECT_RE.fullmatch(deployment["shadow_compose_project"]) is None:
        raise CutoverContractError("shadow compose project is invalid")
    if deployment["shadow_compose_project"] == deployment["legacy_compose_project"]:
        raise CutoverContractError("shadow compose project collides with legacy production")
    for key in ("shadow_root", "controller_journal_path", "controller_evidence_root"):
        path = PurePosixPath(deployment[key])
        if not path.is_absolute() or ".." in path.parts or "current" in path.parts:
            raise CutoverContractError(f"deployment.{key} is unsafe")

    artifacts = _require_exact_fields(manifest["artifacts"], ARTIFACT_FIELDS, label="artifacts")
    for field in (
        "release_bundle_sha256",
        "role_material_sha256",
        "legacy_bot_rollback_sha256",
        "legacy_webapp_rollback_sha256",
        "shadow_compose_sha256",
        "cutover_approval_sha256",
    ):
        if not isinstance(artifacts[field], str) or SHA256_RE.fullmatch(artifacts[field]) is None:
            raise CutoverContractError(f"artifacts.{field} is not a SHA-256 digest")
    for field in ("app_image_id", "postgres_image_id"):
        if not isinstance(artifacts[field], str) or IMAGE_ID_RE.fullmatch(artifacts[field]) is None:
            raise CutoverContractError(f"artifacts.{field} is not an immutable image ID")
    expected_postgres_ref = f"trading_bot_postgres_boottime:15-{manifest['release_sha']}"
    if artifacts["postgres_image_ref"] != expected_postgres_ref:
        raise CutoverContractError("custom PostgreSQL image ref is not bound to the release")

    policy = _require_exact_fields(manifest["policy"], POLICY_FIELDS, label="policy")
    non_true = sorted(key for key, value in policy.items() if value is not True)
    if non_true:
        raise CutoverContractError(
            "every fail-closed production policy must be true: " + ",".join(non_true)
        )
    return manifest


def read_root_only_manifest(
    path: Path,
    *,
    owner_uid: int = 0,
    max_size: int = 256 * 1024,
) -> tuple[dict[str, Any], str]:
    """Read a stable exact-mode owner-only manifest without following symlinks."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CutoverContractError(f"cannot securely open production cutover manifest: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > max_size
        ):
            raise CutoverContractError("production cutover manifest must be owner-only mode 0600")
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if len(payload) > max_size or any(
            getattr(before, field) != getattr(after, field) for field in stable
        ):
            raise CutoverContractError("production cutover manifest changed while being read")
    finally:
        os.close(descriptor)
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CutoverContractError("production cutover manifest is not strict UTF-8 JSON") from exc
    return validate_manifest(document), hashlib.sha256(payload).hexdigest()


def _agent_args(
    manifest: dict[str, Any],
    *,
    manifest_sha256: str,
    role: str,
    operation: str,
) -> list[str]:
    topology = manifest["topology"][role]
    agent = [
        REMOTE_AGENT_PATH,
        "--operation",
        operation,
        "--role",
        role,
        "--expected-host",
        topology["host"],
        "--campaign-id",
        manifest["campaign_id"],
        "--operation-id",
        manifest["operation_id"],
        "--release-sha",
        manifest["release_sha"],
        "--legacy-release-sha",
        manifest["legacy_release_sha"],
        "--manifest-sha256",
        manifest_sha256,
        "--approval-sha256",
        manifest["artifacts"]["cutover_approval_sha256"],
        "--release-bundle-sha256",
        manifest["artifacts"]["release_bundle_sha256"],
        "--role-material-sha256",
        manifest["artifacts"]["role_material_sha256"],
        "--shadow-compose-sha256",
        manifest["artifacts"]["shadow_compose_sha256"],
        "--app-image-id",
        manifest["artifacts"]["app_image_id"],
        "--postgres-image-id",
        manifest["artifacts"]["postgres_image_id"],
        "--shadow-project",
        manifest["deployment"]["shadow_compose_project"],
        "--shadow-root",
        manifest["deployment"]["shadow_root"],
    ]
    if topology["transport"] == "local-controller":
        return agent
    if topology["transport"] == "ssh-control-object-storage-payload-only":
        agent.extend(["--payload-transport", "object-storage-private-versioned-age"])
    return [
        "/usr/bin/ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=/root/.ssh/known_hosts",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(topology["ssh_port"]),
        f"{topology['ssh_user']}@{topology['host']}",
        *agent,
    ]


def _validate_rendered_argv(argv_sets: Iterable[list[str]]) -> None:
    for argv in argv_sets:
        if not isinstance(argv, list) or not argv or any(
            not isinstance(token, str) or not token for token in argv
        ):
            raise CutoverContractError("rendered command argv is invalid")
        lowered = [token.lower() for token in argv]
        joined = " ".join(lowered)
        if (
            any(token in {"sh", "bash", "scp", "rsync", "sftp", "rm"} for token in lowered)
            or "/current" in joined
            or "staging" in joined
            or "docker compose down" in joined
            or "volume rm" in joined
            or "downgrade" in joined
        ):
            raise CutoverContractError("rendered command contains a prohibited production action")


def _render_specs(
    manifest: dict[str, Any],
    *,
    manifest_sha256: str,
    specs: tuple[PhaseSpec, ...],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous: str | None = None
    for index, spec in enumerate(specs, 1):
        commands = [
            {
                "command_id": f"{spec.phase}.{role}",
                "role": role,
                "argv": _agent_args(
                    manifest,
                    manifest_sha256=manifest_sha256,
                    role=role,
                    operation=spec.operation,
                ),
                "required": True,
                "render_only": True,
                "executor_available": False,
                "requires_live_state_recheck": True,
                "approval_sha256": manifest["artifacts"]["cutover_approval_sha256"],
                "payload_transfer": (
                    "object-storage-private-versioned-age"
                    if manifest["topology"][role]["transport"]
                    == "ssh-control-object-storage-payload-only"
                    else "none"
                ),
            }
            for role in spec.roles
        ]
        _validate_rendered_argv(command["argv"] for command in commands)
        result.append(
            {
                "index": index,
                "phase": spec.phase,
                "description": spec.description,
                "prerequisite_phase": previous,
                "mutates_production": spec.mutates_production,
                "business_write_allowed": False,
                "execution_supported": False,
                "journal_begin_required_before_commands": True,
                "journal_completion_requires_evidence_sha256": True,
                "commands": commands,
            }
        )
        previous = spec.phase
    return result


def _plan_hash(plan: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    return hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()


def render_plan(
    manifest: dict[str, Any],
    *,
    manifest_sha256: str,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    validate_manifest(manifest)
    if SHA256_RE.fullmatch(manifest_sha256) is None:
        raise CutoverContractError("manifest SHA-256 is invalid")
    phases = _render_specs(
        manifest, manifest_sha256=manifest_sha256, specs=PHASE_SPECS
    )
    rollback = _render_specs(
        manifest, manifest_sha256=manifest_sha256, specs=ROLLBACK_SPECS
    )
    commit_argv = [
        CONTROLLER_PATH,
        "--manifest",
        str(manifest_path) if manifest_path else "<ROOT_ONLY_MANIFEST_PATH>",
        "--action",
        "commit-first-business-write",
        "--evidence-sha256",
        "<PRE_FIRST_WRITE_ACCEPTANCE_SHA256>",
        "--apply",
        "--confirm",
        APPLY_CONFIRMATION,
        "--commit-confirm",
        FIRST_WRITE_COMMIT_CONFIRMATION,
    ]
    plan = {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "execution_backend": "not-implemented-in-controller-slice",
        "executes_commands": False,
        "live_io_supported": False,
        "apply_scope": "root-only-local-journal-transitions",
        "manifest_sha256": manifest_sha256,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
        "topology": manifest["topology"],
        "phases": phases,
        "rollback": {
            "eligible_until_commit_gate": True,
            "prohibited_after_commit_gate": True,
            "preserves_shadow_volumes_and_artifacts": True,
            "commands": rollback,
        },
        "first_business_write_commit_gate": {
            "irreversible_boundary": True,
            "required_completed_phase": PHASES[-1],
            "required_confirmation": FIRST_WRITE_COMMIT_CONFIRMATION,
            "argv_template": commit_argv,
            "effect": (
                "Journal rollback becomes permanently prohibited before any first "
                "business write may be attempted."
            ),
        },
        "prohibitions": [
            "no staging path or deployment",
            "no mutation of any current path",
            "no delete, compose down, volume removal, or destructive cleanup",
            "no database downgrade",
            "no direct payload transfer to WebApp-IR",
            "no business write before the explicit commit gate",
            "no rollback after the explicit commit gate",
        ],
        "operational_gaps": list(OPERATIONAL_GAPS),
    }
    plan["plan_sha256"] = _plan_hash(plan)
    return plan


JOURNAL_FIELDS = frozenset(
    {
        "schema",
        "manifest_sha256",
        "plan_sha256",
        "campaign_id",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "status",
        "completed_phases",
        "phase_evidence_sha256",
        "started_phase",
        "started_at",
        "rollback_eligible",
        "rollback_reason",
        "rollback_evidence_sha256",
        "first_business_write_allowed",
        "commit_evidence_sha256",
        "committed_at",
        "events",
        "event_tail_sha256",
        "created_at",
        "updated_at",
        "state_sha256",
    }
)
JOURNAL_STATUSES = {
    "active",
    "phase_started",
    "ready_for_commit",
    "rolled_back",
    "first_write_committed",
}
EVENT_FIELDS = frozenset(
    {
        "sequence",
        "kind",
        "phase",
        "evidence_sha256",
        "reason",
        "at",
        "previous_hash",
        "event_hash",
    }
)
EVENT_KINDS = {
    "journal_created",
    "phase_started",
    "phase_completed",
    "rollback_recorded",
    "first_write_committed",
}


def _event_hash(event: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "event_hash"}
    return hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()


def _append_event(
    payload: dict[str, Any],
    *,
    kind: str,
    phase: str | None = None,
    evidence_sha256: str | None = None,
    reason: str | None = None,
) -> None:
    if kind not in EVENT_KINDS:
        raise CutoverContractError("unknown cutover journal event kind")
    events = payload["events"]
    previous = events[-1]["event_hash"] if events else ZERO_SHA256
    event: dict[str, Any] = {
        "sequence": len(events) + 1,
        "kind": kind,
        "phase": phase,
        "evidence_sha256": evidence_sha256,
        "reason": reason,
        "at": _now(),
        "previous_hash": previous,
        "event_hash": "",
    }
    event["event_hash"] = _event_hash(event)
    events.append(event)
    payload["event_tail_sha256"] = event["event_hash"]


def _validate_event_history(journal: dict[str, Any]) -> None:
    events = journal["events"]
    if not isinstance(events, list) or not events:
        raise CutoverContractError("cutover journal event history is missing")
    previous = ZERO_SHA256
    replay_completed: list[str] = []
    replay_started: str | None = None
    replay_status = "active"
    replay_evidence: dict[str, str] = {}
    for expected_sequence, raw in enumerate(events, 1):
        event = _require_exact_fields(raw, EVENT_FIELDS, label="cutover journal event")
        kind = event["kind"]
        phase = event["phase"]
        evidence = event["evidence_sha256"]
        reason = event["reason"]
        if (
            event["sequence"] != expected_sequence
            or kind not in EVENT_KINDS
            or not _valid_timestamp(event["at"])
            or event["previous_hash"] != previous
            or event["event_hash"] != _event_hash(event)
        ):
            raise CutoverContractError("cutover journal event hash chain is invalid")
        if expected_sequence == 1:
            if (
                kind != "journal_created"
                or phase is not None
                or evidence is not None
                or reason is not None
            ):
                raise CutoverContractError("cutover journal creation event is invalid")
        elif kind == "phase_started":
            expected_phase = (
                PHASES[len(replay_completed)]
                if len(replay_completed) < len(PHASES)
                else None
            )
            if (
                replay_status != "active"
                or replay_started is not None
                or phase != expected_phase
                or evidence is not None
                or reason is not None
            ):
                raise CutoverContractError("cutover journal phase-start history is invalid")
            replay_started = phase
            replay_status = "phase_started"
        elif kind == "phase_completed":
            if (
                replay_status != "phase_started"
                or phase != replay_started
                or SHA256_RE.fullmatch(str(evidence)) is None
                or reason is not None
            ):
                raise CutoverContractError("cutover journal phase-completion history is invalid")
            replay_completed.append(str(phase))
            replay_evidence[str(phase)] = str(evidence)
            replay_started = None
            replay_status = (
                "ready_for_commit"
                if replay_completed == list(PHASES)
                else "active"
            )
        elif kind == "rollback_recorded":
            if (
                replay_status not in {"active", "phase_started", "ready_for_commit"}
                or phase is not None
                or SHA256_RE.fullmatch(str(evidence)) is None
                or not isinstance(reason, str)
                or not reason
            ):
                raise CutoverContractError("cutover journal rollback history is invalid")
            replay_started = None
            replay_status = "rolled_back"
        elif kind == "first_write_committed":
            if (
                replay_status != "ready_for_commit"
                or phase is not None
                or evidence != replay_evidence.get(PHASES[-1])
                or reason is not None
            ):
                raise CutoverContractError("cutover journal commit history is invalid")
            replay_status = "first_write_committed"
        else:
            raise CutoverContractError("cutover journal creation event may only occur once")
        previous = event["event_hash"]
    if (
        journal["event_tail_sha256"] != previous
        or journal["completed_phases"] != replay_completed
        or journal["phase_evidence_sha256"] != replay_evidence
        or journal["started_phase"] != replay_started
        or journal["status"] != replay_status
    ):
        raise CutoverContractError("cutover journal state differs from its event history")
    if replay_status == "rolled_back":
        last = events[-1]
        if (
            journal["rollback_reason"] != last["reason"]
            or journal["rollback_evidence_sha256"] != last["evidence_sha256"]
        ):
            raise CutoverContractError("cutover rollback state differs from event history")
    if replay_status == "first_write_committed":
        if journal["commit_evidence_sha256"] != events[-1]["evidence_sha256"]:
            raise CutoverContractError("cutover commit state differs from event history")


def _validate_journal(payload: Any) -> dict[str, Any]:
    journal = _require_exact_fields(payload, JOURNAL_FIELDS, label="cutover journal")
    if (
        journal["schema"] != JOURNAL_SCHEMA
        or SHA256_RE.fullmatch(str(journal["manifest_sha256"])) is None
        or SHA256_RE.fullmatch(str(journal["plan_sha256"])) is None
        or SHA_RE.fullmatch(str(journal["release_sha"])) is None
        or SHA_RE.fullmatch(str(journal["legacy_release_sha"])) is None
        or journal["release_sha"] == journal["legacy_release_sha"]
        or journal["status"] not in JOURNAL_STATUSES
        or not _valid_timestamp(journal["created_at"])
        or not _valid_timestamp(journal["updated_at"])
        or journal["state_sha256"] != _state_hash(journal)
    ):
        raise CutoverContractError("cutover journal schema, identity, or hash is invalid")
    _canonical_campaign_id(journal["campaign_id"])
    _canonical_campaign_id(journal["operation_id"])
    if journal["operation_id"] == journal["campaign_id"]:
        raise CutoverContractError("cutover journal operation_id is invalid")
    completed = journal["completed_phases"]
    evidence = journal["phase_evidence_sha256"]
    if (
        not isinstance(completed, list)
        or completed != list(PHASES[: len(completed)])
        or len(completed) != len(set(completed))
        or not isinstance(evidence, dict)
        or set(evidence) != set(completed)
        or any(SHA256_RE.fullmatch(str(value)) is None for value in evidence.values())
    ):
        raise CutoverContractError("cutover journal phase prefix or evidence is invalid")
    next_phase = PHASES[len(completed)] if len(completed) < len(PHASES) else None
    status = journal["status"]
    if status == "phase_started":
        if (
            journal["started_phase"] != next_phase
            or not _valid_timestamp(journal["started_at"])
            or len(completed) >= len(PHASES)
        ):
            raise CutoverContractError("cutover journal interrupted phase is invalid")
    elif journal["started_phase"] is not None or journal["started_at"] is not None:
        raise CutoverContractError("cutover journal has a stale started phase")
    if status == "active" and len(completed) >= len(PHASES):
        raise CutoverContractError("completed cutover journal must be ready for commit")
    if status == "ready_for_commit" and completed != list(PHASES):
        raise CutoverContractError("cutover journal reached commit gate before all phases")
    expected_rollback_eligible = status in {"active", "phase_started", "ready_for_commit"}
    if journal["rollback_eligible"] is not expected_rollback_eligible:
        raise CutoverContractError("cutover journal rollback eligibility is inconsistent")
    if journal["first_business_write_allowed"] is not (status == "first_write_committed"):
        raise CutoverContractError("cutover journal first-write state is inconsistent")
    if status == "rolled_back":
        if (
            not isinstance(journal["rollback_reason"], str)
            or not journal["rollback_reason"]
            or SHA256_RE.fullmatch(str(journal["rollback_evidence_sha256"])) is None
            or journal["commit_evidence_sha256"] is not None
            or journal["committed_at"] is not None
        ):
            raise CutoverContractError("rolled-back journal lacks exact evidence")
    elif journal["rollback_reason"] is not None or journal["rollback_evidence_sha256"] is not None:
        raise CutoverContractError("non-rollback journal contains rollback evidence")
    if status == "first_write_committed":
        if (
            completed != list(PHASES)
            or journal["commit_evidence_sha256"] != evidence[PHASES[-1]]
            or not _valid_timestamp(journal["committed_at"])
        ):
            raise CutoverContractError("first-write commit gate lacks acceptance evidence")
    elif journal["commit_evidence_sha256"] is not None or journal["committed_at"] is not None:
        raise CutoverContractError("pre-commit journal contains commit evidence")
    _validate_event_history(journal)
    return journal


class ProductionCutoverJournal:
    """Root-owned, locked, atomic state for one production cutover."""

    def __init__(self, path: Path, *, owner_uid: int = 0):
        if not path.is_absolute():
            raise CutoverContractError("cutover journal path must be absolute")
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.owner_uid = int(owner_uid)

    def _lock(self) -> int:
        if os.geteuid() != self.owner_uid:
            raise CutoverContractError("cutover journal must run as its root owner")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = os.stat(self.path.parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != self.owner_uid
            or stat.S_IMODE(parent.st_mode) & 0o077
        ):
            raise CutoverContractError("cutover journal directory must be owner-only")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != self.owner_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            os.close(descriptor)
            raise CutoverContractError("cutover journal lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor

    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(
                read_secure_bytes(
                    self.path,
                    label="production cutover journal",
                    owner_uid=self.owner_uid,
                    max_size=1024 * 1024,
                ).decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
        except Exception as exc:
            raise CutoverContractError("production cutover journal is unreadable") from exc
        return _validate_journal(payload)

    def _write(self, payload: dict[str, Any], *, create: bool = False) -> dict[str, Any]:
        payload["updated_at"] = _now()
        payload["state_sha256"] = _state_hash(payload)
        _validate_journal(payload)
        encoded = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        writer = write_secure_new_bytes if create else write_secure_atomic_bytes
        try:
            writer(
                self.path,
                encoded,
                label="production cutover journal",
                mode=0o600,
                max_size=1024 * 1024,
            )
        except SecureFileError as exc:
            raise CutoverContractError("production cutover journal write failed") from exc
        return payload

    @staticmethod
    def _bindings(
        *,
        manifest_sha256: str,
        plan_sha256: str,
        campaign_id: str,
        operation_id: str,
        release_sha: str,
        legacy_release_sha: str,
    ) -> dict[str, str]:
        if (
            SHA256_RE.fullmatch(manifest_sha256) is None
            or SHA256_RE.fullmatch(plan_sha256) is None
            or SHA_RE.fullmatch(release_sha) is None
            or SHA_RE.fullmatch(legacy_release_sha) is None
            or release_sha == legacy_release_sha
        ):
            raise CutoverContractError("cutover journal binding is invalid")
        _canonical_campaign_id(campaign_id)
        _canonical_campaign_id(operation_id)
        if operation_id == campaign_id:
            raise CutoverContractError("cutover operation_id must be distinct from campaign_id")
        return {
            "manifest_sha256": manifest_sha256,
            "plan_sha256": plan_sha256,
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "legacy_release_sha": legacy_release_sha,
        }

    def create(
        self,
        *,
        manifest_sha256: str,
        plan_sha256: str,
        campaign_id: str,
        operation_id: str,
        release_sha: str,
        legacy_release_sha: str,
    ) -> dict[str, Any]:
        bindings = self._bindings(
            manifest_sha256=manifest_sha256,
            plan_sha256=plan_sha256,
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
            legacy_release_sha=legacy_release_sha,
        )
        descriptor = self._lock()
        try:
            if self.path.exists():
                existing = self._read()
                if any(existing[key] != value for key, value in bindings.items()):
                    raise CutoverContractError("existing cutover journal has different bindings")
                return existing
            now = _now()
            payload: dict[str, Any] = {
                "schema": JOURNAL_SCHEMA,
                **bindings,
                "status": "active",
                "completed_phases": [],
                "phase_evidence_sha256": {},
                "started_phase": None,
                "started_at": None,
                "rollback_eligible": True,
                "rollback_reason": None,
                "rollback_evidence_sha256": None,
                "first_business_write_allowed": False,
                "commit_evidence_sha256": None,
                "committed_at": None,
                "events": [],
                "event_tail_sha256": ZERO_SHA256,
                "created_at": now,
                "updated_at": now,
                "state_sha256": "",
            }
            _append_event(payload, kind="journal_created")
            return self._write(payload, create=True)
        finally:
            os.close(descriptor)

    def assert_bindings(
        self,
        *,
        manifest_sha256: str,
        plan_sha256: str,
        campaign_id: str,
        operation_id: str,
        release_sha: str,
        legacy_release_sha: str,
    ) -> dict[str, Any]:
        expected = self._bindings(
            manifest_sha256=manifest_sha256,
            plan_sha256=plan_sha256,
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
            legacy_release_sha=legacy_release_sha,
        )
        descriptor = self._lock()
        try:
            payload = self._read()
            if any(payload[key] != value for key, value in expected.items()):
                raise CutoverContractError("cutover journal differs from manifest or plan binding")
            return payload
        finally:
            os.close(descriptor)

    def load(self) -> dict[str, Any]:
        descriptor = self._lock()
        try:
            return self._read()
        finally:
            os.close(descriptor)

    def begin_phase(self, phase: str) -> dict[str, Any]:
        if phase not in PHASES:
            raise CutoverContractError("unknown production cutover phase")
        descriptor = self._lock()
        try:
            payload = self._read()
            if payload["status"] in {"rolled_back", "first_write_committed", "ready_for_commit"}:
                raise CutoverContractError("cutover phase cannot begin from terminal state")
            if phase in payload["completed_phases"]:
                return payload
            if payload["status"] == "phase_started":
                if payload["started_phase"] == phase:
                    return payload
                raise CutoverContractError("a different cutover phase requires reconciliation")
            expected = PHASES[len(payload["completed_phases"])]
            if phase != expected:
                raise CutoverContractError(f"cutover phase is out of order; expected {expected}")
            payload["status"] = "phase_started"
            payload["started_phase"] = phase
            payload["started_at"] = _now()
            _append_event(payload, kind="phase_started", phase=phase)
            return self._write(payload)
        finally:
            os.close(descriptor)

    def complete_phase(self, phase: str, *, evidence_sha256: str) -> dict[str, Any]:
        if phase not in PHASES or SHA256_RE.fullmatch(evidence_sha256) is None:
            raise CutoverContractError("cutover phase or evidence SHA-256 is invalid")
        descriptor = self._lock()
        try:
            payload = self._read()
            if phase in payload["completed_phases"]:
                if payload["phase_evidence_sha256"][phase] != evidence_sha256:
                    raise CutoverContractError("idempotent phase evidence differs from the journal")
                return payload
            if payload["status"] != "phase_started" or payload["started_phase"] != phase:
                raise CutoverContractError("cutover phase completion has no matching durable start")
            payload["completed_phases"].append(phase)
            payload["phase_evidence_sha256"][phase] = evidence_sha256
            payload["started_phase"] = None
            payload["started_at"] = None
            payload["status"] = (
                "ready_for_commit"
                if payload["completed_phases"] == list(PHASES)
                else "active"
            )
            _append_event(
                payload,
                kind="phase_completed",
                phase=phase,
                evidence_sha256=evidence_sha256,
            )
            return self._write(payload)
        finally:
            os.close(descriptor)

    def record_rollback(
        self,
        *,
        reason: str,
        evidence_sha256: str,
    ) -> dict[str, Any]:
        normalized_reason = str(reason).strip()
        if (
            not normalized_reason
            or len(normalized_reason) > 256
            or SHA256_RE.fullmatch(evidence_sha256) is None
        ):
            raise CutoverContractError("rollback reason or evidence SHA-256 is invalid")
        descriptor = self._lock()
        try:
            payload = self._read()
            if payload["status"] == "first_write_committed":
                raise CutoverContractError("rollback is prohibited after first-write commit")
            if payload["status"] == "rolled_back":
                if (
                    payload["rollback_reason"] != normalized_reason
                    or payload["rollback_evidence_sha256"] != evidence_sha256
                ):
                    raise CutoverContractError("idempotent rollback evidence differs from journal")
                return payload
            payload["status"] = "rolled_back"
            payload["started_phase"] = None
            payload["started_at"] = None
            payload["rollback_eligible"] = False
            payload["rollback_reason"] = normalized_reason
            payload["rollback_evidence_sha256"] = evidence_sha256
            _append_event(
                payload,
                kind="rollback_recorded",
                evidence_sha256=evidence_sha256,
                reason=normalized_reason,
            )
            return self._write(payload)
        finally:
            os.close(descriptor)

    def commit_first_business_write(
        self,
        *,
        evidence_sha256: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if confirmation != FIRST_WRITE_COMMIT_CONFIRMATION:
            raise CutoverContractError("first-business-write commit confirmation is invalid")
        if SHA256_RE.fullmatch(evidence_sha256) is None:
            raise CutoverContractError("first-business-write evidence SHA-256 is invalid")
        descriptor = self._lock()
        try:
            payload = self._read()
            if payload["status"] == "first_write_committed":
                if payload["commit_evidence_sha256"] != evidence_sha256:
                    raise CutoverContractError("idempotent commit evidence differs from journal")
                return payload
            if payload["status"] != "ready_for_commit":
                raise CutoverContractError("first business write cannot commit before all gates")
            if payload["phase_evidence_sha256"][PHASES[-1]] != evidence_sha256:
                raise CutoverContractError(
                    "commit evidence must equal pre-first-write acceptance evidence"
                )
            payload["status"] = "first_write_committed"
            payload["rollback_eligible"] = False
            payload["first_business_write_allowed"] = True
            payload["commit_evidence_sha256"] = evidence_sha256
            payload["committed_at"] = _now()
            _append_event(
                payload,
                kind="first_write_committed",
                evidence_sha256=evidence_sha256,
            )
            return self._write(payload)
        finally:
            os.close(descriptor)


MUTATING_ACTIONS = {
    "create-journal",
    "begin-phase",
    "complete-phase",
    "record-rollback",
    "commit-first-business-write",
}


def _planned_transition(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": "planned",
        "action": args.action,
        "phase": args.phase,
        "required_apply_confirmation": APPLY_CONFIRMATION,
        "required_commit_confirmation": (
            FIRST_WRITE_COMMIT_CONFIRMATION
            if args.action == "commit-first-business-write"
            else None
        ),
        "journal_mutated": False,
        "production_contacted": False,
    }


def _require_action_arguments(args: argparse.Namespace) -> None:
    if args.action in {"begin-phase", "complete-phase"} and args.phase not in PHASES:
        raise CutoverContractError("--phase must name the exact next cutover phase")
    if args.action in {
        "complete-phase",
        "record-rollback",
        "commit-first-business-write",
    } and SHA256_RE.fullmatch(str(args.evidence_sha256 or "")) is None:
        raise CutoverContractError("--evidence-sha256 is required for this action")
    if args.action == "record-rollback" and not str(args.reason or "").strip():
        raise CutoverContractError("--reason is required for rollback evidence")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--action",
        choices=(
            "plan",
            "show-journal",
            "create-journal",
            "begin-phase",
            "complete-phase",
            "record-rollback",
            "commit-first-business-write",
        ),
        default="plan",
    )
    parser.add_argument("--phase")
    parser.add_argument("--evidence-sha256")
    parser.add_argument("--reason")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--commit-confirm")
    args = parser.parse_args()

    try:
        if os.geteuid() != 0:
            raise CutoverContractError("production cutover controller must run as root")
        manifest, manifest_sha256 = read_root_only_manifest(args.manifest)
        _require_action_arguments(args)
        plan = render_plan(
            manifest,
            manifest_sha256=manifest_sha256,
            manifest_path=args.manifest,
        )
        journal = ProductionCutoverJournal(
            Path(manifest["deployment"]["controller_journal_path"])
        )
        if args.action == "plan":
            if args.apply:
                raise CutoverContractError("plan action never accepts --apply")
            payload = plan
        elif args.action == "show-journal":
            if args.apply:
                raise CutoverContractError("show-journal never accepts --apply")
            state = journal.assert_bindings(
                manifest_sha256=manifest_sha256,
                plan_sha256=plan["plan_sha256"],
                campaign_id=manifest["campaign_id"],
                operation_id=manifest["operation_id"],
                release_sha=manifest["release_sha"],
                legacy_release_sha=manifest["legacy_release_sha"],
            )
            payload = {"status": "observed", "journal": state}
        elif not args.apply:
            payload = _planned_transition(args)
        else:
            if args.confirm != APPLY_CONFIRMATION:
                raise CutoverContractError(f"--apply requires --confirm {APPLY_CONFIRMATION}")
            if args.action == "create-journal":
                state = journal.create(
                    manifest_sha256=manifest_sha256,
                    plan_sha256=plan["plan_sha256"],
                    campaign_id=manifest["campaign_id"],
                    operation_id=manifest["operation_id"],
                    release_sha=manifest["release_sha"],
                    legacy_release_sha=manifest["legacy_release_sha"],
                )
            else:
                journal.assert_bindings(
                    manifest_sha256=manifest_sha256,
                    plan_sha256=plan["plan_sha256"],
                    campaign_id=manifest["campaign_id"],
                    operation_id=manifest["operation_id"],
                    release_sha=manifest["release_sha"],
                    legacy_release_sha=manifest["legacy_release_sha"],
                )
                if args.action == "begin-phase":
                    state = journal.begin_phase(str(args.phase))
                elif args.action == "complete-phase":
                    state = journal.complete_phase(
                        str(args.phase), evidence_sha256=str(args.evidence_sha256)
                    )
                elif args.action == "record-rollback":
                    state = journal.record_rollback(
                        reason=str(args.reason),
                        evidence_sha256=str(args.evidence_sha256),
                    )
                else:
                    state = journal.commit_first_business_write(
                        evidence_sha256=str(args.evidence_sha256),
                        confirmation=str(args.commit_confirm or ""),
                    )
            payload = {
                "status": state["status"],
                "action": args.action,
                "journal": state,
                "production_contacted": False,
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
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
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
