#!/usr/bin/env python3
"""Produce and publish the three-role startup-normalization closure.

The source producer loads the canonical historical running receipt, runs the
three exact-release workers concurrently under live controller authority, and
delegates the final stopped observation to the prepared-inventory collector.
The apply path consumes only its root-only digest-addressed source record,
derives the public zero claims from inventory evidence, verifies the release
contract, and advances the cutover journal.
"""

from __future__ import annotations

import argparse
from concurrent.futures import (
    FIRST_EXCEPTION,
    Future,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Protocol, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from scripts import orchestrate_production_shadow_prepared_clone_inventory as PREPARED  # noqa: E402
from scripts import orchestrate_production_shadow_frozen_prepare as PROCESS  # noqa: E402
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import production_shadow_global_docker_inventory_agent as INVENTORY  # noqa: E402
from scripts import production_shadow_startup_normalization_worker as WORKER  # noqa: E402
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402


PHASE = "shadow_startup_normalization"
OPERATION = "normalize-operation-owned-shadow-startup-state"
ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
CHECKPOINTS = (
    "before-initial-state",
    "before-first-startup-normalization",
    "before-first-state",
    "before-second-startup-normalization",
    "before-second-state",
    "before-database-stop",
)

PLAN_SCHEMA = "production-shadow-startup-normalization-phase-plan-v1"
SOURCE_PRODUCTION_PLAN_SCHEMA = (
    "production-shadow-startup-normalization-source-production-plan-v1"
)
SOURCE_PRODUCTION_RESULT_SCHEMA = (
    "production-shadow-startup-normalization-source-production-result-v1"
)
SOURCE_SPEC_RECORD_SCHEMA = (
    "production-shadow-startup-normalization-persisted-source-spec-v1"
)
PHASE_REQUEST_SCHEMA = (
    "production-shadow-startup-normalization-phase-request-v1"
)
PHASE_REQUEST_MODE = "persisted-source-plan-apply"
REFERENCE_FIELDS = frozenset({"path", "sha256"})
PHASE_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "mode",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_path",
        "manifest_sha256",
        "approval_path",
        "approval_sha256",
        "approval_policy_path",
        "approval_policy_sha256",
        "controller_plan_sha256",
        "prior_phase_evidence",
        "source_spec_record",
        "source_binding_sha256",
        "constraints",
    }
)
EXPECTED_PHASE_REQUEST_CONSTRAINTS = {
    "persisted_sources_only": True,
    "digest_addressed_request_required": True,
    "caller_truth_values_forbidden": True,
    "production_contact_forbidden": True,
    "runtime_authorization_required_for_apply": True,
    "controller_liveness_required_for_apply": True,
}
SOURCE_SPEC_RECORD_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "controller_plan_sha256",
        "source_binding_sha256",
        "source_spec",
        "parallel_worker_count",
        "worker_completion_skew_limit_seconds",
        "fresh_stopped_inventory",
        "journal_mutated",
        "production_contacted",
    }
)
CLOSURE_SCHEMA = (
    "production-shadow-startup-normalization-three-role-closure-v1"
)
ROLE_CLOSURE_SCHEMA = (
    "production-shadow-startup-normalization-role-closure-v1"
)
CLAIM_SOURCE_SCHEMA = "production-shadow-phase-claim-source-v1"
ROLE_VALIDATION_SCHEMA = "production-shadow-host-agent-validation-v1"
PUBLICATION_SCHEMA = (
    "production-shadow-startup-normalization-phase-publication-v1"
)
RESULT_SCHEMA = "production-shadow-startup-normalization-phase-result-v1"

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_WORKER_CAPTURE_SKEW_SECONDS = 30.0
CROSS_CLOCK_SKEW = timedelta(
    seconds=PREPARED.COMMAND_CLOCK_SKEW_SECONDS
)
WORKER_REQUEST_LIFETIME = timedelta(minutes=20)
WORKER_SESSION_TIMEOUT_SECONDS = 15 * 60.0
WORKER_STREAM_LIMIT_BYTES = WORKER.MAX_RESPONSE_BYTES + 1
WORKER_STDERR_LIMIT_BYTES = 64 * 1024
WORKER_POLL_SECONDS = 0.1
WORKER_TERM_GRACE_SECONDS = 3.0
WORKER_KILL_GRACE_SECONDS = 3.0
OUTPUT_DIRECTORY_MODE = 0o700
OUTPUT_FILE_MODE = 0o600
ZERO_SHA256 = "0" * 64
OUTPUT_SUBDIRECTORY = "startup-normalization-phase-bridge"

CLAIMS = (
    "legacy_resource_delta_count",
    "operation_owned_running_container_count",
    "unplanned_container_delta_count",
)
RESOURCE_BINDING_FIELDS = (
    "prepared_container_id",
    "prepared_network_id",
    "prepared_container_identity_sha256",
    "prepared_container_metadata_sha256",
    "prepared_network_identity_sha256",
    "prepared_network_metadata_sha256",
    "prepared_config_sha256",
    "prepared_environment_sha256",
    "prepared_environment_entry_count",
    "prepared_compose_config_sha256",
    "prepared_host_config_sha256",
    "prepared_mounts_sha256",
    "prepared_network_attachment_sha256",
)
NON_OPERATION_FIELDS = (
    "non_operation_inventory_root_sha256",
    "non_operation_identity_root_sha256",
    "non_operation_state_root_sha256",
    "non_operation_metadata_root_sha256",
    "non_operation_resource_counts",
)
OPTIONAL_REDIS_BINDING_FIELDS = (
    "prepared_redis_identity_sha256",
    "prepared_redis_chain_metadata_sha256",
    "prepared_redis_metadata_sha256",
    "prepared_redis_target_count",
    "prepared_redis_unsafe_path_count",
    "prepared_redis_entry_count",
    "prepared_redis_pristine",
)

CLOSURE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "running_aggregate_sha256",
        "stopped_aggregate_sha256",
        "running_controller_challenge_sha256",
        "stopped_controller_challenge_sha256",
        "normalization_controller_challenge_sha256_by_role",
        "roles",
        "claims",
        "claim_derivation",
        "running_inventory_historically_validated",
        "stopped_inventory_freshly_validated",
        "worker_results_validated",
        "caller_claims_accepted",
        "legacy_resource_mutated",
        "current_mutated",
        "persistent_resource_removed",
        "object_storage_used",
        "captured_at",
        "closure_sha256",
    }
)
ROLE_CLOSURE_FIELDS = frozenset(
    {
        "schema",
        "role",
        "expected_host",
        "running_request_sha256",
        "running_response_sha256",
        "normalization_request_sha256",
        "normalization_result_sha256",
        "stopped_request_sha256",
        "stopped_response_sha256",
        "prepared_container_id",
        "prepared_network_id",
        "non_operation_inventory_root_sha256",
        "operation_resource_counts",
        "prepared_database_running",
        "prepared_database_healthy",
        "resource_binding_sha256",
        "chronology_sha256",
        "captured_at",
    }
)
PUBLICATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "phase",
        "operation",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "plan_sha256",
        "closure_path",
        "closure_file_sha256",
        "role_source_paths",
        "role_source_sha256",
        "role_validation_paths",
        "role_validation_sha256",
        "claim_source_paths",
        "claim_source_sha256",
        "phase_evidence_path",
        "phase_evidence_sha256",
        "local_verification_path",
        "local_verification_sha256",
        "journal_status",
        "journal_mutated",
        "production_contacted",
        "caller_truth_values_accepted",
        "create_only",
        "readback_verified",
    }
)


class StartupNormalizationPhaseError(RuntimeError):
    """The public phase bridge could not prove an exact stopped closure."""


@dataclass(frozen=True)
class ClosureInputs:
    running_aggregate: Mapping[str, Any]
    running_requests: Mapping[str, Mapping[str, Any]]
    running_responses: Mapping[str, Mapping[str, Any]]
    normalization_requests: Mapping[str, Mapping[str, Any]]
    normalization_results: Mapping[str, Mapping[str, Any]]
    stopped_aggregate: Mapping[str, Any]
    stopped_requests: Mapping[str, Mapping[str, Any]]
    stopped_responses: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class EvidenceContext:
    """Already-loaded controller records for local evidence publication."""

    manifest_path: Path
    approval_path: Path
    approval_policy_path: Path
    journal_path: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    plan: Mapping[str, Any]
    plan_sha256: str
    journal: Mapping[str, Any]
    prior_records: Mapping[str, Mapping[str, Any]]
    prior_digests: Mapping[str, str]
    prior_paths: Mapping[str, Path]
    output_root: Path


class PreparedSourceLoader(Protocol):
    """Load persisted, read-back-verified sources without contacting a host."""

    def load(self) -> ClosureInputs:
        """Return the exact running/worker/stopped source set."""


@dataclass(frozen=True)
class PersistedClosureSourceSpec:
    running_receipt_path: Path
    running_controller_challenge_sha256: str
    running_aggregate_artifact_sha256: str
    stopped_receipt_path: Path
    stopped_controller_challenge_sha256: str
    stopped_aggregate_artifact_sha256: str
    normalization_request_paths: Mapping[str, Path]
    normalization_request_artifact_sha256: Mapping[str, str]
    normalization_result_paths: Mapping[str, Path]
    normalization_result_artifact_sha256: Mapping[str, str]


@dataclass(frozen=True)
class RunningBaselineSpec:
    receipt_path: Path
    controller_challenge_sha256: str
    aggregate_artifact_sha256: str


