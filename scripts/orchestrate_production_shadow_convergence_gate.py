#!/usr/bin/env python3
"""Fail-closed public bridge for the production-shadow convergence gate.

The bridge deliberately does not collect production observations.  A real
production observer is not available in this repository, so this module only
accepts a strictly shaped, root-only, immutable observation source-set that a
future observer must publish after the controller has durably started this
phase.  This prevents the controller from turning staging evidence, caller
booleans, or a plan-time snapshot into a production convergence claim.

The bridge has four bounded local steps:

* inspect a source-set and create one digest-addressed source record;
* create one digest-addressed phase request from that source record;
* materialize verifier-compatible claim records and phase evidence; and
* run the release-bound verifier and complete an already-started journal.

No function here opens SSH, Docker, Object Storage, a database, or a network
connection.  Missing, stale, non-canonical, or unimplemented producer input is
reported as unavailable and cannot advance the journal.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Iterator, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from core.writer_witness_contract import (  # noqa: E402
    WitnessProofError,
    validate_witness_lease_proof,
    witness_public_key_is_valid,
)
from scripts.production_shadow_prepared_clone_errors import (  # noqa: E402
    PreparedCloneInventoryError,
)
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402


PHASE = "convergence_gate"
OPERATION = "verify-shadow-three-site-convergence"
ROLES = ("bot_fi", "webapp_fi", "webapp_ir", "witness")
CLAIMS = tuple(VERIFY.PHASE_CLAIM_RULES[PHASE])
PRIOR_PHASES = CONTROLLER.PHASES[: CONTROLLER.PHASES.index(PHASE)]

PLAN_SCHEMA = "production-shadow-convergence-gate-plan-v1"
SOURCE_SET_SCHEMA = "production-shadow-convergence-observation-source-set-v1"
SOURCE_RECORD_SCHEMA = "production-shadow-convergence-gate-source-record-v1"
REQUEST_SCHEMA = "production-shadow-convergence-gate-phase-request-v1"
PUBLICATION_SCHEMA = "production-shadow-convergence-gate-publication-v1"
RESULT_SCHEMA = "production-shadow-convergence-gate-result-v1"

_DEFAULT_APPLY_DEPENDENCY = object()

DATABASE_OBSERVATION_SCHEMA = "production-shadow-convergence-database-observation-v1"
DR_OBSERVATION_SCHEMA = "production-shadow-convergence-dr-observation-v1"
BLOB_OBSERVATION_SCHEMA = "production-shadow-convergence-blob-observation-v1"
QUEUE_OBSERVATION_SCHEMA = "production-shadow-convergence-queue-observation-v1"
TLS_OBSERVATION_SCHEMA = "production-shadow-convergence-dr-tls-observation-v1"
FIREWALL_OBSERVATION_SCHEMA = "production-shadow-convergence-firewall-observation-v1"
WITNESS_OBSERVATION_SCHEMA = "production-shadow-convergence-witness-live-observation-v1"

SOURCE_LABELS = (
    "database_parity",
    "dr_convergence",
    "blob_roundtrip",
    "queue_state",
    "dr_tls",
    "destination_firewall",
    "witness_live",
)

REFERENCE_FIELDS = frozenset({"path", "sha256"})
SOURCE_SET_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "phase_started_at",
        "role_validation",
        "observations",
        "source_set_closure_sha256",
    }
)
SOURCE_RECORD_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "operation",
        "phase_started_at",
        "captured_at",
        "source_set",
        "prior_phase_evidence",
        "role_validation",
        "observations",
        "source_binding_sha256",
        "caller_claim_values_accepted",
        "production_contacted_by_bridge",
    }
)
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
        "approval_path",
        "approval_sha256",
        "approval_policy_path",
        "approval_policy_sha256",
        "prior_phase_evidence",
        "source_record",
        "source_binding_sha256",
        "output_root",
        "constraints",
    }
)

EXPECTED_CONSTRAINTS = {
    "production_observer_required": True,
    "staging_evidence_insufficient": True,
    "source_set_root_only_immutable_required": True,
    "source_observations_after_journal_start_required": True,
    "source_freshness_from_signed_or_observed_time_required": True,
    "caller_claim_values_forbidden": True,
    "create_only_publication_required": True,
    "runtime_authorization_required_for_apply": True,
    "controller_liveness_required_for_apply": True,
    "prestarted_journal_required_for_apply": True,
    "production_contact_forbidden_by_bridge": True,
}

MAX_JSON_BYTES = 16 * 1024 * 1024
OUTPUT_DIR_MODE = 0o700
OUTPUT_FILE_MODE = 0o600
MAX_SOURCE_AGE = timedelta(minutes=15)
MAX_SOURCE_SKEW = timedelta(minutes=2)
MAX_FUTURE_SKEW = timedelta(seconds=5)
MIN_LIVE_LEASE_REMAINING = timedelta(seconds=90)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64

DATABASE_PAIRS = frozenset(
    {
        ("bot-authority", "bot_fi", "webapp_fi"),
        ("bot-authority", "bot_fi", "webapp_ir"),
        ("webapp-authority", "webapp_fi", "bot_fi"),
        ("webapp-authority", "webapp_fi", "webapp_ir"),
    }
)
DR_PAIRS = frozenset(
    (origin, destination)
    for origin in ("bot_fi", "webapp_fi", "webapp_ir")
    for destination in ("bot_fi", "webapp_fi", "webapp_ir")
    if origin != destination
)
BLOB_PAIRS = frozenset(
    {
        ("webapp-authority", "webapp_fi", "webapp_ir"),
        ("webapp-authority", "webapp_ir", "webapp_fi"),
    }
)
DR_TLS_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
TLS_PAIRS = frozenset(
    (origin, destination)
    for origin in DR_TLS_ROLES
    for destination in DR_TLS_ROLES
    if origin != destination
)


class ConvergenceGateError(RuntimeError):
    """The convergence gate cannot be proven from the exact source closure."""


class ConvergenceSourceUnavailable(ConvergenceGateError):
    """The required independent production observer has not published input."""


class _StrictObjectError(ValueError):
    pass


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    nlink: int
    size: int


@dataclass(frozen=True)
class SecureRecord:
    path: Path
    sha256: str
    payload: bytes
    document: dict[str, Any]
    identity: FileIdentity


@dataclass(frozen=True)
class Reference:
    path: Path
    sha256: str


@dataclass(frozen=True)
class EvidenceContext:
    manifest_path: Path
    approval_path: Path
    approval_policy_path: Path
    journal_path: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    plan: dict[str, Any]
    plan_sha256: str
    journal: dict[str, Any]
    prior_paths: Mapping[str, Path]
    prior_digests: Mapping[str, str]
    prior_records: Mapping[str, Mapping[str, Any]]
    evidence_root: Path


@dataclass(frozen=True)
class SourceSet:
    reference: Reference
    record: SecureRecord
    role_validation: Mapping[str, SecureRecord]
    observations: Mapping[str, SecureRecord]
    observed_at: Mapping[str, datetime]
    captured_at: datetime


@dataclass(frozen=True)
class ValidatedSourceMembers:
    role_validation: Mapping[str, SecureRecord]
    observations: Mapping[str, SecureRecord]
    observed_at: Mapping[str, datetime]
    captured_at: datetime


@dataclass(frozen=True)
class PreparedSourceRecord:
    context: EvidenceContext
    source_set: SourceSet
    document: dict[str, Any]
    payload: bytes
    sha256: str
    output: Path
    required_confirmation: str


@dataclass(frozen=True)
class PreparedRequest:
    context: EvidenceContext
    source_record: SecureRecord
    document: dict[str, Any]
    payload: bytes
    sha256: str
    output: Path
    required_confirmation: str


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
        raise ConvergenceGateError("value is not canonical JSON") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _StrictObjectError("duplicate JSON field")
        result[key] = value
    return result


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise ConvergenceGateError(f"{label} SHA-256 is invalid")
    return value


def _absolute_path(value: Path | str, *, label: str) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise ConvergenceGateError(f"{label} path is invalid") from exc
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(os.path.abspath(path))
    ):
        raise ConvergenceGateError(f"{label} path is not canonical absolute")
    return path


def _reference(value: Any, *, label: str) -> Reference:
    if not isinstance(value, Mapping) or set(value) != REFERENCE_FIELDS:
        raise ConvergenceGateError(f"{label} reference fields differ")
    return Reference(
        path=_absolute_path(value["path"], label=label),
        sha256=_nonzero_sha256(value["sha256"], label=label),
    )


def _reference_document(reference: Reference) -> dict[str, str]:
    return {"path": os.fspath(reference.path), "sha256": reference.sha256}


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ConvergenceGateError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConvergenceGateError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConvergenceGateError(f"{label} timestamp lacks a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _require_within(path: Path, root: Path, *, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ConvergenceGateError(f"{label} is outside its canonical root") from exc


def _private_directory(path: Path, *, label: str) -> None:
    path = _absolute_path(path, label=label)
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ConvergenceGateError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != OUTPUT_DIR_MODE
    ):
        raise ConvergenceGateError(f"{label} must be root:root mode 0700")


def _ensure_private_child(parent: Path, name: str, *, label: str) -> Path:
    _private_directory(parent, label=f"{label} parent")
    child = parent / name
    try:
        child.mkdir(mode=OUTPUT_DIR_MODE)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ConvergenceGateError(f"{label} cannot be created") from exc
    _private_directory(child, label=label)
    return child


def _required_source_directory(path: Path, *, label: str) -> None:
    """Classify an absent future-observer directory as unavailable, not safe."""

    try:
        _private_directory(path, label=label)
    except ConvergenceGateError as exc:
        try:
            missing = not path.exists()
        except OSError:
            missing = False
        if missing:
            raise ConvergenceSourceUnavailable(
                f"{label} has not been published by a production observer"
            ) from exc
        raise


def _read_secure_record(
    reference: Reference,
    *,
    label: str,
    maximum: int = MAX_JSON_BYTES,
) -> SecureRecord:
    path = _absolute_path(reference.path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConvergenceSourceUnavailable(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != OUTPUT_FILE_MODE
            or before.st_size < 1
            or before.st_size > maximum
        ):
            raise ConvergenceGateError(f"{label} is not a private immutable file")
        chunks: list[bytes] = []
        remaining = maximum
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, remaining + 1))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
            if remaining < 0:
                raise ConvergenceGateError(f"{label} exceeds its size limit")
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise ConvergenceGateError(f"{label} changed while being read")
    finally:
        os.close(descriptor)
    if _sha256(payload) != reference.sha256:
        raise ConvergenceGateError(f"{label} digest differs")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _StrictObjectError) as exc:
        raise ConvergenceGateError(f"{label} is not strict JSON") from exc
    if not isinstance(document, dict):
        raise ConvergenceGateError(f"{label} root is not an object")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ConvergenceGateError(f"{label} cannot be resolved") from exc
    if resolved != path:
        raise ConvergenceGateError(f"{label} traverses a symbolic link")
    return SecureRecord(
        path=path,
        sha256=reference.sha256,
        payload=payload,
        document=document,
        identity=FileIdentity(
            device=before.st_dev,
            inode=before.st_ino,
            mode=before.st_mode,
            uid=before.st_uid,
            gid=before.st_gid,
            nlink=before.st_nlink,
            size=before.st_size,
        ),
    )


def _assert_records_unchanged(records: Mapping[str, SecureRecord]) -> None:
    for label, expected in records.items():
        observed = _read_secure_record(
            Reference(expected.path, expected.sha256),
            label=f"stable {label}",
        )
        if (
            observed.identity != expected.identity
            or observed.payload != expected.payload
            or observed.document != expected.document
        ):
            raise ConvergenceGateError(f"{label} changed during validation")


def _phase_root(manifest: Mapping[str, Any]) -> Path:
    return _absolute_path(
        Path(manifest["deployment"]["controller_evidence_root"]) / "convergence-gate",
        label="convergence gate root",
    )


def _source_input_root(manifest: Mapping[str, Any]) -> Path:
    return _phase_root(manifest) / "observation-inputs"


def _source_set_root(manifest: Mapping[str, Any]) -> Path:
    return _source_input_root(manifest) / "source-sets"


def _role_validation_root(manifest: Mapping[str, Any]) -> Path:
    return _source_input_root(manifest) / "role-validations"


def _observation_root(manifest: Mapping[str, Any]) -> Path:
    return _source_input_root(manifest) / "observations"


def _source_record_root(manifest: Mapping[str, Any]) -> Path:
    return _phase_root(manifest) / "source-records"


def _request_root(manifest: Mapping[str, Any]) -> Path:
    return _phase_root(manifest) / "requests"


def _claim_root(manifest: Mapping[str, Any]) -> Path:
    return _phase_root(manifest) / "claim-sources"


def _evidence_root(manifest: Mapping[str, Any]) -> Path:
    return _phase_root(manifest) / "evidence"


def _candidate_root(manifest: Mapping[str, Any]) -> Path:
    return _phase_root(manifest) / "verification-candidates"


def _source_record_path(manifest: Mapping[str, Any], digest: str) -> Path:
    return _source_record_root(manifest) / f"convergence-source.{digest}.json"


def _request_path(manifest: Mapping[str, Any], digest: str) -> Path:
    return _request_root(manifest) / f"convergence-request.{digest}.json"


def _evidence_path(manifest: Mapping[str, Any], digest: str) -> Path:
    return _evidence_root(manifest) / f"{PHASE}.{digest}.json"


def _candidate_path(manifest: Mapping[str, Any], source_digest: str, evidence_digest: str) -> Path:
    return _candidate_root(manifest) / f"{source_digest}.{evidence_digest}.json"


def _claim_path(manifest: Mapping[str, Any], claim: str, digest: str) -> Path:
    return _claim_root(manifest) / f"{claim}.{digest}.json"


def _journal_bindings(context: EvidenceContext) -> dict[str, str]:
    return {
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
    }


def _position(journal: Mapping[str, Any]) -> str:
    prefix = list(PRIOR_PHASES)
    if (
        journal.get("completed_phases") == prefix
        and journal.get("status") == "active"
        and journal.get("started_phase") is None
    ):
        return "ready"
    if (
        journal.get("completed_phases") == prefix
        and journal.get("status") == "phase_started"
        and journal.get("started_phase") == PHASE
        and isinstance(journal.get("started_at"), str)
    ):
        return "started"
    if (
        journal.get("completed_phases") == [*prefix, PHASE]
        and journal.get("status") == "active"
        and journal.get("started_phase") is None
    ):
        return "completed"
    return "invalid"


def _document_sha256(document: Mapping[str, Any]) -> str:
    return _sha256(_canonical_json(dict(document)) + b"\n")


def _validate_context(
    context: EvidenceContext,
    *,
    required_position: str = "any",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        not isinstance(context, EvidenceContext)
        or os.geteuid() != 0
        or os.getegid() != 0
    ):
        raise ConvergenceGateError("convergence context requires root:root")
    try:
        manifest = CONTROLLER.validate_manifest(json.loads(_canonical_json(context.manifest)))
        journal = CONTROLLER._validate_journal(json.loads(_canonical_json(context.journal)))  # noqa: SLF001
    except (CONTROLLER.CutoverContractError, TypeError, ValueError) as exc:
        raise ConvergenceGateError("convergence context is invalid") from exc
    if (
        context.plan.get("plan_sha256") != context.plan_sha256
        or _nonzero_sha256(context.manifest_sha256, label="manifest") != context.manifest_sha256
        or _nonzero_sha256(context.plan_sha256, label="controller plan") != context.plan_sha256
        or context.journal_path != Path(manifest["deployment"]["controller_journal_path"])
    ):
        raise ConvergenceGateError("convergence context binding differs")
    if any(journal.get(key) != value for key, value in _journal_bindings(context).items()):
        raise ConvergenceGateError("convergence journal binding differs")
    position = _position(journal)
    if position == "invalid" or (required_position != "any" and position != required_position):
        raise ConvergenceGateError("convergence phase is not the exact journal successor")
    if (
        set(context.prior_paths) != set(PRIOR_PHASES)
        or set(context.prior_digests) != set(PRIOR_PHASES)
        or set(context.prior_records) != set(PRIOR_PHASES)
    ):
        raise ConvergenceGateError("prior phase evidence closure is not exact")
    expected_prior = {
        phase: journal["phase_evidence_sha256"].get(phase)
        for phase in PRIOR_PHASES
    }
    if dict(context.prior_digests) != expected_prior:
        raise ConvergenceGateError("prior evidence differs from the journal")
    for phase in PRIOR_PHASES:
        document = context.prior_records[phase]
        expected = {
            "schema": VERIFY.EVIDENCE_SCHEMA,
            "phase": phase,
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "legacy_release_sha": manifest["legacy_release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "plan_sha256": context.plan_sha256,
            "approval_sha256": manifest["artifacts"]["cutover_approval_sha256"],
            "status": "passed",
            "business_write_observed": False,
        }
        if (
            not isinstance(document, Mapping)
            or set(document) != VERIFY.EVIDENCE_FIELDS
            or _document_sha256(document) != context.prior_digests[phase]
            or any(document.get(key) != value for key, value in expected.items())
        ):
            raise ConvergenceGateError(f"prior phase {phase} evidence differs")
    root = _absolute_path(context.evidence_root, label="controller evidence root")
    if root != Path(manifest["deployment"]["controller_evidence_root"]):
        raise ConvergenceGateError("controller evidence root differs from manifest")
    _private_directory(root, label="controller evidence root")
    return manifest, journal


def load_evidence_context(
    *,
    manifest_path: Path,
    approval_path: Path,
    approval_policy_path: Path,
    prior_evidence_paths: Mapping[str, Path],
) -> EvidenceContext:
    """Load only the controller-local root-owned context and exact prefix."""

    if os.geteuid() != 0 or os.getegid() != 0:
        raise ConvergenceGateError("convergence context loading requires root:root")
    manifest_path = _absolute_path(manifest_path, label="cutover manifest")
    approval_path = _absolute_path(approval_path, label="cutover approval")
    approval_policy_path = _absolute_path(approval_policy_path, label="approval policy")
    try:
        manifest, manifest_sha256 = CONTROLLER.read_root_only_manifest(manifest_path)
        plan = CONTROLLER.render_plan(manifest, manifest_sha256=manifest_sha256, manifest_path=manifest_path)
        approval = read_secure_bytes(approval_path, label="production cutover approval", owner_uid=0, max_size=MAX_JSON_BYTES)
        policy = read_secure_bytes(approval_policy_path, label="production approval policy", owner_uid=0, max_size=4 * 1024 * 1024)
    except (CONTROLLER.CutoverContractError, SecureFileError) as exc:
        raise ConvergenceGateError("trusted controller context is unavailable") from exc
    if (
        _sha256(approval) != manifest["artifacts"]["cutover_approval_sha256"]
        or _sha256(policy) != manifest["artifacts"]["human_approval_policy_sha256"]
    ):
        raise ConvergenceGateError("approval artifacts differ from the manifest")
    if not isinstance(prior_evidence_paths, Mapping) or set(prior_evidence_paths) != set(PRIOR_PHASES):
        raise ConvergenceGateError("prior evidence path mapping is not exact")
    evidence_root = _absolute_path(manifest["deployment"]["controller_evidence_root"], label="controller evidence root")
    _private_directory(evidence_root, label="controller evidence root")
    records: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    paths: dict[str, Path] = {}
    for phase in PRIOR_PHASES:
        path = _absolute_path(prior_evidence_paths[phase], label=f"prior phase {phase}")
        _require_within(path, evidence_root, label=f"prior phase {phase}")
        try:
            document, digest = VERIFY.read_root_only_evidence(path)
        except VERIFY.PhaseEvidenceError as exc:
            raise ConvergenceGateError(f"prior phase {phase} evidence is unavailable") from exc
        records[phase] = document
        digests[phase] = digest
        paths[phase] = path
    journal_path = Path(manifest["deployment"]["controller_journal_path"])
    try:
        journal = CONTROLLER.ProductionCutoverJournal(journal_path).load()
    except CONTROLLER.CutoverContractError as exc:
        raise ConvergenceGateError("cutover journal is unavailable") from exc
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
        prior_paths=paths,
        prior_digests=digests,
        prior_records=records,
        evidence_root=evidence_root,
    )
    _validate_context(context)
    return context


def _context_from_request(document: Mapping[str, Any]) -> EvidenceContext:
    if set(document) != REQUEST_FIELDS or document.get("schema") != REQUEST_SCHEMA or document.get("status") != "ready":
        raise ConvergenceGateError("convergence request fields differ")
    if document.get("constraints") != EXPECTED_CONSTRAINTS:
        raise ConvergenceGateError("convergence request constraints differ")
    prior = document.get("prior_phase_evidence")
    if not isinstance(prior, Mapping) or set(prior) != set(PRIOR_PHASES):
        raise ConvergenceGateError("convergence request prior prefix differs")
    paths = {phase: _reference(prior[phase], label=f"request prior {phase}").path for phase in PRIOR_PHASES}
    context = load_evidence_context(
        manifest_path=_absolute_path(document["manifest_path"], label="request manifest"),
        approval_path=_absolute_path(document["approval_path"], label="request approval"),
        approval_policy_path=_absolute_path(document["approval_policy_path"], label="request approval policy"),
        prior_evidence_paths=paths,
    )
    expected = {
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "approval_sha256": context.manifest["artifacts"]["cutover_approval_sha256"],
        "approval_policy_sha256": context.manifest["artifacts"]["human_approval_policy_sha256"],
        "output_root": os.fspath(_phase_root(context.manifest)),
    }
    if any(document.get(key) != value for key, value in expected.items()):
        raise ConvergenceGateError("convergence request differs from controller context")
    for phase in PRIOR_PHASES:
        reference = _reference(prior[phase], label=f"request prior {phase}")
        if (
            reference.path != context.prior_paths[phase]
            or reference.sha256 != context.prior_digests[phase]
        ):
            raise ConvergenceGateError(f"request prior {phase} differs from readback")
    return context


def _source_identity_fields(context: EvidenceContext) -> dict[str, Any]:
    return {
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "approval_sha256": context.manifest["artifacts"]["cutover_approval_sha256"],
    }


def _validate_source_identity(
    document: Mapping[str, Any],
    *,
    context: EvidenceContext,
    schema: str,
    label: str,
) -> datetime:
    expected = _source_identity_fields(context)
    if (
        document.get("schema") != schema
        or document.get("status") != "observed"
        or any(document.get(key) != value for key, value in expected.items())
    ):
        raise ConvergenceGateError(f"{label} observation identity differs")
    return _timestamp(document.get("observed_at"), label=f"{label} observation")


def _hash_field(value: Any, *, label: str, zero_allowed: bool = False) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ConvergenceGateError(f"{label} hash is invalid")
    if not zero_allowed and value == ZERO_SHA256:
        raise ConvergenceGateError(f"{label} hash is zero")
    return value


def _nonnegative(value: Any, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ConvergenceGateError(f"{label} must be a non-negative integer")
    return value


def _positive(value: Any, *, label: str) -> int:
    value = _nonnegative(value, label=label)
    if value < 1:
        raise ConvergenceGateError(f"{label} must be positive")
    return value


def _validate_database_observation(
    document: Mapping[str, Any],
    *,
    context: EvidenceContext,
) -> datetime:
    fields = {
        "schema", "status", *(_source_identity_fields(context)), "observed_at",
        "comparisons", "mismatch_count", "database_state_sha256",
    }
    if set(document) != fields:
        raise ConvergenceGateError("database convergence observation fields differ")
    observed_at = _validate_source_identity(
        document,
        context=context,
        schema=DATABASE_OBSERVATION_SCHEMA,
        label="database convergence",
    )
    if document["mismatch_count"] != 0 or not isinstance(document["comparisons"], list):
        raise ConvergenceGateError("database convergence did not report exact parity")
    expected_fields = {
        "scope", "source_site", "target_site", "table_set_sha256",
        "source_business_fingerprint_sha256", "target_business_fingerprint_sha256",
        "source_row_count", "target_row_count", "table_count",
        "business_drift_count", "critical_drift_count", "incomplete_count",
        "local_only_difference_count", "volatile_difference_count",
    }
    seen: set[tuple[str, str, str]] = set()
    for row in document["comparisons"]:
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise ConvergenceGateError("database convergence comparison fields differ")
        key = (str(row["scope"]), str(row["source_site"]), str(row["target_site"]))
        if key not in DATABASE_PAIRS or key in seen:
            raise ConvergenceGateError("database convergence pair differs")
        seen.add(key)
        for field in (
            "table_set_sha256",
            "source_business_fingerprint_sha256",
            "target_business_fingerprint_sha256",
        ):
            _hash_field(row[field], label=f"database {field}")
        if (
            _nonnegative(row["source_row_count"], label="database source rows")
            != _nonnegative(row["target_row_count"], label="database target rows")
            or _positive(row["table_count"], label="database table count") < 1
            or row["source_business_fingerprint_sha256"]
            != row["target_business_fingerprint_sha256"]
            or any(
                _nonnegative(row[field], label=f"database {field}") != 0
                for field in (
                    "business_drift_count",
                    "critical_drift_count",
                    "incomplete_count",
                )
            )
        ):
            raise ConvergenceGateError("database convergence contains a harmful drift")
        _nonnegative(row["local_only_difference_count"], label="database local difference")
        _nonnegative(row["volatile_difference_count"], label="database volatile difference")
    if seen != DATABASE_PAIRS:
        raise ConvergenceGateError("database convergence does not cover all authority pairs")
    _hash_field(document["database_state_sha256"], label="database convergence state")
    return observed_at


def _validate_dr_observation(
    document: Mapping[str, Any],
    *,
    context: EvidenceContext,
) -> datetime:
    fields = {
        "schema", "status", *(_source_identity_fields(context)), "observed_at",
        "streams", "conflict_count", "dr_state_sha256",
    }
    if set(document) != fields:
        raise ConvergenceGateError("DR convergence observation fields differ")
    observed_at = _validate_source_identity(
        document,
        context=context,
        schema=DR_OBSERVATION_SCHEMA,
        label="DR convergence",
    )
    if document["conflict_count"] != 0 or not isinstance(document["streams"], list):
        raise ConvergenceGateError("DR convergence conflict state differs")
    row_fields = {
        "origin_site", "destination_site", "producer_epoch", "source_sequence",
        "received_sequence", "applied_sequence", "source_transaction_hash",
        "received_transaction_hash", "applied_transaction_hash",
    }
    seen: set[tuple[str, str]] = set()
    for row in document["streams"]:
        if not isinstance(row, Mapping) or set(row) != row_fields:
            raise ConvergenceGateError("DR convergence stream fields differ")
        key = (str(row["origin_site"]), str(row["destination_site"]))
        if key not in DR_PAIRS or key in seen or _positive(row["producer_epoch"], label="DR producer epoch") < 1:
            raise ConvergenceGateError("DR convergence stream identity differs")
        seen.add(key)
        sequences = [
            _nonnegative(row[field], label=f"DR {field}")
            for field in ("source_sequence", "received_sequence", "applied_sequence")
        ]
        hashes = [
            _hash_field(row[field], label=f"DR {field}", zero_allowed=True)
            for field in (
                "source_transaction_hash",
                "received_transaction_hash",
                "applied_transaction_hash",
            )
        ]
        if (
            len(set(sequences)) != 1
            or len(set(hashes)) != 1
            or (sequences[0] == 0) != (hashes[0] == ZERO_SHA256)
        ):
            raise ConvergenceGateError("DR convergence stream is not exactly applied")
    if seen != DR_PAIRS:
        raise ConvergenceGateError("DR convergence does not cover every directed stream")
    _hash_field(document["dr_state_sha256"], label="DR convergence state")
    return observed_at


def _validate_blob_observation(
    document: Mapping[str, Any],
    *,
    context: EvidenceContext,
) -> datetime:
    fields = {
        "schema", "status", *(_source_identity_fields(context)), "observed_at",
        "object_storage_versioning", "missing_object_count", "corrupt_object_count",
        "scopes", "blob_state_sha256",
    }
    if set(document) != fields:
        raise ConvergenceGateError("blob roundtrip observation fields differ")
    observed_at = _validate_source_identity(
        document,
        context=context,
        schema=BLOB_OBSERVATION_SCHEMA,
        label="blob roundtrip",
    )
    if (
        document["object_storage_versioning"] is not True
        or document["missing_object_count"] != 0
        or document["corrupt_object_count"] != 0
        or not isinstance(document["scopes"], list)
    ):
        raise ConvergenceGateError("blob roundtrip status differs")
    row_fields = {
        "scope", "source_site", "target_site", "source_set_sha256",
        "target_set_sha256", "source_object_count", "target_object_count",
        "readback_sample_count", "source_keyring_sha256", "target_keyring_sha256",
    }
    seen: set[tuple[str, str, str]] = set()
    for row in document["scopes"]:
        if not isinstance(row, Mapping) or set(row) != row_fields:
            raise ConvergenceGateError("blob roundtrip scope fields differ")
        key = (str(row["scope"]), str(row["source_site"]), str(row["target_site"]))
        if key not in BLOB_PAIRS or key in seen:
            raise ConvergenceGateError("blob roundtrip scope identity differs")
        seen.add(key)
        for field in (
            "source_set_sha256",
            "target_set_sha256",
            "source_keyring_sha256",
            "target_keyring_sha256",
        ):
            _hash_field(row[field], label=f"blob {field}")
        source_count = _nonnegative(row["source_object_count"], label="blob source count")
        target_count = _nonnegative(row["target_object_count"], label="blob target count")
        samples = _nonnegative(row["readback_sample_count"], label="blob readback samples")
        if (
            row["source_set_sha256"] != row["target_set_sha256"]
            or row["source_keyring_sha256"] != row["target_keyring_sha256"]
            or source_count != target_count
            or samples > source_count
            or (source_count > 0 and samples < 1)
        ):
            raise ConvergenceGateError("blob roundtrip keyring or readback differs")
    if seen != BLOB_PAIRS:
        raise ConvergenceGateError("blob roundtrip does not cover both WebApps")
    _hash_field(document["blob_state_sha256"], label="blob roundtrip state")
    return observed_at


def _validate_queue_observation(
    document: Mapping[str, Any],
    *,
    context: EvidenceContext,
) -> datetime:
    fields = {
        "schema", "status", *(_source_identity_fields(context)), "observed_at",
        "running_business_mutator_count", "due_otp_job_count", "inflight_effect_count",
        "telegram_lease_count", "provider_attempt_delta_count", "queue_state_sha256",
    }
    if set(document) != fields:
        raise ConvergenceGateError("queue observation fields differ")
    observed_at = _validate_source_identity(
        document,
        context=context,
        schema=QUEUE_OBSERVATION_SCHEMA,
        label="queue state",
    )
    if any(
        _nonnegative(document[field], label=f"queue {field}") != 0
        for field in (
            "running_business_mutator_count",
            "due_otp_job_count",
            "inflight_effect_count",
            "telegram_lease_count",
            "provider_attempt_delta_count",
        )
    ):
        raise ConvergenceGateError("queue observation reports live or due work")
    _hash_field(document["queue_state_sha256"], label="queue state")
    return observed_at


def _validate_tls_observation(
    document: Mapping[str, Any],
    *,
    context: EvidenceContext,
) -> datetime:
    fields = {
        "schema", "status", *(_source_identity_fields(context)), "observed_at",
        "peers", "peer_set_sha256",
    }
    if set(document) != fields:
        raise ConvergenceGateError("DR TLS observation fields differ")
    observed_at = _validate_source_identity(
        document,
        context=context,
        schema=TLS_OBSERVATION_SCHEMA,
        label="DR TLS",
    )
    if not isinstance(document["peers"], list):
        raise ConvergenceGateError("DR TLS peers are not a list")
    row_fields = {
        "origin_role", "destination_role", "protocol", "status_code",
        "certificate_sha256", "peer_handshake_sha256", "ca_bundle_sha256",
    }
    seen: set[tuple[str, str]] = set()
    for row in document["peers"]:
        if not isinstance(row, Mapping) or set(row) != row_fields:
            raise ConvergenceGateError("DR TLS peer fields differ")
        key = (str(row["origin_role"]), str(row["destination_role"]))
        if key not in TLS_PAIRS or key in seen:
            raise ConvergenceGateError("DR TLS peer identity differs")
        seen.add(key)
        if row["protocol"] not in {"TLSv1.2", "TLSv1.3"} or row["status_code"] != 200:
            raise ConvergenceGateError("DR TLS peer handshake is not healthy")
        for field in ("certificate_sha256", "peer_handshake_sha256", "ca_bundle_sha256"):
            _hash_field(row[field], label=f"DR TLS {field}")
    if seen != TLS_PAIRS:
        raise ConvergenceGateError("DR TLS observation does not cover every peer direction")
    expected_set_hash = _sha256(_canonical_json(document["peers"]))
    if document["peer_set_sha256"] != expected_set_hash:
        raise ConvergenceGateError("DR TLS peer-set digest differs")
    return observed_at


def _validate_firewall_observation(
    document: Mapping[str, Any],
    *,
    context: EvidenceContext,
) -> datetime:
    fields = {
        "schema", "status", *(_source_identity_fields(context)), "observed_at",
        "roles", "allowlist_set_sha256",
    }
    if set(document) != fields:
        raise ConvergenceGateError("destination firewall observation fields differ")
    observed_at = _validate_source_identity(
        document,
        context=context,
        schema=FIREWALL_OBSERVATION_SCHEMA,
        label="destination firewall",
    )
    if not isinstance(document["roles"], Mapping) or set(document["roles"]) != set(ROLES):
        raise ConvergenceGateError("destination firewall role set differs")
    row_fields = {
        "expected_allowlist_sha256", "observed_allowlist_sha256",
        "operation_rule_count", "unexpected_destination_count",
        "missing_destination_count", "forbidden_egress_count", "readback_sha256",
    }
    canonical_rows: dict[str, Any] = {}
    for role in ROLES:
        row = document["roles"][role]
        if not isinstance(row, Mapping) or set(row) != row_fields:
            raise ConvergenceGateError(f"destination firewall {role} fields differ")
        for field in ("expected_allowlist_sha256", "observed_allowlist_sha256", "readback_sha256"):
            _hash_field(row[field], label=f"destination firewall {role} {field}")
        if (
            row["expected_allowlist_sha256"] != row["observed_allowlist_sha256"]
            or _positive(row["operation_rule_count"], label=f"destination firewall {role} rule count") < 1
            or any(
                _nonnegative(row[field], label=f"destination firewall {role} {field}") != 0
                for field in (
                    "unexpected_destination_count",
                    "missing_destination_count",
                    "forbidden_egress_count",
                )
            )
        ):
            raise ConvergenceGateError(f"destination firewall {role} readback differs")
        canonical_rows[role] = dict(row)
    if document["allowlist_set_sha256"] != _sha256(_canonical_json(canonical_rows)):
        raise ConvergenceGateError("destination firewall set digest differs")
    return observed_at


def _proof_sha256(proof: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(proof), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_witness_observation(
    document: Mapping[str, Any],
    *,
    context: EvidenceContext,
    now: datetime,
    require_fresh: bool,
) -> datetime:
    fields = {
        "schema", "status", *(_source_identity_fields(context)), "observed_at",
        "witness_public_key", "witness_public_key_sha256", "signed_proof",
        "signed_proof_sha256", "witness_status_receipt_sha256",
        "lease_live_readback_sha256",
    }
    if set(document) != fields:
        raise ConvergenceGateError("Witness live observation fields differ")
    observed_at = _validate_source_identity(
        document,
        context=context,
        schema=WITNESS_OBSERVATION_SCHEMA,
        label="Witness live",
    )
    public_key = document["witness_public_key"]
    if (
        not isinstance(public_key, str)
        or not witness_public_key_is_valid(public_key)
        or document["witness_public_key_sha256"] != _sha256(public_key.encode("ascii"))
        or not isinstance(document["signed_proof"], Mapping)
        or _proof_sha256(document["signed_proof"]) != document["signed_proof_sha256"]
    ):
        raise ConvergenceGateError("Witness live key or proof binding differs")
    try:
        proof = validate_witness_lease_proof(
            dict(document["signed_proof"]),
            public_key_base64=public_key,
            expected_site="webapp_fi",
            expected_epoch=1,
            now=now,
            safety_margin_seconds=0,
            max_clock_skew_seconds=30,
            max_lifetime_seconds=3600,
        ).canonical_payload
    except WitnessProofError as exc:
        raise ConvergenceGateError("Witness live proof signature or lifetime differs") from exc
    if proof != dict(document["signed_proof"]):
        raise ConvergenceGateError("Witness live proof canonical form differs")
    expires_at = _timestamp(proof["expires_at"], label="Witness proof expiry")
    if require_fresh and expires_at - now < MIN_LIVE_LEASE_REMAINING:
        raise ConvergenceGateError("Witness live lease lacks the minimum remaining lifetime")
    _hash_field(document["witness_status_receipt_sha256"], label="Witness status receipt")
    readback = {
        "proof_sha256": document["signed_proof_sha256"],
        "status_receipt_sha256": document["witness_status_receipt_sha256"],
        "observed_at": document["observed_at"],
    }
    if document["lease_live_readback_sha256"] != _sha256(_canonical_json(readback)):
        raise ConvergenceGateError("Witness live readback digest differs")
    return observed_at


def _source_set_closure(
    *,
    phase_started_at: str,
    role_validation: Mapping[str, Reference],
    observations: Mapping[str, Reference],
) -> str:
    rows = {
        "phase_started_at": phase_started_at,
        "role_validation": {
            role: _reference_document(role_validation[role])
            for role in ROLES
        },
        "observations": {
            label: _reference_document(observations[label])
            for label in SOURCE_LABELS
        },
    }
    return _sha256(_canonical_json(rows))


def _canonical_source_set_path(manifest: Mapping[str, Any], digest: str) -> Path:
    return _source_set_root(manifest) / f"source-set.{digest}.json"


def _canonical_role_validation_path(
    manifest: Mapping[str, Any],
    *,
    role: str,
    digest: str,
) -> Path:
    return _role_validation_root(manifest) / f"{role}.{digest}.json"


def _canonical_observation_path(
    manifest: Mapping[str, Any],
    *,
    label: str,
    digest: str,
) -> Path:
    return _observation_root(manifest) / f"{label}.{digest}.json"


def _validate_source_times(
    observed: Mapping[str, datetime],
    *,
    phase_started_at: datetime,
    now: datetime,
    require_fresh: bool,
) -> datetime:
    if not observed:
        raise ConvergenceGateError("convergence source observations are empty")
    values = list(observed.values())
    if any(value < phase_started_at for value in values):
        raise ConvergenceGateError("convergence source predates the durable phase start")
    if require_fresh:
        if any(value > now + MAX_FUTURE_SKEW for value in values):
            raise ConvergenceGateError("convergence source is implausibly future dated")
        if any(now - value > MAX_SOURCE_AGE for value in values):
            raise ConvergenceSourceUnavailable("fresh production convergence observations are unavailable")
        if max(values) - min(values) > MAX_SOURCE_SKEW:
            raise ConvergenceGateError("convergence source capture skew is too large")
    return max(values)


def _validate_source_member_layout(
    context: EvidenceContext,
    *,
    require_fresh: bool,
) -> None:
    _validate_context(context, required_position="started" if require_fresh else "any")
    _required_source_directory(_phase_root(context.manifest), label="convergence gate root")
    _required_source_directory(_source_input_root(context.manifest), label="convergence observation-input root")
    _required_source_directory(_source_set_root(context.manifest), label="convergence source-set root")
    _required_source_directory(_role_validation_root(context.manifest), label="convergence role-validation root")
    _required_source_directory(_observation_root(context.manifest), label="convergence observation root")
    # Each referenced raw input is opened without following a leaf symlink.
    # Require its fixed parent layout to be private and controller-owned too.
    incoming_root = _source_input_root(context.manifest) / "incoming"
    _required_source_directory(incoming_root, label="convergence incoming root")
    for label in ("requests", "attestations", "transport-receipts"):
        _required_source_directory(
            incoming_root / label,
            label=f"convergence incoming {label} root",
        )


def _validate_source_members(
    context: EvidenceContext,
    *,
    role_validation: Mapping[str, Reference],
    observations: Mapping[str, Reference],
    phase_started_at: datetime,
    now: datetime,
    require_fresh: bool,
    _layout_checked: bool = False,
) -> ValidatedSourceMembers:
    """Validate the immutable role and observation closure shared by publisher and gate."""
    if not _layout_checked:
        _validate_source_member_layout(context, require_fresh=require_fresh)
    if set(role_validation) != set(ROLES) or set(observations) != set(SOURCE_LABELS):
        raise ConvergenceGateError("convergence source-set members differ")

    role_records: dict[str, SecureRecord] = {}
    observation_records: dict[str, SecureRecord] = {}
    all_records: dict[str, SecureRecord] = {}
    for role in ROLES:
        reference_role = role_validation[role]
        if reference_role.path != _canonical_role_validation_path(
            context.manifest,
            role=role,
            digest=reference_role.sha256,
        ):
            raise ConvergenceGateError(f"source-set role {role} path is not canonical")
        role_record = _read_secure_record(reference_role, label=f"source-set role {role}")
        role_records[role] = role_record
        all_records[f"role:{role}"] = role_record
    for label in SOURCE_LABELS:
        reference_observation = observations[label]
        if reference_observation.path != _canonical_observation_path(
            context.manifest,
            label=label,
            digest=reference_observation.sha256,
        ):
            raise ConvergenceGateError(f"source-set {label} path is not canonical")
        observation_record = _read_secure_record(
            reference_observation,
            label=f"source-set {label}",
        )
        observation_records[label] = observation_record
        all_records[f"observation:{label}"] = observation_record
    identities = {(item.identity.device, item.identity.inode) for item in all_records.values()}
    if len(identities) != len(all_records):
        raise ConvergenceGateError("convergence source artifacts share a file identity")
    # Use the release verifier's role-validation contract rather than a local
    # duplicate. These records remain input evidence; this bridge never
    # manufactures a host validation statement.
    try:
        role_requests, role_sources, role_times = VERIFY._read_role_validation_records(
            [f"{role}={role_validation[role].path}" for role in ROLES],
            phase=PHASE,
            manifest=context.manifest,
            manifest_sha256=context.manifest_sha256,
            now=now,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise ConvergenceGateError("production convergence role validations are invalid") from exc
    if (
        set(role_requests) != set(ROLES)
        or set(role_times) != set(ROLES)
        or role_sources
        != {role: role_validation[role].sha256 for role in ROLES}
    ):
        raise ConvergenceGateError("production convergence role validation closure differs")
    observation_times = {
        "database_parity": _validate_database_observation(
            observation_records["database_parity"].document,
            context=context,
        ),
        "dr_convergence": _validate_dr_observation(
            observation_records["dr_convergence"].document,
            context=context,
        ),
        "blob_roundtrip": _validate_blob_observation(
            observation_records["blob_roundtrip"].document,
            context=context,
        ),
        "queue_state": _validate_queue_observation(
            observation_records["queue_state"].document,
            context=context,
        ),
        "dr_tls": _validate_tls_observation(
            observation_records["dr_tls"].document,
            context=context,
        ),
        "destination_firewall": _validate_firewall_observation(
            observation_records["destination_firewall"].document,
            context=context,
        ),
        "witness_live": _validate_witness_observation(
            observation_records["witness_live"].document,
            context=context,
            now=now,
            require_fresh=require_fresh,
        ),
    }
    observed = {
        **{
            f"role:{role}": _timestamp(role_times[role], label=f"role {role} observation")
            for role in ROLES
        },
        **{f"observation:{label}": value for label, value in observation_times.items()},
    }
    captured_at = _validate_source_times(
        observed,
        phase_started_at=phase_started_at,
        now=now,
        require_fresh=require_fresh,
    )
    _assert_records_unchanged(all_records)
    return ValidatedSourceMembers(
        role_validation=role_records,
        observations=observation_records,
        observed_at=observed,
        captured_at=captured_at,
    )


def _validate_source_set(
    context: EvidenceContext,
    reference: Reference,
    *,
    now: datetime,
    require_fresh: bool,
    expected_phase_started_at: str | None = None,
) -> SourceSet:
    _validate_source_member_layout(context, require_fresh=require_fresh)
    expected_path = _canonical_source_set_path(context.manifest, reference.sha256)
    if reference.path != expected_path:
        raise ConvergenceGateError("convergence source-set path is not canonical")
    record = _read_secure_record(reference, label="production convergence source-set")
    document = record.document
    if set(document) != SOURCE_SET_FIELDS or document.get("schema") != SOURCE_SET_SCHEMA or document.get("status") != "ready":
        raise ConvergenceGateError("production convergence source-set fields differ")
    phase_start_text = (
        context.journal.get("started_at")
        if require_fresh
        else expected_phase_started_at
    )
    expected = {
        **_source_identity_fields(context),
        "phase": PHASE,
        "phase_started_at": phase_start_text,
    }
    if any(document.get(key) != value for key, value in expected.items()):
        raise ConvergenceGateError("production convergence source-set binding differs")
    phase_started_at = _timestamp(document["phase_started_at"], label="convergence phase start")
    role_value = document.get("role_validation")
    observation_value = document.get("observations")
    if (
        not isinstance(role_value, Mapping)
        or set(role_value) != set(ROLES)
        or not isinstance(observation_value, Mapping)
        or set(observation_value) != set(SOURCE_LABELS)
    ):
        raise ConvergenceGateError("production convergence source-set members differ")
    roles = {
        role: _reference(role_value[role], label=f"source-set role {role}")
        for role in ROLES
    }
    observations = {
        label: _reference(observation_value[label], label=f"source-set {label}")
        for label in SOURCE_LABELS
    }
    if document.get("source_set_closure_sha256") != _source_set_closure(
        phase_started_at=document["phase_started_at"],
        role_validation=roles,
        observations=observations,
    ):
        raise ConvergenceGateError("production convergence source-set closure differs")
    members = _validate_source_members(
        context,
        role_validation=roles,
        observations=observations,
        phase_started_at=phase_started_at,
        now=now,
        require_fresh=require_fresh,
        _layout_checked=True,
    )
    member_records = {
        **{f"role:{role}": members.role_validation[role] for role in ROLES},
        **{
            f"observation:{label}": members.observations[label]
            for label in SOURCE_LABELS
        },
    }
    identities = {(item.identity.device, item.identity.inode) for item in member_records.values()}
    if (record.identity.device, record.identity.inode) in identities:
        raise ConvergenceGateError("convergence source artifacts share a file identity")
    _assert_records_unchanged({"source-set": record, **member_records})
    return SourceSet(
        reference=reference,
        record=record,
        role_validation=members.role_validation,
        observations=members.observations,
        observed_at=members.observed_at,
        captured_at=members.captured_at,
    )


def _validate_schema_role_fence(context: EvidenceContext) -> tuple[str, str, str]:
    migrate = context.prior_records["shadow_migrate"]
    post_roles = context.prior_records["shadow_roles_post_migration"]
    fence = context.prior_records["shadow_fence"]
    try:
        schema = migrate["claims"]["schema_fingerprint_sha256"]["value"]
        post_schema = post_roles["claims"]["migrated_schema_fingerprint_sha256"]["value"]
        fence_schema = fence["claims"]["migrated_schema_fingerprint_sha256"]["value"]
        fence_configuration = fence["claims"]["fence_configuration_sha256"]["value"]
        checks = (
            migrate["claims"]["alembic_chain_state"]["value"] == "target",
            migrate["claims"]["off_chain_revision_count"]["value"] == 0,
            migrate["claims"]["invalid_unready_index_count"]["value"] == 0,
            post_roles["claims"]["least_privilege_role_set_verified"]["value"] is True,
            post_roles["claims"]["excessive_grant_count"]["value"] == 0,
            fence["claims"]["fenced_database_count"]["value"] >= 1,
            fence["claims"]["unfenced_writer_count"]["value"] == 0,
            fence["claims"]["database_event_fence_verified"]["value"] is True,
        )
    except (KeyError, TypeError) as exc:
        raise ConvergenceGateError("prior schema/role/fence evidence is incomplete") from exc
    if (
        not all(checks)
        or not isinstance(schema, str)
        or schema != post_schema
        or schema != fence_schema
    ):
        raise ConvergenceGateError("prior schema/role/fence evidence is not converged")
    _nonzero_sha256(schema, label="migrated schema fingerprint")
    _nonzero_sha256(fence_configuration, label="fence configuration")
    return schema, fence_configuration, _sha256(
        _canonical_json(
            {
                "shadow_migrate": context.prior_digests["shadow_migrate"],
                "shadow_roles_post_migration": context.prior_digests["shadow_roles_post_migration"],
                "shadow_fence": context.prior_digests["shadow_fence"],
            }
        )
    )


def _source_record_binding(document: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in document.items() if key != "source_binding_sha256"}
    return _sha256(_canonical_json(unsigned))


def _build_source_record_document(
    context: EvidenceContext,
    source_set: SourceSet,
) -> dict[str, Any]:
    _validate_context(context, required_position="started")
    schema, fence, _schema_source = _validate_schema_role_fence(context)
    # The assignments above intentionally force the prior verified claims to
    # be read before source record publication.  The values are not carried as
    # caller-provided claims; they are re-derived at every apply.
    del schema, fence
    document: dict[str, Any] = {
        "schema": SOURCE_RECORD_SCHEMA,
        "status": "ready",
        **_source_identity_fields(context),
        "phase": PHASE,
        "operation": OPERATION,
        "phase_started_at": context.journal["started_at"],
        "captured_at": _timestamp_text(source_set.captured_at),
        "source_set": _reference_document(source_set.reference),
        "prior_phase_evidence": {
            phase: _reference_document(Reference(context.prior_paths[phase], context.prior_digests[phase]))
            for phase in PRIOR_PHASES
        },
        "role_validation": {
            role: _reference_document(Reference(source_set.role_validation[role].path, source_set.role_validation[role].sha256))
            for role in ROLES
        },
        "observations": {
            label: _reference_document(Reference(source_set.observations[label].path, source_set.observations[label].sha256))
            for label in SOURCE_LABELS
        },
        "source_binding_sha256": ZERO_SHA256,
        "caller_claim_values_accepted": False,
        "production_contacted_by_bridge": False,
    }
    document["source_binding_sha256"] = _source_record_binding(document)
    if set(document) != SOURCE_RECORD_FIELDS:
        raise ConvergenceGateError("constructed convergence source record fields differ")
    return document


def prepare_source_record(
    context: EvidenceContext,
    *,
    source_set: Reference,
    now: datetime | None = None,
) -> PreparedSourceRecord:
    """Validate already-published production observations without collecting them."""

    _validate_context(context, required_position="started")
    observed_now = (datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc))
    validated = _validate_source_set(context, source_set, now=observed_now, require_fresh=True)
    document = _build_source_record_document(context, validated)
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    output = _source_record_path(context.manifest, digest)
    confirmation = (
        f"publish-{PHASE}-source-record:"
        f"{context.manifest['operation_id']}:{source_set.sha256}:{digest}"
    )
    return PreparedSourceRecord(
        context=context,
        source_set=validated,
        document=document,
        payload=payload,
        sha256=digest,
        output=output,
        required_confirmation=confirmation,
    )


def _write_new_or_same(path: Path, payload: bytes, *, label: str) -> str:
    try:
        write_secure_new_bytes(path, payload, label=label, mode=OUTPUT_FILE_MODE, max_size=MAX_JSON_BYTES)
        outcome = "created"
    except SecureFileError as exc:
        try:
            existing = read_secure_bytes(path, label=f"existing {label}", owner_uid=0, max_size=MAX_JSON_BYTES)
        except SecureFileError as read_exc:
            raise ConvergenceGateError(f"{label} could not be published safely") from read_exc
        if existing != payload:
            raise ConvergenceGateError(f"existing {label} differs and will not be replaced") from exc
        outcome = "reused"
    reference = Reference(path, _sha256(payload))
    observed = _read_secure_record(reference, label=f"published {label}")
    if observed.payload != payload:
        raise ConvergenceGateError(f"published {label} readback differs")
    return outcome


def publish_source_record(
    prepared: PreparedSourceRecord,
    *,
    confirm: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if confirm != prepared.required_confirmation:
        raise ConvergenceGateError("source record publication requires exact digest-bound confirmation")
    # Re-read every source at publication time.  The bridge does not trust a
    # plan object whose inputs may have changed while an operator reviewed it.
    refreshed = prepare_source_record(prepared.context, source_set=prepared.source_set.reference, now=now)
    if (
        refreshed.payload != prepared.payload
        or refreshed.sha256 != prepared.sha256
        or refreshed.output != prepared.output
    ):
        raise ConvergenceGateError("convergence source inputs changed before publication")
    root = _phase_root(prepared.context.manifest)
    _ensure_private_child(prepared.context.evidence_root, "convergence-gate", label="convergence gate root")
    _ensure_private_child(root, "source-records", label="convergence source-record root")
    publication = _write_new_or_same(prepared.output, prepared.payload, label="convergence source record")
    return {
        "schema": PUBLICATION_SCHEMA,
        "status": "published",
        "kind": "source-record",
        "phase": PHASE,
        "operation": OPERATION,
        "source_record_path": os.fspath(prepared.output),
        "source_record_sha256": prepared.sha256,
        "source_binding_sha256": prepared.document["source_binding_sha256"],
        "publication": publication,
        "output_mutated": publication == "created",
        "journal_mutated": False,
        "production_contacted": False,
    }


def _load_source_record(
    context: EvidenceContext,
    reference: Reference,
    *,
    now: datetime,
    require_fresh: bool,
) -> tuple[SecureRecord, SourceSet]:
    position = "started" if require_fresh else "any"
    _validate_context(context, required_position=position)
    _private_directory(_phase_root(context.manifest), label="convergence gate root")
    _private_directory(_source_record_root(context.manifest), label="convergence source-record root")
    if reference.path != _source_record_path(context.manifest, reference.sha256):
        raise ConvergenceGateError("convergence source record path is not canonical")
    record = _read_secure_record(reference, label="convergence source record")
    document = record.document
    if set(document) != SOURCE_RECORD_FIELDS or document.get("schema") != SOURCE_RECORD_SCHEMA or document.get("status") != "ready":
        raise ConvergenceGateError("convergence source record fields differ")
    expected = {
        **_source_identity_fields(context),
        "phase": PHASE,
        "operation": OPERATION,
        "caller_claim_values_accepted": False,
        "production_contacted_by_bridge": False,
    }
    if any(document.get(key) != value for key, value in expected.items()):
        raise ConvergenceGateError("convergence source record identity differs")
    if document.get("source_binding_sha256") != _source_record_binding(document):
        raise ConvergenceGateError("convergence source record binding differs")
    phase_started_at = _timestamp(document.get("phase_started_at"), label="source record phase start")
    captured_at = _timestamp(document.get("captured_at"), label="source record capture")
    if require_fresh and document["phase_started_at"] != context.journal["started_at"]:
        raise ConvergenceGateError("convergence source record predates the current journal start")
    source_set_reference = _reference(document.get("source_set"), label="convergence source-set")
    source_set = _validate_source_set(
        context,
        source_set_reference,
        now=now if require_fresh else captured_at,
        require_fresh=require_fresh,
        expected_phase_started_at=document["phase_started_at"],
    )
    if source_set.captured_at != captured_at:
        raise ConvergenceGateError("convergence source record capture time differs")
    prior = document.get("prior_phase_evidence")
    roles = document.get("role_validation")
    observations = document.get("observations")
    if (
        not isinstance(prior, Mapping)
        or set(prior) != set(PRIOR_PHASES)
        or not isinstance(roles, Mapping)
        or set(roles) != set(ROLES)
        or not isinstance(observations, Mapping)
        or set(observations) != set(SOURCE_LABELS)
    ):
        raise ConvergenceGateError("convergence source record member closure differs")
    for phase in PRIOR_PHASES:
        observed = _reference(prior[phase], label=f"source record prior {phase}")
        if (
            observed.path != context.prior_paths[phase]
            or observed.sha256 != context.prior_digests[phase]
        ):
            raise ConvergenceGateError(f"source record prior {phase} differs")
    for role in ROLES:
        observed = _reference(roles[role], label=f"source record role {role}")
        expected_record = source_set.role_validation[role]
        if observed.path != expected_record.path or observed.sha256 != expected_record.sha256:
            raise ConvergenceGateError(f"source record role {role} differs")
    for label in SOURCE_LABELS:
        observed = _reference(observations[label], label=f"source record {label}")
        expected_record = source_set.observations[label]
        if observed.path != expected_record.path or observed.sha256 != expected_record.sha256:
            raise ConvergenceGateError(f"source record observation {label} differs")
    _validate_schema_role_fence(context)
    _assert_records_unchanged({"source-record": record, "source-set": source_set.record})
    if require_fresh:
        _validate_source_times(
            source_set.observed_at,
            phase_started_at=phase_started_at,
            now=now,
            require_fresh=True,
        )
    return record, source_set


def _build_request_document(
    context: EvidenceContext,
    *,
    source_record: SecureRecord,
) -> dict[str, Any]:
    source_binding = source_record.document["source_binding_sha256"]
    document = {
        "schema": REQUEST_SCHEMA,
        "status": "ready",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_path": os.fspath(context.manifest_path),
        "manifest_sha256": context.manifest_sha256,
        "approval_path": os.fspath(context.approval_path),
        "approval_sha256": context.manifest["artifacts"]["cutover_approval_sha256"],
        "approval_policy_path": os.fspath(context.approval_policy_path),
        "approval_policy_sha256": context.manifest["artifacts"]["human_approval_policy_sha256"],
        "prior_phase_evidence": {
            phase: _reference_document(Reference(context.prior_paths[phase], context.prior_digests[phase]))
            for phase in PRIOR_PHASES
        },
        "source_record": _reference_document(Reference(source_record.path, source_record.sha256)),
        "source_binding_sha256": source_binding,
        "output_root": os.fspath(_phase_root(context.manifest)),
        "constraints": dict(EXPECTED_CONSTRAINTS),
    }
    if set(document) != REQUEST_FIELDS:
        raise ConvergenceGateError("constructed convergence request fields differ")
    return document


def prepare_request(
    context: EvidenceContext,
    *,
    source_record: Reference,
    now: datetime | None = None,
) -> PreparedRequest:
    _validate_context(context, required_position="started")
    observed_now = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    source, _source_set = _load_source_record(
        context,
        source_record,
        now=observed_now,
        require_fresh=True,
    )
    document = _build_request_document(context, source_record=source)
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    output = _request_path(context.manifest, digest)
    confirmation = (
        f"publish-{PHASE}-request:"
        f"{context.manifest['operation_id']}:{source.sha256}:{digest}"
    )
    return PreparedRequest(
        context=context,
        source_record=source,
        document=document,
        payload=payload,
        sha256=digest,
        output=output,
        required_confirmation=confirmation,
    )


def publish_request(
    prepared: PreparedRequest,
    *,
    confirm: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if confirm != prepared.required_confirmation:
        raise ConvergenceGateError("request publication requires exact digest-bound confirmation")
    refreshed = prepare_request(
        prepared.context,
        source_record=Reference(prepared.source_record.path, prepared.source_record.sha256),
        now=now,
    )
    if (
        refreshed.payload != prepared.payload
        or refreshed.sha256 != prepared.sha256
        or refreshed.output != prepared.output
    ):
        raise ConvergenceGateError("convergence request inputs changed before publication")
    root = _phase_root(prepared.context.manifest)
    _ensure_private_child(prepared.context.evidence_root, "convergence-gate", label="convergence gate root")
    _ensure_private_child(root, "requests", label="convergence request root")
    publication = _write_new_or_same(prepared.output, prepared.payload, label="convergence phase request")
    return {
        "schema": PUBLICATION_SCHEMA,
        "status": "published",
        "kind": "request",
        "phase": PHASE,
        "operation": OPERATION,
        "request_path": os.fspath(prepared.output),
        "request_sha256": prepared.sha256,
        "source_record_sha256": prepared.source_record.sha256,
        "source_binding_sha256": prepared.document["source_binding_sha256"],
        "publication": publication,
        "output_mutated": publication == "created",
        "journal_mutated": False,
        "production_contacted": False,
    }


def load_request(
    request_path: Path,
    *,
    now: datetime | None = None,
    require_fresh: bool = True,
) -> tuple[EvidenceContext, SecureRecord, SecureRecord, SourceSet]:
    """Load and fully revalidate one digest-addressed public request."""

    path = _absolute_path(request_path, label="convergence phase request")
    # The path digest is not embedded in its name until after the secure read.
    # Inspect the leaf first, then force the name to match the immutable bytes.
    try:
        payload = read_secure_bytes(path, label="convergence phase request", owner_uid=0, max_size=MAX_JSON_BYTES)
    except SecureFileError as exc:
        raise ConvergenceGateError("convergence phase request is unavailable") from exc
    digest = _sha256(payload)
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _StrictObjectError) as exc:
        raise ConvergenceGateError("convergence phase request is not strict JSON") from exc
    if not isinstance(document, dict):
        raise ConvergenceGateError("convergence phase request root is invalid")
    context = _context_from_request(document)
    _private_directory(_phase_root(context.manifest), label="convergence gate root")
    _private_directory(_request_root(context.manifest), label="convergence request root")
    if path != _request_path(context.manifest, digest):
        raise ConvergenceGateError("convergence phase request path is not canonical")
    request = _read_secure_record(Reference(path, digest), label="convergence phase request")
    now_value = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    source_reference = _reference(request.document["source_record"], label="request source record")
    source, source_set = _load_source_record(
        context,
        source_reference,
        now=now_value,
        require_fresh=require_fresh,
    )
    if request.document["source_binding_sha256"] != source.document["source_binding_sha256"]:
        raise ConvergenceGateError("request source binding differs")
    _assert_records_unchanged({"request": request, "source-record": source})
    return context, request, source, source_set


def _derived_claim_values(
    context: EvidenceContext,
    *,
    source_record: SecureRecord,
    source_set: SourceSet,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Derive every verifier claim from immutable source bytes only."""

    migrated_schema, fence_configuration, schema_source = _validate_schema_role_fence(context)
    observation_hashes = {
        label: source_set.observations[label].sha256 for label in SOURCE_LABELS
    }
    state_inputs = {
        "source_record_sha256": source_record.sha256,
        "source_binding_sha256": source_record.document["source_binding_sha256"],
        "schema_role_fence_source_sha256": schema_source,
        "observations": observation_hashes,
        "migrated_schema_fingerprint_sha256": migrated_schema,
        "fence_configuration_sha256": fence_configuration,
    }
    convergence_state = _sha256(_canonical_json(state_inputs))
    values = {
        "schema_role_fence_verified": True,
        "queue_state_verified": True,
        "database_business_drift_count": 0,
        "dr_unapplied_event_count": 0,
        "dr_tls_peer_handshakes_verified": True,
        "blob_keyring_roundtrip_verified": True,
        "destination_firewall_allowlists_verified": True,
        "signed_witness_attestation_verified": True,
        "migrated_schema_fingerprint_sha256": migrated_schema,
        "fence_configuration_sha256": fence_configuration,
        "convergence_state_sha256": convergence_state,
    }
    if set(values) != set(CLAIMS):
        raise ConvergenceGateError("derived convergence claim set differs")
    source_hashes = {
        "schema_role_fence_verified": schema_source,
        "queue_state_verified": observation_hashes["queue_state"],
        "database_business_drift_count": observation_hashes["database_parity"],
        "dr_unapplied_event_count": observation_hashes["dr_convergence"],
        "dr_tls_peer_handshakes_verified": observation_hashes["dr_tls"],
        "blob_keyring_roundtrip_verified": observation_hashes["blob_roundtrip"],
        "destination_firewall_allowlists_verified": observation_hashes["destination_firewall"],
        "signed_witness_attestation_verified": observation_hashes["witness_live"],
        "migrated_schema_fingerprint_sha256": context.prior_digests["shadow_migrate"],
        "fence_configuration_sha256": context.prior_digests["shadow_fence"],
        "convergence_state_sha256": source_record.sha256,
    }
    if set(source_hashes) != set(CLAIMS):
        raise ConvergenceGateError("derived convergence claim source set differs")
    for claim, value in values.items():
        try:
            VERIFY._validate_claim(  # noqa: SLF001
                claim,
                {"value": value, "source_sha256": source_hashes[claim]},
                VERIFY.PHASE_CLAIM_RULES[PHASE][claim],
            )
        except VERIFY.PhaseEvidenceError as exc:
            raise ConvergenceGateError(f"derived convergence claim {claim} is invalid") from exc
    return values, source_hashes


