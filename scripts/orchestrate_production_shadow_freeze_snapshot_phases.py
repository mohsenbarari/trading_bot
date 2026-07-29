#!/usr/bin/env python3
"""Bridge the frozen-final workflow into three public cutover phases.

The standalone frozen-snapshot coordinator owns the dangerous host work:
freezing the exact legacy writer sets, proving them stopped, taking final
snapshots, and leaving the writers stopped.  This module accepts only its
root-private, content-bound outputs plus a *fresh* legacy-frozen Nginx
readback.  It derives the public phase claims from those outputs and advances
exactly these adjacent cutover phases:

* ``stop_legacy_writers``
* ``zero_writer_surface_readback``
* ``final_snapshot_hashes``

It has no restore, writer-start, rollback, postcommit, SSH, Docker, or Object
Storage code path.  A consumed R2 receipt may be reconciled after its short
challenge expires, but only while its embedded capture time remains inside
the public verifier phase-age window; this is evidence reuse, not a live
zero-writer check.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import stat
import sys
import threading
from typing import Any, Iterator, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_current_frozen_verification as CURRENT,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_frozen_snapshots as FROZEN,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_nginx_generations as NGINX,
)
from scripts import produce_production_shadow_source_snapshot as SOURCE  # noqa: E402
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import production_shadow_legacy_writer_freeze as FREEZE  # noqa: E402
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402


PHASES = (
    "stop_legacy_writers",
    "zero_writer_surface_readback",
    "final_snapshot_hashes",
)
FIRST_PHASE_INDEX = CONTROLLER.PHASES.index(PHASES[0])
if tuple(
    CONTROLLER.PHASES[FIRST_PHASE_INDEX : FIRST_PHASE_INDEX + len(PHASES)]
) != PHASES:
    raise RuntimeError("freeze/snapshot bridge phases are not adjacent")

ROLE_ORDER = ("bot_fi", "webapp_fi")
REQUEST_SCHEMA = "production-shadow-freeze-snapshot-phase-bridge-request-v2"
RESULT_SCHEMA = "production-shadow-freeze-snapshot-phase-bridge-result-v2"
ROLE_VALIDATION_SCHEMA = "production-shadow-host-agent-validation-v1"
CLAIM_SOURCE_SCHEMA = "production-shadow-phase-claim-source-v1"
PHASE_AGGREGATE_SCHEMA = (
    "production-shadow-freeze-snapshot-phase-aggregate-v1"
)
SOURCE_CLOSURE_SCHEMA = (
    "production-shadow-freeze-snapshot-source-closure-v2"
)
FAILURE_SCHEMA = "production-shadow-freeze-snapshot-phase-failure-v1"

ZERO_SHA256 = "0" * 64
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = SOURCE.MAX_ARTIFACT_BYTES
CURRENT_VERIFICATION_MAX_AGE = min(
    VERIFY.PHASE_MAX_AGE.get(phase, VERIFY.MAX_EVIDENCE_AGE)
    for phase in PHASES
)
EOF_POLL_SECONDS = 0.025
MAX_CONTROL_FDS = 4096


def _fresh_observation() -> datetime:
    """Return the controller's real UTC time for apply-time freshness checks."""

    return datetime.now(timezone.utc).astimezone(timezone.utc)

REQUEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_path",
        "manifest_sha256",
        "plan_sha256",
        "approval_path",
        "approval_sha256",
        "approval_policy_path",
        "approval_policy_sha256",
        "output_root",
        "prior_phase_evidence",
        "frozen_snapshot_result",
        "current_frozen_verification_receipt",
        "nginx_readback_receipt",
        "roles",
        "constraints",
    }
)
REFERENCE_FIELDS = frozenset({"path", "sha256"})
ROLE_SOURCE_FIELDS = frozenset(
    {
        "binding",
        "freeze_evidence",
        "snapshot_manifest",
    }
)
CONSTRAINT_FIELDS = frozenset(
    {
        "standalone_workers_completed",
        "exact_legacy_writer_sets_required",
        "fresh_host_verify_required",
        "fresh_three_vhost_readback_required",
        "caller_truth_values_forbidden",
        "legacy_writers_remain_stopped",
        "redis_restore_forbidden",
        "restore_forbidden",
        "writer_restart_forbidden",
        "postcommit_forbidden",
        "create_only_evidence_required",
        "controller_eof_authority_required",
    }
)
EXPECTED_CONSTRAINTS = {
    "standalone_workers_completed": True,
    "exact_legacy_writer_sets_required": True,
    "fresh_host_verify_required": True,
    "fresh_three_vhost_readback_required": True,
    "caller_truth_values_forbidden": True,
    "legacy_writers_remain_stopped": True,
    "redis_restore_forbidden": True,
    "restore_forbidden": True,
    "writer_restart_forbidden": True,
    "postcommit_forbidden": True,
    "create_only_evidence_required": True,
    "controller_eof_authority_required": True,
}

FROZEN_RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "nginx_aggregate_sha256",
        "state_receipt_sha256",
        "lease_claim_sha256",
        "outcome_sha256",
        "consumption_sha256",
        "roles",
        "collection_root",
        "outcome_path",
        "journal_path",
        "journal_state_sha256",
        "public_phase",
        "public_phase_handoff_sha256",
        "public_phase_start_journal_state_sha256",
        "public_phase_start_journal_event_tail_sha256",
        "public_phase_start_journal_event_count",
        "live_lease_outcome",
        "legacy_writers_frozen",
        "automatic_restore_performed",
        "pull_policy",
        "build_performed",
        "object_storage_used",
        "wa_contacted",
    }
)
FROZEN_ROLE_FIELDS = frozenset(
    {
        "host",
        "transport",
        "binding_sha256",
        "freeze_evidence_sha256",
        "lease_claim_sha256",
        "manifest_binding_sha256",
        "files",
    }
)
class FreezeSnapshotPhaseBridgeError(RuntimeError):
    """The public phase bridge cannot safely continue."""


class FreezeSnapshotPhaseBridgeCancellation(FreezeSnapshotPhaseBridgeError):
    """Controller authority was lost or the process was cancelled."""


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    nlink: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class SecureRecord:
    path: Path
    payload: bytes
    sha256: str
    identity: FileIdentity
    document: dict[str, Any]


@dataclass(frozen=True)
class BridgeContext:
    request: SecureRecord
    manifest_path: Path
    approval_path: Path
    approval_policy_path: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    plan: dict[str, Any]
    plan_sha256: str
    output_root: Path
    prior_paths: Mapping[str, Path]


@dataclass(frozen=True)
class ValidatedSources:
    records: Mapping[str, SecureRecord]
    bindings: Mapping[str, SOURCE.SnapshotBinding]
    freeze_results: Mapping[str, Mapping[str, Any]]
    freeze_evidence: Mapping[str, Mapping[str, Any]]
    snapshots: Mapping[str, Mapping[str, Any]]
    frozen_result: Mapping[str, Any]
    current_verification_receipt: Mapping[str, Any]
    nginx_receipt: Mapping[str, Any]
    source_closure_sha256: str


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
        raise FreezeSnapshotPhaseBridgeError(
            "value is not canonical JSON"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _aggregate_hash(value: Any) -> str:
    return _sha256(_canonical_json(value))


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FreezeSnapshotPhaseBridgeError(
                f"duplicate JSON field: {key}"
            )
        result[key] = value
    return result


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or CONTROLLER.SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise FreezeSnapshotPhaseBridgeError(
            f"{label} SHA-256 is invalid"
        )
    return value


def _absolute_path(value: Any, *, label: str) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise FreezeSnapshotPhaseBridgeError(
            f"{label} path is invalid"
        ) from exc
    if not path.is_absolute() or ".." in path.parts:
        raise FreezeSnapshotPhaseBridgeError(
            f"{label} must be an absolute path"
        )
    return path


def _identity(metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        nlink=metadata.st_nlink,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
    )


def _read_private_json(
    path: Path,
    *,
    label: str,
    expected_sha256: str | None = None,
) -> SecureRecord:
    path = _absolute_path(path, label=label)
    descriptor = -1
    primary: BaseException | None = None
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 1 <= before.st_size <= MAX_JSON_BYTES
        ):
            raise FreezeSnapshotPhaseBridgeError(
                f"{label} identity or ownership is unsafe"
            )
        chunks: list[bytes] = []
        observed_size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_JSON_BYTES + 1))
            if not chunk:
                break
            observed_size += len(chunk)
            if observed_size > MAX_JSON_BYTES:
                raise FreezeSnapshotPhaseBridgeError(
                    f"{label} is oversized"
                )
            chunks.append(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable_fields = (
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
            observed_size != before.st_size
            or any(
                getattr(before, field) != getattr(after, field)
                for field in stable_fields
            )
        ):
            raise FreezeSnapshotPhaseBridgeError(
                f"{label} changed while being read"
            )
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        primary = FreezeSnapshotPhaseBridgeError(
            f"{label} is not secure strict JSON"
        )
        raise primary from exc
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except BaseException as exc:
                if primary is not None:
                    try:
                        primary.add_note(
                            f"{label} descriptor cleanup failed: "
                            f"{type(exc).__name__}:{exc}"
                        )
                    except (AttributeError, TypeError):
                        pass
                else:
                    raise FreezeSnapshotPhaseBridgeError(
                        f"{label} descriptor cleanup failed"
                    ) from exc
    if not isinstance(document, dict):
        raise FreezeSnapshotPhaseBridgeError(
            f"{label} JSON root must be an object"
        )
    digest = _sha256(payload)
    if expected_sha256 is not None and digest != _nonzero_sha256(
        expected_sha256,
        label=label,
    ):
        raise FreezeSnapshotPhaseBridgeError(f"{label} digest differs")
    return SecureRecord(
        path=path,
        payload=payload,
        sha256=digest,
        identity=_identity(before),
        document=document,
    )


def _record_from_reference(value: Any, *, label: str) -> SecureRecord:
    if not isinstance(value, dict) or set(value) != REFERENCE_FIELDS:
        raise FreezeSnapshotPhaseBridgeError(
            f"{label} reference fields are not exact"
        )
    return _read_private_json(
        _absolute_path(value["path"], label=label),
        label=label,
        expected_sha256=value["sha256"],
    )


def _ensure_private_directory(path: Path) -> None:
    path = _absolute_path(path, label="bridge output directory")
    missing: list[Path] = []
    cursor = path
    while True:
        try:
            metadata = os.stat(cursor, follow_symlinks=False)
            break
        except FileNotFoundError:
            missing.append(cursor)
            parent = cursor.parent
            if parent == cursor:
                raise FreezeSnapshotPhaseBridgeError(
                    "bridge output has no existing trusted ancestor"
                )
            cursor = parent
        except OSError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                "bridge output directory cannot be inspected"
            ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "bridge output ancestor is unsafe"
        )
    for child in reversed(missing):
        try:
            child.mkdir(mode=0o700)
        except OSError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                "bridge output directory cannot be created"
            ) from exc
    metadata = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "bridge output directory must be root:root mode 0700"
        )


