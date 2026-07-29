#!/usr/bin/env python3
"""Prepare one frozen-final production-shadow database without serving traffic.

The command line is plan-only.  Mutating execution is available only through
``execute(..., apply=True)`` with a controller-owned live-authority callback.
The worker has no SSH, Object Storage, Nginx, ``current``, or legacy-service
operation.  Its only subprocess surface is the local Docker Unix socket and
the exact generation-installed prepare Compose.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import UUID


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [
    entry
    for entry in sys.path
    if Path(entry or os.getcwd()).resolve() != REPO_ROOT
]
sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    orchestrate_production_shadow_frozen_final_restore as RESTORE_ORCHESTRATOR,
)
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import activate_three_site_database_fencing as WEB_GRANTS  # noqa: E402
from scripts import provision_bot_database_roles as BOT_GRANTS  # noqa: E402
from migrations.versions import (  # noqa: E402
    c431d2e3f5a6_reconcile_integrated_database_policy as C431_POLICY,
)
from scripts import (  # noqa: E402
    production_shadow_frozen_final_restore_worker as RESTORE,
)
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402
from scripts import wa_ir_production_operation as WA_OPERATION  # noqa: E402


ProductionOperationError = WA_OPERATION.ProductionOperationError
_concurrent_index_names = WA_OPERATION._concurrent_index_names
_load_migration_graph = WA_OPERATION._load_migration_graph
_migration_corridor = WA_OPERATION._migration_corridor

TRUSTED_IMPORTED_MODULE_PATHS = {
    "restore-orchestrator": (
        RESTORE_ORCHESTRATOR,
        "scripts/orchestrate_production_shadow_frozen_final_restore.py",
    ),
    "controller": (
        CONTROLLER,
        "scripts/production_shadow_cutover_controller.py",
    ),
    "web-grants": (
        WEB_GRANTS,
        "scripts/activate_three_site_database_fencing.py",
    ),
    "bot-grants": (
        BOT_GRANTS,
        "scripts/provision_bot_database_roles.py",
    ),
    "projection-policy": (
        C431_POLICY,
        "migrations/versions/"
        "c431d2e3f5a6_reconcile_integrated_database_policy.py",
    ),
    "restore-worker": (
        RESTORE,
        "scripts/production_shadow_frozen_final_restore_worker.py",
    ),
    "phase-verifier": (
        VERIFY,
        "scripts/verify_production_shadow_phase_evidence.py",
    ),
    "migration-policy": (
        WA_OPERATION,
        "scripts/wa_ir_production_operation.py",
    ),
}
TRUSTED_IMPORTED_MODULE_SHA256: dict[str, str] = {}
_running_source_metadata = Path(__file__).lstat()
for _label, (_module, _relative_path) in (
    TRUSTED_IMPORTED_MODULE_PATHS.items()
):
    _expected_path = REPO_ROOT / _relative_path
    _module_path = Path(str(getattr(_module, "__file__", "")))
    _module_spec = getattr(_module, "__spec__", None)
    _module_origin = Path(
        str(getattr(_module_spec, "origin", ""))
    )
    try:
        _metadata = _module_path.lstat()
        _payload = _module_path.read_bytes()
    except OSError as _exc:
        raise RuntimeError(
            f"trusted {_label} module is unavailable"
        ) from _exc
    if (
        _module_path != _expected_path
        or _module_origin != _expected_path
        or _module_path.is_symlink()
        or not stat.S_ISREG(_metadata.st_mode)
        or _metadata.st_uid != _running_source_metadata.st_uid
        or _metadata.st_gid != _running_source_metadata.st_gid
        or stat.S_IMODE(_metadata.st_mode) & 0o022
        or _metadata.st_nlink != 1
    ):
        raise RuntimeError(
            f"trusted {_label} module origin is invalid"
        )
    TRUSTED_IMPORTED_MODULE_SHA256[_relative_path] = hashlib.sha256(
        _payload
    ).hexdigest()
del (
    _expected_path,
    _label,
    _metadata,
    _module,
    _module_origin,
    _module_path,
    _module_spec,
    _payload,
    _relative_path,
    _running_source_metadata,
)


REQUEST_SCHEMA = "production-shadow-frozen-prepare-request-v1"
AUTHORITY_CHALLENGE_SCHEMA = (
    "production-shadow-frozen-prepare-authority-challenge-v2"
)
AUTHORITY_RESPONSE_SCHEMA = (
    "production-shadow-frozen-prepare-authority-response-v2"
)
JOURNAL_EVENT_SCHEMA = (
    "production-shadow-frozen-prepare-journal-event-v1"
)
EVIDENCE_SCHEMA = "production-shadow-frozen-prepare-evidence-v2"
RESULT_SCHEMA = "production-shadow-frozen-prepare-result-v2"

PHASES = (
    "shadow_roles_pre_migration",
    "shadow_migrate",
    "shadow_roles_post_migration",
    "shadow_fence",
)
_CONTROLLER_PHASES = {
    spec.phase: spec for spec in CONTROLLER.PHASE_SPECS
}
PHASE_OPERATIONS = {
    phase: _CONTROLLER_PHASES[phase].operation
    for phase in PHASES
}
PHASE_ROLES = {
    phase: tuple(_CONTROLLER_PHASES[phase].roles)
    for phase in PHASES
}
ROLE_PATHS = dict(RESTORE.ROLE_PATHS)
ROLE_PREFIXES = dict(RESTORE.ROLE_PREFIXES)
ROLES = tuple(RESTORE.ROLE_NAMES)

STEP_SERVICES: dict[tuple[str, str], tuple[tuple[str, str | None, int], ...]] = {
    ("shadow_roles_pre_migration", "webapp_fi"): (
        ("roles-pre", "webapp_fi_db_roles", 600),
    ),
    ("shadow_roles_pre_migration", "webapp_ir"): (
        ("roles-pre", "webapp_ir_db_roles", 600),
    ),
    ("shadow_migrate", "bot_fi"): (
        ("migrate", "bot_fi_migration", 3600),
    ),
    ("shadow_migrate", "webapp_fi"): (
        ("migrate", "webapp_fi_migration", 3600),
    ),
    ("shadow_migrate", "webapp_ir"): (
        ("migrate", "webapp_ir_migration", 3600),
    ),
    ("shadow_roles_post_migration", "bot_fi"): (
        ("roles-post", "bot_fi_db_roles", 900),
    ),
    ("shadow_roles_post_migration", "webapp_fi"): (
        ("roles-post", "webapp_fi_db_roles_post_migration", 900),
    ),
    ("shadow_roles_post_migration", "webapp_ir"): (
        ("roles-post", "webapp_ir_db_roles_post_migration", 900),
    ),
    ("shadow_fence", "bot_fi"): (
        ("database-fence", "bot_fi_db_fencing", 900),
    ),
    ("shadow_fence", "webapp_fi"): (
        ("database-fence", "webapp_fi_db_fencing", 900),
    ),
    ("shadow_fence", "webapp_ir"): (
        ("database-fence", "webapp_ir_db_fencing", 900),
        ("writer-fence", "webapp_ir_writer_fence", 900),
    ),
}

PHASE_EXECUTION_BLOCKERS: dict[tuple[str, str], str] = {}


def _webapp_phase_command(
    site: str,
    *,
    phase: str,
    confirmation: str,
) -> tuple[str, ...]:
    return (
        "python",
        "scripts/activate_three_site_database_fencing.py",
        "--phase",
        phase,
        "--site",
        site,
        "--application-role",
        f"{site}_app",
        "--projection-role",
        f"{site}_projection",
        "--receiver-role",
        f"{site}_receiver",
        "--delivery-role",
        f"{site}_delivery",
        "--blob-role",
        f"{site}_blob",
        "--effect-role",
        f"{site}_effect",
        "--control-role",
        f"{site}_control",
        "--observer-role",
        f"{site}_observer",
        "--operator",
        "production-shadow-compose",
        "--apply",
        "--confirm",
        confirmation,
    )

PREPARE_SERVICE_COMMANDS = {
    "bot_fi_migration": ("python", "manage.py"),
    "webapp_fi_db_roles": (
        "python",
        "scripts/provision_three_site_database_roles.py",
        "--role-prefix",
        "webapp_fi",
    ),
    "webapp_fi_migration": ("python", "manage.py"),
    "webapp_ir_db_roles": (
        "python",
        "scripts/provision_three_site_database_roles.py",
        "--role-prefix",
        "webapp_ir",
    ),
    "webapp_ir_migration": ("python", "manage.py"),
    "bot_fi_db_roles": (
        "python",
        "scripts/provision_bot_database_roles.py",
        "--phase",
        "roles-grants",
        "--role-prefix",
        "bot_fi",
        "--apply",
        "--confirm",
        "APPLY-BOT-DATABASE-ROLE-GRANTS",
    ),
    "bot_fi_db_fencing": (
        "python",
        "scripts/provision_bot_database_roles.py",
        "--phase",
        "fence",
        "--role-prefix",
        "bot_fi",
        "--apply",
        "--confirm",
        "ENABLE-BOT-DATABASE-FENCING",
    ),
    "webapp_fi_db_roles_post_migration": _webapp_phase_command(
        "webapp_fi",
        phase="grants",
        confirmation="APPLY-THREE-SITE-DATABASE-GRANTS",
    ),
    "webapp_fi_db_fencing": _webapp_phase_command(
        "webapp_fi",
        phase="fence",
        confirmation="ENABLE-THREE-SITE-DATABASE-FENCING",
    ),
    "webapp_ir_db_roles_post_migration": _webapp_phase_command(
        "webapp_ir",
        phase="grants",
        confirmation="APPLY-THREE-SITE-DATABASE-GRANTS",
    ),
    "webapp_ir_db_fencing": _webapp_phase_command(
        "webapp_ir",
        phase="fence",
        confirmation="ENABLE-THREE-SITE-DATABASE-FENCING",
    ),
    "webapp_ir_writer_fence": (
        "python",
        "scripts/manage_webapp_writer.py",
        "fence",
        "--expected-epoch",
        "1",
        "--expected-active-site",
        "webapp_fi",
        "--operator",
        # Compose expands this immutable operation binding at render time.
        "__OPERATION_BOUND_OPERATOR__",
        "--reason",
        "initialize WebApp-IR as an operation-bound locally fenced standby",
        "--apply",
        "--confirm",
        "writer:fence:webapp_ir:1:1",
    ),
}


def _prepare_service_command(
    service: str,
    *,
    operation_id: str,
) -> tuple[str, ...] | None:
    command = PREPARE_SERVICE_COMMANDS.get(service)
    if command is None:
        return None
    return tuple(
        (
            f"production-shadow:{operation_id}"
            if token == "__OPERATION_BOUND_OPERATOR__"
            else token
        )
        for token in command
    )


EXPECTED_RUNTIME_ROLES = {
    "bot_fi": (
        "bot_fi_app",
        "bot_fi_delivery",
        "bot_fi_observer",
        "bot_fi_projection",
        "bot_fi_receiver",
    ),
    "webapp_fi": (
        "webapp_fi_app",
        "webapp_fi_blob",
        "webapp_fi_control",
        "webapp_fi_delivery",
        "webapp_fi_effect",
        "webapp_fi_observer",
        "webapp_fi_projection",
        "webapp_fi_receiver",
    ),
    "webapp_ir": (
        "webapp_ir_app",
        "webapp_ir_blob",
        "webapp_ir_control",
        "webapp_ir_delivery",
        "webapp_ir_effect",
        "webapp_ir_observer",
        "webapp_ir_projection",
        "webapp_ir_receiver",
    ),
}

# Exact target-release union of the tables fenced by migrations
# d3e8f9a0b1c2, e5a0b1c2d3e4, and c431d2e3f5a6.  The worker is itself
# release/tree/hash bound, so a migration that changes this policy must update
# this closed inventory in the same immutable release.
EXPECTED_WRITER_TRIGGER_TABLES = (
    "accountant_relations",
    "admin_broadcast_messages",
    "admin_market_messages",
    "chat_files",
    "chat_members",
    "chats",
    "commodities",
    "commodity_aliases",
    "conversations",
    "customer_relations",
    "dr_blob_deliveries",
    "dr_blob_manifests",
    "dr_blob_receipts",
    "dr_conflict_quarantine",
    "dr_destination_cursors",
    "dr_durability_state",
    "dr_effect_fanouts",
    "dr_effect_outbox",
    "dr_event_deliveries",
    "dr_event_receipts",
    "dr_events",
    "dr_file_intents",
    "dr_producer_cursors",
    "dr_projection_versions",
    "dr_recovery_manifests",
    "dr_replay_nonces",
    "dr_stream_checkpoints",
    "invitation_identity_reservations",
    "invitation_sms_deliveries",
    "invitations",
    "market_channel_notice_receipts",
    "market_runtime_state",
    "market_schedule_overrides",
    "messages",
    "notifications",
    "offer_publication_states",
    "offer_requests",
    "offers",
    "push_subscriptions",
    "session_login_requests",
    "single_session_recovery_admin_targets",
    "single_session_recovery_requests",
    "sync_apply_watermarks",
    "sync_blocks",
    "telegram_admin_broadcast_receipts",
    "telegram_admin_broadcasts",
    "telegram_channel_membership_sagas",
    "telegram_delivery_feeder_states",
    "telegram_delivery_jobs",
    "telegram_delivery_provider_outcomes",
    "telegram_delivery_reconciliation_evidence",
    "telegram_delivery_resume_operations",
    "telegram_delivery_runtime_gates",
    "telegram_interaction_anchor_states",
    "telegram_link_tokens",
    "telegram_notification_outbox",
    "telegram_registration_command_receipts",
    "telegram_registration_intents",
    "telegram_scheduled_operations",
    "trade_delivery_receipts",
    "trades",
    "trading_settings",
    "upload_batches",
    "upload_sessions",
    "user_blocks",
    "user_counter_event_receipts",
    "user_notification_preferences",
    "user_sessions",
    "users",
)

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_COMPLETION_BYTES = RESTORE_ORCHESTRATOR.MAX_COMPLETION_BYTES
MAX_EVENT_COUNT = 64
MAX_ATTEMPTS_PER_STEP = 3
MAX_SQL_ROWS = 100_000
DEFAULT_MAX_SQL_LINE_BYTES = 8192
FINGERPRINT_MAX_SQL_LINE_BYTES = 1024 * 1024
CANCELLATION_QUIESCENCE_SECONDS = 2.0
CANCELLATION_MAX_WAIT_SECONDS = 65.0
CANCELLATION_POLL_SECONDS = 0.1
SQL_CLEANUP_RESERVE_SECONDS = CANCELLATION_MAX_WAIT_SECONDS
SQL_LOCK_TIMEOUT_MS = 5_000
MAX_SQL_INTENTS = 512
SQL_INTENT_SCHEMA = "production-shadow-frozen-prepare-sql-intent-v1"
SQL_INTENT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "phase",
        "request_sha256",
        "restore_generation_sha256",
        "step",
        "attempt",
        "started_event_sha256",
        "stage",
        "sql_kind",
        "sql",
        "sql_sha256",
        "sql_bytes",
        "statement_timeout_ms",
        "lock_timeout_ms",
        "transaction_read_only",
        "reviewed_index",
        "command",
        "intent_sha256",
    }
)
SQL_STAGES = frozenset(
    {
        "pre-start-observe",
        "recovery-observe",
        "post-run-observe",
        "final-readback",
        "step-execution",
    }
)
SQL_KINDS = frozenset({"read-only", "drop-reviewed-index"})
SCHEMA_FINGERPRINT_ALGORITHM = "postgres-public-catalog-jsonb-sha256-v4"
PHASE_CLOSURE_STEP = "phase-closure"
ZERO_SHA256 = "0" * 64
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-z_]{1,64}$")
ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:/+-]{1,512}$")
RUNNING_WORKER_PATH = Path(__file__).resolve()

REQUEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "phase",
        "operation",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_path",
        "controller_manifest_sha256",
        "plan_sha256",
        "role_manifest_path",
        "role_manifest_sha256",
        "restore_completion_path",
        "restore_completion_sha256",
        "restore_phase_evidence_path",
        "restore_phase_evidence_sha256",
        "restore_generation_sha256",
        "prepare_worker_path",
        "prepare_worker_sha256",
        "prior_result_path",
        "prior_result_sha256",
        "output_root",
        "constraints",
    }
)
CONSTRAINT_FIELDS = frozenset(
    {
        "plan_only_cli",
        "controller_live_authority_required",
        "business_write_forbidden",
        "external_network_forbidden",
        "ssh_forbidden",
        "object_storage_forbidden",
        "current_mutation_forbidden",
        "legacy_mutation_forbidden",
        "production_traffic_mutation_forbidden",
        "compose_down_forbidden",
        "volume_mutation_forbidden",
        "app_service_start_forbidden",
    }
)
AUTHORITY_CHALLENGE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "phase",
        "operation",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "plan_sha256",
        "request_sha256",
        "restore_generation_sha256",
        "boundary",
        "sequence",
        "challenge_nonce",
        "previous_authority_sha256",
        "publication_kind",
        "publication_payload_sha256",
    }
)
AUTHORITY_RESPONSE_FIELDS = frozenset(
    {
        *AUTHORITY_CHALLENGE_FIELDS,
        "schema",
        "status",
        "challenge_sha256",
        "response_nonce",
        "controller_lock_held",
        "controller_authoritative",
        "journal_status",
        "journal_state_sha256",
        "journal_event_tail_sha256",
        "journal_event_count",
        "completed_phases",
        "started_phase",
        "business_write_allowed",
        "current_mutation_allowed",
        "legacy_mutation_allowed",
        "production_traffic_mutation_allowed",
        "external_network_payload_allowed",
        "object_storage_mutation_allowed",
    }
)
JOURNAL_EVENT_FIELDS = frozenset(
    {
        "schema",
        "request_sha256",
        "operation_id",
        "role",
        "phase",
        "release_sha",
        "restore_generation_sha256",
        "index",
        "kind",
        "step",
        "attempt",
        "command_invoked",
        "recovered",
        "authority",
        "authority_sha256",
        "started_event_sha256",
        "semantic",
        "semantic_sha256",
        "previous_event_sha256",
        "event_sha256",
    }
)
EVIDENCE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "phase",
        "operation",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "plan_sha256",
        "request_sha256",
        "role_manifest_sha256",
        "restore_completion_sha256",
        "restore_phase_evidence_sha256",
        "restore_generation_sha256",
        "prior_result_sha256",
        "prepare_worker_sha256",
        "journal_event_count",
        "journal_tail_sha256",
        "completed_steps",
        "authority_verification_sha256",
        "business_write_observed",
        "app_service_started",
        "current_mutated",
        "legacy_mutated",
        "production_traffic_mutated",
        "external_network_contacted",
        "ssh_contacted",
        "object_storage_contacted",
        "semantic",
        "publication_authority",
        "publication_authority_sha256",
    }
)
RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "phase",
        "operation",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "plan_sha256",
        "request_sha256",
        "role_manifest_sha256",
        "restore_completion_sha256",
        "restore_phase_evidence_sha256",
        "restore_generation_sha256",
        "prior_result_sha256",
        "prepare_worker_sha256",
        "journal_event_count",
        "journal_tail_sha256",
        "evidence_path",
        "evidence_sha256",
        "semantic",
        "runtime_mutated",
        "business_write_observed",
        "app_service_started",
        "current_mutated",
        "legacy_mutated",
        "production_traffic_mutated",
        "external_network_contacted",
        "ssh_contacted",
        "object_storage_contacted",
        "publication_authority",
        "publication_authority_sha256",
    }
)


class FrozenPrepareWorkerError(RuntimeError):
    """The local frozen preparation cannot safely advance."""


class FrozenPrepareCancellation(FrozenPrepareWorkerError):
    """The controller connection or worker process authority was lost."""


class ControllerLivenessGuard:
    """Bind mutating execution to one controller-owned pipe read end.

    The controller must keep the matching write end open for the entire
    imported ``execute(..., apply=True)`` call and never write payload bytes.
    EOF or any byte is authority loss.  The worker also treats SIGHUP and
    SIGTERM as the same fail-closed cancellation boundary.
    """

    _WAKE_SIGNAL = signal.SIGUSR1
    _HANDLED_SIGNALS = (signal.SIGHUP, signal.SIGTERM, _WAKE_SIGNAL)

    def __init__(self, control_fd: int) -> None:
        if type(control_fd) is not int or control_fd < 0:
            raise FrozenPrepareWorkerError(
                "apply requires a controller-owned liveness pipe"
            )
        try:
            metadata = os.fstat(control_fd)
        except OSError as exc:
            raise FrozenPrepareWorkerError(
                "controller liveness pipe is unavailable"
            ) from exc
        if not stat.S_ISFIFO(metadata.st_mode):
            raise FrozenPrepareWorkerError(
                "controller liveness descriptor is not an anonymous pipe"
            )
        if threading.current_thread() is not threading.main_thread():
            raise FrozenPrepareWorkerError(
                "mutating prepare execution must run in the main thread"
            )
        try:
            self._fd = os.dup(control_fd)
            os.set_inheritable(self._fd, False)
            os.set_blocking(self._fd, False)
        except OSError as exc:
            raise FrozenPrepareWorkerError(
                "controller liveness pipe cannot be secured"
            ) from exc
        self._cancelled = threading.Event()
        self._stopping = threading.Event()
        self._reason = "controller liveness was lost"
        self._old_handlers: dict[int, Any] = {}
        self._monitor: threading.Thread | None = None

    def _cancel(self, reason: str, *, wake_main: bool) -> None:
        if self._cancelled.is_set():
            return
        self._reason = reason
        self._cancelled.set()
        if wake_main:
            main_ident = threading.main_thread().ident
            if main_ident is not None:
                try:
                    signal.pthread_kill(main_ident, self._WAKE_SIGNAL)
                except (OSError, RuntimeError):
                    # The main thread can finish between cancellation and wake.
                    pass

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        if signum == self._WAKE_SIGNAL:
            reason = self._reason
        else:
            reason = f"worker received signal {signum}"
            self._cancel(reason, wake_main=False)
        raise FrozenPrepareCancellation(reason)

    def _monitor_control(self) -> None:
        selector = selectors.DefaultSelector()
        try:
            selector.register(self._fd, selectors.EVENT_READ)
            while not self._stopping.is_set():
                if not selector.select(0.05):
                    continue
                try:
                    payload = os.read(self._fd, 1)
                except BlockingIOError:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        return
                    payload = b""
                reason = (
                    "controller liveness pipe reached EOF"
                    if payload == b""
                    else "controller liveness pipe carried forbidden data"
                )
                self._cancel(reason, wake_main=True)
                return
        finally:
            selector.close()

    def __enter__(self) -> ControllerLivenessGuard:
        for signum in self._HANDLED_SIGNALS:
            self._old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)
        self._monitor = threading.Thread(
            target=self._monitor_control,
            name="frozen-prepare-controller-liveness",
            daemon=True,
        )
        self._monitor.start()
        self.check()
        return self

    def check(self) -> None:
        if self._cancelled.is_set():
            raise FrozenPrepareCancellation(self._reason)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancellation_error(self) -> FrozenPrepareCancellation:
        return FrozenPrepareCancellation(self._reason)

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self._stopping.set()
        if self._monitor is not None:
            self._monitor.join(timeout=1)
        try:
            os.close(self._fd)
        except OSError:
            pass
        for signum, handler in self._old_handlers.items():
            signal.signal(signum, handler)


@dataclass(frozen=True)
class LoadedRequest:
    document: dict[str, Any]
    sha256: str
    path: Path
    manifest: RESTORE.RoleManifest
    controller_manifest: dict[str, Any]
    plan: dict[str, Any]
    restore_completion: dict[str, Any]
    restore_phase_evidence: dict[str, Any]
    prior_result: dict[str, Any] | None
    output_root: Path
    steps: tuple[tuple[str, str | None, int], ...]


@dataclass(frozen=True)
class JournalState:
    events: tuple[dict[str, Any], ...]
    completed_steps: tuple[str, ...]
    active_step: str | None
    active_attempt: int
    active_started_sha256: str | None
    finalized: bool
    tail_sha256: str


@dataclass(frozen=True)
class SqlExecutionScope:
    step: str
    attempt: int
    started_event_sha256: str
    stage: str


class DockerRunner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str],
        stdin: Any = subprocess.DEVNULL,
    ) -> str:
        """Execute one bounded local Docker command."""

    def stream(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str],
    ) -> Any:
        """Execute one bounded streaming local Docker command."""


LiveAuthorityVerifier = Callable[
    [Mapping[str, Any], str], Mapping[str, Any]
]


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
        raise FrozenPrepareWorkerError(
            "document is not canonical ASCII JSON"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FrozenPrepareWorkerError("JSON contains a duplicate field")
        result[key] = value
    return result


def _canonical_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise FrozenPrepareWorkerError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise FrozenPrepareWorkerError(f"{label} is invalid") from exc
    if str(parsed) != value:
        raise FrozenPrepareWorkerError(f"{label} is not canonical")
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise FrozenPrepareWorkerError(f"{label} is not a nonzero SHA-256")
    return value


def _absolute_path(value: Any, *, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise FrozenPrepareWorkerError(f"{label} path is invalid")
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(os.path.abspath(os.fspath(path)))
        or "current" in path.parts
        or "staging" in path.parts
    ):
        raise FrozenPrepareWorkerError(
            f"{label} must be an absolute normalized production path"
        )
    return path


def _read_secure_bytes(
    path: Path,
    *,
    label: str,
    maximum: int,
    mode: int = 0o600,
) -> bytes:
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
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != mode
            or not 1 <= before.st_size <= maximum
        ):
            raise FrozenPrepareWorkerError(
                f"{label} must be root-owned mode {mode:04o}"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
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
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if len(payload) > maximum or any(
            getattr(before, field) != getattr(after, field)
            for field in stable
        ):
            raise FrozenPrepareWorkerError(f"{label} changed while read")
        return payload
    except FrozenPrepareWorkerError:
        raise
    except OSError as exc:
        raise FrozenPrepareWorkerError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_json(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_JSON_BYTES,
) -> tuple[dict[str, Any], bytes, str]:
    payload = _read_secure_bytes(path, label=label, maximum=maximum)
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FrozenPrepareWorkerError(
            f"{label} is not strict ASCII JSON"
        ) from exc
    if not isinstance(value, dict):
        raise FrozenPrepareWorkerError(f"{label} root is not an object")
    return value, payload, _sha256(payload)


def _read_release_file(
    path: Path,
    *,
    label: str,
    expected_sha256: str,
) -> bytes:
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
            or not 1 <= before.st_size <= MAX_JSON_BYTES
        ):
            raise FrozenPrepareWorkerError(
                f"{label} release artifact is unsafe"
            )
        payload = b""
        while len(payload) <= MAX_JSON_BYTES:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            payload += chunk
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
            len(payload) > MAX_JSON_BYTES
            or any(
                getattr(before, field) != getattr(after, field)
                for field in stable
            )
            or _sha256(payload) != expected_sha256
        ):
            raise FrozenPrepareWorkerError(
                f"{label} release artifact identity differs"
            )
        return payload
    except FrozenPrepareWorkerError:
        raise
    except OSError as exc:
        raise FrozenPrepareWorkerError(
            f"{label} release artifact is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _expected_prior_phase(phase: str, role: str) -> str | None:
    if phase == "shadow_roles_pre_migration":
        return None
    if phase == "shadow_migrate":
        return (
            "shadow_roles_pre_migration"
            if role in {"webapp_fi", "webapp_ir"}
            else None
        )
    if phase == "shadow_roles_post_migration":
        return "shadow_migrate"
    if phase == "shadow_fence":
        return "shadow_roles_post_migration"
    raise FrozenPrepareWorkerError("prepare phase is invalid")


def _validate_prior_result(
    document: Any,
    *,
    expected_phase: str,
    request: Mapping[str, Any],
    manifest: RESTORE.RoleManifest,
    result_path: Path,
    result_sha256: str,
) -> dict[str, Any]:
    if (
        not isinstance(document, dict)
        or set(document) != RESULT_FIELDS
        or document.get("schema") != RESULT_SCHEMA
        or document.get("status") != "completed"
        or document.get("phase") != expected_phase
        or document.get("operation")
        != PHASE_OPERATIONS[expected_phase]
        or any(
            document.get(field) != request[field]
            for field in (
                "campaign_id",
                "operation_id",
                "role",
                "release_sha",
                "release_tree_sha",
                "controller_manifest_sha256",
                "plan_sha256",
                "role_manifest_sha256",
                "restore_completion_sha256",
                "restore_phase_evidence_sha256",
                "restore_generation_sha256",
                "prepare_worker_sha256",
            )
        )
        or document.get("business_write_observed") is not False
        or document.get("app_service_started") is not False
        or document.get("current_mutated") is not False
        or document.get("legacy_mutated") is not False
        or document.get("production_traffic_mutated") is not False
        or document.get("external_network_contacted") is not False
        or document.get("ssh_contacted") is not False
        or document.get("object_storage_contacted") is not False
        or not isinstance(document.get("semantic"), dict)
        or not isinstance(document.get("journal_event_count"), int)
        or isinstance(document.get("journal_event_count"), bool)
        or not 1 <= document["journal_event_count"] <= MAX_EVENT_COUNT
        or not isinstance(document.get("runtime_mutated"), bool)
    ):
        raise FrozenPrepareWorkerError(
            "prior prepare result does not close the exact prior phase"
        )
    request_sha256 = _nonzero_sha256(
        document["request_sha256"],
        label="prior result request",
    )
    _nonzero_sha256(
        document["journal_tail_sha256"],
        label="prior result journal tail",
    )
    evidence_sha256 = _nonzero_sha256(
        document["evidence_sha256"],
        label="prior result evidence",
    )
    expected_root = (
        manifest.paths.secret_generation_root
        / "prepare-phases"
        / expected_phase
    )
    expected_result_path = (
        expected_root
        / "results"
        / f"{expected_phase}-{result_sha256}.json"
    )
    expected_evidence_path = (
        expected_root
        / "evidence"
        / f"{expected_phase}-{evidence_sha256}.json"
    )
    evidence_path = _absolute_path(
        document["evidence_path"],
        label="prior prepare evidence",
    )
    if (
        result_path != expected_result_path
        or evidence_path != expected_evidence_path
    ):
        raise FrozenPrepareWorkerError(
            "prior prepare publications are not generation and digest derived"
        )
    _assert_exact_publication_namespace(
        expected_result_path.parent,
        expected_filename=expected_result_path.name,
        label="prior prepare result",
    )
    _assert_exact_publication_namespace(
        expected_evidence_path.parent,
        expected_filename=expected_evidence_path.name,
        label="prior prepare evidence",
    )

    prior_request_path = (
        manifest.paths.secret_generation_root
        / "prepare-requests"
        / f"{expected_phase}-{request_sha256}.json"
    )
    prior_context = load_request(prior_request_path)
    if (
        prior_context.sha256 != request_sha256
        or prior_context.document["phase"] != expected_phase
        or prior_context.document["role"] != request["role"]
        or prior_context.output_root != expected_root
    ):
        raise FrozenPrepareWorkerError(
            "prior prepare request binding differs"
        )
    if (
        document["prior_result_sha256"]
        != prior_context.document["prior_result_sha256"]
    ):
        raise FrozenPrepareWorkerError(
            "prior prepare result prerequisite binding differs"
        )
    journal = _load_journal(prior_context)
    expected_steps = [row[0] for row in prior_context.steps]
    if (
        list(journal.completed_steps) != expected_steps
        or journal.active_step is not None
        or not journal.finalized
        or len(journal.events) != document["journal_event_count"]
        or journal.tail_sha256 != document["journal_tail_sha256"]
    ):
        raise FrozenPrepareWorkerError(
            "prior prepare journal does not close the exact phase"
        )
    semantic = _phase_semantic(prior_context, journal)
    finalized_events = [
        event
        for event in journal.events
        if event["kind"] == "finalized"
    ]
    if (
        document["semantic"] != semantic
        or len(finalized_events) != 1
        or finalized_events[0]["semantic"] != semantic
    ):
        raise FrozenPrepareWorkerError(
            "prior prepare result semantic differs from its journal"
        )
    runtime_mutated = _journal_runtime_mutated(prior_context, journal)
    if document["runtime_mutated"] is not runtime_mutated:
        raise FrozenPrepareWorkerError(
            "prior prepare result runtime mutation claim differs"
        )

    evidence, evidence_payload, observed_evidence_sha256 = _read_json(
        evidence_path,
        label="prior prepare evidence",
    )
    if (
        observed_evidence_sha256 != evidence_sha256
        or set(evidence) != EVIDENCE_FIELDS
        or evidence.get("schema") != EVIDENCE_SCHEMA
        or evidence.get("status") != "completed"
        or evidence.get("journal_event_count") != len(journal.events)
        or evidence.get("journal_tail_sha256") != journal.tail_sha256
        or evidence.get("completed_steps") != expected_steps
        or evidence.get("semantic") != semantic
        or any(
            evidence.get(field) != document[field]
            for field in (
                "campaign_id",
                "operation_id",
                "role",
                "phase",
                "operation",
                "release_sha",
                "release_tree_sha",
                "controller_manifest_sha256",
                "plan_sha256",
                "request_sha256",
                "role_manifest_sha256",
                "restore_completion_sha256",
                "restore_phase_evidence_sha256",
                "restore_generation_sha256",
                "prior_result_sha256",
                "prepare_worker_sha256",
                "business_write_observed",
                "app_service_started",
                "current_mutated",
                "legacy_mutated",
                "production_traffic_mutated",
                "external_network_contacted",
                "ssh_contacted",
                "object_storage_contacted",
            )
        )
    ):
        raise FrozenPrepareWorkerError(
            "prior prepare evidence does not match its result and journal"
        )
    _nonzero_sha256(
        evidence["authority_verification_sha256"],
        label="prior evidence authority verification",
    )
    expected_authority_sha256 = _sha256(
        _canonical_json(
            [
                event["authority_sha256"]
                for event in journal.events
            ]
        )
    )
    if evidence["authority_verification_sha256"] != expected_authority_sha256:
        raise FrozenPrepareWorkerError(
            "prior prepare evidence authority digest differs"
        )
    evidence_publication_authority_sha256 = (
        _validate_publication_authority(
            evidence,
            context=prior_context,
            kind="evidence",
            expected_previous_authority_sha256=(
                _last_authority_sha256(journal)
            ),
        )
    )
    _validate_publication_authority(
        document,
        context=prior_context,
        kind="result",
        expected_previous_authority_sha256=(
            evidence_publication_authority_sha256
        ),
    )
    if evidence_payload != _canonical_json(evidence) + b"\n":
        raise FrozenPrepareWorkerError(
            "prior prepare evidence is not canonical"
        )
    return dict(document)


def _validate_restore_completion(
    document: Any,
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    expected_fields = {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "live_lease_claim_sha256",
        "live_lease_claim_epoch",
        "live_lease_claim_nonce",
        "legacy_frozen_receipt_sha256",
        "roles",
        "role_order",
        "claim_consume_outcome",
        "claim_consumed",
        "consumption_receipt_included",
        "current_mutated",
        "legacy_mutated",
        "object_storage_mutated_by_restore",
        "app_services_started",
        "redis_restored",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected_fields
        or document.get("schema")
        != RESTORE_ORCHESTRATOR.COMPLETION_SCHEMA
        or document.get("status")
        != "three-role-frozen-final-restored"
        or any(
            document.get(field) != request[field]
            for field in (
                "campaign_id",
                "operation_id",
                "release_sha",
                "release_tree_sha",
                "controller_manifest_sha256",
                "restore_generation_sha256",
            )
        )
        or document.get("role_order") != list(ROLES)
        or not isinstance(document.get("roles"), dict)
        or set(document["roles"]) != set(ROLES)
        or document.get("claim_consumed") is not False
        or document.get("consumption_receipt_included") is not False
        or document.get("current_mutated") is not False
        or document.get("legacy_mutated") is not False
        or document.get("object_storage_mutated_by_restore") is not False
        or document.get("app_services_started") is not False
        or document.get("redis_restored") is not False
    ):
        raise FrozenPrepareWorkerError(
            "frozen restore completion identity or safety closure differs"
        )
    role_row = document["roles"][request["role"]]
    if (
        not isinstance(role_row, dict)
        or set(role_row)
        != {
            "source_role",
            "transport",
            "host_result",
            "host_result_sha256",
            "role_manifest_sha256",
            "installer_receipt_sha256",
            "restore_result_sha256",
            "final_evidence_sha256",
        }
        or role_row["role_manifest_sha256"]
        != request["role_manifest_sha256"]
        or _sha256(_canonical_json(role_row["host_result"]))
        != role_row["host_result_sha256"]
    ):
        raise FrozenPrepareWorkerError(
            "role restore completion binding differs"
        )
    host = role_row["host_result"]
    if (
        not isinstance(host, dict)
        or host.get("operation_id") != request["operation_id"]
        or host.get("role") != request["role"]
        or host.get("release_sha") != request["release_sha"]
        or host.get("release_tree_sha") != request["release_tree_sha"]
        or host.get("controller_manifest_sha256")
        != request["controller_manifest_sha256"]
        or host.get("restore_generation_sha256")
        != request["restore_generation_sha256"]
        or host.get("app_services_started") is not False
        or host.get("redis_restored") is not False
        or host.get("current_mutated") is not False
        or host.get("legacy_mutated") is not False
        or host.get("object_storage_mutated") is not False
    ):
        raise FrozenPrepareWorkerError(
            "role host restore result safety closure differs"
        )
    restore_readback = host.get("restore_result")
    restore = (
        restore_readback.get("document")
        if isinstance(restore_readback, dict)
        else None
    )
    if (
        not isinstance(restore_readback, dict)
        or not isinstance(restore, dict)
        or set(restore) != RESTORE.RESULT_FIELDS
        or restore.get("schema") != RESTORE.RESULT_SCHEMA
        or restore.get("status") != "frozen-final-shadow-restored"
        or restore.get("role") != request["role"]
        or restore.get("operation_id") != request["operation_id"]
        or restore.get("release_sha") != request["release_sha"]
        or restore.get("release_tree_sha") != request["release_tree_sha"]
        or restore.get("controller_manifest_sha256")
        != request["controller_manifest_sha256"]
        or restore.get("restore_generation_sha256")
        != request["restore_generation_sha256"]
        or restore.get("redis_restore_bytes") != 0
        or restore.get("redis_pristine") is not True
        or restore.get("public_or_private_app_started") is not False
        or restore.get("current_mutated") is not False
        or restore.get("legacy_mutated") is not False
        or restore.get("object_storage_mutated") is not False
        or restore.get("nginx_state") != "legacy-frozen"
        or restore_readback.get("canonical_document_sha256")
        != role_row["restore_result_sha256"]
        or _sha256(_canonical_json(restore))
        != role_row["restore_result_sha256"]
    ):
        raise FrozenPrepareWorkerError(
            "role frozen restore result differs"
        )
    return dict(document)


def _validate_restore_phase_evidence(
    document: Any,
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    claims = document.get("claims") if isinstance(document, dict) else None
    result_claim = (
        claims.get("restore_result_set_sha256")
        if isinstance(claims, dict)
        else None
    )
    if (
        not isinstance(document, dict)
        or set(document) != VERIFY.EVIDENCE_FIELDS
        or document.get("schema") != VERIFY.EVIDENCE_SCHEMA
        or document.get("phase") != "shadow_restore"
        or document.get("operation")
        != PHASE_OPERATIONS.get(
            "shadow_restore",
            _CONTROLLER_PHASES["shadow_restore"].operation,
        )
        or document.get("status") != "passed"
        or document.get("business_write_observed") is not False
        or document.get("campaign_id") != request["campaign_id"]
        or document.get("operation_id") != request["operation_id"]
        or document.get("release_sha") != request["release_sha"]
        or document.get("manifest_sha256")
        != request["controller_manifest_sha256"]
        or document.get("plan_sha256") != request["plan_sha256"]
        or not isinstance(claims, dict)
        or set(claims) != set(VERIFY.PHASE_CLAIM_RULES["shadow_restore"])
        or not isinstance(result_claim, dict)
        or set(result_claim) != VERIFY.CLAIM_FIELDS
        or result_claim.get("value")
        != request["restore_completion_sha256"]
    ):
        raise FrozenPrepareWorkerError(
            "completed shadow_restore evidence differs"
        )
    _nonzero_sha256(
        result_claim["source_sha256"],
        label="shadow restore result claim source",
    )
    return dict(document)


def _verify_immutable_prepare_worker(
    manifest: RESTORE.RoleManifest,
    *,
    worker_path: Path,
    worker_sha256: str,
) -> None:
    expected = (
        manifest.paths.release_root
        / "scripts"
        / "production_shadow_frozen_prepare_worker.py"
    )
    if worker_path != expected or RUNNING_WORKER_PATH != expected:
        raise FrozenPrepareWorkerError(
            "running prepare worker is not the immutable release worker"
        )
    if (
        not sys.path
        or Path(sys.path[0] or os.getcwd()).resolve()
        != manifest.paths.release_root
    ):
        raise FrozenPrepareWorkerError(
            "immutable release root is not first on the import path"
        )
    _read_release_file(
        worker_path,
        label="frozen prepare worker",
        expected_sha256=worker_sha256,
    )
    trusted_relative_paths = (
        "scripts/production_shadow_frozen_prepare_worker.py",
        *sorted(TRUSTED_IMPORTED_MODULE_SHA256),
    )
    for relative_path, imported_sha256 in (
        TRUSTED_IMPORTED_MODULE_SHA256.items()
    ):
        _read_release_file(
            manifest.paths.release_root / relative_path,
            label=f"trusted imported module {relative_path}",
            expected_sha256=imported_sha256,
        )
    try:
        head = RESTORE._run_readonly(
            [
                RESTORE.GIT,
                "-C",
                str(manifest.paths.release_root),
                "rev-parse",
                "HEAD",
            ]
        )
        tree = RESTORE._run_readonly(
            [
                RESTORE.GIT,
                "-C",
                str(manifest.paths.release_root),
                "rev-parse",
                "HEAD^{tree}",
            ]
        )
        tracked = RESTORE._run_readonly(
            [
                RESTORE.GIT,
                "-C",
                str(manifest.paths.release_root),
                "ls-files",
                "--stage",
                "--",
                *trusted_relative_paths,
            ]
        )
        branch = RESTORE._run_readonly(
            [
                RESTORE.GIT,
                "-C",
                str(manifest.paths.release_root),
                "branch",
                "--show-current",
            ]
        )
        status_output = RESTORE._run_readonly(
            [
                RESTORE.GIT,
                "-C",
                str(manifest.paths.release_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ]
        )
        untracked = RESTORE._run_readonly(
            [
                RESTORE.GIT,
                "-C",
                str(manifest.paths.release_root),
                "ls-files",
                "--others",
                "--exclude-standard",
            ]
        )
        ignored = RESTORE._run_readonly(
            [
                RESTORE.GIT,
                "-C",
                str(manifest.paths.release_root),
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
            ]
        )
        RESTORE._verify_git_index_visibility(
            manifest.paths.release_root
        )
    except RESTORE.FrozenFinalRestoreWorkerError as exc:
        raise FrozenPrepareWorkerError(
            "immutable prepare worker Git binding is unavailable"
        ) from exc
    if (
        head != manifest.release_sha
        or tree != manifest.release_tree_sha
        or branch != ""
        or status_output != ""
        or untracked != ""
        or ignored != ""
        or len(tracked.splitlines()) != len(trusted_relative_paths)
    ):
        raise FrozenPrepareWorkerError(
            "immutable prepare worker is not exact tracked release content"
        )
    tracked_paths: set[str] = set()
    for line in tracked.splitlines():
        match = re.fullmatch(
            r"100(644|755) [0-9a-f]{40} 0\t(.+)",
            line,
        )
        if match is None or match.group(2) not in trusted_relative_paths:
            raise FrozenPrepareWorkerError(
                "trusted imported module is not exact tracked release content"
            )
        tracked_paths.add(match.group(2))
    if tracked_paths != set(trusted_relative_paths):
        raise FrozenPrepareWorkerError(
            "trusted imported module tracking closure differs"
        )


def load_request(path: Path) -> LoadedRequest:
    path = _absolute_path(path, label="prepare request")
    document, _payload, digest = _read_json(
        path,
        label="frozen prepare request",
    )
    if (
        set(document) != REQUEST_FIELDS
        or document.get("schema") != REQUEST_SCHEMA
        or document.get("status") != "authorized-input"
    ):
        raise FrozenPrepareWorkerError(
            "frozen prepare request fields are not exact"
        )
    campaign_id = _canonical_uuid(document["campaign_id"], label="campaign id")
    operation_id = _canonical_uuid(
        document["operation_id"],
        label="operation id",
    )
    role = document["role"]
    phase = document["phase"]
    if (
        campaign_id == operation_id
        or role not in ROLES
        or phase not in PHASES
        or role not in PHASE_ROLES[phase]
        or document["operation"] != PHASE_OPERATIONS[phase]
        or SHA40_RE.fullmatch(str(document["release_sha"])) is None
        or SHA40_RE.fullmatch(str(document["release_tree_sha"])) is None
    ):
        raise FrozenPrepareWorkerError(
            "frozen prepare request phase or release identity is invalid"
        )
    for field in (
        "controller_manifest_sha256",
        "plan_sha256",
        "role_manifest_sha256",
        "restore_completion_sha256",
        "restore_phase_evidence_sha256",
        "restore_generation_sha256",
        "prepare_worker_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    constraints = document["constraints"]
    if (
        not isinstance(constraints, dict)
        or set(constraints) != CONSTRAINT_FIELDS
        or any(value is not True for value in constraints.values())
    ):
        raise FrozenPrepareWorkerError(
            "frozen prepare constraints are not fail-closed"
        )

    role_manifest_path = _absolute_path(
        document["role_manifest_path"],
        label="role manifest",
    )
    try:
        manifest = RESTORE.load_role_manifest(role_manifest_path)
    except RESTORE.FrozenFinalRestoreWorkerError as exc:
        raise FrozenPrepareWorkerError(
            "frozen restore role manifest is invalid"
        ) from exc
    if (
        manifest.canonical_sha256 != document["role_manifest_sha256"]
        or manifest.operation_id != operation_id
        or manifest.role != role
        or manifest.release_sha != document["release_sha"]
        or manifest.release_tree_sha != document["release_tree_sha"]
        or manifest.restore_generation_sha256
        != document["restore_generation_sha256"]
    ):
        raise FrozenPrepareWorkerError(
            "frozen restore role manifest request binding differs"
        )

    expected_request_path = (
        manifest.paths.secret_generation_root
        / "prepare-requests"
        / f"{phase}-{digest}.json"
    )
    if path != expected_request_path:
        raise FrozenPrepareWorkerError(
            "prepare request path is not generation and digest derived"
        )
    output_root = _absolute_path(document["output_root"], label="output root")
    if (
        output_root
        != manifest.paths.secret_generation_root
        / "prepare-phases"
        / phase
    ):
        raise FrozenPrepareWorkerError(
            "prepare output root is not generation and phase derived"
        )

    controller_path = _absolute_path(
        document["controller_manifest_path"],
        label="controller manifest",
    )
    try:
        controller, controller_sha256 = CONTROLLER.read_root_only_manifest(
            controller_path
        )
        plan = CONTROLLER.render_plan(
            controller,
            manifest_sha256=controller_sha256,
            manifest_path=controller_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenPrepareWorkerError(
            "controller manifest or plan is invalid"
        ) from exc
    if (
        controller_sha256 != document["controller_manifest_sha256"]
        or plan["plan_sha256"] != document["plan_sha256"]
        or controller["campaign_id"] != campaign_id
        or controller["operation_id"] != operation_id
        or controller["release_sha"] != manifest.release_sha
        or controller["release_tree_sha"] != manifest.release_tree_sha
        or controller_sha256 != manifest.controller_manifest_sha256
    ):
        raise FrozenPrepareWorkerError(
            "controller manifest, plan, or frozen generation differs"
        )

    completion_path = _absolute_path(
        document["restore_completion_path"],
        label="restore completion",
    )
    completion, _completion_payload, completion_sha256 = _read_json(
        completion_path,
        label="frozen restore completion",
        maximum=MAX_COMPLETION_BYTES,
    )
    if completion_sha256 != document["restore_completion_sha256"]:
        raise FrozenPrepareWorkerError(
            "frozen restore completion digest differs"
        )
    completion = _validate_restore_completion(
        completion,
        request=document,
    )

    restore_evidence_path = _absolute_path(
        document["restore_phase_evidence_path"],
        label="shadow restore evidence",
    )
    restore_evidence, _evidence_payload, evidence_sha256 = _read_json(
        restore_evidence_path,
        label="shadow restore phase evidence",
    )
    if evidence_sha256 != document["restore_phase_evidence_sha256"]:
        raise FrozenPrepareWorkerError(
            "shadow restore phase evidence digest differs"
        )
    restore_evidence = _validate_restore_phase_evidence(
        restore_evidence,
        request=document,
    )

    expected_prior = _expected_prior_phase(phase, role)
    prior_path_value = document["prior_result_path"]
    prior_sha_value = document["prior_result_sha256"]
    prior_result: dict[str, Any] | None = None
    if expected_prior is None:
        if prior_path_value is not None or prior_sha_value is not None:
            raise FrozenPrepareWorkerError(
                "prepare request carries an unexpected prior role result"
            )
    else:
        prior_path = _absolute_path(
            prior_path_value,
            label="prior prepare result",
        )
        prior_sha256 = _nonzero_sha256(
            prior_sha_value,
            label="prior prepare result",
        )
        prior_result, _prior_payload, observed_prior_sha256 = _read_json(
            prior_path,
            label="prior prepare result",
        )
        if observed_prior_sha256 != prior_sha256:
            raise FrozenPrepareWorkerError(
                "prior prepare result digest differs"
            )
        if _prior_payload != _canonical_json(prior_result) + b"\n":
            raise FrozenPrepareWorkerError(
                "prior prepare result is not canonical"
            )
        prior_result = _validate_prior_result(
            prior_result,
            expected_phase=expected_prior,
            request=document,
            manifest=manifest,
            result_path=prior_path,
            result_sha256=prior_sha256,
        )

    worker_path = _absolute_path(
        document["prepare_worker_path"],
        label="prepare worker",
    )
    _verify_immutable_prepare_worker(
        manifest,
        worker_path=worker_path,
        worker_sha256=document["prepare_worker_sha256"],
    )
    return LoadedRequest(
        document=dict(document),
        sha256=digest,
        path=path,
        manifest=manifest,
        controller_manifest=controller,
        plan=plan,
        restore_completion=completion,
        restore_phase_evidence=restore_evidence,
        prior_result=prior_result,
        output_root=output_root,
        steps=STEP_SERVICES[(phase, role)],
    )


def _private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise FrozenPrepareWorkerError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise FrozenPrepareWorkerError(
            f"{label} must be root-owned mode 0700"
        )


def _ensure_private_descendant(
    root: Path,
    path: Path,
    *,
    create: bool,
) -> None:
    root = _absolute_path(root, label="private root")
    path = _absolute_path(path, label="private descendant")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise FrozenPrepareWorkerError(
            "private output escaped its generation root"
        ) from exc
    _private_directory(root, label="generation secret root")
    current = root
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise FrozenPrepareWorkerError(
                "private output path component is invalid"
            )
        parent_fd = -1
        try:
            parent_fd = os.open(
                current,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except FileExistsError:
                    pass
            current = current / part
            _private_directory(current, label="private output directory")
        except FrozenPrepareWorkerError:
            raise
        except OSError as exc:
            if not create and not current.exists():
                raise FrozenPrepareWorkerError(
                    "private output directory does not exist"
                ) from exc
            raise FrozenPrepareWorkerError(
                "private output directory is unsafe"
            ) from exc
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)


def _persist_new_document(
    directory: Path,
    *,
    filename: str,
    document: Mapping[str, Any],
    label: str,
) -> tuple[Path, str, str]:
    if (
        not filename
        or "/" in filename
        or filename in {".", ".."}
        or len(filename) > 512
    ):
        raise FrozenPrepareWorkerError(f"{label} filename is invalid")
    payload = _canonical_json(document) + b"\n"
    if len(payload) > MAX_JSON_BYTES:
        raise FrozenPrepareWorkerError(f"{label} exceeds its byte bound")
    path = directory / filename
    descriptor = -1
    directory_fd = -1
    try:
        directory_fd = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor = os.open(
            filename,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise FrozenPrepareWorkerError(
                    f"{label} write did not progress"
                )
            offset += written
        os.fsync(descriptor)
        os.fsync(directory_fd)
        publication = "created"
    except FileExistsError:
        observed = _read_secure_bytes(
            path,
            label=f"existing {label}",
            maximum=MAX_JSON_BYTES,
        )
        if observed != payload:
            raise FrozenPrepareWorkerError(
                f"existing create-only {label} differs"
            )
        publication = "reused"
    except FrozenPrepareWorkerError:
        raise
    except OSError as exc:
        raise FrozenPrepareWorkerError(
            f"{label} could not be persisted safely"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)
    observed = _read_secure_bytes(
        path,
        label=f"persisted {label}",
        maximum=MAX_JSON_BYTES,
    )
    if observed != payload:
        raise FrozenPrepareWorkerError(f"{label} readback differs")
    return path, _sha256(payload), publication


def _assert_exact_publication_namespace(
    directory: Path,
    *,
    expected_filename: str,
    label: str,
) -> None:
    try:
        names = sorted(path.name for path in directory.iterdir())
    except OSError as exc:
        raise FrozenPrepareWorkerError(
            f"{label} namespace cannot be enumerated"
        ) from exc
    if names not in ([], [expected_filename]):
        raise FrozenPrepareWorkerError(
            f"{label} namespace contains a foreign publication"
        )


@contextmanager
def _phase_lock(context: LoadedRequest):  # noqa: ANN202
    root = context.manifest.paths.secret_generation_root
    _ensure_private_descendant(root, context.output_root, create=True)
    lock_path = context.output_root / "prepare.lock"
    descriptor = -1
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise FrozenPrepareWorkerError(
                "prepare phase lock is unsafe"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except FrozenPrepareWorkerError:
        raise
    except OSError as exc:
        raise FrozenPrepareWorkerError(
            "prepare phase lock is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def _event_hash(document: Mapping[str, Any]) -> str:
    return _sha256(
        _canonical_json(
            {
                key: value
                for key, value in document.items()
                if key != "event_sha256"
            }
        )
    )


def _validate_authority_document(
    authority: Any,
    *,
    context: LoadedRequest,
) -> str:
    if (
        not isinstance(authority, dict)
        or set(authority) != AUTHORITY_RESPONSE_FIELDS
        or authority.get("schema") != AUTHORITY_RESPONSE_SCHEMA
        or authority.get("status") != "verified-live"
        or any(
            authority.get(field) != context.document[field]
            for field in (
                "campaign_id",
                "operation_id",
                "role",
                "phase",
                "operation",
                "release_sha",
                "release_tree_sha",
                "controller_manifest_sha256",
                "plan_sha256",
                "restore_generation_sha256",
            )
        )
        or authority.get("request_sha256") != context.sha256
        or authority.get("controller_lock_held") is not True
        or authority.get("controller_authoritative") is not True
        or authority.get("journal_status") != "phase_started"
        or authority.get("started_phase") != context.document["phase"]
        or authority.get("business_write_allowed") is not False
        or authority.get("current_mutation_allowed") is not False
        or authority.get("legacy_mutation_allowed") is not False
        or authority.get("production_traffic_mutation_allowed") is not False
        or authority.get("external_network_payload_allowed") is not False
        or authority.get("object_storage_mutation_allowed") is not False
        or not isinstance(authority.get("sequence"), int)
        or isinstance(authority.get("sequence"), bool)
        or not 1 <= authority["sequence"] <= 1_000_000
        or not isinstance(authority.get("journal_event_count"), int)
        or isinstance(authority.get("journal_event_count"), bool)
        or authority["journal_event_count"] < 1
        or not isinstance(authority.get("boundary"), str)
        or SAFE_TOKEN_RE.fullmatch(authority["boundary"]) is None
        or HEX64_RE.fullmatch(str(authority.get("challenge_nonce"))) is None
        or HEX64_RE.fullmatch(str(authority.get("response_nonce"))) is None
        or authority["challenge_nonce"] == authority["response_nonce"]
    ):
        raise FrozenPrepareWorkerError(
            "controller live authority response is invalid"
        )
    publication_kind = authority.get("publication_kind")
    publication_payload_sha256 = authority.get(
        "publication_payload_sha256"
    )
    is_publication = authority["boundary"] in {
        "publish:evidence",
        "publish:result",
    }
    if (
        is_publication
        and (
            publication_kind not in {"evidence", "result"}
            or publication_payload_sha256 is None
        )
    ) or (
        not is_publication
        and (
            publication_kind is not None
            or publication_payload_sha256 is not None
        )
    ):
        raise FrozenPrepareWorkerError(
            "controller publication authority binding is invalid"
        )
    if publication_payload_sha256 is not None:
        _nonzero_sha256(
            publication_payload_sha256,
            label="authority publication payload",
        )
    for field in (
        "challenge_sha256",
        "journal_state_sha256",
        "journal_event_tail_sha256",
    ):
        _nonzero_sha256(authority[field], label=f"authority {field}")
    previous = authority["previous_authority_sha256"]
    if previous != ZERO_SHA256:
        _nonzero_sha256(previous, label="previous authority")
    expected_completed = list(
        CONTROLLER.PHASES[
            : CONTROLLER.PHASES.index(context.document["phase"])
        ]
    )
    if authority.get("completed_phases") != expected_completed:
        raise FrozenPrepareWorkerError(
            "controller phase prefix is not exact"
        )
    challenge = {
        field: authority[field]
        for field in AUTHORITY_CHALLENGE_FIELDS
    }
    challenge["schema"] = AUTHORITY_CHALLENGE_SCHEMA
    challenge["status"] = "challenge"
    if authority["challenge_sha256"] != _sha256(
        _canonical_json(challenge)
    ):
        raise FrozenPrepareWorkerError(
            "persisted authority challenge digest differs"
        )
    return _sha256(_canonical_json(authority))


def _load_journal(context: LoadedRequest) -> JournalState:
    events_directory = context.output_root / "journal" / "events"
    if not events_directory.exists():
        return JournalState((), (), None, 0, None, False, ZERO_SHA256)
    _ensure_private_descendant(
        context.manifest.paths.secret_generation_root,
        events_directory,
        create=False,
    )
    try:
        candidates = sorted(events_directory.iterdir())
    except OSError as exc:
        raise FrozenPrepareWorkerError(
            "prepare journal cannot be enumerated"
        ) from exc
    if len(candidates) > MAX_EVENT_COUNT:
        raise FrozenPrepareWorkerError(
            "prepare journal exceeds its event bound"
        )
    expected_steps = [row[0] for row in context.steps]
    completed: list[str] = []
    active: str | None = None
    active_attempt = 0
    active_started: str | None = None
    previous = ZERO_SHA256
    previous_authority = ZERO_SHA256
    finalized = False
    cleaned_attempts: set[tuple[str, int]] = set()
    events: list[dict[str, Any]] = []
    pattern = re.compile(r"^([0-9]{8})-([0-9a-f]{64})\.json$")
    for index, path in enumerate(candidates, 1):
        match = pattern.fullmatch(path.name)
        if match is None or int(match.group(1)) != index:
            raise FrozenPrepareWorkerError(
                "prepare journal filename sequence is invalid"
            )
        event, _payload, _raw_sha256 = _read_json(
            path,
            label="prepare journal event",
        )
        if (
            set(event) != JOURNAL_EVENT_FIELDS
            or event.get("schema") != JOURNAL_EVENT_SCHEMA
            or event.get("request_sha256") != context.sha256
            or event.get("operation_id") != context.document["operation_id"]
            or event.get("role") != context.document["role"]
            or event.get("phase") != context.document["phase"]
            or event.get("release_sha") != context.document["release_sha"]
            or event.get("restore_generation_sha256")
            != context.document["restore_generation_sha256"]
            or event.get("index") != index
            or event.get("kind")
            not in {"started", "cleanup", "completed", "finalized"}
            or (
                event.get("step") not in expected_steps
                and event.get("step") != PHASE_CLOSURE_STEP
            )
            or event.get("previous_event_sha256") != previous
            or event.get("event_sha256") != match.group(2)
            or _event_hash(event) != match.group(2)
        ):
            raise FrozenPrepareWorkerError(
                "prepare journal event binding differs"
            )
        authority_sha256 = _validate_authority_document(
            event["authority"],
            context=context,
        )
        if event["authority_sha256"] != authority_sha256:
            raise FrozenPrepareWorkerError(
                "prepare journal authority digest differs"
            )
        if (
            event["authority"]["sequence"] != index
            or event["authority"]["previous_authority_sha256"]
            != previous_authority
        ):
            raise FrozenPrepareWorkerError(
                "prepare journal authority chain differs"
            )
        step = str(event["step"])
        if event["kind"] == "finalized":
            semantic = event["semantic"]
            if (
                finalized
                or active is not None
                or completed != expected_steps
                or step != PHASE_CLOSURE_STEP
                or event["attempt"] != 0
                or event["command_invoked"] is not False
                or event["recovered"] is not False
                or event["started_event_sha256"] is not None
                or not isinstance(semantic, dict)
                or event["semantic_sha256"]
                != _sha256(_canonical_json(semantic))
                or event["authority"]["boundary"]
                != f"finalize:{PHASE_CLOSURE_STEP}"
            ):
                raise FrozenPrepareWorkerError(
                    "prepare finalized event is invalid"
                )
            finalized = True
            previous = event["event_sha256"]
            previous_authority = authority_sha256
            events.append(event)
            continue
        if finalized:
            raise FrozenPrepareWorkerError(
                "prepare journal continues after finalization"
            )
        if event["kind"] == "cleanup":
            cleanup = event["semantic"]
            cleanup_key = (step, event["attempt"])
            if (
                active != step
                or event["attempt"] != active_attempt
                or cleanup_key in cleaned_attempts
                or event["command_invoked"] is not True
                or event["recovered"] is not True
                or event["started_event_sha256"] != active_started
                or not isinstance(cleanup, dict)
                or set(cleanup) != CLEANUP_SEMANTIC_FIELDS
                or not isinstance(cleanup.get("residue_count"), int)
                or isinstance(cleanup.get("residue_count"), bool)
                or not isinstance(cleanup.get("removed_count"), int)
                or isinstance(cleanup.get("removed_count"), bool)
                or cleanup.get("residue_count") != 1
                or cleanup.get("removed_count") != 1
                or cleanup.get("persistent_volume_removed") is not False
                or cleanup.get("generation_data_mutated") is not False
                or event["semantic_sha256"]
                != _sha256(_canonical_json(cleanup))
                or event["authority"]["boundary"]
                != f"cleanup:{step}:attempt:{active_attempt}"
            ):
                raise FrozenPrepareWorkerError(
                    "prepare cleanup event is invalid"
                )
            _nonzero_sha256(
                cleanup["residue_identity_sha256"],
                label="prepare residue identity",
            )
            cleaned_attempts.add(cleanup_key)
            previous = event["event_sha256"]
            previous_authority = authority_sha256
            events.append(event)
            continue
        if len(completed) >= len(expected_steps):
            raise FrozenPrepareWorkerError(
                "prepare journal continues after all steps"
            )
        expected_step = expected_steps[len(completed)]
        if step != expected_step:
            raise FrozenPrepareWorkerError(
                "prepare journal step order differs"
            )
        attempt = event["attempt"]
        if (
            isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or not 1 <= attempt <= MAX_ATTEMPTS_PER_STEP
        ):
            raise FrozenPrepareWorkerError(
                "prepare journal attempt is invalid"
            )
        if event["kind"] == "started":
            if (
                event["command_invoked"] is not False
                or event["recovered"] is not False
                or event["started_event_sha256"] is not None
                or event["semantic"] is not None
                or event["semantic_sha256"] is not None
                or attempt != active_attempt + 1
                or event["authority"]["boundary"]
                != f"before:{step}:attempt:{attempt}"
            ):
                raise FrozenPrepareWorkerError(
                    "prepare started event is invalid"
                )
            active = step
            active_attempt = attempt
            active_started = event["event_sha256"]
        else:
            semantic = event["semantic"]
            if (
                active != step
                or attempt != active_attempt
                or event["started_event_sha256"] != active_started
                or not isinstance(event["command_invoked"], bool)
                or not isinstance(event["recovered"], bool)
                or not isinstance(semantic, dict)
                or event["semantic_sha256"]
                != _sha256(_canonical_json(semantic))
                or event["command_invoked"]
                != semantic.get("execution", {}).get("command_invoked")
                or event["authority"]["boundary"]
                != (
                    f"reconcile:{step}:attempt:{attempt}"
                    if event["recovered"]
                    else f"after:{step}:attempt:{attempt}"
                )
            ):
                raise FrozenPrepareWorkerError(
                    "prepare completed event is invalid"
                )
            completed.append(step)
            active = None
            active_attempt = 0
            active_started = None
        previous = event["event_sha256"]
        previous_authority = authority_sha256
        events.append(event)
    return JournalState(
        tuple(events),
        tuple(completed),
        active,
        active_attempt,
        active_started,
        finalized,
        previous,
    )


def _append_event(
    context: LoadedRequest,
    journal: JournalState,
    *,
    kind: str,
    step: str,
    attempt: int,
    authority: Mapping[str, Any],
    command_invoked: bool,
    recovered: bool,
    started_event_sha256: str | None,
    semantic: Mapping[str, Any] | None,
) -> dict[str, Any]:
    events_directory = context.output_root / "journal" / "events"
    _ensure_private_descendant(
        context.manifest.paths.secret_generation_root,
        events_directory,
        create=True,
    )
    authority_document = dict(authority)
    authority_sha256 = _validate_authority_document(
        authority_document,
        context=context,
    )
    semantic_document = dict(semantic) if semantic is not None else None
    event = {
        "schema": JOURNAL_EVENT_SCHEMA,
        "request_sha256": context.sha256,
        "operation_id": context.document["operation_id"],
        "role": context.document["role"],
        "phase": context.document["phase"],
        "release_sha": context.document["release_sha"],
        "restore_generation_sha256": context.document[
            "restore_generation_sha256"
        ],
        "index": len(journal.events) + 1,
        "kind": kind,
        "step": step,
        "attempt": attempt,
        "command_invoked": command_invoked,
        "recovered": recovered,
        "authority": authority_document,
        "authority_sha256": authority_sha256,
        "started_event_sha256": started_event_sha256,
        "semantic": semantic_document,
        "semantic_sha256": (
            _sha256(_canonical_json(semantic_document))
            if semantic_document is not None
            else None
        ),
        "previous_event_sha256": journal.tail_sha256,
        "event_sha256": "",
    }
    event["event_sha256"] = _event_hash(event)
    filename = f"{event['index']:08d}-{event['event_sha256']}.json"
    _persist_new_document(
        events_directory,
        filename=filename,
        document=event,
        label="prepare journal event",
    )
    observed = _load_journal(context)
    if (
        len(observed.events) != len(journal.events) + 1
        or observed.events[-1] != event
    ):
        raise FrozenPrepareWorkerError(
            "prepare journal event readback differs"
        )
    return event


def _authority(
    context: LoadedRequest,
    verifier: LiveAuthorityVerifier,
    *,
    boundary: str,
    sequence: int,
    previous_authority_sha256: str,
    publication_kind: str | None = None,
    publication_payload_sha256: str | None = None,
) -> tuple[dict[str, Any], str]:
    if SAFE_TOKEN_RE.fullmatch(boundary) is None:
        raise FrozenPrepareWorkerError("authority boundary is invalid")
    expected_publication_kind = {
        "publish:evidence": "evidence",
        "publish:result": "result",
    }.get(boundary)
    if (
        expected_publication_kind is None
        and (
            publication_kind is not None
            or publication_payload_sha256 is not None
        )
    ) or (
        expected_publication_kind is not None
        and (
            publication_kind != expected_publication_kind
            or publication_payload_sha256 is None
        )
    ):
        raise FrozenPrepareWorkerError(
            "authority publication binding differs"
        )
    if publication_payload_sha256 is not None:
        _nonzero_sha256(
            publication_payload_sha256,
            label="authority publication payload",
        )
    challenge = {
        "schema": AUTHORITY_CHALLENGE_SCHEMA,
        "status": "challenge",
        "campaign_id": context.document["campaign_id"],
        "operation_id": context.document["operation_id"],
        "role": context.document["role"],
        "phase": context.document["phase"],
        "operation": context.document["operation"],
        "release_sha": context.document["release_sha"],
        "release_tree_sha": context.document["release_tree_sha"],
        "controller_manifest_sha256": context.document[
            "controller_manifest_sha256"
        ],
        "plan_sha256": context.document["plan_sha256"],
        "request_sha256": context.sha256,
        "restore_generation_sha256": context.document[
            "restore_generation_sha256"
        ],
        "boundary": boundary,
        "sequence": sequence,
        "challenge_nonce": secrets.token_hex(32),
        "previous_authority_sha256": previous_authority_sha256,
        "publication_kind": publication_kind,
        "publication_payload_sha256": publication_payload_sha256,
    }
    if set(challenge) != AUTHORITY_CHALLENGE_FIELDS:
        raise FrozenPrepareWorkerError(
            "internal authority challenge fields differ"
        )
    try:
        raw_response = verifier(dict(challenge), boundary)
    except FrozenPrepareCancellation:
        raise
    except Exception as exc:
        raise FrozenPrepareWorkerError(
            "controller live authority verifier failed"
        ) from exc
    if not isinstance(raw_response, Mapping):
        raise FrozenPrepareWorkerError(
            "controller live authority verifier returned no mapping"
        )
    response = dict(raw_response)
    for field, value in challenge.items():
        if field not in {"schema", "status"} and response.get(field) != value:
            raise FrozenPrepareWorkerError(
                "controller live authority did not echo its challenge"
            )
    if response.get("challenge_sha256") != _sha256(
        _canonical_json(challenge)
    ):
        raise FrozenPrepareWorkerError(
            "controller live authority challenge digest differs"
        )
    digest = _validate_authority_document(response, context=context)
    return response, digest


def _last_authority_sha256(journal: JournalState) -> str:
    return (
        journal.events[-1]["authority_sha256"]
        if journal.events
        else ZERO_SHA256
    )


def _publication_core_sha256(document: Mapping[str, Any]) -> str:
    core = {
        key: value
        for key, value in document.items()
        if key
        not in {
            "publication_authority",
            "publication_authority_sha256",
        }
    }
    return _sha256(_canonical_json(core) + b"\n")


def _validate_publication_authority(
    document: Mapping[str, Any],
    *,
    context: LoadedRequest,
    kind: str,
    expected_previous_authority_sha256: str,
) -> str:
    authority = document.get("publication_authority")
    observed_digest = _validate_authority_document(
        authority,
        context=context,
    )
    if (
        document.get("publication_authority_sha256") != observed_digest
        or authority["boundary"] != f"publish:{kind}"
        or authority["publication_kind"] != kind
        or authority["publication_payload_sha256"]
        != _publication_core_sha256(document)
        or authority["previous_authority_sha256"]
        != expected_previous_authority_sha256
    ):
        raise FrozenPrepareWorkerError(
            f"{kind} publication authority binding differs"
        )
    return observed_digest


OBSERVATION_FIELDS = frozenset(
    {
        "phase",
        "step",
        "role",
        "source_revision",
        "target_revision",
        "current_revision",
        "database_container_count",
        "oneoff_container_count",
        "network_present",
        "named_volume_count",
        "satisfied",
        "details",
        "business_write_observed",
        "public_or_private_app_started",
        "current_mutated",
        "legacy_mutated",
        "production_traffic_mutated",
        "external_network_contacted",
        "ssh_contacted",
        "object_storage_contacted",
    }
)
EXECUTION_FIELDS = frozenset(
    {
        "step",
        "service",
        "command_invoked",
        "output_sha256",
        "output_bytes",
        "repaired_concurrent_indexes",
        "pull_performed",
        "build_performed",
        "compose_down_performed",
        "volume_mutated",
        "public_or_private_app_started",
        "current_mutated",
        "legacy_mutated",
        "production_traffic_mutated",
        "external_network_contacted",
        "ssh_contacted",
        "object_storage_contacted",
    }
)
STEP_SEMANTIC_FIELDS = frozenset({"observation", "execution"})
CLEANUP_SEMANTIC_FIELDS = frozenset(
    {
        "residue_count",
        "residue_identity_sha256",
        "removed_count",
        "persistent_volume_removed",
        "generation_data_mutated",
    }
)


def _validate_cancellation_cleanup(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != CLEANUP_SEMANTIC_FIELDS
        or type(value.get("residue_count")) is not int
        or type(value.get("removed_count")) is not int
        or value["residue_count"] not in {0, 1}
        or value["removed_count"] != value["residue_count"]
        or (
            value["residue_count"] == 0
            and value["residue_identity_sha256"] is not None
        )
        or (
            value["residue_count"] == 1
            and (
                not isinstance(
                    value["residue_identity_sha256"],
                    str,
                )
                or value["residue_identity_sha256"] == ZERO_SHA256
                or SHA256_RE.fullmatch(
                    value["residue_identity_sha256"]
                )
                is None
            )
        )
        or value["persistent_volume_removed"] is not False
        or value["generation_data_mutated"] is not False
    ):
        raise FrozenPrepareWorkerError(
            "active one-off safety cleanup closure differs"
        )
    return dict(value)


class PrepareBackend(Protocol):
    def observe(self, step: str) -> Mapping[str, Any]:
        """Return one normalized readback for the exact step."""

    def run_step(
        self,
        step: str,
        *,
        attempt: int,
        started_event_sha256: str,
    ) -> Mapping[str, Any]:
        """Execute only the exact operation-owned step."""

    def cancel_active_oneoff(
        self,
        *,
        step: str,
        attempt: int,
        started_event_sha256: str,
    ) -> Mapping[str, Any]:
        """Remove only the exact journal-owned one-off after an aborted run."""


GENERATION_ENV_KEYS = frozenset(
    {
        "PRODUCTION_SHADOW_PROJECT",
        "PRODUCTION_SHADOW_OPERATION_ID",
        "PRODUCTION_SHADOW_PROJECT_ROOT",
        "PRODUCTION_SHADOW_RELEASE_ROOT",
        "PRODUCTION_SHADOW_DATA_ROOT",
        "PRODUCTION_SHADOW_SECRET_ROOT",
        "PRODUCTION_SHADOW_CGROUP_PARENT",
        "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID",
    }
)


class SanitizedDockerRunner:
    """Strip material and inherited process controls before local Docker."""

    def __init__(self, delegate: DockerRunner) -> None:
        self.delegate = delegate

    @staticmethod
    def _environment(value: Mapping[str, str]) -> dict[str, str]:
        if any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in value.items()
        ):
            raise FrozenPrepareWorkerError(
                "Docker environment contains a non-string entry"
            )
        result = {
            key: value[key]
            for key in RESTORE.SAFE_ENV
            if key in value
        }
        if result != RESTORE.SAFE_ENV:
            raise FrozenPrepareWorkerError(
                "Docker environment lacks the fixed safe process controls"
            )
        result.update(
            {
                key: value[key]
                for key in GENERATION_ENV_KEYS
                if key in value
            }
        )
        return result

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str],
        stdin: Any = subprocess.DEVNULL,
    ) -> str:
        return self.delegate.run(
            arguments,
            timeout=timeout,
            env=self._environment(env),
            stdin=stdin,
        )

    def stream(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str],
    ) -> Any:
        return self.delegate.stream(
            arguments,
            timeout=timeout,
            env=self._environment(env),
        )


class DeadlineDockerRunner:
    """Clamp every nested Docker operation to one monotonic deadline."""

    def __init__(
        self,
        delegate: DockerRunner,
        *,
        deadline: float,
    ) -> None:
        if not isinstance(deadline, (int, float)):
            raise FrozenPrepareWorkerError(
                "Docker deadline is invalid"
            )
        self.delegate = delegate
        self.deadline = float(deadline)

    def _timeout(self, requested: int | float) -> float:
        if (
            not isinstance(requested, (int, float))
            or isinstance(requested, bool)
            or requested <= 0
        ):
            raise FrozenPrepareWorkerError(
                "Docker command timeout is invalid"
            )
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise FrozenPrepareWorkerError(
                "Docker operation exceeded its absolute deadline"
            )
        return min(float(requested), remaining)

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str],
        stdin: Any = subprocess.DEVNULL,
    ) -> str:
        return self.delegate.run(
            arguments,
            timeout=self._timeout(timeout),
            env=env,
            stdin=stdin,
        )

    def stream(
        self,
        arguments: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str],
    ) -> Any:
        return self.delegate.stream(
            arguments,
            timeout=self._timeout(timeout),
            env=env,
        )


def _psql_lines(
    raw: str,
    *,
    label: str,
    maximum_line_bytes: int = DEFAULT_MAX_SQL_LINE_BYTES,
) -> list[str]:
    if (
        type(maximum_line_bytes) is not int
        or not 1 <= maximum_line_bytes <= FINGERPRINT_MAX_SQL_LINE_BYTES
    ):
        raise FrozenPrepareWorkerError(
            f"{label} maximum line bound is invalid"
        )
    values = raw.splitlines() if raw else []
    if (
        len(values) > MAX_SQL_ROWS
        or len(values) != len(set(values))
        or any(
            not value
            or len(value.encode("utf-8")) > maximum_line_bytes
            or any(ord(character) < 32 and character != "\t" for character in value)
            for value in values
        )
    ):
        raise FrozenPrepareWorkerError(f"{label} output is invalid")
    return values


class LocalDockerPrepareBackend:
    """Bounded local-only implementation for one generation and role."""

    def __init__(
        self,
        context: LoadedRequest,
        runner: DockerRunner,
    ) -> None:
        self.context = context
        self.manifest = context.manifest
        self.runner = SanitizedDockerRunner(runner)
        blocker = PHASE_EXECUTION_BLOCKERS.get(
            (
                str(context.document["phase"]),
                str(context.document["role"]),
            )
        )
        if blocker is not None:
            raise FrozenPrepareWorkerError(
                "installed immutable prepare contract cannot execute the "
                f"exact controller phase: {blocker}"
            )
        try:
            graph = _load_migration_graph(self.manifest.paths.release_root)
            corridor = _migration_corridor(
                graph,
                source_revision=self.manifest.source_database.alembic_revision,
                target_revision=self.manifest.target_migration_revision,
            )
            concurrent = _concurrent_index_names(graph, corridor)
        except ProductionOperationError as exc:
            raise FrozenPrepareWorkerError(
                "immutable release migration corridor is invalid"
            ) from exc
        self.corridor = tuple(corridor)
        self.concurrent_indexes = tuple(concurrent)
        self._sql_scope: SqlExecutionScope | None = None
        self._sql_contract_cache: RESTORE.DatabaseRuntimeContract | None = None
        self._prepare_contract_cache: dict[
            str,
            tuple[RESTORE.DatabaseRuntimeContract, dict[str, str]],
        ] = {}

    def _bind_sql_scope(
        self,
        *,
        step: str,
        attempt: int,
        started_event_sha256: str,
        stage: str,
    ) -> None:
        expected_steps = {row[0] for row in self.context.steps}
        if (
            step not in expected_steps
            or stage not in SQL_STAGES
            or type(attempt) is not int
            or (
                stage == "pre-start-observe"
                and (
                    attempt != 0
                    or started_event_sha256 != ZERO_SHA256
                )
            )
            or (
                stage != "pre-start-observe"
                and (
                    not 1 <= attempt <= MAX_ATTEMPTS_PER_STEP
                    or SHA256_RE.fullmatch(started_event_sha256) is None
                    or started_event_sha256 == ZERO_SHA256
                )
            )
        ):
            raise FrozenPrepareWorkerError(
                "prepare SQL execution scope is invalid"
            )
        self._sql_scope = SqlExecutionScope(
            step=step,
            attempt=attempt,
            started_event_sha256=started_event_sha256,
            stage=stage,
        )

    def _require_sql_scope(self) -> SqlExecutionScope:
        scope = getattr(self, "_sql_scope", None)
        if not isinstance(scope, SqlExecutionScope):
            raise FrozenPrepareWorkerError(
                "prepare SQL execution scope is not bound"
            )
        return scope

    @staticmethod
    def _sql_command(
        *,
        sql: str,
        sql_kind: str,
        statement_timeout_ms: int,
        lock_timeout_ms: int,
    ) -> tuple[str, ...]:
        if sql_kind == "read-only":
            wrapped = (
                "BEGIN TRANSACTION READ ONLY; "
                "SET LOCAL transaction_read_only TO on; "
                f"SET LOCAL statement_timeout TO '{statement_timeout_ms}ms'; "
                f"SET LOCAL lock_timeout TO '{lock_timeout_ms}ms'; "
                "SET LOCAL search_path TO pg_catalog; "
                f"{sql}; ROLLBACK;"
            )
        elif sql_kind == "drop-reviewed-index":
            return (
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "--no-psqlrc",
                "-Atq",
                "--command",
                "SET transaction_read_only TO off",
                "--command",
                (
                    "SET statement_timeout TO "
                    f"'{statement_timeout_ms}ms'"
                ),
                "--command",
                f"SET lock_timeout TO '{lock_timeout_ms}ms'",
                "--command",
                "SET search_path TO pg_catalog",
                "--command",
                sql,
            )
        else:
            raise FrozenPrepareWorkerError(
                "prepare SQL intent kind is invalid"
            )
        return (
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "--no-psqlrc",
            "-Atqc",
            wrapped,
        )

    def _sql_intent(
        self,
        *,
        sql: str,
        sql_kind: str,
        timeout: int,
        reviewed_index: str | None,
    ) -> tuple[dict[str, Any], Path]:
        scope = self._require_sql_scope()
        try:
            sql_payload = sql.encode("ascii")
        except UnicodeError as exc:
            raise FrozenPrepareWorkerError(
                "prepare SQL is not canonical ASCII"
            ) from exc
        if (
            sql_kind not in SQL_KINDS
            or type(timeout) is not int
            or not 1 <= timeout <= 3600
            or not sql_payload
            or len(sql_payload) > MAX_JSON_BYTES // 2
            or "\x00" in sql
            or ";" in sql
            or "\\" in sql
            or "--" in sql
            or "/*" in sql
            or "*/" in sql
        ):
            raise FrozenPrepareWorkerError(
                "operation database query is invalid"
            )
        if sql_kind == "read-only":
            if (
                re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE)
                is None
                or reviewed_index is not None
            ):
                raise FrozenPrepareWorkerError(
                    "operation database readback is not read-only SQL"
                )
        elif (
            reviewed_index is None
            or reviewed_index not in self.concurrent_indexes
            or ROLE_RE.fullmatch(reviewed_index) is None
            or sql
            != (
                "DROP INDEX CONCURRENTLY IF EXISTS "
                f'public."{reviewed_index}"'
            )
        ):
            raise FrozenPrepareWorkerError(
                "reviewed concurrent index DROP differs"
            )
        statement_timeout_ms = timeout * 1000
        lock_timeout_ms = min(
            SQL_LOCK_TIMEOUT_MS,
            statement_timeout_ms,
        )
        command = self._sql_command(
            sql=sql,
            sql_kind=sql_kind,
            statement_timeout_ms=statement_timeout_ms,
            lock_timeout_ms=lock_timeout_ms,
        )
        core: dict[str, Any] = {
            "schema": SQL_INTENT_SCHEMA,
            "status": "authorized-intent",
            "campaign_id": self.context.document["campaign_id"],
            "operation_id": self.context.document["operation_id"],
            "role": self.context.document["role"],
            "phase": self.context.document["phase"],
            "request_sha256": self.context.sha256,
            "restore_generation_sha256": (
                self.manifest.restore_generation_sha256
            ),
            "step": scope.step,
            "attempt": scope.attempt,
            "started_event_sha256": scope.started_event_sha256,
            "stage": scope.stage,
            "sql_kind": sql_kind,
            "sql": sql,
            "sql_sha256": _sha256(sql_payload),
            "sql_bytes": len(sql_payload),
            "statement_timeout_ms": statement_timeout_ms,
            "lock_timeout_ms": lock_timeout_ms,
            "transaction_read_only": sql_kind == "read-only",
            "reviewed_index": reviewed_index,
            "command": list(command),
        }
        intent_sha256 = _sha256(_canonical_json(core))
        document = {**core, "intent_sha256": intent_sha256}
        if set(document) != SQL_INTENT_FIELDS:
            raise FrozenPrepareWorkerError(
                "internal prepare SQL intent fields differ"
            )
        directory = self.context.output_root / "sql-intents"
        _ensure_private_descendant(
            self.manifest.paths.secret_generation_root,
            directory,
            create=True,
        )
        try:
            names = sorted(path.name for path in directory.iterdir())
        except OSError as exc:
            raise FrozenPrepareWorkerError(
                "prepare SQL intent namespace is unavailable"
            ) from exc
        pattern = re.compile(r"^[0-9a-f]{64}\.json$")
        if (
            len(names) > MAX_SQL_INTENTS
            or any(pattern.fullmatch(name) is None for name in names)
            or (
                len(names) == MAX_SQL_INTENTS
                and f"{intent_sha256}.json" not in names
            )
        ):
            raise FrozenPrepareWorkerError(
                "prepare SQL intent namespace differs"
            )
        path, _payload_sha256, _publication = _persist_new_document(
            directory,
            filename=f"{intent_sha256}.json",
            document=document,
            label="prepare SQL intent",
        )
        return document, path

    def _load_sql_intent(self, intent_sha256: str) -> dict[str, Any]:
        _nonzero_sha256(
            intent_sha256,
            label="prepare SQL intent",
        )
        path = (
            self.context.output_root
            / "sql-intents"
            / f"{intent_sha256}.json"
        )
        document, payload, _payload_sha256 = _read_json(
            path,
            label="prepare SQL intent",
        )
        core = {
            key: value
            for key, value in document.items()
            if key != "intent_sha256"
        }
        sql = document.get("sql")
        command = document.get("command")
        if (
            set(document) != SQL_INTENT_FIELDS
            or document.get("schema") != SQL_INTENT_SCHEMA
            or document.get("status") != "authorized-intent"
            or document.get("campaign_id")
            != self.context.document["campaign_id"]
            or document.get("operation_id")
            != self.context.document["operation_id"]
            or document.get("role") != self.context.document["role"]
            or document.get("phase") != self.context.document["phase"]
            or document.get("request_sha256") != self.context.sha256
            or document.get("restore_generation_sha256")
            != self.manifest.restore_generation_sha256
            or document.get("step")
            not in {row[0] for row in self.context.steps}
            or document.get("stage") not in SQL_STAGES
            or document.get("sql_kind") not in SQL_KINDS
            or not isinstance(sql, str)
            or not isinstance(command, list)
            or any(not isinstance(value, str) for value in command)
            or type(document.get("attempt")) is not int
            or type(document.get("sql_bytes")) is not int
            or type(document.get("statement_timeout_ms")) is not int
            or type(document.get("lock_timeout_ms")) is not int
            or document.get("intent_sha256") != intent_sha256
            or _sha256(_canonical_json(core)) != intent_sha256
            or payload != _canonical_json(document) + b"\n"
        ):
            raise FrozenPrepareWorkerError(
                "prepare SQL intent binding differs"
            )
        try:
            sql_payload = sql.encode("ascii")
        except UnicodeError as exc:
            raise FrozenPrepareWorkerError(
                "prepare SQL intent is not canonical ASCII"
            ) from exc
        stage = document["stage"]
        attempt = document["attempt"]
        started_event_sha256 = document["started_event_sha256"]
        reviewed_index = document["reviewed_index"]
        sql_kind = document["sql_kind"]
        if (
            document["sql_sha256"] != _sha256(sql_payload)
            or document["sql_bytes"] != len(sql_payload)
            or not sql_payload
            or len(sql_payload) > MAX_JSON_BYTES // 2
            or "\x00" in sql
            or ";" in sql
            or "\\" in sql
            or "--" in sql
            or "/*" in sql
            or "*/" in sql
            or not 1 <= document["statement_timeout_ms"] <= 3_600_000
            or not 1 <= document["lock_timeout_ms"] <= SQL_LOCK_TIMEOUT_MS
            or document["lock_timeout_ms"]
            > document["statement_timeout_ms"]
            or document["transaction_read_only"]
            is not (sql_kind == "read-only")
            or (
                stage == "pre-start-observe"
                and (
                    attempt != 0
                    or started_event_sha256 != ZERO_SHA256
                )
            )
            or (
                stage != "pre-start-observe"
                and (
                    not 1 <= attempt <= MAX_ATTEMPTS_PER_STEP
                    or SHA256_RE.fullmatch(
                        str(started_event_sha256)
                    )
                    is None
                    or started_event_sha256 == ZERO_SHA256
                )
            )
            or tuple(command)
            != self._sql_command(
                sql=sql,
                sql_kind=sql_kind,
                statement_timeout_ms=document[
                    "statement_timeout_ms"
                ],
                lock_timeout_ms=document["lock_timeout_ms"],
            )
            or (
                sql_kind == "read-only"
                and (
                    reviewed_index is not None
                    or re.match(
                        r"^\s*(SELECT|WITH)\b",
                        sql,
                        re.IGNORECASE,
                    )
                    is None
                )
            )
            or (
                sql_kind == "drop-reviewed-index"
                and (
                    stage != "step-execution"
                    or document["step"] != "migrate"
                    or not isinstance(reviewed_index, str)
                    or reviewed_index not in self.concurrent_indexes
                    or sql
                    != (
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        f'public."{reviewed_index}"'
                    )
                )
            )
        ):
            raise FrozenPrepareWorkerError(
                "prepare SQL intent content differs"
            )
        return document

    def _sql_oneoff_name(self, intent_sha256: str) -> str:
        _nonzero_sha256(
            intent_sha256,
            label="prepare SQL intent",
        )
        return (
            f"{self.manifest.paths.project_name}-prepare-sql-"
            f"{intent_sha256[:24]}"
        )

    def _sql_production_labels(
        self,
        intent: Mapping[str, Any],
    ) -> dict[str, str]:
        return {
            "trading-bot.production.operation-id": (
                self.context.document["operation_id"]
            ),
            "trading-bot.production.prepare-generation": (
                self.manifest.restore_generation_sha256
            ),
            "trading-bot.production.prepare-phase": (
                self.context.document["phase"]
            ),
            "trading-bot.production.prepare-request": self.context.sha256,
            "trading-bot.production.prepare-step": intent["step"],
            "trading-bot.production.prepare-attempt": str(
                intent["attempt"]
            ),
            "trading-bot.production.prepare-started-event": intent[
                "started_event_sha256"
            ],
            "trading-bot.production.prepare-sql-stage": intent["stage"],
            "trading-bot.production.prepare-sql-kind": intent["sql_kind"],
            "trading-bot.production.prepare-sql-sha256": intent[
                "sql_sha256"
            ],
            "trading-bot.production.prepare-sql-intent": intent[
                "intent_sha256"
            ],
        }

    def _sql_runtime_contract(
        self,
        runner: DockerRunner,
    ) -> RESTORE.DatabaseRuntimeContract:
        cached = getattr(self, "_sql_contract_cache", None)
        if cached is not None:
            return cached
        try:
            contract = RESTORE._service_runtime_contract(
                self.manifest,
                runner,
                service_name=f"{self.manifest.role}_restore_tool",
                expected_restart="no",
            )
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "prepare SQL one-off runtime contract failed closed"
            ) from exc
        self._sql_contract_cache = contract
        return contract

    def _validate_sql_oneoff_runtime(
        self,
        row: Mapping[str, Any],
        *,
        intent: Mapping[str, Any],
        runner: DockerRunner,
    ) -> None:
        contract = self._sql_runtime_contract(runner)
        identifier = row.get("Id")
        config = row.get("Config")
        host = row.get("HostConfig")
        labels = config.get("Labels") if isinstance(config, dict) else None
        network = (
            f"{self.manifest.paths.project_name}_{self.manifest.role}"
        )
        mounts = row.get("Mounts")
        networks = (
            row.get("NetworkSettings", {}).get("Networks")
            if isinstance(row.get("NetworkSettings"), dict)
            else None
        )
        expected_binds = [
            (
                f"{self.manifest.paths.restore_input_root}:"
                "/run/restore-input:ro"
            ),
            f"{self.manifest.paths.uploads}:/run/restore-target/uploads:rw",
            f"{self.manifest.paths.audit}:/run/restore-target/audit:rw",
        ]
        if (
            not isinstance(identifier, str)
            or RESTORE.CONTAINER_ID_RE.fullmatch(identifier) is None
            or not isinstance(config, dict)
            or not isinstance(labels, dict)
            or not isinstance(host, dict)
            or not isinstance(mounts, list)
            or not isinstance(networks, dict)
        ):
            raise FrozenPrepareWorkerError(
                "prepare SQL one-off runtime identity is invalid"
            )
        compose_slug = labels.get("com.docker.compose.slug")
        config_hash = labels.get("com.docker.compose.config-hash")
        if (
            (
                compose_slug is not None
                and (
                    not isinstance(compose_slug, str)
                    or SHA256_RE.fullmatch(compose_slug) is None
                )
            )
            or not isinstance(config_hash, str)
            or SHA256_RE.fullmatch(config_hash) is None
            or config_hash == ZERO_SHA256
        ):
            raise FrozenPrepareWorkerError(
                "prepare SQL one-off Compose identity differs"
            )
        try:
            expected_non_compose_labels = {
                **contract.labels,
                **self._sql_production_labels(intent),
            }
            RESTORE._validate_exact_container_config(
                config,
                container_id=identifier,
                contract=contract,
                command=tuple(intent["command"]),
                environment=contract.environment,
            )
            RESTORE._validate_exact_host_config(
                host,
                binds=expected_binds,
                network_mode=network,
                cgroup_parent=contract.cgroup_parent,
                nano_cpus=contract.nano_cpus,
                memory=contract.memory,
                pids_limit=contract.pids_limit,
                auto_remove=True,
                restart_policy="no",
                log_config=contract.log_config,
            )
            environment = RESTORE._environment_map(
                config.get("Env"),
                label="prepare SQL one-off environment",
            )
            command = RESTORE._string_vector(
                config.get("Cmd"),
                label="prepare SQL one-off command",
            )
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "refusing to recover a non-exact prepare SQL one-off"
            ) from exc
        observed_mounts = {
            (
                mount.get("Type"),
                mount.get("Source"),
                mount.get("Destination"),
                mount.get("RW"),
            )
            for mount in mounts
            if isinstance(mount, dict)
        }
        expected_mounts = {
            (
                "bind",
                str(self.manifest.paths.restore_input_root),
                "/run/restore-input",
                False,
            ),
            (
                "bind",
                str(self.manifest.paths.uploads),
                "/run/restore-target/uploads",
                True,
            ),
            (
                "bind",
                str(self.manifest.paths.audit),
                "/run/restore-target/audit",
                True,
            ),
        }
        compose_labels = {
            key: value
            for key, value in labels.items()
            if key.startswith("com.docker.compose.")
        }
        non_compose_labels = {
            key: value
            for key, value in labels.items()
            if not key.startswith("com.docker.compose.")
        }
        allowed_compose_labels = {
            "com.docker.compose.config-hash": config_hash,
            "com.docker.compose.depends_on": (
                contract.compose_dependencies
            ),
            "com.docker.compose.image": contract.image_id,
            "com.docker.compose.oneoff": "True",
            "com.docker.compose.project": (
                self.manifest.paths.project_name
            ),
            "com.docker.compose.project.config_files": str(
                self.manifest.role_compose_path
            ),
            "com.docker.compose.project.environment_file": str(
                self.manifest.environment_path
            ),
            "com.docker.compose.project.working_dir": str(
                self.manifest.role_compose_path.parent
            ),
            "com.docker.compose.service": contract.service,
            "com.docker.compose.version": contract.compose_version,
        }
        if compose_slug is not None:
            allowed_compose_labels[
                "com.docker.compose.slug"
            ] = compose_slug
        if "com.docker.compose.container-number" in compose_labels:
            allowed_compose_labels[
                "com.docker.compose.container-number"
            ] = "1"
        required_compose_labels = {
            "com.docker.compose.config-hash",
            "com.docker.compose.oneoff",
            "com.docker.compose.project",
            "com.docker.compose.service",
        }
        if (
            row.get("Name")
            != f"/{self._sql_oneoff_name(intent['intent_sha256'])}"
            or row.get("Image") != self.manifest.postgres_image_id
            or config.get("Image") != self.manifest.postgres_image_id
            or non_compose_labels != expected_non_compose_labels
            or not required_compose_labels.issubset(compose_labels)
            or not set(compose_labels).issubset(
                allowed_compose_labels
            )
            or any(
                value != allowed_compose_labels[key]
                for key, value in compose_labels.items()
            )
            or command != tuple(intent["command"])
            or environment != contract.environment
            or set(networks) != {network}
            or len(mounts) != len(expected_mounts)
            or observed_mounts != expected_mounts
        ):
            raise FrozenPrepareWorkerError(
                "refusing to recover a non-exact prepare SQL one-off"
            )

    def _sql_residues(
        self,
        *,
        runner: DockerRunner,
        intent_sha256: str | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        residues: list[tuple[str, dict[str, Any]]] = []
        try:
            identifiers = RESTORE._project_container_ids(
                self.manifest,
                runner,
            )
            for identifier in identifiers:
                row = RESTORE._inspect_container(
                    identifier,
                    self.manifest,
                    runner,
                )
                config = row.get("Config")
                labels = (
                    config.get("Labels")
                    if isinstance(config, dict)
                    else None
                )
                if not isinstance(labels, dict):
                    RESTORE._container_semantics(row, self.manifest)
                    continue
                observed_intent = labels.get(
                    "trading-bot.production.prepare-sql-intent"
                )
                if observed_intent is None:
                    if (
                        labels.get("com.docker.compose.oneoff")
                        != "True"
                    ):
                        RESTORE._container_semantics(
                            row,
                            self.manifest,
                        )
                    continue
                if (
                    labels.get(
                        "trading-bot.production.prepare-request"
                    )
                    != self.context.sha256
                ):
                    continue
                if (
                    not isinstance(observed_intent, str)
                    or SHA256_RE.fullmatch(observed_intent) is None
                    or (
                        intent_sha256 is not None
                        and observed_intent != intent_sha256
                    )
                ):
                    if intent_sha256 is None:
                        raise FrozenPrepareWorkerError(
                            "prepare SQL residue intent label differs"
                        )
                    continue
                intent = self._load_sql_intent(observed_intent)
                self._validate_sql_oneoff_runtime(
                    row,
                    intent=intent,
                    runner=runner,
                )
                residues.append((identifier, intent))
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "prepare SQL one-off inventory failed closed"
            ) from exc
        if len(residues) > 1:
            raise FrozenPrepareWorkerError(
                "prepare SQL intent owns multiple one-off residues"
            )
        return residues

    def _remove_sql_residue(
        self,
        identifier: str,
        *,
        runner: DockerRunner,
    ) -> None:
        boundary = RESTORE._capture_runtime_path_identities(
            self.manifest,
            require_stores=True,
        )
        command_env, _overrides = RESTORE._compose_environment(
            self.manifest
        )
        try:
            runner.run(
                [*RESTORE.DOCKER_BASE, "rm", "--force", identifier],
                timeout=60,
                env=command_env,
            )
            RESTORE._recheck_runtime_path_identities(
                self.manifest,
                boundary,
                require_stores=True,
            )
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "exact prepare SQL one-off cleanup failed closed"
            ) from exc

    def _cleanup_sql_oneoffs(
        self,
        *,
        deadline: float,
        intent_sha256: str | None,
        quiesce: bool,
    ) -> list[str]:
        runner = DeadlineDockerRunner(
            self.runner,
            deadline=deadline,
        )
        removed: list[str] = []
        absent_since: float | None = None
        while True:
            residues = self._sql_residues(
                runner=runner,
                intent_sha256=intent_sha256,
            )
            now = time.monotonic()
            if residues:
                absent_since = None
                identifier = residues[0][0]
                self._remove_sql_residue(
                    identifier,
                    runner=runner,
                )
                if identifier not in removed:
                    removed.append(identifier)
            else:
                if not quiesce:
                    return removed
                if absent_since is None:
                    absent_since = now
                if (
                    now - absent_since
                    >= CANCELLATION_QUIESCENCE_SECONDS
                ):
                    if len(removed) > 1:
                        raise FrozenPrepareWorkerError(
                            "multiple delayed prepare SQL one-offs "
                            "were removed"
                        )
                    return removed
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FrozenPrepareWorkerError(
                    "prepare SQL one-off cleanup did not quiesce"
                )
            time.sleep(min(CANCELLATION_POLL_SECONDS, remaining))

    def _cleanup_sql_with_cancellation_retry(
        self,
        *,
        deadline: float,
        intent_sha256: str | None,
        quiesce: bool,
    ) -> tuple[list[str], FrozenPrepareCancellation | None]:
        interrupted: FrozenPrepareCancellation | None = None
        while True:
            try:
                return (
                    self._cleanup_sql_oneoffs(
                        deadline=deadline,
                        intent_sha256=intent_sha256,
                        quiesce=quiesce,
                    ),
                    interrupted,
                )
            except FrozenPrepareCancellation as exc:
                interrupted = exc
                if time.monotonic() >= deadline:
                    raise FrozenPrepareWorkerError(
                        "prepare SQL cancellation cleanup exceeded "
                        "its deadline"
                    ) from exc

    def recover_sql_oneoffs(self) -> Mapping[str, Any]:
        directory = self.context.output_root / "sql-intents"
        try:
            directory.lstat()
        except FileNotFoundError:
            return {
                "residue_count": 0,
                "residue_identity_sha256": None,
                "removed_count": 0,
                "persistent_volume_removed": False,
                "generation_data_mutated": False,
            }
        except OSError as exc:
            raise FrozenPrepareWorkerError(
                "prepare SQL intent namespace is unavailable"
            ) from exc
        _ensure_private_descendant(
            self.manifest.paths.secret_generation_root,
            directory,
            create=False,
        )
        deadline = time.monotonic() + CANCELLATION_MAX_WAIT_SECONDS
        removed, interrupted = self._cleanup_sql_with_cancellation_retry(
            deadline=deadline,
            intent_sha256=None,
            quiesce=True,
        )
        if interrupted is not None:
            raise interrupted
        return {
            "residue_count": len(removed),
            "residue_identity_sha256": (
                _sha256(_canonical_json(removed)) if removed else None
            ),
            "removed_count": len(removed),
            "persistent_volume_removed": False,
            "generation_data_mutated": False,
        }

    def _assert_drop_authority(
        self,
        *,
        reviewed_index: str,
    ) -> None:
        scope = self._require_sql_scope()
        journal = _load_journal(self.context)
        if (
            scope.stage != "step-execution"
            or scope.step != "migrate"
            or reviewed_index not in self.concurrent_indexes
            or ROLE_RE.fullmatch(reviewed_index) is None
            or journal.active_step != scope.step
            or journal.active_attempt != scope.attempt
            or journal.active_started_sha256
            != scope.started_event_sha256
        ):
            raise FrozenPrepareWorkerError(
                "reviewed concurrent index DROP lacks active authority"
            )

    def _execute_sql(
        self,
        *,
        sql: str,
        sql_kind: str,
        timeout: int,
        reviewed_index: str | None,
    ) -> str:
        if reviewed_index is not None:
            self._assert_drop_authority(
                reviewed_index=reviewed_index,
            )
        intent, _intent_path = self._sql_intent(
            sql=sql,
            sql_kind=sql_kind,
            timeout=timeout,
            reviewed_index=reviewed_index,
        )
        intent_sha256 = intent["intent_sha256"]
        overall_deadline = (
            time.monotonic()
            + timeout
            + SQL_CLEANUP_RESERVE_SECONDS
        )
        execution_deadline = (
            overall_deadline - SQL_CLEANUP_RESERVE_SECONDS
        )
        runner = DeadlineDockerRunner(
            self.runner,
            deadline=execution_deadline,
        )
        command_env, _overrides = RESTORE._compose_environment(
            self.manifest
        )
        boundary = RESTORE._capture_runtime_path_identities(
            self.manifest,
            require_stores=True,
        )
        labels = self._sql_production_labels(intent)
        arguments = [
            *RESTORE._restore_compose_base(self.manifest),
            "run",
            "--rm",
            "--no-deps",
            "--pull",
            "never",
            "--name",
            self._sql_oneoff_name(intent_sha256),
        ]
        for key, value in sorted(labels.items()):
            arguments.extend(("--label", f"{key}={value}"))
        arguments.extend(
            (
                "-T",
                f"{self.manifest.role}_restore_tool",
                *intent["command"],
            )
        )
        try:
            self._sql_runtime_contract(runner)
            if reviewed_index is not None:
                self._assert_drop_authority(
                    reviewed_index=reviewed_index,
                )
            output = runner.run(
                arguments,
                timeout=timeout,
                env=command_env,
            )
            RESTORE._recheck_runtime_path_identities(
                self.manifest,
                boundary,
                require_stores=True,
            )
        except BaseException:
            cleanup_deadline = min(
                overall_deadline,
                (
                    time.monotonic()
                    + CANCELLATION_MAX_WAIT_SECONDS
                ),
            )
            try:
                self._cleanup_sql_with_cancellation_retry(
                    deadline=cleanup_deadline,
                    intent_sha256=intent_sha256,
                    quiesce=True,
                )
            except BaseException as cleanup_exc:
                raise FrozenPrepareWorkerError(
                    "prepare SQL cancellation cleanup failed"
                ) from cleanup_exc
            raise
        cleanup_deadline = min(
            overall_deadline,
            time.monotonic() + CANCELLATION_MAX_WAIT_SECONDS,
        )
        try:
            _removed, interrupted = (
                self._cleanup_sql_with_cancellation_retry(
                    deadline=cleanup_deadline,
                    intent_sha256=intent_sha256,
                    quiesce=False,
                )
            )
        except BaseException as cleanup_exc:
            raise FrozenPrepareWorkerError(
                "prepare SQL completion cleanup failed"
            ) from cleanup_exc
        if interrupted is not None:
            raise interrupted
        return output

    def _psql(self, sql: str, *, timeout: int = 300) -> str:
        try:
            return self._execute_sql(
                sql=sql,
                sql_kind="read-only",
                timeout=timeout,
                reviewed_index=None,
            )
        except FrozenPrepareCancellation:
            raise
        except FrozenPrepareWorkerError:
            raise
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "operation database readback failed closed"
            ) from exc

    def _drop_reviewed_index(
        self,
        name: str,
        *,
        timeout: int = 600,
    ) -> None:
        try:
            self._execute_sql(
                sql=(
                    "DROP INDEX CONCURRENTLY IF EXISTS "
                    f'public."{name}"'
                ),
                sql_kind="drop-reviewed-index",
                timeout=timeout,
                reviewed_index=name,
            )
        except FrozenPrepareCancellation:
            raise
        except FrozenPrepareWorkerError:
            raise
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "reviewed concurrent index DROP failed closed"
            ) from exc

    def _preflight(self) -> dict[str, Any]:
        try:
            RESTORE._verify_image(self.manifest, self.runner)
            resources = dict(
                RESTORE._preflight_generation_resources(
                    self.manifest,
                    self.runner,
                )
            )
            RESTORE._verify_database_healthy(self.manifest, self.runner)
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "frozen generation runtime preflight failed"
            ) from exc
        if (
            resources.get("database_count") != 1
            or resources.get("oneoff_count") != 0
            or resources.get("network_present") is not True
            or resources.get("named_volume_count") != 0
            or resources.get("container_count") != 1
        ):
            raise FrozenPrepareWorkerError(
                "frozen generation contains a non-database runtime surface"
            )
        return resources

    def _revision(self) -> str:
        rows = _psql_lines(
            self._psql(
                "SELECT version_num FROM public.alembic_version"
            ),
            label="Alembic revision",
        )
        if (
            len(rows) != 1
            or REVISION_RE.fullmatch(rows[0]) is None
        ):
            raise FrozenPrepareWorkerError(
                "database Alembic revision inventory is invalid"
            )
        return rows[0]

    def _expected_release_grants(self) -> list[list[str]]:
        site = self.manifest.role
        expected: set[
            tuple[str, str, str, str, str, str, str]
        ] = set()

        def add(
            kind: str,
            object_name: str,
            permissions: str,
            role: str,
            *,
            schema: str = "public",
            subobject: str = "",
        ) -> None:
            if role not in EXPECTED_RUNTIME_ROLES[site]:
                raise FrozenPrepareWorkerError(
                    "release grant policy names a foreign runtime role"
                )
            for privilege in permissions.split(","):
                normalized = privilege.strip().upper()
                if not normalized:
                    raise FrozenPrepareWorkerError(
                        "release grant policy contains an empty privilege"
                    )
                expected.add(
                    (
                        kind,
                        schema,
                        object_name,
                        subobject,
                        normalized,
                        role,
                        "false",
                    )
                )

        relation_rows = _psql_lines(
            self._psql(
                "SELECT concat_ws(E'\\t', class.relname, class.relkind) "
                "FROM pg_class class JOIN pg_namespace namespace "
                "ON namespace.oid=class.relnamespace "
                "WHERE namespace.nspname='public' "
                "AND class.relkind IN ('r','p','v','m','f','S') "
                "ORDER BY class.relname"
            ),
            label="release grant relation inventory",
        )
        tables: set[str] = set()
        sequences: set[str] = set()
        for row in relation_rows:
            fields = row.split("\t")
            if (
                len(fields) != 2
                or ROLE_RE.fullmatch(fields[0]) is None
                or fields[1] not in {"r", "p", "v", "m", "f", "S"}
            ):
                raise FrozenPrepareWorkerError(
                    "release grant relation inventory is invalid"
                )
            (sequences if fields[1] == "S" else tables).add(fields[0])

        receiver_columns = _psql_lines(
            self._psql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='dr_events' "
                "AND column_name<>'source_xid' ORDER BY ordinal_position"
            ),
            label="release receiver column inventory",
        )
        if not receiver_columns or any(
            ROLE_RE.fullmatch(column) is None
            for column in receiver_columns
        ):
            raise FrozenPrepareWorkerError(
                "release receiver column inventory is invalid"
            )
        projection_tables = _psql_lines(
            self._psql(
                "SELECT table_name FROM "
                "public.dr_projection_table_allowlist ORDER BY table_name"
            ),
            label="release projection table inventory",
        )
        canonical_projection_tables = list(C431_POLICY.PROJECTION_TABLES)
        if (
            canonical_projection_tables
            != sorted(set(canonical_projection_tables))
            or any(
                ROLE_RE.fullmatch(table) is None
                for table in canonical_projection_tables
            )
            or projection_tables != canonical_projection_tables
        ):
            raise FrozenPrepareWorkerError(
                "release projection table allowlist differs from policy"
            )
        projection_table_literals = ",".join(
            f"'{table}'" for table in canonical_projection_tables
        )
        release_projection_rows = _psql_lines(
            self._psql(
                "SELECT concat_ws(E'\\t', table_name, column_name) "
                "FROM information_schema.columns "
                "WHERE table_schema='public' "
                f"AND table_name IN ({projection_table_literals}) "
                "ORDER BY table_name, ordinal_position"
            ),
            label="release projection column policy source",
        )
        release_projection_columns: dict[str, list[str]] = {
            table: [] for table in canonical_projection_tables
        }
        for row in release_projection_rows:
            fields = row.split("\t")
            if (
                len(fields) != 2
                or fields[0] not in release_projection_columns
                or ROLE_RE.fullmatch(fields[1]) is None
            ):
                raise FrozenPrepareWorkerError(
                    "release projection column policy source is invalid"
                )
            release_projection_columns[fields[0]].append(fields[1])
        if any(
            not columns
            for columns in release_projection_columns.values()
        ):
            raise FrozenPrepareWorkerError(
                "release projection policy names a missing table"
            )
        forbidden_projection_fields = set(
            C431_POLICY.PROJECTION_FORBIDDEN_FIELDS
        )
        if any(
            not isinstance(pair, tuple)
            or len(pair) != 2
            or pair[0] not in release_projection_columns
            or ROLE_RE.fullmatch(pair[1]) is None
            for pair in forbidden_projection_fields
        ):
            raise FrozenPrepareWorkerError(
                "release projection forbidden-field policy is invalid"
            )
        canonical_projection_fields = sorted(
            (table, column)
            for table, columns in release_projection_columns.items()
            for column in columns
            if (table, column) not in forbidden_projection_fields
        )
        observed_projection_field_rows = _psql_lines(
            self._psql(
                "SELECT concat_ws(E'\\t', table_name, column_name) "
                "FROM public.dr_projection_field_allowlist "
                "ORDER BY table_name, column_name"
            ),
            label="release projection field allowlist",
        )
        observed_projection_fields: list[tuple[str, str]] = []
        for row in observed_projection_field_rows:
            fields = row.split("\t")
            if (
                len(fields) != 2
                or ROLE_RE.fullmatch(fields[0]) is None
                or ROLE_RE.fullmatch(fields[1]) is None
            ):
                raise FrozenPrepareWorkerError(
                    "release projection field allowlist is invalid"
                )
            observed_projection_fields.append(
                (fields[0], fields[1])
            )
        if observed_projection_fields != canonical_projection_fields:
            raise FrozenPrepareWorkerError(
                "release projection field allowlist differs from policy"
            )
        canonical_projection_columns: dict[str, list[str]] = {
            table: [] for table in canonical_projection_tables
        }
        for table, column in canonical_projection_fields:
            canonical_projection_columns[table].append(column)

        def projection_columns(table: str) -> list[str]:
            try:
                return list(canonical_projection_columns[table])
            except KeyError as exc:
                raise FrozenPrepareWorkerError(
                    "release projection policy names a foreign table"
                ) from exc

        cleanup_rows = _psql_lines(
            self._psql(
                "SELECT format('%I.%I(%s)', namespace.nspname, "
                "procedure.proname, "
                "pg_get_function_identity_arguments(procedure.oid)) "
                "FROM pg_proc procedure JOIN pg_namespace namespace "
                "ON namespace.oid=procedure.pronamespace "
                "WHERE procedure.oid=to_regprocedure("
                "'public.trading_bot_cleanup_expired_replay_nonces"
                "(timestamptz,integer)')"
            ),
            label="release cleanup function inventory",
        )
        database_rows = _psql_lines(
            self._psql("SELECT current_database()"),
            label="release database identity",
        )
        if len(cleanup_rows) != 1 or len(database_rows) != 1:
            raise FrozenPrepareWorkerError(
                "release grant function or database identity is invalid"
            )
        cleanup_function = cleanup_rows[0]
        database_name = database_rows[0]

        if site == "bot_fi":
            application_role = "bot_fi_app"
            projection_role = "bot_fi_projection"
            observer_role = "bot_fi_observer"
            service_roles = {
                "receiver": "bot_fi_receiver",
                "delivery": "bot_fi_delivery",
                "projector": projection_role,
            }
            for sequence in sequences:
                add(
                    "sequence",
                    sequence,
                    "USAGE, SELECT",
                    application_role,
                )
                add(
                    "sequence",
                    sequence,
                    "USAGE, SELECT",
                    projection_role,
                )
            add("table", "alembic_version", "SELECT", application_role)
            for table in BOT_GRANTS.CONVERGENCE_OBSERVER_TABLES:
                add("table", table, "SELECT", observer_role)
            for role in service_roles.values():
                for table in (
                    "alembic_version",
                    "dr_database_runtime",
                    "dr_projection_service_roles",
                ):
                    add("table", table, "SELECT", role)
            for table in (
                BOT_GRANTS.BOT_PRODUCT_TABLES
                | BOT_GRANTS.BOT_LOCAL_APPLICATION_TABLES
            ):
                add(
                    "table",
                    table,
                    "SELECT, INSERT, UPDATE, DELETE",
                    application_role,
                )
            for table, permissions in (
                BOT_GRANTS.BOT_LOCAL_QUEUE_APPLICATION_GRANTS.items()
            ):
                add("table", table, permissions, application_role)
            for table, permissions in (
                BOT_GRANTS.BOT_APPLICATION_INTERNAL_GRANTS.items()
            ):
                add("table", table, permissions, application_role)
            for scope, grants in BOT_GRANTS.BOT_DR_SERVICE_GRANTS.items():
                for table, permissions in grants.items():
                    add(
                        "table",
                        table,
                        permissions,
                        service_roles[scope],
                    )
            for column in receiver_columns:
                add(
                    "column",
                    "dr_events",
                    "INSERT",
                    service_roles["receiver"],
                    subobject=column,
                )
            if not BOT_GRANTS.BOT_PRODUCT_TABLES.issubset(
                set(canonical_projection_tables)
            ):
                raise FrozenPrepareWorkerError(
                    "Bot projection release policy is incomplete"
                )
            for table in sorted(BOT_GRANTS.BOT_PRODUCT_TABLES):
                columns = projection_columns(table)
                if not columns:
                    continue
                add("table", table, "DELETE", projection_role)
                for column in columns:
                    add(
                        "column",
                        table,
                        "SELECT, INSERT, UPDATE",
                        projection_role,
                        subobject=column,
                    )
            add(
                "routine",
                cleanup_function,
                "EXECUTE",
                projection_role,
            )
            all_roles = EXPECTED_RUNTIME_ROLES[site]
        else:
            application_role = f"{site}_app"
            projection_role = f"{site}_projection"
            control_role = f"{site}_control"
            observer_role = f"{site}_observer"
            service_roles = {
                "receiver": f"{site}_receiver",
                "delivery": f"{site}_delivery",
                "projector": projection_role,
                "blob": f"{site}_blob",
                "effect": f"{site}_effect",
            }
            for table in tables:
                add("table", table, "SELECT", application_role)
            for sequence in sequences:
                add(
                    "sequence",
                    sequence,
                    "USAGE, SELECT",
                    application_role,
                )
                add(
                    "sequence",
                    sequence,
                    "USAGE, SELECT",
                    projection_role,
                )
            for table in (
                set(EXPECTED_WRITER_TRIGGER_TABLES)
                - WEB_GRANTS.APPLICATION_WRITE_EXCLUDED_TABLES
            ):
                add(
                    "table",
                    table,
                    "INSERT, UPDATE, DELETE",
                    application_role,
                )
            add("table", "dr_database_runtime", "SELECT", control_role)
            add(
                "table",
                "dr_durability_state",
                "SELECT, UPDATE",
                control_role,
            )
            add(
                "table",
                "webapp_writer_state",
                "SELECT, UPDATE",
                control_role,
            )
            add(
                "table",
                "webapp_writer_transitions",
                "SELECT, INSERT",
                control_role,
            )
            add(
                "table",
                "webapp_writer_activation_operations",
                "SELECT, INSERT, UPDATE",
                control_role,
            )
            for table in WEB_GRANTS.CONVERGENCE_OBSERVER_TABLES:
                add("table", table, "SELECT", observer_role)
            for role in service_roles.values():
                for table in (
                    "alembic_version",
                    "dr_database_runtime",
                    "dr_projection_service_roles",
                    "dr_durability_state",
                    "webapp_writer_state",
                ):
                    add("table", table, "SELECT", role)
            for table, permissions in (
                WEB_GRANTS.APPLICATION_INTERNAL_GRANTS.items()
            ):
                add("table", table, permissions, application_role)
            for scope, grants in WEB_GRANTS.DR_SERVICE_INTERNAL_GRANTS.items():
                for table, permissions in grants.items():
                    add(
                        "table",
                        table,
                        permissions,
                        service_roles[scope],
                    )
            for column in receiver_columns:
                add(
                    "column",
                    "dr_events",
                    "INSERT",
                    service_roles["receiver"],
                    subobject=column,
                )
            for column in (
                "id",
                "content_hash",
                "size",
                "mime_type",
                "created_at",
                "s3_key",
            ):
                add(
                    "column",
                    "chat_files",
                    "SELECT",
                    service_roles["blob"],
                    subobject=column,
                )
            add(
                "column",
                "chat_files",
                "UPDATE",
                service_roles["blob"],
                subobject="s3_key",
            )
            for table in canonical_projection_tables:
                if table in WEB_GRANTS.PROJECTOR_INTERNAL_TABLES:
                    continue
                columns = projection_columns(table)
                if not columns:
                    continue
                add("table", table, "DELETE", projection_role)
                for column in columns:
                    add(
                        "column",
                        table,
                        "SELECT, INSERT, UPDATE",
                        projection_role,
                        subobject=column,
                    )
            add(
                "routine",
                cleanup_function,
                "EXECUTE",
                service_roles["projector"],
            )
            all_roles = {
                application_role,
                control_role,
                observer_role,
                *service_roles.values(),
            }
        for role in all_roles:
            add(
                "database",
                database_name,
                "CONNECT",
                role,
                schema="",
            )
            add("schema", "public", "USAGE", role)
        return [list(row) for row in sorted(expected)]

    def _role_inventory(self) -> dict[str, Any]:
        roles = EXPECTED_RUNTIME_ROLES[self.manifest.role]
        literals = ",".join(f"'{role}'" for role in roles)
        rows = _psql_lines(
            self._psql(
                "SELECT concat_ws(E'\\t', role.rolname, "
                "role.rolcanlogin::text, role.rolinherit::text, "
                "role.rolsuper::text, role.rolcreaterole::text, "
                "role.rolcreatedb::text, role.rolreplication::text, "
                "role.rolbypassrls::text, "
                "(SELECT count(*) FROM pg_auth_members membership "
                " WHERE membership.roleid=role.oid "
                " OR membership.member=role.oid)::text, "
                "(SELECT count(*) FROM pg_class object "
                " WHERE object.relowner=role.oid)::text, "
                "(SELECT count(*) FROM pg_proc procedure "
                " WHERE procedure.proowner=role.oid)::text, "
                "coalesce(cardinality(role.rolconfig),0)::text, "
                "(SELECT count(*) FROM pg_db_role_setting setting "
                " WHERE setting.setrole=role.oid)::text, "
                "((SELECT count(*) FROM pg_namespace namespace "
                "  WHERE namespace.nspowner=role.oid) + "
                " (SELECT count(*) FROM pg_database database_row "
                "  WHERE database_row.datdba=role.oid))::text, "
                "(SELECT count(*) FROM pg_type type_row "
                " WHERE type_row.typowner=role.oid)::text, "
                "(SELECT count(*) FROM pg_tablespace tablespace "
                " WHERE tablespace.spcowner=role.oid)::text, "
                "(SELECT count(*) FROM pg_language language "
                " WHERE language.lanowner=role.oid)::text, "
                "(SELECT count(*) FROM pg_largeobject_metadata large_object "
                " WHERE large_object.lomowner=role.oid)::text, "
                "((SELECT count(*) FROM pg_foreign_data_wrapper wrapper "
                "  WHERE wrapper.fdwowner=role.oid) + "
                " (SELECT count(*) FROM pg_foreign_server server "
                "  WHERE server.srvowner=role.oid))::text, "
                "((SELECT count(*) FROM pg_event_trigger event_trigger "
                "  WHERE event_trigger.evtowner=role.oid) + "
                " (SELECT count(*) FROM pg_extension extension "
                "  WHERE extension.extowner=role.oid) + "
                " (SELECT count(*) FROM pg_publication publication "
                "  WHERE publication.pubowner=role.oid) + "
                " (SELECT count(*) FROM pg_subscription subscription "
                "  WHERE subscription.subowner=role.oid) + "
                " (SELECT count(*) FROM pg_statistic_ext statistics "
                "  WHERE statistics.stxowner=role.oid) + "
                " (SELECT count(*) FROM pg_collation collation "
                "  WHERE collation.collowner=role.oid) + "
                " (SELECT count(*) FROM pg_conversion conversion "
                "  WHERE conversion.conowner=role.oid) + "
                " (SELECT count(*) FROM pg_operator operator "
                "  WHERE operator.oprowner=role.oid) + "
                " (SELECT count(*) FROM pg_opclass operator_class "
                "  WHERE operator_class.opcowner=role.oid) + "
                " (SELECT count(*) FROM pg_opfamily operator_family "
                "  WHERE operator_family.opfowner=role.oid) + "
                " (SELECT count(*) FROM pg_ts_config configuration "
                "  WHERE configuration.cfgowner=role.oid) + "
                " (SELECT count(*) FROM pg_ts_dict dictionary "
                "  WHERE dictionary.dictowner=role.oid) + "
                " (SELECT count(*) FROM pg_default_acl defaults "
                "  WHERE defaults.defaclrole=role.oid) + "
                " (SELECT count(*) FROM pg_user_mapping mapping "
                "  WHERE mapping.umuser=role.oid))::text, "
                "(SELECT count(*) FROM pg_shdepend dependency "
                " WHERE dependency.refclassid='pg_authid'::regclass "
                " AND dependency.refobjid=role.oid "
                " AND dependency.deptype='o')::text, "
                "role.rolconnlimit::text, "
                "coalesce(role.rolvaliduntil::text,'infinity')) "
                "FROM pg_roles role "
                f"WHERE role.rolname IN ({literals}) ORDER BY role.rolname"
            ),
            label="runtime role inventory",
        )
        parsed: list[dict[str, Any]] = []
        excessive = 0
        for row in rows:
            fields = row.split("\t")
            if (
                len(fields) != 23
                or fields[0] not in roles
                or any(
                    value not in {"true", "false", "t", "f"}
                    for value in fields[1:8]
                )
                or any(not value.isdigit() for value in fields[8:21])
                or fields[21] != "-1"
                or fields[22] != "infinity"
            ):
                raise FrozenPrepareWorkerError(
                    "runtime role inventory row is invalid"
                )
            flags = [value in {"true", "t"} for value in fields[1:8]]
            closed = (
                flags[0]
                and not flags[1]
                and not any(flags[2:])
                and int(fields[8]) == 0
                and int(fields[9]) == 0
                and int(fields[10]) == 0
                and int(fields[11]) == 0
                and int(fields[12]) == 0
                and int(fields[13]) == 0
                and int(fields[14]) == 0
                and int(fields[15]) == 0
                and int(fields[16]) == 0
                and int(fields[17]) == 0
                and int(fields[18]) == 0
                and int(fields[19]) == 0
                and int(fields[20]) == 0
                and fields[21] == "-1"
                and fields[22] == "infinity"
            )
            if not closed:
                excessive += 1
            parsed.append(
                {
                    "role": fields[0],
                    "login": flags[0],
                    "inherit": flags[1],
                    "superuser": flags[2],
                    "create_role": flags[3],
                    "create_database": flags[4],
                    "replication": flags[5],
                    "bypass_rls": flags[6],
                    "membership_path_count": int(fields[8]),
                    "owned_object_count": int(fields[9]),
                    "owned_function_count": int(fields[10]),
                    "role_configuration_count": int(fields[11]),
                    "database_role_setting_count": int(fields[12]),
                    "owned_database_schema_count": int(fields[13]),
                    "owned_type_count": int(fields[14]),
                    "owned_tablespace_count": int(fields[15]),
                    "owned_language_count": int(fields[16]),
                    "owned_large_object_count": int(fields[17]),
                    "owned_foreign_data_count": int(fields[18]),
                    "owned_extended_catalog_count": int(fields[19]),
                    "shared_owner_dependency_count": int(fields[20]),
                    "connection_limit": int(fields[21]),
                    "valid_until": fields[22],
                    "closed": closed,
                }
            )
        observed_roles = tuple(row["role"] for row in parsed)
        missing = sorted(set(roles) - set(observed_roles))
        if len(observed_roles) != len(set(observed_roles)):
            raise FrozenPrepareWorkerError(
                "runtime role inventory is duplicate"
            )
        grant_rows = _psql_lines(
            self._psql(
                "SELECT concat_ws(E'\\t', kind, object_schema, "
                "object_name, subobject_name, privilege_type, grantee, "
                "is_grantable::text) "
                "FROM ("
                " SELECT CASE WHEN class.relkind='S' "
                " THEN 'sequence' ELSE 'table' END kind, "
                " namespace.nspname object_schema, class.relname object_name, "
                " ''::text subobject_name, acl.privilege_type, "
                " grantee.rolname grantee, acl.is_grantable "
                " FROM pg_class class "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=class.relnamespace "
                " CROSS JOIN LATERAL aclexplode(coalesce(class.relacl, "
                " acldefault(CASE WHEN class.relkind='S' "
                " THEN 's'::\"char\" ELSE 'r'::\"char\" END, "
                " class.relowner))) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                " WHERE class.relkind IN ('r','p','v','m','f','S') "
                f" AND grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'column', namespace.nspname, class.relname, "
                " attribute.attname, acl.privilege_type, grantee.rolname, "
                " acl.is_grantable "
                " FROM pg_attribute attribute "
                " JOIN pg_class class ON class.oid=attribute.attrelid "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=class.relnamespace "
                " CROSS JOIN LATERAL aclexplode(attribute.attacl) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                " WHERE attribute.attnum>0 AND NOT attribute.attisdropped "
                f" AND grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'routine', namespace.nspname, "
                " format('%I.%I(%s)', namespace.nspname, procedure.proname, "
                " pg_get_function_identity_arguments(procedure.oid)), '', "
                " acl.privilege_type, grantee.rolname, acl.is_grantable "
                " FROM pg_proc procedure "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=procedure.pronamespace "
                " CROSS JOIN LATERAL aclexplode(coalesce(procedure.proacl, "
                " acldefault('f', procedure.proowner))) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'type', namespace.nspname, type_row.typname, '', "
                " acl.privilege_type, grantee.rolname, acl.is_grantable "
                " FROM pg_type type_row "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=type_row.typnamespace "
                " CROSS JOIN LATERAL aclexplode(coalesce(type_row.typacl, "
                " acldefault('T', type_row.typowner))) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'database', '', database_row.datname, '', "
                " acl.privilege_type, grantee.rolname, acl.is_grantable "
                " FROM pg_database database_row "
                " CROSS JOIN LATERAL aclexplode("
                "  coalesce(database_row.datacl, "
                "  acldefault('d', database_row.datdba))) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'schema', namespace.nspname, namespace.nspname, '', "
                " acl.privilege_type, grantee.rolname, acl.is_grantable "
                " FROM pg_namespace namespace "
                " CROSS JOIN LATERAL aclexplode("
                "  coalesce(namespace.nspacl, "
                "  acldefault('n', namespace.nspowner))) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'tablespace', '', tablespace.spcname, '', "
                " acl.privilege_type, grantee.rolname, acl.is_grantable "
                " FROM pg_tablespace tablespace "
                " CROSS JOIN LATERAL aclexplode(coalesce(tablespace.spcacl, "
                " acldefault('t', tablespace.spcowner))) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'language', 'pg_catalog', language.lanname, '', "
                " acl.privilege_type, grantee.rolname, acl.is_grantable "
                " FROM pg_language language "
                " CROSS JOIN LATERAL aclexplode(coalesce(language.lanacl, "
                " acldefault('l', language.lanowner))) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'foreign-data-wrapper', 'pg_catalog', "
                " wrapper.fdwname, '', acl.privilege_type, "
                " grantee.rolname, acl.is_grantable "
                " FROM pg_foreign_data_wrapper wrapper "
                " CROSS JOIN LATERAL aclexplode(coalesce(wrapper.fdwacl, "
                " acldefault('F', wrapper.fdwowner))) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'foreign-server', 'pg_catalog', server.srvname, '', "
                " acl.privilege_type, grantee.rolname, acl.is_grantable "
                " FROM pg_foreign_server server "
                " CROSS JOIN LATERAL aclexplode(coalesce(server.srvacl, "
                " acldefault('S', server.srvowner))) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'large-object', 'pg_catalog', "
                " large_object.oid::text, '', acl.privilege_type, "
                " grantee.rolname, acl.is_grantable "
                " FROM pg_largeobject_metadata large_object "
                " CROSS JOIN LATERAL aclexplode(coalesce("
                " large_object.lomacl, "
                " acldefault('L', large_object.lomowner))) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'parameter', 'pg_catalog', parameter.parname, '', "
                " acl.privilege_type, grantee.rolname, acl.is_grantable "
                " FROM pg_parameter_acl parameter "
                " CROSS JOIN LATERAL aclexplode(parameter.paracl) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'default', coalesce(namespace.nspname,''), "
                " owner.rolname || ':' || defaults.defaclobjtype, '', "
                " acl.privilege_type, grantee.rolname, acl.is_grantable "
                " FROM pg_default_acl defaults "
                " JOIN pg_roles owner ON owner.oid=defaults.defaclrole "
                " LEFT JOIN pg_namespace namespace "
                " ON namespace.oid=defaults.defaclnamespace "
                " CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl "
                " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                f" WHERE grantee.rolname IN ({literals})"
                " UNION ALL "
                " SELECT 'membership-in', '', parent.rolname, "
                " membership.admin_option::text, "
                " 'MEMBERSHIP', member.rolname, false "
                " FROM pg_auth_members membership "
                " JOIN pg_roles parent ON parent.oid=membership.roleid "
                " JOIN pg_roles member ON member.oid=membership.member "
                f" WHERE member.rolname IN ({literals}) "
                " UNION ALL "
                " SELECT 'membership-out', '', member.rolname, "
                " membership.admin_option::text, "
                " 'MEMBERSHIP', parent.rolname, false "
                " FROM pg_auth_members membership "
                " JOIN pg_roles parent ON parent.oid=membership.roleid "
                " JOIN pg_roles member ON member.oid=membership.member "
                f" WHERE parent.rolname IN ({literals})"
                ") grants ORDER BY kind, object_schema, object_name, "
                "subobject_name, privilege_type, grantee"
            ),
            label="runtime grant inventory",
        )
        normalized_grants: list[list[str]] = []
        for row in grant_rows:
            fields = row.split("\t")
            if (
                len(fields) != 7
                or fields[0]
                not in {
                    "table",
                    "sequence",
                    "column",
                    "routine",
                    "type",
                    "database",
                    "schema",
                    "tablespace",
                    "language",
                    "foreign-data-wrapper",
                    "foreign-server",
                    "large-object",
                    "parameter",
                    "default",
                    "membership-in",
                    "membership-out",
                }
                or not all(fields[index] for index in (0, 2, 4, 5))
                or fields[5] not in roles
                or fields[6] not in {"true", "false", "t", "f"}
            ):
                raise FrozenPrepareWorkerError(
                    "runtime direct grant inventory row is invalid"
                )
            fields[6] = (
                "true" if fields[6] in {"true", "t"} else "false"
            )
            fields[4] = fields[4].upper()
            normalized_grants.append(fields)
        normalized_grants.sort(key=tuple)
        public_rows = _psql_lines(
            self._psql(
                "SELECT concat_ws(E'\\t', kind, object_schema, identity, "
                "privilege, is_grantable::text) FROM ("
                " SELECT CASE WHEN class.relkind='S' "
                " THEN 'sequence' ELSE 'relation' END kind, "
                " namespace.nspname object_schema, class.relname identity, "
                " acl.privilege_type privilege, acl.is_grantable "
                " FROM pg_class class "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=class.relnamespace "
                " CROSS JOIN LATERAL aclexplode(coalesce(class.relacl, "
                " acldefault(CASE WHEN class.relkind='S' "
                " THEN 's'::\"char\" ELSE 'r'::\"char\" END, "
                " class.relowner))) acl "
                " WHERE class.relkind IN ('r','p','v','m','f','S') "
                " AND acl.grantee=0 "
                " UNION ALL "
                " SELECT 'column', namespace.nspname, "
                " class.relname || '.' || attribute.attname, "
                " acl.privilege_type, acl.is_grantable "
                " FROM pg_attribute attribute "
                " JOIN pg_class class ON class.oid=attribute.attrelid "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=class.relnamespace "
                " CROSS JOIN LATERAL aclexplode(attribute.attacl) acl "
                " WHERE attribute.attnum>0 AND NOT attribute.attisdropped "
                " AND acl.grantee=0 "
                " UNION ALL "
                " SELECT CASE WHEN procedure.prosecdef "
                " THEN 'security-definer-function' ELSE 'function' END, "
                " namespace.nspname, "
                " format('%I(%s)', procedure.proname, "
                " pg_get_function_identity_arguments(procedure.oid)), "
                " acl.privilege_type, acl.is_grantable "
                " FROM pg_proc procedure "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=procedure.pronamespace "
                " CROSS JOIN LATERAL aclexplode(coalesce(procedure.proacl, "
                " acldefault('f', procedure.proowner))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 'type', namespace.nspname, type_row.typname, "
                " acl.privilege_type, acl.is_grantable "
                " FROM pg_type type_row "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=type_row.typnamespace "
                " CROSS JOIN LATERAL aclexplode(coalesce(type_row.typacl, "
                " acldefault('T', type_row.typowner))) acl "
                " WHERE type_row.typisdefined AND acl.grantee=0 "
                " UNION ALL "
                " SELECT 'schema', namespace.nspname, namespace.nspname, "
                " acl.privilege_type, acl.is_grantable "
                " FROM pg_namespace namespace "
                " CROSS JOIN LATERAL aclexplode("
                "  coalesce(namespace.nspacl, "
                "  acldefault('n', namespace.nspowner))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 'database', '', database_row.datname, "
                " acl.privilege_type, acl.is_grantable "
                " FROM pg_database database_row "
                " CROSS JOIN LATERAL aclexplode("
                "  coalesce(database_row.datacl, "
                "  acldefault('d', database_row.datdba))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 'tablespace', '', tablespace.spcname, "
                " acl.privilege_type, acl.is_grantable "
                " FROM pg_tablespace tablespace "
                " CROSS JOIN LATERAL aclexplode(coalesce(tablespace.spcacl, "
                " acldefault('t', tablespace.spcowner))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT CASE WHEN language.lanpltrusted "
                " THEN 'trusted-language' ELSE 'untrusted-language' END, "
                " 'pg_catalog', language.lanname, "
                " acl.privilege_type, acl.is_grantable "
                " FROM pg_language language "
                " CROSS JOIN LATERAL aclexplode(CASE "
                "  WHEN language.lanpltrusted "
                "  THEN coalesce(language.lanacl, "
                "   acldefault('l', language.lanowner)) "
                "  ELSE coalesce(language.lanacl, '{}'::aclitem[]) "
                " END) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 'foreign-data-wrapper', 'pg_catalog', "
                " wrapper.fdwname, acl.privilege_type, acl.is_grantable "
                " FROM pg_foreign_data_wrapper wrapper "
                " CROSS JOIN LATERAL aclexplode(coalesce(wrapper.fdwacl, "
                " acldefault('F', wrapper.fdwowner))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 'foreign-server', 'pg_catalog', server.srvname, "
                " acl.privilege_type, acl.is_grantable "
                " FROM pg_foreign_server server "
                " CROSS JOIN LATERAL aclexplode(coalesce(server.srvacl, "
                " acldefault('S', server.srvowner))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 'large-object', 'pg_catalog', "
                " large_object.oid::text, acl.privilege_type, "
                " acl.is_grantable "
                " FROM pg_largeobject_metadata large_object "
                " CROSS JOIN LATERAL aclexplode(coalesce("
                " large_object.lomacl, "
                " acldefault('L', large_object.lomowner))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 'parameter', 'pg_catalog', parameter.parname, "
                " acl.privilege_type, acl.is_grantable "
                " FROM pg_parameter_acl parameter "
                " CROSS JOIN LATERAL aclexplode(parameter.paracl) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 'user-mapping', 'pg_catalog', server.srvname, "
                " 'USAGE', false "
                " FROM pg_user_mapping mapping "
                " JOIN pg_foreign_server server "
                " ON server.oid=mapping.srvid "
                " WHERE mapping.umuser=0 "
                " UNION ALL "
                " SELECT 'default', coalesce(namespace.nspname,''), "
                " owner.rolname || ':' || defaults.defaclobjtype "
                " || ':' || coalesce(namespace.nspname,''), "
                " acl.privilege_type, acl.is_grantable "
                " FROM pg_default_acl defaults "
                " JOIN pg_roles owner ON owner.oid=defaults.defaclrole "
                " LEFT JOIN pg_namespace namespace "
                " ON namespace.oid=defaults.defaclnamespace "
                " CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl "
                " WHERE acl.grantee=0"
                ") public_acl_inventory "
                "ORDER BY kind, object_schema, identity, privilege, "
                "is_grantable"
            ),
            label="PUBLIC privilege inventory",
        )
        parsed_public: list[list[str]] = []
        unsafe_public: list[list[str]] = []
        for row in public_rows:
            fields = row.split("\t")
            if (
                len(fields) != 5
                or fields[0]
                not in {
                    "relation",
                    "sequence",
                    "column",
                    "function",
                    "security-definer-function",
                    "type",
                    "schema",
                    "database",
                    "tablespace",
                    "trusted-language",
                    "untrusted-language",
                    "foreign-data-wrapper",
                    "foreign-server",
                    "large-object",
                    "parameter",
                    "user-mapping",
                    "default",
                }
                or not all(fields[index] for index in (0, 2, 3))
                or fields[4] not in {"true", "false", "t", "f"}
            ):
                raise FrozenPrepareWorkerError(
                    "PUBLIC privilege inventory row is invalid"
                )
            fields[3] = fields[3].upper()
            fields[4] = (
                "true" if fields[4] in {"true", "t"} else "false"
            )
            parsed_public.append(fields)
            kind, namespace, identity, privilege, is_grantable = fields
            system_schema = (
                namespace.startswith("pg_")
                or namespace == "information_schema"
            )
            unsafe = (
                is_grantable == "true"
                or
                (
                    kind
                    in {
                        "relation",
                        "sequence",
                        "column",
                        "function",
                        "security-definer-function",
                    }
                    and not system_schema
                )
                or kind
                in {
                    "foreign-data-wrapper",
                    "foreign-server",
                    "large-object",
                    "parameter",
                    "tablespace",
                    "untrusted-language",
                    "user-mapping",
                }
                or kind == "database"
                or (
                    kind == "schema"
                    and (
                        identity == "public"
                        or not system_schema
                        or privilege != "USAGE"
                    )
                )
                or kind == "trusted-language"
                or (kind == "type" and privilege != "USAGE")
                or kind == "default"
            )
            if unsafe:
                unsafe_public.append(fields)
        parsed_public.sort(key=tuple)
        unsafe_public.sort(key=tuple)
        phase = str(
            getattr(
                getattr(self, "context", None),
                "document",
                {},
            ).get("phase", "")
        )
        post_phase = phase in {
            "shadow_roles_post_migration",
            "shadow_fence",
        }
        grant_policy_delta_count = len(normalized_grants)
        exact_public_type_usage_verified = False
        exact_release_grant_policy_verified = False
        if post_phase:
            expected_grants = self._expected_release_grants()
            grant_policy_delta_count = len(
                set(map(tuple, normalized_grants))
                ^ set(map(tuple, expected_grants))
            )
            type_names = _psql_lines(
                self._psql(
                    "SELECT type_row.typname FROM pg_type type_row "
                    "JOIN pg_namespace namespace "
                    "ON namespace.oid=type_row.typnamespace "
                    "WHERE namespace.nspname='public' "
                    "AND type_row.typisdefined "
                    "ORDER BY type_row.typname"
                ),
                label="defined PUBLIC type inventory",
            )
            expected_public_type_usage = [
                ["type", "public", type_name, "USAGE", "false"]
                for type_name in type_names
            ]
            actual_public_type_usage = [
                row
                for row in parsed_public
                if row[0] == "type" and row[1] == "public"
            ]
            exact_public_type_usage_verified = (
                actual_public_type_usage
                == expected_public_type_usage
            )
            exact_release_grant_policy_verified = bool(
                grant_policy_delta_count == 0
                and not unsafe_public
                and exact_public_type_usage_verified
            )
        public_type_policy_delta_count = (
            0
            if not post_phase or exact_public_type_usage_verified
            else max(
                1,
                len(
                    set(map(tuple, actual_public_type_usage))
                    ^ set(map(tuple, expected_public_type_usage))
                ),
            )
        )
        excessive += (
            grant_policy_delta_count
            + len(unsafe_public)
            + len(missing)
            + public_type_policy_delta_count
        )
        return {
            "expected_roles": list(roles),
            "observed_roles": list(observed_roles),
            "missing_role_count": len(missing),
            "closed_role_count": sum(
                1 for row in parsed if row["closed"]
            ),
            "excessive_grant_count": excessive,
            "explicit_grant_count": len(normalized_grants),
            "public_privilege_count": len(parsed_public),
            "unsafe_public_privilege_count": len(unsafe_public),
            "grant_policy_delta_count": grant_policy_delta_count,
            "public_type_policy_delta_count": (
                public_type_policy_delta_count
            ),
            "exact_public_type_usage_verified": (
                exact_public_type_usage_verified
            ),
            "exact_release_grant_policy_verified": (
                exact_release_grant_policy_verified
            ),
            "role_state_sha256": _sha256(_canonical_json(parsed)),
            "grant_set_sha256": _sha256(
                _canonical_json(normalized_grants)
            ),
            "public_privilege_set_sha256": _sha256(
                _canonical_json(parsed_public)
            ),
            "unsafe_public_privilege_set_sha256": _sha256(
                _canonical_json(unsafe_public)
            ),
            "least_privilege_role_set_verified": (
                not missing
                and excessive == 0
                and (
                    not post_phase
                    or exact_release_grant_policy_verified
                )
            ),
        }

    def _index_inventory(self) -> dict[str, Any]:
        rows = _psql_lines(
            self._psql(
                "SELECT concat_ws(E'\\t', class.relname, "
                "index.indisvalid::text, index.indisready::text) "
                "FROM pg_index index "
                "JOIN pg_class class ON class.oid=index.indexrelid "
                "JOIN pg_namespace namespace ON namespace.oid=class.relnamespace "
                "WHERE namespace.nspname='public' "
                "AND (NOT index.indisvalid OR NOT index.indisready) "
                "ORDER BY class.relname"
            ),
            label="invalid index inventory",
        )
        invalid: list[str] = []
        for row in rows:
            fields = row.split("\t")
            if (
                len(fields) != 3
                or ROLE_RE.fullmatch(fields[0]) is None
                or fields[1] not in {"true", "false", "t", "f"}
                or fields[2] not in {"true", "false", "t", "f"}
            ):
                raise FrozenPrepareWorkerError(
                    "invalid index inventory row is invalid"
                )
            if fields[1] in {"false", "f"} or fields[2] in {"false", "f"}:
                invalid.append(fields[0])
        foreign = sorted(set(invalid) - set(self.concurrent_indexes))
        if foreign:
            raise FrozenPrepareWorkerError(
                "database contains an unreviewed invalid or unready index"
            )
        return {
            "reviewed_concurrent_indexes": list(self.concurrent_indexes),
            "invalid_unready_indexes": sorted(invalid),
            "invalid_unready_index_count": len(invalid),
            "off_chain_revision_count": 0,
            "migration_corridor": list(self.corridor),
            "migration_corridor_sha256": _sha256(
                _canonical_json(list(self.corridor))
            ),
        }

    def _database_fence_inventory(self) -> dict[str, Any]:
        rows = _psql_lines(
            self._psql(
                "SELECT concat_ws(E'\\t', enforcement_enabled::text, "
                "physical_site, application_role, projection_role, "
                "coalesce(control_role,''), require_witness_lease::text) "
                "FROM public.dr_database_runtime WHERE singleton_id=1"
            ),
            label="database fence inventory",
        )
        if len(rows) != 1:
            raise FrozenPrepareWorkerError(
                "database fence singleton is invalid"
            )
        fields = rows[0].split("\t")
        expected_control = (
            ""
            if self.manifest.role == "bot_fi"
            else f"{self.manifest.role}_control"
        )
        expected_witness = self.manifest.role != "bot_fi"
        expected = (
            "true",
            self.manifest.role,
            f"{self.manifest.role}_app",
            f"{self.manifest.role}_projection",
            expected_control,
            "true" if expected_witness else "false",
        )
        normalized = tuple(
            "true" if value == "t" else "false" if value == "f" else value
            for value in fields
        )
        trigger_rows = _psql_lines(
            self._psql(
                "SELECT concat_ws(E'\\t', class.relname, "
                "trigger.tgenabled, trigger.tgtype::text, "
                "(trigger.tgqual IS NULL)::text, "
                "trigger.tgnargs::text, encode(trigger.tgargs,'hex'), "
                "procedure.oid::text, function_namespace.nspname, "
                "procedure.proname, "
                "pg_get_function_identity_arguments(procedure.oid), "
                "owner.rolname, current_user, language.lanname, "
                "procedure.prokind, procedure.provolatile, "
                "procedure.proparallel, procedure.proleakproof::text, "
                "procedure.proretset::text, procedure.pronargs::text, "
                "return_namespace.nspname, return_type.typname, "
                "procedure.prosecdef::text, "
                "octet_length(convert_to(procedure.prosrc,'UTF8'))::text, "
                "encode(sha256(convert_to(procedure.prosrc,'UTF8')),'hex'), "
                "coalesce(cardinality(procedure.proconfig),0)::text, "
                "coalesce(procedure.proconfig[1],'')) "
                "FROM pg_trigger trigger "
                "JOIN pg_class class ON class.oid=trigger.tgrelid "
                "JOIN pg_namespace namespace ON namespace.oid=class.relnamespace "
                "JOIN pg_proc procedure ON procedure.oid=trigger.tgfoid "
                "JOIN pg_namespace function_namespace "
                "ON function_namespace.oid=procedure.pronamespace "
                "JOIN pg_roles owner ON owner.oid=procedure.proowner "
                "JOIN pg_language language ON language.oid=procedure.prolang "
                "JOIN pg_type return_type ON return_type.oid=procedure.prorettype "
                "JOIN pg_namespace return_namespace "
                "ON return_namespace.oid=return_type.typnamespace "
                "WHERE namespace.nspname='public' "
                "AND trigger.tgname='trg_three_site_writer_term' "
                "AND NOT trigger.tgisinternal ORDER BY class.relname"
            ),
            label="database event fence inventory",
        )
        parsed_triggers: list[list[str]] = []
        for row in trigger_rows:
            trigger_fields = row.split("\t")
            if (
                len(trigger_fields) != 26
                or ROLE_RE.fullmatch(trigger_fields[0]) is None
                or trigger_fields[1] not in {"O", "D", "R", "A"}
                or not trigger_fields[2].isdigit()
                or trigger_fields[3] not in {"true", "false", "t", "f"}
                or not trigger_fields[4].isdigit()
                or not trigger_fields[6].isdigit()
                or ROLE_RE.fullmatch(trigger_fields[10]) is None
                or ROLE_RE.fullmatch(trigger_fields[11]) is None
                or trigger_fields[16] not in {"true", "false", "t", "f"}
                or trigger_fields[17] not in {"true", "false", "t", "f"}
                or not trigger_fields[18].isdigit()
                or trigger_fields[21] not in {"true", "false", "t", "f"}
                or not trigger_fields[22].isdigit()
                or SHA256_RE.fullmatch(trigger_fields[23]) is None
                or not trigger_fields[24].isdigit()
            ):
                raise FrozenPrepareWorkerError(
                    "database event fence trigger inventory is invalid"
                )
            parsed_triggers.append(trigger_fields)
        observed_trigger_tables = [
            trigger[0] for trigger in parsed_triggers
        ]
        trigger_contract_exact = (
            observed_trigger_tables
            == list(EXPECTED_WRITER_TRIGGER_TABLES)
            and len(observed_trigger_tables)
            == len(set(observed_trigger_tables))
            and len({trigger[6] for trigger in parsed_triggers}) == 1
            and all(
                trigger[1] == "A"
                and trigger[2] == "31"
                and trigger[3] in {"true", "t"}
                and trigger[4] == "0"
                and trigger[5] == ""
                and trigger[7] == "public"
                and trigger[8]
                == "trading_bot_enforce_writer_term"
                and trigger[9] == ""
                and trigger[10] == trigger[11]
                and trigger[12] == "plpgsql"
                and trigger[13] == "f"
                and trigger[14] == "v"
                and trigger[15] == "u"
                and trigger[16] in {"false", "f"}
                and trigger[17] in {"false", "f"}
                and trigger[18] == "0"
                and trigger[19] == "pg_catalog"
                and trigger[20] == "trigger"
                and trigger[21] in {"true", "t"}
                and trigger[22]
                == str(WEB_GRANTS.WRITER_FUNCTION_PROSRC_BYTES)
                and trigger[23]
                == WEB_GRANTS.WRITER_FUNCTION_PROSRC_SHA256
                and trigger[24] == "1"
                and trigger[25] == "search_path=public, pg_temp"
                for trigger in parsed_triggers
            )
        )
        trigger_count = len(parsed_triggers)
        enabled_count = sum(
            1 for trigger in parsed_triggers if trigger[1] == "A"
        )
        database_fenced = (
            len(normalized) == 6
            and normalized == expected
            and trigger_contract_exact
        )
        writer_fenced: bool | None = None
        writer_state_sha256: str | None = None
        writer_transition_sha256: str | None = None
        if self.manifest.role == "webapp_ir":
            writer_rows = _psql_lines(
                self._psql(
                    "SELECT concat_ws(E'\\t', coalesce(active_site,''), "
                    "writer_epoch::text, control_state, transition_id, "
                    "updated_by, reason, "
                    "num_nonnulls(readiness_evidence_hash, "
                    "readiness_evidence_id, readiness_approved_by, "
                    "readiness_approved_at, readiness_expires_at)::text, "
                    "num_nonnulls(witness_lease_id, "
                    "witness_lease_issued_at, witness_lease_expires_at, "
                    "witness_proof_hash, witness_transition_id, "
                    "witness_local_boot_id, "
                    "witness_local_boottime_deadline, "
                    "witness_observed_wall_at, "
                    "witness_observed_boottime, "
                    "witness_clock_offset_ms)::text "
                    "FROM public.webapp_writer_state "
                    "WHERE authority='webapp'"
                ),
                label="WebApp-IR writer fence inventory",
            )
            if (
                len(writer_rows) != 1
                or len(writer_rows[0].split("\t")) != 8
                or not writer_rows[0].split("\t")[1].isdigit()
                or not writer_rows[0].split("\t")[6].isdigit()
                or not writer_rows[0].split("\t")[7].isdigit()
            ):
                raise FrozenPrepareWorkerError(
                    "WebApp-IR writer fence singleton is invalid"
                )
            writer_fields = writer_rows[0].split("\t")
            try:
                transition_id = _canonical_uuid(
                    writer_fields[3],
                    label="WebApp-IR writer transition id",
                )
            except FrozenPrepareWorkerError:
                transition_id = ""
            expected_operator = (
                "production-shadow:"
                f"{self.context.document['operation_id']}"
            )
            expected_reason = (
                "initialize WebApp-IR as an operation-bound locally "
                "fenced standby"
            )
            transition_rows = (
                _psql_lines(
                    self._psql(
                        "SELECT concat_ws(E'\\t', transition_id, "
                        "authority, action, "
                        "coalesce(previous_active_site,''), "
                        "coalesce(new_active_site,''), "
                        "previous_epoch::text, new_epoch::text, "
                        "operator, reason, "
                        "num_nonnulls(evidence_hash, "
                        "witness_proof_hash)::text "
                        "FROM public.webapp_writer_transitions "
                        f"WHERE transition_id='{transition_id}'"
                    ),
                    label="WebApp-IR writer transition inventory",
                )
                if transition_id
                else []
            )
            transition_fields = (
                transition_rows[0].split("\t")
                if len(transition_rows) == 1
                else []
            )
            transition_exact = (
                len(transition_fields) == 10
                and transition_fields
                == [
                    transition_id,
                    "webapp",
                    "fence",
                    "webapp_fi",
                    "",
                    "1",
                    "1",
                    expected_operator,
                    expected_reason,
                    "0",
                ]
            )
            writer_fenced = (
                writer_fields[0] == ""
                and writer_fields[1] == "1"
                and writer_fields[2] == "fenced"
                and transition_id != ""
                and writer_fields[4] == expected_operator
                and writer_fields[5] == expected_reason
                and writer_fields[6:] == ["0", "0"]
                and transition_exact
            )
            writer_state_sha256 = _sha256(
                _canonical_json(writer_fields)
            )
            writer_transition_sha256 = _sha256(
                _canonical_json(transition_fields)
            )
        configuration = {
            "database_runtime": list(normalized),
            "writer_triggers": parsed_triggers,
            "writer_fenced": writer_fenced,
            "writer_state_sha256": writer_state_sha256,
            "writer_transition_sha256": writer_transition_sha256,
        }
        return {
            "database_fenced": database_fenced,
            "database_event_fence_verified": database_fenced,
            "writer_trigger_count": trigger_count,
            "enabled_writer_trigger_count": enabled_count,
            "writer_fenced": writer_fenced,
            "unfenced_writer_count": (
                0
                if database_fenced
                and (writer_fenced is not False)
                else 1
            ),
            "fence_configuration_sha256": _sha256(
                _canonical_json(configuration)
            ),
        }

    def _database_fingerprint(self) -> dict[str, Any]:
        # This is deliberately a schema-catalog fingerprint.  Role/fence
        # phases update control rows, so the restored row fingerprint cannot
        # satisfy the controller's cross-phase schema identity binding.
        runtime_roles = sorted(
            EXPECTED_RUNTIME_ROLES[self.manifest.role]
        )
        runtime_role_literals = ", ".join(
            f"'{runtime_role}'" for runtime_role in runtime_roles
        )
        rows = _psql_lines(
            self._psql(
                "WITH schema_records(kind, identity, definition) AS ("
                " SELECT 'relation', "
                " format('%I.%I', namespace.nspname, class.relname), "
                " jsonb_build_object("
                "  'kind', class.relkind,"
                "  'owner', pg_get_userbyid(class.relowner),"
                "  'acl', (SELECT coalesce(jsonb_agg("
                "   jsonb_build_object("
                "    'grantee', coalesce(grantee.rolname, 'PUBLIC'),"
                "    'privilege', acl.privilege_type,"
                "    'grantable', acl.is_grantable)"
                "   ORDER BY coalesce(grantee.rolname, 'PUBLIC'),"
                "   acl.privilege_type, acl.is_grantable), '[]'::jsonb) "
                "   FROM aclexplode(coalesce(class.relacl, "
                "   acldefault(CASE WHEN class.relkind='S' "
                "   THEN 's'::\"char\" ELSE 'r'::\"char\" END, "
                "   class.relowner))) acl "
                "   LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                "   WHERE acl.grantee=0 OR grantee.rolname NOT IN ("
                f"{runtime_role_literals})),"
                "  'persistence', class.relpersistence,"
                "  'row_security', class.relrowsecurity,"
                "  'force_row_security', class.relforcerowsecurity,"
                "  'replica_identity', class.relreplident,"
                "  'options', class.reloptions,"
                "  'access_method', access_method.amname,"
                "  'tablespace', tablespace.spcname,"
                "  'partition_key', CASE WHEN class.relkind='p' "
                "   THEN pg_get_partkeydef(class.oid) ELSE NULL END,"
                "  'partition', pg_get_expr("
                "   class.relpartbound, class.oid, true),"
                "  'view_definition', CASE "
                "   WHEN class.relkind IN ('v','m') "
                "   THEN pg_get_viewdef(class.oid, true) ELSE NULL END"
                " )::text "
                " FROM pg_class class "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=class.relnamespace "
                " LEFT JOIN pg_am access_method "
                " ON access_method.oid=class.relam "
                " LEFT JOIN pg_tablespace tablespace "
                " ON tablespace.oid=class.reltablespace "
                " WHERE namespace.nspname='public' "
                " AND class.relkind IN ('r','p','v','m','f','S','c') "
                " UNION ALL "
                " SELECT 'column', "
                " format('%I.%I.%s.%I', namespace.nspname, class.relname, "
                " attribute.attnum, attribute.attname), "
                " jsonb_build_object("
                "  'type', pg_catalog.format_type("
                "attribute.atttypid, attribute.atttypmod),"
                "  'not_null', attribute.attnotnull,"
                "  'identity', attribute.attidentity,"
                "  'generated', attribute.attgenerated,"
                "  'default', pg_get_expr(default_value.adbin, default_value.adrelid),"
                "  'collation', coalesce(collation.collname, '')"
                " )::text "
                " FROM pg_attribute attribute "
                " JOIN pg_class class ON class.oid=attribute.attrelid "
                " JOIN pg_namespace namespace ON namespace.oid=class.relnamespace "
                " LEFT JOIN pg_attrdef default_value "
                "  ON default_value.adrelid=attribute.attrelid "
                "  AND default_value.adnum=attribute.attnum "
                " LEFT JOIN pg_collation collation "
                "  ON collation.oid=attribute.attcollation "
                " WHERE namespace.nspname='public' "
                " AND class.relkind IN ('r','p','v','m','f','c') "
                " AND attribute.attnum>0 AND NOT attribute.attisdropped "
                " UNION ALL "
                " SELECT 'constraint', "
                " format('%I.%I.%I', namespace.nspname, "
                "class.relname, constraint_row.conname), "
                " jsonb_build_object("
                "  'type', constraint_row.contype,"
                "  'definition', pg_get_constraintdef(constraint_row.oid, true),"
                "  'validated', constraint_row.convalidated,"
                "  'deferrable', constraint_row.condeferrable,"
                "  'deferred', constraint_row.condeferred"
                " )::text "
                " FROM pg_constraint constraint_row "
                " JOIN pg_class class ON class.oid=constraint_row.conrelid "
                " JOIN pg_namespace namespace ON namespace.oid=class.relnamespace "
                " WHERE namespace.nspname='public' "
                " UNION ALL "
                " SELECT 'index', "
                " format('%I.%I.%I', namespace.nspname, "
                "table_class.relname, index_class.relname), "
                " jsonb_build_object("
                "  'definition', pg_get_indexdef(index_row.indexrelid),"
                "  'unique', index_row.indisunique,"
                "  'primary', index_row.indisprimary,"
                "  'exclusion', index_row.indisexclusion,"
                "  'immediate', index_row.indimmediate,"
                "  'clustered', index_row.indisclustered,"
                "  'replica_identity', index_row.indisreplident,"
                "  'nulls_not_distinct', index_row.indnullsnotdistinct,"
                "  'valid', index_row.indisvalid,"
                "  'ready', index_row.indisready,"
                "  'live', index_row.indislive"
                " )::text "
                " FROM pg_index index_row "
                " JOIN pg_class table_class ON table_class.oid=index_row.indrelid "
                " JOIN pg_class index_class ON index_class.oid=index_row.indexrelid "
                " JOIN pg_namespace namespace "
                "ON namespace.oid=table_class.relnamespace "
                " WHERE namespace.nspname='public' "
                " UNION ALL "
                " SELECT 'rewrite-rule', "
                " format('%I.%I.%I', namespace.nspname, "
                " class.relname, rewrite.rulename), "
                " jsonb_build_object("
                "  'event', rewrite.ev_type,"
                "  'enabled', rewrite.ev_enabled,"
                "  'instead', rewrite.is_instead,"
                "  'definition', pg_get_ruledef(rewrite.oid, true)"
                " )::text "
                " FROM pg_rewrite rewrite "
                " JOIN pg_class class ON class.oid=rewrite.ev_class "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=class.relnamespace "
                " UNION ALL "
                " SELECT 'inheritance', "
                " format('%I.%I->%I.%I:%s', child_namespace.nspname, "
                " child.relname, parent_namespace.nspname, "
                " parent.relname, inheritance.inhseqno), "
                " jsonb_build_object("
                "  'sequence', inheritance.inhseqno,"
                "  'detach_pending', inheritance.inhdetachpending"
                " )::text "
                " FROM pg_inherits inheritance "
                " JOIN pg_class child ON child.oid=inheritance.inhrelid "
                " JOIN pg_namespace child_namespace "
                " ON child_namespace.oid=child.relnamespace "
                " JOIN pg_class parent ON parent.oid=inheritance.inhparent "
                " JOIN pg_namespace parent_namespace "
                " ON parent_namespace.oid=parent.relnamespace "
                " WHERE child_namespace.nspname='public' "
                " OR parent_namespace.nspname='public' "
                " UNION ALL "
                " SELECT 'trigger', "
                " format('%I.%I.%I', namespace.nspname, "
                "class.relname, trigger_row.tgname), "
                " jsonb_build_object("
                "  'definition', pg_get_triggerdef(trigger_row.oid, true),"
                "  'enabled', trigger_row.tgenabled"
                " )::text "
                " FROM pg_trigger trigger_row "
                " JOIN pg_class class ON class.oid=trigger_row.tgrelid "
                " JOIN pg_namespace namespace ON namespace.oid=class.relnamespace "
                " WHERE namespace.nspname='public' AND NOT trigger_row.tgisinternal "
                " UNION ALL "
                " SELECT 'policy', "
                " format('%I.%I.%I', namespace.nspname, "
                "class.relname, policy.polname), "
                " jsonb_build_object("
                "  'permissive', policy.polpermissive,"
                "  'command', policy.polcmd,"
                "  'roles', ARRAY("
                "   SELECT coalesce(role.rolname, 'PUBLIC') "
                "   FROM unnest(policy.polroles) AS role_oid(oid) "
                "   LEFT JOIN pg_roles role ON role.oid=role_oid.oid "
                "   ORDER BY coalesce(role.rolname, 'PUBLIC')),"
                "  'using', pg_get_expr(policy.polqual, policy.polrelid, true),"
                "  'check', pg_get_expr("
                "   policy.polwithcheck, policy.polrelid, true)"
                " )::text "
                " FROM pg_policy policy "
                " JOIN pg_class class ON class.oid=policy.polrelid "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=class.relnamespace "
                " WHERE namespace.nspname='public' "
                " UNION ALL "
                " SELECT 'sequence', "
                " format('%I.%I', namespace.nspname, class.relname), "
                " jsonb_build_object("
                "  'type', format_type(sequence.seqtypid, NULL),"
                "  'start', sequence.seqstart,"
                "  'increment', sequence.seqincrement,"
                "  'maximum', sequence.seqmax,"
                "  'minimum', sequence.seqmin,"
                "  'cache', sequence.seqcache,"
                "  'cycle', sequence.seqcycle"
                " )::text "
                " FROM pg_sequence sequence "
                " JOIN pg_class class ON class.oid=sequence.seqrelid "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=class.relnamespace "
                " WHERE namespace.nspname='public' "
                " UNION ALL "
                " SELECT 'function', "
                " format('%I.%I(%s)', namespace.nspname, procedure.proname, "
                " pg_get_function_identity_arguments(procedure.oid)), "
                " jsonb_build_object("
                "  'result', pg_get_function_result(procedure.oid),"
                "  'owner', pg_get_userbyid(procedure.proowner),"
                "  'acl', (SELECT coalesce(jsonb_agg("
                "   jsonb_build_object("
                "    'grantee', coalesce(grantee.rolname, 'PUBLIC'),"
                "    'privilege', acl.privilege_type,"
                "    'grantable', acl.is_grantable)"
                "   ORDER BY coalesce(grantee.rolname, 'PUBLIC'),"
                "   acl.privilege_type, acl.is_grantable), '[]'::jsonb) "
                "   FROM aclexplode(coalesce(procedure.proacl, "
                "   acldefault('f', procedure.proowner))) acl "
                "   LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                "   WHERE acl.grantee=0 OR grantee.rolname NOT IN ("
                f"{runtime_role_literals})),"
                "  'language', language.lanname,"
                "  'kind', procedure.prokind,"
                "  'volatility', procedure.provolatile,"
                "  'security_definer', procedure.prosecdef,"
                "  'parallel', procedure.proparallel,"
                "  'config', coalesce(to_jsonb(procedure.proconfig), 'null'::jsonb),"
                "  'definition', pg_get_functiondef(procedure.oid)"
                " )::text "
                " FROM pg_proc procedure "
                " JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace "
                " JOIN pg_language language ON language.oid=procedure.prolang "
                " WHERE namespace.nspname='public' "
                " AND procedure.prokind IN ('f','p') "
                " UNION ALL "
                " SELECT 'type', "
                " format('%I.%I', namespace.nspname, type_row.typname), "
                " jsonb_build_object("
                "  'kind', type_row.typtype,"
                "  'owner', pg_get_userbyid(type_row.typowner),"
                "  'acl', (SELECT coalesce(jsonb_agg("
                "   jsonb_build_object("
                "    'grantee', coalesce(grantee.rolname, 'PUBLIC'),"
                "    'privilege', acl.privilege_type,"
                "    'grantable', acl.is_grantable)"
                "   ORDER BY coalesce(grantee.rolname, 'PUBLIC'),"
                "   acl.privilege_type, acl.is_grantable), '[]'::jsonb) "
                "   FROM aclexplode(coalesce(type_row.typacl, "
                "   acldefault('T', type_row.typowner))) acl "
                "   LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                "   WHERE acl.grantee=0 OR grantee.rolname NOT IN ("
                f"{runtime_role_literals})),"
                "  'category', type_row.typcategory,"
                "  'base', CASE WHEN type_row.typbasetype<>0 "
                "   THEN format_type(type_row.typbasetype, type_row.typtypmod) "
                "   ELSE NULL END,"
                "  'not_null', type_row.typnotnull,"
                "  'default', type_row.typdefault,"
                "  'collation', coalesce(collation.collname, '')"
                " )::text "
                " FROM pg_type type_row "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=type_row.typnamespace "
                " LEFT JOIN pg_collation collation "
                " ON collation.oid=type_row.typcollation "
                " WHERE namespace.nspname='public' "
                " AND type_row.typtype IN ('d','c','e','r','m') "
                " UNION ALL "
                " SELECT 'domain-constraint', "
                " format('%I.%I.%I', namespace.nspname, "
                "type_row.typname, constraint_row.conname), "
                " jsonb_build_object("
                "  'definition', pg_get_constraintdef("
                "   constraint_row.oid, true),"
                "  'validated', constraint_row.convalidated"
                " )::text "
                " FROM pg_constraint constraint_row "
                " JOIN pg_type type_row "
                " ON type_row.oid=constraint_row.contypid "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=type_row.typnamespace "
                " WHERE namespace.nspname='public' "
                " UNION ALL "
                " SELECT 'enum', "
                " format('%I.%I.%s', namespace.nspname, "
                "type_row.typname, enum_row.enumsortorder), "
                " to_jsonb(enum_row.enumlabel)::text "
                " FROM pg_enum enum_row "
                " JOIN pg_type type_row ON type_row.oid=enum_row.enumtypid "
                " JOIN pg_namespace namespace ON namespace.oid=type_row.typnamespace "
                " WHERE namespace.nspname='public' "
                " UNION ALL "
                " SELECT 'foreign-table', "
                " format('%I.%I', namespace.nspname, class.relname), "
                " jsonb_build_object("
                "  'server', server.srvname,"
                "  'options', ARRAY(SELECT option FROM unnest(coalesce("
                "   foreign_table.ftoptions, '{}'::text[])) option "
                "   ORDER BY option)"
                " )::text "
                " FROM pg_foreign_table foreign_table "
                " JOIN pg_class class ON class.oid=foreign_table.ftrelid "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=class.relnamespace "
                " JOIN pg_foreign_server server "
                " ON server.oid=foreign_table.ftserver "
                " WHERE namespace.nspname='public' "
                " UNION ALL "
                " SELECT 'foreign-server', server.srvname, "
                " jsonb_build_object("
                "  'owner', pg_get_userbyid(server.srvowner),"
                "  'acl', (SELECT coalesce(jsonb_agg("
                "   jsonb_build_object("
                "    'grantee', coalesce(grantee.rolname, 'PUBLIC'),"
                "    'privilege', acl.privilege_type,"
                "    'grantable', acl.is_grantable)"
                "   ORDER BY coalesce(grantee.rolname, 'PUBLIC'),"
                "   acl.privilege_type, acl.is_grantable), '[]'::jsonb) "
                "   FROM aclexplode(coalesce(server.srvacl, "
                "   acldefault('S', server.srvowner))) acl "
                "   LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee "
                "   WHERE acl.grantee=0 OR grantee.rolname NOT IN ("
                f"{runtime_role_literals})),"
                "  'type', server.srvtype,"
                "  'version', server.srvversion,"
                "  'wrapper', wrapper.fdwname,"
                "  'wrapper_handler', wrapper.fdwhandler::regproc::text,"
                "  'wrapper_validator', wrapper.fdwvalidator::regproc::text,"
                "  'wrapper_options', ARRAY(SELECT option FROM unnest("
                "   coalesce(wrapper.fdwoptions, '{}'::text[])) option "
                "   ORDER BY option),"
                "  'options', ARRAY(SELECT option FROM unnest(coalesce("
                "   server.srvoptions, '{}'::text[])) option ORDER BY option)"
                " )::text "
                " FROM pg_foreign_server server "
                " JOIN pg_foreign_data_wrapper wrapper "
                " ON wrapper.oid=server.srvfdw "
                " WHERE EXISTS (SELECT 1 FROM pg_foreign_table used_table "
                "  JOIN pg_class used_class "
                "  ON used_class.oid=used_table.ftrelid "
                "  JOIN pg_namespace used_namespace "
                "  ON used_namespace.oid=used_class.relnamespace "
                "  WHERE used_table.ftserver=server.oid "
                "  AND used_namespace.nspname='public') "
                " UNION ALL "
                " SELECT 'event-trigger', event_trigger.evtname, "
                " jsonb_build_object("
                "  'event', event_trigger.evtevent,"
                "  'enabled', event_trigger.evtenabled,"
                "  'tags', coalesce(to_jsonb(event_trigger.evttags), "
                "   'null'::jsonb),"
                "  'function', format('%I.%I(%s)', "
                "   function_namespace.nspname, procedure.proname, "
                "   pg_get_function_identity_arguments(procedure.oid))"
                " )::text "
                " FROM pg_event_trigger event_trigger "
                " JOIN pg_proc procedure "
                " ON procedure.oid=event_trigger.evtfoid "
                " JOIN pg_namespace function_namespace "
                " ON function_namespace.oid=procedure.pronamespace "
                " UNION ALL "
                " SELECT 'extended-statistics', "
                " format('%I.%I', namespace.nspname, statistics.stxname), "
                " jsonb_build_object("
                "  'owner', pg_get_userbyid(statistics.stxowner),"
                "  'relation', class.relname,"
                "  'kinds', statistics.stxkind,"
                "  'keys', statistics.stxkeys::text,"
                "  'expressions', pg_get_expr("
                "   statistics.stxexprs, statistics.stxrelid, true),"
                "  'definition', pg_get_statisticsobjdef(statistics.oid)"
                " )::text "
                " FROM pg_statistic_ext statistics "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=statistics.stxnamespace "
                " JOIN pg_class class ON class.oid=statistics.stxrelid "
                " WHERE namespace.nspname='public' "
                " UNION ALL "
                " SELECT 'publication', publication.pubname, "
                " jsonb_build_object("
                "  'owner', pg_get_userbyid(publication.pubowner),"
                "  'all_tables', publication.puballtables,"
                "  'insert', publication.pubinsert,"
                "  'update', publication.pubupdate,"
                "  'delete', publication.pubdelete,"
                "  'truncate', publication.pubtruncate,"
                "  'via_partition_root', publication.pubviaroot"
                " )::text "
                " FROM pg_publication publication "
                " UNION ALL "
                " SELECT 'publication-relation', "
                " format('%I:%I.%I', publication.pubname, "
                " namespace.nspname, class.relname), "
                " jsonb_build_object("
                "  'columns', (SELECT jsonb_agg(attribute.attname "
                "   ORDER BY selected.ordinality) "
                "   FROM unnest(publication_relation.prattrs) "
                "   WITH ORDINALITY selected(attnum, ordinality) "
                "   JOIN pg_attribute attribute "
                "   ON attribute.attrelid=publication_relation.prrelid "
                "   AND attribute.attnum=selected.attnum),"
                "  'filter', pg_get_expr(publication_relation.prqual, "
                "   publication_relation.prrelid, true)"
                " )::text "
                " FROM pg_publication_rel publication_relation "
                " JOIN pg_publication publication "
                " ON publication.oid=publication_relation.prpubid "
                " JOIN pg_class class "
                " ON class.oid=publication_relation.prrelid "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=class.relnamespace "
                " UNION ALL "
                " SELECT 'publication-schema', "
                " format('%I:%I', publication.pubname, "
                " namespace.nspname), '{}'::jsonb::text "
                " FROM pg_publication_namespace publication_namespace "
                " JOIN pg_publication publication "
                " ON publication.oid=publication_namespace.pnpubid "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=publication_namespace.pnnspid "
                " UNION ALL "
                " SELECT 'extension', extension.extname, "
                " jsonb_build_object("
                "  'version', extension.extversion,"
                "  'relocatable', extension.extrelocatable,"
                "  'schema', namespace.nspname"
                " )::text "
                " FROM pg_extension extension "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=extension.extnamespace"
                ") SELECT concat_ws(E'\\t', kind, identity, definition) "
                "FROM schema_records ORDER BY kind, identity, definition",
                timeout=1800,
            ),
            label="schema catalog fingerprint",
            maximum_line_bytes=FINGERPRINT_MAX_SQL_LINE_BYTES,
        )
        table_count_rows = _psql_lines(
            self._psql(
                "SELECT count(*)::text FROM pg_class class "
                "JOIN pg_namespace namespace ON namespace.oid=class.relnamespace "
                "WHERE namespace.nspname='public' "
                "AND class.relkind IN ('r','p')"
            ),
            label="schema table count",
        )
        if (
            not rows
            or len(table_count_rows) != 1
            or not table_count_rows[0].isdigit()
            or int(table_count_rows[0]) < 1
        ):
            raise FrozenPrepareWorkerError(
                "migrated schema catalog fingerprint is incomplete"
            )
        return {
            "schema_fingerprint_sha256": _sha256(
                _canonical_json(rows)
            ),
            "database_row_count": None,
            "database_table_count": int(table_count_rows[0]),
            "schema_object_count": len(rows),
            "schema_fingerprint_algorithm": SCHEMA_FINGERPRINT_ALGORITHM,
        }

    def observe(self, step: str) -> Mapping[str, Any]:
        valid_steps = {row[0] for row in self.context.steps}
        if step not in valid_steps:
            raise FrozenPrepareWorkerError(
                "prepare observation step is invalid"
            )
        resources = self._preflight()
        revision = self._revision()
        source = self.manifest.source_database.alembic_revision
        target = self.manifest.target_migration_revision
        if revision not in self.corridor:
            raise FrozenPrepareWorkerError(
                "database revision is off the immutable migration corridor"
            )
        details: dict[str, Any]
        satisfied: bool
        if step in {"roles-pre", "roles-post"}:
            details = self._role_inventory()
            details["schema_fingerprint_sha256"] = (
                self._database_fingerprint()[
                    "schema_fingerprint_sha256"
                ]
                if step == "roles-post" and revision == target
                else None
            )
            satisfied = bool(
                details["least_privilege_role_set_verified"]
                and (
                    step != "roles-pre"
                    or details["explicit_grant_count"] == 0
                )
                and (
                    step != "roles-post"
                    or (
                        revision == target
                        and details[
                            "exact_release_grant_policy_verified"
                        ]
                        is True
                        and details["explicit_grant_count"] > 0
                    )
                )
            )
        elif step == "migrate":
            details = self._index_inventory()
            if revision == target:
                details.update(self._database_fingerprint())
            else:
                details.update(
                    {
                        "schema_fingerprint_sha256": None,
                        "database_row_count": None,
                        "database_table_count": None,
                    }
                )
            satisfied = (
                revision == target
                and details["invalid_unready_index_count"] == 0
            )
        else:
            details = self._database_fence_inventory()
            role_details = self._role_inventory()
            details.update(role_details)
            details.update(self._database_fingerprint())
            satisfied = bool(
                details["database_fenced"]
                and details["least_privilege_role_set_verified"]
                and details["exact_release_grant_policy_verified"]
            )
            if step == "writer-fence":
                satisfied = satisfied and details["writer_fenced"] is True
        observation = {
            "phase": self.context.document["phase"],
            "step": step,
            "role": self.manifest.role,
            "source_revision": source,
            "target_revision": target,
            "current_revision": revision,
            "database_container_count": resources["database_count"],
            "oneoff_container_count": resources["oneoff_count"],
            "network_present": resources["network_present"],
            "named_volume_count": resources["named_volume_count"],
            "satisfied": satisfied,
            "details": details,
            "business_write_observed": False,
            "public_or_private_app_started": False,
            "current_mutated": False,
            "legacy_mutated": False,
            "production_traffic_mutated": False,
            "external_network_contacted": False,
            "ssh_contacted": False,
            "object_storage_contacted": False,
        }
        return _validate_observation(
            observation,
            context=self.context,
            step=step,
        )

    def _compose_base(self) -> list[str]:
        return [
            *RESTORE.DOCKER_BASE,
            "compose",
            "--project-name",
            self.manifest.paths.project_name,
            "--env-file",
            str(self.manifest.environment_path),
            "--file",
            str(self.manifest.prepare_compose_path),
            "--profile",
            f"{ROLE_PATHS[self.manifest.role]}-prepare",
        ]

    def _oneoff_name(
        self,
        *,
        step: str,
        attempt: int,
    ) -> str:
        if (
            step not in {row[0] for row in self.context.steps}
            or not 1 <= attempt <= MAX_ATTEMPTS_PER_STEP
        ):
            raise FrozenPrepareWorkerError(
                "prepare one-off identity is invalid"
            )
        return (
            f"{self.manifest.paths.project_name}-prepare-"
            f"{self.context.sha256[:16]}-{step}-{attempt}"
        )

    def _prepare_service_runtime_contract(
        self,
        service_name: str,
        *,
        runner: DockerRunner | None = None,
    ) -> tuple[RESTORE.DatabaseRuntimeContract, dict[str, str]]:
        cache = getattr(self, "_prepare_contract_cache", None)
        if cache is None:
            cache = {}
            self._prepare_contract_cache = cache
        if service_name in cache:
            contract, environment = cache[service_name]
            return contract, dict(environment)
        docker_runner = self.runner if runner is None else runner
        expected_command = _prepare_service_command(
            service_name,
            operation_id=self.manifest.operation_id,
        )
        if expected_command is None:
            raise FrozenPrepareWorkerError(
                "prepare one-off command contract is unavailable"
            )
        command_env, _overrides = RESTORE._compose_environment(
            self.manifest
        )
        try:
            rendered = RESTORE._load_json_output(
                docker_runner.run(
                    [
                        *self._compose_base(),
                        "config",
                        "--format",
                        "json",
                    ],
                    timeout=60,
                    env=command_env,
                ),
                label="rendered prepare Compose",
            )
            services = (
                rendered.get("services")
                if isinstance(rendered, dict)
                else None
            )
            service = (
                services.get(service_name)
                if isinstance(services, dict)
                else None
            )
            if (
                not isinstance(service, dict)
                or service.get("image") != self.manifest.app_image_id
                or service.get("command")
                != list(expected_command)
                or service.get("restart", "no") not in {"no", ""}
                or not isinstance(service.get("cgroup_parent"), str)
                or not isinstance(service.get("pids_limit"), int)
                or isinstance(service.get("pids_limit"), bool)
                or service["pids_limit"] <= 0
                or not isinstance(service.get("labels", {}), dict)
            ):
                raise FrozenPrepareWorkerError(
                    "rendered prepare service contract differs"
                )
            image_rows = RESTORE._load_json_output(
                docker_runner.run(
                    [
                        *RESTORE.DOCKER_BASE,
                        "image",
                        "inspect",
                        self.manifest.app_image_id,
                    ],
                    timeout=30,
                    env=command_env,
                ),
                label="prepare image runtime inspection",
            )
            if (
                not isinstance(image_rows, list)
                or len(image_rows) != 1
                or not isinstance(image_rows[0], dict)
                or image_rows[0].get("Id")
                != self.manifest.app_image_id
                or not isinstance(image_rows[0].get("Config"), dict)
            ):
                raise FrozenPrepareWorkerError(
                    "prepare image runtime inspection differs"
                )
            image_config = image_rows[0]["Config"]
            image_environment = RESTORE._environment_map(
                image_config.get("Env", []),
                label="prepare image environment",
            )
            image_environment.update(
                RESTORE._environment_map(
                    service.get("environment", {}),
                    label="rendered prepare environment",
                )
            )
            image_labels = image_config.get("Labels") or {}
            service_labels = service.get("labels") or {}
            if (
                not isinstance(image_labels, dict)
                or not isinstance(service_labels, dict)
                or any(
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    for labels in (image_labels, service_labels)
                    for key, value in labels.items()
                )
            ):
                raise FrozenPrepareWorkerError(
                    "prepare service label contract differs"
                )
            entrypoint = (
                RESTORE._string_vector(
                    service["entrypoint"],
                    label="rendered prepare entrypoint",
                )
                if service.get("entrypoint") is not None
                else RESTORE._string_vector(
                    image_config.get("Entrypoint"),
                    label="prepare image entrypoint",
                )
            )
            user = service.get("user", image_config.get("User", ""))
            working_dir = service.get(
                "working_dir",
                image_config.get("WorkingDir", ""),
            )
            stop_signal = service.get(
                "stop_signal",
                image_config.get("StopSignal", ""),
            )
            logging = service.get("logging")
            if (
                any(
                    not isinstance(value, str)
                    for value in (user, working_dir, stop_signal)
                )
                or not isinstance(logging, dict)
                or not isinstance(logging.get("driver"), str)
                or not isinstance(logging.get("options"), dict)
                or any(
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    for key, value in logging["options"].items()
                )
            ):
                raise FrozenPrepareWorkerError(
                    "prepare process or logging contract differs"
                )
            hash_output = docker_runner.run(
                [
                    *self._compose_base(),
                    "config",
                    "--hash",
                    service_name,
                ],
                timeout=60,
                env=command_env,
            )
            hash_match = re.fullmatch(
                rf"{re.escape(service_name)} ([0-9a-f]{{64}})\n?",
                hash_output,
            )
            if hash_match is None:
                raise FrozenPrepareWorkerError(
                    "prepare service config hash differs"
                )
            contract = RESTORE.DatabaseRuntimeContract(
                service=service_name,
                container_name="",
                image_id=self.manifest.app_image_id,
                config_hash=hash_match.group(1),
                command=expected_command,
                entrypoint=entrypoint,
                user=user,
                working_dir=working_dir,
                stop_signal=stop_signal,
                stop_timeout=RESTORE._duration_seconds(
                    service.get("stop_grace_period", "10s"),
                    label="rendered prepare stop grace period",
                ),
                environment=dict(sorted(image_environment.items())),
                healthcheck=RESTORE._compose_healthcheck(
                    service.get("healthcheck")
                ),
                labels=dict(
                    sorted({**image_labels, **service_labels}.items())
                ),
                exposed_ports=RESTORE._empty_object_map(
                    image_config.get("ExposedPorts"),
                    label="prepare image exposed ports",
                ),
                volumes=RESTORE._empty_object_map(
                    image_config.get("Volumes"),
                    label="prepare image volumes",
                ),
                on_build=RESTORE._string_vector(
                    image_config.get("OnBuild"),
                    label="prepare image OnBuild",
                ),
                shell=RESTORE._string_vector(
                    image_config.get("Shell"),
                    label="prepare image shell",
                ),
                cgroup_parent=service["cgroup_parent"],
                restart_policy="no",
                nano_cpus=RESTORE._nano_cpus(
                    service.get("cpus"),
                    label="rendered prepare CPU limit",
                ),
                memory=RESTORE._memory_bytes(
                    service.get("mem_limit"),
                    label="rendered prepare memory limit",
                ),
                pids_limit=service["pids_limit"],
                log_config={
                    "Type": logging["driver"],
                    "Config": dict(sorted(logging["options"].items())),
                },
            )
        except FrozenPrepareWorkerError:
            raise
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "prepare service immutable runtime contract failed"
            ) from exc
        environment = dict(sorted(image_environment.items()))
        cache[service_name] = (contract, environment)
        return contract, dict(environment)

    def _validate_prepare_oneoff_runtime(
        self,
        row: Mapping[str, Any],
        *,
        identifier: str,
        expected_name: str,
        expected_service: str,
        expected_network: str,
        expected_production_labels: Mapping[str, str],
        contract: RESTORE.DatabaseRuntimeContract,
        environment: Mapping[str, str],
    ) -> str:
        config = row.get("Config")
        host = row.get("HostConfig")
        if not isinstance(host, dict) or set(host) != RESTORE.HOST_CONFIG_FIELDS:
            raise FrozenPrepareWorkerError(
                "refusing prepare one-off with an inexact HostConfig field set"
            )
        try:
            RESTORE._validate_exact_container_config(
                config,
                container_id=identifier,
                contract=contract,
                command=_prepare_service_command(
                    expected_service,
                    operation_id=self.manifest.operation_id,
                ),
                environment=environment,
            )
            host_sha256 = RESTORE._validate_exact_host_config(
                host,
                binds=(
                    f"{self.manifest.ca_path}:"
                    "/run/production-dr-ca/ca.crt:ro",
                ),
                network_mode=expected_network,
                cgroup_parent=contract.cgroup_parent,
                nano_cpus=contract.nano_cpus,
                memory=contract.memory,
                pids_limit=contract.pids_limit,
                auto_remove=True,
                restart_policy="no",
                log_config=contract.log_config,
            )
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "refusing to recover a non-exact prepare one-off"
            ) from exc
        labels = config["Labels"]
        compose_labels = {
            key: value
            for key, value in labels.items()
            if key.startswith("com.docker.compose.")
        }
        non_compose_labels = {
            key: value
            for key, value in labels.items()
            if not key.startswith("com.docker.compose.")
        }
        expected_non_compose = {
            **contract.labels,
            **expected_production_labels,
        }
        required_compose = {
            "com.docker.compose.project": (
                self.manifest.paths.project_name
            ),
            "com.docker.compose.service": expected_service,
            "com.docker.compose.oneoff": "True",
            "com.docker.compose.config-hash": contract.config_hash,
        }
        allowed_compose_fields = {
            *required_compose,
            "com.docker.compose.container-number",
            "com.docker.compose.project.config_files",
            "com.docker.compose.project.working_dir",
            "com.docker.compose.replace",
            "com.docker.compose.slug",
            "com.docker.compose.version",
            "com.docker.compose.image",
            "com.docker.compose.depends_on",
        }
        if (
            row.get("Id") != identifier
            or row.get("Name") != f"/{expected_name}"
            or row.get("Image") != self.manifest.app_image_id
            or non_compose_labels != expected_non_compose
            or any(
                compose_labels.get(key) != value
                for key, value in required_compose.items()
            )
            or not set(compose_labels).issubset(allowed_compose_fields)
            or (
                "com.docker.compose.container-number" in compose_labels
                and compose_labels[
                    "com.docker.compose.container-number"
                ]
                != "1"
            )
            or (
                "com.docker.compose.project.config_files"
                in compose_labels
                and compose_labels[
                    "com.docker.compose.project.config_files"
                ]
                != str(self.manifest.prepare_compose_path)
            )
            or (
                "com.docker.compose.project.working_dir"
                in compose_labels
                and compose_labels[
                    "com.docker.compose.project.working_dir"
                ]
                != str(self.manifest.prepare_compose_path.parent)
            )
        ):
            raise FrozenPrepareWorkerError(
                "refusing to recover a non-exact prepare one-off"
            )
        return host_sha256

    def _prepare_residue(
        self,
        *,
        step: str,
        attempt: int,
        started_event_sha256: str,
        runner: DockerRunner | None = None,
    ) -> list[tuple[str, Mapping[str, Any]]]:
        docker_runner = self.runner if runner is None else runner
        _nonzero_sha256(
            started_event_sha256,
            label="prepare started event",
        )
        expected_service = next(
            service
            for name, service, _timeout in self.context.steps
            if name == step
        )
        if expected_service is None:
            return []
        expected_name = self._oneoff_name(step=step, attempt=attempt)
        expected_network = (
            f"{self.manifest.paths.project_name}_{self.manifest.role}"
        )
        _command_env, overrides = RESTORE._compose_environment(
            self.manifest
        )
        expected_command = _prepare_service_command(
            expected_service,
            operation_id=self.manifest.operation_id,
        )
        if expected_command is None:
            raise FrozenPrepareWorkerError(
                "prepare one-off command contract is unavailable"
            )
        contract, expected_environment = (
            self._prepare_service_runtime_contract(
                expected_service,
                runner=docker_runner,
            )
        )
        expected_production_labels = {
            "trading-bot.production.operation-id": (
                self.manifest.operation_id
            ),
            "trading-bot.production.prepare-generation": (
                self.manifest.restore_generation_sha256
            ),
            "trading-bot.production.prepare-phase": (
                self.context.document["phase"]
            ),
            "trading-bot.production.prepare-request": self.context.sha256,
            "trading-bot.production.prepare-step": step,
            "trading-bot.production.prepare-attempt": str(attempt),
            "trading-bot.production.prepare-started-event": (
                started_event_sha256
            ),
        }
        residues: list[tuple[str, Mapping[str, Any]]] = []
        try:
            identifiers = RESTORE._project_container_ids(
                self.manifest,
                docker_runner,
            )
            for identifier in identifiers:
                row = RESTORE._inspect_container(
                    identifier,
                    self.manifest,
                    docker_runner,
                )
                config = row.get("Config")
                labels = (
                    config.get("Labels")
                    if isinstance(config, dict)
                    else None
                )
                oneoff = (
                    isinstance(labels, dict)
                    and labels.get("com.docker.compose.oneoff") == "True"
                )
                if not oneoff:
                    RESTORE._container_semantics(row, self.manifest)
                    continue
                if (
                    isinstance(labels, dict)
                    and "trading-bot.production.prepare-sql-intent"
                    in labels
                ):
                    continue
                host = row.get("HostConfig")
                restart = (
                    host.get("RestartPolicy")
                    if isinstance(host, dict)
                    else None
                )
                network_settings = row.get("NetworkSettings")
                networks = (
                    network_settings.get("Networks")
                    if isinstance(network_settings, dict)
                    else None
                )
                mounts = row.get("Mounts")
                production_labels = (
                    {
                        key: value
                        for key, value in labels.items()
                        if key.startswith("trading-bot.production.")
                    }
                    if isinstance(labels, dict)
                    else None
                )
                observed_mounts = (
                    {
                        (
                            mount.get("Type"),
                            mount.get("Source"),
                            mount.get("Destination"),
                            mount.get("RW"),
                        )
                        for mount in mounts
                        if isinstance(mount, dict)
                    }
                    if isinstance(mounts, list)
                    else None
                )
                self._validate_prepare_oneoff_runtime(
                    row,
                    identifier=identifier,
                    expected_name=expected_name,
                    expected_service=expected_service,
                    expected_network=expected_network,
                    expected_production_labels=expected_production_labels,
                    contract=contract,
                    environment=expected_environment,
                )
                if (
                    not isinstance(config, dict)
                    or not isinstance(labels, dict)
                    or labels.get("com.docker.compose.project")
                    != self.manifest.paths.project_name
                    or labels.get("com.docker.compose.service")
                    != expected_service
                    or production_labels != expected_production_labels
                    or row.get("Name") != f"/{expected_name}"
                    or row.get("Image") != self.manifest.app_image_id
                    or config.get("Image") != self.manifest.app_image_id
                    or RESTORE._string_vector(
                        config.get("Cmd"),
                        label="prepare one-off command",
                    )
                    != expected_command
                    or not isinstance(host, dict)
                    or host.get("AutoRemove") is not True
                    or host.get("Privileged") is not False
                    or host.get("ReadonlyRootfs") is not False
                    or not isinstance(restart, dict)
                    or restart.get("Name") not in {"", "no"}
                    or restart.get("MaximumRetryCount", 0) != 0
                    or host.get("NetworkMode") != expected_network
                    or host.get("CgroupParent")
                    != overrides["PRODUCTION_SHADOW_CGROUP_PARENT"]
                    or not isinstance(host.get("PidsLimit"), int)
                    or isinstance(host.get("PidsLimit"), bool)
                    or host["PidsLimit"] <= 0
                    or not isinstance(host.get("Memory"), int)
                    or isinstance(host.get("Memory"), bool)
                    or host["Memory"] <= 0
                    or host.get("PortBindings") not in (None, {})
                    or host.get("PublishAllPorts") is not False
                    or host.get("CapAdd") not in (None, [])
                    or host.get("CapDrop") not in (None, [])
                    or host.get("SecurityOpt") not in (None, [])
                    or host.get("Devices") not in (None, [])
                    or host.get("DeviceRequests") not in (None, [])
                    or host.get("PidMode", "") != ""
                    or host.get("IpcMode", "private") != "private"
                    or host.get("UTSMode", "") != ""
                    or host.get("UsernsMode", "") != ""
                    or host.get("Links") not in (None, [])
                    or host.get("ExtraHosts") not in (None, [])
                    or host.get("Dns") not in (None, [])
                    or host.get("DnsOptions") not in (None, [])
                    or host.get("DnsSearch") not in (None, [])
                    or host.get("GroupAdd") not in (None, [])
                    or host.get("Sysctls") not in (None, {})
                    or host.get("Tmpfs") not in (None, {})
                    or not isinstance(networks, dict)
                    or set(networks) != {expected_network}
                    or not isinstance(mounts, list)
                    or len(mounts) != 1
                    or not all(
                        isinstance(mount, dict) for mount in mounts
                    )
                    or observed_mounts
                    != {
                        (
                            "bind",
                            str(self.manifest.ca_path),
                            "/run/production-dr-ca/ca.crt",
                            False,
                        )
                    }
                ):
                    raise FrozenPrepareWorkerError(
                        "refusing to recover a non-exact prepare one-off"
                    )
                residues.append((identifier, row))
        except FrozenPrepareWorkerError:
            raise
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "prepare one-off inventory failed closed"
            ) from exc
        if len(residues) > 1:
            raise FrozenPrepareWorkerError(
                "prepare journal owns multiple one-off residues"
            )
        return residues

    def inspect_residue(
        self,
        *,
        step: str,
        attempt: int,
        started_event_sha256: str,
    ) -> Mapping[str, Any]:
        residues = self._prepare_residue(
            step=step,
            attempt=attempt,
            started_event_sha256=started_event_sha256,
        )
        identities = [identifier for identifier, _row in residues]
        return {
            "residue_count": len(identities),
            "residue_identity_sha256": (
                _sha256(_canonical_json(identities))
                if identities
                else None
            ),
        }

    def cleanup_residue(
        self,
        *,
        step: str,
        attempt: int,
        started_event_sha256: str,
        deadline: float | None = None,
        runner: DockerRunner | None = None,
    ) -> Mapping[str, Any]:
        if deadline is None:
            deadline = (
                time.monotonic() + CANCELLATION_MAX_WAIT_SECONDS
            )
        docker_runner = (
            DeadlineDockerRunner(self.runner, deadline=deadline)
            if runner is None
            else runner
        )
        residues = self._prepare_residue(
            step=step,
            attempt=attempt,
            started_event_sha256=started_event_sha256,
            runner=docker_runner,
        )
        if len(residues) != 1:
            raise FrozenPrepareWorkerError(
                "prepare cleanup requires one exact journal-owned residue"
            )
        identities = [residues[0][0]]
        boundary = RESTORE._capture_runtime_path_identities(
            self.manifest,
            require_stores=True,
        )
        command_env, _overrides = RESTORE._compose_environment(
            self.manifest
        )
        try:
            docker_runner.run(
                [
                    *RESTORE.DOCKER_BASE,
                    "rm",
                    "--force",
                    identities[0],
                ],
                timeout=60,
                env=command_env,
            )
            RESTORE._recheck_runtime_path_identities(
                self.manifest,
                boundary,
                require_stores=True,
            )
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "journal-owned prepare one-off cleanup failed closed"
            ) from exc
        if self._prepare_residue(
            step=step,
            attempt=attempt,
            started_event_sha256=started_event_sha256,
            runner=docker_runner,
        ):
            raise FrozenPrepareWorkerError(
                "journal-owned prepare one-off residue remains"
            )
        return {
            "residue_count": 1,
            "residue_identity_sha256": _sha256(
                _canonical_json(identities)
            ),
            "removed_count": 1,
            "persistent_volume_removed": False,
            "generation_data_mutated": False,
        }

    def cancel_active_oneoff(
        self,
        *,
        step: str,
        attempt: int,
        started_event_sha256: str,
    ) -> Mapping[str, Any]:
        """Best-effort safety rollback covered by the persisted start grant."""

        deadline = time.monotonic() + CANCELLATION_MAX_WAIT_SECONDS
        docker_runner = DeadlineDockerRunner(
            self.runner,
            deadline=deadline,
        )
        absent_since: float | None = None
        removed_identities: list[str] = []
        while True:
            app_residues = self._prepare_residue(
                step=step,
                attempt=attempt,
                started_event_sha256=started_event_sha256,
                runner=docker_runner,
            )
            sql_residues = self._sql_residues(
                runner=docker_runner,
            )
            if len(app_residues) + len(sql_residues) > 1:
                raise FrozenPrepareWorkerError(
                    "active attempt owns multiple one-off residues"
                )
            now = time.monotonic()
            if app_residues or sql_residues:
                absent_since = None
                if app_residues:
                    identity = app_residues[0][0]
                    cleanup = _validate_cancellation_cleanup(
                        self.cleanup_residue(
                            step=step,
                            attempt=attempt,
                            started_event_sha256=(
                                started_event_sha256
                            ),
                            deadline=deadline,
                            runner=docker_runner,
                        )
                    )
                    if (
                        cleanup["residue_count"] != 1
                        or cleanup["residue_identity_sha256"]
                        != _sha256(_canonical_json([identity]))
                    ):
                        raise FrozenPrepareWorkerError(
                            "active one-off safety cleanup identity differs"
                        )
                else:
                    identity = sql_residues[0][0]
                    self._remove_sql_residue(
                        identity,
                        runner=docker_runner,
                    )
                if identity not in removed_identities:
                    removed_identities.append(identity)
            else:
                if absent_since is None:
                    absent_since = now
                if (
                    now - absent_since
                    >= CANCELLATION_QUIESCENCE_SECONDS
                ):
                    if len(removed_identities) > 1:
                        raise FrozenPrepareWorkerError(
                            "multiple delayed prepare one-offs were removed"
                        )
                    return {
                        "residue_count": len(removed_identities),
                        "residue_identity_sha256": (
                            _sha256(
                                _canonical_json(removed_identities)
                            )
                            if removed_identities
                            else None
                        ),
                        "removed_count": len(removed_identities),
                        "persistent_volume_removed": False,
                        "generation_data_mutated": False,
                    }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FrozenPrepareWorkerError(
                    "active one-off safety cleanup did not quiesce"
                )
            time.sleep(min(CANCELLATION_POLL_SECONDS, remaining))

    def _run_service(
        self,
        *,
        step: str,
        attempt: int,
        started_event_sha256: str,
        service: str,
        timeout: int,
    ) -> tuple[str, int]:
        command_env, _overrides = RESTORE._compose_environment(self.manifest)
        boundary = RESTORE._capture_runtime_path_identities(
            self.manifest,
            require_stores=True,
        )
        arguments = [
            *self._compose_base(),
            "run",
            "--rm",
            "--no-deps",
            "--pull",
            "never",
            "--name",
            self._oneoff_name(step=step, attempt=attempt),
            "--label",
            (
                "trading-bot.production.prepare-generation="
                f"{self.manifest.restore_generation_sha256}"
            ),
            "--label",
            (
                "trading-bot.production.prepare-phase="
                f"{self.context.document['phase']}"
            ),
            "--label",
            (
                "trading-bot.production.prepare-request="
                f"{self.context.sha256}"
            ),
            "--label",
            f"trading-bot.production.prepare-step={step}",
            "--label",
            f"trading-bot.production.prepare-attempt={attempt}",
            "--label",
            (
                "trading-bot.production.prepare-started-event="
                f"{started_event_sha256}"
            ),
            "-T",
            service,
        ]
        try:
            output = self.runner.run(
                arguments,
                timeout=timeout,
                env=command_env,
            )
        except RESTORE.FrozenFinalRestoreWorkerError as exc:
            raise FrozenPrepareWorkerError(
                "prepare one-off failed closed"
            ) from exc
        finally:
            try:
                RESTORE._recheck_runtime_path_identities(
                    self.manifest,
                    boundary,
                    require_stores=True,
                )
            except RESTORE.FrozenFinalRestoreWorkerError as exc:
                raise FrozenPrepareWorkerError(
                    "generation path identity changed during prepare"
                ) from exc
        encoded = output.encode("utf-8")
        return _sha256(encoded), len(encoded)

    def _repair_indexes(self) -> list[str]:
        inventory = self._index_inventory()
        invalid = list(inventory["invalid_unready_indexes"])
        for name in invalid:
            if name not in self.concurrent_indexes or ROLE_RE.fullmatch(name) is None:
                raise FrozenPrepareWorkerError(
                    "refusing to repair an unreviewed concurrent index"
                )
            self._drop_reviewed_index(
                name,
                timeout=600,
            )
        after = self._index_inventory()
        if after["invalid_unready_index_count"] != 0:
            raise FrozenPrepareWorkerError(
                "reviewed concurrent index repair did not converge"
            )
        return invalid

    def run_step(
        self,
        step: str,
        *,
        attempt: int,
        started_event_sha256: str,
    ) -> Mapping[str, Any]:
        self._bind_sql_scope(
            step=step,
            attempt=attempt,
            started_event_sha256=started_event_sha256,
            stage="step-execution",
        )
        match = [
            row for row in self.context.steps if row[0] == step
        ]
        if len(match) != 1:
            raise FrozenPrepareWorkerError(
                "prepare execution step is invalid"
            )
        _name, service, timeout = match[0]
        self._preflight()
        repaired: list[str] = []
        output_sha256: str | None = None
        output_bytes = 0
        command_invoked = False
        if step == "migrate":
            current = self._revision()
            if current != self.manifest.target_migration_revision:
                repaired = self._repair_indexes()
                if service is None:
                    raise FrozenPrepareWorkerError(
                        "migration service is unavailable"
                    )
                output_sha256, output_bytes = self._run_service(
                    step=step,
                    attempt=attempt,
                    started_event_sha256=started_event_sha256,
                    service=service,
                    timeout=timeout,
                )
                command_invoked = True
        elif service is not None:
            output_sha256, output_bytes = self._run_service(
                step=step,
                attempt=attempt,
                started_event_sha256=started_event_sha256,
                service=service,
                timeout=timeout,
            )
            command_invoked = True
        execution = {
            "step": step,
            "service": service,
            "command_invoked": command_invoked,
            "output_sha256": output_sha256,
            "output_bytes": output_bytes,
            "repaired_concurrent_indexes": repaired,
            "pull_performed": False,
            "build_performed": False,
            "compose_down_performed": False,
            "volume_mutated": False,
            "public_or_private_app_started": False,
            "current_mutated": False,
            "legacy_mutated": False,
            "production_traffic_mutated": False,
            "external_network_contacted": False,
            "ssh_contacted": False,
            "object_storage_contacted": False,
        }
        self._preflight()
        return _validate_execution(
            execution,
            context=self.context,
            step=step,
        )


def _validate_observation(
    value: Any,
    *,
    context: LoadedRequest,
    step: str,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != OBSERVATION_FIELDS
        or value.get("phase") != context.document["phase"]
        or value.get("step") != step
        or value.get("role") != context.document["role"]
        or value.get("source_revision")
        != context.manifest.source_database.alembic_revision
        or value.get("target_revision")
        != context.manifest.target_migration_revision
        or not isinstance(value.get("current_revision"), str)
        or REVISION_RE.fullmatch(value["current_revision"]) is None
        or value.get("database_container_count") != 1
        or value.get("oneoff_container_count") != 0
        or value.get("network_present") is not True
        or value.get("named_volume_count") != 0
        or not isinstance(value.get("satisfied"), bool)
        or not isinstance(value.get("details"), dict)
        or value.get("business_write_observed") is not False
        or value.get("public_or_private_app_started") is not False
        or value.get("current_mutated") is not False
        or value.get("legacy_mutated") is not False
        or value.get("production_traffic_mutated") is not False
        or value.get("external_network_contacted") is not False
        or value.get("ssh_contacted") is not False
        or value.get("object_storage_contacted") is not False
    ):
        raise FrozenPrepareWorkerError(
            "prepare observation safety closure differs"
        )
    prior_semantic_field = {
        "shadow_roles_post_migration": "schema_fingerprint_sha256",
        "shadow_fence": "migrated_schema_fingerprint_sha256",
    }.get(str(context.document["phase"]))
    if prior_semantic_field is not None:
        prior_result = context.prior_result
        prior_semantic = (
            prior_result.get("semantic")
            if isinstance(prior_result, Mapping)
            else None
        )
        observed_fingerprint = value["details"].get(
            "schema_fingerprint_sha256"
        )
        if (
            not isinstance(prior_semantic, Mapping)
            or prior_semantic.get("schema_fingerprint_algorithm")
            != SCHEMA_FINGERPRINT_ALGORITHM
            or prior_semantic.get(prior_semantic_field)
            != observed_fingerprint
        ):
            raise FrozenPrepareWorkerError(
                "prepare schema fingerprint differs from prior phase"
            )
        _nonzero_sha256(
            observed_fingerprint,
            label="prior-bound schema fingerprint",
        )
        if (
            context.document["phase"] == "shadow_fence"
            and (
                prior_semantic.get(
                    "post_migration_grant_set_sha256"
                )
                != value["details"].get("grant_set_sha256")
                or prior_semantic.get("role_state_sha256")
                != value["details"].get("role_state_sha256")
            )
        ):
            raise FrozenPrepareWorkerError(
                "prepare role or grant inventory differs from prior phase"
            )
    return dict(value)


def _validate_execution(
    value: Any,
    *,
    context: LoadedRequest,
    step: str,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != EXECUTION_FIELDS
        or value.get("step") != step
        or value.get("service")
        != next(row[1] for row in context.steps if row[0] == step)
        or not isinstance(value.get("command_invoked"), bool)
        or not isinstance(value.get("output_bytes"), int)
        or isinstance(value.get("output_bytes"), bool)
        or not 0 <= value["output_bytes"] <= RESTORE.MAX_OUTPUT_BYTES
        or not isinstance(value.get("repaired_concurrent_indexes"), list)
        or any(
            not isinstance(name, str)
            or ROLE_RE.fullmatch(name) is None
            for name in value["repaired_concurrent_indexes"]
        )
        or value.get("pull_performed") is not False
        or value.get("build_performed") is not False
        or value.get("compose_down_performed") is not False
        or value.get("volume_mutated") is not False
        or value.get("public_or_private_app_started") is not False
        or value.get("current_mutated") is not False
        or value.get("legacy_mutated") is not False
        or value.get("production_traffic_mutated") is not False
        or value.get("external_network_contacted") is not False
        or value.get("ssh_contacted") is not False
        or value.get("object_storage_contacted") is not False
    ):
        raise FrozenPrepareWorkerError(
            "prepare execution safety closure differs"
        )
    output_sha256 = value["output_sha256"]
    if value["command_invoked"]:
        _nonzero_sha256(output_sha256, label="prepare command output")
    elif output_sha256 is not None or value["output_bytes"] != 0:
        raise FrozenPrepareWorkerError(
            "non-invoked prepare command has output"
        )
    return dict(value)


def _recovered_execution(
    context: LoadedRequest,
    step: str,
) -> dict[str, Any]:
    return _validate_execution(
        {
            "step": step,
            "service": next(
                row[1] for row in context.steps if row[0] == step
            ),
            "command_invoked": False,
            "output_sha256": None,
            "output_bytes": 0,
            "repaired_concurrent_indexes": [],
            "pull_performed": False,
            "build_performed": False,
            "compose_down_performed": False,
            "volume_mutated": False,
            "public_or_private_app_started": False,
            "current_mutated": False,
            "legacy_mutated": False,
            "production_traffic_mutated": False,
            "external_network_contacted": False,
            "ssh_contacted": False,
            "object_storage_contacted": False,
        },
        context=context,
        step=step,
    )


def _validate_step_semantic(
    value: Any,
    *,
    context: LoadedRequest,
    step: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != STEP_SEMANTIC_FIELDS:
        raise FrozenPrepareWorkerError(
            "prepare step semantic fields are not exact"
        )
    observation = _validate_observation(
        value["observation"],
        context=context,
        step=step,
    )
    execution = _validate_execution(
        value["execution"],
        context=context,
        step=step,
    )
    if observation["satisfied"] is not True:
        raise FrozenPrepareWorkerError(
            "completed prepare step is not satisfied"
        )
    return {
        "observation": observation,
        "execution": execution,
    }


def _completed_step_semantics(
    context: LoadedRequest,
    journal: JournalState,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event in journal.events:
        if event["kind"] != "completed":
            continue
        step = event["step"]
        if step in result:
            raise FrozenPrepareWorkerError(
                "prepare journal completes a step more than once"
            )
        result[step] = _validate_step_semantic(
            event["semantic"],
            context=context,
            step=step,
        )
    if list(result) != [row[0] for row in context.steps]:
        raise FrozenPrepareWorkerError(
            "prepare journal does not complete every exact step"
        )
    return result


def _completed_step_tail(journal: JournalState) -> str:
    completed = [
        event["event_sha256"]
        for event in journal.events
        if event["kind"] == "completed"
    ]
    if not completed:
        raise FrozenPrepareWorkerError(
            "prepare journal has no completed step"
        )
    return str(completed[-1])


def _journal_runtime_mutated(
    context: LoadedRequest,
    journal: JournalState,
) -> bool:
    services = {
        step: service
        for step, service, _timeout in context.steps
    }
    return any(
        event["kind"] == "completed"
        and (
            event["command_invoked"] is True
            or event["recovered"] is True
        )
        and services[event["step"]] is not None
        for event in journal.events
    )


def _phase_semantic(
    context: LoadedRequest,
    journal: JournalState,
) -> dict[str, Any]:
    steps = _completed_step_semantics(context, journal)
    phase = context.document["phase"]
    role = context.document["role"]
    final_step = context.steps[-1][0]
    final_observation = steps[final_step]["observation"]
    details = final_observation["details"]
    if phase == "shadow_roles_pre_migration":
        semantic = {
            "least_privilege_role_set_verified": details.get(
                "least_privilege_role_set_verified"
            ),
            "excessive_grant_count": details.get(
                "excessive_grant_count"
            ),
            "role_state_sha256": details.get("role_state_sha256"),
            "grant_set_sha256": details.get("grant_set_sha256"),
            "explicit_grant_count": details.get(
                "explicit_grant_count"
            ),
        }
        if (
            semantic["least_privilege_role_set_verified"] is not True
            or semantic["excessive_grant_count"] != 0
            or semantic["explicit_grant_count"] != 0
        ):
            raise FrozenPrepareWorkerError(
                "pre-migration least-privilege closure differs"
            )
    elif phase == "shadow_migrate":
        semantic = {
            "restore_result_set_sha256": context.document[
                "restore_completion_sha256"
            ],
            "alembic_chain_state": "target",
            "source_revision": context.manifest.source_database.alembic_revision,
            "target_revision": context.manifest.target_migration_revision,
            "current_revision": final_observation["current_revision"],
            "migration_corridor_sha256": details.get(
                "migration_corridor_sha256"
            ),
            "off_chain_revision_count": details.get(
                "off_chain_revision_count"
            ),
            "invalid_unready_index_count": details.get(
                "invalid_unready_index_count"
            ),
            "schema_fingerprint_sha256": details.get(
                "schema_fingerprint_sha256"
            ),
            "schema_fingerprint_algorithm": details.get(
                "schema_fingerprint_algorithm",
                SCHEMA_FINGERPRINT_ALGORITHM,
            ),
            "migration_journal_sha256": _completed_step_tail(journal),
        }
        if (
            semantic["current_revision"]
            != semantic["target_revision"]
            or semantic["off_chain_revision_count"] != 0
            or semantic["invalid_unready_index_count"] != 0
        ):
            raise FrozenPrepareWorkerError(
                "migration closure did not reach the exact target"
            )
    elif phase == "shadow_roles_post_migration":
        semantic = {
            "least_privilege_role_set_verified": details.get(
                "least_privilege_role_set_verified"
            ),
            "excessive_grant_count": details.get(
                "excessive_grant_count"
            ),
            "post_migration_grant_set_sha256": details.get(
                "grant_set_sha256"
            ),
            "role_state_sha256": details.get("role_state_sha256"),
            "migrated_schema_fingerprint_sha256": details.get(
                "schema_fingerprint_sha256"
            ),
            "schema_fingerprint_algorithm": details.get(
                "schema_fingerprint_algorithm",
                SCHEMA_FINGERPRINT_ALGORITHM,
            ),
        }
        if (
            semantic["least_privilege_role_set_verified"] is not True
            or semantic["excessive_grant_count"] != 0
        ):
            raise FrozenPrepareWorkerError(
                "post-migration least-privilege closure differs"
            )
    elif phase == "shadow_fence":
        semantic = {
            "fenced_database_count": (
                1 if details.get("database_fenced") is True else 0
            ),
            "unfenced_writer_count": details.get(
                "unfenced_writer_count"
            ),
            "database_event_fence_verified": details.get(
                "database_event_fence_verified"
            ),
            "migrated_schema_fingerprint_sha256": details.get(
                "schema_fingerprint_sha256"
            ),
            "post_migration_grant_set_sha256": details.get(
                "grant_set_sha256"
            ),
            "role_state_sha256": details.get("role_state_sha256"),
            "schema_fingerprint_algorithm": details.get(
                "schema_fingerprint_algorithm",
                SCHEMA_FINGERPRINT_ALGORITHM,
            ),
            "fence_configuration_sha256": details.get(
                "fence_configuration_sha256"
            ),
            "writer_fenced": details.get("writer_fenced"),
            "bot_fence_verification_only": False,
        }
        if (
            semantic["fenced_database_count"] != 1
            or semantic["unfenced_writer_count"] != 0
            or semantic["database_event_fence_verified"] is not True
            or details.get("least_privilege_role_set_verified") is not True
            or details.get("exact_release_grant_policy_verified") is not True
            or (role == "webapp_ir" and semantic["writer_fenced"] is not True)
        ):
            raise FrozenPrepareWorkerError(
                "database fence closure differs"
            )
    else:
        raise FrozenPrepareWorkerError("prepare phase is invalid")
    for key, value in semantic.items():
        if key.endswith("_sha256"):
            _nonzero_sha256(value, label=f"phase semantic {key}")
    if (
        "schema_fingerprint_algorithm" in semantic
        and semantic["schema_fingerprint_algorithm"]
        != SCHEMA_FINGERPRINT_ALGORITHM
    ):
        raise FrozenPrepareWorkerError(
            "phase schema fingerprint algorithm differs"
        )
    return semantic


def confirmation_phrase(context: LoadedRequest) -> str:
    return (
        "apply-production-shadow-frozen-prepare:"
        f"{context.document['operation_id']}:"
        f"{context.document['role']}:"
        f"{context.document['phase']}:"
        f"{context.document['restore_generation_sha256']}:"
        f"{context.sha256}"
    )


def _planned_result(context: LoadedRequest) -> dict[str, Any]:
    blocker = PHASE_EXECUTION_BLOCKERS.get(
        (
            str(context.document["phase"]),
            str(context.document["role"]),
        )
    )
    return {
        "schema": RESULT_SCHEMA,
        "status": "planned",
        "campaign_id": context.document["campaign_id"],
        "operation_id": context.document["operation_id"],
        "role": context.document["role"],
        "phase": context.document["phase"],
        "operation": context.document["operation"],
        "release_sha": context.document["release_sha"],
        "release_tree_sha": context.document["release_tree_sha"],
        "controller_manifest_sha256": context.document[
            "controller_manifest_sha256"
        ],
        "plan_sha256": context.document["plan_sha256"],
        "request_sha256": context.sha256,
        "restore_generation_sha256": context.document[
            "restore_generation_sha256"
        ],
        "steps": [
            {
                "step": step,
                "service": service,
                "timeout_seconds": timeout,
                "local_docker_unix_socket_only": service is not None,
            }
            for step, service, timeout in context.steps
        ],
        "required_confirmation": confirmation_phrase(context),
        "plan_only_default": True,
        "standalone_apply_supported": False,
        "controller_live_authority_required": True,
        "controller_liveness_pipe_required": True,
        "controller_liveness_protocol": (
            "anonymous-pipe-read-end; eof-or-data-cancels"
        ),
        "installed_compose_exact_phase_executable": blocker is None,
        "installed_compose_blocker": blocker,
        "business_write_allowed": False,
        "external_network_allowed": False,
        "ssh_allowed": False,
        "object_storage_allowed": False,
        "current_mutation_allowed": False,
        "legacy_mutation_allowed": False,
        "production_traffic_mutation_allowed": False,
        "output_mutated": False,
        "runtime_mutated": False,
    }


def _existing_publication(
    context: LoadedRequest,
    directory: Path,
    *,
    label: str,
) -> tuple[dict[str, Any], Path, str] | None:
    try:
        names = sorted(entry.name for entry in os.scandir(directory))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FrozenPrepareWorkerError(
            f"{label} namespace is unavailable"
        ) from exc
    if not names:
        return None
    if len(names) != 1:
        raise FrozenPrepareWorkerError(
            f"{label} namespace contains a foreign publication"
        )
    match = re.fullmatch(
        rf"{re.escape(str(context.document['phase']))}-"
        r"([0-9a-f]{64})\.json",
        names[0],
    )
    if match is None:
        raise FrozenPrepareWorkerError(
            f"{label} namespace contains a foreign publication"
        )
    path = directory / names[0]
    document, payload, digest = _read_json(path, label=label)
    if digest != match.group(1) or payload != _canonical_json(document) + b"\n":
        raise FrozenPrepareWorkerError(
            f"{label} publication digest or encoding differs"
        )
    _assert_exact_publication_namespace(
        directory,
        expected_filename=names[0],
        label=label,
    )
    return document, path, digest


def _publish_closure(
    context: LoadedRequest,
    journal: JournalState,
    authority_verifier: LiveAuthorityVerifier,
) -> tuple[dict[str, Any], str, Path, str]:
    semantic = _phase_semantic(context, journal)
    finalized_events = [
        event
        for event in journal.events
        if event["kind"] == "finalized"
    ]
    if (
        not journal.finalized
        or len(finalized_events) != 1
        or finalized_events[0]["semantic"] != semantic
    ):
        raise FrozenPrepareWorkerError(
            "prepare closure lacks exact live-authorized finalization"
        )
    authority_digests = [
        event["authority_sha256"] for event in journal.events
    ]
    evidence_core = {
        "schema": EVIDENCE_SCHEMA,
        "status": "completed",
        "campaign_id": context.document["campaign_id"],
        "operation_id": context.document["operation_id"],
        "role": context.document["role"],
        "phase": context.document["phase"],
        "operation": context.document["operation"],
        "release_sha": context.document["release_sha"],
        "release_tree_sha": context.document["release_tree_sha"],
        "controller_manifest_sha256": context.document[
            "controller_manifest_sha256"
        ],
        "plan_sha256": context.document["plan_sha256"],
        "request_sha256": context.sha256,
        "role_manifest_sha256": context.document[
            "role_manifest_sha256"
        ],
        "restore_completion_sha256": context.document[
            "restore_completion_sha256"
        ],
        "restore_phase_evidence_sha256": context.document[
            "restore_phase_evidence_sha256"
        ],
        "restore_generation_sha256": context.document[
            "restore_generation_sha256"
        ],
        "prior_result_sha256": context.document["prior_result_sha256"],
        "prepare_worker_sha256": context.document[
            "prepare_worker_sha256"
        ],
        "journal_event_count": len(journal.events),
        "journal_tail_sha256": journal.tail_sha256,
        "completed_steps": list(journal.completed_steps),
        "authority_verification_sha256": _sha256(
            _canonical_json(authority_digests)
        ),
        "business_write_observed": False,
        "app_service_started": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "production_traffic_mutated": False,
        "external_network_contacted": False,
        "ssh_contacted": False,
        "object_storage_contacted": False,
        "semantic": semantic,
    }
    if set(evidence_core) != EVIDENCE_FIELDS - {
        "publication_authority",
        "publication_authority_sha256",
    }:
        raise FrozenPrepareWorkerError(
            "internal prepare evidence fields differ"
        )
    evidence_directory = context.output_root / "evidence"
    _ensure_private_descendant(
        context.manifest.paths.secret_generation_root,
        evidence_directory,
        create=True,
    )
    existing_evidence = _existing_publication(
        context,
        evidence_directory,
        label="prepare evidence",
    )
    evidence_core_sha256 = _sha256(
        _canonical_json(evidence_core) + b"\n"
    )
    fresh_evidence_authority, _fresh_evidence_authority_sha256 = _authority(
        context,
        authority_verifier,
        boundary="publish:evidence",
        sequence=len(journal.events) + 1,
        previous_authority_sha256=_last_authority_sha256(journal),
        publication_kind="evidence",
        publication_payload_sha256=evidence_core_sha256,
    )
    if existing_evidence is None:
        evidence_authority = fresh_evidence_authority
        evidence_authority_sha256 = _validate_authority_document(
            evidence_authority,
            context=context,
        )
        evidence = {
            **evidence_core,
            "publication_authority": evidence_authority,
            "publication_authority_sha256": (
                evidence_authority_sha256
            ),
        }
        _validate_publication_authority(
            evidence,
            context=context,
            kind="evidence",
            expected_previous_authority_sha256=(
                _last_authority_sha256(journal)
            ),
        )
        evidence_sha256 = _sha256(_canonical_json(evidence) + b"\n")
        evidence_filename = (
            f"{context.document['phase']}-{evidence_sha256}.json"
        )
        _assert_exact_publication_namespace(
            evidence_directory,
            expected_filename=evidence_filename,
            label="prepare evidence",
        )
        evidence_path, observed_evidence_sha256, _evidence_publication = (
            _persist_new_document(
                evidence_directory,
                filename=evidence_filename,
                document=evidence,
                label="prepare phase evidence",
            )
        )
        if observed_evidence_sha256 != evidence_sha256:
            raise FrozenPrepareWorkerError(
                "prepare evidence persisted digest differs"
            )
    else:
        evidence, evidence_path, evidence_sha256 = existing_evidence
        if (
            set(evidence) != EVIDENCE_FIELDS
            or {
                key: value
                for key, value in evidence.items()
                if key
                not in {
                    "publication_authority",
                    "publication_authority_sha256",
                }
            }
            != evidence_core
        ):
            raise FrozenPrepareWorkerError(
                "replayed prepare evidence differs"
            )
        evidence_authority_sha256 = _validate_publication_authority(
            evidence,
            context=context,
            kind="evidence",
            expected_previous_authority_sha256=(
                _last_authority_sha256(journal)
            ),
        )
        if (
            fresh_evidence_authority["publication_payload_sha256"]
            != _publication_core_sha256(evidence)
        ):
            raise FrozenPrepareWorkerError(
                "fresh evidence publication authority differs"
            )
    _assert_exact_publication_namespace(
        evidence_directory,
        expected_filename=evidence_path.name,
        label="prepare evidence",
    )
    runtime_mutated = _journal_runtime_mutated(context, journal)
    result_core = {
        "schema": RESULT_SCHEMA,
        "status": "completed",
        "campaign_id": context.document["campaign_id"],
        "operation_id": context.document["operation_id"],
        "role": context.document["role"],
        "phase": context.document["phase"],
        "operation": context.document["operation"],
        "release_sha": context.document["release_sha"],
        "release_tree_sha": context.document["release_tree_sha"],
        "controller_manifest_sha256": context.document[
            "controller_manifest_sha256"
        ],
        "plan_sha256": context.document["plan_sha256"],
        "request_sha256": context.sha256,
        "role_manifest_sha256": context.document[
            "role_manifest_sha256"
        ],
        "restore_completion_sha256": context.document[
            "restore_completion_sha256"
        ],
        "restore_phase_evidence_sha256": context.document[
            "restore_phase_evidence_sha256"
        ],
        "restore_generation_sha256": context.document[
            "restore_generation_sha256"
        ],
        "prior_result_sha256": context.document["prior_result_sha256"],
        "prepare_worker_sha256": context.document[
            "prepare_worker_sha256"
        ],
        "journal_event_count": len(journal.events),
        "journal_tail_sha256": journal.tail_sha256,
        "evidence_path": str(evidence_path),
        "evidence_sha256": evidence_sha256,
        "semantic": semantic,
        "runtime_mutated": runtime_mutated,
        "business_write_observed": False,
        "app_service_started": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "production_traffic_mutated": False,
        "external_network_contacted": False,
        "ssh_contacted": False,
        "object_storage_contacted": False,
    }
    if set(result_core) != RESULT_FIELDS - {
        "publication_authority",
        "publication_authority_sha256",
    }:
        raise FrozenPrepareWorkerError(
            "internal prepare result fields differ"
        )
    result_directory = context.output_root / "results"
    _ensure_private_descendant(
        context.manifest.paths.secret_generation_root,
        result_directory,
        create=True,
    )
    existing_result = _existing_publication(
        context,
        result_directory,
        label="prepare result",
    )
    result_core_sha256 = _sha256(
        _canonical_json(result_core) + b"\n"
    )
    fresh_result_authority, _fresh_result_authority_sha256 = _authority(
        context,
        authority_verifier,
        boundary="publish:result",
        sequence=len(journal.events) + 2,
        previous_authority_sha256=evidence_authority_sha256,
        publication_kind="result",
        publication_payload_sha256=result_core_sha256,
    )
    if existing_result is None:
        result_authority = fresh_result_authority
        result_authority_sha256 = _validate_authority_document(
            result_authority,
            context=context,
        )
        result = {
            **result_core,
            "publication_authority": result_authority,
            "publication_authority_sha256": result_authority_sha256,
        }
        _validate_publication_authority(
            result,
            context=context,
            kind="result",
            expected_previous_authority_sha256=(
                evidence_authority_sha256
            ),
        )
        result_sha256 = _sha256(_canonical_json(result) + b"\n")
        result_filename = (
            f"{context.document['phase']}-{result_sha256}.json"
        )
        _assert_exact_publication_namespace(
            result_directory,
            expected_filename=result_filename,
            label="prepare result",
        )
        result_path, observed_result_sha256, publication = (
            _persist_new_document(
                result_directory,
                filename=result_filename,
                document=result,
                label="prepare phase result",
            )
        )
        if observed_result_sha256 != result_sha256:
            raise FrozenPrepareWorkerError(
                "prepare result persisted digest differs"
            )
    else:
        result, result_path, result_sha256 = existing_result
        if (
            set(result) != RESULT_FIELDS
            or {
                key: value
                for key, value in result.items()
                if key
                not in {
                    "publication_authority",
                    "publication_authority_sha256",
                }
            }
            != result_core
        ):
            raise FrozenPrepareWorkerError(
                "replayed prepare result differs"
            )
        _validate_publication_authority(
            result,
            context=context,
            kind="result",
            expected_previous_authority_sha256=(
                evidence_authority_sha256
            ),
        )
        if (
            fresh_result_authority["publication_payload_sha256"]
            != _publication_core_sha256(result)
        ):
            raise FrozenPrepareWorkerError(
                "fresh result publication authority differs"
            )
        publication = "reused"
    _assert_exact_publication_namespace(
        result_directory,
        expected_filename=result_path.name,
        label="prepare result",
    )
    return result, result_sha256, result_path, publication


def _must_force_current_release_invocation(step: str) -> bool:
    # Role password rotation cannot be proven from PostgreSQL catalog
    # readback.  Both role phases must rerun their idempotent release service
    # after lost output so credentials are bound to this generation's secret
    # input rather than adopted from foreign state.
    return step in {"roles-pre", "roles-post", "database-fence"}


def _reject_foreign_satisfied_state(
    context: LoadedRequest,
    *,
    step: str,
    observation: Mapping[str, Any],
) -> None:
    if not observation["satisfied"]:
        return
    if step == "roles-pre":
        raise FrozenPrepareWorkerError(
            "refusing to adopt pre-existing runtime roles without a journal"
        )
    if (
        step == "migrate"
        and context.manifest.source_database.alembic_revision
        != context.manifest.target_migration_revision
    ):
        raise FrozenPrepareWorkerError(
            "refusing to adopt an already-migrated database without a journal"
        )
    if step == "writer-fence":
        raise FrozenPrepareWorkerError(
            "refusing to adopt an already-fenced writer without a journal"
        )


@contextmanager
def _active_attempt_safety_cleanup(
    context: LoadedRequest,
    backend: PrepareBackend,
    liveness: ControllerLivenessGuard,
):  # noqa: ANN202
    try:
        yield
    except BaseException as exc:
        try:
            journal = _load_journal(context)
            if journal.active_step is not None:
                cancel_active = getattr(
                    backend,
                    "cancel_active_oneoff",
                    None,
                )
                if cancel_active is None:
                    raise FrozenPrepareWorkerError(
                        "active prepare backend has no safety cleanup"
                    )
                _validate_cancellation_cleanup(
                    cancel_active(
                        step=journal.active_step,
                        attempt=journal.active_attempt,
                        started_event_sha256=str(
                            journal.active_started_sha256
                        ),
                    )
                )
        except BaseException as cleanup_exc:
            raise FrozenPrepareWorkerError(
                "active prepare one-off safety cleanup failed"
            ) from cleanup_exc
        if liveness.cancelled and not isinstance(
            exc,
            FrozenPrepareCancellation,
        ):
            raise liveness.cancellation_error() from exc
        raise


def _bind_backend_sql_scope(
    backend: PrepareBackend,
    *,
    step: str,
    attempt: int,
    started_event_sha256: str,
    stage: str,
) -> None:
    binder = getattr(backend, "_bind_sql_scope", None)
    if binder is not None:
        binder(
            step=step,
            attempt=attempt,
            started_event_sha256=started_event_sha256,
            stage=stage,
        )


def _recover_backend_sql_oneoffs(backend: PrepareBackend) -> None:
    recover = getattr(backend, "recover_sql_oneoffs", None)
    if recover is not None:
        _validate_cancellation_cleanup(recover())


def _execute_apply(
    *,
    context: LoadedRequest,
    authority_verifier: LiveAuthorityVerifier,
    liveness: ControllerLivenessGuard,
) -> dict[str, Any]:
    liveness.check()
    backend: PrepareBackend = LocalDockerPrepareBackend(
        context,
        RESTORE.SubprocessDockerRunner(),
    )

    liveness.check()
    _authority(
        context,
        authority_verifier,
        boundary="open:phase-lock",
        sequence=1,
        previous_authority_sha256=ZERO_SHA256,
    )
    with (
        _phase_lock(context),
        _active_attempt_safety_cleanup(context, backend, liveness),
    ):
        liveness.check()
        journal = _load_journal(context)
        _recover_backend_sql_oneoffs(backend)
        liveness.check()
        journal = _load_journal(context)
        expected_steps = [row[0] for row in context.steps]
        if (
            list(journal.completed_steps)
            != expected_steps[: len(journal.completed_steps)]
            or (
                journal.active_step is not None
                and journal.active_step
                != expected_steps[len(journal.completed_steps)]
            )
        ):
            raise FrozenPrepareWorkerError(
                "prepare journal prefix is invalid"
            )
        for step in expected_steps[len(journal.completed_steps) :]:
            liveness.check()
            journal = _load_journal(context)
            if (
                journal.active_step is not None
                and journal.active_step != step
            ):
                raise FrozenPrepareWorkerError(
                    "prepare journal has a foreign active step"
                )
            active = journal.active_step == step
            if active:
                inspect_residue = getattr(
                    backend,
                    "inspect_residue",
                    None,
                )
                cleanup_residue = getattr(
                    backend,
                    "cleanup_residue",
                    None,
                )
                if (
                    inspect_residue is None
                    or cleanup_residue is None
                ):
                    residue = {
                        "residue_count": 0,
                        "residue_identity_sha256": None,
                    }
                else:
                    residue = dict(
                        inspect_residue(
                            step=step,
                            attempt=journal.active_attempt,
                            started_event_sha256=str(
                                journal.active_started_sha256
                            ),
                        )
                    )
                if (
                    set(residue)
                    != {
                        "residue_count",
                        "residue_identity_sha256",
                    }
                    or not isinstance(residue["residue_count"], int)
                    or isinstance(residue["residue_count"], bool)
                    or residue["residue_count"] not in {0, 1}
                    or (
                        residue["residue_count"] == 0
                        and residue["residue_identity_sha256"] is not None
                    )
                ):
                    raise FrozenPrepareWorkerError(
                        "prepare residue inspection is invalid"
                    )
                if residue["residue_count"] == 1:
                    residue_sha256 = _nonzero_sha256(
                        residue["residue_identity_sha256"],
                        label="prepare residue identity",
                    )
                    if any(
                        event["kind"] == "cleanup"
                        and event["step"] == step
                        and event["attempt"] == journal.active_attempt
                        for event in journal.events
                    ):
                        raise FrozenPrepareWorkerError(
                            "prepare residue reappeared after cleanup"
                        )
                    cleanup_authority, _cleanup_authority_sha256 = (
                        _authority(
                            context,
                            authority_verifier,
                            boundary=(
                                f"cleanup:{step}:attempt:"
                                f"{journal.active_attempt}"
                            ),
                            sequence=len(journal.events) + 1,
                            previous_authority_sha256=(
                                _last_authority_sha256(journal)
                            ),
                        )
                    )
                    cleanup = dict(
                        cleanup_residue(
                            step=step,
                            attempt=journal.active_attempt,
                            started_event_sha256=str(
                                journal.active_started_sha256
                            ),
                        )
                    )
                    if (
                        set(cleanup) != CLEANUP_SEMANTIC_FIELDS
                        or not isinstance(
                            cleanup["residue_count"],
                            int,
                        )
                        or isinstance(
                            cleanup["residue_count"],
                            bool,
                        )
                        or not isinstance(cleanup["removed_count"], int)
                        or isinstance(cleanup["removed_count"], bool)
                        or cleanup["residue_count"] != 1
                        or cleanup["removed_count"] != 1
                        or cleanup["residue_identity_sha256"]
                        != residue_sha256
                        or cleanup["persistent_volume_removed"] is not False
                        or cleanup["generation_data_mutated"] is not False
                    ):
                        raise FrozenPrepareWorkerError(
                            "prepare residue cleanup closure differs"
                        )
                    _append_event(
                        context,
                        journal,
                        kind="cleanup",
                        step=step,
                        attempt=journal.active_attempt,
                        authority=cleanup_authority,
                        command_invoked=True,
                        recovered=True,
                        started_event_sha256=(
                            journal.active_started_sha256
                        ),
                        semantic=cleanup,
                    )
                    journal = _load_journal(context)
            active = journal.active_step == step
            _bind_backend_sql_scope(
                backend,
                step=step,
                attempt=journal.active_attempt if active else 0,
                started_event_sha256=(
                    str(journal.active_started_sha256)
                    if active
                    else ZERO_SHA256
                ),
                stage=(
                    "recovery-observe"
                    if active
                    else "pre-start-observe"
                ),
            )
            observation = _validate_observation(
                backend.observe(step),
                context=context,
                step=step,
            )
            force = _must_force_current_release_invocation(step)
            if not active:
                _reject_foreign_satisfied_state(
                    context,
                    step=step,
                    observation=observation,
                )
            can_reconcile = active and observation["satisfied"] and not force
            if can_reconcile:
                authority, _authority_sha256 = _authority(
                    context,
                    authority_verifier,
                    boundary=(
                        f"reconcile:{step}:attempt:{journal.active_attempt}"
                    ),
                    sequence=len(journal.events) + 1,
                    previous_authority_sha256=_last_authority_sha256(
                        journal
                    ),
                )
                semantic = _validate_step_semantic(
                    {
                        "observation": observation,
                        "execution": _recovered_execution(context, step),
                    },
                    context=context,
                    step=step,
                )
                _append_event(
                    context,
                    journal,
                    kind="completed",
                    step=step,
                    attempt=journal.active_attempt,
                    authority=authority,
                    command_invoked=False,
                    recovered=True,
                    started_event_sha256=journal.active_started_sha256,
                    semantic=semantic,
                )
                continue

            if (
                active
                and journal.active_attempt >= MAX_ATTEMPTS_PER_STEP
            ):
                raise FrozenPrepareWorkerError(
                    "prepare step exceeded its bounded recovery attempts"
                )
            attempt = journal.active_attempt + 1 if active else 1
            before_authority, _before_sha256 = _authority(
                context,
                authority_verifier,
                boundary=f"before:{step}:attempt:{attempt}",
                sequence=len(journal.events) + 1,
                previous_authority_sha256=_last_authority_sha256(journal),
            )
            started = _append_event(
                context,
                journal,
                kind="started",
                step=step,
                attempt=attempt,
                authority=before_authority,
                command_invoked=False,
                recovered=False,
                started_event_sha256=None,
                semantic=None,
            )
            journal = _load_journal(context)

            no_op = (
                observation["satisfied"]
                and not force
                and step == "migrate"
                and (
                    context.manifest.source_database.alembic_revision
                    == context.manifest.target_migration_revision
                )
            )
            execution = (
                _recovered_execution(context, step)
                if no_op
                else None
            )
            if execution is None:
                raw_execution = backend.run_step(
                    step,
                    attempt=attempt,
                    started_event_sha256=started["event_sha256"],
                )
                execution = _validate_execution(
                    raw_execution,
                    context=context,
                    step=step,
                )
            liveness.check()
            _bind_backend_sql_scope(
                backend,
                step=step,
                attempt=attempt,
                started_event_sha256=started["event_sha256"],
                stage="post-run-observe",
            )
            after_observation = _validate_observation(
                backend.observe(step),
                context=context,
                step=step,
            )
            if after_observation["satisfied"] is not True:
                raise FrozenPrepareWorkerError(
                    "prepare step did not reach its exact readback"
                )
            after_authority, _after_sha256 = _authority(
                context,
                authority_verifier,
                boundary=f"after:{step}:attempt:{attempt}",
                sequence=len(journal.events) + 1,
                previous_authority_sha256=_last_authority_sha256(journal),
            )
            semantic = _validate_step_semantic(
                {
                    "observation": after_observation,
                    "execution": execution,
                },
                context=context,
                step=step,
            )
            _append_event(
                context,
                journal,
                kind="completed",
                step=step,
                attempt=attempt,
                authority=after_authority,
                command_invoked=execution["command_invoked"],
                recovered=False,
                started_event_sha256=started["event_sha256"],
                semantic=semantic,
            )

        liveness.check()
        journal = _load_journal(context)
        if (
            list(journal.completed_steps) != expected_steps
            or journal.active_step is not None
        ):
            raise FrozenPrepareWorkerError(
                "prepare journal did not complete every exact step"
            )
        completed_step_semantics = _completed_step_semantics(
            context,
            journal,
        )
        final_step = expected_steps[-1]
        for step in expected_steps:
            completed_event = next(
                event
                for event in journal.events
                if event["kind"] == "completed"
                and event["step"] == step
            )
            _bind_backend_sql_scope(
                backend,
                step=step,
                attempt=completed_event["attempt"],
                started_event_sha256=completed_event[
                    "started_event_sha256"
                ],
                stage="final-readback",
            )
            current = _validate_observation(
                backend.observe(step),
                context=context,
                step=step,
            )
            if current["satisfied"] is not True:
                raise FrozenPrepareWorkerError(
                    "prepare final readback drifted after completion"
                )
            if (
                step == final_step
                and current
                != completed_step_semantics[step]["observation"]
            ):
                raise FrozenPrepareWorkerError(
                    "prepare final observation differs from journaled readback"
                )
        if not journal.finalized:
            closure_semantic = _phase_semantic(context, journal)
            final_authority, _final_authority_sha256 = _authority(
                context,
                authority_verifier,
                boundary=f"finalize:{PHASE_CLOSURE_STEP}",
                sequence=len(journal.events) + 1,
                previous_authority_sha256=_last_authority_sha256(
                    journal
                ),
            )
            _append_event(
                context,
                journal,
                kind="finalized",
                step=PHASE_CLOSURE_STEP,
                attempt=0,
                authority=final_authority,
                command_invoked=False,
                recovered=False,
                started_event_sha256=None,
                semantic=closure_semantic,
            )
            journal = _load_journal(context)
        if not journal.finalized:
            raise FrozenPrepareWorkerError(
                "prepare journal did not finalize under live authority"
            )
        result, result_sha256, result_path, publication = (
            _publish_closure(
                context,
                journal,
                authority_verifier,
            )
        )
        liveness.check()
    return {
        **_planned_result(context),
        "status": "completed",
        "output_mutated": True,
        "runtime_mutated": result["runtime_mutated"],
        "completed_steps": expected_steps,
        "journal_event_count": len(journal.events),
        "journal_tail_sha256": journal.tail_sha256,
        "result": result,
        "result_path": str(result_path),
        "result_sha256": result_sha256,
        "result_publication": publication,
    }


def execute(
    *,
    request_path: Path,
    apply: bool = False,
    confirm: str | None = None,
    authority_verifier: LiveAuthorityVerifier | None = None,
    control_fd: int | None = None,
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise FrozenPrepareWorkerError(
            "frozen prepare worker must run as root"
        )
    context = load_request(request_path)
    if not apply:
        return _planned_result(context)
    if confirm != confirmation_phrase(context):
        raise FrozenPrepareWorkerError(
            "frozen prepare confirmation does not match the exact request"
        )
    if authority_verifier is None:
        raise FrozenPrepareWorkerError(
            "apply requires a controller-owned live authority verifier"
        )
    if control_fd is None:
        raise FrozenPrepareWorkerError(
            "apply requires a controller-owned liveness pipe"
        )
    with ControllerLivenessGuard(control_fd) as liveness:
        return _execute_apply(
            context=context,
            authority_verifier=authority_verifier,
            liveness=liveness,
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.apply:
            raise FrozenPrepareWorkerError(
                "standalone apply is disabled; use the controller "
                "orchestrator Python API with live authority"
            )
        result = execute(
            request_path=args.request,
            apply=False,
            confirm=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except FrozenPrepareWorkerError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "output_mutated": False,
                    "runtime_mutated": False,
                    "business_write_observed": False,
                    "app_service_started": False,
                    "current_mutated": False,
                    "legacy_mutated": False,
                    "production_traffic_mutated": False,
                    "external_network_contacted": False,
                    "ssh_contacted": False,
                    "object_storage_contacted": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
