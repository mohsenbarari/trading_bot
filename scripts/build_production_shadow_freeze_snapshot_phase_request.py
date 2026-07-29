#!/usr/bin/env python3
"""Build the immutable v2 freeze/snapshot public-phase bridge request.

The builder is controller-local and read-only apart from one create-only
request publication.  Every input is an absolute path plus an expected
SHA-256.  Campaign, release, controller-plan, output, role, and constraint
values are derived from the verified manifest and referenced artifacts.

No function in this module contacts a host, Docker, Redis, Object Storage, or
the production cutover journal.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    write_secure_new_bytes,
)
from scripts import orchestrate_production_shadow_current_frozen_verification as CURRENT  # noqa: E402
from scripts import orchestrate_production_shadow_freeze_snapshot_phases as BRIDGE  # noqa: E402
from scripts import orchestrate_production_shadow_nginx_cutover_phases as NGINX_PHASES  # noqa: E402
from scripts import orchestrate_production_shadow_pre_freeze_evidence as PRE_FREEZE  # noqa: E402
from scripts import orchestrate_production_shadow_startup_normalization_phase as STARTUP  # noqa: E402
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402


PLAN_SCHEMA = "production-shadow-freeze-snapshot-request-build-plan-v1"
RESULT_SCHEMA = "production-shadow-freeze-snapshot-request-build-result-v1"
OUTPUT_DIRECTORY = "requests"
OUTPUT_PREFIX = "freeze-snapshot-phase-request"
COORDINATOR_RESULT_DIRECTORY = "results"
COORDINATOR_RESULT_PREFIX = "frozen-snapshot-result"
MAX_JSON_BYTES = BRIDGE.MAX_JSON_BYTES

SAFETY_RESULT = {
    "network_io": False,
    "ssh_contacted": False,
    "docker_contacted": False,
    "object_storage_contacted": False,
    "production_contacted": False,
    "journal_mutated": False,
    "service_mutated": False,
    "runtime_mutated": False,
}


class FreezeSnapshotRequestBuildError(RuntimeError):
    """The bridge request cannot be built without weakening its contract."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise FreezeSnapshotRequestBuildError(
            "freeze/snapshot request arguments are invalid"
        )


@dataclass(frozen=True)
class Reference:
    path: Path
    sha256: str


@dataclass(frozen=True)
class RequestReferences:
    manifest: Reference
    approval: Reference
    approval_policy: Reference
    prior_phase_evidence: Mapping[str, Reference]
    frozen_snapshot_result: Reference
    current_frozen_verification_receipt: Reference
    nginx_readback_receipt: Reference
    roles: Mapping[str, Mapping[str, Reference]]


@dataclass(frozen=True)
class PreparedRequest:
    document: Mapping[str, Any]
    payload: bytes
    sha256: str
    output: Path
    context: BRIDGE.BridgeContext
    source_records: Mapping[str, BRIDGE.SecureRecord]
    source_closure_sha256: str
    input_records: Mapping[str, BRIDGE.SecureRecord]
    input_closure_sha256: str


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
        raise FreezeSnapshotRequestBuildError(
            "request value is not canonical JSON"
        ) from exc


def _fresh_observation() -> datetime:
    """Return real controller time for every apply-time source check."""

    return datetime.now(timezone.utc).astimezone(timezone.utc)


def _observed_time(now: datetime | None = None) -> datetime:
    return _fresh_observation() if now is None else now.astimezone(timezone.utc)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or CONTROLLER.SHA256_RE.fullmatch(value) is None
        or value == BRIDGE.ZERO_SHA256
    ):
        raise FreezeSnapshotRequestBuildError(
            f"{label} SHA-256 is invalid"
        )
    return value


def _absolute_path(value: Any, *, label: str) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise FreezeSnapshotRequestBuildError(
            f"{label} path is invalid"
        ) from exc
    if (
        not path.is_absolute()
        or ".." in path.parts
        or Path(os.path.abspath(path)) != path
    ):
        raise FreezeSnapshotRequestBuildError(
            f"{label} path is not canonical absolute"
        )
    return path


def _reference(path: Any, digest: Any, *, label: str) -> Reference:
    return Reference(
        path=_absolute_path(path, label=label),
        sha256=_nonzero_sha256(digest, label=label),
    )