def _persist_document(
    directory: Path,
    *,
    prefix: str,
    document: Mapping[str, Any],
) -> tuple[Path, str]:
    _ensure_private_directory(directory)
    payload = _canonical_json(dict(document)) + b"\n"
    digest = _sha256(payload)
    path = directory / f"{prefix}.{digest}.json"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=f"{prefix} evidence",
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError:
        try:
            observed = read_secure_bytes(
                path,
                label=f"existing {prefix} evidence",
                owner_uid=0,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                f"{prefix} evidence cannot be published"
            ) from exc
        if observed != payload:
            raise FreezeSnapshotPhaseBridgeError(
                f"existing {prefix} evidence differs"
            )
    return path, digest


def _assert_records_unchanged(records: Mapping[str, SecureRecord]) -> None:
    errors: list[str] = []
    for label, expected in records.items():
        try:
            observed = _read_private_json(
                expected.path,
                label=label,
                expected_sha256=expected.sha256,
            )
        except FreezeSnapshotPhaseBridgeError as exc:
            errors.append(f"{label}:{exc}")
            continue
        if (
            observed.identity != expected.identity
            or observed.payload != expected.payload
        ):
            errors.append(f"{label}:identity changed")
    if errors:
        raise FreezeSnapshotPhaseBridgeError(
            "validated source changed: " + "; ".join(errors)
        )


def _revalidate_source_closure(
    context: BridgeContext,
    *,
    expected: ValidatedSources,
    checkpoint: str,
) -> ValidatedSources:
    """Freshly reread every source and require the exact prior closure."""

    observed = _validate_sources(context, now=_fresh_observation())
    if (
        observed.source_closure_sha256 != expected.source_closure_sha256
        or set(observed.records) != set(expected.records)
        or any(
            observed.records[label].identity != expected.records[label].identity
            or observed.records[label].payload != expected.records[label].payload
            for label in expected.records
        )
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "bridge sources changed " + checkpoint
        )
    return observed


def _reference_path(value: Any, *, label: str) -> tuple[Path, str]:
    if not isinstance(value, dict) or set(value) != REFERENCE_FIELDS:
        raise FreezeSnapshotPhaseBridgeError(
            f"{label} reference fields are not exact"
        )
    return (
        _absolute_path(value["path"], label=label),
        _nonzero_sha256(value["sha256"], label=label),
    )


def _load_request(path: Path) -> BridgeContext:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise FreezeSnapshotPhaseBridgeError(
            "freeze/snapshot phase bridge requires root:root"
        )
    request = _read_private_json(path, label="phase bridge request")
    document = request.document
    if (
        set(document) != REQUEST_FIELDS
        or document["schema"] != REQUEST_SCHEMA
        or document["status"] != "ready"
        or not isinstance(document["constraints"], dict)
        or set(document["constraints"]) != CONSTRAINT_FIELDS
        or document["constraints"] != EXPECTED_CONSTRAINTS
        or not isinstance(document["roles"], dict)
        or set(document["roles"]) != set(ROLE_ORDER)
        or any(
            not isinstance(row, dict)
            or set(row) != ROLE_SOURCE_FIELDS
            for row in document["roles"].values()
        )
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "phase bridge request fields or constraints differ"
        )
    manifest_path = _absolute_path(
        document["manifest_path"],
        label="cutover manifest",
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
    except CONTROLLER.CutoverContractError as exc:
        raise FreezeSnapshotPhaseBridgeError(
            "cutover manifest or plan is invalid"
        ) from exc
    expected = {
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "manifest_sha256": manifest_sha256,
        "plan_sha256": plan["plan_sha256"],
        "approval_sha256": manifest["artifacts"]["cutover_approval_sha256"],
        "approval_policy_sha256": manifest["artifacts"][
            "human_approval_policy_sha256"
        ],
    }
    if any(document.get(field) != value for field, value in expected.items()):
        raise FreezeSnapshotPhaseBridgeError(
            "phase bridge request differs from manifest or plan"
        )
    approval_path = _absolute_path(
        document["approval_path"],
        label="cutover approval",
    )
    policy_path = _absolute_path(
        document["approval_policy_path"],
        label="approval policy",
    )
    approval = _read_private_json(
        approval_path,
        label="cutover approval",
        expected_sha256=document["approval_sha256"],
    )
    policy = _read_private_json(
        policy_path,
        label="approval policy",
        expected_sha256=document["approval_policy_sha256"],
    )
    del approval, policy
    prior_names = CONTROLLER.PHASES[:FIRST_PHASE_INDEX]
    prior = document["prior_phase_evidence"]
    if not isinstance(prior, dict) or set(prior) != set(prior_names):
        raise FreezeSnapshotPhaseBridgeError(
            "prior phase evidence mapping is not the exact journal prefix"
        )
    prior_paths: dict[str, Path] = {}
    for phase in prior_names:
        prior_path, expected_digest = _reference_path(
            prior[phase],
            label=f"prior phase {phase}",
        )
        try:
            evidence, observed = VERIFY.read_root_only_evidence(prior_path)
        except VERIFY.PhaseEvidenceError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                f"prior phase {phase} evidence is unsafe"
            ) from exc
        if (
            observed != expected_digest
            or evidence.get("phase") != phase
            or evidence.get("campaign_id") != manifest["campaign_id"]
            or evidence.get("operation_id") != manifest["operation_id"]
            or evidence.get("release_sha") != manifest["release_sha"]
            or evidence.get("legacy_release_sha")
            != manifest["legacy_release_sha"]
            or evidence.get("manifest_sha256") != manifest_sha256
            or evidence.get("plan_sha256") != plan["plan_sha256"]
            or evidence.get("approval_sha256")
            != manifest["artifacts"]["cutover_approval_sha256"]
            or evidence.get("status") != "passed"
            or evidence.get("business_write_observed") is not False
        ):
            raise FreezeSnapshotPhaseBridgeError(
                f"prior phase {phase} evidence binding differs"
            )
        prior_paths[phase] = prior_path
    output_root = _absolute_path(
        document["output_root"],
        label="bridge output root",
    )
    expected_root = (
        Path(manifest["deployment"]["controller_evidence_root"])
        / "freeze-snapshot-phase-bridge"
    )
    if output_root != expected_root:
        raise FreezeSnapshotPhaseBridgeError(
            "bridge output root is not manifest-derived"
        )
    return BridgeContext(
        request=request,
        manifest_path=manifest_path,
        approval_path=approval_path,
        approval_policy_path=policy_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan=plan,
        plan_sha256=plan["plan_sha256"],
        output_root=output_root,
        prior_paths=prior_paths,
    )


def _verify_authorization(context: BridgeContext) -> None:
    try:
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            context.manifest,
            approval_path=context.approval_path,
            approval_policy_path=context.approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise FreezeSnapshotPhaseBridgeError(
            "production approval is invalid or expired"
        ) from exc


def _journal_bindings(context: BridgeContext) -> dict[str, str]:
    return {
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
    }


