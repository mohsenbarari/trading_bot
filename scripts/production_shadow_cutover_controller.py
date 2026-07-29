#!/usr/bin/env python3
"""Fail-closed controller contract for a side-by-side production cutover.

This module does not execute production host commands.  It verifies one
root-only manifest, renders fixed argv for a future bounded host agent, runs
the immutable release-bound local evidence verifier, and maintains a
crash-visible local journal.  The separate first-business-write boundary is
hard-disabled until the forward-only executor and bounded operation workers
exist because rollback to the untouched legacy database ends at that boundary.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Iterable, Mapping, Sequence
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
from core.docker_image_identity import (  # noqa: E402
    DockerImageIdentityError,
    verify_content_descriptor,
)
from core.production_shadow_authorization import (  # noqa: E402
    ProductionShadowAuthorizationError,
    authorization_basis_sha256,
    verify_authorization_documents,
)
from core.canonical_json import canonical_json_bytes  # noqa: E402
from core.three_site_topology import (  # noqa: E402
    BOT_FI_HOST,
    PRODUCTION_WITNESS_HOST,
    WEBAPP_FI_HOST,
    WEBAPP_IR_HOST,
)
from scripts import production_shadow_convergence_runtime_targets as runtime_targets  # noqa: E402
from scripts import production_shadow_remote_receiver_signing_policy as receiver_policy  # noqa: E402


MANIFEST_SCHEMA = runtime_targets.CUTOVER_MANIFEST_SCHEMA
LEGACY_MANIFEST_SCHEMA = runtime_targets.LEGACY_CUTOVER_MANIFEST_SCHEMA
PLAN_SCHEMA = "production-shadow-cutover-plan-v1"
JOURNAL_SCHEMA = "production-shadow-cutover-journal-v1"
PHASE_VERIFICATION_SCHEMA = "production-shadow-phase-evidence-verification-v1"
APPLY_CONFIRMATION = "APPLY-PRODUCTION-SHADOW-CUTOVER-JOURNAL"
FIRST_WRITE_COMMIT_CONFIRMATION = "COMMIT-PRODUCTION-SHADOW-FIRST-BUSINESS-WRITE"
CONTROLLER_PATH = "/usr/local/sbin/trading-bot-production-shadow-controller"
PYTHON = "/usr/bin/python3"
REMOTE_AGENT_RELATIVE_PATH = PurePosixPath(
    "scripts/production_shadow_host_agent.py"
)
REMOTE_AGENT_CONTRACT_ROOT = PurePosixPath(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
HOST_AGENT_CONTRACT_SCHEMA = "production-shadow-host-agent-contract-v1"
PHASE_EVIDENCE_VERIFIER_RELATIVE_PATH = (
    "scripts/verify_production_shadow_phase_evidence.py"
)
PRODUCTION_HOSTNAME = "coin.gold-trade.ir"
LEGACY_COMPOSE_PROJECT = "trading_bot"
ZERO_SHA256 = "0" * 64
IRREVERSIBLE_COMMIT_ENABLED = False
MAX_RELEASE_VERIFIER_STREAM_BYTES = 64 * 1024
RELEASE_VERIFIER_TIMEOUT_SECONDS = 120.0
RELEASE_VERIFIER_TERM_SECONDS = 2.0
RELEASE_VERIFIER_POLL_SECONDS = 0.01
RELEASE_VERIFIER_TREE_QUIESCENCE_SECONDS = 0.05
MAX_RELEASE_VERIFIER_PROCESS_SNAPSHOT_MEMBERS = 65536
MAX_RELEASE_VERIFIER_PROCESS_TREE_MEMBERS = 8192
PR_SET_CHILD_SUBREAPER = 36
_RELEASE_VERIFIER_RUN_LOCK = threading.Lock()
FORWARD_ONLY_COMMIT_GATE = "journal_forward_only_commit_gate"
PRECOMMIT_JOURNAL_STATUS = "rollback-eligible-precommit"
POSTCOMMIT_JOURNAL_STATUS = "forward-only-committed"
PRODUCTION_VHOSTS = {
    "bot_fi": ("coin.362514.ir", "mini-app.362514.ir"),
    "webapp_fi": (PRODUCTION_HOSTNAME,),
}
LEGACY_REDIS_POLICY = "sealed-rollback-evidence-only"
SHADOW_REDIS_POLICY = "pristine-empty-no-restore"
NGINX_GENERATION_ARTIFACT_FIELDS = {
    "legacy-normal": "nginx_rollback_generation_sha256",
    "legacy-frozen": "nginx_freeze_generation_sha256",
    "shadow-readonly": "nginx_shadow_readonly_generation_sha256",
    "shadow-writable": "nginx_shadow_writable_generation_sha256",
}

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{7,62}$")
IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
DOCKER_RUNTIME_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA = (
    runtime_targets.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA
)
CONVERGENCE_RUNTIME_TARGETS_FILENAME = (
    runtime_targets.CONVERGENCE_RUNTIME_TARGETS_FILENAME
)
CONVERGENCE_RUNTIME_TARGET_DESCRIPTOR_FIELDS = (
    runtime_targets.CONVERGENCE_RUNTIME_TARGET_DESCRIPTOR_FIELDS
)
MANIFEST_CAPABILITIES = runtime_targets.RUNTIME_TARGET_CAPABILITIES
IMAGE_ARTIFACT_FIELDS = frozenset(
    {
        "archive_sha256",
        "archive_bytes",
        "config_digest",
        "content_descriptor",
        "content_identity",
    }
)
PHASE_VERIFICATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "phase",
        "operation",
        "campaign_id",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase_evidence_schema_sha256",
        "manifest_artifact_bindings_sha256",
        "prior_phase_evidence_closure_sha256",
        "phase_input_closure_sha256",
        "prior_phase_count",
        "evidence_sha256",
        "verified_roles",
        "verified_claim_count",
        "captured_at",
        "verified_at",
        "production_contacted",
    }
)

MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "capabilities",
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
        "release_bundle_bytes",
        "role_materials",
        "image_artifacts",
        "role_runtime_image_ids",
        "convergence_runtime_targets",
        "remote_receiver_signing_policies",
        "postgres_runtime_uid",
        "postgres_runtime_gid",
        "postgres_image_ref",
        "legacy_bot_rollback_sha256",
        "legacy_webapp_rollback_sha256",
        "legacy_bot_redis_rollback_sha256",
        "legacy_webapp_redis_rollback_sha256",
        "shadow_compose_sha256",
        "cutover_approval_sha256",
        "human_approval_policy_sha256",
        "nginx_freeze_generation_sha256",
        "nginx_rollback_generation_sha256",
        "nginx_shadow_readonly_generation_sha256",
        "nginx_shadow_writable_generation_sha256",
        "postcommit_executor_contract_sha256",
        "phase_evidence_schema_sha256",
        "host_agent_sha256",
        "host_agent_contract_sha256",
        "phase_evidence_verifier_sha256",
    }
)
REMOTE_RECEIVER_POLICY_ROLES = ("webapp_ir", "witness")
REMOTE_RECEIVER_POLICY_CONTRACT_FIELDS = frozenset(
    {
        "policy_file_sha256",
        "policy_sha256",
        "key_id",
        "public_key_sha256",
        "receiver_sha256",
        "worker_sha256",
    }
)
ROLE_MATERIAL_FIELDS = frozenset(
    {"sha256", "bytes", "transport", "format"}
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
        "legacy_redis_restore_forbidden",
        "pristine_shadow_redis_required",
        "nginx_generation_coordinated_all_vhosts_required",
        "postcommit_forward_recovery_required",
        "iran_public_route_prepromotion_forbidden",
        "iran_effects_prepromotion_forbidden",
        "queue_rehydrate_before_claim_required",
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
    forward_only: bool = False
    first_write_boundary: bool = False
    business_write_allowed: bool = False
    required_journal_status: str = PRECOMMIT_JOURNAL_STATUS
    requirements: tuple[str, ...] = ()
    nginx_generations: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifiedPhaseCompletion:
    """Opaque controller result produced by the release-bound verifier."""

    phase: str
    evidence_sha256: str
    receipt_sha256: str


PREPARATION_SPECS: tuple[PhaseSpec, ...] = (
    PhaseSpec(
        "reversible_verify_installation",
        "verify-installation",
        ("bot_fi", "webapp_fi"),
        "Verify the exact staged release, artifacts, images, role material, and Compose closure.",
        False,
    ),
    PhaseSpec(
        "reversible_bootstrap_database",
        "bootstrap-database",
        ("bot_fi", "webapp_fi"),
        "Create or safely adopt only the operation-owned shadow database.",
        True,
    ),
    PhaseSpec(
        "reversible_restore_shadow",
        "restore-shadow",
        ("bot_fi", "webapp_fi"),
        "Restore the bound PostgreSQL and file artifacts without restoring Redis.",
        True,
    ),
    PhaseSpec(
        "reversible_prepare_shadow",
        "prepare-shadow",
        ("bot_fi", "webapp_fi"),
        "Apply the exact forward migration corridor, roles, and database fence.",
        True,
    ),
    PhaseSpec(
        "reversible_readonly_acceptance",
        "readonly-acceptance",
        ("bot_fi", "webapp_fi"),
        "Run exact-release read-only acceptance with provider capabilities absent.",
        True,
    ),
)


# Every phase below remains before the first business write. The controller
# renders commands and journals intent only; it never contacts a host.
PHASE_SPECS: tuple[PhaseSpec, ...] = (
    PhaseSpec(
        "pre_freeze_evidence",
        "capture-pre-freeze-evidence",
        ("bot_fi", "webapp_fi", "webapp_ir", "witness"),
        "Capture immutable health, release, topology, backup, route, and rollback evidence.",
        False,
        requirements=(
            "attest exact releases, images, host identities, routes, and legacy rollback sets",
            "capture the active Nginx generation on Bot-FI and WebApp-FI",
        ),
        nginx_generations=("legacy-normal",),
    ),
    PhaseSpec(
        "shadow_startup_normalization",
        "normalize-operation-owned-shadow-startup-state",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Normalize only operation-owned containers to the audited stopped baseline.",
        True,
        requirements=(
            "no legacy container, service, volume, current path, or route mutation",
            "zero unplanned container delta before any shadow start",
        ),
    ),
    PhaseSpec(
        "freeze_generation_install",
        "install-coordinated-three-vhost-freeze-generations",
        ("bot_fi", "webapp_fi"),
        "Stage matching reversible write-block generations for all three production vhosts.",
        True,
        requirements=(
            "Bot-FI vhosts coin.362514.ir and mini-app.362514.ir are both write-blocked",
            "WebApp-FI vhost coin.gold-trade.ir is write-blocked",
            "the previous complete Nginx generation remains restorable",
        ),
        nginx_generations=("legacy-normal", "legacy-frozen"),
    ),
    PhaseSpec(
        "freeze_generation_test",
        "test-coordinated-three-vhost-freeze-generations",
        ("bot_fi", "webapp_fi"),
        "Test the complete three-vhost generation on both Nginx hosts.",
        False,
        requirements=(
            "nginx configuration tests pass on Bot-FI and WebApp-FI",
            "no partial-host activation is permitted",
        ),
        nginx_generations=("legacy-frozen",),
    ),
    PhaseSpec(
        "freeze_generation_activate",
        "activate-ordered-fail-closed-three-vhost-freeze",
        ("bot_fi", "webapp_fi"),
        "Activate the tested write-block generations in an ordered fail-closed sequence.",
        True,
        requirements=(
            "each host activates and reads back its manifest-bound generation",
            "activation failure compensates by restoring every host already switched",
        ),
        nginx_generations=("legacy-frozen",),
    ),
    PhaseSpec(
        "stop_legacy_writers",
        "stop-legacy-writer-processes",
        ("bot_fi", "webapp_fi"),
        "Stop only legacy processes capable of authoritative writes.",
        True,
        requirements=(
            "stop every database writer and every process capable of mutating uploads or audit files",
            "prove no GET, logging, sync, worker, or background path can mutate final file artifacts",
        ),
    ),
    PhaseSpec(
        "zero_writer_surface_readback",
        "verify-zero-write-capable-routes-processes-and-clients",
        ("bot_fi", "webapp_fi", "witness"),
        "Externally read back all vhosts and prove no write-capable route, process, or DB client remains.",
        False,
        requirements=(
            "external readback covers coin.362514.ir, mini-app.362514.ir, and coin.gold-trade.ir",
            "Bot-FI and WebApp-FI have zero legacy writer processes and DB writer clients",
            "Bot-FI and WebApp-FI have zero file-mutating processes against uploads and audit trees",
        ),
    ),
    PhaseSpec(
        "final_snapshot_hashes",
        "capture-final-frozen-snapshot-hashes",
        ("bot_fi", "webapp_fi"),
        "Capture final DB/file snapshots and seal legacy Redis only as rollback evidence.",
        True,
        requirements=(
            "snapshot PostgreSQL, uploads, audit, and manifests for shadow restore",
            "bind stable pre/post stat and tree hashes while file writers remain quiesced or use an atomic filesystem snapshot",
            "hash legacy Redis RDB/AOF as sealed rollback evidence only",
            "exclude every legacy Redis byte from shadow restore artifacts",
        ),
    ),
    PhaseSpec(
        "pristine_shadow_redis",
        "verify-pristine-empty-shadow-redis-targets",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Prove every operation-owned Redis target is secure and pristine before first start.",
        False,
        requirements=(
            "root-owned non-symlink mode-0700 directory chain",
            "empty target and no RDB, AOF, session, cache, OTP, or queue restore",
        ),
    ),
    PhaseSpec(
        "shadow_restore",
        "restore-shadow-postgres-and-files-without-redis",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Restore reviewed PostgreSQL and file artifacts only into operation-owned shadow volumes.",
        True,
        requirements=(
            "restore PostgreSQL and explicitly reviewed uploads/audit artifacts only",
            "legacy Redis restore is prohibited",
            "WebApp-IR PostgreSQL restore is stdin-only",
        ),
    ),
    PhaseSpec(
        "shadow_roles_pre_migration",
        "bind-shadow-least-privilege-roles-before-migration",
        ("webapp_fi", "webapp_ir"),
        "Create the WebApp role set needed to run exact-release migrations.",
        True,
    ),
    PhaseSpec(
        "shadow_migrate",
        "resume-safe-migrate-shadow-databases-forward",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Run the exact reviewed Alembic chain with crash-safe resume.",
        True,
        requirements=(
            "accept only source, target, or on-chain intermediate Alembic revisions",
            "repair only reviewed invalid or unready concurrent indexes",
            "reject every off-chain or unknown schema state",
        ),
    ),
    PhaseSpec(
        "shadow_roles_post_migration",
        "rebind-shadow-least-privilege-roles-after-migration",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Re-apply least-privilege grants after migration-created objects exist.",
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
        "Require schema, role, fence, queue, parity, DR, TLS, blob, and firewall evidence.",
        False,
        requirements=(
            "verify signed Witness release/health attestation and live singleton lease",
            "verify exact DR TLS SAN/EKU/expiry/key/CA/peer handshakes",
            "verify FI/IR blob keyring compatibility and encrypted exact-version round trip",
            "verify operation-labelled nftables or DOCKER-USER destination allowlists",
        ),
    ),
    PhaseSpec(
        "readonly_upstream_switch",
        "switch-three-vhost-upstreams-shadow-readonly",
        ("bot_fi", "webapp_fi"),
        "Switch all three vhosts to shadow readonly upstreams as one tested generation.",
        True,
        requirements=(
            "all vhosts remain write-blocked",
            "WebApp-IR remains unrouted and effects-disabled",
            "each host reads back the expected readonly upstream generation",
        ),
        nginx_generations=("legacy-frozen", "shadow-readonly"),
    ),
    PhaseSpec(
        "precommit_no_due_mutator_delta",
        "verify-no-due-jobs-effects-leases-or-provider-attempts",
        ("bot_fi", "webapp_fi", "webapp_ir"),
        "Capture zero-delta baselines while every business mutator remains stopped.",
        False,
        requirements=(
            "all APIs remain background-disabled and all effects/Bot polling remain stopped",
            "no due OTP, inline SMS, DR effect outbox, Telegram lease, dispatch, or provider attempt",
            "authoritative event/change sequences are captured as durable baselines",
        ),
    ),
    PhaseSpec(
        "precommit_provider_free_queue_rehydrate",
        "rehydrate-bot-limiter-provider-free-one-shot",
        ("bot_fi",),
        "Rehydrate fresh Redis from PostgreSQL with no token, provider egress, claim, or polling.",
        True,
        requirements=(
            "isolated DB/Redis-only one-shot receives no BOT_TOKEN or provider network",
            "source rows, leases, dispatch_started_at, outcomes, and provider attempts remain unchanged",
        ),
    ),
    PhaseSpec(
        "precommit_irreversible_effect_watchers",
        "establish-durable-first-effect-watchers",
        ("bot_fi", "webapp_fi", "webapp_ir", "witness"),
        "Install durable baselines and watchers before the forward-only commit.",
        False,
        requirements=(
            "watch Telegram dispatch_started_at commits before provider calls",
            "watch DR effect outbox inflight/attempt commits and inline SMS claims",
            "watch authoritative event/change sequences, lease epoch, and public route generation",
        ),
    ),
    PhaseSpec(
        "pre_first_write_acceptance",
        "verify-pre-first-write-acceptance",
        ("bot_fi", "webapp_fi", "webapp_ir", "witness"),
        "Re-read every gate and prove rollback remains available before commit.",
        False,
        nginx_generations=("shadow-readonly", "shadow-writable"),
    ),
)
PHASES = tuple(spec.phase for spec in PHASE_SPECS)

POSTCOMMIT_SPECS: tuple[PhaseSpec, ...] = (
    PhaseSpec(
        "postcommit_activate_webapp_apis",
        "activate-fi-ir-apis-with-background-jobs",
        ("webapp_fi", "webapp_ir"),
        "Start exact-release FI and IR APIs with BACKGROUND_JOBS_ENABLED=true.",
        True,
        forward_only=True,
        business_write_allowed=True,
        required_journal_status=POSTCOMMIT_JOURNAL_STATUS,
        requirements=(
            "both WebApp APIs run with BACKGROUND_JOBS_ENABLED=true",
            "WebApp-IR remains absent from public routes and effects remain stopped",
        ),
    ),
    PhaseSpec(
        "postcommit_activate_fi_effects",
        "activate-webapp-fi-effects-only",
        ("webapp_fi",),
        "Activate provider effects only on WebApp-FI.",
        True,
        forward_only=True,
        business_write_allowed=True,
        required_journal_status=POSTCOMMIT_JOURNAL_STATUS,
        requirements=("WebApp-IR effects remain disabled",),
    ),
    PhaseSpec(
        "postcommit_activate_bot_worker_watch",
        "activate-bot-worker-and-watch-first-irreversible-effect",
        ("bot_fi",),
        "Start the sole Telegram execution owner and atomically watch its first DB/provider effect.",
        True,
        forward_only=True,
        business_write_allowed=True,
        required_journal_status=POSTCOMMIT_JOURNAL_STATUS,
        requirements=(
            "provider-free queue rehydration and durable watchers already completed",
            "journal is already in forward-only recovery before Bot polling starts",
            "any naturally occurring first DB write or provider attempt is captured",
            "no synthetic Telegram effect is required",
        ),
    ),
    PhaseSpec(
        "postcommit_forward_only_unblock",
        "activate-forward-only-three-vhost-generations",
        ("bot_fi", "webapp_fi"),
        "Activate the write-capable generations in a forward-only ordered sequence.",
        True,
        forward_only=True,
        business_write_allowed=True,
        required_journal_status=POSTCOMMIT_JOURNAL_STATUS,
        requirements=(
            "Bot watcher is armed and Bot health is proven without requiring a send",
            "each host reads back the exact target generation after activation",
            "partial failure retries the same generation and never restores legacy writers",
        ),
        nginx_generations=("shadow-writable",),
    ),
    PhaseSpec(
        "postcommit_first_write_observation",
        "observe-first-write-provider-effect-and-replication",
        ("bot_fi", "webapp_fi", "webapp_ir", "witness"),
        "Observe the first write, provider effect, DR projection, lease, and queue result.",
        False,
        forward_only=True,
        business_write_allowed=True,
        required_journal_status=POSTCOMMIT_JOURNAL_STATUS,
        requirements=(
            "first write is recorded exactly once",
            "forward recovery remains the only recovery mode",
        ),
    ),
)

ROLLBACK_SPECS: tuple[PhaseSpec, ...] = (
    PhaseSpec(
        "rollback_refence_and_revoke_lease",
        "refence-shadow-fi-and-revoke-witness-lease",
        ("webapp_fi", "webapp_ir", "witness"),
        "Re-fence shadow writer authority and expire the operation Witness lease.",
        True,
        requirements=(
            "WebApp-FI and WebApp-IR are fenced",
            "operation lease is revoked or expired at the expected epoch",
        ),
    ),
    PhaseSpec(
        "rollback_lease_readback",
        "verify-no-live-shadow-witness-lease",
        ("webapp_fi", "webapp_ir", "witness"),
        "Read back null shadow writer authority and no live operation lease.",
        False,
    ),
    PhaseSpec(
        "rollback_readonly_generation",
        "restore-three-vhost-legacy-readonly-generation",
        ("bot_fi", "webapp_fi"),
        "Stage the manifest-bound legacy readonly generation on both Nginx hosts.",
        True,
        nginx_generations=("legacy-frozen",),
    ),
    PhaseSpec(
        "rollback_nginx_test",
        "test-production-nginx-configuration",
        ("bot_fi", "webapp_fi"),
        "Validate the complete legacy readonly generation on both hosts.",
        False,
        nginx_generations=("legacy-frozen",),
    ),
    PhaseSpec(
        "rollback_nginx_reload",
        "reload-production-nginx-legacy-readonly",
        ("bot_fi", "webapp_fi"),
        "Activate the validated legacy readonly generation on both hosts.",
        True,
        nginx_generations=("legacy-frozen",),
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
        "restore-three-vhost-legacy-write-policy",
        ("bot_fi", "webapp_fi"),
        "Restore the legacy write policy on all three vhosts only after legacy health is proven.",
        True,
        nginx_generations=("legacy-normal",),
    ),
    PhaseSpec(
        "rollback_final_readback",
        "verify-legacy-production-readback",
        ("bot_fi", "webapp_fi", "witness"),
        "Externally verify all three vhosts, legacy health, null lease, and routing after rollback.",
        False,
        nginx_generations=("legacy-normal",),
    ),
)

OPERATIONAL_GAPS = (
    {
        "component": str(REMOTE_AGENT_RELATIVE_PATH),
        "status": "reversible-precommit-only",
        "required_for": (
            "Five reversible precommit operations are release-bound and executable; "
            "freeze, cutover, rollback switching, and postcommit operations remain blocked."
        ),
    },
    {
        "component": "immutable-release-image-compose-verifier",
        "status": "implemented-for-reversible-precommit",
        "required_for": (
            "The installed precommit worker verifies release tree, four image "
            "archives/IDs, role Compose, role material, and operation identity."
        ),
    },
    {
        "component": "coordinated-three-vhost-nginx-generation-worker",
        "status": "missing",
        "required_for": (
            "Atomic install/test/activate/rollback across both Bot-FI vhosts "
            "and the WebApp-FI vhost, with external readback."
        ),
    },
    {
        "component": "final-frozen-snapshot-worker",
        "status": "missing",
        "required_for": (
            "Final DB/uploads/audit restore material and sealed legacy Redis "
            "rollback-only hashes."
        ),
    },
    {
        "component": "resume-safe-shadow-restore-migration-role-fence-worker",
        "status": "implemented-for-reversible-precommit",
        "required_for": (
            "Bot-FI/WebApp-FI use the root-only precommit journal; WA-IR uses "
            "its independent operation state. Both prohibit legacy Redis restore."
        ),
    },
    {
        "component": "signed-witness-attestation-and-live-lease-validator",
        "status": "missing",
        "required_for": (
            "Signed exact-version health/release/TLS attestation plus immediate "
            "singleton lease, epoch, and current/previous key readback."
        ),
    },
    {
        "component": "tls-blob-firewall-and-convergence-validator",
        "status": "missing",
        "required_for": (
            "Peer TLS handshakes, blob keyring round-trip, destination-allowlisted "
            "nftables/DOCKER-USER rules, queue, DR, parity, and lag evidence."
        ),
    },
    {
        "component": "readonly-upstream-and-rollback-worker",
        "status": "missing",
        "required_for": "Readonly shadow switch, pre-commit rollback rehearsal, and legacy readback.",
    },
    {
        "component": "wa-ir-private-key-csr-certificate-and-dns-worker",
        "status": "missing",
        "required_for": (
            "WA-local private key/CSR, controller-side Arvan DNS-01 issuance, "
            "curl --resolve validation, and reversible versioned DNS A update."
        ),
    },
    {
        "component": "phase-evidence-schema-verifiers",
        "status": "controller-wired-local-verifier",
        "required_for": (
            "Actual bounded workers must produce the root-only role, claim, "
            "and prior-evidence artifacts consumed by the wired verifier."
        ),
    },
    {
        "component": "postcommit-forward-recovery-executor",
        "status": "missing-hard-blocker",
        "required_for": (
            "WebApp activation, fresh-Redis/job proof, provider-free Bot queue "
            "rehydration, watched Bot start, coordinated HTTP unblock, and first-effect observation."
        ),
    },
)


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _urlsafe_json(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        _canonical_json_bytes(payload)
    ).decode("ascii")


def host_agent_contract_document() -> dict[str, Any]:
    return {
        "schema": HOST_AGENT_CONTRACT_SCHEMA,
        "production_vhosts": {
            role: list(vhosts) for role, vhosts in PRODUCTION_VHOSTS.items()
        },
        "topology": EXPECTED_TOPOLOGY,
        "policies": {
            "legacy_redis": LEGACY_REDIS_POLICY,
            "shadow_redis": SHADOW_REDIS_POLICY,
            "precommit_journal_status": PRECOMMIT_JOURNAL_STATUS,
            "postcommit_journal_status": POSTCOMMIT_JOURNAL_STATUS,
            "business_write_forbidden": "forbid",
            "business_write_forward_only": "allow-after-forward-only-commit",
        },
        "operations": [
            {
                "operation": spec.operation,
                "roles": list(spec.roles),
                "forward_only": spec.forward_only,
                "business_write_allowed": spec.business_write_allowed,
                "required_journal_status": spec.required_journal_status,
                "nginx_generations": list(spec.nginx_generations),
            }
            for spec in (
                *PREPARATION_SPECS,
                *PHASE_SPECS,
                *POSTCOMMIT_SPECS,
                *ROLLBACK_SPECS,
            )
        ],
    }


HOST_AGENT_CONTRACT_SHA256 = hashlib.sha256(
    _canonical_json_bytes(host_agent_contract_document())
).hexdigest()


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


def _shadow_project(operation_id: str) -> str:
    return f"tb3p-{operation_id.replace('-', '')}"


def _secure_root(campaign_id: str) -> PurePosixPath:
    return PurePosixPath("/root/secure-envs/trading-bot/production-cutover") / campaign_id


def _shadow_root(operation_id: str) -> PurePosixPath:
    return (
        PurePosixPath("/srv/trading-bot-three-site-production-shadow")
        / operation_id
    )


def _operation_release_root(
    operation_id: str,
    release_sha: str,
) -> PurePosixPath:
    return _shadow_root(operation_id) / "releases" / release_sha


def _remote_agent_path(
    operation_id: str,
    release_sha: str,
) -> PurePosixPath:
    return (
        _operation_release_root(operation_id, release_sha)
        / REMOTE_AGENT_RELATIVE_PATH
    )


def _remote_agent_contract_path(operation_id: str) -> PurePosixPath:
    return (
        REMOTE_AGENT_CONTRACT_ROOT
        / operation_id
        / "host-agent-contract.json"
    )


def validate_manifest(document: Any) -> dict[str, Any]:
    if runtime_targets.is_legacy_cutover_manifest_schema(document):
        raise CutoverContractError(runtime_targets.CUTOVER_V4_MIGRATION_MESSAGE)
    manifest = _require_exact_fields(document, MANIFEST_FIELDS, label="manifest")
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise CutoverContractError(
            "manifest schema is invalid; a fresh v4 template and fresh approval are required"
        )
    try:
        runtime_targets.validate_runtime_target_capabilities(
            manifest["capabilities"],
            label="manifest capabilities",
        )
    except runtime_targets.ConvergenceRuntimeTargetDescriptorError as exc:
        raise CutoverContractError("manifest capabilities are invalid") from exc
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
        "shadow_compose_project": _shadow_project(operation_id),
        "shadow_root": str(_shadow_root(operation_id)),
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
        "legacy_bot_rollback_sha256",
        "legacy_webapp_rollback_sha256",
        "legacy_bot_redis_rollback_sha256",
        "legacy_webapp_redis_rollback_sha256",
        "shadow_compose_sha256",
        "cutover_approval_sha256",
        "human_approval_policy_sha256",
        "nginx_freeze_generation_sha256",
        "nginx_rollback_generation_sha256",
        "nginx_shadow_readonly_generation_sha256",
        "nginx_shadow_writable_generation_sha256",
        "postcommit_executor_contract_sha256",
        "phase_evidence_schema_sha256",
        "host_agent_sha256",
        "host_agent_contract_sha256",
        "phase_evidence_verifier_sha256",
    ):
        if (
            not isinstance(artifacts[field], str)
            or SHA256_RE.fullmatch(artifacts[field]) is None
            or artifacts[field] == ZERO_SHA256
        ):
            raise CutoverContractError(f"artifacts.{field} is not a SHA-256 digest")
    generation_digests = {
        artifacts[field]
        for field in NGINX_GENERATION_ARTIFACT_FIELDS.values()
    }
    if len(generation_digests) != len(NGINX_GENERATION_ARTIFACT_FIELDS):
        raise CutoverContractError(
            "Nginx generation digests must be distinct across semantic states"
        )
    if artifacts["host_agent_contract_sha256"] != HOST_AGENT_CONTRACT_SHA256:
        raise CutoverContractError(
            "artifacts.host_agent_contract_sha256 differs from the controller contract"
        )
    for field in (
        "release_bundle_bytes",
    ):
        if (
            isinstance(artifacts[field], bool)
            or not isinstance(artifacts[field], int)
            or not 1 <= artifacts[field] <= 64 * 1024 * 1024 * 1024
        ):
            raise CutoverContractError(
                f"artifacts.{field} is outside its size bound"
            )
    role_materials = artifacts["role_materials"]
    if (
        not isinstance(role_materials, dict)
        or set(role_materials) != set(EXPECTED_TOPOLOGY)
    ):
        raise CutoverContractError(
            "artifacts.role_materials roles are not exact"
        )
    for role, topology in EXPECTED_TOPOLOGY.items():
        material = _require_exact_fields(
            role_materials[role],
            ROLE_MATERIAL_FIELDS,
            label=f"artifacts.role_materials.{role}",
        )
        _nonzero_material_sha = material["sha256"]
        if (
            not isinstance(_nonzero_material_sha, str)
            or SHA256_RE.fullmatch(_nonzero_material_sha) is None
            or _nonzero_material_sha == ZERO_SHA256
            or isinstance(material["bytes"], bool)
            or not isinstance(material["bytes"], int)
            or not 1 <= material["bytes"] <= 64 * 1024 * 1024 * 1024
            or material["transport"] != topology["transport"]
            or material["format"]
            != (
                "production-shadow-witness-material-tar"
                if role == "witness"
                else "production-shadow-role-material-tar"
            )
        ):
            raise CutoverContractError(
                f"artifacts.role_materials.{role} is invalid"
            )
    if len(
        {role_materials[role]["sha256"] for role in role_materials}
    ) != len(role_materials):
        raise CutoverContractError(
            "role material digests must be distinct per role"
        )
    remote_receiver_policies = artifacts["remote_receiver_signing_policies"]
    if (
        not isinstance(remote_receiver_policies, dict)
        or set(remote_receiver_policies) != set(REMOTE_RECEIVER_POLICY_ROLES)
    ):
        raise CutoverContractError(
            "remote receiver signing-policy roles are not exact"
        )
    for role in REMOTE_RECEIVER_POLICY_ROLES:
        contract = _require_exact_fields(
            remote_receiver_policies[role],
            REMOTE_RECEIVER_POLICY_CONTRACT_FIELDS,
            label=f"artifacts.remote_receiver_signing_policies.{role}",
        )
        for field in (
            "policy_file_sha256",
            "policy_sha256",
            "public_key_sha256",
            "receiver_sha256",
            "worker_sha256",
        ):
            if (
                not isinstance(contract[field], str)
                or SHA256_RE.fullmatch(contract[field]) is None
                or contract[field] == ZERO_SHA256
            ):
                raise CutoverContractError(
                    f"{role} remote receiver {field} is not a SHA-256 digest"
                )
        if (
            not isinstance(contract["key_id"], str)
            or receiver_policy.IDENTIFIER_RE.fullmatch(contract["key_id"])
            is None
        ):
            raise CutoverContractError(
                f"{role} remote receiver key id is invalid"
            )
        if contract["receiver_sha256"] == contract["worker_sha256"]:
            raise CutoverContractError(
                f"{role} remote receiver and worker source digests must differ"
            )
    image_artifacts = artifacts["image_artifacts"]
    if (
        not isinstance(image_artifacts, dict)
        or set(image_artifacts) != set(IMAGE_KINDS)
    ):
        raise CutoverContractError("image artifact roles are not exact")
    for kind in IMAGE_KINDS:
        row = _require_exact_fields(
            image_artifacts[kind],
            IMAGE_ARTIFACT_FIELDS,
            label=f"artifacts.image_artifacts.{kind}",
        )
        if (
            not isinstance(row["archive_sha256"], str)
            or SHA256_RE.fullmatch(row["archive_sha256"]) is None
            or row["archive_sha256"] == ZERO_SHA256
        ):
            raise CutoverContractError(
                f"image artifact {kind}.archive_sha256 is invalid"
            )
        if (
            not isinstance(row["config_digest"], str)
            or IMAGE_ID_RE.fullmatch(row["config_digest"]) is None
            or row["config_digest"] == f"sha256:{ZERO_SHA256}"
            or not isinstance(row["content_identity"], str)
            or IMAGE_ID_RE.fullmatch(row["content_identity"]) is None
            or row["content_identity"] == f"sha256:{ZERO_SHA256}"
            or isinstance(row["archive_bytes"], bool)
            or not isinstance(row["archive_bytes"], int)
            or not 1 <= row["archive_bytes"] <= 64 * 1024 * 1024 * 1024
        ):
            raise CutoverContractError(
                f"image artifact {kind} identity is invalid"
            )
        try:
            observed_identity = verify_content_descriptor(
                row["content_descriptor"]
            )
        except DockerImageIdentityError as exc:
            raise CutoverContractError(
                f"image artifact {kind} content descriptor is invalid"
            ) from exc
        if (
            row["content_descriptor"]["architecture"] != "amd64"
            or row["content_descriptor"]["os"] != "linux"
            or observed_identity != row["content_identity"]
        ):
            raise CutoverContractError(
                f"image artifact {kind} content identity differs"
            )
    for field in ("archive_sha256", "config_digest", "content_identity"):
        if len(
            {image_artifacts[kind][field] for kind in IMAGE_KINDS}
        ) != len(IMAGE_KINDS):
            raise CutoverContractError(
                f"all four image {field} values must be distinct"
            )

    runtime_ids = artifacts["role_runtime_image_ids"]
    if (
        not isinstance(runtime_ids, dict)
        or set(runtime_ids) != set(DOCKER_RUNTIME_ROLES)
    ):
        raise CutoverContractError(
            "runtime image inventory must contain exactly the three Docker roles"
        )
    for role in DOCKER_RUNTIME_ROLES:
        role_ids = runtime_ids[role]
        if (
            not isinstance(role_ids, dict)
            or set(role_ids) != set(IMAGE_KINDS)
            or any(
                not isinstance(value, str)
                or IMAGE_ID_RE.fullmatch(value) is None
                or value == f"sha256:{ZERO_SHA256}"
                for value in role_ids.values()
            )
            or len(set(role_ids.values())) != len(IMAGE_KINDS)
        ):
            raise CutoverContractError(
                f"runtime image inventory for {role} is invalid"
            )
    try:
        runtime_targets.validate_runtime_target_descriptor(
            artifacts["convergence_runtime_targets"],
            label="artifacts.convergence_runtime_targets",
        )
    except runtime_targets.ConvergenceRuntimeTargetDescriptorError as exc:
        raise CutoverContractError(
            "convergence runtime target descriptor is invalid"
        ) from exc
    if (
        artifacts["postgres_runtime_uid"] != 70
        or artifacts["postgres_runtime_gid"] != 70
    ):
        raise CutoverContractError(
            "PostgreSQL runtime UID/GID must match the immutable image contract"
        )
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
    if payload != canonical_json_bytes(document):
        raise CutoverContractError(
            "production cutover manifest is not canonical JSON"
        )
    manifest = validate_manifest(document)
    read_runtime_target_derivation_receipt(
        manifest,
        manifest_path=path,
        owner_uid=owner_uid,
    )
    return manifest, hashlib.sha256(payload).hexdigest()


def read_runtime_target_derivation_receipt(
    manifest: Mapping[str, Any],
    *,
    manifest_path: Path,
    owner_uid: int = 0,
) -> bytes:
    """Reopen the exact root-only receipt for an already validated manifest."""

    return _validate_runtime_target_derivation_receipt_for_manifest(
        manifest,
        manifest_path=manifest_path,
        owner_uid=owner_uid,
    )


def _validate_runtime_target_derivation_receipt_for_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_path: Path,
    owner_uid: int,
) -> bytes:
    """Reopen the template-builder receipt before accepting final manifest IO."""

    pending = json.loads(canonical_json_bytes(dict(manifest)))
    pending["artifacts"]["cutover_approval_sha256"] = ZERO_SHA256
    pending_payload = canonical_json_bytes(pending)
    try:
        receipt_path = runtime_targets.runtime_target_derivation_receipt_path(
            manifest_path
        )
        receipt_raw = read_secure_bytes(
            receipt_path,
            label="runtime target derivation receipt",
            owner_uid=owner_uid,
            max_size=64 * 1024,
        )
        receipt = json.loads(
            receipt_raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (
        OSError,
        SecureFileError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        runtime_targets.ConvergenceRuntimeTargetDescriptorError,
    ) as exc:
        raise CutoverContractError(
            "runtime target derivation receipt is unavailable or unsafe"
        ) from exc
    if receipt_raw != canonical_json_bytes(receipt):
        raise CutoverContractError(
            "runtime target derivation receipt is not canonical JSON"
        )
    try:
        runtime_targets.validate_runtime_target_derivation_receipt(
            receipt,
            campaign_id=manifest["campaign_id"],
            operation_id=manifest["operation_id"],
            release_sha=manifest["release_sha"],
            template_sha256=hashlib.sha256(pending_payload).hexdigest(),
            authorization_basis_sha256=authorization_basis_sha256(pending),
            canonical_compose_sha256=manifest["artifacts"]["shadow_compose_sha256"],
            convergence_runtime_targets=manifest["artifacts"][
                "convergence_runtime_targets"
            ],
            label="runtime target derivation receipt",
        )
    except (
        KeyError,
        runtime_targets.ConvergenceRuntimeTargetDescriptorError,
    ) as exc:
        raise CutoverContractError(
            "runtime target derivation receipt does not bind the final manifest"
        ) from exc
    return receipt_raw


def _agent_args(
    manifest: dict[str, Any],
    *,
    manifest_sha256: str,
    role: str,
    operation: str,
    business_write_allowed: bool,
    required_journal_status: str,
    execute: bool = False,
) -> list[str]:
    topology = manifest["topology"][role]
    remote_agent_path = str(
        _remote_agent_path(
            manifest["operation_id"],
            manifest["release_sha"],
        )
    )
    remote_agent_contract_path = str(
        _remote_agent_contract_path(manifest["operation_id"])
    )
    agent = [
        "/usr/bin/python3",
        "-I",
        remote_agent_path,
        "--operation",
        operation,
        "--business-write-policy",
        (
            "allow-after-forward-only-commit"
            if business_write_allowed
            else "forbid"
        ),
        "--required-journal-status",
        required_journal_status,
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
        "--release-bundle-bytes",
        str(manifest["artifacts"]["release_bundle_bytes"]),
        "--role-material-sha256",
        manifest["artifacts"]["role_materials"][role]["sha256"],
        "--role-material-bytes",
        str(manifest["artifacts"]["role_materials"][role]["bytes"]),
        "--role-material-format",
        manifest["artifacts"]["role_materials"][role]["format"],
        "--image-artifacts-b64",
        _urlsafe_json(manifest["artifacts"]["image_artifacts"]),
        "--runtime-image-ids-b64",
        _urlsafe_json(
            manifest["artifacts"]["role_runtime_image_ids"].get(role, {})
        ),
        "--shadow-compose-sha256",
        manifest["artifacts"]["shadow_compose_sha256"],
        "--postgres-runtime-uid",
        str(manifest["artifacts"]["postgres_runtime_uid"]),
        "--postgres-runtime-gid",
        str(manifest["artifacts"]["postgres_runtime_gid"]),
        "--shadow-project",
        manifest["deployment"]["shadow_compose_project"],
        "--shadow-root",
        manifest["deployment"]["shadow_root"],
        "--production-vhosts-b64",
        _urlsafe_json(PRODUCTION_VHOSTS),
        "--nginx-freeze-generation-sha256",
        manifest["artifacts"]["nginx_freeze_generation_sha256"],
        "--nginx-rollback-generation-sha256",
        manifest["artifacts"]["nginx_rollback_generation_sha256"],
        "--nginx-shadow-readonly-generation-sha256",
        manifest["artifacts"]["nginx_shadow_readonly_generation_sha256"],
        "--nginx-shadow-writable-generation-sha256",
        manifest["artifacts"]["nginx_shadow_writable_generation_sha256"],
        "--legacy-redis-policy",
        LEGACY_REDIS_POLICY,
        "--shadow-redis-policy",
        SHADOW_REDIS_POLICY,
        "--postcommit-executor-contract-sha256",
        manifest["artifacts"]["postcommit_executor_contract_sha256"],
        "--phase-evidence-schema-sha256",
        manifest["artifacts"]["phase_evidence_schema_sha256"],
        "--host-agent-sha256",
        manifest["artifacts"]["host_agent_sha256"],
        "--host-agent-contract",
        remote_agent_contract_path,
        "--host-agent-contract-sha256",
        manifest["artifacts"]["host_agent_contract_sha256"],
    ]
    if execute:
        agent.append("--execute")
    if topology["transport"] == "local-controller":
        return agent
    if topology["transport"] == "ssh-control-object-storage-payload-only":
        agent.extend(["--payload-transport", "object-storage-private-versioned-age"])
    return [
        "/usr/bin/ssh",
        "-F",
        "/dev/null",
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
        shlex.join(agent),
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
    initial_prerequisite: str | None = None,
    execution_supported: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous = initial_prerequisite
    for index, spec in enumerate(specs, 1):
        if spec.forward_only != (
            spec.required_journal_status == POSTCOMMIT_JOURNAL_STATUS
        ):
            raise CutoverContractError(
                f"phase {spec.phase} has inconsistent forward-only journal binding"
            )
        if spec.business_write_allowed and not spec.forward_only:
            raise CutoverContractError(
                f"phase {spec.phase} permits writes outside forward-only recovery"
            )
        if (
            len(set(spec.nginx_generations)) != len(spec.nginx_generations)
            or any(
                state not in NGINX_GENERATION_ARTIFACT_FIELDS
                for state in spec.nginx_generations
            )
            or tuple(
                state
                for state in NGINX_GENERATION_ARTIFACT_FIELDS
                if state in spec.nginx_generations
            )
            != spec.nginx_generations
        ):
            raise CutoverContractError(
                f"phase {spec.phase} has invalid Nginx generation bindings"
            )
        if "shadow-writable" in spec.nginx_generations and not (
            spec.phase == "pre_first_write_acceptance" or spec.forward_only
        ):
            raise CutoverContractError(
                f"phase {spec.phase} binds writable Nginx before the commit boundary"
            )
        nginx_generation_bindings = {
            state: manifest["artifacts"][
                NGINX_GENERATION_ARTIFACT_FIELDS[state]
            ]
            for state in spec.nginx_generations
        }
        commands = [
            {
                "command_id": f"{spec.phase}.{role}",
                "role": role,
                "argv": _agent_args(
                    manifest,
                    manifest_sha256=manifest_sha256,
                    role=role,
                    operation=spec.operation,
                    business_write_allowed=spec.business_write_allowed,
                    required_journal_status=spec.required_journal_status,
                    execute=execution_supported,
                ),
                "required": True,
                "render_only": not execution_supported,
                "executor_available": execution_supported,
                "requires_live_state_recheck": True,
                "business_write_allowed": spec.business_write_allowed,
                "required_journal_status": spec.required_journal_status,
                "approval_sha256": manifest["artifacts"]["cutover_approval_sha256"],
                "nginx_generation_bindings": nginx_generation_bindings,
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
                "business_write_allowed": spec.business_write_allowed,
                "forward_only": spec.forward_only,
                "first_write_boundary": spec.first_write_boundary,
                "required_journal_status": spec.required_journal_status,
                "requirements": list(spec.requirements),
                "nginx_generation_bindings": nginx_generation_bindings,
                "execution_supported": execution_supported,
                "journal_begin_required_before_commands": True,
                "journal_completion_requires_release_verifier_receipt": True,
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
    preparation = _render_specs(
        manifest,
        manifest_sha256=manifest_sha256,
        specs=PREPARATION_SPECS,
        execution_supported=True,
    )
    rollback = _render_specs(
        manifest, manifest_sha256=manifest_sha256, specs=ROLLBACK_SPECS
    )
    postcommit = _render_specs(
        manifest,
        manifest_sha256=manifest_sha256,
        specs=POSTCOMMIT_SPECS,
        initial_prerequisite=FORWARD_ONLY_COMMIT_GATE,
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
        "production_vhosts": PRODUCTION_VHOSTS,
        "reversible_preparation": {
            "execution_supported": True,
            "business_write_allowed": False,
            "freeze_allowed": False,
            "current_mutation_allowed": False,
            "legacy_mutation_allowed": False,
            "phases": preparation,
        },
        "phases": phases,
        "postcommit_forward_recovery": {
            "execution_supported": False,
            "contract_sha256": manifest["artifacts"][
                "postcommit_executor_contract_sha256"
            ],
            "first_write_boundary_phase": FORWARD_ONLY_COMMIT_GATE,
            "commands": postcommit,
        },
        "nginx_generation_transaction": {
            "hosts": ["bot_fi", "webapp_fi"],
            "vhosts": PRODUCTION_VHOSTS,
            "legacy_normal_generation_sha256": manifest["artifacts"][
                "nginx_rollback_generation_sha256"
            ],
            "freeze_generation_sha256": manifest["artifacts"][
                "nginx_freeze_generation_sha256"
            ],
            "rollback_generation_sha256": manifest["artifacts"][
                "nginx_rollback_generation_sha256"
            ],
            "shadow_readonly_generation_sha256": manifest["artifacts"][
                "nginx_shadow_readonly_generation_sha256"
            ],
            "shadow_writable_generation_sha256": manifest["artifacts"][
                "nginx_shadow_writable_generation_sha256"
            ],
            "rollback_is_legacy_normal_alias": True,
            "requires_both_host_tests_before_activation": True,
            "requires_external_readback": True,
            "cross_host_instantaneous_atomicity_claimed": False,
            "coordination_model": "ordered-fail-closed-per-host-generation-readback",
            "freeze_failure_mode": "compensating-restore-already-switched-hosts",
            "forward_unblock_failure_mode": "retry-exact-same-generation",
            "per_host_generation_readback_required": True,
        },
        "redis_contract": {
            "legacy": LEGACY_REDIS_POLICY,
            "shadow": SHADOW_REDIS_POLICY,
            "legacy_bot_rollback_sha256": manifest["artifacts"][
                "legacy_bot_redis_rollback_sha256"
            ],
            "legacy_webapp_rollback_sha256": manifest["artifacts"][
                "legacy_webapp_redis_rollback_sha256"
            ],
            "queue_rehydrate_before_bot_claims": True,
        },
        "webapp_ir_standby_contract": {
            "background_jobs_enabled": True,
            "public_route_enabled_before_promotion": False,
            "effects_enabled_before_promotion": False,
            "fresh_redis_required": True,
            "due_otp_jobs_allowed": False,
            "site_local_job_allowlist_required": True,
        },
        "phase_evidence_verification": {
            "verifier_path": str(
                _operation_release_root(
                    manifest["operation_id"],
                    manifest["release_sha"],
                )
                / PHASE_EVIDENCE_VERIFIER_RELATIVE_PATH
            ),
            "verifier_sha256": manifest["artifacts"][
                "phase_evidence_verifier_sha256"
            ],
            "contract_sha256": manifest["artifacts"][
                "phase_evidence_schema_sha256"
            ],
            "semantic_verification_required_before_journal_completion": True,
            "controller_executes_release_bound_verifier": True,
            "root_only_verification_receipt_required": True,
            "prior_evidence_files_must_match_journal": True,
            "root_only_manifest_plan_approval_journal_required": True,
            "root_only_role_and_claim_source_records_required": True,
            "expected_role_request_sha256_required": True,
            "expected_role_source_artifact_readback_sha256_required": True,
            "source_available_in_release": True,
            "exact_release_path_required": True,
            "executor_wired": True,
        },
        "host_agent_contract": {
            "path": str(
                _remote_agent_contract_path(manifest["operation_id"])
            ),
            "agent_path": str(
                _remote_agent_path(
                    manifest["operation_id"],
                    manifest["release_sha"],
                )
            ),
            "sha256": manifest["artifacts"]["host_agent_contract_sha256"],
            "agent_sha256": manifest["artifacts"]["host_agent_sha256"],
            "standalone_contract_sha256": HOST_AGENT_CONTRACT_SHA256,
            "self_hash_required": True,
            "local_host_identity_required": True,
            "operation_execution_supported": False,
            "reversible_precommit_execution_supported": True,
        },
        "rollback": {
            "eligible_until_commit_gate": True,
            "prohibited_after_commit_gate": True,
            "preserves_shadow_volumes_and_artifacts": True,
            "commands": rollback,
        },
        "first_business_write_commit_gate": {
            "enabled": IRREVERSIBLE_COMMIT_ENABLED,
            "hard_disabled": not IRREVERSIBLE_COMMIT_ENABLED,
            "irreversible_boundary": True,
            "required_completed_phase": PHASES[-1],
            "required_confirmation": FIRST_WRITE_COMMIT_CONFIRMATION,
            "prospective_argv_template": commit_argv,
            "blocked_by": [
                "postcommit-forward-recovery-executor",
                "coordinated-three-vhost-nginx-generation-worker",
            ],
            "effect": (
                "Hard-disabled while the rendered postcommit contract has no "
                "bounded executor and production operation workers."
            ),
        },
        "prohibitions": [
            "no staging path or deployment",
            "no mutation of any current path",
            "no delete, compose down, volume removal, or destructive cleanup",
            "no database downgrade",
            "no direct payload transfer to WebApp-IR",
            "no legacy Redis restore into any shadow target",
            "no WebApp-IR public route or effects before explicit promotion",
            "no business write before the explicit commit gate",
            "no rollback after the explicit commit gate",
        ],
        "operational_gaps": list(OPERATIONAL_GAPS),
    }
    plan["plan_sha256"] = _plan_hash(plan)
    return plan


def _hash_release_verifier(path: Path, *, owner_uid: int = 0) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os,
        "O_NOFOLLOW",
        0,
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CutoverContractError(
            "cannot securely open release-bound phase verifier"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > 4 * 1024 * 1024
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise CutoverContractError(
                "release-bound phase verifier artifact is unsafe"
            )
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 4 * 1024 * 1024:
                raise CutoverContractError(
                    "release-bound phase verifier artifact is oversized"
                )
            digest.update(chunk)
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
        if any(
            getattr(before, field) != getattr(after, field) for field in stable
        ):
            raise CutoverContractError(
                "release-bound phase verifier changed while being hashed"
            )
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _absolute_mapping_args(values: list[str], *, label: str) -> list[str]:
    result: list[str] = []
    observed: set[str] = set()
    for value in values:
        key, separator, raw_path = str(value).partition("=")
        path = Path(raw_path)
        if (
            not separator
            or not key
            or key in observed
            or not path.is_absolute()
            or ".." in path.parts
        ):
            raise CutoverContractError(f"{label} must use unique absolute paths")
        observed.add(key)
        result.append(f"{key}={path}")
    return result


def _validate_phase_verification_result(
    document: Any,
    *,
    phase: str,
    manifest: dict[str, Any],
    manifest_sha256: str,
    plan_sha256: str,
) -> tuple[VerifiedPhaseCompletion, bytes]:
    result = _require_exact_fields(
        document,
        PHASE_VERIFICATION_FIELDS,
        label="phase verification result",
    )
    spec = next((item for item in PHASE_SPECS if item.phase == phase), None)
    if spec is None:
        raise CutoverContractError("phase verification result names an unknown phase")
    expected = {
        "schema": PHASE_VERIFICATION_SCHEMA,
        "status": "verified",
        "phase": phase,
        "operation": spec.operation,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
        "manifest_sha256": manifest_sha256,
        "plan_sha256": plan_sha256,
        "approval_sha256": manifest["artifacts"]["cutover_approval_sha256"],
        "phase_evidence_schema_sha256": manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ],
        "manifest_artifact_bindings_sha256": hashlib.sha256(
            _canonical_json_bytes(manifest["artifacts"])
        ).hexdigest(),
        "prior_phase_count": PHASES.index(phase),
        "verified_roles": list(spec.roles),
        "production_contacted": False,
    }
    if any(result[field] != value for field, value in expected.items()):
        raise CutoverContractError(
            "release-bound phase verifier returned mismatched bindings"
        )
    for field in (
        "prior_phase_evidence_closure_sha256",
        "phase_input_closure_sha256",
        "evidence_sha256",
    ):
        if (
            not isinstance(result[field], str)
            or SHA256_RE.fullmatch(result[field]) is None
            or result[field] == ZERO_SHA256
        ):
            raise CutoverContractError(
                "release-bound phase verifier returned an invalid digest"
            )
    if (
        not isinstance(result["verified_claim_count"], int)
        or isinstance(result["verified_claim_count"], bool)
        or result["verified_claim_count"] < 1
        or not _valid_timestamp(result["captured_at"])
        or not _valid_timestamp(result["verified_at"])
    ):
        raise CutoverContractError(
            "release-bound phase verifier returned invalid verification metadata"
        )
    receipt = (
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    receipt_sha256 = hashlib.sha256(receipt).hexdigest()
    return (
        VerifiedPhaseCompletion(
            phase=phase,
            evidence_sha256=result["evidence_sha256"],
            receipt_sha256=receipt_sha256,
        ),
        receipt,
    )


@dataclass(frozen=True)
class ReleaseVerifierProcessIdentity:
    pid: int
    parent_pid: int
    process_group: int
    session_id: int
    start_time: int
    state: str

    @property
    def key(self) -> tuple[int, int]:
        return self.pid, self.start_time


def _release_verifier_process_identity(
    pid: int,
) -> ReleaseVerifierProcessIdentity | None:
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = payload[payload.rindex(") ") + 2 :].split()
        if len(fields) < 20:
            raise ValueError("short process stat")
        return ReleaseVerifierProcessIdentity(
            pid=pid,
            parent_pid=int(fields[1], 10),
            process_group=int(fields[2], 10),
            session_id=int(fields[3], 10),
            start_time=int(fields[19], 10),
            state=fields[0],
        )
    except (FileNotFoundError, ProcessLookupError):
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        raise CutoverContractError(
            "release-bound phase verifier process identity is unavailable"
        ) from exc


def _read_release_verifier_process_identity(
    pid: int,
) -> ReleaseVerifierProcessIdentity:
    identity = _release_verifier_process_identity(pid)
    if identity is None:
        raise CutoverContractError(
            "release-bound phase verifier root identity is unavailable"
        )
    return identity


def _release_verifier_process_snapshot(
) -> dict[int, ReleaseVerifierProcessIdentity]:
    observed: dict[int, ReleaseVerifierProcessIdentity] = {}
    scanned = 0
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            scanned += 1
            if scanned > MAX_RELEASE_VERIFIER_PROCESS_SNAPSHOT_MEMBERS:
                raise CutoverContractError(
                    "release-bound phase verifier process inventory "
                    "exceeds its member bound"
                )
            identity = _release_verifier_process_identity(
                int(entry.name, 10)
            )
            if identity is not None:
                observed[identity.pid] = identity
    except CutoverContractError:
        raise
    except OSError as exc:
        raise CutoverContractError(
            "release-bound phase verifier process inventory is unavailable"
        ) from exc
    return observed


def _enable_release_verifier_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise CutoverContractError(
            "release-bound phase verifier child subreaper setup failed "
            f"with errno {error}"
        )


def _release_verifier_child_baseline() -> frozenset[tuple[int, int]]:
    owner = os.getpid()
    return frozenset(
        identity.key
        for identity in _release_verifier_process_snapshot().values()
        if identity.parent_pid == owner
    )


def _owned_release_verifier_processes(
    root_identity: ReleaseVerifierProcessIdentity,
    *,
    baseline_children: frozenset[tuple[int, int]],
    tracked: dict[
        tuple[int, int],
        ReleaseVerifierProcessIdentity,
    ],
    include_zombies: bool = False,
) -> tuple[ReleaseVerifierProcessIdentity, ...]:
    snapshot = _release_verifier_process_snapshot()
    owned_ids: set[int] = set()
    observed_root = snapshot.get(root_identity.pid)
    if (
        observed_root is not None
        and observed_root.start_time == root_identity.start_time
    ):
        owned_ids.add(root_identity.pid)
    for identity in tracked.values():
        current = snapshot.get(identity.pid)
        if (
            current is not None
            and current.start_time == identity.start_time
        ):
            owned_ids.add(identity.pid)
    owner = os.getpid()
    for identity in snapshot.values():
        if (
            identity.pid != root_identity.pid
            and identity.parent_pid == owner
            and identity.key not in baseline_children
        ):
            owned_ids.add(identity.pid)
    if len(owned_ids) > MAX_RELEASE_VERIFIER_PROCESS_TREE_MEMBERS:
        raise CutoverContractError(
            "release-bound phase verifier subprocess tree "
            "exceeds its member bound"
        )
    children: dict[int, list[int]] = {}
    for identity in snapshot.values():
        children.setdefault(identity.parent_pid, []).append(identity.pid)
    pending = list(owned_ids)
    while pending:
        parent = pending.pop()
        for child in children.get(parent, ()):
            if child in owned_ids:
                continue
            owned_ids.add(child)
            if len(owned_ids) > MAX_RELEASE_VERIFIER_PROCESS_TREE_MEMBERS:
                raise CutoverContractError(
                    "release-bound phase verifier subprocess tree "
                    "exceeds its member bound"
                )
            pending.append(child)
    owned = tuple(
        identity
        for pid, identity in snapshot.items()
        if pid in owned_ids
    )
    for identity in owned:
        tracked[identity.key] = identity
    if len(tracked) > MAX_RELEASE_VERIFIER_PROCESS_TREE_MEMBERS:
        raise CutoverContractError(
            "release-bound phase verifier tracked subprocess tree "
            "exceeds its member bound"
        )
    return tuple(
        identity
        for identity in owned
        if include_zombies or identity.state != "Z"
    )


def _release_verifier_identity_is_live(
    identity: ReleaseVerifierProcessIdentity,
) -> bool:
    current = _release_verifier_process_identity(identity.pid)
    return (
        current is not None
        and current.start_time == identity.start_time
        and current.state != "Z"
    )


def _reap_release_verifier_zombies(
    *,
    root_identity: ReleaseVerifierProcessIdentity,
    baseline_children: frozenset[tuple[int, int]],
    tracked: dict[
        tuple[int, int],
        ReleaseVerifierProcessIdentity,
    ],
) -> None:
    owner = os.getpid()
    while True:
        reaped = False
        for identity in _owned_release_verifier_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
            include_zombies=True,
        ):
            if (
                identity.key == root_identity.key
                or identity.parent_pid != owner
                or identity.state != "Z"
            ):
                continue
            try:
                waited, _status = os.waitpid(identity.pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                continue
            except OSError as exc:
                raise CutoverContractError(
                    "release-bound phase verifier zombie could not be reaped"
                ) from exc
            if waited not in {0, identity.pid}:
                raise CutoverContractError(
                    "release-bound phase verifier reaped an unexpected PID"
                )
            reaped |= waited == identity.pid
        if not reaped:
            return


def _raise_release_verifier_cleanup_error(
    label: str,
    error: BaseException,
) -> None:
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        raise error
    if isinstance(error, CutoverContractError):
        raise error
    raise CutoverContractError(
        f"release-bound phase verifier {label} cleanup failed"
    ) from error


def _note_release_verifier_cleanup_error(
    original_error: BaseException,
    label: str,
    error: BaseException,
) -> None:
    original_error.add_note(
        "additional release-bound phase verifier cleanup failure "
        f"during {label}: {type(error).__name__}"
    )


def _signal_release_verifier_handle(
    descriptor: int,
    signum: int,
) -> None:
    try:
        signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise CutoverContractError(
            "release-bound phase verifier identity signal failed"
        ) from exc


def _signal_release_verifier_identity(
    identity: ReleaseVerifierProcessIdentity,
    signum: int,
) -> None:
    current = _release_verifier_process_identity(identity.pid)
    if current is None or current.start_time != identity.start_time:
        return
    try:
        descriptor = os.pidfd_open(identity.pid, 0)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise CutoverContractError(
            "release-bound phase verifier identity handle cannot be opened"
        ) from exc
    try:
        refreshed = _release_verifier_process_identity(identity.pid)
        if refreshed is None or refreshed.start_time != identity.start_time:
            return
        _signal_release_verifier_handle(descriptor, signum)
    finally:
        original_error = sys.exception()
        try:
            os.close(descriptor)
        except BaseException as exc:
            if original_error is not None:
                _note_release_verifier_cleanup_error(
                    original_error,
                    "temporary identity handle",
                    exc,
                )
            else:
                _raise_release_verifier_cleanup_error(
                    "temporary identity handle",
                    exc,
                )


def _signal_owned_release_verifier_process(
    identity: ReleaseVerifierProcessIdentity,
    signum: int,
    *,
    root_identity: ReleaseVerifierProcessIdentity,
    root_descriptor: int | None,
) -> None:
    if identity.key == root_identity.key and root_descriptor is not None:
        _signal_release_verifier_handle(root_descriptor, signum)
        return
    _signal_release_verifier_identity(identity, signum)


def _terminate_release_verifier_tree(
    process: subprocess.Popen[bytes],
    *,
    root_identity: ReleaseVerifierProcessIdentity,
    root_descriptor: int | None,
    baseline_children: frozenset[tuple[int, int]],
    tracked: dict[
        tuple[int, int],
        ReleaseVerifierProcessIdentity,
    ],
) -> bool:
    descendant_observed = any(
        key != root_identity.key for key in tracked
    )

    def refresh(
        *,
        include_zombies: bool = False,
    ) -> tuple[ReleaseVerifierProcessIdentity, ...]:
        nonlocal descendant_observed
        current = _owned_release_verifier_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
            include_zombies=include_zombies,
        )
        descendant_observed = descendant_observed or any(
            identity.key != root_identity.key for identity in current
        )
        return current

    def signal_live(signum: int) -> None:
        current = refresh()
        for identity in sorted(
            current,
            key=lambda item: item.pid,
            reverse=True,
        ):
            _signal_owned_release_verifier_process(
                identity,
                signum,
                root_identity=root_identity,
                root_descriptor=root_descriptor,
            )
        if (
            root_descriptor is not None
            and process.poll() is None
            and not any(
                identity.key == root_identity.key
                for identity in current
            )
        ):
            _signal_release_verifier_handle(root_descriptor, signum)

    signal_live(signal.SIGTERM)
    deadline = time.monotonic() + RELEASE_VERIFIER_TERM_SECONDS
    while time.monotonic() < deadline:
        process.poll()
        _reap_release_verifier_zombies(
            root_identity=root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
        current = refresh()
        if process.poll() is not None and not any(
            _release_verifier_identity_is_live(identity)
            for identity in current
        ):
            break
        signal_live(signal.SIGTERM)
        time.sleep(
            min(
                RELEASE_VERIFIER_POLL_SECONDS,
                max(0.0, deadline - time.monotonic()),
            )
        )
    signal_live(signal.SIGKILL)
    try:
        process.wait(timeout=RELEASE_VERIFIER_TERM_SECONDS)
    except subprocess.TimeoutExpired:
        signal_live(signal.SIGKILL)
        try:
            process.wait(timeout=RELEASE_VERIFIER_TERM_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise CutoverContractError(
                "release-bound phase verifier root survived cleanup"
            ) from exc

    absence_deadline = (
        time.monotonic()
        + RELEASE_VERIFIER_TERM_SECONDS
        + RELEASE_VERIFIER_TREE_QUIESCENCE_SECONDS
    )
    stable_since: float | None = None
    while time.monotonic() < absence_deadline:
        _reap_release_verifier_zombies(
            root_identity=root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
        residue = refresh(include_zombies=True)
        if residue:
            stable_since = None
            for identity in sorted(
                residue,
                key=lambda item: item.pid,
                reverse=True,
            ):
                if _release_verifier_identity_is_live(identity):
                    _signal_owned_release_verifier_process(
                        identity,
                        signal.SIGKILL,
                        root_identity=root_identity,
                        root_descriptor=root_descriptor,
                    )
        elif stable_since is None:
            stable_since = time.monotonic()
        elif (
            time.monotonic() - stable_since
            >= RELEASE_VERIFIER_TREE_QUIESCENCE_SECONDS
        ):
            return descendant_observed
        time.sleep(
            min(
                RELEASE_VERIFIER_POLL_SECONDS,
                max(0.0, absence_deadline - time.monotonic()),
            )
        )
    _reap_release_verifier_zombies(
        root_identity=root_identity,
        baseline_children=baseline_children,
        tracked=tracked,
    )
    if refresh(include_zombies=True):
        raise CutoverContractError(
            "release-bound phase verifier subprocess tree survived cleanup"
        )
    return descendant_observed


def _terminate_unidentified_release_verifier_root(
    process: subprocess.Popen[bytes],
    *,
    root_descriptor: int | None,
    baseline_children: frozenset[tuple[int, int]],
    tracked: dict[
        tuple[int, int],
        ReleaseVerifierProcessIdentity,
    ],
) -> None:
    descriptor = root_descriptor
    close_descriptor = False
    if descriptor is None:
        try:
            descriptor = os.pidfd_open(process.pid, 0)
            close_descriptor = True
        except ProcessLookupError:
            descriptor = None
        except OSError as exc:
            raise CutoverContractError(
                "release-bound phase verifier root handle cannot be "
                "reacquired for cleanup"
            ) from exc
    try:
        if descriptor is not None:
            _signal_release_verifier_handle(descriptor, signal.SIGKILL)
        try:
            process.wait(timeout=RELEASE_VERIFIER_TERM_SECONDS)
        except subprocess.TimeoutExpired:
            if descriptor is not None:
                _signal_release_verifier_handle(
                    descriptor,
                    signal.SIGKILL,
                )
            try:
                process.wait(timeout=RELEASE_VERIFIER_TERM_SECONDS)
            except subprocess.TimeoutExpired as second_exc:
                raise CutoverContractError(
                    "release-bound phase verifier unidentified root "
                    "survived cleanup"
                ) from second_exc
        placeholder = ReleaseVerifierProcessIdentity(
            pid=process.pid,
            parent_pid=os.getpid(),
            process_group=process.pid,
            session_id=process.pid,
            start_time=-1,
            state="?",
        )
        _terminate_release_verifier_tree(
            process,
            root_identity=placeholder,
            root_descriptor=descriptor,
            baseline_children=baseline_children,
            tracked=tracked,
        )
    finally:
        if close_descriptor and descriptor is not None:
            original_error = sys.exception()
            try:
                os.close(descriptor)
            except BaseException as exc:
                if original_error is not None:
                    _note_release_verifier_cleanup_error(
                        original_error,
                        "reacquired root identity handle",
                        exc,
                    )
                else:
                    _raise_release_verifier_cleanup_error(
                        "reacquired root identity handle",
                        exc,
                    )


def _run_bounded_release_verifier_locked(
    arguments: Sequence[str],
    *,
    timeout: float,
    max_stream_bytes: int,
) -> subprocess.CompletedProcess[bytes]:
    _enable_release_verifier_subreaper()
    baseline_children = _release_verifier_child_baseline()
    process: subprocess.Popen[bytes] | None = None
    root_identity: ReleaseVerifierProcessIdentity | None = None
    root_descriptor: int | None = None
    tracked: dict[
        tuple[int, int],
        ReleaseVerifierProcessIdentity,
    ] = {}
    selector: selectors.BaseSelector | None = None
    streams: dict[str, Any] = {}
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    tree_cleaned = False
    descendant_observed = False
    try:
        process = subprocess.Popen(  # noqa: S603
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME": "/nonexistent",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            close_fds=True,
            shell=False,
            start_new_session=True,
        )
        root_descriptor = os.pidfd_open(process.pid, 0)
        root_identity = _read_release_verifier_process_identity(process.pid)
        tracked[root_identity.key] = root_identity
        if process.stdout is None or process.stderr is None:
            raise CutoverContractError(
                "release-bound phase verifier lacks bounded pipes"
            )
        streams = {
            "stdout": process.stdout,
            "stderr": process.stderr,
        }
        selector = selectors.DefaultSelector()
        for label, stream in streams.items():
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            current = _owned_release_verifier_processes(
                root_identity,
                baseline_children=baseline_children,
                tracked=tracked,
            )
            descendant_observed = descendant_observed or any(
                identity.key != root_identity.key for identity in current
            )
            _reap_release_verifier_zombies(
                root_identity=root_identity,
                baseline_children=baseline_children,
                tracked=tracked,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CutoverContractError(
                    "release-bound phase verifier timed out"
                )
            events = selector.select(
                min(RELEASE_VERIFIER_POLL_SECONDS, remaining)
            )
            if not events:
                if process.poll() is not None and not tree_cleaned:
                    descendant_observed |= (
                        _terminate_release_verifier_tree(
                            process,
                            root_identity=root_identity,
                            root_descriptor=root_descriptor,
                            baseline_children=baseline_children,
                            tracked=tracked,
                        )
                    )
                    tree_cleaned = True
                continue
            for key, _mask in events:
                stream = key.fileobj
                label = key.data
                buffer = buffers[label]
                try:
                    chunk = os.read(
                        stream.fileno(),
                        min(
                            65536,
                            max_stream_bytes + 1 - len(buffer),
                        ),
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                buffer.extend(chunk)
                if len(buffer) > max_stream_bytes:
                    raise CutoverContractError(
                        "release-bound phase verifier output is oversized"
                    )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CutoverContractError(
                "release-bound phase verifier timed out"
            )
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise CutoverContractError(
                "release-bound phase verifier timed out"
            ) from exc
        if not tree_cleaned:
            descendant_observed |= _terminate_release_verifier_tree(
                process,
                root_identity=root_identity,
                root_descriptor=root_descriptor,
                baseline_children=baseline_children,
                tracked=tracked,
            )
            tree_cleaned = True
        if descendant_observed:
            raise CutoverContractError(
                "release-bound phase verifier left a descendant process"
            )
        return subprocess.CompletedProcess(
            list(arguments),
            returncode,
            bytes(buffers["stdout"]),
            bytes(buffers["stderr"]),
        )
    except CutoverContractError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise CutoverContractError(
            "release-bound phase verifier could not complete"
        ) from exc
    finally:
        original_error = sys.exception()
        cleanup_errors: list[tuple[str, BaseException]] = []
        if process is not None and not tree_cleaned:
            for attempt in range(2):
                try:
                    if root_identity is None:
                        _terminate_unidentified_release_verifier_root(
                            process,
                            root_descriptor=root_descriptor,
                            baseline_children=baseline_children,
                            tracked=tracked,
                        )
                    else:
                        _terminate_release_verifier_tree(
                            process,
                            root_identity=root_identity,
                            root_descriptor=root_descriptor,
                            baseline_children=baseline_children,
                            tracked=tracked,
                        )
                    tree_cleaned = True
                    break
                except BaseException as exc:
                    cleanup_errors.append(
                        (f"subprocess tree attempt {attempt + 1}", exc)
                    )
        if selector is not None:
            try:
                selector.close()
            except BaseException as exc:
                cleanup_errors.append(("selector", exc))
        cleanup_streams = dict(streams)
        if process is not None:
            for label in ("stdout", "stderr"):
                stream = getattr(process, label, None)
                if stream is not None:
                    cleanup_streams.setdefault(label, stream)
        for label, stream in cleanup_streams.items():
            try:
                stream.close()
            except BaseException as exc:
                cleanup_errors.append((f"{label} stream", exc))
        if root_descriptor is not None:
            try:
                os.close(root_descriptor)
            except BaseException as exc:
                cleanup_errors.append(("root identity handle", exc))
        if cleanup_errors:
            if original_error is not None:
                for label, error in cleanup_errors:
                    _note_release_verifier_cleanup_error(
                        original_error,
                        label,
                        error,
                    )
            else:
                label, error = cleanup_errors[0]
                _raise_release_verifier_cleanup_error(label, error)


def _run_bounded_release_verifier(
    arguments: Sequence[str],
    *,
    timeout: float = RELEASE_VERIFIER_TIMEOUT_SECONDS,
    max_stream_bytes: int = MAX_RELEASE_VERIFIER_STREAM_BYTES,
) -> subprocess.CompletedProcess[bytes]:
    if (
        not arguments
        or any(
            not isinstance(argument, str) or not argument
            for argument in arguments
        )
        or type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or timeout <= 0
        or type(max_stream_bytes) is not int
        or max_stream_bytes < 1
    ):
        raise CutoverContractError(
            "release-bound phase verifier limits are invalid"
        )
    with _RELEASE_VERIFIER_RUN_LOCK:
        return _run_bounded_release_verifier_locked(
            arguments,
            timeout=float(timeout),
            max_stream_bytes=max_stream_bytes,
        )


def _release_verifier_arguments(
    *,
    verifier_path: Path,
    phase: str,
    evidence_path: Path,
    manifest_path: Path,
    approval_path: Path,
    approval_policy_path: Path,
    role_validation: Sequence[str],
    claim_source: Sequence[str],
    prior_phase_evidence: Sequence[str],
) -> list[str]:
    argv = [
        PYTHON,
        "-I",
        "-B",
        str(verifier_path),
        "--evidence",
        str(evidence_path),
        "--manifest",
        str(manifest_path),
        "--approval",
        str(approval_path),
        "--approval-policy",
        str(approval_policy_path),
        "--expected-phase",
        phase,
    ]
    for flag, values, label in (
        ("--role-validation", role_validation, "role validation"),
        ("--claim-source", claim_source, "claim source"),
        (
            "--prior-phase-evidence",
            prior_phase_evidence,
            "prior phase evidence",
        ),
    ):
        for value in _absolute_mapping_args(list(values), label=label):
            argv.extend((flag, value))
    return argv


def _run_release_phase_verifier(
    *,
    phase: str,
    manifest: dict[str, Any],
    manifest_sha256: str,
    plan: dict[str, Any],
    manifest_path: Path,
    approval_path: Path,
    approval_policy_path: Path,
    evidence_path: Path,
    role_validation: list[str],
    claim_source: list[str],
    prior_phase_evidence: list[str],
) -> tuple[VerifiedPhaseCompletion, bytes]:
    for path, label in (
        (manifest_path, "manifest"),
        (approval_path, "approval"),
        (approval_policy_path, "approval policy"),
        (evidence_path, "evidence"),
    ):
        if not path.is_absolute() or ".." in path.parts:
            raise CutoverContractError(
                f"phase verification {label} must be an absolute path"
            )
    verifier_path = (
        Path(
            _operation_release_root(
                manifest["operation_id"],
                manifest["release_sha"],
            )
        )
        / PHASE_EVIDENCE_VERIFIER_RELATIVE_PATH
    )
    expected_verifier_sha256 = manifest["artifacts"][
        "phase_evidence_verifier_sha256"
    ]
    if _hash_release_verifier(verifier_path) != expected_verifier_sha256:
        raise CutoverContractError(
            "release-bound phase verifier differs from the manifest"
        )
    argv = _release_verifier_arguments(
        verifier_path=verifier_path,
        phase=phase,
        evidence_path=evidence_path,
        manifest_path=manifest_path,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
        role_validation=role_validation,
        claim_source=claim_source,
        prior_phase_evidence=prior_phase_evidence,
    )
    completed = _run_bounded_release_verifier(argv)
    if (
        completed.returncode != 0
        or completed.stderr
        or not completed.stdout
        or len(completed.stdout) > 64 * 1024
        or completed.stdout.count(b"\n") > 1
    ):
        raise CutoverContractError(
            "release-bound phase verifier rejected the phase evidence"
        )
    try:
        result = json.loads(
            completed.stdout.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CutoverContractError(
            "release-bound phase verifier returned invalid strict JSON"
        ) from exc
    if _hash_release_verifier(verifier_path) != expected_verifier_sha256:
        raise CutoverContractError(
            "release-bound phase verifier changed during verification"
        )
    return _validate_phase_verification_result(
        result,
        phase=phase,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan["plan_sha256"],
    )


def _persist_phase_verification_receipt(
    *,
    token: VerifiedPhaseCompletion,
    receipt: bytes,
    evidence_root: Path,
) -> Path:
    if hashlib.sha256(receipt).hexdigest() != token.receipt_sha256:
        raise CutoverContractError("phase verification receipt digest is invalid")
    path = (
        evidence_root
        / "verification"
        / f"{token.phase}.{token.receipt_sha256}.json"
    )
    try:
        write_secure_new_bytes(
            path,
            receipt,
            label="production phase verification receipt",
            mode=0o600,
            max_size=64 * 1024,
        )
    except SecureFileError:
        try:
            existing = read_secure_bytes(
                path,
                label="production phase verification receipt",
                owner_uid=0,
                max_size=64 * 1024,
            )
        except SecureFileError as exc:
            raise CutoverContractError(
                "phase verification receipt could not be persisted"
            ) from exc
        if existing != receipt:
            raise CutoverContractError(
                "existing phase verification receipt differs"
            )
    return path


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
        "phase_verification_sha256",
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
}
EVENT_FIELDS = frozenset(
    {
        "sequence",
        "kind",
        "phase",
        "evidence_sha256",
        "verification_sha256",
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
    verification_sha256: str | None = None,
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
        "verification_sha256": verification_sha256,
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
    replay_verification: dict[str, str] = {}
    for expected_sequence, raw in enumerate(events, 1):
        event = _require_exact_fields(raw, EVENT_FIELDS, label="cutover journal event")
        kind = event["kind"]
        phase = event["phase"]
        evidence = event["evidence_sha256"]
        verification = event["verification_sha256"]
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
                or verification is not None
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
                or verification is not None
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
                or SHA256_RE.fullmatch(str(verification)) is None
                or evidence == ZERO_SHA256
                or verification == ZERO_SHA256
                or reason is not None
            ):
                raise CutoverContractError("cutover journal phase-completion history is invalid")
            replay_completed.append(str(phase))
            replay_evidence[str(phase)] = str(evidence)
            replay_verification[str(phase)] = str(verification)
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
                or verification is not None
                or not isinstance(reason, str)
                or not reason
            ):
                raise CutoverContractError("cutover journal rollback history is invalid")
            replay_started = None
            replay_status = "rolled_back"
        else:
            raise CutoverContractError("cutover journal creation event may only occur once")
        previous = event["event_hash"]
    if (
        journal["event_tail_sha256"] != previous
        or journal["completed_phases"] != replay_completed
        or journal["phase_evidence_sha256"] != replay_evidence
        or journal["phase_verification_sha256"] != replay_verification
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
    verification = journal["phase_verification_sha256"]
    if (
        not isinstance(completed, list)
        or completed != list(PHASES[: len(completed)])
        or len(completed) != len(set(completed))
        or not isinstance(evidence, dict)
        or set(evidence) != set(completed)
        or not isinstance(verification, dict)
        or set(verification) != set(completed)
        or any(
            SHA256_RE.fullmatch(str(value)) is None or value == ZERO_SHA256
            for value in evidence.values()
        )
        or any(
            SHA256_RE.fullmatch(str(value)) is None or value == ZERO_SHA256
            for value in verification.values()
        )
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
    if journal["first_business_write_allowed"] is not False:
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
    if journal["commit_evidence_sha256"] is not None or journal["committed_at"] is not None:
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
                "phase_verification_sha256": {},
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
            if payload["status"] in {"rolled_back", "ready_for_commit"}:
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

    def complete_phase(
        self,
        phase: str,
        *,
        verification: VerifiedPhaseCompletion,
    ) -> dict[str, Any]:
        if (
            phase not in PHASES
            or not isinstance(verification, VerifiedPhaseCompletion)
            or verification.phase != phase
            or SHA256_RE.fullmatch(verification.evidence_sha256) is None
            or SHA256_RE.fullmatch(verification.receipt_sha256) is None
            or verification.evidence_sha256 == ZERO_SHA256
            or verification.receipt_sha256 == ZERO_SHA256
        ):
            raise CutoverContractError(
                "cutover phase lacks a valid release-verifier completion"
            )
        descriptor = self._lock()
        try:
            payload = self._read()
            if phase in payload["completed_phases"]:
                if (
                    payload["phase_evidence_sha256"][phase]
                    != verification.evidence_sha256
                    or payload["phase_verification_sha256"][phase]
                    != verification.receipt_sha256
                ):
                    raise CutoverContractError("idempotent phase evidence differs from the journal")
                return payload
            if payload["status"] != "phase_started" or payload["started_phase"] != phase:
                raise CutoverContractError("cutover phase completion has no matching durable start")
            payload["completed_phases"].append(phase)
            payload["phase_evidence_sha256"][phase] = verification.evidence_sha256
            payload["phase_verification_sha256"][phase] = (
                verification.receipt_sha256
            )
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
                evidence_sha256=verification.evidence_sha256,
                verification_sha256=verification.receipt_sha256,
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
        del evidence_sha256, confirmation
        raise CutoverContractError(
            "irreversible first-business-write commit is hard-disabled until "
            "the postcommit executor and bounded operation workers exist"
        )


MUTATING_ACTIONS = {
    "create-journal",
    "begin-phase",
    "complete-phase",
    "record-rollback",
    "commit-first-business-write",
}

AUTHORIZATION_REQUIRED_ACTIONS = {
    "create-journal",
    "begin-phase",
    "complete-phase",
    "commit-first-business-write",
}


def _verify_runtime_authorization(
    manifest: dict[str, Any],
    *,
    approval_path: Path,
    approval_policy_path: Path,
) -> None:
    try:
        approval_bytes = read_secure_bytes(
            approval_path,
            label="production cutover approval",
            owner_uid=0,
            max_size=16 * 1024 * 1024,
        )
        policy_bytes = read_secure_bytes(
            approval_policy_path,
            label="production human approval policy",
            owner_uid=0,
            max_size=4 * 1024 * 1024,
        )
        verify_authorization_documents(
            manifest,
            approval_bytes=approval_bytes,
            policy_bytes=policy_bytes,
            require_fresh=True,
        )
    except (SecureFileError, ProductionShadowAuthorizationError) as exc:
        raise CutoverContractError(
            "production cutover authorization is invalid or expired"
        ) from exc


def _planned_transition(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": (
            "blocked"
            if args.action == "commit-first-business-write"
            else "planned"
        ),
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
        "irreversible_commit_enabled": IRREVERSIBLE_COMMIT_ENABLED,
    }


def _require_action_arguments(args: argparse.Namespace) -> None:
    if args.action in {"begin-phase", "complete-phase"} and args.phase not in PHASES:
        raise CutoverContractError("--phase must name the exact next cutover phase")
    if args.action == "complete-phase":
        if args.evidence_sha256 is not None:
            raise CutoverContractError(
                "complete-phase never accepts a caller-supplied evidence SHA-256"
            )
        if (
            args.evidence is None
            or args.approval is None
            or args.approval_policy is None
        ):
            raise CutoverContractError(
                "complete-phase requires --evidence, --approval and "
                "--approval-policy for the release-bound verifier"
            )
    if args.apply and args.action in AUTHORIZATION_REQUIRED_ACTIONS and (
        args.approval is None or args.approval_policy is None
    ):
        raise CutoverContractError(
            f"{args.action} requires --approval and --approval-policy"
        )
    if args.action in {
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
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--approval-policy", type=Path)
    parser.add_argument("--role-validation", action="append", default=[])
    parser.add_argument("--claim-source", action="append", default=[])
    parser.add_argument("--prior-phase-evidence", action="append", default=[])
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
            if args.action in AUTHORIZATION_REQUIRED_ACTIONS:
                _verify_runtime_authorization(
                    manifest,
                    approval_path=Path(args.approval),
                    approval_policy_path=Path(args.approval_policy),
                )
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
                    verification, receipt = _run_release_phase_verifier(
                        phase=str(args.phase),
                        manifest=manifest,
                        manifest_sha256=manifest_sha256,
                        plan=plan,
                        manifest_path=args.manifest,
                        approval_path=Path(args.approval),
                        approval_policy_path=Path(args.approval_policy),
                        evidence_path=Path(args.evidence),
                        role_validation=list(args.role_validation),
                        claim_source=list(args.claim_source),
                        prior_phase_evidence=list(args.prior_phase_evidence),
                    )
                    _persist_phase_verification_receipt(
                        token=verification,
                        receipt=receipt,
                        evidence_root=Path(
                            manifest["deployment"]["controller_evidence_root"]
                        ),
                    )
                    state = journal.complete_phase(
                        str(args.phase),
                        verification=verification,
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
        return 2 if payload.get("status") == "blocked" else 0
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