def _load_reference(
    reference: Reference,
    *,
    label: str,
) -> BRIDGE.SecureRecord:
    try:
        record = BRIDGE._read_private_json(  # noqa: SLF001
            reference.path,
            label=label,
            expected_sha256=reference.sha256,
        )
    except BRIDGE.FreezeSnapshotPhaseBridgeError as exc:
        raise FreezeSnapshotRequestBuildError(
            f"{label} reference is unsafe or differs"
        ) from exc
    try:
        resolved = reference.path.resolve(strict=True)
    except OSError as exc:
        raise FreezeSnapshotRequestBuildError(
            f"{label} canonical path cannot be resolved"
        ) from exc
    if resolved != reference.path:
        raise FreezeSnapshotRequestBuildError(
            f"{label} path traverses a symbolic link"
        )
    return record


def _reference_document(reference: Reference) -> dict[str, str]:
    return {
        "path": os.fspath(reference.path),
        "sha256": reference.sha256,
    }


def _prior_names() -> tuple[str, ...]:
    return tuple(CONTROLLER.PHASES[: BRIDGE.FIRST_PHASE_INDEX])


def _canonical_prior_path(
    manifest: Mapping[str, Any],
    *,
    phase: str,
    digest: str,
) -> Path:
    root = Path(manifest["deployment"]["controller_evidence_root"])
    if phase == PRE_FREEZE.PHASE:
        return (
            root
            / PRE_FREEZE.PHASE
            / "phase-evidence"
            / f"{phase}.{digest}.json"
        )
    if phase == STARTUP.PHASE:
        return (
            root
            / STARTUP.OUTPUT_SUBDIRECTORY
            / phase
            / "evidence"
            / f"{phase}.{digest}.json"
        )
    if phase in NGINX_PHASES.PHASES:
        return (
            root
            / "nginx-cutover-phases"
            / "phases"
            / phase
            / "evidence"
            / f"{phase.replace('_', '-')}-{digest}.json"
        )
    raise FreezeSnapshotRequestBuildError(
        "prior phase has no canonical producer path"
    )


def canonical_coordinator_result_path(
    *,
    operation_id: str,
    release_sha: str,
    digest: str,
) -> Path:
    digest = _nonzero_sha256(
        digest,
        label="frozen snapshot coordinator result",
    )
    paths = BRIDGE.FROZEN.canonical_paths(operation_id, release_sha)
    return (
        Path(paths["controller_root"])
        / COORDINATOR_RESULT_DIRECTORY
        / f"{COORDINATOR_RESULT_PREFIX}.{digest}.json"
    )


def _canonical_request_root(manifest: Mapping[str, Any]) -> Path:
    return (
        Path(manifest["deployment"]["controller_evidence_root"])
        / "freeze-snapshot-phase-bridge"
        / OUTPUT_DIRECTORY
    )


def _canonical_request_path(
    manifest: Mapping[str, Any],
    digest: str,
) -> Path:
    return (
        _canonical_request_root(manifest)
        / f"{OUTPUT_PREFIX}.{digest}.json"
    )


def _assert_private_directory(path: Path, *, label: str) -> None:
    path = _absolute_path(path, label=label)
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise FreezeSnapshotRequestBuildError(
            f"{label} is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise FreezeSnapshotRequestBuildError(
            f"{label} must be root:root mode 0700"
        )


def _ensure_private_child(
    parent: Path,
    name: str,
    *,
    label: str,
) -> Path:
    _assert_private_directory(parent, label=f"{label} parent")
    child = parent / name
    try:
        child.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise FreezeSnapshotRequestBuildError(
            f"{label} cannot be created"
        ) from exc
    _assert_private_directory(child, label=label)
    return child