def _validate_journal_corridor(
    state: Mapping[str, Any],
    *,
    context: BridgeContext,
) -> dict[str, Any]:
    prefix = tuple(state.get("completed_phases", ()))
    allowed = {
        tuple(
            CONTROLLER.PHASES[
                : FIRST_PHASE_INDEX + completed_count
            ]
        )
        for completed_count in range(len(PHASES) + 1)
    }
    started = state.get("started_phase")
    if (
        any(state.get(key) != value for key, value in _journal_bindings(context).items())
        or prefix not in allowed
        or state.get("rollback_eligible") is not True
        or state.get("first_business_write_allowed") is not False
        or (
            state.get("status") == "phase_started"
            and started
            != CONTROLLER.PHASES[len(prefix)]
        )
        or (
            state.get("status") != "phase_started"
            and started is not None
        )
        or state.get("status") not in {"active", "phase_started"}
        or (
            len(prefix) == FIRST_PHASE_INDEX + len(PHASES)
            and (
                state.get("status") != "active"
                or started is not None
            )
        )
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "cutover journal is outside the exact freeze/snapshot corridor"
        )
    return dict(state)


def _validate_frozen_public_phase_start(
    state: Mapping[str, Any],
    *,
    context: BridgeContext,
    frozen_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Require the coordinator's durable phase-one start before bridge work."""

    state = _validate_journal_corridor(state, context=context)
    completed = list(state["completed_phases"])
    prefix = list(CONTROLLER.PHASES[:FIRST_PHASE_INDEX])
    event_count = frozen_result["public_phase_start_journal_event_count"]
    events = state.get("events")
    if (
        PHASES[0] not in completed
        and (
            state["status"] != "phase_started"
            or state["started_phase"] != PHASES[0]
        )
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "cutover journal lacks the coordinator-owned public phase start"
        )
    if (
        not isinstance(events, list)
        or len(events) < event_count
        or completed[:FIRST_PHASE_INDEX] != prefix
        or frozen_result["public_phase"] != PHASES[0]
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "cutover journal lacks the coordinator-owned public phase start"
        )
    start_event = events[event_count - 1]
    if (
        not isinstance(start_event, dict)
        or start_event.get("kind") != "phase_started"
        or start_event.get("phase") != PHASES[0]
        or start_event.get("event_hash")
        != frozen_result["public_phase_start_journal_event_tail_sha256"]
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "frozen result does not bind the public phase start event"
        )
    if PHASES[0] not in completed:
        if (
            state["state_sha256"]
            != frozen_result["public_phase_start_journal_state_sha256"]
            or state["event_tail_sha256"]
            != frozen_result["public_phase_start_journal_event_tail_sha256"]
            or len(events) != event_count
        ):
            raise FreezeSnapshotPhaseBridgeError(
                "public phase one is not at its coordinator-owned durable start"
            )
    return state


def _validate_frozen_result(
    value: Any,
    *,
    context: BridgeContext,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != FROZEN_RESULT_FIELDS
        or value.get("schema") != FROZEN.RESULT_SCHEMA
        or value.get("status") != "complete"
        or value.get("operation_id") != context.manifest["operation_id"]
        or value.get("release_sha") != context.manifest["release_sha"]
        or value.get("release_tree_sha")
        != context.manifest["release_tree_sha"]
        or not isinstance(value.get("roles"), dict)
        or set(value["roles"]) != set(ROLE_ORDER)
        or value.get("live_lease_outcome") != "handoff-shadow-readonly"
        or value.get("legacy_writers_frozen") is not True
        or value.get("automatic_restore_performed") is not False
        or value.get("pull_policy") != "never"
        or value.get("build_performed") is not False
        or value.get("object_storage_used") is not False
        or value.get("wa_contacted") is not False
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "frozen snapshot coordinator result differs"
        )
    for field in (
        "nginx_aggregate_sha256",
        "state_receipt_sha256",
        "lease_claim_sha256",
        "outcome_sha256",
        "consumption_sha256",
        "journal_state_sha256",
        "public_phase_handoff_sha256",
        "public_phase_start_journal_state_sha256",
        "public_phase_start_journal_event_tail_sha256",
    ):
        _nonzero_sha256(value[field], label=f"frozen result {field}")
    if (
        value["public_phase"] != PHASES[0]
        or type(value["public_phase_start_journal_event_count"]) is not int
        or value["public_phase_start_journal_event_count"] < 1
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "frozen result public phase handoff differs"
        )
    for role in ROLE_ORDER:
        row = value["roles"][role]
        if (
            not isinstance(row, dict)
            or set(row) != FROZEN_ROLE_FIELDS
            or row["host"] != FROZEN.ROLE_HOSTS[role]
            or row["transport"] != FROZEN.ROLE_TRANSPORTS[role]
            or not isinstance(row["files"], dict)
            or set(row["files"]) != set(FROZEN.SNAPSHOT_FILENAMES)
            or row["lease_claim_sha256"] != value["lease_claim_sha256"]
        ):
            raise FreezeSnapshotPhaseBridgeError(
                f"{role} frozen snapshot result closure differs"
            )
        for field in (
            "binding_sha256",
            "freeze_evidence_sha256",
            "manifest_binding_sha256",
        ):
            _nonzero_sha256(row[field], label=f"{role} frozen result {field}")
        for name, file_row in row["files"].items():
            if (
                not isinstance(file_row, dict)
                or set(file_row) != {"sha256", "bytes"}
                or type(file_row["bytes"]) is not int
                or not 1 <= file_row["bytes"] <= MAX_ARTIFACT_BYTES
            ):
                raise FreezeSnapshotPhaseBridgeError(
                    f"{role} frozen result file {name} differs"
                )
            _nonzero_sha256(
                file_row["sha256"],
                label=f"{role} frozen result {name}",
            )
    return dict(value)


def _load_current_verification_epoch(
    *,
    context: BridgeContext,
    frozen_result: Mapping[str, Any],
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    current_record: SecureRecord,
    nginx_record: SecureRecord,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Mapping[str, Any]]]:
    """Load R2 fresh first, then completed-only within public phase age."""
    observed_at_epoch = int(now.astimezone(timezone.utc).timestamp())
    current_kwargs = {
        "expected_sha256": current_record.sha256,
        "expected_operation_id": context.manifest["operation_id"],
        "expected_release_sha": context.manifest["release_sha"],
        "expected_release_tree_sha": context.manifest["release_tree_sha"],
        "expected_legacy_release_sha": context.manifest[
            "legacy_release_sha"
        ],
        "expected_nginx_aggregate_sha256": frozen_result[
            "nginx_aggregate_sha256"
        ],
        "expected_bindings": {
            role: bindings[role].canonical_sha256 for role in ROLE_ORDER
        },
        "expected_capture_state_receipt_sha256": frozen_result[
            "state_receipt_sha256"
        ],
        "observed_at_epoch": observed_at_epoch,
    }
    try:
        current_receipt, observed_current_sha256 = (
            CURRENT.load_current_frozen_verification_receipt(
                current_record.path,
                **current_kwargs,
            )
        )
    except CURRENT.CurrentFrozenVerificationError:
        try:
            current_receipt, observed_current_sha256 = (
                CURRENT.load_current_frozen_verification_receipt(
                    current_record.path,
                    **current_kwargs,
                    allow_historical_completed=True,
                )
            )
        except CURRENT.CurrentFrozenVerificationError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                "current frozen verification receipt is invalid"
            ) from exc
    captured_at = datetime.fromtimestamp(
        current_receipt["captured_at_epoch"],
        tz=timezone.utc,
    )
    if (
        captured_at > now + VERIFY.MAX_FUTURE_SKEW
        or now - captured_at > CURRENT_VERIFICATION_MAX_AGE
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "completed current frozen verification is outside phase age"
        )
    if (
        observed_current_sha256 != current_record.sha256
        or current_receipt != current_record.document
        or current_receipt["capture_lease_claim_sha256"]
        != frozen_result["lease_claim_sha256"]
        or current_receipt["capture_outcome_sha256"]
        != frozen_result["outcome_sha256"]
        or current_receipt["capture_lease_consumption_sha256"]
        != frozen_result["consumption_sha256"]
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "current frozen verification origin binding differs"
        )
    freeze_results: dict[str, Mapping[str, Any]] = {}
    for role in ROLE_ORDER:
        result = current_receipt["host_results"][role]
        if (
            result["freeze_evidence_live_lease_claim_sha256"]
            != frozen_result["lease_claim_sha256"]
            or result["freeze_evidence_sha256"]
            != frozen_result["roles"][role]["freeze_evidence_sha256"]
            or current_receipt["freeze_evidence"][role]
            != {
                "live_lease_claim_sha256": frozen_result[
                    "lease_claim_sha256"
                ],
                "sha256": frozen_result["roles"][role][
                    "freeze_evidence_sha256"
                ],
            }
        ):
            raise FreezeSnapshotPhaseBridgeError(
                f"{role} R1 freeze evidence differs from R2 verification"
            )
        freeze_results[role] = result
    expected_nginx_path = FROZEN.canonical_paths(
        context.manifest["operation_id"],
        context.manifest["release_sha"],
        state_receipt_sha256=current_receipt[
            "fresh_state_receipt_sha256"
        ],
    )["state_receipt"]
    if nginx_record.path != expected_nginx_path:
        raise FreezeSnapshotPhaseBridgeError(
            "fresh Nginx receipt path is not controller-canonical"
        )
    try:
        nginx_receipt, observed_receipt_sha256 = NGINX.load_state_receipt(
            nginx_record.path,
            "legacy-frozen",
            context.manifest["operation_id"],
            context.manifest["release_sha"],
            context.manifest["release_tree_sha"],
            frozen_result["nginx_aggregate_sha256"],
            observed_at_epoch=observed_at_epoch,
        )
    except NGINX.NginxCoordinatorError:
        try:
            nginx_receipt, observed_receipt_sha256 = (
                NGINX.load_state_receipt(
                    nginx_record.path,
                    "legacy-frozen",
                    context.manifest["operation_id"],
                    context.manifest["release_sha"],
                    context.manifest["release_tree_sha"],
                    frozen_result["nginx_aggregate_sha256"],
                    allow_historical=True,
                )
            )
        except NGINX.NginxCoordinatorError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                "fresh three-vhost Nginx readback receipt is invalid"
            ) from exc
    if (
        observed_receipt_sha256 != nginx_record.sha256
        or nginx_receipt != nginx_record.document
        or nginx_receipt.get("schema")
        != NGINX.PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA
        or observed_receipt_sha256
        != current_receipt["fresh_state_receipt_sha256"]
        or nginx_receipt["readback_challenge_sha256"]
        != current_receipt["readback_challenge_sha256"]
        or nginx_receipt["issued_at_epoch"]
        != current_receipt["issued_at_epoch"]
        or nginx_receipt["expires_at_epoch"]
        != current_receipt["expires_at_epoch"]
        or nginx_receipt["source_action"] != "readback"
        or nginx_receipt["coordinator_status"] != "read-back"
        or nginx_receipt["global_generation_sha256"]
        != context.manifest["artifacts"]["nginx_freeze_generation_sha256"]
        or nginx_receipt["global_generation_sha256"]
        != current_receipt["freeze_generation_sha256"]
        or any(
            current_receipt["host_results"][role][
                "role_freeze_generation_sha256"
            ]
            != nginx_receipt["readbacks"][role]["generation_sha256"]
            for role in ROLE_ORDER
        )
    ):
        raise FreezeSnapshotPhaseBridgeError(
            "Nginx receipt is not a fresh read-only legacy-frozen readback"
        )
    return current_receipt, nginx_receipt, freeze_results