def _claim_source_documents(
    context: EvidenceContext,
    *,
    source_record: SecureRecord,
    source_set: SourceSet,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    values, independent_source_hashes = _derived_claim_values(
        context,
        source_record=source_record,
        source_set=source_set,
    )
    observed_at = source_record.document["captured_at"]
    documents: dict[str, dict[str, Any]] = {}
    provenance: dict[str, str] = {}
    for claim in CLAIMS:
        document = {
            "schema": "production-shadow-phase-claim-source-v1",
            "campaign_id": context.manifest["campaign_id"],
            "operation_id": context.manifest["operation_id"],
            "release_sha": context.manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "phase": PHASE,
            "operation": OPERATION,
            "claim": claim,
            "value": values[claim],
            "observed_at": observed_at,
            "status": "observed",
        }
        if set(document) != VERIFY.CLAIM_SOURCE_FIELDS:
            raise ConvergenceGateError(f"claim source {claim} fields differ")
        documents[claim] = document
        provenance[claim] = independent_source_hashes[claim]
    return documents, provenance


def _materialize_claim_sources(
    context: EvidenceContext,
    *,
    source_record: SecureRecord,
    source_set: SourceSet,
) -> tuple[dict[str, Reference], dict[str, str], dict[str, Any], dict[str, str]]:
    documents, provenance = _claim_source_documents(
        context,
        source_record=source_record,
        source_set=source_set,
    )
    root = _phase_root(context.manifest)
    _ensure_private_child(context.evidence_root, "convergence-gate", label="convergence gate root")
    _ensure_private_child(root, "claim-sources", label="convergence claim-source root")
    references: dict[str, Reference] = {}
    source_hashes: dict[str, str] = {}
    for claim in CLAIMS:
        payload = _canonical_json(documents[claim]) + b"\n"
        digest = _sha256(payload)
        path = _claim_path(context.manifest, claim, digest)
        _write_new_or_same(path, payload, label=f"convergence claim source {claim}")
        references[claim] = Reference(path, digest)
        source_hashes[claim] = digest
    return references, source_hashes, documents, provenance


def _read_role_validation_inputs(
    context: EvidenceContext,
    source_set: SourceSet,
    *,
    now: datetime,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    try:
        requests, sources, observed_at = VERIFY._read_role_validation_records(  # noqa: SLF001
            [f"{role}={source_set.role_validation[role].path}" for role in ROLES],
            phase=PHASE,
            manifest=context.manifest,
            manifest_sha256=context.manifest_sha256,
            now=now,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise ConvergenceGateError("convergence role validation inputs are invalid") from exc
    for role in ROLES:
        if sources.get(role) != source_set.role_validation[role].sha256:
            raise ConvergenceGateError(f"convergence role {role} readback digest differs")
    return requests, sources, observed_at


def _build_evidence_document(
    context: EvidenceContext,
    *,
    source_record: SecureRecord,
    source_set: SourceSet,
    claim_references: Mapping[str, Reference],
    claim_source_hashes: Mapping[str, str],
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    role_requests, role_sources, role_observed_at = _read_role_validation_inputs(
        context,
        source_set,
        now=now,
    )
    try:
        dynamic_values, loaded_claim_hashes = VERIFY._read_claim_source_records(  # noqa: SLF001
            [f"{claim}={claim_references[claim].path}" for claim in CLAIMS],
            phase=PHASE,
            manifest=context.manifest,
            manifest_sha256=context.manifest_sha256,
            now=now,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise ConvergenceGateError("derived convergence claim source is invalid") from exc
    if dict(loaded_claim_hashes) != dict(claim_source_hashes):
        raise ConvergenceGateError("derived convergence claim source digest differs")
    values, _provenance = _derived_claim_values(
        context,
        source_record=source_record,
        source_set=source_set,
    )
    if any(dynamic_values.get(name) != values[name] for name, rule in VERIFY.PHASE_CLAIM_RULES[PHASE].items() if rule.kind != "exact"):
        raise ConvergenceGateError("derived dynamic convergence claim differs")
    prior_rows = [
        {"phase": phase, "evidence_sha256": context.prior_digests[phase]}
        for phase in PRIOR_PHASES
    ]
    prior_records = {
        phase: {"document": context.prior_records[phase], "file_sha256": context.prior_digests[phase]}
        for phase in PRIOR_PHASES
    }
    try:
        prior_claim_rows = VERIFY._derive_prior_claim_rows(  # noqa: SLF001
            phase=PHASE,
            prior_digests=dict(context.prior_digests),
            prior_records=prior_records,
            campaign_id=context.manifest["campaign_id"],
            operation_id=context.manifest["operation_id"],
            release_sha=context.manifest["release_sha"],
            legacy_release_sha=context.manifest["legacy_release_sha"],
            manifest_sha256=context.manifest_sha256,
            plan_sha256=context.plan_sha256,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise ConvergenceGateError("convergence prior phase prefix is not semantically valid") from exc
    observed_times = [
        _timestamp(value, label=f"role {role} observation")
        for role, value in role_observed_at.items()
    ]
    observed_times.append(_timestamp(source_record.document["captured_at"], label="convergence source capture"))
    captured_at = _timestamp_text(max(observed_times))
    role_documents = {
        role: source_set.role_validation[role].document
        for role in ROLES
    }
    for role, role_document in role_documents.items():
        if (
            role_document.get("request_sha256") != role_requests[role]
            or role_document.get("observed_at") != role_observed_at[role]
            or source_set.role_validation[role].sha256 != role_sources[role]
        ):
            raise ConvergenceGateError(
                f"convergence role {role} proof closure changed before evidence assembly"
            )
    phase_input = {
        "manifest_sha256": context.manifest_sha256,
        "manifest_artifacts_sha256": _sha256(_canonical_json(context.manifest["artifacts"])),
        "prior_phase_evidence": prior_rows,
        "prior_claim_bindings": prior_claim_rows,
        "dynamic_claim_values": dynamic_values,
        "claim_source_sha256": {claim: claim_source_hashes[claim] for claim in sorted(CLAIMS)},
        "role_request_sha256": {role: role_requests[role] for role in ROLES},
        "role_source_artifact_sha256": {role: role_sources[role] for role in ROLES},
        "role_observed_at": {role: role_observed_at[role] for role in ROLES},
    }
    document = {
        "schema": VERIFY.EVIDENCE_SCHEMA,
        "phase_evidence_schema_sha256": VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "approval_sha256": context.manifest["artifacts"]["cutover_approval_sha256"],
        "manifest_artifact_bindings": dict(context.manifest["artifacts"]),
        "phase": PHASE,
        "operation": OPERATION,
        "journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "status": "passed",
        "captured_at": captured_at,
        "business_write_observed": False,
        "prior_phase_evidence": prior_rows,
        "prior_phase_evidence_closure_sha256": _sha256(_canonical_json(prior_rows)),
        "prior_claim_bindings": prior_claim_rows,
        "phase_input_closure_sha256": _sha256(_canonical_json(phase_input)),
        "role_attestations": [
            {
                "role": role,
                # These values came from the re-opened local host proof and
                # transport receipt, not from controller topology.
                "expected_host": role_documents[role]["expected_host"],
                "operation": OPERATION,
                "request_sha256": role_requests[role],
                "app_release_sha": context.manifest["release_sha"],
                "agent_artifact_sha256": context.manifest["artifacts"]["host_agent_sha256"],
                "host_identity_observed": role_documents[role]["host_identity_observed"],
                "observed_at": role_observed_at[role],
                "status": "verified",
                "transport": role_documents[role]["transport"],
                "source_artifact_sha256": role_sources[role],
            }
            for role in ROLES
        ],
        "claims": {
            claim: {"value": values[claim], "source_sha256": claim_source_hashes[claim]}
            for claim in CLAIMS
        },
    }
    if set(document) != VERIFY.EVIDENCE_FIELDS:
        raise ConvergenceGateError("constructed convergence evidence fields differ")
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    try:
        verification = VERIFY.verify_phase_evidence(
            document,
            expected_phase=PHASE,
            expected_campaign_id=context.manifest["campaign_id"],
            expected_operation_id=context.manifest["operation_id"],
            expected_release_sha=context.manifest["release_sha"],
            expected_legacy_release_sha=context.manifest["legacy_release_sha"],
            expected_manifest_sha256=context.manifest_sha256,
            expected_plan_sha256=context.plan_sha256,
            expected_approval_sha256=context.manifest["artifacts"]["cutover_approval_sha256"],
            expected_phase_evidence_schema_sha256=context.manifest["artifacts"]["phase_evidence_schema_sha256"],
            expected_manifest_artifacts=dict(context.manifest["artifacts"]),
            expected_role_request_sha256=dict(role_requests),
            expected_role_source_artifact_sha256=dict(role_sources),
            expected_role_observed_at=dict(role_observed_at),
            expected_dynamic_claim_values=dict(dynamic_values),
            expected_claim_source_sha256=dict(claim_source_hashes),
            expected_prior_phase_evidence_sha256=dict(context.prior_digests),
            prior_phase_evidence_records=prior_records,
            now=now,
            evidence_file_sha256=digest,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise ConvergenceGateError("constructed convergence evidence failed self-verification") from exc
    if verification.get("status") != "verified" or verification.get("evidence_sha256") != digest:
        raise ConvergenceGateError("convergence evidence self-verification result differs")
    return document, verification


def build_plan(
    *,
    context: EvidenceContext | None,
    source_available: bool,
    source_record_sha256: str | None = None,
    source_binding_sha256: str | None = None,
    request_sha256: str | None = None,
) -> dict[str, Any]:
    """Render a no-I/O plan.  It never claims a missing producer is safe."""

    if type(source_available) is not bool:
        raise ConvergenceGateError("convergence source availability is invalid")
    if context is None:
        if source_available or any(value is not None for value in (source_record_sha256, source_binding_sha256, request_sha256)):
            raise ConvergenceGateError("unbound convergence plan cannot carry source bindings")
        identity = {
            "campaign_id": None,
            "operation_id": None,
            "release_sha": None,
            "manifest_sha256": None,
            "controller_plan_sha256": None,
        }
    else:
        _validate_context(context)
        identity = {
            "campaign_id": context.manifest["campaign_id"],
            "operation_id": context.manifest["operation_id"],
            "release_sha": context.manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "controller_plan_sha256": context.plan_sha256,
        }
        if source_available:
            for value, label in (
                (source_record_sha256, "source record"),
                (source_binding_sha256, "source binding"),
            ):
                _nonzero_sha256(value, label=f"convergence plan {label}")
            if request_sha256 is not None:
                _nonzero_sha256(request_sha256, label="convergence plan request")
        elif any(value is not None for value in (source_record_sha256, source_binding_sha256, request_sha256)):
            raise ConvergenceGateError("unavailable convergence plan carries source bindings")
    body = {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "phase": PHASE,
        "operation": OPERATION,
        "roles": list(ROLES),
        "claims": list(CLAIMS),
        **identity,
        "source_available": source_available,
        "trusted_production_observer_available": source_available,
        "missing_producer_behavior": "fail-closed",
        "staging_artifacts_accepted_as_production": False,
        "caller_claim_values_accepted": False,
        "journal_begin_required_before_observation_collection": True,
        "journal_begin_performed_by_bridge": False,
        "prestarted_journal_required_for_apply": True,
        "runtime_authorization_required_for_apply": True,
        "controller_liveness_required_for_apply": True,
        "release_verifier_required": True,
        "bridge_network_io": False,
        "bridge_docker_io": False,
        "bridge_ssh_io": False,
        "production_contacted": False,
        "journal_mutated": False,
        "source_record_sha256": source_record_sha256,
        "source_binding_sha256": source_binding_sha256,
        "request_sha256": request_sha256,
    }
    digest = _sha256(_canonical_json(body))
    confirmation = None
    if source_available and context is not None:
        confirmation = (
            f"run-{PHASE}:{context.manifest['operation_id']}:"
            f"{context.manifest['release_sha']}:{request_sha256}:{digest}"
        )
    return {**body, "plan_sha256": digest, "required_confirmation": confirmation}


def _verify_runtime_authorization(context: EvidenceContext) -> None:
    try:
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            dict(context.manifest),
            approval_path=context.approval_path,
            approval_policy_path=context.approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise ConvergenceGateError("production approval is invalid or expired") from exc


def _load_verification_candidate(
    context: EvidenceContext,
    *,
    source_record_sha256: str,
    evidence_sha256: str,
) -> tuple[CONTROLLER.VerifiedPhaseCompletion, bytes] | None:
    path = _candidate_path(context.manifest, source_record_sha256, evidence_sha256)
    try:
        payload = read_secure_bytes(path, label="convergence verification candidate", owner_uid=0, max_size=64 * 1024)
    except SecureFileError:
        return None
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
        verification, canonical = CONTROLLER._validate_phase_verification_result(  # noqa: SLF001
            document,
            phase=PHASE,
            manifest=dict(context.manifest),
            manifest_sha256=context.manifest_sha256,
            plan_sha256=context.plan_sha256,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _StrictObjectError, CONTROLLER.CutoverContractError) as exc:
        raise ConvergenceGateError("convergence verification candidate is invalid") from exc
    if (
        canonical != payload
        or verification.phase != PHASE
        or verification.evidence_sha256 != evidence_sha256
        or _sha256(payload) != verification.receipt_sha256
    ):
        raise ConvergenceGateError("convergence verification candidate differs")
    return verification, payload


def _write_candidate(
    context: EvidenceContext,
    *,
    source_record_sha256: str,
    verification: CONTROLLER.VerifiedPhaseCompletion,
    receipt: bytes,
) -> None:
    if _sha256(receipt) != verification.receipt_sha256:
        raise ConvergenceGateError("convergence verification receipt digest differs")
    root = _phase_root(context.manifest)
    _ensure_private_child(context.evidence_root, "convergence-gate", label="convergence gate root")
    _ensure_private_child(root, "verification-candidates", label="convergence verification candidate root")
    _write_new_or_same(
        _candidate_path(context.manifest, source_record_sha256, verification.evidence_sha256),
        receipt,
        label="convergence verification candidate",
    )


def _publish_evidence(
    context: EvidenceContext,
    *,
    evidence: Mapping[str, Any],
) -> tuple[Path, str, str]:
    payload = _canonical_json(dict(evidence)) + b"\n"
    digest = _sha256(payload)
    root = _phase_root(context.manifest)
    _ensure_private_child(context.evidence_root, "convergence-gate", label="convergence gate root")
    _ensure_private_child(root, "evidence", label="convergence evidence root")
    path = _evidence_path(context.manifest, digest)
    publication = _write_new_or_same(path, payload, label="convergence phase evidence")
    try:
        document, observed_digest = VERIFY.read_root_only_evidence(path)
    except VERIFY.PhaseEvidenceError as exc:
        raise ConvergenceGateError("published convergence evidence is unavailable") from exc
    if observed_digest != digest or document != evidence:
        raise ConvergenceGateError("published convergence evidence readback differs")
    return path, digest, publication


def _load_completed_phase(
    context: EvidenceContext,
    *,
    state: Mapping[str, Any],
    request: SecureRecord,
    source_record: SecureRecord,
    source_set: SourceSet,
) -> dict[str, Any]:
    evidence_sha256 = state["phase_evidence_sha256"].get(PHASE)
    receipt_sha256 = state["phase_verification_sha256"].get(PHASE)
    _nonzero_sha256(evidence_sha256, label="completed convergence evidence")
    _nonzero_sha256(receipt_sha256, label="completed convergence verification receipt")
    evidence_path = _evidence_path(context.manifest, evidence_sha256)
    receipt_path = context.evidence_root / "verification" / f"{PHASE}.{receipt_sha256}.json"
    try:
        evidence, observed_evidence = VERIFY.read_root_only_evidence(evidence_path)
        receipt = read_secure_bytes(receipt_path, label="convergence release verification receipt", owner_uid=0, max_size=64 * 1024)
        receipt_document = json.loads(receipt.decode("utf-8"), object_pairs_hook=_strict_object)
        verification, canonical = CONTROLLER._validate_phase_verification_result(  # noqa: SLF001
            receipt_document,
            phase=PHASE,
            manifest=dict(context.manifest),
            manifest_sha256=context.manifest_sha256,
            plan_sha256=context.plan_sha256,
        )
    except (VERIFY.PhaseEvidenceError, SecureFileError, UnicodeDecodeError, json.JSONDecodeError, _StrictObjectError, CONTROLLER.CutoverContractError) as exc:
        raise ConvergenceGateError("completed convergence phase is unavailable") from exc
    if (
        observed_evidence != evidence_sha256
        or canonical != receipt
        or _sha256(receipt) != receipt_sha256
        or verification.evidence_sha256 != evidence_sha256
        or verification.receipt_sha256 != receipt_sha256
        or evidence.get("phase") != PHASE
        or evidence.get("status") != "passed"
        or evidence.get("business_write_observed") is not False
    ):
        raise ConvergenceGateError("completed convergence phase differs from journal")
    # Reconstruct source-derived values at their immutable capture instant.
    # This catches a forged evidence document without requiring a still-live
    # lease after the phase has already completed.
    values, _source_hashes = _derived_claim_values(
        context,
        source_record=source_record,
        source_set=source_set,
    )
    if any(evidence.get("claims", {}).get(claim, {}).get("value") != values[claim] for claim in CLAIMS):
        raise ConvergenceGateError("completed convergence evidence claim differs from source closure")
    return {
        "status": "completed-reused",
        "request_path": os.fspath(request.path),
        "request_sha256": request.sha256,
        "source_record_sha256": source_record.sha256,
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "verification_receipt_path": os.fspath(receipt_path),
        "verification_receipt_sha256": receipt_sha256,
        "journal_status": state["status"],
        "journal_mutated": False,
        "production_contacted": False,
    }


def apply_phase(
    request_path: Path,
    *,
    confirm: str,
    control_fd: int | None,
    now: datetime | None = None,
    liveness_factory: Any = _DEFAULT_APPLY_DEPENDENCY,
    signal_authority_factory: Any = _DEFAULT_APPLY_DEPENDENCY,
    authorization_verifier: Any = _verify_runtime_authorization,
    journal_factory: Any = CONTROLLER.ProductionCutoverJournal,
    release_verifier: Any = CONTROLLER._run_release_phase_verifier,  # noqa: SLF001
    receipt_persister: Any = CONTROLLER._persist_phase_verification_receipt,  # noqa: SLF001
) -> dict[str, Any]:
    """Complete only a durably-started convergence phase from fresh sources."""

    observed_now = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    context, request, source_record, source_set = load_request(
        request_path,
        now=observed_now,
        require_fresh=False,
    )
    try:
        journal = journal_factory(context.journal_path)
        state = journal.assert_bindings(**_journal_bindings(context))
    except CONTROLLER.CutoverContractError as exc:
        raise ConvergenceGateError("convergence journal binding differs") from exc
    live_context = replace(context, journal=state)
    position = _position(state)
    if position == "completed":
        return _load_completed_phase(
            live_context,
            state=state,
            request=request,
            source_record=source_record,
            source_set=source_set,
        )

    _validate_context(live_context, required_position="started")
    plan = build_plan(
        context=live_context,
        source_available=True,
        source_record_sha256=source_record.sha256,
        source_binding_sha256=source_record.document["source_binding_sha256"],
        request_sha256=request.sha256,
    )
    if confirm != plan["required_confirmation"]:
        raise ConvergenceGateError("convergence apply requires exact digest-bound confirmation")
    if type(control_fd) is not int or control_fd < 0:
        raise ConvergenceGateError("convergence apply requires controller liveness")

    if (
        liveness_factory is _DEFAULT_APPLY_DEPENDENCY
        or signal_authority_factory is _DEFAULT_APPLY_DEPENDENCY
    ):
        # Source-set inspection and fully injected application paths must not
        # load the prepared-clone runtime.  Resolve it only for a missing
        # implementation default.
        from scripts import orchestrate_production_shadow_prepared_clone_inventory as prepared

        if liveness_factory is _DEFAULT_APPLY_DEPENDENCY:
            liveness_factory = prepared.ControllerLiveness
        if signal_authority_factory is _DEFAULT_APPLY_DEPENDENCY:
            signal_authority_factory = prepared._signal_authority  # noqa: SLF001

    if not all(callable(item) for item in (
        liveness_factory,
        signal_authority_factory,
        authorization_verifier,
        journal_factory,
        release_verifier,
        receipt_persister,
    )):
        raise ConvergenceGateError("convergence apply dependency is unavailable")
    try:
        with signal_authority_factory(), liveness_factory(control_fd) as liveness:
            liveness.check()
            authorization_verifier(live_context)
            # Freshly re-open the request after liveness and approval.  This
            # rejects a source that aged out while an operator reviewed it.
            context, request, source_record, source_set = load_request(
                request_path,
                now=observed_now,
                require_fresh=True,
            )
            state = journal.assert_bindings(**_journal_bindings(context))
            live_context = replace(context, journal=state)
            _validate_context(live_context, required_position="started")
            liveness.check()
            authorization_verifier(live_context)
            claim_references, claim_hashes, _claim_documents, _provenance = _materialize_claim_sources(
                live_context,
                source_record=source_record,
                source_set=source_set,
            )
            liveness.check()
            authorization_verifier(live_context)
            evidence, _self_verification = _build_evidence_document(
                live_context,
                source_record=source_record,
                source_set=source_set,
                claim_references=claim_references,
                claim_source_hashes=claim_hashes,
                now=observed_now,
            )
            evidence_path, evidence_sha256, evidence_publication = _publish_evidence(
                live_context,
                evidence=evidence,
            )
            liveness.check()
            authorization_verifier(live_context)
            candidate = _load_verification_candidate(
                live_context,
                source_record_sha256=source_record.sha256,
                evidence_sha256=evidence_sha256,
            )
            if candidate is None:
                verification, receipt = release_verifier(
                    phase=PHASE,
                    manifest=dict(live_context.manifest),
                    manifest_sha256=live_context.manifest_sha256,
                    plan=dict(live_context.plan),
                    manifest_path=live_context.manifest_path,
                    approval_path=live_context.approval_path,
                    approval_policy_path=live_context.approval_policy_path,
                    evidence_path=evidence_path,
                    role_validation=[f"{role}={source_set.role_validation[role].path}" for role in ROLES],
                    claim_source=[f"{claim}={claim_references[claim].path}" for claim in CLAIMS],
                    prior_phase_evidence=[f"{phase}={live_context.prior_paths[phase]}" for phase in PRIOR_PHASES],
                )
                if (
                    not isinstance(verification, CONTROLLER.VerifiedPhaseCompletion)
                    or verification.phase != PHASE
                    or verification.evidence_sha256 != evidence_sha256
                    or _sha256(receipt) != verification.receipt_sha256
                ):
                    raise ConvergenceGateError("release verifier completion differs")
                _write_candidate(
                    live_context,
                    source_record_sha256=source_record.sha256,
                    verification=verification,
                    receipt=receipt,
                )
                candidate = _load_verification_candidate(
                    live_context,
                    source_record_sha256=source_record.sha256,
                    evidence_sha256=evidence_sha256,
                )
                if candidate is None:
                    raise ConvergenceGateError("convergence verification candidate was not persisted")
            verification, receipt = candidate
            liveness.check()
            authorization_verifier(live_context)
            receipt_path = receipt_persister(
                token=verification,
                receipt=receipt,
                evidence_root=live_context.evidence_root,
            )
            liveness.check()
            authorization_verifier(live_context)
            completed = journal.complete_phase(PHASE, verification=verification)
            liveness.check()
    except (
        CONTROLLER.CutoverContractError,
        PreparedCloneInventoryError,
        VERIFY.PhaseEvidenceError,
        SecureFileError,
    ) as exc:
        raise ConvergenceGateError("convergence phase apply failed closed") from exc
    final_context = replace(live_context, journal=completed)
    _validate_context(final_context, required_position="completed")
    if (
        completed["phase_evidence_sha256"].get(PHASE) != verification.evidence_sha256
        or completed["phase_verification_sha256"].get(PHASE) != verification.receipt_sha256
    ):
        raise ConvergenceGateError("convergence journal completion differs")
    return {
        **plan,
        "status": "completed",
        "request_path": os.fspath(request.path),
        "request_sha256": request.sha256,
        "source_record_sha256": source_record.sha256,
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "evidence_publication": evidence_publication,
        "verification_receipt_path": os.fspath(receipt_path),
        "verification_receipt_sha256": verification.receipt_sha256,
        "journal_status": completed["status"],
        "journal_mutated": True,
        "production_contacted": False,
        "network_io": False,
        "docker_invoked": False,
        "ssh_invoked": False,
    }


def _parse_path_sha(value: str, *, label: str) -> Reference:
    path_text, marker, digest = str(value).rpartition("@")
    if not marker or not path_text or not digest:
        raise ConvergenceGateError(f"{label} must be /absolute/path@sha256")
    return Reference(
        path=_absolute_path(path_text, label=label),
        sha256=_nonzero_sha256(digest, label=label),
    )


def _parse_prior_paths(values: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        phase, marker, raw_path = str(value).partition("=")
        if not marker or phase not in PRIOR_PHASES or phase in result:
            raise ConvergenceGateError("prior phase evidence arguments differ")
        result[phase] = _absolute_path(raw_path, label=f"prior phase {phase}")
    if set(result) != set(PRIOR_PHASES):
        raise ConvergenceGateError("prior phase evidence arguments are not the exact prefix")
    return result


def _plan_result(
    context: EvidenceContext,
    *,
    source_set: Reference | None,
) -> dict[str, Any]:
    if source_set is None:
        plan = build_plan(context=context, source_available=False)
        return {
            **plan,
            "source_status": "unavailable",
            "source_reason": "production observer source-set was not supplied",
        }
    try:
        source = _validate_source_set(
            context,
            source_set,
            now=datetime.now(timezone.utc),
            require_fresh=_position(context.journal) == "started",
        )
        source_document = _build_source_record_document(context, source)
        source_payload = _canonical_json(source_document) + b"\n"
        return {
            **build_plan(
                context=context,
                source_available=True,
                source_record_sha256=_sha256(source_payload),
                source_binding_sha256=source_document["source_binding_sha256"],
                request_sha256=None,
            ),
            "source_status": "available-but-unpublished",
        }
    except ConvergenceGateError:
        return {
            **build_plan(context=context, source_available=False),
            "source_status": "unavailable",
            "source_reason": "fresh trusted production observer source-set is unavailable",
        }


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ConvergenceGateError("convergence bridge arguments are invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument(
        "--action",
        choices=("plan", "source-record", "request", "apply"),
        required=True,
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--prior-phase-evidence", action="append", default=[], metavar="PHASE=/ABS/PATH")
    parser.add_argument("--source-set", metavar="/ABS/PATH@SHA256")
    parser.add_argument("--source-record", metavar="/ABS/PATH@SHA256")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--control-fd", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.action == "apply":
            if (
                not args.apply
                or args.request is None
                or args.confirm is None
                or args.control_fd is None
                or args.source_set is not None
                or args.source_record is not None
            ):
                raise ConvergenceGateError("convergence apply requires only request, confirmation, and liveness")
            result = apply_phase(
                args.request,
                confirm=args.confirm,
                control_fd=args.control_fd,
            )
        else:
            if args.request is not None or args.control_fd is not None:
                raise ConvergenceGateError("request path and liveness are valid only for convergence apply")
            context = load_evidence_context(
                manifest_path=args.manifest,
                approval_path=args.approval,
                approval_policy_path=args.approval_policy,
                prior_evidence_paths=_parse_prior_paths(args.prior_phase_evidence),
            )
            if args.action == "plan":
                if args.apply or args.confirm is not None or args.source_record is not None:
                    raise ConvergenceGateError("convergence plan does not accept apply, confirmation, or source record")
                source_set = _parse_path_sha(args.source_set, label="source-set") if args.source_set else None
                result = _plan_result(context, source_set=source_set)
            elif args.action == "source-record":
                if args.source_set is None or args.source_record is not None:
                    raise ConvergenceGateError("source-record action requires exactly one source-set")
                prepared = prepare_source_record(
                    context,
                    source_set=_parse_path_sha(args.source_set, label="source-set"),
                )
                base = {
                    "schema": RESULT_SCHEMA,
                    "status": "planned",
                    "kind": "source-record",
                    "phase": PHASE,
                    "operation": OPERATION,
                    "source_record_path": os.fspath(prepared.output),
                    "source_record_sha256": prepared.sha256,
                    "source_binding_sha256": prepared.document["source_binding_sha256"],
                    "required_confirmation": prepared.required_confirmation,
                    "output_mutated": False,
                    "journal_mutated": False,
                    "production_contacted": False,
                }
                result = (
                    publish_source_record(prepared, confirm=args.confirm or "")
                    if args.apply
                    else base
                )
                if not args.apply and args.confirm is not None:
                    raise ConvergenceGateError("source-record confirmation is valid only with --apply")
            else:
                if args.source_record is None or args.source_set is not None:
                    raise ConvergenceGateError("request action requires exactly one source record")
                prepared = prepare_request(
                    context,
                    source_record=_parse_path_sha(args.source_record, label="source record"),
                )
                base = {
                    "schema": RESULT_SCHEMA,
                    "status": "planned",
                    "kind": "request",
                    "phase": PHASE,
                    "operation": OPERATION,
                    "request_path": os.fspath(prepared.output),
                    "request_sha256": prepared.sha256,
                    "source_record_sha256": prepared.source_record.sha256,
                    "source_binding_sha256": prepared.document["source_binding_sha256"],
                    "required_confirmation": prepared.required_confirmation,
                    "output_mutated": False,
                    "journal_mutated": False,
                    "production_contacted": False,
                }
                result = (
                    publish_request(prepared, confirm=args.confirm or "")
                    if args.apply
                    else base
                )
                if not args.apply and args.confirm is not None:
                    raise ConvergenceGateError("request confirmation is valid only with --apply")
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except ConvergenceSourceUnavailable:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": "fresh trusted production convergence source is unavailable",
                    "error_class": "ConvergenceSourceUnavailable",
                    "journal_mutated": False,
                    "production_contacted": False,
                    "network_io": False,
                    "docker_invoked": False,
                    "ssh_invoked": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": "convergence gate request was rejected",
                    "error_class": "ConvergenceGateError",
                    "journal_mutated": False,
                    "production_contacted": False,
                    "network_io": False,
                    "docker_invoked": False,
                    "ssh_invoked": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