def _load_manifest_context(
    record: BRIDGE.SecureRecord,
    reference: Reference,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    try:
        manifest, digest = CONTROLLER.read_root_only_manifest(
            reference.path
        )
        plan = CONTROLLER.render_plan(
            manifest,
            manifest_sha256=digest,
            manifest_path=reference.path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise FreezeSnapshotRequestBuildError(
            "production cutover manifest or plan is invalid"
        ) from exc
    if (
        digest != reference.sha256
        or digest != record.sha256
        or manifest != record.document
        or plan.get("plan_sha256") in {None, BRIDGE.ZERO_SHA256}
    ):
        raise FreezeSnapshotRequestBuildError(
            "manifest path/digest reference differs"
        )
    return manifest, digest, plan


def _load_all_records(
    references: RequestReferences,
) -> dict[str, BRIDGE.SecureRecord]:
    records = {
        "manifest": _load_reference(
            references.manifest,
            label="production cutover manifest",
        ),
        "approval": _load_reference(
            references.approval,
            label="production cutover approval",
        ),
        "approval_policy": _load_reference(
            references.approval_policy,
            label="production approval policy",
        ),
        "frozen_snapshot_result": _load_reference(
            references.frozen_snapshot_result,
            label="frozen snapshot coordinator result",
        ),
        "current_frozen_verification_receipt": _load_reference(
            references.current_frozen_verification_receipt,
            label="current frozen verification receipt",
        ),
        "nginx_readback_receipt": _load_reference(
            references.nginx_readback_receipt,
            label="fresh Nginx readback receipt",
        ),
    }
    for phase in _prior_names():
        records[f"prior:{phase}"] = _load_reference(
            references.prior_phase_evidence[phase],
            label=f"prior phase {phase}",
        )
    for role in BRIDGE.ROLE_ORDER:
        for kind in BRIDGE.ROLE_SOURCE_FIELDS:
            records[f"{role}:{kind}"] = _load_reference(
                references.roles[role][kind],
                label=f"{role} {kind}",
            )
    identities: set[tuple[int, int]] = set()
    for record in records.values():
        identity = (record.identity.device, record.identity.inode)
        if identity in identities:
            raise FreezeSnapshotRequestBuildError(
                "independent request inputs share one file identity"
            )
        identities.add(identity)
    return records


def _validate_control_paths(
    *,
    manifest: Mapping[str, Any],
    references: RequestReferences,
) -> None:
    evidence_root = _absolute_path(
        manifest["deployment"]["controller_evidence_root"],
        label="controller evidence root",
    )
    control_root = evidence_root.parent
    _assert_private_directory(control_root, label="controller control root")
    _assert_private_directory(evidence_root, label="controller evidence root")
    if any(
        reference.path.parent != control_root
        for reference in (
            references.manifest,
            references.approval,
            references.approval_policy,
        )
    ):
        raise FreezeSnapshotRequestBuildError(
            "manifest, approval, and policy are outside the canonical "
            "controller root"
        )


def _validate_prior_evidence(
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    plan_sha256: str,
    references: RequestReferences,
    records: Mapping[str, BRIDGE.SecureRecord],
) -> dict[str, Path]:
    if set(references.prior_phase_evidence) != set(_prior_names()):
        raise FreezeSnapshotRequestBuildError(
            "prior phase references are not the exact phase prefix"
        )
    paths: dict[str, Path] = {}
    for phase in _prior_names():
        reference = references.prior_phase_evidence[phase]
        expected_path = _canonical_prior_path(
            manifest,
            phase=phase,
            digest=reference.sha256,
        )
        if reference.path != expected_path:
            raise FreezeSnapshotRequestBuildError(
                f"prior phase {phase} path is not canonical"
            )
        try:
            evidence, digest = VERIFY.read_root_only_evidence(
                reference.path
            )
        except VERIFY.PhaseEvidenceError as exc:
            raise FreezeSnapshotRequestBuildError(
                f"prior phase {phase} evidence is invalid"
            ) from exc
        if (
            digest != reference.sha256
            or records[f"prior:{phase}"].document != evidence
            or evidence.get("phase") != phase
            or evidence.get("campaign_id") != manifest["campaign_id"]
            or evidence.get("operation_id") != manifest["operation_id"]
            or evidence.get("release_sha") != manifest["release_sha"]
            or evidence.get("legacy_release_sha")
            != manifest["legacy_release_sha"]
            or evidence.get("manifest_sha256") != manifest_sha256
            or evidence.get("plan_sha256") != plan_sha256
            or evidence.get("approval_sha256")
            != manifest["artifacts"]["cutover_approval_sha256"]
            or evidence.get("status") != "passed"
            or evidence.get("business_write_observed") is not False
        ):
            raise FreezeSnapshotRequestBuildError(
                f"prior phase {phase} evidence binding differs"
            )
        paths[phase] = reference.path
    return paths


def _validate_producer_paths(
    *,
    context: BRIDGE.BridgeContext,
    references: RequestReferences,
    records: Mapping[str, BRIDGE.SecureRecord],
) -> None:
    try:
        frozen = BRIDGE._validate_frozen_result(  # noqa: SLF001
            records["frozen_snapshot_result"].document,
            context=context,
        )
    except BRIDGE.FreezeSnapshotPhaseBridgeError as exc:
        raise FreezeSnapshotRequestBuildError(
            "frozen snapshot coordinator result is invalid"
        ) from exc
    paths = BRIDGE.FROZEN.canonical_paths(
        context.manifest["operation_id"],
        context.manifest["release_sha"],
        state_receipt_sha256=frozen["state_receipt_sha256"],
    )
    expected_result_path = canonical_coordinator_result_path(
        operation_id=context.manifest["operation_id"],
        release_sha=context.manifest["release_sha"],
        digest=references.frozen_snapshot_result.sha256,
    )
    expected_current_path = (
        CURRENT._paths(  # noqa: SLF001
            context.manifest["operation_id"],
            context.manifest["release_sha"],
        )["verification_receipts"]
        / (
            references.current_frozen_verification_receipt.sha256
            + ".json"
        )
    )
    if (
        references.frozen_snapshot_result.path != expected_result_path
        or references.current_frozen_verification_receipt.path
        != expected_current_path
        or references.nginx_readback_receipt.path
        != Path(paths["state_receipt"])
        or frozen["collection_root"] != os.fspath(paths["collection_root"])
        or frozen["outcome_path"] != os.fspath(paths["outcome"])
        or frozen["journal_path"] != os.fspath(paths["journal"])
    ):
        raise FreezeSnapshotRequestBuildError(
            "coordinator or verification source path is not canonical"
        )
    for role in BRIDGE.ROLE_ORDER:
        expected_role = paths["roles"][role]
        if (
            references.roles[role]["binding"].path
            != Path(expected_role["binding"])
            or references.roles[role]["freeze_evidence"].path
            != Path(expected_role["freeze_evidence"])
            or references.roles[role]["snapshot_manifest"].path
            != Path(expected_role["manifest"])
        ):
            raise FreezeSnapshotRequestBuildError(
                f"{role} source path is not canonical"
            )


def _input_closure(
    records: Mapping[str, BRIDGE.SecureRecord],
) -> str:
    rows = [
        {
            "label": label,
            "path": os.fspath(record.path),
            "sha256": record.sha256,
            "bytes": record.identity.size,
        }
        for label, record in sorted(records.items())
    ]
    return _sha256(_canonical_json(rows))


def _assert_records_unchanged(
    records: Mapping[str, BRIDGE.SecureRecord],
) -> None:
    for label, expected in records.items():
        observed = _load_reference(
            Reference(expected.path, expected.sha256),
            label=f"stable {label}",
        )
        if (
            observed.identity != expected.identity
            or observed.payload != expected.payload
            or observed.document != expected.document
        ):
            raise FreezeSnapshotRequestBuildError(
                f"{label} changed during request construction"
            )


def _build_request_document(
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    plan_sha256: str,
    references: RequestReferences,
) -> dict[str, Any]:
    output_root = (
        Path(manifest["deployment"]["controller_evidence_root"])
        / "freeze-snapshot-phase-bridge"
    )
    document = {
        "schema": BRIDGE.REQUEST_SCHEMA,
        "status": "ready",
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "manifest_path": os.fspath(references.manifest.path),
        "manifest_sha256": manifest_sha256,
        "plan_sha256": plan_sha256,
        "approval_path": os.fspath(references.approval.path),
        "approval_sha256": references.approval.sha256,
        "approval_policy_path": os.fspath(
            references.approval_policy.path
        ),
        "approval_policy_sha256": references.approval_policy.sha256,
        "output_root": os.fspath(output_root),
        "prior_phase_evidence": {
            phase: _reference_document(
                references.prior_phase_evidence[phase]
            )
            for phase in _prior_names()
        },
        "frozen_snapshot_result": _reference_document(
            references.frozen_snapshot_result
        ),
        "current_frozen_verification_receipt": _reference_document(
            references.current_frozen_verification_receipt
        ),
        "nginx_readback_receipt": _reference_document(
            references.nginx_readback_receipt
        ),
        "roles": {
            role: {
                kind: _reference_document(
                    references.roles[role][kind]
                )
                for kind in BRIDGE.ROLE_SOURCE_FIELDS
            }
            for role in BRIDGE.ROLE_ORDER
        },
        "constraints": dict(BRIDGE.EXPECTED_CONSTRAINTS),
    }
    if set(document) != BRIDGE.REQUEST_FIELDS:
        raise FreezeSnapshotRequestBuildError(
            "built request fields differ from the target bridge"
        )
    return document


def prepare_request(
    references: RequestReferences,
    *,
    now: datetime | None = None,
) -> PreparedRequest:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise FreezeSnapshotRequestBuildError(
            "freeze/snapshot request builder requires root:root"
        )
    if (
        not isinstance(references, RequestReferences)
        or set(references.prior_phase_evidence) != set(_prior_names())
        or set(references.roles) != set(BRIDGE.ROLE_ORDER)
        or any(
            set(row) != set(BRIDGE.ROLE_SOURCE_FIELDS)
            for row in references.roles.values()
        )
    ):
        raise FreezeSnapshotRequestBuildError(
            "request reference closure is not exact"
        )
    records = _load_all_records(references)
    manifest, manifest_sha256, plan = _load_manifest_context(
        records["manifest"],
        references.manifest,
    )
    _validate_control_paths(
        manifest=manifest,
        references=references,
    )
    if (
        references.approval.sha256
        != manifest["artifacts"]["cutover_approval_sha256"]
        or references.approval_policy.sha256
        != manifest["artifacts"]["human_approval_policy_sha256"]
    ):
        raise FreezeSnapshotRequestBuildError(
            "approval or policy digest differs from the manifest"
        )
    prior_paths = _validate_prior_evidence(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan["plan_sha256"],
        references=references,
        records=records,
    )
    document = _build_request_document(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan["plan_sha256"],
        references=references,
    )
    payload = _canonical_json(document) + b"\n"
    request_sha256 = _sha256(payload)
    output = _canonical_request_path(manifest, request_sha256)
    synthetic_request = BRIDGE.SecureRecord(
        path=output,
        payload=payload,
        sha256=request_sha256,
        identity=records["manifest"].identity,
        document=document,
    )
    context = BRIDGE.BridgeContext(
        request=synthetic_request,
        manifest_path=references.manifest.path,
        approval_path=references.approval.path,
        approval_policy_path=references.approval_policy.path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan=plan,
        plan_sha256=plan["plan_sha256"],
        output_root=Path(document["output_root"]),
        prior_paths=prior_paths,
    )
    _validate_producer_paths(
        context=context,
        references=references,
        records=records,
    )
    try:
        sources = BRIDGE._validate_sources(  # noqa: SLF001
            context,
            now=_observed_time(now),
        )
    except BRIDGE.FreezeSnapshotPhaseBridgeError as exc:
        raise FreezeSnapshotRequestBuildError(
            "target bridge rejected referenced sources"
        ) from exc
    _assert_records_unchanged(records)
    return PreparedRequest(
        document=document,
        payload=payload,
        sha256=request_sha256,
        output=output,
        context=context,
        source_records=dict(sources.records),
        source_closure_sha256=sources.source_closure_sha256,
        input_records=records,
        input_closure_sha256=_input_closure(records),
    )


def confirmation_phrase(prepared: PreparedRequest) -> str:
    return (
        "build-production-shadow-freeze-snapshot-phase-request:"
        f"{prepared.context.manifest['campaign_id']}:"
        f"{prepared.context.manifest['operation_id']}:"
        f"{prepared.context.manifest['release_sha']}:"
        f"{prepared.sha256}"
    )


def _preflight_output(prepared: PreparedRequest) -> str | None:
    try:
        os.lstat(prepared.output)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FreezeSnapshotRequestBuildError(
            "request output cannot be inspected"
        ) from exc
    record = _load_reference(
        Reference(prepared.output, prepared.sha256),
        label="existing freeze/snapshot phase request",
    )
    if (
        record.payload != prepared.payload
        or record.document != prepared.document
    ):
        raise FreezeSnapshotRequestBuildError(
            "refusing to overwrite a different phase request"
        )
    return "reused"


def _publish(prepared: PreparedRequest) -> str:
    existing = _preflight_output(prepared)
    if existing is not None:
        return existing
    evidence_root = Path(
        prepared.context.manifest["deployment"][
            "controller_evidence_root"
        ]
    )
    output_root = _ensure_private_child(
        evidence_root,
        "freeze-snapshot-phase-bridge",
        label="freeze/snapshot bridge output root",
    )
    request_root = _ensure_private_child(
        output_root,
        OUTPUT_DIRECTORY,
        label="freeze/snapshot request output root",
    )
    if request_root != prepared.output.parent:
        raise FreezeSnapshotRequestBuildError(
            "request publication root differs from the plan"
        )
    try:
        write_secure_new_bytes(
            prepared.output,
            prepared.payload,
            label="freeze/snapshot phase request",
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise FreezeSnapshotRequestBuildError(
            "freeze/snapshot request publication failed closed"
        ) from exc
    record = _load_reference(
        Reference(prepared.output, prepared.sha256),
        label="published freeze/snapshot phase request",
    )
    if (
        record.payload != prepared.payload
        or record.document != prepared.document
        or stat.S_IMODE(record.identity.mode) != 0o600
        or record.identity.nlink != 1
    ):
        raise FreezeSnapshotRequestBuildError(
            "published phase request readback differs"
        )
    return "created"


def _self_validate_publication(
    prepared: PreparedRequest,
    *,
    now: datetime,
) -> None:
    try:
        context = BRIDGE._load_request(prepared.output)  # noqa: SLF001
        sources = BRIDGE._validate_sources(  # noqa: SLF001
            context,
            now=now,
        )
    except BRIDGE.FreezeSnapshotPhaseBridgeError as exc:
        raise FreezeSnapshotRequestBuildError(
            "target bridge rejected the published request"
        ) from exc
    if (
        context.request.payload != prepared.payload
        or context.request.sha256 != prepared.sha256
        or context.request.document != prepared.document
        or context.manifest_sha256 != prepared.context.manifest_sha256
        or context.plan_sha256 != prepared.context.plan_sha256
        or sources.source_closure_sha256
        != prepared.source_closure_sha256
        or set(sources.records) != set(prepared.source_records)
        or any(
            sources.records[label].path
            != prepared.source_records[label].path
            or sources.records[label].sha256
            != prepared.source_records[label].sha256
            or sources.records[label].payload
            != prepared.source_records[label].payload
            for label in sources.records
        )
    ):
        raise FreezeSnapshotRequestBuildError(
            "published request self-validation closure differs"
        )
    _assert_records_unchanged(prepared.input_records)
    try:
        BRIDGE._assert_records_unchanged(sources.records)  # noqa: SLF001
    except BRIDGE.FreezeSnapshotPhaseBridgeError as exc:
        raise FreezeSnapshotRequestBuildError(
            "published request sources changed during validation"
        ) from exc
    _preflight_output(prepared)


def _revalidate_prepared_sources(
    prepared: PreparedRequest,
    *,
    now: datetime,
) -> None:
    """Keep a stale request from being published after its source check."""

    try:
        sources = BRIDGE._validate_sources(  # noqa: SLF001
            prepared.context,
            now=now,
        )
    except BRIDGE.FreezeSnapshotPhaseBridgeError as exc:
        raise FreezeSnapshotRequestBuildError(
            "target bridge rejected sources immediately before publication"
        ) from exc
    if (
        sources.source_closure_sha256 != prepared.source_closure_sha256
        or set(sources.records) != set(prepared.source_records)
        or any(
            sources.records[label].identity
            != prepared.source_records[label].identity
            or sources.records[label].payload
            != prepared.source_records[label].payload
            for label in sources.records
        )
    ):
        raise FreezeSnapshotRequestBuildError(
            "bridge sources changed before request publication"
        )
    _assert_records_unchanged(prepared.input_records)
    try:
        BRIDGE._assert_records_unchanged(sources.records)  # noqa: SLF001
    except BRIDGE.FreezeSnapshotPhaseBridgeError as exc:
        raise FreezeSnapshotRequestBuildError(
            "bridge sources changed during publication revalidation"
        ) from exc


def build_plan(prepared: PreparedRequest) -> dict[str, Any]:
    required = confirmation_phrase(prepared)
    return {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "campaign_id": prepared.context.manifest["campaign_id"],
        "operation_id": prepared.context.manifest["operation_id"],
        "release_sha": prepared.context.manifest["release_sha"],
        "release_tree_sha": prepared.context.manifest[
            "release_tree_sha"
        ],
        "manifest_sha256": prepared.context.manifest_sha256,
        "controller_plan_sha256": prepared.context.plan_sha256,
        "request_schema": BRIDGE.REQUEST_SCHEMA,
        "request_sha256": prepared.sha256,
        "input_closure_sha256": prepared.input_closure_sha256,
        "source_closure_sha256": prepared.source_closure_sha256,
        "output": os.fspath(prepared.output),
        "required_confirmation": required,
        "reference_count": len(prepared.input_records),
        "caller_claim_values_accepted": False,
        "output_mutated": False,
        **SAFETY_RESULT,
    }


def execute(
    references: RequestReferences,
    *,
    apply: bool = False,
    confirm: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if apply and now is not None:
        raise FreezeSnapshotRequestBuildError(
            "apply does not accept a caller-supplied observation time"
        )
    prepared = prepare_request(
        references,
        now=_fresh_observation() if apply else now,
    )
    plan = build_plan(prepared)
    _preflight_output(prepared)
    if not apply:
        if confirm is not None:
            raise FreezeSnapshotRequestBuildError(
                "plan does not accept confirmation"
            )
        return plan
    if confirm != plan["required_confirmation"]:
        raise FreezeSnapshotRequestBuildError(
            "apply confirmation differs from the digest-bound plan"
        )
    _revalidate_prepared_sources(
        prepared,
        now=_fresh_observation(),
    )
    publication = _publish(prepared)
    _self_validate_publication(prepared, now=_fresh_observation())
    return {
        **plan,
        "status": (
            "published" if publication == "created"
            else "already-published"
        ),
        "publication": publication,
        "output_mutated": publication == "created",
        "target_load_request_verified": True,
        "target_validate_sources_verified": True,
        **SAFETY_RESULT,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description=__doc__)
    for name in (
        "manifest",
        "approval",
        "approval-policy",
        "frozen-snapshot-result",
        "current-frozen-verification-receipt",
        "nginx-readback-receipt",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    for phase in _prior_names():
        name = "prior-" + phase.replace("_", "-")
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    for role in BRIDGE.ROLE_ORDER:
        role_name = role.replace("_", "-")
        for kind in BRIDGE.ROLE_SOURCE_FIELDS:
            kind_name = kind.replace("_", "-")
            name = f"{role_name}-{kind_name}"
            parser.add_argument(f"--{name}", type=Path, required=True)
            parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def _references_from_args(args: argparse.Namespace) -> RequestReferences:
    def fixed(name: str, label: str) -> Reference:
        destination = name.replace("-", "_")
        return _reference(
            getattr(args, destination),
            getattr(args, destination + "_sha256"),
            label=label,
        )

    prior = {
        phase: fixed(
            "prior-" + phase.replace("_", "-"),
            f"prior phase {phase}",
        )
        for phase in _prior_names()
    }
    roles = {
        role: {
            kind: fixed(
                (
                    role.replace("_", "-")
                    + "-"
                    + kind.replace("_", "-")
                ),
                f"{role} {kind}",
            )
            for kind in BRIDGE.ROLE_SOURCE_FIELDS
        }
        for role in BRIDGE.ROLE_ORDER
    }
    return RequestReferences(
        manifest=fixed("manifest", "manifest"),
        approval=fixed("approval", "approval"),
        approval_policy=fixed(
            "approval-policy",
            "approval policy",
        ),
        prior_phase_evidence=prior,
        frozen_snapshot_result=fixed(
            "frozen-snapshot-result",
            "frozen snapshot result",
        ),
        current_frozen_verification_receipt=fixed(
            "current-frozen-verification-receipt",
            "current frozen verification receipt",
        ),
        nginx_readback_receipt=fixed(
            "nginx-readback-receipt",
            "Nginx readback receipt",
        ),
        roles=roles,
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(
            sys.argv[1:] if argv is None else argv
        )
        result = execute(
            _references_from_args(args),
            apply=args.apply,
            confirm=args.confirm,
        )
        status = 0
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        result = {
            "schema": RESULT_SCHEMA,
            "status": "blocked",
            "error": "freeze/snapshot phase request build failed closed",
            "error_class": "FreezeSnapshotRequestBuildError",
            "output_mutated": None,
            **SAFETY_RESULT,
        }
        status = 1
    sys.stdout.buffer.write(_canonical_json(result) + b"\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