def _validate_sources(
    context: BridgeContext,
    *,
    now: datetime,
) -> ValidatedSources:
    document = context.request.document
    records: dict[str, SecureRecord] = {}
    frozen_record = _record_from_reference(
        document["frozen_snapshot_result"],
        label="frozen snapshot coordinator result",
    )
    frozen_result = _validate_frozen_result(
        frozen_record.document,
        context=context,
    )
    records["frozen_snapshot_result"] = frozen_record
    current_record = _record_from_reference(
        document["current_frozen_verification_receipt"],
        label="current frozen verification receipt",
    )
    records["current_frozen_verification_receipt"] = current_record
    bindings: dict[str, SOURCE.SnapshotBinding] = {}
    freeze_evidence: dict[str, Mapping[str, Any]] = {}
    snapshots: dict[str, Mapping[str, Any]] = {}
    identities: set[tuple[int, int]] = set()
    for role in ROLE_ORDER:
        row = document["roles"][role]
        binding_record = _record_from_reference(
            row["binding"],
            label=f"{role} frozen-final binding",
        )
        try:
            binding = SOURCE.load_binding(binding_record.path)
        except SOURCE.SourceSnapshotError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                f"{role} frozen-final binding is invalid"
            ) from exc
        if (
            binding.role != role
            or binding.mode != "frozen-final"
            or binding.operation_id != context.manifest["operation_id"]
            or binding.release_sha != context.manifest["release_sha"]
            or binding.legacy_release_sha
            != context.manifest["legacy_release_sha"]
            or binding.controller_manifest_sha256
            != context.manifest_sha256
            or binding.approval_sha256
            != context.manifest["artifacts"]["cutover_approval_sha256"]
            or binding.canonical_sha256
            != frozen_result["roles"][role]["binding_sha256"]
            or binding.canonical_sha256
            != frozen_result["roles"][role]["manifest_binding_sha256"]
        ):
            raise FreezeSnapshotPhaseBridgeError(
                f"{role} frozen-final binding differs"
            )
        bindings[role] = binding
        records[f"{role}_binding"] = binding_record

        snapshot_record = _record_from_reference(
            row["snapshot_manifest"],
            label=f"{role} frozen snapshot manifest",
        )
        output_paths = SOURCE.OutputPaths(
            operation_root=snapshot_record.path.parent.parent.parent,
            role_root=snapshot_record.path.parent.parent,
            final=snapshot_record.path.parent,
            staging=snapshot_record.path.parent / ".unused",
            manifest=snapshot_record.path,
        )
        expected_freeze_sha256 = frozen_result["roles"][role][
            "freeze_evidence_sha256"
        ]
        try:
            snapshot = SOURCE.verify_completed_output(
                output_paths,
                binding,
                freeze_sha256=expected_freeze_sha256,
            )
        except SOURCE.SourceSnapshotError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                f"{role} frozen snapshot output is invalid"
            ) from exc
        manifest_inventory = frozen_result["roles"][role]["files"][
            SOURCE.MANIFEST_FILE
        ]
        if (
            snapshot_record.sha256 != manifest_inventory["sha256"]
            or snapshot_record.identity.size != manifest_inventory["bytes"]
            or snapshot_record.document != snapshot
        ):
            raise FreezeSnapshotPhaseBridgeError(
                f"{role} snapshot manifest differs from coordinator result"
            )
        for name in FROZEN.SNAPSHOT_FILENAMES:
            artifact = snapshot_record.path.parent / name
            maximum = (
                MAX_JSON_BYTES
                if name == SOURCE.MANIFEST_FILE
                else MAX_ARTIFACT_BYTES
            )
            try:
                observed = FROZEN._hash_file(  # noqa: SLF001
                    artifact,
                    label=f"{role} collected {name}",
                    required_uid=0,
                    expected_mode=0o600,
                    maximum=maximum,
                )
            except FROZEN.FrozenSnapshotOrchestratorError as exc:
                raise FreezeSnapshotPhaseBridgeError(
                    f"{role} collected snapshot artifact is unsafe"
                ) from exc
            expected_file = frozen_result["roles"][role]["files"][name]
            if observed != (
                expected_file["sha256"],
                expected_file["bytes"],
            ):
                raise FreezeSnapshotPhaseBridgeError(
                    f"{role} collected snapshot artifact differs"
                )
        snapshots[role] = snapshot
        records[f"{role}_snapshot_manifest"] = snapshot_record

        freeze_evidence_record = _record_from_reference(
            row["freeze_evidence"],
            label=f"{role} freeze evidence",
        )
        source_container_ids = {
            kind: snapshot["source"]["containers"][kind]["id"]
            for kind in SOURCE.SOURCE_CONTAINERS
        }
        try:
            freeze_document, observed_freeze_sha256 = (
                SOURCE.load_freeze_evidence(
                    freeze_evidence_record.path,
                    binding,
                    source_container_ids=source_container_ids,
                    live_lease_claim_sha256=frozen_result[
                        "lease_claim_sha256"
                    ],
                )
            )
        except SOURCE.SourceSnapshotError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                f"{role} freeze evidence is invalid"
            ) from exc
        if (
            observed_freeze_sha256 != freeze_evidence_record.sha256
            or observed_freeze_sha256 != expected_freeze_sha256
            or freeze_evidence_record.document != freeze_document
            or any(
                freeze_document[field] != 0
                for field in (
                    "write_capable_route_count",
                    "legacy_writer_process_count",
                    "writer_database_client_count",
                    "file_mutator_process_count",
                )
            )
            or freeze_document["freeze_active"] is not True
        ):
            raise FreezeSnapshotPhaseBridgeError(
                f"{role} freeze evidence zero-writer closure differs"
            )
        freeze_evidence[role] = freeze_document
        records[f"{role}_freeze_evidence"] = freeze_evidence_record

    nginx_record = _record_from_reference(
        document["nginx_readback_receipt"],
        label="fresh three-vhost Nginx readback",
    )
    current_receipt, nginx_receipt, freeze_results = (
        _load_current_verification_epoch(
            context=context,
            frozen_result=frozen_result,
            bindings=bindings,
            current_record=current_record,
            nginx_record=nginx_record,
            now=now,
        )
    )
    records["nginx_readback_receipt"] = nginx_record

    for record in records.values():
        key = (record.identity.device, record.identity.inode)
        if key in identities:
            raise FreezeSnapshotPhaseBridgeError(
                "independent bridge sources share one file identity"
            )
        identities.add(key)
    closure_rows = [
        {
            "label": label,
            "path": os.fspath(record.path),
            "sha256": record.sha256,
            "bytes": record.identity.size,
        }
        for label, record in sorted(records.items())
    ]
    closure = {
        "schema": SOURCE_CLOSURE_SCHEMA,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "sources": closure_rows,
        "caller_truth_values_accepted": False,
        "restore_performed": False,
        "writer_restart_performed": False,
    }
    _assert_records_unchanged(records)
    return ValidatedSources(
        records=records,
        bindings=bindings,
        freeze_results=freeze_results,
        freeze_evidence=freeze_evidence,
        snapshots=snapshots,
        frozen_result=frozen_result,
        current_verification_receipt=current_receipt,
        nginx_receipt=nginx_receipt,
        source_closure_sha256=_aggregate_hash(closure),
    )