class ProductionWorkerInvoker:
    """Bounded interactive local/SSH invoker for one normalization worker."""

    def __init__(
        self,
        *,
        ssh_identity: Path,
        ssh_identity_sha256: str,
        known_hosts: Path,
        known_hosts_sha256: str,
        session_factory: Any = subprocess.Popen,
    ):
        self.trust = PREPARED.ProductionInvoker(
            ssh_identity=ssh_identity,
            ssh_identity_sha256=ssh_identity_sha256,
            known_hosts=known_hosts,
            known_hosts_sha256=known_hosts_sha256,
        )
        if not callable(session_factory):
            raise StartupNormalizationPhaseError(
                "normalization session factory is unavailable"
            )
        self.session_factory = session_factory
        self._ownership_lock = threading.RLock()
        self._active_session_count = 0
        self._next_session_token = 1
        self._active_sessions: dict[
            int,
            PROCESS.ProcessIdentity | None,
        ] = {}
        self._active_root_pids: dict[int, int] = {}
        self._active_roots: dict[
            tuple[int, int],
            int,
        ] = {}
        self._identity_owners: dict[tuple[int, int], int] = {}
        self._quarantined_identities: set[
            PROCESS.ProcessIdentity
        ] = set()
        self._ownership_abort = threading.Event()
        self._direct_child_baseline: frozenset[
            tuple[int, int]
        ] = frozenset()

    def _argv(
        self,
        role: str,
        request: Mapping[str, Any],
    ) -> tuple[str, ...]:
        host = (
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            "HOME=/root",
            "LANG=C.UTF-8",
            "LC_ALL=C.UTF-8",
            "PYTHONDONTWRITEBYTECODE=1",
            "/usr/bin/python3",
            "-I",
            "-B",
            request["worker_path"],
            "--host-stdio",
            "--apply",
            "--confirm",
            WORKER.confirmation_phrase(request),
        )
        if role == "bot_fi":
            return host
        expected_host, port = self.trust._endpoint(role)  # noqa: SLF001
        if request["expected_host"] != expected_host:
            raise StartupNormalizationPhaseError(
                f"{role} normalization endpoint differs"
            )
        self.trust._verify_ssh_trust()  # noqa: SLF001
        remote = " ".join(
            "'" + item.replace("'", "'\"'\"'") + "'"
            for item in host
        )
        return (
            "/usr/bin/ssh",
            "-F",
            "/dev/null",
            "-T",
            "-p",
            str(port),
            "-i",
            os.fspath(self.trust.ssh_identity),
            "-o",
            "BatchMode=yes",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "PermitLocalCommand=no",
            "-o",
            "RequestTTY=no",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.trust.known_hosts}",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            f"root@{request['expected_host']}",
            remote,
        )

    def _refresh_owned(
        self,
        root: PROCESS.ProcessIdentity,
        tracked: set[PROCESS.ProcessIdentity],
        *,
        session_token: int,
    ) -> set[PROCESS.ProcessIdentity]:
        with self._ownership_lock:
            if session_token not in self._active_sessions:
                raise StartupNormalizationPhaseError(
                    "normalization ownership session is unavailable"
                )
            snapshot = PROCESS._process_snapshot()  # noqa: SLF001
            current_root = snapshot.get(root.pid)
            root_is_current = (
                current_root is not None
                and current_root.start_time == root.start_time
            )
            if root_is_current:
                owner = self._identity_owners.setdefault(
                    root.key,
                    session_token,
                )
                if owner != session_token:
                    self._ownership_abort.set()
                    raise StartupNormalizationPhaseError(
                        "normalization root ownership collided"
                    )
            # A process group/session number is only authoritative while an
            # exact start-time-bound member remains.  This prevents a reused
            # PID/PGID from becoming a signal target.
            group_is_anchored = root_is_current or any(
                identity.process_group == root.process_group
                and identity.session_id == root.session_id
                for identity in tracked
                if (
                    (current := snapshot.get(identity.pid)) is not None
                    and current.start_time == identity.start_time
                    and self._identity_owners.get(identity.key)
                    == session_token
                )
            )
            if group_is_anchored:
                for identity in snapshot.values():
                    if (
                        identity.process_group == root.process_group
                        and identity.session_id == root.session_id
                        and identity
                        not in self._quarantined_identities
                    ):
                        owner = self._identity_owners.setdefault(
                            identity.key,
                            session_token,
                        )
                        if owner != session_token:
                            self._ownership_abort.set()
                            raise StartupNormalizationPhaseError(
                                "normalization process group ownership "
                                "collided"
                            )
            self._attribute_adopted_children(snapshot)
            owned_pids = {
                identity.pid
                for identity in snapshot.values()
                if self._identity_owners.get(identity.key)
                == session_token
            }
            changed = True
            while changed:
                changed = False
                for identity in snapshot.values():
                    if (
                        identity.pid not in owned_pids
                        and identity.parent_pid in owned_pids
                        and identity
                        not in self._quarantined_identities
                    ):
                        owner = self._identity_owners.setdefault(
                            identity.key,
                            session_token,
                        )
                        if owner != session_token:
                            self._ownership_abort.set()
                            raise StartupNormalizationPhaseError(
                                "normalization descendant ownership collided"
                            )
                        owned_pids.add(identity.pid)
                        changed = True
            observed = {
                identity
                for identity in snapshot.values()
                if identity.pid in owned_pids
            }
            if len(tracked | observed) > PROCESS.MAX_PROCESS_TREE_MEMBERS:
                raise StartupNormalizationPhaseError(
                    "normalization process tree exceeds its bound"
                )
            tracked.update(observed)
            return {
                identity
                for identity in tracked
                if (
                    self._identity_owners.get(identity.key)
                    == session_token
                    and PROCESS._identity_is_live(identity)  # noqa: SLF001
                )
            }

    def _attribute_adopted_children(
        self,
        snapshot: Mapping[int, PROCESS.ProcessIdentity],
    ) -> None:
        """Attribute adopted direct children or quarantine them globally."""

        active_root_keys = set(self._active_roots)
        active_root_pids = set(self._active_root_pids.values())
        active_roots = {
            token: root
            for token, root in self._active_sessions.items()
            if root is not None
        }
        for identity in snapshot.values():
            if (
                identity.parent_pid != os.getpid()
                or identity.key in self._direct_child_baseline
                or identity.key in active_root_keys
                or identity.pid in active_root_pids
                or identity.key in self._identity_owners
                or identity in self._quarantined_identities
            ):
                continue
            candidates = {
                token
                for token, root in active_roots.items()
                if (
                    identity.process_group == root.process_group
                    and identity.session_id == root.session_id
                )
            }
            if len(candidates) == 1:
                self._identity_owners[identity.key] = candidates.pop()
            elif len(self._active_sessions) == 1:
                self._identity_owners[identity.key] = next(
                    iter(self._active_sessions)
                )
            else:
                self._quarantined_identities.add(identity)
                self._ownership_abort.set()
        if (
            len(self._identity_owners)
            + len(self._quarantined_identities)
            > PROCESS.MAX_PROCESS_TREE_MEMBERS
        ):
            self._ownership_abort.set()
            raise StartupNormalizationPhaseError(
                "normalization global process registry exceeds its bound"
            )

    def _refresh_session_owned(
        self,
        *,
        session_token: int,
        tracked: set[PROCESS.ProcessIdentity],
    ) -> set[PROCESS.ProcessIdentity]:
        with self._ownership_lock:
            if session_token not in self._active_sessions:
                raise StartupNormalizationPhaseError(
                    "normalization ownership session is unavailable"
                )
            snapshot = PROCESS._process_snapshot()  # noqa: SLF001
            self._attribute_adopted_children(snapshot)
            owned_pids = {
                identity.pid
                for identity in snapshot.values()
                if self._identity_owners.get(identity.key)
                == session_token
            }
            changed = True
            while changed:
                changed = False
                for identity in snapshot.values():
                    if (
                        identity.pid not in owned_pids
                        and identity.parent_pid in owned_pids
                        and identity
                        not in self._quarantined_identities
                    ):
                        owner = self._identity_owners.setdefault(
                            identity.key,
                            session_token,
                        )
                        if owner != session_token:
                            self._ownership_abort.set()
                            raise StartupNormalizationPhaseError(
                                "normalization descendant ownership collided"
                            )
                        owned_pids.add(identity.pid)
                        changed = True
            observed = {
                identity
                for identity in snapshot.values()
                if identity.pid in owned_pids
            }
            tracked.update(observed)
            return {
                identity
                for identity in tracked
                if (
                    self._identity_owners.get(identity.key)
                    == session_token
                    and PROCESS._identity_is_live(identity)  # noqa: SLF001
                )
            }

    @staticmethod
    def _signal_identities(
        identities: set[PROCESS.ProcessIdentity],
        signum: int,
        *,
        root_identity: PROCESS.ProcessIdentity | None = None,
        root_descriptor: int | None = None,
    ) -> None:
        for identity in identities:
            try:
                if (
                    root_identity is not None
                    and identity.key == root_identity.key
                    and root_descriptor is not None
                ):
                    PROCESS._signal_process_handle(  # noqa: SLF001
                        root_descriptor,
                        signum,
                    )
                else:
                    PROCESS._signal_identity(  # noqa: SLF001
                        identity,
                        signum,
                    )
            except ProcessLookupError:
                continue

    @staticmethod
    def _reap_adopted(
        identities: set[PROCESS.ProcessIdentity],
    ) -> None:
        for identity in tuple(identities):
            observed = PROCESS._process_identity(  # noqa: SLF001
                identity.pid
            )
            if (
                observed is None
                or observed.start_time != identity.start_time
                or observed.parent_pid != os.getpid()
            ):
                continue
            try:
                os.waitpid(identity.pid, os.WNOHANG)
            except ChildProcessError:
                pass

    def _reconcile_quarantine(self) -> None:
        """Kill only unattributable operation children, never sibling roots."""

        def refresh() -> set[PROCESS.ProcessIdentity]:
            with self._ownership_lock:
                snapshot = PROCESS._process_snapshot()  # noqa: SLF001
                self._attribute_adopted_children(snapshot)
                quarantined_pids = {
                    identity.pid
                    for identity in self._quarantined_identities
                    if (
                        (current := snapshot.get(identity.pid)) is not None
                        and current.start_time == identity.start_time
                    )
                }
                changed = True
                while changed:
                    changed = False
                    for identity in snapshot.values():
                        if (
                            identity.pid not in quarantined_pids
                            and identity.parent_pid in quarantined_pids
                            and identity.key
                            not in self._identity_owners
                            and identity.key not in self._active_roots
                        ):
                            self._quarantined_identities.add(identity)
                            quarantined_pids.add(identity.pid)
                            changed = True
                return {
                    identity
                    for identity in self._quarantined_identities
                    if PROCESS._identity_is_live(identity)  # noqa: SLF001
                }

        self._reap_adopted(set(self._quarantined_identities))
        live = refresh()
        if not live:
            self._reap_adopted(set(self._quarantined_identities))
            with self._ownership_lock:
                self._quarantined_identities = {
                    identity
                    for identity in self._quarantined_identities
                    if PROCESS._identity_is_live(identity)  # noqa: SLF001
                }
            return
        self._signal_identities(live, signal.SIGTERM)
        deadline = time.monotonic() + WORKER_TERM_GRACE_SECONDS
        while time.monotonic() < deadline:
            self._reap_adopted(
                set(self._quarantined_identities)
            )
            live = refresh()
            if not live:
                break
            time.sleep(WORKER_POLL_SECONDS)
        live = refresh()
        self._signal_identities(live, signal.SIGKILL)
        absence_deadline = (
            time.monotonic()
            + WORKER_KILL_GRACE_SECONDS
            + PROCESS.PROCESS_TREE_QUIESCENCE_SECONDS
        )
        stable_since: float | None = None
        while time.monotonic() < absence_deadline:
            self._reap_adopted(
                set(self._quarantined_identities)
            )
            live = refresh()
            if live:
                stable_since = None
                self._signal_identities(live, signal.SIGKILL)
            elif stable_since is None:
                stable_since = time.monotonic()
            elif (
                time.monotonic() - stable_since
                >= PROCESS.PROCESS_TREE_QUIESCENCE_SECONDS
            ):
                with self._ownership_lock:
                    self._quarantined_identities = {
                        identity
                        for identity in self._quarantined_identities
                        if PROCESS._identity_is_live(identity)  # noqa: SLF001
                    }
                return
            time.sleep(WORKER_POLL_SECONDS)
        if refresh():
            raise StartupNormalizationPhaseError(
                "normalization quarantine survived forced cleanup"
            )

    def _end_session(
        self,
        session_token: int,
        root_identity: PROCESS.ProcessIdentity | None,
        *,
        tracked: set[PROCESS.ProcessIdentity] | None = None,
    ) -> None:
        final_tracked = set() if tracked is None else set(tracked)
        if root_identity is not None:
            final_tracked.add(root_identity)
        with self._ownership_lock:
            if session_token not in self._active_sessions:
                raise StartupNormalizationPhaseError(
                    "normalization session registry underflow"
                )
        started = time.monotonic()
        kill_after = started + WORKER_TERM_GRACE_SECONDS
        deadline = (
            kill_after
            + (2 * WORKER_KILL_GRACE_SECONDS)
            + (3 * PROCESS.PROCESS_TREE_QUIESCENCE_SECONDS)
        )
        stable_since: float | None = None
        while True:
            self._reconcile_quarantine()
            live_owned = self._refresh_session_owned(
                session_token=session_token,
                tracked=final_tracked,
            )
            self._reap_adopted(final_tracked)
            live_owned = self._refresh_session_owned(
                session_token=session_token,
                tracked=final_tracked,
            )
            now = time.monotonic()
            if live_owned:
                stable_since = None
                self._signal_identities(
                    live_owned,
                    (
                        signal.SIGTERM
                        if now < kill_after
                        else signal.SIGKILL
                    ),
                )
            elif stable_since is None:
                stable_since = now
            elif (
                now - stable_since
                >= PROCESS.PROCESS_TREE_QUIESCENCE_SECONDS
            ):
                with self._ownership_lock:
                    snapshot = PROCESS._process_snapshot()  # noqa: SLF001
                    self._attribute_adopted_children(snapshot)
                    final_live_owned = {
                        identity
                        for identity in snapshot.values()
                        if (
                            self._identity_owners.get(identity.key)
                            == session_token
                            and PROCESS._identity_is_live(  # noqa: SLF001
                                identity
                            )
                        )
                    }
                    self._quarantined_identities = {
                        identity
                        for identity in self._quarantined_identities
                        if PROCESS._identity_is_live(identity)  # noqa: SLF001
                    }
                    if (
                        not final_live_owned
                        and not self._quarantined_identities
                    ):
                        self._identity_owners = {
                            key: owner
                            for key, owner in self._identity_owners.items()
                            if owner != session_token
                        }
                        if root_identity is not None:
                            self._active_roots.pop(
                                root_identity.key,
                                None,
                            )
                        self._active_sessions.pop(session_token)
                        self._active_root_pids.pop(
                            session_token,
                            None,
                        )
                        self._active_session_count -= 1
                        if self._active_session_count == 0:
                            self._active_roots.clear()
                            self._active_sessions.clear()
                            self._active_root_pids.clear()
                            self._identity_owners.clear()
                            self._direct_child_baseline = frozenset()
                            self._ownership_abort.clear()
                        return
                    final_tracked.update(final_live_owned)
                stable_since = None
            if now >= deadline:
                self._signal_identities(
                    live_owned,
                    signal.SIGKILL,
                )
                self._reap_adopted(final_tracked)
                self._ownership_abort.set()
                raise StartupNormalizationPhaseError(
                    "normalization final session teardown did not quiesce"
                )
            time.sleep(WORKER_POLL_SECONDS)

    def _terminate(
        self,
        process: subprocess.Popen[bytes],
        *,
        root_descriptor: int | None,
        root_identity: PROCESS.ProcessIdentity | None,
        tracked: set[PROCESS.ProcessIdentity],
        session_token: int,
    ) -> None:
        if root_identity is None:
            if root_descriptor is None:
                raise StartupNormalizationPhaseError(
                    "normalization root identity was never retained"
                )
            try:
                PROCESS._signal_process_handle(  # noqa: SLF001
                    root_descriptor,
                    signal.SIGKILL,
                )
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=WORKER_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired as exc:
                raise StartupNormalizationPhaseError(
                    "unidentified normalization root survived cleanup"
                ) from exc
            live = self._refresh_session_owned(
                session_token=session_token,
                tracked=tracked,
            )
            self._signal_identities(live, signal.SIGTERM)
            time.sleep(WORKER_TERM_GRACE_SECONDS)
            live = self._refresh_session_owned(
                session_token=session_token,
                tracked=tracked,
            )
            self._signal_identities(live, signal.SIGKILL)
            self._reap_adopted(tracked)
            self._reconcile_quarantine()
            if self._refresh_session_owned(
                session_token=session_token,
                tracked=tracked,
            ):
                raise StartupNormalizationPhaseError(
                    "unidentified normalization descendants survived cleanup"
                )
            return
        self._reconcile_quarantine()
        live = self._refresh_owned(
            root_identity,
            tracked,
            session_token=session_token,
        )
        self._signal_identities(
            live,
            signal.SIGTERM,
            root_identity=root_identity,
            root_descriptor=root_descriptor,
        )
        deadline = time.monotonic() + WORKER_TERM_GRACE_SECONDS
        while time.monotonic() < deadline:
            process.poll()
            live = self._refresh_owned(
                root_identity,
                tracked,
                session_token=session_token,
            )
            if not live:
                break
            time.sleep(WORKER_POLL_SECONDS)
        live = self._refresh_owned(
            root_identity,
            tracked,
            session_token=session_token,
        )
        self._signal_identities(
            live,
            signal.SIGKILL,
            root_identity=root_identity,
            root_descriptor=root_descriptor,
        )
        try:
            process.wait(timeout=WORKER_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            self._signal_identities(
                {root_identity},
                signal.SIGKILL,
                root_identity=root_identity,
                root_descriptor=root_descriptor,
            )
            raise StartupNormalizationPhaseError(
                "normalization root survived forced cleanup"
            ) from exc
        absence_deadline = (
            time.monotonic()
            + WORKER_KILL_GRACE_SECONDS
            + PROCESS.PROCESS_TREE_QUIESCENCE_SECONDS
        )
        stable_since: float | None = None
        while time.monotonic() < absence_deadline:
            self._reap_adopted(tracked)
            self._reconcile_quarantine()
            live = self._refresh_owned(
                root_identity,
                tracked,
                session_token=session_token,
            )
            if live:
                stable_since = None
                self._signal_identities(
                    live,
                    signal.SIGKILL,
                    root_identity=root_identity,
                    root_descriptor=root_descriptor,
                )
            elif stable_since is None:
                stable_since = time.monotonic()
            elif (
                time.monotonic() - stable_since
                >= PROCESS.PROCESS_TREE_QUIESCENCE_SECONDS
            ):
                self._reconcile_quarantine()
                return
            time.sleep(WORKER_POLL_SECONDS)
        if self._refresh_owned(
            root_identity,
            tracked,
            session_token=session_token,
        ):
            raise StartupNormalizationPhaseError(
                "normalization descendants survived forced cleanup"
            )

    def __call__(
        self,
        role: str,
        request_value: Mapping[str, Any],
        *,
        authority_check: Any,
        cancellation: threading.Event,
    ) -> dict[str, Any]:
        request = WORKER.validate_request(request_value)
        if (
            request["role"] != role
            or role not in ROLES
            or not callable(authority_check)
            or not isinstance(cancellation, threading.Event)
        ):
            raise StartupNormalizationPhaseError(
                "normalization worker invocation binding differs"
            )
        process: subprocess.Popen[bytes] | None = None
        root_descriptor: int | None = None
        root_identity: PROCESS.ProcessIdentity | None = None
        tracked: set[PROCESS.ProcessIdentity] = set()
        registry_slot = False
        session_token: int | None = None
        selector = selectors.DefaultSelector()
        stdout_buffer = bytearray()
        pending_stdin = bytearray()
        stderr_bytes = 0
        total_stdout = 0
        expected_sequence = 1
        completed_cleanly = False
        result: dict[str, Any] | None = None
        deadline = time.monotonic() + WORKER_SESSION_TIMEOUT_SECONDS
        try:
            with self._ownership_lock:
                PROCESS._enable_child_subreaper()  # noqa: SLF001
                if self._active_session_count == 0:
                    self._direct_child_baseline = (
                        PROCESS._direct_child_baseline()  # noqa: SLF001
                    )
                session_token = self._next_session_token
                self._next_session_token += 1
                self._active_sessions[session_token] = None
                self._active_session_count += 1
                registry_slot = True
                process = self.session_factory(  # noqa: S603
                    list(self._argv(role, request)),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={
                        "PATH": "/usr/bin:/bin",
                        "HOME": "/root",
                        "LANG": "C.UTF-8",
                        "LC_ALL": "C.UTF-8",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    close_fds=True,
                    start_new_session=True,
                )
                if (
                    process.stdin is None
                    or process.stdout is None
                    or process.stderr is None
                    or type(process.pid) is not int
                    or process.pid <= 1
                ):
                    raise StartupNormalizationPhaseError(
                        f"{role} normalization session is not isolated"
                    )
                self._active_root_pids[session_token] = process.pid
                # Retain the kernel handle before consulting mutable /proc
                # process-group/session metadata.
                root_descriptor = os.pidfd_open(process.pid, 0)
                root_identity = PROCESS._process_identity(  # noqa: SLF001
                    process.pid
                )
                if (
                    root_identity is None
                    or root_identity.process_group != process.pid
                    or root_identity.session_id != process.pid
                ):
                    raise StartupNormalizationPhaseError(
                        f"{role} normalization session identity differs"
                    )
                tracked.add(root_identity)
                self._active_sessions[session_token] = root_identity
                self._active_roots[root_identity.key] = session_token
                self._identity_owners[root_identity.key] = session_token
            for label, stream in (
                ("stdin", process.stdin),
                ("stdout", process.stdout),
                ("stderr", process.stderr),
            ):
                os.set_blocking(stream.fileno(), False)
                if label != "stdin":
                    selector.register(
                        stream,
                        selectors.EVENT_READ,
                        label,
                    )
            pending_stdin.extend(_canonical_json(request) + b"\n")
            selector.register(
                process.stdin,
                selectors.EVENT_WRITE,
                "stdin",
            )
            open_streams = {"stdout", "stderr"}
            while result is None:
                self._refresh_owned(
                    root_identity,
                    tracked,
                    session_token=session_token,
                )
                if self._ownership_abort.is_set():
                    raise StartupNormalizationPhaseError(
                        "normalization process ownership became ambiguous"
                    )
                if cancellation.is_set():
                    raise StartupNormalizationPhaseError(
                        f"{role} normalization session was cancelled"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StartupNormalizationPhaseError(
                        f"{role} normalization session timed out"
                    )
                events = selector.select(
                    min(WORKER_POLL_SECONDS, remaining)
                )
                if not events:
                    if process.poll() is not None:
                        raise StartupNormalizationPhaseError(
                            f"{role} normalization worker exited early"
                        )
                    continue
                for key, _mask in events:
                    label = str(key.data)
                    if label == "stdin":
                        try:
                            written = os.write(
                                key.fileobj.fileno(),
                                pending_stdin,
                            )
                        except BlockingIOError:
                            continue
                        if written <= 0:
                            raise StartupNormalizationPhaseError(
                                f"{role} normalization stdin stalled"
                            )
                        del pending_stdin[:written]
                        if not pending_stdin:
                            selector.unregister(key.fileobj)
                        continue
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        open_streams.discard(label)
                        if label == "stdout" and result is None:
                            raise StartupNormalizationPhaseError(
                                f"{role} normalization control reached EOF"
                            )
                        continue
                    if label == "stderr":
                        stderr_bytes += len(chunk)
                        if stderr_bytes > WORKER_STDERR_LIMIT_BYTES:
                            raise StartupNormalizationPhaseError(
                                f"{role} normalization stderr is oversized"
                            )
                        raise StartupNormalizationPhaseError(
                            f"{role} normalization emitted stderr"
                        )
                    total_stdout += len(chunk)
                    if total_stdout > WORKER_STREAM_LIMIT_BYTES:
                        raise StartupNormalizationPhaseError(
                            f"{role} normalization stdout is oversized"
                        )
                    stdout_buffer.extend(chunk)
                    if len(stdout_buffer) > WORKER.MAX_CONTROL_BYTES:
                        raise StartupNormalizationPhaseError(
                            f"{role} normalization frame is oversized"
                        )
                    frames: list[bytes] = []
                    while (newline := stdout_buffer.find(b"\n")) >= 0:
                        frames.append(bytes(stdout_buffer[:newline]))
                        del stdout_buffer[: newline + 1]
                    for index, raw in enumerate(frames):
                        try:
                            document = json.loads(
                                raw.decode("ascii"),
                                object_pairs_hook=_strict_object,
                            )
                        except (
                            UnicodeError,
                            ValueError,
                            json.JSONDecodeError,
                        ) as exc:
                            raise StartupNormalizationPhaseError(
                                f"{role} normalization frame is invalid"
                            ) from exc
                        if raw != _canonical_json(document):
                            raise StartupNormalizationPhaseError(
                                f"{role} normalization frame is not canonical"
                            )
                        if (
                            document.get("schema")
                            == WORKER.AUTHORITY_REQUEST_SCHEMA
                        ):
                            if (
                                index != len(frames) - 1
                                or stdout_buffer
                                or set(document)
                                != {
                                    "schema",
                                    "sequence",
                                    "checkpoint",
                                    "challenge",
                                    "request_binding_sha256",
                                }
                                or document["sequence"]
                                != expected_sequence
                                or document["checkpoint"]
                                != CHECKPOINTS[expected_sequence - 1]
                                or document["request_binding_sha256"]
                                != request["request_binding_sha256"]
                                or pending_stdin
                                or not isinstance(
                                    document["challenge"],
                                    str,
                                )
                                or CONTROLLER.SHA256_RE.fullmatch(
                                    document["challenge"]
                                )
                                is None
                            ):
                                raise StartupNormalizationPhaseError(
                                    f"{role} normalization authority differs"
                                )
                            authority_check(
                                role,
                                document["checkpoint"],
                            )
                            response = {
                                "schema": (
                                    WORKER.AUTHORITY_RESPONSE_SCHEMA
                                ),
                                "status": "authorized",
                                "sequence": expected_sequence,
                                "checkpoint": document["checkpoint"],
                                "challenge": document["challenge"],
                                "request_binding_sha256": request[
                                    "request_binding_sha256"
                                ],
                            }
                            pending_stdin.extend(
                                _canonical_json(response) + b"\n"
                            )
                            selector.register(
                                process.stdin,
                                selectors.EVENT_WRITE,
                                "stdin",
                            )
                            expected_sequence += 1
                            continue
                        if (
                            set(document) != {"schema", "result"}
                            or document["schema"]
                            != (
                                "production-shadow-startup-"
                                "normalization-final-v1"
                            )
                            or expected_sequence
                            != len(CHECKPOINTS) + 1
                            or index != len(frames) - 1
                            or stdout_buffer
                            or pending_stdin
                        ):
                            raise StartupNormalizationPhaseError(
                                f"{role} normalization final frame differs"
                            )
                        result = WORKER.validate_result(
                            document["result"],
                            request=request,
                        )
                        break
            try:
                try:
                    selector.unregister(process.stdin)
                except KeyError:
                    pass
                process.stdin.close()
            except (OSError, ValueError):
                pass
            exit_deadline = min(
                deadline,
                time.monotonic() + WORKER_TERM_GRACE_SECONDS,
            )
            while process.poll() is None or open_streams:
                self._refresh_owned(
                    root_identity,
                    tracked,
                    session_token=session_token,
                )
                if self._ownership_abort.is_set():
                    raise StartupNormalizationPhaseError(
                        "normalization process ownership became ambiguous"
                    )
                remaining = exit_deadline - time.monotonic()
                if remaining <= 0:
                    raise StartupNormalizationPhaseError(
                        f"{role} normalization did not exit"
                    )
                for key, _mask in selector.select(
                    min(WORKER_POLL_SECONDS, remaining)
                ):
                    label = str(key.data)
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        open_streams.discard(label)
                    elif label == "stderr":
                        raise StartupNormalizationPhaseError(
                            f"{role} normalization emitted trailing stderr"
                        )
                    else:
                        raise StartupNormalizationPhaseError(
                            f"{role} normalization emitted trailing stdout"
                        )
            if process.wait(timeout=0.1) != 0 or result is None:
                raise StartupNormalizationPhaseError(
                    f"{role} normalization exited unsuccessfully"
                )
            residual = {
                identity
                for identity in self._refresh_owned(
                    root_identity,
                    tracked,
                    session_token=session_token,
                )
                if (
                    identity.pid != root_identity.pid
                    or identity.start_time != root_identity.start_time
                )
            }
            if residual:
                raise StartupNormalizationPhaseError(
                    f"{role} normalization retained descendants"
                )
            completed_cleanly = True
            return result
        except (
            OSError,
            subprocess.SubprocessError,
            WORKER.StartupNormalizationError,
        ) as exc:
            raise StartupNormalizationPhaseError(
                f"{role} normalization session failed closed"
            ) from exc
        finally:
            original_error = sys.exception()
            cleanup_error: BaseException | None = None
            if process is not None:
                try:
                    self._terminate(
                        process,
                        root_descriptor=root_descriptor,
                        root_identity=root_identity,
                        tracked=tracked,
                        session_token=(
                            session_token
                            if session_token is not None
                            else -1
                        ),
                    )
                except BaseException as exc:
                    cleanup_error = exc
            try:
                selector.close()
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
            if process is not None:
                for stream in (
                    process.stdin,
                    process.stdout,
                    process.stderr,
                ):
                    if stream is None:
                        continue
                    try:
                        if not stream.closed:
                            stream.close()
                    except BaseException as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            if root_descriptor is not None:
                try:
                    os.close(root_descriptor)
                except BaseException as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            if registry_slot:
                try:
                    self._end_session(
                        (
                            session_token
                            if session_token is not None
                            else -1
                        ),
                        root_identity,
                        tracked=tracked,
                    )
                except BaseException as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            if cleanup_error is not None:
                if original_error is not None:
                    if hasattr(original_error, "add_note"):
                        original_error.add_note(
                            "normalization session cleanup also failed: "
                            f"{type(cleanup_error).__name__}"
                        )
                else:
                    raise cleanup_error


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StartupNormalizationPhaseError(
            "phase value is not canonical JSON"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _document_sha256(value: Mapping[str, Any]) -> str:
    return _sha256(_canonical_json(value) + b"\n")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StartupNormalizationPhaseError(
                f"duplicate JSON field: {key}"
            )
        result[key] = value
    return result


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise StartupNormalizationPhaseError(
            f"{label} is not canonical UTC"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise StartupNormalizationPhaseError(
            f"{label} is not canonical UTC"
        ) from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat(timespec="microseconds").replace(
            "+00:00",
            "Z",
        )
        != value
    ):
        raise StartupNormalizationPhaseError(
            f"{label} is not canonical UTC"
        )
    return parsed


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _absolute_path(value: Path | str, *, label: str) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(os.path.abspath(os.fspath(path)))
    ):
        raise StartupNormalizationPhaseError(
            f"{label} must be an absolute normalized path"
        )
    return path


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or CONTROLLER.SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise StartupNormalizationPhaseError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _validate_role_mapping(
    value: Any,
    *,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(ROLES):
        raise StartupNormalizationPhaseError(
            f"{label} role mapping is not exact"
        )
    return value


def _historical_aggregate_time(value: Mapping[str, Any]) -> datetime:
    return _timestamp(
        value.get("controller_observed_at"),
        label="running aggregate controller observation",
    )


def _validate_aggregate(
    aggregate: Mapping[str, Any],
    *,
    requests: Mapping[str, Mapping[str, Any]],
    responses: Mapping[str, Mapping[str, Any]],
    now: datetime,
    label: str,
) -> dict[str, Any]:
    try:
        return PREPARED.validate_aggregate(
            aggregate,
            requests=requests,
            responses=responses,
            now=now,
        )
    except PREPARED.PreparedCloneInventoryError as exc:
        raise StartupNormalizationPhaseError(
            f"{label} three-role inventory is invalid"
        ) from exc


def _validate_worker_result(
    result: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    completed_at = _timestamp(
        result.get("completed_at"),
        label=f"{request.get('role')} worker completion",
    )
    try:
        return WORKER.validate_result(
            result,
            request=request,
            now=completed_at,
        )
    except WORKER.StartupNormalizationError as exc:
        raise StartupNormalizationPhaseError(
            f"{request.get('role')} normalization result is invalid"
        ) from exc


def _identity_matches(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> bool:
    return all(
        observed.get(field) == expected.get(field)
        for field in (
            "campaign_id",
            "operation_id",
            "release_sha",
            "release_tree_sha",
            "role",
            "expected_host",
        )
    )


def _operation_counts_are_exact(value: Any) -> bool:
    return value == {
        "container": 1,
        "network": 1,
        "volume": 0,
        "image": 0,
    }


def _optional_bindings_match(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    for field in OPTIONAL_REDIS_BINDING_FIELDS:
        before_present = field in before
        after_present = field in after
        if before_present != after_present:
            return False
        if before_present and before[field] != after[field]:
            return False
    return True


def _chronology_is_valid(
    *,
    running_captured_at: datetime,
    running_controller_observed_at: datetime,
    normalization_issued_at: datetime,
    normalization_captured_at: datetime,
    normalization_completed_at: datetime,
    stopped_issued_at: datetime,
    stopped_captured_at: datetime,
    stopped_controller_observed_at: datetime,
) -> bool:
    # Controller-to-controller and worker-to-worker edges are strict.  Only
    # controller/remote edges use the prepared collector's existing 5s bound.
    return (
        running_captured_at
        <= running_controller_observed_at + CROSS_CLOCK_SKEW
        and running_controller_observed_at
        <= normalization_issued_at
        and normalization_issued_at
        <= normalization_captured_at + CROSS_CLOCK_SKEW
        and normalization_captured_at
        <= normalization_completed_at
        and normalization_completed_at
        <= stopped_issued_at + CROSS_CLOCK_SKEW
        and stopped_issued_at
        <= stopped_captured_at + CROSS_CLOCK_SKEW
        and stopped_captured_at
        <= stopped_controller_observed_at + CROSS_CLOCK_SKEW
    )


def _role_closure(
    role: str,
    *,
    running_request: Mapping[str, Any],
    running_response: Mapping[str, Any],
    normalization_request: Mapping[str, Any],
    normalization_result: Mapping[str, Any],
    stopped_request: Mapping[str, Any],
    stopped_response: Mapping[str, Any],
    running_controller_observed_at: datetime,
    stopped_controller_observed_at: datetime,
) -> dict[str, Any]:
    if not all(
        _identity_matches(running_request, value)
        for value in (
            running_response,
            normalization_request,
            normalization_result,
            stopped_request,
            stopped_response,
        )
    ):
        raise StartupNormalizationPhaseError(
            f"{role} source identity differs"
        )
    if (
        normalization_request["pre_inventory_request"] != running_request
        or normalization_request["pre_inventory_response"]
        != running_response
        or normalization_result["pre_inventory_response_sha256"]
        != running_response["response_sha256"]
        or stopped_request["baseline_response_sha256"]
        != running_response["response_sha256"]
        or running_response["prepared_database_running"] is not True
        or running_response["prepared_database_healthy"] is not True
        or stopped_response["prepared_database_running"] is not False
        or stopped_response["prepared_database_healthy"] is not False
        or not _operation_counts_are_exact(
            running_response["operation_resource_counts"]
        )
        or not _operation_counts_are_exact(
            stopped_response["operation_resource_counts"]
        )
        or any(
            running_response[field] != stopped_response[field]
            for field in RESOURCE_BINDING_FIELDS
        )
        or any(
            running_response[field] != stopped_response[field]
            for field in NON_OPERATION_FIELDS
        )
        or not _optional_bindings_match(
            running_response,
            stopped_response,
        )
        or any(
            normalization_result[field] != running_response[field]
            for field in RESOURCE_BINDING_FIELDS
        )
    ):
        raise StartupNormalizationPhaseError(
            f"{role} stopped inventory is not the exact prepared closure"
        )
    resource_binding = {
        field: stopped_response[field]
        for field in RESOURCE_BINDING_FIELDS
    }
    for field in OPTIONAL_REDIS_BINDING_FIELDS:
        if field in stopped_response:
            resource_binding[field] = stopped_response[field]
    running_captured_at = _timestamp(
        running_response["captured_at"],
        label=f"{role} running capture",
    )
    normalization_issued_at = _timestamp(
        normalization_request["issued_at"],
        label=f"{role} normalization issue",
    )
    normalization_captured_at = _timestamp(
        normalization_result["captured_at"],
        label=f"{role} normalization capture",
    )
    normalization_completed_at = _timestamp(
        normalization_result["completed_at"],
        label=f"{role} normalization completion",
    )
    stopped_issued_at = _timestamp(
        stopped_request["issued_at"],
        label=f"{role} stopped inventory issue",
    )
    stopped_captured_at = _timestamp(
        stopped_response["captured_at"],
        label=f"{role} stopped capture",
    )
    chronology = {
        "running_captured_at": _timestamp_text(running_captured_at),
        "running_controller_observed_at": _timestamp_text(
            running_controller_observed_at
        ),
        "normalization_issued_at": _timestamp_text(
            normalization_issued_at
        ),
        "normalization_captured_at": _timestamp_text(
            normalization_captured_at
        ),
        "normalization_completed_at": _timestamp_text(
            normalization_completed_at
        ),
        "stopped_issued_at": _timestamp_text(stopped_issued_at),
        "stopped_captured_at": _timestamp_text(stopped_captured_at),
        "stopped_controller_observed_at": _timestamp_text(
            stopped_controller_observed_at
        ),
    }
    if not _chronology_is_valid(
        running_captured_at=running_captured_at,
        running_controller_observed_at=(
            running_controller_observed_at
        ),
        normalization_issued_at=normalization_issued_at,
        normalization_captured_at=normalization_captured_at,
        normalization_completed_at=normalization_completed_at,
        stopped_issued_at=stopped_issued_at,
        stopped_captured_at=stopped_captured_at,
        stopped_controller_observed_at=(
            stopped_controller_observed_at
        ),
    ):
        raise StartupNormalizationPhaseError(
            f"{role} phase source chronology is reordered or stale"
        )
    document = {
        "schema": ROLE_CLOSURE_SCHEMA,
        "role": role,
        "expected_host": INVENTORY.ROLE_HOSTS[role],
        "running_request_sha256": _document_sha256(running_request),
        "running_response_sha256": _document_sha256(running_response),
        "normalization_request_sha256": _document_sha256(
            normalization_request
        ),
        "normalization_result_sha256": _document_sha256(
            normalization_result
        ),
        "stopped_request_sha256": _document_sha256(stopped_request),
        "stopped_response_sha256": _document_sha256(stopped_response),
        "prepared_container_id": stopped_response[
            "prepared_container_id"
        ],
        "prepared_network_id": stopped_response["prepared_network_id"],
        "non_operation_inventory_root_sha256": stopped_response[
            "non_operation_inventory_root_sha256"
        ],
        "operation_resource_counts": dict(
            stopped_response["operation_resource_counts"]
        ),
        "prepared_database_running": False,
        "prepared_database_healthy": False,
        "resource_binding_sha256": _sha256(
            _canonical_json(resource_binding)
        ),
        "chronology_sha256": _sha256(_canonical_json(chronology)),
        "captured_at": _timestamp_text(stopped_captured_at),
    }
    if set(document) != ROLE_CLOSURE_FIELDS:
        raise StartupNormalizationPhaseError(
            f"{role} role closure fields differ"
        )
    return document


def validate_normalization_closure(
    sources: ClosureInputs,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate all three transitions and derive the only public claims."""

    if not isinstance(sources, ClosureInputs):
        raise StartupNormalizationPhaseError(
            "normalization closure inputs are invalid"
        )
    for value, label in (
        (sources.running_requests, "running requests"),
        (sources.running_responses, "running responses"),
        (sources.normalization_requests, "normalization requests"),
        (sources.normalization_results, "normalization results"),
        (sources.stopped_requests, "stopped requests"),
        (sources.stopped_responses, "stopped responses"),
    ):
        _validate_role_mapping(value, label=label)
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    running_time = _historical_aggregate_time(sources.running_aggregate)
    running = _validate_aggregate(
        sources.running_aggregate,
        requests=sources.running_requests,
        responses=sources.running_responses,
        now=running_time,
        label="running",
    )
    stopped = _validate_aggregate(
        sources.stopped_aggregate,
        requests=sources.stopped_requests,
        responses=sources.stopped_responses,
        now=observed_now,
        label="stopped",
    )
    stopped_controller_time = _timestamp(
        stopped["controller_observed_at"],
        label="stopped aggregate controller observation",
    )
    if (
        running["expected_database_state"] != "running-healthy"
        or stopped["expected_database_state"] != "stopped"
        or any(
            running[field] != stopped[field]
            for field in (
                "campaign_id",
                "operation_id",
                "release_sha",
                "release_tree_sha",
            )
        )
    ):
        raise StartupNormalizationPhaseError(
            "running and stopped aggregate identities differ"
        )
    worker_results: dict[str, dict[str, Any]] = {}
    role_rows: dict[str, dict[str, Any]] = {}
    worker_challenges: dict[str, str] = {}
    worker_times: list[datetime] = []
    for role in ROLES:
        request = sources.normalization_requests[role]
        if request.get("role") != role:
            raise StartupNormalizationPhaseError(
                f"{role} normalization request role differs"
            )
        worker_results[role] = _validate_worker_result(
            sources.normalization_results[role],
            request=request,
        )
        worker_challenges[role] = request[
            "controller_challenge_sha256"
        ]
        worker_times.append(
            _timestamp(
                worker_results[role]["completed_at"],
                label=f"{role} worker completion",
            )
        )
        role_rows[role] = _role_closure(
            role,
            running_request=sources.running_requests[role],
            running_response=sources.running_responses[role],
            normalization_request=request,
            normalization_result=worker_results[role],
            stopped_request=sources.stopped_requests[role],
            stopped_response=sources.stopped_responses[role],
            running_controller_observed_at=running_time,
            stopped_controller_observed_at=stopped_controller_time,
        )
    challenge_values = {
        running["controller_challenge_sha256"],
        stopped["controller_challenge_sha256"],
        *worker_challenges.values(),
    }
    if (
        len(challenge_values) != 2 + len(ROLES)
        or (
            max(worker_times) - min(worker_times)
        ).total_seconds()
        > MAX_WORKER_CAPTURE_SKEW_SECONDS
    ):
        raise StartupNormalizationPhaseError(
            "normalization challenges or cross-role capture skew differ"
        )
    captured_at = max(
        [
            _timestamp(
                stopped["controller_observed_at"],
                label="stopped aggregate observation",
            ),
            *worker_times,
        ]
    )
    if captured_at > observed_now + timedelta(seconds=5):
        raise StartupNormalizationPhaseError(
            "normalization closure observation is in the future"
        )

    # Each zero is derived only after the fresh stopped collector proves:
    # (a) every non-operation root/count is byte-for-byte unchanged,
    # (b) the exact sole operation container is the prepared DB and is stopped,
    # (c) all immutable/container/network bindings are unchanged.
    claims = {
        "legacy_resource_delta_count": 0,
        "operation_owned_running_container_count": 0,
        "unplanned_container_delta_count": 0,
    }
    claim_derivation = {
        "legacy_resource_delta_count": {
            "source": "fresh-running-vs-stopped-non-operation-roots",
            "roles": list(ROLES),
            "equal_fields": list(NON_OPERATION_FIELDS),
        },
        "operation_owned_running_container_count": {
            "source": "fresh-stopped-operation-closure",
            "roles": list(ROLES),
            "database_running": False,
            "operation_container_count_per_role": 1,
        },
        "unplanned_container_delta_count": {
            "source": "fresh-running-vs-stopped-resource-bindings",
            "roles": list(ROLES),
            "equal_fields": list(RESOURCE_BINDING_FIELDS),
        },
    }
    document: dict[str, Any] = {
        "schema": CLOSURE_SCHEMA,
        "status": "validated-fresh-stopped-closure",
        "campaign_id": running["campaign_id"],
        "operation_id": running["operation_id"],
        "release_sha": running["release_sha"],
        "release_tree_sha": running["release_tree_sha"],
        "running_aggregate_sha256": running["aggregate_sha256"],
        "stopped_aggregate_sha256": stopped["aggregate_sha256"],
        "running_controller_challenge_sha256": running[
            "controller_challenge_sha256"
        ],
        "stopped_controller_challenge_sha256": stopped[
            "controller_challenge_sha256"
        ],
        "normalization_controller_challenge_sha256_by_role": (
            worker_challenges
        ),
        "roles": role_rows,
        "claims": claims,
        "claim_derivation": claim_derivation,
        "running_inventory_historically_validated": True,
        "stopped_inventory_freshly_validated": True,
        "worker_results_validated": True,
        "caller_claims_accepted": False,
        "legacy_resource_mutated": False,
        "current_mutated": False,
        "persistent_resource_removed": False,
        "object_storage_used": False,
        "captured_at": _timestamp_text(captured_at),
        "closure_sha256": ZERO_SHA256,
    }
    document["closure_sha256"] = _sha256(
        _canonical_json(
            {
                key: value
                for key, value in document.items()
                if key != "closure_sha256"
            }
        )
    )
    if set(document) != CLOSURE_FIELDS:
        raise StartupNormalizationPhaseError(
            "normalization closure fields are not exact"
        )
    return validate_closure(document)


def validate_closure(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != CLOSURE_FIELDS:
        raise StartupNormalizationPhaseError(
            "normalization closure fields are not exact"
        )
    document = json.loads(
        _canonical_json(dict(value)).decode("ascii"),
        object_pairs_hook=_strict_object,
    )
    if (
        document["schema"] != CLOSURE_SCHEMA
        or document["status"] != "validated-fresh-stopped-closure"
        or set(document["roles"]) != set(ROLES)
        or document["claims"]
        != {
            "legacy_resource_delta_count": 0,
            "operation_owned_running_container_count": 0,
            "unplanned_container_delta_count": 0,
        }
        or set(document["claim_derivation"]) != set(CLAIMS)
        or document["running_inventory_historically_validated"] is not True
        or document["stopped_inventory_freshly_validated"] is not True
        or document["worker_results_validated"] is not True
        or document["caller_claims_accepted"] is not False
        or document["legacy_resource_mutated"] is not False
        or document["current_mutated"] is not False
        or document["persistent_resource_removed"] is not False
        or document["object_storage_used"] is not False
    ):
        raise StartupNormalizationPhaseError(
            "normalization closure safety claims differ"
        )
    _timestamp(document["captured_at"], label="closure captured_at")
    for role in ROLES:
        row = document["roles"][role]
        if (
            not isinstance(row, dict)
            or set(row) != ROLE_CLOSURE_FIELDS
            or row["schema"] != ROLE_CLOSURE_SCHEMA
            or row["role"] != role
            or row["expected_host"] != INVENTORY.ROLE_HOSTS[role]
            or row["prepared_database_running"] is not False
            or row["prepared_database_healthy"] is not False
            or not _operation_counts_are_exact(
                row["operation_resource_counts"]
            )
        ):
            raise StartupNormalizationPhaseError(
                f"{role} closure row differs"
            )
        for field in (
            "running_request_sha256",
            "running_response_sha256",
            "normalization_request_sha256",
            "normalization_result_sha256",
            "stopped_request_sha256",
            "stopped_response_sha256",
            "non_operation_inventory_root_sha256",
            "resource_binding_sha256",
            "chronology_sha256",
        ):
            _nonzero_sha256(row[field], label=f"{role} {field}")
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "closure_sha256"
    }
    if document["closure_sha256"] != _sha256(_canonical_json(unsigned)):
        raise StartupNormalizationPhaseError(
            "normalization closure digest differs"
        )
    return document


def _ensure_private_directory(path: Path) -> None:
    path = _absolute_path(path, label="phase output directory")
    if path == path.parent:
        raise StartupNormalizationPhaseError(
            "filesystem root cannot be a phase output directory"
        )
    if not path.exists():
        _ensure_private_directory(path.parent)
        try:
            os.mkdir(path, OUTPUT_DIRECTORY_MODE)
        except FileExistsError:
            pass
        except OSError as exc:
            raise StartupNormalizationPhaseError(
                "phase output directory cannot be created"
            ) from exc
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise StartupNormalizationPhaseError(
            "phase output directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != OUTPUT_DIRECTORY_MODE
    ):
        raise StartupNormalizationPhaseError(
            "phase output directory is not root-only"
        )


def _persist_document(
    directory: Path,
    *,
    prefix: str,
    document: Mapping[str, Any],
) -> tuple[Path, str]:
    _ensure_private_directory(directory)
    payload = _canonical_json(document) + b"\n"
    if not 1 <= len(payload) <= MAX_JSON_BYTES:
        raise StartupNormalizationPhaseError(
            f"{prefix} document exceeds its bound"
        )
    digest = _sha256(payload)
    path = directory / f"{prefix}.{digest}.json"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=prefix,
            mode=OUTPUT_FILE_MODE,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError:
        try:
            existing = read_secure_bytes(
                path,
                label=f"existing {prefix}",
                owner_uid=0,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise StartupNormalizationPhaseError(
                f"{prefix} could not be persisted safely"
            ) from exc
        if existing != payload:
            raise StartupNormalizationPhaseError(
                f"existing digest-addressed {prefix} differs"
            )
    try:
        observed = read_secure_bytes(
            path,
            label=f"persisted {prefix}",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise StartupNormalizationPhaseError(
            f"{prefix} readback failed"
        ) from exc
    if observed != payload or _sha256(observed) != digest:
        raise StartupNormalizationPhaseError(
            f"{prefix} readback differs"
        )
    return path, digest


def _manifest_output_root(manifest: Mapping[str, Any]) -> Path:
    return _absolute_path(
        Path(manifest["deployment"]["controller_evidence_root"])
        / OUTPUT_SUBDIRECTORY,
        label="manifest-derived phase evidence output root",
    )


def load_evidence_context(
    *,
    manifest_path: Path,
    approval_path: Path,
    approval_policy_path: Path,
    prior_evidence_paths: Mapping[str, Path],
) -> EvidenceContext:
    """Load the only trusted local controller context for apply/resume."""

    if os.geteuid() != 0 or os.getegid() != 0:
        raise StartupNormalizationPhaseError(
            "phase controller context requires root:root"
        )
    manifest_path = _absolute_path(
        manifest_path,
        label="cutover manifest",
    )
    approval_path = _absolute_path(
        approval_path,
        label="cutover approval",
    )
    approval_policy_path = _absolute_path(
        approval_policy_path,
        label="approval policy",
    )
    try:
        manifest, manifest_sha256 = CONTROLLER.read_root_only_manifest(
            manifest_path
        )
        plan = CONTROLLER.render_plan(
            manifest,
            manifest_sha256=manifest_sha256,
            manifest_path=manifest_path,
        )
        approval = read_secure_bytes(
            approval_path,
            label="production cutover approval",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        approval_policy = read_secure_bytes(
            approval_policy_path,
            label="production human approval policy",
            owner_uid=0,
            max_size=4 * 1024 * 1024,
        )
    except (CONTROLLER.CutoverContractError, SecureFileError) as exc:
        raise StartupNormalizationPhaseError(
            "trusted cutover context is unavailable or unsafe"
        ) from exc
    if (
        _sha256(approval)
        != manifest["artifacts"]["cutover_approval_sha256"]
        or _sha256(approval_policy)
        != manifest["artifacts"]["human_approval_policy_sha256"]
    ):
        raise StartupNormalizationPhaseError(
            "approval artifacts differ from the cutover manifest"
        )
    journal_path = _absolute_path(
        Path(manifest["deployment"]["controller_journal_path"]),
        label="cutover journal",
    )
    try:
        journal = CONTROLLER.ProductionCutoverJournal(
            journal_path
        ).load()
    except CONTROLLER.CutoverContractError as exc:
        raise StartupNormalizationPhaseError(
            "cutover journal is unavailable or unsafe"
        ) from exc
    expected_prior = tuple(
        CONTROLLER.PHASES[: CONTROLLER.PHASES.index(PHASE)]
    )
    if (
        not isinstance(prior_evidence_paths, Mapping)
        or set(prior_evidence_paths) != set(expected_prior)
    ):
        raise StartupNormalizationPhaseError(
            "prior evidence paths are not the exact phase prefix"
        )
    prior_records: dict[str, dict[str, Any]] = {}
    prior_digests: dict[str, str] = {}
    prior_paths: dict[str, Path] = {}
    for prior_phase in expected_prior:
        path = _absolute_path(
            prior_evidence_paths[prior_phase],
            label=f"{prior_phase} prior evidence",
        )
        try:
            document, digest = VERIFY.read_root_only_evidence(path)
        except VERIFY.PhaseEvidenceError as exc:
            raise StartupNormalizationPhaseError(
                f"{prior_phase} prior evidence is unavailable or unsafe"
            ) from exc
        prior_records[prior_phase] = document
        prior_digests[prior_phase] = digest
        prior_paths[prior_phase] = path
    context = EvidenceContext(
        manifest_path=manifest_path,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
        journal_path=journal_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan=plan,
        plan_sha256=plan["plan_sha256"],
        journal=journal,
        prior_records=prior_records,
        prior_digests=prior_digests,
        prior_paths=prior_paths,
        output_root=_manifest_output_root(manifest),
    )
    _validated_controller_context(
        context,
        required_position="any",
    )
    return context


def _validated_controller_context(
    context: EvidenceContext,
    *,
    required_position: str = "started",
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[str, Any]],
    Path,
]:
    if (
        not isinstance(context, EvidenceContext)
        or os.geteuid() != 0
        or os.getegid() != 0
    ):
        raise StartupNormalizationPhaseError(
            "phase evidence publication requires a root-owned context"
        )
    try:
        manifest = CONTROLLER.validate_manifest(
            json.loads(_canonical_json(dict(context.manifest)))
        )
        journal = CONTROLLER._validate_journal(  # noqa: SLF001
            json.loads(_canonical_json(dict(context.journal)))
        )
    except (CONTROLLER.CutoverContractError, TypeError) as exc:
        raise StartupNormalizationPhaseError(
            "phase evidence controller context is invalid"
        ) from exc
    manifest_sha256 = _nonzero_sha256(
        context.manifest_sha256,
        label="context manifest",
    )
    plan_sha256 = _nonzero_sha256(
        context.plan_sha256,
        label="context plan",
    )
    if (
        required_position not in {"ready", "started", "completed", "any"}
        or not isinstance(context.plan, Mapping)
        or context.plan.get("plan_sha256") != plan_sha256
    ):
        raise StartupNormalizationPhaseError(
            "phase evidence controller plan differs"
        )
    manifest_path = _absolute_path(
        context.manifest_path,
        label="cutover manifest",
    )
    _absolute_path(context.approval_path, label="cutover approval")
    _absolute_path(
        context.approval_policy_path,
        label="approval policy",
    )
    journal_path = _absolute_path(
        context.journal_path,
        label="cutover journal",
    )
    if journal_path != Path(
        manifest["deployment"]["controller_journal_path"]
    ):
        raise StartupNormalizationPhaseError(
            "phase evidence journal path differs from the manifest"
        )
    del manifest_path
    expected_bindings = {
        "manifest_sha256": manifest_sha256,
        "plan_sha256": plan_sha256,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
    }
    if any(
        journal.get(field) != expected
        for field, expected in expected_bindings.items()
    ):
        raise StartupNormalizationPhaseError(
            "phase evidence journal bindings differ"
        )
    expected_prior = list(
        CONTROLLER.PHASES[: CONTROLLER.PHASES.index(PHASE)]
    )
    if (
        journal["completed_phases"] == expected_prior
        and journal["status"] == "active"
        and journal["started_phase"] is None
    ):
        position = "ready"
    elif (
        journal["completed_phases"] == expected_prior
        and journal["status"] == "phase_started"
        and journal["started_phase"] == PHASE
    ):
        position = "started"
    elif (
        journal["completed_phases"] == [*expected_prior, PHASE]
        and journal["status"] == "active"
        and journal["started_phase"] is None
    ):
        position = "completed"
    else:
        position = "invalid"
    if (
        position == "invalid"
        or (
            required_position != "any"
            and position != required_position
        )
        or not isinstance(context.prior_records, Mapping)
        or set(context.prior_records) != set(expected_prior)
        or not isinstance(context.prior_digests, Mapping)
        or dict(context.prior_digests)
        != {
            phase: journal["phase_evidence_sha256"][phase]
            for phase in expected_prior
        }
        or not isinstance(context.prior_paths, Mapping)
        or set(context.prior_paths) != set(expected_prior)
    ):
        raise StartupNormalizationPhaseError(
            "normalization phase is not the exact durable journal successor"
        )
    prior_records: dict[str, dict[str, Any]] = {}
    for prior_phase in expected_prior:
        raw = context.prior_records[prior_phase]
        if not isinstance(raw, Mapping):
            raise StartupNormalizationPhaseError(
                f"{prior_phase} prior evidence is invalid"
            )
        document = json.loads(_canonical_json(dict(raw)))
        digest = context.prior_digests[prior_phase]
        if (
            set(document) != VERIFY.EVIDENCE_FIELDS
            or _document_sha256(document) != digest
            or document.get("phase") != prior_phase
            or document.get("campaign_id") != manifest["campaign_id"]
            or document.get("operation_id") != manifest["operation_id"]
            or document.get("release_sha") != manifest["release_sha"]
            or document.get("legacy_release_sha")
            != manifest["legacy_release_sha"]
            or document.get("manifest_sha256") != manifest_sha256
            or document.get("plan_sha256") != plan_sha256
            or document.get("approval_sha256")
            != manifest["artifacts"]["cutover_approval_sha256"]
            or document.get("status") != "passed"
            or document.get("business_write_observed") is not False
        ):
            raise StartupNormalizationPhaseError(
                f"{prior_phase} prior evidence differs from the journal"
            )
        prior_records[prior_phase] = {
            "document": document,
            "file_sha256": digest,
        }
    if (
        manifest["artifacts"]["phase_evidence_schema_sha256"]
        != VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
    ):
        raise StartupNormalizationPhaseError(
            "phase evidence contract differs from the release manifest"
        )
    output_root = _absolute_path(
        context.output_root,
        label="phase evidence output root",
    )
    expected_output_root = _manifest_output_root(manifest)
    if output_root != expected_output_root:
        raise StartupNormalizationPhaseError(
            "phase evidence output root is not manifest-derived"
        )
    return manifest, journal, prior_records, output_root


def _validated_evidence_context(
    context: EvidenceContext,
    *,
    closure: Mapping[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[str, Any]],
    Path,
]:
    manifest, journal, prior_records, output_root = (
        _validated_controller_context(context)
    )
    if any(
        closure.get(field) != manifest[field]
        for field in (
            "campaign_id",
            "operation_id",
            "release_sha",
            "release_tree_sha",
        )
    ):
        raise StartupNormalizationPhaseError(
            "normalization closure differs from the cutover manifest"
        )
    return manifest, journal, prior_records, output_root


def _require_private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise StartupNormalizationPhaseError(
            f"{label} directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != OUTPUT_DIRECTORY_MODE
    ):
        raise StartupNormalizationPhaseError(
            f"{label} directory is not root-only"
        )


def _load_worker_source(
    *,
    output_root: Path,
    role: str,
    kind: str,
    path: Path,
    expected_sha256: str,
) -> dict[str, Any]:
    if kind not in {"request", "result"} or role not in ROLES:
        raise StartupNormalizationPhaseError(
            "worker persisted source identity is invalid"
        )
    digest = _nonzero_sha256(
        expected_sha256,
        label=f"{role} normalization {kind} artifact",
    )
    source_root = output_root / PHASE / "worker-sources"
    expected_path = (
        source_root
        / f"normalization-{kind}-{role}.{digest}.json"
    )
    observed_path = _absolute_path(
        path,
        label=f"{role} normalization {kind} source",
    )
    if observed_path != expected_path:
        raise StartupNormalizationPhaseError(
            f"{role} normalization {kind} path is not canonical"
        )
    for directory, label in (
        (output_root, "phase output"),
        (output_root / PHASE, "normalization phase"),
        (source_root, "normalization worker source"),
    ):
        _require_private_directory(directory, label=label)
    try:
        payload = read_secure_bytes(
            observed_path,
            label=f"{role} normalization {kind}",
            owner_uid=0,
            max_size=WORKER.MAX_RESPONSE_BYTES + 1,
        )
    except SecureFileError as exc:
        raise StartupNormalizationPhaseError(
            f"{role} normalization {kind} is unavailable or unsafe"
        ) from exc
    if (
        _sha256(payload) != digest
        or not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
    ):
        raise StartupNormalizationPhaseError(
            f"{role} normalization {kind} bytes differ"
        )
    try:
        document = json.loads(
            payload[:-1].decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise StartupNormalizationPhaseError(
            f"{role} normalization {kind} is not strict JSON"
        ) from exc
    if (
        not isinstance(document, dict)
        or payload != _canonical_json(document) + b"\n"
    ):
        raise StartupNormalizationPhaseError(
            f"{role} normalization {kind} is not canonical"
        )
    return document


class PersistedClosureSourceLoader:
    """Load only manifest-rooted immutable receipts and worker artifacts."""

    def __init__(
        self,
        context: EvidenceContext,
        spec: PersistedClosureSourceSpec,
        *,
        now: datetime | None = None,
    ):
        if not isinstance(spec, PersistedClosureSourceSpec):
            raise StartupNormalizationPhaseError(
                "persisted closure source specification is invalid"
            )
        self.context = context
        self.spec = spec
        self.now = (
            datetime.now(timezone.utc)
            if now is None
            else now.astimezone(timezone.utc)
        )

    def load(self) -> ClosureInputs:
        manifest, journal, _prior, output_root = (
            _validated_controller_context(self.context)
        )
        spec = self.spec
        for mapping, label in (
            (
                spec.normalization_request_paths,
                "normalization request paths",
            ),
            (
                spec.normalization_request_artifact_sha256,
                "normalization request digests",
            ),
            (
                spec.normalization_result_paths,
                "normalization result paths",
            ),
            (
                spec.normalization_result_artifact_sha256,
                "normalization result digests",
            ),
        ):
            _validate_role_mapping(mapping, label=label)
        running_challenge = _nonzero_sha256(
            spec.running_controller_challenge_sha256,
            label="running receipt controller challenge",
        )
        stopped_challenge = _nonzero_sha256(
            spec.stopped_controller_challenge_sha256,
            label="stopped receipt controller challenge",
        )
        if running_challenge == stopped_challenge:
            raise StartupNormalizationPhaseError(
                "running and stopped receipt challenges are not independent"
            )
        try:
            running = (
                PREPARED
                .load_historical_running_prepared_clone_baseline_receipt(
                    spec.running_receipt_path,
                    output_root=output_root,
                    expected_campaign_id=manifest["campaign_id"],
                    expected_operation_id=manifest["operation_id"],
                    expected_release_sha=manifest["release_sha"],
                    expected_release_tree_sha=manifest[
                        "release_tree_sha"
                    ],
                    expected_controller_challenge_sha256=(
                        running_challenge
                    ),
                    expected_aggregate_artifact_sha256=(
                        spec.running_aggregate_artifact_sha256
                    ),
                    now=self.now,
                )
            )
            stopped = PREPARED.load_pre_freeze_current_operation_receipt(
                spec.stopped_receipt_path,
                output_root=output_root,
                now=self.now,
            )
        except PREPARED.PreparedCloneInventoryError as exc:
            raise StartupNormalizationPhaseError(
                "persisted prepared inventory receipt is invalid"
            ) from exc
        if (
            stopped.get("readback_verified") is not True
            or stopped["aggregate"]["sha256"]
            != _nonzero_sha256(
                spec.stopped_aggregate_artifact_sha256,
                label="stopped receipt aggregate artifact",
            )
            or stopped["receipt"].get(
                "controller_challenge_sha256"
            )
            != stopped_challenge
            or stopped["receipt"].get("expected_database_state")
            != "stopped"
            or any(
                stopped["receipt"].get(field) != manifest[field]
                for field in (
                    "campaign_id",
                    "operation_id",
                    "release_sha",
                    "release_tree_sha",
                )
            )
        ):
            raise StartupNormalizationPhaseError(
                "fresh stopped receipt differs from the manifest binding"
            )
        normalization_requests: dict[str, dict[str, Any]] = {}
        normalization_results: dict[str, dict[str, Any]] = {}
        for role in ROLES:
            normalization_requests[role] = _load_worker_source(
                output_root=output_root,
                role=role,
                kind="request",
                path=spec.normalization_request_paths[role],
                expected_sha256=(
                    spec.normalization_request_artifact_sha256[role]
                ),
            )
            normalization_results[role] = _load_worker_source(
                output_root=output_root,
                role=role,
                kind="result",
                path=spec.normalization_result_paths[role],
                expected_sha256=(
                    spec.normalization_result_artifact_sha256[role]
                ),
            )
        try:
            journal_started_at = datetime.fromisoformat(
                str(journal["started_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise StartupNormalizationPhaseError(
                "normalization journal start time is invalid"
            ) from exc
        if any(
            _timestamp(
                normalization_requests[role]["issued_at"],
                label=f"{role} normalization issue",
            )
            < journal_started_at
            for role in ROLES
        ):
            raise StartupNormalizationPhaseError(
                "normalization worker source predates durable phase start"
            )
        sources = ClosureInputs(
            running_aggregate=running["receipt"],
            running_requests=running["requests"],
            running_responses=running["responses"],
            normalization_requests=normalization_requests,
            normalization_results=normalization_results,
            stopped_aggregate=stopped["receipt"],
            stopped_requests=stopped["requests"],
            stopped_responses=stopped["responses"],
        )
        validate_normalization_closure(sources, now=self.now)
        return sources


def _source_spec_binding(
    spec: PersistedClosureSourceSpec,
) -> tuple[dict[str, Any], str]:
    if not isinstance(spec, PersistedClosureSourceSpec):
        raise StartupNormalizationPhaseError(
            "persisted closure source specification is invalid"
        )
    for mapping, label in (
        (
            spec.normalization_request_paths,
            "normalization request paths",
        ),
        (
            spec.normalization_request_artifact_sha256,
            "normalization request digests",
        ),
        (
            spec.normalization_result_paths,
            "normalization result paths",
        ),
        (
            spec.normalization_result_artifact_sha256,
            "normalization result digests",
        ),
    ):
        _validate_role_mapping(mapping, label=label)
    document = {
        "running_receipt_path": os.fspath(
            _absolute_path(
                spec.running_receipt_path,
                label="running receipt",
            )
        ),
        "running_controller_challenge_sha256": _nonzero_sha256(
            spec.running_controller_challenge_sha256,
            label="running receipt challenge",
        ),
        "running_aggregate_artifact_sha256": _nonzero_sha256(
            spec.running_aggregate_artifact_sha256,
            label="running receipt artifact",
        ),
        "stopped_receipt_path": os.fspath(
            _absolute_path(
                spec.stopped_receipt_path,
                label="stopped receipt",
            )
        ),
        "stopped_controller_challenge_sha256": _nonzero_sha256(
            spec.stopped_controller_challenge_sha256,
            label="stopped receipt challenge",
        ),
        "stopped_aggregate_artifact_sha256": _nonzero_sha256(
            spec.stopped_aggregate_artifact_sha256,
            label="stopped receipt artifact",
        ),
        "normalization_request_paths": {
            role: os.fspath(
                _absolute_path(
                    spec.normalization_request_paths[role],
                    label=f"{role} normalization request",
                )
            )
            for role in ROLES
        },
        "normalization_request_artifact_sha256": {
            role: _nonzero_sha256(
                spec.normalization_request_artifact_sha256[role],
                label=f"{role} normalization request artifact",
            )
            for role in ROLES
        },
        "normalization_result_paths": {
            role: os.fspath(
                _absolute_path(
                    spec.normalization_result_paths[role],
                    label=f"{role} normalization result",
                )
            )
            for role in ROLES
        },
        "normalization_result_artifact_sha256": {
            role: _nonzero_sha256(
                spec.normalization_result_artifact_sha256[role],
                label=f"{role} normalization result artifact",
            )
            for role in ROLES
        },
    }
    if (
        document["running_controller_challenge_sha256"]
        == document["stopped_controller_challenge_sha256"]
    ):
        raise StartupNormalizationPhaseError(
            "persisted receipt challenges are not independent"
        )
    return document, _sha256(_canonical_json(document))


def load_persisted_source_spec_record(
    path: Path,
    *,
    context: EvidenceContext,
) -> PersistedClosureSourceSpec:
    """Load one canonical, digest-addressed source specification record."""

    manifest, _journal, _prior, output_root = (
        _validated_controller_context(
            context,
            required_position="any",
        )
    )
    path = _absolute_path(path, label="persisted source specification")
    try:
        metadata = os.lstat(path)
        payload = read_secure_bytes(
            path,
            label="persisted source specification",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (
        OSError,
        SecureFileError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise StartupNormalizationPhaseError(
            "persisted source specification is unavailable or unsafe"
        ) from exc
    file_sha256 = _sha256(payload)
    expected_path = (
        output_root
        / PHASE
        / "source-spec"
        / f"persisted-source-spec.{file_sha256}.json"
    )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != OUTPUT_FILE_MODE
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_nlink != 1
        or path != expected_path
        or payload != _canonical_json(document) + b"\n"
        or not isinstance(document, dict)
        or set(document) != SOURCE_SPEC_RECORD_FIELDS
        or document["schema"] != SOURCE_SPEC_RECORD_SCHEMA
        or document["status"]
        != "persisted-create-only-readback-verified"
        or document["campaign_id"] != manifest["campaign_id"]
        or document["operation_id"] != manifest["operation_id"]
        or document["release_sha"] != manifest["release_sha"]
        or document["manifest_sha256"] != context.manifest_sha256
        or document["controller_plan_sha256"]
        != context.plan_sha256
        or document["parallel_worker_count"] != len(ROLES)
        or document["worker_completion_skew_limit_seconds"]
        != MAX_WORKER_CAPTURE_SKEW_SECONDS
        or document["fresh_stopped_inventory"] is not True
        or type(document["journal_mutated"]) is not bool
        or document["production_contacted"] is not True
        or not isinstance(document["source_spec"], dict)
    ):
        raise StartupNormalizationPhaseError(
            "persisted source specification record differs"
        )
    value = document["source_spec"]
    expected_keys = {
        "running_receipt_path",
        "running_controller_challenge_sha256",
        "running_aggregate_artifact_sha256",
        "stopped_receipt_path",
        "stopped_controller_challenge_sha256",
        "stopped_aggregate_artifact_sha256",
        "normalization_request_paths",
        "normalization_request_artifact_sha256",
        "normalization_result_paths",
        "normalization_result_artifact_sha256",
    }
    if set(value) != expected_keys:
        raise StartupNormalizationPhaseError(
            "persisted source specification fields differ"
        )
    try:
        spec = PersistedClosureSourceSpec(
            running_receipt_path=Path(value["running_receipt_path"]),
            running_controller_challenge_sha256=value[
                "running_controller_challenge_sha256"
            ],
            running_aggregate_artifact_sha256=value[
                "running_aggregate_artifact_sha256"
            ],
            stopped_receipt_path=Path(value["stopped_receipt_path"]),
            stopped_controller_challenge_sha256=value[
                "stopped_controller_challenge_sha256"
            ],
            stopped_aggregate_artifact_sha256=value[
                "stopped_aggregate_artifact_sha256"
            ],
            normalization_request_paths={
                role: Path(value["normalization_request_paths"][role])
                for role in ROLES
            },
            normalization_request_artifact_sha256={
                role: value[
                    "normalization_request_artifact_sha256"
                ][role]
                for role in ROLES
            },
            normalization_result_paths={
                role: Path(value["normalization_result_paths"][role])
                for role in ROLES
            },
            normalization_result_artifact_sha256={
                role: value[
                    "normalization_result_artifact_sha256"
                ][role]
                for role in ROLES
            },
        )
    except (KeyError, TypeError) as exc:
        raise StartupNormalizationPhaseError(
            "persisted source specification role bindings differ"
        ) from exc
    observed, source_binding_sha256 = _source_spec_binding(spec)
    if (
        observed != value
        or document["source_binding_sha256"] != source_binding_sha256
    ):
        raise StartupNormalizationPhaseError(
            "persisted source specification digest differs"
        )
    return spec


def _root_private_document(
    path: Path,
    *,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    path = _absolute_path(path, label=label)
    try:
        metadata = os.lstat(path)
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (
        OSError,
        SecureFileError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise StartupNormalizationPhaseError(
            f"{label} is unavailable or invalid"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != OUTPUT_FILE_MODE
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_nlink != 1
        or not isinstance(document, dict)
        or payload != _canonical_json(document) + b"\n"
    ):
        raise StartupNormalizationPhaseError(
            f"{label} is not canonical root-private newline JSON"
        )
    return document, payload


def _request_reference(
    value: Any,
    *,
    label: str,
) -> tuple[Path, str]:
    if not isinstance(value, dict) or set(value) != REFERENCE_FIELDS:
        raise StartupNormalizationPhaseError(
            f"{label} reference fields differ"
        )
    return (
        _absolute_path(Path(value["path"]), label=label),
        _nonzero_sha256(value["sha256"], label=label),
    )


def persist_phase_request_create_only(
    context: EvidenceContext,
    *,
    source_spec_record_path: Path,
) -> dict[str, Any]:
    """Create the sole digest-addressed CLI request for plan/apply."""

    manifest, _journal, _prior, output_root = (
        _validated_controller_context(
            context,
            required_position="any",
        )
    )
    source_spec_record_path = _absolute_path(
        source_spec_record_path,
        label="source specification record",
    )
    source_spec = load_persisted_source_spec_record(
        source_spec_record_path,
        context=context,
    )
    _source_document, source_binding_sha256 = _source_spec_binding(
        source_spec
    )
    try:
        source_payload = read_secure_bytes(
            source_spec_record_path,
            label="source specification record",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise StartupNormalizationPhaseError(
            "source specification record cannot seed a phase request"
        ) from exc
    document = {
        "schema": PHASE_REQUEST_SCHEMA,
        "status": "ready",
        "mode": PHASE_REQUEST_MODE,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "manifest_path": os.fspath(context.manifest_path),
        "manifest_sha256": context.manifest_sha256,
        "approval_path": os.fspath(context.approval_path),
        "approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "approval_policy_path": os.fspath(
            context.approval_policy_path
        ),
        "approval_policy_sha256": manifest["artifacts"][
            "human_approval_policy_sha256"
        ],
        "controller_plan_sha256": context.plan_sha256,
        "prior_phase_evidence": {
            phase: {
                "path": os.fspath(context.prior_paths[phase]),
                "sha256": context.prior_digests[phase],
            }
            for phase in CONTROLLER.PHASES[
                : CONTROLLER.PHASES.index(PHASE)
            ]
        },
        "source_spec_record": {
            "path": os.fspath(source_spec_record_path),
            "sha256": _sha256(source_payload),
        },
        "source_binding_sha256": source_binding_sha256,
        "constraints": dict(EXPECTED_PHASE_REQUEST_CONSTRAINTS),
    }
    if set(document) != PHASE_REQUEST_FIELDS:
        raise StartupNormalizationPhaseError(
            "phase request fields differ"
        )
    path, digest = _persist_document(
        output_root / PHASE / "requests",
        prefix="phase-request",
        document=document,
    )
    return {
        "path": os.fspath(path),
        "sha256": digest,
        "source_binding_sha256": source_binding_sha256,
        "create_only": True,
        "readback_verified": True,
    }


def load_phase_request(
    request_path: Path,
) -> tuple[
    EvidenceContext,
    PersistedClosureSourceSpec,
    str,
]:
    """Load a root-private request containing paths and digests, never claims."""

    request_path = _absolute_path(
        request_path,
        label="startup-normalization phase request",
    )
    document, payload = _root_private_document(
        request_path,
        label="startup-normalization phase request",
    )
    if (
        set(document) != PHASE_REQUEST_FIELDS
        or document["schema"] != PHASE_REQUEST_SCHEMA
        or document["status"] != "ready"
        or document["mode"] != PHASE_REQUEST_MODE
        or document["constraints"]
        != EXPECTED_PHASE_REQUEST_CONSTRAINTS
        or not isinstance(document["prior_phase_evidence"], dict)
    ):
        raise StartupNormalizationPhaseError(
            "startup-normalization phase request fields differ"
        )
    expected_prior = tuple(
        CONTROLLER.PHASES[: CONTROLLER.PHASES.index(PHASE)]
    )
    if set(document["prior_phase_evidence"]) != set(expected_prior):
        raise StartupNormalizationPhaseError(
            "phase request prior evidence prefix differs"
        )
    prior_paths: dict[str, Path] = {}
    prior_digests: dict[str, str] = {}
    for phase in expected_prior:
        path, digest = _request_reference(
            document["prior_phase_evidence"][phase],
            label=f"{phase} prior phase evidence",
        )
        prior_paths[phase] = path
        prior_digests[phase] = digest
    source_path, source_sha256 = _request_reference(
        document["source_spec_record"],
        label="persisted source specification record",
    )
    context = load_evidence_context(
        manifest_path=_absolute_path(
            Path(document["manifest_path"]),
            label="cutover manifest",
        ),
        approval_path=_absolute_path(
            Path(document["approval_path"]),
            label="cutover approval",
        ),
        approval_policy_path=_absolute_path(
            Path(document["approval_policy_path"]),
            label="approval policy",
        ),
        prior_evidence_paths=prior_paths,
    )
    manifest = context.manifest
    expected_identity = {
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "approval_policy_sha256": manifest["artifacts"][
            "human_approval_policy_sha256"
        ],
        "controller_plan_sha256": context.plan_sha256,
    }
    request_sha256 = _sha256(payload)
    expected_request_path = (
        context.output_root
        / PHASE
        / "requests"
        / f"phase-request.{request_sha256}.json"
    )
    try:
        source_payload = read_secure_bytes(
            source_path,
            label="persisted source specification record",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise StartupNormalizationPhaseError(
            "phase request source record is unavailable"
        ) from exc
    if (
        request_path != expected_request_path
        or any(
            document.get(field) != expected
            for field, expected in expected_identity.items()
        )
        or document["manifest_path"] != os.fspath(context.manifest_path)
        or document["approval_path"] != os.fspath(context.approval_path)
        or document["approval_policy_path"]
        != os.fspath(context.approval_policy_path)
        or prior_digests != dict(context.prior_digests)
        or any(
            prior_paths[phase] != context.prior_paths[phase]
            for phase in expected_prior
        )
        or _sha256(source_payload) != source_sha256
    ):
        raise StartupNormalizationPhaseError(
            "phase request differs from its trusted path/digest context"
        )
    source_spec = load_persisted_source_spec_record(
        source_path,
        context=context,
    )
    _source_document, source_binding_sha256 = _source_spec_binding(
        source_spec
    )
    if document["source_binding_sha256"] != source_binding_sha256:
        raise StartupNormalizationPhaseError(
            "phase request source binding differs"
        )
    return context, source_spec, request_sha256


def _hash_release_worker(
    running: Mapping[str, Any],
) -> str:
    requests = _validate_role_mapping(
        running.get("requests"),
        label="running receipt requests",
    )
    expected_root = Path(
        CONTROLLER._operation_release_root(  # noqa: SLF001
            running["receipt"]["operation_id"],
            running["receipt"]["release_sha"],
        )
    )
    paths = {
        Path(requests[role]["release_root"])
        / WORKER.WORKER_RELATIVE
        for role in ROLES
    }
    if paths != {expected_root / WORKER.WORKER_RELATIVE}:
        raise StartupNormalizationPhaseError(
            "normalization worker path differs from the exact release"
        )
    path = paths.pop()
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= WORKER.MAX_RELEASE_FILE_BYTES
        ):
            raise StartupNormalizationPhaseError(
                "normalization worker artifact is unsafe"
            )
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > WORKER.MAX_RELEASE_FILE_BYTES:
                raise StartupNormalizationPhaseError(
                    "normalization worker artifact is oversized"
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
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            observed != before.st_size
            or any(
                getattr(before, field) != getattr(after, field)
                for field in stable
            )
        ):
            raise StartupNormalizationPhaseError(
                "normalization worker artifact changed during hashing"
            )
        return digest.hexdigest()
    except StartupNormalizationPhaseError:
        raise
    except OSError as exc:
        raise StartupNormalizationPhaseError(
            "normalization worker artifact is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def build_source_production_plan(
    context: EvidenceContext,
    *,
    baseline: RunningBaselineSpec,
    worker_sha256: str,
) -> dict[str, Any]:
    manifest, _journal, _prior, output_root = (
        _validated_controller_context(
            context,
            required_position="any",
        )
    )
    if PHASE in _journal["completed_phases"]:
        raise StartupNormalizationPhaseError(
            "normalization sources cannot be produced after completion"
        )
    if not isinstance(baseline, RunningBaselineSpec):
        raise StartupNormalizationPhaseError(
            "running baseline specification is invalid"
        )
    body = {
        "schema": SOURCE_PRODUCTION_PLAN_SCHEMA,
        "status": "planned",
        "phase": PHASE,
        "operation": OPERATION,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "controller_plan_sha256": context.plan_sha256,
        "output_root": os.fspath(output_root),
        "running_receipt_path": os.fspath(
            _absolute_path(
                baseline.receipt_path,
                label="running baseline receipt",
            )
        ),
        "running_controller_challenge_sha256": _nonzero_sha256(
            baseline.controller_challenge_sha256,
            label="running baseline challenge",
        ),
        "running_aggregate_artifact_sha256": _nonzero_sha256(
            baseline.aggregate_artifact_sha256,
            label="running baseline artifact",
        ),
        "worker_sha256": _nonzero_sha256(
            worker_sha256,
            label="normalization worker",
        ),
        "roles": list(ROLES),
        "parallel_worker_count": len(ROLES),
        "worker_session_timeout_seconds": (
            WORKER_SESSION_TIMEOUT_SECONDS
        ),
        "fresh_stopped_inventory_required": True,
        "runtime_authorization_required": True,
        "controller_liveness_required": True,
        "journal_start_before_workers_required": True,
        "production_contacted": False,
        "journal_mutated": False,
    }
    digest = _sha256(_canonical_json(body))
    return {
        **body,
        "plan_sha256": digest,
        "required_confirmation": (
            f"produce-{PHASE}-sources:"
            f"{manifest['operation_id']}:{manifest['release_sha']}:{digest}"
        ),
    }


def _run_workers_concurrently(
    requests: Mapping[str, Mapping[str, Any]],
    *,
    worker_invoker: Any,
    authority_check: Any,
) -> dict[str, dict[str, Any]]:
    _validate_role_mapping(requests, label="normalization requests")
    if not callable(worker_invoker) or not callable(authority_check):
        raise StartupNormalizationPhaseError(
            "parallel normalization worker dependencies are unavailable"
        )
    cancellation = threading.Event()
    barrier = threading.Barrier(len(ROLES))

    def invoke(role: str) -> dict[str, Any]:
        try:
            barrier.wait(timeout=10.0)
        except threading.BrokenBarrierError as exc:
            raise StartupNormalizationPhaseError(
                "parallel normalization start barrier failed"
            ) from exc
        if cancellation.is_set():
            raise StartupNormalizationPhaseError(
                f"{role} normalization was cancelled before start"
            )
        return worker_invoker(
            role,
            requests[role],
            authority_check=authority_check,
            cancellation=cancellation,
        )

    futures: dict[str, Future[dict[str, Any]]] = {}
    with ThreadPoolExecutor(
        max_workers=len(ROLES),
        thread_name_prefix="shadow-normalization",
    ) as executor:
        for role in ROLES:
            futures[role] = executor.submit(invoke, role)
        done, _pending = wait(
            futures.values(),
            return_when=FIRST_EXCEPTION,
        )
        if any(future.exception() is not None for future in done):
            cancellation.set()
        wait(futures.values())
    failures = {
        role: future.exception()
        for role, future in futures.items()
        if future.exception() is not None
    }
    if failures:
        summary = ",".join(sorted(failures))
        raise StartupNormalizationPhaseError(
            "parallel normalization workers failed closed: " + summary
        )
    results = {
        role: futures[role].result() for role in ROLES
    }
    for role in ROLES:
        results[role] = _validate_worker_result(
            results[role],
            request=requests[role],
        )
    return results


def produce_persisted_sources(
    context: EvidenceContext,
    *,
    baseline: RunningBaselineSpec,
    confirm: str,
    control_fd: int,
    worker_invoker: Any,
    stopped_inventory_invoker: Any,
    now: datetime | None = None,
    clock: Any = None,
    journal_factory: Any = CONTROLLER.ProductionCutoverJournal,
    liveness_factory: Any = PREPARED.ControllerLiveness,
    signal_authority_factory: Any = PREPARED._signal_authority,  # noqa: SLF001
    authorization_verifier: Any = None,
    stopped_collector: Any = PREPARED.collect,
    worker_artifact_verifier: Any = _hash_release_worker,
) -> tuple[PersistedClosureSourceSpec, dict[str, Any]]:
    """Create exact worker/stopped sources while the phase is durably started."""

    manifest, _context_state, _prior, output_root = (
        _validated_controller_context(
            context,
            required_position="any",
        )
    )
    if PHASE in _context_state["completed_phases"]:
        raise StartupNormalizationPhaseError(
            "normalization source production is already completed"
        )
    if type(control_fd) is not int or control_fd < 0:
        raise StartupNormalizationPhaseError(
            "source production requires controller liveness"
        )
    active_authorization_verifier = (
        _verify_runtime_authorization
        if authorization_verifier is None
        else authorization_verifier
    )
    if not all(
        callable(item)
        for item in (
            worker_invoker,
            stopped_inventory_invoker,
            journal_factory,
            liveness_factory,
            signal_authority_factory,
            active_authorization_verifier,
            stopped_collector,
            worker_artifact_verifier,
        )
    ):
        raise StartupNormalizationPhaseError(
            "source production dependency is unavailable"
        )
    active_clock = clock or (lambda: datetime.now(timezone.utc))
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    try:
        running = (
            PREPARED
            .load_historical_running_prepared_clone_baseline_receipt(
                baseline.receipt_path,
                output_root=output_root,
                expected_campaign_id=manifest["campaign_id"],
                expected_operation_id=manifest["operation_id"],
                expected_release_sha=manifest["release_sha"],
                expected_release_tree_sha=manifest["release_tree_sha"],
                expected_controller_challenge_sha256=(
                    baseline.controller_challenge_sha256
                ),
                expected_aggregate_artifact_sha256=(
                    baseline.aggregate_artifact_sha256
                ),
                now=observed_now,
            )
        )
    except PREPARED.PreparedCloneInventoryError as exc:
        raise StartupNormalizationPhaseError(
            "running baseline cannot seed normalization"
        ) from exc
    worker_sha256 = worker_artifact_verifier(running)
    plan = build_source_production_plan(
        context,
        baseline=baseline,
        worker_sha256=worker_sha256,
    )
    if confirm != plan["required_confirmation"]:
        raise StartupNormalizationPhaseError(
            "source production confirmation differs"
        )
    try:
        journal = journal_factory(context.journal_path)
        state = journal.assert_bindings(**_journal_bindings(context))
    except CONTROLLER.CutoverContractError as exc:
        raise StartupNormalizationPhaseError(
            "source production journal binding differs"
        ) from exc
    live_context = replace(context, journal=state)
    _validated_controller_context(
        live_context,
        required_position="any",
    )
    journal_mutated = False
    request_paths: dict[str, Path] = {}
    request_digests: dict[str, str] = {}
    result_paths: dict[str, Path] = {}
    result_digests: dict[str, str] = {}
    worker_source_root = output_root / PHASE / "worker-sources"
    try:
        with (
            signal_authority_factory(),
            liveness_factory(control_fd) as liveness,
        ):
            liveness.check()
            active_authorization_verifier(live_context)
            if state["status"] == "active":
                state = journal.begin_phase(PHASE)
                journal_mutated = True
            state = journal.assert_bindings(
                **_journal_bindings(context)
            )
            live_context = replace(context, journal=state)
            _validated_controller_context(
                live_context,
                required_position="started",
            )

            def check_authority(
                _role: str | None = None,
                _checkpoint: str | None = None,
            ) -> None:
                liveness.check()
                active_authorization_verifier(live_context)
                current = journal.assert_bindings(
                    **_journal_bindings(context)
                )
                _validated_controller_context(
                    replace(context, journal=current),
                    required_position="started",
                )

            check_authority()
            issued_at = active_clock().astimezone(timezone.utc)
            expires_at = issued_at + WORKER_REQUEST_LIFETIME
            requests: dict[str, dict[str, Any]] = {}
            for role in ROLES:
                running_request = running["requests"][role]
                requests[role] = WORKER.build_request(
                    campaign_id=manifest["campaign_id"],
                    operation_id=manifest["operation_id"],
                    release_sha=manifest["release_sha"],
                    release_tree_sha=manifest["release_tree_sha"],
                    role=role,
                    worker_sha256=worker_sha256,
                    inventory_agent_sha256=running_request[
                        "agent_sha256"
                    ],
                    contract_worker_sha256=running_request[
                        "contract_worker_sha256"
                    ],
                    role_manifest_path=running_request[
                        "role_manifest_path"
                    ],
                    role_manifest_sha256=running_request[
                        "role_manifest_sha256"
                    ],
                    pre_inventory_request=running_request,
                    pre_inventory_response=running["responses"][role],
                    controller_challenge_sha256=(
                        INVENTORY.new_controller_challenge()
                    ),
                    issued_at=issued_at,
                    expires_at=expires_at,
                )
                request_path, request_digest = _persist_document(
                    worker_source_root,
                    prefix=f"normalization-request-{role}",
                    document=requests[role],
                )
                request_paths[role] = request_path
                request_digests[role] = request_digest
            if len(
                {
                    request["controller_challenge_sha256"]
                    for request in requests.values()
                }
            ) != len(ROLES):
                raise StartupNormalizationPhaseError(
                    "normalization worker challenges are not independent"
                )
            results = _run_workers_concurrently(
                requests,
                worker_invoker=worker_invoker,
                authority_check=check_authority,
            )
            for role in ROLES:
                result_path, result_digest = _persist_document(
                    worker_source_root,
                    prefix=f"normalization-result-{role}",
                    document=results[role],
                )
                result_paths[role] = result_path
                result_digests[role] = result_digest
            check_authority()
            stopped_inputs = PREPARED.CollectionInputs(
                campaign_id=manifest["campaign_id"],
                operation_id=manifest["operation_id"],
                release_sha=manifest["release_sha"],
                release_tree_sha=manifest["release_tree_sha"],
                agent_sha256=running["requests"]["bot_fi"][
                    "agent_sha256"
                ],
                roles={
                    role: PREPARED.RoleBinding(
                        contract_worker_sha256=running["requests"][role][
                            "contract_worker_sha256"
                        ],
                        role_manifest_sha256=running["requests"][role][
                            "role_manifest_sha256"
                        ],
                    )
                    for role in ROLES
                },
                expected_database_state="stopped",
                prior_requests=running["requests"],
                prior_responses=running["responses"],
            )
            stopped_plan = PREPARED.build_plan(stopped_inputs)
            duplicate_fd = os.dup(control_fd)
            try:
                stopped_aggregate, stopped_requests, stopped_responses = (
                    stopped_collector(
                        stopped_inputs,
                        invoke=stopped_inventory_invoker,
                        confirm=stopped_plan["required_confirmation"],
                        controller_liveness_fd=duplicate_fd,
                        authorization_check=lambda: check_authority(),
                        clock=active_clock,
                    )
                )
                duplicate_fd = -1
            finally:
                if duplicate_fd >= 0:
                    os.close(duplicate_fd)
            check_authority()
            stopped_publication = PREPARED.publish_receipt_create_only(
                stopped_aggregate,
                requests=stopped_requests,
                responses=stopped_responses,
                output_root=output_root,
                now=active_clock().astimezone(timezone.utc),
            )
            spec = PersistedClosureSourceSpec(
                running_receipt_path=baseline.receipt_path,
                running_controller_challenge_sha256=(
                    baseline.controller_challenge_sha256
                ),
                running_aggregate_artifact_sha256=(
                    baseline.aggregate_artifact_sha256
                ),
                stopped_receipt_path=Path(
                    stopped_publication["path"]
                ),
                stopped_controller_challenge_sha256=stopped_aggregate[
                    "controller_challenge_sha256"
                ],
                stopped_aggregate_artifact_sha256=stopped_publication[
                    "sha256"
                ],
                normalization_request_paths=request_paths,
                normalization_request_artifact_sha256=request_digests,
                normalization_result_paths=result_paths,
                normalization_result_artifact_sha256=result_digests,
            )
            source_document, source_binding_sha256 = (
                _source_spec_binding(spec)
            )
            record = {
                "schema": SOURCE_SPEC_RECORD_SCHEMA,
                "status": "persisted-create-only-readback-verified",
                "campaign_id": manifest["campaign_id"],
                "operation_id": manifest["operation_id"],
                "release_sha": manifest["release_sha"],
                "manifest_sha256": context.manifest_sha256,
                "controller_plan_sha256": context.plan_sha256,
                "source_binding_sha256": source_binding_sha256,
                "source_spec": source_document,
                "parallel_worker_count": len(ROLES),
                "worker_completion_skew_limit_seconds": (
                    MAX_WORKER_CAPTURE_SKEW_SECONDS
                ),
                "fresh_stopped_inventory": True,
                "journal_mutated": journal_mutated,
                "production_contacted": True,
            }
            record_path, record_sha256 = _persist_document(
                output_root / PHASE / "source-spec",
                prefix="persisted-source-spec",
                document=record,
            )
            PersistedClosureSourceLoader(
                live_context,
                spec,
                now=active_clock().astimezone(timezone.utc),
            ).load()
            check_authority()
    except (
        CONTROLLER.CutoverContractError,
        PREPARED.PreparedCloneInventoryError,
    ) as exc:
        raise StartupNormalizationPhaseError(
            "normalization source production failed closed"
        ) from exc
    return spec, {
        **plan,
        "schema": SOURCE_PRODUCTION_RESULT_SCHEMA,
        "status": "completed",
        "source_binding_sha256": source_binding_sha256,
        "source_spec_path": os.fspath(record_path),
        "source_spec_sha256": record_sha256,
        "worker_request_sha256": request_digests,
        "worker_result_sha256": result_digests,
        "stopped_receipt_path": stopped_publication["path"],
        "stopped_receipt_sha256": stopped_publication["sha256"],
        "parallel_worker_count": len(ROLES),
        "journal_mutated": journal_mutated,
        "production_contacted": True,
    }


def _build_role_validation(
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    role: str,
    request_sha256: str,
    observed_at: str,
) -> dict[str, Any]:
    document = {
        "schema": ROLE_VALIDATION_SCHEMA,
        "status": "validated-request",
        "request_sha256": request_sha256,
        "operation": OPERATION,
        "role": role,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "app_release_sha": manifest["release_sha"],
        "manifest_sha256": manifest_sha256,
        "approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "expected_host": manifest["topology"][role]["host"],
        "observed_host": manifest["topology"][role]["host"],
        "required_journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "business_write_policy": "forbid",
        "agent_artifact_sha256": manifest["artifacts"][
            "host_agent_sha256"
        ],
        "host_agent_contract_sha256": manifest["artifacts"][
            "host_agent_contract_sha256"
        ],
        "transport": manifest["topology"][role]["transport"],
        "observed_at": observed_at,
        "host_identity_observed": True,
        "execution_supported": False,
        "production_contacted": False,
    }
    if set(document) != VERIFY.HOST_AGENT_VALIDATION_FIELDS:
        raise StartupNormalizationPhaseError(
            f"{role} role validation fields differ"
        )
    return document


def publish_phase_evidence(
    context: EvidenceContext,
    *,
    closure: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Publish and locally verify immutable evidence without journal mutation."""

    validated_closure = validate_closure(closure)
    manifest, journal, prior_records, output_root = (
        _validated_evidence_context(
            context,
            closure=validated_closure,
        )
    )
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    captured_at = _timestamp(
        validated_closure["captured_at"],
        label="normalization closure capture",
    )
    if (
        captured_at > observed_now + timedelta(seconds=5)
        or observed_now - captured_at > VERIFY.MAX_EVIDENCE_AGE
    ):
        raise StartupNormalizationPhaseError(
            "normalization closure is stale for phase evidence"
        )
    phase_root = output_root / PHASE
    closure_path, closure_file_sha256 = _persist_document(
        phase_root / "closures",
        prefix="normalization-closure",
        document=validated_closure,
    )

    role_source_paths: dict[str, str] = {}
    role_source_sha256: dict[str, str] = {}
    role_validation_paths: dict[str, str] = {}
    role_validation_sha256: dict[str, str] = {}
    role_request_sha256: dict[str, str] = {}
    role_observed_at: dict[str, str] = {}
    for role in ROLES:
        role_source = validated_closure["roles"][role]
        source_path, source_digest = _persist_document(
            phase_root / "role-sources",
            prefix=f"role-source-{role}",
            document=role_source,
        )
        role_source_paths[role] = os.fspath(source_path)
        role_source_sha256[role] = source_digest
        request_binding = {
            "phase": PHASE,
            "operation": OPERATION,
            "role": role,
            "closure_file_sha256": closure_file_sha256,
            "role_source_sha256": source_digest,
            "normalization_request_sha256": role_source[
                "normalization_request_sha256"
            ],
            "normalization_result_sha256": role_source[
                "normalization_result_sha256"
            ],
            "stopped_request_sha256": role_source[
                "stopped_request_sha256"
            ],
            "stopped_response_sha256": role_source[
                "stopped_response_sha256"
            ],
        }
        request_digest = _sha256(_canonical_json(request_binding))
        role_request_sha256[role] = request_digest
        role_observed_at[role] = role_source["captured_at"]
        validation = _build_role_validation(
            manifest=manifest,
            manifest_sha256=context.manifest_sha256,
            role=role,
            request_sha256=request_digest,
            observed_at=role_source["captured_at"],
        )
        validation_path, validation_digest = _persist_document(
            phase_root / "role-validation",
            prefix=f"role-validation-{role}",
            document=validation,
        )
        role_validation_paths[role] = os.fspath(validation_path)
        role_validation_sha256[role] = validation_digest

    claim_source_paths: dict[str, str] = {}
    claim_source_sha256: dict[str, str] = {}
    for claim in CLAIMS:
        source = {
            "schema": CLAIM_SOURCE_SCHEMA,
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "phase": PHASE,
            "operation": OPERATION,
            "claim": claim,
            "value": validated_closure["claims"][claim],
            "observed_at": validated_closure["captured_at"],
            "status": "observed",
        }
        if set(source) != VERIFY.CLAIM_SOURCE_FIELDS:
            raise StartupNormalizationPhaseError(
                f"{claim} claim source fields differ"
            )
        source_path, source_digest = _persist_document(
            phase_root / "claim-sources",
            prefix=f"claim-{claim}",
            document=source,
        )
        claim_source_paths[claim] = os.fspath(source_path)
        claim_source_sha256[claim] = source_digest

    prior_rows = [
        {
            "phase": phase,
            "evidence_sha256": context.prior_digests[phase],
        }
        for phase in CONTROLLER.PHASES[
            : CONTROLLER.PHASES.index(PHASE)
        ]
    ]
    try:
        prior_claims = VERIFY._derive_prior_claim_rows(  # noqa: SLF001
            phase=PHASE,
            prior_digests=dict(context.prior_digests),
            prior_records=prior_records,
            campaign_id=manifest["campaign_id"],
            operation_id=manifest["operation_id"],
            release_sha=manifest["release_sha"],
            legacy_release_sha=manifest["legacy_release_sha"],
            manifest_sha256=context.manifest_sha256,
            plan_sha256=context.plan_sha256,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise StartupNormalizationPhaseError(
            "normalization prior claim bindings are invalid"
        ) from exc
    phase_input = {
        "manifest_sha256": context.manifest_sha256,
        "manifest_artifacts_sha256": _sha256(
            _canonical_json(manifest["artifacts"])
        ),
        "prior_phase_evidence": prior_rows,
        "prior_claim_bindings": prior_claims,
        "dynamic_claim_values": {},
        "claim_source_sha256": {
            name: claim_source_sha256[name]
            for name in sorted(claim_source_sha256)
        },
        "role_request_sha256": {
            role: role_request_sha256[role] for role in ROLES
        },
        "role_source_artifact_sha256": {
            role: role_validation_sha256[role] for role in ROLES
        },
        "role_observed_at": {
            role: role_observed_at[role] for role in ROLES
        },
    }
    evidence = {
        "schema": VERIFY.EVIDENCE_SCHEMA,
        "phase_evidence_schema_sha256": manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ],
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "manifest_artifact_bindings": manifest["artifacts"],
        "phase": PHASE,
        "operation": OPERATION,
        "journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "status": "passed",
        "captured_at": validated_closure["captured_at"],
        "business_write_observed": False,
        "prior_phase_evidence": prior_rows,
        "prior_phase_evidence_closure_sha256": _sha256(
            _canonical_json(prior_rows)
        ),
        "prior_claim_bindings": prior_claims,
        "phase_input_closure_sha256": _sha256(
            _canonical_json(phase_input)
        ),
        "role_attestations": [
            {
                "role": role,
                "expected_host": manifest["topology"][role]["host"],
                "operation": OPERATION,
                "request_sha256": role_request_sha256[role],
                "app_release_sha": manifest["release_sha"],
                "agent_artifact_sha256": manifest["artifacts"][
                    "host_agent_sha256"
                ],
                "host_identity_observed": True,
                "observed_at": role_observed_at[role],
                "status": "verified",
                "transport": manifest["topology"][role]["transport"],
                "source_artifact_sha256": role_validation_sha256[role],
            }
            for role in ROLES
        ],
        "claims": {
            claim: {
                "value": validated_closure["claims"][claim],
                "source_sha256": claim_source_sha256[claim],
            }
            for claim in CLAIMS
        },
    }
    if set(evidence) != VERIFY.EVIDENCE_FIELDS:
        raise StartupNormalizationPhaseError(
            "normalization phase evidence fields differ"
        )
    verification_arguments = {
        "expected_phase": PHASE,
        "expected_campaign_id": manifest["campaign_id"],
        "expected_operation_id": manifest["operation_id"],
        "expected_release_sha": manifest["release_sha"],
        "expected_legacy_release_sha": manifest["legacy_release_sha"],
        "expected_manifest_sha256": context.manifest_sha256,
        "expected_plan_sha256": context.plan_sha256,
        "expected_approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "expected_phase_evidence_schema_sha256": manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ],
        "expected_manifest_artifacts": dict(manifest["artifacts"]),
        "expected_role_request_sha256": role_request_sha256,
        "expected_role_source_artifact_sha256": (
            role_validation_sha256
        ),
        "expected_role_observed_at": role_observed_at,
        "expected_dynamic_claim_values": {},
        "expected_claim_source_sha256": claim_source_sha256,
        "expected_prior_phase_evidence_sha256": dict(
            context.prior_digests
        ),
        "prior_phase_evidence_records": prior_records,
        "now": observed_now,
    }
    try:
        VERIFY.verify_phase_evidence(evidence, **verification_arguments)
    except VERIFY.PhaseEvidenceError as exc:
        raise StartupNormalizationPhaseError(
            "normalization phase evidence failed local verification"
        ) from exc
    evidence_path, evidence_sha256 = _persist_document(
        phase_root / "evidence",
        prefix=PHASE,
        document=evidence,
    )
    try:
        readback, readback_sha256 = VERIFY.read_root_only_evidence(
            evidence_path
        )
        if readback != evidence or readback_sha256 != evidence_sha256:
            raise VERIFY.PhaseEvidenceError(
                "normalization evidence readback differs"
            )
        verification = VERIFY.verify_phase_evidence(
            readback,
            evidence_file_sha256=evidence_sha256,
            **verification_arguments,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise StartupNormalizationPhaseError(
            "persisted normalization evidence failed verification"
        ) from exc
    verification_path, verification_sha256 = _persist_document(
        phase_root / "local-verification",
        prefix="local-verification",
        document=verification,
    )
    publication = {
        "schema": PUBLICATION_SCHEMA,
        "status": "published-create-only-readback-verified",
        "phase": PHASE,
        "operation": OPERATION,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "closure_path": os.fspath(closure_path),
        "closure_file_sha256": closure_file_sha256,
        "role_source_paths": role_source_paths,
        "role_source_sha256": role_source_sha256,
        "role_validation_paths": role_validation_paths,
        "role_validation_sha256": role_validation_sha256,
        "claim_source_paths": claim_source_paths,
        "claim_source_sha256": claim_source_sha256,
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "local_verification_path": os.fspath(verification_path),
        "local_verification_sha256": verification_sha256,
        "journal_status": journal["status"],
        "journal_mutated": False,
        "production_contacted": False,
        "caller_truth_values_accepted": False,
        "create_only": True,
        "readback_verified": True,
    }
    if set(publication) != PUBLICATION_FIELDS:
        raise StartupNormalizationPhaseError(
            "normalization publication fields differ"
        )
    return publication


def build_plan(
    *,
    operation_id: str,
    release_sha: str,
    source_loader_available: bool,
    manifest_sha256: str | None = None,
    controller_plan_sha256: str | None = None,
    source_binding_sha256: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(operation_id, str)
        or not isinstance(release_sha, str)
        or CONTROLLER.SHA_RE.fullmatch(release_sha) is None
        or type(source_loader_available) is not bool
    ):
        raise StartupNormalizationPhaseError(
            "phase plan identity is invalid"
        )
    bindings = (
        manifest_sha256,
        controller_plan_sha256,
        source_binding_sha256,
    )
    if source_loader_available:
        for value, label in zip(
            bindings,
            ("manifest", "controller plan", "source binding"),
            strict=True,
        ):
            _nonzero_sha256(value, label=f"phase plan {label}")
    elif any(value is not None for value in bindings):
        raise StartupNormalizationPhaseError(
            "unavailable source plan cannot contain apply bindings"
        )
    body = {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "phase": PHASE,
        "operation": OPERATION,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "roles": list(ROLES),
        "checkpoints": list(CHECKPOINTS),
        "historically_validated_running_inventory_required": True,
        "fresh_stopped_inventory_required": True,
        "worker_invocation_count_per_role": 2,
        "public_claims": list(CLAIMS),
        "claims_derived_from_validated_inventory_only": True,
        "journal_begin_required_before_worker_commands": True,
        "journal_completion_requires_release_verifier_receipt": True,
        "external_controller_liveness_required": True,
        "runtime_authorization_required": True,
        "prepared_source_loader_available": source_loader_available,
        "manifest_sha256": manifest_sha256,
        "controller_plan_sha256": controller_plan_sha256,
        "source_binding_sha256": source_binding_sha256,
        "apply_supported": source_loader_available,
        "production_contacted": False,
        "journal_mutated": False,
    }
    digest = _sha256(_canonical_json(body))
    return {
        **body,
        "plan_sha256": digest,
        "required_confirmation": (
            f"run-{PHASE}:{operation_id}:{release_sha}:{digest}"
        ),
    }


def _journal_bindings(context: EvidenceContext) -> dict[str, str]:
    return {
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
    }


def _verify_runtime_authorization(context: EvidenceContext) -> None:
    try:
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            dict(context.manifest),
            approval_path=context.approval_path,
            approval_policy_path=context.approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise StartupNormalizationPhaseError(
            "production approval is invalid or expired"
        ) from exc


def _load_completed_phase(
    context: EvidenceContext,
    *,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    evidence_sha256 = state["phase_evidence_sha256"][PHASE]
    receipt_sha256 = state["phase_verification_sha256"][PHASE]
    evidence_path = (
        context.output_root
        / PHASE
        / "evidence"
        / f"{PHASE}.{evidence_sha256}.json"
    )
    receipt_path = (
        Path(context.manifest["deployment"]["controller_evidence_root"])
        / "verification"
        / f"{PHASE}.{receipt_sha256}.json"
    )
    try:
        evidence, observed_evidence_sha256 = (
            VERIFY.read_root_only_evidence(evidence_path)
        )
        receipt = read_secure_bytes(
            receipt_path,
            label="normalization release verification receipt",
            owner_uid=0,
            max_size=64 * 1024,
        )
        result = json.loads(
            receipt.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
        verification, canonical_receipt = (
            CONTROLLER._validate_phase_verification_result(  # noqa: SLF001
                result,
                phase=PHASE,
                manifest=dict(context.manifest),
                manifest_sha256=context.manifest_sha256,
                plan_sha256=context.plan_sha256,
            )
        )
    except (
        CONTROLLER.CutoverContractError,
        SecureFileError,
        VERIFY.PhaseEvidenceError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise StartupNormalizationPhaseError(
            "completed normalization evidence is unavailable or invalid"
        ) from exc
    if (
        observed_evidence_sha256 != evidence_sha256
        or evidence.get("phase") != PHASE
        or evidence.get("status") != "passed"
        or evidence.get("business_write_observed") is not False
        or canonical_receipt != receipt
        or _sha256(receipt) != receipt_sha256
        or verification.evidence_sha256 != evidence_sha256
        or verification.receipt_sha256 != receipt_sha256
    ):
        raise StartupNormalizationPhaseError(
            "completed normalization evidence differs from the journal"
        )
    return {
        "status": "completed-reused",
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "verification_receipt_path": os.fspath(receipt_path),
        "verification_receipt_sha256": receipt_sha256,
    }


def apply_persisted_phase(
    context: EvidenceContext,
    *,
    source_spec: PersistedClosureSourceSpec,
    confirm: str,
    control_fd: int,
    now: datetime | None = None,
    journal_factory: Any = CONTROLLER.ProductionCutoverJournal,
    liveness_factory: Any = PREPARED.ControllerLiveness,
    signal_authority_factory: Any = PREPARED._signal_authority,  # noqa: SLF001
    authorization_verifier: Any = _verify_runtime_authorization,
    release_verifier: Any = CONTROLLER._run_release_phase_verifier,  # noqa: SLF001
    receipt_persister: Any = CONTROLLER._persist_phase_verification_receipt,  # noqa: SLF001
    completed_reader: Any = _load_completed_phase,
) -> dict[str, Any]:
    """Resume one durably-started phase from immutable persisted sources."""

    manifest, _state, _prior, _output_root = (
        _validated_controller_context(
            context,
            required_position="any",
        )
    )
    _spec_document, source_binding_sha256 = _source_spec_binding(
        source_spec
    )
    plan = build_plan(
        operation_id=manifest["operation_id"],
        release_sha=manifest["release_sha"],
        source_loader_available=True,
        manifest_sha256=context.manifest_sha256,
        controller_plan_sha256=context.plan_sha256,
        source_binding_sha256=source_binding_sha256,
    )
    if confirm != plan["required_confirmation"]:
        raise StartupNormalizationPhaseError(
            "phase apply requires exact digest-bound confirmation"
        )
    if type(control_fd) is not int or control_fd < 0:
        raise StartupNormalizationPhaseError(
            "phase apply requires controller liveness"
        )
    if not all(
        callable(item)
        for item in (
            journal_factory,
            liveness_factory,
            signal_authority_factory,
            authorization_verifier,
            release_verifier,
            receipt_persister,
            completed_reader,
        )
    ):
        raise StartupNormalizationPhaseError(
            "phase apply dependency is unavailable"
        )
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    try:
        journal = journal_factory(context.journal_path)
        state = journal.assert_bindings(**_journal_bindings(context))
    except CONTROLLER.CutoverContractError as exc:
        raise StartupNormalizationPhaseError(
            "normalization journal binding differs"
        ) from exc
    live_context = replace(context, journal=state)
    _validated_controller_context(
        live_context,
        required_position="any",
    )
    if PHASE in state["completed_phases"]:
        reused = completed_reader(live_context, state=state)
        return {
            **plan,
            **reused,
            "journal_mutated": False,
            "production_contacted": False,
        }
    _validated_controller_context(
        live_context,
        required_position="started",
    )

    try:
        with (
            signal_authority_factory(),
            liveness_factory(control_fd) as liveness,
        ):
            liveness.check()
            authorization_verifier(live_context)
            state = journal.assert_bindings(**_journal_bindings(context))
            live_context = replace(context, journal=state)
            _validated_controller_context(
                live_context,
                required_position="started",
            )
            liveness.check()
            authorization_verifier(live_context)
            sources = PersistedClosureSourceLoader(
                live_context,
                source_spec,
                now=observed_now,
            ).load()
            closure = validate_normalization_closure(
                sources,
                now=observed_now,
            )
            liveness.check()
            authorization_verifier(live_context)
            publication = publish_phase_evidence(
                live_context,
                closure=closure,
                now=observed_now,
            )
            liveness.check()
            authorization_verifier(live_context)
            verification, receipt = release_verifier(
                phase=PHASE,
                manifest=dict(manifest),
                manifest_sha256=context.manifest_sha256,
                plan=dict(context.plan),
                manifest_path=context.manifest_path,
                approval_path=context.approval_path,
                approval_policy_path=context.approval_policy_path,
                evidence_path=Path(
                    publication["phase_evidence_path"]
                ),
                role_validation=[
                    f"{role}={publication['role_validation_paths'][role]}"
                    for role in ROLES
                ],
                claim_source=[
                    f"{claim}={publication['claim_source_paths'][claim]}"
                    for claim in CLAIMS
                ],
                prior_phase_evidence=[
                    f"{phase}={context.prior_paths[phase]}"
                    for phase in CONTROLLER.PHASES[
                        : CONTROLLER.PHASES.index(PHASE)
                    ]
                ],
            )
            if (
                not isinstance(
                    verification,
                    CONTROLLER.VerifiedPhaseCompletion,
                )
                or verification.phase != PHASE
                or verification.evidence_sha256
                != publication["phase_evidence_sha256"]
            ):
                raise StartupNormalizationPhaseError(
                    "release verifier completion differs from publication"
                )
            liveness.check()
            authorization_verifier(live_context)
            receipt_path = receipt_persister(
                token=verification,
                receipt=receipt,
                evidence_root=Path(
                    manifest["deployment"]["controller_evidence_root"]
                ),
            )
            completed = journal.complete_phase(
                PHASE,
                verification=verification,
            )
            liveness.check()
    except (
        CONTROLLER.CutoverContractError,
        PREPARED.PreparedCloneInventoryError,
    ) as exc:
        raise StartupNormalizationPhaseError(
            "normalization phase apply failed closed"
        ) from exc
    final_context = replace(context, journal=completed)
    _validated_controller_context(
        final_context,
        required_position="completed",
    )
    if (
        completed["phase_evidence_sha256"][PHASE]
        != verification.evidence_sha256
        or completed["phase_verification_sha256"][PHASE]
        != verification.receipt_sha256
    ):
        raise StartupNormalizationPhaseError(
            "normalization journal completion differs"
        )
    return {
        **plan,
        "status": "completed",
        "publication": publication,
        "phase_evidence_path": publication["phase_evidence_path"],
        "phase_evidence_sha256": verification.evidence_sha256,
        "verification_receipt_path": os.fspath(receipt_path),
        "verification_receipt_sha256": verification.receipt_sha256,
        "journal_status": completed["status"],
        "journal_mutated": True,
        "production_contacted": False,
    }


def execute(
    *,
    operation_id: str,
    release_sha: str,
    apply: bool = False,
    confirm: str | None = None,
    context: EvidenceContext | None = None,
    source_spec: PersistedClosureSourceSpec | None = None,
    control_fd: int | None = None,
) -> dict[str, Any]:
    """Plan the phase or resume it from exact persisted source artifacts."""

    available = (
        isinstance(context, EvidenceContext)
        and isinstance(source_spec, PersistedClosureSourceSpec)
    )
    if available:
        _manifest, _journal, _prior, _root = (
            _validated_controller_context(
                context,
                required_position="any",
            )
        )
        _source_document, source_binding_sha256 = (
            _source_spec_binding(source_spec)
        )
        manifest_sha256 = context.manifest_sha256
        controller_plan_sha256 = context.plan_sha256
    else:
        source_binding_sha256 = None
        manifest_sha256 = None
        controller_plan_sha256 = None
    plan = build_plan(
        operation_id=operation_id,
        release_sha=release_sha,
        source_loader_available=available,
        manifest_sha256=manifest_sha256,
        controller_plan_sha256=controller_plan_sha256,
        source_binding_sha256=source_binding_sha256,
    )
    if not apply:
        if confirm is not None or control_fd is not None:
            raise StartupNormalizationPhaseError(
                "phase plan does not accept confirmation or liveness"
            )
        return plan
    if (
        not available
        or context is None
        or source_spec is None
        or control_fd is None
    ):
        raise StartupNormalizationPhaseError(
            "phase apply requires trusted persisted sources and liveness"
        )
    if (
        operation_id != context.manifest["operation_id"]
        or release_sha != context.manifest["release_sha"]
    ):
        raise StartupNormalizationPhaseError(
            "phase apply identity differs from the trusted context"
        )
    return apply_persisted_phase(
        context,
        source_spec=source_spec,
        confirm=confirm or "",
        control_fd=control_fd,
    )


def _named_path_arguments(values: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if (
            not isinstance(value, str)
            or value.count("=") != 1
            or not value.split("=", 1)[0]
            or not value.split("=", 1)[1]
        ):
            raise StartupNormalizationPhaseError(
                "--prior-evidence must be PHASE=/absolute/path"
            )
        phase, raw_path = value.split("=", 1)
        if phase in result:
            raise StartupNormalizationPhaseError(
                "prior evidence phase is duplicated"
            )
        result[phase] = _absolute_path(
            Path(raw_path),
            label=f"{phase} prior evidence",
        )
    return result


def _cli_context(args: argparse.Namespace) -> EvidenceContext:
    if (
        args.manifest is None
        or args.approval is None
        or args.approval_policy is None
    ):
        raise StartupNormalizationPhaseError(
            "trusted actions require --manifest, --approval, and "
            "--approval-policy"
        )
    return load_evidence_context(
        manifest_path=args.manifest,
        approval_path=args.approval,
        approval_policy_path=args.approval_policy,
        prior_evidence_paths=_named_path_arguments(
            args.prior_evidence
        ),
    )


def _cli_baseline(args: argparse.Namespace) -> RunningBaselineSpec:
    if (
        args.running_receipt is None
        or args.running_controller_challenge_sha256 is None
        or args.running_aggregate_artifact_sha256 is None
    ):
        raise StartupNormalizationPhaseError(
            "source production requires the exact running receipt, "
            "challenge, and aggregate artifact digest"
        )
    return RunningBaselineSpec(
        receipt_path=args.running_receipt,
        controller_challenge_sha256=(
            args.running_controller_challenge_sha256
        ),
        aggregate_artifact_sha256=(
            args.running_aggregate_artifact_sha256
        ),
    )


def _cli_worker_sha256(
    context: EvidenceContext,
    baseline: RunningBaselineSpec,
) -> str:
    manifest, _journal, _prior, output_root = (
        _validated_controller_context(
            context,
            required_position="any",
        )
    )
    try:
        running = (
            PREPARED
            .load_historical_running_prepared_clone_baseline_receipt(
                baseline.receipt_path,
                output_root=output_root,
                expected_campaign_id=manifest["campaign_id"],
                expected_operation_id=manifest["operation_id"],
                expected_release_sha=manifest["release_sha"],
                expected_release_tree_sha=manifest[
                    "release_tree_sha"
                ],
                expected_controller_challenge_sha256=(
                    baseline.controller_challenge_sha256
                ),
                expected_aggregate_artifact_sha256=(
                    baseline.aggregate_artifact_sha256
                ),
                now=datetime.now(timezone.utc),
            )
        )
    except PREPARED.PreparedCloneInventoryError as exc:
        raise StartupNormalizationPhaseError(
            "running baseline cannot be loaded for source planning"
        ) from exc
    return _hash_release_worker(running)


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path)
    parser.add_argument(
        "--action",
        choices=(
            "phase-plan",
            "produce-plan",
            "produce-sources",
            "apply",
        ),
        default="phase-plan",
    )
    parser.add_argument("--operation-id")
    parser.add_argument("--release-sha")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument(
        "--control-fd",
        "--controller-liveness-fd",
        dest="control_fd",
        type=int,
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--approval-policy", type=Path)
    parser.add_argument(
        "--prior-evidence",
        action="append",
        default=[],
    )
    parser.add_argument("--source-spec-record", type=Path)
    parser.add_argument("--running-receipt", type=Path)
    parser.add_argument(
        "--running-controller-challenge-sha256"
    )
    parser.add_argument("--running-aggregate-artifact-sha256")
    parser.add_argument("--ssh-identity", type=Path)
    parser.add_argument("--ssh-identity-sha256")
    parser.add_argument("--known-hosts", type=Path)
    parser.add_argument("--known-hosts-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _cli_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    action = args.action
    try:
        if args.apply:
            if action != "phase-plan":
                raise StartupNormalizationPhaseError(
                    "--apply cannot be combined with --action"
                )
            action = "apply"
        if args.request is not None:
            forbidden_request_arguments = (
                args.operation_id,
                args.release_sha,
                args.manifest,
                args.approval,
                args.approval_policy,
                args.source_spec_record,
                args.running_receipt,
                args.running_controller_challenge_sha256,
                args.running_aggregate_artifact_sha256,
                args.ssh_identity,
                args.ssh_identity_sha256,
                args.known_hosts,
                args.known_hosts_sha256,
            )
            if (
                action not in {"phase-plan", "apply"}
                or args.prior_evidence
                or any(
                    value is not None
                    for value in forbidden_request_arguments
                )
            ):
                raise StartupNormalizationPhaseError(
                    "--request is the sole trusted path input"
                )
            context, source_spec, _request_sha256 = (
                load_phase_request(args.request)
            )
            result = execute(
                operation_id=context.manifest["operation_id"],
                release_sha=context.manifest["release_sha"],
                apply=action == "apply",
                confirm=args.confirm,
                context=context,
                source_spec=source_spec,
                control_fd=args.control_fd,
            )
        elif action == "phase-plan" and args.manifest is None:
            if not args.operation_id or not args.release_sha:
                raise StartupNormalizationPhaseError(
                    "unbound phase plan requires --operation-id and "
                    "--release-sha"
                )
            if (
                args.confirm is not None
                or args.control_fd is not None
                or args.source_spec_record is not None
            ):
                raise StartupNormalizationPhaseError(
                    "unbound phase plan does not accept apply inputs"
                )
            result = execute(
                operation_id=args.operation_id,
                release_sha=args.release_sha,
            )
        else:
            context = _cli_context(args)
            operation_id = (
                context.manifest["operation_id"]
                if args.operation_id is None
                else args.operation_id
            )
            release_sha = (
                context.manifest["release_sha"]
                if args.release_sha is None
                else args.release_sha
            )
            if (
                operation_id != context.manifest["operation_id"]
                or release_sha != context.manifest["release_sha"]
            ):
                raise StartupNormalizationPhaseError(
                    "CLI identity differs from the trusted manifest"
                )
            if action == "phase-plan":
                source_spec = (
                    None
                    if args.source_spec_record is None
                    else load_persisted_source_spec_record(
                        args.source_spec_record,
                        context=context,
                    )
                )
                result = execute(
                    operation_id=operation_id,
                    release_sha=release_sha,
                    context=context,
                    source_spec=source_spec,
                )
            elif action in {"produce-plan", "produce-sources"}:
                baseline = _cli_baseline(args)
                worker_sha256 = _cli_worker_sha256(
                    context,
                    baseline,
                )
                source_plan = build_source_production_plan(
                    context,
                    baseline=baseline,
                    worker_sha256=worker_sha256,
                )
                if action == "produce-plan":
                    if (
                        args.confirm is not None
                        or args.control_fd is not None
                    ):
                        raise StartupNormalizationPhaseError(
                            "source plan does not accept confirmation or "
                            "liveness"
                        )
                    result = source_plan
                else:
                    if (
                        args.confirm is None
                        or args.control_fd is None
                        or args.ssh_identity is None
                        or args.ssh_identity_sha256 is None
                        or args.known_hosts is None
                        or args.known_hosts_sha256 is None
                    ):
                        raise StartupNormalizationPhaseError(
                            "source apply requires confirmation, liveness, "
                            "and exact SSH trust inputs"
                        )
                    try:
                        worker_invoker = ProductionWorkerInvoker(
                            ssh_identity=args.ssh_identity,
                            ssh_identity_sha256=(
                                args.ssh_identity_sha256
                            ),
                            known_hosts=args.known_hosts,
                            known_hosts_sha256=args.known_hosts_sha256,
                        )
                        stopped_invoker = PREPARED.ProductionInvoker(
                            ssh_identity=args.ssh_identity,
                            ssh_identity_sha256=(
                                args.ssh_identity_sha256
                            ),
                            known_hosts=args.known_hosts,
                            known_hosts_sha256=args.known_hosts_sha256,
                        )
                    except PREPARED.PreparedCloneInventoryError as exc:
                        raise StartupNormalizationPhaseError(
                            "production invoker trust binding is invalid"
                        ) from exc
                    _spec, result = produce_persisted_sources(
                        context,
                        baseline=baseline,
                        confirm=args.confirm,
                        control_fd=args.control_fd,
                        worker_invoker=worker_invoker,
                        stopped_inventory_invoker=stopped_invoker,
                    )
            else:
                if (
                    args.source_spec_record is None
                    or args.confirm is None
                    or args.control_fd is None
                ):
                    raise StartupNormalizationPhaseError(
                        "phase apply requires source record, confirmation, "
                        "and liveness"
                    )
                source_spec = load_persisted_source_spec_record(
                    args.source_spec_record,
                    context=context,
                )
                result = execute(
                    operation_id=operation_id,
                    release_sha=release_sha,
                    apply=True,
                    confirm=args.confirm,
                    context=context,
                    source_spec=source_spec,
                    control_fd=args.control_fd,
                )
        status = 0
    except (
        StartupNormalizationPhaseError,
        PREPARED.PreparedCloneInventoryError,
        OSError,
        TypeError,
        ValueError,
    ):
        result = {
            "schema": RESULT_SCHEMA,
            "status": "blocked",
            "error": "startup-normalization phase failed closed",
            "action": action,
            # Conservative failure reporting: a live action may have crossed
            # its boundary before the local controller observed the error.
            "production_contacted": action == "produce-sources",
            "journal_mutated": action in {"produce-sources", "apply"},
        }
        status = 1
    sys.stdout.buffer.write(_canonical_json(result) + b"\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