def _derive_final_hashes(
    sources: ValidatedSources,
) -> tuple[str, str, str]:
    postgres_rows: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    redis_rows: list[dict[str, Any]] = []
    for role in ROLE_ORDER:
        snapshot = sources.snapshots[role]
        postgres_rows.append(
            {
                "role": role,
                "database_backup_sha256": snapshot["artifacts"][
                    "database-backup"
                ]["sha256"],
                "database_backup_bytes": snapshot["artifacts"][
                    "database-backup"
                ]["bytes"],
                "database_fingerprint_sha256": snapshot[
                    "source_database"
                ]["database_fingerprint_sha256"],
                "alembic_revision": snapshot["source_database"][
                    "alembic_revision"
                ],
            }
        )
        for kind in ("uploads", "audit"):
            artifact = snapshot["artifacts"][f"{kind}-archive"]
            tree = snapshot["file_snapshots"][kind]
            if not (
                tree["pre_tree_sha256"]
                == tree["archive_tree_sha256"]
                == tree["post_tree_sha256"]
                == artifact["restored_tree_sha256"]
            ):
                raise FreezeSnapshotPhaseBridgeError(
                    f"{role} {kind} pre/archive/post tree is unstable"
                )
            file_rows.append(
                {
                    "role": role,
                    "kind": kind,
                    "archive_sha256": artifact["sha256"],
                    "archive_bytes": artifact["bytes"],
                    "tree_sha256": tree["archive_tree_sha256"],
                    "member_count": tree["member_count"],
                    "expanded_bytes": tree["expanded_bytes"],
                }
            )
        redis = snapshot["redis_rollback_only"]
        if (
            redis["policy"] != "sealed-rollback-evidence-only"
            or redis["archive_created"] is not False
            or redis["restore"] is not False
            or snapshot["redis_restored"] is not False
        ):
            raise FreezeSnapshotPhaseBridgeError(
                f"{role} Redis is not sealed rollback-only evidence"
            )
        redis_rows.append(
            {
                "role": role,
                "source_volume": redis["source_volume"],
                "tree_sha256": redis["tree_sha256"],
                "metadata_sha256": redis["metadata_sha256"],
                "member_count": redis["member_count"],
                "bytes": redis["bytes"],
            }
        )
    return (
        _aggregate_hash(postgres_rows),
        _aggregate_hash(file_rows),
        _aggregate_hash(redis_rows),
    )


def _phase_claims(
    phase: str,
    sources: ValidatedSources,
) -> dict[str, Any]:
    if phase == "stop_legacy_writers":
        values = {
            "legacy_writer_process_count": sum(
                result["legacy_writer_process_count"]
                for result in sources.freeze_results.values()
            ),
            "legacy_writer_database_client_count": sum(
                result["writer_database_client_count"]
                for result in sources.freeze_results.values()
            ),
            "legacy_file_mutator_process_count": sum(
                result["file_mutator_process_count"]
                for result in sources.freeze_results.values()
            ),
        }
    elif phase == "zero_writer_surface_readback":
        external = sources.nginx_receipt["external_readback"]
        expected_vhosts = {
            vhost for vhost, _address in NGINX.VHOST_TARGETS
        }
        if (
            set(external["vhosts"]) != expected_vhosts
            or any(
                probes != {
                    "get": probes["get"],
                    "post": 503,
                    "websocket": 503,
                }
                for probes in external["vhosts"].values()
            )
        ):
            raise FreezeSnapshotPhaseBridgeError(
                "external three-vhost write-block readback differs"
            )
        values = {
            "write_capable_route_count": sum(
                int(
                    probes["post"] != 503
                    or probes["websocket"] != 503
                )
                for probes in external["vhosts"].values()
            ),
            "legacy_writer_process_count": sum(
                result["legacy_writer_process_count"]
                for result in sources.freeze_results.values()
            ),
            "writer_database_client_count": sum(
                result["writer_database_client_count"]
                for result in sources.freeze_results.values()
            ),
            "file_mutator_process_count": sum(
                result["file_mutator_process_count"]
                for result in sources.freeze_results.values()
            ),
            "externally_read_vhost_count": len(external["vhosts"]),
        }
    elif phase == "final_snapshot_hashes":
        postgres_hash, file_hash, redis_hash = _derive_final_hashes(sources)
        values = {
            "postgres_snapshot_set_sha256": postgres_hash,
            "reviewed_file_snapshot_set_sha256": file_hash,
            "legacy_redis_sealed_set_sha256": redis_hash,
            "legacy_redis_restore_member_count": sum(
                int(snapshot["redis_rollback_only"]["restore"])
                for snapshot in sources.snapshots.values()
            ),
            "frozen_writer_delta_count": sum(
                int(
                    sources.freeze_results[role][
                        "freeze_evidence_sha256"
                    ]
                    != sources.frozen_result["roles"][role][
                        "freeze_evidence_sha256"
                    ]
                )
                for role in ROLE_ORDER
            ),
            "file_mutator_process_count": sum(
                result["file_mutator_process_count"]
                for result in sources.freeze_results.values()
            ),
            "file_snapshot_pre_post_stat_stable": all(
                row["pre_tree_sha256"] == row["post_tree_sha256"]
                for snapshot in sources.snapshots.values()
                for row in snapshot["file_snapshots"].values()
            ),
            "file_snapshot_tree_hash_stable": all(
                row["pre_tree_sha256"]
                == row["archive_tree_sha256"]
                == row["post_tree_sha256"]
                for snapshot in sources.snapshots.values()
                for row in snapshot["file_snapshots"].values()
            ),
        }
    else:
        raise FreezeSnapshotPhaseBridgeError(
            "bridge phase is not allowlisted"
        )
    rules = VERIFY.PHASE_CLAIM_RULES[phase]
    if set(values) != set(rules):
        raise FreezeSnapshotPhaseBridgeError(
            f"{phase} derived claim set differs"
        )
    for name, rule in rules.items():
        try:
            VERIFY._validate_claim(  # noqa: SLF001
                name,
                {"value": values[name], "source_sha256": "1" * 64},
                rule,
            )
        except VERIFY.PhaseEvidenceError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                f"{phase} derived claim {name} is invalid"
            ) from exc
    return values


def _phase_role_sources(
    phase: str,
    sources: ValidatedSources,
) -> dict[str, SecureRecord]:
    if phase in {"stop_legacy_writers", "zero_writer_surface_readback"}:
        result = {
            role: sources.records[
                "current_frozen_verification_receipt"
            ]
            for role in ROLE_ORDER
        }
        if phase == "zero_writer_surface_readback":
            result["witness"] = sources.records["nginx_readback_receipt"]
        return result
    if phase == "final_snapshot_hashes":
        return {
            role: sources.records[f"{role}_snapshot_manifest"]
            for role in ROLE_ORDER
        }
    raise FreezeSnapshotPhaseBridgeError("bridge phase is not allowlisted")


def _load_prior_records(
    *,
    phase: str,
    evidence_paths: Mapping[str, Path],
    state: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    expected = CONTROLLER.PHASES[: CONTROLLER.PHASES.index(phase)]
    if set(evidence_paths) != set(expected):
        raise FreezeSnapshotPhaseBridgeError(
            f"{phase} prior evidence path set differs"
        )
    result: dict[str, dict[str, Any]] = {}
    for prior_phase in expected:
        try:
            document, digest = VERIFY.read_root_only_evidence(
                evidence_paths[prior_phase]
            )
        except VERIFY.PhaseEvidenceError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                f"{prior_phase} prior evidence is unsafe"
            ) from exc
        if (
            digest != state["phase_evidence_sha256"][prior_phase]
            or document.get("phase") != prior_phase
            or document.get("status") != "passed"
            or document.get("business_write_observed") is not False
        ):
            raise FreezeSnapshotPhaseBridgeError(
                f"{prior_phase} prior evidence differs from journal"
            )
        result[prior_phase] = {
            "document": document,
            "file_sha256": digest,
        }
    return result


def _prepare_phase_evidence(
    context: BridgeContext,
    *,
    phase: str,
    state: Mapping[str, Any],
    evidence_paths: Mapping[str, Path],
    sources: ValidatedSources,
    now: datetime,
) -> tuple[Path, dict[str, Path], dict[str, Path], dict[str, Any]]:
    spec = next(item for item in CONTROLLER.PHASE_SPECS if item.phase == phase)
    observed_at = now.astimezone(timezone.utc).isoformat()
    role_sources = _phase_role_sources(phase, sources)
    role_paths: dict[str, Path] = {}
    role_request_sha256: dict[str, str] = {}
    role_source_sha256: dict[str, str] = {}
    for role in spec.roles:
        source = role_sources[role]
        request_sha256 = _aggregate_hash(
            {
                "phase": phase,
                "operation": spec.operation,
                "role": role,
                "source_path": os.fspath(source.path),
                "source_sha256": source.sha256,
                "source_closure_sha256": sources.source_closure_sha256,
            }
        )
        role_validation = {
            "schema": ROLE_VALIDATION_SCHEMA,
            "status": "validated-request",
            "request_sha256": request_sha256,
            "operation": spec.operation,
            "role": role,
            "campaign_id": context.manifest["campaign_id"],
            "operation_id": context.manifest["operation_id"],
            "app_release_sha": context.manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "approval_sha256": context.manifest["artifacts"][
                "cutover_approval_sha256"
            ],
            "expected_host": context.manifest["topology"][role]["host"],
            "observed_host": context.manifest["topology"][role]["host"],
            "required_journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
            "business_write_policy": "forbid",
            "agent_artifact_sha256": context.manifest["artifacts"][
                "host_agent_sha256"
            ],
            "host_agent_contract_sha256": context.manifest["artifacts"][
                "host_agent_contract_sha256"
            ],
            "transport": context.manifest["topology"][role]["transport"],
            "observed_at": observed_at,
            "host_identity_observed": True,
            "execution_supported": False,
            "production_contacted": False,
        }
        if set(role_validation) != VERIFY.HOST_AGENT_VALIDATION_FIELDS:
            raise FreezeSnapshotPhaseBridgeError(
                "internal role validation fields differ"
            )
        path, digest = _persist_document(
            context.output_root / phase / "role-validation",
            prefix=f"role-validation-{role}",
            document=role_validation,
        )
        role_paths[role] = path
        role_request_sha256[role] = request_sha256
        role_source_sha256[role] = digest

    claims = _phase_claims(phase, sources)
    claim_paths: dict[str, Path] = {}
    claim_source_sha256: dict[str, str] = {}
    for claim, value in claims.items():
        source = {
            "schema": CLAIM_SOURCE_SCHEMA,
            "campaign_id": context.manifest["campaign_id"],
            "operation_id": context.manifest["operation_id"],
            "release_sha": context.manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "phase": phase,
            "operation": spec.operation,
            "claim": claim,
            "value": value,
            "observed_at": observed_at,
            "status": "observed",
        }
        if set(source) != VERIFY.CLAIM_SOURCE_FIELDS:
            raise FreezeSnapshotPhaseBridgeError(
                "internal claim source fields differ"
            )
        path, digest = _persist_document(
            context.output_root / phase / "claim-sources",
            prefix=f"claim-{claim}",
            document=source,
        )
        claim_paths[claim] = path
        claim_source_sha256[claim] = digest

    prior_records = _load_prior_records(
        phase=phase,
        evidence_paths=evidence_paths,
        state=state,
    )
    prior_rows = [
        {
            "phase": prior_phase,
            "evidence_sha256": state["phase_evidence_sha256"][
                prior_phase
            ],
        }
        for prior_phase in CONTROLLER.PHASES[
            : CONTROLLER.PHASES.index(phase)
        ]
    ]
    try:
        prior_claims = VERIFY._derive_prior_claim_rows(  # noqa: SLF001
            phase=phase,
            prior_digests={
                row["phase"]: row["evidence_sha256"]
                for row in prior_rows
            },
            prior_records=prior_records,
            campaign_id=context.manifest["campaign_id"],
            operation_id=context.manifest["operation_id"],
            release_sha=context.manifest["release_sha"],
            legacy_release_sha=context.manifest["legacy_release_sha"],
            manifest_sha256=context.manifest_sha256,
            plan_sha256=context.plan_sha256,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise FreezeSnapshotPhaseBridgeError(
            f"{phase} prior claim bindings are invalid"
        ) from exc
    dynamic = {
        name: value
        for name, value in claims.items()
        if VERIFY.PHASE_CLAIM_RULES[phase][name].kind != "exact"
    }
    phase_input = {
        "manifest_sha256": context.manifest_sha256,
        "manifest_artifacts_sha256": _aggregate_hash(
            context.manifest["artifacts"]
        ),
        "prior_phase_evidence": prior_rows,
        "prior_claim_bindings": prior_claims,
        "dynamic_claim_values": dynamic,
        "claim_source_sha256": {
            name: claim_source_sha256[name]
            for name in sorted(claim_source_sha256)
        },
        "role_request_sha256": {
            role: role_request_sha256[role] for role in spec.roles
        },
        "role_source_artifact_sha256": {
            role: role_source_sha256[role] for role in spec.roles
        },
        "role_observed_at": {
            role: observed_at for role in spec.roles
        },
    }
    evidence = {
        "schema": VERIFY.EVIDENCE_SCHEMA,
        "phase_evidence_schema_sha256": context.manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ],
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "approval_sha256": context.manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "manifest_artifact_bindings": context.manifest["artifacts"],
        "phase": phase,
        "operation": spec.operation,
        "journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "status": "passed",
        "captured_at": observed_at,
        "business_write_observed": False,
        "prior_phase_evidence": prior_rows,
        "prior_phase_evidence_closure_sha256": _aggregate_hash(prior_rows),
        "prior_claim_bindings": prior_claims,
        "phase_input_closure_sha256": _aggregate_hash(phase_input),
        "role_attestations": [
            {
                "role": role,
                "expected_host": context.manifest["topology"][role][
                    "host"
                ],
                "operation": spec.operation,
                "request_sha256": role_request_sha256[role],
                "app_release_sha": context.manifest["release_sha"],
                "agent_artifact_sha256": context.manifest["artifacts"][
                    "host_agent_sha256"
                ],
                "host_identity_observed": True,
                "observed_at": observed_at,
                "status": "verified",
                "transport": context.manifest["topology"][role][
                    "transport"
                ],
                "source_artifact_sha256": role_source_sha256[role],
            }
            for role in spec.roles
        ],
        "claims": {
            name: {
                "value": value,
                "source_sha256": claim_source_sha256[name],
            }
            for name, value in claims.items()
        },
    }
    if set(evidence) != VERIFY.EVIDENCE_FIELDS:
        raise FreezeSnapshotPhaseBridgeError(
            "internal phase evidence fields differ"
        )
    evidence_path, evidence_sha256 = _persist_document(
        context.output_root / phase / "evidence",
        prefix=phase,
        document=evidence,
    )
    aggregate = {
        "schema": PHASE_AGGREGATE_SCHEMA,
        "status": "completed",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "phase": phase,
        "operation": spec.operation,
        "roles": list(spec.roles),
        "source_closure_sha256": sources.source_closure_sha256,
        "claims": claims,
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "caller_truth_values_accepted": False,
        "legacy_writers_frozen": True,
        "restore_performed": False,
        "writer_restart_performed": False,
        "business_write_observed": False,
    }
    _persist_document(
        context.output_root / phase / "aggregates",
        prefix=f"phase-aggregate-{phase}",
        document=aggregate,
    )
    return evidence_path, role_paths, claim_paths, aggregate


def _locate_completed_evidence(
    context: BridgeContext,
    *,
    phase: str,
    digest: str,
) -> Path:
    path = (
        context.output_root
        / phase
        / "evidence"
        / f"{phase}.{digest}.json"
    )
    try:
        document, observed = VERIFY.read_root_only_evidence(path)
    except VERIFY.PhaseEvidenceError as exc:
        raise FreezeSnapshotPhaseBridgeError(
            f"completed {phase} evidence is unavailable"
        ) from exc
    if (
        observed != digest
        or document.get("phase") != phase
        or document.get("status") != "passed"
    ):
        raise FreezeSnapshotPhaseBridgeError(
            f"completed {phase} evidence differs"
        )
    return path


class ControllerEOFGuard:
    """Turn controller pipe EOF or any payload into one SIGUSR1 cancellation."""

    def __init__(self, descriptor: int):
        if type(descriptor) is not int or descriptor < 0:
            raise FreezeSnapshotPhaseBridgeError(
                "controller liveness descriptor is invalid"
            )
        try:
            metadata = os.fstat(descriptor)
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            target = os.readlink(f"/proc/self/fd/{descriptor}")
        except OSError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                "controller liveness pipe is unavailable"
            ) from exc
        if (
            not stat.S_ISFIFO(metadata.st_mode)
            or flags & os.O_ACCMODE != os.O_RDONLY
            or target != f"pipe:[{metadata.st_ino}]"
        ):
            raise FreezeSnapshotPhaseBridgeError(
                "controller liveness must be an anonymous read-only pipe"
            )
        try:
            entries = tuple(Path("/proc/self/fd").iterdir())
        except OSError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                "controller liveness descriptor table is unavailable"
            ) from exc
        if len(entries) > MAX_CONTROL_FDS:
            raise FreezeSnapshotPhaseBridgeError(
                "controller liveness descriptor table is oversized"
            )
        for entry in entries:
            try:
                candidate = int(entry.name)
            except ValueError:
                continue
            if candidate == descriptor:
                continue
            try:
                candidate_metadata = os.fstat(candidate)
                candidate_flags = fcntl.fcntl(
                    candidate,
                    fcntl.F_GETFL,
                )
                candidate_target = os.readlink(
                    f"/proc/self/fd/{candidate}"
                )
            except OSError as exc:
                if exc.errno in {errno.EBADF, errno.ENOENT}:
                    continue
                raise FreezeSnapshotPhaseBridgeError(
                    "controller liveness descriptor scan failed"
                ) from exc
            if (
                stat.S_ISFIFO(candidate_metadata.st_mode)
                and candidate_metadata.st_dev == metadata.st_dev
                and candidate_metadata.st_ino == metadata.st_ino
                and candidate_target == target
                and candidate_flags & os.O_ACCMODE != os.O_RDONLY
            ):
                raise FreezeSnapshotPhaseBridgeError(
                    "controller process retains a writer for its liveness pipe"
                )
        try:
            duplicate = os.dup(descriptor)
        except OSError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                "controller liveness pipe cannot be duplicated"
            ) from exc
        self.descriptor = duplicate
        self.failed = threading.Event()
        self.stopping = threading.Event()
        self.signalled = threading.Event()
        self.thread = threading.Thread(
            target=self._watch,
            name="freeze-snapshot-controller-eof",
            daemon=True,
        )
        self.start_attempted = False

    def _signal_once(self) -> None:
        self.failed.set()
        if not self.signalled.is_set():
            self.signalled.set()
            os.kill(os.getpid(), signal.SIGUSR1)

    def _watch(self) -> None:
        try:
            os.set_blocking(self.descriptor, False)
            while not self.stopping.is_set():
                readable, _, _ = select.select(
                    [self.descriptor],
                    [],
                    [],
                    EOF_POLL_SECONDS,
                )
                if not readable:
                    continue
                try:
                    chunk = os.read(self.descriptor, 1)
                except BlockingIOError:
                    continue
                if not self.stopping.is_set():
                    # A liveness descriptor is EOF-only; bytes are authority
                    # protocol confusion and fail closed identically.
                    del chunk
                    self._signal_once()
                return
        except BaseException:
            if not self.stopping.is_set():
                self._signal_once()

    def __enter__(self) -> "ControllerEOFGuard":
        try:
            os.set_blocking(self.descriptor, False)
            readable, _, _ = select.select(
                [self.descriptor],
                [],
                [],
                0,
            )
            if readable:
                try:
                    os.read(self.descriptor, 1)
                except BlockingIOError:
                    pass
                else:
                    self.failed.set()
                    raise FreezeSnapshotPhaseBridgeCancellation(
                        "controller liveness was already lost"
                    )
            self.start_attempted = True
            self.thread.start()
            self.check()
            return self
        except BaseException as exc:
            self._cleanup(exc)
            raise

    def check(self) -> None:
        if self.failed.is_set():
            raise FreezeSnapshotPhaseBridgeCancellation(
                "controller liveness reached EOF"
            )

    def _cleanup(
        self,
        error: BaseException | None,
    ) -> None:
        self.stopping.set()
        cleanup_errors: list[BaseException] = []
        try:
            os.close(self.descriptor)
        except BaseException as exc:
            cleanup_errors.append(exc)
        if self.start_attempted:
            try:
                self.thread.join(timeout=1.0)
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                if self.thread.is_alive():
                    cleanup_errors.append(
                        FreezeSnapshotPhaseBridgeError(
                            "controller liveness watcher did not stop"
                        )
                    )
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            summary = "; ".join(
                f"{type(item).__name__}:{item}"
                for item in cleanup_errors
            )
            if error is not None:
                try:
                    error.add_note(
                        "controller liveness cleanup failed: " + summary
                    )
                except (AttributeError, TypeError):
                    pass
                return
            raise FreezeSnapshotPhaseBridgeError(
                "controller liveness cleanup failed: " + summary
            ) from cleanup_errors[0]

    def __exit__(
        self,
        _error_type: Any,
        error: BaseException | None,
        _traceback: Any,
    ) -> bool:
        self._cleanup(error)
        return False


class OneShotSignalGuard:
    """Install one-shot cancellation with explicit reconciliation deferral."""

    SIGNALS = (
        signal.SIGHUP,
        signal.SIGINT,
        signal.SIGTERM,
        signal.SIGUSR1,
    )

    def __init__(self) -> None:
        self.previous: dict[int, Any] = {}
        self.seen = False
        self.pending_signum: int | None = None
        self.defer_depth = 0

    def _cancel(self, signum: int, _frame: Any) -> None:
        if self.seen:
            return
        self.seen = True
        if self.defer_depth:
            self.pending_signum = signum
            return
        raise FreezeSnapshotPhaseBridgeCancellation(
            f"phase bridge received {signal.Signals(signum).name}"
        )

    def __enter__(self) -> "OneShotSignalGuard":
        if threading.current_thread() is not threading.main_thread():
            raise FreezeSnapshotPhaseBridgeError(
                "phase bridge must run in the main thread"
            )
        self.previous = {
            signum: signal.getsignal(signum) for signum in self.SIGNALS
        }
        installed: list[int] = []
        try:
            for signum in self.SIGNALS:
                signal.signal(signum, self._cancel)
                installed.append(signum)
        except BaseException as primary:
            cleanup_errors: list[BaseException] = []
            for signum in reversed(installed):
                try:
                    signal.signal(signum, self.previous[signum])
                except BaseException as exc:
                    cleanup_errors.append(exc)
            if cleanup_errors:
                summary = "; ".join(
                    f"{type(item).__name__}:{item}"
                    for item in cleanup_errors
                )
                try:
                    primary.add_note(
                        "partial signal installation cleanup failed: "
                        + summary
                    )
                except (AttributeError, TypeError):
                    pass
            raise
        return self

    def __exit__(
        self,
        _error_type: Any,
        error: BaseException | None,
        _traceback: Any,
    ) -> bool:
        cleanup_errors: list[BaseException] = []
        for signum, handler in reversed(tuple(self.previous.items())):
            try:
                signal.signal(signum, handler)
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            summary = "; ".join(
                f"{type(item).__name__}:{item}"
                for item in cleanup_errors
            )
            if error is not None:
                try:
                    error.add_note(
                        "signal handler cleanup failed: " + summary
                    )
                except (AttributeError, TypeError):
                    pass
                return False
            raise FreezeSnapshotPhaseBridgeError(
                "signal handler cleanup failed: " + summary
            ) from cleanup_errors[0]
        return False

    @contextmanager
    def reconciliation_scope(self) -> Iterator[None]:
        self.defer_depth += 1
        try:
            yield
        finally:
            self.defer_depth -= 1

    def raise_pending(self) -> None:
        if self.defer_depth:
            raise FreezeSnapshotPhaseBridgeError(
                "pending cancellation checked inside reconciliation"
            )
        if self.pending_signum is not None:
            raise FreezeSnapshotPhaseBridgeCancellation(
                "phase bridge deferred "
                f"{signal.Signals(self.pending_signum).name} "
                "until reconciliation completed"
            )


@contextmanager
def _one_shot_signal_guard() -> Iterator[OneShotSignalGuard]:
    with OneShotSignalGuard() as guard:
        yield guard


def confirmation_phrase(context: BridgeContext) -> str:
    return (
        "BRIDGE-FROZEN-SNAPSHOT-PUBLIC-PHASES:"
        f"{context.manifest['operation_id']}:"
        f"{context.manifest['release_sha']}:"
        f"{context.request.sha256}"
    )


def _plan(context: BridgeContext) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "status": "planned",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "request_sha256": context.request.sha256,
        "phases": list(PHASES),
        "required_confirmation": confirmation_phrase(context),
        "standalone_worker_outputs_only": True,
        "caller_truth_values_accepted": False,
        "restore_supported": False,
        "writer_restart_supported": False,
        "postcommit_supported": False,
        "production_contacted": False,
        "journal_mutated": False,
    }


def execute(
    request_path: Path,
    *,
    apply: bool = False,
    confirm: str | None = None,
    control_fd: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if apply and now is not None:
        raise FreezeSnapshotPhaseBridgeError(
            "apply does not accept a caller-supplied observation time"
        )
    context = _load_request(request_path)
    plan = _plan(context)
    if not apply:
        if confirm is not None or control_fd is not None:
            raise FreezeSnapshotPhaseBridgeError(
                "plan mode does not accept confirmation or liveness"
            )
        return plan
    if confirm != confirmation_phrase(context):
        raise FreezeSnapshotPhaseBridgeError(
            "phase bridge confirmation differs"
        )
    if control_fd is None:
        raise FreezeSnapshotPhaseBridgeError(
            "apply requires controller EOF liveness"
        )

    journal = CONTROLLER.ProductionCutoverJournal(
        Path(context.manifest["deployment"]["controller_journal_path"])
    )
    evidence_paths: dict[str, Path] = dict(context.prior_paths)
    phase_aggregates: dict[str, Any] = {}

    with (
        _one_shot_signal_guard() as cancellations,
        ControllerEOFGuard(control_fd) as authority,
    ):
        authority.check()
        _verify_authorization(context)
        try:
            initial = journal.assert_bindings(**_journal_bindings(context))
        except CONTROLLER.CutoverContractError as exc:
            raise FreezeSnapshotPhaseBridgeError(
                "cutover journal binding differs"
            ) from exc
        initial = _validate_journal_corridor(initial, context=context)
        for phase in PHASES:
            if phase not in initial["completed_phases"]:
                break
            evidence_paths[phase] = _locate_completed_evidence(
                context,
                phase=phase,
                digest=initial["phase_evidence_sha256"][phase],
            )
            phase_aggregates[phase] = {
                "status": "reused-completed",
                "phase_evidence_sha256": initial[
                    "phase_evidence_sha256"
                ][phase],
            }
        all_complete = all(
            phase in initial["completed_phases"] for phase in PHASES
        )
        sources = _validate_sources(context, now=_fresh_observation())
        _validate_frozen_public_phase_start(
            initial,
            context=context,
            frozen_result=sources.frozen_result,
        )
        with cancellations.reconciliation_scope():
            _persist_document(
                context.output_root / "source-closure",
                prefix="source-closure",
                document={
                    "schema": SOURCE_CLOSURE_SCHEMA,
                    "campaign_id": context.manifest["campaign_id"],
                    "operation_id": context.manifest["operation_id"],
                    "release_sha": context.manifest["release_sha"],
                    "manifest_sha256": context.manifest_sha256,
                    "plan_sha256": context.plan_sha256,
                    "source_closure_sha256": (
                        sources.source_closure_sha256
                    ),
                    "source_count": len(sources.records),
                    "caller_truth_values_accepted": False,
                    "restore_performed": False,
                    "writer_restart_performed": False,
                },
            )
        cancellations.raise_pending()
        for phase in PHASES:
            authority.check()
            _verify_authorization(context)
            try:
                state = journal.assert_bindings(
                    **_journal_bindings(context)
                )
            except CONTROLLER.CutoverContractError as exc:
                raise FreezeSnapshotPhaseBridgeError(
                    f"{phase} journal binding differs"
                ) from exc
            state = _validate_journal_corridor(state, context=context)
            if phase in state["completed_phases"]:
                evidence_paths[phase] = _locate_completed_evidence(
                    context,
                    phase=phase,
                    digest=state["phase_evidence_sha256"][phase],
                )
                continue
            sources = _revalidate_source_closure(
                context,
                expected=sources,
                checkpoint="before a public phase start",
            )
            _assert_records_unchanged(sources.records)
            authority.check()
            _verify_authorization(context)
            if phase == PHASES[0]:
                state = _validate_frozen_public_phase_start(
                    state,
                    context=context,
                    frozen_result=sources.frozen_result,
                )
            elif state["status"] == "phase_started":
                if state["started_phase"] != phase:
                    raise FreezeSnapshotPhaseBridgeError(
                        f"{phase} has a different durable public start"
                    )
            else:
                try:
                    with cancellations.reconciliation_scope():
                        journal.begin_phase(phase)
                        state = journal.assert_bindings(
                            **_journal_bindings(context)
                        )
                        state = _validate_journal_corridor(
                            state,
                            context=context,
                        )
                        if (
                            state["status"] != "phase_started"
                            or state["started_phase"] != phase
                        ):
                            raise FreezeSnapshotPhaseBridgeError(
                                f"{phase} durable start readback differs"
                            )
                except CONTROLLER.CutoverContractError as exc:
                    raise FreezeSnapshotPhaseBridgeError(
                        f"{phase} cannot be durably started"
                    ) from exc
            cancellations.raise_pending()
            authority.check()
            _verify_authorization(context)
            sources = _revalidate_source_closure(
                context,
                expected=sources,
                checkpoint="after a durable public phase start",
            )
            _assert_records_unchanged(sources.records)
            cancellations.raise_pending()
            authority.check()
            _verify_authorization(context)
            sources = _revalidate_source_closure(
                context,
                expected=sources,
                checkpoint="immediately before phase evidence generation",
            )
            _assert_records_unchanged(sources.records)
            with cancellations.reconciliation_scope():
                (
                    evidence_path,
                    role_paths,
                    claim_paths,
                    aggregate,
                ) = _prepare_phase_evidence(
                    context,
                    phase=phase,
                    state=state,
                    evidence_paths=evidence_paths,
                    sources=sources,
                    now=_fresh_observation(),
                )
            cancellations.raise_pending()
            _assert_records_unchanged(sources.records)
            authority.check()
            _verify_authorization(context)
            try:
                verification, receipt = (
                    CONTROLLER._run_release_phase_verifier(  # noqa: SLF001
                        phase=phase,
                        manifest=context.manifest,
                        manifest_sha256=context.manifest_sha256,
                        plan=context.plan,
                        manifest_path=context.manifest_path,
                        approval_path=context.approval_path,
                        approval_policy_path=context.approval_policy_path,
                        evidence_path=evidence_path,
                        role_validation=[
                            f"{role}={role_paths[role]}"
                            for role in next(
                                spec.roles
                                for spec in CONTROLLER.PHASE_SPECS
                                if spec.phase == phase
                            )
                        ],
                        claim_source=[
                            f"{claim}={claim_paths[claim]}"
                            for claim in VERIFY.PHASE_CLAIM_RULES[phase]
                        ],
                        prior_phase_evidence=[
                            f"{prior}={evidence_paths[prior]}"
                            for prior in CONTROLLER.PHASES[
                                : CONTROLLER.PHASES.index(phase)
                            ]
                        ],
                    )
                )
                cancellations.raise_pending()
                authority.check()
                _verify_authorization(context)
                sources = _revalidate_source_closure(
                    context,
                    expected=sources,
                    checkpoint="immediately before phase receipt persistence",
                )
                _assert_records_unchanged(sources.records)
                cancellations.raise_pending()
                authority.check()
                _verify_authorization(context)
                with cancellations.reconciliation_scope():
                    CONTROLLER._persist_phase_verification_receipt(  # noqa: SLF001
                        token=verification,
                        receipt=receipt,
                        evidence_root=Path(
                            context.manifest["deployment"][
                                "controller_evidence_root"
                            ]
                        ),
                    )
                cancellations.raise_pending()
                authority.check()
                _verify_authorization(context)
                sources = _revalidate_source_closure(
                    context,
                    expected=sources,
                    checkpoint="immediately before public phase completion",
                )
                _assert_records_unchanged(sources.records)
                cancellations.raise_pending()
                authority.check()
                _verify_authorization(context)
                with cancellations.reconciliation_scope():
                    journal.complete_phase(
                        phase,
                        verification=verification,
                    )
                    completed = journal.assert_bindings(
                        **_journal_bindings(context)
                    )
                    completed = _validate_journal_corridor(
                        completed,
                        context=context,
                    )
            except CONTROLLER.CutoverContractError as exc:
                raise FreezeSnapshotPhaseBridgeError(
                    f"{phase} release-bound verification failed"
                ) from exc
            if (
                completed["phase_evidence_sha256"][phase]
                != verification.evidence_sha256
                or completed["phase_verification_sha256"][phase]
                != verification.receipt_sha256
            ):
                raise FreezeSnapshotPhaseBridgeError(
                    f"{phase} public journal completion differs"
                )
            evidence_paths[phase] = evidence_path
            phase_aggregates[phase] = aggregate
            cancellations.raise_pending()
            authority.check()

        final_state = journal.assert_bindings(**_journal_bindings(context))
        final_state = _validate_journal_corridor(
            final_state,
            context=context,
        )
        expected = list(
            CONTROLLER.PHASES[
                : FIRST_PHASE_INDEX + len(PHASES)
            ]
        )
        if (
            final_state["completed_phases"] != expected
            or final_state["status"] != "active"
            or final_state["started_phase"] is not None
            or final_state["first_business_write_allowed"] is not False
        ):
            raise FreezeSnapshotPhaseBridgeError(
                "three-phase public journal closure differs"
            )
        _assert_records_unchanged(sources.records)
        result = {
            **plan,
            "status": "completed",
            "phase_evidence_sha256": {
                phase: final_state["phase_evidence_sha256"][phase]
                for phase in PHASES
            },
            "phase_verification_sha256": {
                phase: final_state["phase_verification_sha256"][phase]
                for phase in PHASES
            },
            "phase_aggregates": phase_aggregates,
            "source_closure_sha256": sources.source_closure_sha256,
            "journal_state_sha256": final_state["state_sha256"],
            "journal_event_tail_sha256": final_state[
                "event_tail_sha256"
            ],
            "next_phase": CONTROLLER.PHASES[
                FIRST_PHASE_INDEX + len(PHASES)
            ],
            "legacy_writers_frozen": True,
            "restore_supported": False,
            "writer_restart_supported": False,
            "postcommit_supported": False,
            "production_contacted": False,
            "journal_mutated": not all_complete,
        }
        result.pop("required_confirmation")
        with cancellations.reconciliation_scope():
            path, digest = _persist_document(
                context.output_root / "aggregates",
                prefix="freeze-snapshot-phase-bridge",
                document=result,
            )
        cancellations.raise_pending()
        return {
            **result,
            "aggregate_path": os.fspath(path),
            "aggregate_sha256": digest,
        }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--control-fd", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = execute(
            args.request,
            apply=args.apply,
            confirm=args.confirm,
            control_fd=args.control_fd,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except FreezeSnapshotPhaseBridgeError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "legacy_writers_may_be_frozen": bool(args.apply),
                    "journal_mutated": None if args.apply else False,
                    "reconciliation_required": (
                        None if args.apply else False
                    ),
                    "restore_performed": False,
                    "writer_restart_performed": False,
                    "postcommit_performed": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
